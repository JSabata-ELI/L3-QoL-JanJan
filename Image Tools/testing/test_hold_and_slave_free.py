"""Two multi-camera navigation rules, asserted on the real widgets and the real signals.

[A] A SLAVE SLIDER STAYS WHERE IT WAS PUT.

    The report: with two cameras, camera A picked as master and the panel in archive mode,
    moving camera B's slider and letting go put B back on A's moment a moment later.

    It was the 200 ms settle pass. `_refine_current_frame`'s multi-cam branch re-rendered
    through `_display_multicam_index`, which resolves EVERY camera from ONE merged index —
    so the pass that only meant to sharpen the picture moved it as well, undoing the move
    the user had just made. Only the independent (no master) mode bailed out of that
    branch, which left the bug in exactly the mode with a master selected. The settle pass
    now redraws each tile on its own frame (`_per_cam_redraw_in_place`), and a slave is
    re-aimed by one thing only: the MASTER moving.

[B] HOLDING A FRAME ARROW RAMPS UP.

    A press is one frame, as it always was. Held down, the rate climbs one rung a second —
    2, 3, 4 then 5 images per second — and if it stays down it opens up further, to 8/s
    after five seconds and 10/s after eight (HOLD_STEP_RAMP). It stops the moment the
    button comes back up.

Runs offscreen against synthetic local frames — no share, no network:

    python testing/test_hold_and_slave_free.py
    python testing/test_hold_and_slave_free.py --cams 3 --frames 200
"""
import argparse
import bisect
import tempfile
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def handle_ts(v, cam: int) -> int:
    """Which frame of camera `cam` its handle is parked on — NEAREST, because
    _per_cam_ts_to_slider truncates and an at-or-before resolve would name the frame
    before the one the handle was placed on."""
    cam_ts = v._cam_ts[cam]
    raw = v._per_cam_slider_to_ts(cam, v._per_cam_rows[cam].value())
    pos = bisect.bisect_left(cam_ts, raw)
    best = None
    for i in (pos - 1, pos, pos + 1):
        if 0 <= i < len(cam_ts) and (best is None
                                     or abs(cam_ts[i] - raw) < abs(cam_ts[best] - raw)):
            best = i
    return cam_ts[best]


def master_frame(v) -> int:
    """The master's frame index as the navigation itself tracks it (_nav_frame is written
    synchronously; _cam_current_idx only when the deferred tick runs)."""
    mi = v._per_cam_master_idx
    return v._nav_frame.get(mi, v._cam_current_idx[mi])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=2)
    ap.add_argument("--frames", type=int, default=200)
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_hold_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)
    all_ts = [t for lst in cam_folder_lists for f in lst for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    axis = (min(all_ts), max(all_ts))

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
            print("scan did not finish")
            return 1
        print(f"scanned: {[len(c) for c in v._cam_ts]} frames per camera")
        check("archive mode, not live", not v._online_mode, f"online={int(v._online_mode)}")
        master = v._per_cam_master_idx
        check("a master camera is selected", master == 0, f"master={master}")
        slave = 1 if args.cams > 1 else 0

        # ── A. the slave slider must stay free ────────────────────────────────────
        print("\n[A] a slave slider keeps the moment the user put it on")
        # Park the master away from the end so a pull towards it would be unmistakable.
        v._per_cam_step(-40)
        pump(app, 0.4)
        master_before = handle_ts(v, master)

        v._on_per_cam_pressed(slave)
        for frac in (0.6, 0.4, 0.25):
            v._per_cam_rows[slave].set_value(int(frac * m.SLIDER_MAX))
            v._on_per_cam_value_changed(slave, v._per_cam_rows[slave].value())
            pump(app, m.NAV_TICK_MS / 1000.0 * 2.0)
        v._on_per_cam_released(slave)
        pump(app, 0.1)
        landed = handle_ts(v, slave)
        landed_val = v._per_cam_rows[slave].value()
        check("the slave really moved", landed != handle_ts(v, master),
              f"{(landed - handle_ts(v, master)) / 1e9:+.3f} s from the master")

        # Past BOTH settle tiers: the 200 ms refine and the 500 ms native upgrade.
        pump(app, 1.6)
        check("the slave handle stayed where it was let go",
              v._per_cam_rows[slave].value() == landed_val,
              f"{landed_val} -> {v._per_cam_rows[slave].value()}")
        check("the slave picture stayed on its own frame",
              handle_ts(v, slave) == landed,
              f"{(handle_ts(v, slave) - landed) / 1e9:+.3f} s")
        check("the slave tile is not showing the master's frame",
              v._cam_ts[slave][v._cam_current_idx[slave]] == landed,
              f"tile {(v._cam_ts[slave][v._cam_current_idx[slave]] - landed) / 1e9:+.3f} s "
              f"off the handle")
        check("dragging a slave did not move the master",
              handle_ts(v, master) == master_before,
              f"{(handle_ts(v, master) - master_before) / 1e9:+.3f} s")

        # …and the ONE thing that may re-aim it: the master moving.
        print("\n[A2] moving the master does bring the slave back")
        v._per_cam_step(+5)
        pump(app, 0.6)
        targets = dict(v._per_cam_slave_targets(master, handle_ts(v, master)))
        want = targets.get(slave)
        check("the master's move resolved a target for the slave", want is not None)
        if want is not None:
            step_tol_ns = int((axis[1] - axis[0]) / m.SLIDER_MAX) + 1
            check("the slave followed the master", abs(handle_ts(v, slave) - want) <= step_tol_ns,
                  f"{(handle_ts(v, slave) - want) / 1e9:+.3f} s off")

        # ── B. press-and-hold on the frame arrows ─────────────────────────────────
        print("\n[B] holding the forward arrow ramps up: 2-3-4-5, then 8 and 10 images/s")
        v.btn_next.setEnabled(True)
        v.btn_prev.setEnabled(True)
        # Room to run: park the master well before the end.
        v._per_cam_step(-(len(v._cam_ts[master]) - 1))
        pump(app, 0.4)

        # A plain click is still exactly one frame.
        f0 = master_frame(v)
        QTest.mousePress(v.btn_next, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(v.btn_next, Qt.MouseButton.LeftButton)
        pump(app, 0.3)
        check("a click moves exactly one frame", master_frame(v) - f0 == 1,
              f"{master_frame(v) - f0} frames")

        # The table itself, first: every rung the ramp is documented with, read straight
        # off _hold_rate. A pure function, so this part cannot be blamed on the harness.
        print("  the ramp table")
        for held, want in ((0.0, 2), (0.9, 2), (1.0, 3), (2.4, 4), (3.0, 5), (4.9, 5),
                           (5.0, 8), (7.9, 8), (8.0, 10), (60.0, 10)):
            check(f"{held:.1f} s held -> {want} images/s",
                  v._hold_rate(held) == want, f"got {v._hold_rate(held)}")

        # The RUNG the hold has reached, sampled while it is held. This, not a count of
        # frames per second, is the assertion that can be trusted here: everything in this
        # harness — decode included — shares one thread offscreen, and GC pauses of nearly
        # a second were measured mid-hold, so a count over any single second is noise.
        # The rate itself is read from the clock by _hold_step_tick, so the interval IS the
        # rung, exactly as the operator experiences it. Counts are checked too, but only
        # for the thing a count can prove: the ramp must never RUN AWAY past its top rung.
        f0 = master_frame(v)
        QTest.mousePress(v.btn_next, Qt.MouseButton.LeftButton)
        press_t = time.monotonic()
        # Sampled against the REAL clock since the press, never against a sum of pumps:
        # the printing and the checks in between cost time of their own, and adding those
        # up walked the "2.5 s" sample past the 3 s rung boundary.
        for at, want_rate in ((0.7, 2), (1.5, 3), (2.5, 4), (3.5, 5), (6.0, 8), (8.5, 10)):
            pump(app, max(0.02, at - (time.monotonic() - press_t)))
            check(f"after {at:.1f} s held the rate is {want_rate} images/s",
                  v._hold_timer.interval() == 1000 // want_rate,
                  f"interval {v._hold_timer.interval()} ms "
                  f"(want {1000 // want_rate} ms) at "
                  f"{time.monotonic() - press_t:.2f} s")
        held_total = master_frame(v) - f0
        # Ideal count over the 8.5 s held: 1 press + 2+3+4+5+5+8+8+10 over the rungs.
        check("the ramp never runs away", held_total <= 60,
              f"{held_total} frames in 8.5 s")
        check("the hold is actually stepping", held_total >= 4, f"{held_total} frames")

        f_top = master_frame(v)
        pump(app, 3.0)
        top_rate = (master_frame(v) - f_top) / 3.0
        check("the top rung is capped at 10 images/s", top_rate <= 10.8,
              f"{top_rate:.1f} frames/s")

        QTest.mouseRelease(v.btn_next, Qt.MouseButton.LeftButton)
        pump(app, 0.1)
        f_rel = master_frame(v)
        pump(app, 1.0)
        check("releasing the button stops the stepping", master_frame(v) == f_rel,
              f"{master_frame(v) - f_rel} frames after release")

        # Backwards, and the ramp must start over rather than resume at 5/s.
        f0 = master_frame(v)
        QTest.mousePress(v.btn_prev, Qt.MouseButton.LeftButton)
        pump(app, 1.1)
        back = f0 - master_frame(v)
        QTest.mouseRelease(v.btn_prev, Qt.MouseButton.LeftButton)
        pump(app, 0.2)
        check("holding the back arrow steps backwards at the same starting rate", 2 <= back <= 4,
              f"{back} frames in the first 1.1 s")
    finally:
        try:
            v.close()
        except Exception:
            pass

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S):"))
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
