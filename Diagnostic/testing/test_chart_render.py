"""Drawing the picture: does it come out, and does it say the right things.

Needs Qt (monitor_tab imports it) but never opens a window and never touches the
network — the archiver read is replaced by canned readings.

Run with:  QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_chart_render.py
"""

import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

import numpy as np  # noqa: E402

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import monitor_tab as mt  # noqa: E402

NS = 1_000_000_000
HOUR = 3600 * NS
DAY = 24 * HOUR
END = int(1.8e18)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@contextlib.contextmanager
def canned(make):
    """Replace the archiver read with `make(series, start, end) -> datas`."""
    real = mt._fetch_chart_data

    def fake(series, start_ns, end_ns, timeout, **kw):
        datas, rep = make(series, start_ns, end_ns)
        return datas, rep

    mt._fetch_chart_data = fake
    try:
        yield
    finally:
        mt._fetch_chart_data = real


def raw_series(pv, name, start, end, n=60, units="degC"):
    t = [start + i * (end - start) // n for i in range(n)]
    v = [20.0 + (i % 7) for i in range(n)]
    return ch.make_raw_series(pv, name, t, v, start, end, units)


def reduced_series(pv, name, start, end, n=5000, units="degC", spike=None):
    rd = ch._BinReducer(ch.SeriesRequest(pv, name), start, end, 900, raw_keep=10)
    step = max(1, (end - start) // n)
    samples = [{"time": start + i * step, "value": 20.0 + (i % 5),
                "metaData": {"units": units}} for i in range(n)]
    if spike is not None:
        samples[n // 2]["value"] = spike
    rd.add_chunk(api.ChunkTask(pv, start, end, 0), samples)
    d = rd.finish()
    d.planned_ns = end - start
    return d


def render(datas, report=None, **kw):
    report = report or ch.FetchReport(n_requests=len(datas), n_done=len(datas),
                                      n_pvs=len(datas))
    series = [mt.ChartSeries(d.request.pv_name, d.request.display_name)
              for d in datas]
    info = {}
    with canned(lambda s, a, b: (datas, report)):
        png = mt.render_chart_png(series, datas[0].bin_read_ns.size and
                                  END - 180 * DAY, END, 5.0, "test window",
                                  out_info=info, **kw)
    return png, info


# --- the picture comes out --------------------------------------------------

def test_a_raw_window_still_draws():
    d = raw_series("PV", "Chiller 1", END - 6 * HOUR, END)
    png, info = render([d])
    assert png and png.startswith(PNG_MAGIC)
    assert not d.is_reduced


def test_a_long_window_draws_condensed():
    d = reduced_series("PV", "Chiller 1", END - 180 * DAY, END)
    png, info = render([d])
    assert png and png.startswith(PNG_MAGIC)
    assert d.is_reduced


def test_a_raw_and_a_condensed_curve_on_one_picture():
    a = raw_series("A", "Chiller 1", END - 180 * DAY, END, n=30)
    b = reduced_series("B", "Chiller 2", END - 180 * DAY, END)
    png, _ = render([a, b])
    assert png and png.startswith(PNG_MAGIC)


def test_a_single_reading_still_produces_a_picture():
    d = ch.make_raw_series("PV", "Chiller 1", [END - HOUR], [21.0],
                           END - 6 * HOUR, END, "degC")
    png, _ = render([d])
    assert png and png.startswith(PNG_MAGIC)


def test_nothing_anywhere_means_no_picture():
    d = ch.make_raw_series("PV", "Chiller 1", [], [], END - DAY, END)
    png, info = render([d])
    assert png is None
    assert "holds no readings" in info["findings"]


def test_one_dead_pv_does_not_take_the_other_curve_with_it():
    good = raw_series("A", "Chiller 1", END - DAY, END)
    rd = ch._BinReducer(ch.SeriesRequest("B", "Chiller 2"), END - DAY, END, 900)
    rd.mark_failed(api.ChunkTask("B", END - DAY, END, 0),
                   RuntimeError("HTTP 500"))
    png, info = render([good, rd.finish()])
    assert png and png.startswith(PNG_MAGIC)
    assert "Chiller 2 could not be read" in info["findings"]


# --- what the picture says --------------------------------------------------

def test_a_sampled_read_is_stamped_on_the_picture():
    d = reduced_series("PV", "Chiller 1", END - 180 * DAY, END)
    rep = ch.FetchReport(mode="sampled", coverage=0.24, n_requests=600,
                         n_done=600, n_pvs=1)
    png, info = render([d], rep)
    assert png
    assert "SAMPLED - 24 % of the window read" in info["banner"]


def test_an_unreadable_stretch_is_stamped_on_the_picture():
    rd = ch._BinReducer(ch.SeriesRequest("PV", "Chiller 1"),
                        END - 4 * DAY, END, 900, raw_keep=10)
    rd.add_chunk(api.ChunkTask("PV", END - 4 * DAY, END - DAY, 0),
                 [{"time": END - 3 * DAY, "value": 20.0}])
    rd.mark_failed(api.ChunkTask("PV", END - DAY, END, 1),
                   RuntimeError("HTTP 500"))
    png, info = render([rd.finish()])
    assert png
    assert "INCOMPLETE" in info["banner"]


def test_a_complete_read_carries_no_stamp():
    d = reduced_series("PV", "Chiller 1", END - 180 * DAY, END)
    png, info = render([d])
    assert png and info["banner"] == ""


def test_the_bands_are_left_out_when_there_are_many_curves():
    datas = [reduced_series(f"P{i}", f"PV {i}", END - 30 * DAY, END, n=500)
             for i in range(12)]
    png, _ = render(datas)
    assert png and png.startswith(PNG_MAGIC)


def test_newest_reading_is_the_true_maximum_not_the_last_bin():
    d = reduced_series("PV", "Chiller 1", END - 180 * DAY, END)
    png, info = render([d])
    assert png
    assert abs(info["newest_ns"] - d.newest_ns) < NS


# --- the numbers behind the drawing -----------------------------------------

def test_a_spike_reaches_the_band_even_when_the_average_hides_it():
    d = reduced_series("PV", "Chiller 1", END - 180 * DAY, END, spike=95.0)
    assert np.nanmax(d.bin_max) == 95.0
    assert np.nanmax(d.bin_mean) < 40.0


def test_timestamps_convert_to_matplotlib_numbers_without_drift():
    t = np.array([END, END + 12 * HOUR], dtype=np.int64)
    num = mt._ns_to_num(t)
    assert abs((num[1] - num[0]) - 0.5) < 1e-9


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
