"""The XY tab has to keep up with Live, with Conditions, and with Back.

Three faults are covered, all reported from the running program:

1. The cloud never grew while Live was on — the tab had to be switched off and
   on again. Nothing in the live tick touched it, although the rows it draws
   from were kept fresh all along.
2. Conditions changed the table and the graph but not the cloud, even when the
   Conditions button was pressed from the XY tab itself.
3. Back and Forward stayed greyed out after a right-drag zoom: only the view
   being LEFT was put on the toolbar's history, never the one being entered.

No network, no archiver — the rows are made up here. Needs a real Qt platform
because the toolbar's buttons are what is being checked:

    python testing/test_xy_live.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication      # noqa: E402

import main as css                              # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


PV_X = "TEST:X"
PV_Y = "TEST:Y"


def _rows(n, start_ns=1_700_000_000_000_000_000, step_ns=1_000_000_000):
    """n rows, both channels present, Y climbing 0, 1, 2, …"""
    out = []
    for i in range(n):
        ts = start_ns + i * step_ns
        out.append((ts, {PV_X: (float(i), ts), PV_Y: (float(i) * 2.0, ts)}))
    return out


def _widget():
    w = css.CSSLoggerWidget()
    w._xy_choice_map = {"X ch": PV_X, "Y ch": PV_Y}
    for combo, label in ((w._xy_x_combo, "X ch"), (w._xy_y_combo, "Y ch")):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(["X ch", "Y ch"])
        combo.setCurrentText(label)
        combo.blockSignals(False)
    return w


def main() -> int:
    print("test_xy_live")
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app

    w = _widget()
    w._table_rows = _rows(20)
    w._table_rows_unfiltered = list(w._table_rows)
    w._notebook.setCurrentWidget(w._tab_xy)
    QApplication.processEvents()

    w._plot_xy()
    QApplication.processEvents()
    check("Plot XY draws a cloud", w._xy_scatter is not None)
    if w._xy_scatter is None:
        print("\nFAILED:\n  - nothing plotted")
        return 1
    n0 = len(w._xy_scatter.get_offsets())
    fig0, canvas0 = w._xy_figure, w._xy_canvas
    check("all rows are in it", n0 == 20, f"{n0} points")

    # ── 1. a live tick ────────────────────────────────────────────────────────
    w._table_rows = _rows(30)
    w._table_rows_unfiltered = list(w._table_rows)
    w._xy_refresh_live()
    QApplication.processEvents()
    n1 = len(w._xy_scatter.get_offsets())
    check("a live tick grows the cloud", n1 == 30, f"{n0} -> {n1} points")
    check("the figure is reused, not rebuilt",
          w._xy_figure is fig0 and w._xy_canvas is canvas0)

    # ── the hidden tab is caught up on the way in ────────────────────────────
    w._notebook.setCurrentWidget(w._tab_table)
    QApplication.processEvents()
    w._table_rows = _rows(40)
    w._table_rows_unfiltered = list(w._table_rows)
    w._xy_refresh_live()
    QApplication.processEvents()
    check("a hidden tab is only marked, not redrawn",
          len(w._xy_scatter.get_offsets()) == 30 and w._xy_dirty is True)
    w._notebook.setCurrentWidget(w._tab_xy)
    QApplication.processEvents()
    check("switching to it catches it up",
          len(w._xy_scatter.get_offsets()) == 40 and w._xy_dirty is False,
          f"{len(w._xy_scatter.get_offsets())} points, dirty={w._xy_dirty}")

    # ── 2. conditions ────────────────────────────────────────────────────────
    # Y climbs 0, 2, 4, … so "Y at most 20" must leave 11 of the 40 rows.
    w._conditions = [{"pv": PV_Y, "min": None, "max": 20.0}]
    w._cond_last_diag = None
    rows = w._filter_master_multiple_rows(w._table_rows_unfiltered)
    w._table_rows = w._apply_conditions_to_rows(rows)
    kept = len(w._table_rows)
    w._xy_dirty = True
    w._xy_refresh_live()
    QApplication.processEvents()
    n_cond = len(w._xy_scatter.get_offsets())
    check("a condition thins the cloud out", n_cond == kept and kept < 40,
          f"{kept} rows kept, {n_cond} points drawn")

    # The same rows feed the Table tab and the Graph tab, so one condition has
    # to show up in all three. (The PV Time tab has its own target ± tolerance
    # rows and is filtered where it is drawn, in _pv_time_draw.)
    w._populate_table()
    QApplication.processEvents()
    check("the Table tab is filtered by the same condition",
          w._table_widget.rowCount() == kept,
          f"{w._table_widget.rowCount()} table rows, {kept} kept")

    # ── 3. the toolbar's history ─────────────────────────────────────────────
    w._conditions = []
    w._table_rows = _rows(40)
    w._plot_xy()
    QApplication.processEvents()
    tb = w._xy_toolbar
    check("the opening view is on the history", len(tb._nav_stack) >= 1,
          f"{len(tb._nav_stack)} views")

    class _E:
        def __init__(self, x, y):
            self.xdata, self.ydata = x, y

    w._on_xy_rect_select(_E(5.0, 10.0), _E(15.0, 30.0))
    QApplication.processEvents()
    check("a right-drag zoom adds the view it arrives at",
          len(tb._nav_stack) >= 2, f"{len(tb._nav_stack)} views")
    back = next((a for a in tb.actions() if a.text() == "Back"), None)
    fwd = next((a for a in tb.actions() if a.text() == "Forward"), None)
    check("Back is live after a right-drag zoom",
          back is not None and back.isEnabled())

    xlim_zoomed = w._xy_figure.axes[0].get_xlim()
    tb.back()
    QApplication.processEvents()
    check("Back really goes back",
          w._xy_figure.axes[0].get_xlim() != xlim_zoomed,
          f"{w._xy_figure.axes[0].get_xlim()}")
    check("Forward is live once Back was used",
          fwd is not None and fwd.isEnabled())
    tb.forward()
    QApplication.processEvents()
    check("Forward returns to the zoom",
          w._xy_figure.axes[0].get_xlim() == xlim_zoomed)

    # A zoomed view must not be dragged around by the next live tick.
    at_zoom = w._xy_figure.axes[0].get_xlim()
    w._table_rows = _rows(60)
    w._xy_refresh_live()
    QApplication.processEvents()
    check("a live tick leaves the user's zoom alone",
          w._xy_figure.axes[0].get_xlim() == at_zoom)

    w.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
