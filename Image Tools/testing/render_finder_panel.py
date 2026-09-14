"""Render the Image Finder tab so its left panel can be LOOKED at.

Offscreen has no fonts and lies about text size, so run this with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_finder_panel.py

No network is touched: the tab does nothing on startup but write its own opening
lines into the log. What to check:
  * Source reads "Time window | PV Search" on the first line and "Cameras" under
    it — the two that used to be the other way round.
  * The log is a short box at the FOOT OF THE PANEL, not a band across the tab.
  * Circle / Square / Cross wear the bare shape the Image Slider's Draw buttons
    show, dark on the pale button, with the colour swatch beside each one.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder  # noqa: E402


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from pathlib import Path

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    w = m.ImageFinderWidget()
    w.resize(1180, 900)
    w.show()

    def shoot():
        app.processEvents()
        out = Path(__file__).with_name("finder_panel.png")
        w.grab().save(str(out))
        print("source row:", w._btn_time_window.text(), "|", w._btn_pv_search.text())
        print("under it  :", w._btn_cameras.text())
        lb = w._log_box
        print("log box   :", lb.width(), "x", lb.height(),
              "at", lb.mapTo(w, lb.rect().topLeft()))
        print("written:", out)

        # The three mark buttons on their own, at four times the size, because at
        # 16 px in a whole-window shot there is no telling a circle from a blob.
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPixmap, QPainter
        btns = [w._wall_draw_btns[k] for k in ("circle", "square", "cross")]
        pad, scale = 6, 4
        cw = max(b.width() for b in btns)
        ch = max(b.height() for b in btns)
        strip = QPixmap((cw + pad) * len(btns) * scale, (ch + pad) * scale)
        strip.fill(Qt.GlobalColor.white)
        p = QPainter(strip)
        p.scale(scale, scale)
        for i, b in enumerate(btns):
            p.drawPixmap(i * (cw + pad) + pad // 2, pad // 2, b.grab())
        p.end()
        out2 = Path(__file__).with_name("finder_mark_buttons.png")
        strip.save(str(out2))
        print("written:", out2)
        app.quit()

    QTimer.singleShot(1200, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
