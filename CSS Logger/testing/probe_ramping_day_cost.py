"""How much does one day of the eight ramping PVs cost from the archiver?

The PV Time Plot used to read pre-built day files. If it is to read the
archiver instead, the question is what a single day of all eight ramping
channels costs in requests and seconds -- that is what decides whether a
months-long range is practical at all.

Run:  python probe_ramping_day_cost.py [YYYY-MM-DD]
"""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cpva_core import (RAMPING_PV_MAP, cpva_fetch_many_adaptive, dt_to_ns,
                       cpva_decode_value)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
    d0 = datetime.strptime(day, "%Y-%m-%d")
    d1 = d0 + timedelta(days=1)

    channels = list(RAMPING_PV_MAP.values())
    print(f"day={day}  channels={len(channels)}")

    t0 = time.perf_counter()
    res, errors, report = cpva_fetch_many_adaptive(
        channels, dt_to_ns(d0), dt_to_ns(d1), count=None,
        max_workers=16, log_fn=lambda m: None)
    dt = time.perf_counter() - t0

    total = 0
    for short, pv in RAMPING_PV_MAP.items():
        n = len(res.get(pv, []))
        total += n
        print(f"  {short:10s} {n:8d} samples   {pv}")

    print(f"\nrequests={report.requests}  splits={report.splits}  "
          f"errors={len(errors)}  samples={total}  time={dt:.1f} s")
    if errors:
        for pv, exc in list(errors.items())[:3]:
            print(f"  error {pv}: {exc}")


if __name__ == "__main__":
    main()
