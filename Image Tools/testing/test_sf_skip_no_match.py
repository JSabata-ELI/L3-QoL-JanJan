"""A day with nothing in range is never walked for a picture.

Reported 25.09.2026, with two screenshots of the results table: the yellow rows —
the days where no shot fell inside the tolerance — took far longer than the green
ones, although Load data had already said there was nothing there. Measured cause:
the image half of the search read `rows_in_tol` nowhere, so those days went to the
frame pool like any other; and they are the WORST case, because a moment with no
frame near it never hits the early exit in `_find_image_in_day` and walks every
probed hour folder of every camera instead of stopping at the first.

The user's decision, asked on 25.09.2026: **always skip**. So:

  * a day whose `rows_in_tol` is empty gets one row per camera with the status
    "skipped" and the share is not touched for it;
  * the row is still there, still yellow, and says "image not searched" rather than
    claiming a frame was looked for and not found;
  * the progress bar still reaches the end — the skip steps the tracker once per
    camera, because an image run counts a day as n_cams steps;
  * a day that DOES have a match is unaffected.

Also pinned here: `_find_image_in_day` probes the shot's own hour and its two
neighbours and no further. IMG_MATCH_TOL_NS is 30 s, so a frame that close to a
moment inside hour H is in H or, at an edge, H±1 — never in H±2, and those two used
to cost two more folder listings per camera on exactly the slow days.

Offscreen, with the archiver and the share both replaced by stubs:

    python testing/test_sf_skip_no_match.py
"""
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# The console here is cp1250 and the statuses this test prints carry ⚠ and ✓.
# Setting PYTHONIOENCODING above is too late — stdout already exists.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication            # noqa: E402

import sf_t                                            # noqa: E402

FAILURES: "list[str]" = []

# Two days that match the band, two that hold nothing near it.
MATCH_DAYS = [date(2026, 9, 1), date(2026, 9, 3)]
MISS_DAYS = [date(2026, 9, 2), date(2026, 9, 4)]
DAYS = sorted(MATCH_DAYS + MISS_DAYS)
CAMS = ["C02-101-CAM", "C02-102-CAM"]
COL = "sbw4"
TARGET, TOL = 20.0, 0.5

API_CALLS: "list[date]" = []
SHARE_CALLS: "list[tuple]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ── the stubs ─────────────────────────────────────────────────────────────────
def fake_load_api_for_day(day, cols, log=None, csv_root=None, on_col_done=None,
                          span_ns=None):
    """A match day sits on the target; a miss day is a long way off it — but it
    still ANSWERS, which is what makes it yellow rather than red."""
    API_CALLS.append(day)
    tz = sf_t.PRAGUE if sf_t.PRAGUE else timezone.utc
    val = TARGET if day in MATCH_DAYS else -0.1
    merged = []
    for i in range(6):
        dt = datetime(day.year, day.month, day.day, 9 + i, 0, tzinfo=tz)
        merged.append({"_dt": dt.replace(tzinfo=None),
                       "_ns": int(dt.timestamp() * 1e9), COL: f"{val:.4f}"})
    if on_col_done is not None:
        on_col_done(1, 1)
    return merged, {COL: merged}, {COL: {"source": "api", "status": "ok"}}


def fake_find_image_in_day(day, cam, dt_obj, ts_ns, hour_cache, images_root=None,
                           day_dir=None, scan_cache=None):
    SHARE_CALLS.append((day, cam))
    return Path(f"/fake/{day}/{cam}/frame.png"), Path(f"/fake/{day}/{cam}")


sf_t._load_api_for_day = fake_load_api_for_day
# Kept before the stub goes in: the hour-probe check is about the real resolver.
REAL_FIND = sf_t._find_image_in_day
sf_t._find_image_in_day = fake_find_image_in_day
sf_t._day_image_folder = lambda day, images_root=None: Path(f"/fake/{day}")
sf_t._find_hour_folder = (lambda day, hour_utc, images_root=None:
                          Path(f"/fake/{day}/{hour_utc:02d}"))
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
    w._pv_cfg = [{"col": COL, "target": TARGET, "tol": TOL, "filter": True}]
    w._pv_rows = []                      # no widgets: _pv_cfg is read as it stands
    w._sync_pv_cfg_from_rows = lambda: None
    w._set_selected_cameras(CAMS)


def by_day(w, cam):
    return {r.day: r for r in w._cam_results[cam]}


# ── the checks ────────────────────────────────────────────────────────────────
def test_data_run_tells_them_apart(app, w):
    print("\nLoad data: which days hold a shot inside the band")
    check("the run finished", run(app, w, False))
    rows = by_day(w, CAMS[0])
    check("every day answered", sorted(rows) == DAYS, str(sorted(rows)))
    check("the match days found their shots",
          all(rows[d].rows_in_tol for d in MATCH_DAYS),
          str([len(rows[d].rows_in_tol) for d in MATCH_DAYS]))
    check("the miss days found none",
          all(not rows[d].rows_in_tol for d in MISS_DAYS),
          str([len(rows[d].rows_in_tol) for d in MISS_DAYS]))


def test_images_skip_the_miss_days(app, w):
    print("\nLoad images: the share is walked only where there is a shot")
    check("the run finished", run(app, w, True))
    walked = sorted({d for d, _c in SHARE_CALLS})
    check("only the match days were looked for", walked == sorted(MATCH_DAYS),
          str(walked))
    check("and each of them once per camera",
          len(SHARE_CALLS) == len(MATCH_DAYS) * len(CAMS), str(len(SHARE_CALLS)))


def test_every_day_still_has_a_row(app, w):
    print("\nA skipped day is still a row, in every camera tab")
    for cam in CAMS:
        rows = by_day(w, cam)
        check(f"{cam}: one row per day", sorted(rows) == DAYS, str(sorted(rows)))
        check(f"{cam}: the match days carry a picture",
              all(rows[d].status == "ok" and rows[d].img_path for d in MATCH_DAYS),
              str([rows[d].status for d in MATCH_DAYS]))
        check(f"{cam}: the miss days are marked skipped",
              all(rows[d].status == "skipped" for d in MISS_DAYS),
              str([rows[d].status for d in MISS_DAYS]))


def test_the_row_says_what_happened(app, w):
    print("\nThe row says it was not searched — it does not claim a failed look-up")
    tbl = w._cam_tables[CAMS[0]]
    rows = w._cam_results[CAMS[0]]
    idx = next(i for i, r in enumerate(rows) if r.day == MISS_DAYS[0])
    status = tbl.item(idx, sf_t.RES_COL_STATUS).text()
    check("it says nothing was in range", "nothing in range" in status, status)
    check("and that no picture was looked for", "image not searched" in status, status)
    bg = tbl.item(idx, 0).background().color().name()
    check("the row is yellow, not red", bg == "#fff3cd", bg)
    img = tbl.item(idx, sf_t.RES_COL_IMG)
    check("the Image cell is a dash", img.text() == sf_t.IMG_MARK_NOT_TRIED,
          img.text())
    check("whose tip explains the skip", "no frame was looked for" in img.toolTip(),
          img.toolTip())
    check("the count names them apart",
          "skipped (nothing in range)" in w._result_lbl.text(), w._result_lbl.text())


def test_the_bar_reaches_the_end(app, w):
    print("\nThe progress bar still gets to the end")
    check("the bar is full", w._prog.value() == w._prog.maximum(),
          f"{w._prog.value()} / {w._prog.maximum()}")


def test_hour_probe_stops_at_one(app, w):
    print("\nA frame is looked for in the shot's hour and its two neighbours only")
    probed: "list[int]" = []

    class _Cache:
        def cam_folder(self, folder, cam, closed):
            probed.append(int(Path(folder).name))
            return None

    tz = sf_t.PRAGUE if sf_t.PRAGUE else timezone.utc
    dt = datetime(2026, 9, 1, 12, 30, tzinfo=tz).replace(tzinfo=None)
    img, folder = REAL_FIND(date(2026, 9, 1), "CAM", dt, None, {},
                            day_dir=Path("/fake/day"), scan_cache=_Cache())
    check("nothing was found", img is None and folder is None)
    check("three hours were probed, not five", len(probed) == 3, str(probed))
    check("and they are the shot's own and its neighbours",
          sorted((h - probed[0]) % 24 for h in probed) == [0, 1, 23],
          str(probed))


def main():
    import tempfile
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
    # The panel saves its band on every rebuild and on tab hide, so the state file is
    # moved into a temp folder first: a test run may not retype the operator's own
    # target and tolerance.
    sf_t.ShotFinderWidget._UI_STATE_PATH = (
        Path(tempfile.mkdtemp(prefix="sf_skip_")) / "shotfinder_ui_state.json")
    w = sf_t.ShotFinderWidget()
    w.resize(1400, 800)
    setup(w)

    test_data_run_tells_them_apart(app, w)
    test_images_skip_the_miss_days(app, w)
    test_every_day_still_has_a_row(app, w)
    test_the_row_says_what_happened(app, w)
    test_the_bar_reaches_the_end(app, w)
    test_hour_probe_stops_at_one(app, w)

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
