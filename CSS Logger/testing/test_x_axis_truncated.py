"""Spectra tab — a truncated X axis, and the unit that goes with it.

The archiver does not always store the whole X axis. L3-SBDP-SPIDER:
TimeDomain_Int_X holds 2048 points while its _Y holds 4096, and the tab used to
compare the two lengths, find them unequal and quietly draw against array
positions — four analysed days came out labelled "Sample number", with a 33 fs
pulse exported as "FWHM 18.2 samples" and its peak as "2045".

What is checked here:

  THE AXIS
    * a uniform axis shorter than the waveform is rebuilt from its own first
      value and step, so 2048 archived points cover all 4096 samples,
    * the rebuilt axis lands on the real numbers (the SPIDER pulse peak sits at
      t = 0 fs, not at "sample 2045"),
    * the graph, the metrics, the auto-fitted From/To range and the CSV all use
      the same rebuilt axis — no two of them may disagree,
    * a NON-uniform short axis (a grating spectrometer's λ axis) is never
      extrapolated: that would invent numbers, so it still falls back to samples,
    * a longer-than-the-waveform axis and a length-1 axis fall back too.

  THE UNIT
    * the unit is guessed from the channel name — fs for the SPIDER time domain,
      nm for a spectrometer — and drives the axis title, the readout, the
      Peak/FWHM labels and the exported column names,
    * typing a unit overrides the guess and survives a save/load,
    * with no axis at all nothing claims a unit.

Renders testing/_out/x_axis_truncated.png so the axis can be looked at.

Run:  python testing/test_x_axis_truncated.py
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

import csv

import numpy as np
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

# The real SPIDER numbers, read out of the archive:
#   TimeDomain_Int_X  2048 points, -3749.0869140625 … -1.83060884475708 fs
#   TimeDomain_Int_Y  4096 points
# so the full axis is 4096 points of the same step and point 2048 is t = 0.
X_FIRST = -3749.0869140625
X_LAST_STORED = -1.83060884475708
N_STORED = 2048
N_FULL = 4096
STEP = (X_LAST_STORED - X_FIRST) / (N_STORED - 1)

T0 = int(1_756_000_000 * 1e9)
_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _stored_x() -> np.ndarray:
    """The archived half of the axis, quantised to float32 exactly as CPVA
    returns it — that quantisation is what makes the step non-constant in the
    last digits and is the reason the uniformity test needs a tolerance."""
    full = X_FIRST + STEP * np.arange(N_STORED, dtype=np.float64)
    return np.asarray(full, dtype=np.float32).astype(np.float64)


def _pulse(n_shots: int, centre_fs: float, width_fs: float, seed: int) -> np.ndarray:
    """n_shots time-domain intensity traces on the FULL 4096-point grid."""
    rng = np.random.default_rng(seed)
    t = X_FIRST + STEP * np.arange(N_FULL)
    c = centre_fs + rng.normal(0.0, 1.0, n_shots)
    wq = width_fs / (2.0 * np.sqrt(2.0 * np.log(2.0)))       # FWHM → sigma
    a = 1.0 + rng.normal(0.0, 0.05, n_shots)
    return (a[:, None] * np.exp(-0.5 * ((t[None, :] - c[:, None]) / wq) ** 2)
            + 1e-4)


def _region(rid: int, x, stack, colour="#1565C0"):
    ts = [T0 + i * 10 ** 9 for i in range(len(stack))]
    return {
        "id": rid, "t_start": ts[0], "t_end": ts[-1],
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": x, "stack": stack, "stack_ts": ts,
        "mean": stack.mean(axis=0), "median": np.median(stack, axis=0),
        "trimmed": stack.mean(axis=0), "sigma": stack.mean(axis=0),
        "std": stack.std(axis=0),
        "p10": np.percentile(stack, 10, axis=0),
        "p90": np.percentile(stack, 90, axis=0),
        "orders": {"GDD": 23504.0, "TOD": -88000.0, "FOD": -15000.0},
        "energy_avg": -0.108411, "energy_n": len(stack), "n": len(stack),
    }


def _widget():
    w = SpectraWidget()
    w.resize(1500, 900)
    # Pin the channel so the test does not depend on whichever PV happens to be
    # saved on this machine — the unit is guessed from this name.
    w._spec_base_pv = "L3-SBDP-SPIDER:TimeDomain_Int"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._edit_x_unit.setText(w._x_unit())
    w._sync_x_unit_labels()
    return w


# ── the helper, on its own ────────────────────────────────────────────────────
def test_fit_helper():
    print("\ntest_fit_helper  (the axis rebuild rule)")
    xs = _stored_x()
    got = sp_t._fit_x_axis(xs, N_FULL)
    _ok(got is not None and len(got) == N_FULL,
        "2048 uniform points cover a 4096-point waveform",
        f"{None if got is None else len(got)}")
    _ok(abs(got[0] - X_FIRST) < 1e-3, "it keeps the archived first value",
        f"{got[0]:.4f}")
    _ok(abs(got[N_STORED]) < 0.2,
        "point 2048 comes out at t = 0 fs — the pulse really is centred there",
        f"{got[N_STORED]:.4f} fs")
    _ok(np.allclose(got[:N_STORED], xs, atol=0.02),
        "the archived half is not moved by the rebuild")

    _ok(sp_t._fit_x_axis(xs, N_STORED) is xs or
        np.array_equal(sp_t._fit_x_axis(xs, N_STORED), xs),
        "a matching length is returned untouched")

    # A grating spectrometer's λ axis: short AND not uniform → never extended.
    lam = np.linspace(700.0, 900.0, 1024) + 8.0 * np.sin(
        np.linspace(0, 3.0, 1024))
    _ok(sp_t._fit_x_axis(lam, 2048) is None,
        "a non-uniform short axis is NOT extrapolated")

    _ok(sp_t._fit_x_axis(np.array([5.0]), 4096) is None,
        "a single stored point gives no axis")
    _ok(sp_t._fit_x_axis(np.zeros(64), 4096) is None,
        "a flat (zero-step) axis gives no axis")
    _ok(sp_t._fit_x_axis(None, 4096) is None, "no axis stays no axis")
    long_x = X_FIRST + STEP * np.arange(8192)
    got_l = sp_t._fit_x_axis(long_x, N_FULL)
    _ok(got_l is not None and len(got_l) == N_FULL,
        "an axis LONGER than the waveform is cut to it, not dropped")
    nan_x = _stored_x().copy()
    nan_x[10] = np.nan
    _ok(sp_t._fit_x_axis(nan_x, N_FULL) is None,
        "an axis with a hole in it gives no axis")


# ── the panel ─────────────────────────────────────────────────────────────────
def test_panel_uses_it(w):
    print("\ntest_panel_uses_it  (graph, metrics, range, CSV)")
    stack = _pulse(40, centre_fs=-5.5, width_fs=33.3, seed=1)
    w._x_data = _stored_x()
    w._regions = [_region(1, _stored_x(), stack)]
    w._region_seq = 1
    w._chk_autofit.setChecked(True)
    w._rebuild_regions_ui()

    _ok(not w._x_is_samples(),
        "the panel no longer calls this 'sample numbers'")
    _ok(w._x_title() == "Time [fs]", "the axis is titled Time [fs]",
        w._x_title())

    xc = w._curve_x(w._regions[0], N_FULL)
    _ok(len(xc) == N_FULL and abs(xc[0] - X_FIRST) < 1e-3,
        "the drawn axis is the rebuilt one", f"{xc[0]:.2f} … {xc[-1]:.2f}")

    # Metrics: FWHM must come out as the pulse duration in fs, not in samples.
    w._auto_fit_range()
    QApplication.processEvents()
    lo, hi = w._sb_x_min.value(), w._sb_x_max.value()
    _ok(lo < 0 < hi and lo > -2000,
        "auto-fit lands the From/To range on the pulse, in fs", f"{lo} … {hi}")

    # Harness detail, not the feature: the widget is never shown, so the empty
    # placeholder graph's own ±0.05 autoscale is still queued and gets recorded as
    # a "user zoom" the moment events are pumped. In the running program the
    # placeholder has long been painted before Analyze. Drop it, or the picture
    # below comes out empty for a reason that has nothing to do with the axis.
    w._bot_user_xlim = None
    w._bot_user_ylim = None
    w._redraw_spectra()
    QApplication.processEvents()
    m = (w._regions[0].get("_metrics") or {})
    _ok(m.get("fwhm") is not None and 25.0 < m["fwhm"] < 45.0,
        "FWHM is the 33 fs pulse duration, not 18 array positions",
        f"{m.get('fwhm')}")
    _ok(m.get("peak_wl") is not None and abs(m["peak_wl"]) < 60.0,
        "the peak is reported near t = 0 fs, not at sample 2045",
        f"{m.get('peak_wl')}")

    html = w._metrics_html(m)
    _ok("Peak t:" in html and "fs" in html,
        "the details box says Peak t and fs, not Peak λ and nm", html[:90])
    _ok("λ" not in html and " nm" not in html,
        "no nanometres anywhere in the details box")

    note = w._x_fit_note()
    _ok("2048 of the 4096" in note,
        "the panel owns up to the axis being rebuilt", note)

    lbl = w._ax_bot.get_xlabel()
    _ok(lbl == "Time [fs]", "the drawn graph carries that title", lbl)

    png = os.path.join(OUT, "x_axis_truncated.png")
    w._fig_bot.savefig(png, dpi=110)
    print(f"        wrote {png}")


def test_csv(w, path):
    print("\ntest_csv")
    w._export_csv(path, [(0, w._regions[0])], [])
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    flat = "\n".join(";".join(r) for r in rows)
    _ok("time_fs" in flat, "the X column is named time_fs")
    _ok("sample_number" not in flat, "no sample_number column")
    _ok("Peak t [fs]" in flat, "the metric headers are in fs")
    _ok("[nm]" not in flat, "no nanometre header survived")
    _ok("2048 of the 4096" in flat,
        "the file says the axis was rebuilt, so nobody takes it for archived")
    hdr = next(r for r in rows if r and r[0] == "time_fs")
    i = rows.index(hdr)
    first_val = float(rows[i + 1][0].replace(",", "."))
    last_val = float(rows[-1][0].replace(",", "."))
    _ok(first_val < 0 and last_val > 0,
        "the exported axis really spans both sides of t = 0",
        f"{first_val:.2f} … {last_val:.2f}")


def test_units(w):
    print("\ntest_units")
    _ok(sp_t._guess_x_unit("L3-SBDP-SPIDER:TimeDomain_Int_X") == "fs",
        "TimeDomain guesses fs")
    _ok(sp_t._guess_x_unit("L3-SBDP-SPIDER:SpecDomain_Int_X") == "nm",
        "SpecDomain guesses nm")
    _ok(sp_t._guess_x_unit("L3-SBDP-SPIDER:TimeDomain_Phase_X") == "fs",
        "the phase channel's AXIS is still fs")
    _ok(sp_t._guess_x_unit("L3-SBW4-SPEC:Wavelengths") == "nm",
        "an unknown spectrometer channel stays on nm")
    _ok(sp_t._x_unit_kind("fs") == ("Time", "t"), "fs is a time")
    _ok(sp_t._x_unit_kind("nm") == ("Wavelength", "λ"), "nm is a wavelength")
    _ok(sp_t._x_unit_kind("bananas") == ("X", "x"),
        "an unknown unit stays generic instead of guessing wrong")

    # Typing a unit wins over the guess, and is remembered.
    w._edit_x_unit.setText("ps")
    w._on_x_unit_edited()
    QApplication.processEvents()
    _ok(w._x_unit() == "ps", "a typed unit overrides the guess", w._x_unit())
    _ok(w._x_title() == "Time [ps]", "and reaches the axis title", w._x_title())
    _ok("range [ps]" in w._g_xrange.title(),
        "and the From/To group title", w._g_xrange.title())
    _ok(w._x_axis_cfg.get("unit") == "ps",
        "it is stored in the axis config, so it is saved with the PV")

    w._edit_x_unit.setText("")
    w._on_x_unit_edited()
    QApplication.processEvents()
    _ok(w._x_unit() == "fs", "clearing it goes back to the guess", w._x_unit())


def test_no_axis_at_all(w):
    print("\ntest_no_axis_at_all  (the honest fallback still works)")
    stack = _pulse(6, centre_fs=0.0, width_fs=33.3, seed=3)
    lam = np.linspace(700.0, 900.0, 1024) + 8.0 * np.sin(np.linspace(0, 3.0, 1024))
    w._regions = [_region(1, lam, stack)]
    w._x_data = lam
    w._rebuild_regions_ui()
    _ok(w._x_is_samples(),
        "a short non-uniform axis still means sample numbers")
    _ok("Sample number" in w._x_title(), "and the title says so", w._x_title())
    html = w._metrics_html(w._regions[0].get("_metrics") or
                           {"peak_wl": 2045.0, "fwhm": 18.2})
    _ok("Peak sample" in html and "fs" not in html and "nm" not in html,
        "the details box claims no unit at all", html[:80])


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # Typing a unit saves the spectrum config; keep the test out of the real one.
    sp_t._spec_pvs_config_path = lambda: os.path.join(OUT, "spec_pvs_test.json")
    test_fit_helper()
    w = _widget()
    test_panel_uses_it(w)
    test_csv(w, os.path.join(OUT, "x_axis_truncated.csv"))
    test_units(w)
    test_no_axis_at_all(w)
    print("\nFAILURES PRESENT" if _failed else "\nall good")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
