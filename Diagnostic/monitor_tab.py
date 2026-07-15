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
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel, QByteArray, QMimeData, QModelIndex, QObject, QRunnable,
    Qt, QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QProgressDialog, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import cpva_api as api
from alerting import (
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EvalConfig,
    NotificationHub, Thresholds, describe_reason,
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
    "avg_last_n": 25,
    "sample_window_s": 60,
    "http_timeout_s": 10.0,
    "renotify_cooldown_minutes": 30,
    # Settle ("grace") window: when a dependency/gate PV turns on (rises 0 -> 1),
    # every monitored PV that depends on it holds its alerts for this many
    # minutes, so the transient while chillers etc. re-stabilise to their new
    # setpoint doesn't fire a false alarm. Per-gate overrides live in
    # ``settle_minutes`` (keyed by gate PV name); ``settle_default_minutes``
    # applies to any gate PV without its own entry. 0 = no hold.
    "settle_default_minutes": 0,
    "settle_minutes": {
        "L3-SIS-KEY:HighPowerStatus": 15,
        "L3-SIS-KEY:LowPowerStatus": 15,
    },
    "recovery_notify": True,
    "debounce_count": 2,
    "hysteresis_frac": 0.05,
    "history_minutes": 720,
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
    "webex_command_poll_s": 1,
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
    # Detach nested containers from the shared DEFAULT_SETTINGS templates so a
    # config that omits them can't mutate the defaults in place.
    settings["settle_minutes"] = dict(settings.get("settle_minutes") or {})
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
    enabled: bool = False
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
            "units": self.units, "group": self.group, "enabled": self.enabled,
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
            enabled=bool(d.get("enabled", False)),
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
    # Conditional profile in force at the last poll (None = default thresholds).
    active_profile: Optional[dict] = None
    # While > now, this PV is inside a settle/grace window (a gate PV it depends
    # on just turned on) and its alerts are held. 0 = not settling.
    settling_until_ns: int = 0
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
    """Return (avg_or_None, units, last_ts_ns, n_rejected) from raw CPVA samples.

    Readings outside [vmin, vmax] are treated as sensor errors and dropped;
    n_rejected counts how many were discarded this pass.
    """
    vals, units, last_ts, rejected = [], "", 0, 0
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
            if _out_of_range(fv, vmin, vmax):
                rejected += 1
                if ts:
                    last_ts = max(last_ts, ts)  # note freshness even if bad
                continue
            vals.append(fv)
            if ts:
                last_ts = max(last_ts, ts)
    if not vals:
        return None, units, last_ts, rejected
    recent = vals[-max(1, avg_n):]
    return sum(recent) / len(recent), units, last_ts, rejected


def _safe_emit(sig_fn, value):
    try:
        sig_fn(value)
    except RuntimeError:
        pass  # signal source deleted (dialog/tab closed while worker ran)


class _PollSignals(QObject):
    done = Signal(object)   # {name: (val_or_None, units, last_ts_ns, err, n_rejected)}
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
        out = {}
        for name in self._names:
            lo, hi = self._ranges.get(name, (None, None))
            try:
                samples = api.cpva_fetch_samples(name, start, end, timeout)
                val, units, last_ts, rejected = _avg_recent_numeric(
                    samples, avg_n, lo, hi)
                if rejected:
                    err = (f"dropped {rejected} out-of-range reading(s) "
                           f"[{_fmt(lo)}..{_fmt(hi)}]")
                else:
                    err = ""
                out[name] = (val, units, last_ts or end, err, rejected)
            except Exception as e:  # noqa: BLE001 - one bad PV can't kill the pass
                out[name] = (None, "", end, str(e), 0)
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

COLS = ["On", "Display name", "PV name", "Value", "Units", "State",
        "Alarm status", "Warn lo/hi", "Alarm lo/hi", "Updated"]

PV_MIME = "application/x-pv-monitor-row"
GROUP_HEADER_BG = QColor("#d7e3f4")
GROUP_HEADER_FG = QColor("#0D47A1")
UNGROUPED_LABEL = "Ungrouped"


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
    if rt.settling_until_ns and api.now_ns() < rt.settling_until_ns:
        return "settling → " + api.ns_to_prague(
            rt.settling_until_ns).strftime("%H:%M")
    if rt.alert.level == AlertLevel.OK:
        return ""
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
    if rt.settling_until_ns and api.now_ns() < rt.settling_until_ns:
        return ("Settling window: a dependency PV just turned on, so alerts for "
                "this PV are held until " + api.ns_to_prague(
                    rt.settling_until_ns).strftime("%H:%M:%S")
                + " while it re-stabilises.")
    if rt.alert.level == AlertLevel.OK:
        return "No active alert."
    lines = [f"State: {rt.alert.level.label}"]
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
    """Flat PV list rendered with per-group header rows.

    ``self.pvs`` is the single source of truth (kept group-contiguous by the
    widget). ``self._display`` is the derived view: a list of ("header", group)
    and ("pv", PVConfig) entries. Header rows appear only when at least one PV
    has a non-empty group, so an ungrouped setup looks exactly as before.
    """

    def __init__(self, pvs: list[PVConfig], runtime: dict[str, PVRuntime], parent=None):
        super().__init__(parent)
        self.pvs = pvs
        self.runtime = runtime
        self._display: list[tuple] = []
        self._rebuild()

    def _rebuild(self):
        show_headers = any(p.group for p in self.pvs)
        disp: list[tuple] = []
        last = object()   # sentinel so the first PV always opens a header
        for pv in self.pvs:
            if show_headers and pv.group != last:
                disp.append(("header", pv.group))
                last = pv.group
            disp.append(("pv", pv))
        self._display = disp

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
        if kind == "header":
            return Qt.ItemIsEnabled | Qt.ItemIsDropEnabled
        base = (Qt.ItemIsEnabled | Qt.ItemIsSelectable
                | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        if index.column() == 0:
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

        if kind == "header":
            if role == Qt.DisplayRole and col == 0:
                return ref or UNGROUPED_LABEL
            if role == Qt.BackgroundRole:
                return GROUP_HEADER_BG
            if role == Qt.ForegroundRole:
                return GROUP_HEADER_FG
            if role == Qt.FontRole:
                f = QFont()
                f.setBold(True)
                return f
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignVCenter | Qt.AlignLeft)
            return None

        pv = ref
        rt = self.runtime.get(pv.name)

        if role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if pv.enabled else Qt.Unchecked

        if role == Qt.ToolTipRole:
            if col == 6:
                return _alarm_status_tooltip(pv, rt)
            tip = pv.name
            if pv.gate_pvs:
                tip += "\nDepends on: " + ", ".join(pv.gate_pvs)
                for prof in pv.profiles:
                    tip += "\n  • " + _describe_profile(pv, prof)
                active = rt.active_profile if rt else None
                label = (active.get("label") or "conditional") if active else "default"
                tip += f"\nActive limits: {label}"
            if rt and rt.last_error:
                tip += f"\nLast error: {rt.last_error}"
            return tip

        level = rt.display_level() if rt else None
        bad = bool(rt and rt.bad_data and level is None and pv.enabled)

        if role == Qt.BackgroundRole and col == 5:
            if bad:
                return QColor(BADDATA_COLOR)
            if level is None:
                return QColor(NODATA_COLOR)
            return _STATE_BG[level]
        if role == Qt.ForegroundRole and col == 5:
            if level in (AlertLevel.WARNING, AlertLevel.ALARM) or level is None:
                return QColor("white")
            return QColor(SUCCESS)

        # Alarm-status cell: paint red only when a send failed, so a lost alert
        # stands out; other states use plain text.
        if col == 6 and rt is not None:
            if role == Qt.BackgroundRole and rt.notify_status == "failed":
                return QColor(ALARM_COLOR)
            if role == Qt.ForegroundRole and rt.notify_status == "failed":
                return QColor("white")

        if role == Qt.TextAlignmentRole and col in (3, 4, 5, 6, 7, 8):
            return int(Qt.AlignCenter)

        if role == Qt.DisplayRole:
            if col == 0:
                return None
            if col == 1:
                return pv.display_name
            if col == 2:
                return pv.name
            if col == 3:
                return _fmt(rt.current_value) if rt else "–"
            if col == 4:
                return (rt.current_units if rt and rt.current_units else pv.units) or ""
            if col == 5:
                if not pv.enabled:
                    return "off"
                if bad:
                    return "bad data"
                if level is None:
                    return "no data"
                return level.label.lower()
            if col == 6:
                return _alarm_status_text(pv, rt)
            # Threshold columns show whichever profile is currently in force:
            # the matched conditional profile, else the default set.
            active = rt.active_profile if rt else None
            if active is not None:
                thr = pv.profile_thresholds(active)
                mark = f" ({active.get('label') or 'cond'})"
            else:
                thr = pv.thresholds()
                mark = ""
            if col == 7:
                return f"{_fmt(thr.warn_low)} / {_fmt(thr.warn_high)}{mark}"
            if col == 8:
                return f"{_fmt(thr.alarm_low)} / {_fmt(thr.alarm_high)}{mark}"
            if col == 9:
                if rt and rt.last_update_ns:
                    return api.ns_to_prague(rt.last_update_ns).strftime("%H:%M:%S")
                return "–"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if index.column() == 0 and role == Qt.CheckStateRole:
            pv = self.pv_at_row(index.row())
            if pv is None:
                return False
            pv.enabled = (Qt.CheckState(value) == Qt.Checked)
            self.dataChanged.emit(index, index)
            win = self.parent()
            if isinstance(win, MonitorWidget):
                win.persist()
            return True
        return False

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
# Conditional-threshold rule row (used inside PVEditDialog)
# ---------------------------------------------------------------------------

class _ProfileRow(QGroupBox):
    """One conditional-threshold rule: dependency conditions + its limits.

    Both dependency slots are always shown; the caption for each reflects the
    dependency PV chosen above (or marks it unused). On save the dialog keeps
    only the conditions for the dependency slots that actually have a PV.
    """

    def __init__(self, on_remove):
        super().__init__()
        self.setStyleSheet(_GROUP_STYLE)
        v = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Label"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. sys-rate 10")
        self.name_edit.setToolTip(
            "A short name for this rule, shown in the table and tooltips when "
            "the rule is active (e.g. 'sys-rate 10').")
        top.addWidget(self.name_edit, 1)
        rm = QPushButton("✕ Remove rule")
        rm.setStyleSheet(SECONDARY_STYLE)
        rm.clicked.connect(lambda: on_remove(self))
        top.addWidget(rm)
        v.addLayout(top)

        self.dep_caption: list[QLabel] = []
        self.c_min: list[OptionalDoubleField] = []
        self.c_max: list[OptionalDoubleField] = []
        for _ in range(2):
            cap = QLabel()
            cap.setStyleSheet("color:#444; font-weight:600;")
            v.addWidget(cap)
            row = QHBoxLayout()
            cmin = OptionalDoubleField("value ≥")
            cmin.setToolTip(
                "Lower edge of this dependency's range for this rule. For an "
                "exact value, tick both ≥ and ≤ with the same number (or a "
                "narrow window). Unticked = no lower limit.")
            cmax = OptionalDoubleField("value ≤")
            cmax.setToolTip(
                "Upper edge of this dependency's range for this rule. "
                "Unticked = no upper limit.")
            row.addWidget(cmin)
            row.addWidget(cmax)
            row.addStretch(1)
            v.addLayout(row)
            self.dep_caption.append(cap)
            self.c_min.append(cmin)
            self.c_max.append(cmax)

        self.warn_low = OptionalDoubleField("Warn low ≤")
        self.warn_high = OptionalDoubleField("Warn high ≥")
        self.alarm_low = OptionalDoubleField("Alarm low ≤")
        self.alarm_high = OptionalDoubleField("Alarm high ≥")
        for a, b in ((self.warn_low, self.warn_high),
                     (self.alarm_low, self.alarm_high)):
            row = QHBoxLayout()
            row.addWidget(a)
            row.addWidget(b)
            row.addStretch(1)
            v.addLayout(row)

    def set_dep_labels(self, names: list[str]):
        for i, cap in enumerate(self.dep_caption):
            name = names[i] if i < len(names) else ""
            if name:
                cap.setText(f"When {api.shorten_pv_name(name)} is in range:")
            else:
                cap.setText(f"Dependency {i + 1} — set a dependency PV above to use")

    def load(self, prof: dict):
        self.name_edit.setText(prof.get("label", ""))
        conds = prof.get("conds") or []
        for i in range(2):
            cond = conds[i] if i < len(conds) else None
            lo, hi = (list(cond) + [None, None])[:2] if cond else (None, None)
            self.c_min[i].set_value(lo)
            self.c_max[i].set_value(hi)
        self.warn_low.set_value(prof.get("warn_low"))
        self.warn_high.set_value(prof.get("warn_high"))
        self.alarm_low.set_value(prof.get("alarm_low"))
        self.alarm_high.set_value(prof.get("alarm_high"))

    def cond(self, slot: int) -> list:
        return [self.c_min[slot].value(), self.c_max[slot].value()]

    def is_empty(self) -> bool:
        vals = (self.c_min[0].value(), self.c_max[0].value(),
                self.c_min[1].value(), self.c_max[1].value(),
                self.warn_low.value(), self.warn_high.value(),
                self.alarm_low.value(), self.alarm_high.value())
        return not (self.name_edit.text().strip()
                    or any(v is not None for v in vals))


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
        self.resize(560, 660)
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
        self.enabled_chk = QCheckBox("Enable alerting for this PV")
        self.enabled_chk.setStyleSheet(_CHK_STYLE)
        self.enabled_chk.setToolTip(
            "When on, this PV is evaluated against its thresholds and can raise "
            "Warning/Alarm alerts. When off, it is still polled and plotted but "
            "never triggers a notification.")
        self.enabled_chk.setChecked(pv.enabled)
        form.addRow("", self.enabled_chk)
        lay.addLayout(form)

        grp = QGroupBox("Default thresholds  (laser off / no gate — "
                        "unchecked = that side not checked)")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        self.f_warn_low = OptionalDoubleField("Warning low ≤")
        self.f_warn_low.setToolTip(
            "Tick and set a value: a Warning is raised when the reading falls to "
            "or below it. Leave unticked to not watch the low side for warnings.")
        self.f_warn_high = OptionalDoubleField("Warning high ≥")
        self.f_warn_high.setToolTip(
            "Tick and set a value: a Warning is raised when the reading rises to "
            "or above it. Leave unticked to not watch the high side for warnings.")
        self.f_alarm_low = OptionalDoubleField("Alarm low ≤")
        self.f_alarm_low.setToolTip(
            "Tick and set a value: an Alarm (more severe than Warning) is raised "
            "when the reading falls to or below it. Usually set below Warning low.")
        self.f_alarm_high = OptionalDoubleField("Alarm high ≥")
        self.f_alarm_high.setToolTip(
            "Tick and set a value: an Alarm (more severe than Warning) is raised "
            "when the reading rises to or above it. Usually set above Warning high.")
        for f in (self.f_warn_low, self.f_warn_high,
                  self.f_alarm_low, self.f_alarm_high):
            gl.addWidget(f)
        self.f_warn_low.set_value(pv.warn_low)
        self.f_warn_high.set_value(pv.warn_high)
        self.f_alarm_low.set_value(pv.alarm_low)
        self.f_alarm_high.set_value(pv.alarm_high)
        lay.addWidget(grp)

        # --- Conditional thresholds (dependency PVs) -----------------------
        gate = QGroupBox("Conditional thresholds  (depend on up to 2 other PVs)")
        gate.setStyleSheet(_GROUP_STYLE)
        gvl = QVBoxLayout(gate)

        self.dep_edits: list[QLineEdit] = []
        for i in range(2):
            grow = QHBoxLayout()
            grow.addWidget(QLabel(f"Dependency PV {i + 1}"))
            edit = QLineEdit(pv.gate_pvs[i] if i < len(pv.gate_pvs) else "")
            edit.setPlaceholderText("blank = not used")
            edit.setToolTip(
                "A PV this one depends on (e.g. hall state, sys-rate). Each rule "
                "below can require this PV to sit in a given range. Leave blank "
                "to use fewer dependencies.")
            edit.textChanged.connect(self._refresh_dep_labels)
            grow.addWidget(edit, 1)
            br = QPushButton("Browse…")
            br.setStyleSheet(SECONDARY_STYLE)
            br.setToolTip("Pick this dependency PV from the CPVA channel list.")
            br.clicked.connect(lambda _=False, e=edit: self._browse_into(e))
            grow.addWidget(br)
            self.dep_edits.append(edit)
            gvl.addLayout(grow)

        self._profiles_host = QVBoxLayout()
        gvl.addLayout(self._profiles_host)
        self._profile_rows: list[_ProfileRow] = []

        add_rule = QPushButton("+ Add rule")
        add_rule.setStyleSheet(SECONDARY_STYLE)
        add_rule.setToolTip(
            "Add a conditional rule. The first rule whose dependency conditions "
            "all match — and that has at least one limit set — decides the active "
            "limits. If no rule matches, the default thresholds above apply.")
        add_rule.clicked.connect(lambda: self._add_profile_row())
        gvl.addWidget(add_rule)

        ghint = QLabel(
            "Rules are checked top to bottom; the first match wins. A rule with "
            "no limits set is ignored. Example: 3 rules on sys-rate (0.2 / 3.3 / "
            "10) give each rate its own limits; leave a dependency's range empty "
            "to mean 'any value'.")
        ghint.setWordWrap(True)
        ghint.setStyleSheet("color:#777; font-size:11px;")
        gvl.addWidget(ghint)
        lay.addWidget(gate)

        for prof in pv.profiles:
            self._add_profile_row(prof)
        self._refresh_dep_labels()

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
        self.f_warn_low.set_value(r["warn_low"])
        self.f_warn_high.set_value(r["warn_high"])
        self.f_alarm_low.set_value(r["alarm_low"])
        self.f_alarm_high.set_value(r["alarm_high"])
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

    def _browse_into(self, edit: QLineEdit):
        dlg = PVBrowserDialog(self._win, self._win._all_channels,
                              float(self._win.settings["http_timeout_s"]))
        if not self._win._all_channels and dlg._all:
            self._win._all_channels = dlg._all
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            edit.setText(dlg.selected[0])

    def _add_profile_row(self, prof: Optional[dict] = None) -> _ProfileRow:
        row = _ProfileRow(self._remove_profile_row)
        if prof:
            row.load(prof)
        self._profile_rows.append(row)
        self._profiles_host.addWidget(row)
        self._refresh_dep_labels()
        return row

    def _remove_profile_row(self, row: _ProfileRow):
        if row in self._profile_rows:
            self._profile_rows.remove(row)
            row.setParent(None)
            row.deleteLater()

    def _refresh_dep_labels(self):
        names = [e.text().strip() for e in self.dep_edits]
        for row in self._profile_rows:
            row.set_dep_labels(names)

    def _collect_gating(self) -> tuple:
        """Build (gate_pvs, profiles) from the dependency fields and rule rows.

        Only dependency slots with a PV name are kept; each rule's conditions
        are aligned to those kept slots. Empty rules are dropped."""
        kept = [(name, slot) for slot, name in
                enumerate(e.text().strip() for e in self.dep_edits) if name]
        gate_pvs = [name for name, _ in kept]
        profiles = []
        for row in self._profile_rows:
            if row.is_empty():
                continue
            profiles.append({
                "label": row.name_edit.text().strip(),
                "conds": [row.cond(slot) for _, slot in kept],
                "warn_low": row.warn_low.value(), "warn_high": row.warn_high.value(),
                "alarm_low": row.alarm_low.value(), "alarm_high": row.alarm_high.value(),
            })
        return gate_pvs, profiles

    def _save(self):
        pv = self.pv
        pv.display_name = self.name_edit.text().strip() or pv.display_name
        pv.units = self.units_combo.currentText().strip()
        pv.group = self.group_combo.currentText().strip()
        pv.enabled = self.enabled_chk.isChecked()
        pv.warn_low = self.f_warn_low.value()
        pv.warn_high = self.f_warn_high.value()
        pv.alarm_low = self.f_alarm_low.value()
        pv.alarm_high = self.f_alarm_high.value()
        pv.gate_pvs, pv.profiles = self._collect_gating()
        pv.valid_min = self.f_valid_min.value()
        pv.valid_max = self.f_valid_max.value()
        if hasattr(self, "_pending_stats"):
            pv.learned_at = datetime.now(api.TZ_PRAGUE).isoformat(timespec="seconds")
            pv.learn_stats = self._pending_stats
        self.accept()


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


class SettleWidget(QWidget):
    """Editable Dependency-PV / hold-minutes table for settle windows.

    One row per gate (dependency) PV: when that PV turns on (rises 0 -> 1),
    every monitored PV depending on it holds its alerts for the given minutes.
    Rows are pre-populated with the gate PVs currently in use plus any already
    configured; extra dependencies can be added by hand for future use.
    """

    COLS = ["Dependency PV", "Hold (min)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.setColumnWidth(1, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(110)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.setStyleSheet(SECONDARY_STYLE)
        add.setToolTip("Add a dependency PV row so it, too, holds alerts for a "
                       "while after it turns on.")
        add.clicked.connect(lambda: self._add_row(start_edit=True))
        rm = QPushButton("Remove selected")
        rm.setStyleSheet(SECONDARY_STYLE)
        rm.setToolTip("Delete the selected dependency row(s).")
        rm.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)

    def _add_row(self, name: str = "", minutes: float = 0.0,
                 start_edit: bool = False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        name_item = QTableWidgetItem(name)
        self.table.setItem(r, 0, name_item)
        spin = _NoWheelDoubleSpinBox()
        spin.setRange(0, 1440)
        spin.setDecimals(0)
        spin.setValue(float(minutes or 0))
        spin.setToolTip("Minutes to hold this dependency's dependent PVs' alerts "
                        "after it turns on. 0 = no hold.")
        self.table.setCellWidget(r, 1, spin)
        if start_edit:
            self.table.setCurrentItem(name_item)
            self.table.editItem(name_item)

    def _remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def set_rows(self, mapping: dict):
        self.table.setRowCount(0)
        for name in sorted(mapping):
            self._add_row(name, mapping.get(name) or 0)

    def mapping(self) -> dict:
        out: dict[str, float] = {}
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            name = item.text().strip() if item else ""
            if not name:
                continue
            spin = self.table.cellWidget(r, 1)
            minutes = spin.value() if spin else 0
            out[name] = int(minutes)
        return out


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
            "commands. Lower = snappier replies but more API calls. Range "
            "1–120 s.")
        self.webex_cmd_poll.setValue(int(s.get("webex_command_poll_s", 1)))
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
        self.hyst = _NoWheelDoubleSpinBox(); self.hyst.setRange(0, 0.5)
        self.hyst.setSingleStep(0.01); self.hyst.setDecimals(3)
        self.hyst.setToolTip(
            "Dead-band around each threshold, as a fraction of it, that the "
            "reading must clear before the state resets — stops flapping when a "
            "value hovers on a limit. E.g. 0.05 = 5 %. Range 0–0.5.")
        self.hyst.setValue(float(s["hysteresis_frac"]))
        form.addRow("Hysteresis (fraction)", self.hyst)
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

        # --- Settle window (grace period after a dependency turns on) ------
        settle_grp = QGroupBox("Settle window after a dependency turns on")
        settle_grp.setStyleSheet(_GROUP_STYLE)
        sgl = QVBoxLayout(settle_grp)
        hint = QLabel(
            "When a dependency (gate) PV rises 0 → 1 — e.g. High/Low power "
            "starts and the chillers begin chasing a new setpoint — the PVs "
            "depending on it hold their alerts for the minutes below, so the "
            "warm-up transient doesn't fire a false alarm.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666; font-size:11px;")
        sgl.addWidget(hint)
        sform = QFormLayout()
        self.settle_default = _NoWheelSpinBox()
        self.settle_default.setRange(0, 1440)
        self.settle_default.setToolTip(
            "Hold time applied to any dependency PV that has no explicit row "
            "below. 0 = don't hold unless listed. Range 0–1440 min.")
        self.settle_default.setValue(int(s.get("settle_default_minutes", 0) or 0))
        sform.addRow("Default hold (min, 0=off)", self.settle_default)
        sgl.addLayout(sform)
        self.settle_table = SettleWidget()
        # Pre-fill with configured entries, then surface every gate PV actually
        # in use so the operator can see/tune it (falling back to the default).
        default_min = int(s.get("settle_default_minutes", 0) or 0)
        mapping = dict(s.get("settle_minutes") or {})
        for g in sorted(parent._gate_pv_names()):
            mapping.setdefault(g, default_min)
        self.settle_table.set_rows(mapping)
        sgl.addWidget(self.settle_table)
        lay.addWidget(settle_grp)

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
        s["avg_last_n"] = self.avg_n.value()
        s["sample_window_s"] = self.window_s.value()
        s["debounce_count"] = self.debounce.value()
        s["hysteresis_frac"] = self.hyst.value()
        s["renotify_cooldown_minutes"] = self.cooldown.value()
        s["settle_default_minutes"] = self.settle_default.value()
        s["settle_minutes"] = self.settle_table.mapping()
        s["recovery_notify"] = self.recovery.isChecked()
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
from matplotlib import cbook  # noqa: E402
from PySide6.QtGui import QIcon, QPixmap  # noqa: E402

MAX_GRAPH_POINTS = 3000


class _LightNavToolbar(NavToolbar):
    """Navigation toolbar whose icons are always painted dark, so they stay
    clearly visible on our light toolbar background. Matplotlib normally recolors
    icons white when it judges the ambient palette to be dark, which made the
    buttons blend into the background (invisible 'white' buttons)."""

    def _icon(self, name):
        path = cbook._get_data_path("images", name)
        large = path.with_name(path.name.replace(".png", "_large.png"))
        pm = QPixmap(str(large if large.exists() else path))
        pm.setDevicePixelRatio(self.devicePixelRatioF() or 1)
        mask = pm.createMaskFromColor(QColor("black"),
                                      Qt.MaskMode.MaskOutColor)
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
    has_warn = has_alarm = False
    for val, ls, lw in ((thr.warn_low, "--", 1.0), (thr.warn_high, "--", 1.0),
                        (thr.alarm_low, "-.", 1.5), (thr.alarm_high, "-.", 1.5)):
        if val is not None:
            ax.axhline(val, linestyle=ls, linewidth=lw, alpha=0.7,
                       color=ALARM_COLOR if ls == "-." else WARN_COLOR)
            if ls == "-.":
                has_alarm = True
            else:
                has_warn = True
    ax.set_title(f"{display_name}  (last {hours:g} h)")
    ax.set_ylabel(units)
    ax.grid(True, alpha=0.3)

    # (1)/(2) X axis: time only, more detail, no rotation.
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=6, maxticks=12))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=api.TZ_PRAGUE))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)
        lbl.set_ha("center")

    # (3) Legend describing the threshold lines.
    from matplotlib.lines import Line2D  # noqa: E402
    handles = [Line2D([0], [0], color=PRIMARY, linewidth=1.5, label=display_name)]
    if has_warn:
        handles.append(Line2D([0], [0], color=WARN_COLOR, linestyle="--",
                              alpha=0.7, label="Warning limit"))
    if has_alarm:
        handles.append(Line2D([0], [0], color=ALARM_COLOR, linestyle="-.",
                              alpha=0.7, label="Alarm limit"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16),
              ncol=len(handles), fontsize=8, frameon=False)

    # (4) Basic statistics as a caption under the graph.
    arr = np.asarray(ys, dtype=float)
    u = f" {units}" if units else ""
    stats = (f"min {arr.min():.4g}{u}    max {arr.max():.4g}{u}    "
             f"mean {arr.mean():.4g}{u}    n = {arr.size}")
    fig.text(0.5, 0.02, stats, ha="center", va="bottom", fontsize=8, color="0.35")

    fig.tight_layout(rect=(0, 0.13, 1, 1))

    buf = BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    return buf.getvalue()


class GraphPanel(QWidget):
    def __init__(self, win: "MonitorWidget"):
        super().__init__()
        self._win = win
        self._yaxis: Optional[tuple] = None   # (lo, hi) fixed Y range, or None=auto
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.combo = _NoWheelComboBox()
        self.combo.setToolTip(
            "Choose what the graph below plots: 'All PVs' overlays every PV "
            "(no threshold lines), or pick a single PV to see it alone with its "
            "Warning/Alarm threshold lines drawn in.")
        self.combo.currentIndexChanged.connect(lambda *_: self.redraw())
        top.addWidget(self.combo, 1)
        lay.addLayout(top)

        self.fig = Figure(figsize=(6, 3), dpi=96)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        lay.addWidget(_LightNavToolbar(self.canvas, self))
        lay.addWidget(self.canvas, 1)

    def set_yaxis(self, lo, hi):
        """Fix the Y range (lo, hi), or pass (None, None) to restore autoscale."""
        self._yaxis = None if lo is None or hi is None else (float(lo), float(hi))
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

    def _plot_one(self, pv: PVConfig, color, with_thresholds: bool):
        rt = self._win.runtime.get(pv.name)
        if not rt or not rt.history:
            return
        pts = list(rt.history)[-MAX_GRAPH_POINTS:]
        xs = [api.ns_to_prague(t) for t, _ in pts]
        ys = [v for _, v in pts]
        self.ax.plot(xs, ys, drawstyle="steps-post", color=color,
                     label=pv.display_name, linewidth=1.6)
        if with_thresholds:
            for val, ls, lw in ((pv.warn_low, "--", 1.0), (pv.warn_high, "--", 1.0),
                                (pv.alarm_low, "-.", 1.6), (pv.alarm_high, "-.", 1.6)):
                if val is not None:
                    self.ax.axhline(val, color=color, linestyle=ls,
                                    linewidth=lw, alpha=0.6)

    def redraw(self):
        self.ax.clear()
        sel = self.combo.currentData()
        win_min = float(self._win.settings["graph_window_minutes"])

        cmap = matplotlib.colormaps.get_cmap("tab10")
        if sel is None:  # "All PVs"
            for i, pv in enumerate(self._win.pvs):
                self._plot_one(pv, cmap(i % 10), with_thresholds=False)
        else:
            pv = next((p for p in self._win.pvs if p.name == sel), None)
            if pv:
                self._plot_one(pv, PRIMARY, with_thresholds=True)

        if self.ax.get_legend_handles_labels()[0]:
            self.ax.legend(loc="upper left", fontsize=8)
        self.ax.grid(True, alpha=0.3)
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=api.TZ_PRAGUE))

        # Limit x to the configured window if we have data
        all_x = [ln.get_xdata() for ln in self.ax.get_lines()]
        if any(len(x) for x in all_x):
            try:
                xmax = max(x[-1] for x in all_x if len(x))
                xmin = xmax - (win_min / (24 * 60))
                self.ax.set_xlim(xmin, xmax)
            except Exception:
                pass
        if self._yaxis is not None:
            self.ax.set_ylim(*self._yaxis)
        # Horizontal time labels (no rotation) — autofmt_xdate would tilt them.
        for lbl in self.ax.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_ha("center")
        self.fig.tight_layout()
        self.canvas.draw_idle()


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
        # Clipboard for the right-click "Copy settings / Paste settings" feature.
        self._copied_settings: Optional[dict] = None
        self._copied_from: str = ""
        # Latest value of every gate PV that isn't itself a monitored PV,
        # refreshed each poll so state-dependent thresholds can switch.
        self._gate_values: dict[str, Optional[float]] = {}
        # Settle windows: last observed value of each gate PV (to detect the
        # 0 -> 1 rising edge) and, per gate PV, the ns until which its dependent
        # PVs hold their alerts.
        self._gate_prev: dict[str, Optional[float]] = {}
        self._settle_until: dict[str, int] = {}
        self._poll_gen = 0
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
        self._cmd_bot_id_inflight = False
        self._cmd_gen = 0

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
            hysteresis_frac=float(s["hysteresis_frac"]),
            renotify_cooldown_minutes=float(s["renotify_cooldown_minutes"]),
            recovery_notify=bool(s["recovery_notify"]),
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
        }
        for label, slot in (("Add PV", self.add_pv),
                            ("Edit", self.edit_pv),
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
        self.table.doubleClicked.connect(lambda *_: self.edit_pv())
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
        hh.setSectionResizeMode(1, QHeaderView.Stretch)   # Display name
        hh.setSectionResizeMode(2, QHeaderView.Stretch)   # PV name
        for c in (0, 3, 4, 5, 6, 7, 8, 9):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.model.modelReset.connect(self._apply_group_spans)
        self._apply_group_spans()
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
        """Make each group-header row span the full width of the table."""
        self.table.clearSpans()
        for r, (kind, _ref) in enumerate(self.model._display):
            if kind == "header":
                self.table.setSpan(r, 0, 1, len(COLS))

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

    # --- settle / grace window ----------------------------------------
    def _settle_minutes_for(self, gate: str) -> float:
        """Hold time (minutes) when this gate PV turns on: its own entry in
        ``settle_minutes``, else the global default. 0 = no hold."""
        table = self.settings.get("settle_minutes") or {}
        try:
            if gate in table and table[gate] is not None:
                return float(table[gate])
            return float(self.settings.get("settle_default_minutes", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _update_settle_windows(self, now: int) -> None:
        """Open a settle window for any gate PV that just rose 0 -> 1.

        Only a genuine observed off->on edge triggers it: an unknown (None)
        previous value never does, so a gate PV that is already on when
        monitoring starts doesn't spuriously hold alerts.
        """
        for g in self._gate_pv_names():
            cur = self._gate_value(g)
            prev = self._gate_prev.get(g)
            rising = (prev is not None and prev < 0.5
                      and cur is not None and cur >= 0.5)
            if rising:
                mins = self._settle_minutes_for(g)
                if mins > 0:
                    self._settle_until[g] = now + int(mins * 60 * 1e9)
                    until = api.ns_to_prague(self._settle_until[g]).strftime("%H:%M")
                    self._log(f"{api.shorten_pv_name(g)} on — holding dependent "
                              f"alerts {mins:g} min (until {until}) while things settle.")
            if cur is not None:
                self._gate_prev[g] = cur

    def _settle_until_for_pv(self, pv: PVConfig, now: int) -> int:
        """The latest active settle deadline among this PV's gate PVs, or 0 if
        none is currently holding it."""
        deadline = 0
        for g in pv.gate_pvs:
            until = self._settle_until.get(g, 0)
            if until and now < until:
                deadline = max(deadline, until)
        return deadline

    def _match_profile(self, pv: PVConfig) -> Optional[dict]:
        """The conditional profile in force for this PV right now, or None if
        no rule matches (caller uses the default thresholds)."""
        if not pv.gate_pvs or not pv.profiles:
            return None
        return pv.match_profile([self._gate_value(g) for g in pv.gate_pvs])

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

    # --- copy / paste settings ----------------------------------------
    # Fields carried by copy/paste: limits, dependency gating and valid range.
    # Identity (name/display_name/group), the enabled flag and learned-history
    # stats stay with each target PV.
    _COPY_KEYS = ("units", "warn_low", "warn_high", "alarm_low", "alarm_high",
                  "gate_pvs", "profiles", "valid_min", "valid_max")

    def _show_context_menu(self, pos):
        idx = self.table.indexAt(pos)
        pv = self.model.pv_at_row(idx.row()) if idx.isValid() else None
        targets = self._selected_pvs()
        menu = QMenu(self)

        act_edit = menu.addAction("Edit…")
        act_edit.setEnabled(pv is not None)
        menu.addSeparator()
        act_copy = menu.addAction("Copy settings")
        act_copy.setEnabled(pv is not None)
        paste_label = "Paste settings"
        if self._copied_settings is not None:
            paste_label += f" from {self._copied_from} → {len(targets)} PV(s)"
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
        elif chosen == act_copy and pv is not None:
            self._copy_pv_settings(pv)
        elif chosen == act_paste:
            self._paste_pv_settings(targets)
        elif chosen == act_remove:
            self.remove_pv()

    def _copy_pv_settings(self, pv: PVConfig):
        d = pv.to_dict()
        self._copied_settings = {k: copy.deepcopy(d[k]) for k in self._COPY_KEYS}
        self._copied_from = pv.display_name
        self._log(f"Copied settings from {pv.display_name} "
                  "(limits, dependencies, valid range).")

    def _paste_pv_settings(self, targets: list[PVConfig]):
        if not self._copied_settings or not targets:
            return
        names = ", ".join(pv.display_name for pv in targets)
        if QMessageBox.question(
                self, "Paste settings",
                f"Overwrite limits, dependencies and valid range of "
                f"{len(targets)} PV(s) with the settings copied from "
                f"{self._copied_from}?\n\n{names}") != QMessageBox.Yes:
            return
        for pv in targets:
            for k in self._COPY_KEYS:
                setattr(pv, k, copy.deepcopy(self._copied_settings[k]))
        self._recluster()
        self.model.reset()
        self.graph.refresh_combo()
        self.persist()
        self._log(f"Pasted settings from {self._copied_from} onto "
                  f"{len(targets)} PV(s).")

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
        """Reorder self.pvs so each group's PVs are contiguous.

        Group order follows first appearance; within-group order is preserved.
        This keeps the group-header view coherent after edits and drops.
        """
        buckets: dict[str, list] = {}
        order: list[str] = []
        for pv in self.pvs:
            if pv.group not in buckets:
                buckets[pv.group] = []
                order.append(pv.group)
            buckets[pv.group].append(pv)
        self.pvs[:] = [pv for g in order for pv in buckets[g]]

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
        elif cur[0] == "pv":                              # before a PV row
            anchor = cur[1]
            group = anchor.group
        else:                                             # on a group header
            prev = disp[target_disp - 1] if target_disp - 1 >= 0 else None
            if prev is None:                              # top of the first group
                group = cur[1]
                nxt = disp[target_disp + 1] if target_disp + 1 < n else None
                anchor = nxt[1] if nxt and nxt[0] == "pv" else None
            else:                                         # end of the prior group
                after = prev[1]
                group = after.group

        reduced = [p for p in self.pvs if p.name not in moving_names]
        for p in moving:
            p.group = group

        if anchor is not None and anchor.name not in moving_names:
            idx = reduced.index(anchor)
        elif after is not None and after.name not in moving_names:
            idx = reduced.index(after) + 1
        else:                                             # end of the target group
            idxs = [i for i, p in enumerate(reduced) if p.group == group]
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
        # Non-modal so the main window stays movable/usable while Settings is
        # open. Reuse the existing instance if it's already shown.
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        before = self._cmd_settings_snapshot()
        dlg = SettingsDialog(self)
        self._settings_dlg = dlg
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.accepted.connect(lambda b=before: self._on_settings_accepted(b))
        dlg.destroyed.connect(lambda *_: setattr(self, "_settings_dlg", None))
        dlg.show()

    def _on_settings_accepted(self, before: str):
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
            # Re-prime edge detection: the first poll after a start records gate
            # values without triggering (prev is unknown), so a gate PV already
            # on at start-up doesn't open a spurious settle window.
            self._gate_prev.clear()
            self._settle_until.clear()
            self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            self.timer.start()
            self._log("Monitoring started.")
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

    def poll_now(self):
        self._log("Manual poll.")
        self._start_poll()

    def _start_poll(self):
        monitored = {pv.name for pv in self.pvs}
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        # Also fetch any gate PV that isn't already monitored, so its value is
        # available this pass to switch state-dependent thresholds.
        names += [g for g in sorted(self._gate_pv_names()) if g not in monitored]
        ranges = {pv.name: self._valid_range(pv) for pv in self.pvs}
        self._poll_gen += 1
        gen = self._poll_gen
        sig = _PollSignals(self)
        sig.done.connect(lambda res, g=gen: self._on_poll(res)
                         if g == self._poll_gen else None)
        self._poll_sig = sig
        QThreadPool.globalInstance().start(
            _PollWorker(sig, names, dict(self.settings), ranges))

    def _on_poll(self, results: dict):
        now = api.now_ns()
        # Refresh gate-PV values first so threshold switching below sees this
        # pass's data (a gate PV may not itself be in the monitored list).
        for g in self._gate_pv_names():
            res = results.get(g)
            if res is not None:
                self._gate_values[g] = res[0]
        # Open/refresh settle windows before evaluating, so a gate PV that
        # turned on this pass already holds its dependents' alerts below.
        self._update_settle_windows(now)
        for pv in self.pvs:
            res = results.get(pv.name)
            if res is None:
                continue
            val, units, last_ts, err, rejected = res
            rt = self.runtime[pv.name]
            prev_rej = rt.rejected_count
            rt.current_value = val
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
            settle_until = self._settle_until_for_pv(pv, now)
            rt.settling_until_ns = settle_until
            if pv.enabled and thr.is_active():
                if settle_until:
                    # In the grace window after a gate PV turned on: freeze the
                    # state machine (don't evaluate/commit/notify) so the
                    # settling transient is ignored entirely. Drop any pending
                    # debounce so a half-formed transition can't survive it.
                    rt.alert.pending_level = None
                    rt.alert.pending_count = 0
                else:
                    note = self.evaluator.evaluate(rt.alert, val, thr, now)
                    if note is not None:
                        self._dispatch_alert(pv, rt, note)
            if rt.alert.level == AlertLevel.OK:
                rt.notify_status = ""      # episode over — clear the status cell
                rt.notify_error = ""
        self.model.refresh_all()
        self.graph.redraw()

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
                             valid_range=(None, None)) -> bool:
        """Start the render+dispatch worker. Returns True if a worker was
        launched, False if there is nothing to send it to."""
        if not self.hub.is_any_configured():
            self._log("  No notification channel configured (see Settings).")
            if tag == "manual":
                self.btn_sendplot.setEnabled(True)
            return False
        hours = float(self.settings.get("alert_plot_hours", 12))
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
        self._resolve_bot_id()
        poll_s = max(1, int(self.settings.get("webex_command_poll_s", 1)))
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
        if self._cmd_poll_inflight:
            return   # previous poll hasn't returned yet — never overlap requests
        if not self._cmd_bot_id:
            self._resolve_bot_id()   # keep retrying until it resolves, off the UI thread
        self._cmd_poll_inflight = True
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
            mentioned = bool(self._cmd_bot_id
                             and self._cmd_bot_id in (it.get("mentionedPeople") or []))
            if "/" in text:
                if not text.startswith("/"):
                    text = text[text.index("/"):]   # strip a leading @mention
            else:
                # Be forgiving: a bare "help"/"?"/"commands" (no slash) is
                # treated as /help. If the bot was @mentioned without any
                # recognizable command, greet with the basics rather than
                # staying silent. Anything else without a slash is ignored so
                # the bot stays quiet during normal conversation.
                low = text.lower()
                if low in ("help", "?", "commands") or low.endswith(" help"):
                    text = "/help"
                elif mentioned:
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
            "**PV Monitor — I watch the L3 beamline PVs and alert on "
            "warning/alarm limits.** Mention me and send one of these:\n"
            "- `/status` — all PVs + current values + state\n"
            "- `/list` — list configured PVs\n"
            "- `/plot <pv>` — send a current plot (PNG) of a PV\n"
            "- `/graph <pv|all>` — set which PV the live graph shows\n"
            "- `/start` — monitoring on\n"
            "- `/stop [hours]` — monitoring off; with hours, auto-resume later "
            "(e.g. `/stop 10`)\n"
            "- `/enable <pv>` `/disable <pv>` — alerting on/off per PV\n"
            "- `/window <minutes>` — live-graph time window\n"
            "- `/yaxis <lo> <hi>` | `/yaxis auto` — live-graph Y range\n"
            "- `/help` — this list\n"
            "\n_PV names accept partial matches (e.g. `/plot chiller`)._")

    def _cmd_status(self) -> str:
        if not self.pvs:
            return "No PVs configured."
        lines = []
        for p in self.pvs:
            rt = self.runtime.get(p.name)
            val = _fmt(rt.current_value) if rt else "–"
            units = (rt.current_units if rt and rt.current_units else p.units) or ""
            if not p.enabled:
                state = "off"
            elif rt and rt.display_level() is not None:
                state = rt.display_level().label.lower()
            else:
                state = "no data"
            lines.append(f"- **{p.display_name}**: {val} {units} [{state}]")
        mon = "MONITORING" if self._monitoring else "stopped"
        return f"**Status ({mon}):**\n" + "\n".join(lines)

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
        note = self.evaluator.evaluate(self._sim_state, v, self._active_thresholds(pv),
                                       rt.last_update_ns)
        # Reflect simulated level in the table without touching the real alert state.
        rt.alert.level = self._sim_state.level
        rt.alert.first_notified_ns = self._sim_state.first_notified_ns
        if note is not None:
            self._dispatch_alert(pv, rt, note)
        self.model.refresh_all()
        self.graph.redraw()
