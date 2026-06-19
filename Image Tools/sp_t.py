"""
sp_t.py — Spectra tab

Zobrazuje spektra ze SPIDER spektrometru přes CPVA archiver.
X osa (vlnové délky / doménové hodnoty): L3-SBDP-SPIDER:SpecDomain_Int_X
  — waveform, mění se zřídka; načte se jednou a cachuje se v paměti
Y osa (intenzity): L3-SBDP-SPIDER:SpecDomain_Int_Y
  — waveform, mění se každý výstřel laseru

Funkce:
  • Live mód: průběžný polling, klouzavý průměr přes N posledních výstřelů
  • Archivní mód: výběr časového rozsahu, stažení historických dat z CPVA
  • Srovnání dní: overlay denních průměrů z libovolných dat
  • Waterfall 2D: matice výstřelů jako heatmapa
  • Normalizace, std pásmo, min/max pásmo
  • Detekce peaků a FWHM
  • Centroid výpočet
  • Export do NPZ / CSV
  • Extra PV: volitelné číselné zobrazení dalších procesních proměnných
  • Indikátor stavu připojení k CPVA

Standalone spuštění:  python sp_t.py
Integrace do tabů:    via importlib v main.py  (třída SpectraWidget)
"""

import json
import os
import ssl
import sys
import threading
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    PRAGUE = None

try:
    from scipy.signal import find_peaks as _scipy_find_peaks
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from PySide6.QtCore import Qt, QObject, QTimer, Signal, QDate, QDateTime, QTime
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QCheckBox, QGroupBox,
    QDateTimeEdit, QDateEdit, QScrollArea, QLineEdit, QSizePolicy,
    QButtonGroup, QFileDialog, QMessageBox,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure


# ── CONFIG ────────────────────────────────────────────────────────────────────

CPVA_BASE_URL = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_TIMEOUT  = 15.0  # seconds

PV_X = "L3-SBDP-SPIDER:SpecDomain_Int_X"
PV_Y = "L3-SBDP-SPIDER:SpecDomain_Int_Y"

_REF_COLORS = ["#e67e22", "#9b59b6", "#27ae60", "#e74c3c", "#1abc9c", "#f39c12"]

_CONN_COLORS = {
    "ok":   ("#22aa22", "Připojeno"),
    "warn": ("#cc8800", "Problémy s připojením"),
    "err":  ("#cc2222", "Odpojeno"),
}


# ── CPVA helpers ──────────────────────────────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _cpva_fetch(channel: str, start_ns: int, end_ns: int) -> list:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url = f"{CPVA_BASE_URL}/samples?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=CPVA_TIMEOUT, context=_ssl_ctx()) as r:
        data = json.loads(r.read().decode())
    return data if isinstance(data, list) else []


def _sample_to_array(val) -> "np.ndarray | None":
    if isinstance(val, list) and val:
        return np.asarray(val, dtype=float)
    if val is not None:
        try:
            return np.array([float(val)])
        except (TypeError, ValueError):
            pass
    return None


def _latest_waveform(channel: str) -> "np.ndarray | None":
    """Return the most recent waveform for channel (looks back 10 min)."""
    end_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
    start_ns = end_ns - int(10 * 60 * 1e9)
    samples  = _cpva_fetch(channel, start_ns, end_ns)
    if not samples:
        return None
    return _sample_to_array(samples[-1].get("value"))


def _fetch_waveforms(channel: str, start_ns: int, end_ns: int) -> "list[tuple[int, np.ndarray]]":
    """Return all waveform samples in range as list of (timestamp_ns, array)."""
    out = []
    for s in _cpva_fetch(channel, start_ns, end_ns):
        t_ns = s.get("time")
        if t_ns is None:
            continue
        arr = _sample_to_array(s.get("value"))
        if arr is not None:
            out.append((int(t_ns), arr))
    return out


# ── Spectral analysis helpers ─────────────────────────────────────────────────

def _centroid(x: np.ndarray, y: np.ndarray) -> "float | None":
    """Spectral centroid (intensity-weighted mean of x)."""
    total = float(np.sum(y))
    if total == 0:
        return None
    return float(np.sum(x * y) / total)


def _fwhm(x: np.ndarray, y: np.ndarray) -> "tuple[float, float, float] | None":
    """
    Estimate FWHM by threshold crossing at half the global maximum.
    Returns (left_x, right_x, width) or None if not found.
    """
    if len(y) < 3:
        return None
    half = float(np.max(y)) / 2.0
    above = y >= half
    crossings_up   = np.where(np.diff(above.astype(int)) ==  1)[0]
    crossings_down = np.where(np.diff(above.astype(int)) == -1)[0]
    if len(crossings_up) == 0 or len(crossings_down) == 0:
        return None
    left_idx  = crossings_up[0]
    right_idx = crossings_down[-1]
    if left_idx >= right_idx:
        return None
    return float(x[left_idx]), float(x[right_idx]), float(x[right_idx] - x[left_idx])


def _find_peaks(y: np.ndarray, min_height_pct: float = 10.0) -> np.ndarray:
    """Return peak indices. Uses scipy if available, otherwise argmax only."""
    min_h = float(np.max(y)) * min_height_pct / 100.0
    if _HAS_SCIPY:
        peaks, _ = _scipy_find_peaks(y, height=min_h, distance=max(3, len(y) // 50))
        return peaks
    # Fallback: return global maximum only
    return np.array([int(np.argmax(y))])


def _normalize_rows(arrs: "list[np.ndarray]") -> "list[np.ndarray]":
    """Normalize each spectrum to its own maximum (returns new list)."""
    result = []
    for a in arrs:
        m = float(np.max(a))
        result.append(a / m if m > 0 else a.copy())
    return result


def _most_common_len(arrs: "list[np.ndarray]") -> int:
    lengths = [len(a) for a in arrs]
    return max(set(lengths), key=lengths.count)


# ── Thread signalling ─────────────────────────────────────────────────────────

class _Sig(QObject):
    done     = Signal(object)
    error    = Signal(str)
    progress = Signal(str)


def _bg(fn) -> None:
    threading.Thread(target=fn, daemon=True).start()


# ── Main widget ───────────────────────────────────────────────────────────────

class SpectraWidget(QWidget):
    """
    Spectra viewer tab — live polling and archive analysis of SPIDER spectrometer.
    Attributes used by main.py:  cancel_scan()
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Buffer: deque of (timestamp_ns, np.ndarray) — live or archive data
        self._x_data: "np.ndarray | None" = None
        self._buf: deque = deque(maxlen=500)
        self._refs: list[tuple[str, np.ndarray]] = []
        self._live = False
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_tick)
        self._extra_pvs: list[str] = []
        self._extra_vals: dict[str, str] = {}
        self._conn_failures = 0   # consecutive live-fetch failures
        self._waterfall = False   # True = imshow mode
        self._colorbar = None     # matplotlib colorbar handle

        self._build_ui()
        self._connect()
        self._switch_mode(live=True)
        self._reload_x_bg()

    # ─────────────────────────────────────────────────────────────────────────
    #  UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── left panel ──────────────────────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(268)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)
        lv.setSpacing(5)

        # X-axis + connection status
        gx = QGroupBox("X-axis (domain)")
        gxl = QVBoxLayout(gx)
        x_row = QHBoxLayout()
        self._lbl_x = QLabel("Loading…")
        self._lbl_x.setWordWrap(True)
        self._lbl_x.setStyleSheet("font-size:10px; color:#444;")
        self._dot_conn = QLabel("●")
        self._dot_conn.setFixedWidth(16)
        self._dot_conn.setToolTip("Stav připojení k CPVA")
        self._dot_conn.setStyleSheet("color:#888; font-size:14px;")
        x_row.addWidget(self._lbl_x, stretch=1)
        x_row.addWidget(self._dot_conn)
        gxl.addLayout(x_row)
        self._btn_reload_x = QPushButton("Reload X")
        gxl.addWidget(self._btn_reload_x)
        lv.addWidget(gx)

        # Mode
        gm = QGroupBox("Mode")
        gml = QHBoxLayout(gm)
        self._btn_mode_live = QPushButton("Live")
        self._btn_mode_live.setCheckable(True)
        self._btn_mode_arch = QPushButton("Archive")
        self._btn_mode_arch.setCheckable(True)
        self._mode_grp = QButtonGroup(self)
        self._mode_grp.setExclusive(True)
        self._mode_grp.addButton(self._btn_mode_live)
        self._mode_grp.addButton(self._btn_mode_arch)
        gml.addWidget(self._btn_mode_live)
        gml.addWidget(self._btn_mode_arch)
        lv.addWidget(gm)

        # ── Live panel ───────────────────────────────────────────────────────
        self._pnl_live = QGroupBox("Live")
        pll = QVBoxLayout(self._pnl_live)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Interval (s):"))
        self._sb_interval = QSpinBox()
        self._sb_interval.setRange(1, 120)
        self._sb_interval.setValue(5)
        r1.addWidget(self._sb_interval)
        pll.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Průměr N:"))
        self._sb_avg_n = QSpinBox()
        self._sb_avg_n.setRange(1, 500)
        self._sb_avg_n.setValue(10)
        r2.addWidget(self._sb_avg_n)
        pll.addLayout(r2)
        self._chk_show_buf = QCheckBox("Průsvitně vše v bufferu")
        pll.addWidget(self._chk_show_buf)
        self._btn_startstop = QPushButton("▶  Start Live")
        pll.addWidget(self._btn_startstop)
        lv.addWidget(self._pnl_live)

        # ── Archive panel ────────────────────────────────────────────────────
        self._pnl_arch = QGroupBox("Archive")
        pal = QVBoxLayout(self._pnl_arch)
        now_qdt = QDateTime.currentDateTime()
        midnight = QDateTime(now_qdt.date(), QTime(0, 0, 0))
        pal.addWidget(QLabel("Od:"))
        self._dt_start = QDateTimeEdit(midnight)
        self._dt_start.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._dt_start.setCalendarPopup(True)
        pal.addWidget(self._dt_start)
        pal.addWidget(QLabel("Do:"))
        self._dt_end = QDateTimeEdit(now_qdt)
        self._dt_end.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._dt_end.setCalendarPopup(True)
        pal.addWidget(self._dt_end)
        r_arch = QHBoxLayout()
        r_arch.addWidget(QLabel("Průměr N:"))
        self._sb_arch_avg_n = QSpinBox()
        self._sb_arch_avg_n.setRange(1, 500)
        self._sb_arch_avg_n.setValue(10)
        r_arch.addWidget(self._sb_arch_avg_n)
        pal.addLayout(r_arch)
        self._btn_load_arch = QPushButton("Načíst")
        pal.addWidget(self._btn_load_arch)
        lv.addWidget(self._pnl_arch)

        # ── Analysis options ─────────────────────────────────────────────────
        ga = QGroupBox("Analýza")
        gal = QVBoxLayout(ga)
        self._chk_normalize = QCheckBox("Normalizovat na peak")
        self._chk_normalize.setToolTip(
            "Každé spektrum vydělí svým maximem před průměrováním.\n"
            "Vhodné při srovnávání spekter s různou energií."
        )
        self._chk_std = QCheckBox("Std. pásmo (±1σ)")
        self._chk_std.setToolTip("Zobrazí pásmo průměr ± směrodatná odchylka")
        self._chk_minmax = QCheckBox("Min/Max pásmo")
        self._chk_minmax.setToolTip("Zobrazí min a max přes buffered výstřely")
        self._chk_peaks = QCheckBox("Peaky + FWHM")
        self._chk_peaks.setToolTip(
            "Označí peaky na grafu a zobrazí FWHM.\n"
            + ("Používá scipy.signal.find_peaks." if _HAS_SCIPY else
               "scipy není k dispozici — zobrazí pouze globální maximum.")
        )
        pk_row = QHBoxLayout()
        pk_row.addWidget(QLabel("  Min. výška (%):"))
        self._sb_peak_h = QSpinBox()
        self._sb_peak_h.setRange(1, 99)
        self._sb_peak_h.setValue(10)
        self._sb_peak_h.setToolTip("Minimální výška peaku jako % globálního maxima")
        pk_row.addWidget(self._sb_peak_h)
        self._chk_centroid = QCheckBox("Centroid (svislá čára)")
        self._chk_centroid.setToolTip("Zobrazí centroid (těžiště) průměrného spektra")
        gal.addWidget(self._chk_normalize)
        gal.addWidget(self._chk_std)
        gal.addWidget(self._chk_minmax)
        gal.addWidget(self._chk_peaks)
        gal.addLayout(pk_row)
        gal.addWidget(self._chk_centroid)
        lv.addWidget(ga)

        # ── Reference days ───────────────────────────────────────────────────
        gr = QGroupBox("Referenční dny")
        grl = QVBoxLayout(gr)
        ref_row = QHBoxLayout()
        self._de_ref = QDateEdit(QDate.currentDate())
        self._de_ref.setCalendarPopup(True)
        self._de_ref.setDisplayFormat("yyyy-MM-dd")
        self._btn_add_ref = QPushButton("Přidat")
        ref_row.addWidget(self._de_ref, stretch=1)
        ref_row.addWidget(self._btn_add_ref)
        grl.addLayout(ref_row)
        self._ref_rows_w = QWidget()
        self._ref_rows_l = QVBoxLayout(self._ref_rows_w)
        self._ref_rows_l.setContentsMargins(0, 0, 0, 0)
        self._ref_rows_l.setSpacing(2)
        ref_scroll = QScrollArea()
        ref_scroll.setWidget(self._ref_rows_w)
        ref_scroll.setWidgetResizable(True)
        ref_scroll.setMaximumHeight(100)
        ref_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        grl.addWidget(ref_scroll)
        self._btn_clear_refs = QPushButton("Smazat vše")
        grl.addWidget(self._btn_clear_refs)
        lv.addWidget(gr)

        # ── Extra PVs ────────────────────────────────────────────────────────
        gp = QGroupBox("Extra PV (číselné hodnoty)")
        gpl = QVBoxLayout(gp)
        pv_row = QHBoxLayout()
        self._le_pv = QLineEdit()
        self._le_pv.setPlaceholderText("Název PV…")
        self._btn_add_pv = QPushButton("Přidat")
        pv_row.addWidget(self._le_pv, stretch=1)
        pv_row.addWidget(self._btn_add_pv)
        gpl.addLayout(pv_row)
        self._pv_rows_w = QWidget()
        self._pv_rows_l = QVBoxLayout(self._pv_rows_w)
        self._pv_rows_l.setContentsMargins(0, 0, 0, 0)
        self._pv_rows_l.setSpacing(2)
        pv_scroll = QScrollArea()
        pv_scroll.setWidget(self._pv_rows_w)
        pv_scroll.setWidgetResizable(True)
        pv_scroll.setMaximumHeight(90)
        pv_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        gpl.addWidget(pv_scroll)
        lv.addWidget(gp)

        lv.addStretch()

        self._lbl_status = QLabel("Připraveno")
        self._lbl_status.setWordWrap(True)
        self._lbl_status.setStyleSheet("color:#555; font-size:10px;")
        lv.addWidget(self._lbl_status)

        # ── Right panel ──────────────────────────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(2)

        # View + export toolbar
        vtb = QHBoxLayout()
        self._btn_1d = QPushButton("1D Spektrum")
        self._btn_1d.setCheckable(True)
        self._btn_1d.setChecked(True)
        self._btn_wf = QPushButton("Waterfall 2D")
        self._btn_wf.setCheckable(True)
        self._view_grp = QButtonGroup(self)
        self._view_grp.setExclusive(True)
        self._view_grp.addButton(self._btn_1d)
        self._view_grp.addButton(self._btn_wf)
        self._btn_export = QPushButton("💾  Exportovat")
        self._btn_export.setToolTip("Uloží X osu + buffered spektra do NPZ nebo CSV")
        vtb.addWidget(self._btn_1d)
        vtb.addWidget(self._btn_wf)
        vtb.addStretch()
        vtb.addWidget(self._btn_export)
        rv.addLayout(vtb)

        self._fig = Figure(tight_layout=True)
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._mpl_toolbar = NavigationToolbar2QT(self._canvas, right)
        rv.addWidget(self._mpl_toolbar)
        rv.addWidget(self._canvas, stretch=1)

        self._pv_bar = QLabel()
        self._pv_bar.setStyleSheet(
            "background:#f0f4ff; border-top:1px solid #cce; padding:3px 8px; font-size:11px;"
        )
        self._pv_bar.setVisible(False)
        rv.addWidget(self._pv_bar)

        root.addWidget(left)
        root.addWidget(right, stretch=1)

        self._init_plot()

    def _init_plot(self):
        self._ax.set_xlabel("Doménová osa")
        self._ax.set_ylabel("Intenzita")
        self._ax.set_title("Spektrum — SPIDER")
        self._ax.grid(True, alpha=0.3)
        self._ax.text(0.5, 0.5, "Žádná data", transform=self._ax.transAxes,
                      ha="center", va="center", color="#bbb", fontsize=14)
        self._canvas.draw_idle()

    # ─────────────────────────────────────────────────────────────────────────
    #  Signal wiring
    # ─────────────────────────────────────────────────────────────────────────

    def _connect(self):
        self._btn_reload_x.clicked.connect(self._reload_x_bg)
        self._btn_mode_live.clicked.connect(lambda: self._switch_mode(live=True))
        self._btn_mode_arch.clicked.connect(lambda: self._switch_mode(live=False))
        self._btn_startstop.clicked.connect(self._toggle_live)
        self._btn_load_arch.clicked.connect(self._load_archive_bg)
        self._btn_add_ref.clicked.connect(self._add_ref_bg)
        self._btn_clear_refs.clicked.connect(self._clear_refs)
        self._btn_add_pv.clicked.connect(self._add_extra_pv)
        self._le_pv.returnPressed.connect(self._add_extra_pv)
        self._btn_1d.clicked.connect(lambda: self._set_waterfall(False))
        self._btn_wf.clicked.connect(lambda: self._set_waterfall(True))
        self._btn_export.clicked.connect(self._export_data)
        # Redraw triggers
        for w in (self._sb_avg_n, self._sb_arch_avg_n, self._sb_peak_h):
            w.valueChanged.connect(lambda _: self._redraw())
        for chk in (self._chk_show_buf, self._chk_normalize, self._chk_std,
                    self._chk_minmax, self._chk_peaks, self._chk_centroid):
            chk.stateChanged.connect(lambda _: self._redraw())

    # ─────────────────────────────────────────────────────────────────────────
    #  Mode switching
    # ─────────────────────────────────────────────────────────────────────────

    def _switch_mode(self, live: bool):
        self._btn_mode_live.setChecked(live)
        self._btn_mode_arch.setChecked(not live)
        self._pnl_live.setVisible(live)
        self._pnl_arch.setVisible(not live)
        if not live and self._live:
            self._stop_live()

    # ─────────────────────────────────────────────────────────────────────────
    #  X-axis — load and cache
    # ─────────────────────────────────────────────────────────────────────────

    def _reload_x_bg(self):
        self._lbl_x.setText("Načítám…")
        self._btn_reload_x.setEnabled(False)
        sig = _Sig(self)
        sig.done.connect(self._on_x_loaded)
        sig.error.connect(self._on_x_error)

        def _run():
            try:
                arr = _latest_waveform(PV_X)
                sig.done.emit(arr)
            except Exception as exc:
                sig.error.emit(str(exc))

        _bg(_run)

    def _on_x_loaded(self, arr):
        self._btn_reload_x.setEnabled(True)
        if arr is None or len(arr) == 0:
            self._lbl_x.setText("Žádná data pro X")
            return
        self._x_data = arr
        self._lbl_x.setText(f"{len(arr)} bodů\n[{arr[0]:.4g} … {arr[-1]:.4g}]")
        self._redraw()

    def _on_x_error(self, msg: str):
        self._btn_reload_x.setEnabled(True)
        self._lbl_x.setText(f"Chyba X:\n{msg[:80]}")

    def _xaxis(self, n: int) -> np.ndarray:
        if self._x_data is not None and len(self._x_data) == n:
            return self._x_data
        return np.arange(n)

    # ─────────────────────────────────────────────────────────────────────────
    #  Live mode
    # ─────────────────────────────────────────────────────────────────────────

    def _toggle_live(self):
        if self._live:
            self._stop_live()
        else:
            self._start_live()

    def _start_live(self):
        self._buf = deque(maxlen=500)
        self._conn_failures = 0
        self._live = True
        self._btn_startstop.setText("⏹  Stop Live")
        self._live_tick()

    def _stop_live(self):
        self._live = False
        self._live_timer.stop()
        self._btn_startstop.setText("▶  Start Live")
        self._set_status("Live zastaven.")

    def _live_tick(self):
        self._live_timer.stop()
        sig = _Sig(self)
        sig.done.connect(self._on_live_y)
        sig.error.connect(self._on_live_error)

        def _run():
            try:
                arr = _latest_waveform(PV_Y)
                sig.done.emit(arr)
            except Exception as exc:
                sig.error.emit(str(exc))

        _bg(_run)
        if self._extra_pvs:
            self._poll_extra_pvs()

    def _on_live_y(self, arr):
        if arr is None:
            self._conn_failures += 1
            self._set_status("Live: žádná nová data")
        else:
            self._conn_failures = 0
            now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
            self._buf.append((now_ns, arr))
            self._redraw()
            t = datetime.now().strftime("%H:%M:%S")
            n = self._sb_avg_n.value()
            self._set_status(
                f"Live @ {t}  |  buf: {len(self._buf)}  |  avg N={min(n, len(self._buf))}"
            )
        self._update_conn_status()
        if self._live:
            self._live_timer.setSingleShot(True)
            self._live_timer.start(self._sb_interval.value() * 1000)

    def _on_live_error(self, msg: str):
        self._conn_failures += 1
        self._set_status(f"Live chyba: {msg[:60]}")
        self._update_conn_status()
        if self._live:
            self._live_timer.setSingleShot(True)
            self._live_timer.start(self._sb_interval.value() * 1000)

    def _update_conn_status(self):
        if self._conn_failures == 0:
            color, tip = _CONN_COLORS["ok"]
        elif self._conn_failures <= 2:
            color, tip = _CONN_COLORS["warn"]
        else:
            color, tip = _CONN_COLORS["err"]
        self._dot_conn.setStyleSheet(f"color:{color}; font-size:14px;")
        self._dot_conn.setToolTip(tip)

    # ─────────────────────────────────────────────────────────────────────────
    #  Archive mode
    # ─────────────────────────────────────────────────────────────────────────

    def _load_archive_bg(self):
        self._btn_load_arch.setEnabled(False)
        self._set_status("Načítám archiv…")

        def _qdt_to_ns(qdte: QDateTimeEdit) -> int:
            qdt = qdte.dateTime()
            d = qdt.date()
            ti = qdt.time()
            dt = datetime(
                d.year(), d.month(), d.day(),
                ti.hour(), ti.minute(), 0,
                tzinfo=PRAGUE if PRAGUE else timezone.utc,
            )
            return int(dt.timestamp() * 1e9)

        start_ns = _qdt_to_ns(self._dt_start)
        end_ns   = _qdt_to_ns(self._dt_end)

        sig = _Sig(self)
        sig.done.connect(self._on_archive_loaded)
        sig.error.connect(self._on_archive_error)

        def _run():
            try:
                samples = _fetch_waveforms(PV_Y, start_ns, end_ns)
                sig.done.emit(samples)
            except Exception as exc:
                sig.error.emit(str(exc))

        _bg(_run)

    def _on_archive_loaded(self, samples: list):
        self._btn_load_arch.setEnabled(True)
        if not samples:
            self._set_status("Archiv: žádná data v daném rozsahu")
            return
        self._buf = deque(samples, maxlen=max(len(samples), 500))
        self._redraw()
        self._set_status(f"Archiv: načteno {len(samples)} spekter")

    def _on_archive_error(self, msg: str):
        self._btn_load_arch.setEnabled(True)
        self._set_status(f"Archiv chyba: {msg[:80]}")

    # ─────────────────────────────────────────────────────────────────────────
    #  Reference spectra
    # ─────────────────────────────────────────────────────────────────────────

    def _add_ref_bg(self):
        qd = self._de_ref.date()
        label = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
        if any(lbl == label for lbl, _ in self._refs):
            self._set_status(f"Referenční den {label} je již načten")
            return
        self._set_status(f"Načítám referenci {label}…")

        tz = PRAGUE if PRAGUE else timezone.utc
        day_start = datetime(qd.year(), qd.month(), qd.day(), 0, 0, 0, tzinfo=tz)
        start_ns  = int(day_start.timestamp() * 1e9)
        end_ns    = start_ns + int(24 * 3600 * 1e9)

        sig = _Sig(self)
        sig.done.connect(lambda arr: self._on_ref_loaded(label, arr))
        sig.error.connect(lambda e: self._set_status(f"Ref chyba: {e[:60]}"))

        def _run():
            try:
                samples = _fetch_waveforms(PV_Y, start_ns, end_ns)
                if not samples:
                    sig.error.emit(f"Žádná data pro {label}")
                    return
                arrs = [a for _, a in samples if len(a) > 0]
                if not arrs:
                    sig.error.emit("Prázdná pole")
                    return
                common = _most_common_len(arrs)
                arrs = [a for a in arrs if len(a) == common]
                if self._chk_normalize.isChecked():
                    arrs = _normalize_rows(arrs)
                avg = np.mean(np.stack(arrs), axis=0)
                sig.done.emit(avg)
            except Exception as exc:
                sig.error.emit(str(exc))

        _bg(_run)

    def _on_ref_loaded(self, label: str, avg: np.ndarray):
        self._refs.append((label, avg))
        self._rebuild_ref_ui()
        self._redraw()
        self._set_status(f"Reference {label}: {len(avg)} bodů (denní průměr)")

    def _rebuild_ref_ui(self):
        while self._ref_rows_l.count():
            w = self._ref_rows_l.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, (label, _) in enumerate(self._refs):
            color = _REF_COLORS[i % len(_REF_COLORS)]
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 0, 2, 0)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:13px;")
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size:10px;")
            btn = QPushButton("✕")
            btn.setFixedSize(20, 18)
            btn.setStyleSheet("font-size:9px; padding:0; border-radius:2px;")
            btn.clicked.connect(lambda _, lbl_=label: self._remove_ref(lbl_))
            rl.addWidget(dot)
            rl.addWidget(lbl, stretch=1)
            rl.addWidget(btn)
            self._ref_rows_l.addWidget(row)

    def _remove_ref(self, label: str):
        self._refs = [(l, a) for l, a in self._refs if l != label]
        self._rebuild_ref_ui()
        self._redraw()

    def _clear_refs(self):
        self._refs.clear()
        self._rebuild_ref_ui()
        self._redraw()

    # ─────────────────────────────────────────────────────────────────────────
    #  Extra PVs
    # ─────────────────────────────────────────────────────────────────────────

    def _add_extra_pv(self):
        pv = self._le_pv.text().strip()
        if not pv or pv in self._extra_pvs:
            return
        self._extra_pvs.append(pv)
        self._le_pv.clear()
        self._rebuild_pv_ui()

    def _remove_extra_pv(self, pv: str):
        if pv in self._extra_pvs:
            self._extra_pvs.remove(pv)
            self._extra_vals.pop(pv, None)
            self._rebuild_pv_ui()
            self._update_pv_bar()

    def _rebuild_pv_ui(self):
        while self._pv_rows_l.count():
            w = self._pv_rows_l.takeAt(0).widget()
            if w:
                w.deleteLater()
        for pv in self._extra_pvs:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 0, 2, 0)
            name_lbl = QLabel(pv)
            name_lbl.setStyleSheet("font-size:10px; color:#333;")
            val_lbl = QLabel(self._extra_vals.get(pv, "—"))
            val_lbl.setStyleSheet("font-weight:600; font-size:10px;")
            btn = QPushButton("✕")
            btn.setFixedSize(20, 18)
            btn.setStyleSheet("font-size:9px; padding:0; border-radius:2px;")
            btn.clicked.connect(lambda _, p=pv: self._remove_extra_pv(p))
            rl.addWidget(name_lbl, stretch=1)
            rl.addWidget(val_lbl)
            rl.addWidget(btn)
            self._pv_rows_l.addWidget(row)

    def _poll_extra_pvs(self):
        pvs = list(self._extra_pvs)
        sig = _Sig(self)
        sig.done.connect(self._on_extra_pvs)

        def _run():
            vals: dict[str, str] = {}
            for pv in pvs:
                try:
                    arr = _latest_waveform(pv)
                    vals[pv] = f"{arr[0]:.5g}" if arr is not None and len(arr) > 0 else "—"
                except Exception:
                    vals[pv] = "chyba"
            sig.done.emit(vals)

        _bg(_run)

    def _on_extra_pvs(self, vals: dict):
        self._extra_vals.update(vals)
        self._rebuild_pv_ui()
        self._update_pv_bar()

    def _update_pv_bar(self):
        if not self._extra_pvs:
            self._pv_bar.setVisible(False)
            return
        parts = [f"<b>{pv}:</b> {self._extra_vals.get(pv, '—')}" for pv in self._extra_pvs]
        self._pv_bar.setText("  |  ".join(parts))
        self._pv_bar.setVisible(True)

    # ─────────────────────────────────────────────────────────────────────────
    #  Waterfall toggle
    # ─────────────────────────────────────────────────────────────────────────

    def _set_waterfall(self, on: bool):
        self._waterfall = on
        self._btn_1d.setChecked(not on)
        self._btn_wf.setChecked(on)
        # Show buffer toggle only for 1D mode
        self._chk_show_buf.setVisible(not on)
        self._chk_std.setVisible(not on)
        self._chk_minmax.setVisible(not on)
        self._chk_centroid.setVisible(not on)
        self._redraw()

    # ─────────────────────────────────────────────────────────────────────────
    #  Export
    # ─────────────────────────────────────────────────────────────────────────

    def _export_data(self):
        if not self._buf:
            QMessageBox.warning(self, "Export", "Žádná data k exportu.")
            return

        path, fmt = QFileDialog.getSaveFileName(
            self, "Uložit spektra",
            os.path.expanduser("~"),
            "NumPy archive (*.npz);;CSV (*.csv)",
        )
        if not path:
            return

        buf = list(self._buf)
        timestamps = np.array([t for t, _ in buf], dtype=np.int64)
        arrs = [arr for _, arr in buf]
        common = _most_common_len(arrs)
        arrs = [a for a in arrs if len(a) == common]

        try:
            if path.lower().endswith(".csv"):
                matrix = np.stack(arrs)           # shape (N_shots, N_wavelengths)
                x = self._xaxis(common)
                header = "timestamp_ns," + ",".join(f"{v:.6g}" for v in x)
                rows = np.hstack([timestamps[:len(arrs)].reshape(-1, 1), matrix])
                np.savetxt(path, rows, delimiter=",", header=header, comments="")
            else:
                if not path.lower().endswith(".npz"):
                    path += ".npz"
                x = self._xaxis(common)
                np.savez_compressed(
                    path,
                    x_axis=x,
                    spectra=np.stack(arrs),
                    timestamps_ns=timestamps[:len(arrs)],
                )
            self._set_status(f"Exportováno: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.critical(self, "Chyba exportu", str(exc))

    # ─────────────────────────────────────────────────────────────────────────
    #  Plot rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _redraw(self):
        # Remove colorbar from previous render if switching modes
        if self._colorbar is not None:
            try:
                self._colorbar.remove()
            except Exception:
                pass
            self._colorbar = None

        self._ax.cla()

        buf = list(self._buf)
        if not buf and not self._refs:
            self._init_plot()
            return

        n_avg = self._sb_avg_n.value() if self._pnl_live.isVisible() else self._sb_arch_avg_n.value()
        raw_arrs  = [arr for _, arr in buf[-n_avg:]]
        raw_times = [t   for t, _   in buf[-n_avg:]]

        if raw_arrs:
            common = _most_common_len(raw_arrs)
            raw_arrs  = [a for a in raw_arrs  if len(a) == common]
            raw_times = raw_times[:len(raw_arrs)]

        if self._waterfall:
            self._draw_waterfall(raw_arrs, raw_times)
        else:
            self._draw_1d(raw_arrs)

        try:
            self._fig.tight_layout()
        except Exception:
            pass
        self._canvas.draw_idle()

    def _draw_waterfall(self, arrs: list, times: list):
        if not arrs:
            self._ax.text(0.5, 0.5, "Žádná data", transform=self._ax.transAxes,
                          ha="center", va="center", color="#bbb", fontsize=14)
            self._ax.set_title("Waterfall — SPIDER")
            return

        normalize = self._chk_normalize.isChecked()
        rows = _normalize_rows(arrs) if normalize else list(arrs)
        matrix = np.stack(rows)   # shape (N_shots, N_wavelengths)

        x = self._xaxis(matrix.shape[1])
        im = self._ax.imshow(
            matrix, aspect="auto", origin="lower",
            extent=[float(x[0]), float(x[-1]), 0, matrix.shape[0]],
            cmap="inferno",
            interpolation="nearest",
        )
        self._colorbar = self._fig.colorbar(im, ax=self._ax, fraction=0.046, pad=0.04)
        self._colorbar.set_label("Intenzita" + (" (norm.)" if normalize else ""))

        self._ax.set_xlabel("Doménová osa")
        self._ax.set_ylabel("Výstřel #")
        label = "norm." if normalize else "raw"
        self._ax.set_title(f"Waterfall — SPIDER  ({matrix.shape[0]} výstřelů, {label})")

    def _draw_1d(self, arrs: list):
        self._ax.set_xlabel("Doménová osa")
        self._ax.set_ylabel("Intenzita")
        has_data = False
        annotations: list[str] = []

        if arrs:
            normalize = self._chk_normalize.isChecked()
            work = _normalize_rows(arrs) if normalize else arrs
            x = self._xaxis(len(work[0]))

            stacked = np.stack(work)
            avg = np.mean(stacked, axis=0)

            # Ghost traces
            if self._chk_show_buf.isChecked() and self._pnl_live.isVisible():
                n = len(work)
                for i, y in enumerate(work):
                    alpha = 0.06 + 0.22 * (i / max(n - 1, 1))
                    self._ax.plot(x, y, color="#90CAF9", alpha=alpha, linewidth=0.6)

            # Std envelope
            if self._chk_std.isChecked() and len(work) >= 2:
                std = np.std(stacked, axis=0)
                self._ax.fill_between(x, avg - std, avg + std,
                                      color="#1565C0", alpha=0.18, label="±1σ")

            # Min/max envelope
            if self._chk_minmax.isChecked() and len(work) >= 2:
                mn = np.min(stacked, axis=0)
                mx = np.max(stacked, axis=0)
                self._ax.fill_between(x, mn, mx,
                                      color="#42A5F5", alpha=0.12, label="Min/Max")

            # Average
            avg_label = f"Průměr (N={len(work)})" + (" norm." if normalize else "")
            self._ax.plot(x, avg, color="#1565C0", linewidth=2.0, label=avg_label)

            # Last spectrum
            if len(work) >= 2:
                self._ax.plot(x, work[-1], color="#42A5F5", linewidth=1.0,
                              alpha=0.6, label="Poslední")

            # Centroid
            if self._chk_centroid.isChecked():
                c = _centroid(x, avg)
                if c is not None:
                    self._ax.axvline(c, color="#ff7043", linewidth=1.2,
                                     linestyle=":", label=f"Centroid={c:.4g}")
                    annotations.append(f"centroid={c:.4g}")

            # Peaks + FWHM
            if self._chk_peaks.isChecked():
                peaks = _find_peaks(avg, min_height_pct=float(self._sb_peak_h.value()))
                for pk in peaks:
                    self._ax.axvline(x[pk], color="#e53935", linewidth=1.0,
                                     linestyle="--", alpha=0.8)
                    self._ax.annotate(
                        f"{x[pk]:.4g}",
                        xy=(x[pk], avg[pk]),
                        xytext=(0, 6), textcoords="offset points",
                        ha="center", fontsize=7, color="#e53935",
                    )
                fw = _fwhm(x, avg)
                if fw is not None:
                    lx, rx, w = fw
                    half = float(np.max(avg)) / 2.0
                    self._ax.hlines(half, lx, rx, colors="#e53935",
                                    linewidth=1.2, linestyle="-.", alpha=0.7)
                    annotations.append(f"FWHM={w:.4g}")

            has_data = True

        # Reference overlays
        for i, (label, ref) in enumerate(self._refs):
            color = _REF_COLORS[i % len(_REF_COLORS)]
            x = self._xaxis(len(ref))
            self._ax.plot(x, ref, color=color, linewidth=1.5,
                          linestyle="--", alpha=0.85, label=f"Ref {label}")
            has_data = True

        if has_data:
            title = "Spektrum — SPIDER"
            if annotations:
                title += "  |  " + "  ".join(annotations)
            self._ax.set_title(title, fontsize=10)
            self._ax.legend(fontsize=8, loc="best")
            self._ax.grid(True, alpha=0.3)
        else:
            self._ax.set_title("Spektrum — SPIDER")
            self._ax.grid(True, alpha=0.3)
            self._ax.text(0.5, 0.5, "Žádná data", transform=self._ax.transAxes,
                          ha="center", va="center", color="#bbb", fontsize=14)

    # ─────────────────────────────────────────────────────────────────────────
    #  Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._lbl_status.setText(msg)

    def cancel_scan(self):
        """Called by main window Stop All button."""
        if self._live:
            self._stop_live()


# ── Standalone entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget      { background: #f3f3f3; color: #111; }
        QLabel       { background: transparent; }
        QPushButton  { padding: 4px 8px; }
        QGroupBox    { font-weight: 600; }
        QToolTip     { background: #ffffcc; color: #111; border: 1px solid #aaa; padding: 4px; }
    """)
    w = SpectraWidget()
    w.setWindowTitle("Spectra — SPIDER")
    w.resize(1280, 800)
    w.show()
    sys.exit(app.exec())
