"""Laser settings — the board the five squares are arranged on.

    python testing/test_hall_layout_drag.py

Headless and silent: the `offscreen` platform, nothing resized on screen and no
settings file touched. Nothing is clicked on the real desktop either — the
board's own `mousePressEvent` / `mouseMoveEvent` / `mouseReleaseEvent` are
called with events built in code, which is how a Qt widget is driven from a test
here.

What it pins:

  * a square moves where it is dragged, and the rectangles land in the settings
    document as fractions;
  * the first drag takes the arrangement OFF automatic, and "Arrange them
    again" puts it back;
  * an edge SNAPS to a neighbour's edge, and waggling the pointer frees it;
  * dragging an edge resizes and leaves the opposite edge where it was;
  * a few pixels of tremor while picking a square is not a drag;
  * nothing can be dragged out of the band or shrunk to nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def close(what, got, want, tol=0.004):
    if abs(float(got) - float(want)) > tol:
        FAILED.append(f"{what}: got {got!r}, wanted about {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted about {want!r}")
    else:
        print(f"  ok    {what}")


# One square per tenth of the band, a tenth of a band apart: every edge is on a
# round number, so what should snap and what should not can be stated exactly.
_LAYOUT = {"Int": [0.00, 0.0, 0.10, 1.0],
           "E2":  [0.20, 0.0, 0.10, 1.0],
           "E3":  [0.40, 0.0, 0.10, 1.0],
           "E4":  [0.60, 0.0, 0.10, 1.0],
           "E5":  [0.80, 0.0, 0.10, 1.0]}


def _press(board, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, y),
                     QPointF(x, y), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    board.mousePressEvent(ev)


def _move(board, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(x, y), QPointF(x, y),
                     Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    board.mouseMoveEvent(ev)


def _release(board, x, y):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(x, y),
                     QPointF(x, y), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    board.mouseReleaseEvent(ev)


def _board(hand=True):
    """A board 808 x 101 modelling a real band of 800 x 100, and its halls.

    The model and the thing modelled are deliberately the same shape, so one
    board pixel is one real pixel and every number in this file can be read
    either way round.
    """
    import ls_core as C
    from ft_t import _LayoutBoard
    halls = C.halls_of({"halls": {"auto": not hand, "band_px": 100,
                                  "show_word": True, "word_px": 11,
                                  "layout": dict(_LAYOUT) if hand else {}}})
    board = _LayoutBoard(halls, band_px=(800, 100))
    board.resize(808, 101)
    board.reload()
    return board, halls


def test_a_square_goes_where_it_is_dragged():
    print("a square goes where it is dragged, and lands in the settings")
    board, halls = _board()
    b = board._board()
    w = b.width()
    # The middle of Int, and 0.14 of the band to the right. 0.14 is deliberately
    # not near any edge: 0.20 is 0.06 away, which is three times the snap.
    start = board._tile_rect(0).center()
    _press(board, start.x(), start.y())
    _move(board, start.x() + 0.14 * w, start.y())
    close("it moved by what the pointer moved", board.tiles()[0][0], 0.14)
    close("and not vertically", board.tiles()[0][1], 0.0)
    check("it is off automatic now", halls["auto"], False)
    close("and the settings document has the rectangle",
          halls["layout"]["Int"][0], 0.14)
    _release(board, start.x() + 0.14 * w, start.y())
    check("the drag is over", board._drag_idx, -1)


def test_an_edge_snaps_to_its_neighbour():
    print("an edge snaps to a neighbour's edge")
    board, _halls = _board()
    b = board._board()
    w = b.width()
    start = board._tile_rect(0).center()
    _press(board, start.x(), start.y())
    # Int's left edge to 0.19, a hundredth short of E2's left edge at 0.20.
    _move(board, start.x() + 0.19 * w, start.y())
    check("it landed exactly on the neighbour's edge",
          round(board.tiles()[0][0], 6), 0.2)
    # Two guides, not one: the square's top is already on the band's top edge,
    # so that snaps as well and gets its own line.
    check("and the guide line is drawn", board._guides,
          [("v", 0.2), ("h", 0.0)])
    _release(board, start.x() + 0.19 * w, start.y())

    print("and waggling the pointer lets the snapping go")
    board, _halls = _board()
    start = board._tile_rect(0).center()
    _press(board, start.x(), start.y())
    x = start.x() + 0.19 * w
    # Back and forth on the spot: more direction reversals than SHAKE_FLIPS
    # over less than SHAKE_TRAVEL_FRAC of travel. Two pixels each way, not
    # three — at three the eight recorded moves add up to 48 px of travel
    # against a 47.5 px threshold on this board, which is a test balanced on a
    # knife edge rather than a test of shaking.
    for k in range(board.SHAKE_WINDOW + 2):
        _move(board, x + (2 if k % 2 else -2), start.y())
    check("the snapping is off for the rest of this drag",
          board._snap_off, True)
    check("so the square is where the pointer is, not on the edge",
          round(board.tiles()[0][0], 6) == 0.2, False)
    _release(board, x, start.y())


def test_an_edge_resizes_and_the_other_stays():
    print("dragging an edge resizes and leaves the opposite edge alone")
    board, halls = _board()
    b = board._board()
    w = b.width()
    r = board._tile_rect(2)                  # E3, at 0.40 .. 0.50
    _press(board, r.right() - 2, r.center().y())
    check("the right edge was grabbed", board._drag_mode, "right")
    # 0.07 to the right: the right edge would land on 0.57, which is nowhere
    # near 0.60 or the band's middle.
    _move(board, r.right() - 2 + 0.07 * w, r.center().y())
    close("it got wider", board.tiles()[2][2], 0.17)
    close("and the left edge did not move", board.tiles()[2][0], 0.40)
    _release(board, r.right() - 2 + 0.07 * w, r.center().y())

    print("and the left edge is grabbed on the left")
    r = board._tile_rect(4)                  # E5, at 0.80 .. 0.90
    _press(board, r.left() + 2, r.center().y())
    check("the left edge was grabbed", board._drag_mode, "left")
    _move(board, r.left() + 2 - 0.07 * w, r.center().y())
    close("it got wider to the left", board.tiles()[4][0], 0.73)
    close("and its right edge stayed", board.tiles()[4][0]
          + board.tiles()[4][2], 0.90)


def test_a_tremor_is_not_a_drag():
    print("a few pixels of tremor while picking a square is not a drag")
    board, halls = _board(hand=False)
    before = board.tiles()
    start = board._tile_rect(0).center()
    _press(board, start.x(), start.y())
    _move(board, start.x() + 2, start.y() + 2)
    check("nothing moved", board.tiles(), before)
    check("and it is still automatic", halls["auto"], True)
    _release(board, start.x() + 2, start.y() + 2)


def test_nothing_leaves_the_band():
    print("nothing can be dragged out of the band")
    board, _halls = _board()
    b = board._board()
    w = b.width()
    start = board._tile_rect(4).center()     # E5, the right-hand one
    _press(board, start.x(), start.y())
    _move(board, start.x() + 4 * w, start.y() + 4 * b.height())
    t = board.tiles()[4]
    check("its right edge is the band's right edge", round(t[0] + t[2], 6), 1.0)
    check("and its bottom the band's bottom", round(t[1] + t[3], 6), 1.0)
    _release(board, start.x(), start.y())

    print("and a square cannot be shrunk to nothing")
    import ls_core as C
    r = board._tile_rect(0)
    _press(board, r.right() - 2, r.center().y())
    _move(board, r.left() - 10 * w, r.center().y())
    check("it keeps a width it can be grabbed by",
          round(board.tiles()[0][2], 6), round(C.TILE_MIN_FRAC, 6))


def test_arrange_them_again():
    print("Arrange them again goes back to the even row of five")
    board, halls = _board()
    check("by hand to begin with", halls["auto"], False)
    seen = []
    board.changed.connect(lambda: seen.append(1))
    board.arrange_again()
    check("back on automatic", halls["auto"], True)
    check("and the hand-made rectangles are gone", halls["layout"], {})
    check("the tab was told", len(seen), 1)
    widths = {round(t[2], 6) for t in board.tiles()}
    check("five squares of one width", len(widths), 1)


def test_a_rebuild_mid_drag_is_ignored():
    print("the board is not re-read from the file under the operator's hand")
    # Every drag asks the window to refresh every tab, this one included, and
    # that calls `reload()`. Re-reading the document in the middle of a drag
    # would snap the square back to where it was when the press landed.
    board, halls = _board()
    start = board._tile_rect(0).center()
    _press(board, start.x(), start.y())
    _move(board, start.x() + 0.14 * board._board().width(), start.y())
    moved = board.tiles()
    halls["layout"]["Int"] = [0.0, 0.0, 0.10, 1.0]
    board.reload()
    check("the drag is still in charge", board.tiles(), moved)
    _release(board, start.x(), start.y())
    board.reload()
    check("and once it is let go the file wins again",
          board.tiles()[0], [0.0, 0.0, 0.10, 1.0])


def main():
    from PySide6.QtWidgets import QApplication

    import ls_ui as U
    app = QApplication.instance() or QApplication(sys.argv)
    U.install_app_look(app)

    for test in (test_a_square_goes_where_it_is_dragged,
                 test_an_edge_snaps_to_its_neighbour,
                 test_an_edge_resizes_and_the_other_stays,
                 test_a_tremor_is_not_a_drag,
                 test_nothing_leaves_the_band,
                 test_arrange_them_again,
                 test_a_rebuild_mid_drag_is_ignored):
        test()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
