"""Probe: how far is a frame's own archiver sample, and how many frames read "n/a"?

A PV reads "n/a" when no sample of that channel falls inside _PV_WINDOW_NS (±0.3 s)
of the image timestamp. That window was measured at 3.3 Hz in August 2026. This
re-measures it on real frames off the share against real samples off the archiver, and
reports:

  * the distribution of |frame - nearest sample| per channel,
  * how many frames the ±0.3 s window rejects — i.e. read "n/a" although the value is
    there,
  * how far the NEIGHBOURING shot is, which is what a wider window would risk pairing
    with by mistake.

  python testing/probe_pv_match_window.py                    # last full hour, first camera
  python testing/probe_pv_match_window.py --hours 3 --cam PFM4NF
"""
import argparse
import os
import statistics
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import bench_common as B  # noqa: E402

CHANNELS = [
    ("PTM1", "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"),
    ("SBW4", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"),
    ("Back_Ref", "L3-PM03-023:Energy"),
]


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--cam", default="", help="substring of the camera folder name")
    ap.add_argument("--max-frames", type=int, default=2000)
    ap.add_argument("--from", dest="t_from", default="",
                    help="Prague start, 'YYYY-MM-DD HH:MM' — default: --hours ago")
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva

    if args.t_from:
        t0 = datetime.strptime(args.t_from, "%Y-%m-%d %H:%M").replace(
            tzinfo=cpva.TZ_PRAGUE)
        start_ns = int(t0.timestamp() * 1e9)
        end_ns = start_ns + int(args.hours * 3600 * 1e9)
    else:
        end_ns = int(time.time() * 1e9)
        start_ns = end_ns - int(args.hours * 3600 * 1e9)

    dirs = m.hour_dirs_for_windows([(start_ns, end_ns)])
    frames: "list[int]" = []
    cam_used = ""
    for d in dirs:
        try:
            cams = sorted(p for p in d.iterdir() if p.is_dir())
        except Exception as exc:
            print(f"  {d}: {type(exc).__name__}: {exc}")
            continue
        if args.cam:
            cams = [c for c in cams if args.cam.lower() in c.name.lower()]
        if not cams:
            continue
        cam = cams[0] if not cam_used else next(
            (c for c in cams if c.name == cam_used), None)
        if cam is None:
            continue
        cam_used = cam.name
        for p in cam.glob("*.png"):
            t = m.parse_unix_ns_from_name(p)
            if t and start_ns <= t <= end_ns:
                frames.append(t)
    frames = sorted(frames)[-args.max_frames:]
    if not frames:
        print("no frames found on the share for that window")
        return 1

    first = datetime.fromtimestamp(frames[0] / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")
    last = datetime.fromtimestamp(frames[-1] / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")
    gaps = [(b - a) / 1e9 for a, b in zip(frames, frames[1:])]
    print(f"camera {cam_used}   {len(frames)} frames   {first}–{last}")
    if gaps:
        print(f"frame spacing: p50 {statistics.median(gaps):.2f}s  "
              f"min {min(gaps):.2f}s  max {max(gaps):.2f}s")
    print(f"match window in the app: ±{m._PV_WINDOW_NS / 1e9:.2f}s   "
          f"exact below ±{cpva.PV_EXACT_MATCH_NS / 1e9:.2f}s")

    win = m._PV_WINDOW_NS
    for label, ch in CHANNELS:
        samples = cpva.fetch_values(ch, frames[0] - 5_000_000_000,
                                    frames[-1] + 5_000_000_000,
                                    timeout=cpva.FULL_DAY_TIMEOUT)
        if not samples:
            print(f"  {label:9s} archiver returned nothing for this window")
            continue
        ts = [t for t, _ in samples]
        import bisect
        dists, nn = [], []
        for f in frames:
            i = bisect.bisect_left(ts, f)
            cand = [abs(ts[j] - f) for j in (i - 1, i, i + 1) if 0 <= j < len(ts)]
            if cand:
                dists.append(min(cand) / 1e9)
            # distance to the SECOND nearest sample — what a wider window risks
            two = sorted(abs(ts[j] - f) for j in range(max(0, i - 2),
                                                       min(len(ts), i + 2)))
            if len(two) > 1:
                nn.append(two[1] / 1e9)
        # The OTHER direction, and the one that tells a clock offset from a sparse
        # channel: every archived sample should sit within half a frame period of some
        # frame. A constant shift here means the two clocks disagree, which would break
        # the pairing for every frame; a spread means the channel is simply archived
        # less often than the camera records.
        import bisect as _b
        signed = []
        for t, _v in samples:
            j = _b.bisect_left(frames, t)
            cand = [frames[k] for k in (j - 1, j) if 0 <= k < len(frames)]
            if cand:
                signed.append(min((t - f for f in cand), key=abs) / 1e9)
        if signed:
            print(f"            sample → nearest frame: p50 {statistics.median(signed):+.3f}s  "
                  f"p10 {pct(signed, 10):+.3f}s  p90 {pct(signed, 90):+.3f}s  "
                  f"(a constant shift here = clocks disagree)")

        out = sum(1 for d in dists if d > win / 1e9)
        print(f"  {label:9s} {len(samples):6d} samples   "
              f"|frame-sample| p50 {statistics.median(dists):.3f}s  "
              f"p90 {pct(dists, 90):.3f}s  p99 {pct(dists, 99):.3f}s  "
              f"max {max(dists):.3f}s")
        print(f"            outside ±{win/1e9:.2f}s → \"n/a\": "
              f"{out} of {len(dists)} frames ({100.0 * out / len(dists):.1f} %)"
              f"   2nd-nearest sample p10 {pct(nn, 10):.3f}s" if nn else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
