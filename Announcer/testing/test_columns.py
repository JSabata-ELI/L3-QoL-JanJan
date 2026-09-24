"""Column widths: as wide as the widest cell, and still draggable.

Run:  python testing/test_columns.py

Two complaints from 18.9.2026, both about the same thing. "Say when it fires"
could not be widened by hand at all, and the limit columns — Lo Lo, Lo, Hi, Hi
Hi — were not as wide as the numbers in them. The cause was Qt's two automatic
modes, and neither of them can do the job:

  * `ResizeToContents` sizes the column right and then REFUSES to be dragged.
  * `Stretch` hands the column the room that is left over and ignores what is
    in it, which is how the heading came out as "Say when it fire".

`U.ColumnFitter` uses `Interactive` — the only mode that can be dragged — and
computes the widths itself. The third thing it has to get right is spans: Qt's
own `resizeColumnToContents` measures spanned cells too, so the Watch table's
full-width heading line would have made the tick-box column as wide as a
sentence.

No window is shown.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import (QApplication, QHeaderView,      # noqa: E402
                               QTableWidget, QTableWidgetItem)

_app = QApplication.instance() or QApplication([])

import ann_ui as U                                             # noqa: E402


def _table(rows=3, cols=3):
    t = QTableWidget(rows, cols)
    t.setHorizontalHeaderLabels([f"H{c}" for c in range(cols)])
    for r in range(rows):
        for c in range(cols):
            t.setItem(r, c, QTableWidgetItem(f"r{r}c{c}"))
    return t


def test_every_column_can_be_dragged():
    t = _table()
    U.ColumnFitter(t)
    hh = t.horizontalHeader()
    for col in range(t.columnCount()):
        assert hh.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive, \
            f"column {col} cannot be dragged"
    assert not hh.stretchLastSection(), \
        "the last column would ignore its own contents"
    t.deleteLater()


def test_a_long_cell_widens_its_column():
    t = _table()
    fit = U.ColumnFitter(t)
    fit.fit()
    narrow = t.columnWidth(1)
    t.item(1, 1).setText("a sentence far longer than any heading here")
    fit.fit()
    assert t.columnWidth(1) > narrow, \
        "the column did not follow the widest cell in it"
    t.deleteLater()


def test_a_spanned_row_does_not_widen_the_first_column():
    """The Watch table's heading line, and the empty-table note."""
    t = _table()
    fit = U.ColumnFitter(t)
    fit.fit()
    was = t.columnWidth(0)
    t.setItem(0, 0, QTableWidgetItem(
        "Watched · in range   (14) — a whole line across every column"))
    t.setSpan(0, 0, 1, t.columnCount())
    fit.fit()
    assert t.columnWidth(0) == was, \
        "a cell spanning the table was counted as this column's contents"
    t.deleteLater()


def test_a_width_the_operator_set_himself_is_kept():
    t = _table(cols=4)
    fit = U.ColumnFitter(t)
    fit.fit()
    # What dragging the divider does, in one call.
    t.horizontalHeader().resizeSection(1, 400)
    fit.fit()
    assert t.columnWidth(1) == 400, "his own width was thrown away on a reload"
    # ...and the others still follow their contents. Column 2, not the last
    # one: the last one is the one that swallows the room left over.
    t.item(2, 2).setText("something much longer than r2c2")
    before = t.columnWidth(2)
    fit.fit()
    assert t.columnWidth(2) > before
    fit.reset()
    assert t.columnWidth(1) != 400, "reset() must give them all back"
    t.deleteLater()


def test_no_empty_strip_at_the_right_edge():
    """The room left over goes to the last column, at any window width."""
    t = _table()
    t.resize(900, 300)
    fit = U.ColumnFitter(t)
    fit.fit()
    used = sum(t.columnWidth(c) for c in range(t.columnCount()))
    assert used >= t.viewport().width(), \
        f"{t.viewport().width() - used} px of empty grey at the right edge"
    # Narrower again, and the last column gives the room back instead of
    # pushing a horizontal scroll bar out.
    t.resize(500, 300)
    fit.fit()
    used = sum(t.columnWidth(c) for c in range(t.columnCount()))
    assert abs(used - t.viewport().width()) <= 2, \
        "the last column did not follow the window back in"
    t.deleteLater()


def test_the_last_column_is_never_cut_to_fill():
    """A narrow window must scroll, not chop the last column's contents."""
    t = _table()
    t.resize(900, 300)
    fit = U.ColumnFitter(t)
    t.item(0, 2).setText("a last column with a very long sentence in it "
                         "that is far wider than this whole window is")
    fit.fit()
    t.resize(200, 300)
    fit.fit()
    assert t.columnWidth(2) >= fit._width_for(2), \
        "the last column was cut below what is written in it"
    t.deleteLater()


def test_the_heading_is_never_cut_off():
    t = QTableWidget(1, 2)
    t.setHorizontalHeaderLabels(["Say when it fires", "x"])
    t.setItem(0, 0, QTableWidgetItem("hi"))
    t.setItem(0, 1, QTableWidgetItem("hi"))
    fit = U.ColumnFitter(t)
    fit.fit()
    hint = t.horizontalHeader().sectionSizeHint(0)
    assert t.columnWidth(0) >= hint, "the heading does not fit its own column"
    t.deleteLater()


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            bad += 1
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
