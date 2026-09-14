"""Three rules the Image Finder now follows.

1. **THE PICKS ARE A LIST, AND THE LIST CAN BE EDITED.** A moment and a marked
   region are the same thing to the search — one frame per camera — so both are
   in one table under the graph, each with its own ✕. Before this the regions
   lived in the 275 px sidebar and the moments nowhere, so a moment could only be
   taken back by Undo, in the order it was made: four picks deep, dropping the
   second one meant undoing three good ones and making them again.

2. **A NUMBER STAYS WITH ITS PICK, AND NO TWO PICKS SHARE ONE.** Moments and
   regions are numbered from ONE counter, so "2" names exactly one pick and the
   frames can be labelled with plain numbers. Deleting pick 3 leaves 1, 2, 4 — on
   the graph, in the tables and in the config handed to the wall. "Renumber" is
   the only thing that ever closes the gaps, because a number that moves while the
   list is being tidied is not the number being talked about.

3. **THE SEARCH BUTTON COUNTS WHAT IS PICKED, ALWAYS.** It went stale on the
   delete paths, which reached none of the refreshers, and offered to search
   regions that had already been taken off.

Plus the camera list: a click MARKS the camera on the wall and opens nothing.

Offscreen, no share and no archiver.
"""
import sys
from datetime import date
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)
CAM = ("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def make_dialog(m):
    """PV Search on one day, with a day of samples and no archiver behind it."""
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    t0 = m.cpva.day_bounds_ns(DAY.strftime("%Y-%m-%d"))[0]
    series = [(t0 + i * 60 * 1_000_000_000, 10.0 + (i % 30) / 10.0)
              for i in range(24 * 60)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))
    dlg = m.PVRegionSearchDialog([CAM], [QDate(DAY.year, DAY.month, DAY.day)])
    dlg._series = {DAY: {m.CPVA_SBW4_CHANNEL: series}}
    return dlg, series


def pick_moment(dlg, ts_ns):
    dlg._set_moment_from_x(dlg._ns_to_x(ts_ns, DAY))


def mark_region(dlg, a_ns, b_ns):
    dlg._on_span(dlg._ns_to_x(a_ns, DAY), dlg._ns_to_x(b_ns, DAY))


def table_rows(dlg):
    t = dlg._pick_table
    return [(t.item(r, 0).text(), t.item(r, 2).text(), t.item(r, 4).text())
            for r in range(t.rowCount())]


# ── 1. both kinds on one list ────────────────────────────────────────────────
def check_table(m):
    dlg, series = make_dialog(m)
    for i in (100, 300, 500):
        pick_moment(dlg, series[i][0])
    mark_region(dlg, series[600][0], series[700][0])
    mark_region(dlg, series[800][0], series[900][0])

    rows = table_rows(dlg)
    check("every pick is on the table, both kinds", len(rows) == 5, str(len(rows)))
    check("the moments are there", sum(1 for r in rows if r[1] == "Moment") == 3)
    check("and the regions with them", sum(1 for r in rows if r[1] == "Region") == 2)
    check("they read in clock order",
          [r[2] for r in rows] == sorted(r[2] for r in rows),
          str([r[2] for r in rows]))
    check("the line above counts them and says what a search costs",
          "3 moments" in dlg._lbl_picks.text()
          and "2 regions" in dlg._lbl_picks.text()
          and "5 frame(s) per camera" in dlg._lbl_picks.text(),
          dlg._lbl_picks.text())
    check("the button counts what is picked",
          dlg._btn_search.text() == "🎯 Search 5 selections",
          dlg._btn_search.text())
    check("ONE run of numbers over both kinds — no pick shares a number",
          [r[0] for r in rows] == ["1)", "2)", "3)", "4)", "5)"],
          str([r[0] for r in rows]))
    return dlg, series


# ── 2. deleting, and what the numbers do ─────────────────────────────────────
def check_delete_and_numbers(m, dlg, series):
    dlg._delete_moment(dlg._moments[1])          # the middle moment
    rows = table_rows(dlg)
    check("the ✕ takes one moment off and leaves the rest",
          sum(1 for r in rows if r[1] == "Moment") == 2)
    check("the numbers of the survivors do NOT move",
          [r[0] for r in rows if r[1] == "Moment"] == ["1)", "3)"],
          str([r[0] for r in rows]))
    check("the search button follows the deletion",
          dlg._btn_search.text() == "🎯 Search 4 selections",
          dlg._btn_search.text())

    dlg._delete_region(dlg._regions[0]["id"])
    rows = table_rows(dlg)
    check("a region goes the same way",
          sum(1 for r in rows if r[1] == "Region") == 1)
    check("and the region left keeps ITS number",
          [r[0] for r in rows if r[1] == "Region"] == ["5)"],
          str([r[0] for r in rows]))
    check("the button follows a deleted region too — it used to go stale here",
          dlg._btn_search.text() == "🎯 Search 3 selections",
          dlg._btn_search.text())

    cfg = dlg.get_config()
    check("the config carries the numbers as they stand, gaps and all",
          cfg["moment_nos"] == [1, 3], str(cfg.get("moment_nos")))
    check("and the region reaches the wall as pick 5",
          [r["index"] for r in cfg["regions"][DAY]] == [5],
          str([r["index"] for r in cfg["regions"][DAY]]))

    dlg._renumber_picks()
    rows = table_rows(dlg)
    check("Renumber, and only Renumber, closes the gaps — over BOTH kinds",
          [r[0] for r in rows] == ["1)", "2)", "3)"], str(rows))
    check("the moments are 1…n again",
          dlg.get_config()["moment_nos"] == [1, 2],
          str(dlg.get_config()["moment_nos"]))
    check("and the region takes the number after them",
          [r["index"] for r in dlg.get_config()["regions"][DAY]] == [3],
          str([r["index"] for r in dlg.get_config()["regions"][DAY]]))

    # Undo is still the one gesture that takes a whole step back.
    dlg._undo_pick()
    check("Undo puts the numbering back the way it was",
          dlg.get_config()["moment_nos"] == [1, 3],
          str(dlg.get_config()["moment_nos"]))
    dlg._undo_pick()
    check("and another Undo brings the deleted region back",
          len(dlg._regions) == 2, str(len(dlg._regions)))


# ── 3. the numbers reach the search ──────────────────────────────────────────
def check_numbers_reach_search(m):
    picks_seen = {}

    class Stub:
        def _checked_cameras(self):
            return [CAM]

        def _log(self, *_a, **_k):
            pass

        def _load_moments(self, picks):
            picks_seen["picks"] = [dict(p) for p in picks]

    stub = Stub()
    stub._start_pv_search = m.ImageFinderWidget._start_pv_search.__get__(stub)
    stub._start_pick_search = m.ImageFinderWidget._start_pick_search.__get__(stub)
    stub._pending_pv_cfg = None
    stub._shot_stamps = []
    cfg = {"cameras": [CAM], "days": [DAY], "regions": {}, "condition": None,
           "moments_ns": [111, 222], "moment_nos": [1, 4], "moment_ns": 111,
           "primary_channel": None, "snap_stamps": [],
           "start_hour": 0, "max_hour": 23}
    stub._start_pv_search(cfg)
    check("the wall is asked for the numbers the window handed over",
          [p["index"] for p in picks_seen.get("picks", [])] == [1, 4],
          str(picks_seen.get("picks")))

    picks_seen.clear()
    cfg.pop("moment_nos")
    stub._start_pv_search(cfg)
    check("a config without them still numbers 1…n",
          [p["index"] for p in picks_seen.get("picks", [])] == [1, 2],
          str(picks_seen.get("picks")))


# ── 4. a click on a camera opens nothing ─────────────────────────────────────
def check_camera_click(m):
    from PySide6.QtCore import QModelIndex
    seen = {"marked": None, "window": 0}

    class FakeWall:
        # `add=True` is the real signature: every click ADDS a camera to what is
        # marked, and the panel no longer passes the flag at all.
        def mark_camera(self, cam, add=True):
            seen["marked"] = (cam, add)
            return True

    class Stub:
        _wall = FakeWall()

        def _sel_table_name(self, _row):
            return CAM[0]

        def _show_frame_window(self):
            seen["window"] += 1

        def _log(self, *_a, **_k):
            pass

    stub = Stub()
    stub._on_sel_table_clicked = \
        m.ImageFinderWidget._on_sel_table_clicked.__get__(stub)

    class Idx:
        @staticmethod
        def row():
            return 0

    stub._on_sel_table_clicked(Idx())
    check("clicking a camera marks it on the wall, adding it",
          seen["marked"] == (m.extract_display_label(CAM[0]), True),
          str(seen["marked"]))
    check("and opens NO window", seen["window"] == 0, str(seen["window"]))

    wall = m._DayWall()
    wall.resize(800, 400)
    # Pretend both frames are already decoded, so the wall starts no reader
    # thread for two files that do not exist (it would still be running at
    # interpreter shutdown and print a traceback over the results).
    for p in (Path("a.png"), Path("b.png")):
        wall._raw[p] = None
    wall.set_cells([
        {"day": DAY, "cam": "PTM11WNF", "cam_folder": CAM[0],
         "path": Path("a.png"), "ts_ns": 1, "status": "found", "meta": {}},
        {"day": DAY, "cam": "PAM1FF", "cam_folder": "other",
         "path": Path("b.png"), "ts_ns": 2, "status": "found", "meta": {}},
    ])
    check("the wall marks every frame of that camera",
          wall.mark_camera("PTM11WNF") is True
          and wall._shared.sel == {Path("a.png")},
          str(wall._shared.sel))
    check("a camera with nothing on the wall says so instead of doing nothing",
          wall.mark_camera("NOT_HERE") is False)


def main() -> int:
    m = load_finder()
    print("=== moments and regions on one list ===")
    dlg, series = check_table(m)
    print("\n=== deleting a pick, and what the numbers do ===")
    check_delete_and_numbers(m, dlg, series)
    print("\n=== the numbers reach the wall ===")
    check_numbers_reach_search(m)
    print("\n=== a click on a camera marks it, and opens nothing ===")
    check_camera_click(m)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("   ", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
