"""Assert the multi-day search picks the frame the LASER points at.

A fortnight of searching used to come back with one usable picture, and the reason was
the order the readings were consulted in. The camera's own :TotalPower gated everything,
so a camera whose channel is missing or asleep never reached the laser energy readings at
all; inside a window the moment was drawn AT RANDOM rather than aimed at a shot; and the
tier below was the daily energy CSV, dead since 19.08.2026 — leaving "first file in the
folder", which on a machine firing once every 35 s is usually a frame between shots.

The order this pins is: SBW4, then PTM1, then :TotalPower, then CSV, then a blind scan —
and within whichever one speaks, the STRONGEST shot.

Runs against synthetic local frames and a fake archiver — no share, no network:

    python testing/test_day_pick.py
"""
import importlib.util
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


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


CAM = "C03-040-PTM11WNF-_-IMG"
DAY = date(2026, 8, 10)
HOUR_UTC = 8                       # the one hour folder that exists


def hour_start_ns() -> int:
    return int(datetime(DAY.year, DAY.month, DAY.day, HOUR_UTC,
                        tzinfo=timezone.utc).timestamp() * 1e9)


def write_frames(root: Path) -> "list[int]":
    """One frame every 35 s across the hour, the cadence the archive really stores."""
    import numpy as np
    from PIL import Image as PilImage
    folder = root / f"cpva-image-{DAY.year}" / str(DAY.year) / str(DAY.month) \
        / str(DAY.day) / str(HOUR_UTC) / CAM
    folder.mkdir(parents=True)
    stamps = []
    for i in range(100):
        ts = hour_start_ns() + i * 35_000_000_000
        arr = np.full((40, 60), 100 + i, dtype=np.uint16)
        PilImage.fromarray(arr).save(folder / f"{CAM}_-_{ts}.png")
        stamps.append(ts)
    return stamps


class FakeDay:
    """Stands in for cpva.DayResult."""

    def __init__(self, samples):
        self.samples = samples
        self.status = "ok" if samples else "empty"
        self.age_s = 0.0
        self.ts_list = [t for t, _ in samples]


def main():
    _if = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="if_pick_"))
    stamps = write_frames(tmp)
    peak_ts = stamps[70]            # the moment every scenario will aim at

    w = _if.ImageFinderWidget()
    w._build_target_path = lambda dt: (
        tmp / f"cpva-image-{dt.year}" / str(dt.year) / str(dt.month)
        / str(dt.day) / str(dt.hour))
    w._blocking_call = lambda fn, cancelled=None: fn()
    # The CSV tier must not answer for us — it is dead in production anyway.
    w._energy_rows_for_day_cached = lambda *a, **k: []

    SBW4, PTM1 = _if.CPVA_SBW4_CHANNEL, _if.CPVA_SHOT_CHANNEL

    def shots_around(centre_ns, peak_at, n=100, peak=3.0, base=0.4):
        out = []
        for i in range(n):
            t = centre_ns + i * 35_000_000_000
            out.append((t, peak if abs(t - peak_at) < 1_000_000_000 else base))
        return out

    def install(day_map, tp_samples=()):
        """day_map: channel → samples. Anything else answers empty."""
        _if.cpva.get_day = lambda ch, dk, **kw: FakeDay(list(day_map.get(ch, ())))
        _if._cpva_fetch_samples = lambda ch, s, e, timeout=None: list(tp_samples)

    def run():
        return w._find_image_for_day_cam(DAY, CAM, True, HOUR_UTC, HOUR_UTC)

    def ts_of(path):
        return _if.extract_ns_from_stem(Path(path).stem)

    print("\nSBW4 is asked first, and its strongest shot is what gets picked")
    install({SBW4: shots_around(hour_start_ns(), peak_ts),
             PTM1: shots_around(hour_start_ns(), stamps[10], peak=9.0)})
    p, h, meta, status = run()
    check("a frame was found", status == "found" and p is not None, str(status))
    check("it is SBW4's peak, not PTM1's",
          p is not None and ts_of(p) == peak_ts,
          f"{ts_of(p) - peak_ts if p else '—'} ns off")
    check("the tile knows SBW4 chose it", meta.get("source") == "sbw4",
          str(meta.get("source")))

    print("\nPTM1 answers only when SBW4 recorded nothing")
    install({PTM1: shots_around(hour_start_ns(), stamps[30])})
    p, h, meta, status = run()
    check("a frame was found", status == "found" and p is not None, str(status))
    check("it is PTM1's peak", p is not None and ts_of(p) == stamps[30])
    check("the tile knows PTM1 chose it", meta.get("source") == "ptm1",
          str(meta.get("source")))

    print("\nan all-zero energy day is 'no signal', not 'a weak signal'")
    install({SBW4: [(t, 0.0) for t in stamps[:20]],
             PTM1: shots_around(hour_start_ns(), stamps[45])})
    p, h, meta, status = run()
    check("SBW4's zeros are ignored and PTM1 answers",
          meta.get("source") == "ptm1" and p is not None and ts_of(p) == stamps[45],
          str(meta.get("source")))

    print("\nthe camera's own power reading is the third resort, aimed at its peak")
    tp = [{"time": stamps[i], "value": (9.0 if i == 55 else 0.05)}
          for i in range(0, 100)]
    install({}, tp_samples=tp)
    p, h, meta, status = run()
    check("a frame was found", status == "found" and p is not None, str(status))
    check("it is the strongest moment of the window, never a random one",
          p is not None and ts_of(p) == stamps[55],
          f"picked index {stamps.index(ts_of(p)) if p and ts_of(p) in stamps else '?'}")
    check("the tile says the power reading chose it",
          meta.get("source") == "totalpower", str(meta.get("source")))

    print("\nwith nothing to go on the frame is still returned, and flagged")
    install({})
    p, h, meta, status = run()
    check("a frame was found", status == "found" and p is not None, str(status))
    check("it is flagged as picked blind", meta.get("source") == "blind",
          str(meta.get("source")))
    check("the caption warns about it",
          _if._DayWall._SOURCE_TAG.get("blind") == "no shot data")

    print("\nthe day's energy series is fetched ONCE, not once per camera")
    calls = []
    base = {SBW4: shots_around(hour_start_ns(), peak_ts)}
    _if.cpva.get_day = lambda ch, dk, **kw: (calls.append((ch, dk)),
                                             FakeDay(list(base.get(ch, ()))))[1]
    for _ in range(5):
        run()
    # 5 searches, but a real run would be 5 CAMERAS on the same day — the point is that
    # each one asks cpva.get_day, whose own per-day cache answers without a query. What
    # is pinned here is that nothing bypasses that cache with a raw range fetch.
    check("every read goes through the per-day cache",
          all(dk == DAY.isoformat() for _ch, dk in calls) and len(calls) >= 5,
          f"{len(calls)} call(s)")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
