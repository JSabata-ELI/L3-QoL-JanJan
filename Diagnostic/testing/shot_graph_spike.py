"""Render the live graph over the 23 Sep 2026 chiller dips and prove they show.

Run with QT_QPA_PLATFORM=windows — offscreen has no fonts and lies about text.

Nothing is clicked or typed: the panel is driven through its own Python API,
the same way the other shot_*.py scripts do it.

Everything is asserted before the picture is saved, so a wrong graph fails
loudly instead of quietly producing a plausible-looking PNG.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PySide6.QtWidgets import QApplication                        # noqa: E402

import cpva_api as api                                            # noqa: E402
import monitor_tab as mt                                          # noqa: E402

PV_NAME = "L3-UTIL-CHL03-006:Temp"
STRIDE = "--stride" in sys.argv
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
    else HERE


class _Win:
    """The few attributes GraphPanel reaches for on its owner."""

    def __init__(self, pv, rt):
        self.pvs = [pv]
        self.runtime = {pv.name: rt}
        self.settings = dict(mt.DEFAULT_SETTINGS)
        self.settings["graph_window_minutes"] = 7 * 60   # the whole fixture


def _stride(t, v, max_points):
    """What the graph did before 23 Sep 2026: keep every n-th reading."""
    n = t.size
    if n <= max_points:
        return t, v
    step = n / max_points
    idx = [int(i * step) for i in range(max_points)]
    idx[-1] = n - 1
    idx = np.asarray(idx)
    return t[idx], v[idx]


def main() -> int:
    fx = json.loads((HERE / "fixture_chiller_dips.json").read_text())
    t = np.asarray(fx["t_ns"], dtype=np.int64)
    v = np.asarray(fx["values"], dtype=np.float64)
    dip_ts = t[v == 10.0]
    print(f"fixture: {t.size} readings, {dip_ts.size} of them 10.0 degC")

    if STRIDE:
        mt._envelope = _stride                     # the control picture

    app = QApplication.instance() or QApplication([])

    pv = mt.PVConfig(name=PV_NAME, display_name="Utility Chiller", units="DegC")
    hist = mt._SampleHistory(span_ns=24 * 3600 * 10**9, cap=mt.HISTORY_HARD_CAP)
    hist.append_samples(t, v)
    rt = mt.PVRuntime(current_value=float(v[-1]), current_units="DegC",
                      last_update_ns=int(t[-1]), history=hist)

    win = _Win(pv, rt)
    panel = mt.GraphPanel(win)
    panel.refresh_combo()
    panel.combo.setCurrentIndex(panel.combo.findData(PV_NAME))  # single PV + limits
    panel.resize(1500, 600)
    panel.redraw()
    panel.canvas.draw()

    assert len(panel._curve_rows) == 1, panel._curve_rows
    line = panel._curve_rows[0][3]
    ys = np.asarray(line.get_ydata(), dtype=float)
    xs = np.asarray(line.get_xdata(orig=False), dtype=float)
    print(f"drawn: {ys.size} points, min {ys.min():.2f}, max {ys.max():.2f}")

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("  ok   " if cond else "  FAIL ") + name)

    if STRIDE:
        check("CONTROL: stride thinning never draws the dip", ys.min() > 15.0)
    else:
        check("the dip reaches the drawn curve", float(ys.min()) == 10.0)
        # x is in matplotlib date numbers, i.e. days: one minute is 1/1440.
        # The 11:54:20 dip is two readings in one second, so count clusters
        # rather than points.
        drawn_dips = xs[ys == 10.0]
        events = 1 + int(np.count_nonzero(np.diff(drawn_dips) > 1 / 1440))
        check(f"all three dip events are drawn (got {events})", events == 3)
        # the cursor readout reads the same data, through np.searchsorted
        arr, vals, _c, _l = panel._snap_series[0]
        snapped = panel._snap_at(arr, vals, mt._ns_to_num(int(dip_ts[0])))
        check("the cursor value box finds the dip too", snapped == 10.0)
    check("the newest reading survived",
          abs(xs[-1] - mt._ns_to_num(int(t[-1]))) < 1e-9)
    check("x is non-decreasing", bool(np.all(np.diff(xs) >= 0)))

    # The x axis is the one thing that breaks silently: _series hands over
    # floats now, so a missing xaxis_date would label the ticks 20000.8 and the
    # picture would still look fine at a glance.
    labels = [lb.get_text() for lb in panel.ax.get_xticklabels()]
    shown = [lb for lb in labels if lb]
    print(f"x tick labels: {shown[:3]} ... {shown[-2:]}")
    check("x ticks read as wall-clock times",
          all(len(lb) == 5 and lb[2] == ":" for lb in shown))

    y0, y1 = panel.ax.get_ylim()
    check("y axis spans the dip", y0 <= 10.0 <= y1 or STRIDE)
    print(f"y axis: {y0:.2f} .. {y1:.2f}")

    name = "graph_spike_stride.png" if STRIDE else "graph_spike_envelope.png"
    out = OUT / name
    panel.fig.savefig(out, dpi=110, facecolor=panel.fig.get_facecolor())
    print(f"saved {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
