"""Probe: the Graph tab's opening split, the statistics cards, and the cursor
labels at the right-hand edge.

Three things reported from the running program:

* the PV list opened far too tall — the graph was left with a strip;
* Std and P-P were absolute numbers, and are read as "how steady is it", which
  is a percentage of the average;
* with the mouse at the far right the value boxes were drawn past the edge of
  the window and could not be read.

Renders with a real Qt platform, because offscreen has no fonts and lies about
text size.

    python testing/probe_graph_layout.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication            # noqa: E402

import main as css                                    # noqa: E402
import test_smoke as smoke                            # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")


class _Ev:
    """The bits of a matplotlib mouse event the hover handler reads."""
    def __init__(self, ax, x, y, canvas):
        self.inaxes = ax
        self.xdata, self.ydata = x, y
        self.x, self.y = canvas.figure.transFigure.transform((0.5, 0.5))
        self.canvas = canvas


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app

    # Eight channels, so the PV list has more rows than the opening height can
    # hold — that is the case where the split matters.
    w = smoke._loaded_widget(tuple(f"L3-TEST-{c}:Energy" for c in "ABCDEFGH"))
    w.resize(1500, 950)
    w.show()
    QApplication.processEvents()
    w._autosize_axis_pane()
    QApplication.processEvents()

    # ── the opening split ────────────────────────────────────────────────────
    spl = w._graph_v_splitter
    tv = w._axis_tv
    sizes = spl.sizes()
    total = sum(sizes) or 1
    share = sizes[1] / total
    row_h = tv.rowHeight(0) if tv.rowCount() else 26
    rows_visible = tv.viewport().height() / row_h if row_h else 0
    print(f"splitter sizes      : {sizes}")
    print(f"PV list share       : {share:.0%}   (want about 25 %)")
    print(f"rows on screen      : {rows_visible:.1f}   (want 4-5)")
    print(f"table rows in total : {tv.rowCount()}")

    # ── the statistics, in percent ───────────────────────────────────────────
    from cpva_core import _fmt_percent_of
    cases = [
        ("std 0.07 of mean 1.0", _fmt_percent_of(0.07, 1.0), "7.00 %"),
        ("p-p 0.37 of mean 1.0", _fmt_percent_of(0.37, 1.0), "37.00 %"),
        ("rounded to hundredths", _fmt_percent_of(1.0, 3.0), "33.33 %"),
        ("negative average", _fmt_percent_of(0.5, -2.0), "25.00 %"),
        ("average of zero", _fmt_percent_of(0.5, 0.0), "—"),
        ("nothing there", _fmt_percent_of(float("nan"), 1.0), "—"),
    ]
    print()
    for name, got, want in cases:
        print(f"  {'ok ' if got == want else 'BAD'}  {name:24s} {got!r:12s} want {want!r}")

    # ── the cursor labels at the right-hand edge ─────────────────────────────
    ax = w._graph_axes[0]
    x_lo, x_hi = ax.get_xlim()
    canvas = w._mpl_canvas

    for tag, x_at in (("left", x_lo + (x_hi - x_lo) * 0.05),
                      ("right", x_hi - (x_hi - x_lo) * 0.001)):
        w._on_graph_mouse_move(_Ev(ax, x_at, ax.get_ylim()[0], canvas))
        w._process_mouse_move()
        QApplication.processEvents()
        bb = ax.get_window_extent()
        worst = None
        for ann in w._crosshair_texts:
            if ann is None or not ann.get_visible():
                continue
            ext = ann.get_window_extent(canvas.get_renderer())
            over = ext.x1 - bb.x1
            worst = over if worst is None else max(worst, over)
        print(f"\ncursor at the {tag:5s} edge: worst overhang "
              f"{'n/a' if worst is None else f'{worst:+.0f} px'}   "
              f"(want <= 0 on the right)")
        w.grab().save(os.path.join(OUT, f"cursor_{tag}.png"))

    # ── the statistics cards as they really render ───────────────────────────
    x_lo, x_hi = ax.get_xlim()
    w._sel_range = (x_lo + (x_hi - x_lo) * 0.2, x_lo + (x_hi - x_lo) * 0.8)
    w._recompute_stats()
    QApplication.processEvents()
    w._stats_scroll.grab().save(os.path.join(OUT, "stats_cards.png"))

    w.grab().save(os.path.join(OUT, "graph_layout.png"))
    print(f"\nScreenshots in {OUT}")
    w.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
