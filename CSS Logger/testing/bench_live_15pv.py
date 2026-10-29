"""How long one Live refresh takes with 15 PVs on the graph.

Measures, on a window as Live leaves it after a while (a thinned start with
min/max bands plus a raw tail polled at 1 Hz):

  update  - _update_graph_data, the graph's data refresh (Python side)
  draw    - one full canvas render of the figure
  scroll  - sliding the time axis between refreshes, render included
  table   - the table rebuild Live runs every ~1.5 s

No network. Headless (offscreen), no window takes the focus:

    python testing/bench_live_15pv.py [n_pvs] [span_hours] [tail_minutes]
"""
import os
import sys
import time
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import test_smoke as smoke                      # noqa: E402
import cpva_core                                # noqa: E402
import main as app_main                         # noqa: E402

N_PV = int(sys.argv[1]) if len(sys.argv) > 1 else 15
SPAN_H = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
TAIL_MIN = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
PVS = [f"L3-BENCH-{i:02d}:Energy" for i in range(N_PV)]


def _fake_adaptive(pvs, start_ns, end_ns, count=None, **_k):
    """A thinned read: `count` points per PV, each with a min/max spread."""
    n = int(count or 2000)
    span = end_ns - start_ns
    out = {}
    for j, pv in enumerate(pvs):
        rows = []
        for i in range(n):
            v = 100.0 + 10.0 * ((i * 7 + j) % 13)
            rows.append({"time": start_ns + span * i // n, "value": v,
                         "minimum": v - 3.0, "maximum": v + 3.0,
                         "metaData": {"units": "J"}})
        out[pv] = rows
    return out, {}, None


cpva_core.cpva_fetch_many_adaptive = _fake_adaptive
cpva_core.cpva_fetch_last_before_many = lambda *a, **k: {}


def _t(fn, n):
    best = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        best.append(time.perf_counter() - t0)
    best.sort()
    return best[len(best) // 2] * 1000.0        # median, ms


def main() -> int:
    smoke._install_network_mock()
    smoke._install_dialog_mocks()
    w = smoke._new_widget()
    smoke._set_pvs(w, PVS)
    now = app_main.datetime.now()
    w._dt_from, w._dt_to = now - timedelta(hours=SPAN_H), now
    w.resize(1600, 950)
    w.show()
    smoke._app.processEvents()
    smoke._load_and_wait(w)

    # Turn the loaded window into a Live one with a raw 1 Hz tail.
    w._live_mode = True
    w._live_window_span = timedelta(hours=SPAN_H)
    end = app_main.now_ns()
    tail = int(TAIL_MIN * 60)
    for j, pv in enumerate(PVS):
        s = w._samples_by_pv[pv]
        s[:] = [x for x in s if x[0] < end - tail * 10**9]
        for i in range(tail):
            s.append((end - (tail - i) * 10**9, 100.0 + (i + j) % 17, "J"))
    w._plot_graph()
    smoke._app.processEvents()
    canvas = w._mpl_canvas
    n_pts = sum(len(w._samples_by_pv[p]) for p in PVS)
    print(f"{N_PV} PVs, {SPAN_H:g} h window, {TAIL_MIN:g} min raw tail, "
          f"{n_pts} samples")

    k = [0]

    def _update():
        # A new sample per PV, as a Live tick brings.
        k[0] += 1
        t = app_main.now_ns()
        for pv in PVS:
            w._samples_by_pv[pv].append((t + k[0], 101.0, "J"))
        w._update_graph_data()

    def _scroll():
        w._scroll_live_time_axis()
        canvas.draw()

    def _table():
        rows = w._build_table_rows(w._samples_by_pv, w._base_pv_order or w._pv_order,
                                   w._pre_window_vals)
        rows = w._remove_master_only_rows(rows)
        rows = w._remove_fake_hour_boundary_rows(rows)
        w._table_rows_unfiltered = rows
        w._rebuild_custom_pvs()
        rows = w._filter_master_multiple_rows(w._table_rows_unfiltered)
        w._table_rows = w._apply_conditions_to_rows(rows)
        w._populate_table()
        w._flush_axis_measured()

    print(f"  update  {_t(_update, 7):8.1f} ms")
    print(f"  draw    {_t(canvas.draw, 7):8.1f} ms")
    print(f"  scroll  {_t(_scroll, 7):8.1f} ms")
    print(f"  table   {_t(_table, 3):8.1f} ms")
    w._live_mode = False
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
