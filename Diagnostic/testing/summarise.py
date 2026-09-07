"""The whole answer in one page: how far the archive reaches, what was counted,
how many shots per year, and how much of that rests on a single meter.

    python summarise.py            # best of the four arrays (the answer)
    python summarise.py PC1        # one array's own record, raw

Reads shot_counts_cache.json, which count_shots.py fills in day by day.
"""
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

CACHE = Path(__file__).with_name("shot_counts_cache.json")

# The earliest reading of the diodes' energy anywhere in the archive. Settled
# by asking each channel for a one-minute window in 2010: the archiver answers
# a window that predates the data with the first sample AFTER it, so the reply
# IS the channel's first reading. There is no energy and no rate before this,
# and the waveform channel that looks older (12 May 2025) holds nothing even on
# a day the diodes plainly fired. The high-power key and 211 other pulser
# channels reach back to 29 Mar 2022, but they carry connection and fault
# states, never a shot.
ARCHIVE_FLOOR = date(2025, 8, 15)
METER_STARTS = {"PC1": "27 Aug 2025", "PC2": "30 Aug 2025",
                "PC3": "15 Aug 2025", "PC4": "15 Aug 2025"}

# All four arrays fire together in all but a fraction of a per cent of the
# time, and every way the archive fails LOSES readings - a copy that stops
# writing, a hole longer than a minute credited with one shot. Nothing in it
# can invent a shot. So the largest of the four is the closest to the truth,
# and every total here is a LOWER BOUND.
GOOD = ("PC1", "PC3", "PC4")     # PC2's meter is broken; see below
PC = sys.argv[1] if len(sys.argv) > 1 else "best"

rows = json.loads(CACHE.read_text("utf-8"))
days = sorted(rows)
if not days:
    sys.exit("shot_counts_cache.json is empty - run count_shots.py first")


def rate_class(rates):
    slow = any(r < 1 for r in rates)
    fast = any(r >= 1 for r in rates)
    return "both" if slow and fast else "3.3" if fast else "0.2" if slow else "-"


years = defaultdict(lambda: {"days": 0, "fired": 0, "hp_h": 0.0,
                             "fire_h": 0.0, "shots": 0, "lonely": 0,
                             "d02": 0, "d33": 0, "dboth": 0})
months = defaultdict(int)
fire_hours, spread = [], []
blind_h = murky_h = 0.0

for d in days:
    r = rows[d]
    if PC == "best":
        pc = max(r["pc"].values(), key=lambda v: v["shots"]) if r["pc"] else None
        good = [r["pc"][k]["shots"] for k in GOOD
                if k in r["pc"] and r["pc"][k]["shots"] > 0]
    else:
        pc = r["pc"].get(PC)
        good = []
    if pc is None:
        continue

    y = years[d[:4]]
    y["days"] += 1
    y["shots"] += pc["shots"]
    y["hp_h"] += r["hpe_on_hours"]
    y["fire_h"] += pc["firing_h"]
    months[d[:7]] += pc["shots"]
    blind_h += pc.get("no_data_h", 0.0)
    murky_h += pc.get("unreliable_h", 0.0)

    if pc["shots"] > 0:
        y["fired"] += 1
        fire_hours.append(pc["firing_h"])
        y[{"0.2": "d02", "3.3": "d33", "both": "dboth",
           "-": "dboth"}[rate_class(pc["rates"])]] += 1
    if len(good) > 1:
        s = (max(good) - min(good)) / max(good)
        spread.append(s)
        if s > 0.05:
            # Only one meter recorded this day properly, so this day's count
            # has no second opinion behind it.
            y["lonely"] += pc["shots"]

first, last = days[0], days[-1]
missing = ((date.fromisoformat(last) - date.fromisoformat(first)).days + 1
           - len(days))

print(f"""
=================== DIODE SHOTS, {'best of the four arrays' if PC == 'best'
                                  else PC} ===================

  earliest the archive can answer   {ARCHIVE_FLOOR:%d %b %Y}
  counted from                      {first}
  counted to                        {last}
  days in the count                 {len(days)}   (gaps in the range: {missing})

  Nothing before {ARCHIVE_FLOOR:%d %b %Y} exists to be counted: no energy
  reading of the diodes and no repetition rate. Each meter's own record starts
  later still - {', '.join(f'{k} {v}' for k, v in METER_STARTS.items())} -
  which is why the four are read together and the fullest one is taken.
""")

print(f"  {'year':6s} {'days':>5s} {'fired':>6s} {'HP h':>6s} {'firing h':>9s} "
      f"{'0.2':>4s} {'3.3':>4s} {'mix':>4s} {'shots':>12s} {'one meter':>12s}")
tot = lonely = 0
for k in sorted(years):
    y = years[k]
    tot += y["shots"]
    lonely += y["lonely"]
    print(f"  {k:6s} {y['days']:5d} {y['fired']:6d} {y['hp_h']:6.0f} "
          f"{y['fire_h']:9.0f} {y['d02']:4d} {y['d33']:4d} {y['dboth']:4d} "
          f"{y['shots']:12,d} {y['lonely']:12,d}")
print(f"  {'TOTAL':6s} {len(days):5d} "
      f"{sum(y['fired'] for y in years.values()):6d} "
      f"{sum(y['hp_h'] for y in years.values()):6.0f} "
      f"{sum(y['fire_h'] for y in years.values()):9.0f} "
      f"{sum(y['d02'] for y in years.values()):4d} "
      f"{sum(y['d33'] for y in years.values()):4d} "
      f"{sum(y['dboth'] for y in years.values()):4d} "
      f"{tot:12,d} {lonely:12,d}")
print("\n  Both part-years: 2025 starts 15 Aug, 2026 ends at the last counted "
      "day.\n  '0.2' / '3.3' / 'mix' count DAYS that fired at that rate; "
      "'one meter' is\n  how many of the shots come from a day where the "
      "meters disagreed by over 5 %.")

print("\n  by month:")
line = "   "
for i, k in enumerate(sorted(months), 1):
    line += f" {k} {months[k]:>9,d}  "
    if i % 4 == 0:
        print(line)
        line = "   "
if line.strip():
    print(line)

fired = sum(y["fired"] for y in years.values())
print(f"""
  ------------------------------------------------------------------
  TOTAL over {len(days)} days: {tot:,} shots
  ------------------------------------------------------------------

  per calendar day        {tot / len(days):>10,.0f}
  per day that fired      {tot / fired if fired else 0:>10,.0f}
  scaled to a full year   {tot / len(days) * 365:>10,.0f}

  days that fired         {fired} of {len(days)}  ({fired / len(days) * 100:.0f} %)
  firing hours on such a day: median {statistics.median(fire_hours):.1f} h, """
      f"""longest {max(fire_hours):.1f} h

  This total is a LOWER BOUND. Every fault in the archive loses readings and
  none can invent one, so the real number is this or higher.""")

if spread:
    bad = sum(1 for s in spread if s > 0.05)
    print(f"""
  How solid it is
    the healthy meters ({', '.join(GOOD)}) disagree by a median of """
          f"""{statistics.median(spread) * 100:.2f} %
    days where they disagree by over 5 %: {bad} of {len(spread)}, """
          f"""carrying {lonely:,} shots ({lonely / tot * 100:.0f} % of the total)
    hours with high power on and no reading at all: {blind_h:.0f}
    hours held over an outage, credited with one shot: {murky_h:.0f}""")

pc2 = sum(rows[d]["pc"]["PC2"]["shots"] for d in days if "PC2" in rows[d]["pc"])
if PC == "best" and pc2:
    print(f"""
  PC2 is left out of the agreement check on purpose: its meter stops writing
  in the middle of nearly every firing day and totals {pc2:,} shots against
  the {tot:,} above. That is a fault in the meter, not in the array.""")
