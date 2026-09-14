"""Switching the camera tab must keep the day, the shot list and the picked shot.

Every camera tab answers the SAME question about the same days, so moving to another
camera is "show me this day, this shot, on that camera" — not "start over". The operator
reported all three being lost, so this pins them.

It also checks the search progress tracker: the bar must move in fractions of a day
(one day with one camera is a single step, and standing at 0 for the whole wait is what
made a search look dead), never go backwards, and end at exactly days × cameras — on the
normal path, on a day the archiver had no data for, and on a day it refused outright.

Runs offscreen, no share and no archiver:

    python testing/test_tab_switch_keeps_day.py
"""
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ── synthetic results ─────────────────────────────────────────────────────────
DAYS = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
CAMS = ["C02-101-CAM", "C02-102-CAM"]
COL = "sbw4"


def _ns(day: date, hour: int, minute: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=timezone.utc).timestamp() * 1e9)


def _row(day: date, hour: int, minute: int, val: float) -> dict:
    return {"_dt": datetime(day.year, day.month, day.day, hour, minute),
            "_ns": _ns(day, hour, minute), COL: f"{val:.2f}"}


def _result(day: date, cam: str, n_shots: int, status: str = "ok") -> dict:
    rows = [_row(day, 8 + i, 0, 10.0 + i * 0.1) for i in range(n_shots)]
    return {
        "day": day, "cam": cam, "status": status,
        "best_row": rows[0], "rows_in_tol": rows,
        "col": COL, "actual": 10.0, "diff": 0.0, "target_csv": 10.0,
        "hour_folder": None, "per_col": {COL: rows},
        "search_cols": [COL], "extra_cols": [],
        "criteria_csv": [{"col": COL, "target_csv": 10.0, "tol_ui": 1.0}],
        "col_meta": {COL: {"source": "api", "status": "ok"}},
        "img_path": None, "folder_path": None, "display_vals": {},
        "reason": "",
    }


def fill(w, shots_by_cam: "dict[str, int]", no_data: "set | None" = None):
    """One row per day in every camera tab, the way the worker emits them."""
    no_data = no_data or set()
    w._rebuild_result_tabs(CAMS)
    for day in DAYS:
        for cam in CAMS:
            if (day, cam) in no_data:
                w._on_day_result({"day": day, "cam": cam, "status": "no_data",
                                  "reason": "no samples archived for this PV",
                                  "search_cols": [COL], "extra_cols": [],
                                  "criteria_csv": [{"col": COL, "target_csv": 10.0,
                                                    "tol_ui": 1.0}]})
            else:
                w._on_day_result(_result(day, cam, shots_by_cam[cam]))


def sel_row(table) -> int:
    rows = sorted(set(i.row() for i in table.selectedIndexes()))
    return rows[0] if rows else -1


def day_open(w) -> bool:
    """Is the shot list on screen? (panel today, window after the rework)"""
    fn = getattr(w, "_day_open", None)
    if fn is not None:
        return bool(fn())
    return not w._day_panel.isHidden()


# ── the checks ────────────────────────────────────────────────────────────────
def test_switch_keeps_day(app, w):
    print("\nSwitching camera keeps day + shot list + shot")
    fill(w, {CAMS[0]: 5, CAMS[1]: 5})
    app.processEvents()

    # Day 2 (row 1) opened, third shot picked.
    w._results_tabs.setCurrentIndex(0)
    w._table.selectRow(1)
    w._on_table_double_clicked(w._table.model().index(1, 0))
    app.processEvents()
    w._day_table.selectRow(2)
    app.processEvents()
    want_ns = w._day_rows[2]["_ns"]
    check("day list opened on camera 1", day_open(w) and w._day_dr.day == DAYS[1])

    w._results_tabs.setCurrentIndex(1)
    app.processEvents()
    check("day row still selected", sel_row(w._table) == 1,
          f"selected row = {sel_row(w._table)}")
    check("shot list still open", day_open(w),
          f"day_dr = {getattr(w._day_dr, 'day', None)}")
    check("same day in the list", w._day_dr is not None and w._day_dr.day == DAYS[1])
    check("same shot picked",
          w._day_dr is not None and sel_row(w._day_table) >= 0
          and w._day_rows[sel_row(w._day_table)]["_ns"] == want_ns)
    check("list belongs to the new camera", w._day_cam == CAMS[1],
          f"day_cam = {w._day_cam}")

    # …and back again.
    w._results_tabs.setCurrentIndex(0)
    app.processEvents()
    check("back on camera 1: day kept", sel_row(w._table) == 1)
    check("back on camera 1: list kept", day_open(w))
    check("back on camera 1: shot kept",
          w._day_rows and sel_row(w._day_table) >= 0
          and w._day_rows[sel_row(w._day_table)]["_ns"] == want_ns)


def test_switch_without_list(app, w):
    print("\nA day selected but not opened stays selected")
    fill(w, {CAMS[0]: 4, CAMS[1]: 4})
    app.processEvents()
    w._results_tabs.setCurrentIndex(0)
    w._table.selectRow(2)
    app.processEvents()
    w._results_tabs.setCurrentIndex(1)
    app.processEvents()
    check("day row carried over", sel_row(w._table) == 2,
          f"selected row = {sel_row(w._table)}")
    check("no shot list forced open", not day_open(w))


def test_switch_to_no_data_day(app, w):
    print("\nA day the other camera has no data for: row yes, list no")
    fill(w, {CAMS[0]: 3, CAMS[1]: 3}, no_data={(DAYS[1], CAMS[1])})
    app.processEvents()
    w._results_tabs.setCurrentIndex(0)
    w._table.selectRow(1)
    w._on_table_double_clicked(w._table.model().index(1, 0))
    app.processEvents()
    w._results_tabs.setCurrentIndex(1)
    app.processEvents()
    check("day row still selected", sel_row(w._table) == 1,
          f"selected row = {sel_row(w._table)}")
    check("no shot list for a day without data", not day_open(w))


def test_nearest_shot(app, w):
    print("\nOne shot fewer on the other camera: nearest shot wins")
    fill(w, {CAMS[0]: 6, CAMS[1]: 4})
    app.processEvents()
    w._results_tabs.setCurrentIndex(0)
    w._table.selectRow(0)
    w._on_table_double_clicked(w._table.model().index(0, 0))
    app.processEvents()
    w._day_table.selectRow(5)                     # 13:00 — camera 2 stops at 11:00
    app.processEvents()
    want_ns = w._day_rows[5]["_ns"]
    w._results_tabs.setCurrentIndex(1)
    app.processEvents()
    got = sel_row(w._day_table)
    check("last shot of the shorter list picked", got == 3, f"picked row {got}")
    check("and it is the nearest one",
          got >= 0 and all(abs(w._day_rows[got]["_ns"] - want_ns)
                           <= abs(r["_ns"] - want_ns) for r in w._day_rows))


def test_switch_mid_search(app, w):
    print("\nSwitching before the other camera's rows arrived")
    w._rebuild_result_tabs(CAMS)
    for cam in CAMS:                              # day 1 only, both cameras
        w._on_day_result(_result(DAYS[0], cam, 3))
    w._on_day_result(_result(DAYS[1], CAMS[0], 3))   # day 2: camera 1 only, so far
    app.processEvents()
    w._results_tabs.setCurrentIndex(0)
    w._table.selectRow(1)
    app.processEvents()
    w._results_tabs.setCurrentIndex(1)            # that day is not here yet
    app.processEvents()
    w._on_day_result(_result(DAYS[1], CAMS[1], 3))   # …and now it arrives
    app.processEvents()
    check("the awaited day gets selected when it lands", sel_row(w._table) == 1,
          f"selected row = {sel_row(w._table)}")


# ── progress tracker ──────────────────────────────────────────────────────────
def test_progress_tracker():
    print("\nSearch progress: monotone, fractional, exact at the end")
    Tracker = getattr(sf_t, "_ProgressTracker", None)
    if Tracker is None:
        check("_ProgressTracker exists", False, "not implemented yet")
        return

    for label, plan in (("normal", "ok"), ("a day with no data", "no_data"),
                        ("a day the archiver refused", "error")):
        seen: "list[float]" = []
        t = Tracker(DAYS, len(CAMS), seen.append)
        n_chans = 2
        for day in DAYS:                          # pre-warm, all days at once
            for _ in range(n_chans):
                t.pv_step(day, 1.0 / n_chans)
        for i, day in enumerate(DAYS):
            if i == 1 and plan != "ok":
                t.day_done(day)                   # nothing searchable this day
                continue
            t.pv_done(day)
            for _ in CAMS:
                t.cam_done(day)
        check(f"{label}: never goes backwards",
              all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])))
        check(f"{label}: moves before the first day is finished",
              any(0 < v < len(CAMS) for v in seen),
              f"first values {['%.2f' % v for v in seen[:3]]}")
        check(f"{label}: ends at days × cameras",
              abs(seen[-1] - len(DAYS) * len(CAMS)) < 1e-6,
              f"ended at {seen[-1]:.3f}")


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                      "QLabel { background: transparent; }")
    w = sf_t.ShotFinderWidget()
    w.resize(1400, 800)

    test_switch_keeps_day(app, w)
    test_switch_without_list(app, w)
    test_switch_to_no_data_day(app, w)
    test_nearest_shot(app, w)
    test_switch_mid_search(app, w)
    test_progress_tracker()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
