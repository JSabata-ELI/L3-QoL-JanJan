"""Conditions, the live selection and the Grid tick box on the Graph tab.

  * Conditions hold on EVERY path: the live refresh (which reads the samples,
    not the filtered table), the value from before the window, the min-max
    band and the PV table's numbers. "Apply conditions" off shows it all again.
  * A live selection counts every shot since its start, also once the start
    is older than the graph's own span (the live window trims old samples).
  * "Live selection restarts after pause": a pause in firing longer than the
    set seconds moves the start onto the first shot after it.
  * A selection over a stretch with no shots shows its cards (N 0) at once.
  * The Grid tick box switches the grid lines without rebuilding the graph.

Headless, network mocked:

    python testing/test_conditions_selection_grid.py
"""
import gc
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                  # noqa: E402
from PySide6.QtCore import Qt                                       # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox    # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
PVS = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]
NS = 1_000_000_000


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


def _fake(channel, start_ns, end_ns, *a, **k):
    span = max(int(end_ns) - int(start_ns), 1)
    return [{"time": int(start_ns) + span * i // 40, "value": 100.0 + i,
             "metaData": {"units": "J"}} for i in range(40)]


def _drawn_values(w, pv):
    i = w._graph_pvs.index(pv)
    return np.asarray(w._graph_raw_np[i][1], dtype=float)


def _card_n(w, pv):
    for name, _c, stats in getattr(w, "_stats_rows", []):
        if name == w._col_label(pv):
            return int(str(stats["n"]).replace(",", ""))
    return None


def main() -> int:
    print("test_conditions_selection_grid")
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
    w._master_multiple_edit.setText("")
    w._custom_pvs = []
    w._chk_cond_on.setChecked(True)
    w._set_conditions([])
    w._on_load_clicked()
    ok = _pump(15.0, lambda: (not w._load_in_flight) and w._graph_axes)
    check("a graph is drawn", ok)
    if not ok:
        print("\nFAILED: no graph")
        return 1

    # ── 7. Conditions on every path ──────────────────────────────────────────
    w._set_conditions([{"pv": PVS[0], "min": 120.0, "max": 1e9}])
    v = _drawn_values(w, PVS[0])
    check("full redraw: nothing below the condition",
          v.size and np.nanmin(v) >= 120.0, f"min {np.nanmin(v) if v.size else None}")
    # The live refresh reads the samples themselves.
    w._update_graph_data()
    v = _drawn_values(w, PVS[0])
    check("live refresh: nothing below the condition",
          v.size and np.nanmin(v) >= 120.0, f"min {np.nanmin(v) if v.size else None}")
    v_b = _drawn_values(w, PVS[1])
    check("live refresh: the other channel loses the same shots",
          v_b.size and np.nanmin(v_b) >= 120.0, f"min {np.nanmin(v_b) if v_b.size else None}")
    # A value from before the window below the limit is not drawn.
    w._pre_window_vals[PVS[0]] = (0, 50.0, "J")
    w._update_graph_data()
    v = _drawn_values(w, PVS[0])
    check("the value from before the window is not drawn when rejected",
          v.size and np.nanmin(v) >= 120.0, f"min {np.nanmin(v)}")
    # Thinned bins: those lying wholly below the condition stay out; a kept
    # bin's band is its whole archived range (so no spike is hidden).
    s = w._samples_by_pv[PVS[0]]
    s_t = np.array([t for t, _v, _u in s], dtype=float)
    s_v = np.array([v for _t, v, _u in s], dtype=float)
    w._minmax_by_pv[PVS[0]] = (s_t, s_v - 30.0, s_v + 1.5)
    w._update_graph_data()
    lo_band = w._graph_env[w._graph_pvs.index(PVS[0])][0]
    check("the band holds only the kept bins' ranges",
          np.nanmin(lo_band) >= 119.0 - 30.0, f"min {np.nanmin(lo_band)}")
    # A thinned bin is judged by the shots behind it, not by its mean: a bin
    # whose maximum reaches the condition may hold a good shot, and is kept
    # (a day with a few good shots among laser-off ones vanished before).
    keep = w._condition_mask(s_t.astype(np.int64), w._condition_tables())
    check("a bin whose range reaches the condition is kept",
          bool(keep[s_v == 119.0].all()) and not bool(keep[s_v == 118.0].any()),
          f"lowest kept {s_v[keep].min() if keep.any() else None}")
    w._minmax_by_pv[PVS[0]] = None
    w._update_graph_data()
    st = w._pv_stats(PVS[0])
    check("PV table numbers obey the conditions",
          float(st["min"]) >= 120.0 and st["count"] == "20", str(st))

    # Switch off: everything is back, the condition rows are kept.
    w._chk_cond_on.setChecked(False)
    w._update_graph_data()
    v = _drawn_values(w, PVS[0])
    check("'Apply conditions' off shows the rejected shots again",
          v.size and np.nanmin(v) < 120.0, f"min {np.nanmin(v)}")
    check("…and keeps the condition rows",
          len(w._conditions_view()) == 1, str(w._conditions_view()))
    w._chk_cond_on.setChecked(True)
    _pump(0.6)
    w._update_graph_data()
    v = _drawn_values(w, PVS[0])
    check("on again: filtered again", np.nanmin(v) >= 120.0, f"min {np.nanmin(v)}")
    w._minmax_by_pv.pop(PVS[0], None)
    w._pre_window_vals.pop(PVS[0], None)
    w._set_conditions([])

    # ── 9. Live selection counts past the graph's span ───────────────────────
    ax0 = w._graph_axes[0]
    lo, hi = ax0.get_xlim()
    start = lo + (hi - lo) * 0.5
    w._on_span_select(start, hi)
    check("live selection", w._sel_live is True)
    n0 = _card_n(w, PVS[0])

    def _tick(values, dt_ns=5 * NS, trim_keep=10):
        """One live tick as _on_incremental_finished does it: append, let the
        selection copy, then trim the window down to its last samples."""
        for val in values:
            for pv in PVS:
                s_ = w._samples_by_pv[pv]
                s_.append((s_[-1][0] + dt_ns, val, "J"))
        w._sel_sync()
        for pv in PVS:
            s_ = w._samples_by_pv[pv]
            del s_[:max(0, len(s_) - trim_keep)]
        w._update_graph_data()

    _tick([130.0] * 30)
    n1 = _card_n(w, PVS[0])
    check("N grows past what the trimmed window still holds",
          n1 is not None and n0 is not None and n1 == n0 + 30
          and len(w._samples_by_pv[PVS[0]]) == 10,
          f"{n0} -> {n1}, window holds {len(w._samples_by_pv[PVS[0]])}")
    check("the start stays", abs(w._sel_range[0] - start) < 1e-12)

    # ── 8. Restart after a pause ─────────────────────────────────────────────
    w._chk_sel_pause.setChecked(True)
    w._spin_sel_pause.setValue(15)
    _tick([131.0], dt_ns=40 * NS)          # 40 s pause, then firing again
    _tick([132.0, 133.0])
    first_after = w._samples_by_pv[PVS[0]][-3][0]
    lo_ns = (w._sel_range[0] - css._mpl_epoch_num()) * css._NS_PER_DAY
    check("the start jumps onto the first shot after the pause",
          abs(lo_ns - first_after) < 2e6, f"{lo_ns:.0f} vs {first_after}")
    check("N counts only the shots after it", _card_n(w, PVS[0]) == 3,
          str(_card_n(w, PVS[0])))
    check("the heading says so", "pause" in w._stats_title.text(),
          w._stats_title.text())
    w._chk_sel_pause.setChecked(False)

    # ── 11. A selection with no shots ────────────────────────────────────────
    w._clear_selection()
    # The mocked ticks ran ahead of the clock: show up to the newest sample,
    # as the live window does, then drag from just after it to the edge.
    newest = w._newest_sample_num()
    x_lo = w._graph_axes[0].get_xlim()[0]
    w._graph_axes[0].set_xlim(x_lo, newest + 1e-4)
    w._on_span_select(newest + 1e-6, newest + 1e-4)
    check("an empty live selection is live", w._sel_live is True)
    check("its cards are there with N 0",
          _card_n(w, PVS[0]) == 0 and _card_n(w, PVS[1]) == 0,
          str(getattr(w, "_stats_rows", None)))
    check("its heading says live", "end follows" in w._stats_title.text(),
          w._stats_title.text())
    _tick([140.0])
    check("the first shot is counted", _card_n(w, PVS[0]) == 1,
          str(_card_n(w, PVS[0])))
    w._clear_selection()

    # ── 10. Grid without a rebuild ───────────────────────────────────────────
    calls = []
    orig = w._plot_graph
    w._plot_graph = lambda *a, **k: calls.append(1) or orig(*a, **k)
    xl_before = tuple(w._graph_axes[0].get_xlim())
    cols = list(w._axis_tv_cols)
    gcol = cols.index("grid")
    row = w._axis_row_pv.index(PVS[0])
    item = w._axis_tv.item(row, gcol)
    item.setCheckState(Qt.CheckState.Checked)
    _pump(0.4)
    ax = w._graph_axes[w._graph_pvs.index(PVS[0])]
    on = any(gl.get_visible() for gl in ax.yaxis.get_gridlines())
    check("ticking Grid draws the grid", on)
    check("…without rebuilding the graph", not calls, f"{len(calls)} rebuilds")
    check("…and the view stays", tuple(w._graph_axes[0].get_xlim()) == xl_before)
    w._axis_tv.item(row, gcol).setCheckState(Qt.CheckState.Unchecked)
    _pump(0.4)
    off = not any(gl.get_visible() for gl in ax.yaxis.get_gridlines())
    check("unticking hides it, still no rebuild", off and not calls)
    w._plot_graph = orig

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
