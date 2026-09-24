"""What a higher-resolution chat picture costs.

Renders the same canned 90-day curve at several dpi and reports pixel size, PNG
size, how long the drawing took and how much memory the canvas needs, so the
choice of CHART_DPI is a measurement and not a guess.

    python testing/bench_chart_dpi.py
"""

from __future__ import annotations

import math
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import memstats  # noqa: E402
import monitor_tab as mt  # noqa: E402

NS = 1_000_000_000
DPIS = (110, 220, 400, 600, 900, 1200)


def canned(start_ns: int, end_ns: int, n: int = 4000):
    step = max(1, (end_ns - start_ns) // n)
    t, v, ts, i = [], [], start_ns, 0
    while ts < end_ns:
        t.append(ts)
        # A saw wave with one two-second spike, the reading the band exists for.
        v.append(41.0 if i == 1500 else 16.0 + 4.0 * math.sin(i / 40.0))
        ts += step
        i += 1
    return t, v


def main() -> int:
    end = api.now_ns()
    start = end - 90 * 86400 * NS
    t, v = canned(start, end)
    data = ch.make_raw_series("X:Temp", "Helium Chiller", t, v, start, end,
                              units="DegC")
    data.raw_t_ns = data.raw_v = None          # the condensed drawing
    mt._fetch_chart_data = lambda *a, **kw: ([data], ch.FetchReport(coverage=1.0))
    keep = mt.CHART_DPI
    print(f"{'dpi':>5} {'pixels':>13} {'PNG':>9} {'draw':>7} {'canvas RGBA':>12}"
          f" {'own commit after':>17}")
    try:
        for dpi in DPIS:
            mt.CHART_DPI = dpi
            t0 = time.monotonic()
            png = mt.render_chart_png(
                [mt.ChartSeries("X:Temp", "Helium Chiller")], start, end,
                timeout=5.0, window_label="last 90 d")
            dt = time.monotonic() - t0
            w, h = int(8 * dpi), int(4 * dpi)
            snap = memstats.read()
            own = f"{snap.proc_commit / 2**30:.2f} GB" if snap else "n/a"
            print(f"{dpi:>5} {f'{w} x {h}':>13} {len(png) / 1024:>7.0f} kB"
                  f" {dt:>6.2f} s {w * h * 4 / 2**20:>9.0f} MB {own:>17}")
            path = os.path.join(HERE, f"chart_dpi_{dpi}.png")
            with open(path, "wb") as fh:
                fh.write(png)
    finally:
        mt.CHART_DPI = keep
    print("\nPNGs saved next to this script (chart_dpi_<dpi>.png).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
