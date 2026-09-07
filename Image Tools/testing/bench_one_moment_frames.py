"""bench_one_moment_frames.py — what finding a moment's frames actually costs.

(One Moment was merged into the Image Finder on 2026-09-04; this benches the
resolver and the shared folder cache in `if_t.py`, where they live now.)

The tab took about a minute to put a wall of frames up, where it used to take a few
seconds. The time was not in decoding the pictures; it was in walking the share:

  * `sf_t._resolve_cam_folder`, when a camera's folder is not in the hour under its
    exact name, listed the hour folder and then asked `is_dir()` about EVERY entry in
    it — a busy hour holds about ninety camera folders, so about ninety separate
    network round trips, and it did that per camera and per probed hour (five of them);
  * `sf_t._find_image_for_ts` then walked the camera's own folder again, per camera and
    per probed hour;
  * and `om_t` handed the resolver a fresh, empty per-camera cache every time, so none
    of it was ever shared between cameras or kept for the next moment.

`sf_t.DayScanCache` reads each folder once instead. This bench measures the difference
against a fake archive on local disk — the same shape the real one has, and the same
one `test_one_moment.py` part 2 builds: …/cpva-image-YYYY/Y/M/D/H/CAM/<unix_ns>.png,
unpadded folders and UTC hours.

Local disk is far faster than SMB, so the SECONDS here are not the operator's seconds.
The number that carries over is the count of FOLDER READINGS: on the share each one is
about 150 ms, so multiplying the counts below by that gives the real cost.

Run:  python testing/bench_one_moment_frames.py [--cams 20] [--hours 5]
Not shipped (bench_ prefix).
"""
import argparse
import importlib.util
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP = Path(__file__).resolve().parent.parent
# Same reason as the test's: the archive path is long, and under a deep working
# directory the …/CAM/<name>_<19 digits>.png leaf goes past Windows' 260-character
# limit — mkdir then fails as if the folder were missing.
ROOT = Path(os.environ.get("TEMP", r"C:\Temp")) / "om_bench_archive"
STATE_HOME = Path(os.environ.get("TEMP", r"C:\Temp")) / "om_bench_appdata"


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, APP / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The tab writes its settings to %APPDATA%\ELI_ImageTools; point that somewhere
# harmless BEFORE anything is built, or running the bench rewrites the operator's own
# window, camera and PV pick.
shutil.rmtree(STATE_HOME, ignore_errors=True)
STATE_HOME.mkdir(parents=True, exist_ok=True)
os.environ["APPDATA"] = str(STATE_HOME)

import numpy as np                                          # noqa: E402
from PIL import Image                                       # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

_NS_PER_S = 1_000_000_000

app = QApplication.instance() or QApplication([])
load("image_slider", "is_t.py")
sf = load("shot_finder", "sf_t.py")
# One Moment was merged into the Image Finder; the resolver and the shared
# folder cache it is benched against live there now.
om = load("image_finder", "if_t.py")
sl = sys.modules["image_slider"]

DAY = datetime(2026, 8, 21, tzinfo=timezone.utc).date()
MOMENT = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)
MOMENT_NS = int(MOMENT.timestamp() * _NS_PER_S)


def build_tree(n_cams: int, n_hours: int, frames_per_hour: int,
               present: float, bystanders: int) -> "list[str]":
    """A day of the archive, the shape the real one has — including the shape that made
    the old code slow.

    Two things matter here and neither is the file count:

    * **not every camera records in every hour.** 2026-08-17 held 92 cameras, only 27
      of them in every hour. A camera missing from the hour the moment falls in is what
      sends the resolver into the neighbouring hours — and what sent the old
      `_resolve_cam_folder` into listing the hour folder and asking about every entry
      in it, one round trip each.
    * **the hour folder is crowded.** The cost of that fallback is one question per
      folder in the hour, so the bystanders — cameras nobody picked — are what it is
      paid on.
    """
    if ROOT.exists():
        shutil.rmtree(ROOT, ignore_errors=True)
    cams = [f"C{i:02d}-0{i:02d}-BENCHCAM{i:02d}-_-IMG" for i in range(n_cams)]
    others = [f"C{i:02d}-9{i:02d}-OTHERCAM{i:02d}-_-IMG" for i in range(bystanders)]
    day_dir = ROOT / f"cpva-image-{DAY.year}" / str(DAY.year) / str(DAY.month) \
        / str(DAY.day)
    base_hour = MOMENT.hour
    art = Image.fromarray(np.full((32, 32), 2000, dtype=np.uint16))
    written = 0
    holes = 0
    step = 3600 // max(frames_per_hour, 1)
    for h in range(base_hour - n_hours // 2, base_hour + n_hours // 2 + 1):
        hour_dir = day_dir / str(h % 24)
        hour_start = MOMENT.replace(hour=h % 24, minute=0, second=0)
        for idx, cam in enumerate(list(cams) + others):
            # Deterministic holes, so two runs are comparable: every camera is in the
            # hours its own index does not exclude.
            if ((idx + h) % 10) >= max(1, round(present * 10)):
                if cam in cams and h % 24 == base_hour:
                    holes += 1
                continue
            folder = hour_dir / cam
            folder.mkdir(parents=True, exist_ok=True)
            for k in range(frames_per_hour):
                ts_ns = int(hour_start.timestamp() * _NS_PER_S) \
                    + k * step * _NS_PER_S
                art.save(str(folder / f"{cam}_{ts_ns}.png"))
                written += 1
    print(f"  fake archive at {ROOT}")
    print(f"  {n_cams} picked camera(s) + {bystanders} bystander(s), {n_hours} hour(s)"
          f", {frames_per_hour} frame(s) each = {written} file(s)")
    print(f"  {holes} of the picked cameras are NOT in the moment's own hour")
    return cams


class _Counter:
    """Counts folder readings the way the old code did them, so the two are comparable:
    one for each hour folder listed and one for each camera folder walked."""

    def __init__(self):
        self.n = 0

    def wrap(self, module):
        real_resolve = module._resolve_cam_folder
        real_find = module._find_image_for_ts

        def resolve(hour_folder, cam):
            if hour_folder is not None:
                # The exact-name probe is one round trip; the fallback listing plus a
                # question per entry is what made this expensive.
                self.n += 1
                try:
                    if not (hour_folder / cam).is_dir():
                        self.n += sum(1 for _ in hour_folder.iterdir()) \
                            if hour_folder.is_dir() else 0
                except OSError:
                    pass
            return real_resolve(hour_folder, cam)

        def find(cam_folder, ts_dt, ts_ns_override=None):
            self.n += 1
            return real_find(cam_folder, ts_dt, ts_ns_override=ts_ns_override)

        module._resolve_cam_folder = resolve
        module._find_image_for_ts = find
        return real_resolve, real_find


def run(cams, scan_cache, label):
    t0 = time.perf_counter()
    found = 0
    for cam in cams:
        if om._resolve_moment_one(MOMENT_NS, cam, scan_cache)["path"] is not None:
            found += 1
    ms = (time.perf_counter() - t0) * 1000.0
    return ms, found, label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=20,
                    help="cameras on the wall (the real one runs to ninety)")
    ap.add_argument("--hours", type=int, default=5,
                    help="hour folders around the moment")
    ap.add_argument("--frames", type=int, default=40,
                    help="frames per camera per hour")
    ap.add_argument("--present", type=float, default=0.4,
                    help="how often a camera actually recorded in an hour "
                         "(2026-08-17: 27 of 92 cameras in every hour)")
    ap.add_argument("--bystanders", type=int, default=70,
                    help="cameras in the hour folder that nobody picked — the old "
                         "fallback paid one round trip for each of them")
    args = ap.parse_args()

    print("\n=== One Moment: what finding the frames costs ===")
    cams = build_tree(args.cams, args.hours, args.frames, args.present,
                      args.bystanders)
    sl.container_root_for_year = lambda year: ROOT / f"cpva-image-{year}"

    counter = _Counter()
    counter.wrap(sf)

    print("\n[the walking resolver — no shared cache, as it was]")
    counter.n = 0
    ms, found, _ = run(cams, None, "walk")
    walk_reads, walk_ms = counter.n, ms
    print(f"  {walk_reads:6d} folder reading(s)   {walk_ms:8.1f} ms   "
          f"{found}/{len(cams)} frame(s) found")

    cache = sf.DayScanCache()
    print("\n[the shared listing cache — cold]")
    ms, found, _ = run(cams, cache, "cold")
    cold_reads, cold_ms = cache.stats()[0], ms
    print(f"  {cold_reads:6d} folder reading(s)   {cold_ms:8.1f} ms   "
          f"{found}/{len(cams)} frame(s) found")

    print("\n[the shared listing cache — warm, the same moment again]")
    before = cache.stats()[0]
    ms, found, _ = run(cams, cache, "warm")
    warm_reads = cache.stats()[0] - before
    print(f"  {warm_reads:6d} folder reading(s)   {ms:8.1f} ms   "
          f"{found}/{len(cams)} frame(s) found")

    # A share reading is about 150 ms; local disk is far faster, so this is the number
    # that carries over to the operator's PC.
    share_ms = 150.0
    print("\n[what that would be on the share, at ~150 ms a reading]")
    print(f"  walking      {walk_reads * share_ms / 1000:8.1f} s")
    print(f"  cold cache   {cold_reads * share_ms / 1000:8.1f} s")
    print(f"  warm cache   {warm_reads * share_ms / 1000:8.1f} s")

    ok = True
    if warm_reads != 0:
        print(f"\nFAIL the warm pass read {warm_reads} folder(s); it must read none")
        ok = False
    if cold_reads > walk_reads:
        print(f"\nFAIL the cold pass ({cold_reads}) is no better than walking "
              f"({walk_reads})")
        ok = False
    print("\nOK" if ok else "\nFAILED")
    shutil.rmtree(ROOT, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
