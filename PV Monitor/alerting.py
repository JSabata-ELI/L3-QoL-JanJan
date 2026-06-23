"""
Alert state machine + Microsoft Teams webhook client for PV Monitor.

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
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """Returned when the caller should send a Teams message."""
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
