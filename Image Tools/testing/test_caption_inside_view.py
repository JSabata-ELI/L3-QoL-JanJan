"""A region caption must stay inside the area being painted.

A region drawn along the top edge used to place its numbers above the canvas, where
nothing could be read. The test paints on a known-size image and checks that the dark
plate the numbers sit on actually has pixels in the picture, and that it is not glued
to the very top row when the region sits at the top.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPointF                     # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter   # noqa: E402

import wk_t                                            # noqa: E402

W, H = 400, 300
_app = QGuiApplication.instance() or QGuiApplication(sys.argv)


def _plate_rows(annot):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    wk_t._draw_caption(p, annot, lambda x, y: QPointF(x, y), 1.0, QColor("#ffffff"))
    p.end()
    rows = [y for y in range(H)
            if any(QColor(img.pixel(x, y)).red() < 200 for x in range(W))]
    return rows


def _roi(y0, y1):
    a = wk_t._Annot(kind=wk_t.A_ROI_RECT, pts=[[20.0, float(y0)], [180.0, float(y1)]])
    a.label = "min 3\nmax 250\nmean 41.5"
    return a


def test_caption_at_top_is_visible():
    rows = _plate_rows(_roi(2, 90))
    assert rows, "the caption was painted outside the picture"
    assert min(rows) >= 0 and max(rows) < H


def test_caption_in_the_middle_still_sits_above():
    rows = _plate_rows(_roi(150, 220))
    assert rows and max(rows) < 150, "the caption should stay above the region"


if __name__ == "__main__":
    test_caption_at_top_is_visible()
    test_caption_in_the_middle_still_sits_above()
    print("ok")
