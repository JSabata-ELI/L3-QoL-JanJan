"""Camera tile headers in narrow tiles: the timestamp drops onto a second line
instead of being cut off, and the picture keeps its room.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_tile_label_wrap.py

Three tiles of the same camera at three widths, all at the SAME label size, so the
only thing that changes is how much room the header has.

What to check on the picture:

  * the wide tile has name and timestamp side by side on one line;
  * the narrow tiles put the timestamp on its own second line, fully readable —
    no clipped text, no "…" in the middle of the time;
  * the header never lies on top of the picture: the dark image area starts below
    the last header line in every tile;
  * a name too long even for a whole line is shortened in the MIDDLE, not cut.

Writes tile_label_wrap.png beside this file.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from bench_common import load_slider

fails: list = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def main() -> int:
    m = load_slider()
    import numpy as np
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.clip(((xx + yy) % 64) * 3, 0, 255).astype(np.uint8)
    img = QImage(arr.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
    pm = QPixmap.fromImage(img)

    box = QWidget()
    box.setStyleSheet("background: #f3f3f3;")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(6, 6, 6, 6)
    lay.setSpacing(6)

    widths = [260, 150, 110]
    views = []
    for i, tw in enumerate(widths):
        cv = m.CameraView(i, "L3:PTM11WNF:Image" if i < 2 else
                          "L3:AVERYLONGCAMERANAMEINDEED:Image")
        cv.set_label_font_size(14)
        cv.img_view.set_pixmap(pm)
        cv.set_timestamp("12:34:56.789")
        cv.setFixedSize(tw, 190)
        lay.addWidget(cv)
        views.append(cv)
    box.show()
    app.processEvents()
    for cv in views:
        cv.resize(cv.width(), cv.height())
    app.processEvents()

    print("header reflow")
    check(not views[0]._ts_wrapped, "the 260 px tile keeps one header line")
    check(views[1]._ts_wrapped, "the 150 px tile moves the timestamp down")
    check(views[2]._ts_wrapped, "the 110 px tile moves the timestamp down")
    check(views[1]._ts_lbl.text() == "12:34:56.789",
          f"the timestamp is whole, not elided ({views[1]._ts_lbl.text()!r})")

    print("the picture keeps its room")
    for cv, tw in zip(views, widths):
        top = cv.img_view.geometry().top()
        hdr = cv._ts_row_w.geometry().bottom() if cv._ts_wrapped \
            else cv._name_lbl.geometry().bottom()
        check(top > hdr, f"{tw} px: picture starts at {top}, header ends at {hdr}")
        check(cv.img_view.height() > 40, f"{tw} px: the picture is still {cv.img_view.height()} px tall")

    print("names too long for a line")
    long_cv = views[2]
    check(long_cv._name_lbl.text() != long_cv._name_full,
          f"the long name is shortened ({long_cv._name_lbl.text()!r})")
    check("…" in long_cv._name_lbl.text(),
          "and shortened in the middle, so the ends still read")
    check(long_cv._name_lbl.toolTip() == long_cv._name_full,
          "the full name stays in the tooltip")

    print("overhead is reported honestly")
    check(views[1].image_overhead_px() > views[0].image_overhead_px(),
          f"a wrapped header costs more ({views[1].image_overhead_px()} vs "
          f"{views[0].image_overhead_px()} px)")

    out = Path(__file__).parent

    def shoot():
        app.processEvents()
        box.grab().save(str(out / "tile_label_wrap.png"))
        print("written: tile_label_wrap.png")
        app.quit()

    QTimer.singleShot(700, shoot)
    app.exec()
    print()
    if fails:
        print(f"{len(fails)} FAILED")
        for f in fails:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
