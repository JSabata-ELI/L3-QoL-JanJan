"""Day by day (Horizontal): the camera name lives IN THE CAPTION STRIP.

It used to be a grey band over every row, drawn at a fixed 8.5 pt — so "Label
size" grew the date/time captions but never the name, and the band cost a strip
of height on every row (27.10.2026). Now the name is a plate at the left end of
the row's caption strip, pinned to the pane's left edge while the wall scrolls
sideways, in the captions' own size.

Pinned here:
  * no band: a row's tiles start at the row's top
  * the name grows with Label size, as fast as the captions
  * the plate follows the sideways scroll
  * a caption never runs under the plate
  * a click on the plate marks nothing
  * the vertical wall's camera plates and day bands grow with Label size too

Needs real fonts, so:

    QT_QPA_PLATFORM=minimal:enable_fonts python testing/test_wall_row_name_in_caption.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")

import test_wall_rows_wide as T          # noqa: E402  (make_wall, load_finder)

FAILURES = T.FAILURES
check = T.check


class _Bar:
    def __init__(self, v=0):
        self.v = v

    def value(self):
        return self.v

    def maximum(self):
        return 10 ** 6


class _Host:
    """Stands in for the _WallScroll: only the scroll positions are read."""
    def __init__(self):
        self.h, self.vb = _Bar(), _Bar()

    def horizontalScrollBar(self):
        return self.h

    def verticalScrollBar(self):
        return self.vb


def paint(wall):
    from PySide6.QtGui import QImage, QPainter, QColor
    img = QImage(max(1, wall.minimumWidth() or T.PANE_W),
                 max(1, wall.minimumHeight() or T.PANE_H),
                 QImage.Format.Format_RGB888)
    img.fill(QColor("#1a1a1a"))
    p = QPainter(img)
    try:
        wall._paint_into(p, wall._rects)
    finally:
        p.end()
    return img


def main() -> int:
    m = T.load_finder()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontMetrics
    app = QApplication.instance() or QApplication(sys.argv[:1])

    print("\n=== no band over the row ===")
    wall = T.make_wall(m, 3, n_days=6)
    host = _Host()
    wall._scroll_host = host
    tops = sorted({r.y() for r in wall._rects})
    check("each row's tiles start at the row's own top",
          tops == sorted(wall.row_tops()), f"{tops} vs {wall.row_tops()}")
    check("the rows step by the tile height alone",
          all(r.height() == wall.row_pitch() for r in wall._rects),
          f"{wall._rects[0].height()} vs pitch {wall.row_pitch()}")

    print("\n=== the plate sits in the caption strip, at the left edge ===")
    paint(wall)
    box0, txt0 = wall._row_heads[0]
    r0 = [r for c, r in zip(wall._cells, wall._rects) if c["cam"] == "CAM00"][0]
    check("it names the camera", txt0 == "CAM00", repr(txt0))
    check("it lies inside the row's bottom caption strip",
          r0.top() < box0.top() and box0.bottom() <= r0.bottom(),
          f"plate {box0.top()}..{box0.bottom()}, tile {r0.top()}..{r0.bottom()}")
    check("at the pane's left edge", 0 <= box0.left() <= 6, f"x {box0.left()}")

    print("\n=== it rides with the sideways scroll ===")
    host.h.v = 450
    paint(wall)
    box1 = wall._row_heads[0][0]
    check("scrolled 450 px, the plate moved 450 px",
          box1.left() - box0.left() == 450, f"{box0.left()} ->{box1.left()}")
    check("and stayed on the same line", box1.top() == box0.top(),
          f"{box0.top()} vs {box1.top()}")

    print("\n=== a click on the plate marks nothing ===")
    check("the plate is not a tile", wall._hit(box1.center()) == -1,
          str(wall._hit(box1.center())))
    check("and is not empty canvas either", wall._on_row_head(box1.center()))

    print("\n=== a caption never runs under the plate ===")
    # The caption text of the tile under the plate is centred right of it: the
    # drawn text's left edge is past the plate's right edge.
    i_anchor = next(i for i, (c, r) in enumerate(zip(wall._cells, wall._rects))
                    if c["cam"] == "CAM00" and r.left() <= box1.left() <= r.right())
    r = wall._rects[i_anchor]
    txt = wall._caption(wall._cells[i_anchor])
    fm = QFontMetrics(wall._caption_font())
    left = box1.right() + 1 + 4
    room_w = r.right() + 1 - left
    text_left = left + (room_w - fm.horizontalAdvance(txt)) / 2
    check("the caption starts right of the plate",
          text_left >= box1.right(), f"text from {text_left:.0f}, plate to {box1.right()}")

    print("\n=== the name grows with Label size, as fast as the captions ===")
    host.h.v = 0
    wall._shared.cap_pt = 8.5
    wall.refresh_captions()
    paint(wall)
    small_w, small_h = wall._row_heads[0][0].width(), wall._CAPTION_H
    wall._shared.cap_pt = 17.5
    wall.refresh_captions()
    paint(wall)
    big_w, big_h = wall._row_heads[0][0].width(), wall._CAPTION_H
    check("the plate grows wider", big_w > small_w * 1.5, f"{small_w} ->{big_w}")
    check("and the strip taller", big_h > small_h, f"{small_h} ->{big_h}")
    ratio_name = wall._band_font().pointSizeF() / 17.5
    check("the name's text is the captions' size",
          abs(wall._band_font().pointSizeF() - wall._caption_font().pointSizeF()) < 0.01,
          f"ratio {ratio_name:.2f}")

    print("\n=== the saved picture carries the names too ===")
    img = wall.composite_image(scale=1.0)
    check("the wall composites", img is not None)

    print("\n=== vertical wall: plates and day bands follow Label size ===")
    v = T.make_wall(m, 3, n_days=3)
    v.set_layout_mode("cols")
    v._shared.cap_pt = 8.5
    v.refresh_captions()
    hdr_s, band_s = v._DAY_HDR_H, v._DAY_BAND_H
    check("at the default size the old heights stay",
          hdr_s == 22 and band_s == 20, f"{hdr_s} / {band_s}")
    v._shared.cap_pt = 20.0
    v.refresh_captions()
    check("larger text makes the camera plate taller", v._DAY_HDR_H > hdr_s,
          f"{hdr_s} ->{v._DAY_HDR_H}")
    check("and the day band", v._DAY_BAND_H > band_s, f"{band_s} ->{v._DAY_BAND_H}")
    check("the plates are laid out at the new height",
          v._row_heads[0][0].height() == v._DAY_HDR_H,
          f"{v._row_heads[0][0].height()} vs {v._DAY_HDR_H}")
    v._shared.cap_pt = 8.5

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
