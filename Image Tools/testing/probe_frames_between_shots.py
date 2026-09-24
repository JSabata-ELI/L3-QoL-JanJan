"""Probe: do the frames BETWEEN two archived energy samples show a shot at all?

93 % of stored frames have no energy sample within the ±0.3 s pairing window
(probe_pv_match_window.py). Two very different machines look like that:

  * the laser fires at the camera's rate and the archiver keeps only some shots —
    then those frames are real shots whose number simply was not stored;
  * the camera free-runs and the laser fires rarely — then those frames are dark and
    "n/a" is the only honest answer.

This reads a few frames of each kind off the share and prints how bright they are.

  python testing/probe_frames_between_shots.py --from "2026-09-16 15:26" --hours 0.1
"""
import argparse
import bisect
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import bench_common as B  # noqa: E402

CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", required=True,
                    help="Prague start, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--hours", type=float, default=0.1)
    ap.add_argument("--cam", default="")
    ap.add_argument("--n", type=int, default=6, help="frames of each kind to read")
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva
    import numpy as np

    t0 = datetime.strptime(args.t_from, "%Y-%m-%d %H:%M").replace(tzinfo=cpva.TZ_PRAGUE)
    start_ns = int(t0.timestamp() * 1e9)
    end_ns = start_ns + int(args.hours * 3600 * 1e9)

    paths = {}
    for d in m.hour_dirs_for_windows([(start_ns, end_ns)]):
        try:
            cams = sorted(p for p in d.iterdir() if p.is_dir())
        except Exception:
            continue
        if args.cam:
            cams = [c for c in cams if args.cam.lower() in c.name.lower()]
        if not cams:
            continue
        for p in cams[0].glob("*.png"):
            t = m.parse_unix_ns_from_name(p)
            if t and start_ns <= t <= end_ns:
                paths[t] = p
    if not paths:
        print("no frames on the share for that window")
        return 1
    frames = sorted(paths)
    samples = cpva.fetch_values(CHANNEL, start_ns - 5_000_000_000,
                                end_ns + 5_000_000_000)
    ts = [t for t, _ in samples]
    print(f"{len(frames)} frames, {len(samples)} PTM1 samples")

    matched, between = [], []
    for f in frames:
        i = bisect.bisect_left(ts, f)
        near = min((abs(ts[j] - f) for j in (i - 1, i) if 0 <= j < len(ts)),
                   default=10 ** 18)
        (matched if near <= m._PV_WINDOW_NS else between).append(f)

    def brightness(ts_list, label):
        vals = []
        for t in ts_list[:args.n]:
            from PySide6.QtGui import QImage
            reader, _buf, _ba = m._open_reader(paths[t])
            img = reader.read() if reader is not None else None
            if img is None or img.isNull():
                continue
            img = img.convertToFormat(QImage.Format.Format_Grayscale16)
            w, h = img.width(), img.height()
            arr = np.frombuffer(img.constBits(), dtype=np.uint16,
                                count=w * h).reshape(h, w)
            vals.append((datetime.fromtimestamp(t / 1e9, cpva.TZ_PRAGUE)
                         .strftime("%H:%M:%S.%f")[:-3],
                         float(arr.mean()), float(arr.max())))
        print(f"\n{label}  ({len(ts_list)} frames of this kind)")
        for when, mean, mx in vals:
            print(f"   {when}   mean {mean:8.1f}   peak {mx:8.0f}")
        if vals:
            print(f"   median peak {statistics.median(v[2] for v in vals):.0f}")

    brightness(matched, "frames WITH an archived energy sample")
    brightness(between, "frames BETWEEN archived samples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
