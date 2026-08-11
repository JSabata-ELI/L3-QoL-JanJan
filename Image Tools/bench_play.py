"""Measure multi-camera playback, headless.

Drives the real Play/Stop at each speed and reports, per camera, how many frames actually
reached the screen against how many the speed setting implies. Playback's failure mode is
not latency but DROPS — the file names scroll while the picture barely moves — so the
number that matters here is shown-vs-expected, not paints/s.

  python bench_play.py
  python bench_play.py --speeds 0.25 1 5 --duration 4
  python bench_play.py --root "\\\\users-L3...\\2026\\08\\11\\09"
"""
import argparse
import shutil
import tempfile
import time
from pathlib import Path

import bench_common as B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=4)
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--root", default=None)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--speeds", type=float, nargs="+", default=[0.5, 1.0, 5.0],
                    help="%%/s settings to test (must exist in the Speed combo)")
    ap.add_argument("--duration", type=float, default=4.0, help="seconds per speed")
    ap.add_argument("--wait-prox", type=float, default=1.0)
    ap.add_argument("--latency-ms", type=float, default=145.0)
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication
    app = QApplication.instance() or QApplication([])

    tmp = None
    if args.root:
        root = Path(args.root)
        dirs = sorted(p for p in root.iterdir() if p.is_dir())[:args.cams]
        cam_names = [d.name for d in dirs]
        cam_folder_lists = [[d] for d in dirs]
        axis = None
    else:
        tmp = Path(tempfile.mkdtemp(prefix="is_bench_"))
        print(f"writing {args.cams} x {args.frames} synthetic frames …")
        cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)
        all_ts = [m.parse_unix_ns_from_name(p)
                  for lst in cam_folder_lists for f in lst for p in f.glob("*.png")]
        all_ts = [t for t in all_ts if t]
        axis = (min(all_ts), max(all_ts)) if all_ts else None

    B.install_read_latency(m, args.latency_ms)
    if args.latency_ms:
        print(f"simulating {args.latency_ms:.0f} ms per frame read")

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 180.0):
            print("scan did not finish")
            return 1
        n = min(len(c) for c in v._cam_ts)
        print(f"scanned: {[len(c) for c in v._cam_ts]} frames per camera")
        if args.wait_prox > 0:
            B.wait_for(lambda: B.proxy_fraction(v) >= args.wait_prox, 600.0)
        print(f"preview at {B.proxy_fraction(v)*100:.0f} %")

        master = v._per_cam_master_idx
        for pct in args.speeds:
            idx = next((i for i in range(v.speed_cb.count())
                        if abs(v.speed_cb.itemData(i) - pct) < 1e-9), None)
            if idx is None:
                opts = [v.speed_cb.itemData(i) for i in range(v.speed_cb.count())]
                print(f"\nspeed {pct} %/s is not offered (available: {opts}) — skipped")
                continue
            v.speed_cb.setCurrentIndex(idx)

            # Rewind to the start so every speed gets the same runway.
            v._per_cam_rows[master].set_value(0)
            v._on_per_cam_value_changed(master, 0)
            B.wait_for(lambda: False, 0.5)

            v._bench.clear()
            v._diag_prev = v._diag_miss = v._diag_load = v._diag_cach = 0
            asked = {}
            t0 = time.monotonic()
            v.play()
            tick = v.play_timer.interval()
            while time.monotonic() - t0 < args.duration and v._is_playing:
                QCoreApplication.processEvents()
                B.note_targets(v, asked)
                time.sleep(0.002)
            v.stop()
            elapsed = time.monotonic() - t0

            res = B.report(v, cam_names, elapsed,
                           f"PLAY {pct} %/s  ({tick} ms tick)  {len(cam_names)} cams "
                           f"x {n} frames", asked=asked)
            # The speed setting is a wall-clock rate, so this is how many frames it implies.
            want_frames = pct / 100.0 * n * elapsed
            per_tick = want_frames / max(1.0, elapsed * 1000.0 / tick)
            print(f"{pct} %/s over {n} frames implies {want_frames:.0f} frames in "
                  f"{elapsed:.1f} s = stride {per_tick:.2f} per {tick} ms tick")
            if per_tick > 1.05:
                print(f"  -> skipping is unavoidable at this setting: "
                      f"~{(1 - 1/per_tick)*100:.0f} % of frames cannot be shown")
            shown = [r.get("shown_frac", 0) for r in res.values()]
            rates = [r.get("paints_per_s", 0) for r in res.values()]
            if rates and max(rates):
                print(f"  spread {min(rates)/max(rates)*100:.0f} %, "
                      f"shown/asked {min(shown)*100:.0f} %")
        return 0
    finally:
        try:
            v._hard_reset_runtime()
        except Exception:
            pass
        if tmp and not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
