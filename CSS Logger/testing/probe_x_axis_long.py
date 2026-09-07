"""Render the CSS Logger time axis over several periods and check the stamps.

Why: over more than a day the stamps used to be written over each other, and
the right-hand one always overlapped its neighbour because both window edges
were stamped with only a quarter of a step of clearance.

Run:  python "CSS Logger/testing/probe_x_axis_long.py"

Saves one PNG per period into testing/_out/ and prints, for each, the stamps it
produced and the smallest gap between two of them in pixels. A negative or tiny
gap means they touch. Offscreen Qt has no fonts and mis-measures text, so this
renders with the real Windows backend.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
OUT = HERE / "_out"
OUT.mkdir(exist_ok=True)

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtWidgets import QApplication

import main as app_main
from cpva_core import TZ_PRAGUE

PERIODS = [
    ("1 hour",   timedelta(hours=1)),
    ("12 hours", timedelta(hours=12)),
    ("2 days",   timedelta(days=2)),
    ("1 week",   timedelta(days=7)),
    ("1 month",  timedelta(days=31)),
    ("1 year",   timedelta(days=365)),
]

FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class _Stub:
    """Just enough of CSSLoggerWidget for the two tick routines."""
    _graph_opts = dict(app_main._GRAPH_OPTS_DEFAULTS)
    _apply_x_ticks = app_main.CSSLoggerWidget._apply_x_ticks
    _compute_x_ticks = app_main.CSSLoggerWidget._compute_x_ticks


def run_one(label, span):
    t_hi = datetime(2026, 9, 2, 14, 37, 12, tzinfo=TZ_PRAGUE)
    t_lo = t_hi - span
    fig = Figure(figsize=(9.0, 2.4), dpi=110)
    fig.subplots_adjust(left=0.06, right=0.985, top=0.95, bottom=0.30)
    ax = fig.add_subplot(111)
    ax.xaxis.axis_date(tz=TZ_PRAGUE)
    ax.set_xlim(t_lo, t_hi)
    stub = _Stub()
    stub._apply_x_ticks(ax, t_lo, t_hi)
    ax.tick_params(axis="x", which="major", labelsize=10, rotation=0)
    ax.tick_params(axis="x", which="minor", length=3, labelsize=0)
    ax.set_xlabel("Time (Prague)", fontsize=11)
    canvas = FigureCanvasQTAgg(fig)
    canvas.draw()

    texts, boxes = [], []
    for t in ax.get_xticklabels():
        s = t.get_text()
        if not s:
            continue
        bb = t.get_window_extent(renderer=canvas.get_renderer())
        texts.append(s.replace("\n", "|"))
        boxes.append((bb.x0, bb.x1))
    boxes_sorted = sorted(boxes)
    gaps = [boxes_sorted[i + 1][0] - boxes_sorted[i][1]
            for i in range(len(boxes_sorted) - 1)]
    min_gap = min(gaps) if gaps else 999.0

    fig_w = fig.get_size_inches()[0] * fig.dpi
    left_over = min(b[0] for b in boxes) if boxes else 0.0
    right_over = fig_w - max(b[1] for b in boxes) if boxes else 0.0

    png = OUT / f"x_axis_{label.replace(' ', '_')}.png"
    fig.savefig(png, dpi=110)
    print(f"\n{label}: {len(texts)} stamps, min gap {min_gap:.1f} px")
    print("   " + "  ".join(texts))
    check(f"{label}: stamps do not touch", min_gap >= 3.0, f"min gap {min_gap:.1f} px")
    check(f"{label}: nothing hangs off the left", left_over >= -1.0,
          f"{left_over:.1f} px")
    check(f"{label}: nothing hangs off the right", right_over >= -1.0,
          f"{right_over:.1f} px")
    if span > timedelta(days=1):
        check(f"{label}: says which day", any("|" in t or "-" in t for t in texts))
    print(f"   saved {png.name}")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    for label, span in PERIODS:
        run_one(label, span)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all periods readable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
