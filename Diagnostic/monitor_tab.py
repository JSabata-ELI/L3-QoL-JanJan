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

import json
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool,
    QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTableView, QVBoxLayout,
    QWidget,
)

import cpva_api as api
from alerting import (
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EvalConfig,
    NotificationHub, Thresholds, describe_reason,
)

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
# Dialog styles (carried over from the original app for dialog widgets)
# ---------------------------------------------------------------------------

PRIMARY, PRIMARY_HOVER = "#1565C0", "#0D47A1"
SUCCESS = "#2E7D32"
DANGER = "#B71C1C"
WARN_COLOR = "#cc6600"
ALARM_COLOR = "#cc2200"
NODATA_COLOR = "#9e9e9e"

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
    "recovery_notify": True,
    "debounce_count": 2,
    "hysteresis_frac": 0.05,
    "history_minutes": 720,
    "learn_days_default": 7,
    "warn_k_default": 3.0,
    "alarm_k_default": 5.0,
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
    "email_recipients": [],
    # webex
    "webex_mode": "bot",             # webhook | bot  (bot supports the PNG graph)
    "webex_webhook_url": "",
    "webex_bot_token": "",
    "webex_room_id": "",
    # webex two-way commands (bot mode only)
    "webex_commands_enabled": True,
    "webex_command_poll_s": 7,
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
    data["settings"] = settings
    data.setdefault("pvs", [])
    return data


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
    enabled: bool = False
    warn_low: Optional[float] = None
    warn_high: Optional[float] = None
    alarm_low: Optional[float] = None
    alarm_high: Optional[float] = None
    learned_at: Optional[str] = None
    learn_stats: Optional[dict] = None

    def __post_init__(self):
        if not self.display_name:
            self.display_name = api.shorten_pv_name(self.name)

    def thresholds(self) -> Thresholds:
        return Thresholds(self.warn_low, self.warn_high,
                          self.alarm_low, self.alarm_high)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "display_name": self.display_name,
            "units": self.units, "enabled": self.enabled,
            "warn_low": self.warn_low, "warn_high": self.warn_high,
            "alarm_low": self.alarm_low, "alarm_high": self.alarm_high,
            "learned_at": self.learned_at, "learn_stats": self.learn_stats,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PVConfig":
        return cls(
            name=d["name"], display_name=d.get("display_name", ""),
            units=d.get("units", ""), enabled=bool(d.get("enabled", False)),
            warn_low=d.get("warn_low"), warn_high=d.get("warn_high"),
            alarm_low=d.get("alarm_low"), alarm_high=d.get("alarm_high"),
            learned_at=d.get("learned_at"), learn_stats=d.get("learn_stats"),
        )


@dataclass
class PVRuntime:
    current_value: Optional[float] = None
    current_units: str = ""
    last_update_ns: int = 0
    alert: AlertState = field(default_factory=AlertState)
    history: deque = field(default_factory=lambda: deque(maxlen=2000))
    last_error: str = ""

    def display_level(self):
        """AlertLevel for colouring, or None for NODATA."""
        if self.current_value is None:
            return None
        return self.alert.level


# ---------------------------------------------------------------------------
# Workers (QThreadPool + QRunnable)
# ---------------------------------------------------------------------------

def _avg_recent_numeric(samples: list[dict], avg_n: int):
    """Return (avg_value_or_None, units, last_ts_ns) from raw CPVA samples."""
    vals, units, last_ts = [], "", 0
    for s in samples:
        v = api.cpva_decode_value(s)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            vals.append(float(v))
            u = api.cpva_decode_units(s)
            if u:
                units = u
            t = s.get("time")
            if isinstance(t, (int, float)):
                last_ts = max(last_ts, int(t))
    if not vals:
        return None, units, last_ts
    recent = vals[-max(1, avg_n):]
    return sum(recent) / len(recent), units, last_ts


def _safe_emit(sig_fn, value):
    try:
        sig_fn(value)
    except RuntimeError:
        pass  # signal source deleted (dialog/tab closed while worker ran)


class _PollSignals(QObject):
    done = Signal(object)   # {name: (value_or_None, units, last_ts_ns, error_str)}
    log = Signal(str)


class _PollWorker(QRunnable):
    def __init__(self, sig: _PollSignals, names: list[str], settings: dict):
        super().__init__()
        self._sig = sig
        self._names = names
        self._s = settings

    def run(self):
        window_ns = int(self._s["sample_window_s"] * 1e9)
        avg_n = int(self._s["avg_last_n"])
        timeout = float(self._s["http_timeout_s"])
        end = api.now_ns()
        start = end - window_ns
        out = {}
        for name in self._names:
            try:
                samples = api.cpva_fetch_samples(name, start, end, timeout)
                val, units, last_ts = _avg_recent_numeric(samples, avg_n)
                out[name] = (val, units, last_ts or end, "")
            except Exception as e:  # noqa: BLE001 - one bad PV can't kill the pass
                out[name] = (None, "", end, str(e))
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


class _LearnWorker(QRunnable):
    def __init__(self, sig: _LearnSignals, name: str, days: int,
                 warn_k: float, alarm_k: float, timeout: float):
        super().__init__()
        self._sig = sig
        self._name = name
        self._days = days
        self._warn_k = warn_k
        self._alarm_k = alarm_k
        self._timeout = timeout

    def run(self):
        try:
            end = api.now_ns()
            start = end - int(self._days * 86400 * 1e9)
            _safe_emit(self._sig.log.emit,
                       f"Fetching {self._days} d of history for {self._name}…")
            samples = api.cpva_fetch_samples_chunked(
                self._name, start, end, self._timeout,
                log_fn=lambda m: _safe_emit(self._sig.log.emit, m))
            vals = []
            units = ""
            for s in samples:
                v = api.cpva_decode_value(s)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)) and np.isfinite(v):
                    vals.append(float(v))
                    u = api.cpva_decode_units(s)
                    if u:
                        units = u
            if len(vals) < 30:
                _safe_emit(self._sig.error.emit,
                           f"Insufficient history ({len(vals)} numeric points). "
                           "Limits left unchanged.")
                return
            result = compute_baseline(np.asarray(vals), self._warn_k, self._alarm_k)
            result["units"] = units
            result["n"] = len(vals)
            result["days"] = self._days
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


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------

COLS = ["On", "PV", "Value", "Units", "State", "Warn lo/hi", "Alarm lo/hi", "Updated"]


def _fmt(x) -> str:
    return "–" if x is None else f"{x:g}"


class PVTableModel(QAbstractTableModel):
    def __init__(self, pvs: list[PVConfig], runtime: dict[str, PVRuntime], parent=None):
        super().__init__(parent)
        self.pvs = pvs
        self.runtime = runtime

    def rowCount(self, parent=QModelIndex()):
        return len(self.pvs)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLS[section]
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:
            return base | Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        pv = self.pvs[index.row()]
        rt = self.runtime.get(pv.name)
        col = index.column()

        if role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if pv.enabled else Qt.Unchecked

        if role == Qt.ToolTipRole:
            tip = pv.name
            if rt and rt.last_error:
                tip += f"\nLast error: {rt.last_error}"
            return tip

        level = rt.display_level() if rt else None

        if role == Qt.BackgroundRole and col == 4:
            if level is None:
                return QColor(NODATA_COLOR)
            return _STATE_BG[level]
        if role == Qt.ForegroundRole and col == 4:
            if level in (AlertLevel.WARNING, AlertLevel.ALARM) or level is None:
                return QColor("white")
            return QColor(SUCCESS)

        if role == Qt.TextAlignmentRole and col in (2, 3, 4, 5, 6):
            return int(Qt.AlignCenter)

        if role == Qt.DisplayRole:
            if col == 0:
                return None
            if col == 1:
                return pv.display_name
            if col == 2:
                return _fmt(rt.current_value) if rt else "–"
            if col == 3:
                return (rt.current_units if rt and rt.current_units else pv.units) or ""
            if col == 4:
                if not pv.enabled:
                    return "off"
                if level is None:
                    return "no data"
                return level.label.lower()
            if col == 5:
                return f"{_fmt(pv.warn_low)} / {_fmt(pv.warn_high)}"
            if col == 6:
                return f"{_fmt(pv.alarm_low)} / {_fmt(pv.alarm_high)}"
            if col == 7:
                if rt and rt.last_update_ns:
                    return api.ns_to_prague(rt.last_update_ns).strftime("%H:%M:%S")
                return "–"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if index.column() == 0 and role == Qt.CheckStateRole:
            pv = self.pvs[index.row()]
            pv.enabled = (Qt.CheckState(value) == Qt.Checked)
            self.dataChanged.emit(index, index)
            win = self.parent()
            if isinstance(win, MonitorWidget):
                win.persist()
            return True
        return False

    def refresh_row(self, row: int):
        if 0 <= row < len(self.pvs):
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, len(COLS) - 1))

    def refresh_all(self):
        if self.pvs:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self.pvs) - 1, len(COLS) - 1))


# ---------------------------------------------------------------------------
# Optional double field (spinbox + enable checkbox -> None when unchecked)
# ---------------------------------------------------------------------------

class OptionalDoubleField(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chk = QCheckBox(label)
        self.chk.setStyleSheet(_CHK_STYLE)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(-1e12, 1e12)
        self.spin.setDecimals(4)
        self.spin.setEnabled(False)
        self.chk.toggled.connect(self.spin.setEnabled)
        lay.addWidget(self.chk)
        lay.addWidget(self.spin, 1)

    def value(self) -> Optional[float]:
        return self.spin.value() if self.chk.isChecked() else None

    def set_value(self, v: Optional[float]):
        if v is None:
            self.chk.setChecked(False)
        else:
            self.chk.setChecked(True)
            self.spin.setValue(v)


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
        self.search.textChanged.connect(self._apply_filter)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        lay.addWidget(self.list, 1)

        self.count = QLabel("")
        lay.addWidget(self.count)

        bb = QDialogButtonBox()
        add = bb.addButton("Add selected", QDialogButtonBox.AcceptRole)
        add.setStyleSheet(_BTN_PRIMARY)
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
        self.resize(460, 460)
        self._win = parent
        self.pv = pv

        lay = QVBoxLayout(self)

        info = QLabel(pv.name)
        info.setStyleSheet("color:#555;")
        info.setWordWrap(True)
        lay.addWidget(info)

        form = QFormLayout()
        self.name_edit = QLineEdit(pv.display_name)
        form.addRow("Display name", self.name_edit)
        self.enabled_chk = QCheckBox("Enable alerting for this PV")
        self.enabled_chk.setStyleSheet(_CHK_STYLE)
        self.enabled_chk.setChecked(pv.enabled)
        form.addRow("", self.enabled_chk)
        lay.addLayout(form)

        grp = QGroupBox("Thresholds  (unchecked = that side not checked)")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        self.f_warn_low = OptionalDoubleField("Warning low ≤")
        self.f_warn_high = OptionalDoubleField("Warning high ≥")
        self.f_alarm_low = OptionalDoubleField("Alarm low ≤")
        self.f_alarm_high = OptionalDoubleField("Alarm high ≥")
        for f in (self.f_warn_low, self.f_warn_high,
                  self.f_alarm_low, self.f_alarm_high):
            gl.addWidget(f)
        self.f_warn_low.set_value(pv.warn_low)
        self.f_warn_high.set_value(pv.warn_high)
        self.f_alarm_low.set_value(pv.alarm_low)
        self.f_alarm_high.set_value(pv.alarm_high)
        lay.addWidget(grp)

        learn = QGroupBox("Learn from history")
        learn.setStyleSheet(_GROUP_STYLE)
        ll = QHBoxLayout(learn)
        ll.addWidget(QLabel("Days"))
        self.days = QSpinBox()
        self.days.setRange(1, 90)
        self.days.setValue(int(parent.settings["learn_days_default"]))
        ll.addWidget(self.days)
        ll.addWidget(QLabel("warn k"))
        self.warn_k = QDoubleSpinBox()
        self.warn_k.setRange(0.5, 20)
        self.warn_k.setSingleStep(0.5)
        self.warn_k.setValue(float(parent.settings["warn_k_default"]))
        ll.addWidget(self.warn_k)
        ll.addWidget(QLabel("alarm k"))
        self.alarm_k = QDoubleSpinBox()
        self.alarm_k.setRange(0.5, 30)
        self.alarm_k.setSingleStep(0.5)
        self.alarm_k.setValue(float(parent.settings["alarm_k_default"]))
        ll.addWidget(self.alarm_k)
        self.learn_btn = QPushButton("Learn")
        self.learn_btn.setStyleSheet(_BTN_PRIMARY)
        self.learn_btn.clicked.connect(self._learn)
        ll.addWidget(self.learn_btn)
        lay.addWidget(learn)

        self.learn_status = QLabel("")
        self.learn_status.setWordWrap(True)
        self.learn_status.setStyleSheet("color:#555;")
        lay.addWidget(self.learn_status)

        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _learn(self):
        self.learn_btn.setEnabled(False)
        self.learn_status.setText("Learning…")
        sig = _LearnSignals(self)
        sig.done.connect(self._on_learned)
        sig.error.connect(self._on_learn_error)
        sig.log.connect(self.learn_status.setText)
        self._learn_sig = sig
        worker = _LearnWorker(
            sig, self.pv.name, self.days.value(),
            self.warn_k.value(), self.alarm_k.value(),
            float(self._win.settings["http_timeout_s"]))
        QThreadPool.globalInstance().start(worker)

    def _on_learned(self, r: dict):
        self.learn_btn.setEnabled(True)
        self.f_warn_low.set_value(r["warn_low"])
        self.f_warn_high.set_value(r["warn_high"])
        self.f_alarm_low.set_value(r["alarm_low"])
        self.f_alarm_high.set_value(r["alarm_high"])
        if r.get("units") and not self.pv.units:
            self.pv.units = r["units"]
        self._pending_stats = {k: r[k] for k in
                               ("center", "spread", "method", "n", "days")}
        self.learn_status.setText(
            f"Learned from {r['n']} pts / {r['days']} d "
            f"({r['method']}, center={r['center']:g}, spread={r['spread']:g}). "
            "Review and Save.")

    def _on_learn_error(self, msg: str):
        self.learn_btn.setEnabled(True)
        self.learn_status.setText(f"⚠ {msg}")

    def _save(self):
        pv = self.pv
        pv.display_name = self.name_edit.text().strip() or pv.display_name
        pv.enabled = self.enabled_chk.isChecked()
        pv.warn_low = self.f_warn_low.value()
        pv.warn_high = self.f_warn_high.value()
        pv.alarm_low = self.f_alarm_low.value()
        pv.alarm_high = self.f_alarm_high.value()
        if hasattr(self, "_pending_stats"):
            pv.learned_at = datetime.now(api.TZ_PRAGUE).isoformat(timespec="seconds")
            pv.learn_stats = self._pending_stats
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, parent: "MonitorWidget"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(560, 720)
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
        self.email_en = QCheckBox("Email")
        self.webex_en = QCheckBox("Webex")
        for w, key in ((self.teams_en, "teams_enabled"),
                       (self.email_en, "email_enabled"),
                       (self.webex_en, "webex_enabled")):
            w.setStyleSheet(_CHK_STYLE)
            w.setChecked(bool(s.get(key)))
            cl.addRow("", w)
        self.plot_hours = QSpinBox()
        self.plot_hours.setRange(0, 168)
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
        tl.addWidget(self.webhook)
        bt = QPushButton("Send test to Teams")
        bt.setStyleSheet(_BTN_PRIMARY)
        bt.clicked.connect(self._test_teams)
        tl.addWidget(bt)
        lay.addWidget(teams)

        # --- Email ---------------------------------------------------------
        email = QGroupBox("Email (SMTP)")
        email.setStyleSheet(_GROUP_STYLE)
        ef = QFormLayout(email)
        self.smtp_host = QLineEdit(s.get("smtp_host", ""))
        ef.addRow("SMTP host", self.smtp_host)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(s.get("smtp_port", 587)))
        ef.addRow("Port", self.smtp_port)
        self.smtp_sec = QComboBox()
        self.smtp_sec.addItems(["none", "starttls", "ssl"])
        self.smtp_sec.setCurrentText(s.get("smtp_security", "starttls"))
        ef.addRow("Security", self.smtp_sec)
        self.smtp_user = QLineEdit(s.get("smtp_user", ""))
        ef.addRow("Username (optional)", self.smtp_user)
        self.smtp_pass = QLineEdit(s.get("smtp_password", ""))
        self.smtp_pass.setEchoMode(QLineEdit.Password)
        self.smtp_pass.setToolTip("Stored in plaintext in monitor_config.json. "
                                  "Tip: use ${ENV:NAME} to read from an env var.")
        ef.addRow("Password", self.smtp_pass)
        self.email_from = QLineEdit(s.get("email_from", ""))
        ef.addRow("From address", self.email_from)
        self.email_to = QLineEdit("; ".join(s.get("email_recipients", [])))
        self.email_to.setPlaceholderText("comma- or semicolon-separated")
        ef.addRow("Recipients", self.email_to)
        be = QPushButton("Send test email")
        be.setStyleSheet(_BTN_PRIMARY)
        be.clicked.connect(self._test_email)
        ef.addRow("", be)
        lay.addWidget(email)

        # --- Webex ---------------------------------------------------------
        webex = QGroupBox("Webex")
        webex.setStyleSheet(_GROUP_STYLE)
        wf = QFormLayout(webex)
        self.webex_mode = QComboBox()
        self.webex_mode.addItems(["webhook", "bot"])
        self.webex_mode.setCurrentText(s.get("webex_mode", "webhook"))
        self.webex_mode.currentTextChanged.connect(self._update_webex_fields)
        wf.addRow("Mode", self.webex_mode)
        self.webex_url = QLineEdit(s.get("webex_webhook_url", ""))
        self.webex_url.setPlaceholderText("https://…webex incoming webhook…")
        wf.addRow("Webhook URL", self.webex_url)
        self.webex_token = QLineEdit(s.get("webex_bot_token", ""))
        self.webex_token.setEchoMode(QLineEdit.Password)
        self.webex_token.setToolTip("Stored in plaintext in monitor_config.json. "
                                    "Tip: use ${ENV:NAME} to read from an env var.")
        wf.addRow("Bot token", self.webex_token)
        self.webex_room = QLineEdit(s.get("webex_room_id", ""))
        wf.addRow("Room ID", self.webex_room)
        bw = QPushButton("Send test to Webex")
        bw.setStyleSheet(_BTN_PRIMARY)
        bw.clicked.connect(self._test_webex)
        wf.addRow("", bw)

        self.webex_cmds = QCheckBox("Accept commands from Webex (bot mode only)")
        self.webex_cmds.setStyleSheet(_CHK_STYLE)
        self.webex_cmds.setChecked(bool(s.get("webex_commands_enabled", True)))
        wf.addRow("", self.webex_cmds)
        self.webex_cmd_poll = QSpinBox()
        self.webex_cmd_poll.setRange(3, 120)
        self.webex_cmd_poll.setValue(int(s.get("webex_command_poll_s", 7)))
        wf.addRow("Command poll (s)", self.webex_cmd_poll)
        self.webex_allow = QLineEdit("; ".join(s.get("webex_command_allowlist", [])))
        self.webex_allow.setPlaceholderText("allowed sender e-mails, empty = anyone in room")
        wf.addRow("Command allowlist", self.webex_allow)

        lay.addWidget(webex)
        self._update_webex_fields(self.webex_mode.currentText())

        # --- Monitoring ----------------------------------------------------
        form_grp = QGroupBox("Monitoring")
        form_grp.setStyleSheet(_GROUP_STYLE)
        form = QFormLayout(form_grp)
        self.poll = QSpinBox(); self.poll.setRange(5, 3600)
        self.poll.setValue(int(s["poll_interval_s"]))
        form.addRow("Poll interval (s)", self.poll)
        self.avg_n = QSpinBox(); self.avg_n.setRange(1, 500)
        self.avg_n.setValue(int(s["avg_last_n"]))
        form.addRow("Average last N samples", self.avg_n)
        self.window_s = QSpinBox(); self.window_s.setRange(5, 3600)
        self.window_s.setValue(int(s["sample_window_s"]))
        form.addRow("Sample window (s)", self.window_s)
        self.debounce = QSpinBox(); self.debounce.setRange(1, 20)
        self.debounce.setValue(int(s["debounce_count"]))
        form.addRow("Debounce (polls)", self.debounce)
        self.hyst = QDoubleSpinBox(); self.hyst.setRange(0, 0.5)
        self.hyst.setSingleStep(0.01); self.hyst.setDecimals(3)
        self.hyst.setValue(float(s["hysteresis_frac"]))
        form.addRow("Hysteresis (fraction)", self.hyst)
        self.cooldown = QSpinBox(); self.cooldown.setRange(0, 1440)
        self.cooldown.setValue(int(s["renotify_cooldown_minutes"]))
        form.addRow("Re-notify cooldown (min, 0=off)", self.cooldown)
        self.recovery = QCheckBox("Notify on recovery to OK")
        self.recovery.setStyleSheet(_CHK_STYLE)
        self.recovery.setChecked(bool(s["recovery_notify"]))
        form.addRow("", self.recovery)
        self.hist = QSpinBox(); self.hist.setRange(10, 10080)
        self.hist.setValue(int(s["history_minutes"]))
        form.addRow("History kept (min)", self.hist)
        self.graph_win = QSpinBox(); self.graph_win.setRange(1, 10080)
        self.graph_win.setValue(int(s["graph_window_minutes"]))
        form.addRow("Graph window (min)", self.graph_win)
        self.autostart = QCheckBox("Start monitoring on launch")
        self.autostart.setStyleSheet(_CHK_STYLE)
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
        self.webex_room.setEnabled(is_bot)

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
            recipients=_parse_recipients(self.email_to.text()),
            timeout=float(self._win.settings["http_timeout_s"]))
        self._show_test(c.send_test(), "Email", c.last_error)

    def _test_webex(self):
        from alerting import WebexNotifier
        c = WebexNotifier(
            mode=self.webex_mode.currentText(),
            webhook_url=self.webex_url.text().strip(),
            bot_token=self.webex_token.text().strip(),
            room_id=self.webex_room.text().strip(),
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
        s["smtp_password"] = self.smtp_pass.text()
        s["email_from"] = self.email_from.text().strip()
        s["email_recipients"] = _parse_recipients(self.email_to.text())
        # webex
        s["webex_mode"] = self.webex_mode.currentText()
        s["webex_webhook_url"] = self.webex_url.text().strip()
        s["webex_bot_token"] = self.webex_token.text().strip()
        s["webex_room_id"] = self.webex_room.text().strip()
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
        s["recovery_notify"] = self.recovery.isChecked()
        s["history_minutes"] = self.hist.value()
        s["graph_window_minutes"] = self.graph_win.value()
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


def render_pv_png(pv_name: str, display_name: str, hours: float,
                  thr: Thresholds, timeout: float) -> bytes | None:
    """Fetch the last `hours` h from CPVA and render a value+threshold PNG.

    Worker-thread safe: builds its own Figure and uses the non-Qt Agg canvas,
    rendering to in-memory PNG bytes (no temp file, no shared matplotlib state).
    """
    end = api.now_ns()
    start = end - int(hours * 3600 * 1e9)
    samples = api.cpva_fetch_samples_chunked(pv_name, start, end, timeout)
    xs, ys, units = [], [], ""
    for s in samples:
        v = api.cpva_decode_value(s)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        t = s.get("time")
        if isinstance(t, (int, float)):
            xs.append(api.ns_to_prague(int(t)))
            ys.append(float(v))
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


class GraphPanel(QWidget):
    def __init__(self, win: "MonitorWidget"):
        super().__init__()
        self._win = win
        self._yaxis: Optional[tuple] = None   # (lo, hi) fixed Y range, or None=auto
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(lambda *_: self.redraw())
        top.addWidget(self.combo, 1)
        lay.addLayout(top)

        self.fig = Figure(figsize=(6, 3), dpi=96)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        lay.addWidget(NavToolbar(self.canvas, self))
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
        self.fig.autofmt_xdate()
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
                 timeout: float, tag: str):
        super().__init__()
        self._sig = sig
        self._hub = hub
        self._payload = payload
        self._thr = thr
        self._hours = hours
        self._timeout = timeout
        self._tag = tag

    def run(self):
        png = None
        if self._hours > 0:
            try:
                png = render_pv_png(self._payload.pv_name, self._payload.display_name,
                                    self._hours, self._thr, self._timeout)
            except Exception:  # noqa: BLE001 - alert must still go out text-only
                png = None
        errors = self._hub.dispatch(self._payload, png)
        _safe_emit(self._sig.done.emit, (self._tag, errors, png is not None))


# ---------------------------------------------------------------------------
# Webex two-way command workers (bot mode only)
# ---------------------------------------------------------------------------

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
        self._poll_gen = 0
        self._monitoring = False
        self._sim_timer: Optional[QTimer] = None
        self._cmd_timer: Optional[QTimer] = None
        self._cmd_last_id = None
        self._cmd_primed = False

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
        self.btn_monitor.clicked.connect(self.toggle_monitoring)
        btn_row.addWidget(self.btn_monitor)
        btn_row.addSpacing(8)

        for label, slot in (("Add PV", self.add_pv),
                            ("Edit", self.edit_pv),
                            ("Remove", self.remove_pv),
                            ("Move ↑", lambda: self.move_pv(-1)),
                            ("Move ↓", lambda: self.move_pv(1)),
                            ("Poll now", self.poll_now),
                            ("Simulate alert", self.simulate_alert)):
            b = _btn(label, SECONDARY_STYLE)
            b.clicked.connect(slot)
            btn_row.addWidget(b)

        self.btn_sendplot = _btn("Send plot now", SECONDARY_STYLE)
        self.btn_sendplot.clicked.connect(self.send_plot_now)
        btn_row.addWidget(self.btn_sendplot)

        self.btn_settings = _btn("Settings", SECONDARY_STYLE)
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
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.doubleClicked.connect(lambda *_: self.edit_pv())
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3, 4, 5, 6, 7):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        splitter.addWidget(self.table)

        self.graph = GraphPanel(self)
        self.graph.refresh_combo()
        splitter.addWidget(self.graph)
        splitter.setSizes([300, 360])
        root.addWidget(splitter, 1)

        self.log = LogWidget()
        self.log.setMaximumHeight(140)
        root.addWidget(self.log)

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

    def _selected_pv(self) -> Optional[PVConfig]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.pvs[rows[0].row()]

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
            self.model.beginResetModel()
            for name in dlg.selected:
                if name in existing:
                    continue
                pv = PVConfig(name=name)        # disabled until learned/set
                self.pvs.append(pv)
                self.runtime[pv.name] = PVRuntime(history=deque(maxlen=ml))
                added += 1
            self.model.endResetModel()
            if added:
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
            self.model.refresh_all()
            self.graph.refresh_combo()
            self.persist()
            self._log(f"Updated {pv.display_name}.")

    def remove_pv(self):
        pv = self._selected_pv()
        if not pv:
            return
        if QMessageBox.question(self, "Remove PV",
                                f"Remove {pv.display_name}?") != QMessageBox.Yes:
            return
        self.model.beginResetModel()
        self.pvs.remove(pv)
        self.runtime.pop(pv.name, None)
        self.model.endResetModel()
        self.graph.refresh_combo()
        self.persist()
        self._update_status()
        self._log(f"Removed {pv.display_name}.")

    def move_pv(self, delta: int):
        pv = self._selected_pv()
        if not pv:
            return
        i = self.pvs.index(pv)
        j = i + delta
        if 0 <= j < len(self.pvs):
            self.model.beginResetModel()
            self.pvs[i], self.pvs[j] = self.pvs[j], self.pvs[i]
            self.model.endResetModel()
            self.table.selectRow(j)
            self.graph.refresh_combo()
            self.persist()

    def open_settings(self):
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
            self._start_cmd_listener()
            self.persist()
            self._update_status()
            self._log("Settings saved.")

    # --- monitoring loop ----------------------------------------------
    def toggle_monitoring(self, on: bool):
        self._monitoring = on
        self.btn_monitor.setChecked(on)
        self.btn_monitor.setText("⏹ Stop monitoring" if on else "▶ Start monitoring")
        self.btn_monitor.setStyleSheet(STOP_BUTTON_STYLE if on else BUTTON_STYLE)
        if on:
            self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            self.timer.start()
            self._log("Monitoring started.")
            self._start_poll()
        else:
            self.timer.stop()
            self._poll_gen += 1            # discard any in-flight result
            self._log("Monitoring stopped.")
        self._update_status()

    def poll_now(self):
        self._log("Manual poll.")
        self._start_poll()

    def _start_poll(self):
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        self._poll_gen += 1
        gen = self._poll_gen
        sig = _PollSignals(self)
        sig.done.connect(lambda res, g=gen: self._on_poll(res)
                         if g == self._poll_gen else None)
        self._poll_sig = sig
        QThreadPool.globalInstance().start(_PollWorker(sig, names, dict(self.settings)))

    def _on_poll(self, results: dict):
        now = api.now_ns()
        for pv in self.pvs:
            res = results.get(pv.name)
            if res is None:
                continue
            val, units, last_ts, err = res
            rt = self.runtime[pv.name]
            rt.current_value = val
            rt.current_units = units or rt.current_units
            rt.last_update_ns = last_ts or now
            rt.last_error = err
            if val is not None:
                rt.history.append((rt.last_update_ns, val))
            if pv.enabled and pv.thresholds().is_active():
                note = self.evaluator.evaluate(rt.alert, val, pv.thresholds(), now)
                if note is not None:
                    self._dispatch_alert(pv, rt, note)
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
        self._launch_alert_worker(payload, pv.thresholds(), tag="auto")

    def _launch_alert_worker(self, payload: AlertPayload, thr: Thresholds, tag: str):
        if not self.hub.is_any_configured():
            self._log("  No notification channel configured (see Settings).")
            if tag == "manual":
                self.btn_sendplot.setEnabled(True)
            return
        hours = float(self.settings.get("alert_plot_hours", 12))
        timeout = float(self.settings["http_timeout_s"])
        sig = _AlertSignals(self)
        sig.done.connect(self._on_alert_result)
        self._alert_sig = sig
        QThreadPool.globalInstance().start(
            _AlertWorker(sig, self.hub, payload, thr, hours, timeout, tag))

    def _on_alert_result(self, result):
        tag, errors, had_png = result
        for ch, err in errors.items():
            self._log(f"  {ch} send failed: {err}")
        if tag == "manual":
            self.btn_sendplot.setEnabled(True)
            if not errors:
                extra = "" if had_png else " (no data for plot — text only)"
                self._log(f"Plot sent.{extra}")

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
        self._launch_alert_worker(payload, pv.thresholds(), tag=tag)

    def send_plot_now(self):
        pv = self._selected_pv()
        if not pv:
            QMessageBox.information(self, "Send plot", "Select a PV first.")
            return
        self.btn_sendplot.setEnabled(False)
        self._log(f"Sending plot for {pv.display_name}…")
        self._send_plot_for(pv, tag="manual")

    # --- Webex two-way command listener -------------------------------
    def _start_cmd_listener(self):
        self._stop_cmd_listener()
        if not (self.settings.get("webex_commands_enabled")
                and self.hub.webex.can_listen()):
            return
        self._cmd_primed = False
        self._cmd_last_id = None
        poll_s = max(3, int(self.settings.get("webex_command_poll_s", 7)))
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

    def _poll_commands(self):
        if not self.hub.webex.can_listen():
            return
        sig = _CmdPollSignals(self)
        sig.done.connect(self._on_commands)
        self._cmd_sig = sig
        QThreadPool.globalInstance().start(
            _CmdPollWorker(sig, self.hub.webex, self._cmd_last_id))

    def _on_commands(self, result):
        new_items, newest_id = result
        if newest_id:
            self._cmd_last_id = newest_id
        if not self._cmd_primed:
            self._cmd_primed = True   # don't replay backlog on the first poll
            return
        allow = [e.lower() for e in self.settings.get("webex_command_allowlist", [])]
        for it in new_items:
            text = (it.get("text") or "").strip()
            if "/" not in text:
                continue
            if not text.startswith("/"):
                text = text[text.index("/"):]   # strip a leading @mention
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
                self.toggle_monitoring(False)
                self._reply("⏹ Monitoring stopped.")
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
            "**PV Monitor commands:**\n"
            "- `/status` — all PVs + values + state\n"
            "- `/list` — list configured PVs\n"
            "- `/plot <pv>` — send current plot of a PV\n"
            "- `/start` `/stop` — monitoring on/off\n"
            "- `/enable <pv>` `/disable <pv>` — alerting per PV\n"
            "- `/graph <pv|all>` — set the live graph\n"
            "- `/window <minutes>` — graph time window\n"
            "- `/yaxis <lo> <hi>` | `/yaxis auto` — graph Y range")

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
        if not pv.thresholds().is_active():
            QMessageBox.information(
                self, "Simulate",
                "This PV has no thresholds set. Edit/learn limits first.")
            return
        thr = pv.thresholds()
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
        note = self.evaluator.evaluate(self._sim_state, v, pv.thresholds(),
                                       rt.last_update_ns)
        # Reflect simulated level in the table without touching the real alert state.
        rt.alert.level = self._sim_state.level
        if note is not None:
            self._dispatch_alert(pv, rt, note)
        self.model.refresh_all()
        self.graph.redraw()
