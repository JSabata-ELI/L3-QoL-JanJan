"""
if_t.py — Image Finder

The comparison tab. Frames side by side on ONE absolute scale, so a difference on
screen is a difference in the laser and not in a display window: many days of one
camera, or every picked camera at a picked moment.

ONE MOMENT WAS MERGED IN HERE (2026-09-04) and `om_t.py` deleted. What follows is
its design spec, kept because it is a specification and not a comment — it says why
the parts behave as they do.

  THE TWO HALVES ARE INDEPENDENT. Which cameras to look at and which moments to
  look at are separate questions — the PV graph reads no camera at all — so either
  may be answered first and the search runs when both are in (`_start_pv_search`
  holds a finished search in `_pending_pv_cfg` until the camera picker returns).

  PICKING. `PV Search` opens the graph: every marked day can be plotted, one day at
  a time or all of them side by side on one axis. LEFT-CLICK picks a moment and
  every further click ADDS one, on any marked day, so the set being searched is
  built up across days; Ctrl+Z or Undo takes the last pick back. LEFT-DRAG marks a
  time region — one frame per region, taken from the peak of the primary PV inside
  it. RIGHT-drag zooms the time axis, a plain RIGHT-CLICK zooms back out one step:
  the two buttons never do the same thing, left is "read this", right is "look
  closer". A day that carries a pick is never unmarked by a calendar click.

  ONE GRAPH, ALWAYS. Every checked PV is drawn in a single graph, grouped by UNIT
  with one y axis per unit (further ones on outward-offset spines), so a joule and a
  motor count are never plotted against the same scale and NO VALUE IS NORMALISED —
  every number on screen is the number that was archived. Any PV can be given an
  axis of its own.

  A RANGE WITH NO SAMPLE IN IT STILL HAS A VALUE. A setpoint-shaped channel — a
  waveplate angle, a motor position — is archived when it MOVES, so a five-minute
  range can contain not one sample of it while the value was perfectly well defined
  throughout. Both the range statistics and the CURVE fall back to the last sample
  before the range, held forward, and say so (n = 0, "held", the age in the
  tooltip). An empty row there used to read as "this channel is broken".

  A FORMULA IS A CURVE LIKE ANY OTHER. A picked formula is computed over time: its
  sources are read even when they are not themselves picked, evaluated on the union
  of their own timestamps with each source held forward, and the result is drawn and
  measured like a read channel. WHAT a formula means is still is_t's answer
  (`pv_eval_derived`, the same evaluator the Slider and the burn-in use); what lives
  here is the time base to evaluate it on. Its line BREAKS wherever a source has no
  value instead of being drawn straight across the gap.

  A MOMENT LOOKED AT TWICE IS FREE THE SECOND TIME. Which file answers (camera,
  moment) is kept in RAM, and one reading of an hour folder answers every camera and
  every later moment inside that hour (`sf_t.DayScanCache`). A miss younger than ten
  minutes is NOT remembered, because the archiver runs behind and the frame may
  simply not be written yet.

  NAVIGATION SAVES NOTHING. The prev/next shot arrows walk the primary PV's samples
  and record nothing; Save is an explicit press, and the saved list is SESSION-ONLY
  — never written to the settings file, so closing the program empties it.

  FROM THE MOMENT: "Send moment" opens it in the Image Slider and KEEPS the cameras
  picked there; "Send + cameras" carries this tab's pick over as well.

WHAT IS BORROWED, AND FROM WHERE (nothing here re-implements a resolver or a
renderer): `image_slider` (is_t.py) — the PV registry and its evaluator, the image
renderer, `container_root_for_year`, `parse_unix_ns_from_name`, `qt_pv_bar_below`;
`shot_finder` (sf_t.py) — `_find_image_in_day`, the ONE frame-for-a-timestamp
resolver in this program, and `DayScanCache`; `workshop` (wk_t.py) — `action_icon`,
the program's one painted-icon vocabulary; `cpva_client` — the archiver, its day
cache and the two names SBW4 is archived under; `daypicker` — the only owner of day
picking.
"""

import bisect
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image as PilImage

from PySide6.QtCore import (Qt, QTimer, QDate, QRunnable, QThreadPool, QObject,
                            Signal, QPointF, QRect, QSize, QLocale)
from PySide6.QtGui import (QColor, QTextCharFormat, QPixmap, QImage, QFont, QCursor,
                           QPainter, QPen, QPalette, QImageWriter,
                           QShortcut, QKeySequence)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QSlider,
    QScrollArea, QFrame, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QCalendarWidget, QDialog,
    QFileDialog, QMessageBox, QLineEdit, QMainWindow, QStyledItemDelegate,
    QDialogButtonBox, QSizePolicy, QSplitter, QTabWidget, QProgressBar,
    QButtonGroup, QSpinBox, QToolButton, QMenu, QStyle,
    QListWidget, QListWidgetItem, QGroupBox, QStackedWidget, QColorDialog,
    QDoubleSpinBox, QRadioButton,
)

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:
    import warnings
    warnings.warn("zoneinfo not available; falling back to UTC for Prague time", RuntimeWarning)
    PRAGUE = timezone.utc

import socket as _socket

def _detect_is_lab() -> bool:
    h = _socket.gethostname().upper()
    return any(h.startswith(p) for p in ("OPR1", "OPR2", "OPR3", "VIS01", "VIS02"))

_IS_LAB = _detect_is_lab()

# ── CONFIG ────────────────────────────────────────────────────────────────────
IMAGES_ROOT_BASE = r"//users-L3.tier0.lcs.local"

MAX_SCAN_FILES      = 2000  # max files to stat() per folder (network perf)

ACT_MAX_GAP_S      = 120
MIN_SEG_ROWS       = 25
MIN_SEG_DURATION_S = 10 * 60

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
CAM_33HZ   = {65, 66, 67, 63, 64, 60, 57, 58, 53, 54, 55, 56,
              31, 26, 22, 21, 25, 13, 14}

FINAL_RE  = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{3}|\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d{6})$")
SOURCE_RE = re.compile(r"(\d+)$")

# ── ENERGY CSV CONFIG ─────────────────────────────────────────────────────────
# Root folder where daily CSV files live
# Salvation is NOT running (as of 2026-08-19), so no new daily CSV is written and
# this fallback yields nothing for recent days — energy comes from the CPVA archiver
# alone. Kept wired up on purpose: historical days still have their CSV, and the
# fallback costs nothing until the archiver answers a day with no samples.
ENERGY_CSV_ROOT = r"//hapls-share.cs.eli-beams.eu/scratch/Salvation/2026_alldata"

# File name pattern: dataof{year}{MonthAbbr}_{day}  e.g. dataof2026Mar_24
# Python strftime format used to build the filename from a datetime:
ENERGY_CSV_NAME_FMT = "dataof%Y%b_%d"   # e.g. dataof2026Mar_24

# Columns available for annotation — edit this list to add/remove columns.
# These must match the CSV header exactly (case-sensitive).
ENERGY_COLUMNS_AVAILABLE = [
    "waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4",
    "CampOn", "E2_Open", "E3_Open", "E4_Open", "E5_Open", "Back_Ref",
]

# Default selected columns shown pre-checked in the column picker dialog
ENERGY_COLUMNS_DEFAULT = []

# The CSV-column → label table that used to live here is gone: what a PV is CALLED now
# comes from the shared registry (_pv_label_for → PV_LABELS), which is also where the
# operator's own name for it is typed. Two label tables meant a PV could be "SBW4" in
# one tab and "Compressed SBW4" in the other while both read the same channel.

# Match tolerance in seconds: |t_image - t_csv| must be ≤ this value.
# The daily energy CSV is logged irregularly (~20 s median between rows, gaps up
# to several minutes), NOT per shot — so a 2 s window left most images showing
# "—". The energies (SBW4/PTM1/…) change slowly, so the nearest sample within a
# couple of minutes is a faithful value for the image's shot.
ENERGY_MATCH_TOL_S = 120.0
# API (archiver) data is per-shot, not the sparse ~20 s CSV log — with a 120 s
# window a dark frame could inherit the PREVIOUS shot's energy. Revert to 120.0
# if operators prefer the old behaviour.
ENERGY_MATCH_TOL_API_S = 30.0

# ── CPVA ARCHIVER API ─────────────────────────────────────────────────────────
CPVA_BASE_URL     = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HTTP_TIMEOUT = 10.0   # seconds per request

# Channel used to find the best shot (highest energy = real shot, not dark/empty)
CPVA_SHOT_CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"

def _import_cpva_client():
    """Load the shared CPVA client (sibling cpva_client.py). Reuses an
    already-loaded instance so every tool (and re-exec'd module copy) shares
    one connection pool and one day cache."""
    import importlib.util as _ilu
    mod = sys.modules.get("cpva_client")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "cpva_client.py"
    spec = _ilu.spec_from_file_location("cpva_client", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["cpva_client"] = mod   # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


cpva = _import_cpva_client()


def _import_img_scale():
    """Load the shared intensity-scale helper (sibling img_scale.py) the same way
    as cpva_client: one instance per process, registered before exec."""
    import importlib.util as _ilu
    mod = sys.modules.get("img_scale")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "img_scale.py"
    spec = _ilu.spec_from_file_location("img_scale", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["img_scale"] = mod     # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


img_scale = _import_img_scale()


def _import_daypicker():
    """Load the shared day/time picker (sibling daypicker.py) the same way as
    cpva_client and img_scale: one instance per process, registered before exec.
    It owns HOW A DAY AND A TIME WINDOW ARE PICKED, so this tab cannot drift away
    from the Slider's calendar again."""
    import importlib.util as _ilu
    mod = sys.modules.get("daypicker")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "daypicker.py"
    spec = _ilu.spec_from_file_location("daypicker", p)
    mod = _ilu.module_from_spec(spec)
    sys.modules["daypicker"] = mod     # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


daypicker = _import_daypicker()

# Maps energy CSV column name → CPVA archiver channel name for API lookup
CPVA_CHANNEL_MAP: dict[str, str] = cpva.CHANNEL_MAP
# Was a second hard-coded literal up with CPVA_SHOT_CHANNEL and went stale when
# SBW4 was renamed — the channel name now comes from cpva_client only.
CPVA_SBW4_CHANNEL = cpva.SBW4_CHANNEL

# ── PV registry, shared with the Image Slider ────────────────────────────────
# The Slider module OWNS the registry — the presets, the PVs the operator added, the
# names given to them, their units and the formulas — and its picker is the one editor
# for it (PvConfigDialog). This tab reads that same module-level registry instead of
# keeping a second list of CSV column names beside it, which is what let "SBW4" mean
# one number here and a different one there.
#
# A picked PV is therefore identified by its REGISTRY NAME ("SBW4", "Compressed SBW4",
# an added channel's own name, a formula's name) — not by a CSV column name as it used
# to be. The CSV columns that only ever existed in Salvation's file (CampOn, E2..E5
# Open) are still reachable: type the column name into the picker's search box and the
# CSV fallback below finds it.
def _pv_all_names() -> "list[str]":
    return _get_slider_module().pv_all_names()


def _pv_channel_for(name: str) -> "str | None":
    """The archiver channel a picked PV reads. None for a formula (computed, not read)
    and for a CSV-only column. An added PV's name IS its channel."""
    sl = _get_slider_module()
    if sl.pv_is_derived(name):
        return None
    ch = sl.pv_channel_for(name)
    if ch:
        return ch
    return None if name in ENERGY_COLUMNS_AVAILABLE else name


def _pv_csv_col(name: str) -> "str | None":
    """The daily-CSV column for a picked PV, or None if it has none.

    Salvation stopped writing on 2026-08-19, so this only decides what a HISTORICAL
    day can still serve — and an arbitrary archiver PV was never in that file."""
    col = _get_slider_module().PV_DISPLAY_TO_COL.get(name)
    if col in ENERGY_COLUMNS_AVAILABLE:
        return col
    return name if name in ENERGY_COLUMNS_AVAILABLE else None


def _pv_scale_for(name: str) -> float:
    """The registry's own factor for a NAMED PV.

    1.0 for everything today: the one entry that carried a factor ("Compressed SBW4")
    is a FORMULA in the shared registry now, so the conversion is visible in the picker
    instead of being applied out of sight. This stays as the single place a
    registry-wide factor would live — never a number typed per tab, which is how this
    tab came to apply 0.749 to SBW4 and report a different SBW4 than every other tab."""
    try:
        return float(_get_slider_module().PV_SCALE.get(name, 1.0)) or 1.0
    except (TypeError, ValueError):
        return 1.0


def _pv_label_for(name: str) -> str:
    return _get_slider_module().pv_label_for(name)


def _pv_unit_for(name: str) -> str:
    return _get_slider_module().pv_units_for(name)


_SLIDER_MOD = None


def _get_slider_module():
    """Borrow helpers (GRADIENTS, _copy_metadata_into_png, …) from the Image
    Slider module WITHOUT re-executing 13k lines of is_t.py on every use —
    prefer the instance main.py already loaded, else load once and cache."""
    global _SLIDER_MOD
    mod = sys.modules.get("image_slider")
    if mod is not None:
        return mod
    if _SLIDER_MOD is None:
        import importlib.util as _ilu
        p = Path(__file__).resolve().parent / "is_t.py"
        spec = _ilu.spec_from_file_location("is_t_helpers", p)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SLIDER_MOD = mod
    return _SLIDER_MOD


_SHOT_FINDER_MOD = None


def _get_shot_finder_module():
    """Borrow the Shot Finder's frame resolver (`_find_image_in_day`) and its
    `DayScanCache`, the same way `_get_slider_module` borrows the Slider's.

    There is ONE frame-for-a-timestamp resolver in this program and it lives in
    sf_t.py. A moment found here is therefore the moment found in the Shot
    Finder, and the folder listings it reads are shared with them through the
    cache this tab owns."""
    global _SHOT_FINDER_MOD
    mod = sys.modules.get("shot_finder")
    if mod is not None:
        return mod
    if _SHOT_FINDER_MOD is None:
        import importlib.util as _ilu
        # sf_t looks for the Slider under "image_slider" and execs is_t.py itself
        # if it is not there. Running this tab on its own that would be a SECOND
        # copy of a 27k-line module — a second connection pool and a second day
        # cache beside the one this tab already uses. Publish ours first.
        if sys.modules.get("image_slider") is None:
            sys.modules["image_slider"] = _get_slider_module()
        p = Path(__file__).resolve().parent / "sf_t.py"
        spec = _ilu.spec_from_file_location("sf_t_helpers", p)
        mod = _ilu.module_from_spec(spec)
        # Register before exec, the sibling-loader rule: a re-entrant import must
        # find this half-built module instead of running the file twice.
        sys.modules["sf_t_helpers"] = mod
        spec.loader.exec_module(mod)
        _SHOT_FINDER_MOD = mod
    return _SHOT_FINDER_MOD


# How many cameras are asked for at once when one moment is resolved. Each worker
# spends its time waiting on the share, not on the CPU, and the folder listings
# they need are shared through the DayScanCache — so sixteen of them cost about
# one folder read, not sixteen.
_MOMENT_RESOLVE_WORKERS = 16

# The day-and-region search runs (day x camera) at a time instead of one after the
# other. Fewer workers than the moment fan-out above: a unit here can also ask the
# archiver (the automatic mode reads TotalPower for its day), and the archiver
# answers a wide query with a 500 rather than a queue.
_SEARCH_UNIT_WORKERS = 8
# Reading the primary PV for the marked regions of a day. One per day, so a week of
# days is one round of six rather than six rounds of one.
_SEARCH_DAY_WORKERS = 6

# A camera that had no frame at a moment is remembered as "nothing there" — but
# only once the moment is old enough for that to be final. The archiver is about
# a second behind and a frame may simply not be written yet, so a miss inside
# this window is never cached.
_MOMENT_MISS_MIN_AGE_S = 600

# How many saved moments the session-only list keeps, and how many of them are
# looked for quietly in the background so going back to one is instant.
_SAVED_MOMENTS_MAX = 60
_MOMENT_PREFETCH_KEEP = 12


def _resolve_moment_one(ts_ns: int, cam: str, scan_cache=None) -> dict:
    """Which file holds `cam`'s frame at `ts_ns`.

    A thin adapter over `shot_finder._find_image_in_day`: it turns the moment
    into the Prague day and the naive Prague time the resolver expects (the
    resolver reads `.hour` and converts it to the UTC folder hour itself), and
    hands back what the wall needs.

    `note` says why there is no path, so a camera with nothing near the moment
    can be SHOWN as such instead of quietly disappearing from the wall."""
    sl, sf = _get_slider_module(), _get_shot_finder_module()
    res = {"cam": cam, "path": None, "ts_ns": None, "asked_ns": int(ts_ns),
           "note": ""}
    try:
        dt_p = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            dt_p = dt_p.astimezone(PRAGUE)
        day = date(dt_p.year, dt_p.month, dt_p.day)
        day_dir = (sl.container_root_for_year(day.year)
                   / str(day.year) / str(day.month) / str(day.day))
        path, _cam_folder = sf._find_image_in_day(
            day, cam, dt_p.replace(tzinfo=None), int(ts_ns), {},
            day_dir=day_dir, scan_cache=scan_cache)
        if path is None:
            res["note"] = "no frame near this moment"
            return res
        res["path"] = path
        res["ts_ns"] = sl.parse_unix_ns_from_name(path)
    except Exception as exc:
        res["note"] = f"{type(exc).__name__}"
    return res


class _MomentSignals(QObject):
    """One instance per widget, never one per worker — a `QObject` created for
    each job never dies (~3 KB a time, half a gigabyte of commit in a week)."""
    item = Signal(object, int)          # (resolved item, generation)
    done = Signal(int, float, int)      # (generation, ms spent, folder readings)


class _MomentResolveTask(QRunnable):
    """(moment, camera) pairs → the file holding each camera's frame.

    Given `cams` it is one moment across those cameras, as before. Given `jobs` it
    is an arbitrary set of (ts_ns, camera) pairs — several picked moments across
    several cameras — which is the same fan-out over a longer list: the pairs share
    one folder-listing cache, so two moments inside one hour cost one reading, not
    two."""

    def __init__(self, gen: int, ts_ns: int, cams: "list[str]",
                 signals: "_MomentSignals", stop_flag: "threading.Event",
                 scan_cache=None, jobs: "list | None" = None):
        super().__init__()
        self._gen = int(gen)
        self._ts_ns = int(ts_ns)
        self._jobs = ([(int(t), c) for t, c in jobs] if jobs
                      else [(int(ts_ns), c) for c in (cams or [])])
        self._sig = signals
        self._stop = stop_flag
        self._scan = scan_cache

    def _one(self, job: tuple) -> dict:
        ts_ns, cam = job
        if self._stop.is_set():
            return {"cam": cam, "path": None, "ts_ns": None,
                    "asked_ns": ts_ns, "note": ""}
        return _resolve_moment_one(ts_ns, cam, self._scan)

    def run(self):
        if not self._jobs:
            self._sig.done.emit(self._gen, 0.0, 0)
            return
        t0 = time.perf_counter()
        before = self._scan.stats()[0] if self._scan is not None else 0
        workers = min(_MOMENT_RESOLVE_WORKERS, len(self._jobs))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for res in ex.map(self._one, self._jobs):
                    if self._stop.is_set():
                        return
                    self._sig.item.emit(res, self._gen)
        finally:
            if not self._stop.is_set():
                after = self._scan.stats()[0] if self._scan is not None else 0
                self._sig.done.emit(self._gen,
                                    (time.perf_counter() - t0) * 1000.0,
                                    int(after - before))


_WORKSHOP_MOD = None


def _get_workshop_module():
    """Borrow the Workshop's painted-icon set — the program's one icon vocabulary.

    Only for `action_icon`; the Workshop is where the recipes live because that is
    the tab that needed them first. Loading it standalone is a last resort, so a
    missing Workshop leaves the buttons on plain text rather than failing."""
    global _WORKSHOP_MOD
    mod = sys.modules.get("workshop")
    if mod is not None:
        return mod
    if _WORKSHOP_MOD is None:
        import importlib.util as _ilu
        p = Path(__file__).resolve().parent / "wk_t.py"
        spec = _ilu.spec_from_file_location("wk_t_helpers", p)
        mod = _ilu.module_from_spec(spec)
        sys.modules["wk_t_helpers"] = mod
        spec.loader.exec_module(mod)
        _WORKSHOP_MOD = mod
    return _WORKSHOP_MOD


def _set_action_icon(btn, name: str, ink: str = "#1e2530"):
    """Put a PAINTED icon on a button.

    Text glyphs (`↺`, `↩`) were what these buttons carried, and they render at
    whatever weight the system font feels like — thin, pale and unreadable at the
    panel's size. A drawn icon carries its own artwork for every state, the
    greyed-out one included, which is what stops a disabled button from fading to
    invisible. Failure is not fatal: the button keeps its words."""
    try:
        btn.setIcon(_get_workshop_module().action_icon(name, ink))
        btn.setIconSize(QSize(16, 16))
    except Exception:
        pass


def _cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                        timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    """Fetch archiver samples via the shared pooled client (kept as a thin
    wrapper so existing call sites stay unchanged). Raises cpva.CpvaError.

    An empty answer is asked for again under the channel's OTHER name, when it has
    one: SBW4 lives under a HAPLS-era name and an L3 name, and which of them a
    given stretch of time was written to depends on the configuration that ran, not
    on the date. Without this a region search over such a stretch reports "the PV
    has nothing here" and falls back to the region midpoint, which is how a search
    that should have worked came back with the wrong frame."""
    got = cpva.fetch_samples(channel, start_ns, end_ns, timeout=timeout)
    if got:
        return got
    for alt in cpva.channel_aliases(channel):
        try:
            alt_got = cpva.fetch_samples(alt, start_ns, end_ns, timeout=timeout)
        except cpva.CpvaError:
            continue
        if alt_got:
            return alt_got
    return got


# ── a formula over time ───────────────────────────────────────────────────────
# The registry's own evaluator (is_t.pv_eval_derived) answers "what is this formula
# worth AT ONE MOMENT" and is what the Slider, this tab and the burn-in all use. A
# graph needs the same answer at every moment of the window, which needs two things
# the scalar evaluator does not do: a time base to evaluate ON, and a way to do it
# 20 000 times without freezing anything. Both live here; what a formula MEANS still
# lives in is_t. Moved out of One Moment, which is where this engine was written.

_NS_PER_S = 1_000_000_000

# Two per-shot channels write their samples for the SAME shot tens of milliseconds
# apart. Without a merge window the union of their timestamps holds two points per
# shot, and the first of each pair pairs the new value of one source with the old
# value of the other — a sawtooth that is an artefact of the fetch, not of the laser.
# 137 ms is the CSS Logger's own SAMPLE_HOLD_MIN_GAP_MS over these very channels, so
# both programs group a shot the same way.
_DERIVED_MERGE_GAP_NS = 137_000_000

# Refused rather than thinned: a decimated formula next to full-rate channels is a
# different curve, and one drawn without saying so is worse than one not drawn.
_DERIVED_MAX_POINTS = 200_000

# How long one sample of a source may stand in for the value: 20 × the source's own
# median spacing, clamped. This window mixes ~1 Hz shot channels with sensors read
# once an hour, so a single constant either blanks the slow one or carries a dead
# fast one across an hour the laser was off.
_HOLD_SLACK = 20.0
_HOLD_MIN_NS = 30 * _NS_PER_S
_HOLD_MAX_NS = 3600 * _NS_PER_S

# Tokens whose MEANING changes between a float and an array: min()/max()/round()
# either reduce or raise, math.* takes scalars only, a conditional expression picks
# one whole branch by element 0, and an index or an attribute reaches into the array
# itself. Every one of these was measured against numpy, not guessed.
_NO_VECTOR_RE = re.compile(r"\b(?:min|max|round)\s*\(|\bif\b|\[|\.\s*[A-Za-z_]")

_EMPTY_TS = np.zeros(0, dtype=np.int64)
_EMPTY_VAL = np.zeros(0, dtype=np.float64)

# A channel key on the PV list that names a FORMULA rather than an archiver channel.
_DERIVED_PREFIX = "derived:"


def _is_derived_key(key: str) -> bool:
    return bool(key) and str(key).startswith(_DERIVED_PREFIX)


def _derived_name(key: str) -> str:
    return str(key)[len(_DERIVED_PREFIX):] if _is_derived_key(key) else ""


def derived_plan(names: "list[str]") -> "list[dict]":
    """The formulas among `names`, in registry definition order.

    A SNAPSHOT, taken on the GUI thread and handed to the worker: PV_DERIVED is
    written only by the picker, and a picker accepted mid-load must not change what
    is already being computed. `sources` is the recursive, cycle-guarded LEAF list —
    a formula chained onto another formula lists the second one's channels, because
    that is what the evaluator recomputes the chain from."""
    sl = _get_slider_module()
    want = {n for n in names if sl.pv_is_derived(n)}
    if not want:
        return []
    plan: "list[dict]" = []
    for d in sl.PV_DERIVED:
        nm = str(d.get("name") or "")
        if nm not in want:
            continue
        expr = (d.get("expr") or "").strip()
        bindings = dict(d.get("bindings") or {})
        letters = sl.pv_expr_vars(expr)
        plan.append({
            "name": nm,
            "expr": expr,
            "bindings": bindings,
            "letters": letters,
            "unbound": [l for l in letters if l not in bindings],
            "chained": any(sl.pv_is_derived(str(v)) for v in bindings.values()),
            "sources": sl.pv_source_names([nm]),
        })
    return plan


def _merge_base_ts(parts: "list", gap_ns: int = _DERIVED_MERGE_GAP_NS):
    """The union of the sources' timestamps, near-simultaneous ones collapsed onto
    the LAST of the group — the moment at which every source has published its value
    for that shot."""
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return _EMPTY_TS
    ts = np.unique(np.concatenate([np.asarray(p, dtype=np.int64) for p in parts]))
    if ts.size < 2 or gap_ns <= 0:
        return ts
    keep = np.r_[np.diff(ts) > int(gap_ns), True]
    return ts[keep]


def _hold_index(src_ts, base_ts):
    """Index of the source sample AT OR BEFORE every base timestamp; -1 where the
    source has nothing yet. One searchsorted, not one query per point."""
    base_ts = np.asarray(base_ts, dtype=np.int64)
    if src_ts is None or len(src_ts) == 0:
        return np.full(base_ts.size, -1, dtype=np.int64)
    return np.searchsorted(np.asarray(src_ts, dtype=np.int64),
                           base_ts, side="right").astype(np.int64) - 1


def _hold_limit_ns(src_ts, channel: str) -> int:
    """How long one sample of `channel` may stand in for the value.

    A STEP channel's archiver record IS a step function — a sample only when the
    value changes — so at any instant the last sample is the true value however old
    it is. Everything else is limited, see _HOLD_SLACK."""
    if channel and getattr(cpva, "is_step_channel",
                           lambda *_a, **_k: False)(channel):
        return int(np.iinfo(np.int64).max)
    if src_ts is None or len(src_ts) < 3:
        return _HOLD_MAX_NS
    step = float(np.median(np.diff(np.asarray(src_ts, dtype=np.int64))))
    return int(min(_HOLD_MAX_NS, max(_HOLD_MIN_NS, _HOLD_SLACK * step)))


def _align_source(name: str, d: dict, base_ts,
                  seed: "tuple | None" = None):
    """One source's values on `base_ts`, held forward. NaN where it has no value.

    PV_SCALE is applied here: `pv_eval_derived` is documented to want the registry's
    own factor already in, so a formula cannot mean one thing here and another in
    the Slider."""
    scale = float(_get_slider_module().PV_SCALE.get(name, 1.0))
    base_ts = np.asarray(base_ts, dtype=np.int64)
    out = np.full(base_ts.size, np.nan, dtype=np.float64)
    if base_ts.size == 0:
        return out
    src_ts = np.asarray((d or {}).get("ts", _EMPTY_TS), dtype=np.int64)
    src_val = np.asarray((d or {}).get("val", _EMPTY_VAL), dtype=np.float64)
    limit = _hold_limit_ns(src_ts, str((d or {}).get("channel") or ""))

    if src_ts.size:
        idx = _hold_index(src_ts, base_ts)
        have = idx >= 0
        safe = np.clip(idx, 0, max(src_ts.size - 1, 0))
        vals = src_val[safe] * scale
        age = base_ts - src_ts[safe]
        out[have] = np.where(age[have] <= limit, vals[have], np.nan)
    else:
        have = np.zeros(base_ts.size, dtype=bool)

    # The seed is the last sample BEFORE the window: without it a source read once
    # an hour blanks the formula until its first in-window sample.
    if seed is not None:
        s_ts, s_val = int(seed[0]), float(seed[1])
        before = ~have
        if before.any():
            age = base_ts[before] - s_ts
            out[before] = np.where(age <= limit, s_val * scale, np.nan)
    return out


def _break_gaps(ts, val):
    """Drop the points the formula has no value for, but leave ONE NaN standing in
    each run of them. That NaN is what breaks the line instead of drawing it straight
    across an hour with no data."""
    ts = np.asarray(ts, dtype=np.int64)
    val = np.asarray(val, dtype=np.float64)
    if ts.size == 0:
        return _EMPTY_TS, _EMPTY_VAL
    good = np.isfinite(val)
    if good.all():
        return ts, val
    if not good.any():
        return _EMPTY_TS, _EMPTY_VAL
    bad = ~good
    first_bad = bad.copy()
    first_bad[1:] &= ~bad[:-1]
    keep = good | first_bad
    out = val[keep].copy()
    # The marker is written as NaN whatever it was: a division by zero comes back as
    # ±inf, and one inf in the array pushes matplotlib's y limits out to infinity
    # and flattens every real curve on that axis onto the edge.
    out[~np.isfinite(out)] = np.nan
    return ts[keep], out


def _can_vectorise(expr: str) -> bool:
    return bool(expr) and _NO_VECTOR_RE.search(expr) is None


def _compiled(expr: str):
    """The registry's OWN expression cache, so a formula is compiled once for the
    whole program and an unparsable one is remembered as False."""
    cache = _get_slider_module()._PV_EXPR_CACHE
    code = cache.get(expr)
    if code is None:
        try:
            code = compile(expr, "<pv-derived>", "eval")
        except Exception:
            code = False
        cache[expr] = code
    return code


def _eval_vector(expr: str, ns: dict, n: int):
    """The formula on whole arrays, or None when the answer cannot be trusted.

    Evaluated in the registry's own sandbox, never in a numpy-flavoured copy of it.
    None comes back when the expression is vetoed, when eval raised, or when the
    result is not a float array of exactly `n` points — and that last test, not the
    veto, is what catches a reduction like min(A) collapsing into a perfectly
    straight, perfectly wrong line."""
    if not _can_vectorise(expr):
        return None
    code = _compiled(expr)
    if not code:
        return None
    try:
        with np.errstate(all="ignore"):          # A/0 → inf here, filtered later
            r = eval(code, _get_slider_module()._PV_EVAL_ENV, dict(ns))
    except Exception:
        return None
    if (not isinstance(r, np.ndarray) or r.shape != (n,)
            or r.dtype.kind not in "fiu"):
        return None
    return r.astype(np.float64, copy=False)


def _eval_loop(names: "list[str]", base_ts, cols: dict, statuses: dict,
               stop=None) -> "dict | None":
    """Point by point through is_t.pv_eval_derived — the SAME evaluator the Slider
    and the burn-in use, so an expression the vectoriser refuses is still computed,
    and computed identically. One pass also covers a formula chained onto another
    one. Returns None when it was stopped."""
    sl = _get_slider_module()
    n = int(np.asarray(base_ts).size)
    out = {nm: np.full(n, np.nan, dtype=np.float64) for nm in names}
    keys = list(cols.keys())
    for i in range(n):
        if stop is not None and (i % 512) == 0 and stop.is_set():
            return None
        raw = {}
        for k in keys:
            v = cols[k][i]
            raw[k] = float(v) if np.isfinite(v) else None
        res = sl.pv_eval_derived(names, raw, statuses)
        for nm in names:
            v = res.get(nm, (None, "missing"))[0]
            if v is not None:
                out[nm][i] = float(v)
    return out


def _spot_parity(name: str, base_ts, cols: dict, statuses: dict, vec) -> bool:
    """Check the vectorised answer against the per-point one at three points. Three
    scalar calls, effectively free, and they catch whatever the token veto missed."""
    n = vec.size
    if n == 0:
        return True
    idx = sorted({0, n // 2, n - 1})
    ref = _eval_loop([name], np.asarray(base_ts)[idx],
                     {k: v[idx] for k, v in cols.items()}, statuses)
    if ref is None:
        return False
    return bool(np.allclose(ref[name], vec[idx], rtol=1e-9, atol=0.0,
                            equal_nan=True))


_PV_STATUS_ORDER = {"ok": 0, "approx": 1, "empty": 1, "stale": 2, "error": 3}


def _worst_status(statuses) -> str:
    """The worst of a formula's source statuses — a number derived from a stale
    reading is itself stale, which is `pv_eval_derived`'s own rule."""
    worst = "ok"
    for s in statuses:
        if _PV_STATUS_ORDER.get(s, 0) > _PV_STATUS_ORDER.get(worst, 0):
            worst = s
    return worst


def build_derived_series(plan: "list[dict]", series: dict,
                         windows: "list", seeds: "dict | None" = None,
                         stop=None) -> dict:
    """{formula name → series entry}, built from the sources already fetched.

    ONE TIME BASE PER FORMULA — the union of its own leaf sources' timestamps,
    merged and held forward — and one base PER WINDOW, so a held value never crosses
    a stretch deliberately cut out of the pick (two separate days).

    A formula with an unbound letter, no live source, or a base over
    _DERIVED_MAX_POINTS gets an EMPTY series carrying `reason`, never a silent
    absence: the graph prints the reason where the curve would be."""
    seeds = seeds or {}
    out: dict = {}
    for spec in plan:
        nm = spec["name"]
        expr = spec["expr"]
        entry = {"channel": f"= {expr}" if expr else "= (empty)",
                 "ts": _EMPTY_TS, "val": _EMPTY_VAL,
                 "status": "missing", "reason": ""}
        out[nm] = entry
        if not expr:
            entry["reason"] = "This formula has no expression yet."
            continue
        if spec["unbound"]:
            entry["reason"] = ("letter(s) " + ", ".join(spec["unbound"])
                               + " stand for no PV — bind them in the Image "
                                 "Slider's PV picker.")
            continue
        srcs = [s for s in spec["sources"] if s in series]
        if not srcs:
            entry["reason"] = ("none of this formula's source PVs was read — its "
                               "letters point at PVs that no longer exist.")
            continue
        statuses = {s: series[s].get("status", "ok") for s in srcs}
        entry["status"] = _worst_status(statuses.values())

        # Vectorising is safe only when every letter stands for a READ channel: a
        # formula chained onto another formula is recomputed from its leaves, which
        # only the per-point evaluator does.
        vector_ok = _can_vectorise(expr) and not spec["chained"]

        ts_parts, val_parts = [], []
        n_total = 0
        for a, b in windows:
            if stop is not None and stop.is_set():
                return out
            parts = []
            sliced: dict = {}
            for s in srcs:
                d = series[s]
                ts = np.asarray(d.get("ts", _EMPTY_TS), dtype=np.int64)
                m = (ts >= int(a)) & (ts < int(b))
                sliced[s] = {"ts": ts[m],
                             "val": np.asarray(d.get("val", _EMPTY_VAL),
                                               dtype=np.float64)[m],
                             "channel": d.get("channel", "")}
                parts.append(sliced[s]["ts"])
            base = _merge_base_ts(parts)
            if base.size == 0:
                continue
            n_total += int(base.size)
            if n_total > _DERIVED_MAX_POINTS:
                entry["ts"], entry["val"] = _EMPTY_TS, _EMPTY_VAL
                entry["reason"] = (
                    f"too many samples to compute ({n_total} points) — pick a "
                    "shorter window. Thinning it would draw a different curve.")
                ts_parts = []
                break
            cols = {s: _align_source(s, sliced[s], base,
                                     seed=seeds.get((s, int(a))))
                    for s in srcs}
            vals = None
            if vector_ok:
                ns = {}
                for letter in spec["letters"]:
                    src = spec["bindings"].get(letter)
                    if src not in cols:
                        ns = None
                        break
                    ns[letter] = cols[src]
                if ns is not None:
                    cand = _eval_vector(expr, ns, int(base.size))
                    if cand is not None and _spot_parity(nm, base, cols, statuses,
                                                         cand):
                        vals = cand
            if vals is None:
                got = _eval_loop([nm], base, cols, statuses, stop=stop)
                if got is None:
                    return out                  # stopped
                vals = got[nm]
            ts_parts.append(base)
            val_parts.append(vals)

        if ts_parts:
            ts_all = np.concatenate(ts_parts)
            val_all = np.concatenate(val_parts)
            order = np.argsort(ts_all, kind="stable")
            ts_all, val_all = _break_gaps(ts_all[order], val_all[order])
            entry["ts"], entry["val"] = ts_all, val_all
            if ts_all.size == 0 and not entry["reason"]:
                entry["reason"] = ("every point of this window is missing at least "
                                   "one of the formula's sources.")
        elif not entry["reason"]:
            entry["reason"] = "no source sample inside the picked window."
    return out


def _is_finite(v) -> bool:
    """A real number, and not a NaN or an infinity.

    The archiver hands values back as strings, lists of one, and occasionally None,
    and one infinity in a series pushes a plot's y limits out to infinity and
    flattens every other curve onto the edge."""
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _region_span(region) -> "tuple[int, int]":
    """(start_ns, end_ns) of a marked region, whichever shape it is in.

    PV Search hands regions over as dicts carrying the number, the colour and the
    day; the older callers (and the tests) pass a bare `(start, end)` pair. Both
    are accepted so nothing has to be updated twice."""
    if isinstance(region, dict):
        return int(region["t_start_ns"]), int(region["t_end_ns"])
    a, b = region
    return int(a), int(b)


def _cam_totalpower_channel(cam_name: str) -> "str | None":
    """
    Derive the CPVA TotalPower channel from a camera folder name.
    e.g. "C03-033-PAM1FF-_-IMG" → "C03-033-PAM1FF:TotalPower"
         "C03-033-PAM1FF_-_IMG"  → "C03-033-PAM1FF:TotalPower"
    Returns None if pattern not recognised.
    """
    m = re.match(r"^(C\d{2}-\d{2,3}-[A-Za-z0-9]+)[-_]", cam_name)
    if m:
        return f"{m.group(1)}:TotalPower"
    return None


def _cpva_active_windows_ns(channel: str, start_ns: int, end_ns: int,
                             timeout: float = CPVA_HTTP_TIMEOUT,
                             merge_gap_ns: int = 300_000_000_000,
                             ref_start_ns: "int | None" = None,
                             ref_end_ns:   "int | None" = None,
                             active_from_ns: "int | None" = None,
                             debug_log=None) -> "list[tuple[int,int]]":
    """
    Query TotalPower channel and return merged time windows where camera was active.

    Threshold is derived dynamically from a reference window (6–7h Prague time,
    passed as ref_start_ns / ref_end_ns).  Samples in that window represent
    background noise.  Only samples whose value exceeds 10× the reference median
    (and at least 10× the global minimum) are considered active.

    If no reference samples are available the absolute minimum across the whole
    day is used as the baseline.

    Gaps shorter than merge_gap_ns (default 5 min) are bridged.
    Returns list of (window_start_ns, window_end_ns) tuples, empty on failure.
    """
    dbg = debug_log or (lambda *_: None)
    try:
        samples = _cpva_fetch_samples(channel, start_ns, end_ns, timeout=timeout)
        if not isinstance(samples, list) or not samples:
            dbg(f"  TotalPower debug: no samples returned (got {type(samples).__name__})")
            return []

        # Parse all values
        parsed: list[tuple[int, float]] = []  # (t_ns, value)
        for s in samples:
            t_ns = s.get("time")
            if t_ns is None:
                continue
            val = s.get("value")
            if isinstance(val, list):
                val = val[0] if len(val) == 1 else None
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            parsed.append((int(t_ns), v))

        if not parsed:
            dbg(f"  TotalPower debug: {len(samples)} raw samples, 0 parseable")
            return []

        # ── Determine threshold from data distribution ────────────────────────
        import statistics as _stats

        all_vals = sorted(v for _, v in parsed)
        n = len(all_vals)

        # Use the 5th-percentile as background baseline (robust against cameras
        # that run all day — their 6–7h ref window would be high too, so a
        # fixed ref window is unreliable).
        p05_idx = max(0, int(n * 0.05))
        p95_idx = min(n - 1, int(n * 0.95))
        baseline  = all_vals[p05_idx]
        p95_val   = all_vals[p95_idx]

        dynamic_range = p95_val - baseline

        dbg(f"  TotalPower debug: {len(parsed)} samples | p05={baseline:.3e} p95={p95_val:.3e} ratio={p95_val/baseline:.2f}" if baseline > 0 else f"  TotalPower debug: {len(parsed)} samples | p05={baseline:.3e} p95={p95_val:.3e}")

        if baseline > 0 and p95_val / baseline < 1.5:
            # Signal is flat — camera runs at constant power all day (or is always off).
            # Treat entire queried range as one active window so the caller picks
            # a timestamp from the middle of the day.
            dbg(f"  TotalPower debug: flat signal (ratio<1.5) — treating whole day as active")
            window_start = parsed[0][0]
            window_end   = parsed[-1][0]
            result = [(window_start, window_end)]
            if active_from_ns is not None:
                result = [(max(ws, active_from_ns), we) for ws, we in result if we >= active_from_ns]
            return result

        if dynamic_range > baseline * 0.5 and dynamic_range > 1e-9:
            # Clear on/off signal: threshold at baseline + 30% of dynamic range
            threshold = baseline + dynamic_range * 0.30
        else:
            # Small dynamic range but not flat — use 10× floor
            threshold = max(baseline * 10.0, 1e-6)

        # ── Collect active timestamps ─────────────────────────────────────────
        active_ts = [t for t, v in parsed if v > threshold]
        if not active_ts:
            dbg(f"  TotalPower debug: 0 active samples above threshold={threshold:.3e}")
            return []
        active_ts.sort()

        # ── Merge into windows ────────────────────────────────────────────────
        windows: list[tuple[int, int]] = []
        w_start = active_ts[0]
        w_end   = active_ts[0]
        for t in active_ts[1:]:
            if t - w_end <= merge_gap_ns:
                w_end = t
            else:
                windows.append((w_start, w_end))
                w_start = t
                w_end   = t
        windows.append((w_start, w_end))

        # Drop windows that end before active_from_ns (reference-only period)
        if active_from_ns is not None:
            windows = [(ws, we) for ws, we in windows if we >= active_from_ns]

        return windows
    except Exception as _e:
        dbg(f"  TotalPower debug: exception — {type(_e).__name__}: {_e}")
        return []


def _energy_shots_ranked(channel: str, date_key: str, start_ns: int, end_ns: int,
                         timeout: float = CPVA_HTTP_TIMEOUT,
                         debug_log=None) -> "list[tuple[int, float]]":
    """Every shot the laser fired on one energy channel that day, strongest first.

    This is what tells the multi-day search WHEN the machine was actually running. It
    reads through `cpva.get_day`, which caches by Prague day, so a 15-day × 40-camera
    search asks the archiver 15 times per channel instead of 600 — the same series was
    previously re-fetched for every camera.

    An energy channel reads ~0 between shots, so "there was signal" is simply "a sample
    stands well above the day's floor". The cut is 2 % of the day's peak: high enough
    that readout noise is not mistaken for a shot, low enough that a weak but real day
    still counts. An empty list therefore means "the laser did not fire on this channel
    today", not "the query failed" — failures are logged and also come back empty, which
    is what makes the caller fall through to the next channel.
    """
    dbg = debug_log or (lambda *_: None)
    try:
        res = cpva.get_day(channel, date_key, timeout=timeout)
    except Exception as e:
        dbg(f"  {channel}: query failed — {type(e).__name__}: {e}")
        return []
    samples = list(getattr(res, "samples", None) or ())
    if not samples:
        dbg(f"  {channel}: no samples ({getattr(res, 'status', '?')})")
        return []
    shots = []
    for t_ns, val in samples:
        if not (start_ns <= int(t_ns) <= end_ns):
            continue
        try:
            v = float(val[0] if isinstance(val, (list, tuple)) else val)
        except (TypeError, ValueError):
            continue
        shots.append((int(t_ns), v))
    if not shots:
        dbg(f"  {channel}: {len(samples)} sample(s), none inside the searched hours")
        return []
    peak = max(v for _, v in shots)
    if peak <= 0:
        dbg(f"  {channel}: all samples zero — laser did not fire")
        return []
    cut = peak * 0.02
    shots = [s for s in shots if s[1] > cut]
    shots.sort(key=lambda s: s[1], reverse=True)
    dbg(f"  {channel}: {len(shots)} shot(s) above {cut:.4g}, peak {peak:.4g}")
    return shots


# Annotation bar appearance
ENERGY_BAR_HEIGHT_PX    = 40    # height of white bar added below image
ENERGY_BAR_FONT_SIZE_PT = 24   # font size for annotation text
ENERGY_BAR_BG_COLOR     = (255, 255, 255)   # RGB white
ENERGY_BAR_TEXT_COLOR   = (0,   0,   0)     # RGB black

INFO_TEXT = """\
Image Finder — Image Tools

Steps:
  1. Pick a date — auto-hour is chosen from ramping CSV.
     If "Lab time?" is unchecked: program looks for folders in Prague time.
       Example: for 19:00, it looks in folder 18:00 in the archiver.
     If "Lab time?" is checked: looks in lab time (1 hour later).
  2. Click "Cameras" and pick one or more cameras.
     Search by name or number; a click adds or removes a camera.
  3. The picked cameras are listed under the Workshop button.
     Click one to preview it, double-click it to unpick it.
  4. Click "Load data" (Source group) to read the images, or Save As...
     to copy them to a folder.
  5. Enable "Auto-open in Slider" to automatically switch to the
     Image Slider tab and load the first selected camera folder.
  6. Use Add to A / Add to B + Compare A vs B for diff comparison.
"""

# ── GRADIENTS ─────────────────────────────────────────────────────────────────
def _make_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f * (c1[k] - c0[k])) for k in range(3))
                break
    return lut

def _make_binary_lut():
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[128:] = 255
    return lut

def _make_stepped_lut(stops):
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j + 1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

# NI Vision "Binary", measured off the real viewer — same table and same rule as in
# is_t.py, where how it was measured is written down. 15 colours, black below the first
# band, one colour per 1024 stored 16-bit units.
_NI_BINARY_CYCLE = [
    (255,0,0), (0,255,0), (0,0,255), (255,255,0), (255,0,255), (0,255,255),
    (255,127,0), (255,0,127), (127,255,0), (127,0,255), (0,127,255), (0,255,127),
    (255,127,127), (127,255,127), (127,127,255),
]
NI_BINARY_BAND = 1024
_NI_BINARY_TABLE = np.array([(0, 0, 0)] + _NI_BINARY_CYCLE, dtype=np.uint8)

def _make_ni_binary_lut():
    """The NI Binary rule as a 256-entry LUT over the ABSOLUTE 8-bit scale.

    This tab renders from 8-bit images, so unlike the Image Slider it cannot take the
    exact 16-bit path: one code is 257 stored units against a 1024-unit band, so a code
    on a band edge can land one colour out."""
    codes = np.arange(256, dtype=np.int64) * 257
    band = codes // NI_BINARY_BAND
    idx = np.where(band <= 0, 0, (band - 1) % 15 + 1)
    return _NI_BINARY_TABLE[idx]

# "False Colors" (same definition as in is_t.py): dark blue → violet → purple →
# magenta → pink → white, with the stops crowded at the bottom so faint detail gets
# most of the colour range. One colour family on purpose — a spectrum here only
# duplicates Gradient / Jet / Turbo.
_FALSE_COLORS_STOPS = [
    (0.00, (0,0,0)), (0.03, (25,0,70)), (0.07, (45,0,120)), (0.12, (70,0,160)),
    (0.20, (100,0,180)), (0.30, (130,5,185)), (0.42, (160,20,180)),
    (0.55, (190,40,175)), (0.68, (215,70,170)), (0.80, (235,105,170)),
    (0.90, (247,150,185)), (0.96, (252,200,215)), (1.00, (255,255,255)),
]

# "Rainbow" (same definition as in is_t.py): the NI Vision palette — blue to red with
# a prominent green middle, 0 black and 255 white.
_RAINBOW_STOPS = [
    (0.00, (0,0,0)), (0.04, (0,0,200)), (0.14, (0,40,255)), (0.26, (0,150,255)),
    (0.36, (0,230,180)), (0.46, (0,255,80)), (0.56, (90,255,0)), (0.66, (190,255,0)),
    (0.76, (255,220,0)), (0.86, (255,120,0)), (0.94, (255,0,0)), (1.00, (255,255,255)),
]

GRADIENTS = {
    "Default":         None,
    "Grayscale":       None,
    "Gradient":        _make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Binary":          _make_ni_binary_lut(),
    "False Colors":    _make_lut(_FALSE_COLORS_STOPS),
    "Rainbow":         _make_lut(_RAINBOW_STOPS),
    # Red / yellow lowered, pale yellow added, white kept at the top; see is_t.py.
    "Hot":             _make_lut([(0,(0,0,0)),(0.27,(255,0,0)),(0.53,(255,255,0)),(0.78,(255,255,190)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut(),
    "Viridis":         _make_lut([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}
GRADIENT_NAMES = list(GRADIENTS.keys())

# Palettes mapped onto the frame's own p0.5..p99.5 window instead of the absolute
# 0..255 scale — see is_t.ADAPTIVE_PALETTES for why this one and no others.
ADAPTIVE_PALETTES = frozenset({"False Colors"})
# Cyclic palettes are ABSOLUTE: NI's Binary bands are fixed at 1024 stored units and do
# not follow the frame, so no stretch may run first (see is_t.CYCLIC_PALETTES).
CYCLIC_PALETTES = frozenset({"Binary"})


def _palette_normalize(arr):
    """uint8 frame → uint8 spread over its OWN p0.5..p99.5 window."""
    a = arr if arr.size <= 250_000 else np.ravel(arr)[::(arr.size // 250_000) | 1]
    lo = float(np.percentile(a, 0.5))
    hi = float(np.percentile(a, 99.5))
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        return arr
    return np.clip((arr.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                   0, 255).astype(np.uint8)


def _lut_pixels(lut, arr, name: str):
    """RGB pixels for `arr` under `lut`.

    The adaptive palettes are spread over p0.5..p99.5; everything else, cyclic palettes
    included, is the raw absolute scale."""
    if name in ADAPTIVE_PALETTES:
        arr = _palette_normalize(arr)
    return lut[arr]

_CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
"""

# The same box for a DARK surround (the frame-preview strip, the only one left —
# the PV Search sidebar is light again). Same indicator, light ink: _CHECKBOX_STYLE's
# #111 label is invisible on near-black.
_CHECKBOX_STYLE_DARK = _CHECKBOX_STYLE.replace("color: #111;", "color: #eeeeee;")

# ── STANDALONE HELPERS ────────────────────────────────────────────────────────
def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def load_readme_text() -> str:
    base = _app_dir()
    for fname in ("README.txt", "README.md", "readme.txt", "readme.md"):
        p = base / fname
        try:
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return INFO_TEXT


def _read_img_max_value(path: Path) -> "float | None":
    """The frame's PEAK in raw counts, from PNG tEXt metadata (`MaxValue`).

    Not the sensor's range — it reads 4095 only because a saturated 12-bit frame's
    peak IS 4095. Used here for the empty-frame test; the display scale does not need
    it at all (see img_scale).

    This used to take tEXt chunk number 12 (the Matlab `imgMeta.OtherText{12,2}`
    idiom), i.e. it identified the tag by POSITION: correct for the files we have and
    an arbitrary other number for anything written by a different IMAQ version, which
    then decided "empty" for a perfectly good frame. Now looked up by name."""
    return img_scale.read_max_value(path)


# ── Contrast / Brightness / Gamma rows ─────────────────────────────────────
# Built exactly like the Image Slider's block, down to the short names and the readout
# widths: the same three controls with the same rule (an Auto checkbox overrides its own
# row's slider) have to look the same in both tabs, or the operator learns them twice.
# The names are shortened to Con / Bri / Gam so a numeric readout of the value actually
# in use fits on the same row — acceptable only because the full name and the meaning
# are one hover away: the tooltip sits on the name label, the slider AND the readout.
_BC_NAME_W = 34          # room for "Con:" / "Bri:" / "Gam:" so the three sliders align
_BC_VALUE_W = 38         # room for "-127", "-255", "0.10" without the row jittering

_TT_CONTRAST = (
    "Contrast (-127 to +127) — multiplicative gain around the frame's own black level.\n"
    "0 = untouched; positive spreads the values apart, negative squeezes them together.")
_TT_BRIGHTNESS = (
    "Brightness (-255 to +255) — additive offset: the number is added to every pixel.\n"
    "0 = untouched; positive lifts the whole frame, negative darkens it.")
_TT_GAMMA = (
    f"Gamma ({img_scale.GAMMA_MIN:.2f}–{img_scale.GAMMA_MAX:.2f}) — the number shown is "
    "the exponent of the display curve.\n"
    "1.00 = linear absolute scale. Below 1 lifts the dark end (0.50 is the usable "
    "working point on these cameras); above 1 darkens.\n"
    "Comparability survives — the same pixel value always gives the same colour. "
    "What changes is that equal count differences stop looking equally big.")


def _bc_value_label(text: str, tooltip: str) -> "QLabel":
    """Read-only numeric readout for a Contrast/Brightness/Gamma row."""
    lbl = QLabel(text)
    lbl.setFixedWidth(_BC_VALUE_W)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setToolTip(tooltip)
    lbl.setStyleSheet(
        "QLabel { font-weight: 700; color: #111; }"
        "QLabel:disabled { color: #9a9a9a; }")
    return lbl


def _render_u8(arr, auto: bool, full_scale: float = None, gamma=None,
               contrast: int = 0, offset: int = 0, out: "dict | None" = None):
    """Decoded frame → uint8 for display. Every render path in this tab goes through
    here, so the Finder cannot drift from the Slider on what an intensity means.

    Absolute unless an Auto box is ticked — see img_scale.render_u8. `contrast` and
    `offset` are the manual pair, applied on top of the mapping. `gamma` is in
    slider units and bends the absolute curve without costing comparability. What this
    replaced was `MaxValue * arr / arr.max()` followed by `/4095`, which was the right
    answer only for 12-bit cameras: it rendered the 6–11 bit diode cameras nearly black
    and, with the tEXt chunk missing, blew every frame out to white."""
    if full_scale is None:
        full_scale = img_scale.FULL_SCALE_16
    return img_scale.render_u8(arr, auto, full_scale, gamma, contrast, offset, out)


def _scale_note(info: dict, arr, auto: bool, full_scale: float = None, gamma=None,
                path=None, pil_mode: str = None,
                contrast: int = 0, offset: int = 0,
                gamma_applied: "float | None" = None) -> str:
    """One line saying what the displayed intensities mean, for the label under the
    preview. `info` is an already-open image's `.info` — never re-open the file for it,
    a share read costs 130–160 ms.

    A gamma other than 1 is named here because it moves which count a colour sits on;
    Auto gamma is resolved from the frame so the number shown is the one applied. A
    manual contrast or brightness is named for exactly the same reason. Pass
    `gamma_applied` when the render already resolved Auto gamma, so this does not repeat
    the median pass over the frame.

    "8-bit source" is decided by the PIL MODE when it is given: a 16-bit frame drawn on
    its camera's reference range also has a full_scale of its own (see img_scale), so
    testing the number alone would label it an 8-bit file."""
    applied = None
    if not auto:
        applied = gamma_applied
        if applied is None:
            applied = (img_scale.auto_gamma(arr, full_scale or img_scale.FULL_SCALE_16)
                       if img_scale.is_auto_gamma(gamma)
                       else img_scale.gamma_from_slider(gamma))
    is_8bit = (pil_mode not in ("I", "I;16")) if pil_mode is not None else (
        full_scale is not None and full_scale != img_scale.FULL_SCALE_16)
    if is_8bit:
        mode = "auto stretch" if auto else "absolute scale"
        if applied is not None and abs(applied - img_scale.GAMMA_NEUTRAL) > 0.005:
            mode += f"  ·  gamma {applied:.2f}"
        if contrast:
            mode += f"  ·  contrast {int(contrast):+d}"
        if offset:
            mode += f"  ·  brightness {int(offset):+d}"
        return f"8-bit source  ·  {mode}"
    return img_scale.meta_from_info(info, arr).scale_note(
        auto, gamma, applied,
        img_scale.current_reference_bits(img_scale.camera_from_path(path))
        if path is not None else None,
        contrast, offset)


# Frames whose physical max pixel value is below this are considered "empty"
# (dark frame, no beam). Camera dark noise is typically tens of counts on the
# 12/16-bit sensors here; real shots reach thousands. Tune if a camera differs.
EMPTY_IMG_MAX_THRESHOLD = 100.0
# Pixel-fallback: minimum (max − median) contrast in raw counts to call a
# downscaled decode non-empty.
EMPTY_IMG_CONTRAST_MIN = 50.0


def _image_is_nonempty(path: Path, log=None) -> bool:
    """True when the image plausibly contains a beam (not a dark frame).

    imgMaxValue PNG metadata first (no decode); pixel fallback decodes a
    downscaled copy and checks max−median contrast. Validation failures count
    as EMPTY so the caller moves on to the next candidate."""
    try:
        mv = _read_img_max_value(path)
        if mv is not None:
            ok = mv > EMPTY_IMG_MAX_THRESHOLD
            if log and not ok:
                log(f"  {path.name}: imgMaxValue={mv:.0f} ≤ {EMPTY_IMG_MAX_THRESHOLD:.0f} → empty")
            return ok
        import numpy as _np
        with PilImage.open(str(path)) as pil:
            pil.draft("L", (256, 256))
            if pil.mode in ("I", "I;16"):
                arr = _np.asarray(pil, dtype=_np.float32)
            else:
                arr = _np.asarray(pil.convert("L"), dtype=_np.float32)
        contrast = float(arr.max()) - float(_np.median(arr))
        ok = contrast > EMPTY_IMG_CONTRAST_MIN
        if log and not ok:
            log(f"  {path.name}: pixel contrast {contrast:.0f} ≤ "
                f"{EMPTY_IMG_CONTRAST_MIN:.0f} → empty")
        return ok
    except Exception as e:
        if log:
            log(f"  {path.name}: validation failed ({type(e).__name__}) → treated as empty")
        return False


# The calendar helpers moved to daypicker.py — see the re-export block below,
# after the matplotlib toolbar helpers.


def _make_mpl_toolbar(nav_cls, canvas, parent=None):
    """Build a matplotlib NavigationToolbar with visible icons.

    matplotlib tints the toolbar icons *at construction* and only when the
    palette background is dark — recolouring them to the (light) foreground,
    which under the app's dark palette makes the icons invisible. It never
    re-tints afterwards, so the palette must be right before the toolbar is
    created. We give it a light-palette host parent → tinting is skipped and
    the original black icons survive → then paint a light toolbar background.

    Belt AND braces: the icons are then REPAINTED here in a fixed dark ink, so
    no palette, no style and no Windows dark mode can turn them white again.
    Relying on matplotlib's own decision has already failed once."""
    host = QWidget(parent)
    hp = host.palette()
    hp.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    hp.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
    hp.setColor(QPalette.ColorRole.WindowText, QColor("#202020"))
    hp.setColor(QPalette.ColorRole.ButtonText, QColor("#202020"))
    host.setPalette(hp)

    toolbar = nav_cls(canvas, host)
    toolbar.setStyleSheet(
        "QToolBar { background: #f0f0f0; border: none; spacing: 1px; }"
        "QToolButton { background: transparent; padding: 3px; }"
        "QToolButton:hover { background: #d6d6d6; border-radius: 3px; }"
        "QLabel { color: #202020; }")
    _repaint_mpl_toolbar_icons(toolbar)
    return toolbar


def _repaint_mpl_toolbar_icons(toolbar, ink: str = "#1e2530",
                               ink_off: str = "#9aa0a8"):
    """Redraw every toolbar action's icon from matplotlib's own artwork in `ink`.

    The artwork is black on transparent, so painting the ink THROUGH its own alpha
    keeps the shape and replaces only the colour. Every QIcon mode is spelled out:
    left alone, Qt invents a disabled icon by fading the normal one until it is
    barely there."""
    try:
        from matplotlib import cbook
        from PySide6.QtGui import QIcon, QPainter, QPixmap
    except Exception:
        return
    by_text = {a.text(): a for a in toolbar.actions() if a.text()}
    for item in getattr(toolbar, "toolitems", ()):
        text, _tip, image_file, _cb = item
        act = by_text.get(text)
        if act is None or not image_file:
            continue
        try:
            path = cbook._get_data_path("images", image_file + ".png")
            large = path.with_name(path.name.replace(".png", "_large.png"))
            base = QPixmap(str(large if large.exists() else path))
            if base.isNull():
                continue
            icon = QIcon()
            for mode, col in ((QIcon.Mode.Normal, ink), (QIcon.Mode.Active, ink),
                              (QIcon.Mode.Selected, ink),
                              (QIcon.Mode.Disabled, ink_off)):
                pm = QPixmap(base.size())
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.drawPixmap(0, 0, base)
                p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_SourceIn)
                p.fillRect(pm.rect(), QColor(col))
                p.end()
                for state in (QIcon.State.Off, QIcon.State.On):
                    icon.addPixmap(pm, mode, state)
            act.setIcon(icon)
        except Exception:
            continue


# ── CALENDAR — one widget for the whole program ───────────────────────────────
# This file used to carry TWO calendar stacks of its own: a "house style" one
# (delegate + stylesheet + _style_calendar) and a multi-select one ported from
# Spectra — while the Image Slider carried a third, near-identical copy. A day
# looked and clicked differently depending on which tab you were in. All of it
# now lives in daypicker.py; these names are only re-exported so the call sites
# in this file keep reading the way they always did.
_MS_CAL_STYLE = daypicker.CAL_STYLE
_STD_CAL_STYLE = daypicker.CAL_STYLE
_MultiSelectDelegate = daypicker.MultiSelectDelegate
_WeekendDelegate = daypicker.MultiSelectDelegate
_NoScrollCalendar = daypicker.NoScrollCalendar
_make_multiselect_calendar = daypicker.make_calendar


def _style_calendar(cal) -> None:
    """Apply the house look to a plain QCalendarWidget — for the few places that
    still build their own (PV Region Search's day navigator). A calendar that
    skips this inherits the app's dark stylesheet and comes out unreadable."""
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setGridVisible(True)
    hf = QTextCharFormat()
    hf.setForeground(QColor("#111"))
    hf.setFontWeight(QFont.Weight.Bold)
    cal.setHeaderTextFormat(hf)
    wf = QTextCharFormat()
    wf.setForeground(QColor("#111"))
    for day in (Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday,
                Qt.DayOfWeek.Thursday, Qt.DayOfWeek.Friday):
        cal.setWeekdayTextFormat(day, wf)
    wf_we = QTextCharFormat()
    wf_we.setForeground(QColor("#cc0000"))
    for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
        cal.setWeekdayTextFormat(day, wf_we)
    cal.setStyleSheet(daypicker.CAL_STYLE)
    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal.day_delegate = daypicker.MultiSelectDelegate(cal)
        cal._wk_delegate = cal.day_delegate
        view.setItemDelegate(cal.day_delegate)


# ── NO-SCROLL COMBOBOX ────────────────────────────────────────────────────────
class _NoScrollComboBox(QComboBox):
    """QComboBox that ignores mousewheel — prevents accidental value changes."""
    def wheelEvent(self, event):
        event.ignore()

# ── UI HELPERS ────────────────────────────────────────────────────────────────
def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;")
    return f

def _group_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "font-size: 10px; color: #777; font-weight: 700; "
        "letter-spacing: 1px; padding-top: 2px;"
    )
    return lbl

# Panel group boxes: the Image Slider's own CollapsibleSection, so a group here has
# the same coloured header, the same click-to-fold behaviour and the same pale wash
# of the header colour behind its controls as the groups in the Slider and Workshop.
_SECTION_ACCENTS = {
    "time":     "#2f6fd0",   # blue
    "pv":       "#1a9e9e",   # teal
    "actions":  "#c0392b",   # red
    "display":  "#7a4fc0",   # purple
    "compare":  "#d08a1e",   # amber
}

def _section_cls():
    return _get_slider_module().CollapsibleSection

# ── LOGIC HELPERS (unchanged from original) ───────────────────────────────────
def is_valid_image_file(name: str) -> bool:
    n = name.strip()
    if not n or n.startswith("."): return False
    low = n.lower()
    if low in ("thumbs.db", "desktop.ini"): return False
    return Path(low).suffix in IMAGE_EXTS

def extract_display_label(folder_name: str) -> str:
    """Strip -_-IMG suffix (and variants) from camera folder names, keep the rest."""
    s = folder_name.strip()
    # Remove trailing -_-IMG or _-_IMG (case-insensitive), keep everything before it
    cleaned = re.sub(r"[-_]+IMG$", "", s, flags=re.IGNORECASE).rstrip("-_")
    return cleaned

def extract_folder_number(folder_name: str) -> str:
    s = folder_name.strip()
    # 3-digit: C03-047-...  or  2-digit: C03-47-...
    m = re.match(r"^C\d{2}-(\d{2,3})-", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"-(?:C\d{2})-(\d{2,3})(?:-|$)", s, flags=re.IGNORECASE)
    if m: return m.group(1)
    m = re.search(r"-(C\d{2,3})(?:-|$)", s, flags=re.IGNORECASE)
    if m: return m.group(1).upper()
    return ""

def extract_ns_from_stem(stem: str):
    m = SOURCE_RE.search(stem)
    if not m: return None
    try: return int(m.group(1))
    except: return None

def convert_timestamp(ns: int, use_prague_time: bool) -> str:
    dt_utc = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
    dt = dt_utc.astimezone(PRAGUE) if use_prague_time else dt_utc
    ms = (ns % 1_000_000_000) // 1_000_000
    return dt.strftime("%Y-%m-%d_%H-%M-%S-") + f"{ms:03d}"

_CAM_IMG_MARK_RE = re.compile(r"[-_]+IMG(?=$|[-_])", re.IGNORECASE)
_CAM_CONTAINER_RE = re.compile(r"^C\d{2}[-_]", re.IGNORECASE)

def clean_cam_for_filename(cam: str) -> str:
    """Camera token as it should appear in a saved file name:
    'C03-040-PFM13NF-_-IMG' -> '040-PFM13NF'.

    The '-IMG' marker and the leading container code carry no information for the
    person looking at the file. Cameras without a 'Cxx-' prefix keep whatever
    they have."""
    s = _CAM_IMG_MARK_RE.sub("", cam).strip("-_")
    return _CAM_CONTAINER_RE.sub("", s, count=1).strip("-_")

def build_new_name(stem: str, use_prague_time: bool):
    # cam token + trailing ns timestamp → "<clean cam>_<timestamp>"
    if FINAL_RE.search(stem): return None, "already_converted"
    m = SOURCE_RE.search(stem)
    if not m: return None, "no_trailing_number"
    ns = int(m.group(1))
    cam = clean_cam_for_filename(stem[:m.start(1)])
    time_str = convert_timestamp(ns, use_prague_time)
    return (f"{cam}_{time_str}" if cam else time_str), None


# ── ENERGY CSV ENGINE ────────────────────────────────────────────────────────

def _energy_csv_path(dt: datetime) -> Path:
    """
    Build the path to the daily CSV file for a given datetime.
    Pattern: ENERGY_CSV_ROOT / dataof{year}{MonthAbbr}_{day}
    Example: dataof2026Mar_24  (month abbreviation capitalised as in strftime)
    """
    fname = dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"   # e.g. "dataof2026Mar_24.csv"
    return Path(ENERGY_CSV_ROOT) / fname


class _EnergyRow:
    """One parsed row from the daily CSV."""
    __slots__ = ("ts_dt", "values")

    def __init__(self, ts_dt: datetime, values: dict[str, str]):
        self.ts_dt  = ts_dt
        self.values = values


def _load_energy_csv(csv_path: Path) -> list[_EnergyRow]:
    """
    Load a daily CSV file and return a list of _EnergyRow sorted by timestamp.
    Returns [] on any error (file missing, wrong format, network issue).
    Timestamp column: 'Timestamp', format: '2026-03-24 09:55:15.152'
    """
    rows: list[_EnergyRow] = []
    try:
        raw = csv_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return rows

    if not raw.strip():
        return rows

    # Auto-detect delimiter
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=[",", ";", "\t"])
        delim = dialect.delimiter
    except Exception:
        delim = ","

    reader = csv.DictReader(raw.splitlines(), delimiter=delim)
    if reader.fieldnames is None:
        return rows

    # Strip whitespace from field names
    fieldnames_stripped = [f.strip() for f in reader.fieldnames]

    for r in reader:
        # Re-key with stripped names
        row_clean = {k.strip(): v for k, v in r.items() if k is not None}

        ts_str = row_clean.get("Timestamp", "").strip()
        if not ts_str:
            continue
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

        rows.append(_EnergyRow(dt, row_clean))

    rows.sort(key=lambda r: r.ts_dt)
    return rows


def _energy_api_for_day(
    dt: datetime,
    cols: list[str],
    csv_root: "str | None" = None,
    log=None,
) -> "tuple[list[_EnergyRow], dict, bool]":
    """
    Query CPVA archiver for the given day and return (_EnergyRow list, per_col dict,
    had_error). Falls back column-by-column to CSV when API returns nothing.
    dt should be a naive Prague-local datetime (used only for the date).

    Returns:
      - merged: list[_EnergyRow] sorted by timestamp (merged across all channels)
      - per_col: dict[str, list[_EnergyRow]] mapping each column to sorted rows that
                 have a value for that column (used for per-column closest-timestamp
                 lookup). Also carries precomputed "_ns:<col>" → list[int] arrays so
                 the per-file lookup is a pure bisect instead of O(rows) rebuilds.
      - had_error: True if any column's API fetch FAILED (as opposed to genuinely
                   having no data) and CSV had nothing either — the caller must NOT
                   cache such a day, so the next lookup retries.
    """
    date_key = dt.strftime("%Y-%m-%d")

    def _log(msg):
        if log is not None:
            log(msg)

    # Collect per-column rows: {t_ns: {col: value, ...}}
    by_ts: dict[int, dict] = {}
    # Per-column row lists: col -> list of (t_ns, dt_local, val)
    per_col_raw: dict[str, list[tuple[int, datetime, str]]] = {}
    err_flags: dict[str, bool] = {}
    src_flags: dict[str, str] = {}   # col → "api" | "csv"

    def _fetch_one_col(col: str) -> "tuple[str, list[tuple[int, datetime, str]], bool, str]":
        # `col` is a registry NAME. get(col, col) is what makes an arbitrary archiver
        # channel work at all: it used to be get(col) alone, so anything outside the
        # eight presets resolved to None and fell straight through to a CSV that has
        # never heard of it.
        channel = _pv_channel_for(col)
        col_rows: list[tuple[int, datetime, str]] = []
        had_error = False
        src = "api"

        if channel is not None:
            res = cpva.get_day(channel, date_key, timeout=CPVA_HTTP_TIMEOUT)
            if res.status == "error":
                had_error = True
                _log(f"  API {col} ({channel}) FETCH FAILED — will retry on next lookup")
            elif res.status == "stale":
                _log(f"  API {col} ({channel}): fetch failed, showing "
                     f"{len(res.samples)} samples from {res.age_s:.0f}s ago")
            for t_ns, v in res.samples:
                if PRAGUE is not None:
                    dt_local = datetime.fromtimestamp(
                        t_ns / 1e9, tz=timezone.utc).astimezone(PRAGUE).replace(tzinfo=None)
                else:
                    dt_local = datetime.utcfromtimestamp(t_ns / 1e9)
                col_rows.append((t_ns, dt_local, str(v)))
            if res.samples:
                _log(f"  API {col} ({channel}): {len(res.samples)} samples")
        else:
            _log(f"  {col}: no CPVA channel mapping, trying CSV only")

        # CSV fallback if API returned nothing — only for a PV that HAS a column in
        # that file. An arbitrary archiver channel has none, and looking for it used to
        # mean opening the day's CSV once per such PV to find nothing.
        csv_col = _pv_csv_col(col)
        if not col_rows and csv_col is not None:
            root = csv_root if csv_root is not None else ENERGY_CSV_ROOT
            fname = dt.strftime(ENERGY_CSV_NAME_FMT) + ".csv"
            csv_path = Path(root) / fname
            csv_rows = _load_energy_csv(csv_path)
            for r in csv_rows:
                if csv_col in r.values:
                    if PRAGUE is not None:
                        t_ns = int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000)
                    else:
                        t_ns = int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
                    # Stored under the REGISTRY NAME, not under the CSV column name:
                    # everything downstream looks a value up by the name that was picked.
                    col_rows.append((t_ns, r.ts_dt, r.values[csv_col]))
            if col_rows:
                had_error = False   # CSV covered the outage
                src = "csv"
                _log(f"  CSV fallback {col}: {len(col_rows)} rows")

        return col, col_rows, had_error, src

    with ThreadPoolExecutor(max_workers=max(1, len(cols))) as _aex:
        _col_futs = {_aex.submit(_fetch_one_col, c): c for c in cols}
        for _fut in as_completed(_col_futs):
            try:
                _col, _col_rows, _col_err, _col_src = _fut.result()
                err_flags[_col] = _col_err
                src_flags[_col] = _col_src
                if _col_rows:
                    per_col_raw[_col] = _col_rows
                for t_ns, dt_local, val in _col_rows:
                    if t_ns not in by_ts:
                        by_ts[t_ns] = {}
                    by_ts[t_ns].setdefault("_dt", dt_local)
                    by_ts[t_ns][_col] = val
            except Exception as exc:
                err_flags[_col_futs[_fut]] = True
                _log(f"  col fetch ERROR: {type(exc).__name__}: {exc}")

    # Convert to _EnergyRow objects sorted by timestamp
    result: list[_EnergyRow] = []
    for t_ns in sorted(by_ts):
        entry = by_ts[t_ns]
        dt_local = entry.get("_dt", datetime.utcfromtimestamp(t_ns / 1e9))
        values = {k: v for k, v in entry.items() if k != "_dt"}
        result.append(_EnergyRow(dt_local, values))

    # Build per_col: col -> sorted list of _EnergyRow that have only that col's
    # value, plus parallel "_ns:<col>" sorted int arrays for direct bisect and
    # "_src:<col>" data-source markers (API rows use a tighter match tolerance).
    per_col: dict = {}
    for col, rows_raw in per_col_raw.items():
        rows_raw_sorted = sorted(rows_raw, key=lambda x: x[0])
        per_col[col] = [
            _EnergyRow(dt_local, {col: val})
            for t_ns, dt_local, val in rows_raw_sorted
        ]
        per_col[f"_ns:{col}"] = [t_ns for t_ns, _dt, _v in rows_raw_sorted]
        per_col[f"_src:{col}"] = src_flags.get(col, "csv")

    return result, per_col, any(err_flags.values())


def _find_energy_match(
    rows: list[_EnergyRow],
    img_ts_ns: int,
    tol_s: float = ENERGY_MATCH_TOL_S,
) -> tuple[_EnergyRow | None, _EnergyRow | None, _EnergyRow | None]:
    if not rows:
        return None, None, None

    # Convert image UTC nanosecond timestamp to Prague local datetime
    img_dt = datetime.fromtimestamp(img_ts_ns / 1_000_000_000, tz=timezone.utc)
    if PRAGUE:
        img_dt = img_dt.astimezone(PRAGUE).replace(tzinfo=None)
    else:
        img_dt = img_dt.replace(tzinfo=None)

    # Binary search by datetime
    ts_list = [r.ts_dt for r in rows]
    idx = bisect.bisect_left(ts_list, img_dt)

    candidates = []
    if idx > 0: candidates.append(rows[idx - 1])
    if idx < len(rows): candidates.append(rows[idx])

    best = min(candidates, key=lambda r: abs((r.ts_dt - img_dt).total_seconds()), default=None)
    if best and abs((best.ts_dt - img_dt).total_seconds()) <= tol_s:
        return best, None, None

    before = rows[idx - 1] if idx > 0 else None
    after  = rows[idx]     if idx < len(rows) else None
    return None, before, after

def _find_closest_per_col_value(
    per_col: "dict[str, list[_EnergyRow]]",
    col: str,
    target_ns: int,
    tol_s: float = 2.0,
) -> str:
    """
    Find the closest-timestamp value for `col` in per_col within tol_s seconds of target_ns.
    per_col maps column name -> sorted list of _EnergyRow objects that have a value for that col.
    Returns the formatted-raw value string, or "—" if no row is within tolerance.
    """
    rows = per_col.get(col)
    if not rows:
        return "—"
    # Use the precomputed ns array when present ("_ns:<col>" sidecar built by
    # _energy_api_for_day / _build_per_col_from_rows) — rebuilding it here made
    # the lookup O(files × cols × rows).
    ns_list = per_col.get(f"_ns:{col}")
    if ns_list is None or len(ns_list) != len(rows):
        # Fallback for dicts built elsewhere — compute locally, do NOT write
        # back into per_col (it is shared across worker threads via the cache).
        ns_list = []
        for r in rows:
            if PRAGUE is not None:
                r_ns = int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000)
            else:
                r_ns = int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000)
            ns_list.append(r_ns)
    idx = bisect.bisect_left(ns_list, target_ns)
    best_val = None
    best_diff = float("inf")
    for i in [idx - 1, idx]:
        if 0 <= i < len(rows):
            diff = abs(ns_list[i] - target_ns)
            if diff < best_diff:
                best_diff = diff
                best_val = rows[i].values.get(col, "—")
    tol_ns = int(tol_s * 1_000_000_000)
    if best_val is not None and best_diff <= tol_ns:
        return best_val
    return "—"


def _build_per_col_from_rows(
    rows: "list[_EnergyRow]",
) -> "dict[str, list[_EnergyRow]]":
    """
    Build the per-column closest-timestamp lookup table from plain CSV rows.

    Returns {col: [rows with a non-empty value for col]} — each list keeps the
    chronological order of `rows` (which _load_energy_csv already sorts by ts),
    so _find_closest_per_col_value can binary-search it directly.
    """
    per_col: dict = {}
    for r in rows:
        for col, val in r.values.items():
            if col == "Timestamp":
                continue
            if val is not None and str(val).strip() not in ("", "—"):
                per_col.setdefault(col, []).append(r)
    # Same "_ns:<col>"/"_src:<col>" sidecars as _energy_api_for_day builds, so
    # _find_closest_per_col_value never needs its O(rows) rebuild fallback.
    for col in [c for c in per_col if not c.startswith("_")]:
        ns_list = []
        for r in per_col[col]:
            if PRAGUE is not None:
                ns_list.append(int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1_000_000_000))
            else:
                ns_list.append(int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1_000_000_000))
        per_col[f"_ns:{col}"] = ns_list
        per_col[f"_src:{col}"] = "csv"
    return per_col


def _format_energy_diff_s(diff_s: float) -> str:
    """Format a time difference in seconds to a readable string."""
    diff_s = abs(diff_s)
    if diff_s < 1.0:
        return f"{diff_s*1000:.0f} ms"
    return f"{diff_s:.1f} s"

# Energies small enough to be read in mJ. House convention, kept: these two are
# fractions of a joule and "0.0031 J" is harder to compare at a glance than "3.10 mJ".
_MJ_NAMES = {"Back_Ref", "PAP1"}
# Columns that only ever lived in Salvation's CSV and hold a 0/1 flag.
_YESNO_NAMES = {"CampOn", "E2_Open", "E3_Open", "E4_Open", "E5_Open"}


def _format_energy_value(name: str, raw_val: str) -> str:
    """One PV's value as text, keyed by its REGISTRY NAME.

    SBW4 used to be multiplied by 0.749 here, so this tab printed the compressed
    energy under the name of the channel that reads the uncompressed one — a number
    that matched no other tab and no archiver query. A PV now reports what the
    archiver holds; the only factor left is the registry's own for an entry that
    exists to BE a conversion ("Compressed SBW4")."""
    v = raw_val.strip() if raw_val else "—"
    if v == "—" or v == "":
        return "—"
    if name in _YESNO_NAMES:
        try:
            return "YES" if int(float(v)) == 1 else "NO"
        except Exception:
            return v
    # Waveplate — plain number, no unit, snapped onto its 1000-count grid. The
    # waveplate is only ever commanded to whole multiples of 1000, so anything else
    # is the motor readback caught mid-travel. int() also TRUNCATED, so a settled
    # 349 999.6 printed as 349 999 — one count below a position that does exist.
    if name == "Waveplate" or name == "waveplate":
        try:
            ch = _pv_channel_for(name) or "waveplate"
            return f"{cpva.quantize(ch, float(v))[0]:.0f}"
        except Exception:
            return v
    try:
        v_f = float(v) * _pv_scale_for(name)
    except Exception:
        return v
    if name in _MJ_NAMES:
        return f"{v_f * 1000:.2f} mJ"
    unit = _pv_unit_for(name)
    if unit == "J":
        return f"{v_f:.3f} J"
    return f"{v_f:.4g} {unit}".strip() if unit else f"{v_f:.4g}"

def _annotate_image_with_energy(
    src: Path,
    dst: Path,
    match_row: _EnergyRow | None,
    no_match_before: _EnergyRow | None,
    no_match_after: _EnergyRow | None,
    img_ts_ns: int,
    selected_cols: list[str],
) -> None:
    """
    Add a white annotation bar below the image and save to dst.
    If match_row is given: write selected column values.
    Otherwise: write a "No match" message with nearest timestamps.
    """
    from PIL import Image as _Img, ImageDraw, ImageFont as _IF

    img = _Img.open(src)

    # Build annotation text
    if match_row is not None:
        parts = []
        for col in selected_cols:
            val   = _format_energy_value(col, match_row.values.get(col, "—"))
            parts.append(f"{_pv_label_for(col)}: {val}")
        text = "   |   ".join(parts) if parts else "(no columns selected)"
    else:
        # PV values only — no timestamps or extra info in the bar
        parts = [f"{_pv_label_for(col)}: n/a" for col in selected_cols]
        text = "   |   ".join(parts) if parts else "n/a"

    # Create bar — dynamický počet řádků, font a výška se přizpůsobí obsahu
    w, h = img.size
    _tmp_draw = ImageDraw.Draw(_Img.new("RGB", (1, 1)))

    # Načti font
    font = None
    for _fname in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            font = _IF.truetype(_fname, ENERGY_BAR_FONT_SIZE_PT)
            break
        except Exception:
            continue
    if font is None:
        font = _IF.load_default()

    # Rozděl text na části podle separátoru
    parts_list = text.split("   |   ")

    # Najdi font size a počet řádků tak aby se vše vešlo
    chosen_font = font
    display_lines = [text]  # fallback
    for fsize in range(ENERGY_BAR_FONT_SIZE_PT, 7, -1):
        # Načti font v této velikosti
        _f = None
        for _fname in (
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "DejaVuSans.ttf",
        ):
            try:
                _f = _IF.truetype(_fname, fsize)
                break
            except Exception:
                continue
        if _f is None:
            _f = _IF.load_default()

        # Zkus nejprve jeden řádek
        try:
            bbox = _tmp_draw.textbbox((0, 0), text, font=_f)
            if (bbox[2] - bbox[0]) <= w - 20:
                chosen_font = _f
                display_lines = [text]
                break
        except Exception:
            pass

        # Zkus rozdělit na více řádků (2, 3, 4...)
        fitted = False
        for n_lines in range(2, len(parts_list) + 1):
            # Rozdělení parts_list rovnoměrně do n_lines řádků
            chunk = max(1, len(parts_list) // n_lines)
            lines = []
            for i in range(0, len(parts_list), chunk):
                lines.append("   |   ".join(parts_list[i:i + chunk]))
            # Zkontroluj šířku nejdelšího řádku
            max_w = 0
            try:
                for line in lines:
                    bb = _tmp_draw.textbbox((0, 0), line, font=_f)
                    max_w = max(max_w, bb[2] - bb[0])
            except Exception:
                max_w = w
            if max_w <= w - 20:
                chosen_font = _f
                display_lines = lines
                fitted = True
                break
        if fitted:
            break

    # Změř výšku jednoho řádku
    try:
        bb = _tmp_draw.textbbox((0, 0), "Ag", font=chosen_font)
        line_h = bb[3] - bb[1]
    except Exception:
        line_h = ENERGY_BAR_FONT_SIZE_PT + 4
    padding = 10
    bar_h = max(ENERGY_BAR_HEIGHT_PX, line_h * len(display_lines) + padding * (len(display_lines) + 1))

    bar = _Img.new("RGB", (w, bar_h), ENERGY_BAR_BG_COLOR)
    draw = ImageDraw.Draw(bar)

    # Kresli každý řádek vycentrovaný
    total_text_h = line_h * len(display_lines) + padding * (len(display_lines) - 1)
    y = (bar_h - total_text_h) // 2
    for line in display_lines:
        try:
            bb = draw.textbbox((0, 0), line, font=chosen_font)
            text_w = bb[2] - bb[0]
        except Exception:
            text_w = 0
        x = max(8, (w - text_w) // 2)
        draw.text((x, y), line, fill=ENERGY_BAR_TEXT_COLOR, font=chosen_font)
        y += line_h + padding

    combined = _Img.new("RGB", (w, h + bar_h), ENERGY_BAR_BG_COLOR)
    combined.paste(img.convert("RGB"), (0, 0))
    combined.paste(bar, (0, h))
    combined.save(dst)


def _write_annotated_with_text(src: Path, dst: Path, text: str) -> None:
    """
    Add a white annotation bar below image with arbitrary text, save to dst.
    Used when saving a frame with its camera name, timestamp and PV values burnt in.
    """
    from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF

    img = _Img.open(src)
    w, h = img.size

    fsize = ENERGY_BAR_FONT_SIZE_PT
    font = None
    for _fname in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            font = _IF.truetype(_fname, fsize)
            break
        except Exception:
            continue
    if font is None:
        font = _IF.load_default()

    parts_list = text.split("   |   ")
    # Fit parts onto 1 or 2 lines
    tmp_draw = _ID.Draw(_Img.new("RGB", (1, 1)))
    line_w = max((tmp_draw.textlength(p, font=font) for p in parts_list), default=0)
    padding = 6
    line_h = fsize + padding

    if line_w <= w - padding * 2:
        display_lines = ["   |   ".join(parts_list)]
    else:
        # Split roughly in half
        mid = len(parts_list) // 2 or 1
        display_lines = [
            "   |   ".join(parts_list[:mid]),
            "   |   ".join(parts_list[mid:]),
        ]

    bar_h = line_h * len(display_lines) + padding
    bar = _Img.new("RGB", (w, bar_h), (255, 255, 255))
    draw = _ID.Draw(bar)
    y = padding // 2
    for line in display_lines:
        draw.text((padding, y), line, font=font, fill=(0, 0, 0))
        y += line_h

    combined = _Img.new("RGB", (w, h + bar_h), (255, 255, 255))
    combined.paste(img.convert("RGB"), (0, 0))
    combined.paste(bar, (0, h))
    combined.save(dst)


def _write_annotated_from_pil(img: "PilImage.Image", dst: Path, text: str) -> None:
    """
    Add a white annotation bar below an already-rendered PIL image and save to dst.
    Font size is auto-scaled so all PV parts fill the bar as large as possible,
    splitting into multiple lines as needed (same logic as Shot Finder).
    """
    from PIL import Image as _Img, ImageDraw as _ID, ImageFont as _IF

    img = img.convert("RGB")
    w, h = img.size

    _FONT_CANDIDATES = (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "DejaVuSans.ttf",
    )
    parts_list = [p for p in text.split("   |   ") if p]
    tmp_draw = _ID.Draw(_Img.new("RGB", (1, 1)))
    padding = 8

    chosen_font = None
    display_lines = [text]

    for fsize in range(28, 7, -1):
        _f = None
        for _fname in _FONT_CANDIDATES:
            try:
                _f = _IF.truetype(_fname, fsize)
                break
            except Exception:
                continue
        if _f is None:
            _f = _IF.load_default()

        # Try fitting everything on one line first
        try:
            bb = tmp_draw.textbbox((0, 0), text, font=_f)
            if (bb[2] - bb[0]) <= w - padding * 2:
                chosen_font = _f
                display_lines = [text]
                break
        except Exception:
            pass

        # Try splitting into increasing number of lines
        fitted = False
        for n_lines in range(2, len(parts_list) + 1):
            chunk = max(1, len(parts_list) // n_lines)
            lines = []
            for i in range(0, len(parts_list), chunk):
                lines.append("   |   ".join(parts_list[i:i + chunk]))
            max_w = 0
            try:
                for line in lines:
                    bb2 = tmp_draw.textbbox((0, 0), line, font=_f)
                    max_w = max(max_w, bb2[2] - bb2[0])
            except Exception:
                max_w = w
            if max_w <= w - padding * 2:
                chosen_font = _f
                display_lines = lines
                fitted = True
                break
        if fitted:
            break

    if chosen_font is None:
        for _fname in _FONT_CANDIDATES:
            try:
                chosen_font = _IF.truetype(_fname, 8)
                break
            except Exception:
                continue
        if chosen_font is None:
            chosen_font = _IF.load_default()
        display_lines = [text]

    try:
        bb_ref = tmp_draw.textbbox((0, 0), "Ag", font=chosen_font)
        line_h = bb_ref[3] - bb_ref[1]
    except Exception:
        line_h = 14

    bar_h = max(30, line_h * len(display_lines) + padding * (len(display_lines) + 1))
    bar = _Img.new("RGB", (w, bar_h), (255, 255, 255))
    draw = _ID.Draw(bar)
    total_text_h = line_h * len(display_lines) + padding * (len(display_lines) - 1)
    y = (bar_h - total_text_h) // 2
    for line in display_lines:
        try:
            bb = draw.textbbox((0, 0), line, font=chosen_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = 0
        x = max(padding, (w - tw) // 2)
        draw.text((x, y), line, font=chosen_font, fill=(0, 0, 0))
        y += line_h + padding

    combined = _Img.new("RGB", (w, h + bar_h), (255, 255, 255))
    combined.paste(img, (0, 0))
    combined.paste(bar, (0, h))
    combined.save(str(dst))


# ── ENERGY CSV LOADER (async) ─────────────────────────────────────────────────

class _EnergyLoadSignals(QObject):
    finished = Signal(list)   # list[_EnergyRow]

class _EnergyLoadTask(QRunnable):
    """Load CSV in a background thread."""
    def __init__(self, csv_path: Path, signals: "_EnergyLoadSignals"):
        super().__init__()
        self._path   = csv_path
        self._signals = signals

    def run(self):
        rows = _load_energy_csv(self._path)
        self._signals.finished.emit(rows)


# ── COLUMN PICKER DIALOG ──────────────────────────────────────────────────────

# EnergyColumnDialog used to live here: a fixed list of the twelve Salvation CSV
# columns as tick boxes. It is gone — the PV picker is the Image Slider's
# PvConfigDialog now (see _pick_energy_columns), which reads the shared registry, can
# search the whole archiver, takes a channel typed in full, and carries the names,
# units and formulas. Two pickers over one registry is what let the two tabs disagree
# about what "SBW4" means.


class _LoadSignals(QObject):
    done      = Signal(list, dict, int)
    not_found = Signal(object)
    error     = Signal(str)
    log_msg   = Signal(str)

class _CollectSignals(QObject):
    done = Signal(list)

class _CompareSignals(QObject):
    done  = Signal(object, object, object, object)
    error = Signal(str)

class _LogSignals(QObject):
    msg = Signal(str)

class _PreviewSignals(QObject):
    ready = Signal(object, int)   # (QPixmap, gen)


class _TryAgainSignals(QObject):
    progress  = Signal(str, str)                        # main label, hour label
    cell_done = Signal(str, object, int, object, str)   # cam, date, hour, path|None, log line
    finished  = Signal(list)                            # summary lines


# ── CAMERA PICKER ─────────────────────────────────────────────────────────────
class _CameraPickDialog(QDialog):
    """Pick the cameras to search — the Image Slider's camera picker, over the
    cameras the day scan actually found.

    This replaces the always-on camera table that used to fill half the tab. The
    list is only interesting while you are choosing, so it lives in a dialog and
    the panel keeps just the short list of what is picked.

    The presets are the SAME file the Image Slider writes (cam_presets.json), so a
    set of cameras saved in one tab is offered in the other. A preset can name a
    camera that this day has no folder for; loading it picks what is there and says
    how many were missing.
    """

    _PRESETS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "cam_presets.json"

    def __init__(self, cams: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select cameras")
        self.resize(760, 640)

        # (name, num, label) per camera, in the order the scan found them
        self._cams = [(c["name"], c.get("num", ""), c.get("label", c["name"]))
                      for c in cams]
        self._selected: list[str] = [c["name"] for c in cams if c.get("checked")]
        self._shown: list[tuple] = list(self._cams)
        self._presets: dict = self._load_presets()

        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # ── Left: search + the cameras this day has ──────────────────────────
        left = QVBoxLayout(); left.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search cameras…")
        self._search.setToolTip(
            "Type any part of the name or the camera number. Several words are\n"
            "all required, in any position — \"pt near\" finds \"PT_NearField\".")
        self._search.textChanged.connect(self._filter)
        left.addWidget(self._search)

        self._list = QTableWidget(0, 2)
        self._list.setHorizontalHeaderLabels(["#", "Camera"])
        self._list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.verticalHeader().setVisible(False)
        self._list.cellClicked.connect(self._on_row_clicked)
        left.addWidget(self._list, 1)

        all_row = QHBoxLayout()
        btn_all = QPushButton("Select all")
        btn_all.setToolTip("Every camera the search box currently shows")
        btn_all.clicked.connect(self._select_all_shown)
        btn_none = QPushButton("Clear")
        btn_none.setToolTip("Unpick every camera")
        btn_none.clicked.connect(self._clear_all)
        all_row.addWidget(btn_all); all_row.addWidget(btn_none)
        all_row.addStretch(1)
        left.addLayout(all_row)

        self._status = QLabel("")
        self._status.setStyleSheet("font-size: 10px; color: #555;")
        self._status.setWordWrap(True)
        left.addWidget(self._status)

        top_row.addLayout(left, 3)

        # ── Right: presets, shared with the Image Slider ─────────────────────
        right = QVBoxLayout(); right.setSpacing(4)
        preset_lbl = QLabel("Presets")
        preset_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #333;")
        right.addWidget(preset_lbl)

        self._preset_list = QTableWidget(0, 1)
        self._preset_list.setHorizontalHeaderLabels(["Name"])
        self._preset_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._preset_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._preset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._preset_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preset_list.verticalHeader().setVisible(False)
        self._preset_list.setToolTip(
            "Click a preset to pick its cameras. The same presets the Image Slider "
            "has — saving one here offers it there too.")
        self._preset_list.itemSelectionChanged.connect(self._on_preset_load)
        right.addWidget(self._preset_list, 1)

        btn_save = QPushButton("Save")
        btn_save.setToolTip("Save the cameras picked right now under a name")
        btn_rename = QPushButton("Rename")
        btn_delete = QPushButton("Delete")
        for b in (btn_save, btn_rename, btn_delete):
            b.setFixedHeight(24)
            right.addWidget(b)
        btn_save.clicked.connect(self._on_preset_save)
        btn_rename.clicked.connect(self._on_preset_rename)
        btn_delete.clicked.connect(self._on_preset_delete)
        right.addStretch()

        top_row.addLayout(right, 2)
        lay.addLayout(top_row, 1)

        sel_lbl = QLabel("Selected cameras:")
        sel_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #333;")
        lay.addWidget(sel_lbl)

        self._sel_table = QTableWidget(0, 2)
        self._sel_table.setHorizontalHeaderLabels(["Camera", ""])
        self._sel_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._sel_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed)
        self._sel_table.setColumnWidth(1, 28)
        self._sel_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._sel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sel_table.verticalHeader().setVisible(False)
        self._sel_table.setMaximumHeight(180)
        lay.addWidget(self._sel_table)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._note = ""
        self._refresh_preset_list()
        self._fill_list()
        self._refresh_sel_table()

    # ── presets (shared file with the Image Slider) ────────────────────────────
    def _load_presets(self) -> dict:
        """{name: {"cameras": [...], …}}. An old preset is a bare list of names."""
        raw = {}
        try:
            if self._PRESETS_PATH.exists():
                raw = json.loads(self._PRESETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        out = {}
        for name, val in (raw or {}).items():
            if isinstance(val, list):
                out[name] = {"cameras": [str(c) for c in val], "auto": True}
            elif isinstance(val, dict):
                entry = dict(val)
                entry["cameras"] = [str(c) for c in entry.get("cameras", [])]
                out[name] = entry
        return out

    def _save_presets(self):
        try:
            self._PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PRESETS_PATH.write_text(
                json.dumps(self._presets, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Presets", f"Could not save the presets:\n{e}")

    def _refresh_preset_list(self):
        self._preset_list.blockSignals(True)
        self._preset_list.setRowCount(0)
        for name in sorted(self._presets.keys(), key=str.lower):
            r = self._preset_list.rowCount()
            self._preset_list.insertRow(r)
            self._preset_list.setItem(r, 0, QTableWidgetItem(name))
        self._preset_list.blockSignals(False)

    def _selected_preset_name(self) -> "str | None":
        rows = self._preset_list.selectedItems()
        return rows[0].text() if rows else None

    def _on_preset_load(self, *_):
        name = self._selected_preset_name()
        if not name or name not in self._presets:
            return
        wanted = [str(c) for c in (self._presets[name].get("cameras") or [])]
        have   = {n for n, _num, _lbl in self._cams}
        self._selected = [c for c in wanted if c in have]
        missing = len(wanted) - len(self._selected)
        self._note = (f"Preset “{name}”: {len(self._selected)} picked"
                      + (f", {missing} not recorded in the selected day(s)" if missing else ""))
        self._highlight()
        self._refresh_sel_table()
        self._fill_list()

    def _on_preset_save(self):
        from PySide6.QtWidgets import QInputDialog
        current = self._selected_preset_name() or ""
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        old = self._presets.get(name) or {}
        entry = {"cameras": list(self._selected)}
        # Overwriting a preset the Image Slider saved with an arrangement: the
        # arrangement is kept only while the camera set is the same one it was made
        # for, otherwise those tiles belong to nothing and the preset goes back to
        # arranging itself.
        if sorted(old.get("cameras", [])) == sorted(self._selected) and not old.get("auto", True):
            entry.update({k: old[k] for k in ("auto", "cam_order", "tiles") if k in old})
        else:
            entry["auto"] = True
        self._presets[name] = entry
        self._save_presets()
        self._refresh_preset_list()
        for r in range(self._preset_list.rowCount()):
            if self._preset_list.item(r, 0).text() == name:
                self._preset_list.blockSignals(True)
                self._preset_list.selectRow(r)
                self._preset_list.blockSignals(False)
                break

    def _on_preset_rename(self):
        from PySide6.QtWidgets import QInputDialog
        name = self._selected_preset_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name:
            return
        self._presets[new_name.strip()] = self._presets.pop(name)
        self._save_presets()
        self._refresh_preset_list()

    def _on_preset_delete(self):
        name = self._selected_preset_name()
        if not name:
            return
        if QMessageBox.question(self, "Delete preset", f"Delete preset '{name}'?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) \
                != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._save_presets()
        self._refresh_preset_list()

    # ── list ──────────────────────────────────────────────────────────────────
    def _label_for(self, name: str) -> str:
        for n, _num, lbl in self._cams:
            if n == name:
                return lbl or n
        return name

    def _filter(self, text: str):
        toks = [t for t in text.strip().lower().split() if t]
        if not toks:
            self._shown = list(self._cams)
        else:
            self._shown = [c for c in self._cams
                           if all(t in f"{c[1]} {c[2]} {c[0]}".lower() for t in toks)]
        self._fill_list()

    def _fill_list(self):
        self._list.setRowCount(0)
        for name, num, label in self._shown:
            r = self._list.rowCount()
            self._list.insertRow(r)
            self._list.setItem(r, 0, QTableWidgetItem(num))
            it = QTableWidgetItem(label or name)
            it.setData(Qt.ItemDataRole.UserRole, name)
            self._list.setItem(r, 1, it)
        self._highlight()
        txt = f"{len(self._shown)} of {len(self._cams)} cameras"
        if getattr(self, "_note", ""):
            txt += "  ·  " + self._note
        self._status.setText(txt)

    def _highlight(self):
        for r in range(self._list.rowCount()):
            it = self._list.item(r, 1)
            if it is None:
                continue
            on = it.data(Qt.ItemDataRole.UserRole) in self._selected
            bg = QColor("#d0e8ff") if on else QColor("#ffffff")
            for c in range(self._list.columnCount()):
                cell = self._list.item(r, c)
                if cell:
                    cell.setBackground(bg)
                    f = cell.font(); f.setBold(on); cell.setFont(f)

    def _on_row_clicked(self, row: int, _col: int):
        it = self._list.item(row, 1)
        if it is None:
            return
        name = it.data(Qt.ItemDataRole.UserRole)
        if name in self._selected:
            self._selected.remove(name)
        else:
            self._selected.append(name)
        self._highlight()
        self._refresh_sel_table()
        self._list.clearSelection()

    def _select_all_shown(self):
        for name, _num, _lbl in self._shown:
            if name not in self._selected:
                self._selected.append(name)
        self._highlight()
        self._refresh_sel_table()

    def _clear_all(self):
        self._selected.clear()
        self._highlight()
        self._refresh_sel_table()

    # ── selected list ─────────────────────────────────────────────────────────
    def _refresh_sel_table(self):
        self._sel_table.setRowCount(0)
        for name in self._selected:
            r = self._sel_table.rowCount()
            self._sel_table.insertRow(r)
            self._sel_table.setItem(r, 0, QTableWidgetItem(self._label_for(name)))
            btn = QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("font-size: 10px; padding: 0;")
            btn.clicked.connect(lambda _checked=False, n=name: self._remove(n))
            self._sel_table.setCellWidget(r, 1, btn)

    def _remove(self, name: str):
        if name in self._selected:
            self._selected.remove(name)
        self._highlight()
        self._refresh_sel_table()

    def selected_names(self) -> list:
        return list(self._selected)


# ── MAIN WIDGET ───────────────────────────────────────────────────────────────
class ImageFinderWidget(QWidget):
    """
    PySide6 port of the original tkinter FolderPickerApp.
    All original logic preserved. Only UI layer changed.

    Embed via main.py (set ._slider_ref and ._tab_widget after construction),
    or run standalone via main() at the bottom of this file.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Integration — set by main.py after construction
        self._slider_ref = None   # Viewer instance (is.py)
        self._tab_widget = None   # QTabWidget for tab switching

        # ── same state as original FolderPickerApp ────────────────────────────
        # The cameras the day scan found, and which of them are picked. This list is
        # the single source of truth — it used to be the rows of a big on-screen
        # table, which is now a picker dialog plus the short "selected" list.
        # Each entry: {path, name, num, label, hz33, qty, checked}.
        #   hz33 — "YES" for the 3.3+ Hz cameras (CAM_33HZ). Not shown any more;
        #          kept because the scan knows it and a future read may want it.
        #   qty  — how many frames to take from the camera. Always 1 today; the
        #          per-camera count column is gone, the plumbing that honours it
        #          is not.
        self._cams: list[dict] = []
        self._load_gen = 0
        self.primary_files: list[Path] = []
        self._namecache: dict = {}
        self._collect_busy = False

        self._mem_a: Path | None = None
        self._mem_b: Path | None = None

        self._view_temp_dir: str | None = None
        self._view_temp_paths: list[Path] = []
        atexit.register(self._cleanup_view_temp)

        self._user_has_selected_day = False
        self._autoload_timer: QTimer | None = None

        # ── energy CSV state ─────────────────────────────────────────────────
        # Selected columns — loaded from ENERGY_COLUMNS_DEFAULT, user can change
        # Registry NAMES (see _pv_channel_for), not CSV column names. Restored from
        # this tab's own state at the end of the build, once the widgets it paints into
        # exist.
        self._energy_selected_cols: list[str] = list(ENERGY_COLUMNS_DEFAULT)
        self._energy_hidden_pvs: set = set()
        # Cached CSV rows for the last loaded day: {date_str: list[_EnergyRow]}
        self._energy_cache: dict[str, list[_EnergyRow]] = {}
        # Per-column cache: {date_str: dict[str, list[_EnergyRow]]}
        self._energy_per_col_cache: dict[str, dict] = {}
        # Last energy lookup results: list of (Path, match|None, before|None, after|None)
        self._energy_results: list[tuple] = []
        self._energy_csv_offset: int = 0   # offset from matched row when navigating outside image set¨
        self._energy_csv_anchor_idx: int | None = None  # csv_rows index anchor for CSV navigation mode
        self._energy_csv_anchor_rows: list = []          # csv_rows for current anchor image
        # Background thread pool for CSV loading
        self._energy_pool = QThreadPool()
        self._energy_pool.setMaxThreadCount(1)

        self._log_sig = _LogSignals()
        self._log_sig.msg.connect(self._log)

        self._preview_sig = _PreviewSignals()
        self._preview_sig.ready.connect(self._on_preview_ready)

        self._preview_gen: int = 0
        self._preview_paths: list = []
        self._preview_cam_names: list[str] = []  # per-file cam name (parallel to _preview_paths)
        self._preview_idx: int = 0
        self._preview_cam: str = ""
        self._preview_from_view: bool = False  # True when preview was loaded by View button
        self._tp_dead_channels: set[str] = set()  # channels that timed out → skip next time
        self._last_save_dir: "Path | None" = None

        # ── one moment, every camera ─────────────────────────────────────────
        # The shared folder-listing cache. ONE reading of an hour folder answers
        # every camera asked for, and every later moment inside that hour costs
        # nothing at all — see sf_t.DayScanCache. It is what makes picking a
        # moment out of the PV graph as quick as it is.
        self._scan_cache = None            # built on first use (loads sf_t)
        # The moments the wall is showing, in the order they were picked.
        self._moments_ns: "list[int]" = []
        self._moment_ns: "int | None" = None
        # Every sample of the primary PV from the last PV Search — what the
        # prev/next shot arrows step through, kept so walking the day never
        # reopens the window.
        self._shot_stamps: "list[int]" = []
        # The moments Save was pressed on, newest first. SESSION-ONLY: never
        # written to the settings file, so closing the program empties it.
        self._saved_moments: "list[int]" = []
        self._prefetch_sig = _MomentSignals()
        self._prefetch_sig.item.connect(self._on_prefetch_item)
        # A PV Search that was finished before any camera was picked. Held here
        # and started by the camera picker, so the two halves of the question may
        # be answered in either order.
        self._pending_pv_cfg: "dict | None" = None
        self._moment_gen = 0
        self._moment_stop = threading.Event()
        self._moment_items: list = []
        self._moment_sig = _MomentSignals()
        self._moment_sig.item.connect(self._on_moment_item)
        self._moment_sig.done.connect(self._on_moment_done)
        # Its own pool, so a frame somebody is waiting for never queues behind
        # the CSV loader.
        self._moment_pool = QThreadPool(self)
        self._moment_pool.setMaxThreadCount(2)
        # (cam, asked_ns) → the resolved item. A moment looked at twice is free
        # the second time: no share walk and no worker thread.
        self._res_cache: dict = {}

        self._build_ui()
        # Trigger today's load after the event loop starts
        QTimer.singleShot(0, self._auto_select_today)

    # ── LOGGING ───────────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        for btn in [self._btn_view, self._btn_save,
                    self._btn_open_folder, self._btn_cameras,
                    self._btn_time_window,
                    self._btn_compare, self._btn_energy_cols,
                    self._gradient_cb]:
            btn.setEnabled(not busy)
    
    def _log(self, msg: str):
        """Main-thread log. Safe to call from any thread via _log_safe."""
        print(msg)
        if hasattr(self, "_log_box"):
            self._log_box.appendPlainText(str(msg))
            sb = self._log_box.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _log_safe(self, msg: str):
        """Thread-safe: routes through Qt signal so Qt widget is only touched on main thread."""
        try:
            self._log_sig.msg.emit(str(msg))
        except Exception:
            print(msg)

    # ── DEBOUNCED AUTOLOAD ────────────────────────────────────────────────────
    def _schedule_autoload(self, delay_ms: int = 150):
        if self._autoload_timer is not None:
            self._autoload_timer.stop()
        t = QTimer(self); t.setSingleShot(True)
        t.timeout.connect(self.load_folders)
        t.start(delay_ms)
        self._autoload_timer = t

    # ── UI BUILD ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Main layout: one row + the log under the whole width
        #   ROW:  left panel (fixed) | view (stretch, full height)
        #   LOG:  fixed height, spans the tab — it used to sit under the panel only,
        #         where with the camera table gone it would be 268 px of wrapped text
        page = QVBoxLayout(self)
        page.setContentsMargins(6, 6, 6, 6); page.setSpacing(4)
        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(6)
        page.addLayout(outer, 1)

        # Left side container
        left_side = QWidget()
        left_side.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root = QVBoxLayout(left_side)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(4)

        # Inner horizontal row: left_scroll + table
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0); top_row.setSpacing(6)

        # ════ LEFT PANEL ═════════════════════════════════════════════════════
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(268)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet(
            "QScrollArea{background:transparent;}QScrollBar:vertical{width:10px;}")

        lw = QWidget(); lw.setMinimumWidth(240)
        ll = QVBoxLayout(lw); ll.setContentsMargins(0, 0, 4, 0); ll.setSpacing(4)
        # The panel layout the groups themselves sit in. `ll` below is re-pointed at
        # each group's body in turn, so everything after a group banner lands inside
        # that group.
        panel_lay = ll

        # ── Collapsible groups ───────────────────────────────────────────────
        # Same groups, same colours and the same remembered open/closed state as the
        # Image Slider and Workshop panels. Which groups are open is kept in this
        # tab's own state file next to the PV selection.
        self._ui_state = self._load_ui_state()
        self._sections: dict = {}

        def _add_section(key, title, default_expanded=True):
            cls = _section_cls()
            sec = cls(title, key,
                      bool(self._ui_state.get(f"sec_{key}", default_expanded)),
                      accent=_SECTION_ACCENTS.get(key, "#4a78c0"))
            sec.toggled.connect(self._on_section_toggled)
            self._sections[key] = sec
            panel_lay.addWidget(sec)
            return sec

        _exp_row = QHBoxLayout(); _exp_row.setSpacing(4)
        _btn_exp = QPushButton("Expand all")
        _btn_exp.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_exp.clicked.connect(lambda: self._set_all_sections(True))
        _btn_col = QPushButton("Collapse all")
        _btn_col.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_col.clicked.connect(lambda: self._set_all_sections(False))
        _exp_row.addWidget(_btn_exp); _exp_row.addWidget(_btn_col)
        panel_lay.addLayout(_exp_row)

        # No group of its own for the cameras: the button sits in Source (the day and
        # the cameras are the one "what to search" question, the way the Slider's
        # Source group asks it) and the list of picked cameras sits in Actions.
        # Source first, then Actions right under it: the panel is read top-down as
        # "what to search" → "Load data" → "what to do with what came back". PV Values
        # is a reading of the result, so it sits below both.
        s_time = _add_section("time",     "Source",           True)
        s_act  = _add_section("actions",  "Actions",          True)
        s_pv   = _add_section("pv",       "PV Values",        True)
        # Image / Display used to be folded away — it was three sliders and a palette.
        # It now also holds the marks, the rotation and the reference day that used to
        # sit in a strip above the pictures, so it opens by default.
        s_disp = _add_section("display",  "Image / Display",  True)
        s_cmp  = _add_section("compare",  "Comparison",       False)

        # ══════════════════ Group: SOURCE ════════════════════════════════════
        # The Image Slider's Source pattern: one button opens the calendar, the
        # button next to it picks the cameras, PV Search sits underneath. The panel
        # itself stays short — the calendar is only interesting while choosing.
        ll = s_time.body_layout

        src_row = QHBoxLayout(); src_row.setSpacing(4)
        self._btn_time_window = QPushButton("Time window")
        self._btn_time_window.setToolTip(
            "Pick the day (or days) and the hour to search.")
        self._btn_time_window.clicked.connect(self._open_time_window)
        self._btn_cameras = QPushButton("📷  Cameras")
        self._btn_cameras.setToolTip(
            "Choose which cameras to search. The list is the cameras found in the "
            "selected day(s); presets are shared with the Image Slider.")
        self._btn_cameras.clicked.connect(self._open_camera_picker)
        src_row.addWidget(self._btn_time_window)
        src_row.addWidget(self._btn_cameras)
        ll.addLayout(src_row)

        # PV Search — under the two pickers it works with.
        self._btn_pv_search = QPushButton("🎯 PV Search")
        self._btn_pv_search.setToolTip(
            "Plot a PV for a day, then click it to pick moments (every click adds "
            "one, on any marked day) or drag to mark time regions.\n\n"
            "The cameras do not have to be chosen first — whichever of the two is "
            "set second, the search starts when both are in.")
        self._btn_pv_search.clicked.connect(self._open_pv_region_search)
        ll.addWidget(self._btn_pv_search)

        # Walking through the shots around the moment on screen. THE ARROWS ARE
        # HERE, not in the PV window: following a stretch of the day must not mean
        # reopening it. The samples the arrows step through are the primary PV's own
        # samples, kept by the tab when the window closed.
        shot_row = QHBoxLayout(); shot_row.setSpacing(4)
        _shot_css = (
            "QPushButton { background:#e8e8e8; color:#111; border:1px solid #9a9a9a;"
            " border-radius:3px; font-size:12px; font-weight:700; padding:2px 6px; }"
            "QPushButton:hover:enabled { background:#ffffff; }"
            "QPushButton:disabled { background:#4a4a4a; color:#8a8a8a;"
            " border:1px solid #5f5f5f; }")
        self._btn_shot_prev = QPushButton("◀ shot")
        self._btn_shot_next = QPushButton("shot ▶")
        for b, d in ((self._btn_shot_prev, -1), (self._btn_shot_next, +1)):
            b.setStyleSheet(_shot_css)
            b.setEnabled(False)
            b.setToolTip(
                "The shot before the one on the wall." if d < 0 else
                "The shot after the one on the wall.")
            b.clicked.connect(lambda _=False, k=d: self._step_shot(k))
            shot_row.addWidget(b, 1)
        ll.addLayout(shot_row)
        self._lbl_shot = QLabel("")
        self._lbl_shot.setWordWrap(True)
        self._lbl_shot.setStyleSheet("font-size:10px;color:#333;padding:1px 0;")
        ll.addWidget(self._lbl_shot)

        # ── The moments kept for this session ────────────────────────────────
        # Stepping through shots records NOTHING. Save is an explicit press, and
        # the list dies with the program — it is never written to the settings
        # file, so closing Image Tools empties it.
        self._moment_list = QListWidget()
        self._moment_list.setMaximumHeight(96)
        self._moment_list.setToolTip(
            "Moments you pressed Save on. Click one to go back to it — the frames "
            "are already found, so it comes back at once.\n\n"
            "This list is not saved: closing the program empties it.")
        self._moment_list.setStyleSheet(
            "QListWidget { background:#ffffff; color:#111111;"
            " border:1px solid #b0b0b0; font-size:11px; }"
            "QListWidget::item { padding:1px 3px; }"
            "QListWidget::item:selected { background:#1565C0; color:#ffffff; }"
            "QScrollBar:vertical { background:#e8e8e8; width:12px; }"
            "QScrollBar::handle:vertical { background:#8a8a8a; min-height:20px;"
            " border-radius:3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0px; }")
        self._moment_list.itemClicked.connect(self._on_saved_moment_clicked)
        ll.addWidget(self._moment_list)
        mm_row = QHBoxLayout(); mm_row.setSpacing(4)
        self._btn_moment_save = QPushButton("Save")
        self._btn_moment_save.setToolTip(
            "Keep the moment on the wall on the list above.")
        self._btn_moment_save.clicked.connect(self._save_current_moment)
        self._btn_moment_forget = QPushButton("Forget")
        self._btn_moment_forget.setToolTip("Take the selected moment off the list.")
        self._btn_moment_forget.clicked.connect(self._forget_saved_moment)
        self._btn_moment_clear = QPushButton("Clear")
        self._btn_moment_clear.setToolTip("Empty the list.")
        self._btn_moment_clear.clicked.connect(self._clear_saved_moments)
        for b in (self._btn_moment_save, self._btn_moment_forget,
                  self._btn_moment_clear):
            b.setEnabled(False)
            mm_row.addWidget(b, 1)
        ll.addLayout(mm_row)

        # The moment, over in the Image Slider — where the shots either side of it
        # can be slid through, which is the one thing this tab cannot do.
        send_row = QHBoxLayout(); send_row.setSpacing(4)
        self._btn_send_moment = QPushButton("Send moment")
        self._btn_send_moment.clicked.connect(
            lambda: self._send_moment_to_slider(False))
        self._btn_send_moment_cams = QPushButton("Send + cameras")
        self._btn_send_moment_cams.clicked.connect(
            lambda: self._send_moment_to_slider(True))
        for b in (self._btn_send_moment, self._btn_send_moment_cams):
            b.setEnabled(False)
            send_row.addWidget(b, 1)
        ll.addLayout(send_row)

        # What the Time window button is currently set to — the calendar is behind
        # the button now, so the panel has to say what was picked.
        self._time_summary = QLabel("")
        self._time_summary.setWordWrap(True)
        self._time_summary.setStyleSheet("font-size: 10px; color: #333; padding: 1px 0;")
        # Scan state stays in the PANEL, not in the dialog: it says whether the
        # camera list for the picked days is ready, and that has to be readable
        # with the calendar closed.
        self._status_dot = QLabel("⬤")
        self._status_dot.setToolTip("Green = the camera list for the picked day(s) is ready.")
        self._status_dot.setStyleSheet("color: green; font-size: 12px;")
        sum_row = QHBoxLayout(); sum_row.setSpacing(4)
        sum_row.addWidget(self._time_summary, 1)
        sum_row.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignTop)
        ll.addLayout(sum_row)

        # Load data — the button that actually goes and reads the frames for the
        # day, hour and cameras picked above. It belongs to those pickers, not to
        # the things you do afterwards, so it closes this group instead of opening
        # Actions.
        self._btn_view = QPushButton("Load data")
        self._btn_view.setToolTip(
            "Read the frames for the picked day(s), hour and cameras, and show "
            "them in the view on the right.")
        self._btn_view.clicked.connect(self.view_primary_files)
        ll.addWidget(self._btn_view)

        # ── The picked days and their time windows ───────────────────────────
        # There is no calendar in this panel any more. The Time window button
        # opens daypicker.DayTimePicker — the SAME dialog the Slider, Shot Finder
        # and One Moment open — and nothing takes effect until its OK is pressed.
        # It used to be an embedded pane in a Close-only window where every click
        # applied at once, which is why this tab felt unlike every other one.
        self._selected_days: "list[QDate]" = [QDate.currentDate()]
        # Per-day windows, exactly what the picker hands back. One entry per day,
        # in Prague (real) time — this tab has no lab-time mode any more.
        self._segments: "list" = [
            daypicker.PickSeg(daypicker.qdate_to_date(self._selected_days[0]),
                              *daypicker.default_window_for(
                                  daypicker.qdate_to_date(self._selected_days[0])))]
        # The Mon–Sun gate for a Ctrl+Shift stretch lives in the dialog now; the
        # last state is carried across openings so it does not reset every time.
        self._wd_gate: "set[int]" = {0, 1, 2, 3, 4}

        # ══════════════════ Group: PV VALUES ═════════════════════════════════
        ll = s_pv.body_layout

        self._energy_info = QPlainTextEdit()
        self._energy_info.setReadOnly(True)
        # The PV list. Literally the Image Slider's widget (PvValueTable) over the
        # Slider's registry, so one PV cannot read one way in this tab and another way
        # there. The eye takes a PV off the picture without stopping it being read.
        self._pv_table = _get_slider_module().PvValueTable()
        self._pv_table.setVisible(False)
        self._pv_table.eye_clicked.connect(self._pv_toggle_eye)
        ll.addWidget(self._pv_table)
        self._pv_no_pv_lbl = QLabel("No PVs selected. Click PVs to choose.")
        self._pv_no_pv_lbl.setStyleSheet("font-size: 10px; color: #888; padding: 2px 0;")
        self._pv_no_pv_lbl.setWordWrap(True)
        ll.addWidget(self._pv_no_pv_lbl)

        self._energy_info.setMaximumHeight(120)
        self._energy_info.setPlaceholderText(
            "Energy values appear here after Load data.")
        self._energy_info.setStyleSheet(
            "font-family:Consolas,monospace;font-size:11px;"
            "background:#f9f9f9;border:1px solid #ddd;")
        ll.addWidget(self._energy_info)
        # The picked PVs come back now that the table they paint into exists. Before
        # this the tab started every session reading NOTHING until the picker was
        # opened by hand, which reads exactly like "the PVs are there but it ignores
        # them".
        self._load_pv_state()
        self._pv_refresh_table()
        # Navigation row for energy results
        # Row 1: ◀ label ▶  |  PVs
        nav_row = QHBoxLayout()
        self._btn_energy_prev = QPushButton("◀")
        self._btn_energy_prev.setFixedWidth(28)
        self._btn_energy_prev.setToolTip("Previous shot (energy data)")
        self._btn_energy_prev.clicked.connect(self._energy_nav_prev)
        self._btn_energy_next = QPushButton("▶")
        self._btn_energy_next.setFixedWidth(28)
        self._btn_energy_next.setToolTip("Next shot (energy data)")
        self._btn_energy_next.clicked.connect(self._energy_nav_next)
        self._energy_nav_lbl = QLabel("0 / 0")
        self._energy_nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._energy_nav_lbl.setStyleSheet("font-size: 10px; color: #555;")
        nav_row.addWidget(self._btn_energy_prev)
        nav_row.addWidget(self._energy_nav_lbl, 1)
        nav_row.addWidget(self._btn_energy_next)
        # separator
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setStyleSheet("color: #ccc;")
        nav_row.addWidget(sep)
        self._btn_energy_cols = QPushButton("PVs")
        self._btn_energy_cols.setFixedWidth(36)
        self._btn_energy_cols.setToolTip(
            "Pick the PVs to read: the presets, any archiver channel (search it or "
            "type its name in full), and formulas.\n"
            "The same picker the Image Slider has, over the same list.")
        self._btn_energy_cols.clicked.connect(self._pick_energy_columns)
        nav_row.addWidget(self._btn_energy_cols)
        ll.addLayout(nav_row)

        # Row 2: Navigate images  |  Annotate energies
        cb_row = QHBoxLayout()
        self._cb_pv_preview = QCheckBox("PVs in preview")
        self._cb_pv_preview.setChecked(True)
        self._cb_pv_preview.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_pv_preview.setToolTip(
            "Show a white bar with the selected PV values below the image\n"
            "in the preview above. Does not change the saved files.")
        self._cb_pv_preview.stateChanged.connect(self._on_pv_preview_toggle)
        self._cb_annotate = QCheckBox("attach PVs")
        self._cb_annotate.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_annotate.setToolTip(
            "Bake a white bar with the selected PV values below each\n"
            "image when saving (Save As…).")
        cb_row.addWidget(self._cb_pv_preview, 1)
        cb_row.addWidget(self._cb_annotate, 1)
        ll.addLayout(cb_row)

        # Energy navigation mode toggle
        cb_row2 = QHBoxLayout()
        self._cb_nav_images = QCheckBox("Browse energy rows")
        self._cb_nav_images.setChecked(False)
        self._cb_nav_images.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_nav_images.setToolTip(
            "Unchecked (default): the ◀ ▶ arrows step through the loaded "
            "images, showing each image's PV values.\n"
            "Checked: the arrows scroll through the CSV energy rows around "
            "the current image, without changing the displayed image.")
        self._cb_nav_images.stateChanged.connect(self._on_nav_mode_changed)
        cb_row2.addWidget(self._cb_nav_images, 1)
        ll.addLayout(cb_row2)

        # ══════════════════ Group: ACTIONS ═══════════════════════════════════
        ll = s_act.body_layout

        # action buttons  row0=[Save As|Folder]  row1=[Workshop]
        # Load data moved up into Source — this group is only what you do with the
        # frames once they are loaded.
        btn_grid = QGridLayout(); btn_grid.setSpacing(4)
        self._btn_save = QPushButton("Save As")
        self._btn_save.clicked.connect(self.save_primary_files_as)
        self._btn_open_folder = QPushButton("📁 Folder")
        self._btn_open_folder.clicked.connect(self.open_folder_in_explorer)
        btn_grid.addWidget(self._btn_save, 0, 0)
        btn_grid.addWidget(self._btn_open_folder, 0, 1)

        self._btn_send_workshop = QPushButton("➤ Workshop")
        self._btn_send_workshop.setToolTip("Send currently selected images to Workshop tab for editing")
        self._btn_send_workshop.clicked.connect(self._send_to_workshop)
        btn_grid.addWidget(self._btn_send_workshop, 1, 0, 1, 2)
        ll.addLayout(btn_grid)

        # The cameras that will be searched — just the list, no banner and no count
        # line: the count is on the Cameras button and the rest is in the tooltip.
        self._sel_table = QTableWidget(0, 2)
        self._sel_table.setHorizontalHeaderLabels(["Cam #", "Camera"])
        self._sel_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._sel_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._sel_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sel_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sel_table.setMaximumHeight(180)
        self._sel_table.setToolTip(
            "The cameras that will be searched.\n"
            "Click a camera to preview its first frame, double-click to unpick it.")
        self._sel_table.clicked.connect(self._on_sel_table_clicked)
        self._sel_table.doubleClicked.connect(self._on_sel_table_double_clicked)
        ll.addWidget(self._sel_table)

        # The frame arrows are NOT here any more — they are under the picture on the
        # One frame page, where the frame they step through is.

        # ══════════════════ Group: IMAGE / DISPLAY ═══════════════════════════
        ll = s_disp.body_layout

        # Gradient — the palette the preview image is drawn with
        grad_row = QHBoxLayout(); grad_row.addWidget(QLabel("Gradient:"))
        self._gradient_cb = _NoScrollComboBox()
        for name in GRADIENT_NAMES: self._gradient_cb.addItem(name)
        self._gradient_cb.setCurrentText("Gradient")
        self._gradient_cb.currentTextChanged.connect(self._on_gradient_changed)
        grad_row.addWidget(self._gradient_cb, 1)
        ll.addLayout(grad_row)

        # Contrast / Brightness / Gamma — the Image Slider's three-row block, control
        # for control. Moving a slider re-renders the previewed frame, which is a read
        # off the share, so the three of them share one debounce instead of firing on
        # every pixel of a drag.
        self._bc_debounce = QTimer(self)
        self._bc_debounce.setSingleShot(True)
        self._bc_debounce.setInterval(120)
        self._bc_debounce.timeout.connect(self._bc_reshow_preview)

        # CONTRAST. Its Auto box is the percentile auto-stretch this tab used to show as
        # a separate "Auto stretch" checkbox — the same operation, now sitting on the row
        # it overrides, the way the Slider has always had it. Absolute scale stays the
        # default (see img_scale): one palette colour = one intensity, so frames and
        # cameras are comparable. Auto trades that away for legibility on the dim
        # cameras, which is why it is visible and off by default rather than something
        # the viewer does behind the operator's back.
        row_con = QHBoxLayout()
        self._lbl_contrast_name = QLabel("Con:")
        self._lbl_contrast_name.setMinimumWidth(_BC_NAME_W)
        self._lbl_contrast_name.setToolTip(_TT_CONTRAST)
        row_con.addWidget(self._lbl_contrast_name)
        self._contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self._contrast_slider.setRange(img_scale.CONTRAST_MIN, img_scale.CONTRAST_MAX)
        self._contrast_slider.setValue(0)
        self._contrast_slider.setToolTip(_TT_CONTRAST)
        self._contrast_slider.valueChanged.connect(self._on_contrast_slider_changed)
        # The manual value to come back to when Auto is switched off — the greyed-out
        # slider is overwritten while Auto is on (see _park_auto_bc).
        self._contrast_manual = 0
        row_con.addWidget(self._contrast_slider, 1)
        self._lbl_contrast_val = _bc_value_label("0", _TT_CONTRAST)
        row_con.addWidget(self._lbl_contrast_val)
        self._btn_contrast_reset = QPushButton("↺")
        self._btn_contrast_reset.setFixedWidth(26)
        self._btn_contrast_reset.setToolTip("Reset contrast")
        self._btn_contrast_reset.clicked.connect(self._reset_contrast_slider)
        row_con.addWidget(self._btn_contrast_reset)
        self._cb_auto_stretch = QCheckBox("Auto")
        self._cb_auto_stretch.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_auto_stretch.setToolTip(
            "Auto contrast — stretch each frame over its own p0.5–p99.5 window. "
            "Overrides the Contrast slider.\n"
            "OFF: absolute scale — pixel value / camera full scale. Brightness is "
            "comparable between frames and between cameras.\n"
            "ON: a dim frame becomes readable, but colours no longer mean the same "
            "intensity from frame to frame.\n"
            "The Binary and False Colors palettes always map per frame, by design.")
        self._cb_auto_stretch.toggled.connect(self._on_auto_stretch_toggled)
        row_con.addWidget(self._cb_auto_stretch)
        ll.addLayout(row_con)

        # BRIGHTNESS — an additive offset, never a gain (that is what Contrast is).
        # Its Auto box is the auto LEVEL: the same p0.5–p99.5 window as Auto contrast,
        # placed on the data. No additive rule can do that job — a shift cannot spread a
        # narrow range — which is why Auto brightness and Auto contrast are one pass and
        # ticking both does not level the frame twice.
        row_bri = QHBoxLayout()
        self._lbl_bright_name = QLabel("Bri:")
        self._lbl_bright_name.setMinimumWidth(_BC_NAME_W)
        self._lbl_bright_name.setToolTip(_TT_BRIGHTNESS)
        row_bri.addWidget(self._lbl_bright_name)
        self._bright_slider = QSlider(Qt.Orientation.Horizontal)
        self._bright_slider.setRange(img_scale.BRIGHTNESS_MIN, img_scale.BRIGHTNESS_MAX)
        self._bright_slider.setValue(0)
        self._bright_slider.setToolTip(_TT_BRIGHTNESS)
        self._bright_slider.valueChanged.connect(self._on_bright_slider_changed)
        self._bright_manual = 0
        row_bri.addWidget(self._bright_slider, 1)
        self._lbl_bright_val = _bc_value_label("0", _TT_BRIGHTNESS)
        row_bri.addWidget(self._lbl_bright_val)
        self._btn_bright_reset = QPushButton("↺")
        self._btn_bright_reset.setFixedWidth(26)
        self._btn_bright_reset.setToolTip("Reset brightness")
        self._btn_bright_reset.clicked.connect(self._reset_bright_slider)
        row_bri.addWidget(self._btn_bright_reset)
        self._cb_bright_auto = QCheckBox("Auto")
        self._cb_bright_auto.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_bright_auto.setToolTip(
            "Auto brightness — auto level: the frame's p0.5–p99.5 window mapped onto "
            "the full range. Overrides the Brightness slider.\n"
            "Per-frame, so it gives up comparability the same way Auto contrast does.")
        self._cb_bright_auto.toggled.connect(self._on_bright_auto_toggled)
        row_bri.addWidget(self._cb_bright_auto)
        ll.addLayout(row_bri)

        # GAMMA — the third member of the pattern, and the only one that does NOT cost
        # comparability: the curve depends on the pixel value alone, so one colour still
        # means one intensity (see img_scale). It is the answer to "the absolute scale is
        # right but the frame is dark" that Auto contrast answers by giving that up.
        row_gam = QHBoxLayout()
        self._lbl_gamma = QLabel("Gam:")
        self._lbl_gamma.setMinimumWidth(_BC_NAME_W)
        self._lbl_gamma.setToolTip(_TT_GAMMA)
        row_gam.addWidget(self._lbl_gamma)
        self._gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self._gamma_slider.setRange(img_scale.GAMMA_SLIDER_MIN, img_scale.GAMMA_SLIDER_MAX)
        self._gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)
        self._gamma_slider.setToolTip(_TT_GAMMA)
        self._gamma_slider.valueChanged.connect(self._on_gamma_slider_changed)
        self._gamma_manual = img_scale.GAMMA_SLIDER_NEUTRAL
        row_gam.addWidget(self._gamma_slider, 1)
        self._lbl_gamma_val = _bc_value_label("1.00", _TT_GAMMA)
        row_gam.addWidget(self._lbl_gamma_val)
        self._btn_gamma_reset = QPushButton("↺")
        self._btn_gamma_reset.setFixedWidth(26)
        self._btn_gamma_reset.setToolTip("Reset gamma to 1.00 (linear)")
        self._btn_gamma_reset.clicked.connect(self._reset_gamma_slider)
        row_gam.addWidget(self._btn_gamma_reset)
        self._cb_gamma_auto = QCheckBox("Auto")
        self._cb_gamma_auto.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_gamma_auto.setToolTip(
            "Auto gamma — the curve that lands THIS frame's median at "
            f"{int(img_scale.AUTO_GAMMA_TARGET * 100)} % of the range.\n"
            "Per-frame, so it overrides the slider and gives up comparability, same as "
            "Auto contrast.")
        self._cb_gamma_auto.toggled.connect(self._on_gamma_auto_toggled)
        row_gam.addWidget(self._cb_gamma_auto)
        ll.addLayout(row_gam)
        # All three rows exist now, so put them in the state their checkboxes call for.
        self._sync_bc_enabled()

        # ── The wall's own controls ──────────────────────────────────────────
        # These used to be a horizontal strip above the pictures — the one part of
        # this tab that was not in the panel. They ask the same question as the
        # sliders above ("how does the wall look"), so they live in the same group,
        # under a divider.
        ll.addWidget(_hsep())

        ref_row = QHBoxLayout(); ref_row.setSpacing(4)
        ref_row.addWidget(QLabel("Reference day:"))
        self._baseline_cb = _NoScrollComboBox()
        # No minimum width: in a 268 px panel a minimum on a combo is what pushes a
        # horizontal scrollbar onto the whole column. It takes the space that is left.
        self._baseline_cb.currentIndexChanged.connect(self._on_baseline_changed)
        ref_row.addWidget(self._baseline_cb, 1)
        ll.addLayout(ref_row)

        size_row = QHBoxLayout(); size_row.setSpacing(4)
        self._btn_fit = QPushButton("Fit")
        self._btn_fit.setEnabled(False)
        self._btn_fit.setToolTip(
            "Back to the size that fills the pane exactly.\n"
            "Ctrl + mouse wheel over the frames makes them bigger or smaller; "
            "the pane then scrolls.")
        self._btn_fit.clicked.connect(self._fit_wall)
        size_row.addWidget(self._btn_fit, 1)
        self._btn_save_wall = QPushButton("Save view")
        self._btn_save_wall.setToolTip(
            "The whole view in one file — every row, including the ones below the "
            "fold. PNG or PDF, this tab or every tab.")
        self._btn_save_wall.clicked.connect(self._save_wall)
        size_row.addWidget(self._btn_save_wall, 1)
        ll.addLayout(size_row)

        self._build_overlay_controls(ll)

        # ══════════════════ Group: COMPARISON ════════════════════════════════
        ll = s_cmp.body_layout

        # memory A/B
        ab_row = QHBoxLayout()
        btn_add_a = QPushButton("Add to A"); btn_add_a.clicked.connect(lambda: self._save_to_memory("A"))
        btn_add_b = QPushButton("Add to B"); btn_add_b.clicked.connect(lambda: self._save_to_memory("B"))
        ab_row.addWidget(btn_add_a); ab_row.addWidget(btn_add_b); ll.addLayout(ab_row)

        self._lbl_mem_a = QLabel("A: —"); self._lbl_mem_b = QLabel("B: —")
        a_row = QHBoxLayout(); a_row.addWidget(self._lbl_mem_a, 1)
        btn_ca = QPushButton("Clear A"); btn_ca.setFixedWidth(65)
        btn_ca.clicked.connect(lambda: self._clear_slot("A")); a_row.addWidget(btn_ca); ll.addLayout(a_row)
        b_row = QHBoxLayout(); b_row.addWidget(self._lbl_mem_b, 1)
        btn_cb_w = QPushButton("Clear B"); btn_cb_w.setFixedWidth(65)
        btn_cb_w.clicked.connect(lambda: self._clear_slot("B")); b_row.addWidget(btn_cb_w); ll.addLayout(b_row)

        self._btn_compare = QPushButton("Compare A vs B")
        self._btn_compare.setEnabled(False)
        self._btn_compare.clicked.connect(self._compare_memory)
        ll.addWidget(self._btn_compare)

        panel_lay.addStretch(1)
        left_scroll.setWidget(lw)

        # The camera table used to sit here, to the right of the panel, taking a
        # column of the window for a list that only matters while choosing. It is
        # now the Cameras picker; the panel is the whole left side and every pixel
        # it freed goes to the pictures.
        top_row.addWidget(left_scroll)
        root.addLayout(top_row, 1)

        # ── Log box — full width of the tab, fixed height ─────────────────────
        self._log_box = QPlainTextEdit()
        self._log_box.setReadOnly(True)
        self._log_box.setFixedHeight(100)
        self._log_box.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        self._log_box.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._log_box.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        page.addWidget(self._log_box, 0)

        # left_side uses stretch=0 so it stays at the panel's own width;
        # preview_col gets all remaining space via stretch=1
        outer.addWidget(left_side, 0)

        # ── View — right column, full height ──────────────────────────────────
        # One result set, several ways to read it, as tabs: the close-up of a single
        # frame, one wall per camera (its days next to each other), and the day-by-day
        # wall where each row is a day and each column the same camera throughout.
        # These used to be split between this tab and a pop-up window that opened after
        # every search; there is now one place to look.
        preview_col = QWidget()
        preview_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pcl = QVBoxLayout(preview_col)
        pcl.setContentsMargins(0, 0, 0, 0); pcl.setSpacing(2)

        self._wall_shared = _WallShared()
        self._cam_walls: "dict[str, _DayWall]" = {}
        self._wall_pages: "dict[int, _DayWall]" = {}   # tab index → wall
        self._last_wall_tab = 0

        # Nothing sits above the pictures any more: the reference day, Save comparison
        # and the marks/rotation row were moved into the Image / Display group of the
        # left panel, where every other control in this tab already lives. The tabs
        # start at the top of this column.

        # The "current" wall — whichever tab is showing. It always exists, so every
        # caller (Stop All, the display sliders, the tests) has something to talk to
        # before the first search has run.
        self._wall = _DayWall(shared=self._wall_shared)
        self._wire_wall(self._wall)

        # The close-up is no longer a tab. It was a permanent half-size picture next to
        # the walls that nobody switched to; what the tile click is actually asking for
        # is "show me this one, big". So the page below is built exactly as before but
        # goes into a window of its own (_show_frame_window), opened by clicking a tile
        # and sized well above the stored frame.
        single_page = QWidget()
        spl = QVBoxLayout(single_page)
        spl.setContentsMargins(4, 4, 4, 4); spl.setSpacing(2)
        # Explicitly dark, not "whatever the theme gives": a frame viewer has to be a
        # dark surround or the picture's own black edges cannot be told from the page.
        single_page.setAutoFillBackground(True)
        _fp_pal = single_page.palette()
        _fp_pal.setColor(QPalette.ColorRole.Window, QColor("#202020"))
        _fp_pal.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
        single_page.setPalette(_fp_pal)
        self._frame_page = single_page
        self._frame_dlg: "QDialog | None" = None

        self._view_tabs = QTabWidget()
        self._view_tabs.setDocumentMode(True)
        self._view_tabs.addTab(self._wrap_scroll(self._wall), "Days side by side")
        self._wall_pages = {0: self._wall}
        self._view_tabs.currentChanged.connect(self._on_view_tab_changed)
        pcl.addWidget(self._view_tabs, 1)

        pcl = spl        # everything below builds the single-frame page

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_lbl.setStyleSheet("background:#1a1a1a; border-radius:3px;")
        self._preview_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._preview_lbl.setMinimumSize(100, 100)
        # Scale pixmap to fit the label automatically when label is resized
        self._preview_lbl.setScaledContents(False)
        pcl.addWidget(self._preview_lbl, 1)

        lbl_row = QHBoxLayout()
        lbl_row.setContentsMargins(0, 0, 0, 0)
        lbl_row.setSpacing(2)
        self._preview_cam_lbl = QLabel("")
        self._preview_cam_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._preview_cam_lbl.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #eee; background: #444; "
            "padding: 2px 6px; border-radius: 2px;")
        self._preview_cam_lbl.setFixedHeight(34)
        self._preview_ts_lbl = QLabel("")
        self._preview_ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._preview_ts_lbl.setStyleSheet(
            "font-size: 20px; color: #ffd54f; background: #333; "
            "padding: 2px 6px; border-radius: 2px;")
        self._preview_ts_lbl.setFixedHeight(34)
        lbl_row.addWidget(self._preview_cam_lbl, 1)
        lbl_row.addWidget(self._preview_ts_lbl, 2)
        pcl.addLayout(lbl_row, 0)

        # What the displayed intensities MEAN. Without it a frame at 3 % of full scale
        # is indistinguishable from a broken render, and a palette is decoration rather
        # than a reading.
        self._preview_scale_lbl = QLabel("")
        self._preview_scale_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
        self._preview_scale_lbl.setStyleSheet(
            "font-size: 11px; color: #bbb; background: transparent; padding: 0 6px;")
        pcl.addWidget(self._preview_scale_lbl, 0)

        # Step through the loaded frames. These arrows lived in the panel on the left,
        # far from the picture they move — nobody found them, and the page looked like
        # it could only ever show one frame. They belong under the frame.
        # Dark ink on a light button, and the counter light on the dark page — the row
        # sits under a black picture now, where the old #333 counter and the themed
        # buttons were all but invisible.
        _nav_btn_css = (
            "QPushButton { background:#e8e8e8; color:#111; border:1px solid #9a9a9a;"
            " border-radius:3px; font-size:16px; font-weight:700; padding:2px 0; }"
            "QPushButton:hover:enabled { background:#ffffff; }"
            "QPushButton:disabled { background:#4a4a4a; color:#8a8a8a;"
            " border:1px solid #5f5f5f; }")
        nav_prev_row = QHBoxLayout(); nav_prev_row.setSpacing(8)
        nav_prev_row.addStretch(1)
        self._prev_btn = QPushButton("◀"); self._prev_btn.setFixedSize(52, 30)
        self._prev_btn.setToolTip("Previous loaded frame")
        self._prev_btn.setStyleSheet(_nav_btn_css)
        self._prev_btn.clicked.connect(self._preview_prev)
        self._next_btn = QPushButton("▶"); self._next_btn.setFixedSize(52, 30)
        self._next_btn.setToolTip("Next loaded frame")
        self._next_btn.setStyleSheet(_nav_btn_css)
        self._next_btn.clicked.connect(self._preview_next)
        # "0 / 0" from the start, not an empty label: the row has to read as frame
        # navigation before anything is loaded, or it looks like decoration again.
        self._prev_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._preview_counter = QLabel("0 / 0")
        self._preview_counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_counter.setMinimumWidth(90)
        self._preview_counter.setStyleSheet(
            "font-size:14px; font-weight:700; color:#eeeeee;")
        nav_prev_row.addWidget(self._prev_btn)
        nav_prev_row.addWidget(self._preview_counter, 0)
        nav_prev_row.addWidget(self._next_btn)
        nav_prev_row.addStretch(1)
        pcl.addLayout(nav_prev_row, 0)

        outer.addWidget(preview_col, 1)

        # The day was applied before the hour combo existed, so the line under the
        # buttons is written once here, with every widget in place.
        self._sync_time_summary()
        self._log("READY. No network scan on startup.")
        self._log(f"IMAGES_ROOT_BASE = {IMAGES_ROOT_BASE}")
        self._log(f"Network source: {'Lab' if _IS_LAB else 'Office'} (hostname: {_socket.gethostname()})")
        self._log("Press Time window to pick the day(s) and hours, then Load data.")

    # ── CAMERA LIST HELPERS ───────────────────────────────────────────────────
    def _picked_cams(self) -> list:
        """The picked cameras, in the order the scan found them."""
        return [c for c in self._cams if c.get("checked")]

    def _cam_by_name(self, name: str) -> "dict | None":
        for c in self._cams:
            if c["name"] == name:
                return c
        return None

    def _open_camera_picker(self):
        """The Cameras button — pick which cameras the search runs on.

        A PV Search that was finished before the cameras were known is held; the
        moment cameras are picked here, it runs. That is the whole of the
        "it does not matter which half comes first" rule."""
        if not self._cams:
            QMessageBox.information(
                self, "No cameras yet",
                "No cameras have been found for the selected day(s) yet.\n\n"
                "Open Time window, pick a day, and wait for the scan to finish "
                "(the dot next to the day/hour line turns green).")
            return
        dlg = _CameraPickDialog(self._cams, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = set(dlg.selected_names())
        for c in self._cams:
            c["checked"] = c["name"] in picked
        self._refresh_selected_table()
        self._log(f"Cameras: {len(picked)} selected")
        self._run_pending_pv_search()

    # ── The moments kept for this session ─────────────────────────────────────
    def _fmt_moment(self, ts_ns: int) -> str:
        dt = datetime.fromtimestamp(int(ts_ns) / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            dt = dt.astimezone(PRAGUE)
        return dt.strftime("%d.%m.  %H:%M:%S")

    def _refresh_moment_list(self):
        """The saved list, newest first, with the moment ON SCREEN banded.

        The band is painted on the item, never left to Qt's selection colour: the
        selection is where the operator last clicked, which is not the same thing
        as what is on the wall."""
        if not hasattr(self, "_moment_list"):
            return
        cur = set(self._moments_ns or ([self._moment_ns]
                                       if self._moment_ns is not None else []))
        self._moment_list.blockSignals(True)
        self._moment_list.clear()
        if not self._saved_moments:
            # An empty white box with three buttons under it does not say what it
            # is for. One greyed line does, and it cannot be clicked.
            empty = QListWidgetItem("No moment kept yet — press Save.")
            empty.setForeground(QColor("#8a8a8a"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._moment_list.addItem(empty)
        for ts in self._saved_moments:
            it = QListWidgetItem(self._fmt_moment(ts))
            it.setData(Qt.ItemDataRole.UserRole, int(ts))
            if int(ts) in cur:
                it.setBackground(QColor("#BBDEFB"))
                it.setForeground(QColor("#0D47A1"))
                f = it.font(); f.setBold(True); it.setFont(f)
                it.setToolTip("This is the moment on the wall.")
            self._moment_list.addItem(it)
        self._moment_list.blockSignals(False)
        on = bool(self._saved_moments)
        self._btn_moment_forget.setEnabled(on)
        self._btn_moment_clear.setEnabled(on)
        # Save greys out once the moment on screen is already on the list — the
        # button then has nothing to do, and a press that changes nothing reads as
        # a broken button.
        one = self._moment_ns
        self._btn_moment_save.setEnabled(
            one is not None and int(one) not in set(self._saved_moments))

    def _save_current_moment(self):
        one = self._moment_ns
        if one is None:
            return
        one = int(one)
        if one in self._saved_moments:
            return
        self._saved_moments.insert(0, one)
        del self._saved_moments[_SAVED_MOMENTS_MAX:]
        self._refresh_moment_list()
        self._log(f"[moment] saved {self._fmt_moment(one)} "
                  f"({len(self._saved_moments)} on the list)")
        self._start_moment_prefetch()

    def _forget_saved_moment(self):
        it = self._moment_list.currentItem()
        if it is None:
            self._log("Nothing forgotten — click a moment on the list first.")
            return
        ts = int(it.data(Qt.ItemDataRole.UserRole))
        self._saved_moments = [t for t in self._saved_moments if t != ts]
        self._refresh_moment_list()

    def _clear_saved_moments(self):
        self._saved_moments = []
        self._refresh_moment_list()

    def _on_saved_moment_clicked(self, item):
        ts = item.data(Qt.ItemDataRole.UserRole)
        if ts is not None:
            self._load_moments([int(ts)])

    def _start_moment_prefetch(self):
        """Find the frames of the newest saved moments quietly, in the background.

        Only the RESOLUTION — which file holds each camera's frame — and only for
        the newest few, one moment at a time on a pool of its own. That is the half
        that costs share round trips; the pictures themselves are cached by the
        wall. It never runs before something the operator actually asked for, so a
        prefetch cannot take a folder listing away from a live click."""
        if not self._saved_moments:
            return
        cams = [c[0] for c in self._checked_cameras()]
        if not cams:
            return
        want: list = []
        for ts in self._saved_moments[:_MOMENT_PREFETCH_KEEP]:
            for cam in cams:
                if self._res_cache_get(cam, ts) is None:
                    want.append((int(ts), cam))
        if not want:
            return
        self._ensure_scan_cache()
        stop = self._moment_stop
        scan = self._scan_cache

        def _work():
            for ts, cam in want:
                if stop.is_set():
                    return
                try:
                    res = _resolve_moment_one(ts, cam, scan)
                except Exception:
                    continue
                try:
                    self._prefetch_sig.item.emit(res, 0)
                except RuntimeError:
                    return

        threading.Thread(target=_work, daemon=True).start()

    def _on_prefetch_item(self, res: dict, _gen: int):
        """A prefetched resolution goes into the same cache a click reads."""
        self._res_cache_put(res)

    # ── Walking the shots around the moment on screen ─────────────────────────
    def _sync_shot_steps(self):
        """The two arrows beside PV Search: on when there is a moment on the wall
        and a series of shots to step through."""
        if not hasattr(self, "_btn_shot_prev"):
            return
        stamps = self._shot_stamps
        cur = self._moment_ns
        can = bool(stamps) and cur is not None
        i = bisect.bisect_left(stamps, int(cur)) if can else 0
        self._btn_shot_prev.setEnabled(bool(can and i > 0))
        self._btn_shot_next.setEnabled(
            bool(can and i < len(stamps) - 1))
        if not can:
            self._lbl_shot.setText("")
            return
        when = datetime.fromtimestamp(int(cur) / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            when = when.astimezone(PRAGUE)
        self._lbl_shot.setText(
            f"shot {min(i + 1, len(stamps))} of {len(stamps)}   ·   "
            + when.strftime("%d.%m. %H:%M:%S"))

    def _step_shot(self, direction: int):
        """The shot before or after the moment on the wall.

        It steps through the PRIMARY PV's samples — the same ones a click in the
        graph snaps to — so every step lands on a moment a shot was really archived
        at. Nothing is saved: stepping is navigation (see the Save button on the
        saved-moments list)."""
        stamps = self._shot_stamps
        cur = self._moment_ns
        if not stamps or cur is None:
            return
        i = bisect.bisect_left(stamps, int(cur))
        if i >= len(stamps) or stamps[i] != int(cur):
            # The moment on the wall is not itself a sample (a region's peak, a
            # condition hit): step from the nearest one.
            i = max(0, min(i, len(stamps) - 1))
        j = i + direction
        if j < 0 or j >= len(stamps):
            return
        self._load_moments([stamps[j]])

    def _run_pending_pv_search(self):
        """Start a held PV Search now that there are cameras to run it on."""
        cfg = getattr(self, "_pending_pv_cfg", None)
        if not cfg or not self._checked_cameras():
            return
        self._pending_pv_cfg = None
        self._log("PV Search: cameras picked — starting the held search.")
        self._start_pv_search(cfg)

    # ── TIME WINDOW ───────────────────────────────────────────────────────────
    def _open_time_window(self):
        """The Time window button — the SAME picker the Slider, Shot Finder and One
        Moment open (daypicker.DayTimePicker). Modal, and nothing takes effect
        until OK; Cancel leaves the previous pick exactly as it was.

        No Live mode here: this tab does not follow new frames, and a tick that
        does nothing reads as a broken tick."""
        dlg = daypicker.DayTimePicker(
            parent=self,
            init_date=self._primary_day(),
            init_segments=list(self._segments),
            allow_live=False)
        # Carry the Mon–Sun stretch gate over from the last time it was opened.
        for i, cb in enumerate(dlg._wd_checks):
            cb.setChecked(i in self._wd_gate)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._wd_gate = {i for i, cb in enumerate(dlg._wd_checks) if cb.isChecked()}
        self._segments = dlg.all_segments()
        self._selected_days = [daypicker.date_to_qdate(s.date) for s in self._segments]
        self._user_has_selected_day = True
        self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
        self._log(f"Time window: {len(self._segments)} day(s), "
                  f"{self._window_summary()}")
        self._sync_time_summary()
        self._schedule_autoload(150)

    def _primary_day(self):
        """The leading day — the one a single-day action works on."""
        return self._segments[0].date if self._segments else date.today()

    def _window_summary(self) -> str:
        """The picked hours as one string; "per day" once the days disagree."""
        if not self._segments:
            return "no window"
        spans = {(s.h_from, s.m_from, s.h_to, s.m_to) for s in self._segments}
        if len(spans) == 1:
            hf, mf, ht, mt = spans.pop()
            return f"{hf:02d}:{mf:02d}–{ht:02d}:{mt:02d}"
        return "per day"

    def _sync_time_summary(self):
        """One line under the buttons saying what the Time window is set to."""
        if not hasattr(self, "_time_summary"):
            return
        days = list(self._selected_days)
        if not days:
            self._time_summary.setText("No day picked")
            return
        first = days[0].toString("dd.MM.yyyy")
        if len(days) == 1:
            head = first
        else:
            head = f"{len(days)} days ({first} … {days[-1].toString('dd.MM.yyyy')})"
        self._time_summary.setText(f"{head}  ·  {self._window_summary()}")

    def _sync_cameras_button(self):
        # No counts on the button. "0/92" said nothing useful — nobody knows which
        # 92 cameras a given day happens to hold, and the picked ones are listed
        # right below in Actions.
        self._btn_cameras.setText("📷  Cameras")

    # ── Day wall ──────────────────────────────────────────────────────────────
    def _wrap_scroll(self, wall: "_DayWall") -> QWidget:
        """A wall inside a scroll area. At the fitted size it never scrolls, but
        day-by-day is as tall as there are days and Ctrl+wheel can make any wall
        taller than the pane, so it has to be able to."""
        sc = _WallScroll()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(wall)
        wall._scroll_host = sc
        sc.zoomed.connect(lambda notches, w=wall: self._zoom_wall(w, notches))
        return sc

    def _zoom_wall(self, wall: "_DayWall", notches: int):
        if wall.zoom_by_notches(notches):
            self._sync_zoom_label()

    def _fit_wall(self):
        """Back to the size that fills the pane exactly."""
        w = getattr(self, "_wall", None)
        if w is not None:
            w.reset_zoom()
        self._sync_zoom_label()

    def _sync_zoom_label(self):
        w = getattr(self, "_wall", None)
        if not hasattr(self, "_btn_fit") or w is None:
            return
        z = w.zoom()
        self._btn_fit.setEnabled(abs(z - 1.0) > 1e-6)
        self._btn_fit.setText("Fit" if abs(z - 1.0) <= 1e-6
                              else f"Fit  ({z * 100:.0f} %)")

    def _wire_wall(self, wall: "_DayWall"):
        wall.tile_clicked.connect(self._on_wall_tile_clicked)
        wall.tile_context.connect(self._on_wall_context)
        wall.selection_changed.connect(self._on_wall_selection_changed)

    def _all_walls(self) -> list:
        return list(self._wall_pages.values())

    # ── The close-up window ───────────────────────────────────────────────────
    def _frame_window_is_open(self) -> bool:
        dlg = getattr(self, "_frame_dlg", None)
        try:
            return dlg is not None and dlg.isVisible()
        except RuntimeError:
            return False

    def _show_frame_window(self):
        """Open (or raise) the close-up window on whatever the preview is set to.

        Deliberately big: the point of a close-up is that it is LARGER than the
        stored frame, not a thumbnail with a frame around it. The window takes ~85 %
        of the screen the tab is on, and the picture is scaled up to fill it."""
        if self._frame_dlg is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Frame close-up")
            # A window, not a modal box: the wall stays clickable behind it, so the
            # next tile can be opened without closing this one.
            dlg.setModal(False)
            dlg.setSizeGripEnabled(True)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(6, 6, 6, 6); lay.setSpacing(4)
            lay.addWidget(self._frame_page)
            # Re-fit the picture when the window is resized — the same 50 ms debounce
            # the tab used, so a drag does not re-render on every pixel.
            def _on_resize(ev, _orig=dlg.resizeEvent):
                _orig(ev)
                if self._preview_paths:
                    QTimer.singleShot(50, self._preview_show)
            dlg.resizeEvent = _on_resize
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None:
                av = scr.availableGeometry()
                dlg.resize(int(av.width() * 0.85), int(av.height() * 0.85))
            else:
                dlg.resize(1200, 900)
            self._frame_dlg = dlg
        self._frame_dlg.show()
        self._frame_dlg.raise_()
        self._frame_dlg.activateWindow()
        self._preview_show()

    def _set_view_mode(self, idx: int):
        """Kept for the callers that only ever meant "show a wall" (0) or "show the
        close-up" (1). The close-up is a window of its own now; a wall is whichever
        wall tab was last looked at."""
        if idx:
            self._show_frame_window()
        else:
            self._view_tabs.setCurrentIndex(self._last_wall_tab)

    def _on_view_tab_changed(self, idx: int):
        wall = self._wall_pages.get(idx)
        on_wall = wall is not None
        if on_wall:
            self._last_wall_tab = idx
            self._wall = wall
            self._sync_wall_display()
            # The reference day names a tile, and every tab holds a different set of
            # them, so the list is rebuilt for the wall now on screen.
            self._rebuild_baseline_combo(wall.cells())
        for w in (self._baseline_cb, self._btn_save_wall):
            w.setEnabled(on_wall)
        for w in self._overlay_widgets:
            w.setEnabled(on_wall)
        # Fit belongs to the wall now on screen, and each wall carries its own zoom.
        if on_wall:
            self._sync_zoom_label()
        elif hasattr(self, "_btn_fit"):
            self._btn_fit.setEnabled(False)
            self._btn_fit.setText("Fit")

    def _results_from_files(self, files: "list[Path]") -> dict:
        """Turn a plain list of frames into the {camera: [(day, hour, path, meta,
        status), …]} shape `fill_wall` reads, so the single-day Load data lands on the
        same wall as a multi-day search instead of needing a second display path.

        The camera is the folder the frame sits in, and the day and hour come from the
        timestamp in the file name — read in Prague time, the way the rest of the tab
        reads a frame, not from the UTC hour folder above it."""
        out: dict = {}
        for p in files:
            p = Path(p)
            cam_folder = p.parent.name
            ns = extract_ns_from_stem(p.stem)
            day, hour = None, None
            if ns is not None:
                dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                if PRAGUE is not None:
                    dt = dt.astimezone(PRAGUE)
                day, hour = dt.date(), dt.hour
            out.setdefault(cam_folder, []).append(
                (day, hour, p, {"source": "manual"}, "found"))
        return out

    def fill_wall(self, results: dict, cameras: "list | None" = None,
                  moment_ns: "int | None" = None,
                  moments_ns: "list | None" = None):
        """Put a finished search on the walls: one tile per day per camera.

        `results` is the shape the multi-day search already produces —
        {cam_folder_name: [(day, hour, path, meta, status), …]}.

        One tab per camera holds that camera's days next to each other, and one more
        holds every camera arranged a day per row. They share one frame cache and one
        set of per-frame adjustments (`_WallShared`), so a frame is read from the share
        once however many tabs it appears in.

        `moment_ns` says the whole wall is picked MOMENTS seen by many cameras
        (`moments_ns` carries the rest of them when more than one was picked). Then
        there is a single wall instead of a tab per camera — a tab holding one tile
        is not a comparison — and a camera that had nothing near the moment is kept
        on it. Everywhere else a camera with no frame is dropped, because a
        many-day search would otherwise fill the wall with empty tiles for every
        day a camera did not run."""
        one_moment = moment_ns is not None
        cells = []
        for cam_name in sorted(results.keys()):
            for day, _hour, path, meta, status in results[cam_name]:
                keep = (status == "found" and path) or \
                       (one_moment and status == "no_frame")
                if not keep:
                    continue
                _m = dict(meta or {})
                _reg = _m.get("region") if isinstance(_m.get("region"), dict) else None
                cells.append({
                    "day": day,
                    "cam": extract_display_label(cam_name),
                    "cam_folder": cam_name,
                    "meta": _m,
                    # Which pick this tile answers, 1-based — the number the graph
                    # drew beside the moment. Absent when only one was picked.
                    "pick": _m.get("pick"),
                    # Which marked region it answers, and the row it belongs on.
                    # `(day, None)` for everything that is not region-driven, which
                    # is what keeps one row per day for the CSV / energy / blind
                    # searches and the moment wall.
                    "region": _reg,
                    "row_key": (day, _reg.get("index") if _reg else None),
                    "path": Path(path) if path else None,
                    "ts_ns": extract_ns_from_stem(Path(path).stem) if path else None,
                    "status": status,
                })
        if one_moment:
            # Camera first, pick second: with several moments picked the wall is a
            # tab per camera, and inside a camera's tab the tiles read in the order
            # the moments were clicked.
            cells.sort(key=lambda c: (c["cam"], c.get("pick") or 0))
            self._build_wall_tabs(cells, moment_ns=moment_ns,
                                  moments_ns=moments_ns)
            return
        cells.sort(key=lambda c: (c["cam"], str(c["day"]),
                                  (c.get("region") or {}).get("index") or -1))
        self._build_wall_tabs(cells)
        # Cells, not days: a day carrying four marked regions puts four frames per
        # camera on the wall, and calling those "4 days" was simply wrong.
        n_rows = len({c.get("row_key") for c in cells})
        self._log(f"WALL: {len(cells)} frame(s) on the wall, {n_rows} row(s).")

    def _build_wall_tabs(self, cells: list, moment_ns: "int | None" = None,
                         moments_ns: "list | None" = None):
        # Every tab is a wall now — the close-up moved to a window of its own — so the
        # whole bar is rebuilt.
        self._view_tabs.blockSignals(True)
        while self._view_tabs.count() > 0:
            page = self._view_tabs.widget(0)
            self._view_tabs.removeTab(0)
            page.deleteLater()
        self._wall_pages.clear()
        self._cam_walls.clear()

        picks = [int(t) for t in (moments_ns or [])]
        if moment_ns is not None and not picks:
            picks = [int(moment_ns)]
        if moment_ns is not None and len(picks) <= 1:
            # ONE moment, every camera — one wall. A tab per camera would each hold
            # a single tile, which is the opposite of what this view is for. With
            # SEVERAL moments picked that reasoning no longer holds: the wall then
            # goes through the ordinary path below — a tab per camera, plus Day by
            # day — because the flat grid of every camera × every moment is an
            # export layout, not something to read on screen.
            wall = _DayWall(shared=self._wall_shared)
            self._wire_wall(wall)
            wall.set_cells(cells)

            def _local(ns):
                w = datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)
                return w.astimezone(PRAGUE) if PRAGUE is not None else w
            title = _local(picks[0]).strftime("%d.%m.  %H:%M:%S")
            idx = self._view_tabs.addTab(self._wrap_scroll(wall), title)
            self._wall_pages[idx] = wall
            self._day_wall = wall
            for c in cells:
                self._cam_walls[c["cam"]] = wall
            self._view_tabs.blockSignals(False)
            self._last_wall_tab = idx
            self._wall = wall
            self._sync_wall_display()
            self._rebuild_baseline_combo(cells)
            self._view_tabs.setCurrentIndex(idx)
            self._on_view_tab_changed(idx)
            self._on_wall_selection_changed()
            return

        cams = sorted({c["cam"] for c in cells})
        for cam in cams:
            w = _DayWall(shared=self._wall_shared)
            self._wire_wall(w)
            w.set_cells([c for c in cells if c["cam"] == cam])
            idx = self._view_tabs.addTab(self._wrap_scroll(w), cam)
            self._wall_pages[idx] = w
            self._cam_walls[cam] = w

        day_wall = _DayWall(shared=self._wall_shared)
        self._wire_wall(day_wall)
        if cells:
            day_wall.set_layout_mode("rows")
            day_wall.set_cells(cells)
            title = "Day by day"
        else:
            # Nothing found. One empty wall still goes up, so every caller — Stop All,
            # the display sliders — has a wall to talk to, and it says so on its face.
            title = "Days side by side"
        idx = self._view_tabs.addTab(self._wrap_scroll(day_wall), title)
        self._wall_pages[idx] = day_wall
        self._day_wall = day_wall
        self._view_tabs.blockSignals(False)

        first_wall = min(self._wall_pages) if self._wall_pages else 0
        self._last_wall_tab = first_wall
        self._wall = self._wall_pages.get(first_wall, self._wall)
        self._sync_wall_display()
        self._rebuild_baseline_combo(self._wall.cells())
        self._view_tabs.setCurrentIndex(first_wall)
        self._on_view_tab_changed(first_wall)
        self._on_wall_selection_changed()

    def _rebuild_baseline_combo(self, cells: list):
        self._baseline_cb.blockSignals(True)
        self._baseline_cb.clear()
        self._baseline_cb.addItem("none", None)
        multi_cam = len({c["cam"] for c in cells}) > 1
        for i, c in enumerate(cells):
            day = c["day"]
            txt = day.strftime("%d.%m.%Y") if hasattr(day, "strftime") else str(day)
            if multi_cam:
                txt = f"{c['cam']}  {txt}"
            # Two moments picked (or two regions marked) on one day would
            # otherwise give two entries with the same words, and no way to tell
            # which frame is being chosen.
            if c.get("pick"):
                txt = f"{c['pick']})  {txt}"
            reg = c.get("region") or {}
            if reg.get("index"):
                txt += f"   region {reg['index']}"
                if reg.get("t_start_ns"):
                    try:
                        _a = datetime.fromtimestamp(
                            int(reg["t_start_ns"]) / 1e9, tz=timezone.utc)
                        if PRAGUE is not None:
                            _a = _a.astimezone(PRAGUE)
                        txt += "  " + _a.strftime("%H:%M:%S")
                    except Exception:
                        pass
            self._baseline_cb.addItem(txt, i)
        self._baseline_cb.blockSignals(False)

    def _on_baseline_changed(self, _idx: int):
        self._wall.set_baseline(self._baseline_cb.currentData())

    # ── Overlay / rotation / undo block ───────────────────────────────────────
    def _build_overlay_controls(self, ll: QVBoxLayout):
        """Marking up a frame and turning it round — carried over from the pop-up window
        that used to open after every search, then from the strip above the pictures.

        It is a column now, not a row: two buttons per line so a 240 px panel holds them
        without eliding a caption, and each shape keeps its colour swatch beside it."""
        self._overlay_widgets: list = []

        def keep(w):
            # Every control here is dead unless a wall is on screen — see
            # _on_view_tab_changed, which enables and disables this whole list.
            self._overlay_widgets.append(w)
            return w

        grid = QGridLayout(); grid.setSpacing(4)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)

        self._wall_draw_btns: dict = {}
        self._wall_shape_colors = getattr(self, "_wall_shape_colors", {})
        self._wall_color_btns = getattr(self, "_wall_color_btns", {})
        cells = (("circle", "Circle", "mark_circle", QColor(255, 255, 0, 230), 0, 0),
                 ("square", "Square", "mark_square", QColor(0, 200, 255, 230), 0, 1),
                 ("cross",  "Cross",  "mark_cross",  QColor(0, 255, 0, 220),   1, 0))
        for kind, label, icon, col, r, c in cells:
            btn = keep(QPushButton(label))
            _set_action_icon(btn, icon)
            btn.setCheckable(True)
            btn.setToolTip(f"Draw a {label.lower()} on a frame: drag on it, then drag "
                           "the handles to move or resize. Shift keeps it round/square.")
            btn.toggled.connect(lambda on, k=kind: self._on_draw_mode_toggled(k, on))
            self._wall_draw_btns[kind] = btn

            sw = keep(QPushButton())
            sw.setFixedSize(16, 16)
            sw.setToolTip(f"{label} colour")
            sw.setStyleSheet(f"background:{col.name()}; border:1px solid #888; "
                             "border-radius:2px;")
            sw.clicked.connect(lambda _=False, k=kind: self._pick_shape_color(k))
            self._wall_shape_colors[kind] = col
            self._wall_color_btns[kind] = sw

            cell = QHBoxLayout(); cell.setSpacing(3); cell.setContentsMargins(0, 0, 0, 0)
            cell.addWidget(btn, 1); cell.addWidget(sw, 0)
            grid.addLayout(cell, r, c)

        b = keep(QPushButton("Clear marks"))
        _set_action_icon(b, "marks_clear")
        b.setToolTip("Remove every drawn mark from every frame.")
        b.clicked.connect(self._clear_wall_overlays)
        grid.addWidget(b, 1, 1)

        # The direction stays in the WORDS. The two turning icons are mirror images
        # of each other and at 16 px they cannot be told apart, so an icon-only pair
        # would be two identical buttons that do opposite things.
        b = keep(QPushButton("Left 90°"))
        _set_action_icon(b, "rotate_left")
        b.setToolTip("Turn the selected frames counter-clockwise (all of them if none "
                     "is selected).")
        b.clicked.connect(lambda: self._rotate_wall(-90))
        grid.addWidget(b, 2, 0)
        b = keep(QPushButton("Right 90°"))
        _set_action_icon(b, "rotate_right")
        b.setToolTip("Turn the selected frames clockwise (all of them if none is "
                     "selected).")
        b.clicked.connect(lambda: self._rotate_wall(+90))
        grid.addWidget(b, 2, 1)

        b = keep(QPushButton("Undo"))
        _set_action_icon(b, "undo")
        b.setToolTip("Step back through the changes made to individual frames.")
        b.clicked.connect(self._undo_wall_edit)
        grid.addWidget(b, 3, 0)
        b = keep(QPushButton("Reset"))
        _set_action_icon(b, "reset")
        b.setToolTip("Put every frame back on the shared brightness and remove all marks.")
        b.clicked.connect(self._reset_wall_edits)
        grid.addWidget(b, 3, 1)

        ll.addLayout(grid)

        # One mark, every frame. On by default — the reason to draw a circle round a
        # beam is almost always to ask whether the other frames sit inside it.
        self._cb_link_marks = keep(QCheckBox("Same spot on every frame"))
        self._cb_link_marks.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_link_marks.setChecked(True)
        self._cb_link_marks.setToolTip(
            "A mark drawn on one frame appears on all of them, at the same relative "
            "point.\n\n"
            "Over many days of ONE camera that is the same sensor pixel. Across "
            "cameras of different shape it is the same relative point, not the same "
            "distance. A frame turned 90° wears its mark at the same place on "
            "screen, not on the sensor.")
        self._cb_link_marks.toggled.connect(self._on_link_marks_toggled)
        ll.addWidget(self._cb_link_marks)

        self._sel_wall_lbl = QLabel("0 frames selected")
        self._sel_wall_lbl.setStyleSheet("color:#888; font-size:11px;")
        self._sel_wall_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                        Qt.AlignmentFlag.AlignVCenter)
        keep(self._sel_wall_lbl)
        ll.addWidget(self._sel_wall_lbl)

    def _on_draw_mode_toggled(self, kind: str, on: bool):
        # At most one shape mode at a time, as in the window this came from.
        if on:
            for k, btn in self._wall_draw_btns.items():
                if k != kind and btn.isChecked():
                    btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
        mode = kind if on else ""
        for w in self._all_walls():
            w.set_draw_mode(mode)

    def _on_link_marks_toggled(self, on: bool):
        """Turning it ON does not go back and copy the existing marks: it changes
        what the NEXT mark does. Reaching back would silently overwrite a frame
        somebody had deliberately marked on its own."""
        self._wall_shared.link_marks = bool(on)

    def _pick_shape_color(self, kind: str):
        cur = self._wall_shape_colors.get(kind, QColor("#ffffff"))
        col = QColorDialog.getColor(cur, self, f"{kind.capitalize()} colour")
        if not col.isValid():
            return
        self._wall_shape_colors[kind] = col
        self._wall_color_btns[kind].setStyleSheet(
            f"background:{col.name()}; border:1px solid #888; border-radius:2px;")
        for w in self._all_walls():
            w.set_shape_color(kind, col)

    def _clear_wall_overlays(self):
        self._wall_shared.push_undo()
        for w in self._all_walls():
            w.clear_overlays()

    def _rotate_wall(self, delta: int):
        paths = self._wall_target_paths()
        if not paths:
            return
        self._wall_shared.push_undo()
        for w in self._all_walls():
            w.rotate(paths, delta)

    def _undo_wall_edit(self):
        if not self._wall_shared.pop_undo():
            return
        for w in self._all_walls():
            w.refresh_edits()
        self._on_wall_selection_changed()

    def _reset_wall_edits(self):
        self._wall_shared.push_undo()
        self._wall_shared.clear_edits()
        for w in self._all_walls():
            w.refresh_edits()

    # ── selection ─────────────────────────────────────────────────────────────
    def _wall_target_paths(self) -> list:
        """Which frames a display control aims at: the selected ones, or every frame on
        the wall when nothing is selected. Same rule as the Image Slider — the scope is
        read at the moment the control is used, so picking a frame afterwards never
        drags somebody else's settings onto it."""
        sel = set(self._wall_shared.sel)
        if sel:
            return list(sel)
        return [c["path"] for c in self._wall.cells() if c.get("path") is not None]

    def _on_wall_selection_changed(self):
        n = len(self._wall_shared.sel)
        if hasattr(self, "_sel_wall_lbl"):
            self._sel_wall_lbl.setText(
                "0 frames selected" if n == 0 else
                ("1 frame selected" if n == 1 else f"{n} frames selected"))
        for w in self._all_walls():
            w.update()

    def _sync_wall_display(self):
        """Push the display controls onto the walls.

        With nothing selected this is the shared setting every tile renders on — which
        is what keeps the days comparable. With frames selected the values become THOSE
        frames' own, and they are marked as adjusted; see the note on _DayWall."""
        if not hasattr(self, "_wall"):
            return
        auto, gamma, contrast, offset = self._bc_args()
        grad = self._gradient_cb.currentText() if hasattr(self, "_gradient_cb") else "Grayscale"
        sel = list(self._wall_shared.sel)
        if sel:
            # Scoped: the slider moved THESE frames and must leave the rest of the wall
            # exactly where it was, so the shared values are held at what they were.
            shared = getattr(self, "_wall_display_shared", (auto, gamma, contrast, offset))
        else:
            shared = (auto, gamma, contrast, offset)
        self._wall_display_shared = shared
        # The palette is a colour scheme, not an intensity, so it always applies to the
        # whole wall — a per-frame palette would be a second legend on the same picture.
        for w in self._all_walls() or [self._wall]:
            w.set_display(grad, *shared)
            if sel:
                w.apply_adjust(sel, contrast, offset, gamma)

    def _on_wall_tile_clicked(self, idx: int):
        """A tile is the way into the close-up: the wall answers "which day is
        different", the close-up window answers "what exactly does it look like".

        The whole wall goes into the close-up, not just the clicked tile, so ◀ ▶ step
        through the frames next to the one that was opened instead of dead-ending."""
        cells = self._wall.cells()
        if not (0 <= idx < len(cells)):
            return
        paths = [c["path"] for c in cells if c.get("path") is not None]
        cams  = [c.get("cam", "") for c in cells if c.get("path") is not None]
        cell  = cells[idx]
        if paths:
            try:
                start = paths.index(cell["path"])
            except ValueError:
                start = 0
            self._preview_set_files(paths, cell.get("cam", ""), cams, index=start)
        else:
            self._preview_set_files([cell["path"]], cell.get("cam", ""),
                                    [cell.get("cam", "")])
        self._show_frame_window()

    def _on_wall_context(self, idx: int, gpos):
        cells = self._wall.cells()
        if not (0 <= idx < len(cells)):
            return
        cell = cells[idx]
        menu = QMenu(self)
        act_open = menu.addAction("🔍 Open close-up")
        act_again = menu.addAction("↻ Search again…")
        act_pick = menu.addAction("📂 Pick image from folder…")
        menu.addSeparator()
        is_base = (self._wall.baseline_idx() == idx)
        act_ref = menu.addAction("Clear reference day" if is_base else "Set as reference day")
        act_clear = menu.addAction("Clear marks on this frame")
        chosen = menu.exec(gpos)
        if chosen is None:
            return
        if chosen is act_open:
            self._on_wall_tile_clicked(idx)
        elif chosen is act_again:
            self._wall_search_again(cell)
        elif chosen is act_pick:
            self._wall_pick_from_folder(cell)
        elif chosen is act_ref:
            self._baseline_cb.setCurrentIndex(0 if is_base else idx + 1)
        elif chosen is act_clear:
            self._wall_shared.push_undo()
            for w in self._all_walls():
                w.clear_overlays([cell.get("path")])

    # ── Another try at one day's frame ────────────────────────────────────────
    @staticmethod
    def _cell_identity(cell: dict) -> tuple:
        """What names ONE tile: the camera, the day, the marked region and the
        picked moment.

        The camera and the day alone are not enough — four regions marked on one
        day, or four moments picked on it, are four different tiles, and picking a
        file for one of them used to overwrite all four."""
        reg = cell.get("region") or {}
        return (cell.get("cam", ""), cell.get("day"),
                reg.get("index"), cell.get("pick"))

    def _replace_cell_frame(self, cell: dict, new_path: Path):
        """Put a different picture in one tile, on every wall showing it."""
        want = self._cell_identity(cell)
        new_path = Path(new_path)
        for w in self._all_walls():
            touched = False
            for c in w._cells:
                if self._cell_identity(c) == want:
                    c["path"] = new_path
                    c["ts_ns"] = extract_ns_from_stem(new_path.stem)
                    c["meta"] = dict(c.get("meta") or {}, source="manual")
                    touched = True
            if touched:
                w._pix.clear()
                w._load_gen += 1
                w._kick_load(w._load_gen)
                w._relayout()
                w.update()
        self._rebuild_baseline_combo(self._wall.cells())

    def _wall_search_again(self, cell: dict):
        """Look for a different frame for this camera on this day, starting from an hour
        the user names. Carried over from the pop-up window, where it was the one thing
        the wall could not do."""
        from PySide6.QtWidgets import QInputDialog
        cam_folder = cell.get("cam_folder") or cell.get("cam", "")
        day = cell.get("day")
        if not cam_folder or day is None:
            return
        self._try_hour = getattr(self, "_try_hour", {})
        # The region and the pick are part of the key: two regions (or two picked
        # moments) on one day are two tiles, and the hour already tried for one of
        # them says nothing about the other.
        key = self._cell_identity(cell)
        cur_h = None
        ts = cell.get("ts_ns")
        if ts:
            try:
                cur_h = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)\
                                .astimezone(PRAGUE).hour
            except Exception:
                cur_h = None
        last_tried = self._try_hour.get(key, cur_h if cur_h is not None else -1)
        default_h = max(0, last_tried + 1) if last_tried < 23 else 0
        cur_str = f"{cur_h:02d}:00" if cur_h is not None else "unknown"
        chosen_hour, ok = QInputDialog.getInt(
            self, "Search again",
            f"{cell.get('cam', '')}  {day.strftime('%d.%m.%Y')}\n"
            f"Currently showing: {cur_str}  |  Last tried: {last_tried:02d}:00\n"
            f"Search from hour:",
            default_h, 0, 23)
        if not ok:
            return
        self._try_hour[key] = chosen_hour
        self._run_search_again(cell, cam_folder, day, chosen_hour)

    def _sa_candidates(self, cam_folder: str, day, real_h: int,
                       exclude: "set[str]", log) -> list:
        """Ranked candidate frames for one (camera, day, Prague hour): the energy-anchored
        picks first, then the file-size heuristic."""
        if PRAGUE is not None:
            dt_p = datetime(day.year, day.month, day.day, real_h, tzinfo=PRAGUE)
            dt_eff = dt_p.astimezone(timezone.utc).replace(tzinfo=None)
        else:
            dt_eff = datetime(day.year, day.month, day.day, real_h)
        folder = self._build_target_path(dt_eff) / cam_folder
        try:
            if not folder.exists():
                return []
        except Exception:
            return []
        out, seen = [], set(exclude)

        def _add(paths):
            for p in paths or []:
                sp = str(p)
                if sp not in seen:
                    seen.add(sp)
                    out.append(p)

        try:
            start_ns = int(datetime(dt_eff.year, dt_eff.month, dt_eff.day, dt_eff.hour,
                                    tzinfo=timezone.utc).timestamp() * 1_000_000_000)
            _add(self._select_by_totalpower(folder, 3, _cam_totalpower_channel(cam_folder),
                                            log, start_ns,
                                            start_ns + 3_600_000_000_000 - 1))
        except Exception as e:
            log(f"  energy anchor failed ({type(e).__name__}: {e}) — size fallback")
        try:
            _add(self.select_images_from_folder(folder, 3))
        except Exception:
            pass
        return out

    def _run_search_again(self, cell: dict, cam_folder: str, day, chosen_hour: int):
        prog = QDialog(self)
        prog.setWindowTitle("Searching…")
        prog.setMinimumWidth(400)
        pv = QVBoxLayout(prog)
        plbl = QLabel(f"{cell.get('cam','')}  {day.strftime('%d.%m.%Y')}")
        plbl.setWordWrap(True)
        hour_lbl = QLabel()
        hour_lbl.setStyleSheet("font-size:10px; color:#555;")
        btn_cancel = QPushButton("Cancel")
        pv.addWidget(plbl); pv.addWidget(hour_lbl)
        pv.addWidget(btn_cancel, 0, Qt.AlignmentFlag.AlignRight)

        cancel_ev = threading.Event()
        btn_cancel.clicked.connect(cancel_ev.set)
        prog.rejected.connect(cancel_ev.set)

        self._try_sig = _TryAgainSignals()
        sig = self._try_sig
        sig.progress.connect(lambda _m, h: hour_lbl.setText(h))

        def _on_cell(_cam, _date, real_h, path, line):
            if path:
                self._try_hour[self._cell_identity(cell)] = real_h
                self._replace_cell_frame(cell, Path(path))
            self._log(f"SEARCH AGAIN: {line}")

        def _on_finished(summary):
            try:
                prog.accept()
            except Exception:
                pass
            if summary:
                QMessageBox.information(self, "Search again", "\n".join(summary))

        sig.cell_done.connect(_on_cell)
        sig.finished.connect(_on_finished)

        cur = cell.get("path")
        excl = {str(cur)} if cur else set()

        def worker():
            logs: list = []
            found_path = found_h = None
            tried = empty = 0
            for real_h in range(chosen_hour, 24):
                if cancel_ev.is_set():
                    break
                sig.progress.emit("", f"Scanning hour {real_h:02d}:00")
                for cand in self._sa_candidates(cam_folder, day, real_h, excl, logs.append):
                    if cancel_ev.is_set():
                        break
                    tried += 1
                    excl.add(str(cand))
                    if _image_is_nonempty(cand, log=logs.append):
                        found_path, found_h = cand, real_h
                        break
                    empty += 1
                if found_path is not None:
                    break
            if found_path is not None:
                line = (f"{cell.get('cam','')} {day.strftime('%d.%m')}: found "
                        f"{found_h:02d}:00 ({tried} candidate(s), {empty} empty)")
                sig.cell_done.emit(cam_folder, day, found_h, str(found_path), line)
            else:
                line = (f"{cell.get('cam','')} {day.strftime('%d.%m')}: no non-empty "
                        f"image from {chosen_hour:02d}:00 ({tried} candidate(s), "
                        f"{empty} empty)")
                sig.cell_done.emit(cam_folder, day, chosen_hour, None, line)
            sig.finished.emit([line])

        threading.Thread(target=worker, daemon=True).start()
        prog.exec()

    def _wall_pick_from_folder(self, cell: dict):
        """Browse for any picture and put it in this tile."""
        cam_folder = cell.get("cam_folder") or cell.get("cam", "")
        day = cell.get("day")
        cur = cell.get("path")
        start_dir = str(Path(cur).parent) if cur else ""
        if not start_dir and day is not None:
            for real_h in range(24):
                if PRAGUE is not None:
                    dt_eff = datetime(day.year, day.month, day.day, real_h,
                                      tzinfo=PRAGUE).astimezone(timezone.utc)\
                                     .replace(tzinfo=None)
                else:
                    dt_eff = datetime(day.year, day.month, day.day, real_h)
                f = self._build_target_path(dt_eff) / cam_folder
                if f.exists():
                    start_dir = str(f)
                    break
        fname, _ = QFileDialog.getOpenFileName(
            self, f"Pick image — {cell.get('cam','')}  "
                  f"{day.strftime('%d.%m.%Y') if day else ''}",
            start_dir, "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if not fname or not Path(fname).exists():
            return
        self._replace_cell_frame(cell, Path(fname))

    # ── Save view ─────────────────────────────────────────────────────────────
    def _wall_provenance(self, wall, tab_name: str) -> dict:
        """What the saved file has to say about itself.

        A wall of frames with no words is an anonymous collage: which cameras,
        which days, how the frames were chosen and which one is the reference all
        have to travel with the picture."""
        cells = wall.cells()
        cams = sorted({c.get("cam", "") for c in cells if c.get("cam")})
        days = []
        for c in cells:
            d = c.get("day")
            txt = d.strftime("%d.%m.%Y") if hasattr(d, "strftime") else str(d)
            if txt not in days:
                days.append(txt)
        picked = sorted({(c.get("meta") or {}).get("source") or "?"
                         for c in cells})
        how = ", ".join(_DayWall._SOURCE_TAG.get(p, p) for p in picked if p != "?")
        ref = ""
        base = wall.baseline_idx()
        if base is not None and 0 <= base < len(cells):
            bd = cells[base].get("day")
            ref = bd.strftime("%d.%m.%Y") if hasattr(bd, "strftime") else str(bd)
        regs = sorted({(c.get("region") or {}).get("index")
                       for c in cells if c.get("region")})
        return {"tab": tab_name, "cams": cams, "days": days, "how": how,
                "ref": ref, "cells": len(cells), "regions": [r for r in regs if r]}

    @staticmethod
    def _provenance_line(p: dict) -> str:
        parts = [p["tab"]] if p.get("tab") else []
        if p["cams"]:
            parts.append(", ".join(p["cams"][:6])
                         + (f" +{len(p['cams']) - 6}" if len(p["cams"]) > 6 else ""))
        if p["days"]:
            parts.append(", ".join(p["days"][:6])
                         + (f" +{len(p['days']) - 6}" if len(p["days"]) > 6 else ""))
        if p["regions"]:
            parts.append(f"{len(p['regions'])} region(s)")
        if p["how"]:
            parts.append("picked by " + p["how"])
        if p["ref"]:
            parts.append("reference " + p["ref"])
        return "  |  ".join(parts)

    def _wall_page_image(self, wall, tab_name: str) -> "QImage | None":
        """One wall as one image, caption strip included.

        A wall on a tab that was never shown has had no `set_canvas` call, so it
        would composite at a placeholder size. The current tab's viewport size is
        stated on it, the picture is taken, and the old size put back — the same
        force-render-and-restore as Pulser Monitor's export."""
        page = self._view_tabs.currentWidget()
        vw = vh = 0
        try:
            vp = page.viewport()
            vw, vh = vp.width(), vp.height()
        except Exception:
            pass
        old = (getattr(wall, "_fit_w", 0), getattr(wall, "_fit_h", 0))
        try:
            if vw > 1 and vh > 1 and (old[0] <= 1 or old[1] <= 1):
                wall.set_canvas(vw, vh)
            wall._relayout()
            img = wall.composite_image()
        finally:
            if old[0] and old[1]:
                wall.set_canvas(old[0], old[1])
        if img is None:
            return None
        line = self._provenance_line(self._wall_provenance(wall, tab_name))
        if line:
            try:
                sl = _get_slider_module()
                return sl.qt_pv_bar_below(QPixmap.fromImage(img), line).toImage()
            except Exception as e:
                self._log(f"WALL: caption strip skipped — {e}")
        return img

    def _save_wall(self):
        """Save view — the whole wall (or every wall) as PNG or PDF."""
        title = "Save view"
        pages: list = []          # (tab name, wall)
        cur_idx = self._view_tabs.currentIndex()
        for idx, wall in sorted(self._wall_pages.items()):
            if wall.cells():
                pages.append((self._view_tabs.tabText(idx), wall, idx))
        if not pages:
            QMessageBox.information(self, title, "Nothing on the wall yet.")
            return
        opts = _SaveViewDialog(len(pages), self)
        if opts.exec() != QDialog.DialogCode.Accepted:
            return
        fmt, scope = opts.result()
        chosen = pages if scope == "all" else [
            p for p in pages if p[2] == cur_idx] or pages[:1]

        cams = sorted({c.get("cam", "") for c in chosen[0][1].cells()})
        stem = f"view_{'_'.join(cams)[:40] or 'wall'}"
        start = self._last_save_dir
        # NEVER hand the dialog a UNC path — it pays the full SMB timeout (~48 s)
        # before it draws.
        start_dir = (str(start) if start and not str(start).startswith("\\\\")
                     else _get_slider_module()._default_save_dir())
        if fmt == "pdf":
            path, _ = QFileDialog.getSaveFileName(
                self, title, str(Path(start_dir) / (stem + ".pdf")),
                "PDF document (*.pdf)")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, title, str(Path(start_dir) / (stem + ".png")),
                "PNG image (*.png)")
        if not path:
            return
        self._last_save_dir = Path(path).parent
        written: list = []
        failed: list = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if fmt == "pdf":
                self._save_view_pdf(Path(path), chosen, written, failed)
            else:
                self._save_view_png(Path(path), chosen, written, failed)
        finally:
            QApplication.restoreOverrideCursor()
        for p in written:
            self._log(f"WALL: saved {p}")
        msg = ""
        if written:
            msg += f"Saved {len(written)} file(s):\n" + "\n".join(
                str(p) for p in written)
        if failed:
            msg += ("\n\n" if msg else "") + "Failed:\n" + "\n".join(failed)
        QMessageBox.information(self, title, msg or "Nothing was saved.")

    def _save_view_png(self, path: Path, chosen: list, written: list,
                       failed: list):
        """One PNG per wall. Several walls → `<stem>_<tab>.png`."""
        many = len(chosen) > 1
        for tab_name, wall, idx in chosen:
            out = path
            if many:
                safe = re.sub(r"[^0-9A-Za-z._-]+", "_", tab_name).strip("_") or f"tab{idx}"
                out = path.with_name(f"{path.stem}_{safe}{path.suffix}")
            try:
                img = self._wall_page_image(wall, tab_name)
                if img is None:
                    failed.append(f"{tab_name}: nothing to draw")
                    continue
                writer = QImageWriter(str(out), b"png")
                p = self._wall_provenance(wall, tab_name)
                writer.setText("Camera", ", ".join(p["cams"]))
                writer.setText("Days", ", ".join(p["days"]))
                if p["ref"]:
                    writer.setText("Reference day", p["ref"])
                if p["how"]:
                    writer.setText("Frames picked by", p["how"])
                writer.setText("Scale",
                               "absolute, per-camera sensor range (img_scale)")
                if not writer.write(img):
                    raise RuntimeError(writer.errorString())
                written.append(out)
            except Exception as e:
                failed.append(f"{tab_name}: {e}")

    def _save_view_pdf(self, path: Path, chosen: list, written: list,
                       failed: list):
        """One PDF, one page per wall.

        The page is sized from the composite's own pixel size at 200 dpi, so
        nothing is scaled down or cropped. `QPdfWriter` comes with PySide6 — no new
        dependency."""
        from PySide6.QtGui import QPdfWriter, QPageSize, QPageLayout
        from PySide6.QtCore import QSizeF, QMarginsF
        dpi = 200
        writer = None
        painter = None
        try:
            for tab_name, wall, _idx in chosen:
                img = self._wall_page_image(wall, tab_name)
                if img is None:
                    failed.append(f"{tab_name}: nothing to draw")
                    continue
                w_pt = img.width() * 72.0 / dpi
                h_pt = img.height() * 72.0 / dpi
                size = QPageSize(QSizeF(w_pt, h_pt), QPageSize.Unit.Point,
                                 "wall", QPageSize.SizeMatchPolicy.ExactMatch)
                if writer is None:
                    writer = QPdfWriter(str(path))
                    writer.setResolution(dpi)
                    writer.setTitle(f"Image Finder — {tab_name}")
                    writer.setCreator("Image Tools / Image Finder")
                    writer.setPageSize(size)
                    writer.setPageMargins(QMarginsF(0, 0, 0, 0),
                                          QPageLayout.Unit.Point)
                    painter = QPainter(writer)
                else:
                    writer.setPageSize(size)
                    writer.setPageMargins(QMarginsF(0, 0, 0, 0),
                                          QPageLayout.Unit.Point)
                    writer.newPage()
                painter.drawImage(0, 0, img)
            if painter is not None:
                painter.end()
                painter = None
                written.append(path)
            elif not failed:
                failed.append("nothing to draw")
        except Exception as e:
            failed.append(f"{path.name}: {e}")
        finally:
            if painter is not None:
                painter.end()


    def cancel_scan(self):
        """Stop whatever this tab has running. main.py's 'Stop All' has always called
        this — it just never existed, inside a bare except, so Stop All silently did
        nothing here."""
        self._load_gen += 1
        self._preview_gen = getattr(self, "_preview_gen", 0) + 1
        self._moment_gen += 1
        self._moment_stop.set()
        for w in (self._all_walls() if hasattr(self, "_wall_pages") else []):
            w._load_gen += 1
        if hasattr(self, "_wall"):
            self._wall._load_gen += 1
        self._log("STOP: cancelled pending scans.")

    # ── Inline preview panel ──────────────────────────────────────────────────
    def _preview_load_cam(self, cam: dict):
        """Enumerate one camera's folder in the background, then show its first
        image. Called from the picked-cameras list."""
        folder = cam.get("path")
        if folder is None:
            return
        cam_name = cam.get("label") or folder.name
        self._preview_from_view = False

        self._preview_gen += 1
        scan_gen = self._preview_gen

        def _scan():
            try:
                if not folder.exists():
                    return
                files = sorted(
                    [p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS],
                    key=lambda p: p.name)
            except Exception:
                files = []

            def _on_done():
                if getattr(self, "_preview_gen", 0) != scan_gen:
                    return
                if not files:
                    self._preview_counter.setText("0 / 0")
                    return
                self._preview_paths = files
                self._preview_cam_names = []
                self._preview_idx   = 0
                self._preview_cam   = cam_name
                self._preview_show()

            QTimer.singleShot(0, _on_done)

        threading.Thread(target=_scan, daemon=True).start()

    def _preview_set_files(self, files: list, cam_name: str = "",
                           cam_names: "list[str] | None" = None,
                           index: int = 0):
        """Set the preview to a specific file list (e.g. after View).

        `index` is which of them to show — a tile click opens the whole wall but has
        to land on the frame that was clicked, not on the first one."""
        if not files:
            return
        self._preview_paths    = list(files)
        # Per-file cam names: use provided list, else derive from parent folder name
        if cam_names and len(cam_names) == len(files):
            self._preview_cam_names = list(cam_names)
        else:
            self._preview_cam_names = [f.parent.name for f in files]
        self._preview_idx      = index if 0 <= index < len(files) else 0
        self._preview_cam      = cam_name
        self._preview_from_view = True
        self._preview_show()

    def _preview_prev(self):
        if not self._preview_paths:
            return
        self._preview_idx = (self._preview_idx - 1) % len(self._preview_paths)
        self._preview_show()

    def _preview_next(self):
        if not self._preview_paths:
            return
        self._preview_idx = (self._preview_idx + 1) % len(self._preview_paths)
        self._preview_show()

    def _on_preview_ready(self, pm: QPixmap, gen: int):
        """Slot called on main thread when background thread finishes loading."""
        if gen != self._preview_gen:
            return
        energy_text = getattr(self, "_preview_energy_text", "")
        if energy_text:
            pm = self._paint_pv_bar(pm, energy_text)
        self._preview_scale_lbl.setText(getattr(self, "_preview_scale_note", ""))
        # Park the greyed-out sliders on what the Auto passes actually applied to this
        # frame, so the number on screen is the number in the picture (same contract as
        # the Slider tab's Auto controls).
        self._park_auto_bc(getattr(self, "_bc_applied", None))
        lbl = self._preview_lbl
        avail_w = max(lbl.width(),  200)
        avail_h = max(lbl.height(), 200)
        pm = pm.scaled(avail_w, avail_h,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        lbl.setPixmap(pm)

    @staticmethod
    def _paint_pv_bar(pm: QPixmap, energy_text: str) -> QPixmap:
        """Return a new pixmap = image + a white bar with centered black PV text.
        Adaptive font fit / 2-line split, ported from Shot Finder."""
        from PySide6.QtGui import QFontMetrics
        available_w = pm.width() - 20
        font = QFont()
        display_text = energy_text
        fitted = False
        for fsize in range(22, 8, -1):
            font.setPixelSize(fsize)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(energy_text) <= available_w:
                fitted = True
                break
        if not fitted:
            parts_split = energy_text.split("  |  ")
            mid = len(parts_split) // 2
            display_text = ("  |  ".join(parts_split[:mid]) + "\n" +
                            "  |  ".join(parts_split[mid:]))
            for fsize in range(18, 8, -1):
                font.setPixelSize(fsize)
                fm = QFontMetrics(font)
                max_line = max(fm.horizontalAdvance(l) for l in display_text.split("\n"))
                if max_line <= available_w:
                    break
        fm = QFontMetrics(font)
        line_count = display_text.count("\n") + 1
        bar_h = max(38, fm.height() * line_count + 16)
        combined = QPixmap(pm.width(), pm.height() + bar_h)
        combined.fill(QColor(255, 255, 255))
        painter = QPainter(combined)
        painter.drawPixmap(0, 0, pm)
        bar_rect = QRect(0, pm.height(), pm.width(), bar_h)
        painter.fillRect(bar_rect, QColor(255, 255, 255))
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(bar_rect,
                         Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                         display_text)
        painter.end()
        return combined

    def _preview_show(self):
        if not self._preview_paths:
            return
        idx   = self._preview_idx
        total = len(self._preview_paths)
        path  = self._preview_paths[idx]
        self._preview_counter.setText(f"{idx + 1} / {total}")
        self._prev_btn.setEnabled(total > 1)
        self._next_btn.setEnabled(total > 1)
        # Use per-file cam name if available, fallback to folder name
        per_file_cam = (self._preview_cam_names[idx]
                        if self._preview_cam_names and idx < len(self._preview_cam_names)
                        else None)
        raw_cam = per_file_cam or self._preview_cam or path.parent.name
        self._preview_cam_lbl.setText(extract_display_label(raw_cam))
        ns = extract_ns_from_stem(path.stem)
        if ns is not None:
            try:
                dt_utc = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
                try:
                    from zoneinfo import ZoneInfo as _ZI
                    dt_local = dt_utc.astimezone(_ZI("Europe/Prague"))
                except Exception:
                    dt_local = dt_utc
                ts_str = dt_local.strftime("%Y-%m-%d  %H:%M:%S.") + \
                         f"{dt_local.microsecond // 1000:03d}"
            except Exception:
                ts_str = path.stem
        else:
            ts_str = path.stem
        self._preview_ts_lbl.setText(ts_str)

        # PV overlay text for the bar painted in _on_preview_ready (main thread).
        self._preview_energy_text = ""
        # Cleared here so a failed load shows no note rather than the previous frame's.
        self._preview_scale_note = ""
        if (self._cb_pv_preview.isChecked() and self._pv_visible_cols()):
            entry = self._energy_entry_for_path(path)
            if entry is not None:
                parts = self._energy_parts_for_path(path, entry)
                self._preview_energy_text = "  |  ".join(parts)
        # The table reports on THIS frame, so it follows the preview.
        self._pv_refresh_table()

        # With the close-up shut there is nothing to draw into. The captions and the
        # PV table above still had to be updated — they are read from the panel — but
        # rendering the frame would be a read off the share for a picture nobody can
        # see. Opening the window calls back in here.
        if not self._frame_window_is_open():
            return

        self._preview_gen += 1
        gen = self._preview_gen
        sig = self._preview_sig
        grad_name = self._gradient_cb.currentText()
        # Read the widgets HERE: _load runs on a worker thread and touching a widget
        # from one is not safe.
        auto, gamma, contrast, offset = self._bc_args()

        def _load():
            try:
                img = PilImage.open(path)
                if img.mode in ("I", "I;16"):
                    arr = np.array(img, dtype=np.float32)
                else:
                    arr = np.array(img.convert("L"), dtype=np.float32)
                # The camera's reference range for a 16-bit frame, 255 for an 8-bit one —
                # see img_scale.full_scale_for_pil.
                full_scale = img_scale.full_scale_for_pil(path, img.info, img.mode)
                # `bc_out` comes back with what the Auto passes actually applied, so
                # the greyed-out sliders can be parked on it and Auto gamma costs no
                # second median pass over the frame.
                bc_out: dict = {}
                arr8 = _render_u8(arr, auto, full_scale, gamma, contrast, offset, bc_out)
                self._bc_applied = bc_out
                g_applied = (bc_out.get("gamma")
                             if (not auto and img_scale.is_auto_gamma(gamma)) else None)
                self._preview_scale_note = _scale_note(img.info, arr, auto, full_scale,
                                                       gamma, path, img.mode,
                                                       contrast, offset, g_applied)
                lut = GRADIENTS.get(grad_name)
                if lut is not None:
                    pil_img = PilImage.fromarray(
                        _lut_pixels(lut, arr8, grad_name).astype(np.uint8), mode="RGB")
                else:
                    pil_img = PilImage.fromarray(arr8, "L").convert("RGB")
                raw = bytes(pil_img.tobytes("raw", "RGB"))
                w2, h2 = pil_img.size
                qimg = QImage(raw, w2, h2, w2 * 3, QImage.Format.Format_RGB888)
                pm = QPixmap.fromImage(qimg)
                # Emit Signal — guaranteed delivery on main thread
                sig.ready.emit(pm, gen)
            except Exception as _e:
                import traceback as _tb
                self._log_safe(f"PREVIEW ERROR: {_e}\n{_tb.format_exc()}")

        threading.Thread(target=_load, daemon=True).start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The picture lives in the close-up window now, which has its own resize
        # handler; re-fitting on the tab's resize would render for nothing.
        if self._preview_paths and self._frame_window_is_open():
            QTimer.singleShot(50, self._preview_show)

    def _capture_selection_state(self) -> dict[str, int]:
        """Save the picked cameras as {folder_name: qty} so a re-scan (another day,
        another hour) comes back with the same cameras picked."""
        return {c["name"]: int(c.get("qty", 1)) for c in self._cams if c.get("checked")}

    def _refresh_selected_table(self):
        """Fill the short list of picked cameras under the Cameras button."""
        self._sel_table.setRowCount(0)
        rows = [(c.get("num", ""), c.get("label") or c["name"], c["name"])
                for c in self._picked_cams()]
        rows.sort(key=lambda t: (t[0].lower(), t[1].lower(), t[2].lower()))
        for num, label, name in rows:
            row = self._sel_table.rowCount(); self._sel_table.insertRow(row)
            self._sel_table.setItem(row, 0, QTableWidgetItem(num))
            it = QTableWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, name)
            self._sel_table.setItem(row, 1, it)
        self._sync_cameras_button()

    def _sel_table_name(self, row: int) -> str:
        it = self._sel_table.item(row, 1)
        return "" if it is None else (it.data(Qt.ItemDataRole.UserRole) or "")

    def _on_sel_table_clicked(self, index):
        """Single click in the picked list → preview that camera's first frame."""
        cam = self._cam_by_name(self._sel_table_name(index.row()))
        if cam is not None:
            # The frame is shown in the close-up window, so the click has to open it —
            # otherwise the list says "click to preview" and nothing appears.
            self._show_frame_window()
            self._preview_load_cam(cam)

    def _on_sel_table_double_clicked(self, index):
        """Double-click in the picked list → unpick that camera."""
        cam = self._cam_by_name(self._sel_table_name(index.row()))
        if cam is not None:
            cam["checked"] = False
            self._refresh_selected_table()

    # ── EVENTS ────────────────────────────────────────────────────────────────
    def _effective_days(self) -> "list[QDate]":
        """The days actually used (camera-table union + Load data) — exactly what
        was picked in the Time window. Weekends only appear if they were added."""
        return list(self._selected_days)

    def _day_window(self, day) -> "tuple[int, int, int, int]":
        """(h_from, m_from, h_to, m_to) picked for one day, in Prague time."""
        for s in self._segments:
            if s.date == day:
                return (s.h_from, s.m_from, s.h_to, s.m_to)
        return daypicker.default_window_for(day)

    def _auto_select_today(self):
        """Called once after startup — act on the day the tab opened on."""
        self._user_has_selected_day = True
        d = self._primary_day()
        self._log(f"Auto-selecting today: {d.strftime('%d.%m.%Y')}")
        self._sync_time_summary()
        self._schedule_autoload(150)

    def _on_gradient_changed(self, name: str):
        # The directory-listing cache used to be cleared here too. A palette has
        # nothing to do with which files are in a folder, and throwing the listing
        # away cost a re-read of the share for nothing.
        self._log(f"GRADIENT -> {name}")
        self._sync_wall_display()
        if self._preview_paths:
            self._preview_show()

    def _sync_bc_enabled(self):
        """Enable/disable the Contrast, Brightness and Gamma controls. Every
        enable/disable of them goes through here, so the rules cannot overwrite
        each other:
          - Auto on → that row's own slider and reset button are greyed out (the
            app-wide 'checkbox beats slider' rule).
          - Either Auto contrast or Auto brightness on → the whole Gamma row goes dead.
            Both of them set the frame's two ends themselves, so gamma has nothing left
            to bend and the render drops it (see img_scale.to_u8). The Slider tab keeps
            its gamma row alive under Auto brightness because its subtraction mode still
            uses it; this tab has no such mode, so a live-but-ignored slider would just
            be a lie.
        The name labels and the numeric readouts stay live either way: while Auto is on,
        the readout is exactly what the user wants to see — the value Auto picked, put
        there by _park_auto_bc."""
        c_live = not self._cb_auto_stretch.isChecked()
        b_live = not self._cb_bright_auto.isChecked()
        self._contrast_slider.setEnabled(c_live)
        self._btn_contrast_reset.setEnabled(c_live)
        self._bright_slider.setEnabled(b_live)
        self._btn_bright_reset.setEnabled(b_live)
        gamma_usable = c_live and b_live
        g_live = gamma_usable and not self._cb_gamma_auto.isChecked()
        self._cb_gamma_auto.setEnabled(gamma_usable)
        self._lbl_gamma.setEnabled(gamma_usable)
        self._lbl_gamma_val.setEnabled(gamma_usable)
        self._gamma_slider.setEnabled(g_live)
        self._btn_gamma_reset.setEnabled(g_live)

    def _bc_args(self):
        """(auto, gamma, contrast, offset) for the render — read on the MAIN thread and
        passed into the workers; never read a widget from one.

        Honours the 'Auto checkbox overrides its own slider' rule of each pair, exactly
        as the Slider tab does: Auto contrast zeroes the manual contrast, Auto brightness
        zeroes the manual offset, and each leaves the other one live. Both Autos are the
        same percentile pass (see img_scale.stretch_u8), so `auto` is set by either and
        ticking both does not level the frame twice."""
        auto_c = self._cb_auto_stretch.isChecked()
        auto_b = self._cb_bright_auto.isChecked()
        auto = auto_c or auto_b
        if auto:
            gamma = img_scale.GAMMA_SLIDER_NEUTRAL
        elif self._cb_gamma_auto.isChecked():
            gamma = img_scale.GAMMA_SLIDER_AUTO
        else:
            gamma = int(self._gamma_slider.value())
        contrast = 0 if auto_c else int(self._contrast_slider.value())
        offset = 0 if auto_b else int(self._bright_slider.value())
        return auto, gamma, contrast, offset

    def _sync_bc_value_labels(self):
        """Write the three numeric readouts from the sliders themselves.

        The sliders are the single source of truth for what is on screen — Auto
        included, because the Auto pass parks its own value there (_park_auto_bc). That
        parking blocks signals, so the handlers do not run and this is called instead."""
        self._lbl_contrast_val.setText(str(int(self._contrast_slider.value())))
        self._lbl_bright_val.setText(str(int(self._bright_slider.value())))
        self._lbl_gamma_val.setText(
            f"{img_scale.gamma_from_slider(int(self._gamma_slider.value())):.2f}")

    def _park_auto_bc(self, applied: "dict | None"):
        """Park each greyed-out slider on the value its Auto pass actually applied to the
        previewed frame, instead of leaving it at 0 while the picture is clearly changed.
        Signals are blocked: no reload.

        DISPLAY ONLY. Unticking an Auto box restores the user's own value instead of
        keeping what was parked (see the toggle handlers) — an Auto checkbox has to be
        undoable, and the parked number is Auto's, not the user's."""
        if not applied:
            return
        c = applied.get("contrast")
        if c is not None and self._cb_auto_stretch.isChecked():
            self._contrast_slider.blockSignals(True)
            self._contrast_slider.setValue(int(c))
            self._contrast_slider.blockSignals(False)
        o = applied.get("offset")
        if o is not None and self._cb_bright_auto.isChecked():
            self._bright_slider.blockSignals(True)
            self._bright_slider.setValue(int(o))
            self._bright_slider.blockSignals(False)
        g = applied.get("gamma")
        if g is not None and self._cb_gamma_auto.isChecked():
            self._gamma_slider.blockSignals(True)
            self._gamma_slider.setValue(img_scale.slider_from_gamma(float(g)))
            self._gamma_slider.blockSignals(False)
        self._sync_bc_value_labels()

    def _bc_reshow_preview(self):
        # The one funnel for every Contrast / Brightness / Gamma change — the three
        # sliders debounce into it and all three Auto boxes call it — so the wall is
        # kept in step from here rather than from six separate handlers.
        self._sync_wall_display()
        if self._preview_paths:
            self._preview_show()

    def _on_contrast_slider_changed(self, value: int):
        # Only user moves reach this (_park_auto_bc blocks signals), so this is the value
        # to come back to when Auto is switched off.
        self._contrast_manual = int(value)
        self._lbl_contrast_val.setText(str(int(value)))
        self._bc_debounce.start()

    def _reset_contrast_slider(self):
        self._contrast_slider.setValue(0)

    def _on_auto_stretch_toggled(self, on: bool):
        self._log(f"CONTRAST -> {'auto stretch (per frame)' if on else 'absolute'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → the slider is live again, so it must not be left on the number
            # Auto parked there: the stretch of a dim frame needs a gain the slider
            # cannot reach, so the parked value saturates and does not reproduce the
            # picture.
            self._contrast_slider.blockSignals(True)
            self._contrast_slider.setValue(int(self._contrast_manual))
            self._contrast_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    def _on_bright_slider_changed(self, value: int):
        self._bright_manual = int(value)
        self._lbl_bright_val.setText(str(int(value)))
        self._bc_debounce.start()

    def _reset_bright_slider(self):
        self._bright_slider.setValue(0)

    def _on_bright_auto_toggled(self, on: bool):
        self._log(f"BRIGHTNESS -> {'auto level (per frame)' if on else 'manual'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → back to the user's own offset, not the black level Auto parked
            # on the greyed-out slider. Same rule as contrast: an Auto checkbox has to be
            # undoable.
            self._bright_slider.blockSignals(True)
            self._bright_slider.setValue(int(self._bright_manual))
            self._bright_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    def _on_gamma_slider_changed(self, value: int):
        self._gamma_manual = int(value)
        self._lbl_gamma_val.setText(f"{img_scale.gamma_from_slider(value):.2f}")
        self._bc_debounce.start()

    def _reset_gamma_slider(self):
        self._gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)

    def _on_gamma_auto_toggled(self, on: bool):
        self._log(f"GAMMA -> {'auto (per frame)' if on else 'manual'}")
        self._sync_bc_enabled()
        if not on:
            # Auto off → the user's own value, not the one Auto parked on the greyed-out
            # slider. An Auto checkbox has to be undoable.
            self._gamma_slider.blockSignals(True)
            self._gamma_slider.setValue(int(self._gamma_manual))
            self._gamma_slider.blockSignals(False)
        self._sync_bc_value_labels()
        self._bc_reshow_preview()

    # ── ENERGY CSV METHODS ────────────────────────────────────────────────────

    def _pick_energy_columns(self):
        """The Image Slider's PV picker — one dialog, one registry, for both tabs.

        What comes back is split in two: the REGISTRY (added PVs, formulas, names,
        units) is shared and goes to the shared store, while the selection and the eye
        state belong to this tab alone."""
        sl = _get_slider_module()
        dlg = sl.PvConfigDialog(self._energy_selected_cols, sl.PV_CUSTOM_CHANNELS,
                               sl.PV_DERIVED, sl.PV_LABELS,
                               hidden=self._energy_hidden_pvs,
                               units=sl.PV_CUSTOM_UNITS,
                               # The alarm limits are part of the shared registry, so
                               # they have to be handed in and taken back out here too.
                               # Opening this picker with them missing would show empty
                               # Min/Max boxes for PVs that DO have a limit, and wipe
                               # them on OK.
                               limits=sl.PV_LIMITS, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sl.PV_CUSTOM_CHANNELS.clear()
        sl.PV_CUSTOM_CHANNELS.update(dlg.custom_channels())
        sl.PV_DERIVED[:] = dlg.derived_defs()
        sl.PV_LABELS.clear()
        sl.PV_LABELS.update(dlg.labels())
        sl.PV_CUSTOM_UNITS.clear()
        sl.PV_CUSTOM_UNITS.update(dlg.custom_units())
        sl.PV_LIMITS.clear()
        sl.PV_LIMITS.update(dlg.limits())
        sl.pv_registry_save()
        self._energy_selected_cols = dlg.selected_names()
        self._energy_hidden_pvs = (set(dlg.hidden_names())
                                   & set(self._energy_selected_cols))
        self._save_pv_state()
        self._log(f"PVs: {self._energy_selected_cols}")
        # The cached days were fetched for the PREVIOUS list, so a PV added just now
        # would read "n/a" until the day happened to be re-fetched for another reason.
        self._energy_cache.clear()
        self._energy_per_col_cache.clear()
        if hasattr(self, "_energy_cache_time"):
            self._energy_cache_time.clear()
        if hasattr(self, "_energy_error_days"):
            self._energy_error_days.clear()
        self._pv_refresh_table()
        if self._energy_results:
            self._refresh_energy_info()

    # ── the PV list: selection, eye, saved state ──────────────────────────
    def _pv_visible_cols(self) -> "list[str]":
        """The picked PVs whose value is PRINTED — in the info panel, in the bar under
        the preview and in a burned-in image. Everything picked is still read; the eye
        only decides what is shown, exactly as in the Slider."""
        return [c for c in self._energy_selected_cols
                if c not in self._energy_hidden_pvs]

    def _pv_fetch_cols(self) -> "list[str]":
        """What has to be READ for the picked list: every picked PV that is a channel,
        plus the sources of every picked formula — a formula built on a PV that is not
        itself picked would otherwise read n/a."""
        sl = _get_slider_module()
        out = [c for c in self._energy_selected_cols if not sl.pv_is_derived(c)]
        for src in sl.pv_source_names(list(self._energy_selected_cols)):
            if src not in out:
                out.append(src)
        return out

    def _pv_toggle_eye(self, name: str):
        """Eye column of the PV table: take this PV off the picture (or put it back).
        It keeps being read and stays listed either way."""
        if name not in self._energy_selected_cols:
            return
        if name in self._energy_hidden_pvs:
            self._energy_hidden_pvs.discard(name)
        else:
            self._energy_hidden_pvs.add(name)
        self._save_pv_state()
        self._pv_refresh_table()
        # The bar under the preview follows the eye, so the frame has to be repainted.
        if self._preview_paths:
            self._preview_show()

    def _current_preview_path(self) -> "Path | None":
        """The frame the preview is showing, or None."""
        paths = getattr(self, "_preview_paths", None) or []
        idx = getattr(self, "_preview_idx", 0)
        return paths[idx] if 0 <= idx < len(paths) else None

    def _pv_refresh_table(self):
        """Repaint the PV table for the frame currently previewed."""
        tbl = getattr(self, "_pv_table", None)
        if tbl is None:
            return
        has = bool(self._energy_selected_cols)
        tbl.setVisible(has)
        self._pv_no_pv_lbl.setVisible(not has)
        if not has:
            return
        vals: dict = {}
        path = self._current_preview_path()
        entry = self._energy_entry_for_path(path) if path is not None else None
        if entry is not None:
            img_ns = extract_ns_from_stem(path.stem) or 0
            per_col = entry[6] if len(entry) > 6 else {}
            match = entry[1] if len(entry) > 1 else None
            for col, raw, state in self._pv_values_for_ns(
                    img_ns, self._energy_selected_cols, per_col, match=match,
                    allow_network=False):
                vals[col] = self._format_pv_state(col, raw, state)

        def _value_of(name: str):
            txt = vals.get(name)
            if txt is None:
                # No frame previewed yet, or this PV was not resolved for it — say
                # "not read yet" in grey rather than printing a bare dash that reads
                # like a PV with no data.
                return "…", True, ("No frame selected yet — pick a result row."
                                   if entry is None else
                                   "Not resolved for this frame.")
            grey = txt in (cpva.PV_TEXT_NOT_FOUND, cpva.PV_TEXT_ERROR, "—")
            tip = ""
            if txt == cpva.PV_TEXT_ERROR:
                tip = ("The archiver fetch failed for this PV. It is retried on the "
                       "next lookup.")
            elif txt == cpva.PV_TEXT_NOT_FOUND:
                tip = ("Nothing was archived near this frame's time for this PV "
                       "(the day loaded fine).")
            return txt, grey, tip

        tbl.refresh(self._energy_selected_cols, self._energy_hidden_pvs, _value_of)

    _PV_STATE_PATH = (Path(os.environ.get("APPDATA", Path.home()))
                      / "ELI_ImageTools" / "finder_ui_state.json")

    def _load_pv_state(self):
        """This tab's own selection. The PVs themselves come from the shared registry
        (see is_t.pv_registry_load) — what is stored here is only WHICH of them this
        tab shows, and which of those are off the picture."""
        sl = _get_slider_module()
        sl.pv_registry_load()
        try:
            data = json.loads(self._PV_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        hid = data.get("pv_hidden")
        if isinstance(hid, list):
            self._energy_hidden_pvs = {str(n) for n in hid}
        sel = data.get("pv_selected")
        if isinstance(sel, list):
            # A recipe preset saved by an older version (the "Compressed SBW4"
            # channel) becomes its source PV plus the formula that converts it. Before
            # the filter: the recipe name is no longer a channel, so `known` would drop
            # it first. The eye set is handed in because the recipe takes its source
            # off the picture.
            sel = sl.pv_migrate_preset_recipes([str(n) for n in sel],
                                               self._energy_hidden_pvs)
            # Iterating the SAVED list keeps the operator's order; the filter drops a
            # PV that no longer exists, so a removed channel cannot come back.
            known = set(sl.pv_all_names()) | set(ENERGY_COLUMNS_AVAILABLE)
            self._energy_selected_cols = [n for n in sel if n in known]
        self._energy_hidden_pvs &= set(self._energy_selected_cols)

    def _read_ui_state_file(self) -> dict:
        try:
            data = json.loads(self._PV_STATE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_ui_state_file(self, updates: dict):
        """Merge into the state file rather than replacing it. The PV selection and the
        open/closed panel groups share one document, so a writer that rewrites the whole
        thing silently throws away the other one's half."""
        data = self._read_ui_state_file()
        data.update(updates)
        try:
            self._PV_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PV_STATE_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_ui_state(self) -> dict:
        """Which panel groups were left open. Read before the panel is built, so it must
        not touch any widget."""
        return self._read_ui_state_file()

    def _on_section_toggled(self, key: str, expanded: bool):
        self._ui_state[f"sec_{key}"] = bool(expanded)
        self._write_ui_state_file({f"sec_{key}": bool(expanded)})

    def _set_all_sections(self, expanded: bool):
        """Expand all / Collapse all — one write for the whole panel."""
        upd = {}
        for key, sec in getattr(self, "_sections", {}).items():
            sec.set_expanded(expanded)
            self._ui_state[f"sec_{key}"] = bool(expanded)
            upd[f"sec_{key}"] = bool(expanded)
        self._write_ui_state_file(upd)

    def _save_pv_state(self):
        self._write_ui_state_file({"pv_selected": list(self._energy_selected_cols),
                                   "pv_hidden": sorted(self._energy_hidden_pvs)})

    def _get_energy_rows_for_dt(self, dt: datetime) -> list[_EnergyRow]:
        """
        Return cached energy rows for the date of dt.
        Tries CPVA API first; falls back to CSV. Called from background thread.
        Also populates self._energy_per_col_cache for per-column closest-timestamp lookups.
        """
        day_key = dt.strftime("%Y-%m-%d")
        today_key = datetime.now(PRAGUE).strftime("%Y-%m-%d") if PRAGUE else \
            datetime.utcnow().strftime("%Y-%m-%d")
        if not hasattr(self, "_energy_cache_time"):
            self._energy_cache_time = {}     # day_key → time.monotonic() of fetch
        if not hasattr(self, "_energy_error_days"):
            self._energy_error_days = set()  # day_keys whose last fetch had a failure
        if day_key in self._energy_cache and day_key not in self._energy_error_days:
            # Past days are immutable; TODAY keeps filling in — refetch after a
            # short TTL (cheap: the shared client's day cache does the real work,
            # only today's channels are actually re-hit).
            if day_key != today_key:
                return self._energy_cache[day_key]
            age = time.monotonic() - self._energy_cache_time.get(day_key, 0.0)
            if age < 30.0:
                return self._energy_cache[day_key]
        # Read the sources of the picked formulas too — see _pv_fetch_cols.
        cols = self._pv_fetch_cols()
        if cols:
            rows, per_col, had_error = _energy_api_for_day(dt, cols, log=self._log_safe)
            if had_error:
                # A fetch FAILED (≠ "day has no data"). Show what we got, but mark
                # the day for retry — caching the gap silently meant one outage
                # showed "—" for the whole session. Retry only re-hits the failed
                # channel (successful ones are served from the shared day cache).
                self._energy_error_days.add(day_key)
                self._log_safe(f"ENERGY: API fetch FAILED for {day_key} — will retry on next lookup")
            else:
                self._energy_error_days.discard(day_key)
            if rows:
                self._log_safe(f"ENERGY: {len(rows)} rows via API for {day_key}")
                self._energy_cache[day_key] = rows
                self._energy_per_col_cache[day_key] = per_col
                self._energy_cache_time[day_key] = time.monotonic()
                return rows
            if had_error:
                return self._energy_cache.get(day_key, [])
        # CSV fallback
        csv_path = _energy_csv_path(dt)
        rows = _load_energy_csv(csv_path)
        self._energy_cache[day_key] = rows
        self._energy_cache_time[day_key] = time.monotonic()
        # Build the per-column closest-timestamp lookup from the CSV rows so that
        # _find_closest_per_col_value works identically for CSV and API data.
        self._energy_per_col_cache[day_key] = _build_per_col_from_rows(rows)
        if rows:
            self._log_safe(f"ENERGY: {len(rows)} rows from CSV {csv_path.name}")
        else:
            self._log_safe(f"ENERGY: no data (API + CSV) for {day_key}")
        return rows

    def _lookup_energy_for_files(self, files: list[Path]) -> list[tuple]:
        """
        For each file, look up a matching CSV row.
        Returns list of (path, match, before, after, csv_rows, match_idx, per_col) tuples.
        csv_rows: full list of _EnergyRow for that day
        match_idx: index in csv_rows of the matched row (or nearest before), or None
        per_col: dict[str, list[_EnergyRow]] mapping each column to sorted per-column rows
        """
        results = []
        for path in files:
            ns = extract_ns_from_stem(path.stem)
            if ns is None:
                results.append((path, None, None, None, [], None, {}))
                continue
            dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc)
            if PRAGUE:
                dt = dt.astimezone(PRAGUE).replace(tzinfo=None)
            else:
                dt = dt.replace(tzinfo=None)
            rows = self._get_energy_rows_for_dt(dt)
            day_key = dt.strftime("%Y-%m-%d")
            per_col = self._energy_per_col_cache.get(day_key, {})
            match, before, after = _find_energy_match(rows, ns)

            # Find match_idx — index of matched row, or index of 'before' row
            match_idx = None
            if match is not None and rows:
                try:
                    match_idx = rows.index(match)
                except ValueError:
                    pass
            elif before is not None and rows:
                try:
                    match_idx = rows.index(before)
                except ValueError:
                    pass

            # Warm the slow-PV look-back cache HERE (background thread) so the
            # UI-thread display path (allow_network=False) gets cache hits for
            # waveplate-like channels whose last sample is hours/days old.
            try:
                self._pv_values_for_ns(ns, self._energy_selected_cols, per_col,
                                       match=match, allow_network=True)
            except Exception:
                pass

            results.append((path, match, before, after, rows, match_idx, per_col))
        return results

    def _pv_values_for_ns(self, img_ns: int, cols: "list[str]",
                          per_col: dict, match=None,
                          allow_network: bool = False
                          ) -> "list[tuple[str, str, str]]":
        """
        THE single matching implementation: resolve each column's value at the
        image timestamp. Returns [(col, raw, state)] with state:
          "ok"        — per-column sample within tolerance (or merged-row /
                        slow-PV look-back hit); raw is a valid value string
          "error"     — archiver fetch failed (display "ERR")
          "not_found" — data loaded fine, nothing matches (display "n/a")

        Per-column nearest match first (30 s API / 120 s CSV window), then the
        merged row, then — for slow PVs only (waveplate: archived on-change, so
        the last sample can be days old) — the archiver's last-at-or-before
        look-back. allow_network=False (UI thread) still serves look-back
        cache hits.
        """
        sl = _get_slider_module()
        want = list(cols)
        # A formula is COMPUTED, so it is never looked up here — but its sources are,
        # whether or not they are picked themselves.
        read_names = [c for c in want if not sl.pv_is_derived(c)]
        for src in sl.pv_source_names(want):
            if src not in read_names:
                read_names.append(src)
        resolved: dict = {}
        for col in read_names:
            # API rows are per-shot → tight window; sparse CSV keeps the wide one.
            tol = (ENERGY_MATCH_TOL_API_S if per_col.get(f"_src:{col}") == "api"
                   else ENERGY_MATCH_TOL_S)
            raw_val = _find_closest_per_col_value(per_col, col, img_ns, tol_s=tol)
            if raw_val and raw_val != "—":
                resolved[col] = (raw_val, "ok")
                continue
            if match is not None:
                mv = match.values.get(col, "")
                if mv and mv != "—":
                    resolved[col] = (mv, "ok")
                    continue
            channel = _pv_channel_for(col)
            if channel and cpva.is_step_channel(channel, int(img_ns),
                                                network_ok=allow_network):
                res = cpva.value_at_or_before(channel, int(img_ns),
                                              timeout=CPVA_HTTP_TIMEOUT,
                                              network_ok=allow_network)
                if res.value is not None:
                    resolved[col] = (str(res.value), "ok")
                    continue
                if res.status == "error":
                    resolved[col] = ("", "error")
                    continue
            resolved[col] = ("", "not_found")

        # Formulas, evaluated in definition order from the numbers just resolved —
        # the SAME evaluator the Slider uses (pv_eval_derived), so a formula cannot
        # mean one thing here and another there. It wants values with the registry's
        # own factor already applied.
        if any(sl.pv_is_derived(c) for c in want):
            raw_num: dict = {}
            statuses: dict = {}
            for _n, (_rw, _st) in resolved.items():
                try:
                    raw_num[_n] = float(_rw) * _pv_scale_for(_n)
                except (TypeError, ValueError):
                    raw_num[_n] = None
                statuses[_n] = _st
            for _n, (_val, _st) in sl.pv_eval_derived(want, raw_num, statuses).items():
                resolved[_n] = ("" if _val is None else f"{_val}", _st)

        # Answer in the order that was ASKED for, and only for what was asked: a
        # formula's sources were read as a means, not because the caller wants them
        # printed.
        return [(c, *resolved.get(c, ("", "not_found"))) for c in want]

    @staticmethod
    def _format_pv_state(col: str, raw: str, state: str) -> str:
        """Tri-state display: real value (incl. genuine 0) / "ERR" / "n/a"."""
        if state == "error":
            return cpva.PV_TEXT_ERROR
        if state != "ok" or raw == "":
            return cpva.PV_TEXT_NOT_FOUND
        return _format_energy_value(col, raw)

    def _energy_parts_for_path(self, path: Path, entry: tuple) -> list[str]:
        """
        Return ['<label>=<value>', ...] for the selected PV columns of one image.
        Delegates to _pv_values_for_ns — single source of truth for the info
        panel, the preview overlay and saves.
        """
        match    = entry[1] if len(entry) > 1 else None
        per_col  = entry[6] if len(entry) > 6 else {}
        img_ns   = extract_ns_from_stem(path.stem) or 0
        parts: list[str] = []
        for col, raw, state in self._pv_values_for_ns(
                img_ns, self._pv_visible_cols(), per_col, match=match,
                allow_network=False):
            val   = self._format_pv_state(col, raw, state)
            parts.append(f"{_pv_label_for(col)}={val}")
        return parts

    def _energy_entry_for_path(self, path: Path) -> "tuple | None":
        """Return the energy-results entry (path, match, …, per_col) for a file."""
        for entry in (self._energy_results or []):
            if entry and entry[0] == path:
                return entry
        return None

    def _on_pv_preview_toggle(self):
        """Re-render the current preview so the PV bar appears/disappears."""
        if self._preview_paths:
            self._preview_show()

    def _refresh_energy_info(self):
        """Reset navigation to first result and display it."""
        self._energy_nav_index = 0
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()
        # PV values may now be available — repaint the preview bar.
        if self._preview_paths:
            self._preview_show()

    def _on_nav_mode_changed(self):
        """Reset CSV offset when switching navigation mode."""
        self._energy_csv_offset = 0
        self._energy_csv_anchor_idx = None
        self._energy_csv_anchor_rows = []
        self._refresh_energy_info_single()

    def _energy_nav_prev(self):
        if not self._energy_results:
            return
        if not self._cb_nav_images.isChecked():
            # Step-through-images mode (checkbox OFF = default)
            if self._energy_nav_index > 0:
                self._energy_nav_index -= 1
                self._energy_csv_offset = 0
                self._energy_csv_anchor_idx = None
                self._energy_csv_anchor_rows = []
        else:
            # CSV navigation mode — move through CSV rows around current image
            self._energy_csv_offset -= 1
        self._refresh_energy_info_single()

    def _energy_nav_next(self):
        if not self._energy_results:
            return
        if not self._cb_nav_images.isChecked():
            # Step-through-images mode (checkbox OFF = default)
            if self._energy_nav_index < len(self._energy_results) - 1:
                self._energy_nav_index += 1
                self._energy_csv_offset = 0
                self._energy_csv_anchor_idx = None
                self._energy_csv_anchor_rows = []
        else:
            # CSV navigation mode — move through CSV rows around current image
            self._energy_csv_offset += 1
        self._refresh_energy_info_single()

    def _refresh_energy_info_single(self):
        if not self._energy_results:
            self._energy_info.setPlainText("")
            self._energy_nav_lbl.setText("0 / 0")
            self._btn_energy_prev.setEnabled(False)
            self._btn_energy_next.setEnabled(False)
            return

        n = len(self._energy_results)
        idx = max(0, min(self._energy_nav_index, n - 1))
        self._energy_nav_index = idx

        entry = self._energy_results[idx]
        path  = entry[0]
        match = entry[1]
        before = entry[2]
        after  = entry[3]
        csv_rows: list = entry[4] if len(entry) > 4 else []
        match_idx = entry[5] if len(entry) > 5 else None
        per_col_for_entry: dict = entry[6] if len(entry) > 6 else {}

        # Set anchor when entering CSV mode or switching images
        if self._energy_csv_anchor_idx is None or self._energy_csv_anchor_rows is not csv_rows:
            self._energy_csv_anchor_idx = match_idx
            self._energy_csv_anchor_rows = csv_rows

        nav_images = not self._cb_nav_images.isChecked()  # checkbox OFF = step images
        csv_offset = self._energy_csv_offset
        base_idx   = self._energy_csv_anchor_idx

        # ── Navigation label ──────────────────────────────────────────────
        if nav_images or csv_offset == 0:
            self._energy_nav_lbl.setText(f"📷 {idx + 1} / {n}")
        elif csv_offset < 0:
            self._energy_nav_lbl.setText(f"◄ {abs(csv_offset)} before #{idx + 1}")
        else:
            self._energy_nav_lbl.setText(f"► {csv_offset} after #{idx + 1}")

        # ── Button enable/disable ─────────────────────────────────────────
        if nav_images:
            self._btn_energy_prev.setEnabled(idx > 0)
            self._btn_energy_next.setEnabled(idx < n - 1)
        else:
            can_prev = base_idx is None or (base_idx + csv_offset - 1) >= 0
            can_next = (base_idx is None or not csv_rows or
                        (base_idx + csv_offset + 1) < len(csv_rows))
            self._btn_energy_prev.setEnabled(can_prev)
            self._btn_energy_next.setEnabled(can_next)

        lines = []

        if not nav_images and csv_offset != 0 and base_idx is not None and csv_rows:
            # ── CSV browse mode — show a neighbouring CSV row (by time) ────
            target_csv_idx = base_idx + csv_offset
            if 0 <= target_csv_idx < len(csv_rows):
                row = csv_rows[target_csv_idx]
                parts = []
                for col in self._pv_visible_cols():
                    val   = _format_energy_value(col, row.values.get(col, "—"))
                    parts.append(f"{_pv_label_for(col)}={val}")
                ts = row.ts_dt.strftime("%H:%M:%S.%f")[:-3]
                lines.append(f"{ts}  (CSV row)\n  " + "  |  ".join(parts))
            else:
                lines.append("(no more CSV rows)")

        else:
            # ── Current image — PV values only ────────────────────────────
            parts = self._energy_parts_for_path(path, entry)
            lines.append("  |  ".join(parts) if parts else "(no PVs selected)")

        self._energy_info.setPlainText("\n\n".join(lines))

    # ── RAMPING CSV ───────────────────────────────────────────────────────────
    # The RAMPING CSV reader lived here (_ensure_ramping_root,
    # _read_ramping_csv_rows, _get_ramping_for_day_cached). Its only consumer was
    # the auto-hour below, and Salvation stopped writing the file on 26.05.2026,
    # so it had been reading an empty share for months.

    # The automatic "strongest hour of the day" lived here — a PV probe, a
    # best-block scan over the RAMPING CSV and a 14:00 fallback. The CSV stopped
    # being written on 26.05.2026, so the probe found nothing on every recent day
    # and the fallback took over; 14:00 was measurably the THINNEST hour on
    # 06.08.2026 (4540 frames against 11985 at 13:00). Guessing badly is worse
    # than not guessing: the window is picked in the Time window dialog, and
    # finding the moment inside it is what the PV search is for.

    # ── DATETIME / PATH LOGIC ─────────────────────────────────────────────────
    def _build_datetime(self, day=None, hour: "int | None" = None) -> datetime:
        """Prague wall time → the UTC hour the archive folder is named after.

        The archive tree is UTC and the numbers in it are unpadded; everything a
        user sees here is Prague. There is no lab-time mode any more — this tab
        searches in real time, so the conversion is unconditional."""
        day = day or self._primary_day()
        if hour is None:
            hour = self._day_window(day)[0]
        dt_real = datetime(day.year, day.month, day.day, hour, 0, 0)
        if PRAGUE is not None:
            dt_prague = datetime(day.year, day.month, day.day, hour, 0, 0,
                                 tzinfo=PRAGUE)
            return dt_real - timedelta(
                hours=int(dt_prague.utcoffset().total_seconds() / 3600))
        return dt_real - timedelta(hours=1)

    def _build_target_path(self, dt: datetime) -> Path:
        year = dt.year
        # Each year lives in its own share: cpva-image-<year> (e.g. 2024 -> cpva-image-2024).
        root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{year}"
        return root / str(year) / str(dt.month) / str(dt.day) / str(dt.hour)

    def _log_selected_datetime_preview(self):
        """Log where the picked window lands in the archive."""
        self._sync_time_summary()
        try:
            day = self._primary_day()
            hf, mf, ht, mt = self._day_window(day)
            target = self._build_target_path(self._build_datetime(day, hf))
            self._log(
                "-------------------------------\n"
                f"Selected - Prague Time:  {day.strftime('%d.%m.%Y')} "
                f"{hf:02d}:{mf:02d}-{ht:02d}:{mt:02d}\n"
                f"Target path:             {target}\n"
            )
        except Exception as e:
            self._log(f"Preview ERROR: {e}")

    # ── LOAD FOLDERS ──────────────────────────────────────────────────────────
    def load_folders(self):
        if not self._user_has_selected_day:
            return

        # Clear the camera list immediately on the main thread (instant)
        self._status_dot.setStyleSheet("color: gray; font-size: 12px;")
        saved_sel = self._capture_selection_state()
        self._cams = []
        self._refresh_selected_table()
        self._load_gen += 1
        current_gen = self._load_gen; self.primary_files = []

        dt          = self._build_datetime()
        target_path = self._build_target_path(dt)
        # Camera table = union of cameras across ALL effective (weekday-filtered)
        # selected days. A day with fewer cameras no longer hides cameras that
        # appear on other selected days.
        eff_days = [(d.year(), d.month(), d.day()) for d in self._effective_days()]
        if not eff_days:
            eff_days = [(dt.year, dt.month, dt.day)]
        self._log(f"Load -> {len(eff_days)} day(s) | primary target={target_path}")

        self._load_sig = _LoadSignals()
        self._load_sig.done.connect(self._on_load_done)
        self._load_sig.not_found.connect(self._on_load_not_found)
        self._load_sig.error.connect(self._on_load_error)
        self._load_sig.log_msg.connect(self._log)
        _sig = self._load_sig  # local ref — prevents GC if load_folders() called again

        def worker():
            try:
                # Collect camera folders from ALL hours of EVERY effective day.
                # Cameras are deduplicated by folder name across days/hours.
                seen: set[str] = set()
                all_entries: list[Path] = []
                found_any_hour = False

                def _scan_hour(day_dir: Path, h: int) -> tuple[bool, list[Path]]:
                    hour_dir = day_dir / str(h)
                    try:
                        if not hour_dir.exists() or not hour_dir.is_dir():
                            return False, []
                    except Exception:
                        return False, []
                    entries: list[Path] = []
                    try:
                        for e in os.scandir(hour_dir):
                            if e.is_dir():
                                entries.append(Path(e.path))
                    except Exception:
                        pass
                    return True, entries

                for (yy, mm, dd) in eff_days:
                    # Each year lives in its own share: cpva-image-<year>.
                    root = Path(IMAGES_ROOT_BASE) / f"cpva-image-{yy}"
                    day_dir = root / str(yy) / str(mm) / str(dd)
                    with ThreadPoolExecutor(max_workers=12) as _hex:
                        _futs = [_hex.submit(_scan_hour, day_dir, h) for h in range(24)]
                        for _fut in as_completed(_futs):
                            try:
                                _existed, _entries = _fut.result()
                                if _existed:
                                    found_any_hour = True
                                for _p in _entries:
                                    if _p.name not in seen:
                                        seen.add(_p.name)
                                        all_entries.append(_p)
                            except Exception:
                                pass

                if not found_any_hour:
                    _sig.not_found.emit(target_path)
                    return

                subfolders = sorted(all_entries, key=lambda x: x.name.lower())
                _sig.log_msg.emit(f"LOAD: scanned {len(eff_days)} day(s), found {len(subfolders)} cameras")
                _sig.done.emit(subfolders, saved_sel, current_gen)
            except Exception as e:
                _sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()


    def _on_load_not_found(self, target_path: Path):
        if not self.isVisible():
            return
        self._log(f"Target folder not found: {target_path}")
        resp = QMessageBox.question(
            self, "Not found",
            f"Folder does not exist:\n{target_path}\n\n"
            "Search by PV region instead? (Plot a PV, mark time regions, and "
            "pull frames from the peak of each region.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        self._refresh_selected_table()
        if resp == QMessageBox.StandardButton.Yes:
            self._open_pv_region_search()

    def _on_load_error(self, err: str):
        if not self.isVisible():
            return
        self._log(f"ERROR: {err}")
        QMessageBox.critical(self, "Error", err)

    def _on_load_done(self, subfolders: list[Path], saved_sel: dict[str, int], gen: int):
        """Called on main thread after background scan — fills the camera list."""
        self._log(f"_on_load_done: start, {len(subfolders)} folders")
        if gen != self._load_gen:
            self._log(f"_on_load_done: ignoring stale load (gen {gen} != {self._load_gen})")
            return
        try:
            # Count how many hours each camera label appears in (for disambiguation)
            label_count: dict[str, int] = {}
            for p in subfolders:
                lbl = extract_display_label(p.name)
                label_count[lbl] = label_count.get(lbl, 0) + 1

            seen: dict[str, int] = {}
            cams: list[dict] = []
            for p in subfolders:
                label = extract_display_label(p.name)
                seen[label] = seen.get(label, 0) + 1
                if label_count.get(label, 1) > 1:
                    try:
                        label_display = f"{label} [h{p.parent.name}]"
                    except Exception:
                        label_display = f"{label} ({seen[label]})"
                else:
                    label_display = label
                num = extract_folder_number(p.name)
                try:
                    cam_int = (int(num) if num.isdigit()
                               else int(num[1:]) if num.upper().startswith("C") and num[1:].isdigit()
                               else None)
                    hz33 = "YES" if (cam_int is not None and cam_int in CAM_33HZ) else ""
                except: hz33 = ""
                cams.append({
                    "path":    p,
                    "name":    p.name,
                    "num":     num,
                    "label":   label_display,
                    "hz33":    hz33,
                    "qty":     int(saved_sel.get(p.name, 1)),
                    "checked": p.name in saved_sel,
                })
            self._cams = cams
            self._refresh_selected_table()
            self._log(f"Subfolders loaded: {len(subfolders)}")
            self._status_dot.setStyleSheet("color: green; font-size: 14px;")
            self._sync_time_summary()
        except Exception as e:
            self._log(f"_on_load_done ERROR: {type(e).__name__}: {e}")
            import traceback
            self._log(traceback.format_exc())


    # ── OPEN IN SLIDER / EXPLORER ─────────────────────────────────────────────
    def _send_moment_to_slider(self, with_cameras: bool):
        """The moment on the wall, opened in the Image Slider.

        Two flavours, and the difference matters. "Send moment" carries only the
        TIME: the Slider is usually already set up on the cameras somebody is
        working with, and replacing that pick undoes their work. "Send + cameras"
        carries this tab's camera pick as well, for when the point is to see these
        cameras there.

        The Slider then shows a window around the moment and lands on the frame
        NEAREST it, so the shots either side can be slid through — which is the
        thing this tab cannot do."""
        title = "Send to Image Slider"
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, title, "Image Slider not connected.")
            return
        ts = self._moment_ns
        if ts is None:
            QMessageBox.information(
                self, title,
                "No moment on the wall. Pick one in PV Search first.")
            return
        cams = [c[0] for c in self._checked_cameras()] if with_cameras else None
        if with_cameras and not cams:
            QMessageBox.information(self, title, "No camera picked.")
            return
        try:
            ok = self._slider_ref.open_moment(int(ts), cams)
        except Exception as e:
            self._log(f"SLIDER: open_moment failed — {e}")
            QMessageBox.warning(self, title, f"Could not open the moment:\n{e}")
            return
        if not ok:
            QMessageBox.information(
                self, title,
                "The Slider has no camera to open — send the cameras with it, or "
                "pick some there first.")
            return
        idx = getattr(self, "_slider_tab_idx", None)
        self._tab_widget.setCurrentIndex(1 if idx is None else idx)
        when = datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            when = when.astimezone(PRAGUE)
        self._log(f"SLIDER: moment {when.strftime('%d.%m. %H:%M:%S')}"
                  + (f" with {len(cams)} camera(s)" if cams else " (cameras kept)"))

    def _sync_send_moment_buttons(self):
        """Both greyed for their own reason, each saying which in its tooltip."""
        if not hasattr(self, "_btn_send_moment"):
            return
        have_moment = self._moment_ns is not None
        connected = self._slider_ref is not None and self._tab_widget is not None
        n_cams = len(self._checked_cameras())
        self._btn_send_moment.setEnabled(have_moment and connected)
        self._btn_send_moment_cams.setEnabled(
            have_moment and connected and n_cams > 0)
        why = ("" if connected else "\n\nThe Image Slider is not connected.")
        if not have_moment:
            why += "\n\nNo moment on the wall — pick one in PV Search."
        self._btn_send_moment.setToolTip(
            "Open this moment in the Image Slider and KEEP the cameras picked "
            "there." + why)
        self._btn_send_moment_cams.setToolTip(
            "Open this moment in the Image Slider with THIS tab's cameras."
            + why + ("" if n_cams else "\n\nNo camera picked here."))

    def open_in_slider(self):
        """Hand the Slider the frames that are actually on the wall.

        This used to open the FIRST checked folder WHOLE and log that it was ignoring
        the rest — the tab did all the work of picking a frame per day and then threw
        the selection away at the door. When there is a wall, the picked frames are
        copied to a temp folder and pushed through the Slider's own public handoff
        (`receive_external_folder`), which is the same route the Shot Finder uses and
        which clears a multi-cam grid, an armed live mode, a subtraction reference and
        focus mode first. With no wall it falls back to opening the folder, as before.
        """
        if self._slider_ref is None or self._tab_widget is None:
            QMessageBox.information(self, "Info", "Image Slider not connected."); return

        cells = self._wall.cells() if hasattr(self, "_wall") else []
        cells = [c for c in cells if c.get("path")]
        if cells:
            self._push_cells_to_slider(cells)
            return

        if not self._user_has_selected_day:
            QMessageBox.information(self, "Info", "Select a day first."); return
        jobs    = self._snapshot_collect_jobs()
        folders = [f for f, _ in jobs if f and f.exists() and f.is_dir()]
        if not folders:
            QMessageBox.information(self, "Info", "No cameras selected."); return
        if len(folders) > 1:
            self._log(f"SLIDER: {len(folders)} folders selected, opening FIRST: {folders[0].name}")
        self._tab_widget.setCurrentIndex(1)
        self._slider_ref.open_folder_path(folders[0])
        self._log(f"SLIDER: opened {folders[0].name}")

    def _push_cells_to_slider(self, cells: list):
        """Copy the picked frames into a temp folder and hand them over as a set.

        Each caption is carried NEXT TO its own file rather than looked up by index
        later: a day without a picture would otherwise shift every following caption
        onto the wrong frame (the mistake the Shot Finder's handoff documents)."""
        try:
            root = Path(tempfile.mkdtemp(prefix="IF_slider_"))
            # img_scale.camera_from_path reads the camera off the PARENT FOLDER, so a
            # flat temp folder would strip it and the Slider would fall back to the
            # plain 65535 range — the same frame would come out ~16× darker there than
            # on the wall. Keeping the camera folder name preserves the sensor range,
            # so the handed-over frames look like what was being compared.
            src_folders = {Path(c["path"]).parent.name for c in cells if c.get("path")}
            new_dir = root / src_folders.pop() if len(src_folders) == 1 else root
            new_dir.mkdir(parents=True, exist_ok=True)
            energy_map: dict = {}
            cams = set()
            for c in cells:
                src = Path(c["path"])
                if not src.exists():
                    continue
                dst = new_dir / src.name
                shutil.copy2(src, dst)
                cams.add(c.get("cam", ""))
                day = c.get("day")
                cap = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
                energy_map[dst.name] = f"{c.get('cam', '')}  {cap}".strip()
            if not energy_map:
                QMessageBox.information(self, "Info", "No frames to send."); return
            cam_name = next(iter(cams)) if len(cams) == 1 else ""
            self._tab_widget.setCurrentIndex(1)
            ok = False
            if hasattr(self._slider_ref, "receive_external_folder"):
                ok = self._slider_ref.receive_external_folder(
                    new_dir, energy_map=energy_map, discrete=True, cam_name=cam_name)
            if not ok:
                # Older is_t.py without the public handoff — set what it reads directly.
                self._slider_ref._sf_energy_map = dict(energy_map)
                self._slider_ref._discrete_mode = True
                self._slider_ref.open_folder_path(new_dir)
            # The previous temp folder is dropped only AFTER the new one is handed
            # over, so a Slider still showing the last send does not lose its files.
            old = getattr(self, "_slider_temp_dir", None)
            if old and Path(old).exists() and Path(old) != root:
                shutil.rmtree(old, ignore_errors=True)
            self._slider_temp_dir = root      # the root, so the camera subfolder goes too
            self._log(f"SLIDER: sent {len(energy_map)} frame(s) as a set.")
        except Exception as e:
            QMessageBox.warning(self, "Image Slider", f"Could not hand over:\n{e}")

    def _open_first_in_slider(self):
        """Internal auto-open: switch to Slider tab and load first checked folder."""
        if self._slider_ref is None or self._tab_widget is None: return
        jobs = self._snapshot_collect_jobs()
        if not jobs: return
        folder, _ = jobs[0]
        if not folder or not folder.exists() or not folder.is_dir():
            self._log(f"SLIDER: folder not found: {folder}"); return
        self._tab_widget.setCurrentIndex(1)
        self._slider_ref.open_folder_path(folder)
        self._log(f"SLIDER: opened {folder.name}")

    def open_folder_in_explorer(self):
        if not self._user_has_selected_day:
            QMessageBox.information(self, "Info", "Select a day first."); return
        jobs    = self._snapshot_collect_jobs()
        folders = [f for f, _ in jobs if f and f.exists() and f.is_dir()]
        if not folders:
            QMessageBox.information(self, "Info", "No folders selected."); return

        import subprocess

        def _open(folder_list):
            for folder in folder_list:
                try:
                    subprocess.Popen(["explorer", str(folder)])
                    self._log(f"EXPLORER: {folder}")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

        if len(folders) == 1:
            _open(folders); return

        # Multiple cameras — ask which to open
        labels = [extract_display_label(f.name) for f in folders]
        dlg = QDialog(self); dlg.setWindowTitle("Otevřít složku")
        dlg_lay = QVBoxLayout(dlg)
        dlg_lay.addWidget(QLabel("Vyber kameru nebo otevři všechny:"))

        from PySide6.QtWidgets import QRadioButton
        btn_group = QButtonGroup(dlg)
        radio_btns = []
        for lbl in labels:
            rb = QRadioButton(lbl)
            btn_group.addButton(rb)
            dlg_lay.addWidget(rb)
            radio_btns.append(rb)
        radio_btns[0].setChecked(True)

        btns_row = QHBoxLayout()
        btn_selected = QPushButton("Otevřít vybranou")
        btn_all      = QPushButton("Otevřít všechny")
        btn_cancel   = QPushButton("Zrušit")
        btns_row.addWidget(btn_selected)
        btns_row.addWidget(btn_all)
        btns_row.addWidget(btn_cancel)
        dlg_lay.addLayout(btns_row)

        btn_selected.clicked.connect(dlg.accept)
        btn_all.clicked.connect(lambda: (setattr(dlg, "_open_all", True), dlg.accept()))
        btn_cancel.clicked.connect(dlg.reject)
        dlg._open_all = False

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg._open_all:
            _open(folders)
        else:
            checked_idx = next((i for i, rb in enumerate(radio_btns) if rb.isChecked()), 0)
            _open([folders[checked_idx]])

    # ── MULTI-DAY SEARCH ─────────────────────────────────────────────────────

    @staticmethod
    def _blocking_call(fn, cancelled: "threading.Event", poll_s: float = 0.05):
        """
        Run fn() in a daemon thread. Poll cancelled every poll_s seconds.
        If cancelled fires before fn completes, abandon the thread and return None.
        Otherwise return fn()'s result.
        """
        result_box: list = [None]
        done_evt = threading.Event()

        def _run():
            try:
                result_box[0] = fn()
            except Exception:
                pass
            finally:
                done_evt.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        while not done_evt.wait(timeout=poll_s):
            if cancelled.is_set():
                return None          # abandon; daemon thread dies with the process
        return result_box[0]

    def _nearest_file_for_ns(self, cam_folder: Path,
                              target_ns: int,
                              cancelled: "threading.Event | None" = None) -> "Path | None":
        """
        Find the image file in cam_folder whose filename timestamp is closest
        to target_ns (UTC nanoseconds).

        Strategy: probe candidate ns values directly via os.path.exists() rather
        than listing the whole directory.  Files are named {folder_name}_-_{ns}.ext
        (original format) so we can construct the path directly.

        We probe at intervals matching the camera's acquisition period (33 Hz ≈
        30 ms, or 1 Hz ≈ 1 s for slower cameras) spanning ±2 s around target_ns.
        The first hit is returned; if nothing is found within ±2 s we fall back to
        a single scandir pass (no stat calls).
        """
        prefix = cam_folder.name  # folder name == file-name prefix
        cam_num_str = extract_folder_number(prefix)
        try:
            is_33hz = int(cam_num_str) in CAM_33HZ
        except (ValueError, TypeError):
            is_33hz = False

        step_ns   = 30_303_030 if is_33hz else 1_000_000_000   # ~33 Hz or 1 Hz
        window_ns = 2_000_000_000                               # ±2 s search window

        # Probe candidates: start at target and expand outward by one step at a time.
        best_path: "Path | None" = None
        best_delta = window_ns + 1

        steps = int(window_ns / step_ns) + 1
        # Build offsets sorted by absolute distance: 0, ±step, ±2*step, …
        offsets: list[int] = [0]
        for i in range(1, steps + 1):
            offsets.append( i * step_ns)
            offsets.append(-i * step_ns)
        offsets.sort(key=abs)

        for off in offsets:
            if cancelled and cancelled.is_set():
                return None
            if abs(off) > best_delta:
                # All remaining offsets are farther — no improvement possible.
                break
            cand_ns = target_ns + off
            for ext in (".png", ".tif", ".tiff", ".jpg"):
                p = cam_folder / f"{prefix}_-_{cand_ns}{ext}"
                if p.exists():
                    best_delta = abs(off)
                    best_path = p
                    break

        if best_path is not None:
            return best_path

        # Fallback: single scandir pass (no stat calls) — handles converted filenames.
        names_ns = []
        try:
            with os.scandir(cam_folder) as it:
                for e in it:
                    if cancelled and cancelled.is_set():
                        return None
                    name = e.name
                    if not name or name.startswith("."):
                        continue
                    dot = name.rfind(".")
                    if dot < 0 or name[dot:].lower() not in IMAGE_EXTS:
                        continue
                    ns = extract_ns_from_stem(name[:dot])
                    if ns is None:
                        continue
                    names_ns.append((ns, name))
        except Exception:
            return None
        if not names_ns:
            return None
        names_ns.sort(key=lambda x: x[0])
        ns_vals = [x[0] for x in names_ns]
        idx = bisect.bisect_left(ns_vals, target_ns)
        candidates = []
        if idx > 0:
            candidates.append(names_ns[idx - 1])
        if idx < len(names_ns):
            candidates.append(names_ns[idx])
        if not candidates:
            return None
        _, best_name = min(candidates, key=lambda x: abs(x[0] - target_ns))
        return cam_folder / best_name

    def _ensure_scan_cache(self):
        """The one shared folder cache, built on first use — it loads `sf_t` with it,
        so it is not made until something actually asks the share. Built on the MAIN
        thread before a search fans out; two threads asking for it at once would each
        make one and the listings would stop being shared."""
        if self._scan_cache is None:
            self._scan_cache = _get_shot_finder_module().DayScanCache()
        return self._scan_cache

    def _resolve_frame_at(self, cam_name: str, target_ns: int,
                          cancelled: "threading.Event | None" = None,
                          log=None) -> "tuple[Path | None, int | None]":
        """The frame `cam_name` holds at `target_ns` — ONE resolver for every search.

        The work goes to `_resolve_moment_one`, the same adapter over
        `shot_finder._find_image_in_day` a picked moment uses, so a region search, a
        condition search and a moment all answer the same question the same way and
        share one folder cache: the first camera in an hour pays for the listing and
        every camera after it is free.

        This replaced `_frame_nearest_ns`, which guessed file names and asked the
        share whether each one existed — up to 132 round trips across a ±2 s window,
        for every camera, for every region, sharing nothing with anything else.

        Returns (path, real_hour) — the hour of the FRAME's own time, in Prague — or
        (None, None), which is the shape the wall already takes.
        """
        if cancelled is not None and cancelled.is_set():
            return None, None
        res = _resolve_moment_one(int(target_ns), cam_name, self._ensure_scan_cache())
        path = res.get("path")
        if path is None:
            if log:
                log(f"  {res.get('note') or 'no frame near this moment'}")
            return None, None
        own_ns = int(res.get("ts_ns") or target_ns)
        dt = datetime.fromtimestamp(own_ns / 1e9, tz=timezone.utc)
        real_h = dt.astimezone(PRAGUE).hour if PRAGUE is not None else dt.hour
        if log:
            log(f"  → {path.name}  ({(own_ns - int(target_ns)) / 1e9:+.1f}s)")
        return path, real_h

    # ── ONE MOMENT, EVERY CAMERA ──────────────────────────────────────────────
    # Picked out of the PV graph: one timestamp, and the frame each camera holds at
    # it. Deliberately a different code path from the day-and-region searches above
    # — they ask the archiver which moment is worth looking at, this one is TOLD.
    # All it has to do is be quick, and that comes from two things: the shared
    # folder-listing cache (one reading of an hour answers every camera) and
    # remembering what it found, so coming back to a moment touches nothing.

    def _res_cache_get(self, cam: str, ts_ns: int) -> "dict | None":
        got = self._res_cache.pop((cam, int(ts_ns)), None)
        if got is not None:
            self._res_cache[(cam, int(ts_ns))] = got     # touched → back of the queue
        return got

    def _res_cache_put(self, item: dict):
        cam, ts = item.get("cam"), item.get("asked_ns")
        if not cam or ts is None:
            return
        if item.get("path") is None:
            # "This camera has nothing there" is only final once the moment is old
            # enough. The archiver runs about a second behind and a frame may simply
            # not be written yet, so a fresh miss is never remembered — otherwise
            # the first look at the current minute would be cached as empty for the
            # rest of the sitting.
            now_ns = int(datetime.now(tz=timezone.utc).timestamp() * 1e9)
            if int(ts) > now_ns - _MOMENT_MISS_MIN_AGE_S * 1_000_000_000:
                return
        self._res_cache[(cam, int(ts))] = dict(item)
        while len(self._res_cache) > 6000:
            self._res_cache.pop(next(iter(self._res_cache)), None)

    def _load_moment(self, ts_ns: int):
        """One moment — the single-moment way in, kept for every old caller."""
        self._load_moments([int(ts_ns)])

    def _load_moments(self, ts_list: "list[int]"):
        """Every picked camera's frame at every picked moment, onto one wall.

        (camera, moment) pairs already answered come straight out of memory; only
        the rest are asked for, sixteen at a time, through the shared folder cache.
        With everything answered from memory nothing is started at all — and two
        moments inside the same hour share the folder reading, which is why picking
        a handful of moments costs barely more than picking one."""
        cams = self._checked_cameras()
        moments: list = []
        for t in ts_list or []:
            t = int(t)
            if t not in moments:
                moments.append(t)      # pick order, not sorted — one numbering
        if not moments:
            return
        if not cams:
            # Should not happen (the caller holds the search until there are
            # cameras), but never silently show an empty wall.
            self._pending_pv_cfg = {"moments_ns": moments, "cameras": [],
                                    "days": [], "regions": {}, "condition": None,
                                    "moment_ns": moments[0],
                                    "primary_channel": None,
                                    "start_hour": 0, "max_hour": 23}
            self._open_camera_picker()
            return
        self._moments_ns = moments
        self._moment_ns = moments[0]
        self._moment_gen += 1
        gen = self._moment_gen
        # A fresh event, not a cleared one: the task still running holds a reference
        # to the old one and must stay stopped.
        self._moment_stop.set()
        self._moment_stop = threading.Event()
        self._moment_items = []

        names = [c[0] for c in cams]
        jobs: list = []
        for t in moments:
            for cam in names:
                hit = self._res_cache_get(cam, t)
                if hit is None:
                    jobs.append((t, cam))
                else:
                    self._moment_items.append(hit)
        when = datetime.fromtimestamp(moments[0] / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            when = when.astimezone(PRAGUE)
        head = when.strftime("%d.%m.%Y %H:%M:%S")
        if len(moments) > 1:
            head += f" + {len(moments) - 1} more"
        self._log(f"[moment] {head} — {len(names)} camera(s) × "
                  f"{len(moments)} moment(s), {len(jobs)} to look for")
        if not jobs:
            self._on_moment_done(gen, 0.0, 0)
            return
        self._ensure_scan_cache()
        self._moment_pool.start(_MomentResolveTask(
            gen, moments[0], [], self._moment_sig, self._moment_stop,
            self._scan_cache, jobs=jobs))

    def _on_moment_item(self, res: dict, gen: int):
        if gen != self._moment_gen:
            return
        self._res_cache_put(res)
        self._moment_items.append(res)

    def _on_moment_done(self, gen: int, ms: float = 0.0, reads: int = 0):
        moments = list(self._moments_ns or [])
        if not moments and self._moment_ns is not None:
            moments = [int(self._moment_ns)]
        if gen != self._moment_gen or not moments:
            return
        # The same ordinal the graph drew and the label listed — one numbering for
        # the whole feature, so tile 2 IS pick 2.
        order = {t: i + 1 for i, t in enumerate(moments)}
        results: dict = {}
        found = 0
        for it in self._moment_items:
            path = it.get("path")
            own = it.get("ts_ns")
            asked = int(it.get("asked_ns") or moments[0])
            if path is not None and own:
                dt = datetime.fromtimestamp(own / 1e9, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(asked / 1e9, tz=timezone.utc)
            if PRAGUE is not None:
                dt = dt.astimezone(PRAGUE)
            meta = {"ptm1": None, "sbw4": None, "source": "pv",
                    "asked_ns": asked, "note": it.get("note") or ""}
            if len(moments) > 1:
                meta["pick"] = order.get(asked)
            if path is not None:
                found += 1
                status = "found"
                if not _image_is_nonempty(path):
                    meta["blank"] = True
            else:
                status = "no_frame"
            results.setdefault(it.get("cam"), []).append(
                (dt.date(), dt.hour, path, meta, status))

        cost = ("from memory" if ms <= 0 else
                f"found in {ms / 1000:.1f} s, {reads} folder read(s)")
        misses = len(self._moment_items) - found
        note = f"[moment] {found} frame(s) — {cost}"
        if misses:
            note += (f"   ·   {misses} (camera, moment) pair(s) had nothing "
                     "near the moment picked")
        self._log(note)

        self.fill_wall(results, self._checked_cameras(),
                       moment_ns=moments[0], moments_ns=moments)
        self._sync_shot_steps()
        self._refresh_moment_list()
        self._sync_send_moment_buttons()
        # Only now, once what was asked for is on the wall: a prefetch must never
        # take a folder listing away from a live click.
        self._start_moment_prefetch()

        paths = sorted([it["path"] for it in self._moment_items
                        if it.get("path") is not None], key=lambda p: p.name)
        if paths:
            self._preview_set_files(paths, "PV moment")
            self._energy_info.setPlainText("Loading energy data…")

            def _after_energy(res_list: list):
                self._energy_results = res_list
                self._refresh_energy_info()
            self._run_energy_lookup_async(paths, on_done=_after_energy)

    def _region_targets(
        self,
        day,                       # datetime.date
        regions_for_day: "list[tuple[int, int]]",
        primary_channel: str,
        cancelled: "threading.Event | None" = None,
        log_fn=None,
    ) -> list:
        """WHEN to pull a frame from, for each marked region on `day`: the time of the
        PEAK of the primary PV inside the region, or the region's midpoint when the PV
        has no samples there.

        It knows nothing about cameras, and that is the whole point. The samples in a
        region are the same whichever camera is being looked for, and this used to be
        fetched again for every one of them — days × cameras × regions requests to the
        archiver where days × regions do. Six cameras over five days with two regions
        each: 10 requests instead of 60.

        Returns one dict per region: {"region": (start_ns, end_ns), "target_ns": int,
        "peak": float | None, "info": the region dict it came from}.
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        out: list = []
        for _reg in regions_for_day:
            info = _reg if isinstance(_reg, dict) else None
            t_start_ns, t_end_ns = _region_span(_reg)
            if is_cancelled():
                break
            samples = None
            try:
                if cancelled is not None:
                    samples = self._blocking_call(
                        lambda ch=primary_channel, s=t_start_ns, e=t_end_ns:
                            _cpva_fetch_samples(ch, s, e, timeout=3.0),
                        cancelled)
                else:
                    samples = _cpva_fetch_samples(primary_channel,
                                                  t_start_ns, t_end_ns, timeout=3.0)
            except Exception as _e:
                log(f"PV fetch error: {_e}")
            if is_cancelled():
                break

            target_ns: "int | None" = None
            peak_val: "float | None" = None
            if isinstance(samples, list):
                for s in samples:
                    t_ns = s.get("time")
                    if t_ns is None:
                        continue
                    val = s.get("value")
                    if isinstance(val, list):
                        val = val[0] if len(val) == 1 else None
                    try:
                        fv = float(val)
                    except (TypeError, ValueError):
                        continue
                    if peak_val is None or fv > peak_val:
                        peak_val = fv
                        target_ns = int(t_ns)

            if target_ns is None:
                target_ns = (t_start_ns + t_end_ns) // 2
                log("region: no PV samples — using midpoint")
            else:
                dt_tgt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                if PRAGUE:
                    dt_tgt = dt_tgt.astimezone(PRAGUE)
                log(f"region peak: {dt_tgt.strftime('%H:%M:%S')}  value={peak_val:.4g}")
            out.append({"region": (t_start_ns, t_end_ns),
                        "target_ns": int(target_ns), "peak": peak_val,
                        "info": info})
        return out

    def _find_image_for_regions(
        self,
        day,                       # datetime.date
        cam_name: str,
        regions_for_day: "list[tuple[int, int]]",
        primary_channel: str,
        cancelled: "threading.Event | None" = None,
        log_fn=None,
        targets: "list | None" = None,
    ) -> "list[tuple[Path | None, int | None, dict, str]]":
        """PV-region driven image lookup.

        For each (t_start_ns, t_end_ns) region on `day`, take the timestamp of the
        PEAK value of primary_channel inside the region as the target time, and
        return the camera frame nearest that time. If the PV has no samples in a
        region, the region midpoint is used instead.

        Returns a LIST of (path, real_hour, meta, status) — one entry per region
        that yielded a frame, each the SAME shape as _find_image_for_day_cam so the
        wall takes either without knowing which ran.

        One entry per region, not one per day: it used to return on the first region
        that worked, so marking three spans on a day still gave one tile and the
        other two spans were silently ignored.

        `targets` is what `_region_targets` worked out for this day. Passing it in is
        what keeps the archiver out of the per-camera loop; left out, this reads the
        PV itself, so the function still works on its own.
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        _no_meta: dict = {"ptm1": None, "sbw4": None, "source": None}
        if is_cancelled():
            return [(None, None, _no_meta, "cancelled")]

        if targets is None:
            targets = self._region_targets(day, regions_for_day, primary_channel,
                                           cancelled=cancelled, log_fn=log_fn)
        hits: list = []

        for tgt in targets:
            if is_cancelled():
                break
            target_ns = int(tgt["target_ns"])
            peak_val = tgt.get("peak")

            p, real_h = self._resolve_frame_at(cam_name, target_ns, cancelled, log)
            if is_cancelled():
                break
            if p is not None:
                meta = dict(_no_meta)
                if peak_val is not None:
                    if primary_channel == CPVA_SBW4_CHANNEL:
                        meta["sbw4"] = peak_val
                    elif primary_channel == CPVA_SHOT_CHANNEL:
                        meta["ptm1"] = peak_val
                    meta["pv_peak"] = peak_val
                meta["source"] = "pv"
                meta["region_ns"] = tuple(tgt["region"])
                meta["target_ns"] = target_ns
                # WHICH region this frame answers. `region_ns` alone was written
                # and never read; the wall needs the number to give each region a
                # row of its own instead of four frames sharing one.
                info = tgt.get("info")
                if isinstance(info, dict):
                    meta["region"] = {
                        "index": info.get("index"),
                        "count": info.get("count"),
                        "label": info.get("label"),
                        "color": info.get("color"),
                        "t_start_ns": int(info["t_start_ns"]),
                        "t_end_ns": int(info["t_end_ns"]),
                    }
                hits.append((p, real_h, meta, "found"))
            else:
                log("region: no frame within reach")

        if hits:
            return hits
        if is_cancelled():
            return [(None, None, _no_meta, "cancelled")]
        return [(None, None, _no_meta, "not_found")]

    # ── PV condition → one shared moment ──────────────────────────────────────
    @staticmethod
    def _cond_predicate(condition: dict):
        """The condition as a plain test on one value."""
        op = condition.get("op") or ">"
        a = float(condition.get("value") or 0.0)
        b = float(condition.get("value2") or 0.0)
        if op == "between":
            lo, hi = (a, b) if a <= b else (b, a)
            return lambda v: lo <= v <= hi
        return {
            ">":  lambda v: v > a,
            ">=": lambda v: v >= a,
            "<":  lambda v: v < a,
            "<=": lambda v: v <= a,
        }.get(op, lambda v: v > a)

    @staticmethod
    def _cond_text(condition: dict) -> str:
        op = condition.get("op") or ">"
        lbl = condition.get("label") or condition.get("channel") or "PV"
        if op == "between":
            return (f"{lbl} between {condition.get('value', 0):g} and "
                    f"{condition.get('value2', 0):g}")
        sym = {">": "above", ">=": "at or above", "<": "below",
               "<=": "at or below"}.get(op, op)
        return f"{lbl} {sym} {condition.get('value', 0):g}"

    def _find_condition_moment(self, days, cams, condition,
                               regions_by_day, cancelled=None, log_fn=None,
                               after_ns: int = 0) -> dict:
        """The FIRST moment the condition held and the cameras had something on them.

        One moment for every camera, on purpose: the question "what did the cameras
        look like when SBW4 was over 13 J" is about one shot, and giving each camera
        its own nearest-match timestamp would answer a different question per tile.

        Returns {status, target_ns, value, day, …}. `status` is one of
          found              — target_ns is the answer
          never_met          — the condition was never true on the marked days
          no_camera_signal   — it was true, but no camera was live at any such moment
          archiver_error     — the days could not be read, so "never" cannot be said
          cancelled
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        channel = condition.get("channel")
        test = self._cond_predicate(condition)
        scope_regions = (condition.get("scope") == "regions")

        # ── 1. Every moment the condition held, in time order ────────────────
        candidates: list = []          # (t_ns, value, day)
        error_days: list = []
        for day in days:
            if is_cancelled():
                return {"status": "cancelled"}
            key = day.strftime("%Y-%m-%d")
            ch_day = cpva.channel_for_day(channel, key)
            try:
                res = cpva.get_day(ch_day, key, timeout=cpva.FULL_DAY_TIMEOUT)
            except Exception as e:
                log(f"{key}: archiver error — {e}")
                error_days.append(day)
                continue
            if res.status in ("error", "stale"):
                log(f"{key}: archiver did not answer ({res.status})")
                error_days.append(day)
                if res.status == "error":
                    continue
            windows = None
            if scope_regions:
                marked = regions_by_day.get(day) or regions_by_day.get(key) or []
                if not marked:
                    log(f"{key}: no region marked — skipped")
                    continue
                windows = [_region_span(r) for r in marked]
            hits = 0
            for t_ns, val in res.samples:
                try:
                    fv = float(val)
                except (TypeError, ValueError):
                    continue
                if not test(fv):
                    continue
                if windows is not None and not any(a <= t_ns <= b for a, b in windows):
                    continue
                if after_ns and int(t_ns) <= after_ns:
                    continue        # already tried and rejected
                candidates.append((int(t_ns), fv, day))
                hits += 1
                if hits >= _COND_MAX_HITS_PER_DAY:
                    log(f"{key}: {hits} matching samples — only the earliest are tried")
                    break
            if hits:
                log(f"{key}: {hits} sample(s) match")
        candidates.sort(key=lambda c: c[0])

        if not candidates:
            if error_days:
                return {"status": "archiver_error", "error_days": error_days}
            return {"status": "never_met"}

        # ── 2. The first of them where the cameras were actually live ────────
        # Judged by each camera's own :TotalPower — the reading the automatic search
        # already trusts for "was this camera seeing anything".
        tp_cache: dict = {}

        def cam_live(cam_name: str, day, t_ns: int) -> "bool | None":
            """True/False, or None when this camera cannot be judged (no TotalPower
            channel, or the archiver would not say)."""
            tp = _cam_totalpower_channel(cam_name)
            if not tp:
                return None
            key = (cam_name, day)
            if key not in tp_cache:
                if is_cancelled():
                    return None
                d0, d1 = _day_bounds_ns_for(day)
                try:
                    tp_cache[key] = _cpva_active_windows_ns(tp, d0, d1,
                                                            debug_log=None) or []
                except Exception as e:
                    log(f"TotalPower {tp}: {e}")
                    tp_cache[key] = None
            wins = tp_cache[key]
            if wins is None:
                return None
            if not wins:
                return False
            return any(a <= t_ns <= b for a, b in wins)

        tried = 0
        fallback = None            # first candidate where SOME camera was live
        for t_ns, val, day in candidates:
            if is_cancelled():
                return {"status": "cancelled"}
            tried += 1
            if tried > _COND_MAX_CANDIDATES:
                log(f"stopped after {_COND_MAX_CANDIDATES} candidate moments")
                break
            verdicts = [cam_live(c[0], day, t_ns) for c in cams]
            live = [v for v in verdicts if v is True]
            dead = [v for v in verdicts if v is False]
            if not dead:
                # Nothing says a camera was dark: either all live, or nothing to go on.
                return {"status": "found", "target_ns": t_ns, "value": val,
                        "day": day, "cams_live": len(live), "cams": len(cams),
                        "error_days": error_days, "candidates": len(candidates)}
            if live and fallback is None:
                fallback = (t_ns, val, day, len(live))

        if fallback is not None:
            t_ns, val, day, n_live = fallback
            log(f"no moment had every camera live — taking the first where "
                f"{n_live}/{len(cams)} were")
            return {"status": "found", "target_ns": t_ns, "value": val, "day": day,
                    "cams_live": n_live, "cams": len(cams), "partial": True,
                    "error_days": error_days, "candidates": len(candidates)}
        return {"status": "no_camera_signal", "candidates": len(candidates),
                "first_ns": candidates[0][0], "first_day": candidates[0][2],
                "error_days": error_days}

    def _find_image_for_day_cam(
        self,
        day,           # datetime.date
        cam_name: str,
        start_hour_real: int,
        max_hour_real: int,
        cancelled: "threading.Event | None" = None,
        log_fn=None,
    ) -> "tuple[Path | None, int | None, dict, str]":
        """
        Find one representative image for (day, camera).

        The question this answers is "when was the machine actually SHOOTING that day",
        and the laser's own energy readings answer it directly — so they are asked first
        and the camera's own power reading only afterwards:

          1. SBW4 energy. Every shot of the day, strongest first; walk down that list
             until one of them has a frame in this camera's folder.
          2. PTM1 energy, the same way — only if SBW4 recorded nothing at all.
          3. This camera's :TotalPower. Its active windows, best window first, aiming at
             the STRONGEST sample in the window.
          4. The daily energy CSV, then a blind hour-by-hour scan.

        The order used to be the reverse, and that is why a fortnight of searching came
        back with one usable picture. :TotalPower gated everything, so a camera whose
        channel is missing or asleep never got as far as the energy readings; the moment
        inside a window was drawn AT RANDOM rather than aimed at a shot; and the fallback
        below it was the daily energy CSV, which has been dead since 19.08.2026 — leaving
        "first file in the folder", i.e. a dark frame between shots.

        Returns (path, real_hour, meta, status) where:
          status ∈ 'found' | 'inactive' | 'not_found' | 'cancelled'
          meta   = {'ptm1': float|None, 'sbw4': float|None, 'source': str|None}
        `source` names the reading that picked the frame — 'sbw4', 'ptm1', 'totalpower',
        'csv' or 'blind' — and is shown on the tile, so a frame nobody vouched for is
        visible as such instead of looking like a broken camera.
        """
        def log(msg: str):
            if log_fn:
                log_fn(f"  {msg}")

        def is_cancelled() -> bool:
            return cancelled is not None and cancelled.is_set()

        _no_meta: dict = {"ptm1": None, "sbw4": None, "source": None}

        class _FallbackToBlindScan(Exception):
            pass

        year, month, day_n = day.year, day.month, day.day

        def real_to_folder_h(real_h: int) -> int:
            if PRAGUE is None:
                return real_h
            dt_p = datetime(year, month, day_n, real_h, tzinfo=PRAGUE)
            return real_h - int(dt_p.utcoffset().total_seconds() / 3600)

        def ns_to_real_h(target_ns: int) -> int:
            dt_utc = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
            if PRAGUE is not None:
                return dt_utc.astimezone(PRAGUE).hour
            return dt_utc.hour

        def try_timestamp(target_ns: int) -> "tuple[Path|None, int|None]":
            if is_cancelled():
                return None, None
            real_h = ns_to_real_h(target_ns)
            folder_h = real_to_folder_h(real_h)
            if not (0 <= folder_h <= 23):
                return None, None   # maps outside this day's folders
            dt_eff = datetime(year, month, day_n, folder_h)
            cam_folder = self._build_target_path(dt_eff) / cam_name
            log(f"scan folder h={folder_h:02d}  {cam_folder}")
            exists = bc(lambda cf=cam_folder: cf.exists(), cancelled)
            if is_cancelled():
                return None, None
            if not exists:
                log("  folder does not exist")
                return None, None
            t0 = time.perf_counter()
            p = bc(lambda cf=cam_folder: self._nearest_file_for_ns(cf, target_ns),
                   cancelled)
            if is_cancelled():
                return None, None
            log(f"  scandir {time.perf_counter()-t0:.2f}s  →  {p.name if p else 'nothing'}")
            return (p, real_h) if p else (None, None)

        def try_timestamp_window(w_start_ns: int, w_end_ns: int) -> "tuple[Path|None, int|None]":
            """Pick any file inside the active TotalPower window (midpoint heuristic)."""
            mid_ns = (w_start_ns + w_end_ns) // 2
            return try_timestamp(mid_ns)

        if is_cancelled():
            return None, None, _no_meta, "cancelled"

        bc = self._blocking_call   # shorthand

        # ── Build day time window ─────────────────────────────────────────────
        if PRAGUE is not None:
            t_start = datetime(year, month, day_n, start_hour_real,
                               tzinfo=PRAGUE).astimezone(timezone.utc)
            t_end   = datetime(year, month, day_n, max_hour_real, 59, 59,
                               tzinfo=PRAGUE).astimezone(timezone.utc)
        else:
            t_start = datetime(year, month, day_n, start_hour_real,
                               tzinfo=timezone.utc)
            t_end   = datetime(year, month, day_n, max_hour_real, 59, 59,
                               tzinfo=timezone.utc)
        start_ns = int(t_start.timestamp() * 1e9)
        end_ns   = int(t_end.timestamp()   * 1e9)

        # ── Method 0: the laser's own energy readings ─────────────────────────
        # SBW4 first, PTM1 only if SBW4 recorded nothing. Both name the moments the
        # machine was firing, which is exactly what a comparison of days wants; the
        # camera's own power reading (below) is a proxy for the same thing and is only
        # needed where the energy readings are silent.
        date_key = day.isoformat()
        energy_max_tries = 20

        def try_energy_channel(channel: str, meta_key: str) -> "tuple | None":
            shots = _energy_shots_ranked(channel, date_key, start_ns, end_ns,
                                         timeout=CPVA_HTTP_TIMEOUT, debug_log=log)
            if not shots:
                return None
            # Strongest first, then down the list: the peak shot of the day is the one
            # worth comparing, and if the archive happens to hold no frame at that exact
            # second the next-strongest is still a shot rather than a gap between them.
            for t_ns, val in shots[:energy_max_tries]:
                if is_cancelled():
                    return None
                p, h = try_timestamp(t_ns)
                if p is not None:
                    dt_tgt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
                    if PRAGUE:
                        dt_tgt = dt_tgt.astimezone(PRAGUE)
                    log(f"  {meta_key.upper()}: {dt_tgt.strftime('%H:%M:%S')} "
                        f"value={val:.4g} → {p.name}")
                    meta = dict(_no_meta)
                    meta[meta_key] = val
                    meta["source"] = meta_key
                    return p, h, meta, "found"
            log(f"  {meta_key.upper()}: {len(shots)} shot(s), but no frame for this camera")
            return None

        log("method0: laser energy (SBW4, then PTM1)")
        for _ch, _key in ((CPVA_SBW4_CHANNEL, "sbw4"), (CPVA_SHOT_CHANNEL, "ptm1")):
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            hit = try_energy_channel(_ch, _key)
            if hit is not None:
                return hit
            if is_cancelled():
                return None, None, _no_meta, "cancelled"

        # ── Method 1: TotalPower → file + additive PTM1/SBW4 ─────────────────
        tp_channel = _cam_totalpower_channel(cam_name)
        _tp_no_windows = False   # set True when TotalPower returns empty → blind-scan fallback
        if tp_channel and tp_channel in self._tp_dead_channels:
            log(f"method1: TotalPower  channel={tp_channel} — skipped (timed out previously)")
            tp_channel = None
        if tp_channel:
            log(f"method1: TotalPower  channel={tp_channel}")
            try:
                # ── Reference window: 6–7h Prague time → used as noise baseline ──
                # Always query from 6h regardless of start_hour_real so that
                # reference samples are always available.
                if PRAGUE is not None:
                    _ref_start = datetime(year, month, day_n, 6, tzinfo=PRAGUE).astimezone(timezone.utc)
                    _ref_end   = datetime(year, month, day_n, 7, tzinfo=PRAGUE).astimezone(timezone.utc)
                else:
                    _ref_start = datetime(year, month, day_n, 6, tzinfo=timezone.utc)
                    _ref_end   = datetime(year, month, day_n, 7, tzinfo=timezone.utc)
                ref_start_ns = int(_ref_start.timestamp() * 1e9)
                ref_end_ns   = int(_ref_end.timestamp()   * 1e9)
                # Extend query start to include 6–7h reference if needed
                query_start_ns = min(start_ns, ref_start_ns)

                t0 = time.perf_counter()
                windows = bc(
                    lambda qs=query_start_ns, rs=ref_start_ns, re=ref_end_ns:
                        _cpva_active_windows_ns(tp_channel, qs, end_ns,
                                                timeout=3.0,
                                                ref_start_ns=rs,
                                                ref_end_ns=re,
                                                active_from_ns=start_ns,
                                                debug_log=log),
                    cancelled)
                elapsed = time.perf_counter() - t0
                if is_cancelled():
                    return None, None, _no_meta, "cancelled"
                if windows is None:
                    if elapsed >= 2.5:
                        # True timeout (not cancellation) — mark channel as dead so
                        # subsequent days skip the 10s wait immediately.
                        self._tp_dead_channels.add(tp_channel)
                        log(f"  TotalPower: timeout ({elapsed:.2f}s) — channel marked dead, skipping for remaining days")
                    else:
                        log(f"  TotalPower: query cancelled/timed out ({elapsed:.2f}s) — falling back to blind scan")
                    _tp_no_windows = True
                    raise _FallbackToBlindScan()
                if not windows:
                    log(f"  TotalPower: no active windows ({elapsed:.2f}s) — falling back to blind scan")
                    _tp_no_windows = True
                    raise _FallbackToBlindScan()

                log(f"  TotalPower: {len(windows)} window(s)  ({elapsed:.2f}s)")

                # Read the power curve once; both the window choice and the target
                # moment come out of it.
                tp_parsed: "list[tuple[int, float]]" = []
                try:
                    for s in (_cpva_fetch_samples(tp_channel, query_start_ns, end_ns,
                                                  timeout=3.0) or ()):
                        t_ns = s.get("time")
                        if t_ns is None:
                            continue
                        v = s.get("value")
                        if isinstance(v, list):
                            v = v[0] if len(v) == 1 else None
                        try:
                            tp_parsed.append((int(t_ns), float(v)))
                        except (TypeError, ValueError):
                            continue
                except Exception as _pe:
                    log(f"  TotalPower samples unavailable: {_pe}")

                # Which window. Where the energy readings did record shots — they just
                # had no frame at those exact seconds — the window holding the most of
                # them is the one the machine was running in. The shot lists come from
                # the per-day cache filled by method 0, so this costs no extra query.
                chosen_window = None
                for ref_ch, ref_key in ((CPVA_SBW4_CHANNEL, "sbw4"),
                                        (CPVA_SHOT_CHANNEL, "ptm1")):
                    if is_cancelled():
                        break
                    ref_shots = _energy_shots_ranked(ref_ch, date_key, start_ns, end_ns,
                                                     timeout=CPVA_HTTP_TIMEOUT)
                    if not ref_shots:
                        continue
                    best_n = 0
                    for ws, we in windows:
                        n_in = sum(1 for t, _ in ref_shots if ws <= t <= we)
                        if n_in > best_n:
                            best_n, chosen_window = n_in, (ws, we)
                    if chosen_window is not None:
                        log(f"  window chosen by {ref_key.upper()}: {best_n} shot(s) inside")
                        break

                if chosen_window is None:
                    if tp_parsed:
                        def _window_avg_power(w):
                            ws, we = w
                            vals = [v for t, v in tp_parsed if ws <= t <= we]
                            return sum(vals) / len(vals) if vals else 0.0
                        chosen_window = max(windows, key=_window_avg_power)
                        log("  window chosen by highest average TotalPower")
                    else:
                        chosen_window = max(windows, key=lambda w: w[1] - w[0])
                        log("  window chosen by length (no samples to weigh)")

                w_start, w_end = chosen_window
                # Aim at the STRONGEST moment in the window, then work down. This used
                # to be a random draw from the window, which on a five-minute-merged
                # window lands between shots as often as on one.
                in_window = sorted((s for s in tp_parsed if w_start <= s[0] <= w_end),
                                   key=lambda s: s[1], reverse=True)
                targets = [t for t, _ in in_window[:energy_max_tries]] or \
                          [(w_start + w_end) // 2]

                for target_ns in targets:
                    if is_cancelled():
                        return None, None, _no_meta, "cancelled"
                    p, h = try_timestamp(target_ns)
                    if p is not None:
                        dt_tgt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                        if PRAGUE:
                            dt_tgt = dt_tgt.astimezone(PRAGUE)
                        log(f"  TotalPower: {dt_tgt.strftime('%H:%M:%S')} → {p.name}")
                        meta = dict(_no_meta)
                        meta["source"] = "totalpower"
                        return p, h, meta, "found"

                # The chosen window's hour has no folder/image for this camera —
                # fall through to the CSV-guided + blind scan below instead of
                # giving up, so a camera active in another hour is still found.
                log("  TotalPower window had no image for this camera — trying CSV/blind scan")
                raise _FallbackToBlindScan()

            except _FallbackToBlindScan:
                pass   # no active windows — continue to blind scan below
            except Exception as e:
                log(f"  TotalPower error: {e}")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not _tp_no_windows:
                log("  TotalPower failed, falling back to blind scan")

        # ── Energy-CSV guidance (CPVA dead → use logged SBW4/PTM1) ────────────
        # The daily energy CSV records when the laser was actually firing.
        def _en(r, col):
            try:
                return float((r.values.get(col) or "").strip())
            except Exception:
                return 0.0

        def _row_ns(r):
            if PRAGUE is not None:
                return int(r.ts_dt.replace(tzinfo=PRAGUE).timestamp() * 1e9)
            return int((r.ts_dt - datetime(1970, 1, 1)).total_seconds() * 1e9)

        erows = self._energy_rows_for_day_cached(year, month, day_n)
        shots = sorted((r for r in erows if _en(r, "sbw4") > 0 or _en(r, "ptm1") > 0),
                       key=lambda r: (_en(r, "sbw4"), _en(r, "ptm1")), reverse=True)

        # Best (highest-SBW4) shot per folder-hour — used to aim the blind scan.
        best_shot_by_h: dict = {}
        for r in shots:
            fh = real_to_folder_h(ns_to_real_h(_row_ns(r)))
            if fh not in best_shot_by_h:
                best_shot_by_h[fh] = r   # shots is energy-desc, so first = strongest

        # ── Method 2a: jump straight to the strongest shots ───────────────────
        if shots:
            log(f"method2a: CSV-guided, {len(shots)} energetic shots in CSV")
            for r in shots[:15]:
                if is_cancelled():
                    return None, None, _no_meta, "cancelled"
                p, h = try_timestamp(_row_ns(r))
                if p is not None:
                    meta = {"ptm1": _en(r, "ptm1") or None,
                            "sbw4": _en(r, "sbw4") or None,
                            "source": "csv"}
                    log(f"  CSV-guided: shot {r.ts_dt.strftime('%H:%M:%S')} "
                        f"sbw4={_en(r,'sbw4'):.3f} ptm1={_en(r,'ptm1'):.2f}")
                    return p, h, meta, "found"

        # ── Method 2b: blind hour-by-hour scan (guaranteed fallback) ──────────
        # Always return SOMETHING if the camera has any image that day. Where the
        # CSV has a shot in this hour, aim at it (image with laser data) instead
        # of just grabbing the first file in the folder.
        log(f"method2b: blind scan h={start_hour_real}–{max_hour_real}")
        for real_h in range(start_hour_real, max_hour_real + 1):
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            folder_h = real_to_folder_h(real_h)
            if not (0 <= folder_h <= 23):
                continue   # maps to an adjacent day's folder — skip
            dt_eff   = datetime(year, month, day_n, folder_h)
            cam_folder = self._build_target_path(dt_eff) / cam_name
            exists = bc(lambda cf=cam_folder: cf.exists(), cancelled)
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            if not exists:
                continue
            best_r = best_shot_by_h.get(folder_h)
            if best_r is not None:
                tns = _row_ns(best_r)
                found_file = bc(lambda cf=cam_folder, t=tns: self._nearest_file_for_ns(cf, t),
                                cancelled)
                meta = {"ptm1": _en(best_r, "ptm1") or None,
                        "sbw4": _en(best_r, "sbw4") or None,
                        "source": "csv"}
            else:
                found_file = bc(lambda cf=cam_folder: self._any_image_from_folder(cf),
                                cancelled)
                # Nothing vouches for this frame — it is simply the first picture in an
                # hour folder, as likely to sit between shots as on one. The tile says so.
                meta = dict(_no_meta, source="blind")
            if is_cancelled():
                return None, None, _no_meta, "cancelled"
            log(f"  blind h={real_h:02d}  →  {found_file.name if found_file else 'nothing'}")
            if found_file:
                return found_file, real_h, meta, "found"

        return None, None, _no_meta, "not_found"

    def _energy_rows_for_day_cached(self, year: int, month: int, day: int) -> "list[_EnergyRow]":
        """Load (and cache) the daily energy CSV rows for the multi-day engine.
        Uses the CSV directly (no CPVA) so it works while the API is dead."""
        key = (year, month, day)
        cache = getattr(self, "_md_energy_cache", None)
        if cache is None:
            cache = self._md_energy_cache = {}
        if key not in cache:
            try:
                cache[key] = _load_energy_csv(_energy_csv_path(datetime(year, month, day)))
            except Exception:
                cache[key] = []
        return cache[key]

    def _open_pv_region_search(self, prefill_qdate: "QDate | None" = None):
        """Open the PV Search dialog (proactive button or fail-path popup).
        On accept, run the region- or condition-driven multi-day search.

        The cameras do NOT have to be picked first. Which cameras to look at and
        which moments to look at are independent halves of one question — the PV
        graph does not depend on a camera at all — so either half may be answered
        first and the search starts when both are in. With no camera picked, the
        finished search is held (`_pending_pv_cfg`) and the camera picker is
        offered; it also runs by itself the moment cameras are checked."""
        cams = self._checked_cameras()
        # Start from EVERY day picked in the Time window, not just the focus day: a
        # multi-day pick that collapsed to one day the moment PV Search opened was
        # the thing that made marking spans across days guesswork.
        # The Time window's days. The fallback used to reach for a panel calendar
        # (`self._cal`) that moved into daypicker long ago, so opening PV Search
        # with no day picked raised AttributeError instead of offering today.
        qds = [prefill_qdate] if prefill_qdate else (
            self._effective_days()
            or [daypicker.date_to_qdate(self._primary_day())])
        try:
            dlg = PVRegionSearchDialog(cams, qds, self)
        except Exception as e:
            import traceback
            self._log(f"PV Search error: {e}\n{traceback.format_exc()}")
            QMessageBox.critical(self, "PV Search", f"Could not open dialog:\n{e}")
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cfg = dlg.get_config()
        self._start_pv_search(cfg)

    # ── The two halves: what to look at, and when ─────────────────────────────
    def _start_pv_search(self, cfg: dict):
        """Run a finished PV Search — or hold it until the cameras are known.

        Called straight from the dialog, and again from the camera picker when the
        cameras were the half that came second."""
        if not self._checked_cameras():
            self._pending_pv_cfg = dict(cfg)
            self._log("PV Search: what to look FOR is set — waiting for the "
                      "cameras. Picking them starts the search.")
            self._open_camera_picker()
            return
        self._pending_pv_cfg = None
        # The cameras as they are NOW, not as they were when the window opened.
        cfg = dict(cfg)
        cfg["cameras"] = self._checked_cameras()
        # Keep the shots the arrows step through. Only replaced when the window
        # actually read some — a search that carries none must not empty them.
        stamps = [int(t) for t in (cfg.get("snap_stamps") or [])]
        if stamps:
            self._shot_stamps = stamps
        cond = cfg.get("condition")
        moments = [int(t) for t in (cfg.get("moments_ns") or [])]
        if not moments and cfg.get("moment_ns") is not None:
            moments = [int(cfg["moment_ns"])]
        if moments:
            # Moments, pointed at. Nothing to search FOR, so it skips the
            # day-and-camera search engine entirely and goes to the frames.
            self._load_moments(moments)
            return
        if cond:
            self._log(f"VIEW (PV condition): {len(cfg['cameras'])} cams × "
                      f"{len(cfg['days'])} day(s), {cond['label']} {cond['op']} "
                      f"{cond['value']:g} over {cond['scope']}")
        else:
            self._log(f"VIEW (PV region): {len(cfg['cameras'])} cams × "
                      f"{len(cfg['days'])} day(s), "
                      f"{sum(len(v) for v in cfg['regions'].values())} region(s)")
        self._run_multiday_search(cfg)

    def _run_condition_search(self, cfg: dict, condition: dict):
        """PV-condition search: find ONE moment, then take it from every camera.

        Deliberately not a per-camera search. "Show me the cameras when SBW4 was over
        13 J" is a question about a single shot; letting each camera pick its own
        nearest match would put a different shot in every tile and call it a
        comparison."""
        cams    = cfg["cameras"]
        days    = cfg["days"]
        regions_by_day = cfg.get("regions") or {}
        cond_text = self._cond_text(condition)

        cancel_evt = threading.Event()
        prog_dlg = QDialog(self)
        prog_dlg.setWindowTitle("Searching…")
        prog_dlg.setMinimumWidth(460)
        prog_dlg.setWindowFlags(prog_dlg.windowFlags() &
                                ~Qt.WindowType.WindowCloseButtonHint)
        prog_layout = QVBoxLayout(prog_dlg)
        prog_lbl = QLabel(f"Looking for the first moment {cond_text}…")
        prog_lbl.setWordWrap(True)
        prog_bar = QProgressBar()
        prog_bar.setRange(0, len(cams) + 1)
        prog_bar.setValue(0)
        prog_bar.setFormat("%v / %m")
        prog_layout.addWidget(prog_lbl)
        prog_layout.addWidget(prog_bar)
        btn_cancel = QPushButton("Cancel")

        def _do_cancel():
            cancel_evt.set()
            prog_lbl.setText("Cancelling… (finishing current request)")
            btn_cancel.setEnabled(False)

        btn_cancel.clicked.connect(_do_cancel)
        prog_layout.addWidget(btn_cancel)

        class _DoneSignal(QObject):
            done = Signal(dict)
        _sig = _DoneSignal()

        def emit_log(msg: str):
            try:
                _sig.done.emit({"_log": msg})
            except RuntimeError:
                pass

        def emit_progress(n: int, label: str):
            try:
                _sig.done.emit({"_progress": n, "_label": label})
            except RuntimeError:
                pass

        def worker():
            emit_log(f"[condition] {cond_text}  over {len(days)} day(s), "
                     f"{len(cams)} camera(s)")
            after_ns = 0
            last_fail: dict = {"status": "never_met"}
            for attempt in range(_COND_MAX_FRAME_TRIES):
                if cancel_evt.is_set():
                    break
                res = self._find_condition_moment(
                    days, cams, condition, regions_by_day,
                    cancelled=cancel_evt, log_fn=emit_log, after_ns=after_ns)
                if res.get("status") != "found":
                    last_fail = res
                    break
                target_ns = res["target_ns"]
                day = res["day"]
                dt = datetime.fromtimestamp(target_ns / 1e9, tz=timezone.utc)
                if PRAGUE:
                    dt = dt.astimezone(PRAGUE)
                emit_log(f"  moment: {day.strftime('%d.%m.%Y')} {dt.strftime('%H:%M:%S')}"
                         f"  value={res['value']:.4g}"
                         f"  ({res.get('cams_live', 0)}/{res.get('cams', 0)} cameras live)")
                emit_progress(1, "Reading the cameras…")

                # Every camera at ONE moment — the same question the moment search
                # asks, so it is asked the same way: all the cameras at once through
                # the shared folder cache, which reads an hour's folder once for the
                # lot of them instead of once each.
                def one_cam(item):
                    _i, (cam_name, cam_label, _folder) = item
                    lines: list = []
                    if cancel_evt.is_set():
                        return cam_name, cam_label, None, None, {}, "cancelled", lines
                    p, real_h = self._resolve_frame_at(
                        cam_name, target_ns, cancel_evt, None)
                    meta = {"ptm1": None, "sbw4": None, "source": "pv_cond",
                            "pv_peak": res["value"], "pv_cond": cond_text}
                    if condition.get("channel") == CPVA_SBW4_CHANNEL:
                        meta["sbw4"] = res["value"]
                    elif condition.get("channel") == CPVA_SHOT_CHANNEL:
                        meta["ptm1"] = res["value"]
                    status = "not_found"
                    blank = False
                    if p is not None:
                        status = "found"
                        # ~1 frame per 35 s is stored, so the nearest one can be a
                        # good few seconds off. That is a reading, and it is said.
                        f_ns = extract_ns_from_stem(p.stem)
                        if f_ns:
                            off = abs(f_ns - target_ns) / 1e9
                            meta["offset_s"] = off
                            lines.append(f"  {cam_label}: {p.name}  ({off:+.1f}s from the moment)")
                        if not _image_is_nonempty(p):
                            meta["blank"] = True
                            blank = True
                            lines.append(f"  {cam_label}: nothing on the frame")
                    else:
                        lines.append(f"  {cam_label}: no frame within reach")
                    meta["_blank"] = blank
                    return cam_name, cam_label, p, real_h, meta, status, lines

                results: dict[str, list] = {}
                usable = 0
                self._ensure_scan_cache()
                workers = min(_MOMENT_RESOLVE_WORKERS, max(1, len(cams)))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for i, (cam_name, cam_label, p, real_h, meta, status, lines) in \
                            enumerate(ex.map(one_cam, list(enumerate(cams)))):
                        for ln in lines:
                            emit_log(ln)
                        if status == "found" and not meta.pop("_blank", False):
                            usable += 1
                        meta.pop("_blank", None)
                        results[cam_name] = [(day, real_h, p, meta, status)]
                        emit_progress(2 + i, cam_label)

                if usable or cancel_evt.is_set():
                    try:
                        _sig.done.emit({"_final": results, "_moment": res})
                    except RuntimeError:
                        pass
                    return
                emit_log("  every camera came back blank — trying the next moment")
                after_ns = target_ns
                last_fail = {"status": "all_blank", "target_ns": target_ns, "day": day}
            try:
                _sig.done.emit({"_final": {}, "_fail": last_fail})
            except RuntimeError:
                pass

        def on_signal(data: dict):
            if not self.isVisible():
                return
            if "_log" in data:
                self._log(data["_log"]); return
            if "_progress" in data:
                prog_bar.setValue(data["_progress"])
                prog_lbl.setText(data["_label"]); return
            prog_dlg.accept()
            results = data.get("_final") or {}
            fail = data.get("_fail")
            if fail is not None or not results:
                self._report_condition_failure(fail or {}, cond_text)
                return
            found_paths = sorted(
                [p for v in results.values() for _, _, p, _, _ in v if p is not None],
                key=lambda x: x.name)
            moment = data.get("_moment") or {}
            if found_paths:
                self._preview_set_files(found_paths, "PV condition")
                self._energy_info.setPlainText("Loading energy data…")

                def _after_energy(res_list: list):
                    self._energy_results = res_list
                    self._refresh_energy_info()
                self._run_energy_lookup_async(found_paths, on_done=_after_energy)
            self.fill_wall(results, cfg["cameras"])
            n = len(found_paths)
            if moment.get("partial"):
                QMessageBox.information(
                    self, "PV Search",
                    f"{cond_text}.\n\nNo moment had every camera live. Took the first "
                    f"where {moment.get('cams_live', 0)} of {moment.get('cams', 0)} "
                    f"were — {n} frame(s) on the wall.")
            elif n < len(cams):
                QMessageBox.information(
                    self, "PV Search",
                    f"{cond_text}.\n\n{n} of {len(cams)} cameras had a frame at that "
                    "moment; the rest are listed in the log.")

        _sig.done.connect(on_signal)
        threading.Thread(target=worker, daemon=True).start()
        prog_dlg.exec()

    def _report_condition_failure(self, fail: dict, cond_text: str):
        """Say WHICH of the three ways it failed. "Not found" would read as "the
        machine never did that", which is only one of them."""
        st = fail.get("status")
        if st == "cancelled":
            self._log("PV condition search: cancelled.")
            return
        if st == "archiver_error":
            days = fail.get("error_days") or []
            names = ", ".join(d.strftime("%d.%m.") for d in days) or "the marked days"
            msg = (f"The archiver did not answer for {names}, so it cannot be said "
                   f"whether {cond_text} ever happened.")
        elif st == "no_camera_signal":
            t = fail.get("first_ns")
            when = ""
            if t:
                dt = datetime.fromtimestamp(t / 1e9, tz=timezone.utc)
                if PRAGUE:
                    dt = dt.astimezone(PRAGUE)
                when = f" — the first was {dt.strftime('%d.%m.%Y %H:%M:%S')}"
            msg = (f"{cond_text} happened {fail.get('candidates', 0)} time(s){when}, "
                   "but no camera was recording anything at any of them "
                   "(judged by each camera's TotalPower).")
        elif st == "all_blank":
            msg = (f"{cond_text} happened, but every camera came back with an empty "
                   "frame at the moments tried.")
        else:
            msg = f"{cond_text} never happened on the marked days."
        self._log(f"PV condition search: {msg}")
        QMessageBox.information(self, "PV Search", msg)

    def _run_search_units(self, cfg: dict, cancel_evt, emit_log, emit_progress) -> dict:
        """The search itself: every (day, camera) the config asks for, in parallel.

        It used to be one (day, camera) after another. Every unit waits on the share
        or on the archiver rather than on the processor, so they overlap almost
        perfectly — and the folder listings they need are shared through
        `_scan_cache`, which means the cameras of one hour cost ONE listing between
        them however many run at once.

        The primary PV is read ONCE PER DAY (`_region_targets`) before any camera is
        looked for. It used to be read again for every camera, though the samples in a
        region are the same whichever camera is being searched for.

        Results are collected by (day, camera) and assembled at the end in the order
        the days and the cameras were picked, so the wall is filled exactly as it was
        when this ran one after the other. Returns {camera: [(day, hour, path, meta,
        status), …]} — path None for a day the camera had nothing on, so the user can
        see it was asked for and retry.

        Separate from `_run_multiday_search` so that it can be run, and counted,
        without a modal progress window (`testing/test_search_speed.py`)."""
        regions_by_day = cfg.get("regions") or {}
        primary_channel = cfg.get("primary_channel")
        region_mode = bool(regions_by_day) and bool(primary_channel)
        hours_by_day = cfg.get("hours_by_day") or {}
        start_hour_real = cfg.get("start_hour")
        max_hour_real = cfg.get("max_hour")

        results: dict = {c[0]: [] for c in cfg["cameras"]}
        days = list(cfg["days"])
        cams = list(cfg["cameras"])
        # Built here, on the main search thread: the units share it, and two of them
        # asking for it at once would each make their own and share no listing.
        self._ensure_scan_cache()

        # ── The PV, once per day ──────────────────────────────────────────────
        targets_by_day: dict = {}
        if region_mode:
            def _targets_for(day):
                lines: list = []
                regions_for_day = regions_by_day.get(day) \
                    or regions_by_day.get(day.isoformat()) or []
                tgts = self._region_targets(
                    day, regions_for_day, primary_channel,
                    cancelled=cancel_evt, log_fn=lines.append)
                return day, tgts, lines
            with ThreadPoolExecutor(
                    max_workers=min(_SEARCH_DAY_WORKERS, max(1, len(days)))) as ex:
                for day, tgts, lines in ex.map(_targets_for, days):
                    targets_by_day[day] = tgts
                    if lines:
                        emit_log(f"[regions] {day.strftime('%d.%m.%Y')}\n"
                                 + "\n".join(lines))

        # ── One frame per (day, camera) ───────────────────────────────────────
        def run_unit(unit):
            di, day, ci, cam = unit
            cam_name, cam_label, _folder = cam
            # Each unit keeps its own log lines and they are printed when it is done,
            # so a line and the line explaining it stay together — with eight units
            # running at once, logging as you go interleaves them into nonsense.
            lines = [f"[search] {day.strftime('%d.%m.%Y')}  {cam_label}"]
            if cancel_evt is not None and cancel_evt.is_set():
                return di, ci, [], lines
            t0 = time.perf_counter()
            if region_mode:
                # One hit per marked region, so a day with three spans gives three
                # tiles instead of just the first one that worked.
                hits = self._find_image_for_regions(
                    day, cam_name, [], primary_channel,
                    cancelled=cancel_evt, log_fn=lines.append,
                    targets=targets_by_day.get(day) or [])
            else:
                # The window the Time window picked FOR THIS DAY; days the user gave
                # their own times keep them.
                d_from, d_to = hours_by_day.get(
                    day, (start_hour_real, max_hour_real))
                hits = [self._find_image_for_day_cam(
                    day, cam_name, d_from, d_to,
                    cancelled=cancel_evt, log_fn=lines.append)]
            elapsed = time.perf_counter() - t0
            for found_path, found_hour, _meta, status in hits:
                if found_path:
                    lines.append(f"  → found  h={found_hour:02d}  "
                                 f"({elapsed:.2f}s)  {found_path.name}")
                else:
                    lines.append(f"  → {status}  ({elapsed:.2f}s)")
            return di, ci, hits, lines

        units = [(di, day, ci, cam)
                 for di, day in enumerate(days)
                 for ci, cam in enumerate(cams)]
        got: dict = {}
        done = 0
        if units:
            with ThreadPoolExecutor(
                    max_workers=min(_SEARCH_UNIT_WORKERS, len(units))) as ex:
                # map hands the results back in the order they were submitted, so the
                # progress bar still counts up through the days in order even though
                # the work itself is out of order.
                for di, ci, hits, lines in ex.map(run_unit, units):
                    got[(di, ci)] = hits
                    emit_log("\n".join(lines))
                    done += 1
                    emit_progress(done,
                                  f"{days[di].strftime('%d.%m')} / {cams[ci][1]}")

        for di, day in enumerate(days):
            for ci, (cam_name, _lbl, _f) in enumerate(cams):
                for found_path, found_hour, meta, status in got.get((di, ci), []):
                    results[cam_name].append(
                        (day, found_hour, found_path, meta, status))
        return results

    def _run_multiday_search(self, cfg: dict):
        """Run the multi-day image search for the given config and put the results on
        the wall tabs.

        cfg = {cameras: [(folder_name, label, folder)], days: [date,…],
               start_hour: int, max_hour: int,
               hours_by_day: {date: (start_hour, max_hour)}}

        `hours_by_day` is what the Time window picked — every day may carry its
        own window (daypicker rule 3). start_hour/max_hour are the fallback for a
        day that is not in the map.

        Optional PV-region mode: if cfg["regions"] is a non-empty dict
        {date: [{"t_start_ns", "t_end_ns", "index", "count", "label", "color"}, …]}
        the search pulls one frame per camera from the PEAK of
        cfg["primary_channel"] inside each region, instead of the automatic
        TotalPower/random-hour selection. A bare `(start, end)` pair is still
        accepted (see `_region_span`); it just gives the frame no region number, so
        the wall puts it on the day's own row.

        Optional PV-condition mode: if cfg["condition"] is set, the search first
        finds ONE moment — the earliest the condition held and the cameras had signal
        — and then takes that same moment from every camera.
        """
        if not cfg["cameras"]:
            QMessageBox.information(self, "Multi-day search", "Select at least one camera."); return
        if not cfg["days"]:
            QMessageBox.information(self, "Multi-day search", "No days selected."); return

        start_hour_real = cfg["start_hour"]
        max_hour_real   = cfg["max_hour"]
        hours_by_day    = cfg.get("hours_by_day") or {}
        regions_by_day  = cfg.get("regions") or {}
        primary_channel = cfg.get("primary_channel")
        condition       = cfg.get("condition") or None
        cond_mode       = bool(condition and condition.get("channel"))
        region_mode     = (not cond_mode) and bool(regions_by_day) and bool(primary_channel)
        if cond_mode:
            self._run_condition_search(cfg, condition)
            return

        # Launch search in background
        cancel_evt = threading.Event()

        prog_dlg = QDialog(self)
        prog_dlg.setWindowTitle("Searching…")
        prog_dlg.setMinimumWidth(420)
        prog_dlg.setWindowFlags(prog_dlg.windowFlags() &
                                ~Qt.WindowType.WindowCloseButtonHint)
        prog_layout = QVBoxLayout(prog_dlg)
        prog_lbl = QLabel("Starting search…")
        prog_lbl.setWordWrap(True)
        _total_items = len(cfg["days"]) * len(cfg["cameras"])
        prog_bar = QProgressBar()
        prog_bar.setRange(0, _total_items)
        prog_bar.setValue(0)
        prog_bar.setFormat("%v / %m  (%p%)")
        prog_bar.setTextVisible(True)
        prog_layout.addWidget(prog_lbl)
        prog_layout.addWidget(prog_bar)
        btn_cancel_search = QPushButton("Cancel")

        def _do_cancel():
            cancel_evt.set()
            prog_lbl.setText("Cancelling… (finishing current request)")
            btn_cancel_search.setEnabled(False)

        btn_cancel_search.clicked.connect(_do_cancel)
        prog_layout.addWidget(btn_cancel_search)

        class _DoneSignal(QObject):
            done = Signal(dict)
        _sig = _DoneSignal()

        def emit_log(msg: str):
            try:
                _sig.done.emit({"_log": msg})
            except RuntimeError:
                pass

        def emit_progress(n: int, label: str):
            try:
                _sig.done.emit({"_progress": n, "_label": label})
            except RuntimeError:
                pass

        def worker():
            results = self._run_search_units(cfg, cancel_evt, emit_log, emit_progress)
            try:
                _sig.done.emit({"_final": results})
            except RuntimeError:
                pass

        def on_signal(data: dict):
            if not self.isVisible():
                return
            if "_log" in data:
                self._log(data["_log"])
                return
            if "_progress" in data:
                prog_bar.setValue(data["_progress"])
                prog_lbl.setText(data["_label"])
                return
            prog_dlg.accept()
            results = data["_final"]
            total_found = sum(1 for v in results.values()
                              for _, _, p, _, _ in v if p is not None)
            if total_found == 0:
                # Nothing matched. Offer the PV-region fallback (unless this WAS
                # already a PV-region search, to avoid looping).
                if not region_mode:
                    resp = QMessageBox.question(
                        self, "Multi-day search",
                        "No images found by the automatic search.\n\n"
                        "Search by PV region instead? (Plot a PV, mark time "
                        "regions, and pull frames from the peak of each region.)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes)
                    if resp == QMessageBox.StandardButton.Yes:
                        first_day = cfg["days"][0] if cfg["days"] else None
                        qd = (QDate(first_day.year, first_day.month, first_day.day)
                              if first_day else None)
                        self._open_pv_region_search(qd)
                else:
                    QMessageBox.information(self, "PV Region Search", "No images found in the marked regions.")
                return
            # Send all found images to inline preview panel
            found_paths = sorted(
                [p for v in results.values() for _, _, p, _, _ in v if p is not None],
                key=lambda x: x.name)
            if found_paths:
                self._preview_set_files(found_paths, "Multi-day")
                # Look up PV values so the preview/info panel show them.
                def _after_energy(res: list):
                    self._energy_results = res
                    self._refresh_energy_info()
                self._energy_info.setPlainText("Loading energy data…")
                self._run_energy_lookup_async(found_paths, on_done=_after_energy)
            # The wall is what this tab is for: the days next to each other, on one
            # shared scale, in the main view. A second window used to open on top of it
            # showing the same frames as thumbnails; everything it could do that the
            # wall could not — search again, marks, rotation, undo — now lives on the
            # wall, so nothing pops up any more.
            self.fill_wall(results, cfg["cameras"])

        _sig.done.connect(on_signal)
        threading.Thread(target=worker, daemon=True).start()
        prog_dlg.exec()

    # ── COLLECT JOBS ──────────────────────────────────────────────────────────
    def _snapshot_collect_jobs(self) -> list[tuple[Path, int]]:
        jobs = []
        for c in self._picked_cams():
            folder = c.get("path")
            if folder is None: continue
            jobs.append((folder, max(0, int(c.get("qty", 1)))))
        return jobs

    # ── SMB LISTING CACHE ─────────────────────────────────────────────────────
    def _get_items_cached(self, folder: Path, debug_log=None, scan_limit: int = None):
        """
        Returns list of (ns, name, size_bytes) for all valid images in folder.
        Two-phase: (1) collect names+ns from scandir without stat(), sort by ns;
        (2) stat() only an evenly-spaced sample of MAX_SCAN_FILES items so that
        network drives don't block on thousands of individual stat calls.
        """
        def log(s: str):
            if debug_log: debug_log(s)

        key  = str(folder)
        key2 = f"{key}::limit={scan_limit or 'ALL'}"
        rec  = self._namecache.get(key2)
        if rec is not None:
            log(f"cache HIT: items={rec['count']}")
            return rec["items"]

        t0 = time.perf_counter()
        # Phase 1: collect names + timestamps only (no stat) — fast even on network drives
        names_ns = []; entries = 0
        try:
            with os.scandir(folder) as it:
                for i, e in enumerate(it):
                    if scan_limit is not None and i >= scan_limit: break
                    entries += 1
                    if not e.is_file() or not is_valid_image_file(e.name): continue
                    ns = extract_ns_from_stem(Path(e.name).stem)
                    if ns is None: continue
                    names_ns.append((ns, e.name))
        except Exception as e:
            log(f"scandir ERROR: {e}")

        names_ns.sort(key=lambda x: x[0])
        t1 = time.perf_counter()

        # Phase 2: stat() a uniform sample — limits network round-trips
        n = len(names_ns)
        limit = MAX_SCAN_FILES if scan_limit is None else min(scan_limit, MAX_SCAN_FILES)
        if n <= limit:
            sample_idx = range(n)
        else:
            # evenly spaced indices across the sorted list
            sample_idx = [round(i * (n - 1) / (limit - 1)) for i in range(limit)]

        sampled_set = set(sample_idx)
        items = []
        for i, (ns, name) in enumerate(names_ns):
            if i in sampled_set:
                try:
                    sz = (folder / name).stat().st_size
                except Exception:
                    sz = 1
            else:
                sz = 0  # not sampled — will be filtered out by threshold anyway
            items.append((ns, name, sz))

        t2 = time.perf_counter()
        sampled = len(sampled_set)
        self._namecache[key2] = {"t": t2, "items": items, "count": len(items)}
        log(f"cache MISS: scandir={t1-t0:.3f}s stat={t2-t1:.3f}s | entries={entries} valid={n} sampled={sampled} usable={len(items)}")
        return items

    def _any_image_from_folder(self, folder: Path) -> "Path | None":
        """Return any image file from folder without stat() calls — used by blind scan."""
        try:
            with os.scandir(folder) as it:
                for e in it:
                    name = e.name
                    if not name or name.startswith("."):
                        continue
                    dot = name.rfind(".")
                    if dot >= 0 and name[dot:].lower() in IMAGE_EXTS:
                        return folder / name
        except Exception:
            pass
        return None

    # ── SELECTION ALGORITHM ───────────────────────────────────────────────────
    def select_images_from_folder(
        self,
        folder: Path,
        how_many: int,
        debug: bool = False,
        debug_log=None,
    ) -> list[Path]:
        """Pick `how_many` frames out of a folder with NO archiver value to aim at.

        The blind fallback: a size threshold taken from the upper half of a sampled
        set of file sizes, the folder split into runs on a 90 s gap, and the quota
        shared out between the runs. It is what answers "show me this hour" when the
        archiver is unreachable or the camera has no TotalPower channel — the one
        selection the Shot Finder cannot make, because its engine needs a target to
        minimise the distance to.

        Four parameters (`window`, `tol_kb`, `sample_step`, `sample_near`) used to sit
        in this signature, dutifully passed by the only call site and read by nothing.
        """
        def log(msg: str):
            if debug and debug_log: debug_log(msg)

        if how_many <= 0: return []

        t0    = time.perf_counter()
        items = self._get_items_cached(folder, debug_log=debug_log if debug else None)
        if not items: log("no items"); return []

        # Threshold computed only from sampled files (sz > 0); unsampled have sz=0
        sampled_sizes = sorted(x[2] for x in items if x[2] > 0); ns = len(sampled_sizes)
        top_half = sampled_sizes[ns // 2:]
        threshold = (top_half[len(top_half) // 2] * 0.75) if top_half else 0

        # Only use files with known size (sz > 0) for selection
        full = [(ts, name) for ts, name, sz in items if sz >= threshold]
        log(f"threshold={threshold:.0f}B full={len(full)}/{len(items)}")
        if not full:
            full = [(ns, name) for ns, name, sz in items]
            log("fallback: using all items (no files above threshold)")

        if how_many >= len(full): return [folder / name for _, name in full]

        SEG_GAP_NS = 90 * 10**9
        segs: list[list] = []; cur_seg = [full[0]]
        for i in range(1, len(full)):
            if full[i][0] - full[i-1][0] > SEG_GAP_NS: segs.append(cur_seg); cur_seg = []
            cur_seg.append(full[i])
        segs.append(cur_seg)
        log(f"segments={len(segs)} sizes={[len(s) for s in segs]}")

        if how_many == 1:
            size_lup = {name: sz for ns, name, sz in items}
            def seg_avg(s): return sum(size_lup.get(nm, 0) for _, nm in s) / len(s)
            best_seg = max(segs, key=seg_avg)
            # Pick the single image with the largest file size in that segment
            best_name = max(best_seg, key=lambda x: size_lup.get(x[1], 0))[1]
            return [folder / best_name]

        total_full = len(full); alloc = []; remaining = how_many
        for i, seg in enumerate(segs):
            if i == len(segs) - 1:
                alloc.append(max(0, remaining))
            else:
                a = round(how_many * len(seg) / total_full)
                a = max(0, min(a, remaining))
                alloc.append(a); remaining -= a
        log(f"alloc={alloc}")

        chosen = []
        for seg, count in zip(segs, alloc):
            if count <= 0: continue
            if count >= len(seg): chosen.extend(folder / name for _, name in seg); continue
            idxs = [round(i * (len(seg)-1) / (count-1)) for i in range(count)] if count > 1 else [len(seg)-1]
            seen_i = set()
            for idx in idxs:
                idx = max(0, min(len(seg)-1, idx))
                if idx not in seen_i: seen_i.add(idx); chosen.append(folder / seg[idx][1])

        log(f"chosen={len(chosen)} total_time={time.perf_counter()-t0:.3f}s")
        return chosen

    # ── TEMP VIEW COPIES ──────────────────────────────────────────────────────
    def _cleanup_view_temp(self):
        try:
            for p in self._view_temp_paths:
                try:
                    if p.exists(): p.unlink()
                except: pass
            self._view_temp_paths = []
            if self._view_temp_dir:
                shutil.rmtree(self._view_temp_dir, ignore_errors=True)
                self._view_temp_dir = None
        except: pass

    def _apply_gradient_to_image(self, img: PilImage.Image, src_path: "Path | None" = None,
                                 grad_name: "str | None" = None,
                                 auto: "bool | None" = None,
                                 gamma=None,
                                 contrast: "int | None" = None,
                                 offset: "int | None" = None) -> PilImage.Image:
        """Apply the selected gradient LUT. Pass grad_name and the display settings when
        calling from a worker thread (reading a widget off the main thread is unsafe)."""
        name = grad_name if grad_name is not None else self._gradient_cb.currentText()
        lut  = GRADIENTS.get(name)
        if lut is None: return img
        if auto is None or gamma is None or contrast is None or offset is None:
            # Called without a snapshot, so this is the main thread: read the controls
            # now. Only the ones the caller left out are taken from the widgets.
            _a, _g, _c, _o = self._bc_args()
            auto = _a if auto is None else auto
            gamma = _g if gamma is None else gamma
            contrast = _c if contrast is None else contrast
            offset = _o if offset is None else offset
        arr = np.array(img)
        if arr.ndim == 3: arr = arr.mean(axis=2)
        arr = arr.astype(np.float32)
        self._log_safe(f"IMG range: min={arr.min():.0f} max={arr.max():.0f} dtype={img.mode} shape={arr.shape}")
        # Same range the preview used, so a saved/exported frame looks like what was on
        # screen — including a camera whose frames are bracketed differently.
        full_scale = (img_scale.full_scale_for_pil(src_path, img.info, img.mode)
                      if src_path is not None
                      else (img_scale.FULL_SCALE_16 if img.mode in ("I", "I;16") else 255.0))
        arr8 = _render_u8(arr, auto, full_scale, gamma, contrast, offset)
        return PilImage.fromarray(
            _lut_pixels(lut, arr8, name).astype(np.uint8), mode="RGB")

    def _make_view_copy_with_readable_name(self, src: Path) -> Path:
        if self._view_temp_dir is None:
            self._view_temp_dir = tempfile.mkdtemp(prefix="IT_view_")
            self._log(f"VIEW temp folder: {self._view_temp_dir}")
        dest_dir = Path(self._view_temp_dir)
        new_stem, reason = build_new_name(src.stem, use_prague_time=True)
        if new_stem is None:
            new_stem = src.stem.replace("-_-","_").replace("_-_","_")
        dst = dest_dir / f"{new_stem}{src.suffix}"
        if dst.exists():
            base = new_stem; i = 1
            while True:
                cand = dest_dir / f"{base}_dup{i}{src.suffix}"
                if not cand.exists(): dst = cand; break
                i += 1
        grad_name = self._gradient_cb.currentText()
        if grad_name != "Grayscale":
            try: self._apply_gradient_to_image(PilImage.open(src), src).save(dst)
            except: shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)
        self._view_temp_paths.append(dst)
        return dst

    # ── ASYNC COLLECT ─────────────────────────────────────────────────────────
    def _collect_primary_files_now(self, jobs: list[tuple[Path, int]]) -> list[Path]:
        self._log_safe(f"_collect_primary_files_now: start, {len(jobs)} jobs")
        if not jobs: return []
        files = []; t0 = time.perf_counter()

        # Derive start_ns/end_ns from the UI-selected datetime (avoids UNC path parsing)
        try:
            dt_utc = self._build_datetime()  # naive UTC datetime
            hour_start = datetime(dt_utc.year, dt_utc.month, dt_utc.day,
                                  dt_utc.hour, 0, 0, tzinfo=timezone.utc)
            hour_end   = datetime(dt_utc.year, dt_utc.month, dt_utc.day,
                                  dt_utc.hour, 59, 59, 999999, tzinfo=timezone.utc)
            query_start_ns = int(hour_start.timestamp() * 1e9)
            query_end_ns   = int(hour_end.timestamp()   * 1e9)
            self._log_safe(f"COLLECT: hour window {hour_start} – {hour_end} UTC")
        except Exception as e:
            self._log_safe(f"COLLECT: cannot build hour window — {e}, using blind scan")
            query_start_ns = None
            query_end_ns   = None

        def mk_debug_log(folder_name: str):
            return lambda s: self._log_safe(f"{folder_name}: {s}")

        def worker(folder: Path, qty: int):
            log = mk_debug_log(folder.name)
            cam_name = folder.name

            # ── Try TotalPower-guided selection first ──────────────────────────
            if query_start_ns is not None:
                tp_channel = _cam_totalpower_channel(cam_name)
                chosen = self._select_by_totalpower(
                    folder, qty, tp_channel, log, query_start_ns, query_end_ns)
                if chosen:
                    return folder, qty, chosen

            # ── Fallback: blind file-size selection ────────────────────────────
            log("TotalPower unavailable — blind file-size fallback")
            chosen = self.select_images_from_folder(
                folder, qty, debug=True, debug_log=log,
            )
            return folder, qty, chosen

        max_workers = min(24, len(jobs))
        self._log_safe(f"COLLECT: parallel scan max_workers={max_workers}")
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(worker, f, q) for f, q in jobs]
            for fut in as_completed(futs):
                try:
                    folder, qty, chosen = fut.result()
                    files.extend(chosen)
                    self._log_safe(f"- {folder.name}: selected {len(chosen)}/{qty}")
                except Exception as e:
                    self._log_safe(f"COLLECT worker ERROR: {type(e).__name__}: {e}")
        self._log_safe(f"COLLECT DONE: total files = {len(files)} | total_time={time.perf_counter()-t0:.3f}s")
        return files

    def _select_by_totalpower(self, folder: Path, qty: int,
                               tp_channel: "str | None", log,
                               start_ns: int, end_ns: int) -> "list[Path]":
        """
        Use SBW4/PTM1/TotalPower to find the best laser-active period, then pick
        the qty image files whose timestamps are closest to the best shot timestamp.

        Strategy:
          1. SBW4: find best shot timestamp (highest energy) in the hour
          2. PTM1: same if SBW4 gives nothing
          3. TotalPower: pick window with highest avg power, use its center
          4. If nothing found, return [] so blind fallback takes over

        We deliberately do NOT filter files by the energy window — instead we anchor
        on the best shot timestamp and pick the nearest files.  This is robust against
        the archiver writing images slightly before or after the actual shot time.
        """
        if not tp_channel:
            return []

        log(f"  hour window: {start_ns/1e9:.0f}–{end_ns/1e9:.0f} (UTC)")

        # ── Step 1: SBW4 → PTM1: find best shot timestamp ────────────────────
        best_shot_ns: "int | None" = None
        for ref_ch in (CPVA_SBW4_CHANNEL, CPVA_SHOT_CHANNEL):
            try:
                t = cpva.best_shot_ns(start_ns, end_ns, channel=ref_ch, timeout=4.0)
                if t is not None:
                    best_shot_ns = t
                    log(f"  {ref_ch}: best shot at {t/1e9:.3f} UTC")
                    break
                log(f"  {ref_ch}: no shots in this hour")
            except Exception as e:
                log(f"  {ref_ch} error: {e}")

        # ── Step 2: TotalPower fallback ───────────────────────────────────────
        if best_shot_ns is None:
            log("  SBW4/PTM1 empty — trying TotalPower")
            try:
                tp_windows = _cpva_active_windows_ns(tp_channel, start_ns, end_ns,
                                                     timeout=4.0, debug_log=log)
                if tp_windows:
                    tp_samples = _cpva_fetch_samples(tp_channel, start_ns, end_ns, timeout=3.0)
                    def _window_avg(w):
                        ws, we = w
                        vals = []
                        for s in tp_samples:
                            t = s.get("time")
                            v = s.get("value")
                            if t is None: continue
                            if isinstance(v, list): v = v[0] if v else None
                            try:
                                if ws <= int(t) <= we:
                                    vals.append(float(v))
                            except (TypeError, ValueError):
                                pass
                        return sum(vals) / len(vals) if vals else 0.0
                    best_win = max(tp_windows, key=_window_avg)
                    best_shot_ns = (best_win[0] + best_win[1]) // 2
                    log(f"  TotalPower: best window center at {best_shot_ns/1e9:.3f} UTC")
            except Exception as e:
                log(f"  TotalPower fallback error: {e}")

        if best_shot_ns is None:
            log("  no active period found — returning []")
            return []

        # ── Correct the hour folder based on best_shot_ns ────────────────────
        # load_folders stores the first found hour for each camera (dedup by name),
        # which may differ from the hour containing the actual best shot.
        import time as _time_cf
        best_shot_dt_utc = datetime.fromtimestamp(best_shot_ns / 1e9, tz=timezone.utc)
        correct_h = best_shot_dt_utc.hour
        correct_folder = folder.parent.parent / str(correct_h) / folder.name
        if correct_folder != folder:
            _t_cf = _time_cf.perf_counter()
            _cf_exists = correct_folder.exists()
            log(f"  correct_folder.exists() took {_time_cf.perf_counter()-_t_cf:.3f}s → {_cf_exists}")
            if _cf_exists:
                log(f"  correcting folder h{folder.parent.name} → h{correct_h}")
                folder = correct_folder
            else:
                log(f"  correct_folder h{correct_h} does not exist — using original")

        # ── Step 3: fast exact-match glob → fallback full listdir ────────────
        # Filenames encode nanoseconds at the end of the stem (SOURCE_RE = r"(\d+)$").
        # Try an exact glob first — single SMB lookup, avoids listing huge directories.
        import bisect as _bisect
        import time as _time

        _t0_listdir = _time.perf_counter()

        if qty == 1:
            try:
                exact_hits = list(folder.glob(f"*{best_shot_ns}.*"))
                exact_hits = [p for p in exact_hits if is_valid_image_file(p.name)]
                if exact_hits:
                    log(f"  exact glob hit: {exact_hits[0].name} (Δ=0.0s) in {_time.perf_counter()-_t0_listdir:.3f}s")
                    return [exact_hits[0]]
            except Exception:
                pass

        try:
            raw_names = os.listdir(folder)
        except Exception as e:
            log(f"  listdir error: {e}")
            return []

        log(f"  listdir took {_time.perf_counter()-_t0_listdir:.3f}s, {len(raw_names)} entries")

        # Parse ns from every valid image filename
        items_ns: list[tuple[int, str]] = []
        for n in raw_names:
            if not is_valid_image_file(n):
                continue
            ns = extract_ns_from_stem(Path(n).stem)
            if ns is not None:
                items_ns.append((ns, n))

        if not items_ns:
            log("  folder empty after filter")
            return []

        items_ns.sort()  # sort by ns ascending
        ns_keys = [x[0] for x in items_ns]
        log(f"  listdir: {len(items_ns)} files | anchor={best_shot_ns/1e9:.3f} UTC")

        # Binary search: find insertion point for best_shot_ns
        idx = _bisect.bisect_left(ns_keys, best_shot_ns)
        idx = min(idx, len(items_ns) - 1)

        # Grab candidates around idx — ±half entries by time proximity
        half = max(qty * 4, 20)
        lo = max(0, idx - half)
        hi = min(len(items_ns), idx + half + 1)
        candidates = items_ns[lo:hi]
        candidates.sort(key=lambda x: abs(x[0] - best_shot_ns))

        if qty == 1:
            best_ns, best_name = candidates[0]
            log(f"  chosen: {best_name} (Δ={(best_ns - best_shot_ns)/1e9:.1f}s)")
            return [folder / best_name]

        # Multiple: pick nearest-in-time candidates, then rank by file size
        near = candidates[:max(qty * 4, 20)]
        try:
            near_sz = [(ns, name, (folder / name).stat().st_size) for ns, name in near]
        except Exception:
            near_sz = [(ns, name, 0) for ns, name in near]
        near_sz.sort(key=lambda x: x[2], reverse=True)
        chosen_names = [name for _, name, _ in near_sz[:qty]]
        log(f"  chosen {len(chosen_names)} files by size near anchor")
        return [folder / name for name in chosen_names]

    def _collect_primary_files_async(self, on_done):
        if self._collect_busy: return
        jobs = self._snapshot_collect_jobs()
        if not jobs:
            QMessageBox.information(self, "Info", "No folders selected."); return
        self._collect_busy = True
        self._set_busy(True)

        # Keep signal alive on self — local variable would be GC'd before thread finishes
        self._collect_sig = _CollectSignals()
        _my_sig = self._collect_sig   # capture at start — don't use self._collect_sig later

        def on_sig_done(files: list):
            if not self.isVisible():
                self._collect_busy = False
                return
            self._log(f"on_sig_done: {len(files)} files, calling on_done")
            self._collect_busy = False
            self._set_busy(False)
            self._collect_sig = None
            on_done(files)

        self._collect_sig.done.connect(on_sig_done)

        def worker():
            self._log_safe("collect worker thread: start")
            try:
                files = self._collect_primary_files_now(jobs)
            except Exception as e:
                import traceback
                self._log_safe(f"COLLECT ERROR: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                files = []
            try:
                _my_sig.done.emit(files)
            except RuntimeError:
                pass   # widget already destroyed

        threading.Thread(target=worker, daemon=True).start()

    # ── VIEW / SAVE ───────────────────────────────────────────────────────────
    def _checked_cameras(self) -> list:
        """Return [(folder_name, label, folder)] for every picked camera."""
        cams = []
        for c in self._picked_cams():
            folder = c.get("path")
            if folder is None:
                continue
            cams.append((folder.name, c.get("label") or folder.name, folder))
        return cams

    def view_primary_files(self):
        # Multi-day: more than one effective (weekday-filtered) day selected →
        # run the multi-day search engine instead of the single-day collect.
        eff_days = self._effective_days()
        if len(eff_days) > 1:
            cams = self._checked_cameras()
            if not cams:
                QMessageBox.information(self, "Info", "No cameras selected — use the Cameras button."); return
            # Search the window the Time window picked — per day, since every day
            # may carry its own (daypicker rule 3). It used to hard-code 00–23 and
            # ignore whatever was picked, so a 08:00–19:00 selection still read the
            # whole night.
            days = [s.date for s in self._segments]
            hours_by_day = {s.date: (s.h_from, max(s.h_from, s.h_to - 1))
                            for s in self._segments}
            spans = sorted(hours_by_day.values())
            cfg = {
                "cameras":      cams,
                "days":         days,
                "hours_by_day": hours_by_day,
                # Fallback for a day with no window of its own: the widest picked.
                "start_hour":   min(s[0] for s in spans),
                "max_hour":     max(s[1] for s in spans),
            }
            self._log(f"VIEW (multi-day): {len(cams)} cams × {len(days)} days, "
                      f"{self._window_summary()}")
            self._run_multiday_search(cfg)
            return

        jobs      = self._snapshot_collect_jobs()
        requested = sum(qty for _, qty in jobs)
        if requested <= 0:
            QMessageBox.information(self, "Info", "No folders selected."); return
        if requested > 5:
            if QMessageBox.question(
                self, "Open many images?",
                f"You are about to open ~{requested} images.\nDo you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return

        def after_collect(files):
            self.primary_files = files
            if not files:
                QMessageBox.information(self, "Info", "No images found."); return

            # Show the files directly in the inline preview panel
            # Use the label of the first picked camera as cam_name
            picked = self._picked_cams()
            cam_name = (picked[0].get("label") or picked[0]["name"]) if picked else ""
            self._preview_set_files(files, cam_name)
            # One day's frames go on the wall too. They used to be visible only in the
            # One frame tab, so with that tab gone a single-day Load data would have
            # come back with nothing on screen.
            self.fill_wall(self._results_from_files(files))

            def after_energy(results: list):
                self._energy_results = results
                self._refresh_energy_info()

            self._energy_info.setPlainText("Loading energy data…")
            self._run_energy_lookup_async(files, on_done=after_energy)

        self._collect_primary_files_async(after_collect)

    def _run_energy_lookup_async(self, files: list[Path], on_done=None):
        """Look up energy data for files in a background thread, then update UI."""
        self._energy_sig = _CollectSignals()
        _sig = self._energy_sig  # local ref — prevents GC if called again

        def on_results(results: list):
            if on_done is not None:
                on_done(results)
            else:
                self._energy_results = results
                self._refresh_energy_info()

        _sig.done.connect(on_results)

        def worker():
            try:
                print("ENERGY worker: start")
                results = self._lookup_energy_for_files(files)
                print(f"ENERGY worker: done, {len(results)} results")
                _sig.done.emit(results)
            except Exception as e:
                import traceback
                print(f"Energy lookup error: {e}\n{traceback.format_exc()}")
                _sig.done.emit([])

        threading.Thread(target=worker, daemon=True).start()

    # ── SAVE AS — the frames on the wall ──────────────────────────────────────
    # Four cells across on a saved sheet, the same number the wall allows itself on
    # screen, and the operators' own. Wider than 1400 px per frame makes a file
    # nothing will open without complaint and buys no detail a 12-bit frame has.
    _SHEET_COLS = 4
    # ...unless the frames divide evenly by camera and up to this many go across.
    # Then the row break lands ON the camera boundary and each row is one camera's
    # moments in order — six cameras at five picked moments is 5 × 6, not eight rows
    # of four with the cameras cut in half. Same flat grid, wrapped where it means
    # something.
    _SHEET_COLS_MAX = 6
    _SHEET_CELL_MAX = 1400

    def _frames_on_the_wall(self) -> "list[dict]":
        """Every frame the wall is showing, once each.

        The per-camera tabs and the Day-by-day tab hold the SAME frames, so the union
        across tabs is deduplicated by file path — a frame that appears on two tabs is
        one file to save, not two. A tile with nothing behind it (a camera that had no
        frame near the picked moment) has nothing to save and is left out.
        """
        seen: set = set()
        out: list = []
        walls = list(self._all_walls())
        cur = getattr(self, "_wall", None)
        if cur is not None and cur not in walls:
            walls.append(cur)
        for wall in walls:
            try:
                cells = wall.cells()
            except Exception:
                continue
            for c in cells:
                p = c.get("path")
                if not p:
                    continue
                p = Path(p)
                if str(p) in seen:
                    continue
                seen.add(str(p))
                out.append({
                    "path":  p,
                    "cam":   c.get("cam") or p.parent.name,
                    "day":   c.get("day"),
                    "pick":  c.get("pick"),
                    "ts_ns": c.get("ts_ns") or extract_ns_from_stem(p.stem),
                })
        out.sort(key=lambda d: (str(d["cam"]), str(d["day"]), d["ts_ns"] or 0))
        return out

    def save_primary_files_as(self):
        """Save the frames that are ON THE WALL, having first asked how.

        It used to ignore the wall entirely: it called `_collect_primary_files_async`,
        which re-picks ONE frame per picked camera folder out of the hour in the Time
        window. Six cameras showing five picked moments each — thirty frames on
        screen — therefore wrote six files, and not even those six. The wall is the
        answer this tab has already produced, and saving anything else answers a
        different question.

        With nothing on the wall yet the old behaviour is still the right one, so it
        is kept as the fallback: straight after the cameras are picked, before any
        search has run, Save As writes the TotalPower pick for each of them.
        """
        frames = self._frames_on_the_wall()
        if frames:
            self.primary_files = [f["path"] for f in frames]
            self._log(f"SAVE AS: {len(frames)} frame(s) on the wall")
            self._save_frames_with_options(frames)
            return

        def after_collect(files):
            self._log(f"after_collect: start, {len(files)} files")
            self.primary_files = files
            if not files:
                QMessageBox.information(self, "Info", "No images selected yet."); return
            self._save_frames_with_options([
                {"path": p, "cam": extract_display_label(p.parent.name),
                 "day": None, "pick": None,
                 "ts_ns": extract_ns_from_stem(p.stem)} for p in files])

        self._collect_primary_files_async(after_collect)

    def _save_frames_with_options(self, frames: "list[dict]"):
        """Ask how, ask where, then write them in a worker.

        Everything the worker needs is read off the widgets HERE — the palette, the
        three display sliders and the picked PVs. A worker that reads a widget is the
        bug this tab has already been bitten by.
        """
        title = "Save As"
        frames = [f for f in frames if f.get("path")]
        if not frames:
            QMessageBox.information(self, title, "No images selected yet."); return
        cams = sorted({str(f.get("cam") or "") for f in frames})
        dlg = _SaveFramesDialog(len(frames), len(cams),
                                self._cb_annotate.isChecked(), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        opts = dlg.result_options()
        mode = opts["mode"]

        # NEVER hand the file dialog a UNC path — it pays the full SMB timeout
        # (about 48 s) before it draws.
        start = self._last_save_dir
        start_dir = (str(start) if start and not str(start).startswith("\\\\")
                     else str(Path.home()))
        stem = f"frames_{'_'.join(cams)[:40] or 'wall'}"
        if mode == "pdf":
            dest, _ = QFileDialog.getSaveFileName(
                self, title, str(Path(start_dir) / (stem + ".pdf")),
                "PDF document (*.pdf)")
        elif mode == "sheet":
            dest, _ = QFileDialog.getSaveFileName(
                self, title, str(Path(start_dir) / (stem + ".png")),
                "PNG image (*.png)")
        else:
            dest = QFileDialog.getExistingDirectory(
                self, "Select destination folder", start_dir)
        if not dest:
            return
        dest_path = Path(dest)
        self._last_save_dir = (dest_path if mode in ("each", "cam")
                               else dest_path.parent)

        grad_name = self._gradient_cb.currentText()
        auto, gamma, contrast, offset = self._bc_args()
        sel_cols = self._pv_visible_cols()

        self._save_as_sig = _CollectSignals()
        _sig = self._save_as_sig   # local ref — prevents GC if called again

        def on_save_done(payload: list):
            self._set_busy(False)
            QMessageBox.information(self, title, payload[0] if payload else "Done")

        _sig.done.connect(on_save_done)
        self._set_busy(True)
        self._log(f"SAVE AS: {len(frames)} frame(s) → {dest_path} (mode={mode}, "
                  f"subfolders={opts['subfolders']}, pv_bar={opts['pv_bar']}) "
                  f"in background…")

        def worker():
            try:
                render = {
                    "grad_name": grad_name, "auto": auto, "gamma": gamma,
                    "contrast": contrast, "offset": offset,
                    "sel_cols": sel_cols, "pv_bar": opts["pv_bar"],
                    "energy": (self._save_energy_map(frames, sel_cols)
                               if opts["pv_bar"] else {}),
                }
                if mode == "each":
                    msg = self._save_frames_each(frames, dest_path,
                                                 opts["subfolders"], render)
                elif mode == "pdf":
                    msg = self._save_frames_pdf(frames, dest_path, render)
                elif mode == "cam":
                    msg = self._save_frames_per_camera(frames, dest_path, render)
                else:
                    msg = self._save_frames_sheet(frames, dest_path, render)
            except Exception as e:
                import traceback
                self._log_safe(f"SAVE AS ERROR: {type(e).__name__}: {e}\n"
                               f"{traceback.format_exc()}")
                msg = f"Nothing was saved.\n{type(e).__name__}: {e}"
            try:
                _sig.done.emit([msg])
            except RuntimeError:
                pass          # widget already destroyed

        threading.Thread(target=worker, daemon=True).start()

    def _save_energy_map(self, frames: "list[dict]", sel_cols: "list[str]") -> dict:
        """path → (match, before, after) for the PV bar, looked up once for the whole
        save. The shared day cache is warmed in parallel first, so the per-file
        lookups below are in-memory bisects rather than a request each."""
        files = [f["path"] for f in frames]
        try:
            chans = [CPVA_CHANNEL_MAP[c] for c in sel_cols if c in CPVA_CHANNEL_MAP]
            dkeys = {cpva.date_key_for_ns(ns) for ns in
                     (extract_ns_from_stem(p.stem) for p in files) if ns}
            if chans and dkeys:
                cpva.warm_days(chans, dkeys, timeout=CPVA_HTTP_TIMEOUT)
        except Exception:
            pass
        out: dict = {}
        try:
            for entry in self._lookup_energy_for_files(files):
                out[str(entry[0])] = (entry[1], entry[2], entry[3])
        except Exception as e:
            self._log_safe(f"SAVE AS: PV lookup failed — {e}")
        return out

    def _save_caption(self, f: dict) -> str:
        """`3)   C03-039-PAM10NF   04.09. 13:08:41` — the pick number when there is
        one, the camera, and the frame's OWN time. Never the moment that was asked
        for: the file holds the frame the archiver actually wrote."""
        parts = []
        if f.get("pick"):
            parts.append(f"{f['pick']})")
        if f.get("cam"):
            parts.append(str(f["cam"]))
        ns = f.get("ts_ns")
        if ns:
            try:
                w = datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)
                if PRAGUE is not None:
                    w = w.astimezone(PRAGUE)
                parts.append(w.strftime("%d.%m. %H:%M:%S"))
            except Exception:
                pass
        return "   ".join(parts)

    def _save_display_pil(self, src: Path, render: dict) -> "PilImage.Image":
        """`src` rendered the way this tab is drawing it — 8-bit RGB, always.

        "Grayscale" does NOT mean "leave the file alone" here. The archive frames are
        12-bit counts stored in 16-bit PNGs, and PIL's own 16-bit-to-RGB conversion
        clips at 255 — which is why a grayscale frame saved with a PV bar used to come
        out white. A sheet or a PDF page has to hold something a viewer can display,
        on this tab's own absolute per-camera scale (`img_scale`), with its contrast,
        brightness and gamma. Saving a file for each frame keeps copying the archive
        original instead, which is the right answer THERE — that is what it is for.
        """
        pil = PilImage.open(src)
        if render["grad_name"] != "Grayscale":
            return self._apply_gradient_to_image(
                pil, src, grad_name=render["grad_name"], auto=render["auto"],
                gamma=render["gamma"], contrast=render["contrast"],
                offset=render["offset"]).convert("RGB")
        arr = np.array(pil)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        full_scale = img_scale.full_scale_for_pil(src, pil.info, pil.mode)
        arr8 = _render_u8(arr.astype(np.float32), render["auto"], full_scale,
                          render["gamma"], render["contrast"], render["offset"])
        return PilImage.fromarray(arr8.astype(np.uint8), mode="L").convert("RGB")

    def _save_pv_bar_onto(self, img: "PilImage.Image", src: Path,
                          render: dict) -> "PilImage.Image":
        """The white PV bar under an already-rendered frame, in memory.

        `_write_annotated_from_pil` is the program's one bar drawer and it writes a
        FILE, so the frame goes out through a temporary one and comes back rather
        than being handed to a second bar drawer that could drift from it."""
        if not render["pv_bar"]:
            return img
        tmp = None
        try:
            match = render["energy"].get(str(src), (None, None, None))[0]
            text = _pv_bar_text(match, render["sel_cols"])
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t:
                tmp = Path(t.name)
            _write_annotated_from_pil(img, tmp, text)
            out = PilImage.open(tmp)
            out.load()
            return out.convert("RGB")
        except Exception as e:
            self._log_safe(f"SAVE AS: PV bar failed for {src.name} — {e}")
            return img
        finally:
            if tmp is not None:
                try: tmp.unlink()
                except Exception: pass

    def _save_frames_each(self, frames: "list[dict]", dest: Path,
                          subfolders: bool, render: dict) -> str:
        """A file for each frame: the archive frame itself, renamed to its Prague
        timestamp, optionally in a folder of its camera's own.

        An untouched grayscale frame is COPIED, so what lands on disk is the archive
        file with its metadata and its full 16-bit depth. A palette or a PV bar makes
        it a new picture, and that one is written as a PNG with the source's metadata
        copied back in."""
        rendered = render["pv_bar"] or render["grad_name"] != "Grayscale"
        try:
            _copy_meta = _get_slider_module()._copy_metadata_into_png
        except Exception:
            _copy_meta = None
        copied = 0; annotated = 0; skipped_already = 0; skipped_nomatch = 0
        used_dirs: set = set()
        for f in frames:
            src = f["path"]
            try:
                if not src.exists() or not is_valid_image_file(src.name):
                    self._log_safe(f"SAVE: {src} is gone — skipped")
                    continue
                out_dir = dest
                if subfolders:
                    safe = re.sub(r"[^0-9A-Za-z._-]+", "_",
                                  str(f.get("cam") or src.parent.name)).strip("_")
                    out_dir = dest / (safe or "camera")
                if out_dir not in used_dirs:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    used_dirs.add(out_dir)

                new_stem, reason = build_new_name(src.stem, use_prague_time=True)
                if new_stem is None and reason == "already_converted":
                    new_stem = src.stem.replace("-_-", "_").replace("_-_", "_")
                    skipped_already += 1
                if new_stem is None and reason == "no_trailing_number":
                    new_stem = src.stem; skipped_nomatch += 1
                if new_stem is None:
                    new_stem = src.stem

                suffix = ".png" if rendered else src.suffix
                dst = out_dir / f"{new_stem}{suffix}"
                if dst.exists():
                    base = dst.stem; i = 1
                    while True:
                        cand = out_dir / f"{base}_dup{i}{suffix}"
                        if not cand.exists():
                            dst = cand; break
                        i += 1

                if rendered:
                    img = self._save_display_pil(src, render)
                    if render["pv_bar"]:
                        match = render["energy"].get(str(src), (None, None, None))[0]
                        _write_annotated_from_pil(
                            img, dst, _pv_bar_text(match, render["sel_cols"]))
                        annotated += 1
                    else:
                        img.save(dst)
                    if _copy_meta is not None:
                        try: _copy_meta(src, dst, save_txt=False)
                        except Exception: pass
                else:
                    shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                self._log_safe(f"SAVE ERROR: {src} -> {type(e).__name__}: {e}")

        msg = f"Saved {copied} of {len(frames)} frame(s) to:\n{dest}"
        if subfolders:
            msg += f"\n- one folder per camera ({len(used_dirs)})"
        if annotated:
            msg += f"\n- {annotated} with the PV values burned in"
        if skipped_already:
            msg += f"\n- {skipped_already} already in final format (kept name)"
        if skipped_nomatch:
            msg += f"\n- {skipped_nomatch} had no trailing ns timestamp (kept name)"
        return msg

    def _save_sheet_heading(self, frames: "list[dict]") -> str:
        def _d(d):
            return d.strftime("%d.%m.%Y") if hasattr(d, "strftime") else str(d)
        cams = sorted({str(f.get("cam") or "") for f in frames})
        days = sorted({f["day"] for f in frames if f.get("day")}, key=str)
        head = (f"{len(frames)} frame{'s' if len(frames) != 1 else ''}, "
                f"{len(cams)} camera{'s' if len(cams) != 1 else ''}")
        if days:
            head += "   ·   " + _d(days[0])
            if len(days) > 1:
                head += " – " + _d(days[-1])
        return head

    def _save_frames_per_row(self, imgs: list) -> int:
        """How many across so a row is one camera — 0 when that does not work out.

        Only when every camera on the sheet carries the SAME number of frames, and
        that number fits across the page: otherwise a row would start mid-camera
        anyway and the ordinary four-across grid is the honest layout. The frames
        arrive sorted camera-first, so the wrap alone does the grouping.
        """
        counts: dict = {}
        for f, _img in imgs:
            counts[str(f.get("cam") or "")] = counts.get(str(f.get("cam") or ""), 0) + 1
        n = set(counts.values())
        if len(counts) < 2 or len(n) != 1:
            return 0
        per = n.pop()
        return per if 2 <= per <= self._SHEET_COLS_MAX else 0

    def _save_sheet_image(self, frames: "list[dict]", render: dict,
                          heading: str = "") -> "PilImage.Image":
        """Every frame side by side on one picture, four across, each captioned.

        The frames are rendered at their own resolution and then scaled to ONE cell
        size — the rule the wall follows on screen, and for the same reason: a
        comparison read at two magnifications is not a comparison. The caption band
        under each frame is dark grey with white ink (a band whose colour is set and
        whose ink is not is how an unreadable caption happens) and names the camera
        and the frame's own time, because a sheet leaves the tab behind and has to
        say what it holds on its own.
        """
        from PIL import ImageDraw as _ID
        imgs: list = []
        for f in frames:
            try:
                img = self._save_display_pil(f["path"], render)
                imgs.append((f, self._save_pv_bar_onto(img, f["path"], render)))
            except Exception as e:
                self._log_safe(f"SHEET: {f['path'].name} left out — "
                               f"{type(e).__name__}: {e}")
        if not imgs:
            raise RuntimeError("not one frame could be read")

        cell_w = min(self._SHEET_CELL_MAX, max(i.width for _f, i in imgs))
        cell_h = max(max(1, int(round(i.height * cell_w / max(1, i.width))))
                     for _f, i in imgs)
        cap_h = max(30, cell_w // 24)
        cap_fs = max(15, int(cap_h * 0.6))
        gap = 8
        cols = min(self._SHEET_COLS, len(imgs))
        per_cam = self._save_frames_per_row(imgs)
        if per_cam:
            cols = per_cam
        rows = (len(imgs) + cols - 1) // cols
        head_fs = max(16, int(cap_fs * 1.3))
        head_h = (head_fs + 22) if heading else 0

        W = cols * cell_w + (cols + 1) * gap
        H = head_h + rows * (cell_h + cap_h + gap) + gap
        sheet = PilImage.new("RGB", (W, H), (26, 26, 26))
        draw = _ID.Draw(sheet)
        if heading:
            draw.text((gap + 4, 10), heading, fill=(240, 240, 240),
                      font=_pil_font(head_fs))
        cap_font = _pil_font(cap_fs)
        for n, (f, img) in enumerate(imgs):
            r, c = divmod(n, cols)
            x = gap + c * (cell_w + gap)
            y = head_h + gap + r * (cell_h + cap_h + gap)
            new_h = max(1, int(round(img.height * cell_w / max(1, img.width))))
            scaled = img.resize((cell_w, new_h), PilImage.Resampling.LANCZOS)
            sheet.paste(scaled, (x, y + max(0, (cell_h - new_h) // 2)))
            draw.rectangle((x, y + cell_h, x + cell_w - 1, y + cell_h + cap_h - 1),
                           fill=(58, 58, 58))
            draw.text((x + 8, y + cell_h + (cap_h - cap_fs) // 2),
                      self._save_caption(f), fill=(255, 255, 255), font=cap_font)
        return sheet

    def _save_frames_sheet(self, frames: "list[dict]", dest: Path,
                           render: dict) -> str:
        img = self._save_sheet_image(frames, render,
                                     self._save_sheet_heading(frames))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest)
        return (f"Saved {len(frames)} frame(s) on one picture:\n{dest}\n"
                f"- {img.width} × {img.height} px")

    def _save_frames_per_camera(self, frames: "list[dict]", dest: Path,
                                render: dict) -> str:
        """One picture per camera, holding that camera's frames side by side."""
        dest.mkdir(parents=True, exist_ok=True)
        by_cam: dict = {}
        for f in frames:
            by_cam.setdefault(str(f.get("cam") or "camera"), []).append(f)
        written: list = []
        failed: list = []
        for cam in sorted(by_cam):
            safe = re.sub(r"[^0-9A-Za-z._-]+", "_", cam).strip("_") or "camera"
            out = dest / f"{safe}.png"
            i = 1
            while out.exists():
                out = dest / f"{safe}_dup{i}.png"; i += 1
            try:
                head = f"{cam}   ·   {self._save_sheet_heading(by_cam[cam])}"
                self._save_sheet_image(by_cam[cam], render, head).save(out)
                written.append(out)
            except Exception as e:
                failed.append(f"{cam}: {type(e).__name__}: {e}")
        msg = f"Saved {len(written)} picture(s), one per camera, to:\n{dest}"
        for p in written:
            msg += f"\n- {p.name}"
        if failed:
            msg += "\nFailed:\n" + "\n".join(failed)
        return msg

    def _save_stamp_caption(self, img: "PilImage.Image",
                            text: str) -> "PilImage.Image":
        """A frame with its name under it. A PDF page carries one frame and nothing
        else, so without this the page cannot say which frame it is."""
        from PIL import ImageDraw as _ID
        cap_h = max(30, img.width // 40)
        fs = max(14, int(cap_h * 0.6))
        out = PilImage.new("RGB", (img.width, img.height + cap_h), (58, 58, 58))
        out.paste(img, (0, 0))
        _ID.Draw(out).text((8, img.height + (cap_h - fs) // 2), text,
                           fill=(255, 255, 255), font=_pil_font(fs))
        return out

    def _save_frames_pdf(self, frames: "list[dict]", dest: Path,
                         render: dict) -> str:
        """One PDF, a page for each frame, at the frame's own resolution.

        PIL writes it (`save_all`), so there is no new dependency and no QPainter on
        a worker thread. 200 dpi is stated on every page, which is what makes a page
        come out the size of the frame instead of a nominal A4 with the frame
        floating on it."""
        pages: list = []
        for f in frames:
            try:
                img = self._save_display_pil(f["path"], render)
                img = self._save_pv_bar_onto(img, f["path"], render)
                pages.append(self._save_stamp_caption(img, self._save_caption(f)))
            except Exception as e:
                self._log_safe(f"PDF: {f['path'].name} left out — "
                               f"{type(e).__name__}: {e}")
        if not pages:
            raise RuntimeError("not one frame could be read")
        dest.parent.mkdir(parents=True, exist_ok=True)
        pages[0].save(dest, "PDF", save_all=True, append_images=pages[1:],
                      resolution=200.0,
                      title=f"Image Finder — {len(pages)} frame(s)")
        return f"Saved {len(pages)} frame(s) as {len(pages)} page(s):\n{dest}"

    # ── INFO ──────────────────────────────────────────────────────────────────
    def show_info(self):
        dlg = QDialog(self); dlg.setWindowTitle("Info"); dlg.resize(720, 420)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit(); txt.setReadOnly(True); txt.setPlainText(load_readme_text())
        lay.addWidget(txt)
        btn = QPushButton("Close"); btn.clicked.connect(dlg.accept); lay.addWidget(btn)
        dlg.exec()

    # ── MEMORY A/B ────────────────────────────────────────────────────────────
    def _save_to_memory(self, slot: str = None):
        # Use the currently displayed preview image if available
        if self._preview_paths and 0 <= self._preview_idx < len(self._preview_paths):
            p = self._preview_paths[self._preview_idx]
            self._log(f"MEMORY: using currently displayed preview [{self._preview_idx + 1}/{len(self._preview_paths)}]")
            self._do_save_to_memory_slot(p, slot)
            return
        QMessageBox.information(self, "Memory",
            "No image displayed in preview. Use View first, then navigate to the desired image.")

    def _send_to_workshop(self):
        """Collect primary files and send all of them to Workshop tab."""
        wk = getattr(self, "_workshop_ref", None)
        if wk is None:
            return

        # The Workshop measures and builds its histogram from the picture it is given,
        # so it gets the plain absolute mapping — never this tab's Auto passes,
        # Contrast, Brightness or Gamma, which would hand it a display curve instead
        # of data.
        def _send_one(src: Path):
            from PIL import Image as _PilImg
            import numpy as _np
            pil = _PilImg.open(str(src))
            if pil.mode in ("I", "I;16"):
                arr_f = _np.array(pil, dtype=_np.float32)
            else:
                arr_f = _np.array(pil.convert("L"), dtype=_np.float32)
            full_scale = img_scale.full_scale_for_pil(src, pil.info, pil.mode)
            arr8 = _render_u8(arr_f, False, full_scale, None)
            cam_name = src.parent.name
            label = f"{cam_name}  |  {src.name}"
            wk.receive_image(arr8, label, source_path=src)

        def after_collect(files):
            if not files:
                QMessageBox.information(self, "Workshop", "No image selected."); return
            sent = 0
            errors = []
            for src in files:
                try:
                    _send_one(src)
                    sent += 1
                except Exception as e:
                    errors.append(f"{src.name}: {e}")
            self._log(f"WORKSHOP: sent {sent} file(s)")
            if errors:
                QMessageBox.warning(self, "Workshop",
                    f"Sent {sent} file(s), {len(errors)} error(s):\n" + "\n".join(errors))

        self._collect_primary_files_async(after_collect)

    def _do_save_to_memory_slot(self, p: Path, slot: str = None):
        def readable_name(path: Path) -> str:
            new_stem, reason = build_new_name(path.stem,
                                              use_prague_time=True)
            self._log(f"READABLE_NAME: stem={path.stem!r} -> new_stem={new_stem!r} reason={reason!r}")
            if new_stem:
                m = re.search(r"(\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d+)$", new_stem)
                if m: return m.group(1)
            return path.stem

        target = slot if slot is not None else ("A" if self._mem_a is None else "B")
        if target == "A":
            self._mem_a = p; self._lbl_mem_a.setText(f"A: {readable_name(p)}")
            self._log(f"MEMORY A: {p}")
        else:
            self._mem_b = p; self._lbl_mem_b.setText(f"B: {readable_name(p)}")
            self._log(f"MEMORY B: {p}")
        if self._mem_a is not None and self._mem_b is not None:
            self._btn_compare.setEnabled(True)

    def _clear_slot(self, slot: str):
        if slot == "A":
            self._mem_a = None; self._lbl_mem_a.setText("A: —"); self._log("MEMORY A cleared")
        else:
            self._mem_b = None; self._lbl_mem_b.setText("B: —"); self._log("MEMORY B cleared")
        if self._mem_a is None or self._mem_b is None:
            self._btn_compare.setEnabled(False)

    def _clear_memory(self, win=None):
        """Clear both memory slots. Optionally close the compare dialog."""
        self._clear_slot("A"); self._clear_slot("B")
        self._log("MEMORY cleared")
        if win:
            try: win.accept()
            except: pass

    # ── COMPARE ───────────────────────────────────────────────────────────────
    def _align_images(self, arr_a: np.ndarray, arr_b: np.ndarray) -> np.ndarray:
        """Phase correlation alignment, max ±15px. Identical to original."""
        def to_gray(arr):
            if arr.ndim == 3: return arr.mean(axis=2).astype(np.float32)
            return arr.astype(np.float32)
        ga = to_gray(arr_a); gb = to_gray(arr_b)
        fa = np.fft.fft2(ga); fb = np.fft.fft2(gb)
        cross = fa * np.conj(fb); denom = np.abs(cross); denom[denom == 0] = 1
        ir = np.fft.ifft2(cross / denom).real
        h, w = ir.shape; MAX_SHIFT = 15; best_val = -np.inf; best_dy = best_dx = 0
        for dy in range(-MAX_SHIFT, MAX_SHIFT + 1):
            for dx in range(-MAX_SHIFT, MAX_SHIFT + 1):
                val = ir[dy % h, dx % w]
                if val > best_val: best_val = val; best_dy = dy; best_dx = dx
        self._log(f"ALIGN: shift dy={best_dy} dx={best_dx} (peak={best_val:.2f})")
        if best_dy == 0 and best_dx == 0: return arr_b
        return np.roll(arr_b, (best_dy, best_dx), axis=(0, 1))

    def _compare_memory(self):
        if self._mem_a is None or self._mem_b is None:
            QMessageBox.information(self, "Compare", "Save two images to memory first."); return

        self._set_busy(True)
        self._compare_sig = _CompareSignals()
        self._compare_sig.done.connect(self._show_compare_window)
        def _on_compare_error(msg: str):
            if self.isVisible():
                QMessageBox.critical(self, "Compare error", msg)
        self._compare_sig.error.connect(_on_compare_error)

        def worker():
            try:
                self._log(f"COMPARE: loading A={self._mem_a.name}")
                self._log(f"COMPARE: loading B={self._mem_b.name}")
                img_a = PilImage.open(self._mem_a); img_b = PilImage.open(self._mem_b)
                arr_a = np.array(img_a).astype(np.float32)
                arr_b = np.array(img_b).astype(np.float32)
                if arr_a.ndim == 3: arr_a = arr_a.mean(axis=2)
                if arr_b.ndim == 3: arr_b = arr_b.mean(axis=2)
                if arr_a.shape != arr_b.shape:
                    self._compare_sig.error.emit(f"Images have different dimensions:\nA: {arr_a.shape}\nB: {arr_b.shape}")
                    return
                arr_b_aligned = self._align_images(arr_a, arr_b)
                diff_ab = arr_a - arr_b_aligned
                diff_ba = arr_b_aligned - arr_a

                def norm(d):
                    d_c = np.clip(d, 0, None); mx = float(d_c.max())
                    if mx == 0: return np.zeros_like(d, dtype=np.uint8)
                    return (d_c / mx * 255).astype(np.uint8)

                img_disp_ab = PilImage.fromarray(norm(diff_ab))
                img_disp_ba = PilImage.fromarray(norm(diff_ba))
                self._compare_sig.done.emit(img_disp_ab, img_disp_ba, diff_ab, diff_ba)
            except Exception as e:
                self._compare_sig.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _show_compare_window(
        self,
        img_ab: PilImage.Image,
        img_ba: PilImage.Image,
        diff_ab_raw: np.ndarray,
        diff_ba_raw: np.ndarray,
    ):
        def readable_label(p: Path) -> str:
            new_stem, _ = build_new_name(p.stem,
                                         use_prague_time=True)
            if new_stem:
                m = re.search(r"(\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d+)$", new_stem)
                if m: return m.group(1).replace("__", "_")
            return p.stem

        label_a = readable_label(self._mem_a)
        label_b = readable_label(self._mem_b)
        stem_a  = Path(label_a).stem
        stem_b  = Path(label_b).stem

        win = QDialog(self)
        win.setWindowTitle(f"Comparison: {label_a}  vs  {label_b}")
        win.resize(1100, 700)

        MAX_W = MAX_H = 460
        bright_ab = [1.0]; bright_ba = [1.0]

        def make_qpixmap(diff_raw: np.ndarray, brightness: float) -> QPixmap:
            d   = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
            arr = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
            img = PilImage.fromarray(arr)
            img.thumbnail((MAX_W, MAX_H), PilImage.Resampling.LANCZOS)
            qimg = QImage(img.tobytes(), img.width, img.height,
                          img.width, QImage.Format.Format_Grayscale8)
            return QPixmap.fromImage(qimg)

        body = QHBoxLayout()

        # A-B column
        col_ab = QFrame(); col_ab.setFrameShape(QFrame.Shape.StyledPanel)
        cl_ab  = QVBoxLayout(col_ab)
        cl_ab.addWidget(QLabel("<b>A \u2212 B</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        lbl_ab = QLabel(); lbl_ab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ab.setPixmap(make_qpixmap(diff_ab_raw, 1.0)); cl_ab.addWidget(lbl_ab, 1)
        ctrl_ab = QHBoxLayout(); ctrl_ab.addWidget(QLabel("Brightness:"))
        sl_ab = QSlider(Qt.Orientation.Horizontal); sl_ab.setRange(1, 1000); sl_ab.setValue(100)
        lbl_ab_val = QLabel("1.00x"); lbl_ab_val.setFixedWidth(46)
        def update_ab(v):
            b = v / 100.0; bright_ab[0] = b; lbl_ab_val.setText(f"{b:.2f}x")
            lbl_ab.setPixmap(make_qpixmap(diff_ab_raw, b))
        sl_ab.valueChanged.connect(update_ab)
        btn_auto_ab = QPushButton("Auto"); btn_auto_ab.setFixedWidth(46)
        btn_auto_ab.clicked.connect(lambda: sl_ab.setValue(100))
        ctrl_ab.addWidget(sl_ab, 1); ctrl_ab.addWidget(lbl_ab_val); ctrl_ab.addWidget(btn_auto_ab)
        cl_ab.addLayout(ctrl_ab); body.addWidget(col_ab, 1)

        # B-A column
        col_ba = QFrame(); col_ba.setFrameShape(QFrame.Shape.StyledPanel)
        cl_ba  = QVBoxLayout(col_ba)
        cl_ba.addWidget(QLabel("<b>B \u2212 A</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        lbl_ba = QLabel(); lbl_ba.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_ba.setPixmap(make_qpixmap(diff_ba_raw, 1.0)); cl_ba.addWidget(lbl_ba, 1)
        ctrl_ba = QHBoxLayout(); ctrl_ba.addWidget(QLabel("Brightness:"))
        sl_ba = QSlider(Qt.Orientation.Horizontal); sl_ba.setRange(1, 1000); sl_ba.setValue(100)
        lbl_ba_val = QLabel("1.00x"); lbl_ba_val.setFixedWidth(46)
        def update_ba(v):
            b = v / 100.0; bright_ba[0] = b; lbl_ba_val.setText(f"{b:.2f}x")
            lbl_ba.setPixmap(make_qpixmap(diff_ba_raw, b))
        sl_ba.valueChanged.connect(update_ba)
        btn_auto_ba = QPushButton("Auto"); btn_auto_ba.setFixedWidth(46)
        btn_auto_ba.clicked.connect(lambda: sl_ba.setValue(100))
        ctrl_ba.addWidget(sl_ba, 1); ctrl_ba.addWidget(lbl_ba_val); ctrl_ba.addWidget(btn_auto_ba)
        cl_ba.addLayout(ctrl_ba); body.addWidget(col_ba, 1)

        def save_png(diff_raw: np.ndarray, brightness: float, label: str):
            default_name = f"diff_{label}_{stem_a}_vs_{stem_b}.png"
            dst, _ = QFileDialog.getSaveFileName(
                win, f"Save {label}", default_name, "PNG Images (*.png)")
            if not dst: return
            d    = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
            arr8 = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
            PilImage.fromarray(arr8).save(dst)
            self._log(f"SAVED {label}: {dst}")

        def save_both():
            dest = QFileDialog.getExistingDirectory(win, "Select folder for both results")
            if not dest: return
            dest_path = Path(dest)
            for diff_raw, brightness, label in (
                (diff_ab_raw, bright_ab[0], "A-B"),
                (diff_ba_raw, bright_ba[0], "B-A"),
            ):
                d    = np.clip(diff_raw, 0, None); mx = float(d.max()) or 1.0
                arr8 = np.clip(d / mx * 255.0 * brightness, 0, 255).astype(np.uint8)
                PilImage.fromarray(arr8).save(dest_path / f"diff_{label}_{stem_a}_vs_{stem_b}.png")
                self._log(f"SAVED {label}: {dest_path}")
            QMessageBox.information(win, "Saved", f"Both saved to:\n{dest_path}")

        btn_row = QHBoxLayout()
        btn_row.addWidget(QPushButton("Save A\u2212B", clicked=lambda: save_png(diff_ab_raw, bright_ab[0], "A-B")))
        btn_row.addWidget(QPushButton("Save B\u2212A", clicked=lambda: save_png(diff_ba_raw, bright_ba[0], "B-A")))
        btn_row.addWidget(QPushButton("Save both",       clicked=save_both))
        btn_row.addStretch(1)
        btn_row.addWidget(QPushButton("Clear memory A/B", clicked=lambda: self._clear_memory(win)))
        btn_row.addWidget(QPushButton("Close",            clicked=win.accept))

        main_lay = QVBoxLayout(win)
        main_lay.addWidget(QLabel(f"<b>A:</b> {label_a}   <b>|</b>   <b>B:</b> {label_b}"))
        main_lay.addLayout(body, 1)
        main_lay.addLayout(btn_row)
        self._set_busy(False)
        win.exec()



# ── MULTI-DAY SETUP DIALOG ────────────────────────────────────────────────────
# _MultiDaySetupDialog lived here: a second multi-day setup window with its own
# calendar, its own inline copy of the stylesheet, a right-click day-pinning
# scheme and a 9/19 "hour fallback". Nothing ever opened it. Multi-day is now
# what the one calendar does when you pick a second day (daypicker rule 3).


def _paint_dialog(dlg) -> None:
    """Give a dialog an explicit light panel and dark ink.

    main.py paints `QWidget { background:#f3f3f3; color:#111 }` over the whole
    application, so a dialog inside the program is already light — but this PC is in
    Windows dark mode, so the SAME dialog opened by a test or a render script comes
    up dark, and a #111 label on it is unreadable. Nothing is left to the theme: the
    dialog states both colours itself."""
    dlg.setStyleSheet(
        "QDialog { background:#f3f3f3; color:#111111; }"
        "QLabel { background:transparent; color:#111111; }")


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet("font-size:10px;color:#444;font-weight:700;letter-spacing:1px;")
    return lbl

# ── PV-REGION SEARCH ──────────────────────────────────────────────────────────
# Reverse of cpva.CHANNEL_MAP: full archiver channel → short preset label.
_PV_PRESET_LABELS: dict[str, str] = {ch: name.upper()
                                     for name, ch in CPVA_CHANNEL_MAP.items()}
# Cache of every archiver channel name (populated once by the Browse dialog).
_PV_CHANNEL_CACHE: "list[str] | None" = None
_PV_REGION_COLORS = ["#C62828", "#2E7D32", "#EF6C00", "#6A1B9A",
                     "#00838F", "#AD1457", "#1565C0", "#37474F"]
# One colour per PV curve, taken by the PV's own place in the list — not by the order
# the checked ones happen to be drawn in, or unchecking one would recolour the rest.
# The first six are One Moment's, so a PV looks the same in both; the rest are there
# because the list is as long as the operator makes it. Past the end of the colours
# the line changes SHAPE (dashed, dotted) rather than repeating a colour outright —
# with seven PVs and six colours, two of them came out identical.
_PV_LINE_COLORS = ["#1565C0", "#C62828", "#2E7D32", "#EF6C00", "#6A1B9A", "#00838F",
                   "#AD1457", "#37474F", "#827717", "#4527A0"]
_PV_LINE_DASHES = ["-", "--", ":", "-."]
# How bad an archiver answer is, worst wins when several days are merged. "empty"
# is a real answer ("nothing was recorded"); "error" is the absence of one.
_PV_STATUS_RANK = {"ok": 0, "empty": 1, "stale": 2, "error": 3}
# A condition like "SBW4 above 5 J" can be true for thousands of samples in a day.
# Only the earliest ones can ever be the answer, so the scan stops counting there;
# and only so many are tested against the cameras, because each new day tested costs
# one TotalPower query per camera.
_COND_MAX_HITS_PER_DAY = 400
_COND_MAX_CANDIDATES = 60
# How many moments to try when every camera's frame comes back blank at the one
# picked. Each try is a fresh set of share reads, so it is not unbounded.
_COND_MAX_FRAME_TRIES = 4
# How far the pointer may travel and still count as a CLICK rather than a drag.
# Measured in screen pixels: a few seconds is an enormous drag on a zoomed-in axis
# and no movement at all across a week.
_PV_CLICK_SLOP_PX = 5


def _day_bounds_ns_for(day) -> "tuple[int, int]":
    """(start, end) of one calendar day in the zone the tab is working in."""
    tz = PRAGUE if PRAGUE is not None else timezone.utc
    t0 = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
    t1 = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=tz)
    return int(t0.timestamp() * 1e9), int(t1.timestamp() * 1e9)


class _SaveViewDialog(QDialog):
    """Format and scope, asked once before the file dialog.

    Two questions with two answers each — a small dialog rather than four more
    buttons on an already crowded panel."""

    def __init__(self, n_tabs: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save view")
        _paint_dialog(self)
        lay = QVBoxLayout(self)

        # A dialog is a plain QWidget, and the app stylesheet paints those LIGHT
        # grey with dark ink — so the dark-sidebar styles do not belong here:
        # `_CHECKBOX_STYLE_DARK` has only a QCheckBox selector (it does nothing at
        # all to a radio button, whose indicator then falls through to Fusion on a
        # dark-mode Windows) and its light ink would be white on near-white.
        def _head(text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setStyleSheet("font-size:10px;color:#555;font-weight:700;"
                              "letter-spacing:1px;")
            return lbl

        lay.addWidget(_head("Format"))
        self._png = QRadioButton("PNG image")
        self._pdf = QRadioButton("PDF document")
        self._png.setChecked(True)
        self._pdf.setToolTip("One page per tab, at full size — nothing scaled down.")
        lay.addWidget(self._png)
        lay.addWidget(self._pdf)
        lay.addWidget(_head("What to save"))
        self._this = QRadioButton("This tab")
        self._all = QRadioButton(f"Every tab  ({n_tabs})")
        self._this.setChecked(True)
        self._all.setEnabled(n_tabs > 1)
        for b in (self._png, self._pdf, self._this, self._all):
            b.setStyleSheet(_RADIO_STYLE)
        lay.addWidget(self._this)
        lay.addWidget(self._all)
        note = QLabel("Rows below the fold are included — the file holds the "
                      "whole view, not what happens to be on screen.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;font-size:10px;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result(self) -> "tuple[str, str]":
        return ("pdf" if self._pdf.isChecked() else "png",
                "all" if self._all.isChecked() else "this")


class _PVBrowseDialog(QDialog):
    """Filterable list of every archiver channel (live discovery via
    cpva.fetch_channels('**')). Returns the selected channel names."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Browse PVs")
        self.resize(460, 520)
        lay = QVBoxLayout(self)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Type to filter channels…")
        self._filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.itemDoubleClicked.connect(lambda *_: self.accept())
        lay.addWidget(self._list, 1)

        self._status = QLabel("Loading channels…")
        self._status.setStyleSheet("color:#666;font-size:10px;")
        lay.addWidget(self._status)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        class _Sig(QObject):
            done = Signal(object)
        self._sig = _Sig()
        self._sig.done.connect(self._on_channels)

        self._all: list[str] = []
        if _PV_CHANNEL_CACHE is not None:
            self._on_channels(_PV_CHANNEL_CACHE)
        else:
            threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        global _PV_CHANNEL_CACHE
        try:
            # The cached listing, the one the Slider's picker and the Shot Finder
            # already use — the ~9700 names were being re-fetched here every session.
            chans = cpva.fetch_channels_cached("**")
        except Exception as e:
            try:
                self._sig.done.emit(e)
            except RuntimeError:
                pass
            return
        _PV_CHANNEL_CACHE = sorted(chans)
        try:
            self._sig.done.emit(_PV_CHANNEL_CACHE)
        except RuntimeError:
            pass

    def _on_channels(self, payload):
        if isinstance(payload, Exception):
            self._status.setText(f"Failed to load channels: {payload}")
            return
        self._all = list(payload)
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str):
        """Rank the channels with the SHARED PV ranker.

        This box used to do a plain "is this substring in the name" test, so it was
        the one PV search in the program that behaved differently from the others:
        typing two words found nothing, and the best match was wherever the alphabet
        happened to put it. `cpva.rank_pv_match` is what the Image Slider's picker
        and the Shot Finder use — words are tokens, AND-matched anywhere in the name,
        with the order they were typed in worth a bonus."""
        q = cpva.split_query(text or "")
        self._list.clear()
        if not q:
            ranked = [(0, ch) for ch in self._all]
        else:
            ranked = []
            for ch in self._all:
                score = cpva.rank_pv_match(ch, ch, q)
                if score is not None:
                    ranked.append((score, ch))
            ranked.sort(key=lambda t: (t[0], t[1].lower()))
        shown = 0
        for _score, ch in ranked:
            self._list.addItem(QListWidgetItem(ch))
            shown += 1
            if shown >= 500:
                break
        self._status.setText(f"{shown} shown / {len(self._all)} channels"
                             + ("  (capped at 500 — refine filter)" if shown >= 500 else ""))

    def selected_channels(self) -> list[str]:
        return [it.text() for it in self._list.selectedItems()]


class PVRegionSearchDialog(QDialog):
    """Manual image search over as many days as are marked.

    Mark the days in the calendar, plot one or more PVs for them, and either drag
    time regions on the graph — one frame per region, taken from the PEAK of the
    primary PV inside it — or state a numeric condition ("SBW4 above 13 J") and let
    it name the moment. Modelled on the Spectra tab in the CSS Logger.

    The graph reads either way: "One day" shows the day the arrows are on, "All
    days" puts the marked days next to each other on one axis, so two days a week
    apart sit side by side with no empty week between them.
    """

    def __init__(self, cams: list, initial_qdates, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PV Search")
        self.resize(1180, 800)
        self._cams = cams
        self._regions: list[dict] = []      # {id, t_start_ns, t_end_ns, color, day}
        self._region_seq = 0
        # day → {channel → [(t_ns, value), …]}. One entry per marked day; the graph
        # reads whichever days it is showing.
        self._series: "dict[object, dict[str, list]]" = {}
        self._day_status: "dict[object, str]" = {}   # day → ok | empty | stale | error
        # channel → {"name", "unit", "step"} — what the PV registry and the archiver
        # know about a channel. `step` says the channel is written only when it
        # CHANGES, which is what decides whether its curve is held across a gap.
        self._ch_meta: "dict[str, dict]" = {}
        # (channel, day) → (ts_ns, value) — the value a step channel was already
        # sitting at when the day opened. Without it a setting that last moved
        # yesterday draws as nothing at all today.
        self._seeds: dict = {}
        # Extra y axes (one per unit beyond the first). Kept so a redraw can take
        # them off the figure — they belong to the drawing, not to the window.
        self._axes_extra: list = []
        self._pv_colour: "dict[str, str]" = {}
        # Channels the operator has put on an axis of their own — see _unit_key_for.
        self._own_axis: "set[str]" = set()
        # formula name → why it has no curve (unbound letter, no source, too many
        # points). Printed rather than left as a silent gap.
        self._derived_reason: "dict[str, str]" = {}
        # channel → the OTHER archived name that actually answered for it. SBW4's
        # two names take turns, so a curve may legitimately come from a name nobody
        # picked; the window says which, rather than quietly drawing it.
        self._alias_used: "dict[str, str]" = {}
        self._load_gen = 0
        self._span = None                   # left drag — mark a region
        self._zoom_span = None              # right drag — zoom the time axis
        # What the strip of controls under the graph is set to. `ax.clear()` throws
        # all of it away on every redraw, so it lives here and is re-stated by
        # `_apply_graph_opts` — it is state, not a one-off call.
        self._show_grid = True
        self._show_legend = True
        self._log_y = False
        self._y_lim: "tuple | None" = None       # hand-set value range
        self._x_lim_user: "tuple | None" = None  # hand-set time range
        self._tick_min = 0                       # minutes between time labels
        self._mode = "one"                  # "one" (a day at a time) | "all"
        self._focus_i = 0                   # index into self._days in "one" mode
        # The moments picked by clicking the graph, in the order they were clicked.
        # The transpose of a region: a region asks "find me the best frame in here",
        # a moment says "this one".
        #
        # A LIST, not one moment: every click adds another timestamp, and a pick
        # made on one day survives switching the graph to another day, so the set
        # being searched is built up across as many days as are marked. Undo
        # (Ctrl+Z or the Undo button) is what takes one back off.
        self._moments: "list[int]" = []
        # Snapshots of (moments, regions) — one per picking gesture, for Undo.
        self._pick_undo: list = []
        self._press_x: "float | None" = None
        self._xlim_stack: list = []          # right-drag zoom history

        # The days to work on. A single QDate is still accepted so the old call
        # ("search this day") keeps working.
        if isinstance(initial_qdates, QDate):
            initial_qdates = [initial_qdates]
        self._days: list = sorted({datetime(q.year(), q.month(), q.day()).date()
                                   for q in (initial_qdates or [])})
        if not self._days:
            _t = QDate.currentDate()
            self._days = [datetime(_t.year(), _t.month(), _t.day()).date()]

        # Lazy matplotlib import (keeps module import time low).
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_qtagg import (
            FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT)
        from matplotlib.widgets import SpanSelector
        import matplotlib.dates as mdates
        self._Figure, self._FigureCanvas = Figure, FigureCanvas
        self._NavToolbar, self._SpanSelector = NavigationToolbar2QT, SpanSelector
        self._mdates = mdates

        class _Sig(QObject):
            done = Signal(object)
        self._sig = _Sig()
        self._sig.done.connect(self._on_series_loaded)

        self._build_ui()
        self._seed_default_pvs()
        self._sync_pv_buttons()
        self._refresh_day_list()
        self._sync_moment_label()
        self._reload_series()

    # ── The graph's own controls ────────────────────────────────────────────
    def _build_graph_controls(self, parent_lay):
        """Grid, legend, log, the two ranges, the tick spacing, copy and save — as
        BUTTONS under the graph.

        One Moment kept all of this on four right-click menus. The operator's
        decision (04.09.2026) was not to port them: a menu that only appears if you
        already know it is there is not a control, and the right button now belongs
        to zooming. So every setting the menus offered sits on the strip below."""
        row = QHBoxLayout(); row.setSpacing(6)
        self._cb_grid = QCheckBox("Grid")
        self._cb_grid.setChecked(True)
        self._cb_grid.setToolTip("Lines across the plot at every tick.")
        self._cb_legend = QCheckBox("Legend")
        self._cb_legend.setChecked(True)
        self._cb_legend.setToolTip("The box naming the curves.")
        self._cb_logy = QCheckBox("Log Y")
        self._cb_logy.setToolTip(
            "Every y axis logarithmic. Values at or below zero cannot be drawn on "
            "a log axis and are left out.")
        for cb in (self._cb_grid, self._cb_legend, self._cb_logy):
            # DARK ink — the whole window is light. The light-ink variant of this
            # style left three labelled boxes with no visible labels.
            cb.setStyleSheet(_CHECKBOX_STYLE)
            cb.toggled.connect(self._on_graph_opt)
            row.addWidget(cb)
        row.addSpacing(8)

        def _btn(text, tip, slot):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton { background:#e8e8e8; color:#111;"
                " border:1px solid #9a9a9a; border-radius:3px; padding:1px 8px;"
                " font-size:11px; }"
                "QPushButton:hover:enabled { background:#ffffff; }"
                "QPushButton:disabled { background:#dcdcdc; color:#8a8a8a;"
                " border:1px solid #bdbdbd; }")
            b.clicked.connect(slot)
            row.addWidget(b)
            return b

        self._btn_yrange = _btn(
            "Y range", "Set the value axis by hand, or put it back to automatic.",
            self._edit_y_range)
        self._btn_trange = _btn(
            "Time range", "Show only part of the day.", self._edit_time_range)
        self._btn_ticks = _btn(
            "Ticks", "How far apart the time labels are.", self._edit_ticks)
        self._btn_reset_view = _btn(
            "Whole day", "Undo every zoom and hand-set range.", self._reset_view)
        row.addStretch(1)
        self._btn_copy_graph = _btn(
            "Copy", "The graph to the clipboard, as a picture.", self._copy_graph)
        self._btn_save_graph = _btn(
            "Save", "The graph to a PNG file.", self._save_graph)
        parent_lay.addLayout(row)

    def _on_graph_opt(self, *_):
        self._show_grid = self._cb_grid.isChecked()
        self._show_legend = self._cb_legend.isChecked()
        self._log_y = self._cb_logy.isChecked()
        self._redraw()

    def _apply_graph_opts(self, axes: list, handles: list, labels: list):
        """The settings from the strip, applied to a drawing that was just built.

        Called at the END of every redraw: `ax.clear()` throws all of this away, so
        it has to be re-stated rather than set once. Note `grid(False, **style)`
        turns the grid ON — the flag has to be passed on its own."""
        ax = self._ax
        if self._show_grid:
            ax.grid(True, alpha=0.25)
        else:
            ax.grid(False)
        if self._log_y:
            for a in axes:
                try:
                    a.set_yscale("log")
                except Exception:
                    pass
        if self._show_legend and handles:
            ax.legend(handles=handles, labels=labels, loc="upper right", fontsize=8)
        elif ax.get_legend() is not None:
            ax.get_legend().remove()
        # A hand-set value range beats the automatic one, and survives a redraw.
        if self._y_lim is not None and axes:
            try:
                axes[0].set_ylim(*self._y_lim)
            except Exception:
                pass
        if self._tick_min:
            try:
                from matplotlib.ticker import MultipleLocator
                # Both x mappings are in DAYS (one-day mode is an mdates number,
                # all-days is base + day index + fraction), so one conversion works
                # for both.
                ax.xaxis.set_major_locator(
                    MultipleLocator(self._tick_min / 1440.0))
            except Exception:
                pass
        if self._x_lim_user is not None:
            try:
                ax.set_xlim(*self._x_lim_user)
            except Exception:
                pass

    def _edit_y_range(self):
        """The value axis by hand. Empty boxes mean automatic."""
        axes = [self._ax] + list(self._axes_extra)
        lo, hi = axes[0].get_ylim()
        dlg = QDialog(self)
        dlg.setWindowTitle("Y range")
        _paint_dialog(dlg)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Leave a box empty for automatic."))
        grid = QHBoxLayout()
        e_lo, e_hi = QLineEdit(), QLineEdit()
        for e, v in ((e_lo, lo), (e_hi, hi)):
            e.setPlaceholderText("auto")
            if self._y_lim is not None:
                e.setText(f"{v:g}")
            e.setStyleSheet("background:#ffffff;color:#111111;")
        grid.addWidget(QLabel("from")); grid.addWidget(e_lo)
        grid.addWidget(QLabel("to")); grid.addWidget(e_hi)
        lay.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            a = float(e_lo.text().replace(",", ".")) if e_lo.text().strip() else None
            b = float(e_hi.text().replace(",", ".")) if e_hi.text().strip() else None
        except ValueError:
            QMessageBox.information(self, "Y range", "That is not a number.")
            return
        self._y_lim = None if (a is None or b is None or a == b) else (min(a, b),
                                                                      max(a, b))
        self._redraw()

    def _edit_time_range(self):
        """Part of the day, typed as two clock times."""
        day = self._focus_day() if self._mode == "one" else None
        if day is None:
            QMessageBox.information(
                self, "Time range",
                "Switch to One day first — a time range means one day.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Time range")
        _paint_dialog(dlg)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"{_fmt_day_long(day)}   (Prague time)"))
        row = QHBoxLayout()
        e_a, e_b = QLineEdit(), QLineEdit()
        for e, txt in ((e_a, "08:00"), (e_b, "19:00")):
            e.setPlaceholderText(txt)
            e.setStyleSheet("background:#ffffff;color:#111111;")
        row.addWidget(QLabel("from")); row.addWidget(e_a)
        row.addWidget(QLabel("to")); row.addWidget(e_b)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        def _parse(txt, dflt_h):
            txt = (txt or "").strip()
            if not txt:
                return dflt_h, 0, 0
            parts = [p for p in re.split(r"[:.\s]+", txt) if p]
            try:
                nums = [int(p) for p in parts[:3]]
            except ValueError:
                raise ValueError(txt)
            while len(nums) < 3:
                nums.append(0)
            return nums[0], nums[1], nums[2]

        try:
            h0, m0, s0 = _parse(e_a.text(), 8)
            h1, m1, s1 = _parse(e_b.text(), 19)
        except ValueError:
            QMessageBox.information(self, "Time range",
                                    "Type the times as HH:MM.")
            return
        tz = self._tz()
        a_ns = int(datetime(day.year, day.month, day.day,
                            min(h0, 23), min(m0, 59), min(s0, 59),
                            tzinfo=tz).timestamp() * 1e9)
        b_ns = int(datetime(day.year, day.month, day.day,
                            min(h1, 23), min(m1, 59), min(s1, 59),
                            tzinfo=tz).timestamp() * 1e9)
        if b_ns <= a_ns:
            QMessageBox.information(self, "Time range",
                                    "The second time must be later than the first.")
            return
        self._x_lim_user = (self._ns_to_x(a_ns, day), self._ns_to_x(b_ns, day))
        self._redraw()

    def _edit_ticks(self):
        """How far apart the time labels sit. 0 = let matplotlib decide."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Ticks")
        _paint_dialog(dlg)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Minutes between time labels (0 = automatic):"))
        sp = QSpinBox()
        sp.setRange(0, 720)
        sp.setSingleStep(5)
        sp.setValue(int(self._tick_min or 0))
        sp.setStyleSheet("background:#ffffff;color:#111111;")
        lay.addWidget(sp)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._tick_min = int(sp.value())
        self._redraw()

    def _reset_view(self):
        """Back to the whole day, automatic axes, no zoom."""
        self._y_lim = None
        self._x_lim_user = None
        self._tick_min = 0
        self._xlim_stack = []
        self._redraw()

    def _copy_graph(self):
        try:
            QApplication.clipboard().setPixmap(self._canvas.grab())
            self._status.setText("Graph copied to the clipboard.")
        except Exception as e:
            QMessageBox.warning(self, "Copy", f"Could not copy the graph:\n{e}")

    def _save_graph(self):
        try:
            sl = _get_slider_module()
            start = sl._default_save_dir()
        except Exception:
            start = str(Path.home())
        day = self._focus_day()
        stem = "pv_graph" + (f"_{day.strftime('%Y-%m-%d')}" if day else "")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save graph", str(Path(start) / (stem + ".png")),
            "PNG image (*.png)")
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=200, facecolor="white")
            self._status.setText(f"Graph saved: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Save graph", f"Could not save:\n{e}")

    # ── Statistics of the marked range ──────────────────────────────────────
    def _build_stats(self, parent_lay):
        """Count, mean, spread and the extremes over one marked region.

        A PV with NO sample in the range still gets a row: the value it was already
        sitting at, held forward and said out loud in amber. Three dashes there read
        as "this channel is broken", which is the one thing the range must not say
        about a setting that simply did not move."""
        head = QHBoxLayout(); head.setSpacing(6)
        head.addWidget(_section_label("Marked range"))
        self._stats_cb = QComboBox()
        self._stats_cb.setMinimumWidth(260)
        self._stats_cb.setToolTip("Which marked region the numbers are for.")
        self._stats_cb.currentIndexChanged.connect(lambda *_: self._refresh_stats())
        head.addWidget(self._stats_cb, 1)
        self._lbl_range = QLabel("")
        self._lbl_range.setStyleSheet("color:#333333;font-size:11px;")
        head.addWidget(self._lbl_range, 0)
        parent_lay.addLayout(head)

        self._stat_table = QTableWidget(0, 6)
        self._stat_table.setHorizontalHeaderLabels(
            ["PV", "n", "Mean", "± Std", "Min", "Max"])
        hh = self._stat_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setHighlightSections(False)
        self._stat_table.verticalHeader().setVisible(False)
        self._stat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._stat_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._stat_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stat_table.setWordWrap(False)
        # A table has to look like a table: light cells, dark text, a header band
        # that is visibly a header. Left to the style it comes out of Windows dark
        # mode black on black.
        self._stat_table.setStyleSheet(
            "QTableWidget { font-size: 11px; background: #ffffff; color: #111111;"
            "  gridline-color: #dfe3e8; border: 1px solid #c4c8cf; }"
            "QTableWidget::item { padding: 0px 3px; color: #111111; }"
            "QHeaderView { background: #e8ebef; }"
            "QHeaderView::section { background: #e8ebef; color: #1e2530;"
            "  font-weight: 600; padding: 2px 3px; border: 0px;"
            "  border-right: 1px solid #d0d5db; border-bottom: 1px solid #c4c8cf; }"
            "QTableCornerButton::section { background: #e8ebef; border: 0px; }")
        self._stat_table.setToolTip(
            "Count, mean, spread and the extremes over the marked region. Hover a "
            "row for the median, the peak-to-peak, the first and last value and "
            "the trend across the range.")
        parent_lay.addWidget(self._stat_table)

    def _stats_region(self) -> "dict | None":
        rid = self._stats_cb.currentData()
        for r in self._regions:
            if r["id"] == rid:
                return r
        return None

    def _refresh_stats_combo(self):
        """One entry per marked region, in the sidebar's own order and numbering.

        A region that has just been dragged takes the selection: its numbers are
        what the drag was for. An older pick keeps it, so reading one range is not
        interrupted by a redraw."""
        prev = self._stats_cb.currentData()
        known = getattr(self, "_stats_ids", set())
        self._stats_cb.blockSignals(True)
        self._stats_cb.clear()
        by_day = self._regions_by_day()
        ids = set()
        for day in sorted(by_day.keys()):
            for i, r in enumerate(by_day[day], 1):
                self._stats_cb.addItem(
                    f"{day.strftime('%d.%m.')}  {i})  {self._fmt_region_span(r)}",
                    r["id"])
                ids.add(r["id"])
        fresh = [r["id"] for r in self._regions if r["id"] not in known]
        idx = -1
        if fresh:
            idx = self._stats_cb.findData(fresh[-1])
        if idx < 0:
            idx = self._stats_cb.findData(prev)
        if idx < 0 and self._regions:
            idx = self._stats_cb.findData(self._regions[-1]["id"])
        if idx >= 0:
            self._stats_cb.setCurrentIndex(idx)
        self._stats_cb.blockSignals(False)
        self._stats_ids = ids

    def _samples_in(self, channel: str, day, lo: int, hi: int) -> "list[tuple]":
        series = (self._series.get(day) or {}).get(channel) or []
        return [(t, v) for (t, v) in series if lo <= t <= hi]

    def _last_before(self, channel: str, day, ts_ns: int) -> "tuple | None":
        """(value, timestamp) of the newest sample of `channel` at or before
        `ts_ns` — what makes a setting-shaped channel readable.

        A waveplate angle is archived when it MOVES, so a range can hold not one
        sample of it while its value was perfectly well defined throughout: the
        last sample before the range IS the value during it."""
        series = (self._series.get(day) or {}).get(channel) or []
        for t, v in reversed(series):
            if t <= ts_ns:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv):
                    return fv, int(t)
        seed = self._seeds.get((channel, day))
        if seed and seed[0] <= ts_ns:
            return float(seed[1]), int(seed[0])
        return None

    def _refresh_stats(self):
        """Fill the table for the region the combo is on."""
        if not hasattr(self, "_stat_table"):
            return
        r = self._stats_region()
        names = [(lbl, ch) for lbl, ch in self._checked_channels()]
        if r is None or not names:
            self._stat_table.setRowCount(0)
            self._lbl_range.setText(
                "Drag on the graph to mark a region." if names
                else "No PV checked.")
            return
        day, lo, hi = r["day"], int(r["t_start_ns"]), int(r["t_end_ns"])
        self._lbl_range.setText(
            f"{self._local_dt(lo).strftime('%d.%m. %H:%M:%S')} → "
            f"{self._local_dt(hi).strftime('%H:%M:%S')}")
        self._stat_table.setRowCount(len(names))
        for row, (label, ch) in enumerate(names):
            unit = self._pv_meta_for(ch).get("unit") or ""
            it = QTableWidgetItem(label)
            it.setForeground(QColor(self._colour_for(ch)))
            it.setToolTip(ch + (f"   [{unit}]" if unit else ""))
            self._stat_table.setItem(row, 0, it)
            vals = [float(v) for (_t, v) in self._samples_in(ch, day, lo, hi)
                    if _is_finite(v)]
            times = [t for (t, v) in self._samples_in(ch, day, lo, hi)
                     if _is_finite(v)]
            if not vals:
                self._fill_held_row(row, label, ch, day, lo, unit)
            else:
                arr = np.asarray(vals, dtype=float)
                std = float(arr.std(ddof=1)) if arr.size > 1 else float("nan")
                tip = self._stats_tip(label, unit, arr, times, std)
                for c, txt in ((1, str(arr.size)),
                               (2, f"{arr.mean():.4g}"),
                               (3, "—" if arr.size < 2 else f"{std:.3g}"),
                               (4, f"{arr.min():.4g}"),
                               (5, f"{arr.max():.4g}")):
                    cell = QTableWidgetItem(txt)
                    cell.setForeground(QColor("#111111"))
                    cell.setToolTip(tip)
                    self._stat_table.setItem(row, c, cell)
            self._stat_table.setRowHeight(row, 20)
        head_h = self._stat_table.horizontalHeader().height()
        self._stat_table.setMaximumHeight(20 * max(len(names), 1) + head_h + 6)

    def _stats_tip(self, label: str, unit: str, y, t: list, std: float) -> str:
        """What the range DID, for the row's tooltip — the six columns only say how
        much it moved."""
        head = f"{label}  [{unit}]" if unit else label
        bits = [head, f"n = {y.size}", f"mean = {y.mean():.6g}"]
        if y.size > 1:
            bits.append(f"std = {std:.6g}")
        bits += [f"median = {float(np.median(y)):.6g}",
                 f"min = {y.min():.6g}", f"max = {y.max():.6g}",
                 f"peak-to-peak = {float(y.max() - y.min()):.6g}"]
        if y.size > 1 and len(t) > 1:
            bits.append(f"first = {float(y[0]):.6g}   →   last = {float(y[-1]):.6g}")
            span_s = (t[-1] - t[0]) / 1e9
            if span_s > 0:
                # Per minute, not per second: these channels move over shots, and a
                # rate per second reads as zero for everything but a fast sensor.
                rate = (float(y[-1]) - float(y[0])) / span_s * 60.0
                bits.append(f"trend = {rate:+.4g} {unit or 'units'} per minute")
        return "\n".join(bits)

    def _fill_held_row(self, row: int, label: str, channel: str, day, lo: int,
                       unit: str):
        """A PV with no sample inside the range: the value from before it, held.

        Amber, `n = 0` and the word "held" rather than a spread, so it can never be
        read as a mean of samples that are not there."""
        got = self._last_before(channel, day, lo)
        if got is None:
            tip = ("No sample of this PV inside the marked range, and none before "
                   "it either — nothing was archived for it up to this point.")
            cells = (("0", "#888888"), ("—", "#888888"), ("—", "#888888"),
                     ("—", "#888888"), ("—", "#888888"))
        else:
            val, at_ns = got
            age_s = (lo - at_ns) / 1e9
            tip = (f"{label}  [{unit}]\n" if unit else f"{label}\n")
            tip += ("No sample inside the marked range.\n"
                    f"Last value before it: {val:.6g}\n"
                    f"archived at {self._local_dt(at_ns).strftime('%d.%m. %H:%M:%S')}\n"
                    f"{age_s / 60.0:.1f} minutes before the range starts\n"
                    "Held forward — this channel is written when it changes, so "
                    "that is its value over the whole range.")
            # The held value IS the smallest and the largest over the range, since
            # it never moved; written into all three so the extremes cannot read as
            # "unknown" for a channel that is perfectly fine.
            cells = (("0", "#8a6114"), (f"{val:.4g}", "#8a6114"),
                     ("held", "#8a6114"), (f"{val:.4g}", "#8a6114"),
                     (f"{val:.4g}", "#8a6114"))
        for c, (txt, ink) in enumerate(cells, start=1):
            cell = QTableWidgetItem(txt)
            cell.setForeground(QColor(ink))
            cell.setToolTip(tip)
            self._stat_table.setItem(row, c, cell)

    # ── The marked days ─────────────────────────────────────────────────────
    def _focus_day(self):
        """The day the graph is on in One-day mode."""
        if not self._days:
            return None
        self._focus_i = max(0, min(self._focus_i, len(self._days) - 1))
        return self._days[self._focus_i]

    def _qdates(self) -> "list[QDate]":
        return [QDate(d.year, d.month, d.day) for d in self._days]

    def _tz(self):
        return PRAGUE if PRAGUE is not None else timezone.utc

    def _day_bounds_for(self, day) -> "tuple[int, int]":
        tz = self._tz()
        t0 = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
        t1 = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=tz)
        return int(t0.timestamp() * 1e9), int(t1.timestamp() * 1e9)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setSpacing(8)

        # ── Left sidebar ──────────────────────────────────────────────────
        side = QVBoxLayout()
        side.setSpacing(6)
        # A margin of its own: with none, the first caption ("DAYS") sat hard against
        # the top edge of the scroll area and its upper half was cut off.
        side.setContentsMargins(2, 4, 4, 2)

        # Days — the same multi-select calendar and the same click rules as the
        # tab's own Time window, so a day is picked the same way everywhere.
        side.addWidget(_section_label("Days"))
        self._cal_frame, self._cal = _make_multiselect_calendar(
            QDate(self._days[0].year, self._days[0].month, self._days[0].day))
        self._cal.setMinimumWidth(260)
        self._cal.setMaximumHeight(172)
        self._last_cal_click = QDate(self._days[0].year, self._days[0].month,
                                     self._days[0].day)
        self._cal.clicked.connect(self._on_cal_clicked)
        side.addWidget(self._cal_frame)

        # The Mon–Sun gate for a Ctrl+Shift stretch — the same row, in the same
        # place, as in the Time window dialog. Mon–Fri on, so a stretch across
        # three weeks skips the weekends unless they are ticked here.
        _gate_lbl = QLabel("Ctrl+Shift stretch adds:")
        _gate_lbl.setStyleSheet(daypicker.SECTION_STYLE)
        side.addWidget(_gate_lbl)
        _gate_row, self._wd_checks = daypicker.weekday_gate_row()
        side.addWidget(_gate_row)

        # One row per marked day with how many regions sit on it — a multi-day pick
        # is otherwise invisible once the graph is on a single day.
        self._day_list = QListWidget()
        self._day_list.setMaximumHeight(92)
        self._day_list.setToolTip("The marked days. Click one to show it in the graph.")
        self._day_list.setStyleSheet(
            "QListWidget { background:#ffffff; color:#111; border:1px solid #b0b0b0; }"
            "QListWidget::item:selected { background:#1565C0; color:#ffffff; }")
        self._day_list.currentRowChanged.connect(self._on_day_row_changed)
        side.addWidget(self._day_list)

        self._lbl_tz = QLabel("Prague time")
        self._lbl_tz.setStyleSheet("color:#555555;font-size:10px;")
        side.addWidget(self._lbl_tz)

        # PV list
        side.addWidget(_section_label("PVs to plot"))
        self._pv_list = QListWidget()
        # Tall enough to show the presets AND a couple of added PVs: a new row
        # appended below the fold of a five-row box is the reason adding a PV
        # looked like it had done nothing. White cells, dark ink, a visible
        # scroll bar — this PC is in Windows dark mode, so nothing unpainted is
        # legible.
        self._pv_list.setMinimumHeight(150)
        self._pv_list.setMaximumHeight(190)
        self._pv_list.setStyleSheet(
            "QListWidget { background:#ffffff; color:#111111;"
            " border:1px solid #b0b0b0; }"
            "QListWidget::item { padding:1px 2px; }"
            "QListWidget::item:selected { background:#1565C0; color:#ffffff; }"
            "QScrollBar:vertical { background:#e8e8e8; width:12px; }"
            "QScrollBar::handle:vertical { background:#8a8a8a;"
            " min-height:20px; border-radius:3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0px; }")
        self._pv_list.setToolTip(
            "The PVs the graph draws. The tick plots one; Browse adds any archiver "
            "channel, Remove takes the selected one off the list.")
        self._pv_list.itemChanged.connect(self._on_pv_checks_changed)
        side.addWidget(self._pv_list)
        pv_btns = QHBoxLayout()
        btn_browse = QPushButton("Browse")
        btn_browse.setToolTip("Search all archiver channels")
        btn_browse.clicked.connect(self._browse_pvs)
        btn_remove = QPushButton("Remove")
        btn_remove.setToolTip("Take the selected PV(s) off the list.")
        btn_remove.clicked.connect(self._remove_selected_pv)
        pv_btns.addWidget(btn_browse); pv_btns.addWidget(btn_remove)
        side.addLayout(pv_btns)

        # Per-PV settings, on the PV list itself. One Moment kept these on a
        # right-click menu over the curve; the right button belongs to zooming now,
        # and a menu nobody knows is there is not a control.
        pv_btns2 = QHBoxLayout()
        self._btn_own_axis = QPushButton("Own axis")
        self._btn_own_axis.setCheckable(True)
        self._btn_own_axis.setToolTip(
            "Give the selected PV a value axis of its own. Two PVs in the same "
            "unit but three orders of magnitude apart share an axis on which "
            "neither can be read.")
        self._btn_own_axis.clicked.connect(self._toggle_own_axis)
        self._btn_pv_edit = QPushButton("Edit")
        self._btn_pv_edit.setToolTip(
            "The selected PV's name, unit and limits — the same settings the "
            "Image Slider keeps.")
        self._btn_pv_edit.clicked.connect(self._edit_selected_pv)
        pv_btns2.addWidget(self._btn_own_axis); pv_btns2.addWidget(self._btn_pv_edit)
        side.addLayout(pv_btns2)
        self._pv_list.currentItemChanged.connect(lambda *_: self._sync_pv_buttons())

        prim_row = QHBoxLayout()
        prim_row.addWidget(QLabel("Primary (peak):"))
        self._primary_cb = QComboBox()
        self._primary_cb.setToolTip("PV whose peak inside a region defines the target time")
        prim_row.addWidget(self._primary_cb, 1)
        side.addLayout(prim_row)

        # ── Condition ─────────────────────────────────────────────────────
        # The other way of naming a moment: instead of dragging a span by eye, say
        # what the value had to be and let the search find the first time it was.
        side.addWidget(_section_label("Condition"))
        self._cond_on = QCheckBox("Find the first moment a PV was…")
        self._cond_on.setStyleSheet(_CHECKBOX_STYLE)
        self._cond_on.setToolTip(
            "Instead of the marked regions, search for the first moment the PV met "
            "this condition — the SAME moment for every camera.")
        self._cond_on.toggled.connect(self._on_cond_toggled)
        side.addWidget(self._cond_on)

        cond_row = QHBoxLayout(); cond_row.setSpacing(4)
        self._cond_pv_cb = QComboBox()
        self._cond_pv_cb.setToolTip("Which PV the condition is about")
        self._cond_pv_cb.setMinimumWidth(90)
        self._cond_op_cb = QComboBox()
        for _op, _lbl in ((">", ">"), (">=", "≥"), ("<", "<"), ("<=", "≤"),
                          ("between", "between")):
            self._cond_op_cb.addItem(_lbl, _op)
        self._cond_op_cb.setFixedWidth(74)
        self._cond_op_cb.currentIndexChanged.connect(self._sync_cond_row)
        self._cond_val = QDoubleSpinBox()
        self._cond_val.setRange(-1e9, 1e9); self._cond_val.setDecimals(2)
        self._cond_val.setValue(13.0)
        self._cond_val2 = QDoubleSpinBox()
        self._cond_val2.setRange(-1e9, 1e9); self._cond_val2.setDecimals(2)
        self._cond_val2.setValue(20.0)
        self._cond_val2.setVisible(False)
        for _w in (self._cond_val, self._cond_val2):
            _w.setMinimumWidth(70)
        cond_row.addWidget(self._cond_pv_cb, 1)
        cond_row.addWidget(self._cond_op_cb, 0)
        cond_row.addWidget(self._cond_val, 0)
        cond_row.addWidget(self._cond_val2, 0)
        side.addLayout(cond_row)

        scope_row = QHBoxLayout(); scope_row.setSpacing(6)
        self._cond_scope_days = QRadioButton("whole days")
        self._cond_scope_regs = QRadioButton("marked regions only")
        self._cond_scope_days.setChecked(True)
        for _rb in (self._cond_scope_days, self._cond_scope_regs):
            _rb.setStyleSheet(_RADIO_STYLE + "QRadioButton { font-size:11px; }")
            scope_row.addWidget(_rb)
        scope_row.addStretch(1)
        side.addLayout(scope_row)
        self._cond_widgets = [self._cond_pv_cb, self._cond_op_cb, self._cond_val,
                              self._cond_val2, self._cond_scope_days,
                              self._cond_scope_regs]
        self._on_cond_toggled(False)

        # Regions
        side.addWidget(_section_label("Regions"))
        reg_scroll = QScrollArea()
        reg_scroll.setWidgetResizable(True)
        reg_scroll.setMaximumHeight(132)
        self._regions_host = QWidget()
        self._regions_lay = QVBoxLayout(self._regions_host)
        self._regions_lay.setContentsMargins(0, 0, 0, 0)
        self._regions_lay.setSpacing(2)
        reg_scroll.setWidget(self._regions_host)
        side.addWidget(reg_scroll)
        btn_clear = QPushButton("Clear all regions")
        btn_clear.clicked.connect(self._clear_regions)
        side.addWidget(btn_clear)

        # The sidebar is taller than a small screen now (calendar + days + PVs +
        # condition + regions), so its middle scrolls instead of squashing its rows.
        # The host keeps a real minimum width — a QScrollArea's own sizeHint does not.
        side_w = QWidget(); side_w.setLayout(side)
        side_w.setMinimumWidth(292)
        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side_scroll.setWidget(side_w)

        # THE MOMENT, and the status and the Search button, sit OUTSIDE the scroll,
        # pinned to the bottom. Scrolling to find the button that runs the search is
        # the one thing this panel must never make anyone do — and the moment that
        # was just clicked is the answer the whole window exists to produce, so it
        # cannot be somewhere below the fold either.
        # Two rows, not one: with the buttons beside it the label had about 150 px
        # and the count was elided to "3 moments on 1 day   ·   last ↑" — the thing
        # the window exists to report, cut off. The label owns its own full-width
        # row and the buttons sit under it.
        mom_col = QVBoxLayout(); mom_col.setSpacing(3)
        mom_row = QHBoxLayout(); mom_row.setSpacing(4)
        self._lbl_moment = QLabel("No moment picked.")
        self._lbl_moment.setWordWrap(True)
        # The answer the window exists to produce, so it wears a colour of its own —
        # a pale amber band with DARK ink. The amber-on-near-black version of this
        # block came from the dark port and was the brightest thing in a light panel.
        self._lbl_moment.setStyleSheet(
            "QLabel { background:#fff3c4; color:#3a2c00; border:1px solid #d6b656;"
            " border-radius:3px; padding:3px 6px; font-size:12px;"
            " font-weight:600; }")
        self._lbl_moment.setToolTip(
            "Click the graph to pick a moment. Every click adds one more — on this "
            "day or on any other marked day — and they are all searched together. "
            "Each pick snaps to the nearest real sample of the primary PV: a time "
            "between two samples has no shot behind it.")
        self._btn_undo_pick = QPushButton("Undo")
        self._btn_undo_pick.setFixedWidth(72)
        self._btn_undo_pick.setToolTip(
            "Take the last pick back — a moment or a marked region  (Ctrl+Z).")
        self._btn_undo_pick.setEnabled(False)
        self._btn_undo_pick.clicked.connect(self._undo_pick)
        _set_action_icon(self._btn_undo_pick, "undo")
        self._btn_clear_moment = QPushButton("Clear")
        self._btn_clear_moment.setFixedWidth(58)
        self._btn_clear_moment.setToolTip("Forget every picked moment.")
        self._btn_clear_moment.setEnabled(False)
        self._btn_clear_moment.clicked.connect(self._clear_moment)
        mom_col.addWidget(self._lbl_moment)
        mom_row.addStretch(1)
        mom_row.addWidget(self._btn_undo_pick, 0)
        mom_row.addWidget(self._btn_clear_moment, 0)
        mom_col.addLayout(mom_row)

        # Ctrl+Z anywhere in the window. The graph canvas has the keyboard focus
        # most of the time, so this has to be a window-wide shortcut rather than a
        # key handler on the panel.
        _sc_undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        _sc_undo.setContext(Qt.ShortcutContext.WindowShortcut)
        _sc_undo.activated.connect(self._undo_pick)

        self._status = QLabel("Ready.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#333333;font-size:10px;")

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._btn_search = bb.addButton("🎯 Search",
                                        QDialogButtonBox.ButtonRole.AcceptRole)
        self._btn_search.clicked.connect(self._on_accept)
        # Opened with no camera picked: that is allowed, and the tab asks for them
        # after Search. Said here so it does not look like the window is unaware.
        self._btn_search.setToolTip(
            "Search the picked moments (or the marked regions)."
            + ("\n\nNo camera is picked yet — the camera picker opens when you "
               "press this, and the search then starts by itself."
               if not self._cams else ""))
        bb.rejected.connect(self.reject)

        side_col = QWidget()
        side_lay = QVBoxLayout(side_col)
        side_lay.setContentsMargins(0, 0, 0, 0); side_lay.setSpacing(4)
        side_lay.addWidget(side_scroll, 1)
        side_lay.addLayout(mom_col, 0)
        side_lay.addWidget(self._status, 0)
        side_lay.addWidget(bb, 0)
        side_col.setFixedWidth(312)
        # THE SIDEBAR IS LIGHT, like every other panel in the program. It was ported
        # from the CSS Logger's dark Spectra tab and for a while it kept that tab's
        # near-black ground, which inside this light dialog read as a black patch
        # around the calendar and the lists — the surround showed through wherever a
        # child widget had no colour of its own. Every ink in here is therefore
        # stated for a LIGHT ground: dark text, white cells, light buttons.
        # A plain QWidget needs WA_StyledBackground before a stylesheet background
        # is painted at all.
        for _w in (side_col, side_w):
            _w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side_col.setStyleSheet(
            "QWidget { background:#f3f3f3; color:#111111; }"
            "QLabel { background:transparent; color:#111111; }")
        self._side_scroll = side_scroll
        side_scroll.setStyleSheet(
            "QScrollArea { background:#f3f3f3; border:0px; }"
            "QScrollBar:vertical { background:#e0e0e0; width:12px; }"
            "QScrollBar::handle:vertical { background:#8a8a8a; min-height:20px;"
            " border-radius:3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height:0px; }")
        root.addWidget(side_col)

        # ── Right: plot ───────────────────────────────────────────────────
        right = QVBoxLayout()

        # Above the graph: how the days are shown, and — in One-day mode — which day.
        head = QHBoxLayout(); head.setSpacing(6)
        self._btn_mode_one = QPushButton("One day")
        self._btn_mode_all = QPushButton("All days")
        for b in (self._btn_mode_one, self._btn_mode_all):
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setStyleSheet(
                "QPushButton { background:#e8e8e8; color:#111; border:1px solid #9a9a9a;"
                " border-radius:3px; padding:2px 12px; font-weight:600; }"
                "QPushButton:checked { background:#1565C0; color:#ffffff;"
                " border:1px solid #0D47A1; }")
        self._btn_mode_one.setChecked(True)
        self._btn_mode_one.setToolTip("Show one marked day at a time; step with the arrows.")
        self._btn_mode_all.setToolTip(
            "Put every marked day on one axis, side by side — days a week apart with "
            "no empty week between them.")
        self._btn_mode_one.clicked.connect(lambda: self._set_mode("one"))
        self._btn_mode_all.clicked.connect(lambda: self._set_mode("all"))
        head.addWidget(self._btn_mode_one); head.addWidget(self._btn_mode_all)
        head.addStretch(1)

        _step_css = (
            "QPushButton { background:#e8e8e8; color:#111; border:1px solid #9a9a9a;"
            " border-radius:3px; font-size:15px; font-weight:700; }"
            "QPushButton:hover:enabled { background:#ffffff; }"
            "QPushButton:disabled { background:#dcdcdc; color:#8a8a8a;"
            " border:1px solid #bdbdbd; }")
        self._btn_day_prev = QPushButton("◀"); self._btn_day_prev.setFixedSize(40, 26)
        self._btn_day_prev.setStyleSheet(_step_css)
        self._btn_day_prev.setToolTip("Previous marked day")
        self._btn_day_prev.clicked.connect(lambda: self._step_day(-1))
        self._lbl_day = QLabel("")
        self._lbl_day.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_day.setMinimumWidth(190)
        self._lbl_day.setStyleSheet("font-size:13px; font-weight:700;")
        self._btn_day_next = QPushButton("▶"); self._btn_day_next.setFixedSize(40, 26)
        self._btn_day_next.setStyleSheet(_step_css)
        self._btn_day_next.setToolTip("Next marked day")
        self._btn_day_next.clicked.connect(lambda: self._step_day(+1))
        head.addWidget(self._btn_day_prev)
        head.addWidget(self._lbl_day)
        head.addWidget(self._btn_day_next)
        right.addLayout(head)

        self._fig = self._Figure(figsize=(6, 4), tight_layout=True)
        self._canvas = self._FigureCanvas(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._toolbar = _make_mpl_toolbar(self._NavToolbar, self._canvas, self)
        right.addWidget(self._toolbar)
        right.addWidget(self._canvas, 1)
        # Left reads, right zooms. The span selectors take the drags (see
        # _install_span); these two take the clicks that did not move.
        self._canvas.mpl_connect("button_press_event", self._on_press)
        self._canvas.mpl_connect("button_release_event", self._on_release)
        hint = QLabel(
            "left click = one moment   ·   left drag = mark a region   ·   "
            "right drag = zoom in   ·   right click = zoom back out")
        hint.setStyleSheet("color:#555555;font-size:11px;")
        right.addWidget(hint)
        self._build_graph_controls(right)
        self._build_stats(right)
        right_w = QWidget(); right_w.setLayout(right)
        root.addWidget(right_w, 1)

        self._rebuild_regions_ui()
        self._sync_day_header()

    def showEvent(self, event):
        """Open the sidebar at its TOP.

        Building the panel ticks SBW4 and scrolls the PV list to it, and that walks
        up the parents and scrolls the sidebar itself down by a dozen pixels — just
        enough to cut the first caption and the top of the calendar off, which is
        what made the panel look like it started mid-way through."""
        super().showEvent(event)
        if not getattr(self, "_side_parked", False):
            self._side_parked = True
            sb = self._side_scroll.verticalScrollBar()
            QTimer.singleShot(0, lambda: sb.setValue(0))

    def _seed_default_pvs(self):
        # Presets from CHANNEL_MAP; SBW4 pre-checked as the default primary.
        self._pv_list.blockSignals(True)
        for name, ch in CPVA_CHANNEL_MAP.items():
            it = QListWidgetItem(name.upper())
            it.setData(Qt.ItemDataRole.UserRole, ch)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if ch == CPVA_SBW4_CHANNEL
                             else Qt.CheckState.Unchecked)
            self._set_pv_row_tip(it)
            self._pv_list.addItem(it)
        # The FORMULAS the shared PV list holds — a ratio, a scaled energy, a
        # difference. They are computed, not read, so they carry a "derived:" key
        # instead of a channel; the graph draws them over time exactly like a
        # channel. Unticked to begin with: computing one costs the sources.
        try:
            sl = _get_slider_module()
            for d in sl.PV_DERIVED:
                nm = str(d.get("name") or "")
                if not nm:
                    continue
                it = QListWidgetItem(sl.pv_label_for(nm) + "  (formula)")
                it.setData(Qt.ItemDataRole.UserRole, _DERIVED_PREFIX + nm)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
                self._set_pv_row_tip(it)
                self._pv_list.addItem(it)
        except Exception:
            pass
        self._pv_list.blockSignals(False)
        # Show the one that is ticked. It is the last of the presets, so a box that
        # holds five rows opens on four unticked ones and reads as "nothing chosen".
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                self._pv_list.scrollToItem(
                    it, QAbstractItemView.ScrollHint.PositionAtCenter)
                break
        self._refresh_primary_combo()

    # ── What a channel IS ─────────────────────────────────────────────────
    def _pv_row_tip(self, channel: str) -> str:
        """What a row in the PV list says when the mouse rests on it.

        The rows carry SHORT labels ("SBW4", "PCM2"), and the full archived name is
        the one thing an operator needs before believing a curve — the list used to
        answer with the box's own general tooltip instead, so hovering a row never
        told you which channel it was. Notes about that row (read under the other
        name, formula could not be computed) are APPENDED to this, never in place
        of it."""
        if _is_derived_key(channel):
            nm = _derived_name(channel)
            try:
                expr = next((d.get("expr", "") for d in _get_slider_module().PV_DERIVED
                             if str(d.get("name") or "") == nm), "")
            except Exception:
                expr = ""
            return f"{nm}  (formula)" + (f"\n= {expr}" if expr else "")
        meta = self._pv_meta_for(channel)
        tip = channel
        if meta.get("unit"):
            tip += f"\nunit: {meta['unit']}"
        alt = ", ".join(cpva.channel_aliases(channel))
        if alt:
            tip += (f"\nalso archived as: {alt}\nWhichever of the two names holds "
                    "the day is the one read.")
        return tip

    def _set_pv_row_tip(self, it, extra: str = ""):
        """The row's own tooltip, with an optional note under it."""
        ch = it.data(Qt.ItemDataRole.UserRole)
        base = self._pv_row_tip(ch)
        it.setToolTip(base + ("\n\n" + extra if extra else ""))

    def _registry_name_for(self, channel: str) -> str:
        """The registry's name for a channel, so this window reads the SAME unit and
        the same label as the Slider does. The registry is keyed by PV name, the
        window works in channels, and the map between them is built once.

        A formula IS a registry name already — it has no channel to look up."""
        if _is_derived_key(channel):
            return _derived_name(channel)
        cache = getattr(self, "_reg_by_channel", None)
        if cache is None:
            cache = {}
            try:
                sl = _get_slider_module()
                for nm in sl.pv_all_names():
                    ch = sl.pv_channel_for(nm)
                    if ch:
                        cache[ch] = nm
            except Exception:
                pass
            self._reg_by_channel = cache
        return cache.get(channel, "")

    def _pv_meta_for(self, channel: str) -> dict:
        """Unit and step-or-not for a channel, worked out once and remembered.

        The unit is what the y axes are grouped by: joules on one axis, millimetres
        on another, and a PV with no unit on its own — never everything divided by
        its own maximum, which is what this window used to draw. Two PVs on one axis
        can then be COMPARED; before, a curve at 0.9 meant nothing but "near its own
        biggest".
        """
        got = self._ch_meta.get(channel)
        if got is None:
            name = self._registry_name_for(channel)
            unit = ""
            try:
                if name:
                    unit = _get_slider_module().pv_units_for(name) or ""
            except Exception:
                unit = ""
            got = {"name": name, "unit": unit, "step": None}
            self._ch_meta[channel] = got
        return got

    def _unit_key_for(self, channel: str) -> str:
        """Which y axis a channel belongs on. Its unit, or the channel itself when it
        has none — a unitless PV shares an axis with nothing, since there is nothing
        to say the numbers are the same kind of thing.

        A PV the operator has put on its own axis ("Own axis" under the PV list)
        gets a key nothing else can match, however it is archived: two joules
        channels three orders of magnitude apart share a unit but not a readable
        axis."""
        if channel in self._own_axis:
            return f"@own:{channel}"
        meta = self._pv_meta_for(channel)
        return meta.get("unit") or f"@{channel}"

    def _style_for(self, channel: str) -> tuple:
        """How a PV's curve is drawn — (colour, line shape) — from the PV ITSELF,
        never from where it happens to sit among the ones being drawn. Checking a PV
        off must not repaint the others.

        Past the end of the colours the line changes shape instead of a colour being
        used twice."""
        got = self._pv_colour.get(channel)
        if got:
            return got
        order = []
        for i in range(self._pv_list.count()):
            ch = self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)
            if ch:
                order.append(ch)
        idx = order.index(channel) if channel in order else len(self._pv_colour)
        n = len(_PV_LINE_COLORS)
        got = (_PV_LINE_COLORS[idx % n],
               _PV_LINE_DASHES[(idx // n) % len(_PV_LINE_DASHES)])
        self._pv_colour[channel] = got
        return got

    def _colour_for(self, channel: str) -> str:
        return self._style_for(channel)[0]

    # ── PV list handling ──────────────────────────────────────────────────
    def _pv_label_for(self, channel: str) -> str:
        """The name the PV list shows for a channel (the channel itself if it is
        not on the list)."""
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == channel:
                return it.text()
        return channel

    def _checked_channels(self) -> list:
        out = []
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append((it.text(), it.data(Qt.ItemDataRole.UserRole)))
        return out

    def _refresh_primary_combo(self):
        prev = self._primary_cb.currentData()
        prev_cond = self._cond_pv_cb.currentData()
        for cb, keep in ((self._primary_cb, prev), (self._cond_pv_cb, prev_cond)):
            cb.blockSignals(True)
            cb.clear()
            for label, ch in self._checked_channels():
                # Formulas are drawn, not searched: both of these are asked of the
                # ARCHIVER (the peak inside a region, the first moment a condition
                # held), and a formula has no channel to ask for.
                if _is_derived_key(ch):
                    continue
                cb.addItem(label, ch)
            # keep the previous pick if it is still checked, else default to first
            idx = cb.findData(keep)
            if idx >= 0:
                cb.setCurrentIndex(idx)
            cb.blockSignals(False)

    def _on_pv_checks_changed(self, *_):
        self._refresh_primary_combo()
        self._reload_series()

    # ── Condition ─────────────────────────────────────────────────────────
    def _on_cond_toggled(self, on: bool):
        for w in getattr(self, "_cond_widgets", []):
            w.setEnabled(on)
        self._sync_cond_row()
        self._sync_search_button()

    def _sync_cond_row(self, *_):
        between = (self._cond_op_cb.currentData() == "between")
        self._cond_val2.setVisible(between)

    def _cond_is_on(self) -> bool:
        return bool(self._cond_on.isChecked() and self._cond_pv_cb.currentData())

    def _sync_search_button(self):
        if not hasattr(self, "_btn_search"):
            return
        if self._cond_is_on():
            txt = "🎯 Search by condition"
        elif len(self._moments) > 1:
            txt = f"🎯 Search these {len(self._moments)} moments"
        elif self._moments:
            txt = "🎯 Search this moment"
        else:
            txt = "🎯 Search these regions"
        self._btn_search.setText(txt)

    def _browse_pvs(self):
        """Add PVs to the list — and SAY what happened.

        A PV is appended at the bottom of a short list that already holds the
        presets, so without scrolling to it and saying so on the status line the
        whole operation looked like it did nothing at all."""
        dlg = _PVBrowseDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        existing = {self._pv_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self._pv_list.count())}
        picked = list(dlg.selected_channels())
        added: list = []
        dupes: list = []
        first_new = None
        self._pv_list.blockSignals(True)
        for ch in picked:
            if ch in existing:
                dupes.append(ch)
                continue
            it = QListWidgetItem(_PV_PRESET_LABELS.get(ch, ch))
            it.setData(Qt.ItemDataRole.UserRole, ch)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self._set_pv_row_tip(it)
            self._pv_list.addItem(it)
            existing.add(ch)
            added.append(it.text())
            if first_new is None:
                first_new = it
        self._pv_list.blockSignals(False)
        self._refresh_primary_combo()
        # Show the new row instead of leaving it below the fold.
        if first_new is not None:
            self._pv_list.setCurrentItem(first_new)
            self._pv_list.scrollToItem(
                first_new, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._announce_pv_change(added, dupes, [], picked)
        self._reload_series()

    def _remove_selected_pv(self):
        rows = self._pv_list.selectedItems()
        if not rows:
            self._status.setText(
                "Nothing removed — click a PV on the list first, then Remove.")
            return
        gone: list = []
        for it in rows:
            gone.append(it.text())
            self._alias_used.pop(it.data(Qt.ItemDataRole.UserRole), None)
            self._pv_list.takeItem(self._pv_list.row(it))
        self._refresh_primary_combo()
        self._announce_pv_change([], [], gone, [])
        self._reload_series()

    def _selected_channel(self) -> "str | None":
        it = self._pv_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it is not None else None

    def _sync_pv_buttons(self):
        """The two per-PV buttons follow whichever row is selected."""
        ch = self._selected_channel()
        for b in (self._btn_own_axis, self._btn_pv_edit):
            b.setEnabled(ch is not None)
        self._btn_own_axis.blockSignals(True)
        self._btn_own_axis.setChecked(bool(ch and ch in self._own_axis))
        self._btn_own_axis.blockSignals(False)

    def _toggle_own_axis(self, on: bool):
        ch = self._selected_channel()
        if not ch:
            return
        if on:
            self._own_axis.add(ch)
        else:
            self._own_axis.discard(ch)
        self._status.setText(
            f"{self._pv_label_for(ch)} "
            + ("now has a value axis of its own." if on
               else "shares the axis of its unit again."))
        self._redraw()

    def _edit_selected_pv(self):
        """The selected PV's name and unit, written to the SHARED registry.

        The name and the unit belong to the PV registry the Image Slider keeps, not
        to this window: `is_t.PV_LABELS` and `is_t.PV_CUSTOM_UNITS` are the two
        dictionaries every tab reads and the Slider saves. Keeping a second copy
        here is exactly how one PV came to mean two things in two tabs, so this
        writes theirs — and it refuses when the channel is not on the shared list,
        because there is then nothing to write it against."""
        ch = self._selected_channel()
        if not ch:
            return
        name = self._registry_name_for(ch)
        try:
            sl = _get_slider_module()
        except Exception as e:
            QMessageBox.information(self, "Edit PV",
                                    f"The PV registry is not reachable:\n{e}")
            return
        if not name:
            QMessageBox.information(
                self, "Edit PV",
                f"{ch}\n\nThis channel is not on the shared PV list, so it has no "
                "name or unit to edit. Add it in the Image Slider's "
                "\"Select PV channels\" first and it will be known here too.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit PV")
        _paint_dialog(dlg)
        lay = QVBoxLayout(dlg)
        # A dialog is light grey with dark ink (the app stylesheet), so these two
        # notes are DARK grey — the dark sidebar's #c8c8c8 is invisible here.
        info = QLabel(f"{name}\n{ch}")
        info.setStyleSheet("color:#444;font-size:11px;")
        lay.addWidget(info)
        e_lbl, e_unit = QLineEdit(), QLineEdit()
        e_lbl.setText(sl.pv_label_for(name))
        e_unit.setText(sl.pv_units_for(name) or "")
        for lbl, w in (("Name on screen", e_lbl), ("Unit", e_unit)):
            w.setStyleSheet("background:#ffffff;color:#111111;")
            lay.addWidget(QLabel(lbl))
            lay.addWidget(w)
        try:
            lo, hi = sl.pv_limits_for(name)
        except Exception:
            lo = hi = None
        note = QLabel(
            "Alarm limits: "
            + (f"{lo:g} … {hi:g}" if lo is not None and hi is not None else "none")
            + "\nLimits and formulas are set in the Image Slider's "
              "\"Select PV channels\" — one place for the whole program.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;font-size:10px;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_lbl = e_lbl.text().strip()
        new_unit = e_unit.text().strip()
        try:
            if new_lbl and new_lbl != name:
                sl.PV_LABELS[name] = new_lbl
            else:
                sl.PV_LABELS.pop(name, None)
            if new_unit:
                sl.PV_CUSTOM_UNITS[name] = new_unit
            else:
                sl.PV_CUSTOM_UNITS.pop(name, None)
        except Exception as e:
            QMessageBox.warning(self, "Edit PV", f"Could not save:\n{e}")
            return
        # The unit decides which axis the curve sits on, so both caches go.
        self._ch_meta.pop(ch, None)
        self._reg_by_channel = None
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == ch:
                it.setText(sl.pv_label_for(name))
        self._refresh_primary_combo()
        self._status.setText(
            f"{sl.pv_label_for(name)}: name and unit saved to the shared PV list.")
        self._redraw()
        self._refresh_stats()

    def _announce_pv_change(self, added: list, dupes: list, gone: list,
                            picked: list):
        """One sentence on the status line naming what went on or off the list.

        `added` / `gone` are the LABELS as the list shows them. The list itself is
        the record, but it is short, it scrolls, and it sits above the fold — so a
        change to it that is not stated in words reads as nothing having happened."""
        parts: list = []
        if added:
            parts.append("Added " + ", ".join(added))
        if dupes:
            parts.append(f"{len(dupes)} already on the list")
        if gone:
            parts.append("Removed " + ", ".join(gone))
        if not parts:
            if picked:
                parts.append("Nothing added — every PV picked was already listed")
            else:
                parts.append("Nothing added — no PV was selected in Browse")
        n_on = len(self._checked_channels())
        self._status.setText("   ·   ".join(parts)
                             + f"   ·   {self._pv_list.count()} PV(s) on the list, "
                               f"{n_on} plotted.")

    # ── Day handling ──────────────────────────────────────────────────────
    def _on_cal_clicked(self, d: QDate):
        """Same rules as the tab's Time window: plain click picks one day,
        Ctrl+click toggles one in or out, Ctrl+Shift+click takes the range from the
        last click.

        A day that CARRIES A PICK — a marked region or a picked moment — is never
        unmarked. Building the set to search means going day by day, and a plain
        click on the next day unmarks every other one, so the old rule ("a dropped
        day takes its regions with it") threw away everything picked so far the
        moment the second day was opened. To let a day go, take its picks off
        first (Undo, or Clear)."""
        mods  = QApplication.keyboardModifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        clicked = date(d.year(), d.month(), d.day())
        anchor = (date(self._last_cal_click.year(), self._last_cal_click.month(),
                       self._last_cal_click.day())
                  if self._last_cal_click is not None else None)
        # One rule set for the whole program — the same function the Time window
        # dialog runs, so a Ctrl+Shift stretch behaves identically in both.
        new_days = daypicker.compute_click(
            self._days, clicked, anchor, ctrl, shift,
            {i for i, cb in enumerate(self._wd_checks) if cb.isChecked()})

        if not new_days:
            new_days = [clicked]
        self._last_cal_click = d
        # Days that hold something picked stay marked whatever the click said.
        keep = {r["day"] for r in self._regions} | set(self._moment_days())
        new_days = sorted(set(new_days) | (keep & set(self._days)))
        self._days = new_days
        # Stay on the day that was just clicked when it is still in the set.
        self._focus_i = (self._days.index(clicked) if clicked in self._days else 0)
        self._apply_day_paint()
        self._refresh_day_list()
        self._reload_series()

    def _apply_day_paint(self):
        delegate = getattr(self._cal, "_wk_delegate", None)
        if delegate is None:
            return
        qds = self._qdates()
        delegate.set_selected(qds)
        focus = self._focus_day()
        delegate.set_focus_date(QDate(focus.year, focus.month, focus.day)
                                if focus else None)

    def _refresh_day_list(self):
        """One row per marked day, with what is picked on it.

        The counts are the whole point of the row: picks are made day by day and
        the graph shows one day at a time, so without them the four moments picked
        on Tuesday are invisible while Wednesday is on screen."""
        by_day = self._regions_by_day()
        counts = {d: len(v) for d, v in by_day.items()}
        moments: dict = {}
        for t in self._moments:
            k = self._local_dt(t).date()
            moments[k] = moments.get(k, 0) + 1
        self._day_list.blockSignals(True)
        self._day_list.clear()
        for i, d in enumerate(self._days):
            n = counts.get(d, 0)
            m = moments.get(d, 0)
            txt = f"{_WEEKDAYS_EN[d.weekday()][:3]}  {d.strftime('%d.%m.%Y')}"
            if m:
                txt += f"   —  {m} moment{'s' if m != 1 else ''}"
            if n:
                txt += ("   —  " if not m else ",  ") \
                       + f"{n} region{'s' if n != 1 else ''}"
            st = self._day_status.get(d)
            if st in ("error", "stale"):
                txt += "   ⚠ archiver"
            elif st == "empty":
                txt += "   (no data)"
            it = QListWidgetItem(txt)
            # WHICH regions, not just how many — the count on the row cannot say
            # whether the four spans are the four you meant.
            tip = []
            for j, r in enumerate(by_day.get(d, []), 1):
                tip.append(f"{j})  {self._fmt_region_span(r)}")
            for j, t in enumerate(
                    [t for t in self._moments
                     if self._local_dt(t).date() == d], 1):
                tip.append(f"moment {j})  " + self._local_dt(t).strftime("%H:%M:%S"))
            if tip:
                it.setToolTip("\n".join(tip))
            self._day_list.addItem(it)
        self._day_list.setCurrentRow(self._focus_i if self._days else -1)
        self._day_list.blockSignals(False)
        self._apply_day_paint()
        self._sync_day_header()

    def _on_day_row_changed(self, row: int):
        if row < 0 or row >= len(self._days):
            return
        self._focus_i = row
        self._apply_day_paint()
        self._sync_day_header()
        if self._mode == "one":
            self._reload_series()

    def _sync_day_header(self):
        one = (self._mode == "one")
        n = len(self._days)
        for w in (self._btn_day_prev, self._btn_day_next):
            w.setEnabled(one and n > 1)
        d = self._focus_day()
        if not one:
            first = self._days[0].strftime("%d.%m.") if self._days else ""
            last = self._days[-1].strftime("%d.%m.%Y") if self._days else ""
            self._lbl_day.setText(f"{n} days   {first} … {last}" if n > 1
                                  else (last or ""))
        elif d is None:
            self._lbl_day.setText("")
        else:
            self._lbl_day.setText(
                f"{_WEEKDAYS_EN[d.weekday()][:3]}  {d.strftime('%d.%m.%Y')}"
                + (f"   {self._focus_i + 1} / {n}" if n > 1 else ""))

    def _step_day(self, delta: int):
        if not self._days:
            return
        self._focus_i = (self._focus_i + delta) % len(self._days)
        self._day_list.blockSignals(True)
        self._day_list.setCurrentRow(self._focus_i)
        self._day_list.blockSignals(False)
        self._apply_day_paint()
        self._sync_day_header()
        self._reload_series()

    def _set_mode(self, mode: str):
        self._mode = mode
        self._btn_mode_one.setChecked(mode == "one")
        self._btn_mode_all.setChecked(mode == "all")
        self._sync_day_header()
        # All days shows days that One day never asked for, so the switch is a load,
        # not just a redraw.
        self._reload_series()

    # ── Axis mapping ───────────────────────────────────────────────────────
    # One pair of helpers for both modes, so the plot, the region shading and the
    # drag all agree on what an x coordinate means. In "all" mode a day is drawn at
    # its INDEX rather than its real date: the marked days sit next to each other
    # even when they are a week apart.
    def _base_num(self) -> float:
        d = self._days[0]
        return self._mdates.date2num(
            datetime(d.year, d.month, d.day, tzinfo=self._tz()))

    def _local_dt(self, t_ns: int):
        dt = datetime.fromtimestamp(t_ns / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            dt = dt.astimezone(PRAGUE)
        return dt

    def _ns_to_x(self, t_ns: int, day=None) -> float:
        dt = self._local_dt(t_ns)
        if self._mode == "one":
            return self._mdates.date2num(dt)
        d = day or dt.date()
        try:
            idx = self._days.index(d)
        except ValueError:
            return float("nan")
        d0_ns, _ = self._day_bounds_for(d)
        frac = (t_ns - d0_ns) / 86_400e9
        return self._base_num() + idx + frac

    def _x_to_ns(self, x: float) -> "int | None":
        if self._mode == "one":
            try:
                return int(self._mdates.num2date(x).timestamp() * 1e9)
            except Exception:
                return None
        off = x - self._base_num()
        idx = int(math.floor(off))
        if idx < 0 or idx >= len(self._days):
            return None
        frac = min(max(off - idx, 0.0), 1.0)
        d0_ns, _ = self._day_bounds_for(self._days[idx])
        return int(d0_ns + frac * 86_400e9)

    def _day_for_x(self, x: float):
        if self._mode == "one":
            return self._focus_day()
        idx = int(math.floor(x - self._base_num()))
        if 0 <= idx < len(self._days):
            return self._days[idx]
        return None

    # ── Loading ────────────────────────────────────────────────────────────
    def _shown_days(self) -> list:
        """The days the graph is drawing right now."""
        if self._mode == "all":
            return list(self._days)
        d = self._focus_day()
        return [d] if d is not None else []

    @staticmethod
    def _fetch_window(channel: str, start_ns: int,
                      end_ns: int) -> "tuple[list, str, str]":
        """Samples between two timestamps, through the archiver's DAY cache.

        `cpva.get_day` keeps an LRU of whole days, recovers from the archiver's
        HTTP 500 on an oversize query by splitting it, and reports whether the fetch
        actually succeeded — none of which the plain windowed query it replaced could
        do. A UTC ("lab time") day straddles two Prague days, so both are asked for
        and the result is clipped back to the window.

        The third value is the name the samples actually came from when it is NOT
        the one asked for — SBW4's two names take turns, and a curve drawn from the
        other one has to say so rather than look like the name on the list."""
        keys = {cpva.date_key_for_ns(start_ns), cpva.date_key_for_ns(end_ns)}
        merged: list = []
        worst = "ok"
        alias = ""
        for k in sorted(keys):
            asked = cpva.channel_for_day(channel, k)
            res = cpva.get_day(asked, k, timeout=cpva.FULL_DAY_TIMEOUT)
            merged.extend(res.samples)
            if _PV_STATUS_RANK.get(res.status, 3) > _PV_STATUS_RANK.get(worst, 0):
                worst = res.status
            src = (getattr(res, "src_channel", "") or "").split(".value")[0]
            if res.samples and src and src != channel:
                alias = src
        out = sorted((t, v) for (t, v) in merged if start_ns <= t <= end_ns)
        if worst == "ok" and not out:
            worst = "empty"
        return out, worst, alias

    def _reload_series(self):
        """Fetch what the graph is about to show and is not holding yet.

        Days already read stay in `self._series`, so stepping back to a day, or
        switching to All days after looking at one, costs nothing. Changing the PV
        list throws the lot away — the stored series are per channel."""
        channels = [ch for _, ch in self._checked_channels()]
        key = tuple(sorted(channels))
        if key != getattr(self, "_series_key", None):
            self._series = {}
            self._day_status = {}
            self._series_key = key
        days = self._shown_days()
        self._load_gen += 1
        gen = self._load_gen
        if not channels:
            self._series = {}
            self._redraw()
            self._status.setText("No PV selected — check at least one PV to plot.")
            return
        missing = [d for d in days if d not in self._series]
        if not missing:
            self._redraw()
            self._on_series_loaded({"gen": gen, "series": {}, "status": {}})
            return
        days = missing
        self._status.setText(
            f"Loading {len(days)} day(s) — {len(channels)} PV(s)…")

        bounds = {d: self._day_bounds_for(d) for d in days}
        # A formula is COMPUTED, not read: the plan and the channels of its leaf
        # sources are worked out here, on the GUI thread, and handed to the worker.
        # The plan is a snapshot on purpose — the registry must not change under a
        # computation that is already running.
        derived_keys = [ch for ch in channels if _is_derived_key(ch)]
        channels = [ch for ch in channels if not _is_derived_key(ch)]
        plan = []
        src_channels: dict = {}          # registry name → channel to fetch
        if derived_keys:
            try:
                sl = _get_slider_module()
                plan = derived_plan([_derived_name(k) for k in derived_keys])
                for spec in plan:
                    for nm in spec["sources"]:
                        ch = sl.pv_channel_for(nm)
                        if ch:
                            src_channels[nm] = ch
            except Exception as e:
                self._log_derived_error = str(e)
                plan = []

        def worker():
            out: dict = {}
            stat: dict = {}
            known: dict = {}
            alias: dict = {}
            reasons: dict = {}       # formula name → why it has no curve
            for d in days:
                s_ns, e_ns = bounds[d]
                per_ch: dict = {}
                worst = "ok"
                for ch in channels:
                    try:
                        vals, st, alt = self._fetch_window(ch, s_ns, e_ns)
                    except Exception:
                        vals, st, alt = [], "error", ""
                    if alt:
                        alias[ch] = alt
                    per_ch[ch] = vals
                    if _PV_STATUS_RANK.get(st, 3) > _PV_STATUS_RANK.get(worst, 0):
                        worst = st
                    if ch not in known:
                        # Without a timestamp this answers from what is already
                        # known and never asks the archiver — see below.
                        try:
                            known[ch] = bool(cpva.is_step_channel(ch))
                        except Exception:
                            known[ch] = False
                # ── The formulas, over the same day ───────────────────────────
                # Computed from the LEAF sources, fetched here whether or not they
                # are on the PV list: a formula's letters are its own business.
                if plan:
                    src: dict = {}
                    for nm, ch in src_channels.items():
                        if ch in per_ch:
                            vals = per_ch[ch]
                            st = "ok" if vals else "empty"
                        else:
                            try:
                                vals, st, _alt = self._fetch_window(ch, s_ns, e_ns)
                            except Exception:
                                vals, st = [], "error"
                        src[nm] = {
                            "channel": ch, "status": st,
                            "ts": np.asarray([t for (t, _v) in vals],
                                             dtype=np.int64),
                            "val": np.asarray([float(v) if _is_finite(v)
                                               else float("nan")
                                               for (_t, v) in vals],
                                              dtype=np.float64),
                        }
                    try:
                        got = build_derived_series(plan, src, [(s_ns, e_ns + 1)])
                    except Exception as e:
                        got = {spec["name"]: {"ts": _EMPTY_TS, "val": _EMPTY_VAL,
                                              "reason": str(e)}
                               for spec in plan}
                    for nm, entry in got.items():
                        # Back to the shape the graph and the statistics read: a
                        # plain list of (t, value), NaN where the formula has a gap.
                        per_ch[_DERIVED_PREFIX + nm] = [
                            (int(t), float(v)) for t, v in
                            zip(entry.get("ts", ()), entry.get("val", ()))]
                        r = entry.get("reason")
                        if r:
                            reasons[nm] = r
                out[d] = per_ch
                stat[d] = worst
            try:
                self._sig.done.emit({"gen": gen, "series": out, "status": stat,
                                     "steps": known, "alias": alias,
                                     "reasons": reasons})
            except RuntimeError:
                pass

            # ── Second pass: does a channel HOLD its value between samples? ────
            # After the graph is up, never before it. Deciding this can cost the
            # archiver a request or two, and the samples for the day are already in
            # hand — making the drawing wait for it meant an unreachable archiver
            # left the window with no curves and no moment to click at all.
            steps: dict = {}
            seeds: dict = {}
            for ch in channels:
                if self._ch_meta.get(ch, {}).get("step") is not None:
                    continue
                s_ns0 = bounds[days[0]][0]
                try:
                    steps[ch] = bool(cpva.classify_step_channel(
                        ch, s_ns0, primary=(out.get(days[0]) or {}).get(ch),
                        timeout=cpva.FULL_DAY_TIMEOUT))
                except Exception:
                    steps[ch] = False
            for d in days:
                s_ns, _e = bounds[d]
                for ch in channels:
                    holds = steps.get(ch, self._ch_meta.get(ch, {}).get("step"))
                    if not holds or (ch, d) in self._seeds:
                        continue
                    # What it was already sitting at when the day opened. A setting
                    # that last moved last week has nothing IN the day, and drawing
                    # that as "no data" is the lie this exists to prevent.
                    try:
                        res = cpva.value_at_or_before(ch, s_ns - 1)
                        if res.ts_ns is not None and res.value is not None:
                            seeds[(ch, d)] = (int(res.ts_ns), float(res.value))
                        else:
                            seeds[(ch, d)] = None
                    except Exception:
                        seeds[(ch, d)] = None
            if steps or seeds:
                try:
                    self._sig.done.emit({"gen": gen, "series": {}, "status": {},
                                         "steps": steps, "seeds": seeds})
                except RuntimeError:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_series_loaded(self, data: dict):
        if data.get("gen") != self._load_gen:
            return   # a newer request superseded this one
        self._series.update(data.get("series") or {})
        self._day_status.update(data.get("status") or {})
        self._alias_used.update(data.get("alias") or {})
        # A formula with no curve says WHY, where the curve would be. A silent gap
        # reads as a broken tab.
        self._derived_reason.update(data.get("reasons") or {})
        for ch, holds in (data.get("steps") or {}).items():
            self._pv_meta_for(ch)["step"] = bool(holds)
        self._seeds.update(data.get("seeds") or {})
        self._redraw()
        self._refresh_day_list()
        n = sum(len(v) for per in self._series.values() for v in per.values())
        bad = [d for d in self._shown_days()
               if self._day_status.get(d) in ("error", "stale")]
        if bad:
            # An archiver failure is NOT "no data" — saying so would turn a broken
            # query into a statement about the machine.
            names = ", ".join(d.strftime("%d.%m.") for d in bad)
            msg = (f"Loaded {n} samples. Archiver did not answer for {names} — "
                   "that day may be incomplete.")
        elif n == 0:
            msg = "No PV data for the marked day(s)."
        else:
            msg = f"Loaded {n} samples.  Drag to mark a region."
        # A curve that came from the channel's OTHER name says so. SBW4 runs under
        # a HAPLS-era name and an L3 name by turns, and reading one as empty is
        # exactly what made a search come back with nothing.
        if self._alias_used:
            shown = [self._pv_label_for(ch) for ch in self._alias_used]
            msg += ("   ·   " + ", ".join(shown)
                    + " read under the other archived name.")
        # A picked formula that could not be computed says so — and on its row.
        live = {_derived_name(ch) for _lbl, ch in self._checked_channels()
                if _is_derived_key(ch)}
        bad_f = {nm: why for nm, why in self._derived_reason.items() if nm in live}
        if bad_f:
            msg += "   ·   " + "; ".join(f"{nm}: {why}" for nm, why in bad_f.items())
        # Every row's tooltip, rebuilt: the archived name first (that is what the
        # mouse is asked for), then whatever this load has to say about the row.
        # Written as base + note, because a note that REPLACED the tooltip is how
        # hovering SBW4 stopped naming the channel at all.
        for i in range(self._pv_list.count()):
            it = self._pv_list.item(i)
            ch = it.data(Qt.ItemDataRole.UserRole)
            note = ""
            alt = self._alias_used.get(ch)
            if alt:
                note = f"Read under its other name: {alt}"
            if _is_derived_key(ch):
                why = self._derived_reason.get(_derived_name(ch))
                if why:
                    note = why
            self._set_pv_row_tip(it, note)
        self._status.setText(msg)
        # The numbers for the marked range come from the samples that just arrived.
        self._refresh_stats()

    # ── Plot ────────────────────────────────────────────────────────────────
    def _hold_xy(self, channel: str, day) -> tuple:
        """One day of a PV, as the graph draws it: (x, y, marker_x, marker_y).

        A channel written only when it CHANGES (a setting, a waveplate) has no
        samples inside a day it did not move — and drawing that as nothing at all
        says "no data" about a PV whose value is perfectly well known. So its curve
        starts at the left edge from the value it was already sitting at (`_seeds`)
        and is carried to the right edge, or to now if the day is today. Drawn
        steps-post: the value held until the next sample, which is what the archive
        actually says.

        The edge points are SYNTHETIC — nothing was recorded at those times — so they
        get no marker. Only real samples do.
        """
        s_ns, e_ns = self._day_bounds_for(day)
        samples = (self._series.get(day) or {}).get(channel) or []
        holds = bool(self._pv_meta_for(channel).get("step"))
        xs: list = []
        ys: list = []
        if holds:
            seed = self._seeds.get((channel, day))
            first_ns = samples[0][0] if samples else None
            if seed and (first_ns is None or seed[0] < first_ns):
                xs.append(self._ns_to_x(s_ns, day))
                ys.append(float(seed[1]))
        mx = [self._ns_to_x(t, day) for t, _v in samples]
        my = [float(v) for _t, v in samples]
        xs.extend(mx)
        ys.extend(my)
        if holds and ys:
            end_ns = min(int(e_ns), time.time_ns())
            if end_ns > (samples[-1][0] if samples else s_ns):
                xs.append(self._ns_to_x(end_ns, day))
                ys.append(ys[-1])
        return xs, ys, mx, my

    def _redraw(self):
        ax = self._ax
        # The extra y axes belong to the drawing, not to the window: a redraw builds
        # them from what is checked NOW, so the old ones come off the figure first.
        for extra in self._axes_extra:
            try:
                extra.remove()
            except Exception:
                pass
        self._axes_extra = []
        ax.clear()
        label_by_ch = {ch: lbl for lbl, ch in self._checked_channels()}
        days = self._shown_days()
        any_data = False

        # ── One y axis per UNIT ───────────────────────────────────────────────
        # Joules on one axis, millimetres on another, a PV with no unit on its own.
        # Every value is drawn as it was archived. This window used to divide each
        # PV by its own biggest value, which made every curve fill the height and
        # meant a reading of 0.9 said nothing but "near its own maximum" — two PVs
        # could not be compared, and neither could two days of the same PV.
        groups: dict = {}
        for label, ch in self._checked_channels():
            groups.setdefault(self._unit_key_for(ch), []).append((label, ch))
        handles: list = []
        for k, (unit_key, members) in enumerate(groups.items()):
            if k == 0:
                axis = ax
            else:
                axis = ax.twinx()
                if k >= 2:
                    axis.spines["right"].set_position(("outward", 46 * (k - 1)))
                self._axes_extra.append(axis)
            for label, ch in members:
                colour, dash = self._style_for(ch)
                xs: list = []
                ys: list = []
                mxs: list = []
                mys: list = []
                for d in days:
                    dx, dy, mx, my = self._hold_xy(ch, d)
                    if not dx:
                        continue
                    any_data = True
                    if xs:
                        xs.append(np.nan); ys.append(np.nan)   # break between days
                    xs.extend(dx); ys.extend(dy)
                    mxs.extend(mx); mys.extend(my)
                if not xs:
                    continue
                ln, = axis.plot(xs, ys, dash, lw=1.2, alpha=0.9, color=colour,
                                drawstyle="steps-post",
                                label=label_by_ch.get(ch, label))
                handles.append(ln)
                # The real samples, where there are few enough for a dot to mean
                # something. A held value is a line, never a dot: nothing was
                # recorded there.
                if 0 < len(mxs) < 600:
                    axis.plot(mxs, mys, linestyle="none", marker=".", ms=3,
                              color=colour)
            unit = unit_key if not unit_key.startswith("@") else ""
            one = members[0][1] if len(members) == 1 else None
            ink = self._colour_for(one) if one else "#333333"
            axis.set_ylabel(unit or (label_by_ch.get(members[0][1], "value")
                                     if len(members) == 1 else "value"),
                            color=ink)
            axis.tick_params(axis="y", colors=ink)

        if self._mode == "all" and days:
            base = self._base_num()
            ax.set_xlim(base, base + len(days))
            # Where one day ends and the next begins, and which day is which.
            for i in range(1, len(days)):
                ax.axvline(base + i, color="#607D8B", lw=1.2, ls="-", zorder=1)
            for i, d in enumerate(days):
                ax.text(base + i + 0.5, 1.01, d.strftime("%a %d.%m."),
                        transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                        fontsize=8, color="#37474F")
            ax.set_xlabel("Time within each day")
            # Ticks by the hour of the (virtual) day, so every day is read the same
            # way. Left to matplotlib's own locator the labels land wherever a run
            # of several days happens to put them.
            step = 6 if len(days) <= 4 else 12
            ax.xaxis.set_major_locator(
                self._mdates.HourLocator(byhour=range(0, 24, step)))
        elif days:
            s_ns, e_ns = self._day_bounds_for(days[0])
            ax.set_xlim(self._ns_to_x(s_ns, days[0]), self._ns_to_x(e_ns, days[0]))
            ax.set_xlabel("Time")
        ax.xaxis.set_major_formatter(self._mdates.DateFormatter(
            "%H:%M", tz=PRAGUE))
        # The strip of controls under the graph — grid, legend, log, the two hand-set
        # ranges and the tick spacing. One legend for every axis: matplotlib gives
        # each axes its own, and three of them would sit on top of each other in the
        # same corner.
        self._apply_graph_opts([ax] + list(self._axes_extra), handles,
                               [h.get_label() for h in handles])
        if not any_data:
            bad = [d for d in days if self._day_status.get(d) in ("error", "stale")]
            msg = ("The archiver did not answer for this day"
                   if bad else "No PV data for this day")
            ax.text(0.5, 0.5, msg, ha="center", va="center",
                    transform=ax.transAxes, color=("#C62828" if bad else "#999"))
        self._paint_region_spans()
        self._paint_moment_cursor()
        self._fig.autofmt_xdate(rotation=30)
        # The axis was just rebuilt, so the zoom history points at limits that no
        # longer exist. `ax.clear()` threw the view away; the stack has to go with it.
        self._xlim_stack = []
        self._canvas.draw_idle()
        self._install_span()

    def _paint_region_spans(self):
        shown = set(self._shown_days())
        for r in self._regions:
            if r["day"] not in shown:
                continue
            x0 = self._ns_to_x(r["t_start_ns"], r["day"])
            x1 = self._ns_to_x(r["t_end_ns"], r["day"])
            if x0 != x0 or x1 != x1:      # NaN — the day is not on the axis
                continue
            self._ax.axvspan(x0, x1, alpha=0.25, color=r["color"], zorder=0)

    def _paint_moment_cursor(self):
        """Every picked moment, on the axis it was picked from.

        Each one carries its ordinal, the same number the label and the wall use,
        so a set of picks can be read off the graph. Picks on a day the graph is
        not showing are simply not drawn — they are still picked."""
        if not self._moments:
            return
        shown = set(self._shown_days())
        for i, t_ns in enumerate(self._moments, 1):
            day = self._local_dt(t_ns).date()
            if day not in shown:
                continue
            x = self._ns_to_x(t_ns, day)
            if x != x:                   # NaN — not on this axis
                continue
            self._ax.axvline(x, color="#111111", lw=1.4, ls="--", zorder=5)
            # The number sits just under the top of the axes, in its own white box,
            # so it stays readable over a curve.
            self._ax.annotate(
                str(i), xy=(x, 1.0), xycoords=("data", "axes fraction"),
                xytext=(2, -3), textcoords="offset points",
                ha="left", va="top", fontsize=8, color="#111111", zorder=6,
                bbox=dict(boxstyle="round,pad=0.15", fc="#ffffff",
                          ec="#111111", lw=0.5))

    def _make_span(self, on_select, colour: str, button: int):
        """One span selector, bound to ONE mouse button.

        Left reads, right zooms — the rule the whole program follows. A selector
        left on its default answers to BOTH buttons, which is how a right-drag
        meant to zoom used to mark a region as well."""
        kw = dict(useblit=False, interactive=False, button=button)
        try:
            return self._SpanSelector(
                self._ax, on_select, "horizontal",
                props=dict(alpha=0.20, facecolor=colour), **kw)
        except TypeError:
            # matplotlib < 3.5 spells the same thing "rectprops".
            return self._SpanSelector(
                self._ax, on_select, "horizontal",
                rectprops=dict(alpha=0.20, facecolor=colour), **kw)

    def _install_span(self):
        for old in (self._span, self._zoom_span):
            if old is not None:
                try:
                    old.set_active(False)
                except Exception:
                    pass
        self._span = self._make_span(self._on_span, "#90CAF9", 1)
        self._zoom_span = self._make_span(self._on_zoom_span, "#FFCC80", 3)

    def _toolbar_busy(self) -> bool:
        """Pan or Zoom held down in the matplotlib toolbar. While one of those is
        armed the drag belongs to it, not to us."""
        try:
            return bool(getattr(self._toolbar, "mode", ""))
        except Exception:
            return False

    def _is_drag(self, x_from: float, x_to: float) -> bool:
        """Measured in PIXELS, not in seconds: a few seconds is a huge drag on a
        zoomed-in axis and no movement at all on a whole week."""
        try:
            (a, _), (b, _) = self._ax.transData.transform(
                [(x_from, 0.0), (x_to, 0.0)])
            return abs(b - a) > _PV_CLICK_SLOP_PX
        except Exception:
            return abs(x_to - x_from) > 0.0

    def _on_press(self, event):
        if event.inaxes is self._ax and event.xdata is not None:
            self._press_x = float(event.xdata)
        else:
            self._press_x = None

    def _on_release(self, event):
        """A click, as opposed to a drag. The span selectors have already had the
        drag; what is left for this is the click that did not move."""
        x0, self._press_x = self._press_x, None
        if x0 is None or self._toolbar_busy():
            return
        if event.inaxes is not self._ax or event.xdata is None:
            return
        if self._is_drag(x0, float(event.xdata)):
            return
        if event.button == 1:
            self._set_moment_from_x(float(event.xdata))
        elif event.button == 3:
            self._zoom_out()

    # ── The moments ────────────────────────────────────────────────────────
    @property
    def _moment_ns(self) -> "int | None":
        """The moment picked LAST — what a single-moment reader wants.

        Read-only on purpose: the picks live in `self._moments`, and every change
        goes through `_add_moment` / `_undo_pick` / `_clear_moment` so that the
        undo history, the label and the graph can never disagree with it."""
        return self._moments[-1] if self._moments else None

    def _moment_days(self) -> list:
        """The days the picked moments fall on, earliest first."""
        return sorted({self._local_dt(t).date() for t in self._moments})

    def _primary_series(self) -> list:
        """The samples the primary PV has on the day the click landed on — the ones
        a picked moment is snapped to."""
        ch = self._primary_cb.currentData()
        day = self._local_dt(self._moment_ns).date() if self._moment_ns else None
        if not ch or day is None:
            return []
        return (self._series.get(day) or {}).get(ch) or []

    def _snap_ns(self, t_ns: int, day) -> "int | None":
        """The primary PV's sample nearest `t_ns`.

        A moment BETWEEN two samples has no shot behind it, so the frames pulled for
        it would be an arbitrary pick. With no primary PV, or no samples on the day,
        the raw time stands — it is still better than refusing to answer."""
        ch = self._primary_cb.currentData()
        if not ch or day is None:
            return t_ns
        series = (self._series.get(day) or {}).get(ch) or []
        if not series:
            return t_ns
        stamps = [t for t, _ in series]
        j = bisect.bisect_left(stamps, t_ns)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(stamps):
                if best is None or abs(stamps[k] - t_ns) < abs(best - t_ns):
                    best = stamps[k]
        return best if best is not None else t_ns

    def _push_pick_undo(self):
        """Remember what was picked BEFORE the gesture about to happen."""
        self._pick_undo.append((list(self._moments),
                                [dict(r) for r in self._regions]))
        while len(self._pick_undo) > 200:
            self._pick_undo.pop(0)

    def _set_moment_from_x(self, x: float):
        """One click on the graph = one more moment on the list.

        It does NOT replace the previous pick and it does not throw the marked
        regions away: picks accumulate, across days as well, and Undo is what takes
        one back. A moment landing on the exact sample that is already picked is
        ignored rather than listed twice."""
        day = self._day_for_x(x)
        t_ns = self._x_to_ns(x)
        if t_ns is None or day is None:
            return
        t_ns = int(self._snap_ns(int(t_ns), day))
        if t_ns in self._moments:
            self._status.setText(
                self._local_dt(t_ns).strftime(
                    "%d.%m. %H:%M:%S is already picked — nothing added."))
            return
        self._push_pick_undo()
        self._moments.append(t_ns)
        self._redraw()
        self._sync_search_button()
        self._sync_moment_label()
        self._refresh_day_list()

    def _undo_pick(self):
        """Ctrl+Z / the Undo button — take the last pick back.

        It undoes marking a region as well as picking a moment, because both are
        the same gesture on the same graph and one button that only half worked
        would be worse than none."""
        if not self._pick_undo:
            self._status.setText("Nothing to undo — no moment or region picked yet.")
            return
        moments, regions = self._pick_undo.pop()
        self._moments = list(moments)
        self._regions = [dict(r) for r in regions]
        self._redraw()
        self._rebuild_regions_ui()
        self._sync_search_button()
        self._sync_moment_label()
        self._refresh_day_list()
        self._status.setText(
            f"Undone. {len(self._moments)} moment(s), "
            f"{len(self._regions)} region(s) left.")

    def _clear_moment(self):
        """Forget EVERY picked moment (the regions stay)."""
        if not self._moments:
            return
        self._push_pick_undo()
        self._moments = []
        self._redraw()
        self._sync_search_button()
        self._sync_moment_label()
        self._refresh_day_list()

    def _moment_list_text(self) -> str:
        """Every picked moment, one per line — for the tooltip."""
        out = []
        for i, t in enumerate(self._moments, 1):
            out.append(f"{i})  " + self._local_dt(t).strftime("%d.%m.%Y  %H:%M:%S"))
        return "\n".join(out)

    def _sync_moment_label(self):
        n = len(self._moments)
        self._btn_clear_moment.setEnabled(n > 0)
        self._btn_undo_pick.setEnabled(bool(self._pick_undo))
        if n == 0:
            self._lbl_moment.setText("No moment picked.")
            self._lbl_moment.setToolTip(
                "Click the graph to pick a moment. Every click adds one more — on "
                "this day or on any other marked day — and they are all searched "
                "together. Ctrl+Z takes the last one back.")
            return
        if n == 1:
            txt = self._local_dt(self._moments[0]).strftime("%d.%m.%Y  %H:%M:%S")
        else:
            days = len(self._moment_days())
            last = self._local_dt(self._moments[-1]).strftime("%d.%m. %H:%M:%S")
            txt = (f"{n} moments, {days} day{'s' if days != 1 else ''}"
                   f"  ·  last {last}")
        # Both picked at once is legal, but only the moments are searched. Said
        # here rather than by silently deleting the regions, which is what used to
        # happen and cost N drags to a single click.
        if self._regions:
            txt += "  ·  regions ignored"
        self._lbl_moment.setText(txt)
        self._lbl_moment.setToolTip(
            self._moment_list_text()
            + ("\n\nThe marked regions are ignored while a moment is picked — "
               "press Clear to search them instead." if self._regions else ""))

    def _zoom_out(self):
        """One step back out. Right click is "look wider", and it never changes
        which moment or which regions are picked."""
        if not self._xlim_stack:
            return
        lo, hi = self._xlim_stack.pop()
        self._ax.set_xlim(lo, hi)
        self._canvas.draw_idle()

    def _on_zoom_span(self, x_from: float, x_to: float):
        lo, hi = sorted((float(x_from), float(x_to)))
        if not self._is_drag(lo, hi):
            return
        self._xlim_stack.append(tuple(self._ax.get_xlim()))
        self._ax.set_xlim(lo, hi)
        self._canvas.draw_idle()

    def _on_span(self, xmin: float, xmax: float):
        if xmax - xmin < 1e-9:
            return
        # A left drag that did not really move is a CLICK, and a click picks a
        # moment — _on_release does that. Without this a click also left a
        # zero-width region behind it.
        if not self._is_drag(xmin, xmax):
            return
        # A drag in "all days" mode can start on one day and end on the next. It is
        # split at the boundary rather than silently clipped, so the marked time is
        # the time that was actually dragged over.
        day0 = self._day_for_x(xmin)
        day1 = self._day_for_x(max(xmin, xmax - 1e-9))
        if day0 is None and day1 is None:
            return
        if day0 is None:
            day0 = day1
        pieces = []
        if self._mode == "all" and day1 is not None and day1 != day0:
            i0, i1 = self._days.index(day0), self._days.index(day1)
            base = self._base_num()
            for i in range(i0, i1 + 1):
                a = max(xmin, base + i)
                b = min(xmax, base + i + 1)
                if b - a > 1e-9:
                    pieces.append((i, a, b))
        elif self._mode == "all":
            pieces.append((self._days.index(day0), xmin, xmax))
        else:
            pieces.append((None, xmin, xmax))

        # One undo step per DRAG, taken before anything changes; put straight back
        # if the drag turned out to mark nothing.
        self._push_pick_undo()
        added = 0
        for idx, a, b in pieces:
            if idx is None:
                day = day0
                t_start, t_end = self._x_to_ns(a), self._x_to_ns(b)
            else:
                # Per DAY, not through the global mapping: the right-hand edge of a
                # piece sits exactly on the next day's midnight, and asking the
                # global mapping for it would time-stamp the end of one day as the
                # start of the following one.
                day = self._days[idx]
                d0_ns, _ = self._day_bounds_for(day)
                base = self._base_num()
                fa = min(max(a - (base + idx), 0.0), 1.0)
                fb = min(max(b - (base + idx), 0.0), 1.0)
                t_start = int(d0_ns + fa * 86_400e9)
                t_end = int(d0_ns + fb * 86_400e9)
            if t_start is None or t_end is None or t_end <= t_start:
                continue
            rid = self._region_seq
            self._region_seq += 1
            color = _PV_REGION_COLORS[rid % len(_PV_REGION_COLORS)]
            self._regions.append({"id": rid, "t_start_ns": t_start, "t_end_ns": t_end,
                                  "color": color, "day": day})
            self._ax.axvspan(a, b, alpha=0.25, color=color, zorder=0)
            added += 1
        if not added:
            self._pick_undo.pop()
            return
        # A picked moment is NOT thrown away by marking a region. Both can be on
        # screen; the moments are what gets searched, and the label says so. It
        # used to delete them, which cost one click to redo — but the reverse
        # (a click deleting N drags) is what made this rule wrong in both
        # directions, so neither side deletes the other now.
        self._canvas.draw_idle()
        self._rebuild_regions_ui()
        self._refresh_day_list()
        self._sync_search_button()
        self._sync_moment_label()

    # ── Regions UI ──────────────────────────────────────────────────────────
    @staticmethod
    def _hms(ns) -> str:
        dt = datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)
        if PRAGUE is not None:
            dt = dt.astimezone(PRAGUE)
        return dt.strftime("%H:%M:%S")

    def _fmt_region_span(self, r: dict) -> str:
        """`10:12:33–10:19:01  (6m28s)` — the times and how long it is.

        The DAY is not in it: the rows are grouped under a day header now, and
        repeating the date on every row was what pushed the times out of the
        275 px sidebar."""
        a, b = int(r["t_start_ns"]), int(r["t_end_ns"])
        secs = max(0, (b - a) // 1_000_000_000)
        if secs >= 3600:
            length = f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
        elif secs >= 60:
            length = f"{secs // 60}m{secs % 60:02d}s"
        else:
            length = f"{secs}s"
        return f"{self._hms(a)}–{self._hms(b)}  ({length})"

    def _fmt_region(self, r: dict) -> str:
        """The old one-line form, day included — still used where there is no day
        header to carry it (the day list's tooltip)."""
        return f"{r['day'].strftime('%d.%m')}  {self._fmt_region_span(r)}"

    def _rebuild_regions_ui(self):
        while self._regions_lay.count():
            item = self._regions_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # Unparent BEFORE deleteLater: a widget only taken out of the layout
                # keeps its parent and goes on painting where it was until the delete
                # is actually delivered, which drew the old rows over the new ones.
                w.setParent(None)
                w.deleteLater()
        if not self._regions:
            empty = QLabel("Drag on the graph\nto add a region.")
            empty.setStyleSheet("color:#666;font-size:11px;")
            self._regions_lay.addWidget(empty)
            self._regions_lay.addStretch(1)
            if hasattr(self, "_stats_cb"):
                self._refresh_stats_combo()
                self._refresh_stats()
            return
        # Grouped by day, numbered inside the day — the same numbering the wall's
        # row banners use, so "region 2" is one thing in both places.
        by_day = self._regions_by_day()
        for day in sorted(by_day.keys()):
            lst = by_day[day]
            head = QLabel(f"{_fmt_day_long(day)}   —   {len(lst)} region"
                          f"{'s' if len(lst) != 1 else ''}")
            head.setStyleSheet(
                "QLabel { background:#333333; color:#ffffff; font-size:11px;"
                " font-weight:700; padding:2px 4px; }")
            self._regions_lay.addWidget(head)
            for i, r in enumerate(lst, 1):
                row = QHBoxLayout()
                row.setContentsMargins(2, 0, 2, 0)
                num = QLabel(f"{i})")
                num.setStyleSheet("font-size:11px; color:#555555;")
                num.setFixedWidth(18)
                dot = QLabel("■")
                dot.setStyleSheet(f"color:{r['color']};font-size:13px;")
                lbl = QLabel(self._fmt_region_span(r))
                lbl.setStyleSheet("font-size:11px; color:#111111;")
                # Elide in the MIDDLE: the sidebar is 275 px and the end of the
                # line (the length) must not be the half that is lost.
                lbl.setTextFormat(Qt.TextFormat.PlainText)
                lbl.setToolTip(self._fmt_region(r))
                btn = QToolButton(); btn.setText("✕")
                btn.setFixedSize(20, 20)
                btn.setStyleSheet(
                    "QToolButton { background:#e8e8e8; color:#111;"
                    " border:1px solid #9a9a9a;"
                    " border-radius:3px; font-weight:700; }"
                    "QToolButton:hover { background:#ffffff; }")
                btn.setToolTip("Delete region")
                btn.clicked.connect(
                    lambda _=False, rid=r["id"]: self._delete_region(rid))
                row.addWidget(num); row.addWidget(dot)
                row.addWidget(lbl, 1); row.addWidget(btn)
                w = QWidget(); w.setLayout(row)
                w.setFixedHeight(24)
                self._regions_lay.addWidget(w)
        self._regions_lay.addStretch(1)
        # The statistics below the graph describe one of these regions, so they are
        # rebuilt from the same place the rows are — every caller gets both.
        if hasattr(self, "_stats_cb"):
            self._refresh_stats_combo()
            self._refresh_stats()

    def _delete_region(self, rid: int):
        self._regions = [r for r in self._regions if r["id"] != rid]
        self._redraw()
        self._rebuild_regions_ui()
        self._refresh_day_list()

    def _clear_regions(self):
        self._regions = []
        self._redraw()
        self._rebuild_regions_ui()
        self._refresh_day_list()

    # ── Accept ────────────────────────────────────────────────────────────
    def _on_accept(self):
        title = "PV Search"
        # No camera check here on purpose. Which cameras to look at and which
        # moments to look at are two independent halves of one question, and this
        # window owns only the second: the tab asks for the cameras when it has to,
        # whichever half was answered first.
        if self._cond_is_on():
            if self._cond_scope_regs.isChecked() and not self._regions:
                QMessageBox.information(
                    self, title,
                    "The condition is set to search the marked regions, but no "
                    "region is marked. Drag one on the graph, or switch the "
                    "condition to whole days."); return
            if not self._days:
                QMessageBox.information(self, title, "Mark at least one day."); return
            self.accept()
            return
        if self._moments:
            # A moment needs no primary PV: the time was pointed at, not derived
            # from a peak. (One is still used to snap the click, when there is one.)
            self.accept()
            return
        if not self._regions:
            QMessageBox.information(
                self, title,
                "Click the graph to pick a moment — every click adds one more — "
                "or drag to mark a region.")
            return
        if self._primary_cb.currentData() is None:
            QMessageBox.information(self, title,
                                    "Check at least one PV and pick a primary PV."); return
        self.accept()

    def _regions_by_day(self) -> dict:
        """{day: [region, …]} with the regions of each day in TIME order.

        The one place the numbering comes from — the sidebar rows, the day list's
        tooltip, the config handed to the search and the row banner on the wall all
        read it, so "region 2" means the same thing everywhere."""
        out: dict = {}
        for r in self._regions:
            out.setdefault(r["day"], []).append(r)
        for day, lst in out.items():
            lst.sort(key=lambda r: (r["t_start_ns"], r["t_end_ns"]))
        return out

    def get_config(self) -> dict:
        # Regions as DICTS, not bare (start, end) pairs: the number, the colour and
        # the day have to survive the trip to the wall, or four regions on one day
        # arrive as four frames nothing can tell apart — which is exactly how they
        # ended up sharing one row.
        regions_by_day: dict = {}
        for day, lst in self._regions_by_day().items():
            n = len(lst)
            regions_by_day[day] = [
                {"t_start_ns": int(r["t_start_ns"]),
                 "t_end_ns":   int(r["t_end_ns"]),
                 "index":      i + 1,
                 "count":      n,
                 "color":      r.get("color"),
                 "label":      self._fmt_region_span(r)}
                for i, r in enumerate(lst)]
        cond = None
        if self._cond_is_on():
            cond = {
                "channel": self._cond_pv_cb.currentData(),
                "label":   self._cond_pv_cb.currentText(),
                "op":      self._cond_op_cb.currentData(),
                "value":   float(self._cond_val.value()),
                "value2":  float(self._cond_val2.value()),
                "scope":   ("regions" if self._cond_scope_regs.isChecked()
                            else "days"),
            }
        # In condition mode the search covers every MARKED day, not only the days
        # that happen to carry a region.
        days = (sorted(self._days) if cond is not None
                else sorted(regions_by_day.keys()))
        return {
            "cameras":         self._cams,
            "days":            days,
            "regions":         regions_by_day,
            "condition":       cond,
            # Set only when a moment was clicked, and then it is the whole answer:
            # the tab reads it and goes straight to the frames. `moments_ns` is
            # every pick in the order they were clicked; `moment_ns` is the first
            # of them, so a reader that only understands one moment still works.
            "moments_ns":      ([] if cond is not None
                                else [int(t) for t in self._moments]),
            "moment_ns":       (None if cond is not None or not self._moments
                                else int(self._moments[0])),
            "primary_channel": self._primary_cb.currentData(),
            # Every sample of the primary PV over the days that were read. The TAB
            # keeps these after this window closes, so its prev/next shot arrows
            # can walk the day without reopening it.
            "snap_stamps":     self._snap_stamps(),
            "start_hour":      0,
            "max_hour":        23,
        }

    def _snap_stamps(self) -> "list[int]":
        """The primary PV's sample times over every day read, sorted."""
        ch = self._primary_cb.currentData()
        if not ch:
            return []
        out: list = []
        for d in sorted(self._series.keys()):
            out.extend(int(t) for (t, _v) in
                       ((self._series.get(d) or {}).get(ch) or []))
        out.sort()
        return out


# ── MULTI-DAY PREVIEW WINDOW ──────────────────────────────────────────────────
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday")


def _fmt_day_long(day) -> str:
    """'Monday  03.08.2026'. Spelled out rather than %A so the banner reads the same on
    a machine with a Czech locale as it does in the rest of the app's English text."""
    try:
        return f"{_WEEKDAYS_EN[day.weekday()]}  {day.strftime('%d.%m.%Y')}"
    except Exception:
        return str(day or "")


class _WallSignals(QObject):
    tile_ready = Signal(int, int)      # (cell index, generation)
    all_done   = Signal(int)           # (generation)


class _WallShared:
    """Everything several walls must agree on, keyed by the frame's own file path.

    One search now fills a stack of walls — one per camera plus the day-by-day view —
    and they show THE SAME frames. Two things follow. A frame must be read from the
    share once, not once per tab (a read over the share is 130-160 ms, and a fortnight
    of ten cameras is 140 of them). And a frame the user adjusted or drew on in its
    camera tab must look adjusted and drawn on in the day-by-day tab too, so none of
    that state may live in a widget.

    Keyed by path rather than by tile index because the index means something different
    on every wall.
    """

    def __init__(self):
        self.raw: "dict[Path, tuple]" = {}      # path → (float32 array, full_scale)
        self.adj: "dict[Path, dict]"  = {}      # path → {contrast, offset, gamma, rot}
        self.ov:  "dict[Path, dict]"  = {}      # path → overlay shapes (see _OverlayState)
        self.sel: "set" = set()                 # selected paths
        self.undo: "list[dict]" = []            # snapshots of adj/ov/sel
        # A mark drawn on one frame appears on every frame of the wall, at the same
        # relative point. On by default: the reason to draw a circle on a beam at
        # all is almost always to ask whether the OTHER frames sit inside it.
        self.link_marks = True

    # ── undo ──────────────────────────────────────────────────────────────────
    def push_undo(self):
        """Snapshot before a change. Bounded — an undo stack that grows all session is
        one more slow leak, and nobody steps back more than a handful of edits."""
        self.undo.append({
            "adj": {k: dict(v) for k, v in self.adj.items()},
            "ov":  {k: _copy_overlay(v) for k, v in self.ov.items()},
            "sel": set(self.sel),
        })
        if len(self.undo) > 30:
            del self.undo[0]

    def pop_undo(self) -> bool:
        if not self.undo:
            return False
        st = self.undo.pop()
        self.adj = st["adj"]
        self.ov  = st["ov"]
        self.sel = st["sel"]
        return True

    def clear_edits(self):
        self.adj.clear()
        self.ov.clear()


class _WallScroll(QScrollArea):
    """The pane a wall sits in, with Ctrl+wheel over it as the size control.

    The frames grow INSIDE the pane: the window does not move and neither does
    anything else in the tab. A plain wheel still scrolls, which is how a wall
    taller than its pane gets read — and on the Day-by-day wall one notch steps a
    whole DAY, because a row there IS a day and half a day of scroll is a view of
    nothing in particular.

    The wall itself has no use for a wheel event, so it arrives here on its own —
    no filter on every child is needed. The app-wide wheel guard does not touch
    this: it only stands between the wheel and spin boxes, drop-downs and sliders.
    """

    zoomed = Signal(int)                 # notches: + is bigger, - is smaller

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The wall cannot work its own pane size out — zoomed in it is bigger than
        # the pane on purpose. So it is told. See _DayWall._avail.
        w = self.widget()
        if isinstance(w, _DayWall):
            vp = self.viewport()
            w.set_canvas(vp.width(), vp.height())

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            notches = event.angleDelta().y() / 120.0
            if notches:
                # Accepted, so the pane does NOT also scroll: a zoom that runs away
                # down the wall is a zoom nobody can aim.
                event.accept()
                self.zoomed.emit(int(round(notches))
                                 or (1 if notches > 0 else -1))
                return
        # A plain wheel on the Day-by-day wall steps whole days, one row a notch.
        w = self.widget()
        if isinstance(w, _DayWall) and w.layout_mode() == "rows":
            notches = event.angleDelta().y() / 120.0
            if notches:
                step = int(round(notches)) or (1 if notches > 0 else -1)
                bar = self.verticalScrollBar()
                bar.setValue(bar.value() - step * w.row_pitch())
                event.accept()
                return
        super().wheelEvent(event)


def _copy_overlay(o: dict) -> dict:
    out = dict(o)
    for k in ("circle", "square", "cross"):
        if isinstance(out.get(k), dict):
            out[k] = dict(out[k])
    return out


class _DayWall(QWidget):
    """Days next to each other — the transpose of the Image Slider's camera grid.

    The Slider answers "what did the machine look like at ONE moment" and tiles many
    cameras. This answers "how did ONE camera change over MANY days" and tiles the
    days. Geometrically that is the same problem, so it borrows the Slider's own
    packer (`compute_camera_layout`) instead of carrying a second one: the tiles are
    a seamless partition of the canvas, the smallest day is made as large as the
    arrangement allows, and two tiles sharing an edge land on the same pixel.

    One display setting drives every tile BY DEFAULT, which is the opposite of the
    Slider's rule. Here the whole point of the view is that one colour means one
    intensity in every day, so a tile is NEVER stretched by its own min/max, and every
    frame goes on ITS CAMERA's fixed sensor range via `img_scale.full_scale_for_pil`.

    Both of those were wrong in the thumbnail grid this replaces: it rendered every
    camera against 65535 instead of the sensor range, and then stretched each
    thumbnail by its own min/max — so two days of visibly different strength came out
    looking equally bright, which is the one thing a comparison view must not do.

    A single frame CAN be given its own brightness, contrast, gamma and rotation: pick
    it and move a control, exactly as in the Slider, where a control hits whatever was
    selected at the moment it was moved. Somebody comparing ten days needs to be able
    to open up the one dim day without flattening the other nine. But a tile rendered
    on different terms from its neighbours is no longer comparable with them, so it has
    to SAY SO — an adjusted tile carries a mark in its caption strip, and Reset puts
    every frame back on the shared setting.

    Auto and the reference-day deviation are deliberately not part of that: both work
    off the raw frames (`_shared_auto_pair`, `set_baseline`) and stay wall-wide, so the
    two readings that must be comparable to mean anything always are.
    """

    tile_clicked = Signal(int)         # index into the cell list
    tile_context = Signal(int, object) # (index, global QPoint) — right-click menu
    selection_changed = Signal()

    _GAP       = 3                    # px between tiles, so captions cannot touch
    _CAPTION_H = 20                   # px reserved under each frame for its day label
    _DAY_HDR_H = 22                   # px for the day banner in rows-by-day mode
    _HANDLE_R  = 7                    # overlay grab handle radius
    # Never more than four frames across. Beyond that a row of thumbnails is not a
    # comparison any more — and it is the operators' own number.
    _MAX_COLS  = 4
    _ZOOM_MIN, _ZOOM_MAX = 0.5, 8.0
    _ZOOM_STEP = 1.15

    def __init__(self, parent=None, shared: "_WallShared | None" = None):
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background:#1a1a1a;")
        self.setMouseTracking(True)

        self._shared = shared if shared is not None else _WallShared()
        self._cells: "list[dict]" = []      # {day, cam, path, ts_ns, status}
        # Alias, not a copy: every wall reads the frames through the same cache.
        self._raw = self._shared.raw            # path → (float32 array, full_scale)
        self._pix:   "dict[int, tuple]" = {}    # cell index → (per-frame key, pixmap)
        self._rects: "list[QRect]" = []
        self._img_rects: "list[QRect]" = []     # where the picture itself landed
        self._render_key = None             # display state the cached pixmaps belong to
        self._baseline_idx: "int | None" = None
        self._hover = -1
        self._load_gen = 0
        # "grid" — every tile the same size, at most four across, as large as the
        #          pane allows. What a comparison wall wants, and the default.
        # "rows" — one row per day, the cameras always in the same order.
        # "pack" — the Slider's free-form partition, biggest frame wins. Kept only
        #          for a caller that explicitly asks for it.
        self._layout_mode = "grid"
        self._row_heads: "list[tuple[QRect, str]]" = []   # day banners in rows mode
        self._multi_cam = False             # set by set_cells — see _caption
        self._zoom = 1.0                    # 1.0 = fits the pane exactly
        self._fit_w = self._fit_h = 0       # last known pane size — see _avail

        # Display state — the shared values every un-adjusted tile uses.
        self._grad_name = "Grayscale"
        self._auto      = False
        self._gamma     = None
        self._contrast  = 0
        self._offset    = 0
        self._auto_pair = None              # shared (contrast, offset) standing in for Auto

        # Overlay drawing
        self._draw_mode = ""                # "" | "circle" | "square" | "cross"
        self._shape_colors = {"circle": QColor(255, 255, 0, 230),
                              "square": QColor(0, 200, 255, 230),
                              "cross":  QColor(0, 255, 0, 220)}
        self._drag_idx    = -1
        self._drag_handle = ""
        self._drag_start  = None
        self._did_drag    = False

        self._sig = _WallSignals()
        self._sig.tile_ready.connect(self._on_tile_ready)

    # ── contents ──────────────────────────────────────────────────────────────
    def cells(self) -> "list[dict]":
        return list(self._cells)

    def set_cells(self, cells: "list[dict]"):
        """Replace what the wall shows. Frames are read in the background so a slow
        share fills the wall progressively instead of blocking the window."""
        self._load_gen += 1
        self._cells = [dict(c) for c in cells]
        # What a tile has to be identified BY depends on what the wall holds. Many
        # days of one camera → the day; many cameras at one moment → the camera,
        # because then the day is the same on every tile and saying it ten times
        # over tells the operator nothing.
        self._multi_cam = len({c.get("cam", "") for c in self._cells}) > 1
        self._pix.clear()
        self._rects = []
        self._auto_pair = None
        self._baseline_idx = None
        self._render_key = None
        self._hover = -1
        self._relayout()
        self.update()
        if self._cells:
            self._kick_load(self._load_gen)

    # How many frames are read from the share at once. A read is ~130-160 ms of
    # WAITING on the network, so the workers cost nothing while they queue; what
    # they must not do is swamp the share, hence a bound rather than one per tile.
    _READ_WORKERS = 8

    @staticmethod
    def _read_raw(p: Path):
        """One frame → (float32 array, full scale), or None if it cannot be read.

        The bytes are pulled into memory FIRST and PIL is handed a buffer. Reading
        straight off the UNC path is the measured slow path, and going through
        `_read_frame_bytes` also brings the mid-write wait with it: a frame caught
        while the archiver is still writing it is waited out (it checks for the
        PNG end marker) instead of coming back as a broken tile."""
        data = None
        try:
            data = _get_slider_module()._read_frame_bytes(p)
        except Exception:
            data = None
        try:
            src = BytesIO(data) if data else str(p)
            with PilImage.open(src) as pil:
                mode = pil.mode
                info = dict(pil.info or {})
                if mode in ("I", "I;16"):
                    arr = np.array(pil, dtype=np.float32)
                else:
                    arr = np.array(pil.convert("L"), dtype=np.float32)
            return arr, float(img_scale.full_scale_for_pil(p, info, mode))
        except Exception:
            return None

    def _kick_load(self, gen: int):
        paths = [c.get("path") for c in self._cells]
        todo = [(i, p) for i, p in enumerate(paths)
                if p is not None and p not in self._raw]

        def worker():
            # Frames already in the shared cache need no read — say so at once, so a
            # wall that is entirely a revisit goes up without touching the share.
            for i, p in enumerate(paths):
                if gen != self._load_gen:
                    return
                if p is not None and p in self._raw:
                    self._sig.tile_ready.emit(i, gen)
            if todo:
                def one(job):
                    i, p = job
                    if gen != self._load_gen:
                        return None
                    self._raw[p] = self._read_raw(p)
                    return i
                workers = min(self._READ_WORKERS, len(todo))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for i in ex.map(one, todo):
                        if gen != self._load_gen:
                            return
                        if i is not None:
                            self._sig.tile_ready.emit(i, gen)
            self._sig.all_done.emit(gen)

        threading.Thread(target=worker, daemon=True).start()

    def _on_tile_ready(self, idx: int, gen: int):
        if gen != self._load_gen:
            return
        # A frame's real aspect ratio only becomes known once it is read, so the
        # arrangement is recomputed as they arrive rather than guessed up front. A new
        # frame also widens the pooled Auto window, so that is dropped too.
        self._auto_pair = None
        self._pix.pop(idx, None)
        self._relayout()
        self.update()

    # ── display state ─────────────────────────────────────────────────────────
    def set_display(self, grad_name: str, auto: bool, gamma, contrast: int, offset: int):
        key = (grad_name, bool(auto), gamma, int(contrast), int(offset))
        if key == (self._grad_name, self._auto, self._gamma, self._contrast, self._offset):
            return
        self._grad_name, self._auto, self._gamma, self._contrast, self._offset = (
            grad_name, bool(auto), gamma, int(contrast), int(offset))
        self._auto_pair = None
        self._pix.clear()
        self.update()

    def _shared_auto_pair(self):
        """ONE contrast/brightness pair standing in for Auto across the whole wall.

        `img_scale.stretch_u8` says of itself "NOT comparable between frames", and it is
        right: a percentile stretch taken per tile levels every day to its own content,
        so a strong day and a weak one come out equally bright and the comparison is
        worthless. So the window is taken ONCE over a pooled sample of every day on the
        wall, converted to the equivalent manual pair (the conversion `stretch_u8`
        already documents and returns), and that same pair is then applied to every tile
        through the ordinary absolute path. Auto still does its job — dim cameras become
        visible — without any day being measured against itself."""
        if self._auto_pair is not None:
            return self._auto_pair
        samples = []
        for c in self._cells:
            entry = self._raw.get(c.get("path"))
            if not entry:
                continue
            arr, full_scale = entry
            if not full_scale:
                continue
            # Each day is brought onto ONE common range BEFORE being pooled. The stored
            # values cannot be pooled as they are: the archiver stretched every frame by
            # its own bracket, so a stored 51299 is 400 counts on a dim day and 3200 on
            # a bright one. Pooling them raw asks for the percentiles of a mixture of
            # different units, and the pair that came out saturated every brighter day
            # to white — the exact flattening this whole view exists to avoid.
            scaled = arr.astype(np.float32) * (img_scale.FULL_SCALE_16 / float(full_scale))
            samples.append(img_scale.stat_sample(scaled).ravel())
        if not samples:
            return (0, 0)
        pooled = np.concatenate(samples)
        out: dict = {}
        img_scale.stretch_u8(pooled, full_scale=img_scale.FULL_SCALE_16, out=out)
        self._auto_pair = (int(out.get("contrast", 0)), int(out.get("offset", 0)))
        return self._auto_pair

    def set_baseline(self, idx: "int | None"):
        """Pick one day as the reference; every other tile then shows |day − reference|.
        Honest without any extra bookkeeping because both frames are already on the
        same absolute scale."""
        if idx == self._baseline_idx:
            return
        self._baseline_idx = idx
        self._pix.clear()
        self.update()

    def baseline_idx(self) -> "int | None":
        return self._baseline_idx

    # ── per-frame state ───────────────────────────────────────────────────────
    def set_layout_mode(self, mode: str):
        """"grid" — every tile the same size, at most four across, as large as they go.
        "rows" — one row per day, the cameras always in the same order, for reading down
        a column and seeing one camera change.
        "pack" — the Slider's free-form partition, where the biggest frame wins."""
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        self._pix.clear()
        self._relayout()
        self.update()

    def layout_mode(self) -> str:
        """Which of the three layouts this wall is on. The pane asks, so a plain
        wheel can step whole days on the Day-by-day wall and scroll on the others."""
        return self._layout_mode

    # ── zoom ──────────────────────────────────────────────────────────────────
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, z: float) -> bool:
        """1.0 fits the pane; above that the tiles grow and the pane scrolls.

        No debounce: a notch is a geometry pass and a cleared pixmap cache, and Qt
        coalesces the repaints that follow. Deferring it would only make the wheel
        feel like it was lagging behind the hand."""
        z = max(self._ZOOM_MIN, min(self._ZOOM_MAX, float(z)))
        if abs(z - self._zoom) < 1e-6:
            return False
        self._zoom = z
        self._pix.clear()          # tiles are rendered to the size they are drawn at
        self._relayout()
        self.update()
        return True

    def zoom_by_notches(self, notches: int) -> bool:
        return self.set_zoom(self._zoom * (self._ZOOM_STEP ** int(notches)))

    def reset_zoom(self) -> bool:
        return self.set_zoom(1.0)

    def selected_paths(self) -> set:
        return set(self._shared.sel)

    def set_selected(self, path, on: bool):
        if on:
            self._shared.sel.add(path)
        else:
            self._shared.sel.discard(path)
        self.update()
        self.selection_changed.emit()

    def clear_selection(self):
        if not self._shared.sel:
            return
        self._shared.sel.clear()
        self.update()
        self.selection_changed.emit()

    def apply_adjust(self, paths, contrast: int, offset: int, gamma, rot: "int | None" = None):
        """Give these frames their own contrast / brightness / gamma (and rotation).
        `paths` empty means the whole wall goes back to the shared setting."""
        for p in paths:
            cur = dict(self._shared.adj.get(p) or {})
            cur.update({"contrast": int(contrast), "offset": int(offset), "gamma": gamma})
            if rot is not None:
                cur["rot"] = int(rot) % 360
            self._shared.adj[p] = cur
        self._pix.clear()
        self.update()

    def rotate(self, paths, delta: int):
        """Turn these frames by ±90°. With nothing selected the caller passes every
        frame on the wall, so the gesture still reads as 'rotate the pictures'."""
        for p in paths:
            cur = dict(self._shared.adj.get(p) or {})
            cur["rot"] = (int(cur.get("rot", 0)) + delta) % 360
            self._shared.adj[p] = cur
        self._pix.clear()
        self._relayout()          # a turned frame has a different shape
        self.update()

    def clear_adjust(self):
        self._shared.adj.clear()
        self._pix.clear()
        self._relayout()
        self.update()

    def refresh_edits(self):
        """Re-render after somebody else changed the shared adjustments or overlays."""
        self._pix.clear()
        self._relayout()
        self.update()

    # ── overlays ──────────────────────────────────────────────────────────────
    def set_draw_mode(self, mode: str):
        self._draw_mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_shape_color(self, kind: str, color: QColor):
        self._shape_colors[kind] = QColor(color)
        self.update()

    def clear_overlays(self, paths=None):
        if paths is None:
            self._shared.ov.clear()
        else:
            for p in paths:
                self._shared.ov.pop(p, None)
            # While the marks are linked, clearing ONE clears them all — otherwise
            # the next drag would simply mirror the mark straight back onto it, and
            # the menu entry would look broken.
            if self._shared.link_marks:
                for c in self._cells:
                    self._shared.ov.pop(c.get("path"), None)
        self.update()

    def _ov_for(self, path) -> dict:
        o = self._shared.ov.get(path)
        if o is None:
            o = self._shared.ov[path] = {}
        return o

    def _mirror_marks(self, src_path):
        """Put the marks now on `src_path` onto every other frame of this wall.

        A mark is stored as FRACTIONS of the frame it is drawn on (a circle is a
        centre and two radii between 0 and 1), so "the same point on every frame"
        is nothing more than copying those numbers across. What that means is worth
        being exact about: over many days of ONE camera it is the same sensor pixel;
        across cameras of different shape it is the same RELATIVE point, not the
        same micrometre. And the fractions are of the frame AS DRAWN, so a frame
        turned 90° wears its mark at the same place on screen, not on the sensor.

        Copied on every drag step rather than on release, so the mark grows on all
        the tiles at once under the hand. It is a handful of dicts of floats.
        """
        if not self._shared.link_marks:
            return
        src = self._shared.ov.get(src_path)
        for c in self._cells:
            p = c.get("path")
            if p is None or p == src_path:
                continue
            if src:
                self._shared.ov[p] = _copy_overlay(src)
            else:
                self._shared.ov.pop(p, None)

    # ── layout ────────────────────────────────────────────────────────────────
    def _rot_of(self, cell: dict) -> int:
        return int((self._shared.adj.get(cell.get("path")) or {}).get("rot", 0)) % 360

    def _aspects(self) -> list:
        out = []
        for c in self._cells:
            entry = self._raw.get(c.get("path"))
            if entry:
                arr = entry[0]
                h, w = arr.shape[:2]
                if self._rot_of(c) in (90, 270):
                    w, h = h, w
                out.append(max(0.05, float(w) / max(1.0, float(h))))
            else:
                out.append(4.0 / 3.0)     # placeholder until the frame is read
        return out

    # How many rows of the Day-by-day wall fit the pane before it scrolls. The
    # operator's own measure: "some normal size … about three rows".
    _ROWS_IN_VIEW = 3

    @staticmethod
    def _cell_row_key(cell: dict):
        """The row a cell belongs on: THE DAY.

        A row is a day and the next row is the next day — the operator's rule.
        Several picks on one day (four marked regions, five picked moments) do not
        split it into four rows; they sit side by side inside that one row, which is
        what `_cell_col_key` is for."""
        return cell.get("day")

    @staticmethod
    def _cell_col_key(cell: dict) -> tuple:
        """The column a cell belongs in: `(camera, which pick)`.

        A column is ONE camera all the way down the wall. When a day carries several
        picks that camera owns several adjacent columns — the same columns in every
        row, so a day that is missing a pick leaves a gap rather than shifting the
        camera underneath a different one. Keying the column on the camera alone is
        what gave four frames of one day the same rectangle: three were painted
        under the fourth and clicking picked one that was not on screen."""
        reg = cell.get("region") or {}
        idx = cell.get("pick") or reg.get("index")
        return (cell.get("cam", ""), idx if idx is not None else 0)

    def _row_order(self) -> "tuple[list, list]":
        """(days top to bottom, columns left to right).

        The columns are sorted by camera NAME and then by the pick's own number, and
        the same list is used for every row — that is the whole reason to look at the
        wall this way."""
        rows, cols = [], []
        for c in self._cells:
            k, col = self._cell_row_key(c), self._cell_col_key(c)
            if k not in rows:
                rows.append(k)
            if col not in cols:
                cols.append(col)
        rows.sort(key=lambda d: str(d))
        cols.sort(key=lambda t: (str(t[0]), t[1]))
        return rows, cols

    def _row_head_text(self, day) -> str:
        """The banner over one row: the day, and what is on it when the day carries
        several picks."""
        head = _fmt_day_long(day)
        picks = sorted({self._cell_col_key(c)[1] for c in self._cells
                        if self._cell_row_key(c) == day
                        and (c.get("pick") or (c.get("region") or {}).get("index"))})
        if len(picks) > 1:
            kind = "moments" if any(c.get("pick") for c in self._cells) else "regions"
            head += f"   ·   {len(picks)} {kind}"
        return head

    def row_pitch(self) -> int:
        """The height of one row including its banner — what a wheel notch steps."""
        rows, cols = self._row_order()
        if not rows or not cols:
            return max(1, self._DAY_HDR_H + 90)
        _W, H = self._avail()
        row_h = max(90, int(H / self._ROWS_IN_VIEW) - self._DAY_HDR_H)
        return self._DAY_HDR_H + row_h

    def rows_content_height(self) -> int:
        """How tall the wall needs to be in rows-by-day mode, so the scroll area that
        holds it knows what to scroll.

        The row height comes from the PANE, not from the frames' aspect: three rows
        fill it and the rest is scrolled to. Sizing a row off the mean aspect made a
        row as tall as one frame wanted to be, which with wide frames left two days
        visible and with tall ones eight."""
        rows, cols = self._row_order()
        if not rows or not cols:
            return max(1, self.height())
        return len(rows) * self.row_pitch()

    def _relayout_rows(self):
        """One row per day; one column per (camera, pick), the same in every row."""
        rows, cols = self._row_order()
        self._rects = [QRect() for _ in self._cells]
        self._row_heads = []
        if not rows or not cols:
            return
        W = max(1, self.width())
        pitch = self.row_pitch()
        row_h = pitch - self._DAY_HDR_H
        total_h = len(rows) * pitch
        pos = {}
        n = len(cols)
        for r, day in enumerate(rows):
            y = r * pitch
            self._row_heads.append((QRect(0, y, W, self._DAY_HDR_H),
                                    self._row_head_text(day)))
            for cidx, col in enumerate(cols):
                x0 = int(round(cidx * W / n))
                x1 = int(round((cidx + 1) * W / n))
                pos[(day, col)] = QRect(x0, y + self._DAY_HDR_H,
                                        max(20, x1 - x0), row_h)
        for i, c in enumerate(self._cells):
            self._rects[i] = pos.get((self._cell_row_key(c), self._cell_col_key(c)),
                                     QRect(0, 0, 0, 0))
        self.setMinimumHeight(total_h)

    def _avail(self) -> "tuple[int, int]":
        """The room the tiles have to fit into.

        Zoomed in, this widget is deliberately BIGGER than the pane it sits in — so
        a fit measured against its own size would see the size the last pass asked
        for and grow again, every pass.

        So the pane's size is not guessed from this widget at all — the pane STATES
        it, through `set_canvas`, and `_WallScroll` calls that whenever its viewport
        changes. Every attempt to infer it instead was wrong in one direction or the
        other: Qt does not shrink a widget back when its minimum is relaxed, so the
        stretched height lingers and reads as a huge pane; and a scroll area that
        has not been laid out yet answers with a placeholder viewport size.

        Falling back to this widget's own size covers only the moment before anyone
        has stated one — the first paint, or a wall standing on its own in a test.
        """
        return (self._fit_w or max(1, self.width()),
                self._fit_h or max(1, self.height()))

    def set_canvas(self, w: int, h: int):
        """The pane says how much room there is. Called by `_WallScroll`."""
        w, h = max(1, int(w)), max(1, int(h))
        if (w, h) == (self._fit_w, self._fit_h):
            return
        self._fit_w, self._fit_h = w, h
        self._pix.clear()          # tiles are rendered to the size they are drawn at
        self._relayout()
        self.update()

    def _relayout_grid(self):
        """Every tile the same size, at most four across, as large as they go.

        Two rules that pull against each other, resolved in that order:

          * SAME SIZE. One cell size for the whole wall. Comparing frames means
            comparing them at one magnification, and the free-form partition this
            replaces made the biggest frame the biggest tile — so the day worth
            looking at was whichever day happened to be widest.
          * AS LARGE AS THEY GO. The column count is not fixed at four; every count
            up to four is tried and the one that makes the SMALLEST drawn picture
            largest wins. Two frames therefore come out bigger than three, and four
            portrait frames go in a row where four landscape ones go two by two.

        Zoom scales the cell and then re-flows the columns, so growing the tiles
        only ever makes the wall taller — it never scrolls sideways.
        """
        n = len(self._cells)
        W, H = self._avail()
        aspects = self._aspects()
        top = self._CAPTION_H + 2 * self._GAP

        best = None
        for cols in range(1, min(self._MAX_COLS, n) + 1):
            rows = (n + cols - 1) // cols
            cw, ch = W / cols, H / rows
            pic_h = ch - top
            pic_w = cw - 2 * self._GAP
            if pic_h <= 1 or pic_w <= 1:
                continue
            # The smallest picture on the wall, which is the one being maximised.
            worst = min((min(pic_w, pic_h * a) * min(pic_w / a, pic_h))
                        for a in aspects)
            # Ties go to fewer rows: the same size with less to scroll past.
            key = (worst, -rows)
            if best is None or key > best[0]:
                best = (key, cols, cw, ch)

        if best is None:
            # Nowhere to put anything (the pane is a few pixels tall). One column,
            # and let the scroll area carry it.
            cols, cw, ch = 1, float(W), float(max(top + 20, H))
        else:
            _, cols, cw, ch = best

        shape = ch / cw if cw > 0 else 1.0
        x_off = 0.0
        if abs(self._zoom - 1.0) > 1e-6:
            # Zooming asks for a cell exactly `zoom` times as wide, and it gets it:
            # every notch of the wheel changes the size of the frames. As many cells
            # as fit go in a row, and whatever width is left over is split EVENLY on
            # both sides, so the row sits centred in the pane.
            #
            # Both of the obvious alternatives were tried and are worse. Snapping
            # the cell up to fill the row exactly gave five notches in a row that
            # changed nothing at all (every width between "one tile fills the pane"
            # and "one tile is wider than it" snaps to the same thing). Leaving the
            # leftover at the right-hand end instead put up to half the pane black
            # beside a single tile, which reads as a broken window rather than a
            # deliberate margin.
            #
            # At zoom 1 the asked-for width IS the fitted width, so nothing is left
            # over and the tiles still tile the pane edge to edge.
            cw = cw * self._zoom
            ch = cw * shape
            # The epsilon matters: at zoom 1 the exact fit divides to 2.9999… and
            # would come out one column short.
            cols = max(1, min(self._MAX_COLS, int(W / max(1.0, cw) + 1e-6)))
            x_off = max(0.0, (W - cw * cols) / 2.0)
        rows = (n + cols - 1) // cols
        total_h = ch * rows
        total_w = cw * cols

        # A single pixel of rounding must not be allowed to raise a scroll bar: the
        # bar takes width from the viewport, which re-lays out narrower, which can
        # raise it again.
        if total_h <= H + 2:
            self.setMinimumHeight(0)
            ch = H / rows
            total_h = float(H)
        else:
            self.setMinimumHeight(int(round(total_h)))
        # Sideways scrolling appears in ONE case only: a single tile asked to be
        # wider than the pane, which is a deliberate "show me this one closer".
        self.setMinimumWidth(int(round(total_w)) if total_w > W + 2 else 0)

        for i in range(n):
            r, c = divmod(i, cols)
            x0 = int(round(x_off + c * cw)); x1 = int(round(x_off + (c + 1) * cw))
            y0 = int(round(r * ch)); y1 = int(round((r + 1) * ch))
            self._rects.append(QRect(x0, y0, max(20, x1 - x0), max(20, y1 - y0)))

    def _relayout(self):
        n = len(self._cells)
        self._rects = []
        self._row_heads = []
        if n == 0:
            self.setMinimumHeight(150)
            return
        if self._layout_mode == "rows":
            self._relayout_rows()
            return
        if self._layout_mode == "grid":
            self._relayout_grid()
            return
        self.setMinimumHeight(150)
        W, H = max(1, self.width()), max(1, self.height())
        aspects = self._aspects()
        entries = None
        try:
            sl = _get_slider_module()
            entries = sl.compute_camera_layout(
                aspects, W, H, top_px=float(self._CAPTION_H + 2 * self._GAP))
        except Exception:
            entries = None
        if not entries:
            # Fallback: plain near-square grid, still edge-to-edge.
            cols = max(1, int(n ** 0.5 + 0.999))
            rows = max(1, (n + cols - 1) // cols)
            for i in range(n):
                r, c = divmod(i, cols)
                x0 = int(round(c * W / cols)); x1 = int(round((c + 1) * W / cols))
                y0 = int(round(r * H / rows)); y1 = int(round((r + 1) * H / rows))
                self._rects.append(QRect(x0, y0, max(20, x1 - x0), max(20, y1 - y0)))
            return
        try:
            sl = _get_slider_module()
            for e in entries[:n]:
                self._rects.append(sl._entry_rect(e.x, e.y, e.w, e.h, W, H))
        except Exception:
            self._rects = []

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Deliberately does NOT record the new size as the pane's — see _avail. The
        # pane states its own size through set_canvas; a resize here is just as
        # likely to be this widget stretching itself for a zoom.
        self._relayout()
        self._pix.clear()          # tiles are rendered to their drawn size

    # ── rendering ─────────────────────────────────────────────────────────────
    def _tile_pixmap(self, idx: int, target: QRect) -> "QPixmap | None":
        # Resolve the shared Auto pair BEFORE the cache key is built — it is part of
        # the key, and computing it afterwards would invalidate the cache on the very
        # next tile and re-render the whole wall on every repaint.
        auto_pair = self._shared_auto_pair() if self._auto else None
        key = (self._grad_name, self._auto, self._gamma, self._contrast,
               self._offset, auto_pair, self._baseline_idx,
               target.width(), target.height())
        if self._render_key != key:
            self._pix.clear()
            self._render_key = key
        cell = self._cells[idx]
        adj = self._shared.adj.get(cell.get("path")) or {}
        # The per-frame values ride in the cache entry, not the wall-wide key: adjusting
        # one tile must not throw away the other forty-nine pixmaps.
        akey = (adj.get("contrast"), adj.get("offset"), adj.get("gamma"), adj.get("rot", 0))
        hit = self._pix.get(idx)
        if hit is not None and hit[0] == akey:
            return hit[1]
        entry = self._raw.get(cell.get("path"))
        if not entry:
            return None
        arr, full_scale = entry
        if self._baseline_idx is not None and self._baseline_idx != idx:
            base = self._raw.get(self._cells[self._baseline_idx].get("path"))
            if base and base[0].shape == arr.shape and full_scale and base[1]:
                # Subtract on ONE common range, never on the stored values. The archiver
                # stretched each frame by its own bracket, so a dim day and a bright one
                # can both sit near 51 000 in the file: subtracting those gives almost
                # zero and the deviation comes out black, while the real difference is
                # the whole point. Converting both to the same range first makes the
                # difference a difference in true intensity.
                k = img_scale.FULL_SCALE_16
                arr = np.abs(arr.astype(np.float32) * (k / float(full_scale))
                             - base[0].astype(np.float32) * (k / float(base[1])))
                full_scale = k
        if adj:
            # This frame was picked out and adjusted by hand. Its own numbers replace
            # the wall's — including Auto, which is a shared reading it has opted out
            # of — and the caption says so, because it is no longer comparable.
            arr8 = _render_u8(arr, False, full_scale, adj.get("gamma"),
                              int(adj.get("contrast", 0)), int(adj.get("offset", 0)))
        elif self._auto:
            # Auto becomes one shared pair on the absolute path — never a per-tile
            # stretch, which would level every day to its own content.
            a_con, a_off = auto_pair or (0, 0)
            arr8 = _render_u8(arr, False, full_scale, self._gamma, a_con, a_off)
        else:
            arr8 = _render_u8(arr, False, full_scale, self._gamma,
                              self._contrast, self._offset)
        rot = int(adj.get("rot", 0)) % 360
        if rot:
            arr8 = np.ascontiguousarray(np.rot90(arr8, k=(4 - rot // 90) % 4))
        h, w = arr8.shape[:2]
        try:
            lut = _get_slider_module().GRADIENTS.get(self._grad_name)
        except Exception:
            lut = None
        if lut is None:
            img = QImage(np.ascontiguousarray(arr8).data, w, h, w,
                         QImage.Format.Format_Grayscale8).copy()
        else:
            # The LUT is applied to the ABSOLUTE code, never to a per-frame window —
            # a palette spread over each day's own range would destroy the comparison.
            rgb = np.ascontiguousarray(np.asarray(lut, dtype=np.uint8)[arr8])
            img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        avail_h = max(8, target.height() - self._CAPTION_H - 2 * self._GAP)
        avail_w = max(8, target.width() - 2 * self._GAP)
        pm = QPixmap.fromImage(img).scaled(
            avail_w, avail_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._pix[idx] = (akey, pm)
        return pm

    # How the frame was chosen, in words the caption has room for. A frame nobody
    # vouched for has to look different from one a shot picked, or a black tile reads
    # as a broken camera instead of as "we just took whatever was in the folder".
    _SOURCE_TAG = {"sbw4": "SBW4", "ptm1": "PTM1", "totalpower": "power",
                   "csv": "CSV", "pv": "PV", "pv_cond": "PV condition",
                   "blind": "no shot data"}

    def _caption(self, cell: dict) -> str:
        if self._multi_cam:
            # Many cameras at one moment: the camera is what tells the tiles apart.
            txt = str(cell.get("cam") or "")
        else:
            day = cell.get("day")
            txt = day.strftime("%d.%m.") if hasattr(day, "strftime") else str(day or "")
        # Several moments picked: which pick this tile answers, the same number the
        # graph drew beside it. Without it two tiles of one camera minutes apart
        # cannot be told from one another.
        pick = cell.get("pick")
        if pick:
            txt = f"{pick})  {txt}" if txt else f"{pick})"
        ts = cell.get("ts_ns")
        if ts:
            try:
                txt += "  " + datetime.fromtimestamp(
                    ts / 1e9, tz=timezone.utc).astimezone(PRAGUE).strftime("%H:%M:%S")
            except Exception:
                pass
        elif cell.get("status") == "no_frame":
            # Said on the tile, not only in the count under the wall: a camera that
            # had nothing near the moment must not read as one that is still loading.
            txt += "  no frame"
        # Only the WARNING is captioned. Naming the channel the frame was picked by
        # ("[SBW4]") repeated the search on every tile — the operator just chose it,
        # and it is the same for the whole wall. A frame nobody vouched for still has
        # to say so, or a black tile reads as a broken camera.
        _m = cell.get("meta") or {}
        if _m.get("source") == "blind":
            txt += f"  [{self._SOURCE_TAG['blind']}]"
        # An empty frame is not the same as a missing one, and a condition search
        # deliberately keeps it: the moment was right, this camera just saw nothing.
        if _m.get("blank"):
            txt += "  (nothing on it)"
        if self._shared.adj.get(cell.get("path")):
            # Plain words, matching "(reference)" — this has to be legible in the caption
            # strip at any tile size, which a decorative glyph is not.
            txt += "  (adjusted)"
        return txt

    def _paint_into(self, painter: QPainter, rects: list, scale: float = 1.0):
        f = painter.font()
        f.setPointSizeF(max(6.0, 8.5 * scale))
        painter.setFont(f)
        live = (scale == 1.0 and rects is self._rects)
        if live:
            self._img_rects = [QRect() for _ in self._cells]
        if self._layout_mode == "rows":
            hf = painter.font()
            hf.setBold(True)
            for hr, txt in self._row_heads:
                r = (hr if scale == 1.0 else
                     QRect(int(hr.x() * scale), int(hr.y() * scale),
                           int(hr.width() * scale), int(hr.height() * scale)))
                painter.fillRect(r, QColor("#333"))
                painter.setFont(hf)
                painter.setPen(QPen(QColor("#eee")))
                painter.drawText(r.adjusted(int(10 * scale), 0, 0, 0),
                                 Qt.AlignmentFlag.AlignVCenter |
                                 Qt.AlignmentFlag.AlignLeft, txt)
                painter.setFont(f)
        for i, cell in enumerate(self._cells):
            if i >= len(rects):
                break
            r = rects[i]
            if r.width() <= 0 or r.height() <= 0:
                continue
            is_base = (self._baseline_idx == i)
            path = cell.get("path")
            pm = self._tile_pixmap(i, r)
            if pm is not None and not pm.isNull():
                x = r.x() + (r.width() - pm.width()) // 2
                y = r.y() + self._GAP + (
                    r.height() - self._CAPTION_H - 2 * self._GAP - pm.height()) // 2
                painter.drawPixmap(x, y, pm)
                ir = QRect(x, y, pm.width(), pm.height())
                if live:
                    self._img_rects[i] = ir
                self._paint_overlay(painter, path, ir, scale)
            else:
                # Three different states, three different looks. "Nothing near the
                # moment" is an ANSWER and wears the warning colour; a frame still
                # being read is a dash; a cell with no picture for any other reason
                # says so plainly. All three used to be one grey "no image".
                miss = cell.get("status") == "no_frame"
                painter.setPen(QPen(QColor("#ff8a80" if miss else "#666")))
                if miss:
                    note = (cell.get("meta") or {}).get("note") \
                        or "nothing near this moment"
                else:
                    note = "no image" if path is None else "…"
                painter.drawText(r.adjusted(int(4 * scale), 0, int(-4 * scale),
                                            -self._CAPTION_H),
                                 Qt.AlignmentFlag.AlignCenter |
                                 Qt.TextFlag.TextWordWrap, note)
            cap_rect = QRect(r.x(), r.y() + r.height() - self._CAPTION_H,
                             r.width(), self._CAPTION_H)
            if cell.get("status") == "no_frame":
                # Dark red band with white ink — never a dark tint left to inherit
                # black text, which is the rogue-row look this program avoids.
                painter.fillRect(cap_rect, QColor("#6d2020"))
                painter.setPen(QPen(QColor("#ffffff")))
            else:
                painter.fillRect(cap_rect,
                                 QColor("#2b2b2b" if not is_base else "#4a3b00"))
                painter.setPen(QPen(QColor("#ffd54f" if is_base else "#ddd")))
            painter.drawText(cap_rect, Qt.AlignmentFlag.AlignCenter,
                             self._caption(cell) + ("  (reference)" if is_base else ""))
            if path is not None and path in self._shared.sel:
                painter.setPen(QPen(QColor("#4a9eff"), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(r.adjusted(1, 1, -2, -2))
            elif i == self._hover:
                painter.setPen(QPen(QColor("#6cf"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(r.adjusted(1, 1, -2, -2))

    # ── overlay painting ──────────────────────────────────────────────────────
    def _paint_overlay(self, painter: QPainter, path, ir: QRect, scale: float = 1.0):
        """Shapes are held in fractions of the picture (0-1), so they stay put when the
        tile is re-laid out, saved at double size, or moved to another tab."""
        ov = self._shared.ov.get(path)
        if not ov or ir.width() <= 0 or ir.height() <= 0:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cross = ov.get("cross")
        if cross:
            cx = ir.left() + int(cross["x"] * ir.width())
            cy = ir.top() + int(cross["y"] * ir.height())
            s = max(6, int(18 * scale))
            painter.setPen(QPen(self._shape_colors["cross"], max(1, int(2 * scale))))
            painter.drawLine(cx - s, cy, cx + s, cy)
            painter.drawLine(cx, cy - s, cx, cy + s)
        circ = ov.get("circle")
        if circ:
            cx = ir.left() + int(circ["x"] * ir.width())
            cy = ir.top() + int(circ["y"] * ir.height())
            rx = int(circ["rx"] * ir.width())
            ry = int(circ["ry"] * ir.height())
            painter.setPen(QPen(self._shape_colors["circle"], max(1, int(2 * scale))))
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if self._draw_mode == "circle" and scale == 1.0:
                self._paint_handles(painter, self._circle_handles(circ, ir),
                                    self._shape_colors["circle"])
        sq = ov.get("square")
        if sq:
            sx = ir.left() + int(sq["l"] * ir.width())
            sy = ir.top() + int(sq["t"] * ir.height())
            sw = int((sq["r"] - sq["l"]) * ir.width())
            sh = int((sq["b"] - sq["t"]) * ir.height())
            painter.setPen(QPen(self._shape_colors["square"], max(1, int(2 * scale))))
            painter.drawRect(sx, sy, sw, sh)
            if self._draw_mode == "square" and scale == 1.0:
                self._paint_handles(painter, self._square_handles(sq, ir),
                                    self._shape_colors["square"])

    def _paint_handles(self, painter: QPainter, handles: dict, color: QColor):
        r = self._HANDLE_R
        painter.setPen(QPen(color))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 120))
        for pt in handles.values():
            painter.drawEllipse(int(pt[0]) - r, int(pt[1]) - r, r * 2, r * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _circle_handles(circ: dict, ir: QRect) -> dict:
        cx = ir.left() + circ["x"] * ir.width()
        cy = ir.top() + circ["y"] * ir.height()
        rx = circ["rx"] * ir.width()
        ry = circ["ry"] * ir.height()
        return {"move": (cx, cy), "n": (cx, cy - ry), "s": (cx, cy + ry),
                "e": (cx + rx, cy), "w": (cx - rx, cy)}

    @staticmethod
    def _square_handles(sq: dict, ir: QRect) -> dict:
        sx = ir.left() + sq["l"] * ir.width()
        sy = ir.top() + sq["t"] * ir.height()
        ex = ir.left() + sq["r"] * ir.width()
        ey = ir.top() + sq["b"] * ir.height()
        mx, my = (sx + ex) / 2, (sy + ey) / 2
        return {"move": (mx, my), "nw": (sx, sy), "ne": (ex, sy), "sw": (sx, ey),
                "se": (ex, ey), "n": (mx, sy), "s": (mx, ey),
                "w": (sx, my), "e": (ex, my)}

    def _hit_handle(self, pos, handles: dict) -> str:
        r = self._HANDLE_R + 3
        for name, pt in handles.items():
            if abs(pos.x() - pt[0]) <= r and abs(pos.y() - pt[1]) <= r:
                return name
        return ""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#1a1a1a"))
        if not self._cells:
            painter.setPen(QPen(QColor("#777")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No days yet — run a search.")
            return
        if len(self._rects) != len(self._cells):
            self._relayout()
        self._paint_into(painter, self._rects)

    def composite_image(self, scale: float = 2.0) -> "QImage | None":
        """The whole wall as ONE image, captions included — the thing neither existing
        tab can produce, since both save frames singly."""
        if not self._cells or not self._rects:
            return None
        W = int(self.width() * scale); H = int(self.height() * scale)
        if W <= 0 or H <= 0:
            return None
        big = [QRect(int(r.x() * scale), int(r.y() * scale),
                     int(r.width() * scale), int(r.height() * scale))
               for r in self._rects]
        img = QImage(W, H, QImage.Format.Format_RGB888)
        img.fill(QColor("#1a1a1a"))
        painter = QPainter(img)
        saved_pix, saved_key = self._pix, self._render_key
        self._pix, self._render_key = {}, None      # render at the larger tile size
        try:
            self._paint_into(painter, big, scale=scale)
        finally:
            painter.end()
            self._pix, self._render_key = saved_pix, saved_key
        return img

    # ── interaction ───────────────────────────────────────────────────────────
    def _hit(self, pos) -> int:
        for i, r in enumerate(self._rects):
            if r.contains(pos):
                return i
        return -1

    def _img_rect_at(self, idx: int) -> "QRect | None":
        if 0 <= idx < len(self._img_rects):
            r = self._img_rects[idx]
            if r.width() > 0 and r.height() > 0:
                return r
        return None

    def _tile_tip(self, idx: int) -> str:
        """Everything the caption strip has no room for.

        The caption has to stay legible at the smallest tile size, so it carries
        only the camera (or the day) and the frame's own time. HOW FAR the frame is
        from the moment that was asked for belongs here — a stored frame can be a
        good few seconds off, and that is a reading, not a detail."""
        if not (0 <= idx < len(self._cells)):
            return ""
        cell = self._cells[idx]
        lines = []
        cam = cell.get("cam_folder") or cell.get("cam")
        if cam:
            lines.append(str(cam))
        day = cell.get("day")
        if hasattr(day, "strftime"):
            lines.append(day.strftime("%A  %d.%m.%Y"))
        ts, asked = cell.get("ts_ns"), (cell.get("meta") or {}).get("asked_ns")
        pick = cell.get("pick")
        if pick and asked:
            try:
                a = datetime.fromtimestamp(int(asked) / 1e9, tz=timezone.utc)
                if PRAGUE is not None:
                    a = a.astimezone(PRAGUE)
                lines.append(f"moment {pick} picked:  " + a.strftime("%H:%M:%S"))
            except Exception:
                pass
        if ts:
            try:
                own = datetime.fromtimestamp(ts / 1e9, tz=timezone.utc)
                if PRAGUE is not None:
                    own = own.astimezone(PRAGUE)
                txt = own.strftime("%H:%M:%S.%f")[:-3]
                if asked:
                    off = (int(ts) - int(asked)) / 1e9
                    txt += f"   ({off:+.1f} s from the moment picked)"
                lines.append(txt)
            except Exception:
                pass
        elif cell.get("status") == "no_frame":
            lines.append((cell.get("meta") or {}).get("note")
                         or "nothing near this moment")
        p = cell.get("path")
        if p is not None:
            lines.append(str(p))
        return "\n".join(lines)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag_idx >= 0 and (event.buttons() & Qt.MouseButton.LeftButton):
            self._did_drag = True
            self._drag_overlay(pos, bool(event.modifiers()
                                         & Qt.KeyboardModifier.ShiftModifier))
            return
        i = self._hit(pos.toPoint())
        if i != self._hover:
            self._hover = i
            self.setToolTip(self._tile_tip(i) if i >= 0 else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover != -1:
            self._hover = -1
            self.setToolTip("")
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        pos = event.position()
        i = self._hit(pos.toPoint())
        if i < 0:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.RightButton:
            # Everything that acts on one frame lives here: the close-up, another try at
            # finding that day's picture, and the reference day (which right click used
            # to set outright, with nothing to say that it had).
            self.tile_context.emit(i, event.globalPosition().toPoint())
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._draw_mode:
            if self._begin_overlay(i, pos):
                return
        if event.button() == Qt.MouseButton.LeftButton:
            # Selecting and opening are the same gesture on purpose: you click the frame
            # you want to look at, and that is the frame the brightness controls should
            # be aiming at when you reach for them.
            path = self._cells[i].get("path")
            if path is not None:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.set_selected(path, path not in self._shared.sel)
                    return
                self._shared.sel.clear()
                self._shared.sel.add(path)
                self.selection_changed.emit()
                self.update()
            self.tile_clicked.emit(i)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_idx = -1
        self._drag_handle = ""
        self._drag_start = None
        self._did_drag = False
        super().mouseReleaseEvent(event)

    # ── overlay editing ───────────────────────────────────────────────────────
    def _begin_overlay(self, idx: int, pos) -> bool:
        ir = self._img_rect_at(idx)
        if ir is None:
            return False
        path = self._cells[idx].get("path")
        if path is None:
            return False
        # One snapshot per gesture, taken before anything changes — so Undo takes a
        # mark off every frame it was mirrored onto in a single step.
        self._shared.push_undo()
        ov = self._ov_for(path)
        self._drag_idx, self._drag_start, self._did_drag = idx, pos, False
        if self._draw_mode == "cross":
            self._drag_handle = "cross"
            self._set_cross(ov, pos, ir)
            self._mirror_marks(path)
            self.update()
            return True
        shape = ov.get(self._draw_mode)
        if shape:
            handles = (self._circle_handles(shape, ir) if self._draw_mode == "circle"
                       else self._square_handles(shape, ir))
            hit = self._hit_handle(pos, handles)
            if hit:
                self._drag_handle = hit
                return True
        self._drag_handle = "new"
        return True

    @staticmethod
    def _clamp(v):
        return max(0.0, min(1.0, v))

    def _set_cross(self, ov: dict, pos, ir: QRect):
        ov["cross"] = {"x": self._clamp((pos.x() - ir.left()) / ir.width()),
                       "y": self._clamp((pos.y() - ir.top()) / ir.height())}

    def _drag_overlay(self, pos, shift: bool):
        idx = self._drag_idx
        ir = self._img_rect_at(idx)
        if ir is None:
            return
        path = self._cells[idx].get("path")
        ov = self._ov_for(path)
        c = self._clamp
        if self._draw_mode == "cross":
            self._set_cross(ov, pos, ir)
            self._mirror_marks(path)
            self.update()
            return
        if self._draw_mode == "circle":
            circ = ov.get("circle")
            if self._drag_handle == "new":
                x0, y0 = self._drag_start.x(), self._drag_start.y()
                dx = abs(pos.x() - x0) / ir.width()
                dy = abs(pos.y() - y0) / ir.height()
                if shift:
                    dx = dy = max(dx, dy)
                ov["circle"] = {"x": c((x0 - ir.left()) / ir.width()),
                                "y": c((y0 - ir.top()) / ir.height()),
                                "rx": dx, "ry": dy}
            elif circ and self._drag_handle == "move":
                circ["x"] = c(circ["x"] + (pos.x() - self._drag_start.x()) / ir.width())
                circ["y"] = c(circ["y"] + (pos.y() - self._drag_start.y()) / ir.height())
                self._drag_start = pos
            elif circ and self._drag_handle in ("e", "w"):
                cx = ir.left() + circ["x"] * ir.width()
                circ["rx"] = abs(pos.x() - cx) / ir.width()
                if shift:
                    circ["ry"] = circ["rx"]
            elif circ and self._drag_handle in ("n", "s"):
                cy = ir.top() + circ["y"] * ir.height()
                circ["ry"] = abs(pos.y() - cy) / ir.height()
                if shift:
                    circ["rx"] = circ["ry"]
        elif self._draw_mode == "square":
            sq = ov.get("square")
            nx = c((pos.x() - ir.left()) / ir.width())
            ny = c((pos.y() - ir.top()) / ir.height())
            if self._drag_handle == "new":
                x0 = c((self._drag_start.x() - ir.left()) / ir.width())
                y0 = c((self._drag_start.y() - ir.top()) / ir.height())
                dx, dy = abs(nx - x0), abs(ny - y0)
                if shift:
                    dx = dy = max(dx, dy)
                ov["square"] = {"l": c(x0 - dx), "t": c(y0 - dy),
                                "r": c(x0 + dx), "b": c(y0 + dy)}
            elif sq and self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                w_, h_ = sq["r"] - sq["l"], sq["b"] - sq["t"]
                sq["l"] = c(sq["l"] + dx); sq["t"] = c(sq["t"] + dy)
                sq["r"] = c(sq["l"] + w_); sq["b"] = c(sq["t"] + h_)
            elif sq:
                h = self._drag_handle
                if "w" in h: sq["l"] = min(nx, sq["r"] - 0.01)
                if "e" in h: sq["r"] = max(nx, sq["l"] + 0.01)
                if "n" in h: sq["t"] = min(ny, sq["b"] - 0.01)
                if "s" in h: sq["b"] = max(ny, sq["t"] + 0.01)
                if shift and h in ("nw", "ne", "sw", "se"):
                    side = max(sq["r"] - sq["l"], sq["b"] - sq["t"])
                    if h == "se": sq["r"], sq["b"] = c(sq["l"] + side), c(sq["t"] + side)
                    elif h == "nw": sq["l"], sq["t"] = c(sq["r"] - side), c(sq["b"] - side)
                    elif h == "ne": sq["r"], sq["t"] = c(sq["l"] + side), c(sq["b"] - side)
                    else: sq["l"], sq["b"] = c(sq["r"] - side), c(sq["t"] + side)
        self._mirror_marks(path)
        self.update()


# ── SAVING THE FRAMES ─────────────────────────────────────────────────────────
def _pil_font(size: int):
    """A real TrueType face at `size` px, PIL's built-in bitmap font as the last
    resort.

    PIL's default font is fixed at about 11 px, so a caption left to it under a
    1400 px wide frame comes out unreadable — every caption drawn on a saved sheet
    or PDF page asks for a size."""
    from PIL import ImageFont as _IF
    for name in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                 "C:/Windows/Fonts/calibri.ttf", "DejaVuSans.ttf"):
        try:
            return _IF.truetype(name, int(size))
        except Exception:
            continue
    return _IF.load_default()


def _pv_bar_text(match_row, selected_cols: "list[str]") -> str:
    """The line that goes in the white bar under a saved frame.

    Lifted out of `_annotate_image_with_energy` unchanged, so the bar reads the same
    whichever save path drew it: a frame saved on its own and the same frame on a
    sheet must not disagree about what a PV was. Values only — no timestamps and no
    "nearest row" note, which is the choice that function already made."""
    if match_row is not None:
        parts = [f"{_pv_label_for(col)}: "
                 f"{_format_energy_value(col, match_row.values.get(col, '—'))}"
                 for col in selected_cols]
        return "   |   ".join(parts) if parts else "(no columns selected)"
    parts = [f"{_pv_label_for(col)}: n/a" for col in selected_cols]
    return "   |   ".join(parts) if parts else "n/a"


# Radio buttons on the app's LIGHT ground — main.py paints every QWidget #f3f3f3
# with #111 ink, and a dialog is a QWidget. Fusion leaves the indicator itself to
# the OS theme, and this machine is in Windows dark mode, so an unstyled radio
# comes out a dark blob whose dot cannot be seen. Set both, as everything else in
# this tab does.
_RADIO_STYLE = """
QRadioButton { spacing: 6px; padding: 3px 4px; font-weight: 600; color: #111; }
QRadioButton::indicator { width: 14px; height: 14px; border: 2px solid #4a4a4a;
    border-radius: 9px; background: #ffffff; }
QRadioButton::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
/* The radius must match the OUTER box, and a border grows outwards: 14 px of
   content with a 5 px ring is a 24 px box, so 9 px leaves a rounded SQUARE where a
   dot belongs — and next to the square check boxes below it reads as one. */
QRadioButton::indicator:checked { border: 5px solid #2d7dff; background: #ffffff;
    border-radius: 12px; }
QRadioButton:disabled { color: #8a8a8a; }
QRadioButton::indicator:disabled { border: 2px solid #c0c0c0; background: #e9e9e9; }
"""

# _CHECKBOX_STYLE has no disabled rule, so a switched-off box keeps its bright blue
# tick and its black label and reads as live. "A folder for each camera" IS switched
# off for the single-file modes, where it means nothing, and it has to look it.
_CHECKBOX_STYLE_OFFABLE = _CHECKBOX_STYLE + """
QCheckBox:disabled { color: #8a8a8a; }
QCheckBox::indicator:disabled { border: 2px solid #c0c0c0; background: #e9e9e9; }
QCheckBox::indicator:checked:disabled { border: 2px solid #a8bfe0; background: #a8bfe0; }
"""


class _SaveFramesDialog(QDialog):
    """How to save the frames on the wall — asked once, before the file dialog.

    Save As used to write files with no questions asked, which is part of why nobody
    noticed it was writing the WRONG files: one frame per camera out of the hour in
    the Time window, not the frames on screen. It now leads with how many frames it
    is about to write, and takes the three answers the operator asked for — separate
    files or one sheet, a folder per camera, and the PV bar burned in or not.
    """

    MODES = (
        ("each",  "A file for each frame"),
        ("pdf",   "One PDF, a page for each frame"),
        ("cam",   "One picture for each camera, its frames side by side"),
        ("sheet", "One picture, every frame side by side"),
    )

    def __init__(self, n_frames: int, n_cams: int, pv_bar: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save As")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)

        head = QLabel(f"{n_frames} frame{'s' if n_frames != 1 else ''} from "
                      f"{n_cams} camera{'s' if n_cams != 1 else ''} — everything "
                      f"on the wall, every tab.")
        head.setWordWrap(True)
        head.setStyleSheet("color:#111;font-weight:700;")
        lay.addWidget(head)

        lay.addWidget(_section_label("How"))
        self._modes: dict = {}
        for key, text in self.MODES:
            b = QRadioButton(text)
            b.setStyleSheet(_RADIO_STYLE)
            lay.addWidget(b)
            self._modes[key] = b
        self._modes["each"].setChecked(True)

        lay.addWidget(_section_label("Also"))
        self._cb_subfolders = QCheckBox("A folder for each camera")
        self._cb_subfolders.setStyleSheet(_CHECKBOX_STYLE_OFFABLE)
        self._cb_subfolders.setChecked(n_cams > 1)
        lay.addWidget(self._cb_subfolders)

        self._cb_pv = QCheckBox("PV values burned into the picture")
        self._cb_pv.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_pv.setChecked(bool(pv_bar))
        lay.addWidget(self._cb_pv)

        # A folder for each camera means nothing when the answer is a single file.
        def _sync():
            self._cb_subfolders.setEnabled(self._modes["each"].isChecked())
        for b in self._modes.values():
            b.toggled.connect(lambda *_: _sync())
        _sync()

        note = QLabel("These are the archive frames themselves, at full resolution, "
                      "with the palette and the contrast, brightness and gamma this "
                      "tab is showing. For a picture of the wall as it is drawn on "
                      "screen, use Save view.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;font-size:10px;")
        lay.addWidget(note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result_options(self) -> dict:
        mode = next((k for k, b in self._modes.items() if b.isChecked()), "each")
        return {"mode": mode,
                "subfolders": mode == "each" and self._cb_subfolders.isChecked(),
                "pv_bar": self._cb_pv.isChecked()}


# ── wheel guard ───────────────────────────────────────────────────────────────
def install_wheel_guard(app):
    """A value must never change just because the pointer crossed its control.

    Number fields, drop-downs and setting sliders answer the mouse wheel only
    once they have been CLICKED (i.e. they hold the keyboard focus). Until then
    the notch goes to the panel behind them instead, so a settings panel still
    scrolls when the pointer happens to pass over a field on the way down. A
    control that is meant to take the wheel at any time carries the "wheelAlways"
    property.

    Scroll bars are left out: they are sliders too, and the wheel is how a pane
    gets scrolled.

    One app-wide filter, so a dialog built much later is covered as well. It is
    spelled out in every entry point rather than imported once: each tab also
    runs on its own, and a sibling module would have to survive the frozen build
    (the same reason `_import_img_scale` is copied into each tab).
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import (QAbstractScrollArea, QAbstractSlider,
                                   QAbstractSpinBox, QScrollBar)

    class _WheelGuard(QObject):
        _GUARDED = (QAbstractSpinBox, QComboBox, QAbstractSlider)
        # Focus the user asked for. The focus a freshly opened window HANDS to its
        # first field (ActiveWindow / Other) does not count, or the top field of a
        # panel would answer the wheel before it had ever been touched.
        _EARNED = (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                   Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason)
        _GIVEN = (Qt.FocusReason.ActiveWindowFocusReason,
                  Qt.FocusReason.OtherFocusReason)

        def eventFilter(self, obj, ev):
            t = ev.type()
            if t == QEvent.Type.FocusIn and isinstance(obj, self._GUARDED):
                # Popup and menu reasons are left as they are: closing a drop-down
                # hands the focus back, which must not undo the click that opened it.
                if ev.reason() in self._EARNED:
                    obj.setProperty("wheelReady", True)
                elif ev.reason() in self._GIVEN:
                    obj.setProperty("wheelReady", False)
                return False
            if t != QEvent.Type.Wheel:
                return False
            if not isinstance(obj, self._GUARDED) or isinstance(obj, QScrollBar):
                return False
            if (obj.property("wheelAlways")
                    or (obj.hasFocus() and obj.property("wheelReady"))):
                return False
            pane = obj.parentWidget()
            while pane is not None and not isinstance(pane, QAbstractScrollArea):
                pane = pane.parentWidget()
            if pane is not None:
                QApplication.sendEvent(pane.viewport(), ev)
            return True

    app.installEventFilter(_WheelGuard(app))


def main():
    """Run Image Finder as a standalone window (without Image Slider)."""
    app = QApplication.instance() or QApplication(sys.argv)
    install_wheel_guard(app)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget     { background: #f3f3f3; color: #111; }
        QLabel      { background: transparent; }
        QPushButton { padding: 5px 8px; }
        QComboBox   { padding: 3px 6px; }
    """)
    win = QMainWindow()
    win.setWindowTitle("Image Finder")
    win.resize(800, 600)
    widget = ImageFinderWidget()
    win.setCentralWidget(widget)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()