"""What happens to a chunked fetch when the archiver refuses some of it.

No network: cpva_api.cpva_fetch_samples is replaced by a fake that answers from
a script, so every failure mode is reproducible.

Run with:  python testing/test_fetch_isolation.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

import cpva_api as api  # noqa: E402

HOUR = int(3600 * 1e9)


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def http_error(status=500):
    return requests.HTTPError(f"HTTP {status}", response=FakeResponse(status))


class FakeArchiver:
    """Answers /samples from a rule, and remembers every range it was asked for.

    `fail_if(channel, start, end)` returns an exception to raise, or None to
    answer normally with one sample per minute.
    """

    def __init__(self, fail_if=None):
        self.fail_if = fail_if or (lambda c, s, e: None)
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout=None):
        with self._lock:
            self.calls.append((channel, start_ns, end_ns))
        exc = self.fail_if(channel, start_ns, end_ns)
        if exc is not None:
            raise exc
        out = []
        t = start_ns
        while t < end_ns:
            out.append({"time": t, "value": 1.0})
            t += int(60 * 1e9)
        return out


def with_fake(fake, fn):
    real = api.cpva_fetch_samples
    api.cpva_fetch_samples = fake
    try:
        return fn()
    finally:
        api.cpva_fetch_samples = real


# --- splitting -------------------------------------------------------------

def test_oversize_range_is_split_until_it_fits():
    # Anything longer than 15 min is "too big"; the halving must find that out.
    fake = FakeArchiver(lambda c, s, e: http_error() if e - s > 15 * 60 * 1e9
                        else None)
    out = with_fake(fake, lambda: api.cpva_fetch_samples_split(
        "PV", 0, HOUR, timeout=1.0))
    assert len(out) == 60, len(out)
    # 1 h refused, 2 x 30 min refused, 4 x 15 min accepted = 7 requests.
    assert len(fake.calls) == 7, fake.calls


def test_split_reports_the_span_that_failed():
    seen = []
    fake = FakeArchiver(lambda c, s, e: http_error() if e - s > 15 * 60 * 1e9
                        else None)
    with_fake(fake, lambda: api.cpva_fetch_samples_split(
        "PV", 0, HOUR, timeout=1.0, on_split=lambda ch, span: seen.append(span)))
    assert seen and seen[0] == HOUR


def test_client_error_is_not_split():
    fake = FakeArchiver(lambda c, s, e: http_error(404))
    try:
        with_fake(fake, lambda: api.cpva_fetch_samples_split(
            "PV", 0, HOUR, timeout=1.0))
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("a 404 was split instead of raised")
    assert len(fake.calls) == 1, fake.calls


def test_split_gives_up_at_the_floor():
    fake = FakeArchiver(lambda c, s, e: http_error())
    try:
        with_fake(fake, lambda: api.cpva_fetch_samples_split(
            "PV", 0, HOUR, timeout=1.0))
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("an always-failing range was not raised")
    # Depth is bounded, so this stops long before it reaches one nanosecond.
    assert len(fake.calls) <= 2 ** (api.SPLIT_MAX_DEPTH + 1)


def test_halves_do_not_overlap():
    fake = FakeArchiver(lambda c, s, e: http_error() if e - s > 30 * 60 * 1e9
                        else None)
    out = with_fake(fake, lambda: api.cpva_fetch_samples_split(
        "PV", 0, HOUR, timeout=1.0))
    times = [s["time"] for s in out]
    assert len(times) == len(set(times)), "a boundary sample came back twice"


# --- one bad chunk must not kill the rest ----------------------------------

def test_without_errors_dict_the_first_failure_still_raises():
    """The historical contract: three existing callers rely on it."""
    fake = FakeArchiver(lambda c, s, e: http_error(404) if s == HOUR else None)
    try:
        with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
            "PV", 0, 4 * HOUR, timeout=1.0))
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("a failed chunk was swallowed")


def test_with_errors_dict_the_good_chunks_survive():
    fake = FakeArchiver(lambda c, s, e: http_error(404) if s == HOUR else None)
    errors = {}
    out = with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
        "PV", 0, 4 * HOUR, timeout=1.0, errors=errors))
    assert errors and "PV" in errors
    assert len(out) == 180, len(out)          # 3 of 4 hours, 60 samples each
    assert out == sorted(out, key=lambda s: s["time"])


def test_chunk_size_is_adjustable():
    fake = FakeArchiver()
    with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
        "PV", 0, 24 * HOUR, timeout=1.0, chunk_ns=6 * HOUR))
    assert len(fake.calls) == 4, fake.calls


# --- the shared scheduler --------------------------------------------------

def test_one_pv_failing_leaves_the_other_complete():
    fake = FakeArchiver(lambda c, s, e: http_error(404) if c == "BAD" else None)
    got = {"GOOD": 0, "BAD": 0}
    failed = []
    lock = threading.Lock()          # on_result/on_error run in worker threads
    tasks = [api.ChunkTask(pv, i * HOUR, (i + 1) * HOUR, i)
             for pv in ("GOOD", "BAD") for i in range(4)]

    def keep(task, samples):
        with lock:
            got[task.channel] += len(samples)

    def note(task, exc):
        with lock:
            failed.append(task.channel)

    def run():
        return api.cpva_run_chunks(tasks, timeout=1.0, max_workers=4,
                                   on_result=keep, on_error=note)

    done, nfailed = with_fake(fake, run)
    assert got["GOOD"] == 240, got
    assert got["BAD"] == 0
    assert done == 4 and nfailed == 4
    assert failed == ["BAD"] * 4


def test_progress_counts_every_task_once():
    fake = FakeArchiver(lambda c, s, e: http_error(404) if s == HOUR else None)
    seen = []
    lock = threading.Lock()
    tasks = [api.ChunkTask("PV", i * HOUR, (i + 1) * HOUR, i) for i in range(5)]

    def note(done, total):
        with lock:
            seen.append((done, total))

    with_fake(fake, lambda: api.cpva_run_chunks(
        tasks, timeout=1.0, max_workers=2, progress_fn=note))
    assert seen[-1] == (5, 5), seen


def test_cancel_stops_issuing_requests():
    fake = FakeArchiver()
    tasks = [api.ChunkTask("PV", i * HOUR, (i + 1) * HOUR, i) for i in range(50)]
    state = {"n": 0}

    def cancel():
        state["n"] += 1
        return state["n"] > 5

    with_fake(fake, lambda: api.cpva_run_chunks(
        tasks, timeout=1.0, max_workers=1, cancel_fn=cancel))
    assert len(fake.calls) < 50, len(fake.calls)


def test_span_hint_pre_splits_the_remaining_chunks():
    """A channel that refuses 1 h must not be asked for 1 h fifty more times."""
    fake = FakeArchiver(lambda c, s, e: http_error() if e - s > 30 * 60 * 1e9
                        else None)
    hint = {}
    tasks = [api.ChunkTask("PV", i * HOUR, (i + 1) * HOUR, i) for i in range(10)]
    with_fake(fake, lambda: api.cpva_run_chunks(
        tasks, timeout=1.0, max_workers=1, span_hint=hint))
    assert hint.get("PV") == HOUR // 2
    oversize = [c for c in fake.calls if c[2] - c[1] > 30 * 60 * 1e9]
    assert len(oversize) <= 2, oversize


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
