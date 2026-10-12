"""Single-camera view: the name/time strip sits directly above the picture.

With Pointing analysis open the picture is centred lower in a tall view; the
strip must end exactly at the picture's top edge, not stay parked at y = 0 and
never cover the picture.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QColor, QImage

app = QApplication.instance() or QApplication(sys.argv)

import is_t  # noqa: E402


def main():
    iv = is_t.ImageView()
    iv.cam_label_use_overlay = False
    iv.cam_label_font_px = 12
    iv.cam_label_text = "C03-039-TEST"
    iv.cam_ts_text = "2026-10-07 12:00:00"
    iv.resize(600, 700)                       # tall view, wide frame -> big margin
    pm = QPixmap(800, 400)
    pm.fill(QColor(0, 200, 0))
    iv.set_pixmap(pm)

    ir = iv._img_rect()
    assert ir is not None
    lbh = iv._label_bar_h()
    assert ir.top() - lbh > 20, "test needs a margin above the picture"

    img = QImage(iv.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(1, 2, 3))
    iv.render(img)

    x = ir.left() + 10
    strip = QColor(img.pixel(x, ir.top() - lbh // 2))
    top = QColor(img.pixel(x, 2))
    inside = QColor(img.pixel(x, ir.top() + 5))
    print("img_rect", ir, "label bar", lbh)
    print("just above picture", strip.name(), "top of view", top.name(),
          "inside picture", inside.name())
    assert strip.name() == "#444444", "strip is not directly above the picture"
    assert top.name() != "#444444", "strip still parked at the top of the view"
    assert inside.green() > 150, "strip covers the picture"
    print("OK")


if __name__ == "__main__":
    main()
