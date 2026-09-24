"""Probe: does the PV panel fill in the numbers for a REAL frame, off the REAL archiver?

bench_pv_live_multi.py pins the same promise against a FAKE archiver, so it cannot
answer "is the API giving us anything today". This drives the real Viewer, with the
PVs this installation has picked, over frames stamped at genuine shot times read out
of the archiver — no share (the frames are local copies), but every PV number comes
from the live API.

What it prints per PV: the text the panel would show, the frame the value belongs to,
and the gap to the frame on screen. A row reading "n/a" or "wait" here is the
archiver's answer for that instant, not a GUI fault.

  python testing/probe_pv_panel_real.py
  python testing/probe_pv_panel_real.py --pv PTM1 --pv SBW4
"""
import argparse
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

import bench_common as B

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pv", action="append", default=None,
                    help="PV panel name; repeatable. Default: the saved selection.")
    ap.add_argument("--frames", type=int, default=6)
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    m.pv_registry_load()
    names = args.pv or ["PTM1", "Back_Ref", "SBW4"]
    known = set(m.pv_all_names())
    names = [n for n in names if n in known] or ["PTM1"]

    # Real shot instants: the newest samples of the shot channel.
    now_ns = int(time.time() * 1e9)
    shots = cpva.fetch_values(cpva.SHOT_CHANNEL, now_ns - 3600 * 1_000_000_000,
                              now_ns + 5_000_000_000)
    if not shots:
        print("the archiver has no shot samples in the last hour — nothing to probe")
        return 1
    shot_ts = [t for t, _ in shots][-args.frames:]
    print(f"PVs: {names}")
    print("frames stamped at real shots "
          + ", ".join(datetime.fromtimestamp(t / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")
                      for t in shot_ts))

    # TWO cameras on purpose: with one, the Viewer is in single-camera mode and
    # _pv_current_ts reads `items[current_idx]`, which a scan-driven harness does not
    # fill — the panel then answers None and never fetches at all.
    tmp = Path(tempfile.mkdtemp(prefix="is_pvreal_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, 2, 1, hz=1.0)
    for ci, lst in enumerate(cam_folder_lists):
        folder = lst[0]
        src = next(f for f in sorted(folder.glob("*.png")) if f.stat().st_size > 1000)
        for t in shot_ts:
            shutil.copyfile(src, folder / f"{cam_names[ci]}_{t:019d}.png")
        src.unlink()

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = list(names)
    v._pv_hidden = set()

    v._start_multi_cam_scan(cam_names, cam_folder_lists,
                            (shot_ts[0], shot_ts[-1]), False, None)
    if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 60.0):
        print("scan did not finish")
        return 1

    rc = 0
    master = v._per_cam_master_idx
    row = v._per_cam_rows[master]
    for i, t in enumerate(shot_ts):
        # Move the REAL master slider, the way a mouse does, so the frame on screen
        # (and therefore the instant the panel asks the archiver about) is the one
        # being probed. Poking _cam_shown_ts_ns instead is overwritten by the next
        # tile paint, and the panel then keeps answering for the newest frame.
        v._on_per_cam_pressed(master)
        row.set_value(int(i * m.SLIDER_MAX / max(1, len(shot_ts) - 1)))
        v._on_per_cam_value_changed(master, row.value())
        v._on_per_cam_released(master)
        B.wait_for(lambda: (v._cam_shown_ts_ns or [0])[master] == t, 20.0)
        v._pv_last_fetch_mono = 0.0          # defeat the leading-edge rate limit
        v._pv_force_refresh()
        B.wait_for(lambda: getattr(v, "_pv_fetch_inflight", False), 5.0)
        B.wait_for(lambda: not getattr(v, "_pv_fetch_inflight", False), 30.0)
        pump(app, 0.4)
        when = datetime.fromtimestamp(t / 1e9, cpva.TZ_PRAGUE).strftime("%H:%M:%S")
        shown = (v._cam_shown_ts_ns or [0])[master]
        tag = "" if shown == t else (
            f"   [tile is on {datetime.fromtimestamp(shown / 1e9, cpva.TZ_PRAGUE).strftime('%H:%M:%S')}]")
        print(f"\nframe {i + 1}  {when}{tag}")
        for n in names:
            text = v._pv_display_text(n)
            src_ts = v._pv_last_good_ts.get(n)
            gap = f"{(src_ts - t) / 1e9:+.2f}s" if src_ts else "—"
            print(f"  {m.pv_label_for(n):12s} {text!r:22s} asked for frame {gap}")
            if not any(c.isdigit() for c in text):
                rc = 1
    print("\n" + cpva.stats_line())
    print("\nSOME ROWS ABOVE read n/a / wait / ERR — the archiver had nothing for "
          "that instant" if rc else "\nevery PV answered for every frame")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
