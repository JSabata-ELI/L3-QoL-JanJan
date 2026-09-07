"""Render the PV Search window so it can be LOOKED at.

Offscreen has no fonts and lies about text size, so run this with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_pv_search.py

It stubs the archiver (no network), fills a day with samples, picks three moments
and marks a region, then writes `pv_search.png` beside this file. What to check:
the PV list is white with dark ink and its added row is visible, the moment line
reads the count and carries Undo and Clear, and every pick is drawn on the graph
with its own number.
"""
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder, prague_bounds

DAY = date(2026, 8, 17)


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QDate, QTimer
    from PySide6.QtWidgets import QApplication
    from pathlib import Path

    t8 = prague_bounds(DAY, 8)
    # A day of shots, one every 10 s from 08:00 on, with a slow drift so the curve
    # has a shape to look at.
    series = [(t8 + i * 10 * 1_000_000_000, 9.0 + 1.5 * ((i % 360) / 360.0))
              for i in range(3600)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))

    app = QApplication.instance() or QApplication(sys.argv)
    # Under the program's OWN stylesheet, or the panel is judged on colours it never
    # actually has.
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])
    dlg.resize(1280, 940)
    dlg.show()

    def shoot():
        # Three picks and one marked region, through the real gestures.
        for i in (400, 1200, 2600):
            dlg._set_moment_from_x(dlg._ns_to_x(series[i][0], DAY))
        a = dlg._ns_to_x(series[3000][0], DAY)
        b = dlg._ns_to_x(series[3400][0], DAY)
        dlg._on_span(a, b)
        app.processEvents()
        out = Path(__file__).with_name("pv_search.png")
        dlg.grab().save(str(out))
        print("moment line:", dlg._lbl_moment.text())
        print("search button:", dlg._btn_search.text())
        print("status:", dlg._status.text())
        print("written:", out)
        app.quit()

    QTimer.singleShot(2500, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
