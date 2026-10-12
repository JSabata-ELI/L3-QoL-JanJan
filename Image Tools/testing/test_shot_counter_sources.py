"""Shot counter: one shot per event, from any loaded camera or energy channel.

What is pinned here:

  1. A frame that never reached the archive on the main camera is still a shot when
     another camera on the same shot rate saw it — or an energy reading did.
  2. Cameras write their files with their own constant delay (0.16 s and 0.5 s
     measured); two cameras seeing the same shots never count it twice, at 5 s and at
     3.3 Hz.
  3. A late frame (older than one already counted) still matches its shot.
  4. A free-running 10 Hz camera never adds shots.
  5. Changing the main camera, or live mode starting again, keeps the number.
  6. Silence longer than X seconds: the count drops, the old number stays shown, and
     the next shot starts at 1. A late tail of the old run does not start a new one.

Runs headless, no share and no archiver:

    python testing/test_shot_counter_sources.py
"""
import sys

import bench_common  # noqa: F401  (offscreen Qt before anything else)

sys.path.insert(0, str(bench_common.HERE))
import is_t

S = 1_000_000_000
MS = 1_000_000


def main() -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    def run(gap_ns, n, lag_a, lag_b, drop_a=(), drop_b=(), start=1000 * S):
        """Two cameras, A the main one, n shots `gap_ns` apart. Each camera sends
        its frame with its own delay; frames in drop_* never arrive."""
        c = is_t.ShotCounter(reset_s=10)
        c.set_main("cam:A")
        c.prime("cam:A", [start - k * gap_ns + lag_a for k in range(1, 4)])
        c.prime("cam:B", [start - k * gap_ns + lag_b for k in range(1, 4)])
        now = 0.0
        for k in range(n):
            t = start + k * gap_ns
            now += gap_ns / S
            if k not in drop_a:
                c.add("cam:A", [t + lag_a], now)
            if k not in drop_b:
                c.add("cam:B", [t + lag_b], now + 0.01)
        return c

    print("Two cameras on the same shots")
    for gap, tag in ((5 * S, "5 s"), (300 * MS, "3.3 Hz")):
        c = run(gap, 40, 160 * MS, 500 * MS)
        check(c.shown == 40, f"{tag}: 40 shots seen by both = 40 ({c.shown})")
        c = run(gap, 40, 160 * MS, 500 * MS, drop_a={10, 25, 26})
        check(c.shown == 40,
              f"{tag}: 3 frames missing on the main camera, the other saw them = 40 "
              f"({c.shown})")
        c = run(gap, 40, 160 * MS, 500 * MS, drop_a={20}, drop_b={30})
        check(c.shown == 40, f"{tag}: one missing on each camera = 40 ({c.shown})")

    print("Energy fills a missing frame")
    c = is_t.ShotCounter(reset_s=10)
    c.set_main("cam:A")
    c.prime("cam:A", [995 * S + 300 * MS])
    c.add_energy("PTM1", [990 * S, 995 * S], 0.0)      # first look = history
    for k in range(20):
        t = 1000 * S + k * 5 * S
        if k != 12:
            c.add("cam:A", [t + 300 * MS], float(k * 5))
        c.add_energy("PTM1", [t], float(k * 5) + 1.0)
    check(c.shown == 20, f"frame 12 missing, energy saw it = 20 ({c.shown})")

    print("Energy alone (no camera)")
    e = is_t.ShotCounter(reset_s=10)
    e.add_energy("PTM1", [10 * S], now=0.0)
    for k, t in enumerate((11, 12, 13), start=1):
        e.add_energy("PTM1", [t * S], now=float(k))
    check(e.shown == 3, f"energy readings alone count shots ({e.shown})")
    e.add_energy("SBW4", [12 * S, 13 * S], now=3.1)    # first look = history
    e.add_energy("SBW4", [14 * S], now=4.0)
    e.add_energy("PTM1", [14 * S + 2 * MS], now=4.1)
    check(e.shown == 4, f"two channels reading one shot = one shot ({e.shown})")

    print("Late frame, repeated frame")
    c = is_t.ShotCounter(reset_s=10)
    c.set_main("cam:A")
    c.prime("cam:A", [90 * S, 100 * S])
    check(not c.add("cam:A", [90 * S, 100 * S], 0.0),
          "frames known when live began are not shots")
    c.add("cam:A", [105 * S, 115 * S], 1.0)          # the poll found 2, missed 110
    c.add("cam:A", [115 * S], 1.1)                   # the watcher repeats one
    c.add("cam:A", [110 * S], 1.2)                   # the missed one arrives late
    check(c.shown == 3, f"late frame still counted, repeat not = 3 ({c.shown})")

    print("Free-running camera")
    c = is_t.ShotCounter(reset_s=10)
    c.set_main("cam:A")
    c.prime("cam:A", [1000 * S - k * 5 * S for k in range(1, 4)])
    c.prime("cam:F", [1000 * S - k * 100 * MS for k in range(1, 4)])
    now = 0.0
    for k in range(10):
        t = 1000 * S + k * 5 * S
        now += 5
        c.add("cam:A", [t + 200 * MS], now)
        c.add("cam:F", [t + j * 100 * MS for j in range(50)], now)
    check(c.shown == 10, f"10 Hz camera beside a 5 s one adds nothing = 10 ({c.shown})")

    print("Main camera changes")
    c = run(5 * S, 10, 160 * MS, 500 * MS)
    before = c.shown
    c.set_main("cam:B")
    check(c.shown == before, f"switching main keeps the number ({c.shown})")
    c.prime("cam:B", [])                              # live mode starting again
    t = 1000 * S + 10 * 5 * S
    c.add("cam:B", [t + 500 * MS], 60.0)
    c.add("cam:A", [t + 160 * MS], 60.1)
    check(c.shown == before + 1, f"the next shot adds one, not two ({c.shown})")
    c.set_main("cam:C")                               # a camera never seen before
    c.prime("cam:C", [t + 300 * MS])                  # its frame of the last shot
    t += 5 * S
    c.add("cam:C", [t + 300 * MS], 65.0)
    c.add("cam:A", [t + 160 * MS], 65.1)
    check(c.shown == before + 2, f"new main camera continues the count ({c.shown})")

    print("Silence")
    c = is_t.ShotCounter(reset_s=10)
    c.set_main("cam:A")
    for k in range(4):
        c.add("cam:A", [(100 + 5 * k) * S], float(k))
    c.tick(20.0)
    check(c.count == 0 and c.shown == 4, "after X s: count dropped, old number shown")
    check(not c.add_energy("PTM1", [100 * S], 20.5), "energy first look = history")
    check(not c.add_energy("PTM1", [114 * S], 20.6),
          "late reading of the ended run does not start a new one")
    check(c.shown == 4, "and the shown number is untouched")
    c.add("cam:A", [200 * S], 30.0)
    check(c.shown == 1, f"the next shot starts again from 1 ({c.shown})")
    c.add("cam:A", [205 * S], 35.0)
    check(c.shown == 2, f"gaps below X keep counting ({c.shown})")
    c.reset_s = 3
    c.add("cam:A", [210 * S], 39.0)
    check(c.shown == 1, f"with X = 3 s a 4 s gap starts again ({c.shown})")

    def judged(plan, cams, polls_s=1.5, stop_after=None):
        """Energies answering (the normal case). `plan` = list of shot times; each
        shot reaches PTM1 and SBW4 unless listed in their `drop`; `cams` = {name:
        (lag_ns, drop_set, free_gap_ns or None)}. A free camera stores a frame every
        free_gap_ns from the first shot to `stop_after`, laser or not. Returns the
        number counted (one run)."""
        c = is_t.ShotCounter(reset_s=1e9)
        t0 = plan[0]
        end = (stop_after or plan[-1]) + 30 * S
        ev = []
        for name, (lag, drop, free) in cams.items():
            if free:
                hist = [t0 - k * free + lag for k in range(1, 4)]
                t = t0 + lag
                while t <= end:
                    ev.append((t + 350 * MS, name, t))
                    t += free
            else:
                hist = [t0 - k * (plan[1] - plan[0]) + lag for k in range(1, 4)]
                for k, t in enumerate(plan):
                    if k not in drop:
                        ev.append((t + lag + 350 * MS, name, t + lag))
            c.prime(name, hist)
        energy = {lab: [t for k, t in enumerate(plan) if k not in drop]
                  for lab, drop in (("PTM1", e_drop), ("SBW4", e_drop2))}
        gap0 = plan[1] - plan[0]
        for lab in energy:
            c.energy_answered(0.0)
            c.add_energy(lab, [t0 - k * gap0 for k in range(1, 5)], 0.0)
        p = t0
        while p <= end:
            ev.append((p, None, None))
            p += int(polls_s * S)
        ev.sort(key=lambda e: (e[0], e[1] is None))
        before = c.count
        for a, name, t in ev:
            now = (a - t0) / S + 1.0
            if name is None:
                c.energy_answered(now)
                for lab, ts in energy.items():
                    c.add_energy(lab, [x for x in ts if a - 90 * S <= x <= a], now)
            else:
                c.add(name, [t], now)
        return c.count - before

    print("Energies judge, cameras fill what they missed")
    plan = [1000 * S + k * 5 * S for k in range(40)]
    e_drop, e_drop2 = {7, 15, 16, 30}, {7, 15, 16, 22}
    got = judged(plan, {"cam:A": (160 * MS, set(), None),
                        "cam:B": (500 * MS, {16}, None)})
    check(got == 40, f"5 s: energies missed 7, 15, 16, cameras saw them = 40 ({got})")
    got = judged(plan, {"cam:A": (160 * MS, set(), None),
                        "cam:D": (-100 * MS, set(), 5 * S),
                        "cam:E": (2500 * MS, set(), 5 * S)},
                 stop_after=plan[-1] + 120 * S)
    check(got == 40, f"5 s: cameras running on after the laser stopped add nothing = 40 "
                     f"({got})")
    e_drop, e_drop2 = set(), set()
    got = judged(plan, {"cam:F": (0, set(), 300 * MS), "cam:G": (30 * MS, set(), 100 * MS)})
    check(got == 40, f"5 s: only 3.3 Hz and 10 Hz cameras beside the energies = 40 ({got})")

    print("3.3 Hz")
    plan = [1000 * S + k * 300 * MS for k in range(120)]
    e_drop, e_drop2 = {40, 41, 90}, {41, 90, 100}
    got = judged(plan, {"cam:A": (160 * MS, set(), None),
                        "cam:B": (500 * MS, {90}, None),
                        "cam:G": (30 * MS, set(), 100 * MS)})
    check(got == 120, f"energies at 3.3 Hz missed 41 and 90, cameras saw them = 120 ({got})")

    print("The rate changes in the middle of a run")
    plan = ([1000 * S + k * 5 * S for k in range(20)]
            + [1100 * S + k * 300 * MS for k in range(100)])
    e_drop, e_drop2 = set(), set()
    got = judged(plan, {"cam:A": (160 * MS, set(), None),
                        "cam:F": (0, set(), 300 * MS)})
    check(got == 120, f"20 shots at 5 s, then 100 at 3.3 Hz = 120 ({got})")

    print()
    if fails:
        print(f"{len(fails)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
