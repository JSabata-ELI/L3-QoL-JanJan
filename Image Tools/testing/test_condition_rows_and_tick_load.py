"""PV Search window, 27.10.2026: condition rows, formulas in the search, tick loads.

1. **ONE CONDITION ROW PER TICKED PV, FORMULAS INCLUDED.** Each row has its own box,
   test and value; the search finds the first moment where EVERY ticked row holds at
   once. A row keeps its settings while its PV is unticked.
2. **THE SEARCH JUDGES THE ROWS TOGETHER.** Two channels sampled at different rates
   are put on one time base and held forward; a formula is computed from its sources
   the same way the graph computes it.
3. **TICKING A PV DOES NOT RE-READ THE OTHERS.** Unticking is a redraw, ticking reads
   only the new PV, and a burst of ticks is ONE load after the last of them.
4. **A FORMULA OVER ONE PV IS DRAWN IN THAT PV'S UNIT**, so "SBW4 × 0.749" shares
   the joules axis instead of lying exactly under the SBW4 curve on its own axis.

Offscreen, no share and no archiver.

    python testing/test_condition_rows_and_tick_load.py
"""
import sys
import types
from datetime import date, datetime
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)
SBW4_CH = "L3-SBW4-PM311:Energy"                      # registry SBW4, unit J
PTM1_CH = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"       # registry PTM1, unit J
ARCHIVE: dict = {}
FETCHES: list = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(hour: int, minute: int = 0, sec: int = 0) -> int:
    from zoneinfo import ZoneInfo
    return int(datetime(DAY.year, DAY.month, DAY.day, hour, minute, sec,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def fake_fetch(channel, start_ns, end_ns):
    FETCHES.append(channel)
    out = [(t, v) for (t, v) in ARCHIVE.get(channel, []) if start_ns <= t <= end_ns]
    return out, ("ok" if out else "empty"), ""


def install_stubs(m):
    m.PVRegionSearchDialog._fetch_window = staticmethod(fake_fetch)
    m._fetch_window_samples = fake_fetch
    m.cpva.classify_step_channel = lambda ch, ts_ns, primary=None, **kw: False
    m.cpva.is_step_channel = lambda ch, *a, **kw: False
    m.cpva.value_at_or_before = (
        lambda ch, ts_ns, **kw: types.SimpleNamespace(ts_ns=None, value=None,
                                                      status="ok"))


def fill_archive():
    ARCHIVE.clear()
    # SBW4 once a second 09:00–09:10, rising 0 → 30 J; PTM1 every 5 s, 40 ms after
    # the SBW4 sample of the same shot, high (5) only from 09:05 on.
    for i in range(600):
        t = ns_at(9) + i * 1_000_000_000
        ARCHIVE.setdefault(SBW4_CH, []).append((t, i / 20.0))
        if i % 5 == 0:
            ARCHIVE.setdefault(PTM1_CH, []).append(
                (t + 40_000_000, 5.0 if i >= 300 else 1.0))


def check_engine(m):
    print("\n=== the rows are judged together ===")
    fill_archive()
    win = [(ns_at(8), ns_at(12))]
    one = [{"channel": SBW4_CH, "op": ">=", "value": 13.0}]
    got = m._first_all_rows_hit(one, win, fetch=fake_fetch)
    check("one row: the first sample at or above 13", got is not None
          and got[0] == ns_at(9) + 260 * 1_000_000_000,
          repr(got and (got[0] - ns_at(9)) / 1e9))
    two = one + [{"channel": PTM1_CH, "op": ">", "value": 3.0}]
    got = m._first_all_rows_hit(two, win, fetch=fake_fetch)
    check("two rows: the first moment BOTH hold (09:05, when PTM1 goes high)",
          got is not None and abs(got[0] - (ns_at(9, 5) + 40_000_000)) < 1_000_000,
          repr(got and (got[0] - ns_at(9)) / 1e9))
    check("and it says what each row read there",
          got is not None and got[1][SBW4_CH] >= 13.0 and got[1][PTM1_CH] > 3.0,
          repr(got and got[1]))
    got2 = m._first_all_rows_hit(two, win, after_ns=got[0], fetch=fake_fetch)
    check("asked again after that moment, the next one",
          got2 is not None and got2[0] > got[0], repr(got2 and got2[0] - got[0]))
    never = one + [{"channel": PTM1_CH, "op": ">", "value": 99.0}]
    check("rows that never hold together → None",
          m._first_all_rows_hit(never, win, fetch=fake_fetch) is None)

    plan = [{"name": "half SBW4", "expr": "I*0.5", "bindings": {"I": "SBW4"},
             "letters": ["I"], "unbound": [], "chained": False,
             "sources": ["SBW4"]}]
    frow = [{"channel": m._DERIVED_PREFIX + "half SBW4", "op": ">=", "value": 13.0,
             "plan": plan, "src_channels": {"SBW4": SBW4_CH}}]
    # The formula has to be in the registry: the engine checks its vectorised answer
    # against the registry's own evaluator, as it does for the graph.
    sl = m._get_slider_module()
    saved = list(sl.PV_DERIVED)
    sl.PV_DERIVED.append({"name": "half SBW4", "expr": "I*0.5", "unit": "",
                          "bindings": {"I": "SBW4"}})
    try:
        got = m._first_all_rows_hit(frow, win, fetch=fake_fetch)
    finally:
        sl.PV_DERIVED[:] = saved
    check("a formula row: half of SBW4 reaches 13 when SBW4 reaches 26",
          got is not None and abs((got[0] - ns_at(9)) / 1e9 - 520) < 1.5,
          repr(got and (got[0] - ns_at(9)) / 1e9))

    def broken(channel, a, b):
        return [], "error", ""
    try:
        m._first_all_rows_hit(two, win, fetch=broken)
        raised = False
    except Exception:
        raised = True
    check("an archiver failure raises — never read as 'never met'", raised)


def make_dialog(m, checked):
    from PySide6.QtCore import QDate, Qt
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])
    set_ticks(dlg, checked)
    dlg._reload_series()
    B.wait_for(lambda: all(ch in (dlg._series.get(DAY) or {}) for ch in checked),
               timeout_s=15.0)
    return dlg


def set_ticks(dlg, checked, signals=False):
    from PySide6.QtCore import Qt
    if not signals:
        dlg._pv_list.blockSignals(True)
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        on = it.data(Qt.ItemDataRole.UserRole) in checked
        want = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        if it.checkState() != want:
            it.setCheckState(want)
    if not signals:
        dlg._pv_list.blockSignals(False)
        dlg._refresh_primary_combo()


def check_dialog(m):
    print("\n=== condition rows in the window ===")
    sl = m._get_slider_module()
    saved = list(sl.PV_DERIVED)
    sl.PV_DERIVED.append({"name": "half SBW4", "expr": "I*0.5", "unit": "",
                          "bindings": {"I": "SBW4"}})
    fkey = m._DERIVED_PREFIX + "half SBW4"
    try:
        fill_archive()
        dlg = make_dialog(m, (SBW4_CH, PTM1_CH, fkey))
        check("one row per ticked PV, the formula included",
              sorted(dlg._cond_rows) == sorted([SBW4_CH, PTM1_CH, fkey]),
              repr(sorted(dlg._cond_rows)))
        dlg._cond_on.setChecked(True)
        check("no row comes ticked (27.10.2026)",
              not any(r["box"].isChecked() for r in dlg._cond_rows.values()))
        dlg._cond_rows[SBW4_CH]["box"].setChecked(True)
        dlg._cond_rows[PTM1_CH]["box"].setChecked(True)
        dlg._cond_rows[PTM1_CH]["v1"].setValue(3.0)
        dlg._cond_rows[fkey]["box"].setChecked(True)
        cfg = dlg.get_config()["condition"]
        chans = [r["channel"] for r in cfg["rows"]]
        check("every ticked row goes to the search", len(chans) == 3, repr(chans))
        frow = [r for r in cfg["rows"] if r["channel"] == fkey]
        check("the formula row carries its own plan",
              frow and frow[0].get("plan") and frow[0]["plan"][0]["expr"] == "I*0.5")
        check("the search button says it searches by condition",
              "condition" in dlg._btn_search.text(), dlg._btn_search.text())

        # Untick PTM1 and tick it again: its row comes back as it was left.
        set_ticks(dlg, (SBW4_CH, fkey))
        check("an unticked PV loses its row", PTM1_CH not in dlg._cond_rows)
        set_ticks(dlg, (SBW4_CH, PTM1_CH, fkey))
        r = dlg._cond_rows.get(PTM1_CH)
        check("ticked again, the row keeps its box and value",
              r is not None and r["box"].isChecked()
              and abs(r["v1"].value() - 3.0) < 1e-9)

        print("\n=== ticking does not re-read the others ===")
        FETCHES.clear()
        set_ticks(dlg, (SBW4_CH, fkey))
        dlg._reload_series()
        B.wait_for(lambda: False, timeout_s=0.5)
        check("unticking reads nothing", FETCHES == [], repr(FETCHES))
        set_ticks(dlg, (SBW4_CH, PTM1_CH, fkey))
        dlg._reload_series()
        B.wait_for(lambda: False, timeout_s=0.5)
        check("ticking a PV already read reads nothing", FETCHES == [], repr(FETCHES))

        dlg._series.get(DAY, {}).pop(PTM1_CH, None)
        FETCHES.clear()
        loads = []
        orig = dlg._reload_series
        dlg._tick_timer.timeout.disconnect()
        dlg._tick_timer.timeout.connect(lambda: (loads.append(1), orig()))
        for _ in range(5):                       # a burst of five ticks
            dlg._on_pv_checks_changed()
        B.wait_for(lambda: bool(loads), timeout_s=3.0)
        B.wait_for(lambda: PTM1_CH in (dlg._series.get(DAY) or {}), timeout_s=5.0)
        check("five quick ticks are ONE load", len(loads) == 1, f"{len(loads)} load(s)")
        check("and it reads only the PV not held yet", FETCHES == [PTM1_CH],
              repr(FETCHES))

        print("\n=== a formula over one PV takes that PV's unit ===")
        unit = dlg._pv_meta_for(fkey).get("unit")
        check("half SBW4 is drawn in J", unit == dlg._pv_meta_for(SBW4_CH).get("unit")
              and bool(unit), repr(unit))
        check("so it shares SBW4's axis",
              dlg._unit_key_for(fkey) == dlg._unit_key_for(SBW4_CH))
        sl.PV_DERIVED.append({"name": "RATIO", "expr": "A / B", "unit": "",
                              "bindings": {"A": "SBW4", "B": "PTM1"}})
        check("a formula over two PVs keeps no unit",
              m.PVRegionSearchDialog._formula_source_unit("RATIO") == "")
        dlg.close()
        dlg.deleteLater()
    finally:
        sl.PV_DERIVED[:] = saved


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    install_stubs(m)
    check_engine(m)
    check_dialog(m)
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
