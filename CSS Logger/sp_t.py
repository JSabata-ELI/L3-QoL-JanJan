"""
sp_t.py — SPIDER spectrometer spectral analysis

Archive workflow:
  1. Click "Load day" -> pick a date (or a multi-day range). The "Search data by"
     PV (SBW4 energy by default; add your own, e.g. Alpha output energy / GDD / TOD)
     is loaded into the top search graph.
  2. Drag on the search graph to select a time region (= one spectrum); repeat.
  3. Click "Analyze" -> averaged spectra appear in the bottom graph. Colour them by
     selection order or by GDD / TOD on a rainbow scale.
  4. Display -> Show -> "Every spectrum" switches the averaging off: every single
     shot measured inside each region is drawn as its own curve.

Live workflow:
  1. Switch to Live -> click "Start Live".
  2. The bottom graph shows the newest shot plus the average of the last N spectra.

Standalone:   python sp_t.py
Integration:  class SpectraWidget, method cancel_scan()
"""

from __future__ import annotations

import csv, json, os, re, ssl, sys, threading, urllib.parse, urllib.request, warnings
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

import numpy as np

try:
    from zoneinfo import ZoneInfo
    _PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    _PRAGUE = None

from PySide6.QtCore import (Qt, QObject, QTimer, Signal,
                            QRect, QPoint, QEvent)
from PySide6.QtGui import (QAction, QColor, QCursor, QIcon, QPalette,
                           QShortcut, QKeySequence, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QScrollArea, QSizePolicy, QButtonGroup,
    QFileDialog, QDialog, QDialogButtonBox, QFrame, QSplitter,
    QMessageBox, QMainWindow, QTabWidget, QComboBox,
    QProgressBar, QStyledItemDelegate, QAbstractItemView, QInputDialog,
    QToolButton, QMenu, QStyle, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QDoubleSpinBox,
    QGridLayout, QSlider, QStyleOptionSlider,
)

import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams['axes.facecolor']   = 'white'
matplotlib.rcParams['figure.facecolor'] = 'white'
import matplotlib.cm as _mpl_cm
import matplotlib.colors as _mpl_colors
import matplotlib.dates as mdates
from matplotlib.collections import LineCollection
from matplotlib.ticker import FixedLocator, FuncFormatter
from matplotlib.transforms import blended_transform_factory
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector


def _import_daypicker():
    """Load the shared day/time picker (sibling daypicker.py): one instance per
    process, registered before exec.

    daypicker.py owns HOW A DAY AND A TIME WINDOW ARE PICKED for every program in
    the suite. The master copy lives in Image Tools/; this folder keeps a verbatim
    copy because the builder only ever bundles .py files from the program's own
    folder (Dev Tools/b_t.py). testing/test_daypicker_sync.py fails the moment the
    two copies differ.

    Loaded by path instead of `import daypicker` on purpose: a plain import would
    make the builder's module-home check see the same module name in two program
    folders and refuse to build.
    """
    import importlib.util as _ilu
    mod = sys.modules.get("daypicker")
    if mod is not None:
        return mod
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daypicker.py")
    spec = _ilu.spec_from_file_location("daypicker", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["daypicker"] = mod     # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


daypicker = _import_daypicker()
PickSeg        = daypicker.PickSeg
seg_bounds_ns  = daypicker.seg_bounds_ns
DayTimePicker  = daypicker.DayTimePicker

# ── Constants ─────────────────────────────────────────────────────────────────
PV_ENERGY = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"
PV_SPEC_X = "L3-SBDP-SPIDER:SpecDomain_Int_X"
PV_SPEC_Y = "L3-SBDP-SPIDER:SpecDomain_Int_Y"
CPVA_URL  = "https://10.78.0.57:8443/api/1.0/cpva"

# Extra scalar PVs averaged per region and shown in the region details.
# (label, channel) — add more entries here in the future, the UI/export adapt.
ORDER_PVS = [
    ("GDD", "L3-SPFE-AOD03-002:Order2_RB"),
    ("TOD", "L3-SPFE-AOD03-002:Order3_RB"),
    ("FOD", "L3-SPFE-AOD03-002:Order4_RB"),
]

PV_ALPHA_ENERGY = "HAPLS-ENER_IN_GPL_LT2_DIAG2:Energy"

# Scalar PVs the user can plot in the top "search" graph to pick time regions.
# This list is user-editable in the UI (add / remove / presets) and persisted to JSON.
DEFAULT_SEARCH_PVS = [
    ("SBW4 energy [J]",  PV_ENERGY),
    ("Alpha energy [J]", PV_ALPHA_ENERGY),
    ("GDD (Order 2)",    "L3-SPFE-AOD03-002:Order2_RB"),
    ("TOD (Order 3)",    "L3-SPFE-AOD03-002:Order3_RB"),
]


def _search_pv_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "search_pvs.json")

def _layout_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "layout.json")

def _preset_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "search_presets.json")

def _spec_pvs_config_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ELI_Spectra")
    return os.path.join(base, "spec_pvs.json")

def _strip_xy_suffix(ch: str) -> str:
    """Return the base PV name by stripping a trailing _X or _Y suffix."""
    for suffix in ("_X", "_Y"):
        if ch.endswith(suffix):
            return ch[:-len(suffix)]
    return ch

def _load_search_presets() -> list:
    try:
        with open(_preset_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [p for p in data
                    if isinstance(p, dict) and p.get("name") and isinstance(p.get("pvs"), list)]
    except Exception:
        pass
    return []

def _save_search_presets(presets: list):
    try:
        p = _preset_config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=2)
    except Exception:
        pass

SIDEBAR_W       = 340          # fixed width of the left control panel
LIVE_INTERVAL_S = 3            # poll period in live mode
LIVE_BUF_MAX    = 2000         # max spectra kept in the rolling buffer
DEFAULT_LIVE_N  = 100          # default "average last N" value
LIVE_HISTORY_S  = 600          # on Start Live, preload this many seconds of recent shots
MAX_INDIVIDUAL_LINES = 400     # cap when overlaying a region's individual spectra

# "Every spectrum" display: draw each measured shot instead of one averaged curve.
# A whole day is tens of thousands of shots, so the drawing is capped and evenly
# decimated - and the legend says how many of how many are actually on screen,
# because a silently thinned graph reads as "this is all there was".
SINGLE_METHOD    = "single"     # _METHODS value for "Every spectrum"
MAX_SINGLE_LINES = 3000         # cap when every spectrum is the display itself

# ── Colour-bar slot on the spectra graph ──────────────────────────────────────
# The GDD/TOD colour bar lives in ONE permanent axes that is only shown or hidden.
# It used to be created with fig.colorbar(sm, ax=ax), which takes a slice of the
# main axes' CURRENT width and never gives it back on Colorbar.remove(): measured,
# 30 clicks on "Colour by" shrank the plot from 93 % of the figure to 0 % and left
# the right-hand side blank. Room for the bar now comes from the layout engine's
# rect, and both values below are absolute — a redraw can never accumulate.
_CBAR_BOX  = (0.90, 0.13, 0.020, 0.78)   # x, y, w, h of the bar itself
_CBAR_RECT = (0.0, 0.0, 0.88, 1.0)       # area left to the plot while the bar shows
_FULL_RECT = (0.0, 0.0, 1.0, 1.0)        # the whole figure, bar hidden

# Because that bar is positioned by hand it has no gridspec cell, and tight_layout
# warns about it on every single draw. The placement is deliberate and the main axes
# is still laid out correctly inside the rect above (measured), so the warning is
# pure noise on stderr — silence just this one message, nothing else.
warnings.filterwarnings(
    "ignore",
    message="This figure includes Axes that are not compatible with tight_layout",
    category=UserWarning,
)

_REGION_COLORS = [
    "#C62828", "#2E7D32", "#EF6C00", "#6A1B9A",
    "#00838F", "#4E342E", "#AD1457", "#37474F",
]

# Search-graph traces have their own palette, so a PV curve is never drawn in the
# colour that already stands for a selected spectrum.
_TRACE_COLORS = [
    "#1565C0", "#00897B", "#7CB342", "#F9A825",
    "#5E35B1", "#D81B60", "#00ACC1", "#6D4C41",
]

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

# ── The shot bar under the search graph ───────────────────────────────────────
# "Every spectrum — pick one" is a QSlider and not a QScrollBar: a scroll bar's
# handle is a block as wide as one page, and this bar has to say WHERE ON THE
# GRAPH one single shot is — that is a point, so it needs a handle that is a
# point. (It used to be a horizontal QScrollBar in the side panel.)
#
# Every colour is set here. _APP_STYLESHEET styles only the VERTICAL scroll bars,
# and an unstyled QSlider takes the inherited dark theme and comes out as a grey
# handle on a grey groove on a grey panel.
_SLIDER_HANDLE_W = 12          # kept in sync with the handle width below

_TIMEBAR_STYLE = f"""
QSlider:horizontal {{ height: 20px; }}
QSlider::groove:horizontal {{
    background: #e6e6e6; border: 1px solid #b4b4b4; border-radius: 5px;
    height: 12px; margin: 0;
}}
QSlider::handle:horizontal {{
    background: #5b6b80; border: 1px solid #3f4c5c; border-radius: 3px;
    width: {_SLIDER_HANDLE_W}px; margin: -4px 0;
}}
QSlider::handle:horizontal:hover   {{ background: #1565C0; border-color: #0D47A1; }}
QSlider::handle:horizontal:pressed {{ background: #0D47A1; border-color: #08306b; }}
QSlider::groove:horizontal:disabled {{ background: #efefef; border-color: #d0d0d0; }}
QSlider::handle:horizontal:disabled {{ background: #c4c4c4; border-color: #b0b0b0; }}
"""

# Finer than any pixel width the bar can have, so rounding in the
# value <-> pixel conversion never costs a pixel of alignment.
_SHOT_BAR_MAX = 100_000


def _slider_metrics(sl: QSlider) -> "tuple[int, int, int]":
    """(groove_x, travel, handle_width) for a horizontal slider, from the style.

    These are the three numbers Qt itself uses to place a handle, so asking for
    them is the only way a value and a pixel can be converted the same way in
    both directions. `travel` is how far the handle's left edge can move, so the
    handle's CENTRE only ever covers groove_x + handle_width/2 … + travel.

    That inset is exactly what the bar has to overhang the plot box by on each
    side for "same X on the graph = same X on the bar" to hold — and it must be
    measured, never assumed: it changes with the platform style, and a wrong
    guess drifts the handle a whole handle width across the bar.
    """
    w  = max(1, sl.width())
    hwid = _SLIDER_HANDLE_W
    gx, gw = 0, w
    try:
        opt = QStyleOptionSlider()
        sl.initStyleOption(opt)
        st = sl.style()
        hr = st.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                               QStyle.SubControl.SC_SliderHandle, sl)
        gr = st.subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                               QStyle.SubControl.SC_SliderGroove, sl)
        if hr.width() > 0:
            hwid = hr.width()
        if gr.width() > 0:
            gx, gw = gr.x(), gr.width()
    except Exception:
        pass
    return gx, max(1, gw - hwid), hwid


class _ShotBar(QSlider):
    """The bar under the search graph. A horizontal slider, driven differently in
    three ways.

    1. A click anywhere jumps straight to that place. The bar is pinned to the
       graph's time axis, so a click on it is a click on a moment in time —
       Qt's default (page towards it) would need a dozen clicks to cross a day.
    2. The wheel and the arrow keys move ONE MEASURED SHOT, not one slider unit.
       The value range is far finer than the pixel width, so that the mapping
       from time to pixels never rounds; that also makes a single unit a no-op,
       which is why stepping is handed back to the tab.
    3. Nothing else may be put in its layout row — the row's left and right
       margins are what pins the bar to the plot box above it.
    """

    def __init__(self, step_cb, page_cb):
        super().__init__(Qt.Orientation.Horizontal)
        self._step_cb = step_cb          # ±1 shot
        self._page_cb = page_cb          # ±a screenful of shots
        self._dragging = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ── absolute positioning ──────────────────────────────────────────────
    # Both directions go through Qt's own value<->position arithmetic, so the
    # pixel this bar reports is the pixel Qt actually draws the handle on. Doing
    # the division by hand instead was off by more than a pixel at some widths.
    def value_at_pixel(self, x: float) -> int:
        """The value whose HANDLE CENTRE sits at logical pixel x."""
        gx, travel, hwid = _slider_metrics(self)
        return int(QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            int(round(x - gx - hwid / 2.0)), travel,
            self.invertedAppearance()))

    def pixel_at_value(self, v: int) -> float:
        """Where the handle's centre lands for value v, in logical pixels."""
        gx, travel, hwid = _slider_metrics(self)
        pos = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), int(v), travel,
            self.invertedAppearance())
        return gx + pos + hwid / 2.0

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._dragging = True
            self.setSliderDown(True)
            self.setValue(self.value_at_pixel(ev.position().x()))
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._dragging:
            self.setValue(self.value_at_pixel(ev.position().x()))
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._dragging and ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setSliderDown(False)
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    # ── stepping by shots ─────────────────────────────────────────────────
    def wheelEvent(self, ev):
        dy = ev.angleDelta().y()
        if dy:
            self._step_cb(1 if dy > 0 else -1)
            ev.accept()
            return
        super().wheelEvent(ev)

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self._step_cb(-1); ev.accept(); return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self._step_cb(+1); ev.accept(); return
        if k == Qt.Key.Key_PageDown:
            self._page_cb(-1); ev.accept(); return
        if k == Qt.Key.Key_PageUp:
            self._page_cb(+1); ev.accept(); return
        super().keyPressEvent(ev)

# ── Step cards at the top of the sidebar ──────────────────────────────────────
# The top of the panel reads downwards as "what am I doing": pick a day, pick the
# signal you search on, then say which spectrum you measure. The search signal and
# the PV list are ONE card, the same way the spectrum card carries both its value
# and its "Change…" button: the list is how you choose the signal, so keeping them
# apart made every pick a hop between two cards.
# (accent, pale fill, border) per step.
_STEP_COLORS = {
    1: ("#546E7A", "#eceff1", "#cfd8dc"),   # grey  — day
    2: ("#2E7D32", "#e8f5e9", "#c5e1c8"),   # green — search signal + PV list
    3: ("#1565C0", "#e8f0fe", "#c3d7f5"),   # blue  — measured spectrum
}

# Columns of the PV table inside step 2. Named, not hand-counted: the tick box was
# added in front of Label, and every literal 0/1 left behind renamed the wrong cell.
_PV_COL_SHOW, _PV_COL_LABEL, _PV_COL_CHAN = 0, 1, 2
# The tick box is painted at a fixed 14 px by _TickBoxDelegate, so the column only
# has to hold that plus a little air — every spare pixel goes to the channel name.
_PV_SHOW_W = 26                                  # tick-box column, px
_PV_TABLE_FONT_PX = 10                           # PV table text, px
# Default share of the height for [search graph, spectra graph]. Until there is a
# spectrum to look at the search graph takes the window; once curves appear the
# spectra graph is twice the search graph. A dragged divider overrides both.
_SPLIT_NO_CURVES = (600, 400)
_SPLIT_CURVES    = (330, 660)
_PV_LABEL_MAX_W = 92                             # Label column, px, at most
_PV_CHAN_MIN_W  = 130                            # Channel column never thinner


class _TickBoxDelegate(QStyledItemDelegate):
    """The ✓ column of the PV table, drawn by hand.

    Qt paints the standard tick with the row's text colour, so on the highlighted
    row — the PV currently searched by — the mark vanished into the blue band and
    there was no way to tell a ticked PV from an unticked one. The box is therefore
    always painted as a white square with a dark green tick, on top of whatever
    background the row has, and the whole cell is a click target instead of only
    the indicator."""

    _BOX = 14                       # side of the box, px

    @staticmethod
    def _is_checked(index) -> bool:
        """Qt hands the tick state back as a Qt.CheckState here but as a plain int
        elsewhere, and Qt.CheckState is not an int subclass — int() on it raises."""
        v = index.data(Qt.ItemDataRole.CheckStateRole)
        if isinstance(v, Qt.CheckState):
            return v == Qt.CheckState.Checked
        try:
            return int(v) == int(Qt.CheckState.Checked.value)
        except (TypeError, ValueError):
            return False

    def paint(self, painter, option, index):
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            bg = index.data(Qt.ItemDataRole.BackgroundRole)
            painter.fillRect(option.rect, QColor(bg) if bg else QColor("#ffffff"))
        box = QRect(0, 0, self._BOX, self._BOX)
        box.moveCenter(option.rect.center())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#455A64"), 1.4))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(box, 3, 3)
        if self._is_checked(index):
            pen = QPen(QColor("#1B5E20"), 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            x, y, s = box.left(), box.top(), self._BOX
            painter.drawLine(x + int(s * 0.22), y + int(s * 0.52),
                             x + int(s * 0.44), y + int(s * 0.76))
            painter.drawLine(x + int(s * 0.44), y + int(s * 0.76),
                             x + int(s * 0.80), y + int(s * 0.24))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and (index.flags() & Qt.ItemFlag.ItemIsUserCheckable)):
            on = self._is_checked(index)
            model.setData(index,
                          Qt.CheckState.Unchecked if on else Qt.CheckState.Checked,
                          Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


def _fit_button(btn, extra: int = 22):
    """Give a button a minimum width measured from its own label.

    Never setFixedWidth() on a button that holds text: _APP_STYLESHEET adds
    'padding: 5px 8px' plus a 1 px border, so a hand-picked pixel count silently
    clips the label — and clips it harder at 125/150 % display scaling, where the
    font grows but the number does not."""
    btn.setMinimumWidth(btn.fontMetrics().horizontalAdvance(btn.text()) + extra)


def _step_card(number: int, title: str) -> tuple:
    """One numbered step card. Returns (frame, body_layout) so the caller fills it.

    Dark text on a pale fill, never the other way round — the number badge is the
    only reversed element and it carries its own dark background."""
    accent, fill, border = _STEP_COLORS.get(number, _STEP_COLORS[1])
    card = QFrame()
    card.setObjectName(f"stepCard{number}")
    # Scoped to the object name so the fill and border cannot cascade onto the
    # table, spin boxes or child frames living inside the card.
    card.setStyleSheet(
        f"QFrame#stepCard{number} {{ background: {fill}; border: 1px solid {border};"
        f" border-left: 4px solid {accent}; border-radius: 4px; }}"
        f"QFrame#stepCard{number} > QLabel {{ background: transparent; border: none; }}"
    )
    outer = QVBoxLayout(card)
    outer.setContentsMargins(7, 5, 7, 6)
    outer.setSpacing(3)

    head = QHBoxLayout()
    head.setSpacing(5)
    badge = QLabel(str(number))
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedSize(15, 15)
    badge.setStyleSheet(
        f"background: {accent}; color: white; border: none; border-radius: 7px;"
        " font-size: 9px; font-weight: 700;"
    )
    head.addWidget(badge)
    lbl = QLabel(title)
    lbl.setStyleSheet(
        f"color: {accent}; font-size: 10px; font-weight: 700; border: none;"
        " background: transparent;"
    )
    head.addWidget(lbl, stretch=1)
    outer.addLayout(head)

    body = QVBoxLayout()
    body.setSpacing(4)
    outer.addLayout(body)
    return card, body

# ── The panel's bottom settings block ─────────────────────────────────────────
# Everything that only changes HOW the result is drawn — the graph options, the
# comparison curve and the horizontal range — lives in one coloured, foldable
# group at the very bottom, under the buttons. Same shape as the Image Slider's
# sections in Image Tools, so the two panels read alike: a solid accent bar with
# white text over a body washed in a very pale tint of the same accent.
#
# Every colour is written here. An unstyled group box inherits the dark theme and
# comes out grey-on-grey.

def _shade(hex_color: str, factor: float) -> str:
    """hex_color moved toward black (factor < 1) or toward white (factor > 1)."""
    h = hex_color.lstrip("#")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    if factor <= 1.0:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1.0)) for c in (r, g, b))
    r, g, b = (max(0, min(255, c)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"

# Purple, the same accent the Image Slider gives its "Image / Display" section.
_SET_ACCENT = "#7a4fc0"

def _sub_label(text: str) -> QLabel:
    """Small caption for one block inside the settings group."""
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "font-size: 10px; font-weight: 700; letter-spacing: 1px; padding-top: 2px;"
        f" color: {_shade(_SET_ACCENT, 0.75)}; background: transparent; border: none;")
    return lbl

class _SettingsGroup(QWidget):
    """Coloured header + pale body; the header folds the body away."""

    def __init__(self, title: str, accent: str = _SET_ACCENT,
                 expanded: bool = True, parent=None):
        super().__init__(parent)
        self._title = title
        self._expanded = bool(expanded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QToolButton()
        self._header.setCheckable(True)
        self._header.setChecked(self._expanded)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self._header.setStyleSheet(
            "QToolButton { text-align: left; border: none; padding: 6px 9px;"
            " margin-top: 6px; font-weight: 700; font-size: 11px;"
            " letter-spacing: 1px; border-top-left-radius: 4px;"
            " border-top-right-radius: 4px; color: #ffffff;"
            f" background: {accent}; }}"
            f"QToolButton:hover {{ background: {_shade(accent, 0.85)}; }}"
        )
        self._header.setToolTip("Click to fold this block away or open it again.")
        self._header.clicked.connect(self._on_clicked)
        outer.addWidget(self._header)

        self.body = QWidget()
        self.body.setObjectName("setBody")
        # A near-white tint (1.93): anything stronger and the black control text
        # stops being comfortably legible. Scoped to the object name so the
        # controls inside keep their own white / transparent backgrounds, and the
        # label rule fixes the text colour that would otherwise come from the
        # inherited dark theme.
        self.body.setStyleSheet(
            "#setBody { border: 1px solid " + _shade(accent, 1.55) + ";"
            " border-left: 3px solid " + _shade(accent, 1.35) + ";"
            " background: " + _shade(accent, 1.93) + ";"
            " border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }"
            "#setBody QLabel { background: transparent; border: none; color: #111; }"
        )
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 5, 7, 7)
        self.body_layout.setSpacing(4)
        outer.addWidget(self.body)

        self.body.setVisible(self._expanded)
        self._paint_header()

    def _paint_header(self):
        arrow = "▾" if self._expanded else "▸"
        # Escape '&' — QToolButton would eat it as a shortcut marker.
        self._header.setText(f"{arrow}  {self._title.upper().replace('&', '&&')}")

    def _on_clicked(self):
        self._expanded = self._header.isChecked()
        self.body.setVisible(self._expanded)
        self._paint_header()

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
# Archive / Live: the chosen one is a filled blue button with white text, the
# other dark ink on light grey. Written out because the app-wide stylesheet
# repaints every button and the style's own "checked" look never shows through.
_BTN_MODE = (
    "QPushButton { background:#f0f0f0; color:#111; font-weight:600; "
    "padding:6px 10px; border:1px solid #b4b4b4; border-radius:4px; }"
    "QPushButton:hover:!checked { background:#dde8ff; }"
    "QPushButton:checked { background:#1565C0; color:#ffffff; font-weight:700; "
    "border:1px solid #0D47A1; }"
)

_TB_STYLE = (
    "QToolBar { background: white; border: none; } "
    "QToolButton { background: transparent; color: black; } "
    "QToolButton:hover { background: #e8f0fe; } "
    "QToolButton:checked { background: #bbd4f8; border-radius: 3px; } "
    "QToolButton:checked:hover { background: #a8c6f5; border-radius: 3px; }"
)
_TB_HINTS = {
    "Home":     "Reset view",
    "Back":     "Previous view",
    "Forward":  "Next view",
    "Pan":      "Pan (drag left btn) / zoom (drag right btn)",
    "Zoom":     "Zoom to selection",
    "Subplots": "Adjust margins",
    "Save":     "Save as image",
}


class _CustomToolbar(NavigationToolbar2QT):
    """Custom toolbar. Permanently removes 'Export values' from the Subplots
    settings dialog and emits subplot_params_changed when that dialog closes."""
    subplot_params_changed = Signal()

    def configure_subplots(self):
        super().configure_subplots()
        QTimer.singleShot(0, self._patch_subplots_dialog)

    def _patch_subplots_dialog(self):
        dlg = getattr(self, "_subplot_dialog", None)
        if dlg is None:
            for w in QApplication.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible():
                    dlg = w
                    break
        if dlg is None:
            return
        for btn in dlg.findChildren(QPushButton):
            if btn.text().strip() == "Export values":
                btn.setParent(None)
                break
        dlg.adjustSize()
        try:
            dlg.finished.connect(lambda _: self.subplot_params_changed.emit())
        except Exception:
            pass


# ── SSL + CPVA helpers ────────────────────────────────────────────────────────
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _cpva_fetch(channel: str, start_ns: int, end_ns: int) -> list:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": int(start_ns),
        "end":   int(end_ns),
    })
    req = urllib.request.urlopen(
        f"{CPVA_URL}/samples?{params}", context=_ssl_ctx(), timeout=30
    )
    return json.loads(req.read())


# The archiver answers with HTTP 500 when a single response would carry too many
# samples: a fast PV (tens of samples per second) over a whole day is far past
# that limit, and the old code turned the failure into an empty graph that looked
# exactly like "this PV was not recorded". Every read is therefore split into
# 1-hour requests, run a few at a time, and a piece that still fails is halved
# again down to one minute. Whatever is left unread is remembered per channel so
# the panel can say so instead of showing nothing.
_CHUNK_NS      = int(3600 * 1e9)      # one hour
_CHUNK_MIN_NS  = int(60 * 1e9)        # do not split below one minute
_FETCH_WORKERS = 8
# Above this many one-hour requests the load is worth a word of warning first
# (hours × PVs). A normal day of a dozen PVs is well under it.
_FETCH_WARN_REQUESTS = 400

# The shortest drag on the search graph that counts as selecting a spectrum
# rather than a click. The axis is in seconds of archive time.
_MIN_SPAN_S = 0.25

# Days sit flush against one another on the compressed axis, so a drag meant for
# one day almost always clips its neighbour by a few pixels — and zoomed out over
# a fortnight a few pixels are minutes of archive time. Such a clipping used to
# become its own spectrum, which the operator then had to delete by hand. A piece
# of a drag now has to earn its place: either it holds a fair share of the drag,
# or it covers practically the whole of its own day.
#
# The share is measured against the LONGEST piece of the drag, never against the
# drag total: with a total-based rule a deliberate drag over 20 equal days would
# give every piece 5 % and throw them all away.
_EDGE_KEEP_FRAC = 0.20   # at least a fifth of the drag's longest day
_FULL_DAY_FRAC  = 0.90   # ...or practically all of this day's own loaded window

_last_fetch_error: dict = {}          # channel -> message from its last failure


def _cpva_fetch_split(channel: str, start_ns: int, end_ns: int) -> list:
    """One request, halved and retried while it keeps failing."""
    try:
        return _cpva_fetch(channel, start_ns, end_ns)
    except Exception:
        if end_ns - start_ns <= _CHUNK_MIN_NS:
            raise
    mid = (start_ns + end_ns) // 2
    return (_cpva_fetch_split(channel, start_ns, mid)
            + _cpva_fetch_split(channel, mid, end_ns))


def _cpva_fetch_chunked(channel: str, start_ns: int, end_ns: int) -> list:
    """Same samples as _cpva_fetch, fetched in 1-hour pieces."""
    _last_fetch_error.pop(channel, None)
    bounds = []
    cs = start_ns
    while cs < end_ns:
        bounds.append((cs, min(cs + _CHUNK_NS, end_ns)))
        cs = bounds[-1][1]
    if not bounds:
        return []
    if len(bounds) == 1:
        return _cpva_fetch(channel, start_ns, end_ns)

    parts, errs = {}, []
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(bounds))) as ex:
        futs = {ex.submit(_cpva_fetch_split, channel, a, b): i
                for i, (a, b) in enumerate(bounds)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                parts[i] = fut.result()
            except Exception as exc:
                parts[i] = []
                errs.append(f"{_fmt_hms(bounds[i][0])} ({exc})")
    if errs:
        _last_fetch_error[channel] = (
            f"{len(errs)} of {len(bounds)} hour(s) could not be read — "
            f"first: {errs[0]}")
    # Every request also returns the last sample before its own start, so the
    # same sample arrives in two neighbouring pieces — keep one per timestamp.
    seen, merged = set(), []
    for i in sorted(parts):
        for s in parts[i]:
            t = s.get("time")
            if t in seen:
                continue
            seen.add(t)
            merged.append(s)
    return merged


def _fetch_waveforms(channel: str, start_ns: int, end_ns: int) -> list[tuple[int, np.ndarray]]:
    try:
        samples = _cpva_fetch_chunked(channel, start_ns, end_ns)
    except Exception as exc:
        _last_fetch_error[channel] = str(exc)
        return []
    result = []
    for s in samples:
        t = s.get("time")
        v = s.get("value")
        if isinstance(v, list) and v:
            try:
                arr = np.array(v, dtype=float)
                if t is not None:
                    result.append((int(t), arr))
            except (ValueError, TypeError):
                pass
    return result


_cpva_channel_cache: list = []   # module-level cache shared between dialogs

def _cpva_load_all_channels() -> list:
    """Fetch every archived channel name from CPVA (no filter — filter locally).
    Uses the /channels-by-pattern endpoint (pattern=** → all ~9600 channels).
    The response is a JSON list of names, or of dicts carrying the name under
    channelName / name / channel. Returns a sorted list of strings; [] on failure."""
    url = f"{CPVA_URL}/channels-by-pattern?" + urllib.parse.urlencode({"pattern": "**"})
    try:
        req = urllib.request.urlopen(url, context=_ssl_ctx(), timeout=20)
        data = json.loads(req.read())
        names = []
        if isinstance(data, list):
            for x in data:
                if isinstance(x, str):
                    names.append(x)
                elif isinstance(x, dict):
                    n = x.get("channelName") or x.get("name") or x.get("channel")
                    if n:
                        names.append(str(n))
        return sorted(set(names))
    except Exception:
        pass
    return []


# ── PV-name search (same behaviour as the Image Slider PV picker) ─────────────
# Camera channels (C03-013-PFM1NF:Exposure, …) are a large slice of the archiver
# list, so they are ranked last — otherwise a query like "pcm" returns nothing
# but cameras before the PV the user actually wants shows up.
_CAM_CHANNEL_RE = re.compile(r"^C\d{2}-\d{2,3}-")


def _split_query(text: str) -> list:
    """Query text → lowercase tokens. Spaces, commas and '*' all separate, so
    "l3 sbw4", "l3,sbw4" and "*l3**sbw4*" are the same query: every token must
    appear somewhere in the name (implicit wildcards between them)."""
    return [t for t in re.split(r"[\s,;*]+", (text or "").strip().lower()) if t]


def _tokens_in_order(hay: str, tokens: list) -> bool:
    """True when every token occurs in `hay` in the order typed."""
    pos = 0
    for t in tokens:
        i = hay.find(t, pos)
        if i < 0:
            return False
        pos = i + len(t)
    return True


def _rank_pv_match(name: str, tokens: list) -> "int | None":
    """Sort weight of one channel against the tokens (lower = better), or None
    when it doesn't match. Multi-token queries are AND-matched anywhere in the
    name; tokens found in the typed order rank above scrambled ones."""
    if not tokens:
        return None
    k = name.lower()
    field = k.rsplit(":", 1)[-1]
    worst = 0
    total = 0
    for t in tokens:
        if t == k:
            s = 0
        elif field == t:
            s = 1
        elif field.startswith(t) or k.startswith(t):
            s = 2
        elif t in field:
            s = 3
        elif t in k:
            s = 4
        else:
            return None          # AND semantics: one missing token = no hit
        worst = max(worst, s)
        total += s
    # The weakest token decides the tier; the sum only breaks ties.
    score = worst * 10 + min(total, 9)
    if len(tokens) > 1 and not _tokens_in_order(k, tokens):
        score += 5
    if _CAM_CHANNEL_RE.match(name):
        score += 100
    return score


def _pv_search(channels, text: str, exclude=None) -> list:
    """Channel names matching `text`, best first. Empty query → no results."""
    tokens = _split_query(text)
    if not tokens:
        return []
    skip = exclude or ()
    scored = []
    for i, ch in enumerate(channels):
        if ch in skip:
            continue
        s = _rank_pv_match(ch, tokens)
        if s is not None:
            scored.append((s, i, ch))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [ch for _s, _i, ch in scored]


def _fetch_scalars(channel: str, start_ns: int, end_ns: int) -> list[tuple[int, float]]:
    try:
        samples = _cpva_fetch_chunked(channel, start_ns, end_ns)
    except Exception as exc:
        _last_fetch_error[channel] = str(exc)
        return []
    result = []
    for s in samples:
        t = s.get("time")
        v = s.get("value")
        if isinstance(v, list):
            v = v[0] if v else None
        if t is not None and v is not None:
            try:
                result.append((int(t), float(v)))
            except (TypeError, ValueError):
                pass
    return result


def _trimmed_mean(stack: np.ndarray, frac: float = 0.1) -> np.ndarray:
    """Mean after dropping the lowest and highest `frac` of values per column."""
    n = stack.shape[0]
    k = int(n * frac)
    if n - 2 * k < 1:
        return stack.mean(axis=0)
    s = np.sort(stack, axis=0)
    return s[k:n - k].mean(axis=0)


def _sigma_clipped_mean(stack: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    """Mean per column after masking points beyond `sigma` standard deviations."""
    mean = stack.mean(axis=0)
    std  = stack.std(axis=0)
    lo, hi = mean - sigma * std, mean + sigma * std
    masked = np.where((stack >= lo) & (stack <= hi), stack, np.nan)
    with np.errstate(invalid="ignore"):
        clipped = np.nanmean(masked, axis=0)
    return np.where(np.isnan(clipped), mean, clipped)


def _trapz(y, x) -> float:
    """Trapezoidal integral, compatible with both NumPy 1.x (trapz) and 2.x (trapezoid)."""
    fn = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    return float(fn(y, x))


# The archiver does not always store the whole X axis. L3-SBDP-SPIDER:
# TimeDomain_Int_X holds 2048 points while its _Y holds 4096, and every drawing
# path used to compare the two lengths, find them unequal and quietly count
# array positions instead — four analysed days came out in "samples", with a
# 33 fs pulse reported as "FWHM 18.2" and its peak as "2045 nm".
# A short axis whose step is constant is not a broken axis, it is a truncated
# one: it can be rebuilt exactly from its own first value and step (the SPIDER
# axis starts at -3749.087 fs and steps 1.8306 fs, so point 2048 is t = 0 —
# confirmed against TimeDomain_FL_Y, whose transform-limited pulse peaks there).
# An axis that is NOT uniform is never extended: extrapolating a grating
# spectrometer's λ axis would invent numbers.
_X_UNIFORM_TOL = 1e-3      # spread of the step, relative, still counted as uniform


def _fit_x_axis(x, n: int) -> "np.ndarray | None":
    """The measured X axis to draw n intensity points against, or None if the
    stored axis cannot honestly cover them (caller then uses sample numbers)."""
    if x is None or n < 1:
        return None
    x = np.asarray(x, dtype=float)
    if x.size == n:
        return x
    if x.size < 2 or not bool(np.all(np.isfinite(x))):
        return None
    d = np.diff(x)
    step = (float(x[-1]) - float(x[0])) / (x.size - 1)
    if step == 0.0:
        return None
    if float(np.max(np.abs(d - step))) > abs(step) * _X_UNIFORM_TOL:
        return None
    return float(x[0]) + step * np.arange(n, dtype=float)


# Quantity name, axis unit and short symbol that go with a unit string. The panel
# was written for a grating spectrometer and said "Wavelength [nm]" / "Peak λ"
# everywhere; the same tab is used on SPIDER TimeDomain_Int, whose axis is
# femtoseconds, and a femtosecond axis labelled nm is worse than no axis at all.
_X_UNIT_KINDS = {
    "nm":  ("Wavelength", "λ"),
    "µm":  ("Wavelength", "λ"),
    "um":  ("Wavelength", "λ"),
    "thz": ("Frequency",  "f"),
    "fs":  ("Time",       "t"),
    "ps":  ("Time",       "t"),
    "ns":  ("Time",       "t"),
    "rad": ("Phase",      "φ"),
}


def _x_unit_kind(unit: str) -> tuple[str, str]:
    """(quantity name, symbol) for a unit; a unit we do not know stays generic."""
    return _X_UNIT_KINDS.get((unit or "").strip().lower(), ("X", "x"))


def _guess_x_unit(*pv_names: str) -> str:
    """Default unit of the X AXIS of a spectrum channel, from its name. Only the
    SPIDER time domain differs from the nanometres the tab was built for — note
    this is the axis, so TimeDomain_Phase is also fs (its phase is the Y value)
    while SpecDomain_Phase is nm. Editable in the panel when a name lies."""
    blob = " ".join(n or "" for n in pv_names).lower()
    if "timedomain" in blob or "time_domain" in blob:
        return "fs"
    if "thz" in blob:
        return "THz"
    return "nm"


def _fwhm(x: np.ndarray, y: np.ndarray) -> "float | None":
    """Full width at half maximum (above baseline), with linear edge interpolation."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if y.size < 2 or x.size != y.size:
        return None
    base, peak = float(np.min(y)), float(np.max(y))
    if peak <= base:
        return None
    half = base + (peak - base) / 2.0
    idx = np.where(y >= half)[0]
    if idx.size == 0:
        return None
    iL, iR = int(idx[0]), int(idx[-1])

    def _edge(i_in: int, i_out: int) -> float:
        if i_out < 0 or i_out >= len(x) or y[i_in] == y[i_out]:
            return float(x[i_in])
        t = (half - y[i_out]) / (y[i_in] - y[i_out])
        return float(x[i_out] + t * (x[i_in] - x[i_out]))

    return abs(_edge(iR, iR + 1) - _edge(iL, iL - 1))


def _spectral_metrics(x: np.ndarray, y: np.ndarray) -> dict:
    """Peak / width metrics for a single (already range-masked) spectrum."""
    out = {"peak_wl": None, "peak_int": None, "centroid": None,
           "fwhm": None, "rms_bw": None, "area": None}
    if x is None or y is None:
        return out
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2 or y.size != x.size:
        return out
    imax = int(np.argmax(y))
    out["peak_int"] = float(y[imax])
    out["peak_wl"] = float(x[imax])
    out["area"] = _trapz(y, x)
    yb = y - float(np.min(y))            # baseline-subtract for centroid / width
    tot = float(np.sum(yb))
    if tot > 0:
        centroid = float(np.sum(x * yb) / tot)
        out["centroid"] = centroid
        out["rms_bw"] = float(np.sqrt(max(0.0, np.sum(yb * (x - centroid) ** 2) / tot)))
    out["fwhm"] = _fwhm(x, y)
    return out


def _smooth(y: np.ndarray, win: int) -> np.ndarray:
    """Savitzky-Golay (quadratic) smoothing without scipy; edge-padded.
    Falls back to a moving average if the coefficient solve fails."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if win < 3 or n < 3:
        return y
    if win % 2 == 0:
        win += 1
    win = min(win, n if n % 2 == 1 else n - 1)
    if win < 3:
        return y
    half = win // 2
    k = np.arange(-half, half + 1)
    A = np.vstack([k ** 0, k ** 1, k ** 2]).T
    try:
        coef = np.linalg.pinv(A)[0]                  # SG smoothing weights
    except Exception:
        coef = np.full(win, 1.0 / win)
    ypad = np.pad(y, half, mode="edge")
    return np.convolve(ypad, coef[::-1], mode="valid")


def _parse_number_list(text: str) -> list[float]:
    """Parse a list of numbers from free text, auto-detecting the delimiter so both
    of these export styles work:
      • comma-separated integers on one line  → '595,595,596,...'  (comma = separator)
      • whitespace/semicolon-separated values with Czech decimal commas
                                               → '593,27 593,54'    (comma = decimal)
    """
    import re
    text = text.strip()
    if not text:
        return []
    has_ws = bool(re.search(r"\s", text))
    if ";" in text:                          # semicolons separate; comma = decimal
        parts = [p.replace(",", ".") for p in text.split(";")]
    elif "," in text and not has_ws:         # comma-separated values, no spaces
        parts = text.split(",")              #   → comma is the separator
    elif has_ws:                             # whitespace separates; comma = decimal
        parts = [p.replace(",", ".") for p in re.split(r"\s+", text)]
    else:
        parts = [text]
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            m = re.search(r"[-+]?\d+(?:\.\d+)?", p.replace(",", "."))
            if m:
                out.append(float(m.group()))
    return out


def _reconstruct_wavelength_axis(vals: np.ndarray) -> np.ndarray:
    """Rebuild a strictly-increasing wavelength axis from values whose decimals were
    lost on export (so each integer nm repeats 3–4×). Each run of an equal integer
    N of length k becomes N + (i+0.5)/k, preserving the per-nm count while making the
    axis monotonic. If the values already carry decimals, they are returned unchanged.
    """
    vals = np.asarray(vals, dtype=float)
    if vals.size == 0:
        return vals
    if np.any(np.abs(vals - np.round(vals)) > 1e-6):
        return vals                      # already has real decimals — trust them
    out = np.empty_like(vals)
    i, n = 0, vals.size
    while i < n:
        j = i
        while j < n and vals[j] == vals[i]:
            j += 1
        k = j - i
        out[i:j] = vals[i] + (np.arange(k) + 0.5) / k
        i = j
    return out


def _load_x_csv(path: str) -> "np.ndarray | None":
    """Load a wavelength axis from a CSV/text file, repairing decimals stripped on
    export (see _reconstruct_wavelength_axis). Returns None on failure."""
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            text = f.read()
    except Exception:
        return None
    vals = _parse_number_list(text)
    if not vals:
        return None
    return _reconstruct_wavelength_axis(np.asarray(vals, dtype=float))


def _compute_stats(arrs: list[np.ndarray]) -> dict | None:
    """Combine a list of waveforms (keeping only the most common length).

    Computes every averaging method up front so the user can switch the method
    afterwards without re-fetching the data.
    """
    if not arrs:
        return None
    lens = [len(a) for a in arrs]
    common = max(set(lens), key=lens.count)
    arrs = [a for a in arrs if len(a) == common]
    if not arrs:
        return None
    stack = np.vstack(arrs)
    return {
        "mean":    stack.mean(axis=0),
        "median":  np.median(stack, axis=0),
        "trimmed": _trimmed_mean(stack, 0.1),
        "sigma":   _sigma_clipped_mean(stack, 3.0),
        "std":     stack.std(axis=0),
        "p10":     np.percentile(stack, 10, axis=0),
        "p90":     np.percentile(stack, 90, axis=0),
        "stack":   stack,          # individual spectra (feature: show all spectra)
        "n":       len(arrs),
    }


# user-facing dropdown label -> stat key
_METHODS = {
    "Mean":               "mean",
    "Median":             "median",
    "Trimmed mean 10%":   "trimmed",
    "Sigma-clipped mean": "sigma",
    "Every spectrum":     SINGLE_METHOD,
}

# Short forms for the legend. Measured on synthetic data, the four methods differ by
# only 0.1-0.2 % of peak on clean spectra (and up to 30 % once a few bad shots are in
# there), so on a good day switching them looks like nothing happened. Naming the
# active method in the legend is what makes the setting visibly do something.
_METHOD_SHORT = {
    "mean":    "Mean",
    "median":  "Median",
    "trimmed": "Trimmed 10%",
    "sigma":   "Sigma-clip",
    SINGLE_METHOD: "Every spectrum",
}


def _ns_to_dt(t_ns: int) -> datetime:
    dt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
    return dt.astimezone(_PRAGUE) if _PRAGUE else dt


def _fmt_hms(t_ns: int) -> str:
    return _ns_to_dt(t_ns).strftime("%H:%M:%S")


def _fmt_date(t_ns: int) -> str:
    return _ns_to_dt(t_ns).strftime("%Y-%m-%d")


def _fmt_dur(t0_ns: int, t1_ns: int) -> str:
    secs = max(0, int((t1_ns - t0_ns) / 1e9))
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


def _fmt_window(w: "tuple[int, int]") -> str:
    """'2026-08-25 08:00-12:00' — one picked window, for labels and status text."""
    a, b = _ns_to_dt(w[0]), _ns_to_dt(w[1])
    return f"{a.strftime('%Y-%m-%d %H:%M')}-{b.strftime('%H:%M')}"


# ── _TimeMap ──────────────────────────────────────────────────────────────────
class _TimeMap:
    """The compressed time axis of the search graph.

    The picked windows are laid end to end and the time BETWEEN them is removed
    from the axis, so picking Mon 08-12 and Wed 14-19 gives a graph of two blocks
    side by side instead of two thin traces with two empty days between them.

    x is plain seconds from the start of the first window - deliberately NOT a
    matplotlib date number, because half of matplotlib's date machinery would
    then quietly interpret a compressed x as a real instant. Real dates come back
    on the tick labels through a FuncFormatter.

    With a single window this is the identity (offset 0), so the one-day graph
    behaves exactly as it did before: there is no separate code path for it.

    Everything the user sees or clicks on the search graph goes through here:
    trace data, region shading, the drag that creates a region, the crosshair
    readout and the axis limits. Regions themselves keep storing absolute ns.
    """

    def __init__(self, windows: "list[tuple[int, int]]"):
        # Sorted, positive-length, non-overlapping: overlaps would make from_x
        # ambiguous. The picker gives one window per day, so a merge is enough.
        ws: list[list[int]] = []
        for a, b in sorted((w for w in windows if w[1] > w[0])):
            if ws and a <= ws[-1][1]:
                ws[-1][1] = max(ws[-1][1], b)
            else:
                ws.append([a, b])
        self.windows: list[tuple[int, int]] = [(a, b) for a, b in ws]
        # Cumulative x offset of each window's start, in seconds.
        self._off: list[float] = []
        acc = 0.0
        for a, b in self.windows:
            self._off.append(acc)
            acc += (b - a) / 1e9
        self._total = acc

    def __bool__(self) -> bool:
        return bool(self.windows)

    def is_identity(self) -> bool:
        return len(self.windows) <= 1

    # ── time → x ──────────────────────────────────────────────────────────────
    def to_x(self, t_ns: int) -> "float | None":
        """Seconds on the compressed axis, or None when t falls in a removed gap.

        The window end is EXCLUSIVE, the same convention the calendar uses
        (daypicker.seg_bounds_ns): 12:00-13:00 is one hour, and a sample stamped
        exactly 13:00:00 belongs to the next window, not this one.
        """
        for (a, b), off in zip(self.windows, self._off):
            if a <= t_ns < b:
                return off + (t_ns - a) / 1e9
        return None

    def to_x_clamped(self, t_ns: int) -> float:
        """Like to_x, but a time in a gap is pulled to the nearest window edge.

        Used for region shading: a region selected before the windows changed can
        start or end in time that is no longer on the axis, and it still has to be
        drawn somewhere sensible instead of vanishing.
        """
        if not self.windows:
            return 0.0
        if t_ns <= self.windows[0][0]:
            return 0.0
        if t_ns >= self.windows[-1][1]:
            return self._total
        for i, ((a, b), off) in enumerate(zip(self.windows, self._off)):
            if t_ns < a:                       # in the gap before this window
                return off
            if t_ns <= b:
                return off + (t_ns - a) / 1e9
        return self._total

    def clip(self, t0_ns: int, t1_ns: int) -> "list[tuple[float, float]]":
        """Split [t0, t1] into one x-range per window it actually overlaps.

        A region dragged across a window boundary must be painted as two blocks,
        never as one block that also covers the removed time.
        """
        out = []
        for (a, b), off in zip(self.windows, self._off):
            lo, hi = max(t0_ns, a), min(t1_ns, b)
            if hi > lo:
                out.append((off + (lo - a) / 1e9, off + (hi - a) / 1e9))
        return out

    def split_ns(self, t0_ns: int, t1_ns: int) -> "list[tuple[int, int]]":
        """The same split, but in absolute ns - one interval per window."""
        out = []
        for a, b in self.windows:
            lo, hi = max(t0_ns, a), min(t1_ns, b)
            if hi > lo:
                out.append((lo, hi))
        return out

    def contains(self, t_ns: int) -> bool:
        return any(a <= t_ns < b for a, b in self.windows)

    def window_len_ns(self, t_ns: int) -> int:
        """Length of the loaded window this instant falls in (0 if in removed time).

        Lets a caller ask "how much of that day did I select?" — a day loaded with
        a 30-minute window is a small slice of a multi-day drag even when it was
        selected in full.
        """
        for a, b in self.windows:
            if a <= t_ns < b:
                return b - a
        return 0

    def window_at_x(self, x: float) -> "tuple[int, int] | None":
        """The (start, end) of the window under this point on the axis.

        None outside every window — the axis has a hair of padding on both sides
        (see xlim), so a click there is not on any day. On a join the next window
        wins, the same convention from_x follows.
        """
        if not self.windows or x < 0 or x > self._total:
            return None
        last = len(self.windows) - 1
        for i, ((a, b), off) in enumerate(zip(self.windows, self._off)):
            if x < off + (b - a) / 1e9 or i == last:
                return (a, b)
        return None

    # ── x → time ──────────────────────────────────────────────────────────────
    def from_x(self, x: float) -> int:
        """Absolute ns for a point on the compressed axis (clamped to the ends).

        A join is a single point standing for two instants — the end of one
        window and the start of the next. The next window wins, so a drag that
        starts on a join belongs to the day the user can see to the right of it,
        and a drag that ends there stops at the end of the day on the left.
        """
        if not self.windows:
            return 0
        if x <= 0:
            return self.windows[0][0]
        last = len(self.windows) - 1
        for i, ((a, b), off) in enumerate(zip(self.windows, self._off)):
            end = off + (b - a) / 1e9
            if x < end or i == last:
                return int(a + max(0.0, min(x, end) - off) * 1e9)
        return self.windows[-1][1]

    # ── axis ──────────────────────────────────────────────────────────────────
    def xlim(self) -> "tuple[float, float]":
        # A hair of margin on both sides, so the first and last sample and the
        # boundary dividers are not drawn on top of the axis spines.
        pad = max(1.0, self._total * 0.002)
        return -pad, self._total + pad

    def boundaries(self) -> "list[float]":
        """x of every join between two windows (nothing for a single window)."""
        return [off for off in self._off[1:]]

    def window_centres(self) -> "list[tuple[float, tuple[int, int]]]":
        return [(off + (b - a) / 2e9, (a, b))
                for (a, b), off in zip(self.windows, self._off)]

    def is_window_start(self, x: float, tol: float = 1.0) -> bool:
        return any(abs(x - off) <= tol for off in self._off)

    def trace(self, t_ns, vals):
        """Remap a sorted sample series onto the compressed axis.

        Returns (plot_x, plot_y, cur_x, cur_y).

        plot_* carry a NaN break at every window join, so the steps-post line
        cannot be drawn straight across removed time — without it Monday evening
        would appear joined to Wednesday morning by a fake horizontal line. Each
        window's last value is also held out to the window's own end, which is
        what an archived value actually does.

        cur_* are the same points without the NaNs, kept strictly ascending
        because the crosshair searchsorts on them.
        """
        px, py, cx, cy = [], [], [], []
        for (a, b), off in zip(self.windows, self._off):
            lo = int(np.searchsorted(t_ns, a, side="left"))
            hi = int(np.searchsorted(t_ns, b, side="left"))   # end is exclusive
            if hi <= lo:
                continue
            xs = off + (t_ns[lo:hi] - a) / 1e9
            ys = vals[lo:hi]
            end_x = off + (b - a) / 1e9
            cx.append(xs); cy.append(ys)
            if px:
                px.append(np.array([np.nan])); py.append(np.array([np.nan]))
            px.append(xs); py.append(ys)
            # Hold the last value out to the window edge (zero-order hold).
            if xs[-1] < end_x:
                px.append(np.array([end_x])); py.append(np.array([ys[-1]]))
                cx.append(np.array([end_x])); cy.append(np.array([ys[-1]]))
        if not px:
            empty = np.array([], dtype=float)
            return empty, empty, empty, empty
        return (np.concatenate(px), np.concatenate(py),
                np.concatenate(cx), np.concatenate(cy))

    def ticks(self, max_ticks: int = 12) -> "tuple[list[float], list[str]]":
        """Tick positions and labels showing the REAL date and time.

        Every window gets at least its own start tick, so no block is left
        unlabelled however short it is. Beyond a handful of windows the start
        ticks alone fill the axis, and inner clock ticks are dropped — with a
        fortnight picked they printed straight through the dates ("08-2412:00").
        """
        if not self.windows:
            return [], []
        n = len(self.windows)
        multi = n > 1
        if n > max_ticks // 2:
            # Day starts only, thinned out if even those would not fit.
            every = max(1, -(-n // max_ticks))
            pos, lab = [], []
            for i, ((a, _b), off) in enumerate(zip(self.windows, self._off)):
                if i % every:
                    continue
                pos.append(off)
                lab.append(_ns_to_dt(a).strftime("%m-%d\n%H:%M"))
            return pos, lab
        per = max(1, max_ticks // n)
        # A round step that keeps roughly `per` ticks inside the longest window.
        longest = max((b - a) / 1e9 for a, b in self.windows)
        for step in (300, 600, 900, 1800, 3600, 2 * 3600, 3 * 3600, 6 * 3600,
                     12 * 3600, 24 * 3600):
            if longest / step <= per:
                break
        pos: list[float] = []
        lab: list[str] = []
        for (a, b), off in zip(self.windows, self._off):
            span = (b - a) / 1e9
            dt_a = _ns_to_dt(a)
            # First tick of the window: the window's own start, always labelled
            # with the date when there is more than one window.
            pos.append(off)
            lab.append(dt_a.strftime("%m-%d\n%H:%M") if multi
                       else dt_a.strftime("%H:%M"))
            # Then round clock times inside it.
            first = ((int(dt_a.hour * 3600 + dt_a.minute * 60 + dt_a.second)
                      // step) + 1) * step
            sec = first - (dt_a.hour * 3600 + dt_a.minute * 60 + dt_a.second)
            while sec < span - step * 0.25:
                if sec > step * 0.25:
                    pos.append(off + sec)
                    lab.append(_ns_to_dt(int(a + sec * 1e9)).strftime("%H:%M"))
                sec += step
        return pos, lab


# ── Cross-thread signal carrier ───────────────────────────────────────────────
class _Sig(QObject):
    done       = Signal(object)
    error      = Signal(str)
    progress   = Signal(str)
    progress_n = Signal(int, int)   # (done, total) for the progress bar


def _bg(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ── PvSearchDialog ────────────────────────────────────────────────────────────
class PvSearchDialog(QDialog):
    """Load all CPVA channel names once, then filter locally as the user types."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add PV")
        self.setMinimumSize(520, 460)
        self._added: list = []
        # Name typed for a channel, kept while the selection changes so a name is
        # not lost by clicking one more channel.
        self._names: dict = {}
        self._all_channels: list = list(_cpva_channel_cache)
        self._build_ui()
        if self._all_channels:
            self._lbl_status.setText(
                f"{len(self._all_channels)} channels loaded. Type to filter."
            )
        else:
            self._kick_load()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addWidget(QLabel("Search for a CPVA channel (type part of the name):"))
        self._edit = QLineEdit()
        self._edit.setPlaceholderText('search any archiver PV…  (e.g. "l3 sbw4")')
        self._edit.textEdited.connect(self._filter)
        lay.addWidget(self._edit)
        self._lbl_status = QLabel("Loading channels from CPVA…")
        self._lbl_status.setStyleSheet("color: #666; font-size: 10px;")
        lay.addWidget(self._lbl_status)
        self._lst = QListWidget()
        self._lst.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._lst.setAlternatingRowColors(False)
        self._lst.itemSelectionChanged.connect(self._sync_picked)
        lay.addWidget(self._lst, stretch=1)
        note = QLabel("Click to select (Ctrl+click for multiple), then click Add.")
        note.setStyleSheet("font-size: 10px; color: #555;")
        lay.addWidget(note)

        # ── Chosen channels, with the name they will carry in the PV list ──
        lay.addWidget(QLabel("Name in the PV list — click a name to change it:"))
        self._tbl_pick = QTableWidget(0, 2)
        self._tbl_pick.setHorizontalHeaderLabels(["Name", "Channel"])
        self._tbl_pick.verticalHeader().setVisible(False)
        self._tbl_pick.setMaximumHeight(130)
        self._tbl_pick.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tbl_pick.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # One click opens the name for typing — a double-click-only cell reads as
        # a read-only column.
        self._tbl_pick.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        _ph = self._tbl_pick.horizontalHeader()
        _ph.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        _ph.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tbl_pick.setColumnWidth(0, 150)
        self._tbl_pick.itemChanged.connect(self._on_name_edited)
        lay.addWidget(self._tbl_pick)
        self._sync_picked()

        row = QHBoxLayout()
        self._btn_add = QPushButton("Add selected")
        self._btn_add.setStyleSheet(_BTN_PRIMARY)
        self._btn_add.clicked.connect(self._on_add)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(self._btn_add)
        row.addWidget(btn_cancel)
        lay.addLayout(row)

    def _kick_load(self):
        sig = _Sig(self)
        sig.done.connect(self._on_loaded)
        sig.error.connect(lambda e: self._lbl_status.setText(f"Load failed: {e}"))
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception as exc:
                sig.error.emit(str(exc))
        _bg(_work)

    def _on_loaded(self, channels: list):
        global _cpva_channel_cache
        _cpva_channel_cache = channels
        self._all_channels = channels
        self._lbl_status.setText(
            f"{len(channels)} channels loaded. Type to filter."
            if channels else "CPVA returned no channels."
        )
        self._filter(self._edit.text())

    def _filter(self, text: str):
        self._lst.clear()
        if not text.strip() or not self._all_channels:
            return
        matches = _pv_search(self._all_channels, text)
        for ch in matches[:300]:
            self._lst.addItem(ch)
        extra = f" (showing top 300)" if len(matches) > 300 else ""
        self._lbl_status.setText(
            f"{len(matches)} match(es){extra}." if matches else "No matches."
        )

    def _picked_channels(self) -> list:
        return [it.text() for it in self._lst.selectedItems()]

    def _sync_picked(self):
        """Mirror the list selection into the name table, keeping typed names."""
        chans = self._picked_channels()
        self._tbl_pick.blockSignals(True)
        self._tbl_pick.setRowCount(0)
        for ch in chans:
            r = self._tbl_pick.rowCount()
            self._tbl_pick.insertRow(r)
            name = QTableWidgetItem(self._names.get(ch, ch))
            name.setToolTip("Click to type the name you want to see in the PV list.")
            chan = QTableWidgetItem(ch)
            chan.setFlags(chan.flags() & ~Qt.ItemFlag.ItemIsEditable)
            chan.setToolTip(ch)
            self._tbl_pick.setItem(r, 0, name)
            self._tbl_pick.setItem(r, 1, chan)
        self._tbl_pick.blockSignals(False)
        self._tbl_pick.setVisible(bool(chans))

    def _on_name_edited(self, item: QTableWidgetItem):
        if item.column() != 0:
            return
        chan_item = self._tbl_pick.item(item.row(), 1)
        if chan_item is None:
            return
        text = item.text().strip()
        # An emptied name falls back to the channel itself rather than an unnamed
        # row that no legend could label.
        if not text:
            text = chan_item.text()
            self._tbl_pick.blockSignals(True)
            item.setText(text)
            self._tbl_pick.blockSignals(False)
        self._names[chan_item.text()] = text

    def _on_add(self):
        chans = self._picked_channels()
        if not chans:
            QMessageBox.information(self, "Add PV", "Select at least one channel from the list.")
            return
        self._added = [(self._names.get(ch, ch), ch) for ch in chans]
        self.accept()

    def added_pvs(self) -> list:
        """Returns list of (label, channel) tuples."""
        return self._added


# ── XAxisSourceDialog ───────────────────────────────────────────────────────--
class XAxisSourceDialog(QDialog):
    """Ask how to build the wavelength (X) axis for a spectrum PV that has no
    matching _X channel: copy from another PV, copy + linear transform, or use
    the plain sample index."""

    def __init__(self, base_pv: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wavelength axis")
        self.setMinimumSize(460, 420)
        self._all_channels: list = list(_cpva_channel_cache)
        self._result: dict | None = None
        self._build_ui(base_pv)
        if self._all_channels:
            self._lbl_status.setText(f"{len(self._all_channels)} channels. Type to filter.")
        else:
            self._kick_load()

    def _build_ui(self, base_pv: str):
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        info = QLabel(
            f"<b>{base_pv}</b> has no matching <b>_X</b> channel in the archive.<br>"
            "Choose how to build the wavelength axis:"
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self._grp = QButtonGroup(self)
        self._rb_pv     = QRadioButton("Copy X axis from another PV")
        self._rb_linear = QRadioButton("Copy from a PV, then apply a linear transform")
        self._rb_csv    = QRadioButton("Load X axis from a CSV / text file")
        self._rb_index  = QRadioButton("Use the sample index (0, 1, 2 …)")
        self._rb_pv.setChecked(True)
        for rb in (self._rb_pv, self._rb_linear, self._rb_csv, self._rb_index):
            self._grp.addButton(rb)
            lay.addWidget(rb)
            rb.toggled.connect(self._sync_enabled)

        # CSV file picker (mode 'csv')
        row_csv = QHBoxLayout()
        row_csv.addSpacing(20)
        self._edit_csv = QLineEdit()
        self._edit_csv.setPlaceholderText("Path to a .csv / .txt wavelength file…")
        row_csv.addWidget(self._edit_csv, stretch=1)
        self._btn_browse = QPushButton("Browse…")
        self._btn_browse.clicked.connect(self._browse_csv)
        row_csv.addWidget(self._btn_browse)
        lay.addLayout(row_csv)
        note_csv = QLabel(
            "Decimals lost on export (e.g. each nm repeated 3–4×) are rebuilt "
            "automatically into an increasing axis."
        )
        note_csv.setWordWrap(True)
        note_csv.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        lay.addWidget(note_csv)

        # linear transform spins
        row_lin = QHBoxLayout()
        row_lin.addSpacing(20)
        row_lin.addWidget(QLabel("x' ="))
        self._sb_scale = QDoubleSpinBox()
        self._sb_scale.setRange(-1e6, 1e6)
        self._sb_scale.setDecimals(6)
        self._sb_scale.setValue(1.0)
        row_lin.addWidget(self._sb_scale)
        row_lin.addWidget(QLabel("· x +"))
        self._sb_offset = QDoubleSpinBox()
        self._sb_offset.setRange(-1e6, 1e6)
        self._sb_offset.setDecimals(6)
        self._sb_offset.setValue(0.0)
        row_lin.addWidget(self._sb_offset)
        row_lin.addWidget(QLabel("nm"))
        row_lin.addStretch(1)
        lay.addLayout(row_lin)

        # channel picker (shared by 'pv' and 'linear')
        self._edit = QLineEdit()
        self._edit.setPlaceholderText('search _X channels…  (e.g. "l3 sbw4")')
        self._edit.textEdited.connect(self._filter)
        lay.addWidget(self._edit)
        self._lbl_status = QLabel("Loading channels from CPVA…")
        self._lbl_status.setStyleSheet("color: #666; font-size: 10px;")
        lay.addWidget(self._lbl_status)
        self._lst = QListWidget()
        self._lst.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self._lst, stretch=1)

        row = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.setStyleSheet(_BTN_PRIMARY)
        btn_ok.clicked.connect(self._on_ok)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        lay.addLayout(row)
        self._sync_enabled()

    def _sync_enabled(self, *_):
        need_pv = self._rb_pv.isChecked() or self._rb_linear.isChecked()
        self._edit.setEnabled(need_pv)
        self._lst.setEnabled(need_pv)
        lin = self._rb_linear.isChecked()
        self._sb_scale.setEnabled(lin)
        self._sb_offset.setEnabled(lin)
        csv = self._rb_csv.isChecked()
        self._edit_csv.setEnabled(csv)
        self._btn_browse.setEnabled(csv)

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select wavelength CSV", "",
            "CSV / text files (*.csv *.txt *.dat);;All files (*)",
        )
        if path:
            self._edit_csv.setText(path)
            self._rb_csv.setChecked(True)

    def _kick_load(self):
        sig = _Sig(self)
        sig.done.connect(self._on_loaded)
        sig.error.connect(lambda e: self._lbl_status.setText(f"Load failed: {e}"))
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception as exc:
                sig.error.emit(str(exc))
        _bg(_work)

    def _on_loaded(self, channels: list):
        global _cpva_channel_cache
        _cpva_channel_cache = channels
        self._all_channels = channels
        self._lbl_status.setText(
            f"{len(channels)} channels. Type to filter."
            if channels else "CPVA returned no channels."
        )
        self._filter(self._edit.text())

    def _filter(self, text: str):
        self._lst.clear()
        if not text.strip() or not self._all_channels:
            return
        matches = _pv_search(self._all_channels, text)
        for ch in matches[:300]:
            self._lst.addItem(ch)
        extra = " (top 300)" if len(matches) > 300 else ""
        self._lbl_status.setText(
            f"{len(matches)} match(es){extra}." if matches else "No matches."
        )

    def _on_ok(self):
        if self._rb_index.isChecked():
            self._result = {"mode": "index"}
            self.accept()
            return
        if self._rb_csv.isChecked():
            path = self._edit_csv.text().strip()
            if not path or not os.path.isfile(path):
                QMessageBox.information(self, "Wavelength axis",
                                        "Pick a valid CSV / text file first.")
                return
            arr = _load_x_csv(path)
            if arr is None or arr.size == 0:
                QMessageBox.warning(self, "Wavelength axis",
                                    "No numbers could be read from that file.")
                return
            self._result = {"mode": "csv", "csv_path": path}
            self.accept()
            return
        items = self._lst.selectedItems()
        if not items:
            QMessageBox.information(self, "Wavelength axis",
                                    "Pick a source channel from the list first.")
            return
        src = items[0].text()
        if self._rb_linear.isChecked():
            self._result = {"mode": "linear", "source_pv": src,
                            "scale": float(self._sb_scale.value()),
                            "offset": float(self._sb_offset.value())}
        else:
            self._result = {"mode": "pv", "source_pv": src}
        self.accept()

    def result_cfg(self) -> "dict | None":
        return self._result


# ── PresetEditDialog ──────────────────────────────────────────────────────────
class PresetEditDialog(QDialog):
    """Save, load, and delete named PV presets."""
    preset_loaded = Signal(list)   # list of (label, channel) tuples

    def __init__(self, current_pvs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Presets")
        self.resize(620, 440)
        self._sel_pvs: list = list(current_pvs)   # (label, channel) tuples — working copy
        self._presets = _load_search_presets()
        self._all_channels: list = list(_cpva_channel_cache)
        self._build_ui()
        self._refresh()
        self._refresh_list()
        if self._all_channels:
            self._lbl_search.setText(f"{len(self._all_channels)} channels. Type to filter.")
        else:
            self._kick_load()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(10)

        # ── Left: saved presets list ──────────────────────────────────────────
        left_w = QWidget()
        left_w.setFixedWidth(160)
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        left.addWidget(QLabel("Saved presets:"))
        self._lst_presets = QListWidget()
        left.addWidget(self._lst_presets, stretch=1)
        self._btn_load_preset = QPushButton("Load →")
        self._btn_load_preset.setToolTip("Load this preset's PVs into the selection.")
        self._btn_load_preset.clicked.connect(self._on_load_preset)
        left.addWidget(self._btn_load_preset)
        body.addWidget(left_w)

        # ── Right: search bar + combined list ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(4)

        right.addWidget(QLabel("Search channels to add (or click a green row to deselect):"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText('search any archiver PV…  (e.g. "l3 sbw4")')
        self._search_edit.textEdited.connect(self._refresh_list)
        right.addWidget(self._search_edit)
        self._lbl_search = QLabel("Loading channels from CPVA…")
        self._lbl_search.setStyleSheet("color: #666; font-size: 10px;")
        right.addWidget(self._lbl_search)

        # One combined list: selected PVs (green, top) + search results (below)
        self._lst_pvs = QListWidget()
        self._lst_pvs.itemClicked.connect(self._on_item_clicked)
        right.addWidget(self._lst_pvs, stretch=1)

        body.addLayout(right, stretch=1)
        outer.addLayout(body, stretch=1)

        # ── Bottom buttons ────────────────────────────────────────────────────
        btns = QHBoxLayout()
        self._btn_save = QPushButton("Save PVs as preset…")
        self._btn_del  = QPushButton("Delete preset")
        self._btn_del.setStyleSheet(_BTN_DANGER)
        btn_apply = QPushButton("Apply && Close")
        btn_apply.setStyleSheet(_BTN_PRIMARY)
        btn_close = QPushButton("Close")
        self._btn_save.clicked.connect(self._on_save_preset)
        self._btn_del.clicked.connect(self._on_delete_preset)
        btn_apply.clicked.connect(self._on_apply)
        btn_close.clicked.connect(self.close)
        for b in (self._btn_save, self._btn_del, btn_apply, btn_close):
            btns.addWidget(b)
        outer.addLayout(btns)

    # ── Preset list management ────────────────────────────────────────────────
    def _refresh(self):
        self._lst_presets.blockSignals(True)
        self._lst_presets.clear()
        for p in self._presets:
            self._lst_presets.addItem(p["name"])
        self._lst_presets.blockSignals(False)

    def _on_load_preset(self):
        row = self._lst_presets.currentRow()
        if not (0 <= row < len(self._presets)):
            QMessageBox.information(self, "Load Preset", "Select a preset from the list first.")
            return
        pvs_data = self._presets[row].get("pvs", [])
        pvs = [(d["label"], d["channel"]) for d in pvs_data if d.get("label") and d.get("channel")]
        self._sel_pvs = pvs
        self._refresh_list(self._search_edit.text())

    def _on_save_preset(self):
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        pvs = [{"label": lbl, "channel": ch} for lbl, ch in self._sel_pvs]
        for p in self._presets:
            if p["name"] == name:
                p["pvs"] = pvs
                break
        else:
            self._presets.append({"name": name, "pvs": pvs})
        _save_search_presets(self._presets)
        self._refresh()

    def _on_delete_preset(self):
        row = self._lst_presets.currentRow()
        if not (0 <= row < len(self._presets)):
            QMessageBox.information(self, "Delete Preset", "Select a preset from the list first.")
            return
        name = self._presets[row]["name"]
        if QMessageBox.question(
            self, "Delete Preset", f"Delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        del self._presets[row]
        _save_search_presets(self._presets)
        self._refresh()

    def _on_apply(self):
        if self._sel_pvs:
            self.preset_loaded.emit(self._sel_pvs)
        self.close()

    # ── Combined channel list (selected PVs + search results) ─────────────────
    def _refresh_list(self, text: str = ""):
        q = (text.strip().lower() if isinstance(text, str) else "")
        sel_channels = {ch for _, ch in self._sel_pvs}

        self._lst_pvs.blockSignals(True)
        self._lst_pvs.clear()

        # 1. Selected PVs always at top, green background
        for lbl, ch in self._sel_pvs:
            item = QListWidgetItem(f"✓  {lbl}  [{ch}]")
            item.setData(Qt.ItemDataRole.UserRole, ch)
            item.setToolTip(f"Selected — click to deselect\n{ch}")
            item.setBackground(QColor("#c8e6c9"))
            self._lst_pvs.addItem(item)

        # 2. Search results below (not already selected)
        if q and self._all_channels:
            matches = _pv_search(self._all_channels, q, exclude=sel_channels)
            for ch in matches[:300]:
                item = QListWidgetItem(ch)
                item.setData(Qt.ItemDataRole.UserRole, ch)
                item.setToolTip(f"Click to add to selection\n{ch}")
                self._lst_pvs.addItem(item)
            extra = " (top 300)" if len(matches) > 300 else ""
            self._lbl_search.setText(f"{len(matches)} match(es){extra}.")
        elif q:
            self._lbl_search.setText("No matches.")

        self._lst_pvs.blockSignals(False)

    def _on_item_clicked(self, item: QListWidgetItem):
        ch = item.data(Qt.ItemDataRole.UserRole)
        # If already selected → remove it
        for i, (lbl, existing_ch) in enumerate(self._sel_pvs):
            if existing_ch == ch:
                del self._sel_pvs[i]
                self._refresh_list(self._search_edit.text())
                return
        # Otherwise → add to selection (use channel as label initially)
        self._sel_pvs.append((ch, ch))
        self._refresh_list(self._search_edit.text())

    # ── Channel loading ───────────────────────────────────────────────────────
    def _kick_load(self):
        sig = _Sig(self)
        sig.done.connect(self._on_ch_loaded)
        sig.error.connect(lambda e: self._lbl_search.setText(f"Load failed: {e}"))
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception as exc:
                sig.error.emit(str(exc))
        _bg(_work)

    def _on_ch_loaded(self, channels: list):
        global _cpva_channel_cache
        _cpva_channel_cache = channels
        self._all_channels = channels
        self._lbl_search.setText(
            f"{len(channels)} channels. Type to filter."
            if channels else "CPVA returned no channels."
        )
        self._refresh_list(self._search_edit.text())


# ── ExportDialog ──────────────────────────────────────────────────────────────
class ExportDialog(QDialog):
    """Pick what to export: one CSV (details + curve data) and/or the graph image."""

    def __init__(self, n_regions: int, n_live: int, method: str, parent=None,
                 single: bool = False, n_shots: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Export results")
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        parts = []
        if n_regions:
            parts.append(f"{n_regions} spectra"
                         + (f" = {n_shots} measured shots" if single and n_shots
                            else ""))
        if n_live:
            parts.append(f"{n_live} live shot(s)")
        hdr = QLabel("Export " + " + ".join(parts) + ":")
        hdr.setStyleSheet("font-weight: 700; font-size: 12px;")
        lay.addWidget(hdr)

        self._chk_data = QCheckBox(
            "Data table  →  CSV  (details + one column per measured spectrum "
            "+ live shots)" if single else
            f"Data table  →  CSV  (details + wavelength/{method}/std + live shots)"
        )
        self._chk_graph = QCheckBox("Graph image  →  picture of the spectra plot")
        for c in (self._chk_data, self._chk_graph):
            c.setChecked(True)
            c.setStyleSheet(_CHK_STYLE)
            lay.addWidget(c)

        row_fmt = QHBoxLayout()
        row_fmt.addWidget(QLabel("Image format:"))
        self._cmb_fmt = QComboBox()
        self._cmb_fmt.addItems(["png", "pdf", "svg"])
        row_fmt.addWidget(self._cmb_fmt, stretch=1)
        lay.addLayout(row_fmt)

        note_text = (
            "One CSV holds a details block (date, time, energy, dispersion orders) "
            "followed by the curve table. A 'sep=;' line and a decimal point let "
            "Excel open it directly in any locale."
        )
        if single:
            note_text += (
                "  The display is set to Every spectrum, so the curve table holds "
                "one column per measured shot, named by its time, with the shot's "
                "own intensity against the wavelength column — every shot, even "
                "the ones the graph left out when it thinned the picture. "
                "Thousands of columns make a large file, and Excel stops reading "
                "at 16 384."
            )
        note = QLabel(note_text)
        note.setWordWrap(True)
        note.setStyleSheet(
            "background:#e8f0fe; border:1px solid #90CAF9; border-radius:4px; "
            "padding:6px; font-size:11px; color:#0D47A1;"
        )
        lay.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Export  \U0001F4BE")
        ok_btn.setStyleSheet(
            "QPushButton { background:#1565C0; color:white; font-weight:700; "
            "padding:6px 16px; border-radius:4px; }"
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def options(self) -> dict:
        return {
            "data":      self._chk_data.isChecked(),
            "graph":     self._chk_graph.isChecked(),
            "graph_fmt": self._cmb_fmt.currentText(),
        }


# ── _AxisLabelsDialog ─────────────────────────────────────────────────────────
class _AxisLabelsDialog(QDialog):
    """Edit axis title, X label and Y label."""

    def __init__(self, ax, canvas, parent=None):
        super().__init__(parent)
        self._ax = ax
        self._canvas = canvas
        self.setWindowTitle("Axis labels")
        self.setModal(True)
        self.setMinimumWidth(370)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        grp = QGroupBox("Labels")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        def _row(label_text, current):
            r = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(65)
            edit = QLineEdit(current)
            r.addWidget(lbl)
            r.addWidget(edit, stretch=1)
            gl.addLayout(r)
            return edit

        self._e_title  = _row("Title:",   ax.get_title())
        self._e_xlabel = _row("X label:", ax.get_xlabel())
        self._e_ylabel = _row("Y label:", ax.get_ylabel())
        lay.addWidget(grp)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _apply(self):
        self._ax.set_title(self._e_title.text())
        self._ax.set_xlabel(self._e_xlabel.text())
        self._ax.set_ylabel(self._e_ylabel.text())
        self._canvas.draw_idle()
        self.accept()


# ── _AxisLimitsDialog ─────────────────────────────────────────────────────────
class _AxisLimitsDialog(QDialog):
    """Edit axis limits.  X limits are disabled for date-based axes."""

    def __init__(self, ax, canvas, parent=None):
        super().__init__(parent)
        self._ax = ax
        self._canvas = canvas
        self.setWindowTitle("Axis limits")
        self.setModal(True)
        self.setMinimumWidth(370)

        # A time axis: either a real matplotlib date axis (the CSS Logger tab) or
        # the Spectra search graph's compressed axis, which is tagged because its
        # formatter is a plain FuncFormatter — typing limits there would mean
        # typing seconds-along-the-selection.
        _date_x = (isinstance(ax.xaxis.get_major_formatter(), mdates.DateFormatter)
                   or bool(getattr(ax, "_sp_time_axis", False)))

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        grp = QGroupBox("Limits")
        grp.setStyleSheet(_GROUP_STYLE)
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        self._e_xmin = self._e_xmax = None
        if _date_x:
            note = QLabel("X axis is time — adjust the range with the zoom/pan "
                          "tools, or pick a different time window.")
            note.setStyleSheet("color: #666; font-size: 10px;")
            note.setWordWrap(True)
            gl.addWidget(note)
        else:
            row_x = QHBoxLayout()
            lx = QLabel("X:")
            lx.setFixedWidth(20)
            self._e_xmin = QLineEdit(f"{xlim[0]:.6g}")
            self._e_xmax = QLineEdit(f"{xlim[1]:.6g}")
            row_x.addWidget(lx)
            row_x.addWidget(QLabel("min:"))
            row_x.addWidget(self._e_xmin, stretch=1)
            row_x.addWidget(QLabel("max:"))
            row_x.addWidget(self._e_xmax, stretch=1)
            gl.addLayout(row_x)

        row_y = QHBoxLayout()
        ly = QLabel("Y:")
        ly.setFixedWidth(20)
        self._e_ymin = QLineEdit(f"{ylim[0]:.6g}")
        self._e_ymax = QLineEdit(f"{ylim[1]:.6g}")
        row_y.addWidget(ly)
        row_y.addWidget(QLabel("min:"))
        row_y.addWidget(self._e_ymin, stretch=1)
        row_y.addWidget(QLabel("max:"))
        row_y.addWidget(self._e_ymax, stretch=1)
        gl.addLayout(row_y)

        lay.addWidget(grp)

        btn_auto = QPushButton("Auto (reset to data)")
        btn_auto.setToolTip("Reset both axes to automatic limits based on plotted data.")
        btn_auto.clicked.connect(self._auto)
        lay.addWidget(btn_auto)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _auto(self):
        self._ax.autoscale()
        self._canvas.draw_idle()
        self.accept()

    def _apply(self):
        if self._e_xmin is not None:
            try:
                self._ax.set_xlim(float(self._e_xmin.text()), float(self._e_xmax.text()))
            except ValueError:
                pass
        try:
            self._ax.set_ylim(float(self._e_ymin.text()), float(self._e_ymax.text()))
        except ValueError:
            pass
        self._canvas.draw_idle()
        self.accept()


# ── SpectraWidget ─────────────────────────────────────────────────────────────
class _SpectraFocusWindow(QWidget):
    """Borderless window that shows nothing but the spectra graph.

    Same shape as the Image Slider's focus mode: a real top-level window (parent
    None, so it can sit on any monitor) with no title bar at all. Qt.Tool keeps it
    off the taskbar; StaysOnTop keeps it above the main window.

    There is no title bar to drag, so moving and resizing are done by hand from the
    border margin — that margin IS the resize grip.
    """

    _BORDER = 8          # px of layout margin that doubles as the resize grip

    def __init__(self, content: QWidget, owner):
        super().__init__(None, Qt.WindowType.Tool
                               | Qt.WindowType.FramelessWindowHint
                               | Qt.WindowType.WindowStaysOnTopHint)
        self._owner = owner
        self._content = content
        self._orig_parent = content.parentWidget()
        self._drag_edges = Qt.Edge(0)
        self._drag_start = None
        self._drag_geom = None
        self._leaving = False       # True while the owner tears this window down

        self.setObjectName("spectraFocus")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Scoped to the object name: Qt's stylesheet parser is unreliable with type
        # selectors on classes whose name starts with an underscore.
        self.setStyleSheet("QWidget#spectraFocus { background: #202020; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(self._BORDER, self._BORDER, self._BORDER, self._BORDER)
        lay.addWidget(content, 1)
        content.show()
        self.setMinimumSize(320, 240)

        for seq in ("Esc", "F11"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._leave)

        self.setMouseTracking(True)
        self._content.installEventFilter(self)
        for child in self._content.findChildren(QWidget):
            child.installEventFilter(self)

    # ── handing the graph back ────────────────────────────────────────────────
    def _leave(self):
        self._owner.toggle_focus_mode()

    def release_content(self):
        """Give the graph back to whoever owned it, event filters removed."""
        self._content.removeEventFilter(self)
        for child in self._content.findChildren(QWidget):
            child.removeEventFilter(self)
        lay = self.layout()
        if lay is not None:
            lay.removeWidget(self._content)
        self._content.setParent(self._orig_parent)
        return self._content

    def closeEvent(self, ev):
        # Alt+F4 must hand the graph back, not destroy it. But close() is also how
        # the owner tears this window down at the end of _focus_leave, and asking it
        # to toggle again there re-entered focus mode immediately.
        if self._leaving:
            ev.accept()
            return
        ev.ignore()
        QTimer.singleShot(0, self._leave)

    # ── move / resize without a title bar ─────────────────────────────────────
    def eventFilter(self, obj, ev):
        # A left-press anywhere on the graph moves the window, and the click is
        # swallowed. Safe because the graph toolbar is hidden in focus mode, so a
        # plain drag on the spectra canvas has nothing else to do — the crosshair
        # follows plain motion and the right-click menu still works.
        if (ev.type() == ev.Type.MouseButtonPress
                and ev.button() == Qt.MouseButton.LeftButton):
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                return True
        return super().eventFilter(obj, ev)

    def _edges_at(self, pos) -> Qt.Edge:
        b, edges = self._BORDER, Qt.Edge(0)
        if pos.x() <= b:                    edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - b:     edges |= Qt.Edge.RightEdge
        if pos.y() <= b:                    edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - b:    edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges) -> Qt.CursorShape:
        left  = bool(edges & Qt.Edge.LeftEdge)
        right = bool(edges & Qt.Edge.RightEdge)
        top   = bool(edges & Qt.Edge.TopEdge)
        bot   = bool(edges & Qt.Edge.BottomEdge)
        if (left and top) or (right and bot):
            return Qt.CursorShape.SizeFDiagCursor
        if (right and top) or (left and bot):
            return Qt.CursorShape.SizeBDiagCursor
        if left or right:
            return Qt.CursorShape.SizeHorCursor
        if top or bot:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(ev)
        edges = self._edges_at(ev.position().toPoint())
        handle = self.windowHandle()
        if edges and handle is not None and handle.startSystemResize(edges):
            return
        if edges:
            # The window manager declined; fall back to arithmetic.
            self._drag_edges = edges
            self._drag_start = ev.globalPosition().toPoint()
            self._drag_geom = self.geometry()
            return
        if handle is not None:
            handle.startSystemMove()

    def mouseMoveEvent(self, ev):
        if self._drag_edges and self._drag_start is not None:
            d = ev.globalPosition().toPoint() - self._drag_start
            g = QRect(self._drag_geom)
            if self._drag_edges & Qt.Edge.LeftEdge:   g.setLeft(g.left() + d.x())
            if self._drag_edges & Qt.Edge.RightEdge:  g.setRight(g.right() + d.x())
            if self._drag_edges & Qt.Edge.TopEdge:    g.setTop(g.top() + d.y())
            if self._drag_edges & Qt.Edge.BottomEdge: g.setBottom(g.bottom() + d.y())
            if g.width() >= self.minimumWidth() and g.height() >= self.minimumHeight():
                self.setGeometry(g)
            return
        self.setCursor(self._cursor_for(self._edges_at(ev.position().toPoint())))
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._drag_edges = Qt.Edge(0)
        self._drag_start = None
        super().mouseReleaseEvent(ev)


class SpectraWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        # What the calendar was asked for: one time window per picked day, each
        # with its own hours. _segments keeps the pick so reopening the calendar
        # shows it again; _tmap is the compressed axis built from the windows.
        self._segments:         list                     = []   # daypicker.PickSeg
        self._windows:          list[tuple[int, int]]    = []   # (start_ns, end_ns)
        self._tmap:             "_TimeMap"               = _TimeMap([])
        # User-editable list of scalar PVs to plot in the top "search" graph.
        # A PV can stay in the list but be taken off the graph (its tick box) —
        # channels parked that way live in _pv_hidden and are filled by the load.
        self._pv_hidden:        set[str]                 = set()
        self._search_pvs:       list[tuple[str, str]]    = self._load_search_pvs()
        # Waveform PVs used for spectrum analysis (X = wavelength axis, Y = intensity).
        # User picks any _X or _Y variant; the base and both axes are auto-derived.
        self._spec_y_pv:        str                      = self._load_spec_y()
        self._spec_base_pv:     str                      = _strip_xy_suffix(self._spec_y_pv)
        self._spec_x_pv:        str                      = self._spec_base_pv + "_X"
        # How to build the wavelength axis when {base}_X is missing.
        # {"mode": "native"|"pv"|"linear"|"index", "source_pv": str, "scale", "offset"}
        self._x_axis_cfg:       dict                     = self._load_x_axis_cfg()
        self._color_mode:       str                      = "order"   # order | gdd | tod
        self._energy_data:      list[tuple[int, float]]  = []
        self._x_data:           np.ndarray | None        = None
        # Single source of truth for regions. Each region dict carries its own
        # selection (t_start/t_end/color/visible/expanded) and, once analyzed,
        # its results (mean/median/.../stack, energy_avg, orders, n).
        self._regions:          list[dict]               = []
        self._region_seq:       int                      = 0   # monotonic id source
        self._row_widgets:      dict[int, dict]          = {}  # id -> row widget refs
        self._span:             SpanSelector | None      = None
        self._top_user_xlim:    tuple | None             = None   # (xmin,xmax) when user zoomed
        self._top_user_ylim:    tuple | None             = None
        self._top_extra_axes:   list                     = []     # twinx axes (multi Y-axis)
        self._top_cursor_series: list                    = []     # per-trace cursor readout state
        self._bot_user_xlim:    tuple | None             = None
        self._bot_user_ylim:    tuple | None             = None
        self._live              = False
        self._live_buf:         deque                    = deque(maxlen=LIVE_BUF_MAX)
        self._live_start_ns:    int                      = 0
        self._live_last_ns:     int                      = 0
        self._live_autofit_done = False
        self._live_timer        = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._live_tick)
        self._blink_timer       = QTimer(self)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_on          = False
        self._busy              = False
        self._cancel            = threading.Event()
        self._loading           = False   # a day load is in flight
        self._focus_win         = None    # frameless graph-only window (F11)
        self._focus_was_maximized = False
        self._focus_saw_minimized = False
        self._focus_toggling    = False
        self._bot_container     = None    # the widget focus mode borrows
        self._live_used_n:      int | None            = None   # shots the average used
        self._live_slice_n      = 0       # shots handed to it (see _on_live_y)
        self._analysis_gen      = 0       # bumped when in-flight results go stale
        self._reanalyze_pending = False   # re-run queued behind an obsolete run
        self._cax_bot:          object | None            = None   # permanent colour-bar slot
        self._colorbar_bot:     object | None            = None
        self._colorbar_info:    dict   | None            = None
        self._twin_bot:         object | None            = None   # ratio compare axis
        self._last_saved_layout: dict                    = {}
        # Right margin the user set by hand in the Subplots dialog, if any. Only
        # meaningful while the layout engine is off — see _set_cbar_space().
        self._bot_right_base:   float  | None            = None
        # Splitter ratio the user last dragged. _update_top_visibility() restores
        # THIS instead of resetting to a hard-coded pair on every mode switch.
        self._split_ratio:      list                     = list(_SPLIT_NO_CURVES)
        # False until the divider is actually dragged. Before that the panel picks
        # the ratio itself: the search graph owns the window while there is nothing
        # to compare, the spectra take two thirds once curves are on screen.
        self._split_user_set:   bool                     = False

        self._build_ui()
        self._connect_signals()
        self._connect_zoom_tracking()
        self._load_layout()
        self._refresh_pill()          # paint the state pill for the first time
        self._fit_pv_columns()        # the table was filled during _build_ui
        self._update_active_card()
        self._ensure_channels_loaded()   # warm the cache for inline search / _X checks

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        # The sidebar lives in a vertical scroll area so that on a small monitor
        # its controls scroll instead of being squeezed / overlapping each other.
        self._sidebar_scroll = QScrollArea()
        self._sidebar_scroll.setWidget(self._make_sidebar())
        self._sidebar_scroll.setWidgetResizable(True)
        self._sidebar_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sidebar_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sbw = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        self._sidebar_scroll.setFixedWidth(SIDEBAR_W + sbw + 2)
        root.addWidget(self._sidebar_scroll)
        root.addWidget(self._make_graphs(), stretch=1)

    def _make_sidebar(self) -> QWidget:
        sb = QWidget()
        sb.setFixedWidth(SIDEBAR_W)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(6)

        # ── Top status block (live indicator + selected day + messages) ──
        row_state = QHBoxLayout()
        row_state.setSpacing(4)
        self._lbl_live_ind = QLabel()
        self._lbl_live_ind.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_live_ind.setToolTip(
            "What this tab is doing right now:\n"
            "○ Idle — nothing running\n"
            "⟳ Working… — loading a day or analysing spectra "
            "(the progress bar by Analyze shows how far)\n"
            "● LIVE — streaming new shots"
        )
        row_state.addWidget(self._lbl_live_ind, stretch=1)
        self._btn_stop = QPushButton("⏹  Stop")
        self._btn_stop.setToolTip(
            "Stop whatever is running: live streaming, a day load or an analysis "
            "in progress. Selections and results already fetched are kept."
        )
        self._btn_stop.setStyleSheet(_BTN_DANGER)
        self._btn_stop.setEnabled(False)
        row_state.addWidget(self._btn_stop)
        lay.addLayout(row_state)

        self._lbl_status = QLabel("Ready.")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet("color: #555; font-size: 10px;")
        lay.addWidget(self._lbl_status)

        # ══ Step 1 — the day and the time window ═══════════════════════
        card1, b1 = _step_card(1, "DAY & TIME")
        self._lbl_day = QLabel("No day selected")
        self._lbl_day.setWordWrap(True)
        self._lbl_day.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #263238; border: none;"
            " background: transparent;")
        b1.addWidget(self._lbl_day)
        # No "&" in a button label: Qt eats it as the shortcut marker, so
        # "Load day & time" came out as "Load day  time" with an underlined t.
        self._btn_pick_day = QPushButton("\U0001F4C5  Load day and time…")
        self._btn_pick_day.setStyleSheet(_BTN_PRIMARY)
        self._btn_pick_day.setToolTip(
            "Open the calendar and load data into the search graph.\n"
            "Click = one day · Ctrl+click = several days · Ctrl+Shift+click = a run of days.\n"
            "Each day gets its own From/To time; only the chosen hours are loaded."
        )
        b1.addWidget(self._btn_pick_day)
        lay.addWidget(card1)

        # ══ Step 2 — the search signal and the list it comes from ══════
        # One card on purpose: the answer on top, the list you change it in below.
        card2, b2 = _step_card(2, "SEARCH BY  —  what I search on")
        self._lbl_active_search = QLabel("—")
        self._lbl_active_search.setWordWrap(True)
        self._lbl_active_search.setStyleSheet(
            "font-size: 12px; font-weight: 700; color: #1B5E20; border: none;"
            " background: transparent;")
        b2.addWidget(self._lbl_active_search)
        self._lbl_active_chan = QLabel("")
        self._lbl_active_chan.setWordWrap(True)
        self._lbl_active_chan.setStyleSheet(
            "font-size: 9px; color: #558B2F; border: none; background: transparent;")
        b2.addWidget(self._lbl_active_chan)

        sep_pv = QFrame()
        sep_pv.setFrameShape(QFrame.Shape.HLine)
        sep_pv.setStyleSheet("color: #c5e1c8; background: #c5e1c8; border: none;"
                             " max-height: 1px;")
        b2.addWidget(sep_pv)

        row_preset = QHBoxLayout()
        row_preset.setSpacing(3)
        lbl_pre = QLabel("Preset:")
        lbl_pre.setStyleSheet("border: none; background: transparent;")
        row_preset.addWidget(lbl_pre)
        self._cmb_preset = QComboBox()
        self._cmb_preset.setToolTip("Load a saved set of PVs. The chosen preset stays "
                                    "selected so you can rename or delete it.")
        self._cmb_preset.addItem("-- select preset --")
        for p in _load_search_presets():
            self._cmb_preset.addItem(p["name"])
        row_preset.addWidget(self._cmb_preset, stretch=1)
        self._btn_preset_add = QPushButton("+")
        self._btn_preset_add.setToolTip("Save the current PV list as a new preset.")
        self._btn_preset_ren = QPushButton("✎")
        self._btn_preset_ren.setToolTip("Rename the selected preset and update it to the current PV list.")
        self._btn_preset_del = QPushButton("🗑")
        self._btn_preset_del.setToolTip("Delete the selected preset.")
        for b in (self._btn_preset_add, self._btn_preset_ren, self._btn_preset_del):
            _fit_button(b, extra=10)     # single glyph, but never clipped
            row_preset.addWidget(b)
        b2.addLayout(row_preset)

        # ── Inline channel search (fast add) ───────────────────────────
        self._edit_pv_search = QLineEdit()
        self._edit_pv_search.setPlaceholderText('🔍  search CPVA channels…  (e.g. "l3 sbw4")')
        self._edit_pv_search.setToolTip("Type part of a channel name; click a result to add it to the list.")
        b2.addWidget(self._edit_pv_search)
        self._lst_pv_search = QListWidget()
        # min/max instead of a hard fixed height so the panel can reflow on a
        # short window (the whole sidebar scrolls as one unit).
        self._lst_pv_search.setMinimumHeight(64)
        self._lst_pv_search.setMaximumHeight(110)
        self._lst_pv_search.setVisible(False)
        self._lst_pv_search.setToolTip("Click a channel to add it to the list below.")
        b2.addWidget(self._lst_pv_search)

        # ── Selected-PV table ──────────────────────────────────────────
        # Three columns: the tick box says what is drawn, the highlighted row says
        # what is searched by. Two different questions, so two different controls.
        self._tbl_pvs = QTableWidget(0, 3)
        self._tbl_pvs.setHorizontalHeaderLabels(["✓", "Label", "Channel"])
        _h0 = self._tbl_pvs.horizontalHeaderItem(_PV_COL_SHOW)
        if _h0 is not None:
            _h0.setToolTip("Drawn in the search graph")
        # Channel is Interactive and sized in _fit_pv_columns(): wide enough for the
        # longest name, but never narrower than the space left in the table, so the
        # column always reaches the right-hand edge instead of leaving a grey gap.
        # A name wider than the panel still scrolls rather than being elided.
        hdr = self._tbl_pvs.horizontalHeader()
        hdr.setSectionResizeMode(_PV_COL_SHOW, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_PV_COL_LABEL, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_PV_COL_CHAN, QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._tbl_pvs.setColumnWidth(_PV_COL_SHOW, _PV_SHOW_W)
        self._tbl_pvs.setColumnWidth(_PV_COL_LABEL, 90)
        # The tick box is painted by hand so it stays visible on the highlighted
        # row (see _TickBoxDelegate).
        self._tbl_pvs.setItemDelegateForColumn(_PV_COL_SHOW, _TickBoxDelegate(self._tbl_pvs))
        # A channel name is up to ~40 characters and the sidebar is 340 px wide, so
        # the name cannot be shown in full at a readable size. Smaller text plus a
        # middle ellipsis keeps both ends — the system and the field — on screen;
        # the full name is in the tooltip and at the top of this card.
        _f = self._tbl_pvs.font()
        _f.setPixelSize(_PV_TABLE_FONT_PX)
        self._tbl_pvs.setFont(_f)
        self._tbl_pvs.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._tbl_pvs.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._tbl_pvs.setWordWrap(False)
        self._tbl_pvs.verticalHeader().setVisible(False)
        self._tbl_pvs.verticalHeader().setDefaultSectionSize(20)
        self._tbl_pvs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_pvs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # min/max instead of a hard fixed height so the table never squeezes over
        # the controls below it on a short window — the sidebar scrolls instead.
        self._tbl_pvs.setMinimumHeight(96)
        self._tbl_pvs.setMaximumHeight(150)
        self._tbl_pvs.setToolTip(
            "Tick box: draw this PV in the search graph or leave it off — it stays "
            "in the list either way. Click a row to search by that PV. Double-click "
            "a label to rename it. The full channel is also shown at the top of "
            "this card and on hover."
        )
        for i, (lbl, ch) in enumerate(self._search_pvs):
            self._tbl_pvs.insertRow(i)
            self._fill_pv_row(i, lbl, ch)
        if self._search_pvs:
            self._tbl_pvs.selectRow(self._first_shown_row())
        b2.addWidget(self._tbl_pvs)

        # Add / Remove buttons
        row_pv = QHBoxLayout()
        self._btn_add_pv = QPushButton("+ Add PV…")
        self._btn_add_pv.setToolTip("Open a full CPVA channel search to add one or more PVs.")
        self._btn_rem_pv = QPushButton("✕ Remove")
        self._btn_rem_pv.setToolTip("Remove the selected PV from the list.")
        self._btn_rem_pv.setEnabled(bool(self._search_pvs))
        row_pv.addWidget(self._btn_add_pv)
        row_pv.addWidget(self._btn_rem_pv)
        b2.addLayout(row_pv)
        lay.addWidget(card2)

        # ══ Step 3 — which spectrum is measured ════════════════════════
        card3, b3 = _step_card(3, "SPECTRUM  —  what I measure")
        self._lbl_spec_base = QLineEdit(self._spec_base_pv)
        self._lbl_spec_base.setReadOnly(True)
        self._lbl_spec_base.setToolTip(
            f"X axis: {self._x_axis_summary()}\nY axis: {self._spec_y_pv}"
        )
        self._lbl_spec_base.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #0D47A1; "
            "border: 1px solid #c3d7f5; border-radius: 3px; background: #fff; padding: 2px;"
        )
        b3.addWidget(self._lbl_spec_base)
        row_sp = QHBoxLayout()
        row_sp.setSpacing(5)
        self._lbl_spec_pair = QLabel(f"X: {self._x_axis_summary()}   /   Y: {self._spec_y_pv}")
        self._lbl_spec_pair.setStyleSheet(
            "font-size: 9px; color: #607D8B; border: none; background: transparent;")
        self._lbl_spec_pair.setWordWrap(True)
        row_sp.addWidget(self._lbl_spec_pair, stretch=1)
        self._btn_spec = QPushButton("Change…")
        # Sized from the text, not a magic number: the old setFixedWidth(62) lost
        # ~16 px to the stylesheet's padding and border and clipped the label to
        # "Chang…" as soon as the display was scaled to 125 %.
        _fit_button(self._btn_spec)
        self._btn_spec.setToolTip(
            "Search CPVA for a spectrum PV. Pick any _X or _Y variant — both axes "
            "are paired automatically. If the _X channel is missing you can build "
            "the wavelength axis from another PV or a CSV file."
        )
        row_sp.addWidget(self._btn_spec, alignment=Qt.AlignmentFlag.AlignBottom)
        b3.addLayout(row_sp)

        # Unit of the X axis. The tab was written for a grating spectrometer and
        # printed "Wavelength [nm]" / "Peak λ" everywhere, but the same panel is
        # used on the SPIDER time domain, whose axis is femtoseconds — a pulse
        # duration announced in nanometres is worse than no axis at all. Guessed
        # from the channel name and editable, because a name can lie.
        row_un = QHBoxLayout()
        row_un.setSpacing(5)
        lbl_un = QLabel("Unit:")
        # Own colour, not the host's: the card is pale blue, and a label that
        # inherits a dark theme's white text disappears into it.
        lbl_un.setStyleSheet(
            "font-size: 11px; color: #111; background: transparent; border: none;")
        row_un.addWidget(lbl_un)
        self._edit_x_unit = QLineEdit(self._x_unit())
        self._edit_x_unit.setMaximumWidth(64)
        self._edit_x_unit.setStyleSheet(
            "font-size: 11px; color: #111; border: 1px solid #bbb; "
            "border-radius: 3px; background: #fff; padding: 1px 3px;")
        self._edit_x_unit.setToolTip(
            "Unit of the horizontal axis of the spectrum graph — nm for a "
            "spectrometer, fs for the SPIDER time domain. It sets the axis title, "
            "the readout, the Peak / FWHM labels and the exported column names.")
        self._edit_x_unit.editingFinished.connect(self._on_x_unit_edited)
        row_un.addWidget(self._edit_x_unit)
        row_un.addStretch(1)
        b3.addLayout(row_un)
        lay.addWidget(card3)

        # ── Mode ───────────────────────────────────────────────────────
        # Built here, put on the panel further down: the order on screen is
        # day → search by → spectrum → selected spectra → mode → analyze.
        g_mode = self._g_mode = QGroupBox("Mode")
        g_mode.setStyleSheet(_GROUP_STYLE)
        row_m = QHBoxLayout(g_mode)
        self._btn_archive   = QPushButton("Archive")
        self._btn_live_mode = QPushButton("Live")
        self._btn_archive.setToolTip("Browse past data: load a day and select spectra from the archive.")
        self._btn_live_mode.setToolTip("Stream the latest spectra in real time and average the last N shots.")
        self._btn_archive.setCheckable(True)
        self._btn_live_mode.setCheckable(True)
        self._btn_archive.setChecked(True)
        # The app-wide stylesheet gives every button its own border and background,
        # which wipes out the style's "pressed in" look — both mode buttons then
        # looked identical and nothing on the panel said which mode was on. The
        # chosen one is now a filled blue button with white text.
        for _b in (self._btn_archive, self._btn_live_mode):
            _b.setStyleSheet(_BTN_MODE)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        self._mode_grp.addButton(self._btn_archive)
        self._mode_grp.addButton(self._btn_live_mode)
        row_m.addWidget(self._btn_archive)
        row_m.addWidget(self._btn_live_mode)

        # ── Live controls ──────────────────────────────────────────────
        self._g_live = QGroupBox("Live")
        self._g_live.setStyleSheet(_GROUP_STYLE)
        live_l = QVBoxLayout(self._g_live)
        row_n = QHBoxLayout()
        row_n.addWidget(QLabel("Average last N:"))
        self._sb_live_n = QSpinBox()
        self._sb_live_n.setRange(1, LIVE_BUF_MAX)
        self._sb_live_n.setValue(DEFAULT_LIVE_N)
        self._sb_live_n.setToolTip(
            "How many of the most-recent live spectra to average together "
            "(the newest shot is always drawn on top in red).\n\n"
            f"Start Live begins with the last {LIVE_HISTORY_S // 60} minutes of "
            f"shots and then adds one poll every {LIVE_INTERVAL_S} s, so right "
            "after starting there are usually fewer than this in the buffer — the "
            "status line says how many are actually being averaged.\n"
            f"Every shot in the buffer is averaged, but at most "
            f"{MAX_INDIVIDUAL_LINES} of them are drawn as faint individual traces."
        )
        row_n.addWidget(self._sb_live_n)
        live_l.addLayout(row_n)
        self._btn_live_start = QPushButton("▶  Start Live")
        self._btn_live_start.setStyleSheet(_BTN_SUCCESS)
        self._btn_live_start.setToolTip(
            "Start/stop streaming live spectra. The bottom graph shows the newest shot "
            "plus the average of the last N spectra."
        )
        live_l.addWidget(self._btn_live_start)
        # (the blinking "live is running" indicator lives in the top status block)
        self._g_live.setVisible(False)

        # (region selection list lives in the collapsible block, not here)

        # ══ Settings — everything that only changes how it is DRAWN ════
        # One coloured, foldable group at the bottom of the panel: the graph
        # options, the comparison curve and the horizontal range. They are not
        # steps of the workflow, so they are out of the way of the steps.
        self._g_settings = _SettingsGroup("Display settings")
        set_l = self._g_settings.body_layout

        # ── Display options ────────────────────────────────────────────
        # One grid, not five separate rows: the three labels line up in a column and
        # the section is several rows shorter. As five independent QHBoxLayouts every
        # combo started at a different x and the block wasted vertical space.
        set_l.addWidget(_sub_label("Graph"))
        g_disp = QWidget()
        disp_l = QVBoxLayout(g_disp)
        disp_l.setContentsMargins(0, 0, 0, 0)
        disp_l.setSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        disp_l.addLayout(grid)

        grid.addWidget(QLabel("Show"), 0, 0)
        self._cmb_method = QComboBox()
        self._cmb_method.addItems(list(_METHODS.keys()))
        self._cmb_method.setToolTip(
            "What the bottom graph draws for each selected time region:\n"
            "• Mean — plain average\n"
            "• Median — robust against outlier shots\n"
            "• Trimmed mean 10% — drops the 10% lowest/highest values\n"
            "• Sigma-clipped mean — drops points beyond 3σ, then averages\n"
            "• Every spectrum — no averaging at all: every single shot measured "
            "in the region is drawn as its own curve\n\n"
            "The active setting is named in the graph title and in every legend "
            "entry — on clean spectra the four averages differ by a fraction of a "
            "percent, so the label is the only way to tell them apart.\n\n"
            f"Every spectrum draws at most {MAX_SINGLE_LINES} curves per region; "
            "above that every n-th shot is drawn and the legend says how many of "
            "how many you are looking at."
        )
        grid.addWidget(self._cmb_method, 0, 1)

        grid.addWidget(QLabel("Colour"), 1, 0)
        self._cmb_color = QComboBox()
        self._cmb_color.addItems(["Selection order", "GDD", "TOD"])
        self._cmb_color.setToolTip(
            "How to colour the averaged spectra:\n"
            "• Selection order — each spectrum a distinct colour (1, 2, 3 …)\n"
            "• GDD / TOD — rainbow scale by the dispersion value (low → high), so "
            "spectra with similar GDD/TOD share a colour. Needs analyzed spectra."
        )
        grid.addWidget(self._cmb_color, 1, 1)

        grid.addWidget(QLabel("Normalize"), 2, 0)
        self._cmb_norm = QComboBox()
        self._cmb_norm.addItems(["None", "Peak", "Area"])
        self._cmb_norm.setToolTip(
            "Scale each spectrum before plotting:\n"
            "• None — raw intensity\n"
            "• Peak — divide by its maximum (peak = 1)\n"
            "• Area — divide by the integrated area (unit area under the curve)"
        )
        grid.addWidget(self._cmb_norm, 2, 1)

        # Variation band (±1σ or percentile) — checkbox doubles as the row label
        self._chk_std = QCheckBox("Variation band")
        self._chk_std.setStyleSheet(_CHK_STYLE)
        self._chk_std.setToolTip("Shade a spread band around each averaged spectrum.")
        grid.addWidget(self._chk_std, 3, 0)
        self._cmb_band = QComboBox()
        self._cmb_band.addItems(["±1σ", "10–90 pct"])
        self._cmb_band.setToolTip(
            "Band type:\n"
            "• ±1σ — one standard deviation (sensitive to outliers)\n"
            "• 10–90 pct — robust percentile band"
        )
        grid.addWidget(self._cmb_band, 3, 1)

        # Smoothing
        self._chk_smooth = QCheckBox("Smooth")
        self._chk_smooth.setStyleSheet(_CHK_STYLE)
        self._chk_smooth.setToolTip("Savitzky–Golay (quadratic) smoothing of the displayed curve.")
        grid.addWidget(self._chk_smooth, 4, 0)
        row_sm = QHBoxLayout()
        row_sm.setSpacing(4)
        row_sm.addWidget(QLabel("window:"))
        self._sb_smooth = QSpinBox()
        self._sb_smooth.setRange(3, 201)
        self._sb_smooth.setSingleStep(2)
        self._sb_smooth.setValue(11)
        row_sm.addWidget(self._sb_smooth)
        row_sm.addStretch(1)
        grid.addLayout(row_sm, 4, 1)

        self._chk_show_energy = QCheckBox("Show search graph")
        self._chk_show_energy.setChecked(True)
        self._chk_show_energy.setToolTip(
            "Uncheck to minimize the top search graph — the spectra graph then fills "
            "the whole window."
        )
        self._chk_show_energy.setStyleSheet(_CHK_STYLE)
        disp_l.addWidget(self._chk_show_energy)
        set_l.addWidget(g_disp)

        # "Every spectrum — pick one" used to be a group box here. It now lives
        # directly under the search graph (_make_shot_bar), because the bar has to
        # line up with that graph's time axis pixel for pixel — a bar in this
        # panel could never say WHERE on the graph the picked shot is.

        # ── X range ────────────────────────────────────────────────────
        # The caption is not fixed: it says "Wavelength range [nm]" on a
        # spectrometer and "Time range [fs]" on the SPIDER time domain
        # (_sync_x_unit_labels writes it from the unit).
        self._lbl_xrange = _sub_label("Spectrum range")
        set_l.addWidget(self._lbl_xrange)
        g_xr = QWidget()
        xr_v = QVBoxLayout(g_xr)
        xr_v.setContentsMargins(0, 0, 0, 0)
        xr_v.setSpacing(4)
        xr_l = QHBoxLayout()
        xr_l.setSpacing(4)
        xr_l.addWidget(QLabel("From:"))
        self._sb_x_min = QSpinBox()
        self._sb_x_min.setRange(-9999, 9999)
        self._sb_x_min.setValue(700)
        # Fixed-ish width plus one trailing stretch, so "To:" sits right next to the
        # first value. Letting both spin boxes take the slack pushed them to opposite
        # ends of the panel with a wide gap in the middle.
        self._sb_x_min.setMaximumWidth(76)
        xr_l.addWidget(self._sb_x_min)
        xr_l.addSpacing(8)
        xr_l.addWidget(QLabel("To:"))
        self._sb_x_max = QSpinBox()
        self._sb_x_max.setRange(-9999, 9999)
        self._sb_x_max.setValue(900)
        self._sb_x_max.setMaximumWidth(76)
        xr_l.addWidget(self._sb_x_max)
        xr_l.addStretch(1)
        xr_v.addLayout(xr_l)
        self._chk_autofit = QCheckBox("Auto-fit range to data on Analyze")
        self._chk_autofit.setChecked(True)
        self._chk_autofit.setStyleSheet(_CHK_STYLE)
        self._chk_autofit.setToolTip(
            "After each Analyze, set From/To to the wavelength span that actually "
            "contains signal. Uncheck to keep your manual values."
        )
        xr_v.addWidget(self._chk_autofit)
        set_l.addWidget(g_xr)
        self._sync_x_unit_labels()      # caption carries the unit, not a fixed "nm"

        # ── Compare two regions ────────────────────────────────────────
        set_l.addWidget(_sub_label("Compare regions"))
        g_cmp = QWidget()
        cmp_l = QVBoxLayout(g_cmp)
        cmp_l.setContentsMargins(0, 0, 0, 0)
        cmp_l.setSpacing(3)
        self._chk_compare = QCheckBox("Show comparison curve")
        self._chk_compare.setStyleSheet(_CHK_STYLE)
        self._chk_compare.setToolTip(
            "Plot the difference (A−B) or ratio (A÷B) of two analyzed spectra."
        )
        cmp_l.addWidget(self._chk_compare)
        row_cmp = QHBoxLayout()
        row_cmp.addWidget(QLabel("A:"))
        self._cmb_cmp_a = QComboBox()
        row_cmp.addWidget(self._cmb_cmp_a, stretch=1)
        row_cmp.addWidget(QLabel("B:"))
        self._cmb_cmp_b = QComboBox()
        row_cmp.addWidget(self._cmb_cmp_b, stretch=1)
        cmp_l.addLayout(row_cmp)
        self._cmb_cmp_mode = QComboBox()
        self._cmb_cmp_mode.addItems(["A − B (difference)", "A ÷ B (ratio)"])
        cmp_l.addWidget(self._cmb_cmp_mode)
        set_l.addWidget(g_cmp)

        # ══ The panel, in the order it is used ═════════════════════════
        # The three numbered steps are already on it. What follows is the list of
        # what was picked, then how it is read (mode), then Analyze, then the two
        # buttons that end the job — and the drawing settings last of all.

        # ── Selected spectra (the list itself) ─────────────────────────
        lay.addWidget(self._make_region_panel(), stretch=1)

        # ── Mode (+ the live controls that belong to it) ───────────────
        lay.addWidget(self._g_mode)
        lay.addWidget(self._g_live)

        # ── Analyze + its progress bar ─────────────────────────────────
        self._btn_analyze = QPushButton("✓  Analyze")
        self._btn_analyze.setEnabled(False)
        self._btn_analyze.setStyleSheet(_BTN_SUCCESS)
        self._btn_analyze.setToolTip(
            "Fetch and average the spectra in every not-yet-analyzed selection, then plot them."
        )
        lay.addWidget(self._btn_analyze)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("Analyzing  %v / %m  (%p%)")
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #b0b0b0; border-radius: 4px; "
            "text-align: center; height: 20px; font-size: 11px; font-weight: 700; "
            "background: #ffffff; color: #111; }"
            "QProgressBar::chunk { background: #2E7D32; border-radius: 3px; }"
        )
        lay.addWidget(self._progress)

        # ── Clear all + Export ─────────────────────────────────────────
        self._btn_clear_regs = QPushButton("Clear all")
        self._btn_clear_regs.setToolTip("Remove all selected spectra from the list.")
        self._btn_export = QPushButton("\U0001F4BE  Export results")
        self._btn_export.setEnabled(False)
        self._btn_export.setToolTip(
            "Export the analyzed spectra to a CSV (details + curves) and/or save the plot image."
        )
        row_end = QHBoxLayout()
        row_end.setSpacing(6)
        row_end.addWidget(self._btn_clear_regs)
        row_end.addWidget(self._btn_export, stretch=1)
        lay.addLayout(row_end)

        # ── Display settings, last ─────────────────────────────────────
        lay.addWidget(self._g_settings)

        return sb

    # ── The shot bar: "Every spectrum — pick one", under the search graph ─────
    # A bundle of a thousand faint curves says nothing about WHICH shot is which.
    # This bar picks one of them and the spectra graph paints it bold.
    #
    # Two rules make it what it is:
    #   1. The bar and the graph share one X. The same place on the graph is the
    #      same place on the bar — handle centre and data point on the SAME pixel.
    #      That is why the bar's travel is pinned to the plot box (_pin_shot_bar)
    #      and not to the canvas or to the widget.
    #   2. The bar can only stand on a real measurement. Where no shot was taken
    #      the handle cannot go: it sticks at the last shot before an empty
    #      stretch and reappears at the first shot after it (_on_shot_bar_moved).
    #
    # Only shown while the display is set to Every spectrum — there is nothing to
    # step through when one averaged curve is on screen.

    def _make_shot_bar(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 1, 0, 3)
        v.setSpacing(1)

        # Row 1 — the bar, alone. Its left/right margins are re-pinned to the
        # plot box on every draw of the search graph, so anything else in this
        # row would push the bar's ends off the graph's axis.
        self._shot_bar_row = QWidget()
        row = QHBoxLayout(self._shot_bar_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._sl_shot = _ShotBar(lambda s: self._step_single(s),
                                 lambda s: self._page_single(s))
        self._sl_shot.setStyleSheet(_TIMEBAR_STYLE)
        self._sl_shot.setRange(0, _SHOT_BAR_MAX)
        self._sl_shot.setEnabled(False)
        self._sl_shot.setToolTip(
            "Pick one measured shot out of the bundle. The bar lines up with the "
            "graph above it, so the handle stands exactly under the shot it is "
            "on, and it can only stop where a shot was actually taken."
        )
        self._sl_shot.valueChanged.connect(self._on_shot_bar_moved)
        row.addWidget(self._sl_shot, stretch=1)
        v.addWidget(self._shot_bar_row)

        # Row 2 — the buttons and the caption. Plain widgets, so this row keeps
        # its own margins and is free to be as wide as the panel.
        row2 = QHBoxLayout()
        row2.setContentsMargins(6, 0, 6, 0)
        row2.setSpacing(6)
        self._btn_single_prev = QPushButton("◀")
        self._btn_single_next = QPushButton("▶")
        for _btn, _step, _tip in ((self._btn_single_prev, -1, "One shot earlier"),
                                  (self._btn_single_next, +1, "One shot later")):
            _btn.setFixedWidth(30)
            _btn.setFixedHeight(20)
            _btn.setToolTip(_tip)
            _btn.setEnabled(False)
            _btn.clicked.connect(lambda _=False, s=_step: self._step_single(s))
            _btn.setStyleSheet(
                "QPushButton { color: #111; font-weight: 700; background: #f4f4f4; "
                "border: 1px solid #b4b4b4; border-radius: 3px; }"
                "QPushButton:hover { background: #e8f0fe; }"
                "QPushButton:disabled { color: #aaa; background: #efefef; "
                "border-color: #d4d4d4; }"
            )
        row2.addWidget(self._btn_single_prev)
        row2.addWidget(self._btn_single_next)
        self._chk_single_hl = QCheckBox("Highlight")
        self._chk_single_hl.setChecked(True)
        self._chk_single_hl.setStyleSheet(_CHK_STYLE)
        self._chk_single_hl.setToolTip(
            "Draw the picked shot bold on top of the bundle in the spectra graph, "
            "and mark it in the search graph. Uncheck to see the bundle on its own."
        )
        self._chk_single_hl.toggled.connect(self._on_single_hl_toggled)
        row2.addWidget(self._chk_single_hl)
        self._lbl_single = QLabel("Nothing to step through yet — analyze a spectrum.")
        self._lbl_single.setStyleSheet("font-size: 11px; color: #222;")
        row2.addWidget(self._lbl_single, stretch=1)
        v.addLayout(row2)

        box.setVisible(False)
        self._shot_bar_box = box
        return box

    def _make_canvas_panel(self, suffix: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        fig = Figure(tight_layout={"pad": 0.3})
        ax  = fig.add_subplot(111)
        if suffix == "bot":
            # Permanent colour-bar slot — created once, then only shown or hidden.
            # set_in_layout(False) keeps tight_layout from warning about an axes with
            # no subplotspec; the plot's own room is steered by _set_cbar_space().
            cax = fig.add_axes(_CBAR_BOX)
            cax.set_in_layout(False)
            cax.set_visible(False)
            self._cax_bot = cax
        canvas = FigureCanvasQTAgg(fig)
        canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Matplotlib tints toolbar icons from the palette: on a dark inherited
        # palette it makes them white (invisible on our white bar). Force a light
        # palette on the parent BEFORE building the toolbar so icons stay black.
        pal = w.palette()
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.Button):
            pal.setColor(role, QColor("white"))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
                     QPalette.ColorRole.Text):
            pal.setColor(role, QColor("black"))
        w.setPalette(pal)
        tb = _CustomToolbar(canvas, w)
        tb.setPalette(pal)
        tb.setStyleSheet(_TB_STYLE)
        for action in tb.actions():
            hint = _TB_HINTS.get(action.text())
            if hint:
                action.setToolTip(hint)
        # Remove "Customize" button (axis/curve editor) — moved to right-click on the axis
        for action in tb.actions():
            if action.text() == "Customize":
                tb.removeAction(action)
                break
        v.addWidget(tb)
        v.addWidget(canvas, stretch=1)
        setattr(self, f"_fig_{suffix}",    fig)
        setattr(self, f"_ax_{suffix}",     ax)
        setattr(self, f"_canvas_{suffix}", canvas)
        setattr(self, f"_tb_{suffix}",     tb)
        # The shot bar belongs to the search graph, so it is built INSIDE that
        # graph's panel: it has to sit right under the plot box with nothing
        # between them, and it has to follow the panel wherever the panel goes.
        if suffix == "top":
            v.addWidget(self._make_shot_bar())

        # Double-click inside a day on the search graph → mark that whole day.
        # Connected here, once: _install_span runs on every redraw, so hooking it
        # there would stack a fresh handler each time. Left double-click DOES reach
        # matplotlib (only right-click does not, see the note below).
        if suffix == "top":
            canvas.mpl_connect("button_press_event", self._on_top_dblclick)

        # Right-click inside the axis → "Edit axis…" context menu.
        # Uses Qt CustomContextMenu (reliable); mpl button_press_event misses
        # right-click because Qt intercepts it before matplotlib sees it.
        canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _on_context_menu(pos, _fig=fig, _tb=tb, _canvas=canvas):
            # Convert Qt coords (origin top-left, y↓, logical px) → matplotlib display
            # coords (origin bottom-left, y↑, physical px). Multiplying by the device
            # pixel ratio is what makes this work on a display-scaled monitor — without it
            # get_window_extent().contains() fails at 125/150 % and no menu appears.
            ratio  = getattr(_canvas, "device_pixel_ratio", 1) or 1
            x_disp = pos.x() * ratio
            y_disp = _canvas.figure.bbox.height - pos.y() * ratio
            axes = _fig.get_axes()
            # Pick the axis under the cursor; fall back to the first axis so a right-click
            # on the labels / margins still opens the menu instead of silently doing nothing.
            target = next(
                (ax for ax in axes if ax.get_window_extent().contains(x_disp, y_disp)),
                axes[0] if axes else None,
            )
            if target is not None:
                ax = target
                menu = QMenu(_canvas)

                act_lim    = menu.addAction("Axis limits…")
                act_labels = menu.addAction("Axis labels…")
                menu.addSeparator()

                # Grid
                major_lines = ax.get_xgridlines()
                major_on = bool(major_lines) and major_lines[0].get_visible()
                minor_on = getattr(ax, "_ctx_minor_grid", False)
                act_major = menu.addAction("Major grid")
                act_major.setCheckable(True)
                act_major.setChecked(major_on)
                act_minor = menu.addAction("Minor grid")
                act_minor.setCheckable(True)
                act_minor.setChecked(minor_on)
                menu.addSeparator()

                # Y scale
                y_menu  = menu.addMenu("Y scale")
                act_ylin = y_menu.addAction("Linear")
                act_ylin.setCheckable(True)
                act_ylog = y_menu.addAction("Logarithmic")
                act_ylog.setCheckable(True)
                if ax.get_yscale() == "log":
                    act_ylog.setChecked(True)
                else:
                    act_ylin.setChecked(True)
                menu.addSeparator()

                act_reset = menu.addAction("Reset view")

                chosen = menu.exec(_canvas.mapToGlobal(pos))

                if chosen is act_lim:
                    _AxisLimitsDialog(ax, _canvas, _canvas).exec()
                    # Typed limits are not a pan/zoom gesture, so the tracker
                    # ignores them — file them here or the next redraw drops them.
                    self._remember_axis_limits(ax)
                elif chosen is act_labels:
                    _AxisLabelsDialog(ax, _canvas, _canvas).exec()
                elif chosen is act_major:
                    ax.grid(not major_on, which="major", alpha=0.25)
                    _canvas.draw_idle()
                elif chosen is act_minor:
                    new_minor = not minor_on
                    ax._ctx_minor_grid = new_minor
                    if new_minor:
                        ax.minorticks_on()
                        ax.grid(True, which="minor", alpha=0.10, linestyle=":")
                    else:
                        ax.grid(False, which="minor")
                        ax.minorticks_off()
                    _canvas.draw_idle()
                elif chosen is act_ylin:
                    try:
                        ax.set_yscale("linear")
                        _canvas.draw_idle()
                    except Exception:
                        pass
                elif chosen is act_ylog:
                    try:
                        ax.set_yscale("log")
                        _canvas.draw_idle()
                    except Exception:
                        pass
                elif chosen is act_reset:
                    # home() called in code does not fire the toolbar action, so
                    # the saved zoom has to be dropped here too — otherwise the
                    # next redraw puts the view straight back.
                    self._forget_axis_limits(ax)
                    _tb.home()
                return

        canvas.customContextMenuRequested.connect(_on_context_menu)

        if suffix == "top":
            # Top plot has multiple Y-axes (one per PV); it needs a cursor that
            # reads each trace's real value on its own axis.
            self._install_top_cursor(canvas, fig, ax)
        else:
            self._install_cursor(canvas, fig, ax, x_is_time=False)

        return w

    def _install_bot_cursor_artists(self):
        """(Re)create the spectra-plot crosshair + floating value labels.

        Called after every ax.clear() on the spectra plot (in _redraw_spectra and
        _draw_bot_empty), since clear() detaches the old artists from the figure.
        Drawing a detached Text raises 'NoneType has no attribute dpi', so the
        cursor code always reads the live artists from self._bot_cursor_artists."""
        from matplotlib.transforms import blended_transform_factory as _btf
        ax = self._ax_bot
        vline = ax.axvline(color="#888", linewidth=0.8, linestyle="--", visible=False)
        hline = ax.axhline(color="#888", linewidth=0.8, linestyle="--", visible=False)
        # Y-value annotation — floats along left axis edge
        y_ann = ax.text(
            0.0, 0.5, "",
            transform=_btf(ax.transAxes, ax.transData),
            ha="right", va="center", fontsize=9,
            color="#1565C0", zorder=10, visible=False, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.2", fc="white",
                      ec="#1565C0", alpha=0.88, linewidth=0.7),
        )
        # X-value annotation — floats along bottom axis edge
        x_ann = ax.text(
            0.5, 0.0, "",
            transform=_btf(ax.transData, ax.transAxes),
            ha="center", va="top", fontsize=9,
            color="#555", zorder=10, visible=False, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.2", fc="white",
                      ec="#888", alpha=0.88, linewidth=0.7),
        )
        self._bot_cursor_artists = {"vline": vline, "hline": hline,
                                    "y_ann": y_ann, "x_ann": x_ann}
        # The bold "this one" curve is an overlay on the same canvas and dies with
        # the same ax.clear(), so it is recreated here rather than in a second hook
        # somebody would forget to call.
        self._install_single_hl_artists()
        # clear() also replaced the axes' callback registry — the pan/zoom memory
        # has to be reconnected to the new one or it is dead after the first redraw.
        self._track_bot_zoom()

    def _install_cursor(self, canvas, fig, ax, x_is_time: bool):
        """Blitted crosshair with Y-axis and X-axis floating annotations inside the graph.

        Artists live in self._bot_cursor_artists and are recreated after each
        ax.clear(); the callbacks below always read them fresh so a queued redraw
        never touches an orphaned artist."""
        import matplotlib.dates as _mdates

        _state = {"bg": None, "pending": False, "last_event": None}
        self._install_bot_cursor_artists()

        def _artists():
            ca = getattr(self, "_bot_cursor_artists", None)
            return [ca["vline"], ca["hline"], ca["y_ann"], ca["x_ann"]] if ca else []

        def _hide_all():
            for a in _artists():
                a.set_visible(False)

        def _blit():
            """Restore the captured background and repaint the overlay on top.

            One place, used by the cursor, by the leave handler and by the
            "Every spectrum" shot bar — each of them used to restore the
            background and blit on its own, so whichever ran last wiped the
            other's artists off the graph."""
            bg = _state["bg"]
            if bg is None:
                canvas.draw_idle()
                return
            canvas.restore_region(bg)
            for artist in _artists() + self._single_hl_list():
                if artist.get_visible() and artist.axes is not None:
                    artist.axes.draw_artist(artist)
            canvas.blit(fig.bbox)

        self._bot_blit_fn = _blit

        def _on_draw(_evt):
            _hide_all()
            _state["bg"] = canvas.copy_from_bbox(fig.bbox)
            # The bold "this one" curve is animated, so the full draw that just
            # finished left it out — put it straight back on the fresh background.
            try:
                self._update_single_highlight(blit=False)
            except Exception:
                pass
            _blit()

        def _fmt_y(y):
            abs_y = abs(y)
            if y == 0 or (1e-3 <= abs_y < 1e6):
                return f"{y:.5g}"
            return f"{y:.4e}"

        def _fmt_x(x):
            if x_is_time:
                try:
                    from zoneinfo import ZoneInfo
                    dt = _mdates.num2date(x, tz=ZoneInfo("Europe/Prague"))
                    return dt.strftime("%H:%M:%S")
                except Exception:
                    return f"{x:.4g}"
            if self._x_is_samples():
                return f"{x:.4g}"
            return f"{x:.4g} {self._x_unit()}".strip()

        def _process():
            _state["pending"] = False
            evt = _state["last_event"]
            bg  = _state["bg"]
            ca  = getattr(self, "_bot_cursor_artists", None)

            if evt is None or evt.inaxes is None or not ca:
                if bg:
                    _hide_all()
                    _blit()
                return

            x, y = evt.xdata, evt.ydata
            if x is None or y is None:
                return

            ca["vline"].set_xdata([x, x]); ca["vline"].set_visible(True)
            ca["hline"].set_ydata([y, y]); ca["hline"].set_visible(True)

            ca["y_ann"].set_position((0.0, y))
            ca["y_ann"].set_text(f" {_fmt_y(y)} ")
            ca["y_ann"].set_visible(True)

            ca["x_ann"].set_position((x, 0.0))
            ca["x_ann"].set_text(f" {_fmt_x(x)} ")
            ca["x_ann"].set_visible(True)

            _blit()

        def _on_motion(evt):
            _state["last_event"] = evt
            if not _state["pending"]:
                _state["pending"] = True
                from PySide6.QtCore import QTimer
                QTimer.singleShot(16, _process)

        def _on_leave(_evt):
            # Only the crosshair goes away — the bold curve is not the mouse's.
            _hide_all()
            if _state["bg"]:
                _blit()

        canvas.mpl_connect("draw_event",          _on_draw)
        canvas.mpl_connect("motion_notify_event", _on_motion)
        canvas.mpl_connect("axes_leave_event",    _on_leave)

    def _install_top_cursor_artists(self):
        """(Re)create the crosshair + per-trace value labels for the energy plot.

        Called at the end of every _draw_energy, after the twin axes exist, since
        ax.clear() wipes the old artists. Each trace gets a coloured dot and a
        value label anchored on its own Y-axis edge (left or right)."""
        from matplotlib.transforms import blended_transform_factory as _btf
        ax = self._ax_top
        vline = ax.axvline(color="#888", linewidth=0.8, linestyle="--",
                           visible=False, zorder=9)
        x_ann = ax.text(
            0.5, 0.0, "", transform=_btf(ax.transData, ax.transAxes),
            ha="center", va="top", fontsize=9, color="#333", zorder=12,
            visible=False, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#888",
                      alpha=0.9, linewidth=0.7),
        )
        for cs in self._top_cursor_series:
            a = cs["axis"]
            dot, = a.plot([], [], "o", ms=6, color=cs["color"],
                          visible=False, zorder=11)
            on_left = cs["side"] == "left"
            xpos = 0.0 if on_left else 1.0
            ha   = "right" if on_left else "left"
            lbl = a.text(
                xpos, 0.5, "", transform=_btf(a.transAxes, a.transData),
                ha=ha, va="center", fontsize=9, color="white", zorder=12,
                visible=False, clip_on=False,
                bbox=dict(boxstyle="round,pad=0.2", fc=cs["color"],
                          ec=cs["color"], alpha=0.95, linewidth=0.7),
            )
            cs["dot"] = dot
            cs["value_label"] = lbl
        self._top_cursor_artists = {"vline": vline, "x_ann": x_ann}
        # The shot bar's marker is an overlay on the same axes and dies with the
        # same ax.clear(), so it is recreated here rather than in a second hook
        # somebody would forget to call.
        self._install_top_marker_artists()
        # clear() also replaced the axes' callback registry: without this the
        # zoom memory and the shot bar's follow-the-axis wiring are both dead
        # after the first redraw.
        self._track_top_zoom()

    def _install_top_marker_artists(self):
        """(Re)create the "the bar is here" marker on the search graph.

        animated=True keeps both artists out of every full draw, so the blitted
        background stays clean and _blit_top() is the only thing that paints them
        — an artist baked into the background would leave a ghost behind at every
        position the bar passed through."""
        ax = self._ax_top
        line = ax.axvline(color="#111111", linewidth=1.6, visible=False,
                          animated=True, zorder=10)
        # Inside the plot box, hanging from the top edge — NOT above it: the search
        # graph carries a title there ("Drag to select time region(s)…") and a tag
        # sitting on top of it would cover it.
        tag = ax.text(
            0.5, 0.995, "", transform=blended_transform_factory(ax.transData,
                                                                ax.transAxes),
            ha="center", va="top", fontsize=9, color="#111111", zorder=13,
            visible=False, animated=True, clip_on=False,
            bbox=dict(boxstyle="round,pad=0.25", fc="#ffffff", ec="#555555",
                      alpha=0.95, linewidth=1.6),
        )
        self._top_marker = {"line": line, "tag": tag}

    def _top_marker_list(self) -> list:
        """The marker's artists, or an empty list before the graph exists."""
        m = getattr(self, "_top_marker", None)
        return [m["line"], m["tag"]] if m else []

    def _blit_top(self):
        """Repaint the search graph's overlay only (crosshair + shot marker).

        Falls back to a full draw until the cursor has captured a background."""
        fn = getattr(self, "_top_blit_fn", None)
        if fn is not None:
            try:
                fn()
                return
            except Exception:
                pass
        canvas = getattr(self, "_canvas_top", None)
        if canvas is not None:
            canvas.draw_idle()

    def _update_top_marker(self, blit: bool = True):
        """Point the marker at the shot the bar is on, or hide it.

        The line sits at the shot's own place on the compressed time axis, so it
        is on the same pixel as the bar's handle below it."""
        arts = self._top_marker_list()
        if not arts:
            return
        line, tag = arts
        r = it = None
        chk = getattr(self, "_chk_single_hl", None)
        if self._is_single() and chk is not None and chk.isChecked():
            r, _k, it = self._single_current()
        tmap = self._tmap
        x = None
        if r is not None and it is not None and it.get("ts") and tmap:
            x = tmap.to_x(int(it["ts"]))
        if x is None:
            for a in arts:
                a.set_visible(False)
        else:
            line.set_xdata([x, x])
            line.set_visible(True)
            # The border carries the spectrum's own colour, so the marker says
            # which selection the shot came out of. Taken from the region, never
            # from its place in the list.
            colors = getattr(self, "_region_colors_cache", None) or {}
            col = colors.get(r["id"], r.get("color", "#555555"))
            tag.set_position((x, 0.995))
            tag.set_text(f" {_fmt_hms(int(it['ts']))} ")
            tag.get_bbox_patch().set_edgecolor(col)
            tag.set_visible(True)
            line.set_color(col)
        if blit:
            self._blit_top()

    def _install_top_cursor(self, canvas, fig, ax):
        """Blitted crosshair for the energy plot: one vertical line + a time label
        at the bottom, plus each trace's value read off its own Y-axis at the
        cursor's X (zero-order hold, matching the steps-post lines)."""
        import matplotlib.dates as _mdates
        _state = {"bg": None, "pending": False, "last_event": None}

        def _artists():
            arts = []
            ca = getattr(self, "_top_cursor_artists", None)
            if ca:
                arts += [ca["vline"], ca["x_ann"]]
            for cs in self._top_cursor_series:
                if "dot" in cs:
                    arts += [cs["dot"], cs["value_label"]]
            return arts

        def _hide_all():
            # Only the crosshair — the shot marker is not the mouse's.
            for a in _artists():
                a.set_visible(False)

        def _blit():
            """Restore the captured background and repaint the overlay on top.

            One place, used by the cursor, by the leave handler and by the shot
            bar: each of them restoring the background on its own means whichever
            ran last wipes the other's artists off the graph."""
            bg = _state["bg"]
            if bg is None:
                canvas.draw_idle()
                return
            canvas.restore_region(bg)
            for a in _artists() + self._top_marker_list():
                if a.get_visible() and a.axes is not None:
                    a.axes.draw_artist(a)
            canvas.blit(fig.bbox)

        self._top_blit_fn = _blit

        def _on_draw(_evt):
            _hide_all()
            _state["bg"] = canvas.copy_from_bbox(fig.bbox)
            # The marker is animated, so the full draw that just finished left it
            # out — put it straight back on the fresh background.
            try:
                self._update_top_marker(blit=False)
            except Exception:
                pass
            _blit()
            # tight_layout has settled, so this is the moment the plot box's
            # pixels are final and the bar below can be lined up with it.
            self._schedule_shot_bar_pin()

        def _fmt_v(v):
            av = abs(v)
            if v == 0 or (1e-3 <= av < 1e6):
                return f"{v:.5g}"
            return f"{v:.4e}"

        def _fmt_time(x):
            # x is a position on the compressed axis, so the real instant has to
            # come from the map — and it must carry the date, because the cursor
            # can now be standing on any of several days.
            tmap = self._tmap
            if not tmap:
                return f"{x:.4g}"
            try:
                dt = _ns_to_dt(tmap.from_x(x))
                return (dt.strftime("%H:%M:%S") if tmap.is_identity()
                        else dt.strftime("%d.%m. %H:%M:%S"))
            except Exception:
                return f"{x:.4g}"

        def _value_at(cs, x):
            times, vals = cs["times"], cs["vals"]
            if len(times) == 0:
                return None
            idx = int(np.searchsorted(times, x, side="right")) - 1
            if idx < 0:
                return None
            return float(vals[idx])

        def _process():
            _state["pending"] = False
            evt = _state["last_event"]
            bg  = _state["bg"]
            if evt is None or evt.inaxes is None:
                if bg:
                    _hide_all()
                    _blit()
                return
            x = evt.xdata
            ca = getattr(self, "_top_cursor_artists", None)
            if x is None or not ca:
                return
            ca["vline"].set_xdata([x, x]); ca["vline"].set_visible(True)
            ca["x_ann"].set_position((x, 0.0))
            ca["x_ann"].set_text(f" {_fmt_time(x)} ")
            ca["x_ann"].set_visible(True)
            for cs in self._top_cursor_series:
                v = _value_at(cs, x)
                if v is None:
                    cs["dot"].set_visible(False)
                    cs["value_label"].set_visible(False)
                    continue
                cs["dot"].set_data([x], [v]); cs["dot"].set_visible(True)
                xpos = 0.0 if cs["side"] == "left" else 1.0
                cs["value_label"].set_position((xpos, v))
                cs["value_label"].set_text(f" {_fmt_v(v)} ")
                cs["value_label"].set_visible(True)
            _blit()

        def _on_motion(evt):
            _state["last_event"] = evt
            if not _state["pending"]:
                _state["pending"] = True
                from PySide6.QtCore import QTimer
                QTimer.singleShot(16, _process)

        def _on_leave(_evt):
            _hide_all()
            if _state["bg"]:
                _blit()

        canvas.mpl_connect("draw_event",          _on_draw)
        canvas.mpl_connect("motion_notify_event", _on_motion)
        canvas.mpl_connect("axes_leave_event",    _on_leave)

    def _make_region_panel(self) -> QWidget:
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        lbl = QLabel("Selected spectra")
        lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        head.addWidget(lbl, stretch=1)
        self._btn_expand_all = QPushButton("⤢ Expand all")
        self._btn_expand_all.setToolTip("Expand all spectra to show their details (or collapse them all)")
        self._btn_expand_all.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 2px 6px; border: 1px solid #c8c8c8; "
            "border-radius: 3px; background: #f4f4f4; }"
            "QPushButton:hover { background: #e8f0fe; }"
            "QPushButton:disabled { color: #aaa; }"
        )
        self._btn_expand_all.clicked.connect(self._toggle_all_expanded)
        self._btn_expand_all.setEnabled(False)
        head.addWidget(self._btn_expand_all)
        v.addLayout(head)

        # The region list is a plain framed container (NOT its own scroll area):
        # a scroll-inside-scroll behaves unpredictably on a short window. The whole
        # sidebar lives in one outer QScrollArea, so the list just grows naturally
        # and the single outer scrollbar handles overflow.
        self._regions_w = QFrame()
        self._regions_w.setObjectName("regionsBox")
        # Scope to the object name so the border does NOT cascade onto the child
        # region-row QFrames inside it.
        self._regions_w.setStyleSheet(
            "QFrame#regionsBox { border: 1px solid #b0b0b0; border-radius: 4px; background: white; }"
        )
        self._regions_lay = QVBoxLayout(self._regions_w)
        self._regions_lay.setContentsMargins(0, 0, 0, 0)
        self._regions_lay.setSpacing(0)
        self._regions_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.addWidget(self._regions_w)
        # Analyze, the progress bar, Clear all and Export are NOT part of this
        # panel any more — they are built by _make_sidebar and sit below Mode, so
        # the panel reads list → mode → analyze → finish.
        return col

    def _make_graphs(self) -> QWidget:
        # vertical splitter [SBW4 energy (top) | spectra (bottom)]
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._top_container = self._make_canvas_panel("top")
        splitter.addWidget(self._top_container)
        # Kept as an attribute because focus mode hands this very widget to a
        # frameless window and back — the same widget, never a copy, so the live
        # refresh keeps painting into it with no extra code.
        self._bot_container = self._make_canvas_panel("bot")
        splitter.addWidget(self._bot_container)
        splitter.setSizes(list(_SPLIT_NO_CURVES))
        self._splitter = splitter

        self._draw_top_empty()
        self._draw_bot_empty()
        self._rebuild_regions_ui()
        return splitter

    # ── Focus mode (F11): the spectra graph alone, no window header ───────────
    # Deliberately NOT gated on live mode — F11 works in Archive too. In Live mode
    # the graph simply keeps refreshing, because focus mode re-parents the very same
    # canvas widget instead of building a second one, so the poll timer needs no
    # changes at all.

    def toggle_focus_mode(self):
        """Public API — called by F11 (the suite forwards the key here)."""
        if self._focus_toggling:
            return          # a window closing mid-teardown must not re-trigger us
        self._focus_toggling = True
        try:
            if self._focus_win is None:
                self._focus_enter()
            else:
                self._focus_leave()
        finally:
            self._focus_toggling = False

    def _focus_enter(self):
        content = self._bot_container
        if content is None:
            return
        self._focus_win = _SpectraFocusWindow(content, self)
        # "Just the graph": the matplotlib toolbar goes away, which also frees a
        # plain drag on the canvas to move the window.
        self._tb_bot.hide()

        rect = self._load_focus_rect()
        if rect is None:
            scr = self.screen() or QGuiApplication.primaryScreen()
            rect = scr.availableGeometry()
        self._focus_win.setGeometry(rect)
        self._focus_win.show()
        self._focus_win.raise_()
        self._focus_win.activateWindow()

        # Park the main window on the taskbar so it is not sitting half-covered
        # behind the graph. Restoring it from the taskbar leaves focus mode.
        win = self.window()
        self._focus_was_maximized = win.isMaximized() or win.isFullScreen()
        self._focus_saw_minimized = False
        win.installEventFilter(self)
        win.showMinimized()
        self._set_status("Focus mode — Esc or F11 to come back.")

    def _focus_leave(self):
        fw, self._focus_win = self._focus_win, None
        if fw is None:
            return
        self._save_focus_rect(fw.geometry())
        fw._leaving = True
        content = fw.release_content()
        fw.close()
        fw.deleteLater()
        # Back into the splitter, below the search graph, and re-apply the ratio.
        self._splitter.insertWidget(1, content)
        self._tb_bot.show()
        self._update_top_visibility()

        win = self.window()
        win.removeEventFilter(self)
        if self._focus_was_maximized:
            win.showMaximized()
        else:
            win.showNormal()
        win.raise_()
        win.activateWindow()
        self._canvas_bot.draw_idle()
        self._set_status("Ready.")

    def eventFilter(self, obj, ev):
        """Restoring the main window from the taskbar leaves focus mode.

        Two steps on purpose. Watching only for "not minimized" fired on the
        showMinimized() call in _focus_enter itself — the state change arrives
        before the window is actually minimized — so focus mode ended the instant
        it started. The window must be seen minimized FIRST."""
        if (self._focus_win is not None
                and ev.type() == ev.Type.WindowStateChange
                and obj is self.window()):
            if obj.isMinimized():
                self._focus_saw_minimized = True
            elif self._focus_saw_minimized:
                QTimer.singleShot(0, self._focus_leave)
        return super().eventFilter(obj, ev)

    def _load_focus_rect(self):
        """Saved focus geometry, unless it lands on a monitor that is now gone."""
        try:
            with open(_layout_config_path(), encoding="utf-8") as f:
                r = json.load(f).get("focus_window")
            rect = QRect(int(r[0]), int(r[1]), int(r[2]), int(r[3]))
        except Exception:
            return None
        if rect.width() < 200 or rect.height() < 150:
            return None
        for scr in QGuiApplication.screens():
            if scr.availableGeometry().intersects(rect):
                return rect
        return None

    def _save_focus_rect(self, rect):
        try:
            p = _layout_config_path()
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data["focus_window"] = [rect.x(), rect.y(), rect.width(), rect.height()]
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self._last_saved_layout = {}     # force the next _save_layout to write
        except Exception:
            pass

    def _update_top_visibility(self):
        """Energy graph shows only in archive mode and when the user wants it;
        otherwise the spectra graph fills the whole area.

        Restores the ratio the user last dragged. It used to reset to a hard-coded
        440/320 here, which threw that ratio away on every Archive/Live click and on
        every "Show search graph" tick — the graph appeared to resize by itself."""
        if getattr(self, "_splitter", None) is None:
            return                      # called while the panel is still being built
        show = self._btn_archive.isChecked() and self._chk_show_energy.isChecked()
        self._top_container.setVisible(show)
        if show:
            self._splitter.setSizes(self._wanted_split())
        else:
            self._splitter.setSizes([0, max(1, self.height())])

    def _wanted_split(self) -> list:
        """Heights for [search graph, spectra graph].

        A dragged divider wins for the rest of the session. Until then the search
        graph is the big one — that is where the work happens while regions are
        being picked — and the spectra graph takes two thirds as soon as there is
        a curve in it."""
        if self._split_user_set:
            return list(self._split_ratio)
        drawn = any(r.get("analyzed") for r in self._regions)
        return list(_SPLIT_CURVES if drawn else _SPLIT_NO_CURVES)

    def _on_splitter_moved(self, *_):
        """Remember what the user dragged, then persist it."""
        sizes = self._splitter.sizes()
        if len(sizes) == 2 and sizes[0] > 0:
            self._split_ratio = sizes
            self._split_user_set = True
        self._save_layout()

    # ── Signal wiring ─────────────────────────────────────────────────────────
    def _connect_signals(self):
        self._btn_pick_day.clicked.connect(self._pick_day)
        self._btn_stop.clicked.connect(self.cancel_scan)
        self._btn_archive.clicked.connect(lambda: self._set_live_mode(False))
        self._btn_live_mode.clicked.connect(lambda: self._set_live_mode(True))
        self._btn_live_start.clicked.connect(self._toggle_live)
        self._btn_analyze.clicked.connect(self._run_analysis)
        self._btn_clear_regs.clicked.connect(self._clear_regions)
        self._btn_export.clicked.connect(self._export)
        self._cmb_norm.currentIndexChanged.connect(self._redraw_spectra)
        self._chk_std.stateChanged.connect(self._redraw_spectra)
        self._cmb_band.currentIndexChanged.connect(self._redraw_spectra)
        self._chk_smooth.stateChanged.connect(self._redraw_spectra)
        self._sb_smooth.valueChanged.connect(self._redraw_spectra)
        self._chk_compare.stateChanged.connect(self._redraw_spectra)
        self._cmb_cmp_a.currentIndexChanged.connect(self._redraw_spectra)
        self._cmb_cmp_b.currentIndexChanged.connect(self._redraw_spectra)
        self._cmb_cmp_mode.currentIndexChanged.connect(self._redraw_spectra)
        self._chk_show_energy.toggled.connect(self._update_top_visibility)
        self._cmb_method.currentIndexChanged.connect(self._on_method_changed)
        self._sb_x_min.valueChanged.connect(self._on_x_range_edited)
        self._sb_x_max.valueChanged.connect(self._on_x_range_edited)
        self._sb_live_n.valueChanged.connect(self._redraw_spectra)
        self._tbl_pvs.itemSelectionChanged.connect(self._on_search_pv_changed)
        self._tbl_pvs.itemChanged.connect(self._on_pv_item_changed)
        self._btn_add_pv.clicked.connect(self._open_add_pv_dialog)
        self._btn_rem_pv.clicked.connect(self._remove_selected_pv)
        self._btn_spec.clicked.connect(self._change_spec_pv)
        self._cmb_preset.currentIndexChanged.connect(self._on_preset_combo_changed)
        self._btn_preset_add.clicked.connect(self._preset_add)
        self._btn_preset_ren.clicked.connect(self._preset_rename)
        self._btn_preset_del.clicked.connect(self._preset_delete)
        self._edit_pv_search.textEdited.connect(self._on_inline_search)
        self._lst_pv_search.itemClicked.connect(self._on_inline_result_clicked)
        self._cmb_color.currentIndexChanged.connect(self._on_color_mode_changed)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._tb_top.subplot_params_changed.connect(self._save_layout)
        self._tb_bot.subplot_params_changed.connect(self._save_layout)

    @staticmethod
    def _measured_y_range(ax):
        """Lowest and highest value actually drawn on ax, or None.

        Matplotlib's own scaling is not trustworthy on this graph. It keeps a
        running box of "where the data is" and skips any artist it cannot place
        (here: thousands of spectra handed over as one bundle). When that skip
        happens the only thing left in the box is the crosshair's horizontal line
        at zero, so the graph is scaled to the empty-graph ±0.05 and every
        spectrum is cut off just above the baseline — the reported bug: real peaks
        at 1.0, the axis stopping at 0.05.

        So the range is measured here from the curves themselves. Only artists
        drawn in real data coordinates count: the crosshair lines and the value
        tags are pinned to the frame, not to the data, and must not be measured.
        """
        lo, hi = np.inf, -np.inf

        def _grow(vals):
            nonlocal lo, hi
            v = np.asarray(vals, dtype=float).ravel()
            v = v[np.isfinite(v)]
            if v.size:
                lo = min(lo, float(v.min()))
                hi = max(hi, float(v.max()))

        def _in_data_coords(artist):
            try:
                return artist.get_transform() == ax.transData
            except Exception:
                return False

        for coll in list(ax.collections):
            if not _in_data_coords(coll):
                continue
            for p in coll.get_paths():
                if p.vertices.size:
                    _grow(p.vertices[:, 1])
        for ln in list(ax.lines):
            if _in_data_coords(ln):
                _grow(ln.get_ydata())
        if not np.isfinite(lo) or not np.isfinite(hi):
            return None
        return lo, hi

    def _drawn_x_span(self):
        """Lowest and highest X the curves on the graph could cover, or None.

        The whole axis of every visible spectrum, BEFORE the From/To range cuts
        it down — that is what the range has to be compared against."""
        lo, hi = np.inf, -np.inf
        for r in self._regions:
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            y = r.get(self._curve_method())
            if y is None:
                continue
            x = self._curve_x(r, len(np.asarray(y)))
            if x.size:
                lo, hi = min(lo, float(x.min())), max(hi, float(x.max()))
        if self._live and self._live_buf:
            x = self._axis_for(self._x_data, len(list(self._live_buf)[-1][1]))
            if x.size:
                lo, hi = min(lo, float(x.min())), max(hi, float(x.max()))
        return None if not np.isfinite(lo) else (lo, hi)

    def _range_misses_data_msg(self) -> str:
        """What to put on the graph when From/To keeps no data at all.

        An empty white graph with a ±0.05 axis reads as "the archive has nothing",
        which is the wrong thing to conclude — and it is exactly what the panel
        showed while the range and the curves were on two different axes."""
        quantity, unit, _ = self._x_names()
        span = self._drawn_x_span()
        head = (f"Nothing inside {quantity} range "
                f"{self._sb_x_min.value()} … {self._sb_x_max.value()}"
                + (f" {unit}" if unit else ""))
        if span is None:
            return head
        return (f"{head}\nThe spectra cover {span[0]:.0f} … {span[1]:.0f}"
                + (f" {unit}" if unit else "")
                + " — widen From/To, or tick Auto-fit and analyze again.")

    def _fit_bot_ylim(self, ax):
        """Scale the spectra graph to the curves that are on it, plus 5 % air."""
        rng = self._measured_y_range(ax)
        if rng is None:
            return
        lo, hi = rng
        pad = (hi - lo) * 0.05 or (abs(hi) * 0.05 or 0.05)
        ax.set_ylim(lo - pad, hi + pad)

    def _track_zoom_on(self, ax, xlim_attr, ylim_attr, redrawing_attr, tb_attr):
        """(Re)connect the pan/zoom memory of one graph.

        Two things this has to get right, and both were wrong:

        * **Re-wire after every ax.clear().** clear() throws the axes' whole
          callback registry away and puts an empty one in its place, so wiring
          this once in __init__ left the memory dead from the first redraw on:
          a zoom was forgotten as soon as anything was redrawn.
        * **Only a real gesture counts.** Matplotlib autoscales an axes while it
          is being DRAWN, i.e. after the redraw flag has been dropped, so the
          empty placeholder graph's own ±0.05 fired ylim_changed and was filed
          away as "the user's zoom". Every later redraw then re-applied it, and
          the intensity axis stopped at 0.05 with spectra peaking at 1.0 — the
          reported bug, and the reason a new X range dragged Y to a range the
          user had never asked for. So a change is only remembered while Pan or
          Zoom is actually the active tool; typed limits and Reset view record
          and forget themselves (_remember/_forget_axis_limits)."""
        def _gesture() -> bool:
            if getattr(self, redrawing_attr, False):
                return False
            return bool(getattr(getattr(self, tb_attr, None), "mode", ""))

        def _on_xlim(a):
            if _gesture():
                setattr(self, xlim_attr, a.get_xlim())

        def _on_ylim(a):
            if _gesture():
                setattr(self, ylim_attr, a.get_ylim())

        ax.callbacks.connect('xlim_changed', _on_xlim)
        ax.callbacks.connect('ylim_changed', _on_ylim)

    def _axis_limit_attrs(self, ax):
        """The saved-zoom field names of this axes, or None (e.g. a twin axes)."""
        if ax is getattr(self, "_ax_top", None):
            return "_top_user_xlim", "_top_user_ylim"
        if ax is getattr(self, "_ax_bot", None):
            return "_bot_user_xlim", "_bot_user_ylim"
        return None

    def _remember_axis_limits(self, ax):
        """Keep the limits this axes has now across the next redraws.

        For limits the user TYPED (the Axis limits… dialog): those come from no
        gesture at all, so the tracker above deliberately ignores them and they
        would be gone with the next redraw."""
        attrs = self._axis_limit_attrs(ax)
        if attrs:
            setattr(self, attrs[0], ax.get_xlim())
            setattr(self, attrs[1], ax.get_ylim())

    def _forget_axis_limits(self, ax):
        """Drop the saved zoom, so the graph fits itself to the data again."""
        attrs = self._axis_limit_attrs(ax)
        if attrs:
            setattr(self, attrs[0], None)
            setattr(self, attrs[1], None)

    def _track_top_zoom(self):
        """Pan/zoom memory of the search graph, plus the shot bar that rides on it."""
        self._track_zoom_on(self._ax_top, '_top_user_xlim', '_top_user_ylim',
                            '_top_redrawing', '_tb_top')
        # The shot bar covers whatever stretch of the axis the graph is showing,
        # so a zoom or a pan has to move the handle to keep it under its shot.
        # This runs on programmatic limit changes too, which is what it is for:
        # _draw_energy re-applies the saved zoom and the handle must follow.
        self._ax_top.callbacks.connect('xlim_changed',
                                       lambda _a: self._sync_shot_bar())

    def _track_bot_zoom(self):
        """Pan/zoom memory of the spectra graph."""
        self._track_zoom_on(self._ax_bot, '_bot_user_xlim', '_bot_user_ylim',
                            '_bot_redrawing', '_tb_bot')

    def _connect_zoom_tracking(self):
        """Save user's pan/zoom state so redraws don't reset it."""
        # Only the search graph is wired here: its artists are created by the
        # first _draw_energy, so nothing has wired it yet. The spectra graph was
        # wired while its canvas was built (_install_bot_cursor_artists), and both
        # are re-wired after every clear() from those same installers.
        self._track_top_zoom()
        # Clear saved zoom when user presses Home (resets to full data view)
        for action in self._tb_top.actions():
            if action.text() == "Home":
                action.triggered.connect(lambda: setattr(self, '_top_user_xlim', None) or
                                                 setattr(self, '_top_user_ylim', None))
        for action in self._tb_bot.actions():
            if action.text() == "Home":
                action.triggered.connect(lambda: setattr(self, '_bot_user_xlim', None) or
                                                 setattr(self, '_bot_user_ylim', None))

        # ── "Select spectra" mode button (top toolbar only) ───────────────────
        # Checked  = drag on top graph selects a new time region (default)
        # Unchecked = Pan or Zoom is active; span selection is suspended
        act_sel = QAction("Select", self._tb_top)
        act_sel.setCheckable(True)
        act_sel.setChecked(True)
        act_sel.setToolTip("Drag on the graph to select a time region (spectrum)")
        first = self._tb_top.actions()[0] if self._tb_top.actions() else None
        self._tb_top.insertAction(first, act_sel)
        self._act_select = act_sel

        _changing = [False]   # guard against recursive toggling

        def _on_select_toggled(checked):
            if _changing[0]:
                return
            _changing[0] = True
            try:
                if not checked:
                    # Don't allow unchecking Select unless pan/zoom is taking over
                    pan_zoom_on = any(
                        a.isChecked() for a in self._tb_top.actions()
                        if a.text() in ("Pan", "Zoom")
                    )
                    if not pan_zoom_on:
                        act_sel.setChecked(True)   # keep Select active
                        return
                if self._span is not None:
                    self._span.set_active(checked)
                if checked:
                    # Cancel any active pan/zoom mode
                    for a in self._tb_top.actions():
                        if a.text() == "Pan" and a.isChecked():
                            self._tb_top.pan()
                        elif a.text() == "Zoom" and a.isChecked():
                            self._tb_top.zoom()
            finally:
                _changing[0] = False

        def _on_pan_zoom_toggled(checked):
            if _changing[0]:
                return
            _changing[0] = True
            try:
                if checked:
                    act_sel.setChecked(False)
                    if self._span is not None:
                        self._span.set_active(False)
                else:
                    # If neither pan nor zoom remains active, restore Select
                    still_on = any(
                        a.isChecked() for a in self._tb_top.actions()
                        if a.text() in ("Pan", "Zoom")
                    )
                    if not still_on:
                        act_sel.setChecked(True)
                        if self._span is not None:
                            self._span.set_active(True)
            finally:
                _changing[0] = False

        act_sel.toggled.connect(_on_select_toggled)
        for a in self._tb_top.actions():
            if a.text() in ("Pan", "Zoom"):
                a.toggled.connect(_on_pan_zoom_toggled)

    # ── Mode ──────────────────────────────────────────────────────────────────
    def _set_live_mode(self, live: bool):
        self._g_live.setVisible(live)
        if not live:
            self._stop_live()
            # back to archive: redraw the loaded day, if any
            if self._energy_data:
                self._draw_energy()
                self._install_span()
            else:
                self._draw_top_empty()
        # In live mode the search graph is irrelevant; in archive it follows the
        # "Show search graph" checkbox.
        self._update_top_visibility()
        self._redraw_spectra()

    # ── Search-PV list (pick / add / remove / persist) ─────────────────────────
    def _load_search_pvs(self) -> "list[tuple[str, str]]":
        """Read the saved PV list and, as a side effect, the set of PVs whose tick
        box is off. A missing "shown" key means shown, so a file written by an older
        version comes back with every PV on the graph."""
        try:
            with open(_search_pv_config_path(), encoding="utf-8") as f:
                data = json.load(f)
            pvs = [(str(d["label"]), str(d["channel"]))
                   for d in data if d.get("label") and d.get("channel")]
            if pvs:
                self._pv_hidden = {str(d["channel"]) for d in data
                                   if d.get("channel") and d.get("shown") is False}
                # Never come back with nothing on the graph: if every PV was parked,
                # the panel would look broken and the tick boxes give no hint why.
                if len(self._pv_hidden) >= len(pvs):
                    self._pv_hidden = set()
                return pvs
        except Exception:
            pass
        return list(DEFAULT_SEARCH_PVS)

    def _save_search_pvs(self):
        try:
            p = _search_pv_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump([{"label": lbl, "channel": ch,
                            "shown": ch not in self._pv_hidden}
                           for lbl, ch in self._search_pvs], f, indent=2)
        except Exception:
            pass

    def _load_spec_y(self) -> str:
        """Load the saved spectrum Y (intensity) channel. Supports both the new
        'y' key (full channel name) and the legacy 'base' key (which implied a
        _X/_Y pair, i.e. Y = base + '_Y')."""
        try:
            with open(_spec_pvs_config_path(), encoding="utf-8") as f:
                data = json.load(f)
            y = data.get("y")
            if y:
                return str(y).strip()
            base = str(data.get("base", "") or "").strip()
            if base:
                return base + "_Y"
        except Exception:
            pass
        return PV_SPEC_Y

    def _load_x_axis_cfg(self) -> dict:
        """Load the saved wavelength-axis build config (default: native _X)."""
        try:
            with open(_spec_pvs_config_path(), encoding="utf-8") as f:
                data = json.load(f)
            cfg = data.get("x_axis")
            if isinstance(cfg, dict) and cfg.get("mode") in (
                "native", "pv", "linear", "csv", "index"
            ):
                return cfg
        except Exception:
            pass
        return {"mode": "native"}

    def _save_spec_base(self):
        try:
            p = _spec_pvs_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"y": self._spec_y_pv, "base": self._spec_base_pv,
                           "x_axis": self._x_axis_cfg}, f, indent=2)
        except Exception:
            pass

    def _x_axis_summary(self) -> str:
        """Human-readable description of the current wavelength-axis source."""
        cfg = self._x_axis_cfg or {"mode": "native"}
        mode = cfg.get("mode", "native")
        if mode == "native":
            return self._spec_x_pv
        if mode == "index":
            return "sample index (0, 1, 2 …)"
        if mode == "csv":
            return f"CSV: {os.path.basename(cfg.get('csv_path', '?'))}"
        src = cfg.get("source_pv", "?")
        if mode == "linear":
            return f"{cfg.get('scale', 1.0)}·({src}) + {cfg.get('offset', 0.0)}"
        return src

    # ── Unit of the X axis ────────────────────────────────────────────────────
    def _x_unit(self) -> str:
        """Unit of the spectrum graph's horizontal axis: what the user typed, or
        a guess from the channel name."""
        cfg = self._x_axis_cfg or {"mode": "native"}
        u = cfg.get("unit")
        if isinstance(u, str) and u.strip():
            return u.strip()
        if cfg.get("mode") == "index":
            return ""
        src = (cfg.get("source_pv") if cfg.get("mode") in ("pv", "linear")
               else self._spec_x_pv)
        return _guess_x_unit(src, self._spec_base_pv)

    def _x_names(self) -> tuple:
        """(quantity, unit, symbol) behind every axis label the panel prints —
        ("Wavelength", "nm", "λ") for a spectrometer, ("Time", "fs", "t") for
        the SPIDER time domain."""
        unit = self._x_unit()
        quantity, symbol = _x_unit_kind(unit)
        return quantity, unit, symbol

    def _x_title(self) -> str:
        """Axis title of the spectrum graph, sample numbers included."""
        if self._x_is_samples():
            return "Sample number (no measured axis resolved)"
        quantity, unit, _ = self._x_names()
        return f"{quantity} [{unit}]" if unit else quantity

    def _fmt_x_value(self, v, decimals: int = 2) -> str:
        """One X value with its unit, for the readouts and the details box."""
        if v is None:
            return "n/a"
        unit = "" if self._x_is_samples() else self._x_unit()
        return f"{v:.{decimals}f} {unit}".strip()

    def _on_x_unit_edited(self):
        """The unit changes labels only — no data is refetched or recomputed."""
        new = self._edit_x_unit.text().strip()
        cfg = dict(self._x_axis_cfg or {"mode": "native"})
        if new == (cfg.get("unit") or "").strip():
            return
        cfg["unit"] = new
        self._x_axis_cfg = cfg
        self._save_spec_base()
        self._edit_x_unit.setText(self._x_unit())
        self._sync_x_unit_labels()
        self._rebuild_regions_ui()
        self._redraw_spectra()

    def _x_fit_note(self) -> str:
        """A word about an axis the archiver stored only part of, so a rebuilt
        axis never passes for a fully archived one. '' when nothing was rebuilt."""
        method = self._curve_method()
        for r in self._regions:
            if not r.get("analyzed"):
                continue
            y, x = r.get(method), r.get("x")
            if y is None or x is None:
                continue
            n  = len(np.asarray(y))
            nx = len(np.asarray(x))
            if nx != n and _fit_x_axis(x, n) is not None:
                # Say the assumption out loud: the stored part was taken as the
                # BEGINNING of the axis and continued at the same spacing. (For
                # SPIDER that is certain — the Fourier-limit trace peaks exactly
                # on point 2048, so point 2048 is t = 0.)
                return (f"X axis: the archive stored only {nx} of the {n} axis "
                        f"points — the rest was continued at the same spacing.")
        return ""

    def _sync_x_unit_labels(self):
        """Repaint the labels that carry the unit outside the graph itself."""
        quantity, unit, _ = self._x_names()
        if getattr(self, "_lbl_xrange", None) is not None:
            self._lbl_xrange.setText(
                (f"{quantity} range [{unit}]" if unit else f"{quantity} range").upper())

    def _resolve_x_data(self, start_ns: int, end_ns: int) -> "np.ndarray | None":
        """Fetch / build the wavelength axis according to self._x_axis_cfg.
        Returns None when the axis should fall back to the sample index."""
        cfg = self._x_axis_cfg or {"mode": "native"}
        mode = cfg.get("mode", "native")
        if mode == "index":
            return None
        if mode == "csv":
            return _load_x_csv(cfg.get("csv_path", ""))
        src = self._spec_x_pv if mode == "native" else cfg.get("source_pv")
        if not src:
            return None
        wf = _fetch_waveforms(src, start_ns, end_ns)
        if not wf:
            return None
        x = np.asarray(wf[-1][1], dtype=float)
        if mode == "linear":
            x = float(cfg.get("scale", 1.0)) * x + float(cfg.get("offset", 0.0))
        return x

    def _change_spec_pv(self):
        """Open PvSearchDialog and set the spectrum Y (intensity) channel.

        • A channel ending in _X or _Y is treated as one half of a paired
          waveform: Y = base+'_Y', X = base+'_X'.
        • Any other channel (e.g. …:FundY) IS the Y waveform itself, and almost
          never has a matching _X — so we ask how to build the wavelength axis.
        """
        dlg = PvSearchDialog(self)
        dlg.setWindowTitle("Select spectrum PV  (an _X/_Y pair, or a standalone waveform)")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        added = dlg.added_pvs()
        if not added:
            return
        ch = added[0][1]
        paired = ch.endswith("_X") or ch.endswith("_Y")
        base = _strip_xy_suffix(ch)
        self._spec_base_pv = base
        self._spec_x_pv    = base + "_X"
        self._spec_y_pv    = (base + "_Y") if paired else ch
        self._x_data       = None   # invalidate cached X axis

        # Decide how the wavelength axis is built.
        native_x_exists = self._spec_x_pv in _cpva_channel_cache
        if native_x_exists or (paired and not _cpva_channel_cache):
            self._x_axis_cfg = {"mode": "native"}
        else:
            xdlg = XAxisSourceDialog(base, self)
            if xdlg.exec() == QDialog.DialogCode.Accepted and xdlg.result_cfg():
                self._x_axis_cfg = xdlg.result_cfg()
            else:
                self._x_axis_cfg = {"mode": "index"}

        self._lbl_spec_base.setText(base)
        self._lbl_spec_base.setToolTip(
            f"X axis: {self._x_axis_summary()}\nY axis: {self._spec_y_pv}"
        )
        self._lbl_spec_pair.setText(f"X: {self._x_axis_summary()}   /   Y: {self._spec_y_pv}")
        # A new channel means a new axis, so the unit is guessed again from its
        # name — a hand-typed unit belongs to the channel it was typed for.
        self._edit_x_unit.setText(self._x_unit())
        self._sync_x_unit_labels()
        self._save_spec_base()
        if self._live:
            self._stop_live()
            self._set_status(f"Spectrum PV changed to {base} — live stopped.")
        else:
            self._set_status(f"Spectrum PV: {base}  (X: {self._x_axis_summary()})")
        self._reanalyze_all(f"Spectrum PV changed to {base}")

    # ── Re-analysis after the spectrum source changed ──────────────────────────
    # Results carried by a region belong to ONE channel and ONE wavelength axis.
    # Changing either used to leave them in place: the old curves stayed on screen,
    # the graph silently mixed two channels, and Analyze stayed grey because every
    # region still counted as analyzed — so the only way out was Clear all and
    # reselecting every span by hand. Now the selections stay and the results are
    # recomputed for them.
    _RESULT_KEYS = ("x", "mean", "median", "trimmed", "sigma", "std", "p10", "p90",
                    "stack", "stack_ts", "orders", "energy_avg", "energy_n", "n",
                    "_metrics")

    def _reanalyze_all(self, why: str):
        """Drop stale results but KEEP the selections, then re-run the analysis."""
        stale = [r for r in self._regions if r.get("analyzed")]
        if not stale and not self._busy:
            return
        self._analysis_gen += 1          # anything in flight is now obsolete
        for r in stale:
            r["analyzed"] = False
            for k in self._RESULT_KEYS:
                r.pop(k, None)
            r["n"] = 0
        self._rebuild_regions_ui()
        self._redraw_spectra()
        self._update_action_buttons()   # Analyze becomes available again
        n = len(stale) or len(self._regions)
        self._set_status(f"{why} — re-analysing {n} selection(s)…")
        if self._busy:
            # Let the obsolete run unwind first; its done/error handler starts this
            # one. Two overlapping fetches on the same regions would race.
            self._cancel.set()
            self._reanalyze_pending = True
            return
        self._run_analysis()

    def _start_pending_reanalysis(self):
        """Kick off a re-analysis that was waiting for an obsolete run to unwind."""
        if not self._reanalyze_pending:
            return
        self._reanalyze_pending = False
        self._cancel.clear()
        QTimer.singleShot(0, self._run_analysis)

    def _set_cbar_space(self, on: bool):
        """Reserve or release the width the colour bar needs on the spectra graph.

        Idempotent on purpose: both branches SET an absolute value rather than
        adjusting the current one, so running this on every redraw can never
        accumulate. That accumulation was the bug — see _CBAR_BOX."""
        fig = self._fig_bot
        eng = fig.get_layout_engine()
        if eng is not None and hasattr(eng, "set"):
            eng.set(rect=_CBAR_RECT if on else _FULL_RECT)
        elif self._bot_right_base is not None:
            # Layout engine is off because the user set the margins by hand in the
            # Subplots dialog. Squeeze the plot only as far as the bar needs, always
            # measured from the margin THEY chose, never from the current one.
            base = self._bot_right_base
            fig.subplots_adjust(right=min(base, _CBAR_RECT[2]) if on else base)

    def _save_layout(self, *_):
        """Persist splitter sizes and (when manually adjusted) subplot margins.

        Saves the remembered ratio, not the live sizes: while the search graph is
        hidden the live sizes are [0, everything], and saving that collapsed the top
        graph on the next start."""
        # "splitter_user" separates a divider the user dragged from the ratio the
        # panel chose for itself. Without it every saved file looked like a manual
        # setting and the automatic ratio could never apply again.
        data: dict = {"splitter": list(self._split_ratio),
                      "splitter_user": self._split_user_set}
        for suffix, key in (("top", "fig_top"), ("bot", "fig_bot")):
            fig = getattr(self, f"_fig_{suffix}")
            engine = fig.get_layout_engine()
            # Only save subplot params when user has manually adjusted them
            # (tight/constrained layout engine is no longer active).
            if engine is None or type(engine).__name__ == "PlaceHolderLayoutEngine":
                sp = fig.subplotpars
                data[key] = {k: round(getattr(sp, k), 4)
                             for k in ("left", "right", "top", "bottom", "hspace", "wspace")}
                # Remember the right margin the user actually chose. Skip it while
                # the colour bar is up, or the squeezed value would become the base
                # and the plot would never grow back.
                if suffix == "bot" and self._colorbar_bot is None:
                    self._bot_right_base = sp.right
        if data == self._last_saved_layout:
            return
        self._last_saved_layout = data
        try:
            p = _layout_config_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_layout(self):
        """Restore splitter sizes and subplot margins saved by _save_layout()."""
        try:
            with open(_layout_config_path(), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        sizes = data.get("splitter")
        if (data.get("splitter_user") and isinstance(sizes, list) and len(sizes) == 2
                and all(s >= 0 for s in sizes)):
            self._splitter.setSizes([int(s) for s in sizes])
            # A collapsed top graph is a "search graph hidden" state, not a
            # ratio — keep the previous default so unhiding it works.
            if sizes[0] > 0:
                self._split_ratio = [int(s) for s in sizes]
                self._split_user_set = True
        _sp_keys = ("left", "right", "top", "bottom", "hspace", "wspace")
        for suffix, key in (("top", "fig_top"), ("bot", "fig_bot")):
            params = data.get(key)
            if not params:
                continue
            fig = getattr(self, f"_fig_{suffix}")
            try:
                kwargs = {k: float(params[k]) for k in _sp_keys if k in params}
                fig.set_layout_engine(None)   # disable tight_layout; use saved params
                fig.subplots_adjust(**kwargs)
                if suffix == "bot":
                    self._bot_right_base = kwargs.get("right", fig.subplotpars.right)
            except Exception:
                pass

    def _active_search_channel(self) -> str:
        row = self._tbl_pvs.currentRow()
        return self._search_pvs[row][1] if 0 <= row < len(self._search_pvs) else PV_ENERGY

    def _active_search_label(self) -> str:
        row = self._tbl_pvs.currentRow()
        return self._search_pvs[row][0] if 0 <= row < len(self._search_pvs) else "Signal"

    def _pv_is_shown(self, channel: str) -> bool:
        """Is this PV drawn in the search graph? (Off ≠ removed: it stays listed.)"""
        return channel not in self._pv_hidden

    def _shown_pvs(self) -> "list[tuple[str, str]]":
        return [(lbl, ch) for lbl, ch in self._search_pvs if self._pv_is_shown(ch)]

    def _first_shown_row(self) -> int:
        """Row of the first ticked PV, so the search never starts on a hidden one."""
        for i, (_lbl, ch) in enumerate(self._search_pvs):
            if self._pv_is_shown(ch):
                return i
        return 0

    def _fill_pv_row(self, row: int, lbl: str, ch: str):
        """Build the three cells of one PV row. Caller has already inserted the row
        and is responsible for blocking itemChanged while it rebuilds the table."""
        show_item = QTableWidgetItem()
        show_item.setFlags((show_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                           & ~Qt.ItemFlag.ItemIsEditable)
        show_item.setCheckState(Qt.CheckState.Checked if self._pv_is_shown(ch)
                                else Qt.CheckState.Unchecked)
        # Centred so the indicator sits under the column's ✓ header instead of
        # hugging the left edge of the cell.
        show_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        show_item.setToolTip("Draw this PV in the search graph.")
        lbl_item = QTableWidgetItem(lbl)
        lbl_item.setToolTip(ch)
        ch_item = QTableWidgetItem(ch)
        ch_item.setFlags(ch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        ch_item.setToolTip(ch)
        # A PV that is off keeps its row readable — greyed, never blanked out.
        if not self._pv_is_shown(ch):
            for it in (lbl_item, ch_item):
                it.setForeground(QColor("#90A4AE"))
        self._tbl_pvs.setItem(row, _PV_COL_SHOW, show_item)
        self._tbl_pvs.setItem(row, _PV_COL_LABEL, lbl_item)
        self._tbl_pvs.setItem(row, _PV_COL_CHAN, ch_item)

    def _trace_colour(self, channel: str) -> str:
        """Colour of one search-graph trace, tied to the PV ITSELF (its place in the
        PV list), never to its position among the loaded curves. Keyed by position in
        the loaded set, a PV with no data that day — or any reorder — silently handed
        every other curve a different colour."""
        for i, (_lbl, ch) in enumerate(self._search_pvs):
            if ch == channel:
                return _TRACE_COLORS[i % len(_TRACE_COLORS)]
        return _TRACE_COLORS[0]

    def _fit_pv_columns(self):
        """Fit all three columns inside the table, Channel included.

        The Channel column used to be sized to the longest channel name, which is
        wider than the sidebar — so the column ran off the right-hand edge and both
        its header and every name were cut in half. It now takes exactly the room
        left over and elides in the middle instead. The horizontal scrollbar stays
        for the emergency case: a squeezed panel where even the minimum widths do
        not fit."""
        tbl = getattr(self, "_tbl_pvs", None)
        if tbl is None:
            return
        # resizeColumnToContents() first, then read the width back: on an
        # Interactive column sectionSizeHint() reports the HEADER's hint, not the
        # widest cell, which sized Label to the word "Label".
        tbl.setColumnWidth(_PV_COL_SHOW, _PV_SHOW_W)
        tbl.resizeColumnToContents(_PV_COL_LABEL)
        tbl.setColumnWidth(_PV_COL_LABEL,
                           max(60, min(tbl.columnWidth(_PV_COL_LABEL) + 6,
                                       _PV_LABEL_MAX_W)))
        room = (tbl.viewport().width() - tbl.columnWidth(_PV_COL_SHOW)
                - tbl.columnWidth(_PV_COL_LABEL) - 2)
        tbl.setColumnWidth(_PV_COL_CHAN, max(_PV_CHAN_MIN_W, room))

    def resizeEvent(self, ev):
        # The table's share of the width changes with the sidebar's scrollbar
        # appearing or disappearing, so re-fit on every resize.
        super().resizeEvent(ev)
        self._fit_pv_columns()

    def _refresh_pv_table(self, select_row: int = 0):
        # Drop tick-box state for PVs that are no longer listed, otherwise adding
        # the same channel back later brings it in already switched off.
        self._pv_hidden &= {ch for _, ch in self._search_pvs}
        self._tbl_pvs.blockSignals(True)
        self._tbl_pvs.setRowCount(0)
        for lbl, ch in self._search_pvs:
            r = self._tbl_pvs.rowCount()
            self._tbl_pvs.insertRow(r)
            self._fill_pv_row(r, lbl, ch)
        if self._search_pvs:
            row = min(max(select_row, 0), len(self._search_pvs) - 1)
            # Searching by a PV that is not drawn would leave the card naming a
            # curve nobody can see, so land on a ticked row instead.
            if not self._pv_is_shown(self._search_pvs[row][1]):
                row = self._first_shown_row()
            self._tbl_pvs.selectRow(row)
        self._tbl_pvs.blockSignals(False)
        self._fit_pv_columns()
        # The PV *set* changed → reload all search PVs for the loaded windows.
        self._update_active_card()
        self._btn_rem_pv.setEnabled(self._tbl_pvs.currentRow() >= 0)
        if not self._live and self._windows:
            self._load_day_energy()

    def _update_preset_combo(self):
        self._cmb_preset.blockSignals(True)
        self._cmb_preset.clear()
        self._cmb_preset.addItem("-- select preset --")
        for p in _load_search_presets():
            self._cmb_preset.addItem(p["name"])
        self._cmb_preset.setCurrentIndex(0)
        self._cmb_preset.blockSignals(False)

    def _on_preset_combo_changed(self, idx: int):
        # Loads the preset's PVs but KEEPS the preset selected, so the rename /
        # delete buttons know which preset to act on.
        if idx <= 0:
            return
        presets = _load_search_presets()
        pidx = idx - 1
        if not (0 <= pidx < len(presets)):
            return
        pvs_data = presets[pidx].get("pvs", [])
        pvs = [(d["label"], d["channel"]) for d in pvs_data if d.get("label") and d.get("channel")]
        if pvs:
            self._search_pvs = pvs
            self._save_search_pvs()
            self._refresh_pv_table(select_row=0)

    def _selected_preset_index(self) -> int:
        """Index into the preset list of the combo's current item (-1 = none)."""
        return self._cmb_preset.currentIndex() - 1

    def _reload_preset_combo(self, select_name: "str | None" = None):
        self._cmb_preset.blockSignals(True)
        self._cmb_preset.clear()
        self._cmb_preset.addItem("-- select preset --")
        names = [p["name"] for p in _load_search_presets()]
        for n in names:
            self._cmb_preset.addItem(n)
        if select_name and select_name in names:
            self._cmb_preset.setCurrentIndex(names.index(select_name) + 1)
        else:
            self._cmb_preset.setCurrentIndex(0)
        self._cmb_preset.blockSignals(False)

    def _preset_add(self):
        name, ok = QInputDialog.getText(self, "New preset",
                                        "Save the current PV list as preset named:")
        if not ok or not name.strip():
            return
        name = name.strip()
        presets = _load_search_presets()
        pvs = [{"label": l, "channel": c} for l, c in self._search_pvs]
        for p in presets:
            if p["name"] == name:
                p["pvs"] = pvs
                break
        else:
            presets.append({"name": name, "pvs": pvs})
        _save_search_presets(presets)
        self._reload_preset_combo(select_name=name)
        self._set_status(f"Preset '{name}' saved ({len(pvs)} PV(s)).")

    def _preset_rename(self):
        pidx = self._selected_preset_index()
        presets = _load_search_presets()
        if not (0 <= pidx < len(presets)):
            QMessageBox.information(self, "Preset", "Select a preset to update first.")
            return
        cur = presets[pidx]["name"]
        name, ok = QInputDialog.getText(
            self, "Update preset",
            "New name (also saves the current PV list into this preset):", text=cur)
        if not ok or not name.strip():
            return
        presets[pidx]["name"] = name.strip()
        presets[pidx]["pvs"] = [{"label": l, "channel": c} for l, c in self._search_pvs]
        _save_search_presets(presets)
        self._reload_preset_combo(select_name=name.strip())
        self._set_status(f"Preset '{name.strip()}' updated.")

    def _preset_delete(self):
        pidx = self._selected_preset_index()
        presets = _load_search_presets()
        if not (0 <= pidx < len(presets)):
            QMessageBox.information(self, "Preset", "Select a preset to delete first.")
            return
        name = presets[pidx]["name"]
        if QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        del presets[pidx]
        _save_search_presets(presets)
        self._reload_preset_combo()
        self._set_status(f"Preset '{name}' deleted.")

    # ── Inline CPVA channel search (fast add) ──────────────────────────────
    def _ensure_channels_loaded(self):
        if _cpva_channel_cache:
            return
        sig = _Sig(self)
        sig.done.connect(self._on_channels_loaded)
        def _work():
            try:
                sig.done.emit(_cpva_load_all_channels())
            except Exception:
                sig.done.emit([])
        _bg(_work)

    def _on_channels_loaded(self, channels: list):
        global _cpva_channel_cache
        if channels:
            _cpva_channel_cache = channels
        self._on_inline_search(self._edit_pv_search.text())

    def _on_inline_search(self, text: str):
        q = text.strip().lower()
        self._lst_pv_search.clear()
        if not q:
            self._lst_pv_search.setVisible(False)
            return
        if not _cpva_channel_cache:
            self._ensure_channels_loaded()
            self._lst_pv_search.setVisible(False)
            return
        matches = _pv_search(_cpva_channel_cache, q)
        for ch in matches[:200]:
            self._lst_pv_search.addItem(ch)
        self._lst_pv_search.setVisible(bool(matches))

    def _on_inline_result_clicked(self, item: QListWidgetItem):
        ch = item.text()
        if ch not in {c for _, c in self._search_pvs}:
            self._search_pvs.append((ch, ch))
            self._save_search_pvs()
            self._refresh_pv_table(select_row=len(self._search_pvs) - 1)
            self._set_status(f"Added {ch}.")

    def _open_add_pv_dialog(self):
        dlg = PvSearchDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        added = dlg.added_pvs()
        existing_channels = {ch for _, ch in self._search_pvs}
        for lbl, ch in added:
            if ch not in existing_channels:
                self._search_pvs.append((lbl, ch))
                existing_channels.add(ch)
        self._save_search_pvs()
        self._refresh_pv_table(select_row=len(self._search_pvs) - 1)

    def _remove_selected_pv(self):
        row = self._tbl_pvs.currentRow()
        if not (0 <= row < len(self._search_pvs)):
            return
        if len(self._search_pvs) <= 1:
            QMessageBox.information(self, "Remove PV", "Keep at least one PV in the list.")
            return
        del self._search_pvs[row]
        self._save_search_pvs()
        self._refresh_pv_table(select_row=min(row, len(self._search_pvs) - 1))

    def _on_pv_item_changed(self, item: QTableWidgetItem):
        """One itemChanged handler for the whole table: the tick box switches the
        curve on the graph, the Label cell renames the PV."""
        if item.column() == _PV_COL_SHOW:
            self._on_pv_show_toggled(item)
        elif item.column() == _PV_COL_LABEL:
            self._on_pv_label_edited(item)

    def _on_pv_show_toggled(self, item: QTableWidgetItem):
        row = item.row()
        if not (0 <= row < len(self._search_pvs)):
            return
        ch = self._search_pvs[row][1]
        want_shown = item.checkState() == Qt.CheckState.Checked
        if want_shown == self._pv_is_shown(ch):
            return
        # Refuse to empty the graph: the last remaining curve stays on.
        if not want_shown and len(self._shown_pvs()) <= 1:
            self._tbl_pvs.blockSignals(True)
            item.setCheckState(Qt.CheckState.Checked)
            self._tbl_pvs.blockSignals(False)
            self._set_status("At least one PV has to stay on the search graph.")
            return
        if want_shown:
            self._pv_hidden.discard(ch)
        else:
            self._pv_hidden.add(ch)
        self._save_search_pvs()
        # Repaint the row (grey when off) without rebuilding the table, so the
        # selected row and the scroll position survive a tick.
        self._tbl_pvs.blockSignals(True)
        col = QColor("#90A4AE") if not want_shown else QColor("#000000")
        for c in (_PV_COL_LABEL, _PV_COL_CHAN):
            cell = self._tbl_pvs.item(row, c)
            if cell is not None:
                cell.setForeground(col)
        self._tbl_pvs.blockSignals(False)
        # Was the PV being searched by the one just switched off? Move to a visible
        # row, which also redraws; otherwise just redraw.
        if not want_shown and self._tbl_pvs.currentRow() == row:
            self._tbl_pvs.selectRow(self._first_shown_row())
        else:
            self._update_active_card()
            self._refresh_energy_view()

    def _on_pv_label_edited(self, item: QTableWidgetItem):
        row = item.row()
        if not (0 <= row < len(self._search_pvs)):
            return
        new_lbl = item.text().strip()
        if not new_lbl:
            self._tbl_pvs.blockSignals(True)
            item.setText(self._search_pvs[row][0])
            self._tbl_pvs.blockSignals(False)
            return
        _, ch = self._search_pvs[row]
        if new_lbl != self._search_pvs[row][0]:
            self._search_pvs[row] = (new_lbl, ch)
            item.setToolTip(ch)
            self._save_search_pvs()

    def _open_edit_presets_dialog(self):
        dlg = PresetEditDialog(self._search_pvs, self)
        dlg.preset_loaded.connect(self._apply_preset)
        dlg.exec()
        self._update_preset_combo()

    def _apply_preset(self, pvs: list):
        self._search_pvs = pvs
        self._save_search_pvs()
        self._refresh_pv_table(select_row=0)

    def _update_active_card(self):
        """Fill the top of step 2 with the PV the search is actually running on.

        The ticked PVs are drawn in the search graph, but the selected row is the
        one being searched by — so name that one in full and only count the rest,
        instead of running them all together into one grey line."""
        if not self._search_pvs:
            self._lbl_active_search.setText("—")
            self._lbl_active_search.setToolTip("")
            self._lbl_active_chan.setText("Add a PV in the list below")
            return
        label = self._active_search_label()
        chan  = self._active_search_channel()
        others = max(0, len(self._shown_pvs()) - 1)
        off    = len(self._pv_hidden)
        extra  = f"      (+{others} more plotted)" if others > 0 else ""
        if off:
            extra += f"      ({off} off)"
        self._lbl_active_search.setText(label)
        self._lbl_active_chan.setText(chan + extra)
        tip = "\n".join(f"{'✓' if self._pv_is_shown(ch) else '—'}  {lbl}  →  {ch}"
                        for lbl, ch in self._search_pvs)
        self._lbl_active_search.setToolTip(tip)
        self._lbl_active_chan.setToolTip(tip)

    def _on_search_pv_changed(self, *_):
        # Selecting a different row only changes which trace is emphasized — the
        # ticked search PVs are plotted together, so just redraw (no refetch).
        row = self._tbl_pvs.currentRow()
        # "Search by this" implies "show this": picking a row that was switched off
        # switches it back on rather than searching by an invisible curve.
        if 0 <= row < len(self._search_pvs):
            ch = self._search_pvs[row][1]
            if not self._pv_is_shown(ch):
                self._pv_hidden.discard(ch)
                self._save_search_pvs()
                self._tbl_pvs.blockSignals(True)
                box = self._tbl_pvs.item(row, _PV_COL_SHOW)
                if box is not None:
                    box.setCheckState(Qt.CheckState.Checked)
                for c in (_PV_COL_LABEL, _PV_COL_CHAN):
                    cell = self._tbl_pvs.item(row, c)
                    if cell is not None:
                        cell.setForeground(QColor("#000000"))
                self._tbl_pvs.blockSignals(False)
        self._btn_rem_pv.setEnabled(self._tbl_pvs.currentRow() >= 0)
        self._update_active_card()
        if not self._live and self._energy_data:
            self._refresh_energy_view()

    def _on_color_mode_changed(self, *_):
        idx = self._cmb_color.currentIndex()
        self._color_mode = {0: "order", 1: "gdd", 2: "tod"}.get(idx, "order")
        self._redraw_spectra()

    # ── Day picker ────────────────────────────────────────────────────────────
    def _day_summary(self) -> str:
        """The Step-1 label: one window spelled out, several summed up."""
        w = self._windows
        if not w:
            return "No day selected"
        if len(w) == 1:
            a, b = _ns_to_dt(w[0][0]), _ns_to_dt(w[0][1])
            return (f"{a.strftime('%Y-%m-%d')}\n"
                    f"{a.strftime('%H:%M')} – {b.strftime('%H:%M')}")
        hours = sum(b - a for a, b in w) / 3.6e12
        return (f"{len(w)} days\n"
                f"{_ns_to_dt(w[0][0]).strftime('%Y-%m-%d')} … "
                f"{_ns_to_dt(w[-1][0]).strftime('%Y-%m-%d')}\n"
                f"{hours:.1f} h selected")

    def _window_tooltip(self) -> str:
        if not self._windows:
            return ""
        return "Loaded:\n" + "\n".join(_fmt_window(w) for w in self._windows)

    def _pick_day(self):
        """Open the shared day/time picker (daypicker.py — the same calendar as
        Image Tools). It returns one time window per picked day, each with its own
        hours; only those windows are fetched and drawn."""
        init_date = _ns_to_dt(self._windows[0][0]).date() if self._windows else None
        dlg = DayTimePicker(parent=self, init_date=init_date,
                            init_segments=list(self._segments) or None,
                            allow_live=False,     # Spectra has its own Live button
                            title="Select day(s) and time window")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        windows = [w for w in dlg.selected_windows() if w[1] > w[0]]
        if not windows:
            return
        if not self._confirm_request_volume(windows):
            return
        # switch UI back to archive mode
        self._btn_archive.setChecked(True)
        self._set_live_mode(False)
        self._segments = list(dlg.all_segments())
        self._windows  = windows
        # Keep already-selected spectra across day changes — they carry absolute
        # timestamps and stay in the list (delete them via the ✕ in the list).
        # A zoom remembered in axis coordinates would stand for a different
        # instant now, but _load_day_energy ends in _draw_energy(), which resets
        # the view anyway — so there is nothing to clear here.
        self._load_day_energy()

    def _confirm_request_volume(self, windows: "list[tuple[int, int]]") -> bool:
        """Warn before a load that will take minutes — but never refuse it.

        The archive is read in one-hour pieces, per PV, so the request count is
        hours × PVs: a fortnight of eleven PVs is well over a thousand. Capping it
        would turn a slow load into a silently incomplete one, so this only asks.
        """
        hours = sum(-(-(b - a) // _CHUNK_NS) for a, b in windows)
        n_req = int(hours) * max(1, len(self._search_pvs))
        if n_req <= _FETCH_WARN_REQUESTS:
            return True
        ans = QMessageBox.question(
            self, "This will take a while",
            f"{len(windows)} day(s) × {len(self._search_pvs)} PV(s) is about "
            f"{n_req} archive requests.\n\n"
            f"It will work, but it can take several minutes. Stop cancels it at "
            f"any point.\n\nLoad it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        return ans == QMessageBox.StandardButton.Yes

    def _load_day_energy(self):
        windows = list(self._windows)
        if not windows:
            return
        # The sidebar label describes what is LOADED, so it is set here rather
        # than in _pick_day: whatever route got us here, it cannot go stale.
        self._tmap = _TimeMap(windows)
        self._lbl_day.setText(self._day_summary())
        self._lbl_day.setToolTip(self._window_tooltip())
        pvs = list(self._search_pvs)
        if not pvs:
            self._draw_top_empty("Add a search PV to plot")
            return
        self._set_status(f"Loading {len(pvs)} search PV(s)…")
        self._btn_pick_day.setEnabled(False)
        self._loading = True
        self._refresh_pill()
        # One step per PV *and* window, or the bar would sit at 1/1 through a
        # ten-day load.
        steps = len(pvs) * len(windows)
        self._progress.setRange(0, steps)
        self._progress.setValue(0)
        self._progress.setFormat("Loading  %v / %m  (%p%)")
        self._progress.setVisible(True)

        sig = _Sig(self)
        sig.done.connect(self._on_energy_loaded)
        sig.error.connect(self._on_energy_error)
        sig.progress.connect(self._set_status)
        sig.progress_n.connect(self._on_analysis_progress)

        def _work():
            try:
                series = []
                step = 0
                for i, (lbl, ch) in enumerate(pvs):
                    if self._cancel.is_set():
                        break
                    data: list = []
                    err = None
                    newest = None
                    for j, (w_start, w_end) in enumerate(windows):
                        if self._cancel.is_set():
                            break
                        if len(windows) == 1:
                            sig.progress.emit(f"Loading {i+1}/{len(pvs)}: {lbl}…")
                        else:
                            sig.progress.emit(
                                f"Loading {i+1}/{len(pvs)}: {lbl} — "
                                f"window {j+1}/{len(windows)} "
                                f"({_fmt_window((w_start, w_end))})…")
                        raw = _fetch_scalars(ch, w_start, w_end)
                        # Each window overwrites _last_fetch_error, so keep the
                        # first failure instead of only the last window's.
                        err = err or _last_fetch_error.get(ch)
                        if raw:
                            newest = max(newest or 0, max(t for t, _ in raw))
                        # Every request also returns the last sample from BEFORE
                        # its own start. On the compressed axis that sample sits in
                        # removed time, so it is dropped here, in the worker, where
                        # the window is still in hand: doing it in the slot meant
                        # scanning every window for every one of half a million
                        # samples with the whole panel frozen.
                        data += [(t, v) for (t, v) in raw if w_start <= t < w_end]
                        step += 1
                        sig.progress_n.emit(step, steps)
                    data.sort(key=lambda tv: tv[0])
                    series.append({"label": lbl, "channel": ch, "data": data,
                                   "raw_newest": newest,
                                   "error": err})
                sig.done.emit(series)
            except Exception as e:
                sig.error.emit(str(e))

        self._cancel.clear()
        _bg(_work)

    def _on_energy_loaded(self, series: list):
        self._btn_pick_day.setEnabled(True)
        self._loading = False
        self._refresh_pill()
        self._progress.setVisible(False)
        self._progress.setFormat("Analyzing  %v / %m  (%p%)")
        if self._cancel.is_set():
            self._set_status("Load cancelled.")
            return
        # The worker already dropped the "last value before start" sample that
        # every request returns, so s["data"] holds only time that was asked for.
        out = []
        for s in series:
            d = s["data"]
            # Those dropped samples are still worth keeping: for a PV with nothing
            # in any window they are the only clue to when it last recorded.
            last_before = s.get("raw_newest") if not d else None
            out.append({"label": s["label"], "channel": s["channel"],
                        "color": self._trace_colour(s["channel"]), "data": d,
                        "error": s.get("error"), "last_before": last_before})
        self._energy_data = out
        total = sum(len(s["data"]) for s in out)
        empties = [s for s in out if not s["data"]]
        notes = []
        for s in empties:
            if s.get("error"):
                notes.append(f"{s['label']}: {s['error']}")
            elif s.get("last_before"):
                notes.append(f"{s['label']}: nothing in the selected time "
                             f"(last archived {_fmt_date(s['last_before'])} "
                             f"{_fmt_hms(s['last_before'])})")
            else:
                notes.append(f"{s['label']}: nothing archived")
        if total == 0:
            self._set_status("No data for the selected PV(s) in the selected "
                             "time.  " + "   ".join(notes))
            self._draw_top_empty("No data for the selected PV(s)")
            return
        msg = f"Loaded {total} samples across {len(out) - len(empties)} PV(s)."
        if notes:
            msg += "  " + "   ".join(notes)
        self._set_status(msg)
        self._draw_energy()
        self._install_span()

    def _on_energy_error(self, err: str):
        self._btn_pick_day.setEnabled(True)
        self._loading = False
        self._refresh_pill()
        self._progress.setVisible(False)
        self._progress.setFormat("Analyzing  %v / %m  (%p%)")
        self._set_status(f"Energy error: {err}")
        self._draw_top_empty("Error loading data")

    # ── Energy graph ──────────────────────────────────────────────────────────
    def _draw_top_empty(self, msg: str = "Select a day and time first  →  button on the left"):
        ax = self._ax_top
        ax.clear()
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color="#aaa", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        # Still a time axis as far as Axis limits is concerned, even while empty:
        # its X numbers would be seconds along a selection that does not exist.
        ax._sp_time_axis = True
        # clear() detached the shot marker; drawing a detached artist raises
        # "'NoneType' has no attribute 'dpi'", so put a fresh pair in place.
        self._install_top_marker_artists()
        self._canvas_top.draw_idle()

    def _paint_region_spans(self, ax):
        # A region is stored in absolute time, so it may cover time that is not on
        # the axis at all (a different day, or an unselected part of a day). _TimeMap
        # cuts it into one block per selected window: time nobody asked for is never
        # shaded, and a region wholly outside the selection simply paints nothing.
        for r in self._regions:
            for x0, x1 in self._tmap.clip(r["t_start"], r["t_end"]):
                ax.axvspan(x0, x1, alpha=0.25, color=r["color"], zorder=0)

    def _style_time_axis(self, ax):
        """Put real dates and times back on the compressed x axis.

        The axis is in seconds along the concatenated windows, so matplotlib's
        date machinery cannot be used: a FixedLocator places the round clock
        times and a FuncFormatter turns each position back into the real time it
        stands for. The formatter reads the position, never a tick index, so the
        labels stay right after a zoom.

        Known trade-off, deliberately kept: the tick positions are fixed, so a
        deep zoom into one block can leave only one or two ticks on screen. Those
        labels are still correct, and the crosshair gives the exact instant. Do
        NOT "fix" it by going back to matplotlib's date locators — they would read
        this axis as real time and mislabel every tick.
        """
        tmap = self._tmap
        # Read by _AxisLimitsDialog: typing limits into a compressed axis would
        # mean typing seconds-along-the-selection, which is meaningless.
        ax._sp_time_axis = True
        if not tmap:
            ax.set_xlabel("Time")
            return
        multi = len(tmap.windows) > 1
        ax.set_xlim(*tmap.xlim())
        pos, _lab = tmap.ticks(12)
        ax.xaxis.set_major_locator(FixedLocator(pos))

        def _fmt(x, _pos):
            dt = _ns_to_dt(tmap.from_x(x))
            # The date only on the first tick of each block — repeating it on
            # every tick makes the axis a wall of text.
            if multi and tmap.is_window_start(x, tol=1.0):
                return dt.strftime("%m-%d\n%H:%M")
            return dt.strftime("%H:%M")

        ax.xaxis.set_major_formatter(FuncFormatter(_fmt))
        ax.tick_params(axis="x", labelsize=8, colors="#263238")
        if multi:
            hours = sum(b - a for a, b in tmap.windows) / 3.6e12
            ax.set_xlabel(f"Time   —   {len(tmap.windows)} days, {hours:.1f} h "
                          f"selected   (unselected time removed)")
        else:
            a, b = tmap.windows[0]
            ax.set_xlabel(f"Time   —   {_ns_to_dt(a).strftime('%Y-%m-%d')}   "
                          f"{_ns_to_dt(a).strftime('%H:%M')} – "
                          f"{_ns_to_dt(b).strftime('%H:%M')}")

    def _paint_window_joins(self, ax):
        """Mark where one picked window ends and the next begins.

        Without this the graph reads as one continuous stretch of time, and a
        step from one day to the next looks like a real jump in the signal.
        """
        tmap = self._tmap
        if len(tmap.windows) < 2:
            return
        for x in tmap.boundaries():
            ax.axvline(x, color="#37474F", lw=1.4, ls=(0, (4, 3)), zorder=6)
        tr = blended_transform_factory(ax.transData, ax.transAxes)
        n = len(tmap.windows)
        # Inside the axes, not above them: above is where the title is. Every
        # other label once a fortnight is picked, or they overlap into a smear —
        # and each one gets a white plate so it stays readable over a trace.
        every = 1 if n <= 8 else (2 if n <= 16 else 3)
        fmt = "%a %d.%m." if n <= 8 else "%d.%m."
        for i, (cx, (a, _b)) in enumerate(tmap.window_centres()):
            if i % every:
                continue
            ax.text(cx, 0.99, _ns_to_dt(a).strftime(fmt), transform=tr,
                    ha="center", va="top", fontsize=8, color="#263238",
                    clip_on=False, zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="#CFD8DC", alpha=0.85))

    def _draw_energy(self, reset_view: bool = True):
        ax = self._ax_top
        if reset_view:
            self._top_user_xlim = None
            self._top_user_ylim = None
        self._top_redrawing = True
        # Drop the twin axes from a previous draw before clearing the base one,
        # otherwise old Y-axes pile up on every redraw.
        for extra in self._top_extra_axes:
            try:
                extra.remove()
            except Exception:
                pass
        self._top_extra_axes = []
        self._top_cursor_series = []
        ax.clear()
        # Every listed PV is fetched, but only the ticked ones are drawn — that way
        # a tick box acts instantly instead of triggering another archive read.
        series = [s for s in self._energy_data
                  if s.get("data") and self._pv_is_shown(s["channel"])]
        if not series:
            self._top_redrawing = False
            return
        # Each PV keeps its REAL values on its own Y-axis (units differ wildly:
        # J vs fs² vs …). Axes are split between the two sides so the labels stay
        # readable: 1→L, 2→L+R, 3→2×L+R, 4→2×L+2×R (left fills first).
        active_ch = self._active_search_channel()
        n = len(series)
        n_left = (n + 1) // 2
        sides = ["left"] * n_left + ["right"] * (n - n_left)

        axes_for_series = []
        left_i = right_i = 0
        for side in sides:
            if side == "left":
                if left_i == 0:
                    a = ax                      # base axis owns the primary left spine
                else:
                    a = ax.twinx()
                    a.yaxis.set_label_position("left")
                    a.yaxis.set_ticks_position("left")
                    a.spines["left"].set_position(("outward", 55 * left_i))
                    a.spines["right"].set_visible(False)
                    self._top_extra_axes.append(a)
                left_i += 1
            else:
                a = ax.twinx()
                if right_i > 0:
                    a.spines["right"].set_position(("outward", 55 * right_i))
                self._top_extra_axes.append(a)
                right_i += 1
            axes_for_series.append((a, side))

        for s, (a, side) in zip(series, axes_for_series):
            # A whole day of a fast PV is half a million samples, so the sort and
            # the timestamp conversion run in numpy — one point at a time took
            # ~25 s for that many and looked like the panel had hung.
            raw   = s["data"]
            t_ns  = np.fromiter((t for t, _ in raw), dtype=np.int64, count=len(raw))
            vals  = np.fromiter((v for _, v in raw), dtype=float, count=len(raw))
            order = np.argsort(t_ns, kind="stable")
            t_ns, vals = t_ns[order], vals[order]
            # Onto the compressed axis: one block per picked window, NaN-separated.
            times, plot_vals, cur_times, cur_vals = self._tmap.trace(t_ns, vals)
            is_active = (s["channel"] == active_ch)
            # Colour is looked up per PV on every draw, so it survives a reload, a
            # day with no data for one PV and any change to the PV list.
            col = self._trace_colour(s["channel"])
            s["color"] = col
            # Archived values hold until the next sample (zero-order hold), so a
            # step-after line reflects the real signal — no false linear ramps.
            # Sample dots help on a sparse trace; on a dense one they merge into a
            # solid band and only make every pan and zoom slower, so drop them.
            a.plot(times, plot_vals, "-", drawstyle="steps-post",
                   lw=2.0 if is_active else 1.0,
                   color=col, alpha=0.9,
                   marker="." if len(times) <= 20000 else "None", ms=3,
                   label=s["label"], zorder=5 if is_active else 3)
            a.set_ylabel(s["label"], color=col)
            a.tick_params(axis="y", colors=col)
            spine = "left" if side == "left" else "right"
            a.spines[spine].set_color(col)
            self._top_cursor_series.append({
                "label": s["label"], "color": col, "axis": a,
                "side": side, "times": cur_times, "vals": cur_vals,
            })

        self._style_time_axis(ax)
        ax.set_title("Drag to select time region(s), then click Analyze")
        ax.grid(True, alpha=0.25)
        self._paint_region_spans(ax)
        self._paint_window_joins(ax)
        self._install_top_cursor_artists()
        self._top_redrawing = False
        if self._top_user_xlim is not None:
            ax.set_xlim(self._top_user_xlim)
        if self._top_user_ylim is not None:
            ax.set_ylim(self._top_user_ylim)
        self._canvas_top.draw_idle()

    def _install_span(self):
        if self._span is not None:
            self._span.set_active(False)
            self._span = None
        self._span = SpanSelector(
            self._ax_top,
            self._on_span,
            "horizontal",
            useblit=False,                 # reliable rendering of the committed span
            props=dict(alpha=0.20, facecolor="#90CAF9"),
            interactive=False,
        )
        # If pan/zoom is active, keep span suspended until Select is re-chosen
        if hasattr(self, '_act_select') and not self._act_select.isChecked():
            self._span.set_active(False)

    def _add_region(self, t_start: int, t_end: int) -> dict:
        """Append one region and paint it, without touching the rest of the UI.

        The single place a region is built, so a drag and a double-click cannot
        drift apart. The caller repaints the canvas and rebuilds the list.
        """
        rid = self._region_seq
        self._region_seq += 1
        color = _REGION_COLORS[rid % len(_REGION_COLORS)]
        r = {
            "id": rid, "t_start": t_start, "t_end": t_end, "color": color,
            "visible": True, "expanded": False, "show_individual": False,
            "analyzed": False, "n": 0,
        }
        self._regions.append(r)
        # add only the new span (keeps current zoom/pan — nothing else changes)
        for x0, x1 in self._tmap.clip(t_start, t_end):
            self._ax_top.axvspan(x0, x1, alpha=0.25, color=color, zorder=0)
        return r

    def _keep_drag_parts(self, parts: "list[tuple[int, int]]"):
        """Drop the days a drag only clipped. Returns (kept, dropped).

        A piece survives if it holds at least _EDGE_KEEP_FRAC of the drag's
        longest piece, or covers at least _FULL_DAY_FRAC of its own day's loaded
        window — the second test is what saves a day loaded with a short window
        (30 min beside a neighbour's 11 h) from being read as an accidental clip.
        The longest piece is always kept, so a drag never ends up marking nothing.
        """
        if len(parts) < 2:
            return list(parts), []
        durs    = [b - a for a, b in parts]
        biggest = max(durs)
        floor   = biggest * _EDGE_KEEP_FRAC
        keep, drop = [], []
        for (a, b), dur in zip(parts, durs):
            own = self._tmap.window_len_ns(a)
            full_day = own > 0 and dur >= own * _FULL_DAY_FRAC
            if dur >= floor or full_day:
                keep.append((a, b))
            else:
                drop.append((a, b))
        if not keep:                       # cannot happen with the rule above, but
            i = durs.index(biggest)        # never leave the operator with nothing
            keep = [parts[i]]
            drop = [p for j, p in enumerate(parts) if j != i]
        return keep, drop

    def _on_span(self, xmin: float, xmax: float):
        # The axis is in seconds now, not matplotlib date numbers, so the old
        # 1e-9 guard was one nanosecond wide and let every click through as a
        # region.
        if xmax - xmin < _MIN_SPAN_S:
            return
        tmap = self._tmap
        if not tmap:
            return
        t_start = tmap.from_x(xmin)
        t_end   = tmap.from_x(xmax)
        # A drag that crosses a join covers time that is not on the axis. One
        # region spanning it would silently average two different days together,
        # so it becomes one spectrum per window instead.
        parts = tmap.split_ns(t_start, t_end) or [(t_start, t_end)]
        # ...but a day the drag merely clipped by a few pixels is not a spectrum
        # the operator asked for.
        parts, dropped = self._keep_drag_parts(parts)
        parts = [(a, b) for a, b in parts if (b - a) / 1e9 >= _MIN_SPAN_S] or parts
        added = []
        for p_start, p_end in parts:
            self._add_region(p_start, p_end)
            added.append((p_start, p_end))
        self._canvas_top.draw_idle()
        self._rebuild_regions_ui()
        self._update_action_buttons()
        if dropped:
            days = ", ".join(sorted({_fmt_date(a) for a, _ in dropped}))
            trimmed = f"  The overhang into {days} was ignored."
        else:
            trimmed = ""
        if len(added) == 1:
            a, b = added[0]
            self._set_status(
                f"Spectrum {len(self._regions)} added: "
                f"{_fmt_date(a)} {_fmt_hms(a)} – {_fmt_hms(b)}.{trimmed}  "
                f"Add more or click Analyze."
            )
        else:
            self._set_status(
                f"The selection crossed {len(added)} days, so {len(added)} "
                f"spectra were added: "
                + ",  ".join(f"{_fmt_date(a)} {_fmt_hms(a)}–{_fmt_hms(b)}"
                             for a, b in added)
                + f".{trimmed}  Add more or click Analyze."
            )

    def _on_top_dblclick(self, event):
        """Double-click inside a day → mark that whole day, edge to edge.

        Aiming a drag at one block is what produced the unwanted slivers in the
        first place; this needs no aiming. Same gate as the SpanSelector, so it is
        inert while Pan/Zoom has the mouse.
        """
        if not getattr(event, "dblclick", False) or event.button != 1:
            return
        # A second PV puts twinx axes on top of _ax_top, so inaxes is usually one
        # of THOSE, not _ax_top itself — an identity test here would kill the
        # feature as soon as a second search PV is ticked. They share the x axis,
        # so read x back through _ax_top's own transform instead.
        if event.inaxes is None or event.x is None:
            return
        if event.inaxes is not self._ax_top \
                and event.inaxes not in getattr(self, "_top_extra_axes", []):
            return
        act = getattr(self, "_act_select", None)
        if act is not None and not act.isChecked():
            return
        tmap = self._tmap
        if not tmap:
            return
        x_axis, _y = self._ax_top.transData.inverted().transform(
            (event.x, event.y))
        win = tmap.window_at_x(float(x_axis))
        if win is None:
            return
        a, b = win
        same = next((i for i, r in enumerate(self._regions)
                     if r["t_start"] == a and r["t_end"] == b), None)
        if same is not None:
            self._set_status(
                f"All of {_fmt_date(a)} is already marked as "
                f"{self._region_label(same)}."
            )
            return
        self._add_region(a, b)
        self._canvas_top.draw_idle()
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._set_status(
            f"Spectrum {len(self._regions)} added: all of {_fmt_date(a)} "
            f"({_fmt_hms(a)} – {_fmt_hms(b)}).  Add more or click Analyze."
        )

    # ── Regions UI ────────────────────────────────────────────────────────────
    def _region_label(self, i: int) -> str:
        return f"Spectrum {i + 1}"

    def _find_region(self, rid: int) -> dict | None:
        return next((r for r in self._regions if r["id"] == rid), None)

    def _rebuild_regions_ui(self):
        self._row_widgets.clear()
        while self._regions_lay.count():
            item = self._regions_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._regions:
            empty = QLabel("Drag on the search graph\nto select a spectrum.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #999; font-size: 12px; padding: 8px;")
            self._regions_lay.addWidget(empty)
            return

        for i, r in enumerate(self._regions):
            self._regions_lay.addWidget(self._make_region_row(i, r))
        self._refresh_compare_combos()

    def _make_region_row(self, i: int, r: dict) -> QWidget:
        rid = r["id"]
        box = QFrame()
        box.setStyleSheet(
            "QFrame { border-bottom: 1px solid #e6e6e6; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── header (clickable name + eye + delete) ─────────────────────
        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(4)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {r['color']}; font-size: 15px; border: none;")
        dot.setFixedWidth(16)

        chev = "▾" if r["expanded"] else "▸"
        btn_name = QPushButton(f"{chev}  {self._region_label(i)}")
        btn_name.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_name.setToolTip("Click to show/hide details")
        btn_name.clicked.connect(lambda _, x=rid: self._toggle_region_expanded(x))

        btn_eye = QPushButton("\U0001F441")          # 👁
        btn_eye.setCheckable(True)
        btn_eye.setChecked(r["visible"])
        btn_eye.setFixedSize(34, 30)
        btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_eye.toggled.connect(lambda on, x=rid: self._toggle_region_visible(x, on))

        btn_del = QPushButton("✕")              # ✕
        btn_del.setFixedSize(34, 30)
        btn_del.setToolTip("Remove this spectrum")
        btn_del.setStyleSheet(
            "QPushButton { color:#B71C1C; font-weight:700; font-size:18px; "
            "border:none; background:transparent; }"
            "QPushButton:hover { background:#ffe0e0; border-radius:3px; }"
        )
        btn_del.clicked.connect(lambda _, x=rid: self._delete_region(x))

        h.addWidget(dot)
        h.addWidget(btn_name, stretch=1)
        h.addWidget(btn_eye)
        h.addWidget(btn_del)
        v.addWidget(header)

        # ── details (collapsible) ──────────────────────────────────────
        details = self._build_region_details(r)
        details.setVisible(r["expanded"])
        v.addWidget(details)

        self._row_widgets[rid] = {"name": btn_name, "eye": btn_eye, "details": details,
                                  "metrics": getattr(details, "_metric_label", None)}
        self._apply_visibility_style(rid, r["visible"])
        return box

    def _build_region_details(self, r: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet(
            "QWidget { background: #fafafa; border: none; }"
            "QLabel { font-size: 12px; color: #333; border: none; }"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 4, 8, 8)
        lay.setSpacing(2)

        d0, d1 = _fmt_date(r["t_start"]), _fmt_date(r["t_end"])
        date_str = d0 if d0 == d1 else f"{d0} → {d1}"

        def add(text: str):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        add(f"<b>Date:</b> {date_str}")
        add(f"<b>Time:</b> {_fmt_hms(r['t_start'])} – {_fmt_hms(r['t_end'])}")

        if not r.get("analyzed"):
            note = QLabel("Not analyzed yet — click Analyze.")
            note.setStyleSheet("color: #999; font-style: italic; border: none;")
            lay.addWidget(note)
            return w

        add(f"<b># of spectra:</b> {r.get('n', 0)}")
        ea = r.get("energy_avg")
        if ea is not None:
            add(f"<b>SBW4 Output energy:</b> {ea:.3f} J  "
                f"<span style='color:#888'>(avg of {r.get('energy_n', 0)})</span>")
        else:
            add("<b>SBW4 Output energy:</b> n/a")

        orders = r.get("orders") or {}
        for label, _ in ORDER_PVS:
            val = orders.get(label)
            add(f"<b>{label}:</b> {round(val)}"
                if val is not None else f"<b>{label}:</b> n/a")

        # Spectral metrics (peak λ / centroid / FWHM / RMS bandwidth / area).
        # Filled in / refreshed by _update_metric_labels() after each redraw,
        # since they depend on the current range, method and smoothing.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ddd;")
        lay.addWidget(sep)
        metric_lbl = QLabel(self._metrics_html(r.get("_metrics")))
        metric_lbl.setWordWrap(True)
        metric_lbl.setVisible(bool(r.get("_metrics")))
        lay.addWidget(metric_lbl)
        w._metric_label = metric_lbl

        if r.get("n", 0) > 0 and r.get("stack") is not None:
            chk = QCheckBox(f"Show all {r['n']} spectra in graph")
            chk.setChecked(r.get("show_individual", False))
            chk.setStyleSheet("QCheckBox { font-size: 12px; color: #1565C0; border: none; }")
            chk.toggled.connect(
                lambda on, x=r["id"]: self._toggle_region_individual(x, on)
            )
            lay.addWidget(chk)
        return w

    def _apply_visibility_style(self, rid: int, visible: bool):
        refs = self._row_widgets.get(rid)
        if not refs:
            return
        refs["eye"].setToolTip("Visible — click to hide" if visible
                               else "Hidden — click to show")
        refs["eye"].setStyleSheet(
            "QPushButton { border: none; background: transparent; font-size: 18px;"
            + (" }" if visible else " color: #c4c4c4; }")
            + "QPushButton:hover { background: #e8f0fe; border-radius: 3px; }"
        )
        name_color = "#111" if visible else "#aaa"
        deco = "" if visible else "text-decoration: line-through;"
        refs["name"].setStyleSheet(
            "QPushButton { text-align: left; border: none; background: transparent; "
            f"font-weight: 700; font-size: 13px; color: {name_color}; {deco} padding: 2px; }}"
            "QPushButton:hover { color: #1565C0; }"
        )

    def _toggle_region_expanded(self, rid: int):
        r = self._find_region(rid)
        refs = self._row_widgets.get(rid)
        if not r or not refs:
            return
        r["expanded"] = not r["expanded"]
        refs["details"].setVisible(r["expanded"])
        i = self._regions.index(r)
        chev = "▾" if r["expanded"] else "▸"
        refs["name"].setText(f"{chev}  {self._region_label(i)}")
        self._update_expand_all_btn()

    def _toggle_all_expanded(self):
        """Master button: expand every spectrum's details at once (or collapse all)."""
        if not self._regions:
            return
        expand = any(not r["expanded"] for r in self._regions)
        self._regions_w.setUpdatesEnabled(False)
        for i, r in enumerate(self._regions):
            r["expanded"] = expand
            refs = self._row_widgets.get(r["id"])
            if refs:
                refs["details"].setVisible(expand)
                chev = "▾" if expand else "▸"
                refs["name"].setText(f"{chev}  {self._region_label(i)}")
        self._regions_w.setUpdatesEnabled(True)
        self._update_expand_all_btn()

    def _update_expand_all_btn(self):
        btn = getattr(self, "_btn_expand_all", None)
        if btn is None:
            return
        btn.setEnabled(bool(self._regions))
        all_expanded = bool(self._regions) and all(r["expanded"] for r in self._regions)
        btn.setText("⤡ Collapse all" if all_expanded else "⤢ Expand all")

    def _toggle_region_visible(self, rid: int, visible: bool):
        r = self._find_region(rid)
        if not r:
            return
        r["visible"] = visible
        self._apply_visibility_style(rid, visible)
        self._redraw_spectra()

    def _toggle_region_individual(self, rid: int, show: bool):
        r = self._find_region(rid)
        if not r:
            return
        r["show_individual"] = show
        self._redraw_spectra()

    def _delete_region(self, rid: int):
        r = self._find_region(rid)
        if r is None:
            return
        self._regions.remove(r)
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._refresh_energy_view()
        self._redraw_spectra()

    def _clear_regions(self):
        self._regions.clear()
        self._region_seq = 0
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._refresh_energy_view()
        self._draw_bot_empty()

    def _update_action_buttons(self):
        self._btn_analyze.setEnabled(any(not r["analyzed"] for r in self._regions))
        self._btn_export.setEnabled(any(r["analyzed"] for r in self._regions))
        self._update_expand_all_btn()
        # Curves appearing (or the last one being removed) changes which graph
        # deserves the room — see _wanted_split().
        if not self._split_user_set:
            self._update_top_visibility()

    def _refresh_energy_view(self):
        """Redraw the archive energy graph (live mode has no energy graph)."""
        if self._live:
            return
        if self._energy_data:
            self._draw_energy(reset_view=False)
            self._install_span()

    # ── Analysis ──────────────────────────────────────────────────────────────
    def _run_analysis(self):
        if self._busy:
            return
        todo = [r for r in self._regions if not r["analyzed"]]
        if not todo:
            return
        self._busy = True
        self._refresh_pill()
        self._btn_analyze.setEnabled(False)
        self._btn_pick_day.setEnabled(False)
        # snapshot only the immutable selection fields for the worker thread
        snap = [{"id": r["id"], "t_start": r["t_start"], "t_end": r["t_end"]}
                for r in todo]
        self._set_status(f"Loading spectra for {len(snap)} region(s)…")
        self._progress.setRange(0, len(snap))
        self._progress.setValue(0)
        self._progress.setVisible(True)

        sig = _Sig(self)
        sig.done.connect(self._on_analysis_done)
        sig.error.connect(self._on_analysis_error)
        sig.progress.connect(self._set_status)
        sig.progress_n.connect(self._on_analysis_progress)

        x_cached  = self._x_data
        spec_y_ch = self._spec_y_pv
        # Stamp this run. If the spectrum source changes while it is in flight the
        # stamp no longer matches and its results are thrown away instead of being
        # written into selections that now belong to a different channel.
        gen = self._analysis_gen

        def _work():
            x_data = x_cached
            if x_data is None:
                sig.progress.emit("Loading spectrometer X axis…")
                r0 = snap[0]
                x_data = self._resolve_x_data(
                    r0["t_start"] - int(10 * 60 * 1e9),
                    r0["t_end"]   + int(10 * 60 * 1e9),
                )

            results = []
            for i, r in enumerate(snap):
                if self._cancel.is_set():
                    break
                sig.progress.emit(
                    f"Spectrum {i+1}/{len(snap)}: "
                    f"{_fmt_hms(r['t_start'])}–{_fmt_hms(r['t_end'])}…"
                )
                wfs = _fetch_waveforms(spec_y_ch, r["t_start"], r["t_end"])
                fetch_err = _last_fetch_error.get(spec_y_ch)
                st = _compute_stats([a for _, a in wfs])

                # extra per-region scalars: output energy + dispersion orders
                energy_vals = [v for _, v in _fetch_scalars(
                    PV_ENERGY, r["t_start"], r["t_end"])]
                orders = {}
                for label, pv in ORDER_PVS:
                    vals = [v for _, v in _fetch_scalars(pv, r["t_start"], r["t_end"])]
                    orders[label] = float(np.mean(vals)) if vals else None

                res = {
                    "id": r["id"], "x": x_data, "orders": orders,
                    "energy_avg": float(np.mean(energy_vals)) if energy_vals else None,
                    "energy_n": len(energy_vals),
                    "fetch_error": fetch_err,
                }
                if st is None:
                    res.update({"mean": None, "median": None, "trimmed": None,
                                "sigma": None, "std": None, "stack": None,
                                "stack_ts": [], "n": 0})
                else:
                    res.update(st)
                    # _compute_stats keeps only the most common waveform length —
                    # repeat that filter on the timestamps so row i of the stack
                    # and stack_ts[i] are the same shot ("Every spectrum" names
                    # each curve by its time in the CSV).
                    common = st["stack"].shape[1]
                    res["stack_ts"] = [t for t, a in wfs if len(a) == common]
                results.append(res)
                sig.progress_n.emit(i + 1, len(snap))

            sig.done.emit((x_data, results, gen))

        self._cancel.clear()
        _bg(_work)

    def _on_analysis_progress(self, done: int, total: int):
        self._progress.setMaximum(total)
        self._progress.setValue(done)

    def _on_analysis_done(self, payload):
        x_data, results, gen = payload
        if gen != self._analysis_gen:
            # Obsolete run — the spectrum source changed while it was fetching.
            self._finish_analysis_ui()
            self._start_pending_reanalysis()
            return
        if x_data is not None:
            self._x_data = x_data
        by_id = {res["id"]: res for res in results}
        for r in self._regions:
            res = by_id.get(r["id"])
            if res is None:
                continue
            r.update(res)
            r["analyzed"] = True
        self._finish_analysis_ui()
        if self._cancel.is_set():
            self._rebuild_regions_ui()
            self._redraw_spectra()
            self._set_status(f"Analysis cancelled ({len(results)} region(s) done).")
            return
        n_total = sum(res["n"] for res in results)
        empties = [self._region_label(i) for i, r in enumerate(self._regions)
                   if r["id"] in by_id and r.get("n", 0) == 0]
        msg = f"Analysis done. {n_total} spectra total."
        if empties:
            msg += f"  No spectra in: {', '.join(empties)}."
            # An empty selection can also mean the archive read failed — say so
            # instead of leaving it looking like the shots were simply not taken.
            errs = {res["fetch_error"] for res in results if res.get("fetch_error")}
            if errs:
                msg += "  " + "   ".join(sorted(errs))
        note = self._x_fit_note()
        if note:
            msg += "  " + note
        # Also in the channel's own tooltip: the status line is overwritten by the
        # next thing that happens, and this is a permanent property of the archive.
        self._lbl_spec_base.setToolTip(
            f"X axis: {self._x_axis_summary()}\nY axis: {self._spec_y_pv}"
            + (f"\n{note}" if note else ""))
        self._set_status(msg)
        if self._chk_autofit.isChecked():
            self._auto_fit_range()      # snap range to the data span (signals blocked)
        self._rebuild_regions_ui()      # populate details (energy, orders, n)
        self._redraw_spectra()

    def _on_analysis_error(self, err: str):
        self._finish_analysis_ui()
        self._set_status(f"Error: {err}")
        self._start_pending_reanalysis()

    def _finish_analysis_ui(self):
        """Common tail of every analysis run, successful or not."""
        self._busy = False
        self._refresh_pill()
        self._btn_pick_day.setEnabled(True)
        self._progress.setVisible(False)
        self._update_action_buttons()

    # ── Spectra graph ─────────────────────────────────────────────────────────
    def _draw_bot_empty(self, msg: str = "Analyze a spectrum in the search graph"):
        ax = self._ax_bot
        self._bot_redrawing = True
        ax.clear()
        # An empty graph never carries a colour bar — hide the slot and hand the
        # width back, so the placeholder text is centred in the whole figure.
        self._colorbar_bot = None
        if self._cax_bot is not None:
            self._cax_bot.clear()
            self._cax_bot.set_visible(False)
        self._set_cbar_space(False)
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color="#aaa", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        # Pin the empty view instead of leaving matplotlib to invent one. An
        # unscaled axes autoscales itself to ±0.05 while it is being DRAWN, long
        # after this method returned, and that stray limit change used to be
        # filed away as the user's own zoom — which is what capped the intensity
        # axis at 0.05 once real spectra arrived.
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        self._install_bot_cursor_artists()
        self._bot_redrawing = False
        self._canvas_bot.draw_idle()

    # ── Display-option helpers ─────────────────────────────────────────────
    def _norm_mode(self) -> str:
        return {0: "none", 1: "peak", 2: "area"}.get(self._cmb_norm.currentIndex(), "none")

    def _band_kind(self) -> str:
        return "pct" if self._cmb_band.currentIndex() == 1 else "std"

    def _smooth_win(self) -> int:
        return self._sb_smooth.value() if self._chk_smooth.isChecked() else 0

    def _norm_scale(self, xp, yp, norm: str) -> float:
        if yp is None or len(yp) == 0:
            return 1.0
        if norm == "peak":
            m = float(np.max(yp))
            return m if m > 0 else 1.0
        if norm == "area":
            a = _trapz(yp, xp) if len(yp) > 1 else 0.0
            return a if a > 0 else 1.0
        return 1.0

    @staticmethod
    def _axis_for(x, n: int):
        """The X axis n intensity points are ACTUALLY drawn against.

        `x` when it fits the waveform, otherwise the plain sample number.

        THE one place that decides it. Every curve on the spectra graph — the
        averaged one, the faint individuals, every spectrum of the bundle, the
        bold picked one, the A/B comparison, the live traces — plus the metrics,
        the auto-fitted From/To range and the CSV export must agree, and they
        drifted apart twice already because the fallback was written out by hand
        in each of them.

        "Fits" is _fit_x_axis, NOT a bare length test: an axis the archiver
        truncated (SPIDER stores 2048 of its 4096 time points) is rebuilt from
        its own constant step instead of being thrown away. A bare length test
        was exactly the bug behind the bundle being drawn against sample numbers
        while everything else was on femtoseconds — the picked spectrum landed at
        0 fs next to a bundle piled up around "2000", and the fitted range then
        masked that bundle down to a slice of its own baseline, which is why the
        intensity axis stopped at 0.05 with peaks at 1.0."""
        xf = _fit_x_axis(x, n)
        return xf if xf is not None else np.arange(n, dtype=float)

    @staticmethod
    def _curve_x(r, n: int):
        """_axis_for for a region: the axis that region's curves are drawn on."""
        return SpectraWidget._axis_for(r.get("x") if r is not None else None, n)

    def _x_is_samples(self, r=None) -> bool:
        """True while the plot's X axis is sample numbers, not a measured axis.

        The axis title and the CSV's first column both have to own up to it —
        a graph labelled "Wavelength [nm]" that is really counting array
        positions is the kind of thing nobody notices for months."""
        regs = ([r] if r is not None else
                [q for q in self._regions
                 if q.get("analyzed") and q.get("visible", True)])
        seen = False
        for q in regs:
            y = q.get(self._curve_method()) if q else None
            if y is None:
                continue
            seen = True
            if _fit_x_axis(q.get("x"), len(np.asarray(y))) is not None:
                return False       # at least one real axis — never cry samples
        if seen:
            return True
        # No analysed spectra: live mode is the only thing on the graph, and
        # _plot_live_spectra has the same fallback on a mismatched _x_data.
        if self._live and self._live_buf:
            n = len(list(self._live_buf)[-1][1])
            return _fit_x_axis(self._x_data, n) is None
        return False

    def _prep_curve(self, x, y, smooth_win: int = 0):
        """Mask a curve to the current range, optionally smoothing it.
        Returns (xp, yp, mask)."""
        y = np.asarray(y, dtype=float)
        x = self._axis_for(x, len(y))
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp, yp = x[mask], y[mask]
        if smooth_win and xp.size:
            yp = _smooth(yp, smooth_win)
        return xp, yp, mask

    def _plot_spectrum(self, ax, x, avg, std, color, label, norm, band_lo, band_hi,
                       lw=1.6, smooth_win=0):
        xp, yp, mask = self._prep_curve(x, avg, smooth_win)
        ys = np.asarray(std, dtype=float)[mask] if std is not None else None
        lo = np.asarray(band_lo, dtype=float)[mask] if band_lo is not None else None
        hi = np.asarray(band_hi, dtype=float)[mask] if band_hi is not None else None
        scale = self._norm_scale(xp, yp, norm)
        if scale and scale != 1.0:
            yp = yp / scale
            if ys is not None: ys = ys / scale
            if lo is not None: lo = lo / scale
            if hi is not None: hi = hi / scale
        ax.plot(xp, yp, color=color, label=label, lw=lw)
        if lo is not None and hi is not None:
            ax.fill_between(xp, lo, hi, alpha=0.18, color=color)
        elif ys is not None:
            ax.fill_between(xp, yp - ys, yp + ys, alpha=0.18, color=color)

    def _plot_individual(self, ax, x, stack, color, ref_scale):
        """Overlay the individual spectra of a region as faint thin lines."""
        if stack is None or len(stack) == 0:
            return
        x = self._axis_for(x, stack.shape[1])
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]
        scale = ref_scale if (ref_scale and ref_scale > 0) else 1.0
        # subsample so we never draw thousands of lines
        rows = stack
        if len(stack) > MAX_INDIVIDUAL_LINES:
            step = int(np.ceil(len(stack) / MAX_INDIVIDUAL_LINES))
            rows = stack[::step]
        for row in rows:
            ax.plot(xp, row[mask] / scale, color=color, lw=0.4, alpha=0.15, zorder=0)

    def _method(self) -> str:
        return _METHODS.get(self._cmb_method.currentText(), "mean")

    def _is_single(self) -> bool:
        """True while the graph shows every measured spectrum instead of an average."""
        return self._method() == SINGLE_METHOD

    def _curve_method(self) -> str:
        """The stat key that stands in for one representative curve.

        "Every spectrum" is a way of drawing, not a way of combining: the metric
        block, the A/B comparison, the auto-fit and the CSV details still need a
        single curve per region, and that curve is the plain mean."""
        m = self._method()
        return "mean" if m == SINGLE_METHOD else m

    def _method_label(self) -> str:
        """The name shown for the current setting (metrics say where they come from)."""
        return "Mean" if self._is_single() else self._cmb_method.currentText()

    def _on_method_changed(self):
        """The variation band describes an average — with every spectrum on screen
        there is nothing for it to describe, so it is greyed out instead of being
        silently ignored."""
        single = self._is_single()
        self._chk_std.setEnabled(not single)
        self._cmb_band.setEnabled(not single)
        self._redraw_spectra()

    @staticmethod
    def _single_rows(stack):
        """Which rows of a region's stack are actually drawn/exported.

        Returns (row_indices, total). Above MAX_SINGLE_LINES the spectra are
        evenly thinned — every caller reports both numbers, never just the drawn
        one."""
        total = 0 if stack is None else len(stack)
        if total == 0:
            return np.empty(0, dtype=int), 0
        if total <= MAX_SINGLE_LINES:
            return np.arange(total), total
        # Spread the cap over the whole region instead of stepping by ceil(): with
        # 9007 shots a step of 4 threw away a quarter of the allowance and drew
        # only 2252 of the 3000 that were allowed.
        idx = np.unique(np.linspace(0, total - 1, MAX_SINGLE_LINES).round().astype(int))
        return idx, total

    def _plot_all_spectra(self, ax, x, stack, color, norm, smooth_win, label):
        """Draw every spectrum of a region as its own curve.

        One LineCollection, not thousands of ax.plot() calls — with a day's worth
        of shots the per-line version takes minutes and then redraws just as slowly
        on every zoom. The legend gets one proxy line per region."""
        idx, total = self._single_rows(stack)
        if total == 0:
            return 0, 0
        # The same axis as every other curve (_axis_for). This used to be a bare
        # "len(x) != stack width -> sample numbers" test, and it is what put the
        # whole bundle on array positions while the averaged curve, the bold
        # picked spectrum, the metrics and the auto-fitted range were all on the
        # real axis.
        x = self._axis_for(x, stack.shape[1])
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]
        if xp.size == 0:
            return 0, total

        segs = []
        for i in idx:
            yp = np.asarray(stack[i], dtype=float)[mask]
            if smooth_win:
                yp = _smooth(yp, smooth_win)
            scale = self._norm_scale(xp, yp, norm)
            if scale and scale != 1.0:
                yp = yp / scale
            segs.append(np.column_stack((xp, yp)))

        n_drawn = len(segs)
        # Thin, faint lines when there are many; a handful of shots stay solid.
        alpha = float(np.clip(40.0 / max(1, n_drawn), 0.06, 0.9))
        lw    = 1.2 if n_drawn <= 20 else 0.5
        lc = LineCollection(segs, colors=[color], linewidths=lw, alpha=alpha, zorder=1)
        ax.add_collection(lc)
        # add_collection does not grow the data limits on its own
        ax.autoscale_view()
        # legend proxy — a LineCollection with an alpha this low is invisible there
        ax.plot([], [], color=color, lw=2.0, label=label)
        return n_drawn, total

    # ── "Every spectrum": pick one shot out of the bundle ──────────────────────
    # The bar under the search graph (_make_shot_bar) walks every drawn spectrum
    # and paints the one it is on over the bundle: its spectrum's colour at full
    # strength, on a white halo, plus a tag naming the time inside the graph (so
    # it still works in focus mode, where everything but this graph is hidden).
    #
    # The bold curve is painted ONLY by a blit, never by a full draw
    # (set_animated(True)). Three thousand curves take seconds to lay out, so a
    # redraw per step of the bar would be unusable — and an artist baked into the
    # blit background would leave a ghost behind every time the bar moved.

    def _single_items(self) -> list:
        """Every individual spectrum the graph is drawing, oldest first.

        One flat list across all visible analyzed spectra, sorted by the time the
        shot was measured — the same order as the search graph the regions were
        picked from, and it runs across day boundaries when several days are
        loaded. Each entry is {rid, k, ts, label}, where `k` is the row of that
        region's stack. The list is rebuilt after every draw, so hiding a region
        or re-analyzing can never leave a stale row number behind."""
        items: list = []
        if not self._is_single():
            return items
        for i, r in enumerate(self._regions):
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            idx, _total = self._single_rows(r.get("stack"))
            ts = r.get("stack_ts") or []
            label = self._region_label(i)
            for k in idx:
                k = int(k)
                items.append({"rid": r["id"], "k": k, "label": label,
                              "ts": int(ts[k]) if k < len(ts) else 0})
        items.sort(key=lambda it: (it["ts"], it["rid"], it["k"]))
        return items

    def _rebuild_single_browser(self):
        """Refresh the bar, keeping the user on the same shot.

        Called at the end of every bottom-graph redraw. The position is matched
        back by (region, stack row) and NOT by its number: hiding a region or
        changing the display renumbers the list, and a plain clamp would quietly
        move the user onto a different spectrum."""
        old = getattr(self, "_single_items_cache", [])
        pos_old = getattr(self, "_single_pos", 0)
        prev = ((old[pos_old]["rid"], old[pos_old]["k"])
                if 0 <= pos_old < len(old) else None)

        items = self._single_items()
        self._single_items_cache = items
        n = len(items)
        pos = 0
        if prev is not None and n:
            for j, it in enumerate(items):
                if (it["rid"], it["k"]) == prev:
                    pos = j
                    break
            else:
                pos = min(pos_old, n - 1)
        self._single_pos = pos

        # The times of every shot, for the bar's snap. Shots with no timestamp
        # cannot be placed on a time axis at all, so they are left out of the
        # lookup instead of piling up at position zero.
        ts_all = np.array([int(it.get("ts") or 0) for it in items], dtype=np.int64)
        keep = np.flatnonzero(ts_all > 0)
        self._single_ts_pos = keep
        self._single_ts_arr = ts_all[keep]

        self._update_shot_bar_visibility()
        # Without a loaded time axis there is nothing to pin the bar to, so it
        # cannot say where anything is: it is switched off rather than left
        # looking live and doing nothing. The ◀ ▶ buttons still work — they walk
        # the list itself and need no axis.
        self._sl_shot.setEnabled(n > 1 and bool(self._tmap))
        self._btn_single_prev.setEnabled(n > 1)
        self._btn_single_next.setEnabled(n > 1)
        self._sync_shot_bar()
        self._update_single_label()
        self._update_top_marker()

    def _update_shot_bar_visibility(self):
        """The bar is only there when it has something to do — one averaged curve
        on screen has nothing to step through.

        It does not have to check whether the search graph is shown: the bar lives
        inside that graph's panel, so minimising the graph takes the bar with it.
        That is deliberate — a bar whose whole job is to point at a place on that
        graph is meaningless without it."""
        box = getattr(self, "_shot_bar_box", None)
        if box is not None:
            box.setVisible(self._is_single())

    def _update_single_label(self):
        """The caption beside the bar: which shot, when, from where."""
        items = getattr(self, "_single_items_cache", [])
        if not items:
            self._lbl_single.setText("Nothing to step through yet — analyze a spectrum.")
            return
        pos = min(getattr(self, "_single_pos", 0), len(items) - 1)
        it = items[pos]
        self._lbl_single.setText(
            f"{pos + 1} of {len(items)}   ·   {self._single_when(it)}"
            f"   ·   {it['label']}"
        )

    @staticmethod
    def _single_when(it) -> str:
        """The shot's own date + time. Never day-scoped: with several days loaded,
        two neighbouring positions can sit on different dates."""
        ts = it.get("ts") or 0
        return f"{_fmt_date(ts)} {_fmt_hms(ts)}" if ts else "time unknown"

    def _single_current(self):
        """(region, stack row, entry) for the spectrum the bar is on."""
        items = getattr(self, "_single_items_cache", [])
        pos = getattr(self, "_single_pos", 0)
        if not items or not (0 <= pos < len(items)):
            return None, None, None
        it = items[pos]
        r = self._find_region(it["rid"])
        stack = r.get("stack") if r else None
        if stack is None or it["k"] >= len(stack):
            return None, None, None
        return r, it["k"], it

    def _single_curve(self, r, k):
        """One shot of a region, prepared exactly as the bundle behind it.

        Same X axis, same mask, same smoothing, same normalization as
        _plot_all_spectra — otherwise the bold curve would sit at a different
        place than the shot it is supposed to be naming."""
        stack = r["stack"]
        x = self._curve_x(r, stack.shape[1])
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]
        yp = np.asarray(stack[k], dtype=float)[mask]
        win = self._smooth_win()
        if win and xp.size:
            yp = _smooth(yp, win)
        scale = self._norm_scale(xp, yp, self._norm_mode())
        if scale and scale != 1.0:
            yp = yp / scale
        return xp, yp

    def _install_single_hl_artists(self):
        """(Re)create the bold "this one" curve on the spectra plot.

        Called from _install_bot_cursor_artists, i.e. after every ax.clear(), for
        the same reason the crosshair is: clear() detaches the artists, and drawing
        a detached one raises 'NoneType has no attribute dpi'.

        animated=True keeps all three out of every full draw, so the blitted
        background stays clean and _blit_bot() is the only thing that paints them.
        That is also why _savefig_bot switches animation off around savefig — a
        normal draw skips animated artists, and the picture would lose the curve."""
        # BLACK on a thin white outline, not the spectrum's own colour. Measured by
        # rendering: 3000 curves of one colour make a solid blue band, and a bold
        # line of that same blue inside a white halo reads as a white gap with a
        # faint core — the halo wins. Black is a colour the bundle never has, so
        # the curve is unmistakable whatever the colouring mode does; which
        # spectrum it came from is on the tag, not in the ink.
        ax = self._ax_bot
        halo, = ax.plot([], [], color="#ffffff", lw=4.4, solid_capstyle="round",
                        zorder=8, visible=False, animated=True)
        line, = ax.plot([], [], color="#000000", lw=2.4, solid_capstyle="round",
                        zorder=9, visible=False, animated=True)
        tag = ax.text(
            0.015, 0.985, "", transform=ax.transAxes, ha="left", va="top",
            fontsize=9, color="#111111", zorder=12, visible=False, animated=True,
            bbox=dict(boxstyle="round,pad=0.3", fc="#ffffff", ec="#555555",
                      alpha=0.92, linewidth=0.8),
        )
        self._single_hl = {"halo": halo, "line": line, "tag": tag}

    def _single_hl_list(self) -> list:
        """The three highlight artists, or an empty list before the graph exists."""
        a = getattr(self, "_single_hl", None)
        return [a["halo"], a["line"], a["tag"]] if a else []

    def _blit_bot(self):
        """Repaint the spectra plot's overlay only (crosshair + bold curve).

        Falls back to a full draw until the cursor has captured a background."""
        fn = getattr(self, "_bot_blit_fn", None)
        if fn is not None:
            try:
                fn()
                return
            except Exception:
                pass
        canvas = getattr(self, "_canvas_bot", None)
        if canvas is not None:
            canvas.draw_idle()

    def _update_single_highlight(self, blit: bool = True):
        """Point the bold curve at the bar's spectrum, or hide it."""
        arts = self._single_hl_list()
        if not arts:
            return
        halo, line, tag = arts
        r = k = it = None
        chk = getattr(self, "_chk_single_hl", None)
        if self._is_single() and chk is not None and chk.isChecked():
            r, k, it = self._single_current()
        if r is None:
            for a in arts:
                a.set_visible(False)
        else:
            xp, yp = self._single_curve(r, k)
            on = bool(xp.size)
            halo.set_data(xp, yp)
            halo.set_visible(on)
            line.set_data(xp, yp)
            line.set_visible(on)
            n = len(getattr(self, "_single_items_cache", []))
            pos = getattr(self, "_single_pos", 0)
            tag.set_text(f" {it['label']}  ·  {self._single_when(it)}  ·  "
                         f"{pos + 1} of {n} ")
            # The tag's border carries the spectrum's colour, so the bold black
            # curve still says which bundle it came out of.
            colors = getattr(self, "_region_colors_cache", None) or {}
            tag.get_bbox_patch().set_edgecolor(
                colors.get(r["id"], r.get("color", "#555555")))
            tag.get_bbox_patch().set_linewidth(2.0)
            tag.set_color("#111111")
            tag.set_visible(on)
        if blit:
            self._blit_bot()

    def _on_single_hl_toggled(self, _on: bool):
        self._update_single_highlight()
        self._update_top_marker()

    # ── The shot bar: time <-> bar, and the snap onto real measurements ────────

    def _shot_bar_x_span(self):
        """The stretch of the search graph's axis the bar covers: exactly what the
        graph is showing. Zoom the graph and the bar follows, so "same X on the
        graph = same X on the bar" holds at every zoom."""
        ax = getattr(self, "_ax_top", None)
        if ax is None:
            return None
        try:
            x0, x1 = (float(v) for v in ax.get_xlim())
        except Exception:
            return None
        if not (np.isfinite(x0) and np.isfinite(x1)) or x1 <= x0:
            return None
        return x0, x1

    def _shot_bar_geom(self):
        """(bar, x of the canvas's left edge in the bar's own pixels, dpi ratio).

        None while the widgets have no geometry yet — during construction, and
        whenever the panel is hidden."""
        sl     = getattr(self, "_sl_shot", None)
        canvas = getattr(self, "_canvas_top", None)
        if sl is None or canvas is None or sl.width() <= 1 or canvas.width() <= 1:
            return None
        ratio = getattr(canvas, "device_pixel_ratio", 1) or 1
        off = sl.mapFromGlobal(canvas.mapToGlobal(QPoint(0, 0))).x()
        return sl, off, ratio

    # Time and bar position are converted THROUGH THE PIXEL the graph draws that
    # instant on (ax.transData), not through a proportion of the axis. That is
    # rule 1 said in code, and it also absorbs the last of the rounding: the bar's
    # margins have to be whole pixels, so its travel can never be an exact match
    # for the plot box, and a proportional mapping inherited that error and grew
    # it towards the ends of the bar. Going through the pixel leaves only Qt's own
    # half-pixel. The proportional form is kept as the fallback for when there is
    # no geometry to measure yet.

    def _shot_bar_value_from_ts(self, ts) -> "int | None":
        """Bar value whose handle lands on this instant. None if it cannot be
        placed (no time on the shot, or no axis yet)."""
        span = self._shot_bar_x_span()
        tmap = self._tmap
        if span is None or not tmap or not ts:
            return None
        x0, x1 = span
        x = tmap.to_x_clamped(int(ts))
        g = self._shot_bar_geom()
        if g is not None:
            sl, off, ratio = g
            try:
                px = float(self._ax_top.transData.transform((x, 0.0))[0]) / ratio + off
                return int(np.clip(sl.value_at_pixel(px), 0, _SHOT_BAR_MAX))
            except Exception:
                pass
        return int(round(float(np.clip((x - x0) / (x1 - x0), 0.0, 1.0)) * _SHOT_BAR_MAX))

    def _shot_bar_ts_from_value(self, value: int) -> "int | None":
        """The instant a bar value points at, on the compressed time axis."""
        span = self._shot_bar_x_span()
        tmap = self._tmap
        if span is None or not tmap:
            return None
        x0, x1 = span
        g = self._shot_bar_geom()
        if g is not None:
            sl, off, ratio = g
            try:
                px = (sl.pixel_at_value(int(value)) - off) * ratio
                x = float(self._ax_top.transData.inverted().transform((px, 0.0))[0])
                return int(tmap.from_x(float(np.clip(x, x0, x1))))
            except Exception:
                pass
        return int(tmap.from_x(x0 + (x1 - x0) * (int(value) / _SHOT_BAR_MAX)))

    def _nearest_shot_pos(self, ts: int) -> "int | None":
        """The shot closest in time to this instant.

        NEAREST, never "the newest at or before". Turning a time into a bar value
        and back truncates, so an at-or-before lookup lands on the shot BEFORE the
        one the handle was put on — every time, in the same direction. On a fast
        run that is a visible jump backwards on every single move."""
        arr = getattr(self, "_single_ts_arr", None)
        idx = getattr(self, "_single_ts_pos", None)
        if arr is None or idx is None or arr.size == 0:
            return None
        j = int(np.searchsorted(arr, int(ts), side="left"))
        if j <= 0:
            j = 0
        elif j >= arr.size:
            j = arr.size - 1
        elif abs(int(arr[j - 1]) - int(ts)) <= abs(int(arr[j]) - int(ts)):
            j -= 1
        return int(idx[j])

    def _sync_shot_bar(self):
        """Put the handle on the shot that is picked, without re-triggering the snap."""
        sl = getattr(self, "_sl_shot", None)
        if sl is None:
            return
        _r, _k, it = self._single_current()
        v = self._shot_bar_value_from_ts(it.get("ts") if it else None)
        if v is None:
            return
        sl.blockSignals(True)
        sl.setValue(v)
        sl.blockSignals(False)

    def _go_to_shot(self, pos: int):
        """Show shot number `pos`: one blit per graph, no re-layout of anything."""
        n = len(getattr(self, "_single_items_cache", []))
        if not n:
            return
        self._single_pos = int(np.clip(pos, 0, n - 1))
        self._sync_shot_bar()
        self._update_single_label()
        self._update_single_highlight()
        self._update_top_marker()

    def _on_shot_bar_moved(self, value: int):
        """The bar was dragged, clicked or stepped.

        The raw position is never kept: it is resolved to the nearest measured
        shot and the handle is written back onto that shot's own place. So the
        handle can never rest between two measurements, and a stretch of the axis
        where nothing was measured is simply unreachable — the handle sticks at
        the last shot before it and reappears at the first shot after it."""
        ts = self._shot_bar_ts_from_value(value)
        if ts is None:
            return
        pos = self._nearest_shot_pos(ts)
        if pos is None:
            return
        self._go_to_shot(pos)

    def _step_single(self, step: int):
        """◀ / ▶ / wheel / arrow keys — exactly one shot, clamped to the ends."""
        n = len(getattr(self, "_single_items_cache", []))
        if not n:
            return
        pos = int(np.clip(getattr(self, "_single_pos", 0) + step, 0, n - 1))
        self._go_to_shot(pos)
        self._keep_shot_in_view()

    def _page_single(self, direction: int):
        """PageUp / PageDown — a twentieth of the list at a time."""
        n = len(getattr(self, "_single_items_cache", []))
        if n:
            self._step_single(int(direction) * max(1, n // 20))

    def _keep_shot_in_view(self):
        """Bring the picked shot back onto the graph if stepping walked off it.

        Only ever needed when the user has zoomed in: the handle must stay under
        its shot, so if the shot leaves the visible stretch the graph slides over
        instead (same width, the shot in the middle)."""
        tmap = self._tmap
        ax = getattr(self, "_ax_top", None)
        span = self._shot_bar_x_span()
        if ax is None or span is None or not tmap:
            return
        _r, _k, it = self._single_current()
        if not it or not it.get("ts"):
            return
        x = tmap.to_x_clamped(int(it["ts"]))
        x0, x1 = span
        if x0 <= x <= x1:
            return
        w = x1 - x0
        lo, hi = tmap.xlim()
        new_x0 = float(np.clip(x - w / 2.0, lo, max(lo, hi - w)))
        ax.set_xlim(new_x0, new_x0 + w)
        self._canvas_top.draw_idle()

    # ── Rule 1: the bar's travel is pinned to the plot box ────────────────────

    def _schedule_shot_bar_pin(self):
        """Re-pin once Qt has finished its own layout.

        Deferred, and only one deferral in flight: the pin is driven from the
        canvas's draw_event, which fires several times in a row while a window is
        being dragged."""
        if getattr(self, "_shot_bar_pin_pending", False):
            return
        self._shot_bar_pin_pending = True
        QTimer.singleShot(0, self._pin_shot_bar)

    def _pin_shot_bar(self):
        """Line the bar's travel up with the plot box, to the pixel.

        The bar's row is given left and right margins that put value 0 on the
        plot box's left edge and the top value on its right edge. Both are pulled
        in by the handle's own inset, because value 0 puts the handle's CENTRE
        half a handle in from the groove's end — without that the whole travel is
        short by one handle width and the alignment drifts across the bar.

        Re-run from the canvas's draw_event, which is the one hook that covers
        every way the plot box can move: a resize, a splitter drag, a redraw, and
        a per-PV Y axis appearing on the right or longer tick labels on the left.
        """
        self._shot_bar_pin_pending = False
        sl     = getattr(self, "_sl_shot", None)
        row    = getattr(self, "_shot_bar_row", None)
        ax     = getattr(self, "_ax_top", None)
        canvas = getattr(self, "_canvas_top", None)
        if sl is None or row is None or ax is None or canvas is None:
            return
        lay = row.layout()
        if lay is None or row.width() <= 0:
            return
        try:
            bb = ax.get_window_extent()
        except Exception:
            return
        # get_window_extent is in physical pixels; Qt margins are logical ones.
        # Without the ratio the bar is off by a quarter of the width at 125 %.
        ratio = getattr(canvas, "device_pixel_ratio", 1) or 1
        # The canvas and the bar are separate widgets, so the plot box's x has to
        # be carried into the bar row's own coordinates.
        x_off = row.mapFromGlobal(canvas.mapToGlobal(QPoint(0, 0))).x()
        gx, _travel, hwid = _slider_metrics(sl)
        # The handle's centre never reaches the groove's own ends: it stops half a
        # handle short at each, and the groove itself may be inset by gx.
        inset = gx + int(round(hwid / 2.0))
        left  = int(round(x_off + bb.x0 / ratio)) - inset
        right = row.width() - int(round(x_off + bb.x1 / ratio)) - inset
        left, right = max(0, left), max(0, right)
        if (left, right) == getattr(self, "_shot_bar_margins", None):
            return                      # nothing moved — do not restart the layout
        self._shot_bar_margins = (left, right)
        lay.setContentsMargins(left, 0, right, 0)
        # The travel just changed, so the handle's pixel has to be recomputed too.
        self._sync_shot_bar()

    def _savefig_bot(self, path: str):
        """Save the spectra plot, bold curve included.

        The highlight artists are animated so the shot bar can blit them, and a
        normal draw skips animated artists — so they are switched back on for the
        length of the save."""
        arts = [a for a in self._single_hl_list() if a.get_visible()]
        for a in arts:
            a.set_animated(False)
        try:
            self._fig_bot.savefig(path, dpi=150, bbox_inches="tight")
        finally:
            for a in arts:
                a.set_animated(True)

    def _color_order_label(self) -> "str | None":
        """The dispersion-order key the current colour mode maps onto, or None for
        the default selection-order colouring."""
        return {"gdd": "GDD", "tod": "TOD"}.get(self._color_mode)

    def _compute_region_colors(self) -> dict:
        """Map each region id → colour. 'order' keeps each spectrum's own palette
        colour; 'gdd'/'tod' map the dispersion value onto a rainbow scale so spectra
        with similar GDD/TOD share a colour (low value → blue end, high → red end).
        Also stores self._colorbar_info for use by _redraw_spectra()."""
        default = {r["id"]: r["color"] for r in self._regions}
        order_label = self._color_order_label()
        self._colorbar_info = None
        if order_label is None:
            return default
        vis = [r for r in self._regions
               if r.get("analyzed") and r.get("visible", True)
               and (r.get("orders") or {}).get(order_label) is not None]
        if not vis:
            return default
        vals = [float(r["orders"][order_label]) for r in vis]
        vmin, vmax = min(vals), max(vals)
        span = (vmax - vmin) or 1.0
        try:
            cmap = matplotlib.colormaps["rainbow"]
        except Exception:
            cmap = _mpl_cm.get_cmap("rainbow")
        colors = dict(default)
        for r in vis:
            colors[r["id"]] = cmap((float(r["orders"][order_label]) - vmin) / span)
        self._colorbar_info = {"cmap": cmap, "vmin": vmin, "vmax": vmax, "label": order_label}
        return colors

    def _intensity_label(self, norm: str) -> str:
        return {"peak": "Intensity (norm. to peak)",
                "area": "Intensity (norm. to area)"}.get(norm, "Intensity")

    def _metrics_html(self, m: dict) -> str:
        """Format spectral metrics dict as a small HTML block for region details.

        Names the averaging method, because every number below is computed from
        whichever average is selected in Display."""
        if not m:
            return ""
        def val(v): return self._fmt_x_value(v)
        def sci(v): return f"{v:.3g}"    if v is not None else "n/a"
        # "Peak λ" is a wavelength name. On a time axis the same number is a
        # position in the pulse and the RMS width is a duration, not a bandwidth.
        _, _, sym = self._x_names()
        samples = self._x_is_samples()
        peak_lbl = "Peak sample" if samples else f"Peak {sym}"
        wide_lbl = "RMS width" if (samples or sym != "λ") else "RMS bw"
        return (
            f"<span style='color:#888'>from {self._method_label()}</span><br>"
            f"<b>{peak_lbl}:</b> {val(m.get('peak_wl'))} "
            f"<span style='color:#888'>@ {sci(m.get('peak_int'))}</span><br>"
            f"<b>Centroid:</b> {val(m.get('centroid'))}<br>"
            f"<b>FWHM:</b> {val(m.get('fwhm'))} &nbsp; "
            f"<b>{wide_lbl}:</b> {val(m.get('rms_bw'))}<br>"
            f"<b>Area:</b> {sci(m.get('area'))}"
        )

    def _update_metric_labels(self):
        """Refresh the per-region metric labels without rebuilding the whole UI."""
        for r in self._regions:
            refs = self._row_widgets.get(r["id"])
            if not refs:
                continue
            lbl = refs.get("metrics")
            if lbl is None:
                continue
            m = r.get("_metrics")
            lbl.setText(self._metrics_html(m) if m else "")
            lbl.setVisible(bool(m))

    def _refresh_compare_combos(self):
        """Repopulate the A/B comparison combos from the analyzed regions,
        preserving the current selection where possible."""
        analyzed = [(i, r) for i, r in enumerate(self._regions) if r.get("analyzed")]
        for cmb in (self._cmb_cmp_a, self._cmb_cmp_b):
            prev = cmb.currentData()
            cmb.blockSignals(True)
            cmb.clear()
            for i, r in analyzed:
                cmb.addItem(self._region_label(i), r["id"])
            if prev is not None:
                idx = cmb.findData(prev)
                if idx >= 0:
                    cmb.setCurrentIndex(idx)
            cmb.blockSignals(False)
        # default B to the second region when nothing was chosen yet
        if len(analyzed) >= 2 and self._cmb_cmp_b.currentIndex() == self._cmb_cmp_a.currentIndex():
            self._cmb_cmp_b.setCurrentIndex(1)

    def _on_x_range_edited(self, *_):
        """User typed a new From/To: drop any saved manual zoom so the typed range
        actually takes effect (otherwise _bot_user_xlim would override it), then redraw."""
        self._bot_user_xlim = None
        self._redraw_spectra()

    def _signal_span(self, x, y) -> "tuple | None":
        """Return the (lo, hi) wavelength span of (x, y) that actually contains
        signal (1% of peak above the baseline), or None when there is no signal."""
        y = np.asarray(y, dtype=float)
        x = self._axis_for(x, len(y))
        if y.size == 0:
            return None
        base = float(np.median(np.sort(y)[:max(1, len(y) // 5)]))
        peak = float(np.max(y))
        if peak <= base:
            return None
        idx = np.where(y > base + 0.01 * (peak - base))[0]
        if not idx.size:
            return None
        return float(x[idx[0]]), float(x[idx[-1]])

    def _apply_fit_span(self, lo: float, hi: float):
        """Write a fitted span into the From/To spinboxes (padded a touch) and drop
        any saved manual zoom so the new range actually takes effect."""
        pad = 0.02 * (hi - lo) if hi > lo else 1.0
        lo, hi = lo - pad, hi + pad
        self._sb_x_min.blockSignals(True)
        self._sb_x_max.blockSignals(True)
        self._sb_x_min.setValue(int(np.floor(lo)))
        self._sb_x_max.setValue(int(np.ceil(hi)))
        self._sb_x_min.blockSignals(False)
        self._sb_x_max.blockSignals(False)
        self._bot_user_xlim = None

    def _auto_fit_range(self):
        """Set From/To to the wavelength span that actually contains signal,
        across all analyzed regions (selected averaging method)."""
        method = self._curve_method()
        lo_c, hi_c = [], []
        for r in self._regions:
            if not r.get("analyzed"):
                continue
            y = r.get(method)
            if y is None:
                continue
            span = self._signal_span(r.get("x"), y)
            if span:
                lo_c.append(span[0])
                hi_c.append(span[1])
        if not lo_c:
            return
        self._apply_fit_span(min(lo_c), max(hi_c))

    def _auto_fit_live_range(self):
        """Fit From/To to the live data on the current (possibly custom) X axis,
        so a wavelength axis outside the default 700–900 nm is not masked away."""
        if not self._live_buf:
            return
        n_avg = self._sb_live_n.value()
        arrs  = [a for _, a in list(self._live_buf)[-n_avg:]]
        st = _compute_stats(arrs)
        if st is None:
            return
        curve = st[self._curve_method()]
        span = self._signal_span(self._x_data, curve)
        if span is None:
            # No clear signal: fall back to the full X-axis span so it is at least
            # visible. The FITTED axis, not the stored one — a truncated axis's own
            # min/max cover half the shot and would mask the other half away.
            xf = _fit_x_axis(self._x_data, len(np.asarray(curve)))
            if xf is not None and xf.size:
                span = (float(np.min(xf)), float(np.max(xf)))
        if span:
            self._apply_fit_span(*span)

    def _single_draw_estimate(self) -> int:
        """How many individual curves the next draw would put on the graph."""
        return sum(len(self._single_rows(r.get("stack"))[0])
                   for r in self._regions
                   if r.get("analyzed") and r.get("visible", True))

    def _redraw_spectra(self):
        """Wait cursor around a heavy draw.

        Thousands of curves take a couple of seconds to lay out and paint, and
        every Normalize / Smooth / range click comes back through here — without
        the cursor the window just looks stuck."""
        heavy = self._is_single() and self._single_draw_estimate() > 500
        if heavy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._redraw_spectra_now()
        finally:
            if heavy:
                QApplication.restoreOverrideCursor()

    def _redraw_spectra_now(self):
        norm       = self._norm_mode()
        single     = self._is_single()
        band_on    = self._chk_std.isChecked() and not single
        band_kind  = self._band_kind()
        smooth_win = self._smooth_win()
        method     = self._method()
        curve_m    = self._curve_method()
        x_min, x_max = self._sb_x_min.value(), self._sb_x_max.value()
        color_for   = self._compute_region_colors()  # also sets self._colorbar_info
        # Kept for the bold "this one" curve, which is painted by a blit long after
        # this method has returned and must use the very same colours.
        self._region_colors_cache = color_for
        order_label = self._color_order_label()
        ax = self._ax_bot
        self._bot_redrawing = True
        # The colour bar is never removed — its axes is permanent, we just wipe it.
        self._colorbar_bot = None
        if self._cax_bot is not None:
            self._cax_bot.clear()
            self._cax_bot.set_visible(False)
        if self._twin_bot is not None:
            self._twin_bot.remove()
            self._twin_bot = None
        ax.clear()
        any_drawn = False
        single_shown = 0     # spectra actually drawn / available, "Every spectrum" only
        single_total = 0

        # analyzed regions (skip hidden ones — visibility "eye" toggle)
        for i, r in enumerate(self._regions):
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            center = r.get(curve_m)
            if center is None:
                continue
            # metrics computed on the masked, smoothed (un-normalized) curve
            xp_m, yp_m, _ = self._prep_curve(r.get("x"), center, smooth_win)
            r["_metrics"] = _spectral_metrics(xp_m, yp_m)
            col = color_for.get(r["id"], r["color"])
            m_short = _METHOD_SHORT.get(method, method)
            label = f"{self._region_label(i)} · {m_short} (n={r.get('n', 0)})"
            if order_label is not None:
                v = (r.get("orders") or {}).get(order_label)
                if v is not None:
                    label = (f"{self._region_label(i)}  {order_label}="
                             f"{round(float(v))} · {m_short}")
            if single:
                # every shot in the region, no averaged curve at all
                stack = r.get("stack")
                idx, total = self._single_rows(stack)
                n_drawn = len(idx)
                count = (f"{total} spectra" if n_drawn >= total
                         else f"{n_drawn} of {total} spectra drawn")
                head = self._region_label(i)
                if order_label is not None:
                    v = (r.get("orders") or {}).get(order_label)
                    if v is not None:
                        head = f"{head}  {order_label}={round(float(v))}"
                drawn, tot = self._plot_all_spectra(
                    ax, r.get("x"), stack, col, norm, smooth_win,
                    f"{head} · {count}")
                single_shown += drawn
                single_total += tot
                if drawn:
                    any_drawn = True
                continue
            band_lo = band_hi = std_arg = None
            if band_on and band_kind == "pct":
                band_lo, band_hi = r.get("p10"), r.get("p90")
            elif band_on:
                std_arg = r.get("std")
            if r.get("show_individual") and r.get("stack") is not None:
                ref_scale = self._norm_scale(xp_m, yp_m, norm)
                self._plot_individual(ax, r.get("x"), r["stack"], col, ref_scale)
            self._plot_spectrum(ax, r.get("x"), center, std_arg, col, label, norm,
                                 band_lo, band_hi, smooth_win=smooth_win)
            any_drawn = True

        # live: only the last N shots (newest red, older faint blue, average black)
        if self._live and self._live_buf:
            n_avg  = self._sb_live_n.value()
            buf = list(self._live_buf)[-n_avg:]
            self._plot_live_spectra(ax, buf, norm)
            st = _compute_stats([a for _, a in buf])
            # _compute_stats keeps only the most common waveform length, so the
            # average can silently rest on fewer shots than were handed to it.
            # Record both counts and let the status line own up to the difference.
            self._live_slice_n = len(buf)
            self._live_used_n  = st["n"] if st is not None else 0
            # "Every spectrum" means exactly that — no averaged curve on top.
            if st is not None and not single:
                band_lo = band_hi = std_arg = None
                if band_on and band_kind == "pct":
                    band_lo, band_hi = st.get("p10"), st.get("p90")
                elif band_on:
                    std_arg = st["std"]
                self._plot_spectrum(ax, self._x_data, st[curve_m], std_arg,
                                    "#000000", f"Live {curve_m} (n={st['n']})",
                                    norm, band_lo, band_hi, lw=2.4, smooth_win=smooth_win)
            any_drawn = True

        # comparison curve (difference / ratio of two analyzed regions)
        if self._chk_compare.isChecked():
            any_drawn = self._plot_comparison(ax, curve_m, smooth_win) or any_drawn

        if any_drawn:
            # Say what the axis really is. With no resolved axis the curves are
            # drawn against the sample number, and calling that "Wavelength [nm]"
            # turns sample 2045 into "Peak λ 2045 nm" — a number that looks
            # measured and is not. The unit comes from the channel, so the SPIDER
            # time domain is labelled "Time [fs]" and not nanometres either.
            ax.set_xlabel(self._x_title())
            ax.set_ylabel(self._intensity_label(norm))
            if single:
                head = "Live spectra — every shot" if self._live else "Every spectrum"
                if single_total:
                    head += (f" ({single_total} measured)" if single_shown >= single_total
                             else f" ({single_shown} of {single_total} drawn)")
                ax.set_title(head)
            else:
                ax.set_title(("Live spectra — " if self._live else "Averaged spectra — ")
                             + self._cmb_method.currentText())
            ax.set_xlim(x_min, x_max)
            ax.grid(True, alpha=0.25)
            ax.legend(fontsize=9)
            if self._colorbar_info is not None and self._cax_bot is not None:
                sm = _mpl_cm.ScalarMappable(
                    cmap=self._colorbar_info["cmap"],
                    norm=_mpl_colors.Normalize(
                        vmin=self._colorbar_info["vmin"],
                        vmax=self._colorbar_info["vmax"],
                    ),
                )
                sm.set_array([])
                self._cax_bot.set_visible(True)
                self._set_cbar_space(True)
                self._colorbar_bot = self._fig_bot.colorbar(sm, cax=self._cax_bot)
                self._colorbar_bot.set_label(self._colorbar_info["label"], fontsize=9)
                self._colorbar_bot.ax.tick_params(labelsize=8)
            else:
                self._set_cbar_space(False)
            # ax.clear() above detached the crosshair artists — recreate them so a
            # queued cursor redraw doesn't draw an orphaned Text (NoneType .dpi crash).
            self._install_bot_cursor_artists()
            # Y range last: it is measured from the finished graph, and the
            # crosshair's line at zero must already be there to be skipped.
            self._fit_bot_ylim(ax)
            # _measured_y_range came back empty = the curves exist but the From/To
            # range kept none of their points. Say so instead of showing a blank
            # graph with an invented ±0.05 axis.
            if self._measured_y_range(ax) is None:
                self._draw_bot_empty(self._range_misses_data_msg())
        else:
            # Nothing was drawn. With spectra in hand that means the same thing:
            # the range does not cover them (a bundle drops out here, one point
            # earlier than an averaged curve does).
            self._draw_bot_empty(self._range_misses_data_msg()
                                 if self._drawn_x_span() is not None else
                                 "Analyze a spectrum in the search graph")

        self._bot_redrawing = False
        if self._bot_user_xlim is not None:
            ax.set_xlim(self._bot_user_xlim)
        if self._bot_user_ylim is not None:
            ax.set_ylim(self._bot_user_ylim)
        self._canvas_bot.draw_idle()
        # The shot bar's list is rebuilt here, not when the display box changes:
        # hiding a spectrum, re-analyzing and switching the method all change what
        # is on the graph, and all of them come through this one method.
        self._rebuild_single_browser()
        self._update_metric_labels()

    def _plot_comparison(self, ax, method: str, smooth_win: int) -> bool:
        """Overlay the difference (A−B) or ratio (A÷B) of two analyzed regions.
        Difference is drawn on the main axis; ratio on a right-hand twin axis."""
        ra = self._find_region(self._cmb_cmp_a.currentData())
        rb = self._find_region(self._cmb_cmp_b.currentData())
        if not ra or not rb or ra is rb:
            return False
        ya, yb = ra.get(method), rb.get(method)
        if ya is None or yb is None:
            return False
        xa, ca, _ = self._prep_curve(ra.get("x"), ya, smooth_win)
        # interpolate B onto A's masked wavelength grid
        xb_full = ra.get("x") if (rb.get("x") is None) else rb.get("x")
        yb = np.asarray(yb, dtype=float)
        xb = self._axis_for(xb_full, len(yb))
        if smooth_win and yb.size:
            yb = _smooth(yb, smooth_win)
        cb = np.interp(xa, xb, yb)
        if xa.size == 0:
            return False
        if self._cmb_cmp_mode.currentIndex() == 1:    # ratio on twin axis
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(cb != 0, ca / cb, np.nan)
            self._twin_bot = ax.twinx()
            self._twin_bot.plot(xa, ratio, color="#6A1B9A", lw=1.8, ls="--",
                                label="A ÷ B")
            self._twin_bot.set_ylabel("A ÷ B ratio", color="#6A1B9A", fontsize=9)
            self._twin_bot.tick_params(axis="y", labelcolor="#6A1B9A", labelsize=8)
        else:                                          # difference on main axis
            ax.plot(xa, ca - cb, color="#000000", lw=1.8, ls="--", label="A − B")
        return True

    def _plot_live_spectra(self, ax, buf, norm):
        """Older shots faint light-blue; the newest measured spectrum solid red."""
        arrs = [a for _, a in buf]
        if not arrs:
            return
        x = self._axis_for(self._x_data, len(arrs[-1]))
        mask = (x >= self._sb_x_min.value()) & (x <= self._sb_x_max.value())
        xp = x[mask]

        def _y(a):
            yp = a[mask]
            scale = self._norm_scale(xp, yp, norm)
            return yp / scale if scale and scale != 1.0 else yp

        older = arrs[:-1]
        if len(older) > MAX_INDIVIDUAL_LINES:
            step = int(np.ceil(len(older) / MAX_INDIVIDUAL_LINES))
            older = older[::step]
        for a in older:
            if len(a) == len(x):
                # lighter blue, a bit less transparent than before (alpha 0.5 -> 0.6)
                ax.plot(xp, _y(a), color="#6AA0E0", lw=0.6, alpha=0.6, zorder=1)

        newest = arrs[-1]
        if len(newest) == len(x):
            ax.plot(xp, _y(newest), color="#D32F2F", lw=2.7, alpha=1.0,
                    zorder=3, label="Newest live shot")

    # ── Live mode ─────────────────────────────────────────────────────────────
    def _toggle_live(self):
        if self._live:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._live = True
        self._live_buf = deque(maxlen=LIVE_BUF_MAX)
        self._btn_live_start.setText("⏹  Stop Live")
        self._btn_live_start.setStyleSheet(_BTN_DANGER)
        self._blink_on = False     # _blink_tick flips it → starts bright
        self._blink_timer.start(600)
        self._blink_tick()
        self._update_stop_button()

        # Preload a short window of recent history so there is something to average
        # immediately, then keep polling forward. (No "start time" to fiddle with —
        # the only control is "average last N".)
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        self._live_start_ns = now_ns - int(LIVE_HISTORY_S * 1e9)
        self._live_last_ns  = self._live_start_ns
        self._live_autofit_done = False

        self._set_status(
            f"Live started — preloading last {LIVE_HISTORY_S // 60} min of shots…"
        )

        # Resolve the (possibly custom) wavelength axis BEFORE polling Y, otherwise the
        # first frames are drawn on a bare index axis and masked away by the From/To range.
        if self._x_data is None:
            t0 = self._live_start_ns - int(10 * 60 * 1e9)
            t1 = self._live_start_ns + int(60 * 1e9)
            sig_x = _Sig(self)

            def _on_x(arr):
                if arr is not None:
                    self._x_data = arr
                if self._live:
                    self._live_tick()

            sig_x.done.connect(_on_x)

            def _fetch_x():
                sig_x.done.emit(self._resolve_x_data(t0, t1))

            _bg(_fetch_x)
        else:
            self._live_tick()

    def _stop_live(self):
        if not self._live:
            return
        self._live = False
        self._live_timer.stop()
        self._blink_timer.stop()
        self._refresh_pill()
        self._btn_live_start.setText("▶  Start Live")
        self._btn_live_start.setStyleSheet(_BTN_SUCCESS)
        self._set_status("Live stopped.")

    def _set_pill(self, text: str, fg: str, bg: str = "transparent",
                  border: str = "#ddd"):
        """Paint the state pill. Every state goes through here, so the outline and
        the text size can no longer differ between them — the old code wrote a
        bordered 13 px style at build time and a border-less 12 px one on stop, so
        the pill lost its outline the first time Live was stopped."""
        self._lbl_live_ind.setText(text)
        self._lbl_live_ind.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {fg}; "
            f"background: {bg}; border: 1px solid {border}; "
            "border-radius: 4px; padding: 4px;"
        )

    def _refresh_pill(self):
        """Show what the tab is actually doing right now.

        The pill used to track live mode only, so it read "idle" while an Analyze
        was fetching half a day of spectra."""
        if self._live:
            return                      # _blink_tick owns the pill while live runs
        if self._busy or self._loading:
            self._set_pill("⟳  Working…", "#0D47A1", "#e8f0fe", "#c3d7f5")
        else:
            self._set_pill("○  Idle", "#999")
        self._update_stop_button()

    def _update_stop_button(self):
        self._btn_stop.setEnabled(bool(self._live or self._busy or self._loading))

    def _blink_tick(self):
        """Toggle the 'live is running' indicator so the user sees it is alive."""
        self._blink_on = not self._blink_on
        if self._blink_on:
            self._set_pill("●  LIVE", "#2E7D32", "#e3f4e4", "#bfe0c0")
        else:
            self._set_pill("●  LIVE", "#bfe0c0", "transparent", "#e3f4e4")

    def _live_tick(self):
        if not self._live:
            return
        now_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
        start_ns = self._live_last_ns

        # live mode only needs spectra — the top search graph is hidden
        sig_y = _Sig(self)
        sig_y.done.connect(self._on_live_y)
        sig_y.error.connect(self._on_live_y_error)
        spec_y_ch = self._spec_y_pv

        def _work():
            try:
                wfs = _fetch_waveforms(spec_y_ch, start_ns, now_ns)
                sig_y.done.emit((now_ns, wfs))
            except Exception as ex:
                sig_y.error.emit(str(ex))

        _bg(_work)

    def _on_live_y(self, payload):
        now_ns, wfs = payload
        for t, arr in wfs:
            self._live_buf.append((t, arr))
        self._live_last_ns = now_ns
        # Redrawing the whole figure (ax.clear + re-plot up to MAX_INDIVIDUAL_LINES
        # traces + a full draw_idle) is expensive, and most ticks bring no new
        # shots — the poll just advances the clock. Skip the redraw on those ticks
        # so the GUI stays smooth; only rebuild when new spectra actually arrived.
        if wfs:
            # Once, on the first real live data: fit From/To to this X axis so a custom
            # wavelength axis outside the default 700–900 nm is not masked to nothing.
            if (not self._live_autofit_done and self._live_buf
                    and self._chk_autofit.isChecked()):
                self._auto_fit_live_range()
                self._live_autofit_done = True
            self._redraw_spectra()
        want  = self._sb_live_n.value()
        have  = len(self._live_buf)
        n_avg = min(want, have)
        msg = f"Live: averaging last {n_avg} of {have} buffered (N={want})"
        if n_avg < want:
            msg += " — filling up"
        used = self._live_used_n
        if used is not None and 0 <= used < self._live_slice_n:
            msg += f", {self._live_slice_n - used} skipped (different length)"
        self._set_status(f"{msg}  ({_fmt_hms(now_ns)})")
        if self._live:
            self._live_timer.start(LIVE_INTERVAL_S * 1000)

    def _on_live_y_error(self, err: str):
        self._set_status(f"Live error: {err}")
        if self._live:
            self._live_timer.start((LIVE_INTERVAL_S + 2) * 1000)

    # ── Export ────────────────────────────────────────────────────────────────
    @staticmethod
    def _fmt_full(v) -> str:
        """Plain decimal string with a period — never scientific notation, never
        a comma (a decimal comma would collide with Excel's column separator)."""
        try:
            s = f"{float(v):.6f}"
        except (TypeError, ValueError):
            return ""
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"

    def _live_export_spectra(self) -> list:
        """Live shots currently shown/averaged: the last N from the buffer."""
        if not self._live or not self._live_buf:
            return []
        n = self._sb_live_n.value()
        return list(self._live_buf)[-n:]

    def _export(self):
        analyzed = [(i, r) for i, r in enumerate(self._regions)
                    if r.get("analyzed") and r.get("n", 0) > 0]
        live = self._live_export_spectra()
        if not analyzed and not live:
            QMessageBox.information(
                self, "Export", "Nothing to export — analyze a spectrum or start live first."
            )
            return

        single = self._is_single()
        n_shots = (sum(self._export_rows(r.get("stack"))[1] for _, r in analyzed)
                   if single else 0)
        dlg = ExportDialog(len(analyzed), len(live), self._curve_method(), self,
                           single=single, n_shots=n_shots)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.options()
        if not (opts["data"] or opts["graph"]):
            return

        base, _ = QFileDialog.getSaveFileName(
            self, "Export — choose a base file name", "spectra_export",
            "All files (*)",
        )
        if not base:
            return
        base = os.path.splitext(base)[0]   # we append our own suffixes/extensions

        written: list[str] = []
        try:
            if opts["data"]:
                p = base + ".csv"
                self._export_csv(p, analyzed, live)
                written.append(p)
            if opts["graph"]:
                p = f"{base}_graph.{opts['graph_fmt']}"
                self._savefig_bot(p)
                written.append(p)
        except Exception as ex:
            QMessageBox.critical(self, "Export error", str(ex))
            return

        self._set_status("Exported: " + ", ".join(os.path.basename(p) for p in written))
        QMessageBox.information(self, "Export done", "Saved:\n" + "\n".join(written))

    @staticmethod
    def _export_rows(stack):
        """Which rows of a stack the CSV writes: all of them.

        Deliberately NOT _single_rows(). The graph thins above MAX_SINGLE_LINES
        because thousands of curves take seconds to lay out and repaint, but a
        column in a text file costs nothing — so "every spectrum" in the file
        means every spectrum, even when the picture on screen is a thinned
        sample of it. The details block says which of the two you are holding."""
        total = 0 if stack is None else len(stack)
        return np.arange(total), total

    def _export_csv(self, path: str, analyzed: list[tuple[int, dict]], live: list):
        """One CSV: a details block, a blank line, then the curve table.

        Columns: each analyzed region's averaged curve, plus every live shot
        (so 7 regions + 100 live shots export as 107 curves). With the display set
        to "Every spectrum" the region columns are the individual shots instead —
        one column per shot, headed by the time it was measured, and ALL of them:
        the graph's 3000-curve drawing cap does not apply to the file.

        The X column is the axis the graph is really drawn against (_curve_x), not
        self._x_data. Those two used to be able to disagree: a region with no
        resolved wavelength axis is plotted against the sample number, while
        _x_data still held a real axis from an earlier resolve — so the same peak
        sat at 2045 on screen and at -5.5 in the file.
        """
        method = self._curve_method()
        single = self._is_single()
        regs = [(i, r) for i, r in analyzed if r.get(method) is not None]

        # The export grid: a region's own axis, exactly as it is drawn. The first
        # region that HAS a real axis wins — with a mixed set, taking region 1's
        # sample numbers would have meant interpolating everyone else's
        # nanometres onto array positions.
        x = None
        if regs:
            pick = next((q for _, q in regs
                         if _fit_x_axis(q.get("x"),
                                        len(np.asarray(q[method]))) is not None),
                        regs[0][1])
            x = self._curve_x(pick, len(np.asarray(pick[method])))
        elif live:
            x = self._axis_for(self._x_data, len(live[-1][1]))
        if x is None or (not regs and not live):
            raise ValueError("No curve data to export.")
        x = np.asarray(x, dtype=float)
        nx = len(x)
        samples = self._x_is_samples()
        x_quantity, x_unit, x_sym = self._x_names()
        x_name = ("sample_number" if samples else
                  f"{x_quantity.lower()}_{x_unit}" if x_unit else x_quantity.lower())
        order_labels = [lbl for lbl, _ in ORDER_PVS]
        # live shots whose length matches the export axis
        live_ok = [(t, np.asarray(a, dtype=float)) for t, a in live if len(a) == nx]
        live_skipped = len(live) - len(live_ok)

        # "Every spectrum": one column per shot instead of the average + std pair.
        single_cols: dict = {}
        drawn_cap: dict = {}
        if single:
            for i, r in regs:
                stack = r.get("stack")
                idx, total = self._export_rows(stack)
                drawn_cap[i] = len(self._single_rows(stack)[0])
                ts = r.get("stack_ts") or []
                # A region whose waveform length differs from the export grid is
                # resampled onto it instead of being cut off at nx — that silently
                # blanked the tail of every such column.
                xr = self._curve_x(r, stack.shape[1]) if total else None
                regrid = xr if (total and stack.shape[1] != nx) else None
                cols = []
                for k in idx:
                    y = np.asarray(stack[k], dtype=float)
                    if regrid is not None:
                        y = np.interp(x, regrid, y)
                    cols.append((
                        (f"{self._region_label(i)} {_fmt_hms(ts[k])}"
                         if k < len(ts) else f"{self._region_label(i)} #{k + 1}"),
                        y,
                    ))
                single_cols[i] = (cols, total)

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            f.write("sep=;\r\n")          # tell Excel the delimiter (locale-proof)
            w = csv.writer(f, delimiter=";")

            # ── details block ───────────────────────────────────────────
            if samples:
                # Never let a sample number leave the program dressed as a unit.
                w.writerow(["# NOTE: no measured axis was resolved for these "
                            "spectra — the first column and every position / width "
                            "below are SAMPLE NUMBERS, not physical units."])
            else:
                fit_note = self._x_fit_note()
                if fit_note:
                    w.writerow([f"# NOTE: {fit_note}"])
            wide_lbl = "RMS width" if x_sym != "λ" else "RMS bandwidth"
            metric_cols = (["Peak sample", "Peak intensity", "Centroid [sample]",
                            "FWHM [samples]", "RMS width [samples]", "Area"]
                           if samples else
                           [f"Peak {x_sym} [{x_unit}]", "Peak intensity",
                            f"Centroid [{x_unit}]", f"FWHM [{x_unit}]",
                            f"{wide_lbl} [{x_unit}]", "Area"])
            metric_keys = ["peak_wl", "peak_int", "centroid", "fwhm", "rms_bw", "area"]
            w.writerow(["# Spectrum details"])
            w.writerow(["Spectrum", "Date", "Start", "End", "# of spectra",
                        "Method", "SBW4 Output energy [J]"] + order_labels + metric_cols)
            for i, r in regs:
                d0, d1 = _fmt_date(r["t_start"]), _fmt_date(r["t_end"])
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                ea = r.get("energy_avg")
                orders = r.get("orders") or {}
                m = r.get("_metrics") or {}
                meth_txt = method
                if single:
                    cols, total = single_cols.get(i, ([], 0))
                    meth_txt = f"every spectrum ({len(cols)})"
                    drawn = drawn_cap.get(i, len(cols))
                    if drawn < len(cols):
                        # The file is fuller than the picture; say so, or the two
                        # look like they disagree.
                        meth_txt += f", graph drew {drawn}"
                w.writerow([
                    self._region_label(i), date_str,
                    _fmt_hms(r["t_start"]), _fmt_hms(r["t_end"]),
                    r.get("n", 0), meth_txt,
                    self._fmt_full(ea) if ea is not None else "",
                ] + [self._fmt_full(orders.get(lbl)) if orders.get(lbl) is not None
                     else "" for lbl in order_labels]
                  + [self._fmt_full(m.get(k)) if m.get(k) is not None else ""
                     for k in metric_keys])
            if live_ok:
                t0, t1 = live_ok[0][0], live_ok[-1][0]
                d0, d1 = _fmt_date(t0), _fmt_date(t1)
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                live_txt = "individual"
                if live_skipped:
                    live_txt += f", {live_skipped} skipped (different length)"
                w.writerow([f"Live shots (last {len(live_ok)})", date_str,
                            _fmt_hms(t0), _fmt_hms(t1),
                            len(live_ok), live_txt, "",
                            *[""] * len(order_labels), *[""] * len(metric_cols)])

            w.writerow([])   # blank separator line

            # ── curve table ─────────────────────────────────────────────
            cmp_name, cmp_vals = self._export_comparison_curve(x, method)

            w.writerow(["# Curve data"])
            header = [x_name]
            for i, _ in regs:
                if single:
                    header.extend(name for name, _ in single_cols.get(i, ([], 0))[0])
                    continue
                header.append(f"{self._region_label(i)} ({method})")
                header.append(f"{self._region_label(i)} std")
            for t, _ in live_ok:
                header.append(f"Live {_fmt_hms(t)}")
            if cmp_name:
                header.append(cmp_name)
            w.writerow(header)

            for j in range(nx):
                row = [self._fmt_full(x[j])]
                for i, r in regs:
                    if single:
                        for _, a in single_cols.get(i, ([], 0))[0]:
                            row.append(self._fmt_full(a[j]) if j < len(a) else "")
                        continue
                    y, s = r.get(method), r.get("std")
                    row.append(self._fmt_full(y[j]) if y is not None and j < len(y) else "")
                    row.append(self._fmt_full(s[j]) if s is not None and j < len(s) else "")
                for _, a in live_ok:
                    row.append(self._fmt_full(a[j]))
                if cmp_vals is not None:
                    v = cmp_vals[j]
                    row.append("" if (v is None or np.isnan(v)) else self._fmt_full(v))
                w.writerow(row)

    def _export_comparison_curve(self, x, method: str):
        """Difference (A−B) or ratio (A÷B) of the two compared regions, sampled on
        the export wavelength grid. Returns (column_name, values) or (None, None)
        when comparison is off or the regions aren't both analyzed."""
        if not self._chk_compare.isChecked():
            return None, None
        ra = self._find_region(self._cmb_cmp_a.currentData())
        rb = self._find_region(self._cmb_cmp_b.currentData())
        if not ra or not rb or ra is rb:
            return None, None
        ya, yb = ra.get(method), rb.get(method)
        if ya is None or yb is None:
            return None, None
        ya = np.asarray(ya, dtype=float)
        yb = np.asarray(yb, dtype=float)
        xa = self._curve_x(ra, len(ya))
        xb = self._curve_x(rb, len(yb))
        xg = np.asarray(x, dtype=float)
        ca = np.interp(xg, xa, ya)
        cb = np.interp(xg, xb, yb)
        la = self._region_label(self._regions.index(ra))
        lb = self._region_label(self._regions.index(rb))
        if self._cmb_cmp_mode.currentIndex() == 1:
            with np.errstate(divide="ignore", invalid="ignore"):
                vals = np.where(cb != 0, ca / cb, np.nan)
            return f"{la} / {lb} (ratio)", vals
        return f"{la} - {lb} (difference)", ca - cb

    # ── Misc ──────────────────────────────────────────────────────────────────
    def _set_status(self, msg: str):
        self._lbl_status.setText(msg)

    def cancel_scan(self):
        """Stop everything: live streaming, a day load, an analysis in flight.

        Wired to the Stop button in the sidebar and to the suite closing down. The
        pending-re-analysis flag is dropped as well, or a queued re-run would start
        the moment the cancelled one unwinds."""
        self._cancel.set()
        self._reanalyze_pending = False
        self._stop_live()
        if self._busy or self._loading:
            self._busy = False
            self._loading = False
            self._progress.setVisible(False)
            self._btn_pick_day.setEnabled(True)
            self._update_action_buttons()
        self._refresh_pill()
        self._set_status("Stopped.")


# ── App stylesheet ────────────────────────────────────────────────────────────
_APP_STYLESHEET = """
QWidget      { background: #f3f3f3; color: #111;
               font-family: "Segoe UI", Arial, sans-serif; }
QLabel       { background: transparent; }
QPushButton  { padding: 5px 8px; border-radius: 3px; border: 1px solid #bbb; }
QPushButton:hover { background: #dde8ff; }
QGroupBox    { border: 1px solid #ccc; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #ccc; }
QTabBar::tab {
    background: #e8e8e8; color: #444;
    padding: 6px 18px; border: 1px solid #ccc;
    border-bottom: none; border-radius: 3px 3px 0 0;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #f3f3f3; color: #111; font-weight: 600; }
QTabBar::tab:hover    { background: #d8e8ff; }
QToolTip { background: #ffffcc; color: #111; border: 1px solid #aaa; padding: 4px; }
QScrollBar:vertical { width: 8px; background: #eee; }
QScrollBar::handle:vertical { background: #bbb; border-radius: 4px; }
"""


# ── Standalone entry point ────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(_APP_STYLESHEET)

    win = QMainWindow()
    win.setWindowTitle("Spectral analysis")
    # Keep this small enough to fit on a smaller / display-scaled monitor without the
    # window overflowing off-screen; the left panel scrolls and the graph is Expanding.
    win.setMinimumSize(860, 480)

    tabs = QTabWidget()
    tabs.setDocumentMode(True)
    widget = SpectraWidget()
    tabs.addTab(widget, "SPIDER spectra")
    win.setCentralWidget(tabs)

    # F11 focus mode. In the suite the key is owned by CSS Logger's main.py, which
    # forwards it here whenever the Spectra tab is the visible one — a single owner,
    # so Qt never has to resolve an ambiguous shortcut.
    _sc_focus = QShortcut(QKeySequence("F11"), win)
    _sc_focus.setContext(Qt.ShortcutContext.ApplicationShortcut)
    _sc_focus.activated.connect(widget.toggle_focus_mode)

    from PySide6.QtWidgets import QStatusBar
    status = QStatusBar()
    btn_stop = QPushButton("⏹ Stop")
    btn_stop.setStyleSheet(
        "QPushButton { background:#B71C1C; color:white; font-weight:700; "
        "padding:3px 12px; border-radius:3px; margin:2px; }"
        "QPushButton:hover { background:#7F0000; }"
    )
    btn_stop.clicked.connect(widget.cancel_scan)
    status.addPermanentWidget(btn_stop)
    win.setStatusBar(status)

    win.showMaximized()
    sys.exit(app.exec())
