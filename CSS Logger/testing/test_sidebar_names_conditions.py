"""
The October 2026 round of CSS Logger fixes, checked without an archiver:

  * the mouse wheel never changes a drop-down / number box that was not
    clicked — not even when Qt gave it focus from the wheel itself — and stops
    again once the pointer has left it;
  * the sidebar INFO line keeps its width and height whatever it says, and
    ``text()`` still returns exactly what was set;
  * the Custom PV dialog's "What each letter means" table keeps its scroll
    position when it is rebuilt;
  * the PV list shows each PV's NAME, keeps the PV behind it, renames it
    everywhere (and remembers it), and moves PVs up / down;
  * conditions: every kind turns into the right min/max, old saved forms
    load as they were meant, the Conditions window applies at once, and a
    switched-off row filters nothing;
  * the PV Time Plot asks for a redraw on its own when its inputs change.

Runs headless.  Run:  python testing/test_sidebar_names_conditions.py
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                          # noqa: E402
from PySide6.QtCore import Qt, QEvent, QPoint, QPointF           # noqa: E402
from PySide6.QtGui import QWheelEvent, QMouseEvent               # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QSpinBox,  # noqa: E402
                               QScrollArea, QWidget, QVBoxLayout)

app = QApplication.instance() or QApplication(sys.argv)
app_main.install_app_look(app)
app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
# Nothing here may write the user's real settings file.
app_main.save_config = lambda *a, **k: None

failures = []


def check(what, ok, detail=""):
    print(("ok    " if ok else "FAIL  ") + what + (f"   [{detail}]" if not ok and detail else ""))
    if not ok:
        failures.append(what)


# ── 1. the wheel ─────────────────────────────────────────────────────────────
guard = app._wheel_guard
host = QScrollArea()
inner = QWidget(); lay = QVBoxLayout(inner)
combo = QComboBox(); combo.addItems(["a", "b", "c"])
spin = QSpinBox()
lay.addWidget(combo); lay.addWidget(spin)
host.setWidget(inner)
combo.ensurePolished(); spin.ensurePolished()
check("a drop-down no longer takes focus from the wheel",
      combo.focusPolicy() == Qt.FocusPolicy.StrongFocus, str(combo.focusPolicy()))
check("a number box no longer takes focus from the wheel",
      spin.focusPolicy() == Qt.FocusPolicy.StrongFocus, str(spin.focusPolicy()))


def wheel():
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def press():
    return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


combo.hasFocus = lambda: True          # what Qt's WheelFocus used to do
check("an untouched drop-down ignores the wheel even when it has focus",
      guard.eventFilter(combo, wheel()) is True)
guard.eventFilter(combo, press())
check("after a click the wheel works on it",
      guard.eventFilter(combo, wheel()) is False)
guard.eventFilter(combo, QEvent(QEvent.Type.Leave))
check("once the pointer has left, the wheel stops working on it again",
      guard.eventFilter(combo, wheel()) is True)
# A spin box is clicked on its inner line edit: the click still arms the box.
spin.hasFocus = lambda: True
guard.eventFilter(spin.lineEdit(), press())
check("a click on a number box's text arms the box",
      guard.eventFilter(spin, wheel()) is False)

# ── 2. the INFO line ─────────────────────────────────────────────────────────
w = app_main.CSSLoggerWidget()
w._request_reload = lambda *a, **k: None
w.resize(1400, 900)
lbl = w._lbl_status
h0 = lbl.height() or lbl.maximumHeight()
long_pv = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy_and_some_more_text_to_make_it_long"
msg = ("Loaded 12 PVs: " + ", ".join([long_pv] * 6) + " ⚠ 3 signals incomplete")
lbl.setText(msg)
check("text() returns exactly what was set", lbl.text() == msg)
check("the INFO line asks for no width of its own",
      lbl.sizePolicy().horizontalPolicy() == app_main.QSizePolicy.Policy.Ignored)
check("the INFO line keeps a fixed height",
      lbl.minimumHeight() == lbl.maximumHeight() and lbl.maximumHeight() == h0,
      f"{lbl.minimumHeight()}..{lbl.maximumHeight()} vs {h0}")
check("the sidebar's scrollbar is always there (width never changes)",
      w._sidebar_widget.parentWidget().parentWidget().verticalScrollBarPolicy()
      == Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

# ── 3. the Live button sits next to Time window ──────────────────────────────
row = w._btn_live.parentWidget().layout()
same_row = None
for i in range(row.count()):
    it = row.itemAt(i)
    lay_i = it.layout() if it else None
    if lay_i is not None:
        widgets = [lay_i.itemAt(j).widget() for j in range(lay_i.count())]
        if w._btn_live in widgets:
            same_row = [x.text() for x in widgets if x is not None]
check("Live is in the same row as the Time window button",
      same_row is not None and any("Time window" in t for t in same_row), str(same_row))

# ── 4. PV list names ─────────────────────────────────────────────────────────
PVS = ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"]
w._pv_list.clear()
for pv in PVS:
    w._pv_list.addItem(w._make_pv_item(pv))
w._sync_pv_list_customs()
check("the list still hands the PVs to the archiver",
      w._real_pv_names() == PVS, str(w._real_pv_names()))
check("a row shows the short name, the PV is its tooltip",
      w._pv_list.item(0).text() == app_main.shorten_pv_name(PVS[0])
      and PVS[0] in w._pv_list.item(0).toolTip())
w._set_pv_display_name(PVS[1], "Beam B")
check("a rename shows in the list", w._pv_list.item(1).text() == "Beam B")
check("a rename is the table / graph name too", w._col_label(PVS[1]) == "Beam B")
check("a rename is remembered for the next start",
      w._pv_display_names.get(PVS[1]) == "Beam B")
check("the PV behind a renamed row is unchanged", w._real_pv_names() == PVS)
w._set_pv_display_name(PVS[1], "")
check("an empty name goes back to the automatic one",
      w._col_label(PVS[1]) == app_main.shorten_pv_name(PVS[1])
      and PVS[1] not in w._pv_display_names)
w._pv_list.clearSelection(); w._pv_list.item(2).setSelected(True)
w._move_selected_pvs(-1)
check("Move up moves the PV one place up",
      w._real_pv_names() == [PVS[0], PVS[2], PVS[1]], str(w._real_pv_names()))
w._move_selected_pvs(-1); w._move_selected_pvs(-1)
check("Move up stops at the top",
      w._real_pv_names() == [PVS[2], PVS[0], PVS[1]], str(w._real_pv_names()))
# Rows added as plain text (older code) are still read as PVs.
w._pv_list.addItem("L3-TEST-D:Energy")
check("a plain-text row still counts as a PV",
      "L3-TEST-D:Energy" in w._real_pv_names())

# ── 5. conditions ────────────────────────────────────────────────────────────
N = app_main._cond_normalize
c = N({"pv": "X", "min": 1.0, "max": 5.0})
check("old {min, max} loads as Between", c["kind"] == "between"
      and (c["min"], c["max"]) == (1.0, 5.0) and c["enabled"])
c = N({"pv": "X", "target": 70.0, "tol": 10.0})
check("old PV-time target ± % loads as Target ± %", c["kind"] == "pct"
      and abs(c["min"] - 63.0) < 1e-9 and abs(c["max"] - 77.0) < 1e-9)
c = N({"pv": "X", "kind": "abs", "a": 10.0, "b": 2.0})
check("Target ± value", (c["min"], c["max"]) == (8.0, 12.0))
c = N({"pv": "X", "kind": "above", "a": 3.0})
check("At least", (c["min"], c["max"]) == (3.0, None))
c = N({"pv": "X", "kind": "below", "a": 3.0})
check("At most", (c["min"], c["max"]) == (None, 3.0))
c = N({"pv": "X", "kind": "between", "a": 9.0, "b": 2.0})
check("Between with the numbers swapped is still a range",
      (c["min"], c["max"]) == (2.0, 9.0))
c = N({"pv": "X", "kind": "pct", "a": -50.0, "b": 10.0})
check("Target ± % around a negative target",
      abs(c["min"] + 55.0) < 1e-9 and abs(c["max"] + 45.0) < 1e-9, str(c))

applied = []
dlg = app_main._ConditionsDialog([], [("A name", "PV:A"), ("B name", "PV:B")],
                                 on_change=applied.append)
rec = dlg.editor.add_row()
rec["kind"].setCurrentIndex(rec["kind"].findData("above"))
rec["a"].setText("4")
check("the second box hides for a one-number kind", not rec["b"].isVisibleTo(dlg))
dlg.editor._timer.stop(); dlg._changed()
check("the Conditions window applies without OK",
      applied and applied[-1][0]["min"] == 4.0 and applied[-1][0]["pv"] == "PV:A",
      str(applied[-1:] if applied else None))
rec["enabled"].setChecked(False)
dlg.editor._timer.stop(); dlg._changed()
w._set_conditions(applied[-1])
check("a switched-off condition is kept but does not filter",
      w._conditions == [] and len(w._conditions_all) == 1)
dlg.close()

# A new PV Time row starts off and switches itself on when a limit is typed.
ed = w._pv_time_cond_editor
r = ed.add_row()
check("a new PV Time condition starts switched off", not r["enabled"].isChecked())
r["a"].setText("1")
check("typing a limit switches it on", r["enabled"].isChecked())
r["enabled"].click()
r["b"].setText("2")
check("switched off by hand, it stays off", not r["enabled"].isChecked())

# ── 6. PV Time Plot redraws on its own ───────────────────────────────────────
w._pv_time_auto_ready = True
w._pv_time_tab_visible = lambda: True
w._pv_time_auto_timer.stop()
w._pv_time_mode_combo.setCurrentIndex(1)
check("changing the mode asks for a redraw", w._pv_time_auto_timer.isActive())
w._pv_time_auto_timer.stop()
r["b"].setText("3")
ed._timer.stop(); ed.changed.emit()
check("changing a condition asks for a redraw", w._pv_time_auto_timer.isActive())
w._pv_time_auto_timer.stop()
w._pv_time_tab_visible = lambda: False
w._pv_time_mode_combo.setCurrentIndex(0)
check("a hidden tab only notes that it is out of date",
      not w._pv_time_auto_timer.isActive() and w._pv_time_stale)

# ── 7. the bindings table keeps its place ────────────────────────────────────
customs = [{"name": f"C{i}", "expr": "A+B+C", "bindings":
            {"A": PVS[0], "B": PVS[1], "C": PVS[2]}} for i in range(12)]
channels = [(lt, pv, pv, True) for lt, pv in zip("ABC", PVS)]
cdlg = app_main._CustomPVDialog(customs, channels)
cdlg.resize(900, 780)
cdlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
cdlg.show(); app.processEvents()
bar = cdlg._bind_tbl.verticalScrollBar()
bar.setValue(bar.maximum() // 2 or 1)
before = bar.value()
cdlg._refresh_bindings_table()
for _ in range(5):
    app.processEvents()
import time as _t; _t.sleep(0.05); app.processEvents()
check("a rebuild of the bindings table keeps its scroll position",
      before > 0 and bar.value() == before, f"{before} -> {bar.value()}")
cdlg.hide()

print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
