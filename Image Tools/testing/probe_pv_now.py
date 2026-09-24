"""Probe: is the archiver answering RIGHT NOW for the PVs the Slider panel reads?

Why: "the values do not load" has three different causes that look identical on the
picture — the archiver has no recent sample for that channel, the query fails, or the
panel asks for the wrong instant. This asks the archiver directly, once per channel,
for the last hour and for the last two minutes, and prints the newest sample with its
age. Nothing about the GUI is involved, so a failure here is the API's, not the panel's.

Run:  python testing/probe_pv_now.py
      python testing/probe_pv_now.py --minutes 10
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402

# The channels the Slider's PV panel reads for this installation (presets it can pick
# plus the two user-added ones), by the name shown on screen.
CHANNELS = {
    "PTM1":      "HAPLS-ENER_IN_PTM1_LT5_DIAG2:Energy",
    "Back_Ref":  None,          # filled from cpva.CHANNEL_MAP below
    "SBW4":      "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "GDD":       "L3-SPFE-AOD03-002:Order2_RB",
    "shot no.":  None,
}


def hhmmss(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S.%f")[:-3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    args = ap.parse_args()

    CHANNELS["Back_Ref"] = cpva.CHANNEL_MAP.get("Back_Ref")
    CHANNELS["PTM1"] = cpva.CHANNEL_MAP.get("ptm1", CHANNELS["PTM1"])
    CHANNELS["shot no."] = cpva.SHOT_CHANNEL

    now_ns = int(time.time() * 1e9)
    print(f"base = {cpva.CPVA_BASE_URL}")
    print(f"local clock = {hhmmss(now_ns)} (this PC runs ahead of the facility)")
    for label, ch in CHANNELS.items():
        if not ch:
            print(f"  {label:10s} -- no channel")
            continue
        for span_s, tag in ((args.minutes * 60.0, f"{args.minutes:g} min"),
                            (120.0, "2 min")):
            t0 = time.time()
            try:
                got, src = cpva.fetch_values_ex(
                    ch, now_ns - int(span_s * 1e9), now_ns + 5_000_000_000,
                    timeout=cpva.DEFAULT_TIMEOUT)
                dt = time.time() - t0
                if got:
                    newest_ns, newest_v = got[-1]
                    age = (now_ns - newest_ns) / 1e9
                    print(f"  {label:10s} {tag:>7s}: {len(got):5d} samples  "
                          f"newest {hhmmss(newest_ns)} = {newest_v:g}  "
                          f"age {age:6.1f}s  src={src}  ({dt:.2f}s)")
                else:
                    print(f"  {label:10s} {tag:>7s}: EMPTY (archiver answered)  "
                          f"({dt:.2f}s)")
            except Exception as e:
                dt = time.time() - t0
                print(f"  {label:10s} {tag:>7s}: FAILED after {dt:.2f}s: "
                      f"{type(e).__name__}: {e}")
    print("\n" + cpva.stats_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
