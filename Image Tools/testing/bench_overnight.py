"""The reported failure, end to end: live mode left running overnight.

bench_live_rollover.py covers a ONE-hour gap (and, with --day-boundary, the 23:00 ->
00:00 candidate). Neither reaches the case that was actually reported: live mode set
at 16:00, images stopping at 19:00, and the next one arriving at 08:05 the following
morning. That gap is ~13 hours, far wider than the three-hour forward walk, and it
produced a silent green freeze — the archive had moved on and the app was still
listing yesterday's last hour folder.

So this drives the real poll over real folders with a gap of that size, and checks
the two things that have to happen: the morning's folder is FOUND, and the arrival is
recognised as a new day rather than merged onto yesterday's axis.

The re-base itself is recorded rather than run, because it reloads through
DEFAULT_OPEN_ROOT — the archive share, which no temp folder can stand in for. What
the re-base leaves behind is checked in test_day_roll.py instead.

    python testing/bench_overnight.py
    python testing/bench_overnight.py --cams 3     # multi-cam grid path
"""
import argparse
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def hour_dir(root: Path, when: datetime) -> Path:
    """…/YYYY/M/D/H — unpadded, the way hour_dirs_for_windows builds it."""
    return root / str(when.year) / str(when.month) / str(when.day) / str(when.hour)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=1)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_night_"))
    now_utc = datetime.now(timezone.utc)
    # Yesterday evening: late enough that no three-hour walk from it reaches today,
    # whatever time of day the test is run at.
    last_utc = (now_utc - timedelta(days=1)).replace(hour=17)
    last_dir, cur_dir = hour_dir(tmp, last_utc), hour_dir(tmp, now_utc)
    print(f"open folder:  {last_dir}   (yesterday 17:00 UTC)")
    print(f"next write:   {cur_dir}   (today, current UTC hour)")
    print(f"gap:          {(now_utc - last_utc).total_seconds() / 3600:.0f} hours\n")

    # The frame timestamps have to match the folder they sit in: the new-day
    # decision reads the DATE off the timestamp, not off the path.
    seed = int(last_utc.replace(minute=0, second=0, microsecond=0).timestamp() * 1e9)
    cam_names, _ = B.make_synthetic_set(last_dir, args.cams, args.frames,
                                        start_ns=seed)
    cam_folder_lists = [[last_dir / n] for n in cam_names]

    rebased: "list[int]" = []
    real_rebase = m.Viewer._live_rebase_to_day
    m.Viewer._live_rebase_to_day = lambda self, ts: rebased.append(int(ts))

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        all_ts = [t for lst in cam_folder_lists for d in lst
                  for p in d.glob("*.png") if (t := m.parse_unix_ns_from_name(p))]
        axis = (min(all_ts), max(all_ts))
        src = next(p for p in sorted(cam_folder_lists[0][0].glob("*.png"))
                   if p.stat().st_size > 1000)
        # Live mode needs a remembered camera set — the re-base reloads with it.
        v.last_pick_cam_names = list(cam_names)

        if args.cams == 1:
            v._ts_windows = None
            v._start_scan(list(cam_folder_lists[0]), axis_override=axis,
                          folder_label=str(cam_folder_lists[0][0]))
            if not B.wait_for(lambda: bool(v.ts_list), 120.0):
                print("scan did not finish")
                return 1
            v._btn_auto_follow.setChecked(True)      # -> _start_online_mode
        else:
            v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, True, None)
            if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
                print("scan did not finish")
                return 1
        check("live mode started", v._online_mode)

        print(f"\nwaiting out LIVE_START_GRACE_S ({m.LIVE_START_GRACE_S:.0f} s) …")
        end = time.monotonic() + m.LIVE_START_GRACE_S + 1.0
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

        print("\n[A] the next morning's first image is written")
        known_before = (len(v.ts_list) if args.cams == 1
                        else sum(len(c) for c in v._cam_ts))
        # A timestamp on TODAY, inside the hour whose folder it lands in.
        morning_ns = int(now_utc.replace(minute=1, second=0, microsecond=0)
                         .timestamp() * 1e9)
        for i, cam in enumerate(cam_names):
            (cur_dir / cam).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, cur_dir / cam / f"{cam}_{morning_ns + i:019d}.png")

        found = B.wait_for(lambda: bool(rebased), 40.0)
        check("the new day's hour folder is found across the overnight gap", found,
              f"rebased={rebased} known={known_before}")
        check("the arrival is recognised as a NEW DAY",
              bool(rebased) and rebased[0] >= morning_ns,
              f"rebased={rebased} want>={morning_ns}")

        if args.cams == 1:
            joined = any(Path(f) == cur_dir / cam_names[0]
                         for f in v.opened_folders)
            where = [str(f) for f in v.opened_folders]
        else:
            joined = all(any(Path(f) == cur_dir / cam_names[i] for f in fl)
                         for i, fl in enumerate(v._cam_folder_lists))
            where = [str(f) for fl in v._cam_folder_lists for f in fl]
        check("the new folder joined the poll set", joined, f"folders={where}")

        # Everything the old code could see is still yesterday — the point of the
        # re-base is that this is what gets thrown away, not stretched.
        check("the timeline was NOT stretched across the night",
              (len(v.ts_list) if args.cams == 1
               else sum(len(c) for c in v._cam_ts)) == known_before,
              "the frame was merged onto yesterday's axis instead")
    finally:
        m.Viewer._live_rebase_to_day = real_rebase
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
    sys.exit(main())
