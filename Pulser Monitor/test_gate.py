"""Offline check of the Pulser Monitor active-time gate.

Three layers, cheapest first:
  1. the span algebra on its own (pure integers, no images),
  2. the gate through the real analysis engine, on synthetic frames built from the real
     all-alive reference image — the case that matters is an overnight window, which
     ungated reports the night as an array trip and every pulser as dead,
  3. an optional live query for L3-SIS-KEY:HighPowerEnable (skipped off-site).

Run: python test_gate.py          (add --live to include layer 3)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pulser_monitor as pm  # noqa: E402

CAM = "PD1M1"
CADENCE_NS = 5_000_000_000
S = pm._NS_PER_S
H = 3600 * S

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def check_true(label, got):
    check(label, bool(got), True)


# ── layer 1: span algebra ─────────────────────────────────────────────────────

def test_span_algebra():
    print("\nSpan algebra")
    check("merge overlapping", pm._merge_spans([(0, 10), (5, 20), (30, 40)]),
          [(0, 20), (30, 40)])
    check("merge touching", pm._merge_spans([(0, 10), (10, 20)]), [(0, 20)])
    check("merge drops empty", pm._merge_spans([(5, 5), (7, 3), (1, 2)]), [(1, 2)])
    check("clip", pm._clip_spans([(0, 10), (20, 30)], 5, 25), [(5, 10), (20, 25)])
    check("invert", pm._invert_spans([(10, 20)], 0, 30), [(0, 10), (20, 30)])
    check("invert full", pm._invert_spans([(0, 30)], 0, 30), [])
    check("invert empty", pm._invert_spans([], 0, 30), [(0, 30)])
    check("intersect", pm._intersect_spans([(0, 10), (20, 30)], [(5, 25)]),
          [(5, 10), (20, 25)])
    check("intersect disjoint", pm._intersect_spans([(0, 5)], [(10, 15)]), [])

    ss = pm._SpanSet([(100, 200), (300, 400)])
    check("total", ss.total_ns, 200)
    check("len", len(ss), 2)
    check("contains inside", ss.contains(150), True)
    check("contains start (inclusive)", ss.contains(100), True)
    check("contains end (exclusive)", ss.contains(200), False)
    check("contains between", ss.contains(250), False)
    check("overlap spanning both", ss.overlap_ns(0, 500), 200)
    check("overlap partial", ss.overlap_ns(150, 350), 100)
    check("overlap none", ss.overlap_ns(200, 300), 0)
    check("clip_back inside", ss.clip_back(150), 100)
    check("clip_back outside", ss.clip_back(250), 250)
    check("empty set is falsy", bool(pm._SpanSet()), False)


def test_daytime_spans():
    print("\nDaytime spans (Prague)")
    tz = pm.TZ_PRAGUE or timezone.utc
    # A three-day window starting at midnight: expect one 14 h span per day.
    start = datetime(2026, 8, 10, 0, 0, tzinfo=tz)
    end = datetime(2026, 8, 13, 0, 0, tzinfo=tz)
    spans = pm.daytime_spans(int(start.timestamp()) * S, int(end.timestamp()) * S)
    check("one span per day", len(spans), 3)
    check("each is 14 h", sorted({b - a for a, b in spans}), [14 * H])
    firsts = [datetime.fromtimestamp(a / 1e9, tz=tz) for a, _ in spans]
    check("all start at 07:00", sorted({d.hour for d in firsts}), [7])
    lasts = [datetime.fromtimestamp(b / 1e9, tz=tz) for _, b in spans]
    check("all end at 21:00", sorted({d.hour for d in lasts}), [21])

    # Clipped to a window that opens mid-morning and closes mid-afternoon.
    s2 = int(datetime(2026, 8, 10, 9, 0, tzinfo=tz).timestamp()) * S
    e2 = int(datetime(2026, 8, 10, 15, 0, tzinfo=tz).timestamp()) * S
    check("clipped to window", pm.daytime_spans(s2, e2), [(s2, e2)])

    # DST: the boundaries must stay on the wall clock, not drift by an hour.
    s3 = int(datetime(2026, 3, 28, 0, 0, tzinfo=tz).timestamp()) * S
    e3 = int(datetime(2026, 3, 31, 0, 0, tzinfo=tz).timestamp()) * S
    dst = pm.daytime_spans(s3, e3)
    hours = {datetime.fromtimestamp(a / 1e9, tz=tz).hour for a, _ in dst}
    check("07:00 local across the DST change", sorted(hours), [7])

    check("end <= start gates everything away",
          pm.daytime_spans(s2, e2, 21, 7), [])
    check("hour_end=24 reaches midnight",
          len(pm.daytime_spans(int(start.timestamp()) * S,
                               int(end.timestamp()) * S, 0, 24)), 3)


def test_active_spans_hours_only():
    print("\nactive_spans, hour rule only")
    tz = pm.TZ_PRAGUE or timezone.utc
    s = int(datetime(2026, 8, 10, 0, 0, tzinfo=tz).timestamp()) * S
    e = int(datetime(2026, 8, 12, 0, 0, tzinfo=tz).timestamp()) * S
    active, excluded, notes, failed = pm.active_spans(
        s, e, gate_hours=True, gate_high_power=False)
    check("hpe not attempted", failed, False)
    check("active = 2 x 14 h", active.total_ns, 2 * 14 * H)
    check("excluded = 48 h - 28 h", excluded.total_ns, (48 - 28) * H)
    check("active + excluded = window", active.total_ns + excluded.total_ns, e - s)
    check("one note", len(notes), 1)


# ── layer 2: the gate through the analysis engine ─────────────────────────────

def load_rois():
    cfg = Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return [pm.RoiDefinition.from_dict(r) for r in data["cameras"][CAM]]


def measure(arr, rois):
    means, contrasts = [], []
    for r in rois:
        y0, y1 = max(0, r.y), min(arr.shape[0], r.y + r.h)
        x0, x1 = max(0, r.x), min(arr.shape[1], r.x + r.w)
        means.append(float(arr[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0)
        contrasts.append(pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[2])
    return means, contrasts, float(arr[::4, ::4].mean())


def blank_frame(shape, rng):
    """The diodes off, as the archiver really writes it: structureless mid-grey noise."""
    h, w = shape
    grad = np.linspace(0.30, 0.46, h, dtype=np.float32)[:, None]
    return np.clip(rng.normal(0.0, 0.085, (h, w)).astype(np.float32) + grad, 0, 1)


def build_at(rois, ref, script, rng, t_start, cadence_ns=CADENCE_NS):
    """script: [(n_frames, kind, payload)] laid down from t_start at `cadence_ns`."""
    samples, t = [], t_start
    for n, kind, payload in script:
        for _ in range(n):
            if kind == "blank":
                arr = blank_frame(ref.shape, rng)
            else:
                arr = ref.copy()
                if kind == "dark":
                    for name in payload:
                        r = next(q for q in rois if q.name == name)
                        ring = pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[1]
                        arr[r.y:r.y + r.h, r.x:r.x + r.w] = ring
            m, c, fm = measure(arr, rois)
            samples.append(pm.SamplePoint(t, None, m, fm, roi_contrasts=c))
            t += cadence_ns
    return samples


def test_overnight(rois, ref, rng):
    """The case the gate exists for: a window covering an evening, a night and a morning.

    Ungated, the night's blank frames are one long array outage. Gated, the night is not
    judged at all — but the real dropout in the morning still is."""
    print("\nOvernight window (the reason the gate exists)")
    tz = pm.TZ_PRAGUE or timezone.utc
    victim = "C4"

    # A 1-minute cadence, not the real 5 s: the frames have to FILL the analysed hours,
    # or the empty part of them is a genuine no-images outage and the gate is not what is
    # being measured. At 5 s that would be 4300 frames of real pixel work per run.
    cad = 60 * S
    win_start = int(datetime(2026, 8, 10, 19, 0, tzinfo=tz).timestamp()) * S
    win_end = int(datetime(2026, 8, 11, 11, 0, tzinfo=tz).timestamp()) * S

    # 19:00→21:00 running (the analysed part of the evening), the diodes off overnight,
    # then 07:00→11:00 running with a genuine 6-frame dropout mid-morning.
    evening = build_at(rois, ref, [(120, "ok", None)], rng, win_start, cad)
    night_t0 = int(datetime(2026, 8, 10, 22, 0, tzinfo=tz).timestamp()) * S
    night = build_at(rois, ref, [(60, "blank", None)], rng, night_t0, cad)
    morn_t0 = int(datetime(2026, 8, 11, 7, 0, tzinfo=tz).timestamp()) * S
    morning = build_at(rois, ref, [
        (117, "ok", None),
        (6, "dark", [victim]),      # 09:57–10:02, a real dropout inside the working day
        (117, "ok", None),
    ], rng, morn_t0, cad)
    samples = evening + night + morning

    # Two param sets, as the app builds them: the gate flags are the RECORD of what was
    # asked for, and the spans are what was applied. They have to agree.
    params = pm.AnalysisParams()
    gated_params = pm.AnalysisParams(gate_hours=True, gate_high_power=False)
    un = pm.analyze_camera(rois, samples, params)
    st_un = {s.name: s for s in un.stats}[victim]
    print(f"    ungated: {len(un.trips)} trip(s), "
          f"{sum(s.faults for s in un.stats)} fault(s), "
          f"{sum(1 for s in un.stats if s.is_dead)} dead")
    check_true("ungated: the night shows up at all", len(un.trips) >= 1)
    check("ungated: and it reads as the diodes being off",
          [t.kind for t in un.trips], [pm.GAP_OFF])
    check("ungated: victim fault still found", st_un.faults, 1)

    active, excluded, notes, failed = pm.active_spans(
        win_start, win_end, gate_hours=True, gate_high_power=False)
    print(f"    gate: {notes}")
    # 19:00-21:00 on the 10th + 07:00-11:00 on the 11th = 6 h observed of a 16 h window.
    check("gated: observed time", active.total_ns, 6 * H)

    # Only the frames the file scanner would have kept reach the analyser.
    kept = [sp for sp in samples if active.contains(sp.ts_ns)]
    check("gated: frames kept", len(kept), 120 + 240)
    check("gated: every night frame dropped", len(samples) - len(kept), 60)
    check_true("gated: no night frame survived",
               all(not excluded.contains(sp.ts_ns) for sp in kept))

    an = pm.analyze_camera(rois, kept, gated_params, excluded=excluded,
                           gate_notes=notes, gate_hpe_failed=failed)
    st = {s.name: s for s in an.stats}[victim]
    print(f"    gated:   {len(an.trips)} trip(s), "
          f"{sum(s.faults for s in an.stats)} fault(s), "
          f"{sum(1 for s in an.stats if s.is_dead)} dead")
    check("gated: the night is NOT an array trip", len(an.trips), 0)
    check("gated: the real fault survives", st.faults, 1)
    check("gated: nobody is dead", [s.name for s in an.stats if s.is_dead], [])
    check("gated: excluded time recorded", an.excluded_total_ns, excluded.total_ns)
    check_true("gated: the gate is reported in the warnings",
               any("Active-time gate" in w for w in an.warnings))
    check_true("gated: some frames flagged excluded", any(an.frame_excluded))

    # The overnight break must not be credited to anyone as a run or an outage.
    # The exported record of what the bundle covers.
    import csv
    import tempfile
    gate_csv = Path(tempfile.mkdtemp()) / "pulser_analysed_time.csv"
    pm._write_gate_csv(str(gate_csv), win_start, win_end, an)
    rows = list(csv.reader(gate_csv.read_text(encoding="utf-8").splitlines()))
    kv = {r[0]: r[1] for r in rows if len(r) == 2}
    check("gate CSV: gated flag", kv.get("gated"), "1")
    check("gate CSV: excluded seconds", kv.get("excluded_s"), str(10 * 3600))
    check("gate CSV: observed seconds", kv.get("observed_s"),
          str(an.observed_total_ns // S))
    check("gate CSV: hpe failure flag", kv.get("high_power_gating_failed"), "0")
    check("gate CSV: hour rule recorded",
          (kv.get("rule_hours"), kv.get("rule_hours_from"), kv.get("rule_hours_to")),
          ("1", "7", "21"))
    check("gate CSV: one excluded span row",
          sum(1 for r in rows if len(r) == 3 and r[2].isdigit()), 1)
    check_true("gate CSV: notes carried", any(r[0] == "note" for r in rows if r))

    check_true("gated: longest run never spans the night",
               all(s.longest_run_ns <= 6 * H for s in an.stats))
    check_true("gated: span excludes the night",
               all(s.span_ns <= 6 * H for s in an.stats))
    check_true("gated: downtime excludes the night",
               all(s.total_down_ns < 1 * H for s in an.stats))
    check("gated: excluded frames not counted as observed",
          [s.frames for s in an.stats][0], sum(1 for e in an.frame_excluded if not e))


def test_trip_inside_hours(rois, ref, rng):
    """A genuine outage during working hours must still be found with the gate on.

    "Genuine" now means a pulser went dark right before the array stopped. That is what
    separates the array falling over from us stopping it: with nothing dark beforehand and
    the data back within minutes, the honest reading is that we switched the trigger off,
    and calling it an outage would put our own actions in the fault log."""
    print("\nReal trip inside working hours (gate must not hide it)")
    tz = pm.TZ_PRAGUE or timezone.utc
    victim = "C4"
    win_start = int(datetime(2026, 8, 10, 9, 0, tzinfo=tz).timestamp()) * S
    win_end = int(datetime(2026, 8, 10, 17, 0, tzinfo=tz).timestamp()) * S
    samples = build_at(rois, ref, [
        (40, "ok", None),
        (5, "dark", [victim]),    # the pulser that takes the array down
        (25, "blank", None),      # a real outage, 10:xx, well inside the working day
        (40, "ok", None),
    ], rng, win_start)
    active, excluded, notes, _f = pm.active_spans(
        win_start, win_end, gate_hours=True, gate_high_power=False)
    check("whole window is inside the hours", excluded.total_ns, 0)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams(),
                           excluded=excluded, gate_notes=notes)
    check("the real outage is still detected", len(an.outages), 1)
    check("and it restarted", an.array_restarts, 1)
    check("the culprit is named", an.outages[0].cause_names, [victim])

    # Same stretch, nobody dark first: ours, not the array's.
    ours = build_at(rois, ref, [(40, "ok", None), (25, "blank", None),
                                (40, "ok", None)], rng, win_start)
    an2 = pm.analyze_camera(rois, ours, pm.AnalysisParams(),
                            excluded=excluded, gate_notes=notes)
    check("an unexplained short stop is not an outage", len(an2.outages), 0)
    check("it is recorded as a trigger-off", [t.kind for t in an2.trips], [pm.GAP_TRIGGER])
    check("and costs nobody a recovery", an2.array_restarts, 0)


def test_fully_excluded(rois, ref, rng):
    """A window entirely outside the hours must report nothing, not 0 % uptime."""
    print("\nWindow entirely outside the analysed hours")
    tz = pm.TZ_PRAGUE or timezone.utc
    win_start = int(datetime(2026, 8, 10, 22, 0, tzinfo=tz).timestamp()) * S
    win_end = int(datetime(2026, 8, 11, 4, 0, tzinfo=tz).timestamp()) * S
    samples = build_at(rois, ref, [(60, "ok", None)], rng, win_start)
    active, excluded, notes, _f = pm.active_spans(
        win_start, win_end, gate_hours=True, gate_high_power=False)
    check("nothing active", bool(active), False)
    check("everything excluded", excluded.total_ns, win_end - win_start)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams(),
                           excluded=excluded, gate_notes=notes)
    check("no pulser reports frames", sorted({s.frames for s in an.stats}), [0])
    check("no pulser reports uptime", sorted({s.uptime_pct for s in an.stats}), [0.0])
    check("nobody is called dead", [s.name for s in an.stats if s.is_dead], [])
    check("no trips invented", len(an.trips), 0)


# ── layer 2b: the panel controls ──────────────────────────────────────────────

def test_gui_controls():
    """The gate's controls and what they hand to the analyser."""
    print("\nPanel controls")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    w = pm.PulserMonitorWidget()

    check("hour gate on by default", w._gate_hours_chk.isChecked(), True)
    check("high-power gate on by default", w._gate_hpe_chk.isChecked(), True)
    check("default hours", (w._gate_h0_sb.value(), w._gate_h1_sb.value()),
          (pm.GATE_HOUR_START, pm.GATE_HOUR_END))
    check("checkbox names the hours", w._gate_hours_chk.text(),
          f"Only {pm.GATE_HOUR_START:02d}:00–{pm.GATE_HOUR_END:02d}:00 (Prague)")

    p = w._analysis_params()
    check("params record the hour gate", p.gate_hours, True)
    check("params record the high-power gate", p.gate_high_power, True)
    check("params record the hours", (p.gate_hour_start, p.gate_hour_end),
          (pm.GATE_HOUR_START, pm.GATE_HOUR_END))

    # The label has to follow the spin boxes, or it keeps advertising 07:00–21:00.
    w._gate_h0_sb.setValue(6)
    w._gate_h1_sb.setValue(22)
    check("label follows the spin boxes", w._gate_hours_chk.text(),
          "Only 06:00–22:00 (Prague)")
    check("params follow the spin boxes",
          (w._analysis_params().gate_hour_start, w._analysis_params().gate_hour_end),
          (6, 22))

    # end must stay above start, in both directions, or the gate removes everything.
    w._gate_h0_sb.setValue(23)
    check("raising start pushes end up", w._gate_h1_sb.value() > 23, True)
    w._gate_h1_sb.setValue(5)
    check("lowering end pulls start down", w._gate_h0_sb.value() < 5, True)

    w._gate_hours_chk.setChecked(False)
    check("hour spin boxes disabled with the rule", w._gate_h0_sb.isEnabled(), False)
    check("label drops the hours when off", w._gate_hours_chk.text(),
          "Only these hours (Prague)")
    check("params follow the checkbox", w._analysis_params().gate_hours, False)

    # A gate worker with only the hour rule needs no network, so it can be run here.
    tz = pm.TZ_PRAGUE or timezone.utc
    s = int(datetime(2026, 8, 10, 0, 0, tzinfo=tz).timestamp()) * S
    e = int(datetime(2026, 8, 11, 0, 0, tzinfo=tz).timestamp()) * S
    got = {}
    sig = pm._GateSignals()
    sig.ready.connect(lambda a, x, n, f: got.update(
        active=a, excluded=x, notes=n, failed=f))
    pm._GateWorker(sig, s, e, True, False, 7, 21, [0], 0).run()
    app.processEvents()
    check("worker emitted", sorted(got), ["active", "excluded", "failed", "notes"])
    check("worker active time", got["active"].total_ns, 14 * H)
    check("worker excluded time", got["excluded"].total_ns, 10 * H)
    check("worker reports no failure", got["failed"], False)

    # A stale generation must be dropped — otherwise a cancelled scan's gate would
    # arrive after the next one had already started and silently replace it.
    got.clear()
    pm._GateWorker(sig, s, e, True, False, 7, 21, [5], 4).run()
    app.processEvents()
    check("stale generation ignored", got, {})

    w.deleteLater()


# ── layer 3: live archiver ────────────────────────────────────────────────────

def test_live_high_power():
    print("\nLive archiver: " + pm.HPE_CHANNEL)
    tz = pm.TZ_PRAGUE or timezone.utc
    now = datetime.now(tz)
    start = now - timedelta(days=7)
    s_ns, e_ns = int(start.timestamp()) * S, int(now.timestamp()) * S
    try:
        spans, note = pm.high_power_spans(s_ns, e_ns)
    except Exception as exc:
        print(f"  SKIP  archiver unreachable ({exc})")
        return
    print(f"    {note}")
    check_true("spans are inside the window",
               all(s_ns <= a < b <= e_ns for a, b in spans))
    check("spans are merged and sorted", spans, pm._merge_spans(spans))
    on = pm._SpanSet(spans)
    check_true("on-time is at most the window", on.total_ns <= e_ns - s_ns)
    active, excluded, notes, failed = pm.active_spans(
        s_ns, e_ns, gate_hours=True, gate_high_power=True)
    check("high-power gating succeeded", failed, False)
    check("gate has both notes", len(notes), 2)
    check_true("gated time is a subset of the hour rule",
               active.total_ns <= pm._SpanSet(
                   pm.daytime_spans(s_ns, e_ns)).total_ns)
    check("active + excluded = window", active.total_ns + excluded.total_ns, e_ns - s_ns)
    print(f"    last 7 days: {pm._fmt_dur(active.total_ns)} would be analysed, "
          f"{pm._fmt_dur(excluded.total_ns)} left out")


def main():
    test_span_algebra()
    test_daytime_spans()
    test_active_spans_hours_only()

    rois = load_rois()
    ref = pm._load_as_float32_gray(pm._bundled_ref(CAM, "allgood"))
    for r in rois:
        t, _ring, c = pm._tile_floor_contrast(ref, r.x, r.y, r.w, r.h)
        r.ref_brightness, r.ref_contrast = t, c
    rng = np.random.default_rng(11)

    test_overnight(rois, ref, rng)
    test_trip_inside_hours(rois, ref, rng)
    test_fully_excluded(rois, ref, rng)
    test_gui_controls()

    if "--live" in sys.argv:
        test_live_high_power()
    else:
        print("\n(skipping the live archiver check — pass --live to include it)")

    print("\n" + ("ALL PASS" if not failures
                  else f"{len(failures)} FAILURE(S): " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
