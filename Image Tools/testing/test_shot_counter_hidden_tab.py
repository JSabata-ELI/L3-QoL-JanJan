"""Shot counter keeps counting while its Image Slider tab is out of sight.

A hidden layout tab (Viewer.suspend()) stops everything that costs CPU or share
reads — except the shot counter: the energy poll keeps running, and every frame the
folder watchers or the slow safety-net listing find is counted, also across a UTC
hour rollover. Coming back (resume()) must not count anything a second time.

Drives the real Viewer offscreen on synthetic folders (…/YYYY/M/D/H/CAM). The
energy archive is made unreachable, so the cameras alone decide (and no test touches
the real archiver).

    python testing/test_shot_counter_hidden_tab.py            # one camera
    python testing/test_shot_counter_hidden_tab.py --cams 3   # multi-camera
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
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def hour_dir(root: Path, when: datetime) -> Path:
    return root / str(when.year) / str(when.month) / str(when.day) / str(when.hour)


def run(m, app, cams: int) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="is_shot_hidden_"))
    now_utc = datetime.now(timezone.utc)
    prev_dir = hour_dir(tmp, now_utc - timedelta(hours=1))
    cur_dir = hour_dir(tmp, now_utc)
    cam_names, _ = B.make_synthetic_set(prev_dir, cams, 12)
    folders = [[prev_dir / n] for n in cam_names]

    def boom(*_a, **_k):
        raise OSError("archiver unreachable (test)")
    real_fetch = m.cpva.fetch_values
    m.cpva.fetch_values = boom

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    try:
        all_ts = sorted(t for lst in folders for d in lst for p in d.glob("*.png")
                        if (t := m.parse_unix_ns_from_name(p)))
        src = next(p for p in sorted(folders[0][0].glob("*.png"))
                   if p.stat().st_size > 1000)
        if cams == 1:
            v._ts_windows = None
            v._start_scan(list(folders[0]), axis_override=(all_ts[0], all_ts[-1]),
                          folder_label=str(folders[0][0]))
            B.wait_for(lambda: bool(v.ts_list), 120.0)
        else:
            v._start_multi_cam_scan(cam_names, folders, (all_ts[0], all_ts[-1]),
                                    False, None)
            B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0)
        v._btn_auto_follow.setChecked(True)            # live mode
        check("live mode started", v._online_mode)
        pump(app, 1.0)

        newest = all_ts[-1]
        step = 1_000_000_000

        def shoot(folder_of, k):
            nonlocal newest
            for _ in range(k):
                newest += step
                for ci, name in enumerate(cam_names):
                    d = folder_of(name)
                    d.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, d / f"{name}_{newest + ci * 1_000_000:019d}.png")
                pump(app, 0.25)

        shoot(lambda n: prev_dir / n, 3)
        B.wait_for(lambda: v._shot.count >= 3, 20.0)
        base = v._shot.count
        check("shots count while on screen", base >= 3, f"count {base}")

        v.suspend()
        check("tab is suspended", v._suspended)
        check("energy poll keeps running while hidden", v._shot_timer.isActive())
        shoot(lambda n: prev_dir / n, 4)
        ok = B.wait_for(lambda: v._shot.count >= base + 4, 30.0)
        check("frames in the open hour folder count while hidden", ok,
              f"count {v._shot.count}, want {base + 4}")
        base = v._shot.count

        # The archive moves into the next hour folder while the tab is hidden.
        shoot(lambda n: cur_dir / n, 3)
        budget = max(40.0, m._NEG_PROBE_TTL_S + m.ONLINE_POLL_MAX_INTERVAL_S * 3)
        ok = B.wait_for(lambda: v._shot.count >= base + 3, budget)
        check("hour rollover found and counted while hidden", ok,
              f"count {v._shot.count}, want {base + 3}")
        shown_hidden = v._shot.count

        v.resume()
        pump(app, 3.0)
        check("coming back counts nothing twice", v._shot.count == shown_hidden,
              f"count {v._shot.count}, was {shown_hidden}")
        if cams == 1:
            check("the hidden frames reached the list",
                  bool(v.ts_list) and v.ts_list[-1] >= newest - step)
    finally:
        m.cpva.fetch_values = real_fetch
        try:
            v._btn_auto_follow.setChecked(False)
            v._hard_reset_runtime()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=1)
    args = ap.parse_args()
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    print(f"{args.cams} camera(s)")
    run(m, app, args.cams)
    print("\n" + ("all checks passed" if not FAILURES else
                  f"{len(FAILURES)} FAILED:\n  " + "\n  ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
