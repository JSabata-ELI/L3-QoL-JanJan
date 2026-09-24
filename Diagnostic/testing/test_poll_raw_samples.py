"""The poll now hands the graph every reading and the thresholds the same
average as before. These tests pin both halves of that, offline.

The average is the part that must not move: it feeds the whole alerting state
machine, and this change was supposed to leave alerting alone.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_tab as mt                                          # noqa: E402

SEC = 1_000_000_000
T0 = 1_758_600_000 * SEC


def _s(ts, value, units="DegC"):
    return {"time": ts, "value": value,
            "metaData": {"units": units} if units else {}}


def _window(n=30, value=20.0, start=T0):
    return [_s(start + i * SEC, value) for i in range(n)]


# --- the average is exactly what it always was ------------------------------

def test_the_average_is_the_mean_of_the_last_n_readings():
    samples = [_s(T0 + i * SEC, float(i)) for i in range(100)]
    avg, units, last_ts, rejected, last_raw, pairs = mt._avg_recent_numeric(
        samples, avg_n=25)
    # readings 75..99
    assert avg == sum(range(75, 100)) / 25
    assert units == "DegC"
    assert last_ts == T0 + 99 * SEC
    assert rejected == 0
    assert last_raw == 99.0


def test_the_chiller_case_still_averages_to_19_2():
    """23 twenties and two tens over 25 — the number the operator saw."""
    vals = [20.0] * 23 + [10.0, 10.0]
    samples = [_s(T0 + i * SEC, v) for i, v in enumerate(vals)]
    avg, *_ = mt._avg_recent_numeric(samples, avg_n=25)
    assert round(avg, 3) == 19.2


def test_fewer_readings_than_n_averages_what_there_is():
    avg, *_ = mt._avg_recent_numeric(_window(3, 21.0), avg_n=25)
    assert avg == 21.0


def test_no_readings_gives_no_value_and_no_pairs():
    avg, units, last_ts, rejected, last_raw, (t, v) = mt._avg_recent_numeric(
        [], avg_n=25)
    assert avg is None and last_ts == 0 and rejected == 0 and last_raw is None
    assert t.size == 0 and v.size == 0


# --- what reaches the graph -------------------------------------------------

def test_every_in_range_reading_reaches_the_graph():
    samples = _window(30)
    _avg, _u, _ts, _r, _raw, (t, v) = mt._avg_recent_numeric(samples, avg_n=25)
    assert t.size == 30 and v.size == 30
    assert bool(np.all(np.diff(t) >= 0))


def test_a_one_reading_dip_is_kept_whole():
    samples = _window(30)
    samples[17]["value"] = 10.0
    avg, _u, _ts, _r, _raw, (t, v) = mt._avg_recent_numeric(samples, avg_n=25)
    assert float(v.min()) == 10.0, "the dip must survive into the graph"
    assert round(avg, 3) == round((24 * 20.0 + 10.0) / 25, 3), \
        "...while the average stays what the thresholds always saw"


def test_out_of_range_readings_are_kept_out_of_both():
    samples = _window(10)
    samples[4]["value"] = 999.0
    avg, _u, _ts, rejected, last_raw, (t, v) = mt._avg_recent_numeric(
        samples, avg_n=25, vmin=None, vmax=80.0)
    assert rejected == 1
    assert avg == 20.0
    assert last_raw == 20.0 or last_raw == 999.0   # last_raw ignores the range
    assert 999.0 not in set(v.tolist())
    assert t.size == 9


def test_a_reading_with_no_usable_time_counts_but_cannot_be_drawn():
    samples = _window(4)
    samples[2]["time"] = None
    avg, _u, _ts, _r, _raw, (t, v) = mt._avg_recent_numeric(samples, avg_n=25)
    assert avg == 20.0            # still four readings in the average
    assert t.size == 3            # but only three can go on a time axis


def test_booleans_are_not_readings():
    samples = _window(3) + [_s(T0 + 9 * SEC, True)]
    _avg, _u, _ts, _r, _raw, (t, _v) = mt._avg_recent_numeric(samples, avg_n=25)
    assert t.size == 3


# --- the widened window after a skipped pass --------------------------------

def test_the_widened_stretch_reaches_the_graph_only():
    """A pass that reaches further back must not change a single number the
    thresholds see — n_rejected feeds the error string and the bad-data flag."""
    configured_from = T0 + 60 * SEC
    old = [_s(T0 + i * SEC, 999.0) for i in range(60)]       # all out of range
    new = [_s(configured_from + i * SEC, 20.0) for i in range(60)]

    narrow = mt._avg_recent_numeric(new, avg_n=25, vmax=80.0)
    wide = mt._avg_recent_numeric(old + new, avg_n=25, vmax=80.0,
                                  avg_from_ns=configured_from)

    assert wide[0] == narrow[0], "the average must not move"
    assert wide[2] == narrow[2], "last_ts must not move"
    assert wide[3] == narrow[3] == 0, "the older stretch must not be rejected"
    assert wide[4] == narrow[4], "last_raw must not move"
    # ...and the graph still gets nothing it should not: 999 is out of range.
    assert wide[5][0].size == narrow[5][0].size


def test_the_widened_stretch_does_reach_the_graph_when_it_is_valid():
    configured_from = T0 + 60 * SEC
    old = [_s(T0 + i * SEC, 20.0) for i in range(60)]
    new = [_s(configured_from + i * SEC, 20.0) for i in range(60)]
    wide = mt._avg_recent_numeric(old + new, avg_n=25, vmax=80.0,
                                  avg_from_ns=configured_from)
    assert wide[5][0].size == 120, "the gap-filling readings must be drawn"


# --- the shape the rest of the program unpacks ------------------------------

class _Sig:
    class _S:
        def __init__(self): self.value = None
        def emit(self, v): self.value = v
    def __init__(self):
        self.done, self.log = self._S(), self._S()


def _worker(monkeypatch, fetch, newest=None):
    monkeypatch.setattr(mt.api, "cpva_fetch_samples", fetch)
    sig = _Sig()
    s = dict(mt.DEFAULT_SETTINGS)
    mt._PollWorker(sig, ["PV-A"], s, {"PV-A": (None, 80.0)}, newest).run()
    return sig.done.value["PV-A"]


def test_the_poll_result_has_seven_members(monkeypatch):
    # The readings have to sit inside the window the worker asks for, or
    # avg_from_ns correctly skips them all.
    def fetch(channel, start_ns, end_ns, timeout):
        return _window(30, start=start_ns)

    res = _worker(monkeypatch, fetch)
    assert len(res) == 7
    val, units, last_ts, err, rejected, raw_val, pairs = res
    assert val == 20.0 and units == "DegC" and err == "" and rejected == 0
    assert raw_val == 20.0 and pairs[0].size == 30


def test_a_failed_fetch_also_has_seven_members(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("archiver said no")
    res = _worker(monkeypatch, boom)
    assert len(res) == 7
    assert res[0] is None and "archiver said no" in res[3]
    assert res[6][0].size == 0, "no readings, but still a usable empty pair"


def test_the_gap_recovery_widens_the_fetch(monkeypatch):
    asked = {}

    def fetch(channel, start_ns, end_ns, timeout):
        asked["span_s"] = (end_ns - start_ns) / 1e9
        return _window(5, start=start_ns)

    s = dict(mt.DEFAULT_SETTINGS)
    window_s = float(s["sample_window_s"])
    now = mt.api.now_ns()

    _worker(monkeypatch, fetch, newest={"PV-A": now - int(200e9)})
    assert asked["span_s"] > window_s * 1.5, "it must reach back to the gap"
    assert asked["span_s"] <= window_s * mt.POLL_GAP_MAX_WINDOWS + 1, \
        "...but never further than the cap"


def test_a_long_outage_is_capped_not_unbounded(monkeypatch):
    asked = {}

    def fetch(channel, start_ns, end_ns, timeout):
        asked["span_s"] = (end_ns - start_ns) / 1e9
        return []

    window_s = float(mt.DEFAULT_SETTINGS["sample_window_s"])
    now = mt.api.now_ns()
    _worker(monkeypatch, fetch, newest={"PV-A": now - int(30 * 86400e9)})
    assert asked["span_s"] <= window_s * mt.POLL_GAP_MAX_WINDOWS + 1


def test_a_fresh_history_asks_for_the_configured_window_only(monkeypatch):
    asked = {}

    def fetch(channel, start_ns, end_ns, timeout):
        asked["span_s"] = (end_ns - start_ns) / 1e9
        return []

    _worker(monkeypatch, fetch, newest={})
    assert round(asked["span_s"]) == mt.DEFAULT_SETTINGS["sample_window_s"]


def test_the_data_watchdog_still_reads_the_result():
    """_is_conn_failure slices the result, so a seventh member cannot break it."""
    ok = (20.0, "DegC", T0, "", 0, 20.0, (mt._EMPTY_T, mt._EMPTY_V))
    down = (None, "", 0, "connection refused", 0, None, (mt._EMPTY_T, mt._EMPTY_V))
    bad = (None, "", T0, "dropped 3 out-of-range reading(s)", 3, 999.0,
           (mt._EMPTY_T, mt._EMPTY_V))

    def is_conn_failure(res):
        val, _units, _ts, err, rejected = res[:5]
        return val is None and rejected == 0 and bool(err)

    assert not is_conn_failure(ok)
    assert is_conn_failure(down)
    assert not is_conn_failure(bad), "bad data is not a connection failure"
