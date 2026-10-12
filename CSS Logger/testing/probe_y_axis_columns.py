"""Probe: how tightly the Graph tab's Y-axis columns are packed.

Every PV has its own Y axis column left of the plot: spine, tick marks, the
rotated tick numbers, then the rotated title. This draws the graph with 4 and
12 PVs at three font sizes and measures, in pixels, the empty gap between each
title and the column to its left (its spine / tick numbers), plus any overlap.
The text extents come from matplotlib's own renderer, so the Qt platform does
not matter here. A crop of the axis area is saved into testing/_out/.

    python testing/probe_y_axis_columns.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication            # noqa: E402

import main as css                                    # noqa: E402
import test_smoke as smoke                            # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")


def measure(w):
    """Per column i ≥ 1: px between the title's left ink edge and the right
    edge of whatever column i-1 puts there (its spine, tick marks or tick
    numbers). Negative = overlap. Also the title ↔ own-numbers gap."""
    fig = w._mpl_figure
    w._mpl_canvas.draw()
    r = fig.canvas.get_renderer()
    axes = w._graph_axes
    cols = []
    for ax in axes:
        spine_x = ax.spines["left"].get_window_extent(r).x1
        nums = [t.get_window_extent(r) for t in ax.get_yticklabels()
                if t.get_visible() and t.get_text()]
        num_x0 = min((b.x0 for b in nums), default=spine_x)
        num_x1 = max((b.x1 for b in nums), default=spine_x)
        lab = ax.yaxis.label
        t = lab.get_window_extent(r) if lab.get_text() else None
        cols.append({"spine": spine_x, "num_x0": num_x0, "num_x1": num_x1,
                     "title": t})
    gaps, own = [], []
    for i in range(1, len(cols)):
        left_edge = max(cols[i - 1]["spine"], cols[i - 1]["num_x1"])
        t = cols[i]["title"]
        if t is not None:
            gaps.append(t.x0 - left_edge)
            own.append(cols[i]["num_x0"] - t.x1)
    step = (cols[1]["spine"] - cols[0]["spine"]) if len(cols) > 1 else 0
    return gaps, own, step, cols


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    worst = 0.0
    for n in (4, 12):
        pvs = tuple(f"L3-TEST-{chr(65 + i)}:Energy" for i in range(n))
        w = smoke._loaded_widget(pvs)
        w.resize(1500, 950)
        QApplication.processEvents()
        for fs in (9, 11, 14):
            w._font_size_spin.setValue(fs)
            w._plot_graph_impl()
            QApplication.processEvents()
            gaps, own, step, cols = measure(w)
            left_px = cols[0]["title"].x0 if cols[0]["title"] is not None else 0
            print(f"n={n:2d} font={fs:2d}  step={step:5.1f}px  "
                  f"title↔left column min/max={min(gaps):5.1f}/{max(gaps):5.1f}px  "
                  f"title↔own numbers={min(own):4.1f}px  outer={left_px:4.1f}px")
            worst = min(worst, round(min(gaps), 1), round(min(own), 1))
            fig = w._mpl_figure
            right = cols[-1]["spine"] + 20
            from matplotlib.transforms import Bbox
            bb = Bbox.from_extents(0, 0, right, fig.bbox.height)
            fig.savefig(os.path.join(OUT, f"y_columns_n{n}_f{fs}.png"),
                        dpi=fig.dpi, bbox_inches=bb.transformed(
                            fig.dpi_scale_trans.inverted()))
        w.close()
    print("OVERLAP" if worst < 0 else "no overlap")
    return 1 if worst < 0 else 0


if __name__ == "__main__":
    sys.exit(main())
