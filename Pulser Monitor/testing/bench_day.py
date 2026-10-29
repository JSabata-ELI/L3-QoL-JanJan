"""One real day through the program's own pipeline, without the window.

Same steps as pressing Scan with the default settings: the active-time gate (07-21 h and
the high-power key), the full-power gate (dump bus + energy of all four arrays), the
listing of every hour folder, the reading plan, the per-frame measurement and the
analyser. Prints how long each step took and the day stretch by stretch — the same table
the Statistics tab shows — and writes the exports plus a pickle of each result (for
render_tabs.py) to the output folder.

This reads the share and decodes frames for minutes: it is a heavy job. Ask first.

    python testing/bench_day.py --day 2026-10-22 --cams PD1M1,PD3M1,PD4M1
    python testing/bench_day.py --day 2026-10-22 --every-frame     (energy-guided reading off)
"""
import argparse
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as pm  # noqa: E402


def _say(text: str) -> None:
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace") + "\n")
    sys.stdout.flush()


def list_files(cam: str, start_ns: int, end_ns: int, active) -> list:
    """The program's own hour-folder listing (`_FileScanWorker`), run synchronously."""
    got: list = []
    sig = pm._FileScanSignals()
    sig.files_ready.connect(lambda files: got.extend(files))
    sig.error.connect(lambda msg: _say(f"    listing error: {msg}"))
    pm._FileScanWorker(sig, pm.IMAGES_ROOT_OPTIONS["Lab"], pm.CAMERAS[cam], start_ns, end_ns,
                       pm.MIN_IMAGE_BYTES, [0], 0, active).run()
    return got


def measure(rois: list, files: list, workers: int) -> list:
    rects = [(r.x, r.y, r.w, r.h) for r in rois]
    tasks = [(ts, str(p)) for ts, p in files]
    out = []
    with ProcessPoolExecutor(max_workers=workers, initializer=pm._pool_init,
                             initargs=(rects,)) as ex:
        for (ts, means, fm, con), (_t, src) in zip(
                ex.map(pm._measure_frame, tasks, chunksize=16), tasks):
            if means is not None:
                out.append(pm.SamplePoint(ts, Path(src), means, fm, roi_contrasts=con))
    out.sort(key=lambda sp: sp.ts_ns)
    return out


def hms(ns: int) -> str:
    return pm._ns_to_dt(ns).strftime("%H:%M:%S")


def report(cam: str, an) -> None:
    _say(f"  {'#':>2}  {'from - to':<19} {'length':>7}  {'low power':<12} "
         f"{'dark from start':<16} {'full':>4} {'part':>4} {'unexpl':>6}  per pulser")
    for s in an.stretches:
        low = f"{s.low_power_n}x {s.low_power_ns / 6e10:.1f}m" if s.low_power_n else "-"
        if not s.judged:
            _say(f"  {s.index:>2}  {hms(s.start_ns)}-{hms(s.end_ns)} "
                 f"{(s.end_ns - s.start_ns) / 6e10:>6.0f}m  {low:<12} NOT JUDGED: {s.note}")
            continue
        per = ", ".join(f"{n} {k}x" for n, k in
                        sorted(s.per_pulser.items(), key=lambda nk: -nk[1])) or "-"
        un = "n/a" if s.unexplained < 0 else str(s.unexplained)
        _say(f"  {s.index:>2}  {hms(s.start_ns)}-{hms(s.end_ns)} "
             f"{(s.end_ns - s.start_ns) / 6e10:>6.0f}m  {low:<12} "
             f"{', '.join(s.dark_from_start) or '-':<16} {s.blinks:>4} {s.partial:>4} "
             f"{un:>6}  {per}")
    tot = ", ".join(f"{n} {k}x" for n, k in an.worst_blinkers(40)) or "none"
    _say(f"  whole day {cam}: {an.blinks_total} blinks ({an.partial_total} partial): {tot}")
    _say(f"  dead: {[pm._dead_label(s) for s in an.stats if s.is_dead]}")
    if an.unexplained_dips:
        _say("  energy dips with no pulser: " + ", ".join(
            f"{hms(t)} {d * 100:+.1f}%" for t, d in an.unexplained_dips))
    _say(f"  judged {pm._fmt_dur(an.array_up_ns)}; outages {len(an.outages)}; "
         f"warm-up {pm._fmt_dur(an.warmup_total_ns)} in {len(an.warmup_windows)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-22")
    ap.add_argument("--cams", default="PD1M1,PD2M1,PD3M1,PD4M1")
    # The program's own pool size, so the timing is the one a Scan would see.
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--every-frame", action="store_true")
    ap.add_argument("--out", default=str(Path(os.environ.get("TEMP", "C:/Temp"))
                                         / "pulser_bench"))
    args = ap.parse_args()

    from PySide6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    out = Path(args.out) / args.day
    out.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((Path(os.environ["APPDATA"]) / "PulserMonitor" / "rois.json")
                     .read_text(encoding="utf-8"))
    tz = pm.TZ_PRAGUE
    d = datetime.fromisoformat(args.day)
    start_ns = int(datetime(d.year, d.month, d.day, 0, tzinfo=tz).timestamp()) * pm._NS_PER_S
    end_ns = int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz).timestamp()) \
        * pm._NS_PER_S
    params = pm.AnalysisParams(gate_hours=True, gate_high_power=True, gate_full_power=True,
                               energy_guided=not args.every_frame)

    t_all = time.time()
    t0 = time.time()
    active, excluded, notes, hpe_failed = pm.active_spans(start_ns, end_ns)
    _say(f"gate: {pm._fmt_dur(active.total_ns)} active ({time.time() - t0:.1f}s)")
    for n in notes:
        _say(f"  {n}")
    t0 = time.time()
    power = pm.full_power_info(start_ns, end_ns, params, active)
    _say(f"full power read in {time.time() - t0:.1f}s; failed={power.failed}")
    for n in power.notes:
        _say(f"  {n}")
    _say(f"  low-power stretches, all arrays: {len(power.low_all)}")

    for cam in [c.strip() for c in args.cams.split(",") if c.strip()]:
        rois = [pm.RoiDefinition.from_dict(r) for r in cfg["cameras"][cam]]
        _say(f"\n=== {cam}")
        t0 = time.time()
        files = list_files(cam, start_ns, end_ns, active)
        t_list = time.time() - t0
        cp = power.cams.get(cam)
        if cp is not None and cp.failed:
            cp = None
        kept, note = pm.plan_reading(files, cp, params)
        _say(f"  listed {len(files)} in {t_list:.1f}s; {note}")
        t0 = time.time()
        samples = measure(rois, kept, args.workers)
        t_meas = time.time() - t0
        _say(f"  measured {len(samples)} in {t_meas:.1f}s "
             f"({len(samples) / max(t_meas, 1e-9):.0f} frames/s)")
        t0 = time.time()
        an = pm.analyze_camera(rois, samples, params, excluded=excluded, gate_notes=notes,
                               gate_hpe_failed=hpe_failed, power=cp,
                               low_power=power.low_all,
                               gate_power_failed=cp is None,
                               window_ns=(start_ns, end_ns))
        _say(f"  analysed in {time.time() - t0:.1f}s")
        report(cam, an)
        pm._write_events_csv(str(out / f"pulser_events_{cam}.csv"), cam, an)
        pm._write_stretches_csv(str(out / f"pulser_stretches_{cam}.csv"), cam, an)
        pm._write_trips_csv(str(out / f"pulser_trips_{cam}.csv"), cam, an)
        with open(out / f"{cam}.pkl", "wb") as f:
            pickle.dump({"cam": cam, "rois": rois, "samples": samples, "analysis": an,
                         "start_ns": start_ns, "end_ns": end_ns}, f)
    _say(f"\nall done in {(time.time() - t_all) / 60:.1f} min -> {out}")


if __name__ == "__main__":
    main()
