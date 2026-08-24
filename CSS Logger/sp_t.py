"""
sp_t.py — SPIDER spectrometer spectral analysis

Archive workflow:
  1. Click "Load day" -> pick a date (or a multi-day range). The "Search data by"
     PV (SBW4 energy by default; add your own, e.g. Alpha output energy / GDD / TOD)
     is loaded into the top search graph.
  2. Drag on the search graph to select a time region (= one spectrum); repeat.
  3. Click "Analyze" -> averaged spectra appear in the bottom graph. Colour them by
     selection order or by GDD / TOD on a rainbow scale.

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

from PySide6.QtCore import (Qt, QObject, QTimer, Signal, QDate, QLocale,
                            QRect, QPoint, QEvent)
from PySide6.QtGui import (QAction, QColor, QCursor, QIcon, QPalette,
                           QShortcut, QKeySequence, QGuiApplication,
                           QPainter, QPen)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QCheckBox, QGroupBox, QScrollArea, QSizePolicy, QButtonGroup,
    QFileDialog, QDialog, QDialogButtonBox, QFrame, QSplitter,
    QCalendarWidget, QMessageBox, QMainWindow, QTabWidget, QComboBox,
    QProgressBar, QStyledItemDelegate, QAbstractItemView, QInputDialog,
    QToolButton, QMenu, QStyle, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QDoubleSpinBox,
    QGridLayout,
)

import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams['axes.facecolor']   = 'white'
matplotlib.rcParams['figure.facecolor'] = 'white'
import matplotlib.cm as _mpl_cm
import matplotlib.colors as _mpl_colors
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector

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


def _day_range_ns(qdate: QDate) -> tuple[int, int]:
    tz = _PRAGUE or timezone.utc
    d = date(qdate.year(), qdate.month(), qdate.day())
    start = datetime(d.year, d.month, d.day,  0,  0,  0, tzinfo=tz)
    end   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
    return int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)


# ── Cross-thread signal carrier ───────────────────────────────────────────────
class _Sig(QObject):
    done       = Signal(object)
    error      = Signal(str)
    progress   = Signal(str)
    progress_n = Signal(int, int)   # (done, total) for the progress bar


def _bg(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ── DatePickerDialog ──────────────────────────────────────────────────────────
# Calendar styling: gray day-name header, blue navigation bar (matches Image Tools).
_CAL_STYLE = """
QCalendarWidget QWidget { background: #ffffff; color: #111; }
QCalendarWidget QAbstractItemView:enabled {
    background: #ffffff; color: #111;
    selection-background-color: #1565C0; selection-color: white;
}
QCalendarWidget QWidget#qt_calendar_navigationbar { background: #eeeeee; }
QCalendarWidget QToolButton {
    color: #222; background: transparent;
    font-weight: 700; font-size: 13px;
    border-radius: 3px; padding: 3px 6px;
}
QCalendarWidget QToolButton:hover { background: #d0d0d0; }
QCalendarWidget QToolButton#qt_calendar_monthbutton {
    border: 1px solid #aaaaaa; background: #f5f5f5; padding: 2px 8px;
}
QCalendarWidget QToolButton#qt_calendar_monthbutton:hover { background: #e0e0e0; }
QCalendarWidget QSpinBox {
    color: #222; background: #eeeeee; border: none; font-weight: 700;
}
QCalendarWidget QMenu { color: #111; background: #fff; }
"""


class _WeekendDelegate(QStyledItemDelegate):
    """Paint calendar cells: selected=blue background, Sat/Sun=red text.

    Weekend detection uses the cell's real date, so spillover-month cells are
    coloured correctly too.
    initStyleOption strips State_Selected for cells not in _selected_keys so that
    Qt's own selection highlight (today after Clear, etc.) never bleeds through.
    """
    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()   # (year, month, day) tuples

    def _first_cell(self) -> "tuple[int, int]":
        """Row/column of the first *day* cell. Qt drops the header row when
        NoHorizontalHeader is set and the week-number column when
        NoVerticalHeader is set, so the grid does not always start at (1, 1)."""
        first_row = 1
        if (self._cal.horizontalHeaderFormat()
                == QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader):
            first_row = 0
        first_col = 1
        if (self._cal.verticalHeaderFormat()
                == QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader):
            first_col = 0
        return first_row, first_col

    def _date_for_index(self, index) -> "QDate | None":
        # The model knows the real date for in-month cells — always prefer it.
        d = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, QDate) and d.isValid():
            return d
        first_row, first_col = self._first_cell()
        if index.row() < first_row or index.column() < first_col:
            return None                       # header row / week-number column
        first = QDate(self._cal.yearShown(), self._cal.monthShown(), 1)
        if not first.isValid():
            return None
        # Column offset of the 1st within the first displayed week.
        offset = (first.dayOfWeek() - self._cal.firstDayOfWeek().value) % 7
        row = index.row() - first_row
        # Qt shifts the whole grid one week back when the 1st sits in the very
        # first column (QCalendarModel::dateForCell, MinimumDayOffset = 1), so
        # row 0 then shows the PREVIOUS week. Without this the painted days are
        # a week off (clicking one day highlighted a different one).
        if offset < 1:
            row -= 1
        start = first.addDays(-offset)
        return start.addDays(row * 7 + (index.column() - first_col))

    def set_selected(self, dates: "list[QDate]"):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        d = self._date_for_index(index)
        if d is not None and (d.year(), d.month(), d.day()) not in self._selected_keys:
            # Strip Qt's internal selection highlight (e.g. today's cell after Clear).
            option.state = option.state & ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index):
        d = self._date_for_index(index)
        if d is None:
            super().paint(painter, option, index)
            return
        is_weekend = d.dayOfWeek() in (6, 7)    # weekend from the date, never the column
        is_sel = (d.year(), d.month(), d.day()) in self._selected_keys
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        if is_sel:
            painter.save()
            painter.fillRect(option.rect, QColor("#1565C0"))
            painter.setPen(QColor("#ffcccc") if is_weekend else QColor("#ffffff"))
            painter.setFont(option.font)
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()
        else:
            super().paint(painter, option, index)
            if is_weekend:
                painter.save()
                painter.setPen(QColor("#cc0000"))
                painter.setFont(option.font)
                painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, text)
                painter.restore()


def _make_calendar(initial: "QDate | None" = None) -> "tuple[QFrame, QCalendarWidget]":
    """Return (wrapper_frame, cal).

    The wrapper contains:
      1. A custom gray QWidget row with day-name QLabels (Mon…Sun) — 100% reliable
         gray header regardless of PySide6/Fusion stylesheet interactions.
      2. The QCalendarWidget with the built-in header hidden.

    Weekend cells (Sat/Sun) — including spillover-month cells — are coloured red
    via _WeekendDelegate, which resolves each cell to its real date.
    """
    # ── QCalendarWidget (both built-in headers hidden — we supply our own nav + day row) ──
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(_CAL_STYLE)

    # Hide built-in navigation bar — we draw our own below so hdr_row sits directly above the grid.
    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()

    # Install weekend+selection delegate on the grid view.
    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal._wk_delegate = _WeekendDelegate(cal)
        view.setItemDelegate(cal._wk_delegate)

    # ── Custom navigation bar (light gray, month as button, year spinbox) ────
    _MONTHS = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

    nav_row = QWidget()
    nav_row.setAutoFillBackground(True)
    nav_pal = nav_row.palette()
    nav_pal.setColor(QPalette.ColorRole.Window, QColor("#eeeeee"))
    nav_row.setPalette(nav_pal)
    nav_lay = QHBoxLayout(nav_row)
    nav_lay.setContentsMargins(4, 3, 4, 3)
    nav_lay.setSpacing(4)

    prev_btn = QToolButton()
    prev_btn.setText("◀")
    prev_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    month_btn = QPushButton()
    month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet(
        "QPushButton { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " font-weight: bold; font-size: 12px; padding: 2px 10px; }"
        "QPushButton:hover { background: #e0e0e0; }"
    )

    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet(
        "QSpinBox { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " padding: 1px 4px; font-weight: bold; font-size: 12px; }"
    )
    year_spin.setFixedWidth(60)

    next_btn = QToolButton()
    next_btn.setText("▶")
    next_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    nav_lay.addWidget(prev_btn)
    nav_lay.addStretch()
    nav_lay.addWidget(month_btn)
    nav_lay.addWidget(year_spin)
    nav_lay.addStretch()
    nav_lay.addWidget(next_btn)

    def _update_nav():
        m, y = cal.monthShown(), cal.yearShown()
        month_btn.setText(_MONTHS[m - 1])
        year_spin.blockSignals(True)
        year_spin.setValue(y)
        year_spin.blockSignals(False)

    def _on_month_btn():
        menu = QMenu(month_btn)
        for i, name in enumerate(_MONTHS, 1):
            menu.addAction(name).setData(i)
        chosen = menu.exec(month_btn.mapToGlobal(month_btn.rect().bottomLeft()))
        if chosen:
            cal.setCurrentPage(cal.yearShown(), chosen.data())

    prev_btn.clicked.connect(cal.showPreviousMonth)
    next_btn.clicked.connect(cal.showNextMonth)
    month_btn.clicked.connect(_on_month_btn)
    year_spin.valueChanged.connect(lambda y: cal.setCurrentPage(y, cal.monthShown()))
    cal.currentPageChanged.connect(lambda _y, _m: _update_nav())
    _update_nav()

    # ── Custom gray day-name header ──────────────────────────────────────────
    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#757575"))
    hdr_row.setPalette(hdr_pal)
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0)
    hdr_lay.setSpacing(0)
    for name in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #111111; font-weight: 700; padding: 4px 0;")
        hdr_lay.addWidget(lbl, stretch=1)

    # ── Wrapper frame: [nav_row] [hdr_row] [cal grid] ───────────────────────
    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame { border: 1px solid #b0b0b0; border-radius: 3px; }")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0, 0, 0, 0)
    w_lay.setSpacing(0)
    w_lay.addWidget(nav_row)
    w_lay.addWidget(hdr_row)
    w_lay.addWidget(cal)

    return wrapper, cal


class DatePickerDialog(QDialog):
    """Single-calendar date picker.

    Plain click        → select exactly that one day (replaces the selection).
    Ctrl+click         → toggle that single day in/out of a multi-day selection.
    Ctrl+Shift+click   → range from the last click to the clicked day, XOR-ed into
                         the selection (so a repeat over the same range deselects
                         what it selected — an "anti-selection").
    Weekends (Sat/Sun) can ONLY be picked by a plain click — Ctrl and Ctrl+Shift
    skip them.

    Uses QCalendarWidget.clicked(QDate) — reliable across all PySide6 versions.
    """
    def __init__(self, parent=None, initial: QDate | None = None):
        super().__init__(parent)
        self.setWindowTitle("Select day(s)")
        self.setWindowIcon(QIcon())
        self.setModal(True)
        self.setMinimumWidth(330)

        self._last_click: QDate | None = None
        self._dates: list[QDate] = []

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        self._frame, self._cal = _make_calendar(initial)
        lay.addWidget(self._frame)

        self._lbl_info = QLabel("")
        self._lbl_info.setStyleSheet("color: #555; font-size: 11px;")
        lay.addWidget(self._lbl_info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        clear_btn = btns.addButton("Clear", QDialogButtonBox.ButtonRole.ResetRole)
        clear_btn.setToolTip("Deselect all days")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        clear_btn.clicked.connect(lambda: self._apply_selection([]))
        lay.addWidget(btns)

        self._cal.clicked.connect(self._on_date_clicked)

        if initial and initial.isValid():
            self._apply_selection([initial])
            self._last_click = initial

    # ── Click handler ─────────────────────────────────────────────────────────
    def _on_date_clicked(self, d: QDate):
        mods  = QApplication.keyboardModifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        new_dates = self._compute_click(d, ctrl, shift)
        self._last_click = d
        if new_dates is None:    # ignored (Ctrl on a weekend)
            self._lbl_info.setText("Weekends can only be selected by a plain click.")
            return
        self._apply_selection(new_dates)

    def _compute_click(self, d: QDate, ctrl: bool, shift: bool) -> "list[QDate] | None":
        """Pure selection logic. Returns the new selection, or None if the click
        should be ignored (Ctrl on a weekend)."""
        cur      = list(self._dates)
        cur_keys = {self._key(x) for x in cur}
        if ctrl and shift:
            # Range [anchor … d], weekdays only, XOR-ed into the current selection.
            anchor = self._last_click if self._last_click is not None else d
            rng = [x for x in self._date_range(anchor, d) if not self._is_weekend(x)]
            rng_keys = {self._key(x) for x in rng}
            keep = [x for x in cur if self._key(x) not in rng_keys]   # deselect overlap
            add  = [x for x in rng if self._key(x) not in cur_keys]   # select the rest
            new_dates = keep + add
        elif ctrl:
            # Toggle a single weekday; weekends are plain-click only.
            if self._is_weekend(d):
                return None
            if self._key(d) in cur_keys:
                new_dates = [x for x in cur if self._key(x) != self._key(d)]
            else:
                new_dates = cur + [d]
        else:
            # Plain click: exactly one day (weekends allowed).
            new_dates = [d]
        return sorted(new_dates, key=lambda x: (x.year(), x.month(), x.day()))

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _key(d: QDate) -> tuple:
        return (d.year(), d.month(), d.day())

    @staticmethod
    def _is_weekend(d: QDate) -> bool:
        return d.dayOfWeek() >= 6   # 6=Sat, 7=Sun

    @staticmethod
    def _date_range(d1: QDate, d2: QDate) -> "list[QDate]":
        if d2 < d1:
            d1, d2 = d2, d1
        days, d = [], d1
        while d <= d2:
            days.append(d)
            d = d.addDays(1)
        return days

    def _apply_selection(self, dates: "list[QDate]"):
        self._dates = dates
        delegate = getattr(self._cal, "_wk_delegate", None)
        if delegate is not None:
            delegate.set_selected(dates)
        if dates:
            self._cal.setSelectedDate(dates[-1])
        n = len(dates)
        if n == 0:
            self._lbl_info.setText("Click = 1 day · Ctrl+click = multi · Ctrl+Shift+click = range")
        elif n == 1:
            self._lbl_info.setText(f"Selected: {dates[0].toString('yyyy-MM-dd')}")
        else:
            self._lbl_info.setText(
                f"Selected: {dates[0].toString('yyyy-MM-dd')} → "
                f"{dates[-1].toString('yyyy-MM-dd')}  ({n} days)"
            )

    # ── Public API ────────────────────────────────────────────────────────────
    def selected_date(self) -> QDate:
        return self._dates[0] if self._dates else self._cal.selectedDate()

    def selected_dates(self) -> "list[QDate]":
        """Chronologically sorted list of selected days."""
        return list(self._dates) if self._dates else [self._cal.selectedDate()]


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

    def __init__(self, n_regions: int, n_live: int, method: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export results")
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        parts = []
        if n_regions:
            parts.append(f"{n_regions} spectra")
        if n_live:
            parts.append(f"{n_live} live shot(s)")
        hdr = QLabel("Export " + " + ".join(parts) + ":")
        hdr.setStyleSheet("font-weight: 700; font-size: 12px;")
        lay.addWidget(hdr)

        self._chk_data = QCheckBox(
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

        note = QLabel(
            "One CSV holds a details block (date, time, energy, dispersion orders) "
            "followed by the curve table. A 'sep=;' line and a decimal point let "
            "Excel open it directly in any locale."
        )
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

        _date_x = isinstance(ax.xaxis.get_major_formatter(), mdates.DateFormatter)

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
            note = QLabel("X axis uses datetime — adjust range with zoom/pan tools.")
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

        self._selected_day:     QDate | None             = None
        self._selected_days:    list[QDate]              = []   # multi-day support
        self._day_start_ns:     int                      = 0
        self._day_end_ns:       int                      = 0
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

        # ══ Step 1 — the day ═══════════════════════════════════════════
        card1, b1 = _step_card(1, "DAY")
        self._lbl_day = QLabel("No day selected")
        self._lbl_day.setWordWrap(True)
        self._lbl_day.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #263238; border: none;"
            " background: transparent;")
        b1.addWidget(self._lbl_day)
        self._btn_pick_day = QPushButton("\U0001F4C5  Load day…")
        self._btn_pick_day.setStyleSheet(_BTN_PRIMARY)
        self._btn_pick_day.setToolTip(
            "Open the calendar and load a day (or a range of days) of data into the search graph."
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
        lay.addWidget(card3)

        # ── Mode ───────────────────────────────────────────────────────
        g_mode = QGroupBox("Mode")
        g_mode.setStyleSheet(_GROUP_STYLE)
        row_m = QHBoxLayout(g_mode)
        self._btn_archive   = QPushButton("Archive")
        self._btn_live_mode = QPushButton("Live")
        self._btn_archive.setToolTip("Browse past data: load a day and select spectra from the archive.")
        self._btn_live_mode.setToolTip("Stream the latest spectra in real time and average the last N shots.")
        self._btn_archive.setCheckable(True)
        self._btn_live_mode.setCheckable(True)
        self._btn_archive.setChecked(True)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        self._mode_grp.addButton(self._btn_archive)
        self._mode_grp.addButton(self._btn_live_mode)
        row_m.addWidget(self._btn_archive)
        row_m.addWidget(self._btn_live_mode)
        lay.addWidget(g_mode)

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
        lay.addWidget(self._g_live)

        # (region selection list lives in the collapsible block, not here)

        # ── Display options ────────────────────────────────────────────
        # One grid, not five separate rows: the three labels line up in a column and
        # the section is several rows shorter. As five independent QHBoxLayouts every
        # combo started at a different x and the block wasted vertical space.
        g_disp = QGroupBox("Display")
        g_disp.setStyleSheet(_GROUP_STYLE)
        disp_l = QVBoxLayout(g_disp)
        disp_l.setSpacing(4)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        disp_l.addLayout(grid)

        grid.addWidget(QLabel("Average"), 0, 0)
        self._cmb_method = QComboBox()
        self._cmb_method.addItems(list(_METHODS.keys()))
        self._cmb_method.setToolTip(
            "How to combine the spectra in each region:\n"
            "• Mean — plain average\n"
            "• Median — robust against outlier shots\n"
            "• Trimmed mean 10% — drops the 10% lowest/highest values\n"
            "• Sigma-clipped mean — drops points beyond 3σ, then averages\n\n"
            "The active method is named in the graph title and in every legend "
            "entry — on clean spectra the four differ by a fraction of a percent, "
            "so the label is the only way to tell them apart."
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
        lay.addWidget(g_disp)

        # ── X range ────────────────────────────────────────────────────
        g_xr = QGroupBox("Spectrum range [nm]")
        g_xr.setStyleSheet(_GROUP_STYLE)
        xr_v = QVBoxLayout(g_xr)
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
        lay.addWidget(g_xr)

        # ── Compare two regions ────────────────────────────────────────
        g_cmp = QGroupBox("Compare regions")
        g_cmp.setStyleSheet(_GROUP_STYLE)
        cmp_l = QVBoxLayout(g_cmp)
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
        lay.addWidget(g_cmp)

        # ── Selected regions (list + analyze + progress) ───────────────
        # Lives here, under "Compare regions", to save horizontal space.
        lay.addWidget(self._make_region_panel(), stretch=1)

        # ── Export ─────────────────────────────────────────────────────
        self._btn_export = QPushButton("\U0001F4BE  Export results")
        self._btn_export.setEnabled(False)
        self._btn_export.setToolTip(
            "Export the analyzed spectra to a CSV (details + curves) and/or save the plot image."
        )
        lay.addWidget(self._btn_export)

        return sb

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

        def _on_draw(_evt):
            _hide_all()
            _state["bg"] = canvas.copy_from_bbox(fig.bbox)

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
            return f"{x:.4g} nm"

        def _process():
            _state["pending"] = False
            evt = _state["last_event"]
            bg  = _state["bg"]
            ca  = getattr(self, "_bot_cursor_artists", None)

            if evt is None or evt.inaxes is None or not ca:
                if bg:
                    canvas.restore_region(bg)
                    canvas.blit(fig.bbox)
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

            if bg:
                canvas.restore_region(bg)
                for artist in _artists():
                    if artist.get_visible() and artist.axes is not None:
                        artist.axes.draw_artist(artist)
                canvas.blit(fig.bbox)
            else:
                canvas.draw_idle()

        def _on_motion(evt):
            _state["last_event"] = evt
            if not _state["pending"]:
                _state["pending"] = True
                from PySide6.QtCore import QTimer
                QTimer.singleShot(16, _process)

        def _on_leave(_evt):
            _hide_all()
            if _state["bg"]:
                canvas.restore_region(_state["bg"])
                canvas.blit(fig.bbox)

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
            for a in _artists():
                a.set_visible(False)

        def _on_draw(_evt):
            _hide_all()
            _state["bg"] = canvas.copy_from_bbox(fig.bbox)

        def _fmt_v(v):
            av = abs(v)
            if v == 0 or (1e-3 <= av < 1e6):
                return f"{v:.5g}"
            return f"{v:.4e}"

        def _fmt_time(x):
            try:
                from zoneinfo import ZoneInfo
                return _mdates.num2date(x, tz=ZoneInfo("Europe/Prague")).strftime("%H:%M:%S")
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
                    canvas.restore_region(bg)
                    canvas.blit(fig.bbox)
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
            if bg:
                canvas.restore_region(bg)
                for a in _artists():
                    if a.get_visible():
                        a.axes.draw_artist(a)
                canvas.blit(fig.bbox)
            else:
                canvas.draw_idle()

        def _on_motion(evt):
            _state["last_event"] = evt
            if not _state["pending"]:
                _state["pending"] = True
                from PySide6.QtCore import QTimer
                QTimer.singleShot(16, _process)

        def _on_leave(_evt):
            _hide_all()
            if _state["bg"]:
                canvas.restore_region(_state["bg"])
                canvas.blit(fig.bbox)

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

        row_btns = QHBoxLayout()
        self._btn_clear_regs = QPushButton("Clear all")
        self._btn_clear_regs.setToolTip("Remove all selected spectra from the list.")
        self._btn_analyze    = QPushButton("✓  Analyze")
        self._btn_analyze.setEnabled(False)
        self._btn_analyze.setStyleSheet(_BTN_SUCCESS)
        self._btn_analyze.setToolTip(
            "Fetch and average the spectra in every not-yet-analyzed selection, then plot them."
        )
        row_btns.addWidget(self._btn_clear_regs)
        row_btns.addWidget(self._btn_analyze)
        v.addLayout(row_btns)

        # analysis progress bar (hidden until analysis runs)
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setFormat("Analyzing  %v / %m  (%p%)")
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #b0b0b0; border-radius: 4px; "
            "text-align: center; height: 20px; font-size: 11px; font-weight: 700; }"
            "QProgressBar::chunk { background: #2E7D32; border-radius: 3px; }"
        )
        v.addWidget(self._progress)
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
        self._cmb_method.currentIndexChanged.connect(self._redraw_spectra)
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

    def _connect_zoom_tracking(self):
        """Save user's pan/zoom state so redraws don't reset it."""
        def _mk_handler(ax, xlim_attr, ylim_attr, redrawing_attr):
            def _on_xlim(a):
                if not getattr(self, redrawing_attr, False):
                    setattr(self, xlim_attr, a.get_xlim())
            def _on_ylim(a):
                if not getattr(self, redrawing_attr, False):
                    setattr(self, ylim_attr, a.get_ylim())
            ax.callbacks.connect('xlim_changed', _on_xlim)
            ax.callbacks.connect('ylim_changed', _on_ylim)
        _mk_handler(self._ax_top, '_top_user_xlim', '_top_user_ylim', '_top_redrawing')
        _mk_handler(self._ax_bot, '_bot_user_xlim', '_bot_user_ylim', '_bot_redrawing')
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
                    "stack", "orders", "energy_avg", "energy_n", "n", "_metrics")

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
        # The PV *set* changed → reload all search PVs for the loaded day.
        self._update_active_card()
        self._btn_rem_pv.setEnabled(self._tbl_pvs.currentRow() >= 0)
        if not self._live and self._selected_days:
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
    def _pick_day(self):
        dlg = DatePickerDialog(self, initial=self._selected_day or QDate.currentDate())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # switch UI back to archive mode
        self._btn_archive.setChecked(True)
        self._set_live_mode(False)
        dates = dlg.selected_dates()
        self._selected_days = dates
        self._selected_day  = dates[0]
        if len(dates) == 1:
            self._lbl_day.setText(f"Day: {dates[0].toString('yyyy-MM-dd')}")
        else:
            self._lbl_day.setText(
                f"Days: {dates[0].toString('yyyy-MM-dd')} → "
                f"{dates[-1].toString('yyyy-MM-dd')}  ({len(dates)} days)")
        # Keep already-selected spectra across day changes — they carry absolute
        # timestamps and stay in the list (delete them via the ✕ in the list).
        self._load_day_energy()

    def _load_day_energy(self):
        days = self._selected_days or ([self._selected_day] if self._selected_day else [])
        if not days:
            return
        start_ns, _ = _day_range_ns(days[0])
        _, end_ns   = _day_range_ns(days[-1])
        self._day_start_ns, self._day_end_ns = start_ns, end_ns
        pvs = list(self._search_pvs)
        if not pvs:
            self._draw_top_empty("Add a search PV to plot")
            return
        self._set_status(f"Loading {len(pvs)} search PV(s)…")
        self._btn_pick_day.setEnabled(False)
        self._loading = True
        self._refresh_pill()
        self._progress.setRange(0, len(pvs))
        self._progress.setValue(0)
        self._progress.setFormat("Loading  %v / %m  PV(s)  (%p%)")
        self._progress.setVisible(True)

        sig = _Sig(self)
        sig.done.connect(self._on_energy_loaded)
        sig.error.connect(self._on_energy_error)
        sig.progress.connect(self._set_status)
        sig.progress_n.connect(self._on_analysis_progress)

        def _work():
            try:
                series = []
                for i, (lbl, ch) in enumerate(pvs):
                    if self._cancel.is_set():
                        break
                    sig.progress.emit(f"Loading {i+1}/{len(pvs)}: {lbl}…")
                    data = _fetch_scalars(ch, start_ns, end_ns)
                    series.append({"label": lbl, "channel": ch, "data": data,
                                   "error": _last_fetch_error.get(ch)})
                    sig.progress_n.emit(i + 1, len(pvs))
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
        # Drop the "last value before start" sample EPICS returns, so the axis
        # is clamped to the selected day instead of stretching to the previous day.
        out = []
        for s in series:
            d = [(t, v) for (t, v) in s["data"]
                 if self._day_start_ns <= t <= self._day_end_ns]
            # That dropped sample is still worth keeping: for a PV with nothing on
            # the chosen day it is the only clue to when it last recorded anything.
            last_before = max((t for t, _ in s["data"]), default=None) if not d else None
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
                notes.append(f"{s['label']}: nothing on this day "
                             f"(last archived {_fmt_date(s['last_before'])} "
                             f"{_fmt_hms(s['last_before'])})")
            else:
                notes.append(f"{s['label']}: nothing archived")
        if total == 0:
            self._set_status("No data for the selected PV(s) on this day.  "
                             + "   ".join(notes))
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
    def _draw_top_empty(self, msg: str = "Select a day first  →  button on the left"):
        ax = self._ax_top
        ax.clear()
        ax.set_facecolor("white")
        ax.text(0.5, 0.5, msg, transform=ax.transAxes,
                ha="center", va="center", color="#aaa", fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])
        self._canvas_top.draw_idle()

    def _paint_region_spans(self, ax):
        # Regions can now span multiple days; spans outside the current day's
        # x-limits are simply clipped, so painting them all is harmless.
        for r in self._regions:
            ax.axvspan(
                mdates.date2num(_ns_to_dt(r["t_start"])),
                mdates.date2num(_ns_to_dt(r["t_end"])),
                alpha=0.25, color=r["color"], zorder=0,
            )

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
            times = mdates.date2num(t_ns.astype("datetime64[ns]"))
            is_active = (s["channel"] == active_ch)
            # Colour is looked up per PV on every draw, so it survives a reload, a
            # day with no data for one PV and any change to the PV list.
            col = self._trace_colour(s["channel"])
            s["color"] = col
            # Archived values hold until the next sample (zero-order hold), so a
            # step-after line reflects the real signal — no false linear ramps.
            # Sample dots help on a sparse trace; on a dense one they merge into a
            # solid band and only make every pan and zoom slower, so drop them.
            a.plot(times, vals, "-", drawstyle="steps-post",
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
                "side": side, "times": times, "vals": vals,
            })

        days = self._selected_days or ([self._selected_day] if self._selected_day else [])
        multi = len(days) > 1
        # multi-day: show the date in each tick; single day: just the time
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%m-%d %H:%M" if multi else "%H:%M", tz=_PRAGUE))
        # clamp the view to the selected day/range (issue: previous day used to show)
        ax.set_xlim(mdates.date2num(_ns_to_dt(self._day_start_ns)),
                    mdates.date2num(_ns_to_dt(self._day_end_ns)))
        self._fig_top.autofmt_xdate(rotation=0, ha="center")
        # Task 6: always show the selected date on the axis, even for a single day.
        if days:
            date_str = (f"{days[0].toString('yyyy-MM-dd')} → {days[-1].toString('yyyy-MM-dd')}"
                        if multi else days[0].toString("yyyy-MM-dd"))
            ax.set_xlabel(f"Time   —   {date_str}")
        else:
            ax.set_xlabel("Time")
        ax.set_title("Drag to select time region(s), then click Analyze")
        ax.grid(True, alpha=0.25)
        self._paint_region_spans(ax)
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

    def _on_span(self, xmin: float, xmax: float):
        if xmax - xmin < 1e-9:
            return
        try:
            t_start = int(mdates.num2date(xmin).timestamp() * 1e9)
            t_end   = int(mdates.num2date(xmax).timestamp() * 1e9)
        except Exception:
            return
        rid   = self._region_seq
        self._region_seq += 1
        color = _REGION_COLORS[rid % len(_REGION_COLORS)]
        self._regions.append({
            "id": rid, "t_start": t_start, "t_end": t_end, "color": color,
            "visible": True, "expanded": False, "show_individual": False,
            "analyzed": False, "n": 0,
        })
        # add only the new span (keeps current zoom/pan — nothing else changes)
        self._ax_top.axvspan(xmin, xmax, alpha=0.25, color=color, zorder=0)
        self._canvas_top.draw_idle()
        self._rebuild_regions_ui()
        self._update_action_buttons()
        self._set_status(
            f"Spectrum {len(self._regions)} added: "
            f"{_fmt_hms(t_start)} – {_fmt_hms(t_end)}.  "
            f"Add more or click Analyze."
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
                                "sigma": None, "std": None, "stack": None, "n": 0})
                else:
                    res.update(st)
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
        self._install_bot_cursor_artists()
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

    def _prep_curve(self, x, y, smooth_win: int = 0):
        """Mask a curve to the current range, optionally smoothing it.
        Returns (xp, yp, mask)."""
        y = np.asarray(y, dtype=float)
        if x is None or len(x) != len(y):
            x = np.arange(len(y))
        x = np.asarray(x, dtype=float)
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
        if x is None or len(x) != stack.shape[1]:
            x = np.arange(stack.shape[1])
        x = np.asarray(x, dtype=float)
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
        def nm(v):  return f"{v:.2f} nm" if v is not None else "n/a"
        def sci(v): return f"{v:.3g}"    if v is not None else "n/a"
        return (
            f"<span style='color:#888'>from {self._cmb_method.currentText()}</span><br>"
            f"<b>Peak λ:</b> {nm(m.get('peak_wl'))} "
            f"<span style='color:#888'>@ {sci(m.get('peak_int'))}</span><br>"
            f"<b>Centroid:</b> {nm(m.get('centroid'))}<br>"
            f"<b>FWHM:</b> {nm(m.get('fwhm'))} &nbsp; "
            f"<b>RMS bw:</b> {nm(m.get('rms_bw'))}<br>"
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
        if x is None or len(x) != len(y):
            x = np.arange(len(y))
        x = np.asarray(x, dtype=float)
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
        method = self._method()
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
        span = self._signal_span(self._x_data, st[self._method()])
        if span is None and self._x_data is not None and len(self._x_data):
            # no clear signal: fall back to the full X-axis span so it is at least visible
            span = (float(np.min(self._x_data)), float(np.max(self._x_data)))
        if span:
            self._apply_fit_span(*span)

    def _redraw_spectra(self):
        norm       = self._norm_mode()
        band_on    = self._chk_std.isChecked()
        band_kind  = self._band_kind()
        smooth_win = self._smooth_win()
        method     = self._method()
        x_min, x_max = self._sb_x_min.value(), self._sb_x_max.value()
        color_for   = self._compute_region_colors()  # also sets self._colorbar_info
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

        # analyzed regions (skip hidden ones — visibility "eye" toggle)
        for i, r in enumerate(self._regions):
            if not r.get("analyzed") or not r.get("visible", True):
                continue
            center = r.get(method)
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
            if st is not None:
                band_lo = band_hi = std_arg = None
                if band_on and band_kind == "pct":
                    band_lo, band_hi = st.get("p10"), st.get("p90")
                elif band_on:
                    std_arg = st["std"]
                self._plot_spectrum(ax, self._x_data, st[method], std_arg,
                                    "#000000", f"Live {method} (n={st['n']})",
                                    norm, band_lo, band_hi, lw=2.4, smooth_win=smooth_win)
            any_drawn = True

        # comparison curve (difference / ratio of two analyzed regions)
        if self._chk_compare.isChecked():
            any_drawn = self._plot_comparison(ax, method, smooth_win) or any_drawn

        if any_drawn:
            ax.set_xlabel("Wavelength [nm]")
            ax.set_ylabel(self._intensity_label(norm))
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
        else:
            self._draw_bot_empty()

        self._bot_redrawing = False
        if self._bot_user_xlim is not None:
            ax.set_xlim(self._bot_user_xlim)
        if self._bot_user_ylim is not None:
            ax.set_ylim(self._bot_user_ylim)
        self._canvas_bot.draw_idle()
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
        xb = np.asarray(xb_full, dtype=float) if (
            xb_full is not None and len(xb_full) == len(yb)) else np.arange(len(yb))
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
        x = self._x_data
        if x is None or len(x) != len(arrs[-1]):
            x = np.arange(len(arrs[-1]))
        x = np.asarray(x, dtype=float)
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

        dlg = ExportDialog(len(analyzed), len(live), self._method(), self)
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
                self._fig_bot.savefig(p, dpi=150, bbox_inches="tight")
                written.append(p)
        except Exception as ex:
            QMessageBox.critical(self, "Export error", str(ex))
            return

        self._set_status("Exported: " + ", ".join(os.path.basename(p) for p in written))
        QMessageBox.information(self, "Export done", "Saved:\n" + "\n".join(written))

    def _export_csv(self, path: str, analyzed: list[tuple[int, dict]], live: list):
        """One CSV: a details block, a blank line, then the curve table.

        Columns: each analyzed region's averaged curve, plus every live shot
        (so 7 regions + 100 live shots export as 107 curves).
        """
        method = self._method()
        regs = [(i, r) for i, r in analyzed if r.get(method) is not None]

        x = self._x_data
        if x is None and regs:
            x = regs[0][1].get("x")
        if x is None and regs:
            x = np.arange(len(regs[0][1][method]))
        if x is None and live:
            x = np.arange(len(live[-1][1]))
        if x is None or (not regs and not live):
            raise ValueError("No curve data to export.")
        x = np.asarray(x, dtype=float)
        nx = len(x)
        order_labels = [lbl for lbl, _ in ORDER_PVS]
        # live shots whose length matches the wavelength axis
        live_ok = [(t, np.asarray(a, dtype=float)) for t, a in live if len(a) == nx]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            f.write("sep=;\r\n")          # tell Excel the delimiter (locale-proof)
            w = csv.writer(f, delimiter=";")

            # ── details block ───────────────────────────────────────────
            metric_cols = ["Peak λ [nm]", "Peak intensity", "Centroid [nm]",
                           "FWHM [nm]", "RMS bandwidth [nm]", "Area"]
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
                w.writerow([
                    self._region_label(i), date_str,
                    _fmt_hms(r["t_start"]), _fmt_hms(r["t_end"]),
                    r.get("n", 0), method,
                    self._fmt_full(ea) if ea is not None else "",
                ] + [self._fmt_full(orders.get(lbl)) if orders.get(lbl) is not None
                     else "" for lbl in order_labels]
                  + [self._fmt_full(m.get(k)) if m.get(k) is not None else ""
                     for k in metric_keys])
            if live_ok:
                t0, t1 = live_ok[0][0], live_ok[-1][0]
                d0, d1 = _fmt_date(t0), _fmt_date(t1)
                date_str = d0 if d0 == d1 else f"{d0}…{d1}"
                w.writerow([f"Live shots (last {len(live_ok)})", date_str,
                            _fmt_hms(t0), _fmt_hms(t1),
                            len(live_ok), "individual", "",
                            *[""] * len(order_labels), *[""] * len(metric_cols)])

            w.writerow([])   # blank separator line

            # ── curve table ─────────────────────────────────────────────
            cmp_name, cmp_vals = self._export_comparison_curve(x, method)

            w.writerow(["# Curve data"])
            header = ["wavelength_nm"]
            for i, _ in regs:
                header.append(f"{self._region_label(i)} ({method})")
                header.append(f"{self._region_label(i)} std")
            for t, _ in live_ok:
                header.append(f"Live {_fmt_hms(t)}")
            if cmp_name:
                header.append(cmp_name)
            w.writerow(header)

            for j in range(nx):
                row = [self._fmt_full(x[j])]
                for _, r in regs:
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
        xa = ra.get("x"); xb = rb.get("x")
        xa = np.asarray(xa, float) if (xa is not None and len(xa) == len(ya)) else np.arange(len(ya))
        xb = np.asarray(xb, float) if (xb is not None and len(xb) == len(yb)) else np.arange(len(yb))
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
