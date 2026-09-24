"""Render several reference marks, one of each picked out, on a dark and a bright
frame — the only way to judge whether the highlight can actually be seen.

The house rule is that no colour may be left to the theme and nothing may be
invisible, and a mark's own colour cannot satisfy that on its own: yellow disappears
into a blown-out beam core and a dark ring disappears into a black field. The picked-out
mark therefore carries its own contrast — a black casing under the colour and white
dashes over it.

Run with the real Windows platform (offscreen has no fonts and lies about text):

    set QT_QPA_PLATFORM=windows
    python testing/render_overlay_marks.py

Writes testing/overlay_marks.png.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

import is_t

W, H = 460, 340


def _frame(level: int) -> QPixmap:
    """A flat frame of one brightness, with a bright blob in the middle — the two
    grounds a mark has to stay visible on, in one picture."""
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(0xFF000000 | (level << 16) | (level << 8) | level)
    pm = QPixmap.fromImage(img)
    p = QPainter(pm)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255) if level < 128 else QColor(10, 10, 10))
    p.drawEllipse(W // 2 - 70, H // 2 - 55, 140, 110)
    p.end()
    return pm


def _view(level: int) -> "is_t.ImageView":
    iv = is_t.ImageView()
    iv.bg_color = QColor("#222")
    iv.resize(W, H)
    iv.set_pixmap(_frame(level))
    iv.show_cross = iv.show_circle = iv.show_square = True
    # Three of each, spread over both the flat ground and the blob.
    for xy in ((0.18, 0.22), (0.50, 0.50), (0.82, 0.76)):
        iv.add_mark("cross", xy)
    for c in ((0.30, 0.68, 0.10, 0.12), (0.50, 0.50, 0.20, 0.19),
              (0.78, 0.26, 0.11, 0.13)):
        iv.add_mark("circle", c)
    for r in ((0.06, 0.06, 0.26, 0.24), (0.40, 0.14, 0.62, 0.34),
              (0.62, 0.58, 0.94, 0.90)):
        iv.add_mark("square", r)
    return iv


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    cols = [("dark frame", 25), ("bright frame", 235)]
    rows = [("cross picked out", ("cross", 1)),
            ("circle picked out", ("circle", 1)),
            ("square picked out", ("square", 2))]

    cap_h = 26
    sheet = QPixmap(W * len(cols), (H + cap_h) * len(rows))
    sheet.fill(QColor("#f3f3f3"))
    p = QPainter(sheet)
    f = QFont(); f.setPixelSize(13); f.setBold(True)
    p.setFont(f)

    for r, (row_name, sel) in enumerate(rows):
        for c, (col_name, level) in enumerate(cols):
            iv = _view(level)
            iv.set_draw_mode(sel[0])
            iv._sel = sel
            x, y = c * W, r * (H + cap_h)
            p.setPen(QColor("#111"))
            p.drawText(QRect(x + 8, y, W - 16, cap_h),
                       Qt.AlignmentFlag.AlignVCenter,
                       f"{row_name}  ·  {col_name}")
            p.drawPixmap(x, y + cap_h, iv.grab())
    p.end()

    out = Path(__file__).resolve().parent / "overlay_marks.png"
    sheet.save(str(out))
    print(f"written {out}  ({sheet.width()}x{sheet.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
