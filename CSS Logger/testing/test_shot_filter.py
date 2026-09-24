"""Spectra tab — the shot filter: keep only the shots taken at a given PV value.

The feature: several conditions on any scalar PV ("GDD = 24700 +- 0"), each with
its own tick box, AND-ed together, in Archive and in Live. The averages are
rebuilt from the shots that matched, and so are the metrics, the legend counts,
the shot bar and the CSV.

What is pinned here, and why each one is worth a test:

  HOLD FORWARD
    * the dispersion PVs record ~35 samples in three days (FOD once), so a short
      selection usually holds NO sample of its own: every value comes from the
      archiver's "last sample before the window" freebie. A test with a sample
      inside the window would pass while the real case was broken.
    * a step mid-selection: the shot AT the step takes the new value.
    * a channel that never recorded before the shots -> NaN -> rejected, and the
      panel names the channel instead of showing an empty graph.

  THE COMPARISON
    * MEASURED on three days of archive: GDD is stored exactly (24700.0), but
      TOD holds -97999.99999999999 where -98000 was set. A bare "==" would throw
      away every shot at that setting, which looks exactly like "never measured".
    * at target 0 a purely relative epsilon would give zero slack, so 1e-17 has
      to match and 0.01 must not.

  THE REWRITE
    * stack / stack_ts / n stay aligned after filtering,
    * the average really is the average of the kept rows (a no-op fails),
    * switching the filter off restores the analysis's own numbers bit for bit,
    * the *_all originals are never touched.

  THE SHOT BAR
    * it is restored by (region, measurement time) and not by (region, row):
      the filter renumbers the rows, so a row match lands on the neighbour.

  NOTHING MATCHES
    * the graph, the details line, the folded header and the export all name the
      FILTER — never the wavelength range and never "analyze something first".

  LIVE
    * "average last N" slices the RAW buffer first and the filter comes after,
    * the red "newest" curve is the newest MATCHING shot and says how many newer
      ones were dropped,
    * a channel that has not been polled yet arms instead of rejecting,
    * the filter never empties the buffer.

No archiver, no network: every series is made up here.

Run:  python testing/test_shot_filter.py
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
import json
import tempfile

import numpy as np
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 256
X = np.linspace(780.0, 840.0, NX)
T0 = int(1_756_000_000 * 1e9)
SEC = 10 ** 9
HOUR = 3600 * SEC
DAY = 86_400 * SEC

GDD = "L3-SPFE-AOD03-002:Order2_RB"
TOD = "L3-SPFE-AOD03-002:Order3_RB"
FOD = "L3-SPFE-AOD03-002:Order4_RB"

_failed = False
# Widgets are kept alive on purpose: a SpectraWidget's background fetches hold a
# _Sig parented to it, and letting the widget be garbage collected while one is
# still emitting prints "Signal source has been deleted" after the result line.
_KEEP: list = []


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


# ── fixture ───────────────────────────────────────────────────────────────────
def _stack(n: int, centre: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    c = centre + rng.normal(0.0, 1.2, n)
    w = 8.0 + rng.normal(0.0, 0.6, n)
    a = 1.0 + rng.normal(0.0, 0.12, n)
    return (a[:, None] * np.exp(-0.5 * ((X[None, :] - c[:, None]) / w[:, None]) ** 2)
            + rng.normal(0.0, 0.004, (n, NX)))


def _region(rid: int, times, centre: float, seed: int, colour: str,
            series: dict) -> dict:
    """An analyzed region exactly as the analysis worker leaves it.

    `series` is {channel: [(ts, value)]} — the raw archive reads, from which the
    per-shot values are held forward, the same way _run_analysis does it.
    """
    times = [int(t) for t in times]
    st = _stack(len(times), centre, seed)
    stats = sp_t._stats_from_stack(st)
    ts_arr = np.asarray(times, dtype=np.int64)
    # The window means, as the analysis writes them: a region straddling a step
    # therefore has an average GDD that is neither of the two settings.
    def _win_mean(ch):
        v = sp_t._hold_forward(series.get(ch, []), ts_arr)
        v = v[np.isfinite(v)]
        return float(v.mean()) if v.size else None

    r = {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": colour, "visible": True, "expanded": True,
        "show_individual": False, "analyzed": True, "x": X,
        "stack_all": st, "stack_ts_all": list(times), "n_all": len(times),
        "stats_all": {k: stats[k] for k in sp_t.STAT_KEYS},
        "scalar_series": dict(series),
        "shot_vals": {ch: sp_t._hold_forward(s, ts_arr)
                      for ch, s in series.items()},
        "orders_all": {lbl: _win_mean(ch) for lbl, ch in sp_t.ORDER_PVS},
        "energy_avg_all": 9.87, "energy_n_all": len(times),
    }
    return r


def _widget():
    w = SpectraWidget()
    w.resize(1500, 900)
    w._spec_base_pv = "L3-SBW4-SPEC:Spectrum"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._x_data = X
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)
    w._chk_autofit.setChecked(False)
    # A filter file saved on this machine must not decide what the test sees.
    w._filter_on = False
    w._filter_conds = [{"label": lbl, "channel": ch, "value": None, "tol": 0.0,
                        "on": False} for lbl, ch in sp_t.ORDER_PVS]
    w._rebuild_filter_rows()
    w._chk_filter.setChecked(False)
    _KEEP.append(w)
    return w


def _set_filter(w, *conds, on=True):
    """conds: (label, channel, value, tol). Applies straight away, no debounce."""
    w._filter_conds = [{"label": lbl, "channel": ch, "value": val, "tol": tol,
                        "on": True} for lbl, ch, val, tol in conds]
    w._filter_on = on
    w._rebuild_filter_rows()
    w._apply_shot_filter()
    w._redraw_spectra()
    w._update_filter_readout()
    QApplication.processEvents()


def _load_time_axis(w, *shot_times):
    wins = [(min(t) - HOUR, max(t) + HOUR) for t in shot_times]
    w._windows = wins
    w._tmap = sp_t._TimeMap(wins)
    w._ax_top.set_xlim(*w._tmap.xlim())
    return wins


def _set_show(w, text: str):
    i = w._cmb_method.findText(text)
    assert i >= 0, f"'{text}' missing from the Show box"
    w._cmb_method.setCurrentIndex(i)
    QApplication.processEvents()


# ── A. hold forward ───────────────────────────────────────────────────────────
def test_hold_forward():
    print("\ntest_hold_forward  (the value AT each shot)")

    # The normal case: the PV was written an hour before the selection and not
    # once inside it. Only the archiver's pre-window freebie carries the value.
    times = np.array([T0 + i * SEC for i in range(500)], dtype=np.int64)
    vals = sp_t._hold_forward([(T0 - HOUR, 24700.0)], times)
    _ok(vals.shape == times.shape and np.all(vals == 24700.0),
        "a selection with no sample of its own still has a value everywhere",
        f"{np.unique(vals)}")

    # A step in the middle: side='right', so the shot written at the same instant
    # already counts as the new value.
    t_mid = int(times[250])
    vals = sp_t._hold_forward([(T0 - HOUR, 24700.0), (t_mid, 24800.0)], times)
    _ok(np.all(vals[:250] == 24700.0), "shots before a step hold the old value")
    _ok(vals[250] == 24800.0, "the shot AT the step takes the new one", f"{vals[250]}")
    _ok(np.all(vals[250:] == 24800.0), "and everything after it too")

    # Nothing archived at all, and nothing archived YET.
    _ok(np.all(np.isnan(sp_t._hold_forward([], times))),
        "a channel that never recorded is NaN, not zero")
    late = sp_t._hold_forward([(int(times[100]), 5.0)], times)
    _ok(np.all(np.isnan(late[:100])) and np.all(late[100:] == 5.0),
        "shots before the first sample are NaN, the rest are held",
        f"{np.count_nonzero(np.isnan(late))} NaN")

    # Samples handed over out of order must not corrupt the lookup.
    shuffled = [(t_mid, 24800.0), (T0 - HOUR, 24700.0)]
    _ok(np.array_equal(sp_t._hold_forward(shuffled, times),
                       sp_t._hold_forward(list(reversed(shuffled)), times)),
        "the sample order it is handed does not matter")


# ── B. the comparison ─────────────────────────────────────────────────────────
def test_match_value():
    print("\ntest_match_value  (exact, but not naively exact)")

    # Straight out of the archive, three days of Order3_RB.
    tod = np.array([-98000.0, -97999.99999999999, -94000.00000000001,
                    -95000.0, np.nan])
    m = sp_t._match_value(tod, -98000.0, 0.0)
    _ok(bool(m[1]), "TOD -97999.99999999999 counts as -98000 at tolerance 0")
    _ok(not m[2] and not m[3], "and -94000.00000000001 / -95000 still do not")
    _ok(bool(sp_t._match_value(tod, -94000.0, 0.0)[2]),
        "-94000.00000000001 counts as -94000")

    gdd = np.array([24700.0, 24701.0, 24699.9, 24650.0, 24750.0, 24750.1])
    m = sp_t._match_value(gdd, 24700.0, 0.0)
    _ok(bool(m[0]) and not m[1] and not m[2],
        "GDD 24700 +- 0 is exact: 24701 and 24699.9 are out")
    m = sp_t._match_value(gdd, 24700.0, 50.0)
    _ok(bool(m[3]) and bool(m[4]) and not m[5],
        "+- 50 includes both ends and excludes 24750.1")

    zero = np.array([1e-17, 0.0, 0.01])
    m = sp_t._match_value(zero, 0.0, 0.0)
    _ok(bool(m[0]) and bool(m[1]) and not m[2],
        "at target 0 a float-noise 1e-17 matches but 0.01 does not")

    _ok(not np.any(sp_t._match_value(np.array([np.nan]), 0.0, 1e9)),
        "NaN never matches, at any tolerance")


# ── C. the rewrite ────────────────────────────────────────────────────────────
def test_region_rewrite():
    print("\ntest_region_rewrite  (stack, stack_ts, n and the averages)")
    app = QApplication.instance() or QApplication([])
    w = _widget()

    times = [T0 + i * SEC for i in range(300)]
    step = times[120]
    series = {GDD: [(T0 - HOUR, 24700.0), (step, 24800.0)],
              TOD: [(T0 - HOUR, -97999.99999999999)],
              FOD: [(T0 - HOUR, -15000.0)]}
    w._regions = [_region(1, times, 810.0, 3, "#1f77b4", series)]
    _load_time_axis(w, times)
    w._apply_shot_filter()

    _ok(w._regions[0]["n"] == 300 and w._regions[0]["filter_mask"] is None,
        "with the filter off every shot is used", f"n={w._regions[0]['n']}")
    mean_off = np.array(w._regions[0]["mean"], copy=True)

    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    r = w._regions[0]
    _ok(r["n"] == 120, "only the shots before the GDD step are kept", f"n={r['n']}")
    _ok(len(r["stack"]) == r["n"] == len(r["stack_ts"]),
        "stack, stack_ts and n agree", f"{len(r['stack'])}/{r['n']}/{len(r['stack_ts'])}")
    # The one check that catches a slipped index: re-derive the verdict from the
    # timestamps that survived and every one of them must still match.
    kept_vals = sp_t._hold_forward(series[GDD],
                                   np.asarray(r["stack_ts"], dtype=np.int64))
    _ok(bool(np.all(sp_t._match_value(kept_vals, 24700.0, 0.0))),
        "every surviving shot really does satisfy the condition")
    _ok(np.allclose(r["mean"], r["stack_all"][:120].mean(axis=0)),
        "the average is the average of the kept rows")
    _ok(not np.allclose(r["mean"], mean_off),
        "and it is NOT the unfiltered average (a no-op would pass otherwise)")
    _ok(r["orders"]["GDD"] == 24700.0,
        "the GDD shown is the matching shots' own value, not the window mean",
        f"{r['orders']['GDD']} (window mean was {r['orders_all']['GDD']})")
    _ok(r["energy_n"] == 0 and r["energy_avg"] is None,
        "a scalar with no per-shot values reports nothing rather than guessing")

    # Two conditions, AND.
    _set_filter(w, ("GDD", GDD, 24800.0, 0.0), ("TOD", TOD, -98000.0, 0.0))
    _ok(w._regions[0]["n"] == 180,
        "two conditions both have to hold", f"n={w._regions[0]['n']}")
    _set_filter(w, ("GDD", GDD, 24800.0, 0.0), ("TOD", TOD, -12345.0, 0.0))
    _ok(w._regions[0]["n"] == 0,
        "one impossible condition is enough to keep nothing",
        f"n={w._regions[0]['n']}")

    # A row that is switched off must not filter, and neither must a blank value.
    w._filter_conds = [{"label": "GDD", "channel": GDD, "value": 24700.0,
                        "tol": 0.0, "on": False}]
    w._apply_shot_filter()
    _ok(w._regions[0]["n"] == 300, "an unticked condition does nothing")
    w._filter_conds = [{"label": "GDD", "channel": GDD, "value": None,
                        "tol": 0.0, "on": True}]
    w._apply_shot_filter()
    _ok(w._regions[0]["n"] == 300, "a ticked condition with no value does nothing")

    # And the originals were never touched by any of it.
    _ok(w._regions[0]["n_all"] == 300 and len(w._regions[0]["stack_all"]) == 300,
        "the unfiltered stack survived every pass")
    _ok(np.array_equal(w._regions[0]["mean"], mean_off),
        "switching the filter off restores the analysis's own average, bit for bit")


def test_lazy_stats_fill_in():
    print("\ntest_lazy_stats_fill_in  (an average the filter pass skipped)")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    times = [T0 + i * SEC for i in range(200)]
    series = {GDD: [(T0 - HOUR, 24700.0), (times[80], 24800.0)]}
    w._regions = [_region(1, times, 810.0, 5, "#1f77b4", series)]
    _load_time_axis(w, times)
    _set_show(w, "Mean")
    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    r = w._regions[0]
    _ok(r["mean"] is not None, "the mean is there while Mean is shown")
    _ok(r["median"] is None,
        "and the median was not computed — 9.6 s of work nobody asked for")
    _set_show(w, "Median")
    r = w._regions[0]
    _ok(r["median"] is not None, "switching to Median fills it in")
    _ok(np.allclose(r["median"], np.median(r["stack_all"][:80], axis=0)),
        "from the matching rows, not from all of them")


# ── D. the shot bar ───────────────────────────────────────────────────────────
def test_shot_bar_keeps_the_same_shot():
    print("\ntest_shot_bar_keeps_the_same_shot  (identity is the time, not the row)")
    app = QApplication.instance() or QApplication([])
    w = _widget()

    # Two regions on two days, interleaved in time, so list order cannot be
    # right by accident.
    t_a = [T0 + i * SEC for i in range(60)]
    t_b = [T0 + DAY + i * SEC for i in range(60)]
    ser_a = {GDD: [(T0 - HOUR, 24700.0), (t_a[30], 24800.0)]}
    ser_b = {GDD: [(T0 + DAY - HOUR, 24700.0), (t_b[10], 24800.0)]}
    w._regions = [_region(1, t_a, 808.0, 7, "#1f77b4", ser_a),
                  _region(2, t_b, 814.0, 8, "#d62728", ser_b)]
    _load_time_axis(w, t_a, t_b)
    w._apply_shot_filter()
    _set_show(w, "Every spectrum")
    QApplication.processEvents()

    items = w._single_items_cache
    _ok(len(items) == 120, "every shot of both regions is in the list", str(len(items)))
    _ok([it["ts"] for it in items] == sorted(it["ts"] for it in items),
        "ordered by the time it was measured")

    # Park on a shot in region 1 that the filter will KEEP, with plenty of
    # shots before it that the filter will throw away... so pick one from the
    # 24800 half and then filter to 24800: the rows before it vanish.
    target_pos = next(j for j, it in enumerate(items)
                      if it["rid"] == 1 and it["ts"] == t_a[45])
    w._go_to_shot(target_pos)
    QApplication.processEvents()
    before = w._single_items_cache[w._single_pos]
    _ok(before["ts"] == t_a[45], "parked on a known shot", str(before))

    _set_filter(w, ("GDD", GDD, 24800.0, 0.0))
    after = w._single_items_cache[w._single_pos]
    _ok(after["ts"] == t_a[45],
        "the filter renumbers the list but leaves you on the SAME shot",
        f"row {before['k']} -> {after['k']}, pos {target_pos} -> {w._single_pos}")
    _ok(after["k"] != before["k"],
        "and the row index really did change (so a (rid,row) match would fail)",
        f"{before['k']} -> {after['k']}")

    # Every place the bar can rest is a matching shot.
    kept_ts = {int(t) for r in w._regions for t in (r.get("stack_ts") or [])}
    _ok(all(int(it["ts"]) in kept_ts for it in w._single_items_cache),
        "the bar can only stand on a shot that passed the filter",
        f"{len(w._single_items_cache)} places")
    _ok("filtered" in w._lbl_single.text(),
        "and the caption says the list is filtered", w._lbl_single.text())

    # Dropping the shot the bar is on must not raise, and must land on a real one.
    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    cur = w._single_current()
    _ok(cur[0] is not None,
        "dropping the picked shot still leaves the bar on a real one",
        str(w._single_items_cache[w._single_pos]))
    kept_ts = {int(t) for r in w._regions for t in (r.get("stack_ts") or [])}
    _ok(int(w._single_items_cache[w._single_pos]["ts"]) in kept_ts,
        "which is one of the survivors")

    # The "now ..." readout: the condition's PV at the shot the bar is ON.
    pos_early = next(j for j, it in enumerate(w._single_items_cache)
                     if it["rid"] == 1 and it["ts"] == t_a[5])
    w._go_to_shot(pos_early)
    QApplication.processEvents()
    _ok(w._cond_value_at_picked_shot(w._filter_conds[0]) == 24700.0,
        "the row reads the PV's value at the picked shot",
        str(w._cond_value_at_picked_shot(w._filter_conds[0])))
    note, _style = w._filter_row_note(w._filter_conds[0])
    _ok("now 24700" in note, "and puts it on the row", repr(note))


# ── E. nothing matches ────────────────────────────────────────────────────────
def test_nothing_matches_says_why():
    print("\ntest_nothing_matches_says_why")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    times = [T0 + i * SEC for i in range(200)]
    series = {GDD: [(T0 - HOUR, 24700.0)]}
    w._regions = [_region(1, times, 810.0, 11, "#1f77b4", series)]
    _load_time_axis(w, times)
    w._apply_shot_filter()
    w._rebuild_regions_ui()

    _set_filter(w, ("GDD", GDD, 1e9, 0.0))
    _ok(w._regions[0]["n"] == 0, "nothing survives")

    msg = w._filter_keeps_nothing_msg()
    _ok("Shot filter kept 0 of 200" in msg, "the message counts what was lost", repr(msg))
    _ok("GDD = 1000000000" in msg, "and names the condition", repr(msg))
    _ok("range" not in msg.lower(),
        "it does not blame the wavelength range", repr(msg))
    _ok("Analyze" not in msg,
        "and it does not tell the user to analyze something", repr(msg))

    on_graph = "\n".join(t.get_text() for t in w._ax_bot.texts)
    _ok("Shot filter kept 0" in on_graph,
        "the graph itself carries it", repr(on_graph[:90]))

    count_html = w._region_count_html(w._regions[0])
    _ok("0" in count_html and "of 200" in count_html,
        "the details line says 0 of 200", count_html)
    _ok("#B71C1C" in count_html, "in red, not grey", count_html)

    _ok("0 of 200" in w._g_filter._title.lower().replace("of", "of"),
        "the folded header carries the count", w._g_filter._title)
    _ok(w._g_filter._accent == sp_t._FILTER_ALARM,
        "and the header turns red", w._g_filter._accent)
    _ok("nothing matches" in w._filter_status_line().lower(),
        "the status line says so too", w._filter_status_line())

    # A whole-day fallback: the filter must not be blamed when it is innocent.
    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    _ok(w._filter_keeps_nothing_msg() == "",
        "with shots surviving the filter claims nothing")


def test_channel_never_recorded():
    print("\ntest_channel_never_recorded  (NaN is rejected, and named)")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    times = [T0 + i * SEC for i in range(50)]
    # FOD's first sample is AFTER every shot — the archive knew nothing yet.
    series = {GDD: [(T0 - HOUR, 24700.0)], FOD: [(T0 + DAY, -15000.0)]}
    w._regions = [_region(1, times, 810.0, 13, "#1f77b4", series)]
    _load_time_axis(w, times)
    _set_filter(w, ("FOD", FOD, -15000.0, 0.0))
    _ok(w._regions[0]["n"] == 0, "a shot with no archived value is rejected")
    msg = w._filter_keeps_nothing_msg()
    _ok(FOD in msg and "no archived value" in msg,
        "and the message names the channel", repr(msg))
    _ok("first sample" in msg,
        "and when that channel DID first record", repr(msg))
    note, style = w._filter_row_note(w._filter_conds[0])
    _ok(FOD in note and "#B71C1C" in style,
        "the condition's own line says it, in red", f"{note!r} {style!r}")


# ── F. pending channel ────────────────────────────────────────────────────────
def test_pending_channel_passes_through():
    print("\ntest_pending_channel_passes_through")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    times = [T0 + i * SEC for i in range(40)]
    w._regions = [_region(1, times, 810.0, 17, "#1f77b4",
                          {GDD: [(T0 - HOUR, 24700.0)]})]
    _load_time_axis(w, times)
    # A condition on a channel the analysis never fetched.
    _set_filter(w, ("Custom", "L3-TEST-SOMETHING:Value", 42.0, 0.0))
    _ok(w._regions[0]["n"] == 40,
        "a condition whose values are still in flight keeps everything",
        f"n={w._regions[0]['n']}")
    note, _style = w._filter_row_note(w._filter_conds[0])
    _ok("fetching" in note, "and the row says it is being fetched", repr(note))

    calls = []
    real = sp_t._fetch_scalars

    def _fake(ch, t0, t1):
        calls.append((ch, t0, t1))
        return [(T0 - HOUR, 42.0)] if ch.startswith("L3-TEST") else []

    sp_t._fetch_scalars = _fake
    try:
        started = w._ensure_filter_values()
        _ok(started, "the top-up fetch is started")
        for _ in range(60):
            QApplication.processEvents()
            if not w._filter_busy:
                break
        _ok(len(calls) == 1, "exactly one request, for the missing pair", str(calls))
        _ok(w._regions[0]["n"] == 40,
            "and the value really did arrive, so everything matches 42",
            f"n={w._regions[0]['n']}")
        _ok(not w._ensure_filter_values(),
            "a second apply asks for nothing — the values are cached")
    finally:
        sp_t._fetch_scalars = real


# ── G. live ───────────────────────────────────────────────────────────────────
def test_live_filter():
    print("\ntest_live_filter")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    w._live = True
    times = [T0 + i * SEC for i in range(40)]
    st = _stack(40, 810.0, 23)
    for t, a in zip(times, st):
        w._live_buf.append((int(t), a))
    # GDD steps to 24800 at shot 30, so the four newest shots are rejected when
    # the filter asks for 24700.
    w._live_scalars = {GDD: [(T0 - HOUR, 24700.0), (int(times[36]), 24800.0)]}
    w._sb_live_n.setValue(20)

    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    kept = w._live_shots(20)
    _ok(w._live_seen_n == 40,
        "the filter sweeps the WHOLE buffer first", str(w._live_seen_n))
    _ok(w._live_matched_n == 36,
        "36 of the 40 shots were taken at 24700", str(w._live_matched_n))
    _ok(len(kept) == 20,
        "and N then takes the last 20 of THOSE", str(len(kept)))
    _ok([t for t, _ in kept] == times[16:36],
        "which are the newest matching shots, in order")
    _ok(w._live_newer_dropped == 4,
        "it knows how many NEWER raw shots it dropped",
        str(w._live_newer_dropped))
    _ok(len(w._live_buf) == 40,
        "the buffer itself is untouched — a filter never destroys data",
        str(len(w._live_buf)))
    _ok(w._live_export_spectra() == kept,
        "and the export holds exactly what the picture holds")

    # The red curve is the newest MATCHING shot and says how many were dropped.
    w._redraw_spectra()
    QApplication.processEvents()
    red = [ln for ln in w._ax_bot.lines
           if getattr(ln, "get_color", None) and ln.get_color() == "#D32F2F"]
    _ok(len(red) == 1, "one red curve", str(len(red)))
    if red:
        label = red[0].get_label()
        _ok("4 newer filtered out" in label,
            "whose label counts the shots thrown away", label)
        _ok(np.allclose(red[0].get_ydata()[:5], st[35][:5]),
            "and it really is shot 36, not shot 40")

    # A channel nobody has polled yet arms; it must not reject.
    w._live_scalars = {}
    kept = w._live_shots(20)
    _ok(len(kept) == 20 and w._live_arming == [GDD],
        "an unpolled channel arms instead of rejecting everything",
        f"{len(kept)} kept, arming={w._live_arming}")

    # Nothing matches: a message, and still no data lost.
    w._live_scalars = {GDD: [(T0 - HOUR, 24700.0)]}
    _set_filter(w, ("GDD", GDD, 1.0, 0.0))
    _ok(w._live_shots(20) == [], "an impossible value keeps nothing")
    on_graph = "\n".join(t.get_text() for t in w._ax_bot.texts)
    _ok("Shot filter kept 0" in on_graph,
        "the graph says the filter did it", repr(on_graph[:80]))
    _ok(len(w._live_buf) == 40, "and the buffer is still full", str(len(w._live_buf)))
    w._live = False


# ── H. the colour collapse ────────────────────────────────────────────────────
def test_colour_by_pinned_order():
    print("\ntest_colour_by_pinned_order")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    t_a = [T0 + i * SEC for i in range(40)]
    t_b = [T0 + DAY + i * SEC for i in range(40)]
    # Both selections straddle a GDD step, so without the filter the two regions
    # have DIFFERENT average GDDs and the rainbow works. The filter is then what
    # pins them both to 24700.
    ser = {GDD: [(T0 - HOUR, 24700.0), (t_a[20], 24800.0)]}
    ser_b = {GDD: [(T0 + DAY - HOUR, 24700.0), (t_b[20], 24900.0)]}
    w._regions = [_region(1, t_a, 808.0, 29, "#1f77b4", ser),
                  _region(2, t_b, 814.0, 31, "#d62728", ser_b)]
    _load_time_axis(w, t_a, t_b)
    w._cmb_color.setCurrentText("GDD")
    w._apply_shot_filter()
    rainbow = w._compute_region_colors()
    _ok(rainbow[1] != rainbow[2] and w._colorbar_info is not None,
        "with two different GDDs the rainbow and its colour bar work as before")

    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))
    colors = w._compute_region_colors()
    _ok(w._regions[0]["orders"]["GDD"] == w._regions[1]["orders"]["GDD"] == 24700.0,
        "the filter has pinned both spectra to one GDD")
    _ok(colors[1] != colors[2],
        "yet they do not come out the same colour",
        f"{colors[1]} / {colors[2]}")
    _ok(w._colorbar_info is None, "and no colour bar over a zero-wide range")
    _ok("pinned" in w._filter_status_line(),
        "the status line explains the fallback", w._filter_status_line())


# ── I. persistence and re-analysis ────────────────────────────────────────────
def test_persistence_and_reanalysis():
    print("\ntest_persistence_and_reanalysis")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    tmp = os.path.join(tempfile.mkdtemp(prefix="shotfilter_"), "shot_filter.json")
    real_path = sp_t._shot_filter_config_path
    sp_t._shot_filter_config_path = lambda: tmp
    try:
        w._filter_conds = [
            {"label": "GDD", "channel": GDD, "value": 24700.0, "tol": 5.0, "on": True},
            {"label": "TOD", "channel": TOD, "value": None, "tol": 0.0, "on": False},
        ]
        w._filter_on = True
        w._save_shot_filter()
        w2 = _widget()
        w2._load_shot_filter()
        _ok(w2._filter_on is True, "the master switch round-trips")
        _ok(len(w2._filter_conds) == 2, "both conditions come back",
            str(len(w2._filter_conds)))
        c = w2._filter_conds[0]
        _ok((c["channel"], c["value"], c["tol"], c["on"]) == (GDD, 24700.0, 5.0, True),
            "with the channel, the value, the tolerance and the tick", str(c))
        _ok(w2._filter_conds[1]["value"] is None,
            "a blank value stays blank and does not become 0")

        os.remove(tmp)
        w3 = _widget()
        w3._load_shot_filter()
        _ok([c["channel"] for c in w3._filter_conds] == [ch for _, ch in sp_t.ORDER_PVS],
            "a missing file gives the three dispersion orders",
            str([c["label"] for c in w3._filter_conds]))
        # The master switch defaults ON with nothing ticked, so typing a value
        # is the only step; an armed filter with no condition keeps every shot.
        _ok(w3._filter_on is True, "the master switch defaults to on")
        _ok(all(not c["on"] for c in w3._filter_conds),
            "but no condition is ticked, so nothing is filtered")
        _ok(w3._active_conditions() == [],
            "and there is no active condition to filter by")

        # A switch the user turned off himself must stay off — the default only
        # applies when the file says nothing.
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"enabled": False, "conditions": []}, f)
        w4 = _widget()
        w4._load_shot_filter()
        _ok(w4._filter_on is False,
            "an explicit off in the file beats the default")
    finally:
        sp_t._shot_filter_config_path = real_path

    # Changing the spectrum channel must leave nothing of the old channel behind.
    times = [T0 + i * SEC for i in range(30)]
    w._regions = [_region(1, times, 810.0, 37, "#1f77b4",
                          {GDD: [(T0 - HOUR, 24700.0)]})]
    _load_time_axis(w, times)
    w._apply_shot_filter()
    w._busy = False
    w._reanalyze_all("test")
    left = [k for k in ("stack_all", "stack_ts_all", "stats_all", "shot_vals",
                        "scalar_series", "filter_mask", "n_all")
            if k in w._regions[0]]
    _ok(not left,
        "re-analysis drops every cached per-shot value with the old shot times",
        str(left))


# ── J. the CSV ────────────────────────────────────────────────────────────────
# ── K. the window is the window ───────────────────────────────────────────────
def test_analysis_clips_to_the_region():
    """The archiver answers every request with one sample BEFORE the start and
    one AT OR AFTER the end, whatever was asked for. MEASURED on the real
    archive: a five-minute window holding 60 shots came back with 62, and a
    window holding none came back with two shots from five hours away. Unclipped,
    a marked stretch is averaged together with a shot from either side of it and
    its count is wrong by two — and with the shot filter on, those two strays are
    counted as "shots taken at GDD 24700" when they were not even in the region.
    """
    print("\ntest_analysis_clips_to_the_region")
    app = QApplication.instance() or QApplication([])
    w = _widget()

    t0 = T0 + 10 * SEC
    t1 = T0 + 20 * SEC
    inside = [T0 + i * SEC for i in range(10, 20)]
    # What the archiver really hands over: the shot before the window, the ten
    # inside it, and the first one at or after its end.
    served = [T0] + inside + [t1 + 5 * SEC]
    stack = _stack(len(served), 810.0, 47)

    calls = {}

    def _fake_waveforms(ch, start, end):
        calls["window"] = (start, end)
        return list(zip(served, stack))

    def _fake_scalars(ch, start, end):
        # Same shape for a scalar: a freebie before, and one past the end.
        return [(T0 - HOUR, 24700.0), (t1 + 5 * SEC, 24800.0)]

    real_w, real_s = sp_t._fetch_waveforms, sp_t._fetch_scalars
    sp_t._fetch_waveforms, sp_t._fetch_scalars = _fake_waveforms, _fake_scalars
    try:
        w._regions = [{"id": 1, "t_start": t0, "t_end": t1, "color": "#1f77b4",
                       "visible": True, "expanded": False,
                       "show_individual": False, "analyzed": False, "n": 0}]
        _load_time_axis(w, inside)
        w._x_data = X
        w._run_analysis()
        for _ in range(200):
            QApplication.processEvents()
            if w._regions[0].get("analyzed"):
                break
    finally:
        sp_t._fetch_waveforms, sp_t._fetch_scalars = real_w, real_s

    r = w._regions[0]
    _ok(r.get("analyzed"), "the analysis finished")
    _ok(r["n_all"] == 10,
        "the two shots from outside the region are not in it", f"n={r.get('n_all')}")
    ts = [int(t) for t in (r.get("stack_ts_all") or [])]
    _ok(ts and min(ts) >= t0 and max(ts) < t1,
        "every timestamp is inside the marked stretch, end exclusive",
        f"{len(ts)} shots")
    _ok(np.allclose(r["stats_all"]["mean"], stack[1:11].mean(axis=0)),
        "so the average is the average of the region's own shots")
    # The scalar SERIES keeps its freebie — that is the value held forward.
    _ok(len((r.get("scalar_series") or {}).get(GDD, [])) == 2,
        "the raw scalar series is kept whole, freebie included")
    _ok(r["orders_all"]["GDD"] == 24700.0,
        "but the window average of GDD does not reach past the end",
        str(r["orders_all"]["GDD"]))
    _ok(all(v == 24700.0 for v in r["shot_vals"][GDD]),
        "and every shot in the region was taken at 24700")


def test_live_never_buffers_a_shot_twice():
    """Consecutive live polls overlap, because each one also returns the sample
    before its start and the first at/after its end. MEASURED by replaying six
    real three-second ticks: 16 buffer entries for 6 shots. Averaging then
    weighted some shots two or three times and the red "newest" curve was often
    not the newest."""
    print("\ntest_live_never_buffers_a_shot_twice")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    w._live = True
    w._sb_live_n.setValue(100)
    w._chk_autofit.setChecked(False)

    shots = [T0 + i * 5 * SEC for i in range(6)]
    arrs = {t: a for t, a in zip(shots, _stack(6, 810.0, 53))}

    def _served(start, end):
        """What the archiver gives for [start, end): the last before, everything
        inside, and the first at or after the end."""
        before = [t for t in shots if t < start]
        inside = [t for t in shots if start <= t < end]
        after = [t for t in shots if t >= end]
        out = (before[-1:] if before else []) + inside + (after[:1] if after else [])
        return [(t, arrs[t]) for t in out]

    now = shots[0] + 2 * SEC
    for _ in range(8):
        w._on_live_y((now, _served(w._live_last_ns or shots[0], now), {}))
        w._live_last_ns = now
        now += 3 * SEC
        QApplication.processEvents()

    ts = [int(t) for t, _ in w._live_buf]
    _ok(len(ts) == len(set(ts)),
        "no shot is in the buffer twice", f"{len(ts)} entries, {len(set(ts))} distinct")
    _ok(ts == sorted(ts), "and they are still in time order")
    _ok(set(ts) <= set(shots), "every entry is a real shot")
    w._live = False


def test_csv_says_it_is_filtered():
    print("\ntest_csv_says_it_is_filtered")
    app = QApplication.instance() or QApplication([])
    w = _widget()
    times = [T0 + i * SEC for i in range(60)]
    series = {GDD: [(T0 - HOUR, 24700.0), (times[20], 24800.0)]}
    w._regions = [_region(1, times, 810.0, 41, "#1f77b4", series)]
    _load_time_axis(w, times)
    _set_show(w, "Every spectrum")
    _set_filter(w, ("GDD", GDD, 24700.0, 0.0))

    path = os.path.join(OUT, "shot_filter.csv")
    analyzed = [(0, w._regions[0])]
    w._export_csv(path, analyzed, [])
    with open(path, encoding="utf-8-sig") as f:
        text = f.read()
    _ok("shot filter active" in text, "the file carries a note about the filter")
    _ok("20 of 60" in text, "which says how many shots it holds")
    rows = list(csv.reader(text.splitlines()[1:], delimiter=";"))
    hdr = next(r for r in rows if r and r[0] == "Spectrum")
    body = rows[rows.index(hdr) + 1]
    _ok(body[4] == "20", "the details row's count is the filtered one", body[4])
    _ok("matched the shot filter" in body[5], "and the Method cell says so", body[5])
    curve_hdr = next(r for r in rows
                     if r and r[0] in ("wavelength_nm", "sample_number"))
    _ok(len(curve_hdr) - 1 == 20,
        "one column per MATCHING shot, and no more", str(len(curve_hdr) - 1))


def main():
    app = QApplication.instance() or QApplication([])
    test_hold_forward()
    test_match_value()
    test_region_rewrite()
    test_lazy_stats_fill_in()
    test_shot_bar_keeps_the_same_shot()
    test_nothing_matches_says_why()
    test_channel_never_recorded()
    test_pending_channel_passes_through()
    test_live_filter()
    test_colour_by_pinned_order()
    test_persistence_and_reanalysis()
    test_analysis_clips_to_the_region()
    test_live_never_buffers_a_shot_twice()
    test_csv_says_it_is_filtered()
    print("\nRESULT:", "FAILURES" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
