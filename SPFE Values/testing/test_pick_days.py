"""
Pick days: the calendar that reaches back further than the log goes.

Worth a test of its own because it is the one place this program talks to
`daypicker.py`, the shared calendar. Nothing here checks how it looks -- that is
what render_window.py is for -- but everything it calls on the calendar is
exercised, so a change on the Image Tools side that renames one of them fails
here instead of in front of an operator.

APPDATA is redirected and the share is pointed at a folder inside it, so neither
the real log nor the scratch share is touched.

Run:  python testing/test_pick_days.py
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
_TMP = tempfile.mkdtemp(prefix="spfe_pick_")
os.environ["APPDATA"] = _TMP

import spfe_store as store                                  # noqa: E402

_FAKE_SHARE = Path(_TMP) / "share"
_FAKE_SHARE.mkdir(parents=True, exist_ok=True)
store.SHARE_CANDIDATES = (str(_FAKE_SHARE),)

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def main():
    print("pick days")
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QDialog
    except ImportError:
        print("  SKIP  PySide6 is not installed")
        return 0
    import spfe_t
    from spfe_t import SPFEValuesWidget
    # The window loads the calendar by path; the same module object is used
    # here, so this test cannot accidentally check a different copy.
    dp = spfe_t.daypicker

    app = QApplication.instance() or QApplication([])
    w = SPFEValuesWidget()
    w.cfg["catch_up_on_start"] = False
    w._day = date(2026, 8, 18)                 # a Tuesday, well before the log

    # The picker is modal, so the "operator" is a timer: it finds the dialog,
    # clicks two more days into it and presses OK.
    picked_now = {}

    def drive():
        dlg = [d for d in w.findChildren(QDialog) if d.isVisible()]
        if not dlg:
            QTimer.singleShot(50, drive)
            return
        d = dlg[0]
        cal = d.findChild(dp.NoScrollCalendar)
        picked_now["has_calendar"] = cal is not None
        if cal is not None:
            picked_now["selected"] = len(cal.day_delegate._selected_keys)
        d.accept()

    QTimer.singleShot(0, drive)
    days = w._ask_for_days()

    check("the dialog carries the shared calendar",
          picked_now.get("has_calendar") is True)
    check("it opens with the day on screen already picked",
          picked_now.get("selected") == 1, str(picked_now.get("selected")))
    check("OK returns exactly that day", days == [date(2026, 8, 18)], str(days))

    # Cancel must return nothing at all: nothing takes effect until OK.
    def cancel():
        dlg = [d for d in w.findChildren(QDialog) if d.isVisible()]
        if not dlg:
            QTimer.singleShot(50, cancel)
            return
        dlg[0].reject()

    QTimer.singleShot(0, cancel)
    check("Cancel picks nothing", w._ask_for_days() == [])

    # The span the buttons hand to the filler must not run into the future:
    # the archiver cannot answer for a moment that has not happened.
    w._day = date.today() + timedelta(days=3)
    w._on_fill_this_day()
    check("a day in the future is refused, with a reason",
          "not happened yet" in w.lbl_status.text(), w.lbl_status.text())

    # And a weekend picked by hand IS filled in, unlike the automatic catch-up.
    sat = date(2026, 8, 15)                    # a Saturday
    cfg_weekend = dict(w.cfg, weekdays_only=False)
    check("a hand-picked Saturday has both moments",
          len(store.scheduled_slots(
              sat, datetime(2026, 8, 15, 23, 59, tzinfo=store.TZ_PRAGUE),
              cfg_weekend)) == 2)
    check("while the automatic catch-up still skips it",
          store.scheduled_slots(
              sat, datetime(2026, 8, 15, 23, 59, tzinfo=store.TZ_PRAGUE),
              w.cfg) == [])

    w.shutdown()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
