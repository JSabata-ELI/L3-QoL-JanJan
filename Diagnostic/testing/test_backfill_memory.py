"""_BackfillWorker must never hold the archive it reads.

The old shape collected every chunk of every PV into Python lists and reduced
only at the end. For the twelve hours the graph now keeps, that is ~2 M
readings across 31 PVs — the shape that has taken this PC down before. The
rewrite reduces each answer in the thread that received it and lets it go.

That promise is checked here with a stand-in archiver whose answers are
counted and weak-referenced; no network.
"""
import gc
import sys
import threading
import weakref
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chart_history                                              # noqa: E402
import cpva_api as api                                            # noqa: E402
import monitor_tab as mt                                          # noqa: E402

SEC = 1_000_000_000
END = 1_758_600_000 * SEC
RATE_HZ = 1.9                      # the fastest PV measured 23 Sep 2026


class _Answer(list):
    """A plain list cannot be weak-referenced, and whether each answer is
    released is exactly what these tests are about."""


class _Sig:
    class _S:
        def __init__(self):
            self.value = None
            self.lines = []

        def emit(self, v):
            self.value = v
            self.lines.append(v)

    def __init__(self):
        self.done, self.log = self._S(), self._S()


class _Archive:
    """A stand-in CPVA that counts what it served and remembers each answer."""

    def __init__(self, dip_at=None, dip_channel=None, rate_hz=RATE_HZ):
        self.rate_hz = rate_hz
        self.requests = 0
        self.readings = 0
        self.answers = []
        self._dip_at = dip_at
        self._dip_channel = dip_channel
        self._lock = threading.Lock()

    def __call__(self, channel, start_ns, end_ns, timeout):
        n = max(1, int((end_ns - start_ns) / SEC * self.rate_hz))
        out = _Answer({"time": start_ns + i * SEC, "value": 20.0,
                       "metaData": {"units": "DegC"}} for i in range(n))
        if (self._dip_at is not None and channel == self._dip_channel
                and start_ns <= self._dip_at < end_ns):
            out[n // 2]["value"] = 10.0
        with self._lock:
            self.requests += 1
            self.readings += n
            self.answers.append(weakref.ref(out))
        return out


def _run(monkeypatch, names, start, end, archive, max_points=216_000,
         workers=8, ranges=None):
    monkeypatch.setattr(api, "cpva_fetch_samples", archive)
    sig = _Sig()
    mt._BackfillWorker(sig, names, start, end, 10.0,
                       ranges if ranges is not None
                       else {n: (None, 80.0) for n in names},
                       max_points=max_points, workers=workers).run()
    return sig


@pytest.fixture(scope="module")
def twelve_hours():
    """The real shape: 31 PVs, twelve hours, ~2.5 M readings.

    Module-scoped and patched by hand — this is the one expensive fixture in
    the file, and building it once per assertion cost a minute of the suite.
    """
    names = [f"FAKE-PV-{i:02d}" for i in range(31)]
    start = END - 12 * 3600 * SEC
    archive = _Archive(dip_at=start + 6 * 3600 * SEC, dip_channel=names[0])
    real = api.cpva_fetch_samples
    api.cpva_fetch_samples = archive
    try:
        sig = _Sig()
        mt._BackfillWorker(sig, names, start, END, 10.0,
                           {n: (None, 80.0) for n in names},
                           max_points=216_000, workers=8).run()
    finally:
        api.cpva_fetch_samples = real
    return names, archive, sig


def test_it_reads_the_whole_window(twelve_hours):
    names, archive, sig = twelve_hours
    assert set(sig.done.value) == set(names)
    assert archive.readings > 2_000_000, "the fixture must be the real size"


def test_no_answer_is_still_alive_afterwards(twelve_hours):
    _names, archive, _sig = twelve_hours
    gc.collect()
    alive = [r for r in archive.answers if r() is not None]
    assert alive == [], (
        f"{len(alive)} of {len(archive.answers)} archive answers were kept — "
        f"this is the accumulation the rewrite exists to remove")


def test_what_it_keeps_is_a_small_fraction_of_what_it_read(twelve_hours):
    _names, archive, sig = twelve_hours
    held = sum(t.nbytes + v.nbytes for t, v in sig.done.value.values())
    raw = archive.readings * 345          # measured bytes per reading of JSON
    assert held < raw / 10
    assert held < 64 * 1024 * 1024


def test_nothing_exceeds_the_per_pv_cap(twelve_hours):
    _names, _archive, sig = twelve_hours
    assert all(len(t) <= 216_000 for t, _ in sig.done.value.values())


def test_every_pv_comes_back_in_time_order(twelve_hours):
    _names, _archive, sig = twelve_hours
    for t, _v in sig.done.value.values():
        assert bool(np.all(np.diff(t) >= 0))


def test_a_one_reading_dip_survives_the_backfill(twelve_hours):
    names, _archive, sig = twelve_hours
    assert float(sig.done.value[names[0]][1].min()) == 10.0


def test_the_plan_is_announced(twelve_hours):
    _names, _archive, sig = twelve_hours
    assert any("requests" in ln for ln in sig.log.lines)


# --- the awkward asks -------------------------------------------------------

def test_an_oversized_ask_is_sampled_and_says_so(monkeypatch):
    # The full 31 PVs, because sampling only starts once the request count
    # passes the budget — but a slow stand-in PV, because what is checked here
    # is the wording and the release, not the volume. The volume case is
    # `twelve_hours` above.
    names = [f"FAKE-PV-{i:02d}" for i in range(31)]
    archive = _Archive(rate_hz=0.05)
    sig = _run(monkeypatch, names, END - 3 * 86400 * SEC, END, archive)
    said = " ".join(sig.log.lines)
    assert "spread across the whole range" in said
    assert "take a few minutes" in said, "the wait is the honest warning"
    gc.collect()
    assert [r for r in archive.answers if r() is not None] == []


def test_a_refused_plan_still_reports_back(monkeypatch):
    """PlotTooBig must not leave the caller's in-flight flag stuck True."""
    def refuse(*a, **k):
        raise chart_history.PlotTooBig("far too much")

    monkeypatch.setattr(chart_history, "plan_tasks", refuse)
    sig = _run(monkeypatch, ["FAKE-PV-00"], END - 86400 * SEC, END, _Archive())
    assert sig.done.value == {}, "a result must always be emitted"
    assert any("far too much" in ln for ln in sig.log.lines)


def test_a_memory_trip_keeps_what_was_read(monkeypatch):
    names = ["FAKE-PV-00"]
    start = END - 6 * 3600 * SEC

    class _TripAfter:
        """Trips once a couple of chunks have been absorbed."""
        def __init__(self):
            self.tripped = False
            self.reason = "pretend the machine is full"
            self._n = 0

        def __call__(self):
            self._n += 1
            if self._n > 3:
                self.tripped = True
            return self.tripped

    monkeypatch.setattr(chart_history, "_MemoryGuard",
                        lambda *a, **k: _TripAfter())
    sig = _run(monkeypatch, names, start, END, _Archive())
    t, v = sig.done.value.get(names[0], (mt._EMPTY_T, mt._EMPTY_V))
    assert t.size > 0, "a partial read is still worth drawing"
    assert bool(np.all(np.diff(t) >= 0))
    assert any("stopped" in ln for ln in sig.log.lines)


def test_an_unreadable_pv_does_not_cost_the_others(monkeypatch):
    names = ["GOOD", "BAD"]
    good = _Archive()

    def archive(channel, start_ns, end_ns, timeout):
        if channel == "BAD":
            raise RuntimeError("archiver said no")
        return good(channel, start_ns, end_ns, timeout)

    sig = _run(monkeypatch, names, END - 3600 * SEC, END, archive)
    assert "GOOD" in sig.done.value and len(sig.done.value["GOOD"][0])
    assert "BAD" not in sig.done.value
    assert any("could not be read" in ln for ln in sig.log.lines)


def test_out_of_range_readings_never_reach_the_history(monkeypatch):
    def archive(channel, start_ns, end_ns, timeout):
        n = 100
        out = _Answer({"time": start_ns + i * SEC, "value": 20.0}
                      for i in range(n))
        out[10]["value"] = 999.0
        return out

    sig = _run(monkeypatch, ["A"], END - 3600 * SEC, END, archive,
               ranges={"A": (None, 80.0)})
    _t, v = sig.done.value["A"]
    assert 999.0 not in set(v.tolist())


def test_an_empty_window_reports_back_cleanly(monkeypatch):
    sig = _run(monkeypatch, ["A"], END, END, _Archive())
    assert sig.done.value == {}
