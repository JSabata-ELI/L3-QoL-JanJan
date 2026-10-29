"""Leaving live mode by hand, and what the history reload does afterwards.

The report (2026-10-21), three faults that turned out to be one mechanism:

  1. Moving into the past while live mode runs sometimes left live mode ON.
     Only a mouse PRESS on a handle switched it off. The wheel, the arrow keys, a click
     on the groove and the frame arrows emit valueChanged alone, so live kept running
     and the next arrival pulled the view back to the newest frame.

  2. With live mode OFF the slider jumped back to "now" a few seconds after going into
     the past. Leaving live starts a reload of the full history (live keeps only the
     newest ONLINE_MAX_ITEMS frames per camera). It remembered the moment live went off
     — the newest frame — and when the share answered, seconds later, it put the view
     back there, wherever the user had gone in the meantime.

  3. Moving the master, some cameras did not move although they had frames there.
     Until that reload is in, a fast camera holds only its last few minutes, so it had
     no frame within SLAVE_SYNC_MAX_NS of an older moment and was left alone. Nothing
     re-synced it when the frames arrived.

The live trim is reproduced by cutting each camera's list by hand, and the history
reload is held back (captured from scan_pool) so the user can move BEFORE it lands —
the order that happens on the real share.

    python testing/test_live_leave_and_restore.py
"""
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


class CapturePool:
    """scan_pool stand-in that holds the history reload back until the test says so."""

    def __init__(self, real):
        self.real = real
        self.held = []

    def start(self, task, *a):
        if type(task).__name__ == "_BackfillTask":
            self.held.append(task)
        else:
            self.real.start(task, *a)

    def release(self):
        held, self.held = self.held, []
        for t in held:
            t.run()          # emits done on the GUI thread → the merge runs now
        return len(held)

    def __getattr__(self, name):
        return getattr(self.real, name)


def shown_ts(v, cam: int) -> int:
    """The frame tile `cam` was last asked for."""
    cam_ts = v._cam_ts[cam]
    return cam_ts[max(0, min(len(cam_ts) - 1, v._cam_current_idx[cam]))]


def fake_live(v, keep: "list[int]"):
    """Live mode as it looks after a long run: each camera trimmed to its newest
    keep[i] frames, every tile on the newest moment, the Live button ticked."""
    for ci, k in enumerate(keep):
        n = len(v._cam_items[ci])
        if n > k:
            v._cam_items[ci] = v._cam_items[ci][n - k:]
            v._cam_ts[ci] = v._cam_ts[ci][n - k:]
            v._bump_cam_offset(ci, n - k)
            v._cam_poll_max_ts[ci] = v._cam_ts[ci][-1]
    v._rebuild_shared_items_from_cams()
    v._invalidate_shared_ckeys()
    v._live_trimmed = True
    master = v._per_cam_master_idx
    latest = v._cam_ts[master][-1]
    for ci in range(len(v._cam_items)):
        v._cam_current_idx[ci] = len(v._cam_ts[ci]) - 1
    v._per_cam_rows[master].set_value(v._per_cam_ts_to_slider(master, latest))
    v._per_cam_display_one(master, latest)
    v._per_cam_sync_slaves(master, latest, live_edge=True)
    v._per_cam_sync_shared_widgets(master, latest)
    v._online_mode = True
    v._auto_follow = True
    v._btn_auto_follow.setEnabled(True)
    v._btn_auto_follow.blockSignals(True)
    v._btn_auto_follow.setChecked(True)
    v._btn_auto_follow.blockSignals(False)


def main():
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_liveleave_"))
    # 3.3 Hz, 200 frames = one minute per camera.
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, 2, 200)
    all_ts = [t for lst in cam_folder_lists for f in lst for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    axis = (min(all_ts), max(all_ts))

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    pool = CapturePool(v.scan_pool)
    v.scan_pool = pool

    try:
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
            print("scan did not finish")
            return 1
        master, slave = v._per_cam_master_idx, 1
        check("master is camera 0", master == 0, f"master={master}")
        full = [list(c) for c in v._cam_ts]

        # ── 1 + 2 + 3: wheel on the master row, then the reload lands ─────────────────
        print("\n[1] the mouse wheel on the master row leaves live mode")
        # Master keeps 100 frames (~30 s), the slave only 15 (~4.5 s): a moment 25 s back
        # is in the master's memory and not in the slave's — the field case.
        fake_live(v, [100, 15])
        pump(app, 0.3)
        epoch_live = v._live_epoch
        target = v._cam_ts[master][15]          # ~25 s before the end
        row = v._per_cam_rows[master]
        # What the wheel does: setValue with signals ON, no sliderPressed.
        wheel_val = v._per_cam_ts_to_slider(master, target)
        # A raw slider value resolves at-or-before, and _per_cam_ts_to_slider truncates,
        # so the wheel lands on the frame its value maps back to (one earlier here) — the
        # moment that must then survive the reload is THAT one.
        landed = v._cam_ts[master][v._per_cam_ts_to_frame(
            master, v._per_cam_slider_to_ts(master, wheel_val))]
        row._slider.setValue(wheel_val)
        pump(app, 0.3)
        check("the wheel landed within one frame of the aim",
              0 <= target - landed <= 400_000_000,
              f"{(landed - target) / 1e9:+.2f} s")
        target = landed
        check("Live mode button is off", not v._btn_auto_follow.isChecked())
        check("live mode stopped", not v._online_mode and not v._auto_follow)
        check("a poll launched under live may no longer move the view",
              not v._live_result_may_move(epoch_live))
        check("the history reload was started", len(pool.held) == 1, f"{len(pool.held)}")
        check("the master is on the wheel's moment", shown_ts(v, master) == target,
              f"{(shown_ts(v, master) - target) / 1e9:+.2f} s")

        print("\n[3] before the reload the slave has nothing there (the field symptom)")
        check("the slave's memory starts after the master's moment",
              v._cam_ts[slave][0] - target > m.SLAVE_SYNC_MAX_NS,
              f"gap {(v._cam_ts[slave][0] - target) / 1e9:.1f} s")

        print("\n[2] the reload lands where the user is NOW, and the slave follows")
        check("one reload delivered", pool.release() == 1)
        pump(app, 0.5)
        check("all frames are back", [len(c) for c in v._cam_ts] == [len(c) for c in full],
              f"{[len(c) for c in v._cam_ts]}")
        check("the master stayed on the wheel's moment (not back to 'now')",
              shown_ts(v, master) == target,
              f"{(shown_ts(v, master) - target) / 1e9:+.2f} s")
        want = dict(v._per_cam_slave_targets(master, target)).get(slave)
        check("the slave has a frame there now", want is not None)
        if want is not None:
            check("the slave moved to the master's moment", shown_ts(v, slave) == want,
                  f"{(shown_ts(v, slave) - want) / 1e9:+.2f} s")
        check("live mode is still off", not v._online_mode and not v._btn_auto_follow.isChecked())

        # ── jump to a saved timestamp the live trim had dropped ───────────────────────
        print("\n[4] jump to a saved timestamp outside live memory")
        fake_live(v, [100, 15])
        pump(app, 0.3)
        target = full[master][5]                # trimmed: not in memory right now
        check("the target is not in memory", target < v._cam_ts[master][0])
        v._jump_to_saved_ts(target, "test")
        pump(app, 0.3)
        check("Live mode button is off", not v._btn_auto_follow.isChecked())
        check("the history reload was started", len(pool.held) == 1, f"{len(pool.held)}")
        pool.release()
        pump(app, 0.5)
        check("the master landed on the saved moment once the frames were back",
              shown_ts(v, master) == target,
              f"{(shown_ts(v, master) - target) / 1e9:+.2f} s")

        # ── the frame arrows ──────────────────────────────────────────────────────────
        print("\n[5] the frame arrow leaves live mode")
        fake_live(v, [100, 15])
        pump(app, 0.3)
        v.step_frame(-1)
        pump(app, 0.3)
        check("Live mode button is off", not v._btn_auto_follow.isChecked())
        pool.release()
        pump(app, 0.3)

        # ── a drag (press) on a slave row ─────────────────────────────────────────────
        print("\n[6] grabbing a row leaves live mode and moves the shared position at once")
        fake_live(v, [100, 15])
        pump(app, 0.3)
        v._on_per_cam_pressed(master)
        check("Live mode button is off", not v._btn_auto_follow.isChecked())
        check("live mode stopped", not v._online_mode)
        v._on_per_cam_released(master)
        pool.release()
        pump(app, 0.3)

        def wheel_to(cam, aim):
            """Move one row the way the mouse wheel does; returns the frame it lands on."""
            val = v._per_cam_ts_to_slider(cam, aim)
            got = v._cam_ts[cam][v._per_cam_ts_to_frame(
                cam, v._per_cam_slider_to_ts(cam, val))]
            v._per_cam_rows[cam]._slider.setValue(val)
            return got

        # ── 26.10.2026: a SLAVE moved into the past must stay there ─────────────────
        print("\n[7] a slave moved into the past stays there when the reload lands")
        fake_live(v, [100, 100])
        pump(app, 0.3)
        landed = wheel_to(slave, v._cam_ts[slave][20])
        pump(app, 0.3)
        check("Live mode button is off", not v._btn_auto_follow.isChecked())
        check("the slave is on the moment it was moved to", shown_ts(v, slave) == landed,
              f"{(shown_ts(v, slave) - landed) / 1e9:+.2f} s")
        check("one reload delivered", pool.release() == 1)
        pump(app, 0.5)
        check("the slave STAYED there (not back to the master's live moment)",
              shown_ts(v, slave) == landed,
              f"{(shown_ts(v, slave) - landed) / 1e9:+.2f} s")

        # ── 26.10.2026: the reload lands DURING a drag ───────────────────────────────
        print("\n[8] a reload landing mid-drag waits for the release")
        fake_live(v, [100, 100])
        pump(app, 0.3)
        trimmed = [len(c) for c in v._cam_ts]
        v._on_per_cam_pressed(master)
        landed = wheel_to(master, v._cam_ts[master][30])
        pump(app, 0.1)
        pool.release()
        pump(app, 0.1)
        check("nothing merged while the handle is held",
              [len(c) for c in v._cam_ts] == trimmed, f"{[len(c) for c in v._cam_ts]}")
        v._on_per_cam_released(master)
        pump(app, 0.8)
        check("merged after the release",
              [len(c) for c in v._cam_ts] == [len(c) for c in full],
              f"{[len(c) for c in v._cam_ts]}")
        check("the master is where it was let go", shown_ts(v, master) == landed,
              f"{(shown_ts(v, master) - landed) / 1e9:+.2f} s")
        check("live mode is still off", not v._online_mode and not v._btn_auto_follow.isChecked())
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
