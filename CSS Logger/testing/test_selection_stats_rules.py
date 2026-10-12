"""Selection statistics count real shots, and the rules around them.

* N of a fixed selection is the number of the channel's own samples inside it,
  whatever the screen thinning does (it used to count thinned bins, and in
  Live the count wandered 709 → 710 → 709).
* "Skip zero-energy shots at same waveplate": a shot whose energy is ~0 while
  the waveplate did not move is left out; unticked it counts again.
* "Restart after pause" works for a fixed selection too: counting begins at
  the first shot after the newest pause inside it.
* Start / End: one shot later / earlier, and a dragged start lands on the
  first shot after a pause when let go near it.
* An "Outside" condition keeps what lies beyond its bounds.
* A saved PV Time condition row that is off and empty switches itself on
  when a number is typed in (the SBW4 row that never filtered).

Headless, network mocked:

    python testing/test_selection_stats_rules.py
"""
import gc
import os
import sys
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox   # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
E_PV = "L3-TEST-E:Energy"
B_PV = "L3-TEST-B:Energy"
WP_PV = "L3-TEST-WP:RawPos"
PVS = [E_PV, B_PV, WP_PV]

NOW = datetime.now().replace(microsecond=0)
T_FROM = NOW - timedelta(minutes=30)
T_TO = NOW
NS = 1_000_000_000
T0_NS = int(T_FROM.timestamp()) * NS
WP_MOVE_NS = T0_NS + 414 * NS


def _schedule():
    """Shots every 5 s over 30 min with a 120 s pause after 15 min. Every 4th
    shot is a trigger shot: energy 0, B still reads."""
    shots = []
    t = T0_NS + 2 * NS
    k = 0
    while t < T0_NS + 1795 * NS:
        if not (900 * NS <= t - T0_NS < 1020 * NS):
            shots.append((t, k))
            k += 1
        t += 5 * NS
    return shots


SHOTS = _schedule()


def _fake(channel, start_ns, end_ns, *a, **k):
    out = []
    if channel == WP_PV:
        # Set at the start, moved once at 414 s: the zero-energy shot right
        # after the move (417 s) is a real reading at a new setting.
        for t, v in ((T0_NS + NS, 100.0), (WP_MOVE_NS, 120.0)):
            if int(start_ns) <= t <= int(end_ns):
                out.append({"time": t, "value": v, "metaData": {"units": ""}})
        return out
    for t, i in SHOTS:
        if int(start_ns) <= t <= int(end_ns):
            if channel == E_PV:
                v = 0.0 if i % 4 == 3 else 5.0 + 0.01 * i
            else:
                v = 50.0 + 0.1 * i
            out.append({"time": t + (3_000_000 if channel == E_PV else 0),
                        "value": v, "metaData": {"units": "J"}})
    return out


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _pump(seconds=0.0, cond=None):
    deadline = time.time() + seconds
    while True:
        _app.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= deadline:
            return cond() if cond is not None else True
        time.sleep(0.01)


def _num(t_ns):
    return float(css._ns_to_num(np.float64(t_ns)))


def _card_n(w, pv):
    for name, _c, st in w._stats_rows:
        if name == w._col_label(pv):
            return int(st["n"].replace(",", ""))
    return None


def main() -> int:
    print("test_selection_stats_rules")
    for mod in (cpva_core, css):
        for fn in ("cpva_fetch_samples", "cpva_fetch_samples_chunked"):
            if hasattr(mod, fn):
                setattr(mod, fn, _fake)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected

    w = css.CSSLoggerWidget()
    w.resize(1400, 900)
    w._pv_list.clear()
    for pv in PVS:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    w._master_pv_edit.setText(WP_PV)
    w._master_multiple_edit.setText("")
    w._set_conditions([])
    w._custom_pvs = []
    w._dt_from, w._dt_to = T_FROM, T_TO
    w._chk_dead_shot.setChecked(False)
    w._chk_sel_pause.setChecked(False)
    w._avg_target_spin.setValue(50)          # heavy screen thinning
    w._on_load_clicked()
    ok = _pump(20.0, lambda: (not w._load_in_flight) and w._graph_axes)
    check("a graph is drawn", ok)
    if not ok:
        return 1

    # ── N is the number of real samples, not of thinned bins ────────────────
    a_ns, b_ns = T0_NS + 300 * NS, T0_NS + 600 * NS
    w._on_span_select(_num(a_ns), _num(b_ns))
    lo_ns = (w._sel_range[0] - css._mpl_epoch_num()) * css._NS_PER_DAY
    hi_ns = (w._sel_range[1] - css._mpl_epoch_num()) * css._NS_PER_DAY
    want = sum(1 for t, _i in SHOTS if lo_ns <= t <= hi_ns + 1e6)
    got = _card_n(w, B_PV)
    check("N counts every sample in the selection despite thinning",
          got == want, f"N={got}, samples={want}")
    w._avg_target_spin.setValue(37)
    w._plot_graph()
    check("and does not change when the thinning does",
          _card_n(w, B_PV) == want, f"N={_card_n(w, B_PV)}")

    # ── dead shots ───────────────────────────────────────────────────────────
    w._dead_energy_edit.setText(E_PV)
    w._dead_energy_edit.editingFinished.emit()
    w._dead_energy_spin.setValue(0.05)
    w._chk_dead_shot.setChecked(True)
    sel_shots = [(t, i) for t, i in SHOTS if lo_ns <= t <= hi_ns + 1e6]
    after_move = min(t for t, _i in SHOTS if t > WP_MOVE_NS)
    live_shots = sum(1 for t, i in sel_shots if i % 4 != 3 or t == after_move)
    check("the shot after the waveplate move is a zero-energy one",
          any(t == after_move and i % 4 == 3 for t, i in SHOTS))
    check("ticked: zero-energy shots at the same waveplate are left out",
          _card_n(w, B_PV) == live_shots and _card_n(w, E_PV) == live_shots,
          f"B N={_card_n(w, B_PV)}, E N={_card_n(w, E_PV)}, want {live_shots}")
    w._chk_dead_shot.setChecked(False)
    check("unticked: they count again", _card_n(w, B_PV) == len(sel_shots),
          f"N={_card_n(w, B_PV)}")

    # ── restart after pause, fixed selection ─────────────────────────────────
    a2, b2 = T0_NS + 600 * NS, T0_NS + 1500 * NS
    w._on_span_select(_num(a2), _num(b2))
    w._spin_sel_pause.setValue(60)
    w._chk_sel_pause.setChecked(True)
    after = [t for t, _i in SHOTS if T0_NS + 1020 * NS <= t <= b2]
    hi2 = (w._sel_range[1] - css._mpl_epoch_num()) * css._NS_PER_DAY
    after = [t for t, _i in SHOTS if T0_NS + 1020 * NS <= t <= hi2 + 1e6]
    check("restart after pause: counts from the first shot after the pause",
          _card_n(w, B_PV) == len(after), f"N={_card_n(w, B_PV)}, want {len(after)}")
    check("and the heading says so", "counted from" in w._stats_title.text(),
          w._stats_title.text())
    w._chk_sel_pause.setChecked(False)

    # ── snapping and one-shot steps ──────────────────────────────────────────
    first_after_pause = min(t for t, _i in SHOTS if t >= T0_NS + 1020 * NS)
    ax0 = w._graph_axes[0]
    v_lo, v_hi = ax0.get_xlim()
    px = (v_hi - v_lo) / max(1.0, float(ax0.bbox.width))
    w._on_span_select(_num(first_after_pause) - 3 * px, _num(T0_NS + 1500 * NS))
    s_ns = (w._sel_range[0] - css._mpl_epoch_num()) * css._NS_PER_DAY
    check("a start let go near the end of a pause lands on its first shot",
          abs(s_ns - (first_after_pause - 1e6)) < 2e5,
          f"start off by {(s_ns - first_after_pause) / 1e6:.1f} ms")
    n0 = _card_n(w, B_PV)
    w._step_sel_edge("start", 1)
    check("Start +1 drops one shot", _card_n(w, B_PV) == n0 - 1,
          f"{n0} → {_card_n(w, B_PV)}")
    w._step_sel_edge("start", -1)
    check("Start -1 takes it back", _card_n(w, B_PV) == n0)
    w._step_sel_edge("end", -1)
    check("End -1 drops one shot", _card_n(w, B_PV) == n0 - 1)

    # ── Outside ──────────────────────────────────────────────────────────────
    c = css._cond_normalize({"pv": B_PV, "kind": "outside", "a": 60.0, "b": 70.0})
    check("Outside keeps a value below min", css._cond_value_passes(55.0, c["min"], c["max"], c["outside"]))
    check("Outside keeps a value above max", css._cond_value_passes(75.0, c["min"], c["max"], c["outside"]))
    check("Outside drops a value inside", not css._cond_value_passes(65.0, c["min"], c["max"], c["outside"]))
    m = css._cond_mask_passes(np.array([55.0, 65.0, np.nan, 80.0]), c["min"], c["max"], True)
    check("the array version agrees, NaN never passes",
          m.tolist() == [True, False, False, True], str(m.tolist()))
    w._set_conditions([c])
    rows = w._table_rows
    vals = [r[B_PV][0] for _t, r in rows if B_PV in r]
    check("an Outside condition filters the table rows",
          vals and all(v < 60.0 or v > 70.0 for v in vals), f"{len(vals)} rows")
    w._set_conditions([])

    # ── PV Time: a saved, empty, switched-off row switches on when typed in ─
    ed = css._ConditionEditor([(E_PV, E_PV)],
                              [{"pv": E_PV, "enabled": False, "kind": "above",
                                "a": None, "b": None}], new_row_enabled=False)
    rec = ed._rows[0]
    rec["a"].setText("0.15")
    check("a saved empty row switches itself on once a limit is typed",
          rec["enabled"].isChecked())
    ed2 = css._ConditionEditor([(E_PV, E_PV)],
                               [{"pv": E_PV, "enabled": False, "kind": "above",
                                 "a": 0.2, "b": None}], new_row_enabled=False)
    ed2._rows[0]["a"].setText("0.3")
    check("a row with limits switched off by hand stays off",
          not ed2._rows[0]["enabled"].isChecked())

    w.close()
    w.deleteLater()
    _app.processEvents()
    deadline = time.time() + 5
    while threading.active_count() > 1 and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    gc.collect()

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
