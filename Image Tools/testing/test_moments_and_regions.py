"""Picked moments AND marked regions are BOTH searched, on every camera.

The fault, and it was silent: `_start_pv_search` looked at the moments first and
returned — `cfg["regions"]` was never read again. So four clicks plus four drags
searched four moments and dropped the four spans on the floor, on a button that
had already announced it would search "these 4 moments" and a status line that
called the regions ignored. Half of what the operator marked, gone.

A region is only a moment worked out from the peak of the primary PV inside it,
and both ends at the SAME frame resolver (`_resolve_moment_one`). So there is one
pipeline now: the regions are turned into instants (`_region_targets`, one
archiver read per day and NOTHING per camera) and everything goes through
`_load_moments`.

Pinned here:
  * `_start_pick_search` carries the moments and the regions into one pick list
  * a region with no primary PV is refused out loud, never dropped in silence
  * `_load_moments` asks for cameras × EVERY pick — the count the operator checks
  * a region-driven tile carries its region and its own number (r2, not 2)
  * `fill_wall` keeps a camera that found nothing, so the count still adds up
  * on the Day-by-day wall the tiles of one camera read LEFT TO RIGHT IN TIME,
    whichever kind of pick found them — moment 1 and region 1 no longer collide
  * the row banner names both halves

Runs offscreen, no images, no share, no archiver:

    python testing/test_moments_and_regions.py
"""
import importlib.util
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []
# Walls stay referenced for the whole run: their frame reader is a daemon thread
# that signals back, and a wall collected while it is still running takes the C++
# object out from under that signal.
KEEP: list = []
DAY = date(2026, 9, 1)
DAY2 = date(2026, 9, 2)
CAMS = ("C03-040-PTM11WNF-_-IMG", "C03-041-PAM1FF-_-IMG", "C03-042-PAM2FF-_-IMG")


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def drain(seconds: float = 0.3):
    """Let the wall's background frame reader finish while the wall is still alive."""
    import time
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)


def load_finder():
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


def ns_at(day, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=tz).timestamp() * 1e9)


def region(i: int, count: int, day, h0: int, h1: int) -> dict:
    return {"t_start_ns": ns_at(day, h0), "t_end_ns": ns_at(day, h1), "index": i,
            "count": count, "color": "#C62828",
            "label": f"{h0:02d}:00:00-{h1:02d}:00:00"}


# Four moments and four regions on one day. The moments are picked at 09, 11, 13
# and 15; the regions cover 08-10, 10-12, 12-14 and 14-16 and their peaks are put
# at the half hour, so the two kinds INTERLEAVE in time — which is the case that
# used to make two tiles share one column.
MOMENTS = [ns_at(DAY, 9), ns_at(DAY, 11), ns_at(DAY, 13), ns_at(DAY, 15)]
REGIONS = [region(1, 4, DAY, 8, 10), region(2, 4, DAY, 10, 12),
           region(3, 4, DAY, 12, 14), region(4, 4, DAY, 14, 16)]
REGION_PEAKS = [ns_at(DAY, 8, 30), ns_at(DAY, 10, 30),
                ns_at(DAY, 12, 30), ns_at(DAY, 14, 30)]


def region_picks() -> list:
    return [{"ts": REGION_PEAKS[i], "kind": "region", "index": r["index"],
             "region": dict(r)} for i, r in enumerate(REGIONS)]


# ── the two halves reach one pick list ────────────────────────────────────────
def check_pick_assembly(m):
    print("\n=== both halves reach one pick list ===")
    got = {}
    warned = []
    stub = types.SimpleNamespace(
        _log=lambda msg: None,
        _load_moments=lambda picks: got.update(picks=list(picks)),
        _resolve_region_picks_async=lambda regs, primary, on_done:
            on_done(region_picks()),
    )
    start = m.ImageFinderWidget._start_pick_search.__get__(stub)
    cfg = {"cameras": list(CAMS), "days": [DAY],
           "primary_channel": "L3-SBW4-PM311:Energy"}
    start(cfg, list(MOMENTS), {DAY: list(REGIONS)})
    picks = got.get("picks") or []
    check("four moments plus four regions = eight picks", len(picks) == 8,
          f"{len(picks)} pick(s)")
    kinds = [p["kind"] for p in picks]
    check("both kinds are in it", kinds.count("moment") == 4
          and kinds.count("region") == 4, repr(kinds))
    check("every moment kept its own number",
          [p["index"] for p in picks if p["kind"] == "moment"] == [1, 2, 3, 4],
          repr([p["index"] for p in picks if p["kind"] == "moment"]))
    check("and every region kept its own",
          [p["index"] for p in picks if p["kind"] == "region"] == [1, 2, 3, 4],
          repr([p["index"] for p in picks if p["kind"] == "region"]))
    check("a region pick carries the region itself",
          all(p.get("region", {}).get("t_start_ns")
              for p in picks if p["kind"] == "region"))

    # Moments alone: no archiver read at all, no progress box.
    got.clear()
    touched = []
    stub._resolve_region_picks_async = (
        lambda regs, primary, on_done: touched.append(True))
    start(cfg, list(MOMENTS), {})
    check("moments alone still go straight to the frames",
          len(got.get("picks") or []) == 4 and not touched,
          f"{len(got.get('picks') or [])} pick(s), archiver touched {bool(touched)}")

    # A region with no primary PV is REFUSED, out loud. Dropping it quietly is the
    # bug this whole change is about.
    got.clear()
    from PySide6 import QtWidgets
    orig = QtWidgets.QMessageBox.information
    QtWidgets.QMessageBox.information = (
        lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))
    try:
        start({"cameras": list(CAMS), "days": [DAY], "primary_channel": None},
              list(MOMENTS), {DAY: list(REGIONS)})
    finally:
        QtWidgets.QMessageBox.information = orig
    check("a region with no primary PV says so", bool(warned),
          repr(warned[:1]))
    check("and it names the count that would have been lost",
          any("4 region" in str(w) for w in warned), repr(warned[:1]))
    check("the moments are still searched", len(got.get("picks") or []) == 4,
          f"{len(got.get('picks') or [])} pick(s)")


# ── cameras × every pick ──────────────────────────────────────────────────────
def check_jobs(m):
    print("\n=== cameras x every pick is what is asked for ===")
    jobs_seen = {}
    logs = []

    class _Pool:
        def start(self, task):
            jobs_seen["jobs"] = list(task._jobs)

    stub = types.SimpleNamespace(
        _checked_cameras=lambda: [(c, c, Path(c)) for c in CAMS],
        _res_cache_get=lambda cam, ts: None,
        _pending_pv_cfg=None,
        _open_camera_picker=lambda: None,
        _moments_ns=[], _moment_ns=None, _pick_info={},
        _moment_gen=0, _moment_stop=None, _moment_items=[],
        _log=logs.append,
        _ensure_scan_cache=lambda: None,
        _scan_cache=None, _moment_sig=None,
        _moment_pool=_Pool(),
        _on_moment_done=lambda gen, ms=0.0, reads=0: None,
    )
    import threading
    stub._moment_stop = threading.Event()
    load = m.ImageFinderWidget._load_moments.__get__(stub)
    load(region_picks() + [{"ts": t, "kind": "moment", "index": i + 1}
                           for i, t in enumerate(MOMENTS)])
    jobs = jobs_seen.get("jobs") or []
    check("three cameras x eight picks = 24 lookups", len(jobs) == 24,
          f"{len(jobs)} lookup(s)")
    check("every camera is asked for every pick",
          len({j[1] for j in jobs}) == 3 and len({j[0] for j in jobs}) == 8,
          f"{len({j[1] for j in jobs})} camera(s), {len({j[0] for j in jobs})} instant(s)")
    check("the log says how many frames are wanted",
          any("3 camera(s)" in s and "24 frame(s) wanted" in s for s in logs),
          repr([s for s in logs if "camera(s)" in s]))
    check("and it counts the regions separately",
          any("4 region(s)" in s for s in logs),
          repr([s for s in logs if "region(s)" in s]))
    check("what each instant IS was recorded",
          len(stub._pick_info) == 8
          and sum(1 for v in stub._pick_info.values()
                  if v["kind"] == "region") == 4,
          repr(sorted((v["kind"], v["index"]) for v in stub._pick_info.values())))
    return stub._pick_info


# ── the results carry which pick found them ───────────────────────────────────
def results_from(m, pick_info: dict, missing=(), day=DAY) -> list:
    """Run the real `_on_moment_done` over fabricated resolver answers, then the
    real `fill_wall` cell builder over what it produced."""
    items = []
    for ts in pick_info:
        for cam in CAMS:
            if (cam, ts) in missing:
                items.append({"cam": cam, "path": None, "ts_ns": None,
                              "asked_ns": ts, "note": "nothing near it"})
            else:
                items.append({"cam": cam,
                              "path": Path(f"{cam}_{ts}.png"),
                              "ts_ns": ts, "asked_ns": ts, "note": ""})
    captured = {}
    stub = types.SimpleNamespace(
        _moments_ns=list(pick_info.keys()), _moment_ns=list(pick_info.keys())[0],
        _moment_gen=0, _moment_items=items, _pick_info=pick_info,
        _log=lambda msg: None,
        _checked_cameras=lambda: [(c, c, Path(c)) for c in CAMS],
        fill_wall=lambda results, cams, moment_ns=None, moments_ns=None:
            captured.update(results=results, moment_ns=moment_ns,
                            moments_ns=moments_ns),
        _sync_shot_steps=lambda: None,
        _refresh_moment_list=lambda: None,
        _sync_send_moment_buttons=lambda: None,
        _start_moment_prefetch=lambda: None,
        _preview_set_files=lambda *a, **k: None,
        _energy_info=types.SimpleNamespace(setPlainText=lambda t: None),
        _run_energy_lookup_async=lambda paths, on_done=None: None,
    )
    # An all-zero frame counts as blank; the fabricated paths do not exist, so the
    # emptiness test is stubbed out rather than reading the disk.
    orig = m._image_is_nonempty
    m._image_is_nonempty = lambda p: True
    try:
        m.ImageFinderWidget._on_moment_done.__get__(stub)(0, 0.0, 0)
    finally:
        m._image_is_nonempty = orig
    res = captured.get("results") or {}

    cells_holder = {}
    fstub = types.SimpleNamespace(
        _build_wall_tabs=lambda cells, moment_ns=None, moments_ns=None:
            cells_holder.update(cells=cells),
        _log=lambda msg: None)
    m.ImageFinderWidget.fill_wall.__get__(fstub)(
        res, None, moment_ns=captured.get("moment_ns"),
        moments_ns=captured.get("moments_ns"))
    return cells_holder.get("cells") or []


def check_cells(m, pick_info: dict):
    print("\n=== every tile knows which pick found it ===")
    cells = results_from(m, pick_info)
    check("three cameras x eight picks = 24 tiles", len(cells) == 24,
          f"{len(cells)} tile(s)")
    regs = [c for c in cells if c.get("region")]
    check("twelve of them came from a region", len(regs) == 12,
          f"{len(regs)} tile(s)")
    check("and each names its own region number",
          sorted({(c["region"] or {}).get("index") for c in regs}) == [1, 2, 3, 4],
          repr(sorted({(c['region'] or {}).get('index') for c in regs})))
    moms = [c for c in cells if not c.get("region")]
    check("the other twelve came from a moment", len(moms) == 12,
          f"{len(moms)} tile(s)")
    check("and each names its own moment number",
          sorted({c.get("pick") for c in moms}) == [1, 2, 3, 4],
          repr(sorted({c.get('pick') for c in moms})))

    import numpy as np
    wall = m._DayWall()
    wall.set_layout_mode("rows")
    wall.resize(1200, 700)
    wall.set_canvas(1200, 700)
    # Pre-load the frames, so the wall's background reader has nothing to fetch —
    # the fabricated paths are not files.
    for c in cells:
        if c.get("path") is not None:
            wall._shared.raw[c["path"]] = (
                np.zeros((1024, 1280), dtype=np.float32), 4095.0)
    KEEP.append(wall)
    wall.set_cells([dict(c) for c in cells])
    drain()
    caps = {}
    for c in wall.cells():
        caps[str(c.get("path"))] = wall._caption(c)
    # PLAIN numbers, no `r`. Moments and regions are numbered from one counter
    # now, so no two picks share a number and nothing needs a letter to tell them
    # apart. A caption that started with an `r` would be the old behaviour.
    check("no tile caption wears an r any more",
          not [v for v in caps.values() if v.startswith("r")],
          repr([v for v in caps.values() if v.startswith("r")][:2]))
    numbered = [v for v in caps.values() if v[:1].isdigit() and ")" in v[:3]]
    check("every tile is captioned with its plain pick number",
          len(numbered) == 24, f"{len(numbered)} caption(s), e.g. {numbered[:1]}")

    print("\n=== on the wall they read left to right IN TIME ===")
    rows, cols = wall._row_order()
    check("three cameras = three rows", len(rows) == 3, repr(rows))
    check("one day x eight picks = eight columns", len(cols) == 8,
          f"{len(cols)} column(s)")
    # Every camera's eight tiles in x order must be in time order — that is the
    # whole point, and keying the column on the pick NUMBER put moment 1 and
    # region 1 in one rectangle.
    per_cam = {}
    for i, c in enumerate(wall.cells()):
        per_cam.setdefault(c["cam"], []).append((wall._rects[i].x(), c))
    ok = True
    for cam, lst in per_cam.items():
        lst.sort(key=lambda t: t[0])
        stamps = [c["ts_ns"] for _x, c in lst]
        if stamps != sorted(stamps):
            ok = False
    check("each camera's tiles run in time order", ok,
          repr({k: [c['ts_ns'] for _x, c in v] for k, v in list(per_cam.items())[:1]}))
    check("and every tile has a rectangle of its own",
          len({(r.x(), r.y()) for r in wall._rects}) == 24,
          f"{len({(r.x(), r.y()) for r in wall._rects})} distinct of 24")

    print("\n=== the banner names both halves ===")
    head = wall._row_heads[0][1]
    check("it says four moments", "4 moments" in head, repr(head))
    check("and four regions", "4 regions" in head, repr(head))


def check_missing_camera_still_counts(m, pick_info: dict):
    print("\n=== a camera that found nothing still holds its place ===")
    ts0 = list(pick_info.keys())[0]
    cells = results_from(m, pick_info, missing={(CAMS[2], ts0)})
    check("the tile count is unchanged", len(cells) == 24, f"{len(cells)} tile(s)")
    blanks = [c for c in cells if c.get("status") == "no_frame"]
    check("one of them says it found no frame", len(blanks) == 1,
          f"{len(blanks)} tile(s)")
    check("and it still knows which pick it answers",
          bool(blanks and (blanks[0].get("pick")
                           or (blanks[0].get("region") or {}).get("index"))),
          repr(blanks[:1] and {k: blanks[0].get(k) for k in ("pick", "region")}))


def check_tabs(m, pick_info: dict):
    print("\n=== ONE moment plus regions is still a tab per camera ===")
    # This is the trap the shortcut left: "one moment, every camera" goes up as a
    # SINGLE wall on purpose (a tab holding one tile is no comparison), and with
    # one click plus four drags that test was still true — so four fifths of the
    # search went up hidden behind the fifth.
    first = next(t for t, v in pick_info.items() if v["kind"] == "moment")
    mixed = {first: pick_info[first]}
    for p in region_picks():
        mixed[p["ts"]] = {"kind": "region", "index": p["index"],
                          "region": p["region"]}
    cells = results_from(m, mixed)
    check("three cameras x five picks = 15 tiles", len(cells) == 15,
          f"{len(cells)} tile(s)")
    check("some of them carry a region",
          any(c.get("region") for c in cells))
    # The gate itself, read off the source the way _build_wall_tabs reads it.
    any_region = any(c.get("region") for c in cells)
    picks = {int(t) for t in mixed}
    check("so the one-wall shortcut is refused",
          not (len(picks) <= 1 and not any_region),
          f"{len(picks)} pick(s), region on the wall: {any_region}")

    # And with a single moment and NO region it is still taken, as before.
    only_one = {first: pick_info[first]}
    cells1 = results_from(m, only_one)
    check("one moment on its own still gets the single wall",
          not any(c.get("region") for c in cells1) and len(only_one) == 1,
          f"{len(cells1)} tile(s)")


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    check_pick_assembly(m)
    pick_info = check_jobs(m)
    check_cells(m, pick_info)
    check_missing_camera_still_counts(m, pick_info)
    check_tabs(m, pick_info)
    drain()

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
