"""Day by day, Vertical: A COLUMN IS ONE CAMERA, the days run downward.

The operator's layout (12.10.2026): as many columns as there are cameras, up to
six filling the pane and a sideways bar beyond that; one row per day with the
NEWEST day on top and the oldest at the bottom, read by scrolling down. The
Horizontal layout (a row per camera) stays available behind a switch, and there
one or two cameras keep their size but sit in the middle of the pane.

Also pinned here, from the same request:
  * the Picture size slider and Ctrl+wheel are one zoom, each follows the other
  * hovering a MARKED tile still shows the hover outline
  * the Detailed view: camera + time ABOVE the picture, no image-info line, and
    the camera plate is a button whose list jumps to another camera — on the same
    day as the frame on screen

Runs offscreen, no share, no network:

    python testing/test_day_wall_vertical.py
"""
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_day_wall import load_finder

FAILURES: "list[str]" = []
PANE_W, PANE_H = 1200, 700


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(day, hour: int) -> int:
    from zoneinfo import ZoneInfo
    return int(datetime(day.year, day.month, day.day, hour,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def make_wall(m, n_cams: int, n_days: int, mode: str = "cols"):
    import numpy as np
    wall = m._DayWall()
    wall.set_layout_mode(mode)
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    day0 = date(2026, 9, 1)
    cells = []
    for d in range(n_days):
        day = day0 + timedelta(days=d)
        for c in range(n_cams):
            p = Path(f"vert_{d}_{c}.png")
            wall._shared.raw[p] = (np.zeros((1024, 1280), dtype=np.float32), 4095.0)
            cells.append({"day": day, "cam": f"CAM{c:02d}", "cam_folder": f"CAM{c:02d}",
                          "path": p, "ts_ns": ns_at(day, 8 + c), "status": "found",
                          "meta": {"source": "pv"}})
    wall.set_cells(cells)
    wall._relayout()
    return wall


def check_vertical(m):
    print("\n=== Vertical: a column per camera, newest day on top ===")
    wall = make_wall(m, 2, 5)
    xs = {c["cam"]: {wall._rects[i].x() for i, cc in enumerate(wall._cells)
                     if cc["cam"] == c["cam"]} for c in wall._cells}
    check("every camera keeps one column", all(len(v) == 1 for v in xs.values()),
          repr(xs))
    span = max(r.x() + r.width() for r in wall._rects)
    check("two cameras fill the pane's width", abs(span - PANE_W) <= 2,
          f"{span} of {PANE_W}")
    check("no sideways bar for two", wall.minimumWidth() == 0,
          str(wall.minimumWidth()))
    y_of_day = {}
    for i, c in enumerate(wall._cells):
        y_of_day.setdefault(c["day"], set()).add(wall._rects[i].y())
    check("a day is one row across both cameras",
          all(len(v) == 1 for v in y_of_day.values()), repr(y_of_day))
    order = [d for d, _y in sorted(((d, min(v)) for d, v in y_of_day.items()),
                                   key=lambda t: t[1])]
    check("the newest day is on top, the oldest at the bottom",
          order == sorted(order, reverse=True), repr(order))
    check("one band per day, naming it", len(wall._day_bands) == 5
          and "05.09.2026" in wall._day_bands[0][1], repr(wall._day_bands[:1]))
    check("one name plate per camera", [t for _r, t in wall._row_heads]
          == ["CAM00", "CAM01"], repr(wall._row_heads))
    r0 = wall._rects[0]
    day_rows = (PANE_H - wall._DAY_HDR_H) / (r0.height() + wall._DAY_BAND_H)
    check("at least three days fit the pane", day_rows >= 2.95, f"{day_rows:.2f}")
    check("five days are taller than the pane, so it scrolls down",
          wall.minimumHeight() > PANE_H, str(wall.minimumHeight()))
    # 13.10.2026: a notch must move EXACTLY one day row — the day band included —
    # so the cameras stay put. Stepping by the tile height alone drifted by the band.
    tops = wall.row_tops()
    steps = {b - a for a, b in zip(tops, tops[1:])}
    check("row edges are one tile + one day band apart",
          steps == {r0.height() + wall._DAY_BAND_H}, repr(steps))
    check("the first row parks the pane at the top", tops[:1] == [0], repr(tops[:1]))
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    sc = m._WallScroll()
    sc.resize(PANE_W, PANE_H)
    sc.setWidget(wall)
    sc.setWidgetResizable(True)
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    wall._scroll_host = sc
    wall._relayout()
    tops = wall.row_tops()
    bar = sc.verticalScrollBar()

    def notch(dy):
        sc.wheelEvent(QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                                  QPoint(0, dy), Qt.MouseButton.NoButton,
                                  Qt.KeyboardModifier.NoModifier,
                                  Qt.ScrollPhase.NoScrollPhase, False))
    seen = []
    for _ in range(3):
        notch(-120)
        seen.append(bar.value())
    want = [min(t, bar.maximum()) for t in tops[1:4]]
    check("each notch down lands on the next row edge exactly", seen == want,
          f"{seen} vs {want} (max {bar.maximum()})")
    bar.setValue(tops[1] + 7)                 # off-grid, e.g. after a drag
    notch(-120)
    check("an off-grid start snaps to the next edge, not edge + 7",
          bar.value() == min(tops[2], bar.maximum()), str(bar.value()))
    notch(120)
    check("and a notch up goes back exactly one row", bar.value() == tops[1],
          str(bar.value()))
    sc.takeWidget()
    check("a click lands on the tile it is over",
          all(wall._hit(r.center()) == i for i, r in enumerate(wall._rects)
              if r.top() > wall._DAY_HDR_H))
    check("the name plates are not tiles",
          wall._hit(wall._row_heads[0][0].center()) == -1)
    check("the tile caption names the day, not the camera",
          wall._caption(wall._cells[0]).startswith("01.09."),
          wall._caption(wall._cells[0]))

    print("\n=== a tall camera does not take the whole pane ===")
    import numpy as np
    tall = make_wall(m, 4, 5)
    for i, c in enumerate(tall._cells):
        if c["cam"] == "CAM00":          # the diode array: portrait, 1024 x 2048
            tall._shared.raw[c["path"]] = (np.zeros((2048, 1024), np.float32), 4095.0)
    tall._relayout()
    r = tall._rects[0]
    rows_in = (PANE_H - tall._DAY_HDR_H) / (r.height() + tall._DAY_BAND_H)
    check("still at least three days in the pane", rows_in >= 2.95, f"{rows_in:.2f}")
    tall.set_zoom(2.0)
    check("Picture size still makes the rows taller",
          tall._rects[0].height() > r.height(), f"{tall._rects[0].height()} vs {r.height()}")

    print("\n=== the tiles are drawn from a thinned frame, the hovered one sharp ===")
    big = make_wall(m, 2, 3)
    for c in big._cells:
        big._shared.raw[c["path"]] = (np.random.default_rng(1).random(
            (2048, 2560), dtype=np.float32) * 4000, 4095.0)
    big._relayout()
    r = big._rects[0]
    step = big._thin_step((2048, 2560), r, 0)
    check("a 2560 px frame in a small tile is thinned", step >= 2, f"step {step}")
    calls: list = []
    real = m._render_u8

    def spy(arr, *a, **k):
        calls.append(arr.shape)
        return real(arr, *a, **k)
    m._render_u8 = spy
    try:
        big._pix.clear()
        big._tile_pixmap(0, r)
        big._hover = 0
        big._make_hq()
    finally:
        m._render_u8 = real
    check("the tile renders the thinned frame",
          calls and calls[0] == (len(range(0, 2048, step)), len(range(0, 2560, step))),
          repr(calls[:1]))
    check("the hovered tile renders every pixel",
          len(calls) > 1 and calls[1] == (2048, 2560), repr(calls[1:2]))
    check("and keeps that sharp copy", big._hq is not None and big._hq[0] == 0)
    pm = big._tile_pixmap(0, r)
    avail_w = r.width() - 2 * big._GAP
    check("the thinned picture still fills the tile", pm.width() >= avail_w - 2
          or pm.height() >= r.height() - big._CAPTION_H - 2 * big._GAP - 2,
          f"{pm.width()}x{pm.height()} in {r.width()}x{r.height()}")

    print("\n=== the hover tooltip is light with dark text ===")
    css = big.styleSheet()
    check("the dark ground is named, not inherited by the tooltip",
          "QWidget#dayWall" in css and "QToolTip" in css and "#111111" in css, css)

    print("\n=== more than six cameras scroll sideways ===")
    wide = make_wall(m, 8, 2)
    cw = wide.cols_col_width()
    check("a column is a sixth of the pane", abs(cw - PANE_W / 6) <= 1,
          f"{cw:.1f}")
    check("the widget asks for eight sixths of the pane",
          abs(wide.minimumWidth() - PANE_W * 8 / 6) <= 4, str(wide.minimumWidth()))

    print("\n=== a long camera name is never cut, the text shrinks ===")
    long = make_wall(m, 6, 1)
    long._row_heads[0] = (long._row_heads[0][0], "C03-040-PTM11WNF-VERY-LONG-NAME")
    from PySide6.QtGui import QImage, QPainter, QColor
    img = QImage(PANE_W, PANE_H, QImage.Format.Format_RGB888)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    try:
        long._paint_into(p, long._rects)
        ok = True
    except Exception as e:      # noqa: BLE001
        ok = False
        print("    ", e)
    p.end()
    check("the wall paints with a name wider than its column", ok)

    print("\n=== the saved picture covers every row ===")
    out = wall.composite_image(scale=1.0)
    check("it composites", out is not None)
    if out is not None:
        check("as tall as every day", out.height() >= wall.minimumHeight(),
              f"{out.height()} vs {wall.minimumHeight()}")


def check_horizontal_centred(m):
    print("\n=== Horizontal: one or two cameras sit in the middle ===")
    for n in (1, 2):
        wall = make_wall(m, n, 3, mode="rows")
        # No band over the row any more: the tiles themselves are the top.
        top = min(r.y() for r in wall._rects)
        bottom = max(r.y() + r.height() for r in wall._rects)
        check(f"{n} camera(s): same gap above and below",
              abs(top - (PANE_H - bottom)) <= 2, f"top {top}, bottom gap {PANE_H - bottom}")
        check(f"{n} camera(s): the row keeps its size",
              abs(wall.row_pitch() - PANE_H // 3) <= 3, str(wall.row_pitch()))
    wall = make_wall(m, 4, 3, mode="rows")
    check("four cameras start at the top as before",
          min(r.y() for r in wall._rects) == 0)


def _scroll_host(m, wall):
    from PySide6.QtWidgets import QApplication
    sc = m._WallScroll()
    sc.resize(PANE_W, PANE_H)
    sc.setWidget(wall)
    sc.setWidgetResizable(True)
    QApplication.processEvents()
    wall._scroll_host = sc
    wall._relayout()
    QApplication.processEvents()
    return sc


def _notch(sc, dy):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    sc.wheelEvent(QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                              QPoint(0, dy), Qt.MouseButton.NoButton,
                              Qt.KeyboardModifier.NoModifier,
                              Qt.ScrollPhase.NoScrollPhase, False))


def check_horizontal_wheel(m):
    print("\n=== Horizontal: the wheel steps a day sideways ===")
    # 27.10.2026: in the horizontal layout the days run sideways, so wheel down
    # goes right and wheel up goes left, a whole day a notch.
    wall = make_wall(m, 2, 8, mode="rows")
    sc = _scroll_host(m, wall)
    hbar, vbar = sc.horizontalScrollBar(), sc.verticalScrollBar()
    lefts = wall.col_lefts()
    check("eight days are wider than the pane", hbar.maximum() > 0,
          str(hbar.maximum()))
    seen = []
    for _ in range(3):
        _notch(sc, -120)
        seen.append(hbar.value())
    want = [min(x, hbar.maximum()) for x in lefts[1:4]]
    check("each notch down moves one day to the right", seen == want,
          f"{seen} vs {want}")
    check("and the pane does not move down", vbar.value() == 0, str(vbar.value()))
    _notch(sc, 120)
    check("a notch up moves one day back to the left", hbar.value() == lefts[2],
          f"{hbar.value()} vs {lefts[2]}")
    hbar.setValue(lefts[1] + 7)
    _notch(sc, -120)
    check("an off-grid start snaps to the next day edge",
          hbar.value() == min(lefts[2], hbar.maximum()), str(hbar.value()))
    for _ in range(20):
        _notch(sc, -120)
    check("past the last day edge it stops at the far right",
          hbar.value() == hbar.maximum(), f"{hbar.value()} vs {hbar.maximum()}")
    sc.takeWidget()

    print("\n=== Horizontal, every day fits: the wheel steps the cameras ===")
    wall = make_wall(m, 5, 3, mode="rows")
    sc = _scroll_host(m, wall)
    hbar, vbar = sc.horizontalScrollBar(), sc.verticalScrollBar()
    check("three days fit the pane's width", hbar.maximum() == 0, str(hbar.maximum()))
    _notch(sc, -120)
    tops = wall.row_tops()
    check("a notch down moves one camera row down",
          vbar.value() == min(tops[1], vbar.maximum()), f"{vbar.value()} vs {tops[1]}")
    sc.takeWidget()


def check_hover_on_marked(m):
    print("\n=== hovering a marked tile still shows ===")
    from PySide6.QtGui import QImage, QPainter, QColor
    wall = make_wall(m, 1, 2, mode="grid")
    p0 = wall._cells[0]["path"]
    wall.set_selected(p0, True)
    wall._hover = 0
    img = QImage(PANE_W, PANE_H, QImage.Format.Format_RGB888)
    img.fill(QColor("#000000"))
    painter = QPainter(img)
    wall._paint_into(painter, wall._rects)
    painter.end()
    r = wall._rects[0]
    # The hover ring is drawn 4 px inside the blue mark on a marked tile.
    px = QColor(img.pixel(r.center().x(), r.top() + 4))
    check("the hover outline is drawn inside the mark",
          px.blue() > 200 and px.green() > 150, px.name())


def check_finder(m, app):
    print("\n=== Picture size slider and Ctrl+wheel are one zoom ===")
    w = m.ImageFinderWidget()
    w.resize(1400, 900)
    wall = w._wall
    wall.set_canvas(1000, 700)
    w._zoom_slider.setValue(200)
    check("the slider zooms the wall", abs(wall.zoom() - 2.0) < 1e-6, str(wall.zoom()))
    check("and Fit says so", "200" in w._btn_fit.text(), w._btn_fit.text())
    w._zoom_wall(wall, -2)
    check("Ctrl+wheel moves the slider", w._zoom_slider.value()
          == int(round(wall.zoom() * 100)), f"{w._zoom_slider.value()} vs {wall.zoom()}")
    w._fit_wall()
    check("Fit puts the slider back to 100", w._zoom_slider.value() == 100,
          str(w._zoom_slider.value()))
    check("the layout switch shows the remembered layout",
          w._btn_day_vert.isChecked() == (w._day_layout == "cols")
          and w._btn_day_horiz.isChecked() == (w._day_layout == "rows"))

    print("\n=== Reference day sits in the Comparison group ===")
    def _inside(widget, section):
        p = widget.parentWidget()
        while p is not None:
            if p is section:
                return True
            p = p.parentWidget()
        return False
    check("in Comparison", _inside(w._baseline_cb, w._sections["compare"]))
    check("not in Image / Display",
          not _inside(w._baseline_cb, w._sections["display"]))

    print("\n=== Detailed view: captions on top, no info line ===")
    page = w._detail_page
    lay = page.layout()
    first = lay.itemAt(0)
    check("the camera / time row comes first, above the picture",
          first.layout() is not None
          and first.layout().itemAt(0).widget() is w._detail_view["cam"])
    check("the picture is under it", lay.itemAt(1).widget() is w._detail_view["img"])
    check("no image-info line", "scale" not in w._detail_view)

    print("\n=== the camera plate jumps to another camera ===")
    import numpy as np
    from PIL import Image as PilImage
    tmp = Path(tempfile.mkdtemp(prefix="if_vert_"))
    cells = []
    for c, cam in enumerate(("CAMA", "CAMB", "CAMC")):
        d = tmp / f"{cam}-_-IMG"
        d.mkdir()
        for i in range(3):
            ts = 1_700_000_000_000_000_000 + (i * 10 + c) * 1_000_000_000
            p = d / f"{cam}-_-IMG_-_{ts}.png"
            PilImage.fromarray(np.full((60, 80), 100 * (c + 1), np.uint16)).save(p)
            cells.append({"day": date(2026, 8, 10) + timedelta(days=i), "cam": cam,
                          "cam_folder": d.name, "path": p, "ts_ns": ts,
                          "status": "found", "meta": {}})
    wall.set_cells(cells)
    end = time.monotonic() + 20         # let the background reads finish first
    while (any(c["path"] not in wall._raw for c in cells)
           and time.monotonic() < end):
        app.processEvents()
        time.sleep(0.02)
    w._preview_set_files([c["path"] for c in cells], "",
                         [c["cam"] for c in cells], index=1)   # CAMA, day 2
    menu = w._detail_view["cam_menu"]
    w._fill_cam_menu(menu)
    acts = [a for a in menu.actions() if a.isCheckable()]
    check("one entry per camera", [a.text() for a in acts] == ["CAMA", "CAMB", "CAMC"],
          repr([a.text() for a in acts]))
    check("the camera on screen is ticked", acts[0].isChecked()
          and not acts[1].isChecked())
    acts[2].trigger()
    now = cells[w._preview_idx]
    check("picking CAMC shows CAMC", now["cam"] == "CAMC", now["cam"])
    check("on the same day as before", now["day"] == cells[1]["day"],
          f"{now['day']} vs {cells[1]['day']}")
    check("the plate says so", "CAMC" in w._detail_view["cam"].text(),
          w._detail_view["cam"].text())

    print("\n=== the arrows step through ONE camera's frames ===")
    check("the counter counts that camera's frames: 2 / 3",
          w._detail_view["counter"].text() == "2 / 3", w._detail_view["counter"].text())
    w._preview_next()
    check("next stays on CAMC, next day", cells[w._preview_idx]["cam"] == "CAMC"
          and w._detail_view["counter"].text() == "3 / 3",
          f"{cells[w._preview_idx]['cam']} {w._detail_view['counter'].text()}")
    w._preview_next()
    check("and wraps round inside CAMC", cells[w._preview_idx]["cam"] == "CAMC"
          and w._detail_view["counter"].text() == "1 / 3",
          f"{cells[w._preview_idx]['cam']} {w._detail_view['counter'].text()}")
    w._preview_prev()
    check("back wraps to CAMC's last", w._detail_view["counter"].text() == "3 / 3",
          w._detail_view["counter"].text())
    check("the tab title counts the camera too",
          "(3/3)" in w._view_tabs.tabText(w._detail_tab_idx),
          w._view_tabs.tabText(w._detail_tab_idx))

    print("\n=== the layout switch sits over the Day by day pictures ===")
    results: dict = {}
    for c in cells:
        results.setdefault(c["cam_folder"], []).append(
            (c["day"], 8, c["path"], {"source": "pv"}, "found"))
    w.fill_wall(results, None)
    for _ in range(30):
        app.processEvents()
        time.sleep(0.01)
    titles = [w._view_tabs.tabText(i) for i in range(w._view_tabs.count())]
    check("not on a camera tab", w._day_strip.isHidden(), repr(titles))
    dbd = titles.index("Day by day")
    w._view_tabs.setCurrentIndex(dbd)
    app.processEvents()
    check("shown on the Day by day tab", not w._day_strip.isHidden())
    check("with both buttons in it",
          w._btn_day_horiz.parentWidget() is w._day_strip
          and w._btn_day_vert.parentWidget() is w._day_strip)
    w._view_tabs.setCurrentIndex(w._detail_tab_idx)
    app.processEvents()
    check("hidden again on the Detailed view", w._day_strip.isHidden())

    print("\n=== the PV Search window opens taller ===")
    from PySide6.QtCore import QDate
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: ([], "ok", ""))
    dlg = m.PVRegionSearchDialog([("CAMA-_-IMG", "CAMA", Path("x"))],
                                 [QDate(2026, 9, 1)], w)
    scr = w.window().screen() or app.primaryScreen()
    want = max(720, min(970, scr.availableGeometry().height() - 60))
    check("970 px tall (13.10.2026), or as tall as the screen allows",
          dlg.height() == max(720, want), f"{dlg.height()} vs {max(720, want)}")
    dlg.deleteLater()
    w.deleteLater()


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    check_vertical(m)
    check_horizontal_centred(m)
    check_horizontal_wheel(m)
    check_hover_on_marked(m)
    check_finder(m, app)
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
