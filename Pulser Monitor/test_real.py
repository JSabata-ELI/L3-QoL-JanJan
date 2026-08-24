"""End-to-end check on the REAL running/warm-up frames from Downloads\\diody.

No synthetic pixels at all: the sequence is built by repeating the genuine running and
warm-up frames of each camera, so the whole chain — measurement, warm-up detection,
baseline learning, classification — runs on real data.

Expected: the warm frames are recognised as warm-up (not as an array trip and not as
40 dropouts), and the pulsers that are genuinely dark in the August frames are reported
as dead, while nothing else is.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP = Path(r"c:\Users\jan.moucka.eli-laser\OneDrive - ELI ERIC\ELI Beamlines\Python"
           r"\programy\L3-QoL-JanJan\Pulser Monitor")
sys.path.insert(0, str(APP))
# Own directory first, so the sibling test modules here win over any older copy
# sitting next to the app.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pulser_monitor as pm  # noqa: E402
import test_pulser as tp  # noqa: E402

D = Path(r"C:\Users\jan.moucka.eli-laser\Downloads\diody")
CADENCE = 5_000_000_000
T0 = 1_786_000_000_000_000_000

cfg = json.loads((Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json")
                 .read_text(encoding="utf-8"))
ROIS = {c: [pm.RoiDefinition.from_dict(r) for r in v] for c, v in cfg["cameras"].items()}
PREFIX = {c: v.split("-_-")[0].split("-", 1)[1] for c, v in pm.CAMERAS.items()}

failures = []


def check(label, cond, detail=""):
    print(f"    {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not cond:
        failures.append(label)


def frame(cam, day, mode):
    d = next(x for x in D.iterdir()
             if x.name.startswith(day) and x.name.endswith(mode))
    return pm._load_as_float32_gray(next(d.glob(PREFIX[cam] + "*.png")))


def measure(arr, rois):
    means, contrasts = [], []
    for r in rois:
        y0, y1 = max(0, r.y), min(arr.shape[0], r.y + r.h)
        x0, x1 = max(0, r.x), min(arr.shape[1], r.x + r.w)
        means.append(float(arr[y0:y1, x0:x1].mean()))
        contrasts.append(pm._tile_floor_contrast(arr, r.x, r.y, r.w, r.h)[2])
    return means, contrasts, float(arr[::4, ::4].mean())


def build(cam, rois, rng):
    """12 warm frames, 40 running across both real days, 20 blank, 20 running.

    The blank stretch is 20 frames rather than 10 so that at this 5 s cadence it lasts
    ~105 s — past the 60 s floor below which a stretch without data is a hiccup in the
    archive rather than the array being down. The point of the test is the real pixels, not
    the boundary, so it sits clear of it."""
    warm = frame(cam, "140826", "warm")
    r13, r14 = frame(cam, "130826", "runing"), frame(cam, "140826", "running")
    script = ([("warm", warm)] * 12 + [("run", r13)] * 20 + [("run", r14)] * 20
              + [("blank", None)] * 20 + [("run", r14)] * 20)
    samples, t = [], T0
    for kind, arr in script:
        a = tp.blank_frame(r14.shape, rng) if kind == "blank" else arr
        m, c, fm = measure(a, rois)
        samples.append(pm.SamplePoint(t, None, m, fm, roi_contrasts=c))
        t += CADENCE
    return samples


# The pulsers whose contrast AND plain brightness both collapsed between the
# February reference and 14 August — i.e. the real deaths, confirmed by hand.
EXPECTED_DEAD = {"PD1M1": ["A1", "C5", "D6"], "PD2M1": ["A5", "B7"],
                 "PD3M1": ["B3", "B7"], "PD4M1": ["B5", "D3"]}


def main():
    rng = np.random.default_rng(5)
    # A pulser is dead once it has stayed dark through three recoveries OR 20 minutes of
    # array-up time. This script is 72 frames at 5 s — six minutes in total — so the
    # 20-minute clock is scaled down to one minute here. It has to be stated rather than
    # inherited: at the default the correct answer for a six-minute window is "nothing has
    # been shown to be dead yet", which is true but tests nothing about the pixels.
    PARAMS = pm.AnalysisParams(dead_confirm_min=1.0)
    for cam in pm.CAMERAS:
        rois = ROIS[cam]
        print(f"\n=== {cam}")
        samples = build(cam, rois, rng)
        an = pm.analyze_camera(rois, samples, PARAMS)
        dead = sorted(s.name for s in an.stats if s.is_dead)
        dropped = sorted(s.name for s in an.stats
                         if s.dropouts or s.faults or (s.trips and not s.is_dead))
        print(f"    w={an.warm_level_used:.3f} ({an.warm_level_source})  "
              f"warm frames={sum(an.frame_warmup)}  nodata={sum(an.frame_nodata)}  "
              f"outages={len(an.outages)}  restarts={an.array_restarts}")
        print(f"    dead={dead}")
        print(f"    kinds={[t.kind_label for t in an.trips]}")
        print(f"    dropouts/faults={dropped or 'none'}")

        check("warm-up detected on the real frames", sum(an.frame_warmup) == 12,
              str(sum(an.frame_warmup)))
        # The 10 blank frames are dropped from the series entirely; the gap they leave
        # is marked with the usual two synthetic no-data markers.
        # All 10 blank frames leave the series; the 72 real ones stay and none of them
        # is mistaken for no-data (only the synthetic gap markers are).
        real = len(an.times_ns) - sum(an.frame_nodata)
        check("blanks excluded, every real frame kept", real == 72,
              f"{real} real frames of 72, {len(an.times_ns)} total")
        check("exactly one array outage", len(an.outages) == 1, str(len(an.outages)))
        # The dead pulsers are dark right up to the moment the data stops, so this reads as
        # the array falling over rather than as a stop of ours.
        check("read as a trip, not a deliberate stop",
              [t.kind for t in an.trips] == [pm.GAP_TRIP], str([t.kind for t in an.trips]))
        check("the restart is counted", an.array_restarts == 1)
        check("warming did not create dropouts", not dropped, str(dropped))
        check("dead list matches the hand-checked one", dead == EXPECTED_DEAD[cam],
              f"{dead} vs {EXPECTED_DEAD[cam]}")
        for w in an.warnings:
            print(f"    note: {w[:110]}")

    print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
