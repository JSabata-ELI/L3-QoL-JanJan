"""The day cache may hold PART of a day — and must never pass it off as all of it.

Shot Finder searches 07:00–21:00, so asking the archiver for 00:00–24:00 means ten
hours fetched, parsed and thrown away on every channel of every day. `get_day` now
takes the slice the caller actually wants (`span_ns`) and remembers it with the
samples, which makes one rule the whole thing rests on:

    a cached slice answers only questions that fit INSIDE it.

Serving a 07–21 entry to somebody who asked for the whole day would report the
night as "nothing archived" — the one failure this must not have.

No archiver and no Qt: the transport is replaced by a counter.

    python testing/test_cpva_span_cache.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import cpva_client as cpva                               # noqa: E402

FAILURES: "list[str]" = []
CH = "TEST:Span"
DAY = "2026-08-18"                                       # a finished past day
H = 3_600_000_000_000


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ── the fake archiver ─────────────────────────────────────────────────────────
ASKED: "list[tuple[int, int]]" = []


def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=0.0,
                         try_value_suffix=True):
    """One sample an hour, only inside the window asked for — like the real one."""
    ASKED.append((start_ns, end_ns))
    t = start_ns
    out = []
    while t < end_ns:
        out.append((t, 1.0))
        t += H
    return out, channel


cpva.fetch_values_ex = fake_fetch_values_ex


def hours(day_key: str, h_from: int, h_to: int) -> "tuple[int, int]":
    s, _e = cpva.day_bounds_ns(day_key)
    return s + h_from * H, s + h_to * H


def reset():
    cpva.invalidate()
    ASKED.clear()


# ── the checks ────────────────────────────────────────────────────────────────
def test_only_the_window_is_read():
    print("\nonly the picked hours are asked for")
    reset()
    win = hours(DAY, 7, 21)
    res = cpva.get_day(CH, DAY, span_ns=win)
    check("one request", len(ASKED) == 1, str(len(ASKED)))
    check("and it asked for 07:00–21:00, not the whole day", ASKED[0] == win,
          f"{ASKED[0]} vs {win}")
    check("fourteen samples came back", len(res.samples) == 14, str(len(res.samples)))
    check("status ok", res.status == "ok", res.status)


def test_cache_inside_the_window():
    print("\na question inside the cached slice costs nothing")
    reset()
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 7, 21))
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 7, 21))
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 9, 12))
    check("the same window is served from memory", len(ASKED) == 1, str(len(ASKED)))
    check("and so is a narrower one", len(ASKED) == 1, str(len(ASKED)))


def test_wider_question_refetches():
    print("\na question the slice cannot answer is NOT answered from it")
    reset()
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 7, 21))
    res = cpva.get_day(CH, DAY)                       # the whole day
    check("the whole day is fetched again", len(ASKED) == 2, str(len(ASKED)))
    check("over the whole day", ASKED[1] == cpva.day_bounds_ns(DAY),
          str(ASKED[1]))
    check("and the night is there", len(res.samples) == 24, str(len(res.samples)))
    # …and now the narrow question is covered by the wide entry.
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 7, 21))
    check("the wider entry then answers the narrow question",
          len(ASKED) == 2, str(len(ASKED)))


def test_peek_respects_the_slice():
    print("\npeek_day says 'not cached' rather than 'half of it'")
    reset()
    cpva.get_day(CH, DAY, span_ns=hours(DAY, 7, 21))
    check("a peek inside the slice hits",
          cpva.peek_day(CH, DAY, hours(DAY, 8, 9)) is not None)
    check("a peek at the whole day misses", cpva.peek_day(CH, DAY) is None)


def test_warm_days_passes_the_span():
    print("\nwarm_days warms the picked hours, in the number of threads asked for")
    reset()
    days = ["2026-08-18", "2026-08-19", "2026-08-20"]
    spans = {d: hours(d, 7, 21) for d in days}
    seen: "list[str]" = []
    cpva.warm_days([CH], days, span_for_day=spans.get,
                   max_workers=cpva.FOREGROUND_WARM_WORKERS,
                   on_done=lambda ch, dk, n, tot: seen.append(dk))
    check("every day warmed", sorted(seen) == days, str(sorted(seen)))
    check("each one only for its own window",
          all((e - s) == 14 * H for s, e in ASKED),
          str([(e - s) / H for s, e in ASKED]))
    check("and all of them are in the cache now",
          all(cpva.peek_day(CH, d, spans[d]) is not None for d in days))


def test_cache_bound_still_holds():
    print("\nthe cache never grows past its bound")
    reset()
    many = [f"2026-06-{d:02d}" for d in range(1, 29)]
    cpva.warm_days([CH, CH + "2", CH + "3"], many,
                   span_for_day=lambda dk: hours(dk, 7, 21))
    check(f"at most {cpva._DAY_CACHE_MAX} days held",
          len(cpva._day_cache) <= cpva._DAY_CACHE_MAX, str(len(cpva._day_cache)))


def main():
    test_only_the_window_is_read()
    test_cache_inside_the_window()
    test_wider_question_refetches()
    test_peek_respects_the_slice()
    test_warm_days_passes_the_span()
    test_cache_bound_still_holds()
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
