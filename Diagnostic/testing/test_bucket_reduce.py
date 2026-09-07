"""Condensing readings into per-point minimum / maximum / average.

Everything here is arithmetic on made-up readings — no network, no Qt.

Run with:  python testing/test_bucket_reduce.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402

NS = 1_000_000_000
HOUR = 3600 * NS
DAY = 24 * HOUR


def samples(pairs, units="degC"):
    return [{"time": int(t), "value": v, "metaData": {"units": units}}
            for t, v in pairs]


def reducer(n_bins=4, start=0, end=4 * HOUR, vmin=None, vmax=None,
            raw_keep=ch.RAW_KEEP):
    req = ch.SeriesRequest("PV", "PV", vmin, vmax)
    return ch._BinReducer(req, start, end, n_bins, raw_keep)


def task(start, end):
    return api.ChunkTask("PV", start, end, 0)


# --- the arithmetic ---------------------------------------------------------

def test_min_max_mean_and_count_per_bin():
    rd = reducer()
    rd.add_chunk(task(0, 4 * HOUR), samples([
        (0, 1.0), (HOUR // 2, 3.0),          # bin 0
        (HOUR, 10.0),                        # bin 1
        (3 * HOUR, -5.0), (3 * HOUR + 1, 5.0),   # bin 3
    ]))
    d = rd.finish()
    assert list(d.bin_count) == [2, 1, 0, 2]
    assert d.bin_mean[0] == 2.0 and d.bin_min[0] == 1.0 and d.bin_max[0] == 3.0
    assert d.bin_mean[1] == 10.0
    assert np.isnan(d.bin_mean[2])
    assert d.bin_min[3] == -5.0 and d.bin_max[3] == 5.0
    assert d.units == "degC"


def test_a_peak_survives_condensing():
    """The whole point: one spike among thousands must still be visible."""
    rd = reducer(n_bins=10, start=0, end=10 * HOUR)
    pairs = [(i * HOUR // 100, 20.0) for i in range(1000)]
    pairs[517] = (517 * HOUR // 100, 95.0)
    rd.add_chunk(task(0, 10 * HOUR), samples(pairs))
    d = rd.finish()
    assert d.bin_max.max() == 95.0
    assert round(float(d.bin_mean[5]), 1) == 20.8   # the average barely moves


def test_reading_time_is_the_average_not_the_bin_centre():
    rd = reducer(n_bins=2, start=0, end=2 * HOUR)
    rd.add_chunk(task(0, 2 * HOUR), samples([(60 * NS, 1.0), (120 * NS, 1.0)]))
    d = rd.finish()
    assert d.bin_t_ns[0] == 90 * NS


def test_merging_two_chunks_equals_reducing_their_union():
    pairs = [(i * 60 * NS, float(i % 7)) for i in range(200)]
    one = reducer(n_bins=8, start=0, end=4 * HOUR)
    one.add_chunk(task(0, 4 * HOUR), samples(pairs))
    two = reducer(n_bins=8, start=0, end=4 * HOUR)
    half = len(pairs) // 2
    split_t = pairs[half][0]
    two.add_chunk(task(split_t, 4 * HOUR), samples(pairs[half:]))
    two.add_chunk(task(0, split_t), samples(pairs[:half]))
    a, b = one.finish(), two.finish()
    assert np.array_equal(a.bin_count, b.bin_count)
    assert np.allclose(np.nan_to_num(a.bin_mean), np.nan_to_num(b.bin_mean))
    assert np.allclose(np.nan_to_num(a.bin_min), np.nan_to_num(b.bin_min))
    assert np.allclose(np.nan_to_num(a.bin_max), np.nan_to_num(b.bin_max))


def test_out_of_order_readings_give_the_same_answer():
    pairs = [(i * 60 * NS, float(i % 5)) for i in range(120)]
    ordered = reducer(n_bins=6, start=0, end=2 * HOUR)
    ordered.add_chunk(task(0, 2 * HOUR), samples(pairs))
    shuffled = reducer(n_bins=6, start=0, end=2 * HOUR)
    mixed = list(pairs)
    mixed.reverse()
    shuffled.add_chunk(task(0, 2 * HOUR), samples(mixed))
    a, b = ordered.finish(), shuffled.finish()
    assert np.array_equal(a.bin_count, b.bin_count)
    assert np.allclose(np.nan_to_num(a.bin_mean), np.nan_to_num(b.bin_mean))
    assert np.allclose(np.nan_to_num(a.bin_max), np.nan_to_num(b.bin_max))


def test_one_reading_still_produces_a_curve():
    rd = reducer(n_bins=4)
    rd.add_chunk(task(0, 4 * HOUR), samples([(HOUR, 7.0)]))
    d = rd.finish()
    assert d.has_data and d.bin_count.sum() == 1 and d.bin_mean[1] == 7.0


def test_everything_in_one_bin():
    rd = reducer(n_bins=900, start=0, end=900 * HOUR)
    rd.add_chunk(task(0, HOUR), samples([(i, float(i)) for i in range(50)]))
    d = rd.finish()
    assert d.bin_count[0] == 50 and d.bin_count.sum() == 50


# --- filtering --------------------------------------------------------------

def test_readings_outside_the_valid_range_are_dropped_and_counted():
    rd = reducer(n_bins=2, start=0, end=2 * HOUR, vmin=0.0, vmax=80.0)
    rd.add_chunk(task(0, 2 * HOUR), samples([
        (0, 20.0), (1, 1e38), (2, -5.0), (3, 30.0)]))
    d = rd.finish()
    assert d.n_samples == 2 and d.n_rejected == 2
    assert d.bin_max[0] == 30.0        # the 1e38 spike never reaches the band


def test_non_finite_readings_are_dropped():
    rd = reducer(n_bins=1, start=0, end=HOUR)
    rd.add_chunk(task(0, HOUR), samples([(0, 1.0), (1, float("nan")),
                                         (2, float("inf"))]))
    d = rd.finish()
    assert d.n_samples == 1 and d.n_rejected == 2


def test_a_switch_is_refused_rather_than_drawn_as_a_curve():
    rd = reducer(n_bins=2)
    rd.add_chunk(task(0, 2 * HOUR), samples([(0, True), (1, False)]))
    d = rd.finish()
    assert d.non_numeric and not d.has_data


def test_single_element_list_values_are_decoded():
    rd = reducer(n_bins=2)
    rd.add_chunk(task(0, 2 * HOUR), samples([(0, [4.0]), (1, [6.0])]))
    d = rd.finish()
    assert d.n_samples == 2 and d.bin_mean[0] == 5.0


def test_newest_reading_is_the_true_maximum_timestamp():
    rd = reducer(n_bins=4, start=0, end=4 * HOUR)
    rd.add_chunk(task(2 * HOUR, 4 * HOUR), samples([(3 * HOUR, 1.0)]))
    rd.add_chunk(task(0, 2 * HOUR), samples([(HOUR, 1.0)]))
    assert rd.finish().newest_ns == 3 * HOUR


# --- read, empty, unread ----------------------------------------------------

def test_an_empty_answer_still_counts_as_read():
    rd = reducer(n_bins=2, start=0, end=2 * HOUR)
    rd.add_chunk(task(0, HOUR), [])
    d = rd.finish()
    assert d.bin_read_ns[0] > 0 and d.bin_read_ns[1] == 0
    assert d.bin_count[0] == 0
    assert d.failed_ns == 0


def test_a_failed_stretch_is_not_marked_read():
    rd = reducer(n_bins=2, start=0, end=2 * HOUR)
    rd.mark_failed(task(0, HOUR), RuntimeError("HTTP 500"))
    rd.add_chunk(task(HOUR, 2 * HOUR), samples([(HOUR, 1.0)]))
    d = rd.finish()
    assert d.bin_read_ns[0] == 0 and d.bin_read_ns[1] > 0
    assert d.failed_ns == HOUR and d.n_chunks_failed == 1
    assert d.errors == ["HTTP 500"]


def test_coverage_is_what_was_actually_read():
    rd = reducer(n_bins=4, start=0, end=4 * HOUR)
    rd.add_chunk(task(0, HOUR), [])
    rd.mark_failed(task(HOUR, 4 * HOUR), RuntimeError("HTTP 500"))
    d = rd.finish()
    assert abs(d.coverage - 0.25) < 1e-9


# --- the raw curve for short windows ----------------------------------------

def test_raw_readings_are_kept_below_the_cap():
    rd = reducer(n_bins=4, raw_keep=100)
    rd.add_chunk(task(0, 4 * HOUR),
                 samples([(i * NS, float(i)) for i in range(50)]))
    d = rd.finish()
    assert not d.is_reduced and d.raw_t_ns is not None and d.raw_t_ns.size == 50


def test_raw_readings_are_dropped_above_the_cap():
    rd = reducer(n_bins=4, raw_keep=100)
    rd.add_chunk(task(0, 4 * HOUR),
                 samples([(i * NS, float(i)) for i in range(200)]))
    d = rd.finish()
    assert d.is_reduced and d.bin_count.sum() == 200


def test_raw_readings_come_back_in_time_order():
    rd = reducer(n_bins=4, raw_keep=1000)
    rd.add_chunk(task(2 * HOUR, 4 * HOUR), samples([(3 * HOUR, 2.0)]))
    rd.add_chunk(task(0, 2 * HOUR), samples([(HOUR, 1.0)]))
    d = rd.finish()
    assert list(d.raw_t_ns) == [HOUR, 3 * HOUR]
    assert list(d.raw_v) == [1.0, 2.0]


# --- nanoseconds ------------------------------------------------------------

def test_timestamps_keep_microsecond_precision_over_half_a_year():
    start = int(1.8e18)                       # a realistic epoch-ns value
    end = start + 180 * DAY
    rd = reducer(n_bins=900, start=start, end=end, raw_keep=10)
    t = start + 90 * DAY + 123_000            # 123 us past a round moment
    rd.add_chunk(task(start, end), samples([(t, 1.0)]))
    d = rd.finish()
    assert int(d.bin_t_ns[d.bin_count.argmax()]) == t


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
