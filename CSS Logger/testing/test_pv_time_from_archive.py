"""The PV Time Plot tab, end to end, against the real archiver.

Builds the CSS Logger tab, puts the Ramping channels in the PV list, picks two
working days, presses Plot and waits for the fetch to finish -- then screenshots
the tab in both modes so the drawing can be looked at rather than guessed about.

Run:  set QT_QPA_PLATFORM=windows && python test_pv_time_from_archive.py
(offscreen has no fonts and would lie about the text; see the house rule.)
"""
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import main as css
from cpva_core import RAMPING_PV_MAP

OUT = HERE / "_out"
OUT.mkdir(exist_ok=True)


def _pump(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _workdays(n):
    out, d = [], date.today()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # The program is light panels with dark ink, and none of that comes from
    # Windows. Without this the window is rendered in the machine's own dark
    # theme, which the real program never shows, and the screenshots are
    # useless for judging any colour.
    css.install_app_look(app)
    w = css.CSSLoggerWidget()
    w.resize(1500, 900)
    w.show()
    _pump(app, 1.0)

    # No live mode in a test: it would keep refetching under the plot.
    if w._live_mode:
        w._stop_live("test")

    w._pv_list.clear()
    for pv in RAMPING_PV_MAP.values():
        w._pv_list.addItem(pv)
    w._sync_pv_list_customs()
    w._refresh_pv_time_choices()

    labels = [w._pv_time_y_combo.itemText(i)
              for i in range(w._pv_time_y_combo.count())]
    print("Y choices:", labels)
    assert labels, "the Y drop-down stayed empty"

    # Y = sbw4, condition off (the default target 70 has nothing to do with it)
    idx = next(i for i, l in enumerate(labels) if "SBW4" in l.upper())
    w._pv_time_y_combo.setCurrentIndex(idx)
    for row in w._pv_time_condition_rows:
        row["enabled"].setChecked(False)

    w._pv_time_days = _workdays(2)
    w._refresh_pv_time_days_label()
    print("days:", w._pv_time_days, "|", w._lbl_pv_time_days.text())

    tab = w._notebook
    tab.setCurrentIndex(2)                     # PV Time Plot
    _pump(app, 0.3)

    t0 = time.monotonic()
    w._plot_pv_time()
    while w._pv_time_busy and time.monotonic() - t0 < 180:
        _pump(app, 0.2)
    _pump(app, 1.0)
    print(f"fetch+draw took {time.monotonic() - t0:.1f} s")
    print("info:", w._lbl_pv_time_info.text())
    assert w._pv_time_canvas is not None, "no violin plot was drawn"
    w.grab().save(str(OUT / "pv_time_daily.png"))

    # Same days again: must redraw from memory, without touching the archiver.
    t1 = time.monotonic()
    w._plot_pv_time()
    _pump(app, 0.5)
    print(f"replot from cache took {time.monotonic() - t1:.1f} s")
    assert not w._pv_time_busy, "the second Plot went back to the archiver"

    # A condition on a channel that is already in memory must not refetch.
    row = w._pv_time_condition_rows[0]
    row["variable"].setCurrentIndex(idx)
    row["target"].setText("5")
    row["tolerance"].setText("80")
    row["enabled"].setChecked(True)
    w._plot_pv_time()
    _pump(app, 0.5)
    assert not w._pv_time_busy, "changing a condition refetched the days"
    print("with condition:", w._lbl_pv_time_info.text())
    w.grab().save(str(OUT / "pv_time_condition.png"))
    row["enabled"].setChecked(False)

    # A condition on a channel NOT read yet: only that channel is fetched.
    other = next(i for i, l in enumerate(labels) if "PTM1 -" in l)
    row["variable"].setCurrentIndex(other)
    row["target"].setText("60")
    row["tolerance"].setText("90")
    row["enabled"].setChecked(True)
    t2 = time.monotonic()
    w._plot_pv_time()
    while w._pv_time_busy and time.monotonic() - t2 < 120:
        _pump(app, 0.2)
    _pump(app, 0.5)
    print(f"new condition channel took {time.monotonic() - t2:.1f} s:",
          w._lbl_pv_time_info.text())
    row["enabled"].setChecked(False)

    # Raw shots mode.
    w._pv_time_mode_combo.setCurrentText("Raw shots")
    w._plot_pv_time()
    while w._pv_time_busy and time.monotonic() - t2 < 120:
        _pump(app, 0.2)
    _pump(app, 0.8)
    print("raw:", w._lbl_pv_time_info.text())
    assert w._pv_time_canvas is not None, "raw shots drew nothing"
    w.grab().save(str(OUT / "pv_time_raw.png"))

    # A derived channel as Y, over the same days: nothing new to read.
    derived = next((i for i, l in enumerate(labels) if l.startswith("Sum of Green")), None)
    if derived is not None:
        w._pv_time_mode_combo.setCurrentText("Daily distribution")
        w._pv_time_y_combo.setCurrentIndex(derived)
        t3 = time.monotonic()
        w._plot_pv_time()
        while w._pv_time_busy and time.monotonic() - t3 < 120:
            _pump(app, 0.2)
        _pump(app, 0.8)
        print("derived Y:", w._lbl_pv_time_info.text())
        w.grab().save(str(OUT / "pv_time_derived.png"))

    print("saved to", OUT)
    w.close()


if __name__ == "__main__":
    main()
