"""Layout tabs in the Image Slider (slider_tabs.py + Viewer.suspend / resume).

Checks, on a synthetic two-camera live set (offscreen, nothing shown):
  1. switching to another tab suspends the first: its timers stop and F11 is off;
  2. a frame written while it is hidden is only NOTED (camera list) — the shared
     timeline is not extended and no tile is asked to paint;
  3. switching back shows the newest frame at once, with no re-scan (same _gen);
  4. leaving the Image Slider tab suspends the current layout tab too;
  5. a static time window and a live tab survive export -> JSON -> restore;
  6. a handoff (receive_external_folder) opens a NEW tab; closing it leaves one.

Run: python testing/test_slider_tabs.py   (from the Image Tools folder)
"""
import bench_common as B

import json
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path


def main() -> int:
    m = B.load_slider()
    import importlib.util
    spec = importlib.util.spec_from_file_location("slider_tabs", str(B.HERE / "slider_tabs.py"))
    st_mod = importlib.util.module_from_spec(spec)
    sys.modules["slider_tabs"] = st_mod
    spec.loader.exec_module(st_mod)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_tabs_"))
    st_mod.TABS_PATH = tmp / "slider_tabs.json"

    class TestViewer(m.Viewer):
        def auto_start_online(self):      # the real one opens a modal dialog
            pass

    fails = []

    def check(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    def pump(s):
        end = time.monotonic() + s
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    slider = st_mod.SliderTabs(TestViewer)
    slider.set_outer_visible(True)
    pump(0.2)
    va = slider.active()
    cam_names, lists = B.make_synthetic_set(tmp / "data", 2, 30, hz=2.0)
    folders = [l[0] for l in lists]
    all_ts = [t for f in folders for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    va._ts_windows = None
    va._start_multi_cam_scan(cam_names, lists, (min(all_ts), max(all_ts)), False, None)
    check(B.wait_for(lambda: all(len(c) for c in (va._cam_ts or [[]])), 60.0),
          "scan finished")
    va._btn_auto_follow.setChecked(True)
    check(va._online_mode, "live mode on in tab 1")
    pump(1.0)

    vb = slider.new_tab("B")
    pump(0.3)
    check(va._suspended and not vb._suspended, "tab 1 suspended, tab 2 running")
    check(not va._cam_stuck_timer.isActive()
          and not va._pv_health_timer.isActive() and not va._cam_dot_timer.isActive(),
          "tab 1 timers stopped")
    # The shot counter is the one thing a hidden tab keeps doing: the energy poll,
    # and the (slowed) live poll that keeps the folder watchers on the right hour.
    check(va._shot_timer.isActive() and va._online_timer.isActive(),
          "tab 1 shot counter still running")
    check(not any(sc.isEnabled() for sc in va._app_shortcuts), "tab 1 F11 off")
    check(all(sc.isEnabled() for sc in vb._app_shortcuts), "tab 2 F11 on")

    paints = []
    orig = va._per_cam_display_one
    va._per_cam_display_one = lambda *a, **k: (paints.append(a), orig(*a, **k))[1]
    n_shared = len(va.items)
    gen = va._gen
    src = sorted(folders[0].glob("*.png"))[-1]
    newest = max(all_ts)
    new_ts = [newest + i * 400_000_000 for i in (1, 2, 3)]
    for t in new_ts:
        shutil.copy(src, folders[0] / f"{cam_names[0]}_{t:019d}.png")
    if m._DIRWATCH_AVAILABLE:
        check(B.wait_for(lambda: va._cam_ts[0][-1] == new_ts[-1], 10.0),
              "hidden tab noted the new frames")
    pump(0.5)
    check(len(va.items) == n_shared, "hidden tab: shared timeline not extended")
    check(not paints, "hidden tab: no tile asked to paint")

    slider._tabs.setCurrentIndex(0)
    pump(0.5)
    check(not va._suspended and vb._suspended, "back on tab 1: it runs, tab 2 sleeps")
    check(B.wait_for(lambda: va.items and va.items[-1].ts_ns == new_ts[-1], 10.0),
          "tab 1 timeline reaches the newest frame")
    check(va._gen == gen and va._scan_task is None, "no re-scan on return")
    check(va._online_timer.isActive() and va._cam_stuck_timer.isActive(),
          "tab 1 timers running again")
    check(B.wait_for(lambda: m._at(va._cam_shown_ts_ns, 0, 0) == new_ts[-1], 10.0),
          "tab 1 tile shows the newest frame")

    slider.set_outer_visible(False)
    check(va._suspended, "leaving Image Slider suspends the current tab")
    slider.set_outer_visible(True)
    check(not va._suspended, "coming back resumes it")

    # ── export / restore ──
    vc = slider.new_tab("static")
    vc.last_pick_cam_names = ["CAM-A", "CAM-B"]
    vc.last_pick_date = date(2026, 9, 30)
    vc.last_pick_hour_from, vc.last_pick_min_from = 10, 15
    vc.last_pick_hour_to, vc.last_pick_min_to = 12, 0
    seg = m.PickSeg(vc.last_pick_date, 10, 15, 12, 0)
    w = m.seg_bounds_ns(seg)
    vc._ts_windows = [w]
    vc._last_pick_windows = [w]
    vc.last_pick_axis_override = w
    exported = json.loads(json.dumps(vc.export_tab_state()))
    vd = TestViewer()
    calls = []
    vd._reload_with_last_cameras = lambda: calls.append(1)
    vd.set_pending_tab_state(exported)
    check(vd.start_from_tab_state() and calls, "static tab restores and reloads")
    check(vd.last_pick_cam_names == ["CAM-A", "CAM-B"]
          and vd.last_pick_date == date(2026, 9, 30)
          and (vd.last_pick_hour_from, vd.last_pick_min_from) == (10, 15)
          and vd._ts_windows == [w] and not vd._pending_online_mode,
          "static window comes back the same")

    ve = TestViewer()
    ve._reload_with_last_cameras = lambda: calls.append(2)
    ve.set_pending_tab_state({"cameras": ["CAM-A"], "live": True})
    check(ve.start_from_tab_state() and ve._pending_online_mode
          and ve.last_pick_date == m.datetime.now(m.TZ_PRAGUE).date(),
          "live tab restores as live, today")

    slider.save()
    saved = json.loads(st_mod.TABS_PATH.read_text(encoding="utf-8"))
    check([t["name"] for t in saved["tabs"]] == ["Panel 1", "B", "static"],
          "tab names saved")
    check(saved["tabs"][0].get("live") is True, "live flag saved")

    # ── handoff opens a new tab ──
    n = slider._tabs.count()
    ok = slider.receive_external_folder(folders[1], discrete=True, cam_name=cam_names[1])
    pump(0.3)
    check(ok and slider._tabs.count() == n + 1, "handoff opened a new tab")
    for page in list(slider._pages())[1:]:
        slider._close_page(page)
    pump(0.3)
    check(slider._tabs.count() == 1, "closing leaves one tab")

    for v in (vd, ve):
        v.deleteLater()
    print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
