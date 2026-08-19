"""What are the small early-morning frames actually showing?

Measures the array level of every Nth frame from 07:40 to 09:10 Prague on 13 Aug and
prints it as a trace, so the claim "those frames are warm-up" can be checked against the
pixels instead of against the file size.
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

APP = Path(r"c:\Users\jan.moucka.eli-laser\OneDrive - ELI ERIC\ELI Beamlines\Python"
           r"\programy\L3-QoL-JanJan\Pulser Monitor")
sys.path.insert(0, str(APP))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pulser_monitor as pm  # noqa: E402

CAM = os.environ.get("PROBE_CAM", "PD1M1")
STEP = int(os.environ.get("PROBE_STEP", "20"))
root = pm.IMAGES_ROOT_OPTIONS["Lab"]
cfg = json.loads((Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json")
                 .read_text(encoding="utf-8"))


def main():
    rois = [pm.RoiDefinition.from_dict(r) for r in cfg["cameras"][CAM]]
    ref = np.array([r.ref_contrast for r in rois], dtype=np.float64)
    usable = ref > 0

    files = []
    for h in (5, 6, 7):
        d = root / "2026" / "8" / "13" / str(h) / pm.CAMERAS[CAM]
        if not d.is_dir():
            continue
        for e in os.scandir(str(d)):
            if not e.is_file():
                continue
            p = Path(e.path)
            ts = pm._parse_ts_from_path(p)
            if ts:
                files.append((ts, p, e.stat().st_size))
    files.sort()
    files = files[::STEP]
    print(f"{CAM}: {len(files)} sampled frames (every {STEP}th)\n")

    rects = [(r.x, r.y, r.w, r.h) for r in rois]
    tasks = [(ts, str(p)) for ts, p, _ in files]
    out = []
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1),
                             initializer=pm._pool_init, initargs=(rects,)) as ex:
        for (ts, means, fm, contrasts), (_t, _p, size) in zip(
                ex.map(pm._measure_frame, tasks, chunksize=8), files):
            if means is None:
                continue
            c = np.asarray(contrasts, dtype=np.float64)
            lvl = float(np.median(c[usable] / ref[usable]))
            out.append((ts, lvl, fm, size / 1024))

    print(f"{'time':>10} {'kB':>6} {'frame_mean':>11} {'array_level':>12}   state")
    prev_state = None
    for ts, lvl, fm, kb in out:
        if fm < 0.05:
            st = "BLANK (diodes off)"
        elif lvl < 0.64:
            st = "warm-up"
        else:
            st = "running"
        mark = "  <-- change" if st != prev_state else ""
        print(f"{pm._ns_to_dt(ts):%H:%M:%S} {kb:6.0f} {fm:11.4f} {lvl:12.3f}   {st}{mark}")
        prev_state = st

    # Summarise contiguous stretches at full resolution boundaries.
    print("\nSummary of the sampled trace:")
    runs, cur = [], None
    for ts, lvl, fm, kb in out:
        st = "BLANK" if fm < 0.05 else ("warm-up" if lvl < 0.64 else "running")
        if cur is None or cur[0] != st:
            if cur:
                runs.append(cur)
            cur = [st, ts, ts]
        else:
            cur[2] = ts
    if cur:
        runs.append(cur)
    for st, a, b in runs:
        mins = (b - a) / 6e10
        print(f"   {st:8} {pm._ns_to_dt(a):%H:%M:%S} .. {pm._ns_to_dt(b):%H:%M:%S}  ({mins:.1f} min)")


if __name__ == "__main__":
    main()
