"""_SampleHistory: the buffer that lets the graph keep every archived reading.

The invariants here are load-bearing elsewhere — np.searchsorted reads the
times, and the graph's right edge is the last element — so they are pinned one
by one rather than through the graph.
"""
import sys
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_tab as mt                                          # noqa: E402

SEC = 1_000_000_000
HOUR = 3600 * SEC


def _h(span=HOUR, cap=100_000):
    return mt._SampleHistory(span_ns=span, cap=cap)


def _ramp(n, start=0, value=20.0):
    return (np.arange(start, start + n, dtype=np.int64) * SEC,
            np.full(n, value, dtype=np.float64))


# --- the deque surface the rest of the program still uses ------------------

def test_an_empty_history_is_falsy_and_empty():
    h = _h()
    assert not h and len(h) == 0
    assert h.newest_ns == 0 and h.oldest_ns == 0


def test_it_indexes_and_iterates_like_the_deque_it_replaced():
    h = _h()
    h.append_samples(*_ramp(3))
    assert h[0] == (0, 20.0)
    assert h[-1] == (2 * SEC, 20.0)
    assert list(h) == [(0, 20.0), (SEC, 20.0), (2 * SEC, 20.0)]
    assert len(h) == 3 and bool(h)


def test_a_single_append_still_works():
    h = _h()
    h.append((5 * SEC, 1.5))
    assert list(h) == [(5 * SEC, 1.5)]


def test_arrays_are_views_not_copies():
    h = _h()
    h.append_samples(*_ramp(10))
    t, v = h.arrays()
    assert t.base is h._t and v.base is h._v


# --- de-duplication: the 60 s window / 30 s poll overlap -------------------

def test_the_overlap_between_two_passes_is_not_stored_twice():
    h = _h()
    h.append_samples(*_ramp(60))
    added = h.append_samples(*_ramp(60, start=30))      # 50 % overlap
    assert added == 30 and len(h) == 90
    t, _ = h.arrays()
    assert np.unique(t).size == len(h)


def test_a_pass_that_brings_nothing_new_adds_nothing():
    h = _h()
    h.append_samples(*_ramp(60))
    assert h.append_samples(*_ramp(60)) == 0
    assert len(h) == 60


def test_an_out_of_order_batch_comes_back_in_order():
    h = _h()
    h.append_samples(np.array([5 * SEC, SEC, 3 * SEC]),
                     np.array([3.0, 1.0, 2.0]))
    assert [p[0] for p in h] == [SEC, 3 * SEC, 5 * SEC]
    assert [p[1] for p in h] == [1.0, 2.0, 3.0]


def test_an_empty_batch_is_harmless():
    h = _h()
    assert h.append_samples(mt._EMPTY_T, mt._EMPTY_V) == 0


# --- the two bounds ---------------------------------------------------------

def test_readings_older_than_the_retention_span_are_dropped():
    h = _h(span=10 * SEC)
    h.append_samples(*_ramp(100))
    assert h.newest_ns == 99 * SEC
    assert h.oldest_ns >= 89 * SEC
    assert len(h) <= 12


def test_the_per_pv_cap_drops_the_oldest_and_keeps_the_newest():
    h = _h(span=10 ** 18, cap=64)
    t, v = np.arange(500, dtype=np.int64) * SEC, np.arange(500.0)
    h.append_samples(t, v)
    assert len(h) <= 64
    assert h[-1] == (499 * SEC, 499.0), "the newest reading is never the one cut"
    assert h.dropped_old > 0, "and the program knows it had to cut"


def test_lowering_the_setting_prunes_and_frees_memory():
    h = _h(span=10 ** 18)
    h.append_samples(*_ramp(10_000))
    big = h.nbytes
    h.set_retention(10 * SEC, 100_000)
    assert len(h) <= 12
    assert h.nbytes < big


def test_raising_the_setting_keeps_what_is_there():
    h = _h(span=10 * SEC)
    h.append_samples(*_ramp(100))
    kept = list(h)
    h.set_retention(HOUR, 100_000)
    assert list(h) == kept


# --- memory shape -----------------------------------------------------------

def test_it_starts_small_rather_than_allocating_the_whole_cap():
    h = _h(span=10 ** 18, cap=400_000)
    assert h.nbytes <= 4096 * 16, "31 PVs must not cost 100 MB at launch"


def test_it_grows_by_doubling():
    h = _h(span=10 ** 18, cap=400_000)
    h.append_samples(*_ramp(5000))
    assert 5000 * 16 <= h.nbytes <= 8192 * 16


def test_twelve_hours_of_the_real_rate_fits_the_measured_budget():
    """45.4 readings/s across 31 PVs, measured 23 Sep 2026."""
    h = _h(span=12 * HOUR, cap=mt.HISTORY_HARD_CAP)
    n = int(12 * 3600 * 1.9)            # the fastest PV measured
    h.append_samples(*_ramp(n))
    assert h.nbytes < 3 * 1024 * 1024, f"{h.nbytes / 1e6:.1f} MB for one PV"


def test_the_version_counter_moves_on_every_change():
    h = _h()
    v0 = h.version
    h.append((SEC, 1.0))
    assert h.version > v0
    v1 = h.version
    h.append_samples(*_ramp(5, start=10))
    assert h.version > v1


# --- prepend: the backfill path --------------------------------------------

def test_prepend_puts_older_readings_in_front():
    h = _h(span=10 ** 18)
    h.append_samples(*_ramp(10, start=100))
    h.prepend_samples(*_ramp(100, start=0, value=1.0))
    t, _ = h.arrays()
    assert bool(np.all(np.diff(t) >= 0))
    assert h.oldest_ns == 0 and h.newest_ns == 109 * SEC


def test_prepend_never_sacrifices_the_live_tail():
    h = _h(span=10 ** 18, cap=60)
    h.append_samples(*_ramp(50, start=500, value=2.0))
    tail = list(h)
    h.prepend_samples(*_ramp(500, start=0, value=1.0))
    assert list(h)[-50:] == tail, "the freshest readings must never be cut"
    assert len(h) <= 60


def test_prepend_ignores_readings_already_held():
    h = _h(span=10 ** 18)
    h.append_samples(*_ramp(50, start=50))
    assert h.prepend_samples(*_ramp(50, start=50)) == 0


def test_prepend_respects_the_retention_span():
    h = _h(span=10 * SEC)
    h.append_samples(*_ramp(5, start=100))
    h.prepend_samples(*_ramp(100, start=0, value=1.0))
    assert h.oldest_ns >= 94 * SEC


# --- _history_arrays, for the tests that still hand in a deque -------------

def test_a_plain_deque_still_works():
    d = deque([(SEC, 1.0), (2 * SEC, 2.0)])
    t, v = mt._history_arrays(d)
    assert t.tolist() == [SEC, 2 * SEC] and v.tolist() == [1.0, 2.0]


def test_a_newest_first_deque_comes_back_oldest_first():
    """Two of the existing test fixtures build their history descending."""
    now, minute = 1_700_000_000 * SEC, 60 * SEC
    d = deque([(now - i * minute, 16.0 + i) for i in range(5)])
    t, v = mt._history_arrays(d)
    assert bool(np.all(np.diff(t) >= 0))
    assert v[0] == 20.0 and v[-1] == 16.0


def test_an_empty_source_gives_empty_arrays():
    for src in (deque(), _h()):
        t, v = mt._history_arrays(src)
        assert t.size == 0 and v.size == 0


def test_retune_leaves_a_deque_usable():
    d = deque([(SEC, 1.0)], maxlen=10)
    out = mt._history_retune(d, HOUR, 10)
    assert list(out) == [(SEC, 1.0)]


def test_since_returns_only_the_recent_readings():
    h = _h(span=10 ** 18)
    h.append_samples(*_ramp(100))
    recent = mt._history_since(h, 90 * SEC)
    assert len(recent) == 10
    assert recent[0][0] == 90 * SEC
