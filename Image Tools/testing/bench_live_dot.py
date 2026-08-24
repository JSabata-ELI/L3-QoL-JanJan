"""Assert the live refresh-dot verdict (Viewer._live_health), headless.

The reported bug was that an IDLE source turned every dot red: green used to require a
new file within CAM_DOT_FRESH_S, so five seconds after a run ended the app declared
itself broken while nothing was wrong. Case 1 below pins that and must never be allowed
to regress — it is the whole reason this file exists.

The other cases pin the five faults that DO deserve red, each of which was previously
invisible (swallowed by `except Exception: pass` in the poll workers, or by
`img.isNull()` being conflated with a stale generation in the load handlers).

Runs offscreen against synthetic local files, no share and no network, in well under a
minute:

  python testing/bench_live_dot.py
  python testing/bench_live_dot.py --cams 1        # the single-camera predicate + summary
"""
import argparse
import os
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


def pump(app, seconds: float):
    """Spin the real event loop — the poll timer and the dot tick must actually run."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def worst(v, n_cams: int):
    """(state, reason) over every camera, via the shipping predicate."""
    now = time.monotonic()
    states = [v._live_health(i, now) for i in range(n_cams)]
    bad = [s for s in states if s[0] == "fault"]
    if bad:
        return "fault", bad[0][2], bad[0][1]
    return ("active" if any(s[0] == "active" for s in states) else "idle"), "", states[0][1]


def write_frame(m, folder: Path, cam: str, ts_ns: int, truncated: bool = False):
    """Append one new frame with a valid 19-digit ns name. truncated=True writes a file
    that passes the name filter and cannot possibly decode."""
    p = folder / f"{cam}_{ts_ns:019d}.png"
    if truncated:
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 180)
        return p
    src = next(f for f in sorted(folder.glob("*.png")) if f.stat().st_size > 1000)
    shutil.copyfile(src, p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=2)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_dot_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(tmp, args.cams, args.frames)
    step_ns = int(1e9 / 3.3)

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    try:
        all_ts = [t for lst in cam_folder_lists for f in lst for p in f.glob("*.png")
                  if (t := m.parse_unix_ns_from_name(p))]
        # Online mode leaves the window's end open, so an axis ending at the last
        # existing frame still accepts the newer ones written below.
        axis = (min(all_ts), max(all_ts))
        if args.cams == 1:
            # ONE camera must go through the single-camera path, the way open_folder
            # does — _start_multi_cam_scan leaves opened_folders empty, so _online_poll
            # returns before polling anything and nothing would be exercised at all.
            v._ts_windows = None
            v._start_scan(list(cam_folder_lists[0]), axis_override=axis,
                          folder_label=str(cam_folder_lists[0][0]))
            if not B.wait_for(lambda: bool(v.ts_list), 120.0):
                print("scan did not finish"); return 1
            n = 1
        else:
            v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
            if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
                print("scan did not finish"); return 1
            n = len(v._cam_names)
        newest = max(all_ts)

        v._btn_auto_follow.setChecked(True)       # → _start_online_mode
        check("live mode started", v._online_mode)
        print(f"\nwaiting out LIVE_START_GRACE_S ({m.LIVE_START_GRACE_S:.0f} s) …")
        pump(app, m.LIVE_START_GRACE_S + 1.0)

        # ── 1. IDLE — THE REPORTED BUG. Nothing is written for well over
        #       CAM_DOT_FRESH_S; every dot must stay green for the whole hold.
        print("\n[1] idle source (the reported bug)")
        reds = []
        end = time.monotonic() + (m.CAM_DOT_FRESH_S * 3)
        while time.monotonic() < end:
            app.processEvents()
            st, reason, _tip = worst(v, n)
            if st == "fault":
                reds.append(reason)
            time.sleep(0.02)
        check("idle stays green for 3x CAM_DOT_FRESH_S", not reds,
              f"went red: {sorted(set(reds))}" if reds else "")
        st, _r, tip = worst(v, n)
        check("idle reports state 'idle'", st == "idle", f"got {st!r}")
        check("idle tooltip explains itself", "no problem" in tip, tip)

        # ── 2. FRESH — a real new frame arrives and is displayed.
        print("\n[2] a new frame arrives")
        newest += step_ns
        for ci, lst in enumerate(cam_folder_lists):
            write_frame(m, lst[0], cam_names[ci], newest + ci)
        if not B.wait_for(lambda: v._live_health(0, time.monotonic())[0] == "active", 20.0):
            pump(app, 0.5)
        st, reason, tip = worst(v, n)
        check("arrival reads 'active', not a fault", st == "active", f"{st!r} {reason} {tip}")

        # ── 3. READ FAILURE — a file that exists and cannot be decoded.
        print("\n[3] a new file that cannot be read")
        newest += step_ns
        for ci, lst in enumerate(cam_folder_lists):
            write_frame(m, lst[0], cam_names[ci], newest + ci, truncated=True)
        ok = B.wait_for(
            lambda: worst(v, n)[0] == "fault", m.CAM_UNDISPLAYED_RED_S + 6.0)
        st, reason, tip = worst(v, n)
        check("undecodable new frame turns the dot red", ok and st == "fault",
              f"{st!r} {reason}")
        check("reason is read or lag", reason in ("read", "lag"), f"{reason!r} {tip}")
        # …and a VALID frame afterwards clears it.
        newest += step_ns
        for ci, lst in enumerate(cam_folder_lists):
            write_frame(m, lst[0], cam_names[ci], newest + ci)
        ok = B.wait_for(lambda: worst(v, n)[0] != "fault", 25.0)
        check("a valid frame afterwards clears it", ok, worst(v, n)[1])

        # ── 4. FOLDER ERROR — os.listdir raises for every folder.
        print("\n[4] folder unreachable (WinError 53)")
        real_listdir = m.os.listdir

        def boom(path, *a, **k):
            if str(tmp) in str(path):
                # 4-arg form: only that one sets .winerror on Windows. OSError(53, msg)
                # sets errno instead, which is what _oserror_message keys off — and is
                # NOT what a real failing SMB listdir raises.
                raise OSError(0, "network path not found", None, 53)
            return real_listdir(path, *a, **k)

        m.os.listdir = boom
        try:
            ok = B.wait_for(lambda: worst(v, n)[0] == "fault",
                            m.CAM_FOLDER_ERR_RED_S + 20.0)
            st, reason, tip = worst(v, n)
            check("unreachable folder turns the dot red", ok and st == "fault",
                  f"{st!r} {reason}")
            check("reason is 'folder'", reason == "folder", f"{reason!r}")
            check("tooltip names the WinError", "WinError 53" in tip, tip)
        finally:
            m.os.listdir = real_listdir
        ok = B.wait_for(lambda: worst(v, n)[0] != "fault",
                        m.CAM_FOLDER_ERR_RED_S + 20.0)
        check("recovers once the folder lists again", ok, worst(v, n)[1])

        # ── 5. POLL HUNG — os.listdir blocks instead of raising (the common case).
        print("\n[5] folder listing hangs")

        def sleeper(path, *a, **k):
            if str(tmp) in str(path):
                time.sleep(120)
            return real_listdir(path, *a, **k)

        m.os.listdir = sleeper
        try:
            ok = B.wait_for(lambda: worst(v, n)[0] == "fault",
                            m.CAM_POLL_HUNG_S + 20.0)
            st, reason, tip = worst(v, n)
            check("hung listing turns the dot red", ok and st == "fault",
                  f"{st!r} {reason}")
            check("reason is 'poll'", reason == "poll", f"{reason!r} {tip}")
        finally:
            m.os.listdir = real_listdir

        # ── 6. STALL — the GUI thread was blocked, so nothing was displayed.
        print("\n[6] GUI thread blocked")
        time.sleep(m.ONLINE_STALL_RED_S + 2.0)     # no processEvents on purpose
        st, reason, tip = worst(v, n)
        check("a blocked event loop turns the dot red", st == "fault", f"{st!r}")
        check("reason is 'stall'", reason == "stall", f"{reason!r} {tip}")
        pump(app, 1.0)

        # ── 7. The Info-panel summary agrees with the tiles, and live-off is grey.
        print("\n[7] summary + live off")
        st_sum, tip_sum, _r = v._live_health_summary()
        st_worst = worst(v, n)[0]
        check("summary matches the worst tile",
              (st_sum == "fault") == (st_worst == "fault"),
              f"summary={st_sum!r} tiles={st_worst!r}")
        v._btn_auto_follow.setChecked(False)       # → _stop_online_mode
        pump(app, 0.5)
        check("live off stops online mode", not v._online_mode)
        dot = v._multi_grid._cam_views[0]._refresh_dot if n > 1 else None
        if dot is not None:
            check("live off greys the tile dots", "#444" in dot.styleSheet(),
                  dot.styleSheet())
    finally:
        try:
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
