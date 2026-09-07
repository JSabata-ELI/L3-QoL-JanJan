"""Taking back a plot that is still being fetched (/cancel).

A year-long /plot is hundreds of archiver requests. These tests check that
saying "never mind" actually stops the requests, that the chart renderer gives
up in the middle and says WHY it came back empty, and that a cancelled job
sends nothing to the chat.

No network and no window: the archiver call is replaced by a fake.

Run with:  python testing/test_cancel_plot.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_api as api  # noqa: E402

HOUR = int(3600 * 1e9)


class FakeArchiver:
    """One sample per minute, and a note of every range it was asked for."""

    def __init__(self, on_call=None):
        self.calls = []
        self._on_call = on_call
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout=None):
        with self._lock:
            self.calls.append((channel, start_ns, end_ns))
            n = len(self.calls)
        if self._on_call:
            self._on_call(n)
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


# --- the fetch stops --------------------------------------------------------

def test_cancel_skips_the_chunks_not_yet_fetched():
    """A day asked for, cancelled after the third hour: not 24 requests."""
    token = {"stop": False}
    fake = FakeArchiver(on_call=lambda n: token.__setitem__("stop", n >= 3))
    out = with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
        "PV", 0, 24 * HOUR, timeout=1.0, max_workers=1,
        cancel_fn=lambda: token["stop"]))
    assert len(fake.calls) < 10, len(fake.calls)
    # A cancelled fetch answers with nothing: a third of a window would be
    # plotted as if it were the whole of it.
    assert out == [], len(out)


def test_without_cancel_everything_is_fetched():
    fake = FakeArchiver()
    out = with_fake(fake, lambda: api.cpva_fetch_samples_chunked(
        "PV", 0, 24 * HOUR, timeout=1.0, max_workers=1))
    assert len(fake.calls) == 24, len(fake.calls)
    assert len(out) == 24 * 60, len(out)


# --- the picture is not drawn ----------------------------------------------

def test_render_gives_up_and_says_it_was_cancelled():
    import monitor_tab as mt

    token = mt._CancelToken("PV — last year")
    fake = FakeArchiver(on_call=lambda n: token.cancel() if n >= 2 else None)
    series = [mt.ChartSeries("PV", "PV", None, None, None)]
    info = {}
    png = with_fake(fake, lambda: mt.render_chart_png(
        series, 0, 24 * HOUR, 1.0, "last day", None, 0.0, info, token))
    assert png is None
    # "No data in that window" and "you took it back" are different answers.
    assert info.get("cancelled") is True, info


def test_cancelled_job_sends_nothing():
    import monitor_tab as mt

    class FakeHub:
        def __init__(self):
            self.sent = 0

        def dispatch_chart(self, *a, **kw):
            self.sent += 1
            return {}

    hub = FakeHub()
    token = mt._CancelToken("PV — last year")
    token.cancel()
    emitted = []
    fake = FakeArchiver()

    class FakeSig:
        class done:
            @staticmethod
            def emit(payload):
                emitted.append(payload)

    worker = mt._ChartWorker(
        FakeSig(), hub, [mt.ChartSeries("PV", "PV", None, None, None)],
        0, 24 * HOUR, 1.0, "Plot", "last day", "body", None, 0.0, token)
    with_fake(fake, worker.run)
    assert hub.sent == 0, hub.sent
    assert emitted and emitted[0][3] is True, emitted


def test_token_reads_as_its_own_cancel_fn():
    import monitor_tab as mt

    token = mt._CancelToken("x")
    assert token() is False
    token.cancel()
    assert token() is True and token.cancelled is True


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
