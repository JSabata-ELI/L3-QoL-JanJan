"""A statistics selection dragged out past the right-hand edge follows the data.

Its start stays where it was put; its end moves onto the newest sample each
time new data arrives, so the numbers always include the latest point. A
selection that ends inside the graph stays exactly where it was put. And the
right button is the zoom only — it must not start a selection as well.

Headless, network mocked:

    python testing/test_live_selection.py
"""
import gc
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox   # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
PVS = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]


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


def main() -> int:
    print("test_live_selection")
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
    w._conditions = []
    w._custom_pvs = []
    w._on_load_clicked()
    ok = _pump(15.0, lambda: (not w._load_in_flight) and w._graph_axes)
    check("a graph is drawn", ok)
    if not ok:
        print("\nFAILED: no graph")
        return 1

    check("the statistics selection answers the left button only",
          list(getattr(w._span_selector, "validButtons", None) or []) == [1],
          str(getattr(w._span_selector, "validButtons", None)))

    ax0 = w._graph_axes[0]
    lo, hi = ax0.get_xlim()
    start = lo + (hi - lo) * 0.5

    # ── dragged out to the right edge → live ─────────────────────────────────
    w._on_span_select(start, hi)
    check("dragging to the right edge makes it live", w._sel_live is True)

    def _add_newer(dt_ns):
        for pv in PVS:
            s = w._samples_by_pv[pv]
            s.append((s[-1][0] + dt_ns, 500.0, "J"))

    _add_newer(2_000_000_000)
    w._update_graph_data()
    newest = w._newest_sample_num()
    check("its end moves onto the newest sample",
          abs(w._sel_range[1] - newest) < 1e-9,
          f"end {w._sel_range[1]:.9f}, newest {newest:.9f}")
    check("its start stays", abs(w._sel_range[0] - start) < 1e-12)
    check("the statistics include the new point",
          "end follows the newest point" in w._stats_title.text(),
          w._stats_title.text())
    extents = tuple(w._span_selector.extents)
    check("the blue band moves with it", abs(extents[1] - newest) < 1e-9, str(extents))

    # The band survives a full redraw and keeps following.
    w._plot_graph()
    _add_newer(2_000_000_000)
    w._update_graph_data()
    check("still live after a full redraw",
          abs(w._sel_range[1] - w._newest_sample_num()) < 1e-9)

    # ── a selection inside the graph stays put ───────────────────────────────
    lo, hi = w._graph_axes[0].get_xlim()
    a, b = lo + (hi - lo) * 0.2, lo + (hi - lo) * 0.6
    w._on_span_select(a, b)
    check("a selection inside the graph is not live", w._sel_live is False)
    _add_newer(2_000_000_000)
    w._update_graph_data()
    check("and does not move", w._sel_range == (a, b), str(w._sel_range))

    # ── Clear selection ends it ──────────────────────────────────────────────
    w._on_span_select(start, w._graph_axes[0].get_xlim()[1])
    w._clear_selection()
    check("Clear selection ends the live selection",
          w._sel_live is False and w._sel_range is None)

    # ── Axis limits: a date range and one PV's own Y range ───────────────────
    dlg = css._GraphAxisLimitsDialog(w)
    check("the time is shown as a date and a clock",
          len(dlg._dt_from.text()) == 19 and dlg._dt_from.text()[4] == "-",
          dlg._dt_from.text())
    check("every graphed PV can be picked", dlg._combo.count() == len(w._graph_pvs))
    lo, hi = w._graph_axes[0].get_xlim()
    new_x = (lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.75)
    w._apply_axis_limits_dialog(new_x, {PVS[1]: (90.0, 150.0)})
    st = w._pv_settings[PVS[1]]
    check("the Y range lands in that PV's own Y min / Y max",
          st.get("ymin") == 90.0 and st.get("ymax") == 150.0, str(st.get("ymin")))
    got = w._graph_axes[0].get_xlim()
    check("the time range survives the redraw the Y change needs",
          abs(got[0] - new_x[0]) < 1e-9 and abs(got[1] - new_x[1]) < 1e-9, str(got))
    dlg.reject()

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
