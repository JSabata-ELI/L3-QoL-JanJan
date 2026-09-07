"""Probe: what does the archiver answer TODAY for both SBW4 names?

Why: PV Search keeps saying "The archiver did not answer for this day" with SBW4
ticked. Three things can look the same on screen — a transport failure, an empty
day, and a day that only answers under the channel's other name. This asks the
archiver directly, one name at a time, and prints which of the three it is.

Run:  python "Image Tools/testing/probe_sbw4_today.py"
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402
from datetime import datetime  # noqa: E402


def _hhmmss(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")


NAMES = [cpva.SBW4_CHANNEL, cpva.SBW4_CHANNEL_LEGACY]
DAYS = [cpva.today_key(), "2026-09-03", "2026-09-02", "2026-08-29"]

print(f"today (Prague) = {cpva.today_key()}   base = {cpva.CPVA_BASE_URL}")
for day in DAYS:
    print(f"\n=== {day} ===")
    s_ns, e_ns = cpva.day_bounds_ns(day)
    for name in NAMES:
        t0 = time.time()
        try:
            got, src = cpva.fetch_values_ex(name, s_ns, e_ns,
                                            timeout=cpva.FULL_DAY_TIMEOUT)
            dt = time.time() - t0
            if got:
                first = _hhmmss(got[0][0])
                last = _hhmmss(got[-1][0])
                print(f"  {name:42s} {len(got):7d} samples  "
                      f"{first}–{last}  src={src}  {dt:.1f}s")
            else:
                print(f"  {name:42s} EMPTY (archiver answered)  {dt:.1f}s")
        except Exception as e:
            dt = time.time() - t0
            print(f"  {name:42s} FAILED after {dt:.1f}s: "
                  f"{type(e).__name__}: {e}")
    # And what get_day — the path PV Search actually uses — makes of it.
    res = cpva.get_day(cpva.SBW4_CHANNEL, day, timeout=cpva.FULL_DAY_TIMEOUT)
    print(f"  get_day({cpva.SBW4_CHANNEL}) -> status={res.status!r} "
          f"n={len(res.samples)} src={res.src_channel!r}")
