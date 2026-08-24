"""Unit tests for the alert state machine + notifiers (no Qt, no real network)."""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from alerting import (  # noqa: E402
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EmailNotifier,
    EvalConfig, NotificationHub, TeamsClient, Thresholds, Trend, WebexNotifier,
    classify_trend, detect_frozen, fmt_duration,
)

SEC = 1_000_000_000
MIN = 60 * SEC

# Symmetric thresholds around ~10: warn at 8/12, alarm at 5/15.
THR = Thresholds(warn_low=8.0, warn_high=12.0, alarm_low=5.0, alarm_high=15.0)


def _cfg(**kw):
    """EvalConfig for tests: settle window and stability hold off by default,
    so transitions notify as soon as they commit; individual tests turn the
    holds back on explicitly."""
    kw.setdefault("settle_minutes", 0.0)
    kw.setdefault("stable_seconds", 0.0)
    return EvalConfig(**kw)


def feed(ev, state, values, thr=THR, start=0, step=SEC):
    """Feed a sequence of values; return list of (level, notification)."""
    out = []
    t = start
    for v in values:
        note = ev.evaluate(state, v, thr, t)
        out.append((state.level, note))
        t += step
    return out


def test_stays_ok_when_inside():
    ev = AlertEvaluator(_cfg(debounce_count=1))
    res = feed(ev, AlertState(), [10.0, 9.5, 11.0])
    assert all(lvl == AlertLevel.OK and n is None for lvl, n in res)


def test_warning_transition_with_debounce():
    ev = AlertEvaluator(_cfg(debounce_count=2))
    st = AlertState()
    n1 = ev.evaluate(st, 13.0, THR, 0)
    assert st.level == AlertLevel.OK and n1 is None
    n2 = ev.evaluate(st, 13.0, THR, SEC)
    assert st.level == AlertLevel.WARNING
    assert n2 is not None and n2.level == AlertLevel.WARNING
    assert n2.kind == "transition"


def test_debounce_resets_on_return():
    ev = AlertEvaluator(_cfg(debounce_count=3))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    ev.evaluate(st, 10.0, THR, SEC)
    assert st.level == AlertLevel.OK and st.pending_count == 0


def test_escalation_to_alarm():
    ev = AlertEvaluator(_cfg(debounce_count=1))
    st = AlertState()
    feed(ev, st, [13.0])
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 16.0, THR, 5 * SEC)
    assert st.level == AlertLevel.ALARM
    assert n is not None and n.prev_level == AlertLevel.WARNING


def test_recovery_follows_raw_thresholds():
    """No hysteresis: back inside the limits = back to OK (after debounce)."""
    ev = AlertEvaluator(_cfg(debounce_count=1))
    st = AlertState()
    feed(ev, st, [13.0])
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 11.9, THR, 2 * SEC)
    assert st.level == AlertLevel.OK
    assert n is not None and n.level == AlertLevel.OK


def test_settle_window_silences_transient():
    ev = AlertEvaluator(_cfg(debounce_count=1, settle_minutes=5.0))
    st = AlertState()
    assert ev.evaluate(st, 13.0, THR, 0) is None          # window opens
    assert ev.evaluate(st, 10.0, THR, 1 * MIN) is None    # recovered inside
    assert ev.evaluate(st, 10.0, THR, 6 * MIN) is None    # closes silently


def test_settle_window_reports_ongoing_warning():
    ev = AlertEvaluator(_cfg(debounce_count=1, settle_minutes=5.0))
    st = AlertState()
    assert ev.evaluate(st, 13.0, THR, 0) is None
    n = ev.evaluate(st, 13.0, THR, 6 * MIN)
    assert n is not None and n.level == AlertLevel.WARNING
    assert "did not settle" in n.reason


def test_stability_hold_suppresses_flapping():
    ev = AlertEvaluator(_cfg(debounce_count=1, stable_seconds=120.0))
    st = AlertState()
    t = 0
    for i in range(10):                     # OK <-> WARNING every 30 s
        v = 13.0 if i % 2 == 0 else 10.0
        assert ev.evaluate(st, v, THR, t) is None
        t += 30 * SEC
    # Sticks at WARNING -> exactly one message once stable for 120 s.
    notes = [ev.evaluate(st, 13.0, THR, t + i * 30 * SEC) for i in range(6)]
    real = [n for n in notes if n is not None]
    assert len(real) == 1
    assert real[0].level == AlertLevel.WARNING
    assert real[0].prev_level == AlertLevel.OK


def test_recovery_notify_can_be_disabled():
    ev = AlertEvaluator(_cfg(debounce_count=1, recovery_notify=False))
    st = AlertState()
    feed(ev, st, [13.0])
    n = ev.evaluate(st, 10.0, THR, 2 * SEC)
    assert st.level == AlertLevel.OK and n is None


def test_renotify_cooldown():
    ev = AlertEvaluator(_cfg(debounce_count=1, renotify_cooldown_minutes=30))
    st = AlertState()
    n0 = ev.evaluate(st, 13.0, THR, 0)
    assert n0 is not None
    n1 = ev.evaluate(st, 13.0, THR, 10 * MIN)
    assert n1 is None
    n2 = ev.evaluate(st, 13.0, THR, 31 * MIN)
    assert n2 is not None and n2.kind == "reminder"


def test_renotify_disabled_when_zero():
    ev = AlertEvaluator(_cfg(debounce_count=1, renotify_cooldown_minutes=0))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    n = ev.evaluate(st, 13.0, THR, 100 * MIN)
    assert n is None


def test_one_sided_threshold():
    thr = Thresholds(warn_low=46.0, alarm_low=45.5)
    ev = AlertEvaluator(_cfg(debounce_count=1))
    st = AlertState()
    assert ev.evaluate(st, 50.0, thr, 0) is None and st.level == AlertLevel.OK
    ev.evaluate(st, 45.8, thr, SEC)
    assert st.level == AlertLevel.WARNING
    ev.evaluate(st, 45.0, thr, 2 * SEC)
    assert st.level == AlertLevel.ALARM


def test_nodata_never_alerts_and_clears_pending():
    ev = AlertEvaluator(_cfg(debounce_count=2))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    n = ev.evaluate(st, None, THR, SEC)
    assert n is None and st.pending_count == 0 and st.level == AlertLevel.OK


def test_inactive_thresholds_never_alert():
    thr = Thresholds()
    ev = AlertEvaluator(_cfg(debounce_count=1))
    st = AlertState()
    for v in (-1e9, 0, 1e9):
        assert ev.evaluate(st, v, thr, 0) is None
    assert st.level == AlertLevel.OK


# ---------------------------------------------------------------------------
# Trend classification + trend-scaled reminder cooldown
# ---------------------------------------------------------------------------

NOW = 1_000_000 * SEC
LOOKBACK = 600.0    # 10 min


def _ramp(v_start, v_end, n=30, now_ns=NOW, lookback_s=LOOKBACK):
    """n samples linearly spread across the last lookback_s seconds."""
    span = int(lookback_s * SEC)
    start = now_ns - span
    return [(start + int(i / (n - 1) * span),
             v_start + (v_end - v_start) * i / (n - 1)) for i in range(n)]


def test_trend_rising_is_detected():
    assert classify_trend(_ramp(100.0, 105.0), NOW, LOOKBACK) == Trend.RISING


def test_trend_falling_is_detected():
    assert classify_trend(_ramp(105.0, 100.0), NOW, LOOKBACK) == Trend.FALLING


def test_trend_small_change_is_flat():
    # 0.5% overall change is below the 2% default flat band.
    assert classify_trend(_ramp(100.0, 100.5), NOW, LOOKBACK) == Trend.FLAT


def test_trend_constant_is_flat():
    assert classify_trend(_ramp(100.0, 100.0), NOW, LOOKBACK) == Trend.FLAT


def test_trend_choppy_is_flat():
    span = int(LOOKBACK * SEC)
    start = NOW - span
    samples = [(start + int(i / 29 * span), 90.0 if i % 2 else 110.0)
               for i in range(30)]
    assert classify_trend(samples, NOW, LOOKBACK) == Trend.FLAT


def test_trend_too_few_samples_is_flat():
    assert classify_trend(_ramp(100.0, 105.0, n=3), NOW, LOOKBACK) == Trend.FLAT
    assert classify_trend([], NOW, LOOKBACK) == Trend.FLAT


def test_trend_ignores_samples_outside_lookback():
    # Old rising data far in the past must not count; recent data is flat.
    old = [(NOW - 3600 * SEC + i * SEC, 50.0 + i) for i in range(10)]
    recent = _ramp(100.0, 100.2)
    assert classify_trend(old + recent, NOW, LOOKBACK) == Trend.FLAT


def test_reminder_cooldown_scale_slows_down():
    ev = AlertEvaluator(_cfg(debounce_count=1, renotify_cooldown_minutes=30))
    st = AlertState()
    assert ev.evaluate(st, 13.0, THR, 0) is not None          # initial transition
    # scale 2.0 -> reminder needs 60 min, so 31 min is too soon.
    assert ev.evaluate(st, 13.0, THR, 31 * MIN, cooldown_scale=2.0) is None
    n = ev.evaluate(st, 13.0, THR, 61 * MIN, cooldown_scale=2.0)
    assert n is not None and n.kind == "reminder"


def test_reminder_cooldown_scale_speeds_up():
    ev = AlertEvaluator(_cfg(debounce_count=1, renotify_cooldown_minutes=30))
    st = AlertState()
    assert ev.evaluate(st, 13.0, THR, 0) is not None
    # scale 0.5 -> reminder needs 15 min.
    assert ev.evaluate(st, 13.0, THR, 10 * MIN, cooldown_scale=0.5) is None
    n = ev.evaluate(st, 13.0, THR, 16 * MIN, cooldown_scale=0.5)
    assert n is not None and n.kind == "reminder"


def test_reminder_cooldown_scale_default_unchanged():
    ev = AlertEvaluator(_cfg(debounce_count=1, renotify_cooldown_minutes=30))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    assert ev.evaluate(st, 13.0, THR, 31 * MIN) is not None    # default scale 1.0


# ---------------------------------------------------------------------------
# Frozen-value ("not updating") detection
# ---------------------------------------------------------------------------

FROZEN_AFTER = 2 * 3600.0    # 2 h, the app's default


def _close(a, b, tol=1.0):
    return abs(a - b) <= tol


def _flat(value, hours, n=60, now_ns=NOW):
    """n samples of one constant value spread over the last `hours`."""
    span = int(hours * 3600 * SEC)
    start = now_ns - span
    return [(start + int(i / (n - 1) * span), value) for i in range(n)]


def test_frozen_constant_value_is_detected():
    info = detect_frozen(_flat(16.3, 48), NOW, FROZEN_AFTER)
    assert info.frozen
    assert info.n_points == 60
    assert _close(info.span_s, 48 * 3600)
    # Nothing different precedes the run, so the freeze may be even older.
    assert not info.bounded


def test_frozen_needs_the_full_span():
    # Same constant value, but only 30 min of it: below the 2 h limit.
    assert not detect_frozen(_flat(16.3, 0.5), NOW, FROZEN_AFTER).frozen


def test_frozen_measures_from_the_last_real_change():
    moving = [(NOW - int(10 * 3600 * SEC) + i * SEC, 10.0 + i) for i in range(20)]
    stuck = _flat(16.3, 5, n=40)
    info = detect_frozen(moving + stuck, NOW, FROZEN_AFTER)
    assert info.frozen and info.bounded
    assert _close(info.span_s, 5 * 3600)
    assert info.n_points == 40


def test_moving_value_is_not_frozen():
    ramp = [(NOW - int(6 * 3600 * SEC) + i * MIN, 16.0 + i * 0.01)
            for i in range(300)]
    assert not detect_frozen(ramp, NOW, FROZEN_AFTER).frozen


def test_frozen_only_the_latest_value_counts():
    # It changed 1 min ago after sitting still for hours: not frozen now.
    samples = (_flat(16.3, 24, n=100, now_ns=NOW - 2 * MIN)
               + [(NOW - MIN, 16.4), (NOW, 16.4)])
    info = detect_frozen(samples, NOW, FROZEN_AFTER)
    assert not info.frozen and info.n_points == 2


def test_frozen_ignores_sparse_data():
    # Two readings 24 h apart carry no evidence of a stuck sensor.
    sparse = [(NOW - int(24 * 3600 * SEC), 16.3), (NOW, 16.3)]
    assert not detect_frozen(sparse, NOW, FROZEN_AFTER).frozen
    # The same span with enough samples behind it is frozen.
    assert detect_frozen(_flat(16.3, 24, n=5), NOW, FROZEN_AFTER).frozen


def test_frozen_averaging_rounding_still_counts_as_unchanged():
    # Live polls store the mean of N identical samples, which can land a few
    # float-ULPs apart; that must not read as a real change.
    v = 16.3
    samples = [(NOW - int(6 * 3600 * SEC) + i * MIN,
                sum([v] * 25) / 25 if i % 2 else v) for i in range(300)]
    assert detect_frozen(samples, NOW, FROZEN_AFTER).frozen


def test_frozen_check_disabled_and_no_data():
    assert not detect_frozen(_flat(16.3, 48), NOW, 0).frozen
    assert not detect_frozen([], NOW, FROZEN_AFTER).frozen
    assert detect_frozen([], NOW, FROZEN_AFTER).n_points == 0


def test_frozen_ignores_future_samples():
    future = [(NOW + int(3600 * SEC), 99.0)]
    assert detect_frozen(_flat(16.3, 48) + future, NOW, FROZEN_AFTER).frozen


def test_fmt_duration():
    assert fmt_duration(45) == "45 s"
    assert fmt_duration(90 * 60) == "1 h 30 min"
    assert fmt_duration(20 * 60) == "20 min"
    assert fmt_duration(50 * 3600) == "2 d 2 h"
    assert fmt_duration(-5) == "0 s"


# ---------------------------------------------------------------------------
# Notifiers
# ---------------------------------------------------------------------------

def _payload():
    return AlertPayload(
        level=AlertLevel.ALARM, prev_level=AlertLevel.OK,
        pv_name="L3-TEMP:Value", display_name="Temp",
        value=42.0, units="°C", reason="ALARM high: 42 ≥ 40",
        timestamp_str="2026-06-29 12:00:00", kind="transition")


def test_email_not_configured():
    n = EmailNotifier()
    assert not n.is_configured()
    assert n.send("s", "b") is False
    assert n.last_error


def test_email_send_attaches_png():
    n = EmailNotifier(host="smtp.local", from_addr="a@b.c", recipients=["x@y.z"],
                      security="none")
    sent = {}
    fake = mock.MagicMock()

    def capture(msg):
        sent["msg"] = msg
    fake.send_message.side_effect = capture
    with mock.patch("alerting.smtplib.SMTP", return_value=fake):
        ok = n.send("subj", "body", png_bytes=b"\x89PNG\r\n", png_name="p.png")
    assert ok and not n.last_error
    msg = sent["msg"]
    attachments = [p for p in msg.iter_attachments()]
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "image/png"


def test_email_never_raises():
    n = EmailNotifier(host="smtp.local", from_addr="a@b.c", recipients=["x@y.z"])
    with mock.patch("alerting.smtplib.SMTP", side_effect=OSError("boom")):
        assert n.send("s", "b") is False
    assert "boom" in n.last_error


def test_webex_webhook_posts_markdown():
    n = WebexNotifier(mode="webhook", webhook_url="https://webhook")
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.send(_payload()) is True
    args, kwargs = post.call_args
    assert kwargs["json"]["markdown"]


def test_webex_bot_uploads_file_when_png():
    n = WebexNotifier(mode="bot", bot_token="tok", room_ids=["room"])
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.send(_payload(), png_bytes=b"\x89PNG") is True
    _, kwargs = post.call_args
    assert "files" in kwargs and "files" in kwargs["files"]


def test_webex_bot_broadcasts_to_multiple_rooms():
    n = WebexNotifier(mode="bot", bot_token="tok", room_ids=["room1", "room2"])
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.send(_payload()) is True
    assert post.call_count == 2
    posted_rooms = {c.kwargs["json"]["roomId"] for c in post.call_args_list}
    assert posted_rooms == {"room1", "room2"}


def test_webex_bot_partial_room_failure_reported():
    n = WebexNotifier(mode="bot", bot_token="tok", room_ids=["ok", "bad"])
    ok_resp = mock.MagicMock(status_code=200)
    bad_resp = mock.MagicMock(status_code=404, text="not found")
    with mock.patch("alerting.requests.post", side_effect=[ok_resp, bad_resp]):
        assert n.send(_payload()) is False
    assert "bad" in n.last_error


def test_webex_tracks_own_message_ids_to_block_feedback_loop():
    n = WebexNotifier(mode="bot", bot_token="tok", room_ids=["room"],
                      listen_room_id="room")
    resp = mock.MagicMock(status_code=200)
    resp.json.return_value = {"id": "msg123"}
    with mock.patch("alerting.requests.post", return_value=resp):
        assert n.post_text("hello") is True
    assert n.is_own_message("msg123") is True
    assert n.is_own_message("someone-elses-msg") is False


def test_webex_post_markdown_uploads_png_to_every_room():
    n = WebexNotifier(mode="bot", bot_token="tok", room_ids=["room1", "room2"])
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.post_markdown("**chart**", png_bytes=b"\x89PNG") is True
    assert post.call_count == 2
    rooms = {c.kwargs["files"]["roomId"][1] for c in post.call_args_list}
    assert rooms == {"room1", "room2"}
    assert all("files" in c.kwargs["files"] for c in post.call_args_list)


def test_webex_post_markdown_webhook_has_no_file():
    n = WebexNotifier(mode="webhook", webhook_url="https://webhook")
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.post_markdown("**chart**", png_bytes=b"\x89PNG") is True
    _, kwargs = post.call_args
    assert "files" not in kwargs
    assert "e-mail" in kwargs["json"]["markdown"]


def test_webex_not_configured():
    assert WebexNotifier(mode="bot").is_configured() is False
    assert WebexNotifier(mode="webhook").is_configured() is False


def test_hub_dispatch_only_enabled_and_configured():
    s = {
        "teams_enabled": True, "teams_webhook_url": "https://teams",
        "email_enabled": True, "smtp_host": "smtp", "email_from": "a@b.c",
        "email_recipients": ["x@y.z"], "smtp_security": "none",
        "webex_enabled": False,
    }
    hub = NotificationHub.from_settings(s)
    with mock.patch.object(hub.teams, "post_payload", return_value=True) as t, \
         mock.patch.object(hub.email, "send_payload", return_value=True) as e, \
         mock.patch.object(hub.webex, "send", return_value=True) as w:
        errors = hub.dispatch(_payload(), png_bytes=b"x")
    assert errors == {}
    assert t.called and e.called and not w.called


def test_hub_dispatch_chart_reaches_every_channel():
    s = {
        "teams_enabled": True, "teams_webhook_url": "https://teams",
        "email_enabled": True, "smtp_host": "smtp", "email_from": "a@b.c",
        "email_recipients": ["x@y.z"], "smtp_security": "none",
        "webex_enabled": True, "webex_mode": "bot", "webex_bot_token": "tok",
        "webex_rooms": [{"room_id": "r", "enabled": True, "listen": True}],
    }
    hub = NotificationHub.from_settings(s)
    with mock.patch.object(hub.teams, "post", return_value=True) as t, \
         mock.patch.object(hub.email, "send", return_value=True) as e, \
         mock.patch.object(hub.webex, "post_markdown", return_value=True) as w:
        errors = hub.dispatch_chart("Plot — 3 PVs", "plain", "**md**", b"png")
    assert errors == {}
    assert t.call_args.args[0]["title"] == "Plot — 3 PVs"   # text card, not a fact table
    assert e.call_args.args[:2] == ("Plot — 3 PVs", "plain")
    assert w.call_args.args == ("**md**", b"png")


def test_hub_dispatch_chart_collects_errors():
    s = {"teams_enabled": True, "teams_webhook_url": "https://teams",
         "email_enabled": False, "webex_enabled": False}
    hub = NotificationHub.from_settings(s)
    hub.teams.last_error = "HTTP 500"
    with mock.patch.object(hub.teams, "post", return_value=False):
        errors = hub.dispatch_chart("t", "plain", "**md**", None)
    assert errors == {"teams": "HTTP 500"}


def test_hub_email_uses_only_enabled_contacts():
    s = {
        "email_enabled": True, "smtp_host": "smtp", "email_from": "a@b.c",
        "smtp_security": "none",
        "email_contacts": [
            {"name": "A", "address": "a@x.eu", "enabled": True},
            {"name": "B", "address": "b@x.eu", "enabled": False},
        ],
    }
    hub = NotificationHub.from_settings(s)
    assert hub.email.recipients == ["a@x.eu"]


def test_hub_webex_broadcasts_to_enabled_rooms_only():
    s = {
        "webex_enabled": True, "webex_mode": "bot", "webex_bot_token": "tok",
        "webex_rooms": [
            {"name": "Main", "room_id": "r1", "enabled": True, "listen": True},
            {"name": "Testing", "room_id": "r2", "enabled": False},
        ],
    }
    hub = NotificationHub.from_settings(s)
    assert hub.webex.room_ids == ["r1"]
    assert hub.webex.listen_room_id == "r1"


def test_hub_collects_errors():
    s = {"teams_enabled": True, "teams_webhook_url": "https://teams"}
    hub = NotificationHub.from_settings(s)
    hub.teams.last_error = "HTTP 500"
    with mock.patch.object(hub.teams, "post_payload", return_value=False):
        errors = hub.dispatch(_payload())
    assert errors == {"teams": "HTTP 500"}


def test_payload_render_methods():
    p = _payload()
    assert "ALARM" in p.subject()
    assert "L3-TEMP:Value" in p.text_body()
    assert p.markdown_body().startswith("**")


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
