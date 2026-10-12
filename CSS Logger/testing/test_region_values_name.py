"""Spectra — a spectrum is named after what was searched for, and X axis settings.

  1. The name is the mean of every TICKED Search-by PV inside the spectrum's
     stretch, written with the PV's own Label: "SBW4 energy [J] 20.1 · GDD
     (Order 2) 24780". Unticked PVs stay out of it.
  2. A PV with no sample inside the stretch holds its last earlier value — a
     quiet set point is never "no data".
  3. Renaming a Label renames the spectrum; unticking everything gives back
     "Spectrum N"; a stretch the loaded data does not cover keeps its name.
  4. Two stretches at the same values get a number on the later one.
  5. The name is printed inside the shaded block on the search graph.
  6. A one-number PV (L3-SBDP-SPIDER:FWHM_FL) is not read as a one-point
     spectrum, and the X axis settings dialog still returns the same settings.

No network, nothing shown on screen:

    python testing/test_region_values_name.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="spectra_names_test_")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

import sp_t                                          # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


SEC = 10 ** 9
T0 = int(1_759_240_800 * SEC)        # a fixed wall-clock start
WIN = (T0, T0 + 3600 * SEC)

E_CH, G_CH, A_CH = "TEST:ENERGY", "TEST:GDD", "TEST:ALPHA"


def _series():
    # Energy: 13 J for the first 20 min, 20.1 J after. GDD: written once, at
    # the very start (a set point). Alpha: noise, but unticked.
    e = [(T0 + k * SEC, 13.0 if k < 1200 else 20.1) for k in range(0, 3600, 2)]
    g = [(T0, 24780.4)]
    a = [(T0 + k * SEC, 0.5) for k in range(0, 3600, 10)]
    return [
        {"label": "SBW4 energy [J]", "channel": E_CH, "data": e, "color": "#000",
         "error": None, "last_before": None},
        {"label": "GDD (Order 2)", "channel": G_CH, "data": g, "color": "#000",
         "error": None, "last_before": None},
        {"label": "Alpha energy [J]", "channel": A_CH, "data": a, "color": "#000",
         "error": None, "last_before": None},
    ]


def _widget():
    w = sp_t.SpectraWidget()
    w._search_pvs = [("SBW4 energy [J]", E_CH), ("GDD (Order 2)", G_CH),
                     ("Alpha energy [J]", A_CH)]
    w._pv_hidden = {A_CH}
    w._windows = [WIN]
    w._tmap = sp_t._TimeMap([WIN])
    w._energy_data = _series()
    return w


def test_names(w):
    print("names")
    check("number format: 20.1 / 24780 / 0.5",
          (sp_t._fmt_name_value(20.1), sp_t._fmt_name_value(24780.4),
           sp_t._fmt_name_value(0.5)) == ("20.1", "24780", "0.5"))

    w._draw_energy()
    r1 = w._add_region(T0 + 1500 * SEC, T0 + 1800 * SEC)     # 20.1 J inside
    r2 = w._add_region(T0 + 100 * SEC, T0 + 400 * SEC)       # 13 J inside
    name = w._region_label(0)
    check("ticked PVs, own labels, means", name ==
          "SBW4 energy [J] 20.1 · GDD (Order 2) 24780", repr(name))
    check("the unticked PV stays out", "Alpha" not in name, repr(name))
    check("the set point written before the stretch is held forward",
          "GDD (Order 2) 24780" in w._region_label(1), repr(w._region_label(1)))
    check("second stretch at 13 J", w._region_label(1).startswith("SBW4 energy [J] 13 "),
          repr(w._region_label(1)))

    # Same values again → a number on the later one.
    w._add_region(T0 + 2000 * SEC, T0 + 2300 * SEC)
    check("a repeated name gets a number", w._region_label(2).endswith("(2)"),
          repr(w._region_label(2)))

    # Label edit → rename.
    w._search_pvs[0] = ("SBW4 [J]", E_CH)
    w._energy_data[0]["label"] = "SBW4 [J]"
    w._draw_energy(reset_view=False)
    check("a renamed Label renames the spectrum",
          w._region_label(0).startswith("SBW4 [J] 20.1"), repr(w._region_label(0)))

    # The name is on the search graph, horizontal (a long one broken into lines).
    def words(s):
        return " ".join(s.replace(" · ", " ").split())
    arts = [t for a in [w._ax_top] + w._top_extra_axes for t in a.texts
            if t.get_gid() == "region_name"]
    texts = [words(t.get_text()) for t in arts]
    check("the name is printed inside its block", words(w._region_label(0)) in texts,
          repr(texts))
    check("one name per spectrum, no leftovers", len(arts) == len(w._regions),
          f"{len(arts)} texts")
    check("every name is horizontal", all(t.get_rotation() == 0 for t in arts))

    # A stretch not covered by the loaded data keeps its name.
    w._tmap = sp_t._TimeMap([(T0 + 7200 * SEC, T0 + 9000 * SEC)])
    w._refresh_region_names()
    check("a stretch outside the loaded data keeps its name",
          w._region_label(0).startswith("SBW4 [J] 20.1"), repr(w._region_label(0)))
    w._tmap = sp_t._TimeMap([WIN])

    # Nothing ticked → back to Spectrum N.
    w._pv_hidden = {E_CH, G_CH, A_CH}
    w._refresh_region_names()
    check("nothing ticked gives Spectrum N", w._region_label(0) == "Spectrum 1",
          repr(w._region_label(0)))
    check("legend text follows", r1.get("name", "") == "" and r2.get("name", "") == "")


def test_close_means():
    print("close means get more digits")
    w = _widget()
    e = [(T0 + k * SEC, 26.72 if k < 1800 else 26.68) for k in range(0, 3600, 2)]
    w._energy_data[0]["data"] = e
    w._pv_hidden = {G_CH, A_CH}
    w._draw_energy()
    w._add_region(T0 + 100 * SEC, T0 + 400 * SEC)
    w._add_region(T0 + 2000 * SEC, T0 + 2300 * SEC)
    a, b = w._region_label(0), w._region_label(1)
    check("26.72 and 26.68, no (2)",
          (a, b) == ("SBW4 energy [J] 26.72", "SBW4 energy [J] 26.68"), repr((a, b)))
    w._add_region(T0 + 500 * SEC, T0 + 800 * SEC)
    check("equal means still get a number",
          w._region_label(2) == "SBW4 energy [J] 26.72 (2)", repr(w._region_label(2)))
    check("a lone value keeps three digits",
          sp_t._fmt_name_value(26.72) == "26.7")


def test_expanded_row_is_no_window():
    print("an expanded row never becomes its own window")
    w = _widget()
    w._draw_energy()
    w._add_region(T0 + 100 * SEC, T0 + 400 * SEC)
    w._regions[0]["expanded"] = True
    seen = []
    orig = sp_t.SpectraWidget._build_region_details

    def spy(self, r):
        d = orig(self, r)
        orig_vis = d.setVisible

        def vis(on, d=d, orig_vis=orig_vis):
            seen.append(on and d.parent() is None)
            orig_vis(on)
        d.setVisible = vis
        return d
    sp_t.SpectraWidget._build_region_details = spy
    try:
        w._rebuild_regions_ui()
    finally:
        sp_t.SpectraWidget._build_region_details = orig
    check("details shown only once inside the row", seen and not any(seen), repr(seen))


def test_one_number_pv():
    print("one number per shot")
    samples = [{"time": T0 + k * SEC, "value": [28.0 + k * 0.01]} for k in range(60)]
    orig = sp_t._cpva_fetch_chunked
    sp_t._cpva_fetch_chunked = lambda ch, a, b: samples
    try:
        out = sp_t._fetch_waveforms("L3-SBDP-SPIDER:FWHM_FL", T0, T0 + 60 * SEC)
    finally:
        sp_t._cpva_fetch_chunked = orig
    check("one-number samples are not spectra", out == [], f"{len(out)} kept")
    check("and that is remembered for the message",
          "L3-SBDP-SPIDER:FWHM_FL" in sp_t._one_number_hits)
    check("the message names the channel",
          "FWHM_FL holds one number per shot" in
          sp_t._not_a_spectrum_msg("L3-SBDP-SPIDER:FWHM_FL"))


def test_x_axis_dialog(w):
    print("X axis settings dialog")
    sp_t._cpva_channel_cache[:] = ["L3-SBDP-SPIDER:SpecDomain_Int_X", "OTHER:X"]
    d = sp_t.XAxisSourceDialog("L3-SBDP-SPIDER:FWHM_FL", w,
                               current={"mode": "linear",
                                        "source_pv": "L3-SBDP-SPIDER:SpecDomain_Int_X",
                                        "scale": 2.0, "offset": 3.0}, unit="nm")
    check("title", d.windowTitle() == "X axis settings")
    check("opens on the setting in force", d._rb_pv.isChecked() and d._chk_linear.isChecked())
    check("the source PV is picked in the list",
          [i.text() for i in d._lst.selectedItems()] == ["L3-SBDP-SPIDER:SpecDomain_Int_X"])
    check("the PV list belongs to the PV option",
          not d._box_pv.isHidden() and d._box_csv.isHidden())
    d._on_ok()
    check("linear result unchanged in shape", d.result_cfg() == {
        "mode": "linear", "source_pv": "L3-SBDP-SPIDER:SpecDomain_Int_X",
        "scale": 2.0, "offset": 3.0}, repr(d.result_cfg()))

    d = sp_t.XAxisSourceDialog("X:Y", w, current={"mode": "pv", "source_pv": "OTHER:X"})
    d._on_ok()
    check("pv result", d.result_cfg() == {"mode": "pv", "source_pv": "OTHER:X"},
          repr(d.result_cfg()))
    d._rb_index.setChecked(True)
    check("index hides the PV list", d._box_pv.isHidden())
    d._on_ok()
    check("index result", d.result_cfg() == {"mode": "index"})

    d = sp_t.XAxisSourceDialog("A:B", w, current={"mode": "native"}, native_x="A:B_X")
    check("own X offered and chosen", not d._rb_native.isHidden() and d._rb_native.isChecked())
    d._on_ok()
    check("native result", d.result_cfg() == {"mode": "native"})


def test_spectra_group(w):
    print("Spectra group")
    check("the list lives in the Spectra group",
          w._g_spectra.body.isAncestorOf(w._regions_w))
    check("an Analyze under the list",
          w._g_spectra.body.isAncestorOf(w._btn_analyze2))
    w._update_action_buttons()
    check("both Analyze buttons agree",
          w._btn_analyze.isEnabled() == w._btn_analyze2.isEnabled())


def main():
    app = QApplication.instance() or QApplication([])  # noqa: F841
    w = _widget()
    test_names(w)
    test_close_means()
    test_expanded_row_is_no_window()
    test_one_number_pv()
    test_x_axis_dialog(w)
    test_spectra_group(w)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
