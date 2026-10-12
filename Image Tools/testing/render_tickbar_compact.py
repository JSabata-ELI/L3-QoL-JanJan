"""LOOK at the slider + time axis block with real fonts (compact axis, 6 Oct 2026).

    QT_QPA_PLATFORM=windows python testing/render_tickbar_compact.py

Writes beside this file tickbar_compact.png: from top to bottom
  1. one camera, discrete axis (few frames: one tick per frame)
  2. one camera, normal clock axis with Set From / Set To marks
  3. three cameras (per-camera sliders), normal axis
  4. a two-day pick (bands, window dates)
Each strip is cut from the slider's top edge to the axis' bottom edge, so the gap
between them is what is seen. The window is placed off the desktop and shown without
activating, so it never takes the keyboard.
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_is_tickbar_")

import bench_common as B                                             # noqa: E402
import is_t                                                          # noqa: E402
from PySide6.QtCore import Qt, QRect, QPoint                         # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor, QFontMetrics     # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                  # noqa: E402

OUT = Path(__file__).resolve().parent
FAILS: "list[str]" = []
H = 3600 * 1_000_000_000


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def quiet_show(w):
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    w.move(-4000, -4000)
    w.show()


def pump(app, s):
    end = time.monotonic() + s
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def strip(v, app):
    """Slider top → axis bottom, in the right panel's coordinates."""
    pump(app, 0.4)
    top_w = (v._per_cam_scroll if v._per_cam_scroll.isVisible() else v.slider)
    host = v.tickbar.parentWidget()
    top = top_w.mapTo(host, QPoint(0, 0)).y()
    bot = v.tickbar.mapTo(host, QPoint(0, v.tickbar.height())).y()
    gap = v.tickbar.mapTo(host, QPoint(0, 0)).y() - (top_w.mapTo(host, QPoint(0, top_w.height())).y())
    return host.grab(QRect(0, top, host.width(), bot - top)), gap


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
    tmp = Path(tempfile.mkdtemp(prefix="is_tickbar_"))
    shots = []
    try:
        v = is_t.Viewer()
        try:
            v._diag_timer.stop()
        except Exception:
            pass
        v.resize(1400, 900)
        quiet_show(v)
        pump(app, 0.3)

        names, lists = B.make_synthetic_set(tmp / "one", 1, 12, hz=0.2)
        folder = lists[0][0]
        ts = sorted(t for p in folder.glob("*.png") if (t := is_t.parse_unix_ns_from_name(p)))
        v._ts_windows = None
        v._start_scan([folder], axis_override=(ts[0], ts[-1]), folder_label=str(folder))
        B.wait_for(lambda: bool(v.ts_list), 60.0)
        pump(app, 0.5)
        lh = QFontMetrics(v.tickbar.font()).height()
        check("axis is as tall as its rows", v.tickbar.height() == 2 * lh + 15,
              f"{v.tickbar.height()} px, label line {lh} px")
        pic, gap = strip(v, app)
        check("no gap between slider and axis (one camera)", gap <= 0, f"{gap} px")
        shots.append(("one camera, discrete axis", pic))

        tb = v.tickbar
        tb.discrete_ticks = None
        tb.set_axis(ts[0] - H // 2, ts[0] + H)
        tb.set_marks(ts[0] - H // 4, ts[0] + H // 3)
        tb.set_cursor(ts[0] + H // 2)
        shots.append(("one camera, clock axis, marks", strip(v, app)[0]))

        tb.set_segments([(ts[0] - H, ts[0] + 2 * H), (ts[0] + 24 * H, ts[0] + 27 * H)])
        tb.set_axis(ts[0] - H, ts[0] + 27 * H)
        tb.set_marks(None, None)
        shots.append(("two-day pick", strip(v, app)[0]))
        tb.set_segments(None)

        names3, lists3 = B.make_synthetic_set(tmp / "three", 3, 40, hz=0.2)
        all_ts = sorted(t for l in lists3 for p in l[0].glob("*.png")
                        if (t := is_t.parse_unix_ns_from_name(p)))
        v._start_multi_cam_scan(names3, lists3, (all_ts[0] - H, all_ts[-1] + H), False, None)
        B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 60.0)
        pump(app, 0.8)
        v.tickbar.discrete_ticks = None
        v.tickbar.update()
        pic, gap = strip(v, app)
        check("no gap between sliders and axis (three cameras)", gap <= 1, f"{gap} px")
        shots.append(("three cameras", pic))

        wmax = max(p.width() for _, p in shots)
        htot = sum(p.height() + 26 for _, p in shots)
        img = QImage(wmax, htot, QImage.Format.Format_ARGB32)
        img.fill(QColor("#ffffff"))
        p = QPainter(img)
        y = 0
        for label, pm in shots:
            p.setPen(QColor("#a00000"))
            p.drawText(6, y + 17, label)
            y += 24
            p.drawPixmap(0, y, pm)
            y += pm.height() + 2
        p.end()
        img.save(str(OUT / "tickbar_compact.png"))
        print("  wrote tickbar_compact.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILURE(S)")
    app.quit()
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
