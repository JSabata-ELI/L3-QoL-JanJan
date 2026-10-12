"""Save Layout — every camera, as arranged on screen, in one picture (01.10.2026).

What is checked (a real camera grid built in process, never shown on screen):

  * one picture comes out, covering exactly the tiles, frames included — no
    other margins; the frames, the tile insides and the gaps between tiles are
    the screen's colours (05.10.2026: they were all black), and the refresh dot
    is drawn;
  * every camera's name and timestamp are in the picture, where the tile shows
    them (02.10.2026: a layout without them could not say what it showed);
  * each camera's frame lands where its tile is, in its own colour;
  * the picture is drawn above screen size when the frames have the pixels for
    it, and never beyond the cap;
  * the marks drawn on a camera are in the picture;
  * a zoomed tile shows its zoomed part, as on screen;
  * with nothing loaded there is nothing to save (None, no crash).

    PYTHONIOENCODING=utf-8 python testing/test_save_layout_image.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_layout_"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtCore import QPoint, Qt                                 # noqa: E402
from PySide6.QtGui import QColor, QPixmap                             # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def label_rects(grid):
    """Name and timestamp labels of every tile, in grid coordinates."""
    out = []
    for cv in grid._cam_views:
        for lbl in (cv._name_lbl, cv._ts_lbl):
            if lbl.isVisibleTo(cv) and lbl.text() and lbl.width() > 0:
                at = lbl.mapTo(grid, QPoint(0, 0))
                out.append((lbl, at.x(), at.y(), lbl.width(), lbl.height()))
    return out


def tile_box(grid):
    """The tiles (frame and all) in grid coordinates, and their bounding box."""
    tiles = []
    for cv in grid._cam_views:
        at = cv.mapTo(grid, QPoint(0, 0))
        tiles.append((at.x(), at.y(), cv.width(), cv.height()))
    x0 = min(t[0] for t in tiles); y0 = min(t[1] for t in tiles)
    x1 = max(t[0] + t[2] for t in tiles); y1 = max(t[1] + t[3] for t in tiles)
    return tiles, (x0, y0, x1, y1)


def near(c, hexcol, tol=12):
    w = QColor(hexcol)
    return (abs(c.red() - w.red()) < tol and abs(c.green() - w.green()) < tol
            and abs(c.blue() - w.blue()) < tol)


def solid(w, h, c):
    pm = QPixmap(w, h)
    pm.fill(c)
    return pm


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    names = ["C01-A-LAYOUT1NF", "C02-B-LAYOUT2NF", "C03-C-LAYOUT3NF"]
    colours = [QColor(220, 30, 30), QColor(30, 200, 30), QColor(40, 60, 230)]
    grid = is_t.MultiCameraGrid()
    grid.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    grid.resize(900, 300)

    check("nothing loaded → nothing to save",
          is_t.render_layout_image(grid) is None)

    grid.setup_cameras(names)
    grid.show()
    app.processEvents()
    for i, c in enumerate(colours):
        grid.get_img_view(i).set_pixmap(solid(1200, 900, c))
        grid._cam_views[i]._ts_lbl.setText(f"2026-10-02 12:00:0{i}.000")
    app.processEvents()

    rects = []
    for i in range(3):
        iv = grid.get_img_view(i)
        ir = iv._img_rect()
        tl = iv.mapTo(grid, ir.topLeft())
        rects.append((tl.x(), tl.y(), ir.width(), ir.height()))
    check("three tiles with a picture", all(r[2] > 0 for r in rects), str(rects))

    img = is_t.render_layout_image(grid, max_scale=2.0)
    check("one picture comes out", img is not None and not img.isNull())
    labs = label_rects(grid)
    check("every camera has a name and a timestamp label", len(labs) == 6, str(len(labs)))
    tiles, (x0, y0, x1, y1) = tile_box(grid)
    s = img.width() / (x1 - x0)
    check("drawn above screen size, at the cap", abs(s - 2.0) < 0.02, f"scale {s:.3f}")
    check("cut to the tiles, frames included (same shape as their bounding box)",
          abs(img.height() / s - (y1 - y0)) <= 1.0,
          f"{img.width()}x{img.height()} for {x1 - x0}x{y1 - y0}")
    # 05.10.2026: the tiles look as on screen — grey frame, dark grey inside, the
    # grid's own background between them (it was all black).
    tx, ty, tw, th = tiles[0]
    c = img.pixelColor(int((tx + tw / 2 - x0) * s), int((ty + 0.5 - y0) * s))
    check("tile frame: #555 border", near(c, is_t.LAYOUT_TILE_BORDER), c.name())
    c = img.pixelColor(int((tx + tw / 2 - x0) * s), int((ty + 4.5 - y0) * s))
    check("tile inside: #222 around the labels", near(c, is_t.LAYOUT_TILE_BG), c.name())
    gx = (tiles[0][0] + tiles[0][2] + tiles[1][0]) / 2
    if tiles[1][0] - (tiles[0][0] + tiles[0][2]) >= 2:
        c = img.pixelColor(int((gx - x0) * s), int((ty + th / 2 - y0) * s))
        check("between tiles: the grid background #1a1a1a", near(c, is_t.LAYOUT_PAGE_BG, 6),
              c.name())
    dot = grid._cam_views[0]._refresh_dot
    dat = dot.mapTo(grid, QPoint(dot.width() // 2, dot.height() // 2))
    c = img.pixelColor(int((dat.x() - x0) * s), int((dat.y() - y0) * s))
    check("the refresh dot is in the picture", near(c, "#444444"), c.name())
    # Each label is painted in its own box colour, where the tile shows it.
    for lbl, lx, ly, lw, lh in labs:
        want = QColor("#444444") if lbl.text().startswith("C0") else QColor("#333333")
        got = img.pixelColor(int((lx + 2 - x0) * s), int((ly + lh / 2 - y0) * s))
        ok = all(abs(a - b) < 12 for a, b in ((got.red(), want.red()),
                                               (got.green(), want.green()),
                                               (got.blue(), want.blue())))
        check(f"label \"{lbl.text()[:16]}\" is in the picture", ok, got.name())

    for i, (rx, ry, rw, rh) in enumerate(rects):
        cx = int(((rx + rw / 2) - x0) * s); cy = int(((ry + rh / 2) - y0) * s)
        got = img.pixelColor(cx, cy)
        want = colours[i]
        ok = (abs(got.red() - want.red()) < 6 and abs(got.green() - want.green()) < 6
              and abs(got.blue() - want.blue()) < 6)
        check(f"camera {i + 1} sits where its tile is", ok, got.name())

    # A mark on camera 1, a zoom on camera 2.
    iv0 = grid.get_img_view(0)
    iv0.show_cross = True
    iv0.add_mark("cross", (0.5, 0.5))
    iv0.cross_size = 30
    iv1 = grid.get_img_view(1)
    pm = solid(1200, 900, QColor(30, 200, 30))
    from PySide6.QtGui import QPainter
    p = QPainter(pm); p.fillRect(0, 0, 600, 900, QColor(250, 250, 250)); p.end()
    iv1.set_pixmap(pm)                      # left half white, right half green
    iv1.set_zoom((0.6, 0.0, 1.0, 1.0))      # zoomed into the green half only
    app.processEvents()
    img = is_t.render_layout_image(grid, max_scale=2.0)

    # The zoom changed tile 2's shape and with it the row height, so every tile is
    # measured again.
    rects = []
    for i in range(3):
        iv = grid.get_img_view(i)
        ir = iv._img_rect()
        tl = iv.mapTo(grid, ir.topLeft())
        rects.append((tl.x(), tl.y(), ir.width(), ir.height()))
    _tiles, (x0, y0, x1, _y1) = tile_box(grid)
    s = img.width() / (x1 - x0)

    rx, ry, rw, rh = rects[0]
    # The cross arm 15 px right of the centre: the cross colour, not the frame's red.
    px = int(((rx + rw / 2 + 15) - x0) * s); py = int(((ry + rh / 2) - y0) * s)
    c = img.pixelColor(px, py)
    check("the cross on camera 1 is in the picture", c.green() > 150 and c.red() < 120,
          c.name())
    ir1 = iv1._img_rect(); tl1 = iv1.mapTo(grid, ir1.topLeft())
    px = int(((tl1.x() + 4) - x0) * s); py = int(((tl1.y() + ir1.height() / 2) - y0) * s)
    c = img.pixelColor(px, py)
    check("a zoomed tile shows its zoomed part (green, not the white left half)",
          c.green() > 150 and c.red() < 120, c.name())

    base = is_t.render_layout_image(grid, max_scale=1.0)
    cap = int(base.width() * base.height() * 2.25)       # room for 1.5× screen size
    big = is_t.render_layout_image(grid, max_scale=4.0, max_pixels=cap)
    sc = big.width() / base.width()
    check("the pixel cap brings the size down (1.5× here)",
          abs(sc - 1.5) < 0.02 and big.width() * big.height() <= cap * 1.01,
          f"{big.width()}x{big.height()}, scale {sc:.3f}")
    tiny = is_t.render_layout_image(grid, max_scale=4.0, max_pixels=1000)
    check("…but never below screen size", tiny.width() >= base.width(),
          f"{tiny.width()} vs {base.width()}")

    # The PV values panel is painted in where it floats over the tiles.
    pv = is_t._PvOverlayPanel(grid)
    pv.bg_opacity = 100
    pv.bg_color = QColor(255, 255, 0)
    pv._apply_style()
    pv.setGeometry(rects[0][0] + 10, rects[0][1] + 10, 120, 60)
    pv.show()
    app.processEvents()
    img = is_t.render_layout_image(grid, pv, max_scale=2.0)
    c = img.pixelColor(int((rects[0][0] + 40 - x0) * s), int((rects[0][1] + 40 - y0) * s))
    check("the PV panel is in the picture where it floats", c.red() > 200
          and c.green() > 200 and c.blue() < 120, c.name())

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
