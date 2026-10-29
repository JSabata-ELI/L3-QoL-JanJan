"""Assert an archive-mode grid sharpens to native after EVERY kind of stop, camera by camera.

The report (27.10.2026): in archive mode, stopping somewhere on the slider must always end
with every camera at full resolution. Two ways it did not:

  [1] Arrow / wheel / frame-button steps in the per-camera layout went through
      _per_cam_nav_tick, which never armed the native tier. Only a PREVIEW paint did, so a
      tile served from the cache or by a real read (zoomed, Binary palette, preview not yet
      covering that moment) stayed at tile size for good.
  [2] A tile-size read of the same frame that finished AFTER the native read had been
      claimed went through _redraw_cam_in_place, which put the tile back to tile size.
      The native render then landed, was rejected as stale, and the tile stayed blurry
      with the sharp picture unused in the cache.

Runs offscreen against synthetic local frames — no share, no network:

    python testing/test_archive_native_after_step.py
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
    from PySide6.QtCore import QCoreApplication
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.002)


def tiles_at_native(m, v, n):
    return sum(1 for k in (v._cam_shown_key or [])[:n] if k and k[1] == m.HQ_SIDE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=4)
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_native_step_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        all_ts = [m.parse_unix_ns_from_name(p)
                  for lst in cam_folder_lists for f in lst for p in f.glob("*.png")]
        all_ts = [t for t in all_ts if t]
        v._start_multi_cam_scan(cam_names, cam_folder_lists,
                                (min(all_ts), max(all_ts)), False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 180.0):
            print("scan did not finish — no frames found")
            return 1
        check("archive mode, not live", not v._online_mode)
        check("per-camera layout", bool(v._per_cam_rows) and v._per_cam_master_idx >= 0)
        idle(2.0)

        # ---- [1] arrow steps with the preview unable to serve -------------------
        print("\n[1] arrow steps, preview refusing (zoomed / Binary): tiles must reach native")
        real_proxy = v._proxy_try_paint_cam
        v._proxy_try_paint_cam = lambda *a, **k: False
        try:
            for _ in range(3):
                v._per_cam_step(1)
                idle(0.05)
            idle(0.2)
            check("tiles left at tile size right after the steps",
                  tiles_at_native(m, v, args.cams) < args.cams,
                  f"{tiles_at_native(m, v, args.cams)}/{args.cams} native")
            idle(3.0)
            n_hq = tiles_at_native(m, v, args.cams)
            check("every tile native after the stop", n_hq == args.cams,
                  f"{n_hq}/{args.cams} native")
        finally:
            v._proxy_try_paint_cam = real_proxy

        # ---- [2] a late tile-size read must not undo the native claim -----------
        print("\n[2] a late tile-size read of the same frame lands after the native claim")
        cam = 0
        native_key = v._cam_shown_key[cam]
        # Drop the native render from the cache so the stale arrival takes the
        # "wanted render not cached" branch — the one that used to downgrade.
        v._cam_caches[cam]._d.pop(native_key, None)
        idx = v._cam_current_idx[cam]
        small_side = v._cam_tile_side(args.cams)
        small_key = (native_key[0], small_side) + tuple(native_key[2:])
        img = QImage(64, 64, QImage.Format.Format_RGB32)
        img.fill(0)
        d = v._cam_disp_get(cam)
        v._on_cam_loaded(cam, v._gen, -1, idx, small_side, d["brighten"], d["gid"],
                         d["bc"], img, small_key)
        k_after = v._cam_shown_key[cam]
        check("tile still waits for the native render",
              bool(k_after) and k_after[1] == m.HQ_SIDE,
              f"shown side={k_after[1] if k_after else None}")
        idle(3.0)
        k_end = v._cam_shown_key[cam]
        got = v._cam_caches[cam].get(k_end) if k_end else None
        check("native render read again and shown",
              bool(k_end) and k_end[1] == m.HQ_SIDE and got is not None,
              f"shown side={k_end[1] if k_end else None}, cached={got is not None}")

        print("\n" + ("ALL PASS" if not FAILURES else
                      "FAILURES:\n  " + "\n  ".join(FAILURES)))
        return 1 if FAILURES else 0
    finally:
        try:
            v._hard_reset_runtime()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
