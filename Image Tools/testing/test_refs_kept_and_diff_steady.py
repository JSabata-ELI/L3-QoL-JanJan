"""References survive a new time window; a threshold click does not jiggle the column.

The report (26.10.2026):

  3. "I pick a reference at 8:30, change the time window so 8:30 is not in it, and the
     reference is gone." Every scan emptied the per-camera reference arrays. The
     reference is now remembered by CAMERA NAME for the whole session and put back after
     each scan — only closing the program (or Remove ref) forgets it.

  6. "With a reference and Subtraction on, clicking the threshold makes the histograms
     reload and the whole left toolbar moves a little." Every click emptied the
     per-camera numbers, and the histogram column was rebuilt one camera at a time, each
     step a new height for the INFO panel above the settings. The old blocks now stay,
     marked "not final", until each camera's new numbers replace them.

Runs with real fonts and no window on screen:
    QT_QPA_PLATFORM=minimal:enable_fonts PYTHONIOENCODING=utf-8 python testing/test_refs_kept_and_diff_steady.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_refkeep_"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtWidgets import QApplication                          # noqa: E402

import is_t                                                          # noqa: E402
from test_remove_ref import make_items, _badge, app_stylesheet       # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def open_set(v, app, names, peaks):
    """What a scan of a new time window does to the camera set."""
    folders = [_TMP / f"{n}-_-IMG" for n in names]
    items = [make_items(f, peaks) for f in folders]
    v._setup_multi_cam(list(names), folders)
    v._cam_items = items
    v._cam_ts = [[it.ts_ns for it in c] for c in items]
    v._cam_current_idx = [len(c) - 1 for c in items]
    v._cam_disp_reset(len(names))
    app.processEvents()
    return items


def test_refs_kept(v, app):
    names = ["C03-081-CAMA", "C03-082-CAMB", "C03-083-CAMC"]
    items = open_set(v, app, names, [900, 2600])
    v._multi_grid._selected_set = {0, 1}
    v._cam_current_idx = [0, 0, 0]
    v._set_reference_frame()
    app.processEvents()
    ref_a, ref_b = v._cam_ref_paths[0], v._cam_ref_paths[1]
    check("references set on A and B", ref_a is not None and ref_b is not None
          and v._cam_ref_paths[2] is None)

    print("  a new time window (same cameras, other frames)")
    open_set(v, app, names, [1500, 1700, 1900])
    check("A keeps its reference", v._cam_ref_paths[0] == ref_a)
    check("B keeps its reference", v._cam_ref_paths[1] == ref_b)
    check("C still has none", v._cam_ref_paths[2] is None)
    check("the green badges are back on A and B",
          bool(_badge(v, 0)) and bool(_badge(v, 1)) and not _badge(v, 2),
          str([_badge(v, i) for i in range(3)]))
    check("the reference renders although its frame is outside the window",
          v._cam_ref_arr_for(0, 400) is not None)

    print("  another order of cameras: the reference follows the NAME, not the slot")
    open_set(v, app, ["C03-082-CAMB", "C03-083-CAMC"], [1200])
    check("B (now slot 0) has B's reference", v._cam_ref_paths[0] == ref_b)
    check("C (now slot 1) has none", v._cam_ref_paths[1] is None)

    print("  Remove ref forgets it for good")
    v._multi_grid._selected_set = {0}
    v._remove_reference_frame()
    open_set(v, app, names, [900])
    check("B has no reference after the next window", v._cam_ref_paths[1] is None)
    check("A still has its own", v._cam_ref_paths[0] == ref_a)
    v._multi_grid._selected_set = {0}
    v._remove_reference_frame()


def test_threshold_steady(v, app):
    names = ["C03-081-CAMA", "C03-082-CAMB", "C03-083-CAMC"]
    open_set(v, app, names, [900, 2600])
    v._multi_grid._selected_set = {0, 1, 2}
    v._cam_current_idx = [0, 0, 0]
    v._set_reference_frame()
    v.cb_subtract.setChecked(True)
    st = {"max": 40.0, "mean": 9.0, "min": 0.0, "above": 1234, "side": is_t.FULL_RES_SIDE,
          "hist": [5, 4, 3, 2, 1] * 10}
    for i in range(3):
        v._cam_diff_stats[i] = (("f", i), dict(st))
    v._flush_cam_diff_stats()
    app.processEvents()
    box = v._diff_hist_box
    h0 = box.height()
    n0 = sum(1 for b in v._diff_hist_blocks if b.isVisible())
    print(f"  before the click: {n0} blocks, box {h0} px")

    v.sub_threshold_sb.setValue(v.sub_threshold_sb.value() + 1)
    v._flush_cam_diff_stats()
    check("right after the click the old numbers stand in, marked not final",
          all(b.lbl.text().endswith("…") for b in v._diff_hist_blocks[:n0]),
          str([b.lbl.text() for b in v._diff_hist_blocks[:n0]]))
    # The tiles report back one at a time — the real re-render runs meanwhile, and a
    # tile can also come back with no numbers at all (not measured / evicted).
    heights, counts = [box.height()], [sum(1 for b in v._diff_hist_blocks if b.isVisible())]
    for cam_i in range(3):
        v._collect_cam_diff_stats(cam_i, ("never-measured", cam_i))
        v._flush_cam_diff_stats()
        for _ in range(20):
            app.processEvents()
        heights.append(box.height())
        counts.append(sum(1 for b in v._diff_hist_blocks if b.isVisible()))
    check("every camera's block stays during the re-render", counts == [n0] * 4,
          f"{counts}")
    check("the box keeps its height (the column below does not move)",
          heights == [h0] * 4, f"{h0} -> {heights}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    v = is_t.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v.resize(1500, 950)
    v.show()                       # minimal platform: nothing on screen, no focus
    app.processEvents()

    print("references across time windows")
    test_refs_kept(v, app)
    print("\nthreshold click with a reference and Subtraction on")
    test_threshold_steady(v, app)

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
