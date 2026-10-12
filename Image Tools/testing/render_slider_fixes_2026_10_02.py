"""LOOK at the Image Slider fixes of 2 Oct 2026 with real fonts.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_slider_fixes_2026_10_02.py

Writes beside this file:
  timeline_row.png     the Timeline & Range section (icons, Speed on the arrow row,
                       pins either side of Save Timestamp) — enabled and disabled
  pv_overlay_dot.png   the PV overlay without and with an older-shot dot
  mark_tags.png        two crosses with number tags, one selected with its rotate knob
  pointing_progress.png the pointing progress bar mid-run

The window is placed off the desktop and shown without activating, so it never takes
the keyboard. Prints whether every Timeline button's content fits its width.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_is_render1002_")

import is_t                                                          # noqa: E402
from PySide6.QtCore import Qt, QRect                                 # noqa: E402
from PySide6.QtGui import QFontMetrics, QImage, QPainter, QColor     # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                  # noqa: E402

OUT = Path(__file__).resolve().parent
FAILS: "list[str]" = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def quiet_show(w):
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    w.move(-4000, -4000)
    w.show()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    try:
        from render_display_rows import app_stylesheet
        css = app_stylesheet()
        if css:
            app.setStyleSheet(css)
    except Exception as e:
        print("no app stylesheet:", e)

    v = is_t.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v.resize(1500, 950)
    quiet_show(v)
    for c in v.findChildren(QWidget):
        if hasattr(c, "set_expanded"):
            c.set_expanded(True)
    app.processEvents()

    btns = [v.btn_prev, v.btn_next, v.btn_play, v.btn_stop,
            v.btn_prev_ts, v.btn_save_ts, v.btn_next_ts]
    for b in btns:
        fm = QFontMetrics(b.font())
        need = (b.iconSize().width() if not b.icon().isNull() else 0) \
            + (fm.horizontalAdvance(b.text()) + 4 if b.text() else 0) + 10
        check(f"{b.toolTip().splitlines()[0][:40]!r} fits", need <= b.width(),
              f"needs {need}, has {b.width()}")
    box = v.btn_play.parentWidget()
    right = max(w.geometry().right() for w in (v.speed_cb, v.btn_next_ts))
    check("nothing sticks out of the panel", right <= box.width(),
          f"right edge {right} of {box.width()}")

    top = v.btn_prev.mapTo(box, v.btn_prev.rect().topLeft()).y() - 6
    bot = v.lbl_ts_status.mapTo(box, v.lbl_ts_status.rect().bottomLeft()).y() + 6
    r = QRect(0, max(0, top), box.width(), bot - top)
    off = box.grab(r)
    for b in btns + [v.speed_cb, v.btn_goto_ts, v.btn_clear_ts]:
        b.setEnabled(True)
    app.processEvents()
    on = box.grab(r)
    pic = QImage(r.width(), r.height() * 2 + 8, QImage.Format.Format_ARGB32)
    pic.fill(QColor("#ffffff"))
    p = QPainter(pic)
    p.drawPixmap(0, 0, on)
    p.drawPixmap(0, r.height() + 8, off)
    p.end()
    pic.save(str(OUT / "timeline_row.png"))
    print("  wrote timeline_row.png")

    # Pointing progress bar.
    v.pointing_progress.setRange(0, 100)
    v.pointing_progress.setValue(42)
    v.pointing_progress.setFormat("Camera 2/3 · PAP1DF — %p %")
    v.pointing_progress.setVisible(True)
    app.processEvents()
    v.pointing_progress.grab().save(str(OUT / "pointing_progress.png"))
    print("  wrote pointing_progress.png")

    # PV overlay without / with the dot.
    host = QWidget()
    host.resize(420, 230)
    host.setStyleSheet("background: #303030;")
    ov = is_t._PvOverlayPanel(host)
    rows = [("SBW4", "12.95 J"), ("GDD", "-1240 fs2"), ("Back_Ref", "0.87 J")]
    ov.move(8, 8)
    ov.update_values(rows)
    ov2 = is_t._PvOverlayPanel(host)
    ov2.move(8, 118)
    ov2.update_values(rows, states={"GDD": is_t.PV_STATE_HELD})
    quiet_show(host)
    app.processEvents()
    check("overlay the same size with a dot", ov.size() == ov2.size(),
          f"{ov.width()}x{ov.height()} vs {ov2.width()}x{ov2.height()}")
    host.grab().save(str(OUT / "pv_overlay_dot.png"))
    print("  wrote pv_overlay_dot.png")

    # Marks with number tags and the rotate knob.
    img = QImage(320, 200, QImage.Format.Format_ARGB32)
    img.fill(QColor(20, 20, 20))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    is_t.draw_marks(p, QRect(0, 0, 320, 200),
                    {"cross": [(0.25, 0.6), (0.7, 0.6, 30.0)],
                     "circle": [], "square": []},
                    dict(is_t.MARK_STYLE_DEFAULTS), draw_mode="cross",
                    selected=("cross", 1), handle_radius=6, numbers=True)
    p.end()
    img.save(str(OUT / "mark_tags.png"))
    print("  wrote mark_tags.png")

    print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILURE(S)")
    app.quit()
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
