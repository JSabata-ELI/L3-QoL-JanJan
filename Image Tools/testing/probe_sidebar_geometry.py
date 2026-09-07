"""Where does every widget in the PV Search sidebar actually sit?

Why: a screenshot cannot say whether a caption is missing, zero-height, or
painting its text in the background colour. This prints each row's geometry, text
and ink, which is how the clipped first caption was tracked down.

Note: `QWidget.grab()` on the WHOLE dialog does not paint the sidebar's first
caption, while grabbing the sidebar itself does — so judge the panel from a grab of
the sidebar (or from the real window), never from the whole-dialog picture alone.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python probe_sidebar_geometry.py
"""
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder, prague_bounds  # noqa: E402

DAY = date(2026, 8, 17)


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication, QLabel

    t8 = prague_bounds(DAY, 8)
    series = [(t8 + i * 10 * 1_000_000_000, 9.0 + (i % 100) / 100.0)
              for i in range(600)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    dlg = m.PVRegionSearchDialog([], [QDate(DAY.year, DAY.month, DAY.day)])
    dlg.resize(1280, 940)
    dlg.show()
    app.processEvents()

    host = dlg._cal_frame.parentWidget()
    print(f"sidebar host: {host.objectName() or type(host).__name__} "
          f"{host.geometry().getRect()}")
    lay = host.layout()
    for i in range(lay.count()):
        item = lay.itemAt(i)
        w = item.widget()
        if w is None:
            print(f"  [{i}] layout {type(item).__name__}")
            continue
        txt = w.text() if isinstance(w, QLabel) else ""
        print(f"  [{i}] {type(w).__name__:14s} {w.geometry().getRect()!s:24s} "
              f"vis={w.isVisible()} text={txt!r} "
              f"ink={w.palette().windowText().color().name()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
