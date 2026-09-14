"""Day by day: A ROW IS ONE CAMERA, and FOUR tiles fill the pane.

A row is a camera and it holds that camera's days, left to right. It used to be
the other way round — a row was a day and a column a camera — which put every
camera of one day on a single horizontal line and was the whole complaint.

`_relayout_rows` also used to divide the pane width by EVERY column, so twelve
days came out as twelve slivers a couple of centimetres wide and the view
answered nothing. Four across is the operator's own measure, and it is the same
`_MAX_COLS` the grid mode obeys.

Pinned here:
  * a row is a CAMERA and a column is a DAY
  * up to four columns tile the pane edge to edge — no empty quarter for three
  * more than four ask for a wider widget, which is what raises the sideways bar
  * the row banner names the CAMERA and spans the WHOLE width, not just the
    visible part
  * the row height still fits about three rows, so the vertical bar steps cameras
  * the layout is measured against the PANE, never against the widget's own size
    (which is bigger on purpose once it scrolls — measuring itself would grow it
    again on every pass)
  * `composite_image` covers every column, including the ones off to the right

Runs offscreen, no images, no share, no network:

    python testing/test_wall_rows_wide.py
"""
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []
PANE_W, PANE_H = 1200, 700


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_finder():
    B.load_slider()
    if "image_finder" in sys.modules:
        return sys.modules["image_finder"]
    argv, sys.argv = sys.argv, ["if_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_finder", str(B.HERE / "if_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_finder"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


def ns_at(day, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=tz).timestamp() * 1e9)


def make_wall(m, n_cams: int, n_days: int = 4):
    """`n_cams` cameras × `n_days` days, one frame each — so `n_cams` ROWS and
    `n_days` COLUMNS."""
    import numpy as np
    wall = m._DayWall()
    wall.set_layout_mode("rows")
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    day0 = date(2026, 9, 1)
    cells = []
    for d in range(n_days):
        day = day0 + timedelta(days=d)
        for c in range(n_cams):
            p = Path(f"rows_{d}_{c}.png")
            wall._shared.raw[p] = (np.zeros((1024, 1280), dtype=np.float32), 4095.0)
            cells.append({"day": day, "cam": f"CAM{c:02d}",
                          "cam_folder": f"CAM{c:02d}", "path": p,
                          "ts_ns": ns_at(day, 8 + c), "status": "found",
                          "meta": {"source": "pv"}})
    wall.set_cells(cells)
    wall._relayout()
    return wall


def cols_of(wall) -> int:
    return len({r.x() for r in wall._rects})


def rows_of(wall) -> int:
    return len({r.y() for r in wall._rects})


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    print("\n=== a row is a camera, a column is a day ===")
    # The operator's case: five cameras over five days is a five-by-five grid,
    # four of the days in the pane and the fifth reached sideways.
    wall = make_wall(m, 5, n_days=5)
    check("5 cameras make 5 rows", rows_of(wall) == 5, f"{rows_of(wall)} row(s)")
    check("5 days make 5 columns", cols_of(wall) == 5, f"{cols_of(wall)} column(s)")
    check("one banner per camera, naming it",
          len(wall._row_heads) == 5 and wall._row_heads[0][1].startswith("CAM00"),
          f"{len(wall._row_heads)} banner(s), first {wall._row_heads[0][1]!r}")
    check("four days in the pane, the fifth off to the right",
          abs(wall.minimumWidth() - PANE_W * 5 / 4) <= 4,
          f"minimum width {wall.minimumWidth()} vs {int(PANE_W * 5 / 4)}")
    # Each camera's own line: the same five x positions, one y.
    by_cam: dict = {}
    for c, r in zip(wall._cells, wall._rects):
        by_cam.setdefault(c["cam"], []).append(r)
    check("every camera's frames sit on one line",
          all(len({r.y() for r in rs}) == 1 for rs in by_cam.values()),
          repr({k: sorted({r.y() for r in v}) for k, v in list(by_cam.items())[:2]}))
    check("and every line carries the same five days",
          all(len({r.x() for r in rs}) == 5 for rs in by_cam.values()))

    print("\n=== up to four across, filling the pane ===")
    for n in (1, 2, 3, 4):
        wall = make_wall(m, 2, n_days=n)
        widths = {r.width() for r in wall._rects}
        span = max(r.x() + r.width() for r in wall._rects)
        check(f"{n} day(s): {cols_of(wall)} across", cols_of(wall) == n,
              f"{cols_of(wall)} column(s)")
        check(f"{n} day(s): the row fills the pane edge to edge",
              abs(span - PANE_W) <= 2, f"{span} of {PANE_W}")
        check(f"{n} day(s): no sideways scrolling", wall.minimumWidth() == 0,
              f"minimum width {wall.minimumWidth()}")
        check(f"{n} day(s): one tile size", len(widths) <= 2, repr(sorted(widths)))

    print("\n=== more than four scroll sideways ===")
    wall = make_wall(m, 4, n_days=12)
    check("twelve days are laid out", cols_of(wall) == 12,
          f"{cols_of(wall)} column(s) laid out")
    cw = wall.row_col_width()
    check("a column is a quarter of the pane", abs(cw - PANE_W / 4) <= 1,
          f"{cw:.1f} px vs {PANE_W / 4:.1f}")
    check("and the widget asks to be three panes wide",
          abs(wall.minimumWidth() - PANE_W * 3) <= 4,
          f"minimum width {wall.minimumWidth()}")
    check("which is what raises the sideways bar",
          wall.minimumWidth() > PANE_W, f"{wall.minimumWidth()} vs {PANE_W}")

    print("\n=== the camera banner spans the whole width ===")
    heads = wall._row_heads
    check("one banner per camera", len(heads) == 4, f"{len(heads)} banner(s)")
    check("each spans every column, not just the visible part",
          all(r.width() >= wall.minimumWidth() for r, _t in heads),
          repr([r.width() for r, _t in heads]))
    check("the banner names the camera", heads[0][1].startswith("CAM00"),
          repr(heads[0][1]))
    check("and says how many days its line reaches across",
          "12 days" in heads[0][1], repr(heads[0][1]))

    print("\n=== the rows step cameras ===")
    check("about three rows fit the pane",
          abs(wall.row_pitch() - PANE_H // 3) <= 3,
          f"pitch {wall.row_pitch()} px, pane {PANE_H}")
    check("and the wall asks for the height all four need",
          wall.minimumHeight() == 4 * wall.row_pitch(),
          f"{wall.minimumHeight()} vs {4 * wall.row_pitch()}")
    ys = sorted({r.y() for r in wall._rects})
    check("each camera sits below the previous one", len(ys) == 4, repr(ys))

    print("\n=== the pane's size is what is measured, never the widget's ===")
    # This is the trap: once the wall is wider than its pane, Qt grows the WIDGET
    # to the minimum. A layout that then measures `self.width()` would compute a
    # quarter of THAT, and grow again on the next pass, for ever.
    wall.resize(wall.minimumWidth(), wall.minimumHeight())
    first = wall.minimumWidth()
    for _ in range(5):
        wall._relayout()
    check("five more passes change nothing", wall.minimumWidth() == first,
          f"{wall.minimumWidth()} vs {first}")

    print("\n=== nothing off to the right is lost from the saved picture ===")
    wall.resize(PANE_W, PANE_H)
    wall.set_canvas(PANE_W, PANE_H)
    wall._relayout()
    img = wall.composite_image(scale=1.0)
    check("the wall composites", img is not None)
    if img is not None:
        need_w = max(r.x() + r.width() for r in wall._rects)
        check("the picture is as wide as every column, not as the pane",
              img.width() >= need_w,
              f"{img.width()} px, needs >= {need_w}, pane {PANE_W}")
        check("and as tall as all four rows",
              img.height() >= 4 * wall.row_pitch(),
              f"{img.height()} px, needs >= {4 * wall.row_pitch()}")

    print("\n=== the saved picture is sized off the FRAMES, not a fixed 2x ===")

    def drawn_width(w, idx: int) -> float:
        """The room the PICTURE gets inside a tile, aspect honoured — which is what
        the export factor has to make up to the frame's own pixel width."""
        r = w._rects[idx]
        pw = max(1, r.width() - 2 * w._GAP)
        ph = max(1, r.height() - w._CAPTION_H - 2 * w._GAP)
        return max(1.0, min(float(pw), ph * (1280.0 / 1024.0)))

    # A small wall: nothing is capped, so the rule itself can be read off it.
    small = make_wall(m, 1, n_days=2)
    f_small = small.export_scale()
    want = 1280.0 / drawn_width(small, 0)
    check("it asks for exactly what puts the frame on screen 1:1",
          abs(f_small - want) < 0.05, f"{f_small:.2f}x vs {want:.2f}x")
    check("which is more than the old fixed 2x", f_small > 2.0, f"{f_small:.2f}x")

    # The big wall wants even more, and there the ceiling is what answers.
    factor = wall.export_scale()
    px = wall.minimumWidth() * wall.minimumHeight()
    check("a 48-tile wall wants more than the ceiling allows",
          1280.0 / drawn_width(wall, 0) > factor,
          f"wanted {1280.0 / drawn_width(wall, 0):.2f}x, got {factor:.2f}x")
    check("so it is capped, and one save cannot ask for a gigabyte",
          px * factor * factor <= wall._EXPORT_MAX_PX * 1.01,
          f"{px * factor * factor / 1e6:.0f} Mpx")
    check("but never below the old 2x", factor >= 2.0, f"{factor:.2f}x")

    print("\n=== a click still lands on the tile it is over ===")
    hit_ok = all(wall._hit(r.center()) == i for i, r in enumerate(wall._rects))
    check("hit-testing reaches every column, scrolled or not", hit_ok)

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
