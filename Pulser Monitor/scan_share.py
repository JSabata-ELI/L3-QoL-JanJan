"""Run the real pipeline over a real window on the share.

Uses the same file discovery, the same per-frame measurement and the same analyser the
GUI uses — only the Qt plumbing is replaced by a plain loop.

The default window is the morning of 13 Aug 2026, which is where the user's own warm-up
capture (08:20 Prague) and running capture (08:53 Prague) came from, so a warm-up window
followed by normal running is known to be in there.
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP = Path(r"c:\Users\jan.moucka.eli-laser\OneDrive - ELI ERIC\ELI Beamlines\Python"
           r"\programy\L3-QoL-JanJan\Pulser Monitor")
sys.path.insert(0, str(APP))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pulser_monitor as pm  # noqa: E402

DAY = int(os.environ.get("SCAN_DAY", "13"))
H0 = int(os.environ.get("SCAN_H0", "5"))     # UTC
H1 = int(os.environ.get("SCAN_H1", "9"))
CAMS = os.environ.get("SCAN_CAMS", "PD1M1,PD2M1,PD3M1,PD4M1").split(",")

cfg = json.loads((Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json")
                 .read_text(encoding="utf-8"))
root = pm.IMAGES_ROOT_OPTIONS["Lab"]


def find_files(cam):
    folder_name = pm.CAMERAS[cam]
    min_bytes = pm.MIN_IMAGE_BYTES
    out = []
    cur = datetime(2026, 8, DAY, H0, tzinfo=timezone.utc)
    end = datetime(2026, 8, DAY, H1, tzinfo=timezone.utc)
    while cur <= end:
        d = (pm._images_root_for_year(root, cur.year) / str(cur.year) / str(cur.month)
             / str(cur.day) / str(cur.hour) / folder_name)
        if d.is_dir():
            with os.scandir(str(d)) as it:
                for e in it:
                    p = Path(e.path)
                    if p.suffix.lower() not in pm.IMAGE_EXTS:
                        continue
                    try:
                        if p.stat().st_size < min_bytes:
                            continue
                    except OSError:
                        continue
                    ts = pm._parse_ts_from_path(p)
                    if ts is not None:
                        out.append((ts, p))
        cur += timedelta(hours=1)
    out.sort(key=lambda x: x[0])
    return out



def main():
    for cam in CAMS:
        rois = [pm.RoiDefinition.from_dict(r) for r in cfg["cameras"][cam]]
        t0 = time.time()
        files = find_files(cam)
        t_list = time.time() - t0
        if not files:
            print(f"\n=== {cam}: no files")
            continue
        print(f"\n=== {cam}: {len(files)} frames  "
              f"{pm._ns_to_dt(files[0][0]):%Y-%m-%d %H:%M} .. {pm._ns_to_dt(files[-1][0]):%H:%M} "
              f"Prague   (listing {t_list:.1f}s)", flush=True)

        rects = [(r.x, r.y, r.w, r.h) for r in rois]
        tasks = [(ts, str(p)) for ts, p in files]
        samples = []
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1),
                                 initializer=pm._pool_init, initargs=(rects,)) as ex:
            for ts_ns, means, fm, contrasts in ex.map(pm._measure_frame, tasks, chunksize=16):
                if means is not None:
                    samples.append(pm.SamplePoint(ts_ns, None, means, fm,
                                                  roi_contrasts=contrasts))
        dt = time.time() - t0
        print(f"    measured {len(samples)} frames in {dt:.1f}s ({len(samples)/max(dt,1e-9):.0f}/s)",
              flush=True)

        an = pm.analyze_camera(rois, samples, pm.AnalysisParams())
        dead = [s.name for s in an.stats if s.is_dead]
        print(f"    warm-up: {pm._fmt_dur(an.warmup_total_ns)} in {len(an.warmup_windows)} "
              f"window(s), level {an.warm_level_used:.3f} ({an.warm_level_source})")
        for a_ns, b_ns in an.warmup_windows:
            print(f"        {pm._ns_to_dt(a_ns):%H:%M:%S} -> {pm._ns_to_dt(b_ns):%H:%M:%S}")
        # EVERY stretch without data, with what it was read as and why. This is the table
        # that says whether the classification is right: an outage, a stop of ours, and a
        # hiccup in the archive look identical in a count and completely different here.
        print(f"    array outages: {len(an.outages)}   restarts: {an.array_restarts}"
              f"   diodes off: {pm._fmt_dur(an.diodes_off_total_ns)}")
        print(f"    {'start':>8}  {'duration':>10}  {'kind':<12} {'restarted':<9} cause")
        for t in an.trips:
            cause = ", ".join(f"{n}({f}f)" for n, f in t.caused_by) or "-"
            print(f"        {pm._ns_to_dt(t.start_ns):%H:%M:%S}  "
                  f"{pm._fmt_dur(t.duration_ns):>10}  {t.kind_label:<12} "
                  f"{'yes' if t.restarted else 'no':<9} {cause}")
        print(f"    dropouts {sum(s.dropouts for s in an.stats)}  "
              f"faults {sum(s.faults for s in an.stats)}  "
              f"pulser trips {sum(s.trips for s in an.stats)}")
        busy = sorted((s for s in an.stats if s.total_off_events),
                      key=lambda s: -(s.faults * 10 + s.trips * 10 + s.dropouts))[:8]
        for s in busy:
            print(f"        {s.name}: {s.faults} faults, {s.trips} trips, "
                  f"{s.dropouts} dropouts, uptime {s.uptime_pct:.1f}%, "
                  f"dark {pm._fmt_dur(s.dark_time_ns)}")
        print(f"    dead ({len(dead)}): {[pm._dead_label(s) for s in an.stats if s.is_dead]}")
        # Every fault, with the two look-forward measurements the verdict came from, so a
        # fault can be checked against a trip and against a death by reading the times.
        faults = sorted(((ev, s) for s in an.stats for ev in s.events
                         if ev.kind != pm.KIND_DROPOUT),
                        key=lambda es: es[0].start_ns)
        if faults:
            print(f"    fault log ({len(faults)}):")
            for ev, s in faults[:30]:
                print(f"        {pm._ns_to_dt(ev.start_ns):%H:%M:%S}  {s.name:<4} "
                      f"{ev.kind:<8} {pm._fmt_dur(ev.duration_ns(an.times_ns[-1])):>9}  "
                      f"dark={ev.dark_frames:<5} array_fell={'y' if ev.array_fell else 'n'} "
                      f"restarts={ev.restarts}  ended={ev.ended_by}")
        for w in an.warnings:
            print(f"    note: {w[:150]}")


if __name__ == "__main__":
    main()
