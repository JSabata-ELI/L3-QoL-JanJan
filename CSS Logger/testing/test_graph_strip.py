"""
Graph tab: tool strip, loupe, PV-table row order, statistics strip, margins
dialog and image saving.

What this pins:

  * rows dragged in the PV table set the order of the Y axes on the graph,
    while the PV list on the left keeps its own order; a new PV lands last;
  * the statistics strip is exactly as tall as its cards (no dead band under
    them), "One row" shows one row and "Hide" shows none;
  * the Margins and spacing window changes the gap between a Y-axis title and
    its numbers, and the title really moves;
  * "Save graph with selection" writes the statistics under the graph (a
    taller picture), "Save graph" leaves the band out;
  * the loupe shows only the signal nearest the mouse, the spot around the
    mouse magnified evenly in time and value;
  * every Show / Autoscale / Grid cell has a tick box from the start.

Runs headless (offscreen) with the network mocked, as test_smoke does.
"""
import os
import sys
import pathlib
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import test_smoke as ts                     # noqa: E402  (helpers + mocks)
from PySide6.QtGui import QImage            # noqa: E402

A, B, C = "L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"


def _replot(w):
    w._plot_graph()
    ts._app.processEvents()


def test_ordered_for_plot():
    w = ts._new_widget()
    w._plot_order = []
    assert w._ordered_for_plot([A, B, C]) == [A, B, C]
    w._plot_order = [C, A]
    assert w._ordered_for_plot([A, B, C]) == [C, A, B], "unplaced PV must go last"
    w._plot_order = [C, "GONE", A]
    assert w._ordered_for_plot([A, C]) == [C, A]
    w.close()


def test_dragged_rows_reorder_the_graph_not_the_list():
    w = ts._loaded_widget((A, B, C))
    w._plot_order = []
    _replot(w)
    mine = (A, B, C)

    def _real_rows():
        return [p for p in w._axis_row_pv if p in mine]

    def _plotted():
        return [p for p in w._graph_pvs if p in mine]

    assert _real_rows() == [A, B, C], w._axis_row_pv
    rows = w._axis_row_pv
    # Drag C to just above A (the top of the original PVs).
    w._on_axis_rows_dropped([rows.index(C)], rows.index(A))
    _replot(w)
    assert _real_rows() == [C, A, B], w._axis_row_pv
    assert _plotted() == [C, A, B], w._graph_pvs
    assert w._real_pv_names() == [A, B, C], "left PV list must keep its order"
    assert [p for p in w.config["plot_order"] if p in mine] == [C, A, B]
    # Drag A to just below B (the end of the original PVs).
    rows = w._axis_row_pv
    w._on_axis_rows_dropped([rows.index(A)], rows.index(B) + 1)
    _replot(w)
    assert _plotted() == [C, B, A], w._graph_pvs
    w.close()


def _select_middle(w):
    lo, hi = w._graph_axes[0].get_xlim()
    w._on_span_select(lo + 0.3 * (hi - lo), lo + 0.7 * (hi - lo))
    ts._app.processEvents()


def test_stats_strip_fits_its_cards():
    w = ts._loaded_widget((A, B, C))
    w.resize(1400, 900)
    w.show()
    ts._app.processEvents()
    _select_middle(w)
    w._set_stats_mode("all")
    ts._app.processEvents()
    sc = w._stats_scroll
    flow = w._stats_flow
    assert flow.count() == 3
    need = flow.heightForWidth(sc.width())
    assert sc.isVisible() and abs(sc.height() - need) <= 1, (sc.height(), need)
    # No space below the cards: the last card ends inside the strip's bottom margin.
    bottom = max(flow.itemAt(i).geometry().bottom() for i in range(flow.count()))
    assert sc.height() - bottom <= 8, (sc.height(), bottom)
    w._set_stats_mode("hide")
    assert not sc.isVisible() and w._stats_head.isVisible()
    w._set_stats_mode("row")
    assert sc.isVisible()
    assert sc.height() <= flow.first_row_height(sc.width()) + 1
    w._set_stats_mode("all")
    w.close()


def test_margins_dialog_moves_the_axis_title():
    w = ts._loaded_widget((A, B))
    _replot(w)
    x0 = w._graph_axes[0].yaxis.label.get_position()[0]
    w._open_margins_dialog()
    dlg = w._margins_dlg
    dlg.w_pad.setValue(dlg.w_pad.value() + 20)
    assert w._graph_opts["label_pad_px"] == dlg.w_pad.value()
    _replot(w)
    x1 = w._graph_axes[0].yaxis.label.get_position()[0]
    assert x1 < x0, f"title did not move away from its numbers: {x0} -> {x1}"
    dlg.close()
    w.close()


def test_save_image_with_and_without_selection():
    w = ts._loaded_widget((A, B))
    w.resize(1200, 800)
    w.show()
    ts._app.processEvents()
    _select_middle(w)
    # The band as a mouse drag leaves it (a programmatic select does not paint it).
    w._span_selector.set_visible(True)
    tmp = pathlib.Path(tempfile.gettempdir())
    p_sel = tmp / "cssl_test_sel.png"
    p_nosel = tmp / "cssl_test_nosel.png"
    n_texts = len(w._mpl_figure.texts)
    w._write_graph_image(str(p_sel), True, dpi=80)
    w._write_graph_image(str(p_nosel), False, dpi=80)
    assert len(w._mpl_figure.texts) == n_texts, "table text left on the graph"
    img_sel, img_no = QImage(str(p_sel)), QImage(str(p_nosel))
    assert img_sel.height() > img_no.height() + 30, (img_sel.height(), img_no.height())
    # The band is back on screen after the "without" save.
    assert any(a.get_visible() for a in w._span_selector.artists)
    w.close()


def test_loupe_shows_the_span_around_the_mouse():
    w = ts._loaded_widget((A, B))
    w.resize(1200, 800)
    w.show()
    ts._app.processEvents()
    w._set_loupe_on(True)
    w._set_loupe_mag(10)
    lo, hi = w._graph_axes[0].get_xlim()
    x = (lo + hi) / 2
    # The mouse right on trace B: the loupe must pick B, and only B.
    ax_b, src_b = w._graph_axes[1], w._graph_lines[1][-1]
    xs, ys = src_b.get_xdata(), src_b.get_ydata()
    i = max(0, int((xs <= x).sum()) - 1)
    x_px, y_px = ax_b.transData.transform((x, float(ys[i])))
    ok = w._loupe.update_at(x, w._graph_axes, w._graph_lines, 10, None,
                            y_px=y_px, names=["A", "B"])
    assert ok
    lp = w._loupe
    assert lp._ax.get_lines()[0].get_color() == src_b.get_color()
    assert "B" in lp._title.get_text()
    # The box shown is the loupe's size / 10 in screen pixels, both ways.
    (l0x, l0y), (l1x, l1y) = ax_b.transData.transform(
        [(lp._ax.get_xlim()[0], lp._ax.get_ylim()[0]),
         (lp._ax.get_xlim()[1], lp._ax.get_ylim()[1])])
    dpr = float(getattr(w._mpl_canvas, "device_pixel_ratio", 1.0) or 1.0)
    assert abs((l1x - l0x) - lp.W * dpr / 10) < 0.5, (l1x - l0x)
    assert abs((l1y - l0y) - lp.H * dpr / 10) < 0.5, (l1y - l0y)
    assert lp._ax.get_xlim()[0] < x < lp._ax.get_xlim()[1]
    w._set_loupe_on(False)
    assert not w._loupe.isVisible()
    w.close()


def test_every_tick_box_cell_has_a_box():
    """Show / Autoscale / Grid cells must carry a tick box from the first fill,
    unticked ones included (they used to appear only after a toggle)."""
    from PySide6.QtCore import Qt
    w = ts._loaded_widget((A, B))
    _replot(w)
    tv, cols = w._axis_tv, list(w._axis_tv_cols)
    for name in ("show", "auto_scale", "grid"):
        if name not in cols:
            continue
        c = cols.index(name)
        for r, pv in enumerate(w._axis_row_pv):
            if not pv:
                continue
            it = tv.item(r, c)
            assert it is not None and it.data(Qt.ItemDataRole.CheckStateRole) is not None, \
                (name, pv)
    w.close()


if __name__ == "__main__":
    ts._install_dialog_mocks()
    fails = 0
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
            print("PASS ", t.__name__)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print("FAIL ", t.__name__, exc)
            fails += 1
    print(f"{len(tests) - fails}/{len(tests)} passed.")
    sys.exit(1 if fails else 0)
