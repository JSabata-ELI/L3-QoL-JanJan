"""The PV overlay's own size, set in "Overlay settings".

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_pv_overlay_size.py

Opens the real settings dialog off a real Viewer, so the row is where the operator
would find it — under the font, next to the colours.

What to check on the pictures:

  * the dialog has a "Panel size (w × h)" row, two boxes that read "auto" at 0 and a
    "Fit to text" button beside them;
  * the panel really is the size that was typed, and back to fitting its text at 0;
  * the values stay readable at every size.

Writes overlay_settings.png, overlay_panel_auto.png and overlay_panel_fixed.png
beside this file.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bench_common import load_slider
from render_save_view_dialog import app_stylesheet

ROWS = [("SBW4", "12.95 J"), ("GDD", "-1240 fs2"), ("Back_Ref", "0.87 J")]

fails: list = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def main() -> int:
    m = load_slider()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QPushButton,
                                   QSpinBox, QWidget, QVBoxLayout)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    out = Path(__file__).parent

    # ── the panel on its own: auto, then held at a size ───────────────────────
    host = QWidget()
    host.setFixedSize(420, 200)
    host.setStyleSheet("background: #303030;")     # a dark picture behind it
    ov = m._PvOverlayPanel(host)
    ov.move(8, 8)
    ov.update_values(ROWS)
    app.processEvents()
    auto_size = (ov.width(), ov.height())
    host.show()
    app.processEvents()
    host.grab().save(str(out / "overlay_panel_auto.png"))

    ov.apply_settings(ov.font_size_px, ov.font_family, ov.bg_opacity,
                      ov.font_color, ov.bg_color, panel_w=260, panel_h=120)
    ov.update_values(ROWS)
    app.processEvents()
    check(ov.size().width() == 260 and ov.size().height() == 120,
          f"the panel is the size that was typed ({ov.width()}×{ov.height()})")
    host.grab().save(str(out / "overlay_panel_fixed.png"))

    # A long value must not push a held panel wider.
    ov.update_values([("SBW4", "12.953456 J"), ("GDD", "-124000 fs2"),
                      ("Back_Ref", "0.8712 J")])
    app.processEvents()
    check(ov.width() == 260, f"a longer value cannot widen it ({ov.width()})")

    ov.apply_settings(ov.font_size_px, ov.font_family, ov.bg_opacity,
                      ov.font_color, ov.bg_color, panel_w=0, panel_h=0)
    ov.update_values(ROWS)
    app.processEvents()
    check(abs(ov.width() - auto_size[0]) <= 2 and abs(ov.height() - auto_size[1]) <= 2,
          f"0 gives back the panel that fits its text ({ov.width()}×{ov.height()} "
          f"vs {auto_size[0]}×{auto_size[1]})")

    # ── the settings dialog itself ────────────────────────────────────────────
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v.resize(1200, 800)
    v.show()
    app.processEvents()

    def grab_dialog():
        for w in app.topLevelWidgets():
            if isinstance(w, QDialog) and w.windowTitle() == "PV Overlay Settings":
                labels = [x.text() for x in w.findChildren(QLabel)]
                check(any("Panel size" in t for t in labels),
                      f"the dialog has a panel-size row ({labels})")
                sbs = [s for s in w.findChildren(QSpinBox)
                       if s.specialValueText() == "auto"]
                check(len(sbs) == 2, f"two size boxes, both 'auto' at 0 ({len(sbs)})")
                check(any(b.text() == "Fit to text"
                          for b in w.findChildren(QPushButton)),
                      "and a 'Fit to text' button")
                if len(sbs) == 2:
                    sbs[0].setValue(300)
                    sbs[1].setValue(140)
                    app.processEvents()
                    check(v._pv_overlay.width() == 300,
                          f"typing a size previews it at once ({v._pv_overlay.width()})")
                w.grab().save(str(out / "overlay_settings.png"))
                # Cancel must put the old size back — but the dialog's own exec() has
                # to return first, so the check waits for the next turn of the loop.
                QTimer.singleShot(400, lambda: check(
                    v._pv_overlay.panel_w == 0,
                    f"Cancel undoes the preview ({v._pv_overlay.panel_w})"))
                w.reject()
                return
        check(False, "the settings dialog opened")

    QTimer.singleShot(900, grab_dialog)
    QTimer.singleShot(2400, app.quit)
    QTimer.singleShot(300, v._open_pv_overlay_settings)
    app.exec()

    print("written: overlay_panel_auto.png, overlay_panel_fixed.png, "
          "overlay_settings.png")
    print()
    if fails:
        print(f"{len(fails)} FAILED")
        for f in fails:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
