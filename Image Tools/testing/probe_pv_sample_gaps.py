"""Probe: how often is an energy channel actually archived, and in what pattern?

Tells "the laser fired slowly" from "the laser fired fast and only some shots were
archived": in the second case the gaps between stored samples are whole multiples of
the shot period. Also prints the rep-rate channels if the archiver knows any.

  python testing/probe_pv_sample_gaps.py --from "2026-09-16 15:26" --hours 0.1
"""
import argparse
import collections
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402

CHANNELS = [
    ("PTM1", "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"),
    ("SBW4", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"),
]
SHOT_PERIOD_S = 0.30      # the camera's frame spacing in the same window


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", required=True)
    ap.add_argument("--hours", type=float, default=0.1)
    args = ap.parse_args()

    t0 = datetime.strptime(args.t_from, "%Y-%m-%d %H:%M").replace(tzinfo=cpva.TZ_PRAGUE)
    s_ns = int(t0.timestamp() * 1e9)
    e_ns = s_ns + int(args.hours * 3600 * 1e9)

    for label, ch in CHANNELS:
        got = cpva.fetch_values(ch, s_ns, e_ns)
        if len(got) < 3:
            print(f"{label}: {len(got)} samples — nothing to say about the pattern")
            continue
        gaps = [(b[0] - a[0]) / 1e9 for a, b in zip(got, got[1:])]
        mult = collections.Counter(round(g / SHOT_PERIOD_S) for g in gaps)
        off = [abs(g / SHOT_PERIOD_S - round(g / SHOT_PERIOD_S)) for g in gaps]
        print(f"\n{label}: {len(got)} samples in {args.hours * 60:.0f} min "
              f"({len(got) / (args.hours * 3600):.2f}/s)")
        print(f"  gap p50 {statistics.median(gaps):.2f}s  min {min(gaps):.2f}s  "
              f"max {max(gaps):.2f}s")
        print(f"  gap as a multiple of the {SHOT_PERIOD_S:.2f}s frame period: "
              + ", ".join(f"x{k}:{n}" for k, n in sorted(mult.items())[:12]))
        print(f"  how far off a whole multiple: p50 {statistics.median(off):.3f} "
              f"(0 = every gap is a whole number of shots)")
        vals = [v for _, v in got]
        print(f"  values {min(vals):.2f}..{max(vals):.2f} J, "
              f"median {statistics.median(vals):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
