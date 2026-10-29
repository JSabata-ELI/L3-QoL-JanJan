"""Probe: how long does the archiver take to answer a TINY request?

A one-minute window of a slow channel returns 0–2 samples, so its time is almost
pure server and network latency — nothing of ours is in it. Asked several times
in a row on one kept-alive connection, and once more with a fresh connection, to
separate the connection set-up (TLS) from the server's own answer time.

Run:  python testing/probe_archiver_latency.py
      python testing/probe_archiver_latency.py --n 20
"""
import argparse
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import cpva_client as cpva                              # noqa: E402

CHANNELS = ["L3-SPFE-AOD03-002:Order2_RB", "L3-PM03-023:Energy",
            "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    args = ap.parse_args()
    host = cpva.CPVA_HOST if isinstance(cpva.CPVA_HOST, str) else cpva.CPVA_HOST[0]
    t0 = time.monotonic()
    try:
        s = socket.create_connection((host.split(":")[0], 8443), timeout=5)
        s.close()
        print(f"TCP connect to {host}: {(time.monotonic() - t0) * 1000:.0f} ms")
    except Exception as e:
        print(f"TCP connect to {host} failed: {e}")

    now = int(time.time() * 1e9)
    for ch in CHANNELS:
        times = []
        for i in range(args.n):
            end = now - i * 60_000_000_000
            t = time.monotonic()
            try:
                got = cpva.fetch_samples(ch, end - 60_000_000_000, end, timeout=30)
                n = len(got)
            except Exception as e:
                n = f"ERR {e}"[:40]
            times.append(time.monotonic() - t)
            print(f"  {ch:<38} 1-min window #{i + 1}: {times[-1]:5.2f} s  n={n}")
        print(f"  -> median {statistics.median(times):.2f} s, "
              f"min {min(times):.2f} s, max {max(times):.2f} s\n")
    print(cpva.stats_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
