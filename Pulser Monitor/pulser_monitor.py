"""
pulser_monitor.py — Pulser Monitor

Scans camera images over a date range and detects "dead pulsers":
rectangular regions that are less bright than expected.

Run standalone: python pulser_monitor.py
"""

import csv
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

try:
    from zoneinfo import ZoneInfo
    TZ_PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    TZ_PRAGUE = None

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure

from PySide6.QtCore import (
    Qt, QDate, QObject, Signal, QEvent, QPoint, QRect,
    QRunnable, QThreadPool,
)
from PySide6.QtGui import (
    QColor, QTextCharFormat, QPixmap, QImage, QPainter, QPen, QBrush,
    QFont, QIcon, QCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication, QButtonGroup,
    QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QSizePolicy, QSpinBox, QStatusBar, QStyledItemDelegate, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

# ── CONSTANTS ──────────────────────────────────────────────────────────────────

PRIMARY     = "#1565C0"
PRIMARY_HOV = "#0D47A1"
SUCCESS     = "#2E7D32"
SUCCESS_HOV = "#1B5E20"
DANGER      = "#B71C1C"
DANGER_HOV  = "#7F0000"

IMAGES_ROOT_OPTIONS = {
    "Lab":    Path(r"//users-L3.tier0.lcs.local/cpva-image-2026"),
    "Office": Path(r"\\users-L3.tier0.lcs.local\cpva-image-2026"),
}

# Fixed cameras — display name → CPVA folder name
CAMERAS = {
    "PD1M1": "C03-015-PD1M1DF-_-IMG",
    "PD2M1": "C03-019-PD2M1DF-_-IMG",
    "PD3M1": "C03-023-PD3M1DF-_-IMG",
    "PD4M1": "C03-027-PD4M1DF-_-IMG",
}

# Minimum file size (bytes) for a valid image on each camera
CAM_MIN_BYTES = {
    "PD1M1": 350 * 1024,
    "PD2M1": 1900 * 1024,
    "PD3M1": 400 * 1024,
    "PD4M1": 400 * 1024,
}

# Column labels left→right per camera (PD2M1 columns are mirrored)
CAM_COLS = {
    "PD1M1": ["A", "B", "C", "D", "E"],
    "PD2M1": ["E", "D", "C", "B", "A"],
    "PD3M1": ["A", "B", "C", "D", "E"],
    "PD4M1": ["A", "B", "C", "D", "E"],
}
_ROW_LABELS = ["1", "2", "3", "4", "5", "6", "7", "8"]

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
_NS_19_RE  = re.compile(r"\d{19}")

_ROI_COLORS = [
    "#FF9800", "#2196F3", "#4CAF50", "#E91E63",
    "#9C27B0", "#00BCD4", "#FF5722", "#FFEB3B",
]

_BTN = (
    f"QPushButton {{background:{PRIMARY};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{PRIMARY_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_SM = (
    f"QPushButton {{background:{PRIMARY};color:#fff;border:none;"
    f"border-radius:4px;padding:3px 8px;font-size:11px;}}"
    f"QPushButton:hover{{background:{PRIMARY_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_DANGER_STYLE = (
    f"QPushButton {{background:{DANGER};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{DANGER_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_BTN_SUCCESS_STYLE = (
    f"QPushButton {{background:{SUCCESS};color:#fff;border:none;"
    f"border-radius:4px;padding:5px 10px;}}"
    f"QPushButton:hover{{background:{SUCCESS_HOV};}}"
    f"QPushButton:disabled{{background:#aaa;}}"
)
_CHECKBOX_STYLE = (
    "QCheckBox::indicator{width:14px;height:14px;border:2px solid #aaa;"
    "border-radius:2px;background:#fff;}"
    "QCheckBox::indicator:checked{border:2px solid #2d7dff;background:#2d7dff;}"
)
_CAL_STYLE = """
QCalendarWidget QWidget { background: #f6f6f6; color: #111; }
QCalendarWidget QAbstractItemView {
    background: #fcfcfc; color: #111;
    selection-background-color: #2d7dff; selection-color: #fff;
    alternate-background-color: #f2f2f2; gridline-color: #d8d8d8; }
QCalendarWidget QTableView {
    background: #fcfcfc;
    selection-background-color: #2d7dff; selection-color: #fff;
    gridline-color: #d8d8d8; outline: 0; }
QCalendarWidget QToolButton {
    background: #efefef; border: 1px solid #c8c8c8;
    padding: 3px 6px; border-radius: 4px; color: #111; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #efefef; }
QCalendarWidget QAbstractItemView:enabled { color: #111; }
"""

# ── DATA CLASSES ──────────────────────────────────────────────────────────────

@dataclass
class RoiDefinition:
    name: str
    x: int
    y: int
    w: int
    h: int
    ref_brightness: float = 0.0
    color: str = "#FF9800"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RoiDefinition":
        return cls(
            name=str(d.get("name", "ROI")),
            x=int(d.get("x", 0)), y=int(d.get("y", 0)),
            w=int(d.get("w", 10)), h=int(d.get("h", 10)),
            ref_brightness=float(d.get("ref_brightness", 0.0)),
            color=str(d.get("color", "#FF9800")),
        )


@dataclass
class SamplePoint:
    ts_ns: int
    path: Path
    roi_means: list
    roi_norms: list
    roi_alive: list


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _parse_ts_from_path(p: Path) -> "int | None":
    for m in _NS_19_RE.finditer(p.stem):
        ts = int(m.group())
        if 946684800_000_000_000 <= ts <= 4102444800_000_000_000:
            return ts
    return None


def _read_image_max_sample(path: Path) -> "int | None":
    ext = path.suffix.lower()
    try:
        with PilImage.open(str(path)) as pil:
            if ext in (".tif", ".tiff"):
                tag_data = pil.tag_v2 if hasattr(pil, "tag_v2") else getattr(pil, "tag", {})
                val = tag_data.get(281)
                if val is not None:
                    if isinstance(val, (list, tuple)):
                        val = val[0]
                    return int(val)
            elif ext == ".png":
                info = pil.info
                for key in ("MaxValue", "max_value", "MaxSampleValue", "max_sample_value",
                            "BitDepthMax", "bit_depth_max"):
                    v = info.get(key)
                    if v is not None:
                        try:
                            return int(float(v))
                        except (ValueError, TypeError):
                            pass
                for v in info.values():
                    if isinstance(v, str) and v.strip().isdigit():
                        n = int(v.strip())
                        if 256 <= n <= 65535:
                            return n
    except Exception:
        pass
    return None


def _load_as_float32_gray(path: Path, fallback_bit_depth: int = 12) -> "np.ndarray | None":
    try:
        with PilImage.open(str(path)) as pil:
            arr = np.array(pil.convert("I"), dtype=np.float32)
        max_meta = _read_image_max_sample(path)
        if max_meta and max_meta > 0:
            max_val = float(max_meta)
        else:
            max_val = float((1 << fallback_bit_depth) - 1)
            arr_max = float(arr.max())
            if arr_max > max_val:
                max_val = arr_max or 1.0
        return arr / max_val
    except Exception:
        return None


def _normalize(roi_means: list, norm_mode: str, rois: list) -> list:
    if norm_mode == "median":
        med = float(np.median(roi_means)) if roi_means else 1.0
        if med <= 0:
            med = 1.0
        return [m / med for m in roi_means]
    elif norm_mode == "reference":
        return [m / (roi.ref_brightness or 1.0) for m, roi in zip(roi_means, rois)]
    else:
        return list(roi_means)


def _ns_to_dt(ts_ns: int) -> datetime:
    tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
    return datetime.fromtimestamp(ts_ns / 1e9, tz=tz)


def _find_latest_cam_folder(images_root: Path, camera: str) -> "Path | None":
    now = datetime.now(timezone.utc)
    for delta in range(0, 24 * 7):
        cur = now - timedelta(hours=delta)
        folder = (images_root / str(cur.year) / str(cur.month)
                  / str(cur.day) / str(cur.hour) / camera)
        try:
            if folder.exists() and folder.is_dir():
                with os.scandir(str(folder)) as it:
                    for e in it:
                        if e.is_file() and Path(e.path).suffix.lower() in IMAGE_EXTS:
                            return folder
        except Exception:
            pass
    return None


def _find_ref_image(images_root: Path, cam_key: str) -> "Path | None":
    """Return the first qualifying image (by file size) from the most recent folder.
    Falls back to any image found if nothing meets the size threshold."""
    folder_name = CAMERAS.get(cam_key)
    if not folder_name:
        return None
    min_bytes = CAM_MIN_BYTES.get(cam_key, 0)
    folder = _find_latest_cam_folder(images_root, folder_name)
    if not folder:
        return None
    fallback = None
    try:
        with os.scandir(str(folder)) as it:
            for e in it:
                if not e.is_file():
                    continue
                p = Path(e.path)
                if p.suffix.lower() not in IMAGE_EXTS:
                    continue
                if fallback is None:
                    fallback = p
                try:
                    if p.stat().st_size >= min_bytes:
                        return p
                except Exception:
                    continue
    except Exception:
        pass
    return fallback


def _make_default_rois(cam_key: str, img_w: int, img_h: int) -> list:
    """Create a default 5-column × 8-row ROI grid for the given camera."""
    cols = CAM_COLS.get(cam_key, ["A", "B", "C", "D", "E"])
    rows = _ROW_LABELS
    n_cols, n_rows = len(cols), len(rows)
    margin_x = int(img_w * 0.06)
    margin_y = int(img_h * 0.06)
    usable_w = img_w - 2 * margin_x
    usable_h = img_h - 2 * margin_y
    cell_w = usable_w // n_cols
    cell_h = usable_h // n_rows
    pad = 2
    rois = []
    for ri, row_lbl in enumerate(rows):
        for ci, col_lbl in enumerate(cols):
            x = margin_x + ci * cell_w + pad
            y = margin_y + ri * cell_h + pad
            w = max(4, cell_w - 2 * pad)
            h = max(4, cell_h - 2 * pad)
            name = f"{col_lbl}{row_lbl}"
            color = _ROI_COLORS[(ri * n_cols + ci) % len(_ROI_COLORS)]
            rois.append(RoiDefinition(name=name, x=x, y=y, w=w, h=h, color=color))
    return rois


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color:#ccc;margin:2px 0;")
    return f


def _group_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "font-size:10px;color:#777;font-weight:700;letter-spacing:1px;padding-top:4px;"
    )
    return lbl


# ── CALENDAR COMPONENTS ───────────────────────────────────────────────────────

class _WeekendDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        col = index.column()
        if col < 1:
            return
        date_val = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date_val, QDate) and date_val.isValid():
            if date_val.dayOfWeek() in (6, 7):
                option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
                option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))
            return
        if col in (6, 7):
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))


class _NoScrollCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._noscroll = set()

    def _install(self):
        for child in self.findChildren(QAbstractScrollArea):
            if id(child) not in self._noscroll:
                child.installEventFilter(self)
                child.viewport().installEventFilter(self)
                self._noscroll.add(id(child))

    def showEvent(self, event):
        super().showEvent(event)
        self._install()

    def wheelEvent(self, event):
        event.accept()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            event.accept()
            return True
        return super().eventFilter(obj, event)


class _NoScrollComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


# ── TIME WINDOW DIALOG ────────────────────────────────────────────────────────

class _TimeWindowDialog(QDialog):
    def __init__(self, start_dt: "datetime | None" = None,
                 end_dt: "datetime | None" = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Time window")
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        if start_dt is None:
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if end_dt is None:
            end_dt = now

        def _make_cal(init_dt: datetime) -> _NoScrollCalendar:
            cal = _NoScrollCalendar()
            cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
            cal.setGridVisible(True)
            cal.setNavigationBarVisible(True)
            cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
            view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
            if view:
                view.setItemDelegate(_WeekendDelegate(view))
            hf = QTextCharFormat()
            hf.setForeground(QColor("#111111"))
            cal.setHeaderTextFormat(hf)
            wf = QTextCharFormat()
            wf.setForeground(QColor("#111111"))
            for day in [Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                        Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday]:
                cal.setWeekdayTextFormat(day, wf)
            wf_we = QTextCharFormat()
            wf_we.setForeground(QColor("#cc0000"))
            for day in [Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday]:
                cal.setWeekdayTextFormat(day, wf_we)
            cal.setStyleSheet(_CAL_STYLE)
            cal.setSelectedDate(QDate(init_dt.year, init_dt.month, init_dt.day))
            return cal

        grp_s = QGroupBox("Start point")
        sl = QVBoxLayout(grp_s)
        self._cal_start = _make_cal(start_dt)
        self._hour_start = QSpinBox()
        self._hour_start.setRange(0, 23)
        self._hour_start.setValue(start_dt.hour)
        self._hour_start.setFixedWidth(70)
        hr_s = QHBoxLayout()
        hr_s.addWidget(QLabel("Hour:"))
        hr_s.addWidget(self._hour_start)
        hr_s.addStretch(1)
        sl.addWidget(self._cal_start)
        sl.addLayout(hr_s)

        grp_e = QGroupBox("End point")
        el = QVBoxLayout(grp_e)
        self._cal_end = _make_cal(end_dt)
        self._hour_end = QSpinBox()
        self._hour_end.setRange(0, 23)
        self._hour_end.setValue(end_dt.hour)
        self._hour_end.setFixedWidth(70)
        btn_now = QPushButton("Now")
        btn_now.setFixedWidth(48)
        btn_now.clicked.connect(self._go_now)
        hr_e = QHBoxLayout()
        hr_e.addWidget(QLabel("Hour:"))
        hr_e.addWidget(self._hour_end)
        hr_e.addWidget(btn_now)
        hr_e.addStretch(1)
        el.addWidget(self._cal_end)
        el.addLayout(hr_e)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(grp_s)
        row.addWidget(grp_e)
        main = QVBoxLayout(self)
        main.addLayout(row)
        main.addWidget(btns)

    def _go_now(self):
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        self._cal_end.setSelectedDate(QDate(now.year, now.month, now.day))
        self._hour_end.setValue(now.hour)

    def _on_accept(self):
        s, e = self.selected_range()
        if e < s:
            QMessageBox.warning(self, "Invalid range", "End must be after start.")
            return
        self.accept()

    def _get_start(self) -> datetime:
        d = self._cal_start.selectedDate()
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_start.value(), 0, 0, tzinfo=tz)

    def _get_end(self) -> datetime:
        d = self._cal_end.selectedDate()
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        return datetime(d.year(), d.month(), d.day(), self._hour_end.value(), 59, 59, tzinfo=tz)

    def selected_range(self) -> "tuple[datetime, datetime]":
        return self._get_start(), self._get_end()


# ── SCAN SIGNALS & WORKERS ────────────────────────────────────────────────────

class _FileScanSignals(QObject):
    files_ready = Signal(object)  # list of (ts_ns, Path)
    error       = Signal(str)


class _ScanSignals(QObject):
    progress = Signal(int, int)   # done, total
    sample   = Signal(object)     # SamplePoint
    finished = Signal()
    error    = Signal(str)
    log_msg  = Signal(str)


class _FileScanWorker(QRunnable):
    def __init__(self, sig: _FileScanSignals, images_root: Path, camera: str,
                 start_ns: int, end_ns: int, min_size: int,
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._root = images_root
        self._camera = camera
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._min_size = min_size
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        try:
            utc = timezone.utc
            start_utc = datetime.fromtimestamp(self._start_ns / 1e9, tz=utc)
            end_utc = datetime.fromtimestamp(self._end_ns / 1e9, tz=utc)
            cur = start_utc.replace(minute=0, second=0, microsecond=0)
            end_hour = end_utc.replace(minute=0, second=0, microsecond=0)
            files = []
            while cur <= end_hour:
                if self._gen_box[0] != self._my_gen:
                    return
                folder = (self._root / str(cur.year) / str(cur.month)
                          / str(cur.day) / str(cur.hour) / self._camera)
                try:
                    if folder.exists() and folder.is_dir():
                        with os.scandir(str(folder)) as it:
                            for entry in it:
                                if not entry.is_file():
                                    continue
                                p = Path(entry.path)
                                if p.suffix.lower() not in IMAGE_EXTS:
                                    continue
                                if self._min_size > 0:
                                    try:
                                        if p.stat().st_size < self._min_size:
                                            continue
                                    except Exception:
                                        continue
                                ts = _parse_ts_from_path(p)
                                if ts is not None and self._start_ns <= ts <= self._end_ns:
                                    files.append((ts, p))
                except Exception:
                    pass
                cur += timedelta(hours=1)
            files.sort(key=lambda x: x[0])
            if self._gen_box[0] == self._my_gen:
                self._sig.files_ready.emit(files)
        except Exception as exc:
            self._sig.error.emit(str(exc))


class _ScanWorker(QRunnable):
    def __init__(self, sig: _ScanSignals, files: list, rois: list,
                 norm_mode: str, threshold: float, fallback_bits: int,
                 gen_box: list, my_gen: int):
        super().__init__()
        self._sig = sig
        self._files = files
        self._rois = rois
        self._norm_mode = norm_mode
        self._threshold = threshold
        self._fallback_bits = fallback_bits
        self._gen_box = gen_box
        self._my_gen = my_gen
        self.setAutoDelete(True)

    def run(self):
        total = len(self._files)
        try:
            for idx, (ts_ns, path) in enumerate(self._files):
                if self._gen_box[0] != self._my_gen:
                    return
                arr = _load_as_float32_gray(path, self._fallback_bits)
                if arr is None:
                    self._sig.log_msg.emit(f"Skip {path.name}: load failed")
                    self._sig.progress.emit(idx + 1, total)
                    continue
                h, w = arr.shape
                roi_means = []
                for roi in self._rois:
                    x0 = max(0, roi.x); y0 = max(0, roi.y)
                    x1 = min(w, roi.x + roi.w); y1 = min(h, roi.y + roi.h)
                    if x1 <= x0 or y1 <= y0:
                        roi_means.append(0.0)
                    else:
                        roi_means.append(float(arr[y0:y1, x0:x1].mean()))
                roi_norms = _normalize(roi_means, self._norm_mode, self._rois)
                roi_alive = [n >= self._threshold for n in roi_norms]
                self._sig.sample.emit(SamplePoint(ts_ns, path, roi_means, roi_norms, roi_alive))
                self._sig.progress.emit(idx + 1, total)
            if self._gen_box[0] == self._my_gen:
                self._sig.finished.emit()
        except Exception as exc:
            self._sig.error.emit(str(exc))


# ── ROI EDITOR ────────────────────────────────────────────────────────────────

class _ImageCanvas(QWidget):
    roi_selected = Signal(int)
    roi_created  = Signal()

    _HANDLE_PX = 7  # hit-test radius around resize handles in screen pixels

    def __init__(self, rois: list, parent=None):
        super().__init__(parent)
        self._rois = rois
        self._pm: "QPixmap | None" = None
        self._img_w = 0
        self._img_h = 0
        self._scale = 1.0
        self._off_x = 0
        self._off_y = 0
        self._selected_idx: "int | None" = None

        # Interaction state machine
        self._mode: str = ""          # "draw" | "move" | "resize" | ""
        self._drag_start: "QPoint | None" = None   # screen pos at press
        self._drag_last:  "QPoint | None" = None   # screen pos at last move (move mode)
        self._draw_end:   "QPoint | None" = None   # current rubber-band end
        self._resize_handle: str = ""              # e.g. "TL", "B", "R"
        self._drag_roi_origin: tuple = (0, 0, 0, 0)  # (x,y,w,h) when drag started

        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    @property
    def img_w(self) -> int:
        return self._img_w

    @property
    def img_h(self) -> int:
        return self._img_h

    def set_image(self, arr8: np.ndarray):
        h, w = arr8.shape[:2]
        if arr8.ndim == 2:
            qimg = QImage(bytes(arr8.data), w, h, w, QImage.Format.Format_Grayscale8)
        else:
            rgb = np.ascontiguousarray(arr8[:, :, :3])
            qimg = QImage(bytes(rgb.data), w, h, w * 3, QImage.Format.Format_RGB888)
        self._pm = QPixmap.fromImage(qimg.copy())
        self._img_w = w
        self._img_h = h
        self._update_tf()
        self.update()

    def _update_tf(self):
        if self._img_w == 0 or self._img_h == 0:
            return
        scale = min(self.width() / self._img_w, self.height() / self._img_h)
        self._scale = scale
        self._off_x = int((self.width() - self._img_w * scale) / 2)
        self._off_y = int((self.height() - self._img_h * scale) / 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_tf()
        self.update()

    # ── coordinate helpers ────────────────────────────────────────────────────

    def _d2i(self, px: int, py: int) -> "tuple[int, int]":
        if self._scale == 0:
            return 0, 0
        return int((px - self._off_x) / self._scale), int((py - self._off_y) / self._scale)

    def _i2d(self, ix: int, iy: int) -> QPoint:
        return QPoint(int(ix * self._scale + self._off_x),
                      int(iy * self._scale + self._off_y))

    def _roi_drect(self, roi: RoiDefinition) -> QRect:
        tl = self._i2d(roi.x, roi.y)
        br = self._i2d(roi.x + roi.w, roi.y + roi.h)
        return QRect(tl, br)

    def _hit_roi(self, px: int, py: int) -> "int | None":
        ix, iy = self._d2i(px, py)
        for i in range(len(self._rois) - 1, -1, -1):
            r = self._rois[i]
            if r.x <= ix < r.x + r.w and r.y <= iy < r.y + r.h:
                return i
        return None

    # ── handle helpers ────────────────────────────────────────────────────────

    def _handle_positions(self, roi: RoiDefinition) -> dict:
        """Returns {name: (screen_x, screen_y)} for the 8 resize handles."""
        tl = self._i2d(roi.x, roi.y)
        br = self._i2d(roi.x + roi.w, roi.y + roi.h)
        mx = (tl.x() + br.x()) // 2
        my = (tl.y() + br.y()) // 2
        return {
            "TL": (tl.x(), tl.y()), "T": (mx, tl.y()), "TR": (br.x(), tl.y()),
            "R":  (br.x(), my),
            "BR": (br.x(), br.y()), "B": (mx, br.y()), "BL": (tl.x(), br.y()),
            "L":  (tl.x(), my),
        }

    def _hit_handle(self, px: int, py: int, roi_idx: int) -> str:
        hs = self._HANDLE_PX
        for name, (hx, hy) in self._handle_positions(self._rois[roi_idx]).items():
            if abs(px - hx) <= hs and abs(py - hy) <= hs:
                return name
        return ""

    @staticmethod
    def _cursor_for_handle(handle: str) -> Qt.CursorShape:
        return {
            "TL": Qt.CursorShape.SizeFDiagCursor,
            "BR": Qt.CursorShape.SizeFDiagCursor,
            "TR": Qt.CursorShape.SizeBDiagCursor,
            "BL": Qt.CursorShape.SizeBDiagCursor,
            "T":  Qt.CursorShape.SizeVerCursor,
            "B":  Qt.CursorShape.SizeVerCursor,
            "L":  Qt.CursorShape.SizeHorCursor,
            "R":  Qt.CursorShape.SizeHorCursor,
        }.get(handle, Qt.CursorShape.SizeAllCursor)

    # ── mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        px, py = int(event.position().x()), int(event.position().y())

        # 1) Handle hit on selected ROI → resize
        if self._selected_idx is not None and 0 <= self._selected_idx < len(self._rois):
            handle = self._hit_handle(px, py, self._selected_idx)
            if handle:
                roi = self._rois[self._selected_idx]
                self._mode = "resize"
                self._resize_handle = handle
                self._drag_start = QPoint(px, py)
                self._drag_roi_origin = (roi.x, roi.y, roi.w, roi.h)
                self.setCursor(QCursor(self._cursor_for_handle(handle)))
                return

        # 2) Interior hit → select + prepare move
        hit = self._hit_roi(px, py)
        if hit is not None:
            self._selected_idx = hit
            self.roi_selected.emit(hit)
            roi = self._rois[hit]
            self._mode = "move"
            self._drag_start = QPoint(px, py)
            self._drag_last  = QPoint(px, py)
            self._drag_roi_origin = (roi.x, roi.y, roi.w, roi.h)
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            self.update()
            return

        # 3) Empty area → draw new ROI
        self._selected_idx = None
        self.roi_selected.emit(-1)
        self._mode = "draw"
        self._drag_start = QPoint(px, py)
        self._draw_end   = QPoint(px, py)
        self.update()

    def mouseMoveEvent(self, event):
        px, py = int(event.position().x()), int(event.position().y())

        if self._mode == "draw":
            self._draw_end = QPoint(px, py)
            self.update()
            return

        if self._mode == "move" and self._selected_idx is not None and self._drag_last:
            # Incremental delta from last position to avoid rounding drift
            ddx = px - self._drag_last.x()
            ddy = py - self._drag_last.y()
            dix = int(ddx / self._scale) if self._scale else 0
            diy = int(ddy / self._scale) if self._scale else 0
            if dix != 0 or diy != 0:
                roi = self._rois[self._selected_idx]
                roi.x = max(0, min(self._img_w - roi.w, roi.x + dix))
                roi.y = max(0, min(self._img_h - roi.h, roi.y + diy))
                self._drag_last = QPoint(px, py)
                self.update()
            return

        if self._mode == "resize" and self._selected_idx is not None and self._drag_start:
            # Compute total delta from drag start against stored origin
            dx_total = int((px - self._drag_start.x()) / self._scale) if self._scale else 0
            dy_total = int((py - self._drag_start.y()) / self._scale) if self._scale else 0
            ox, oy, ow, oh = self._drag_roi_origin
            x, y, w, h = ox, oy, ow, oh
            handle = self._resize_handle
            if "L" in handle:
                nw = ow - dx_total
                if nw >= 4:
                    x, w = ox + dx_total, nw
            if "R" in handle:
                nw = ow + dx_total
                if nw >= 4:
                    w = nw
            if "T" in handle:
                nh = oh - dy_total
                if nh >= 4:
                    y, h = oy + dy_total, nh
            if "B" in handle:
                nh = oh + dy_total
                if nh >= 4:
                    h = nh
            x = max(0, x); y = max(0, y)
            if self._img_w > 0: w = min(w, self._img_w - x)
            if self._img_h > 0: h = min(h, self._img_h - y)
            roi = self._rois[self._selected_idx]
            roi.x, roi.y, roi.w, roi.h = x, y, max(4, w), max(4, h)
            self.update()
            return

        # Hover (no button held) → update cursor
        if self._mode == "":
            self._update_hover_cursor(px, py)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._mode == "draw":
            end_px = int(event.position().x())
            end_py = int(event.position().y())
            if self._drag_start:
                dx = abs(end_px - self._drag_start.x())
                dy = abs(end_py - self._drag_start.y())
                if dx >= 5 and dy >= 5:
                    x0, y0 = self._d2i(min(self._drag_start.x(), end_px),
                                       min(self._drag_start.y(), end_py))
                    x1, y1 = self._d2i(max(self._drag_start.x(), end_px),
                                       max(self._drag_start.y(), end_py))
                    x0 = max(0, x0); y0 = max(0, y0)
                    if self._img_w > 0:
                        x1 = min(self._img_w, x1); y1 = min(self._img_h, y1)
                    roi = RoiDefinition(
                        name=f"Pulser_{len(self._rois) + 1}",
                        x=x0, y=y0, w=max(1, x1 - x0), h=max(1, y1 - y0),
                        color=_ROI_COLORS[len(self._rois) % len(_ROI_COLORS)],
                    )
                    self._rois.append(roi)
                    self._selected_idx = len(self._rois) - 1
                    self.roi_created.emit()
                    self.roi_selected.emit(self._selected_idx)
        self._mode = ""
        self._drag_start = self._drag_last = self._draw_end = None
        self._update_hover_cursor(int(event.position().x()), int(event.position().y()))
        self.update()

    def _update_hover_cursor(self, px: int, py: int):
        if self._selected_idx is not None and 0 <= self._selected_idx < len(self._rois):
            handle = self._hit_handle(px, py, self._selected_idx)
            if handle:
                self.setCursor(QCursor(self._cursor_for_handle(handle)))
                return
        hit = self._hit_roi(px, py)
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor if hit is not None
                               else Qt.CursorShape.CrossCursor))

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        if self._pm and not self._pm.isNull():
            p.drawPixmap(self._off_x, self._off_y,
                         int(self._img_w * self._scale),
                         int(self._img_h * self._scale), self._pm)
        else:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Load a reference image to define ROIs\n"
                       "(drag on empty area to draw · click to select · drag to move)")
        _ROI_BORDER = QColor(204, 0, 0)
        for i, roi in enumerate(self._rois):
            rect = self._roi_drect(roi)
            is_sel = (i == self._selected_idx)
            c = QColor(roi.color)
            p.setPen(QPen(_ROI_BORDER, 5 if is_sel else 2))
            p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 55 if is_sel else 25)))
            p.drawRect(rect)
            font = QFont()
            font.setPointSize(8)
            font.setBold(is_sel)
            p.setFont(font)
            p.setPen(QColor(255, 255, 255) if is_sel else _ROI_BORDER)
            p.drawText(rect.adjusted(3, 2, 0, 0),
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, roi.name)
            # Draw handles on selected ROI
            if is_sel:
                p.setPen(QPen(QColor(255, 255, 255), 1))
                p.setBrush(QBrush(_ROI_BORDER))
                for hx, hy in self._handle_positions(roi).values():
                    p.drawRect(QRect(hx - 4, hy - 4, 8, 8))
        # Rubber band while drawing
        if self._mode == "draw" and self._drag_start and self._draw_end:
            p.setPen(QPen(_ROI_BORDER, 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            x0 = min(self._drag_start.x(), self._draw_end.x())
            y0 = min(self._drag_start.y(), self._draw_end.y())
            p.drawRect(QRect(x0, y0,
                             abs(self._draw_end.x() - self._drag_start.x()),
                             abs(self._draw_end.y() - self._drag_start.y())))
        p.end()

    def select(self, idx: "int | None"):
        self._selected_idx = idx
        self.update()

    def refresh(self):
        self.update()


class ROIEditorDialog(QDialog):
    def __init__(self, rois: list, parent=None,
                 cam_key: str = "", images_root: "Path | None" = None):
        super().__init__(parent)
        title = f"ROI Editor — {cam_key}" if cam_key else "ROI Editor"
        self.setWindowTitle(title)
        self.resize(960, 680)
        import copy
        self._rois: list = copy.deepcopy(rois)
        self._selected_idx: "int | None" = None
        self._cam_key: str = cam_key
        self._images_root: "Path | None" = images_root
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        tb = QHBoxLayout()
        btn_img = QPushButton("Load image…")
        btn_img.setStyleSheet(_BTN_SM)
        btn_img.clicked.connect(self._load_image_manual)
        tb.addWidget(btn_img)
        if self._cam_key and self._images_root:
            btn_auto_img = QPushButton("Auto-load latest")
            btn_auto_img.setStyleSheet(_BTN_SM)
            btn_auto_img.clicked.connect(self._auto_load_ref)
            tb.addWidget(btn_auto_img)
        tb.addSpacing(6)
        if self._cam_key:
            btn_grid = QPushButton("Auto-create grid")
            btn_grid.setStyleSheet(_BTN_SM)
            btn_grid.clicked.connect(self._auto_grid)
            tb.addWidget(btn_grid)
        tb.addSpacing(6)
        tb.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit()
        self._name_edit.setFixedWidth(110)
        self._name_edit.setPlaceholderText("ROI name")
        tb.addWidget(self._name_edit)
        btn_rename = QPushButton("Rename")
        btn_rename.setFixedWidth(68)
        btn_rename.clicked.connect(self._rename)
        tb.addWidget(btn_rename)
        tb.addSpacing(6)
        self._btn_del = QPushButton("Delete")
        self._btn_del.setStyleSheet(_BTN_DANGER_STYLE)
        self._btn_del.setFixedWidth(68)
        self._btn_del.clicked.connect(self._delete)
        self._btn_del.setEnabled(False)
        tb.addWidget(self._btn_del)
        tb.addStretch(1)
        btn_load_j = QPushButton("Load configuration…")
        btn_save_j = QPushButton("Save configuration…")
        btn_load_j.clicked.connect(self._load_json)
        btn_save_j.clicked.connect(self._save_json)
        tb.addWidget(btn_load_j)
        tb.addWidget(btn_save_j)

        self._hint = QLabel(self._hint_text())
        self._hint.setStyleSheet("font-size:10px;color:#555;")

        self._canvas = _ImageCanvas(self._rois, self)
        self._canvas.roi_selected.connect(self._on_selected)
        self._canvas.roi_created.connect(self._on_created)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay.addLayout(tb)
        lay.addWidget(self._hint)
        lay.addWidget(self._canvas, 1)
        lay.addWidget(btns)

    def showEvent(self, event):
        super().showEvent(event)
        if self._cam_key and self._images_root and self._canvas.img_w == 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(80, self._auto_load_ref)

    def _hint_text(self) -> str:
        return (f"Drag to draw ROIs · Click to select · "
                f"{len(self._rois)} ROI(s) defined")

    def _auto_load_ref(self):
        if not (self._cam_key and self._images_root):
            return
        self._hint.setText("Searching for reference image…")
        QApplication.processEvents()
        ref = _find_ref_image(self._images_root, self._cam_key)
        if not ref:
            self._hint.setText(
                f"No qualifying image found for {self._cam_key} "
                f"(min {CAM_MIN_BYTES.get(self._cam_key, 0) // 1024} kB). "
                f"Load manually.")
            return
        self._load_image_from_path(ref)
        if not self._rois:
            self._auto_grid()

    def _load_image_manual(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open reference image", "",
            "Images (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;All files (*)")
        if path:
            self._load_image_from_path(Path(path))

    def _load_image_from_path(self, path: Path):
        try:
            arr = _load_as_float32_gray(path)
            if arr is None:
                raise ValueError("Could not read image")
            lo, hi = float(arr.min()), float(arr.max())
            if hi > lo:
                arr8 = np.clip((arr - lo) / (hi - lo) * 255, 0, 255).astype(np.uint8)
            else:
                arr8 = np.zeros(arr.shape, dtype=np.uint8)
            self._canvas.set_image(arr8)
            self._hint.setText(
                f"Loaded: {path.name}  ({arr.shape[1]}×{arr.shape[0]})  —  "
                f"{self._hint_text()}")
        except Exception as exc:
            QMessageBox.warning(self, "Load image", f"Failed:\n{exc}")

    def _auto_grid(self):
        w, h = self._canvas.img_w, self._canvas.img_h
        if w == 0 or h == 0:
            QMessageBox.information(self, "Auto-create grid",
                                    "Load a reference image first.")
            return
        new_rois = _make_default_rois(self._cam_key, w, h)
        self._rois.clear()
        self._rois.extend(new_rois)
        self._selected_idx = None
        self._name_edit.clear()
        self._btn_del.setEnabled(False)
        self._canvas.select(None)
        self._canvas.refresh()
        self._hint.setText(
            f"Grid created (cols: {' '.join(CAM_COLS.get(self._cam_key, []))}, "
            f"rows: 1–{len(_ROW_LABELS)})  —  {self._hint_text()}")

    def _on_selected(self, idx: int):
        self._selected_idx = idx if idx >= 0 else None
        self._name_edit.setText(self._rois[idx].name if idx >= 0 else "")
        self._btn_del.setEnabled(idx >= 0)

    def _on_created(self):
        self._selected_idx = len(self._rois) - 1
        self._name_edit.setText(self._rois[self._selected_idx].name)
        self._hint.setText(self._hint_text())

    def _rename(self):
        if self._selected_idx is None:
            return
        name = self._name_edit.text().strip()
        if name:
            self._rois[self._selected_idx].name = name
            self._canvas.refresh()

    def _delete(self):
        if self._selected_idx is None:
            return
        del self._rois[self._selected_idx]
        self._selected_idx = None
        self._name_edit.clear()
        self._btn_del.setEnabled(False)
        self._canvas.select(None)
        self._hint.setText(self._hint_text())

    def _save_json(self):
        default = f"rois_{self._cam_key or 'cam'}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs", default, "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "cam_key": self._cam_key,
                           "rois": [r.to_dict() for r in self._rois]}, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Save ROIs", f"Failed:\n{exc}")

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            new_rois = [RoiDefinition.from_dict(r) for r in data.get("rois", [])]
            self._rois.clear()
            self._rois.extend(new_rois)
            self._selected_idx = None
            self._name_edit.clear()
            self._btn_del.setEnabled(False)
            self._canvas.select(None)
            self._canvas.refresh()
            self._hint.setText(self._hint_text())
        except Exception as exc:
            QMessageBox.warning(self, "Load ROIs", f"Failed:\n{exc}")

    def result_rois(self) -> list:
        return list(self._rois)


# ── RESULT TABS ───────────────────────────────────────────────────────────────

class _TimelineTab(QWidget):
    sample_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fig = Figure(figsize=(10, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._ax = self._fig.add_subplot(111)
        self._times_num = []
        self._sample_points = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._toolbar)
        lay.addWidget(self._canvas, 1)
        self._canvas.mpl_connect("button_press_event", self._on_click)

    def update_data(self, sample_points: list, rois: list, threshold: float):
        self._sample_points = sample_points
        self._ax.clear()
        if not sample_points or not rois:
            self._ax.text(0.5, 0.5, "No scan data yet",
                         transform=self._ax.transAxes,
                         ha="center", va="center", color="#888", fontsize=12)
            self._canvas.draw()
            return
        n_rois = len(rois)
        n_times = len(sample_points)
        alive_matrix = np.full((n_rois, n_times), np.nan)
        self._times_num = []
        for t_idx, sp in enumerate(sample_points):
            dt = _ns_to_dt(sp.ts_ns)
            self._times_num.append(mdates.date2num(dt))
            for r_idx in range(n_rois):
                if r_idx < len(sp.roi_alive):
                    alive_matrix[r_idx, t_idx] = 1.0 if sp.roi_alive[r_idx] else 0.0
        cmap = ListedColormap([DANGER, SUCCESS])
        cmap.set_bad("lightgray")
        masked = np.ma.masked_invalid(alive_matrix)
        if len(self._times_num) > 1:
            dt_half = (self._times_num[1] - self._times_num[0]) * 0.5
            t0 = self._times_num[0] - dt_half
            t1 = self._times_num[-1] + dt_half
        else:
            t0 = self._times_num[0] - 0.001
            t1 = self._times_num[0] + 0.001
        self._ax.imshow(masked, aspect="auto", cmap=cmap, vmin=0, vmax=1,
                        interpolation="nearest",
                        extent=[t0, t1, n_rois - 0.5, -0.5])
        self._ax.set_yticks(range(n_rois))
        self._ax.set_yticklabels([r.name for r in rois], fontsize=9)
        self._ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        self._ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._fig.autofmt_xdate(rotation=30)
        self._ax.set_title("Pulser alive (green) / dead (red)  —  click to inspect")
        self._ax.set_xlabel("Prague time")
        self._fig.tight_layout()
        self._canvas.draw()

    def _on_click(self, event):
        if event.inaxes != self._ax or not self._times_num:
            return
        x = event.xdata
        if x is None:
            return
        idx = int(np.argmin([abs(x - t) for t in self._times_num]))
        self.sample_selected.emit(idx)


class _OverlayCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm: "QPixmap | None" = None
        self._rois: list = []
        self._roi_norms: list = []
        self._roi_alive: list = []
        self._show_overlay = True
        self._show_labels = True
        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(40, 40, 40))
        self.setAutoFillBackground(True)
        self.setPalette(pal)

    def set_image(self, pm: "QPixmap | None"):
        self._pm = pm
        self.update()

    def set_overlay(self, rois: list, roi_norms: list, roi_alive: list):
        self._rois = rois
        self._roi_norms = roi_norms
        self._roi_alive = roi_alive
        self.update()

    def set_show_overlay(self, v: bool):
        self._show_overlay = v
        self.update()

    def set_show_labels(self, v: bool):
        self._show_labels = v
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        if self._pm is None or self._pm.isNull():
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Click a timeline cell to load image preview")
            p.end()
            return
        pm_w, pm_h = self._pm.width(), self._pm.height()
        cw, ch = self.width(), self.height()
        scale = min(cw / pm_w, ch / pm_h)
        dw, dh = int(pm_w * scale), int(pm_h * scale)
        ox, oy = (cw - dw) // 2, (ch - dh) // 2
        p.drawPixmap(ox, oy, dw, dh, self._pm)
        if self._show_overlay:
            for i, roi in enumerate(self._rois):
                alive = self._roi_alive[i] if i < len(self._roi_alive) else True
                c = QColor(SUCCESS if alive else DANGER)
                p.setPen(QPen(c, 2))
                p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 40)))
                rx = int(roi.x * scale + ox)
                ry = int(roi.y * scale + oy)
                rw = int(roi.w * scale)
                rh = int(roi.h * scale)
                p.drawRect(QRect(rx, ry, rw, rh))
                if self._show_labels and rh > 16:
                    norm_s = f"{self._roi_norms[i]:.2f}" if i < len(self._roi_norms) else ""
                    p.setPen(QColor(255, 255, 255))
                    f = QFont(); f.setPointSize(8); p.setFont(f)
                    p.drawText(QRect(rx + 2, ry + 2, rw - 2, rh - 2),
                               Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                               f"{roi.name}\n{norm_s}")
        p.end()


class _PreviewLoadSig(QObject):
    ready = Signal(object, int)


class _PreviewTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sample_points: list = []
        self._rois: list = []
        self._current_idx: "int | None" = None
        self._prev_gen = 0
        self._load_sig = _PreviewLoadSig()
        self._load_sig.ready.connect(self._on_ready)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        tb = QHBoxLayout()
        self._path_lbl = QLabel("—")
        self._path_lbl.setStyleSheet("font-size:10px;color:#555;")
        btn_prev = QPushButton("◀")
        btn_prev.setFixedWidth(32)
        btn_prev.clicked.connect(lambda: self._step(-1))
        btn_next = QPushButton("▶")
        btn_next.setFixedWidth(32)
        btn_next.clicked.connect(lambda: self._step(1))
        chk_ov = QCheckBox("Overlays")
        chk_ov.setChecked(True)
        chk_ov.setStyleSheet(_CHECKBOX_STYLE)
        chk_ov.stateChanged.connect(lambda s: self._canvas.set_show_overlay(s > 0))
        chk_lb = QCheckBox("Labels")
        chk_lb.setChecked(True)
        chk_lb.setStyleSheet(_CHECKBOX_STYLE)
        chk_lb.stateChanged.connect(lambda s: self._canvas.set_show_labels(s > 0))
        tb.addWidget(self._path_lbl, 1)
        tb.addWidget(btn_prev)
        tb.addWidget(btn_next)
        tb.addSpacing(8)
        tb.addWidget(chk_ov)
        tb.addWidget(chk_lb)
        self._canvas = _OverlayCanvas(self)
        lay.addLayout(tb)
        lay.addWidget(self._canvas, 1)

    def set_data(self, sample_points: list, rois: list):
        self._sample_points = sample_points
        self._rois = rois
        if sample_points:
            self._current_idx = 0
            self._load()

    def select_sample(self, idx: int):
        if not self._sample_points:
            return
        self._current_idx = max(0, min(len(self._sample_points) - 1, idx))
        self._load()

    def _step(self, delta: int):
        if self._current_idx is None or not self._sample_points:
            return
        self._current_idx = max(0, min(len(self._sample_points) - 1,
                                       self._current_idx + delta))
        self._load()

    def _load(self):
        if self._current_idx is None:
            return
        sp = self._sample_points[self._current_idx]
        self._path_lbl.setText(
            f"[{self._current_idx + 1}/{len(self._sample_points)}]  {sp.path.name}")
        self._canvas.set_overlay(self._rois, sp.roi_norms, sp.roi_alive)
        self._prev_gen += 1
        gen = self._prev_gen
        sig = self._load_sig
        path = sp.path

        class _Ldr(QRunnable):
            def run(self_ldr):
                try:
                    arr = _load_as_float32_gray(path)
                    if arr is None:
                        sig.ready.emit(None, gen)
                        return
                    arr8 = np.clip(arr * 255, 0, 255).astype(np.uint8)
                    h, w = arr8.shape
                    qimg = QImage(bytes(arr8.data), w, h, w, QImage.Format.Format_Grayscale8)
                    sig.ready.emit(QPixmap.fromImage(qimg.copy()), gen)
                except Exception:
                    sig.ready.emit(None, gen)

        ldr = _Ldr()
        ldr.setAutoDelete(True)
        QThreadPool.globalInstance().start(ldr)

    def _on_ready(self, pm, gen: int):
        if gen != self._prev_gen:
            return
        self._canvas.set_image(pm)


class _BrightnessTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fig = Figure(figsize=(10, 4))
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._ax = self._fig.add_subplot(111)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self._toolbar)
        lay.addWidget(self._canvas, 1)

    def update_data(self, sample_points: list, rois: list, threshold: float):
        self._ax.clear()
        if not sample_points or not rois:
            self._ax.text(0.5, 0.5, "No scan data yet",
                         transform=self._ax.transAxes,
                         ha="center", va="center", color="#888", fontsize=12)
            self._canvas.draw()
            return
        times = [mdates.date2num(_ns_to_dt(sp.ts_ns)) for sp in sample_points]
        for i, roi in enumerate(rois):
            norms = [sp.roi_norms[i] if i < len(sp.roi_norms) else 0.0
                     for sp in sample_points]
            self._ax.plot(times, norms,
                         color=_ROI_COLORS[i % len(_ROI_COLORS)],
                         label=roi.name, linewidth=1, alpha=0.85)
        self._ax.axhline(threshold, color="orange", linestyle="--",
                        linewidth=1.5, label=f"Threshold ({threshold:.2f})")
        self._ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        self._ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._fig.autofmt_xdate(rotation=30)
        self._ax.set_ylabel("Normalized brightness")
        self._ax.set_title("Brightness per pulser over time")
        ncol = max(1, min(len(rois), 6))
        self._ax.legend(fontsize=8, ncol=ncol, loc="upper right")
        self._fig.tight_layout()
        self._canvas.draw()


class _SummaryTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["ROI", "Uptime %", "Dead intervals", "First dropout", "Last dropout", "Last recovery"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget{background:#fff;gridline-color:#e0e0e0;}"
            "QTableWidget::item{background:#fff;color:#111;padding:2px 4px;}"
            "QTableWidget::item:alternate{background:#e8f0fe;color:#111;}"
            "QTableWidget::item:selected{background:#1565C0;color:#fff;}"
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        btn_exp = QPushButton("Export summary CSV…")
        btn_exp.setStyleSheet(_BTN)
        btn_exp.clicked.connect(self._export)
        lay.addWidget(self._table, 1)
        lay.addWidget(btn_exp)

    def update_data(self, sample_points: list, rois: list):
        self._rows = []
        self._table.setRowCount(0)
        if not sample_points or not rois:
            return
        for r_idx, roi in enumerate(rois):
            seq = [sp.roi_alive[r_idx] if r_idx < len(sp.roi_alive) else True
                   for sp in sample_points]
            total = len(seq)
            alive_n = sum(seq)
            uptime = 100.0 * alive_n / total if total else 0.0
            dead_intervals = 0
            first_drop = last_drop = last_rec = None
            in_dead = False
            for i, alive in enumerate(seq):
                ts = sample_points[i].ts_ns
                dt_s = _ns_to_dt(ts).strftime("%Y-%m-%d %H:%M")
                if not alive and not in_dead:
                    in_dead = True
                    dead_intervals += 1
                    if first_drop is None:
                        first_drop = dt_s
                    last_drop = dt_s
                elif alive and in_dead:
                    in_dead = False
                    last_rec = dt_s
            row = {
                "name": roi.name, "uptime": uptime,
                "dead": dead_intervals,
                "first_drop": first_drop or "—",
                "last_drop": last_drop or "—",
                "last_rec": last_rec or "—",
            }
            self._rows.append(row)
            r = self._table.rowCount()
            self._table.insertRow(r)

            def _item(txt, left=False):
                it = QTableWidgetItem(str(txt))
                it.setTextAlignment(
                    (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    if left else Qt.AlignmentFlag.AlignCenter)
                return it

            self._table.setItem(r, 0, _item(roi.name, left=True))
            self._table.setItem(r, 1, _item(f"{uptime:.1f}%"))
            self._table.setItem(r, 2, _item(str(dead_intervals)))
            self._table.setItem(r, 3, _item(row["first_drop"]))
            self._table.setItem(r, 4, _item(row["last_drop"]))
            self._table.setItem(r, 5, _item(row["last_rec"]))

    def _export(self):
        if not self._rows:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export summary", "pulser_summary.csv", "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["ROI", "Uptime %", "Dead intervals",
                            "First dropout", "Last dropout", "Last recovery"])
                for r in self._rows:
                    w.writerow([r["name"], f"{r['uptime']:.1f}", r["dead"],
                               r["first_drop"], r["last_drop"], r["last_rec"]])
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


# ── MAIN WIDGET ───────────────────────────────────────────────────────────────

class PulserMonitorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tz = TZ_PRAGUE if TZ_PRAGUE else timezone.utc
        now = datetime.now(tz)
        self._start_dt: datetime = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._end_dt: datetime = now
        self._images_root: Path = IMAGES_ROOT_OPTIONS["Lab"]

        # Per-camera ROI definitions
        self._rois_by_camera: dict = {cam: [] for cam in CAMERAS}
        # Per-camera scan results
        self._results_by_camera: dict = {}
        # Currently displayed results and ROIs (set when viewing a camera)
        self._results: list = []
        self._rois: list = []

        self._gen_box: list = [0]
        self._file_sig: "object | None" = None
        self._scan_sig: "object | None" = None
        self._scan_queue: list = []          # list of cam_key strings
        self._current_scan_cam_key: str = ""

        self._build_ui()

    def _build_ui(self):
        root_lay = QHBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── LEFT PANEL ────────────────────────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(295)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lw = QWidget()
        left_scroll.setWidget(lw)
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(4)

        # Cameras
        lv.addWidget(_group_label("Cameras"))
        self._cam_checks: dict = {}
        self._roi_rows: dict = {}
        grid_w = QWidget()
        grid = QHBoxLayout(grid_w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        col_a = QVBoxLayout()
        col_b = QVBoxLayout()
        for i, cam_key in enumerate(CAMERAS):
            chk = QCheckBox(cam_key)
            chk.setChecked(True)
            chk.setStyleSheet(_CHECKBOX_STYLE)
            chk.stateChanged.connect(
                lambda state, k=cam_key: self._set_roi_row_visible(k, state > 0))
            self._cam_checks[cam_key] = chk
            (col_a if i % 2 == 0 else col_b).addWidget(chk)
        grid.addLayout(col_a)
        grid.addLayout(col_b)
        grid.addStretch(1)
        lv.addWidget(grid_w)

        # Time window
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Time window"))
        btn_tw = QPushButton("Set time window…")
        btn_tw.setStyleSheet(_BTN)
        btn_tw.clicked.connect(self._open_tw)
        lv.addWidget(btn_tw)
        self._tw_lbl = QLabel(self._tw_text())
        self._tw_lbl.setStyleSheet("font-size:10px;color:#555;")
        self._tw_lbl.setWordWrap(True)
        lv.addWidget(self._tw_lbl)

        # ROIs — one edit button per camera
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("ROIs  (click to edit per camera)"))
        self._roi_count_lbls: dict = {}
        for cam_key in CAMERAS:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            btn = QPushButton(f"Edit {cam_key}…")
            btn.setStyleSheet(_BTN_SM)
            btn.setFixedWidth(110)
            btn.clicked.connect(lambda checked=False, k=cam_key: self._open_roi_editor(k))
            lbl = QLabel("0 ROIs")
            lbl.setStyleSheet("font-size:10px;color:#555;")
            self._roi_count_lbls[cam_key] = lbl
            row.addWidget(btn)
            row.addWidget(lbl)
            row.addStretch(1)
            self._roi_rows[cam_key] = row_w
            lv.addWidget(row_w)

        roi_json_row = QHBoxLayout()
        btn_load_all = QPushButton("Load configuration…")
        btn_load_all.setFixedWidth(130)
        btn_load_all.clicked.connect(self._load_rois_json)
        roi_json_row.addWidget(btn_load_all)
        roi_json_row.addStretch(1)
        lv.addLayout(roi_json_row)
        hint_json = QLabel("Saves/loads ROI positions per camera.")
        hint_json.setStyleSheet("font-size:9px;color:#888;")
        hint_json.setWordWrap(True)
        lv.addWidget(hint_json)

        # Detection
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Detection"))
        thr_row = QHBoxLayout()
        thr_row.addWidget(QLabel("Threshold:"))
        self._thr_sb = QDoubleSpinBox()
        self._thr_sb.setRange(0.01, 5.0)
        self._thr_sb.setSingleStep(0.05)
        self._thr_sb.setDecimals(2)
        self._thr_sb.setValue(0.5)
        self._thr_sb.setFixedWidth(75)
        thr_row.addWidget(self._thr_sb)
        thr_row.addStretch(1)
        lv.addLayout(thr_row)
        lv.addWidget(QLabel("Normalization:"))
        self._norm_group = QButtonGroup(self)
        for label, val in [("Median of ROIs  (recommended)", "median"),
                            ("Reference image", "reference"),
                            ("Absolute", "absolute")]:
            rb = QRadioButton(label)
            rb.setProperty("norm_val", val)
            self._norm_group.addButton(rb)
            lv.addWidget(rb)
        self._norm_group.buttons()[0].setChecked(True)
        bd_row = QHBoxLayout()
        bd_row.addWidget(QLabel("Bit depth fallback:"))
        self._bd_sb = QSpinBox()
        self._bd_sb.setRange(8, 16)
        self._bd_sb.setValue(12)
        self._bd_sb.setFixedWidth(55)
        bd_row.addWidget(self._bd_sb)
        bd_row.addWidget(QLabel("bit"))
        bd_row.addStretch(1)
        lv.addLayout(bd_row)

        # Scan buttons
        lv.addWidget(_hsep())
        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setStyleSheet(_BTN_SUCCESS_STYLE)
        self._btn_scan.clicked.connect(self._start_scan)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setStyleSheet(_BTN_DANGER_STYLE)
        self._btn_stop.clicked.connect(self.cancel_scan)
        self._btn_stop.setEnabled(False)
        lv.addWidget(self._btn_scan)
        lv.addWidget(self._btn_stop)
        self._prog = QProgressBar()
        self._prog.setVisible(False)
        self._prog.setTextVisible(True)
        lv.addWidget(self._prog)
        self._status_lbl = QLabel("Idle")
        self._status_lbl.setStyleSheet("font-size:10px;color:#555;")
        lv.addWidget(self._status_lbl)

        # Export
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Export"))
        self._btn_csv = QPushButton("Export full CSV…")
        self._btn_csv.setStyleSheet(_BTN)
        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_csv.setEnabled(False)
        lv.addWidget(self._btn_csv)

        # Log
        lv.addWidget(_hsep())
        lv.addWidget(_group_label("Log"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        self._log.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        lv.addWidget(self._log)
        lv.addStretch(1)

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 4, 0, 0)
        right_lay.setSpacing(2)

        cam_view_row = QHBoxLayout()
        cam_view_row.addWidget(QLabel("Viewing camera:"))
        self._result_cam_combo = _NoScrollComboBox()
        self._result_cam_combo.setEnabled(False)
        self._result_cam_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._result_cam_combo.currentTextChanged.connect(self._on_result_cam_changed)
        cam_view_row.addWidget(self._result_cam_combo, 1)
        right_lay.addLayout(cam_view_row)

        self._tabs = QTabWidget()
        self._tab_tl = _TimelineTab()
        self._tab_pv = _PreviewTab()
        self._tab_br = _BrightnessTab()
        self._tab_sm = _SummaryTab()
        self._tabs.addTab(self._tab_tl, "Timeline")
        self._tabs.addTab(self._tab_pv, "Image Preview")
        self._tabs.addTab(self._tab_br, "Brightness Plot")
        self._tabs.addTab(self._tab_sm, "Summary")
        self._tab_tl.sample_selected.connect(self._on_tl_click)
        right_lay.addWidget(self._tabs, 1)

        root_lay.addWidget(left_scroll)
        root_lay.addWidget(right_w, 1)

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _tw_text(self) -> str:
        return (f"{self._start_dt.strftime('%Y-%m-%d %Hh')} → "
                f"{self._end_dt.strftime('%Y-%m-%d %Hh')}")

    def _log_msg(self, msg: str):
        self._log.appendPlainText(msg)

    def _update_roi_lbl(self, cam_key: str):
        n = len(self._rois_by_camera.get(cam_key, []))
        lbl = self._roi_count_lbls.get(cam_key)
        if lbl:
            lbl.setText(f"{n} ROI{'s' if n != 1 else ''}")

    def _set_roi_row_visible(self, cam_key: str, visible: bool):
        row_w = self._roi_rows.get(cam_key)
        if row_w:
            row_w.setVisible(visible)

    def _norm_mode(self) -> str:
        for btn in self._norm_group.buttons():
            if btn.isChecked():
                return btn.property("norm_val")
        return "median"

    def _selected_cam_keys(self) -> list:
        return [k for k, chk in self._cam_checks.items() if chk.isChecked()]

    # ── SLOTS ─────────────────────────────────────────────────────────────────

    def _open_tw(self):
        dlg = _TimeWindowDialog(self._start_dt, self._end_dt, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._start_dt, self._end_dt = dlg.selected_range()
            self._tw_lbl.setText(self._tw_text())

    def _open_roi_editor(self, cam_key: str):
        current_rois = self._rois_by_camera.get(cam_key, [])
        dlg = ROIEditorDialog(
            current_rois, self,
            cam_key=cam_key,
            images_root=self._images_root,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._rois_by_camera[cam_key] = dlg.result_rois()
            self._update_roi_lbl(cam_key)

    def _load_rois_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            new_rois = [RoiDefinition.from_dict(r) for r in data.get("rois", [])]
            cam_key = data.get("cam_key", "")
            if cam_key in self._rois_by_camera:
                self._rois_by_camera[cam_key] = new_rois
                self._update_roi_lbl(cam_key)
                self._log_msg(
                    f"Loaded {len(new_rois)} ROIs for {cam_key} from {Path(path).name}")
            else:
                QMessageBox.warning(
                    self, "Load ROIs",
                    f"JSON has cam_key='{cam_key}' which is not one of the known cameras.\n"
                    f"Known cameras: {', '.join(CAMERAS)}")
        except Exception as exc:
            QMessageBox.warning(self, "Load ROIs", f"Failed:\n{exc}")

    def _on_tl_click(self, idx: int):
        self._tab_pv.select_sample(idx)
        self._tabs.setCurrentIndex(1)

    # ── SCAN ──────────────────────────────────────────────────────────────────

    def _start_scan(self):
        cam_keys = self._selected_cam_keys()
        if not cam_keys:
            QMessageBox.warning(self, "Scan", "Check at least one camera checkbox.")
            return
        missing = [k for k in cam_keys if not self._rois_by_camera.get(k)]
        if missing:
            ans = QMessageBox.question(
                self, "Scan — missing ROIs",
                f"The following cameras have no ROIs defined:\n  {', '.join(missing)}\n\n"
                f"They will be skipped. Continue with the rest?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
            cam_keys = [k for k in cam_keys if self._rois_by_camera.get(k)]
        if not cam_keys:
            QMessageBox.warning(self, "Scan", "No cameras with ROIs to scan.")
            return

        self._results.clear()
        self._results_by_camera.clear()
        self._scan_queue = list(cam_keys)
        self._gen_box[0] += 1
        self._btn_scan.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_csv.setEnabled(False)
        self._prog.setValue(0)
        self._prog.setVisible(True)
        self._log_msg(
            f"Scan started: {len(cam_keys)} camera(s): {', '.join(cam_keys)}, "
            f"{self._start_dt.strftime('%Y-%m-%d %H:%M')} → "
            f"{self._end_dt.strftime('%Y-%m-%d %H:%M')}"
        )
        self._scan_next_camera()

    def _scan_next_camera(self):
        cam_key = self._scan_queue.pop(0)
        self._current_scan_cam_key = cam_key
        my_gen = self._gen_box[0]
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        self._status_lbl.setText(f"[{n_done + 1}/{n_total}] {cam_key}: enumerating…")
        start_ns = int(self._start_dt.timestamp() * 1_000_000_000)
        end_ns = int(self._end_dt.timestamp() * 1_000_000_000)
        min_size = CAM_MIN_BYTES.get(cam_key, 0)
        folder_name = CAMERAS[cam_key]
        self._file_sig = _FileScanSignals(self)
        self._file_sig.files_ready.connect(
            lambda files, _g=my_gen: self._on_files_ready(files, _g))
        self._file_sig.error.connect(self._on_scan_error)
        worker = _FileScanWorker(
            self._file_sig, self._images_root, folder_name,
            start_ns, end_ns, min_size, self._gen_box, my_gen)
        QThreadPool.globalInstance().start(worker)

    def _on_files_ready(self, files: list, my_gen: int):
        if self._gen_box[0] != my_gen:
            return
        cam_key = self._current_scan_cam_key
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        if not files:
            min_kb = CAM_MIN_BYTES.get(cam_key, 0) // 1024
            self._log_msg(
                f"[{n_done + 1}/{n_total}] {cam_key}: no qualifying images "
                f"(min {min_kb} kB) found in time window")
            self._results_by_camera[cam_key] = []
            if self._scan_queue:
                self._scan_next_camera()
            else:
                self._finish_all_scans()
            return
        self._prog.setRange(0, len(files))
        self._status_lbl.setText(
            f"[{n_done + 1}/{n_total}] {cam_key}: scanning 0/{len(files)}…")
        self._log_msg(f"[{n_done + 1}/{n_total}] {cam_key}: {len(files)} files to scan")
        rois = list(self._rois_by_camera.get(cam_key, []))
        self._scan_sig = _ScanSignals(self)
        self._scan_sig.progress.connect(self._on_progress)
        self._scan_sig.sample.connect(self._on_sample)
        self._scan_sig.finished.connect(self._on_finished)
        self._scan_sig.error.connect(self._on_scan_error)
        self._scan_sig.log_msg.connect(self._log_msg)
        worker = _ScanWorker(
            self._scan_sig, files, rois,
            self._norm_mode(), self._thr_sb.value(),
            self._bd_sb.value(), self._gen_box, my_gen,
        )
        QThreadPool.globalInstance().start(worker)

    def _on_progress(self, done: int, total: int):
        self._prog.setValue(done)
        n_done = len(self._results_by_camera)
        n_total = n_done + 1 + len(self._scan_queue)
        self._status_lbl.setText(
            f"[{n_done + 1}/{n_total}] {self._current_scan_cam_key}: {done}/{total}…")

    def _on_sample(self, sp: SamplePoint):
        self._results.append(sp)

    def _on_finished(self):
        cam_key = self._current_scan_cam_key
        self._results_by_camera[cam_key] = list(self._results)
        self._log_msg(f"Camera {cam_key}: {len(self._results)} samples")
        self._results.clear()
        if self._scan_queue:
            self._scan_next_camera()
        else:
            self._finish_all_scans()

    def _finish_all_scans(self):
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        n_cameras = len(self._results_by_camera)
        total_samples = sum(len(v) for v in self._results_by_camera.values())
        self._status_lbl.setText(f"Done — {n_cameras} camera(s), {total_samples} samples")
        self._log_msg(f"All done: {n_cameras} cameras, {total_samples} total samples")
        self._result_cam_combo.blockSignals(True)
        self._result_cam_combo.clear()
        for cam_key in self._results_by_camera:
            self._result_cam_combo.addItem(cam_key)
        self._result_cam_combo.setEnabled(n_cameras > 1)
        self._result_cam_combo.blockSignals(False)
        if self._results_by_camera:
            self._btn_csv.setEnabled(True)
            first_cam = next(iter(self._results_by_camera))
            self._result_cam_combo.setCurrentText(first_cam)
            self._results = list(self._results_by_camera[first_cam])
            self._rois = list(self._rois_by_camera.get(first_cam, []))
            self._render_all_tabs()

    def _on_scan_error(self, msg: str):
        cam_key = self._current_scan_cam_key
        self._scan_queue.clear()
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        self._status_lbl.setText("Error — see log")
        self._log_msg(f"ERROR ({cam_key}): {msg}")

    def cancel_scan(self):
        self._gen_box[0] += 1
        self._scan_queue.clear()
        self._results.clear()
        self._btn_scan.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._prog.setVisible(False)
        self._status_lbl.setText("Cancelled")
        self._log_msg("Scan cancelled")

    def _on_result_cam_changed(self, cam_key: str):
        if cam_key and cam_key in self._results_by_camera:
            self._results = list(self._results_by_camera[cam_key])
            self._rois = list(self._rois_by_camera.get(cam_key, []))
            self._render_all_tabs()

    def _render_all_tabs(self):
        t = self._thr_sb.value()
        self._tab_tl.update_data(self._results, self._rois, t)
        self._tab_pv.set_data(self._results, self._rois)
        self._tab_br.update_data(self._results, self._rois, t)
        self._tab_sm.update_data(self._results, self._rois)

    # ── CSV EXPORT ────────────────────────────────────────────────────────────

    def _export_csv(self):
        if not self._results:
            return
        cam_key = self._result_cam_combo.currentText() or "pulser"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", f"pulser_{cam_key}.csv", "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                header = ["camera", "timestamp_ns", "datetime_prague"]
                for roi in self._rois:
                    header += [f"{roi.name}_brightness", f"{roi.name}_norm", f"{roi.name}_alive"]
                w.writerow(header)
                for sp in self._results:
                    dt = _ns_to_dt(sp.ts_ns)
                    row = [cam_key, sp.ts_ns, dt.strftime("%Y-%m-%d %H:%M:%S")]
                    for i in range(len(self._rois)):
                        mean_v = sp.roi_means[i] if i < len(sp.roi_means) else 0.0
                        norm_v = sp.roi_norms[i] if i < len(sp.roi_norms) else 0.0
                        alive_v = 1 if (i < len(sp.roi_alive) and sp.roi_alive[i]) else 0
                        row += [f"{mean_v:.6f}", f"{norm_v:.6f}", str(alive_v)]
                    w.writerow(row)
            QMessageBox.information(self, "Export", f"Saved:\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "Export", f"Failed:\n{exc}")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ELIBeamlines.PulserMonitor")
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
        QGroupBox   { font-weight: 600; border: 1px solid #ccc; border-radius: 4px;
                      margin-top: 6px; padding-top: 6px; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; }
        QScrollBar:vertical { width: 8px; background: #f0f0f0; border: none; }
        QScrollBar::handle:vertical { background: #bbb; border-radius: 4px; min-height: 20px; }
        QTabWidget::pane { border: 1px solid #ccc; }
        QTabBar::tab { padding: 5px 14px; }
        QTabBar::tab:selected { background: #fff; border-bottom: 2px solid #1565C0;
                                font-weight: 600; }
    """)

    win = QMainWindow()
    win.setWindowTitle("Pulser Monitor")
    win.setMinimumSize(1100, 700)

    widget = PulserMonitorWidget()
    win.setCentralWidget(widget)

    status_bar = QStatusBar()
    win.setStatusBar(status_bar)
    btn_stop = QPushButton("Stop Scan")
    btn_stop.setStyleSheet(
        f"QPushButton{{background:{DANGER};color:#fff;border:none;"
        f"border-radius:3px;padding:3px 8px;font-size:11px;}}"
    )
    btn_stop.clicked.connect(widget.cancel_scan)
    status_bar.addPermanentWidget(btn_stop)

    try:
        icon_path = Path(__file__).resolve().parent.parent / "Image Tools" / "icon.ico"
        if icon_path.exists():
            win.setWindowIcon(QIcon(str(icon_path)))
    except Exception:
        pass

    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
