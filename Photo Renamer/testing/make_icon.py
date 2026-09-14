"""
make_icon.py  --  draws Photo Renamer's icon.ico.

Matches the other icons in this repository: black line art inside a thick black
ring, nothing but transparency outside the ring, no colour at all. Checked
against Image Tools (a pen nib), Screenshots (a camera), Git Work, Announcer,
Calibrations, Time Converter and CSS Logger.

The drawing is a photograph -- a frame -- with a pencil laid across its lower
right corner: a picture, and renaming it. Two shapes, nothing else, because the
size that decides whether an icon works is 16 px.

Two things keep it sharp where the old version was mush:

* every size in the .ico is drawn at that size, instead of shrinking one 256 px
  picture down. A shrunk 256 px drawing is grey soup at 16 px.
* the line weights are given in *screen* pixels with a floor, so the ring and
  the frame never thin out below what a small icon can show.

The optional sun and mountain inside the frame (--scene) are only drawn from
32 px up; at 16 px there is no room for them and they turn into a smudge along
the frame's bottom edge.

    python make_icon.py                          writes ../icon.ico
    python make_icon.py <path.ico>               writes somewhere else
    python make_icon.py <path.ico> <preview dir> also writes icon_preview.png,
                                                 a sheet of every size that
                                                 matters, for the eye check
    python make_icon.py ... --scene              put the sun and mountain back
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtWidgets import QApplication

# The sizes the other icons in the repository carry.
ICO_SIZES = [16, 24, 32, 48, 64, 72, 96, 128, 256]

_BOX = 256.0
_INK = QColor("#000000")
_PAPER = QColor("#ffffff")


def _pen(width: float, colour: QColor = _INK) -> QPen:
    p = QPen(colour)
    p.setWidthF(width)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def draw(p: QPainter, px: int, scene: bool = False) -> None:
    """Draw the icon into a 256-unit box that will end up `px` screen pixels
    wide. Line weights are chosen from `px`, not from the box."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    k = _BOX / float(px)                 # one screen pixel, in box units
    if px <= 16:
        # 16 px has room for the ring or for the drawing, not for both at full
        # weight. The ring gives way; a fat ring at this size squeezes the
        # photograph and the pencil into one black clump.
        ring_w, line_w = 1.6 * k, 1.25 * k
    else:
        ring_w = max(15.0, 2.0 * k)      # never thinner than 2.0 screen px
        line_w = max(12.0, 1.5 * k)      # never thinner than 1.5 screen px

    # The ring. White inside it, so the line art has a ground of its own on a
    # dark taskbar; transparent outside, like the others.
    half = ring_w / 2.0
    ring = QRectF(half + 1.0, half + 1.0,
                  _BOX - 2.0 * half - 2.0, _BOX - 2.0 * half - 2.0)
    p.setBrush(QBrush(_PAPER))
    p.setPen(_pen(ring_w))
    p.drawEllipse(ring)

    # A thicker ring eats inwards, so at 16 px the white disc is a good deal
    # smaller than at 256 px. Shrink the line art to match, about the centre,
    # or its corners end up under the ring. Pen widths are divided by the same
    # factor, so the floors above still hold after the shrink.
    inner = 127.0 - ring_w                       # radius of the white disc
    s = (inner - line_w / 2.0) / 106.0
    line_w = line_w / s
    p.translate(128.0, 128.0)
    p.scale(s, s)
    p.translate(-128.0, -128.0)

    # The photograph. A whole, unbroken rectangle up and to the left; the lower
    # right corner of the circle is left empty for the pencil, so the two shapes
    # never touch and neither has to be cut out of the other.
    if px <= 16:
        frame = QRectF(54.0, 66.0, 106.0, 84.0)  # a touch smaller, to leave a
        radius = 6.0                             # clear gap to the pencil
    else:
        frame = QRectF(52.0, 64.0, 116.0, 92.0)
        radius = 10.0
    p.setBrush(QBrush(_PAPER))
    p.setPen(_pen(line_w))
    p.drawRoundedRect(frame, radius, radius)

    if scene and px >= 32:
        # Sun, then one hill standing on the frame's bottom edge. Filled: an
        # outline at this size turns to mush.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(line_w))
        p.drawEllipse(QRectF(68.0, 80.0, 24.0, 24.0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_INK))
        hill = QPainterPath()
        hill.moveTo(60.0, 150.0)
        hill.lineTo(104.0, 102.0)
        hill.lineTo(148.0, 150.0)
        hill.closeSubpath()
        p.drawPath(hill)

    # The pencil, lying diagonally in the free corner beside the photograph.
    if px <= 24:
        # A hollow pencil this small is grey mush -- one solid diagonal stroke
        # is the only mark that stays crisp.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(line_w * 2.0))
        p.drawLine(QPointF(158.0, 210.0), QPointF(212.0, 156.0))
    else:
        p.setPen(_pen(line_w))
        p.setBrush(QBrush(_PAPER))
        body = QPolygonF([QPointF(159.0, 221.0), QPointF(141.0, 203.0),
                          QPointF(209.0, 135.0), QPointF(227.0, 153.0)])
        p.drawPolygon(body)

        # The tip: a filled triangle, so it reads as a pencil, not as a bar.
        tip = QPolygonF([QPointF(141.0, 203.0), QPointF(159.0, 221.0),
                         QPointF(134.0, 228.0)])
        p.setBrush(QBrush(_INK))
        p.drawPolygon(tip)

    if px >= 48:
        # The band near the top of the pencil. Only where it can be seen.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(line_w))
        p.drawLine(QPointF(193.0, 151.0), QPointF(211.0, 169.0))


def render(px: int, scene: bool = False) -> QImage:
    img = QImage(px, px, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.scale(px / _BOX, px / _BOX)
    draw(p, px, scene)
    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(target: Path, scene: bool = False) -> None:
    """Write the .ico by hand: one PNG per size, each drawn at that size."""
    pngs = [_png_bytes(render(s, scene)) for s in ICO_SIZES]

    out = bytearray(struct.pack("<HHH", 0, 1, len(pngs)))
    offset = 6 + 16 * len(pngs)
    for size, blob in zip(ICO_SIZES, pngs):
        side = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32,
                           len(blob), offset)
        offset += len(blob)
    for blob in pngs:
        out += blob

    target.write_bytes(bytes(out))
    print(f"wrote {target}  ({len(out)} bytes, {len(pngs)} sizes)")


def write_preview(folder: Path) -> None:
    """One sheet on a mid grey, so both the black ink and the white ground are
    visible. Top row: frame and pencil only. Bottom row: with sun and hill."""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    zoom = [16, 24, 32, 48]                      # blown up, to judge the small
    zoom_to = 160                                # sizes with the naked eye
    pad = 14
    width = max(sum(sizes) + pad * (len(sizes) + 1),
                zoom_to * len(zoom) + pad * (len(zoom) + 1))
    height = (256 + pad) * 2 + (zoom_to + pad) + pad

    sheet = QImage(width, height, QImage.Format.Format_ARGB32)
    sheet.fill(QColor(128, 128, 128))
    p = QPainter(sheet)
    for row, scene in enumerate((False, True)):
        top = pad + row * (256 + pad)
        x = pad
        for s in sizes:
            p.drawImage(x, top + (256 - s) // 2, render(s, scene))
            x += s + pad

    top = pad + 2 * (256 + pad)
    x = pad
    for s in zoom:
        big = render(s).scaled(zoom_to, zoom_to,
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.FastTransformation)
        p.drawImage(x, top, big)
        x += zoom_to + pad
    p.end()

    out = folder / "icon_preview.png"
    sheet.save(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--scene"]
    scene = "--scene" in sys.argv

    app = QApplication([])                       # QPainter needs an application
    here = Path(__file__).resolve().parent
    target = Path(argv[0]) if argv else here.parent / "icon.ico"
    write_ico(target, scene)
    if len(argv) > 1:
        write_preview(Path(argv[1]))
