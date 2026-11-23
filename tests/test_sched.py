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

P = pol.Policy(floor_gb=8, heavy_gb=4, disk_floor_gb=30, max_running=4, starve_secs=1800, max_heavy=1)
P2 = pol.Policy(floor_gb=8, heavy_gb=4, disk_floor_gb=30, max_running=4, starve_secs=1800, max_heavy=2)
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
        # Codex on #3: a 2 GB estimate with a 4 GB cap may grow to 4 GB, so the
        # cap must fit in what is projected free (the worst case).
        tight = pol.Request("t", "t", "t-kind", 2.0, budget_gb=12.0)
        d = pol.decide([tight], [], pol.Machine(11, BIG_DISK), P)
        self.assertEqual(d.start, [], "2 + 8 fits, but its 12 GB cap does not")
        self.assertIn("for its cap", d.waits["t"])
        light = pol.Request("l", "l", "l-kind", 2.0, budget_gb=4.0)
        self.assertEqual(pol.decide([light], [], pol.Machine(11, BIG_DISK), P).start, ["l"])
        self.assertFalse(light.heavy(P), "heavy or light is still judged on the estimate")
        running = [pol.Running("r", "o", "k", peak_gb=2, used_gb=2, budget_gb=5)]
        self.assertEqual(pol.projected(pol.Machine(20, BIG_DISK), running), 17)

    def test_an_empty_box_admits_a_job_whose_cap_fits_and_whose_peak_leaves_the_floor(self):
        # 2026-11-19: with nothing running and 15.3 GB free, an 11 GB cap was held
        # for 11 + 8 = 19 GB and the whole queue stalled. The floor protects the
        # expected peak; the cap only has to fit.
        verify = pol.Request("v", "verify", "cargo test", 6.0, budget_gb=11.0)
        self.assertEqual(pol.decide([verify], [], pol.Machine(15.3, BIG_DISK), P).start, ["v"])
        big = pol.Request("b", "persist", "nextest", 9.0, budget_gb=13.5)
        d = pol.decide([big], [], pol.Machine(15.3, BIG_DISK), P)
        self.assertEqual(d.start, [], "9 + 8 = 17 GB is more than 15.3 free")
        self.assertIn("expected peak", d.waits["b"])

    def test_nothing_starts_beside_an_uncapped_job(self):
        # Codex on #3: a legacy scope with memory.max "max" was counted as a
        # 14 GB job, though nothing stops it growing past that.
        free = pol.Running("cargo-guard-1.scope", "legacy", "k", peak_gb=3, used_gb=3, uncapped=True)
        d = pol.decide([req("a", 0.5)], [free], pol.Machine(28, BIG_DISK), P)
        self.assertEqual(d.start, [])
        self.assertIn("no memory cap", d.waits["a"])

    def test_a_waiter_says_why_once_per_reason_not_per_poll(self):
        # 2026-11-19: the projected-GB figure changed every 3 s poll, and each
        # change printed another "waits:" line (over 100 for one cargo fmt).
        from grace.sched.runner import reason_gist
        a = "memory: needs 9.0 GB plus the 8 GB floor, 14.2 GB projected free once running jobs reach their peaks"
        b = "memory: needs 9.0 GB plus the 8 GB floor, 13.7 GB projected free once running jobs reach their peaks"
        self.assertEqual(reason_gist(a), reason_gist(b))
        self.assertNotEqual(reason_gist(a), reason_gist("one heavy job at a time: edge:check holds it"))
        # Codex on #5: digits in a name are not a live figure.
        self.assertNotEqual(reason_gist("the heavy slot is promised to team2"),
                            reason_gist("the heavy slot is promised to team3"))
        self.assertNotEqual(reason_gist("one heavy job at a time: p:persist v61 build holds it"),
                            reason_gist("one heavy job at a time: p:persist v62 build holds it"))
        self.assertEqual(reason_gist("behind e:k, which has waited 31 min; nothing passes it now"),
                         reason_gist("behind e:k, which has waited 32 min; nothing passes it now"))

    def test_a_job_too_big_for_any_cap_is_refused_on_measured_need_only(self):
        # Codex on #3: a job whose estimate is above any cap would be killed at it.
        from grace.sched import runner
        self.assertIn("measured need", runner.too_big_for_box(None, 18.5, [16.1], None))
        # 2026-11-20: persist's lib nextest needs 12.3 GB; its estimate (14.1 with
        # the 15% margin) is above the 14 GB cap, but the measured need is not.
        self.assertIsNone(runner.too_big_for_box(9.0, 14.1, [12.3], None))
        self.assertIn("declared peak", runner.too_big_for_box(20.0, 20.0, [], None))
        self.assertIsNone(runner.too_big_for_box(None, 16.1, [14.0], 16.0), "an explicit --cap is the caller's")
        # Old records carry only memory.peak (page cache included): they never refuse
        # a build that reached the cap through cache and finished cleanly.
        self.assertIsNone(runner.too_big_for_box(None, 16.1, [], None))
        self.assertIsNone(runner.too_big_for_box(None, 9.0, [8.0], None))

    def test_two_heavy_jobs_run_at_once_when_both_caps_fit(self):
        # Eric 2026-11-20: persist's v62 push needs more than one heavy build.
        # Each job is hard-capped in its own scope and admission reserves the
        # caps, so the second starts only when its cap fits beside the first.
        test = pol.Request("t", "persist", "nextest", 12.3, budget_gb=14.0)
        clippy = pol.Request("c", "persist", "clippy", 6.0, budget_gb=9.0, created=1)
        d = pol.decide([test, clippy], [], pol.Machine(30, BIG_DISK), P2)
        self.assertEqual(d.start, ["t", "c"], "after the test's 14 GB cap, 16 GB remain: clippy's 6 + 8 floor fits")
        tight = pol.decide([test, clippy], [], pol.Machine(22, BIG_DISK), P2)
        self.assertEqual(tight.start, ["t"], "the second cap does not fit: memory, not a count, says no")
        self.assertIn("memory:", tight.waits["c"])
        third = pol.Request("x", "edge", "check", 5.0, budget_gb=7.0, created=2)
        running = [pol.Running("t", "persist", "nextest", 12.3, 3, budget_gb=14.0),
                   pol.Running("c", "persist", "clippy", 6.0, 2, budget_gb=9.0)]
        full = pol.decide([third], running, pol.Machine(60, BIG_DISK), P2)
        self.assertEqual(full.start, [])
        self.assertIn("2 heavy jobs at a time", full.waits["x"])
        self.assertEqual(pol.Policy().max_heavy, 2, "two by default")

    def test_a_lone_job_on_an_idle_box_starts_when_its_cap_fits(self):
        # 2026-11-20: persist's nextest (14.4 GB) waited forever on an empty box
        # with 22.0 GB free because 14.4 + the 8 GB floor = 22.4.
        lone = pol.Request("n", "persist", "nextest", 14.4, budget_gb=14.4)
        self.assertEqual(pol.decide([lone], [], pol.Machine(22.0, BIG_DISK), P2).start, ["n"])
        self.assertEqual(pol.decide([lone], [], pol.Machine(18.0, BIG_DISK), P2).start, [],
                         "its cap plus half the floor (4 GB for the sessions) must still fit")
        heavy = [pol.Running("r", "edge", "check", 6.0, 1.0, budget_gb=9.0)]
        self.assertEqual(pol.decide([lone], heavy, pol.Machine(30.0, BIG_DISK), P2).start, [],
                         "beside a running HEAVY job the full floor still applies")

    def test_a_light_job_beside_does_not_end_idle(self):
        # 2026-11-21: a 2 GB client localize task (using 0.02 GB) held persist's
        # critical clippy: 13.2 + the 8 GB floor = 21.2 GB against 21.0 free.
        # The light job's memory is already reserved; only a heavy job ends idle.
        crit = pol.Request("p", "persist", "clippy", 13.2, "critical", budget_gb=14.0)
        light = [pol.Running("l", "CIRISClient", "localize", 0.02, 1.0, budget_gb=2.0)]
        d = pol.decide([crit], light, pol.Machine(21.0, BIG_DISK), P2)
        self.assertEqual(d.start, ["p"], d.waits)
        self.assertEqual(pol.decide([crit], light, pol.Machine(17.0, BIG_DISK), P2).start, [],
                         "its cap plus half the floor must still fit beside the light job")

    def test_a_small_job_waits_on_half_the_floor_beside_a_heavy_one(self):
        # 2026-11-22: persist's sqlite lane (declared 3 GB, measured 0.1-0.24)
        # waited behind an edge test that reserved 9.2 GB and used 1.9: 6.0 GB
        # projected free against 3.0 + the 8 GB floor.
        edge = [pol.Running("e", "edge", "test", 9.2, 1.9, budget_gb=9.2)]
        box = pol.Machine(13.3, BIG_DISK)  # 13.3 - 7.3 headroom = 6.0 projected
        small = pol.Request("s", "persist", "sqlite-lib", 0.5, budget_gb=4.5, small=True)
        self.assertEqual(pol.decide([small], edge, box, P2).start, ["s"])
        unsmall = pol.Request("u", "persist", "sqlite-lib", 0.5, budget_gb=4.5)
        self.assertEqual(pol.decide([unsmall], edge, box, P2).start, [],
                         "the same job without its measured history keeps the full floor")
        tight = pol.Machine(11.0, BIG_DISK)  # 3.7 projected: its 4.5 GB cap no longer fits
        self.assertEqual(pol.decide([small], edge, tight, P2).start, [],
                         "a small job's cap must still fit")

    def test_a_job_past_its_peak_gives_back_its_headroom(self):
        # 2026-11-22: edge's 45.1.10 lib test held its 9.2 GB reservation at
        # 2.5 GB used for 17 min after linking; four jobs waited behind it.
        p = pol.Policy()
        self.assertIsNone(pol.reclaim(9.2, 2.5, 2.5, 0.0, 999.0, p), "never reached its peak")
        self.assertIsNone(pol.reclaim(9.2, 6.0, 8.5, 0.0, 999.0, p), "not settled under half its max")
        self.assertIsNone(pol.reclaim(9.2, 2.5, 8.5, 900.0, 999.0, p), "settled for less than settle_secs")
        self.assertEqual(pol.reclaim(9.2, 2.5, 8.5, 0.0, 999.0, p), 4.0, "used + 1.5 GB")
        self.assertEqual(pol.reclaim(9.2, 3.0, 8.0, 0.0, 999.0, p), 4.5)
        self.assertEqual(pol.reclaim(2.5, 1.2, 2.4, 0.0, 999.0, p), 2.5, "never above the estimate")
        # Its smaller reservation is what admission sees, and it no longer holds a heavy slot.
        edge = pol.Running("e", "edge", "test", 9.2, 2.5, budget_gb=9.2, reclaimed_gb=4.0)
        self.assertAlmostEqual(edge.headroom_gb(), 1.5)
        self.assertFalse(edge.heavy(p))
        waiting = pol.Request("v", "verify", "commit", 2.0, budget_gb=3.5)
        held = pol.Running("e", "edge", "test", 9.2, 2.5, budget_gb=9.2)
        box = pol.Machine(15.0, BIG_DISK)
        self.assertEqual(pol.decide([waiting], [held], box, P2).start, [], "before: 15 - 6.7 = 8.3 < 10")
        self.assertEqual(pol.decide([waiting], [edge], box, P2).start, ["v"], "after: 15 - 1.5 = 13.5")

    def test_a_small_job_backfills_past_a_starving_head(self):
        head = pol.Request("h", "persist", "seam-check", 8.0, budget_gb=9.5, created=0)
        small = pol.Request("s", "verify", "cargo test", 0.5, budget_gb=2.0, created=10, small=True)
        big = pol.Request("b", "edge", "clippy", 5.0, budget_gb=7.0, created=20)
        box = pol.Machine(10.0, BIG_DISK)
        d = pol.decide([head, small, big], [], box, P2, now=P2.starve_secs + 100)
        self.assertNotIn("h", d.start, "the head still cannot fit")
        self.assertIn("s", d.start, d.waits)
        self.assertIn("nothing passes it now", d.waits["b"])

    def test_memory_pressure_holds_every_start(self):
        light = pol.Request("l", "CIRISClient", "gates", 1.0, budget_gb=2.0)
        calm = pol.Machine(28, BIG_DISK, psi_some_avg10=2.0)
        self.assertEqual(pol.decide([light], [], calm, P2).start, ["l"])
        stalled = pol.Machine(28, BIG_DISK, psi_some_avg10=25.0)
        d = pol.decide([light], [], stalled, P2)
        self.assertEqual(d.start, [])
        self.assertIn("memory pressure", d.waits["l"])

    def test_is_small_needs_three_measured_runs_all_small(self):
        self.assertFalse(pol.is_small([0.1, 0.2]), "two runs are not a measurement")
        self.assertTrue(pol.is_small([0.1, 0.2, 0.24]))
        self.assertFalse(pol.is_small([0.1, 0.2, 1.7]), "one big recent run ends it")
        self.assertTrue(pol.is_small([5.0, 0.1, 0.1, 0.1, 0.1, 0.1]), "only the last five count")

    def test_a_declaration_is_learned_below_after_three_runs(self):
        self.assertEqual(pol.estimate_peak(3.0, [0.1, 0.2]), (3.0, "declared"),
                         "two runs do not move a declaration")
        peak, source = pol.estimate_peak(3.0, [0.11, 0.17, 0.24])
        self.assertEqual(peak, 0.5, "never below LEARNED_MIN_GB")
        self.assertIn("below the declared 3", source)
        peak, _ = pol.estimate_peak(8.0, [4.0, 5.0, 6.0])
        self.assertEqual(peak, 6.9, "learned at the measured peak x1.15")
        self.assertEqual(pol.estimate_peak(2.0, [1.9, 1.9, 1.9]),
                         (2.2, "learned (3 runs, above the declared 2)"))

    def test_an_exclusive_reservation_holds_back_every_other_owner(self):
        # 2026-11-21: persist's certify needs the box free of foreign cargo; a
        # heavy-slot reservation still let light jobs backfill, so it waited forever.
        light = pol.Request("l", "CIRISClient", "gates", 1.0, budget_gb=2.0)
        heavy = pol.Request("h", "edge", "test", 8.0, budget_gb=10.0)
        own = pol.Request("p", "persist", "certify", 8.0, "critical", budget_gb=10.0)
        d = pol.decide([light, heavy, own], [], pol.Machine(28, BIG_DISK), P2,
                       reserved_for="persist", reserved_exclusive=True)
        self.assertEqual(d.start, ["p"])
        self.assertIn("reserved for persist (exclusive)", d.waits["l"])
        shared = pol.decide([light], [], pol.Machine(28, BIG_DISK), P2, reserved_for="persist")
        self.assertEqual(shared.start, ["l"], "a plain reservation still lets light work backfill")

    def test_a_waiting_critical_job_holds_back_other_heavy_starts(self):
        # Eric 2026-11-20: persist's critical lane waited ten minutes while an
        # edge test lane held the memory. Once a critical heavy job waits,
        # nothing heavy of a lower class starts ahead of it; light work may.
        running = [pol.Running("e", "edge", "lib tests", 9.1, 1.3, budget_gb=13.6)]
        crit = pol.Request("p", "persist", "nextest", 12.5, "critical", 0, budget_gb=14.0)
        normal = pol.Request("n", "edge", "check", 5.0, "normal", 1, budget_gb=7.0)
        light = pol.Request("l", "verify", "fmt", 0.5, "normal", 2, budget_gb=2.5)
        d = pol.decide([crit, normal, light], running, pol.Machine(26, BIG_DISK), P2)
        self.assertNotIn("p", d.start, "memory is short: it waits")
        self.assertIn("behind the critical persist:nextest", d.waits["n"])
        self.assertIn("l", d.start, "light work still backfills")

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

    def test_a_torn_history_record_hides_no_later_run(self):
        # Codex on #3: an interrupted append left a truncated line, and every
        # measurement after it was ignored by admission.
        d = Path(self.tmp.name)
        state.record(d, kind="k", peak_gb=2.0)
        with open(d / "history.jsonl", "a") as f:
            f.write('{"kind": "k", "peak_g\n')
        state.record(d, kind="k", peak_gb=9.0)
        self.assertEqual(state.history(d, "k"), [2.0, 9.0])

    def test_a_legacy_scope_without_a_limit_is_uncapped(self):
        # Codex on #3: memory.max "max" is no limit (cgroup v2), not 14 GB.
        base = Path(self.tmp.name)
        for name, mx in (("cargo-guard-1.scope", "max"), ("cargo-guard-2.scope", str(4 * probe.GIB))):
            (base / name).mkdir()
            (base / name / "memory.max").write_text(mx + "\n")
            (base / name / "memory.current").write_text(str(probe.GIB) + "\n")
        got = {g["unit"]: g for g in probe.legacy_guard_scopes(base)}
        self.assertIsNone(got["cargo-guard-1.scope"]["cap_gb"])
        self.assertTrue(got["cargo-guard-1.scope"]["uncapped"])
        self.assertEqual(got["cargo-guard-2.scope"]["cap_gb"], 4.0)

    def test_history_learns_need_and_old_records_count_only_when_asked(self):
        with state.locked() as d:
            state.record(d, kind="k", peak_gb=16.0)              # before need was measured
            state.record(d, kind="k", peak_gb=14.0, need_gb=5.5)  # memory.peak counted cache
            self.assertEqual(state.history(d, "k"), [16.0, 5.5])
            self.assertEqual(state.history(d, "k", need_only=True), [5.5])

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
                    "GRACE_SCHED_MAX_HEAVY": "1",
                    "GRACE_SCHED_ALLOW_UNCAPPED": "1",
                    "GRACE_SCHED_FLOOR_GB": "0", "GRACE_SCHED_DISK_FLOOR_GB": "0",
                    # a heavy test job must never take the box's real legacy slot locks
                    "GRACE_SCHED_LEGACY_LOCK": str(Path(self.tmp.name, "no-legacy.lock")),
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

    def test_a_measured_10_gb_build_starts_on_a_15_5_gb_idle_box(self):
        # 2026-11-21: persist's sqlite lib-test build (need ~9.9 GB, estimate 10.5)
        # was capped at 14 GB, so an idle box needed 14 + 4 = 18 GB and it never ran.
        from grace.sched import runner
        cap = runner.default_cap(10.5, "declared")
        self.assertEqual(cap, 12.1)
        req = pol.Request("p", "persist", "nextest", 10.5, "critical", budget_gb=cap)
        self.assertEqual(pol.decide([req], [], pol.Machine(15.5, BIG_DISK), P2).start, ["p"])

    def test_an_unknown_job_keeps_the_legacy_guards_cap(self):
        from grace.sched import runner
        self.assertEqual(runner.default_cap(6.0, "default (no declaration, no history)"), 11.0)
        self.assertEqual(runner.default_cap(2.0, "declared"), 3.5)
        # A peak learned BELOW its declaration lowers admission, never the cap.
        peak, source, cap = runner.plan(3.0, [0.11, 0.17, 0.24])
        self.assertEqual((peak, cap), (0.5, runner.default_cap(3.0, "declared")), source)
        self.assertEqual(runner.default_cap(12.0, "learned (3 runs)", runs=3), 13.8)

    def test_one_quiet_run_does_not_shrink_an_undeclared_cap(self):
        # 2026-11-19: edge's first `git push` ran no hook build (0.02 GB); the
        # learned cap (2 GB) then killed the next push, whose hook ran clippy.
        from grace.sched import runner
        peak, source = pol.estimate_peak(None, [0.02])
        self.assertEqual(runner.default_cap(peak, source, runs=1), 11.0)
        self.assertEqual(runner.default_cap(peak, source, runs=2), 11.0)
        peak3, source3 = pol.estimate_peak(None, [0.02, 5.9, 5.8])
        self.assertEqual(runner.default_cap(peak3, source3, runs=3), 8.3)
        self.assertEqual(runner.default_cap(2.0, "declared", runs=0), 3.5, "a declaration is believed at once")
        # The warm-up cap is a floor: a warming kind that already ran past it
        # keeps the calculated cap (persist's nextest, 14.1 GB, killed at 11).
        self.assertEqual(runner.default_cap(14.1, "learned (2 runs)", runs=2, declared=False), 14.0)

    def _seed(self, kind, *peaks):
        for pk in peaks:
            state.record(Path(self.tmp.name), kind=kind, peak_gb=pk, need_gb=pk)

    def test_a_declared_job_above_its_declaration_keeps_the_calculated_cap(self):
        # Codex on #6: a declared 8 GB job with a 9 GB run behind it got the
        # 11 GB warm-up cap meant for undeclared jobs, not its 14 GB. Here at a
        # smaller scale: declared 0.8, one 0.9 run, so its cap is 1.0 + 2.
        self._seed("decl", 0.9)
        r = self.sched("run", "--kind", "decl", "--peak", "0.8", "--wait", "60", "--", "true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._last_history()["cap_gb"], 2.5, r.stderr)

    def test_a_warming_kind_stays_in_the_heavy_slot(self):
        # Codex on #6: one quiet run (0.02 GB) rounded the estimate to 0 GB, so
        # the kind's next run, which may build, was admitted as light work.
        self._seed("quiet", 0.02)
        r = self.sched("run", "--kind", "quiet", "--cap", "0.5", "--wait", "60", "--", "true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreater(self._last_history()["peak_estimate_gb"], pol.Policy().heavy_gb, r.stderr)
        self._seed("quiet", 0.02, 0.02)
        r = self.sched("run", "--kind", "quiet", "--cap", "0.5", "--wait", "60", "--", "true")
        self.assertLess(self._last_history()["peak_estimate_gb"], 1.0, "after LEARN_RUNS runs it is learned")

    def test_a_heavy_job_holds_the_legacy_guards_free_slots(self):
        import fcntl
        lock = Path(self.tmp.name, "legacy.lock")
        lock.touch()
        Path(f"{lock}.1").touch()
        probe_cmd = ("import fcntl,os,sys\nfor p in sys.argv[1:]:\n fd=os.open(p,os.O_RDWR)\n try:\n"
                     "  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); print('FREE',p)\n except OSError: print('HELD',p)")
        env = {**self.env, "GRACE_SCHED_LEGACY_LOCK": str(lock), "GRACE_SCHED_HEAVY_GB": "0.01"}
        r = subprocess.run([sys.executable, "-m", "grace.sched", "run", "--peak", "0.05", "--",
                            sys.executable, "-c", probe_cmd, str(lock), f"{lock}.1"],
                           env=env, capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("HELD"), 2, r.stdout)
        fd = os.open(lock, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released once the job is done
        os.close(fd)

    def test_the_shim_names_the_real_command_not_its_nice_prefix(self):
        # Edge 2026-11-19: `cargo_guard.sh nice -n 19 ionice -c3 cargo ...` keyed
        # every session's history as "nice -n 19", so nothing was learned.
        r = subprocess.run([str(ROOT / "bin" / "cargo_guard.sh"), "nice", "-n", "19", "ionice", "-c3",
                            "env", "A=b", "true", "check", "--lib"],
                           env={**self.env, "GRACE_SCHED_PEAK_GB": "0.1"}, capture_output=True, text=True,
                           cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        kind = self._last_history()["kind"]
        self.assertTrue(kind.endswith(": true check --lib"), kind)

    def test_the_shim_skips_a_wrapper_options_separate_value(self):
        # Codex on #4: `timeout -s TERM 300 cargo ...` took TERM for the command,
        # so the kind carried the duration and learned nothing across runs.
        for wrapped in (["timeout", "-s", "TERM", "300"], ["timeout", "--kill-after", "5", "60"],
                        ["env", "-u", "FOO", "nice", "-n", "5"], ["stdbuf", "-o", "L"],
                        ["taskset", "-c", "0-3"]):
            r = subprocess.run([str(ROOT / "bin" / "cargo_guard.sh"), *wrapped, "true", "check", "--lib"],
                               env={**self.env, "GRACE_SCHED_PEAK_GB": "0.1"}, capture_output=True, text=True,
                               cwd=self.tmp.name)
            self.assertEqual(r.returncode, 0, r.stderr)
            kind = self._last_history()["kind"]
            self.assertTrue(kind.endswith(": true check --lib"), (wrapped, kind))

    def test_a_declared_peak_above_any_cap_is_refused_before_it_runs(self):
        r = self.sched("run", "--peak", "20", "--", sys.executable, "-c", "print('RAN')")
        self.assertEqual(r.returncode, 79, r.stderr)
        self.assertNotIn("RAN", r.stdout)
        self.assertIn("declared peak 20 GB", r.stderr)

    @unittest.skipUnless(probe.systemd_scopes_usable(), "needs systemd user scopes")
    def test_page_cache_is_not_learned_as_need(self):
        # Codex on #3: memory.peak counts page cache the kernel reclaims at the
        # cap; learning from it ratcheted a working build's estimate past the cap.
        env = {k: v for k, v in self.env.items() if k not in ("GRACE_SCHED_NO_SYSTEMD", "GRACE_SCHED_ALLOW_UNCAPPED")}
        target = Path(self.tmp.name, "cache.bin")
        write = (f"f=open({str(target)!r},'wb')\n"
                 "for _ in range(48): f.write(b'x'*(8*1024*1024))\n"
                 "f.flush(); f.close()")
        r = subprocess.run([sys.executable, "-m", "grace.sched", "run", "--kind", "cachewriter", "--peak", "1",
                            "--", sys.executable, "-c", write],
                           env=env, capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = json.loads(Path(self.tmp.name, "history.jsonl").read_text().splitlines()[-1])
        self.assertGreater(rec["peak_gb"], 0.2, "memory.peak saw the written pages")
        self.assertLess(rec["need_gb"], 0.2, "need is the anonymous memory only")

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
