"""Save view: the WHOLE view in one file, PNG or PDF, this tab or every tab.

Reported: a Day-by-day tab with many regions can only be read by scrolling, and
there was no way to get it into one file. `composite_image` already renders the
rows below the fold; what was missing was PDF and more than one tab.

Pinned here: a rows wall taller than its pane composites to the full height (so
nothing below the fold is lost), a wall on a tab that was never shown still
exports at a real size instead of a placeholder, PNG in every-tab scope writes
one file per tab, and the PDF holds one page per tab.

Offscreen, no share and no archiver — the frames are generated PNGs in a temp
folder.
"""
import sys
import tempfile
import types
from datetime import date
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def make_frame(path: Path, w=160, h=120):
    from PySide6.QtGui import QImage, QColor
    img = QImage(w, h, QImage.Format.Format_Grayscale8)
    img.fill(QColor(120, 120, 120))
    img.save(str(path), "PNG")


def build_cells(tmp: Path, n_rows: int, cams=("PTM11WNF", "PAM1FF")) -> list:
    """`n_rows` DAYS × the cameras, each day carrying one marked region.

    Days, not regions: a row on the Day-by-day wall is a day, so several days is
    what makes a wall taller than its pane."""
    from datetime import timedelta
    cells = []
    for i in range(n_rows):
        day = DAY + timedelta(days=i)
        for cam in cams:
            p = tmp / f"{cam}_{i}.png"
            if not p.exists():
                make_frame(p)
            cells.append({
                "day": day, "cam": cam, "cam_folder": cam + "-_-IMG",
                "meta": {"source": "pv"}, "path": p, "ts_ns": None,
                "status": "found", "pick": None,
                "region": {"index": 1, "count": 1, "color": "#C62828",
                           "label": "08:00:00–09:00:00",
                           "t_start_ns": 1, "t_end_ns": 2},
                "row_key": (day, 1),
            })
    return cells


class _FakeTabs:
    """Just enough QTabWidget for the export: names, the current page, and a
    viewport size to state on a wall that was never shown."""

    def __init__(self, pages):
        self._pages = pages          # [(name, widget)]
        self._cur = 0

    def tabText(self, i):
        return self._pages[i][0]

    def currentIndex(self):
        return self._cur

    def currentWidget(self):
        return self._pages[self._cur][1]


class _FakePage:
    def __init__(self, w, h):
        self._w, self._h = w, h

    def viewport(self):
        return types.SimpleNamespace(width=lambda: self._w,
                                     height=lambda: self._h)


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="wall_export_"))

    # A rows wall with four DAYS, in a pane that holds about three.
    rows_wall = m._DayWall()
    rows_wall.set_layout_mode("rows")
    rows_wall.resize(900, 300)
    rows_wall.set_canvas(900, 300)
    rows_wall.set_cells(build_cells(tmp, 4))
    rows_wall._relayout()
    B.wait_for(lambda: bool(rows_wall._raw), timeout_s=10.0)
    rows_wall._relayout()

    print("=== nothing below the fold is lost ===")
    img = rows_wall.composite_image(scale=1.0)
    check("the wall composites", img is not None)
    need = 4 * rows_wall.row_pitch()
    check("the picture is as tall as all four rows, not as the pane",
          img is not None and img.height() >= need,
          f"{img.height() if img else 0} px, needs ≥ {need}, pane 300")

    # One grid wall that was never shown — no set_canvas call.
    grid_wall = m._DayWall()
    grid_wall.set_cells(build_cells(tmp, 2, cams=("PTM11WNF",)))
    B.wait_for(lambda: bool(grid_wall._raw), timeout_s=10.0)

    pages = [("Day by day", _FakePage(900, 300)),
             ("PTM11WNF", _FakePage(900, 300))]
    tabs = _FakeTabs(pages)
    stub = types.SimpleNamespace(
        _view_tabs=tabs,
        _wall_pages={0: rows_wall, 1: grid_wall},
        _last_save_dir=tmp,
        _log=lambda msg: None)
    for name in ("_wall_provenance", "_wall_page_image",
                 "_save_view_png", "_save_view_pdf"):
        setattr(stub, name, getattr(m.ImageFinderWidget, name).__get__(stub))
    # A staticmethod is already a plain function — binding it would hand `stub`
    # in as its first argument.
    stub._provenance_line = m.ImageFinderWidget._provenance_line

    print("\n=== the caption strip says what the picture is ===")
    prov = stub._wall_provenance(rows_wall, "Day by day")
    line = stub._provenance_line(prov)
    for want in ("Day by day", "PTM11WNF", "01.09.2026", "04.09.2026",
                 "picked by PV"):
        check(f"the line names {want}", want in line, repr(line))

    print("\n=== a tab that was never shown still exports properly ===")
    check("it has no canvas of its own to begin with",
          not (getattr(grid_wall, "_fit_w", 0) > 1
               and getattr(grid_wall, "_fit_h", 0) > 1),
          f"{getattr(grid_wall, '_fit_w', 0)}×{getattr(grid_wall, '_fit_h', 0)}")
    page = stub._wall_page_image(grid_wall, "PTM11WNF")
    check("and it still produces a picture", page is not None)
    check("at the size the pane states",
          page is not None and page.width() >= 900,
          f"{page.width() if page else 0} px wide")

    print("\n=== PNG, every tab ===")
    written, failed = [], []
    stub._save_view_png(tmp / "view.png",
                        [("Day by day", rows_wall, 0), ("PTM11WNF", grid_wall, 1)],
                        written, failed)
    check("one file per tab", len(written) == 2 and not failed,
          f"{[p.name for p in written]} failed={failed}")
    check("named after the tab",
          {p.name for p in written} == {"view_Day_by_day.png", "view_PTM11WNF.png"},
          repr(sorted(p.name for p in written)))
    check("and both are real files", all(p.exists() and p.stat().st_size > 1000
                                         for p in written))

    print("\n=== PNG, one tab ===")
    written, failed = [], []
    stub._save_view_png(tmp / "one.png", [("Day by day", rows_wall, 0)],
                        written, failed)
    check("one file, under the name asked for",
          written == [tmp / "one.png"] and not failed,
          f"{written} failed={failed}")

    print("\n=== PDF, one page per tab ===")
    written, failed = [], []
    stub._save_view_pdf(tmp / "view.pdf",
                        [("Day by day", rows_wall, 0), ("PTM11WNF", grid_wall, 1)],
                        written, failed)
    check("the pdf is written", written == [tmp / "view.pdf"] and not failed,
          f"{written} failed={failed}")
    data = (tmp / "view.pdf").read_bytes() if (tmp / "view.pdf").exists() else b""
    check("it is a PDF", data[:5] == b"%PDF-", repr(data[:8]))
    n_pages = data.count(b"/Type /Page\n") or data.count(b"/Type /Page ")
    check("with two pages", n_pages == 2, f"{n_pages} page marker(s)")
    check("and it is not a stub of a file", len(data) > 5000, f"{len(data)} bytes")

    print(f"\n(files in {tmp})")
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
