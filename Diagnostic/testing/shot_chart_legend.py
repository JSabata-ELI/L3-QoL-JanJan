"""Render the six-chiller 90-day chart that had its legend on top of the text.

The readings sit in the left third of the window (the archiver holds nothing
after that), so matplotlib's "best" legend placement used to drop the box into
the empty lower right - exactly where the red "NOT CURRENT" line is printed.
This draws that same picture from canned readings (no archiver), plus a version
with the banner only and one with neither note.

    python testing/shot_chart_legend.py [out_dir]
"""

from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import chart_history as ch  # noqa: E402
import cpva_api as api  # noqa: E402
import monitor_tab as mt  # noqa: E402

NS = 1_000_000_000
NAMES = ["DA1 Chiller", "DA2 Chiller", "DA3 Chiller", "DA4 Chiller",
         "Helium Chiller", "Utility Chiller"]


def canned(start_ns: int, end_ns: int, phase: float, n: int = 3000):
    """A chiller-like saw wave that stops a third of the way in."""
    stop = start_ns + (end_ns - start_ns) // 3
    step = max(1, (stop - start_ns) // n)
    t, v = [], []
    ts, i = start_ns, 0
    while ts < stop:
        t.append(ts)
        v.append(16.5 + 5.0 * math.sin(i / 37.0 + phase)
                 + 1.5 * math.sin(i / 3.0))
        ts += step
        i += 1
    return t, v


def build(start: int, end: int, names: list[str] | None = None):
    datas = []
    for i, name in enumerate(names or NAMES):
        t, v = canned(start, end, phase=i * 0.7)
        d = ch.make_raw_series(f"X:Chill{i}", name, t, v, start, end,
                               units="DegC")
        d.raw_t_ns = d.raw_v = None        # force the condensed drawing
        datas.append(d)
    return datas


def main(argv: list[str]) -> int:
    out_dir = argv[1] if len(argv) > 1 else HERE
    end = api.now_ns()
    start = end - 90 * 86400 * NS
    cases = [
        ("both", ch.FetchReport(mode="sampled", coverage=0.18,
                                stopped_low_memory=True,
                                stopped_reason="the window was too long")),
        ("banner", ch.FetchReport(mode="sampled", coverage=0.18)),
        ("clean", ch.FetchReport(coverage=1.0)),
    ]
    many = [f"Amplifier {i + 1} cooling water outlet" for i in range(14)]
    cases.append(("many", ch.FetchReport(coverage=1.0), many))
    for case in cases:
        label, report = case[0], case[1]
        names = case[2] if len(case) > 2 else NAMES
        datas = build(start, end, names)
        mt._fetch_chart_data = (lambda *a, _d=datas, _r=report, **kw:
                                (_d, _r))
        info = {}
        png = mt.render_chart_png(
            [mt.ChartSeries(f"X:Chill{i}", n) for i, n in enumerate(names)],
            start, end, timeout=5.0, window_label="last 90 d",
            stale_after_s=0.0 if label in ("clean", "many") else 3600.0,
            out_info=info)
        path = os.path.join(out_dir, f"chart_legend_{label}.png")
        with open(path, "wb") as fh:
            fh.write(png)
        print("saved", path, "| banner:", info.get("banner", "")[:40],
              "| note:", bool(info.get("note")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
