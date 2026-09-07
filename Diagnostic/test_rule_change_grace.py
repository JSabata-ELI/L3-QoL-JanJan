"""Tests for the alerting grace that follows a change of the limits in force.

A PV with conditional rules is judged against a different band the moment its
dependency PVs move — and the measured value cannot have followed. The clearest
case is a chiller being switched on in the morning: its water is at ~20 °C, the
band that applies while it runs asks for 11.4, and it takes ten minutes of
perfectly correct behaviour to get there. Without a hold that start-up alarms
every single day.

Covered here: when the hold opens, what it does to a half-built alert state,
what the table and the bot say while it is open, that a value which really
misses the new band still alerts once the wait is over — and, honestly, the one
thing it does not fix (see the notes above the end-to-end tests).

Qt is imported but nothing is shown; the widget methods under test are called
on a stand-in carrying only the settings and the log they read.
"""

import contextlib
import os
import sys
import types
from collections import deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

import monitor_tab as mt  # noqa: E402
from alerting import AlertEvaluator, AlertLevel, EvalConfig  # noqa: E402

SEC = 1_000_000_000
MIN = 60 * SEC
NOW = 1_000_000 * SEC

# The real end-of-shift case: DA4 Chiller, global limits 7/22 - 7.5/24 while
# nothing matches, and a tight band once the 0.2 Hz rule takes over.
GLOBAL = dict(warn_low=7.5, warn_high=22.0, alarm_low=7.0, alarm_high=24.0)
SLOW_RULE = {"label": "0,2 Hz", "conds": [[1.0, 1.0], [0.2, 0.2]],
             "warn_low": 11.1, "warn_high": 11.7,
             "alarm_low": 10.8, "alarm_high": 12.0}
FAST_RULE = {"label": "3,3 Hz", "conds": [[1.0, 1.0], [2.0, 3.0]],
             "warn_low": 8.2, "warn_high": 8.8,
             "alarm_low": 7.9, "alarm_high": 9.1}


@contextlib.contextmanager
def _clock(ns):
    """Freeze the wall clock the runtime reads, so a grace can be staged."""
    real = mt.api.now_ns
    mt.api.now_ns = lambda: ns
    try:
        yield
    finally:
        mt.api.now_ns = real


def _pv(**over):
    d = dict(name="L3-UTIL-CHL03-004:Temp", display_name="DA4 Chiller",
             enabled=True, units="DegC",
             gate_pvs=["L3-SIS-KEY:HighPowerStatus",
                       "L3-TIMING-TIMING:SysRate"],
             profiles=[dict(SLOW_RULE), dict(FAST_RULE)], **GLOBAL)
    d.update(over)
    return mt.PVConfig(**d)


def _win(**over):
    """Stand-in for MonitorWidget carrying only what the grace check reads."""
    settings = dict(mt.DEFAULT_SETTINGS)
    settings.update(over)
    win = types.SimpleNamespace(settings=settings, logged=[])
    win._log = types.MethodType(lambda self, m: self.logged.append(m), win)
    win._check_limits_change = types.MethodType(
        mt.MonitorWidget._check_limits_change, win)
    win._limits_key = mt.MonitorWidget._limits_key
    win._limits_label = mt.MonitorWidget._limits_label
    return win


def _rt(value, profile=None):
    rt = mt.PVRuntime(current_value=value, history=deque([(NOW, value)]),
                      active_profile=profile)
    rt.live_level = AlertLevel.OK
    return rt


def _settle(win, pv, rt, at_ns):
    """One pass of the two lines _on_poll runs around the evaluator."""
    win._check_limits_change(pv, rt, at_ns)
    return rt.in_grace(at_ns)


# ---------------------------------------------------------------------------
# When the grace opens
# ---------------------------------------------------------------------------

def test_first_pass_never_opens_a_grace():
    """Launch is not a change — there is nothing to have changed from."""
    win, pv, rt = _win(), _pv(), _rt(12.4)
    assert not _settle(win, pv, rt, NOW)
    assert rt.limits_key


def test_a_rule_taking_over_holds_alerting():
    win, pv, rt = _win(), _pv(), _rt(12.4)
    _settle(win, pv, rt, NOW)                    # global limits, value fine
    rt.active_profile = pv.profiles[0]           # 0.2 Hz rule takes over
    assert _settle(win, pv, rt, NOW + MIN)
    assert rt.grace_until_ns == NOW + MIN + 20 * MIN
    assert "0,2 Hz" in rt.grace_reason
    assert any("held until" in m for m in win.logged)


def test_a_rule_dropping_away_holds_alerting_too():
    """High power off puts the global limits back — also a change of band."""
    win, pv = _win(), _pv()
    rt = _rt(14.1, pv.profiles[0])
    _settle(win, pv, rt, NOW)
    rt.active_profile = None
    assert _settle(win, pv, rt, NOW + MIN)


def test_the_same_numbers_under_a_different_rule_are_not_a_change():
    """The grace keys on the limits, not on which rule produced them, so a
    second rule with the same band must not restart the wait."""
    win, pv = _win(), _pv()
    twin = dict(SLOW_RULE, label="0,2 Hz (spare)")
    pv.profiles.append(twin)
    rt = _rt(12.4, pv.profiles[0])
    _settle(win, pv, rt, NOW)
    rt.active_profile = twin
    assert not _settle(win, pv, rt, NOW + MIN)


def test_a_steady_rule_never_opens_a_grace():
    win, pv = _win(), _pv()
    rt = _rt(11.4, pv.profiles[0])
    for i in range(10):
        assert not _settle(win, pv, rt, NOW + i * MIN)


def test_the_grace_can_be_switched_off():
    win, pv = _win(rule_change_grace_minutes=0), _pv()
    rt = _rt(12.4)
    _settle(win, pv, rt, NOW)
    rt.active_profile = pv.profiles[0]
    assert not _settle(win, pv, rt, NOW + MIN)
    assert not rt.grace_until_ns


def test_the_wait_ends_and_the_pv_is_armed_again():
    win, pv, rt = _win(), _pv(), _rt(12.4)
    _settle(win, pv, rt, NOW)
    rt.active_profile = pv.profiles[0]
    _settle(win, pv, rt, NOW + MIN)
    assert _settle(win, pv, rt, NOW + 15 * MIN)          # still holding
    assert not _settle(win, pv, rt, NOW + 25 * MIN)      # over
    assert rt.grace_until_ns == 0 and rt.grace_reason == ""


# ---------------------------------------------------------------------------
# What it does to a half-built alert state
# ---------------------------------------------------------------------------

def test_an_alert_nobody_saw_is_forgotten_on_the_change():
    """The old band had a settle window open but had announced nothing, so
    there is no episode to close — and no 'all clear' owed for it."""
    win, pv, rt = _win(), _pv(), _rt(12.4)
    _settle(win, pv, rt, NOW)
    rt.alert.level = AlertLevel.ALARM
    rt.alert.settle_until_ns = NOW + 7 * MIN
    rt.notify_status = "pending"
    rt.active_profile = pv.profiles[0]
    _settle(win, pv, rt, NOW + MIN)
    assert rt.alert.level == AlertLevel.OK
    assert rt.alert.settle_until_ns == 0
    assert rt.notify_status == ""


def test_an_announced_alarm_survives_the_change():
    """Somebody has been told about this one, so the episode stays open and its
    recovery still goes out — just after the wait, not during it."""
    win, pv, rt = _win(), _pv(), _rt(25.5)
    _settle(win, pv, rt, NOW)
    rt.alert.level = AlertLevel.ALARM
    rt.alert.first_notified_ns = NOW - 5 * MIN
    rt.alert.last_notified_ns = NOW - 5 * MIN
    rt.active_profile = pv.profiles[0]
    _settle(win, pv, rt, NOW + MIN)
    assert rt.alert.level == AlertLevel.ALARM
    assert rt.alert.first_notified_ns == NOW - 5 * MIN


# ---------------------------------------------------------------------------
# The chiller day, end to end through the evaluator
#
# What the archive says about 31.08.2026 (DA4, L3-UTIL-CHL03-004):
#   08:08:46  PumpON 1        — chiller started, water still at ~20 °C
#   ~08:15    Temp 11.4       — down to the setpoint, the band it is judged on
#   19:01:40  TempSP 11.4→15  — end of the day, setpoint let go
#   19:05:08  PumpON 1→0      — chiller switched off, temperature starts rising
#   19:10:35  ALARM sent      — 14.1 against the 0,2 Hz band's 12.0 alarm bound
#   19:10:17  HighPower 1→0   — the rule stops matching, Global 7–24 takes over
#   19:13:05  [OK] Recovered  — 14.1 is fine on the global limits
#
# Two separate things: the band stayed in force after the chiller was switched
# off (a missing dependency on PumpON), and a band that changes hands is
# instantly compared against a value that cannot have followed it (this grace).
# ---------------------------------------------------------------------------

def _run(win, pv, rt, ev, values, start_ns, step_ns=30 * SEC):
    """Poll `values` in, gated exactly as _on_poll gates the evaluator.
    Returns the notifications that would have been sent."""
    sent = []
    for i, val in enumerate(values):
        at = start_ns + i * step_ns
        rt.current_value = val
        rt.history.append((at, val))
        win._check_limits_change(pv, rt, at)
        if rt.in_grace(at):
            continue
        thr = (pv.profile_thresholds(rt.active_profile)
               if rt.active_profile is not None else pv.thresholds())
        note = ev.evaluate(rt.alert, val, thr, at)
        if note is not None:
            sent.append(note)
    return sent


def _ev(win):
    s = win.settings
    return AlertEvaluator(EvalConfig(
        debounce_count=int(s["debounce_count"]),
        renotify_cooldown_minutes=float(s["renotify_cooldown_minutes"]),
        recovery_notify=bool(s["recovery_notify"]),
        settle_minutes=float(s["settle_minutes"]),
        stable_seconds=float(s["stable_seconds"])))


def _cooldown(start=20.0, target=11.4, n=24):
    """A chiller pulling its water down after being switched on, roughly as the
    archive has it: ~20 °C to the setpoint over about ten minutes."""
    step = (start - target) / (n - 1)
    return [round(start - i * step, 1) for i in range(n)]


def test_the_morning_start_up_sends_nothing():
    """The case the grace is really for. With PumpON among the dependencies —
    the fix for the evening below — the tight band takes over the moment the
    chiller is switched on, at ~20 °C, and it takes ten minutes to get to 11.4.
    That is a chiller working perfectly."""
    win, pv, rt = _win(), _pv(), _rt(20.0)
    ev = _ev(win)
    assert _run(win, pv, rt, ev, [20.0] * 6, NOW) == []   # off: global limits
    rt.active_profile = pv.profiles[0]                    # PumpON 1 -> 0,2 Hz
    assert _run(win, pv, rt, ev, _cooldown(), NOW + 3 * MIN) == []
    assert _run(win, pv, rt, ev, [11.4] * 40, NOW + 16 * MIN) == []
    assert rt.alert.level == AlertLevel.OK


def test_without_the_grace_the_morning_start_up_alarms():
    """Kept so the reason for the grace cannot be quietly removed: the same
    perfectly normal start-up alarms as soon as the hold is off."""
    win, pv, rt = _win(rule_change_grace_minutes=0), _pv(), _rt(20.0)
    ev = _ev(win)
    _run(win, pv, rt, ev, [20.0] * 6, NOW)
    rt.active_profile = pv.profiles[0]
    sent = _run(win, pv, rt, ev, _cooldown() + [11.4] * 20, NOW + 3 * MIN)
    assert [n.level for n in sent][:1] == [AlertLevel.ALARM]


def test_the_grace_alone_does_not_cover_a_chiller_switched_off():
    """Honest about what this does not fix. On 31.08. nothing changed hands
    before the alarm: the chiller was switched off at 19:05 with the 0,2 Hz
    band still in force, and the temperature simply left it. The grace cannot
    help there — the band has to stop applying, which is what adding PumpON to
    the rule's dependencies does."""
    win, pv = _win(), _pv()
    rt = _rt(11.4, pv.profiles[0])
    ev = _ev(win)
    assert _run(win, pv, rt, ev, [11.4] * 10, NOW) == []
    warm = [11.4, 11.6, 12.1, 12.8, 13.4, 14.1] + [14.5] * 20
    sent = _run(win, pv, rt, ev, warm, NOW + 5 * MIN)
    assert [n.level for n in sent][:1] == [AlertLevel.ALARM]


def test_a_chiller_that_really_misses_the_new_band_still_alerts():
    """The grace is a delay, not an amnesty: half an hour later the value is
    still outside the band the rule asks for, and that is a real problem."""
    win, pv, rt = _win(), _pv(), _rt(20.0)
    ev = _ev(win)
    _run(win, pv, rt, ev, [20.0] * 5, NOW)
    rt.active_profile = pv.profiles[0]
    # Switched on, and still at 14.5 forty-five minutes later: it is not
    # cooling, which is worth waking somebody for.
    sent = _run(win, pv, rt, ev, [14.5] * 90, NOW + 5 * MIN)
    assert [n.level for n in sent][:1] == [AlertLevel.ALARM]
    assert "did not settle" in sent[0].reason


# ---------------------------------------------------------------------------
# What is on screen and in the chat while the wait is open
# ---------------------------------------------------------------------------

def test_the_state_cell_still_shows_where_the_value_sits():
    """Held does not mean green: the reading really is outside the new band and
    the cell must say so."""
    rt = _rt(14.5)
    rt.live_level = AlertLevel.ALARM
    rt.grace_until_ns = NOW + 10 * MIN
    with _clock(NOW):
        assert rt.display_level(True) == AlertLevel.ALARM


def test_the_alarm_status_cell_says_why_nothing_is_being_raised():
    pv, rt = _pv(), _rt(14.5)
    rt.live_level = AlertLevel.ALARM
    rt.grace_until_ns = NOW + 10 * MIN
    rt.grace_reason = "now 0,2 Hz"
    with _clock(NOW):
        text = mt._alarm_status_text(pv, rt)
        tip = mt._alarm_status_tooltip(pv, rt)
    assert text.startswith("new limits → ")
    assert "0,2 Hz" in tip and "held until" in tip


def test_the_cell_goes_quiet_once_the_wait_is_over():
    pv, rt = _pv(), _rt(11.4)
    rt.grace_until_ns = NOW + 10 * MIN
    with _clock(NOW + 11 * MIN):
        assert mt._alarm_status_text(pv, rt) == ""
        assert rt.display_level(True) == AlertLevel.OK
