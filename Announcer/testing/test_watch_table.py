"""The Watch tab's table: three blocks, headings, and what moves when.

Run:  python testing/test_watch_table.py

No window is shown and nothing is rendered — the table is built and read back
cell by cell. A stub engine stands in for the real one so each verdict can be
set by hand.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QObject, Signal        # noqa: E402
from PySide6.QtWidgets import QApplication        # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_core as C                              # noqa: E402
from wa_t import WatchTab, _G_TITLES, _G_OK, _G_BAD, _G_OFF   # noqa: E402


class _State:
    def __init__(self, state=C.STATE_OK, text="", value=1.0, fired=False):
        self.state = state
        self.text = text
        self.value = value
        self.fired = fired
        self.diff = None
        self.hint = ""


class StubEngine(QObject):
    """items() and state_of() under our control, nothing else running."""

    changed = Signal()
    logged = Signal(str, object)

    def __init__(self, items, states):
        super().__init__()
        self._items = items
        self._states = states
        self.watching = True

    def items(self):
        return self._items

    def state_of(self, iid):
        return self._states.get(int(iid), _State())

    def item_by_id(self, iid):
        for it in self._items:
            if int(it["id"]) == int(iid):
                return it
        return None

    def stats_line(self):
        return "stub"


def _tab(items, states):
    eng = StubEngine(items, states)
    tab = WatchTab(eng)
    tab._tick.stop()          # the banner clock is not wanted in a test
    # The tab refuses to repaint a table nobody can see, so it is told it is on
    # screen instead of being SHOWN: nothing here may put a window on the
    # operator's screen, and none of this needs one.
    tab.isVisible = lambda: True
    return tab, eng


def _value(iid, name, on=True):
    return C.new_value_item(iid, name, f"PV:{name}", hi_hi=10, on=on)


def _plan_names(tab):
    """What the plan says, row by row: headings as their title, rows by name."""
    out = []
    for what, group, payload in tab._plan(tab._engine.items()):
        out.append(_G_TITLES[group] if what == "head" else payload["name"])
    return out


# ── the order ───────────────────────────────────────────────────────────────

def test_the_three_blocks_come_in_order_with_their_headings():
    items = [_value(1, "bad1"), _value(2, "off1", on=False),
             _value(3, "ok1"), _value(4, "bad2"), _value(5, "ok2")]
    states = {1: _State(C.STATE_TRIP), 3: _State(C.STATE_OK),
              4: _State(C.STATE_WARN), 5: _State(C.STATE_OK)}
    tab, _eng = _tab(items, states)
    try:
        assert _plan_names(tab) == [
            _G_TITLES[_G_OK], "ok1", "ok2",
            _G_TITLES[_G_BAD], "bad1", "bad2",
            _G_TITLES[_G_OFF], "off1",
        ]
    finally:
        tab.deleteLater()


def test_every_verdict_that_is_not_in_range_lands_in_the_middle_block():
    items = [_value(i, f"v{i}") for i in range(1, 5)]
    states = {1: _State(C.STATE_WARN), 2: _State(C.STATE_STALE),
              3: _State(C.STATE_UNKNOWN), 4: _State(C.STATE_TRIP)}
    tab, _eng = _tab(items, states)
    try:
        names = _plan_names(tab)
        assert names[0] == _G_TITLES[_G_BAD], names
        assert names[1:] == ["v1", "v2", "v3", "v4"]
    finally:
        tab.deleteLater()


def test_a_row_that_has_fired_is_not_in_range_even_if_it_reads_ok_again():
    items = [_value(1, "latched")]
    states = {1: _State(C.STATE_OK, fired=True)}
    tab, _eng = _tab(items, states)
    try:
        assert _plan_names(tab) == [_G_TITLES[_G_BAD], "latched"]
    finally:
        tab.deleteLater()


def test_a_switched_off_row_is_not_watched_whatever_it_reads():
    items = [_value(1, "off1", on=False)]
    states = {1: _State(C.STATE_TRIP, fired=True)}
    tab, _eng = _tab(items, states)
    try:
        assert _plan_names(tab) == [_G_TITLES[_G_OFF], "off1"]
    finally:
        tab.deleteLater()


def test_an_empty_block_gets_no_heading():
    items = [_value(1, "ok1")]
    tab, _eng = _tab(items, {1: _State(C.STATE_OK)})
    try:
        assert _plan_names(tab) == [_G_TITLES[_G_OK], "ok1"]
    finally:
        tab.deleteLater()


# ── the table that is built out of it ───────────────────────────────────────

def test_the_table_shows_the_headings_across_the_whole_width():
    items = [_value(1, "ok1"), _value(2, "bad1")]
    states = {1: _State(C.STATE_OK), 2: _State(C.STATE_TRIP, "too high")}
    tab, _eng = _tab(items, states)
    try:
        tab._refresh()
        table = tab._table
        assert table.rowCount() == 4, table.rowCount()
        assert table.item(0, 0).text().startswith(_G_TITLES[_G_OK])
        assert "(1)" in table.item(0, 0).text()
        assert table.columnSpan(0, 0) == table.columnCount()
        assert table.item(1, 1).text() == "ok1"
        assert table.item(2, 0).text().startswith(_G_TITLES[_G_BAD])
        assert table.item(3, 1).text() == "bad1"
        # A heading is not a row anybody can click or tick.
        flags = table.item(0, 0).flags()
        from PySide6.QtCore import Qt
        assert not (flags & Qt.ItemFlag.ItemIsSelectable)
        assert not (flags & Qt.ItemFlag.ItemIsUserCheckable)
    finally:
        tab.deleteLater()


def test_a_number_changing_does_not_rebuild_but_changing_block_does():
    items = [_value(1, "v1")]
    states = {1: _State(C.STATE_OK, value=1.0)}
    tab, _eng = _tab(items, states)
    try:
        tab._refresh()
        before = tab._shown_plan
        states[1].value = 2.0
        tab._refresh()
        assert tab._shown_plan == before, "a new number must not reshuffle"
        states[1] = _State(C.STATE_TRIP, "too high")
        tab._refresh()
        assert tab._shown_plan != before, "a new verdict must move the row"
        assert tab._table.item(0, 0).text().startswith(_G_TITLES[_G_BAD])
    finally:
        tab.deleteLater()


def test_the_clicked_row_stays_clicked_when_the_blocks_reshuffle():
    items = [_value(1, "v1"), _value(2, "v2")]
    states = {1: _State(C.STATE_OK), 2: _State(C.STATE_OK)}
    tab, _eng = _tab(items, states)
    try:
        tab._refresh()
        tab._table.selectRow(2)                  # v2, second row of the block
        assert tab._selected_id() == 2
        states[1] = _State(C.STATE_TRIP, "too high")
        tab._refresh()
        assert tab._selected_id() == 2, "the selection follows the row, not the"\
                                        " row number"
    finally:
        tab.deleteLater()


def test_nothing_watched_yet_still_says_so():
    tab, _eng = _tab([], {})
    try:
        tab._refresh()
        assert tab._table.rowCount() == 1
        assert "Nothing is being watched" in tab._table.item(0, 0).text()
    finally:
        tab.deleteLater()


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
