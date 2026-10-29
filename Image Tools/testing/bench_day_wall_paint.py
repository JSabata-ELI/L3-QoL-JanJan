"""How long the Day-by-day wall takes to draw its tiles — thinned frames (now) against
every pixel (before 13.10.2026). Offscreen, synthetic frames, no share:

    python testing/bench_day_wall_paint.py
"""
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_day_wall import load_finder

N_CAMS, N_DAYS = 4, 10
PANE_W, PANE_H = 1800, 1000


def main() -> int:
    import numpy as np
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QPainter, QColor
    QApplication.instance() or QApplication(sys.argv)
    rng = np.random.default_rng(0)
    wall = m._DayWall()
    wall.set_layout_mode("cols")
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    cells = []
    for d in range(N_DAYS):
        for c in range(N_CAMS):
            p = Path(f"bench_{d}_{c}.png")
            shape = (2048, 1024) if c == 0 else (1024, 1280)
            wall._shared.raw[p] = ((rng.random(shape, dtype=np.float32) * 4000), 4095.0)
            cells.append({"day": date(2026, 9, 1) + timedelta(days=d),
                          "cam": f"CAM{c}", "cam_folder": f"CAM{c}", "path": p,
                          "ts_ns": 1, "status": "found", "meta": {}})
    wall.set_cells(cells)
    wall._relayout()
    wall.set_display("Gradient", 0, None, 0, 0)

    def render_all() -> float:
        wall._pix.clear()
        wall._render_key = None
        img = QImage(max(PANE_W, wall.minimumWidth()), wall.minimumHeight(),
                     QImage.Format.Format_RGB888)
        img.fill(QColor("#000000"))
        p = QPainter(img)
        t0 = time.perf_counter()
        wall._paint_into(p, wall._rects)
        dt = time.perf_counter() - t0
        p.end()
        return dt

    now = min(render_all() for _ in range(2))
    real = wall._thin_step
    wall._thin_step = lambda *a: 1
    before = min(render_all() for _ in range(2))
    wall._thin_step = real
    print(f"{N_CAMS} cameras x {N_DAYS} days, every tile rendered:")
    print(f"  every pixel (before): {before * 1000:7.0f} ms")
    print(f"  thinned (now):        {now * 1000:7.0f} ms   ({before / max(now, 1e-9):.1f}x faster)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
