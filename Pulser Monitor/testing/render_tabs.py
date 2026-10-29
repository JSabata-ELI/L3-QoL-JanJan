"""Render every view of a result to PNG, to be looked at.

Uses QT_QPA_PLATFORM=minimal:enable_fonts — the real Windows fonts, and nothing on the
screen, so the keyboard focus is never taken. Offscreen would draw every glyph as a box.

    python testing/render_tabs.py                       (the scripted day of test_blinks)
    python testing/render_tabs.py --pkl <bench out>/2026-10-22/PD1M1.pkl
PNGs go to --out (default %TEMP%/pulser_render).
"""
import argparse
import os
import pickle
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "minimal:enable_fonts"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import main as pm  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def load(args):
    if args.pkl:
        with open(args.pkl, "rb") as f:
            d = pickle.load(f)
        return d["cam"], d["rois"], d["samples"], d["analysis"]
    import test_blinks as tb
    rois, ref = tb.prepared()
    samples, an, _full = tb.build_day(rois, ref)
    return tb.CAM, rois, samples, an


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl")
    ap.add_argument("--out", default=str(Path(os.environ.get("TEMP", "C:/Temp"))
                                         / "pulser_render"))
    ap.add_argument("--pulser", help="pulser to select on the map (default: most blinks)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    pm.apply_app_style(app)
    cam, rois, samples, an = load(args)
    w = pm.PulserMonitorWidget()
    w.resize(1600, 950)
    w._rois_by_camera[cam] = list(rois)
    w._results_by_camera = {cam: samples}
    w._analysis_by_camera = {cam: an}
    w._result_cam_combo.clear()
    w._result_cam_combo.addItem(cam)
    w._results = list(samples)
    w._rois = list(rois)
    w._tab_all.update_data(w._analysis_by_camera)
    w._render_all_tabs()
    w.show()
    app.processEvents()
    tag = Path(args.pkl).stem if args.pkl else "synthetic"

    def grab(name):
        app.processEvents()
        w.grab().save(str(out / f"{tag}_{name}.png"))

    # Map: Status, then Blinks, with the worst pulser selected.
    w._tabs.setCurrentWidget(w._tab_map)
    grab("map_status")
    w._tab_map._metric_combo.setCurrentText("Blinks")
    sel = args.pulser or (an.worst_blinkers(1)[0][0] if an.worst_blinkers(1) else rois[0].name)
    idx = next(i for i, r in enumerate(rois) if r.name == sel)
    w._tab_map._select_pulser(idx)
    grab("map_blinks")
    w._tab_map._metric_combo.setCurrentText("Uptime %")
    grab("map_uptime")
    w._tabs.setCurrentWidget(w._tab_stats)
    logs = w._tab_stats._tbl_s.parentWidget().parentWidget()
    for i in range(logs.count()):
        logs.setCurrentIndex(i)
        grab(f"stats_{i}")
    w._tabs.setCurrentWidget(w._tab_all)
    grab("alldata")
    w._tabs.setCurrentWidget(w._tab_run)
    grab("rungraph")
    print(f"written to {out}")
    w.close()


if __name__ == "__main__":
    main()
