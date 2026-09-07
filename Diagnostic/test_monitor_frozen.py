"""Tests for the two "what is on screen is not live" checks in the PV Monitor tab.

Covers the part that lives in monitor_tab.py:

* the frozen-value check — one PV keeps answering with a dead reading: how a
  poll result turns into a frozen verdict, when the one-shot notification fires,
  and how the table renders it. The pure detector itself is tested in
  test_alerting.py;
* the refresh watchdog — this program itself stops reading, so every value it
  shows and every answer it gives is a leftover: when it notices, what it
  announces, and how /status, /alarms, the State column and a plotted chart say
  so instead of reporting "ok".

One more /status detail rides along here because this is where the reply is
rendered offline: the canteen sign-in line, which is now the ONLY place the app
mentions an expired OKbase session — nothing is posted to the chat about it.

Qt is imported but no window is shown (offscreen platform), and nothing here
touches the archiver, the network share or a notification channel: the widget
methods under test are called on a stand-in object carrying just the settings
they read.
"""

import contextlib
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


# ---------------------------------------------------------------------------
# Refresh watchdog: the program itself stopped reading
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _clock(ns):
    """Freeze the wall clock the monitor reads, so a stall can be staged."""
    real = mt.api.now_ns
    mt.api.now_ns = lambda: ns
    try:
        yield
    finally:
        mt.api.now_ns = real


def _refresh_win(pvs=(), runtime=None, last_ok_ns=NOW, **overrides):
    """Stand-in carrying what the refresh watchdog and the chat replies read."""
    win = _win(**overrides)
    win.pvs = list(pvs)
    win.runtime = dict(runtime or {})
    win._last_poll_ok_ns = last_ok_ns
    win._refresh_bad = False
    win._refresh_bad_since_ns = 0
    win._refresh_alert_sent = False
    win._poll_inflight = False
    win._poll_started_ns = 0
    win._mem_start_ns = NOW - 3600 * SEC
    win.logged = []
    win.marked = []
    win.model = types.SimpleNamespace(stale=False, stale_since="",
                                      refresh_all=lambda: None)
    win.graph = types.SimpleNamespace(
        set_stale_note=lambda text: win.marked.append(text))
    for name in ("_refresh_limit_s", "_refresh_age_s", "_refresh_fault",
                 "_refresh_alert_limit_s",
                 "_check_refresh_health", "_mark_stale_ui", "_pv_stale_note",
                 "_status_line", "_cmd_status", "_cmd_alarms",
                 "_refresh_note_short", "_freshness_header",
                 "_freshness_footer", "_resolve_pvs", "_find_pv",
                 "_menu_signin_note"):
        setattr(win, name, types.MethodType(getattr(mt.MonitorWidget, name), win))
    win._log = types.MethodType(lambda self, m: self.logged.append(m), win)
    win._update_status = types.MethodType(lambda self: None, win)
    # Record the chat message instead of rendering and posting it.
    win._send_refresh_alert = types.MethodType(
        lambda self, level, reason: self.sent.append((level, reason)), win)
    return win


def _ok_pv(value=16.3, data_age_s=30, name="CHILLER:Temp"):
    pv = mt.PVConfig(name=name, enabled=True, warn_high=30.0)
    rt = _rt([(NOW - i * MIN, value) for i in range(5)], value,
             data_ts_ns=NOW - int(data_age_s) * SEC)
    rt.live_level = AlertLevel.OK
    rt.alert.level = AlertLevel.OK
    return pv, rt


def test_a_pass_that_just_landed_is_no_fault():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        assert win._refresh_fault() == ""


def test_stall_is_reported_and_names_the_read_that_never_came_back():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    win._poll_inflight = True
    win._poll_started_ns = NOW
    later = NOW + 20 * MIN
    with _clock(later):
        fault = win._refresh_fault()
    assert "nothing has been read for 20 min" in fault
    assert "has not come back" in fault


def test_a_stall_shorter_than_the_self_healing_wait_is_left_alone():
    """The wedge watchdog in _start_poll writes a lost pass off after five
    intervals and starts a fresh one. A stall cured that way must not wake
    anybody, so the announcement waits longer than the cure does."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt}, poll_interval_s=30)
    with _clock(NOW + 2 * MIN):
        assert win._refresh_fault() == ""
    with _clock(NOW + 5 * MIN):
        assert win._refresh_fault() != ""


def test_settings_can_make_the_announcement_more_patient():
    """Settings owns the limit, so a 5 min setting must stay quiet at 4 min."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt}, poll_interval_s=30,
                       refresh_alarm_minutes=5.0)
    with _clock(NOW + 4 * MIN):
        assert win._refresh_fault() == ""
    with _clock(NOW + 6 * MIN):
        assert win._refresh_fault() != ""


def test_a_limit_the_poll_rhythm_could_not_meet_is_raised():
    """1 min with a 30 s poll would announce every stall the wedge watchdog is
    already curing, so the floor (five intervals) wins instead."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt}, poll_interval_s=30,
                       refresh_alarm_minutes=1.0)
    assert win._refresh_limit_s() >= 5 * 30
    with _clock(NOW + 2 * MIN):
        assert win._refresh_fault() == ""


def test_never_having_read_anything_counts_from_launch():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt}, last_ok_ns=0)
    with _clock(NOW):
        fault = win._refresh_fault()
    assert "no reading has completed since this program started" in fault


def test_a_short_stall_is_marked_but_not_said_in_the_chat():
    """The stall shows up on screen and in every reply straight away, and
    nothing is sent to the chat — neither when it starts nor when it ends. Most
    stalls are cured by the wedge watchdog seconds later, so the pair of
    messages only ever woke somebody up for nothing."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
        win._check_refresh_health()
    assert win.sent == []
    assert win.model.stale and win.marked[-1] != ""
    assert any(mt.NOT_REFRESHED_LABEL.upper() in m for m in win.logged)

    win._last_poll_ok_ns = NOW + 21 * MIN
    with _clock(NOW + 21 * MIN):
        win._check_refresh_health()
    assert win.sent == []
    assert not win.model.stale and win.marked[-1] == ""


def test_a_long_stall_is_said_in_the_chat_once_and_then_cleared():
    """Half an hour is not a stall curing itself, so the chat is told — once,
    however many times the check runs — and told again when reading resumes."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
    assert win.sent == []                     # marked, not announced yet
    with _clock(NOW + 31 * MIN):
        win._check_refresh_health()
        win._check_refresh_health()
    assert len(win.sent) == 1
    level, reason = win.sent[0]
    assert level == AlertLevel.ALARM
    assert "STOPPED READING" in reason and "31 min" in reason

    win._last_poll_ok_ns = NOW + 40 * MIN
    with _clock(NOW + 40 * MIN):
        win._check_refresh_health()
    assert len(win.sent) == 2
    level, reason = win.sent[1]
    assert level == AlertLevel.OK and "resumed" in reason


def test_the_chat_message_can_be_turned_off_and_retimed():
    pv, rt = _ok_pv()
    off = _refresh_win([pv], {pv.name: rt}, refresh_alert_minutes=0)
    with _clock(NOW + 5 * 60 * MIN):
        off._check_refresh_health()
    assert off.sent == []

    soon = _refresh_win([pv], {pv.name: rt}, refresh_alert_minutes=10)
    with _clock(NOW + 8 * MIN):
        soon._check_refresh_health()
    assert soon.sent == []
    with _clock(NOW + 11 * MIN):
        soon._check_refresh_health()
    assert len(soon.sent) == 1


def test_the_chat_message_never_beats_the_marking():
    """A chat wait shorter than the marking wait would ask for a message about
    a stall the program does not even consider one yet."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt}, refresh_alarm_minutes=10.0,
                       refresh_alert_minutes=1.0)
    assert win._refresh_alert_limit_s() >= win._refresh_limit_s()
    with _clock(NOW + 5 * MIN):
        win._check_refresh_health()
    assert win.sent == [] and not win.model.stale


def test_no_chat_message_while_alerting_is_stopped():
    """Monitoring off means no messages at all — the marking still shows."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    win._monitoring = False
    with _clock(NOW + 60 * MIN):
        win._check_refresh_health()
    assert win.sent == []
    assert win.model.stale


def test_stall_is_shown_even_when_alerting_is_stopped():
    """Alerting off must never silence the display — the operator still has to
    be able to see that the numbers are old."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    win._monitoring = False
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
    assert win.sent == []
    assert win.model.stale and win.marked[-1] != ""
    assert any(mt.NOT_REFRESHED_LABEL.upper() in m for m in win.logged)


def test_status_line_says_not_refreshed_instead_of_ok():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        assert win._status_line(pv).endswith("[ok]")
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
        line = win._status_line(pv)
    assert "[ok]" not in line
    assert mt.NOT_REFRESHED_LABEL in line and "nothing read for 20 min" in line


def test_status_reply_warns_above_the_values_and_dates_them():
    """One short line, above the values — read on a phone, a longer warning
    pushes the numbers themselves off the screen."""
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
        reply = win._cmd_status()
    head, _, rest = reply.partition("\n")
    assert mt.NOT_REFRESHED_LABEL.upper() in head       # warning comes first
    assert "20 min ago" in head                         # and how old they are
    assert rest.lstrip("\n").startswith("**Status")     # nothing else above
    assert "Values read at" in reply                    # and when they were read


def test_a_healthy_status_reply_still_dates_the_values():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        reply = win._cmd_status()
    assert reply.startswith("**Status")
    assert "Values read at" in reply
    assert mt.NOT_REFRESHED_LABEL not in reply


def test_status_carries_the_canteen_line_only_when_the_signin_was_refused():
    """Nothing announces an expired canteen sign-in any more, so /status is
    where it has to show up — and only for a real refusal, never for a portal
    that merely did not answer, and never on a single-PV reply."""
    import okbase_menu as om
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    win.settings["okbase_enabled"] = True

    win._menu_error = ""
    with _clock(NOW):
        assert "Canteen sign-in" not in win._cmd_status()

    win._menu_error = om.PORTAL_UNREACHABLE + " (HTTP 502)"
    with _clock(NOW):
        assert "Canteen sign-in" not in win._cmd_status()

    win._menu_error = om.SESSION_EXPIRED
    with _clock(NOW):
        assert "Canteen sign-in expired" in win._cmd_status()
        assert "Canteen sign-in" not in win._cmd_status([pv.name])

    win.settings["okbase_enabled"] = False
    with _clock(NOW):
        assert "Canteen sign-in" not in win._cmd_status()


def test_alarms_will_not_claim_all_clear_while_the_values_are_old():
    pv, rt = _ok_pv()
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        assert win._cmd_alarms().startswith("✅")
    with _clock(NOW + 20 * MIN):
        win._check_refresh_health()
        reply = win._cmd_alarms()
    assert "✅" not in reply
    assert "cannot tell" in reply


def test_one_pv_with_an_old_reading_is_caught_while_the_program_runs():
    """The program is reading fine; this PV's own data stopped arriving. The
    check is made when asked, not trusted from the last poll."""
    pv, rt = _ok_pv(data_age_s=3 * 3600)
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        assert win._refresh_fault() == ""            # the program is healthy
        line = win._status_line(pv)
        alarms = win._cmd_alarms()
    assert mt.NOT_REFRESHED_LABEL in line and "3 h 0 min ago" in line
    assert mt.NOT_REFRESHED_LABEL in alarms


def test_a_pv_opted_out_of_the_frozen_check_is_not_second_guessed():
    pv, rt = _ok_pv(data_age_s=3 * 3600)
    pv.frozen_check = False
    win = _refresh_win([pv], {pv.name: rt})
    with _clock(NOW):
        assert win._pv_stale_note(pv, rt) == ""


def test_state_cell_says_not_refreshed():
    pv, rt = _ok_pv()
    m = _model(pv, rt)
    assert m.data(m.index(0, mt.COL_STATE), Qt.DisplayRole) == "ok"
    m.stale = True
    m.stale_since = "08:15:02"
    idx = m.index(0, mt.COL_STATE)
    assert m.data(idx, Qt.DisplayRole) == mt.NOT_REFRESHED_LABEL
    assert m.data(idx, Qt.BackgroundRole) == QColor(mt.FROZEN_COLOR)
    assert m.data(idx, Qt.ForegroundRole) == QColor("white")
    assert "08:15:02" in m.data(idx, Qt.ToolTipRole)


# ---------------------------------------------------------------------------
# The plotted chart says whether it is current
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _canned_series(newest_ns, n=20, step_ns=MIN):
    """Serve a fixed run of samples instead of asking the archiver."""
    real = mt._fetch_chart_data

    def fake(series, start_ns, end_ns, timeout, **kw):
        t_ns = [newest_ns - i * step_ns for i in range(n - 1, -1, -1)]
        datas = [mt.chart_history.make_raw_series(
            s.pv_name, s.display_name, t_ns,
            [16.0 + 0.1 * i for i in range(n)], start_ns, end_ns, "°C")
            for s in series]
        return datas, mt.chart_history.FetchReport(n_requests=1, n_done=1,
                                                   n_pvs=len(series))

    mt._fetch_chart_data = fake
    try:
        yield
    finally:
        mt._fetch_chart_data = real


def _chart_info(newest_ns, end_ns, stale_after_s):
    info = {}
    with _canned_series(newest_ns):
        png = mt.render_chart_png(
            [mt.ChartSeries("CHILLER:Temp", "Chiller 1")],
            end_ns - 12 * 3600 * SEC, end_ns, 5.0, "last 12 h",
            stale_after_s=stale_after_s, out_info=info)
    assert png                      # a picture is still produced either way
    return info


def test_chart_that_stops_short_of_now_says_so_on_the_picture():
    info = _chart_info(NOW - 3 * 3600 * SEC, NOW, 300.0)
    assert "NOT CURRENT" in info["note"]
    assert "3 h 0 min before the end of the window" in info["note"]


def test_chart_running_up_to_now_carries_no_warning():
    info = _chart_info(NOW - 40 * SEC, NOW, 300.0)
    assert "note" not in info
    assert info["newest_ns"] == NOW - 40 * SEC


def test_a_window_in_the_past_is_never_called_out_of_date():
    """A curve ending at the right-hand edge of a 'yesterday 7-18' plot is what
    was asked for, not a fault."""
    end = NOW - 24 * 3600 * SEC
    info = _chart_info(end - 3 * 3600 * SEC, end, 0.0)
    assert "note" not in info


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
