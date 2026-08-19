"""Tests for the "not updating" (frozen-value) check in the PV Monitor tab.

Covers the part that lives in monitor_tab.py: how a poll result turns into a
frozen verdict, when the one-shot notification fires, and how the table renders
it. The pure detector itself is tested in test_alerting.py.

Qt is imported but no window is shown (offscreen platform), and nothing here
touches the archiver, the network share or a notification channel: the widget
methods under test are called on a stand-in object carrying just the settings
they read.
"""

import os
import sys
import types
from collections import deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import monitor_tab as mt  # noqa: E402
from alerting import AlertLevel  # noqa: E402

SEC = 1_000_000_000
MIN = 60 * SEC
NOW = 1_000_000 * SEC

_APP = QApplication.instance() or QApplication([])


def _win(**overrides):
    """Stand-in for MonitorWidget carrying only what the frozen check reads."""
    settings = dict(mt.DEFAULT_SETTINGS)
    settings.update(overrides)
    win = types.SimpleNamespace(settings=settings, pvs=[], runtime={},
                               _monitoring=True, sent=[])
    for name in ("_frozen_check_on", "_sample_age_limit_s", "_update_frozen",
                 "_check_frozen_alerts"):
        setattr(win, name,
                types.MethodType(getattr(mt.MonitorWidget, name), win))
    # Record dispatches instead of rendering a plot and posting it anywhere.
    win._send_frozen_alert = types.MethodType(
        lambda self, pv, rt, level: self.sent.append((pv.name, level)), win)
    return win


def _rt(history, value, data_ts_ns=NOW):
    return mt.PVRuntime(current_value=value, history=deque(history),
                        last_update_ns=data_ts_ns, data_ts_ns=data_ts_ns)


def _flat(value, hours, n=60, now_ns=NOW):
    span = int(hours * 3600 * SEC)
    start = now_ns - span
    return [(start + int(i / (n - 1) * span), value) for i in range(n)]


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def test_constant_value_is_reported_as_frozen():
    win, pv = _win(), mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 60), 16.3)
    win._update_frozen(pv, rt, NOW)
    assert rt.frozen
    assert "unchanged for 2 d 12 h" in rt.frozen_reason
    assert not rt.frozen_bounded          # constant across all data kept


def test_moving_value_is_not_frozen():
    win, pv = _win(), mt.PVConfig(name="CHILLER:Temp", enabled=True)
    hist = [(NOW - i * MIN, 16.3 + i * 0.01) for i in range(300)]
    rt = _rt(hist, 16.3)
    win._update_frozen(pv, rt, NOW)
    assert not rt.frozen and rt.frozen_reason == ""


def test_no_reading_is_no_data_not_frozen():
    """Losing the data is the existing 'no data' state; don't relabel it."""
    win, pv = _win(), mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 60), None)
    win._update_frozen(pv, rt, NOW)
    assert not rt.frozen and rt.frozen_since_ns == 0


def test_stale_newest_sample_is_frozen_even_if_values_differ():
    """Archiver answers, but with data from hours ago."""
    win, pv = _win(), mt.PVConfig(name="CHILLER:Temp", enabled=True)
    old = NOW - 3 * 3600 * SEC
    hist = [(old - i * MIN, 16.3 + i * 0.1) for i in range(50)]
    rt = _rt(hist, 16.3, data_ts_ns=old)
    win._update_frozen(pv, rt, NOW)
    assert rt.frozen and "newest archive sample is 3 h 0 min old" in rt.frozen_reason


def test_fresh_sample_within_limit_is_not_flagged():
    win, pv = _win(), mt.PVConfig(name="CHILLER:Temp", enabled=True)
    hist = [(NOW - i * MIN, 16.3 + i * 0.1) for i in range(50)]
    rt = _rt(hist, 16.3, data_ts_ns=NOW - 30 * SEC)
    win._update_frozen(pv, rt, NOW)
    assert not rt.frozen


def test_per_pv_optout_and_global_switch():
    off_pv = mt.PVConfig(name="HALL:Enable", enabled=True, frozen_check=False)
    rt = _rt(_flat(1.0, 60), 1.0)
    _win()._update_frozen(off_pv, rt, NOW)
    assert not rt.frozen

    on_pv = mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt2 = _rt(_flat(16.3, 60), 16.3)
    _win(frozen_check_enabled=False)._update_frozen(on_pv, rt2, NOW)
    assert not rt2.frozen


def test_shorter_freeze_than_configured_is_not_flagged():
    win = _win(frozen_after_minutes=120)
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 1), 16.3)        # 1 h of the same value
    win._update_frozen(pv, rt, NOW)
    assert not rt.frozen
    # Same data, limit lowered to 30 min -> now it counts.
    win.settings["frozen_after_minutes"] = 30
    win._update_frozen(pv, rt, NOW)
    assert rt.frozen


# ---------------------------------------------------------------------------
# Notification: once per episode, once on recovery
# ---------------------------------------------------------------------------

def _armed_win(pv, rt, **overrides):
    win = _win(**overrides)
    win.pvs = [pv]
    win.runtime = {pv.name: rt}
    return win


def test_alert_fires_once_per_episode_then_on_recovery():
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 60), 16.3)
    win = _armed_win(pv, rt)
    win._update_frozen(pv, rt, NOW)
    win._check_frozen_alerts()
    win._check_frozen_alerts()            # still frozen -> no repeat
    assert win.sent == [(pv.name, AlertLevel.WARNING)]

    rt.history.append((NOW + MIN, 17.1))
    rt.current_value = 17.1
    rt.data_ts_ns = NOW + MIN
    win._update_frozen(pv, rt, NOW + MIN)
    win._check_frozen_alerts()
    assert win.sent[-1] == (pv.name, AlertLevel.OK)


def test_losing_the_data_does_not_look_like_a_recovery():
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 60), 16.3)
    win = _armed_win(pv, rt)
    win._update_frozen(pv, rt, NOW)
    win._check_frozen_alerts()
    rt.current_value = None               # archiver went quiet
    win._update_frozen(pv, rt, NOW + MIN)
    win._check_frozen_alerts()
    assert win.sent == [(pv.name, AlertLevel.WARNING)]


def test_no_alert_when_monitoring_off_or_alerting_disabled():
    for kwargs, mon, enabled in (({}, False, True),
                                 ({"frozen_alert_enabled": False}, True, True),
                                 ({}, True, False)):
        pv = mt.PVConfig(name="CHILLER:Temp", enabled=enabled)
        rt = _rt(_flat(16.3, 60), 16.3)
        win = _armed_win(pv, rt, **kwargs)
        win._monitoring = mon
        win._update_frozen(pv, rt, NOW)
        win._check_frozen_alerts()
        assert rt.frozen                  # still visible in the table
        assert win.sent == []             # but nothing is sent


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def _model(pv, rt, monitoring=True):
    m = mt.PVTableModel([pv], {pv.name: rt})
    m.monitoring = monitoring
    return m


def test_state_cell_shows_not_updating():
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=True)
    rt = _rt(_flat(16.3, 60), 16.3)
    _win()._update_frozen(pv, rt, NOW)
    m = _model(pv, rt)
    idx = m.index(0, mt.COL_STATE)
    assert m.data(idx, Qt.DisplayRole) == mt.FROZEN_LABEL
    assert m.data(idx, Qt.BackgroundRole) == QColor(mt.FROZEN_COLOR)
    assert m.data(idx, Qt.ForegroundRole) == QColor("white")
    assert "NOT UPDATING" in m.data(idx, Qt.ToolTipRole)
    # The reading is still shown, but marked as not live.
    val = m.index(0, mt.COL_VALUE)
    assert m.data(val, Qt.DisplayRole) == "16.3"
    assert m.data(val, Qt.FontRole).italic()


def test_state_cell_shows_not_updating_for_pvs_with_alerting_off():
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=False)
    rt = _rt(_flat(16.3, 60), 16.3)
    _win()._update_frozen(pv, rt, NOW)
    m = _model(pv, rt)
    assert m.data(m.index(0, mt.COL_STATE), Qt.DisplayRole) == mt.FROZEN_LABEL


def test_healthy_pv_state_cell_unchanged():
    pv = mt.PVConfig(name="CHILLER:Temp", enabled=True, warn_high=30.0)
    hist = [(NOW - i * MIN, 16.3 + i * 0.01) for i in range(300)]
    rt = _rt(hist, 16.3)
    rt.live_level = AlertLevel.OK
    _win()._update_frozen(pv, rt, NOW)
    m = _model(pv, rt)
    idx = m.index(0, mt.COL_STATE)
    assert m.data(idx, Qt.DisplayRole) == "ok"
    assert m.data(idx, Qt.FontRole) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
