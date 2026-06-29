"""Unit tests for the alert state machine + notifiers (no Qt, no real network)."""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from alerting import (  # noqa: E402
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EmailNotifier,
    EvalConfig, NotificationHub, TeamsClient, Thresholds, WebexNotifier,
)

SEC = 1_000_000_000
MIN = 60 * SEC

# Symmetric thresholds around ~10: warn at 8/12, alarm at 5/15.
THR = Thresholds(warn_low=8.0, warn_high=12.0, alarm_low=5.0, alarm_high=15.0)


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
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    res = feed(ev, AlertState(), [10.0, 9.5, 11.0])
    assert all(lvl == AlertLevel.OK and n is None for lvl, n in res)


def test_warning_transition_with_debounce():
    ev = AlertEvaluator(EvalConfig(debounce_count=2))
    st = AlertState()
    n1 = ev.evaluate(st, 13.0, THR, 0)
    assert st.level == AlertLevel.OK and n1 is None
    n2 = ev.evaluate(st, 13.0, THR, SEC)
    assert st.level == AlertLevel.WARNING
    assert n2 is not None and n2.level == AlertLevel.WARNING
    assert n2.kind == "transition"


def test_debounce_resets_on_return():
    ev = AlertEvaluator(EvalConfig(debounce_count=3))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    ev.evaluate(st, 10.0, THR, SEC)
    assert st.level == AlertLevel.OK and st.pending_count == 0


def test_escalation_to_alarm():
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    st = AlertState()
    feed(ev, st, [13.0])
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 16.0, THR, 5 * SEC)
    assert st.level == AlertLevel.ALARM
    assert n is not None and n.prev_level == AlertLevel.WARNING


def test_hysteresis_blocks_immediate_recovery():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.25))
    st = AlertState()
    feed(ev, st, [13.0])
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 11.5, THR, 2 * SEC)
    assert st.level == AlertLevel.WARNING and n is None
    n = ev.evaluate(st, 10.5, THR, 3 * SEC)
    assert st.level == AlertLevel.OK
    assert n is not None and n.level == AlertLevel.OK


def test_recovery_notify_can_be_disabled():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0,
                                   recovery_notify=False))
    st = AlertState()
    feed(ev, st, [13.0])
    n = ev.evaluate(st, 10.0, THR, 2 * SEC)
    assert st.level == AlertLevel.OK and n is None


def test_renotify_cooldown():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0,
                                   renotify_cooldown_minutes=30))
    st = AlertState()
    n0 = ev.evaluate(st, 13.0, THR, 0)
    assert n0 is not None
    n1 = ev.evaluate(st, 13.0, THR, 10 * MIN)
    assert n1 is None
    n2 = ev.evaluate(st, 13.0, THR, 31 * MIN)
    assert n2 is not None and n2.kind == "reminder"


def test_renotify_disabled_when_zero():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, renotify_cooldown_minutes=0))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    n = ev.evaluate(st, 13.0, THR, 100 * MIN)
    assert n is None


def test_one_sided_threshold():
    thr = Thresholds(warn_low=46.0, alarm_low=45.5)
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0))
    st = AlertState()
    assert ev.evaluate(st, 50.0, thr, 0) is None and st.level == AlertLevel.OK
    ev.evaluate(st, 45.8, thr, SEC)
    assert st.level == AlertLevel.WARNING
    ev.evaluate(st, 45.0, thr, 2 * SEC)
    assert st.level == AlertLevel.ALARM


def test_nodata_never_alerts_and_clears_pending():
    ev = AlertEvaluator(EvalConfig(debounce_count=2))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    n = ev.evaluate(st, None, THR, SEC)
    assert n is None and st.pending_count == 0 and st.level == AlertLevel.OK


def test_inactive_thresholds_never_alert():
    thr = Thresholds()
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    st = AlertState()
    for v in (-1e9, 0, 1e9):
        assert ev.evaluate(st, v, thr, 0) is None
    assert st.level == AlertLevel.OK


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
    n = WebexNotifier(mode="bot", bot_token="tok", room_id="room")
    resp = mock.MagicMock(status_code=200)
    with mock.patch("alerting.requests.post", return_value=resp) as post:
        assert n.send(_payload(), png_bytes=b"\x89PNG") is True
    _, kwargs = post.call_args
    assert "files" in kwargs and "files" in kwargs["files"]


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
