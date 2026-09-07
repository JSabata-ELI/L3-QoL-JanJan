"""Spectra tab — the "Every spectrum — pick one" bar, and a full export.

Two features, one fixture, because they are two halves of the same question:
"which shot am I looking at, and did I get all of them in the file?"

The bar's own two rules — it lines up with the search graph pixel for pixel, and
it can only stand on a real measurement — are tested in test_shot_bar.py.

  THE BAR
    * it only exists while the display is Every spectrum,
    * it lists every drawn shot of every VISIBLE spectrum, ordered by the time it
      was measured — interleaved across regions, and across days,
    * moving it points the bold curve at that shot, in that region's colour, and
      the caption beside it and the in-graph tag name the same time,
    * hiding a spectrum keeps you on the shot you were on, it does not renumber
      you onto a different one,
    * unticking the box, and switching back to Mean, hide the bold curve.

  EXPORT
    * the CSV writes EVERY shot even when the graph thinned the picture,
    * the X column and the λ headers say "sample" when no wavelength axis was
      resolved — the case where the graph used to draw sample numbers and label
      them nanometres,
    * a region whose waveform length differs from the export grid is resampled
      onto it instead of having its tail blanked.

Renders testing/_out/single_browser_*.png so the bold curve can be looked at.

Run:  python testing/test_single_browser.py
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

NX = 256
X = np.linspace(780.0, 840.0, NX)
T0 = int(1_756_000_000 * 1e9)          # a fixed wall-clock start, in ns
DAY = int(86_400 * 1e9)

_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _stack(n: int, centre: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    c = centre + rng.normal(0.0, 1.2, n)
    w = 8.0 + rng.normal(0.0, 0.6, n)
    a = 1.0 + rng.normal(0.0, 0.12, n)
    return (a[:, None] * np.exp(-0.5 * ((X[None, :] - c[:, None]) / w[:, None]) ** 2)
            + rng.normal(0.0, 0.004, (n, NX)))


def _region(rid: int, n: int, centre: float, seed: int, colour: str,
            times, x=X, nx=NX):
    """times: the ns timestamp of each shot (given, so they can interleave)."""
    st = _stack(n, centre, seed) if nx == NX else _stack(n, centre, seed)[:, :nx]
    return {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": x, "stack": st, "stack_ts": list(times),
        "mean": st.mean(axis=0), "median": np.median(st, axis=0),
        "trimmed": st.mean(axis=0), "sigma": st.mean(axis=0),
        "std": st.std(axis=0),
        "p10": np.percentile(st, 10, axis=0),
        "p90": np.percentile(st, 90, axis=0),
        "orders": {"GDD": 1234.0, "TOD": -5678.0, "FOD": 9.0},
        "energy_avg": 9.87, "energy_n": n, "n": len(st),
    }


def _set_show(w, text: str):
    i = w._cmb_method.findText(text)
    assert i >= 0, f"'{text}' missing from the Show box"
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()


def _load_time_axis(w, *shot_times):
    """Give the tab the search-graph time axis the shots were picked from.

    The bar under that graph is pinned to this axis, so without it there is
    nothing to map a shot's time onto. One window per day, an hour of air on each
    side, exactly as loading those days would give."""
    hour = 3600 * 10 ** 9
    wins = []
    for times in shot_times:
        wins.append((min(times) - hour, max(times) + hour))
    w._windows = wins
    w._tmap = sp_t._TimeMap(wins)
    w._ax_top.set_xlim(*w._tmap.xlim())
    return wins


def _widget():
    w = SpectraWidget()
    w.resize(1500, 900)
    # The fixture is a nanometre spectrum, so pin a spectrometer channel: the
    # axis unit (and with it the exported column name) is taken from the channel
    # name, and whatever PV happens to be saved on this machine may be the SPIDER
    # time domain, which would rightly call the column time_fs.
    w._spec_base_pv = "L3-SBW4-SPEC:Spectrum"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._x_data = X
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)
    w._chk_autofit.setChecked(False)
    return w


# ── the bar ───────────────────────────────────────────────────────────────────
def test_shot_bar_browsing(w):
    print("\ntest_shot_bar_browsing")
    # Region 1: 5 shots today, one every 10 min. Region 2: 4 shots the NEXT day,
    # deliberately created second so "sorted by time" cannot just be "list order".
    t1 = [T0 + i * 600 * 10 ** 9 for i in range(5)]
    t2 = [T0 + DAY + i * 600 * 10 ** 9 for i in range(4)]
    w._regions = [_region(2, 4, 812.0, 2, "#C62828", t2),
                  _region(1, 5, 806.0, 1, "#1565C0", t1)]
    w._region_seq = 2
    _load_time_axis(w, t1, t2)
    w._rebuild_regions_ui()

    # isHidden(), not isVisible(): the widget is never shown in this harness, so
    # isVisible() is False for every child and would pass by accident.
    _set_show(w, "Mean")
    w._redraw_spectra()
    _ok(w._shot_bar_box.isHidden(), "the bar is hidden while showing an average")

    _set_show(w, "Every spectrum")
    w._redraw_spectra()
    _ok(not w._shot_bar_box.isHidden(), "the bar appears in Every spectrum")
    items = w._single_items_cache
    _ok(len(items) == 9, "every shot of both spectra is listed", f"{len(items)}")
    _ok(len(w._single_ts_arr) == 9, "the bar can reach every one of them",
        f"{len(w._single_ts_arr)}")
    _ok([it["ts"] for it in items] == sorted(t1 + t2),
        "listed by the time they were measured, across both spectra and both days")
    _ok(w._sl_shot.isEnabled() and w._btn_single_next.isEnabled(),
        "the bar and the ◀ ▶ buttons are live")

    # position 0 = the earliest shot = region 1 row 0
    w._go_to_shot(0)
    QApplication.processEvents()
    halo, line, tag = w._single_hl_list()
    r, k, it = w._single_current()
    _ok(r["id"] == 1 and k == 0, "position 1 is the earliest shot", f"id={r['id']} row={k}")
    _ok(line.get_visible() and halo.get_visible(), "the bold curve is on")
    xp, yp = w._single_curve(r, k)
    _ok(np.allclose(line.get_ydata(), yp),
        "the bold curve carries that shot's own values")
    _ok(line.get_color() == "#000000", "in black, a colour no bundle has",
        line.get_color())
    _ok(tag.get_bbox_patch().get_edgecolor()[:3] ==
        (0x15 / 255, 0x65 / 255, 0xC0 / 255),
        "and the tag's border carries its spectrum's colour")
    _ok(sp_t._fmt_date(t1[0]) in w._lbl_single.text()
        and "1 of 9" in w._lbl_single.text(),
        "the caption names it", w._lbl_single.text())
    _ok(w._single_when(it) in tag.get_text(), "so does the tag in the graph",
        tag.get_text().strip())

    # the last position must be the newest shot — the next day's last one
    w._go_to_shot(8)
    QApplication.processEvents()
    r, k, _it = w._single_current()
    _ok(r["id"] == 2 and k == 3, "the last position is the newest shot",
        f"id={r['id']} row={k}")
    _ok(tag.get_bbox_patch().get_edgecolor()[:3] ==
        (0xC6 / 255, 0x28 / 255, 0x28 / 255),
        "and the tag border followed it to the other spectrum")
    dates = w._lbl_single.text()
    _ok(sp_t._fmt_date(t2[-1]) in dates and sp_t._fmt_date(t2[-1]) != sp_t._fmt_date(t1[0]),
        "the date follows the shot, not the loaded day", dates)

    # ◀ steps exactly one
    w._step_single(-1)
    QApplication.processEvents()
    _ok(w._single_pos == 7, "◀ steps one shot", str(w._single_pos))
    w._step_single(+5)
    QApplication.processEvents()
    _ok(w._single_pos == 8, "▶ stops at the end", str(w._single_pos))

    w._fig_bot.savefig(os.path.join(OUT, "single_browser_highlight.png"),
                       dpi=110, bbox_inches="tight")

    # a redraw must not move the user
    w._go_to_shot(3)
    QApplication.processEvents()
    keep = w._single_current()[0]["id"], w._single_current()[1]
    w._cmb_norm.setCurrentIndex(1)          # Normalize → Peak, forces a full redraw
    QApplication.processEvents()
    w._redraw_spectra()
    _ok((w._single_current()[0]["id"], w._single_current()[1]) == keep,
        "a redraw leaves you on the same shot", f"{keep}")
    w._cmb_norm.setCurrentIndex(0)
    w._redraw_spectra()

    # hiding the OTHER spectrum renumbers the list but must not move you
    w._go_to_shot(6)               # a region-2 shot
    QApplication.processEvents()
    keep = w._single_current()[0]["id"], w._single_current()[1]
    w._regions[1]["visible"] = False       # hide region 1
    w._redraw_spectra()
    _ok(len(w._single_items_cache) == 4, "a hidden spectrum leaves the list",
        str(len(w._single_items_cache)))
    _ok((w._single_current()[0]["id"], w._single_current()[1]) == keep,
        "and you are still on the same shot", f"{keep}")
    w._regions[1]["visible"] = True
    w._redraw_spectra()

    # switching the highlight off, and leaving the mode.
    # Re-read the artists: every redraw clears the axes and builds new ones, so a
    # reference taken earlier is a detached artist whose flags never change.
    w._chk_single_hl.setChecked(False)
    QApplication.processEvents()
    _ok(not w._single_hl_list()[1].get_visible(),
        "unticking the box hides the bold curve")
    w._chk_single_hl.setChecked(True)
    QApplication.processEvents()
    _ok(w._single_hl_list()[1].get_visible(), "and ticking it brings it back")

    _set_show(w, "Mean")
    w._redraw_spectra()
    _ok(not w._shot_bar_box.isVisible(), "the bar goes away again with Mean")
    _ok(not w._single_hl_list()[1].get_visible(),
        "and no bold curve is left on the averaged graph")

    # the highlight must never be counted as one of the plotted curves
    real = [l for l in w._ax_bot.get_lines()
            if len(l.get_xdata()) and not str(l.get_label()).startswith("_")]
    _ok(len(real) == 2, "Mean still draws exactly one curve per spectrum",
        str(len(real)))


# ── the export ────────────────────────────────────────────────────────────────
def test_export_is_complete(w):
    print("\ntest_export_is_complete")
    cap = 20
    old_cap, sp_t.MAX_SINGLE_LINES = sp_t.MAX_SINGLE_LINES, cap
    try:
        n = 53
        times = [T0 + i * 5 * 10 ** 9 for i in range(n)]
        w._regions = [_region(1, n, 806.0, 7, "#1565C0", times)]
        w._rebuild_regions_ui()
        _set_show(w, "Every spectrum")
        w._redraw_spectra()

        drawn = len(w._single_items_cache)
        _ok(drawn == cap, f"the graph thins to {cap} curves", str(drawn))

        p = os.path.join(OUT, "single_browser_export.csv")
        w._export_csv(p, [(0, w._regions[0])], [])
        rows = list(csv.reader(open(p, encoding="utf-8-sig"), delimiter=";"))
        hdr = next(r for r in rows if r and r[0] in ("wavelength_nm", "sample_number"))
        body = rows[rows.index(hdr) + 1:]
        _ok(len(hdr) - 1 == n, f"the CSV holds all {n} shots, not {cap}",
            str(len(hdr) - 1))
        _ok(hdr[0] == "wavelength_nm", "the X column is the wavelength axis", hdr[0])
        _ok(len(body) == NX, "one row per wavelength point", str(len(body)))
        _ok(all(len(r) == len(hdr) for r in body) and
            all(v != "" for r in body for v in r),
            "and not one empty cell in the table")
        det = next(r for r in rows if r and r[0] == "Spectrum 1")
        _ok(f"every spectrum ({n})" in det[5] and f"graph drew {cap}" in det[5],
            "the details block says the file is fuller than the picture", det[5])

        # the numbers in the file must be the shots themselves
        # atol 1e-6: the file is written with six decimals on purpose
        col = [float(r[1]) for r in body]
        _ok(np.allclose(col, w._regions[0]["stack"][0], atol=1e-6),
            "the first column is that shot's raw intensity, point for point")
    finally:
        sp_t.MAX_SINGLE_LINES = old_cap


def test_export_says_samples(w):
    print("\ntest_export_says_samples")
    times = [T0 + i * 5 * 10 ** 9 for i in range(6)]
    r = _region(1, 6, 806.0, 11, "#1565C0", times)
    r["x"] = None                      # the axis never resolved
    w._regions = [r]
    w._rebuild_regions_ui()
    _set_show(w, "Every spectrum")
    w._sb_x_min.setValue(0)
    w._sb_x_max.setValue(NX)
    w._redraw_spectra()

    _ok(w._x_is_samples(), "the widget knows it is plotting sample numbers")
    _ok("Sample number" in w._ax_bot.get_xlabel(),
        "the axis title says so instead of claiming nm", w._ax_bot.get_xlabel())

    p = os.path.join(OUT, "single_browser_samples.csv")
    w._export_csv(p, [(0, w._regions[0])], [])
    rows = list(csv.reader(open(p, encoding="utf-8-sig"), delimiter=";"))
    hdr = next(r for r in rows if r and r[0] in ("wavelength_nm", "sample_number"))
    _ok(hdr[0] == "sample_number", "so does the CSV's first column", hdr[0])
    cols = next(r for r in rows if r and r[0] == "Spectrum")
    _ok("Peak sample" in cols, "and the λ headers turn into sample headers",
        ", ".join(cols[-6:]))
    _ok(any(r and "SAMPLE NUMBERS" in r[0] for r in rows),
        "with a note at the top of the file")
    body = rows[rows.index(hdr) + 1:]
    _ok([float(r[0]) for r in body[:3]] == [0.0, 1.0, 2.0],
        "the X column really counts samples", str([r[0] for r in body[:3]]))
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)


def test_export_regrids_a_short_region(w):
    print("\ntest_export_regrids_a_short_region")
    # Region 1 on the full grid, region 2 half as long on its own axis: the file
    # has one X column, so the short one must be resampled onto it.
    short_nx = NX // 2
    xs = np.linspace(780.0, 840.0, short_nx)
    t1 = [T0 + i * 5 * 10 ** 9 for i in range(4)]
    t2 = [T0 + 3600 * 10 ** 9 + i * 5 * 10 ** 9 for i in range(3)]
    w._regions = [_region(1, 4, 806.0, 21, "#1565C0", t1),
                  _region(2, 3, 812.0, 22, "#C62828", t2, x=xs, nx=short_nx)]
    w._rebuild_regions_ui()
    _set_show(w, "Every spectrum")
    w._redraw_spectra()

    p = os.path.join(OUT, "single_browser_regrid.csv")
    w._export_csv(p, [(0, w._regions[0]), (1, w._regions[1])], [])
    rows = list(csv.reader(open(p, encoding="utf-8-sig"), delimiter=";"))
    hdr = next(r for r in rows if r and r[0] == "wavelength_nm")
    body = rows[rows.index(hdr) + 1:]
    _ok(len(hdr) - 1 == 7, "all 4+3 shots are columns", str(len(hdr) - 1))
    _ok(len(body) == NX, "on the long region's grid", str(len(body)))
    _ok(all(v != "" for r in body for v in r),
        "the short region is resampled, not cut off at half height")
    # spot-check the interpolation against numpy on the last short column
    want = np.interp(X, xs, w._regions[1]["stack"][2])
    got = np.array([float(r[-1]) for r in body])
    _ok(np.allclose(got, want, atol=1e-5), "and resampled onto the right points")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = _widget()
    test_shot_bar_browsing(w)
    test_export_is_complete(w)
    test_export_says_samples(w)
    test_export_regrids_a_short_region(w)
    w.deleteLater()
    print("\nOutput in", OUT)
    print("RESULT:", "FAILED" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
