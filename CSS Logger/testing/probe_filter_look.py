"""How the shot-filter block LOOKS, read back off a render.

The block lives in a 340 px sidebar and holds a tick box, a PV button and two
numeric fields on one row. Nothing here can be judged from the code: a guessed
width clips "-98000" to "-9800", and an unpainted widget comes out black on
black on a machine in Windows dark mode. So it is rendered and measured.

Writes testing/_out/filter_look*.png. Nothing is shown on screen — the widget is
grabbed, never shown.

    python testing/probe_filter_look.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

import sp_t                                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

GDD = "L3-SPFE-AOD03-002:Order2_RB"
TOD = "L3-SPFE-AOD03-002:Order3_RB"
FOD = "L3-SPFE-AOD03-002:Order4_RB"
T0 = int(1_756_000_000 * 1e9)
SEC = 10 ** 9
HOUR = 3600 * SEC
NX = 128
X = np.linspace(780.0, 840.0, NX)


def _region(rid, times, series):
    times = [int(t) for t in times]
    rng = np.random.default_rng(rid)
    st = rng.random((len(times), NX))
    stats = sp_t._stats_from_stack(st)
    ts = np.asarray(times, dtype=np.int64)
    return {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": "#1f77b4", "visible": True, "expanded": True,
        "show_individual": False, "analyzed": True, "x": X,
        "stack_all": st, "stack_ts_all": times, "n_all": len(times),
        "stats_all": {k: stats[k] for k in sp_t.STAT_KEYS},
        "scalar_series": dict(series),
        "shot_vals": {ch: sp_t._hold_forward(s, ts) for ch, s in series.items()},
        "orders_all": {"GDD": 24750.0, "TOD": -98000.0, "FOD": -15000.0},
        "energy_avg_all": 9.87, "energy_n_all": len(times),
    }


def _grab(w, name):
    """Save a PNG of the sidebar and report the size, in device pixels."""
    w.updateGeometry()
    QApplication.processEvents()
    pm = w.grab()
    path = os.path.join(OUT, name)
    pm.save(path)
    print(f"  wrote {os.path.basename(path)}   {pm.width()}x{pm.height()} px")
    return pm


def _report_row(w):
    """The measured widths of one condition row, against the room there is."""
    if not w._filter_rows:
        print("  no rows")
        return
    refs = w._filter_rows[0]
    body = w._g_filter.body
    total = sum(x.width() for x in (refs["chk"], refs["pv"], refs["val"],
                                    refs["tol"]))
    print(f"  body {body.width()} px   row: tick {refs['chk'].width()} + "
          f"PV {refs['pv'].width()} + value {refs['val'].width()} + "
          f"tol {refs['tol'].width()}  = {total}")
    for key, want in (("val", "-98000"), ("tol", "1000")):
        e = refs[key]
        fm = e.fontMetrics()
        need = fm.horizontalAdvance(want)
        print(f"  {key}: box {e.width()} px, '{want}' needs {need} px"
              + ("  OK" if e.width() >= need + 8 else "  TOO NARROW"))
        clipped = e.width() < need + 8
        if clipped:
            print("    ^ a value would be clipped")


def main():
    app = QApplication.instance() or QApplication([])
    w = sp_t.SpectraWidget()
    # Tall on purpose: the block is at the bottom of a scroll area, and the
    # viewport would otherwise cut it off in the render.
    w.resize(1500, 1900)
    w._x_data = X

    times = [T0 + i * SEC for i in range(200)]
    series = {GDD: [(T0 - HOUR, 24700.0), (times[120], 24800.0)],
              TOD: [(T0 - HOUR, -97999.99999999999)],
              FOD: [(T0 - HOUR, -15000.0)]}
    w._regions = [_region(1, times, series)]
    w._windows = [(times[0] - HOUR, times[-1] + HOUR)]
    w._tmap = sp_t._TimeMap(w._windows)

    # Three conditions, one of them a long custom channel name, with the block
    # open — the state with the most in it.
    w._filter_conds = [
        {"label": "GDD", "channel": GDD, "value": 24700.0, "tol": 0.0, "on": True},
        {"label": "TOD", "channel": TOD, "value": -98000.0, "tol": 5.0, "on": True},
        {"label": "SBW4 energy [J]", "channel": sp_t.PV_ENERGY,
         "value": 21.437, "tol": 0.5, "on": False},
    ]
    w._filter_on = True
    w._chk_filter.setChecked(True)
    w._rebuild_filter_rows()
    w._g_filter._header.setChecked(True)
    w._g_filter._on_clicked()
    w._apply_shot_filter()
    w._rebuild_regions_ui()
    w._redraw_spectra()
    w._update_filter_readout()
    QApplication.processEvents()

    # The block only has its real width once the sidebar's layout has run.
    w._sidebar_scroll.widget().adjustSize()
    QApplication.processEvents()

    print("\nopen, three conditions")
    print(f"  header  : {w._g_filter._header.text()!r}")
    print(f"  count   : {w._lbl_filter_count.text()!r}")
    for i, c in enumerate(w._filter_conds):
        note, style = w._filter_row_note(c)
        print(f"  row {i}   : {c['label']!r}  note={note!r}")
    _report_row(w)
    _grab(w._g_filter, "filter_look_open.png")

    print("\nnothing matches (the red state)")
    w._filter_conds[0]["value"] = 1e9
    w._apply_shot_filter()
    w._redraw_spectra()
    w._update_filter_readout()
    QApplication.processEvents()
    print(f"  header  : {w._g_filter._header.text()!r}  accent={w._g_filter._accent}")
    print(f"  count   : {w._lbl_filter_count.text()!r}")
    _grab(w._g_filter, "filter_look_zero.png")

    print("\nfolded away")
    w._g_filter._header.setChecked(False)
    w._g_filter._on_clicked()
    QApplication.processEvents()
    print(f"  header  : {w._g_filter._header.text()!r}")
    _grab(w._g_filter, "filter_look_folded.png")

    print("\nthe whole sidebar, filter open")
    w._g_filter._header.setChecked(True)
    w._g_filter._on_clicked()
    w._filter_conds[0]["value"] = 24700.0
    w._apply_shot_filter()
    w._redraw_spectra()
    w._update_filter_readout()
    QApplication.processEvents()
    _grab(w._sidebar_scroll.widget(), "filter_look_sidebar.png")

    # And again under the suite's own stylesheet, which is how the operator
    # really sees it: CSS Logger paints every QWidget #f3f3f3 on #111, and a
    # block that only looks right standalone is not right.
    print("\nunder the CSS Logger suite stylesheet")
    import main as css
    app.setStyleSheet(css._APP_STYLESHEET)
    w._sidebar_scroll.widget().adjustSize()
    QApplication.processEvents()
    _grab(w._sidebar_scroll.widget(), "filter_look_suite.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
