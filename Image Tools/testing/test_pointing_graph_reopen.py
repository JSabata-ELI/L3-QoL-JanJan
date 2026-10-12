"""Pointing graph in the real Image Slider: Close / Open graph, clicks, camera change
(01.10.2026).

The user asked for: closing the graph turns the button into "Open graph", and the
results stay in memory until the camera changes. And clicking a point must load
that point's frame again.

What is checked, with the real Viewer built in process (never shown) over two
synthetic cameras:

  * after Run Analysis the graph is up and the button reads "✕ Close graph";
  * Close graph hides it, the button reads "Open graph", the results are kept;
  * Open graph brings back the same graph — the deleted points stay deleted;
  * a click on a point moves the viewer to that point's moment, and NO point
    is hidden afterwards (the old bug: every navigation hid the later points);
  * ordinary navigation does not hide points either, and rings the right one;
  * scanning the SAME cameras again keeps the results;
  * opening DIFFERENT cameras throws them away and greys the button out.

    PYTHONIOENCODING=utf-8 python testing/test_pointing_graph_reopen.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_reopen_"))
os.environ["APPDATA"] = str(_TMP / "appdata")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common as B                                              # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def scan(v, m, names, lists):
    all_ts = [m.parse_unix_ns_from_name(p)
              for lst in lists for f in lst for p in f.glob("*.png")]
    all_ts = [t for t in all_ts if t]
    axis = (min(all_ts), max(all_ts))
    v._start_multi_cam_scan(names, lists, axis, False, None)
    return B.wait_for(lambda: v.btn_pointing.isEnabled()
                      and all(len(c) for c in (v._cam_ts or [[]])), 120.0)


def main() -> int:
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    names, lists = B.make_synthetic_set(_TMP / "set1", 2, 40)
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass

    check("scan finished", scan(v, m, names, lists))
    v.run_pointing_analysis()
    ok = B.wait_for(lambda: v._pointing_task is None and not v._pointing_queue
                    and bool(v._pointing_sets), 120.0)
    check("Run Analysis produced graphs", ok, f"{len(v._pointing_sets)} sets")
    panel = v.pointing_panel
    check("the graph is up", not panel.isHidden())
    check("button reads Close graph", v.btn_pointing_close.text() == "✕ Close graph",
          v.btn_pointing_close.text())

    n_pts = len(panel._cx)
    panel._delete_indices([0, 1])
    v._close_pointing_panel()
    check("Close graph hides it", panel.isHidden())
    check("…the button reads Open graph", v.btn_pointing_close.text() == "Open graph",
          v.btn_pointing_close.text())
    check("…and stays clickable", v.btn_pointing_close.isEnabled())
    check("…the results are kept", bool(v._pointing_sets))

    v._close_pointing_panel()
    check("Open graph shows it again", not panel.isHidden())
    check("…button back to Close graph", v.btn_pointing_close.text() == "✕ Close graph")
    check("…same points, deletions kept",
          len(panel._cx) == n_pts and not panel._mask[0] and not panel._mask[1]
          and bool(panel._mask[2:].all()))

    # A click on a point (what the canvas emits) moves the viewer to its moment.
    k = n_pts // 2
    ts_k = int(panel._ts_int[k])
    panel.point_clicked.emit(k)
    app.processEvents()
    cam = v._pointing_sets[v._pointing_set_idx]["cam_idx"]
    shown = v._pointing_frame_ts_at(v.play_time_ns)
    check("a click moves the viewer to that point's frame", shown == ts_k,
          f"{shown} vs {ts_k} (camera {cam})")
    check("…and hides no point", panel._replay_ts is None)
    check("…the clicked point is ringed", panel._cur_idx == k)

    # Ordinary navigation: back to the first frame.
    first = int(panel._ts_int[2])
    v._display_multicam_at_time(first, update_slider=True)
    app.processEvents()
    check("navigating hides no point (the cause of 'clicks do nothing')",
          panel._replay_ts is None)
    check("…and rings the point of the frame on screen", panel._cur_idx == 2,
          f"{panel._cur_idx}")

    # Same cameras again → kept.
    v._close_pointing_panel()                       # closed while rescanning
    check("rescan of the same cameras", scan(v, m, names, lists))
    check("…keeps the results", bool(v._pointing_sets))
    check("…graph stays closed, button still Open graph",
          panel.isHidden() and v.btn_pointing_close.text() == "Open graph"
          and v.btn_pointing_close.isEnabled())

    # Different cameras → gone.
    names2, lists2 = B.make_synthetic_set(_TMP / "set2", 2, 30)
    names2 = [f"OTHER{n}" for n in names2]
    for old, new in zip(lists2, names2):
        target = old[0].parent / new
        old[0].rename(target)
        old[0] = target
    for f in lists2:
        for p in f[0].glob("*.png"):
            p.rename(p.with_name(p.name.replace("BENCHCAM", "OTHERBENCHCAM")))
    check("scan of other cameras", scan(v, m, names2, lists2))
    check("…throws the results away", not v._pointing_sets)
    check("…the button is greyed out", not v.btn_pointing_close.isEnabled()
          and v.btn_pointing_close.text() == "✕ Close graph")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
