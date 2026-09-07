"""Assert the Image Finder answers ONE MOMENT quickly and honestly.

Clicking a point in the PV graph asks a different question from every other search
in this tab: not "find me a good frame somewhere in here" but "this exact time —
what did each camera hold". Two things then matter, and they are what this pins.

QUICKLY. The cost is not decoding, it is folder listings over SMB (~150 ms each).
Resolving one moment for twenty cameras the naive way costs hundreds of them, so
the resolver goes through `shot_finder.DayScanCache`: one reading of an hour folder
answers EVERY camera, and one reading of a camera folder answers every later moment
inside that hour. On top of that the tab remembers what it found, keyed by
(camera, moment), so coming back to a moment touches nothing at all. The test
counts FOLDER READINGS, never seconds — seconds measure this machine, readings
measure the code.

HONESTLY. A camera that had nothing within the ±30 s match window must be SHOWN
saying so, not silently dropped: twelve tiles where twenty cameras were picked
reads as "the other eight are fine", which is the opposite of true. And a miss is
only remembered once the moment is old enough for "nothing there" to be final —
the archiver runs behind, so a frame for the current minute may simply not be
written yet.

Runs offscreen against a synthetic local archive tree — no share, no archiver:

    python testing/test_finder_moment.py
"""
import importlib.util
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_finder():
    """Load if_t.py as `image_finder`, the way main.py does — slider FIRST, so the
    Finder borrows that instance instead of exec'ing is_t.py a second time."""
    B.load_slider()
    if "image_finder" in sys.modules:
        return sys.modules["image_finder"]
    argv, sys.argv = sys.argv, ["if_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_finder", str(B.HERE / "if_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_finder"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


# A day well in the past, so every hour folder counts as CLOSED and the scan cache
# is allowed to keep it forever — which is the behaviour being measured.
DAY = date(2026, 8, 17)
CAMS = ["C03-040-PTM11WNF-_-IMG", "C03-041-PAM1FF-_-IMG", "C03-042-PAM2FF-_-IMG"]
# The frame cadence the archive really has: roughly one every few seconds, NOT the
# camera's own rate.
FRAME_STEP_S = 5
FRAMES = 24                      # two minutes of them


def prague_bounds(day: date, hour: int) -> int:
    """Wall-clock `hour` on `day` in Prague, as unix ns."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, 0, 0,
                        tzinfo=tz).timestamp() * 1e9)


def build_archive(root: Path, sf, hour_prague: int) -> "list[int]":
    """Write the synthetic tree in the shape the resolver walks:

        <root>/<year>/<month>/<day>/<UTC hour>/<camera>/<...unix ns...>.png

    The hour folder is named after the UTC hour, while the moment asked for is
    Prague wall time — the conversion is the resolver's own
    `_folder_hour_from_prague`, used here rather than a hard-coded offset so the
    test does not quietly assume a DST rule.
    """
    import numpy as np
    from PIL import Image as PilImage

    hour_utc = sf._folder_hour_from_prague(hour_prague, DAY)
    base_ns = prague_bounds(DAY, hour_prague)
    stamps = [base_ns + i * FRAME_STEP_S * 1_000_000_000 for i in range(FRAMES)]
    arr = np.zeros((40, 60), dtype=np.uint16)
    arr[10:30, 15:45] = 2048              # something on it, so it is not "blank"
    # No `mode=` argument: PIL takes 16-bit unsigned as I;16 on its own, and
    # spelling it out is deprecated.
    img = PilImage.fromarray(arr)
    for cam in CAMS:
        d = root / str(DAY.year) / str(DAY.month) / str(DAY.day) / str(hour_utc) / cam
        d.mkdir(parents=True, exist_ok=True)
        for ts in stamps:
            img.save(str(d / f"{cam}_-_{ts}.png"))
    return stamps


def main():
    m = load_finder()
    sl = sys.modules["image_slider"]
    sf = m._get_shot_finder_module()

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    tmp = Path(tempfile.mkdtemp(prefix="finder_moment_"))
    root = tmp / "cpva-image-2026"
    HOUR = 10
    stamps = build_archive(root, sf, HOUR)

    # The one seam this test needs: the resolver asks the Slider where a year's
    # archive lives. Point it at the synthetic tree instead of the share.
    orig_root = sl.container_root_for_year
    sl.container_root_for_year = lambda year: root

    try:
        run_checks(m, sf, stamps)
    finally:
        sl.container_root_for_year = orig_root

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


def check_dialog(m, stamps):
    """The PV Search window's half of the job: turning a click into a moment.

    The archiver is never touched — `_fetch_window` is replaced, so the series the
    graph and the snapping work off are the synthetic frame timestamps. A dialog
    that reached out to the network here would make the test depend on whether the
    machine was running yesterday."""
    from PySide6.QtCore import QDate
    from pathlib import Path

    day = DAY
    series = [(t, 10.0 + i) for i, t in enumerate(stamps)]
    orig = m.PVRegionSearchDialog._fetch_window
    # (samples, status, alias) — the third value names the OTHER archived channel
    # name when that is what answered (SBW4); empty here.
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (list(series), "ok", ""))
    try:
        cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
        dlg = m.PVRegionSearchDialog(cams, [QDate(day.year, day.month, day.day)])
        B.wait_for(lambda: bool(dlg._series), timeout_s=10.0)
        check("the graph got the samples",
              len(((dlg._series.get(day) or {}).get(
                  dlg._primary_cb.currentData()) or [])) == len(stamps),
              f"{len(((dlg._series.get(day) or {}).get(dlg._primary_cb.currentData()) or []))}"
              f" of {len(stamps)}")

        # Two seconds past a sample: the moment must snap BACK onto it. A time
        # between two samples has no shot behind it, so the frames pulled for it
        # would be an arbitrary pick.
        between = stamps[4] + 2_000_000_000
        snapped = dlg._snap_ns(between, day)
        check("a time between samples snaps to the nearest one",
              snapped == stamps[4],
              f"{(snapped - between) / 1e9:+.1f} s")
        check("a time already on a sample stays put",
              dlg._snap_ns(stamps[7], day) == stamps[7])

        # Clicking through the axis, the way _on_release does.
        x = dlg._ns_to_x(between, day)
        dlg._set_moment_from_x(x)
        check("clicking the graph picks that moment",
              dlg._moment_ns == stamps[4],
              f"{dlg._moment_ns} vs {stamps[4]}")
        check("the button offers to search it",
              "moment" in dlg._btn_search.text().lower(), dlg._btn_search.text())
        check("and the panel says which moment", "10:00:2" in dlg._lbl_moment.text()
              or ":" in dlg._lbl_moment.text(), dlg._lbl_moment.text())
        cfg = dlg.get_config()
        check("the config carries the moment and no regions",
              cfg.get("moment_ns") == stamps[4] and not cfg.get("regions"),
              f"{cfg.get('moment_ns')}, regions {cfg.get('regions')}")

        # Every click ADDS a moment — the picks build up, and across days too.
        dlg._set_moment_from_x(dlg._ns_to_x(stamps[9], day))
        check("a second click adds a second moment",
              dlg._moments == [stamps[4], stamps[9]], str(dlg._moments))
        check("clicking the same sample again adds nothing",
              (dlg._set_moment_from_x(dlg._ns_to_x(stamps[9], day)) or True)
              and dlg._moments == [stamps[4], stamps[9]], str(dlg._moments))
        check("the button counts them",
              "2 moments" in dlg._btn_search.text(), dlg._btn_search.text())
        cfg = dlg.get_config()
        check("the config carries every pick, in the order clicked",
              cfg.get("moments_ns") == [stamps[4], stamps[9]],
              str(cfg.get("moments_ns")))
        check("and moment_ns still names the first, for an old reader",
              cfg.get("moment_ns") == stamps[4])

        # Ctrl+Z takes the last pick back, one gesture at a time.
        dlg._undo_pick()
        check("Undo takes the last moment off", dlg._moments == [stamps[4]],
              str(dlg._moments))

        # A moment and a region can both be picked. The moments are what gets
        # searched — said on the label, never by deleting the other one: a click
        # deleting N drags (or N drags deleting a click) is what made the old rule
        # wrong in both directions.
        # Two hours wide. The whole run of synthetic frames spans two MINUTES, which
        # on an axis showing a whole day is a third of a pixel — and `_is_drag`
        # rightly calls that a click, not a drag.
        two_h = 2 * 3600 * 1_000_000_000
        dlg._on_span(dlg._ns_to_x(stamps[0], day),
                     dlg._ns_to_x(stamps[0] + two_h, day))
        check("dragging a region keeps the picked moment",
              dlg._moments == [stamps[4]] and len(dlg._regions) == 1,
              f"moments {dlg._moments}, {len(dlg._regions)} region(s)")
        check("the label says the regions are ignored while a moment is picked",
              "ignored" in dlg._lbl_moment.text(), dlg._lbl_moment.text())
        dlg._set_moment_from_x(dlg._ns_to_x(stamps[9], day))
        check("clicking a moment keeps the regions", len(dlg._regions) == 1,
              str(dlg._regions))
        check("and get_config searches the moments, not the regions",
              dlg.get_config().get("moments_ns") == [stamps[4], stamps[9]],
              str(dlg.get_config().get("moments_ns")))
        check("undoing the region drag brings the region back and nothing else",
              (dlg._undo_pick() or True) and dlg._moments == [stamps[4]]
              and len(dlg._regions) == 1,
              f"moments {dlg._moments}, {len(dlg._regions)} region(s)")

        dlg._clear_moment()
        check("Clear forgets every moment", dlg._moments == [])
        check("and leaves the regions alone", len(dlg._regions) == 1)
        check("the button goes back to the regions wording",
              "region" in dlg._btn_search.text().lower(), dlg._btn_search.text())
        dlg._clear_regions()
        check("with nothing picked the config carries no moment",
              dlg.get_config().get("moment_ns") is None)

        # Left, right: the two buttons must never do the same thing.
        check("the left drag is a separate selector from the right one",
              dlg._span is not None and dlg._zoom_span is not None
              and dlg._span is not dlg._zoom_span)
        dlg._ax.set_xlim(0.0, 10.0)
        dlg._xlim_stack = []
        dlg._on_zoom_span(2.0, 6.0)
        check("a right drag zooms in",
              tuple(round(v, 3) for v in dlg._ax.get_xlim()) == (2.0, 6.0),
              str(dlg._ax.get_xlim()))
        dlg._zoom_out()
        check("a right click steps back out",
              tuple(round(v, 3) for v in dlg._ax.get_xlim()) == (0.0, 10.0),
              str(dlg._ax.get_xlim()))
        dlg.close()
    finally:
        m.PVRegionSearchDialog._fetch_window = orig


def run_checks(m, sf, stamps):
    cache = sf.DayScanCache()

    print("\n=== the resolver finds the right frame ===")
    exact = stamps[6]
    got = m._resolve_moment_one(exact, CAMS[0], cache)
    check("a moment ON a frame returns that frame",
          got["ts_ns"] == exact, f"asked {exact}, got {got['ts_ns']} {got['note']}")
    check("the asked-for moment is carried back for the caption",
          got["asked_ns"] == exact)

    # Two seconds past a frame: the archive stores one every few seconds, so the
    # nearest is a couple of seconds away and that IS the answer.
    off = exact + 2_000_000_000
    got = m._resolve_moment_one(off, CAMS[0], cache)
    check("a moment BETWEEN frames returns the nearest one",
          got["ts_ns"] == exact,
          f"{(got['ts_ns'] - off) / 1e9:+.1f} s away" if got["ts_ns"] else got["note"])

    print("\n=== a camera with nothing there says so ===")
    # Well outside sf_t.IMG_MATCH_TOL_NS (30 s) but inside the same hour folder,
    # so the folder is found and only the FRAME is missing.
    far = stamps[-1] + 20 * 60 * 1_000_000_000
    got = m._resolve_moment_one(far, CAMS[0], cache)
    check("beyond the ±30 s window there is no frame", got["path"] is None)
    check("and it says why, for the tile to print",
          bool(got["note"]), repr(got["note"]))

    got = m._resolve_moment_one(exact, "C03-099-NOSUCHCAM-_-IMG", cache)
    check("an unknown camera is a miss, not a crash",
          got["path"] is None and bool(got["note"]), repr(got["note"]))

    print("\n=== folder readings, which is what makes it quick ===")
    cold = sf.DayScanCache()
    for cam in CAMS:
        m._resolve_moment_one(exact, cam, cold)
    listings_cold, _ = cold.stats()
    # One hour listing shared by every camera, plus one frame listing each. Without
    # the shared cache this is a listing per camera per probed hour, plus a walk of
    # the camera folder for every one of them.
    check("three cameras cost one hour listing plus one each",
          listings_cold <= 1 + len(CAMS),
          f"{listings_cold} readings for {len(CAMS)} cameras")

    for cam in CAMS:
        m._resolve_moment_one(exact, cam, cold)
    listings_warm, _ = cold.stats()
    check("asking again reads no folder at all",
          listings_warm == listings_cold,
          f"{listings_warm - listings_cold} extra readings")

    # A DIFFERENT moment in the same hour is the case the cache exists for: the
    # folders it needs were already read for the first one.
    for cam in CAMS:
        m._resolve_moment_one(stamps[2], cam, cold)
    listings_next, _ = cold.stats()
    check("another moment in the same hour reads no folder either",
          listings_next == listings_cold,
          f"{listings_next - listings_cold} extra readings")

    print("\n=== the fan-out delivers one answer per camera ===")
    from PySide6.QtCore import QCoreApplication
    import threading

    sig = m._MomentSignals()
    items, done = [], []
    sig.item.connect(lambda res, gen: items.append(res))
    sig.done.connect(lambda gen, ms, reads: done.append((gen, ms, reads)))
    task = m._MomentResolveTask(7, exact, CAMS, sig, threading.Event(),
                                sf.DayScanCache())
    task.run()                              # run it here; the pool adds nothing to test
    B.wait_for(lambda: bool(done), timeout_s=10.0)
    check("one item per camera", len(items) == len(CAMS),
          f"{len(items)} of {len(CAMS)}")
    check("every camera found its frame",
          all(it["path"] is not None for it in items))
    check("the generation is carried through", done and done[0][0] == 7)
    check("the folder readings are reported, so slow is attributable",
          done and done[0][2] >= 1, f"{done[0][2] if done else '-'} readings")

    print("\n=== a fresh miss is not remembered as empty ===")
    holder = types.SimpleNamespace(_res_cache={})
    put = m.ImageFinderWidget._res_cache_put.__get__(holder)
    get = m.ImageFinderWidget._res_cache_get.__get__(holder)
    now_ns = int(datetime.now(tz=timezone.utc).timestamp() * 1e9)

    fresh = now_ns - 60 * 1_000_000_000                  # one minute ago
    put({"cam": "X", "asked_ns": fresh, "path": None, "note": "no frame"})
    check("a miss a minute old is NOT cached", get("X", fresh) is None)

    old = now_ns - 3 * 3600 * 1_000_000_000              # three hours ago
    put({"cam": "X", "asked_ns": old, "path": None, "note": "no frame"})
    check("a miss three hours old IS cached", get("X", old) is not None)

    put({"cam": "Y", "asked_ns": fresh, "path": Path("a.png"), "ts_ns": fresh})
    check("a HIT is cached however fresh the moment",
          get("Y", fresh) is not None)

    print("\n=== the wall keeps the cameras that had nothing ===")
    captured = {}
    stub = types.SimpleNamespace(
        _build_wall_tabs=lambda cells, moment_ns=None, moments_ns=None:
            captured.update(cells=cells, moment_ns=moment_ns,
                            moments_ns=moments_ns),
        _log=lambda msg: None)
    fill = m.ImageFinderWidget.fill_wall.__get__(stub)

    day = date(2026, 8, 17)
    results = {
        CAMS[0]: [(day, 10, Path("one.png"), {"asked_ns": exact}, "found")],
        CAMS[1]: [(day, 10, None, {"asked_ns": exact,
                                   "note": "no frame near this moment"}, "no_frame")],
    }
    fill(results, None, moment_ns=exact)
    cells = captured.get("cells") or []
    check("both cameras are on the moment wall", len(cells) == 2,
          f"{len(cells)} cell(s)")
    check("the missing one is marked, not dropped",
          any(c["status"] == "no_frame" and c["path"] is None for c in cells))
    check("the moment is passed on to the wall builder",
          captured.get("moment_ns") == exact)

    # The same results WITHOUT a moment: a many-day search must go on dropping the
    # empties, or every day a camera did not run becomes a blank tile.
    captured.clear()
    fill(results, None)
    cells = captured.get("cells") or []
    check("a day search still drops a camera with no frame", len(cells) == 1,
          f"{len(cells)} cell(s)")

    print("\n=== several picked moments land on one wall, numbered ===")
    captured.clear()
    later = exact + 90_000_000_000          # a minute and a half on
    picks = [exact, later]
    results2 = {
        CAMS[0]: [(day, 10, Path("a1.png"), {"asked_ns": exact, "pick": 1},
                   "found"),
                  (day, 10, Path("a2.png"), {"asked_ns": later, "pick": 2},
                   "found")],
        CAMS[1]: [(day, 10, Path("b1.png"), {"asked_ns": exact, "pick": 1},
                   "found"),
                  (day, 10, None, {"asked_ns": later, "pick": 2,
                                   "note": "no frame near this moment"},
                   "no_frame")],
    }
    fill(results2, None, moment_ns=exact, moments_ns=picks)
    cells = captured.get("cells") or []
    check("two cameras × two moments = four tiles", len(cells) == 4,
          f"{len(cells)} cell(s)")
    check("every pick reaches the wall as its own number",
          sorted({c.get("pick") for c in cells}) == [1, 2],
          repr(sorted({c.get('pick') for c in cells})))
    # Camera first: with several moments picked the wall is a tab per camera, and
    # inside one camera's tab the tiles read in the order the moments were clicked.
    check("the tiles are ordered camera first, pick second",
          [(c["cam"], c["pick"]) for c in cells]
          == sorted((c["cam"], c["pick"]) for c in cells))
    check("the whole list of picks is passed on",
          captured.get("moments_ns") == picks)
    check("a camera with nothing at ONE of the moments is still kept",
          any(c["status"] == "no_frame" for c in cells))

    wall2 = m._DayWall()
    wall2.resize(800, 400)
    wall2.set_cells([dict(c) for c in cells])
    cap = wall2._caption(wall2.cells()[0])
    check("the tile caption carries the pick number", cap.startswith("1)"),
          repr(cap))
    tip2 = wall2._tile_tip(1)
    check("the tooltip names which pick the tile answers",
          "moment 1 picked" in tip2 or "moment 2 picked" in tip2,
          repr(tip2.splitlines()))

    print("\n=== a click lands on a real sample, never between two ===")
    check_dialog(m, stamps)

    print("\n=== the tile says which camera, and when ===")
    wall = m._DayWall()
    wall.resize(800, 400)
    wall.set_cells([
        {"day": day, "cam": "PTM11WNF", "cam_folder": CAMS[0], "path": None,
         "ts_ns": exact, "status": "found", "meta": {"asked_ns": exact}},
        {"day": day, "cam": "PAM1FF", "cam_folder": CAMS[1], "path": None,
         "ts_ns": None, "status": "no_frame",
         "meta": {"asked_ns": exact, "note": "no frame near this moment"}},
    ])
    check("many cameras at one moment → the wall knows it is multi-camera",
          wall._multi_cam is True)
    cap0 = wall._caption(wall.cells()[0])
    check("the caption names the CAMERA, not the day",
          "PTM11WNF" in cap0 and "17.08" not in cap0, repr(cap0))
    cap1 = wall._caption(wall.cells()[1])
    check("a camera with no frame says so on the tile",
          "no frame" in cap1, repr(cap1))

    tip = wall._tile_tip(0)
    check("the tooltip gives the offset from the moment picked",
          "from the moment picked" in tip, repr(tip.splitlines()[-1:]))

    # Many DAYS of one camera is the other way round, and must not have changed.
    wall.set_cells([
        {"day": day, "cam": "PTM11WNF", "cam_folder": CAMS[0], "path": None,
         "ts_ns": exact, "status": "found", "meta": {}},
        {"day": day + timedelta(days=1), "cam": "PTM11WNF", "cam_folder": CAMS[0],
         "path": None, "ts_ns": exact, "status": "found", "meta": {}},
    ])
    check("one camera over many days → the wall is not multi-camera",
          wall._multi_cam is False)
    check("and the caption goes back to naming the day",
          "17.08" in wall._caption(wall.cells()[0]),
          repr(wall._caption(wall.cells()[0])))


if __name__ == "__main__":
    sys.exit(main())
