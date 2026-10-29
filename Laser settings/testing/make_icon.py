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
  * the drawing is ONE lit hall with a white tick, at every size. Until
    14.10.2026 the frames from 64 px up drew five halls with the beam dropping
    into the middle one, and only the small frames had the single square. The
    taskbar and the title bar show the small frames, so the single square is
    the icon everybody knows — and the big five-hall drawing on the title slide
    of the guide read as "the wrong icon". One drawing everywhere since.
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

# The farthest ink, as a fraction of the inner radius. The square's four corners
# are all ink, so this keeps a white ring showing between them and the rim at
# 16 px.
FIT_SMALL = 0.86


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
# The drawing
# ─────────────────────────────────────────────────────────────────────────────
def _draw_small(p, px, inner):
    """One lit hall with a white tick — the hall is set right."""
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
        _draw_small(p, big, inner)
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
