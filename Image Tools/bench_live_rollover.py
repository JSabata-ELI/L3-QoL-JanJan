"""Assert that live mode keeps following frames ACROSS A UTC HOUR ROLLOVER.

bench_live_dot.py pins the five faults that turn the dot red, but every one of its
cases lives in a single flat folder — bench_common.make_synthetic_set writes
root/CAMNAME/, which _cam_folder_time_key cannot parse, so active_scan_folders keeps
it forever as "keyless". The rollover machinery (poll_scan_folders picking only the
newest hour, and the _probe_hour_folder discovery that has to find the next one) is
therefore never exercised by any test.

That gap matters more than the five faults, because a rollover failure is SILENT: no
listing raises, no read fails, nothing hangs, and _live_health treats "no new images
appeared" as a healthy idle source by design. The dot stays green while the app has
stopped following the archive — which is exactly the "live mode was on, everything
green, no images" report this file exists to reproduce or rule out.

Layout is the real one, …/YYYY/M/D/H/CAM, with unpadded names (see
hour_dirs_for_windows). The app opens on the PREVIOUS UTC hour and the test then
creates the CURRENT one — that direction is the only testable one, because
_probe_hour_folder refuses any candidate whose hour is still in the future.

  python bench_live_rollover.py            # single camera
  python bench_live_rollover.py --cams 3   # multi-cam grid path
"""
import argparse
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def hour_dir(root: Path, when: datetime) -> Path:
    """…/YYYY/M/D/H — unpadded, the way hour_dirs_for_windows builds it."""
    return root / str(when.year) / str(when.month) / str(when.day) / str(when.hour)


def write_frame(folder: Path, cam: str, ts_ns: int, src: Path) -> Path:
    p = folder / f"{cam}_{ts_ns:019d}.png"
    shutil.copyfile(src, p)
    return p


def day_boundary(m, app, args):
    """The 23:00 → 00:00 candidate, which real time reaches once a day.

    That branch is the only one that rebuilds the whole Y/M/D path instead of
    swapping the hour, and it is wrapped in `except Exception: continue` — a
    mistake in it (an off-by-one date, or zero-padded names where the archive uses
    bare integers) would be swallowed and read as "the next folder does not exist
    yet", i.e. a silent green freeze at midnight UTC.

    The wall-clock gate inside _probe_hour_folder makes that unreachable in a test
    at any other time of day, so the probe is replaced by a recorder: it keeps the
    real is_dir() answer and only drops the gate. Everything else — the candidate
    construction, the listdir of the discovered folder, the new_folders plumbing —
    is the shipping code.
    """
    tmp = Path(tempfile.mkdtemp(prefix="is_mid_"))
    # "Yesterday 23:00 UTC" as the open folder, so delta=1 crosses the date.
    late = datetime.now(timezone.utc).replace(hour=23) - timedelta(days=1)
    nxt = late + timedelta(hours=1)          # today 00:00 UTC
    late_dir, next_dir = hour_dir(tmp, late), hour_dir(tmp, nxt)
    print(f"open folder:  {late_dir}   (23:00 UTC)")
    print(f"next folder:  {next_dir}   (00:00 UTC, next day)")

    cam_names, _ = B.make_synthetic_set(late_dir, 1, args.frames)
    cam = cam_names[0]
    open_dir = late_dir / cam

    probed: "list[Path]" = []
    real_probe = m._probe_hour_folder

    def recording_probe(candidate):
        probed.append(Path(candidate))
        try:
            return Path(candidate).is_dir()
        except OSError:
            return False

    m._probe_hour_folder = recording_probe
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    try:
        all_ts = [t for p in open_dir.glob("*.png")
                  if (t := m.parse_unix_ns_from_name(p))]
        newest = max(all_ts)
        src = next(p for p in sorted(open_dir.glob("*.png"))
                   if p.stat().st_size > 1000)
        v._ts_windows = None
        v._start_scan([open_dir], axis_override=(min(all_ts), newest),
                      folder_label=str(open_dir))
        if not B.wait_for(lambda: bool(v.ts_list), 120.0):
            print("scan did not finish")
            return
        v._btn_auto_follow.setChecked(True)
        print(f"\nwaiting out LIVE_START_GRACE_S ({m.LIVE_START_GRACE_S:.0f} s) …")
        end = time.monotonic() + m.LIVE_START_GRACE_S + 1.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

        print("\n[D] the archive crosses midnight UTC")
        before = len(v.ts_list)
        next_dir.joinpath(cam).mkdir(parents=True, exist_ok=True)
        write_frame(next_dir / cam, cam, newest + int(1e9 / 3.3), src)
        probed.clear()
        found = B.wait_for(lambda: len(v.ts_list) > before, 30.0)
        want = next_dir / cam
        check("the next day's hour-0 folder is probed",
              any(p == want for p in probed),
              f"probed {sorted({str(p) for p in probed})}")
        check("the frame written after midnight is discovered", found,
              f"known {len(v.ts_list)} was {before}")
        st, tip, reason = v._live_health(0, time.monotonic())
        if not found:
            check("a missed midnight rollover is reported as a fault", st == "fault",
                  f"state={st!r} tip={tip!r}")
    finally:
        m._probe_hour_folder = real_probe
        try:
            v._btn_auto_follow.setChecked(False)
            v._hard_reset_runtime()
        except Exception:
            pass
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=1)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-grace", action="store_true",
                    help="force poll_scan_folders' out-of-grace branch (ONE folder in "
                         "the poll set) whatever the real UTC minute is — otherwise "
                         "the harder case is only reachable during 5 minutes of "
                         "every hour")
    ap.add_argument("--day-boundary", action="store_true",
                    help="run the 23:00 → 00:00 UTC case instead (see day_boundary)")
    args = ap.parse_args()

    m = B.load_slider()
    if args.no_grace:
        m.ONLINE_ROLLOVER_GRACE_MIN = 0
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    if args.day_boundary:
        day_boundary(m, app, args)
        print("\n" + ("ALL CHECKS PASSED" if not FAILURES else
                      f"{len(FAILURES)} CHECK(S) FAILED:\n  " + "\n  ".join(FAILURES)))
        return 1 if FAILURES else 0

    tmp = Path(tempfile.mkdtemp(prefix="is_roll_"))
    now_utc = datetime.now(timezone.utc)
    prev_utc = now_utc - timedelta(hours=1)
    prev_dir = hour_dir(tmp, prev_utc)
    cur_dir = hour_dir(tmp, now_utc)
    print(f"prev hour folder: {prev_dir}")
    print(f"curr hour folder: {cur_dir}   (created after live mode starts)")
    print(f"UTC minute now:   {now_utc.minute}  "
          f"(rollover grace is < {m.ONLINE_ROLLOVER_GRACE_MIN} min)")

    # Frames in the PREVIOUS hour folder, one camera folder per camera.
    cam_names, seed_lists = B.make_synthetic_set(prev_dir, args.cams, args.frames)
    cam_folder_lists = [[prev_dir / n] for n in cam_names]

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        all_ts = [t for f in cam_folder_lists for lst in [f] for d in lst
                  for p in d.glob("*.png") if (t := m.parse_unix_ns_from_name(p))]
        axis = (min(all_ts), max(all_ts))
        newest = max(all_ts)
        step_ns = int(1e9 / 3.3)
        src = next(p for p in sorted(cam_folder_lists[0][0].glob("*.png"))
                   if p.stat().st_size > 1000)

        if args.cams == 1:
            v._ts_windows = None
            v._start_scan(list(cam_folder_lists[0]), axis_override=axis,
                          folder_label=str(cam_folder_lists[0][0]))
            if not B.wait_for(lambda: bool(v.ts_list), 120.0):
                print("scan did not finish")
                return 1
            n = 1
        else:
            v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
            if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
                print("scan did not finish")
                return 1
            n = len(v._cam_names)

        def known_count(ci: int) -> int:
            if n == 1:
                return len(v.ts_list)
            return len(v._cam_ts[ci]) if ci < len(v._cam_ts) else 0

        def newest_known(ci: int) -> int:
            if n == 1:
                return v.ts_list[-1] if v.ts_list else 0
            ts = v._cam_ts[ci] if ci < len(v._cam_ts) else []
            return ts[-1] if ts else 0

        def worst_state():
            now = time.monotonic()
            states = [v._live_health(i, now) for i in range(n)]
            bad = [s for s in states if s[0] == "fault"]
            if bad:
                return "fault", bad[0][2], bad[0][1]
            return (("active" if any(s[0] == "active" for s in states) else "idle"),
                    "", states[0][1])

        before = [known_count(i) for i in range(n)]
        v._btn_auto_follow.setChecked(True)          # → _start_online_mode
        check("live mode started", v._online_mode)
        print(f"\nwaiting out LIVE_START_GRACE_S ({m.LIVE_START_GRACE_S:.0f} s) …")
        end = time.monotonic() + m.LIVE_START_GRACE_S + 1.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

        # ── 1. Sanity: a frame written into the SAME (previous-hour) folder is still
        #       followed. If this fails, nothing below means anything.
        print("\n[1] new frame in the already-open hour folder")
        newest += step_ns
        for ci, name in enumerate(cam_names):
            write_frame(cam_folder_lists[ci][0], name, newest + ci, src)
        ok = B.wait_for(lambda: all(known_count(i) > before[i] for i in range(n)), 30.0)
        check("frame in the open folder is picked up", ok,
              f"known {[known_count(i) for i in range(n)]} was {before}")

        # ── 2. THE ROLLOVER. The archive moves into the current hour's folder, which
        #       is not in opened_folders / _cam_folder_lists yet. _probe_hour_folder +
        #       the new_folders plumbing are the only things that can find it.
        print("\n[2] archive rolls into the current hour folder")
        before = [known_count(i) for i in range(n)]
        prev_newest = [newest_known(i) for i in range(n)]
        newest += step_ns
        roll_paths = []
        for ci, name in enumerate(cam_names):
            d = cur_dir / name
            d.mkdir(parents=True, exist_ok=True)
            roll_paths.append(write_frame(d, name, newest + ci, src))
        # Discovery costs one poll interval per camera plus the negative-probe TTL that
        # may already be armed for this candidate; give it a generous window.
        budget = max(30.0, m._NEG_PROBE_TTL_S + m.ONLINE_POLL_MAX_INTERVAL_S * 3)
        t0 = time.monotonic()
        found = B.wait_for(
            lambda: all(known_count(i) > before[i] for i in range(n)), budget)
        dt_found = time.monotonic() - t0
        check("the new hour folder is discovered", found,
              f"known {[known_count(i) for i in range(n)]} was {before} "
              f"after {budget:.0f} s")
        print(f"        discovery latency {dt_found:.1f} s")
        check("the rolled-over frame is the newest known",
              all(newest_known(i) > prev_newest[i] for i in range(n)),
              f"newest {[newest_known(i) for i in range(n)]} was {prev_newest}")
        # The paint is asynchronous — allow it the same budget the dot's lag latch
        # does, so this measures the pipeline and not the poll-to-check race.
        painted = B.wait_for(
            lambda: all(m._at(v._cam_shown_ts_ns, i, 0) >= newest_known(i)
                        for i in range(n)),
            m.CAM_UNDISPLAYED_RED_S)
        check("the rolled-over frame reaches the screen", painted,
              f"shown {[m._at(v._cam_shown_ts_ns, i, 0) for i in range(n)]} "
              f"newest {[newest_known(i) for i in range(n)]}")

        # ── 3. THE POINT OF THE FILE. If discovery failed, does the app SAY so? A dot
        #       that stays green through a missed rollover is the reported bug: live
        #       mode on, everything green, no new images.
        print("\n[3] does the dot tell the truth about it?")
        st, reason, tip = worst_state()
        if found:
            check("following live reads as healthy", st != "fault", f"{reason} {tip}")
        else:
            check("a missed rollover is reported as a fault", st == "fault",
                  f"state={st!r} tip={tip!r}")

        # ── 4. The folder the app now polls must be the NEW one, or the next frames
        #       are missed too even though this one was found.
        print("\n[4] the poll set moved to the new hour")
        if n == 1:
            polled = [str(p) for p in m.poll_scan_folders(v.opened_folders)]
        else:
            polled = [str(p) for p in m.poll_scan_folders(v._cam_folder_lists[0])]
        want = str(cur_dir / cam_names[0])
        check("poll_scan_folders points at the current hour", want in polled,
              f"polls {polled}")
    finally:
        try:
            v._btn_auto_follow.setChecked(False)
            v._hard_reset_runtime()
        except Exception:
            pass
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL CHECKS PASSED" if not FAILURES else
                  f"{len(FAILURES)} CHECK(S) FAILED:\n  " + "\n  ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
