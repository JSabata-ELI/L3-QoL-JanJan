"""Probe: what Y range does the "Every spectrum" graph end up with?

Feeds the real widget synthetic regions on a SAMPLE-NUMBER x axis (the case in
the screenshot: "Sample number (no wavelength axis resolved)") with spectra that
peak at 1.0, and prints the data limits the axes ended up with.

Run:  python testing/probe_ylim_range.py
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

import numpy as np
from PySide6.QtWidgets import QApplication

from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 3000
X = np.arange(NX, dtype=float)          # sample numbers, no wavelength axis


def _stack(n, centre, seed, peak=1.0):
    rng = np.random.default_rng(seed)
    c = centre + rng.normal(0.0, 20.0, n)
    w = 60.0 + rng.normal(0.0, 5.0, n)
    a = peak * (1.0 + rng.normal(0.0, 0.12, n))
    return (a[:, None] * np.exp(-0.5 * ((X[None, :] - c[:, None]) / w[:, None]) ** 2)
            + rng.normal(0.0, 0.004, (n, NX)))


def _region(rid, n, centre, seed, colour, t0_s):
    t0 = int((1_756_000_000 + t0_s) * 1e9)
    step = int(0.3e9)
    st = _stack(n, centre, seed)
    return {
        "id": rid, "t_start": t0, "t_end": t0 + n * step,
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": X, "stack": st,
        "stack_ts": [t0 + i * step for i in range(n)],
        "mean": st.mean(axis=0), "median": np.median(st, axis=0),
        "trimmed": st.mean(axis=0), "sigma": st.mean(axis=0),
        "std": st.std(axis=0),
        "p10": np.percentile(st, 10, axis=0),
        "p90": np.percentile(st, 90, axis=0),
        "orders": {}, "energy_avg": 0.0, "energy_n": n, "n": len(st),
    }


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    w = SpectraWidget()
    w.resize(1500, 900)
    w._x_data = X
    w._chk_autofit.setChecked(False)
    w._sb_x_min.setValue(1330)
    w._sb_x_max.setValue(2810)

    w._regions = [_region(1, 512, 2050, 1, "#455A64", 0),
                  _region(2, 2027, 2040, 2, "#EF6C00", 3600),
                  _region(3, 1089, 2060, 3, "#B71C1C", 7200),
                  _region(4, 1829, 2045, 4, "#6A1B9A", 10800)]
    w._region_seq = 4
    w._rebuild_regions_ui()
    i = w._cmb_method.findText("Every spectrum")
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()
    w._redraw_spectra()
    QApplication.processEvents()

    ax = w._ax_bot
    w._fig_bot.canvas.draw()

    real_max = max(float(np.max(r["stack"])) for r in w._regions)
    real_min = min(float(np.min(r["stack"])) for r in w._regions)
    print("norm mode        :", w._norm_mode())
    print("x spin           :", w._sb_x_min.value(), w._sb_x_max.value())
    print("real data        : %.4f .. %.4f" % (real_min, real_max))
    print("ax.dataLim y     :", ax.dataLim.intervaly)
    print("ax.get_ylim()    :", ax.get_ylim())
    print("collections      :", len(ax.collections),
          "segments:", sum(len(c.get_segments()) for c in ax.collections))
    for k, c in enumerate(ax.collections):
        dl = c.get_datalim(ax.transData)
        print(f"  collection {k}: get_datalim y = {dl.intervaly}")
    print("autoscale on y   :", ax.get_autoscaley_on())
    print("_bot_user_ylim   :", w._bot_user_ylim)
    print("_bot_user_xlim   :", w._bot_user_xlim)
    w._fig_bot.savefig(os.path.join(OUT, "probe_ylim_range.png"), dpi=100)
    print("saved", os.path.join(OUT, "probe_ylim_range.png"))


if __name__ == "__main__":
    main()
