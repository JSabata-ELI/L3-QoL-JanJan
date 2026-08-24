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
  - Settle: when a PV first leaves OK, notifications are held for
    `settle_minutes` to let a transient excursion stabilise. When the window
    closes, one message is sent if the PV is still in Warning/Alarm; an
    excursion that recovered within the window stays completely silent.
  - Stability hold: a committed transition is only announced after the level
    has stayed unchanged for `stable_seconds`; every further transition inside
    the window restarts the clock. A value oscillating across a limit thus
    sends nothing until it sticks — and nothing at all if it ends up back at
    the level it started from.
  - Notify only on a committed transition; re-notify a stuck WARNING/ALARM at
    most every `renotify_cooldown_minutes`. NODATA (value None) never alerts.

Notification channels (all are safe to call from a worker thread, never raise):
  - TeamsClient      -> Microsoft Teams Incoming Webhook (text card, no image)
  - EmailNotifier    -> SMTP, supports a PNG attachment
  - WebexNotifier    -> Webex incoming webhook (text) or bot API (text + file,
                        broadcast to any number of rooms)
  - NotificationHub  -> fans a single alert out to all enabled+configured ones
"""

from __future__ import annotations

import json
import os
import smtplib
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage
from enum import IntEnum
from pathlib import Path
from typing import Optional

import requests


# ── Run status: is the app up, and is it actually tracking? ──────────────────
# remote_launcher.py starts the app on a Webex command and then has to answer
# "done, it is running" — so from a DIFFERENT process it must be able to see
# both that the app came up and that monitoring armed itself. That is what this
# little file carries.
#
# It lives in %APPDATA%\Diagnostic, deliberately NOT next to the program: the
# app is started either from the source folder or from a built version under
# C:\Dev\dist\Diagnostic\vX.Y.Z, and a status file next to the program would be
# a different file for each of them. The watcher would then look at the wrong
# one, decide nothing is running, and start a second copy of an app that is
# already open.
#
# Qt-free and dependency-free on purpose: the watcher imports this module and
# must stay a lightweight always-on process.

def run_status_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Diagnostic" / "run_status.json"


def read_run_status() -> dict:
    """The last written status, or {} when there is none / it is unreadable.
    Never raises: a missing or half-written file only means "nothing known"."""
    try:
        with open(run_status_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_run_status(**fields) -> None:
    """Merge `fields` into the status file. Merging, not overwriting, because
    two writers share it: main.py records the PID at startup and monitor_tab
    records the monitoring switch later — neither knows the other's fields."""
    data = read_run_status()
    data.update(fields)
    try:
        p = run_status_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def clear_run_status() -> None:
    try:
        run_status_path().unlink()
    except OSError:
        pass


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
    # When the first alert of the *current* non-OK episode was sent (0 when OK).
    # Reset to 0 on recovery so each episode reports its own first-alert time.
    first_notified_ns: int = 0
    pending_level: Optional[AlertLevel] = None
    pending_count: int = 0
    # Settle window: when a PV first leaves OK, notifications are held until
    # this deadline to give the value a chance to stabilise. 0 = not settling.
    settle_until_ns: int = 0
    # Worst committed level seen during the settle window (for the summary).
    settle_peak_level: AlertLevel = AlertLevel.OK
    # Stability hold: when a transition commits, the notification is held
    # until the level has stayed unchanged for `stable_seconds`. Every further
    # transition restarts the clock. 0 = no hold pending.
    hold_since_ns: int = 0
    # Level in force before the (possibly flapping) episode began; the eventual
    # message reports prev -> current, or nothing if it flapped back to prev.
    hold_prev_level: AlertLevel = AlertLevel.OK


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
    renotify_cooldown_minutes: float = 30.0
    recovery_notify: bool = True
    # When a PV first leaves OK, hold every notification for this many minutes
    # so a transient excursion can settle. After the wait, one message is sent
    # only if the PV is still in Warning/Alarm; if it recovered within the
    # window, nothing is sent. 0 = notify immediately (legacy behaviour).
    settle_minutes: float = 7.0
    # A committed transition is only announced once the level has stayed
    # unchanged for this many seconds; each further transition restarts the
    # clock, so a value oscillating across a limit stays silent until it
    # sticks. 0 = announce immediately (legacy behaviour).
    stable_seconds: float = 120.0


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


def fmt_value(x: float) -> str:
    """Format a measured value for messages: at least one decimal for
    ordinary magnitudes (20 -> '20.0', 20.013 -> '20.0'), but significant
    digits for small ones so e.g. a pressure of 0.0104 Torr doesn't collapse
    to '0.0'. Kept consistent across all channels."""
    if x == 0 or abs(x) >= 0.1:
        return f"{x:.1f}"
    return f"{x:.3g}"


def fmt_duration(seconds: float) -> str:
    """Human-readable span for messages and table tooltips: '3 d 4 h',
    '5 h 12 min', '45 min', '30 s'."""
    s = int(max(0.0, seconds))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{s} s"


def describe_reason(level: AlertLevel, value: float, thr: Thresholds) -> str:
    if level == AlertLevel.OK:
        return "Back to normal range"
    lo = thr.alarm_low if level == AlertLevel.ALARM else thr.warn_low
    hi = thr.alarm_high if level == AlertLevel.ALARM else thr.warn_high
    name = level.label
    if lo is not None and value <= lo:
        return f"{name} low: {fmt_value(value)} ≤ {lo:g}"
    if hi is not None and value >= hi:
        return f"{name} high: {fmt_value(value)} ≥ {hi:g}"
    # Committed level no longer matches the raw bounds: the value is back
    # inside the limits but the recovery hasn't finished debouncing /
    # stabilising yet.
    return (f"{name} held: {fmt_value(value)} is back within limits, "
            f"recovery pending")


# ---------------------------------------------------------------------------
# Recent-trend classification (pure) — used to speed up / slow down the
# re-notify reminders of an already-alarming PV. Kept free of Qt/history
# objects so it can be unit tested: the caller passes raw (timestamp, value)
# samples read from wherever it keeps them.
# ---------------------------------------------------------------------------

class Trend(IntEnum):
    FALLING = -1
    FLAT = 0
    RISING = 1


def classify_trend(samples, now_ns: int, lookback_s: float,
                   n_windows: int = 5, flat_frac: float = 0.02) -> Trend:
    """Classify the recent direction of a value from time-stamped samples.

    `samples` is any iterable of (timestamp_ns, value) pairs (any order); only
    those within the last `lookback_s` seconds of `now_ns` are considered. That
    span is split into `n_windows` overlapping windows (50% overlap); each
    window's mean is taken, ordered old -> new. The classification is:

      - FLAT if there is too little data (< 2 non-empty windows), if the
        relative change between the oldest and newest window means is smaller
        than `flat_frac`, or if the per-window means do not move consistently
        in one direction (choppy data).
      - RISING / FALLING otherwise, describing the raw value only. The caller
        maps that to "improving" vs "worsening" using which bound alarmed.

    Overlapping, pre-averaged windows make this robust to single-sample noise
    and stop slow wander from being read as a real local trend.
    """
    if lookback_s <= 0 or n_windows < 2:
        return Trend.FLAT

    start_ns = now_ns - int(lookback_s * 1e9)
    pts = [(t, v) for (t, v) in samples
           if v is not None and t >= start_ns and t <= now_ns]
    if len(pts) < n_windows:
        return Trend.FLAT
    pts.sort(key=lambda p: p[0])

    span_ns = now_ns - start_ns
    # width = span * 2/(n+1), step = width/2  =>  n windows with 50% overlap
    # tiling [start, now]; window i covers [start + i*step, start + i*step + width].
    width_ns = span_ns * 2.0 / (n_windows + 1)
    step_ns = width_ns / 2.0

    means: list[float] = []
    for i in range(n_windows):
        w_lo = start_ns + i * step_ns
        w_hi = w_lo + width_ns
        vals = [v for (t, v) in pts if w_lo <= t <= w_hi]
        if vals:
            means.append(sum(vals) / len(vals))
    if len(means) < 2:
        return Trend.FLAT

    mean_old, mean_new = means[0], means[-1]
    denom = max(abs(mean_old), 1e-12)
    rel_change = (mean_new - mean_old) / denom
    if abs(rel_change) < flat_frac:
        return Trend.FLAT

    overall = 1 if mean_new > mean_old else -1
    # Consistency guard: the majority of consecutive steps must agree with the
    # overall direction, otherwise the data is choppy and we call it FLAT.
    steps = [means[i + 1] - means[i] for i in range(len(means) - 1)]
    agree = sum(1 for d in steps if (d > 0) == (overall > 0) and d != 0)
    if agree * 2 < len(steps):
        return Trend.FLAT

    return Trend.RISING if overall > 0 else Trend.FALLING


# ---------------------------------------------------------------------------
# Frozen-value detection (pure) — catches a PV that keeps delivering data while
# the reading behind it has stopped moving (dead sensor, stuck IOC, a control
# system that republishes its last value). Such a PV looks perfectly healthy:
# samples keep arriving with fresh timestamps, the value sits inside its limits
# (or outside them, alarming on data that is days old), and nothing else in the
# monitor notices. Kept free of Qt/history objects so it can be unit tested:
# the caller passes raw (timestamp, value) samples read from wherever it keeps
# them.
# ---------------------------------------------------------------------------

@dataclass
class FrozenInfo:
    """Outcome of one frozen-value check.

    ``bounded`` distinguishes "we saw it change at ``since_ns``" from "it was
    already at this value at the start of the data we have", i.e. the freeze is
    at least ``span_s`` long but may well be older.
    """
    frozen: bool = False
    since_ns: int = 0        # timestamp of the oldest sample carrying the value
    span_s: float = 0.0      # how long the value has been unchanged, up to now
    n_points: int = 0        # samples making up that unchanged run
    bounded: bool = False    # True = a different value precedes the run


def _same_value(a: float, b: float, rel_tol: float, abs_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def detect_frozen(samples, now_ns: int, frozen_after_s: float,
                  min_points: int = 5, rel_tol: float = 1e-9,
                  abs_tol: float = 0.0) -> FrozenInfo:
    """Detect a value that has not moved for at least `frozen_after_s` seconds.

    `samples` is any iterable of (timestamp_ns, value) pairs (any order); only
    those at or before `now_ns` count. The newest sample's value is the
    reference: the check walks back through the samples while they still carry
    that same value (within tolerance) and measures the run's length up to
    `now_ns`, so an ongoing freeze keeps growing between calls.

    The tolerance is deliberately near-exact — the point is "literally the same
    number over and over", not "roughly steady". A real sensor's noise always
    moves the last digit; only a stuck one repeats it exactly.

    A run counts as frozen only when it is both long enough AND carried by at
    least `min_points` samples, so sparse data (two readings hours apart) is
    never mistaken for a stuck sensor. `frozen_after_s` <= 0 disables the check.
    """
    if frozen_after_s <= 0:
        return FrozenInfo()

    pts = [(t, v) for (t, v) in samples if v is not None and t <= now_ns]
    if not pts:
        return FrozenInfo()
    pts.sort(key=lambda p: p[0])

    ref = pts[-1][1]
    i = len(pts) - 1
    while i > 0 and _same_value(pts[i - 1][1], ref, rel_tol, abs_tol):
        i -= 1

    span_s = max(0.0, (now_ns - pts[i][0]) / 1e9)
    n_points = len(pts) - i
    return FrozenInfo(
        frozen=(span_s >= frozen_after_s and n_points >= max(1, min_points)),
        since_ns=pts[i][0], span_s=span_s, n_points=n_points,
        bounded=(i > 0))


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

class AlertEvaluator:
    def __init__(self, config: EvalConfig | None = None):
        self.config = config or EvalConfig()

    def evaluate(self, state: AlertState, value: Optional[float],
                 thr: Thresholds, now_ns: int,
                 cooldown_scale: float = 1.0) -> Optional[Notification]:
        """Update `state` in place; return a Notification to send, or None.

        value is None (NODATA) leaves the committed level untouched and never
        alerts; it only clears any pending debounce so a gap doesn't commit a
        stale transition.

        `cooldown_scale` stretches (>1) or shrinks (<1) only the re-notify
        reminder interval for a stuck WARNING/ALARM — the caller sets it from
        the value's recent trend (improving -> slower, worsening -> faster).
        The default 1.0 leaves the reminder rhythm exactly as configured and
        affects nothing else in the state machine.
        """
        cfg = self.config

        if value is None:
            state.pending_level = None
            state.pending_count = 0
            return None

        committed = state.level

        # --- target severity: the raw thresholds, nothing else --------------
        target = _raw_severity(value, thr)

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
                note = self._commit(state, target, value, thr, now_ns)
                # A commit while a settle window is open is suppressed (note is
                # None) — fall through so the window can resolve this pass.
                if note is not None or not state.settle_until_ns:
                    return note

        # --- settle window: held notifications resolve here -----------------
        if state.settle_until_ns:
            if now_ns < state.settle_until_ns:
                return None                     # still giving it time to settle
            return self._resolve_settle(state, value, thr, now_ns)

        # --- stability hold: announce only once the level stops changing ----
        if state.hold_since_ns:
            stable_ns = int(cfg.stable_seconds * 1e9)
            if now_ns - state.hold_since_ns < stable_ns:
                return None                     # still waiting for it to stick
            prev = state.hold_prev_level
            state.hold_since_ns = 0
            state.hold_prev_level = AlertLevel.OK
            if state.level == prev or not self._should_notify(prev, state.level):
                return None      # flapped back to where it started — silent
            state.last_notified_ns = now_ns
            if state.level != AlertLevel.OK and state.first_notified_ns == 0:
                state.first_notified_ns = now_ns
            reason = (describe_reason(state.level, value, thr)
                      + f" — stable for {cfg.stable_seconds:g} s")
            return Notification(level=state.level, prev_level=prev,
                                value=value, reason=reason, kind="transition")

        # --- re-notify cooldown for a stuck WARNING/ALARM ------------------
        if committed in (AlertLevel.WARNING, AlertLevel.ALARM) and \
                cfg.renotify_cooldown_minutes > 0:
            scale = cooldown_scale if cooldown_scale > 0 else 1.0
            cooldown_ns = int(cfg.renotify_cooldown_minutes * 60 * 1e9 * scale)
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
        if target == AlertLevel.OK:
            state.first_notified_ns = 0        # episode ended — arm for the next

        settle_ns = int(self.config.settle_minutes * 60 * 1e9)
        if settle_ns > 0:
            if state.settle_until_ns:
                # Window already open: track the worst level, stay silent.
                if target > state.settle_peak_level:
                    state.settle_peak_level = target
                return None
            if prev == AlertLevel.OK and target != AlertLevel.OK \
                    and not state.hold_since_ns:
                # First departure from OK: open the settle window instead of
                # notifying right away. (Not while a stability hold is open —
                # the hold already covers the flapping episode.)
                state.settle_until_ns = now_ns + settle_ns
                state.settle_peak_level = target
                return None

        if not self._should_notify(prev, target):
            return None

        stable_ns = int(self.config.stable_seconds * 1e9)
        if stable_ns > 0:
            # Don't announce yet: (re)start the stability clock. The level in
            # force before the episode began is kept, so the eventual message
            # reports the true transition — or nothing if it flapped back.
            if not state.hold_since_ns:
                state.hold_prev_level = prev
            state.hold_since_ns = now_ns or 1   # 0 would read as "no hold"
            return None

        state.last_notified_ns = now_ns
        if target != AlertLevel.OK and state.first_notified_ns == 0:
            state.first_notified_ns = now_ns
        return Notification(
            level=target, prev_level=prev, value=value,
            reason=describe_reason(target, value, thr), kind="transition",
        )

    def _resolve_settle(self, state: AlertState, value: float,
                        thr: Thresholds, now_ns: int) -> Optional[Notification]:
        """Close an expired settle window and report the state it ended in."""
        mins = self.config.settle_minutes
        state.settle_until_ns = 0
        state.settle_peak_level = AlertLevel.OK
        level = state.level

        if level == AlertLevel.OK:
            # Excursion came and went within the window. Nothing was ever
            # announced, so stay silent — an all-clear for an alert nobody
            # saw is just noise.
            return None

        state.last_notified_ns = now_ns
        if state.first_notified_ns == 0:
            state.first_notified_ns = now_ns
        reason = (describe_reason(level, value, thr)
                  + f" — did not settle within {mins:g} min")
        return Notification(level=level, prev_level=AlertLevel.OK,
                            value=value, reason=reason, kind="transition")

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
        return f"{fmt_value(self.value)} {self.units}".strip()

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
    """Resolve a stored secret to usable plaintext at send time.

    Delegates to ``secrets_util`` which understands ``${ENV:NAME}`` references
    and ``dpapi:`` (Windows DPAPI) encrypted blobs, so secrets need not be kept
    as literal plaintext in monitor_config.json.
    """
    from secrets_util import resolve_secret
    return resolve_secret(value)


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
    val_str = f"{fmt_value(value)} {units}".strip()
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


def build_textcard(title: str, text: str, color: str = "1565C0") -> dict:
    """Build a plain title+text Teams MessageCard (used for chart replies,
    which have no single PV/level to build a fact table from)."""
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": title,
        "title": title,
        "sections": [{"text": text, "markdown": True}],
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
    mode == "bot":     POST to the Messages API with a bot token, once per
                       room in `room_ids` (one bot broadcasting to many
                       rooms). A PNG is attached as multipart/form-data
                       when given. Two-way commands are only read from
                       `listen_room_id` (a bot can only usefully answer
                       commands in one place).
    """

    def __init__(self, mode: str = "webhook", webhook_url: str = "",
                 bot_token: str = "", room_ids: Optional[list[str]] = None,
                 listen_room_id: str = "", timeout: float = 10.0):
        self.mode = (mode or "webhook").lower()
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.room_ids = list(room_ids or [])
        self.listen_room_id = listen_room_id or (self.room_ids[0] if self.room_ids else "")
        self.timeout = timeout
        self.last_error: str = ""
        # Set when Webex answers HTTP 429; the poller reads it and pauses for
        # that many seconds (Retry-After header) before polling again.
        self.retry_after_s: float = 0.0
        # Group spaces 403 unless mentionedPeople=me is set. Once we learn
        # that, keep using the filter instead of paying two requests per poll.
        self._mentioned_only = False
        # Set the moment we learn this is a group space, i.e. that the bot can
        # only see messages that @mention it. The caller reports it once, because
        # "the bot ignores my commands" is otherwise indistinguishable from a
        # broken token, and the fix (tag the bot) is not guessable.
        self.mention_only_notice = False
        # The bot's own name in Webex, filled in by get_me_id. Used to tell the
        # operator exactly what to type — the name is whatever the bot was
        # registered as, so hard-coding one in the help text would be a guess.
        self.bot_name: str = ""
        # IDs of messages this bot itself posted — a hard backstop against the
        # bot reading back and "replying to" its own messages (personId
        # filtering in the caller can fail transiently; this cannot, since it
        # never depends on a second API call).
        self._own_message_ids: deque = deque(maxlen=200)

    def _remember_own_message(self, resp) -> None:
        try:
            mid = resp.json().get("id")
        except Exception:  # noqa: BLE001 - best effort only
            mid = None
        if mid:
            self._own_message_ids.append(mid)

    def is_own_message(self, message_id: Optional[str]) -> bool:
        return bool(message_id) and message_id in self._own_message_ids

    def is_configured(self) -> bool:
        if self.mode == "bot":
            return bool(self.bot_token and self.room_ids)
        return bool(self.webhook_url and self.webhook_url.lower().startswith("http"))

    def _post_to_room(self, headers: dict, room_id: str,
                      payload: "AlertPayload", png_bytes: bytes | None):
        if png_bytes:
            files = {
                "roomId": (None, room_id),
                "markdown": (None, payload.markdown_body()),
                "files": ("plot.png", png_bytes, "image/png"),
            }
            return requests.post(WEBEX_MESSAGES_URL, headers=headers,
                                 files=files, timeout=self.timeout)
        return requests.post(
            WEBEX_MESSAGES_URL, headers=headers,
            json={"roomId": room_id, "markdown": payload.markdown_body()},
            timeout=self.timeout)

    def send(self, payload: "AlertPayload", png_bytes: bytes | None = None) -> bool:
        if not self.is_configured():
            self.last_error = "Webex not fully configured"
            return False
        try:
            if self.mode == "bot":
                token = _resolve_secret(self.bot_token)
                headers = {"Authorization": f"Bearer {token}"}
                errors = []
                for room_id in self.room_ids:
                    resp = self._post_to_room(headers, room_id, payload, png_bytes)
                    if 200 <= resp.status_code < 300:
                        self._remember_own_message(resp)
                    else:
                        errors.append(f"{room_id}: HTTP {resp.status_code} "
                                     f"{resp.text[:120]}")
                if errors:
                    self.last_error = "; ".join(errors)
                    return False
                self.last_error = ""
                return True
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

    def post_markdown(self, markdown: str, png_bytes: bytes | None = None) -> bool:
        """Broadcast free-form markdown (+ optional PNG) to every configured
        room. Used for replies that are not a single-PV alert, e.g. a chart of
        several PVs. Bot mode only; a webhook cannot carry a file."""
        if not self.is_configured():
            self.last_error = "Webex not fully configured"
            return False
        try:
            if self.mode != "bot":
                md = markdown
                if png_bytes:
                    md += "\n\n_(graph attached in the e-mail alert)_"
                resp = requests.post(self.webhook_url, json={"markdown": md},
                                     timeout=self.timeout, verify=True)
                if 200 <= resp.status_code < 300:
                    self.last_error = ""
                    return True
                self.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return False
            headers = {"Authorization": f"Bearer {_resolve_secret(self.bot_token)}"}
            errors = []
            for room_id in self.room_ids:
                if png_bytes:
                    resp = requests.post(
                        WEBEX_MESSAGES_URL, headers=headers,
                        files={"roomId": (None, room_id),
                               "markdown": (None, markdown),
                               "files": ("plot.png", png_bytes, "image/png")},
                        timeout=self.timeout)
                else:
                    resp = requests.post(
                        WEBEX_MESSAGES_URL, headers=headers,
                        json={"roomId": room_id, "markdown": markdown},
                        timeout=self.timeout)
                if 200 <= resp.status_code < 300:
                    self._remember_own_message(resp)
                else:
                    errors.append(f"{room_id}: HTTP {resp.status_code} "
                                  f"{resp.text[:120]}")
            if errors:
                self.last_error = "; ".join(errors)
                return False
            self.last_error = ""
            return True
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
        """Reading messages needs a bot token + a designated room (webhooks can't read)."""
        return bool(self.mode == "bot" and self.bot_token and self.listen_room_id)

    def _bot_headers(self) -> dict:
        return {"Authorization": f"Bearer {_resolve_secret(self.bot_token)}"}

    def _note_rate_limit(self, resp) -> None:
        """Record a 429 so the caller can honour Retry-After instead of hammering."""
        try:
            ra = float(resp.headers.get("Retry-After", "") or 30.0)
        except (TypeError, ValueError):
            ra = 30.0
        self.retry_after_s = min(max(ra, 5.0), 600.0)
        self.last_error = (f"HTTP 429: rate limited by Webex — pausing polls "
                           f"for {self.retry_after_s:.0f}s (Retry-After)")

    def mention_name(self) -> str:
        """What the operator has to type to tag this bot, e.g. "@Diagnostics".

        The real name comes from the Webex API (get_me_id) as soon as the listener
        has run once. Before that — and in the Settings dialog, which cannot wait
        for a network call — the documented example stands in."""
        return f"@{self.bot_name}" if self.bot_name else "@Diagnostics"

    def get_me_id(self) -> Optional[str]:
        """Return the bot's own personId (to skip its own messages). None on error.

        Also records the bot's own name in passing: the same request already
        carries it, and it is what the help text needs to tell the operator what
        to type."""
        if not self.can_listen():
            return None
        try:
            resp = requests.get(WEBEX_ME_URL, headers=self._bot_headers(),
                                timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                data = resp.json()
                # nickName is the short form Webex actually completes on when you
                # type "@…"; displayName can carry a surname the tag does not need.
                self.bot_name = (data.get("nickName")
                                 or data.get("displayName") or "").strip()
                return data.get("id")
            if resp.status_code == 429:
                self._note_rate_limit(resp)
                return None
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
        params = {"roomId": self.listen_room_id, "max": max_count}
        if self._mentioned_only:
            params["mentionedPeople"] = "me"
        try:
            resp = requests.get(WEBEX_MESSAGES_URL, headers=self._bot_headers(),
                                params=params, timeout=self.timeout)
            if resp.status_code == 403 and not self._mentioned_only:
                self._mentioned_only = True
                self.mention_only_notice = True
                resp = requests.get(
                    WEBEX_MESSAGES_URL, headers=self._bot_headers(),
                    params={**params, "mentionedPeople": "me"}, timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                return resp.json().get("items", [])
            if resp.status_code == 429:
                self._note_rate_limit(resp)
                return []
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
                json={"roomId": self.listen_room_id, "markdown": markdown},
                timeout=self.timeout)
            if 200 <= resp.status_code < 300:
                self.last_error = ""
                self._remember_own_message(resp)
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

        contacts = s.get("email_contacts", [])
        recipients = (s.get("email_recipients")
                     or [c["address"] for c in contacts
                         if c.get("enabled") and c.get("address")])
        email = EmailNotifier(
            host=s.get("smtp_host", ""), port=int(s.get("smtp_port", 587)),
            security=s.get("smtp_security", "starttls"),
            username=s.get("smtp_user", ""), password=s.get("smtp_password", ""),
            from_addr=s.get("email_from", ""),
            recipients=recipients, timeout=timeout)

        rooms = s.get("webex_rooms", [])
        room_ids = [r["room_id"] for r in rooms
                   if r.get("enabled") and r.get("room_id")]
        listen_room_id = next(
            (r["room_id"] for r in rooms if r.get("listen") and r.get("room_id")), "")
        webex = WebexNotifier(
            mode=s.get("webex_mode", "webhook"),
            webhook_url=s.get("webex_webhook_url", ""),
            bot_token=s.get("webex_bot_token", ""),
            room_ids=room_ids, listen_room_id=listen_room_id, timeout=timeout)

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

    def dispatch_chart(self, title: str, text_body: str, markdown_body: str,
                       png_bytes: bytes | None = None) -> dict[str, str]:
        """Send a chart (or its text fallback) to every enabled+configured
        channel. Return {channel: error}.

        Unlike ``dispatch`` this carries no PV state — it is for replies about
        several PVs at once, where a single-PV alert card makes no sense.
        Worker-thread safe; never raises.
        """
        errors: dict[str, str] = {}
        if self.teams_enabled and self.teams.is_configured():
            # Teams Incoming Webhooks cannot embed raw image bytes — text only.
            if not self.teams.post(build_textcard(title, markdown_body)):
                errors["teams"] = self.teams.last_error
        if self.email_enabled and self.email.is_configured():
            if not self.email.send(title, text_body, png_bytes):
                errors["email"] = self.email.last_error
        if self.webex_enabled and self.webex.is_configured():
            if not self.webex.post_markdown(markdown_body, png_bytes):
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
