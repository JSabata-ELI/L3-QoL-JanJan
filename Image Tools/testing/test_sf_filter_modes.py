"""Shot Finder PV filter modes: ± value, ± %, from – to, above, below.

Checks the band each mode gives, that a state file from before the modes still
reads as target ± value, that the panel's boxes reach the search criteria, that
switching modes keeps the same band, and that a different mode is a different
run (Load images must not reuse numbers judged against another band).

Run:  python testing/test_sf_filter_modes.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

FAILED: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(f"{name} {detail}")


def close(a, b):
    return a is not None and b is not None and abs(a - b) < 1e-9


def test_bands():
    print("\nThe band of each mode")
    lo, hi, ref = sf_t._band_of({"target": 20, "tol": 2})
    check("± value", (lo, hi, ref) == (18, 22, 20), (lo, hi, ref))
    lo, hi, ref = sf_t._band_of({"mode": "pct", "target": 20, "pct": 5})
    check("± % of the target", close(lo, 19) and close(hi, 21), (lo, hi))
    lo, hi, ref = sf_t._band_of({"mode": "pct", "target": -20, "pct": 10})
    check("± % of a negative target", close(lo, -22) and close(hi, -18), (lo, hi))
    lo, hi, ref = sf_t._band_of({"mode": "range", "lo": 22, "hi": 18})
    check("from – to, typed the wrong way round", (lo, hi, ref) == (18, 22, 20),
          (lo, hi, ref))
    lo, hi, ref = sf_t._band_of({"mode": "above", "lo": 19.5})
    check("above: open top", lo == 19.5 and hi is None and ref == 19.5)
    lo, hi, ref = sf_t._band_of({"mode": "below", "hi": 21})
    check("below: open bottom", lo is None and hi == 21 and ref == 21)
    check("an unknown mode is ± value",
          sf_t._band_of({"mode": "xyz", "target": 5, "tol": 1}) == (4, 6, 5))

    print("\nIn or out")
    check("edges are inside", sf_t._in_band(18, 18, 22) and sf_t._in_band(22, 18, 22))
    check("outside is outside", not sf_t._in_band(22.01, 18, 22))
    check("above has no ceiling", sf_t._in_band(1e9, 19.5, None))
    check("below has no floor", sf_t._in_band(-1e9, None, 21))

    print("\nHow a band is written")
    for cfg, want in (({"target": 20, "tol": 2}, "20 ± 2 J"),
                      ({"mode": "pct", "target": 20, "pct": 5}, "20 J ± 5 %"),
                      ({"mode": "range", "lo": 18, "hi": 22}, "18 … 22 J"),
                      ({"mode": "above", "lo": 19.5}, "≥ 19.5 J"),
                      ({"mode": "below", "hi": 21}, "≤ 21 J")):
        got = sf_t._band_text(cfg, "J")
        check(f"{cfg.get('mode', 'tol')}", got == want, got)


def test_panel(app):
    print("\nThe panel's boxes reach the search")
    w = sf_t.ShotFinderWidget()
    w._pv_cfg = [{"col": "sbw4", "target": 20.0, "tol": 2.0, "filter": True},
                 {"col": "ptm1", "target": 10.0, "tol": 1.0, "filter": True}]
    w._rebuild_pv_rows()
    r0, r1 = w._pv_rows
    check("an old cfg comes up as ± value", r0["mode_cb"].currentData() == "tol")

    # ± value → from – to keeps 18 … 22
    r0["mode_cb"].setCurrentIndex(sf_t.PV_FILTER_MODES.index("range"))
    app.processEvents()
    check("switching to from – to starts at the same band",
          r0["lo_sb"].value() == 18 and r0["hi_sb"].value() == 22,
          (r0["lo_sb"].value(), r0["hi_sb"].value()))
    check("only the from/to boxes are shown",
          r0["lo_sb"].isVisibleTo(w) is not None
          and not r0["target_sb"].isVisibleTo(r0["chk"].parentWidget())
          and not r0["tol_sb"].isVisibleTo(r0["chk"].parentWidget()))
    r0["hi_sb"].setValue(25.0)

    r1["mode_cb"].setCurrentIndex(sf_t.PV_FILTER_MODES.index("pct"))
    r1["pct_sb"].setValue(10.0)
    crit = w._get_criteria()
    check("criteria carry the mode", [c["mode"] for c in crit] == ["range", "pct"],
          [c["mode"] for c in crit])
    check("range criteria", sf_t._band_of(crit[0])[:2] == (18, 25),
          sf_t._band_of(crit[0]))
    check("pct criteria", close(sf_t._band_of(crit[1])[0], 9.0)
          and close(sf_t._band_of(crit[1])[1], 11.0), sf_t._band_of(crit[1]))

    # from – to → ± value: middle and half-width
    r0["mode_cb"].setCurrentIndex(sf_t.PV_FILTER_MODES.index("tol"))
    check("back to ± value keeps the band",
          r0["target_sb"].value() == 21.5 and r0["tol_sb"].value() == 3.5,
          (r0["target_sb"].value(), r0["tol_sb"].value()))

    print("\nA swapped range is marked")
    r0["mode_cb"].setCurrentIndex(sf_t.PV_FILTER_MODES.index("range"))
    r0["lo_sb"].setValue(30.0)
    check("both boxes amber", bool(r0["lo_sb"].styleSheet())
          and bool(r0["hi_sb"].styleSheet()))
    r0["lo_sb"].setValue(10.0)
    check("and clear again when fixed", not r0["lo_sb"].styleSheet()
          and not r0["hi_sb"].styleSheet())
    return w


def test_state(w):
    print("\nSaved and read back")
    old_path = sf_t.ShotFinderWidget._UI_STATE_PATH
    tmp = Path(tempfile.mkdtemp()) / "shotfinder_ui_state.json"
    try:
        sf_t.ShotFinderWidget._UI_STATE_PATH = tmp
        tmp.write_text(json.dumps({"pv_cfg": [
            {"col": "sbw4", "target": 20.0, "tol": 2.0, "filter": True}]}),
            encoding="utf-8")
        w._pv_cfg = []
        w._load_pv_state()
        c = w._pv_cfg[0]
        check("a file from before the modes reads as ± value",
              c["mode"] == "tol" and c["target"] == 20 and c["tol"] == 2, c)
        check("and its from/to start at the same band", (c["lo"], c["hi"]) == (18, 22))

        w._pv_cfg = [{"col": "sbw4", "mode": "below", "target": 20.0, "tol": 2.0,
                      "pct": 5.0, "lo": 1.0, "hi": 21.0, "filter": True}]
        w._rebuild_pv_rows()
        w._save_pv_state()
        w._pv_cfg = []
        w._load_pv_state()
        c = w._pv_cfg[0]
        check("a mode survives a restart", c["mode"] == "below" and c["hi"] == 21.0, c)
    finally:
        sf_t.ShotFinderWidget._UI_STATE_PATH = old_path


def test_run_key():
    print("\nA different band is a different run")
    base = {"col": "sbw4", "target_csv": 20.0, "lo_csv": 18.0, "hi_csv": 22.0}
    k1 = sf_t.ShotFinderWidget._run_key({}, ["sbw4"], [], [dict(base, mode="tol")])
    k2 = sf_t.ShotFinderWidget._run_key({}, ["sbw4"], [], [dict(base, mode="range")])
    k3 = sf_t.ShotFinderWidget._run_key({}, ["sbw4"], [], [dict(base, mode="tol",
                                                                 hi_csv=23.0)])
    check("same band, other mode", k1 != k2)
    check("other upper limit", k1 != k3)


def main():
    app = QApplication([])
    test_bands()
    w = test_panel(app)
    test_state(w)
    test_run_key()
    if FAILED:
        print(f"\n{len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
