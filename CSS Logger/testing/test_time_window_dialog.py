"""
Start/End time picker test for the CSS Logger.

Guards the one thing that made a long period silently come back as "the last
hour": paging the calendar to another month (the ◀ ▶ buttons, the month menu,
the year box) does not move the selected DAY, so a user who navigates to
February and presses OK gets today's date back — and with the clock boxes still
holding the old window's start time, the result is exactly the window they
started from.

Checks:
  * paging the calendar moves the selection onto the shown month,
  * the status line follows it,
  * OK then hands back the month that is on screen,
  * the multi-day picker is NOT affected (its selection is an explicit list).

Runs headless — no archiver needed.

Run:  python test_time_window_dialog.py
"""
import os
import pathlib
import sys
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                     # noqa: E402
from PySide6.QtCore import QDate                            # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])

_fails = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not ok:
        _fails.append(name)


def test_page_moves_selection():
    now = datetime(2026, 9, 4, 15, 58, 0)
    dlg = app_main.TimeWindowDialog(None, now - timedelta(hours=1), now)
    side = dlg._sides["from"]
    side["tabs"].setCurrentIndex(0)             # Absolute Start
    cal = side["cal"]

    # What the ◀ button / month menu / year box all do underneath.
    cal.setCurrentPage(2026, 2)
    _app.processEvents()

    sel = cal.selectedDate()
    check("paging to February moves the selected day",
          (sel.year(), sel.month()) == (2026, 2),
          f"selected {sel.toString('yyyy-MM-dd')}")

    got = dlg._compute("from")
    check("OK hands back the month on screen",
          (got.year, got.month) == (2026, 2),
          f"start = {got:%Y-%m-%d %H:%M}")

    check("clock boxes are kept", (got.hour, got.minute) == (14, 58),
          f"{got:%H:%M}")

    check("status line follows the page",
          "2026-02" in dlg._lbl_start_status.text(),
          dlg._lbl_start_status.text())

    # The painted highlight must agree with what will be returned, or the user
    # cannot see which day the dialog is about to hand back.
    deleg = getattr(cal, "_wk_delegate", None)
    keys = getattr(deleg, "_selected_keys", set()) if deleg else set()
    check("the highlight is on the returned day",
          (sel.year(), sel.month(), sel.day()) in keys,
          str(keys))

    # A period that starts in February and ends "now" must survive as such.
    dlg._on_ok()
    span_days = (dlg._result_to - dlg._result_from).days
    check("the accepted period is months, not an hour", span_days > 100,
          f"{span_days} days")
    dlg.deleteLater()


def test_day_31_clamped():
    now = datetime(2026, 3, 31, 12, 0, 0)
    dlg = app_main.TimeWindowDialog(None, now - timedelta(hours=1), now)
    side = dlg._sides["from"]
    side["tabs"].setCurrentIndex(0)
    side["cal"].setCurrentPage(2026, 2)         # February has no 31st
    _app.processEvents()
    sel = side["cal"].selectedDate()
    check("a short month clamps the day", sel.isValid() and sel.month() == 2,
          sel.toString("yyyy-MM-dd"))
    dlg.deleteLater()


def test_multi_day_picker_untouched():
    dlg = app_main.DatePickerDialog(None, QDate(2026, 9, 4))
    dlg._cal.setCurrentPage(2026, 2)
    _app.processEvents()
    days = dlg.selected_dates()
    check("the multi-day picker keeps its own list",
          len(days) == 1 and (days[0].year(), days[0].month()) == (2026, 9),
          ", ".join(d.toString("yyyy-MM-dd") for d in days))
    dlg.deleteLater()


if __name__ == "__main__":
    test_page_moves_selection()
    test_day_31_clamped()
    test_multi_day_picker_untouched()
    print()
    if _fails:
        print(f"{len(_fails)} FAILED: " + ", ".join(_fails))
        sys.exit(1)
    print("all checks passed")
