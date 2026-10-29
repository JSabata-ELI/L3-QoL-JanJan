"""Render the 12.10.2026 Image Finder changes so they can be LOOKED at.

Runs on the `minimal` platform with real fonts — nothing appears on screen and no
window takes the keyboard focus:

    python testing/render_day_wall_vertical.py [output folder]

Writes:
  vert_2cams.png      Vertical, two cameras over four days: two columns filling
                      the width, newest day on top, a day band per day, the camera
                      plates on top
  vert_8cams.png      Vertical, eight cameras: six across, the rest sideways
  horiz_2cams.png     Horizontal, two cameras: the old row size, centred
  panel.png           the left panel's wall controls (Picture size, Day by day)
  detail.png          the Detailed view: camera + time on top, no info line
  detail_menu.png     the camera list the camera plate opens
"""
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "minimal:enable_fonts"

from test_finder_moment import load_finder            # noqa: E402

PANE_W, PANE_H = 1240, 720
CAMS = ["C03-035-PTM11wNF", "C03-039-PAM10NF", "C03-040-PFM13NF",
        "C03-041-PASF1NF", "C03-051-WRT2DPNF", "C03-052-WRT3NF",
        "C03-053-OM1FF", "C03-054-SBW4NF"]
DAY0 = date(2026, 9, 1)


def ns_at(day, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def frame(seed: int):
    import numpy as np
    h, w = 1024, 1280
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((yy - h / 2) / (h * 0.33), (xx - w / 2) / (h * 0.33))
    arr = np.exp(-(r ** 6)) * (3200 + 400 * np.sin(r * 9 + seed))
    return arr.astype("float32"), 4095.0


def wall_shot(m, app, n_cams: int, n_days: int, mode: str, out: Path,
              tall_first: bool = False):
    wall = m._DayWall()
    wall.set_layout_mode(mode)
    cells = []
    for d in range(n_days):
        day = DAY0 + timedelta(days=d)
        for c, cam in enumerate(CAMS[:n_cams]):
            p = Path(f"vw_{n_cams}_{d}_{c}.png")
            wall._shared.raw[p] = frame(d * 5 + c)
            if tall_first and c == 0:        # the diode-array camera is portrait
                arr, fs = wall._shared.raw[p]
                wall._shared.raw[p] = (arr.T.copy(), fs)
            cells.append({"day": day, "cam": cam, "cam_folder": cam + "-_-IMG",
                          "path": p, "ts_ns": ns_at(day, 9 + c, 17 * d % 60),
                          "status": "found", "meta": {"source": "pv"}})
    wall.set_cells(cells)
    sc = m._WallScroll()
    sc.setWidgetResizable(True)
    sc.setWidget(wall)
    wall._scroll_host = sc
    sc.resize(PANE_W, PANE_H)
    sc.show()
    app.processEvents()
    wall._relayout()
    app.processEvents()
    sc.grab().save(str(out))
    print("written:", out, f"min {wall.minimumWidth()}x{wall.minimumHeight()}")


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    wall_shot(m, app, 2, 4, "cols", out_dir / "vert_2cams.png")
    wall_shot(m, app, 8, 3, "cols", out_dir / "vert_8cams.png")
    wall_shot(m, app, 2, 4, "rows", out_dir / "horiz_2cams.png")
    wall_shot(m, app, 4, 5, "cols", out_dir / "vert_4cams_tall.png", tall_first=True)

    w = m.ImageFinderWidget()
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    box = w._zoom_slider.parentWidget()
    box.grab().save(str(out_dir / "panel.png"))
    print("written: panel.png")
    w._day_strip.setVisible(True)
    w._day_strip.resize(900, w._day_strip.sizeHint().height())
    w._day_strip.grab().save(str(out_dir / "strip.png"))
    print("written: strip.png")

    import numpy as np
    from PIL import Image as PilImage
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="if_render_"))
    paths, names = [], []
    for c, cam in enumerate(CAMS[:3]):
        d = tmp / f"{cam}-_-IMG"
        d.mkdir()
        ts = 1_760_000_000_000_000_000 + c * 1_000_000_000
        p = d / f"{cam}-_-IMG_-_{ts}.png"
        arr, _fs = frame(c)
        PilImage.fromarray(arr.astype(np.uint16)).save(p)
        paths.append(p)
        names.append(cam)
    w._view_tabs.setCurrentIndex(w._detail_tab_idx)
    app.processEvents()
    w._preview_set_files(paths, "", names, index=0)
    import time
    end = time.monotonic() + 10
    while w._detail_view["img"].pixmap().isNull() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    w._detail_page.grab().save(str(out_dir / "detail.png"))
    print("written: detail.png")
    menu = w._detail_view["cam_menu"]
    w._fill_cam_menu(menu)
    from PySide6.QtCore import QPoint
    menu.popup(QPoint(0, 0))           # minimal platform: sized, nothing on screen
    app.processEvents()
    menu.grab().save(str(out_dir / "detail_menu.png"))
    menu.hide()
    print("written: detail_menu.png")
    # Worker threads (the tab's own start-up load) must not hold the exit up.
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
