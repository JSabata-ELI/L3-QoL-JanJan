"""The three ways a window of samples becomes one number, and the widening read.

No network: every test stands a stub in for the fetch. The live check against
the real archiver is `bench_archiver.py`.

Run:  python testing/test_cpva.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ann_core as C          # noqa: E402
import ann_cpva as A          # noqa: E402

NS = 1_000_000_000


def series(*pairs):
    """(seconds ago, value) → archiver-shaped samples."""
    now = A.now_ns()
    return sorted(((now - int(sec * NS), float(v)) for sec, v in pairs),
                  key=lambda x: x[0])


# ── the reductions ──────────────────────────────────────────────────────────

def test_peak_is_the_worst_sample_of_the_window():
    now = A.now_ns()
    s = series((9, 1.0), (5, 9.0), (1, 2.0))
    assert A.reduce_peak(s, now - 10 * NS) == 9.0


def test_peak_ignores_samples_older_than_the_window():
    now = A.now_ns()
    s = series((30, 99.0), (5, 2.0))
    assert A.reduce_peak(s, now - 10 * NS) == 2.0


def test_peak_falls_back_to_the_value_still_being_held():
    """A value that went high and then sat still writes nothing more.

    The newest sample of the wider window is what the machine is STILL holding,
    so it is the right answer — not "no data".
    """
    now = A.now_ns()
    s = series((45, 7.0))                    # nothing in the last ten seconds
    assert A.reduce_peak(s, now - 10 * NS) == 7.0


def test_peak_of_nothing_is_nothing():
    assert A.reduce_peak([], A.now_ns()) is None


def test_mean_averages_only_the_newest_few():
    s = series(*[(i, 100.0) for i in range(30, 5, -1)])
    s += series(*[(i, 0.0) for i in range(5, 0, -1)])
    s.sort()
    assert A.reduce_mean(s, 5) == 0.0        # the newest five are the zeros
    assert A.reduce_mean([], 5) is None


def test_mean_is_what_keeps_a_chiller_quiet():
    """A single sample crosses +-0.3 constantly; the average does not."""
    s = series((5, 0.4), (4, -0.4), (3, 0.35), (2, -0.35), (1, 0.0))
    assert abs(A.reduce_mean(s, 25)) < 0.05
    assert A.reduce_peak(s, A.now_ns() - 10 * NS) == 0.4


def test_last_is_the_newest_sample():
    assert A.reduce_last(series((99, 1.0), (3, 2.0))) == 2.0
    assert A.reduce_last([]) is None


# ── holding a sparse channel forward ────────────────────────────────────────

def test_hold_forward_answers_for_an_empty_window():
    now = A.now_ns()
    setpoint = series((7 * 86400, 12.0))     # written once, a week ago
    assert A.hold_forward(setpoint, now) == 12.0


def test_hold_forward_takes_the_newest_value_at_or_before():
    s = series((100, 1.0), (50, 2.0), (10, 3.0))
    now = s[-1][0] + 10 * NS
    # Sixty seconds ago the machine was still holding the value written 100 s
    # ago — the 2.0 was not written until 50 s ago.
    assert A.hold_forward(s, now - 70 * NS) == 1.0
    assert A.hold_forward(s, now - 30 * NS) == 2.0
    assert A.hold_forward(s, now) == 3.0
    assert A.hold_forward([], now) is None


def test_hold_forward_before_the_first_sample_uses_the_first():
    now = A.now_ns()
    s = series((10, 5.0))
    assert A.hold_forward(s, now - 100 * NS) == 5.0


def test_difference_holds_the_setpoint_forward():
    """The chiller case: Temp every second, TempSP once a week."""
    temp = series((3, 12.4), (2, 12.5), (1, 12.6))
    setpoint = series((7 * 86400, 12.0))
    diff = A.difference_series(temp, setpoint)
    assert len(diff) == 3
    assert [round(v, 2) for _t, v in diff] == [0.4, 0.5, 0.6]


def test_difference_without_a_subtrahend_is_empty():
    assert A.difference_series(series((1, 1.0)), []) == []


# ── the widening look-back ──────────────────────────────────────────────────

class _FakeFetch:
    """Stands in for fetch_samples and counts what was asked for."""

    def __init__(self, answers_at_span_s=None, samples=None):
        self.answers_at = answers_at_span_s
        self.samples = samples if samples is not None else [(123, 4.5)]
        self.calls = []

    def __call__(self, channel, start_ns, end_ns, *, timeout=None):
        span_s = round((end_ns - start_ns) / NS)
        self.calls.append((channel, span_s))
        if self.answers_at is not None and span_s >= self.answers_at:
            return list(self.samples)
        return []


def _with_fake(fake, fn):
    real = A.fetch_samples
    A.fetch_samples = fake
    try:
        return fn()
    finally:
        A.fetch_samples = real


def test_last_reader_stops_at_the_first_window_that_answers():
    fake = _FakeFetch(answers_at_span_s=3600)
    r = A.LastReader()
    value, t = _with_fake(fake, lambda: r.read("PV:X"))
    assert (value, t) == (4.5, 123)
    assert len(fake.calls) == 1, "the hour answered; nothing wider was asked"


def test_last_reader_widens_until_something_answers():
    fake = _FakeFetch(answers_at_span_s=86400)
    r = A.LastReader()
    value, _ = _with_fake(fake, lambda: r.read("PV:X"))
    assert value == 4.5
    spans = [s for _c, s in fake.calls]
    assert spans == [3600, 6 * 3600, 86400]


def test_a_dead_channel_costs_the_full_walk_once_and_then_one_request():
    """Without this memory, a channel that is not archived at all costs a
    request for every window on every pass, twice a second, for ever."""
    fake = _FakeFetch(answers_at_span_s=None)
    r = A.LastReader()
    assert _with_fake(fake, lambda: r.read("PV:DEAD")) == (None, None)
    first = len(fake.calls)
    assert first == len(A.LastReader.STEPS_S)
    fake.calls.clear()
    assert _with_fake(fake, lambda: r.read("PV:DEAD")) == (None, None)
    assert len(fake.calls) == 1, "only the widest window is tried again"


def test_the_memory_only_ever_widens_and_heals():
    fake = _FakeFetch(answers_at_span_s=None)
    r = A.LastReader()
    _with_fake(fake, lambda: r.read("PV:X"))
    fake.answers_at = 30 * 86400            # the channel starts answering again
    fake.calls.clear()
    value, _ = _with_fake(fake, lambda: r.read("PV:X"))
    assert value == 4.5
    assert len(fake.calls) == 1


def test_each_channel_keeps_its_own_memory():
    fake = _FakeFetch(answers_at_span_s=None)
    r = A.LastReader()
    _with_fake(fake, lambda: r.read("PV:DEAD"))
    fake.answers_at = 3600
    fake.calls.clear()
    _with_fake(fake, lambda: r.read("PV:ALIVE"))
    assert [s for _c, s in fake.calls] == [3600], \
        "a live channel must not inherit a dead one's widened window"


# ── one reading ─────────────────────────────────────────────────────────────

def test_read_value_peak():
    item = C.new_value_item(1, "x", "PV:X", hi_hi=5, read="peak", window_s=10)
    fake = _FakeFetch(answers_at_span_s=0, samples=series((5, 9.0), (1, 1.0)))
    r = _with_fake(fake, lambda: A.read_value(item))
    assert r.ok and r.value == 9.0 and r.samples == 2


def test_read_value_mean():
    item = C.new_value_item(1, "x", "PV:X", read="mean")
    fake = _FakeFetch(answers_at_span_s=0, samples=series((2, 1.0), (1, 3.0)))
    r = _with_fake(fake, lambda: A.read_value(item))
    assert r.value == 2.0


def test_read_value_of_a_difference_pair():
    item = C.new_value_item(1, "chiller", "CH:Temp", minus="CH:TempSP",
                            read="mean")

    def fake(channel, start_ns, end_ns, *, timeout=None):
        if channel == "CH:Temp":
            return series((2, 12.5), (1, 12.5))
        return series((7 * 86400, 12.0))

    r = _with_fake(fake, lambda: A.read_value(item))
    assert round(r.value, 3) == 0.5


def test_a_setpoint_that_is_silent_is_still_found():
    """The 60 s fetch window holds no setpoint at all — the normal case."""
    item = C.new_value_item(1, "chiller", "CH:Temp", minus="CH:TempSP",
                            read="mean")
    calls = []

    def fake(channel, start_ns, end_ns, *, timeout=None):
        span_s = round((end_ns - start_ns) / NS)
        calls.append((channel, span_s))
        if channel == "CH:Temp":
            return series((1, 12.6))
        return series((7 * 86400, 12.0)) if span_s >= 7 * 86400 else []

    r = _with_fake(fake, lambda: A.read_value(item))
    assert round(r.value, 3) == 0.6
    assert any(c == "CH:TempSP" and s >= 7 * 86400 for c, s in calls), \
        "the setpoint has to be hunted for with a widening window"


def test_an_empty_window_is_not_an_error():
    item = C.new_value_item(1, "x", "PV:X")
    fake = _FakeFetch(answers_at_span_s=None)
    r = _with_fake(fake, lambda: A.read_value(item))
    assert r.ok, "the archiver holding nothing is a successful answer"
    assert r.value is None and r.samples == 0
    # ...and the verdict says which of the two it was.
    assert C.judge_value(item, r.value, error=r.error, samples=r.samples)[0] \
        == C.STATE_UNKNOWN


def test_a_failed_read_is_an_error_and_says_why():
    item = C.new_value_item(1, "x", "PV:X")

    def boom(*a, **k):
        raise A.CpvaError("HTTP 500")

    r = _with_fake(boom, lambda: A.read_value(item, readable_error=C.readable_pv_error))
    assert not r.ok
    assert "PV:X" in r.error and "500" in r.error
    assert r.hint and "temporary" in r.hint


def test_a_busy_pool_never_blames_the_channel():
    item = C.new_value_item(1, "x", "PV:X")

    def busy(*a, **k):
        raise A.CpvaBusyError("every connection to the archiver was already in use")

    r = _with_fake(busy, lambda: A.read_value(item, readable_error=C.readable_pv_error))
    assert not r.ok and "too busy" in r.error
    assert "Nothing is wrong with the channel" in (r.hint or "")


def test_no_channel_set():
    item = C.new_value_item(1, "x", "")
    assert A.read_value(item).error == "no channel set"


# ── a whole pass ────────────────────────────────────────────────────────────

def test_a_pass_reads_everything():
    items = C.default_value_items()
    fake = _FakeFetch(answers_at_span_s=0, samples=series((1, 12.4)))
    reader = A.PassReader(workers=4)
    try:
        out = _with_fake(fake, lambda: reader.read_many(items))
    finally:
        reader.close()
    assert set(out) == {int(i["id"]) for i in items}
    assert all(r.ok for r in out.values())


def test_one_slow_channel_does_not_stretch_the_whole_pass():
    """The bound that stops a pass costing the sum of every timeout."""
    items = [C.new_value_item(1, "fast", "PV:FAST"),
             C.new_value_item(2, "slow", "PV:SLOW")]

    def fake(channel, start_ns, end_ns, *, timeout=None):
        if channel == "PV:SLOW":
            time.sleep(3.0)
        return series((1, 1.0))

    reader = A.PassReader(workers=4)
    started = time.monotonic()
    try:
        out = _with_fake(fake, lambda: reader.read_many(items, deadline_s=0.5))
    finally:
        reader.close()
    took = time.monotonic() - started
    assert took < 2.0, f"the pass took {took:.1f} s despite its deadline"
    assert out[1].ok
    assert not out[2].ok and "not read in time" in out[2].error


def test_a_read_that_raises_something_odd_does_not_kill_the_pass():
    items = [C.new_value_item(1, "a", "PV:A"), C.new_value_item(2, "b", "PV:B")]

    def fake(channel, start_ns, end_ns, *, timeout=None):
        if channel == "PV:A":
            raise MemoryError("something unexpected")
        return series((1, 1.0))

    reader = A.PassReader(workers=2)
    try:
        out = _with_fake(fake, lambda: reader.read_many(items))
    finally:
        reader.close()
    assert not out[1].ok and out[2].ok


def test_stats_line_is_one_line():
    line = A.stats_line()
    assert "\n" not in line and line.startswith("archiver ")


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            bad += 1
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
