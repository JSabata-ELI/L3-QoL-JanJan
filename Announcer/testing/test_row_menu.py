"""The right-click menu on a table row.

Run:  python testing/test_row_menu.py

Asked for on 18.9.2026: right-click a row in Values or Areas and get "Assign to
preset" and "Remove"; those two buttons then come off the top of the table; and
with several rows picked, both act on every one of them.

The trap the menu has to avoid is the selection. Right-clicking a row that is
ALREADY one of six picked rows must leave those six alone — a menu that quietly
resets the selection to one row would then remove one row while saying it was
about six. Right-clicking anywhere else picks that row first, so the menu is
never about something other than what was clicked.

Nothing is clicked for real and the menu is never opened: `RowMenu.rows_at`
settles the selection and `RowMenu.labels` says what the menu would read, both
ordinary function calls. Opening it for real is not testable — `QMenu.exec`
blocks on a live event loop and PySide6 will not let it be stubbed out, which
hung the first version of this file. No window is shown.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QPoint                              # noqa: E402
from PySide6.QtWidgets import (QApplication,                     # noqa: E402
                               QTableWidget, QTableWidgetItem)

_app = QApplication.instance() or QApplication([])

import ann_ui as U                                             # noqa: E402


def _table(rows=6):
    t = QTableWidget(rows, 2)
    t.setHorizontalHeaderLabels(["On", "Name"])
    for r in range(rows):
        t.setItem(r, 0, QTableWidgetItem(""))
        t.setItem(r, 1, QTableWidgetItem(f"row {r}"))
    t.resize(300, 200)
    return t


def _menu_with(entries, rows=6):
    t = _table(rows)
    return t, U.RowMenu(t, entries)


def _open_at(table, menu, row):
    """Right-click on `row` without any real mouse: the labels it would show."""
    rect = table.visualRect(table.model().index(row, 1))
    rows = menu.rows_at(QPoint(rect.center()))
    if rows is None:
        return []
    return [text for text in menu.labels(len(rows)) if text is not None]


def _selected_rows(table):
    return sorted({i.row() for i in table.selectedIndexes()})


# ── the selection ───────────────────────────────────────────────────────────

def test_right_clicking_inside_the_selection_keeps_it():
    """The whole point. Six picked rows must survive the right-click."""
    calls = []
    t, menu = _menu_with([("Remove", "trash", lambda: calls.append(1))])
    t.selectRow(0)
    for row in (2, 4):
        t.selectionModel().select(
            t.model().index(row, 0),
            t.selectionModel().SelectionFlag.Select
            | t.selectionModel().SelectionFlag.Rows)
    assert _selected_rows(t) == [0, 2, 4]
    _open_at(t, menu, 2)
    assert _selected_rows(t) == [0, 2, 4], \
        "the right-click threw the operator's selection away"
    t.deleteLater()


def test_right_clicking_outside_the_selection_picks_that_row():
    t, menu = _menu_with([("Remove", "trash", lambda: None)])
    t.selectRow(0)
    _open_at(t, menu, 3)
    assert _selected_rows(t) == [3], \
        "the menu would have been about a row nobody clicked"
    t.deleteLater()


def test_right_clicking_empty_space_offers_nothing():
    t, menu = _menu_with([("Remove", "trash", lambda: None)], rows=0)
    assert _open_at(t, menu, 0) == [], \
        "a menu appeared for a row that does not exist"
    t.deleteLater()


# ── what it says ────────────────────────────────────────────────────────────

def test_the_labels_say_how_many_rows_it_is_about():
    t, menu = _menu_with([
        (lambda n: "Remove" if n == 1 else f"Remove these {n}", "trash",
         lambda: None),
    ])
    t.selectRow(1)
    assert _open_at(t, menu, 1) == ["Remove"]
    for row in (2, 3):
        t.selectionModel().select(
            t.model().index(row, 0),
            t.selectionModel().SelectionFlag.Select
            | t.selectionModel().SelectionFlag.Rows)
    assert _open_at(t, menu, 2) == ["Remove these 3"]
    t.deleteLater()


def test_a_separator_is_not_an_action():
    t, menu = _menu_with([("Edit", "pencil", lambda: None), None,
                          ("Remove", "trash", lambda: None)])
    t.selectRow(0)
    assert _open_at(t, menu, 0) == ["Edit", "Remove"]
    assert menu.labels(1) == ["Edit", None, "Remove"]
    t.deleteLater()


def test_no_label_ends_in_an_ellipsis():
    """House rule: a button or menu label never trails off."""
    import ar_t
    import vl_t
    for mod in (vl_t, ar_t):
        src = (HERE.parent / f"{mod.__name__}.py").read_text(encoding="utf-8")
        assert "…" not in src.split("_build_row_menu")[-1][:900], \
            f"{mod.__name__}'s row menu has an ellipsis in a label"


# ── the menu outlives the building of the tab ───────────────────────────────

def test_the_menu_is_owned_by_the_table():
    """A plain Python object here would be collected and the menu would never
    appear again. It is a QObject parented to the table."""
    import gc
    t = _table()
    U.RowMenu(t, [("Remove", "trash", lambda: None)])
    gc.collect()
    from PySide6.QtCore import QObject
    kids = [c for c in t.children() if isinstance(c, U.RowMenu)]
    assert kids, "the menu was not parented to the table and can be collected"
    assert isinstance(kids[0], QObject)
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
