"""The ceiling on one answer, and handing pieces over instead of piling them up.

Both halves of the fix for the freeze of 2026-09-02, when a 180-day plot asked
for whole days of a busy channel and held ~24 GB of parsed readings.

No network: the HTTP session and cpva_fetch_samples are replaced by fakes.

Run with:  python testing/test_response_ceiling.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import cpva_api as api  # noqa: E402

HOUR = int(3600 * 1e9)
MINUTE = int(60 * 1e9)


# --- the ceiling in the HTTP layer -----------------------------------------

class FakeBody:
    """A response whose body arrives in pieces, like a streamed one."""

    def __init__(self, chunks, headers=None, status=200):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status
        self.closed = False
        self.n_yielded = 0

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size=None):
        for c in self._chunks:
            self.n_yielded += 1
            yield c

    def close(self):
        self.closed = True


def with_session(resp, fn):
    real = api._SESSION.get
    api._SESSION.get = lambda url, **kw: resp
    try:
        return fn()
    finally:
        api._SESSION.get = real


def test_a_normal_answer_is_parsed():
    resp = FakeBody([b'[{"time": 1,', b' "value": 2.5}]'])
    out = with_session(resp, lambda: api._http_get_json("u", timeout=1.0))
    assert out == [{"time": 1, "value": 2.5}]
    assert resp.closed, "the response was left open"


def test_an_oversized_answer_is_refused_and_the_download_abandoned():
    part = b"x" * (1024 * 1024)
    resp = FakeBody([part] * 64)          # 64 MB offered, 32 MB allowed
    try:
        with_session(resp, lambda: api._http_get_json("u", timeout=1.0))
    except api.ResponseTooLarge as exc:
        assert exc.limit == api.MAX_RESPONSE_BYTES
    else:
        raise AssertionError("64 MB of readings were swallowed whole")
    # The point of streaming: it stopped early instead of holding all 64 MB.
    assert resp.n_yielded < 64, resp.n_yielded
    assert resp.closed, "an abandoned download left its connection busy"


def test_a_declared_size_costs_no_download_at_all():
    resp = FakeBody([b"x"], headers={"Content-Length": str(500 * 1024 * 1024)})
    try:
        with_session(resp, lambda: api._http_get_json("u", timeout=1.0))
    except api.ResponseTooLarge:
        pass
    else:
        raise AssertionError("an answer announcing 500 MB was accepted")
    assert resp.n_yielded == 0, "it started downloading anyway"


def test_a_smaller_ceiling_can_be_asked_for():
    resp = FakeBody([b"x" * 2048])
    try:
        with_session(resp, lambda: api._http_get_json("u", timeout=1.0,
                                                     max_bytes=1024))
    except api.ResponseTooLarge:
        pass
    else:
        raise AssertionError("max_bytes was ignored")


def test_our_own_refusal_counts_as_splittable():
    assert api.cpva_is_splittable_error(api.ResponseTooLarge(1, 0))


# --- handing pieces over instead of collecting them ------------------------

class FakeArchiver:
    """One reading per minute, unless the range is 'too large' to answer."""

    def __init__(self, too_big_above_ns=None, fail_if=None):
        self.too_big_above_ns = too_big_above_ns
        self.fail_if = fail_if or (lambda c, s, e: None)
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout=None):
        with self._lock:
            self.calls.append((channel, start_ns, end_ns))
        exc = self.fail_if(channel, start_ns, end_ns)
        if exc is not None:
            raise exc
        if (self.too_big_above_ns is not None
                and end_ns - start_ns > self.too_big_above_ns):
            raise api.ResponseTooLarge(99_000_000, api.MAX_RESPONSE_BYTES)
        out = []
        t = start_ns
        while t < end_ns:
            out.append({"time": t, "value": 1.0})
            t += MINUTE
        return out


def with_fake(fake, fn):
    real = api.cpva_fetch_samples
    api.cpva_fetch_samples = fake
    try:
        return fn()
    finally:
        api.cpva_fetch_samples = real


def collect_pieces(fake, start=0, end=HOUR, **kw):
    pieces = []
    with_fake(fake, lambda: api.cpva_fetch_samples_piecewise(
        "PV", start, end, timeout=1.0,
        on_piece=lambda s, e, samples: pieces.append((s, e, samples)), **kw))
    return pieces


def test_a_split_range_arrives_in_pieces_and_never_as_one_pile():
    fake = FakeArchiver(too_big_above_ns=15 * MINUTE)
    pieces = collect_pieces(fake)
    assert len(pieces) == 4, [(s, e) for s, e, _ in pieces]
    # This is the whole point: the largest thing ever held is one piece, not
    # the hour. Collecting the halves would have made memory grow with the
    # window however small each answer was.
    assert max(len(samples) for _, _, samples in pieces) <= 16
    assert sum(len(samples) for _, _, samples in pieces) == 60


def test_the_pieces_cover_the_range_without_overlapping():
    fake = FakeArchiver(too_big_above_ns=15 * MINUTE)
    pieces = sorted((s, e) for s, e, _ in collect_pieces(fake))
    assert pieces[0][0] == 0 and pieces[-1][1] == HOUR
    for (_, prev_end), (next_start, _) in zip(pieces, pieces[1:]):
        assert next_start == prev_end + 1, (prev_end, next_start)
    times = [s["time"] for _, _, samples in collect_pieces(fake) for s in samples]
    assert len(times) == len(set(times)), "a boundary reading came back twice"


def test_one_unreadable_piece_does_not_cost_the_others():
    # The second quarter-hour is a bad channel name; the rest is fine.
    def fail_if(c, s, e):
        if s == 15 * MINUTE + 1 and e - s <= 15 * MINUTE:
            return requests.HTTPError("HTTP 404", response=None)
        return None

    fake = FakeArchiver(too_big_above_ns=15 * MINUTE, fail_if=fail_if)
    pieces, bad = [], []
    with_fake(fake, lambda: api.cpva_fetch_samples_piecewise(
        "PV", 0, HOUR, timeout=1.0,
        on_piece=lambda s, e, smp: pieces.append((s, e, smp)),
        on_piece_error=lambda s, e, exc: bad.append((s, e))))
    assert len(pieces) == 3, [(s, e) for s, e, _ in pieces]
    assert len(bad) == 1 and bad[0][0] == 15 * MINUTE + 1


def test_without_an_error_callback_the_failure_is_still_raised():
    fake = FakeArchiver(fail_if=lambda c, s, e: requests.HTTPError(
        "HTTP 404", response=None))
    try:
        collect_pieces(fake)
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("a 404 was swallowed")


def test_giving_up_stops_the_split_halfway():
    fake = FakeArchiver(too_big_above_ns=15 * MINUTE)
    pieces = collect_pieces(fake, cancel_fn=lambda: len(fake.calls) >= 3)
    assert len(fake.calls) <= 4, fake.calls
    assert len(pieces) < 4


# --- through the shared scheduler ------------------------------------------

def test_the_scheduler_reports_each_piece_with_its_own_bounds():
    fake = FakeArchiver(too_big_above_ns=15 * MINUTE)
    got, lock = [], threading.Lock()
    task = api.ChunkTask("PV", 0, HOUR, 7)

    def keep(t, samples):
        with lock:
            got.append((t.start_ns, t.end_ns, t.index, len(samples)))

    done, failed = with_fake(fake, lambda: api.cpva_run_chunks(
        [task], timeout=1.0, max_workers=1, on_result=keep))
    assert (done, failed) == (1, 0)          # one planned request, one result
    assert len(got) == 4, got
    # Each piece carries the stretch it actually covers, so a caller that
    # tracks what was read stays right after a split.
    assert sorted(g[:2] for g in got)[0][1] - sorted(g[:2] for g in got)[0][0] \
        <= 15 * MINUTE
    assert all(g[2] == 7 for g in got), "the task's own ordering was lost"


def test_a_partly_readable_task_counts_as_failed_but_keeps_its_good_pieces():
    def fail_if(c, s, e):
        if s == 0 and e - s <= 30 * MINUTE:
            return requests.HTTPError("HTTP 404", response=None)
        return None

    fake = FakeArchiver(too_big_above_ns=30 * MINUTE, fail_if=fail_if)
    kept, bad, lock = [], [], threading.Lock()

    def keep(t, samples):
        with lock:
            kept.append(len(samples))

    def note(t, exc):
        with lock:
            bad.append((t.start_ns, t.end_ns))

    done, failed = with_fake(fake, lambda: api.cpva_run_chunks(
        [api.ChunkTask("PV", 0, HOUR, 0)], timeout=1.0, max_workers=1,
        on_result=keep, on_error=note))
    assert (done, failed) == (0, 1)
    assert sum(kept) == 30, kept       # the readable half still arrived
    assert bad == [(0, 30 * MINUTE)]


def test_a_split_chunk_loses_no_readings_in_the_chunked_fetch():
    """Pieces share one chunk index; keying results by index alone drops them."""
    fake = FakeArchiver(too_big_above_ns=15 * MINUTE)
    out = with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
        "PV", 0, 2 * HOUR, timeout=1.0, max_workers=2))
    assert len(out) == 120, len(out)
    times = [s["time"] for s in out]
    assert times == sorted(times), "the pieces came back out of order"
    assert len(times) == len(set(times))


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")


if __name__ == "__main__":
    _run_all()
    print("all response-ceiling tests passed")
