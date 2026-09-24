"""The auto-copy time grid: cycle moments exactly one interval apart, and a
delay that is always measured from the start of the run.

Run:  python testing/test_auto_grid.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

SEC = 1_000_000_000
FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def test_targets():
    print("recorded cycle moments")
    t0 = 1_757_000_000 * SEC
    targets = [s.auto_tick_target_ns(t0, n, 5) for n in range(6)]
    gaps = {targets[i + 1] - targets[i] for i in range(len(targets) - 1)}
    check("first cycle is the start of the run", targets[0] == t0)
    check("every cycle is exactly 5 s after the one before", gaps == {5 * SEC})
    check("cycle 6 is 25 s in", targets[5] - t0 == 25 * SEC)


def test_delays():
    print("scheduling")
    t0 = 1000.0
    # Cycle 1 ran long: it is now 12 s into a 5 s run.
    now = t0 + 12.0
    check("a tick already due fires at once",
          s.auto_tick_delay_ms(t0, 2, 5, now) == 0)
    check("the next tick still lands on the grid, not 5 s from now",
          s.auto_tick_delay_ms(t0, 3, 5, now) == 3000)
    # Normal case: 1.2 s into the run, tick 1 is due at 5 s.
    check("on time -> the remainder of the interval",
          s.auto_tick_delay_ms(t0, 1, 5, t0 + 1.2) == 3800)
    # A whole run's worth of ticks never drifts, however late the clock is read.
    delays = [s.auto_tick_delay_ms(t0, n, 5, t0 + 0.4) for n in range(4)]
    check("delays follow the grid from t0", delays == [0, 4600, 9600, 14600])


def test_window():
    print("backward window")
    check("the window is 10 s", s.AUTO_BACK_WINDOW_NS == 10 * SEC)
    check("a cycle 9 s late is still inside the window",
          9 * SEC <= s.AUTO_BACK_WINDOW_NS)
    check("a cycle 11 s late is outside it (it gets dropped)",
          11 * SEC > s.AUTO_BACK_WINDOW_NS)


if __name__ == "__main__":
    test_targets()
    test_delays()
    test_window()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")
