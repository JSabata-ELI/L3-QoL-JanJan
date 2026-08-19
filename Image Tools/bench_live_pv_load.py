"""Does the PV panel starve the PICTURES in live multi-camera mode?

The reported regression: with the PV panel refreshing per shot, the images start lagging —
some cameras stop updating altogether, some take a long time, and the sliders stop moving.
The suspicion this exists to test is the paint-triggered PV refresh (`_note_cam_shown` →
`_pv_trigger_fetch`): the archiver work runs off the GUI thread, but every result comes
back to it and rebuilds the table and the overlay, and paints are far more frequent than
frame arrivals.

So this measures the PICTURES, not the numbers, over a live run long enough for a decay to
show: per camera, how many arrivals reached the screen, and whether the paint rate in the
second half holds up against the first. Both the share read (145 ms) and the archiver
round-trip are simulated, because the whole question is what happens when both are slow at
once — on local files with an instant archiver neither can starve anything.

  python bench_live_pv_load.py                 # PV panel on
  python bench_live_pv_load.py --no-pv         # the control: same run, no PV at all
  python bench_live_pv_load.py --seconds 60 --cams 6

Compare the two: if the pictures only stall with PV on, the panel is the cause.
"""
import argparse
import shutil
import tempfile
import threading
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []

SHARE_READ_MS = 145.0      # measured on \\users-L3
ARCHIVER_MS = 250.0        # one channel lookup over the real network


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--shot-interval", type=float, default=1.0)
    ap.add_argument("--pvs", type=int, default=8,
                    help="how many PV channels the panel reads (the fan-out is what "
                         "makes its traffic compete with the tiles)")
    ap.add_argument("--no-pv", action="store_true")
    ap.add_argument("--trim-at", type=int, default=20,
                    help="stand-in for ONLINE_MAX_ITEMS. Live mode keeps only the newest "
                         "N frames per camera and _restore_full_history merges the rest "
                         "back when the user grabs a slider — the real value is 3600, far "
                         "more than a bench can write, so the whole trim/restore path "
                         "would never run.")
    args = ap.parse_args()

    m = B.load_slider()
    m.ONLINE_MAX_ITEMS = args.trim_at
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ── a fake archiver that is SLOW, the way the real one is ─────────────────────
    published: "list[tuple[int, float]]" = []
    pub_lock = threading.Lock()
    fetch_calls = [0]

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        fetch_calls[0] += 1
        time.sleep(ARCHIVER_MS / 1000.0)   # releases the GIL, like the socket read
        with pub_lock:
            return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]

    # Frames stamped TODAY at 1 Hz, a short history to start from.
    n_pre = 6
    step_ns = int(args.shot_interval * 1e9)
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - (n_pre + 2) * step_ns

    tmp = Path(tempfile.mkdtemp(prefix="is_pvload_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, args.cams, n_pre, hz=1.0 / args.shot_interval, start_ns=start_ns)
    folders = [lst[0] for lst in cam_folder_lists]
    srcs = [next(f for f in sorted(fl.glob("*.png")) if f.stat().st_size > 1000)
            for fl in folders]

    pre_ts = sorted(t for p in folders[0].glob("*.png")
                    if (t := m.parse_unix_ns_from_name(p)))
    with pub_lock:
        published += [(t + 25_000_000, 10.0 + i) for i, t in enumerate(pre_ts)]

    B.install_read_latency(m, SHARE_READ_MS)
    print(f"{args.cams} cameras, a shot every {args.shot_interval:.1f} s for "
          f"{args.seconds:.0f} s; {SHARE_READ_MS:.0f} ms per frame read, "
          f"{ARCHIVER_MS:.0f} ms per archiver lookup; "
          f"PV {'OFF' if args.no_pv else 'ON'}")

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    # Four PVs — the panel fans out over the sources, which is what makes its traffic
    # heavy enough to compete with the tiles.
    v._pv_enabled = [] if args.no_pv else [
        n for n in m.pv_all_names() if m.pv_channel_for(n)][:args.pvs]
    v._pv_hidden = set()
    print(f"PV channels: {v._pv_enabled}")

    try:
        axis = (pre_ts[0], pre_ts[-1])
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
            print("scan did not finish")
            return 1
        v._btn_auto_follow.setChecked(True)
        check("live mode started", v._online_mode)

        v._bench.clear()
        arrivals = {i: set() for i in range(args.cams)}
        t0 = time.monotonic()
        next_shot = t0
        shot_i = 0
        # Frame timestamps come from the wall clock, the way the cameras stamp them.
        while time.monotonic() - t0 < args.seconds:
            app.processEvents()
            now = time.monotonic()
            if now >= next_shot:
                next_shot += args.shot_interval
                shot_i += 1
                ts = int(time.time() * 1e9)
                for ci in range(args.cams):
                    dst = folders[ci] / f"{cam_names[ci]}_{ts:019d}.png"
                    shutil.copyfile(srcs[ci], dst)
                    arrivals[ci].add(ts)
                with pub_lock:
                    published.append((ts + 25_000_000, 30.0 + shot_i))
                    published.sort()
                # The archiver's caches must see the new sample, the way a real one would.
                cpva.invalidate()
                cpva.invalidate_lookback()
            time.sleep(0.005)
        elapsed = time.monotonic() - t0

        rows = list(v._bench or [])
        print(f"\nwall {elapsed:.1f} s   shots {shot_i}   paints {len(rows)}   "
              f"archiver lookups {fetch_calls[0]}")
        print(f"{'camera':<14}{'shown/arrived':>15}{'paints/s':>10}"
              f"{'1st half':>10}{'2nd half':>10}{'lag p50':>9}{'max':>9}")

        half = t0 + elapsed / 2.0
        # perf_counter and monotonic share an epoch here only by accident, so split on
        # the ledger's own median time instead of a wall-clock instant.
        times = sorted(r[0] for r in rows)
        mid = times[len(times) // 2] if times else 0.0
        worst_cover, worst_decay = 1.0, 1.0
        for ci, name in enumerate(cam_names):
            mine = [r for r in rows if r[1] == ci]
            painted = {r[3] for r in mine}
            want = arrivals[ci]
            hit = len(want & painted)
            cover = hit / len(want) if want else 1.0
            first = len([r for r in mine if r[0] <= mid])
            second = len([r for r in mine if r[0] > mid])
            decay = (second / first) if first else 1.0
            # How far the painted frame was from the one the tile was asked for.
            d = sorted(abs(r[2] - r[3]) / 1e9 for r in mine if r[2])
            p50 = d[len(d) // 2] if d else 0.0
            worst_cover = min(worst_cover, cover)
            worst_decay = min(worst_decay, decay)
            print(f"{name:<14}{f'{hit}/{len(want)}':>15}"
                  f"{len(mine) / elapsed:>10.1f}{first:>10}{second:>10}"
                  f"{p50:>9.2f}{(d[-1] if d else 0):>9.2f}")

        # Every arriving frame must reach the screen: at one shot per second per camera
        # nothing here is fast enough to justify skipping one.
        check("every camera shows (nearly) every arriving frame", worst_cover >= 0.9,
              f"worst camera showed {worst_cover * 100:.0f} %")
        # The reported symptom is that it gets WORSE with time, so the second half is
        # measured against the first rather than against an absolute rate.
        check("the paint rate does not decay over the run", worst_decay >= 0.7,
              f"worst camera: second half {worst_decay * 100:.0f} % of the first")
        # And the sliders must still be tracking the live edge.
        stalled = []
        for ci in range(args.cams):
            newest = max(v._cam_ts[ci]) if v._cam_ts[ci] else 0
            handle = v._per_cam_slider_to_ts(ci, v._per_cam_rows[ci].value())
            if newest and abs(newest - handle) > 3e9:
                stalled.append(f"cam{ci} {(newest - handle) / 1e9:.1f} s behind")
        check("every slider handle is still at the live edge", not stalled,
              ", ".join(stalled))

        # ── grabbing a slider in live mode must not throw the picture backwards ───
        # _on_per_cam_pressed leaves live mode and kicks _restore_full_history(), which
        # merges the whole window back and so REBUILDS _cam_ts. It runs on the scan pool,
        # i.e. it lands somewhere in the middle of the user's drag — so anything that
        # remembers a position as a frame INDEX names a different, much older moment by
        # the time the drag ends. `_restore_full_history` says it itself: "remember the
        # moment being watched, not the index". The release is where that shows.
        master = v._per_cam_master_idx
        if master >= 0:
            check("live mode really trimmed the history (else this proves nothing)",
                  bool(getattr(v, "_live_trimmed", False)))
            n_before = len(v._cam_ts[master])
            was_at = v._per_cam_slider_to_ts(master, v._per_cam_rows[master].value())
            v._on_per_cam_pressed(master)
            restored = B.wait_for(
                lambda: not getattr(v, "_live_trimmed", False)
                and not getattr(v, "_backfill_running", False), 60.0)
            check("the full history was merged back in mid-drag", restored,
                  f"{n_before} → {len(v._cam_ts[master])} frames")
            v._on_per_cam_released(master)
            B.wait_for(lambda: False, 0.6)
            asked = (v._cam_target_ts_ns[master]
                     if master < len(v._cam_target_ts_ns) else 0)
            drift = abs(asked - was_at) / 1e9 if asked else 0.0
            check("letting go keeps the moment the handle is on", asked and drift <= 1.0,
                  f"asked for a frame {drift:.1f} s from the handle")
            handle_now = v._per_cam_slider_to_ts(master, v._per_cam_rows[master].value())
            check("and the handle did not jump either",
                  abs(handle_now - was_at) / 1e9 <= 1.0,
                  f"handle moved {(handle_now - was_at) / 1e9:+.1f} s")
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
