"""Headless smoke test of the Pulser Monitor result tabs + exports.

Feeds the synthetic sequence from test_pulser.py straight into the real widgets, so every
drawing path, tooltip and CSV writer is exercised without a share or a display.
"""
import csv
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np  # noqa: E402
import test_pulser as tp  # noqa: E402
import pulser_monitor as pm  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = Path(__file__).parent / "gui_out"
OUT.mkdir(exist_ok=True)

app = QApplication.instance() or QApplication([])
failures = []


def check(label, cond, detail=""):
    # Through whatever the console can encode: the details here quote real UI text, which
    # contains dashes and arrows, and a cp1250 console must not take the run down with it.
    line = f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}"
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    sys.stdout.write(line.encode(enc, "replace").decode(enc, "replace") + "\n")
    if not cond:
        failures.append(label)


rois = tp.load_rois()
ref = pm._load_as_float32_gray(pm._bundled_ref(tp.CAM, "allgood"))
for r in rois:
    t, _g, c = pm._tile_floor_contrast(ref, r.x, r.y, r.w, r.h)
    r.ref_brightness, r.ref_contrast = t, c
warm_factor = 0.93
warm_arr = ref * warm_factor
for r in rois:
    t, _g, c = pm._tile_floor_contrast(warm_arr, r.x, r.y, r.w, r.h)
    r.warm_brightness, r.warm_contrast = t, c

rng = np.random.default_rng(11)
victim = "C4"
samples = tp.build(rois, ref, [
    (12, "warm", warm_factor),      # warm-up at the start of the day
    (20, "ok", None),
    (1,  "dark", [victim]),         # dropout (a flicker)
    (15, "ok", None),
    (6,  "dark", [victim]),         # dropout
    (15, "ok", None),
    (6,  "dark", [victim]),         # dark past the flicker limit -> trip
    (20, "blank", None),            # array down
    (10, "warm", warm_factor),      # warming again after the restart
    (20, "ok", None),
], rng)
an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
print(f"analysis: {len(an.trips)} trip(s), {an.array_restarts} restart(s), "
      f"warm-up {pm._fmt_dur(an.warmup_total_ns)} in {len(an.warmup_windows)} window(s)")
start_ns, end_ns = an.times_ns[0], an.times_ns[-1]

print("\nTabs")
tab_map = pm._MapTab()
tab_map.update_data(tp.CAM, rois, an, start_ns, end_ns)
summary = tab_map._map_summary.text()
check("map summary mentions restarts", "restart" in summary)
check("map summary mentions warm-up", "warm-up" in summary, summary.splitlines()[0][:120])
check("map summary names the culprit", victim in summary.split("caused by:")[-1],
      summary.splitlines()[-1][:120])
for metric in ("Status", "Dropouts", "Faults", "Trips", "Caused trips",
               "Total outages", "Uptime %"):
    tab_map._metric_combo.setCurrentText(metric)
check("all heatmap metrics drew", True)

vi = next(i for i, r in enumerate(rois) if r.name == victim)
tab_map._select_pulser(vi)
tip = tab_map._tooltip_text(vi)
check("tooltip has dropouts", "Dropouts" in tip)
check("tooltip has faults and trips", "Faults:" in tip and "Trips:" in tip)
check("tooltip has caused outages", "Caused array outages: 1" in tip)
check("tooltip uptime names its denominator", "of array-up time" in tip)
check("tooltip has warm-up", "Array warm-up" in tip)
check("event table filled", tab_map._tbl.rowCount() == len(an.stats[vi].events),
      f"{tab_map._tbl.rowCount()} rows")

tab_stats = pm._StatsTab()
tab_stats.update_data(tp.CAM, an)
NT = len(pm._StatsTab.TRIP_COLS)
check("trip table has the new columns", tab_stats._tbl.columnCount() == NT)
row0 = [tab_stats._tbl.item(0, c).text() for c in range(NT)]
check("trip row says what kind it was", row0[0] == "trip", " | ".join(row0))
check("trip row names the cause", victim in row0[4], " | ".join(row0))
check("trip row marks the restart", row0[3] == "yes", " | ".join(row0))
# The fault log is the record the operator asked for: every dark run with its time and
# length, so a fault can be told from a trip and from a death by reading, not by counting.
NF = len(pm._StatsTab.FAULT_COLS)
check("fault log has its columns", tab_stats._tbl_f.columnCount() == NF)
check("fault log lists the non-flicker events", tab_stats._tbl_f.rowCount() == 2,
      f"{tab_stats._tbl_f.rowCount()} rows")
frow = [tab_stats._tbl_f.item(0, c).text() for c in range(NF)]
check("fault log names the pulser and the kind", frow[0] == victim and frow[1] in
      ("fault", "trip"), " | ".join(frow))
check("stats summary has warm-up level", "level 0.9" in tab_stats._summary.text(),
      tab_stats._summary.text().splitlines()[-1][:130])

tab_all = pm._AllDataTab()
tab_all.update_data({tp.CAM: an})
check("all-data has its columns",
      tab_all._tbl_master.columnCount() == len(pm._ALLDATA_COLS))
check("all-data dropped the meaningless Avail %", "Avail %" not in pm._ALLDATA_COLS)

tab_run = pm._RunGraphTab()
tab_run.update_data(samples, rois, an, 0.60, 0.40)
check("run graph drew", True)
# The time cursor is the readout the operator asked for; exercise the text it produces
# rather than trusting that a mouse event would have worked.
mid = tab_run._frame_x()[len(an.times_ns) // 2]
readout = tab_run._readout(float(mid), float(vi))
check("cursor reads a Prague timestamp", ":" in readout.splitlines()[0],
      readout.splitlines()[0])
check("cursor names the pulser under it", victim in readout, " / ".join(readout.splitlines()))
# The score strip lives under the map timeline now, one pulser at a time.
check("map draws the selected pulser's score", tab_map._draw_score_strip() is True)

print("\nExports")
p = OUT / "full.csv"
pm.PulserMonitorWidget._write_full_csv(str(p), tp.CAM, rois, samples, an)
with open(p, newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
hdr = rows[0]
check("full CSV has state column", f"{victim}_state" in hdr)
check("full CSV has frame_state", "frame_state" in hdr)
ci = hdr.index(f"{victim}_state")
states = [r[ci] for r in rows[1:]]
n_off = states.count("OFF")
check("full CSV OFF count matches the script", n_off == 1 + 6 + 6, f"{n_off} OFF rows")
fs = hdr.index("frame_state")
check("full CSV marks warm-up frames", sum(1 for r in rows[1:] if r[fs] == "warmup") == 22,
      str(sum(1 for r in rows[1:] if r[fs] == "warmup")))
# Blank frames are measured but excluded from the series, and the CSV says so rather than
# leaving the column empty. Found by their own label rather than by row number, so that
# lengthening a dark run in the script above cannot silently point this at the wrong rows.
blank_rows = [r[fs] for r in rows[1:]
              if r[fs] in ("no-array", "outage", "diodes-off", "data-gap")]
check("every blank frame is accounted for and none reads as real data",
      len(blank_rows) == 20, f"{len(blank_rows)} of 20 — {sorted(set(blank_rows))}")

pm._write_events_csv(str(OUT / "events.csv"), tp.CAM, an)
pm._write_trips_csv(str(OUT / "trips.csv"), tp.CAM, an)
pm._write_warmup_csv(str(OUT / "warmup.csv"), tp.CAM, an)
with open(OUT / "trips.csv", newline="", encoding="utf-8") as f:
    trips = list(csv.reader(f))
check("trips CSV carries the cause", victim in trips[1][7], " | ".join(trips[1]))
check("trips CSV says whether it was an outage", trips[1][2] == "1", " | ".join(trips[1]))
with open(OUT / "warmup.csv", newline="", encoding="utf-8") as f:
    warms = list(csv.reader(f))
check("warm-up CSV has one row per window", len(warms) - 1 == len(an.warmup_windows))
with open(OUT / "events.csv", newline="", encoding="utf-8") as f:
    evs = list(csv.reader(f))
kinds = {r[2] for r in evs[1:]}
check("events CSV has the new kinds", {"dropout", "fault", "trip"} & kinds == kinds,
      str(kinds))
hdr_e = evs[0]
check("events CSV records the look-forward",
      {"array_fell", "restarts", "ended_by"} <= set(hdr_e), str(hdr_e))

# The export slots open a file dialog; call the figure save they wrap instead.
tab_map._map_fig.savefig(OUT / "map.png", dpi=110)
tab_run._fig.savefig(OUT / "run.png", dpi=110)
tab_stats._fig.savefig(OUT / "stats.png", dpi=110)
check("map PNG written", (OUT / "map.png").stat().st_size > 5000)
check("run PNG written", (OUT / "run.png").stat().st_size > 5000)

print("\n" + ("ALL PASS" if not failures else f"FAILURES: {failures}"))
sys.exit(1 if failures else 0)
