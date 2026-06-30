"""
Alert state machine + notification clients for the PV Monitor tab.

The evaluator is intentionally free of Qt and networking so it can be unit
tested in isolation. It takes a value + thresholds + the current AlertState and
a clock (now_ns passed in), mutates the state, and returns a Notification when
the caller should send a message.

Design:
  - Severity: ALARM if value crosses an alarm bound, else WARNING if it crosses
    a warn bound, else OK. Any bound may be None (that side is not checked).
  - Debounce: a new severity must persist `debounce_count` consecutive polls
    before it is committed (filters noise on top of the value averaging).
  - Hysteresis: de-escalation requires the value to move back inside by a
    deadband (fraction of the threshold span) -> no flapping at the boundary.
  - Notify only on a committed transition; re-notify a stuck WARNING/ALARM at
    most every `renotify_cooldown_minutes`. NODATA (value None) never alerts.

Notification channels (all are safe to call from a worker thread, never raise):
  - TeamsClient      -> Microsoft Teams Incoming Webhook (text card, no image)
  - EmailNotifier    -> SMTP, supports a PNG attachment
  - WebexNotifier    -> Webex incoming webhook (text) or bot API (text + file)
  - NotificationHub  -> fans a single alert out to all enabled+configured ones
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from enum import IntEnum
from typing import Optional

import requests


class AlertLevel(IntEnum):
    OK = 0
    WARNING = 1
    ALARM = 2

    @property
    def label(self) -> str:
        return {AlertLevel.OK: "OK",
                AlertLevel.WARNING: "WARNING",
                AlertLevel.ALARM: "ALARM"}[self]


@dataclass
class Thresholds:
    warn_low: Optional[float] = None
    warn_high: Optional[float] = None
    alarm_low: Optional[float] = None
    alarm_high: Optional[float] = None

    def is_active(self) -> bool:
        """True if at least one bound is set (otherwise the PV never alerts)."""
        return any(b is not None for b in
                   (self.warn_low, self.warn_high, self.alarm_low, self.alarm_high))


@dataclass
class AlertState:
    level: AlertLevel = AlertLevel.OK
    since_ns: int = 0
    last_notified_ns: int = 0
    pending_level: Optional[AlertLevel] = None
    pending_count: int = 0


@dataclass
class Notification:
    """Returned when the caller should send a message."""
    level: AlertLevel
    prev_level: AlertLevel
    value: float
    reason: str
    kind: str  # "transition" | "reminder"


@dataclass
class EvalConfig:
    debounce_count: int = 2
    hysteresis_frac: float = 0.05
    renotify_cooldown_minutes: float = 30.0
    recovery_notify: bool = True


# ---------------------------------------------------------------------------
# Severity helpers (pure functions)
# ---------------------------------------------------------------------------

def _raw_severity(value: float, thr: Thresholds) -> AlertLevel:
    if (thr.alarm_low is not None and value <= thr.alarm_low) or \
       (thr.alarm_high is not None and value >= thr.alarm_high):
        return AlertLevel.ALARM
    if (thr.warn_low is not None and value <= thr.warn_low) or \
       (thr.warn_high is not None and value >= thr.warn_high):
        return AlertLevel.WARNING
    return AlertLevel.OK


def _deadband(thr: Thresholds, frac: float) -> float:
    """Hysteresis deadband, scaled from the threshold span."""
    if frac <= 0:
        return 0.0
    if thr.warn_low is not None and thr.warn_high is not None:
        return frac * abs(thr.warn_high - thr.warn_low)
    if thr.alarm_low is not None and thr.alarm_high is not None:
        return frac * abs(thr.alarm_high - thr.alarm_low)
    # One-sided thresholds: scale from the magnitude of whatever bound exists.
    for b in (thr.warn_low, thr.warn_high, thr.alarm_low, thr.alarm_high):
        if b is not None and b != 0:
            return frac * abs(b)
    return 0.0


def _tightened(thr: Thresholds, deadband: float) -> Thresholds:
    """Pull every bound inward by the deadband (stricter -> used for recovery)."""
    return Thresholds(
        warn_low   = thr.warn_low   + deadband if thr.warn_low   is not None else None,
        warn_high  = thr.warn_high  - deadband if thr.warn_high  is not None else None,
        alarm_low  = thr.alarm_low  + deadband if thr.alarm_low  is not None else None,
        alarm_high = thr.alarm_high - deadband if thr.alarm_high is not None else None,
    )


def describe_reason(level: AlertLevel, value: float, thr: Thresholds) -> str:
    if level == AlertLevel.OK:
        return "Back to normal range"
    lo = thr.alarm_low if level == AlertLevel.ALARM else thr.warn_low
    hi = thr.alarm_high if level == AlertLevel.ALARM else thr.warn_high
    name = level.label
    if lo is not None and value <= lo:
        return f"{name} low: {value:g} ≤ {lo:g}"
    if hi is not None and value >= hi:
        return f"{name} high: {value:g} ≥ {hi:g}"
    return f"{name}: {value:g}"


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class AlertEvaluator:
    def __init__(self, config: EvalConfig | None = None):
        self.config = config or EvalConfig()

    def evaluate(self, state: AlertState, value: Optional[float],
                 thr: Thresholds, now_ns: int) -> Optional[Notification]:
        """Update `state` in place; return a Notification to send, or None.

        value is None (NODATA) leaves the committed level untouched and never
        alerts; it only clears any pending debounce so a gap doesn't commit a
        stale transition.
        """
        cfg = self.config

        if value is None:
            state.pending_level = None
            state.pending_count = 0
            return None

        committed = state.level

        # --- target severity with hysteresis -------------------------------
        raw = _raw_severity(value, thr)
        if raw >= committed:
            target = raw                      # escalation is immediate
        else:
            db = _deadband(thr, cfg.hysteresis_frac)
            target = _raw_severity(value, _tightened(thr, db))
            if target > committed:            # safety clamp
                target = committed

        # --- debounce ------------------------------------------------------
        if target == committed:
            state.pending_level = None
            state.pending_count = 0
        else:
            if target == state.pending_level:
                state.pending_count += 1
            else:
                state.pending_level = target
                state.pending_count = 1

            if state.pending_count >= max(1, cfg.debounce_count):
                return self._commit(state, target, value, thr, now_ns)

        # --- re-notify cooldown for a stuck WARNING/ALARM ------------------
        if committed in (AlertLevel.WARNING, AlertLevel.ALARM) and \
                cfg.renotify_cooldown_minutes > 0:
            cooldown_ns = int(cfg.renotify_cooldown_minutes * 60 * 1e9)
            if now_ns - state.last_notified_ns >= cooldown_ns:
                state.last_notified_ns = now_ns
                return Notification(
                    level=committed, prev_level=committed, value=value,
                    reason=describe_reason(committed, value, thr), kind="reminder",
                )

        return None

    def _commit(self, state: AlertState, target: AlertLevel, value: float,
                thr: Thresholds, now_ns: int) -> Optional[Notification]:
        prev = state.level
        state.level = target
        state.since_ns = now_ns
        state.pending_level = None
        state.pending_count = 0

        notify = self._should_notify(prev, target)
        if notify:
            state.last_notified_ns = now_ns
            return Notification(
                level=target, prev_level=prev, value=value,
                reason=describe_reason(target, value, thr), kind="transition",
            )
        return None

    def _should_notify(self, prev: AlertLevel, new: AlertLevel) -> bool:
        if new > prev:
            return True                       # any escalation
        if new == AlertLevel.OK:
            return self.config.recovery_notify  # full recovery
        return True                           # ALARM -> WARNING de-escalation


# ---------------------------------------------------------------------------
# Channel-agnostic payload — single source of truth for every channel's render
# ---------------------------------------------------------------------------

@dataclass
class AlertPayload:
    level: AlertLevel
    prev_level: AlertLevel
    pv_name: str
    display_name: str
    value: float
    units: str
    reason: str
    timestamp_str: str        # already Prague-formatted by the caller
    kind: str                 # "transition" | "reminder" | "manual"

    def _val_str(self) -> str:
        return f"{self.value:g} {self.units}".strip()

    def subject(self) -> str:
        if self.kind == "manual":
            return f"Plot — {self.display_name}"
        if self.kind == "reminder":
            return f"[{self.level.label}] ongoing — {self.display_name}"
        if self.level == AlertLevel.OK:
            return f"[OK] Recovered — {self.display_name}"
        return f"[{self.level.label}] {self.display_name}"

    def text_body(self) -> str:
        return (f"{self.prev_level.label} → {self.level.label}\n"
                f"PV: {self.pv_name}\n"
                f"Value: {self._val_str()}\n"
                f"Reason: {self.reason}\n"
                f"Time: {self.timestamp_str} Europe/Prague")

    def markdown_body(self) -> str:
        return (f"**{self.subject()}**\n\n"
                f"- **PV:** `{self.pv_name}`\n"
                f"- **Value:** {self._val_str()}\n"
                f"- **Reason:** {self.reason}\n"
                f"- **State:** {self.prev_level.label} → {self.level.label}\n"
                f"- **Time:** {self.timestamp_str} Europe/Prague")


def _resolve_secret(value: str) -> str:
    """Resolve a ``${ENV:NAME}`` indirection to os.environ at send time.

    Lets the user keep secrets out of monitor_config.json by storing a
    reference instead of the literal password/token.
    """
    if isinstance(value, str) and value.startswith("${ENV:") and value.endswith("}"):
        return os.environ.get(value[6:-1], "")
    return value or ""


# ---------------------------------------------------------------------------
# Microsoft Teams Incoming Webhook client
# ---------------------------------------------------------------------------

_THEME = {
    AlertLevel.OK: "2E7D32",
    AlertLevel.WARNING: "CC6600",
    AlertLevel.ALARM: "B71C1C",
}


def build_messagecard(level: AlertLevel, prev_level: AlertLevel,
                      pv_name: str, display_name: str,
                      value: float, units: str, reason: str,
                      timestamp_str: str, kind: str = "transition") -> dict:
    """Build a classic Teams MessageCard payload (renders on Incoming Webhooks)."""
    val_str = f"{value:g} {units}".strip()
    if kind == "reminder":
        title = f"{level.label} ongoing — {display_name}"
    elif level == AlertLevel.OK:
        title = f"Recovered — {display_name}"
    else:
        title = f"{level.label} — {display_name}"

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": _THEME.get(level, "808080"),
        "summary": f"PV {level.label}: {pv_name}",
        "title": title,
        "sections": [{
            "facts": [
                {"name": "PV",    "value": pv_name},
                {"name": "Value", "value": val_str},
                {"name": "Reason", "value": reason},
                {"name": "State", "value": f"{prev_level.label} → {level.label}"},
                {"name": "Time",  "value": f"{timestamp_str} Europe/Prague"},
            ],
            "markdown": True,
        }],
    }


class TeamsClient:
    """Posts cards to a Teams Incoming Webhook. Never raises into the caller."""

    def __init__(self, webhook_url: str = "", timeout: float = 10.0):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self.last_error: str = ""

    def is_configured(self) -> bool:
        return bool(self.webhook_url and self.webhook_url.lower().startswith("http"))

    def post(self, card: dict) -> bool:
        if not self.is_configured():
            self.last_error = "No webhook URL configured"
            return False
        try:
            resp = requests.post(
                self.webhook_url, json=card, timeout=self.timeout, verify=True,
            )
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return True
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return False
        except Exception as e:  # noqa: BLE001 - must never propagate to UI thread
            self.last_error = str(e)
            return False

    def post_payload(self, payload: "AlertPayload", png_bytes: bytes | None = None) -> bool:
        # Teams Incoming Webhooks cannot embed raw image bytes (would need a
        # hosted URL), so the PNG is ignored here. Text card only.
        card = build_messagecard(
            payload.level, payload.prev_level, payload.pv_name, payload.display_name,
            payload.value, payload.units, payload.reason, payload.timestamp_str,
            payload.kind if payload.kind in ("transition", "reminder") else "transition")
        return self.post(card)

    def send_test(self) -> bool:
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "1565C0",
            "summary": "PV Monitor test message",
            "title": "PV Monitor — test message",
            "sections": [{
                "text": "If you can read this, the Teams webhook is working.",
                "markdown": True,
            }],
        }
        return self.post(card)


# ---------------------------------------------------------------------------
# Email (SMTP) notifier — supports a PNG attachment
# ---------------------------------------------------------------------------

class EmailNotifier:
    """Sends alert e-mails via SMTP. Never raises into the caller."""

    def __init__(self, host: str = "", port: int = 587, security: str = "starttls",
                 username: str = "", password: str = "", from_addr: str = "",
                 recipients: Optional[list[str]] = None, timeout: float = 10.0):
        self.host = host
        self.port = int(port)
        self.security = (security or "starttls").lower()   # none | starttls | ssl
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.recipients = list(recipients or [])
        self.timeout = timeout
        self.last_error: str = ""

    def is_configured(self) -> bool:
        return bool(self.host and self.from_addr and self.recipients)

    def send(self, subject: str, body: str,
             png_bytes: bytes | None = None, png_name: str = "plot.png") -> bool:
        if not self.is_configured():
            self.last_error = "Email not fully configured (host/from/recipients)"
            return False
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.recipients)
            msg.set_content(body)
            if png_bytes:
                msg.add_attachment(png_bytes, maintype="image", subtype="png",
                                   filename=png_name)

            if self.security == "ssl":
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            try:
                smtp.ehlo()
                if self.security == "starttls":
                    smtp.starttls()
                    smtp.ehlo()
                user = self.username
                pwd = _resolve_secret(self.password)
                if user:
                    smtp.login(user, pwd)
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
            self.last_error = ""
            return True
        except Exception as e:  # noqa: BLE001 - must never propagate to UI thread
            self.last_error = str(e)
            return False

    def send_payload(self, payload: "AlertPayload", png_bytes: bytes | None = None) -> bool:
        return self.send(payload.subject(), payload.text_body(), png_bytes)

    def send_test(self) -> bool:
        return self.send("PV Monitor — test message",
                         "If you can read this, the SMTP settings are working.")


# ---------------------------------------------------------------------------
# Webex notifier — incoming webhook (text) or bot API (text + file upload)
# ---------------------------------------------------------------------------

WEBEX_MESSAGES_URL = "https://webexapis.com/v1/messages"
WEBEX_ME_URL = "https://webexapis.com/v1/people/me"


class WebexNotifier:
    """Sends alerts to Webex. Never raises into the caller.

    mode == "webhook": POST {"markdown": ...} to an incoming webhook URL.
                       Webex incoming webhooks do NOT support file uploads.
    mode == "bot":     POST to the Messages API with a bot token + roomId.
                       A PNG is attached as multipart/form-data when given.
    """

    def __init__(self, mode: str = "webhook", webhook_url: str = "",
                 bot_token: str = "", room_id: str = "", timeout: float = 10.0):
        self.mode = (mode or "webhook").lower()
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.room_id = room_id
        self.timeout = timeout
        self.last_error: str = ""

    def is_configured(self) -> bool:
        if self.mode == "bot":
            return bool(self.bot_token and self.room_id)
        return bool(self.webhook_url and self.webhook_url.lower().startswith("http"))

    def send(self, payload: "AlertPayload", png_bytes: bytes | None = None) -> bool:
        if not self.is_configured():
            self.last_error = "Webex not fully configured"
            return False
        try:
            if self.mode == "bot":
                token = _resolve_secret(self.bot_token)
                headers = {"Authorization": f"Bearer {token}"}
                if png_bytes:
                    files = {
                        "roomId": (None, self.room_id),
                        "markdown": (None, payload.markdown_body()),
                        "files": ("plot.png", png_bytes, "image/png"),
                    }
                    resp = requests.post(WEBEX_MESSAGES_URL, headers=headers,
                                         files=files, timeout=self.timeout)
                else:
                    resp = requests.post(
                        WEBEX_MESSAGES_URL, headers=headers,
                        json={"roomId": self.room_id, "markdown": payload.markdown_body()},
                        timeout=self.timeout)
            else:
                # Incoming webhook: text/markdown only; note the graph is in e-mail.
                md = payload.markdown_body()
                if png_bytes:
                    md += "\n\n_(graph attached in the e-mail alert)_"
                resp = requests.post(self.webhook_url, json={"markdown": md},
                                     timeout=self.timeout, verify=True)

            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return True
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return False
        except Exception as e:  # noqa: BLE001 - must never propagate to UI thread
            self.last_error = str(e)
            return False

    def send_test(self) -> bool:
        payload = AlertPayload(
            level=AlertLevel.OK, prev_level=AlertLevel.OK,
            pv_name="TEST", display_name="PV Monitor",
            value=0.0, units="", reason="Webex test message",
            timestamp_str="now", kind="manual")
        return self.send(payload)

    # --- two-way (bot mode only): read commands + reply -------------------

    def can_listen(self) -> bool:
        """Reading messages needs a bot token + room (webhooks can't read)."""
        return bool(self.mode == "bot" and self.bot_token and self.room_id)

    def _bot_headers(self) -> dict:
        return {"Authorization": f"Bearer {_resolve_secret(self.bot_token)}"}

    def get_me_id(self) -> Optional[str]:
        """Return the bot's own personId (to skip its own messages). None on error."""
        if not self.can_listen():
            return None
        try:
            resp = requests.get(WEBEX_ME_URL, headers=self._bot_headers(),
                                timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return resp.json().get("id")
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
        return None

    def fetch_messages(self, max_count: int = 20) -> list[dict]:
        """Return recent messages in the room (newest first). [] on error.

        Direct (1:1) spaces: a bot sees all messages. Group spaces: a bot may
        only read messages that @mention it, and the API returns 403 unless
        ``mentionedPeople=me`` is set — so we retry with that filter on a 403.
        """
        if not self.can_listen():
            return []
        params = {"roomId": self.room_id, "max": max_count}
        try:
            resp = requests.get(WEBEX_MESSAGES_URL, headers=self._bot_headers(),
                                params=params, timeout=self.timeout)
            if resp.status_code == 403:
                resp = requests.get(
                    WEBEX_MESSAGES_URL, headers=self._bot_headers(),
                    params={**params, "mentionedPeople": "me"}, timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return resp.json().get("items", [])
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
        return []

    def post_text(self, markdown: str) -> bool:
        """Post a plain markdown reply into the room (bot mode)."""
        if not self.can_listen():
            self.last_error = "Webex bot not configured"
            return False
        try:
            resp = requests.post(
                WEBEX_MESSAGES_URL, headers=self._bot_headers(),
                json={"roomId": self.room_id, "markdown": markdown},
                timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return True
            self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            return False
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            return False


# ---------------------------------------------------------------------------
# Notification hub — fan one alert out to every enabled + configured channel
# ---------------------------------------------------------------------------

class NotificationHub:
    def __init__(self, teams: TeamsClient, email: EmailNotifier, webex: WebexNotifier,
                 teams_enabled: bool = True, email_enabled: bool = False,
                 webex_enabled: bool = False):
        self.teams = teams
        self.email = email
        self.webex = webex
        self.teams_enabled = teams_enabled
        self.email_enabled = email_enabled
        self.webex_enabled = webex_enabled

    @classmethod
    def from_settings(cls, s: dict) -> "NotificationHub":
        timeout = float(s.get("http_timeout_s", 10.0))
        teams = TeamsClient(s.get("teams_webhook_url", ""), timeout)
        email = EmailNotifier(
            host=s.get("smtp_host", ""), port=int(s.get("smtp_port", 587)),
            security=s.get("smtp_security", "starttls"),
            username=s.get("smtp_user", ""), password=s.get("smtp_password", ""),
            from_addr=s.get("email_from", ""),
            recipients=s.get("email_recipients", []), timeout=timeout)
        webex = WebexNotifier(
            mode=s.get("webex_mode", "webhook"),
            webhook_url=s.get("webex_webhook_url", ""),
            bot_token=s.get("webex_bot_token", ""),
            room_id=s.get("webex_room_id", ""), timeout=timeout)
        return cls(teams, email, webex,
                   teams_enabled=bool(s.get("teams_enabled", True)),
                   email_enabled=bool(s.get("email_enabled", False)),
                   webex_enabled=bool(s.get("webex_enabled", False)))

    def _active_channels(self) -> list[str]:
        out = []
        if self.teams_enabled and self.teams.is_configured():
            out.append("teams")
        if self.email_enabled and self.email.is_configured():
            out.append("email")
        if self.webex_enabled and self.webex.is_configured():
            out.append("webex")
        return out

    def is_any_configured(self) -> bool:
        return bool(self._active_channels())

    def dispatch(self, payload: AlertPayload,
                 png_bytes: bytes | None = None) -> dict[str, str]:
        """Send to every enabled+configured channel. Return {channel: error}.

        Safe to call from a worker thread; never raises.
        """
        errors: dict[str, str] = {}
        if self.teams_enabled and self.teams.is_configured():
            if not self.teams.post_payload(payload, png_bytes):
                errors["teams"] = self.teams.last_error
        if self.email_enabled and self.email.is_configured():
            if not self.email.send_payload(payload, png_bytes):
                errors["email"] = self.email.last_error
        if self.webex_enabled and self.webex.is_configured():
            if not self.webex.send(payload, png_bytes):
                errors["webex"] = self.webex.last_error
        return errors

    def send_all_tests(self) -> dict[str, str]:
        """Send a test message to every enabled+configured channel."""
        errors: dict[str, str] = {}
        if self.teams_enabled and self.teams.is_configured():
            if not self.teams.send_test():
                errors["teams"] = self.teams.last_error
        if self.email_enabled and self.email.is_configured():
            if not self.email.send_test():
                errors["email"] = self.email.last_error
        if self.webex_enabled and self.webex.is_configured():
            if not self.webex.send_test():
                errors["webex"] = self.webex.last_error
        return errors
