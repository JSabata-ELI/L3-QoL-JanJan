"""How much of the year's total rests on days where the meters disagree?"""
import json
from pathlib import Path

rows = json.loads(Path(__file__).with_name("shot_counts_cache.json")
                  .read_text("utf-8"))
GOOD = ("PC1", "PC3", "PC4")

tot = bad_tot = 0
bad = []
for d in sorted(rows):
    v = [rows[d]["pc"][k]["shots"] for k in GOOD if k in rows[d]["pc"]]
    v = [x for x in v if x > 0]
    if not v:
        continue
    hi = max(v)
    tot += hi
    if len(v) > 1 and (hi - min(v)) / hi > 0.05:
        bad.append((d, v, (hi - min(v)) / hi))
        bad_tot += hi

print(f"total (best of PC1/PC3/PC4): {tot:,}")
print(f"days where they disagree by more than 5 %: {len(bad)}, "
      f"carrying {bad_tot:,} shots ({bad_tot/tot*100:.1f} % of the total)\n")
for d, v, s in bad:
    print(f"   {d}  {v}   spread {s*100:5.1f} %")
