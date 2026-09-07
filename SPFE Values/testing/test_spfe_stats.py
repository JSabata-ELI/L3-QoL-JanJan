"""
Headless, no network: the "this value is off" rule.

Run:  python testing/test_spfe_stats.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spfe_stats as stats     # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def test_median_and_mad():
    check("median odd", stats.median([3, 1, 2]) == 2)
    check("median even", stats.median([1, 2, 3, 4]) == 2.5)
    check("mad of a flat series is 0", stats.mad([5, 5, 5, 5]) == 0)
    check("mad of 1..9 is 2", stats.mad(list(range(1, 10))) == 2)


def test_not_enough_history():
    v = stats.judge(100.0, [10, 10, 10], min_history=8)
    check("too little history gives no verdict", not v.flagged and not v.has_history)
    check("and says so", "not enough history" in v.reason)


def test_normal_value_passes():
    hist = [630, 636, 640, 634, 638, 632, 641, 635, 637, 633]
    v = stats.judge(636.0, hist)
    check("a value in the middle is not flagged", not v.flagged, v.reason)
    check("and it reports the median", abs(v.median - 635.5) < 1e-9)


def test_clear_outlier_flags():
    hist = [630, 636, 640, 634, 638, 632, 641, 635, 637, 633]
    v = stats.judge(6360.0, hist)
    check("a 10x value is flagged", v.flagged)
    check("the reason names the spread", "usual spread" in v.reason, v.reason)
    check("the reason gives the median", "635" in v.reason or "636" in v.reason,
          v.reason)


def test_flat_history_tolerates_a_small_change():
    """The case a plain sigma rule gets wrong.

    A DAC setpoint reads exactly the same every day, so MAD is 0 and a single
    count is infinitely many MADs away. It must not be an alarm.
    """
    hist = [24500] * 12
    v = stats.judge(24501.0, hist, flat_tolerance_pct=20.0)
    check("one count off a constant setpoint is not flagged", not v.flagged, v.reason)
    check("no spread is reported for a flat history", v.spread is None)

    v2 = stats.judge(24500.0, hist)
    check("the same value as always is not flagged", not v2.flagged)


def test_flat_history_still_catches_a_real_move():
    hist = [24500] * 12
    v = stats.judge(12000.0, hist, flat_tolerance_pct=20.0)
    check("halving a constant setpoint is flagged", v.flagged, v.reason)
    check("the reason gives the percentage", "%" in v.reason, v.reason)


def test_flat_zero_history():
    hist = [0.0] * 10
    check("zero stays zero without a flag", not stats.judge(0.0, hist).flagged)
    v = stats.judge(3.0, hist)
    check("a first non-zero reading is flagged", v.flagged, v.reason)


def test_one_bad_day_does_not_hide_the_next():
    """Why median/MAD and not mean/sigma: with mean+sigma the 5000 below would
    inflate sigma enough that the second 5000 looks ordinary."""
    hist = [100, 101, 99, 100, 102, 98, 101, 100, 99, 5000]
    v = stats.judge(5000.0, hist)
    check("a repeat of yesterday's bad value is still flagged", v.flagged, v.reason)


def test_text_and_nan_are_ignored():
    hist = [100, 101, "x", None, 99, 100, 102, 98, 101, 100, 99]
    v = stats.judge(100.0, hist)
    check("non-numbers are skipped, not counted", not v.flagged and v.has_history)


def main():
    print("spfe_stats")
    for fn in (test_median_and_mad, test_not_enough_history,
               test_normal_value_passes, test_clear_outlier_flags,
               test_flat_history_tolerates_a_small_change,
               test_flat_history_still_catches_a_real_move,
               test_flat_zero_history, test_one_bad_day_does_not_hide_the_next,
               test_text_and_nan_are_ignored):
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
