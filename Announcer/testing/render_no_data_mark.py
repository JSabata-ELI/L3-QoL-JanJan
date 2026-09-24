"""Draw the red exclamation mark so it can be LOOKED at.

Run:  python testing/render_no_data_mark.py
Writes: testing/no_data_mark.png

Nothing is shown: the widgets are built and `render`ed into a pixmap, never
put on the screen. Offscreen platform — there is no text in this mark, so the
missing fonts offscreen cannot lie about it.

Three grounds, because the mark floats over whatever the operator happens to be
looking at: a light panel, the dark desktop, and a red window where a red mark
with no outline would vanish.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QPoint                              # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap            # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

app = QApplication.instance() or QApplication([])

import ann_ui as U                                             # noqa: E402

U.install_app_look(app)

GROUNDS = ["#f3f3f3", "#1d1d1d", "#b03030", "#ffffff"]
SIZES = [22, 28, 34, 48]

pad = 14
cell_w = max(SIZES) + 40
row_h = max(SIZES) + pad * 2
sheet = QPixmap(cell_w * len(SIZES), row_h * len(GROUNDS))
p = QPainter(sheet)
for r, ground in enumerate(GROUNDS):
    p.fillRect(0, r * row_h, sheet.width(), row_h, QColor(ground))
    for c, size in enumerate(SIZES):
        light = U.StatusLight(size)
        light.set_colour("green")
        mark = U.AlertMark(size)
        x = c * cell_w + 8
        y = r * row_h + pad
        light.render(p, QPoint(x, y))
        mark.render(p, QPoint(x + light.width() + 2, y))
p.end()

out = HERE / "no_data_mark.png"
sheet.save(str(out))
print(f"wrote {out}  ({sheet.width()}x{sheet.height()})")
