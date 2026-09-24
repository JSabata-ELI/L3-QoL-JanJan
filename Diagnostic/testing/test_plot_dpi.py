"""Asking for a resolution: `/plot …; 300dpi`, and the Settings default.

Three things have to hold:

* `300dpi` is read as a resolution and NOT as a time window - a bare number in
  that position means hours, which is the trap this option walks into;
* the number reaches the picture: the PNG really comes out 8 x 4 inches at that
  dpi;
* a figure from a chat command or a hand-edited settings file is clamped, so
  neither 0 nor 20000 dpi can reach matplotlib.

Run with:  python testing/test_plot_dpi.py
"""

from __future__ import annotations

import math
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bot_commands as bc  # noqa: E402
import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import monitor_tab as mt  # noqa: E402

NS = 1_000_000_000
NOW = api.now_ns()


def opts(*segments):
    return bc.parse_plot_options(list(segments), NOW)


# ---------------------------------------------------------------------------
# The option
# ---------------------------------------------------------------------------

def test_every_spelling_is_accepted():
    for text in ("300dpi", "300 dpi", "dpi 300", "dpi=300", "DPI: 300"):
        assert opts(text).dpi == 300, text


def test_a_bare_number_is_still_a_window_in_hours():
    o = opts("300")
    assert o.dpi is None
    assert o.time is not None and abs(o.time.hours - 300) < 0.01


def test_resolution_and_window_and_yrange_together():
    o = opts("12h", "y 10-30", "600dpi")
    assert o.dpi == 600
    assert abs(o.time.hours - 12) < 0.01
    assert o.yaxis == (10.0, 30.0)
    assert not o.warnings          # the dpi must not count as a second window


def test_out_of_range_is_refused_with_a_readable_message():
    for text in ("10dpi", "5000dpi"):
        try:
            opts(text)
        except bc.CommandError as e:
            assert f"{bc.DPI_MIN}-{bc.DPI_MAX}" in str(e)
        else:
            raise AssertionError(f"{text} should have been refused")


def test_no_option_means_no_opinion():
    assert opts().dpi is None
    assert opts("12h").dpi is None


# ---------------------------------------------------------------------------
# The number reaching the picture
# ---------------------------------------------------------------------------

def _png_size(data: bytes):
    """(width, height) out of the PNG header."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _canned_chart(dpi: int) -> bytes:
    start, end = NOW - 3 * 86400 * NS, NOW
    step = (end - start) // 2000
    t = list(range(start, end, step))
    v = [16.0 + 4.0 * math.sin(i / 40.0) for i in range(len(t))]
    data = ch.make_raw_series("X:Temp", "Chiller", t, v, start, end,
                              units="DegC")
    data.raw_t_ns = data.raw_v = None
    mt._fetch_chart_data = lambda *a, **kw: ([data], ch.FetchReport(coverage=1.0))
    return mt.render_chart_png([mt.ChartSeries("X:Temp", "Chiller")],
                               start, end, timeout=5.0, dpi=dpi)


def test_the_picture_comes_out_at_the_asked_resolution():
    assert _png_size(_canned_chart(100)) == (800, 400)
    assert _png_size(_canned_chart(300)) == (2400, 1200)


def test_no_number_means_the_default():
    assert _png_size(_canned_chart(0)) == (8 * mt.CHART_DPI, 4 * mt.CHART_DPI)


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------

def test_a_nonsense_figure_never_reaches_matplotlib():
    assert mt._chart_dpi(0) == mt.CHART_DPI
    assert mt._chart_dpi(None) == mt.CHART_DPI
    assert mt._chart_dpi("nonsense") == mt.CHART_DPI
    assert mt._chart_dpi(-5) == bc.DPI_MIN
    assert mt._chart_dpi(20000) == bc.DPI_MAX
    assert mt._chart_dpi(300) == 300


def test_the_setting_exists_and_matches_the_module_default():
    assert mt.DEFAULT_SETTINGS["chart_dpi"] == mt.CHART_DPI
    assert bc.DPI_MIN <= mt.CHART_DPI <= bc.DPI_MAX


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
