"""Assert a mark drawn on one frame lands on all of them, at the same point.

Comparing beam position across days means putting a circle round the beam on one
frame and asking whether the others sit inside it. Drawing that circle ten times
by hand, ten times slightly differently, answers nothing — so one drawn mark is
mirrored onto every frame of the wall.

It is not a new geometry problem: a mark has always been stored as FRACTIONS of
the frame it is drawn on (a circle is a centre and two radii between 0 and 1), so
"the same point on every frame" is copying those numbers across. What that means
is worth being exact about, and the tooltip says it: over many days of ONE camera
it is the same sensor pixel; across cameras of different shape it is the same
RELATIVE point, not the same distance.

The mirroring happens on every step of the drag, not on release, so the mark grows
on all the tiles at once under the hand.

Runs offscreen, no images, no share, no network:

    python testing/test_wall_marks.py
"""
import importlib.util
import sys
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
N = 6


def make_wall(m):
    """Six frames of two different shapes — so "the same relative point" is being
    tested, not "the same pixel on identical frames"."""
    import numpy as np
    from pathlib import Path
    wall = m._DayWall()
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    day0 = date(2026, 8, 10)
    cells = []
    for i in range(N):
        p = Path(f"mark_{i}.png")
        h, w = (120, 160) if i % 2 == 0 else (200, 150)
        wall._shared.raw[p] = (np.zeros((h, w), dtype=np.float32), 4095.0)
        cells.append({"day": day0 + timedelta(days=i), "cam": "CAM",
                      "cam_folder": "CAM", "path": p, "ts_ns": 1,
                      "status": "found", "meta": {}})
    wall.set_cells(cells)
    wall._rects = []
    wall._relayout()
    # The image rectangles are worked out while painting, and the drag needs them.
    wall.grab()
    return wall


def drag(wall, idx: int, frm, to, shift: bool = False):
    """The real drag path — the same calls a mouse would make."""
    from PySide6.QtCore import QPointF
    wall._begin_overlay(idx, QPointF(*frm))
    wall._drag_start = QPointF(*frm)
    wall._drag_idx = idx
    wall._drag_overlay(QPointF(*to), shift)


def frac_of(wall, idx: int, pt):
    """Where a point on tile `idx` sits as a fraction of that tile's picture."""
    ir = wall._img_rect_at(idx)
    return ((pt[0] - ir.left()) / ir.width(), (pt[1] - ir.top()) / ir.height())


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    print("\n=== a circle drawn on one frame appears on all of them ===")
    wall = make_wall(m)
    wall.set_draw_mode("circle")
    ir = wall._img_rect_at(0)
    centre = (ir.center().x(), ir.center().y())
    edge = (ir.center().x() + ir.width() // 5, ir.center().y() + ir.height() // 5)
    drag(wall, 0, centre, edge)

    ovs = {c["path"]: wall._shared.ov.get(c["path"]) for c in wall.cells()}
    check("every frame carries a circle",
          all(o and "circle" in o for o in ovs.values()),
          f"{sum(1 for o in ovs.values() if o and 'circle' in o)} of {N}")
    src = ovs[wall.cells()[0]["path"]]["circle"]
    same = all(
        abs(o["circle"][k] - src[k]) < 1e-9
        for o in ovs.values() for k in ("x", "y", "rx", "ry"))
    check("at exactly the same relative point", same,
          f"first {[round(src[k], 4) for k in ('x', 'y', 'rx', 'ry')]}")
    # The frames are two different shapes, so the same fraction is a DIFFERENT
    # number of pixels on each — which is what "the same relative point" means.
    check("the circle really is centred where the drag started",
          abs(src["x"] - frac_of(wall, 0, centre)[0]) < 0.01
          and abs(src["y"] - frac_of(wall, 0, centre)[1]) < 0.01,
          f"{src['x']:.3f},{src['y']:.3f} vs "
          f"{frac_of(wall, 0, centre)[0]:.3f},{frac_of(wall, 0, centre)[1]:.3f}")

    print("\n=== dragging a handle on ANY tile moves them all ===")
    before = dict(wall._shared.ov[wall.cells()[3]["path"]]["circle"])
    ir3 = wall._img_rect_at(3)
    wall._drag_handle = "move"
    drag(wall, 3, (ir3.center().x(), ir3.center().y()),
         (ir3.center().x() + 20, ir3.center().y()))
    wall._drag_handle = "move"
    wall._drag_start = None
    after0 = wall._shared.ov[wall.cells()[0]["path"]]["circle"]
    after3 = wall._shared.ov[wall.cells()[3]["path"]]["circle"]
    check("the tile that was dragged moved", after3["x"] != before["x"],
          f"{before['x']:.3f} -> {after3['x']:.3f}")
    check("and the first tile followed it",
          abs(after0["x"] - after3["x"]) < 1e-9,
          f"{after0['x']:.3f} vs {after3['x']:.3f}")

    print("\n=== the mark grows on all tiles DURING the drag ===")
    wall = make_wall(m)
    wall.set_draw_mode("circle")
    from PySide6.QtCore import QPointF
    ir = wall._img_rect_at(0)
    c0 = (ir.center().x(), ir.center().y())
    wall._begin_overlay(0, QPointF(*c0))
    wall._drag_start, wall._drag_idx = QPointF(*c0), 0
    radii = []
    for step in (10, 25, 45):
        wall._drag_overlay(QPointF(c0[0] + step, c0[1] + step), False)
        last = wall._shared.ov[wall.cells()[N - 1]["path"]]["circle"]
        radii.append(round(last["rx"], 4))
    check("the LAST tile's circle grows on every step",
          all(b > a for a, b in zip(radii, radii[1:])), str(radii))

    print("\n=== undo takes it off every frame in one step ===")
    check("something is marked", len(wall._shared.ov) == N,
          f"{len(wall._shared.ov)} of {N}")
    ok = wall._shared.pop_undo()
    wall.refresh_edits()
    check("one undo is enough", ok and not any(wall._shared.ov.values()),
          f"{sum(1 for o in wall._shared.ov.values() if o)} frames still marked")

    print("\n=== clearing one clears all, while they are linked ===")
    wall = make_wall(m)
    wall.set_draw_mode("cross")
    ir = wall._img_rect_at(2)
    drag(wall, 2, (ir.center().x(), ir.center().y()),
         (ir.center().x(), ir.center().y()))
    check("a cross went on every frame",
          all(wall._shared.ov.get(c["path"], {}).get("cross")
              for c in wall.cells()))
    wall.clear_overlays([wall.cells()[2]["path"]])
    check("clearing one frame cleared them all",
          not any(wall._shared.ov.values()),
          f"{sum(1 for o in wall._shared.ov.values() if o)} still marked")

    print("\n=== unlinked, a mark stays on its own frame ===")
    wall = make_wall(m)
    wall._shared.link_marks = False
    wall.set_draw_mode("square")
    ir = wall._img_rect_at(1)
    drag(wall, 1, (ir.left() + 10, ir.top() + 10),
         (ir.left() + 60, ir.top() + 50))
    marked = [i for i, c in enumerate(wall.cells())
              if wall._shared.ov.get(c["path"], {}).get("square")]
    check("only the frame drawn on is marked", marked == [1], str(marked))

    wall.clear_overlays([wall.cells()[1]["path"]])
    check("and clearing it clears only that one",
          not any(wall._shared.ov.values()))

    print("\n=== the switch is on to begin with ===")
    fresh = m._WallShared()
    check("linked by default", fresh.link_marks is True)

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
