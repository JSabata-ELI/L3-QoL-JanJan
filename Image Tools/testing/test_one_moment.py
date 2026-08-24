"""test_one_moment.py — the One Moment tab, offscreen, with no network and no share.

Two halves, both offline:

  PART 1 — the graph and the numbers.  PV series are injected by hand, so what is
  under test is the logic: ONE graph with an axis per unit (and the stacked mode as the
  alternative), snapping a click onto a REAL sample (a moment between two samples has
  no shot behind it), stepping shot by shot and stopping at the ends of the window, the
  marked-range statistics in the left panel, telling a click apart from a drag, the eye
  taking a PV off the graph without unpicking it, and a picked formula being listed
  rather than silently dropped.

  PART 2 — the frames.  A real …/cpva-image-YYYY/Y/M/D/H/CAM/<unix_ns>.png tree is
  built on local disk with unpadded folder names, UTC hours and 16-bit frames carrying
  a MaxValue tEXt chunk exactly as the archiver writes them, and the tab's share root
  is pointed at it. That covers what cannot be checked by reading the code: that the
  NEAREST frame is the one picked (not the first one found), that a camera with nothing
  near the moment is reported instead of dropped, that the absolute scale comes from
  each frame's own MaxValue (a frame peaking at 255 counts must stay dark next to one
  peaking at 4000), that a display control re-renders WITHOUT walking the share again,
  and that a second click REPLACES the tiles.

Why the fake tree is written to %TEMP% and not next to this file: the archive path is
long, and under a deep working directory the …/CAM/<name>_<19 digits>.png leaf goes past
Windows' 260-character limit — mkdir then fails with "cannot find the path specified",
which reads like a missing folder rather than a too-long name.

Not shipped (test_ prefix). Run:  python testing/test_one_moment.py
Exit code 1 on any failure.
"""
import importlib.util
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("TEMP", r"C:\Temp")) / "om_fake_archive"

# The tab remembers its settings in %APPDATA%\ELI_ImageTools — point that somewhere
# harmless BEFORE the widget is built, or running the tests would rewrite the
# operator's real window, camera and PV pick.
STATE_HOME = Path(os.environ.get("TEMP", r"C:\Temp")) / "om_fake_appdata"
shutil.rmtree(STATE_HOME, ignore_errors=True)
STATE_HOME.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(STATE_HOME)

_FAILS: "list[str]" = []


def check(cond, what: str):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        _FAILS.append(what)


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, APP / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import numpy as np                                          # noqa: E402
from PIL import Image, PngImagePlugin                       # noqa: E402
from PySide6.QtGui import QImage                            # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel          # noqa: E402

app = QApplication.instance() or QApplication([])

load("image_slider", "is_t.py")
load("shot_finder", "sf_t.py")
om = load("one_moment", "om_t.py")
sl = sys.modules["image_slider"]

MODE_ONE, MODE_STACKED = 0, 1


def pump(w, ready, timeout=30.0):
    """Run the event loop until `ready()` or the timeout — the tab does its work in
    background tasks and reports through signals."""
    deadline = time.time() + timeout
    while time.time() < deadline and not ready():
        app.processEvents()
        time.sleep(0.02)


# ═══ PART 1 — graph, moment, statistics ══════════════════════════════════════
def part1():
    print("\n=== PART 1: graph and numbers ===")
    w = om.OneMomentWidget()
    check(True, "widget builds")

    day_start = w._windows[0][0]
    n = 900
    ts = np.linspace(day_start + 7 * 3600 * om.NS_PER_S,
                     day_start + 19 * 3600 * om.NS_PER_S, n).astype(np.int64)
    rng = np.random.default_rng(0)
    energy = 30.0 + 2.0 * np.sin(np.linspace(0, 12, n)) + rng.normal(0, .3, n)
    waveplate = np.round(np.linspace(0, 20000, n) / 1000.0) * 1000.0

    w._pv_selected = ["SBW4", "Waveplate"]
    w._series = {
        "SBW4":      {"channel": "FAKE:SBW4", "ts": ts, "val": energy, "status": "ok"},
        "Waveplate": {"channel": "FAKE:WP", "ts": ts, "val": waveplate, "status": "ok"},
    }
    # Both are preset PVs, so their units come from PV_UNITS: "J" and "" (a waveplate
    # position has none). Two units → two axes; the same unit → one.
    w._refresh_pick_labels()
    w._rebuild_graph()
    w._refresh_pv_table()

    print("\n[one graph, an axis per unit]")
    check(w._cmb_mode.currentIndex() == MODE_ONE, "one graph is the default")
    check(w._plot_order == ["SBW4", "Waveplate"], f"both PVs drawn: {w._plot_order}")
    check(len(w._fig.axes) == 2 and len(w._axes) == 2,
          f"two different units → two y axes in ONE plot ({len(w._fig.axes)})")
    check(len(w._axes_paint) == 1,
          "the cursor and the marked range are painted once, on the base axes")
    check(w._ax_x is w._axes[0] and w._ax_x.xaxis.get_visible(),
          "the time axis is the one whose ticks are actually painted")
    check(w._canvas is not None and w._toolbar is not None, "canvas and toolbar built")
    check(len(w._selectors) == 2, "a range selector on every axes")
    check(w._cmb_snap.count() == 2, "the snap list offers both PVs")
    check(w._pv_table.rowCount() == 2,
          f"a left-panel row per PV ({w._pv_table.rowCount()})")

    sl.PV_UNITS["Waveplate"] = "J"
    w._rebuild_graph()
    check(len(w._fig.axes) == 1, "one unit → a single y axis, still one plot")
    sl.PV_UNITS["Waveplate"] = ""

    print("\n[stacked, the alternative]")
    w._cmb_mode.setCurrentIndex(MODE_STACKED)
    check(len(w._fig.axes) == 2 and len(w._axes_paint) == 2,
          f"one plot per PV ({len(w._fig.axes)})")
    w._cmb_mode.setCurrentIndex(MODE_ONE)

    print("\n[moment]")
    x_click = 12 * 3600 + 137.0                 # 12:02:17, between two samples
    w._set_moment_from_x(x_click)
    check(w._moment_ns is not None, "a moment was set")
    check(int(w._moment_ns) in {int(t) for t in ts},
          "the moment is a real sample, not the raw click position")
    asked = int(day_start + x_click * om.NS_PER_S)
    off_s = abs(w._moment_ns - asked) / om.NS_PER_S
    check(off_s < 30, f"snapped to the nearest sample ({off_s:.1f} s away)")
    check(all(l.get_visible() for l in w._cursor_lines), "the cursor is drawn")
    check(om._fmt_moment(w._moment_ns) == w._lbl_moment.text(),
          f"the moment is written out: {w._lbl_moment.text()}")
    val = w._pv_table.item(0, 2).text()
    check(val not in ("", "—", "n/a"), f"value at the moment shown: {val}")
    check("from the moment picked" in w._pv_table.item(0, 2).toolTip(),
          "and how far its sample is from the moment")

    print("\n[stepping]")
    i0 = int(np.searchsorted(ts, w._moment_ns))
    w._step_moment(+1)
    check(int(np.searchsorted(ts, w._moment_ns)) == i0 + 1, "next shot moves one sample")
    w._step_moment(-1)
    check(int(np.searchsorted(ts, w._moment_ns)) == i0, "previous shot comes back")
    w._apply_moment(int(ts[-1]))
    w._step_moment(+1)
    check(int(w._moment_ns) == int(ts[-1]),
          "next shot at the end of the window stays put")
    w._apply_moment(int(ts[0]))
    w._step_moment(-1)
    check(int(w._moment_ns) == int(ts[0]), "previous shot at the start stays put")

    print("\n[marked range]")
    check(not w._btn_clear_range.isEnabled(), "nothing to clear before a drag")
    w._on_span(9 * 3600.0, 11 * 3600.0)
    check(w._span is not None, "a range was marked")
    mask = (ts >= w._span[0]) & (ts <= w._span[1])
    check(w._stat_table.rowCount() == 2,
          f"a statistics row per PV ({w._stat_table.rowCount()})")
    check(w._stat_table.item(0, 3).text() == str(int(mask.sum())),
          f"n = {w._stat_table.item(0, 3).text()} (expected {int(mask.sum())})")
    shown = float(w._stat_table.item(0, 1).text())
    check(abs(shown - energy[mask].mean()) < 0.01 * abs(energy[mask].mean()),
          f"mean = {shown:.4g} (expected {energy[mask].mean():.4g})")
    tip = w._stat_table.item(0, 1).toolTip()
    check("min = " in tip and "max = " in tip and "peak-to-peak" in tip,
          "the extremes are one hover away, not four more columns")
    check("→" in w._lbl_range.text(), f"the range is written out: {w._lbl_range.text()}")
    check(all(p is not None for p in w._span_patches), "the range is painted")
    check(w._btn_clear_range.isEnabled(), "and can be cleared")
    w._clear_span()
    check(w._span is None and w._stat_table.rowCount() == 0, "clearing empties it")
    w._on_span(9 * 3600.0, 9 * 3600.0)
    check(w._span is None, "a zero-width drag is a click, not a range")

    print("\n[the eye]")
    w._on_pv_eye("Waveplate")
    check(w._plot_order == ["SBW4"], f"a hidden PV is off the graph: {w._plot_order}")
    check(w._pv_table.rowCount() == 2, "but still listed, and still read")
    w._on_pv_eye("Waveplate")
    check(w._plot_order == ["SBW4", "Waveplate"], "and comes back")

    print("\n[no cameras picked]")
    w._cams = []
    w._resolve_frames()
    check("No camera picked" in w._tiles_hint.text(),
          "the frame panel explains itself instead of reading the share")
    check(w._tiles_grid.count() == 0, "and stays empty")

    print("\n[a picked formula is a curve like any other]")
    sl.PV_DERIVED[:] = [{"name": "Ratio", "expr": "A/B", "unit": "",
                         "bindings": {"A": "SBW4", "B": "Waveplate"}}]
    plan = om.derived_plan(["SBW4", "Ratio"])
    check([p["name"] for p in plan] == ["Ratio"] and not plan[0]["unbound"],
          "the plan names the formula and finds its letters bound")
    check(sorted(plan[0]["sources"]) == ["SBW4", "Waveplate"],
          f"and its leaf sources: {plan[0]['sources']}")
    got = om.build_derived_series(plan, w._series, w._windows)
    w._series["Ratio"] = got["Ratio"]
    w._pv_selected = ["SBW4", "Waveplate", "Ratio"]
    w._refresh_pick_labels()
    w._rebuild_graph()
    w._refresh_pv_table()
    ratio = w._series["Ratio"]
    check(ratio["ts"].size > 0, f"the formula has samples ({ratio['ts'].size})")
    check(np.allclose(ratio["val"][np.isfinite(ratio["val"])],
                      (energy / waveplate)[np.isfinite(energy / waveplate)],
                      rtol=1e-9, equal_nan=True),
          "and they are exactly A/B on the sources' own timestamps")
    check("Ratio" in w._plot_order, f"it is drawn: {w._plot_order}")
    check(len(w._fig.axes) == 3,
          f"a unitless formula gets a y axis of its OWN ({len(w._fig.axes)})")
    check(w._cmb_snap.count() == 3, "and can be snapped to")
    w._set_moment_from_x((ts[400] - w._axis_t0_ns) / om.NS_PER_S)
    w._refresh_pv_table()
    cell = w._pv_table.item(2, 2)
    check(cell is not None and float(cell.text()) > 0,
          f"its value at the moment reads as a number: {cell.text()}")
    check("not drawn" not in (cell.toolTip() or ""),
          "and nothing says it is not drawn any more")
    w._on_span((ts[100] - w._axis_t0_ns) / om.NS_PER_S,
               (ts[800] - w._axis_t0_ns) / om.NS_PER_S)
    check(w._stat_table.rowCount() == 3, "the marked range has a row for it too")
    w._clear_span()

    print("\n[a formula that cannot be computed says why]")
    sl.PV_DERIVED[:] = [{"name": "Ratio", "expr": "A/B", "unit": "",
                         "bindings": {"A": "SBW4"}}]
    bad = om.build_derived_series(om.derived_plan(["Ratio"]), w._series, w._windows)
    check(bad["Ratio"]["ts"].size == 0 and "stand for no PV" in bad["Ratio"]["reason"],
          f"unbound letter: {bad['Ratio']['reason'][:44]}…")
    w._series["Ratio"] = bad["Ratio"]
    w._refresh_pv_table()
    cell = w._pv_table.item(2, 2)
    check(cell.text() == "n/a" and "stand for no PV" in cell.toolTip(),
          "and the reason is where the number would be")
    sl.PV_DERIVED[:] = []
    del w._series["Ratio"]
    w._pv_selected = ["SBW4", "Waveplate"]
    w._rebuild_graph()

    print("\n[display controls]")
    w._sld_contrast.setValue(30)
    w._cb_gamma_auto.setChecked(True)
    opts = w._display_opts()
    check(opts["contrast"] == 30, "the contrast slider reaches the renderer")
    check(opts["gamma"] == om.img_scale.GAMMA_SLIDER_AUTO,
          "an Auto gamma tick outranks the gamma slider")
    check(not w._sld_gamma.isEnabled(), "and greys it out")
    w._cb_gamma_auto.setChecked(False)
    w._cmb_palette.setCurrentIndex(sl.GRADIENT_ID_DEFAULT)
    check(not w._sld_contrast.isEnabled() and not w._sld_bright.isEnabled(),
          "Default is the untouched file, so Contrast and Brightness are greyed out")
    w._cmb_palette.setCurrentIndex(sl.GRADIENT_ID_GRAYSCALE)
    check(w._sld_contrast.isEnabled(), "and live again on a real palette")

    print("\n[rebuild]")
    w._pv_selected = ["SBW4"]
    w._rebuild_graph()
    check(len(w._axes) == 1 and len(w._selectors) == 1,
          "a rebuild replaces the old graph rather than stacking on it")
    check(w._graph_lay.count() == 2,
          f"toolbar + canvas and nothing else ({w._graph_lay.count()})")
    w._series, w._pv_selected = {}, []
    w._rebuild_graph()
    check(w._plot_order == [] and w._canvas is None, "the empty state does not crash")
    w.cancel_scan()


# ═══ PART 2 — the frames, against a fake archive ═════════════════════════════
DAY = datetime(2026, 8, 21, tzinfo=timezone.utc).date()
CAM_BRIGHT = "C03-040-PFM13NF-_-IMG"
CAM_DIM = "C03-081-PCW3NF-_-IMG"
CAM_ABSENT = "C03-999-NOTHERE-_-IMG"
# Prague 12:00 on that day is 10:00 UTC (CEST), i.e. the "10" hour folder.
MOMENT = datetime(2026, 8, 21, 10, 0, 0, tzinfo=timezone.utc)
MOMENT_NS = int(MOMENT.timestamp()) * om.NS_PER_S


def write_frame(cam: str, ts_ns: int, peak_counts: int, factor: int) -> Path:
    """One archived frame. The archiver stores raw_counts × 65535/(2**bits−1) — a
    factor fixed WITHIN the frame — and records the raw peak as MaxValue."""
    hour = datetime.fromtimestamp(ts_ns / om.NS_PER_S, tz=timezone.utc).hour
    d = ROOT / "cpva-image-2026" / "2026" / "8" / "21" / str(hour) / cam
    d.mkdir(parents=True, exist_ok=True)
    counts = np.zeros((40, 60), dtype=np.uint16)
    counts[10:30, 20:40] = peak_counts // 2
    counts[18:22, 28:32] = peak_counts
    stored = (counts.astype(np.uint32) * factor).clip(0, 65535).astype(np.uint16)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(peak_counts))
    meta.add_text("Camera type", "Basler acA1600-20gm")
    p = d / f"{cam}_{ts_ns}.png"
    Image.fromarray(stored).save(p, pnginfo=meta)
    return p


def peak_code(qimg) -> int:
    return max(qimg.pixelColor(x, y).value()
               for y in range(qimg.height()) for x in range(qimg.width()))


def part1b():
    """The formula-over-time helpers on their own — pure arrays, no widget."""
    print("\n=== PART 1b: a formula over time ===")
    NS = om.NS_PER_S

    print("\n[the time base]")
    src = (np.arange(0, 10) * 5 * NS).astype(np.int64)
    base = (np.arange(-2, 12) * 2 * NS).astype(np.int64)
    ref = np.asarray([int(np.searchsorted(src, t, side="right")) - 1 for t in base])
    check(np.array_equal(om._hold_index(src, base), ref),
          "the held sample is the last one at or before each point")
    check(int(om._hold_index(src, base)[0]) == -1,
          "and -1 before the source's first sample")
    # Two channels writing the same shot 20 ms apart: one point per shot, not two.
    shots = (np.arange(100) * NS).astype(np.int64)
    a_ts, b_ts = shots, shots + 20_000_000
    merged = om._merge_base_ts([a_ts, b_ts])
    check(merged.size == 100, f"a shot is ONE point, not two ({merged.size})")
    check(np.array_equal(merged, b_ts),
          "and it sits where the last of the pair was published")

    print("\n[the time axis ticks like a clock]")
    check(om._tick_step_s(8 * 3600) == 3600.0,
          "an 8 h window ticks on the hour")
    check(om._tick_step_s(12 * 3600) == 7200.0,
          "a 12 h window every two hours")
    check(om._tick_step_s(24 * 3600) == 10800.0,
          "a whole day every three hours")
    check(om._tick_step_s(600) == 60.0, "ten minutes ticks every minute")
    check(om._tick_step_s(7 * 86400) % 86400 == 0,
          "a week ticks in whole days, never in 10 000-second lumps")

    print("\n[vectorising, and refusing to]")
    for ok_expr in ("A/B", "abs(A-B)*2", "A**0.5"):
        check(om._can_vectorise(ok_expr), f"{ok_expr!r} runs on whole arrays")
    for no_expr in ("min(A,B)", "math.sqrt(A)", "round(A)", "A if A>B else B", "A[0]"):
        check(not om._can_vectorise(no_expr), f"{no_expr!r} does not")
    arr = np.linspace(1.0, 2.0, 7)
    check(om._eval_vector("min(A)", {"A": arr}, arr.size) is None,
          "a reduction is caught by the shape test even past the veto")

    print("\n[the same answer either way]")
    ts = (np.arange(50) * NS).astype(np.int64)
    ea = np.linspace(20.0, 30.0, 50)
    wp = np.linspace(1000.0, 5000.0, 50)
    series = {"SBW4": {"channel": "FAKE:E", "ts": ts, "val": ea, "status": "ok"},
              "Waveplate": {"channel": "FAKE:W", "ts": ts, "val": wp,
                            "status": "ok"}}
    windows = [(int(ts[0]), int(ts[-1]) + NS)]
    for expr, label in (("A/B", "vectorised"), ("min(A,B)", "per point")):
        sl.PV_DERIVED[:] = [{"name": "F", "expr": expr, "unit": "",
                             "bindings": {"A": "SBW4", "B": "Waveplate"}}]
        got = om.build_derived_series(om.derived_plan(["F"]), series, windows)["F"]
        want = [sl.pv_eval_derived(["F"], {"SBW4": float(x), "Waveplate": float(y)},
                                  {"SBW4": "ok", "Waveplate": "ok"})["F"][0]
                for x, y in zip(ea, wp)]
        check(got["ts"].size == 50
              and np.allclose(got["val"], np.asarray(want, dtype=float), rtol=1e-12,
                              equal_nan=True),
              f"{label}: identical to is_t's own evaluator point by point")

    print("\n[gaps, holds and status]")
    sl.PV_DERIVED[:] = [{"name": "F", "expr": "A/B", "unit": "",
                         "bindings": {"A": "SBW4", "B": "Waveplate"}}]
    zero = wp.copy()
    zero[10] = 0.0
    s2 = dict(series)
    s2["Waveplate"] = {"channel": "FAKE:W", "ts": ts, "val": zero, "status": "ok"}
    got = om.build_derived_series(om.derived_plan(["F"]), s2, windows)["F"]
    check(np.isnan(got["val"]).sum() == 1 and np.isfinite(got["val"]).sum() == 49,
          "a division by zero leaves one break marker, not an infinity")
    check(np.isfinite(np.nanmean(got["val"])), "and the mean is still a number")

    late = {"channel": "FAKE:W", "ts": ts[20:], "val": wp[20:], "status": "ok"}
    s3 = dict(series)
    s3["Waveplate"] = late
    got = om.build_derived_series(om.derived_plan(["F"]), s3, windows)["F"]
    first = got["ts"][int(got["good"][0])]
    check(int(first) >= int(ts[20]),
          "with no seed, the formula starts at its slowest source's first sample")

    hole_ts = np.concatenate([ts, ts[-1] + (np.arange(1, 51) * NS
                                            + 2 * 3600 * NS)]).astype(np.int64)
    hole_val = np.concatenate([wp, wp])
    s4 = {"SBW4": {"channel": "FAKE:E", "ts": hole_ts,
                   "val": np.concatenate([ea, ea]), "status": "ok"},
          "Waveplate": {"channel": "FAKE:W", "ts": ts, "val": wp, "status": "ok"}}
    w4 = [(int(hole_ts[0]), int(hole_ts[-1]) + NS)]
    got = om.build_derived_series(om.derived_plan(["F"]), s4, w4)["F"]
    after = got["ts"] > int(ts[-1]) + 60 * NS
    check(not np.isfinite(got["val"][after]).any(),
          "a fast source is not carried across a two-hour hole")
    s5 = dict(s4)
    s5["Waveplate"] = {"channel": "L3-PFWP6-MTR03-1:RawPos", "ts": ts, "val": wp,
                       "status": "ok"}
    got = om.build_derived_series(om.derived_plan(["F"]), s5, w4)["F"]
    after = got["ts"] > int(ts[-1]) + 60 * NS
    check(np.isfinite(got["val"][after]).any(),
          "but a STEP channel is — its last sample IS the value, however old")
    s6 = dict(series)
    s6["Waveplate"] = dict(series["Waveplate"], status="error")
    got = om.build_derived_series(om.derived_plan(["F"]), s6, windows)["F"]
    check(got["status"] == "error", "a source that failed makes the formula 'error'")
    s7 = dict(series)
    s7["Waveplate"] = dict(series["Waveplate"], status="stale")
    got = om.build_derived_series(om.derived_plan(["F"]), s7, windows)["F"]
    check(got["status"] == "stale", "and a stale one makes it stale")

    print("\n[a formula on a formula]")
    sl.PV_DERIVED[:] = [
        {"name": "F", "expr": "A/B", "unit": "",
         "bindings": {"A": "SBW4", "B": "Waveplate"}},
        {"name": "G", "expr": "C*2", "unit": "", "bindings": {"C": "F"}},
    ]
    got = om.build_derived_series(om.derived_plan(["F", "G"]), series, windows)
    check(np.array_equal(got["G"]["ts"], got["F"]["ts"])
          and np.allclose(got["G"]["val"], 2.0 * got["F"]["val"], equal_nan=True),
          "the chained formula is exactly 2 × the one it is built on")
    sl.PV_DERIVED[:] = []

    print("\n[the loader, called straight]")
    sl.PV_DERIVED[:] = [{"name": "F", "expr": "A/B", "unit": "",
                         "bindings": {"A": "SBW4", "B": "Waveplate"}}]
    real_get_day, real_before = om.cpva.get_day, om.cpva.value_at_or_before
    om.cpva.get_day = lambda ch, key, **kw: om.cpva.DayResult(
        [(int(t), float(v)) for t, v in
         zip(ts, ea if ch == "FAKE:E" else wp)], "ok", 0.0)
    om.cpva.value_at_or_before = lambda *a, **k: om.cpva.LookupResult(
        None, None, "not_found")
    try:
        sig = om._PvLoadSignals()
        out = []
        sig.series.connect(lambda d, g: out.append(d))
        wanted = [("SBW4", "FAKE:E", "read"), ("Waveplate", "FAKE:W", "helper")]
        import threading as _th
        om._PvLoadTask(1, ["2026-08-24"], windows, wanted,
                       om.derived_plan(["SBW4", "F"]), sig, _th.Event()).run()
        app.processEvents()
        check(bool(out) and "F" in out[0] and out[0]["F"]["ts"].size > 0,
              "the load computes the formula it was given a plan for")
        check(out[0]["Waveplate"]["role"] == "helper",
              "an unpicked source is fetched and marked as a helper")
        stopped = _th.Event()
        stopped.set()
        out2 = []
        sig2 = om._PvLoadSignals()
        sig2.series.connect(lambda d, g: out2.append(d))
        om._PvLoadTask(2, ["2026-08-24"], windows, wanted,
                       om.derived_plan(["F"]), sig2, stopped).run()
        app.processEvents()
        check(not out2, "a task told to stop before it starts delivers nothing")
    finally:
        om.cpva.get_day, om.cpva.value_at_or_before = real_get_day, real_before
        sl.PV_DERIVED[:] = []


def part1c():
    """What the tab remembers, and what it forgets on purpose."""
    print("\n=== PART 1c: remembered settings ===")
    state = om._state_path()
    if state.exists():
        state.unlink()

    w = om.OneMomentWidget()
    day = om.date(2026, 8, 19)
    w._day = day
    w._pick_hours = (7, 30, 19, 15)
    w._pick_segments = None
    seg = sl.PickSeg(day, 7, 30, 19, 15)
    w._windows = [sl.seg_bounds_ns(seg)]
    w._cams = ["C03-040-PFM13NF-_-IMG"]
    w._pv_selected = ["SBW4", "Waveplate"]
    w._pv_hidden = {"Waveplate"}
    w._cmb_mode.setCurrentIndex(MODE_STACKED)
    w._sld_size.setValue(410)
    w._sld_contrast.setValue(41)
    w._cb_gamma_auto.setChecked(True)
    w._sections["stats"].set_expanded(False)
    w._save_state()
    check(state.exists(), f"the settings file is written: {state.name}")

    scans = []
    real_cams = sl.cameras_for_windows
    sl.cameras_for_windows = lambda *a, **k: (scans.append(1), ([], "no_data"))[1]
    try:
        w2 = om.OneMomentWidget()
    finally:
        sl.cameras_for_windows = real_cams
    check(w2._day == day and tuple(w2._pick_hours) == (7, 30, 19, 15),
          f"the window comes back: {w2._day} {tuple(w2._pick_hours)}")
    check(w2._windows == w._windows, "down to the minute")
    check(w2._cams == w._cams, "the cameras come back")
    check(w2._pv_selected == ["SBW4", "Waveplate"] and w2._pv_hidden == {"Waveplate"},
          "the PV pick and the eye come back")
    check(w2._cmb_mode.currentIndex() == MODE_STACKED
          and w2._sld_size.value() == 410 and w2._sld_contrast.value() == 41
          and w2._cb_gamma_auto.isChecked(),
          "the graph mode and the display controls come back")
    check(w2._sections["stats"]._expanded is False,
          "and a section left closed stays closed")
    check(not scans, "and building it read NOTHING from the share")
    check("07:30" in w2._btn_window.text() and "19:15" in w2._btn_window.text(),
          f"the window is written on its own button: {w2._btn_window.text()!r}")
    check("1 camera" in w2._btn_cams.text(),
          f"and the camera count on its own: {w2._btn_cams.text()!r}")

    print("\n[a PV that no longer exists]")
    w2._pv_selected = ["SBW4", "GhostPV"]
    w2._save_state()
    w3 = om.OneMomentWidget()
    check(w3._pv_selected == ["SBW4"],
          f"a PV gone from the shared registry is dropped: {w3._pv_selected}")

    print("\n[reloading clears the moment]")
    ts = np.asarray([w3._windows[0][0] + k * om.NS_PER_S for k in range(60)],
                    dtype=np.int64)
    w3._pv_selected = ["SBW4"]
    w3._series = {"SBW4": {"channel": "FAKE", "ts": ts,
                           "val": np.linspace(1, 2, ts.size), "status": "ok",
                           "role": "read"}}
    w3._rebuild_graph()
    w3._cams = []
    w3._set_moment_from_x((ts[10] - w3._axis_t0_ns) / om.NS_PER_S)
    check(w3._moment_ns is not None and w3._btn_prev.isEnabled(),
          "a moment is picked")
    w3._on_series(dict(w3._series), w3._pv_gen)
    check(w3._moment_ns is None, "a fresh load drops the moment")
    check(w3._lbl_moment.text() == "—",
          f"and says so instead of showing the old one: {w3._lbl_moment.text()!r}")
    check(not w3._btn_prev.isEnabled() and not w3._btn_next.isEnabled()
          and not w3._btn_popout.isEnabled(),
          "prev / next / pop out are switched off with it")
    check(w3._lbl_frames.text() == "The frames" and w3._tiles_grid.count() == 0,
          "and the frame wall is empty, with no title over it")

    print("\n[a pick while a load is running]")
    gen = w3._pv_gen
    w3._abandon_load()
    check(w3._pv_gen > gen and w3._pv_stop.is_set() is False,
          "the running load is abandoned and a fresh stop flag installed")
    w3._on_series({"NOPE": {"channel": "x", "ts": ts, "val": ts * 0.0,
                            "status": "ok", "role": "read"}}, gen)
    check("NOPE" not in w3._series,
          "and its result cannot land in the state it no longer belongs to")


def part2():
    print("\n=== PART 2: frames, against a fake archive on local disk ===")
    if ROOT.exists():
        shutil.rmtree(ROOT, ignore_errors=True)

    # Bright camera: a frame every 35 s; the nearest to the moment is 5 s late.
    for k in (-2, -1, 0, 1, 2):
        write_frame(CAM_BRIGHT, MOMENT_NS + (k * 35 + 5) * om.NS_PER_S, 4000, 16)
    # Dim camera: one frame 12 s early, peaking at 1/16 of the sensor range.
    write_frame(CAM_DIM, MOMENT_NS - 12 * om.NS_PER_S, 255, 257)
    # A camera whose folder exists but holds nothing near the moment.
    write_frame(CAM_ABSENT, MOMENT_NS + 3600 * om.NS_PER_S, 2000, 32)
    print(f"  fake archive at {ROOT}")

    real_root = sl.container_root_for_year
    sl.container_root_for_year = lambda year: ROOT / f"cpva-image-{year}"
    try:
        w = om.OneMomentWidget()
        w._day = DAY
        w._windows = [om.cpva.day_bounds_ns("2026-08-21")]
        day_start = w._windows[0][0]
        w._axis_t0_ns = day_start
        w._cams = [CAM_BRIGHT, CAM_DIM, CAM_ABSENT]
        ts = np.asarray([MOMENT_NS + k * 35 * om.NS_PER_S for k in range(-3, 4)],
                        dtype=np.int64)
        w._pv_selected = ["SBW4"]
        w._series = {"SBW4": {"channel": "FAKE", "ts": ts,
                              "val": np.linspace(28, 32, ts.size), "status": "ok"}}
        w._rebuild_graph()

        x = (MOMENT_NS - day_start) / om.NS_PER_S
        w._set_moment_from_x(x)
        pump(w, lambda: w._btn_popout.isEnabled())

        print("\n[tiles]")
        res = {r["cam"]: r for r in w._tile_results}
        check(len(res) == 3, f"one result per picked camera ({len(res)})")
        b = res.get(CAM_BRIGHT, {})
        check(b.get("img") is not None, "the bright camera got a picture")
        check(b.get("ts_ns") == MOMENT_NS + 5 * om.NS_PER_S,
              "and it is the NEAREST frame (+5 s), not the first one found")
        check(b.get("img") is not None
              and max(b["img"].width(), b["img"].height()) <= w._sld_size.value(),
              "scaled down to the tile size")
        d = res.get(CAM_DIM, {})
        check(d.get("img") is not None, "the dim camera got a picture")
        check(d.get("ts_ns") == MOMENT_NS - 12 * om.NS_PER_S,
              "12 s before the moment, from the same hour folder")
        a = res.get(CAM_ABSENT, {})
        check(a.get("img") is None,
              "the camera with nothing near the moment has no picture")
        check("no frame" in (a.get("note") or ""), f"and says so: {a.get('note')!r}")

        print("\n[absolute scale]")
        if b.get("img") is not None and d.get("img") is not None:
            pb, pd = peak_code(b["img"]), peak_code(d["img"])
            print(f"       bright peak code {pb}, dim peak code {pd}")
            check(pb > 200, f"a frame at 4000/4095 counts renders near white ({pb})")
            check(pd < pb // 4,
                  f"a frame at 255 counts stays dark next to it ({pd} vs {pb})")

        print("\n[captions]")
        w._place_tiles()
        tiles = [w._tiles_grid.itemAt(i).widget()
                 for i in range(w._tiles_grid.count())]
        tiles = [t for t in tiles if isinstance(t, om._Tile)]
        check(len(tiles) == 3, f"three tiles on screen ({len(tiles)})")
        texts = []
        for t in tiles:
            texts += [c.text() for c in t.findChildren(QLabel)]
        all_text = " | ".join(texts)
        print("       " + all_text.encode("ascii", "replace").decode("ascii"))
        # The caption says the camera and the time and nothing else — the offset from
        # the moment, and the file, are in the tooltip.
        check("12:00:05" in all_text, "the frame's own time is on the caption")
        check("+5.0 s" not in all_text, "the offset is NOT on the caption")
        bright_tile = [t for t in tiles
                       if t._res.get("cam") == CAM_BRIGHT][0]
        check("+5.0 s" in bright_tile.toolTip(), "it is in the tooltip")
        check(str(bright_tile._res["path"]) in bright_tile.toolTip(),
              "together with the file it came from")
        # The Slider's own short label: container code, number and -IMG all go.
        check("PFM13NF" in all_text and CAM_BRIGHT not in all_text,
              "the camera is named the short way, as on a Slider tile")
        # No black gutter: the picture area is the picture, not the widest tile.
        pics = [c for c in bright_tile.findChildren(QLabel) if c.pixmap()]
        check(bool(pics) and pics[0].width() == pics[0].pixmap().width(),
              "the picture area is exactly as wide as the picture")

        print("\n[a display control re-renders, it does not re-read]")
        res_gen, rnd_gen = w._res_gen, w._rnd_gen
        w._cmb_palette.setCurrentIndex(sl.GRADIENT_NAMES.index("Hot"))
        pump(w, lambda: w._rnd_gen > rnd_gen and w._tile_results
             and all(r.get("img") is not None or r.get("path") is None
                     for r in w._tile_results), timeout=20)
        check(w._res_gen == res_gen, "the share is not walked again")
        check(w._rnd_gen > rnd_gen, "but the frames are rendered again")
        b3 = {r["cam"]: r for r in w._tile_results}.get(CAM_BRIGHT, {})
        check(b3.get("img") is not None
              and b3["img"].format() != QImage.Format.Format_Grayscale8,
              "and a colour palette comes out in colour")

        print("\n[the pop-out follows the tab]")
        w._popout()
        dlg = w._popout_dlg
        check(dlg is not None
              and len(dlg._holder.findChildren(om._Tile)) == 3,
              "the pop-out shows the same three frames")
        w._popout()
        check(w._popout_dlg is dlg,
              "opening it again raises the one window instead of making a second")

        print("\n[a second click]")
        w._set_moment_from_x(x + 35.0)
        pump(w, lambda: len(w._tile_results) >= 3
             and {r["cam"]: r for r in w._tile_results}
             .get(CAM_BRIGHT, {}).get("ts_ns") == MOMENT_NS + 40 * om.NS_PER_S)
        check(len(w._tile_results) == 3,
              f"the tiles are replaced, not appended ({len(w._tile_results)})")
        b2 = {r["cam"]: r for r in w._tile_results}.get(CAM_BRIGHT, {})
        check(b2.get("ts_ns") == MOMENT_NS + 40 * om.NS_PER_S,
              "and they follow the new moment")
        tiles2 = w._popout_dlg._holder.findChildren(om._Tile)
        caps = " | ".join(c.text() for t in tiles2 for c in t.findChildren(QLabel))
        check("12:00:40" in caps,
              "the open pop-out moved to the new moment with the wall")
        w._invalidate_moment()
        check(w._popout_dlg is None,
              "and it is closed rather than left showing frames that are gone")

        print("\n[the buttons are painted, not typed]")
        check(om._icon("play") is not None and om._icon("calendar") is not None,
              "the painted icons come from the Workshop's own set")
        check(not w._btn_load.icon().isNull(),
              "and the Load button carries one")
        w.cancel_scan()
    finally:
        sl.container_root_for_year = real_root
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    part1()
    part1b()
    part1c()
    part2()
    print("\n" + ("ALL OK" if not _FAILS
                  else f"{len(_FAILS)} FAILED: " + "; ".join(_FAILS)))
    sys.exit(1 if _FAILS else 0)
