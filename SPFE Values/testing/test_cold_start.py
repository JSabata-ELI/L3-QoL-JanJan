"""
The first run: no log, no shared folder, no PV names filled in.

That is exactly the state the program is in today, so it is the one path that
must not throw. Nothing is written outside a temporary folder and the archiver
is never contacted -- with no PV names there is nothing to ask it for.

Run:  python testing/test_cold_start.py
"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

_TMP = tempfile.mkdtemp(prefix="spfe_cold_")
os.environ["APPDATA"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import spfe_record as recorder        # noqa: E402
import spfe_store as store            # noqa: E402
from spfe_store import SLOT_MORNING, TZ_PRAGUE   # noqa: E402

# A folder that does not exist: the share must be reported unreachable, not
# waited on and not created.
store.SHARE_CANDIDATES = (str(Path(_TMP) / "no_such_share"),)

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def test_no_pv_names_costs_no_request():
    """A quantity whose PV is still blank must cost nothing and throw nothing.

    Five of the shipped rows are in that state -- the archiver has no channel
    with pump, dac, humid, osc or dazz in its name -- so this is not a
    hypothetical path.
    """
    fields = store.load_fields()
    blank = [r.key for r in fields.rows
             if not r.is_manual and not [p for p in r.pvs if p]]
    check("the rows nobody has found a PV for are still recognised",
          set(blank) == {"pumplaser_dac", "humidity", "osc_power",
                         "osc_bandwidth", "dazz2"}, str(blank))

    empty = store.Fields([(g, [r for r in rows if r.is_manual])
                          for g, rows in fields.groups])
    rec = recorder.fetch_moment(
        empty, datetime(2026, 9, 1, 9, 0, tzinfo=TZ_PRAGUE), SLOT_MORNING)
    check("reading a moment with no PVs at all returns an empty column",
          rec.values == {}, str(rec.values))


def test_unreachable_share_is_reported_not_created():
    cfg = dict(store.DEFAULT_CONFIG, share_probe_timeout_s=1.0)
    fields = store.load_fields()
    st = store.Store(cfg, fields)
    root = st.connect()
    check("an unreachable share resolves to nothing", root == "", root)
    check("and share_folder is None", st.share_folder is None)
    check("and the folder was not created",
          not (Path(_TMP) / "no_such_share").exists())


def test_saving_without_a_share_still_keeps_the_values():
    cfg = dict(store.DEFAULT_CONFIG, share_probe_timeout_s=1.0)
    fields = store.load_fields()
    st = store.Store(cfg, fields)
    st.connect()
    rec = store.Record(
        when=datetime(2026, 9, 1, 9, 0, tzinfo=TZ_PRAGUE),
        slot=SLOT_MORNING, values={"osc_power": 636.0})
    res = st.save([rec])
    check("the value reached the local copy", res.local_rows == 1)
    check("nothing reached the share", res.share_rows == 0)
    check("and the status says why, in a sentence",
          "saved on this PC" in res.problem, res.problem)
    check("reading it back gives the value",
          st.all_rows()[0]["osc_power"] == "636.0")


def test_the_window_opens():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("  SKIP  window check - PySide6 is not installed")
        return
    from spfe_t import SPFEValuesWidget

    app = QApplication.instance() or QApplication([])
    w = SPFEValuesWidget()
    w.resize(1200, 800)
    app.processEvents()
    check("the window builds with an empty log", w is not None)
    # One line per QUANTITY: the numbers of a quantity stand side by side, in
    # sub-columns of the moment they were recorded in.
    check("and the table has a line for every quantity",
          w.table.rowCount() == len(w.fields.rows),
          f"{w.table.rowCount()} vs {len(w.fields.rows)}")
    # Two label columns, the two scheduled moments at three sub-columns each,
    # and the filler that stops any real column from having to stretch.
    parts = w._parts()
    check("and both scheduled columns are shown, empty",
          w.table.columnCount() == 2 + 2 * parts + 1,
          f"{w.table.columnCount()} with parts={parts}")

    from PySide6.QtWidgets import QTabWidget
    check("the page has no tab bar of its own",
          w.findChild(QTabWidget) is None)

    # The prefilled numbers must stand in the cells of a day nothing has been
    # recorded for -- that is the whole point of them.
    from spfe_t import _FIRST_VALUE as V, _ROLE_COL
    row_of = {r.key: i for i, r in enumerate(w.fields.rows)}
    g = row_of["gdd_baseline"]
    check("a prefilled number shows on an empty day",
          w.table.item(g, V).text() == "22710", w.table.item(g, V).text())
    check("and the unit stands in the Detail column, not in the cell",
          w.table.item(g, 1).text() == "L3 baseline (fs2)",
          w.table.item(g, 1).text())
    check("the label of that column says Detail",
          w.table.horizontalHeaderItem(1).text() == "Detail",
          w.table.horizontalHeaderItem(1).text())
    # A quantity that is ONE number lies across the whole moment, so there is
    # no empty box beside it.
    check("a single number takes the whole moment",
          w.table.columnSpan(g, V) == parts, str(w.table.columnSpan(g, V)))
    # The three numbers of a quantity are side by side, each in its own cell.
    x = row_of["ba2loop2"]
    check("X, Y and SUM are three cells of one line",
          [w.table.item(x, V + i).data(_ROLE_COL) for i in range(3)]
          == ["ba2loop2_1", "ba2loop2_2", "ba2loop2_3"],
          str([w.table.item(x, V + i).data(_ROLE_COL) for i in range(3)]))
    check("and the heading names them",
          [w.table.horizontalHeaderItem(V + i).text() for i in range(3)]
          == ["X", "Y", "SUM"],
          str([w.table.horizontalHeaderItem(V + i).text() for i in range(3)]))
    # Which hour a column is read at has to be on screen: "the morning value"
    # says nothing until you know the minute the archiver is asked about. That
    # is the strip above the table's own heading now, not a header label.
    check("the scheduled columns say which hour they come from",
          [text for _, _, text in w.band_head._spans]
          == ["Morning (09:00)", "At the end (18:00)"],
          str([text for _, _, text in w.band_head._spans]))
    check("the day band has a campaign box, empty and named as such",
          w.ed_campaign.text() == ""
          and w.ed_campaign.placeholderText() == "not named",
          w.ed_campaign.placeholderText())

    w._open_log()
    check("the Log window opens", w._log_window.isVisible())
    w._log_window.hide()
    w._open_view_day()
    check("the View day window opens", w._day_window.isVisible())
    check("and it lists exactly the days that have a record",
          w.lst_days.count() == len(w._recorded_days()),
          f"{w.lst_days.count()} vs {len(w._recorded_days())}")
    w._day_window.hide()
    w.shutdown()


def main():
    print("cold start")
    for fn in (test_no_pv_names_costs_no_request,
               test_unreachable_share_is_reported_not_created,
               test_saving_without_a_share_still_keeps_the_values,
               test_the_window_opens):
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
