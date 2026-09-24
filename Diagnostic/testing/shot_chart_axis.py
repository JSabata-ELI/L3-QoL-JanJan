"""Render the chat chart's X axis at several window lengths.

The labels on the pictures sent to the chat used to be tilted 30 degrees
(matplotlib's autofmt_xdate). They are horizontal now, which only works if the
labels still fit - hence this: it draws 2 h, 12 h, 3 d, 30 d and 90 d from
canned readings (no archiver) and saves one PNG per window.

    python testing/shot_chart_axis.py [out_dir]
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import math  # noqa: E402

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import monitor_tab as mt  # noqa: E402

NS = 1_000_000_000
WINDOWS = [("2h", 2 / 24), ("12h", 0.5), ("3d", 3), ("30d", 30), ("90d", 90)]


def canned(start_ns: int, end_ns: int, n: int = 4000):
    """A chiller-like saw wave, so the picture looks like the real thing."""
    step = max(1, (end_ns - start_ns) // n)
    t, v = [], []
    ts = start_ns
    i = 0
    while ts < end_ns:
        t.append(ts)
        v.append(16.0 + 4.0 * math.sin(i / 40.0))
        ts += step
        i += 1
    return t, v


def main(argv: list[str]) -> int:
    out_dir = argv[1] if len(argv) > 1 else HERE
    end = api.now_ns()
    for label, days in WINDOWS:
        start = end - int(days * 86400 * NS)
        t, v = canned(start, end)
        data = ch.make_raw_series("X:Temp", "Helium Chiller", t, v,
                                  start, end, units="DegC")
        # Force the condensed drawing for the long windows, the raw one for the
        # short ones - exactly what the reading itself would decide.
        if days >= 1:
            data.raw_t_ns = data.raw_v = None
        # Hand the renderer the canned curve instead of letting it read the
        # archive: this is about the axis, and it must run with no network.
        mt._fetch_chart_data = (
            lambda *a, **kw: ([data], ch.FetchReport(coverage=1.0)))
        png = mt.render_chart_png(
            [mt.ChartSeries("X:Temp", "Helium Chiller")], start, end,
            timeout=5.0, window_label=f"last {label}")
        path = os.path.join(out_dir, f"chart_axis_{label}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
