"""Spectra tab — every curve on the graph must sit on the SAME X axis, and the
intensity axis must fit what is drawn.

Four things were reported on real SPIDER data (4 days, 5799 shots, "Every
spectrum"), and they are all one story:

  1. the intensity axis stopped at 0.05 while the spectra peaked at 1.0,
  2. the auto-fitted From/To range did not cover the drawn data at all,
  3. changing From/To moved the intensity axis to a range nobody had asked for,
  4. the picked "this one" spectrum was drawn at t = 0 while the coloured bundle
     it belongs to sat around "2000".

Two causes:

  * _plot_all_spectra decided its axis with a bare length test, so the whole
    bundle fell back to sample numbers (0…4095) while the averaged curve, the
    bold picked one, the metrics and the auto-fitted range all used the real
    femtosecond axis rebuilt from the archive's 2048 stored points. Hence (4),
    hence (2), and hence (1): a range fitted in fs, applied to sample numbers,
    kept a slice of the bundle's own baseline.
  * The empty placeholder graph autoscales itself to ±0.05 while it is being
    drawn — after the redraw flag is dropped — and that was filed away as "the
    user's zoom" and re-applied on every later redraw. Hence (1) again and (3).

Run:  python testing/test_bundle_axis.py
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

# The real SPIDER numbers, read out of the archive: the X channel holds 2048 of
# the waveform's 4096 points, so the full axis runs -3749 … +3747 fs and point
# 2048 is t = 0.
X_FIRST = -3749.0869140625
X_LAST_STORED = -1.83060884475708
N_STORED = 2048
N_FULL = 4096
STEP = (X_LAST_STORED - X_FIRST) / (N_STORED - 1)
PEAK_FS = -5.5          # where the pulse really is
T0 = int(1_756_000_000 * 1e9)

_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _stored_x() -> np.ndarray:
    """The archived half of the axis, float32-quantised as CPVA returns it."""
    full = X_FIRST + STEP * np.arange(N_STORED, dtype=np.float64)
    return np.asarray(full, dtype=np.float32).astype(np.float64)


def _pulse(n_shots: int, seed: int) -> np.ndarray:
    """n_shots time-domain traces on the FULL 4096-point grid, peaking at ~1."""
    rng = np.random.default_rng(seed)
    t = X_FIRST + STEP * np.arange(N_FULL)
    c = PEAK_FS + rng.normal(0.0, 1.0, n_shots)
    wq = 33.3 / (2.0 * np.sqrt(2.0 * np.log(2.0)))           # FWHM → sigma
    a = 1.0 + rng.normal(0.0, 0.05, n_shots)
    return (a[:, None] * np.exp(-0.5 * ((t[None, :] - c[:, None]) / wq) ** 2)
            + 1e-4)


def _region(rid: int, stack, colour="#1565C0"):
    ts = [T0 + i * 10 ** 9 for i in range(len(stack))]
    return {
        "id": rid, "t_start": ts[0], "t_end": ts[-1],
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": _stored_x(), "stack": stack, "stack_ts": ts,
        "mean": stack.mean(axis=0), "median": np.median(stack, axis=0),
        "trimmed": stack.mean(axis=0), "sigma": stack.mean(axis=0),
        "std": stack.std(axis=0),
        "p10": np.percentile(stack, 10, axis=0),
        "p90": np.percentile(stack, 90, axis=0),
        "orders": {}, "energy_avg": None, "energy_n": 0, "n": len(stack),
    }


def _widget():
    w = SpectraWidget()
    w.resize(1500, 900)
    w._spec_base_pv = "L3-SBDP-SPIDER:TimeDomain_Int"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._edit_x_unit.setText(w._x_unit())
    w._sync_x_unit_labels()
    return w


def _bundle_xy(w):
    """Every vertex of the drawn spectra bundle, as (x, y) arrays."""
    xs, ys = [], []
    ax = w._ax_bot
    for coll in ax.collections:
        if coll.get_transform() != ax.transData:
            continue
        for p in coll.get_paths():
            if p.vertices.size:
                xs.append(p.vertices[:, 0])
                ys.append(p.vertices[:, 1])
    if not xs:
        return np.empty(0), np.empty(0)
    return np.concatenate(xs), np.concatenate(ys)


def _show_every_spectrum(w):
    i = w._cmb_method.findText("Every spectrum")
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()


# ── the placeholder's own scale is not a zoom ────────────────────────────────
def test_placeholder_is_not_a_zoom(w):
    print("\ntest_placeholder_is_not_a_zoom  (where the 0.05 came from)")
    w._draw_bot_empty()
    w._canvas_bot.draw()            # the autoscale happens HERE, not in the redraw
    QApplication.processEvents()
    _ok(w._bot_user_ylim is None,
        "the empty graph's own range is not filed away as the user's zoom",
        str(w._bot_user_ylim))
    _ok(w._bot_user_xlim is None, "same for the X range", str(w._bot_user_xlim))


# ── one axis for every curve ─────────────────────────────────────────────────
def test_one_axis_for_every_curve(w):
    print("\ntest_one_axis_for_every_curve  (bundle, picked curve, range, Y)")
    w._x_data = _stored_x()
    w._regions = [_region(1, _pulse(40, seed=1)),
                  _region(2, _pulse(25, seed=2), "#EF6C00")]
    w._region_seq = 2
    w._chk_autofit.setChecked(True)
    w._rebuild_regions_ui()
    _show_every_spectrum(w)
    w._auto_fit_range()
    w._redraw_spectra()
    w._canvas_bot.draw()
    QApplication.processEvents()

    lo, hi = w._sb_x_min.value(), w._sb_x_max.value()
    _ok(lo < PEAK_FS < hi, "the auto-fitted range is around the pulse, in fs",
        f"{lo} … {hi}")

    bx, by = _bundle_xy(w)
    _ok(bx.size > 0, "the bundle is on the graph", f"{bx.size} points")
    _ok(bx.min() >= lo - 1 and bx.max() <= hi + 1,
        "and it is drawn inside the fitted range, not outside it",
        f"{bx.min():.1f} … {bx.max():.1f}")
    peak_x = float(bx[int(np.argmax(by))])
    _ok(abs(peak_x - PEAK_FS) < 20.0,
        "the bundle's own peak is at t ≈ 0 fs, not at sample 2048",
        f"{peak_x:.1f} fs")

    # The bold "this one" curve must land on the same axis as the bundle.
    r, k, _it = w._single_current()
    _ok(r is not None, "the shot bar is on a spectrum")
    sx, sy = w._single_curve(r, k)
    _ok(sx.size > 0 and abs(float(sx[int(np.argmax(sy))]) - peak_x) < 20.0,
        "the picked spectrum peaks where the bundle does",
        f"{float(sx[int(np.argmax(sy))]):.1f} fs")
    _ok(abs(sx.min() - bx.min()) < 1e-6 and abs(sx.max() - bx.max()) < 1e-6,
        "on the very same stretch of axis", f"{sx.min():.1f} … {sx.max():.1f}")

    y_lo, y_hi = w._ax_bot.get_ylim()
    _ok(y_hi > 0.9, "the intensity axis reaches the peaks (1.0), not 0.05",
        f"{y_lo:.3f} … {y_hi:.3f}")
    _ok(y_hi < 1.5, "and does not leave the graph swimming in air",
        f"{y_hi:.3f}")

    w._fig_bot.savefig(os.path.join(OUT, "bundle_axis.png"), dpi=110)
    print("        wrote", os.path.join(OUT, "bundle_axis.png"))


# ── a new X range does not throw the Y axis around ───────────────────────────
def test_x_range_keeps_y(w):
    print("\ntest_x_range_keeps_y  (typing From/To)")
    # No zoom of the user's own: Y refits to what is now drawn, which is the
    # point of the range box — and it must still cover the curves.
    w._sb_x_min.setValue(-200)
    w._sb_x_max.setValue(200)
    w._on_x_range_edited()
    w._canvas_bot.draw()
    QApplication.processEvents()
    y_hi = w._ax_bot.get_ylim()[1]
    _ok(y_hi > 0.9, "a tighter range still shows the whole pulse height",
        f"{y_hi:.3f}")

    # Now a real gesture: the user zooms the intensity axis by hand. That must
    # survive both a redraw and a new From/To.
    w._tb_bot.mode = "zoom rect"        # what the toolbar sets while zooming
    w._ax_bot.set_ylim(0.0, 0.4)
    QApplication.processEvents()
    w._tb_bot.mode = ""
    _ok(w._bot_user_ylim is not None, "a hand zoom IS remembered",
        str(w._bot_user_ylim))
    w._sb_x_min.setValue(-400)
    w._sb_x_max.setValue(400)
    w._on_x_range_edited()
    w._canvas_bot.draw()
    QApplication.processEvents()
    _ok(abs(w._ax_bot.get_ylim()[1] - 0.4) < 1e-6,
        "and a new X range keeps it instead of inventing a range",
        f"{w._ax_bot.get_ylim()[1]:.3f}")

    # Home hands the graph back to the data.
    w._forget_axis_limits(w._ax_bot)
    w._redraw_spectra()
    w._canvas_bot.draw()
    QApplication.processEvents()
    _ok(w._ax_bot.get_ylim()[1] > 0.9, "Home / Reset view fits the data again",
        f"{w._ax_bot.get_ylim()[1]:.3f}")


def test_range_off_the_data_says_so(w):
    print("\ntest_range_off_the_data_says_so  (instead of a blank ±0.05 graph)")
    for method in ("Every spectrum", "Mean"):
        i = w._cmb_method.findText(method)
        w._cmb_method.setCurrentIndex(i)
        w._sb_x_min.setValue(9000)          # nowhere near the data
        w._sb_x_max.setValue(9500)
        w._on_x_range_edited()
        w._canvas_bot.draw()
        QApplication.processEvents()
        texts = " ".join(t.get_text() for t in w._ax_bot.texts)
        _ok("Nothing inside" in texts and "The spectra cover" in texts,
            f"{method}: the graph says the range misses the data",
            texts.replace("\n", " ")[:90])
    # back to something sane for whatever runs after this
    w._chk_autofit.setChecked(True)
    w._auto_fit_range()
    w._redraw_spectra()
    QApplication.processEvents()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    w = _widget()
    test_placeholder_is_not_a_zoom(w)
    test_one_axis_for_every_curve(w)
    test_x_range_keeps_y(w)
    test_range_off_the_data_says_so(w)
    print("\nRESULT:", "FAILED" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
