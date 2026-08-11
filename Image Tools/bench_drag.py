"""Measure a multi-camera slider drag, headless.

Drives the real master per-camera slider through the real signals (pressed / valueChanged
/ released), so the scrub throttle, the preview layer, the per-camera load depth and the
paint guards all run exactly as they do under a mouse.

The question it answers is the one the user actually asked: do all cameras change together
while the slider moves, and does the picture match the handle?

  python bench_drag.py                          # synthetic data, 4 cams x 600 frames
  python bench_drag.py --cams 4 --frames 2000
  python bench_drag.py --wait-prox 0            # drag with a COLD preview (the bad case)
  python bench_drag.py --root "\\\\users-L3...\\2026\\08\\11\\09"   # real share folders

--wait-prox is the important knob: 0 reproduces "opened a window and dragged immediately",
1.0 waits for the preview to finish first. Run both — the fix has to help the cold case,
which is where the original complaint lives.
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
    ap.add_argument("--frames", type=int, default=600,
                    help="synthetic frames per camera (ignored with --root)")
    ap.add_argument("--root", default=None,
                    help="folder holding one subfolder per camera; synthetic data if unset")
    ap.add_argument("--keep", action="store_true", help="keep the synthetic data")
    ap.add_argument("--wait-prox", type=float, default=1.0,
                    help="preview fraction to reach before dragging (0 = drag cold)")
    ap.add_argument("--sweeps", type=int, default=2, help="drag passes over the window")
    ap.add_argument("--drag-s", type=float, default=3.0, help="seconds per pass")
    ap.add_argument("--steps", type=int, default=90, help="slider moves per pass")
    ap.add_argument("--latency-ms", type=float, default=145.0,
                    help="simulated per-read share latency; 0 = raw local speed. The "
                         "default is what \\\\users-L3 actually costs, and without it the "
                         "benchmark cannot see the concurrency limits at all.")
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication

    app = QApplication.instance() or QApplication([])

    tmp = None
    if args.root:
        root = Path(args.root)
        cam_names, cam_folder_lists = [], []
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            cam_names.append(d.name)
            cam_folder_lists.append([d])
        cam_names = cam_names[:args.cams]
        cam_folder_lists = cam_folder_lists[:args.cams]
        print(f"real data: {len(cam_names)} cameras under {root}")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="is_bench_"))
        print(f"writing {args.cams} x {args.frames} synthetic frames to {tmp} …")
        cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)

    # After the data is written, before anything reads it.
    B.install_read_latency(m, args.latency_ms)
    if args.latency_ms:
        print(f"simulating {args.latency_ms:.0f} ms per frame read")

    v = m.Viewer()
    # The per-minute diag log would fight the ledger for the GUI thread.
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        # An explicit axis spanning exactly the data. Left to _choose_axis it snaps to whole
        # hours, so a few minutes of synthetic frames occupy ~7 % of the slider's travel and
        # every other position clamps to the first or last frame — which looks like a huge
        # |painted - requested| distance that is an artefact of the harness, not the app.
        # Real windows are opened by the hour, so their data fills the axis.
        axis = None
        if not args.root:
            all_ts = []
            for lst in cam_folder_lists:
                for f in lst:
                    for p in f.glob("*.png"):
                        t = m.parse_unix_ns_from_name(p)
                        if t:
                            all_ts.append(t)
            if all_ts:
                axis = (min(all_ts), max(all_ts))
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        ok = B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 180.0)
        if not ok:
            print("scan did not finish — no frames found")
            return 1
        n = min(len(c) for c in v._cam_ts)
        print(f"scanned: {[len(c) for c in v._cam_ts]} frames per camera")

        if args.wait_prox > 0:
            print(f"waiting for the preview to reach {args.wait_prox*100:.0f} % …")
            B.wait_for(lambda: B.proxy_fraction(v) >= args.wait_prox, 600.0)
        print(f"preview at {B.proxy_fraction(v)*100:.0f} % — starting the drag")

        master = v._per_cam_master_idx
        row = v._per_cam_rows[master]
        v._bench.clear()
        v._diag_prev = v._diag_miss = v._diag_load = v._diag_cach = 0

        asked = {}
        t0 = time.monotonic()
        for s in range(args.sweeps):
            v._on_per_cam_pressed(master)
            # Alternate direction: a BACKWARD drag is what the old `idx >= cur` paint guard
            # rejected outright, so a forward-only benchmark could not see that bug.
            for k in range(args.steps):
                frac = k / max(1, args.steps - 1)
                if s % 2:
                    frac = 1.0 - frac
                row.set_value(int(frac * m.SLIDER_MAX))
                # set_value blocks signals (it exists to move the handle without a render),
                # so emit what a mouse would.
                v._on_per_cam_value_changed(master, row.value())
                elapsed_in_sweep = frac if s % 2 == 0 else 1 - frac
                deadline = t0 + (s * args.drag_s) + elapsed_in_sweep * args.drag_s
                while time.monotonic() < deadline:
                    QCoreApplication.processEvents()
                    time.sleep(0.002)
                    B.note_targets(v, asked)
                B.note_targets(v, asked)
            v._on_per_cam_released(master)
            B.wait_for(lambda: False, 0.4)   # let the settle render land
        elapsed = time.monotonic() - t0

        res = B.report(v, cam_names, elapsed,
                       f"DRAG  {len(cam_names)} cams x {n} frames", asked=asked)

        rates = [r.get("paints_per_s", 0) for r in res.values()]
        shown = [r.get("shown_frac", 0) for r in res.values()]
        p95s = [r.get("dt_p95_s", 0) for r in res.values()]
        # The paint-rate target is RELATIVE to how fast the harness moved the slider, not an
        # absolute number: with 60 steps over 4 s the slider only asks for 15 positions a
        # second, so 15 paints/s is everything it asked for and an absolute ">= 25" would
        # fail a perfect run. What is absolute is the coalescing ceiling — a tick cannot
        # paint more than 1000/NAV_TICK_MS positions per second however fast you drag.
        # 0.8, not 0.9: when the request rate is near the tick rate, jitter puts two
        # requests inside some ticks and none in others, and two inside one tick correctly
        # collapse to one paint. shown/asked below is the authoritative correctness check —
        # this one only catches a rate collapse.
        req_rate = (args.steps * args.sweeps) / elapsed if elapsed else 0.0
        ceiling = 1000.0 / m.NAV_TICK_MS
        want_rate = 0.8 * min(req_rate, ceiling)
        print(f"\nslider asked for {req_rate:.1f} positions/s; the {m.NAV_TICK_MS} ms "
              f"navigation tick can paint at most {ceiling:.0f}/s")
        print(f"targets: >={want_rate:.1f} paints/s per camera, spread >=70 %, "
              f"p95 |dt| <= 2.0 s, shown/asked >=90 %")
        bad = []
        if rates and min(rates) < want_rate:
            bad.append(f"slowest camera {min(rates):.1f}/s")
        if rates and max(rates) and min(rates) / max(rates) < 0.7:
            bad.append(f"spread {min(rates)/max(rates)*100:.0f} %")
        if p95s and max(p95s) > 2.0:
            bad.append(f"p95 |dt| {max(p95s):.2f} s")
        if shown and min(shown) < 0.9:
            bad.append(f"shown/asked {min(shown)*100:.0f} %")
        print("VERDICT:", "ok" if not bad else "MISSED — " + ", ".join(bad))
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
