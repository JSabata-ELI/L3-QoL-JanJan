"""Three Image Finder fixes from 12.10.2026.

1. **THE CONDITION SEARCH KEEPS TO THE TIME THE USER SET.** It returns the FIRST
   moment the PV met the condition — by design, one shot for every camera — but
   only inside marked regions when there are any, and otherwise inside each day's
   Time window. There is no "whole days / regions" switch any more: marked regions
   are the narrower time, so they win on their own.
2. **THE DEFAULT WINDOW IS 07:00–21:00, TODAY TOO.** The shared picker stops today
   at the next hour (it feeds live views); this tab searches the whole lab day.
3. **CLICKING A DAY IN THE PV SEARCH LIST KEEPS THE LIST WHERE IT WAS.** The list
   is rebuilt after every load, and the rebuild used to throw the scroll back to
   the first day.

Offscreen, no share and no archiver.

    python testing/test_condition_window_and_day_list.py
"""
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY1, DAY2 = date(2026, 9, 1), date(2026, 9, 2)
CAM = ("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns(day, h, mi=0):
    from zoneinfo import ZoneInfo
    return int(datetime(day.year, day.month, day.day, h, mi,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def hour_of(t_ns):
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(t_ns / 1e9, tz=ZoneInfo("Europe/Prague")).hour


def check_condition(m):
    print("\n=== the condition keeps to the set time ===")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    # SBW above 10 at 06:00 (before the lab day), 09:00 and 15:00 on day 1, and
    # 08:00 on day 2.
    samples = {
        DAY1: [(ns(DAY1, 6), 12.0), (ns(DAY1, 7), 3.0), (ns(DAY1, 9), 11.0),
               (ns(DAY1, 12), 2.0), (ns(DAY1, 15), 14.0)],
        DAY2: [(ns(DAY2, 8), 13.0)],
    }

    # 13.10.2026: the search reads forward slice by slice and stops at the first
    # hit, so the archiver is faked at the request level and the requests counted.
    requests: list = []

    def fake_fetch(channel, a, b, **_k):
        requests.append((a, b))
        return [s for d in samples.values() for s in d if a <= s[0] <= b]
    m.cpva.fetch_values = fake_fetch
    m.cpva.peek_day = lambda *a, **k: None
    m._cam_totalpower_channel = lambda cam: None          # cameras: no verdict
    w = m.ImageFinderWidget()
    cond = {"channel": m.CPVA_SBW4_CHANNEL, "op": ">", "value": 10.0, "scope": "days"}

    def win(day, h0, h1):
        return [m.daypicker.seg_bounds_ns(m.daypicker.PickSeg(day, h0, 0, h1, 0))]

    res = w._find_condition_moment([DAY1, DAY2], [CAM], cond, {},
                                   windows_by_day={DAY1: win(DAY1, 7, 21),
                                                   DAY2: win(DAY2, 7, 21)})
    check("the first match INSIDE 07–21, not the 06:00 one",
          res.get("status") == "found" and hour_of(res["target_ns"]) == 9,
          repr(res.get("status")) + (f" {hour_of(res['target_ns'])} h"
                                     if res.get("target_ns") else ""))
    day1_reqs = [r for r in requests if r[0] < ns(DAY2, 0)]
    check("day 1 stops reading at the first hit (07–08, 08–10: two requests)",
          len(day1_reqs) == 2, f"{len(day1_reqs)} request(s)")

    # A day the cache already holds costs no request at all.
    requests.clear()
    m.cpva.peek_day = lambda ch, key, **k: SimpleNamespace(
        samples=samples.get(datetime.strptime(key, "%Y-%m-%d").date(), []))
    res = w._find_condition_moment([DAY1], [CAM], cond, {},
                                   windows_by_day={DAY1: win(DAY1, 7, 21)})
    check("a cached day is answered from the cache",
          hour_of(res["target_ns"]) == 9 and not requests, f"{len(requests)} request(s)")
    m.cpva.peek_day = lambda *a, **k: None
    res = w._find_condition_moment([DAY1, DAY2], [CAM], cond, {},
                                   windows_by_day={DAY1: win(DAY1, 13, 21),
                                                   DAY2: win(DAY2, 7, 21)})
    check("a narrower window on day 1 moves it to 15:00",
          res.get("status") == "found" and hour_of(res["target_ns"]) == 15
          and res["day"] == DAY1)
    res = w._find_condition_moment([DAY1, DAY2], [CAM], cond, {},
                                   windows_by_day={DAY1: win(DAY1, 16, 21),
                                                   DAY2: win(DAY2, 7, 21)})
    check("nothing left on day 1 → the first on day 2",
          res.get("status") == "found" and res["day"] == DAY2)
    region = {"t_start_ns": ns(DAY1, 14), "t_end_ns": ns(DAY1, 16), "index": 1}
    res = w._find_condition_moment([DAY1, DAY2], [CAM], dict(cond, scope="regions"),
                                   {DAY1: [region]},
                                   windows_by_day={DAY1: win(DAY1, 7, 21),
                                                   DAY2: win(DAY2, 7, 21)})
    check("with a region marked, only inside it",
          res.get("status") == "found" and hour_of(res["target_ns"]) == 15)

    print("\n=== every day gets its own first match ===")
    import threading
    DAY3 = DAY2 + timedelta(days=1)
    samples[DAY2] = [(ns(DAY2, 8), 2.0)]                  # day 2: never above 10
    samples[DAY3] = [(ns(DAY3, 10), 20.0), (ns(DAY3, 11), 30.0)]
    m._image_is_nonempty = lambda p: True
    w._ensure_scan_cache = lambda: None
    asked: list = []

    def fake_resolve(cam_name, target_ns, cancel_evt, _x):
        asked.append((cam_name, target_ns))
        return Path(f"{cam_name}_{target_ns}.png"), hour_of(target_ns)
    w._resolve_frame_at = fake_resolve
    cams = [CAM, ("C03-030-PAP1FF-_-IMG", "PAP1FF", Path("y"))]
    days = [DAY1, DAY2, DAY3]
    wins = {d: win(d, 7, 21) for d in days}
    logs: list = []
    results, per_day = w._condition_results(days, cams, cond, {}, wins,
                                            threading.Event(), logs.append)
    for cam in cams:
        got = [(row[0], hour_of(int(row[2].stem.rsplit("_", 1)[1])))
               for row in results.get(cam[0], [])]
        check(f"{cam[1]}: one frame on day 1 (09 h) and one on day 3 (10 h)",
              got == [(DAY1, 9), (DAY3, 10)], repr(got))
    check("day 2 is reported as no match",
          per_day[DAY2]["status"] == "never_met" and per_day[DAY1]["status"] == "found"
          and per_day[DAY3]["status"] == "found", repr({str(k): v.get("status")
                                                         for k, v in per_day.items()}))
    check("the log names day 2", any("02.09.2026" in ln and "never" in ln for ln in logs))
    row = results[CAM[0]][0]
    check("a frame knows the moment it was asked for",
          row[3].get("asked_ns") == ns(DAY1, 9))

    print("\n=== the dialog picks the scope itself ===")
    dlg = make_dialog(m)
    dlg._cond_on.setChecked(True)
    # Since 27.10.2026 no row comes ticked — the operator ticks the PV to search by.
    next(iter(dlg._cond_rows.values()))["box"].setChecked(True)
    check("no whole-days / regions switch", not hasattr(dlg, "_cond_scope_days"))
    check("no region → the Time window", dlg.get_config()["condition"]["scope"] == "days")
    t0 = m.cpva.day_bounds_ns(DAY1.strftime("%Y-%m-%d"))[0]
    dlg._on_span(dlg._ns_to_x(t0 + 8 * 3600 * 10**9, DAY1),
                 dlg._ns_to_x(t0 + 9 * 3600 * 10**9, DAY1))
    check("a marked region → regions", dlg.get_config()["condition"]["scope"]
          == "regions", repr(dlg.get_config()["condition"]["scope"]))
    dlg.deleteLater()
    w.deleteLater()


def make_dialog(m, n_days: int = 1):
    from PySide6.QtCore import QDate
    t0 = m.cpva.day_bounds_ns(DAY1.strftime("%Y-%m-%d"))[0]
    series = [(t0 + i * 60 * 1_000_000_000, 10.0 + (i % 30) / 10.0)
              for i in range(24 * 60)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))
    days = [DAY1 + timedelta(days=i) for i in range(n_days)]
    return m.PVRegionSearchDialog([CAM], [QDate(d.year, d.month, d.day) for d in days])


def check_default_window(m):
    print("\n=== the default window is 07–21, today too ===")
    w = m.ImageFinderWidget()
    seg = w._segments[0]
    check("the tab opens on 07:00–21:00",
          (seg.h_from, seg.m_from, seg.h_to, seg.m_to) == (7, 0, 21, 0), repr(seg))
    check("an unpicked day falls back to 07:00–21:00",
          w._day_window(date(2020, 1, 1)) == (7, 0, 21, 0))
    w.deleteLater()


def check_day_list(m, app):
    print("\n=== clicking a day keeps the list where it was ===")
    dlg = make_dialog(m, n_days=21)
    dlg.resize(1200, 800)
    dlg.show()                         # offscreen platform: laid out, nothing on screen
    lst = dlg._day_list
    for _ in range(20):
        app.processEvents()
    bar = lst.verticalScrollBar()
    check("three weeks do not fit, the list scrolls", bar.maximum() > 0,
          str(bar.maximum()))
    bar.setValue(bar.maximum())
    app.processEvents()
    item = lst.topLevelItem(18)
    lst.setCurrentItem(item)           # what a click does
    end = time.monotonic() + 5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    check("the clicked day is the one shown", dlg._focus_i == 18, str(dlg._focus_i))
    check("the list did not jump back to the top", bar.value() > 0,
          f"scroll {bar.value()} of {bar.maximum()}")
    cur = lst.currentItem()
    check("and the clicked day is still in view",
          cur is not None and lst.visualItemRect(cur).intersects(lst.viewport().rect()))
    dlg._refresh_day_list()
    check("a rebuild keeps the place too", bar.value() > 0, str(bar.value()))
    dlg.deleteLater()


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    check_default_window(m)
    check_day_list(m, app)
    check_condition(m)
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
