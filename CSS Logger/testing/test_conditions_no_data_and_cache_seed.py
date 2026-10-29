"""Fixes of 12.10.2026 (the user's list):

  * INFO's "Custom PV …: not computed" box names only the custom PVs ticked
    Show in the PV table; the Log still gets every one.
  * A condition on a channel with NO sample inside the window is skipped —
    at the end of a load as well as in the pictures during it. The value held
    from before the window used to make it active at the end only, so a month
    of 2025 (PTM1/PCM4/PAP1 silent) showed data while loading and nothing
    when done.
  * A thinned point passes a condition when its min..max range reaches it.
  * "Keep multiples of" stops the zoom detail only when it acts on the window.
  * A cached day carries the sample before it, so a load starting on it needs
    no look-back to the archiver.

Headless, no network, no share:

    python testing/test_conditions_no_data_and_cache_seed.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["CPVA_CACHE_DIR"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                                  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox    # noqa: E402

import cpva_cache                                # noqa: E402
import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
NS = 1_000_000_000
PV_A = "L3-TEST-A:Energy"
PV_C = "L3-TEST-COND:Energy"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _widget():
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    return css.CSSLoggerWidget()


def test_custom_pv_box_only_for_watched(w):
    print("── custom PV box ──")
    w._custom_pvs = [{"name": "Ratio X", "expr": "A/B",
                      "bindings": {"A": "L3-ELSEWHERE:One", "B": "L3-ELSEWHERE:Two"}}]
    w._pv_order = [PV_A]
    w._pv_settings["Ratio X"] = {"show": False}
    w._cpv_plan()
    w._emit_custom_pv_diag()
    check("not ticked: the INFO box stays empty", w._lbl_cpv_info.text() == "",
          repr(w._lbl_cpv_info.text()))
    check("…but the Log still hears of it",
          any("Ratio X" in m for m in w._cpv_diag), str(w._cpv_diag))
    w._pv_settings["Ratio X"] = {"show": True}
    w._cpv_plan()
    w._emit_custom_pv_diag()
    check("ticked: the INFO box names it", "Ratio X" in w._lbl_cpv_info.text(),
          repr(w._lbl_cpv_info.text()))
    w._custom_pvs = []
    w._cpv_plan()
    w._emit_custom_pv_diag()


def test_condition_without_window_data_is_skipped(w):
    print("── condition channel silent in the window ──")
    t0 = 1_790_000_000 * NS
    w._custom_pvs = []
    w._samples_by_pv = {PV_A: [(t0 + i * NS, 50.0 + i, "J") for i in range(10)],
                        PV_C: []}
    w._minmax_by_pv = {}
    # Held from long before the window, and failing the condition.
    w._pre_window_vals = {PV_C: (t0 - 90 * 86400 * NS, -1.7, "J")}
    w._conditions = [{"pv": PV_C, "min": 5.0, "max": 150.0}]
    final_rows = w._build_table_rows(w._samples_by_pv, [PV_A, PV_C],
                                     w._pre_window_vals)
    kept = w._apply_conditions_to_rows(final_rows)
    check("final load: the rows stay", len(kept) == len(final_rows),
          f"{len(kept)} of {len(final_rows)}")
    check("…and the condition is reported skipped", w._cond_skipped == [PV_C],
          str(w._cond_skipped))
    check("no look-back is started for it", not w._seed_rejected())
    partial_rows = w._build_table_rows(w._samples_by_pv, [PV_A, PV_C], {})
    kept_partial = w._apply_conditions_to_rows(partial_rows)
    check("a picture during loading keeps the same rows",
          len(kept_partial) == len(kept), f"{len(kept_partial)} vs {len(kept)}")
    # With data in the window the condition is active again.
    w._samples_by_pv[PV_C] = [(t0 + i * NS, -1.7, "J") for i in range(10)]
    rows = w._build_table_rows(w._samples_by_pv, [PV_A, PV_C], w._pre_window_vals)
    check("a channel with samples in the window still judges",
          not w._apply_conditions_to_rows(rows))
    w._conditions = []


def test_range_rule():
    print("── thinned points judged by their range ──")
    lo = np.array([0.0, 0.0, 6.0, np.nan])
    hi = np.array([4.0, 9.0, 7.0, 1.0])
    check("between: a range reaching it passes",
          list(css._cond_range_passes(lo, hi, 5.0, 150.0)) == [False, True, True, False])
    check("outside: a range leaving it passes",
          list(css._cond_range_passes(lo, hi, 5.0, 150.0, True)) == [True, True, False, False])
    v = np.array([3.0, 6.0])
    check("a raw point (lo == hi) is judged as before",
          list(css._cond_range_passes(v, v, 5.0, None))
          == list(css._cond_mask_passes(v, 5.0, None)))


def test_step_filter_gate(w):
    print("── zoom detail and Keep multiples of ──")
    w._master_multiple_edit.setText(css.MASTER_STEP_DEFAULT)
    w._samples_by_pv = {PV_A: [(1, 1.0, "")]}
    w._pre_window_vals = {}
    check("default box, master PV not loaded: the filter does not act",
          not w._step_filter_applies())
    master = w._get_master_pv()
    w._samples_by_pv[master] = [(1, 1000.0, "")]
    check("master PV loaded: it acts", w._step_filter_applies())
    w._master_multiple_edit.setText("")
    check("empty box: it does not", not w._step_filter_applies())


def test_cached_day_carries_the_sample_before():
    print("── day cache: the value before the day ──")
    tz = cpva_core.TZ_PRAGUE
    day_lo = cpva_core.dt_to_ns(css.datetime(2026, 9, 1, tzinfo=tz))
    day_hi = day_lo + 86400 * NS
    samples = ([{"time": day_lo - 3600 * NS, "value": 42.0}]
               + [{"time": day_lo + i * 3600 * NS, "value": float(i)} for i in range(24)])
    with tempfile.TemporaryDirectory() as tmp:
        c = cpva_cache.DayCache(tmp)
        now = day_hi + 5 * 86400 * NS
        check("a closed day is stored", c.put(PV_A, day_lo, day_hi, samples, None,
                                              PV_A, tz, now))
        c.flush()
        c2 = cpva_cache.DayCache(tmp)
        c2.prefetch([PV_A], [(day_lo, day_hi)], tz)
        got = c2.get(PV_A, day_lo, day_hi, None, tz) or []
    check("it comes back with the sample before the day first",
          bool(got) and got[0]["time"] == day_lo - 3600 * NS and got[0]["value"] == 42.0,
          str(got[:1]))
    check("…and the day itself whole", len(got) == 25, f"{len(got)} samples")


def main() -> int:
    print("test_conditions_no_data_and_cache_seed")
    test_range_rule()
    test_cached_day_carries_the_sample_before()
    w = _widget()
    test_custom_pv_box_only_for_watched(w)
    test_condition_without_window_data_is_skipped(w)
    test_step_filter_gate(w)
    w.close()
    w.deleteLater()
    _app.processEvents()
    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
