"""Assert a SETTLED archive-mode grid reaches native resolution and then goes quiet.

The report: in archive mode a tile stayed visibly soft after the slider was released —
C03-036-PTM11wFF looked badly out of focus while the file behind it was sharp.

The diag log said what was happening. Three consecutive idle minutes of a 6-camera
archive window:

    prev=1090  hq=0  prox=100%
    prev=1758  hq=0  prox=100%
    prev=1752  hq=0  prox=100%

`hq=0` — the native upgrade (`_hq_upgrade_tiles`) never ran once — and `prev≈1750/min`
= 29 preview repaints a second with the panel standing completely still. Both come from
one loop:

    _refine_current_frame (200 ms)
      -> _display_multicam_index
        -> _proxy_try_paint_cam paints the tile from the preview
          -> _schedule_refine  -> re-arms the 200 ms refine AND the 500 ms HQ tier

6 tiles x 5 Hz = 30 paints/s, which is the measured 29.2. And because `_schedule_hq`
restarts rather than extends, a deadline 500 ms out that is pushed back every 200 ms can
never expire: the tiles were pinned at preview resolution for as long as the window stayed
open. Live mode was unaffected (it re-arms from arriving frames and had hq=37..122/min),
which is why this only ever showed up in archive mode.

Two things are asserted after a release, both on the real widgets driven through the real
signals:
  * the tiles reach HQ_SIDE — `hq` paints happen at all;
  * an idle panel stops repainting — the preview counter stays put instead of climbing.

Runs offscreen against synthetic local frames — no share, no network:

    python testing/test_settle_native.py
    python testing/test_settle_native.py --cams 6 --frames 40
"""
import argparse
import shutil
import tempfile
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def idle(seconds: float):
    """Spin the event loop without touching the panel — the state the bug lives in."""
    from PySide6.QtCore import QCoreApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.002)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=6)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_settle_"))
    print(f"writing {args.cams} x {args.frames} synthetic frames to {tmp} …")
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)

    v = m.Viewer()
    # The per-minute diag log resets the very counters this test reads.
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        all_ts = []
        for lst in cam_folder_lists:
            for f in lst:
                for p in f.glob("*.png"):
                    t = m.parse_unix_ns_from_name(p)
                    if t:
                        all_ts.append(t)
        axis = (min(all_ts), max(all_ts)) if all_ts else None
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 180.0):
            print("scan did not finish — no frames found")
            return 1
        print(f"scanned: {[len(c) for c in v._cam_ts]} frames per camera")
        check("archive mode, not live", not v._online_mode, f"online={int(v._online_mode)}")

        B.wait_for(lambda: B.proxy_fraction(v) >= 1.0, 300.0)
        print(f"preview at {B.proxy_fraction(v)*100:.0f} %")

        master = v._per_cam_master_idx
        check("a master camera drives the shared slider", master >= 0, f"master={master}")
        row = v._per_cam_rows[master]

        # One short drag, then release — the gesture the complaint follows.
        v._on_per_cam_pressed(master)
        for k in range(20):
            row.set_value(int(k / 19 * m.SLIDER_MAX))
            v._on_per_cam_value_changed(master, row.value())
            idle(0.02)
        v._on_per_cam_released(master)

        # ---- [1] the settled grid must reach native resolution ------------------
        print(f"\n[1] after the release, the tiles must reach native "
              f"(HQ_SETTLE_MS={m.HQ_SETTLE_MS} ms)")
        v._diag_hq = 0
        idle(3.0)
        check("the native upgrade ran", v._diag_hq > 0, f"hq={v._diag_hq}")
        # Every visible tile, not just one: the loop starved them all equally, so a single
        # upgraded tile would still leave the reported symptom on screen.
        shown_hq = sum(1 for k in (v._cam_shown_key or []) if k and k[1] == m.HQ_SIDE)
        check("every tile is shown at HQ_SIDE", shown_hq == args.cams,
              f"{shown_hq}/{args.cams} tiles at HQ_SIDE")

        # ---- [2] an idle panel must stop repainting ------------------------------
        print("\n[2] with nothing moving, the preview layer must go quiet")
        before = v._diag_prev
        idle(2.0)
        rate = (v._diag_prev - before) / 2.0
        # The loop ran at cams x 1000/PROXY_REFINE_MS. Anything near that is the loop back;
        # a couple of stragglers from the release are not, hence a rate rather than == 0.
        loop_rate = args.cams * 1000.0 / m.PROXY_REFINE_MS
        check("no self-sustaining refine loop", rate < loop_rate * 0.25,
              f"{rate:.1f} preview paints/s (the loop ran at ~{loop_rate:.0f}/s)")

        # ---- [3] the settled tiles must hold the frame that was ASKED for -------
        # The follow-up report: one tile "moves oddly" while the slider is dragged and
        # released — is some other frame (the newest one?) quietly being shown? The
        # preview layer is allowed to substitute a NEIGHBOUR frame while the handle is
        # moving (_proxy_try_paint_cam accepts anything within its tolerance), and while
        # the refine loop was alive that substitution was repainted 5x/s forever, so a
        # tile could sit on a neighbouring moment for as long as the window was open.
        # After a settle there is nothing left to excuse it: shown must equal target.
        print("\n[3] every settled tile must show the exact frame it was asked for")
        off = []
        for i in range(args.cams):
            shown  = v._cam_shown_ts_ns[i]  if i < len(v._cam_shown_ts_ns)  else 0
            target = v._cam_target_ts_ns[i] if i < len(v._cam_target_ts_ns) else 0
            if shown != target:
                off.append(f"cam{i} shown-target={(shown - target)/1e9:+.3f}s")
        check("no tile is left on a substituted frame", not off, ", ".join(off))
        approx = [i for i, a in enumerate(v._cam_paint_preview or []) if a]
        check("no tile still flagged approximate", not approx, f"cams={approx}")

        print("\n" + ("ALL PASS" if not FAILURES else
                      "FAILURES:\n  " + "\n  ".join(FAILURES)))
        return 1 if FAILURES else 0
    finally:
        try:
            v._hard_reset_runtime()
        except Exception:
            pass
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
