"""Live mode must never re-pick the time window by itself as "hh:00 – 23:59".

The report (27.10.2026): the Time window sometimes changed by itself to 0:00–23:59 or to
"the current hour – 23:59". Two paths did that:

  [1] the live day roll (_do_live_rebase_to_day), when a frame of a new day arrives;
  [2] a tab saved while live, after a restart or Duplicate (start_from_tab_state).

User rule: such a window is just the one hour (a frame at 7:30 gives 7:00–8:00); the
later hours come in through live mode, which keeps the window's end open.

  [3] the day roll only ever moves onto TODAY — the diag log showed it re-picking onto
      26.08 for weeks, because an old camera folder counted as "a later day".

Runs offscreen, no share, no scan (the reload is stubbed out):

    python testing/test_live_window_never_to_midnight.py
"""
from datetime import datetime, timedelta

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def window_of(v):
    return (v.last_pick_hour_from, v.last_pick_min_from,
            v.last_pick_hour_to, v.last_pick_min_to)


def main():
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    reloads = []
    v._reload_with_last_cameras = lambda: reloads.append(1)
    v._stop_online_mode = lambda *a, **k: None
    v.cancel_scan = lambda *a, **k: None

    print("[1] live day roll, first frame of the new day at 7:30")
    now = datetime.now(m.TZ_PRAGUE)
    t730 = now.replace(hour=7, minute=30, second=0, microsecond=0)
    v._online_mode = True
    v.last_pick_cam_names = ["camA"]
    v._do_live_rebase_to_day(m.ns_from_dt(t730))
    check("window is 7:00–8:00", window_of(v) == (7, 0, 8, 0), f"got {window_of(v)}")
    check("date is the frame's day", v.last_pick_date == t730.date())
    check("reload asked for", len(reloads) == 1)

    print("\n[1b] at 23:30 the hour runs to the end of the day")
    t2330 = now.replace(hour=23, minute=30, second=0, microsecond=0)
    v._do_live_rebase_to_day(m.ns_from_dt(t2330))
    check("window is 23:00–23:59", window_of(v) == (23, 0, 23, 59), f"got {window_of(v)}")

    print("\n[2] a tab saved while live comes back on the current hour")
    v._online_mode = False
    v.set_pending_tab_state({"cameras": ["camA"], "live": True})
    ok = v.start_from_tab_state()
    h = datetime.now(m.TZ_PRAGUE).hour
    want = (h, 0) + tuple(m.hour_end_hm(h))
    check("restored", ok)
    check("window is the current hour", window_of(v) == want,
          f"got {window_of(v)}, want {want}")

    print("\n[3] the day roll never moves onto a past day")
    v._online_mode = True
    v.last_pick_cam_names = ["camA"]
    old = datetime(2026, 8, 20, 10, 0, tzinfo=m.TZ_PRAGUE)
    v.ts_list = [m.ns_from_dt(old)]
    v._cam_ts = []
    v._is_multi_cam = lambda: False
    past = m.ns_from_dt(datetime(2026, 8, 26, 8, 0, tzinfo=m.TZ_PRAGUE))
    check("an old day is not a roll", not v._live_day_rolled(past))
    today = m.ns_from_dt(datetime.now(m.TZ_PRAGUE))
    check("today still is", v._live_day_rolled(today))
    v.ts_list = [today - 60 * 10**9]
    check("same day is not", not v._live_day_rolled(today))

    print("\n" + ("ALL PASS" if not FAILURES else
                  "FAILURES:\n  " + "\n  ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
