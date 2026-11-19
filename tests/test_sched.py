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

    def test_admission_reserves_the_cap_not_the_estimate(self):
        # Codex on #3: a 2 GB estimate with a 4 GB cap may grow to 4 GB.
        light = pol.Request("l", "l", "l-kind", 2.0, budget_gb=4.0)
        d = pol.decide([light], [], pol.Machine(11, BIG_DISK), P)
        self.assertEqual(d.start, [], "2 + 8 fits, but its 4 GB cap + 8 does not")
        self.assertIn("its cap", d.waits["l"])
        self.assertEqual(pol.decide([light], [], pol.Machine(12, BIG_DISK), P).start, ["l"])
        self.assertFalse(light.heavy(P), "heavy or light is still judged on the estimate")
        running = [pol.Running("r", "o", "k", peak_gb=2, used_gb=2, budget_gb=5)]
        self.assertEqual(pol.projected(pol.Machine(20, BIG_DISK), running), 17)

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

    def test_a_lease_outlives_its_dead_supervisor_while_the_job_runs(self):
        # Codex on #3: the job runs in its own session and can survive its
        # supervisor; reclaiming its lease would hide it from admission.
        dead = subprocess.Popen(["true"])
        dead.wait()
        job = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        try:
            with state.locked() as d:
                state.put(d, "leases", {"id": "o", "owner": "o", "kind": "k", "peak_gb": 2,
                                        "pid": dead.pid, "pid_start": 1, "pgid": job.pid})
                self.assertEqual(state.reap(d), [])
                kept = state.entries(d, "leases")
                self.assertEqual([v["id"] for v in kept], ["o"])
                self.assertTrue(kept[0]["orphaned"])
            job.kill()
            job.wait()
            with state.locked() as d:
                self.assertEqual([g["id"] for g in state.reap(d)], ["o"], "reclaimed once the job is gone")
        finally:
            job.kill()

    def test_a_state_directory_that_is_not_ours_is_refused(self):
        # Codex on #3: another account could pre-create the /tmp directory.
        link = Path(self.tmp.name, "link")
        target = Path(self.tmp.name, "target")
        target.mkdir()
        link.symlink_to(target)
        os.environ["GRACE_SCHED_DIR"] = str(link)
        with self.assertRaises(state.UnsafeStateDir):
            state.root()
        os.environ["GRACE_SCHED_DIR"] = str(target)
        from unittest import mock
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(state.UnsafeStateDir):
                state.root()
        target.chmod(0o777)
        state.root()
        self.assertEqual(target.stat().st_mode & 0o077, 0, "an open directory of ours is closed")

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
                    "GRACE_SCHED_ALLOW_UNCAPPED": "1",
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

    def test_no_hard_cap_no_run(self):
        # Codex on #3: an uncapped job can outgrow what admission reserved.
        env = {k: v for k, v in self.env.items() if k != "GRACE_SCHED_ALLOW_UNCAPPED"}
        r = subprocess.run([sys.executable, "-m", "grace.sched", "run", "--peak", "0.1", "--",
                            sys.executable, "-c", "print('RAN')"], env=env, capture_output=True, text=True,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 78, r.stderr)
        self.assertNotIn("RAN", r.stdout)
        self.assertIn("no hard memory cap", r.stderr)

    def _last_history(self):
        return json.loads(Path(self.tmp.name, "history.jsonl").read_text().splitlines()[-1])

    def test_an_outside_sigkill_is_not_a_cap_kill(self):
        # Codex on #3: exit 137 alone fabricated a cap-sized peak.
        r = self.sched("run", "--kind", "selfkill", "--peak", "0.1", "--cap", "3", "--",
                       sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual(r.returncode, 137, r.stderr)
        h = self._last_history()
        self.assertFalse(h["oom_killed"])
        self.assertLess(h["peak_gb"], 3.0, "the cap is not recorded as its peak")
        self.assertIn("not a cap kill", r.stderr)

    def test_the_polled_cap_kills_an_uncapped_job_that_outgrows_it(self):
        grow = ("import time; b=bytearray(400*1024*1024)\nfor i in range(0,len(b),4096): b[i]=1\n"
                "time.sleep(20)")
        r = self.sched("run", "--kind", "grow", "--peak", "0.05", "--cap", "0.2", "--",
                       sys.executable, "-c", grow, timeout=60)
        self.assertEqual(r.returncode, 137, r.stderr)
        h = self._last_history()
        self.assertTrue(h["oom_killed"])
        self.assertGreaterEqual(h["peak_gb"], 0.2)

    @unittest.skipUnless(probe.systemd_scopes_usable(), "needs systemd user scopes")
    def test_the_kernel_confirms_a_cap_kill_in_a_scope(self):
        env = {k: v for k, v in self.env.items() if k not in ("GRACE_SCHED_NO_SYSTEMD", "GRACE_SCHED_ALLOW_UNCAPPED")}
        grow = "b=bytearray(600*1024*1024)\nfor i in range(0,len(b),4096): b[i]=1\nprint('should not get here')"
        r = subprocess.run([sys.executable, "-m", "grace.sched", "run", "--kind", "scopegrow", "--peak", "0.05",
                            "--cap", "0.3", "--", sys.executable, "-c", grow],
                           env=env, capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        h = self._last_history()
        self.assertTrue(h["oom_killed"], r.stderr)
        unit = "grace-sched-" + json.loads(Path(self.tmp.name, "events.jsonl").read_text().splitlines()[0])["id"]
        self.assertEqual(probe.unit_result(unit + ".scope"), "success", "its own failed unit is reset")

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
