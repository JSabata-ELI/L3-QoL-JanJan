"""Reopening the calendar on the last pick, then typing a new From/To, must give
the NEW hours back.

The Spectra tab reopens the picker with its last pick (init_segments) but no
hour_from_init. _restore_segments compared each restored day with From/To while
those still held the 07:00-21:00 default, so every restored day came out
"pinned" — and From/To never moves a pinned day. Changing only the time then
loaded the old hours again; only a new date worked.

    python testing/test_daypicker_restored_window_follows_from_to.py
"""
import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no window, no text checked

HERE = Path(__file__).resolve().parent.parent          # CSS Logger/
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QTime                         # noqa: E402
from PySide6.QtWidgets import QApplication               # noqa: E402

import daypicker                                         # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def hm(seg):
    return (seg.h_from, seg.m_from, seg.h_to, seg.m_to)


def type_window(dlg, a: QTime, b: QTime):
    # To first, then From: each fires timeChanged like typing does.
    dlg.time_to.setTime(b)
    dlg.time_from.setTime(a)


def main() -> int:
    print("test_daypicker_restored_window_follows_from_to")
    app = QApplication.instance() or QApplication([])
    PS = daypicker.PickSeg
    d1, d2, d3 = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)

    # ── one day, restored 10:00-12:00, retyped 08:00-09:00 ──
    dlg = daypicker.DayTimePicker(init_date=d1, init_segments=[PS(d1, 10, 0, 12, 0)],
                                  allow_live=True)
    check("one day: From/To show the restored window",
          dlg.selected_times() == (10, 0, 12, 0), str(dlg.selected_times()))
    type_window(dlg, QTime(8, 0), QTime(9, 0))
    got = [hm(s) for s in dlg.all_segments()]
    check("one day: the new From/To is what comes back", got == [(8, 0, 9, 0)], str(got))
    dlg.deleteLater()

    # ── three days, two sharing 10-12, one with its own 14-16 ──
    segs = [PS(d1, 10, 0, 12, 0), PS(d2, 10, 0, 12, 0), PS(d3, 14, 0, 16, 0)]
    dlg = daypicker.DayTimePicker(init_date=d1, init_segments=segs)
    check("several days: From/To show the shared window",
          dlg.selected_times() == (10, 0, 12, 0), str(dlg.selected_times()))
    type_window(dlg, QTime(6, 0), QTime(8, 30))
    got = {s.date: hm(s) for s in dlg.all_segments()}
    check("several days: shared days follow From/To",
          got[d1] == got[d2] == (6, 0, 8, 30), str(got))
    check("several days: the day with its own times keeps them",
          got[d3] == (14, 0, 16, 0), str(got[d3]))
    dlg.deleteLater()

    app.processEvents()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
