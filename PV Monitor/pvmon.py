"""
PV Monitor — watch CPVA archiver PVs, alert to Microsoft Teams when out of range.

Standalone PySide6 app for ELI Beamlines. Polls a configurable list of EPICS
PVs from the CPVA archiver, compares each against learned/manual warning + alarm
limits, sends a Teams message on state changes, and shows the data in a
colour-coded table + a live graph with threshold lines.

See cpva_api.py (archiver client) and alerting.py (state machine + Teams client).
"""

from __future__ import annotations

import json
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool,
    QTimer, Signal,
)
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableView,
    QToolBar, QVBoxLayout, QWidget,
)

import cpva_api as api
from alerting import (
    AlertEvaluator, AlertLevel, AlertState, EvalConfig, TeamsClient, Thresholds,
    build_messagecard, describe_reason,
)

# ---------------------------------------------------------------------------
# Design language (from skill_pyside6_app_design)
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
_BTN_DANGER = (
    "QPushButton { background:#B71C1C; color:white; font-weight:700; "
    "padding:7px 10px; border-radius:4px; }"
    "QPushButton:hover { background:#7F0000; }"
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

CONFIG_FILE = api.APP_DIR / "pvmon_config.json"

DEFAULT_SETTINGS = {
    "teams_webhook_url": "",
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
}


def load_config() -> dict:
    data = {"version": 1, "settings": {}, "pvs": [], "window_geometry": None}
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
        print(f"[config] save failed: {e}")


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
        self._sig.done.emit(out)


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
            self._sig.done.emit(api.cpva_fetch_channels(self._timeout))
        except Exception as e:  # noqa: BLE001
            self._sig.error.emit(str(e))


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
            self._sig.log.emit(f"Fetching {self._days} d of history for {self._name}…")
            samples = api.cpva_fetch_samples_chunked(
                self._name, start, end, self._timeout, log_fn=self._sig.log.emit)
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
                self._sig.error.emit(
                    f"Insufficient history ({len(vals)} numeric points). "
                    "Limits left unchanged.")
                return
            result = compute_baseline(np.asarray(vals), self._warn_k, self._alarm_k)
            result["units"] = units
            result["n"] = len(vals)
            result["days"] = self._days
            self._sig.done.emit(result)
        except Exception as e:  # noqa: BLE001
            self._sig.error.emit(str(e))


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
            if isinstance(win, PVMonitorWindow):
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
        extra = "" if len(matches) <= 2000 else f" (showing first 2000)"
        self.count.setText(f"{len(matches)} match / {len(self._all)} total{extra}")

    def _accept(self):
        self.selected = [i.text() for i in self.list.selectedItems()]
        if self.selected:
            self.accept()


class PVEditDialog(QDialog):
    def __init__(self, parent: "PVMonitorWindow", pv: PVConfig):
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
    def __init__(self, parent: "PVMonitorWindow"):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(520, 480)
        self._win = parent
        s = parent.settings

        lay = QVBoxLayout(self)

        teams = QGroupBox("Microsoft Teams")
        teams.setStyleSheet(_GROUP_STYLE)
        tl = QVBoxLayout(teams)
        tl.addWidget(QLabel("Incoming Webhook URL"))
        self.webhook = QLineEdit(s["teams_webhook_url"])
        self.webhook.setPlaceholderText("https://…webhook…")
        tl.addWidget(self.webhook)
        test = QPushButton("Send test message")
        test.setStyleSheet(_BTN_PRIMARY)
        test.clicked.connect(self._test)
        tl.addWidget(test)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        tl.addWidget(self.test_status)
        lay.addWidget(teams)

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

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _test(self):
        client = TeamsClient(self.webhook.text().strip(),
                             float(self._win.settings["http_timeout_s"]))
        if client.send_test():
            self.test_status.setText("✓ Test message sent.")
            self.test_status.setStyleSheet(f"color:{SUCCESS};")
        else:
            self.test_status.setText(f"✗ Failed: {client.last_error}")
            self.test_status.setStyleSheet(f"color:{DANGER};")

    def _save(self):
        s = self._win.settings
        s["teams_webhook_url"] = self.webhook.text().strip()
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
from matplotlib.figure import Figure  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402

MAX_GRAPH_POINTS = 3000


class GraphPanel(QWidget):
    def __init__(self, win: "PVMonitorWindow"):
        super().__init__()
        self._win = win
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

        self.ax.legend(loc="upper left", fontsize=8) if self.ax.get_legend_handles_labels()[0] else None
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
        self.fig.autofmt_xdate()
        self.fig.tight_layout()
        self.canvas.draw_idle()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class PVMonitorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PV Monitor")
        self.resize(1100, 760)

        cfg = load_config()
        self.settings = cfg["settings"]
        self.pvs: list[PVConfig] = [PVConfig.from_dict(d) for d in cfg["pvs"]]
        self.runtime: dict[str, PVRuntime] = {}
        self._all_channels: list[str] = []
        self._poll_gen = 0
        self._monitoring = False
        self._sim_timer: Optional[QTimer] = None

        self.teams = TeamsClient(self.settings["teams_webhook_url"],
                                 float(self.settings["http_timeout_s"]))
        self.evaluator = AlertEvaluator(self._eval_config())
        self._init_runtime()

        self._build_ui()
        self._restore_geometry(cfg.get("window_geometry"))
        self._prefetch_channels()

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
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_monitor = QAction("▶ Start monitoring", self)
        self.act_monitor.setCheckable(True)
        self.act_monitor.triggered.connect(self.toggle_monitoring)
        tb.addAction(self.act_monitor)
        tb.addSeparator()
        tb.addAction(QAction("Add PV", self, triggered=self.add_pv))
        tb.addAction(QAction("Edit", self, triggered=self.edit_pv))
        tb.addAction(QAction("Remove", self, triggered=self.remove_pv))
        tb.addAction(QAction("Move ↑", self, triggered=lambda: self.move_pv(-1)))
        tb.addAction(QAction("Move ↓", self, triggered=lambda: self.move_pv(1)))
        tb.addSeparator()
        tb.addAction(QAction("Poll now", self, triggered=self.poll_now))
        tb.addAction(QAction("Simulate alert", self, triggered=self.simulate_alert))
        tb.addSeparator()
        tb.addAction(QAction("Settings", self, triggered=self.open_settings))

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

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFixedHeight(120)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(4, 4, 4, 4)
        cl.addWidget(splitter, 1)
        cl.addWidget(self.log)
        self.setCentralWidget(central)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._start_poll)

        self._update_status_bar()
        self._log(f"Loaded {len(self.pvs)} PV(s). Config: {CONFIG_FILE}")

    # --- helpers -------------------------------------------------------
    def _log(self, msg: str):
        ts = datetime.now(api.TZ_PRAGUE).strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {msg}")

    def _update_status_bar(self):
        state = "MONITORING" if self._monitoring else "stopped"
        teams = "Teams ✓" if self.teams.is_configured() else "Teams not set"
        self.statusBar().showMessage(
            f"{state}  ·  {len(self.pvs)} PV(s)  ·  every "
            f"{self.settings['poll_interval_s']}s  ·  {teams}")

    def persist(self):
        save_config({
            "version": 1,
            "settings": self.settings,
            "pvs": [pv.to_dict() for pv in self.pvs],
            "window_geometry": self.saveGeometry().toBase64().data().decode(),
        })

    def _restore_geometry(self, geo):
        if geo:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            except Exception:
                pass

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
            self.teams = TeamsClient(self.settings["teams_webhook_url"],
                                     float(self.settings["http_timeout_s"]))
            self.evaluator = AlertEvaluator(self._eval_config())
            ml = self._history_maxlen()
            for rt in self.runtime.values():
                if rt.history.maxlen != ml:
                    rt.history = deque(rt.history, maxlen=ml)
            if self._monitoring:
                self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            self.persist()
            self._update_status_bar()
            self._log("Settings saved.")

    # --- monitoring loop ----------------------------------------------
    def toggle_monitoring(self, on: bool):
        self._monitoring = on
        self.act_monitor.setChecked(on)
        self.act_monitor.setText("⏹ Stop monitoring" if on else "▶ Start monitoring")
        if on:
            self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            self.timer.start()
            self._log("Monitoring started.")
            self._start_poll()
        else:
            self.timer.stop()
            self._poll_gen += 1            # discard any in-flight result
            self._log("Monitoring stopped.")
        self._update_status_bar()

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
        card = build_messagecard(
            note.level, note.prev_level, pv.name, pv.display_name,
            note.value, rt.current_units or pv.units, note.reason,
            api.ns_to_prague_str(rt.last_update_ns or api.now_ns()), note.kind)
        ok = self.teams.post(card)
        if not ok:
            self._log(f"  Teams send failed: {self.teams.last_error}")
            self.statusBar().showMessage(
                f"Teams send failed: {self.teams.last_error}", 8000)

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

    # --- close ---------------------------------------------------------
    def closeEvent(self, event):
        self.persist()
        super().closeEvent(event)


def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ELI.PVMonitor.1")
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = PVMonitorWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
