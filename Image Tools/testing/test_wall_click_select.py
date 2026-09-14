"""A click on a tile MARKS it, AND EVERY CLICK ADDS. It does not open a window.

The wall used to do both in one gesture: a left click selected the frame and
opened the close-up on top of the wall. So every attempt to aim the brightness,
the palette or a rotation at one day threw a window over the very comparison
being made, and the way to mark a tile without that was Ctrl+click — which
nobody guesses.

It then went on clearing the marking before every plain click, so marking a
second picture silently dropped the first and building a set still needed Ctrl.
Now nothing is cleared behind the operator's back: a click adds, a click on
something already marked takes it back off, and empty canvas is what lets
everything go.

Pinned here:
  * plain left click  → ADDS that frame to what is marked, and `tile_clicked`
    is NOT emitted
  * clicking a marked frame again → that one frame is released, the rest stay
  * Ctrl+click        → the same act, kept because it is in everyone's fingers
  * Shift+click       → the whole CAMERA, added to what is already marked
  * empty canvas, Esc, or right click → Unmark every picture → everything is
    released, so "nothing marked = the whole wall" is always reachable (a wall
    whose tiles fill the pane has no empty canvas, hence the other two)
  * double click      → `tile_clicked`, which is what opens the close-up
  * right click       → `tile_context`, the menu (View, Search again, reference)

Runs offscreen, no images, no share, no network:

    python testing/test_wall_click_select.py
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
N = 4


def make_wall(m, cams=("CAM",)):
    """`N` days × `cams`, one frame each."""
    import numpy as np
    from pathlib import Path
    wall = m._DayWall()
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    day0 = date(2026, 8, 10)
    cells = []
    for cam in cams:
        for i in range(N):
            p = Path(f"click_{cam}_{i}.png")
            wall._shared.raw[p] = (np.zeros((120, 160), dtype=np.float32), 4095.0)
            cells.append({"day": day0 + timedelta(days=i), "cam": cam,
                          "cam_folder": cam, "path": p, "ts_ns": 1_000 + i,
                          "status": "found", "meta": {}})
    wall.set_cells(cells)
    wall._relayout()
    return wall


def press(wall, idx: int, button="left", ctrl: bool = False,
          shift: bool = False, double: bool = False):
    """The real event path — a synthesised QMouseEvent through mousePressEvent,
    not a call to the handler's insides."""
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    btn = {"left": Qt.MouseButton.LeftButton,
           "right": Qt.MouseButton.RightButton}[button]
    mods = Qt.KeyboardModifier.NoModifier
    if ctrl:
        mods |= Qt.KeyboardModifier.ControlModifier
    if shift:
        mods |= Qt.KeyboardModifier.ShiftModifier
    pos = QPointF(wall._rects[idx].center())
    kind = (QEvent.Type.MouseButtonDblClick if double
            else QEvent.Type.MouseButtonPress)
    ev = QMouseEvent(kind, pos, wall.mapToGlobal(pos.toPoint()).toPointF(),
                     btn, btn, mods)
    if double:
        wall.mouseDoubleClickEvent(ev)
    else:
        wall.mousePressEvent(ev)


def empty_point(wall):
    """A point on canvas no tile is on, or None — a wall whose tiles fill the pane
    edge to edge has none, which is why Esc and the menu entry exist too."""
    from PySide6.QtCore import QPoint
    for y in range(wall.height() - 1, 0, -5):
        for x in range(wall.width() - 1, 0, -5):
            pt = QPoint(x, y)
            if wall._hit(pt) < 0 and not wall._on_row_head(pt):
                return pt
    return None


def press_empty(wall) -> bool:
    """A left click on canvas no tile is on — the gesture that lets everything go.
    False when this wall leaves no empty canvas to click."""
    from PySide6.QtCore import Qt, QPointF, QEvent
    from PySide6.QtGui import QMouseEvent
    pt = empty_point(wall)
    if pt is None:
        return False
    pos = QPointF(pt)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos,
                     wall.mapToGlobal(pt).toPointF(),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    wall.mousePressEvent(ev)
    return True


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    wall = make_wall(m)
    opened: list = []
    menued: list = []
    wall.tile_clicked.connect(opened.append)
    wall.tile_context.connect(lambda i, _p: menued.append(i))
    paths = [c["path"] for c in wall.cells()]

    print("\n=== a left click marks the tile, and only marks it ===")
    press(wall, 1)
    check("the clicked frame is marked", wall.selected_paths() == {paths[1]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    check("and NO close-up was asked for", opened == [], repr(opened))

    print("\n=== every click ADDS — the first one is not dropped ===")
    press(wall, 2)
    check("both frames are marked now",
          wall.selected_paths() == {paths[1], paths[2]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    press(wall, 0)
    check("and a third click adds a third",
          wall.selected_paths() == {paths[0], paths[1], paths[2]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    check("still no close-up", opened == [], repr(opened))

    print("\n=== clicking a marked frame releases THAT ONE ===")
    press(wall, 2)
    check("only the one clicked was let go",
          wall.selected_paths() == {paths[0], paths[1]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    check("and that took no window either", opened == [], repr(opened))

    print("\n=== and there is a way back to nothing marked ===")
    # A wall of four tiles fills the pane edge to edge, so there is no empty canvas
    # to click — which is exactly why `clear_selection` is also on Esc and in the
    # right-click menu. Both routes end in this one call.
    wide = make_wall(m)
    wide.resize(PANE_W, PANE_H + 400)      # room under the tiles, without relaying out
    press(wide, 0)
    check("something is marked to let go of", len(wide.selected_paths()) == 1)
    check("a click on empty canvas is what does it", press_empty(wide))
    check("and nothing is marked afterwards", wide.selected_paths() == set(),
          repr(sorted(str(p) for p in wide.selected_paths())))
    check("a full wall has no empty canvas, so Esc and the menu carry it",
          empty_point(wall) is None)
    wall.clear_selection()
    check("nothing is marked now — the whole wall is back in scope",
          wall.selected_paths() == set(),
          repr(sorted(str(p) for p in wall.selected_paths())))

    print("\n=== Ctrl+click is the same act, still working ===")
    press(wall, 0)
    press(wall, 1, ctrl=True)
    press(wall, 3, ctrl=True)
    check("three frames marked",
          wall.selected_paths() == {paths[0], paths[1], paths[3]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    press(wall, 1, ctrl=True)
    check("and Ctrl+click on one of them takes it back out",
          wall.selected_paths() == {paths[0], paths[3]},
          repr(sorted(str(p) for p in wall.selected_paths())))
    check("no close-up through any of that", opened == [], repr(opened))

    print("\n=== looking closely is its own gesture ===")
    press(wall, 3, double=True)
    check("a double click asks for the close-up", opened == [3], repr(opened))
    check("and does not disturb what is marked",
          wall.selected_paths() == {paths[0], paths[3]},
          repr(sorted(str(p) for p in wall.selected_paths())))

    press(wall, 0, button="right")
    check("a right click asks for the menu", menued == [0], repr(menued))
    check("the menu offers View, not 'Open close-up'",
          "🔍 View (close-up)" in open(B.HERE / "if_t.py", encoding="utf-8-sig").read())

    print("\n=== Shift marks the whole camera, and adds it ===")
    # The operator asks for a CAMERA, not a picture: "mark that camera and then
    # change things on it". On the Day-by-day wall a camera owns a whole row, so
    # without this, aiming a control at one camera is a click per day.
    two = make_wall(m, cams=("CAM_A", "CAM_B"))
    two.tile_clicked.connect(opened.append)
    a = [c["path"] for c in two.cells() if c["cam"] == "CAM_A"]
    b = [c["path"] for c in two.cells() if c["cam"] == "CAM_B"]
    idx_a0 = [i for i, c in enumerate(two.cells()) if c["cam"] == "CAM_A"][0]
    idx_b0 = [i for i, c in enumerate(two.cells()) if c["cam"] == "CAM_B"][0]
    press(two, idx_a0, shift=True)
    check("every day of that camera is marked", two.selected_paths() == set(a),
          f"{len(two.selected_paths())} of {len(a)}")
    check("and nothing of the other one", not (two.selected_paths() & set(b)))
    press(two, idx_b0, shift=True)
    check("Shift on another camera ADDS it — the first one stays",
          two.selected_paths() == set(a) | set(b),
          f"{len(two.selected_paths())} of {len(a) + len(b)}")
    press(two, idx_a0, shift=True)
    check("and Shift on a camera that is wholly marked takes it back out",
          two.selected_paths() == set(b),
          f"{len(two.selected_paths())} frame(s)")
    # The panel's camera list is the same act, by name rather than by tile.
    check("mark_camera from the panel adds a camera too",
          two.mark_camera("CAM_A") and
          two.selected_paths() == set(a) | set(b),
          f"{len(two.selected_paths())} of {len(a) + len(b)}")
    check("and says so when the camera has nothing on the wall",
          two.mark_camera("NOT_HERE") is False)
    two.clear_selection()
    check("and one act clears the lot", two.selected_paths() == set(),
          f"{len(two.selected_paths())} frame(s)")
    check("and none of that opened a window", opened == [3], repr(opened))

    print("\n=== a draw mode still draws ===")
    # The picture rectangles inside the tiles are worked out while painting, and
    # drawing a mark needs them.
    wall.grab()
    wall.set_draw_mode("cross")
    before = set(wall.selected_paths())
    press(wall, 2)
    check("a click in draw mode marks nothing, it draws",
          wall.selected_paths() == before,
          repr(sorted(str(p) for p in wall.selected_paths())))
    check("and the frame took a mark",
          bool(wall._shared.ov.get(paths[2], {}).get("cross")),
          repr(wall._shared.ov.get(paths[2])))
    wall.set_draw_mode("")

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
