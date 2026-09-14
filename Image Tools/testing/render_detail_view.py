"""Render the Detailed view tab and the wide Day-by-day wall so they can be SEEN.

Offscreen has no fonts and lies about text size, so run this with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_detail_view.py

Writes two files next to this script:

  detail_view.png  — the Detailed view tab: one frame filling the pane, the camera
                     on the left of the caption bar and the time on the right, the
                     scale note under it, and ◀ 3 / 8 ▶ centred at the foot. What
                     to check: DARK INK ON PALE BUTTONS (the row sits under a black
                     picture, where a themed button is invisible), the counter
                     light on the dark page, and nothing clipped.

  rows_wide.png    — Day by day with twelve cameras over three days: four tiles
                     fill the pane and the rest is off to the right behind the
                     sideways bar. What to check: the grey day banner spans the
                     row with the day spelled out at its left, three rows in view,
                     and every caption legible at that tile size.

No network is touched — the frames are written to a temp folder first.
"""
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_day_wall import load_finder            # noqa: E402

CAMS = [f"C03-{40 + i:03d}-CAM{i:02d}FF-_-IMG" for i in range(12)]


def write_frames(root: Path, n_days: int = 3):
    """One frame per camera per day, at three different strengths."""
    import numpy as np
    from PIL import Image as PilImage, PngImagePlugin
    ims = sys.modules["img_scale"]
    cells = []
    for d in range(n_days):
        day = date(2026, 9, 1) + timedelta(days=d)
        for ci, cam in enumerate(CAMS):
            cam_dir = root / cam
            cam_dir.mkdir(parents=True, exist_ok=True)
            yy, xx = np.mgrid[0:1024:2, 0:1280:2]
            peak = 400 + 220 * ((ci + d) % 5)
            counts = np.exp(-(((xx - 640) ** 2 + (yy - 512) ** 2) / 40000.0)) * peak
            bits = ims.bits_from_max_value(peak)
            stored = np.clip(np.rint(counts * ims.SCALE_FACTORS[bits]),
                             0, 65535).astype(np.uint16)
            ts = int(time.mktime(day.timetuple())) * 1_000_000_000 \
                + (8 + ci) * 3_600_000_000_000
            p = cam_dir / f"{cam}_-_{ts}.png"
            meta = PngImagePlugin.PngInfo()
            meta.add_text("MaxValue", str(int(peak)))
            PilImage.fromarray(stored).save(p, pnginfo=meta)
            cells.append({"day": day, "cam": f"CAM{ci:02d}FF", "cam_folder": cam,
                          "path": p, "ts_ns": ts, "status": "found",
                          "meta": {"source": "pv"}})
    return cells


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    tmp = Path(tempfile.mkdtemp(prefix="if_detail_render_"))
    cells = write_frames(tmp)

    w = m.ImageFinderWidget()
    w.resize(1500, 950)
    w.show()

    results = {}
    for c in cells:
        results.setdefault(c["cam_folder"], []).append(
            (c["day"], 8, c["path"], dict(c["meta"]), "found"))
    w.fill_wall(results, None)
    for _ in range(400):
        app.processEvents()
        time.sleep(0.005)

    here = Path(__file__).resolve().parent

    # ── the wide Day-by-day wall ─────────────────────────────────────────────
    day_tab = max(w._wall_pages)
    w._view_tabs.setCurrentIndex(day_tab)
    for _ in range(200):
        app.processEvents()
        time.sleep(0.005)
    wall = w._wall_pages[day_tab]
    out = here / "rows_wide.png"
    w.grab().save(str(out))
    print("rows_wide.png :", out)
    print("  columns laid out :", len({r.x() for r in wall._rects}))
    print("  column width     :", f"{wall.row_col_width():.0f} px "
                                  f"(pane {wall._avail()[0]} px)")
    print("  widget minimum   :", wall.minimumWidth(), "x", wall.minimumHeight())
    print("  rows in view     :", f"{wall._avail()[1] / wall.row_pitch():.1f}")
    print("  banner           :", repr(wall._row_heads[0][1]) if wall._row_heads else "-")
    print("  save-view factor :", f"{wall.export_scale():.1f}x")

    # Scrolled right: the day banner's WORDS have to ride with the viewport, or the
    # row stops saying which day it is the moment the wall is scrolled at all.
    host = wall._scroll_host
    if host is not None:
        bar = host.horizontalScrollBar()
        bar.setValue(bar.maximum() // 2)
        for _ in range(60):
            app.processEvents()
            time.sleep(0.005)
        out = here / "rows_wide_scrolled.png"
        w.grab().save(str(out))
        print("rows_wide_scrolled.png:", out, f"(scrolled {bar.value()} px)")
        bar.setValue(0)
        app.processEvents()

    # ── the Detailed view tab ────────────────────────────────────────────────
    # Mark eight tiles, so the arrows have a set of their own to walk.
    paths = [c["path"] for c in wall.cells()][:8]
    for p in paths:
        wall.set_selected(p, True)
    w._view_tabs.setCurrentIndex(w._detail_tab_idx)
    w._preview_next()
    w._preview_next()
    for _ in range(300):
        app.processEvents()
        time.sleep(0.005)
    out = here / "detail_view.png"
    w.grab().save(str(out))
    print("detail_view.png:", out)
    print("  tab title  :", repr(w._view_tabs.tabText(w._detail_tab_idx)))
    print("  counter    :", repr(w._detail_view["counter"].text()))
    print("  camera     :", repr(w._detail_view["cam"].text()))
    print("  time       :", repr(w._detail_view["ts"].text()))
    pm = w._detail_view["img"].pixmap()
    print("  picture    :", f"{pm.width()}x{pm.height()}" if not pm.isNull() else "EMPTY")
    print("  arrows     :", w._detail_view["prev"].isEnabled(),
          w._detail_view["next"].isEnabled())

    # The arrow keys, sent the way a keyboard sends them: to whatever has the
    # focus. This is the part a direct call to keyPressEvent cannot check — if the
    # focus sat on an arrow BUTTON, Qt's own arrow-key focus navigation would eat
    # the event and the keys would silently do nothing.
    from PySide6.QtCore import QEvent, Qt as _Qt
    from PySide6.QtGui import QKeyEvent
    # `w.focusWidget()` is the widget that HOLDS the focus inside this window,
    # whether or not the window itself is the active one — which a grab-only run
    # is not, so `app.focusWidget()` would be None here and say nothing.
    holder = w.focusWidget()
    print("  focus      :", type(holder).__name__,
          repr(holder.objectName() if holder is not None else ""),
          "= the frame page" if holder is w._detail_page else "NOT the frame page")
    before = w._preview_idx
    tgt = holder or w
    app.sendEvent(tgt, QKeyEvent(QEvent.Type.KeyPress, _Qt.Key.Key_Right,
                                 _Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    print("  Right key  :", before, "->", w._preview_idx,
          "OK" if w._preview_idx != before else "DID NOTHING")

    # And clicking an arrow must not take the focus away from the page, or the
    # keyboard would stop working the moment the mouse was used.
    w._detail_view["next"].click()
    app.processEvents()
    after_click = w._preview_idx
    still = w.focusWidget()
    app.sendEvent(still or w,
                  QKeyEvent(QEvent.Type.KeyPress, _Qt.Key.Key_Right,
                            _Qt.KeyboardModifier.NoModifier))
    app.processEvents()
    print("  after a click on the arrow button, Right key:",
          after_click, "->", w._preview_idx,
          "OK" if w._preview_idx != after_click else "DID NOTHING",
          "| focus still on the page" if still is w._detail_page
          else f"| focus moved to {type(still).__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
