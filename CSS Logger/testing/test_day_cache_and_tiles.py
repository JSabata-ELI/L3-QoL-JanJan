"""
Long loads: day by day, the day cache, and a look-back that always ends.

Reported 09.10.2026: three months of many signals loaded slowly, stopped at
"92 %" and never moved again (the program had to be killed), and the grey
"loading" area shrank without any curve appearing.

What this pins:

  * a long period is read NEWEST STEP FIRST, a whole step (day, or several days
    for a long period) for every signal before the next, and the "complete
    back to" edge only ever moves back in time,
  * a long period is read in at most MAX_TILES steps, so three months is not
    hundreds of requests per signal,
  * the day cache: a closed day read once is served from the cache the second
    time (no archiver request at all), a coarser stored day is not served for
    a finer request, today is never stored, words are never stored, and the
    month files survive two writers,
  * the look-back for the value before the window ends within its deadline
    even when the archiver refuses or times out on everything, and a cancel
    ends it at once.

Headless, network faked. The cache folder is a temporary one: a test must never
write into the shared cache on the scratch share.

Run:   python testing/test_day_cache_and_tiles.py
"""
import os
import pathlib
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import numpy as np

import cpva_cache
import cpva_core
from cpva_core import TZ_PRAGUE

HOUR = int(3600e9)
DAY = 24 * HOUR
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _midnight(days_ago: int) -> int:
    d = datetime.now(TZ_PRAGUE).date() - timedelta(days=days_ago)
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ_PRAGUE).timestamp() * 1e9)


class _Fake:
    """A small archiver: ``per_day`` thinned points a day (min/max carried)."""

    def __init__(self, per_day=48, words=()):
        self.calls = []
        self.per_day = per_day
        self.words = set(words)
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls.append((channel, int(start_ns), int(end_ns), count))
        n = max(2, int(self.per_day * (end_ns - start_ns) / DAY))
        step = max(1, (end_ns - start_ns) // n)
        out = [{"time": int(start_ns) - 1, "value": 0.5, "quality": "Original",
                "metaData": {"units": "J"}}]
        for i in range(n):
            t = int(start_ns) + i * step
            v = "OPEN" if channel in self.words else float((t // HOUR) % 17)
            out.append({"time": t, "value": v, "quality": "Original",
                        "metaData": {"units": "J"}})
        return out


def _install(fake):
    cpva_core.cpva_fetch_samples = fake
    cpva_core._DENSITY_MEMO.clear()      # each test is its own archive
    return fake


# ── steps: newest first, bounded in number ──────────────────────────────────

def test_long_period_steps():
    end = _midnight(0) + 10 * HOUR
    tiles = cpva_core._load_tiles(end - 90 * DAY, end)
    check("three months is read in at most MAX_TILES steps",
          len(tiles) <= cpva_core.MAX_TILES, f"{len(tiles)} steps")
    check("the steps are newest first and touch",
          all(tiles[i][0] == tiles[i + 1][1] for i in range(len(tiles) - 1)))
    check("the steps cover the whole period",
          tiles[0][1] == end and tiles[-1][0] == end - 90 * DAY)
    short = cpva_core._load_tiles(end - 5 * DAY, end)
    check("a short period is read one day at a time",
          len(short) == 6 and all(b - a <= 25 * HOUR for a, b in short),
          f"{len(short)} steps")


def test_edge_moves_back_and_whole_steps_first():
    fake = _install(_Fake())
    end = _midnight(0) + 10 * HOUR
    start = end - 6 * DAY
    edges, seen = [], []
    cpva_core.cpva_fetch_many_adaptive(
        ["A", "B"], start, end, count=None, preflight=False, max_workers=1,
        tile_fn=edges.append,
        chunk_fn=lambda ch, s: seen.append(max(x["time"] for x in s)))
    check("the complete-back-to edge only moves back",
          edges == sorted(edges, reverse=True) and len(edges) >= 5, str(len(edges)))
    check("it ends at the start of the period", edges and edges[-1] == start)
    first_day = cpva_core._load_tiles(start, end)[0]
    # With one worker the order is strict: both signals of the newest day
    # arrive before anything older.
    check("both signals of the newest day arrive before any older day",
          len(seen) >= 2 and all(t >= first_day[0] for t in seen[:2]),
          str(seen[:3]))
    check("no request crosses a step boundary",
          all(any(a >= lo and b <= hi for lo, hi in cpva_core._load_tiles(start, end))
              for _c, a, b, _n in fake.calls))


# ── the day cache ───────────────────────────────────────────────────────────

def test_cache_serves_closed_days():
    root = tempfile.mkdtemp(prefix="cpva_cache_test_")
    cache = cpva_cache.DayCache(root)
    fake = _install(_Fake())
    end = _midnight(0) + 10 * HOUR         # today, still open
    start = _midnight(4)                   # four closed days before it
    res1, err1, rep1 = cpva_core.cpva_fetch_many_adaptive(
        ["L3-TEST:Energy"], start, end, count=2000, preflight=False, cache=cache)
    deadline = time.time() + 10
    while time.time() < deadline and cache.stored < 4:
        time.sleep(0.05)
    files = list(pathlib.Path(root).rglob("*.npz"))
    check("closed days were written to month files", cache.stored == 4 and files,
          f"{cache.stored} days, {len(files)} files")

    fake2 = _install(_Fake())
    cache2 = cpva_cache.DayCache(root)                # a fresh program start
    res2, err2, rep2 = cpva_core.cpva_fetch_many_adaptive(
        ["L3-TEST:Energy"], start, end, count=2000, preflight=False, cache=cache2)
    asked = [c for c in fake2.calls if c[2] - c[1] > HOUR]
    closed = [c for c in asked if c[2] <= _midnight(0)]
    check("the second load asks the archiver nothing for the closed days",
          not closed, str(closed[:2]))
    check("but still reads today", any(c[2] == end for c in fake2.calls))
    check("the report says how many days came from the cache", rep2.cached == 4,
          str(rep2.cached))
    # The fake's bracket sample (value 0.5, one ns before each request) is not
    # a sample of the day before; the real archiver's is, and comes back with
    # that day's own answer.
    inside = lambda r: [s["time"] for s in r  # noqa: E731
                        if start <= s["time"] < _midnight(0) and s["value"] != 0.5]
    check("the cached days hold the same samples",
          inside(res1["L3-TEST:Energy"]) == inside(res2["L3-TEST:Energy"]),
          f"{len(inside(res1['L3-TEST:Energy']))} vs {len(inside(res2['L3-TEST:Energy']))}")


def test_cache_rules():
    root = tempfile.mkdtemp(prefix="cpva_cache_test_")
    cache = cpva_cache.DayCache(root)
    now = int(time.time() * 1e9)
    d0, d1 = _midnight(3), _midnight(2)
    samples = [{"time": d0 + i * HOUR, "value": [float(i)], "minimum": [0.0],
                "maximum": [float(i) + 1], "quality": "Interpolated"} for i in range(24)]
    check("a closed day is accepted",
          cache.put("P", d0, d1, samples, 24, "P", TZ_PRAGUE, now))
    check("today is refused",
          not cache.put("P", _midnight(0), _midnight(0) + DAY, samples, 24, "P",
                        TZ_PRAGUE, now))
    words = [{"time": d0 + 5, "value": "OPEN"}]
    check("a word value is refused",
          not cache.put("W", d0, d1, words, None, "W", TZ_PRAGUE, now))
    cache.flush()
    fresh = cpva_cache.DayCache(root)
    fresh.prefetch(["P"], [(d0, d1)], TZ_PRAGUE)
    got = fresh.get("P", d0, d1, 24, TZ_PRAGUE)
    check("it comes back with its minimum and maximum",
          got is not None and len(got) == 24 and got[5]["maximum"] == 6.0
          and got[5]["minimum"] == 0.0, str(got[5] if got else None))
    check("a finer request is not served from a coarser day",
          fresh.get("P", d0, d1, 240, TZ_PRAGUE) is None)
    check("a request for the exact samples is not served from a thinned day",
          fresh.get("P", d0, d1, None, TZ_PRAGUE) is None)

    # Two stations saving different days of the same month: both survive.
    a = cpva_cache.DayCache(root)
    b = cpva_cache.DayCache(root)
    d2 = _midnight(1)
    a.put("Q", d0, d1, samples, 24, "Q", TZ_PRAGUE, now)
    b.put("Q", d1, d2, [dict(s, time=s["time"] + DAY) for s in samples], 24, "Q",
          TZ_PRAGUE, now)
    a.flush()
    b.flush()
    c = cpva_cache.DayCache(root)
    c.prefetch(["Q"], [(d0, d1), (d1, d2)], TZ_PRAGUE)
    same_month = (datetime.fromtimestamp(d0 / 1e9, TZ_PRAGUE).month
                  == datetime.fromtimestamp(d1 / 1e9, TZ_PRAGUE).month)
    check("two writers of one month keep each other's days",
          not same_month or (c.get("Q", d0, d1, 24, TZ_PRAGUE) is not None
                             and c.get("Q", d1, d2, 24, TZ_PRAGUE) is not None))
    off = cpva_cache.DayCache("")
    check("an empty root means no cache at all",
          not off.ready(0.1) and off.get("P", d0, d1, 24, TZ_PRAGUE) is None)


# ── the look-back always ends ───────────────────────────────────────────────

class _Refuse(Exception):
    def __init__(self):
        super().__init__("HTTP 500")
        self.response = type("R", (), {"status_code": 500})()


def test_lookback_is_bounded():
    calls = []

    def hopeless(channel, a, b, timeout=10.0, count=None):
        calls.append((channel, a, b))
        time.sleep(0.01)
        raise _Refuse()

    cpva_core.cpva_fetch_samples = hopeless
    t0 = time.monotonic()
    got = cpva_core.cpva_fetch_last_before_many(
        ["A", "B", "C", cpva_core.SBW4_HAPLS], _midnight(0), 10.0, deadline_s=3.0)
    took = time.monotonic() - t0
    check("an archiver refusing everything does not hang the look-back",
          took < 6.0, f"{took:.1f} s")
    check("and it does not fan out into hundreds of requests",
          len(calls) <= 5 * cpva_core.LAST_BEFORE_MAX_REQUESTS, f"{len(calls)} requests")
    check("nothing is invented", got == {}, str(got))

    def slow(channel, a, b, timeout=10.0, count=None):
        time.sleep(5)
        return []

    cpva_core.cpva_fetch_samples = slow
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    t0 = time.monotonic()
    cpva_core.cpva_fetch_last_before_many(["A", "B"], _midnight(0), 10.0,
                                          cancel_fn=flag.is_set, deadline_s=30.0)
    took = time.monotonic() - t0
    check("a cancel ends it without waiting for the running requests",
          took < 1.5, f"{took:.1f} s")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    real = cpva_core.cpva_fetch_samples
    for fn in TESTS:
        print(f"\n── {fn.__name__} ──")
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__, False, f"raised {exc}")
        finally:
            cpva_core.cpva_fetch_samples = real
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
