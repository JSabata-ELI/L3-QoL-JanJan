"""Spectra: several days, each with its own time window.

What is pinned here is what the operator was promised:

  1. only the chosen hours are on the graph — unselected time is removed
  2. one day behaves exactly as before (the map is the identity)
  3. no line is drawn across the join between two days
  4. a value read off the graph names the right day and time
  5. a drag across a join becomes one spectrum per day, in real time
  6. a spectrum selected earlier still shades correctly under a new selection

The window arithmetic itself comes from the shared calendar (daypicker.py), so
that is checked here too: the picked hours are what gets fetched.

No network, no share:

    python testing/test_spectra_windows.py
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="spectra_win_test_")

# A Czech-locale console is cp1250 and cannot print the arrows in these names.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import numpy as np                                             # noqa: E402

import sp_t                                                    # noqa: E402
from sp_t import _TimeMap, _ns_to_dt                           # noqa: E402

dp = sp_t.daypicker

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


TZ = dp.TZ_PRAGUE
MON = date(2026, 8, 24)          # Monday
WED = date(2026, 8, 26)          # Wednesday


def ns(d: date, h: int, m: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp() * 1e9)


# Mon 08:00-12:00 and Wed 14:00-19:00 — two days, two different windows, with a
# two-day hole between them.
W_MON = (ns(MON, 8), ns(MON, 12))
W_WED = (ns(WED, 14), ns(WED, 19))
H = 3600.0


def test_from_the_calendar():
    """The picked hours, not whole days, are what ends up being fetched."""
    segs = [dp.PickSeg(MON, 8, 0, 12, 0), dp.PickSeg(WED, 14, 30, 19, 0)]
    windows = [dp.seg_bounds_ns(s) for s in segs]
    check("the calendar gives one window per day", len(windows) == 2)
    check("a window is only the chosen hours",
          abs((windows[0][1] - windows[0][0]) / 1e9 - 4 * H) < 1,
          f"{(windows[0][1] - windows[0][0]) / 1e9:.0f} s")
    check("minutes are honoured",
          abs((windows[1][1] - windows[1][0]) / 1e9 - 4.5 * H) < 1,
          f"{(windows[1][1] - windows[1][0]) / 1e9:.0f} s")
    total = sum(b - a for a, b in windows) / 1e9
    check("the two days together are 8.5 h, not 3 whole days",
          abs(total - 8.5 * H) < 1, f"{total / H:.2f} h")


def test_identity_for_one_window():
    """One day must behave exactly as it did before the rework."""
    tm = _TimeMap([W_MON])
    check("one window is the identity", tm.is_identity())
    check("it starts at x=0", tm.to_x(W_MON[0]) == 0.0)
    check("4 h maps to 4 h of axis",
          abs(tm.to_x(W_MON[1] - 10 ** 9) - (4 * H - 1)) < 1e-6)
    check("no join to mark", tm.boundaries() == [])
    # The end is exclusive, the same as the calendar's: 08:00-12:00 is four
    # hours, and a sample stamped exactly 12:00:00 was not asked for.
    check("the window end itself is not inside the window",
          tm.to_x(W_MON[1]) is None and not tm.contains(W_MON[1]))
    check("the window start is inside it", tm.contains(W_MON[0]))
    check("the axis still reaches the end", abs(tm.xlim()[1] - 4 * H) < 60)


def test_compression():
    tm = _TimeMap([W_MON, W_WED])
    check("two windows", len(tm.windows) == 2)
    check("the axis holds 9 h of data, not 3 days",
          abs(tm.to_x(W_WED[1] - 10 ** 9) - (9 * H - 1)) < 1e-6,
          f"{tm.to_x(W_WED[1] - 10 ** 9) / H:.2f} h")
    lo, hi = tm.xlim()
    check("the view is that 9 h plus a hair of margin",
          lo < 0 < 9 * H < hi and (hi - lo) - 9 * H < 300,
          f"{lo:.1f} .. {hi:.1f} s")
    check("Wednesday starts right where Monday ended",
          abs(tm.to_x(W_WED[0]) - 4 * H) < 1e-6)
    check("one join to mark", len(tm.boundaries()) == 1)
    check("the join sits at the end of Monday",
          abs(tm.boundaries()[0] - 4 * H) < 1e-6)
    # Tuesday is not on the axis at all.
    tue_noon = ns(date(2026, 8, 25), 12)
    check("a time nobody asked for is not on the axis",
          tm.to_x(tue_noon) is None and not tm.contains(tue_noon))
    check("Monday 13:00 is not on the axis either (outside the window)",
          tm.to_x(ns(MON, 13)) is None)


def test_round_trip():
    """A point on the graph must name the instant it was drawn from."""
    tm = _TimeMap([W_MON, W_WED])
    worst = 0
    for t in (W_MON[0], W_MON[0] + int(1.5 * H * 1e9), W_MON[1] - 10 ** 9,
              W_WED[0], W_WED[0] + int(3 * H * 1e9), W_WED[1] - 10 ** 9):
        x = tm.to_x(t)
        back = tm.from_x(x)
        worst = max(worst, abs(back - t))
    check("time → x → time comes back to the same instant",
          worst < 10 ** 6, f"worst {worst} ns")
    # The join is one point standing for two instants (Monday's end and
    # Wednesday's start). The next day wins, so a drag started on the divider
    # belongs to the day drawn to the right of it.
    check("the divider itself reads as the start of the next day",
          tm.from_x(4 * H) == W_WED[0], str(_ns_to_dt(tm.from_x(4 * H))))
    check("a hair to the left of it is still the previous day",
          _ns_to_dt(tm.from_x(4 * H - 0.5)).date() == MON)
    check("x on Wednesday's side reads as Wednesday",
          _ns_to_dt(tm.from_x(5 * H)).date() == WED,
          str(_ns_to_dt(tm.from_x(5 * H))))
    check("x on Monday's side reads as Monday",
          _ns_to_dt(tm.from_x(1 * H)).date() == MON,
          str(_ns_to_dt(tm.from_x(1 * H))))


def test_no_line_across_the_join():
    """steps-post would otherwise join Monday noon to Wednesday afternoon."""
    tm = _TimeMap([W_MON, W_WED])
    t = np.array([ns(MON, 9), ns(MON, 10), ns(WED, 15), ns(WED, 16)],
                 dtype=np.int64)
    v = np.array([1.0, 2.0, 3.0, 4.0])
    px, py, cx, cy = tm.trace(t, v)
    nan_at = np.flatnonzero(np.isnan(px))
    check("the plotted line is broken at the join", len(nan_at) == 1,
          f"{len(nan_at)} breaks")
    check("nothing plotted is out of order",
          np.all(np.diff(px[~np.isnan(px)]) >= 0))
    check("the crosshair series has no gaps in it",
          not np.any(np.isnan(cx)) and np.all(np.diff(cx) >= 0))
    check("the last value of each day is held to the day's end",
          abs(cx[-1] - 9 * H) < 1e-6 and cy[-1] == 4.0,
          f"x={cx[-1] / H:.2f} h v={cy[-1]}")
    # A sample in a gap must never reach the graph.
    t2 = np.array([ns(MON, 9), ns(date(2026, 8, 25), 12), ns(WED, 15)],
                  dtype=np.int64)
    px2, _py2, cx2, _cy2 = tm.trace(t2, np.array([1.0, 99.0, 3.0]))
    check("a sample from the removed time is not drawn",
          99.0 not in list(_py2[~np.isnan(_py2)]))


def test_drag_across_the_join():
    """One drag over two days = one spectrum per day, in real time."""
    tm = _TimeMap([W_MON, W_WED])
    # From Monday 11:00 to Wednesday 15:00 on screen.
    t0, t1 = tm.from_x(3 * H), tm.from_x(5 * H)
    parts = tm.split_ns(t0, t1)
    check("the drag becomes two spectra", len(parts) == 2, str(len(parts)))
    check("the first ends when Monday's window ends", parts[0][1] == W_MON[1])
    check("the second starts when Wednesday's window starts",
          parts[1][0] == W_WED[0])
    check("no removed time is inside either spectrum",
          all(tm.contains(a) and tm.contains(b - 1) for a, b in parts))
    check("a drag inside one day stays one spectrum",
          len(tm.split_ns(tm.from_x(1 * H), tm.from_x(2 * H))) == 1)


def test_old_region_shading():
    """A spectrum picked before the days changed still shades sensibly."""
    tm = _TimeMap([W_MON, W_WED])
    # Selected while Tuesday was loaded: wholly inside the removed time.
    tue = date(2026, 8, 25)
    check("a spectrum wholly in removed time shades nothing",
          tm.clip(ns(tue, 9), ns(tue, 10)) == [])
    # One that overhangs both ends of Monday's window.
    blocks = tm.clip(ns(MON, 6), ns(MON, 20))
    check("a spectrum overhanging a window is cut to the window",
          len(blocks) == 1 and abs(blocks[0][0]) < 1e-6
          and abs(blocks[0][1] - 4 * H) < 1e-6, str(blocks))
    # One that covers everything: two blocks, never one block over the hole.
    blocks = tm.clip(ns(MON, 0), ns(WED, 23))
    check("a spectrum over both days shades two blocks, not the hole",
          len(blocks) == 2, str(len(blocks)))
    check("a time in the hole is pulled to the nearest edge, not lost",
          abs(tm.to_x_clamped(ns(tue, 12)) - 4 * H) < 1e-6)


def test_axis_labels():
    tm = _TimeMap([W_MON, W_WED])
    pos, lab = tm.ticks(12)
    check("every day gets at least its own labelled tick",
          all(any(abs(p - off) < 1e-6 for p in pos) for off in (0.0, 4 * H)))
    check("ticks are inside the axis", all(-1 <= p <= 9 * H + 1 for p in pos))
    check("as many labels as ticks", len(pos) == len(lab))
    check("a tick label names a real clock time",
          all(":" in s for s in lab), str(lab[:4]))
    check("the day start is flagged so the date can be shown there",
          tm.is_window_start(0.0) and tm.is_window_start(4 * H)
          and not tm.is_window_start(2 * H))


def test_empty_and_overlap():
    tm = _TimeMap([])
    check("no selection: the map is empty and harmless",
          not tm and tm.to_x(W_MON[0]) is None and tm.clip(0, 1) == []
          and tm.from_x(5.0) == 0)
    # Two windows that touch or overlap must merge, or from_x would be ambiguous.
    tm2 = _TimeMap([(ns(MON, 8), ns(MON, 12)), (ns(MON, 10), ns(MON, 14))])
    check("overlapping windows merge into one", len(tm2.windows) == 1,
          str(len(tm2.windows)))
    check("the merged window spans 6 h",
          abs(tm2.to_x(ns(MON, 14) - 10 ** 9) - (6 * H - 1)) < 1e-6)
    check("a zero-length window is dropped",
          _TimeMap([(ns(MON, 8), ns(MON, 8))]).windows == [])


def test_which_day_is_under_the_mouse():
    """window_at_x / window_len_ns — what the double-click and the trim rely on."""
    tm = _TimeMap([W_MON, W_WED])
    check("a point inside the first block names Monday's window",
          tm.window_at_x(2 * H) == W_MON, str(tm.window_at_x(2 * H)))
    check("a point inside the second block names Wednesday's window",
          tm.window_at_x(6 * H) == W_WED, str(tm.window_at_x(6 * H)))
    check("the join belongs to the day on its right, as from_x says",
          tm.window_at_x(4 * H) == W_WED, str(tm.window_at_x(4 * H)))
    check("the very start is Monday", tm.window_at_x(0.0) == W_MON)
    check("the very end is still Wednesday", tm.window_at_x(9 * H) == W_WED)
    check("the padding left of the axis is no day",
          tm.window_at_x(-0.5) is None)
    check("the padding right of the axis is no day",
          tm.window_at_x(9 * H + 5) is None)
    check("an empty map has no day anywhere",
          _TimeMap([]).window_at_x(1.0) is None)

    check("a day's own length is its loaded window, not the whole axis",
          tm.window_len_ns(ns(MON, 9)) == W_MON[1] - W_MON[0]
          and tm.window_len_ns(ns(WED, 15)) == W_WED[1] - W_WED[0],
          f"{tm.window_len_ns(ns(MON, 9)) / 3.6e12:.1f} h")
    check("removed time has no window length",
          tm.window_len_ns(ns(date(2026, 8, 25), 12)) == 0)
    check("the exclusive end of a window is already outside it",
          tm.window_len_ns(W_MON[1]) == 0)


def main() -> int:
    print("test_spectra_windows")
    for fn in (test_from_the_calendar, test_identity_for_one_window,
               test_compression, test_round_trip, test_no_line_across_the_join,
               test_drag_across_the_join, test_old_region_shading,
               test_axis_labels, test_empty_and_overlap,
               test_which_day_is_under_the_mouse):
        print(f"\n{fn.__name__}")
        fn()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
