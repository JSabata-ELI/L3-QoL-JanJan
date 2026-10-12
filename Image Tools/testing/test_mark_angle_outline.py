"""Turned marks and their outline (Image Slider, 01.10.2026).

Crosses, circles and squares can now be turned — with the knob above the picked
mark, or by number in Overlay Settings — and each kind can carry a contrasting
outline. The angle travels as an optional last number of the geometry tuple.

What is pinned here:

  1. The angle round-trips, an unturned mark keeps its short tuple, and angles
     wrap into (-180, 180].
  2. A turned square answers to a click on its TURNED edge, not where its edge
     would be unturned; its grab points turn with it.
  3. Dragging the knob turns the mark counter-clockwise when moved to the left;
     Shift snaps to 15°. Moving or resizing a turned mark keeps its angle.
  4. The outline is drawn beside the line (black pixels next to the colour) and
     not at all when switched off — on screen and in a saved picture alike.
  5. The style survives the trip through slider_ui_state.json.

Runs headless — events are handed straight to the widget, nothing is shown:

    python testing/test_mark_angle_outline.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_angle_"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtCore import QEvent, QPointF, QRect, Qt                 # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ev(w, kind, x, y, mods=Qt.KeyboardModifier.NoModifier):
    local = QPointF(x, y)
    btn = Qt.MouseButton.LeftButton
    buttons = btn if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    return QMouseEvent(kind, local, QPointF(w.mapToGlobal(local.toPoint())),
                       btn, buttons, mods)


def drag(w, x0, y0, x1, y1, mods=Qt.KeyboardModifier.NoModifier):
    w.mousePressEvent(ev(w, QEvent.Type.MouseButtonPress, x0, y0, mods))
    w.mouseMoveEvent(ev(w, QEvent.Type.MouseMove, (x0 + x1) / 2, (y0 + y1) / 2, mods))
    w.mouseMoveEvent(ev(w, QEvent.Type.MouseMove, x1, y1, mods))
    w.mouseReleaseEvent(ev(w, QEvent.Type.MouseButtonRelease, x1, y1, mods))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    print("Angle in the geometry")
    g = is_t.with_angle("square", (0.1, 0.2, 0.3, 0.4), 0.0)
    check("an unturned square keeps four numbers", g == (0.1, 0.2, 0.3, 0.4), str(g))
    g = is_t.with_angle("square", (0.1, 0.2, 0.3, 0.4), 30.0)
    check("a turned one carries a fifth", len(g) == 5 and is_t.mark_angle("square", g) == 30.0)
    check("the shape alone comes back", is_t.mark_core("square", g) == (0.1, 0.2, 0.3, 0.4))
    check("270° wraps to -90°",
          is_t.mark_angle("cross", is_t.with_angle("cross", (0.5, 0.5), 270)) == -90.0)
    check("an old unturned cross reads 0°", is_t.mark_angle("cross", (0.5, 0.5)) == 0.0)

    print("A turned square is hit on its turned edge")
    rect = QRect(0, 0, 400, 400)
    sq0 = (0.25, 0.40, 0.75, 0.60)            # 200 wide, 80 tall, centre (200, 200)
    sq90 = is_t.with_angle("square", sq0, 90.0)
    # Unturned, the top edge runs through (200, 160). Turned 90°, the long sides
    # are vertical: x = 160 and x = 240, from y = 100 to 300.
    check("unturned: hit on the top edge",
          is_t.mark_hit(rect, "square", sq0, QPointF(300, 160)))
    check("turned: no hit where the top edge used to be",
          not is_t.mark_hit(rect, "square", sq90, QPointF(300, 160)))
    check("turned: hit on the turned long side",
          is_t.mark_hit(rect, "square", sq90, QPointF(160, 120)))
    h = is_t.mark_handles(rect, "square", sq90)
    check("turned 90° ccw: the knob is to the LEFT of the centre",
          h["rot"].x() < 200 - 30 and abs(h["rot"].y() - 200) < 1,
          f"({h['rot'].x():.1f}, {h['rot'].y():.1f})")

    print("Turning with the knob")
    iv = is_t.ImageView()
    iv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    iv.resize(400, 400)
    pm = QPixmap(400, 400); pm.fill(QColor(40, 40, 40))
    iv.set_pixmap(pm)
    iv.show(); app.processEvents()
    ir = iv._img_rect()
    iv.show_square = True
    iv.add_mark("square", sq0)
    iv.set_draw_mode("square")
    # Arming only selects the last mark on the view that has the keyboard (2 Oct 2026),
    # and this never-shown view can have none — pick the square as a click would.
    iv._sel = ("square", 0)
    knob = iv._sel_handles(ir)["rot"]
    cx, cy = is_t._mark_centre_px(ir, "square", sq0)
    drag(iv, knob.x(), knob.y(), cx - 100, cy)        # to the left of the centre
    a = is_t.mark_angle("square", iv.marks["square"][0])
    check("knob dragged to the left = +90°", abs(a - 90.0) < 0.5, f"{a:.2f}")
    knob = iv._sel_handles(ir)["rot"]
    drag(iv, knob.x(), knob.y(), cx - 100, cy - 40, Qt.KeyboardModifier.ShiftModifier)
    a = is_t.mark_angle("square", iv.marks["square"][0])
    check("Shift snaps to 15°", abs(a / 15.0 - round(a / 15.0)) < 1e-6, f"{a:.2f}")
    before = is_t.mark_angle("square", iv.marks["square"][0])
    mv = iv._sel_handles(ir)["move"]
    drag(iv, mv.x(), mv.y(), mv.x() + 20, mv.y() + 10)
    check("moving keeps the angle",
          is_t.mark_angle("square", iv.marks["square"][0]) == before)
    e = iv._sel_handles(ir)["e"]
    drag(iv, e.x(), e.y(), e.x() + 5, e.y())
    check("resizing keeps the angle",
          is_t.mark_angle("square", iv.marks["square"][0]) == before)

    iv.show_cross = True
    iv.add_mark("cross", (0.5, 0.5))
    iv.set_draw_mode("cross")
    iv._sel = ("cross", len(iv.marks["cross"]) - 1)      # see the square above
    knob = iv._sel_handles(ir)["rot"]
    drag(iv, knob.x(), knob.y(), 200 + 100, 200 - 100)   # up-right = -45°
    a = is_t.mark_angle("cross", iv.marks["cross"][-1])
    check("a cross turns too (up-right = -45°)", abs(a + 45.0) < 0.5, f"{a:.2f}")

    print("Outline")
    def burn(style_outline: bool) -> QImage:
        img = QImage(200, 200, QImage.Format.Format_ARGB32)
        img.fill(QColor(128, 128, 128))
        p = QPainter(img)
        st = dict(is_t.MARK_STYLE_DEFAULTS)
        st["square_outline"] = style_outline
        st["square_outline_w"] = 2
        is_t.draw_marks(p, QRect(0, 0, 200, 200),
                        {"cross": [], "circle": [], "square": [(0.25, 0.25, 0.75, 0.75)]},
                        st)
        p.end()
        return img
    on, off = burn(True), burn(False)
    # The left edge is drawn at x = 50, 2 px wide; the outline lies just outside it.
    def dark(img, x, y):
        c = img.pixelColor(x, y)
        return c.red() < 40 and c.green() < 40 and c.blue() < 40
    check("outline on: dark pixels beside the line",
          dark(on, 47, 100) or dark(on, 48, 100), f"{on.pixelColor(47, 100).name()}")
    check("outline off: nothing dark beside the line",
          not dark(off, 47, 100) and not dark(off, 48, 100))
    c = on.pixelColor(50, 100)
    check("the line keeps its own colour in the middle",
          c.green() > 150 and c.blue() > 150, c.name())

    print("Style survives slider_ui_state.json")
    st = dict(is_t.MARK_STYLE_DEFAULTS)
    st["circle_outline"] = False
    st["circle_outline_color"] = QColor(255, 0, 0, 200)
    st["cross_outline_w"] = 3
    back = is_t.mark_style_from_json(is_t.mark_style_to_json(st))
    check("outline switch", back["circle_outline"] is False)
    check("outline colour with its alpha",
          back["circle_outline_color"].rgba() == QColor(255, 0, 0, 200).rgba())
    check("outline width", back["cross_outline_w"] == 3)
    check("broken input keeps the defaults",
          is_t.mark_style_from_json({"cross_thick": "x"})["cross_thick"] == 2)

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
