"""The one day/time picker — the rules every tab now shares.

Image Tools used to carry three different calendars with three different sets of
rules; `daypicker.py` is the one widget they all open now. What is pinned here is
the BEHAVIOUR the operator was promised, not the implementation:

  1. one calendar
  2. plain click = one day · Ctrl+click = add one (weekends included) ·
     Ctrl+Shift+click = a stretch, XOR-ed in, weekends skipped unless ticked
  3. a second day switches the per-day time table on by itself
  4. a day just added gets 08:00–19:00, or 08:00–now for today
  5. always OK and Cancel — nothing takes effect until OK
  6. the house look: Monday first, red weekends, grey header band
  7. Live mode is the Image Slider's alone

Offscreen, no share and no network:

    python testing/test_daypicker.py
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

# Point APPDATA at a scratch folder BEFORE any widget is built, or running the
# tests rewrites the operator's own remembered window.
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="daypicker_test_")

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QDate, QTime                       # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

import daypicker as dp                                        # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# Fixed dates so the tests read the same in January as in August.
MON = date(2026, 8, 3)          # Monday
FRI = date(2026, 8, 14)         # the Friday two weeks on
SAT = date(2026, 8, 8)          # Saturday
PAST_A, PAST_B = date(2026, 8, 18), date(2026, 8, 19)
NOW = datetime(2026, 8, 25, 14, 30, tzinfo=dp.TZ_PRAGUE)      # a Tuesday afternoon


def test_click_rules():
    print("\nrule 2 — what a click does")
    gate = {0, 1, 2, 3, 4}                                    # Mon–Fri

    check("a plain click leaves exactly one day",
          dp.compute_click([MON, FRI], date(2026, 8, 10), FRI, False, False)
          == [date(2026, 8, 10)])

    check("Ctrl+click adds a day",
          dp.compute_click([MON], date(2026, 8, 4), MON, True, False)
          == [MON, date(2026, 8, 4)])
    check("Ctrl+click on a picked day takes it out",
          dp.compute_click([MON, date(2026, 8, 4)], date(2026, 8, 4), MON, True, False)
          == [MON])
    # The rule that changed: Ctrl used to REFUSE a weekend and the click did
    # nothing at all, which reads as a dead widget.
    check("Ctrl+click works on a Saturday too",
          dp.compute_click([date(2026, 8, 7)], SAT, date(2026, 8, 7), True, False)
          == [date(2026, 8, 7), SAT],
          f"{SAT} is a {SAT.strftime('%A')}")

    stretch = dp.compute_click([MON], FRI, MON, True, True, gate)
    check("Ctrl+Shift takes the stretch from the last click",
          len(stretch) == 10, f"{len(stretch)} days")
    check("and it skips Saturdays and Sundays",
          all(d.weekday() < 5 for d in stretch),
          str([str(d) for d in stretch if d.weekday() >= 5]))
    check("the day it started from is never dropped", MON in stretch)
    check("the same stretch again takes it back out",
          dp.compute_click(stretch, FRI, MON, True, True, gate) == [MON])
    check("ticking Sat/Sun lets the stretch have them",
          len(dp.compute_click([MON], date(2026, 8, 9), MON, True, True,
                               set(range(7)))) == 7)


def test_default_windows():
    print("\nrule 4 — the window a day gets when it is added")
    check("a past day is the whole lab day",
          dp.default_window_for(PAST_A, NOW) == (8, 0, 19, 0),
          str(dp.default_window_for(PAST_A, NOW)))
    check("today stops at the current hour",
          dp.default_window_for(NOW.date(), NOW) == (8, 0, 15, 0),
          str(dp.default_window_for(NOW.date(), NOW)))
    early = datetime(2026, 8, 25, 8, 20, tzinfo=dp.TZ_PRAGUE)
    check("and never opens on an empty window first thing in the morning",
          dp.default_window_for(early.date(), early) == (8, 0, 9, 0),
          str(dp.default_window_for(early.date(), early)))


def test_bounds():
    print("\nthe window in nanoseconds")
    s, e = dp.seg_bounds_ns(dp.PickSeg(PAST_A, 8, 0, 19, 0))
    check("08:00–19:00 is eleven hours", (e - s) == 11 * 3600 * 10**9)
    s, e = dp.seg_bounds_ns(dp.PickSeg(PAST_A, 0, 0, 23, 59))
    check("a 'to' of 23:59 means midnight, so a whole day is 24 h",
          (e - s) == 24 * 3600 * 10**9)
    check("the exclusive end of an hour is the next one", dp.hour_end_hm(12) == (13, 0))
    check("except 23, which a clock cannot take past 23:59",
          dp.hour_end_hm(23) == (23, 59))


def test_dialog(app):
    print("\nrule 3 — the table appears by itself")
    d = dp.DayTimePicker(init_date=PAST_A)
    d.show(); app.processEvents()
    check("one day: no table, nothing to tick",
          not d.is_multiday() and not d._table.isVisible())
    check("and no 'Multiple days' checkbox exists any more",
          not hasattr(d, "cb_multi"))

    d._set_days([PAST_A, PAST_B]); app.processEvents()
    check("a second day switches multi-day on by itself",
          d.is_multiday() and d._table.isVisible())
    check("with a row per day", d._table.rowCount() == 2, str(d._table.rowCount()))
    check("and one window per day", len(d.selected_windows()) == 2)

    # The ✕ button deletes its own row, so it must not run inside its own click
    # handler — Qt crashes on a widget deleted from the signal it is emitting.
    d._set_days([PAST_A, PAST_B, SAT]); app.processEvents()
    rows = d.selected_dates()                       # the table is date-sorted
    row = rows.index(PAST_A)
    d._table.cellWidget(row, 3).click()
    app.processEvents(); app.processEvents()
    check("the ✕ takes one day out without crashing",
          d.selected_dates() == [x for x in rows if x != PAST_A],
          str(d.selected_dates()))

    d._set_days([PAST_A]); app.processEvents()
    check("back to one day and the table goes away",
          not d.is_multiday() and not d._table.isVisible())
    check("and the last day cannot be removed", d._remove_day(PAST_A) is None
          and d.selected_dates() == [PAST_A])

    print("\nthe global From/To against a row typed by hand")
    d._set_days([PAST_A, PAST_B]); app.processEvents()
    d.time_from.setTime(QTime(10, 30)); app.processEvents()
    d.time_to.setTime(QTime(14, 0)); app.processEvents()
    got = {s.date: (s.h_from, s.m_from, s.h_to, s.m_to) for s in d.all_segments()}
    check("From/To moves every day at once",
          got[PAST_A] == (10, 30, 14, 0) and got[PAST_B] == (10, 30, 14, 0), str(got))

    d._on_row_time(PAST_B, True, QTime(6, 0))
    d._on_row_time(PAST_B, False, QTime(9, 0))
    d.time_from.setTime(QTime(11, 0)); app.processEvents()
    got = {s.date: (s.h_from, s.m_from, s.h_to, s.m_to) for s in d.all_segments()}
    check("a day given its own times keeps them", got[PAST_B] == (6, 0, 9, 0), str(got))
    check("while the others still follow From/To", got[PAST_A][0] == 11, str(got))
    check("and the count says how many were set by hand",
          "1 with their own times" in d._count_lbl.text(), d._count_lbl.text())

    print("\nrule 5 — OK and Cancel are always there")
    from PySide6.QtWidgets import QDialogButtonBox
    box = d.findChild(QDialogButtonBox)
    check("both buttons exist",
          box is not None
          and box.button(QDialogButtonBox.StandardButton.Ok) is not None
          and box.button(QDialogButtonBox.StandardButton.Cancel) is not None)


def test_live(app):
    print("\nrule 7 — Live mode is the Slider's alone")
    off = dp.DayTimePicker(init_date=PAST_A, allow_live=False)
    off.show(); app.processEvents()
    check("a tab without live mode is not offered the tick",
          not off.cb_now.isVisible())
    check("and it can never report itself online", off.is_online_mode() is False)

    on = dp.DayTimePicker(init_date=date.today(), allow_live=True)
    on.show(); app.processEvents()
    check("the Slider is", on.cb_now.isVisible())
    on.cb_now.setChecked(True); app.processEvents()
    now_h = datetime.now(dp.TZ_PRAGUE).hour
    check("ticking it with nothing picked yet falls back to the last hour",
          on.selected_times()[0] == now_h, str(on.selected_times()))

    chosen = dp.DayTimePicker(init_date=date.today(), allow_live=True)
    chosen.show(); app.processEvents()
    chosen.time_from.setTime(QTime(8, 0)); app.processEvents()
    chosen.time_to.setTime(QTime(12, 0)); app.processEvents()
    want = chosen.selected_times()
    chosen.cb_now.setChecked(True); app.processEvents()
    check("but a window the user typed is left exactly as it is",
          chosen.selected_times() == want, f"{chosen.selected_times()} vs {want}")
    chosen._set_days([PAST_A]); app.processEvents()
    check("and picking a past day leaves live mode — it can only follow today",
          not chosen.cb_now.isChecked())


def test_look(app):
    print("\nrule 6 — the house look, against the app's DARK stylesheet")
    app.setStyleSheet("QWidget { background: #2b2b2b; color: #ddd; }")
    d = dp.DayTimePicker(init_date=PAST_A, allow_live=True)
    d.show(); app.processEvents()
    img = d.grab().toImage()

    corner = img.pixelColor(3, 3)
    check("the dialog paints its own light ground, not the app's dark one",
          corner.lightness() > 120, corner.name())

    band = img.pixelColor(d._gate_row.x() + 2, d._gate_row.y() + 2)
    check("the Mon–Sun row carries its own light band",
          band.lightness() > 120, band.name())

    from PySide6.QtWidgets import QLabel
    names = {w.text(): w.styleSheet() for w in d._gate_row.findChildren(QLabel)}
    check("every day name states its own colour", all(names.values()))
    check("Saturday and Sunday are red",
          "#cc0000" in names.get("Sat", "") and "#cc0000" in names.get("Sun", ""))
    check("the weekdays are not", "#cc0000" not in names.get("Mon", ""))
    check("Monday comes first",
          d.cal.firstDayOfWeek() == __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.DayOfWeek.Monday)
    check("Sat/Sun start unticked, so a stretch skips them",
          [c.isChecked() for c in d._wd_checks] == [True] * 5 + [False] * 2)
    app.setStyleSheet("")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    test_click_rules()
    test_default_windows()
    test_bounds()
    test_dialog(app)
    test_live(app)
    test_look(app)
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
