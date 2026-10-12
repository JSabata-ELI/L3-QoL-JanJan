"""Draw icon.ico for Laser settings. Run it again to change the artwork.

    python testing/make_icon.py
    python testing/make_icon.py --preview      also writes icon_preview.png

The house style, which every program in this repo shares: THE DRAWING INSIDE A
WHITE DISC WITH A BLACK RIM, transparent outside the rim. Said for Image Tools
on 25.9.2026 and again on 30.9.2026 — a fix to one icon is a rule for all of
them.

Three things make it sharp rather than soft, and all three are needed:

  * every frame is composed AT ITS OWN SIZE, eight times over, and brought down
    with Pillow's LANCZOS. Qt's own antialiasing at 8x plus one good filter is
    what gives a clean 16 px edge; resampling one big bitmap is what made the
    taskbar icons blurry in the first place;
  * the rim is 6 % of the frame and the drawing is scaled so its farthest ink
    sits at `FIT` of the inner radius, so the white gap between the drawing and
    the rim is the same at every size;
  * below `SMALL_BELOW` the drawing is SIMPLER, not smaller. Five halls at
    24 px merge into one grey band; one lit hall with a tick still says "this
    hall is set right", which is what the program is about. The taskbar and the
    title bar are both under that line, so the simple one is the one that is
    actually seen.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "icon.ico"
PREVIEW = HERE / "icon_preview.png"

# Every size the Windows shell picks from. 256 is what the big tiles use, 16
# and 20 are the taskbar and the title bar at 100 %.
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# Eight times, then LANCZOS down. Four is visibly worse on the 16 px frame.
OVER = 8

INK = "#000000"             # the rim, and the line work
DISC = "#ffffff"            # the disc the drawing sits in
GREEN = "#2e9e3f"           # the lit hall — the Overview's own green
GREEN_EDGE = "#1d6b29"

RIM_FRAC = 0.06             # the rim's width, as a fraction of the frame

# The farthest ink, as a fraction of the inner radius. Two numbers, because the
# two drawings put their ink in different places: the wide one's farthest ink is
# the bottom corners of the end halls, with the top corners of its box empty, so
# it can be pushed out further. The simple one IS a square and its four corners
# are all ink, so it needs the lower value to keep a white ring showing at 16 px.
FIT_FULL = 0.95
FIT_SMALL = 0.86

# Below this the five halls cannot be told apart. Measured by blowing every
# frame up: at 24 px each hall would be three pixels wide.
SMALL_BELOW = 64


# ─────────────────────────────────────────────────────────────────────────────
# The disc
# ─────────────────────────────────────────────────────────────────────────────
def _disc(p, px):
    """The white disc with its black rim. Returns the usable inner radius.

    The rim's pen is centred on its circle, so the circle is pulled in by half
    the pen and the rim's OUTER edge lands exactly on the frame's own circle.
    Without that the rim is cut flat on four sides at the small sizes.
    """
    margin = max(1.0, px * 0.015)
    outer = px / 2.0 - margin
    rim = max(1.6, px * RIM_FRAC)
    radius = outer - rim / 2.0
    pen = QPen(QColor(INK))
    pen.setWidthF(rim)
    p.setPen(pen)
    p.setBrush(QColor(DISC))
    c = px / 2.0
    p.drawEllipse(QRectF(c - radius, c - radius, 2 * radius, 2 * radius))
    return max(2.0, radius - rim / 2.0)


def _fit(inner, unit_w, unit_h, fit):
    """Units-to-pixels for a drawing `unit_w` x `unit_h` inside the disc.

    The corners of the drawing's own box are what touch the circle, so the
    scale comes off the half-DIAGONAL. Fitting the width alone puts the corners
    of a tall drawing through the rim.
    """
    half_diag = ((unit_w / 2.0) ** 2 + (unit_h / 2.0) ** 2) ** 0.5
    return (fit * inner) / max(0.001, half_diag)


# ─────────────────────────────────────────────────────────────────────────────
# The two drawings
# ─────────────────────────────────────────────────────────────────────────────
# The full picture, in its own units: the beam along the top, an arrow dropping
# into the middle hall, and the five halls in a row underneath.
_CELL, _GAP = 1.0, 0.26
_ROW_W = 5 * _CELL + 4 * _GAP           # 6.04
_HALL_Y = 2.30                          # the top of the row of halls
_FULL_W, _FULL_H = _ROW_W, _HALL_Y + _CELL


def _draw_full(p, px, inner):
    """Five halls, the middle one lit, with the beam coming down into it."""
    s = _fit(inner, _FULL_W, _FULL_H, FIT_FULL)
    x0 = px / 2.0 - _FULL_W * s / 2.0
    y0 = px / 2.0 - _FULL_H * s / 2.0

    def ux(u):
        return x0 + u * s

    def uy(u):
        return y0 + u * s

    mid = _ROW_W / 2.0                  # 3.02 — the middle hall's centre line

    # The beam along the top, and the arrow dropping out of it.
    beam = QPen(QColor(INK))
    # In `OVER` units: this is drawn at `OVER` times the frame.
    beam.setWidthF(max(OVER * 1.2, 0.22 * s))
    beam.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(beam)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(ux(0.30), uy(0.13)), QPointF(ux(_ROW_W - 0.30),
                                                    uy(0.13)))
    p.drawLine(QPointF(ux(mid), uy(0.13)), QPointF(ux(mid), uy(1.46)))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(INK))
    p.drawPolygon(QPolygonF([QPointF(ux(mid), uy(2.10)),
                             QPointF(ux(mid - 0.42), uy(1.40)),
                             QPointF(ux(mid + 0.42), uy(1.40))]))

    # The five halls. The middle one is lit, and only it carries a tick.
    for i in range(5):
        lit = (i == 2)
        left = i * (_CELL + _GAP)
        rect = QRectF(ux(left), uy(_HALL_Y), _CELL * s, _CELL * s)
        pen = QPen(QColor(GREEN_EDGE if lit else INK))
        pen.setWidthF(max(OVER * 1.0, (0.17 if lit else 0.15) * s))
        p.setPen(pen)
        p.setBrush(QColor(GREEN) if lit else QColor(DISC))
        p.drawRoundedRect(rect, 0.18 * s, 0.18 * s)
        if lit:
            _tick(p, rect, DISC, max(OVER * 1.2, 0.20 * s))


def _draw_small(p, px, inner):
    """One lit hall with a tick: the same idea with the detail taken out."""
    s = _fit(inner, 1.0, 1.0, FIT_SMALL)
    side = 1.0 * s
    rect = QRectF(px / 2.0 - side / 2.0, px / 2.0 - side / 2.0, side, side)
    # The dark GREEN edge, never black. Measured at 16 px: a black rim on this
    # square comes out 0.8 of a pixel wide, so LANCZOS painted it as a grey bar
    # across the top and the bottom and nothing at all down the sides — the
    # square read as a grey-capped rectangle. A dark green edge that thin simply
    # darkens the green, which is what an edge is for inside a white disc.
    #
    # The minimums are in `OVER` units because this is drawn at `OVER` times the
    # frame: a bare 1.6 here is a fifth of a pixel on the finished icon.
    pen = QPen(QColor(GREEN_EDGE))
    pen.setWidthF(max(OVER * 1.0, 0.09 * s))
    p.setPen(pen)
    p.setBrush(QColor(GREEN))
    p.drawRoundedRect(rect, 0.16 * s, 0.16 * s)
    _tick(p, rect, DISC, max(OVER * 1.6, 0.16 * s))


def _tick(p, rect, colour, width):
    """The tick inside a hall: the Overview's own white check mark."""
    pen = QPen(QColor(colour))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    w, h = rect.width(), rect.height()
    p.drawPolyline(QPolygonF([
        QPointF(rect.left() + 0.24 * w, rect.top() + 0.52 * h),
        QPointF(rect.left() + 0.44 * w, rect.top() + 0.72 * h),
        QPointF(rect.left() + 0.78 * w, rect.top() + 0.28 * h),
    ]))


# ─────────────────────────────────────────────────────────────────────────────
# One frame
# ─────────────────────────────────────────────────────────────────────────────
def draw(px):
    """One frame, composed at `OVER` times its size and brought back down."""
    big = px * OVER
    img = QImage(big, big, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inner = _disc(p, big)
        if px < SMALL_BELOW:
            _draw_small(p, big, inner)
        else:
            _draw_full(p, big, inner)
    finally:
        p.end()
    return _down(img, px)


def _down(img, px):
    """Qt image → PNG bytes → Pillow, LANCZOS to the real size.

    PNG is the one conversion that keeps the alpha channel intact on every Qt
    build, and LANCZOS is what makes the small frames crisp instead of soft.
    """
    from PIL import Image
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    src = Image.open(io.BytesIO(bytes(data))).convert("RGBA")
    return src.resize((px, px), Image.LANCZOS)


# ─────────────────────────────────────────────────────────────────────────────
# Writing it out
# ─────────────────────────────────────────────────────────────────────────────
def write_preview(frames):
    """A contact sheet: every frame blown up with no smoothing, side by side.

    Nearest-neighbour on purpose. The point of the picture is to see the real
    pixels — a smoothed preview hides exactly the blur it is there to catch.
    """
    from PIL import Image, ImageDraw
    shown = [16, 20, 24, 32, 48, 64, 128, 256]
    cell, pad, head = 272, 10, 22
    width = pad + len(shown) * (cell + pad)
    sheet = Image.new("RGBA", (width, head + cell + 2 * pad),
                      (235, 235, 235, 255))
    d = ImageDraw.Draw(sheet)
    for i, px in enumerate(shown):
        frame = frames[SIZES.index(px)]
        k = max(1, cell // px)
        big = frame.resize((px * k, px * k), Image.NEAREST)
        x = pad + i * (cell + pad)
        d.text((x, 4), f"{px} px  (x{k})", fill=(20, 20, 20, 255))
        sheet.alpha_composite(big, (x + (cell - big.width) // 2,
                                    head + pad))
    sheet.convert("RGB").save(PREVIEW)
    print(f"wrote {PREVIEW.name}  ({sheet.width}x{sheet.height})")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from PIL import Image                                   # noqa: F401
    except ImportError:
        print("Pillow is needed to write a multi-size .ico")
        return 1
    frames = [draw(px) for px in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(px, px) for px in SIZES],
                    append_images=frames[:-1])
    print(f"wrote {OUT}  ({', '.join(str(s) for s in SIZES)})")
    if "--preview" in sys.argv:
        write_preview(frames)
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
