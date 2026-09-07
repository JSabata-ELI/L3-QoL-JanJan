"""Spectra tab — the Y axis must fit the spectra that are on the graph.

The bug: with "Every spectrum" showing thousands of curves the graph was scaled
to about -0.055 .. +0.055 while the spectra peaked at 1.0, so only a sliver just
above the baseline was visible.

Cause: matplotlib keeps its own running box of "where the data is" and silently
skips any artist it cannot place — the whole curve bundle is handed over as one
collection, and when that one is skipped the only thing left in the box is the
crosshair's horizontal line at zero. A box of zero height is then blown up to the
placeholder ±0.05, which is exactly the range that was reported.

The fix measures the range from the drawn curves instead. This test checks it
  * on normal data (the axis fits, with a little air),
  * and with the collection's own report forced to "nothing" — the failure above.

Run:  python testing/test_spectra_ylim.py
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
from matplotlib.collections import LineCollection
from matplotlib.transforms import Bbox
from PySide6.QtWidgets import QApplication

from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 3000
X = np.arange(NX, dtype=float)      # sample numbers, no wavelength axis resolved
PEAK = 1.0

_failed = False


def _fail(msg):
    global _failed
    _failed = True
    print("FAIL:", msg)


def _stack(n, centre, seed):
    rng = np.random.default_rng(seed)
    c = centre + rng.normal(0.0, 20.0, n)
    w = 60.0 + rng.normal(0.0, 5.0, n)
    a = PEAK * np.clip(1.0 + rng.normal(0.0, 0.05, n), 0, 1.0)
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


def _build():
    w = SpectraWidget()
    w.resize(1500, 900)
    w._x_data = X
    w._chk_autofit.setChecked(False)
    w._sb_x_min.setValue(1330)
    w._sb_x_max.setValue(2810)
    w._regions = [_region(1, 512, 2050, 1, "#455A64", 0),
                  _region(2, 1200, 2040, 2, "#EF6C00", 3600),
                  _region(3, 800, 2060, 3, "#B71C1C", 7200)]
    w._region_seq = 3
    w._rebuild_regions_ui()
    i = w._cmb_method.findText("Every spectrum")
    assert i >= 0, "'Every spectrum' missing from the Show box"
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()
    w._redraw_spectra()
    QApplication.processEvents()
    return w


def _drawn_extremes(w):
    lo = min(float(np.min(r["stack"])) for r in w._regions)
    hi = max(float(np.max(r["stack"])) for r in w._regions)
    return lo, hi


def _mpl_own_answer(ax):
    """What matplotlib alone would have scaled the axis to (the old behaviour)."""
    keep = ax.get_ylim()
    try:
        ax.set_autoscaley_on(True)
        ax.autoscale_view(scalex=False, scaley=True)
        return ax.get_ylim()
    finally:
        ax.set_ylim(keep)


def _check(w, tag):
    ax = w._ax_bot
    w._fig_bot.canvas.draw()
    lo, hi = _drawn_extremes(w)
    y0, y1 = ax.get_ylim()
    span = hi - lo
    m0, m1 = _mpl_own_answer(ax)
    print(f"{tag:26s} data {lo:+.4f}..{hi:+.4f}   axis {y0:+.4f}..{y1:+.4f}"
          f"   (matplotlib alone: {m0:+.4f}..{m1:+.4f})")
    if y1 < hi or y0 > lo:
        _fail(f"{tag}: the axis {y0:.4f}..{y1:.4f} cuts off data "
              f"that reaches {lo:.4f}..{hi:.4f}")
    # and not absurdly wide either — 5 % of the span on each side
    if (y1 - y0) > span * 1.5:
        _fail(f"{tag}: the axis {y0:.4f}..{y1:.4f} is far wider than the data")
    return (m0, m1)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app

    # ── 1. normal data ────────────────────────────────────────────────────────
    w = _build()
    _check(w, "normal data")
    w._fig_bot.savefig(os.path.join(OUT, "ylim_normal.png"), dpi=100)

    # ── 2. the failure: the bundle reports no extent at all ───────────────────
    # This is what left the graph on ±0.055 with the spectra peaking at 1.0.
    orig = LineCollection.get_datalim
    LineCollection.get_datalim = lambda self, trans: Bbox.null()
    try:
        w2 = _build()
        _, hi = _drawn_extremes(w2)
        m0, m1 = _check(w2, "bundle reports nothing")
        if m1 >= hi:
            _fail("the forced failure did not take effect — matplotlib alone "
                  f"still reaches {m1:.4f}, so the test proves nothing")
        w2._fig_bot.savefig(os.path.join(OUT, "ylim_poisoned.png"), dpi=100)
    finally:
        LineCollection.get_datalim = orig

    print("PASS" if not _failed else "FAIL")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
