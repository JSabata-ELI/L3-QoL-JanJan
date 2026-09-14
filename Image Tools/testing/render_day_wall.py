"""Render the Day-by-day wall so it can be LOOKED at.

Offscreen has no fonts and lies about text size, so run this with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_day_wall.py

Five cameras over five days, no share and no network — the frames are generated.
What to check in `day_wall.png`:

  * ONE ROW PER CAMERA, and the grey banner over each row names that camera and
    says how many days its line reaches across
  * the days run left to right, FOUR of them filling the pane, and the widget is
    wider than the pane so the fifth is reached by the sideways bar
  * the caption under each tile names the DAY and the time, not the camera — the
    banner already said which camera it is
  * three rows fit the pane, so the vertical bar steps whole cameras
"""
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder            # noqa: E402

PANE_W, PANE_H = 1240, 720
CAMS = ["C03-035-PTM11wNF", "C03-039-PAM10NF", "C03-040-PFM13NF",
        "C03-041-PASF1NF", "C03-051-WRT2DPNF"]
DAY0 = date(2026, 9, 1)


def ns_at(day, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def frame(seed: int):
    """A square beam with a bit of structure, so a tile is not a flat block."""
    import numpy as np
    h, w = 1024, 1280
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2, w / 2
    r = np.hypot((yy - cy) / (h * 0.33), (xx - cx) / (h * 0.33))
    arr = np.exp(-(r ** 6)) * (3200 + 400 * np.sin(r * 9 + seed))
    return arr.astype("float32"), 4095.0


def main() -> int:
    m = load_finder()
    from pathlib import Path
    from PySide6.QtWidgets import QApplication, QScrollArea

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    wall = m._DayWall()
    wall.set_layout_mode("rows")
    cells = []
    for d in range(5):
        day = DAY0 + timedelta(days=d)
        for c, cam in enumerate(CAMS):
            p = Path(f"wall_{d}_{c}.png")
            wall._shared.raw[p] = frame(d * 5 + c)
            cells.append({"day": day, "cam": cam, "cam_folder": cam + "-_-IMG",
                          "path": p, "ts_ns": ns_at(day, 9 + c, 17 * d % 60),
                          "status": "found", "meta": {"source": "pv"}})
    wall.set_cells(cells)

    # In the real tab the wall lives in a _WallScroll, which is what tells it the
    # pane size and what raises the two scroll bars.
    sc = m._WallScroll()
    sc.setWidgetResizable(True)
    sc.setWidget(wall)
    wall._scroll_host = sc
    sc.resize(PANE_W, PANE_H)
    sc.show()
    app.processEvents()
    wall._relayout()
    app.processEvents()

    rows, cols = wall._row_order()
    print(f"{len(rows)} row(s) = cameras: {rows}")
    print(f"{len(cols)} column(s) = (day, pick): {[str(k[0]) for k in cols]}")
    print(f"row pitch {wall.row_pitch()} px in a {PANE_H} px pane"
          f"  -> about {PANE_H / max(1, wall.row_pitch()):.1f} rows in view")
    print(f"column width {wall.row_col_width():.0f} px"
          f"  -> {PANE_W / max(1.0, wall.row_col_width()):.1f} days in view")
    print(f"widget minimum {wall.minimumWidth()} x {wall.minimumHeight()} px")
    print("sideways bar:", sc.horizontalScrollBar().maximum() > 0)
    print("vertical bar:", sc.verticalScrollBar().maximum() > 0)
    for hr, txt in wall._row_heads:
        print("  banner:", txt)
    print("  caption:", wall._caption(wall.cells()[0]))

    out = Path(__file__).with_name("day_wall.png")
    sc.grab().save(str(out))
    print("written:", out)

    # And the same wall run down to its last camera, so the bottom row is judged too.
    vb = sc.verticalScrollBar()
    vb.setValue(vb.maximum())
    app.processEvents()
    out2 = Path(__file__).with_name("day_wall_bottom.png")
    sc.grab().save(str(out2))
    print("written:", out2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
