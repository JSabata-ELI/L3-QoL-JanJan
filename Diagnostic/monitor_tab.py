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
import os
import re
import socket
import threading
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
from PySide6.QtGui import (
    QColor, QCursor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTableView, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import cpva_api as api
import shared_pvs
from alerting import (
    AlertEvaluator, AlertLevel, AlertPayload, AlertState, EvalConfig,
    NotificationHub, Thresholds, Trend, _raw_severity, classify_trend,
    describe_reason, detect_frozen, fmt_duration, write_run_status,
)
import bot_commands
import memstats
import notify_provision
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
    # The log is the one part of this app that grows for as long as it is left
    # open, and it is meant to be left open for weeks. Capping the number of
    # lines Qt keeps means the oldest line drops off instead of the log slowly
    # eating memory; 20 000 lines is far more than a day's worth here.
    MAX_LINES = 20000

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setStyleSheet(LOG_STYLE)
        self.setMaximumBlockCount(self.MAX_LINES)

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
# Monitoring stopped: values keep coming in, but nothing is evaluated or sent.
# The State cell keeps its real reading and gets this background instead.
STOPPED_COLOR = "#7f0000"
# The PV keeps delivering the exact same reading (or its newest sample stopped
# advancing): data arrives, but it is not live. Own colour so a frozen PV can't
# be confused with a healthy steady one, with grey no-data or purple bad-data.
FROZEN_COLOR = "#00695c"

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
    "settle_minutes": 7.0,
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
    # Frozen-value check ("not updating"): a PV that keeps returning the exact
    # same reading for this long is no longer live, even though the archiver
    # still answers — a dead sensor or stuck IOC. Reported in the State column
    # and, with frozen_alert_enabled, once per episode over the alert channels.
    # frozen_min_points guards against sparse data: the unchanged run must be
    # carried by at least this many samples before it counts.
    "frozen_check_enabled": True,
    "frozen_after_minutes": 120,
    "frozen_min_points": 5,
    "frozen_alert_enabled": True,
    "learn_days_default": 7,
    "warn_k_default": 3.0,
    "alarm_k_default": 5.0,
    # Sensor-error sanity range applied to every PV unless it overrides:
    # readings outside [valid_min_default, valid_max_default] are discarded.
    # None = no bound on that side.
    "valid_min_default": None,
    "valid_max_default": 80.0,
    "graph_window_minutes": 60,
    # Graph legend placement, set from the graph's right-click menu. Local-only
    # (a per-user view preference, see SHARE_LOCAL_ONLY_KEYS): "best" lets
    # matplotlib pick the emptiest corner, a fixed corner name pins it there,
    # "outside" parks it beside the plot, "off" hides it, and "custom" uses
    # graph_legend_anchor — the (x, y) in axes fractions the user dragged it to.
    "graph_legend_loc": "best",
    "graph_legend_anchor": [],
    # PV list beside the graph: a fixed, scrollable list of the plotted curves
    # (colour sample + name) shown left of the plot. While it is on, no legend
    # is drawn inside the plot — the two are alternatives, and the list is the
    # default because matplotlib re-picks a "best" legend corner on every
    # redraw, which made the legend appear to jump around. Local-only, like the
    # legend keys above.
    "graph_pv_panel": True,
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
    # shared PV list: the list itself, and the shared part of these settings,
    # live on the scratch share (see shared_pvs.py) so every copy of the app
    # monitors the same PVs with the same limits and pacing. These four keys are
    # LOCAL-ONLY (they are what points this PC at the share in the first place)
    # — see SHARE_LOCAL_ONLY_KEYS / shared_settings_subset.
    "shared_pv_list_enabled": True,
    "shared_pv_list_path": "",          # blank = autodetect; folder or full .json
    "shared_pv_list_timeout_s": 3.0,    # max seconds startup may spend on the share
    "_shared_pv_root_cache": "",        # written by the app: last root that worked
}

# Settings that must never leave this PC: the share plumbing (publishing it
# would point every other copy at whichever leg this PC used), plus purely
# personal view preferences that would otherwise reshuffle everyone's graph.
SHARE_LOCAL_ONLY_KEYS = (
    "shared_pv_list_enabled", "shared_pv_list_path", "shared_pv_list_timeout_s",
    "_shared_pv_root_cache",
    "graph_legend_loc", "graph_legend_anchor", "graph_pv_panel",
)


def shared_settings_subset(settings: dict) -> dict:
    """The part of `settings` that travels over the share.

    Everything the Settings dialog configures — poll pacing, debounce/settle,
    re-notify and trend behaviour, learn and valid-range defaults, watchdog,
    graph and alert-plot windows — minus:

      * SHARE_LOCAL_ONLY_KEYS (per-PC plumbing),
      * the notification channels, which come from the build
        (notify_provision) and whose secrets are per-account DPAPI blobs that
        would be useless — and unwelcome — on a scratch share.

    Unknown keys are dropped, so a hand-edited or stale shared file can never
    inject settings this version does not know.
    """
    skip = set(SHARE_LOCAL_ONLY_KEYS) | set(notify_provision.PROVISIONED_KEYS)
    return {k: v for k, v in settings.items()
            if k in DEFAULT_SETTINGS and k not in skip}


# Trailing debounce for publishing to the share. persist() fires on every single
# checkbox click, and an SMB write costs tens to hundreds of ms, so writing
# per-call would stutter the most-used interaction in the tab and republish the
# whole list dozens of times. Coalescing needs no changes at persist()'s many
# call sites.
SHARED_WRITE_DEBOUNCE_MS = 2000

# --- long-run memory watch --------------------------------------------------
# This app is meant to be left running for weeks, and the thing that fails
# first on a Windows PC left up that long is not RAM but the COMMIT limit
# (RAM + page file, promised across every process): once it is full, nothing
# new starts. So the app's own committed memory and the PC's commit charge are
# shown in the status line and written to the log at intervals — the log line
# is what turns "it feels slower today" into a number that either climbs or
# does not. It doubles as an "I am still alive" heartbeat in the log.
MEM_LOG_INTERVAL_MS = 30 * 60 * 1000     # every half hour
MEM_WARN_PCT = 90.0                       # PC commit this full -> warn in the log
MEM_WARN_REPEAT_NS = int(3600e9)          # …and at most once an hour


def load_config() -> dict:
    data = {"version": 1, "settings": {}, "pvs": []}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            data.update(loaded)
        except Exception as e:  # noqa: BLE001 - a bad config must not block launch
            # Not fatal, but no longer invisible: this path silently yields an
            # empty PV list, which used to be indistinguishable from "nothing
            # configured yet".
            print(f"[monitor config] load failed, using defaults: {e}")
    settings = dict(DEFAULT_SETTINGS)
    settings.update(data.get("settings") or {})
    _migrate_settings(settings)
    # Channels baked into the build win over whatever this PC has locally, so
    # every copy alerts through the same accounts with no per-PC setup (and the
    # local file never has to carry the credentials). See notify_provision.
    settings.update(notify_provision.load())
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
    # Never write provisioned channel settings to disk: they are supplied by the
    # build and would otherwise be sitting in a plain JSON next to the exe (and
    # would shadow a later rebuild's values).
    prov = notify_provision.load()
    if prov:
        settings = {k: v for k, v in (data.get("settings") or {}).items()
                    if k not in prov}
        data = {**data, "settings": settings}
    try:
        # Atomic: this file is the offline fallback whose PV list can seed the
        # share, and load_config() degrades a truncated file to an empty list.
        shared_pvs.write_json_atomic(CONFIG_FILE, data)
    except Exception as e:  # noqa: BLE001
        print(f"[monitor config] save failed: {e}")


@dataclass
class PVListChoice:
    """Outcome of deciding which PV list a session runs on.

    ``shared_ok`` is the write guard. False means "this copy is NOT the
    authority, never publish": if the shared list could not be read, writing our
    in-memory list back would overwrite everyone else's list with whatever stale
    copy this PC happened to have.

    ``seed_needed`` is True only when the shared file is missing and we have PVs
    to put there. Republishing an identical list at every launch would bump the
    file's mtime for everyone and make other copies report a phantom conflict.

    ``settings`` is the shared settings block that came with the list, to be laid
    over this PC's local settings. Empty when there was nothing to read.
    """
    pv_dicts: list
    shared_ok: bool
    path: Optional[Path]
    logs: list
    seed_needed: bool = False
    mtime: Optional[float] = None
    settings: dict = field(default_factory=dict)


def load_shared_pv_list(settings: dict, local_pvs: list) -> PVListChoice:
    """Decide which PV list this session runs on (see PVListChoice)."""
    logs: list[str] = []
    res = shared_pvs.load_shared(
        override=str(settings.get("shared_pv_list_path", "") or ""),
        cached_root=str(settings.get("_shared_pv_root_cache", "") or ""),
        timeout_s=float(settings.get("shared_pv_list_timeout_s", 3.0) or 3.0))

    if not res.root:
        logs.append(f"Shared PV list UNAVAILABLE — {res.detail}. Using this PC's "
                    f"local list ({len(local_pvs)} PV(s)); changes will NOT be "
                    f"shared this session.")
        return PVListChoice(local_pvs, False, None, logs)

    settings["_shared_pv_root_cache"] = res.root

    if not res.existed:
        if local_pvs:
            logs.append(f"No shared PV list yet — seeding {res.path} with this "
                        f"PC's {len(local_pvs)} PV(s).")
            return PVListChoice(local_pvs, True, res.path, logs, seed_needed=True)
        logs.append(f"No shared PV list yet and no local PVs — nothing to seed. "
                    f"Will publish to {res.path} once PVs are added.")
        return PVListChoice(local_pvs, True, res.path, logs)

    if res.pvs is None:
        logs.append(f"Shared PV list UNREADABLE — {res.detail}. Using this PC's "
                    f"local list ({len(local_pvs)} PV(s)); it will NOT be "
                    f"overwritten, so the shared file can be repaired by hand.")
        return PVListChoice(local_pvs, False, res.path, logs)

    shared_settings = shared_settings_subset(res.settings or {})
    if shared_settings:
        logs.append(f"Adopted {len(shared_settings)} shared setting(s) "
                    f"(limits, pacing, defaults) from {res.path}.")

    if not res.pvs and local_pvs:
        # The deployed share copy writes its own empty config, so an empty
        # shared list next to a populated local one is far more likely to be
        # that accident than a deliberate "monitor nothing".
        logs.append(f"Shared PV list at {res.path} is EMPTY while this PC has "
                    f"{len(local_pvs)} PV(s) — keeping the local list and NOT "
                    f"publishing, to avoid wiping everyone's PVs. Delete the "
                    f"shared file if you really want to start over.")
        return PVListChoice(local_pvs, False, res.path, logs,
                            settings=shared_settings)

    # A file written by an older version carries no settings block. Publish this
    # PC's once, so the group has a shared baseline from then on (costs one
    # extra write and one phantom-conflict log elsewhere, once).
    seed = not shared_settings and bool(shared_settings_subset(settings))
    if seed:
        logs.append("Shared file has no settings block yet — publishing this "
                    "PC's limits and pacing as the shared baseline.")
    return PVListChoice(res.pvs, True, res.path, logs, mtime=res.mtime,
                        settings=shared_settings, seed_needed=seed)


class _SharedWriteSignals(QObject):
    done = Signal(object)   # (ok, mtime_or_None, was_stale, error)


def _shared_write_job(sig, path, pv_dicts, settings, host, expect_mtime):
    try:
        mtime, stale = shared_pvs.write_shared(
            path, pv_dicts, settings, host, expect_mtime)
        _safe_emit(sig.done.emit, (True, mtime, stale, ""))
    except Exception as e:  # noqa: BLE001 - reported to the UI, never raised
        _safe_emit(sig.done.emit, (False, None, False, str(e)))


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
    # Take part in the frozen-value ("not updating") check. Off for PVs that
    # legitimately hold one value for hours — switch positions, setpoints,
    # enable flags — which would otherwise be reported as stuck for ever.
    frozen_check: bool = True
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
            "frozen_check": self.frozen_check,
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
            frozen_check=bool(d.get("frozen_check", True)),
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
    # Severity of the latest reading taken straight from the thresholds, with no
    # debounce/settle state behind it. Kept fresh on every poll so the table can
    # show a truthful state while monitoring is stopped and the evaluator (which
    # owns `alert`) is not running.
    live_level: Optional[AlertLevel] = None
    # Timestamp of the newest archive sample actually seen at the last poll
    # (0 = the window held nothing). last_update_ns falls back to "now" so the
    # Updated column always shows something; this one never does, so the
    # freshness checks below use it.
    data_ts_ns: int = 0
    # Frozen-value check (see alerting.detect_frozen): data keeps arriving but
    # the reading never changes, or the newest sample itself stopped advancing.
    # Either way the value on screen is not live.
    frozen: bool = False
    frozen_since_ns: int = 0
    frozen_span_s: float = 0.0
    frozen_bounded: bool = False
    frozen_reason: str = ""
    # One notification per freeze episode (and one when it clears).
    frozen_notified: bool = False

    def display_level(self, monitoring: bool = True):
        """AlertLevel for colouring, or None for NODATA."""
        if self.current_value is None:
            return None
        if not monitoring:
            # Evaluator idle: `alert` is frozen at whatever it was when
            # monitoring stopped, so report the raw severity instead.
            return self.live_level if self.live_level is not None \
                else self.alert.level
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
                # last_ts is reported raw (0 = the window held no sample): the
                # caller needs to tell "the archiver has nothing newer" from
                # "we asked just now", which a fallback to `end` would hide.
                return (val, units, last_ts, err, rejected, raw_val)
            except Exception as e:  # noqa: BLE001 - one bad PV can't kill the pass
                return (None, "", 0, str(e), 0, None)

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
                 ranges: dict, max_points: int, workers: int = 24):
        super().__init__()
        self._sig = sig
        self._names = names
        self._start = start_ns
        self._end = end_ns
        self._timeout = timeout
        self._ranges = ranges
        self._max_points = max(10, max_points)
        self._workers = max(1, workers)

    def run(self):
        # PVs are fetched concurrently and each PV's window is itself chunked
        # into <=1 h requests, so the two multiply. Keep the product inside the
        # HTTP connection pool (64): above it every extra request evicts a
        # pooled connection and pays a fresh TLS handshake.
        pv_workers = min(self._workers, max(1, len(self._names)))
        chunk_workers = max(1, min(4, 48 // pv_workers))

        def fetch_one(name):
            try:
                return name, api.cpva_fetch_samples_chunked(
                    name, self._start, self._end, self._timeout,
                    max_workers=chunk_workers)
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
        # PV concurrency follows the poll setting (Concurrent fetches). At the
        # old fixed 4 the launch backfill of ~30 PVs took ~8 s of empty graph;
        # at the poll default it is ~2 s, and the archiver is the same server
        # that already takes the poll pass at that rate.
        out = {}
        with ThreadPoolExecutor(max_workers=pv_workers) as ex:
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


FROZEN_LABEL = "not updating"

# Said of the program itself rather than of a PV: the numbers on screen are the
# ones from the last pass that worked, and no newer pass has landed since. Kept
# apart from FROZEN_LABEL ("this PV's reading is dead") because the cure is
# different — one is a sensor/archiver fault, the other means this program has
# stopped reading and everything it shows is out of date.
NOT_REFRESHED_LABEL = "not refreshed"


def _frozen_tooltip(rt: "PVRuntime") -> str:
    """Why this PV is flagged as not updating, spelled out for the table."""
    lines = [f"⚠ NOT UPDATING — {rt.frozen_reason}."]
    if rt.frozen_since_ns:
        since = api.ns_to_prague(rt.frozen_since_ns).strftime("%d.%m. %H:%M:%S")
        if rt.frozen_bounded:
            lines.append(f"Last real change: {since}.")
        else:
            lines.append(f"Already at this value at {since}, the oldest data "
                         "kept here — the freeze may well be older.")
    lines.append("The archiver keeps answering, but the reading behind it has "
                 "stopped moving, so the value shown is probably not live and "
                 "any alert about it is based on old data.")
    lines.append("If this PV is genuinely constant for hours (a switch, a "
                 "setpoint), untick 'Report this PV as not updating…' in "
                 "Edit PV.")
    return "\n".join(lines)


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
        # Mirrors MonitorWidget._monitoring; only affects how the State cell is
        # rendered (values are polled either way).
        self.monitoring = False
        # Mirrors MonitorWidget._refresh_bad: the program has stopped reading,
        # so every cell in the table is a leftover from the last pass that
        # worked. Set by the window's refresh watchdog.
        self.stale = False
        self.stale_since = ""     # clock time of that last working pass
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
            if col == COL_STATE:
                parts = []
                if self.stale:
                    parts.append(
                        "This program has stopped reading. Everything in this "
                        "row is what it read at "
                        f"{self.stale_since or 'the last working pass'} — not "
                        "what the PV is doing now.")
                if rt is not None and rt.frozen:
                    parts.append(_frozen_tooltip(rt))
                if pv.enabled and not self.monitoring:
                    parts.append("Monitoring is stopped — values are still "
                                 "read and shown, but nothing is evaluated "
                                 "against the limits and no alerts are sent.")
                if parts:
                    return "\n\n".join(parts)
            if col == COL_UPDATED and rt is not None:
                if rt.data_ts_ns:
                    tip = ("Newest sample in the archive: "
                           + api.ns_to_prague(rt.data_ts_ns)
                           .strftime("%Y-%m-%d %H:%M:%S"))
                else:
                    tip = ("The archiver returned no sample at the last poll — "
                           "this is the time of that attempt, not of any data.")
                if rt.frozen:
                    tip += "\n\n" + _frozen_tooltip(rt)
                return tip
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
            if rt and rt.frozen:
                tip += f"\n⚠ Not updating: {rt.frozen_reason}"
            if rt and rt.last_error:
                tip += f"\nLast error: {rt.last_error}"
            return tip

        level = rt.display_level(self.monitoring) if rt else None
        bad = bool(rt and rt.bad_data and level is None and pv.enabled)
        # Data arrives but is not live — outranks the level in the State cell,
        # because a limit verdict on a frozen reading means nothing.
        frozen = bool(rt and rt.frozen)
        # Reading is live but nothing is watching it — flag that in the cell.
        stopped = not self.monitoring and pv.enabled

        if role == Qt.BackgroundRole and col == COL_STATE:
            # Same paint for both "not live" states, since they mean the same
            # thing to whoever is looking: do not trust this cell.
            if self.stale or frozen:
                return QColor(FROZEN_COLOR)
            if bad:
                return QColor(BADDATA_COLOR)
            if level is None:
                return QColor(NODATA_COLOR)
            if stopped:
                return QColor(STOPPED_COLOR)
            return _STATE_BG[level]
        if role == Qt.ForegroundRole and col == COL_STATE:
            if self.stale or level in (AlertLevel.WARNING, AlertLevel.ALARM) \
                    or level is None or stopped or frozen:
                return QColor("white")
            return QColor(SUCCESS)
        # The value itself is real but no longer moving: italics mark it as
        # "last known", the tooltip says since when.
        if role == Qt.FontRole and frozen and col in (COL_VALUE, COL_UPDATED):
            f = QFont()
            f.setItalic(True)
            return f

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
                # Nothing has been read for a while: the cell must not keep
                # saying "ok", which reads as "checked just now and fine".
                if self.stale:
                    return NOT_REFRESHED_LABEL
                if frozen:
                    # Shown for disabled PVs too: a stuck sensor is worth
                    # seeing whether or not this PV may raise alerts.
                    text = FROZEN_LABEL
                elif not pv.enabled:
                    return "off"
                elif bad:
                    text = "bad data"
                elif level is None:
                    text = "no data"
                else:
                    text = level.label.lower()
                # Stopped: the reading is real, but no one is evaluating it.
                return f"⏸ {text}" if stopped else text
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
        self.frozen_chk = QCheckBox(
            "Report this PV as not updating when its value never changes")
        self.frozen_chk.setStyleSheet(_CHK_STYLE)
        self.frozen_chk.setToolTip(
            "On (default): if this PV keeps returning exactly the same reading "
            "for longer than 'Not updating after' in Settings, the State column "
            "shows 'not updating' and an alert says the value is no longer "
            "live. Catches a dead sensor or stuck IOC, which otherwise looks "
            "like a perfectly steady value.\n"
            "Turn it off for PVs that really do hold one value for hours — "
            "switch positions, setpoints, enable flags.")
        self.frozen_chk.setChecked(pv.frozen_check)
        form.addRow("", self.frozen_chk)
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
        pv.frozen_check = self.frozen_chk.isChecked()
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


#  The one thing about the Webex bot nobody can guess, so it is written on screen in
#  both places the Webex settings can appear: the editable group box (a source run) and
#  the read-only summary of the built-in channels (the deployed build). Webex shows a
#  bot ONLY the messages that @mention it as soon as a space has more than two people
#  in it — the API answers 403 for anything else — so a command typed without the tag
#  never reaches the app, and from the room that is indistinguishable from a dead bot.
_WEBEX_MENTION_HINT = (
    "Talking to the bot: in a room with other people in it every command has to "
    "start by tagging the bot — “@Diagnostics /status”. Webex shows a bot only the "
    "messages that mention it, so an untagged command never arrives at all; the bot "
    "is not ignoring you. Pick the name from the list Webex offers while you type "
    "“@” — a name merely typed out does not count as a mention. In a one-to-one chat "
    "with the bot the tag is not needed. Send “/help” in the room for the full list "
    "of commands."
)


def _webex_mention_hint() -> QLabel:
    lbl = QLabel(_WEBEX_MENTION_HINT)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color:#777; font-size:11px;")
    return lbl


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
        # Wide enough for the widest group, so nothing needs horizontal
        # scrolling to be read or clicked (at 620 the form was ~300 px wider
        # than its viewport and the rightmost buttons were unreachable).
        self.resize(960, 860)
        self._win = parent
        s = parent.settings
        # Channel settings supplied by the build (see notify_provision): they are
        # in `s` and in use, but this dialog must neither show nor save them.
        self._prov = notify_provision.load()

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
            "the bot only sends alerts and never reads messages.\n\n"
            "In a room with other people in it every command must start by "
            "tagging the bot (@Diagnostics /status) — Webex does not show a bot "
            "any other message.")
        self.webex_cmds.setChecked(bool(s.get("webex_commands_enabled", True)))
        wf.addRow("", self.webex_cmds)
        # Not decoration: without the tag the command never reaches the bot at
        # all, and from the room it looks identical to a dead bot.
        wf.addRow("", _webex_mention_hint())
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

        # Provisioned build: keep the groups alive (their widgets back _save and
        # the test buttons) but take them off screen and show a summary instead.
        if self._prov:
            for w in (self.teams_en, self.email_en, self.webex_en):
                w.setVisible(False)
            for grp in (teams, email, webex):
                grp.setVisible(False)
            lay.insertWidget(1, self._build_provisioned_box())

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
        self.settle.setValue(float(s.get("settle_minutes", 7.0)))
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
            "Can't show more than 'History kept' holds. This is also the span "
            "loaded from the archiver at launch — older data comes only when "
            "asked for (widen this, or right-click the graph → Load older "
            "data). Range 1–10080 min.")
        self.graph_win.setValue(int(s["graph_window_minutes"]))
        form.addRow("Graph window (min)", self.graph_win)
        self.frozen_en = QCheckBox("Flag PVs whose value never changes")
        self.frozen_en.setStyleSheet(_CHK_STYLE)
        self.frozen_en.setToolTip(
            "Watch for PVs that keep answering with exactly the same reading, "
            "or whose newest archived sample stops advancing. Such a PV is not "
            "live even though nothing else looks wrong: its State column shows "
            "'not updating' and any limit alert about it says the value is old. "
            "Individual PVs can opt out in Edit PV.")
        self.frozen_en.setChecked(bool(s.get("frozen_check_enabled", True)))
        form.addRow("", self.frozen_en)
        self.frozen_after = _NoWheelSpinBox(); self.frozen_after.setRange(5, 10080)
        self.frozen_after.setToolTip(
            "How long a reading may stay at exactly the same value before the "
            "PV is reported as not updating. Keep it well above how long the "
            "value can genuinely sit still — 120 min suits temperatures and "
            "pressures. Range 5–10080 min (one week).")
        self.frozen_after.setValue(int(s.get("frozen_after_minutes", 120)))
        form.addRow("Not updating after (min)", self.frozen_after)
        self.frozen_alert = QCheckBox("Send an alert when a PV stops updating")
        self.frozen_alert.setStyleSheet(_CHK_STYLE)
        self.frozen_alert.setToolTip(
            "Send one notification when a PV stops updating and one when it "
            "starts changing again (only for PVs with alerting on). When off, "
            "it is only shown in the table. Never repeats — this is a data "
            "fault, not a value excursion.")
        self.frozen_alert.setChecked(bool(s.get("frozen_alert_enabled", True)))
        form.addRow("", self.frozen_alert)
        for w in (self.frozen_after, self.frozen_alert):
            w.setEnabled(self.frozen_en.isChecked())
            self.frozen_en.toggled.connect(w.setEnabled)
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

        # --- Shared PV list -------------------------------------------------
        shg = QGroupBox("Shared PV list and settings (network share)")
        shg.setStyleSheet(_GROUP_STYLE)
        shl = QFormLayout(shg)

        self.shared_en = QCheckBox("Keep the PV list and settings on the network share")
        self.shared_en.setStyleSheet(_CHK_STYLE)
        self.shared_en.setToolTip(
            "When on, every copy of Diagnostic reads the same PV list — with its "
            "limits, rules and valid ranges — plus the shared settings (poll "
            "pacing, debounce, defaults, watchdog, graph windows) from the scratch "
            "Software share at startup, and publishes changes back to it. Nothing "
            "has to be set up twice.\n\n"
            "Not shared: the share location and timeout below (per-PC), and the "
            "notification channels, which come from the build itself. "
            "Off = use only this PC's local list and settings.")
        self.shared_en.setChecked(bool(s.get("shared_pv_list_enabled", True)))
        shl.addRow("", self.shared_en)

        win = self._win
        in_use = win.shared_file if win.shared_ok else None

        prow = QHBoxLayout()
        self.shared_path = QLineEdit(s.get("shared_pv_list_path", ""))
        # The resolved path goes in the placeholder rather than a label: a UNC
        # path is one unbreakable token, so a word-wrapped label reports its full
        # width as its size hint and forces the whole dialog wider than its
        # viewport (which pushed Browse… off-screen). A placeholder does not
        # affect the field's size hint.
        self.shared_path.setPlaceholderText(
            f"autodetected: {in_use}" if in_use
            else "blank = autodetect the scratch share")
        self.shared_path.setToolTip(
            "Folder (or full .json path) holding the shared PV list. Leave blank "
            "to autodetect the scratch Software share. Set it only if "
            "autodetection picks the wrong host. Takes effect after a restart."
            + (f"\n\nCurrently in use: {in_use}" if in_use else ""))
        prow.addWidget(self.shared_path, 1)
        shared_browse = QPushButton("Browse…")
        shared_browse.setStyleSheet(SECONDARY_STYLE)
        shared_browse.setToolTip("Pick the folder that holds the shared PV list.")
        shared_browse.clicked.connect(self._browse_shared_path)
        prow.addWidget(shared_browse)
        shl.addRow("Location", prow)

        self.shared_timeout = _NoWheelDoubleSpinBox()
        self.shared_timeout.setRange(0.5, 30.0)
        self.shared_timeout.setDecimals(1)
        self.shared_timeout.setSingleStep(0.5)
        self.shared_timeout.setToolTip(
            "How long startup may spend reaching the share before falling back "
            "to this PC's local list. An unreachable network host can take ~48 s "
            "to time out on its own, so this cap is what keeps launch quick. "
            "Raise it if the share is reachable but slow.")
        self.shared_timeout.setValue(
            float(s.get("shared_pv_list_timeout_s", 3.0)))
        shl.addRow("Startup timeout (s)", self.shared_timeout)

        # Only warn about the abnormal case. When sharing is active the
        # placeholder above already names the file in use, so a confirmation
        # line here would just be noise.
        if win.settings.get("shared_pv_list_enabled", True) and not win.shared_ok:
            warn = QLabel("Read-only this session — the shared list could not be "
                          "read at startup, so PV changes stay on this PC. "
                          "See the Log tab for the reason.")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color:{WARN_COLOR}; font-weight:600;")
            shl.addRow("", warn)
        lay.addWidget(shg)

        lay.addStretch(1)

        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        outer.addWidget(self.test_status)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setStyleSheet(_BTN_SUCCESS)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _build_provisioned_box(self) -> QGroupBox:
        """Read-only stand-in for the Teams / Email / Webex sections when the
        channels come baked into the build."""
        box = QGroupBox("Notification channels (built into this version)")
        box.setStyleSheet(_GROUP_STYLE)
        v = QVBoxLayout(box)
        head = QLabel("Alert channels and their credentials ship with this "
                      "build, so they cannot be read or changed here. To change "
                      "them, edit them in a source run, re-bake "
                      "(python notify_provision.py bake) and rebuild.")
        head.setWordWrap(True)
        v.addWidget(head)
        for line in notify_provision.describe(self._prov):
            lbl = QLabel("• " + line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-weight:600;")
            v.addWidget(lbl)
        # The Webex group box is hidden in a provisioned build, so this is the only
        # place a deployed user can read the mention rule.
        if self._prov.get("webex_commands_enabled") or self._prov.get("webex_rooms"):
            v.addWidget(_webex_mention_hint())
        row = QHBoxLayout()
        for text, slot in (("Send test to Teams", self._test_teams),
                           ("Send test email", self._test_email),
                           ("Send test to Webex", self._test_webex)):
            b = QPushButton(text)
            b.setStyleSheet(_BTN_PRIMARY)
            b.setToolTip("Send a test message now with the built-in settings, "
                         "to confirm this PC can reach the service.")
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        return box

    def _browse_shared_path(self):
        start = self.shared_path.text().strip()
        d = QFileDialog.getExistingDirectory(
            self, "Select the folder holding the shared PV list", start)
        if d:
            self.shared_path.setText(str(Path(d)))

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
        s["alert_plot_hours"] = self.plot_hours.value()
        # Channels: skipped entirely when they are provisioned by the build —
        # re-saving them would DPAPI-encrypt the baked secrets for this account
        # only and break every other PC running the same build.
        if not self._prov:
            # channels
            s["teams_enabled"] = self.teams_en.isChecked()
            s["email_enabled"] = self.email_en.isChecked()
            s["webex_enabled"] = self.webex_en.isChecked()
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
        s["frozen_check_enabled"] = self.frozen_en.isChecked()
        s["frozen_after_minutes"] = self.frozen_after.value()
        s["frozen_alert_enabled"] = self.frozen_alert.isChecked()
        s["valid_min_default"] = self.valid_min.value()
        s["valid_max_default"] = self.valid_max.value()
        s["start_monitoring_on_launch"] = self.autostart.isChecked()
        # shared PV list
        s["shared_pv_list_enabled"] = self.shared_en.isChecked()
        s["shared_pv_list_path"] = self.shared_path.text().strip()
        s["shared_pv_list_timeout_s"] = float(self.shared_timeout.value())
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
import matplotlib.colors as mcolors  # noqa: E402

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


@dataclass
class ChartSeries:
    """One curve of a rendered chart: which PV, and how to sanity-filter it."""
    pv_name: str
    display_name: str
    thresholds: Optional[Thresholds] = None   # drawn only on a single-curve chart
    vmin: Optional[float] = None
    vmax: Optional[float] = None


def _fetch_series(series: ChartSeries, start_ns: int, end_ns: int,
                  timeout: float):
    """Fetch one PV's numeric samples for the window. Returns (xs, ys, units)."""
    samples = api.cpva_fetch_samples_chunked(series.pv_name, start_ns, end_ns,
                                             timeout)
    xs, ys, units = [], [], ""
    for s in samples:
        v = api.cpva_decode_value(s)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        fv = float(v)
        if _out_of_range(fv, series.vmin, series.vmax):
            continue
        t = s.get("time")
        if isinstance(t, (int, float)):
            xs.append(api.ns_to_prague(int(t)))
            ys.append(fv)
            u = api.cpva_decode_units(s)
            if u:
                units = u
    return xs, ys, units


def render_chart_png(series: list[ChartSeries], start_ns: int, end_ns: int,
                     timeout: float, window_label: str = "",
                     yaxis: Optional[tuple] = None,
                     stale_after_s: float = 0.0,
                     out_info: Optional[dict] = None) -> bytes | None:
    """Render one PNG with a curve per PV over [start_ns, end_ns].

    Worker-thread safe: builds its own Figure and uses the non-Qt Agg canvas,
    rendering to in-memory PNG bytes (no temp file, no shared matplotlib state).
    Threshold lines are drawn only for a single-PV chart — on an overlay they
    would belong to no visible curve. Returns None when no PV had any data.

    `stale_after_s` > 0 asks the picture to say whether it is current: if the
    newest point plotted falls short of the end of the window by more than that
    many seconds, a warning is printed into the corner of the graph. Pass 0 for
    a window that ends in the past, where a curve stopping at the right-hand
    edge is simply what was asked for. `out_info`, if given, is filled with the
    same finding (`stale_s`, `newest_ns`, `note`) so the covering message can
    repeat it in words.
    """
    fetched = []
    for s in series:
        xs, ys, units = _fetch_series(s, start_ns, end_ns, timeout)
        if xs:
            fetched.append((s, xs, ys, units))
    if not fetched:
        return None

    fig = Figure(figsize=(8, 4), dpi=110)
    ax = fig.add_subplot(111)
    cmap = matplotlib.colormaps.get_cmap("tab10")
    all_units = {u for _, _, _, u in fetched if u}
    single = len(fetched) == 1
    for i, (s, xs, ys, units) in enumerate(fetched):
        label = s.display_name
        if units and len(all_units) > 1:
            label += f" [{units}]"    # mixed units: say which curve is which
        # Same unit-family styling as the live graph: temperature solid,
        # pressure dashed.
        ax.plot(xs, ys, drawstyle="steps-post", linewidth=1.5,
                linestyle=GraphPanel._unit_linestyle(units),
                color=PRIMARY if single else cmap(i % 10), label=label)
    if single and fetched[0][0].thresholds is not None:
        thr = fetched[0][0].thresholds
        for val, ls, lw in ((thr.warn_low, "--", 1.0), (thr.warn_high, "--", 1.0),
                            (thr.alarm_low, "-.", 1.5), (thr.alarm_high, "-.", 1.5)):
            if val is not None:
                ax.axhline(val, linestyle=ls, linewidth=lw, alpha=0.7,
                           color=ALARM_COLOR if ls == "-." else WARN_COLOR)

    names = ", ".join(s.display_name for s, _, _, _ in fetched)
    if len(names) > 70:
        names = f"{len(fetched)} PVs"
    ax.set_title(f"{names}  ({window_label})" if window_label else names)
    ax.set_ylabel(next(iter(all_units)) if len(all_units) == 1 else "")
    if not single:
        ax.legend(loc="best", fontsize=8)
    if yaxis:
        ax.set_ylim(yaxis[0], yaxis[1])
    ax.grid(True, alpha=0.3)
    # How current is this picture? The curve simply stopping is otherwise
    # indistinguishable from a flat reading, and on a phone-sized image nobody
    # reads the x axis to find out.
    newest_ns = 0
    for _s, xs, _ys, _u in fetched:
        try:
            newest_ns = max(newest_ns, int(max(xs).timestamp() * 1e9))
        except (ValueError, OverflowError, OSError):
            pass
    lag_s = (end_ns - newest_ns) / 1e9 if newest_ns else 0.0
    if stale_after_s > 0 and newest_ns and lag_s > stale_after_s:
        note = (f"NOT CURRENT - newest data "
                f"{api.ns_to_prague(newest_ns).strftime('%d.%m. %H:%M')}, "
                f"{fmt_duration(lag_s)} before the end of the window")
        # No emoji: the bundled font has no glyph for one and it would render
        # as an empty box.
        ax.text(0.99, 0.02, note, transform=ax.transAxes, ha="right",
                va="bottom", fontsize=8, color=ALARM_COLOR,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          edgecolor=ALARM_COLOR, alpha=0.85))
        if out_info is not None:
            out_info["note"] = note
    if out_info is not None:
        out_info["newest_ns"] = newest_ns
        out_info["stale_s"] = lag_s
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=api.TZ_PRAGUE))
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    return buf.getvalue()


def render_pv_png(pv_name: str, display_name: str, hours: float,
                  thr: Thresholds, timeout: float,
                  vmin=None, vmax=None,
                  stale_after_s: float = 0.0) -> bytes | None:
    """Fetch the last `hours` h from CPVA and render a value+threshold PNG.

    This window always ends now, so the plot is marked when its newest point is
    older than `stale_after_s` — an alert whose evidence stops an hour short of
    the present must show that on the picture."""
    end = api.now_ns()
    start = end - int(hours * 3600 * 1e9)
    return render_chart_png(
        [ChartSeries(pv_name, display_name, thr, vmin, vmax)],
        start, end, timeout, window_label=f"last {hours:g} h",
        stale_after_s=stale_after_s)


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


class _GraphPVList(QListWidget):
    """Scrollable list of the curves the graph is drawing, shown beside it.

    Stands in for the in-plot legend, which matplotlib re-places on every
    redraw. Emits `hovered` with the row's PV name so the graph can highlight
    that curve, and with None over blank space or once the mouse leaves.
    """

    hovered = Signal(object)

    _STYLE = """
    QListWidget {
        background: #ffffff;
        color: #222222;
        border: 1px solid #cccccc;
        font-size: 11px;
    }
    QListWidget::item { padding: 2px 3px; }
    QListWidget::item:hover { background: #d8e8ff; color: #111111; }
    """

    def __init__(self):
        super().__init__()
        self.setStyleSheet(self._STYLE)
        self.setMouseTracking(True)      # required for hover tracking
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideRight)
        self.setMinimumWidth(110)

    def _pv_at(self, ev):
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        it = self.itemAt(pos)
        return it.data(Qt.UserRole) if it is not None else None

    def mouseMoveEvent(self, ev):
        super().mouseMoveEvent(ev)
        self.hovered.emit(self._pv_at(ev))

    def leaveEvent(self, ev):
        super().leaveEvent(ev)
        self.hovered.emit(None)


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

        # Banner over the graph, hidden while all is well. A curve that simply
        # stops looks exactly like a steady reading, so when this program stops
        # reading, the graph has to say so in words. A plain label rather than
        # text drawn into the figure: it survives every redraw and cannot upset
        # the blitted crosshair.
        self.lbl_stale = QLabel("")
        self.lbl_stale.setWordWrap(True)
        self.lbl_stale.setStyleSheet(
            f"QLabel {{ color: white; background: {ALARM_COLOR}; "
            f"padding: 4px 8px; border-radius: 3px; font-weight: bold; }}")
        self.lbl_stale.hide()
        lay.addWidget(self.lbl_stale)

        self.fig = Figure(figsize=(6, 3), dpi=96)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = _LightNavToolbar(
            self.canvas, self,
            on_user_view=self._capture_user_view, on_home=self.reset_zoom)
        lay.addWidget(self.toolbar)

        # The plotted-PV list sits left of the plot, in a splitter so long names
        # can be given more room; all the stretch stays with the canvas.
        st = getattr(self._win, "settings", None) or {}
        self._pv_panel_on = bool(st.get("graph_pv_panel", True))
        self.pv_list = _GraphPVList()
        self.pv_list.setToolTip(
            "The PVs currently drawn in the graph, with each one's line colour. "
            "Hover a name to highlight its curve and fade the rest. Right-click "
            "the graph to hide this list and use an in-plot legend instead.")
        self.pv_list.hovered.connect(self._on_pv_hover)
        self.pv_list.setVisible(self._pv_panel_on)
        self._hover_pv = None     # PV name whose curve is highlighted, or None
        self._hover_pending = False   # a highlight repaint is already queued
        self._curve_rows = []     # [(pv_name, display_name, colour, Line2D), …]
        self._panel_sig = None    # last list contents, so a redraw that changed
                                  # nothing doesn't rebuild (and drop) the rows
        # Shape of the picture the last full redraw drew (curves, units, axes,
        # placement). refresh_data() compares it: unchanged means the existing
        # lines only need their new samples pushed in, which skips rebuilding
        # axes, legend, side list and layout on every poll.
        self._plot_sig = None
        # Set when new samples arrived while the graph was on a hidden tab, so
        # nothing is drawn until it is actually on screen again.
        self._pending_data = False
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.pv_list)
        split.addWidget(self.canvas)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([170, 830])
        lay.addWidget(split, 1)

        # Crosshair cursor: artists are recreated on every redraw (ax.clear()
        # drops them); animated=True keeps them out of the blit background so
        # moving the mouse never re-renders the whole figure.
        self._cross: list = []
        self._extra_axes: list = []   # twin y-axes, one per extra unit
        # Per-plotted-series snap cache for the cursor value box, rebuilt every
        # redraw: [(times_num_np, values_np, color, display_name), ...].
        self._snap_series: list = []
        self._readout_texts: list = []   # cursor box: time header + one line/PV
        self._units = ""
        self._blit_bg = None
        self._mouse_ev = None
        self._mouse_pending = False
        self._grid_on = True
        # Fixed Y range per extra (twin) axis, keyed by its index in
        # self._extra_axes. self._yaxis (above) already covers the main axis.
        self._extra_yaxis: dict[int, tuple] = {}
        # Legend placement (right-click menu / drag), remembered across runs.
        s = getattr(self._win, "settings", None) or {}
        self._legend_loc = str(s.get("graph_legend_loc") or "best")
        anchor = s.get("graph_legend_anchor")
        self._legend_anchor = (float(anchor[0]), float(anchor[1])) \
            if isinstance(anchor, (list, tuple)) and len(anchor) == 2 else None
        self._legend = None       # rebuilt by every redraw()
        self._legend_drag = False  # a left-press landed on the legend
        self._legend_press_at = None   # legend corner (px) when that press began
        self.canvas.mpl_connect("draw_event", self._on_draw)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("figure_leave_event", self._on_leave)
        self.canvas.mpl_connect("button_press_event", self._on_canvas_click)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)

    def _on_combo_changed(self, *_):
        # New selection = different data/scale; a zoom pinned on the previous
        # selection would show a nonsense viewport, so drop it.
        self._user_view = None
        self._hover_pv = None      # the previous view's highlight is meaningless
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
        if event.button == 1:
            # Remember a press that landed on the legend, plus where the legend
            # sat, so the matching release can tell a drag from a plain click.
            self._legend_drag = self._hit_legend(event)
            self._legend_press_at = self._legend_corner() if self._legend_drag \
                else None
            return
        if event.button != 3:   # right-click only
            return
        gui_ev = event.guiEvent
        pos = gui_ev.position().toPoint() if hasattr(gui_ev, "position") \
            else gui_ev.pos()
        global_pos = self.canvas.mapToGlobal(pos)
        hit = self._axis_at(event)
        if hit is None:
            # Right-click inside the plot itself: view options only (there is no
            # axis to configure). Skipped while a toolbar tool is armed, where
            # matplotlib already uses the right button (zoom out / pan).
            mode = str(getattr(self.toolbar, "mode", "") or "")
            if event.inaxes is not None and not mode:
                self._show_plot_menu(global_pos)
            return
        kind, ax = hit
        self._show_axis_menu(kind, ax, global_pos)

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
        self._add_view_actions(menu)
        menu.exec(global_pos)

    def _show_plot_menu(self, global_pos):
        menu = QMenu(self)
        self._add_view_actions(menu)
        menu.exec(global_pos)

    def _add_view_actions(self, menu: QMenu):
        """PV list + grid + legend items, shared by the axis and plot menus."""
        panel = menu.addAction("PV list beside graph")
        panel.setCheckable(True)
        panel.setChecked(self._pv_panel_on)
        panel.toggled.connect(self._set_pv_panel)
        self._add_legend_menu(menu)
        grid_action = menu.addAction("Grid lines")
        grid_action.setCheckable(True)
        grid_action.setChecked(self._grid_on)
        grid_action.toggled.connect(self._set_grid_on)
        menu.addSeparator()
        # Launch only fetches the visible window from the archiver (a wider
        # span costs one request per hour per PV). This is the on-request way
        # to reach further back without changing the window.
        older = menu.addAction("Load older data from archive")
        older.setToolTip("Fetch the full kept history from the archiver so the "
                         "graph can be zoomed or panned further back.")
        older.triggered.connect(lambda: self._win.extend_backfill())

    def _set_grid_on(self, on: bool):
        self._grid_on = on
        self.redraw()

    # --- legend placement -------------------------------------------------

    # Menu label → placement. "best" is matplotlib's own emptiest-corner
    # search, "outside" parks the legend beside the plot (never over data),
    # "off" hides it; "custom" is not offered here — it comes from a drag.
    _LEGEND_CHOICES = (
        ("Auto (avoid data)", "best"),
        ("Upper left", "upper left"),
        ("Upper right", "upper right"),
        ("Lower left", "lower left"),
        ("Lower right", "lower right"),
        ("Center right", "center right"),
        ("Outside, right of plot", "outside"),
        ("Hidden", "off"),
    )

    def _add_legend_menu(self, menu: QMenu):
        # Parented to `menu` (rather than menu.addMenu("Legend")) so the submenu
        # is owned on the C++ side and can't be garbage-collected before exec().
        sub = QMenu("Legend", menu)
        menu.addMenu(sub)
        sub.setToolTipsVisible(True)
        for label, loc in self._LEGEND_CHOICES:
            act = sub.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._legend_loc == loc)
            act.triggered.connect(lambda _c=False, l=loc: self._set_legend_loc(l))
        sub.addSeparator()
        dragged = sub.addAction("Dragged position")
        dragged.setCheckable(True)
        dragged.setChecked(self._legend_loc == "custom")
        dragged.setEnabled(self._legend_anchor is not None)
        dragged.setToolTip("Drag the legend with the left mouse button to set this.")
        dragged.triggered.connect(lambda: self._set_legend_loc("custom"))

    def _set_legend_loc(self, loc: str):
        self._legend_loc = loc
        # Asking for a legend inside the plot means asking for it instead of the
        # side list; "Hidden" only means no legend, so it leaves the list alone.
        if loc != "off":
            self._pv_panel_on = False
            self.pv_list.setVisible(False)
        self._persist_legend()
        self.redraw()

    # --- PV list beside the plot ------------------------------------------

    def _set_pv_panel(self, on: bool):
        self._pv_panel_on = bool(on)
        self.pv_list.setVisible(self._pv_panel_on)
        if not self._pv_panel_on:
            self._hover_pv = None      # nothing left to un-highlight from
        self._persist_legend()
        self.redraw()

    def _on_pv_hover(self, pv_name):
        if pv_name == self._hover_pv:
            return                     # every mouse move fires; ignore repeats
        self._hover_pv = pv_name
        # Repaint once, shortly after the pointer settles: dragging it down the
        # list crosses every row, and one full canvas redraw per row is what
        # made the highlight feel sticky.
        if not self._hover_pending:
            self._hover_pending = True
            QTimer.singleShot(40, self._apply_hover)

    def _apply_hover(self):
        self._hover_pending = False
        self._apply_highlight()

    def _pointer_on_list(self) -> bool:
        """Is the mouse really over the side list right now?

        Checked against the live cursor position rather than the last hover
        event: Qt does not always deliver a leave event (a redraw or a tooltip
        under the pointer can swallow it), and a hover state that outlives the
        pointer would keep every curve faded.
        """
        if not self.pv_list.isVisible():
            return False
        try:
            return self.pv_list.rect().contains(
                self.pv_list.mapFromGlobal(QCursor.pos()))
        except Exception:
            return True

    def _apply_highlight(self):
        """Fade every curve except the hovered one (all plain when none)."""
        if not self._curve_rows:
            return
        target = self._hover_pv
        # Only fade for a pointer that is still on the list and a PV that is
        # still drawn. Without this, a missed leave event or a PV that dropped
        # out of the plot leaves every curve faded — an apparently empty graph.
        if target is not None and (not self._pointer_on_list()
                                   or all(n != target
                                          for n, *_ in self._curve_rows)):
            target = self._hover_pv = None
        for name, _label, _color, line in self._curve_rows:
            hot = target is not None and name == target
            line.set_alpha(1.0 if target is None or hot else self._FADED_ALPHA)
            line.set_linewidth(self._CURVE_LW * (2.0 if hot else 1.0))
            line.set_zorder(8 if hot else 2)
        # Curve properties changed, so the blitted crosshair background is stale.
        self._blit_bg = None
        self.canvas.draw_idle()

    @staticmethod
    def _swatch_icon(color, dashed: bool) -> QIcon:
        """A short line sample in the curve's colour and dash pattern."""
        pm = QPixmap(22, 12)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        pen = QPen(QColor(mcolors.to_hex(color)))
        pen.setWidth(3)
        if dashed:
            pen.setDashPattern([2.0, 1.5])
        p.setPen(pen)
        p.drawLine(1, 6, 21, 6)
        p.end()
        return QIcon(pm)

    def _sync_pv_list(self):
        """Refill the side list from the curves the redraw just drew."""
        if not self._pv_panel_on:
            return
        sig = [(n, lbl, mcolors.to_hex(c), str(ln.get_linestyle()))
               for n, lbl, c, ln in self._curve_rows]
        if sig == self._panel_sig:
            return    # same curves as before: leave the rows (and hover) alone
        self._panel_sig = sig
        self.pv_list.clear()
        if not self._curve_rows:
            empty = QListWidgetItem("No data to plot")
            empty.setFlags(Qt.NoItemFlags)
            self.pv_list.addItem(empty)
            return
        for name, label, color, line in self._curve_rows:
            dashed = str(line.get_linestyle()) not in ("-", "solid")
            it = QListWidgetItem(self._swatch_icon(color, dashed), label)
            it.setData(Qt.UserRole, name)
            it.setToolTip(f"{label}\n{name}")
            self.pv_list.addItem(it)

    def _persist_legend(self):
        s = getattr(self._win, "settings", None)
        if s is None:
            return
        s["graph_pv_panel"] = self._pv_panel_on
        s["graph_legend_loc"] = self._legend_loc
        s["graph_legend_anchor"] = list(self._legend_anchor) \
            if self._legend_anchor else []
        try:
            self._win.persist()
        except Exception:
            pass    # a view preference is never worth an error dialog

    def _draw_legend(self, handles, labels):
        """Build the combined legend per the current placement; None if hidden."""
        if self._pv_panel_on:
            return None      # the side list is showing the same information
        if not handles or self._legend_loc == "off":
            return None
        # Solid white frame: where the legend does land on a trace, it hides it
        # cleanly instead of blending into unreadable mush.
        kw = dict(fontsize=8, framealpha=1.0, facecolor="white",
                  edgecolor="#999999")
        if self._legend_loc == "outside":
            # Anchored in figure coords, with room carved out below, so the
            # outward-shifted twin axes can't push it back over the data.
            leg = self.ax.legend(handles, labels, loc="upper right",
                                 bbox_to_anchor=(0.995, 0.98),
                                 bbox_transform=self.fig.transFigure, **kw)
        elif self._legend_loc == "custom" and self._legend_anchor:
            # borderaxespad=0 makes the anchor the drawn top-left corner exactly,
            # so storing a dragged position and redrawing it is loss-free (the
            # default pad would nudge the legend a little further on every drag).
            leg = self.ax.legend(handles, labels, loc="upper left",
                                 bbox_to_anchor=self._legend_anchor,
                                 bbox_transform=self.ax.transAxes,
                                 borderaxespad=0.0, **kw)
        else:
            loc = self._legend_loc if self._legend_loc != "custom" else "best"
            leg = self.ax.legend(handles, labels, loc=loc, **kw)
        # Above the traces but below the cursor value box (zorder 10), which
        # must stay readable wherever the legend sits.
        leg.set_zorder(9)
        # The right margin is reserved by hand further down in redraw(); letting
        # tight_layout() see the legend as well would fight that.
        leg.set_in_layout(False)
        leg.set_draggable(True, use_blit=False)
        return leg

    def _hit_legend(self, event) -> bool:
        if self._legend is None or event.x is None:
            return False
        try:
            return bool(self._legend.get_window_extent()
                        .contains(event.x, event.y))
        except Exception:
            return False

    def _legend_corner(self):
        """Pixel (x, y) of the legend's top-left corner, or None."""
        if self._legend is None:
            return None
        try:
            bb = self._legend.get_window_extent()
            return (float(bb.x0), float(bb.y1))
        except Exception:
            return None

    def _on_canvas_release(self, event):
        if not self._legend_drag:
            return
        self._legend_drag = False
        was, now = self._legend_press_at, self._legend_corner()
        self._legend_press_at = None
        # A plain click on the legend must not silently switch the placement to
        # "dragged" — only a real move (> 2 px) counts.
        if was is None or now is None or max(abs(now[0] - was[0]),
                                             abs(now[1] - was[1])) <= 2:
            return
        # Every redraw builds a fresh legend, so a dragged one only keeps its
        # spot if we store it — as an axes fraction, which survives resizes.
        try:
            x, y = self.ax.transAxes.inverted().transform(now)
        except Exception:
            return
        self._legend_anchor = (float(x), float(y))
        self._legend_loc = "custom"
        self._persist_legend()

    def _reserve_legend_margin(self):
        """Shrink the axes so an 'outside' legend sits beside the plot."""
        renderer = self.canvas.get_renderer()
        if renderer is None or self._legend is None:
            return
        try:
            w_px = self._legend.get_window_extent(renderer).width
        except Exception:
            return
        fig_w_px = max(self.fig.get_size_inches()[0] * self.fig.dpi, 1.0)
        # The twin axes stack outward to the left of the legend, so their own
        # reservation (see redraw()) has to be added, not replaced.
        extra_px = (self._EXTRA_AXIS_SPACING * (len(self._extra_axes) - 1) + 70) \
            if self._extra_axes else 0
        right = 1.0 - (w_px + 14 + extra_px) / fig_w_px
        self.fig.subplots_adjust(right=max(0.35, min(0.95, right)))

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
        xs, ys = self._series(pv)
        if not xs:
            return
        lines = ax.plot(xs, ys, drawstyle="steps-post", color=color,
                        linestyle=self._unit_linestyle(self._pv_units(pv)),
                        label=pv.display_name, linewidth=self._CURVE_LW)
        # Keep the drawn line so the side list can highlight it on hover.
        self._curve_rows.append((pv.name, pv.display_name, color, lines[0]))
        # Cache the plotted (thinned) series so the cursor value box can snap to
        # each PV's value at the hovered time — reuses the exact drawn data and
        # its curve colour for both the single-PV and All-PVs views.
        self._snap_series.append(
            (np.asarray(mdates.date2num(xs), dtype=float),
             np.asarray(ys, dtype=float), color, pv.display_name))
        if with_thresholds:
            for val, ls, lw in ((pv.warn_low, "--", 1.0), (pv.warn_high, "--", 1.0),
                                (pv.alarm_low, "-.", 1.6), (pv.alarm_high, "-.", 1.6)):
                if val is not None:
                    ax.axhline(val, color=color, linestyle=ls,
                               linewidth=lw, alpha=0.6)

    def _series(self, pv: PVConfig):
        """The PV's plottable history as (times, values); ([], []) if empty."""
        rt = self._win.runtime.get(pv.name)
        if not rt or not rt.history:
            return [], []
        pts = list(rt.history)
        if len(pts) > MAX_GRAPH_POINTS:
            # Thin evenly across the whole history (a plain tail-cut would
            # silently shorten the graph's time span); keep the newest point.
            step = len(pts) / MAX_GRAPH_POINTS
            last = pts[-1]
            pts = [pts[int(i * step)] for i in range(MAX_GRAPH_POINTS)]
            pts[-1] = last
        return [api.ns_to_prague(t) for t, _ in pts], [v for _, v in pts]

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

    # Curve line style per unit family, so a temperature and a pressure curve
    # stay apart even where their colours are close: temperature is drawn
    # solid, pressure dashed. Anything else keeps the solid default.
    _UNIT_LINESTYLES = {"pressure": (0, (5, 3))}

    # Outward spacing (points) between consecutive right-hand (twin) axes.
    _EXTRA_AXIS_SPACING = 55

    # Normal curve width; a curve hovered in the side list is drawn twice this.
    _CURVE_LW = 1.6

    # Alpha of the curves that are *not* hovered. Faint enough to push the
    # hovered one forward, but still clearly drawn, so a highlight that gets
    # stuck can never look like a graph with no data in it.
    _FADED_ALPHA = 0.35

    @classmethod
    def _unit_family(cls, unit: str) -> str:
        """"degC" -> "temperature", "mbar" -> "pressure", … .

        Read straight off the axis-label table, so a unit added there for its
        axis caption is styled too — there is no second list to keep in sync.
        """
        label = cls._UNIT_LABELS.get(cls._unit_key(unit), "")
        return label.split("[")[0].strip().lower()

    @classmethod
    def _unit_linestyle(cls, unit: str):
        return cls._UNIT_LINESTYLES.get(cls._unit_family(unit), "-")

    @classmethod
    def _axis_label(cls, unit: str) -> str:
        return cls._UNIT_LABELS.get(cls._unit_key(unit),
                                    (unit or "").strip() or "value")

    def redraw(self):
        for a in self._extra_axes:
            a.remove()
        self._extra_axes = []
        self._snap_series = []
        self._curve_rows = []
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
            # real units (pressure, flow, …) go to twin axes on the right, and
            # the generic unit-less "value" axis is pushed outermost of all.
            def _axis_rank(k):
                if k in self._TEMP_UNIT_KEYS:
                    return 0
                return 2 if k == "" else 1
            units_order.sort(key=_axis_rank)
            ax_of_unit = {}
            extra_i = 0
            for j, k in enumerate(units_order):
                if j == 0:
                    ax_of_unit[k] = self.ax
                else:
                    tw = self.ax.twinx()
                    # Step every right axis outward by its own index so two or
                    # more of them (e.g. pressure + value) never render on top
                    # of each other; the first sits at the axes edge (offset 0).
                    tw.spines["right"].set_position(
                        ("outward", self._EXTRA_AXIS_SPACING * extra_i))
                    extra_i += 1
                    self._extra_axes.append(tw)
                    ax_of_unit[k] = tw
            for i, p in enumerate(plottable):
                self._plot_one(p, cmap(i % 10), with_thresholds=False,
                               ax=ax_of_unit[self._unit_key(self._pv_units(p))])
            for k, a in ax_of_unit.items():
                a.set_ylabel(self._axis_label(unit_text[k]))
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
        self._legend = self._draw_legend(handles, labels)
        self.ax.grid(self._grid_on, alpha=0.3)
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=api.TZ_PRAGUE))

        self._apply_limits(win_min)
        for lbl in self.ax.get_xticklabels():
            lbl.set_rotation(0)
            lbl.set_ha("center")
        self.fig.tight_layout()
        # tight_layout() doesn't reserve room for a spine shifted outward with
        # set_position(("outward", …)), so extra right axes get clipped/crammed
        # against each other. Carve enough right margin for every twin's offset
        # plus its tick labels.
        n_extra = len(self._extra_axes)
        if n_extra:
            fig_w_px = self.fig.get_size_inches()[0] * self.fig.dpi
            needed_px = self._EXTRA_AXIS_SPACING * (n_extra - 1) + 70
            right_frac = max(0.5, min(0.95, 1.0 - needed_px / max(fig_w_px, 1)))
            self.fig.subplots_adjust(right=right_frac)
        if self._legend_loc == "outside":
            self._reserve_legend_margin()

        self._plot_sig = self._layout_sig()
        self._sync_pv_list()
        # A PV hovered in the list keeps its highlight across the periodic
        # redraws, which replaced the Line2D objects it was applied to.
        if self._hover_pv is not None:
            self._apply_highlight()
        self._make_cursor_artists()
        self._blit_bg = None
        self.canvas.draw_idle()

    def _layout_sig(self):
        """Everything the drawn picture depends on except the sample values.

        Two equal signatures mean the same curves, on the same axes, in the
        same colours and with the same placement — so the figure can be reused.
        """
        sel = self.combo.currentData()
        rows = []
        for p in self._win.pvs:
            if sel is None:
                if not p.show_in_graph:
                    continue
            elif p.name != sel:
                continue
            rt = self._win.runtime.get(p.name)
            if not rt or not rt.history:
                continue        # PVs without data aren't drawn and get no axis
            rows.append((p.name, p.display_name,
                         self._unit_key(self._pv_units(p)),
                         None if sel is None else (p.warn_low, p.warn_high,
                                                   p.alarm_low, p.alarm_high)))
        return (sel, tuple(rows), self._pv_panel_on, self._legend_loc,
                self._grid_on, tuple(sorted(self._extra_yaxis.items())),
                self._yaxis, self._user_view is not None)

    def _apply_limits(self, win_min: float):
        """Autoscale y, then put the axes back on the window the user wants."""
        for a in [self.ax] + self._extra_axes:
            a.relim()
            a.autoscale_view(scalex=False)
        # Any fixed range pinned via the extra-axis context menu wins over
        # autoscale (the main axis fixed range is applied further below).
        for idx, a in enumerate(self._extra_axes):
            if idx in self._extra_yaxis:
                a.set_ylim(*self._extra_yaxis[idx])
        # A view the user zoomed/panned to is pinned and always wins over the
        # rolling window, so periodic redraws never yank the zoom away.
        if self._user_view is not None:
            self.ax.set_xlim(self._user_view[0])
            self.ax.set_ylim(self._user_view[1])
            return
        # Limit x to the configured window if we have data. orig=False gives
        # the unit-converted float date numbers (the original data are
        # datetimes, which can't take a float offset). Twin axes share x, so
        # clamping the main axis clamps them all.
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

    def set_stale_note(self, text: str):
        """Show (or clear) the red banner above the graph. `text` is the reason
        the picture is out of date; empty hides it."""
        if text:
            self.lbl_stale.setText(f"⚠ {NOT_REFRESHED_LABEL.upper()} — {text}")
            self.lbl_stale.show()
        else:
            self.lbl_stale.clear()
            self.lbl_stale.hide()

    def refresh_data(self):
        """Show the samples of the poll that just finished.

        Reuses the drawn figure whenever the picture's shape is unchanged:
        only the curves' data is replaced. A full redraw() rebuilds the axes,
        the legend, the side list and the layout, which is what made the graph
        hitch on every poll — it is kept for the cases that need it (a curve
        appeared or vanished, units changed, the selection changed, …).
        """
        if not self.isVisible():
            self._pending_data = True   # nothing to draw for a hidden tab
            return
        self._pending_data = False
        if self._plot_sig is None or self._layout_sig() != self._plot_sig:
            self.redraw()
            return
        snap = []
        by_name = {p.name: p for p in self._win.pvs}
        for name, label, color, line in self._curve_rows:
            pv = by_name.get(name)
            xs, ys = self._series(pv) if pv is not None else ([], [])
            if not xs:
                self.redraw()      # curve lost its data: shape changed after all
                return
            line.set_data(xs, ys)
            snap.append((np.asarray(mdates.date2num(xs), dtype=float),
                         np.asarray(ys, dtype=float), color, label))
        # The cursor value box reads this cache, and its text artists are keyed
        # by position in it — same curves in the same order, so they still fit.
        self._snap_series = snap
        self._apply_limits(float(self._win.settings["graph_window_minutes"]))
        if self._hover_pv is not None:
            self._apply_highlight()
        self._blit_bg = None
        self.canvas.draw_idle()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._pending_data:
            self.refresh_data()

    # --- crosshair cursor ------------------------------------------------
    _READOUT_LINE_PTS = 14   # vertical step (points) between value-box lines

    def _make_cursor_artists(self):
        # Park the (hidden) vertical line mid-view: axvline takes part in
        # autoscale even when invisible, so the default x=0 would drag a date
        # axis all the way back to 1970.
        x_mid = sum(self.ax.get_xlim()) / 2
        y_mid = sum(self.ax.get_ylim()) / 2
        vl = self.ax.axvline(x_mid, color="#888", linewidth=0.8, linestyle="--",
                             visible=False, animated=True)
        # Value box floating near the cursor: a neutral time header followed by
        # one line per plotted PV, each coloured to its curve. Kept as separate
        # Text artists (one colour each) stacked tightly so they read as a
        # single box; each carries a white square bbox so it stays legible over
        # any trace/grid. Rebuilt every redraw because _snap_series just changed.
        self._readout_texts = []
        specs = [("#111", True)]   # (colour, is_header)
        specs += [(color, False) for (_a, _v, color, _lbl) in self._snap_series]
        for color, is_header in specs:
            ann = self.ax.annotate(
                "", xy=(x_mid, y_mid), xytext=(12, 12),
                textcoords="offset points", fontsize=8, color=color,
                ha="left", va="top", visible=False, animated=True, zorder=10,
                fontweight="bold" if is_header else "normal",
                bbox=dict(boxstyle="square,pad=0.35", fc="#ffffff",
                          ec="#888", alpha=0.92, linewidth=0.6))
            self._readout_texts.append(ann)
        self._cross = [vl] + self._readout_texts

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
        if ev is None or not self._cross or self._legend_drag:
            return   # a legend drag repaints the figure; don't blit over it
        if self._hover_pv is not None:
            self._hover_pv = None      # pointer is on the plot, not on the list
            self._apply_highlight()
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

    @staticmethod
    def _snap_at(arr, vals, x_f: float):
        """Value of the sample nearest to time x_f, or None for an empty series."""
        if arr is None or len(arr) == 0:
            return None
        pos = int(np.searchsorted(arr, x_f))
        if pos <= 0:
            idx = 0
        elif pos >= len(arr):
            idx = len(arr) - 1
        else:
            idx = pos if (arr[pos] - x_f) < (x_f - arr[pos - 1]) else pos - 1
        return float(vals[idx])

    def _draw_cursor(self, ev):
        # Work from pixel coordinates against the main axis: with twin y-axes
        # present, ev.inaxes/ev.ydata belong to the topmost twin, not self.ax.
        if (self._blit_bg is None or ev.x is None or ev.y is None
                or not self.ax.bbox.contains(ev.x, ev.y)
                or not self._readout_texts):
            self._hide_cursor()
            return
        x, y = self.ax.transData.inverted().transform((ev.x, ev.y))
        x, y = float(x), float(y)
        vl = self._cross[0]
        vl.set_xdata([x, x])
        try:
            t_str = mdates.num2date(x, tz=api.TZ_PRAGUE).strftime("%H:%M:%S")
        except Exception:
            t_str = ""
        # Header = time under the cursor; the rest = each PV's snapped value.
        self._readout_texts[0].set_text(t_str)
        for i, (arr, vals, _color, label) in enumerate(self._snap_series):
            v = self._snap_at(arr, vals, x)
            self._readout_texts[i + 1].set_text(
                f"{label} = {v:.4g}" if v is not None else f"{label} = –")

        # Stack the lines into one box just off the cursor, flipped near the
        # right/top edges so it always stays inside the axes.
        lh = self._READOUT_LINE_PTS
        n = len(self._readout_texts)
        xlo, xhi = self.ax.get_xlim()
        ylo, yhi = self.ax.get_ylim()
        right = x > (xlo + xhi) / 2
        top = y > (yhi + ylo) / 2
        dx = -12 if right else 12
        ha = "right" if right else "left"
        # Below the cursor when it's in the upper half, above it otherwise, so
        # the whole stack clears the nearest horizontal edge.
        top_dy = -12 if top else 12 + lh * n
        for i, ann in enumerate(self._readout_texts):
            ann.xy = (x, y)
            ann.set_ha(ha)
            ann.set_va("top")
            ann.set_position((dx, top_dy - i * lh))

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
                 timeout: float, tag: str, vmin=None, vmax=None,
                 stale_after_s: float = 0.0):
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
        self._stale_after_s = stale_after_s

    def run(self):
        png = None
        if self._hours > 0:
            try:
                png = render_pv_png(self._payload.pv_name, self._payload.display_name,
                                    self._hours, self._thr, self._timeout,
                                    self._vmin, self._vmax, self._stale_after_s)
            except Exception:  # noqa: BLE001 - alert must still go out text-only
                png = None
        errors = self._hub.dispatch(self._payload, png)
        _safe_emit(self._sig.done.emit, (self._tag, errors, png is not None))


class _ChartSignals(QObject):
    done = Signal(object)   # ({channel: error}, had_png)


class _ChartWorker(QRunnable):
    """Render a multi-PV chart and fan it out to every channel, off the UI
    thread. Used by the bot's /plot, which can ask for several PVs and an
    arbitrary time window (the alert path always renders one PV, last N h)."""

    def __init__(self, sig: _ChartSignals, hub: NotificationHub,
                 series: list[ChartSeries], start_ns: int, end_ns: int,
                 timeout: float, title: str, window_label: str,
                 body_md: str, yaxis=None, stale_after_s: float = 0.0):
        super().__init__()
        self._sig = sig
        self._hub = hub
        self._series = series
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._timeout = timeout
        self._title = title
        self._window_label = window_label
        self._body_md = body_md
        self._yaxis = yaxis
        self._stale_after_s = stale_after_s

    def run(self):
        png = None
        info: dict = {}
        try:
            png = render_chart_png(self._series, self._start_ns, self._end_ns,
                                   self._timeout, self._window_label,
                                   self._yaxis, self._stale_after_s, info)
        except Exception:  # noqa: BLE001 - the reply must go out text-only
            png = None
        body = self._body_md
        if png is None:
            body += "\n\n_No archived data in that window — text only._"
        elif info.get("note"):
            # Said in the message as well as on the picture: a chat client may
            # show the text before the image has loaded, and the warning is the
            # part that must not be missed.
            body += f"\n\n**⚠ {info['note']}**"
        elif info.get("newest_ns"):
            body += (f"\n\n_Newest data point: "
                     f"{api.ns_to_prague(info['newest_ns']).strftime('%d.%m. %H:%M:%S')}._")
        text = re.sub(r"[*`_]", "", body)
        errors = self._hub.dispatch_chart(self._title, text, body, png)
        _safe_emit(self._sig.done.emit, (errors, png is not None))


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

        # --- shared PV list ------------------------------------------------
        # Resolved here, before _init_runtime() and PVTableModel(self.pvs, …),
        # so __init__ stays linear: no async list swap can race the optional
        # start-monitoring-on-launch below.
        self._shared_backed_up = False
        self._shared_write_fails = 0
        self._shared_timer = QTimer(self)
        self._shared_timer.setSingleShot(True)
        self._shared_timer.timeout.connect(self._flush_shared_write)
        self._shared_sig = _SharedWriteSignals()
        self._shared_sig.done.connect(self._on_shared_write_done)

        if self.settings.get("shared_pv_list_enabled", True):
            choice = load_shared_pv_list(self.settings, cfg["pvs"])
        else:
            choice = PVListChoice(
                cfg["pvs"], False, None,
                ["Shared PV list is switched off in Settings — using this PC's "
                 "local list only."])
        self.shared_ok = choice.shared_ok        # write guard, see _schedule_shared_write
        self.shared_file: Optional[Path] = choice.path
        self._shared_mtime = choice.mtime
        self._shared_dirty = choice.seed_needed
        self._shared_log = choice.logs   # _log() needs self.log from _build_ui

        # Precedence, lowest to highest: DEFAULT_SETTINGS -> this PC's config ->
        # the share -> the build's channels. The share carries the group's
        # limits and pacing so they survive a fresh PC; the build's channels
        # must stay on top (they are the only usable credentials).
        if choice.settings:
            self.settings.update(choice.settings)
            self.settings.update(notify_provision.load())

        self.pvs: list[PVConfig] = [PVConfig.from_dict(d) for d in choice.pv_dicts]
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
        self._poll_started_ns = 0   # when the in-flight pass was dispatched
        self._poll_zombies = 0      # passes written off by the watchdog
        # Oldest archive timestamp the graph backfill has already fetched (0 =
        # nothing fetched yet). Launch only covers the visible graph window;
        # anything older is fetched on request, and this marks where that
        # on-request fetch has to start so nothing is downloaded twice.
        self._backfill_start_ns = 0
        self._backfill_inflight = False
        self._backfill_started_ns = 0   # when the in-flight fetch was dispatched
        # Extra minutes to fetch once the first (visible-window) pass is drawn.
        self._backfill_followup_min = 0.0
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
        # Refresh watchdog — the program's own heartbeat, as opposed to the
        # data watchdog above (which is about the archiver answering). A poll
        # that never comes back leaves every number on screen, in the table and
        # in every /status reply exactly as it was, with nothing saying so.
        # These three fields are what turns that into something the operator
        # and the bot can both see.
        self._last_poll_ok_ns = 0        # when a pass last landed
        self._refresh_bad = False        # True once the "not refreshed" alert fired
        self._refresh_bad_since_ns = 0   # when it was first noticed
        # Memory watch (see MEM_LOG_INTERVAL_MS). The launch reading is the
        # baseline every later one is compared against, so growth over days is
        # a number and not an impression.
        self._mem_timer: Optional[QTimer] = None
        self._mem_start = memstats.read()
        self._mem_start_ns = api.now_ns()
        self._mem_warned_ns = 0

        self.hub = NotificationHub.from_settings(self.settings)
        self.evaluator = AlertEvaluator(self._eval_config())
        self._init_runtime()

        self._build_ui()
        self._prefetch_channels()
        self._start_cmd_listener()
        # Publish only a genuine seed (first run against a share with no list
        # yet). Republishing an unchanged list on every launch would bump the
        # mtime for everyone and trip a phantom conflict warning elsewhere.
        if self._shared_dirty:
            self._schedule_shared_write()

        # The environment variable is how remote_launcher.py asks for monitoring
        # on THIS launch only, without touching the saved setting — so a Webex
        # "/run" comes up armed while opening the app by hand still behaves the
        # way the Settings checkbox says.
        if (self.settings.get("start_monitoring_on_launch")
                or os.environ.get("DIAGNOSTIC_START_MONITORING") == "1"):
            self.toggle_monitoring(True)
        # Reading PVs is independent of the monitoring switch, so the poll loop
        # runs from launch (after the autostart above, which does its own first
        # poll, so the launch never fires two overlapping passes).
        self._start_polling()

    # --- setup ---------------------------------------------------------
    def _eval_config(self) -> EvalConfig:
        s = self.settings
        return EvalConfig(
            debounce_count=int(s["debounce_count"]),
            renotify_cooldown_minutes=float(s["renotify_cooldown_minutes"]),
            recovery_notify=bool(s["recovery_notify"]),
            settle_minutes=float(s.get("settle_minutes", 7.0)),
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
            "Arm/disarm alerting. PVs are polled and displayed at the "
            "configured interval either way; while armed, each reading is also "
            "evaluated against its thresholds and alerts are sent on committed "
            "state changes.")
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

        self._mem_timer = QTimer(self)
        self._mem_timer.timeout.connect(self._log_memory)
        self._mem_timer.start(MEM_LOG_INTERVAL_MS)

        self._update_status()
        for line in self._shared_log:
            self._log(line)
        self._shared_log.clear()
        if self.shared_ok and self.shared_file:
            self._log(f"Loaded {len(self.pvs)} PV(s) — SHARED list: "
                      f"{self.shared_file}")
        else:
            self._log(f"Loaded {len(self.pvs)} PV(s) — LOCAL list: {CONFIG_FILE}. "
                      f"PV changes will NOT be shared this session.")
        self._log(f"Local settings: {CONFIG_FILE}")
        self._log_memory()   # the baseline every later reading is compared to

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
        state = "MONITORING" if self._monitoring else "stopped (reading only)"
        # The share state stays visible, not just logged once at launch: while
        # it reads LOCAL ONLY every PV edit is silently kept off the share.
        if not self.settings.get("shared_pv_list_enabled", True):
            share = "local list"
        elif self.shared_ok:
            share = "shared"
        else:
            share = "LOCAL ONLY (not sharing)"
        # Data faults belong in the always-visible line, not only in the table:
        # a PV that stopped updating is easy to scroll past.
        n_frozen = sum(1 for pv in self.pvs
                       if (rt := self.runtime.get(pv.name)) is not None
                       and rt.frozen)
        frozen = f"  ·  ⚠ {n_frozen} {FROZEN_LABEL}" if n_frozen else ""
        # The program having stopped reading outranks everything else on this
        # line — while it is true, none of the other figures mean anything.
        if self._refresh_bad:
            when = (api.ns_to_prague(self._last_poll_ok_ns).strftime("%H:%M:%S")
                    if self._last_poll_ok_ns else "never")
            frozen = (f"  ·  ⚠ {NOT_REFRESHED_LABEL.upper()} "
                      f"(last read {when})") + frozen
        # Memory belongs in the always-visible line for the same reason as the
        # frozen count: on a program left running for weeks it is the figure
        # nobody thinks to check until the PC will not start anything.
        snap = memstats.read()
        mem = f"  ·  {memstats.short_line(snap)}" if snap is not None else ""
        if snap is not None and snap.sys_commit_pct >= MEM_WARN_PCT:
            mem += "  ⚠"
        self._status_lbl.setText(
            f"{state}  ·  {len(self.pvs)} PV(s)  ·  every "
            f"{self.settings['poll_interval_s']}s  ·  {self._channel_summary()}"
            f"  ·  {share}{frozen}{mem}")
        self._status_lbl.setToolTip(
            "mem — memory this program has been promised by Windows (its "
            "commit size), with the part actually held in RAM in brackets.\n"
            "PC — how much of the whole computer's commit limit (RAM + page "
            "file) is promised to all programs together. When this reaches "
            "100 % nothing new can start on the PC, even if RAM looks free.\n"
            "The Log tab records both every half hour, with the growth since "
            "this program started.")

    def _log_memory(self):
        """Write the memory figures to the log, with the growth since launch.

        Doubles as this program's heartbeat: a line every half hour is proof it
        is still running through a quiet spell with no alerts, and the series
        of lines is the only record that says whether its memory use settles
        (normal) or keeps climbing (a leak worth chasing).
        """
        snap = memstats.read()
        if snap is None:
            return
        since = self._mem_start.proc_commit if self._mem_start else None
        up = fmt_duration((api.now_ns() - self._mem_start_ns) / 1e9)
        self._log(f"{memstats.long_line(snap, since)} Running for {up}.")
        if snap.sys_commit_pct >= MEM_WARN_PCT:
            now = api.now_ns()
            if now - self._mem_warned_ns >= MEM_WARN_REPEAT_NS:
                self._mem_warned_ns = now
                self._log(
                    f"⚠ This PC has promised {snap.sys_commit_pct:.0f}% of its "
                    f"memory limit ({memstats.fmt(snap.sys_commit_limit)}). "
                    "Close what is not needed or restart the PC — near 100 % "
                    "Windows can no longer start new programs.")
        self._update_status()

    def persist(self):
        """Save locally (always, instant) and publish to the share (debounced)."""
        save_config({
            "version": 1,
            "settings": self.settings,
            "pvs": [pv.to_dict() for pv in self.pvs],
        })
        self._schedule_shared_write()

    # --- shared PV list publishing --------------------------------------
    def _schedule_shared_write(self):
        """Mark the shared list dirty and (re)arm the trailing debounce."""
        if not (self.shared_ok and self.shared_file):
            return          # THE WRITE GUARD — see load_shared_pv_list
        self._shared_dirty = True
        self._shared_timer.start(SHARED_WRITE_DEBOUNCE_MS)

    def _flush_shared_write(self, blocking: bool = False):
        """Publish the current list to the share on a background thread."""
        if not (self.shared_ok and self.shared_file and self._shared_dirty):
            return
        # Snapshot on the UI thread so the worker never walks a list that the
        # user is editing underneath it.
        payload = [pv.to_dict() for pv in self.pvs]
        settings = shared_settings_subset(self.settings)
        path, expect = self.shared_file, self._shared_mtime
        self._shared_dirty = False
        if not self._shared_backed_up:
            shared_pvs.backup_once(path)
            self._shared_backed_up = True
        t = threading.Thread(
            target=_shared_write_job, daemon=True, name="shared-pv-write",
            args=(self._shared_sig, path, payload, settings,
                  socket.gethostname(), expect))
        t.start()
        if blocking:
            # Bounded, and the thread is a daemon: a share that has gone away
            # cannot hold the app open.
            t.join(5.0)

    def _on_shared_write_done(self, result):
        ok, mtime, was_stale, err = result
        if ok:
            self._shared_mtime = mtime
            self._shared_write_fails = 0
            if was_stale:
                self._log("Shared PV list had been changed by another PC since "
                          "this copy read it — your version won (last writer "
                          "wins). The previous version is in "
                          f"{shared_pvs.BACKUP_FILENAME}.")
            return
        self._shared_dirty = True       # retry on the next change
        self._shared_write_fails += 1
        self._log(f"Could not publish the shared PV list: {err}")
        if self._shared_write_fails >= 3:
            # Typically a permissions problem or a vanished share; stop retrying
            # rather than logging this every couple of seconds.
            self.shared_ok = False
            self._shared_dirty = False
            self._log("Giving up on the shared PV list for this session — PV "
                      "changes stay local. Check the share and restart.")
            self._update_status()

    def shutdown(self):
        """Called by the main window on close; state is also saved per-change."""
        self.timer.stop()
        if self._mem_timer is not None:
            self._mem_timer.stop()
        self._stop_cmd_listener()
        self.persist()
        self._shared_timer.stop()       # don't let the debounce race the close
        self._flush_shared_write(blocking=True)

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

    def _share_settings_snapshot(self) -> tuple:
        return (str(self.settings.get("shared_pv_list_path", "")),
                bool(self.settings.get("shared_pv_list_enabled", True)))

    def open_settings(self):
        before = self._cmd_settings_snapshot()
        share_before = self._share_settings_snapshot()
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.hub = NotificationHub.from_settings(self.settings)
            self.evaluator = AlertEvaluator(self._eval_config())
            ml = self._history_maxlen()
            for rt in self.runtime.values():
                if rt.history.maxlen != ml:
                    rt.history = deque(rt.history, maxlen=ml)
            self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
            # A widened graph window asks for data further back than launch
            # fetched, so top the history up now.
            self.ensure_window_backfilled()
            # Only restart the Webex command listener when its own settings
            # changed (or it isn't running) — a needless restart re-primes and
            # briefly drops commands for no reason.
            if self._cmd_settings_snapshot() != before or self._cmd_timer is None:
                self._start_cmd_listener()
            self.persist()
            if self._share_settings_snapshot() != share_before:
                # Re-pointing must not publish this copy's in-memory list into a
                # file it has never read — that is exactly the clobber the write
                # guard exists to prevent. Swapping self.pvs live would also race
                # the runtime/model/graph while a poll may be mid-pass, so the
                # new location is picked up on the next launch.
                self.settings["_shared_pv_root_cache"] = ""
                self._shared_timer.stop()
                self._shared_dirty = False
                self.shared_ok = False
                self._log("Shared PV list location changed. Restart Diagnostic "
                          "to load from the new location — until then this copy "
                          "will not publish any PV-list changes.")
            self._update_status()
            self._log("Settings saved.")

    # --- monitoring loop ----------------------------------------------
    def _start_polling(self):
        """Start the always-on poll loop.

        Polling is not part of the monitoring switch: the table and the graph
        show live values whether or not alerting is armed. Only threshold
        evaluation, alert dispatch and the data watchdog follow the switch.
        """
        self.timer.setInterval(int(self.settings["poll_interval_s"] * 1000))
        self.timer.start()
        if not self._monitoring:      # armed launches already did both
            self._backfill_history()
            self._start_poll()

    def toggle_monitoring(self, on: bool):
        # Any explicit start/stop cancels a pending timed auto-resume.
        self._cancel_resume()
        self._monitoring = on
        self.model.monitoring = on
        self.btn_monitor.setChecked(on)
        self.btn_monitor.setText("⏹ Stop monitoring" if on else "▶ Start monitoring")
        self.btn_monitor.setStyleSheet(STOP_BUTTON_STYLE if on else BUTTON_STYLE)
        # Polling itself never stops (see _start_polling): the table keeps
        # showing live values either way. This switch only controls threshold
        # evaluation and alert dispatch.
        if on:
            self._log("Monitoring started — thresholds are now evaluated and "
                      "alerts will be sent.")
            # Pressing Stop then Start is what anyone tries first when the
            # values look stuck, so let it actually cure a wedged pass instead
            # of waiting out the watchdog in _start_poll. A pass that really is
            # still running is discarded by the generation check, so at worst
            # this costs one duplicate fetch.
            self._poll_inflight = False
            self._backfill_history()
            self._start_poll()
        else:
            # Drop any half-finished alert episode so a later start doesn't
            # resume from a state that was never re-evaluated meanwhile.
            for rt in self.runtime.values():
                rt.alert = AlertState()
                rt.notify_status = ""
                rt.notify_error = ""
            self._watchdog_fail_streak = 0
            self._watchdog_bad = False
            self._log("Monitoring stopped — values keep updating, but no "
                      "thresholds are evaluated and no alerts are sent.")
        self.model.refresh_all()
        self._update_status()
        # Tell anyone outside this process — remote_launcher.py waits on exactly
        # this before it answers "running and tracking". Every route into the
        # switch passes through here (button, autostart, /start, auto-resume),
        # so there is one writer and it cannot report a state that is not real.
        write_run_status(monitoring=on)

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
        self._poll_inflight = False   # an explicit poll must never be skipped
        self._start_poll()

    # --- graph history backfill ----------------------------------------
    def _backfill_history(self):
        """Pre-fill each PV's history with archive data covering the *visible*
        graph window, so the plot shows that window right away instead of only
        samples collected since monitoring started.

        Deliberately only the visible window: the archiver serves at most one
        hour per request, so a wider span means one request per hour per PV and
        the graph stayed empty for tens of seconds on every launch. Older data
        is fetched only when asked for -- see extend_backfill().
        """
        self._run_backfill(float(self.settings["graph_window_minutes"]),
                           "Fetching archive history for the graph window…")
        # The "value unchanged" check needs frozen_after_minutes of history to
        # ever fire, which is normally more than the visible window. Fetch that
        # remainder as a follow-up instead of widening the first pass, so the
        # graph appears immediately and the check is armed a few seconds later.
        self._backfill_followup_min = 0.0
        if self.settings.get("frozen_check_enabled", True)                 and any(pv.frozen_check for pv in self.pvs):
            need = float(self.settings.get("frozen_after_minutes", 120))
            if need > float(self.settings["graph_window_minutes"]):
                self._backfill_followup_min = need

    def extend_backfill(self, minutes: Optional[float] = None):
        """Fetch archive data older than what the graph already holds.

        Called when the user asks to see further back (widening the graph
        window, or the graph's own "Load older data" item). Only the span that
        is not covered yet is fetched, so nothing is downloaded twice.
        """
        if minutes is None:
            minutes = float(self.settings["history_minutes"])
        self._run_backfill(minutes,
                           f"Fetching {minutes / 60.0:g} h of older archive "
                           f"data for the graph…", extend=True)

    def ensure_window_backfilled(self):
        """Fetch older data if the visible window now reaches further back than
        what has been fetched so far (the user widened the graph window)."""
        mins = float(self.settings["graph_window_minutes"])
        need = api.now_ns() - int(mins * 60e9)
        if self._backfill_start_ns and need < self._backfill_start_ns:
            self.extend_backfill(mins)

    def _run_backfill(self, minutes: float, message: str, extend: bool = False):
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        if self._backfill_inflight:
            # Same watchdog as _start_poll: a fetch that never reports back
            # would wedge this flag True forever, and from then on the graph
            # could never load history again — every attempt would only repeat
            # the line below. A full history fetch is chunked by the hour and
            # can legitimately run for minutes, so the cut-off is generous.
            if api.now_ns() - self._backfill_started_ns < int(600e9):
                self._log("Archive history is still being fetched — wait for "
                          "it to finish.")
                return
            self._log("⚠ Archive history fetch stalled — starting a fresh one.")
            self._backfill_inflight = False
        # Never keep more than the history buffer can hold.
        minutes = min(float(minutes), float(self.settings["history_minutes"]))
        end = api.now_ns()
        start = end - int(minutes * 60e9)
        if extend:
            # Only the still-missing older part: the newer part is already in
            # memory, and re-fetching it would cost the same as the first pass.
            end = self._backfill_start_ns or end
            if start >= end:
                self._log("The graph already holds that much history.")
                return
        ranges = {pv.name: self._valid_range(pv) for pv in self.pvs}
        sig = _BackfillSignals(self)
        sig.log.connect(self._log)
        sig.done.connect(sig.deleteLater)   # see _start_poll
        sig.done.connect(self._on_backfill)
        self._backfill_sig = sig           # keep signals alive while running
        self._backfill_inflight = True
        self._backfill_started_ns = api.now_ns()
        self._backfill_start_ns = min(start, self._backfill_start_ns or start)
        QThreadPool.globalInstance().start(_BackfillWorker(
            sig, names, start, end, float(self.settings["http_timeout_s"]),
            ranges, self._history_maxlen(),
            int(self.settings.get("poll_max_workers", 24))))
        self._log(message)

    def _on_backfill(self, results: dict):
        self._backfill_inflight = False
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
            # Thin the combined series evenly to what the buffer holds. Handing
            # an over-long list to deque(maxlen=...) would keep only its tail,
            # i.e. silently drop exactly the older data just fetched.
            merged = older + list(rt.history)
            ml = rt.history.maxlen
            if ml and len(merged) > ml:
                step = len(merged) / ml
                newest = merged[-1]
                merged = [merged[int(i * step)] for i in range(ml)]
                merged[-1] = newest
            rt.history = deque(merged, maxlen=ml)
            filled += 1
        if filled:
            self._log(f"Backfilled graph history for {filled} PV(s).")
            self.graph.redraw()
        # Arm the frozen check right after the graph is up (see
        # _backfill_history). Deferred by a tick so this pass is fully settled.
        if self._backfill_followup_min:
            mins = self._backfill_followup_min
            self._backfill_followup_min = 0.0
            QTimer.singleShot(0, lambda: self.extend_backfill(mins))

    def _start_poll(self):
        # Runs on every tick, including the ones that give up below: this is the
        # heartbeat that notices the values have stopped moving.
        self._check_refresh_health()
        monitored = {pv.name for pv in self.pvs}
        names = [pv.name for pv in self.pvs]
        if not names:
            return
        # If the previous pass is still fetching, let it finish instead of
        # invalidating it — otherwise a pass slower than the poll interval
        # means no result ever lands and the table stays on "no data".
        if self._poll_inflight:
            # Watchdog — same failure and the same cure as the Webex command
            # listener (see _poll_commands). A pass that never reports back
            # (thread-pool starvation, a network stall over a screen lock or
            # a sleeping WiFi link) wedges this flag True forever: every tick
            # from then on only logs the skip below, the table and the graph
            # stay on the last good pass, and /status keeps repeating those
            # stale numbers. Stop/Start monitoring does not clear the flag, so
            # without this there is no way back short of restarting the app.
            # Once the in-flight pass has outlived any plausible completion
            # time, write it off and start a fresh one; the generation bump a
            # few lines down discards the lost pass if it ever does land.
            timeout = float(self.settings.get("http_timeout_s", 10.0))
            poll_s = max(1, int(self.settings.get("poll_interval_s", 30)))
            max_wait_ns = int(max(poll_s * 5, timeout * 3 + 15) * 1e9)
            waited_s = (api.now_ns() - self._poll_started_ns) / 1e9
            if api.now_ns() - self._poll_started_ns < max_wait_ns:
                self._log("Previous poll still fetching — skipping this tick "
                          "(raise Concurrent fetches or the poll interval).")
                return
            self._poll_zombies += 1
            self._log(f"⚠ PV poll stalled for {waited_s:.0f}s — starting a "
                      f"fresh one (self-healing; {self._poll_zombies} pass(es) "
                      f"lost since launch). If this keeps repeating, the "
                      f"archiver or the network is not answering.")
            self._poll_inflight = False
        # Also fetch any gate PV that isn't already monitored, so its value is
        # available this pass to switch state-dependent thresholds.
        names += [g for g in sorted(self._gate_pv_names()) if g not in monitored]
        ranges = {pv.name: self._valid_range(pv) for pv in self.pvs}
        self._poll_gen += 1
        gen = self._poll_gen
        self._poll_inflight = True
        self._poll_started_ns = api.now_ns()
        sig = _PollSignals(self)
        # Every poll makes one of these, and a poll happens for as long as the
        # app is open. Parented to the widget they would ALL still be alive a
        # week later (tens of thousands of them, plus the connection each one
        # holds) — a slow, invisible climb in the app's memory. deleteLater is
        # connected first so it is queued before the result handler runs; Qt
        # only performs the delete once the current event is finished, so the
        # handler below still gets its data.
        sig.done.connect(sig.deleteLater)
        sig.done.connect(lambda res, g=gen: self._poll_done(res, g))
        self._poll_sig = sig   # stale after the delete; only kept as a handle
        QThreadPool.globalInstance().start(
            _PollWorker(sig, names, dict(self.settings), ranges))

    def _poll_done(self, results: dict, gen: int):
        self._poll_inflight = False
        if gen == self._poll_gen:      # stale results (stop/restart) are dropped
            self._on_poll(results)

    def _on_poll(self, results: dict):
        now = api.now_ns()
        # A pass came back: this is the one place that proves the program is
        # still reading. Everything the refresh watchdog says is measured from
        # here.
        self._last_poll_ok_ns = now
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
            rt.data_ts_ns = last_ts
            rt.last_error = err
            rt.rejected_count = rejected
            rt.bad_data = (val is None and rejected > 0)
            if rejected and not prev_rej:
                self._log(f"{pv.display_name}: {err} — treating as sensor error.")
            elif prev_rej and not rejected:
                self._log(f"{pv.display_name}: readings back within valid range.")
            if val is not None:
                rt.history.append((rt.last_update_ns, val))
            # Before any threshold work: decide whether this reading is still
            # live at all, so an alert raised below can say if it is not.
            self._update_frozen(pv, rt, now)
            rt.active_profile = self._match_profile(pv)
            thr = (pv.profile_thresholds(rt.active_profile)
                   if rt.active_profile is not None else pv.thresholds())
            # Plain severity of this reading, kept up to date even while
            # monitoring is off so the State column stays truthful.
            rt.live_level = (_raw_severity(val, thr)
                             if val is not None and thr.is_active() else
                             AlertLevel.OK if val is not None else None)
            if not self._monitoring:
                continue
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
        self._check_refresh_health()   # clears (and announces) a stall that ended
        self._check_frozen_alerts()
        self.model.refresh_all()
        self._refresh_dep_combos()
        self._update_status()
        self.graph.refresh_data()

    # --- "not updating" check ------------------------------------------
    def _frozen_check_on(self, pv: PVConfig) -> bool:
        """Whether the frozen-value check applies to this PV (global switch
        AND the PV's own opt-out)."""
        return bool(self.settings.get("frozen_check_enabled", True)) \
            and pv.frozen_check

    def _sample_age_limit_s(self) -> float:
        """How old the newest archive sample may get before the PV counts as not
        updating. Derived from the poll pacing rather than being a setting of
        its own: two sample windows or three poll intervals, whichever is
        longer, and never under 5 minutes — enough slack for the archiver's
        ~1 s publish lag and for this PC's clock running ahead of the facility.
        """
        return max(2.0 * float(self.settings["sample_window_s"]),
                   3.0 * float(self.settings["poll_interval_s"]),
                   300.0)

    def _update_frozen(self, pv: PVConfig, rt: PVRuntime, now: int) -> None:
        """Refresh this PV's 'not updating' verdict from its own history.

        Two ways a PV can keep answering while its reading is dead:

          * the value never changes — the archiver serves the same number over
            and over (a stuck IOC, a dead sensor);
          * the newest sample itself stops advancing — the archiver replies, but
            with data that is minutes to days old.

        Either one means what the table shows is not live. A PV with no reading
        at all this pass is NOT frozen: that is the ordinary 'no data' state,
        which the State column already reports.
        """
        def _clear():
            rt.frozen = False
            rt.frozen_reason = ""
            rt.frozen_since_ns = 0
            rt.frozen_span_s = 0.0
            rt.frozen_bounded = False

        if not self._frozen_check_on(pv) or rt.current_value is None:
            _clear()
            return

        after_s = float(self.settings.get("frozen_after_minutes", 120)) * 60.0
        info = detect_frozen(
            rt.history, now, after_s,
            min_points=int(self.settings.get("frozen_min_points", 5)))
        rt.frozen_since_ns = info.since_ns
        rt.frozen_span_s = info.span_s
        rt.frozen_bounded = info.bounded

        reasons = []
        if info.frozen:
            span = fmt_duration(info.span_s)
            reasons.append(f"value unchanged for {span}"
                           + ("" if info.bounded else " (all data kept here)"))
        age_s = (now - rt.data_ts_ns) / 1e9 if rt.data_ts_ns else 0.0
        if age_s > self._sample_age_limit_s():
            reasons.append(f"newest archive sample is {fmt_duration(age_s)} old")
        rt.frozen = bool(reasons)
        rt.frozen_reason = ", ".join(reasons)

    def _check_frozen_alerts(self):
        """Send one alert when a PV stops updating and one when it moves again.

        Deliberately outside the threshold state machine: a frozen PV is a data
        fault, not a value excursion, so it neither debounces nor repeats.
        """
        for pv in self.pvs:
            rt = self.runtime.get(pv.name)
            if rt is None:
                continue
            armed = (self._monitoring and pv.enabled
                     and self._frozen_check_on(pv)
                     and bool(self.settings.get("frozen_alert_enabled", True)))
            if not armed:
                # Not armed (or no longer armed): forget the episode instead of
                # firing a recovery for something never announced.
                rt.frozen_notified = False
                continue
            if rt.frozen and not rt.frozen_notified:
                rt.frozen_notified = True
                self._send_frozen_alert(pv, rt, AlertLevel.WARNING)
            elif not rt.frozen and rt.frozen_notified \
                    and rt.current_value is not None:
                # Only call it recovered on a live reading: losing the data
                # altogether clears `frozen` too, and that is not good news.
                rt.frozen_notified = False
                self._send_frozen_alert(pv, rt, AlertLevel.OK)

    def _send_frozen_alert(self, pv: PVConfig, rt: PVRuntime,
                           level: AlertLevel):
        value = rt.current_value if rt.current_value is not None else 0.0
        units = (rt.current_units or pv.units) or ""
        if level == AlertLevel.OK:
            reason = "Value is changing again — the PV is updating."
            prev = AlertLevel.WARNING
        else:
            shown = f"{_fmt(value)} {units}".strip()
            reason = (f"PV NOT UPDATING — {rt.frozen_reason}. The reading "
                      f"shown ({shown}) is not live, so any limit check on it "
                      f"is meaningless.")
            prev = AlertLevel.OK
        self._log(f"NOT UPDATING {pv.display_name}: {reason}")
        payload = AlertPayload(
            level=level, prev_level=prev,
            pv_name=pv.name, display_name=pv.display_name,
            value=value, units=units, reason=reason,
            timestamp_str=api.ns_to_prague_str(api.now_ns()),
            kind="transition")
        # Tagged apart from the PV name so the result never overwrites this
        # PV's "Alarm status" cell, which tracks its threshold alerts.
        self._launch_alert_worker(payload, self._active_thresholds(pv),
                                  tag=f"frozen:{pv.name}",
                                  valid_range=self._valid_range(pv))

    # --- "the program itself stopped refreshing" check -------------------
    def _refresh_limit_s(self) -> float:
        """How long the program may go without completing a read before what it
        shows counts as out of date.

        Deliberately longer than the wedge watchdog in _start_poll, which writes
        a lost pass off after five intervals and immediately starts a fresh one:
        a stall that cures itself that way should pass without anyone being
        woken, and only a stall that survives the cure is worth announcing.
        Never under three minutes.
        """
        poll_s = max(1, int(self.settings.get("poll_interval_s", 30)))
        timeout = float(self.settings.get("http_timeout_s", 10.0))
        return max(5.0 * poll_s + 60.0, 3.0 * timeout + 60.0, 180.0)

    def _refresh_age_s(self) -> float:
        """Seconds since the last completed read of every PV. Before the first
        one ever completes, measured from launch — a program that has been open
        for ten minutes and never read anything is just as broken as one that
        stopped."""
        ref = self._last_poll_ok_ns or self._mem_start_ns
        return max(0.0, (api.now_ns() - ref) / 1e9)

    def _refresh_fault(self) -> str:
        """Empty while values are being refreshed, otherwise one plain sentence
        saying they are not — written to be pasted straight into a chat reply,
        the status line or the log."""
        if not self.pvs:
            return ""
        age = self._refresh_age_s()
        if age <= self._refresh_limit_s():
            return ""
        poll_s = max(1, int(self.settings.get("poll_interval_s", 30)))
        if self._last_poll_ok_ns:
            what = (f"nothing has been read for {fmt_duration(age)} "
                    f"(a reading is due every {poll_s} s); the newest values I "
                    f"have are the ones from "
                    f"{api.ns_to_prague(self._last_poll_ok_ns).strftime('%H:%M:%S')}")
        else:
            what = (f"no reading has completed since this program started "
                    f"{fmt_duration(age)} ago")
        if self._poll_inflight and self._poll_started_ns:
            waited = (api.now_ns() - self._poll_started_ns) / 1e9
            what += (f"; the read that began {fmt_duration(waited)} ago has "
                     f"not come back")
        return what

    def _check_refresh_health(self):
        """Announce it — once — when the program stops refreshing, and once
        more when it starts again.

        Called from the poll tick and from the Webex listener tick, so whichever
        clock is still running catches the other one being stuck. Deliberately
        separate from the data watchdog: that one fires when the archiver stops
        answering, this one fires when this program stops asking.
        """
        if not hasattr(self, "graph"):
            return   # called before the window is built (nothing to show on yet)
        fault = self._refresh_fault()
        if fault and not self._refresh_bad:
            self._refresh_bad = True
            self._refresh_bad_since_ns = api.now_ns()
            self._log(f"⚠ {NOT_REFRESHED_LABEL.upper()} — {fault}")
            if self._monitoring:
                self._send_refresh_alert(
                    AlertLevel.ALARM,
                    f"VALUES ARE {NOT_REFRESHED_LABEL.upper()} — {fault}. "
                    f"Anything I report until this clears is out of date.")
            self._mark_stale_ui(fault)
        elif not fault and self._refresh_bad:
            gap = fmt_duration(
                (api.now_ns() - self._refresh_bad_since_ns) / 1e9)
            self._refresh_bad = False
            self._refresh_bad_since_ns = 0
            self._log(f"Values are refreshing again (stopped for {gap}).")
            if self._monitoring:
                self._send_refresh_alert(
                    AlertLevel.OK,
                    f"Values are refreshing again after {gap}.")
            self._mark_stale_ui("")

    def _mark_stale_ui(self, fault: str):
        """Put the 'not refreshed' marking on (or take it off) the status line,
        the table's State column and the banner above the graph."""
        self.model.stale = bool(fault)
        self.model.stale_since = (
            api.ns_to_prague(self._last_poll_ok_ns).strftime("%H:%M:%S")
            if self._last_poll_ok_ns else "")
        self.model.refresh_all()
        self.graph.set_stale_note(fault)
        self._update_status()

    def _send_refresh_alert(self, level: AlertLevel, reason: str):
        prev = AlertLevel.ALARM if level == AlertLevel.OK else AlertLevel.OK
        payload = AlertPayload(
            level=level, prev_level=prev,
            pv_name="System", display_name="Value refresh",
            value=0.0, units="", reason=reason,
            timestamp_str=api.ns_to_prague_str(api.now_ns()), kind="transition")
        # Its own tag, so it can never overwrite a PV's "Alarm status" cell nor
        # the data watchdog's.
        self._launch_alert_worker(payload, Thresholds(), tag="refresh",
                                  render_plot=False)

    def _pv_stale_note(self, pv: PVConfig, rt: Optional[PVRuntime]) -> str:
        """Empty when this PV's reading is as fresh as it should be, otherwise
        why it is not — worked out at the moment of asking, not at the last poll.

        That timing is the whole point: when a poll wedges, every verdict stored
        on the PV (including its 'not updating' flag) is itself frozen at the
        last good pass, so only a check made now can tell the truth.

        Kept to a few words: it is repeated on every line of a status list, and
        the full explanation belongs in that list's header instead.
        """
        if self._refresh_bad:
            age = self._refresh_age_s()
            return f"nothing read for {fmt_duration(age)}"
        if rt is None or not self._frozen_check_on(pv):
            return ""
        if not rt.data_ts_ns:
            return ""
        age = (api.now_ns() - rt.data_ts_ns) / 1e9
        if age <= self._sample_age_limit_s():
            return ""
        return (f"newest reading is from "
                f"{api.ns_to_prague(rt.data_ts_ns).strftime('%H:%M:%S')}, "
                f"{fmt_duration(age)} ago")

    def _check_data_watchdog(self, results: dict):
        """Alert once when every monitored PV stops getting data (fetch errors
        across the board — an archiver/network outage), and once more when it
        recovers. Distinct from a single PV's own no-data/threshold alerts."""
        if not self._monitoring or not self.pvs \
                or not self.settings.get("data_watchdog_enabled", True):
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
        reason = note.reason
        if rt.frozen:
            # The limits tripped on a reading that is no longer live — say so in
            # the message instead of letting it read as a fresh measurement.
            reason += f" — ⚠ but this PV is NOT UPDATING ({rt.frozen_reason})"
        payload = AlertPayload(
            level=note.level, prev_level=note.prev_level,
            pv_name=pv.name, display_name=pv.display_name,
            value=note.value, units=rt.current_units or pv.units,
            reason=reason,
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
        sig.done.connect(sig.deleteLater)   # see _start_poll
        sig.done.connect(self._on_alert_result)
        # Each dispatch keeps its own signals object alive via the worker, so
        # concurrent alerts (several PVs tripping at once) don't clobber one
        # another; this attribute is just a convenience handle to the latest.
        self._alert_sig = sig
        QThreadPool.globalInstance().start(
            _AlertWorker(sig, self.hub, payload, thr, hours, timeout, tag,
                         vmin, vmax, self._sample_age_limit_s()))
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
        sig.done.connect(sig.deleteLater)   # see _start_poll
        sig.done.connect(lambda bot_id, g=gen: self._on_bot_id(bot_id, g))
        self._me_id_sig = sig
        QThreadPool.globalInstance().start(_MeIdWorker(sig, self.hub.webex))

    def _on_bot_id(self, bot_id, gen):
        # Released before the staleness check on purpose. The command-poll
        # watchdog bumps _cmd_gen while a resolve may still be in flight; if
        # the flag were only cleared on the fresh-generation path, that resolve
        # would leave it True forever and _resolve_bot_id would return
        # immediately from then on — the bot id would never resolve again.
        self._cmd_bot_id_inflight = False
        if gen != self._cmd_gen:
            return   # listener was restarted/stopped since this request was sent
        self._cmd_bot_id = bot_id
        if not bot_id:
            self._log("⚠ Failed to get bot's personId; will retry on next poll. "
                      "(Own-message IDs are still filtered as a backstop.)")

    def _poll_commands(self):
        # Second heartbeat for the refresh watchdog: this timer and the poll
        # timer are independent, so if the reading loop stops ticking at all
        # (not just stops finishing), the listener still notices and can say so.
        self._check_refresh_health()
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
        sig.done.connect(sig.deleteLater)   # see _start_poll: one per poll, forever
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
        # Said once, the first time Webex admits this is a group space: from then on
        # the bot can only see messages that tag it. Worth a line, because "the bot
        # answers nothing" otherwise looks like a broken token and the fix — type
        # the bot's name first — is not something anybody guesses.
        if getattr(self.hub.webex, "mention_only_notice", False):
            self.hub.webex.mention_only_notice = False
            self._log(f"Webex: this room has other people in it, so I only see "
                      f"messages that tag me. Start commands with "
                      f"“{self.hub.webex.mention_name()} /status”.")
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

    def _resolve_pvs(self, items: list[str]):
        """Resolve a comma-separated item list to PVs, keeping the order and
        dropping duplicates. Returns (pvs, errors); 'all' means every PV."""
        if len(items) == 1 and items[0].strip().lower() == "all":
            return list(self.pvs), []
        pvs, errors, seen = [], [], set()
        for it in items:
            pv, err = self._find_pv(it)
            if pv is None:
                errors.append(err)
            elif pv.name not in seen:
                seen.add(pv.name)
                pvs.append(pv)
        return pvs, errors

    def _handle_command(self, text: str, email: str):
        pc = bot_commands.parse_command(text)
        cmd = pc.cmd
        args = pc.args.split()       # positional args (numbers, on|off)
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
                self._reply(self._cmd_status(pc.items))
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
                self.ensure_window_backfilled()
                self.graph.redraw()
                self.persist()
                self._reply(f"Graph window set to {mins} min.")
            elif cmd == "/yaxis":
                # Accepts "/yaxis auto", "/yaxis 10 30" and "/yaxis 10-30".
                rng = bot_commands.parse_yaxis_spec(f"y {pc.args}")
                if rng is None:
                    self.graph.set_yaxis(None, None)
                    self._reply("Y axis: autoscale.")
                else:
                    self.graph.set_yaxis(rng[0], rng[1])
                    self._reply(f"Y axis set to [{rng[0]:g}, {rng[1]:g}].")
            elif cmd == "/graph":
                if not pc.items or pc.first.lower() == "all":
                    self.graph.select_pv(None)
                    self._reply("Graph: all PVs.")
                elif len(pc.items) > 1:
                    self._reply("⚠ The live graph shows one PV or all of them — "
                                "name a single PV, or use `/graph all`. Several "
                                "PVs at once work with `/plot`.")
                else:
                    pv, err = self._find_pv(pc.first)
                    if not pv:
                        self._reply(f"⚠ {err}")
                    else:
                        self.graph.select_pv(pv.name)
                        self._reply(f"Graph: {pv.display_name}.")
            elif cmd == "/plot":
                self._cmd_plot(pc)
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
                pvs, errors = self._resolve_pvs(pc.items)
                if errors or not pvs:
                    self._reply("⚠ " + "; ".join(errors or ["missing PV name"]))
                else:
                    for pv in pvs:
                        pv.enabled = (cmd == "/enable")
                    self.model.refresh_all()
                    self.persist()
                    state = "enabled" if cmd == "/enable" else "disabled"
                    names = ", ".join(pv.display_name for pv in pvs)
                    self._reply(f"Alerting {state} for {names}.")
            elif cmd in ("/run", "/rundiagnostic"):
                # This command belongs to the standalone listener
                # (remote_launcher.py), which starts the app when it is closed.
                # But the app sits in the same room, and it used to answer
                # "unknown command" to it — so with the listener not running,
                # the only reply /run ever got was an error message, even
                # though the app was up and tracking. Answering here means the
                # app being open is itself the answer. Same wording as the
                # listener uses for the same command.
                if self._monitoring:
                    self._reply("ℹ️ Diagnostic is already running and tracking.")
                else:
                    self._reply(
                        "ℹ️ Diagnostic is already running, but not "
                        "tracking — send `@Diagnostics /start` to arm it "
                        "(or use the Start monitoring button).")
            else:
                self._reply(f"❓ Unknown command {cmd}. Try /help.")
        except bot_commands.CommandError as e:
            # Raised by the parsers with a message written for the chat.
            self._reply(f"⚠ {e} Try /help.")
        except (IndexError, ValueError):
            self._reply(f"⚠ Bad arguments for {cmd}. Try /help.")

    # --- /plot: any number of PVs, any time window ----------------------
    def _cmd_plot(self, pc: "bot_commands.ParsedCommand"):
        if not pc.items:
            self._reply("⚠ Which PV? For example "
                        "`/plot Chiller 1, Chiller 2; 7-18`. "
                        "`/plot all` plots every PV.")
            return
        pvs, errors = self._resolve_pvs(pc.items)
        if errors:
            self._reply("⚠ " + "; ".join(errors))
            return
        if not pvs:
            self._reply("No PVs configured.")
            return
        opts = bot_commands.parse_plot_options(pc.options, api.now_ns(),
                                               api.TZ_PRAGUE)
        if opts.time is not None:
            start_ns, end_ns, label = (opts.time.start_ns, opts.time.end_ns,
                                       opts.time.label)
        else:
            hours = float(self.settings.get("alert_plot_hours", 12)) or 12.0
            end_ns = api.now_ns()
            start_ns = end_ns - int(hours * 3600 * 1e9)
            label = f"last {hours:g} h"
        if not self.hub.is_any_configured():
            self._reply("⚠ No notification channel is configured, so I have "
                        "nowhere to send the plot.")
            return

        names = ", ".join(pv.display_name for pv in pvs)
        short = names if len(names) <= 70 else f"{len(pvs)} PVs"
        # Thresholds only make sense on a single curve — see render_chart_png.
        series = [ChartSeries(pv.name, pv.display_name,
                              self._active_thresholds(pv) if len(pvs) == 1 else None,
                              *self._valid_range(pv))
                  for pv in pvs]
        body = (f"**📈 {names}**\n\n"
                f"- **Window:** {label} (Europe/Prague)\n")
        if opts.yaxis:
            body += f"- **Y range:** {opts.yaxis[0]:g} … {opts.yaxis[1]:g}\n"
        body += "\n".join(self._status_line(pv) for pv in pvs)
        body = self._freshness_header() + body
        # Only judge the picture's freshness when the window was asked to run up
        # to now. A window that ends in the past is supposed to stop where it
        # stops, and calling that "not current" would be nonsense.
        live_window = (api.now_ns() - end_ns) < int(120 * 1e9)
        stale_after_s = self._sample_age_limit_s() if live_window else 0.0
        sig = _ChartSignals(self)
        sig.done.connect(sig.deleteLater)   # see _start_poll
        sig.done.connect(self._on_chart_result)
        self._chart_sig = sig      # handle to the latest; the worker owns it
        QThreadPool.globalInstance().start(_ChartWorker(
            sig, self.hub, series, start_ns, end_ns,
            float(self.settings["http_timeout_s"]),
            f"Plot — {short} ({label})", label, body, opts.yaxis,
            stale_after_s))
        self._reply(f"📈 Rendering {short} — {label}…")

    def _on_chart_result(self, result):
        errors, had_png = result
        for ch, err in errors.items():
            self._log(f"  {ch} send failed: {err}")
        if not errors:
            self._log("Chart sent." if had_png
                      else "Chart sent (no data in that window — text only).")

    def _cmd_help(self) -> str:
        # The mention rule goes first, and with the bot's real name in it when the
        # listener has already asked Webex for it.
        return (
            bot_commands.mention_help(
                getattr(self.hub.webex, "bot_name", "")) + "\n"
            "\n"
            + bot_commands.SYNTAX_HELP + "\n"
            "\n"
            "**Commands**\n"
            "- `/status [pv, pv]` — values + state, all PVs or just those. "
            "Every reply says when the values were last read; if I have stopped "
            f"reading, each line says `{NOT_REFRESHED_LABEL}` instead of `ok` "
            "and a warning goes above the list.\n"
            "- `/alarms` — only PVs currently in warning/alarm, plus any that "
            "stopped updating\n"
            "- `/list` — the configured PVs\n"
            "- `/plot <pv, pv, …>[; window][; y lo-hi]` — send one graph with a "
            "curve per PV, e.g. `/plot Chiller 1, Chiller 2; yesterday 7-18`. "
            "`/plot all` takes every PV; a single PV also gets its limit lines.\n"
            "- `/start` — alerting on (PVs are read and plotted either way)\n"
            "- `/stop [hours]` — alerting off; with hours, auto-resume later "
            "(e.g. `/stop 10`)\n"
            "- `/enable <pv, pv>` `/disable <pv, pv>` — alerting per PV\n"
            "- `/datawatchdog on|off` — the 'no data at all' alert "
            "(no argument: show current state)\n"
            "- I also announce it by myself, without being asked, if I stop "
            "refreshing the values at all, and again when I start again. That "
            "one cannot be switched off.\n"
            "- `/graph <pv|all>` — what the app window itself shows\n"
            "- `/window <minutes>` — time window of that live graph\n"
            "- `/yaxis <lo-hi>|auto` — Y range of that live graph\n"
            "- `/run` — start the app when it is closed (answered by the "
            "always-on listener; if the app is already open it says so)")

    def _status_line(self, p: PVConfig) -> str:
        rt = self.runtime.get(p.name)
        bad = bool(rt and rt.bad_data and rt.current_value is None and p.enabled)
        if rt and rt.current_value is not None:
            val = _fmt(rt.current_value)
        elif bad and rt.raw_value is not None:
            val = _fmt(rt.raw_value)
        else:
            val = "–"
        units = (rt.current_units if rt and rt.current_units else p.units) or ""
        # Freshness comes first: if the number is out of date, saying "ok" about
        # it is worse than saying nothing, because "ok" reads as "checked just
        # now and fine".
        stale = self._pv_stale_note(p, rt)
        if stale:
            state = f"⚠ {NOT_REFRESHED_LABEL} — {stale}"
        elif rt and rt.frozen:
            state = f"⚠ {FROZEN_LABEL} — {rt.frozen_reason}"
        elif not p.enabled:
            state = "off"
        elif bad:
            state = "bad data"
        elif rt and rt.display_level(self._monitoring) is not None:
            state = rt.display_level(self._monitoring).label.lower()
            if not self._monitoring:
                state += ", not monitored"
        else:
            state = "no data"
        return f"- **{p.display_name}**: {val} {units} [{state}]"

    def _cmd_status(self, items: Optional[list[str]] = None) -> str:
        if not self.pvs:
            return "No PVs configured."
        pvs = self.pvs
        if items:
            pvs, errors = self._resolve_pvs(items)
            if errors:
                return "⚠ " + "; ".join(errors)
        mon = "MONITORING" if self._monitoring else "stopped (reading only)"
        out = (f"**Status ({mon}):**\n"
               + "\n".join(self._status_line(p) for p in pvs))
        # The warning goes ABOVE the values, not below them: read on a phone,
        # the first line is the only one that is certain to be read, and if the
        # numbers are out of date that is the thing to know before reading them.
        out = self._freshness_header() + out
        # Only on the whole-list status, and only as a footer: asked from a
        # phone, this is the one way to see how the PC that runs the monitor is
        # doing after days of uptime.
        if not items:
            out += f"\n\n_{self._freshness_footer()}_"
            snap = memstats.read()
            if snap is not None:
                up = fmt_duration((api.now_ns() - self._mem_start_ns) / 1e9)
                out += (f"\n_Up {up} · {memstats.short_line(snap)}_")
        return out

    def _freshness_header(self) -> str:
        """The banner that goes above any list of values, or '' when they are
        current."""
        fault = self._refresh_fault()
        if not fault:
            return ""
        return (f"**⚠ {NOT_REFRESHED_LABEL.upper()} — {fault}.**\n"
                f"_Everything below is the last reading I managed to take, not "
                f"the present state._\n\n")

    def _freshness_footer(self) -> str:
        """One line saying when the values below were actually read."""
        if not self._last_poll_ok_ns:
            return "No reading has completed yet."
        when = api.ns_to_prague(self._last_poll_ok_ns).strftime("%H:%M:%S")
        age = fmt_duration(self._refresh_age_s())
        late = f" — {NOT_REFRESHED_LABEL} ⚠" if self._refresh_fault() else ""
        return f"Values read at {when} ({age} ago){late}"

    def _cmd_alarms(self) -> str:
        lines, frozen = [], []
        for p in self.pvs:
            if not p.enabled:
                continue
            rt = self.runtime.get(p.name)
            val = _fmt(rt.current_value) if rt else "–"
            units = (rt.current_units if rt and rt.current_units else p.units) or ""
            # Out of date as of right now — checked here rather than trusting
            # the verdict stored at the last poll, which is itself out of date
            # when the poll is what stopped. Skipped while the whole program is
            # stalled: the header already says that, and repeating it once per
            # PV would bury the alarms.
            stale = "" if self._refresh_bad else self._pv_stale_note(p, rt)
            if stale:
                frozen.append(f"- **{p.display_name}**: {val} {units} "
                              f"[{NOT_REFRESHED_LABEL} — {stale}]")
                continue
            # A frozen PV is listed as the data fault it is, not as whatever its
            # dead reading happens to score against the limits.
            if rt is not None and rt.frozen:
                frozen.append(f"- **{p.display_name}**: {val} {units} "
                              f"[{rt.frozen_reason}]")
                continue
            level = rt.display_level(self._monitoring) if rt else None
            if level not in (AlertLevel.WARNING, AlertLevel.ALARM):
                continue
            lines.append(f"- **{p.display_name}**: {val} {units} [{level.label.lower()}]")
        out = []
        if lines:
            out.append("**Current alarms:**\n" + "\n".join(lines))
        if frozen:
            out.append(f"**⚠ Reading is not live:**\n" + "\n".join(frozen))
        fault = self._refresh_fault()
        if not out:
            # "No alarms" is a claim about the present. While the values are
            # out of date it is not one this program is entitled to make.
            if fault:
                return (f"**⚠ {NOT_REFRESHED_LABEL.upper()} — {fault}.**\n"
                        f"_I cannot tell whether anything is in alarm right "
                        f"now; nothing was in alarm at the last reading._")
            return f"✅ No PVs currently in warning/alarm.\n\n_{self._freshness_footer()}_"
        return self._freshness_header() + "\n\n".join(out)

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
        self.graph.refresh_data()
