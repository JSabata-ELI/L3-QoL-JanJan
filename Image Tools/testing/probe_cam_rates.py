"""Probe: every camera's own frame rate in one hour, against the PV sample rate.

"3.3 Hz" is one camera's habit, not the archive's. This lists, per camera folder in a
window: how many frames it stored, how far apart they are, and what share of them has
an energy reading within the pairing window — so "does a frame get its own energy" can
be answered for the camera actually being watched instead of for the first one on the
share.

  python testing/probe_cam_rates.py --from "2026-09-16 15:00" --hours 1
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

CHANNELS = [("PTM1", "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"),
            ("SBW4", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", required=True,
                    help="Prague start, 'YYYY-MM-DD HH:MM'")
    ap.add_argument("--hours", type=float, default=1.0)
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva

    t0 = datetime.strptime(args.t_from, "%Y-%m-%d %H:%M").replace(tzinfo=cpva.TZ_PRAGUE)
    start_ns = int(t0.timestamp() * 1e9)
    end_ns = start_ns + int(args.hours * 3600 * 1e9)

    per_cam: "dict[str, list[int]]" = {}
    for d in m.hour_dirs_for_windows([(start_ns, end_ns)]):
        try:
            cams = sorted(p for p in d.iterdir() if p.is_dir())
        except Exception:
            continue
        for cam in cams:
            for p in cam.glob("*.png"):
                t = m.parse_unix_ns_from_name(p)
                if t and start_ns <= t <= end_ns:
                    per_cam.setdefault(cam.name, []).append(t)
    if not per_cam:
        print("no frames on the share for that window")
        return 1

    samples = {}
    for label, ch in CHANNELS:
        got = cpva.fetch_values(ch, start_ns - 60_000_000_000,
                                end_ns + 60_000_000_000,
                                timeout=cpva.FULL_DAY_TIMEOUT)
        samples[label] = [t for t, _ in got]
        rate = len(got) / max(1.0, args.hours * 3600)
        print(f"{label}: {len(got)} readings, {rate:.2f}/s "
              f"(one every {1 / rate:.1f} s)" if rate else f"{label}: none")

    win = m._PV_WINDOW_NS
    print(f"\npaired: with the old fixed ±{win / 1e9:.1f} s → with the claim window "
          f"(half the camera's own frame gap)\n")
    print(f"{'camera':34s} {'frames':>7s} {'every':>8s}   "
          + "  ".join(f"{lab:>14s}" for lab, _ in CHANNELS))
    for cam, ts in sorted(per_cam.items()):
        ts.sort()
        gaps = [(b - a) / 1e9 for a, b in zip(ts, ts[1:])]
        every = f"{statistics.median(gaps):.2f}s" if gaps else "—"
        cols = []
        for label, _ch in CHANNELS:
            sl = samples[label]
            if not sl:
                cols.append("       no data")
                continue
            hit = claim = 0
            for f in ts:
                i = bisect.bisect_left(sl, f)
                near = min((abs(sl[j] - f) for j in (i - 1, i) if 0 <= j < len(sl)),
                           default=10 ** 18)
                if near <= win:
                    hit += 1
                # …and with the claim window the app uses now: half the gap to this
                # camera's own neighbouring frame.
                if near <= m._pv_own_window_ns(ts, f):
                    claim += 1
            cols.append(f"{100.0 * hit / len(ts):3.0f}% →{100.0 * claim / len(ts):4.0f}%")
        print(f"{cam:34s} {len(ts):7d} {every:>8s}   " + "  ".join(cols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
