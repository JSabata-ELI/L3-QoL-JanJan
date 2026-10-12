"""A display change shows at once, on the graph as it is; the wheel never
changes a field the user has not clicked.

  * Width / style / Y min / Y max / Autoscale / smoothing / name edits in the
    PV table change the existing graph: same canvas, no rebuild queued, no
    archive read.
  * Moving or removing a PV in the sidebar uses the data already loaded.
  * A zoomed view survives a full rebuild.
  * The wheel over an unclicked drop-down, a calendar or a tab bar changes
    nothing.

Headless (offscreen), network mocked. Run:  python testing/test_instant_changes_and_wheel.py
"""
import os
import sys
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import test_smoke as ts                       # noqa: E402  (mocks + helpers)
import main as app_main                       # noqa: E402
from PySide6.QtCore import Qt, QPoint, QPointF, QDate        # noqa: E402
from PySide6.QtGui import QWheelEvent                         # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QScrollArea,  # noqa: E402
                               QTabWidget, QWidget, QVBoxLayout, QAbstractItemView)

_fails = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        _fails.append(name)


def _loaded(pvs):
    ts._install_network_mock()
    ts._install_dialog_mocks()
    w = ts._new_widget()
    ts._set_pvs(w, pvs)
    ts._load_and_wait(w)
    return w


def _cell(w, pv, col_name):
    row = w._axis_row_pv.index(pv)
    col = list(w._axis_tv_cols).index(col_name)
    return w._axis_tv.item(row, col)


def _quiet(w):
    """No rebuild queued and no archive read started or queued."""
    return (not w._replot_timer.isActive() and not w._load_in_flight
            and not w._autoload_timer.isActive())


def test_axis_table_edits_change_the_graph_in_place():
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]
    w = _loaded(pvs)
    canvas = w._mpl_canvas
    pv = pvs[0]
    gi = w._graph_pvs.index(pv)

    _cell(w, pv, "width").setText("3.5")
    lw = w._graph_lines[gi][-1].get_linewidth()
    check("width shows at once", lw == 3.5, str(lw))

    _cell(w, pv, "style").setText("dashed")
    ls = w._graph_lines[gi][-1].get_linestyle()
    check("line style shows at once", ls == "--", ls)

    _cell(w, pv, "alpha").setText("40")
    a = w._graph_lines[gi][-1].get_alpha()
    check("transparency shows at once", abs(a - 0.4) < 1e-9, str(a))

    y_before = w._graph_axes[gi].get_ylim()
    _cell(w, pv, "ymin").setText("0")
    _cell(w, pv, "ymax").setText("1000")
    y_after = w._graph_axes[gi].get_ylim()
    check("Y min / Y max show at once", y_after != y_before, f"{y_before} -> {y_after}")

    _cell(w, pv, "display_name").setText("Energy A")
    lbl = w._graph_axes[gi].yaxis.label.get_text()
    check("a new name shows on the axis at once", lbl == "Energy A", lbl)

    check("same canvas — nothing was rebuilt", w._mpl_canvas is canvas)
    check("no rebuild queued, nothing read again", _quiet(w))
    w.close()


def test_sidebar_move_and_remove_read_nothing():
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]
    w = _loaded(pvs)
    n_rows = len(w._table_rows)
    w._pv_list.clearSelection()
    w._pv_list.item(0).setSelected(True)
    w._move_selected_pvs(+1)
    check("move: new order without a read",
          w._base_pv_order == pvs[::-1] and _quiet(w), str(w._base_pv_order))
    check("move: the table keeps its rows", len(w._table_rows) == n_rows)

    w._pv_list.clearSelection()
    w._pv_list.item(0).setSelected(True)       # now L3-TEST-B
    w._remove_selected_pvs()
    check("remove: dropped without a read",
          "L3-TEST-B:Energy" not in w._samples_by_pv and _quiet(w),
          str(list(w._samples_by_pv)))
    check("remove: the other channel stays on the graph",
          w._graph_pvs == ["L3-TEST-A:Energy"], str(w._graph_pvs))
    w.close()


def test_zoom_survives_a_rebuild():
    w = _loaded(["L3-TEST-A:Energy", "L3-TEST-B:Energy"])
    ax = w._graph_axes[0]
    lo, hi = ax.get_xlim()
    zoom = (lo + (hi - lo) * 0.25, lo + (hi - lo) * 0.5)
    ax.set_xlim(*zoom)
    w._user_zoomed = True
    w._plot_graph()                              # e.g. Show ticked / font size
    got = w._graph_axes[0].get_xlim()
    check("zoom kept after a full rebuild",
          abs(got[0] - zoom[0]) < 1e-9 and abs(got[1] - zoom[1]) < 1e-9,
          f"{zoom} -> {got}")
    w.close()


def _wheel(widget, steps=-1):
    ev = QWheelEvent(QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))),
                     QPoint(0, 0), QPoint(0, 120 * steps),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_wheel_never_changes_an_unclicked_field():
    app_main._install_wheel_guard()
    host = QScrollArea()
    inner = QWidget(); lay = QVBoxLayout(inner)
    cb = QComboBox(); cb.addItems(["a", "b", "c"]); cb.setCurrentIndex(1)
    lay.addWidget(cb)
    tabs = QTabWidget(); tabs.addTab(QWidget(), "one"); tabs.addTab(QWidget(), "two")
    lay.addWidget(tabs)
    _frame, cal = app_main._make_calendar(QDate(2026, 6, 15))
    lay.addWidget(_frame)
    host.setWidget(inner)

    _wheel(cb)
    check("drop-down not clicked: wheel changes nothing", cb.currentIndex() == 1,
          str(cb.currentIndex()))
    _wheel(tabs.tabBar())
    check("tab bar: wheel never switches tabs", tabs.currentIndex() == 0,
          str(tabs.currentIndex()))
    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    page = (cal.yearShown(), cal.monthShown())
    _wheel(view.viewport())
    _wheel(view.viewport(), +1)
    check("calendar: wheel never flips the month",
          (cal.yearShown(), cal.monthShown()) == page and
          cal.selectedDate() == QDate(2026, 6, 15),
          f"{page} -> {(cal.yearShown(), cal.monthShown())}")
    host.deleteLater()


if __name__ == "__main__":
    for fn in (test_axis_table_edits_change_the_graph_in_place,
               test_sidebar_move_and_remove_read_nothing,
               test_zoom_survives_a_rebuild,
               test_wheel_never_changes_an_unclicked_field):
        try:
            fn()
        except Exception:
            import traceback; traceback.print_exc()
            _fails.append(fn.__name__)
    print(f"\n{'ALL PASS' if not _fails else f'{len(_fails)} FAILED: ' + ', '.join(_fails)}")
    sys.exit(1 if _fails else 0)
