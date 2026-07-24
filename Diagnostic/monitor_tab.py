"""
PV Monitor tab for the Diagnostic app.

Watches a user-chosen list of CPVA archiver PVs (temperatures, humidity, …)
against user-set warning/alarm limits and, on a committed state change, sends
an alert to every enabled channel (Email, Webex, Teams). Alerts include a PNG
trend of the offending PV over the last N hours; there is also a manual
"Send plot now" button.

Adapted from the former standalone PV Monitor app: the QMainWindow became this
embeddable QWidget (toolbar -> button row, no statusbar/geometry), the single
TeamsClient became a NotificationHub, and a worker-thread plot renderer +
dispatcher were added so the UI never blocks on network/render.

See cpva_api.py (archiver client) and alerting.py (state machine + notifiers).
"""

from __future__ import annotations

import copy
import json
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QObject, QRunnable,
    Qt, QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QProgressDialog, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

import cpva_api as api
from alerting import (
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EvalConfig,
    NotificationHub, Thresholds, Trend, classify_trend, describe_reason,
)
from secrets_util import encrypt_secret

# ---------------------------------------------------------------------------
# Shared styles (kept local so this module has no import cycle with main.py)
# ---------------------------------------------------------------------------

BUTTON_STYLE = """
QPushButton {
    background: #1565C0;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
}
QPushButton:hover { background: #0D47A1; }
QPushButton:disabled { background: #bbb; color: #888; }
"""

STOP_BUTTON_STYLE = """
QPushButton {
    background: #c0392b;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: bold;
}
QPushButton:hover { background: #a93226; }
QPushButton:disabled { background: #bbb; color: #888; }
"""

SECONDARY_STYLE = """
QPushButton {
    background: #e8e8e8;
    color: #111;
    border: 1px solid #bbb;
    border-radius: 4px;
    padding: 5px 12px;
}
QPushButton:hover { background: #d8e8ff; }
"""

LOG_STYLE = """
QPlainTextEdit {
    background: #ffffff;
    color: #222;
    font-family: Consolas, monospace;
    font-size: 11px;
    border: 1px solid #ccc;
}
"""


def _btn(label, style=BUTTON_STYLE):
    b = QPushButton(label)
    b.setStyleSheet(style)
    return b


class LogWidget(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setStyleSheet(LOG_STYLE)

    def append_line(self, text):
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ---------------------------------------------------------------------------
# Wheel-safe inputs
# ---------------------------------------------------------------------------
# Spin boxes and combo boxes change their value on mouse-wheel by default,
# which silently edits fields while you scroll a dialog. These variants only
# react to the wheel once the field has keyboard focus (click or Tab into it);
# otherwise the wheel scrolls the surrounding form as expected.

class _NoWheelMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Drop WheelFocus so scrolling over the widget doesn't focus it.
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()   # let the scroll area handle it instead


class _NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    pass


class _NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    pass


class _NoWheelComboBox(_NoWheelMixin, QComboBox):
    pass


# ---------------------------------------------------------------------------
# Dialog styles (carried over from the original app for dialog widgets)
# ---------------------------------------------------------------------------

PRIMARY, PRIMARY_HOVER = "#1565C0", "#0D47A1"
SUCCESS = "#2E7D32"
DANGER = "#B71C1C"
WARN_COLOR = "#cc6600"
ALARM_COLOR = "#cc2200"
NODATA_COLOR = "#9e9e9e"
BADDATA_COLOR = "#8e24aa"  # out-of-range sensor error (distinct from grey no-data)

_BTN_PRIMARY = (
    "QPushButton { background:#1565C0; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#0D47A1; }"
    "QPushButton:disabled { background:#bbb; color:#888; }"
)
_BTN_SUCCESS = (
    "QPushButton { background:#2E7D32; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#1B5E20; }"
    "QPushButton:disabled { background:#bbb; color:#888; }"
)
_CHK_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator {
    width: 17px; height: 17px;
    border: 2px solid #4a4a4a; border-radius: 3px; background: #fff;
}
QCheckBox::indicator:hover  { border-color: #1565C0; background: #e8f0fe; }
QCheckBox::indicator:checked { border-color: #1565C0; background: #1565C0; }
"""
_GROUP_STYLE = """
QGroupBox {
    font-weight: 700; padding-top: 14px; margin-top: 8px;
    border: 1px solid #ccc; border-radius: 4px;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
"""

_STATE_BG = {
    AlertLevel.OK: QColor("#e6f4ea"),
    AlertLevel.WARNING: QColor(WARN_COLOR),
    AlertLevel.ALARM: QColor(ALARM_COLOR),
}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_FILE = api.APP_DIR / "monitor_config.json"

DEFAULT_SETTINGS = {
    # monitoring
    "poll_interval_s": 30,
    # Max PVs fetched concurrently per poll. The archiver call is I/O-bound
    # HTTP, so a pass's wall-clock is ~ceil(N / this) * per-request time; too
    # low a value makes a pass with many PVs overrun the interval, which skips
    # ticks and makes the table lag (see _PollWorker / _start_poll).
    "poll_max_workers": 24,
    "avg_last_n": 25,
    "sample_window_s": 60,
    "http_timeout_s": 10.0,
    "renotify_cooldown_minutes": 30,
    "recovery_notify": True,
    "debounce_count": 2,
    "settle_minutes": 5.0,
    "stable_seconds": 120,
    # Trend-adaptive reminders: for an already-alarming PV, speed up / slow down
    # the re-notify reminders based on its recent value trend (worsening ->
    # faster, self-correcting -> slower). Only touches the reminder rhythm, not
    # the alert state machine.
    "trend_adaptive_enabled": True,
    "trend_lookback_minutes": 10.0,
    "trend_flat_frac": 0.02,        # < this relative change (old->new) = "flat"
    "trend_speedup_factor": 2.0,    # worsening: reminder cooldown / this
    "trend_slowdown_factor": 2.0,   # improving: reminder cooldown * this
    "history_minutes": 720,
    # Data watchdog: alerts once when every monitored PV fails to fetch data
    # for this many consecutive polls (a network/archiver outage, not a
    # single PV's own no-data), and once more when data flow resumes.
    "data_watchdog_enabled": True,
    "data_watchdog_fail_polls": 2,
    "learn_days_default": 7,
    "warn_k_default": 3.0,
    "alarm_k_default": 5.0,
    # Sensor-error sanity range applied to every PV unless it overrides:
    # readings outside [valid_min_default, valid_max_default] are discarded.
    # None = no bound on that side.
    "valid_min_default": None,
    "valid_max_default": 80.0,
    "graph_window_minutes": 60,
    "start_monitoring_on_launch": False,
    # alert graph
    "alert_plot_hours": 12,
    # channels
    "teams_enabled": True,
    "email_enabled": False,
    "webex_enabled": False,
    # teams
    "teams_webhook_url": "",
    # email
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",     # none | starttls | ssl
    "smtp_user": "",
    "smtp_password": "",
    "email_from": "",
    "email_contacts": [],   # [{"address":..., "description":..., "enabled":...}, ...]
    # webex
    "webex_mode": "bot",             # webhook | bot  (bot supports the PNG graph)
    "webex_webhook_url": "",
    "webex_bot_token": "",
    "webex_rooms": [],      # [{"name":..., "room_id":..., "enabled":..., "listen":...}, ...]
    # webex two-way commands (bot mode only)
    "webex_commands_enabled": True,
    "webex_command_poll_s": 5,   # <5 s tends to trip Webex HTTP 429 rate limits
    "webex_command_allowlist": [],   # sender emails allowed; empty = anyone in room
}


def load_config() -> dict:
    data = {"version": 1, "settings": {}, "pvs": []}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            data.update(loaded)
        except Exception:
            pass
    settings = dict(DEFAULT_SETTINGS)
    settings.update(data.get("settings") or {})
    _migrate_settings(settings)
    data["settings"] = settings
    data.setdefault("pvs", [])
    return data


def _migrate_settings(settings: dict) -> None:
    """One-time upgrade from the old single-email/single-room schema."""
    if not settings.get("email_contacts") and settings.get("email_recipients"):
        settings["email_contacts"] = [
            {"address": addr, "description": "", "enabled": True}
            for addr in settings["email_recipients"]
        ]
    settings.pop("email_recipients", None)

    if not settings.get("webex_rooms") and settings.get("webex_room_id"):
        settings["webex_rooms"] = [{
            "name": "Main", "room_id": settings["webex_room_id"],
            "enabled": True, "listen": True,
        }]
    settings.pop("webex_room_id", None)


def save_config(data: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        print(f"[monitor config] save failed: {e}")


def _parse_recipients(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;,]", text or "") if p.strip()]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PVConfig:
    name: str
    display_name: str = ""
    units: str = ""
    group: str = ""
    subgroup: str = ""
    enabled: bool = False
    # Whether the PV is drawn in the trend graph (the 'Show' column).
    show_in_graph: bool = True
    # Default thresholds — used when no conditional profile below matches (the
    # ordinary single-profile case, and the fallback for the gated case).
    warn_low: Optional[float] = None
    warn_high: Optional[float] = None
    alarm_low: Optional[float] = None
    alarm_high: Optional[float] = None
    # Optional state gating on up to two "dependency" PVs (e.g. hall state +
    # sys-rate). ``gate_pvs`` holds their names (0, 1 or 2). ``profiles`` is an
    # ordered list of conditional rules; the first whose conditions all match
    # the current dependency-PV values (and whose thresholds are set) wins,
    # otherwise the default thresholds above apply. Each profile is a dict:
    #   {"label": str,
    #    "conds": [[min, max], ...],   # one [min, max] per entry in gate_pvs
    #    "warn_low", "warn_high", "alarm_low", "alarm_high"}
    # A None edge in a cond means "unbounded on that side"; a [None, None] cond
    # (or a missing one) means "any value" for that dependency.
    gate_pvs: list = field(default_factory=list)
    profiles: list = field(default_factory=list)
    # Sanity range: readings outside [valid_min, valid_max] are treated as
    # sensor errors and dropped (not plotted, not alarmed, excluded from Learn).
    # None on a side = fall back to the global valid_*_default setting.
    valid_min: Optional[float] = None
    valid_max: Optional[float] = None
    learned_at: Optional[str] = None
    learn_stats: Optional[dict] = None

    def __post_init__(self):
        if not self.display_name:
            self.display_name = api.shorten_pv_name(self.name)

    def thresholds(self) -> Thresholds:
        """Default thresholds (used when no conditional profile matches)."""
        return Thresholds(self.warn_low, self.warn_high,
                          self.alarm_low, self.alarm_high)

    @staticmethod
    def profile_thresholds(prof: dict) -> Thresholds:
        """Thresholds carried by a conditional profile."""
        return Thresholds(prof.get("warn_low"), prof.get("warn_high"),
                          prof.get("alarm_low"), prof.get("alarm_high"))

    @staticmethod
    def _profile_matches(prof: dict, gate_values: list) -> bool:
        """True if every dependency condition in ``prof`` holds for the current
        dependency-PV values. A [None, None] (or missing) cond is 'any'; a cond
        with a real edge against an unknown (None) value never matches."""
        conds = prof.get("conds") or []
        for i, cond in enumerate(conds):
            lo, hi = (cond + [None, None])[:2] if cond else (None, None)
            if lo is None and hi is None:
                continue                       # 'any value' for this dependency
            gv = gate_values[i] if i < len(gate_values) else None
            if gv is None:
                return False                   # condition set but value unknown
            if lo is not None and gv < lo:
                return False
            if hi is not None and gv > hi:
                return False
        return True

    def match_profile(self, gate_values: list) -> Optional[dict]:
        """First conditional profile whose conditions match and whose thresholds
        are set, else None (caller falls back to the default thresholds)."""
        for prof in self.profiles:
            if (self._profile_matches(prof, gate_values)
                    and self.profile_thresholds(prof).is_active()):
                return prof
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "display_name": self.display_name,
            "units": self.units, "group": self.group,
            "subgroup": self.subgroup, "enabled": self.enabled,
            "show_in_graph": self.show_in_graph,
            "warn_low": self.warn_low, "warn_high": self.warn_high,
            "alarm_low": self.alarm_low, "alarm_high": self.alarm_high,
            "gate_pvs": list(self.gate_pvs),
            "profiles": [dict(p) for p in self.profiles],
            "valid_min": self.valid_min, "valid_max": self.valid_max,
            "learned_at": self.learned_at, "learn_stats": self.learn_stats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PVConfig":
        gate_pvs, profiles = cls._read_gating(d)
        return cls(
            name=d["name"], display_name=d.get("display_name", ""),
            units=d.get("units", ""), group=d.get("group", ""),
            subgroup=d.get("subgroup", ""),
            enabled=bool(d.get("enabled", False)),
            show_in_graph=bool(d.get("show_in_graph", True)),
            warn_low=d.get("warn_low"), warn_high=d.get("warn_high"),
            alarm_low=d.get("alarm_low"), alarm_high=d.get("alarm_high"),
            gate_pvs=gate_pvs, profiles=profiles,
            valid_min=d.get("valid_min"), valid_max=d.get("valid_max"),
            learned_at=d.get("learned_at"), learn_stats=d.get("learn_stats"),
        )

    @staticmethod
    def _read_gating(d: dict) -> tuple:
        """Return (gate_pvs, profiles), migrating the old single-gate schema
        (gate_pv + gate_run_* + run_* thresholds) into one profile."""
        if d.get("gate_pvs") is not None or d.get("profiles") is not None:
            return list(d.get("gate_pvs") or []), [dict(p) for p in
                                                   (d.get("profiles") or [])]
        old_gate = d.get("gate_pv") or None
        if not old_gate:
            return [], []
        run_thr = {
            "warn_low": d.get("run_warn_low"), "warn_high": d.get("run_warn_high"),
            "alarm_low": d.get("run_alarm_low"), "alarm_high": d.get("run_alarm_high"),
        }
        if not any(v is not None for v in run_thr.values()):
            return [old_gate], []          # gate existed but had no run limits
        prof = {"label": "running",
                "conds": [[d.get("gate_run_min"), d.get("gate_run_max")]], **run_thr}
        return [old_gate], [prof]


@dataclass
class PVRuntime:
    current_value: Optional[float] = None
    current_units: str = ""
    last_update_ns: int = 0
    alert: AlertState = field(default_factory=AlertState)
    history: deque = field(default_factory=lambda: deque(maxlen=2000))
    last_error: str = ""
    rejected_count: int = 0
    bad_data: bool = False   # last poll returned samples but all out of range
    # Most recent raw reading regardless of range, kept so the Value column
    # can still show what the PV reports while it's out of [min, max] and
    # current_value is None (bad data). Not used for alerting/history.
    raw_value: Optional[float] = None
    # Conditional profile in force at the last poll (None = default thresholds).
    active_profile: Optional[dict] = None
    # Which threshold set the user pinned in the 'Depends on' dropdown:
    # None = Automatic (follow the first matching rule), -1 = Global forced,
    # i >= 0 = pin profiles[i] (its limits apply unconditionally).
    dep_view: Optional[int] = None
    # Delivery state of the current non-OK episode's alert, for the "Alarm status"
    # column: "" (nothing to send / OK), "sending", "sent", "failed".
    notify_status: str = ""
    notify_error: str = ""

    def display_level(self):
        """AlertLevel for colouring, or None for NODATA."""
        if self.current_value is None:
            return None
        return self.alert.level


# ---------------------------------------------------------------------------
# Workers (QThreadPool + QRunnable)
# ---------------------------------------------------------------------------

def _out_of_range(v: float, vmin, vmax) -> bool:
    return (vmin is not None and v < vmin) or (vmax is not None and v > vmax)


def _avg_recent_numeric(samples: list[dict], avg_n: int, vmin=None, vmax=None):
    """Return (avg_or_None, units, last_ts_ns, n_rejected, last_raw) from raw
    CPVA samples.

    Readings outside [vmin, vmax] are treated as sensor errors and dropped
    from the average used for alerting/history; n_rejected counts how many
    were discarded this pass. last_raw is the most recent decoded reading
    regardless of range, for display purposes only.
    """
    vals, units, last_ts, rejected = [], "", 0, 0
    last_raw, last_raw_ts = None, -1
    for s in samples:
        v = api.cpva_decode_value(s)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            fv = float(v)
            u = api.cpva_decode_units(s)
            if u:
                units = u
            t = s.get("time")
            ts = int(t) if isinstance(t, (int, float)) else 0
            if ts >= last_raw_ts:
                last_raw, last_raw_ts = fv, ts
            if _out_of_range(fv, vmin, vmax):
                rejected += 1
                if ts:
                    last_ts = max(last_ts, ts)  # note freshness even if bad
                continue
            vals.append(fv)
            if ts:
                last_ts = max(last_ts, ts)
    if not vals:
        return None, units, last_ts, rejected, last_raw
    recent = vals[-max(1, avg_n):]
    return sum(recent) / len(recent), units, last_ts, rejected, last_raw


def _safe_emit(sig_fn, value):
    try:
        sig_fn(value)
    except RuntimeError:
        pass  # signal source deleted (dialog/tab closed while worker ran)


class _PollSignals(QObject):
    done = Signal(object)   # {name: (val_or_None, units, last_ts_ns, err, n_rejected, raw_val)}
    log = Signal(str)


class _PollWorker(QRunnable):
    def __init__(self, sig: _PollSignals, names: list[str], settings: dict,
                 ranges: Optional[dict] = None):
        super().__init__()
        self._sig = sig
        self._names = names
        self._s = settings
        self._ranges = ranges or {}

    def run(self):
        window_ns = int(self._s["sample_window_s"] * 1e9)
        avg_n = int(self._s["avg_last_n"])
        timeout = float(self._s["http_timeout_s"])
        end = api.now_ns()
        start = end - window_ns

        def fetch_one(name):
            lo, hi = self._ranges.get(name, (None, None))
            try:
                samples = api.cpva_fetch_samples(name, start, end, timeout)
                val, units, last_ts, rejected, raw_val = _avg_recent_numeric(
                    samples, avg_n, lo, hi)
                if rejected:
                    err = (f"dropped {rejected} out-of-range reading(s) "
                           f"[{_fmt(lo)}..{_fmt(hi)}]")
                else:
                    err = ""
                return (val, units, last_ts or end, err, rejected, raw_val)
            except Exception as e:  # noqa: BLE001 - one bad PV can't kill the pass
                return (None, "", end, str(e), 0, None)

        # Archiver responses can take seconds each; fetching sequentially made
        # a full pass slower than the poll interval, so results were always
        # stale. Fetch PVs concurrently instead (same pattern as
        # cpva_fetch_samples_chunked). Concurrency is user-tunable
        # (poll_max_workers) because the ceiling that keeps a pass under the
        # poll interval scales with the number of monitored PVs.
        workers = int(self._s.get("poll_max_workers", 24))
        out = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(self._names)))) as ex:
            futures = {ex.submit(fetch_one, n): n for n in self._names}
            for fut in as_completed(futures):
                out[futures[fut]] = fut.result()
        _safe_emit(self._sig.done.emit, out)


class _BackfillSignals(QObject):
    done = Signal(object)   # {name: [(ts_ns, value), ...] sorted by time}
    log = Signal(str)


class _BackfillWorker(QRunnable):
    """Fetch archive samples covering the graph window so the plot starts
    pre-filled with recent history instead of only data polled from now on."""

    def __init__(self, sig: _BackfillSignals, names: list[str],
                 start_ns: int, end_ns: int, timeout: float,
                 ranges: dict, max_points: int):
        super().__init__()
        self._sig = sig
        self._names = names
        self._start = start_ns
        self._end = end_ns
        self._timeout = timeout
        self._ranges = ranges
        self._max_points = max(10, max_points)

    def run(self):
        def fetch_one(name):
            try:
                return name, api.cpva_fetch_samples_chunked(
                    name, self._start, self._end, self._timeout,
                    max_workers=4)
            except Exception as e:  # noqa: BLE001 - one bad PV can't kill the pass
                _safe_emit(self._sig.log.emit,
                           f"History backfill failed for "
                           f"{api.shorten_pv_name(name)}: {e}")
                return name, None

        # Sequential single-shot fetches over the full (multi-hour) history
        # window made launch's backfill take minutes with several PVs -- the
        # archiver is only reliable for <=1h windows, so a wide window was
        # both slow and dubious. Same fix as _PollWorker: fetch PVs
        # concurrently, each internally chunked into <=1h windows.
        out = {}
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(self._names)))) as ex:
            futures = {ex.submit(fetch_one, n): n for n in self._names}
            for fut in as_completed(futures):
                name, samples = fut.result()
                if samples is None:
                    continue
                lo, hi = self._ranges.get(name, (None, None))
                pts = []
                for s in samples:
                    v = api.cpva_decode_value(s)
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        continue
                    t = s.get("time")
                    ts = int(t) if isinstance(t, (int, float)) else 0
                    if not ts or _out_of_range(float(v), lo, hi):
                        continue
                    pts.append((ts, float(v)))
                pts.sort()
                if len(pts) > self._max_points:  # thin evenly to fit the history
                    step = len(pts) / self._max_points
                    pts = [pts[int(i * step)] for i in range(self._max_points)]
                out[name] = pts
        _safe_emit(self._sig.done.emit, out)


class _ChannelsSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class _ChannelsWorker(QRunnable):
    def __init__(self, sig: _ChannelsSignals, timeout: float):
        super().__init__()
        self._sig = sig
        self._timeout = timeout

    def run(self):
        try:
            channels = api.cpva_fetch_channels(self._timeout)
            _safe_emit(self._sig.done.emit, channels)
        except Exception as e:  # noqa: BLE001
            _safe_emit(self._sig.error.emit, str(e))


class _LearnSignals(QObject):
    done = Signal(object)   # dict of thresholds + stats
    error = Signal(str)
    log = Signal(str)
    progress = Signal(object)   # (done_chunks, total_chunks)


class _LearnWorker(QRunnable):
    def __init__(self, sig: _LearnSignals, name: str, days: int,
                 warn_k: float, alarm_k: float, timeout: float,
                 valid_min=None, valid_max=None):
        super().__init__()
        self._sig = sig
        self._name = name
        self._days = days
        self._warn_k = warn_k
        self._alarm_k = alarm_k
        self._timeout = timeout
        self._vmin = valid_min
        self._vmax = valid_max

    def run(self):
        try:
            end = api.now_ns()
            start = end - int(self._days * 86400 * 1e9)
            _safe_emit(self._sig.log.emit,
                       f"Fetching {self._days} d of history for {self._name}…")
            samples = api.cpva_fetch_samples_chunked(
                self._name, start, end, self._timeout,
                log_fn=lambda m: _safe_emit(self._sig.log.emit, m),
                progress_fn=lambda d, t: _safe_emit(
                    self._sig.progress.emit, (d, t)))
            vals = []
            units = ""
            rejected = 0
            for s in samples:
                v = api.cpva_decode_value(s)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)) and np.isfinite(v):
                    fv = float(v)
                    if _out_of_range(fv, self._vmin, self._vmax):
                        rejected += 1
                        continue
                    vals.append(fv)
                    u = api.cpva_decode_units(s)
                    if u:
                        units = u
            if rejected:
                _safe_emit(self._sig.log.emit,
                           f"Dropped {rejected} out-of-range point(s) "
                           f"[{_fmt(self._vmin)}..{_fmt(self._vmax)}].")
            if len(vals) < 30:
                _safe_emit(self._sig.error.emit,
                           f"Insufficient history ({len(vals)} in-range points). "
                           "Limits left unchanged.")
                return
            result = compute_baseline(np.asarray(vals), self._warn_k, self._alarm_k)
            result["units"] = units
            result["n"] = len(vals)
            result["rejected"] = rejected
            result["days"] = self._days
            # Record exactly where the data came from, so it survives Save.
            result["window_start"] = api.ns_to_prague(start).isoformat(
                timespec="seconds")
            result["window_end"] = api.ns_to_prague(end).isoformat(
                timespec="seconds")
            _safe_emit(self._sig.done.emit, result)
        except Exception as e:  # noqa: BLE001
            _safe_emit(self._sig.error.emit, str(e))


def compute_baseline(v: np.ndarray, warn_k: float, alarm_k: float) -> dict:
    """Derive warn/alarm limits from a sample array (mean±k·std, robust fallback)."""
    mean = float(np.mean(v))
    std = float(np.std(v))
    med = float(np.median(v))
    mad = 1.4826 * float(np.median(np.abs(v - med)))
    # Prefer robust center/spread when the distribution is outlier-dominated.
    if mad > 0 and std > 3 * mad:
        center, spread, method = med, mad, "median_mad"
    else:
        center, spread, method = mean, std, "mean_std"
    if spread <= 0:
        spread = abs(center) * 0.01 or 1.0

    p01, p99 = (float(x) for x in np.percentile(v, [1, 99]))
    vmin, vmax = float(np.min(v)), float(np.max(v))

    warn_low = min(center - warn_k * spread, p01)
    warn_high = max(center + warn_k * spread, p99)
    alarm_low = min(center - alarm_k * spread, vmin)
    alarm_high = max(center + alarm_k * spread, vmax)

    return {
        "warn_low": round(warn_low, 6), "warn_high": round(warn_high, 6),
        "alarm_low": round(alarm_low, 6), "alarm_high": round(alarm_high, 6),
        "center": round(center, 6), "spread": round(spread, 6), "method": method,
    }


_STAT_KEYS = ("center", "spread", "method", "n", "rejected", "days",
              "window_start", "window_end")


def _stats_from_result(r: dict) -> dict:
    """Pull the provenance/stats subset of a Learn result for storage on a PV."""
    return {k: r[k] for k in _STAT_KEYS if k in r}


def _apply_result_to_pv(pv: "PVConfig", r: dict) -> None:
    """Write learned limits + provenance onto a PV (used by batch Learn)."""
    pv.warn_low = r["warn_low"]
    pv.warn_high = r["warn_high"]
    pv.alarm_low = r["alarm_low"]
    pv.alarm_high = r["alarm_high"]
    if r.get("units") and not pv.units:
        pv.units = r["units"]
    pv.learned_at = datetime.now(api.TZ_PRAGUE).isoformat(timespec="seconds")
    pv.learn_stats = _stats_from_result(r)


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

COLS = ["On", "Show", "Display name", "PV name", "Value", "Units", "State",
        "Alarm status", "Depends on", "Alarm ≤", "Warn ≤", "Warn ≥",
        "Alarm ≥", "Updated"]

# Column indices, kept as one place so data(), flags(), the view and the
# double-click handler stay in sync.
(COL_ON, COL_SHOW, COL_NAME, COL_PV, COL_VALUE, COL_UNITS, COL_STATE,
 COL_ALARM_STATUS, DEP_COL) = range(9)
COL_UPDATED = 13

# Threshold columns (double-click opens the limits popup).
THR_COLS = (9, 10, 11, 12)

PV_MIME = "application/x-pv-monitor-row"
GROUP_HEADER_BG = QColor("#d7e3f4")
SUBGROUP_HEADER_BG = QColor("#e9f0fa")
GROUP_HEADER_FG = QColor("#0D47A1")
UNGROUPED_LABEL = "Ungrouped"

# 'Show in graph' column: layer-panel style eye instead of a checkbox.
# Visible = open dark eye, hidden = closed eyelid (kept dark enough to stay
# legible on coloured rows), mixed (on group/subgroup rows) = mid-grey eye.
EYE_GLYPH = "\U0001F441"       # 👁 open eye
EYE_OFF_GLYPH = "◡"       # ◡ closed eyelid
EYE_ON_FG = QColor("#1c1c1c")
EYE_MIXED_FG = QColor("#8fa3bd")
EYE_OFF_FG = QColor("#4a5560")


def _fmt(x) -> str:
    return "–" if x is None else f"{x:g}"


def _fmt_range(cond) -> str:
    lo, hi = (list(cond) + [None, None])[:2] if cond else (None, None)
    if lo is None and hi is None:
        return "any"
    if lo is not None and hi is not None:
        return f"{_fmt(lo)}..{_fmt(hi)}"
    return f"≥{_fmt(lo)}" if hi is None else f"≤{_fmt(hi)}"


def _describe_profile(pv: "PVConfig", prof: dict) -> str:
    """One-line human summary of a conditional profile, e.g.
    'sys-rate: HALL=1  RATE=10 → warn 20/24 alarm 18/26'."""
    conds = prof.get("conds") or []
    parts = []
    for i, name in enumerate(pv.gate_pvs):
        cond = conds[i] if i < len(conds) else None
        parts.append(f"{api.shorten_pv_name(name)} {_fmt_range(cond)}")
    thr = pv.profile_thresholds(prof)
    when = "  ".join(parts) if parts else "always"
    label = prof.get("label") or "rule"
    return (f"{label}: {when} → "
            f"warn {_fmt(thr.warn_low)}/{_fmt(thr.warn_high)} "
            f"alarm {_fmt(thr.alarm_low)}/{_fmt(thr.alarm_high)}")


def _alarm_status_text(pv: "PVConfig", rt: Optional["PVRuntime"]) -> str:
    """One-cell summary of alert delivery for the 'Alarm status' column.

    Blank while OK/off; once a PV is in Warning/Alarm it reports when its first
    alert of the current episode went out ("sent HH:MM:SS"), that it is still
    in flight ("sending…"), that delivery failed ("⚠ not sent"), or that a
    committed alert has nothing to send yet ("pending")."""
    if rt is None or not pv.enabled:
        return ""
    if rt.alert.level == AlertLevel.OK:
        return ""
    if rt.alert.settle_until_ns:
        return "settling → " + api.ns_to_prague(
            rt.alert.settle_until_ns).strftime("%H:%M")
    if rt.alert.hold_since_ns:
        return "stabilising…"
    if rt.notify_status == "failed":
        return "⚠ not sent"
    if rt.notify_status == "sending":
        return "sending…"
    if rt.alert.first_notified_ns:
        return "sent " + api.ns_to_prague(
            rt.alert.first_notified_ns).strftime("%H:%M:%S")
    return "pending"


def _alarm_status_tooltip(pv: "PVConfig", rt: Optional["PVRuntime"]) -> str:
    if rt is None or not pv.enabled:
        return "Alerting off for this PV."
    if rt.alert.level == AlertLevel.OK:
        return "No active alert."
    lines = [f"State: {rt.alert.level.label}"]
    if rt.alert.settle_until_ns:
        lines.append("Settle window open — alert held until " + api.ns_to_prague(
            rt.alert.settle_until_ns).strftime("%H:%M:%S")
            + " to let the value stabilise.")
    if rt.alert.hold_since_ns:
        lines.append("Stability hold — the alert is held until the level has "
                     "stayed unchanged long enough (level last changed at "
                     + api.ns_to_prague(rt.alert.hold_since_ns).strftime("%H:%M:%S")
                     + ").")
    if rt.alert.first_notified_ns:
        lines.append("First alert sent: " + api.ns_to_prague(
            rt.alert.first_notified_ns).strftime("%Y-%m-%d %H:%M:%S"))
    if rt.alert.last_notified_ns:
        lines.append("Last notified: " + api.ns_to_prague(
            rt.alert.last_notified_ns).strftime("%Y-%m-%d %H:%M:%S"))
    if rt.notify_status == "failed":
        lines.append("Delivery FAILED — alert not sent:")
        lines.append("  " + (rt.notify_error or "unknown error"))
    elif rt.notify_status == "sending":
        lines.append("Delivery in progress…")
    elif rt.notify_status == "sent":
        lines.append("Delivered to all enabled channels.")
    return "\n".join(lines)


class PVTableModel(QAbstractTableModel):
    """Flat PV list rendered with per-group (and per-subgroup) header rows.

    ``self.pvs`` is the single source of truth (kept group-contiguous by the
    widget). ``self._display`` is the derived view: a list of ("header", group),
    ("subheader", (group, subgroup)) and ("pv", PVConfig) entries. Header rows
    appear only when at least one PV has a non-empty group, so an ungrouped
    setup looks exactly as before.
    """

    def __init__(self, pvs: list[PVConfig], runtime: dict[str, PVRuntime], parent=None):
        super().__init__(parent)
        self.pvs = pvs
        self.runtime = runtime
        self._display: list[tuple] = []
        # Collapsed rows: ("g", group) hides a group's contents, ("s", (group,
        # subgroup)) hides one subgroup's PVs. In-memory only (not persisted).
        self.collapsed: set = set()
        self._rebuild()

    def _rebuild(self):
        show_headers = any(p.group or p.subgroup for p in self.pvs)
        disp: list[tuple] = []
        last_g = object()   # sentinel so the first PV always opens a header
        last_s = object()
        for pv in self.pvs:
            if show_headers and pv.group != last_g:
                disp.append(("header", pv.group))
                last_g = pv.group
                last_s = object()
            g_folded = ("g", pv.group) in self.collapsed
            if show_headers and pv.subgroup != last_s:
                if pv.subgroup and not g_folded:
                    disp.append(("subheader", (pv.group, pv.subgroup)))
                last_s = pv.subgroup
            if g_folded or (pv.subgroup and
                            ("s", (pv.group, pv.subgroup)) in self.collapsed):
                continue
            disp.append(("pv", pv))
        self._display = disp

    @staticmethod
    def _collapse_key(kind: str, ref):
        return ("g", ref) if kind == "header" else ("s", ref)

    def _header_pvs(self, kind: str, ref) -> list[PVConfig]:
        """PVs covered by a header/subheader row."""
        if kind == "header":
            return [p for p in self.pvs if p.group == ref]
        g, s = ref
        return [p for p in self.pvs if p.group == g and p.subgroup == s]

    def reset(self):
        """Structural refresh: rebuild the display rows and repaint everything."""
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()

    def pv_at_row(self, row: int) -> Optional[PVConfig]:
        if 0 <= row < len(self._display):
            kind, ref = self._display[row]
            if kind == "pv":
                return ref
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._display)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled
        kind, _ = self._display[index.row()]
        if kind in ("header", "subheader"):
            return Qt.ItemIsEnabled | Qt.ItemIsDropEnabled
        base = (Qt.ItemIsEnabled | Qt.ItemIsSelectable
                | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        if index.column() == COL_ON:
            base |= Qt.ItemIsUserCheckable
        return base

    # --- drag & drop reordering ---------------------------------------
    def supportedDropActions(self):
        return Qt.MoveAction

    def supportedDragActions(self):
        return Qt.MoveAction

    def mimeTypes(self):
        return [PV_MIME]

    def mimeData(self, indexes):
        names = []
        for r in sorted({i.row() for i in indexes}):
            pv = self.pv_at_row(r)
            if pv is not None:
                names.append(pv.name)
        md = QMimeData()
        md.setData(PV_MIME, QByteArray("\n".join(names).encode("utf-8")))
        return md

    def canDropMimeData(self, data, action, row, column, parent):
        return data.hasFormat(PV_MIME)

    def dropMimeData(self, data, action, row, column, parent):
        if action != Qt.MoveAction or not data.hasFormat(PV_MIME):
            return False
        names = [n for n in bytes(data.data(PV_MIME)).decode("utf-8").split("\n") if n]
        if not names:
            return False
        if row != -1:
            target = row
        elif parent.isValid():
            target = parent.row()
        else:
            target = len(self._display)
        win = self.parent()
        if isinstance(win, MonitorWidget):
            win.drop_pvs(names, target)
        # The move is done in-place above; returning True lets the view call the
        # (unimplemented, no-op) removeRows without touching our data.
        return True

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        kind, ref = self._display[index.row()]
        col = index.column()

        if kind in ("header", "subheader"):
            if col == COL_SHOW:
                shown = [p.show_in_graph for p in self._header_pvs(kind, ref)]
                if role == Qt.DisplayRole:
                    return EYE_GLYPH if any(shown) else EYE_OFF_GLYPH
                if role == Qt.ForegroundRole:
                    if all(shown):
                        return EYE_ON_FG
                    return EYE_MIXED_FG if any(shown) else EYE_OFF_FG
                if role == Qt.TextAlignmentRole:
                    return int(Qt.AlignCenter)
                if role == Qt.ToolTipRole:
                    what = "group" if kind == "header" else "subgroup"
                    return (f"Click to show/hide every PV of this {what} "
                            "in the graph.")
            if role == Qt.DisplayRole and col == COL_NAME:
                arrow = "▸" if self._collapse_key(kind, ref) in self.collapsed \
                    else "▾"
                if kind == "header":
                    return f"{arrow} " + (ref or UNGROUPED_LABEL)
                return f"      {arrow} {ref[1]}"
            if role == Qt.ToolTipRole and col == COL_NAME:
                return "Click to collapse/expand."
            if role == Qt.BackgroundRole:
                return SUBGROUP_HEADER_BG if kind == "subheader" \
                    else GROUP_HEADER_BG
            if role == Qt.ForegroundRole:
                return GROUP_HEADER_FG
            if role == Qt.FontRole and col == COL_NAME:
                f = QFont()
                f.setBold(kind == "header")
                f.setItalic(kind == "subheader")
                return f
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignVCenter | Qt.AlignLeft)
            return None

        pv = ref
        rt = self.runtime.get(pv.name)

        if role == Qt.CheckStateRole and col == COL_ON:
            return Qt.Checked if pv.enabled else Qt.Unchecked
        if col == COL_SHOW:
            if role == Qt.DisplayRole:
                return EYE_GLYPH if pv.show_in_graph else EYE_OFF_GLYPH
            if role == Qt.ForegroundRole:
                return EYE_ON_FG if pv.show_in_graph else EYE_OFF_FG
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignCenter)

        if role == Qt.ToolTipRole:
            if col == COL_SHOW:
                return "Click to show/hide this PV in the graph."
            if col == COL_ALARM_STATUS:
                return _alarm_status_tooltip(pv, rt)
            tip = pv.name
            if pv.gate_pvs:
                tip += "\nDepends on: " + ", ".join(pv.gate_pvs)
                for prof in pv.profiles:
                    tip += "\n  • " + _describe_profile(pv, prof)
                active = rt.active_profile if rt else None
                label = (active.get("label") or "conditional") if active else "Global"
                pinned = rt is not None and rt.dep_view is not None
                tip += f"\nActive limits: {label}" + (" (pinned)" if pinned else "")
            if col in THR_COLS:
                tip += "\nDouble-click to edit limits (Global + rules)."
            if rt and rt.last_error:
                tip += f"\nLast error: {rt.last_error}"
            return tip

        level = rt.display_level() if rt else None
        bad = bool(rt and rt.bad_data and level is None and pv.enabled)

        if role == Qt.BackgroundRole and col == COL_STATE:
            if bad:
                return QColor(BADDATA_COLOR)
            if level is None:
                return QColor(NODATA_COLOR)
            return _STATE_BG[level]
        if role == Qt.ForegroundRole and col == COL_STATE:
            if level in (AlertLevel.WARNING, AlertLevel.ALARM) or level is None:
                return QColor("white")
            return QColor(SUCCESS)

        # Alarm-status cell: paint red only when a send failed, so a lost alert
        # stands out; other states use plain text.
        if col == COL_ALARM_STATUS and rt is not None:
            if role == Qt.BackgroundRole and rt.notify_status == "failed":
                return QColor(ALARM_COLOR)
            if role == Qt.ForegroundRole and rt.notify_status == "failed":
                return QColor("white")

        if role == Qt.TextAlignmentRole and col in (
                COL_VALUE, COL_UNITS, COL_STATE, COL_ALARM_STATUS) + THR_COLS:
            return int(Qt.AlignCenter)

        if role == Qt.DisplayRole:
            if col in (COL_ON, COL_SHOW):
                return None
            if col == COL_NAME:
                return pv.display_name
            if col == COL_PV:
                return pv.name
            if col == COL_VALUE:
                if not rt:
                    return "–"
                if rt.current_value is not None:
                    return _fmt(rt.current_value)
                # Bad data (all readings out of range): still show what the
                # PV last reported, rather than blanking the cell.
                if bad and rt.raw_value is not None:
                    return _fmt(rt.raw_value)
                return "–"
            if col == COL_UNITS:
                return (rt.current_units if rt and rt.current_units else pv.units) or ""
            if col == COL_STATE:
                if not pv.enabled:
                    return "off"
                if bad:
                    return "bad data"
                if level is None:
                    return "no data"
                return level.label.lower()
            if col == COL_ALARM_STATUS:
                return _alarm_status_text(pv, rt)
            if col == DEP_COL:
                if pv.gate_pvs and pv.profiles:
                    return None      # cell is covered by the rule dropdown
                return _cond_summary(pv)
            # Threshold columns show whichever set is currently in force: the
            # matched conditional profile, else the default set. (Which one is
            # active is spelled out in the row tooltip.)
            active = rt.active_profile if rt else None
            thr = pv.profile_thresholds(active) if active is not None \
                else pv.thresholds()
            if col == THR_COLS[0]:
                return _fmt(thr.alarm_low)
            if col == THR_COLS[1]:
                return _fmt(thr.warn_low)
            if col == THR_COLS[2]:
                return _fmt(thr.warn_high)
            if col == THR_COLS[3]:
                return _fmt(thr.alarm_high)
            if col == COL_UPDATED:
                if rt and rt.last_update_ns:
                    return api.ns_to_prague(rt.last_update_ns).strftime("%H:%M:%S")
                return "–"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.CheckStateRole or index.column() != COL_ON:
            return False
        pv = self.pv_at_row(index.row())
        if pv is None:
            return False
        pv.enabled = (Qt.CheckState(value) == Qt.Checked)
        self.dataChanged.emit(index, index)
        win = self.parent()
        if isinstance(win, MonitorWidget):
            win.persist()
        return True

    def toggle_show(self, row: int):
        """Flip the eye on a PV row; on a (sub)group row show everything unless
        everything is already shown, then hide it all."""
        if not (0 <= row < len(self._display)):
            return
        kind, ref = self._display[row]
        if kind in ("header", "subheader"):
            pvs = self._header_pvs(kind, ref)
            if not pvs:
                return
            target = not all(p.show_in_graph for p in pvs)
            for p in pvs:
                p.show_in_graph = target
        else:
            ref.show_in_graph = not ref.show_in_graph
        self.refresh_all()       # eyes on parent/child rows update too
        win = self.parent()
        if isinstance(win, MonitorWidget):
            win.persist()
            win.graph.redraw()

    def toggle_collapse(self, row: int):
        """Fold/unfold a group or subgroup header row."""
        if not (0 <= row < len(self._display)):
            return
        kind, ref = self._display[row]
        if kind not in ("header", "subheader"):
            return
        key = self._collapse_key(kind, ref)
        self.collapsed.symmetric_difference_update({key})
        self.reset()

    def refresh_all(self):
        if self._display:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._display) - 1, len(COLS) - 1))


# ---------------------------------------------------------------------------
# Optional double field (spinbox + enable checkbox -> None when unchecked)
# ---------------------------------------------------------------------------

class OptionalDoubleField(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.chk = QCheckBox(label)
        self.chk.setStyleSheet(_CHK_STYLE)
        self.spin = _NoWheelDoubleSpinBox()
        self.spin.setRange(-1e12, 1e12)
        self.spin.setDecimals(4)
        self.spin.setEnabled(False)
        # Keep the spinbox only as wide as it needs to be; a trailing stretch
        # left-aligns it so it doesn't balloon across the row.
        self.spin.setMaximumWidth(96)
        self.chk.toggled.connect(self.spin.setEnabled)
        lay.addWidget(self.chk)
        lay.addWidget(self.spin)
        lay.addStretch(1)

    def setToolTip(self, text: str):
        # Qt shows a child's own tooltip, not the parent's, so a tooltip on this
        # container alone would never appear over the checkbox/spinbox. Push it
        # onto both children (and keep it on self for completeness).
        super().setToolTip(text)
        self.chk.setToolTip(text)
        self.spin.setToolTip(text)

    def value(self) -> Optional[float]:
        return self.spin.value() if self.chk.isChecked() else None

    def set_value(self, v: Optional[float]):
        if v is None:
            self.chk.setChecked(False)
        else:
            self.chk.setChecked(True)
            self.spin.setValue(v)


# ---------------------------------------------------------------------------
# Compact condition parsing/formatting for the thresholds editor
# ---------------------------------------------------------------------------

def _to_float(text) -> Optional[float]:
    """Parse a cell string to float, or None if blank/invalid."""
    try:
        return float(str(text).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None


def _fmt_num(v: Optional[float]) -> str:
    """Compact number for a cell: '' for None, no trailing zeros otherwise."""
    if v is None:
        return ""
    return f"{v:g}"


def _parse_cond(text: str) -> list:
    """Parse a compact dependency condition into ``[lo, hi]`` (None = open).

    '' / 'any' / '*'      -> [None, None]      (any value)
    '5'                   -> [5, 5]            (exactly 5)
    '1-3' / '1..3' / '1:3'-> [1, 3]            (range)
    '>=5' / '≥5' / '>5'   -> [5, None]         (at least)
    '<=5' / '≤5' / '<5'   -> [None, 5]         (at most)
    """
    t = (text or "").strip().lower().replace(" ", "")
    if t in ("", "any", "*", "-", "—"):
        return [None, None]
    for pref in ("≥", ">=", ">"):
        if t.startswith(pref):
            return [_to_float(t[len(pref):]), None]
    for pref in ("≤", "<=", "<"):
        if t.startswith(pref):
            return [None, _to_float(t[len(pref):])]
    for sep in ("..", "…", "–", "—", ":"):
        if sep in t:
            a, _, b = t.partition(sep)
            return [_to_float(a), _to_float(b)]
    # bare hyphen range, but not a leading minus sign (negative number)
    if "-" in t[1:]:
        idx = t.index("-", 1)
        return [_to_float(t[:idx]), _to_float(t[idx + 1:])]
    v = _to_float(t)
    return [v, v]


def _fmt_cond(cond) -> str:
    """Inverse of :func:`_parse_cond` for display in a cell."""
    lo, hi = (list(cond) + [None, None])[:2] if cond else (None, None)
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        return _fmt_num(lo) if lo == hi else f"{_fmt_num(lo)}–{_fmt_num(hi)}"
    return f"≥{_fmt_num(lo)}" if hi is None else f"≤{_fmt_num(hi)}"


def _profile_rule_text(pv: "PVConfig", prof: dict) -> str:
    """Compact condition summary of one rule, e.g. 'HighPowerStatus=1' or
    'HALL=1 & RATE≥5'. Empty when the rule has no real conditions."""
    conds = prof.get("conds") or []
    parts = []
    for i, name in enumerate(pv.gate_pvs):
        cond = conds[i] if i < len(conds) else None
        lo, hi = (list(cond) + [None, None])[:2] if cond else (None, None)
        if lo is None and hi is None:
            continue
        short = api.shorten_pv_name(name)
        if lo is not None and hi is not None and lo == hi:
            parts.append(f"{short}={_fmt_num(lo)}")
        else:
            parts.append(f"{short}{_fmt_cond(cond)}")
    return " & ".join(parts)


def _profile_item_text(pv: "PVConfig", i: int, prof: dict) -> str:
    """Dropdown item text for one rule: its label plus the condition summary."""
    rule = _profile_rule_text(pv, prof)
    label = (prof.get("label") or "").strip()
    if label and rule:
        return f"{label}  ({rule})"
    return label or rule or f"Rule {i + 1}"


def _cond_summary(pv: "PVConfig") -> str:
    """One-line, read-only summary of a PV's dependency rules for the table.

    Each rule's active dependencies are ANDed ('&'); rules are ORed (' / ').
    e.g. 'HighPower=1' or 'Low=1 / High=1'."""
    if not pv.gate_pvs or not pv.profiles:
        return ", ".join(api.shorten_pv_name(g) for g in pv.gate_pvs)
    rules = []
    for prof in pv.profiles:
        rule = _profile_rule_text(pv, prof)
        rules.append(rule if rule else (prof.get("label") or "any"))
    return " / ".join(r for r in rules if r)


# ---------------------------------------------------------------------------
# Compact thresholds editor: default limits + conditional rules in one table
# ---------------------------------------------------------------------------

class ThresholdsEditor(QWidget):
    """One compact table editing a PV's alert limits.

    The pinned first row ('Global') holds the limits used when no rule matches.
    Each further row is a conditional rule. Within a row every dependency
    condition must hold (AND); rules are checked top to bottom and the first
    match wins, so separate rows act as OR. An empty limit cell means that side
    is not checked; an empty dependency cell means 'any value'.
    """

    HDR = ["Rule", "Dep 1", "Dep 2", "Alarm ≤", "Warn ≤", "Warn ≥", "Alarm ≥"]
    # Threshold cells sit in columns 3..6, ordered along the number line:
    #   col 3 = alarm_low, col 4 = warn_low, col 5 = warn_high, col 6 = alarm_high.
    # This tuple maps each of those columns to its (wl, wh, al, ah)-tuple index.
    _THR_COL_TO_IDX = (2, 0, 1, 3)   # alarm_low, warn_low, warn_high, alarm_high

    def __init__(self, win, pv: "PVConfig", parent=None):
        super().__init__(parent)
        self._win = win
        self._dep_names = ["", ""]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        dep_box = QGroupBox("Dependency PVs  (other PVs the rules below react to)")
        dep_box.setStyleSheet(_GROUP_STYLE)
        dv = QVBoxLayout(dep_box)
        self.dep_edits: list[QLineEdit] = []
        for i in range(2):
            row = QHBoxLayout()
            row.addWidget(QLabel(f"Dependency PV {i + 1}"))
            edit = QLineEdit()
            edit.setPlaceholderText("blank = not used")
            edit.setToolTip(
                "Another PV whose value a rule below can require to sit in a "
                "range (e.g. High/Low power status, sys-rate). Leave blank to "
                "use fewer dependencies.")
            edit.textChanged.connect(self._refresh_dep_headers)
            row.addWidget(edit, 1)
            br = QPushButton("Browse…")
            br.setStyleSheet(SECONDARY_STYLE)
            br.setToolTip("Pick this dependency PV from the CPVA channel list.")
            br.clicked.connect(lambda _=False, e=edit: self._browse_into(e))
            row.addWidget(br)
            self.dep_edits.append(edit)
            dv.addLayout(row)
        lay.addWidget(dep_box)

        self.table = QTableWidget(0, len(self.HDR))
        self.table.setHorizontalHeaderLabels(self.HDR)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setMinimumSectionSize(70)
        for c in range(1, len(self.HDR)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, 95)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(200)
        self.table.setToolTip(
            "Row 'Global' = limits used when no rule matches.\n"
            "Add rules that depend on the PVs above. Within a row all "
            "conditions must hold (AND); the first matching rule wins, so "
            "rules act as OR.\n\n"
            "Condition syntax:  blank = any · 1 = exactly 1 · 1-3 = range · "
            "≥5 = at least 5 · ≤5 = at most 5.\n"
            "Empty limit cell = that side not checked.")
        lay.addWidget(self.table)

        btns = QHBoxLayout()
        add = QPushButton("+ Add rule")
        add.setStyleSheet(SECONDARY_STYLE)
        add.setToolTip("Add a conditional rule (a new row).")
        add.clicked.connect(lambda: self._add_rule())
        rm = QPushButton("Remove rule")
        rm.setStyleSheet(SECONDARY_STYLE)
        rm.setToolTip("Delete the selected rule row(s). The Global row stays.")
        rm.clicked.connect(self._remove_selected)
        btns.addWidget(add)
        btns.addWidget(rm)
        btns.addStretch(1)
        lay.addLayout(btns)

        hint = QLabel(
            "AND within a row · OR across rows (first match wins). Leave a "
            "dependency blank in a rule to mean 'any value'.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777; font-size:11px;")
        lay.addWidget(hint)

        if pv is not None:
            self.load(pv)

    # --- dependency headers / editability ------------------------------
    def _refresh_dep_headers(self):
        self._dep_names = [e.text().strip() for e in self.dep_edits]
        for i in range(2):
            name = self._dep_names[i]
            hdr = api.shorten_pv_name(name) if name else f"Dep {i + 1} (unused)"
            self.table.horizontalHeaderItem(1 + i).setText(hdr)
        self._sync_dep_editable()

    def _sync_dep_editable(self):
        """Grey out dependency cells with no PV set, and the Default row's."""
        for r in range(self.table.rowCount()):
            is_default = (r == 0)
            for i in range(2):
                item = self.table.item(r, 1 + i)
                if item is None:
                    continue
                usable = bool(self._dep_names[i]) and not is_default
                if usable:
                    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled
                                  | Qt.ItemIsEditable)
                else:
                    item.setText("—" if is_default else "")
                    item.setFlags(Qt.ItemIsSelectable)

    def _browse_into(self, edit: QLineEdit):
        dlg = PVBrowserDialog(self._win, self._win._all_channels,
                              float(self._win.settings["http_timeout_s"]))
        if not self._win._all_channels and dlg._all:
            self._win._all_channels = dlg._all
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            edit.setText(dlg.selected[0])

    # --- rows ----------------------------------------------------------
    def _new_row(self, label: str, label_locked: bool, conds: list, thr: list):
        r = self.table.rowCount()
        self.table.insertRow(r)
        li = QTableWidgetItem(label)
        if label_locked:
            li.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            f = li.font()
            f.setBold(True)
            li.setFont(f)
        self.table.setItem(r, 0, li)
        for i in range(2):
            cond = conds[i] if i < len(conds) else None
            self.table.setItem(r, 1 + i, QTableWidgetItem(_fmt_cond(cond)))
        # thr is (warn_low, warn_high, alarm_low, alarm_high); place each into
        # its number-line column (Alarm ≤, Warn ≤, Warn ≥, Alarm ≥).
        for k, idx in enumerate(self._THR_COL_TO_IDX):
            self.table.setItem(r, 3 + k, QTableWidgetItem(_fmt_num(thr[idx])))
        return r

    def _add_rule(self, prof: Optional[dict] = None):
        conds = (prof.get("conds") or []) if prof else []
        thr = ([prof.get("warn_low"), prof.get("warn_high"),
                prof.get("alarm_low"), prof.get("alarm_high")]
               if prof else [None, None, None, None])
        self._new_row(prof.get("label", "") if prof else "", False, conds, thr)
        self._sync_dep_editable()

    def _remove_selected(self):
        # Row 0 is the pinned Default set and is never removed.
        rows = sorted({i.row() for i in self.table.selectedIndexes()
                       if i.row() > 0}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    # --- load / apply --------------------------------------------------
    def load(self, pv: "PVConfig"):
        for i in range(2):
            self.dep_edits[i].setText(
                pv.gate_pvs[i] if i < len(pv.gate_pvs) else "")
        self.table.setRowCount(0)
        self._new_row("Global", True, [None, None],
                      [pv.warn_low, pv.warn_high, pv.alarm_low, pv.alarm_high])
        for prof in pv.profiles:
            self._add_rule(prof)
        self._refresh_dep_headers()

    def set_default_thresholds(self, wl, wh, al, ah):
        """Fill the Global row (used by Learn)."""
        thr = (wl, wh, al, ah)
        for k, idx in enumerate(self._THR_COL_TO_IDX):
            self.table.setItem(0, 3 + k, QTableWidgetItem(_fmt_num(thr[idx])))

    def _cell(self, r: int, c: int) -> str:
        item = self.table.item(r, c)
        return item.text().strip() if item else ""

    def apply_to(self, pv: "PVConfig"):
        """Write edited default limits, dependency PVs and rules back into pv."""
        # If a cell is still in edit mode (Save clicked without first leaving
        # the cell), commit that pending edit before reading — otherwise
        # item.text() returns the OLD value and the typed one is silently
        # dropped. This is the likely reason a hand-set '1' condition reverted
        # to its previous [0, 1].
        if self.table.state() == QAbstractItemView.EditingState:
            self.table.setFocus()
        # Columns 3..6 are Alarm ≤, Warn ≤, Warn ≥, Alarm ≥ (number-line order).
        pv.alarm_low = _to_float(self._cell(0, 3))
        pv.warn_low = _to_float(self._cell(0, 4))
        pv.warn_high = _to_float(self._cell(0, 5))
        pv.alarm_high = _to_float(self._cell(0, 6))
        kept = [(name, slot) for slot, name in
                enumerate(e.text().strip() for e in self.dep_edits) if name]
        pv.gate_pvs = [name for name, _ in kept]
        profiles = []
        for r in range(1, self.table.rowCount()):
            label = self._cell(r, 0)
            conds_full = [_parse_cond(self._cell(r, 1)),
                          _parse_cond(self._cell(r, 2))]
            # Columns 3..6 are Alarm ≤, Warn ≤, Warn ≥, Alarm ≥.
            alarm_low = _to_float(self._cell(r, 3))
            warn_low = _to_float(self._cell(r, 4))
            warn_high = _to_float(self._cell(r, 5))
            alarm_high = _to_float(self._cell(r, 6))
            thr = (warn_low, warn_high, alarm_low, alarm_high)
            cond_set = any(conds_full[slot] != [None, None] for _, slot in kept)
            if not (label or cond_set or any(v is not None for v in thr)):
                continue
            profiles.append({
                "label": label,
                "conds": [conds_full[slot] for _, slot in kept],
                "warn_low": warn_low, "warn_high": warn_high,
                "alarm_low": alarm_low, "alarm_high": alarm_high,
            })
        pv.profiles = profiles


class ThresholdsPopup(QDialog):
    """Small dialog wrapping :class:`ThresholdsEditor` for one PV, opened from
    the main table's threshold cells so the user picks default vs a conditional
    rule and edits the numbers in place."""

    def __init__(self, win, pv: "PVConfig"):
        super().__init__(win)
        self.setWindowTitle(f"Limits — {pv.display_name}")
        self.resize(900, 600)
        self.setMinimumSize(760, 500)
        self.pv = pv
        lay = QVBoxLayout(self)
        info = QLabel(pv.name)
        info.setStyleSheet("color:#555;")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.editor = ThresholdsEditor(win, pv)
        lay.addWidget(self.editor, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        self.editor.apply_to(self.pv)
        self.accept()


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class PVBrowserDialog(QDialog):
    """Type-to-filter browser over all CPVA channels."""

    def __init__(self, parent, channels: list[str], timeout: float):
        super().__init__(parent)
        self.setWindowTitle("Add PV — browse channels")
        self.resize(560, 560)
        self.selected: list[str] = []
        self._all = channels or []
        self._timeout = timeout

        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter (space = AND, * = wildcard)…")
        self.search.setToolTip(
            "Type to filter the full CPVA channel list below. Space-separated "
            "words must all match (AND); * matches any run of characters. "
            "Example: 'temp * L3' shows channels containing both 'temp' and 'L3'.")
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setToolTip(
            "All archiver channels matching the filter (capped at 2000 shown). "
            "Select one or more (Ctrl/Shift-click) and click 'Add selected', or "
            "double-click a single channel to add it and close.")
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        lay.addWidget(self.list, 1)

        self.count = QLabel("")
        lay.addWidget(self.count)

        bb = QDialogButtonBox()
        add = bb.addButton("Add selected", QDialogButtonBox.AcceptRole)
        add.setStyleSheet(_BTN_PRIMARY)
        add.setToolTip("Add every highlighted channel to the monitor list and "
                       "close this browser.")
        bb.addButton("Close", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        if self._all:
            self._apply_filter()
        else:
            self.count.setText("Loading channels…")
            self._load_channels()

    def _load_channels(self):
        sig = _ChannelsSignals(self)
        sig.done.connect(self._on_loaded)
        sig.error.connect(lambda e: self.count.setText(f"Channel load failed: {e}"))
        self._sig = sig
        QThreadPool.globalInstance().start(_ChannelsWorker(sig, self._timeout))

    def _on_loaded(self, channels):
        self._all = channels
        self._apply_filter()

    def _apply_filter(self):
        pat = self.search.text()
        matches = [c for c in self._all if api._matches_wildcard(c, pat)]
        capped = matches[:2000]
        self.list.clear()
        for c in capped:
            self.list.addItem(QListWidgetItem(c))
        extra = "" if len(matches) <= 2000 else " (showing first 2000)"
        self.count.setText(f"{len(matches)} match / {len(self._all)} total{extra}")

    def _accept(self):
        self.selected = [i.text() for i in self.list.selectedItems()]
        if self.selected:
            self.accept()


class PVEditDialog(QDialog):
    def __init__(self, parent: "MonitorWidget", pv: PVConfig):
        super().__init__(parent)
        self.setWindowTitle(f"Edit PV — {pv.display_name}")
        self.resize(840, 720)
        self.setMinimumSize(720, 600)
        self._win = parent
        self.pv = pv

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        info = QLabel(pv.name)
        info.setStyleSheet("color:#555;")
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        self.name_edit = QLineEdit(pv.display_name)
        self.name_edit.setToolTip(
            "Friendly label shown in the monitor table, graph and alert "
            "messages instead of the raw PV name. Does not change the PV itself.")
        form.addRow("Display name", self.name_edit)
        self.units_combo = _NoWheelComboBox()
        self.units_combo.setEditable(True)
        self.units_combo.lineEdit().setPlaceholderText(
            "e.g. DegC — overrides the archiver's units")
        self.units_combo.setToolTip(
            "Unit shown next to values and on plot axes (e.g. DegC, %, mbar). "
            "Pick a unit already used by another PV, or type a new one. "
            "Overrides whatever the archiver reports. Leave blank to use the "
            "archiver's own units.")
        self.units_combo.addItem("")
        for u in sorted({p.units for p in parent.pvs if p.units}):
            self.units_combo.addItem(u)
        self.units_combo.setCurrentText(pv.units)
        form.addRow("Units", self.units_combo)
        self.group_combo = _NoWheelComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.setToolTip(
            "PVs sharing a group name are shown together under a group header. "
            "Leave blank for ungrouped.")
        self.group_combo.addItem("")
        for g in sorted({p.group for p in parent.pvs if p.group}):
            self.group_combo.addItem(g)
        self.group_combo.setCurrentText(pv.group)
        form.addRow("Group", self.group_combo)
        self.subgroup_combo = _NoWheelComboBox()
        self.subgroup_combo.setEditable(True)
        self.subgroup_combo.setToolTip(
            "Optional second grouping level shown as an indented sub-header "
            "inside the group (e.g. 'Chillers' under 'Temperatures'). "
            "Leave blank for none.")
        self.subgroup_combo.addItem("")
        for s in sorted({p.subgroup for p in parent.pvs if p.subgroup}):
            self.subgroup_combo.addItem(s)
        self.subgroup_combo.setCurrentText(pv.subgroup)
        form.addRow("Subgroup", self.subgroup_combo)
        self.enabled_chk = QCheckBox("Enable alerting for this PV")
        self.enabled_chk.setStyleSheet(_CHK_STYLE)
        self.enabled_chk.setToolTip(
            "When on, this PV is evaluated against its thresholds and can raise "
            "Warning/Alarm alerts. When off, it is still polled and plotted but "
            "never triggers a notification.")
        self.enabled_chk.setChecked(pv.enabled)
        form.addRow("", self.enabled_chk)
        lay.addLayout(form)

        thr_box = QGroupBox("Alert limits  (Global row + conditional rules)")
        thr_box.setStyleSheet(_GROUP_STYLE)
        tbl = QVBoxLayout(thr_box)
        self.thr_editor = ThresholdsEditor(parent, pv)
        tbl.addWidget(self.thr_editor)
        lay.addWidget(thr_box)

        vrg = QGroupBox("Valid range  (sensor-error filter)")
        vrg.setStyleSheet(_GROUP_STYLE)
        vrl = QVBoxLayout(vrg)
        self.f_valid_min = OptionalDoubleField("Drop readings below <")
        self.f_valid_min.setToolTip(
            "Sensor-error guard: readings below this value are treated as bad and "
            "discarded everywhere (plot, alarms, Learn). Overrides the global "
            "default for this PV. Unticked = use the global default.")
        self.f_valid_max = OptionalDoubleField("Drop readings above >")
        self.f_valid_max.setToolTip(
            "Sensor-error guard: readings above this value are treated as bad and "
            "discarded everywhere (plot, alarms, Learn). Overrides the global "
            "default for this PV. Unticked = use the global default.")
        vrl.addWidget(self.f_valid_min)
        vrl.addWidget(self.f_valid_max)
        self.f_valid_min.set_value(pv.valid_min)
        self.f_valid_max.set_value(pv.valid_max)
        gmin = parent.settings.get("valid_min_default")
        gmax = parent.settings.get("valid_max_default")
        hint = QLabel(f"Unchecked = use global default "
                      f"[{_fmt(gmin)} .. {_fmt(gmax)}]. "
                      "Out-of-range readings are dropped everywhere "
                      "(plot, alarms, Learn).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#777; font-size:11px;")
        vrl.addWidget(hint)
        lay.addWidget(vrg)

        learn = QGroupBox("Learn from history")
        learn.setStyleSheet(_GROUP_STYLE)
        ll = QHBoxLayout(learn)
        ll.addWidget(QLabel("Days"))
        self.days = _NoWheelSpinBox()
        self.days.setRange(1, 90)
        self.days.setToolTip(
            "How many days of archiver history to sample when learning "
            "thresholds. More days = a more stable baseline but slower to fetch. "
            "Range 1–90.")
        self.days.setValue(int(parent.settings["learn_days_default"]))
        ll.addWidget(self.days)
        ll.addWidget(QLabel("warn k"))
        self.warn_k = _NoWheelDoubleSpinBox()
        self.warn_k.setRange(0.5, 20)
        self.warn_k.setSingleStep(0.5)
        self.warn_k.setToolTip(
            "Warning band width, in spreads (≈ standard deviations) around the "
            "learned centre. Warning thresholds are set at centre ± k×spread. "
            "Smaller k = tighter, more sensitive warnings.")
        self.warn_k.setValue(float(parent.settings["warn_k_default"]))
        ll.addWidget(self.warn_k)
        ll.addWidget(QLabel("alarm k"))
        self.alarm_k = _NoWheelDoubleSpinBox()
        self.alarm_k.setRange(0.5, 30)
        self.alarm_k.setSingleStep(0.5)
        self.alarm_k.setToolTip(
            "Alarm band width, in spreads around the learned centre "
            "(centre ± k×spread). Set larger than 'warn k' so alarms sit outside "
            "the warning band.")
        self.alarm_k.setValue(float(parent.settings["alarm_k_default"]))
        ll.addWidget(self.alarm_k)
        self.learn_btn = QPushButton("Learn")
        self.learn_btn.setStyleSheet(_BTN_PRIMARY)
        self.learn_btn.setToolTip(
            "Fetch this PV's history for the chosen number of days and compute "
            "Warning/Alarm thresholds from its centre and spread. Results fill "
            "the fields above for review — nothing is saved until you click Save.")
        self.learn_btn.clicked.connect(self._learn)
        ll.addWidget(self.learn_btn)
        lay.addWidget(learn)

        self.learn_prog = QProgressBar()
        self.learn_prog.setVisible(False)
        self.learn_prog.setFormat("%v / %m chunks")
        lay.addWidget(self.learn_prog)

        self.learn_status = QLabel("")
        self.learn_status.setWordWrap(True)
        self.learn_status.setStyleSheet("color:#555;")
        lay.addWidget(self.learn_status)

        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _learn(self):
        self.learn_btn.setEnabled(False)
        self.learn_status.setText("Learning…")
        self.learn_prog.setRange(0, 0)   # busy until first chunk completes
        self.learn_prog.setVisible(True)
        sig = _LearnSignals(self)
        sig.done.connect(self._on_learned)
        sig.error.connect(self._on_learn_error)
        sig.log.connect(self.learn_status.setText)
        sig.progress.connect(self._on_progress)
        self._learn_sig = sig
        # Learn honours the same valid range that would be saved for this PV,
        # using the checkbox values live (falling back to the global default).
        vmin = self.f_valid_min.value()
        if vmin is None:
            vmin = self._win.settings.get("valid_min_default")
        vmax = self.f_valid_max.value()
        if vmax is None:
            vmax = self._win.settings.get("valid_max_default")
        worker = _LearnWorker(
            sig, self.pv.name, self.days.value(),
            self.warn_k.value(), self.alarm_k.value(),
            float(self._win.settings["http_timeout_s"]), vmin, vmax)
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, dt):
        done, total = dt
        if total:
            self.learn_prog.setRange(0, total)
            self.learn_prog.setValue(done)

    def _on_learned(self, r: dict):
        self.learn_btn.setEnabled(True)
        self.learn_prog.setVisible(False)
        self.thr_editor.set_default_thresholds(
            r["warn_low"], r["warn_high"], r["alarm_low"], r["alarm_high"])
        if r.get("units") and not self.units_combo.currentText().strip():
            self.units_combo.setCurrentText(r["units"])
        self._pending_stats = _stats_from_result(r)
        dropped = f", {r['rejected']} dropped" if r.get("rejected") else ""
        window = ""
        if r.get("window_start") and r.get("window_end"):
            window = f"\nData: {r['window_start']} → {r['window_end']}"
        self.learn_status.setText(
            f"Learned from {r['n']} in-range pts / {r['days']} d{dropped} "
            f"({r['method']}, center={r['center']:g}, spread={r['spread']:g}). "
            f"Review and Save.{window}")

    def _on_learn_error(self, msg: str):
        self.learn_btn.setEnabled(True)
        self.learn_prog.setVisible(False)
        self.learn_status.setText(f"⚠ {msg}")

    def _save(self):
        pv = self.pv
        pv.display_name = self.name_edit.text().strip() or pv.display_name
        pv.units = self.units_combo.currentText().strip()
        pv.group = self.group_combo.currentText().strip()
        pv.subgroup = self.subgroup_combo.currentText().strip()
        pv.enabled = self.enabled_chk.isChecked()
        self.thr_editor.apply_to(pv)   # default limits + gate_pvs + profiles
        pv.valid_min = self.f_valid_min.value()
        pv.valid_max = self.f_valid_max.value()
        if hasattr(self, "_pending_stats"):
            pv.learned_at = datetime.now(api.TZ_PRAGUE).isoformat(timespec="seconds")
            pv.learn_stats = self._pending_stats
        self.accept()


class GroupsDialog(QDialog):
    """Manage the group / subgroup structure in one place.

    Tree: groups → subgroups → PVs. Double-click a group/subgroup to rename
    it; drag PVs (or whole subgroups) where they belong; buttons add new
    groups/subgroups. On Save the tree order becomes the table order and each
    PV adopts the group/subgroup it sits under.
    """

    _UNGROUPED = "\x00ungrouped"   # marker in item data for the '(Ungrouped)' node

    def __init__(self, parent: "MonitorWidget"):
        super().__init__(parent)
        self.setWindowTitle("Groups")
        self.resize(480, 560)
        lay = QVBoxLayout(self)
        hint = QLabel("Drag PVs (or whole subgroups) to move them between "
                      "groups. Double-click a group or subgroup to rename it. "
                      "Deleting a group/subgroup keeps its PVs (they move up "
                      "one level); empty groups disappear on Save. The "
                      "(Ungrouped) node is automatic — it can't be renamed "
                      "or deleted.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")
        lay.addWidget(hint)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        root = self.tree.invisibleRootItem()
        root.setFlags(root.flags() & ~Qt.ItemIsDropEnabled)
        lay.addWidget(self.tree, 1)

        # Build: groups in first-appearance order; "" group last as (Ungrouped).
        groups: list[str] = []
        for pv in parent.pvs:
            if pv.group not in groups:
                groups.append(pv.group)
        if "" in groups:                      # keep ungrouped at the bottom
            groups.remove("")
            groups.append("")
        for g in groups:
            g_item = self._make_group_item(g)
            subs_seen: dict[str, QTreeWidgetItem] = {}
            for pv in parent.pvs:
                if pv.group != g:
                    continue
                host = g_item
                if pv.subgroup:
                    if pv.subgroup not in subs_seen:
                        subs_seen[pv.subgroup] = self._make_subgroup_item(
                            g_item, pv.subgroup)
                    host = subs_seen[pv.subgroup]
                self._make_pv_item(host, pv)
        self.tree.expandAll()

        btns = QHBoxLayout()
        b_group = QPushButton("New group")
        b_group.setStyleSheet(SECONDARY_STYLE)
        b_group.clicked.connect(self._new_group)
        b_sub = QPushButton("New subgroup")
        b_sub.setStyleSheet(SECONDARY_STYLE)
        b_sub.setToolTip("Adds a subgroup under the selected group.")
        b_sub.clicked.connect(self._new_subgroup)
        b_ren = QPushButton("Rename")
        b_ren.setStyleSheet(SECONDARY_STYLE)
        b_ren.setToolTip("Rename the selected group/subgroup "
                         "(same as double-clicking it).")
        b_ren.clicked.connect(self._rename)
        b_del = QPushButton("Delete")
        b_del.setStyleSheet(SECONDARY_STYLE)
        b_del.setToolTip("Dissolve the selected group/subgroup — its PVs move "
                         "up one level (group → Ungrouped, subgroup → its "
                         "group).")
        b_del.clicked.connect(self._delete)
        btns.addWidget(b_group)
        btns.addWidget(b_sub)
        btns.addWidget(b_ren)
        btns.addWidget(b_del)
        btns.addStretch(1)
        lay.addLayout(btns)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # --- item factories --------------------------------------------------
    def _make_group_item(self, group: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem(self.tree)
        f = QFont()
        f.setBold(True)
        it.setFont(0, f)
        if group:
            it.setText(0, group)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                        | Qt.ItemIsDropEnabled | Qt.ItemIsEditable)
        else:
            it.setText(0, f"({UNGROUPED_LABEL})")
            it.setData(0, Qt.UserRole, self._UNGROUPED)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsDropEnabled)
        return it

    def _make_subgroup_item(self, parent_item, name: str) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent_item)
        it.setText(0, name)
        f = QFont()
        f.setItalic(True)
        it.setFont(0, f)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
                    | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        return it

    def _make_pv_item(self, parent_item, pv: PVConfig) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent_item)
        it.setText(0, f"{pv.display_name}   ({pv.name})")
        it.setData(0, Qt.UserRole, pv.name)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        return it

    # --- buttons ----------------------------------------------------------
    def _new_group(self):
        it = self._make_group_item("New group")
        self.tree.editItem(it, 0)

    def _new_subgroup(self):
        sel = self.tree.currentItem()
        # Walk up to the top-level (group) ancestor of the selection.
        while sel is not None and sel.parent() is not None:
            sel = sel.parent()
        if sel is None or sel.data(0, Qt.UserRole) == self._UNGROUPED:
            QMessageBox.information(
                self, "New subgroup",
                "Select a group first (subgroups live inside a group).")
            return
        it = self._make_subgroup_item(sel, "New subgroup")
        sel.setExpanded(True)
        self.tree.editItem(it, 0)

    def _rename(self):
        it = self.tree.currentItem()
        if it is None or not (it.flags() & Qt.ItemIsEditable):
            QMessageBox.information(
                self, "Rename",
                "Select a group or subgroup to rename. PVs are renamed via "
                "Edit in the main window; (Ungrouped) is automatic.")
            return
        self.tree.editItem(it, 0)

    def _delete(self):
        it = self.tree.currentItem()
        if it is None or it.data(0, Qt.UserRole) is not None:
            # a PV row or the (Ungrouped) node
            QMessageBox.information(
                self, "Delete",
                "Select a group or subgroup to delete. Its PVs are kept — "
                "they move up one level. (PVs themselves are removed via "
                "Remove in the main window.)")
            return
        children = it.takeChildren()
        parent = it.parent()
        if parent is None:                    # group → contents go to (Ungrouped)
            self._ungrouped_item().addChildren(children)
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
        else:                                 # subgroup → contents go to group
            parent.addChildren(children)
            parent.removeChild(it)

    def _ungrouped_item(self) -> QTreeWidgetItem:
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            if root.child(i).data(0, Qt.UserRole) == self._UNGROUPED:
                return root.child(i)
        it = self._make_group_item("")        # (re)create it at the bottom
        it.setExpanded(True)
        return it

    # --- result -----------------------------------------------------------
    def result_assignments(self) -> list[tuple]:
        """[(pv_name, group, subgroup)] in tree order after the user's edits."""
        out: list[tuple] = []

        def walk(item, group: str, subgroup: str):
            name = item.data(0, Qt.UserRole)
            if name is not None and name != self._UNGROUPED:
                out.append((name, group, subgroup))
                return
            for i in range(item.childCount()):
                child = item.child(i)
                if child.data(0, Qt.UserRole) is None:   # subgroup node
                    walk(child, group, child.text(0).strip())
                else:
                    walk(child, group, subgroup)

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            top = root.child(i)
            group = "" if top.data(0, Qt.UserRole) == self._UNGROUPED \
                else top.text(0).strip()
            walk(top, group, "")
        return out


class _LearnParamsDialog(QDialog):
    """Ask once for Days / warn k / alarm k before a batch Learn."""

    def __init__(self, parent: "MonitorWidget", n_pvs: int):
        super().__init__(parent)
        self.setWindowTitle("Learn selected")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Learn thresholds for {n_pvs} selected PV(s) "
                             "from archiver history."))
        form = QFormLayout()
        self.days = _NoWheelSpinBox()
        self.days.setRange(1, 90)
        self.days.setToolTip(
            "Days of archiver history to sample for every selected PV. "
            "More days = steadier baseline but slower. Range 1–90.")
        self.days.setValue(int(parent.settings["learn_days_default"]))
        form.addRow("Days", self.days)
        self.warn_k = _NoWheelDoubleSpinBox()
        self.warn_k.setRange(0.5, 20)
        self.warn_k.setSingleStep(0.5)
        self.warn_k.setToolTip(
            "Warning band width in spreads (≈ std-devs): thresholds are set at "
            "centre ± k×spread. Smaller k = more sensitive warnings.")
        self.warn_k.setValue(float(parent.settings["warn_k_default"]))
        form.addRow("warn k", self.warn_k)
        self.alarm_k = _NoWheelDoubleSpinBox()
        self.alarm_k.setRange(0.5, 30)
        self.alarm_k.setSingleStep(0.5)
        self.alarm_k.setToolTip(
            "Alarm band width in spreads: centre ± k×spread. Set larger than "
            "'warn k' so alarms sit outside the warning band.")
        self.alarm_k.setValue(float(parent.settings["alarm_k_default"]))
        form.addRow("alarm k", self.alarm_k)
        lay.addLayout(form)
        lay.addWidget(QLabel("Results are written straight to each PV and saved."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self):
        return self.days.value(), self.warn_k.value(), self.alarm_k.value()


class _BatchLearnController(QObject):
    """Runs Learn over several PVs one at a time behind a progress dialog."""

    def __init__(self, win: "MonitorWidget", pvs: list, days: int,
                 warn_k: float, alarm_k: float):
        super().__init__(win)
        self._win = win
        self._pvs = list(pvs)
        self._days = days
        self._warn_k = warn_k
        self._alarm_k = alarm_k
        self._i = 0
        self._ok = 0
        self._fail: list[tuple[str, str]] = []
        self._cancelled = False
        self._sig = None
        self._dlg = QProgressDialog("Learning…", "Cancel", 0, len(self._pvs), win)
        self._dlg.setWindowTitle("Learn selected")
        self._dlg.setWindowModality(Qt.WindowModal)
        self._dlg.setMinimumDuration(0)
        self._dlg.setAutoClose(False)
        self._dlg.setAutoReset(False)
        self._dlg.canceled.connect(self._cancel)

    def start(self):
        if not self._pvs:
            return
        self._dlg.show()
        self._next()

    def _cancel(self):
        self._cancelled = True

    def _next(self):
        if self._cancelled or self._i >= len(self._pvs):
            self._finish()
            return
        pv = self._pvs[self._i]
        self._dlg.setValue(self._i)
        self._dlg.setLabelText(
            f"PV {self._i + 1}/{len(self._pvs)}: {pv.display_name}")
        vmin, vmax = self._win._valid_range(pv)
        sig = _LearnSignals(self)
        sig.done.connect(self._on_done)
        sig.error.connect(self._on_error)
        sig.progress.connect(self._on_progress)
        self._sig = sig
        worker = _LearnWorker(
            sig, pv.name, self._days, self._warn_k, self._alarm_k,
            float(self._win.settings["http_timeout_s"]), vmin, vmax)
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, dt):
        done, total = dt
        if total:
            pv = self._pvs[self._i]
            self._dlg.setLabelText(
                f"PV {self._i + 1}/{len(self._pvs)}: {pv.display_name} "
                f"— chunk {done}/{total}")

    def _on_done(self, r: dict):
        pv = self._pvs[self._i]
        _apply_result_to_pv(pv, r)
        self._ok += 1
        self._win._log(
            f"Learned {pv.display_name}: {r['n']} pts / {r['days']} d, "
            f"warn {_fmt(r['warn_low'])}/{_fmt(r['warn_high'])}, "
            f"alarm {_fmt(r['alarm_low'])}/{_fmt(r['alarm_high'])}.")
        self._i += 1
        self._next()

    def _on_error(self, msg: str):
        pv = self._pvs[self._i]
        self._fail.append((pv.display_name, msg))
        self._win._log(f"Learn failed for {pv.display_name}: {msg}")
        self._i += 1
        self._next()

    def _finish(self):
        self._dlg.close()
        if self._ok:
            self._win.model.refresh_all()
            self._win.graph.refresh_combo()
            self._win.persist()
        summary = f"Learned {self._ok} PV(s)."
        if self._fail:
            summary += (f" Failed {len(self._fail)}: "
                        + ", ".join(n for n, _ in self._fail))
        if self._cancelled:
            summary = "Cancelled. " + summary
        self._win._log(summary)
        QMessageBox.information(self._win, "Learn selected", summary)
        self._win._batch = None   # release


class EmailContactsWidget(QWidget):
    """Editable On/Email/Description table — one row per email recipient.

    Only the address is required; Description is a free-text note (e.g. the
    recipient's real name) so the list stays readable.
    """

    COLS = ["On", "Email address", "Description"]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 32)  # just wide enough for the checkbox
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(110)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setStyleSheet(SECONDARY_STYLE)
        add.setToolTip("Add a new, empty recipient row and start typing the "
                       "e-mail address.")
        add.clicked.connect(lambda: self._add_row(start_edit=True))
        rm = QPushButton("Remove selected")
        rm.setStyleSheet(SECONDARY_STYLE)
        rm.setToolTip("Delete the currently selected recipient row(s) from the "
                      "list.")
        rm.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)

    def _add_row(self, address: str = "", description: str = "",
                enabled: bool = True, start_edit: bool = False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QCheckBox()
        chk.setChecked(enabled)
        cell = QWidget()
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setAlignment(Qt.AlignCenter)
        cl.addWidget(chk)
        self.table.setCellWidget(r, 0, cell)
        addr_item = QTableWidgetItem(address)
        self.table.setItem(r, 1, addr_item)
        self.table.setItem(r, 2, QTableWidgetItem(description))
        if start_edit:
            # A freshly-added row is otherwise two blank cells with no visual
            # cue that they're editable — jump straight into typing the address.
            self.table.setCurrentItem(addr_item)
            self.table.editItem(addr_item)

    def _remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def set_rows(self, contacts: list[dict]):
        self.table.setRowCount(0)
        for c in contacts:
            self._add_row(c.get("address", ""), c.get("description", ""),
                          bool(c.get("enabled", True)))

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            cell = self.table.cellWidget(r, 0)
            chk = cell.findChild(QCheckBox) if cell else None
            addr_item = self.table.item(r, 1)
            address = addr_item.text().strip() if addr_item else ""
            if not address:
                continue
            desc_item = self.table.item(r, 2)
            description = desc_item.text().strip() if desc_item else ""
            out.append({"address": address, "description": description,
                       "enabled": chk.isChecked() if chk else True})
        return out

    def enabled_addresses(self) -> list[str]:
        return [c["address"] for c in self.rows() if c["enabled"]]


class WebexRoomsWidget(QWidget):
    """Editable On/Name/Room ID/Listen table — one bot broadcasting to N rooms.

    Only one row may have "Listen" checked (two-way commands are read from a
    single designated room); checking one unchecks the others.
    """

    COLS = ["On", "Name", "Room ID", "Listen for commands"]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 32)   # just wide enough for the checkbox
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.setColumnWidth(1, 149)  # ~75% of the old stretched width
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 99)   # ~50% of the old stretched width
        hh.setSectionResizeMode(3, QHeaderView.Stretch)  # takes the freed-up space, so the full label fits
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(110)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setStyleSheet(SECONDARY_STYLE)
        add.setToolTip("Add a new, empty room row and start typing its name.")
        add.clicked.connect(lambda: self._add_row(start_edit=True))
        rm = QPushButton("Remove selected")
        rm.setStyleSheet(SECONDARY_STYLE)
        rm.setToolTip("Delete the currently selected room row(s) from the list.")
        rm.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)

    def _add_row(self, name: str = "", room_id: str = "", enabled: bool = True,
                listen: bool = False, start_edit: bool = False):
        r = self.table.rowCount()
        self.table.insertRow(r)

        on_chk = QCheckBox()
        on_chk.setChecked(enabled)
        on_cell = QWidget()
        ol = QHBoxLayout(on_cell)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setAlignment(Qt.AlignCenter)
        ol.addWidget(on_chk)
        self.table.setCellWidget(r, 0, on_cell)

        name_item = QTableWidgetItem(name)
        self.table.setItem(r, 1, name_item)
        self.table.setItem(r, 2, QTableWidgetItem(room_id))
        if start_edit:
            # A freshly-added row is otherwise blank cells with no visible
            # cue that they're editable — jump straight into typing the name.
            self.table.setCurrentItem(name_item)
            self.table.editItem(name_item)

        listen_chk = QCheckBox()
        listen_chk.setChecked(listen)
        listen_chk.toggled.connect(
            lambda checked, cb=listen_chk: self._on_listen_toggled(cb, checked))
        listen_cell = QWidget()
        ll = QHBoxLayout(listen_cell)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setAlignment(Qt.AlignCenter)
        ll.addWidget(listen_chk)
        self.table.setCellWidget(r, 3, listen_cell)

    def _on_listen_toggled(self, cb: QCheckBox, checked: bool):
        if not checked:
            return
        for r in range(self.table.rowCount()):
            cell = self.table.cellWidget(r, 3)
            other = cell.findChild(QCheckBox) if cell else None
            if other is not None and other is not cb and other.isChecked():
                other.setChecked(False)

    def _remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def set_rows(self, rooms: list[dict]):
        self.table.setRowCount(0)
        for r in rooms:
            self._add_row(r.get("name", ""), r.get("room_id", ""),
                          bool(r.get("enabled", True)), bool(r.get("listen", False)))

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            on_cell = self.table.cellWidget(r, 0)
            on_chk = on_cell.findChild(QCheckBox) if on_cell else None
            id_item = self.table.item(r, 2)
            room_id = id_item.text().strip() if id_item else ""
            if not room_id:
                continue
            name_item = self.table.item(r, 1)
            name = (name_item.text().strip() if name_item else "") or room_id
            listen_cell = self.table.cellWidget(r, 3)
            listen_chk = listen_cell.findChild(QCheckBox) if listen_cell else None
            out.append({
                "name": name, "room_id": room_id,
                "enabled": on_chk.isChecked() if on_chk else True,
                "listen": listen_chk.isChecked() if listen_chk else False,
            })
        return out

    def enabled_room_ids(self) -> list[str]:
        return [r["room_id"] for r in self.rows() if r["enabled"]]

    def listen_room_id(self) -> str:
        return next((r["room_id"] for r in self.rows() if r["listen"]), "")


class SettingsDialog(QDialog):
    def __init__(self, parent: "MonitorWidget"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(620, 860)
        self._win = parent
        s = parent.settings

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

        # --- Channels ------------------------------------------------------
        ch = QGroupBox("Notification channels")
        ch.setStyleSheet(_GROUP_STYLE)
        cl = QFormLayout(ch)
        self.teams_en = QCheckBox("Teams")
        self.teams_en.setToolTip(
            "Master switch for Microsoft Teams alerts. Must be on AND the Teams "
            "webhook below configured for alerts to reach Teams.")
        self.email_en = QCheckBox("Email")
        self.email_en.setToolTip(
            "Master switch for e-mail alerts. Must be on AND the SMTP section "
            "below configured with at least one enabled recipient.")
        self.webex_en = QCheckBox("Webex")
        self.webex_en.setToolTip(
            "Master switch for Webex alerts. Must be on AND the Webex section "
            "below configured (webhook URL or bot token + rooms).")
        for w, key in ((self.teams_en, "teams_enabled"),
                       (self.email_en, "email_enabled"),
                       (self.webex_en, "webex_enabled")):
            w.setStyleSheet(_CHK_STYLE)
            w.setChecked(bool(s.get(key)))
            cl.addRow("", w)
        self.plot_hours = _NoWheelSpinBox()
        self.plot_hours.setRange(0, 168)
        self.plot_hours.setToolTip(
            "Hours of trend history rendered into the PNG attached to each alert "
            "(e.g. 12 = last 12 h). 0 = send text-only alerts with no plot. "
            "Range 0–168 h (one week).")
        self.plot_hours.setValue(int(s.get("alert_plot_hours", 12)))
        cl.addRow("Alert plot window (h, 0=off)", self.plot_hours)
        lay.addWidget(ch)

        # --- Teams ---------------------------------------------------------
        teams = QGroupBox("Microsoft Teams")
        teams.setStyleSheet(_GROUP_STYLE)
        tl = QVBoxLayout(teams)
        tl.addWidget(QLabel("Incoming Webhook URL"))
        self.webhook = QLineEdit(s["teams_webhook_url"])
        self.webhook.setPlaceholderText("https://…webhook…")
        self.webhook.setToolTip(
            "Incoming Webhook URL of the Teams channel that should receive "
            "alerts. Create it in the channel's Connectors → Incoming Webhook, "
            "then paste the full https:// URL here.")
        tl.addWidget(self.webhook)
        bt = QPushButton("Send test to Teams")
        bt.setStyleSheet(_BTN_PRIMARY)
        bt.setToolTip("Post a test message to the webhook above right now, to "
                      "confirm the URL works. Does not save the settings.")
        bt.clicked.connect(self._test_teams)
        tl.addWidget(bt)
        lay.addWidget(teams)

        # --- Email ---------------------------------------------------------
        email = QGroupBox("Email (SMTP)")
        email.setStyleSheet(_GROUP_STYLE)
        ef = QFormLayout(email)
        self.smtp_host = QLineEdit(s.get("smtp_host", ""))
        self.smtp_host.setToolTip(
            "Hostname of the outgoing mail (SMTP) server, e.g. "
            "smtp.office365.com or your site's relay.")
        ef.addRow("SMTP host", self.smtp_host)
        self.smtp_port = _NoWheelSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setToolTip(
            "SMTP server port. Typical values: 587 for STARTTLS, 465 for SSL, "
            "25 for an unencrypted relay. Match this to the Security setting.")
        self.smtp_port.setValue(int(s.get("smtp_port", 587)))
        ef.addRow("Port", self.smtp_port)
        self.smtp_sec = _NoWheelComboBox()
        self.smtp_sec.addItems(["none", "starttls", "ssl"])
        self.smtp_sec.setToolTip(
            "Connection encryption: 'starttls' (upgrade on port 587, most "
            "common), 'ssl' (implicit TLS on port 465), or 'none' (unencrypted, "
            "internal relays only).")
        self.smtp_sec.setCurrentText(s.get("smtp_security", "starttls"))
        ef.addRow("Security", self.smtp_sec)
        self.smtp_user = QLineEdit(s.get("smtp_user", ""))
        self.smtp_user.setToolTip(
            "Login name for the SMTP server, if it requires authentication. "
            "Leave blank for an open internal relay that needs no login.")
        ef.addRow("Username (optional)", self.smtp_user)
        self.smtp_pass = QLineEdit(s.get("smtp_password", ""))
        self.smtp_pass.setEchoMode(QLineEdit.Password)
        self.smtp_pass.setToolTip(
            "Encrypted at rest (Windows DPAPI) for your Windows account only — "
            "the saved value is unreadable to others / on other PCs. "
            "Tip: use ${ENV:NAME} to read from an env var instead.")
        ef.addRow("Password", self.smtp_pass)
        self.email_from = QLineEdit(s.get("email_from", ""))
        self.email_from.setToolTip(
            "Address that alert e-mails are sent from (the 'From' header). "
            "Many servers require this to match the authenticated username.")
        ef.addRow("From address", self.email_from)
        recip_lbl = QLabel("Recipients")
        recip_lbl.setToolTip("Tick 'On' for everyone who should get alert e-mails.")
        ef.addRow(recip_lbl)
        self.email_contacts = EmailContactsWidget()
        self.email_contacts.set_rows(s.get("email_contacts", []))
        ef.addRow(self.email_contacts)
        be = QPushButton("Send test email")
        be.setStyleSheet(_BTN_PRIMARY)
        be.setToolTip("Send a test message now using the SMTP settings above to "
                      "every enabled recipient, to confirm delivery works. "
                      "Does not save the settings.")
        be.clicked.connect(self._test_email)
        ef.addRow("", be)
        lay.addWidget(email)

        # --- Webex ---------------------------------------------------------
        webex = QGroupBox("Webex")
        webex.setStyleSheet(_GROUP_STYLE)
        wf = QFormLayout(webex)
        self.webex_mode = _NoWheelComboBox()
        self.webex_mode.addItems(["webhook", "bot"])
        self.webex_mode.setToolTip(
            "'webhook' = one-way alerts to a single space via an Incoming "
            "Webhook URL (simplest). 'bot' = a Webex bot that can broadcast to "
            "several rooms and, optionally, accept two-way commands. Switching "
            "mode enables the relevant fields below.")
        self.webex_mode.setCurrentText(s.get("webex_mode", "webhook"))
        self.webex_mode.currentTextChanged.connect(self._update_webex_fields)
        wf.addRow("Mode", self.webex_mode)
        self.webex_url = QLineEdit(s.get("webex_webhook_url", ""))
        self.webex_url.setPlaceholderText("https://…webex incoming webhook…")
        self.webex_url.setToolTip(
            "Webex Incoming Webhook URL for the target space (webhook mode "
            "only). Create it via the Webex 'Incoming Webhooks' integration.")
        wf.addRow("Webhook URL", self.webex_url)
        self.webex_token = QLineEdit(s.get("webex_bot_token", ""))
        self.webex_token.setEchoMode(QLineEdit.Password)
        self.webex_token.setToolTip(
            "Encrypted at rest (Windows DPAPI) for your Windows account only — "
            "the saved value is unreadable to others / on other PCs. "
            "Tip: use ${ENV:NAME} to read from an env var instead.")
        wf.addRow("Bot token", self.webex_token)
        rooms_lbl = QLabel("Rooms")
        rooms_lbl.setToolTip(
            "Tick 'On' for every room the bot should broadcast alerts to. "
            "Exactly one room can be 'Listen for commands' (two-way chat).")
        wf.addRow(rooms_lbl)
        self.webex_rooms = WebexRoomsWidget()
        self.webex_rooms.set_rows(s.get("webex_rooms", []))
        wf.addRow(self.webex_rooms)
        bw = QPushButton("Send test to Webex")
        bw.setStyleSheet(_BTN_PRIMARY)
        bw.setToolTip("Send a test message now using the Webex settings above "
                      "(webhook URL, or bot token to every enabled room), to "
                      "confirm it works. Does not save the settings.")
        bw.clicked.connect(self._test_webex)
        wf.addRow("", bw)

        self.webex_cmds = QCheckBox("Accept commands from Webex (bot mode only)")
        self.webex_cmds.setStyleSheet(_CHK_STYLE)
        self.webex_cmds.setToolTip(
            "When on (bot mode only), the bot reads the 'Listen for commands' "
            "room and responds to chat commands such as status/stop. When off, "
            "the bot only sends alerts and never reads messages.")
        self.webex_cmds.setChecked(bool(s.get("webex_commands_enabled", True)))
        wf.addRow("", self.webex_cmds)
        self.webex_cmd_poll = _NoWheelSpinBox()
        self.webex_cmd_poll.setRange(1, 120)
        self.webex_cmd_poll.setToolTip(
            "How often (seconds) the bot checks the listen room for new "
            "commands. Lower = snappier replies but more API calls; below "
            "~5 s Webex starts rejecting polls with HTTP 429. Range 1–120 s.")
        self.webex_cmd_poll.setValue(int(s.get("webex_command_poll_s", 5)))
        wf.addRow("Command poll (s)", self.webex_cmd_poll)
        self.webex_allow = QLineEdit("; ".join(s.get("webex_command_allowlist", [])))
        self.webex_allow.setPlaceholderText("allowed sender e-mails, empty = anyone in room")
        self.webex_allow.setToolTip(
            "Semicolon-separated list of e-mail addresses allowed to issue "
            "commands to the bot. Leave empty to accept commands from anyone in "
            "the listen room.")
        wf.addRow("Command allowlist", self.webex_allow)

        lay.addWidget(webex)
        self._update_webex_fields(self.webex_mode.currentText())

        # --- Monitoring ----------------------------------------------------
        form_grp = QGroupBox("Monitoring")
        form_grp.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(form_grp)
        self.poll = _NoWheelSpinBox(); self.poll.setRange(5, 3600)
        self.poll.setToolTip(
            "How often (seconds) every PV is read from the archiver while "
            "monitoring. Lower = faster detection but more load. Range "
            "5–3600 s.")
        self.poll.setValue(int(s["poll_interval_s"]))
        form.addRow("Poll interval (s)", self.poll)
        self.poll_workers = _NoWheelSpinBox(); self.poll_workers.setRange(1, 64)
        self.poll_workers.setToolTip(
            "How many PVs are fetched from the archiver at once each poll. The "
            "fetch is network-bound, so a pass takes about "
            "ceil(PV count / this) x per-request time; when that exceeds the "
            "poll interval, ticks are skipped and the table lags. Raise this "
            "when monitoring many PVs. Range 1-64.")
        self.poll_workers.setValue(int(s.get("poll_max_workers", 24)))
        form.addRow("Concurrent fetches", self.poll_workers)
        self.avg_n = _NoWheelSpinBox(); self.avg_n.setRange(1, 500)
        self.avg_n.setToolTip(
            "Each poll averages up to this many recent samples before comparing "
            "to thresholds, smoothing out noise. 1 = use the latest raw sample. "
            "Range 1–500.")
        self.avg_n.setValue(int(s["avg_last_n"]))
        form.addRow("Average last N samples", self.avg_n)
        self.window_s = _NoWheelSpinBox(); self.window_s.setRange(5, 3600)
        self.window_s.setToolTip(
            "Time span (seconds) of archiver data fetched each poll to draw the "
            "'last N samples' from. Should comfortably cover N samples at the "
            "PV's update rate. Range 5–3600 s.")
        self.window_s.setValue(int(s["sample_window_s"]))
        form.addRow("Sample window (s)", self.window_s)
        self.debounce = _NoWheelSpinBox(); self.debounce.setRange(1, 20)
        self.debounce.setToolTip(
            "A new state (Warning/Alarm/OK) must persist this many consecutive "
            "polls before an alert is committed, suppressing brief spikes. "
            "1 = alert immediately. Range 1–20 polls.")
        self.debounce.setValue(int(s["debounce_count"]))
        form.addRow("Debounce (polls)", self.debounce)
        self.settle = _NoWheelDoubleSpinBox(); self.settle.setRange(0, 120)
        self.settle.setDecimals(1); self.settle.setSingleStep(0.5)
        self.settle.setToolTip(
            "When a PV first leaves its limits, hold the alert this many "
            "minutes to give the value a chance to settle. After the wait one "
            "message is sent only if the Warning/Alarm is still ongoing; an "
            "excursion that recovered within the window sends nothing. "
            "0 = alert immediately. Range 0–120 min.")
        self.settle.setValue(float(s.get("settle_minutes", 5.0)))
        form.addRow("Settle wait (min, 0=off)", self.settle)
        self.stable = _NoWheelSpinBox(); self.stable.setRange(0, 3600)
        self.stable.setToolTip(
            "A state change is only announced once the level has stayed "
            "unchanged for this many seconds; every further change restarts "
            "the clock. A value oscillating across a limit therefore sends "
            "nothing until it settles — and nothing at all if it ends up back "
            "where it started. 0 = announce immediately. Range 0–3600 s.")
        self.stable.setValue(int(s.get("stable_seconds", 120)))
        form.addRow("Stability hold (s, 0=off)", self.stable)
        self.cooldown = _NoWheelSpinBox(); self.cooldown.setRange(0, 1440)
        self.cooldown.setToolTip(
            "Minimum minutes between repeat notifications for a PV that stays in "
            "the same alert state, to avoid spam. 0 = notify only on state "
            "change (no repeats). Range 0–1440 min.")
        self.cooldown.setValue(int(s["renotify_cooldown_minutes"]))
        form.addRow("Re-notify cooldown (min, 0=off)", self.cooldown)
        self.recovery = QCheckBox("Notify on recovery to OK")
        self.recovery.setStyleSheet(_CHK_STYLE)
        self.recovery.setToolTip(
            "When on, send an 'all clear' notification once a PV returns to OK "
            "after a Warning/Alarm. When off, recoveries are silent.")
        self.recovery.setChecked(bool(s["recovery_notify"]))
        form.addRow("", self.recovery)
        self.trend_adaptive = QCheckBox("Adapt reminder rate to the value trend")
        self.trend_adaptive.setStyleSheet(_CHK_STYLE)
        self.trend_adaptive.setToolTip(
            "For a PV that is already in Warning/Alarm, adjust how often it is "
            "re-notified based on its recent trend: send reminders twice as "
            "fast while the value is drifting further past the limit "
            "(worsening), and twice as slow while it is moving back toward "
            "normal (self-correcting). A steady value keeps the normal "
            "re-notify cooldown. Only affects reminder timing, never the "
            "alarm state itself.")
        self.trend_adaptive.setChecked(bool(s.get("trend_adaptive_enabled", True)))
        form.addRow("", self.trend_adaptive)
        self.trend_lookback = _NoWheelDoubleSpinBox()
        self.trend_lookback.setRange(1, 120)
        self.trend_lookback.setDecimals(1); self.trend_lookback.setSingleStep(1.0)
        self.trend_lookback.setToolTip(
            "How many minutes of recent history the trend check looks at when "
            "deciding whether an alarming value is improving or worsening. "
            "Range 1–120 min.")
        self.trend_lookback.setValue(float(s.get("trend_lookback_minutes", 10.0)))
        form.addRow("Trend look-back (min)", self.trend_lookback)
        self.trend_flat = _NoWheelDoubleSpinBox()
        self.trend_flat.setRange(0.1, 50.0)
        self.trend_flat.setDecimals(1); self.trend_flat.setSingleStep(0.5)
        self.trend_flat.setSuffix(" %")
        self.trend_flat.setToolTip(
            "How much the averaged value must change across the look-back "
            "window to count as a real trend rather than 'steady'. Below this "
            "the reminder rate stays normal. Range 0.1–50 %.")
        self.trend_flat.setValue(float(s.get("trend_flat_frac", 0.02)) * 100.0)
        form.addRow("Trend 'steady' band (%)", self.trend_flat)
        self.hist = _NoWheelSpinBox(); self.hist.setRange(10, 10080)
        self.hist.setToolTip(
            "How many minutes of polled samples are kept in memory per PV for "
            "the live graph. Larger = longer graph history but more memory. "
            "Range 10–10080 min (one week).")
        self.hist.setValue(int(s["history_minutes"]))
        form.addRow("History kept (min)", self.hist)
        self.graph_win = _NoWheelSpinBox(); self.graph_win.setRange(1, 10080)
        self.graph_win.setToolTip(
            "Default visible time span (minutes) on the live graph's X axis. "
            "Can't show more than 'History kept' holds. Range 1–10080 min.")
        self.graph_win.setValue(int(s["graph_window_minutes"]))
        form.addRow("Graph window (min)", self.graph_win)
        self.valid_min = OptionalDoubleField("enable")
        self.valid_min.setToolTip(
            "Default sensor-error floor for every PV: readings below this are "
            "discarded as bad. Individual PVs can override it. Unticked = no "
            "lower bound by default.")
        self.valid_min.set_value(s.get("valid_min_default"))
        form.addRow("Global reject below <", self.valid_min)
        self.valid_max = OptionalDoubleField("enable")
        self.valid_max.setToolTip(
            "Default sensor-error ceiling for every PV: readings above this are "
            "discarded as bad. Individual PVs can override it. Unticked = no "
            "upper bound by default.")
        self.valid_max.set_value(s.get("valid_max_default"))
        form.addRow("Global reject above >", self.valid_max)
        self.autostart = QCheckBox("Start monitoring on launch")
        self.autostart.setStyleSheet(_CHK_STYLE)
        self.autostart.setToolTip(
            "When on, the app begins polling and evaluating alerts automatically "
            "as soon as it opens, without clicking 'Start monitoring'.")
        self.autostart.setChecked(bool(s["start_monitoring_on_launch"]))
        form.addRow("", self.autostart)
        lay.addWidget(form_grp)
        lay.addStretch(1)

        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        outer.addWidget(self.test_status)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _update_webex_fields(self, mode: str):
        is_bot = (mode == "bot")
        self.webex_url.setEnabled(not is_bot)
        self.webex_token.setEnabled(is_bot)
        self.webex_rooms.setEnabled(is_bot)

    def _show_test(self, ok: bool, name: str, err: str):
        if ok:
            self.test_status.setText(f"✓ {name} test sent.")
            self.test_status.setStyleSheet(f"color:{SUCCESS};")
        else:
            self.test_status.setText(f"✗ {name} failed: {err}")
            self.test_status.setStyleSheet(f"color:{DANGER};")

    def _test_teams(self):
        from alerting import TeamsClient
        c = TeamsClient(self.webhook.text().strip(),
                        float(self._win.settings["http_timeout_s"]))
        self._show_test(c.send_test(), "Teams", c.last_error)

    def _test_email(self):
        from alerting import EmailNotifier
        c = EmailNotifier(
            host=self.smtp_host.text().strip(), port=self.smtp_port.value(),
            security=self.smtp_sec.currentText(),
            username=self.smtp_user.text().strip(), password=self.smtp_pass.text(),
            from_addr=self.email_from.text().strip(),
            recipients=self.email_contacts.enabled_addresses(),
            timeout=float(self._win.settings["http_timeout_s"]))
        self._show_test(c.send_test(), "Email", c.last_error)

    def _test_webex(self):
        from alerting import WebexNotifier
        c = WebexNotifier(
            mode=self.webex_mode.currentText(),
            webhook_url=self.webex_url.text().strip(),
            bot_token=self.webex_token.text().strip(),
            room_ids=self.webex_rooms.enabled_room_ids(),
            listen_room_id=self.webex_rooms.listen_room_id(),
            timeout=float(self._win.settings["http_timeout_s"]))
        self._show_test(c.send_test(), "Webex", c.last_error)

    def _save(self):
        s = self._win.settings
        # channels
        s["teams_enabled"] = self.teams_en.isChecked()
        s["email_enabled"] = self.email_en.isChecked()
        s["webex_enabled"] = self.webex_en.isChecked()
        s["alert_plot_hours"] = self.plot_hours.value()
        # teams
        s["teams_webhook_url"] = self.webhook.text().strip()
        # email
        s["smtp_host"] = self.smtp_host.text().strip()
        s["smtp_port"] = self.smtp_port.value()
        s["smtp_security"] = self.smtp_sec.currentText()
        s["smtp_user"] = self.smtp_user.text().strip()
        s["smtp_password"] = encrypt_secret(self.smtp_pass.text())
        s["email_from"] = self.email_from.text().strip()
        s["email_contacts"] = self.email_contacts.rows()
        # webex
        s["webex_mode"] = self.webex_mode.currentText()
        s["webex_webhook_url"] = self.webex_url.text().strip()
        s["webex_bot_token"] = encrypt_secret(self.webex_token.text().strip())
        s["webex_rooms"] = self.webex_rooms.rows()
        s["webex_commands_enabled"] = self.webex_cmds.isChecked()
        s["webex_command_poll_s"] = self.webex_cmd_poll.value()
        s["webex_command_allowlist"] = _parse_recipients(self.webex_allow.text())
        # monitoring
        s["poll_interval_s"] = self.poll.value()
        s["poll_max_workers"] = self.poll_workers.value()
        s["avg_last_n"] = self.avg_n.value()
        s["sample_window_s"] = self.window_s.value()
        s["debounce_count"] = self.debounce.value()
        s["settle_minutes"] = self.settle.value()
        s["stable_seconds"] = self.stable.value()
        s["renotify_cooldown_minutes"] = self.cooldown.value()
        s["recovery_notify"] = self.recovery.isChecked()
        s["trend_adaptive_enabled"] = self.trend_adaptive.isChecked()
        s["trend_lookback_minutes"] = self.trend_lookback.value()
        s["trend_flat_frac"] = self.trend_flat.value() / 100.0
        s["history_minutes"] = self.hist.value()
        s["graph_window_minutes"] = self.graph_win.value()
        s["valid_min_default"] = self.valid_min.value()
        s["valid_max_default"] = self.valid_max.value()
        s["start_monitoring_on_launch"] = self.autostart.isChecked()
        self.accept()


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

import matplotlib  # noqa: E402
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT as NavToolbar,
)
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402

MAX_GRAPH_POINTS = 3000


class _LightNavToolbar(NavToolbar):
    """Matplotlib nav toolbar with a light background and forced near-black
    icons. The stock toolbar recolors its glyphs from the Qt palette, so with
    Windows in dark mode they come out white — invisible on this app's light
    UI. Recoloring here makes the buttons legible regardless of the OS theme.

    It also reports view changes: after every zoom/pan/back/forward it calls
    ``on_user_view`` so the panel can pin the user's view across the periodic
    redraws, and Home calls ``on_home`` to return to the live rolling window
    instead of matplotlib's stale first-drawn view."""

    def __init__(self, canvas, parent=None, on_user_view=None, on_home=None):
        super().__init__(canvas, parent)
        self._on_user_view = on_user_view
        self._on_home = on_home
        self.setStyleSheet(
            "QToolBar { background: #f2f2f2; border: 1px solid #ccc; }"
            "QToolButton { background: transparent; color: #111; }"
            "QToolButton:hover { background: #d8e8ff; }"
            "QToolButton:checked { background: #cfe0f7; }"
            "QLabel { color: #111; }")

    def _notify_user_view(self):
        if self._on_user_view is not None:
            self._on_user_view()

    def release_zoom(self, event):
        super().release_zoom(event)
        self._notify_user_view()

    def release_pan(self, event):
        super().release_pan(event)
        self._notify_user_view()

    def back(self, *args):
        super().back(*args)
        self._notify_user_view()

    def forward(self, *args):
        super().forward(*args)
        self._notify_user_view()

    def home(self, *args):
        if self._on_home is not None:
            self._on_home()
        else:
            super().home(*args)

    def _icon(self, name):
        p = Path(matplotlib.get_data_path()) / "images" / name
        large = p.with_name(p.name.replace(".png", "_large.png"))
        pm = QPixmap(str(large if large.exists() else p))
        pm.setDevicePixelRatio(self.devicePixelRatioF() or 1)
        mask = pm.createMaskFromColor(QColor("black"), Qt.MaskOutColor)
        pm.fill(QColor("#111111"))
        pm.setMask(mask)
        return QIcon(pm)


def render_pv_png(pv_name: str, display_name: str, hours: float,
                  thr: Thresholds, timeout: float,
                  vmin=None, vmax=None) -> bytes | None:
    """Fetch the last `hours` h from CPVA and render a value+threshold PNG.

    Worker-thread safe: builds its own Figure and uses the non-Qt Agg canvas,
    rendering to in-memory PNG bytes (no temp file, no shared matplotlib state).
    Readings outside [vmin, vmax] are dropped as sensor errors.
    """
    end = api.now_ns()
    start = end - int(hours * 3600 * 1e9)
    samples = api.cpva_fetch_samples_chunked(pv_name, start, end, timeout)
    xs, ys, units = [], [], ""
    for s in samples:
        v = api.cpva_decode_value(s)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if _out_of_range(fv, vmin, vmax):
            continue
        t = s.get("time")
        if isinstance(t, (int, float)):
            xs.append(api.ns_to_prague(int(t)))
            ys.append(fv)
            u = api.cpva_decode_units(s)
            if u:
                units = u
    if not xs:
        return None

    fig = Figure(figsize=(8, 4), dpi=110)
    ax = fig.add_subplot(111)
    ax.plot(xs, ys, drawstyle="steps-post", color=PRIMARY, linewidth=1.5)
    for val, ls, lw in ((thr.warn_low, "--", 1.0), (thr.warn_high, "--", 1.0),
                        (thr.alarm_low, "-.", 1.5), (thr.alarm_high, "-.", 1.5)):
        if val is not None:
            ax.axhline(val, linestyle=ls, linewidth=lw, alpha=0.7,
                       color=ALARM_COLOR if ls == "-." else WARN_COLOR)
    ax.set_title(f"{display_name}  (last {hours:g} h)")
    ax.set_ylabel(units)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=api.TZ_PRAGUE))
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    return buf.getvalue()


class _AxisRangeDialog(QDialog):
    """Small lo/hi prompt for the graph's axis right-click menu."""

    def __init__(self, parent, title: str, lo: float, hi: float):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.lo = QDoubleSpinBox()
        self.hi = QDoubleSpinBox()
        for sb in (self.lo, self.hi):
            sb.setRange(-1e12, 1e12)
            sb.setDecimals(4)
        self.lo.setValue(lo)
        self.hi.setValue(hi)
        form.addRow("Min", self.lo)
        form.addRow("Max", self.hi)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> tuple[float, float]:
        return self.lo.value(), self.hi.value()


_DT_FMT = "%Y-%m-%d %H:%M"


class _AxisDateRangeDialog(QDialog):
    """Lo/hi prompt for the X (time) axis, in human-readable local time."""

    def __init__(self, parent, lo_dt: datetime, hi_dt: datetime):
        super().__init__(parent)
        self.setWindowTitle("X range (Europe/Prague)")
        form = QFormLayout(self)
        self.lo = QLineEdit(lo_dt.strftime(_DT_FMT))
        self.hi = QLineEdit(hi_dt.strftime(_DT_FMT))
        form.addRow("From", self.lo)
        form.addRow("To", self.hi)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> tuple[datetime, datetime]:
        return (datetime.strptime(self.lo.text().strip(), _DT_FMT),
                datetime.strptime(self.hi.text().strip(), _DT_FMT))


class GraphPanel(QWidget):
    def __init__(self, win: "MonitorWidget"):
        super().__init__()
        self._win = win
        self._yaxis: Optional[tuple] = None   # (lo, hi) fixed Y range, or None=auto
        # (xlim, ylim) pinned by a toolbar zoom/pan. While set, the periodic
        # redraws keep this view instead of snapping back to the rolling window.
        self._user_view: Optional[tuple] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.combo = _NoWheelComboBox()
        self.combo.setToolTip(
            "Choose what the graph below plots: 'All PVs' overlays every PV "
            "(no threshold lines), or pick a single PV to see it alone with its "
            "Warning/Alarm threshold lines drawn in.")
        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        top.addWidget(self.combo, 1)
        self.btn_reset_zoom = QPushButton("⤺ Reset zoom")
        self.btn_reset_zoom.setStyleSheet(SECONDARY_STYLE)
        self.btn_reset_zoom.setToolTip(
            "Drop the current zoom/pan and return to the live rolling window "
            "(same as the toolbar's Home button). The ◀ toolbar arrow steps "
            "back one zoom at a time.")
        self.btn_reset_zoom.clicked.connect(self.reset_zoom)
        top.addWidget(self.btn_reset_zoom)
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.setStyleSheet(_BTN_PRIMARY)
        self.btn_refresh.setToolTip(
            "Reload the PV list into the selector and redraw the graph from "
            "scratch with autoscaled axes (clears any fixed Y range and zoom).")
        self.btn_refresh.clicked.connect(self.reset_view)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        self.fig = Figure(figsize=(6, 3), dpi=96)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = _LightNavToolbar(
            self.canvas, self,
            on_user_view=self._capture_user_view, on_home=self.reset_zoom)
        lay.addWidget(self.toolbar)
        lay.addWidget(self.canvas, 1)

        # Crosshair cursor: artists are recreated on every redraw (ax.clear()
        # drops them); animated=True keeps them out of the blit background so
        # moving the mouse never re-renders the whole figure.
        self._cross: list = []
        self._extra_axes: list = []   # twin y-axes, one per extra unit
        self._snap_np = None    # (times_num, values, label) for single-PV snap
        self._units = ""
        self._blit_bg = None
        self._mouse_ev = None
        self._mouse_pending = False
        self._grid_on = True
        # Fixed Y range per extra (twin) axis, keyed by its index in
        # self._extra_axes. self._yaxis (above) already covers the main axis.
        self._extra_yaxis: dict[int, tuple] = {}
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("figure_leave_event", self._on_leave)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_click)

    def _on_combo_changed(self, *_):
        # New selection = different data/scale; a zoom pinned on the previous
        # selection would show a nonsense viewport, so drop it.
        self._user_view = None
        self.redraw()

    def _capture_user_view(self):
        """Pin the current axes view (called after a toolbar zoom/pan) so
        periodic redraws stop snapping back to the rolling window."""
        self._user_view = (self.ax.get_xlim(), self.ax.get_ylim())

    def reset_zoom(self):
        """Unpin the user's zoom/pan and return to the live rolling window."""
        self._user_view = None
        self.toolbar.update()      # flush the toolbar's back/forward history
        self.redraw()

    def reset_view(self):
        """Reload the PV list, drop any fixed Y range and redraw autoscaled."""
        self._yaxis = None
        self._user_view = None
        self.toolbar.update()
        self.refresh_combo()
        self.redraw()

    def set_yaxis(self, lo, hi):
        """Fix the Y range (lo, hi), or pass (None, None) to restore autoscale."""
        self._yaxis = None if lo is None or hi is None else (float(lo), float(hi))
        self._user_view = None    # an explicit Y command overrides a pinned zoom
        self.redraw()

    # --- right-click axis settings menu -----------------------------------

    def _axis_at(self, event):
        """Return ('x', ax) or ('y', ax) for the axis under a click, else None.

        Hit-tests against each axis's rendered tick-label bbox rather than
        guessing pixel offsets, so it works no matter how many twin (unit)
        axes are stacked on the right.
        """
        renderer = self.canvas.get_renderer()
        if renderer is None or event.x is None or event.y is None:
            return None
        xb = self.ax.xaxis.get_tightbbox(renderer)
        if xb is not None and xb.contains(event.x, event.y):
            return ("x", self.ax)
        for a in [self.ax] + self._extra_axes:
            yb = a.yaxis.get_tightbbox(renderer)
            if yb is not None and yb.contains(event.x, event.y):
                return ("y", a)
        return None

    def _on_canvas_click(self, event):
        if event.button != 3:   # right-click only
            return
        hit = self._axis_at(event)
        if hit is None:
            return
        kind, ax = hit
        gui_ev = event.guiEvent
        pos = gui_ev.position().toPoint() if hasattr(gui_ev, "position") \
            else gui_ev.pos()
        self._show_axis_menu(kind, ax, self.canvas.mapToGlobal(pos))

    def _show_axis_menu(self, kind: str, ax, global_pos):
        menu = QMenu(self)
        if kind == "x":
            menu.addAction("Set X range…", lambda: self._prompt_xrange(ax))
            menu.addAction("Reset X to rolling window", self.reset_zoom)
        else:
            menu.addAction("Set Y range…", lambda: self._prompt_yrange(ax))
            menu.addAction("Autoscale this Y axis",
                            lambda: self._clear_yrange(ax))
        menu.addSeparator()
        grid_action = menu.addAction("Grid lines")
        grid_action.setCheckable(True)
        grid_action.setChecked(self._grid_on)
        grid_action.toggled.connect(self._set_grid_on)
        menu.exec(global_pos)

    def _set_grid_on(self, on: bool):
        self._grid_on = on
        self.redraw()

    def _prompt_xrange(self, ax):
        lo, hi = ax.get_xlim()
        lo_dt = mdates.num2date(lo, tz=api.TZ_PRAGUE)
        hi_dt = mdates.num2date(hi, tz=api.TZ_PRAGUE)
        dlg = _AxisDateRangeDialog(self, lo_dt, hi_dt)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            new_lo, new_hi = dlg.values()
        except ValueError:
            QMessageBox.warning(self, "Invalid date",
                                 f"Use the format {_DT_FMT}.")
            return
        new_lo = new_lo.replace(tzinfo=api.TZ_PRAGUE)
        new_hi = new_hi.replace(tzinfo=api.TZ_PRAGUE)
        self._user_view = ((mdates.date2num(new_lo), mdates.date2num(new_hi)),
                            ax.get_ylim())
        self.redraw()

    def _prompt_yrange(self, ax):
        lo, hi = ax.get_ylim()
        dlg = _AxisRangeDialog(self, "Y range", lo, hi)
        if dlg.exec() == QDialog.Accepted:
            new_lo, new_hi = dlg.values()
            if ax is self.ax:
                self.set_yaxis(new_lo, new_hi)
            else:
                idx = self._extra_axes.index(ax)
                self._extra_yaxis[idx] = (new_lo, new_hi)
                self.redraw()

    def _clear_yrange(self, ax):
        if ax is self.ax:
            self.set_yaxis(None, None)
        else:
            idx = self._extra_axes.index(ax)
            self._extra_yaxis.pop(idx, None)
            self.redraw()

    def select_pv(self, name_or_all: Optional[str]):
        """Set the combo to a PV name (or None for 'All PVs'). Returns True if found."""
        if name_or_all is None:
            self.combo.setCurrentIndex(0)
            return True
        idx = self.combo.findData(name_or_all)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
            return True
        return False

    def refresh_combo(self):
        cur = self.combo.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("All PVs")
        for pv in self._win.pvs:
            self.combo.addItem(pv.display_name, pv.name)
        idx = self.combo.findText(cur)
        self.combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo.blockSignals(False)

    def _plot_one(self, pv: PVConfig, color, with_thresholds: bool, ax=None):
        ax = ax if ax is not None else self.ax
        rt = self._win.runtime.get(pv.name)
        if not rt or not rt.history:
            return
        pts = list(rt.history)
        if len(pts) > MAX_GRAPH_POINTS:
            # Thin evenly across the whole history (a plain tail-cut would
            # silently shorten the graph's time span); keep the newest point.
            step = len(pts) / MAX_GRAPH_POINTS
            last = pts[-1]
            pts = [pts[int(i * step)] for i in range(MAX_GRAPH_POINTS)]
            pts[-1] = last
        xs = [api.ns_to_prague(t) for t, _ in pts]
        ys = [v for _, v in pts]
        ax.plot(xs, ys, drawstyle="steps-post", color=color,
                label=pv.display_name, linewidth=1.6)
        if with_thresholds:
            for val, ls, lw in ((pv.warn_low, "--", 1.0), (pv.warn_high, "--", 1.0),
                                (pv.alarm_low, "-.", 1.6), (pv.alarm_high, "-.", 1.6)):
                if val is not None:
                    ax.axhline(val, color=color, linestyle=ls,
                               linewidth=lw, alpha=0.6)

    def _pv_units(self, pv: PVConfig) -> str:
        rt = self._win.runtime.get(pv.name)
        return (rt.current_units if rt and rt.current_units else pv.units) or ""

    # Units differing only in spelling/case ("DegC" vs "degC") share one axis.
    @staticmethod
    def _unit_key(unit: str) -> str:
        return (unit or "").strip().lower()

    _UNIT_LABELS = {
        "degc": "Temperature [°C]", "°c": "Temperature [°C]",
        "c": "Temperature [°C]", "k": "Temperature [K]",
        "degf": "Temperature [°F]",
        "pa": "Pressure [Pa]", "kpa": "Pressure [kPa]",
        "mpa": "Pressure [MPa]", "bar": "Pressure [bar]",
        "mbar": "Pressure [mbar]",
        "l/min": "Flow [l/min]", "lpm": "Flow [l/min]",
        "torr": "Pressure [Torr]", "mtorr": "Pressure [mTorr]",
    }

    # Units that count as temperature — kept on the familiar left axis.
    _TEMP_UNIT_KEYS = ("degc", "°c", "c", "k", "degf")

    @classmethod
    def _axis_label(cls, unit: str) -> str:
        return cls._UNIT_LABELS.get(cls._unit_key(unit),
                                    (unit or "").strip() or "value")

    def redraw(self):
        for a in self._extra_axes:
            a.remove()
        self._extra_axes = []
        self.ax.clear()
        sel = self.combo.currentData()
        win_min = float(self._win.settings["graph_window_minutes"])

        cmap = matplotlib.colormaps.get_cmap("tab10")
        pv = None
        if sel is None:  # "All PVs" — everything with its eye switched on
            visible = [p for p in self._win.pvs if p.show_in_graph]
            # Only PVs that actually have data get plotted — and only their
            # units get an axis, so a unit-less PV with no samples yet can't
            # spawn an empty 'value' axis.
            plottable = [p for p in visible
                         if (rt := self._win.runtime.get(p.name))
                         and rt.history]
            # One y-axis per distinct (normalized) unit: the first unit owns
            # the main axis, each further unit gets a twin axis on the right.
            units_order: list[str] = []
            unit_text: dict[str, str] = {}
            for p in plottable:
                k = self._unit_key(self._pv_units(p))
                if k not in units_order:
                    units_order.append(k)
                    unit_text[k] = self._pv_units(p)
            # Temperature owns the left (main) axis regardless of PV order;
            # other units (pressure, flow, …) go to twin axes on the right.
            units_order.sort(
                key=lambda k: 0 if k in self._TEMP_UNIT_KEYS else 1)
            ax_of_unit = {}
            for j, k in enumerate(units_order):
                if j == 0:
                    ax_of_unit[k] = self.ax
                else:
                    tw = self.ax.twinx()
                    if j >= 2:   # push 3rd+ axis outward so labels don't overlap
                        tw.spines["right"].set_position(("outward", 48 * (j - 1)))
                    self._extra_axes.append(tw)
                    ax_of_unit[k] = tw
            for i, p in enumerate(plottable):
                self._plot_one(p, cmap(i % 10), with_thresholds=False,
                               ax=ax_of_unit[self._unit_key(self._pv_units(p))])
            for k, a in ax_of_unit.items():
                a.set_ylabel(self._axis_label(unit_text[k]))
            # Any fixed range pinned via the extra-axis context menu wins over
            # autoscale (main axis fixed range is applied further below).
            for idx, a in enumerate(self._extra_axes):
                if idx in self._extra_yaxis:
                    a.set_ylim(*self._extra_yaxis[idx])
            self._units = unit_text[units_order[0]] if len(units_order) == 1 \
                else ""
        else:
            pv = next((p for p in self._win.pvs if p.name == sel), None)
            if pv:
                self._plot_one(pv, PRIMARY, with_thresholds=True)
            self._units = self._pv_units(pv) if pv else ""
            self.ax.set_ylabel(self._axis_label(self._units))

        # One combined legend for all axes, hosted on the main axis.
        handles, labels = [], []
        for a in [self.ax] + self._extra_axes:
            h, l = a.get_legend_handles_labels()
            handles += h
            labels += l
        if handles:
            self.ax.legend(handles, labels, loc="upper left", fontsize=8)
        self.ax.grid(self._grid_on, alpha=0.3)
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=api.TZ_PRAGUE))

        # A view the user zoomed/panned to is pinned and always wins over the
        # rolling window, so periodic redraws never yank the zoom away.
        if self._user_view is not None:
            self.ax.set_xlim(self._user_view[0])
            self.ax.set_ylim(self._user_view[1])
        else:
            # Limit x to the configured window if we have data. orig=False
            # gives the unit-converted float date numbers (the original data
            # are datetimes, which can't take a float offset). Twin axes share
            # x, so clamping the main axis clamps them all.
            all_x = [ln.get_xdata(orig=False)
                     for a in [self.ax] + self._extra_axes for ln in a.get_lines()]
            if any(len(x) for x in all_x):
                try:
                    xmax = max(x[-1] for x in all_x if len(x))
                    xmin = xmax - (win_min / (24 * 60))
                    self.ax.set_xlim(xmin, xmax)
                except Exception:
                    pass
            if self._yaxis is not None:
                self.ax.set_ylim(*self._yaxis)
        for lbl in self.ax.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_ha("center")
        self.fig.tight_layout()

        # Cache the plotted samples for the crosshair's nearest-point snap
        # (single-PV view only; the data line is the first one plotted).
        self._snap_np = None
        if pv is not None and self.ax.get_lines():
            ln = self.ax.get_lines()[0]
            xd = ln.get_xdata()
            if len(xd):
                self._snap_np = (np.asarray(mdates.date2num(xd), dtype=float),
                                 np.asarray(ln.get_ydata(), dtype=float),
                                 pv.display_name)
        self._make_cursor_artists()
        self._blit_bg = None
        self.canvas.draw_idle()

    # --- crosshair cursor ------------------------------------------------
    def _make_cursor_artists(self):
        # Park the (hidden) crosshair lines mid-view: axvline/axhline take
        # part in autoscale even when invisible, so the default x=0 would
        # drag a date axis all the way back to 1970 (and y=0 pull the y range
        # down to zero).
        x_mid = sum(self.ax.get_xlim()) / 2
        y_mid = sum(self.ax.get_ylim()) / 2
        vl = self.ax.axvline(x_mid, color="#888", linewidth=0.8, linestyle="--",
                             visible=False, animated=True)
        hl = self.ax.axhline(y_mid, color="#888", linewidth=0.8, linestyle="--",
                             visible=False, animated=True)
        txt = self.ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            fontsize=8, color="#111", visible=False, animated=True, zorder=10,
            bbox=dict(boxstyle="round,pad=0.3", fc="#ffffe0", ec="#888",
                      alpha=0.9, linewidth=0.6))
        # Exact readouts pinned to the axes (CSS-studio style): the time on
        # the X axis under the cursor, the value on the Y axis beside it.
        _axis_bbox = dict(boxstyle="round,pad=0.25", fc="#ffffe0", ec="#888",
                          alpha=0.95, linewidth=0.6)
        xlab = self.ax.annotate(
            "", xy=(x_mid, 0), xycoords=("data", "axes fraction"),
            xytext=(0, -6), textcoords="offset points",
            ha="center", va="top", fontsize=8, color="#111",
            visible=False, animated=True, zorder=10, bbox=_axis_bbox)
        ylab = self.ax.annotate(
            "", xy=(0, y_mid), xycoords=("axes fraction", "data"),
            xytext=(-8, 0), textcoords="offset points",
            ha="right", va="center", fontsize=8, color="#111",
            visible=False, animated=True, zorder=10, bbox=_axis_bbox)
        self._cross = [vl, hl, txt, xlab, ylab]

    def _on_draw(self, *_):
        # Fresh blit background: hide the animated overlay so it isn't baked in.
        for a in self._cross:
            a.set_visible(False)
        self._blit_bg = self.canvas.copy_from_bbox(self.fig.bbox)
        # A periodic redraw just wiped the crosshair; if the mouse is still
        # over the canvas, repaint it at its last position once this draw is
        # done (the cursor only uses pixel coords, so a stale event is fine).
        if self._mouse_ev is not None and self.canvas.underMouse():
            QTimer.singleShot(0, self._process_mouse)

    def _on_mouse_move(self, event):
        self._mouse_ev = event
        if not self._mouse_pending:
            self._mouse_pending = True
            QTimer.singleShot(16, self._process_mouse)   # ~60 fps cap

    def _process_mouse(self):
        self._mouse_pending = False
        ev = self._mouse_ev
        if ev is None or not self._cross:
            return
        try:
            self._draw_cursor(ev)
        except Exception:
            pass   # replot may tear the figure down mid-hover — harmless

    def _on_leave(self, _e):
        self._mouse_ev = None    # stop the redraw hook resurrecting the cursor
        self._hide_cursor()

    def _hide_cursor(self):
        if not self._cross:
            return
        for a in self._cross:
            a.set_visible(False)
        if self._blit_bg is not None:
            self.canvas.restore_region(self._blit_bg)
            self.canvas.blit(self.fig.bbox)

    def _snap_value(self, x_f: float):
        """(label, value) of the plotted sample nearest to time x_f, or None."""
        if self._snap_np is None:
            return None
        arr, vals, label = self._snap_np
        pos = int(np.searchsorted(arr, x_f))
        if pos <= 0:
            idx = 0
        elif pos >= len(arr):
            idx = len(arr) - 1
        else:
            idx = pos if (arr[pos] - x_f) < (x_f - arr[pos - 1]) else pos - 1
        return label, float(vals[idx])

    def _draw_cursor(self, ev):
        # Work from pixel coordinates against the main axis: with twin y-axes
        # present, ev.inaxes/ev.ydata belong to the topmost twin, not self.ax.
        if (self._blit_bg is None or ev.x is None or ev.y is None
                or not self.ax.bbox.contains(ev.x, ev.y)):
            self._hide_cursor()
            return
        x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
        x, y = float(x), float(y)
        vl, hl, txt, xlab, ylab = self._cross
        vl.set_xdata([x, x])
        hl.set_ydata([y, y])
        try:
            t_str = mdates.num2date(x, tz=api.TZ_PRAGUE).strftime("%H:%M:%S")
        except Exception:
            t_str = ""
        u = f" {self._units}" if self._units else ""
        xlab.xy = (x, 0)
        xlab.set_text(t_str)
        ylab.xy = (0, y)
        ylab.set_text(f"{y:.4g}")
        lines = [t_str, f"y = {y:.4g}{u}"]
        snap = self._snap_value(x)
        if snap is not None:
            lines.append(f"{snap[0]} = {snap[1]:.4g}{u}")
        txt.set_text("\n".join(lines))
        txt.xy = (x, y)
        # Keep the box inside the axes: flip it near the right/top edges.
        xlo, xhi = self.ax.get_xlim()
        ylo, yhi = self.ax.get_ylim()
        right = x > (xlo + xhi) / 2
        top = y > (ylo + yhi) / 2
        txt.set_position((-12 if right else 12, -16 if top else 12))
        txt.set_horizontalalignment("right" if right else "left")
        txt.set_verticalalignment("top" if top else "bottom")
        for a in self._cross:
            a.set_visible(True)
        self.canvas.restore_region(self._blit_bg)
        for a in self._cross:
            self.ax.draw_artist(a)
        self.canvas.blit(self.fig.bbox)


# ---------------------------------------------------------------------------
# Alert dispatch worker (plot render + fan-out, off the UI thread)
# ---------------------------------------------------------------------------

class _AlertSignals(QObject):
    done = Signal(object)   # (tag, {channel: error}, had_png)


class _AlertWorker(QRunnable):
    def __init__(self, sig: _AlertSignals, hub: NotificationHub,
                 payload: AlertPayload, thr: Thresholds, hours: float,
                 timeout: float, tag: str, vmin=None, vmax=None):
        super().__init__()
        self._sig = sig
        self._hub = hub
        self._payload = payload
        self._thr = thr
        self._hours = hours
        self._timeout = timeout
        self._tag = tag
        self._vmin = vmin
        self._vmax = vmax

    def run(self):
        png = None
        if self._hours > 0:
            try:
                png = render_pv_png(self._payload.pv_name, self._payload.display_name,
                                    self._hours, self._thr, self._timeout,
                                    self._vmin, self._vmax)
            except Exception:  # noqa: BLE001 - alert must still go out text-only
                png = None
        errors = self._hub.dispatch(self._payload, png)
        _safe_emit(self._sig.done.emit, (self._tag, errors, png is not None))


# ---------------------------------------------------------------------------
# Webex two-way command workers (bot mode only)
# ---------------------------------------------------------------------------

class _MeIdSignals(QObject):
    done = Signal(object)   # bot_person_id_or_None


class _MeIdWorker(QRunnable):
    """Resolve the bot's own personId (blocking HTTP call) off the UI thread."""

    def __init__(self, sig: _MeIdSignals, webex):
        super().__init__()
        self._sig = sig
        self._webex = webex

    def run(self):
        bot_id = self._webex.get_me_id()
        _safe_emit(self._sig.done.emit, bot_id)


class _CmdPollSignals(QObject):
    done = Signal(object)   # (new_items_oldest_first, newest_id_or_None)


class _CmdPollWorker(QRunnable):
    """Fetch room messages and return only those newer than last_id (oldest-first)."""

    def __init__(self, sig: _CmdPollSignals, webex, last_id):
        super().__init__()
        self._sig = sig
        self._webex = webex
        self._last_id = last_id

    def run(self):
        items = self._webex.fetch_messages(20)   # newest first
        newest_id = items[0]["id"] if items else None
        new = []
        for it in items:
            if it.get("id") == self._last_id:
                break
            new.append(it)
        new.reverse()   # oldest-first execution order
        _safe_emit(self._sig.done.emit, (new, newest_id))


class _TextReplyWorker(QRunnable):
    """Fire-and-forget markdown reply into the Webex room."""

    def __init__(self, webex, markdown: str):
        super().__init__()
        self._webex = webex
        self._markdown = markdown

    def run(self):
        self._webex.post_text(self._markdown)


# ---------------------------------------------------------------------------
# Monitor tab widget
# ---------------------------------------------------------------------------

class MonitorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        cfg = load_config()
        self.settings = cfg["settings"]
        self.pvs: list[PVConfig] = [PVConfig.from_dict(d) for d in cfg["pvs"]]
        self.runtime: dict[str, PVRuntime] = {}
        self._all_channels: list[str] = []
        # Per-PV "Depends on" dropdowns (recreated on every model reset).
        self._dep_combos: dict[str, QComboBox] = {}
        # Clipboard for the right-click "Copy settings / Paste settings" feature.
        self._copied_settings: Optional[dict] = None
        self._copied_from: str = ""
        self._copied_desc: str = "settings"
        # Latest value of every gate PV that isn't itself a monitored PV,
        # refreshed each poll so state-dependent thresholds can switch.
        self._gate_values: dict[str, Optional[float]] = {}
        self._poll_gen = 0
        self._poll_inflight = False
        self._monitoring = False
        self._sim_timer: Optional[QTimer] = None
        self._resume_timer: Optional[QTimer] = None   # auto-resume after /stop <h>
        self._resume_at_ns = 0
        self._cmd_timer: Optional[QTimer] = None
        self._cmd_last_id = None
        self._cmd_primed = False
        self._cmd_handled_ids: deque = deque(maxlen=500)  # answered msg ids
        self._cmd_last_logged_error = ""
        self._cmd_bot_id = None
        self._cmd_poll_inflight = False
        self._cmd_poll_started_ns = 0   # when the in-flight poll was dispatched
        self._cmd_bot_id_inflight = False
        self._cmd_gen = 0
        self._cmd_backoff_until_ns = 0   # honour Webex 429 Retry-After
        self._watchdog_fail_streak = 0   # consecutive fully-failed polls
        self._watchdog_bad = False       # True once the "no data" alert fired

        self.hub = NotificationHub.from_settings(self.settings)
        self.evaluator = AlertEvaluator(self._eval_config())
        self._init_runtime()

        self._build_ui()
        self._prefetch_channels()
        self._start_cmd_listener()

        if self.settings.get("start_monitoring_on_launch"):
            self.toggle_monitoring(True)

    # --- setup ---------------------------------------------------------
    def _eval_config(self) -> EvalConfig:
        s = self.settings
        return EvalConfig(
            debounce_count=int(s["debounce_count"]),
            renotify_cooldown_minutes=float(s["renotify_cooldown_minutes"]),
            recovery_notify=bool(s["recovery_notify"]),
            settle_minutes=float(s.get("settle_minutes", 5.0)),
            stable_seconds=float(s.get("stable_seconds", 120)),
        )

    def _history_maxlen(self) -> int:
        return max(10, int(self.settings["history_minutes"] * 60 /
                           max(1, self.settings["poll_interval_s"])) + 5)

    def _init_runtime(self):
        ml = self._history_maxlen()
        for pv in self.pvs:
            self.runtime[pv.name] = PVRuntime(history=deque(maxlen=ml))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        btn_row = QHBoxLayout()
        self.btn_monitor = _btn("▶ Start monitoring")
        self.btn_monitor.setCheckable(True)
        self.btn_monitor.setToolTip(
            "Start/stop the monitoring loop. While running, every PV is polled "
            "at the configured interval, evaluated against its thresholds, and "
            "alerts are sent on committed state changes.")
        self.btn_monitor.clicked.connect(self.toggle_monitoring)
        btn_row.addWidget(self.btn_monitor)
        btn_row.addSpacing(8)

        tips = {
            "Add PV": "Browse the CPVA archiver channel list and add one or "
                      "more channels to monitor.",
            "Edit": "Open the selected PV to edit its name, units, group, "
                    "thresholds and valid range (same as double-clicking it).",
            "Learn selected": "Auto-compute Warning/Alarm thresholds for the "
                              "selected PV(s) from archiver history.",
            "Remove": "Delete the selected PV(s) from the monitor list. Does "
                      "not affect the archiver.",
            "Poll now": "Read every PV once immediately, without waiting for the "
                        "next scheduled poll. Works even while stopped.",
            "Simulate alert": "Inject a fake alarm for the selected PV to test "
                              "that notification channels are wired up "
                              "correctly. No real data is changed.",
            "Groups": "Organize PVs into groups and subgroups: rename, create "
                      "and drag PVs between them in one tree view.",
        }
        for label, slot in (("Add PV", self.add_pv),
                            ("Edit", self.edit_pv),
                            ("Groups", self.manage_groups),
                            ("Learn selected", self.learn_selected),
                            ("Remove", self.remove_pv),
                            ("Poll now", self.poll_now),
                            ("Simulate alert", self.simulate_alert)):
            b = _btn(label, SECONDARY_STYLE)
            b.setToolTip(tips[label])
            b.clicked.connect(slot)
            btn_row.addWidget(b)

        self.btn_sendplot = _btn("Send plot now", SECONDARY_STYLE)
        self.btn_sendplot.setToolTip(
            "Render a trend plot of the selected PV (or all PVs) right now and "
            "push it to the enabled notification channels, independently of any "
            "alert.")
        self.btn_sendplot.clicked.connect(self.send_plot_now)
        btn_row.addWidget(self.btn_sendplot)

        self.btn_settings = _btn("Settings", SECONDARY_STYLE)
        self.btn_settings.setToolTip(
            "Open Settings: notification channels (Teams/Email/Webex), polling "
            "and alert timing, and global valid-range defaults.")
        self.btn_settings.clicked.connect(self.open_settings)
        btn_row.addWidget(self.btn_settings)

        btn_row.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color:#555;")
        btn_row.addWidget(self._status_lbl)
        root.addLayout(btn_row)

        splitter = QSplitter(Qt.Vertical)

        self.model = PVTableModel(self.pvs, self.runtime, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.clicked.connect(self._on_table_clicked)
        self.table.doubleClicked.connect(self._on_table_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.verticalHeader().setVisible(False)
        # Drag a row to reorder; the drop indicator's top edge decides where it
        # lands (drop into another group's block reassigns that group).
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.InternalMove)
        self.table.setDefaultDropAction(Qt.MoveAction)
        self.table.setDragDropOverwriteMode(False)
        self.table.setDropIndicatorShown(True)
        hh = self.table.horizontalHeader()
        # Every column sizes to its longest value (incl. Display name and PV
        # name) so nothing is elided. "Depends on" hosts the rule dropdowns,
        # which ResizeToContents can't measure — its width is set explicitly
        # in _install_dep_combos from the widest dropdown.
        for c in range(len(COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(DEP_COL, QHeaderView.Interactive)
        self.model.modelReset.connect(self._apply_group_spans)
        self.model.modelReset.connect(self._install_dep_combos)
        self._apply_group_spans()
        self._install_dep_combos()
        splitter.addWidget(self.table)

        self.graph = GraphPanel(self)
        self.graph.refresh_combo()
        splitter.addWidget(self.graph)
        splitter.setSizes([300, 360])
        root.addWidget(splitter, 1)

        # The message log lives in its own top-level "Log" tab (added in
        # main.py). It is still owned/written by this widget via _log(); we
        # just don't place it in the monitor layout. Qt reparents it when
        # main.py adds it to the tab bar.
        self.log = LogWidget()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._start_poll)

        self._update_status()
        self._log(f"Loaded {len(self.pvs)} PV(s). Config: {CONFIG_FILE}")

    # --- helpers -------------------------------------------------------
    def _log(self, msg: str):
        ts = datetime.now(api.TZ_PRAGUE).strftime("%H:%M:%S")
        self.log.append_line(f"[{ts}] {msg}")

    def _channel_summary(self) -> str:
        chans = []
        if self.hub.teams_enabled and self.hub.teams.is_configured():
            chans.append("Teams")
        if self.hub.email_enabled and self.hub.email.is_configured():
            chans.append("Email")
        if self.hub.webex_enabled and self.hub.webex.is_configured():
            chans.append("Webex")
        return ", ".join(chans) if chans else "no channels"

    def _update_status(self):
        state = "MONITORING" if self._monitoring else "stopped"
        self._status_lbl.setText(
            f"{state}  ·  {len(self.pvs)} PV(s)  ·  every "
            f"{self.settings['poll_interval_s']}s  ·  {self._channel_summary()}")

    def persist(self):
        save_config({
            "version": 1,
            "settings": self.settings,
            "pvs": [pv.to_dict() for pv in self.pvs],
        })

    def shutdown(self):
        """Called by the main window on close; state is also saved per-change."""
        self.timer.stop()
        self._stop_cmd_listener()
        self.persist()

    def _apply_group_spans(self):
        """Make each (sub)group-header row span the table width from the name
        column on — the On/Show cells stay separate so the header's group-wide
        Show checkbox remains clickable."""
        self.table.clearSpans()
        for r, (kind, _ref) in enumerate(self.model._display):
            if kind in ("header", "subheader"):
                self.table.setSpan(r, COL_NAME, 1, len(COLS) - COL_NAME)

    # --- "Depends on" rule dropdowns ------------------------------------
    def _install_dep_combos(self):
        """(Re)create the per-row rule dropdown in the 'Depends on' column.

        Every PV with dependency rules gets one. 'Automatic (…)' follows
        whichever rule currently matches the dependency values and shows its
        name; picking a rule (or 'Global') pins that threshold set until the
        user changes it again. Model resets destroy index widgets, so this is
        reconnected to modelReset."""
        self._dep_combos = {}
        for r, (kind, ref) in enumerate(self.model._display):
            if kind != "pv" or not (ref.gate_pvs and ref.profiles):
                continue
            pv = ref
            rt = self.runtime.get(pv.name)
            if rt is not None and rt.dep_view is not None and not (
                    -1 <= rt.dep_view < len(pv.profiles)):
                rt.dep_view = None    # rules changed under a stale pin
            combo = _NoWheelComboBox()
            combo.addItem("")         # 'Automatic (…)' — text kept fresh below
            combo.addItem("Global")
            for i, prof in enumerate(pv.profiles):
                combo.addItem(_profile_item_text(pv, i, prof))
            view = rt.dep_view if rt is not None else None
            combo.setCurrentIndex(
                0 if view is None else (1 if view == -1 else view + 2))
            combo.currentIndexChanged.connect(
                lambda idx, name=pv.name: self._on_dep_combo_changed(name, idx))
            self.table.setIndexWidget(self.model.index(r, DEP_COL), combo)
            self._dep_combos[pv.name] = combo
        self._refresh_dep_combos()
        # Width: widest dropdown, but never narrower than the plain-text rows
        # (PVs with a dependency PV but no rules still render text there).
        w = max([c.sizeHint().width() for c in self._dep_combos.values()]
                or [0])
        w = max(w, self.table.sizeHintForColumn(DEP_COL))
        if w:
            self.table.setColumnWidth(DEP_COL, w + 8)
        if self._dep_combos:
            h = max(c.sizeHint().height() for c in self._dep_combos.values())
            vh = self.table.verticalHeader()
            if vh.defaultSectionSize() < h:
                vh.setDefaultSectionSize(h)

    def _active_limits_label(self, pv: PVConfig, rt: Optional[PVRuntime]) -> str:
        """Short name of the threshold set in force, for the 'Automatic (…)'
        item."""
        active = rt.active_profile if rt else None
        if active is None:
            return "Global"
        return ((active.get("label") or "").strip()
                or _profile_rule_text(pv, active) or "rule")

    def _refresh_dep_combos(self):
        """Keep every dropdown's 'Automatic (…)' text and styling in sync with
        the rule currently in force (called after each poll and on pin
        changes)."""
        for pv in self.pvs:
            combo = self._dep_combos.get(pv.name)
            if combo is None:
                continue
            rt = self.runtime.get(pv.name)
            combo.setItemText(
                0, f"Automatic ({self._active_limits_label(pv, rt)})")
            view = rt.dep_view if rt is not None else None
            pinned = view is not None
            color = " color:#0D47A1;" if pinned else ""
            weight = " font-weight:600;" if pinned else ""
            combo.setStyleSheet(
                "QComboBox { padding:1px 6px;" + color + weight + " }")
            combo.setToolTip(
                "Threshold set in force for this PV.\n"
                "Automatic — the first rule whose dependency conditions match "
                "wins; otherwise the Global limits.\n"
                "Pick a rule to pin its limits unconditionally, or pick "
                "Global to force the global limits. Your choice sticks until "
                "you change it.")

    def _on_dep_combo_changed(self, name: str, idx: int):
        rt = self.runtime.get(name)
        pv = next((p for p in self.pvs if p.name == name), None)
        if rt is None or pv is None:
            return
        rt.dep_view = None if idx == 0 else (-1 if idx == 1 else idx - 2)
        rt.active_profile = self._match_profile(pv)
        self._refresh_dep_combos()
        self.model.refresh_all()
        if rt.dep_view is None:
            self._log(f"{pv.display_name}: limits follow the active rule "
                      "(Default).")
        elif rt.dep_view == -1:
            self._log(f"{pv.display_name}: limits pinned to Global.")
        else:
            self._log(f"{pv.display_name}: limits pinned to "
                      f"'{_profile_item_text(pv, rt.dep_view, pv.profiles[rt.dep_view])}'.")

    def _selected_pv(self) -> Optional[PVConfig]:
        rows = self.table.selectionModel().selectedRows()
        for idx in rows:
            pv = self.model.pv_at_row(idx.row())
            if pv is not None:
                return pv
        return None

    def _selected_pvs(self) -> list[PVConfig]:
        rows = sorted(self.table.selectionModel().selectedRows(),
                      key=lambda x: x.row())
        out = [self.model.pv_at_row(i.row()) for i in rows]
        return [pv for pv in out if pv is not None]

    def _valid_range(self, pv: PVConfig):
        """Effective (min, max) sanity range: per-PV override else global default."""
        lo = pv.valid_min if pv.valid_min is not None \
            else self.settings.get("valid_min_default")
        hi = pv.valid_max if pv.valid_max is not None \
            else self.settings.get("valid_max_default")
        return lo, hi

    def _gate_pv_names(self) -> set[str]:
        """Every dependency PV referenced by a monitored PV (may or may not
        itself be in the monitored list)."""
        names: set[str] = set()
        for pv in self.pvs:
            names.update(g for g in pv.gate_pvs if g)
        return names

    def _gate_value(self, name: str) -> Optional[float]:
        """Current value of a gate PV, whether it's monitored or fetched
        alongside the poll."""
        rt = self.runtime.get(name)
        if rt is not None and rt.current_value is not None:
            return rt.current_value
        return self._gate_values.get(name)

    def _match_profile(self, pv: PVConfig) -> Optional[dict]:
        """The conditional profile in force for this PV right now, or None if
        the global thresholds apply.

        Automatic (nothing pinned): the first rule whose conditions match wins.
        A rule pinned in the 'Depends on' dropdown applies unconditionally —
        its thresholds are in force regardless of the dependency values.
        Pinning 'Global' forces the global thresholds regardless of the
        rules."""
        if not pv.gate_pvs or not pv.profiles:
            return None
        rt = self.runtime.get(pv.name)
        view = rt.dep_view if rt is not None else None
        if view is not None:
            if 0 <= view < len(pv.profiles):
                prof = pv.profiles[view]
                if PVConfig.profile_thresholds(prof).is_active():
                    return prof
            return None      # Global pinned (or pinned rule has no limits set)
        gate_values = [self._gate_value(g) for g in pv.gate_pvs]
        return pv.match_profile(gate_values)

    def _active_thresholds(self, pv: PVConfig) -> Thresholds:
        """The threshold set in force for this PV right now: the first matching
        conditional profile's set, else the default set."""
        prof = self._match_profile(pv)
        return pv.profile_thresholds(prof) if prof is not None else pv.thresholds()

    def learn_selected(self):
        pvs = self._selected_pvs()
        if not pvs:
            QMessageBox.information(self, "Learn selected",
                                    "Select one or more PVs first.")
            return
        params = _LearnParamsDialog(self, len(pvs))
        if params.exec() != QDialog.Accepted:
            return
        days, warn_k, alarm_k = params.values()
        self._log(f"Batch learn: {len(pvs)} PV(s), {days} d.")
        self._batch = _BatchLearnController(self, pvs, days, warn_k, alarm_k)
        self._batch.start()

    def _prefetch_channels(self):
        sig = _ChannelsSignals(self)
        sig.done.connect(self._on_channels)
        sig.error.connect(lambda e: self._log(f"Channel prefetch failed: {e}"))
        self._chan_sig = sig
        QThreadPool.globalInstance().start(
            _ChannelsWorker(sig, float(self.settings["http_timeout_s"])))

    def _on_channels(self, channels):
        self._all_channels = channels
        self._log(f"Channel list loaded ({len(channels)} channels).")

    # --- PV management -------------------------------------------------
    def add_pv(self):
        dlg = PVBrowserDialog(self, self._all_channels,
                              float(self.settings["http_timeout_s"]))
        if not self._all_channels and dlg._all:
            self._all_channels = dlg._all
        if dlg.exec() == QDialog.Accepted:
            existing = {p.name for p in self.pvs}
            added = 0
            ml = self._history_maxlen()
            for name in dlg.selected:
                if name in existing:
                    continue
                pv = PVConfig(name=name)        # disabled until learned/set
                self.pvs.append(pv)
                self.runtime[pv.name] = PVRuntime(history=deque(maxlen=ml))
                added += 1
            if added:
                self._recluster()
                self.model.reset()
                self.graph.refresh_combo()
                self.persist()
                self._update_status()
                self._log(f"Added {added} PV(s). Edit to set/learn thresholds, "
                          "then tick 'On'.")

    def edit_pv(self):
        pv = self._selected_pv()
        if not pv:
            QMessageBox.information(self, "Edit PV", "Select a PV first.")
            return
        dlg = PVEditDialog(self, pv)
        if dlg.exec() == QDialog.Accepted:
            self._recluster()
            self.model.reset()
            self.graph.refresh_combo()
            self.persist()
            self._log(f"Updated {pv.display_name}.")

    def _on_table_clicked(self, index):
        """Single click: the eye column toggles graph visibility; clicking a
        group/subgroup header row folds/unfolds it."""
        if not index.isValid():
            return
        if index.column() == COL_SHOW:
            self.model.toggle_show(index.row())
            return
        kind = self.model._display[index.row()][0]
        if kind in ("header", "subheader"):
            self.model.toggle_collapse(index.row())

    def _on_table_double_clicked(self, index):
        """Double-click a threshold cell to edit limits in a popup; any other
        PV cell opens the full PV editor."""
        kind = self.model._display[index.row()][0] if index.isValid() else None
        if kind != "pv" or index.column() == COL_SHOW:
            return   # header rows fold on single click; the eye just toggles
        if index.column() in THR_COLS:
            self.edit_thresholds(self.model.pv_at_row(index.row()))
        else:
            self.edit_pv()

    def edit_thresholds(self, pv: Optional[PVConfig]):
        """Popup limits editor (default set + conditional rules) for one PV.

        Same underlying data as the full PV editor, so edits made here also show
        up there. Dependency/rule changes take effect on the next poll."""
        if pv is None:
            return
        dlg = ThresholdsPopup(self, pv)
        if dlg.exec() == QDialog.Accepted:
            self.model.reset()
            self.persist()
            self._log(f"Updated limits for {pv.display_name}.")

    # --- copy / paste settings ----------------------------------------
    # Fields carried by a full copy/paste: limits, dependency gating and valid
    # range. Identity (name/display_name/group), the enabled flag and
    # learned-history stats stay with each target PV. Partial copies (submenu)
    # carry only a subset of these keys, or a single conditional rule.
    # _GATING_KEYS also includes the Global row (warn/alarm) so pasting
    # dependencies+rules doesn't leave the target's fallback limits stale.
    _COPY_KEYS = ("units", "warn_low", "warn_high", "alarm_low", "alarm_high",
                  "gate_pvs", "profiles", "valid_min", "valid_max")
    _GLOBAL_KEYS = ("warn_low", "warn_high", "alarm_low", "alarm_high")
    _VALID_KEYS = ("valid_min", "valid_max")
    _GATING_KEYS = ("gate_pvs", "profiles", "warn_low", "warn_high",
                    "alarm_low", "alarm_high")

    def _show_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        pv = self.model.pv_at_row(idx.row()) if idx.isValid() else None
        targets = self._selected_pvs()
        menu = QMenu(self)

        act_edit = menu.addAction("Edit…")
        act_edit.setEnabled(pv is not None)
        menu.addSeparator()

        copy_menu = menu.addMenu("Copy settings")
        copy_menu.setEnabled(pv is not None)
        copy_actions = {}   # QAction -> (keys, profile_index)
        if pv is not None:
            copy_actions[copy_menu.addAction("All settings")] = \
                (self._COPY_KEYS, None)
            copy_menu.addSeparator()
            copy_actions[copy_menu.addAction("Global limits (warn/alarm)")] = \
                (self._GLOBAL_KEYS, None)
            copy_actions[copy_menu.addAction("Valid range")] = \
                (self._VALID_KEYS, None)
            act = copy_menu.addAction("Dependencies && all rules (incl. values)")
            act.setEnabled(bool(pv.gate_pvs or pv.profiles))
            copy_actions[act] = (self._GATING_KEYS, None)
            if pv.profiles:
                copy_menu.addSeparator()
                for i, prof in enumerate(pv.profiles):
                    act = copy_menu.addAction(
                        f"Rule: {_profile_item_text(pv, i, prof)}")
                    copy_actions[act] = (None, i)

        paste_label = "Paste settings"
        if self._copied_settings is not None:
            paste_label = (f"Paste {self._copied_desc} from "
                           f"{self._copied_from} → {len(targets)} PV(s)")
        act_paste = menu.addAction(paste_label)
        act_paste.setEnabled(self._copied_settings is not None and bool(targets))
        menu.addSeparator()
        act_remove = menu.addAction("Remove")
        act_remove.setEnabled(bool(targets))

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_edit and pv is not None:
            dlg = PVEditDialog(self, pv)
            if dlg.exec() == QDialog.Accepted:
                self._recluster()
                self.model.reset()
                self.graph.refresh_combo()
                self.persist()
                self._log(f"Updated {pv.display_name}.")
        elif chosen in copy_actions and pv is not None:
            keys, prof_idx = copy_actions[chosen]
            self._copy_pv_settings(pv, keys=keys, profile_index=prof_idx)
        elif chosen == act_paste:
            self._paste_pv_settings(targets)
        elif chosen == act_remove:
            self.remove_pv()

    def _copy_pv_settings(self, pv: PVConfig, keys=None, profile_index=None):
        """Fill the clipboard from ``pv``. Either a set of plain attribute
        ``keys``, or one conditional rule (``profile_index``) together with the
        dependency PVs its conditions are aligned to."""
        d = pv.to_dict()
        if profile_index is not None:
            prof = d["profiles"][profile_index]
            self._copied_settings = {
                "gate_pvs": copy.deepcopy(d["gate_pvs"]),
                "_profile": copy.deepcopy(prof),
            }
            self._copied_desc = (f"rule '{_profile_item_text(pv, profile_index, prof)}'")
        else:
            keys = tuple(keys or self._COPY_KEYS)
            self._copied_settings = {k: copy.deepcopy(d[k]) for k in keys}
            self._copied_desc = {
                self._COPY_KEYS: "all settings",
                self._GLOBAL_KEYS: "global limits",
                self._VALID_KEYS: "valid range",
                self._GATING_KEYS: "dependencies, rules & global values",
            }.get(keys, "settings")
        self._copied_from = pv.display_name
        self._log(f"Copied {self._copied_desc} from {pv.display_name}.")

    def _paste_pv_settings(self, targets: list[PVConfig]):
        if not self._copied_settings or not targets:
            return
        prof = self._copied_settings.get("_profile")
        names = ", ".join(pv.display_name for pv in targets)
        detail = ""
        if prof is not None:
            detail = ("\n\nThe rule is merged into each PV's rule list "
                      "(replacing a same-label rule if present) and the "
                      "dependency PVs are set to the copied ones.")
        if QMessageBox.question(
                self, "Paste settings",
                f"Apply {self._copied_desc} copied from {self._copied_from} "
                f"to {len(targets)} PV(s)?{detail}\n\n{names}") \
                != QMessageBox.Yes:
            return
        for pv in targets:
            for k, v in self._copied_settings.items():
                if k != "_profile":
                    setattr(pv, k, copy.deepcopy(v))
            if prof is not None:
                self._merge_profile(pv, copy.deepcopy(prof))
        self._recluster()
        self.model.reset()
        self.graph.refresh_combo()
        self.persist()
        self._log(f"Pasted {self._copied_desc} from {self._copied_from} onto "
                  f"{len(targets)} PV(s).")

    @staticmethod
    def _merge_profile(pv: PVConfig, prof: dict):
        """Insert one conditional rule: replace the target's rule with the same
        label (if any), otherwise append it."""
        label = (prof.get("label") or "").strip()
        for i, existing in enumerate(pv.profiles):
            if label and (existing.get("label") or "").strip() == label:
                pv.profiles[i] = prof
                return
        pv.profiles.append(prof)

    def remove_pv(self):
        pv = self._selected_pv()
        if not pv:
            return
        if QMessageBox.question(self, "Remove PV",
                                f"Remove {pv.display_name}?") != QMessageBox.Yes:
            return
        self.pvs.remove(pv)
        self.runtime.pop(pv.name, None)
        self.model.reset()
        self.graph.refresh_combo()
        self.persist()
        self._update_status()
        self._log(f"Removed {pv.display_name}.")

    def _recluster(self):
        """Reorder self.pvs so each group's PVs are contiguous, and within a
        group each subgroup's PVs are contiguous too.

        Group/subgroup order follows first appearance; order inside a subgroup
        is preserved. This keeps the header view coherent after edits/drops.
        """
        buckets: dict[str, dict[str, list]] = {}
        order: list[str] = []
        for pv in self.pvs:
            if pv.group not in buckets:
                buckets[pv.group] = {}
                order.append(pv.group)
            sub = buckets[pv.group]
            sub.setdefault(pv.subgroup, []).append(pv)
        self.pvs[:] = [pv for g in order
                       for pvs in buckets[g].values() for pv in pvs]

    def drop_pvs(self, names: list[str], target_disp: int):
        """Move dragged PVs (by name) to a display row; adopt that spot's group.

        target_disp is an index into the model's display rows (the drop
        indicator sits *above* that row). Landing on/above a group header puts
        the PVs at that group's top; landing just below a group's last row keeps
        them in that group.
        """
        moving_names = set(names)
        moving = [p for p in self.pvs if p.name in moving_names]  # original order
        if not moving:
            return
        disp = self.model._display
        n = len(disp)
        cur = disp[target_disp] if 0 <= target_disp < n else None

        anchor = None          # insert immediately before this PV
        after = None           # ...or immediately after this PV
        if cur is None:                                   # dropped past the end
            group = self.pvs[-1].group if self.pvs else ""
            subgroup = self.pvs[-1].subgroup if self.pvs else ""
        elif cur[0] == "pv":                              # before a PV row
            anchor = cur[1]
            group, subgroup = anchor.group, anchor.subgroup
        elif cur[0] == "subheader":                       # top of that subgroup
            group, subgroup = cur[1]
            nxt = disp[target_disp + 1] if target_disp + 1 < n else None
            anchor = nxt[1] if nxt and nxt[0] == "pv" else None
        else:                                             # on a group header
            prev = disp[target_disp - 1] if target_disp - 1 >= 0 else None
            if prev is None or prev[0] != "pv":           # top of the first group
                group, subgroup = cur[1], ""
                nxt = disp[target_disp + 1] if target_disp + 1 < n else None
                anchor = nxt[1] if nxt and nxt[0] == "pv" else None
            else:                                         # end of the prior group
                after = prev[1]
                group, subgroup = after.group, after.subgroup

        reduced = [p for p in self.pvs if p.name not in moving_names]
        for p in moving:
            p.group = group
            p.subgroup = subgroup

        if anchor is not None and anchor.name not in moving_names:
            idx = reduced.index(anchor)
        elif after is not None and after.name not in moving_names:
            idx = reduced.index(after) + 1
        else:                                             # end of the target group
            idxs = [i for i, p in enumerate(reduced)
                    if p.group == group and p.subgroup == subgroup] \
                or [i for i, p in enumerate(reduced) if p.group == group]
            idx = (idxs[-1] + 1) if idxs else len(reduced)

        reduced[idx:idx] = moving
        self.pvs[:] = reduced
        self._recluster()
        self.model.reset()
        self.graph.refresh_combo()
        self.persist()

    _CMD_KEYS = ("webex_enabled", "webex_mode", "webex_bot_token", "webex_rooms",
                 "webex_commands_enabled", "webex_command_poll_s",
                 "webex_command_allowlist")

    def _cmd_settings_snapshot(self) -> str:
        return json.dumps({k: self.settings.get(k) for k in self._CMD_KEYS},
                          sort_keys=True, default=str)

    def open_settings(self):
        before = self._cmd_settings_snapshot()
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.hub = NotificationHub.from_settings(self.settings)
            self.evaluator = AlertEvaluator(self._eval_config())
            ml = self._history_maxlen()
            for rt in self.runtime.values():
                if rt.history.maxlen != ml:
                    rt.history = deque(rt.history, maxlen=ml)
            if self._monitoring:
                self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            # Only restart the Webex command listener when its own settings
            # changed (or it isn't running) — a needless restart re-primes and
            # briefly drops commands for no reason.
            if self._cmd_settings_snapshot() != before or self._cmd_timer is None:
                self._start_cmd_listener()
            self.persist()
            self._update_status()
            self._log("Settings saved.")

    # --- monitoring loop ----------------------------------------------
    def toggle_monitoring(self, on: bool):
        # Any explicit start/stop cancels a pending timed auto-resume.
        self._cancel_resume()
        self._monitoring = on
        self.btn_monitor.setChecked(on)
        self.btn_monitor.setText("⏹ Stop monitoring" if on else "▶ Start monitoring")
        self.btn_monitor.setStyleSheet(STOP_BUTTON_STYLE if on else BUTTON_STYLE)
        if on:
            self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            self.timer.start()
            self._log("Monitoring started.")
            self._backfill_history()
            self._start_poll()
        else:
            self.timer.stop()
            self._poll_gen += 1            # discard any in-flight result
            self._log("Monitoring stopped.")
        self._update_status()

    def _cancel_resume(self):
        if self._resume_timer is not None:
            self._resume_timer.stop()
            self._resume_timer = None
        self._resume_at_ns = 0

    def pause_monitoring(self, hours: float):
        """Stop monitoring and auto-resume after `hours` (used by /stop <h>)."""
        self.toggle_monitoring(False)     # this clears any previous resume timer
        ms = max(1, int(hours * 3600 * 1000))
        self._resume_at_ns = api.now_ns() + int(hours * 3600 * 1e9)
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._auto_resume)
        self._resume_timer.start(ms)
        until = api.ns_to_prague(self._resume_at_ns).strftime("%Y-%m-%d %H:%M")
        self._log(f"Monitoring paused for {hours:g} h (auto-resume {until}).")

    def _auto_resume(self):
        self._resume_timer = None
        self._resume_at_ns = 0
        self._log("Auto-resume timer fired.")
        self.toggle_monitoring(True)
        if self.hub.webex.can_listen():
            self._reply("▶ Monitoring auto-resumed (scheduled /stop elapsed).")

    def manage_groups(self):
        dlg = GroupsDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        assignments = dlg.result_assignments()
        by_name = {p.name: p for p in self.pvs}
        new_order: list[PVConfig] = []
        for name, group, subgroup in assignments:
            pv = by_name.pop(name, None)
            if pv is None:
                continue
            pv.group = group
            pv.subgroup = subgroup
            new_order.append(pv)
        new_order += list(by_name.values())   # safety net: never lose a PV
        self.pvs[:] = new_order
        self._recluster()
        self.model.reset()
        self.graph.refresh_combo()
        self.persist()
        self._log("Groups updated.")

    def poll_now(self):
        self._log("Manual poll.")
        self._start_poll()

    # --- graph history backfill ----------------------------------------
    def _backfill_history(self):
        """Pre-fill each PV's history with archive data covering the graph
        window, so the plot shows the whole window right away instead of only
        samples collected since monitoring started."""
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        end = api.now_ns()
        # Cover the whole kept history, not just the default view, so zooming
        # or panning back past the graph window still finds data.
        minutes = max(float(self.settings["graph_window_minutes"]),
                      float(self.settings["history_minutes"]))
        start = end - int(minutes * 60e9)
        ranges = {pv.name: self._valid_range(pv) for pv in self.pvs}
        sig = _BackfillSignals(self)
        sig.log.connect(self._log)
        sig.done.connect(self._on_backfill)
        self._backfill_sig = sig           # keep signals alive while running
        QThreadPool.globalInstance().start(_BackfillWorker(
            sig, names, start, end, float(self.settings["http_timeout_s"]),
            ranges, self._history_maxlen()))
        self._log("Fetching archive history for the graph window…")

    def _on_backfill(self, results: dict):
        filled = 0
        for name, pts in results.items():
            rt = self.runtime.get(name)
            if rt is None or not pts:
                continue
            # Only add points older than what live polling has produced, so
            # re-starting monitoring never duplicates samples.
            oldest_live = rt.history[0][0] if rt.history else None
            older = [p for p in pts
                     if oldest_live is None or p[0] < oldest_live]
            if not older:
                continue
            rt.history = deque(older + list(rt.history),
                               maxlen=rt.history.maxlen)
            filled += 1
        if filled:
            self._log(f"Backfilled graph history for {filled} PV(s).")
            self.graph.redraw()

    def _start_poll(self):
        monitored = {pv.name for pv in self.pvs}
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        # If the previous pass is still fetching, let it finish instead of
        # invalidating it — otherwise a pass slower than the poll interval
        # means no result ever lands and the table stays on "no data".
        if self._poll_inflight:
            self._log("Previous poll still fetching — skipping this tick "
                      "(raise Concurrent fetches or the poll interval).")
            return
        # Also fetch any gate PV that isn't already monitored, so its value is
        # available this pass to switch state-dependent thresholds.
        names += [g for g in sorted(self._gate_pv_names()) if g not in monitored]
        ranges = {pv.name: self._valid_range(pv) for pv in self.pvs}
        self._poll_gen += 1
        gen = self._poll_gen
        self._poll_inflight = True
        sig = _PollSignals(self)
        sig.done.connect(lambda res, g=gen: self._poll_done(res, g))
        self._poll_sig = sig
        QThreadPool.globalInstance().start(
            _PollWorker(sig, names, dict(self.settings), ranges))

    def _poll_done(self, results: dict, gen: int):
        self._poll_inflight = False
        if gen == self._poll_gen:      # stale results (stop/restart) are dropped
            self._on_poll(results)

    def _on_poll(self, results: dict):
        now = api.now_ns()
        # Refresh gate-PV values first so threshold switching below sees this
        # pass's data (a gate PV may not itself be in the monitored list).
        for g in self._gate_pv_names():
            res = results.get(g)
            if res is not None:
                self._gate_values[g] = res[0]
        for pv in self.pvs:
            res = results.get(pv.name)
            if res is None:
                continue
            val, units, last_ts, err, rejected, raw_val = res
            rt = self.runtime[pv.name]
            prev_rej = rt.rejected_count
            rt.current_value = val
            rt.raw_value = raw_val
            rt.current_units = units or rt.current_units
            rt.last_update_ns = last_ts or now
            rt.last_error = err
            rt.rejected_count = rejected
            rt.bad_data = (val is None and rejected > 0)
            if rejected and not prev_rej:
                self._log(f"{pv.display_name}: {err} — treating as sensor error.")
            elif prev_rej and not rejected:
                self._log(f"{pv.display_name}: readings back within valid range.")
            if val is not None:
                rt.history.append((rt.last_update_ns, val))
            rt.active_profile = self._match_profile(pv)
            thr = (pv.profile_thresholds(rt.active_profile)
                   if rt.active_profile is not None else pv.thresholds())
            if pv.enabled and thr.is_active():
                scale, worsening = self._trend_cooldown_scale(rt, thr, now)
                note = self.evaluator.evaluate(rt.alert, val, thr, now,
                                               cooldown_scale=scale)
                if note is not None:
                    if worsening and note.kind == "reminder":
                        note.reason += " — worsening"
                    self._dispatch_alert(pv, rt, note)
            if rt.alert.level == AlertLevel.OK:
                rt.notify_status = ""      # episode over — clear the status cell
                rt.notify_error = ""
        self._check_data_watchdog(results)
        self.model.refresh_all()
        self._refresh_dep_combos()
        self.graph.redraw()

    def _check_data_watchdog(self, results: dict):
        """Alert once when every monitored PV stops getting data (fetch errors
        across the board — an archiver/network outage), and once more when it
        recovers. Distinct from a single PV's own no-data/threshold alerts."""
        if not self.settings.get("data_watchdog_enabled", True) or not self.pvs:
            return

        def _is_conn_failure(res) -> bool:
            if res is None:
                return True
            val, _units, _ts, err, rejected, _raw = res
            return val is None and rejected == 0 and bool(err)

        all_failed = all(_is_conn_failure(results.get(pv.name)) for pv in self.pvs)
        if all_failed:
            self._watchdog_fail_streak += 1
            threshold = max(1, int(self.settings.get("data_watchdog_fail_polls", 2)))
            if not self._watchdog_bad and self._watchdog_fail_streak >= threshold:
                self._watchdog_bad = True
                self._send_watchdog_alert(
                    AlertLevel.ALARM,
                    "No data received from any monitored PV for "
                    f"{self._watchdog_fail_streak} consecutive polls.")
        else:
            self._watchdog_fail_streak = 0
            if self._watchdog_bad:
                self._watchdog_bad = False
                self._send_watchdog_alert(AlertLevel.OK, "Data flow restored.")

    def _send_watchdog_alert(self, level: AlertLevel, reason: str):
        prev = AlertLevel.ALARM if level == AlertLevel.OK else AlertLevel.OK
        self._log(f"WATCHDOG: {reason}")
        payload = AlertPayload(
            level=level, prev_level=prev,
            pv_name="System", display_name="Data connection",
            value=0.0, units="", reason=reason,
            timestamp_str=api.ns_to_prague_str(api.now_ns()), kind="transition")
        self._launch_alert_worker(payload, Thresholds(), tag="watchdog",
                                  render_plot=False)

    def _trend_cooldown_scale(self, rt: PVRuntime, thr: Thresholds,
                              now: int) -> tuple[float, bool]:
        """Adaptive re-notify pacing for an already-alarming PV, from its recent
        value trend. Returns (cooldown_scale, worsening).

          - improving (value moving back toward the crossed bound) -> slow the
            reminders down (scale = slowdown_factor).
          - worsening (value drifting further past the bound)      -> speed them
            up (scale = 1 / speedup_factor) and flag worsening=True.
          - flat / feature off / not yet alarming                  -> scale 1.0.

        Trend direction is mapped to improving/worsening by which side alarmed:
        a high-side alarm improves as the value falls, a low-side alarm improves
        as it rises.
        """
        s = self.settings
        if not s.get("trend_adaptive_enabled", True) \
                or rt.alert.level == AlertLevel.OK:
            return 1.0, False

        trend = classify_trend(
            rt.history, now,
            float(s.get("trend_lookback_minutes", 10.0)) * 60.0,
            5, float(s.get("trend_flat_frac", 0.02)))
        if trend == Trend.FLAT:
            return 1.0, False

        high_side = thr.warn_high is not None or thr.alarm_high is not None
        low_side = thr.warn_low is not None or thr.alarm_low is not None
        # If both sides are configured, decide by which bound the value is past.
        if high_side and low_side:
            val = rt.current_value
            hi = thr.alarm_high if thr.alarm_high is not None else thr.warn_high
            high_side = val is not None and hi is not None and val >= hi
            low_side = not high_side

        if high_side:
            improving = trend == Trend.FALLING
        elif low_side:
            improving = trend == Trend.RISING
        else:
            return 1.0, False

        if improving:
            return float(s.get("trend_slowdown_factor", 2.0)), False
        speedup = float(s.get("trend_speedup_factor", 2.0))
        return (1.0 / speedup if speedup > 0 else 1.0), True

    def _dispatch_alert(self, pv: PVConfig, rt: PVRuntime, note):
        self._log(f"ALERT {pv.display_name}: {note.prev_level.label}→"
                  f"{note.level.label} ({note.reason})")
        payload = AlertPayload(
            level=note.level, prev_level=note.prev_level,
            pv_name=pv.name, display_name=pv.display_name,
            value=note.value, units=rt.current_units or pv.units,
            reason=note.reason,
            timestamp_str=api.ns_to_prague_str(rt.last_update_ns or api.now_ns()),
            kind=note.kind)
        # Tag the worker with the PV name so its result updates this PV's
        # "Alarm status" cell (recoveries to OK aren't tracked there).
        if note.level != AlertLevel.OK:
            rt.notify_status = "sending"
            rt.notify_error = ""
        started = self._launch_alert_worker(
            payload, self._active_thresholds(pv), tag=pv.name,
            valid_range=self._valid_range(pv))
        if not started and note.level != AlertLevel.OK:
            rt.notify_status = "failed"
            rt.notify_error = "no notification channel configured"

    def _launch_alert_worker(self, payload: AlertPayload, thr: Thresholds, tag: str,
                             valid_range=(None, None), render_plot: bool = True) -> bool:
        """Start the render+dispatch worker. Returns True if a worker was
        launched, False if there is nothing to send it to."""
        if not self.hub.is_any_configured():
            self._log("  No notification channel configured (see Settings).")
            if tag == "manual":
                self.btn_sendplot.setEnabled(True)
            return False
        hours = float(self.settings.get("alert_plot_hours", 12)) if render_plot else 0.0
        timeout = float(self.settings["http_timeout_s"])
        vmin, vmax = valid_range
        sig = _AlertSignals(self)
        sig.done.connect(self._on_alert_result)
        # Each dispatch keeps its own signals object alive via the worker, so
        # concurrent alerts (several PVs tripping at once) don't clobber one
        # another; this attribute is just a convenience handle to the latest.
        self._alert_sig = sig
        QThreadPool.globalInstance().start(
            _AlertWorker(sig, self.hub, payload, thr, hours, timeout, tag,
                         vmin, vmax))
        return True

    def _on_alert_result(self, result):
        tag, errors, had_png = result
        for ch, err in errors.items():
            self._log(f"  {ch} send failed: {err}")
        if tag == "manual":
            self.btn_sendplot.setEnabled(True)
            if not errors:
                extra = "" if had_png else " (no data for plot — text only)"
                self._log(f"Plot sent.{extra}")
            return
        # Auto alert: tag is the PV name — update its "Alarm status" cell.
        rt = self.runtime.get(tag)
        if rt is not None and rt.alert.level != AlertLevel.OK:
            if errors:
                rt.notify_status = "failed"
                rt.notify_error = "; ".join(f"{c}: {e}" for c, e in errors.items())
            else:
                rt.notify_status = "sent"
                rt.notify_error = ""
            self.model.refresh_all()

    # --- send plot now -------------------------------------------------
    def _send_plot_for(self, pv: PVConfig, tag: str):
        rt = self.runtime.get(pv.name)
        value = rt.current_value if rt and rt.current_value is not None else 0.0
        units = (rt.current_units if rt and rt.current_units else pv.units) or ""
        level = rt.alert.level if rt else AlertLevel.OK
        payload = AlertPayload(
            level=level, prev_level=level, pv_name=pv.name,
            display_name=pv.display_name, value=value, units=units,
            reason="Manual plot request",
            timestamp_str=api.ns_to_prague_str(api.now_ns()), kind="manual")
        self._launch_alert_worker(payload, self._active_thresholds(pv), tag=tag,
                                  valid_range=self._valid_range(pv))

    def send_plot_now(self):
        pvs = self._selected_pvs()
        if not pvs:
            QMessageBox.information(self, "Send plot", "Select a PV first.")
            return
        self.btn_sendplot.setEnabled(False)
        names = ", ".join(pv.display_name for pv in pvs)
        self._log(f"Sending plot for {names}…")
        # One worker per selected PV so every selection is sent (not just the
        # first). The button re-enables when the first worker reports back.
        for pv in pvs:
            self._send_plot_for(pv, tag="manual")

    # --- Webex two-way command listener -------------------------------
    def _start_cmd_listener(self):
        self._stop_cmd_listener()
        if not (self.settings.get("webex_commands_enabled")
                and self.hub.webex.can_listen()):
            return
        self._cmd_primed = False
        self._cmd_last_id = None
        self._cmd_bot_id = None
        self._cmd_poll_inflight = False
        self._cmd_backoff_until_ns = 0
        self._resolve_bot_id()
        poll_s = max(1, int(self.settings.get("webex_command_poll_s", 5)))
        self._cmd_timer = QTimer(self)
        self._cmd_timer.timeout.connect(self._poll_commands)
        self._cmd_timer.setInterval(poll_s * 1000)
        self._cmd_timer.start()
        self._poll_commands()
        self._log(f"Webex command listener on (every {poll_s}s).")

    def _stop_cmd_listener(self):
        if self._cmd_timer is not None:
            self._cmd_timer.stop()
            self._cmd_timer = None
        self._cmd_bot_id = None
        self._cmd_poll_inflight = False
        self._cmd_bot_id_inflight = False
        # Bump so any response from a poll/resolve dispatched before this
        # stop (still in flight in the thread pool, uncancellable) is
        # recognized as stale and discarded instead of being processed as
        # a fresh command — see _on_commands / _on_bot_id.
        self._cmd_gen += 1

    def _resolve_bot_id(self):
        """Fetch the bot's own personId off the UI thread (blocking HTTP call)."""
        if self._cmd_bot_id_inflight:
            return
        self._cmd_bot_id_inflight = True
        gen = self._cmd_gen
        sig = _MeIdSignals(self)
        sig.done.connect(lambda bot_id, g=gen: self._on_bot_id(bot_id, g))
        self._me_id_sig = sig
        QThreadPool.globalInstance().start(_MeIdWorker(sig, self.hub.webex))

    def _on_bot_id(self, bot_id, gen):
        if gen != self._cmd_gen:
            return   # listener was restarted/stopped since this request was sent
        self._cmd_bot_id_inflight = False
        self._cmd_bot_id = bot_id
        if not bot_id:
            self._log("⚠ Failed to get bot's personId; will retry on next poll. "
                      "(Own-message IDs are still filtered as a backstop.)")

    def _poll_commands(self):
        if not self.hub.webex.can_listen():
            return
        if api.now_ns() < self._cmd_backoff_until_ns:
            return   # rate-limited (HTTP 429) — waiting out Retry-After
        if self._cmd_poll_inflight:
            # Watchdog: a poll worker that never reports back (dropped queued
            # signal, thread-pool starvation, or a network stall around a
            # screen lock / WiFi power-save) would wedge this flag True forever
            # and silently kill the command listener — while alerts, which use
            # a generation counter instead of this flag, keep working. That is
            # exactly the "notifies but ignores /list, /stop" failure. If the
            # in-flight poll has outlived any plausible completion time, treat
            # it as lost, discard its result (bump gen), and let a fresh poll
            # proceed so the listener self-heals without an app restart.
            timeout = float(self.settings.get("http_timeout_s", 10.0))
            poll_s = max(1, int(self.settings.get("webex_command_poll_s", 5)))
            max_wait_ns = int(max(poll_s * 5, timeout * 2 + 5) * 1e9)
            if api.now_ns() - self._cmd_poll_started_ns < max_wait_ns:
                return   # previous poll still plausibly running — never overlap
            self._log("⚠ Webex command poll stalled — restarting it "
                      "(listener self-healing).")
            self._cmd_gen += 1   # discard the zombie poll's result when it lands
        if not self._cmd_bot_id:
            self._resolve_bot_id()   # keep retrying until it resolves, off the UI thread
        self._cmd_poll_inflight = True
        self._cmd_poll_started_ns = api.now_ns()
        gen = self._cmd_gen
        sig = _CmdPollSignals(self)
        sig.done.connect(lambda result, g=gen: self._on_commands(result, g))
        self._cmd_sig = sig
        QThreadPool.globalInstance().start(
            _CmdPollWorker(sig, self.hub.webex, self._cmd_last_id))

    def _on_commands(self, result, gen):
        if gen != self._cmd_gen:
            return   # listener was restarted/stopped since this poll was sent — discard
        self._cmd_poll_inflight = False
        new_items, newest_id = result
        # Surface poll failures (bad token, bot not in room, network, 403…)
        # in the Log tab — but only when the error changes, to avoid spamming
        # one line every poll interval.
        err = self.hub.webex.last_error
        if err and err != self._cmd_last_logged_error:
            self._log(f"⚠ Webex command poll failed: {err}")
            self._cmd_last_logged_error = err
        elif not err:
            self._cmd_last_logged_error = ""
        ra = getattr(self.hub.webex, "retry_after_s", 0.0)
        if ra:
            self._cmd_backoff_until_ns = api.now_ns() + int(ra * 1e9)
            self.hub.webex.retry_after_s = 0.0
        if not self._cmd_primed:
            # Establish a baseline WITHOUT processing backlog. Only prime on a
            # *successful* poll: priming on a failed one (newest_id=None) would
            # leave last_id=None, so the next poll would treat the whole fetched
            # backlog as "new" and re-answer every old command — this is what
            # produced the duplicate command-list messages after a restart.
            if err:
                return   # retry priming on the next poll; no baseline yet
            self._cmd_last_id = newest_id
            self._cmd_primed = True
            return
        if newest_id:
            self._cmd_last_id = newest_id
        allow = [e.lower() for e in self.settings.get("webex_command_allowlist", [])]
        for it in new_items:
            mid = it.get("id")
            # Belt-and-braces: never act on a message id twice, even if a
            # listener restart (e.g. after a Settings save, which rebuilds the
            # hub and clears its own-message dedup) makes it reappear.
            if mid:
                if mid in self._cmd_handled_ids:
                    continue
                self._cmd_handled_ids.append(mid)
            if self._cmd_bot_id and it.get("personId") == self._cmd_bot_id:
                continue
            if self.hub.webex.is_own_message(it.get("id")):
                continue
            text = (it.get("text") or "").strip()
            if "/" in text:
                if not text.startswith("/"):
                    text = text[text.index("/"):]   # strip a leading @mention
            else:
                # Be forgiving: a bare "help"/"?"/"commands" (no slash) is
                # treated as /help. Anything else without a slash is ignored
                # so the bot stays quiet during normal conversation.
                low = text.lower()
                if low in ("help", "?", "commands") or low.endswith(" help"):
                    text = "/help"
                else:
                    continue
            email = (it.get("personEmail") or "").lower()
            if allow and email not in allow:
                self._reply(f"⛔ Sorry, {email} is not allowed to command me.")
                continue
            self._handle_command(text, email)

    def _reply(self, markdown: str):
        first = markdown.splitlines()[0] if markdown else ""
        self._log(f"→ Webex: {first}")
        if self.hub.webex.can_listen():
            QThreadPool.globalInstance().start(
                _TextReplyWorker(self.hub.webex, markdown))

    def _find_pv(self, query: str):
        """Return (pv, '') on a unique match, else (None, reason)."""
        q = query.strip().lower()
        if not q:
            return None, "missing PV name"
        exact = [p for p in self.pvs
                 if p.display_name.lower() == q or p.name.lower() == q]
        if exact:
            return exact[0], ""
        matches = [p for p in self.pvs
                   if q in p.display_name.lower() or q in p.name.lower()]
        if len(matches) == 1:
            return matches[0], ""
        if not matches:
            return None, f"no PV matches '{query}'"
        names = ", ".join(p.display_name for p in matches[:8])
        return None, f"'{query}' is ambiguous: {names}"

    def _handle_command(self, text: str, email: str):
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]
        self._log(f"Webex cmd from {email}: {text}")
        try:
            if cmd in ("/help", "/?"):
                self._reply(self._cmd_help())
            elif cmd == "/list":
                if not self.pvs:
                    self._reply("No PVs configured.")
                else:
                    self._reply("**PVs:**\n" + "\n".join(
                        f"- {p.display_name}" for p in self.pvs))
            elif cmd == "/status":
                self._reply(self._cmd_status())
            elif cmd == "/alarms":
                self._reply(self._cmd_alarms())
            elif cmd == "/start":
                self.toggle_monitoring(True)
                self._reply("▶ Monitoring started.")
            elif cmd == "/stop":
                if args:
                    hours = float(args[0])
                    if hours <= 0:
                        raise ValueError("hours must be > 0")
                    self.pause_monitoring(hours)
                    until = api.ns_to_prague(self._resume_at_ns).strftime(
                        "%Y-%m-%d %H:%M")
                    self._reply(f"⏸ Monitoring paused for {hours:g} h — "
                                f"auto-resume at {until}. Send /start to resume now.")
                else:
                    self.toggle_monitoring(False)
                    self._reply("⏹ Monitoring stopped (no auto-resume).")
            elif cmd == "/window":
                mins = int(float(args[0]))
                self.settings["graph_window_minutes"] = mins
                self.graph.redraw()
                self.persist()
                self._reply(f"Graph window set to {mins} min.")
            elif cmd == "/yaxis":
                if args and args[0].lower() == "auto":
                    self.graph.set_yaxis(None, None)
                    self._reply("Y axis: autoscale.")
                else:
                    lo, hi = float(args[0]), float(args[1])
                    self.graph.set_yaxis(lo, hi)
                    self._reply(f"Y axis set to [{lo:g}, {hi:g}].")
            elif cmd == "/graph":
                target = " ".join(args).strip()
                if target.lower() in ("all", ""):
                    self.graph.select_pv(None)
                    self._reply("Graph: all PVs.")
                else:
                    pv, err = self._find_pv(target)
                    if not pv:
                        self._reply(f"⚠ {err}")
                    else:
                        self.graph.select_pv(pv.name)
                        self._reply(f"Graph: {pv.display_name}.")
            elif cmd == "/plot":
                pv, err = self._find_pv(" ".join(args))
                if not pv:
                    self._reply(f"⚠ {err}")
                else:
                    self._send_plot_for(pv, tag="cmd")
                    self._reply(f"📈 Sending plot for {pv.display_name}…")
            elif cmd == "/datawatchdog":
                if args and args[0].lower() in ("on", "off"):
                    enabled = args[0].lower() == "on"
                    self.settings["data_watchdog_enabled"] = enabled
                    self.persist()
                    self._reply(f"Data watchdog {'enabled' if enabled else 'disabled'}.")
                else:
                    state = "on" if self.settings.get(
                        "data_watchdog_enabled", True) else "off"
                    self._reply(f"Data watchdog is {state}. "
                                "Use `/datawatchdog on|off` to change.")
            elif cmd in ("/enable", "/disable"):
                pv, err = self._find_pv(" ".join(args))
                if not pv:
                    self._reply(f"⚠ {err}")
                else:
                    pv.enabled = (cmd == "/enable")
                    self.model.refresh_all()
                    self.persist()
                    self._reply(f"{pv.display_name} alerting "
                                f"{'enabled' if pv.enabled else 'disabled'}.")
            else:
                self._reply(f"❓ Unknown command {cmd}. Try /help.")
        except (IndexError, ValueError):
            self._reply(f"⚠ Bad arguments for {cmd}. Try /help.")

    def _cmd_help(self) -> str:
        return (
            "**PV Monitor commands:**\n"
            "- `/status` — all PVs + values + state\n"
            "- `/alarms` — only PVs currently in warning/alarm\n"
            "- `/list` — list configured PVs\n"
            "- `/plot <pv>` — send current plot of a PV\n"
            "- `/start` — monitoring on\n"
            "- `/stop [hours]` — monitoring off; with hours, auto-resume later "
            "(e.g. `/stop 10`)\n"
            "- `/enable <pv>` `/disable <pv>` — alerting per PV\n"
            "- `/datawatchdog on|off` — toggle the 'no data at all' alert "
            "(no args: show current state)\n"
            "- `/graph <pv|all>` — set the live graph\n"
            "- `/window <minutes>` — graph time window\n"
            "- `/yaxis <lo> <hi>` | `/yaxis auto` — graph Y range")

    def _cmd_status(self) -> str:
        if not self.pvs:
            return "No PVs configured."
        lines = []
        for p in self.pvs:
            rt = self.runtime.get(p.name)
            bad = bool(rt and rt.bad_data and rt.current_value is None and p.enabled)
            if rt and rt.current_value is not None:
                val = _fmt(rt.current_value)
            elif bad and rt.raw_value is not None:
                val = _fmt(rt.raw_value)
            elif rt:
                val = "–"
            else:
                val = "–"
            units = (rt.current_units if rt and rt.current_units else p.units) or ""
            if not p.enabled:
                state = "off"
            elif bad:
                state = "bad data"
            elif rt and rt.display_level() is not None:
                state = rt.display_level().label.lower()
            else:
                state = "no data"
            lines.append(f"- **{p.display_name}**: {val} {units} [{state}]")
        mon = "MONITORING" if self._monitoring else "stopped"
        return f"**Status ({mon}):**\n" + "\n".join(lines)

    def _cmd_alarms(self) -> str:
        lines = []
        for p in self.pvs:
            if not p.enabled:
                continue
            rt = self.runtime.get(p.name)
            level = rt.display_level() if rt else None
            if level not in (AlertLevel.WARNING, AlertLevel.ALARM):
                continue
            val = _fmt(rt.current_value) if rt else "–"
            units = (rt.current_units if rt and rt.current_units else p.units) or ""
            lines.append(f"- **{p.display_name}**: {val} {units} [{level.label.lower()}]")
        if not lines:
            return "✅ No PVs currently in warning/alarm."
        return "**Current alarms:**\n" + "\n".join(lines)

    # --- simulate ------------------------------------------------------
    def simulate_alert(self):
        pv = self._selected_pv()
        if not pv:
            QMessageBox.information(self, "Simulate", "Select a PV first.")
            return
        if not self._active_thresholds(pv).is_active():
            QMessageBox.information(
                self, "Simulate",
                "This PV has no thresholds set. Edit/learn limits first.")
            return
        thr = self._active_thresholds(pv)
        # Build a synthetic OK -> ALARM -> OK sequence from the thresholds.
        hi = thr.alarm_high if thr.alarm_high is not None else None
        lo = thr.alarm_low if thr.alarm_low is not None else None
        mid = thr.warn_low if thr.warn_low is not None else (
            thr.warn_high if thr.warn_high is not None else 0.0)
        if thr.warn_low is not None and thr.warn_high is not None:
            mid = (thr.warn_low + thr.warn_high) / 2
        excursion = (hi + 1) if hi is not None else (lo - 1 if lo is not None else mid + 1)
        seq = [mid, excursion, excursion, excursion, mid, mid]
        self._log(f"Simulating alert on {pv.display_name} (bypassing CPVA)…")
        self._sim_state = AlertState()
        # The simulation runs in seconds, so skip the settle wait — it would
        # hold every simulated alert for minutes and nothing would be sent.
        sim_cfg = self._eval_config()
        sim_cfg.settle_minutes = 0.0
        sim_cfg.stable_seconds = 0.0   # same reason — the sim runs in seconds
        self._sim_eval = AlertEvaluator(sim_cfg)
        self._sim_pv = pv
        self._sim_seq = list(seq)
        if self._sim_timer is None:
            self._sim_timer = QTimer(self)
            self._sim_timer.timeout.connect(self._sim_step)
        self._sim_timer.start(900)
        self._sim_step()

    def _sim_step(self):
        if not self._sim_seq:
            self._sim_timer.stop()
            self._log("Simulation finished.")
            return
        v = self._sim_seq.pop(0)
        pv = self._sim_pv
        rt = self.runtime[pv.name]
        rt.current_value = v
        rt.last_update_ns = api.now_ns()
        rt.history.append((rt.last_update_ns, v))
        note = self._sim_eval.evaluate(self._sim_state, v, self._active_thresholds(pv),
                                       rt.last_update_ns)
        # Reflect simulated level in the table without touching the real alert state.
        rt.alert.level = self._sim_state.level
        rt.alert.first_notified_ns = self._sim_state.first_notified_ns
        if note is not None:
            self._dispatch_alert(pv, rt, note)
        self.model.refresh_all()
        self.graph.redraw()
