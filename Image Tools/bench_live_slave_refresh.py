"""Assert that in LIVE mode every camera with a new frame repaints — on every shot.

bench_master_sync.py pins where the slider HANDLES end up when the master moves. This
pins the question the operator actually asked, which is the other half: when a shot is
fired, does every tile that has something new to show actually show it?

Three things used to stop that happening, and each has a case here:

  1. A SLAVE'S OWN ARRIVAL painted nothing at all. With a master selected,
     _live_advance_cam returned immediately for any other camera, so a frame that landed a
     moment after the master's (the normal case — one shot writes N files, not
     simultaneously) stayed invisible until the master's NEXT arrival.
  2. A SLAVE THE MASTER HAD RUN AWAY FROM was left frozen for good: _per_cam_slave_targets
     skips a camera with no frame within SLAVE_SYNC_MAX_NS, so a camera whose cadence never
     lined up with the master's inside 3 s never repainted again however much it shot.
     Measured minutes wide in the field (a grid at 08:31 next to five tiles at 08:08).
  3. THE LIVE TOGGLE re-displayed almost nothing — turning live off repainted no tile at
     all, so the grid kept whatever moments and decode sizes it happened to be left on.

And the one case where a frozen tile is the truth: a camera that received nothing new must
NOT move.

Runs offscreen against synthetic local files — no share, no network:

  python bench_live_slave_refresh.py
"""
import shutil
import tempfile
import time
from pathlib import Path

import bench_common as B
from bench_master_sync import handle_ts

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


def make_set(root: Path, specs: "list[tuple[str, float, int, float]]", start_ns: int):
    """One folder per (name, period_s, count, first_offset_s). Deliberately NOT
    bench_common's uniform 3.3 Hz set: the whole point here is cameras whose cadences do
    not line up, which is what a uniform set can never produce."""
    from PySide6.QtGui import QImage
    import numpy as np

    w, h = 240, 200
    names, folder_lists = [], []
    for ci, (name, period_s, count, first_off_s) in enumerate(specs):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        names.append(name)
        folder_lists.append([folder])
        step = int(period_s * 1e9)
        for i in range(count):
            ts = start_ns + int(first_off_s * 1e9) + i * step
            p = folder / f"{name}_{ts:019d}.png"
            if p.exists():
                continue
            arr = np.zeros((h, w), dtype=np.uint16)
            arr[:, :] = np.linspace(1400, 3060, w, dtype=np.uint16)
            arr[(i * 7) % h:(i * 7) % h + 10, :] = 60000
            QImage(arr.tobytes(), w, h, w * 2,
                   QImage.Format.Format_Grayscale16).save(str(p), "PNG")
    return names, folder_lists


def add_frame(folder: Path, cam: str, ts_ns: int) -> Path:
    """One more file with a valid 19-digit ns name, copied from an existing PNG."""
    dst = folder / f"{cam}_{ts_ns:019d}.png"
    src = next(f for f in sorted(folder.glob("*.png")) if f.stat().st_size > 500)
    shutil.copyfile(src, dst)
    return dst


def arrive(v, m, app, cam: int, folder: Path, name: str, ts_ns: int):
    """Deliver one live frame through the SHIPPING entry point.

    _online_poll_multi and _on_dir_watch_new_file both funnel into these three steps —
    append to the camera's list, record the arrival, then _live_advance_cam — so driving
    them directly tests the code that runs in the field without waiting on a real poll
    interval."""
    add_frame(folder, name, ts_ns)
    v._cam_items[cam].append(m.Item(folder / f"{name}_{ts_ns:019d}.png", ts_ns))
    v._cam_ts[cam].append(ts_ns)
    if cam < len(v._cam_poll_max_ts):
        v._cam_poll_max_ts[cam] = ts_ns
    v._extend_shared_timeline_from_cams()
    v._note_cam_frames(cam, ts_ns)
    v._live_advance_cam(cam, ts_ns, True)
    pump(app, 0.45)


def shown(v, cam: int) -> int:
    return v._cam_shown_ts_ns[cam] if cam < len(v._cam_shown_ts_ns) else 0


def main():
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    start_ns = 1_760_000_000_000_000_000
    tmp = Path(tempfile.mkdtemp(prefix="is_slaveref_"))
    # cam0 master at 3.3 Hz; cam1 the same cadence (a healthy slave); cam2 one frame every
    # 40 s, its newest already 40 s behind the master's live edge — far outside
    # SLAVE_SYNC_MAX_NS, the camera that used to freeze; cam3 the same, and it will be
    # given NOTHING new so it must stay put.
    specs = [("MSTR", 0.3, 40, 0.0), ("SYNCD", 0.3, 40, 0.0),
             ("SLOW", 40.0, 6, -240.0), ("QUIET", 40.0, 6, -240.0)]
    names, folder_lists = make_set(tmp, specs, start_ns)
    folders = [fl[0] for fl in folder_lists]
    all_ts = [t for f in folders for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    # An axis wide enough that a frame arriving later does not clamp against its end.
    axis = (min(all_ts), max(all_ts) + 600_000_000_000)

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        v._start_multi_cam_scan(names, folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
            print("scan did not finish")
            return 1
        print(f"scanned: {[len(c) for c in v._cam_ts]} frames per camera")
        master = v._per_cam_master_idx
        check("cam0 is the master", master == 0, f"master={master}")

        # Live mode on, but without _start_online_mode's real timers and dir-watchers:
        # every arrival here is delivered explicitly, and a poll racing them would make
        # the cases non-deterministic. The button state is brought along so the toggle in
        # case 5 emits a real transition.
        v._online_mode = True
        v._auto_follow = True
        v._btn_auto_follow.blockSignals(True)
        v._btn_auto_follow.setChecked(True)
        v._btn_auto_follow.blockSignals(False)
        step_tol_ns = int((axis[1] - axis[0]) / m.SLIDER_MAX) + 1

        # Put the grid on the live edge first, the way the toggle does.
        edge = v._cam_ts[master][-1]
        v._per_cam_display_one(master, edge)
        v._per_cam_sync_slaves(master, edge, live_edge=True)
        pump(app, 0.6)
        print(f"\nstart: {[shown(v, i) - start_ns for i in range(4)]} ns after t0")

        # ── 1. THE SLAVE THAT ARRIVES A MOMENT LATE ───────────────────────────────
        # One shot writes N files, and they do not land in the same instant. The master's
        # file is discovered first, the slave's a fraction of a second later — and that
        # second arrival used to paint nothing.
        print("\n[1] one shot: master first, the healthy slave a moment later")
        shot_ts = edge + 300_000_000
        arrive(v, m, app, master, folders[master], names[master], shot_ts)
        check("the master tile is on the new shot", shown(v, master) == shot_ts,
              f"{(shown(v, master) - shot_ts) / 1e9:+.3f} s off")
        before_slave = shown(v, 1)
        slave_ts = shot_ts + 54_000_000        # 54 ms later, as in the field
        arrive(v, m, app, 1, folders[1], names[1], slave_ts)
        check("the slave's own arrival repainted its tile",
              shown(v, 1) == slave_ts,
              f"tile {(shown(v, 1) - slave_ts) / 1e9:+.3f} s off "
              f"(was {(before_slave - slave_ts) / 1e9:+.3f} s)")
        check("its handle followed the picture",
              abs(handle_ts(v, 1) - slave_ts) <= step_tol_ns,
              f"{(handle_ts(v, 1) - slave_ts) / 1e9:+.3f} s off")

        # ── 2. THE SLAVE THE MASTER HAS RUN AWAY FROM ─────────────────────────────
        # cam2 writes once every 40 s, so at the master's moment it never has a frame
        # inside SLAVE_SYNC_MAX_NS. It must still show its own newest frame rather than
        # sit on a picture minutes old.
        print("\n[2] a camera whose cadence never lines up with the master's")
        before_slow = shown(v, 2)
        gap = abs(before_slow - shown(v, master)) / 1e9
        check("it starts well outside the sync window",
              gap > m.SLAVE_SYNC_MAX_NS / 1e9, f"{gap:.1f} s from the master")
        slow_ts = shot_ts - 5_000_000_000      # newer than its tile, older than the master
        arrive(v, m, app, 2, folders[2], names[2], slow_ts)
        check("it advanced to its own new frame instead of freezing",
              shown(v, 2) == slow_ts,
              f"tile {(shown(v, 2) - slow_ts) / 1e9:+.3f} s off "
              f"(was {(before_slow - slow_ts) / 1e9:+.3f} s)")
        check("its handle followed the picture",
              abs(handle_ts(v, 2) - slow_ts) <= step_tol_ns,
              f"{(handle_ts(v, 2) - slow_ts) / 1e9:+.3f} s off")
        check("and its timestamp is marked out of sync with the master",
              v._cam_off_master(2))

        # ── 3. A CAMERA WITH NOTHING NEW MUST NOT MOVE ────────────────────────────
        print("\n[3] the camera that received nothing")
        before_quiet = shown(v, 3)
        for k in range(4):
            arrive(v, m, app, master, folders[master], names[master],
                   shot_ts + (k + 1) * 300_000_000)
        check("four more master shots did not move the quiet camera",
              shown(v, 3) == before_quiet,
              f"{(shown(v, 3) - before_quiet) / 1e9:+.3f} s")
        check("the master itself kept advancing",
              shown(v, master) == shot_ts + 4 * 300_000_000)

        # ── 4. EVERY SHOT REFRESHES EVERY CAMERA THAT HAS SOMETHING ───────────────
        print("\n[4] ten shots, master + two slaves writing on each")
        misses = []
        base = shown(v, master)
        for k in range(10):
            t = base + (k + 1) * 300_000_000
            arrive(v, m, app, master, folders[master], names[master], t)
            for cam in (1, 2):
                arrive(v, m, app, cam, folders[cam], names[cam], t + 40_000_000)
            for cam in (master, 1, 2):
                want = t if cam == master else t + 40_000_000
                if shown(v, cam) != want:
                    misses.append(f"shot{k} cam{cam} {(shown(v, cam) - want) / 1e9:+.3f}s")
        check("every camera showed every shot it received",
              not misses, "; ".join(misses[:6]))
        check("the quiet camera still has not moved", shown(v, 3) == before_quiet)

        # ── 5. THE LIVE TOGGLE RELOADS EVERY TILE ─────────────────────────────────
        print("\n[5] switching live mode off and back on")
        v._bench = []                      # paint ledger: one row per pixmap that lands
        v._btn_auto_follow.setChecked(False)
        pump(app, 0.05)
        check("turning live OFF re-armed the resolution tier", v._hq_timer.isActive())
        pump(app, 1.2)
        painted_off = {r[1] for r in v._bench}
        check("turning live OFF repainted every tile",
              painted_off == {0, 1, 2, 3}, f"repainted {sorted(painted_off)}")
        check("live mode really went off", not v._online_mode and not v._auto_follow)

        v._bench = []
        v._btn_auto_follow.setChecked(True)
        pump(app, 0.05)
        check("turning live ON re-armed the resolution tier", v._hq_timer.isActive())
        pump(app, 1.2)
        painted_on = {r[1] for r in v._bench}
        check("turning live ON repainted every tile",
              painted_on == {0, 1, 2, 3}, f"repainted {sorted(painted_on)}")
        check("live mode came back on", v._online_mode and v._auto_follow)
        check("the master is back on its newest frame",
              shown(v, 0) == v._cam_ts[0][-1],
              f"{(shown(v, 0) - v._cam_ts[0][-1]) / 1e9:+.3f} s off")
        check("the healthy slave is on the master's moment",
              abs(shown(v, 1) - shown(v, 0)) <= m.SLAVE_SYNC_MAX_NS,
              f"{(shown(v, 1) - shown(v, 0)) / 1e9:+.3f} s from the master")
    finally:
        try:
            v.close()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S):"))
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
