"""Probe: is a camera's frame stamped at the same instant as the shot's energy?

Two cameras storing a frame every 5 s can pair 93 % and 5 % with the same energy
channel — which means one of them stamps its frames systematically off the shot. This
prints the SIGNED distance (frame → nearest energy reading) per camera, so a constant
shift (a camera-side delay) can be told from a spread (a free-running camera that is
simply not triggered by the shot).

  python testing/probe_cam_frame_offset.py --from "2026-09-16 15:00" --hours 1 \
      --cam PCM4NF --cam PCM2NF --cam PCW3NF
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


def pct(vals, p):
    s = sorted(vals)
    return s[min(len(s) - 1, int(p / 100.0 * len(s)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--cam", action="append", required=True)
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva

    t0 = datetime.strptime(args.t_from, "%Y-%m-%d %H:%M").replace(tzinfo=cpva.TZ_PRAGUE)
    s_ns = int(t0.timestamp() * 1e9)
    e_ns = s_ns + int(args.hours * 3600 * 1e9)

    sl = [t for t, _ in cpva.fetch_values(CHANNEL, s_ns - 60_000_000_000,
                                          e_ns + 60_000_000_000,
                                          timeout=cpva.FULL_DAY_TIMEOUT)]
    print(f"{len(sl)} readings of {CHANNEL}")

    per_cam: "dict[str, list[int]]" = {}
    for d in m.hour_dirs_for_windows([(s_ns, e_ns)]):
        try:
            cams = sorted(p for p in d.iterdir() if p.is_dir())
        except Exception:
            continue
        for cam in cams:
            if not any(w.lower() in cam.name.lower() for w in args.cam):
                continue
            for p in cam.glob("*.png"):
                t = m.parse_unix_ns_from_name(p)
                if t and s_ns <= t <= e_ns:
                    per_cam.setdefault(cam.name, []).append(t)

    for cam, ts in sorted(per_cam.items()):
        ts.sort()
        signed = []
        for f in ts:
            i = bisect.bisect_left(sl, f)
            cand = [sl[j] for j in (i - 1, i) if 0 <= j < len(sl)]
            if cand:
                signed.append(min((c - f for c in cand), key=abs) / 1e9)
        if not signed:
            print(f"{cam}: nothing to compare")
            continue
        med = statistics.median(signed)
        spread = pct(signed, 90) - pct(signed, 10)
        near = [x for x in signed if abs(x) < 2.5]
        print(f"\n{cam}   {len(ts)} frames")
        print(f"  frame → nearest reading: p50 {med:+.3f}s  p10 {pct(signed, 10):+.3f}s  "
              f"p90 {pct(signed, 90):+.3f}s   spread {spread:.3f}s")
        if near:
            print(f"  of the {len(near)} within 2.5 s: p50 {statistics.median(near):+.3f}s  "
                  f"spread {pct(near, 90) - pct(near, 10):.3f}s")
        verdict = ("a CONSTANT shift — this camera stamps its frames off the shot"
                   if abs(med) > 0.3 and spread < 1.0 else
                   "spread out — this camera is not stamped by the shot at all"
                   if spread > 1.0 else "in step with the shots")
        print(f"  → {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
