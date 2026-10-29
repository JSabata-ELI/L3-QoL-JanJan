"""
test_table.py  --  the Table tab: layout, pixel-exact pictures, shape changes,
templates, and the tab inside the program.

Covers: two 1920 x 1080 pictures side by side giving exactly a 3840 x 1080
picture area with every pixel copied unchanged (with and without headings);
a smaller picture centred and never cropped; a smaller picture in a wider
column enlarged to fill it; swapping rows and columns; a smaller table hiding
cells rather than losing them; filling growing the table; the template file
round trip; and, in the real widget, a pending edit (a quarter turn) reaching
the saved table, "Add to table", and a rename on Save carried into the table.

Runs on the minimal platform with the real fonts: nothing is shown on screen.

    python testing/test_table.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QPointF, QRect, QRectF                 # noqa: E402
from PySide6.QtGui import (QColor, QImage, QLinearGradient,       # noqa: E402
                           QPainter)
from PySide6.QtWidgets import QApplication                        # noqa: E402

import pr_t                                                        # noqa: E402
from pr_t import EditOp                                            # noqa: E402

_FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        _FAILS.append(what)


def _picture(w: int, h: int, a: str, b: str) -> QImage:
    """A picture with no two neighbouring columns alike: a diagonal gradient
    with a few blocks on it, so any shift or resampling shows."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    g = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    g.setColorAt(0.0, QColor(a))
    g.setColorAt(1.0, QColor(b))
    p = QPainter(img)
    p.fillRect(QRectF(0, 0, w, h), g)
    for i in range(12):
        p.fillRect(QRect((i * 157) % w, (i * 89) % h, 37, 23),
                   QColor((i * 40) % 256, (i * 90) % 256, (i * 20) % 256))
    p.end()
    return img


def _same(a: QImage, b: QImage) -> bool:
    a = a.convertToFormat(QImage.Format.Format_RGB32)
    b = b.convertToFormat(QImage.Format.Format_RGB32)
    return a.size() == b.size() and a == b


def _render(tb, spec, pics: dict) -> tuple:
    sizes = {k: (v[1], v[2]) for k, v in pics.items()}
    lay = tb.measure_table(spec, sizes)
    return lay, tb.render_table(spec, lay, pics)


def test_pixels_exact(tb) -> None:
    print("two 1920 x 1080 side by side: 3840 x 1080 of untouched pixels")
    a = _picture(1920, 1080, "#c03020", "#203060")
    b = _picture(1920, 1080, "#20a040", "#f0e020")
    pics = {"a": (a, 1920, 1080), "b": (b, 1920, 1080)}
    spec = tb.TableSpec(rows=1, cols=2, cells=[["a", "b"]], col_heads=False,
                        row_heads=False, margin_pct=0)
    lay, img = _render(tb, spec, pics)
    check(sum(lay.col_w) == 3840 and lay.row_h == [1080],
          f"picture area {sum(lay.col_w)} x {lay.row_h[0]}")
    check(img.width() == 3840 + 3 * lay.lw and img.height() == 1080 + 2 * lay.lw,
          f"whole picture {img.width()} x {img.height()} = area + {lay.lw} px lines")
    check(_same(img.copy(lay.picture_rect(0, 0)), a), "left picture copied pixel"
          " for pixel")
    check(_same(img.copy(lay.picture_rect(0, 1)), b), "right picture copied pixel"
          " for pixel")

    spec = tb.TableSpec(rows=1, cols=2, cells=[["a", "b"]], corner="Camera",
                        col_labels=["WP 360k\nBoth heads ON", "WP 800k\nAll PCs"],
                        row_labels=["C03-079-PCM2NF"], title="Near field",
                        notes="Measured 21.10.2026", margin_pct=2)
    lay, img = _render(tb, spec, pics)
    check(_same(img.copy(lay.picture_rect(0, 0)), a)
          and _same(img.copy(lay.picture_rect(0, 1)), b),
          "with headings, title, notes and a margin: still pixel for pixel")
    check(lay.head_h > 0 and lay.head_w > 0 and lay.title_h > 0 and lay.notes_h > 0,
          f"heading row {lay.head_h} px, heading column {lay.head_w} px, title"
          f" {lay.title_h} px, notes {lay.notes_h} px")
    head = img.pixelColor(lay.head_x + 2, lay.head_y + 2).name()
    check(head == tb._HEAD_BG, f"heading band is {head}")


def test_mixed_sizes(tb) -> None:
    print("mixed sizes: centred, never cropped, smaller enlarged to fit")
    big = _picture(1920, 1080, "#c03020", "#203060")
    small = _picture(960, 540, "#20a040", "#f0e020")
    pics = {"big": (big, 1920, 1080), "small": (small, 960, 540)}
    # One row: the small one keeps its size and sits in the middle.
    spec = tb.TableSpec(rows=1, cols=2, cells=[["big", "small"]],
                        col_heads=False, row_heads=False, margin_pct=0)
    lay, img = _render(tb, spec, pics)
    box = lay.picture_rect(0, 1)
    check(box.width() == 960 and box.height() == 1080,
          f"its cell is {box.width()} x {box.height()}")
    top = box.top() + (1080 - 540) // 2
    check(_same(img.copy(QRect(box.left(), top, 960, 540)), small),
          "small picture centred top to bottom, pixel for pixel")
    check(img.pixelColor(box.left() + 5, box.top() + 5).name() == "#ffffff",
          "white above it")
    # One column: its row is only as tall as the small one, so it stays 1:1,
    # centred across the wide column.
    spec = tb.TableSpec(rows=2, cols=1, cells=[["big"], ["small"]],
                        col_heads=False, row_heads=False, margin_pct=0)
    lay, img = _render(tb, spec, pics)
    box = lay.picture_rect(1, 0)
    left = box.left() + (1920 - 960) // 2
    check(box.size().width() == 1920 and box.height() == 540
          and _same(img.copy(QRect(left, box.top(), 960, 540)), small),
          "in a wider column: centred side to side, pixel for pixel")
    # A cell larger both ways (wide row from another column, wide column
    # from another row): the small picture is enlarged to fill it.
    spec = tb.TableSpec(rows=2, cols=2, cells=[["big", ""], ["", "small"]],
                        col_heads=False, row_heads=False, margin_pct=0)
    pics["big2"] = (big, 1920, 1080)
    spec.cells = [["big", "big2"], ["big2", "small"]]
    lay, img = _render(tb, spec, pics)
    box = lay.picture_rect(1, 1)
    corners = [img.pixelColor(box.left() + 1, box.top() + 1),
               img.pixelColor(box.right() - 1, box.bottom() - 1)]
    check(box.width() == 1920 and all(c.name() != "#ffffff" for c in corners),
          "in a cell larger both ways: enlarged to fill it, corners covered")


def test_shape(tb) -> None:
    print("shape: swap, shrink and grow, fill")
    s = tb.TableSpec(rows=2, cols=3, corner="X",
                     col_labels=["c1", "c2", "c3"], row_labels=["r1", "r2"],
                     cells=[["a", "b", "c"], ["d", "e", "f"]], col_heads=True,
                     row_heads=False)
    s.transpose()
    check((s.rows, s.cols) == (3, 2), "3 x 2 after the swap")
    check(s.cells == [["a", "d"], ["b", "e"], ["c", "f"]], f"cells {s.cells}")
    check(s.row_labels == ["c1", "c2", "c3"] and s.col_labels == ["r1", "r2"],
          "headings went with them")
    check(s.row_heads and not s.col_heads, "shown / hidden headings swapped too")
    s.transpose()
    s.resize(1, 1)
    s.resize(2, 3)
    check(s.cells == [["a", "b", "c"], ["d", "e", "f"]],
          "making it smaller and back loses nothing")
    s.resize(1, 2)
    check(s.to_dict()["cells"] == [["a", "b"]], "only the visible part is saved")
    s = tb.TableSpec(rows=1, cols=2)
    n = s.place(["p1", "p2", "p3"])
    check(n == 3 and (s.rows, s.cols) == (2, 2) and s.cells[1][0] == "p3",
          "filling along rows adds a row when full")
    s = tb.TableSpec(rows=2, cols=1)
    s.place(["p1", "p2", "p3"], by_columns=True)
    check((s.rows, s.cols) == (2, 2) and s.cells[0][1] == "p3",
          "filling down columns adds a column when full")
    s = tb.TableSpec(rows=1, cols=3, cells=[["x", "", ""]])
    s.place(["y"], only_empty=True)
    check(s.cells[0] == ["x", "y", ""], "Add to table uses the next empty cell")
    s = tb.TableSpec(rows=2, cols=2, cells=[["a", "b"], ["c", "d"]])
    s.delete_col(0)
    s.insert_row(0)
    check(s.cells == [[""], ["b"], ["d"]], f"insert / delete rows and columns:"
          f" {s.cells}")


def test_template(tb) -> None:
    print("template file")
    s = tb.TableSpec(rows=2, cols=2, corner="cam", col_labels=["A\nB", "C"],
                     row_labels=["r1", "r2"], cells=[["p", ""], ["", "q"]],
                     title="T", notes="N", col_heads=False, text_pct=150,
                     margin_pct=5, size_pct=40)
    text = json.dumps(s.to_dict())
    back = tb.TableSpec.from_dict(json.loads(text))
    check(back.to_dict() == s.to_dict(), "comes back the same")
    check(tb.TableSpec.from_dict(s.to_dict(pictures=False)).paths() == [],
          "a template without pictures has none")
    junk = tb.TableSpec.from_dict({"rows": "x", "cols": 999, "cells": 5,
                                   "col_labels": [1, None]})
    check((junk.rows, junk.cols) == (2, tb.MAX_COLS) and junk.col_labels[0] == "",
          "a damaged file gives defaults, not a crash")


def test_size_pdf_parts(tb) -> None:
    print("size %, PDF at full size, saving in parts")
    a = _picture(1000, 800, "#c03020", "#203060")
    pics = {f"p{i}": (a, 1000, 800) for i in range(9)}
    sizes = {k: (v[1], v[2]) for k, v in pics.items()}
    spec = tb.TableSpec(rows=3, cols=3,
                        cells=[[f"p{r * 3 + c}" for c in range(3)]
                               for r in range(3)],
                        col_labels=["a", "b", "c"], row_labels=["1", "2", "3"],
                        title="Grid", size_pct=50)
    lay = tb.measure_table(spec, sizes)
    img = tb.render_table(spec, lay, pics, tb.out_scale(spec, lay))
    check((img.width(), img.height()) == (round(lay.width / 2),
                                          round(lay.height / 2)),
          f"50 %: {img.width()} x {img.height()} of {lay.width} x {lay.height}")
    check("50 %" in tb.size_text(spec, lay), tb.size_text(spec, lay))
    spec.size_pct = 100
    check(tb.size_text(spec, lay) == f"{lay.width} × {lay.height} px",
          "100 %: only the full size is shown")
    big = tb.TableLayout(width=43200, height=32400)
    s = tb.out_scale(spec, big)
    check(abs(s - tb.fit_scale(big)) < 1e-12 and s < 0.6,
          f"100 % of 43200 x 32400 is held to the most one picture can hold"
          f" ({tb.size_text(spec, big)})")

    tmp = Path(tempfile.mkdtemp(prefix="pr_pdf_"))
    try:
        target = tmp / "grid.pdf"
        check(tb.write_pdf(spec, lay, pics, str(target)) and target.is_file(),
              "PDF written")
        data = target.read_bytes() if target.is_file() else b""
        check(data.startswith(b"%PDF") and b"/Width 1000" in data
              and b"/Height 800" in data,
              f"PDF holds the photos at full size ({len(data)} bytes)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    old_side, old_bytes = tb._MAX_SIDE, tb._MAX_BYTES
    try:
        tb._MAX_SIDE = 2500          # two columns of 1000 px fit, three not
        tb._MAX_BYTES = 4 * 2500 * 2000
        parts = tb.split_table(spec, sizes)
        laid = [tb.measure_table(p, sizes) for p in parts]
        check(all(tb.fit_scale(x) >= 1.0 for x in laid),
              f"{len(parts)} parts, each at full size:"
              f" {[(x.width, x.height) for x in laid]}")
        seen = sorted(p for part in parts for p in part.paths())
        check(seen == sorted(pics), "every photo in exactly one part")
        check(all(p.col_labels and p.row_labels for p in parts)
              and parts[0].title.startswith("Grid - part 1 of"),
              f"headings and title on every part ('{parts[0].title}')")
    finally:
        tb._MAX_SIDE, tb._MAX_BYTES = old_side, old_bytes


def test_in_program(tb) -> None:
    print("inside the program")
    pr_t.load_ui_state = lambda: {}
    pr_t.save_ui_state = lambda state: None
    pr_t._remember = lambda key, value: None
    tmp = Path(tempfile.mkdtemp(prefix="pr_table_"))
    try:
        _picture(800, 300, "#c03020", "#203060").save(str(tmp / "wide.png"))
        _picture(400, 700, "#20a040", "#f0e020").save(str(tmp / "tall.png"))
        w = pr_t.PhotoRenamerWidget()
        w.open_folder(tmp)
        tab = w._table
        check(w._tabs.count() == 2 and w._tabs.tabText(1) == "Table",
              "a Table tab next to Photos")
        tab.spec = tb.TableSpec(rows=1, cols=1, col_heads=False, row_heads=False,
                                margin_pct=0)
        row = next(i for i, it in enumerate(w._items) if it.orig_name == "wide.png")
        w._list.setCurrentRow(row)
        w.add_selection_to_table(row)
        check(tab.spec.cells[0][0] == str(tmp / "wide.png"),
              "Add to table puts the picture in")
        w.add_selection_to_table(row)
        check((tab.spec.rows, tab.spec.cols) == (2, 1)
              and tab.spec.cells[1][0] == str(tmp / "wide.png"),
              "a full table grows a row for the next one")
        tab.spec.resize(1, 1)
        # A pending quarter turn: the saved table must show it turned.
        w._preview_path = w._items[row].path
        w.add_edit(EditOp(kind="rotate", turn=1))
        img = tab.build_full_image()
        check(img is not None, "the full table is made")
        if img is not None:
            lay = tb.measure_table(tab.spec, {tab.spec.cells[0][0]: (300, 800)})
            check((img.width(), img.height()) == (lay.width, lay.height)
                  and lay.col_w == [300] and lay.row_h == [800],
                  f"pending edit included: {img.width()} x {img.height()}")
        # Save renames the file; the table follows the new name.
        w.reset_edits()
        w.rename_item(row, "wide_renamed")
        w._chk_quick.setChecked(True)
        w._save()
        check(tab.spec.cells[0][0] == str(tmp / "wide_renamed.png"),
              "after a rename on Save the table points at the new name")
        w.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    import tb_t as tb
    test_pixels_exact(tb)
    test_mixed_sizes(tb)
    test_shape(tb)
    test_template(tb)
    test_size_pdf_parts(tb)
    test_in_program(tb)
    del app
    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILED")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
