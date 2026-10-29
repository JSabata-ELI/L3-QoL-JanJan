"""Laser settings — read all five gate valves from the real archives.

SGV501 comes from the CPVA archiver (10.78.0.57), SGV502 to SGV505 from the
L3BT archive behind the proxy (10.78.0.64, twin 10.78.0.65). Prints, for each
hall, which archive answered and what the valve says. Needs the lab network.
Read-only.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ls_core as C                                             # noqa: E402
import ls_cpva as A                                             # noqa: E402


def main():
    shooting = C.default_config()["shooting"]
    names = []
    for hall in C.HALLS:
        names += [C.gate_for(shooting, hall)["open"], C.closed_channel(hall)]
    reader = A.PassReader()
    t0 = time.monotonic()
    readings = reader.read_many(names, deadline_s=20.0)
    took = time.monotonic() - t0
    now = time.time()
    last_ok = {n: now for n, r in readings.items() if r.has_value}
    bad = 0
    for name in names:
        r = readings[name]
        where = "L3BT proxy" if A.on_proxy(name) else "CPVA"
        when = (time.strftime("%d.%m. %H:%M:%S", time.localtime(r.newest_ns / 1e9))
                if r.newest_ns else "-")
        print(f"  {name:40s} {where:10s} value={r.value!r:6} since {when}"
              f"{'  ERROR ' + r.error if r.error else ''}")
        bad += not r.has_value
    print()
    for hall in C.HALLS:
        state, why = C.hall_state(shooting, hall, readings, last_ok, now)
        print(f"  {C.hall_shown(hall):3s} {state:9s} {why.splitlines()[0]}")
    print(f"\npass took {took:.2f} s; {A.stats_line()}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
