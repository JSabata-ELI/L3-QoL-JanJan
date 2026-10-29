# main.py
"""Screenshots — camera frames and monitor screenshots into one folder.

Grabs camera frames (from the CPVA archive, or by capturing the camera's CSS
window) and monitor screenshots into one destination folder: once (Copy), on
a fixed time grid (Auto) or continuously (Live), with a preview grid to review
them. Everything that is not a window lives in ssq_core.py; this file is the
main window and the run control. PySide6 since 16.10.2026 (the Tk s.py it
replaced is kept in C:\\Dev\\backup\\Screenshots_Tk_20261016).
"""
from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QProgressBar,
                               QPushButton, QRadioButton, QScrollArea, QSizePolicy,
                               QSpinBox, QVBoxLayout, QWidget)

import ssq_common as ui
import ssq_core as core
from ssq_common import on_ui
from ssq_dialogs import DetailDialog, LabelsDialog, PresetManager, identify_monitors
from ssq_preview import PreviewWindow

_VER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)


def _detect_version() -> str:
    """The version out of the running file's own name, as the siblings do."""
    try:
        name = (Path(sys.executable).name if getattr(sys, "frozen", False)
                else Path(__file__).name)
        m = _VER_RE.search(name)
        if m:
            return f"v{m.group(1)}.{m.group(2)}.{m.group(3)}"
    except Exception:
        pass
    return ""


APP_TITLE = f"Screenshots {_detect_version()}".strip()
_APP_ID_PREFIX = "ELI.Screenshots"


def _icon_app_id(prefix, ico_path):
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried in this family of programs eventually picked
    up a bad cache entry and then drew the blank window placeholder for good.
    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which is set by the window itself. Returns None when
    there is no icon at all; the caller then sets no id.
    """
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def _nudge_taskbar_icon(hwnd) -> None:
    """Make the taskbar re-read this window's icon a few times after start.

    Explorer reads a taskbar button's picture once, when it creates the
    button; when that one read misses, the button stays blank for the whole
    run (measured on Diagnostic v1.3.4, 15.10.2026). Taking the icon away and
    giving it back makes Explorer read it again, so that is done at about 2,
    10, 30 and 60 s on a daemon thread. The window's own handles are put back,
    so nothing is created or leaked.
    """
    if not hwnd:
        return

    def run():
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        send = user32.SendMessageTimeoutW
        send.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                         wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_size_t
        res = ctypes.c_size_t()
        for delay in (2, 8, 20, 30):          # at about 2, 10, 30 and 60 s
            time.sleep(delay)
            try:
                if not user32.IsWindow(wt.HWND(hwnd)):
                    return
                held = {}
                for which in (1, 0):          # ICON_BIG, ICON_SMALL
                    res.value = 0
                    # SMTO_ABORTIFHUNG: a frozen window just skips this round
                    if send(hwnd, 0x007F, which, 0, 0x0002, 2000,
                            ctypes.byref(res)) and res.value:
                        held[which] = res.value
                for which in held:
                    send(hwnd, 0x0080, which, 0, 0x0002, 2000, ctypes.byref(res))
                for which, h in held.items():
                    send(hwnd, 0x0080, which, h, 0x0002, 2000, ctypes.byref(res))
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="taskbar-icon").start()


class _ResizeWatch(QObject):
    """Calls `fn` whenever the watched widget changes width."""

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def eventFilter(self, obj, ev):
        try:
            if ev.type() == QEvent.Resize and ev.oldSize().width() != ev.size().width():
                self.fn()
        except Exception:
            pass
        return False


class ScreenshotsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ico = core.icon_file()
        if ico:
            self.setWindowIcon(QIcon(str(ico)))
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setPalette(ui.light_palette())
        self.resize(1440, 820)
        self.setMinimumSize(900, 520)

        self._station_id = core._get_station_id()
        print(f"[STATION] hostname='{self._station_id}'", flush=True)
        self.setWindowTitle(f"{APP_TITLE}  —  {self._station_id}" if self._station_id
                            else APP_TITLE)

        self.hub = ui.LogHub(self)
        self.copier = core.ArchiveCopier(self.log)
        self.copier.start_cache_refresh_worker()
        self.live = core.LiveCapture(
            self.log, on_count=lambda n: on_ui(self._live_status.setText, f"{n} files"))

        self._last_browse_dir = str(Path.home())
        self._busy = False
        self._last_out_dir: Path | None = None
        self._notes_name = "notes"
        self._previews: list[PreviewWindow] = []

        # Auto copy — the run's cycle list, the one live preview window, the
        # queue of cycle moments still to be copied.
        self._auto_copy_active = False
        self._auto_copy_count = 0
        self._auto_copy_params: dict | None = None
        self._auto_cycle_history: list[dict] = []
        self._auto_preview: PreviewWindow | None = None
        self._auto_preview_closed_by_user = False
        self._auto_tick_queue: queue.Queue | None = None
        self._auto_ticks_done = False
        self._auto_dropped = 0
        self._auto_run_id = 0
        self._auto_offset_ns: int | None = None
        self._auto_t0_ns = 0
        self._auto_t0_mono = 0.0
        self._auto_next_n = 0
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(lambda: self._on_auto_tick(self._auto_next_n))

        # Monitors
        self._monitors = core.list_monitors_rects()[:8]
        self.monitor_vars: list[bool] = [False for _ in self._monitors]
        self._mon_boxes: dict[int, QCheckBox] = {}
        self._manual_all_screens = True
        self._manual_monitors: set[int] = set()

        # Presets
        self._active_presets: set[str] = set()
        self._preset_mon_ref: dict[int, int] = {}
        self._preset_all_screens_ref = 0
        self._preset_cached_monitors: dict[str, list[int]] = {}
        self._presets: dict[str, dict] = core.load_presets()
        self.preset_buttons: dict[str, QPushButton] = {}

        # Cameras: one state per camera, one box per (section, camera) — a few
        # cameras sit in two sections and both boxes follow the same state.
        self._cam_order: list[str] = list(dict.fromkeys(
            cam for cams in core.CAM_CATEGORIES.values() for cam in cams))
        self._cam_sel: dict[str, bool] = {cam: False for cam in self._cam_order}
        self._cam_boxes: dict[str, list[QCheckBox]] = {cam: [] for cam in self._cam_order}
        self._cam_cats: dict[str, list[str]] = {cam: [] for cam in self._cam_order}
        for cat, cams in core.CAM_CATEGORIES.items():
            for cam in cams:
                if cat not in self._cam_cats[cam]:
                    self._cam_cats[cam].append(cat)
        self._cam_sections: dict[str, ui.CollapsibleSection] = {}
        self._cam_grids: dict[str, QGridLayout] = {}
        self._cat_boxes: dict[str, QCheckBox] = {}
        self._cat_widgets: dict[str, dict[str, QCheckBox]] = {}
        self._cat_grid_key: dict[str, tuple] = {}
        self._cam_cols = 4

        self._build_ui()
        for cat in core.CAM_CATEGORIES:
            self._update_category_check(cat)
        self._apply_monitor_effective()
        self._update_name_label()

    # ── log ──────────────────────────────────────────────────────
    def log(self, msg: str):
        self.hub.write(msg)

    # ── UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        central.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(8)
        root.addLayout(left, 0)

        # ── Settings + Source ────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        dest_box = QGroupBox("Settings")
        g = QGridLayout(dest_box)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(4)
        cap = QLabel("Destination")
        cap.setStyleSheet("color: #555555; font-size: 8pt;")
        g.addWidget(cap, 0, 0, 1, 4)
        dest_row = QHBoxLayout()
        self.dest_edit = QLineEdit(core.default_dest(self._station_id))
        self.dest_edit.setMinimumWidth(200)
        dest_row.addWidget(self.dest_edit, 1)
        browse = QPushButton("Browse")
        browse.setToolTip("Choose the destination folder")
        browse.clicked.connect(self.pick_dest)
        dest_row.addWidget(browse)
        open_b = ui.icon_button("folder", tip="Open the destination folder")
        open_b.setFixedWidth(34)
        open_b.clicked.connect(self.open_dest)
        dest_row.addWidget(open_b)
        g.addLayout(dest_row, 1, 0, 1, 4)
        self._name_label = QLabel("Folder name:")
        g.addWidget(self._name_label, 2, 0, 1, 4)
        self.run_name_edit = QLineEdit()
        g.addWidget(self.run_name_edit, 3, 0)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setObjectName("primary")
        self._copy_btn.clicked.connect(self.on_copy)
        g.addWidget(self._copy_btn, 3, 1)
        labels_b = QPushButton("Labels")
        labels_b.clicked.connect(self.on_labels)
        g.addWidget(labels_b, 3, 2)
        detail_b = QPushButton("Detail")
        detail_b.clicked.connect(self.on_detail)
        g.addWidget(detail_b, 3, 3)
        self._preview_cb = QCheckBox("Preview")
        self._preview_cb.setChecked(True)
        # Re-ticking Preview brings back an auto preview the user closed.
        self._preview_cb.toggled.connect(
            lambda _on: setattr(self, "_auto_preview_closed_by_user", False))
        # The run status sits beside Preview, where the row has room.
        preview_row = QHBoxLayout()
        preview_row.addWidget(self._preview_cb)
        preview_row.addSpacing(12)
        self._auto_status = ui.hint_label("")
        preview_row.addWidget(self._auto_status)
        self._live_status = ui.hint_label("")
        preview_row.addWidget(self._live_status)
        preview_row.addStretch(1)
        g.addLayout(preview_row, 4, 0, 1, 4)

        auto_row = QHBoxLayout()
        auto_row.setSpacing(4)
        auto_row.addWidget(QLabel("Auto every"))
        self._auto_interval = QSpinBox()
        self._auto_interval.setRange(core.AUTO_MIN_INTERVAL_S, 3600)
        self._auto_interval.setValue(60)
        self._auto_interval.setFixedWidth(62)     # 4 digits + the arrows
        auto_row.addWidget(self._auto_interval)
        auto_row.addWidget(QLabel("s  max"))
        self._auto_cycles = QSpinBox()
        self._auto_cycles.setRange(0, 999)
        self._auto_cycles.setValue(0)
        self._auto_cycles.setFixedWidth(54)       # 3 digits + the arrows
        self._auto_cycles.setToolTip("0 = ∞ cycles")
        auto_row.addWidget(self._auto_cycles)
        auto_row.addWidget(QLabel("cycles"))
        auto_row.addSpacing(6)
        self._auto_btn = ui.icon_button("play", "Start auto")
        self._auto_btn.clicked.connect(self._toggle_auto_copy)
        auto_row.addWidget(self._auto_btn)
        self._live_btn = ui.icon_button("record", "Start live")
        self._live_btn.clicked.connect(self._toggle_live)
        auto_row.addWidget(self._live_btn)
        auto_row.addStretch(1)
        cfg_b = QPushButton("Config presets")
        cfg_b.clicked.connect(self._open_preset_manager)
        auto_row.addWidget(cfg_b)
        g.addLayout(auto_row, 5, 0, 1, 4)
        g.setColumnStretch(0, 1)
        top_row.addWidget(dest_box, 1)

        src_box = QGroupBox("Source")
        sl = QVBoxLayout(src_box)
        station = QLabel(self._station_id or "unknown")
        station.setStyleSheet("color: #0055aa; font-weight: bold; font-size: 8pt;")
        sl.addWidget(station)
        self._src_group = QButtonGroup(self)
        self._src_cpva = QRadioButton("Archiver")
        self._src_cpva.setChecked(True)
        self._src_window = QRadioButton("Screenshot window")
        for rb in (self._src_cpva, self._src_window):
            self._src_group.addButton(rb)
            sl.addWidget(rb)
        self._src_group.buttonToggled.connect(
            lambda _b, on: self._render_cameras() if on else None)
        sl.addStretch(1)
        top_row.addWidget(src_box, 0)
        left.addLayout(top_row)

        # ── Monitors + Presets ───────────────────────────────────
        monpres = QHBoxLayout()
        monpres.setSpacing(6)
        mon_box = QGroupBox("Monitors")
        ml = QVBoxLayout(mon_box)
        r1 = QHBoxLayout()
        self._all_screens_cb = QCheckBox("All screens")
        self._all_screens_cb.setChecked(True)
        self._all_screens_cb.clicked.connect(self._on_all_screens_toggle)
        r1.addWidget(self._all_screens_cb)
        r1.addStretch(1)
        ident = QPushButton("Identify")
        ident.setToolTip("Show each monitor's number on the monitor itself")
        ident.clicked.connect(self.on_identify_monitors)
        r1.addWidget(ident)
        ml.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Layout:"))
        self._layout_cb = QComboBox()
        self._layout_cb.addItems(list(core.MONITOR_LAYOUTS.keys()))
        self._layout_cb.setCurrentText(core._station_layout_name(self._station_id) or "VIS-01")
        self._layout_cb.activated.connect(self._on_layout_selected)
        r2.addWidget(self._layout_cb)
        r2.addStretch(1)
        ml.addLayout(r2)
        self._layout_holder = QWidget()
        self._layout_grid = QGridLayout(self._layout_holder)
        self._layout_grid.setContentsMargins(0, 4, 0, 0)
        self._layout_grid.setSpacing(4)
        ml.addWidget(self._layout_holder, 0, Qt.AlignLeft)
        ml.addStretch(1)
        monpres.addWidget(mon_box, 0)
        self._render_monitor_layout(self._layout_cb.currentText())

        presets_box = QGroupBox("Presets")
        pl = QVBoxLayout(presets_box)
        self._presets_holder = QWidget()
        self._presets_grid = QGridLayout(self._presets_holder)
        self._presets_grid.setContentsMargins(0, 0, 0, 0)
        self._presets_grid.setHorizontalSpacing(6)
        self._presets_grid.setVerticalSpacing(4)
        pl.addWidget(self._presets_holder)
        pl.addStretch(1)
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        self._clear_btn = QPushButton("Clear all")
        self._clear_btn.setObjectName("danger")   # light red: it drops the whole selection
        self._clear_btn.setToolTip("Untick every camera, monitor and preset")
        self._clear_btn.clicked.connect(self._clear_all_selections)
        clear_row.addWidget(self._clear_btn)
        pl.addLayout(clear_row)
        monpres.addWidget(presets_box, 1)
        left.addLayout(monpres)
        self.rebuild_preset_buttons()

        # ── Selected cameras ─────────────────────────────────────
        sel_box = QGroupBox("Selected cameras")
        sbl = QVBoxLayout(sel_box)
        self._sel_scroll = QScrollArea()
        self._sel_scroll.setWidgetResizable(False)
        self._sel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._sel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._sel_scroll.setFrameShape(QScrollArea.NoFrame)
        self._sel_inner = QWidget()
        self._sel_inner.setAttribute(Qt.WA_StyledBackground, True)
        self._sel_grid = QGridLayout(self._sel_inner)
        self._sel_grid.setContentsMargins(0, 0, 0, 0)
        self._sel_grid.setHorizontalSpacing(16)
        self._sel_grid.setVerticalSpacing(1)
        self._sel_scroll.setWidget(self._sel_inner)
        sbl.addWidget(self._sel_scroll)
        self._sel_watch = _ResizeWatch(self._refresh_selected_cams_table, self)
        self._sel_scroll.viewport().installEventFilter(self._sel_watch)
        left.addWidget(sel_box)

        # ── Progress (manual Copy only) ──────────────────────────
        self._prog_bar = QProgressBar()
        self._prog_bar.setTextVisible(False)
        self._prog_bar.setFixedHeight(14)
        self._prog_label = ui.hint_label("")
        self._prog_bar.hide()
        self._prog_label.hide()
        left.addWidget(self._prog_bar)
        left.addWidget(self._prog_label)

        # ── Diagnostics ──────────────────────────────────────────
        diag = QGroupBox("Diagnostics")
        dl = QVBoxLayout(diag)
        self.hub.view.setMinimumHeight(140)
        dl.addWidget(self.hub.view)
        left.addWidget(diag, 1)

        # ── RIGHT: Cameras ───────────────────────────────────────
        cam_box = QGroupBox("Cameras")
        cl = QVBoxLayout(cam_box)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.cam_search = QLineEdit()
        self.cam_search.textChanged.connect(lambda _t: self._render_cameras())
        search_row.addWidget(self.cam_search, 1)
        x = ui.icon_button("clear", tip="Clear the search", tool=True)
        x.setFixedSize(28, 24)
        x.clicked.connect(lambda: self.cam_search.setText(""))
        search_row.addWidget(x)
        cl.addLayout(search_row)

        self.cam_scroll = QScrollArea()
        self.cam_scroll.setWidgetResizable(True)
        self.cam_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cam_inner = QWidget()
        cam_inner.setAttribute(Qt.WA_StyledBackground, True)
        self._cam_inner_lay = QVBoxLayout(cam_inner)
        self._cam_inner_lay.setContentsMargins(2, 2, 2, 2)
        self._cam_inner_lay.setSpacing(8)
        for cat in core.CAM_CATEGORIES:
            sec = ui.CollapsibleSection(cat, expanded=False)
            all_cb = QCheckBox("All")
            all_cb.clicked.connect(lambda checked, c=cat: self._toggle_category_all(c, checked))
            sec.right_slot.addWidget(all_cb)
            grid = QGridLayout(sec.content)
            grid.setContentsMargins(30, 2, 0, 4)
            grid.setHorizontalSpacing(18)
            grid.setVerticalSpacing(2)
            self._cam_sections[cat] = sec
            self._cam_grids[cat] = grid
            self._cat_boxes[cat] = all_cb
            self._cat_widgets[cat] = {}
            for cam in core.CAM_CATEGORIES[cat]:
                if cam in self._cat_widgets[cat]:
                    continue
                cb = QCheckBox(cam, sec.content)
                cb.hide()
                cb.clicked.connect(lambda checked, c=cam: self._on_cam_box_clicked(c, checked))
                self._cat_widgets[cat][cam] = cb
                self._cam_boxes[cam].append(cb)
            self._cam_inner_lay.addWidget(sec)
        self._cam_inner_lay.addStretch(1)
        self.cam_scroll.setWidget(cam_inner)
        cl.addWidget(self.cam_scroll, 1)
        self._cam_watch = _ResizeWatch(self._update_cam_cols, self)
        self.cam_scroll.viewport().installEventFilter(self._cam_watch)
        root.addWidget(cam_box, 1)

        self._render_cameras()
        self.cam_search.setFocus()

    # ── small helpers ────────────────────────────────────────────
    @property
    def source_mode(self) -> str:
        return "window" if self._src_window.isChecked() else "cpva"

    def _cam_cell_px(self) -> int:
        """Width of one camera cell — measured once; the names never change."""
        cached = getattr(self, "_cam_cell_px_cached", None)
        if cached:
            return cached
        fm = QFontMetrics(self.font())
        widest = max(fm.horizontalAdvance(c) for c in self._cam_order)
        self._cam_cell_px_cached = widest + 24 + 18
        return self._cam_cell_px_cached

    def _update_cam_cols(self):
        w = self.cam_scroll.viewport().width() - 34
        if w <= 80:
            return
        cols = max(2, min(int(w // self._cam_cell_px()), 7))
        if cols != self._cam_cols:
            self._cam_cols = cols
            self._render_cameras()

    def _get_dest(self) -> Path | None:
        s = self.dest_edit.text().strip()
        if not s:
            ui.message(self, "warning", "Missing destination", "Select destination folder.")
            return None
        p = Path(s)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            ui.message(self, "error", "Destination error",
                       f"Cannot create/access destination:\n{p}\n\n{e}")
            return None
        return p

    def pick_dest(self):
        cur = self.dest_edit.text().strip()
        if cur and not core.is_unc_path(cur) and Path(cur).exists():
            initial = cur
        elif self._last_browse_dir and Path(self._last_browse_dir).exists():
            initial = self._last_browse_dir
        else:
            initial = str(Path.home())
        p = QFileDialog.getExistingDirectory(self, "Select destination folder", initial)
        if p:
            p = str(Path(p))
            self.dest_edit.setText(p)
            self._last_browse_dir = p

    def open_dest(self):
        p = self._get_dest()
        if not p:
            return
        try:
            core.open_in_explorer(p)
        except Exception as e:
            ui.message(self, "error", "Open folder error", str(e))

    def _set_busy(self, busy: bool):
        self._copy_btn.setEnabled(not busy)

    def _progress_show(self, total: int):
        self._prog_bar.setRange(0, max(1, total))
        self._prog_bar.setValue(0)
        self._prog_label.setText("")
        self._prog_bar.show()
        self._prog_label.show()

    def _progress_update(self, done: int, total: int, label: str = ""):
        self._prog_bar.setValue(done)
        self._prog_label.setText(f"{label}  ({done}/{total})" if label else f"{done}/{total}")

    def _progress_hide(self):
        self._prog_bar.hide()
        self._prog_label.hide()
        self._prog_label.setText("")

    # ── cameras ──────────────────────────────────────────────────
    def _selected_cameras(self) -> list[str]:
        return [cam for cam in self._cam_order if self._cam_sel[cam]]

    def _set_cam(self, cam: str, on: bool):
        """Set one camera without the side effects of a click."""
        if cam not in self._cam_sel:
            return
        self._cam_sel[cam] = on
        for cb in self._cam_boxes[cam]:
            if cb.isChecked() != on:
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)

    def _on_cam_box_clicked(self, cam: str, checked: bool):
        """A camera ticked by hand: the monitors follow the selected cameras."""
        self._set_cam(cam, checked)
        for cat in self._cam_cats.get(cam, []):
            self._update_category_check(cat)
        self._recompute_monitors_from_cameras()
        self._update_name_label()

    def _toggle_category_all(self, cat: str, want: bool):
        for cam in core.CAM_CATEGORIES.get(cat, []):
            self._set_cam(cam, want)
        for c in core.CAM_CATEGORIES:
            self._update_category_check(c)
        self._update_name_label()

    def _update_category_check(self, cat: str):
        cams = core.CAM_CATEGORIES.get(cat, [])
        if not cams:
            return
        all_on = all(self._cam_sel.get(cam, False) for cam in cams)
        box = self._cat_boxes.get(cat)
        if box is not None and box.isChecked() != all_on:
            box.blockSignals(True)
            box.setChecked(all_on)
            box.blockSignals(False)

    def _sync_sections_to_selected_cams(self):
        for cat, cams in core.CAM_CATEGORIES.items():
            sec = self._cam_sections.get(cat)
            if sec:
                sec.set_expanded(any(self._cam_sel.get(c, False) for c in cams))

    def _render_cameras(self):
        """Show the cameras matching the search, `_cam_cols` per row.

        The boxes are made once and only re-placed / re-enabled here: this
        runs on every keystroke in the search box, on a column-count change
        and on a source or layout switch."""
        if not self._cam_grids:
            return
        q = core.norm_query(self.cam_search.text())
        cols = self._cam_cols
        source, station, layout = self.source_mode, self._station_id, self._layout_cb.currentText()
        for cat, cams in core.CAM_CATEGORIES.items():
            sec = self._cam_sections[cat]
            grid = self._cam_grids[cat]
            widgets = self._cat_widgets[cat]
            visible = [c for c in widgets if (not q or q in core.norm_query(c))]
            if q and visible:
                sec.set_expanded(True)
            sec.setVisible(bool(visible))
            key = (tuple(visible), cols)
            if self._cat_grid_key.get(cat) != key:
                for cb in widgets.values():
                    grid.removeWidget(cb)
                    cb.hide()
                for i, cam in enumerate(visible):
                    r, c = divmod(i, cols)
                    grid.addWidget(widgets[cam], r, c, Qt.AlignLeft)
                    widgets[cam].show()
                for c in range(8):
                    grid.setColumnStretch(c, 1 if c < cols else 0)
                self._cat_grid_key[cat] = key
            for cam in visible:
                available = core.cam_available(cam, source, station, layout)
                cb = widgets[cam]
                cb.setEnabled(available)
                cb.setToolTip(core.cam_tooltip(cam, available))

    # ── monitors ─────────────────────────────────────────────────
    def _sync_monitor_vars_len(self, n: int):
        """Grow monitor_vars to at least n entries; never shrink (preset /
        manual references point into it)."""
        while len(self.monitor_vars) < n:
            self.monitor_vars.append(False)

    def _set_monitor(self, idx: int, on: bool):
        self.monitor_vars[idx] = on
        box = self._mon_boxes.get(idx)
        if box is not None and box.isChecked() != on:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)

    def _set_all_screens(self, on: bool):
        if self._all_screens_cb.isChecked() != on:
            self._all_screens_cb.blockSignals(True)
            self._all_screens_cb.setChecked(on)
            self._all_screens_cb.blockSignals(False)

    def _clear_layout_grid(self):
        while self._layout_grid.count():
            it = self._layout_grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._mon_boxes.clear()

    def _mon_box(self, text: str, idx: int | None) -> QCheckBox:
        cb = QCheckBox(text, self._layout_holder)
        cb.setMinimumWidth(52)
        if idx is None:
            cb.setEnabled(False)
        else:
            cb.setChecked(self.monitor_vars[idx])
            cb.clicked.connect(lambda checked, i=idx: self._on_specific_monitor_toggle(i, checked))
            self._mon_boxes[idx] = cb
        return cb

    def _render_monitor_layout(self, layout_name: str):
        self._clear_layout_grid()
        rects = core.list_monitors_rects()[:8]
        # Office box with one monitor: preview the wall shapes instead.
        if self._station_id == core.CZOW_STATION and len(rects) <= 1:
            self._render_layout_placeholder(layout_name)
            return
        # A real wall: the grid from live pixel geometry. Positions never
        # change, so a physical cell always toggles the right monitor however
        # Windows numbers the displays.
        self._monitors = rects
        self._sync_monitor_vars_len(len(rects))
        for (r, c), idx in sorted(core.geometric_monitor_grid(rects).items()):
            if idx >= len(self.monitor_vars):
                continue
            self._layout_grid.addWidget(self._mon_box(f"M{idx + 1}", idx), r, c)

    def _render_layout_placeholder(self, layout_name: str):
        """Static MONITOR_LAYOUTS preview — only on the office box, which has a
        single monitor but emulates a wall through the Layout box."""
        for mon, (r, c) in core.MONITOR_LAYOUTS.get(layout_name, {}).items():
            idx = int(mon) - 1
            ok = 0 <= idx < len(self.monitor_vars)
            self._layout_grid.addWidget(self._mon_box(str(mon), idx if ok else None), r, c)

    def _on_layout_selected(self, _i=None):
        self._render_monitor_layout(self._layout_cb.currentText())
        self._apply_monitor_effective()
        self._render_cameras()

    def _apply_monitor_effective(self):
        manual_all = bool(self._manual_all_screens)
        manual_set: set[int] = set() if manual_all else set(self._manual_monitors)
        if self._preset_all_screens_ref > 0:
            effective_all = True
            effective_set: set[int] = set()
        else:
            preset_set = set(self._preset_mon_ref.keys())
            if preset_set and not manual_all:
                self._manual_all_screens = False
                self._set_all_screens(False)
            effective_set = set() if manual_all else (manual_set | preset_set)
            effective_all = manual_all
        if not effective_all:
            effective_set = {i for i in effective_set if 0 <= i < len(self.monitor_vars)}
        if effective_all:
            for i in range(len(self.monitor_vars)):
                self._set_monitor(i, False)
            self._set_all_screens(True)
            self._manual_all_screens = True
            self._manual_monitors.clear()
        else:
            self._set_all_screens(False)
            for i in range(len(self.monitor_vars)):
                self._set_monitor(i, i in effective_set)

    def _on_all_screens_toggle(self, checked: bool):
        self._manual_all_screens = bool(checked)
        if not checked:
            self._preset_all_screens_ref = 0
        else:
            self._manual_monitors.clear()
            for i in range(len(self.monitor_vars)):
                self._set_monitor(i, False)
        self._apply_monitor_effective()
        self._update_name_label()

    def _on_specific_monitor_toggle(self, idx: int, checked: bool):
        self.monitor_vars[idx] = bool(checked)
        if checked:
            self._manual_monitors.add(idx)
        else:
            self._manual_monitors.discard(idx)
            self._preset_mon_ref.pop(idx, None)
        self._manual_all_screens = False
        self._preset_all_screens_ref = 0
        self._set_all_screens(False)
        self._apply_monitor_effective()
        self._update_name_label()

    def _selected_monitors(self) -> list[int] | None:
        if self._all_screens_cb.isChecked():
            return None
        sel = [i for i, v in enumerate(self.monitor_vars) if v]
        return sel if sel else None

    def _recompute_monitors_from_cameras(self):
        """The monitors showing the selected cameras' CSS windows become the
        monitor selection."""
        cams = self._selected_cameras()
        self._preset_mon_ref.clear()
        self._preset_all_screens_ref = 0
        self._manual_monitors.clear()
        self._manual_all_screens = False
        for mi in core.monitors_for_cameras(cams):
            if 0 <= mi < len(self.monitor_vars):
                self._preset_mon_ref[mi] = 1
        self._apply_monitor_effective()

    def on_identify_monitors(self):
        if not identify_monitors(self):
            ui.message(self, "info", "Identify", "No monitors detected.")

    # ── presets ──────────────────────────────────────────────────
    def save_presets(self) -> str | None:
        return core.save_presets(self._presets)

    def rebuild_preset_buttons(self):
        for btn in self.preset_buttons.values():
            self._presets_grid.removeWidget(btn)
            btn.deleteLater()
        self.preset_buttons.clear()
        cols = 6
        for i, name in enumerate(self._presets):
            btn = QPushButton(name, self._presets_holder)
            btn.setProperty("presetOn", False)
            cams = self._presets.get(name, {}).get("cams", [])
            btn.setToolTip("\n".join(cams) if cams else "No cameras")
            btn.clicked.connect(lambda _=False, n=name: self.toggle_preset(n))
            r, c = divmod(i, cols)
            self._presets_grid.addWidget(btn, r, c)
            self.preset_buttons[name] = btn
        self._refresh_preset_button_styles()

    def _refresh_preset_button_styles(self):
        for n, btn in self.preset_buttons.items():
            on = n in self._active_presets
            if bool(btn.property("presetOn")) != on:
                btn.setProperty("presetOn", on)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def _preset_add(self, name: str):
        cams = list(self._presets.get(name, {}).get("cams", []))
        for cam in cams:
            self._set_cam(cam, True)
        self._manual_all_screens = False
        detected = core.monitors_for_cameras(cams)
        self._preset_cached_monitors[name] = detected
        if detected:
            for mi in detected:
                if 0 <= mi < len(self.monitor_vars):
                    self._preset_mon_ref[mi] = self._preset_mon_ref.get(mi, 0) + 1
        else:
            self._preset_all_screens_ref += 1
        self._apply_monitor_effective()

    def _preset_remove(self, name: str):
        cams = list(self._presets.get(name, {}).get("cams", []))
        for cam in cams:
            self._set_cam(cam, False)
        # The monitors found when the preset was switched on — not a new lookup.
        cached = self._preset_cached_monitors.pop(name, None)
        if cached:
            for mi in cached:
                if mi in self._preset_mon_ref:
                    self._preset_mon_ref[mi] -= 1
                    if self._preset_mon_ref[mi] <= 0:
                        del self._preset_mon_ref[mi]
        elif cached is not None:
            # cached as empty → it had added the all-screens fallback
            self._preset_all_screens_ref = max(0, self._preset_all_screens_ref - 1)
        self._apply_monitor_effective()

    def toggle_preset(self, name: str):
        if name in self._active_presets:
            self._active_presets.remove(name)
            self._preset_remove(name)
        else:
            self._active_presets.add(name)
            self._preset_add(name)
        self._refresh_preset_button_styles()
        for cat in core.CAM_CATEGORIES:
            self._update_category_check(cat)
        self._sync_sections_to_selected_cams()
        self._update_name_label()

    def _open_preset_manager(self):
        dlg = PresetManager(self)
        dlg.show()
        return dlg

    def _clear_all_selections(self):
        self._active_presets.clear()
        self._preset_mon_ref.clear()
        self._preset_all_screens_ref = 0
        self._manual_monitors.clear()
        self._manual_all_screens = True
        for cam in self._cam_order:
            self._set_cam(cam, False)
        self._set_all_screens(True)
        for i in range(len(self.monitor_vars)):
            self._set_monitor(i, False)
        for cat in core.CAM_CATEGORIES:
            self._update_category_check(cat)
        self._refresh_preset_button_styles()
        self._sync_sections_to_selected_cams()
        self._update_name_label()
        self.log("[CLEAR] All selections cleared.")

    # ── name label + selected list ───────────────────────────────
    def _planned_output_count(self, monitors_sel, cams_sel) -> int:
        return core.planned_output_count(monitors_sel, self._all_screens_cb.isChecked(),
                                         cams_sel)

    def _update_name_label(self):
        total = self._planned_output_count(self._selected_monitors(), self._selected_cameras())
        self._name_label.setText("File name:" if total <= 1 else "Folder name:")
        self._refresh_selected_cams_table()

    def _refresh_selected_cams_table(self):
        """Everything selected, all at once: about five rows per column, the box
        growing in height to fit; a horizontal bar only when the columns really
        run out of width (then the columns get more rows first)."""
        if not hasattr(self, "_sel_grid"):
            return
        while self._sel_grid.count():
            it = self._sel_grid.takeAt(0)
            if it.widget() is not None:
                it.widget().deleteLater()

        mon_sel = self._selected_monitors()
        if mon_sel is None and self._all_screens_cb.isChecked():
            mon_items = ["All screens"]
        elif mon_sel:
            mon_items = [f"M{m + 1}" for m in mon_sel]
        else:
            mon_items = []
        cam_items = self._selected_cameras()

        cells = len(mon_items) + (1 if mon_items else 0)
        if cam_items:
            if cells % 5:
                cells += 5 - (cells % 5)   # the heading always starts a fresh column
            cells += len(cam_items) + 1

        f_norm = QFont(self.font())
        f_bold = QFont(self.font())
        f_bold.setBold(True)
        fm_n, fm_b = QFontMetrics(f_norm), QFontMetrics(f_bold)
        widest = max([fm_b.horizontalAdvance("Cameras:"), fm_b.horizontalAdvance("Monitors:")]
                     + [fm_n.horizontalAdvance(t) for t in mon_items + cam_items])
        col_w = widest + 16
        avail_w = self._sel_scroll.viewport().width()
        if avail_w < 50:
            avail_w = 600
        max_cols = max(1, avail_w // col_w)
        rows_per_col = 5
        if cells and -(-cells // rows_per_col) > max_cols:
            rows_per_col = -(-cells // max_cols)

        pos = [0, 0]   # row, column

        def _place(text, bold=False):
            lbl = QLabel(text, self._sel_inner)
            if bold:
                lbl.setStyleSheet("font-weight: bold;")
            self._sel_grid.addWidget(lbl, pos[0], pos[1], Qt.AlignLeft | Qt.AlignTop)
            # Shown now, not when the layout gets round to it: a hidden label
            # does not count in the size measured below, and the box came out
            # one line high.
            lbl.show()
            pos[0] += 1
            if pos[0] >= rows_per_col:
                pos[0] = 0
                pos[1] += 1

        if mon_items:
            _place("Monitors:", bold=True)
            for m in mon_items:
                _place(m)
        if cam_items:
            if pos[0] != 0:
                pos[0] = 0
                pos[1] += 1
            _place("Cameras:", bold=True)
            for c in cam_items:
                _place(c)
        if not mon_items and not cam_items:
            _place("Nothing selected")

        self._sel_grid.activate()
        hint = self._sel_inner.sizeHint()
        self._sel_inner.resize(hint)
        need_h = max(hint.height(), fm_n.height() + 2)
        bar_h = (self._sel_scroll.horizontalScrollBar().sizeHint().height()
                 if hint.width() > avail_w + 2 else 0)
        self._sel_scroll.setFixedHeight(need_h + bar_h + 2)

    # ── side windows ─────────────────────────────────────────────
    def on_labels(self):
        dlg = LabelsDialog(self, self._selected_cameras())
        dlg.show()
        return dlg

    def on_detail(self):
        out_dir = self._last_out_dir
        if out_dir is None:
            s = self.dest_edit.text().strip()
            if not s:
                ui.message(self, "warning", "Detail", "Set destination folder first.")
                return None
            out_dir = Path(s)
        dlg = DetailDialog(self, out_dir)
        dlg.show()
        return dlg

    def _new_preview(self, paths, **kw) -> PreviewWindow:
        import shiboken6
        self._previews = [w for w in self._previews if shiboken6.isValid(w)]
        win = PreviewWindow(paths, **kw)
        self._previews.append(win)
        return win

    def _open_preview_with_callbacks(self, monitors_local, all_screens_local,
                                     cams_local, source_mode, copied_files):
        paths = sorted(copied_files or [], key=lambda p: p.name)
        if not paths:
            return

        def _retry(mon=monitors_local, all_s=all_screens_local, cams=cams_local,
                   src=source_mode):
            QTimer.singleShot(0, lambda: self._run_copy_worker(mon, all_s, cams, src))

        self._new_preview(paths, on_try_again=_retry).show()

    # ── Copy (one set for the moment of the click) ───────────────
    def on_copy(self):
        monitors_local = self._selected_monitors()
        all_screens_local = self._all_screens_cb.isChecked()
        cams_local = self._selected_cameras()
        if not cams_local and monitors_local is None and not all_screens_local:
            ui.message(self, "warning", "Copy", "Select cameras or monitors.")
            return
        self._run_copy_worker(monitors_local, all_screens_local, cams_local, self.source_mode)

    def _run_copy_worker(self, monitors_local, all_screens_local, cams_local, source_mode):
        if self._busy:
            self.log("[UI] Ignored: busy=True")
            return
        dest = self._get_dest()
        if not dest:
            return
        self._busy = True
        self._set_busy(True)
        run_name = self.run_name_edit.text()
        total = core.planned_output_count(monitors_local, all_screens_local, cams_local)
        self._progress_show(total)

        def worker():
            t_click_ns = time.time_ns()
            try:
                self.log("=== COPY START ===")
                self.log(f"Source mode: {source_mode}")
                self.log(f"Station: {self._station_id}")
                self.log("Selected monitors: "
                         f"{'ALL' if monitors_local is None else [m + 1 for m in monitors_local]}")
                self.log(f"Selected cameras: {cams_local}")
                self.log(f"Destination: {dest}")

                run_ts = datetime.fromtimestamp(t_click_ns / 1_000_000_000).strftime(
                    core.RUN_FOLDER_FMT)
                custom_name = core.sanitize_folder_name(run_name)
                out_dir = core.ensure_output_path(dest, run_name, total)
                copied_files: list[Path] = []
                problems: list[str] = []
                done = [0]

                def _step(label: str):
                    done[0] += 1
                    on_ui(self._progress_update, done[0], total, label)

                # One desktop grab for every selected monitor.
                targets = core.monitor_targets(out_dir, run_ts, monitors_local,
                                               all_screens_local, custom_name, total)
                if targets:
                    with core.timed(self.log, "screenshots"):
                        for out in core.take_screenshots_monitors_png(targets):
                            self.log(f"[SHOT] saved {out.name}")
                            copied_files.append(out)
                            _step(out.name)

                if cams_local:
                    with core.timed(self.log, "cameras"):
                        res = self.copier.copy_cameras(
                            cams_local, source_mode, t_click_ns, out_dir,
                            custom_name, total,
                            on_file=lambda pth, off: _step(pth.name),
                            newest=True,   # Copy means "what the cameras have now"
                        )
                    copied_files.extend(res["copied"])
                    problems.extend(res["problems"])
                    ages = [a for a in res["ages"].values() if a is not None]
                    if ages:
                        # The absolute figure is mostly the archive clock running
                        # behind this PC; what matters is how close together the
                        # pictures are.
                        rel = max(ages) - min(ages)
                        self.log(f"[COPY] newest frames within {core.fmt_span_ms(rel / 1_000_000)} "
                                 f"of each other (archive clock is "
                                 f"{core.fmt_span_ms(min(ages) / 1_000_000)} behind this PC)")

                self.log(f"=== COPY END | Outputs = {len(copied_files)} | "
                         f"Problems = {len(problems)} ===")
                self._last_out_dir = out_dir
                for p in problems:
                    self.log(f" [!] {p}")
                self.log(f"=== DONE - {out_dir} ===")

                if copied_files:
                    def _open(ff=list(copied_files)):
                        if self._preview_cb.isChecked():
                            self._open_preview_with_callbacks(
                                monitors_local, all_screens_local, cams_local,
                                source_mode, ff)
                    on_ui(_open)
            except Exception as e:
                err = repr(e)
                self.log(f"[FATAL] {err}")
                on_ui(ui.message, self, "error", "Copy error", err)
            finally:
                self._busy = False
                on_ui(self._progress_hide)
                on_ui(self._set_busy, False)

        threading.Thread(target=worker, daemon=True, name="copy").start()

    # ── one persistent preview window for a whole auto run ───────
    def _auto_preview_window(self) -> PreviewWindow | None:
        """The single live preview window, created on first use."""
        if not self._preview_cb.isChecked() or self._auto_preview_closed_by_user:
            return None
        import shiboken6
        win = self._auto_preview
        if win is not None and shiboken6.isValid(win):
            return win
        win = self._new_preview([], cycle_mode=True, on_cycle_change=self._forget_cycle)
        self._auto_preview = win

        def _closed(w=win):
            if self._auto_preview is w:
                self._auto_preview = None
                if self._auto_copy_active:
                    # Closed by the user: stays closed for the rest of this run.
                    self._auto_preview_closed_by_user = True
        win.closed.connect(_closed)
        win.show()
        return win

    def _live_auto_preview(self) -> PreviewWindow | None:
        import shiboken6
        win = self._auto_preview
        if win is None or not shiboken6.isValid(win):
            self._auto_preview = None
            return None
        return win

    def _forget_cycle(self, cycle: int):
        """The preview deleted a cycle's files — drop it from the run too."""
        self._auto_cycle_history = [e for e in self._auto_cycle_history
                                    if e["cycle"] != cycle]

    def _close_auto_preview(self):
        win = self._live_auto_preview()
        self._auto_preview = None
        if win is not None:
            win.close()

    def _preview_begin_cycle(self, cycle: int, target_ns: int):
        """Register a cycle before its files start arriving."""
        self._auto_cycle_history.append({
            "cycle": cycle,
            "ts": datetime.fromtimestamp(target_ns / 1_000_000_000).strftime("%H:%M:%S"),
            "target_ns": target_ns,
            "files": [],
        })
        win = self._auto_preview_window()
        if win is None:
            return
        win.set_cycle_history(self._auto_cycle_history)
        # The previous cycle stays on screen until this one's first picture
        # lands, so there is no blank flash.
        win.set_cycle_status(f"cycle {cycle} running…")

    def _preview_add_file(self, cycle: int, path: Path, offset_ns: int | None = None):
        """One file has landed — show it straight away."""
        entry = next((e for e in self._auto_cycle_history if e["cycle"] == cycle), None)
        if entry is None:
            return
        entry["files"].append(path)
        win = self._live_auto_preview()
        if win is None:
            return
        if offset_ns:
            win.set_caption_extra(path, f"(−{core.fmt_span_ms(offset_ns / 1_000_000)})")
        shown = win.shown_cycle
        if win.following and (shown is None or cycle > shown):
            win.show_cycle(cycle)   # draws this file too — it is already in the entry
            return
        if shown == cycle:
            win.append_path(path)

    def _preview_end_cycle(self, cycle: int, summary: str):
        win = self._live_auto_preview()
        if win is not None:
            win.set_cycle_status(summary)

    # ── auto copy: an exact time grid, recorded per cycle ────────
    def _toggle_auto_copy(self):
        if self._auto_copy_active:
            self._stop_auto_copy()
        else:
            self._start_auto_copy()

    def _auto_interval_s(self) -> int:
        """The running run's interval (fixed at Start, so editing the box
        mid-run cannot shift the time grid); otherwise what the box says."""
        p = self._auto_copy_params
        if self._auto_copy_active and p and p.get("interval_s"):
            return p["interval_s"]
        return max(core.AUTO_MIN_INTERVAL_S, int(self._auto_interval.value()))

    def _auto_max_cycles(self) -> int:
        p = self._auto_copy_params
        if self._auto_copy_active and p and "max_cycles" in p:
            return p["max_cycles"]
        return max(0, int(self._auto_cycles.value()))

    def _start_auto_copy(self):
        monitors_local = self._selected_monitors()
        all_screens_local = self._all_screens_cb.isChecked()
        cams_local = self._selected_cameras()
        source_mode = self.source_mode
        if not cams_local and monitors_local is None and not all_screens_local:
            ui.message(self, "warning", "Auto copy", "Select cameras or monitors.")
            return
        dest = self._get_dest()
        if not dest:
            return

        self._close_auto_preview()
        self._auto_preview_closed_by_user = False   # a new run shows the preview again
        interval_s = self._auto_interval_s()
        max_cycles = self._auto_max_cycles()
        total = core.planned_output_count(monitors_local, all_screens_local, cams_local)
        self._auto_copy_active = True
        self._auto_copy_count = 0
        self._auto_cycle_history = []
        self.copier.last_frame = {}
        self._auto_ticks_done = False
        self._auto_dropped = 0
        self._auto_offset_ns = None   # measured by the first cycle
        self._auto_tick_queue = queue.Queue()
        run_name = self.run_name_edit.text()
        self._auto_copy_params = {
            "monitors": monitors_local,
            "all_screens": all_screens_local,
            "cams": cams_local,
            "source": source_mode,
            "custom_name": core.sanitize_folder_name(run_name),
            "total": total,
            "dest": dest,
            "interval_s": interval_s,
            "max_cycles": max_cycles,
            # Resolved once: the folder cannot change during a run.
            "out_dir": core.ensure_output_path(dest, run_name, total),
        }
        # Wall clock for the recorded moments, monotonic for the scheduling
        # (a clock change must not move the ticks).
        self._auto_t0_ns = time.time_ns()
        self._auto_t0_mono = time.monotonic()
        self._auto_btn.setText("Stop auto")
        self._auto_btn.setIcon(ui.icon("stop"))
        self.log(f"[AUTO] Started — every {interval_s}s, max_cycles={max_cycles}")
        # Each run its own number, so the previous run's worker cannot still be
        # draining when Stop is followed straight away by Start.
        self._auto_run_id += 1
        threading.Thread(target=self._auto_drain_loop, args=(self._auto_run_id,),
                         daemon=True, name="auto-drain").start()
        if cams_local and source_mode == "cpva":
            threading.Thread(target=self.copier.prime, args=(list(cams_local),),
                             daemon=True, name="auto-prime").start()
        self._on_auto_tick(0)

    def _stop_auto_copy(self):
        self._auto_copy_active = False
        self._auto_ticks_done = True
        self._auto_timer.stop()
        self._auto_btn.setText("Start auto")
        self._auto_btn.setIcon(ui.icon("play"))
        self._auto_status.setText("")
        self._progress_hide()
        self.log("[AUTO] Stopped.")

    def _schedule_auto_tick(self, n: int):
        """Fire tick n at t0 + n*interval — never 'interval after the last one',
        so a slow cycle cannot stretch the ones after it."""
        if not self._auto_copy_active:
            return
        delay_ms = core.auto_tick_delay_ms(self._auto_t0_mono, n,
                                           self._auto_interval_s(), time.monotonic())
        self._auto_next_n = n
        self._auto_timer.start(delay_ms)

    def _on_auto_tick(self, n: int):
        if not self._auto_copy_active or self._auto_copy_params is None:
            return
        max_cycles = self._auto_max_cycles()
        if max_cycles > 0 and n >= max_cycles:
            self._auto_ticks_done = True
            self.log(f"[AUTO] All {max_cycles} cycles issued — finishing.")
            return
        cycle = n + 1
        self._auto_copy_count = cycle
        target_ns = core.auto_tick_target_ns(self._auto_t0_ns, n, self._auto_interval_s())
        p = self._auto_copy_params
        self._auto_status.setText(f"cycle {cycle}" + (f"/{max_cycles}" if max_cycles > 0 else ""))
        self._preview_begin_cycle(cycle, target_ns)
        tick = {
            "cycle": cycle,
            "target_ns": target_ns,
            "run_ts": datetime.fromtimestamp(target_ns / 1_000_000_000).strftime(
                core.RUN_FOLDER_FMT),
            "out_dir": p["out_dir"],
        }
        # The screen can only be captured now, so it is captured at the tick
        # itself — never when a lagging queue gets round to this cycle.
        if p["monitors"] is not None or p["all_screens"]:
            threading.Thread(target=self._grab_tick_screenshots, args=(tick,),
                             daemon=True, name="auto-shot").start()
        self._auto_tick_queue.put(tick)
        self._schedule_auto_tick(n + 1)

    def _grab_tick_screenshots(self, tick: dict):
        p = self._auto_copy_params
        if p is None:
            return
        targets = core.monitor_targets(tick["out_dir"], tick["run_ts"], p["monitors"],
                                       p["all_screens"], p["custom_name"], p["total"])
        try:
            written = core.take_screenshots_monitors_png(targets)
        except Exception as e:
            self.log(f"[AUTO] cycle {tick['cycle']}: screenshot failed: {e}")
            return
        for path in written:
            on_ui(self._preview_add_file, tick["cycle"], path)

    def _auto_drain_loop(self, run_id: int):
        """One worker draining the tick queue in order. A tick carries its own
        recorded moment, so a cycle that starts late still copies the frames
        from its own time — the copying may lag, the pictures may not."""
        q = self._auto_tick_queue
        while self._auto_copy_active and self._auto_run_id == run_id:
            try:
                tick = q.get(timeout=0.25)
            except queue.Empty:
                if self._auto_ticks_done:
                    on_ui(self._finish_auto_if, run_id)
                    return
                continue
            if not self._auto_copy_active or self._auto_run_id != run_id:
                return
            try:
                self._process_tick(tick)
            except Exception as e:
                self.log(f"[AUTO] cycle {tick.get('cycle')}: {e!r}")

    def _finish_auto_if(self, run_id: int):
        if self._auto_copy_active and self._auto_run_id == run_id:
            self._stop_auto_copy()

    def _process_tick(self, tick: dict):
        p = self._auto_copy_params
        if p is None:
            return
        cycle, target_ns = tick["cycle"], tick["target_ns"]
        lag_ns = time.time_ns() - target_ns
        behind = self._auto_tick_queue.qsize()

        if lag_ns > core.AUTO_BACK_WINDOW_NS:
            # Past the backward window the archive can no longer answer for
            # this moment, so the cycle is dropped — out loud, never silently.
            self._auto_dropped += 1
            self.log(f"[AUTO] cycle {cycle} dropped — {core.fmt_span_ms(lag_ns / 1_000_000)} "
                     f"behind ({behind} waiting)")
            on_ui(self._preview_end_cycle, cycle, f"cycle {cycle} dropped (too far behind)")
            return
        if not p["cams"]:
            on_ui(self._preview_end_cycle, cycle, f"cycle {cycle} done")
            return

        # Cycle 1 takes the newest picture each camera has and measures from it
        # how far the archive's clock runs behind this computer's; every later
        # cycle asks for its own moment shifted by that offset.
        anchor = self._auto_offset_ns is None
        offset = self._auto_offset_ns or 0
        eff_target = target_ns - offset

        t0 = time.perf_counter()
        res = self.copier.copy_cameras(
            p["cams"], p["source"], eff_target, tick["out_dir"],
            p["custom_name"], p["total"],
            # On the anchor cycle the "age" is the clock offset, not something
            # worth writing under a picture.
            on_file=lambda pth, off, c=cycle, a=anchor: on_ui(
                self._preview_add_file, c, pth, None if a else off),
            cycle=cycle, newest=anchor,
            keep_going=lambda: self._auto_copy_active,
        )
        self._last_out_dir = tick["out_dir"]
        ages = [a for a in res["ages"].values() if a is not None]

        if ages and (anchor or not res["copied"]):
            # Anchor cycle, or a cycle where every camera fell outside the
            # window while still delivering NEW frames — the offset itself must
            # be wrong, so it is re-measured.
            new_offset = offset + min(ages)
            if anchor:
                self.log(f"[AUTO] Archive clock runs {core.fmt_span_ms(min(ages) / 1_000_000)} "
                         f"behind this PC — later cycles are corrected by that.")
            else:
                self.log(f"[AUTO] Nothing was in range — re-measuring the archive clock "
                         f"({core.fmt_span_ms(offset / 1_000_000)} → "
                         f"{core.fmt_span_ms(new_offset / 1_000_000)}).")
            self._auto_offset_ns = new_offset

        spread = ""
        if ages:
            rel = [a - min(ages) for a in ages] if anchor else ages
            lo = core.fmt_span_ms(min(rel) / 1_000_000)
            hi = core.fmt_span_ms(max(rel) / 1_000_000)
            spread = f"  spread {lo}" if lo == hi else f"  spread {lo}–{hi}"
        line = (f"[AUTO] cycle {cycle} @ {tick['run_ts'][-8:]}  "
                f"copied {len(res['copied'])}/{len(p['cams'])}{spread}  "
                f"lag {core.fmt_span_ms(lag_ns / 1_000_000)}  "
                f"took {time.perf_counter() - t0:.1f}s")
        if behind:
            line += f"  ({behind} cycles waiting)"
        self.log(line)
        if res["ages"]:
            base = min(ages) if (anchor and ages) else 0
            detail = "  ".join(
                f"{cam} −{core.fmt_span_ms((age - base) / 1_000_000)}" if age is not None
                else f"{cam} −"
                for cam, age in res["ages"].items())
            self.log(f"       frames: {detail}")
        for prob in res["problems"]:
            self.log(f" [!] {prob}")
        summary = f"cycle {cycle} — {len(res['copied'])}/{len(p['cams'])} cameras{spread}"
        on_ui(self._preview_end_cycle, cycle, summary)
        if behind:
            on_ui(self._auto_status.setText, f"cycle {self._auto_copy_count} · {behind} waiting")

    # ── Live ─────────────────────────────────────────────────────
    def _toggle_live(self):
        if self.live.active:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        cams_local = self._selected_cameras()
        if not cams_local:
            ui.message(self, "warning", "Live", "Select at least one camera.")
            return
        dest = self._get_dest()
        if not dest:
            return
        live_dir = dest / "Live"
        try:
            live_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            ui.message(self, "error", "Live", f"Cannot create the Live folder:\n{e}")
            return
        self._live_btn.setText("Stop live")
        self._live_btn.setIcon(ui.icon("stop"))
        self._live_status.setText("running…")
        self.log(f"[LIVE] Started → {live_dir}")
        self.live.start(cams_local, live_dir)

    def _stop_live(self):
        files = self.live.stop()
        self._live_btn.setText("Start live")
        self._live_btn.setIcon(ui.icon("record"))
        self._live_status.setText(f"stopped — {len(files)} files")
        self.log(f"[LIVE] Stopped. Total files: {len(files)}")
        if files and self._preview_cb.isChecked():
            paths = sorted(files, key=lambda p: p.name)
            QTimer.singleShot(200, lambda: self._new_preview(paths).show())

    # ── shutting down ────────────────────────────────────────────
    def closeEvent(self, ev):
        import shiboken6
        if self._auto_copy_active:
            self._stop_auto_copy()
        if self.live.active:
            self.live.stop()
        for w in self._previews:
            if shiboken6.isValid(w):
                w.close()
        super().closeEvent(ev)


def main() -> int:
    ico = core.icon_file()
    app_id = _icon_app_id(_APP_ID_PREFIX, ico)
    if app_id:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    ui.apply_app_style(app)
    if ico:
        app.setWindowIcon(QIcon(str(ico)))
    ui.init_ui_thread()
    win = ScreenshotsWindow()
    if os.environ.get("SSQ_NO_ACTIVATE"):
        # testing/audit_icon_launch.py: open without taking the keyboard
        # focus. The attribute alone was not enough once (measured
        # 16.10.2026: the window came up in the foreground), so the window
        # also refuses focus altogether for that run.
        win.setAttribute(Qt.WA_ShowWithoutActivating, True)
        win.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    win.show()
    _nudge_taskbar_icon(int(win.winId()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
