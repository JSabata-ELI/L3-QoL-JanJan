"""The day-and-region search: what it ASKS FOR, not how long it takes here.

The Image Finder's region search used to work through the days and cameras one at a
time, and for each one it asked the archiver again for the same PV samples and then
guessed file names at the share until one existed. Six cameras over five days with
two marked regions cost 60 archiver requests and several thousand share round trips,
and every one of them waited for the one before it.

Three claims are pinned here, each counted rather than timed — a count measures the
code, a second measures this machine and this share:

  * **The PV is read once per day.** The samples inside a marked region are the same
    whichever camera is being looked for, so the archiver is asked days × regions
    times, not days × cameras × regions.
  * **The frames go through the one resolver and its folder cache.** The first camera
    in an hour pays for the folder listing; every camera after it, and every later
    region inside that hour, is free. Counted as FOLDER READINGS through
    `DayScanCache.stats()`.
  * **The work overlaps.** The (day, camera) units run together, so the search takes
    about as long as the slowest few, not the sum of all of them.

And what must not change while it gets faster: one entry per marked region, the days
in the order they were picked, the progress count reaching every unit exactly once,
and Cancel stopping it.

Runs offscreen against a synthetic local archive tree — no share, no archiver:

    python testing/test_search_speed.py
"""
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder, prague_bounds

FAILURES: "list[str]" = []

DAYS = [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
CAMS = ["C03-040-PTM11WNF-_-IMG", "C03-041-PAM1FF-_-IMG", "C03-042-PAM2FF-_-IMG"]
HOUR = 10                      # Prague wall-clock hour the frames live in
FRAME_STEP_S = 5
FRAMES = 24                    # two minutes of them
REGIONS_PER_DAY = 2


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def build_archive(root: Path, sf) -> dict:
    """<root>/<year>/<month>/<day>/<UTC hour>/<camera>/<...unix ns...>.png"""
    import numpy as np
    from PIL import Image as PilImage

    arr = np.zeros((24, 32), dtype=np.uint16)
    arr[6:18, 8:24] = 2048
    img = PilImage.fromarray(arr)
    stamps: dict = {}
    for day in DAYS:
        hour_utc = sf._folder_hour_from_prague(HOUR, day)
        base_ns = prague_bounds(day, HOUR)
        stamps[day] = [base_ns + i * FRAME_STEP_S * 1_000_000_000
                       for i in range(FRAMES)]
        for cam in CAMS:
            d = root / str(day.year) / str(day.month) / str(day.day) / str(hour_utc) / cam
            d.mkdir(parents=True, exist_ok=True)
            for ts in stamps[day]:
                img.save(str(d / f"{cam}_-_{ts}.png"))
    return stamps


class _Host:
    """The search methods, without the tab they normally live in — the point is what
    the search asks the archiver and the share for, and a whole Image Finder window
    would drag a UI, a preview panel and an energy lookup in with it."""

    _METHODS = ("_ensure_scan_cache", "_region_targets", "_find_image_for_regions",
                "_resolve_frame_at", "_run_search_units")

    def __init__(self, m):
        self._scan_cache = None
        for name in self._METHODS:
            setattr(self, name, getattr(m.ImageFinderWidget, name).__get__(self))
        self._blocking_call = m.ImageFinderWidget._blocking_call


def cfg_for(days, cams, stamps, regions_per_day=REGIONS_PER_DAY) -> dict:
    """A region search over `days` x `cams`, with the regions around real frames."""
    regions = {}
    for day in days:
        ts = stamps[day]
        regions[day] = [(ts[2] - 1_000_000_000, ts[5] + 1_000_000_000),
                        (ts[12] - 1_000_000_000, ts[15] + 1_000_000_000)][:regions_per_day]
    return {"cameras": [(c, c.split("-")[2], Path("x")) for c in cams],
            "days": list(days), "start_hour": 8, "max_hour": 19,
            "hours_by_day": {}, "regions": regions,
            "primary_channel": "TEST:PRIMARY"}


def run_search(host, cfg):
    """The search, with the progress and the log captured instead of shown."""
    progress: list = []
    logs: list = []
    cancel = threading.Event()
    t0 = time.perf_counter()
    results = host._run_search_units(
        cfg, cancel, logs.append, lambda n, label: progress.append((n, label)))
    return results, progress, logs, (time.perf_counter() - t0)


def main():
    m = load_finder()
    sl = sys.modules["image_slider"]
    sf = m._get_shot_finder_module()

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)

    tmp = Path(tempfile.mkdtemp(prefix="finder_search_"))
    root = tmp / "cpva-image-2026"
    STAMPS.update(build_archive(root, sf))

    orig_root = sl.container_root_for_year
    sl.container_root_for_year = lambda year: root

    # The archiver, replaced by a counter. It answers with the frames' own times, so
    # the peak of a region is a real frame and the search has something to find.
    orig_fetch = m._cpva_fetch_samples

    def fake_fetch(channel, start_ns, end_ns, timeout=3.0):
        FETCH["n"] += 1
        out = []
        for day in DAYS:
            for i, ts in enumerate(STAMPS[day]):
                if start_ns <= ts <= end_ns:
                    out.append({"time": ts, "value": 1.0 + i})
        return out

    m._cpva_fetch_samples = fake_fetch
    try:
        run_checks(m, sf)
    finally:
        sl.container_root_for_year = orig_root
        m._cpva_fetch_samples = orig_fetch

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


def run_checks(m, sf):
    # ── the PV, once per day ────────────────────────────────────────────────
    print("=== the archiver is asked once per day, not once per camera ===")
    host = _Host(m)
    cfg = cfg_for(DAYS, CAMS, STAMPS)
    fetch_calls = FETCH["n"]
    results, progress, logs, secs = run_search(host, cfg)
    asked = FETCH["n"] - fetch_calls
    want = len(DAYS) * REGIONS_PER_DAY
    check("one archiver request per (day, region)", asked == want,
          f"{asked}, and {len(DAYS) * len(CAMS) * REGIONS_PER_DAY} the old way")

    # ── the folder cache ────────────────────────────────────────────────────
    print("\n=== the share is read once per folder, not once per camera per region ===")
    reads = host._scan_cache.stats()[0]
    # An hour costs one listing of the hour folder plus one of each camera folder in
    # it; the second region of that hour, and every later camera, is answered from
    # what is already in hand.
    ceiling = len(DAYS) * (1 + len(CAMS))
    check("the whole search costs one listing per folder", reads <= ceiling,
          f"{reads} readings, ceiling {ceiling}")
    again_host = host
    before = again_host._scan_cache.stats()[0]
    run_search(again_host, cfg)
    check("running it again reads no folder at all",
          again_host._scan_cache.stats()[0] == before,
          f"{again_host._scan_cache.stats()[0] - before} more")

    # ── what the wall gets ──────────────────────────────────────────────────
    print("\n=== the answer is the same shape as before ===")
    check("every camera answered", sorted(results.keys()) == sorted(CAMS))
    per_cam = results[CAMS[0]]
    check("one entry per marked region on every day",
          len(per_cam) == len(DAYS) * REGIONS_PER_DAY,
          f"{len(per_cam)} of {len(DAYS) * REGIONS_PER_DAY}")
    check("the days come back in the order they were picked",
          [row[0] for row in per_cam] == [d for d in DAYS for _ in range(REGIONS_PER_DAY)],
          str([str(row[0]) for row in per_cam]))
    check("every entry found a frame",
          all(row[2] is not None and row[4] == "found" for row in per_cam),
          str([row[4] for row in per_cam]))
    check("the hour is the frame's own hour", all(row[1] == HOUR for row in per_cam),
          str(sorted({row[1] for row in per_cam})))
    metas = [row[3] for row in per_cam]
    check("each entry says which region it came from",
          all(mt.get("region_ns") for mt in metas))
    check("and the moment inside it that was used",
          all(mt.get("target_ns") for mt in metas))
    check("the PV peak is carried through", all(mt.get("pv_peak") for mt in metas))

    # ── progress ────────────────────────────────────────────────────────────
    print("\n=== the progress bar still counts every unit once ===")
    units = len(DAYS) * len(CAMS)
    check("one step per (day, camera)", len(progress) == units,
          f"{len(progress)} of {units}")
    check("counting up, never back",
          [p[0] for p in progress] == list(range(1, units + 1)))
    check("the log names every unit",
          sum(1 for ln in logs if ln.startswith("[search]")) == units,
          f"{sum(1 for ln in logs if ln.startswith('[search]'))} of {units}")
    check("a unit's lines are kept together",
          all(ln.count("[search]") <= 1 for ln in logs))

    # ── the work overlaps ───────────────────────────────────────────────────
    print("\n=== the units run together, not one after another ===")
    host2 = _Host(m)
    slow = {"n": 0}

    def slow_day_cam(day, cam_name, d_from, d_to, cancelled=None, log_fn=None):
        slow["n"] += 1
        time.sleep(0.20)
        return (Path(f"{cam_name}.png"), 10, {"source": "test"}, "found")

    host2._find_image_for_day_cam = slow_day_cam
    auto_cfg = dict(cfg_for(DAYS, CAMS, STAMPS))
    auto_cfg["regions"] = {}          # the automatic mode, one unit per (day, camera)
    _res2, prog2, _logs2, secs2 = run_search(host2, auto_cfg)
    serial = 0.20 * len(DAYS) * len(CAMS)
    check("nine units of 0.2 s take far less than 1.8 s", secs2 < serial * 0.55,
          f"{secs2:.2f} s against {serial:.2f} s one after another")
    check("every unit still ran exactly once", slow["n"] == len(DAYS) * len(CAMS),
          f"{slow['n']}")
    check("and every one is still reported", len(prog2) == len(DAYS) * len(CAMS))

    # ── cancel ──────────────────────────────────────────────────────────────
    print("\n=== Cancel stops it ===")
    host3 = _Host(m)
    stop = threading.Event()
    stop.set()
    logs3: list = []
    t0 = time.perf_counter()
    res3 = host3._run_search_units(cfg_for(DAYS, CAMS, STAMPS), stop,
                                   logs3.append, lambda n, label: None)
    dt3 = time.perf_counter() - t0
    check("a cancelled search finds nothing",
          all(not v for v in res3.values()), str({k: len(v) for k, v in res3.items()}))
    check("and gives up at once", dt3 < 2.0, f"{dt3:.2f} s")

    # ── the resolver itself ─────────────────────────────────────────────────
    print("\n=== the one resolver answers for the search too ===")
    host4 = _Host(m)
    ts = STAMPS[DAYS[0]][7]
    p, h = host4._resolve_frame_at(CAMS[0], ts + 1_000_000_000)
    check("a moment between frames finds the nearest one",
          p is not None and str(ts) in p.name, p.name if p else "nothing")
    check("with the frame's own hour", h == HOUR, str(h))
    far = STAMPS[DAYS[0]][0] - 3600 * 1_000_000_000
    p2, h2 = host4._resolve_frame_at(CAMS[0], far)
    check("an hour with nothing in it is a miss, not a crash",
          p2 is None and h2 is None)


STAMPS: dict = {}
FETCH: dict = {"n": 0}


if __name__ == "__main__":
    sys.exit(main())
