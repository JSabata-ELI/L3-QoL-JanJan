"""Measuring how densely a channel is written, and what "zero" means.

The freeze of 2026-09-02 came down to one line: every probe of a 180-day window
landed on an empty hour, the rate came out as zero, and zero was planned as
"the quietest channel there is - ask for whole days at a time". Whole days of a
busy channel is what filled the memory.

Run with:  python testing/test_probe_honesty.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402

HOUR = int(3600 * 1e9)
DAY = 24 * HOUR
WINDOW = 180 * DAY


def probe(fetch):
    return ch.probe_rate("PV", 0, WINDOW, timeout=1.0, fetch=fetch)


def readings(n):
    """A fetch that answers every probe with n readings."""
    return lambda c, s, e, t=None: [{"time": s, "value": 1.0}] * n


def test_an_answered_empty_hour_is_a_measurement():
    p = probe(readings(0))
    assert p.known and p.rate_per_hour == 0.0
    assert p.n_answered == 5 and p.n_failed == 0
    # A channel that really is quiet is still read a whole day at a time -
    # that is what makes 180 days affordable.
    assert ch.plan_span_for(p) == api.MAX_CHUNK_SPAN_NS


def test_a_probe_that_could_not_read_anything_is_not_a_measurement():
    def dead(c, s, e, t=None):
        raise requests.ConnectionError("archiver unreachable")

    p = probe(dead)
    assert not p.known
    assert p.n_failed == 5 and p.n_answered == 0
    # The crash case: never plan the largest possible request on ignorance.
    assert ch.plan_span_for(p) == api.CHUNK_SIZE_NS
    assert ch.plan_span_for(p) < api.MAX_CHUNK_SPAN_NS


def test_one_probe_getting_through_is_enough_to_plan_on():
    calls = {"n": 0}

    def flaky(c, s, e, t=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise requests.Timeout("slow")
        return [{"time": s, "value": 1.0}] * 4000

    p = probe(flaky)
    assert p.known and p.n_answered == 1
    assert p.rate_per_hour == 4000
    assert ch.plan_span_for(p) == ch.plan_chunk_span(4000)


def test_the_busiest_hour_wins():
    def shifts(c, s, e, t=None):
        # Quiet everywhere except one probe point.
        n = 30_000 if s > 0.4 * WINDOW and s < 0.6 * WINDOW else 0
        return [{"time": s, "value": 1.0}] * n

    p = probe(shifts)
    assert p.rate_per_hour == 30_000
    span = ch.plan_span_for(p)
    assert span <= 2 * HOUR, span


def test_a_probe_too_big_to_read_is_the_strongest_answer_about_density():
    def dense(c, s, e, t=None):
        raise api.ResponseTooLarge(99_000_000, api.MAX_RESPONSE_BYTES)

    p = probe(dense)
    assert p.too_big
    # It answered - it answered "more than I can hold in an hour" - so this is
    # knowledge, not ignorance, and it must plan SMALL, not big.
    assert p.known
    span = ch.plan_span_for(p)
    assert span <= HOUR, span
    assert span >= api.SPLIT_MIN_SPAN_NS


def test_the_probe_looks_at_both_ends_of_the_window():
    seen = []

    def note(c, s, e, t=None):
        seen.append(s)
        return []

    probe(note)
    assert len(seen) == 5, seen
    assert min(seen) < 0.05 * WINDOW, "the start of the window was never probed"
    assert max(seen) > 0.95 * WINDOW, "the end of the window was never probed"


def test_measuring_stays_a_small_part_of_the_reading():
    """A short window must not be half read twice just to size its requests."""
    for window, most in ((HOUR, 1), (8 * HOUR, 2), (DAY, 5)):
        seen = []

        def note(c, s, e, t=None):
            seen.append((s, e))
            return []

        p = ch.probe_rate("PV", 0, window, timeout=1.0, fetch=note)
        assert p.known
        assert len(seen) <= most, (window, seen)
        probed = sum(e - s for s, e in seen)
        assert probed <= window / 2, (window, probed)
        for (_, prev_end), (next_start, _) in zip(sorted(seen), sorted(seen)[1:]):
            assert next_start >= prev_end, (window, seen)


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")


if __name__ == "__main__":
    _run_all()
    print("all probe-honesty tests passed")
