"""Picking several rows: Remove has to take all of them.

Run:  python testing/test_multi_select.py

The tables have always let several rows be selected — the selection behaviour
is whole-row and the mode is Qt's default, which is extended. But every button
asked `_selected()`, which answered None unless EXACTLY ONE row was picked, so
selecting three rows greyed out the lot. From the operator's side that is
indistinguishable from the program having lost the selection.

Remove now works on one or more; Edit, Duplicate, "Take the picture again" and
the saved-rectangle buttons still need exactly one, because each of them is a
question about a single row.

Nothing is clicked. The selection is made with `selectRow`/`setCurrentCell`,
which are the table's own API, and Remove is driven by calling `_remove` with
the confirmation box answered by a stub. No OS input is injected.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QItemSelectionModel, Qt            # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox        # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_core as C                                           # noqa: E402

# The operator's own settings must never be in reach of a test.
_tmp = Path(tempfile.mkdtemp(prefix="announcer_multi_"))
C.config_path = lambda: _tmp / "presets.json"

import ar_t                                                    # noqa: E402
import vl_t                                                    # noqa: E402
from main import AnnouncerWindow                               # noqa: E402

_win = AnnouncerWindow()
# Visible to Qt, but never mapped onto the desk. Half of what is tested here
# only runs when the tab is visible — the preview refuses to work on a hidden
# tab, on purpose — and a test must not throw a window in the operator's face
# to find that out.
_win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
_win.show()
_app.processEvents()


def _tab(cls):
    for i in range(_win._tabs.count()):
        if isinstance(_win._tabs.widget(i), cls):
            return _win._tabs.widget(i)
    raise AssertionError(f"{cls.__name__} is not in the window")


def _select_rows(tab, rows):
    """Pick these rows, the way Ctrl-clicking them would."""
    table = tab._table
    table.clearSelection()
    model = table.selectionModel()
    for r in rows:
        model.select(table.model().index(r, 0),
                     QItemSelectionModel.SelectionFlag.Select
                     | QItemSelectionModel.SelectionFlag.Rows)
    _app.processEvents()


def _answer_yes(fn):
    """Run `fn` with the confirmation box answering Yes, then put it back."""
    real = QMessageBox.question
    QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        return fn()
    finally:
        QMessageBox.question = real


def _reset_items(n=6):
    """A clean list of n values, and the tables rebuilt from it."""
    items = _win.items()
    items[:] = [C.new_value_item(i + 1, f"row {i + 1}", pv=f"PV:{i + 1}")
                for i in range(n)]
    _win.refresh_tabs()
    _app.processEvents()
    return items


# ── the Values tab ──────────────────────────────────────────────────────────

def test_values_remove_is_offered_for_several_rows():
    """Remove is on the rows' right-click menu AS WELL AS on the button row.

    With three rows picked, the menu offers to remove all three and SAYS so.
    """
    tab = _tab(vl_t.ValuesTab)
    _reset_items()
    _select_rows(tab, [0, 2, 4])
    said = tab._row_menu.labels(3)
    assert "Remove these 3" in said, said
    assert "Assign these 3 to a preset" in said, said
    one = tab._row_menu.labels(1)
    assert "Remove" in one and "Assign to a preset" in one, one


def test_values_edit_still_needs_exactly_one():
    tab = _tab(vl_t.ValuesTab)
    _reset_items()
    _select_rows(tab, [0, 2])
    tab._sync_buttons()
    assert not tab._btn_edit.isEnabled(), "Edit cannot mean three rows at once"
    assert not tab._btn_dup.isEnabled()
    _select_rows(tab, [1])
    tab._sync_buttons()
    assert tab._btn_edit.isEnabled()


def test_values_remove_takes_every_selected_row():
    tab = _tab(vl_t.ValuesTab)
    items = _reset_items()
    _select_rows(tab, [0, 2, 4])
    _answer_yes(tab._remove)
    left = [it["name"] for it in items]
    assert left == ["row 2", "row 4", "row 6"], left


def test_values_remove_is_a_button_and_it_deletes():
    """The button, not the menu entry — reported on 2026-09-21 as "the Remove
    button does not delete the value". It has to be there, be live for one row
    or several, and take every picked row off the list."""
    tab = _tab(vl_t.ValuesTab)
    items = _reset_items()
    _select_rows(tab, [0, 1])
    tab._sync_buttons()
    assert tab._btn_remove.isEnabled(), "Remove is dead with two rows picked"
    _answer_yes(tab._btn_remove.click)
    left = [it["name"] for it in items]
    assert left == ["row 3", "row 4", "row 5", "row 6"], left


def test_the_remove_button_is_dead_with_nothing_picked():
    for tab in (_tab(vl_t.ValuesTab), _tab(ar_t.AreasTab)):
        tab._table.clearSelection()
        _app.processEvents()
        assert not tab._btn_remove.isEnabled(), \
            f"{type(tab).__name__}: Remove is live with nothing picked"


def test_values_remove_with_nothing_picked_does_nothing():
    tab = _tab(vl_t.ValuesTab)
    items = _reset_items()
    tab._table.clearSelection()
    _answer_yes(tab._remove)
    assert len(items) == 6


def test_values_remove_takes_the_row_picked_and_not_its_twin():
    """Two rows watching the same thing are EQUAL dicts. Removing by value
    would take the first one and leave the selected one behind."""
    tab = _tab(vl_t.ValuesTab)
    items = _win.items()
    a = C.new_value_item(1, "chiller", pv="PV:1")
    b = C.new_value_item(2, "chiller", pv="PV:1")
    items[:] = [a, b]
    _win.refresh_tabs()
    _app.processEvents()
    _select_rows(tab, [1])              # the SECOND one
    _answer_yes(tab._remove)
    assert len(items) == 1
    assert int(items[0]["id"]) == 1, "it took the wrong row off the list"


# ── the Areas tab ───────────────────────────────────────────────────────────

def _reset_areas(n=4):
    items = _win.items()
    items[:] = [C.new_area_item(i + 1, f"area {i + 1}",
                                region=[10 * i, 10 * i, 10 * i + 40,
                                        10 * i + 30])
                for i in range(n)]
    _win.refresh_tabs()
    _app.processEvents()
    return items


def test_areas_remove_is_offered_for_several_rows():
    tab = _tab(ar_t.AreasTab)
    _reset_areas()
    _select_rows(tab, [0, 1])
    said = tab._row_menu.labels(2)
    assert "Remove these 2" in said, said
    assert "Assign these 2 to a preset" in said, said


def test_areas_the_one_row_buttons_still_need_one_row():
    tab = _tab(ar_t.AreasTab)
    _reset_areas()
    _select_rows(tab, [0, 1])
    tab._on_selection()
    for name, btn in (("Edit", tab._btn_edit),
                      ("Take the picture again", tab._btn_snap),
                      ("Save the rectangle", tab._btn_p_save)):
        assert not btn.isEnabled(), f"{name} cannot mean two areas at once"


def test_areas_remove_takes_every_selected_row():
    tab = _tab(ar_t.AreasTab)
    items = _reset_areas()
    _select_rows(tab, [1, 3])
    _answer_yes(tab._remove)
    left = [it["name"] for it in items]
    assert left == ["area 1", "area 3"], left


def test_areas_remove_is_a_button_and_it_deletes():
    tab = _tab(ar_t.AreasTab)
    items = _reset_areas()
    _select_rows(tab, [0, 2])
    tab._on_selection()
    assert tab._btn_remove.isEnabled(), "Remove is dead with two areas picked"
    _answer_yes(tab._btn_remove.click)
    left = [it["name"] for it in items]
    assert left == ["area 2", "area 4"], left


def test_the_preview_says_how_many_are_picked_rather_than_nothing():
    """"Nothing selected" while three rows are highlighted is the moment the
    operator decides the program has lost the plot."""
    tab = _tab(ar_t.AreasTab)
    _reset_areas()
    _win._tabs.setCurrentWidget(tab)
    _app.processEvents()
    _select_rows(tab, [0, 1, 2])
    tab._refresh_preview()
    said = tab._pv_now.text()
    assert "3" in said, f"the preview says {said!r}"


# ── the names in the question ───────────────────────────────────────────────

def test_the_question_names_what_is_about_to_go():
    import ann_ui as U
    rows = [{"name": "helium"}, {"name": "seeder"}, {}]
    text = U.name_list(rows, "a value")
    assert "helium" in text and "seeder" in text
    assert "a value" in text, "an unnamed row must still be listed"
    assert text.count("\n") == 2


def test_a_long_selection_is_cut_off_with_a_count():
    import ann_ui as U
    rows = [{"name": f"row {i}"} for i in range(30)]
    text = U.name_list(rows, "a value", most=5)
    assert "row 0" in text and "row 4" in text
    assert "row 9" not in text
    assert "25 more" in text, text


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
