"""Assert the wall lays frames out the way a comparison needs.

Three rules, and they pull against each other:

  * THE SAME SIZE AS EACH OTHER. One cell size for the whole wall. The free-form
    partition this replaces made the biggest frame the biggest tile, so the day
    worth looking at was whichever day happened to be widest — which is exactly
    the judgement a comparison view must not make for you.
  * AS LARGE AS THEY GO. The size is still maximised against the pane, so TWO
    frames come out bigger than three. "Same size" does not mean "a fixed size".
  * AT MOST FOUR ACROSS, then a new row. Past four a row of frames stops being a
    comparison and becomes a strip of thumbnails.

And the tiles must tile the pane EDGE TO EDGE — the slack goes inside a tile,
never into gaps between them, so a wall of frames reads as a wall.

Ctrl+wheel grows them in place: the pane then scrolls, and only DOWNWARDS. A wall
that scrolls sideways cannot be read at all.

Runs offscreen, no images, no share, no network:

    python testing/test_wall_grid.py
"""
import importlib.util
import sys
import types
from datetime import date, timedelta

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_finder():
    B.load_slider()
    if "image_finder" in sys.modules:
        return sys.modules["image_finder"]
    argv, sys.argv = sys.argv, ["if_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_finder", str(B.HERE / "if_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_finder"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


PANE_W, PANE_H = 1200, 800


def make_wall(m, n: int, aspect: float = 4 / 3.0, pane=(PANE_W, PANE_H)):
    """A wall of `n` cells whose frames all have the given aspect ratio.

    No files: the wall reads aspects out of the shared frame cache, so the cache is
    seeded directly with arrays of the right shape."""
    import numpy as np
    from pathlib import Path
    wall = m._DayWall()
    wall.resize(pane[0], pane[1])
    # The pane STATES its size; the wall never infers it. In the app _WallScroll
    # does this from its viewport on every resize.
    wall.set_canvas(pane[0], pane[1])
    h = 120
    w = int(round(h * aspect))
    day0 = date(2026, 8, 10)
    cells = []
    for i in range(n):
        p = Path(f"fake_{aspect:.2f}_{i}.png")
        wall._shared.raw[p] = (np.zeros((h, w), dtype=np.float32), 4095.0)
        cells.append({"day": day0 + timedelta(days=i), "cam": "CAM",
                      "cam_folder": "CAM", "path": p, "ts_ns": 1,
                      "status": "found", "meta": {}})
    wall.set_cells(cells)
    # set_cells laid out against whatever size the widget had at the time; do it
    # again now that the host and size are known.
    wall._rects = []
    wall._relayout()
    return wall


def sizes(wall):
    return [(r.width(), r.height()) for r in wall._rects]


def cols_of(wall):
    """How many tiles sit on the top row."""
    if not wall._rects:
        return 0
    y0 = wall._rects[0].y()
    return sum(1 for r in wall._rects if r.y() == y0)


def pic_area(wall, i: int) -> float:
    """The area the PICTURE gets inside tile `i` — the thing being maximised. The
    caption strip and the margins are not part of it."""
    r = wall._rects[i]
    gap, cap = wall._GAP, wall._CAPTION_H
    aw = max(0, r.width() - 2 * gap)
    ah = max(0, r.height() - cap - 2 * gap)
    a = 4 / 3.0
    entry = wall._shared.raw.get(wall._cells[i].get("path"))
    if entry is not None:
        hh, ww = entry[0].shape[:2]
        a = ww / hh
    w = min(aw, ah * a)
    return w * (w / a)


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    print("\n=== every tile the same size ===")
    for n in (1, 2, 3, 4, 5, 9):
        wall = make_wall(m, n)
        s = sizes(wall)
        # One pixel of difference is the edge-rounding that keeps neighbours sharing
        # a pixel; anything more is two different sizes on one wall.
        spread_w = max(x for x, _ in s) - min(x for x, _ in s)
        spread_h = max(y for _, y in s) - min(y for _, y in s)
        check(f"{n} frame(s): one size for all",
              spread_w <= 1 and spread_h <= 1,
              f"width spread {spread_w} px, height spread {spread_h} px")

    print("\n=== at most four across ===")
    for n in (1, 2, 3, 4, 5, 8, 9, 13):
        wall = make_wall(m, n)
        c = cols_of(wall)
        check(f"{n} frame(s): {c} across", c <= wall._MAX_COLS and c >= 1,
              f"{c} columns")

    print("\n=== the size is still maximised ===")
    areas = {n: pic_area(make_wall(m, n), 0) for n in (1, 2, 3, 4, 5, 9, 13)}
    for n, a in areas.items():
        print(f"    {n:>2} frame(s): {a:>9.0f} px² each, "
              f"{cols_of(make_wall(m, n))} across")
    check("two frames are bigger than three", areas[2] > areas[3] * 1.05,
          f"{areas[2]:.0f} vs {areas[3]:.0f} px²")
    check("one frame is the biggest of all", areas[1] > areas[2] * 1.05,
          f"{areas[1]:.0f} vs {areas[2]:.0f} px²")
    # Three and four tie on purpose. Both land on two-by-two, and on this pane that
    # really is the largest the smallest frame can be: three in a row would make
    # every one of them SMALLER (each limited by a third of the width) than three
    # in a two-by-two with one slot empty. Maximising means maximising, not
    # rewarding a smaller count with a worse arrangement.
    check("more frames are never bigger",
          all(areas[a] >= areas[b] - 1
              for a, b in zip(sorted(areas), sorted(areas)[1:])),
          str({n: round(a) for n, a in areas.items()}))
    check("and the tiles do shrink as the count climbs",
          areas[1] > areas[3] > areas[5] > areas[13],
          f"{areas[1]:.0f} > {areas[3]:.0f} > {areas[5]:.0f} > {areas[13]:.0f}")

    # Four tall frames belong in one row; four wide ones do not. This is the part a
    # fixed 2x2 grid would get wrong.
    tall = make_wall(m, 4, aspect=0.4)
    wide = make_wall(m, 4, aspect=3.0)
    check("four portrait frames go side by side", cols_of(tall) == 4,
          f"{cols_of(tall)} columns")
    check("four wide frames stack instead", cols_of(wide) < 4,
          f"{cols_of(wide)} columns")

    print("\n=== the tiles tile the pane, with no gaps ===")
    wall = make_wall(m, 5)
    rects = wall._rects
    c = cols_of(wall)
    top = [r for r in rects if r.y() == rects[0].y()]
    top.sort(key=lambda r: r.x())
    check("the first tile starts at the left edge", top[0].x() == 0,
          f"x={top[0].x()}")
    check("the last tile ends at the right edge",
          top[-1].x() + top[-1].width() == wall.width(),
          f"{top[-1].x() + top[-1].width()} vs {wall.width()}")
    touching = all(top[i].x() + top[i].width() == top[i + 1].x()
                   for i in range(len(top) - 1))
    check("neighbours share a pixel — no gap, no overlap", touching,
          str([(r.x(), r.width()) for r in top]))
    rows_y = sorted({r.y() for r in rects})
    check("the rows touch too",
          all(rects[0].height() * 0 + (rows_y[i] + top[0].height()) == rows_y[i + 1]
              for i in range(len(rows_y) - 1)),
          str(rows_y))

    print("\n=== fitted means fitted: no scroll bar at 100 % ===")
    for n in (1, 2, 3, 5, 9):
        wall = make_wall(m, n)
        bottom = max(r.y() + r.height() for r in wall._rects)
        check(f"{n} frame(s): the wall fits the pane exactly",
              wall.minimumHeight() == 0 and abs(bottom - PANE_H) <= 2,
              f"minimum height {wall.minimumHeight()}, bottom {bottom} of {PANE_H}")

    print("\n=== Ctrl+wheel grows them, downwards only ===")
    wall = make_wall(m, 6)
    before = sizes(wall)[0]
    check("zoom starts at 100 %", abs(wall.zoom() - 1.0) < 1e-6)
    changed = wall.zoom_by_notches(4)
    after = sizes(wall)[0]
    check("four notches change something", changed)
    check("the tiles got bigger", after[0] > before[0],
          f"{before[0]} -> {after[0]} px wide")
    check("the wall got taller, so the pane scrolls",
          wall.minimumHeight() > PANE_H,
          f"{wall.minimumHeight()} vs pane {PANE_H}")
    right = max(r.x() + r.width() for r in wall._rects)
    check("but never wider — no sideways scrolling",
          right <= wall.width() and wall.minimumWidth() == 0,
          f"{right} vs {wall.width()}, minimum width {wall.minimumWidth()}")
    check("still at most four across", cols_of(wall) <= wall._MAX_COLS,
          f"{cols_of(wall)} columns")
    s = sizes(wall)
    check("and still all one size",
          max(x for x, _ in s) - min(x for x, _ in s) <= 1)

    # EVERY notch must change the size of the frames. Snapping the cell up to fill
    # the row exactly gave five notches in a row that did nothing at all.
    wall.reset_zoom()
    widths, colcounts, lefts, rights = [], [], [], []
    for _ in range(10):
        wall.zoom_by_notches(1)
        widths.append(sizes(wall)[0][0])
        colcounts.append(cols_of(wall))
        lefts.append(min(r.x() for r in wall._rects))
        rights.append(max(r.x() + r.width() for r in wall._rects))
    print(f"    widths  {widths}")
    print(f"    columns {colcounts}")
    check("every notch changes the size",
          all(b > a for a, b in zip(widths, widths[1:])), str(widths))
    check("the column count only ever steps down",
          all(a >= b for a, b in zip(colcounts, colcounts[1:])), str(colcounts))
    # Whatever width is left over goes evenly on both sides, so the row reads as
    # centred rather than shoved against the left edge with a black gutter.
    slack = [(PANE_W - (r - l), l) for l, r in zip(lefts, rights)
             if r - l <= PANE_W]
    check("the row is centred in the pane",
          all(abs(total - 2 * left) <= 2 for total, left in slack), str(slack))

    check("Fit puts it back", wall.reset_zoom() and abs(wall.zoom() - 1.0) < 1e-6)
    check("and the wall fits the pane again", wall.minimumHeight() == 0,
          f"minimum height {wall.minimumHeight()}")

    wall.zoom_by_notches(-40)
    check("zooming out stops at the floor",
          abs(wall.zoom() - wall._ZOOM_MIN) < 1e-6, f"{wall.zoom():.2f}")
    wall.zoom_by_notches(400)
    check("zooming in stops at the ceiling",
          abs(wall.zoom() - wall._ZOOM_MAX) < 1e-6, f"{wall.zoom():.2f}")

    print("\n=== the wheel only zooms with Ctrl held ===")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    sc = m._WallScroll()
    got = []
    sc.zoomed.connect(got.append)

    def wheel(mods):
        return QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                           QPoint(0, 120), Qt.MouseButton.NoButton, mods,
                           Qt.ScrollPhase.NoScrollPhase, False)

    sc.wheelEvent(wheel(Qt.KeyboardModifier.ControlModifier))
    check("Ctrl+wheel asks for a zoom", got == [1], str(got))
    got.clear()
    sc.wheelEvent(wheel(Qt.KeyboardModifier.NoModifier))
    check("a plain wheel does not", got == [], str(got))

    print("\n=== the day-by-day wall is left alone ===")
    wall = make_wall(m, 6)
    wall.set_layout_mode("rows")
    wall._rects = []
    wall._relayout()
    check("rows mode still asks for the height its rows need",
          wall.minimumHeight() > 0, f"{wall.minimumHeight()}")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
