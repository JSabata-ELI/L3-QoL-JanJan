"""_envelope: the reduction that stopped the graph from hiding short dips.

Runs against fixture_chiller_dips.json — seven hours of the real Utility
Chiller from 23 Sep 2026, captured by capture_chiller_dips.py. It holds three
one-reading drops to 10.0 degC against a 20.0 baseline, which is the case that
prompted all of this.

The control test at the bottom is the point of the file: it feeds the same
data through the thinning this replaced and shows the dips are simply absent.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor_tab as mt                                          # noqa: E402

FIXTURE = Path(__file__).with_name("fixture_chiller_dips.json")
MINUTE_NS = 60 * 10 ** 9


@pytest.fixture(scope="module")
def chiller():
    if not FIXTURE.exists():
        pytest.skip(f"{FIXTURE.name} missing — run capture_chiller_dips.py")
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return (np.asarray(d["t_ns"], dtype=np.int64),
            np.asarray(d["values"], dtype=np.float64))


def _old_stride(t, v, max_points):
    """What _series did before 23 Sep 2026: keep every n-th reading."""
    n = t.size
    if n <= max_points:
        return t, v
    step = n / max_points
    idx = [int(i * step) for i in range(max_points)]
    idx[-1] = n - 1
    idx = np.asarray(idx)
    return t[idx], v[idx]


def _events(times, gap_ns=MINUTE_NS):
    """How many separate clusters these timestamps fall into."""
    if times.size == 0:
        return 0
    return 1 + int(np.count_nonzero(np.diff(times) > gap_ns))


# --- the fixture is what we think it is ------------------------------------

def test_the_fixture_holds_three_one_reading_dips(chiller):
    t, v = chiller
    assert t.size > 20_000
    dips = t[v == 10.0]
    assert dips.size == 4, "three events, one of them two readings in a second"
    assert _events(dips) == 3


# --- what the envelope guarantees ------------------------------------------

def test_it_stays_inside_the_point_budget(chiller):
    t, v = chiller
    et, ev = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    assert ev.size <= mt.MAX_GRAPH_POINTS + 1
    assert et.size == ev.size


def test_it_keeps_every_dip(chiller):
    t, v = chiller
    et, ev = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    assert float(ev.min()) == 10.0
    assert _events(et[ev == 10.0]) == 3


def test_each_dip_is_drawn_near_the_time_it_happened(chiller):
    t, v = chiller
    et, ev = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    drawn = et[ev == 10.0]
    for real in t[v == 10.0]:
        assert np.min(np.abs(drawn - real)) < MINUTE_NS


def test_it_keeps_the_high_excursions_too(chiller):
    """The same chiller also throws two single readings at 29.9."""
    t, v = chiller
    et, ev = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    assert float(ev.max()) == float(v.max())


def test_the_times_come_back_in_order(chiller):
    t, v = chiller
    et, _ = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    assert bool(np.all(np.diff(et) >= 0)), "np.searchsorted depends on this"


def test_it_ends_on_the_newest_reading(chiller):
    t, v = chiller
    et, ev = mt._envelope(t, v, mt.MAX_GRAPH_POINTS)
    assert et[-1] == t[-1] and ev[-1] == v[-1], \
        "the graph's right edge is taken from the last point"


def test_a_short_series_passes_through_untouched(chiller):
    t, v = chiller
    et, ev = mt._envelope(t[:10], v[:10], 3000)
    assert et.tolist() == t[:10].tolist()
    assert ev.tolist() == v[:10].tolist()


def test_a_tiny_budget_still_holds_the_contract(chiller):
    t, v = chiller
    for budget in (2, 3, 7, 64, 999):
        et, ev = mt._envelope(t, v, budget)
        assert ev.size <= budget + 1
        assert bool(np.all(np.diff(et) >= 0))
        assert et[-1] == t[-1]


def test_a_flat_series_is_reduced_without_inventing_anything():
    t = np.arange(10_000, dtype=np.int64) * 10 ** 9
    v = np.full(10_000, 20.0)
    et, ev = mt._envelope(t, v, 100)
    assert set(ev.tolist()) == {20.0}
    assert bool(np.all(np.diff(et) >= 0))


# --- the control: why this exists ------------------------------------------

def test_the_thinning_this_replaced_loses_every_dip(chiller):
    t, v = chiller
    _st, sv = _old_stride(t, v, mt.MAX_GRAPH_POINTS)
    assert not np.any(sv == 10.0), \
        "if this ever passes, the old thinning was not the problem"
    assert float(sv.min()) > 15.0
