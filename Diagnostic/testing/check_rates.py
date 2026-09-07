"""Which rates did the counter infer, and does SysRate agree?

A period read too long halves the count, so any rate the timing system never
actually ran at is a bug, not a discovery.
"""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cpva_api as C

NS = 1_000_000_000
TZ = ZoneInfo("Europe/Prague")
RATE = "L3-TIMING-TIMING:SysRate"

rows = json.loads(Path(__file__).with_name("shot_counts_cache.json")
                  .read_text("utf-8"))

seen = Counter()
odd_days = []
for d in sorted(rows):
    rs = {r for v in rows[d]["pc"].values() for r in v["rates"]}
    for r in rs:
        seen[round(r, 1)] += 1
    if any(r < 0.15 for r in rs):
        odd_days.append(d)

print("inferred rates over the whole year (rounded), and how many days:")
for k, v in sorted(seen.items()):
    print(f"   {k:6.1f} Hz   {v} days")
print(f"\ndays where a rate below 0.15 Hz was inferred: {len(odd_days)}")
print("   " + ", ".join(odd_days))

print("\nwhat SysRate actually held on those days (samples per hour probed):")
for d in odd_days[:12]:
    day = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TZ)
    vals = Counter()
    for h in (8, 11, 14, 17):
        s = int((day + timedelta(hours=h)).timestamp() * NS)
        try:
            r = C.cpva_fetch_samples(RATE, s, s + 120 * NS, timeout=30.0)
        except Exception:
            continue
        for x in r:
            t = x.get("timestamp") or x.get("time")
            if t is None or not (s <= int(t) < s + 120 * NS):
                continue
            v = C.cpva_decode_value(x)
            if isinstance(v, list) and v:
                v = v[0]
            if isinstance(v, (int, float)):
                vals[round(float(v), 3)] += 1
    print(f"   {d}   {dict(vals)}")
