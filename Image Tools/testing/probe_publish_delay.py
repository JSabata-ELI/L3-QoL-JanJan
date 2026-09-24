"""Probe: how long after a shot can its value be READ out of the archiver?

This is the half of the delay nothing on our side can shorten, so it has to be
measured separately from the panel's own. The workstation's clock is ~25 s ahead of
the facility, which would swamp the answer, so the offset is taken from the server's
own HTTP Date header first and every observation is converted to server time.

Prints, per newly published sample: its timestamp, when it became readable, and the
difference. Also how many tail requests that cost.

  python testing/probe_publish_delay.py --seconds 120
"""
import argparse
import email.utils
import http.client
import json
import os
import ssl
import statistics
import sys
import time
import urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva  # noqa: E402

CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"


def server_offset_s(samples: int = 5) -> float:
    """local clock − server clock, in seconds, from the Date header.

    The header has 1 s resolution, so the moment it TICKS is what is watched: polling
    fast and taking the local time of the change pins the offset to well under a
    second, which a single reading cannot do."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    host = cpva.CPVA_HOST
    qs = urllib.parse.urlencode({"channelName": CHANNEL, "start": "0", "end": "1"})
    path = cpva.CPVA_BASE_PATH + "/samples?" + qs
    ticks = []
    prev_date = None
    t_end = time.monotonic() + 3.0
    while time.monotonic() < t_end and len(ticks) < samples:
        conn = http.client.HTTPSConnection(host, timeout=10, context=ctx)
        try:
            conn.request("GET", path, headers={"Accept": "application/json"})
            resp = conn.getresponse()
            resp.read()
            local = time.time()
            d = resp.getheader("Date")
        finally:
            conn.close()
        if not d:
            return 0.0
        srv = email.utils.parsedate_to_datetime(d).timestamp()
        if prev_date is not None and srv != prev_date:
            # The server second has just turned over: server time is srv + ~0.
            ticks.append(local - srv)
        prev_date = srv
        time.sleep(0.05)
    return statistics.median(ticks) if ticks else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--poll-ms", type=float, default=100.0)
    ap.add_argument("--channel", default=CHANNEL)
    args = ap.parse_args()

    off = server_offset_s()
    print(f"local clock is {off:+.2f} s ahead of the archiver (HTTP Date header)")
    print(f"polling {args.channel} every {args.poll_ms:.0f} ms for "
          f"{args.seconds:.0f} s\n")

    seen: "set[int]" = set()
    delays: "list[float]" = []
    requests = 0
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        now_local_ns = int(time.time() * 1e9)
        try:
            # The look-back has to clear the CLOCK OFFSET before it covers any real
            # past: this workstation is ~28 s ahead, so a "last 30 s" window asked in
            # local time reaches barely 2 s back on the server and the probe sees
            # almost nothing published.
            got = cpva.fetch_values(args.channel,
                                    now_local_ns - 180_000_000_000,
                                    now_local_ns + 5_000_000_000,
                                    timeout=8.0)
            requests += 1
        except Exception as exc:
            print(f"  fetch failed: {exc}")
            time.sleep(args.poll_ms / 1000.0)
            continue
        server_now = time.time() - off
        for t, _v in got:
            if t in seen:
                continue
            seen.add(t)
            if not delays and len(seen) < len(got):
                continue        # the backlog already there when we started
            d = server_now - t / 1e9
            # Negative is kept and printed: it means the sample's own timestamp is
            # AHEAD of the server clock (a source clock a few tenths off), and hiding
            # those would quietly turn a ~0 s delay into "nothing was published".
            if -5.0 < d < 20.0:
                delays.append(d)
                when = datetime.fromtimestamp(t / 1e9, cpva.TZ_PRAGUE)
                print(f"  sample {when.strftime('%H:%M:%S.%f')[:-3]}  "
                      f"readable {d:5.2f} s later")
        time.sleep(args.poll_ms / 1000.0)

    if delays:
        s = sorted(delays)
        print(f"\n{len(s)} samples   publication delay p50 {statistics.median(s):.2f} s"
              f"   p10 {s[int(0.1 * len(s))]:.2f} s   "
              f"p90 {s[min(len(s) - 1, int(0.9 * len(s)))]:.2f} s   "
              f"min {s[0]:.2f} s   max {s[-1]:.2f} s")
    else:
        print("\nno new samples were published during the probe")
    print(f"{requests} tail requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
