"""Probe: what does a WHOLE-DAY query answer today, channel by channel?

The panel does not read single samples — it reads a day and picks the sample nearest
the frame (cpva.get_day). A day of a fast channel can be refused with HTTP 500, which
arrives at the panel as "no data" and is indistinguishable from a quiet channel. This
prints, per channel, the day status, the sample count, the newest sample, and whether
the client had to split the range or hit an HTTP error doing it.

  python testing/probe_pv_day_status.py
  python testing/probe_pv_day_status.py --day 2026-09-15
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402

CHANNELS = [
    ("PTM1",     cpva.CHANNEL_MAP.get("ptm1")),
    ("Back_Ref", cpva.CHANNEL_MAP.get("Back_Ref")),
    ("SBW4",     "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"),
    ("GDD",      "L3-SPFE-AOD03-002:Order2_RB"),
]


def hhmmss(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=cpva.today_key())
    args = ap.parse_args()

    print(f"day {args.day}   base {cpva.CPVA_BASE_URL}")
    for label, ch in CHANNELS:
        if not ch:
            print(f"  {label:10s} -- no channel")
            continue
        before = dict(cpva.STATS)
        t0 = time.time()
        res = cpva.get_day(ch, args.day, timeout=cpva.FULL_DAY_TIMEOUT)
        dt = time.time() - t0
        err = int(cpva.STATS["http_errors"]) - int(before["http_errors"])
        spl = int(cpva.STATS.get("range_splits", 0)) - int(before.get("range_splits", 0))
        req = int(cpva.STATS["requests"]) - int(before["requests"])
        newest = hhmmss(res.samples[-1][0]) if res.samples else "—"
        print(f"  {label:10s} status={res.status:8s} n={len(res.samples):7d} "
              f"newest {newest}  src={res.src_channel}  "
              f"{dt:5.1f}s  requests={req} splits={spl} httpErr={err}")
    print("\n" + cpva.stats_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
