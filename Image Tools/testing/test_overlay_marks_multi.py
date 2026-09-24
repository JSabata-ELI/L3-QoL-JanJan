"""Several reference marks on one camera (Image Slider).

A camera used to carry exactly one cross, one circle and one square, and a second
click simply moved the first. It now carries as many of each as the operator draws,
and one is taken away by clicking it and pressing Delete.

What is pinned here:

  1. Every click adds a mark. A click on an existing one picks it out instead, and
     Ctrl+click adds one on top of it.
  2. Delete takes away the mark that is picked out — only that one — and only while
     that shape's Draw button is armed. Every other key is handed on, or the arrow
     keys would stop stepping through the frames the moment a picture was clicked.
  3. The marks survive being carried between the one-camera and the several-camera
     views, and the two sides do not share the lists.
  4. Ticking a box with nothing drawn is not a mark: the save must not write an
     "annotated" copy identical to the original.
  5. Every saved picture is drawn by the routine the screen uses, so all the marks
     are in the exported file, not just one.

Runs headless, no share and no archiver:

    python testing/test_overlay_marks_multi.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import (QFocusEvent, QImage, QKeyEvent, QMouseEvent, QPainter,
                           QPixmap)
from PySide6.QtWidgets import QApplication

import is_t


def _grey_pixmap(w: int, h: int, level: int = 40) -> QPixmap:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0xFF000000 | (level << 16) | (level << 8) | level)
    return QPixmap.fromImage(img)


def _press(iv, pt, ctrl=False):
    mods = (Qt.KeyboardModifier.ControlModifier if ctrl
            else Qt.KeyboardModifier.NoModifier)
    iv.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(pt), Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton, mods))


def _release(iv, pt):
    iv.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(pt), Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _drag(iv, a, b):
    _press(iv, a)
    iv.mouseMoveEvent(QMouseEvent(
        QEvent.Type.MouseMove, QPointF(b), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    _release(iv, b)


def _click(iv, pt, ctrl=False):
    _press(iv, pt, ctrl=ctrl)
    _release(iv, pt)


def _key(iv, key):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    iv.keyPressEvent(ev)
    return ev


def _fresh_view(w=400, h=300):
    iv = is_t.ImageView()
    iv.resize(w, h)
    iv.set_pixmap(_grey_pixmap(w, h))
    return iv


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ── 1. every click adds a cross ───────────────────────────────────────────
    print("Every click adds a cross; clicking one picks it out")
    iv = _fresh_view()
    ir = iv._img_rect()
    check(ir is not None and ir.width() > 0, "the view has an image rect")
    iv.show_cross = True
    iv.set_draw_mode("cross")

    pts = [QPoint(ir.left() + 40, ir.top() + 40),
           QPoint(ir.left() + 140, ir.top() + 60),
           QPoint(ir.left() + 240, ir.top() + 90)]
    for pt in pts:
        _click(iv, pt)
    check(len(iv.marks["cross"]) == 3,
          f"three clicks made three crosses ({len(iv.marks['cross'])})")
    check(iv._sel == ("cross", 2), f"the last one is picked out ({iv._sel})")

    # A click on one of them picks it out, and adds nothing.
    _click(iv, pts[0])
    check(len(iv.marks["cross"]) == 3, "clicking an existing cross added nothing")
    check(iv._sel == ("cross", 0), f"and picked that one out ({iv._sel})")

    # Ctrl+click on the same spot adds one anyway.
    _click(iv, pts[0], ctrl=True)
    check(len(iv.marks["cross"]) == 4, "Ctrl+click added a fourth cross")
    check(iv._sel == ("cross", 3), "the new one is the one picked out")

    # Two marks on top of each other: the one drawn last is the one hit.
    iv.clear_marks()
    iv.add_mark("cross", (0.30, 0.30))
    iv.add_mark("cross", (0.31, 0.30))
    hit = iv._hit_mark("cross", QPointF(ir.left() + 0.305 * ir.width(),
                                        ir.top() + 0.30 * ir.height()), ir)
    check(hit == 1, f"the mark on top is the one picked out ({hit})")

    # ── 2. Delete ─────────────────────────────────────────────────────────────
    print("Delete takes away the mark that is picked out")
    iv = _fresh_view()
    ir = iv._img_rect()
    iv.show_cross = True
    iv.set_draw_mode("cross")
    for x in (40, 140, 240):
        _click(iv, QPoint(ir.left() + x, ir.top() + 50))
    iv._sel = ("cross", 1)
    ev = _key(iv, Qt.Key.Key_Delete)
    check(len(iv.marks["cross"]) == 2, "Delete took one cross away")
    check(all(abs(g[0] * ir.width() - 140) > 5 for g in iv.marks["cross"]),
          "and it was the one that was picked out")
    check(ev.isAccepted(), "the key was used up")

    # The wrong mode must not delete: in circle mode a picked-out cross is safe.
    iv.set_draw_mode("circle")
    iv._sel = ("cross", 0)
    _key(iv, Qt.Key.Key_Delete)
    check(len(iv.marks["cross"]) == 2,
          "Delete in circle mode leaves the crosses alone")

    # Nothing picked out: the key must travel on, or the arrow keys would die too.
    iv.set_draw_mode("cross")
    iv._sel = None
    ev = _key(iv, Qt.Key.Key_Delete)
    check(not ev.isAccepted(), "Delete with nothing picked out is handed on")
    ev = _key(iv, Qt.Key.Key_Left)
    check(not ev.isAccepted(), "the arrow keys are handed on, so frames still step")

    # Walking away from the picture drops the highlight — Delete only reaches the
    # view that has the keyboard, so a lit mark elsewhere would promise nothing.
    iv._sel = ("cross", 0)
    iv.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    check(iv._sel is None, "leaving the picture drops the highlight")

    # ── 3. circles and squares ────────────────────────────────────────────────
    print("Circles and squares add up the same way")
    iv = _fresh_view()
    ir = iv._img_rect()
    iv.show_circle = True
    iv.set_draw_mode("circle")
    _drag(iv, QPoint(ir.left() + 80, ir.top() + 80),
              QPoint(ir.left() + 120, ir.top() + 110))
    _drag(iv, QPoint(ir.left() + 250, ir.top() + 150),
              QPoint(ir.left() + 290, ir.top() + 180))
    check(len(iv.marks["circle"]) == 2, "two drags made two ellipses")
    first = iv.marks["circle"][0]
    # Dragging the second one must not have touched the first.
    _drag(iv, QPoint(ir.left() + 250, ir.top() + 150),
              QPoint(ir.left() + 300, ir.top() + 200))
    check(iv.marks["circle"][0] == first, "the first ellipse stayed where it was")

    # A click that never moves still leaves a mark — the rule is the same for all
    # three shapes — and it comes out big enough to grab.
    n_before = len(iv.marks["circle"])
    _click(iv, QPoint(ir.left() + 40, ir.top() + 200))
    check(len(iv.marks["circle"]) == n_before + 1,
          "a plain click in circle mode still leaves a mark")
    g = iv.marks["circle"][-1]
    check(g[2] > 0.01 and g[3] > 0.01, f"and it has a usable size ({g[2]:.3f})")

    iv.show_square = True
    iv.set_draw_mode("square")
    _drag(iv, QPoint(ir.left() + 60, ir.top() + 60),
              QPoint(ir.left() + 100, ir.top() + 100))
    _drag(iv, QPoint(ir.left() + 200, ir.top() + 60),
              QPoint(ir.left() + 240, ir.top() + 100))
    check(len(iv.marks["square"]) == 2, "two drags made two rectangles")
    check(len(iv.marks["circle"]) == n_before + 1,
          "and left the ellipses alone")

    # ── 4. carried between the two views, never shared ────────────────────────
    print("The marks survive the hand-off and the two sides keep their own")
    src = _fresh_view()
    src.show_cross = True
    src.add_mark("cross", (0.1, 0.2))
    src.add_mark("cross", (0.3, 0.4))
    state = is_t.MultiCameraGrid._save_iv_overlay(src)
    dst = _fresh_view()
    is_t.MultiCameraGrid._restore_iv_overlay(dst, state)
    check(dst.marks["cross"] == [(0.1, 0.2), (0.3, 0.4)],
          "both crosses came across")
    check(dst.show_cross, "and so did the tick box")
    src.add_mark("cross", (0.9, 0.9))
    check(len(dst.marks["cross"]) == 2,
          "a later change on one side does not reach the other")
    check(dst.marks["cross"] is not state["marks"]["cross"],
          "the store keeps a list of its own")

    # Copying one kind to another camera leaves the other kinds alone.
    a, b = _fresh_view(), _fresh_view()
    a.show_cross = a.show_circle = True
    a.add_mark("cross", (0.2, 0.2))
    a.add_mark("cross", (0.8, 0.8))
    b.show_circle = True
    b.add_mark("circle", (0.5, 0.5, 0.1, 0.1))
    is_t.Viewer._copy_overlay_shape(a, b, "cross")
    check(b.marks["cross"] == [(0.2, 0.2), (0.8, 0.8)],
          "both crosses were copied to the other camera")
    check(b.marks["circle"] == [(0.5, 0.5, 0.1, 0.1)],
          "its own circle was left alone")

    # ── 5. a ticked box with nothing drawn is not a mark ──────────────────────
    print("A ticked box with nothing drawn is not a mark")
    empty = _fresh_view()
    empty.show_cross = empty.show_circle = empty.show_square = True
    check(not is_t.has_any_mark(is_t.visible_marks(empty)),
          "nothing drawn = nothing to burn in")
    empty.add_mark("cross", (0.5, 0.5))
    check(is_t.has_any_mark(is_t.visible_marks(empty)), "one cross = something to burn in")
    empty.show_cross = False
    check(not is_t.has_any_mark(is_t.visible_marks(empty)),
          "a hidden kind does not count")

    # ── 6. every mark reaches the saved picture ───────────────────────────────
    print("Every mark reaches the saved picture")
    view = _fresh_view()
    view.show_cross = view.show_square = True
    for x in (0.2, 0.5, 0.8):
        view.add_mark("cross", (x, 0.5))
    view.add_mark("square", (0.05, 0.05, 0.15, 0.15))
    style = {"cross_color": is_t.QColor(0, 255, 0), "cross_thick": 2, "cross_size": 10,
             "circle_color": is_t.QColor(255, 255, 0), "circle_thick": 2,
             "square_color": is_t.QColor(0, 200, 255), "square_thick": 2}
    params = is_t.overlay_params_from_view(view, style)
    check([len(params["marks"][k]) for k in is_t.MARK_KINDS] == [3, 0, 1],
          f"the save carries all of them: "
          f"{[len(params['marks'][k]) for k in is_t.MARK_KINDS]}")
    view.add_mark("cross", (0.9, 0.9))
    check(len(params["marks"]["cross"]) == 3,
          "a mark added afterwards cannot change a save already under way")

    pix = _grey_pixmap(400, 300)
    painter = QPainter(pix)
    is_t.draw_marks(painter, QRect(0, 0, 400, 300), params["marks"], params)
    painter.end()
    img = pix.toImage()
    lit = [img.pixelColor(int(x * 400), 150).green() > 150 for x in (0.2, 0.5, 0.8)]
    check(all(lit), f"all three crosses are in the picture: {lit}")

    # And a ticked-but-empty shape burns nothing at all.
    blank = _grey_pixmap(80, 60)
    before = blank.toImage()
    painter = QPainter(blank)
    is_t.draw_marks(painter, QRect(0, 0, 80, 60), is_t.new_marks(), style)
    painter.end()
    check(blank.toImage() == before, "nothing drawn leaves the picture untouched")

    print()
    if fails:
        print(f"{len(fails)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
