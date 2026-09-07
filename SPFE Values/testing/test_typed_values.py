"""
Typing over a number. There is no Save button: a cell writes itself the moment
its editor closes.

This is the one path the operator uses every day, and it crosses everything the
typed values touch: what the cell shows, what the editor accepts, what lands in
the log file, and that it lands in that day only. It also pins the three things
that make typing over a FETCHED value safe -- a commit that changed nothing
must write nothing (the cell shows a rounded number), one number of an X;Y;SUM
cell must not wipe the other two, and a day the archiver never answered for
must still be typeable.

Everything happens in a temporary folder -- APPDATA is redirected and the share
is pointed at a folder inside it, so neither the real log nor the scratch share
is touched.

Run:  python testing/test_typed_values.py
"""
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
_TMP = tempfile.mkdtemp(prefix="spfe_typed_")
os.environ["APPDATA"] = _TMP

import spfe_store as store                                  # noqa: E402
from spfe_store import SLOT_MORNING, Record, TZ_PRAGUE      # noqa: E402

_FAKE_SHARE = Path(_TMP) / "share"
_FAKE_SHARE.mkdir(parents=True, exist_ok=True)
store.SHARE_CANDIDATES = (str(_FAKE_SHARE),)

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def seed(fields):
    """Two days, both recorded, neither with a typed number of its own.

    One of them carries a three-number XPW row whose values are NOT round, so
    the cell on screen is rounded and the file is not -- which is the case a
    no-op commit would destroy.
    """
    rows = []
    for d in (date(2026, 8, 31), date(2026, 9, 1)):
        rows.append(Record(
            when=datetime(d.year, d.month, d.day, 9, 0, tzinfo=TZ_PRAGUE),
            slot=SLOT_MORNING,
            values={"osc_power": 636.0,
                    "ba2loop2_1": 143.0, "ba2loop2_2": -272.0,
                    "ba2loop2_3": 2501.0}))
    store.append_csv(store.local_dir() / store.CSV_NAME, rows, fields.columns)


def settle(app, w, tries: int = 400):
    """Let the queued write finish. The save runs on its own thread with a
    400 ms debounce, so the test has to wait for it like the operator does."""
    w._save_timer.stop()
    w._flush_pending()
    for _ in range(tries):
        app.processEvents()
        if not w._saving and not w._working:
            break
        time.sleep(0.01)


def main():
    print("typed values")
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("  SKIP  PySide6 is not installed")
        return 0
    from spfe_t import _ROLE_LEVEL as LEVEL
    from spfe_t import SPFEValuesWidget

    seed(store.load_fields())

    app = QApplication.instance() or QApplication([])
    w = SPFEValuesWidget()
    w.cfg["catch_up_on_start"] = False
    w._rows = store.read_csv(w.store.local_csv)
    w._day = date(2026, 9, 1)
    w._refresh_all()

    # Every NUMBER has a line of its own now, and the value columns start
    # after the three label columns.
    from spfe_t import _FIRST_VALUE as V

    lines = w._table_lines()
    line_of = {ln.column: i for i, ln in enumerate(lines)}
    r = line_of["gdd_baseline"]

    check("the prefilled number is in the cell",
          w.table.item(r, V).text() == "22710", w.table.item(r, V).text())
    check("and the cell can be typed into",
          bool(w.table.item(r, V).flags() & Qt.ItemFlag.ItemIsEditable))
    check("a number row gets the number-only editor",
          w.table.itemDelegateForRow(r) is w._num_delegate)
    # The free-text row keeps the ordinary editor -- but still the delegate
    # that draws the group lines, never a bare None.
    check("the free-text row keeps the ordinary editor",
          w.table.itemDelegateForRow(line_of["notes"]) is w._plain_delegate)
    check("there is no Save button any more",
          not hasattr(w, "btn_save_typed"))

    # Type over it, exactly as the editor does.
    w.table.item(r, V).setText("24000")
    settle(app, w)

    saved = {row["datetime"]: row for row in store.read_csv(w.store.local_csv)}
    today = saved["2026-09-01 09:00:00"]
    other = saved["2026-08-31 09:00:00"]
    check("the typed number reached the log file by itself",
          today["gdd_baseline"] == "24000", today["gdd_baseline"])
    check("the other prefilled numbers went with it",
          today["spider_e5"] == "-917", today["spider_e5"])
    check("the day before was not touched",
          other["gdd_baseline"] == "", repr(other["gdd_baseline"]))
    check("and the measured value survived", today["osc_power"] == "636.0")

    # ── the three numbers of one quantity, one cell each ─────────────────
    x1, x2, x3 = (line_of["ba2loop2_1"], line_of["ba2loop2_2"],
                  line_of["ba2loop2_3"])
    check("X, Y and SUM are three lines, not one cell with semicolons",
          [w.table.item(i, V).text() for i in (x1, x2, x3)]
          == ["140", "-270", "2500"],
          str([w.table.item(i, V).text() for i in (x1, x2, x3)]))
    check("and the line says which number it is",
          [w.table.item(i, 2).text() for i in (x1, x2, x3)]
          == ["X", "Y", "SUM"])
    check("the quantity is named once, beside all three",
          w.table.item(x1, 1).text() == "Input - BA2Loop2",
          w.table.item(x1, 1).text())
    check("and the file still holds what was measured",
          today["ba2loop2_1"] == "143.0", today["ba2loop2_1"])

    # Committing it unchanged must write NOTHING: this is what stands between
    # the rounded text and the measured value.
    w.table.item(x1, V).setText("140")
    settle(app, w)
    again = {row["datetime"]: row
             for row in store.read_csv(w.store.local_csv)}["2026-09-01 09:00:00"]
    check("a commit that changed nothing leaves the measured value alone",
          again["ba2loop2_1"] == "143.0", again["ba2loop2_1"])

    # One number of three: the other two must survive. They now survive by
    # construction -- a cell IS one number -- which is the whole reason the
    # row was split.
    w.table.item(x1, V).setText("150")
    settle(app, w)
    after = {row["datetime"]: row
             for row in store.read_csv(w.store.local_csv)}["2026-09-01 09:00:00"]
    check("the corrected number landed", after["ba2loop2_1"] == "150",
          after["ba2loop2_1"])
    check("and the other two are untouched",
          [after["ba2loop2_2"], after["ba2loop2_3"]] == ["-272.0", "2501.0"],
          f'{after["ba2loop2_2"]} / {after["ba2loop2_3"]}')

    # Emptying one cell clears that number and nothing else.
    w.table.item(x2, V).setText("")
    settle(app, w)
    cleared = {row["datetime"]: row
               for row in store.read_csv(w.store.local_csv)}["2026-09-01 09:00:00"]
    check("an emptied cell clears its own number",
          cleared["ba2loop2_2"] == "", repr(cleared["ba2loop2_2"]))
    check("and leaves the others alone",
          [cleared["ba2loop2_1"], cleared["ba2loop2_3"]] == ["150", "2501.0"],
          f'{cleared["ba2loop2_1"]} / {cleared["ba2loop2_3"]}')
    check("the emptied cell shows a dash, not the old number",
          w.table.item(x2, V).text() == "-", w.table.item(x2, V).text())

    # ── the reference colours ────────────────────────────────────────────
    w._refs = {"osc_power": {"min": 600, "max": 660},
               "ba2loop2_3": {"min": 2000, "max": 2520}}
    w._refresh_all()
    lines = w._table_lines()
    line_of = {ln.column: i for i, ln in enumerate(lines)}
    p = line_of["osc_power"]
    check("a value inside its range is not coloured",
          w.table.item(p, V).data(LEVEL) == "", str(w.table.item(p, V).data(LEVEL)))
    check("a value close to an edge is yellow",
          w.table.item(line_of["ba2loop2_3"], V).data(LEVEL) == "warn",
          str(w.table.item(line_of["ba2loop2_3"], V).data(LEVEL)))
    check("and the yellow cell keeps black text",
          w.table.item(line_of["ba2loop2_3"], V).foreground().color().name()
          == "#000000")

    w.table.item(p, V).setText("212")
    settle(app, w)
    check("a value outside its range is red",
          w.table.item(p, V).data(LEVEL) == "bad",
          str(w.table.item(p, V).data(LEVEL)))
    check("and the red cell carries white text",
          w.table.item(p, V).foreground().color().name() == "#ffffff",
          w.table.item(p, V).foreground().color().name())
    check("the tooltip says why",
          "reference" in (w.table.item(p, V).toolTip() or ""),
          w.table.item(p, V).toolTip())
    w.table.item(p, V).setText("636")
    settle(app, w)

    # ── a day the archiver never answered for ────────────────────────────
    w._day = date(2026, 8, 28)
    w._refresh_all()
    lines = w._table_lines()
    line_of = {ln.column: i for i, ln in enumerate(lines)}
    r = line_of["gdd_baseline"]
    pending_cols = [rec.source for rec in w._columns]
    check("that day has nothing recorded",
          pending_cols == ["pending", "pending"], str(pending_cols))
    check("the settings are left empty on a day before they existed",
          w.table.item(r, V).text() == "", repr(w.table.item(r, V).text()))
    w.table.item(line_of["osc_power"], V).setText("612")
    for _ in range(400):
        app.processEvents()
        if not w._working:
            break
        time.sleep(0.01)
    written = {row["datetime"]: row for row in store.read_csv(w.store.local_csv)}
    check("typing into it writes the day down by hand",
          "2026-08-28 09:00:00" in written, str(sorted(written)))
    if "2026-08-28 09:00:00" in written:
        by_hand = written["2026-08-28 09:00:00"]
        check("with the value that was typed", by_hand["osc_power"] == "612",
              by_hand["osc_power"])
        check("as a hand-written record, not an archiver reading",
              by_hand["source"] == "manual", by_hand["source"])
        check("and still no settings from a later day",
              by_hand["gdd_baseline"] == "", repr(by_hand["gdd_baseline"]))

    w.shutdown()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
