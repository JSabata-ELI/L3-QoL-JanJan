"""Spectra tab — "Every spectrum" display mode.

Feeds the widget synthetic regions (no archiver needed), switches the Display
"Show" box to "Every spectrum" and checks that

  * every spectrum of a region is drawn, not one averaged curve,
  * a region larger than MAX_SINGLE_LINES is evenly thinned and BOTH numbers
    reach the title and the legend,
  * the CSV export writes one column per drawn shot, named by its time,
  * switching back to Mean restores the single averaged curve.

Also renders the bottom graph to PNG so the result can be looked at.

Run:  python testing/test_every_spectrum.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget, MAX_SINGLE_LINES

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

# Points per spectrum. Override to time the drawing on a realistic waveform:
#   set SPEC_TEST_NX=2048 && python testing/test_every_spectrum.py
NX = int(os.environ.get("SPEC_TEST_NX", "512"))
X = np.linspace(780.0, 840.0, NX)


def _make_stack(n: int, centre: float, seed: int) -> np.ndarray:
    """n gaussian spectra that wander in centre, width and height."""
    rng = np.random.default_rng(seed)
    c = centre + rng.normal(0.0, 1.2, n)
    w = 8.0 + rng.normal(0.0, 0.6, n)
    a = 1.0 + rng.normal(0.0, 0.12, n)
    return (a[:, None] * np.exp(-0.5 * ((X[None, :] - c[:, None]) / w[:, None]) ** 2)
            + rng.normal(0.0, 0.004, (n, NX)))


def _region(w, rid: int, n: int, centre: float, seed: int, colour: str, t0_s: int):
    t0 = int((1_756_000_000 + t0_s) * 1e9)
    step = int(0.3e9)
    stack = _make_stack(n, centre, seed)
    return {
        "id": rid, "t_start": t0, "t_end": t0 + n * step,
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": X, "stack": stack,
        "stack_ts": [t0 + i * step for i in range(n)],
        "mean": stack.mean(axis=0), "median": np.median(stack, axis=0),
        "trimmed": stack.mean(axis=0), "sigma": stack.mean(axis=0),
        "std": stack.std(axis=0),
        "p10": np.percentile(stack, 10, axis=0),
        "p90": np.percentile(stack, 90, axis=0),
        "orders": {"GDD": 1234.0, "TOD": -5678.0, "FOD": 9.0},
        "energy_avg": 9.87, "energy_n": n, "n": len(stack),
    }


def _set_show(w, text: str):
    i = w._cmb_method.findText(text)
    assert i >= 0, f"'{text}' missing from the Show box: " \
                   f"{[w._cmb_method.itemText(k) for k in range(w._cmb_method.count())]}"
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()


def _n_line_segments(ax) -> int:
    from matplotlib.collections import LineCollection
    return sum(len(c.get_segments()) for c in ax.collections
               if isinstance(c, LineCollection))


def _fail(msg):
    print("FAIL:", msg)
    globals()["_failed"] = True


_failed = False


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = SpectraWidget()
    w.resize(1500, 900)
    # The fixture is a nanometre spectrum, so pin a spectrometer channel — the
    # axis unit, and with it the exported column name, comes from the channel
    # name, and the saved PV on this machine may be the SPIDER time domain.
    w._spec_base_pv = "L3-SBW4-SPEC:Spectrum"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._x_data = X
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)
    w._chk_autofit.setChecked(False)

    # ── small regions: every single spectrum must be on screen ─────────────
    w._regions = [_region(w, 1, 25, 806.0, 1, "#1565C0", 0),
                  _region(w, 2, 60, 812.0, 2, "#C62828", 3600)]
    w._region_seq = 2
    w._rebuild_regions_ui()
    _set_show(w, "Every spectrum")
    w._redraw_spectra()
    ax = w._ax_bot

    segs = _n_line_segments(ax)
    if segs != 85:
        _fail(f"expected 25+60=85 individual curves, drew {segs}")
    title = ax.get_title()
    if "Every spectrum" not in title or "85" not in title:
        _fail(f"title does not name the count: {title!r}")
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    if not any("25 spectra" in s for s in labels) or \
       not any("60 spectra" in s for s in labels):
        _fail(f"legend does not name each region's count: {labels}")
    print("small regions :", segs, "curves |", title, "|", labels)

    w._fig_bot.savefig(os.path.join(OUT, "every_spectrum_small.png"),
                       dpi=110, bbox_inches="tight")

    # the variation band describes an average — greyed out here
    if w._chk_std.isEnabled() or w._cmb_band.isEnabled():
        _fail("variation band still enabled while showing every spectrum")

    # ── CSV: one column per shot, named by its time ────────────────────────
    csv_path = os.path.join(OUT, "every_spectrum.csv")
    w._export_csv(csv_path, [(i, r) for i, r in enumerate(w._regions)], [])
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    hdr = next(l for l in lines if l.startswith("wavelength_nm"))
    cols = hdr.split(";")
    if len(cols) != 1 + 85:
        _fail(f"CSV should hold 85 spectrum columns, has {len(cols) - 1}")
    if ":" not in cols[1]:
        _fail(f"CSV columns are not named by time: {cols[1:4]}")
    detail = next(l for l in lines if l.startswith("Spectrum 1;")
                  or l.startswith("Region 1;"))
    if "every spectrum" not in detail:
        _fail(f"details block does not say what was exported: {detail}")
    print("csv           :", len(cols) - 1, "columns |", cols[1], "…", cols[-1])

    # ── a big region is thinned, and says so ───────────────────────────────
    big = MAX_SINGLE_LINES * 3 + 7
    w._regions = [_region(w, 1, big, 806.0, 3, "#1565C0", 0)]
    w._rebuild_regions_ui()
    t0 = time.perf_counter()
    w._redraw_spectra()
    w._canvas_bot.draw()
    dt = time.perf_counter() - t0
    segs = _n_line_segments(w._ax_bot)
    if segs > MAX_SINGLE_LINES or segs < MAX_SINGLE_LINES * 0.9:
        _fail(f"{big} spectra should thin down to ~{MAX_SINGLE_LINES}, drew {segs}")
    title = w._ax_bot.get_title()
    labels = [t.get_text() for t in w._ax_bot.get_legend().get_texts()]
    if str(big) not in title or str(segs) not in title:
        _fail(f"title hides the thinning: {title!r}")
    if not any(f"{segs} of {big}" in s for s in labels):
        _fail(f"legend hides the thinning: {labels}")
    print("big region    :", segs, "of", big, f"| redraw+draw {dt * 1000:.0f} ms "
          f"({NX} points/spectrum) |", title, "|", labels)
    w._fig_bot.savefig(os.path.join(OUT, "every_spectrum_big.png"),
                       dpi=110, bbox_inches="tight")

    # ── back to Mean: one curve again ──────────────────────────────────────
    _set_show(w, "Mean")
    w._redraw_spectra()
    if _n_line_segments(w._ax_bot) != 0:
        _fail("individual curves left behind after switching back to Mean")
    # crosshair artists are lines too — count only the labelled data curves
    real = [l for l in w._ax_bot.get_lines()
            if len(l.get_xdata()) and not str(l.get_label()).startswith("_")]
    if len(real) != 1:
        _fail(f"Mean should draw exactly one curve, drew {len(real)}")
    if not w._chk_std.isEnabled():
        _fail("variation band stayed greyed out after leaving Every spectrum")
    print("back to Mean  :", len(real), "curve |", w._ax_bot.get_title())

    w.deleteLater()
    print("\nOutput in", OUT)
    print("RESULT:", "FAILED" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
