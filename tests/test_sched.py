"""grace sched: one test per lesson the shared box taught (2026-10 .. 11)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from grace.sched import policy as pol  # noqa: E402
from grace.sched import probe, state  # noqa: E402

P = pol.Policy(floor_gb=8, heavy_gb=4, disk_floor_gb=30, max_running=4, starve_secs=1800)
BIG_DISK = 200.0


def req(i, peak, prio="normal", created=0.0, owner=None):
    return pol.Request(i, owner or i, f"{i}-kind", peak, prio, created)


class Policy(unittest.TestCase):
    def test_one_heavy_job_at_a_time(self):
        # Two heavy builds at once is how the OOM killer took terminal scopes.
        d = pol.decide([req("a", 9), req("b", 8, created=1)], [], pol.Machine(30, BIG_DISK), P)
        self.assertEqual(d.start, ["a"])
        self.assertIn("one heavy job at a time", d.waits["b"])

    def test_admission_counts_the_headroom_running_jobs_still_grow_into(self):
        # 2026-11-18: three jobs granted on `free` alone; the reaper killed one.
        running = [pol.Running("r", "persist", "build", peak_gb=12, used_gb=3)]
        d = pol.decide([req("light", 3)], running, pol.Machine(19, BIG_DISK), P)
        # 19 available - (12 - 3) headroom = 10 projected; 3 + 8 floor = 11 > 10.
        self.assertEqual(d.start, [])
        self.assertIn("projected", d.waits["light"])
        d2 = pol.decide([req("light", 1.5)], running, pol.Machine(19, BIG_DISK), P)
        self.assertEqual(d2.start, ["light"])

    def test_light_work_pairs_beside_a_heavy_job_when_it_fits(self):
        running = [pol.Running("h", "edge", "check", peak_gb=7, used_gb=7)]
        d = pol.decide([req("ctx", 2)], running, pol.Machine(20, BIG_DISK), P)
        self.assertEqual(d.start, ["ctx"])

    def test_disk_below_its_floor_stops_everything(self):
        # 2026-11-18: the disk hit 75 MB free and every session's writes failed.
        d = pol.decide([req("a", 1)], [], pol.Machine(30, 12), P)
        self.assertEqual(d.start, [])
        self.assertIn("disk", d.waits["a"])

    def test_critical_path_first_then_first_come(self):
        q = [req("n1", 9, "normal", 1), req("crit", 9, "critical", 5), req("n0", 9, "normal", 0)]
        d = pol.decide(q, [], pol.Machine(30, BIG_DISK), P)
        self.assertEqual(d.start, ["crit"])
        order = [r.id for r in pol.order(q)]
        self.assertEqual(order, ["crit", "n0", "n1"])

    def test_a_promised_slot_is_kept_for_its_owner(self):
        q = [req("other", 9, created=0, owner="ratchet"), req("mine", 9, created=1, owner="verify")]
        d = pol.decide(q, [], pol.Machine(30, BIG_DISK), P, reserved_for="verify")
        self.assertEqual(d.start, ["mine"])
        self.assertIn("promised to verify", d.waits["other"])

    def test_a_starving_heavy_job_stops_backfill_behind_it(self):
        running = [pol.Running("h", "edge", "build", peak_gb=9, used_gb=9)]
        q = [req("old-heavy", 9, created=0), req("light", 1, created=10)]
        fresh = pol.decide(q, running, pol.Machine(30, BIG_DISK), P, now=60)
        self.assertEqual(fresh.start, ["light"], "light work backfills while the head is young")
        starving = pol.decide(q, running, pol.Machine(30, BIG_DISK), P, now=4000)
        self.assertEqual(starving.start, [])
        self.assertIn("nothing passes it now", starving.waits["light"])

    def test_every_waiter_is_told_why(self):
        q = [req(str(i), 9, created=i) for i in range(6)]
        d = pol.decide(q, [], pol.Machine(30, BIG_DISK), P)
        for r in q:
            if r.id not in d.start:
                self.assertTrue(d.waits.get(r.id), f"{r.id} waits with no reason")

    def test_a_measured_spike_raises_a_low_declaration(self):
        # 2026-11-19: a "4 GB" job spiked to 4.9 GB, which a 1 s poll missed.
        peak, src = pol.estimate_peak(4.0, [4.9])
        self.assertGreater(peak, 4.9)
        self.assertIn("learned", src)
        self.assertEqual(pol.estimate_peak(None, [])[1], "default (no declaration, no history)")
        self.assertEqual(pol.estimate_peak(6.0, [3.0]), (6.0, "declared"))


class State(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GRACE_SCHED_DIR"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("GRACE_SCHED_DIR", None)
        self.tmp.cleanup()

    def test_a_dead_holder_frees_its_slot(self):
        # 2026-11-19: a leaked flock held slot 0 for an hour. A lease names
        # (pid, start time); a holder that is gone is reclaimed.
        dead = subprocess.Popen(["true"])
        dead.wait()
        with state.locked() as d:
            state.put(d, "leases", {"id": "x", "owner": "o", "kind": "k", "peak_gb": 9,
                                    "pid": dead.pid, "pid_start": 1})
            state.put(d, "leases", {"id": "me", "owner": "o", "kind": "k", "peak_gb": 1,
                                    "pid": os.getpid(), "pid_start": probe.start_time(os.getpid())})
            gone = state.reap(d)
            self.assertEqual([g["id"] for g in gone], ["x"])
            self.assertEqual([v["id"] for v in state.entries(d, "leases")], ["me"])

    def test_a_recycled_pid_is_not_the_holder(self):
        with state.locked() as d:
            state.put(d, "leases", {"id": "y", "owner": "o", "kind": "k", "peak_gb": 1,
                                    "pid": os.getpid(), "pid_start": -1})
            self.assertEqual([g["id"] for g in state.reap(d)], ["y"])


class Run(unittest.TestCase):
    """End to end, without systemd, in a private state directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "GRACE_SCHED_DIR": self.tmp.name, "GRACE_SCHED_NO_SYSTEMD": "1", "GRACE_SCHED_COUNT_LEGACY": "0",
                    "GRACE_SCHED_FLOOR_GB": "0", "GRACE_SCHED_DISK_FLOOR_GB": "0",
                    "PYTHONPATH": str(ROOT)}

    def tearDown(self):
        self.tmp.cleanup()

    def sched(self, *args, **kw):
        return subprocess.run([sys.executable, "-m", "grace.sched", *args], env=self.env,
                              capture_output=True, text=True, cwd=str(ROOT), **kw)

    def test_the_job_inherits_no_scheduler_descriptor_and_done_is_automatic(self):
        probe_cmd = ("import os,sys; fds=[os.readlink(f'/proc/self/fd/{f}') for f in os.listdir('/proc/self/fd')"
                     " if os.path.exists(f'/proc/self/fd/{f}')]; print('FDS', [f for f in fds if 'grace' in f or 'mutex' in f])")
        r = self.sched("run", "--owner", "t", "--kind", "fdprobe", "--peak", "0.1", "--",
                       sys.executable, "-c", probe_cmd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FDS []", r.stdout)
        events = [json.loads(x) for x in Path(self.tmp.name, "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["requested", "started", "finished"])
        self.assertEqual(list(Path(self.tmp.name, "leases").glob("*.json")), [], "the lease is released")
        hist = Path(self.tmp.name, "history.jsonl").read_text()
        self.assertIn('"kind": "fdprobe"', hist)

    def test_the_jobs_exit_code_is_the_runs(self):
        r = self.sched("run", "--peak", "0.1", "--", sys.executable, "-c", "raise SystemExit(3)")
        self.assertEqual(r.returncode, 3)

    def test_a_timeout_kills_only_its_own_job(self):
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            t0 = time.time()
            r = self.sched("run", "--peak", "0.1", "--timeout", "1", "--",
                           sys.executable, "-c", "import time; time.sleep(30)")
            self.assertEqual(r.returncode, 124, r.stderr)
            self.assertLess(time.time() - t0, 20)
            self.assertIsNone(bystander.poll(), "a process outside the job is untouched")
        finally:
            bystander.kill()

    def test_status_shows_a_waiter_and_why(self):
        # A held heavy lease (this test process), then a heavy request waits.
        with_env = dict(os.environ)
        os.environ["GRACE_SCHED_DIR"] = self.tmp.name
        try:
            with state.locked() as d:
                state.put(d, "leases", {"id": "held", "owner": "edge", "kind": "edge check", "peak_gb": 9,
                                        "pid": os.getpid(), "pid_start": probe.start_time(os.getpid()),
                                        "started": time.time(), "priority": "normal"})
        finally:
            os.environ.clear()
            os.environ.update(with_env)
        waiter = subprocess.Popen([sys.executable, "-m", "grace.sched", "run", "--owner", "verify",
                                   "--kind", "hook", "--peak", "9", "--wait", "20", "--", "true"],
                                  env=self.env, cwd=str(ROOT), stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(4)
            s = json.loads(self.sched("status", "--json").stdout)
            self.assertEqual([r["id"] for r in s["running"]], ["held"])
            self.assertEqual(len(s["waiting"]), 1)
            self.assertIn("one heavy job at a time: edge:edge check holds it", s["waiting"][0]["why"])
        finally:
            waiter.kill()


if __name__ == "__main__":
    unittest.main()
