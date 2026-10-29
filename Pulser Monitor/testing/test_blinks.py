"""Blink counting at full power — the full-power gate, the reading plan and the
start-of-stretch scoring, on synthetic frames cut out of the real all-alive reference.

What is scripted here is what the real days of 20-22 Oct 2026 showed:
  - a pulser going out for one frame, and for three, at full power — each one blink;
  - a pulser only dimming to ~0.65 of its level — a PARTIAL blink;
  - its neighbour dimming a little through the optical coupling — nothing;
  - the whole array dipping for one frame — the array, not a pulser;
  - the low-power mode the dump bus cannot see — not judged at all;
  - the ramps either end of a full-power stretch — not judged;
  - a pulser dark from the first frame — dead, and still the same death after the
    lunch-time switch-off, however short the afternoon stretch;
  - an energy dip with no pulser dark in it — reported as unexplained.

Run from the program folder:  python testing/test_blinks.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent
sys.path.insert(0, str(APP))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as pm  # noqa: E402

CAM = "PD1M1"
S = pm._NS_PER_S
T0 = 1_782_281_299_000_000_000
failures = []


def _say(text: str) -> None:
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
    means, contrasts = [], []
    for r in rois:
        y0, y1 = max(0, r.y), min(arr.shape[0], r.y + r.h)
        x0, x1 = max(0, r.x), min(arr.shape[1], r.x + r.w)
        means.append(float(arr[y0:y1, x0:x1].mean()) if (x1 > x0 and y1 > y0) else 0.0)
        contrasts.append(pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[2])
    return means, contrasts, float(arr[::4, ::4].mean())


class Frames:
    """Builds frames at a 1 s cadence. Each frame is the reference with scripted changes;
    identical frames are measured once."""

    def __init__(self, rois, ref):
        self.rois, self.ref = rois, ref
        self.by_name = {r.name: r for r in rois}
        self.cache = {}

    def frame(self, dark=(), dim=None, scale=1.0, always_dark=()):
        key = (tuple(sorted(dark)), tuple(sorted((dim or {}).items())), scale,
               tuple(sorted(always_dark)))
        if key not in self.cache:
            arr = self.ref.copy()
            for name in tuple(dark) + tuple(always_dark):
                r = self.by_name[name]
                floor = pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[1]
                arr[r.y:r.y + r.h, r.x:r.x + r.w] = floor
            for name, f in (dim or {}).items():
                r = self.by_name[name]
                floor = pm._tile_floor_contrast(self.ref, r.x, r.y, r.w, r.h)[1]
                tile = arr[r.y:r.y + r.h, r.x:r.x + r.w]
                arr[r.y:r.y + r.h, r.x:r.x + r.w] = floor + f * (tile - floor)
            arr = arr * scale
            self.cache[key] = measure(arr, self.rois)
        return self.cache[key]

    def samples(self, script):
        """script: {second: kwargs}, plus 'range' entries (a, b, kwargs). Returns
        SamplePoints for every second listed by `seconds`."""
        out = []
        for sec, kw in script:
            m, c, fm = self.frame(**kw)
            out.append(pm.SamplePoint(T0 + sec * S, None, list(m), fm,
                                      roi_contrasts=list(c)))
        return out


def neighbour_of(rois, name):
    """A pulser in the same row, one column over."""
    col, row = name[0], name[1:]
    for r in rois:
        if r.name[1:] == row and r.name[0] != col and abs(ord(r.name[0]) - ord(col)) == 1:
            return r.name
    return None


def test_power_spans():
    _say("\nFull-power stretches from the dump bus")
    # 0.3 s samples: 80 V warm-up, then full power with one low ripple sample, then off.
    samples = []
    t = 0
    for _ in range(200):          # 60 s warming
        samples.append((t, 80.0))
        t += 300_000_000
    for i in range(1000):         # 300 s at full power, one ripple dip to 85 V
        samples.append((t, 85.0 if i == 500 else 99.0))
        t += 300_000_000
    for _ in range(100):
        samples.append((t, 1.0))
        t += 300_000_000
    merge = int(pm._POWER_MERGE_S * S)
    full = pm._spans_where(samples, lambda v: v >= pm.FULL_POWER_V, merge,
                           int(pm._POWER_MIN_SPAN_S * S))
    lit = pm._spans_where(samples, lambda v: v >= pm.LIT_POWER_V, merge, 0)
    check("one full-power stretch despite a ripple sample", len(full), 1)
    check("it starts when 88 V is reached", full[0][0], 200 * 300_000_000)
    check("lit time includes the warm-up", lit[0][0], 0)
    short = pm._spans_where(samples[:300], lambda v: v >= pm.FULL_POWER_V, merge,
                            int(pm._POWER_MIN_SPAN_S * S))
    check("a stretch under a minute is not a stretch", short, [])


def test_energy():
    _say("\nEnergy: dips and the low-power mode")
    n = 600
    et = np.arange(n, dtype=np.int64) * 300_000_000
    ev = np.full(n, 1.0) + 0.0005 * np.sin(np.arange(n))
    ev[100] = 0.965                      # a full blink: -3.5 %
    ev[300] = 0.995                      # a partial: -0.5 %
    ev[400:460] = 0.25                   # the low-power mode
    dev = pm._running_dev(ev)
    spans = [(0, int(et[-1]))]
    dips = pm._energy_dips(et, dev, spans, -0.0035)
    starts = [int(d[0] // 300_000_000) for d in dips]
    check("full blink found", 100 in starts, True)
    check("partial blink found", 300 in starts, True)
    low = pm._energy_low_spans(et, ev, spans, 0.80)
    check("low-power stretch found", len(low), 1)
    check("low-power stretch placed", (low[0][0] // 300_000_000, low[0][1] // 300_000_000),
          (400, 459))


def test_reading_plan():
    _say("\nWhich frames are read")
    files = [(T0 + i * 300_000_000, f"f{i}") for i in range(4000)]   # 20 min at 0.3 s
    full = (T0 + 60 * S, T0 + 1140 * S)
    cp = pm.CamPower(cam=CAM, full_spans=[full], lit_spans=[(T0, T0 + 1140 * S)],
                     judged_spans=[(full[0] + 30 * S, full[1] - 15 * S)],
                     has_energy=True, dips=[(T0 + 600 * S, T0 + 600 * S, -0.03)])
    p = pm.AnalysisParams()
    kept, _note = pm.plan_reading(files, cp, p)
    ts = [t for t, _p in kept]
    near = [t for t in ts if abs(t - (T0 + 600 * S)) <= int(1.5 * S)]
    check("every frame within 1.5 s of the dip is read", len(near), 11)
    check("nothing after the diodes went off", max(ts) < T0 + 1140 * S, True)
    tail = [t for t in ts if T0 + 1110 * S <= t < T0 + 1140 * S]
    check("every frame in the last 30 s before the stop is read", len(tail), 100)
    check("about one frame per 5 s elsewhere",
          200 <= len(ts) - len(near) - len(tail) <= 240, True)
    cp.has_energy = False
    kept2, _ = pm.plan_reading(files, cp, p)
    inside = [t for t, _p in files if cp.judged_spans[0][0] <= t < cp.judged_spans[0][1]]
    check("no energy: every frame at full power is read",
          len([t for t, _p in kept2 if cp.judged_spans[0][0] <= t < cp.judged_spans[0][1]]),
          len(inside))
    p.energy_guided = False
    cp.has_energy = True
    kept3, _ = pm.plan_reading(files, cp, p)
    check("guided reading off: every frame at full power is read",
          len([t for t, _p in kept3 if cp.judged_spans[0][0] <= t < cp.judged_spans[0][1]]),
          len(inside))


VICTIM, PARTIAL, DEAD = "C4", "B6", "D2"


def build_day(rois, ref):
    """(samples, analysis, low-power spans, power) for the scripted day below."""
    fr = Frames(rois, ref)
    victim, partial, dead = VICTIM, PARTIAL, DEAD
    nb = neighbour_of(rois, victim)
    seconds = []
    # Stretch 1 — dump bus full from 20 s to 320 s, judged 50 s .. 305 s.
    for s in range(0, 20):                             # warm-up
        seconds.append((s, dict(scale=0.28, always_dark=(dead,))))
    for s in range(20, 320):
        kw = dict(always_dark=(dead,))
        if s == 30:
            kw["dark"] = (victim,)                     # in the ramp: not judged
        elif s == 120 or 140 <= s <= 142:
            kw["dark"] = (victim,)                     # a 1-frame and a 3-frame blink
        elif s == 160:
            kw["dim"] = {partial: 0.62}                # a partial blink
        elif s == 180 and nb:
            kw["dim"] = {nb: 0.92}                     # optical coupling — nothing
        elif s == 200:
            kw["scale"] = 0.70                         # the whole array dips
        elif 220 <= s <= 229:
            kw["scale"] = 0.30                         # low-power mode
            kw["dark"] = (victim,)                     # ...and nothing in it counts
        seconds.append((s, kw))
    # 40 min with the diodes off, then stretch 2 — full from 2720 s to 3020 s.
    for s in range(2700, 3020):
        kw = dict(always_dark=(dead,))
        if s < 2720:
            kw["scale"] = 0.28
        elif s == 2900:
            kw["dark"] = (victim,)
        seconds.append((s, kw))
    samples = fr.samples(seconds)

    full = [(T0 + 20 * S, T0 + 320 * S), (T0 + 2720 * S, T0 + 3020 * S)]
    p = pm.AnalysisParams(dead_confirm_min=3.0)
    cp = pm.CamPower(
        cam=CAM, full_spans=full, lit_spans=[(T0, T0 + 320 * S), (T0 + 2700 * S, T0 + 3020 * S)],
        judged_spans=[(a + int(p.edge_start_s * S), b - int(p.edge_end_s * S)) for a, b in full],
        has_energy=True,
        dips=[(T0 + 120 * S, T0 + 120 * S, -0.035), (T0 + 140 * S, T0 + 142 * S, -0.04),
              (T0 + 160 * S, T0 + 160 * S, -0.004), (T0 + 250 * S, T0 + 250 * S, -0.012),
              (T0 + 2900 * S, T0 + 2900 * S, -0.035)])
    low = [(T0 + 220 * S, T0 + 229 * S)]
    an = pm.analyze_camera(rois, samples, p, power=cp, low_power=low)
    return samples, an, full


def test_day(rois, ref):
    _say("\nA day at full power: two stretches with the diodes off in between")
    victim, partial, dead = VICTIM, PARTIAL, DEAD
    nb = neighbour_of(rois, victim)
    _samples, an, full = build_day(rois, ref)
    by = {s.name: s for s in an.stats}

    check("two stretches", len(an.stretches), 2)
    check("both judged", [s.judged for s in an.stretches], [True, True])
    check("victim: three blinks, none partial", (by[victim].blinks, by[victim].partial_blinks),
          (3, 0))
    check("victim's blinks at 120, 140, 2900 s",
          [int((ev.start_ns - T0) // S) for ev in by[victim].events], [120, 140, 2900])
    check("the 3-frame blink lasted three frames",
          [ev.dark_frames for ev in by[victim].events], [1, 3, 1])
    check("partial blink counted as partial",
          (by[partial].blinks, by[partial].partial_blinks), (1, 1))
    pe = by[partial].events[0]
    check("its lowest score is the dimming", 0.55 <= pe.min_score < 0.80, True)
    if nb:
        check("neighbour dimming through the coupling is no blink", by[nb].blinks, 0)
    others = sorted(s.name for s in an.stats
                    if s.blinks and s.name not in (victim, partial))
    check("nobody else blinked (array dip, low power, ramp)", others, [])
    i200 = an.times_ns.index(T0 + 200 * S)
    i225 = an.times_ns.index(T0 + 225 * S)
    i30 = an.times_ns.index(T0 + 30 * S)
    check("whole-array dip frame not judged", an.frame_judge[i200], False)
    check("low-power frame not judged", an.frame_judge[i225], False)
    check("ramp frame not judged", an.frame_judge[i30], False)
    check("all three shown as below full power",
          [an.frame_below_full[i] for i in (i200, i225, i30)], [True, True, True])
    check("warm-up still measured", len(an.warmup_windows) >= 1, True)

    check("dead pulser is dead", by[dead].is_dead, True)
    dead_evs = [ev for ev in by[dead].events if ev.kind == pm.KIND_DEAD]
    check("one death, carried across the switch-off", len(dead_evs), 1)
    check("and no other events for it", len(by[dead].events), 1)
    check("it is dark from the start of both stretches",
          [dead in s.dark_from_start for s in an.stretches], [True, True])

    s1 = an.stretches[0]
    check("stretch 1: full and partial blinks", (s1.blinks, s1.partial), (2, 1))
    check("stretch 1: per pulser", s1.per_pulser, {victim: 2, partial: 1})
    check("stretch 1: the low-power stretch inside it", s1.low_power_n, 1)
    check("stretch 1: one energy dip with no pulser", s1.unexplained, 1)
    check("unexplained dip listed at 250 s",
          [int((t - T0) // S) for t, _d in an.unexplained_dips], [250])
    s2 = an.stretches[1]
    check("stretch 2: one blink", (s2.blinks, s2.partial, s2.per_pulser),
          (1, 0, {victim: 1}))
    check("stretch bounds are the voltage's", (s1.start_ns, s1.end_ns), full[0])


def test_stops_with_power(rois, ref):
    _say("\nArray stops by the dump bus: trip, manual, replaced, switched off")
    fr = Frames(rois, ref)
    x, y = "C4", "E6"
    p = pm.AnalysisParams()
    e0, e1 = int(p.edge_start_s * S), int(p.edge_end_s * S)
    # Four full-power stretches (seconds from T0) and what happens at their ends.
    full_s = [(0, 300), (600, 900), (1020, 1320), (3720, 4020)]
    seconds = []
    for a, b in full_s:
        for s in range(a, b):
            kw = {}
            if a == 0 and s >= 290:
                kw["dark"] = (x,)        # x goes out 10 s before the voltage falls
            if a == 1020 and s >= 1310:
                kw["dark"] = (y,)        # y goes out 10 s before the voltage falls
            seconds.append((s, kw))
    samples = fr.samples(seconds)
    full = [(T0 + a * S, T0 + b * S) for a, b in full_s]
    cp = pm.CamPower(cam=CAM, full_spans=full, lit_spans=full,
                     judged_spans=[(a + e0, b - e1) for a, b in full], has_energy=False)
    an = pm.analyze_camera(rois, samples, p, power=cp, low_power=[],
                           window_ns=(T0, T0 + 4500 * S))
    by = {s.name: s for s in an.stats}
    check("four stops", [t.kind for t in an.trips],
          [pm.GAP_TRIP, pm.GAP_MANUAL, pm.GAP_DEAD, pm.GAP_OFF])
    check("5 min stop: x took it down and lit again",
          (an.trips[0].cause_names, an.trips[0].fates), ([x], {x: "revived"}))
    check("...a pulser trip on x's row", [ev.kind for ev in by[x].events], ["trip"])
    check("...its dark run starts in the unjudged ramp, when x went out",
          int((by[x].events[0].start_ns - T0) // S), 290)
    check("2 min stop, nothing dark first: manual, no culprit",
          (an.trips[1].kind, an.trips[1].cause_names), (pm.GAP_MANUAL, []))
    check("40 min stop: y lit again only after it -> replaced, a dead pulser",
          (an.trips[2].cause_names, an.trips[2].fates), ([y], {y: "replaced"}))
    check("...y is dead, replaced", by[y].status_str, "dead — replaced")
    check("...y came back when full power did",
          int((by[y].dead_until_ns - T0) // S) >= 3720, True)
    check("end of the day, nothing dark first: switched off, not back",
          (an.trips[3].kind, an.trips[3].back), (pm.GAP_OFF, False))
    check("restarts after the two outages", an.array_restarts, 2)
    check("stop lengths are the voltage's",
          [int(t.duration_ns // S) for t in an.trips[:3]], [300, 120, 2400])
    check("x counted as a culprit once, y once",
          (by[x].caused_trips, by[y].caused_trips), (1, 1))
    check("the summary line", an.stop_summary(),
          "1 dead pulser · 1 pulser trip · 1 manual stop · 1 switched off")


def test_no_archiver(rois, ref):
    _say("\nNo archiver: stretches read off the images")
    fr = Frames(rois, ref)
    seconds = [(s, {}) for s in range(0, 120)]
    seconds += [(s, dict(scale=0.28)) for s in range(120, 160)]      # warm-up between
    seconds += [(s, {"dark": ("C4",)} if s == 250 else {}) for s in range(160, 300)]
    an = pm.analyze_camera(rois, fr.samples(seconds), pm.AnalysisParams(),
                           gate_power_failed=True)
    check("two stretches, split by the warm-up", len(an.stretches), 2)
    check("the blink is found", {s.name: s.blinks for s in an.stats}["C4"], 1)
    check("the failure is said out loud", any("full-power gate could not be read" in w
                                              for w in an.warnings), True)
    check("result records it", an.power_failed, True)


def prepared():
    """(rois, reference image) with the reference levels measured the current way."""
    rois = load_rois()
    ref = pm._load_as_float32_gray(pm._bundled_ref(CAM, "allgood"))
    warm = ref * 0.28
    for r in rois:
        t, _f, c = pm._tile_floor_contrast(ref, r.x, r.y, r.w, r.h)
        r.ref_brightness, r.ref_contrast = t, c
        tw, _fw, cw = pm._tile_floor_contrast(warm, r.x, r.y, r.w, r.h)
        r.warm_brightness, r.warm_contrast = tw, cw
    return rois, ref


def main():
    rois, ref = prepared()
    test_power_spans()
    test_energy()
    test_reading_plan()
    test_day(rois, ref)
    test_stops_with_power(rois, ref)
    test_no_archiver(rois, ref)
    _say("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
