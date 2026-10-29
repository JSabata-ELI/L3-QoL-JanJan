"""Draw icon.ico for Extractor. Run it again to change the artwork.

    python testing/make_icon.py
    python testing/make_icon.py --preview      also writes icon_preview.png

DRAFT (15.10.2026): the program had no icon, so its exe showed the generic
one. Extractor is a console program, so the picture only lands on the .exe
file (the Builder passes icon.ico as --icon). The drawing is the support zip:
a box with an arrow coming out of it.

The house style, which every program in this repo shares: THE DRAWING INSIDE A
WHITE DISC WITH A BLACK RIM, transparent outside the rim. Every frame is
composed at its own size eight times over and brought down with LANCZOS; the
rim is 6 % of the frame. Same recipe as Laser settings/testing/make_icon.py.
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

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
OVER = 8

INK = "#000000"
DISC = "#ffffff"
BOX = "#c48a3a"             # the cardboard box
BOX_EDGE = "#6b4618"
ARROW = "#2e7d32"           # what comes out of it

RIM_FRAC = 0.06
FIT = 0.88


def _disc(p, px):
    """The white disc with its black rim. Returns the usable inner radius."""
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
    half_diag = ((unit_w / 2.0) ** 2 + (unit_h / 2.0) ** 2) ** 0.5
    return (fit * inner) / max(0.001, half_diag)


def _draw(p, px, inner, small):
    """A box in the lower half, a green arrow rising out of it; 1 x 1.1 box."""
    uw, uh = 1.0, 1.1
    s = _fit(inner, uw, uh, FIT)
    x0 = px / 2.0 - uw * s / 2.0
    y0 = px / 2.0 - uh * s / 2.0

    def pt(u, v):
        return QPointF(x0 + u * s, y0 + v * s)

    edge = QPen(QColor(BOX_EDGE))
    edge.setWidthF(max(OVER * 1.0, 0.07 * s))
    edge.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(edge)
    p.setBrush(QColor(BOX))
    p.drawRect(QRectF(pt(0.0, 0.55), pt(1.0, 1.1)))

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ARROW))
    shaft = 0.30 if small else 0.24
    p.drawPolygon(QPolygonF([
        pt(0.5, 0.0), pt(0.92, 0.42), pt(0.5 + shaft / 2, 0.42),
        pt(0.5 + shaft / 2, 0.80), pt(0.5 - shaft / 2, 0.80),
        pt(0.5 - shaft / 2, 0.42), pt(0.08, 0.42),
    ]))


def draw(px):
    big = px * OVER
    img = QImage(big, big, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inner = _disc(p, big)
        _draw(p, big, inner, small=px <= 32)
    finally:
        p.end()
    return _down(img, px)


def _down(img, px):
    from PIL import Image
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    src = Image.open(io.BytesIO(bytes(data))).convert("RGBA")
    return src.resize((px, px), Image.LANCZOS)


def write_preview(frames):
    """Every frame blown up with no smoothing, side by side."""
    from PIL import Image, ImageDraw
    shown = [16, 20, 24, 32, 48, 64, 128, 256]
    cell, pad, head = 272, 10, 22
    width = pad + len(shown) * (cell + pad)
    sheet = Image.new("RGBA", (width, head + cell + 2 * pad), (235, 235, 235, 255))
    d = ImageDraw.Draw(sheet)
    for i, px in enumerate(shown):
        frame = frames[SIZES.index(px)]
        k = max(1, cell // px)
        big = frame.resize((px * k, px * k), Image.NEAREST)
        x = pad + i * (cell + pad)
        d.text((x, 4), f"{px} px  (x{k})", fill=(20, 20, 20, 255))
        sheet.alpha_composite(big, (x + (cell - big.width) // 2, head + pad))
    sheet.convert("RGB").save(PREVIEW)
    print(f"wrote {PREVIEW.name}  ({sheet.width}x{sheet.height})")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
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
