"""Deciding how much to ask the archiver for, and in what order.

Pure arithmetic — nothing here talks to the network.

Run with:  python testing/test_chunk_plan.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402

NS = 1_000_000_000
HOUR = 3600 * NS
DAY = 24 * HOUR
START = int(1.8e18)


def plan(pvs, window_ns, rates, budget=ch.DEFAULT_BUDGET, detail=False):
    span = {pv: ch.plan_chunk_span(rates[pv]) for pv in pvs}
    return ch.plan_tasks(pvs, START, START + window_ns, span,
                         budget=budget, detail=detail)


# --- how long one request should cover --------------------------------------

def test_a_slower_channel_gets_a_longer_request():
    slow = ch.plan_chunk_span(360)          # one reading every 10 s
    fast = ch.plan_chunk_span(35_000)       # a shot-energy channel
    assert slow > fast


def test_the_span_is_clamped_at_both_ends():
    assert ch.plan_chunk_span(0.001) == api.MAX_CHUNK_SPAN_NS
    assert ch.plan_chunk_span(0) == api.MAX_CHUNK_SPAN_NS
    assert ch.plan_chunk_span(1e12) == api.SPLIT_MIN_SPAN_NS


def test_the_span_is_a_round_number():
    for rate in (10, 100, 360, 5_000, 35_000, 200_000):
        assert ch.plan_chunk_span(rate) in ch._NICE_SPANS_NS


def test_a_request_never_aims_above_the_target():
    for rate in (50, 360, 5_000, 35_000):
        span = ch.plan_chunk_span(rate)
        expected = rate * span / HOUR
        assert expected <= ch.TARGET_SAMPLES * 1.0001, (rate, expected)


def test_the_reported_case_costs_180_requests_not_4320():
    """A chiller temperature over 180 days."""
    tasks, mode, _ = plan(["Chiller"], 180 * DAY, {"Chiller": 360})
    assert mode == "full"
    assert len(tasks) == 180, len(tasks)


# --- full coverage ----------------------------------------------------------

def test_full_coverage_has_no_hole_and_no_overlap():
    tasks, mode, _ = plan(["PV"], 10 * DAY, {"PV": 360})
    assert mode == "full"
    bounds = sorted((t.start_ns, t.end_ns) for t in tasks)
    assert bounds[0][0] == START and bounds[-1][1] == START + 10 * DAY
    for (_, e), (s, _) in zip(bounds, bounds[1:]):
        assert s == e, (e, s)


def test_a_window_shorter_than_one_request_is_one_request():
    tasks, _, _ = plan(["PV"], 10 * 60 * NS, {"PV": 1})
    assert len(tasks) == 1
    assert tasks[0].start_ns == START and tasks[0].end_ns == START + 10 * 60 * NS


def test_the_newest_stretch_is_asked_for_first():
    tasks, _, _ = plan(["PV"], 10 * DAY, {"PV": 360})
    assert tasks[0].end_ns == START + 10 * DAY
    assert tasks[1].start_ns == START


def test_channels_are_interleaved_so_none_is_starved():
    tasks, _, _ = plan(["A", "B"], 10 * DAY, {"A": 360, "B": 360})
    assert {t.channel for t in tasks[:2]} == {"A", "B"}


# --- sampling ---------------------------------------------------------------

def test_a_fast_channel_over_months_switches_to_sampling():
    tasks, mode, planned = plan(["Fast"], 180 * DAY, {"Fast": 35_000})
    assert mode == "sampled"
    assert len(tasks) <= ch.DEFAULT_BUDGET
    covered = planned["Fast"] / (180 * DAY)
    assert 0.05 < covered < 0.5, covered


def test_sampled_stretches_are_spread_evenly_and_do_not_overlap():
    tasks, mode, _ = plan(["Fast"], 180 * DAY, {"Fast": 35_000})
    assert mode == "sampled"
    bounds = sorted((t.start_ns, t.end_ns) for t in tasks)
    gaps = [s2 - e1 for (_, e1), (s2, _) in zip(bounds, bounds[1:])]
    assert min(gaps) > 0, "sampled stretches overlap"
    assert max(gaps) - min(gaps) < HOUR, "stretches are not evenly spread"


def test_sampling_covers_the_end_of_the_window_too():
    tasks, _, _ = plan(["Fast"], 180 * DAY, {"Fast": 35_000})
    assert max(t.end_ns for t in tasks) > START + 179 * DAY


def test_detail_reads_everything_however_many_requests_that_is():
    tasks, mode, planned = plan(["Fast"], 30 * DAY, {"Fast": 35_000},
                                detail=True)
    assert mode == "detail"
    assert len(tasks) > ch.DEFAULT_BUDGET
    assert planned["Fast"] == 30 * DAY


def test_too_many_channels_over_too_long_is_refused_before_fetching():
    pvs = [f"PV{i}" for i in range(30)]
    try:
        plan(pvs, 180 * DAY, {pv: 35_000 for pv in pvs})
    except ch.PlotTooBig as e:
        assert "detail" in str(e) and "30 PVs" in str(e)
    else:
        raise AssertionError("a hopeless plot was accepted")


def test_an_empty_window_plans_nothing():
    tasks, _, _ = plan(["PV"], 0, {"PV": 360})
    assert tasks == []


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
