"""Offline check of the Pulser Monitor analysis engine.

Builds a synthetic frame sequence out of the real all-alive reference image, so the ROI
geometry, the floor-contrast measurement and the classifier are all exercised on real
pixels — only the failures are scripted.

The cases below are organised around the four things a dark run can be (dropout, fault,
trip, dead) and the four things a stretch without data can be (archive gap, outage, trigger
off, diodes off). Most of them exist because the analyser once got them wrong on a real day:
eleven healthy pulsers reported dead at the moment the array was switched off for the
evening, seventeen "array trips" from about eight, and the day's two genuinely dead pulsers
dated to a mid-morning trip they had nothing to do with.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

APP = Path(r"c:\Users\jan.moucka.eli-laser\OneDrive - ELI ERIC\ELI Beamlines\Python"
           r"\programy\L3-QoL-JanJan\Pulser Monitor")
sys.path.insert(0, str(APP))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pulser_monitor as pm  # noqa: E402

CAM = "PD1M1"
CADENCE_NS = 5_000_000_000     # the real archives run ~5 s apart
T0 = 1_782_281_299_685_534_000

failures = []


def _say(text: str) -> None:
    """Print through whatever the console can encode.

    A Windows console here is cp1250, and the labels and values below legitimately contain
    en dashes and arrows — losing the whole test run to a UnicodeEncodeError in the reporting
    line would be absurd."""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace") + "\n")


def check(label, got, want):
    ok = got == want
    _say(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")
    if not ok:
        failures.append(label)


def load_rois():
    cfg = Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return [pm.RoiDefinition.from_dict(r) for r in data["cameras"][CAM]]


def measure(arr, rois):
    """One frame -> (roi_means, roi_contrasts, frame_mean), same maths as the worker."""
    means, contrasts = [], []
    for r in rois:
        y0, y1 = max(0, r.y), min(arr.shape[0], r.y + r.h)
        x0, x1 = max(0, r.x), min(arr.shape[1], r.x + r.w)
        means.append(float(arr[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0)
        contrasts.append(pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[2])
    return means, contrasts, float(arr[::4, ::4].mean())


def blank_frame(shape, rng):
    """A frame with the diodes off, as the archiver really writes it.

    Not a dark frame: the camera's gain lifts pure sensor noise to a mid-grey, measured
    on 13 Aug at mean 0.36 with p1 0.19 / p99 0.57 and a vertical gradient — brighter
    than three quarters of a running frame. Reproducing that here is the point: a blank
    frame has to be recognised by its LACK OF STRUCTURE, not by being dim."""
    h, w = shape
    grad = np.linspace(0.30, 0.46, h, dtype=np.float32)[:, None]
    return np.clip(rng.normal(0.0, 0.085, (h, w)).astype(np.float32) + grad, 0, 1)


def build(rois, ref, script, rng):
    """script: list of (n_frames, kind, payload). Returns SamplePoints.

    Blank frames are measured once and reused: they differ only by noise, and generating a
    fresh 720x310 random array per frame made the longer scripts here take minutes."""
    samples, t = [], T0
    blank_cache = None
    for n, kind, payload in script:
        for _ in range(n):
            if kind == "blank":
                if blank_cache is None:
                    blank_cache = measure(blank_frame(ref.shape, rng), rois)
                m, c, fm = blank_cache
                m, c = list(m), list(c)
            else:
                arr = ref.copy()
                if kind == "warm":
                    arr *= payload
                elif kind == "dark":
                    for name in payload:
                        r = next(q for q in rois if q.name == name)
                        # A dead pulser's tile falls to the surrounding separator level.
                        ring = pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[1]
                        arr[r.y:r.y + r.h, r.x:r.x + r.w] = ring
                m, c, fm = measure(arr, rois)
            samples.append(pm.SamplePoint(t, None, m, fm, roi_contrasts=c))
            t += CADENCE_NS
    return samples


def kinds(st):
    """Event kinds of one pulser, in order — the whole verdict in one comparable value."""
    return [ev.kind for ev in st.events]


def main():
    global failures
    rois = load_rois()
    ref_path = pm._bundled_ref(CAM, "allgood")
    ref = pm._load_as_float32_gray(ref_path)
    _say(f"reference {ref_path.name}  {ref.shape}  mean={ref.mean():.4f}")

    # Reference levels in the config are the OLD (wrong-scale) ones; re-measure, exactly
    # as the v1->v2 migration does, so the test exercises the corrected pipeline.
    for r in rois:
        t, _ring, c = pm._tile_floor_contrast(ref, r.x, r.y, r.w, r.h)
        r.ref_brightness, r.ref_contrast = t, c

    rng = np.random.default_rng(7)
    victim = "C4"     # a mid-array pulser, well away from the dim left column
    vi = next(i for i, r in enumerate(rois) if r.name == victim)

    # ── Test 1: dropout, fault, and a trip the victim caused ──────────────────
    _say("\nTest 1 — dropout / fault / caused trip / restart")
    samples = build(rois, ref, [
        (20, "ok", None),
        (1,  "dark", [victim]),        # 1 frame -> dropout (a flicker)
        (20, "ok", None),
        (8,  "dark", [victim]),        # 8 frames, array stays up -> fault
        (20, "ok", None),
        (6,  "dark", [victim]),        # dark past the flicker limit -> trip, and the cause
        (25, "blank", None),           # array down 125 s: empty, noise-only frames
        (20, "ok", None),              # switched back on
    ], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    st = {s.name: s for s in an.stats}[victim]
    check("victim event kinds", kinds(st), ["dropout", "fault", "trip"])
    check("victim dropouts", st.dropouts, 1)
    check("victim faults", st.faults, 1)
    check("victim trips", st.trips, 1)
    check("array outages", len(an.outages), 1)
    check("victim caused outages", st.caused_trips, 1)
    check("outage cause names", an.outages[0].cause_names if an.outages else [], [victim])
    check("array restarts", an.array_restarts, 1)
    others = [s.name for s in an.stats
              if s.name != victim and (s.dropouts or s.faults or s.trips or s.is_dead)]
    check("no other pulser flagged", others, [])
    # The dim left column used to be marked dead for a whole day by a global threshold.
    check("nothing dead", [s.name for s in an.stats if s.is_dead], [])
    # Uptime is over ARRAY-UP time now, so all forty share a denominator: the victim was
    # dark for 14 of the 94 frames the array was actually running.
    ups = {round(s.uptime_pct, 1) for s in an.stats if s.name != victim}
    check("healthy pulsers all at 100 %", ups, {100.0})
    check("victim below 100 %", st.uptime_pct < 95.0, True)

    # ── Test 1b: the fault/trip split is decided by the LOOK-AHEAD ────────────
    # Same length of dark run, twice; the only difference is whether the array falls after
    # it. This replaces the old "trip dropout", which called any dark run overlapping an
    # outage a trip and so counted 47 of them on a day with about eight.
    _say("\nTest 1b — same dark run: fault if the array holds, trip if it falls")
    stay = build(rois, ref, [(20, "ok", None), (8, "dark", [victim]), (20, "ok", None)], rng)
    fall = build(rois, ref, [(20, "ok", None), (8, "dark", [victim]),
                             (25, "blank", None), (20, "ok", None)], rng)
    an_s = pm.analyze_camera(rois, stay, pm.AnalysisParams())
    an_f = pm.analyze_camera(rois, fall, pm.AnalysisParams())
    check("array held -> fault", kinds({s.name: s for s in an_s.stats}[victim]), ["fault"])
    check("array fell -> trip", kinds({s.name: s for s in an_f.stats}[victim]), ["trip"])

    # ── Test 2: warm-up ───────────────────────────────────────────────────────
    # The real warm-up level, measured on 2026-08-13/14 across all four cameras, is
    # 0.27-0.35 of normal — well BELOW the OFF threshold, so this is the case that would
    # report the whole array as dropped out if warm-up were not found first.
    _say("\nTest 2 — warm-up (array at the real ~0.28 of normal)")
    warm_factor = 0.28
    warm_arr = ref * warm_factor
    for r in rois:
        t, _ring, c = pm._tile_floor_contrast(warm_arr, r.x, r.y, r.w, r.h)
        r.warm_brightness, r.warm_contrast = t, c
    samples = build(rois, ref, [
        (30, "warm", warm_factor),
        (40, "ok", None),
        (15, "warm", warm_factor),
        (20, "ok", None),
    ], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    check("warm level source", an.warm_level_source, "reference")
    check("warm level ≈ 0.28", round(an.warm_level_used, 2), 0.28)
    check("warm-up windows", len(an.warmup_windows), 2)
    check("warm-up frames", sum(an.frame_warmup), 45)
    check("warm-up is not mistaken for no-data", sum(an.frame_nodata), 0)
    check("warm-up is not an outage", len(an.outages), 0)
    check("no events while warming",
          sum(s.total_off_events for s in an.stats), 0)
    check("nothing dead while warming", [s.name for s in an.stats if s.is_dead], [])
    # 30 + 15 warm frames, each covering one 5 s interval up to the first normal frame.
    check("warm-up total", pm._fmt_dur(an.warmup_total_ns), "3m 45s")
    # Warm-up must be OUTSIDE the uptime denominator: it is its own category, not run time.
    check("array-up time excludes warm-up",
          pm._fmt_dur(an.array_up_ns), pm._fmt_dur(59 * CADENCE_NS))

    # ── Test 2b: nothing is judged inside a warm-up stretch ───────────────────
    # This used to assert the opposite. Judging inside warm-up means dividing each score by
    # the warm ratio (~0.28), i.e. multiplying by ~3.6 — which makes a genuinely dead pulser
    # read as alive. That fake ON is what dated the two real deaths of 4 Aug to a
    # mid-morning trip instead of to the first frame of the day.
    _say("\nTest 2b — warm-up frames are not judged at all")
    samples = build(rois, ref, [(10, "ok", None), (10, "warm", warm_factor)], rng)
    for sp in samples[10:]:           # blank out the victim in the warm frames only
        sp.roi_contrasts[vi] = 0.0
        sp.roi_means[vi] = 0.0
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    st = {s.name: s for s in an.stats}[victim]
    check("warm-up still detected", sum(an.frame_warmup), 10)
    check("no verdict from warm frames", kinds(st), [])
    check("nobody flagged", [s.name for s in an.stats if s.is_dead], [])

    # ── Test 3: a pulser dark for the whole window ────────────────────────────
    _say("\nTest 3 — pulser dark for the whole window is dead")
    p_fast = pm.AnalysisParams(dead_confirm_min=1.0)   # 60 s, so 40 frames suffice
    samples = build(rois, ref, [(40, "dark", [victim])], rng)
    an = pm.analyze_camera(rois, samples, p_fast)
    st = {s.name: s for s in an.stats}[victim]
    check("dead", st.is_dead, True)
    check("dead from the first frame", st.dead_since_ns, an.times_ns[0])
    check("still dark at the end", st.dead_until_ns, None)
    check("only the victim is dead", [s.name for s in an.stats if s.is_dead], [victim])
    # There is no "dead from start" category any more; the timestamp says it.
    check("no dead-from-start attribute", hasattr(st, "dead_from_start"), False)

    # ── Test 3b: a window where the diodes were off the whole time ────────────
    _say("\nTest 3b — window with the array never up")
    samples = build(rois, ref, [(380, "blank", None)], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    check("every frame is no-data", sum(an.frame_nodata), 380)
    check("nothing reported dead", [s.name for s in an.stats if s.is_dead], [])
    check("one stretch covering the window", len(an.trips), 1)
    check("read as diodes off, not an outage", an.trips[0].kind, pm.GAP_OFF)
    check("no outages", len(an.outages), 0)

    # ── Test 4: debounce must not silently delete a flicker ───────────────────
    _say("\nTest 4 — default debounce keeps single-frame flickers")
    samples = build(rois, ref, [(15, "ok", None), (1, "dark", [victim]),
                                (15, "ok", None)], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    check("flicker kept at debounce=1", {s.name: s for s in an.stats}[victim].dropouts, 1)
    an2 = pm.analyze_camera(rois, samples, pm.AnalysisParams(debounce=2))
    check("flicker erased at debounce=2", {s.name: s for s in an2.stats}[victim].dropouts, 0)

    # ── Test 5: a short gap in the archive is not an outage ───────────────────
    # At 3.3 Hz the cadence is 0.30 s, so without a floor in real SECONDS a gap of barely
    # over a second became an "array trip". One day reported 17 of them, and 17 restarts.
    _say("\nTest 5 — a short archive gap is a gap, not an outage")
    samples = build(rois, ref, [(20, "ok", None), (8, "blank", None), (20, "ok", None)], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    check("45 s of nothing is no outage", len(an.outages), 0)
    check("no ArrayTrip emitted at all", len(an.trips), 0)
    check("no restart counted", an.array_restarts, 0)
    check("nobody blamed, nobody dead",
          [s.name for s in an.stats if s.is_dead or s.trips], [])
    # It still has to break the run: joining ON->ON across it would claim the pulsers were
    # observed running through time nobody measured.
    check("the gap is still no-data", sum(an.frame_nodata) > 0, True)

    # ── Test 6: how many recoveries a dark pulser survives ────────────────────
    # The operator often fires several recoveries back to back, so a pulser still dark after
    # the first proves nothing. Only the clock is disabled here, so the count decides.
    _say("\nTest 6 — dead after three failed recoveries, not after two")
    p_rs = pm.AnalysisParams(dead_confirm_min=600.0)   # effectively off
    def outage_script(n_outages):
        script = [(20, "ok", None), (6, "dark", [victim])]
        for _ in range(n_outages):
            script += [(25, "blank", None), (6, "dark", [victim])]
        return script + [(20, "ok", None)]
    an2 = pm.analyze_camera(rois, build(rois, ref, outage_script(2), rng), p_rs)
    an3 = pm.analyze_camera(rois, build(rois, ref, outage_script(3), rng), p_rs)
    st2 = {s.name: s for s in an2.stats}[victim]
    st3 = {s.name: s for s in an3.stats}[victim]
    check("two failed recoveries -> not dead", st2.is_dead, False)
    check("two failed recoveries -> trip", kinds(st2), ["trip"])
    check("three failed recoveries -> dead", st3.is_dead, True)
    check("came back afterwards -> replaced", st3.dead_until_ns is not None, True)
    check("replaced pulser still counted dead", st3.status_str, "dead — replaced")

    # ── Test 7: the 17:07 case — the array going off is not forty deaths ──────
    # The array powers down at the end of the day. During the ramp the weakest pulsers read
    # OFF for about a second, and then the samples stop. The old code called every one of
    # those a death: eleven of them, all stamped with the same minute.
    _say("\nTest 7 — the array being switched off is not a death")
    samples = build(rois, ref, [
        (60, "ok", None),
        (2,  "dark", [victim]),        # the dimming ramp, ~10 s
        (380, "blank", None),          # diodes off for the rest of the window
    ], rng)
    an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
    st = {s.name: s for s in an.stats}[victim]
    check("nothing dead", [s.name for s in an.stats if s.is_dead], [])
    check("the ramp is a dropout", kinds(st), ["dropout"])
    check("the shutdown is diodes off",
          [t.kind for t in an.trips], [pm.GAP_OFF])
    check("not an outage", len(an.outages), 0)
    check("not a restart", an.array_restarts, 0)

    # ── Test 8: telling the deliberate stops apart ───────────────────────────
    _say("\nTest 8 — trigger off vs diodes off vs an outage")
    # Nothing dark first, back within trigger_off_max_s -> we did it.
    trig = build(rois, ref, [(20, "ok", None), (20, "blank", None), (20, "ok", None)], rng)
    an = pm.analyze_camera(rois, trig, pm.AnalysisParams())
    check("100 s, nothing dark first -> trigger off",
          [t.kind for t in an.trips], [pm.GAP_TRIGGER])
    check("trigger off is not an outage", len(an.outages), 0)
    check("trigger off is not a restart", an.array_restarts, 0)
    # Same stretch, but a pulser went dark right before it -> the array fell over.
    fell = build(rois, ref, [(20, "ok", None), (6, "dark", [victim]),
                             (20, "blank", None), (20, "ok", None)], rng)
    an = pm.analyze_camera(rois, fell, pm.AnalysisParams())
    check("same stretch after a dark pulser -> outage",
          [t.kind for t in an.trips], [pm.GAP_TRIP])
    check("counted as an outage", len(an.outages), 1)
    # An outage nobody cleared is split: the first 30 min is the outage, the rest is off.
    long_fell = build(rois, ref, [(20, "ok", None), (6, "dark", [victim]),
                                  (500, "blank", None), (20, "ok", None)], rng)
    an = pm.analyze_camera(rois, long_fell, pm.AnalysisParams())
    check("an uncleared outage is split",
          [t.kind for t in an.trips], [pm.GAP_TRIP, pm.GAP_OFF])
    check("the outage half is capped at 30 min",
          pm._fmt_dur(an.trips[0].duration_ns), "30m 0s")

    _say("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
