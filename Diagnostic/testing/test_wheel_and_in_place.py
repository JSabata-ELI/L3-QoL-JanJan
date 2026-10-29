"""Diagnostic: the wheel guard, in-place edits and the debounced save.

Offscreen only — nothing is put on the real screen and nothing takes focus.
Run:  python testing/test_wheel_and_in_place.py
"""
import os
import sys
import time
import types
from collections import deque

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox,  # noqa: E402
                               QScrollArea, QTableView, QVBoxLayout, QWidget)

APP = QApplication.instance() or QApplication([])

import main as diag_main  # noqa: E402
import monitor_tab as mt  # noqa: E402
from alerting import AlertLevel  # noqa: E402

diag_main.install_wheel_guard(APP)

FAILS = []


def check(what, got, want=True):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {what}" + ("" if ok else f"  got {got!r} want {want!r}"))
    if not ok:
        FAILS.append(what)


def pump(ms=0):
    end = time.perf_counter() + ms / 1000
    while True:
        APP.processEvents()
        if time.perf_counter() >= end:
            break
        time.sleep(0.01)


def wheel(w, up=True):
    pos = QPointF(w.width() / 2, w.height() / 2)
    ev = QWheelEvent(pos, w.mapToGlobal(pos), QPoint(0, 0),
                     QPoint(0, 120 if up else -120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(w, ev)


def click(w):
    pos = QPointF(w.width() / 2, w.height() / 2)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, pos, w.mapToGlobal(pos),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)
    ev = QMouseEvent(QEvent.Type.MouseButtonRelease, pos, w.mapToGlobal(pos),
                     Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(w, ev)


# ---------------------------------------------------------------------------
print("the wheel guard")
host = QWidget()
host.setAttribute(Qt.WA_ShowWithoutActivating, True)
area = QScrollArea(host)
inner = QWidget()
lay = QVBoxLayout(inner)
combo = QComboBox()
combo.addItems(["a", "b", "c", "d"])
combo.setCurrentIndex(1)
spin = QDoubleSpinBox()
spin.setValue(5.0)
lay.addWidget(combo)
lay.addWidget(spin)
area.setWidget(inner)
host.resize(300, 200)
area.resize(300, 200)
host.show()
pump()

check("plain combo became StrongFocus", combo.focusPolicy(), Qt.FocusPolicy.StrongFocus)
wheel(combo)
check("an untouched combo ignores the wheel", combo.currentIndex(), 1)
wheel(spin)
check("an untouched spin box ignores the wheel", spin.value(), 5.0)

combo.setFocus(Qt.OtherFocusReason)       # what a freshly opened dialog does
pump()
wheel(combo, up=False)
check("focus handed out by a dialog does not arm it", combo.currentIndex(), 1)


# Offscreen never activates a window, so real focus cannot be had without
# stealing it, and its popup sends focus events a real window does not. The
# focus is stood in for the guard's own question, the popup is kept shut and
# the focus traffic of a real pick (out to the list, back in) is sent by hand.
from PySide6.QtGui import QFocusEvent  # noqa: E402
combo.showPopup = lambda: None
combo.hasFocus = lambda: True


def focus(w, kind, reason):
    QApplication.sendEvent(w, QFocusEvent(kind, reason))


click(combo)
focus(combo, QEvent.Type.FocusOut, Qt.FocusReason.PopupFocusReason)
focus(combo, QEvent.Type.FocusIn, Qt.FocusReason.PopupFocusReason)
wheel(combo, up=False)
check("a clicked combo answers the wheel, also after its list was open",
      combo.currentIndex(), 2)
QApplication.sendEvent(combo, QEvent(QEvent.Type.Leave))
wheel(combo, up=False)
check("the pointer leaving does not deselect it: back over it, the wheel works",
      combo.currentIndex(), 3)
combo.setCurrentIndex(2)
focus(combo, QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason)
wheel(combo, up=False)
check("after another control was selected, it ignores the wheel again",
      combo.currentIndex(), 2)
click(combo)
focus(combo, QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason)
wheel(combo, up=False)
check("switching to another program and back keeps it selected",
      combo.currentIndex(), 3)
mt_combo = None

line = spin.lineEdit()
click(line)                    # the click lands on the spin box's text field
spin.hasFocus = lambda: True
check("a click on its text field arms the spin box",
      bool(spin.property("wheelReady")))
wheel(spin)
check("a clicked spin box answers the wheel", spin.value() != 5.0)
host.hide()


# ---------------------------------------------------------------------------
NOW = 1_000_000 * 1_000_000_000
mt.api.now_ns = lambda: NOW


def _pvs():
    rule = mt.PVConfig(
        name="RULED", display_name="Ruled", units="DegC", group="G",
        warn_low=7.5, warn_high=24.0, alarm_low=7.0, alarm_high=25.0,
        gate_pvs=["GATE"],
        profiles=[{"label": "r1", "conds": [[1.0, 1.0]],
                   "warn_low": 15.0, "warn_high": 16.0,
                   "alarm_low": 14.0, "alarm_high": 17.0}])
    other = mt.PVConfig(
        name="OTHER", display_name="Other", units="DegC", group="G",
        warn_high=24.0, alarm_high=25.0, gate_pvs=["GATE"],
        profiles=[{"label": "o1", "conds": [[1.0, 1.0]],
                   "warn_high": 30.0, "alarm_high": 31.0}])
    plain = mt.PVConfig(name="PLAIN", display_name="Plain", group="H",
                        warn_high=10.0, alarm_high=20.0)
    return [rule, other, plain]


def _win():
    pvs = _pvs()
    runtime = {}
    for p in pvs:
        rt = mt.PVRuntime(current_value=30.0,
                          history=deque([(NOW, 30.0)]), last_update_ns=NOW,
                          data_ts_ns=NOW)
        rt.live_level = AlertLevel.ALARM
        runtime[p.name] = rt
    win = types.SimpleNamespace(
        settings=dict(mt.DEFAULT_SETTINGS), pvs=pvs, runtime=runtime,
        _dep_combos={}, _gate_values={}, logged=[], graph_calls=[])
    win._log = lambda m: win.logged.append(m)
    win._update_status = lambda: None
    win.graph = types.SimpleNamespace(
        refresh_data=lambda: win.graph_calls.append("refresh_data"),
        redraw=lambda: win.graph_calls.append("redraw"))
    win.model = mt.PVTableModel(pvs, runtime)
    win.model.reset()
    win.table = QTableView()
    win.table.setModel(win.model)
    for name in ("_reevaluate_pv", "_apply_pv_edit", "_rebuild_dep_combo",
                 "_make_dep_combo", "_fit_dep_column", "_refresh_dep_combos",
                 "_install_dep_combos", "_active_limits_label",
                 "_on_dep_combo_changed", "_match_profile",
                 "_check_limits_change", "_gate_value"):
        if hasattr(mt.MonitorWidget, name):
            setattr(win, name, types.MethodType(getattr(mt.MonitorWidget, name), win))
    for name in ("_limits_key", "_limits_label"):
        setattr(win, name, getattr(mt.MonitorWidget, name))
    win._install_dep_combos()
    return win


print("\nlimits are judged at once, in place")
win = _win()
plain = win.pvs[2]
resets, changed = [], []
win.model.modelReset.connect(lambda: resets.append(1))
win.model.dataChanged.connect(lambda a, b: changed.append((a.row(), b.row())))
other_combo = win._dep_combos["OTHER"]

plain.warn_high, plain.alarm_high = 40.0, 50.0      # what the popup's Save does
win._apply_pv_edit(plain)
check("the State verdict follows the new limits at once",
      win.runtime["PLAIN"].live_level, AlertLevel.OK)
check("no table rebuild", len(resets), 0)
rows = {r for r, _ in changed}
plain_row = next(r for r, (k, ref) in enumerate(win.model._display)
                 if k == "pv" and ref is plain)
check("its own row was repainted", plain_row in rows)
check("other PVs' rows were left alone",
      all(win.model._display[r][0] != "pv" or win.model._display[r][1] is plain
          for r in rows))
check("the graph was asked for the new lines", "refresh_data" in win.graph_calls)
check("the other PV's dropdown is the same widget", win._dep_combos["OTHER"] is other_combo)

ruled = win.pvs[0]
ruled.profiles.append({"label": "r2", "conds": [[0.0, 0.0]],
                       "warn_high": 50.0, "alarm_high": 60.0})
win._apply_pv_edit(ruled)
check("an edited PV's dropdown shows its new rule",
      win._dep_combos["RULED"].count(), 4)
check("still no table rebuild", len(resets), 0)

print("\npinning a rule in the dropdown")
changed.clear()
win._on_dep_combo_changed("RULED", 2)               # pin "r1": alarm 14..17
check("pinned limits judged at once",
      win.runtime["RULED"].live_level, AlertLevel.ALARM)
win._on_dep_combo_changed("RULED", 3)               # pin "r2": alarm high 60
check("the second rule judged at once",
      win.runtime["RULED"].live_level, AlertLevel.OK)
check("only its row (and its header) repainted",
      {r for r, _ in changed} <= {0, 1}, True)

print("\nno restyling when nothing changed")
calls = []
c = win._dep_combos["OTHER"]
c.setStyleSheet = lambda s, _c=calls: _c.append(s)
win._refresh_dep_combos()
win._refresh_dep_combos()
check("a poll with no change restyles nothing", len(calls), 0)

print("\nthe local save is coalesced")
writes = []
real_save = mt.save_config
mt.save_config = lambda data: writes.append(data)
try:
    from PySide6.QtCore import QTimer
    sw = types.SimpleNamespace(settings={}, pvs=_pvs(), shared_ok=False,
                               shared_file=None)
    sw._local_timer = QTimer()
    sw._local_timer.setSingleShot(True)
    sw._schedule_shared_write = lambda: None
    sw._flush_local_write = types.MethodType(mt.MonitorWidget._flush_local_write, sw)
    sw._local_timer.timeout.connect(sw._flush_local_write)
    sw.persist = types.MethodType(mt.MonitorWidget.persist, sw)
    for _ in range(10):
        sw.persist()
    check("nothing written during the burst", len(writes), 0)
    pump(mt.LOCAL_WRITE_DEBOUNCE_MS + 200)
    check("one write after it", len(writes), 1)
    sw.persist()
    sw._flush_local_write()
    pump(mt.LOCAL_WRITE_DEBOUNCE_MS + 200)
    check("a flush (shutdown) writes now and cancels the pending one", len(writes), 2)
finally:
    mt.save_config = real_save

print()
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
