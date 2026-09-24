"""Load data and Load images are two buttons, and the second lives off the first.

Reading the PVs is the archiver; finding the picture of every day on every camera
walks the image share, and that is where the minutes of a long search go. So:

  * **Load data** reads the numbers and nothing else — no folder on the share is
    touched, and the rows say "no image loaded" in green, because nothing failed.
  * **Load images** over the SAME days, hours, PVs and bands does not ask the
    archiver again: it takes the numbers already on screen and only looks for the
    frames. Picking other cameras in between must not spoil that — the numbers do
    not depend on the camera.
  * Retyping a band DOES spoil it: those are different numbers, so the archiver is
    read again.
  * **The rows stay on screen.** Load images fills in the rows Load data put there
    — the same table, the same row per day — and the new Image column goes from a
    dash to a tick as each one is answered. Emptying the table and refilling it is
    what made a reused run look exactly like a fresh one.
  * **The frames are looked for side by side.** The (day × camera) units go through
    a pool, so a run costs about its slowest few units instead of the sum of all
    of them.
  * **A frame is not decoded to be listed.** The search tests what a stat() can
    tell it; a blank frame is caught where it is decoded anyway — in the preview —
    and the row is corrected then.

Offscreen, with the archiver and the share both replaced by stubs:

    python testing/test_sf_load_split.py
"""
import os
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication            # noqa: E402

import sf_t                                            # noqa: E402

FAILURES: "list[str]" = []

DAYS = [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
CAMS = ["C02-101-CAM", "C02-102-CAM"]
COL = "sbw4"

API_CALLS: "list[date]" = []       # every archiver read, by day
SHARE_CALLS: "list[tuple]" = []    # every look for a frame, by (day, camera)


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ── the stubs ─────────────────────────────────────────────────────────────────
def fake_load_api_for_day(day, cols, log=None, csv_root=None, on_col_done=None,
                          span_ns=None):
    """Ten shots an hour apart from 08:00, all inside the band."""
    API_CALLS.append(day)
    tz = sf_t.PRAGUE if sf_t.PRAGUE else timezone.utc
    per_col, merged = {}, []
    for i in range(10):
        dt = datetime(day.year, day.month, day.day, 8 + i, 0, tzinfo=tz)
        row = {"_dt": dt.replace(tzinfo=None),
               "_ns": int(dt.timestamp() * 1e9), COL: f"{10.0 + i * 0.01:.3f}"}
        merged.append(row)
    per_col[COL] = merged
    if on_col_done is not None:
        on_col_done(1, 1)
    return merged, per_col, {COL: {"source": "api", "status": "ok"}}


def fake_find_image_in_day(day, cam, dt_obj, ts_ns, hour_cache, images_root=None,
                           day_dir=None, scan_cache=None):
    SHARE_CALLS.append((day, cam))
    return Path(f"/fake/{day}/{cam}/frame.png"), Path(f"/fake/{day}/{cam}")


def fake_day_image_folder(day, images_root=None):
    return Path(f"/fake/{day}")


def fake_find_hour_folder(day, hour_utc, images_root=None):
    return Path(f"/fake/{day}/{hour_utc:02d}")


sf_t._load_api_for_day = fake_load_api_for_day
sf_t._find_image_in_day = fake_find_image_in_day
sf_t._day_image_folder = fake_day_image_folder
sf_t._find_hour_folder = fake_find_hour_folder
REAL_IMAGE_PROBLEM = sf_t._image_problem     # kept: one check uses the real one
sf_t._image_problem = lambda p: None


# ── driving the tab ───────────────────────────────────────────────────────────
def run(app, w, want_images: bool, timeout_s: float = 20.0):
    API_CALLS.clear()
    SHARE_CALLS.clear()
    w._start_search(want_images)
    t0 = time.monotonic()
    while w._search_running and time.monotonic() - t0 < timeout_s:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return not w._search_running


def setup(w):
    tz = sf_t.PRAGUE if sf_t.PRAGUE else timezone.utc
    wins = []
    for d in DAYS:
        s = datetime(d.year, d.month, d.day, 7, 0, tzinfo=tz)
        e = datetime(d.year, d.month, d.day, 21, 0, tzinfo=tz)
        wins.append((int(s.timestamp() * 1e9), int(e.timestamp() * 1e9)))
    w._tw_windows = wins
    w._pv_cfg = [{"col": COL, "target": 10.0, "tol": 1.0, "filter": True,
                  "scale": 1.0}]
    w._pv_rows = []                      # no widgets: _pv_cfg is read as it stands
    w._sync_pv_cfg_from_rows = lambda: None
    w._set_selected_cameras(CAMS)


# ── the checks ────────────────────────────────────────────────────────────────
def test_data_only(app, w):
    print("\nLoad data: the numbers, and not one folder on the share")
    check("the run finished", run(app, w, False))
    check("every day was read", sorted(set(API_CALLS)) == DAYS, str(API_CALLS))
    check("the share was never touched", not SHARE_CALLS, str(SHARE_CALLS))
    rows = w._cam_results[CAMS[0]]
    check("one row per day, in every camera tab",
          len(rows) == len(DAYS) and len(w._cam_results[CAMS[1]]) == len(DAYS),
          f"{len(rows)} / {len(w._cam_results[CAMS[1]])}")
    check("and they are 'data' rows, not failures",
          all(r.status == "data" for r in rows),
          str([r.status for r in rows]))
    check("with the shots that matched", all(len(r.rows_in_tol) == 10 for r in rows),
          str([len(r.rows_in_tol) for r in rows]))
    check("no picture is claimed", all(r.img_path is None for r in rows))
    # The row must not be painted like a failure — that is the whole point.
    tbl = w._cam_tables[CAMS[0]]
    bg = tbl.item(0, 0).background().color().name()
    fg = tbl.item(0, sf_t.RES_COL_STATUS).foreground().color().name()
    check("the row is green, not red", bg == "#d4edda", bg)
    check("and its text is dark", fg == "#155724", fg)
    check("the status says what happened",
          "no image loaded" in tbl.item(0, sf_t.RES_COL_STATUS).text(),
          tbl.item(0, sf_t.RES_COL_STATUS).text())


def test_day_detail_without_images(app, w):
    """The shot list of a data run opens — and still touches no folder."""
    print("\nThe day detail after Load data: the shots and the curve, no share")
    run(app, w, False)
    SHARE_CALLS.clear()
    w._table.selectRow(1)
    w._on_table_double_clicked(w._table.model().index(1, 0))
    app.processEvents()
    check("the shot list opened", w._day_open())
    check("with the day's shots in it", w._day_table.rowCount() == 10,
          str(w._day_table.rowCount()))
    w._day_table.selectRow(3)
    app.processEvents()
    check("picking a shot reads nothing off the share", not SHARE_CALLS,
          str(SHARE_CALLS))
    check("and the Frame time column is out of the way",
          w._day_img_col < 0 or w._day_table.isColumnHidden(w._day_img_col))
    w._day_window.reject()
    app.processEvents()


def test_images_reuse(app, w):
    print("\nLoad images straight after: the frames only")
    check("the run finished", run(app, w, True))
    check("the archiver was NOT read again", not API_CALLS, str(API_CALLS))
    check("every day × camera looked for its frame",
          len(SHARE_CALLS) == len(DAYS) * len(CAMS), str(len(SHARE_CALLS)))
    rows = w._cam_results[CAMS[0]]
    check("the rows carry a picture now",
          all(r.status == "ok" and r.img_path is not None for r in rows),
          str([r.status for r in rows]))
    check("and the same shots as before",
          all(len(r.rows_in_tol) == 10 for r in rows))


def test_other_cameras_still_reuse(app, w):
    print("\nOther cameras picked in between: still no archiver")
    run(app, w, False)                       # fresh numbers
    w._set_selected_cameras([CAMS[1]])
    check("the run finished", run(app, w, True))
    check("the archiver was NOT read again", not API_CALLS, str(API_CALLS))
    check("only the one camera was looked for",
          len(SHARE_CALLS) == len(DAYS), str(SHARE_CALLS))
    w._set_selected_cameras(CAMS)


def test_failed_day_is_read_again(app, w):
    """A day the archiver could not answer is not an answer worth keeping."""
    print("\nA day that failed is read again by Load images, not failed twice")
    bad = DAYS[1]
    real = sf_t._load_api_for_day
    fixed = [False]                    # the archiver comes back between the runs

    def flaky(day, cols, **kw):
        if day == bad and not fixed[0]:
            API_CALLS.append(day)
            return [], {}, {COL: {"source": "none", "status": "error"}}
        return real(day, cols, **kw)

    sf_t._load_api_for_day = flaky
    run(app, w, False)
    rows = {r.day: r.status for r in w._cam_results[CAMS[0]]}
    check("the day the archiver refused is a no-data row",
          rows.get(bad) == "no_data", str(rows))

    fixed[0] = True
    ok = run(app, w, True)
    sf_t._load_api_for_day = real
    check("the run finished", ok)
    check("only that one day was read again", API_CALLS == [bad], str(API_CALLS))
    rows = {r.day: r.status for r in w._cam_results[CAMS[0]]}
    check("and it has a picture now like the others",
          all(s == "ok" for s in rows.values()), str(rows))


def img_marks(w, cam):
    """The Image column of that camera's tab, by day."""
    tbl = w._cam_tables[cam]
    rows = w._cam_results[cam]
    return {rows[r].day: tbl.item(r, sf_t.RES_COL_IMG).text()
            for r in range(tbl.rowCount())}


def test_rows_stay_and_tick(app, w):
    print("\nLoad images after Load data: the same rows, ticked off one by one")
    run(app, w, False)
    tbl_before = w._cam_tables[CAMS[0]]
    rows_before = w._cam_results[CAMS[0]]
    marks = img_marks(w, CAMS[0])
    check("after Load data every row shows a dash",
          set(marks.values()) == {sf_t.IMG_MARK_NOT_TRIED}, str(marks))

    check("the run finished", run(app, w, True))
    check("the table was NOT thrown away and built again",
          w._cam_tables[CAMS[0]] is tbl_before)
    check("its rows are the same rows", w._cam_results[CAMS[0]] is rows_before)
    check("one row per day still — nothing doubled",
          tbl_before.rowCount() == len(DAYS), str(tbl_before.rowCount()))
    marks = img_marks(w, CAMS[0])
    check("and every row now wears the tick",
          set(marks.values()) == {sf_t.IMG_MARK_FOUND}, str(marks))


def test_missing_frame_is_a_cross(app, w):
    print("\nA day whose frame is not on the share: a cross, not a tick")
    run(app, w, False)
    gone = DAYS[2]
    real = sf_t._find_image_in_day

    def no_frame(day, cam, *a, **kw):
        if day == gone:
            SHARE_CALLS.append((day, cam))
            return None, None
        return real(day, cam, *a, **kw)

    sf_t._find_image_in_day = no_frame
    ok = run(app, w, True)
    sf_t._find_image_in_day = real
    check("the run finished", ok)
    marks = img_marks(w, CAMS[0])
    check("the day with no frame shows the cross",
          marks.get(gone) == sf_t.IMG_MARK_MISSING, str(marks))
    check("the other days keep the tick",
          all(m == sf_t.IMG_MARK_FOUND for d, m in marks.items() if d != gone),
          str(marks))


def test_frames_overlap(app, w):
    print("\nThe frame hunts run side by side, not one after another")
    run(app, w, False)
    seen_threads: "set[str]" = set()
    real = sf_t._find_image_in_day
    unit_s = 0.05

    def slow(day, cam, *a, **kw):
        seen_threads.add(threading.current_thread().name)
        time.sleep(unit_s)
        return real(day, cam, *a, **kw)

    sf_t._find_image_in_day = slow
    t0 = time.monotonic()
    ok = run(app, w, True)
    took = time.monotonic() - t0
    sf_t._find_image_in_day = real
    n_units = len(DAYS) * len(CAMS)
    check("the run finished", ok)
    check("more than one unit was in flight", len(seen_threads) > 1,
          str(sorted(seen_threads)))
    # One at a time this is n_units × unit_s; through the pool it is about one unit.
    check(f"and it took about one unit, not all {n_units}",
          took < n_units * unit_s * 0.6, f"{took:.3f} s of {n_units * unit_s:.2f} s")


def test_blank_frame_is_the_previews_job(app, w):
    print("\nA blank frame: not decoded by the search, marked when it is previewed")
    import numpy as np
    from PIL import Image as PilImage

    with tempfile.TemporaryDirectory() as td:
        blank = Path(td) / "blank.png"
        PilImage.fromarray(np.zeros((8, 8), dtype=np.uint16), mode="I;16").save(blank)
        check("listing a frame costs no decode — a blank one passes",
              REAL_IMAGE_PROBLEM(blank) is None, str(REAL_IMAGE_PROBLEM(blank)))
        deep = REAL_IMAGE_PROBLEM(blank, deep=True)
        check("and the deep test still knows it is blank",
              deep == "image is blank (all zero)", str(deep))

    run(app, w, False)
    run(app, w, True)
    day = DAYS[1]
    w._on_row_blank(day, CAMS[0])
    app.processEvents()
    marks = img_marks(w, CAMS[0])
    check("a row the preview found blank turns into a cross",
          marks.get(day) == sf_t.IMG_MARK_MISSING, str(marks))
    rows = {r.day: (r.status, r.reason) for r in w._cam_results[CAMS[0]]}
    check("and the row says why",
          rows.get(day) == ("no_image", "image is blank (all zero)"), str(rows.get(day)))


def test_new_band_reloads(app, w):
    print("\nA retyped band is different numbers — and is read again")
    run(app, w, False)
    w._pv_cfg[0]["target"] = 10.05
    check("the run finished", run(app, w, True))
    check("the archiver was read", sorted(set(API_CALLS)) == DAYS, str(API_CALLS))
    w._pv_cfg[0]["target"] = 10.0


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
    w = sf_t.ShotFinderWidget()
    w.resize(1400, 800)
    setup(w)

    test_data_only(app, w)
    test_day_detail_without_images(app, w)
    test_images_reuse(app, w)
    test_other_cameras_still_reuse(app, w)
    test_failed_day_is_read_again(app, w)
    test_rows_stay_and_tick(app, w)
    test_missing_frame_is_a_cross(app, w)
    test_frames_overlap(app, w)
    test_blank_frame_is_the_previews_job(app, w)
    test_new_band_reloads(app, w)

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
