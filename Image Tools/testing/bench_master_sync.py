"""Assert every per-camera slider HANDLE follows the master's, on all six paths.

The rule the per-camera view is built on: with a master radio selected, the master is the
clock. Every other camera is displayed at the master's moment — and its own slider handle
must be sitting there too, because that handle is the only place the operator can read
which frame a tile is showing. A slave whose picture follows but whose handle does not is
worse than no sync at all: the row then reports a moment the tile is not on.

Six ways the master moves, each with its own code path and each pinned here:

  1. drag            _on_per_cam_pressed / _on_per_cam_value_changed → _per_cam_nav_tick
  2. release         _on_per_cam_released → _per_cam_sync_slaves
  3. arrow keys      _per_cam_step
  4. master switch   _on_per_cam_master_chosen
  5. playback        _autoplay_step → _nav_request → _per_cam_nav_tick
  6. live arrival    _live_advance_cam → _per_cam_sync_slaves

…plus independent mode (no master), where the rows must deliberately NOT follow.

A slave with no frame within SLAVE_SYNC_MAX_NS of the master's moment is left alone by
design (_per_cam_slave_targets), so the check is against what that function resolves, not
against "some frame near enough" — the two differ exactly at the window edge, and pinning
the wrong one would make a correct implementation look broken.

Runs offscreen against synthetic local files — no share, no network:

  python testing/bench_master_sync.py
  python testing/bench_master_sync.py --cams 4 --frames 60
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
    """Which FRAME of camera `cam` its handle is parked on.

    NEAREST, not "the newest frame at or before" — _per_cam_ts_to_slider truncates when
    it converts a timestamp to a slider step, so a handle placed exactly on a frame reads
    back a few microseconds BEFORE it, and snapping downwards would name the previous
    frame every single time (a whole frame period of phantom error, in the direction that
    makes a correct sync look one frame off)."""
    cam_ts = v._cam_ts[cam]
    raw = v._per_cam_slider_to_ts(cam, v._per_cam_rows[cam].value())
    pos = bisect.bisect_left(cam_ts, raw)
    best = None
    for i in (pos - 1, pos, pos + 1):
        if 0 <= i < len(cam_ts) and (best is None
                                     or abs(cam_ts[i] - raw) < abs(cam_ts[best] - raw)):
            best = i
    return cam_ts[best]


def master_ts(v):
    """The moment the master row is on, as one of the master's own frames."""
    mi = v._per_cam_master_idx
    return None if mi < 0 else handle_ts(v, mi)


def slave_offsets(v, m):
    """[(cam, handle_ts, wanted_ts)] for every slave the master's moment resolves to.

    `wanted_ts` comes from the shipping resolver (_per_cam_slave_targets), so a slave
    deliberately left alone — no frame within SLAVE_SYNC_MAX_NS — is not counted as a
    failure, and a slave that IS in range is held to the exact frame the app itself
    picked rather than to "something close".
    """
    mi = v._per_cam_master_idx
    mts = master_ts(v)
    out = []
    for cam, want_ts in v._per_cam_slave_targets(mi, mts):
        out.append((cam, handle_ts(v, cam), want_ts))
    return out


def assert_synced(v, m, label: str, tol_ns: int = 0):
    """Every slave handle sits on the frame the master's moment resolves to."""
    mts = master_ts(v)
    bad = []
    n_checked = 0
    for cam, got_ts, want_ts in slave_offsets(v, m):
        n_checked += 1
        # The handle is set with _per_cam_ts_to_slider(want_ts); mapping it back gives
        # want_ts up to one slider step, which is what tol_ns absorbs.
        if abs(got_ts - want_ts) > tol_ns:
            bad.append(f"cam{cam} handle {(got_ts - want_ts) / 1e9:+.3f} s off")
    check(f"{label}: every slave handle sits on the master's moment",
          not bad and n_checked > 0,
          (", ".join(bad) if bad else ("no slave resolved — nothing was checked"
                                       if not n_checked else "")))
    return mts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=3)
    ap.add_argument("--frames", type=int, default=40)
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_msync_"))
    # 3.3 Hz, and make_synthetic_set staggers each camera by step/cams — so the slaves
    # never have a frame at exactly the master's timestamp, which is the real situation
    # and the one where a nearest-frame resolution can actually be wrong.
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)
    all_ts = [t for lst in cam_folder_lists for f in lst for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    axis = (min(all_ts), max(all_ts))

    # One slider step in ns — the quantisation the handle can never beat.
    step_tol_ns = int((axis[1] - axis[0]) / m.SLIDER_MAX) + 1

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
        check("a master row is selected by default", v._per_cam_master_idx == 0,
              f"master={v._per_cam_master_idx}")
        check("one slider row per camera", len(v._per_cam_rows) == args.cams,
              f"{len(v._per_cam_rows)} rows")
        master = v._per_cam_master_idx
        row = v._per_cam_rows[master]

        # ── 1. DRAG ───────────────────────────────────────────────────────────────
        print("\n[1] dragging the master slider")
        v._on_per_cam_pressed(master)
        moved = []
        for k in range(12):
            frac = k / 11.0
            row.set_value(int(frac * m.SLIDER_MAX))
            # set_value blocks signals (it moves the handle without rendering), so emit
            # what a mouse would.
            v._on_per_cam_value_changed(master, row.value())
            pump(app, m.NAV_TICK_MS / 1000.0 * 2.0)
            moved.append(master_ts(v))
        check("the master handle really travelled", len(set(moved)) > 6,
              f"{len(set(moved))} distinct moments over 12 steps")
        assert_synced(v, m, "drag", step_tol_ns)

        # ── 2. RELEASE (the settle path) ──────────────────────────────────────────
        print("\n[2] releasing the master slider")
        v._on_per_cam_released(master)
        pump(app, 0.3)
        assert_synced(v, m, "release", step_tol_ns)

        # Grabbing the handle and letting go without moving it must be a no-op. It is not
        # free: press and release both re-derive "which frame is this row on" by mapping
        # the handle's own slider value back to a time, and that round trip loses a step
        # (_per_cam_ts_to_slider truncates), so the frame lands one earlier than the one
        # the row is actually parked on. Away from the ends of the axis that is a visible
        # backward jump on every click.
        v._per_cam_step(-8)
        pump(app, 0.3)
        before = master_ts(v)
        v._on_per_cam_pressed(master)
        pump(app, 0.2)
        v._on_per_cam_released(master)
        pump(app, 0.3)
        check("clicking the handle without moving it keeps the frame",
              master_ts(v) == before,
              f"{(master_ts(v) - before) / 1e9:+.3f} s")
        assert_synced(v, m, "click without moving", step_tol_ns)

        # ── 3. ARROW KEYS ─────────────────────────────────────────────────────────
        print("\n[3] stepping with the arrow keys")
        before = master_ts(v)
        for _ in range(5):
            v._per_cam_step(-1)
            pump(app, m.NAV_TICK_MS / 1000.0 * 2.0)
        after = master_ts(v)
        check("the arrows moved the master back", after < before,
              f"{(after - before) / 1e9:+.3f} s")
        assert_synced(v, m, "arrows", step_tol_ns)
        for _ in range(3):
            v._per_cam_step(+1)
            pump(app, m.NAV_TICK_MS / 1000.0 * 2.0)
        assert_synced(v, m, "arrows forward", step_tol_ns)

        # ── 4. MASTER SWITCH ──────────────────────────────────────────────────────
        if args.cams > 1:
            print("\n[4] switching the master to another camera")
            new_master = 1
            was_on = handle_ts(v, new_master)
            v._per_cam_rows[new_master]._radio.setChecked(True)
            pump(app, 0.3)
            check("the new master keeps the frame it was already on",
                  handle_ts(v, new_master) == was_on,
                  f"{(handle_ts(v, new_master) - was_on) / 1e9:+.3f} s")
            check("the new camera is the master",
                  v._per_cam_master_idx == new_master,
                  f"master={v._per_cam_master_idx}")
            check("its row is the only one marked master",
                  [i for i, r in enumerate(v._per_cam_rows) if r._is_master]
                  == [new_master])
            assert_synced(v, m, "master switch", step_tol_ns)
            master = new_master
            row = v._per_cam_rows[master]

        # ── 5. PLAYBACK ───────────────────────────────────────────────────────────
        print("\n[5] playback")
        v._per_cam_step(-15)
        pump(app, 0.3)
        t_before = master_ts(v)
        # The combo's default is a few % of the window per second; on a short synthetic
        # window that is well under a frame per second, so a 1-2 s sample can legitimately
        # show no movement at all. Ask for the fastest offered speed instead of waiting.
        v.speed_cb.setCurrentIndex(v.speed_cb.count() - 1)
        v.play()
        check("playback started", v._is_playing)
        pump(app, 3.0)
        t_playing = master_ts(v)
        check("playback advanced the master", t_playing > t_before,
              f"{(t_playing - t_before) / 1e9:+.3f} s")
        assert_synced(v, m, "playing", step_tol_ns)
        v.stop()
        pump(app, 0.4)
        assert_synced(v, m, "after stop", step_tol_ns)

        # ── 6. LIVE ARRIVAL ───────────────────────────────────────────────────────
        print("\n[6] a live frame arriving on the master")
        # Straight through the single 'this camera got a fresh frame' entry point. The
        # arriving frame is the master's own newest EXISTING one (the rows are parked well
        # before it): a frame past the end of the axis would clamp the handle to the last
        # slider step and measure the harness's fixed axis, not the sync.
        v._per_cam_step(-12)
        pump(app, 0.3)
        new_ts = max(v._cam_ts[master])
        check("the rows start behind the arriving frame", master_ts(v) < new_ts)
        v._auto_follow = True
        v._live_advance_cam(master, new_ts, True)
        pump(app, 0.4)
        check("the master handle jumped to the new frame",
              master_ts(v) == new_ts,
              f"{(master_ts(v) - new_ts) / 1e9:+.3f} s off")
        assert_synced(v, m, "live arrival", step_tol_ns)

        # ── 7. INDEPENDENT MODE: the rows must NOT follow ─────────────────────────
        print("\n[7] master deselected — the rows go independent")
        v._per_cam_rows[master]._radio.setChecked(False)
        pump(app, 0.3)
        check("no master is selected", v._per_cam_master_idx < 0,
              f"master={v._per_cam_master_idx}")
        others = [i for i in range(args.cams) if i != master]
        if others:
            other = others[0]
            before = [r.value() for r in v._per_cam_rows]
            v._on_per_cam_pressed(other)
            v._per_cam_rows[other].set_value(int(0.2 * m.SLIDER_MAX))
            v._on_per_cam_value_changed(other, v._per_cam_rows[other].value())
            pump(app, 0.3)
            v._on_per_cam_released(other)
            pump(app, 0.3)
            after = [r.value() for r in v._per_cam_rows]
            check("the dragged row moved", after[other] != before[other])
            unchanged = all(after[i] == before[i]
                            for i in range(args.cams) if i != other)
            check("every other row stayed where it was", unchanged,
                  f"before={before} after={after}")
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
