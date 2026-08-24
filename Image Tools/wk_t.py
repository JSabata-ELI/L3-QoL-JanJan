# wk_t.py — Workshop tab for Image Tools
#
# Receives images from Image Finder / Image Slider / Shot Finder and lets the operator
# look at them the way ImageJ is used in the lab: magnify a chosen spot, measure a
# region, read a profile, annotate, compare two frames, save a copy.
#
# THE ONE RULE OF THIS TAB: display settings never change pixels.
#   Brightness, contrast, gamma, the display window and the palette live in
#   _ViewSettings and are re-applied on every render. Only the explicit edit
#   operations (crop, rotate, flip, resize, reference subtraction) touch `base`, and
#   they are undoable. Saving writes a NEW file; the source file is never rewritten.
#
# Three layers per image slot (see _WorkshopSlot):
#   raw    native-precision counts read back from the source file, for measurement only
#   base   the pixel data being displayed and edited (8-bit, as the sender rendered it)
#   annots vector annotations in image coordinates, painted on top at draw time
#
# Three blocks, in this order: PIXEL OPERATIONS, BEAM MEASUREMENTS, then the tab itself.
# The first two use no Qt at all — an operation that can be checked against a synthetic
# Gaussian has no business being tangled up with widgets — and the panel calls them
# through the names `wk_ops` and `wk_beam`, which are bound to this module further down.

import os
import re
import sys
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
from PIL import Image as _PilImg

from PySide6.QtCore import (
    Qt, QPointF, QRectF, QSize, Signal, QRunnable, QThreadPool, QObject, QTimer,
)
from PySide6.QtGui import (
    QImage, QColor, QPainter, QPen, QBrush, QFont, QPolygonF, QCursor, QGuiApplication,
    QFontMetrics, QIcon, QPixmap, QPainterPath, QShortcut, QKeySequence,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QFileDialog,
    QMessageBox, QSizePolicy, QScrollArea, QFrame, QToolButton, QButtonGroup,
    QColorDialog, QSplitter, QDialog, QInputDialog, QAbstractButton, QMenu,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

try:
    from zoneinfo import ZoneInfo
    _TZ_PRAGUE = ZoneInfo("Europe/Prague")
except Exception:                                    # pragma: no cover - tzdata missing
    from datetime import timezone, timedelta
    _TZ_PRAGUE = timezone(timedelta(hours=1))


# ─────────────────────────────────────────────────────────────────
#  Sibling modules — borrow, do not copy
# ─────────────────────────────────────────────────────────────────

def _import_sibling(name: str):
    """Load a sibling .py by file path: one instance per process, registered before
    exec, the same way if_t/sf_t/is_t load img_scale.

    By path and not by `import name`, because main.py strips site-packages from
    sys.path in the frozen build and these files sit next to the exe, not on the
    import path."""
    import importlib.util as _ilu
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / f"{name}.py"
    spec = _ilu.spec_from_file_location(name, p)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod            # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


def _import_img_scale():
    return _import_sibling("img_scale")


try:
    img_scale = _import_img_scale()
except Exception:                                    # pragma: no cover - standalone run
    img_scale = None

#  The panel calls the two groups above through these two names. They point at this
#  module because the whole tab has to live in one file; keeping the call sites reading
#  `wk_ops.median(...)` and `wk_beam.beam_stats(...)` says which group a function
#  belongs to at every call, and leaves the door open to splitting them out later
#  without touching a single caller. The empty error strings are what the panel checks
#  before it greys a button out.
wk_ops = sys.modules[__name__]
wk_beam = sys.modules[__name__]
_OPS_ERROR = ""
_BEAM_ERROR = ""


_SLIDER_MOD = None


def _get_slider_module():
    """Borrow helpers (palette LUTs, CollapsibleSection, circle fit) from the Image
    Slider WITHOUT re-executing 23k lines of is_t.py — prefer the instance main.py
    already loaded, else load once and cache. Returns None if it cannot be loaded, and
    every caller has a local fallback, so the Workshop still opens on its own."""
    global _SLIDER_MOD
    mod = sys.modules.get("image_slider")
    if mod is not None:
        return mod
    if _SLIDER_MOD is None:
        try:
            import importlib.util as _ilu
            p = Path(__file__).resolve().parent / "is_t.py"
            spec = _ilu.spec_from_file_location("is_t_helpers", p)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _SLIDER_MOD = mod
        except Exception:
            _SLIDER_MOD = False
    return _SLIDER_MOD or None


# ─────────────────────────────────────────────────────────────────
#  Palettes
# ─────────────────────────────────────────────────────────────────
#  Taken from the Image Slider so the same palette name means the same colours in
#  every tab. The fallback below is deliberately minimal — it exists only so the file
#  still opens when is_t.py is unavailable, not as a second definition to maintain.

def _fallback_lut(stops):
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


def _load_palettes():
    mod = _get_slider_module()
    if mod is not None and getattr(mod, "GRADIENTS", None):
        return (dict(mod.GRADIENTS),
                frozenset(getattr(mod, "ADAPTIVE_PALETTES", frozenset())),
                frozenset(getattr(mod, "CYCLIC_PALETTES", frozenset())))
    return ({
        "Default":   None,
        "Grayscale": None,
        "Hot":       _fallback_lut([(0, (0, 0, 0)), (0.27, (255, 0, 0)),
                                    (0.53, (255, 255, 0)), (0.78, (255, 255, 190)),
                                    (1, (255, 255, 255))]),
        "Viridis":   _fallback_lut([(0, (68, 1, 84)), (0.25, (59, 82, 139)),
                                    (0.5, (33, 145, 140)), (0.75, (94, 201, 98)),
                                    (1, (253, 231, 37))]),
        "Jet":       _fallback_lut([(0, (0, 0, 128)), (0.125, (0, 0, 255)),
                                    (0.375, (0, 255, 255)), (0.625, (255, 255, 0)),
                                    (0.875, (255, 0, 0)), (1, (128, 0, 0))]),
    }, frozenset(), frozenset())


GRADIENTS, ADAPTIVE_PALETTES, CYCLIC_PALETTES = _load_palettes()
PALETTE_NAMES = list(GRADIENTS.keys())
#  "Default" keeps a colour source in colour; "Grayscale" forces greyscale. Both have a
#  None LUT, which is why the render path branches on the NAME, never on the index —
#  the Image Slider persists palette choices by index and its list must not be reordered.
PALETTE_DEFAULT = "Default" if "Default" in GRADIENTS else PALETTE_NAMES[0]


def _lut_pixels(lut, arr8: np.ndarray, name: str) -> np.ndarray:
    """RGB pixels for an 8-bit array under `lut`.

    Adaptive palettes are spread over the frame's own p0.5..p99.5 window; everything
    else, cyclic palettes included, uses the absolute scale (see is_t.ADAPTIVE_PALETTES)."""
    if name not in ADAPTIVE_PALETTES:
        return lut[arr8]
    s = _stat_sample(arr8)
    lo = float(np.percentile(s, 0.5))
    hi = float(np.percentile(s, 99.5))
    if hi <= lo:
        lo, hi = float(arr8.min()), float(arr8.max())
    if hi <= lo:
        return lut[arr8]
    scaled = np.clip((arr8.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                     0, 255).astype(np.uint8)
    return lut[scaled]


# ─────────────────────────────────────────────────────────────────
#  Array / QImage helpers
# ─────────────────────────────────────────────────────────────────

_STAT_MAX_SAMPLES = 250_000


def _stat_sample(a: np.ndarray) -> np.ndarray:
    """Deterministic subsample for percentiles. The stride is forced odd so it cannot
    lock onto one set of columns — these sensors have vertical banding."""
    n = a.size
    if n <= _STAT_MAX_SAMPLES:
        return a
    return np.ravel(a)[::(n // _STAT_MAX_SAMPLES) | 1]


def _np_to_qimage(arr: np.ndarray) -> QImage:
    """HxW uint8 (grey) or HxWx3 uint8 (RGB) → QImage."""
    arr = np.ascontiguousarray(arr)
    if arr.ndim == 2:
        h, w = arr.shape
        return QImage(arr.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def _qimage_to_np(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape((h, img.bytesPerLine() // 3, 3))[:, :w].copy()


def _arr_to_pil(arr: np.ndarray) -> "_PilImg.Image":
    return _PilImg.fromarray(np.ascontiguousarray(arr))


def _pil_bilinear():
    """Pillow moved the resampling constants in 9.1; support both spellings."""
    res = getattr(_PilImg, "Resampling", None)
    return res.BILINEAR if res is not None else _PilImg.BILINEAR


def _write_image(img: QImage, path: "Path") -> bool:
    """Write a rendered QImage to disk through Pillow — the same writer the rest of the
    tabs use, so PNG and TIFF behave the same here as they do there."""
    try:
        _arr_to_pil(_qimage_to_np(img)).save(str(path))
        return True
    except Exception:
        return False


def _write_csv(path, header, rows) -> bool:
    """One CSV writer for every table in this tab.

    newline="" and the csv module rather than hand-built lines: a label can contain a
    comma (camera names do), and a value written by hand would then split into two
    columns in Excel without anything looking wrong."""
    try:
        with open(str(path), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if header:
                w.writerow(list(header))
            for row in rows:
                w.writerow(list(row))
        return True
    except Exception:
        return False


def _to_gray(arr: np.ndarray) -> np.ndarray:
    """Luma of an RGB array, or the array itself when it is already single-channel."""
    if arr.ndim == 2:
        return arr
    return (0.299 * arr[:, :, 0].astype(np.float32) +
            0.587 * arr[:, :, 1].astype(np.float32) +
            0.114 * arr[:, :, 2].astype(np.float32))


# ─────────────────────────────────────────────────────────────────
#  File name helpers
# ─────────────────────────────────────────────────────────────────

_NS_RE = re.compile(r"(\d{19})")
_CAM_IMG_MARK_RE = re.compile(r"[-_]+IMG(?=$|[-_])", re.IGNORECASE)
_CAM_CONTAINER_RE = re.compile(r"^C\d{2}[-_]", re.IGNORECASE)


def _clean_cam_for_filename(cam: str) -> str:
    """Camera token as it should appear in a saved file name:
    'C03-040-PFM13NF-_-IMG' -> '040-PFM13NF'.

    The '-IMG' marker and the leading container code carry no information for the
    person looking at the file. Cameras without a 'Cxx-' prefix keep whatever they have."""
    s = _CAM_IMG_MARK_RE.sub("", cam).strip("-_")
    return _CAM_CONTAINER_RE.sub("", s, count=1).strip("-_")


def _build_save_stem(slot) -> str:
    """Save filename stem: {cam_label}_{YYYY-MM-DD_HH-MM-SS-mmm}, Prague local time."""
    cam_label = ""
    ts_dt = None

    if slot.source_path is not None:
        p = slot.source_path
        cam_label = _clean_cam_for_filename(p.parent.name)
        m = _NS_RE.search(p.stem)
        if m:
            ts_dt = datetime.fromtimestamp(int(m.group(1)) / 1e9, tz=_TZ_PRAGUE)

    if not ts_dt:
        label = slot.label
        # "CAM  |  filename_ns.ext" (if_t / sf_t) or "CAM  YYYY-MM-DD HH:MM:SS.mmm" (is_t)
        m_ns = _NS_RE.search(label)
        if m_ns:
            ts_dt = datetime.fromtimestamp(int(m_ns.group(1)) / 1e9, tz=_TZ_PRAGUE)
        else:
            m_dt = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})", label)
            if m_dt:
                try:
                    ts_dt = datetime.strptime(
                        m_dt.group(1), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=_TZ_PRAGUE)
                except ValueError:
                    pass
        if not cam_label:
            m_cam = re.match(r"^([\w\-]+)", label.strip())
            if m_cam:
                cam_label = _clean_cam_for_filename(m_cam.group(1))

    if not ts_dt:
        ts_dt = datetime.now(tz=_TZ_PRAGUE)
    if not cam_label:
        cam_label = "image"
    return f"{cam_label}_{ts_dt:%Y-%m-%d_%H-%M-%S}-{ts_dt.microsecond // 1000:03d}"


# ─────────────────────────────────────────────────────────────────
#  View settings — how the image is SHOWN. Never pixels.
# ─────────────────────────────────────────────────────────────────

GAMMA_SLIDER_MIN = getattr(img_scale, "GAMMA_SLIDER_MIN", 30)
GAMMA_SLIDER_MAX = getattr(img_scale, "GAMMA_SLIDER_MAX", 150)
GAMMA_SLIDER_NEUTRAL = getattr(img_scale, "GAMMA_SLIDER_NEUTRAL", 100)
AUTO_GAMMA_TARGET = getattr(img_scale, "AUTO_GAMMA_TARGET", 0.45)


@dataclass
class _ViewSettings:
    brightness: int = 0                  # additive offset, -255..255
    contrast: int = 0                    # multiplicative gain, -127..127
    gamma_slider: int = GAMMA_SLIDER_NEUTRAL
    auto_contrast: bool = False          # percentile stretch drives the display window
    auto_bright: bool = False            # ImageJ-style auto level
    auto_gamma: bool = False
    win_lo: int = 0                      # display window, in 8-bit display codes
    win_hi: int = 255
    palette: str = PALETTE_DEFAULT

    def key(self) -> tuple:
        return (self.brightness, self.contrast, self.gamma_slider, self.auto_contrast,
                self.auto_bright, self.auto_gamma, self.win_lo, self.win_hi, self.palette)

    def is_neutral(self) -> bool:
        return self.key() == _ViewSettings().key()


def _gamma_value(view: _ViewSettings, gray_stat: np.ndarray) -> float:
    if view.auto_gamma:
        if img_scale is not None:
            try:
                return float(img_scale.gamma_for_median(
                    float(np.median(_stat_sample(gray_stat))), 255.0, AUTO_GAMMA_TARGET))
            except Exception:
                pass
        med = max(1.0, float(np.median(_stat_sample(gray_stat))))
        return max(0.30, min(1.50, math.log(AUTO_GAMMA_TARGET) / math.log(med / 255.0)))
    return max(1, view.gamma_slider) / 100.0


def _contrast_gain(contrast: int) -> float:
    """Slider value in [-127, 127] → multiplicative gain (0 → 1.0). Same curve as the
    Image Slider, so the same number means the same thing in both tabs."""
    c = float(max(-127, min(127, contrast)))
    return (259.0 * (c + 127.0)) / (127.0 * (259.0 - c))


def auto_window(gray_stat: np.ndarray) -> "tuple[int, int]":
    """The p0.5..p99.5 display window, in 8-bit codes. Clipping a small fraction at the
    top means a few hot pixels cannot crush the rest of the frame to black."""
    s = _stat_sample(gray_stat)
    lo, hi = (float(v) for v in np.percentile(s, [0.5, 99.5]))
    if hi <= lo + 1:
        lo, hi = float(gray_stat.min()), float(gray_stat.max())
    if hi <= lo:
        return 0, 255
    return int(max(0, min(254, round(lo)))), int(max(1, min(255, round(hi))))


def render_view(base: np.ndarray, view: _ViewSettings) -> np.ndarray:
    """`base` (uint8, grey or RGB) → RGB/grey uint8 as it should appear on screen.

    Order: display window → manual contrast → gamma → brightness → palette. Contrast is
    a gain pivoted on the frame's own BLACK LEVEL, not on mid-grey: an absolute-scale
    frame sits near code 29, so pivoting on 128 drives it to black on the first nudge.

    Cyclic palettes (NI Binary) bypass everything: their bands are tied to fixed absolute
    values, so any stretch applied first would move the bands and lie about the level."""
    name = view.palette
    lut = GRADIENTS.get(name)

    if name in CYCLIC_PALETTES and lut is not None:
        return _lut_pixels(lut, np.clip(_to_gray(base), 0, 255).astype(np.uint8), name)

    keep_colour = (name == "Default" and base.ndim == 3)
    if keep_colour:
        work = base.astype(np.float32)
        stat = _to_gray(base)
    else:
        work = np.asarray(_to_gray(base), dtype=np.float32)
        stat = work

    lo, hi = view.win_lo, view.win_hi
    if view.auto_contrast:
        lo, hi = auto_window(stat)
    if (lo, hi) != (0, 255) and hi > lo:
        scale = 255.0 / (hi - lo)
        work = (work - lo) * scale
        stat = (stat - lo) * scale if not keep_colour else _to_gray(np.clip(work, 0, 255))

    if view.contrast:
        pivot = float(np.percentile(_stat_sample(stat), 0.5))
        gain = _contrast_gain(view.contrast)
        work = (work - pivot) * gain + pivot
        stat = work if not keep_colour else (stat - pivot) * gain + pivot

    gamma = _gamma_value(view, np.clip(stat, 0, 255))
    if abs(gamma - 1.0) > 1e-3:
        work = 255.0 * np.power(np.clip(work, 0, 255) / 255.0, gamma)
        stat = work if not keep_colour else 255.0 * np.power(
            np.clip(stat, 0, 255) / 255.0, gamma)

    if view.auto_bright:
        s = _stat_sample(np.clip(stat, 0, 255))
        blo = float(np.percentile(s, 0.5))
        bhi = float(np.percentile(s, 99.5))
        if bhi > blo:
            work = (work - blo) * (255.0 / (bhi - blo))
    elif view.brightness:
        work = work + view.brightness

    u8 = np.clip(work, 0, 255).astype(np.uint8)
    if lut is None:
        return u8
    return _lut_pixels(lut, u8, name).astype(np.uint8)


# ═════════════════════════════════════════════════════════════════
#  PIXEL OPERATIONS
# ═════════════════════════════════════════════════════════════════
#  Everything from here to the beam maths takes arrays and returns NEW arrays.
#  Nothing is modified in place: the undo history shares array references between
#  states (see _WorkshopSlot._state), so an in-place write would rewrite history too.
#
#  Two layers travel together through every operation:
#    base   the 8-bit picture on screen (grey HxW or RGB HxWx3)
#    raw    the camera's counts, uint16 grey, used for every measurement
#  An operation that changes what the picture LOOKS like has to change what it
#  MEASURES the same way, or the Measure panel describes a picture that is no longer
#  on screen. So each op below works on either layer, in its own dtype, and
#  WorkshopWidget._apply_to_both applies it to both.
#
#  No Qt in this block, on purpose: an operation that can be checked against a
#  synthetic Gaussian has no business being tangled up with widgets.
#
#  scipy is used where it does the job better (median, gaussian, morphology) and is
#  already part of the build, but every one of those has a numpy fallback so the tab
#  still works in a build without it.


_ND = "unset"        # "unset" until the first look; then the module or None


def _ndimage():
    """scipy.ndimage or None. Imported on first use, not at module import: the
    Workshop must open even in a build where scipy was left out."""
    global _ND
    if _ND == "unset":
        try:
            from scipy import ndimage as nd
            _ND = nd
        except Exception:
            _ND = None
    return _ND


def has_scipy() -> bool:
    return _ndimage() is not None


# ── dtype bookkeeping ────────────────────────────────────────────────────────

def _limits(arr: np.ndarray) -> "tuple[float, float]":
    """The range a result has to fit back into, taken from the input dtype."""
    if arr.dtype == np.uint8:
        return 0.0, 255.0
    if arr.dtype == np.uint16:
        return 0.0, 65535.0
    if np.issubdtype(arr.dtype, np.integer):
        info = np.iinfo(arr.dtype)
        return float(info.min), float(info.max)
    return -math.inf, math.inf


def _restore(work: np.ndarray, like: np.ndarray) -> np.ndarray:
    """Float result → the dtype and range it came from."""
    lo, hi = _limits(like)
    if math.isfinite(lo):
        work = np.clip(work, lo, hi)
    if np.issubdtype(like.dtype, np.integer):
        work = np.rint(work)
    return np.ascontiguousarray(work.astype(like.dtype))


def _per_channel(arr: np.ndarray, fn) -> np.ndarray:
    """Run a 2-D function over a grey image or over each channel of an RGB one."""
    if arr.ndim == 2:
        return fn(arr)
    out = np.empty(arr.shape, dtype=np.float64)
    for c in range(arr.shape[2]):
        out[:, :, c] = fn(arr[:, :, c])
    return out


# ── neighbourhood filters ────────────────────────────────────────────────────

def _box_mean(a: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r+1)² box, from the integral image — O(1) per pixel and no
    dependency. Edges use the smaller box that actually fits, so a filtered frame
    does not grow a dark border."""
    a = a.astype(np.float64)
    h, w = a.shape
    s = np.zeros((h + 1, w + 1), dtype=np.float64)
    s[1:, 1:] = a.cumsum(axis=0).cumsum(axis=1)
    ys = np.arange(h)
    xs = np.arange(w)
    y0 = np.maximum(0, ys - radius)
    y1 = np.minimum(h, ys + radius + 1)
    x0 = np.maximum(0, xs - radius)
    x1 = np.minimum(w, xs + radius + 1)
    tot = (s[np.ix_(y1, x1)] - s[np.ix_(y0, x1)]
           - s[np.ix_(y1, x0)] + s[np.ix_(y0, x0)])
    cnt = np.outer(y1 - y0, x1 - x0).astype(np.float64)
    return tot / np.maximum(cnt, 1.0)


def _gauss_1d(sigma: float) -> np.ndarray:
    r = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


def _sep_convolve(a: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Separable convolution with edge clamping (no dark border)."""
    r = (len(k) - 1) // 2
    h, w = a.shape
    pad = np.pad(a.astype(np.float64), r, mode="edge")
    cols = np.zeros((h + 2 * r, w), dtype=np.float64)
    for i, weight in enumerate(k):
        if weight:
            cols += weight * pad[:, i:i + w]
    rows = np.zeros((h, w), dtype=np.float64)
    for i, weight in enumerate(k):
        if weight:
            rows += weight * cols[i:i + h, :]
    return rows


def gaussian(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur. `sigma` is in pixels."""
    sigma = max(0.1, float(sigma))
    nd = _ndimage()
    if nd is not None:
        fn = lambda a: nd.gaussian_filter(a.astype(np.float64), sigma, mode="nearest")
    else:
        k = _gauss_1d(sigma)
        fn = lambda a: _sep_convolve(a, k)
    return _restore(_per_channel(arr, fn), arr)


def median(arr: np.ndarray, radius: int) -> np.ndarray:
    """Median over a (2r+1)² box — the filter for hot pixels and salt-and-pepper
    speckle. Unlike a blur it removes the outlier without smearing an edge."""
    radius = max(1, int(radius))
    nd = _ndimage()
    if nd is not None:
        fn = lambda a: nd.median_filter(a, size=2 * radius + 1, mode="nearest")
        return _restore(_per_channel(arr, fn), arr)
    # Fallback: a stack of the (2r+1)² shifted copies. Fine for the small radii the
    # panel offers, and it is only ever reached in a build without scipy.
    def fn(a):
        pad = np.pad(a.astype(np.float64), radius, mode="edge")
        h, w = a.shape
        stack = np.empty(((2 * radius + 1) ** 2, h, w), dtype=np.float64)
        i = 0
        for dy in range(2 * radius + 1):
            for dx in range(2 * radius + 1):
                stack[i] = pad[dy:dy + h, dx:dx + w]
                i += 1
        return np.median(stack, axis=0)
    return _restore(_per_channel(arr, fn), arr)


def sharpen(arr: np.ndarray, amount: float = 1.0, sigma: float = 1.0) -> np.ndarray:
    """Unsharp mask: add back what a blur removed. `amount` 0 does nothing, 1 is a
    normal sharpen, 3 is heavy."""
    blur = gaussian(arr, sigma).astype(np.float64)
    work = arr.astype(np.float64) + float(amount) * (arr.astype(np.float64) - blur)
    return _restore(work, arr)


def edges(arr: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude — bright where the picture changes fastest."""
    nd = _ndimage()

    def fn(a):
        a = a.astype(np.float64)
        if nd is not None:
            gx = nd.sobel(a, axis=1, mode="nearest")
            gy = nd.sobel(a, axis=0, mode="nearest")
        else:
            pad = np.pad(a, 1, mode="edge")
            kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float64)
            gx = np.zeros_like(a)
            gy = np.zeros_like(a)
            for dy in range(3):
                for dx in range(3):
                    win = pad[dy:dy + a.shape[0], dx:dx + a.shape[1]]
                    gx += kx[dy, dx] * win
                    gy += kx[dx, dy] * win
        return np.hypot(gx, gy)

    return _restore(_per_channel(arr, fn), arr)


# ── background ───────────────────────────────────────────────────────────────

BACKGROUND_MODES = ("Constant level", "Sloping plane", "Curved surface",
                    "Rolling ball")


def background_map(arr: np.ndarray, mode: str, param: float) -> np.ndarray:
    """The background this frame is sitting on, as a float array the size of the
    frame. Subtracting it is the caller's job (see subtract_background), because the
    two layers clip differently.

    Constant level  a percentile of the whole frame (param = that percentile)
    Sloping plane   least-squares plane through the frame
    Curved surface  least-squares quadratic surface — for vignetting and glow
    Rolling ball    a morphological opening: what is left after everything smaller
                    than `param` pixels across has been rolled away. Same idea as
                    ImageJ's rolling ball, done with an opening plus a smooth rather
                    than with ImageJ's shrink-and-roll, so the numbers will not match
                    ImageJ to the count.
    """
    data = arr.astype(np.float64)
    if data.ndim == 3:
        # One background for the whole picture, taken from its luma, so the three
        # channels do not drift apart in colour.
        flat = luma(data)
    else:
        flat = data

    if mode == BACKGROUND_MODES[0]:
        pct = min(99.0, max(0.0, float(param)))
        return np.full(flat.shape, float(np.percentile(flat, pct)))

    if mode in (BACKGROUND_MODES[1], BACKGROUND_MODES[2]):
        deg = 1 if mode == BACKGROUND_MODES[1] else 2
        return _surface_fit(flat, deg)

    radius = max(1, int(param))
    nd = _ndimage()
    if nd is not None:
        size = 2 * radius + 1
        bg = nd.grey_opening(flat, size=(size, size), mode="nearest")
        return nd.gaussian_filter(bg, radius / 2.0, mode="nearest")
    # Opening = erosion then dilation, both as box extremes over the integral-free
    # shifted stack. Slower, only used without scipy.
    er = _box_extreme(flat, radius, np.minimum)
    di = _box_extreme(er, radius, np.maximum)
    return _sep_convolve(di, _gauss_1d(max(0.5, radius / 2.0)))


def _box_extreme(a: np.ndarray, radius: int, fn) -> np.ndarray:
    out = a.astype(np.float64).copy()
    pad = np.pad(a.astype(np.float64), radius, mode="edge")
    h, w = a.shape
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            out = fn(out, pad[dy:dy + h, dx:dx + w])
    return out


def _surface_fit(flat: np.ndarray, deg: int) -> np.ndarray:
    """Least-squares plane (deg 1) or quadratic surface (deg 2) through the frame.

    Fitted on a subsample: a full-frame design matrix for a 4 Mpx image is 24 MB per
    column and buys nothing — a smooth surface is decided by thousands of points, not
    by millions."""
    h, w = flat.shape
    sy = max(1, h // 256)
    sx = max(1, w // 256)
    ys = np.arange(0, h, sy, dtype=np.float64)
    xs = np.arange(0, w, sx, dtype=np.float64)
    gy, gx = np.meshgrid(ys, xs, indexing="ij")
    z = flat[::sy, ::sx].ravel()
    gx, gy = gx.ravel(), gy.ravel()
    cols = [np.ones_like(gx), gx, gy]
    if deg >= 2:
        cols += [gx * gx, gy * gy, gx * gy]
    A = np.stack(cols, axis=1)
    try:
        coef, *_ = np.linalg.lstsq(A, z, rcond=None)
    except Exception:
        return np.full(flat.shape, float(np.median(flat)))
    Y, X = np.mgrid[0:h, 0:w].astype(np.float64)
    out = coef[0] + coef[1] * X + coef[2] * Y
    if deg >= 2:
        out = out + coef[3] * X * X + coef[4] * Y * Y + coef[5] * X * Y
    return out


def subtract_background(arr: np.ndarray, mode: str, param: float,
                        bg: "np.ndarray | None" = None) -> np.ndarray:
    """Frame minus its background, negatives cut to zero.

    `bg` lets the caller compute the background once and apply the SAME map to both
    layers — the base and the counts must not each fit their own surface, or the
    picture and the numbers stop describing the same subtraction."""
    if bg is None:
        bg = background_map(arr, mode, param)
    data = arr.astype(np.float64)
    if data.ndim == 3 and bg.ndim == 2:
        bg = bg[:, :, None]
    return _restore(data - bg, arr)


# ── combining several images ─────────────────────────────────────────────────

PROJECT_MODES = ("Average", "Maximum", "Sum", "Minimum")


def luma(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return arr.astype(np.float64)
    return (0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] +
            0.114 * arr[:, :, 2]).astype(np.float64)


def common_shape(arrays) -> "tuple[int, int]":
    """The overlap of a set of frames — the largest area every one of them covers."""
    hs = [a.shape[0] for a in arrays]
    ws = [a.shape[1] for a in arrays]
    return min(hs), min(ws)


def project(arrays, mode: str, out_dtype=None) -> np.ndarray:
    """Average / maximum / sum / minimum across several frames, over their overlap.

    Sum is the one that can leave the input range (ten frames of 200 counts make
    2000), so it is computed in float64 and the caller decides what scale the result
    lives on — that is why `out_dtype` is explicit rather than copied from the input.
    """
    if not arrays:
        raise ValueError("nothing to combine")
    h, w = common_shape(arrays)
    grey = any(a.ndim == 2 for a in arrays)
    stack = []
    for a in arrays:
        part = a[:h, :w]
        if grey and part.ndim == 3:
            part = luma(part)
        stack.append(part.astype(np.float64))
    data = np.stack(stack, axis=0)
    if mode == "Average":
        out = data.mean(axis=0)
    elif mode == "Maximum":
        out = data.max(axis=0)
    elif mode == "Minimum":
        out = data.min(axis=0)
    else:
        out = data.sum(axis=0)
    if out_dtype is None:
        return out
    lo, hi = _limits(np.zeros(0, dtype=out_dtype))
    if math.isfinite(lo):
        out = np.clip(out, lo, hi)
    if np.issubdtype(np.dtype(out_dtype), np.integer):
        out = np.rint(out)
    return np.ascontiguousarray(out.astype(out_dtype))


def merge_rg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Two frames as one colour picture: the first in red, the second in green.

    Where both have signal the result is yellow, so a shift between them shows up as
    a red edge on one side and a green edge on the other — much easier to see than a
    difference image, which only says "something moved here"."""
    h, w = common_shape([a, b])
    ra = np.clip(luma(a[:h, :w]), 0, 255).astype(np.uint8)
    rb = np.clip(luma(b[:h, :w]), 0, 255).astype(np.uint8)
    out = np.zeros((h, w, 3), dtype=np.uint8)
    out[:, :, 0] = ra
    out[:, :, 1] = rb
    return out


# ── geometry ─────────────────────────────────────────────────────────────────

def _pil_resample(nearest: bool):
    res = getattr(_PilImg, "Resampling", None)
    if res is not None:
        return res.NEAREST if nearest else res.BILINEAR
    return _PilImg.NEAREST if nearest else _PilImg.BILINEAR


def rotate(arr: np.ndarray, degrees: float, expand: bool = True,
           nearest: bool = False) -> np.ndarray:
    """Turn by any angle, anticlockwise.

    `nearest` is for the counts layer: interpolating measured values would invent
    numbers the sensor never recorded. Pillow handles uint8 and uint16 grey directly;
    anything else is routed through float32, which its affine transform also takes."""
    img = arr
    to_float = arr.dtype not in (np.uint8, np.uint16)
    if to_float:
        img = arr.astype(np.float32)
    pil = _PilImg.fromarray(np.ascontiguousarray(img))
    out = pil.rotate(float(degrees), resample=_pil_resample(nearest),
                     expand=bool(expand), fillcolor=0)
    res = np.asarray(out)
    return _restore(res.astype(np.float64), arr) if to_float else np.ascontiguousarray(res)


def _rotate_matrix(shape, degrees: float, expand: bool):
    """Pillow's own rotation matrix and output size, worked out the same way
    Image.rotate does it.

    Reproduced rather than approximated because the annotations have to land on the
    rotated picture to the pixel: a rounded-off size of my own was one pixel out from
    Pillow's at some angles, which is enough to walk a region off its feature. The
    matrix maps OUTPUT coordinates back to INPUT ones — that is the direction Pillow's
    affine transform works in."""
    h, w = shape[0], shape[1]
    ang = -math.radians(float(degrees))
    a = round(math.cos(ang), 15)
    b = round(math.sin(ang), 15)
    d = -b
    e = a
    cx, cy = w / 2.0, h / 2.0
    c = a * -cx + b * -cy + cx
    f = d * -cx + e * -cy + cy
    nw, nh = w, h
    if expand:
        xs, ys = [], []
        for x, y in ((0, 0), (w, 0), (w, h), (0, h)):
            xs.append(a * x + b * y + c)
            ys.append(d * x + e * y + f)
        nw = int(math.ceil(max(xs)) - math.floor(min(xs)))
        nh = int(math.ceil(max(ys)) - math.floor(min(ys)))
        # Pillow shifts the sampling origin by the ROTATED half-growth, not by the
        # half-growth itself: the translation is multiplied into the matrix from the
        # right, so it goes through the rotation first. Adding it straight to c and f
        # looks right and puts every annotation tens of pixels out.
        tx, ty = -(nw - w) / 2.0, -(nh - h) / 2.0
        c, f = a * tx + b * ty + c, d * tx + e * ty + f
    return (a, b, c, d, e, f), (nh, nw)


def rotate_size(shape, degrees: float, expand: bool) -> "tuple[int, int]":
    """The (h, w) `rotate` will produce — needed to map annotations onto the result."""
    return _rotate_matrix(shape, degrees, expand)[1]


def rotate_point_map(shape, degrees: float, expand: bool):
    """(x, y) in the original → (x, y) in the rotated frame.

    The matrix above goes the other way, so this is its inverse. For a pure rotation
    the 2×2 part is orthogonal with determinant 1, which makes the inverse a matter of
    swapping two signs."""
    (a, b, c, d, e, f), _ = _rotate_matrix(shape, degrees, expand)

    def fn(x: float, y: float):
        # Pillow's matrix is written in corner coordinates while an annotation point
        # names a pixel, i.e. its centre. Half a pixel in and half a pixel back out —
        # without it a quarter turn lands one row off.
        px, py = float(x) + 0.5 - c, float(y) + 0.5 - f
        return (e * px - b * py - 0.5, a * py - d * px - 0.5)

    return fn


def bin_pixels(arr: np.ndarray, factor: int, mode: str = "Average") -> np.ndarray:
    """Join factor × factor blocks into one pixel.

    "Sum" is what a detector does when you bin it on the chip — the values add up, so
    the result no longer fits the original full scale and the caller has to move the
    scale with it. "Average" keeps the scale and just trades resolution for noise.
    Rows and columns that do not fill a whole block are dropped rather than padded:
    a half-weighted edge pixel would read as a dark line."""
    factor = max(2, int(factor))
    h = (arr.shape[0] // factor) * factor
    w = (arr.shape[1] // factor) * factor
    if h < factor or w < factor:
        return arr
    cut = arr[:h, :w].astype(np.float64)
    if cut.ndim == 3:
        blocks = cut.reshape(h // factor, factor, w // factor, factor, cut.shape[2])
        out = blocks.sum(axis=(1, 3)) if mode == "Sum" else blocks.mean(axis=(1, 3))
    else:
        blocks = cut.reshape(h // factor, factor, w // factor, factor)
        out = blocks.sum(axis=(1, 3)) if mode == "Sum" else blocks.mean(axis=(1, 3))
    if mode == "Sum":
        # Summed counts outgrow uint16; the dtype has to grow with them or the first
        # bright spot wraps around to black.
        if np.issubdtype(arr.dtype, np.integer):
            return np.ascontiguousarray(np.rint(out).astype(np.uint32))
        return np.ascontiguousarray(out.astype(arr.dtype))
    return _restore(out, arr)


# ── writing files ────────────────────────────────────────────────────────────

ANIM_SUFFIXES = (".gif", ".png", ".webp")


def pad_frames(frames) -> list:
    """Frames of different sizes onto one canvas, each centred on black.

    Padding rather than scaling on purpose: an animation is usually made to compare
    frames, and resampling one of them to match another would change the very thing
    being compared."""
    if not frames:
        return []
    h = max(f.shape[0] for f in frames)
    w = max(f.shape[1] for f in frames)
    out = []
    for f in frames:
        if f.ndim == 2:
            f = np.stack([f] * 3, axis=2)
        if f.shape[0] == h and f.shape[1] == w:
            out.append(np.ascontiguousarray(f))
            continue
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        y = (h - f.shape[0]) // 2
        x = (w - f.shape[1]) // 2
        canvas[y:y + f.shape[0], x:x + f.shape[1]] = f
        out.append(canvas)
    return out


def write_animation(frames, path, ms_per_frame: int = 200, loop: bool = True) -> str:
    """Write an animated GIF, animated PNG or animated WebP; the suffix decides which.

    Returns "" on success or a message explaining what went wrong. GIF carries 256
    colours per frame, which is plenty for a greyscale camera but will band a colour
    palette; APNG and WebP keep full colour and make much bigger files.
    """
    frames = pad_frames([np.asarray(f) for f in frames])
    if len(frames) < 2:
        return "an animation needs at least two images"
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in ANIM_SUFFIXES:
        return f"{suffix or 'that'} is not an animation format"
    ms = max(10, int(ms_per_frame))
    try:
        images = [_PilImg.fromarray(f) for f in frames]
        kw = dict(save_all=True, append_images=images[1:], duration=ms,
                  loop=0 if loop else 1)
        if suffix == ".gif":
            # Palette per frame, and disposal 2 so a frame is cleared before the next
            # one is drawn — without it a smaller frame leaves the previous one showing
            # around its edges.
            kw["disposal"] = 2
            kw["optimize"] = False
        elif suffix == ".webp":
            kw["lossless"] = True
            kw["quality"] = 90
        images[0].save(str(p), **kw)
        return ""
    except Exception as e:
        return f"{e}"


def write_data_tiff(arr: np.ndarray, path, description: str = "") -> str:
    """Write the measurement layer as a single-channel TIFF, values untouched.

    This is the file to open in ImageJ or to feed a script: no palette, no display
    stretch, no annotations — the counts as they were measured. Returns "" on success
    or a message.
    """
    try:
        data = np.asarray(arr)
        if data.ndim == 3:
            data = luma(data)
        if data.dtype == np.uint8:
            out = data
        elif np.issubdtype(data.dtype, np.integer):
            out = np.clip(data, 0, 65535).astype(np.uint16)
        else:
            out = np.clip(np.rint(data), 0, 65535).astype(np.uint16)
        img = _PilImg.fromarray(np.ascontiguousarray(out))
        kw = {}
        if description:
            kw["description"] = description
        img.save(str(Path(path)), format="TIFF", **kw)
        return ""
    except Exception as e:
        return f"{e}"

# ═════════════════════════════════════════════════════════════════
#  BEAM MEASUREMENTS
# ═════════════════════════════════════════════════════════════════
#  The numbers a laser lab asks a camera frame for, and the ones ImageJ does not have:
#  how wide the spot is (FWHM, D4σ, 1/e²), how round it is, which way its long axis
#  points, and how much of the energy sits inside a given circle.
#
#  TWO RULES THROUGHOUT
#
#  1. A pedestal ruins a width. Every second-moment width weights a pixel by its value
#     times its distance SQUARED, so a background of a few counts spread over the whole
#     frame outweighs the spot itself and D4σ comes out as roughly the frame size. So a
#     baseline is subtracted before any moment is taken, and which baseline was used is
#     returned with the result — a width without its baseline is not a measurement.
#
#  2. Nothing here is fitted unless it is asked for. FWHM and D4σ are read off the data;
#     the Gaussian fit is a separate call whose r² says how much the shape can be
#     trusted as a Gaussian at all.
#
#  scipy.optimize refines the Gaussian fit when it is present, and the moment estimate
#  stands in for it when it is not.


def _gray(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        return arr.astype(np.float64)
    return (0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] +
            0.114 * arr[:, :, 2]).astype(np.float64)


def baseline_value(data: np.ndarray, percentile: float = 5.0) -> float:
    """The level the spot is sitting on: a low percentile of the area being measured.

    A percentile rather than the minimum, because one dead pixel reading zero would
    otherwise define the background for the whole frame."""
    if data.size == 0:
        return 0.0
    return float(np.percentile(data, max(0.0, min(50.0, float(percentile)))))


def width_at_fraction(vals: np.ndarray, frac: float,
                      baseline: float = 0.0) -> "dict | None":
    """Width of a 1-D trace at `frac` of its height above `baseline`.

    frac 0.5 gives FWHM, frac 1/e² = 0.135 gives the 1/e² width the laser world
    quotes. The two crossings are found by walking OUT from the peak, so a second,
    smaller bump elsewhere in the trace cannot be mistaken for the far edge, and each
    crossing is interpolated between its two neighbouring samples — rounding to whole
    pixels would quantise a 20 px spot to 5 % steps.

    Returns None when the trace never comes back down on one side, i.e. the feature is
    wider than the data given."""
    y = np.asarray(vals, dtype=np.float64)
    if y.size < 3:
        return None
    peak_i = int(np.argmax(y))
    peak = float(y[peak_i])
    height = peak - float(baseline)
    if height <= 0:
        return None
    level = float(baseline) + height * float(frac)

    left = None
    for i in range(peak_i, 0, -1):
        if y[i - 1] <= level <= y[i] or y[i - 1] >= level >= y[i]:
            span = y[i] - y[i - 1]
            t = 0.0 if span == 0 else (level - y[i - 1]) / span
            left = (i - 1) + t
            break
    right = None
    for i in range(peak_i, y.size - 1):
        if y[i] >= level >= y[i + 1] or y[i] <= level <= y[i + 1]:
            span = y[i + 1] - y[i]
            t = 0.0 if span == 0 else (level - y[i]) / span
            right = i + t
            break
    if left is None or right is None:
        return None
    return {"width": float(right - left), "left": float(left),
            "right": float(right), "level": level, "peak": peak,
            "peak_pos": float(peak_i), "baseline": float(baseline)}


# ── line profile measurements ────────────────────────────────────────────────

def profile_metrics(x, y, baseline_pct: float = 5.0) -> dict:
    """Everything worth reading off a line profile: peak, centre of mass, FWHM and
    1/e² width, all in the units of `x` (pixels, or millimetres once a scale is set).

    The widths come back in x units by scaling the sample-index width by the average
    step of `x`, so a profile drawn diagonally still reads its true length."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    out = {}
    if y.size < 3:
        return out
    base = baseline_value(y, baseline_pct)
    step = float(np.mean(np.diff(x))) if x.size > 1 else 1.0
    if not math.isfinite(step) or step <= 0:
        step = 1.0
    out["baseline"] = base
    out["peak"] = float(y.max())
    out["peak_x"] = float(x[int(np.argmax(y))])
    above = np.clip(y - base, 0, None)
    tot = float(above.sum())
    if tot > 0:
        out["centroid_x"] = float((above * x).sum() / tot)
        # Second moment of the same trace: the "D4σ" of a one-dimensional cut.
        var = float((above * (x - out["centroid_x"]) ** 2).sum() / tot)
        if var > 0:
            out["d4sigma"] = 4.0 * math.sqrt(var)
    for key, frac in (("fwhm", 0.5), ("w_1e2", 1.0 / math.e ** 2)):
        w = width_at_fraction(y, frac, base)
        if w is not None:
            out[key] = w["width"] * step
            out[f"{key}_left"] = float(x[0]) + w["left"] * step
            out[f"{key}_right"] = float(x[0]) + w["right"] * step
            out[f"{key}_level"] = w["level"]
    return out


def gaussian_fit(x, y) -> "dict | None":
    """Least-squares Gaussian on a pedestal: base + amp · exp(−(x−mu)²/(2σ²)).

    The starting point comes from the data's own moments, which is already close
    enough to plot; scipy then refines it when it is available. r² is reported so the
    operator can see whether calling this spot Gaussian is honest — a clipped or
    double-lobed profile fits badly and says so.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if y.size < 5:
        return None
    base = baseline_value(y, 5.0)
    above = np.clip(y - base, 0, None)
    tot = float(above.sum())
    if tot <= 0:
        return None
    mu = float((above * x).sum() / tot)
    var = float((above * (x - mu) ** 2).sum() / tot)
    sigma = math.sqrt(var) if var > 0 else max(1.0, float(np.ptp(x)) / 10.0)
    amp = float(y.max() - base)
    p = [amp, mu, max(1e-6, sigma), base]

    try:
        from scipy.optimize import curve_fit

        def model(xx, a, m, s, b):
            return b + a * np.exp(-((xx - m) ** 2) / (2.0 * s * s))

        popt, _ = curve_fit(model, x, y, p0=p, maxfev=8000)
        p = [float(v) for v in popt]
        fitted = model(x, *p)
    except Exception:
        fitted = p[3] + p[0] * np.exp(-((x - p[1]) ** 2) / (2.0 * p[2] * p[2]))

    resid = y - fitted
    denom = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / denom if denom > 0 else 0.0
    sigma = abs(p[2])
    return {"amp": p[0], "mu": p[1], "sigma": sigma, "base": p[3],
            # 2·√(2·ln2)·σ for FWHM, 4σ for the 1/e² diameter of a Gaussian.
            "fwhm": 2.0 * math.sqrt(2.0 * math.log(2.0)) * sigma,
            "d_1e2": 4.0 * sigma, "r2": r2, "fitted": fitted}


# ── whole-spot measurements ──────────────────────────────────────────────────

def beam_stats(arr: np.ndarray, mask: "np.ndarray | None" = None,
               baseline_pct: float = 5.0) -> dict:
    """Spot size and shape from the intensity moments of a region.

    Returns, all in pixels and counts:
      total, peak, peak_x/peak_y, cx/cy          how much and where
      d4s_x, d4s_y                              D4σ along the image axes
      d4s_major, d4s_minor, angle_deg           the same after finding the long axis
      ellipticity                               minor / major, 1.0 = round
      fwhm_x, fwhm_y, w1e2_x, w1e2_y            read off the row and column through
                                                the centre of mass
      baseline                                  what was taken off first

    The axis-aligned D4σ pair and the major/minor pair are both given because a spot
    that is tilted reads wider on both axes than it really is — comparing the two is
    how you notice the tilt.
    """
    data = _gray(arr)
    if mask is not None:
        if not mask.any():
            return {}
        sel = data[mask]
    else:
        sel = data
    base = baseline_value(sel, baseline_pct)
    work = np.clip(data - base, 0, None)
    if mask is not None:
        work = np.where(mask, work, 0.0)
    tot = float(work.sum())
    out = {"baseline": base, "total": tot, "peak": float(sel.max())}
    if tot <= 0:
        return out

    h, w = work.shape
    xs = np.arange(w, dtype=np.float64)
    ys = np.arange(h, dtype=np.float64)
    col = work.sum(axis=0)            # collapsed onto x
    row = work.sum(axis=1)            # collapsed onto y
    cx = float((col * xs).sum() / tot)
    cy = float((row * ys).sum() / tot)
    out["cx"], out["cy"] = cx, cy

    py, px = np.unravel_index(int(np.argmax(np.where(mask, data, -np.inf)
                                            if mask is not None else data)), data.shape)
    out["peak_x"], out["peak_y"] = float(px), float(py)

    sxx = float((col * (xs - cx) ** 2).sum() / tot)
    syy = float((row * (ys - cy) ** 2).sum() / tot)
    # The cross moment needs the full 2-D array: it cannot be had from the two
    # collapsed profiles, and it is the term that carries the tilt.
    sxy = float((work * np.outer(ys - cy, xs - cx)).sum() / tot)
    out["d4s_x"] = 4.0 * math.sqrt(max(0.0, sxx))
    out["d4s_y"] = 4.0 * math.sqrt(max(0.0, syy))

    # Principal axes of the second-moment matrix [[sxx, sxy], [sxy, syy]].
    diff = sxx - syy
    root = math.sqrt(max(0.0, diff * diff + 4.0 * sxy * sxy))
    lam1 = 0.5 * (sxx + syy + root)
    lam2 = 0.5 * (sxx + syy - root)
    out["d4s_major"] = 4.0 * math.sqrt(max(0.0, lam1))
    out["d4s_minor"] = 4.0 * math.sqrt(max(0.0, lam2))
    if out["d4s_major"] > 0:
        out["ellipticity"] = out["d4s_minor"] / out["d4s_major"]
    # Angle of the long axis, measured anticlockwise from the horizontal as it appears
    # on screen (image y grows downwards, hence the minus).
    out["angle_deg"] = -0.5 * math.degrees(math.atan2(2.0 * sxy, diff)) if (
        abs(sxy) > 0 or abs(diff) > 0) else 0.0

    # Widths straight off the data, on the row and the column through the centre of
    # mass — the number a Gaussian fit should agree with, and does not when the spot
    # is clipped or has a shoulder.
    iy = int(min(h - 1, max(0, round(cy))))
    ix = int(min(w - 1, max(0, round(cx))))
    row_vals = data[iy, :].copy()
    col_vals = data[:, ix].copy()
    if mask is not None:
        row_vals = np.where(mask[iy, :], row_vals, base)
        col_vals = np.where(mask[:, ix], col_vals, base)
    for key, vals, frac in (("fwhm_x", row_vals, 0.5), ("fwhm_y", col_vals, 0.5),
                            ("w1e2_x", row_vals, 1.0 / math.e ** 2),
                            ("w1e2_y", col_vals, 1.0 / math.e ** 2)):
        m = width_at_fraction(vals, frac, base)
        if m is not None:
            out[key] = m["width"]
    return out


def radial_profile(arr: np.ndarray, cx: float, cy: float,
                   mask: "np.ndarray | None" = None,
                   bin_px: float = 1.0) -> "tuple[np.ndarray, np.ndarray]":
    """Average value against distance from (cx, cy).

    Averaging every direction together beats a single line through the spot: the noise
    on the average falls with the number of pixels in the ring, so a faint halo that a
    single profile cannot separate from noise shows up clearly.

    Returns (radius in px, mean value)."""
    data = _gray(arr)
    h, w = data.shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - float(cx), yy - float(cy))
    if mask is not None:
        r = r[mask]
        vals = data[mask]
    else:
        r = r.ravel()
        vals = data.ravel()
    if r.size == 0:
        return np.zeros(0), np.zeros(0)
    step = max(0.25, float(bin_px))
    idx = (r / step).astype(np.int64)
    n = int(idx.max()) + 1
    counts = np.bincount(idx, minlength=n).astype(np.float64)
    sums = np.bincount(idx, weights=vals.astype(np.float64), minlength=n)
    keep = counts > 0
    radii = (np.arange(n, dtype=np.float64)[keep] + 0.5) * step
    return radii, sums[keep] / counts[keep]


def encircled_energy(arr: np.ndarray, cx: float, cy: float,
                     mask: "np.ndarray | None" = None,
                     baseline_pct: float = 5.0,
                     fractions=(0.5, 0.865, 0.9)) -> dict:
    """How much of the signal sits inside a circle, against the circle's radius.

    The classic focus-quality number: 86.5 % is the fraction inside the 1/e² radius of
    a perfect Gaussian, so comparing the measured 86.5 % radius with D4σ/4 says how
    much energy is in the wings that the width alone hides.

    Returns {"radii", "fraction", "r50"/"r86"/…, "total"} — radii in pixels, fraction
    from 0 to 1. The circle is only meaningful out to the edge of the data, so the
    curve stops where the ring first leaves the region and the caller is told (key
    "clipped")."""
    data = _gray(arr)
    h, w = data.shape
    sel = data[mask] if mask is not None else data
    base = baseline_value(sel, baseline_pct)
    work = np.clip(data - base, 0, None)
    if mask is not None:
        work = np.where(mask, work, 0.0)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - float(cx), yy - float(cy))
    idx = np.rint(r).astype(np.int64)
    n = int(idx.max()) + 1
    ring = np.bincount(idx.ravel(), weights=work.ravel(), minlength=n)
    cum = np.cumsum(ring)
    total = float(cum[-1])
    out = {"radii": np.arange(n, dtype=np.float64), "total": total,
           "baseline": base}
    if total <= 0:
        out["fraction"] = np.zeros(n)
        return out
    frac = cum / total
    out["fraction"] = frac
    # Where the circle first runs off the data: past that the "fraction" only grows
    # because there are no more pixels to add, not because the beam ended.
    out["r_max_valid"] = float(min(cx, cy, w - 1 - cx, h - 1 - cy))
    for f in fractions:
        i = int(np.searchsorted(frac, f))
        if i >= n:
            continue
        if i == 0:
            radius = 0.0
        else:
            span = frac[i] - frac[i - 1]
            t = 0.0 if span <= 0 else (f - frac[i - 1]) / span
            radius = (i - 1) + t
        out[f"r{int(round(f * 1000))}"] = float(radius)
        out["clipped"] = bool(radius > out["r_max_valid"])
    return out


# ─────────────────────────────────────────────────────────────────
#  Annotations — objects in image coordinates, painted on top
# ─────────────────────────────────────────────────────────────────
#  Stored as coordinates, not as painted pixels: that is what lets a shape be picked
#  up and moved after it was drawn, keeps it crisp at 3200 % zoom, and keeps the draw
#  colour out of the palette LUT (a red line through "Jet" would not come out red).

A_FREE, A_LINE, A_ARROW, A_RECT, A_ELLIPSE, A_POLY = (
    "free", "line", "arrow", "rect", "ellipse", "poly")
A_TEXT, A_RULER, A_PROFILE, A_ROI_RECT, A_ROI_ELLIPSE, A_CROSS = (
    "text", "ruler", "profile", "roi_rect", "roi_ellipse", "cross")
#  Three points: two arms and the corner between them, which is where the angle is read.
A_ANGLE = "angle"
#  A bar of a known real length, drawn into the picture so a saved copy carries its own
#  scale. Its `value` is that length in millimetres and its pixel length is recomputed
#  from the slot's scale on every refresh — the bar can never disagree with the scale.
A_SCALEBAR = "scalebar"

#  Shapes whose geometry is a bounding box (two corner points) and which therefore get
#  the full eight resize handles.
_BOX_KINDS = {A_RECT, A_ELLIPSE, A_ROI_RECT, A_ROI_ELLIPSE}
#  Shapes defined by two free endpoints.
_SEG_KINDS = {A_LINE, A_ARROW, A_RULER, A_PROFILE}
#  Shapes that measure something and report it in the Measure section.
MEASURE_KINDS = {A_RULER, A_ROI_RECT, A_ROI_ELLIPSE, A_PROFILE, A_CROSS, A_ANGLE,
                 A_SCALEBAR}
#  The shapes the Measure panel can read numbers out of.
_REGION_KINDS = {A_ROI_RECT, A_ROI_ELLIPSE}
#  What the results table has a row for, in the order the table lists them.
_TABLE_KINDS = (A_ROI_RECT, A_ROI_ELLIPSE, A_RULER, A_ANGLE, A_CROSS, A_PROFILE)


@dataclass
class _Annot:
    kind: str
    pts: list                                  # [[x, y], …] in image coordinates
    color: str = "#ff0000"
    width: int = 2
    filled: bool = False
    text: str = ""
    font_size: int = 16
    label: str = ""                            # measurement caption, filled on refresh
    #  One number a shape may need to carry. Only the scale bar uses it so far (its
    #  length in millimetres); it is here rather than in a subclass so that the undo
    #  history, the session file and copy() keep working without a special case.
    value: float = 0.0

    def copy(self) -> "_Annot":
        return _Annot(self.kind, [list(p) for p in self.pts], self.color, self.width,
                      self.filled, self.text, self.font_size, self.label, self.value)

    def to_dict(self) -> dict:
        """Plain-JSON form, for the session file."""
        return {"kind": self.kind, "pts": [[float(p[0]), float(p[1])] for p in self.pts],
                "color": self.color, "width": int(self.width), "filled": bool(self.filled),
                "text": self.text, "font_size": int(self.font_size),
                "value": float(self.value)}

    @staticmethod
    def from_dict(d: dict) -> "_Annot | None":
        try:
            kind = str(d["kind"])
            pts = [[float(p[0]), float(p[1])] for p in d["pts"]]
        except Exception:
            return None
        if not pts:
            return None
        return _Annot(kind, pts, str(d.get("color", "#ff0000")),
                      int(d.get("width", 2)), bool(d.get("filled", False)),
                      str(d.get("text", "")), int(d.get("font_size", 16)),
                      "", float(d.get("value", 0.0)))

    def bbox(self) -> "tuple[float, float, float, float]":
        xs = [p[0] for p in self.pts] or [0.0]
        ys = [p[1] for p in self.pts] or [0.0]
        return min(xs), min(ys), max(xs), max(ys)

    def translate(self, dx: float, dy: float):
        for p in self.pts:
            p[0] += dx
            p[1] += dy

    def handles(self) -> dict:
        """name → (x, y) in image coordinates. 'move' is always present."""
        x0, y0, x1, y1 = self.bbox()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if self.kind in _BOX_KINDS:
            return {"nw": (x0, y0), "n": (cx, y0), "ne": (x1, y0),
                    "w": (x0, cy), "e": (x1, cy),
                    "sw": (x0, y1), "s": (cx, y1), "se": (x1, y1),
                    "move": (cx, cy)}
        if self.kind in _SEG_KINDS and len(self.pts) >= 2:
            return {"p0": tuple(self.pts[0]), "p1": tuple(self.pts[1]),
                    "move": (cx, cy)}
        if self.kind == A_ANGLE and len(self.pts) >= 3:
            # One handle per corner: an angle is adjusted by moving an arm, and the
            # middle point is the vertex the number is read at.
            return {"a0": tuple(self.pts[0]), "a1": tuple(self.pts[1]),
                    "a2": tuple(self.pts[2]), "move": (cx, cy)}
        return {"move": (cx, cy)}

    def set_handle(self, name: str, x: float, y: float, square: bool = False):
        if self.kind in _SEG_KINDS:
            if name == "p0":
                self.pts[0] = [x, y]
            elif name == "p1":
                self.pts[1] = [x, y]
            return
        if self.kind == A_ANGLE:
            idx = {"a0": 0, "a1": 1, "a2": 2}.get(name)
            if idx is not None and idx < len(self.pts):
                self.pts[idx] = [x, y]
            return
        if self.kind == A_SCALEBAR:
            return                     # its length comes from the scale, not the mouse
        x0, y0, x1, y1 = self.bbox()
        if "n" in name:
            y0 = y
        if "s" in name:
            y1 = y
        if "w" in name:
            x0 = x
        if "e" in name:
            x1 = x
        if square:
            side = max(abs(x1 - x0), abs(y1 - y0))
            if "w" in name:
                x0 = x1 - side
            else:
                x1 = x0 + side
            if "n" in name:
                y0 = y1 - side
            else:
                y1 = y0 + side
        self.pts = [[min(x0, x1), min(y0, y1)], [max(x0, x1), max(y0, y1)]]

    def hit(self, x: float, y: float, tol: float) -> bool:
        x0, y0, x1, y1 = self.bbox()
        if self.kind in _SEG_KINDS and len(self.pts) >= 2:
            return _dist_to_segment(x, y, self.pts[0], self.pts[1]) <= tol
        if self.kind in (A_FREE, A_POLY, A_ANGLE):
            for a, b in zip(self.pts, self.pts[1:]):
                if _dist_to_segment(x, y, a, b) <= tol:
                    return True
            return False
        if self.kind == A_CROSS:
            return abs(x - x0) <= tol * 3 and abs(y - y0) <= tol * 3
        return (x0 - tol) <= x <= (x1 + tol) and (y0 - tol) <= y <= (y1 + tol)


def _nice_length(value: float) -> float:
    """The round number nearest below `value`: 1, 2 or 5 times a power of ten.

    A scale bar reading "4.7 mm" makes the reader do arithmetic; one reading "5 mm"
    does not."""
    if value <= 0:
        return 1.0
    exp = math.floor(math.log10(value))
    base = 10.0 ** exp
    for step in (5.0, 2.0, 1.0):
        if value >= step * base:
            return step * base
    return base


def _fmt_mm(mm: float) -> str:
    """A length in the unit that keeps it readable: 0.05 mm reads as 50 µm, 12 mm as
    12 mm. Trailing zeros are dropped so a round number looks round."""
    if mm <= 0:
        return "0"
    if mm < 1.0:
        txt = f"{mm * 1000.0:.4g}"
        return f"{txt} µm"
    txt = f"{mm:.4g}"
    return f"{txt} mm"


def _region_caption(a: "_Annot", st: dict, slot, unit: str) -> str:
    """Everything a region has to say, on three short lines.

    The tool's own tooltip promises "min, max, mean and more for that area"; a caption
    showing two of those makes the panel a liar and sends the reader looking for the
    rest. Three lines: what the values are, how big the area is, and how much of it
    there is."""
    if not st:
        return ""
    lines = [f"{unit}:  min {st['min']:.0f}   max {st['max']:.0f}   "
             f"mean {st['mean']:.1f}   sd {st['std']:.1f}"]
    x0, y0, x1, y1 = a.bbox()
    w, h = max(0.0, x1 - x0), max(0.0, y1 - y0)
    ppm = slot.px_per_mm
    size = f"{w:.0f} × {h:.0f} px"
    if ppm:
        size += f"  =  {_fmt_mm(w / ppm)} × {_fmt_mm(h / ppm)}"
    lines.append(size)
    tail = f"{st['count']} px"
    if ppm:
        tail += f"   {st['count'] / (ppm ** 2):.4g} mm²"
    tail += f"   sum {st['sum']:.4g}"
    if "cx" in st:
        tail += f"   centre {st['cx']:.0f}, {st['cy']:.0f}"
    lines.append(tail)
    return "\n".join(lines)


def angle_between(p0, vertex, p2) -> float:
    """The angle at `vertex` between the arms to p0 and p2, in degrees, 0…180.

    Always the inner angle: a protractor reading of 250° is the same corner measured
    the long way round, and nobody means that."""
    ax, ay = p0[0] - vertex[0], p0[1] - vertex[1]
    bx, by = p2[0] - vertex[0], p2[1] - vertex[1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cos))


def line_angle_deg(p0, p1) -> float:
    """Direction of a line as it looks on screen: degrees anticlockwise from
    horizontal. Image y grows downwards, so the sign is flipped on the way out."""
    return math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))


#  Directions Shift snaps a hand-drawn line onto: the picture's own axes and their
#  diagonals. This is a stand-in for what is actually wanted — a line PARALLEL TO THE
#  BEAM, since the patch in the frame is hardly ever square to the sensor. Doing that
#  properly means finding the beam's edge first (the long axis from the second moments
#  is already computed for the beam report, and the edges of a top-hat could be fitted)
#  and offering those angles here as well. Until then, straight to the frame at least
#  stops a ruler drifting a degree or two and reading long.
_SNAP_STEP_DEG = 45.0


def snap_direction(p0, x: float, y: float, step_deg: float = _SNAP_STEP_DEG):
    """(x, y) pulled onto the nearest direction that is a multiple of `step_deg`.

    The length is kept — only the angle is rounded — so the operator still controls how
    long the line is while Shift controls where it points."""
    dx, dy = float(x) - p0[0], float(y) - p0[1]
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return float(x), float(y)
    step = math.radians(step_deg)
    ang = round(math.atan2(dy, dx) / step) * step
    return p0[0] + length * math.cos(ang), p0[1] + length * math.sin(ang)


def _dist_to_segment(px: float, py: float, a, b) -> float:
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ─────────────────────────────────────────────────────────────────
#  Region statistics
# ─────────────────────────────────────────────────────────────────

def _region_mask(annot: _Annot, shape) -> "np.ndarray | None":
    """Boolean mask for a measuring region, or None when it is empty."""
    h, w = shape[:2]
    x0, y0, x1, y1 = annot.bbox()
    ix0, iy0 = int(max(0, math.floor(x0))), int(max(0, math.floor(y0)))
    ix1, iy1 = int(min(w, math.ceil(x1) + 1)), int(min(h, math.ceil(y1) + 1))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    mask = np.zeros((h, w), dtype=bool)
    if annot.kind == A_ROI_ELLIPSE:
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        rx, ry = max(0.5, (x1 - x0) / 2.0), max(0.5, (y1 - y0) / 2.0)
        yy, xx = np.ogrid[iy0:iy1, ix0:ix1]
        inside = (((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) <= 1.0
        mask[iy0:iy1, ix0:ix1] = inside
    else:
        mask[iy0:iy1, ix0:ix1] = True
    return mask if mask.any() else None


def region_stats(arr: np.ndarray, mask: "np.ndarray | None") -> dict:
    """min / max / mean / std / sum / count / centroid over a region.

    Always measured on a single channel: RGB sources are reduced to luma so one number
    means one thing. The caller decides whether `arr` is raw counts or display codes."""
    data = _to_gray(arr) if arr.ndim == 3 else arr
    if mask is not None:
        vals = data[mask].astype(np.float64)
    else:
        vals = data.astype(np.float64).ravel()
    if vals.size == 0:
        return {}
    out = {"min": float(vals.min()), "max": float(vals.max()),
           "mean": float(vals.mean()), "std": float(vals.std()),
           "sum": float(vals.sum()), "count": int(vals.size)}
    # Intensity-weighted centroid, from row and column moments. Listing the coordinates
    # of every selected pixel instead costs three full-frame arrays per call, and this
    # runs on every drag of a region.
    try:
        d = data.astype(np.float64) if mask is None else np.where(mask, data, 0.0)
        tot = float(d.sum())
        if tot > 0:
            h, w = d.shape[:2]
            out["cx"] = float((d.sum(axis=0) * np.arange(w)).sum() / tot)
            out["cy"] = float((d.sum(axis=1) * np.arange(h)).sum() / tot)
    except Exception:
        pass
    return out


def line_profile(arr: np.ndarray, p0, p1, width: int = 1) -> "tuple[np.ndarray, np.ndarray]":
    """Values sampled along a line, one sample per pixel of length.

    `width` averages that many pixels ACROSS the line, the way ImageJ's line width
    does. On a noisy frame a one-pixel-wide profile is mostly noise; averaging 5 or 9
    rows perpendicular to the line cuts that down by the square root of the count
    without moving the peak, so an FWHM read off it is far steadier.

    Returns (distance in pixels, value)."""
    data = _to_gray(arr) if arr.ndim == 3 else arr
    h, w = data.shape[:2]
    n = max(2, int(round(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))) + 1)
    xs = np.linspace(p0[0], p1[0], n)
    ys = np.linspace(p0[1], p1[1], n)
    dist = np.hypot(xs - p0[0], ys - p0[1])

    width = max(1, int(width))
    if width == 1:
        vals = data[np.clip(np.rint(ys), 0, h - 1).astype(int),
                    np.clip(np.rint(xs), 0, w - 1).astype(int)].astype(np.float64)
        return dist, vals

    # Unit vector across the line, and an odd number of offsets centred on it so the
    # middle sample is still the line itself.
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln, dx / ln
    offsets = np.arange(width, dtype=np.float64) - (width - 1) / 2.0
    acc = np.zeros(n, dtype=np.float64)
    for off in offsets:
        yy = np.clip(np.rint(ys + ny * off), 0, h - 1).astype(int)
        xx = np.clip(np.rint(xs + nx * off), 0, w - 1).astype(int)
        acc += data[yy, xx].astype(np.float64)
    return dist, acc / len(offsets)


# ─────────────────────────────────────────────────────────────────
#  Slot — one image in the Workshop, with its edit history
# ─────────────────────────────────────────────────────────────────

_SLOT_UNDO_LIMIT = 30

#  Pixel pitch of the Basler cameras on this beamline: 4.4 µm, the same sensor in all
#  of them (acA1600-20gm). Read out of the archived frames themselves, where NI Vision
#  writes it as "Unit length" and again as "Pixel Width (um)" in the Measurements
#  block; the older MATLAB analysis scripts say 4.5, which is 2.3 % out.
#
#  It is offered as the default sensor scale so a frame can be measured in millimetres
#  without a ruler, but it is only true AT THE SENSOR — put a lens or a magnifier in
#  front and the number on the object is different, which is why it stays an option the
#  operator switches on rather than something applied by itself.
#
#  It also assumes one stored pixel is one sensor pixel. Some cameras acquire with 2×2
#  binning (OM1NF does, OM1FF does not), and there one stored pixel covers 8.8 µm — so
#  a binned frame measured at 4.4 µm comes out half its real size on the sensor. That
#  is not read from the frame yet; type the doubled value in until it is.
BASLER_PIXEL_UM = 4.4

#  What a measured value is called on screen. "Counts" is the instrument word for what
#  a camera pixel returns; the people reading this panel call it pixel intensity, so
#  that is what it says. The distinction the code lives by — the camera's own values
#  versus the 8-bit picture — is kept, it is just spelled out instead of hidden in a
#  word only half the readers know.
UNIT_RAW = "pixel intensity"
UNIT_CODE = "8-bit intensity"


@dataclass
class _WorkshopSlot:
    label: str = ""
    base: np.ndarray = field(default_factory=lambda: np.zeros((1, 1, 3), np.uint8))
    source_base: np.ndarray = field(default_factory=lambda: np.zeros((1, 1, 3), np.uint8))
    source_path: "Path | None" = None

    # Native-precision counts read back from the source file — measurement only. Kept
    # geometrically in step with `base`; dropped (with a note) if that ever fails.
    raw: "np.ndarray | None" = None
    source_raw: "np.ndarray | None" = None
    full_scale: "float | None" = None
    camera: str = ""
    raw_note: str = ""

    view: _ViewSettings = field(default_factory=_ViewSettings)
    annots: list = field(default_factory=list)
    px_per_mm: "float | None" = None
    # How much of the object one pixel covers, in micrometres — the number the operator
    # actually sets. Kept beside px_per_mm rather than replacing it so everything
    # downstream keeps reading one number; this one is what the panel shows and what
    # follows the picture through binning and resizing.
    px_um: "float | None" = None
    px_um_measured: bool = False               # True when a ruler produced it

    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    # Per-slot view position, so switching images and switching back keeps the zoom.
    zoom: float = 1.0
    offset: "tuple[float, float]" = (0.0, 0.0)
    fitted: bool = False

    def _state(self):
        # base / raw are never modified in place — every edit builds a new array — so
        # the history can share references instead of copying megabytes per step.
        #
        # full_scale belongs in here even though it is one number: binning with "Sum"
        # multiplies it, and an undo that put the pixels back but left the scale
        # four times too big made every "% of full scale" and the histogram axis wrong,
        # with nothing on screen to show why.
        return (self.base, self.raw, [a.copy() for a in self.annots], self.px_per_mm,
                self.full_scale, self.px_um, self.px_um_measured)

    def _restore(self, st):
        self.base, self.raw, self.annots, self.px_per_mm = st[0], st[1], st[2], st[3]
        if len(st) > 4:
            self.full_scale = st[4]
        if len(st) > 5:
            self.px_um = st[5]
        if len(st) > 6:
            self.px_um_measured = st[6]

    def push_undo(self):
        self.undo_stack.append(self._state())
        if len(self.undo_stack) > _SLOT_UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self._state())
        self._restore(self.undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self._state())
        self._restore(self.redo_stack.pop())
        return True

    def reset_to_source(self):
        self.push_undo()
        self.base = self.source_base
        self.raw = self.source_raw
        self.annots = []

    # ---- measurement source -------------------------------------------------
    def measure_arr(self) -> np.ndarray:
        """The array measurements run on: native counts when they are available and
        still match the picture, the 8-bit display data otherwise."""
        if self.raw is not None and self.raw.shape[:2] == self.base.shape[:2]:
            return self.raw
        return self.base

    def measures_raw(self) -> bool:
        return self.raw is not None and self.raw.shape[:2] == self.base.shape[:2]

    def unit_name(self) -> str:
        return UNIT_RAW if self.measures_raw() else UNIT_CODE

    def unit_key(self) -> str:
        """The same thing without spaces, for a CSV column heading."""
        return "intensity" if self.measures_raw() else "intensity_8bit"

    def value_at(self, x: int, y: int) -> "tuple[float, float] | None":
        """(display code, measured value) at an image pixel, or None if out of range."""
        h, w = self.base.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            return None
        g = self.base[y, x]
        code = float(np.mean(g)) if np.ndim(g) else float(g)
        m = self.measure_arr()
        v = m[y, x]
        return code, (float(np.mean(v)) if np.ndim(v) else float(v))


# ─────────────────────────────────────────────────────────────────
#  Background reader for the native-precision data
# ─────────────────────────────────────────────────────────────────

class _RawSignals(QObject):
    done = Signal(object, object, object, str, str)   # slot, raw, full_scale, camera, note


class _RawLoadTask(QRunnable):
    """Re-open the source file to get the counts behind the 8-bit picture.

    The senders hand over an already-rendered 8-bit frame, so statistics taken from it
    would be statistics of a display mapping, not of the measurement. Reading the file
    again is the cheapest way to get the real numbers without changing the hand-off
    contract every other tab depends on."""

    def __init__(self, slot: _WorkshopSlot, signals: _RawSignals):
        super().__init__()
        self._slot = slot
        self._sig = signals

    def run(self):
        slot = self._slot
        try:
            p = slot.source_path
            if p is None or not Path(p).exists():
                self._sig.done.emit(slot, None, None, "", "no source file — values are display codes")
                return
            with _PilImg.open(str(p)) as im:
                mode = im.mode
                info = dict(getattr(im, "info", {}) or {})
                arr = np.array(im)
            if arr.ndim != 2 or arr.dtype == np.uint8:
                self._sig.done.emit(slot, None, None, "",
                                    "source is 8-bit — values are display codes")
                return
            full_scale = None
            camera = ""
            note = ""
            if img_scale is not None:
                try:
                    camera = img_scale.camera_from_path(str(p)) or ""
                    fb = img_scale.bits_from_max_value(
                        img_scale.max_value_from_info(info or {}))
                    rb = img_scale.reference_bits(camera or None, fb)
                    # Undo the archiver's stretch: what is in the file is the frame's
                    # bracket blown up to 16 bits, not what the sensor counted.
                    arr, full_scale = img_scale.measure_counts(arr, fb, rb)
                    full_scale = float(full_scale)
                    if not fb:
                        note = ("no bracket in the file — values are the stored "
                                "16-bit numbers")
                except Exception:
                    full_scale = None
            self._sig.done.emit(slot, arr, full_scale, camera, note)
        except Exception as e:
            self._sig.done.emit(slot, None, None, "", f"could not read source file ({e})")


class _WriteSignals(QObject):
    done = Signal(str, str)                            # message, error ("" = fine)


class _WriteTask(QRunnable):
    """Any job that writes files, run off the GUI thread.

    Encoding a dozen full-size frames takes seconds, and doing it in the event handler
    froze the whole window while it happened — with no way to tell whether the program
    had crashed. The callable must already hold plain arrays and paths: nothing Qt owns
    may be touched from here."""

    def __init__(self, fn, signals: _WriteSignals):
        super().__init__()
        self._fn = fn
        self._sig = signals

    def run(self):
        try:
            message, error = self._fn()
        except Exception as e:
            message, error = "", f"{e}"
        self._sig.done.emit(message or "", error or "")


def _view_from_dict(d) -> "_ViewSettings":
    """Display settings out of a session file, ignoring anything it does not recognise
    — an older or newer session must not stop the rest of the file from loading."""
    view = _ViewSettings()
    if not isinstance(d, dict):
        return view
    for key, value in d.items():
        if not hasattr(view, key):
            continue
        try:
            current = getattr(view, key)
            setattr(view, key, type(current)(value) if current is not None else value)
        except Exception:
            pass
    if view.palette not in GRADIENTS:
        view.palette = PALETTE_DEFAULT
    return view


# ─────────────────────────────────────────────────────────────────
#  Canvas
# ─────────────────────────────────────────────────────────────────

TOOL_PAN, TOOL_ZOOM, TOOL_SELECT = "pan", "zoom", "select"
TOOL_BRUSH, TOOL_LINE, TOOL_ARROW = "brush", "line", "arrow"
TOOL_RECT, TOOL_ELLIPSE, TOOL_POLY, TOOL_TEXT = "rect", "ellipse", "poly", "text"
TOOL_CROP, TOOL_EYEDROP, TOOL_ERASER = "crop", "eyedrop", "eraser"
TOOL_RULER, TOOL_ROI_RECT, TOOL_ROI_ELLIPSE = "ruler", "roi_rect", "roi_ellipse"
TOOL_PROFILE, TOOL_CROSS, TOOL_ANGLE = "profile", "cross", "angle"

#  tool → the annotation it creates
_TOOL_ANNOT = {
    TOOL_BRUSH: A_FREE, TOOL_LINE: A_LINE, TOOL_ARROW: A_ARROW, TOOL_RECT: A_RECT,
    TOOL_ELLIPSE: A_ELLIPSE, TOOL_POLY: A_POLY, TOOL_TEXT: A_TEXT,
    TOOL_RULER: A_RULER, TOOL_ROI_RECT: A_ROI_RECT,
    TOOL_ROI_ELLIPSE: A_ROI_ELLIPSE, TOOL_PROFILE: A_PROFILE, TOOL_CROSS: A_CROSS,
    TOOL_ANGLE: A_ANGLE,
}

#  A single letter picks a tool, the way ImageJ and the drawing programs do. These
#  live in the canvas keyPressEvent rather than in a window shortcut, so that typing
#  into a text annotation keeps every letter for itself.
_TOOL_KEYS = {
    "H": TOOL_PAN, "Z": TOOL_ZOOM, "S": TOOL_SELECT,
    "B": TOOL_BRUSH, "L": TOOL_LINE, "A": TOOL_ARROW,
    "R": TOOL_RECT, "E": TOOL_ELLIPSE, "P": TOOL_POLY, "T": TOOL_TEXT,
    "U": TOOL_RULER, "Shift+R": TOOL_ROI_RECT, "Shift+E": TOOL_ROI_ELLIPSE,
    "Shift+P": TOOL_PROFILE, "M": TOOL_CROSS, "G": TOOL_ANGLE,
    "C": TOOL_CROP, "I": TOOL_EYEDROP, "D": TOOL_ERASER,
}

_ZOOM_MIN, _ZOOM_MAX = 0.02, 64.0
_DRAG_DEAD_PX = 6          # below this a press is a click, not a drag
_HANDLE_PX = 5             # half-size of a resize handle, in screen pixels
#  Above this magnification a pixel is big enough to write its value inside.
_PIXEL_VALUE_ZOOM = 24.0


class WorkshopCanvas(QWidget):
    """The image area: shows one slot, magnifies, pans, draws and measures.

    Painting is a plain QPainter over a QWidget (the same approach as the Image
    Slider's ImageView) — annotations are geometry, so they are re-drawn at the current
    zoom on every paint and stay sharp instead of being scaled bitmaps."""

    image_changed  = Signal()          # pixels changed (undoable edit)
    annots_changed = Signal()          # annotation list or geometry changed
    color_picked   = Signal(QColor)
    cursor_moved   = Signal(float, float)   # image coordinates, (-1, -1) when outside
    selection_changed = Signal()        # a different shape (or none) is now selected
    zoom_changed   = Signal(float)
    status         = Signal(str)
    profile_requested = Signal(object)      # _Annot of kind A_PROFILE
    tool_requested = Signal(str)            # a letter key asked for another tool

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

        self._slot: "_WorkshopSlot | None" = None
        self._qimage: "QImage | None" = None
        self._render_key = None
        self._compare: "QImage | None" = None

        self._zoom = 1.0
        self._offset = QPointF(0, 0)
        self._pan_active = False
        self._pan_last: "QPointF | None" = None
        self._space_down = False

        self.tool = TOOL_PAN
        self.draw_color = QColor(255, 0, 0)
        self.line_width = 2
        self.brush_size = 6
        self.font_size = 16
        self.fill_shapes = False
        #  Off: a new measuring region replaces the old one, so the numbers under the
        #  panel can only ever belong to the one region on screen. On: regions pile up
        #  and the results table is the place to read them.
        self.keep_regions = False

        self._new: "_Annot | None" = None
        self._press_pos: "QPointF | None" = None
        self._dragged = False
        self._rb_start: "QPointF | None" = None    # widget coords (zoom band / crop)
        self._rb_end: "QPointF | None" = None
        self._sel = -1
        self._drag_handle = ""
        self._drag_last: "QPointF | None" = None
        self._poly: "_Annot | None" = None
        self._angle: "_Annot | None" = None      # protractor being clicked out
        self._text_active = False
        self._hover_img: "QPointF | None" = None
        self._menu_request = None                # set by the tab: right-click menu

    # ── Slot binding ──────────────────────────────────────────────

    def set_slot(self, slot: "_WorkshopSlot | None"):
        self._commit_text()
        self._poly = None
        self._sel = -1
        self._slot = slot
        self._render_key = None
        self._compare = None
        if slot is None:
            self._qimage = None
            self.update()
            return
        self._ensure_image()
        if slot.fitted:
            self._zoom = slot.zoom
            self._offset = QPointF(*slot.offset)
        else:
            self.fit_to_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def refresh(self):
        """Re-render from the slot. Re-fits only when the image size changed."""
        if self._slot is None:
            self._qimage = None
            self.update()
            return
        old = (self._qimage.width(), self._qimage.height()) if self._qimage else (0, 0)
        self._render_key = None
        self._ensure_image()
        if self._qimage and (self._qimage.width(), self._qimage.height()) != old:
            self.fit_to_view()
        self.update()

    def set_compare(self, img: "QImage | None"):
        """Show a composed comparison instead of the slot. Editing is refused while
        one is shown — the picture on screen is no longer this slot's pixels."""
        was_comparing = self._compare is not None
        self._compare = img
        self._sel = -1
        if img is not None:
            self.fit_to_view()
        elif was_comparing and self._slot is not None and self._slot.fitted:
            # Give the operator back the zoom they had on this image before comparing.
            self._zoom = self._slot.zoom
            self._offset = QPointF(*self._slot.offset)
            self.zoom_changed.emit(self._zoom)
        self.update()

    def comparing(self) -> bool:
        return self._compare is not None

    def _ensure_image(self):
        slot = self._slot
        if slot is None:
            self._qimage = None
            return
        key = (id(slot.base), slot.base.shape, slot.view.key())
        if key != self._render_key or self._qimage is None:
            self._qimage = _np_to_qimage(render_view(slot.base, slot.view))
            self._render_key = key

    def display_image(self) -> "QImage | None":
        if self._compare is not None:
            return self._compare
        self._ensure_image()
        return self._qimage

    def rendered_rgb(self) -> "np.ndarray | None":
        """The displayed pixels as an RGB array — what Save and Copy write out."""
        img = self.display_image()
        return None if img is None else _qimage_to_np(img)

    # ── Coordinates ───────────────────────────────────────────────

    def _img_size(self) -> "tuple[int, int]":
        img = self.display_image()
        return (0, 0) if img is None else (img.width(), img.height())

    def _img_rect(self) -> QRectF:
        w, h = self._img_size()
        if w == 0:
            return QRectF()
        return QRectF(self._offset.x(), self._offset.y(), w * self._zoom, h * self._zoom)

    def _widget_to_img(self, pt, clamp: bool = True) -> QPointF:
        w, h = self._img_size()
        if w == 0:
            return QPointF(0, 0)
        x = (pt.x() - self._offset.x()) / self._zoom
        y = (pt.y() - self._offset.y()) / self._zoom
        if clamp:
            x = max(0.0, min(w - 1e-3, x))
            y = max(0.0, min(h - 1e-3, y))
        return QPointF(x, y)

    def _img_to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(x * self._zoom + self._offset.x(),
                       y * self._zoom + self._offset.y())

    def _inside(self, pt) -> bool:
        w, h = self._img_size()
        p = self._widget_to_img(pt, clamp=False)
        return 0 <= p.x() < w and 0 <= p.y() < h

    # ── Zoom / pan ────────────────────────────────────────────────

    def _store_view(self):
        if self._slot is not None and self._compare is None:
            self._slot.zoom = self._zoom
            self._slot.offset = (self._offset.x(), self._offset.y())
            self._slot.fitted = True

    def fit_to_view(self):
        w, h = self._img_size()
        if w == 0 or self.width() <= 0 or self.height() <= 0:
            return
        self._zoom = min(self.width() / w, self.height() / h)
        self._offset = QPointF((self.width() - w * self._zoom) / 2,
                               (self.height() - h * self._zoom) / 2)
        self._store_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom_reset(self):
        self.set_zoom_percent(100.0)

    def zoom_in(self):
        self._zoom_at(QPointF(self.rect().center()), 1.25)

    def zoom_out(self):
        self._zoom_at(QPointF(self.rect().center()), 0.8)

    def _zoom_at(self, center: QPointF, factor: float):
        """Magnify around a fixed point: whatever is under `center` stays under it."""
        w, h = self._img_size()
        if w == 0:
            return
        old = self._zoom
        self._zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, self._zoom * factor))
        if self._zoom == old:
            return
        cx, cy = center.x(), center.y()
        self._offset = QPointF(cx - (cx - self._offset.x()) * self._zoom / old,
                               cy - (cy - self._offset.y()) * self._zoom / old)
        self._store_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def set_zoom_percent(self, pct: float):
        w, h = self._img_size()
        if w == 0:
            return
        new = max(_ZOOM_MIN, min(_ZOOM_MAX, pct / 100.0))
        centre = QPointF(self.rect().center())
        anchor = self._widget_to_img(centre, clamp=False)
        self._zoom = new
        self._offset = QPointF(centre.x() - anchor.x() * new,
                               centre.y() - anchor.y() * new)
        self._store_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom_to_rect(self, x0: float, y0: float, x1: float, y1: float):
        """Blow an image rectangle up to fill the canvas — the drag half of Magnify."""
        w, h = self._img_size()
        if w == 0:
            return
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if rw < 1 or rh < 1 or self.width() <= 0 or self.height() <= 0:
            return
        self._zoom = max(_ZOOM_MIN, min(_ZOOM_MAX,
                                        min(self.width() / rw, self.height() / rh)))
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self._offset = QPointF(self.width() / 2 - cx * self._zoom,
                               self.height() / 2 - cy * self._zoom)
        self._store_view()
        self.update()
        self.zoom_changed.emit(self._zoom)

    def zoom_percent(self) -> float:
        return self._zoom * 100.0

    def wheelEvent(self, e):
        # The wheel magnifies under the cursor in EVERY tool: it is the one gesture that
        # should never depend on which mode happens to be active.
        self._zoom_at(QPointF(e.position()), 1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.display_image() is None:
            return
        if self._slot is not None and not self._slot.fitted:
            self.fit_to_view()
            return
        old, new = e.oldSize(), e.size()
        if old.width() > 0 and old.height() > 0:
            # Keep the image point that was in the middle of the canvas in the middle —
            # resizing the window must never throw away a zoom the operator just set.
            self._offset += QPointF((new.width() - old.width()) / 2.0,
                                    (new.height() - old.height()) / 2.0)
            self._store_view()
        self.update()


    # ── Annotation helpers ────────────────────────────────────────

    def _annots(self) -> list:
        return self._slot.annots if self._slot is not None else []

    def selected(self) -> "_Annot | None":
        a = self._annots()
        return a[self._sel] if 0 <= self._sel < len(a) else None

    def select_index(self, idx: int):
        self._set_sel(idx if 0 <= idx < len(self._annots()) else -1)
        self.update()

    def _set_sel(self, idx: int):
        """Selection is what the Measure panel reads, so every change has to announce
        itself — otherwise the panel keeps showing the region that was selected before."""
        if idx == self._sel:
            return
        self._sel = idx
        self.selection_changed.emit()

    def _tol_img(self) -> float:
        return max(1.0, 6.0 / max(self._zoom, 1e-6))

    def _annot_at(self, p: QPointF) -> int:
        tol = self._tol_img()
        for i in range(len(self._annots()) - 1, -1, -1):
            if self._annots()[i].hit(p.x(), p.y(), tol):
                return i
        return -1

    def _handle_at(self, a: "_Annot", pos: QPointF) -> str:
        for name, (hx, hy) in a.handles().items():
            if name == "move":
                continue
            w = self._img_to_widget(hx, hy)
            if abs(w.x() - pos.x()) <= _HANDLE_PX + 3 and abs(w.y() - pos.y()) <= _HANDLE_PX + 3:
                return name
        return ""

    def add_annot(self, a: "_Annot"):
        if self._slot is None:
            return
        self._slot.push_undo()
        if a.kind in _REGION_KINDS and not self.keep_regions:
            # A region is what the Measure panel reads, and it reads exactly one. Two
            # of them on screen with one set of numbers under them is a trap, so a new
            # region replaces the old one. Undo brings the old one back. Switch on
            # "Keep several regions" and they pile up instead — then the results table
            # is what lists them, and the panel shows whichever one is selected.
            self._slot.annots = [x for x in self._slot.annots
                                 if x.kind not in _REGION_KINDS]
        self._slot.annots.append(a)
        self._sel = len(self._slot.annots) - 1
        self.update_labels()
        self.annots_changed.emit()
        self.update()

    def delete_selected(self):
        a = self.selected()
        if a is None or self._slot is None:
            return
        self._slot.push_undo()
        self._slot.annots.pop(self._sel)
        self._sel = -1
        self.annots_changed.emit()
        self.update()

    def clear_annots(self):
        if self._slot is None or not self._slot.annots:
            return
        self._slot.push_undo()
        self._slot.annots = []
        self._sel = -1
        self.annots_changed.emit()
        self.update()

    def update_labels(self):
        """Fill in the caption each measuring shape carries on the picture."""
        slot = self._slot
        if slot is None:
            return
        arr = slot.measure_arr()
        unit = slot.unit_name()
        for a in slot.annots:
            if a.kind not in MEASURE_KINDS:
                continue
            try:
                if a.kind == A_RULER and len(a.pts) >= 2:
                    # Both numbers, always: the pixel count is what the picture is made
                    # of, the real size is what the operator is asking about, and having
                    # to switch between them is how the wrong one gets written down.
                    dpx = math.hypot(a.pts[1][0] - a.pts[0][0], a.pts[1][1] - a.pts[0][1])
                    a.label = (f"{dpx:.1f} px" if not slot.px_per_mm
                               else f"{dpx:.1f} px  =  {_fmt_mm(dpx / slot.px_per_mm)}")
                elif a.kind == A_ANGLE and len(a.pts) >= 3:
                    a.label = f"{angle_between(a.pts[0], a.pts[1], a.pts[2]):.1f}°"
                elif a.kind == A_SCALEBAR and len(a.pts) >= 2:
                    # The bar is redrawn from the scale every time, so changing the
                    # scale moves the bar instead of leaving a bar that lies.
                    if a.value > 0 and slot.px_per_mm:
                        a.pts[1] = [a.pts[0][0] + a.value * slot.px_per_mm,
                                    a.pts[0][1]]
                        a.label = _fmt_mm(a.value)
                    else:
                        a.label = f"{abs(a.pts[1][0] - a.pts[0][0]):.0f} px"
                elif a.kind == A_PROFILE:
                    a.label = "profile — double-click to plot"
                elif a.kind == A_CROSS and a.pts:
                    x, y = int(round(a.pts[0][0])), int(round(a.pts[0][1]))
                    v = slot.value_at(x, y)
                    a.label = f"({x}, {y})  intensity {v[1]:.0f}" if v else ""
                else:
                    st = region_stats(arr, _region_mask(a, arr.shape))
                    a.label = _region_caption(a, st, slot, unit)
            except Exception:
                a.label = ""

    # ── Mouse ─────────────────────────────────────────────────────

    def _start_pan(self, pos: QPointF):
        self._pan_active = True
        self._pan_last = pos
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mousePressEvent(self, e):
        if self.display_image() is None:
            return
        pos = QPointF(e.position())
        btn, mods = e.button(), e.modifiers()
        self.setFocus()

        pan_gesture = (btn == Qt.MouseButton.MiddleButton or
                       (btn == Qt.MouseButton.LeftButton and
                        (mods & Qt.KeyboardModifier.ControlModifier or self._space_down
                         or self.tool == TOOL_PAN)))
        if pan_gesture:
            self._start_pan(pos)
            return

        if self.tool == TOOL_ZOOM:
            if btn == Qt.MouseButton.LeftButton:
                self._press_pos = pos
                self._dragged = False
                self._rb_start = pos
                self._rb_end = pos
            elif btn == Qt.MouseButton.RightButton:
                self._zoom_at(pos, 0.5)
            return

        if btn == Qt.MouseButton.RightButton:
            if self._poly is not None:
                self._finish_poly()
            elif self._angle is not None:
                self._angle = None
                self.update()
            return
        if btn != Qt.MouseButton.LeftButton:
            return

        if self._compare is not None:
            self.status.emit("Comparison is a view only — switch it off to edit.")
            return
        if self._slot is None:
            return

        p = self._widget_to_img(pos)
        if self._text_active and self.tool != TOOL_TEXT:
            self._commit_text()

        if self.tool == TOOL_EYEDROP:
            self._pick_color(p)
            return
        if self.tool == TOOL_CROP:
            self._rb_start, self._rb_end = pos, pos
            self._dragged = False
            return
        if self.tool == TOOL_ERASER:
            idx = self._annot_at(p)
            if idx >= 0:
                self._set_sel(idx)
                self.delete_selected()
            return
        if self.tool == TOOL_SELECT:
            a = self.selected()
            if a is not None:
                h = self._handle_at(a, pos)
                if h:
                    self._slot.push_undo()
                    self._drag_handle = h
                    self._drag_last = p
                    return
            idx = self._annot_at(p)
            self._set_sel(idx)
            if idx >= 0:
                self._slot.push_undo()
                self._drag_handle = "move"
                self._drag_last = p
            self.update()
            return
        if self.tool == TOOL_TEXT:
            if self._text_active:
                self._commit_text()
            a = _Annot(A_TEXT, [[p.x(), p.y()]], self.draw_color.name(),
                       self.line_width, False, "", self.font_size)
            self.add_annot(a)
            self._text_active = True
            return
        if self.tool == TOOL_POLY:
            if self._poly is None:
                self._poly = _Annot(A_POLY, [[p.x(), p.y()]], self.draw_color.name(),
                                    self.line_width, self.fill_shapes)
            self._poly.pts.append([p.x(), p.y()])
            self.update()
            return
        if self.tool == TOOL_ANGLE:
            # Three clicks: one arm end, the corner, the other arm end. The point under
            # the cursor is kept at the end of the list while it is being placed, which
            # is what makes the shape follow the mouse between clicks.
            if self._angle is None:
                self._angle = _Annot(A_ANGLE, [[p.x(), p.y()], [p.x(), p.y()]],
                                     self.draw_color.name(), self.line_width)
            elif len(self._angle.pts) < 3:
                self._angle.pts.append([p.x(), p.y()])
            else:
                a, self._angle = self._angle, None
                a.pts[2] = [p.x(), p.y()]
                self.add_annot(a)
            self.update()
            return
        if self.tool == TOOL_CROSS:
            self.add_annot(_Annot(A_CROSS, [[p.x(), p.y()]], self.draw_color.name(),
                                  self.line_width))
            return

        kind = _TOOL_ANNOT.get(self.tool)
        if kind is None:
            return
        width = self.brush_size if kind == A_FREE else self.line_width
        self._new = _Annot(kind, [[p.x(), p.y()], [p.x(), p.y()]],
                           self.draw_color.name(), width,
                           self.fill_shapes and kind in (A_RECT, A_ELLIPSE))
        self._dragged = False
        self.update()

    def mouseMoveEvent(self, e):
        pos = QPointF(e.position())
        if self._pan_active and self._pan_last is not None:
            self._offset += pos - self._pan_last
            self._pan_last = pos
            self._store_view()
            self.update()
            return

        if self.display_image() is not None:
            if self._inside(pos):
                p = self._widget_to_img(pos)
                self._hover_img = p
                self.cursor_moved.emit(p.x(), p.y())
            else:
                self._hover_img = None
                self.cursor_moved.emit(-1.0, -1.0)

        left = bool(e.buttons() & Qt.MouseButton.LeftButton)
        if self._rb_start is not None and left:
            self._rb_end = pos
            if (abs(pos.x() - self._rb_start.x()) > _DRAG_DEAD_PX or
                    abs(pos.y() - self._rb_start.y()) > _DRAG_DEAD_PX):
                self._dragged = True
            self.update()
            return

        if self._drag_handle and left and self._slot is not None:
            a = self.selected()
            if a is not None:
                p = self._widget_to_img(pos)
                shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if self._drag_handle == "move":
                    a.translate(p.x() - self._drag_last.x(), p.y() - self._drag_last.y())
                    self._drag_last = p
                else:
                    if shift and a.kind in _SEG_KINDS and \
                            self._drag_handle in ("p0", "p1") and len(a.pts) >= 2:
                        # Straighten against the END THAT IS STAYING PUT, so an existing
                        # ruler can be squared up without redrawing it.
                        anchor = a.pts[1] if self._drag_handle == "p0" else a.pts[0]
                        sx, sy = snap_direction(anchor, p.x(), p.y())
                        p = QPointF(sx, sy)
                    a.set_handle(self._drag_handle, p.x(), p.y(), square=shift)
                self.update_labels()
                self.update()
            return

        if self._new is not None and left:
            p = self._widget_to_img(pos)
            if self._new.kind == A_FREE:
                self._new.pts.append([p.x(), p.y()])
            else:
                shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                if shift and self._new.kind in _BOX_KINDS:
                    x0, y0 = self._new.pts[0]
                    side = max(abs(p.x() - x0), abs(p.y() - y0))
                    p = QPointF(x0 + math.copysign(side, p.x() - x0),
                                y0 + math.copysign(side, p.y() - y0))
                elif shift and self._new.kind in _SEG_KINDS:
                    # Ruler, line, arrow and profile line all straighten under Shift.
                    p = QPointF(*snap_direction(self._new.pts[0], p.x(), p.y()))
                self._new.pts[1] = [p.x(), p.y()]
            self._dragged = True
            self.update()
            return

        if self._poly is not None:
            p = self._widget_to_img(pos)
            self._poly.pts[-1] = [p.x(), p.y()]
            self.update()
            return

        if self._angle is not None:
            p = self._widget_to_img(pos)
            self._angle.pts[-1] = [p.x(), p.y()]
            if len(self._angle.pts) >= 3:
                self._angle.label = f"{angle_between(*self._angle.pts[:3]):.1f}°"
            self.update()

    def mouseReleaseEvent(self, e):
        pos = QPointF(e.position())
        if self._pan_active:
            self._pan_active = False
            self._pan_last = None
            self._apply_cursor()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.tool == TOOL_ZOOM and self._rb_start is not None:
            if self._dragged and self._rb_end is not None:
                a = self._widget_to_img(self._rb_start)
                b = self._widget_to_img(self._rb_end)
                self.zoom_to_rect(a.x(), a.y(), b.x(), b.y())
            else:
                out = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
                self._zoom_at(pos, 0.5 if out else 2.0)
            self._rb_start = self._rb_end = None
            self._dragged = False
            self.update()
            return

        if self.tool == TOOL_CROP and self._rb_start is not None:
            if self._dragged and self._rb_end is not None:
                a = self._widget_to_img(self._rb_start)
                b = self._widget_to_img(self._rb_end)
                self.crop_to(a.x(), a.y(), b.x(), b.y())
            self._rb_start = self._rb_end = None
            self._dragged = False
            self.update()
            return

        if self._drag_handle:
            self._drag_handle = ""
            self._drag_last = None
            self.annots_changed.emit()
            return

        if self._new is not None:
            a, self._new = self._new, None
            keep = True
            if a.kind in _BOX_KINDS or a.kind in _SEG_KINDS:
                x0, y0, x1, y1 = a.bbox()
                keep = (abs(x1 - x0) >= 2 or abs(y1 - y0) >= 2)
            elif a.kind == A_FREE:
                keep = len(a.pts) >= 2
            if keep:
                self.add_annot(a)
            else:
                self.update()

    def mouseDoubleClickEvent(self, e):
        if self._poly is not None:
            self._finish_poly()
            return
        if self.display_image() is None or self._slot is None:
            return
        p = self._widget_to_img(QPointF(e.position()))
        idx = self._annot_at(p)
        if idx >= 0 and self._slot.annots[idx].kind == A_PROFILE:
            self._set_sel(idx)
            self.profile_requested.emit(self._slot.annots[idx])

    def _finish_poly(self):
        a, self._poly = self._poly, None
        if a is not None and len(a.pts) >= 3:
            a.pts.pop()                      # drop the point that follows the cursor
            self.add_annot(a)
        self.update()

    # ── Keyboard ──────────────────────────────────────────────────

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Space:
            self._space_down = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if self._text_active:
            a = self.selected()
            if a is not None and a.kind == A_TEXT:
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._commit_text()
                elif k == Qt.Key.Key_Escape:
                    self._slot.annots.pop(self._sel)
                    self._sel = -1
                    self._text_active = False
                    self.annots_changed.emit()
                elif k == Qt.Key.Key_Backspace:
                    a.text = a.text[:-1]
                else:
                    ch = e.text()
                    if ch and ch.isprintable():
                        a.text += ch
                self.update()
                return

        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected(); return
        if k == Qt.Key.Key_Escape:
            self._poly = None
            self._angle = None
            self._new = None
            self._set_sel(-1)
            self.update(); return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in(); return
        if k == Qt.Key.Key_Minus:
            self.zoom_out(); return
        if k == Qt.Key.Key_0:
            self.fit_to_view(); return
        if k == Qt.Key.Key_1:
            self.zoom_reset(); return

        mods = e.modifiers()
        if not (mods & (Qt.KeyboardModifier.ControlModifier
                        | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier)):
            ch = e.text().upper()
            if len(ch) == 1 and ch.isalpha():
                name = ("Shift+" if mods & Qt.KeyboardModifier.ShiftModifier else "") + ch
                tool = _TOOL_KEYS.get(name)
                if tool is not None:
                    self.tool_requested.emit(tool)
                    return

        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        dx = dy = 0
        if k == Qt.Key.Key_Left:   dx = -step
        elif k == Qt.Key.Key_Right: dx = step
        elif k == Qt.Key.Key_Up:    dy = -step
        elif k == Qt.Key.Key_Down:  dy = step
        if dx or dy:
            a = self.selected()
            if a is not None and self.tool == TOOL_SELECT:
                a.translate(dx, dy)
                self.update_labels()
                self.annots_changed.emit()
            else:
                self._offset += QPointF(-dx * 8, -dy * 8)
                self._store_view()
            self.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            self._space_down = False
            self._apply_cursor()
            return
        super().keyReleaseEvent(e)

    # ── Tool / cursor ─────────────────────────────────────────────

    def set_tool(self, tool: str):
        self._commit_text()
        if self._poly is not None:
            self._finish_poly()
        # A half-clicked protractor is thrown away rather than finished: two of the
        # three points would give an angle nobody asked for.
        self._angle = None
        self.tool = tool
        if tool != TOOL_SELECT:
            self._set_sel(-1)
        self._apply_cursor()
        self.update()

    def _apply_cursor(self):
        shapes = {
            TOOL_PAN: Qt.CursorShape.OpenHandCursor,
            TOOL_ZOOM: Qt.CursorShape.CrossCursor,
            TOOL_SELECT: Qt.CursorShape.ArrowCursor,
            TOOL_TEXT: Qt.CursorShape.IBeamCursor,
            TOOL_EYEDROP: Qt.CursorShape.PointingHandCursor,
            TOOL_ERASER: Qt.CursorShape.PointingHandCursor,
        }
        self.setCursor(QCursor(shapes.get(self.tool, Qt.CursorShape.CrossCursor)))

    def _commit_text(self):
        if not self._text_active:
            return
        self._text_active = False
        a = self.selected()
        if a is not None and a.kind == A_TEXT and not a.text.strip():
            self._slot.annots.pop(self._sel)
            self._sel = -1
        self.annots_changed.emit()
        self.update()

    def _pick_color(self, p: QPointF):
        slot = self._slot
        if slot is None:
            return
        x, y = int(p.x()), int(p.y())
        arr = slot.base
        if arr.ndim == 2:
            v = int(arr[y, x]); col = QColor(v, v, v)
        else:
            col = QColor(int(arr[y, x, 0]), int(arr[y, x, 1]), int(arr[y, x, 2]))
        self.draw_color = col
        self.color_picked.emit(col)

    # ── Edit operation used from the canvas itself ────────────────

    def crop_to(self, x0: float, y0: float, x1: float, y1: float):
        slot = self._slot
        if slot is None:
            return
        ix0, ix1 = sorted((int(round(x0)), int(round(x1))))
        iy0, iy1 = sorted((int(round(y0)), int(round(y1))))
        h, w = slot.base.shape[:2]
        ix0, iy0 = max(0, ix0), max(0, iy0)
        ix1, iy1 = min(w, ix1), min(h, iy1)
        if ix1 - ix0 < 2 or iy1 - iy0 < 2:
            return
        slot.push_undo()
        slot.base = slot.base[iy0:iy1, ix0:ix1].copy()
        if slot.raw is not None and slot.raw.shape[:2] == (h, w):
            slot.raw = slot.raw[iy0:iy1, ix0:ix1].copy()
        for a in slot.annots:
            a.translate(-ix0, -iy0)
        slot.fitted = False
        self.refresh()
        self.update_labels()
        self.image_changed.emit()
        self.status.emit(f"Cropped to {ix1 - ix0} × {iy1 - iy0} px")


    files_dropped = Signal(list)

    # ── Painting ──────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#2b2b2b"))
        img = self.display_image()
        if img is None:
            p.setPen(QColor("#bbb"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "No image.\n\nUse “➤ Workshop” in Image Finder, Image Slider or "
                       "Shot Finder,\nor drop an image file here.")
            return

        # Crisp pixels when magnified (that is the point of magnifying), smooth when
        # the frame is being shrunk to fit.
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._zoom < 1.0)
        p.drawImage(self._img_rect(), img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self._zoom >= _PIXEL_VALUE_ZOOM:
            self._paint_pixel_values(p)

        paint_annots(p, self._annots(), self._img_to_widget, self._zoom,
                     text_caret=self._sel if self._text_active else -1)
        if self._new is not None:
            paint_annots(p, [self._new], self._img_to_widget, self._zoom)
        if self._poly is not None:
            paint_annots(p, [self._poly], self._img_to_widget, self._zoom)
        if self._angle is not None:
            paint_annots(p, [self._angle], self._img_to_widget, self._zoom)

        a = self.selected()
        if a is not None and self.tool == TOOL_SELECT:
            self._paint_handles(p, a)

        if self._rb_start is not None and self._rb_end is not None and self._dragged:
            r = QRectF(self._rb_start, self._rb_end).normalized()
            colour = QColor(255, 220, 0) if self.tool == TOOL_ZOOM else QColor(255, 255, 255)
            p.setPen(QPen(colour, 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(colour.red(), colour.green(), colour.blue(), 30)))
            p.drawRect(r)

        if self._compare is not None:
            p.setPen(QColor("#ffd24a"))
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            p.drawText(8, 18, "COMPARISON VIEW — editing is off")

    def _paint_pixel_values(self, p: QPainter):
        """At high magnification write the value inside each pixel, the way ImageJ does.
        Only the cells actually on screen are considered, and only when there are few
        enough of them for the text to be readable."""
        slot = self._slot
        if slot is None or self._compare is not None:
            return
        tl = self._widget_to_img(QPointF(0, 0), clamp=False)
        br = self._widget_to_img(QPointF(self.width(), self.height()), clamp=False)
        h, w = slot.base.shape[:2]
        x0, y0 = max(0, int(tl.x())), max(0, int(tl.y()))
        x1, y1 = min(w, int(br.x()) + 1), min(h, int(br.y()) + 1)
        if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > 1200:
            return
        arr = slot.measure_arr()
        raw = slot.measures_raw()
        f = QFont("Consolas", max(6, int(self._zoom / 5)))
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 190))
        for iy in range(y0, y1):
            for ix in range(x0, x1):
                v = arr[iy, ix]
                v = float(np.mean(v)) if np.ndim(v) else float(v)
                tw = self._img_to_widget(ix, iy)
                p.drawText(QRectF(tw.x(), tw.y(), self._zoom, self._zoom),
                           Qt.AlignmentFlag.AlignCenter,
                           f"{v:.0f}" if raw else f"{int(v)}")

    def _paint_handles(self, p: QPainter, a: "_Annot"):
        p.setPen(QPen(QColor("#00d0ff"), 1))
        p.setBrush(QBrush(QColor("#ffffff")))
        for name, (hx, hy) in a.handles().items():
            w = self._img_to_widget(hx, hy)
            if name == "move":
                p.setBrush(QBrush(QColor("#00d0ff")))
                p.drawEllipse(w, _HANDLE_PX - 1, _HANDLE_PX - 1)
                p.setBrush(QBrush(QColor("#ffffff")))
            else:
                p.drawRect(QRectF(w.x() - _HANDLE_PX, w.y() - _HANDLE_PX,
                                  _HANDLE_PX * 2, _HANDLE_PX * 2))

    # ── Right-click menu ──────────────────────────────────────────

    def contextMenuEvent(self, e):
        """The common commands where the picture is, instead of across the panel.

        Right-click already means two other things — it zooms out under Magnify and it
        closes a polygon — so the menu stays out of the way in exactly those cases
        rather than taking the button off them."""
        if self._menu_request is None or self.tool == TOOL_ZOOM \
                or self._poly is not None or self._angle is not None:
            e.ignore()
            return
        p = self._widget_to_img(QPointF(e.pos()), clamp=False)
        self._menu_request(e.globalPos(), p)
        e.accept()

    # ── Drag and drop ─────────────────────────────────────────────

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


# ─────────────────────────────────────────────────────────────────
#  Annotation painting — shared by the canvas and by Save
# ─────────────────────────────────────────────────────────────────

def paint_annots(p: QPainter, annots, to_widget, scale: float, text_caret: int = -1):
    """Draw annotations through `to_widget(x, y) -> QPointF`.

    `scale` is how many output pixels one image pixel covers, so line widths and text
    grow with the zoom — what is on screen is exactly what a saved copy contains."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for i, a in enumerate(annots):
        col = QColor(a.color)
        pen = QPen(col, max(1.0, a.width * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 70))
                   if a.filled else Qt.BrushStyle.NoBrush)

        if a.kind == A_SCALEBAR and len(a.pts) >= 2:
            _draw_scale_bar(p, a, to_widget, scale, col)
            continue
        if a.kind == A_ANGLE and len(a.pts) >= 2:
            poly = QPolygonF([to_widget(x, y) for x, y in a.pts])
            p.drawPolyline(poly)
            if len(a.pts) >= 3:
                _draw_angle_arc(p, a, to_widget, scale, col)
        elif a.kind in (A_FREE, A_POLY) and len(a.pts) >= 2:
            poly = QPolygonF([to_widget(x, y) for x, y in a.pts])
            if a.kind == A_POLY and a.filled:
                p.drawPolygon(poly)
            else:
                p.drawPolyline(poly)
        elif a.kind in _SEG_KINDS and len(a.pts) >= 2:
            p0 = to_widget(*a.pts[0]); p1 = to_widget(*a.pts[1])
            if a.kind == A_PROFILE:
                dashed = QPen(pen); dashed.setStyle(Qt.PenStyle.DashLine)
                p.setPen(dashed)
            p.drawLine(p0, p1)
            if a.kind == A_ARROW:
                _draw_arrow_head(p, p0, p1, max(6.0, a.width * scale * 4))
            if a.kind == A_RULER:
                _draw_end_ticks(p, p0, p1, max(4.0, a.width * scale * 3))
            p.setPen(pen)
        elif a.kind in _BOX_KINDS and len(a.pts) >= 2:
            r = QRectF(to_widget(*a.pts[0]), to_widget(*a.pts[1])).normalized()
            if a.kind in (A_ELLIPSE, A_ROI_ELLIPSE):
                p.drawEllipse(r)
            else:
                p.drawRect(r)
        elif a.kind == A_CROSS and a.pts:
            c = to_widget(*a.pts[0])
            arm = max(6.0, 10.0 * min(scale, 3.0))
            p.drawLine(QPointF(c.x() - arm, c.y()), QPointF(c.x() + arm, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - arm), QPointF(c.x(), c.y() + arm))
        elif a.kind == A_TEXT and a.pts:
            c = to_widget(*a.pts[0])
            f = QFont("Arial", max(6, int(round(a.font_size * scale))))
            p.setFont(f)
            txt = a.text + ("|" if i == text_caret else "")
            p.drawText(c, txt)
            continue

        if a.label:
            _draw_caption(p, a, to_widget, scale, col)
    p.restore()


def _draw_arrow_head(p: QPainter, p0: QPointF, p1: QPointF, size: float):
    ang = math.atan2(p1.y() - p0.y(), p1.x() - p0.x())
    for s in (+1, -1):
        a = ang + s * math.radians(155)
        p.drawLine(p1, QPointF(p1.x() + size * math.cos(a), p1.y() + size * math.sin(a)))


def _draw_end_ticks(p: QPainter, p0: QPointF, p1: QPointF, size: float):
    ang = math.atan2(p1.y() - p0.y(), p1.x() - p0.x()) + math.pi / 2
    dx, dy = size * math.cos(ang), size * math.sin(ang)
    for e in (p0, p1):
        p.drawLine(QPointF(e.x() - dx, e.y() - dy), QPointF(e.x() + dx, e.y() + dy))


def _draw_angle_arc(p: QPainter, a: "_Annot", to_widget, scale: float, col: QColor):
    """Small arc inside the corner, so it is obvious WHICH of the two angles the
    number belongs to."""
    v = to_widget(*a.pts[1])
    w0 = to_widget(*a.pts[0])
    w2 = to_widget(*a.pts[2])
    r = max(10.0, min(34.0, 0.35 * min(math.hypot(w0.x() - v.x(), w0.y() - v.y()),
                                       math.hypot(w2.x() - v.x(), w2.y() - v.y()))))
    a0 = math.degrees(math.atan2(-(w0.y() - v.y()), w0.x() - v.x()))
    a2 = math.degrees(math.atan2(-(w2.y() - v.y()), w2.x() - v.x()))
    span = a2 - a0
    while span <= -180.0:
        span += 360.0
    while span > 180.0:
        span -= 360.0
    pen = QPen(col, max(1.0, a.width * scale * 0.7))
    pen.setStyle(Qt.PenStyle.DotLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Qt's arc angles are in sixteenths of a degree, anticlockwise from three o'clock.
    p.drawArc(QRectF(v.x() - r, v.y() - r, 2 * r, 2 * r),
              int(round(a0 * 16)), int(round(span * 16)))


def _draw_scale_bar(p: QPainter, a: "_Annot", to_widget, scale: float, col: QColor):
    """A solid bar with end ticks and its length written above it.

    Drawn on a dark plate like the measurement captions: a scale bar that lands on a
    saturated white spot and disappears is worse than none, because the reader still
    believes the picture is calibrated."""
    w0 = to_widget(*a.pts[0])
    w1 = to_widget(*a.pts[1])
    thick = max(2.0, a.width * scale * 1.6)
    x0, x1 = min(w0.x(), w1.x()), max(w0.x(), w1.x())
    y = w0.y()
    text = a.label or a.text
    f = QFont("Segoe UI", max(7, int(round(a.font_size * min(max(scale, 0.4), 1.6) * 0.62))),
              QFont.Weight.Bold)
    p.setFont(f)
    fm = QFontMetrics(f)
    tw = fm.horizontalAdvance(text) if text else 0
    th = fm.height() if text else 0
    plate = QRectF(x0 - 6, y - thick - th - 8, max(x1 - x0 + 12, tw + 12),
                   thick + th + 12)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(0, 0, 0, 150)))
    p.drawRect(plate)
    p.setPen(QPen(col, thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(x0, y), QPointF(x1, y))
    tick = max(3.0, thick * 1.8)
    p.setPen(QPen(col, max(1.0, thick * 0.5), Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.FlatCap))
    for xe in (x0, x1):
        p.drawLine(QPointF(xe, y - tick), QPointF(xe, y + tick * 0.2))
    if text:
        p.setPen(QPen(col))
        p.drawText(QRectF(x0, y - thick - th - 5, max(x1 - x0, tw), th),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, text)


def _draw_caption(p: QPainter, a: "_Annot", to_widget, scale: float, col: QColor):
    """Measurement caption, drawn on a dark plate so it stays readable over both a
    black background and a saturated spot."""
    x0, y0, _, _ = a.bbox()
    anchor = to_widget(x0, y0)
    f = QFont("Segoe UI", max(7, int(round(9 * min(max(scale, 0.6), 2.0)))))
    p.setFont(f)
    fm = QFontMetrics(f)
    # A region has more to say than fits on one line, so the plate grows with the text.
    lines = a.label.split("\n")
    w = max(fm.horizontalAdvance(t) for t in lines) + 8
    h = fm.height() * len(lines) + 4
    r = QRectF(anchor.x(), anchor.y() - h - 3, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(0, 0, 0, 165)))
    p.drawRect(r)
    p.setPen(QPen(col))
    p.drawText(r.adjusted(4, 2, -4, -2),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, a.label)


# ─────────────────────────────────────────────────────────────────
#  Panel building blocks
# ─────────────────────────────────────────────────────────────────

class _FallbackSection(QWidget):
    """Stand-in for the Image Slider's CollapsibleSection, used only when is_t.py
    cannot be loaded. Same API, plainer looks."""
    toggled = Signal(str, bool)

    def __init__(self, title: str, key: str, expanded: bool = True, parent=None,
                 accent: str = "#4a78c0"):
        super().__init__(parent)
        self._key = key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._hdr = QToolButton()
        self._hdr.setText(("▾ " if expanded else "▸ ") + title.upper())
        self._hdr.setStyleSheet(
            "QToolButton { text-align: left; border: none; border-radius: 4px;"
            f" padding: 7px 9px; margin-top: 6px; font-weight: 700; font-size: 11px;"
            f" color: #fff; background: {accent}; }}")
        self._hdr.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hdr.clicked.connect(lambda: self.set_expanded(not self.body.isVisible()))
        self._title = title
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(6, 5, 6, 6)
        self.body_layout.setSpacing(4)
        self.body.setVisible(expanded)
        lay.addWidget(self._hdr)
        lay.addWidget(self.body)

    def set_expanded(self, on: bool):
        self.body.setVisible(on)
        self._hdr.setText(("▾ " if on else "▸ ") + self._title.upper())
        self.toggled.emit(self._key, on)


def _section_cls():
    mod = _get_slider_module()
    cls = getattr(mod, "CollapsibleSection", None) if mod is not None else None
    return cls or _FallbackSection


#  Every colour here is stated explicitly. The application stylesheet is light and the
#  canvas is dark, so anything that inherits its colour ends up unreadable on one of them.
_BTN_QSS = (
    "QPushButton { padding: 4px 9px; border: 1px solid #b6b6b6; border-radius: 3px;"
    " background: #efefef; color: #111; }"
    "QPushButton:hover { background: #d9e8ff; }"
    "QPushButton:pressed { background: #b9d0f5; }"
    "QPushButton:disabled { background: #f4f4f4; color: #9a9a9a; border-color: #dcdcdc; }"
)
_DANGER_QSS = (
    "QPushButton { padding: 4px 9px; border: 1px solid #c07070; border-radius: 3px;"
    " background: #fdecea; color: #8d1c12; }"
    "QPushButton:hover { background: #f8d3ce; }"
    "QPushButton:disabled { background: #f6f0f0; color: #bb9a97; border-color: #e3d2d0; }"
)
# The tool buttons carry a drawn icon, not a character, so there is no font size
# here any more. The checked blue is #2f6fb5 rather than the old #3a7ebf: white
# on #3a7ebf is only 4.3:1, which reads as washed out, while #2f6fb5 gives 5.2:1
# and still sits in the same blue family as the border.
_TOOLBTN_QSS = (
    "QToolButton { padding: 0px; margin: 0px; border: 1px solid #b6b6b6;"
    " border-radius: 4px; background: #efefef; }"
    "QToolButton:hover { background: #dce9fb; border-color: #8fb4de; }"
    "QToolButton:checked { background: #2f6fb5; border-color: #255a94; }"
    "QToolButton:checked:hover { background: #3a7ec6; border-color: #255a94; }"
    "QToolButton:disabled { background: #f4f4f4; border-color: #dcdcdc; }"
)
_CHECK_QSS = "QCheckBox { color: #111; } QCheckBox::indicator { width: 13px; height: 13px; }"


def _btn(text: str, tip: str = "", danger: bool = False,
         icon: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(_DANGER_QSS if danger else _BTN_QSS)
    if tip:
        b.setToolTip(tip)
    if icon:
        # Red buttons get a red icon, so the mark does not sit in the row as the
        # one dark thing among red text.
        b.setIcon(action_icon(icon, "#8d1c12" if danger else "#1e2530"))
        b.setIconSize(QSize(_ICON_PX, _ICON_PX))
    return b


def _small_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #333; font-size: 11px;")
    return lbl


def _value_label(text: str, width: int = 26) -> QLabel:
    """The number that belongs to a slider. Right aligned and given a fixed width so
    the controls beside it do not shuffle sideways as the digits change."""
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #111; font-size: 11px; font-weight: 600;")
    lbl.setMinimumWidth(width)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def _size_slider(lo: int, hi: int, start: int, tip: str, width: int = 96) -> QSlider:
    sl = QSlider(Qt.Orientation.Horizontal)
    sl.setRange(lo, hi)
    sl.setValue(start)
    sl.setToolTip(tip)
    sl.setFixedWidth(width)
    return sl


#  The zoom slider is spaced by ratio, not by percent: 2 % to 6400 % is a 3200-fold
#  span, so a slider spread out evenly in percent would spend nine tenths of its
#  travel above 640 % and make every ordinary zoom one indistinguishable notch at the
#  far left. Every step here multiplies the magnification by the same small factor,
#  which is what makes dragging feel even at 5 % and at 3000 % alike.
_ZOOM_SLIDER_STEPS = 1000
_ZOOM_SLIDER_SPAN = 3200.0


def _zoom_pct_to_slider(pct: float) -> int:
    pct = max(2.0, min(6400.0, float(pct)))
    return int(round(_ZOOM_SLIDER_STEPS
                     * math.log(pct / 2.0) / math.log(_ZOOM_SLIDER_SPAN)))


def _zoom_slider_to_pct(pos: int) -> float:
    return 2.0 * (_ZOOM_SLIDER_SPAN ** (pos / _ZOOM_SLIDER_STEPS))


class _StrokePreview(QWidget):
    """The little sample beside the Line and Brush sliders. It draws a stroke in the
    colour and thickness the next drawn item will get, so the weight can be seen
    instead of guessed from a number.

    Sizes up to _TRUE_UP_TO are drawn at their real thickness — that is the range
    almost every drawing stays in, and there the sample is exact. Above it the box
    would fill up solid and 20 would look the same as 200, so the rest of the range is
    squeezed into the height that is left: a bigger number always looks thicker, but
    no longer at true size. The frame turns amber and dashed to say so, and the number
    beside the slider is the exact one either way."""

    _TRUE_UP_TO = 14.0

    def __init__(self, curved: bool, max_width: int, tip: str, parent=None):
        super().__init__(parent)
        self._curved = curved
        self._max_width = float(max(2, max_width))
        self._width = 2
        self._color = QColor(255, 0, 0)
        self.setFixedSize(64, 28)
        self.setToolTip(tip)
        self.setAccessibleName(tip)

    def set_stroke(self, width: int, color: QColor):
        w = max(1, int(width))
        col = QColor(color)
        if w == self._width and col == self._color:
            return
        self._width = w
        self._color = col
        self.update()

    def _drawn_width(self) -> "tuple[float, bool]":
        room = float(self.height() - 4)
        true_cap = min(room, self._TRUE_UP_TO)
        w = float(self._width)
        if w <= true_cap:
            return w, False
        top = max(self._max_width, true_cap + 1.0)
        frac = math.log(min(w, top) / true_cap) / math.log(top / true_cap)
        return true_cap + (room - true_cap) * frac, True

    def paintEvent(self, _e):
        w, squeezed = self._drawn_width()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        frame = QPen(QColor("#c98a1e") if squeezed else QColor("#c9ced6"))
        if squeezed:
            frame.setStyle(Qt.PenStyle.DashLine)
        p.setPen(frame)
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0),
                          3, 3)

        p.setPen(QPen(self._color, w, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x0, x1 = 8.0, self.width() - 8.0
        cy = self.height() / 2.0
        if self._curved:
            # The wave flattens out as the stroke grows, so a fat brush stays inside
            # the box instead of being cut off at the top and bottom.
            amp = max(0.0, (float(self.height() - 4) - w) / 2.0)
            path = QPainterPath(QPointF(x0, cy + amp))
            path.cubicTo(QPointF(x0 + (x1 - x0) * 0.35, cy - amp * 1.8),
                         QPointF(x1 - (x1 - x0) * 0.35, cy + amp * 1.8),
                         QPointF(x1, cy - amp))
            p.drawPath(path)
        else:
            p.drawLine(QPointF(x0, cy), QPointF(x1, cy))


class _StrokeZoomPopup(QWidget):
    """The big sample that pops up while a size slider is being dragged.

    The little sample beside the slider runs out of height above _StrokePreview's true
    range, so a 200 wide brush ends up looking much the same as a 60 wide one. This
    window has the room to draw the stroke at its real thickness for the whole range.
    It appears when the slider is grabbed and goes away when it is let go."""

    _PAD = 20

    def __init__(self, curved: bool, max_width: int, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip |
                         Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._curved = curved
        self._width = 2
        self._color = QColor(255, 0, 0)
        side = int(max(2, max_width)) + 2 * self._PAD
        # The extra height is the caption strip at the top; the stroke gets the rest.
        self.setFixedSize(max(260, side + 90), max(96, side) + 18)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_for(self, anchor: QWidget, width: int, color: QColor, hold_ms: int = 0):
        """Put the sample above `anchor` — below it when the screen ends there — and
        keep it up until hide(), or for hold_ms when the size came from the keyboard
        and there is no release to wait for."""
        self._width = max(1, int(width))
        self._color = QColor(color)
        top_left = anchor.mapToGlobal(anchor.rect().topLeft())
        cx = top_left.x() + anchor.width() // 2
        x = cx - self.width() // 2
        y = top_left.y() - self.height() - 8
        scr = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            x = max(g.left() + 2, min(x, g.right() - self.width() - 2))
            if y < g.top() + 2:
                y = top_left.y() + anchor.height() + 8
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()
        if hold_ms > 0:
            self._hide_timer.start(hold_ms)
        else:
            self._hide_timer.stop()

    def hide(self):
        self._hide_timer.stop()
        super().hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#9aa2ad")))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0),
                          5, 5)

        f = QFont(); f.setPointSize(8)
        p.setFont(f)
        p.setPen(QPen(QColor("#666666")))
        p.drawText(QRectF(6, 3, self.width() - 12, 14),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"{self._width} px — true size at 100 % zoom")

        w = float(self._width)
        p.setPen(QPen(self._color, w, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        x0 = self._PAD + w / 2.0
        x1 = self.width() - self._PAD - w / 2.0
        top = 18.0
        room = self.height() - top - 4.0
        cy = top + room / 2.0
        # The wave is only drawn while there is clearly room left over for it. A fat
        # stroke bent into a box it nearly fills reads as a red blob; the same stroke
        # drawn straight shows its thickness at a glance.
        if self._curved and w <= room * 0.45:
            # A cubic swings out to roughly 3/4 of the way to its control points, so
            # the amplitude is scaled back by that much to keep the stroke inside.
            amp = max(0.0, (room - w) / 2.0) / 1.35
            path = QPainterPath(QPointF(x0, cy + amp))
            path.cubicTo(QPointF(x0 + (x1 - x0) * 0.35, cy - amp * 1.8),
                         QPointF(x1 - (x1 - x0) * 0.35, cy + amp * 1.8),
                         QPointF(x1, cy - amp))
            p.drawPath(path)
        else:
            p.drawLine(QPointF(x0, cy), QPointF(x1, cy))


class _StatCell(QLabel):
    """One statistics readout. Double-click copies the number, the way the Image
    Slider's spatial-contrast cells do."""

    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        self._caption = caption
        self._value = "—"
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setStyleSheet(
            "QLabel { background: #f7f7f7; border: 1px solid #d8d8d8; border-radius: 3px;"
            " padding: 3px 5px; color: #111; font-size: 11px; }")
        self.setToolTip("Double-click to copy the value")
        self.set_value("—")

    def set_value(self, text: str):
        self._value = text
        self.setText(f"<span style='color:#666'>{self._caption}</span><br>"
                     f"<b style='font-size:12px'>{text}</b>")

    def mouseDoubleClickEvent(self, e):
        QGuiApplication.clipboard().setText(self._value)


class _HistogramWidget(QWidget):
    """Histogram of the measured values with draggable black and white points.

    This is the ImageJ way of setting a display range: you see where the data actually
    is, instead of guessing with two sliders.

    The bars are counted from the NATIVE data whenever the source file could be read —
    a 12-bit frame really does reach 4095, and folding it into 256 display codes first
    hides where the values sit. The two draggable points stay display codes 0…255,
    because that is what the display mapping is built from; the numbers written next to
    them are converted to the axis unit so the operator reads real counts."""

    changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(96)
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self._counts = np.zeros(256, dtype=np.float64)
        self._lo, self._hi = 0, 255
        self._drag = ""
        self._vmax = 255.0
        self._unit = UNIT_CODE

    def set_data(self, values: "np.ndarray | None", vmax: float = 255.0,
                 unit: str = UNIT_CODE):
        """`values` are in the axis unit, `vmax` is the top of the axis (255 for display
        codes, the camera's full scale for native counts)."""
        self._vmax = float(vmax) if vmax and vmax > 0 else 255.0
        self._unit = unit
        if values is None or values.size == 0:
            self._counts = np.zeros(256, dtype=np.float64)
        else:
            s = _stat_sample(values)
            # 256 buckets over 0…vmax, so the bar positions line up with the display
            # codes the black / white points are expressed in.
            idx = np.clip(np.asarray(s, dtype=np.float64) * (255.0 / self._vmax),
                          0, 255).astype(np.int32)
            self._counts = np.bincount(np.ravel(idx), minlength=256).astype(np.float64)
        self.update()

    def _axis_value(self, code: int) -> float:
        return code / 255.0 * self._vmax

    def set_window(self, lo: int, hi: int):
        self._lo, self._hi = int(lo), int(hi)
        self.update()

    def window(self) -> "tuple[int, int]":
        return self._lo, self._hi

    def _x_of(self, code: int) -> float:
        return code / 255.0 * max(1, self.width() - 1)

    def _code_at(self, x: float) -> int:
        return int(max(0, min(255, round(x / max(1, self.width() - 1) * 255))))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#fbfbfb"))
        p.setPen(QPen(QColor("#d0d0d0")))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        top = float(self._counts.max())
        h = self.height() - 24
        if top > 0:
            # Square-root scaling: a beam spot is a handful of pixels next to a huge
            # background peak, and on a linear axis it is invisible.
            norm = np.sqrt(self._counts / top)
            p.setPen(QPen(QColor("#5b7fb0")))
            for c in range(256):
                x = self._x_of(c)
                bar = norm[c] * h
                if bar > 0:
                    p.drawLine(QPointF(x, h + 1), QPointF(x, h + 1 - bar))
        p.fillRect(QRectF(0, h + 2, self.width(), 9), QColor("#000"))
        for c in range(0, 256, 4):
            p.setPen(QColor(c, c, c))
            p.drawLine(QPointF(self._x_of(c), h + 2), QPointF(self._x_of(c), h + 11))
        for code, colour in ((self._lo, "#111111"), (self._hi, "#c02020")):
            x = self._x_of(code)
            p.setPen(QPen(QColor(colour), 2))
            p.drawLine(QPointF(x, 0), QPointF(x, h + 11))
        p.setPen(QColor("#555"))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(2, 10, f"{self._axis_value(self._lo):.0f}")
        p.drawText(QRectF(0, 0, self.width() - 2, 12),
                   Qt.AlignmentFlag.AlignRight,
                   f"{self._axis_value(self._hi):.0f}")
        p.drawText(QRectF(0, self.height() - 12, self.width(), 12),
                   Qt.AlignmentFlag.AlignHCenter,
                   f"0 … {self._vmax:.0f} {self._unit}")

    def mousePressEvent(self, e):
        c = self._code_at(e.position().x())
        self._drag = "lo" if abs(c - self._lo) <= abs(c - self._hi) else "hi"
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if not (e.buttons() & Qt.MouseButton.LeftButton) or not self._drag:
            return
        c = self._code_at(e.position().x())
        if self._drag == "lo":
            self._lo = min(c, self._hi - 1)
        else:
            self._hi = max(c, self._lo + 1)
        self.update()
        self.changed.emit(self._lo, self._hi)

    def mouseReleaseEvent(self, _e):
        self._drag = ""

    def mouseDoubleClickEvent(self, _e):
        self._lo, self._hi = 0, 255
        self.update()
        self.changed.emit(self._lo, self._hi)


class _PlotWidget(QWidget):
    """Minimal painted line plot with a hover readout.

    Deliberately not matplotlib: a matplotlib toolbar has to be built through the
    Finder's icon-tinting workaround or its icons come out invisible, and this plot
    needs neither a toolbar nor a dependency."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 260)
        self.setMouseTracking(True)
        self._x = np.zeros(0)
        self._y = np.zeros(0)
        self._unit = ""
        self._xunit = "px"
        self._xlabel = "distance along the line"
        self._hover = None
        self._overlay = None          # (x, y, colour, caption) drawn over the trace
        self._hlines = []             # [(value, colour, caption)] — e.g. the half maximum
        self._vlines = []             # [(x, colour)] — e.g. the two FWHM crossings

    def set_data(self, x, y, unit: str, xlabel: str = "distance along the line",
                 xunit: str = "px"):
        self._x, self._y, self._unit = np.asarray(x), np.asarray(y), unit
        self._xlabel, self._xunit = xlabel, xunit
        self.update()

    def set_overlay(self, x=None, y=None, colour: str = "#d05000", caption: str = ""):
        """A second trace over the first — the Gaussian fit. None clears it."""
        self._overlay = None if x is None else (np.asarray(x), np.asarray(y),
                                               colour, caption)
        self.update()

    def set_marks(self, hlines=None, vlines=None):
        self._hlines = list(hlines or [])
        self._vlines = list(vlines or [])
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(56, 12, max(10, self.width() - 72), max(10, self.height() - 46))

    def _limits(self):
        ymin, ymax = float(self._y.min()), float(self._y.max())
        for h in self._hlines:
            ymin, ymax = min(ymin, h[0]), max(ymax, h[0])
        if self._overlay is not None and self._overlay[1].size:
            ymin = min(ymin, float(self._overlay[1].min()))
            ymax = max(ymax, float(self._overlay[1].max()))
        if ymax <= ymin:
            ymax = ymin + 1.0
        xmin, xmax = float(self._x.min()), float(self._x.max())
        if xmax <= xmin:
            xmax = xmin + 1.0
        return xmin, xmax, ymin, ymax

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        r = self._plot_rect()
        p.setPen(QPen(QColor("#888")))
        p.drawRect(r)
        if self._x.size < 2:
            return
        xmin, xmax, ymin, ymax = self._limits()

        def to_px(xv, yv) -> QPointF:
            return QPointF(r.left() + r.width() * (float(xv) - xmin) / (xmax - xmin),
                           r.bottom() - r.height() * (float(yv) - ymin) / (ymax - ymin))

        p.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            yy = r.bottom() - r.height() * i / 4.0
            p.setPen(QPen(QColor("#ececec")))
            p.drawLine(QPointF(r.left(), yy), QPointF(r.right(), yy))
            p.setPen(QColor("#555"))
            p.drawText(QRectF(2, yy - 8, 50, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{ymin + (ymax - ymin) * i / 4.0:.0f}")
        p.setPen(QColor("#555"))
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16),
                   Qt.AlignmentFlag.AlignHCenter,
                   f"{self._xlabel} ({self._xunit}), {xmin:.0f} … {xmax:.0f}")

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for value, colour, caption in self._hlines:
            y = to_px(xmin, value).y()
            p.setPen(QPen(QColor(colour), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            if caption:
                p.setPen(QColor(colour))
                p.drawText(QRectF(r.right() - 96, y - 14, 92, 14),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                           caption)
        for value, colour in self._vlines:
            if not (xmin <= value <= xmax):
                continue
            x = to_px(value, ymin).x()
            p.setPen(QPen(QColor(colour), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        if self._overlay is not None:
            ox, oy, colour, caption = self._overlay
            if ox.size >= 2:
                p.setPen(QPen(QColor(colour), 1.4, Qt.PenStyle.DashLine))
                p.drawPolyline(QPolygonF([to_px(a, b) for a, b in zip(ox, oy)]))
                if caption:
                    p.setPen(QColor(colour))
                    p.drawText(QRectF(r.left() + 6, r.top() + 22, r.width() - 12, 14),
                               Qt.AlignmentFlag.AlignLeft, caption)

        p.setPen(QPen(QColor("#2f6fd0"), 1.6))
        p.drawPolyline(QPolygonF([to_px(a, b) for a, b in zip(self._x, self._y)]))

        if self._hover is not None and r.contains(self._hover):
            frac = (self._hover.x() - r.left()) / r.width()
            idx = int(max(0, min(self._x.size - 1, round(frac * (self._x.size - 1)))))
            hp = to_px(self._x[idx], self._y[idx])
            p.setPen(QPen(QColor("#c02020"), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(hp.x(), r.top()), QPointF(hp.x(), r.bottom()))
            p.setBrush(QBrush(QColor("#c02020")))
            p.drawEllipse(hp, 3, 3)
            txt = (f"{self._x[idx]:.2f} {self._xunit}   "
                   f"{self._y[idx]:.1f} {self._unit}")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 180)))
            p.drawRect(QRectF(r.left() + 4, r.top() + 4, 210, 18))
            p.setPen(QColor("#fff"))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRectF(r.left() + 8, r.top() + 4, 206, 18),
                       Qt.AlignmentFlag.AlignVCenter, txt)

    def mouseMoveEvent(self, e):
        self._hover = QPointF(e.position())
        self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()


class ProfileDialog(QDialog):
    """Values along a line, with the numbers one click away.

    Also where a width is read: FWHM and the 1/e² width straight off the samples, and
    a Gaussian fit on request whose r² says whether calling this spot Gaussian is
    honest. Distances are in millimetres as soon as the image has a scale."""

    #  How many pixels across the line to average. Set by the tab from its own
    #  spinbox before the profile is handed over.
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Line profile")
        self.resize(700, 460)
        self._x = np.zeros(0)
        self._y = np.zeros(0)
        self._unit = ""
        self._xunit = "px"
        self._title = ""
        self._metrics = {}
        self._recompute = None            # set by the tab: (width) -> (x, y)

        lay = QVBoxLayout(self)
        self._info = QLabel("—")
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: #222; font-size: 11px;")
        self._widths = QLabel("—")
        self._widths.setWordWrap(True)
        self._widths.setStyleSheet(
            "QLabel { color: #10243c; font-size: 11px; background: #eef3fa;"
            " border: 1px solid #cfdcea; border-radius: 3px; padding: 4px 6px; }")
        self._plot = _PlotWidget()
        lay.addWidget(self._info)
        lay.addWidget(self._widths)
        lay.addWidget(self._plot, 1)

        row = QHBoxLayout()
        row.addWidget(_small_label("Average across the line"))
        self._width_sb = QSpinBox()
        self._width_sb.setRange(1, 199)
        self._width_sb.setSingleStep(2)
        self._width_sb.setValue(1)
        self._width_sb.setSuffix(" px")
        self._width_sb.setFixedWidth(74)
        self._width_sb.setToolTip(
            "Average this many pixels across the line. A wider strip is much less "
            "noisy and gives a steadier width, as long as the spot does not change "
            "along it.")
        self._width_sb.valueChanged.connect(self._on_width)
        row.addWidget(self._width_sb)
        self._cb_fit = QCheckBox("Gaussian fit")
        self._cb_fit.setStyleSheet(_CHECK_QSS)
        self._cb_fit.setToolTip("Fit a Gaussian on a pedestal and draw it over the trace")
        self._cb_fit.toggled.connect(self._refresh)
        row.addWidget(self._cb_fit)
        row.addStretch(1)
        b_copy = _btn("Copy values", "Copy the samples to the clipboard as text")
        b_copy.clicked.connect(self._copy)
        b_csv = _btn("Save CSV…", "Write the samples to a CSV file")
        b_csv.clicked.connect(self._save_csv)
        row.addWidget(b_copy); row.addWidget(b_csv)
        lay.addLayout(row)

    def line_width(self) -> int:
        return int(self._width_sb.value())

    def set_profile(self, x, y, unit: str, title: str, xunit: str = "px",
                    recompute=None):
        self._x, self._y, self._unit = np.asarray(x), np.asarray(y), unit
        self._xunit = xunit
        self._title = title
        self._recompute = recompute
        self._refresh()

    def _on_width(self, _v):
        """Re-sample at the new width. The dialog does not own the image, so it asks
        the tab for the samples through the callback it was given."""
        if self._recompute is None:
            return
        got = self._recompute(self.line_width())
        if got is None:
            return
        self._x, self._y = np.asarray(got[0]), np.asarray(got[1])
        self._refresh()

    def _refresh(self):
        x, y, unit = self._x, self._y, self._unit
        if y.size == 0:
            return
        self._info.setText(
            f"{self._title} — {y.size} samples, min {y.min():.0f}, max {y.max():.0f}, "
            f"mean {y.mean():.1f} {unit}")
        self._plot.set_data(x, y, unit, "distance along the line", self._xunit)

        m = wk_beam.profile_metrics(x, y) if wk_beam is not None else {}
        self._metrics = m
        bits = []
        if "fwhm" in m:
            bits.append(f"FWHM <b>{m['fwhm']:.2f} {self._xunit}</b>")
        if "w_1e2" in m:
            bits.append(f"1/e² width <b>{m['w_1e2']:.2f} {self._xunit}</b>")
        if "d4sigma" in m:
            bits.append(f"D4σ {m['d4sigma']:.2f} {self._xunit}")
        if "centroid_x" in m:
            bits.append(f"centre of mass {m['centroid_x']:.2f} {self._xunit}")
        if "peak" in m:
            bits.append(f"peak {m['peak']:.0f} {unit} at {m['peak_x']:.2f} "
                        f"{self._xunit}")
        if "baseline" in m:
            bits.append(f"baseline {m['baseline']:.0f} {unit}")

        hlines, vlines = [], []
        if "fwhm_level" in m:
            hlines.append((m["fwhm_level"], "#c02020", "half maximum"))
            vlines += [(m["fwhm_left"], "#c02020"), (m["fwhm_right"], "#c02020")]
        if "w_1e2_level" in m:
            hlines.append((m["w_1e2_level"], "#0a8f4a", "1/e²"))
            vlines += [(m["w_1e2_left"], "#0a8f4a"), (m["w_1e2_right"], "#0a8f4a")]
        self._plot.set_marks(hlines, vlines)

        if self._cb_fit.isChecked() and wk_beam is not None:
            fit = wk_beam.gaussian_fit(x, y)
            if fit is None:
                self._plot.set_overlay(None)
                bits.append("Gaussian fit failed")
            else:
                self._plot.set_overlay(
                    x, fit["fitted"], "#d05000",
                    f"Gaussian fit: σ {fit['sigma']:.2f} {self._xunit}, "
                    f"FWHM {fit['fwhm']:.2f} {self._xunit}, r² {fit['r2']:.4f}")
                bits.append(f"fitted FWHM <b>{fit['fwhm']:.2f} {self._xunit}</b> "
                            f"(r² {fit['r2']:.4f})")
        else:
            self._plot.set_overlay(None)

        self._widths.setText("  |  ".join(bits) if bits
                             else "not enough of a peak to measure a width")

    def _rows(self):
        return [(f"{a:.4f}", f"{b:.4f}") for a, b in zip(self._x, self._y)]

    def _header(self):
        return (f"distance_{self._xunit}", "value")

    def _copy(self):
        head = "\t".join(self._header())
        QGuiApplication.clipboard().setText(
            head + "\n" + "\n".join("\t".join(r) for r in self._rows()))

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", "profile.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        if _write_csv(path, self._header(), self._rows()):
            return
        QMessageBox.warning(self, "Save error", f"Could not save:\n{path}")


# ─────────────────────────────────────────────────────────────────
#  Tables of numbers
# ─────────────────────────────────────────────────────────────────

_TABLE_QSS = (
    "QTableWidget { background: #ffffff; alternate-background-color: #f5f7fa;"
    " color: #16202c; gridline-color: #dfe4ea; font-size: 11px;"
    " selection-background-color: #cfe0f7; selection-color: #10243c; }"
    "QHeaderView::section { background: #eef1f5; color: #16202c; font-weight: 600;"
    " border: 0px; border-right: 1px solid #dfe4ea;"
    " border-bottom: 1px solid #cfd6de; padding: 4px 6px; }"
    "QTableCornerButton::section { background: #eef1f5; border: 0px; }"
)


class _TableDialog(QDialog):
    """A table of numbers with Copy and Save CSV — the results table, the histogram
    counts and the beam report all use this one window.

    Every colour is stated: the table would otherwise take the application's light
    palette for the cells and the system palette for the header, which on this machine
    puts grey-on-grey text in the header row."""

    def __init__(self, title: str, parent=None, extra_buttons=()):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self.resize(760, 480)
        self._headers: list = []
        self._rows: list = []
        self._name = title

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #4a5566; font-size: 11px;")
        lay.addWidget(self._note)

        self._table = QTableWidget(0, 0)
        self._table.setStyleSheet(_TABLE_QSS)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self._table.verticalHeader().setVisible(False)
        lay.addWidget(self._table, 1)

        row = QHBoxLayout()
        for text, tip, fn in extra_buttons:
            b = _btn(text, tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        b_copy = _btn("Copy", "Copy the whole table to the clipboard")
        b_copy.clicked.connect(self._copy)
        b_csv = _btn("Save CSV…", "Write the table to a CSV file")
        b_csv.clicked.connect(self._save_csv)
        b_close = _btn("Close", "")
        b_close.clicked.connect(self.close)
        for b in (b_copy, b_csv, b_close):
            row.addWidget(b)
        lay.addLayout(row)

    def set_content(self, headers, rows, note: str = ""):
        self._headers = [str(h) for h in headers]
        self._rows = [[("" if c is None else str(c)) for c in r] for r in rows]
        self._note.setText(note)
        self._note.setVisible(bool(note))
        self._table.clear()
        self._table.setColumnCount(len(self._headers))
        self._table.setRowCount(len(self._rows))
        self._table.setHorizontalHeaderLabels(self._headers)
        for ri, r in enumerate(self._rows):
            for ci, cell in enumerate(r):
                item = QTableWidgetItem(cell)
                # Numbers right, words left — a column of right-aligned numbers can be
                # compared down the column, a ragged one cannot.
                if _looks_numeric(cell):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                self._table.setItem(ri, ci, item)
        self._table.resizeColumnsToContents()
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        if self._headers:
            hdr.setStretchLastSection(True)

    def _copy(self):
        lines = ["\t".join(self._headers)] if self._headers else []
        lines += ["\t".join(r) for r in self._rows]
        QGuiApplication.clipboard().setText("\n".join(lines))

    def _save_csv(self):
        stem = re.sub(r"[^\w\-]+", "_", self._name.lower()).strip("_") or "table"
        path, _ = QFileDialog.getSaveFileName(self, "Save table", f"{stem}.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        if not _write_csv(path, self._headers, self._rows):
            QMessageBox.warning(self, "Save error", f"Could not save:\n{path}")


def _looks_numeric(text: str) -> bool:
    try:
        float(str(text).replace("°", "").replace("%", "").strip())
        return True
    except ValueError:
        return False


class _CurveDialog(QDialog):
    """One painted curve with a hover readout, Copy and Save CSV — the radial profile
    and the encircled-energy curve. Deliberately the same plot widget as the line
    profile, so the three read the same way."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self.resize(660, 420)
        self._x = np.zeros(0)
        self._y = np.zeros(0)
        self._cols = ("x", "y")
        lay = QVBoxLayout(self)
        self._info = QLabel("—")
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: #222; font-size: 11px;")
        self._plot = _PlotWidget()
        lay.addWidget(self._info)
        lay.addWidget(self._plot, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b_copy = _btn("Copy values", "Copy the samples to the clipboard as text")
        b_copy.clicked.connect(self._copy)
        b_csv = _btn("Save CSV…", "Write the samples to a CSV file")
        b_csv.clicked.connect(self._save_csv)
        b_close = _btn("Close", "")
        b_close.clicked.connect(self.close)
        for b in (b_copy, b_csv, b_close):
            row.addWidget(b)
        lay.addLayout(row)

    def set_curve(self, x, y, info: str, xlabel: str, xunit: str, yunit: str,
                  columns=("x", "y"), hlines=None, vlines=None):
        self._x, self._y, self._cols = np.asarray(x), np.asarray(y), columns
        self._plot.set_data(x, y, yunit, xlabel, xunit)
        self._plot.set_marks(hlines, vlines)
        self._info.setText(info)

    def _rows(self):
        return [(f"{a:.4f}", f"{b:.5f}") for a, b in zip(self._x, self._y)]

    def _copy(self):
        QGuiApplication.clipboard().setText(
            "\t".join(self._cols) + "\n" +
            "\n".join("\t".join(r) for r in self._rows()))

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save curve", "curve.csv",
                                              "CSV (*.csv)")
        if path and not _write_csv(path, self._cols, self._rows()):
            QMessageBox.warning(self, "Save error", f"Could not save:\n{path}")


class _PlayDialog(QDialog):
    """Steps through the images in the Workshop like a projector.

    Here so an animation can be looked at before it is written to a file — a GIF that
    turns out to be too fast is a file saved twice.

    The timer is a precise one: a default Qt timer is coarse on Windows and a 33 ms
    frame time comes out at about 21 frames a second, which reads as a stutter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Play the images")
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self.resize(720, 620)
        self._frames: list = []
        self._labels: list = []
        self._idx = 0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._step)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self._view = QLabel("—")
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._view.setMinimumSize(320, 320)
        self._view.setStyleSheet("QLabel { background: #2b2b2b; color: #dddddd;"
                                 " border: 1px solid #d5dae1; }")
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        lay.addWidget(self._view, 1)

        self._caption = QLabel("—")
        self._caption.setStyleSheet("color: #16202c; font-size: 11px;")
        lay.addWidget(self._caption)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.valueChanged.connect(self._on_slider)
        lay.addWidget(self._slider)

        row = QHBoxLayout()
        self._btn_play = _btn("Play", "Start and stop the animation")
        self._btn_play.clicked.connect(self._toggle)
        row.addWidget(self._btn_play)
        for text, tip, delta in (("◀", "One image back", -1), ("▶", "One image on", 1)):
            b = _btn(text, tip)
            b.setFixedWidth(34)
            b.clicked.connect(lambda _c, d=delta: self._jump(d))
            row.addWidget(b)
        row.addWidget(_small_label("Frame time"))
        self._ms = QSpinBox()
        self._ms.setRange(20, 5000)
        self._ms.setSingleStep(20)
        self._ms.setValue(200)
        self._ms.setSuffix(" ms")
        self._ms.setFixedWidth(84)
        self._ms.valueChanged.connect(
            lambda v: self._timer.setInterval(int(v)) if self._timer.isActive() else None)
        row.addWidget(self._ms)
        self._cb_loop = QCheckBox("Repeat")
        self._cb_loop.setStyleSheet(_CHECK_QSS)
        self._cb_loop.setChecked(True)
        row.addWidget(self._cb_loop)
        row.addStretch(1)
        b_close = _btn("Close", "")
        b_close.clicked.connect(self.close)
        row.addWidget(b_close)
        lay.addLayout(row)

    def frame_ms(self) -> int:
        return int(self._ms.value())

    def loops(self) -> bool:
        return self._cb_loop.isChecked()

    def set_frames(self, images, labels):
        self._frames = list(images)
        self._labels = list(labels)
        self._idx = 0
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(0, len(self._frames) - 1))
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._show()

    def _show(self):
        if not self._frames:
            self._view.setText("No images.")
            self._caption.setText("—")
            return
        img = self._frames[self._idx]
        pm = QPixmap.fromImage(img).scaled(
            self._view.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._view.setPixmap(pm)
        name = self._labels[self._idx] if self._idx < len(self._labels) else ""
        self._caption.setText(f"{self._idx + 1} / {len(self._frames)}   {name}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._show()

    def _on_slider(self, v: int):
        self._idx = int(v)
        self._show()

    def _jump(self, delta: int):
        if not self._frames:
            return
        self._idx = (self._idx + delta) % len(self._frames)
        self._slider.blockSignals(True)
        self._slider.setValue(self._idx)
        self._slider.blockSignals(False)
        self._show()

    def _step(self):
        if not self._frames:
            return
        if self._idx + 1 >= len(self._frames) and not self._cb_loop.isChecked():
            self._toggle()
            return
        self._jump(1)

    def _toggle(self):
        if self._timer.isActive():
            self._timer.stop()
            self._btn_play.setText("Play")
        else:
            if len(self._frames) < 2:
                return
            self._timer.start(self.frame_ms())
            self._btn_play.setText("Stop")

    def closeEvent(self, e):
        self._timer.stop()
        self._btn_play.setText("Play")
        super().closeEvent(e)


# ─────────────────────────────────────────────────────────────────
#  The tab
# ─────────────────────────────────────────────────────────────────

_UI_STATE_PATH = (Path(os.environ.get("APPDATA", Path.home())) /
                  "ELI_ImageTools" / "workshop_ui_state.json")

# ─────────────────────────────────────────────────────────────────────────────
#  Tool icons
#
#  Every icon in the tool strip is DRAWN HERE, not typed as a character. The old
#  strip used Unicode glyphs, which meant three different typefaces in one row:
#  Segoe UI Emoji supplied ✋🔍📏💧 as full-colour bitmaps that ignore the
#  stylesheet's colour, Segoe UI Symbol supplied ▭◯⬠〰 as hairline outlines, and
#  two of them (⬠ ⧉) are not guaranteed to exist at all. At the 13px font size
#  the strip used, the monochrome ones carried barely 9px of ink.
#
#  Drawing them instead buys three things a font cannot: an exact stroke weight,
#  one uniform look across the whole set, and a real white repaint for the
#  checked state — where a colour-emoji glyph used to stay colourful on blue.
#
#  Every recipe draws inside a 20x20 grid; the painter is scaled to whatever
#  size is asked for, so one recipe serves every monitor scaling.
# ─────────────────────────────────────────────────────────────────────────────
_ICON_BOX = 20.0                  # the grid every recipe below draws in
_ICON_PX = 20                     # on-screen icon size
_ICON_SIZES = (20, 30, 40)        # 100% / 150% / 200% Windows display scaling
_INK_NORMAL = QColor("#1e2530")
_INK_CHECKED = QColor("#ffffff")
_INK_DISABLED = QColor("#a0a8b2")
_ICON_CACHE: dict = {}


def _ipen(c: QColor, w: float = 2.0,
          cap=Qt.PenCapStyle.RoundCap,
          join=Qt.PenJoinStyle.RoundJoin,
          dash=None) -> QPen:
    """One pen helper, so the whole set shares a single weight vocabulary."""
    p = QPen(c)
    p.setWidthF(w)
    p.setCapStyle(cap)
    p.setJoinStyle(join)
    if dash:
        p.setDashPattern(dash)
    return p


def _ico_pan(p, c):
    # Solid palm with three fingers and a thumb. A hollow outline turns to mush
    # at this size, so the hand is filled.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    path = QPainterPath()
    path.addRoundedRect(QRectF(5.0, 8.0, 10.0, 9.5), 3.2, 3.2)
    p.drawPath(path)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.6))
    p.drawLine(QPointF(7.0, 9.0), QPointF(7.0, 5.2))
    p.drawLine(QPointF(10.0, 9.0), QPointF(10.0, 3.6))
    p.drawLine(QPointF(13.0, 9.0), QPointF(13.0, 5.6))
    p.drawLine(QPointF(5.4, 12.2), QPointF(3.2, 9.6))


def _ico_zoom(p, c):
    # No + or - inside the lens: this one tool zooms both ways.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawEllipse(QPointF(8.6, 8.6), 5.0, 5.0)
    p.setPen(_ipen(c, 2.6))
    p.drawLine(QPointF(12.5, 12.5), QPointF(17.0, 17.0))


def _ico_select(p, c):
    # Filled cursor plus a thin same-colour outline, so its visual weight
    # matches the stroked icons beside it.
    p.setPen(_ipen(c, 1.2))
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([
        QPointF(4.6, 2.4), QPointF(4.6, 15.6), QPointF(8.1, 12.2),
        QPointF(10.4, 17.4), QPointF(12.9, 16.3), QPointF(10.7, 11.2),
        QPointF(15.4, 11.0)]))


def _ico_brush(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.3))
    path = QPainterPath(QPointF(3.2, 14.6))
    path.cubicTo(QPointF(6.4, 4.0), QPointF(10.0, 20.0), QPointF(13.0, 9.0))
    path.cubicTo(QPointF(14.6, 3.2), QPointF(16.2, 4.6), QPointF(17.0, 7.4))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)          # the dot marks where the pen went down
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(3.2, 14.6), 1.9, 1.9)


def _ico_line(p, c):
    # The two end handles are what tell this apart from the arrow.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(4.0, 16.0), QPointF(16.0, 4.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawRect(QRectF(2.4, 14.4, 3.2, 3.2))
    p.drawRect(QRectF(14.4, 2.4, 3.2, 3.2))


def _ico_arrow(p, c):
    d = QPointF(1.0 / math.sqrt(2), -1.0 / math.sqrt(2))
    tip = QPointF(16.8, 3.2)
    back = QPointF(tip.x() - 6.4 * d.x(), tip.y() - 6.4 * d.y())
    per = QPointF(-d.y(), d.x())
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(3.4, 16.6), back)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(back.x() + 2.9 * per.x(), back.y() + 2.9 * per.y()),
        QPointF(back.x() - 2.9 * per.x(), back.y() - 2.9 * per.y())]))


def _ico_rect(p, c):
    # Landscape on purpose, so it never reads as a square.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawRoundedRect(QRectF(3.0, 4.6, 14.0, 10.8), 1.6, 1.6)


def _ico_ellipse(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawEllipse(QPointF(10.0, 10.0), 7.0, 5.5)


def _ico_poly(p, c):
    # Irregular on purpose: a regular pentagon reads as "a shape", a lopsided
    # one reads as "a polygon you drew".
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawPolygon(QPolygonF([
        QPointF(3.2, 7.4), QPointF(9.4, 2.9), QPointF(17.0, 5.8),
        QPointF(14.8, 16.4), QPointF(5.8, 15.2)]))


def _ico_text(p, c):
    # A T built from strokes, not from the font. The foot serif is what makes
    # it read as a text tool rather than as the letter T.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.4, cap=Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(4.0, 5.2), QPointF(16.0, 5.2))
    p.drawLine(QPointF(10.0, 5.2), QPointF(10.0, 16.4))
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(6.8, 16.4), QPointF(13.2, 16.4))


def _ico_ruler(p, c):
    p.save()
    p.translate(10.0, 10.0)
    p.rotate(-38.0)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(-9.2, -3.4, 18.4, 6.8), 1.2, 1.2)
    p.setPen(_ipen(c, 1.7, cap=Qt.PenCapStyle.FlatCap))
    for x in (-4.6, 0.0, 4.6):
        p.drawLine(QPointF(x, -3.4), QPointF(x, -0.6))
    p.restore()


def _ico_angle(p, c):
    # Two arms from a corner with a dotted arc between them — a protractor reading,
    # which is what the tool measures. The arc is what separates it from the "line"
    # icons; without it the shape reads as a bent line.
    v = QPointF(4.2, 15.8)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawLine(v, QPointF(17.4, 15.8))
    p.drawLine(v, QPointF(14.6, 4.4))
    p.setPen(_ipen(c, 1.5, cap=Qt.PenCapStyle.FlatCap, dash=[1.6, 1.4]))
    r = 8.2
    p.drawArc(QRectF(v.x() - r, v.y() - r, 2 * r, 2 * r), 0, int(40.5 * 16))


def _ico_roi_rect(p, c):
    # Dashed = marching ants = a selection, the way ImageJ shows one.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0, cap=Qt.PenCapStyle.FlatCap,
                   join=Qt.PenJoinStyle.MiterJoin, dash=[1.7, 1.15]))
    p.drawRect(QRectF(3.2, 4.8, 13.6, 10.4))


def _ico_roi_ellipse(p, c):
    # Same pen as the rectangle region, so the two read as a pair.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0, cap=Qt.PenCapStyle.FlatCap, dash=[1.7, 1.15]))
    p.drawEllipse(QPointF(10.0, 10.0), 7.0, 5.5)


def _ico_profile(p, c):
    # Plot axes with a trace over them: the line that becomes a graph.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, cap=Qt.PenCapStyle.FlatCap,
                   join=Qt.PenJoinStyle.MiterJoin))
    axes = QPainterPath(QPointF(3.6, 2.8))
    axes.lineTo(3.6, 16.6)
    axes.lineTo(17.2, 16.6)
    p.drawPath(axes)
    p.setPen(_ipen(c, 2.1))
    p.drawPolyline(QPolygonF([
        QPointF(5.4, 13.4), QPointF(8.6, 6.2),
        QPointF(11.8, 11.4), QPointF(16.2, 4.2)]))


def _ico_cross(p, c):
    # Arms stop short of the middle, leaving the read-out dot visible.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(10.0, 2.6), QPointF(10.0, 6.4))
    p.drawLine(QPointF(10.0, 13.6), QPointF(10.0, 17.4))
    p.drawLine(QPointF(2.6, 10.0), QPointF(6.4, 10.0))
    p.drawLine(QPointF(13.6, 10.0), QPointF(17.4, 10.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawEllipse(QPointF(10.0, 10.0), 2.3, 2.3)


def _ico_crop(p, c):
    # Four bars that overhang where they cross — the crop marks a photographer
    # would draw. Two closed L brackets were tried first and read as two
    # overlapping squares, which looks like a "duplicate" icon instead.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.2, cap=Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(2.6, 6.0), QPointF(15.2, 6.0))
    p.drawLine(QPointF(6.0, 2.6), QPointF(6.0, 15.2))
    p.drawLine(QPointF(4.8, 14.0), QPointF(17.4, 14.0))
    p.drawLine(QPointF(14.0, 4.8), QPointF(14.0, 17.4))


def _ico_eyedrop(p, c):
    # The bulb is one fat round-cap stroke; no separate path needed.
    d = QPointF(1.0 / math.sqrt(2), -1.0 / math.sqrt(2))
    tip = QPointF(3.0, 17.0)

    def at(t):
        return QPointF(tip.x() + t * d.x(), tip.y() + t * d.y())

    per = QPointF(-d.y(), d.x())
    b = at(4.6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(b.x() + 1.9 * per.x(), b.y() + 1.9 * per.y()),
        QPointF(b.x() - 1.9 * per.x(), b.y() - 1.9 * per.y())]))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.4))
    p.drawLine(at(4.6), at(11.4))
    p.setPen(_ipen(c, 5.4))
    p.drawLine(at(12.6), at(16.4))


def _ico_eraser(p, c):
    # The baseline underneath is what says "erasing" rather than "a box".
    p.save()
    p.translate(10.2, 8.6)
    p.rotate(-42.0)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.0, cap=Qt.PenCapStyle.FlatCap,
                   join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(-8.4, -4.0, 15.4, 8.0), 1.4, 1.4)
    p.setPen(_ipen(c, 1.8, cap=Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(-1.4, -4.0), QPointF(-1.4, 4.0))
    p.restore()
    p.setPen(_ipen(c, 2.0))
    p.drawLine(QPointF(4.6, 17.4), QPointF(17.0, 17.4))


_ICON_RECIPES = {
    TOOL_PAN: _ico_pan,             TOOL_ZOOM: _ico_zoom,
    TOOL_SELECT: _ico_select,       TOOL_BRUSH: _ico_brush,
    TOOL_LINE: _ico_line,           TOOL_ARROW: _ico_arrow,
    TOOL_RECT: _ico_rect,           TOOL_ELLIPSE: _ico_ellipse,
    TOOL_POLY: _ico_poly,           TOOL_TEXT: _ico_text,
    TOOL_RULER: _ico_ruler,         TOOL_ANGLE: _ico_angle,
    TOOL_ROI_RECT: _ico_roi_rect,
    TOOL_ROI_ELLIPSE: _ico_roi_ellipse, TOOL_PROFILE: _ico_profile,
    TOOL_CROSS: _ico_cross,         TOOL_CROP: _ico_crop,
    TOOL_EYEDROP: _ico_eyedrop,     TOOL_ERASER: _ico_eraser,
}


def _render_icon_pixmap(name: str, px: int, ink: QColor) -> QPixmap:
    """Draw one icon at `px` pixels. The recipe always works in the 20-unit grid."""
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.scale(px / _ICON_BOX, px / _ICON_BOX)
    recipe = _ICON_RECIPES.get(name) or _ICON_RECIPES_ACTION[name]
    recipe(p, ink)
    p.end()
    return pm


def tool_icon(name: str) -> QIcon:
    """The icon for one tool, carrying its own normal, checked and greyed artwork.

    Spelling out every mode is not optional. Left to itself Qt invents the
    disabled version by fading the normal one until it is barely there, and it
    would keep the dark artwork while the button is checked and its background
    has turned blue. Both are exactly the "can't see it" failures this replaces.

    Must not be called before the application object exists — QPixmap needs it.
    """
    ic = _ICON_CACHE.get(name)
    if ic is not None:
        return ic
    ic = QIcon()
    for mode, state, ink in (
            (QIcon.Mode.Normal,   QIcon.State.Off, _INK_NORMAL),
            (QIcon.Mode.Active,   QIcon.State.Off, _INK_NORMAL),
            (QIcon.Mode.Normal,   QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Active,   QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Selected, QIcon.State.On,  _INK_CHECKED),
            (QIcon.Mode.Disabled, QIcon.State.Off, _INK_DISABLED),
            (QIcon.Mode.Disabled, QIcon.State.On,  _INK_DISABLED)):
        for px in _ICON_SIZES:        # QIcon picks the right one per monitor
            ic.addPixmap(_render_icon_pixmap(name, px, ink), mode, state)
    _ICON_CACHE[name] = ic
    return ic


# ── Icons for the ordinary command buttons ───────────────────────────────────
#  Same grid and the same pen vocabulary as the tool strip, so a Rotate button
#  and a tool button look like they belong to one program. These sit beside a
#  word, so they only have to reinforce the label, not carry it alone.

def _arrow_head(p, c, tip: QPointF, dx: float, dy: float, size: float = 3.1):
    """Filled triangle at `tip`, pointing along (dx, dy) which must be a unit vector."""
    base = QPointF(tip.x() - dx * size * 1.5, tip.y() - dy * size * 1.5)
    px, py = -dy, dx
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([
        tip,
        QPointF(base.x() + px * size * 0.82, base.y() + py * size * 0.82),
        QPointF(base.x() - px * size * 0.82, base.y() - py * size * 0.82)]))


def _arc_arrow(p, c, cx, cy, r, start_deg, sweep_deg, w=2.0, head=3.1):
    """An arc with an arrowhead at the far end. Angles are Qt's: degrees
    counterclockwise from three o'clock."""
    rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
    path = QPainterPath()
    path.arcMoveTo(rect, start_deg)
    path.arcTo(rect, start_deg, sweep_deg)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, w, cap=Qt.PenCapStyle.FlatCap))
    p.drawPath(path)
    a = math.radians(start_deg + sweep_deg)
    s = 1.0 if sweep_deg > 0 else -1.0
    dx, dy = -s * math.sin(a), -s * math.cos(a)
    end = QPointF(cx + r * math.cos(a), cy - r * math.sin(a))
    _arrow_head(p, c, QPointF(end.x() + dx * head, end.y() + dy * head), dx, dy, head)


def _ico_plus(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.6))
    p.drawLine(QPointF(10.0, 5.2), QPointF(10.0, 14.8))
    p.drawLine(QPointF(5.2, 10.0), QPointF(14.8, 10.0))


def _ico_minus(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.6))
    p.drawLine(QPointF(5.2, 10.0), QPointF(14.8, 10.0))


def _ico_undo(p, c):
    # A bent arrow, not a circle — the full circle is reserved for "Original".
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.2, cap=Qt.PenCapStyle.RoundCap))
    path = QPainterPath(QPointF(16.2, 16.0))
    path.cubicTo(QPointF(15.4, 9.4), QPointF(12.2, 6.4), QPointF(8.8, 6.4))
    p.drawPath(path)
    _arrow_head(p, c, QPointF(4.9, 6.4), -1.0, 0.0)


def _ico_redo(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 2.2, cap=Qt.PenCapStyle.RoundCap))
    path = QPainterPath(QPointF(3.8, 16.0))
    path.cubicTo(QPointF(4.6, 9.4), QPointF(7.8, 6.4), QPointF(11.2, 6.4))
    p.drawPath(path)
    _arrow_head(p, c, QPointF(15.1, 6.4), 1.0, 0.0)


def _ico_reset(p, c):
    # Almost a full turn: "put everything back where it started".
    _arc_arrow(p, c, 10.0, 10.2, 6.1, 70.0, 300.0, w=2.2)


def _ico_rotate_left(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.9, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(5.8, 10.0, 8.4, 7.2), 1.1, 1.1)   # the picture
    _arc_arrow(p, c, 10.0, 10.4, 7.0, 25.0, 130.0, w=2.0)      # turning left


def _ico_rotate_right(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.9, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(5.8, 10.0, 8.4, 7.2), 1.1, 1.1)
    _arc_arrow(p, c, 10.0, 10.4, 7.0, 155.0, -130.0, w=2.0)


def _ico_flip_h(p, c):
    # One solid half, one hollow half, pointing away from the fold line. A
    # back-to-back pair of right triangles was tried first and read as a sail.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.7, cap=Qt.PenCapStyle.FlatCap, dash=[1.9, 1.5]))
    p.drawLine(QPointF(10.0, 2.8), QPointF(10.0, 17.2))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(8.0, 5.4), QPointF(8.0, 14.6), QPointF(3.0, 10.0)]))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, join=Qt.PenJoinStyle.MiterJoin))
    p.drawPolygon(QPolygonF([QPointF(12.0, 5.4), QPointF(12.0, 14.6), QPointF(17.0, 10.0)]))


def _ico_flip_v(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.7, cap=Qt.PenCapStyle.FlatCap, dash=[1.9, 1.5]))
    p.drawLine(QPointF(2.8, 10.0), QPointF(17.2, 10.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(5.4, 8.0), QPointF(14.6, 8.0), QPointF(10.0, 3.0)]))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, join=Qt.PenJoinStyle.MiterJoin))
    p.drawPolygon(QPolygonF([QPointF(5.4, 12.0), QPointF(14.6, 12.0), QPointF(10.0, 17.0)]))


def _ico_play(p, c):
    # Filled: this is the button that starts the work, and a hollow triangle next to
    # hollow outline icons stops reading as the primary action.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(6.4, 4.4), QPointF(6.4, 15.6),
                             QPointF(15.6, 10.0)]))


def _ico_step_prev(p, c):
    # Triangle plus a bar, the transport symbol for "one step back", not a bare
    # arrow — a bare arrow is what a scrollbar uses for "keep going".
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(15.2, 4.6), QPointF(15.2, 15.4),
                             QPointF(7.6, 10.0)]))
    p.drawRect(QRectF(4.6, 4.6, 2.1, 10.8))


def _ico_step_next(p, c):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    p.drawPolygon(QPolygonF([QPointF(4.8, 4.6), QPointF(4.8, 15.4),
                             QPointF(12.4, 10.0)]))
    p.drawRect(QRectF(13.3, 4.6, 2.1, 10.8))


def _ico_popout(p, c):
    # A frame with an arrow leaving it through the top-right corner.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(3.0, 6.4, 10.6, 10.6), 1.4, 1.4)
    p.setPen(_ipen(c, 1.9))
    p.drawLine(QPointF(11.0, 9.0), QPointF(16.2, 3.8))
    _arrow_head(p, c, QPointF(16.9, 3.1), 0.7071, -0.7071)


def _ico_calendar(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(3.0, 5.0, 14.0, 12.2), 1.4, 1.4)
    p.setPen(_ipen(c, 1.6, cap=Qt.PenCapStyle.FlatCap))
    p.drawLine(QPointF(3.0, 8.6), QPointF(17.0, 8.6))          # the header band
    p.drawLine(QPointF(6.6, 2.9), QPointF(6.6, 5.9))           # the two rings
    p.drawLine(QPointF(13.4, 2.9), QPointF(13.4, 5.9))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(c))
    for x in (5.9, 9.3, 12.7):                                 # one week of days
        p.drawRect(QRectF(x, 11.0, 1.8, 1.8))
    for x in (5.9, 9.3):
        p.drawRect(QRectF(x, 14.0, 1.8, 1.8))


def _ico_camera(p, c):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_ipen(c, 1.8, join=Qt.PenJoinStyle.MiterJoin))
    p.drawRoundedRect(QRectF(2.6, 6.4, 14.8, 10.4), 1.6, 1.6)  # the body
    p.drawLine(QPointF(6.4, 6.4), QPointF(7.6, 4.2))           # the top bevel
    p.drawLine(QPointF(11.6, 6.4), QPointF(10.4, 4.2))
    p.drawLine(QPointF(7.6, 4.2), QPointF(10.4, 4.2))
    p.setPen(_ipen(c, 1.9))
    p.drawEllipse(QRectF(6.8, 8.4, 6.4, 6.4))                  # the lens


# "180°" keeps its plain text: a third circular arrow next to Undo and Original
# would be one turning arrow too many to tell apart.
_ICON_RECIPES_ACTION = {
    "plus": _ico_plus, "minus": _ico_minus, "undo": _ico_undo, "redo": _ico_redo,
    "reset": _ico_reset, "rotate_left": _ico_rotate_left,
    "rotate_right": _ico_rotate_right,
    "flip_h": _ico_flip_h, "flip_v": _ico_flip_v,
    # Used by the One Moment tab, which has no icon set of its own — one painted
    # icon vocabulary for the whole program.
    "play": _ico_play, "step_prev": _ico_step_prev, "step_next": _ico_step_next,
    "popout": _ico_popout, "calendar": _ico_calendar, "camera": _ico_camera,
}


def action_icon(name: str, ink: str = "#1e2530") -> QIcon:
    """Icon for a plain command button. `ink` lets the red "danger" buttons keep
    their own colour instead of standing out as the one dark mark in a red row."""
    key = ("action", name, ink)
    ic = _ICON_CACHE.get(key)
    if ic is not None:
        return ic
    ic = QIcon()
    for mode, col in ((QIcon.Mode.Normal, QColor(ink)),
                      (QIcon.Mode.Active, QColor(ink)),
                      (QIcon.Mode.Disabled, _INK_DISABLED)):
        for px in _ICON_SIZES:
            ic.addPixmap(_render_icon_pixmap(name, px, col), mode, QIcon.State.Off)
    _ICON_CACHE[key] = ic
    return ic


# Short name, tooltip, tool id. `None` marks a separator.
_TOOL_STRIP = [
    ("Hand", "Hand — drag the picture around (also middle mouse, Ctrl+drag or Space+drag)", TOOL_PAN),
    ("Magnify", "Magnify — left click zooms in where you click, right click zooms out, "
                "drag a box to blow that box up to the whole view", TOOL_ZOOM),
    ("Select", "Select — pick a drawn item, then move it or drag its handles", TOOL_SELECT),
    None,
    ("Freehand line", "Freehand line", TOOL_BRUSH),
    ("Straight line", "Straight line", TOOL_LINE),
    ("Arrow", "Arrow", TOOL_ARROW),
    ("Rectangle", "Rectangle", TOOL_RECT),
    ("Ellipse", "Ellipse (hold Shift for a circle)", TOOL_ELLIPSE),
    ("Polygon", "Polygon — click each corner, right click or double click to finish", TOOL_POLY),
    ("Text", "Text — click, then type; Enter confirms", TOOL_TEXT),
    None,
    ("Ruler", "Ruler — length in pixels and in real size. Hold Shift while drawing to "
              "keep it straight", TOOL_RULER),
    ("Angle", "Angle — click the end of one arm, then the corner, then the end of the "
              "other arm", TOOL_ANGLE),
    ("Rectangle region", "Rectangle region — min, max, mean and more for that area", TOOL_ROI_RECT),
    ("Ellipse region", "Ellipse region — min, max, mean and more for that area", TOOL_ROI_ELLIPSE),
    ("Profile line", "Profile line — double click the line to plot the values along it", TOOL_PROFILE),
    ("Point marker", "Point marker — reads out the value at one pixel", TOOL_CROSS),
    None,
    ("Crop", "Crop — drag the part to keep", TOOL_CROP),
    ("Pick a colour", "Pick a colour from the picture to draw with", TOOL_EYEDROP),
    ("Delete an item", "Delete a drawn item — click it", TOOL_ERASER),
]

_SECTION_ACCENTS = {
    "images": "#2f6fd0", "display": "#7a4fc0", "measure": "#b0396b",
    "beam": "#0f7f8f", "filters": "#4d7a2a", "edit": "#2e9e5b",
    "combine": "#5a5f8f", "compare": "#d08a1e", "save": "#c0392b",
}

COMPARE_MODES = ["Off", "Side by side", "Blend", "Difference"]

#  What "Save all…" and the animation can be written as.
_SAVE_FORMATS = ("PNG", "TIFF", "JPEG")
_FORMAT_SUFFIX = {"PNG": "png", "TIFF": "tiff", "JPEG": "jpg"}


class WorkshopWidget(QWidget):
    """Workshop tab — receives images, shows them, measures them, annotates them.

    Display settings live in each slot's _ViewSettings and are re-applied on every
    render, so nothing on this panel can damage the picture. Only the Edit section and
    the Crop tool change pixels, and every one of those steps is undoable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: "list[_WorkshopSlot]" = []
        self._active = -1
        self._ref = -1
        self._last_save_dir: "Path | None" = None
        self._sync_guard = False
        #  Every window this panel can open is kept and reused, so a second press
        #  raises the one already on screen instead of stacking another copy of it.
        self._profile_dlg: "ProfileDialog | None" = None
        self._results_dlg: "_TableDialog | None" = None
        self._hist_dlg: "_TableDialog | None" = None
        self._beam_dlg: "_TableDialog | None" = None
        self._radial_dlg: "_CurveDialog | None" = None
        self._encircled_dlg: "_CurveDialog | None" = None
        self._play_dlg: "_PlayDialog | None" = None
        self._save_task_busy = False

        self._raw_sig = _RawSignals()
        self._raw_sig.done.connect(self._on_raw_loaded)
        self._write_sig = _WriteSignals()
        self._write_sig.done.connect(self._on_write_done)

        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(40)
        self._view_timer.timeout.connect(self._apply_view_now)

        self._ui_state = self._load_ui_state()
        #  The pixel size a newly opened image starts with. There is no on / off: every
        #  length is reported in pixels AND in real size, so a scale can only help. The
        #  Basler pitch is the starting point and whatever is typed over it is kept for
        #  the next image and the next session.
        try:
            self._sensor_default_um = float(self._ui_state.get("sensor_um",
                                                               BASLER_PIXEL_UM))
        except (TypeError, ValueError):
            self._sensor_default_um = BASLER_PIXEL_UM
        if not self._sensor_default_um > 0:
            self._sensor_default_um = BASLER_PIXEL_UM
        self._build_ui()
        self._update_slot_list()
        self._set_tool(TOOL_PAN)
        self._update_enabled()
        self._keys_dlg: "QDialog | None" = None
        self._shortcuts: list = []
        self._install_shortcuts()

    # ── persisted section state ───────────────────────────────────

    def _load_ui_state(self) -> dict:
        try:
            with open(_UI_STATE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_ui_state(self):
        try:
            _UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_UI_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._ui_state, f)
        except Exception:
            pass

    def _on_section_toggled(self, key: str, expanded: bool):
        self._ui_state[f"sec_{key}"] = bool(expanded)
        self._save_ui_state()

    def _add_section(self, key: str, title: str, default_expanded: bool = True):
        cls = _section_cls()
        sec = cls(title, key, self._ui_state.get(f"sec_{key}", default_expanded),
                  accent=_SECTION_ACCENTS.get(key, "#4a78c0"))
        sec.toggled.connect(self._on_section_toggled)
        self._panel_layout.addWidget(sec)
        return sec

    # ─────────────────────────────────────────────────── UI build ─

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        root.addWidget(self._build_tool_strip())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel = QWidget()
        self._panel_layout = QVBoxLayout(panel)
        self._panel_layout.setContentsMargins(2, 2, 6, 2)
        self._panel_layout.setSpacing(2)

        self._build_images_section()
        self._build_display_section()
        self._build_measure_section()
        self._build_beam_section()
        self._build_filters_section()
        self._build_edit_section()
        self._build_combine_section()
        self._build_compare_section()
        self._build_save_section()
        self._panel_layout.addStretch(1)
        panel_scroll.setWidget(panel)

        self._canvas = WorkshopCanvas()
        self._canvas.image_changed.connect(self._on_image_changed)
        self._canvas.annots_changed.connect(self._on_annots_changed)
        self._canvas.color_picked.connect(self._on_color_picked)
        self._canvas.cursor_moved.connect(self._on_cursor_moved)
        self._canvas.selection_changed.connect(self._on_selection_changed)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.status.connect(self._say)
        self._canvas.profile_requested.connect(self._show_profile)
        self._canvas.files_dropped.connect(self.open_files)
        self._canvas.tool_requested.connect(self._set_tool)
        self._canvas._menu_request = self._canvas_menu

        split.addWidget(panel_scroll)
        split.addWidget(self._canvas)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 900])
        root.addWidget(split, 1)

        self._status_lbl = QLabel("No images. Use “➤ Workshop” in Image Finder, "
                                  "Image Slider or Shot Finder, or drop a file here.")
        self._status_lbl.setStyleSheet(
            "QLabel { background: #eef1f5; border: 1px solid #d5dae1; border-radius: 3px;"
            " padding: 3px 6px; color: #1c2530; font-size: 11px; }")
        root.addWidget(self._status_lbl)

    # ---- tool strip ----------------------------------------------------------

    def _build_tool_strip(self) -> QWidget:
        box = QFrame()
        box.setStyleSheet("QFrame { background: #f0f2f5; border: 1px solid #d5dae1;"
                          " border-radius: 4px; }")
        outer = QVBoxLayout(box)
        outer.setContentsMargins(6, 5, 6, 5)
        outer.setSpacing(5)

        row1 = QHBoxLayout(); row1.setSpacing(2)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons = {}
        for entry in _TOOL_STRIP:
            if entry is None:
                sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color: #c3c9d2;")
                row1.addSpacing(4)
                row1.addWidget(sep)
                row1.addSpacing(4)
                continue
            name, tip, tool = entry
            b = QToolButton()
            b.setIcon(tool_icon(tool))
            b.setIconSize(QSize(_ICON_PX, _ICON_PX))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setToolTip(tip)
            # The button shows no words, so the short name is what a screen
            # reader has to go on.
            b.setAccessibleName(name)
            b.setCheckable(True)
            b.setFixedSize(32, 30)
            b.setStyleSheet(_TOOLBTN_QSS)
            b.clicked.connect(lambda _c, t=tool: self._set_tool(t))
            self._tool_group.addButton(b)
            self._tool_buttons[tool] = b
            row1.addWidget(b)
        row1.addStretch(1)
        outer.addLayout(row1)

        row2 = QHBoxLayout(); row2.setSpacing(5)
        self._draw_color = QColor(255, 0, 0)
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(26, 24)
        self._color_btn.setToolTip("Colour used for drawing — click to change")
        self._color_btn.clicked.connect(self._pick_draw_color)
        self._update_color_btn()
        row2.addWidget(self._color_btn)

        row2.addWidget(_small_label("Line"))
        self._line_sl = _size_slider(
            1, 60, 2, "Thickness of lines and outlines. With something selected it "
                      "changes that item.")
        row2.addWidget(self._line_sl)
        self._line_val = _value_label("2")
        row2.addWidget(self._line_val)
        self._line_prev = _StrokePreview(False, 60,
                                        "How thick a straight line will come out")
        row2.addWidget(self._line_prev)

        row2.addWidget(_small_label("Brush"))
        self._brush_sl = _size_slider(1, 200, 6, "Thickness of the freehand line")
        row2.addWidget(self._brush_sl)
        self._brush_val = _value_label("6", 30)
        row2.addWidget(self._brush_val)
        self._brush_prev = _StrokePreview(True, 200,
                                         "How thick a freehand stroke will come out")
        row2.addWidget(self._brush_prev)

        # The big true-size samples. They are separate windows, so they can be far
        # bigger than the row has height for.
        self._line_zoom = _StrokeZoomPopup(False, 60, self)
        self._brush_zoom = _StrokeZoomPopup(True, 200, self)
        for sl, pop in ((self._line_sl, self._line_zoom),
                        (self._brush_sl, self._brush_zoom)):
            sl.sliderPressed.connect(
                lambda s=sl, w=pop: w.show_for(s, s.value(), self._draw_color))
            sl.sliderReleased.connect(pop.hide)

        # Connected only once both samples exist, so the first drag cannot land in
        # _on_style_changed before there is anything to draw into.
        self._line_sl.valueChanged.connect(self._on_style_changed)
        self._brush_sl.valueChanged.connect(self._on_style_changed)

        row2.addWidget(_small_label("Text"))
        self._text_sb = QSpinBox(); self._text_sb.setRange(6, 200); self._text_sb.setValue(16)
        self._text_sb.setFixedWidth(52)
        self._text_sb.setToolTip("Size of the text. With a text item selected it "
                                 "changes that item.")
        self._text_sb.valueChanged.connect(self._on_style_changed)
        row2.addWidget(self._text_sb)

        self._fill_cb = QCheckBox("Fill")
        self._fill_cb.setStyleSheet(_CHECK_QSS)
        self._fill_cb.setToolTip("Draw rectangles, ellipses and polygons filled")
        self._fill_cb.toggled.connect(self._on_style_changed)
        row2.addWidget(self._fill_cb)

        row2.addStretch(1)
        outer.addLayout(row2)

        # The view and edit buttons sit on their own row: the three sliders and their
        # samples take the width the spin boxes used to leave over.
        row3 = QHBoxLayout(); row3.setSpacing(5)
        row3.addWidget(_small_label("Zoom"))
        self._zoom_sl = QSlider(Qt.Orientation.Horizontal)
        self._zoom_sl.setRange(0, _ZOOM_SLIDER_STEPS)
        self._zoom_sl.setValue(_zoom_pct_to_slider(100.0))
        self._zoom_sl.setFixedWidth(150)
        self._zoom_sl.setToolTip("Magnification — drag it, or use the buttons beside it")
        self._zoom_sl.valueChanged.connect(self._on_zoom_slider)
        row3.addWidget(self._zoom_sl)
        self._zoom_val = _value_label("100 %", 50)
        row3.addWidget(self._zoom_val)
        for text, icon, tip, fn in (
                ("", "plus", "Zoom in", lambda: self._canvas.zoom_in()),
                ("", "minus", "Zoom out", lambda: self._canvas.zoom_out()),
                ("Fit", "", "Fit the whole picture in the window",
                 lambda: self._canvas.fit_to_view()),
                ("1:1", "", "Show at true size, one screen pixel per image pixel",
                 lambda: self._canvas.zoom_reset())):
            b = _btn(text, tip, icon=icon)
            b.setFixedWidth(46 if text else 32)
            if icon:
                b.setAccessibleName(tip)
            b.clicked.connect(fn)
            row3.addWidget(b)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #c3c9d2;")
        row3.addWidget(sep3)

        # No setShortcut here: a button shortcut reaches the whole window and would
        # fire from the other tabs too. _install_shortcuts binds these keys instead,
        # switched off whenever the Workshop is not the tab in front.
        self._btn_undo = _btn("Undo", "Take back the last change", icon="undo")
        self._btn_undo.clicked.connect(self._undo)
        self._btn_redo = _btn("Redo", "Put back what was taken back", icon="redo")
        self._btn_redo.clicked.connect(self._redo)
        self._btn_clear_annots = _btn("Clear drawing",
                                      "Remove every drawn and measured item")
        self._btn_clear_annots.clicked.connect(lambda: self._canvas.clear_annots())
        self._btn_reset_src = _btn("Original", "Go back to the image as it arrived",
                                   danger=True, icon="reset")
        self._btn_reset_src.clicked.connect(self._reset_to_source)
        self._btn_keys = _btn("Shortcuts", "Show every keyboard shortcut")
        self._btn_keys.clicked.connect(self._show_shortcuts)
        for b in (self._btn_undo, self._btn_redo, self._btn_clear_annots,
                  self._btn_reset_src, self._btn_keys):
            row3.addWidget(b)
        row3.addStretch(1)
        outer.addLayout(row3)
        return box

    # ---- keyboard shortcuts --------------------------------------------------

    def _shortcut_table(self):
        """Every key this panel binds, in one place: the shortcuts themselves, the
        button tooltips and the help window are all built from this list, so they
        cannot drift apart. Fields: group, key, what it does, what to call, and the
        button whose tooltip should name the key ("" for none)."""
        c = self._canvas
        return [
            ("History", "Ctrl+Z", "Undo", self._undo, "Undo"),
            ("History", "Ctrl+Y", "Redo", self._redo, "Redo"),
            ("History", "Ctrl+Shift+Z", "Redo", self._redo, ""),
            ("History", "Ctrl+Alt+Z", "Back to the image as it arrived",
             self._reset_to_source, "Original"),
            ("History", "Ctrl+Shift+D", "Remove every drawn and measured item",
             c.clear_annots, "Clear drawing"),

            ("Images", "Ctrl+O", "Open a file", self._open_dialog, "Open file…"),
            ("Images", "Ctrl+Right", "Next image", lambda: self._step_slot(1), ""),
            ("Images", "Ctrl+Left", "Previous image", lambda: self._step_slot(-1), ""),
            ("Images", "Ctrl+D", "Copy this image, or the selected region, to a new one",
             self._duplicate_slot, "Duplicate"),
            ("Images", "Ctrl+V", "Bring in the image on the clipboard",
             self._paste_clipboard, "Paste"),

            ("Edit picture", "Ctrl+L", "Rotate left", lambda: self._rotate(1),
             "Rotate left"),
            ("Edit picture", "Ctrl+R", "Rotate right", lambda: self._rotate(-1),
             "Rotate right"),
            ("Edit picture", "Ctrl+Shift+R", "Turn upside down",
             lambda: self._rotate(2), "180°"),
            ("Edit picture", "Ctrl+H", "Mirror left to right", lambda: self._flip(1),
             "Flip across"),
            ("Edit picture", "Ctrl+Shift+H", "Mirror top to bottom",
             lambda: self._flip(0), "Flip down"),
            ("Edit picture", "Ctrl+E", "Resize", self._resize_dialog, "Resize…"),
            ("Edit picture", "Ctrl+Alt+R", "Rotate by any angle", self._rotate_free,
             "Rotate by angle…"),
            ("Edit picture", "Ctrl+Shift+E", "Straighten along the selected line",
             self._straighten, "Straighten"),
            ("Edit picture", "Ctrl+Alt+B", "Join blocks of pixels into one",
             self._bin_dialog, "Bin…"),
            ("Edit picture", "Ctrl+M", "Subtract the reference image",
             lambda: self._do_diff(False), "Subtract"),
            ("Edit picture", "Ctrl+Shift+M", "Difference against the reference image",
             lambda: self._do_diff(True), "Difference"),

            ("Filters", "Ctrl+Alt+M", "Median filter", self._filter_median, "Median…"),
            ("Filters", "Ctrl+Alt+L", "Blur", self._filter_blur, "Blur…"),
            ("Filters", "Ctrl+Alt+H", "Sharpen", self._filter_sharpen, "Sharpen…"),
            ("Filters", "Ctrl+Alt+G", "Remove the background",
             self._filter_background, "Remove background…"),

            ("Combine", "Ctrl+Alt+C", "Combine every open image into a new one",
             self._do_project, "Combine into a new image"),
            ("Compare", "Ctrl+K", "Next comparison view", self._cycle_compare, ""),

            ("Measure", "Ctrl+Shift+W", "Measure the whole picture",
             self._measure_whole, "Whole image"),
            ("Measure", "Ctrl+P", "Plot the profile line",
             lambda: self._show_profile(c.selected()), "Plot profile"),
            ("Measure", "Ctrl+T", "Every measurement in one table",
             self._show_results, "Results table…"),
            ("Measure", "Ctrl+J", "The histogram as numbers",
             self._show_histogram_numbers, "Histogram numbers…"),

            ("Beam", "Ctrl+B", "Beam report — width, roundness, tilt",
             self._show_beam_report, "Beam report…"),
            ("Beam", "Ctrl+Shift+B", "Radial profile", self._show_radial,
             "Radial profile"),

            ("View", "Ctrl++", "Zoom in", c.zoom_in, "Zoom in"),
            ("View", "Ctrl+=", "Zoom in", c.zoom_in, ""),
            ("View", "Ctrl+-", "Zoom out", c.zoom_out, "Zoom out"),
            ("View", "Ctrl+0", "Fit the whole picture in the window", c.fit_to_view,
             "Fit"),
            ("View", "Ctrl+1", "Show at true size, 1:1", c.zoom_reset, "1:1"),

            ("Save", "Ctrl+S", "Save as PNG", lambda: self._save("png"), "Save PNG…"),
            ("Save", "Ctrl+Shift+S", "Save as TIFF", lambda: self._save("tiff"),
             "Save TIFF…"),
            ("Save", "Ctrl+Alt+J", "Save as JPEG", lambda: self._save("jpg"),
             "Save JPEG…"),
            ("Save", "Ctrl+Alt+T", "Save the measured values as a 16-bit TIFF",
             self._save_data_tiff, "Save the values as TIFF…"),
            ("Save", "Ctrl+Shift+P", "Play the images", self._play_images, "Play…"),
            ("Save", "Ctrl+Shift+A", "Save an animation", self._save_animation,
             "Save animation…"),
            ("Save", "Ctrl+Shift+C", "Copy to the clipboard", self._copy_clipboard,
             "Copy"),

            ("Help", "F1", "This list", self._show_shortcuts, "Shortcuts"),
        ]

    def _install_shortcuts(self):
        """Window-wide keys, switched off while another tab is in front (see
        showEvent / hideEvent). Window scope rather than widget scope on purpose: after
        clicking the tab itself the focus sits on the tab bar, and a widget-scoped key
        would stay dead until the operator clicked inside the panel."""
        for group, keys, label, fn, btn in self._shortcut_table():
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)
        self._annotate_shortcut_tooltips()

    def _set_shortcuts_enabled(self, on: bool):
        for sc in self._shortcuts:
            sc.setEnabled(on)

    def showEvent(self, e):
        super().showEvent(e)
        self._set_shortcuts_enabled(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._set_shortcuts_enabled(False)
        # A separate window would otherwise stay on screen after the tab is left.
        self._line_zoom.hide()
        self._brush_zoom.hide()

    def _annotate_shortcut_tooltips(self):
        """Write the key into the tooltip of the button that does the same thing — a
        shortcut nobody can see is a shortcut nobody uses."""
        want = {}
        for group, keys, label, fn, btn in self._shortcut_table():
            if btn and btn not in want:
                want[btn] = keys
        for b in self.findChildren(QAbstractButton):
            keys = want.get(b.text()) or want.get(b.accessibleName())
            if keys:
                tip = b.toolTip()
                b.setToolTip(f"{tip}  ({keys})" if tip else keys)
        for keys, tool in _TOOL_KEYS.items():
            b = self._tool_buttons.get(tool)
            if b is not None:
                b.setToolTip(f"{b.toolTip()}  ({keys})")

    def _shortcut_groups(self):
        """The help window's contents: the table above with same-action keys merged
        onto one line, plus the keys the picture area handles by itself."""
        groups = []
        for group, keys, label, fn, btn in self._shortcut_table():
            if not groups or groups[-1][0] != group:
                groups.append((group, []))
            rows = groups[-1][1]
            for row in rows:
                if row[1] == label:
                    row[0].append(keys)
                    break
            else:
                rows.append([[keys], label])
        out = [(g, [(" or ".join(k), lbl) for k, lbl in rows]) for g, rows in groups]

        names = {tool: name for name, tip, tool in
                 (e for e in _TOOL_STRIP if e is not None)}
        out.append(("Pick a tool — click the picture first",
                    [(k, names.get(t, t)) for k, t in _TOOL_KEYS.items()]))
        out.append(("On the picture — click it first", [
            ("Space + drag", "Drag the picture around (also middle mouse or Ctrl + drag)"),
            ("+ / -", "Zoom in / out"),
            ("0", "Fit the whole picture in the window"),
            ("1", "Show at true size, 1:1"),
            ("Arrows", "Move the selected item, or the picture — Shift for bigger steps"),
            ("Shift + drag", "Ruler, line, arrow and profile line come out straight — "
                             "across, down or at 45°. Rectangles and ellipses come out "
                             "square. Works on an end point of a finished line too."),
            ("Delete", "Delete the selected item"),
            ("Esc", "Drop the selection, or the polygon being drawn"),
        ]))
        return out

    def _show_shortcuts(self):
        if self._keys_dlg is not None:
            self._keys_dlg.show()
            self._keys_dlg.raise_()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Keyboard shortcuts")
        dlg.setStyleSheet("QDialog { background: #ffffff; }")
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setStyleSheet("QWidget { background: #ffffff; }")
        grid = QGridLayout(body)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)

        row = 0
        for group, entries in self._shortcut_groups():
            head = QLabel(group)
            head.setStyleSheet(
                "QLabel { color: #1c2530; font-size: 12px; font-weight: bold;"
                " padding: 8px 0 2px 0; }")
            grid.addWidget(head, row, 0, 1, 2)
            row += 1
            for keys, label in entries:
                k = QLabel(keys)
                k.setStyleSheet(
                    "QLabel { color: #1c2530; font-size: 11px;"
                    " font-family: Consolas, 'Courier New', monospace;"
                    " background: #f0f2f5; border: 1px solid #d5dae1;"
                    " border-radius: 3px; padding: 1px 5px; }")
                t = QLabel(label)
                t.setWordWrap(True)
                t.setStyleSheet("QLabel { color: #1c2530; font-size: 11px; }")
                grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignLeft
                               | Qt.AlignmentFlag.AlignTop)
                grid.addWidget(t, row, 1)
                row += 1
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(row, 1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        brow = QHBoxLayout()
        brow.addStretch(1)
        b_close = _btn("Close", "")
        b_close.clicked.connect(dlg.close)
        brow.addWidget(b_close)
        outer.addLayout(brow)

        dlg.resize(460, 620)
        self._keys_dlg = dlg
        dlg.show()

    # ---- right-click menu on the picture -------------------------------------

    _MENU_QSS = (
        "QMenu { background: #ffffff; color: #16202c; border: 1px solid #c3c9d2;"
        " padding: 3px; }"
        "QMenu::item { padding: 4px 22px 4px 12px; }"
        "QMenu::item:selected { background: #cfe0f7; color: #10243c; }"
        "QMenu::item:disabled { color: #9aa3ad; }"
        "QMenu::separator { height: 1px; background: #dfe4ea; margin: 3px 6px; }"
    )

    def _canvas_menu(self, global_pos, img_pt: QPointF):
        """The commands worth having under the cursor. Built fresh each time so that
        what is greyed out is right for this moment."""
        slot = self._slot()
        sel = self._canvas.selected()
        menu = QMenu(self)
        menu.setStyleSheet(self._MENU_QSS)

        def act(text: str, fn, enabled: bool = True):
            a = menu.addAction(text)
            a.setEnabled(bool(enabled))
            a.triggered.connect(fn)
            return a

        act("Undo", self._undo, slot is not None and bool(slot.undo_stack))
        act("Redo", self._redo, slot is not None and bool(slot.redo_stack))
        menu.addSeparator()
        act("Measure the whole picture", self._measure_whole, slot is not None)
        act("Results table…", self._show_results, slot is not None)
        act("Beam report…", self._show_beam_report,
            slot is not None and wk_beam is not None)
        act("Plot profile", lambda: self._show_profile(self._canvas.selected()),
            slot is not None and any(a.kind == A_PROFILE for a in slot.annots))
        menu.addSeparator()
        if slot is not None:
            ix, iy = int(img_pt.x()), int(img_pt.y())
            v = slot.value_at(ix, iy)
            if v is not None:
                act(f"Copy the value at {ix}, {iy}",
                    lambda: QGuiApplication.clipboard().setText(f"{v[1]:.4f}"))
                act(f"Put a point marker at {ix}, {iy}",
                    lambda: self._canvas.add_annot(
                        _Annot(A_CROSS, [[float(ix), float(iy)]],
                               self._draw_color.name(), self._line_sl.value())))
        act("Delete the selected item", self._canvas.delete_selected, sel is not None)
        act("Clear the whole drawing", self._canvas.clear_annots,
            slot is not None and bool(slot.annots))
        menu.addSeparator()
        act("Copy the picture", self._copy_clipboard, slot is not None)
        act("Save PNG…", lambda: self._save("png"), slot is not None)
        act("Duplicate", self._duplicate_slot, slot is not None)
        menu.addSeparator()
        act("Fit the whole picture", self._canvas.fit_to_view, slot is not None)
        act("True size, 1:1", self._canvas.zoom_reset, slot is not None)
        act("Shortcuts…", self._show_shortcuts)
        menu.exec(global_pos)

    def _step_slot(self, delta: int):
        if len(self._slots) < 2:
            return
        self._activate_slot((self._active + delta) % len(self._slots))

    def _cycle_compare(self):
        self._compare_cb.setCurrentIndex(
            (self._compare_cb.currentIndex() + 1) % self._compare_cb.count())

    def _measure_whole(self):
        self._canvas.select_index(-1)
        self._refresh_measure()

    # ---- sections ------------------------------------------------------------

    def _build_images_section(self):
        sec = self._add_section("images", "Images", True)
        lay = sec.body_layout

        self._info_lbl = QLabel("—")
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setStyleSheet(
            "QLabel { font-size: 11px; color: #1c2530; background: #f7f7f7;"
            " border: 1px solid #dcdcdc; border-radius: 3px; padding: 4px; }")
        lay.addWidget(self._info_lbl)

        self._slot_list = QComboBox()
        self._slot_list.setToolTip("Which image the tools work on")
        self._slot_list.currentIndexChanged.connect(self._on_slot_selected)
        lay.addWidget(self._slot_list)

        row = QHBoxLayout()
        b_open = _btn("Open file…", "Open one or more images from disk")
        b_open.clicked.connect(self._open_dialog)
        b_rm = _btn("Remove", "Take this image out of the Workshop", danger=True)
        b_rm.clicked.connect(self._remove_slot)
        b_clear = _btn("Clear all", "Take every image out of the Workshop", danger=True)
        b_clear.clicked.connect(self._clear_all)
        for b in (b_open, b_rm, b_clear):
            row.addWidget(b)
        lay.addLayout(row)

        row_b = QHBoxLayout()
        self._btn_dup = _btn(
            "Duplicate",
            "A copy of this image as a new one. With a region selected, only that "
            "part is copied — the way to cut a detail out without touching the "
            "original.")
        self._btn_dup.clicked.connect(self._duplicate_slot)
        b_paste = _btn("Paste", "Bring in the image on the clipboard")
        b_paste.clicked.connect(self._paste_clipboard)
        self._btn_slider = _btn("Show in Image Slider",
                                "Open the folder this frame came from in the Image "
                                "Slider")
        self._btn_slider.clicked.connect(self._show_in_slider)
        row_b.addWidget(self._btn_dup); row_b.addWidget(b_paste)
        lay.addLayout(row_b)
        lay.addWidget(self._btn_slider)

        # The reference lives here, next to the active image, because both the
        # subtraction in Edit and everything in Compare work against it.
        lay.addWidget(_small_label("Reference image"))
        self._ref_combo = QComboBox()
        self._ref_combo.setToolTip("Used by subtraction and by the comparison views")
        self._ref_combo.currentIndexChanged.connect(self._on_ref_changed)
        lay.addWidget(self._ref_combo)
        b_ref_file = _btn("Load reference from file…",
                          "Bring in an image from disk and use it as the reference")
        b_ref_file.clicked.connect(self._load_ref_from_file)
        lay.addWidget(b_ref_file)

    def _build_display_section(self):
        sec = self._add_section("display", "Display", True)
        lay = sec.body_layout

        note = _small_label("These settings only change how the image looks. "
                            "The picture data stays as it arrived.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #4a5566; font-size: 10px; font-style: italic;")
        lay.addWidget(note)

        lay.addWidget(_small_label("Palette"))
        self._palette_cb = QComboBox()
        for name in PALETTE_NAMES:
            self._palette_cb.addItem(name)
        self._palette_cb.setToolTip("False-colour table used to show the values")
        self._palette_cb.currentTextChanged.connect(self._on_palette_changed)
        lay.addWidget(self._palette_cb)

        self._hist = _HistogramWidget()
        self._hist.setToolTip("Where the values are. Drag the black and the red line to "
                              "set which values are shown; double-click to reset.")
        self._hist.changed.connect(self._on_hist_changed)
        lay.addWidget(self._hist)

        self._contrast_sl, self._contrast_val, self._cb_auto_contrast = self._slider_row(
            lay, "Contrast", -127, 127, 0,
            "Spreads the values above the background — a multiplying gain",
            auto_tip="Put the black and white points on the data automatically "
                     "(0.5 % … 99.5 %) and park them on the histogram")
        self._bright_sl, self._bright_val, self._cb_auto_bright = self._slider_row(
            lay, "Brightness", -255, 255, 0,
            "Adds a constant to every value — moves the whole picture up or down",
            auto_tip="Set the brightness from the picture itself")
        self._gamma_sl, self._gamma_val, self._cb_auto_gamma = self._slider_row(
            lay, "Gamma", GAMMA_SLIDER_MIN, GAMMA_SLIDER_MAX, GAMMA_SLIDER_NEUTRAL,
            "Bends the scale so faint detail comes up without clipping the bright parts",
            auto_tip="Choose the bend from the picture itself")

        b_reset = _btn("Reset all display settings",
                       "Back to the plain picture as it arrived")
        b_reset.clicked.connect(self._reset_view)
        lay.addWidget(b_reset)

    def _slider_row(self, lay, name: str, lo: int, hi: int, start: int, tip: str,
                    auto_tip: str = ""):
        """One labelled slider with a live number, a reset arrow and an Auto box.
        Auto parks the slider on the value it computed, so the control always shows
        what is actually on screen."""
        head = QHBoxLayout(); head.setSpacing(4)
        head.addWidget(_small_label(name))
        val = QLabel(str(start))
        val.setStyleSheet("color: #111; font-size: 11px; font-weight: 600;")
        val.setMinimumWidth(34)
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(val)
        head.addStretch(1)
        cb = QCheckBox("Auto")
        cb.setStyleSheet(_CHECK_QSS)
        if auto_tip:
            cb.setToolTip(auto_tip)
        head.addWidget(cb)
        b_reset = _btn("", "Back to neutral", icon="reset")
        b_reset.setAccessibleName("Back to neutral")
        b_reset.setFixedWidth(30)
        head.addWidget(b_reset)
        lay.addLayout(head)

        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(start)
        sl.setToolTip(tip)
        lay.addWidget(sl)

        sl.valueChanged.connect(self._on_view_control)
        cb.toggled.connect(self._on_view_control)
        b_reset.clicked.connect(lambda: (cb.setChecked(False), sl.setValue(start)))
        return sl, val, cb

    def _build_measure_section(self):
        sec = self._add_section("measure", "Measure", False)
        lay = sec.body_layout

        self._measure_src_lbl = _small_label("—")
        self._measure_src_lbl.setWordWrap(True)
        self._measure_src_lbl.setStyleSheet("color: #4a5566; font-size: 10px;")
        lay.addWidget(self._measure_src_lbl)

        grid = QGridLayout(); grid.setSpacing(3)
        self._cells = {}
        for i, (key, caption) in enumerate([
                ("min", "Minimum"), ("max", "Maximum"), ("mean", "Mean"),
                ("std", "Std deviation"), ("sum", "Sum"), ("count", "Pixels"),
                ("centroid", "Centre of mass"), ("area", "Area")]):
            cell = _StatCell(caption)
            self._cells[key] = cell
            grid.addWidget(cell, i // 2, i % 2)
        lay.addLayout(grid)

        row = QHBoxLayout()
        b_whole = _btn("Whole image", "Measure the whole picture instead of a region")
        b_whole.clicked.connect(self._measure_whole)
        b_profile = _btn("Plot profile", "Plot the values along the selected profile line")
        b_profile.clicked.connect(lambda: self._show_profile(self._canvas.selected()))
        row.addWidget(b_whole); row.addWidget(b_profile)
        lay.addLayout(row)

        self._cb_keep_regions = QCheckBox("Keep several regions")
        self._cb_keep_regions.setStyleSheet(_CHECK_QSS)
        self._cb_keep_regions.setToolTip(
            "Off: a new region replaces the old one, so the numbers above always "
            "belong to the region on screen.\nOn: regions pile up — the cells show "
            "whichever one is selected, and the results table lists them all.")
        self._cb_keep_regions.toggled.connect(self._on_keep_regions)
        lay.addWidget(self._cb_keep_regions)

        row2 = QHBoxLayout()
        b_table = _btn("Results table…",
                       "Every region, ruler, angle and point on this image in one "
                       "table, ready to copy or save as CSV")
        b_table.clicked.connect(self._show_results)
        b_hist = _btn("Histogram numbers…",
                      "The numbers behind the histogram: how many pixels at each "
                      "intensity")
        b_hist.clicked.connect(self._show_histogram_numbers)
        row2.addWidget(b_table); row2.addWidget(b_hist)
        lay.addLayout(row2)

        #  Scale. One number decides it all: how much of the object one pixel covers.
        #  It is always in force — there is no "measuring in pixels" mode, because every
        #  length is reported in pixels AND in real size side by side, so nothing is
        #  lost by knowing the size and everything is lost by having to switch it on.
        lay.addWidget(_small_label("Scale"))
        srow_px = QHBoxLayout()
        self._sensor_guard = False          # True while the panel is writing the controls
        srow_px.addWidget(_small_label("One pixel is"))
        self._sensor_um_sb = QDoubleSpinBox()
        self._sensor_um_sb.setRange(0.001, 10000.0)
        self._sensor_um_sb.setDecimals(3)
        self._sensor_um_sb.setSingleStep(0.1)
        self._sensor_um_sb.setValue(self._sensor_default_um)
        self._sensor_um_sb.setSuffix(" µm")
        self._sensor_um_sb.setFixedWidth(96)
        self._sensor_um_sb.setToolTip(
            f"How much of the object one pixel covers. The Basler cameras here have "
            f"{BASLER_PIXEL_UM} µm pixels, which is what this starts at.\n"
            "That is the size AT THE SENSOR: with a lens or a magnifier in front, put "
            "in the size the picture really has on the object — or measure it with "
            "“Set scale…”.\n"
            "A camera acquiring with 2×2 binning stores one pixel per four, so double "
            "it there.\nThe value stays for the next image you open.")
        self._sensor_um_sb.valueChanged.connect(self._on_sensor_um)
        srow_px.addWidget(self._sensor_um_sb)
        srow_px.addStretch(1)
        lay.addLayout(srow_px)

        srow = QHBoxLayout()
        self._scale_lbl = _small_label("—")
        self._scale_lbl.setWordWrap(True)
        srow.addWidget(self._scale_lbl, 1)
        lay.addLayout(srow)

        srow2 = QHBoxLayout()
        b_setscale = _btn("Set scale…",
                          "Draw a ruler over something of a known size, select it, "
                          "then enter that size here — the pixel size is worked out "
                          "from it")
        b_setscale.clicked.connect(self._set_scale)
        b_clrscale = _btn(f"Camera ({BASLER_PIXEL_UM} µm)",
                          "Back to the camera's own pixel size")
        b_clrscale.clicked.connect(self._clear_scale)
        srow2.addWidget(b_setscale); srow2.addWidget(b_clrscale)
        lay.addLayout(srow2)

        srow3 = QHBoxLayout()
        self._btn_scalebar = _btn(
            "Add scale bar…",
            "Draw a bar of a known length into the picture, so a saved copy carries "
            "its own scale. Drag it anywhere with Select.")
        self._btn_scalebar.clicked.connect(self._add_scale_bar)
        b_nobar = _btn("Remove bar", "Take the scale bar off again")
        b_nobar.clicked.connect(self._remove_scale_bar)
        srow3.addWidget(self._btn_scalebar); srow3.addWidget(b_nobar)
        lay.addLayout(srow3)

    # ---- beam ---------------------------------------------------------------

    def _build_beam_section(self):
        sec = self._add_section("beam", "Beam", False)
        lay = sec.body_layout
        note = _small_label(
            "Spot size and shape, measured on the selected region or on the whole "
            "picture. A background is taken off first — a width is meaningless "
            "without one, because every pixel of background counts double at the "
            "edge of the frame.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #4a5566; font-size: 10px; font-style: italic;")
        lay.addWidget(note)

        brow = QHBoxLayout()
        brow.addWidget(_small_label("Background"))
        self._beam_base_sb = QDoubleSpinBox()
        self._beam_base_sb.setRange(0.0, 50.0)
        self._beam_base_sb.setDecimals(1)
        self._beam_base_sb.setSingleStep(1.0)
        self._beam_base_sb.setValue(5.0)
        self._beam_base_sb.setSuffix(" %")
        self._beam_base_sb.setFixedWidth(76)
        self._beam_base_sb.setToolTip(
            "The level taken as background: this percentile of the area being "
            "measured. 5 % suits a frame with a dark surround; set 0 % to measure "
            "the values as they are.")
        brow.addWidget(self._beam_base_sb)
        brow.addStretch(1)
        lay.addLayout(brow)

        grid = QGridLayout(); grid.setSpacing(3)
        ops = [("Beam report…", "Centre, D4σ width, FWHM, roundness and the tilt of "
                                "the long axis, in one table", self._show_beam_report),
               ("Radial profile", "Average value against distance from the centre of "
                                  "mass — the shape of the spot with the noise "
                                  "averaged out", self._show_radial),
               ("Encircled energy", "How much of the signal sits inside a circle, "
                                    "against the radius of that circle",
                self._show_encircled),
               ("Mark the centre", "Put a point marker on the centre of mass",
                self._mark_centroid)]
        for i, (text, tip, fn) in enumerate(ops):
            b = _btn(text, tip)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

    # ---- filters ------------------------------------------------------------

    def _build_filters_section(self):
        sec = self._add_section("filters", "Filters", False)
        lay = sec.body_layout
        warn = _small_label(
            "These change the picture itself, and the measured intensity behind it the "
            "same way, "
            "so what you measure still matches what you see. Every step can be undone.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #4a5566; font-size: 10px; font-style: italic;")
        lay.addWidget(warn)

        grid = QGridLayout(); grid.setSpacing(3)
        ops = [("Median…", "Replaces each pixel by the middle value of its "
                           "neighbours — the filter for hot pixels and speckle",
                self._filter_median),
               ("Blur…", "Gaussian blur, for noise that is spread out rather than "
                         "in single pixels", self._filter_blur),
               ("Sharpen…", "Adds back what a blur would remove", self._filter_sharpen),
               ("Edges", "Bright where the picture changes fastest",
                self._filter_edges),
               ("Remove background…", "Take off a constant level, a sloping plane, a "
                                      "curved surface or everything larger than a "
                                      "rolling ball", self._filter_background)]
        for i, (text, tip, fn) in enumerate(ops):
            b = _btn(text, tip)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        self._filter_buttons = [grid.itemAt(i).widget() for i in range(grid.count())]

    def _build_edit_section(self):
        sec = self._add_section("edit", "Edit picture", False)
        lay = sec.body_layout
        warn = _small_label("These change the picture itself. Every step can be undone.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #4a5566; font-size: 10px; font-style: italic;")
        lay.addWidget(warn)

        grid = QGridLayout(); grid.setSpacing(3)
        ops = [("Rotate left", "rotate_left", "Turn 90° anticlockwise",
                lambda: self._rotate(1)),
               ("Rotate right", "rotate_right", "Turn 90° clockwise",
                lambda: self._rotate(-1)),
               ("180°", "", "Turn upside down", lambda: self._rotate(2)),
               ("Resize…", "", "Scale the picture to a different size",
                self._resize_dialog),
               ("Flip across", "flip_h", "Mirror left to right", lambda: self._flip(1)),
               ("Flip down", "flip_v", "Mirror top to bottom", lambda: self._flip(0))]
        for i, (text, icon, tip, fn) in enumerate(ops):
            b = _btn(text, tip, icon=icon)
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        grid2 = QGridLayout(); grid2.setSpacing(3)
        ops2 = [("Rotate by angle…", "", "Turn by any angle. The picture grows so "
                                        "nothing is cut off.", self._rotate_free),
                ("Straighten", "", "Turn so that the selected straight line, ruler or "
                                   "arrow becomes horizontal", self._straighten),
                ("Bin…", "", "Join blocks of pixels into one — 2 × 2 or more. Averaging "
                             "keeps the scale, adding is what a detector does when it "
                             "is binned on the chip.", self._bin_dialog)]
        for i, (text, icon, tip, fn) in enumerate(ops2):
            b = _btn(text, tip, icon=icon)
            b.clicked.connect(fn)
            grid2.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid2)

        lay.addWidget(_small_label("Against the reference image"))
        srow = QHBoxLayout()
        b_sub = _btn("Subtract", "Active minus reference, negatives cut to zero")
        b_sub.clicked.connect(lambda: self._do_diff(False))
        b_abs = _btn("Difference", "How far apart the two are, in either direction")
        b_abs.clicked.connect(lambda: self._do_diff(True))
        srow.addWidget(b_sub); srow.addWidget(b_abs)
        lay.addLayout(srow)

    # ---- combine ------------------------------------------------------------

    def _build_combine_section(self):
        sec = self._add_section("combine", "Combine images", False)
        lay = sec.body_layout
        note = _small_label(
            "Makes a NEW image out of the ones already open. Nothing existing is "
            "changed.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #4a5566; font-size: 10px; font-style: italic;")
        lay.addWidget(note)

        lay.addWidget(_small_label("Across every open image"))
        self._project_cb = QComboBox()
        for m in (wk_ops.PROJECT_MODES if wk_ops is not None
                  else ("Average", "Maximum", "Sum", "Minimum")):
            self._project_cb.addItem(m)
        self._project_cb.setToolTip(
            "Average — ten shots of the same thing, with the noise divided by about "
            "three.\nMaximum — the envelope: everywhere the beam has been.\n"
            "Sum — total signal.\nMinimum — what is there on every single frame.")
        lay.addWidget(self._project_cb)
        self._btn_project = _btn("Combine into a new image",
                                 "Uses every image in the Workshop, over the area they "
                                 "all cover")
        self._btn_project.clicked.connect(self._do_project)
        lay.addWidget(self._btn_project)

        lay.addWidget(_small_label("Active image with the reference"))
        self._btn_merge = _btn(
            "Merge as red and green",
            "The active image in red, the reference in green. A shift between them "
            "shows as a red edge on one side and a green edge on the other.")
        self._btn_merge.clicked.connect(self._do_merge)
        lay.addWidget(self._btn_merge)

    def _build_compare_section(self):
        sec = self._add_section("compare", "Compare", False)
        lay = sec.body_layout
        self._compare_cb = QComboBox()
        self._compare_cb.setToolTip("Show the active image against the reference image")
        for m in COMPARE_MODES:
            self._compare_cb.addItem(m)
        self._compare_cb.currentIndexChanged.connect(self._update_compare)
        lay.addWidget(self._compare_cb)

        self._blend_sl = QSlider(Qt.Orientation.Horizontal)
        self._blend_sl.setRange(0, 100)
        self._blend_sl.setValue(50)
        self._blend_sl.setToolTip("0 % shows only the reference, 100 % only the active image")
        self._blend_sl.valueChanged.connect(self._update_compare)
        lay.addWidget(self._blend_sl)
        self._blend_lbl = _small_label("Mix: 50 %")
        lay.addWidget(self._blend_lbl)

    def _build_save_section(self):
        sec = self._add_section("save", "Save", True)
        lay = sec.body_layout
        self._cb_burn = QCheckBox("Include the drawing and measurements")
        self._cb_burn.setStyleSheet(_CHECK_QSS)
        self._cb_burn.setChecked(True)
        lay.addWidget(self._cb_burn)

        row = QHBoxLayout()
        b_png = _btn("Save PNG…", "Write what you see as a PNG file")
        b_png.clicked.connect(lambda: self._save("png"))
        b_tif = _btn("Save TIFF…", "Write what you see as a TIFF file")
        b_tif.clicked.connect(lambda: self._save("tiff"))
        b_jpg = _btn("Save JPEG…", "Write what you see as a JPEG file — smaller, but "
                                   "it throws detail away; never save data as JPEG")
        b_jpg.clicked.connect(lambda: self._save("jpg"))
        row.addWidget(b_png); row.addWidget(b_tif); row.addWidget(b_jpg)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        b_all = _btn("Save all…", "Write every image in the Workshop into one folder")
        b_all.clicked.connect(self._save_all)
        b_clip = _btn("Copy", "Put what you see on the clipboard")
        b_clip.clicked.connect(self._copy_clipboard)
        row2.addWidget(b_all); row2.addWidget(b_clip)
        lay.addLayout(row2)

        self._btn_data_tiff = _btn(
            "Save the values as TIFF…",
            "The measured counts as a plain 16-bit TIFF: no palette, no display "
            "stretch, no drawing. This is the file to open in ImageJ or read from a "
            "script.")
        self._btn_data_tiff.clicked.connect(self._save_data_tiff)
        lay.addWidget(self._btn_data_tiff)

        lay.addWidget(_small_label("Animation from every open image"))
        arow = QHBoxLayout()
        arow.addWidget(_small_label("Frame time"))
        self._anim_ms = QSpinBox()
        self._anim_ms.setRange(20, 5000)
        self._anim_ms.setSingleStep(20)
        self._anim_ms.setValue(200)
        self._anim_ms.setSuffix(" ms")
        self._anim_ms.setFixedWidth(84)
        self._anim_ms.setToolTip("How long each image is shown")
        arow.addWidget(self._anim_ms)
        self._cb_anim_loop = QCheckBox("Repeat")
        self._cb_anim_loop.setStyleSheet(_CHECK_QSS)
        self._cb_anim_loop.setChecked(True)
        self._cb_anim_loop.setToolTip("Play the animation over and over")
        arow.addWidget(self._cb_anim_loop)
        arow.addStretch(1)
        lay.addLayout(arow)

        arow2 = QHBoxLayout()
        self._btn_play = _btn("Play…", "Look at the animation before saving it")
        self._btn_play.clicked.connect(self._play_images)
        self._btn_anim = _btn("Save animation…",
                              "Write an animated GIF, PNG or WebP. Images of different "
                              "sizes are centred on black rather than stretched.")
        self._btn_anim.clicked.connect(self._save_animation)
        arow2.addWidget(self._btn_play); arow2.addWidget(self._btn_anim)
        lay.addLayout(arow2)

        lay.addWidget(_small_label("Session — the drawing, the regions and the scale"))
        srow = QHBoxLayout()
        b_ssave = _btn("Save session…",
                       "Write down everything drawn on every open image, so the work "
                       "is not lost when the Workshop is closed")
        b_ssave.clicked.connect(self._save_session)
        b_sload = _btn("Load session…",
                       "Re-open the images from a saved session and put the drawing "
                       "back on them")
        b_sload.clicked.connect(self._load_session)
        srow.addWidget(b_ssave); srow.addWidget(b_sload)
        lay.addLayout(srow)


    # ─────────────────────────────────────────────── Public API ───

    def receive_image(self, arr: np.ndarray, label: str = "",
                      source_path: "Path | None" = None,
                      raw: "np.ndarray | None" = None,
                      full_scale: "float | None" = None, camera: str = ""):
        """Called by Image Finder / Image Slider / Shot Finder.

        `arr` is the 8-bit picture as the sender rendered it. The optional `raw`,
        `full_scale` and `camera` let a sender hand over the native-precision data
        directly; without them the Workshop reads the source file itself in the
        background, so the existing three senders need no change."""
        if arr.ndim == 2:
            base = np.ascontiguousarray(arr)
        elif arr.shape[2] == 4:
            base = np.ascontiguousarray(arr[:, :, :3])
        else:
            base = np.ascontiguousarray(arr)
        base = base.astype(np.uint8, copy=False)

        slot = _WorkshopSlot(label=label or "image", base=base, source_base=base,
                             source_path=Path(source_path) if source_path else None,
                             raw=raw, source_raw=raw, full_scale=full_scale,
                             camera=camera)
        if raw is None:
            slot.raw_note = "reading the source file…"
            QThreadPool.globalInstance().start(_RawLoadTask(slot, self._raw_sig))
        self._apply_default_sensor_scale(slot)
        self._slots.append(slot)
        self._update_slot_list()
        self._activate_slot(len(self._slots) - 1)
        self._say(f"Received: {label}  ({base.shape[1]} × {base.shape[0]} px)")

    def open_files(self, paths):
        """Open image files straight into the Workshop (Open button and drag & drop)."""
        added = 0
        for path in paths:
            p = Path(path)
            if p.suffix.lower() not in (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".bmp"):
                continue
            try:
                with _PilImg.open(str(p)) as im:
                    a = np.array(im.convert("RGB") if im.mode not in ("L", "RGB") else im)
                if a.dtype != np.uint8:
                    a = np.clip(a.astype(np.float32) / max(1.0, float(a.max())) * 255.0,
                                0, 255).astype(np.uint8)
                self.receive_image(a, p.name, source_path=p)
                added += 1
            except Exception as e:
                QMessageBox.warning(self, "Open error", f"Could not open {p.name}:\n{e}")
        if added:
            self._say(f"Opened {added} file(s).")

    def _open_dialog(self):
        # Several files at once, because dropping several onto the picture has always
        # worked and the button having its own one-at-a-time rule was just confusing.
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open images", str(self._last_save_dir or Path.home()),
            "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if paths:
            self.open_files(paths)

    def _duplicate_slot(self):
        """A copy as a new image — of the whole picture, or of the selected region.

        The copy carries the counts and the scale, so it can still be measured; it does
        not carry the drawing, because the point of duplicating is usually to get a
        clean detail out."""
        slot = self._slot()
        if slot is None:
            return
        sel = self._canvas.selected()
        base, raw, what = slot.base, slot.raw, "copy"
        px_per_mm = slot.px_per_mm
        if sel is not None and sel.kind in _REGION_KINDS:
            x0, y0, x1, y1 = sel.bbox()
            h, w = slot.base.shape[:2]
            ix0, iy0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
            ix1, iy1 = min(w, int(math.ceil(x1))), min(h, int(math.ceil(y1)))
            if ix1 - ix0 >= 2 and iy1 - iy0 >= 2:
                base = slot.base[iy0:iy1, ix0:ix1]
                raw = (slot.raw[iy0:iy1, ix0:ix1] if slot.measures_raw() else None)
                what = "region"
        new = self._add_derived_slot(
            np.ascontiguousarray(base.copy()), f"{slot.label[:44]} — {what}",
            raw=None if raw is None else np.ascontiguousarray(raw.copy()),
            full_scale=slot.full_scale, camera=slot.camera,
            note="" if raw is not None else slot.raw_note)
        new.source_path = slot.source_path
        new.px_per_mm = px_per_mm
        new.px_um = slot.px_um
        new.px_um_measured = slot.px_um_measured
        new.view = _ViewSettings(**vars(slot.view))
        self._after_change()
        self._say(f"Duplicated the {what}: {base.shape[1]} × {base.shape[0]} px. "
                  f"The original is untouched.")

    def _paste_clipboard(self):
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            self._say("There is no image on the clipboard.")
            return
        arr = _qimage_to_np(img)
        self._add_derived_slot(arr, "Pasted from the clipboard")
        self._after_change()
        self._say(f"Pasted {arr.shape[1]} × {arr.shape[0]} px from the clipboard.")

    def _show_in_slider(self):
        """Open the folder this frame came from in the Image Slider.

        The same public handoff the Finder and the Shot Finder use, so the Slider
        clears whatever it was doing first. The Workshop cannot push its edited pixels
        back — the Slider browses files on the share, and an edited frame is not one."""
        slot = self._slot()
        viewer = getattr(self, "_slider_ref", None)
        tabs = getattr(self, "_tab_widget", None)
        if slot is None or viewer is None:
            self._say("The Image Slider is not available from here.")
            return
        if slot.source_path is None:
            self._say("This image did not come from a file, so there is no folder to "
                      "open.")
            return
        folder = Path(slot.source_path).parent
        if not folder.is_dir():
            self._say(f"That folder is gone: {folder}")
            return
        ok = False
        recv = getattr(viewer, "receive_external_folder", None)
        try:
            if callable(recv):
                ok = bool(recv(folder, discrete=False, cam_name=slot.camera or None))
            elif hasattr(viewer, "open_folder_path"):
                viewer.open_folder_path(folder)
                ok = True
        except Exception as e:
            QMessageBox.warning(self, "Image Slider", f"Could not hand over:\n{e}")
            return
        if not ok:
            self._say(f"The Image Slider would not take that folder: {folder}")
            return
        if tabs is not None:
            idx = getattr(self, "_slider_tab_idx", None)
            if idx is None:
                idx = tabs.indexOf(viewer)
            if idx is not None and idx >= 0:
                tabs.setCurrentIndex(idx)
        self._say(f"Opened in the Image Slider: {folder.name}")

    # ──────────────────────────────────────────────────── Slots ───

    def _slot(self) -> "_WorkshopSlot | None":
        if 0 <= self._active < len(self._slots):
            return self._slots[self._active]
        return None

    def _update_slot_list(self):
        self._slot_list.blockSignals(True)
        self._ref_combo.blockSignals(True)
        cur = self._slot_list.currentIndex()
        self._slot_list.clear()
        self._ref_combo.clear()
        self._ref_combo.addItem("— none —")
        for i, s in enumerate(self._slots):
            short = s.label[:44] + ("…" if len(s.label) > 44 else "")
            self._slot_list.addItem(f"{i + 1}. {short}")
            self._ref_combo.addItem(f"{i + 1}. {short}")
        if 0 <= cur < len(self._slots):
            self._slot_list.setCurrentIndex(cur)
        elif self._slots:
            self._slot_list.setCurrentIndex(len(self._slots) - 1)
        if 0 <= self._ref < len(self._slots):
            self._ref_combo.setCurrentIndex(self._ref + 1)
        self._slot_list.blockSignals(False)
        self._ref_combo.blockSignals(False)

    def _on_slot_selected(self, idx: int):
        if 0 <= idx < len(self._slots):
            self._activate_slot(idx)

    def _activate_slot(self, idx: int):
        self._active = idx
        self._slot_list.blockSignals(True)
        self._slot_list.setCurrentIndex(idx)
        self._slot_list.blockSignals(False)
        self._canvas.set_slot(self._slot())
        self._sync_view_controls()
        self._update_compare()
        self._after_change()

    def _remove_slot(self):
        if not self._slots or self._active < 0:
            return
        self._slots.pop(self._active)
        self._ref = -1
        self._active = min(self._active, len(self._slots) - 1)
        self._update_slot_list()
        if self._slots:
            self._activate_slot(self._active)
        else:
            self._canvas.set_slot(None)
            self._info_lbl.setText("—")
            self._after_change()
        self._say("Image removed.")

    def _clear_all(self):
        if not self._slots:
            return
        if QMessageBox.question(self, "Clear all", "Remove all images from the Workshop?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._slots.clear()
        self._active = self._ref = -1
        self._update_slot_list()
        self._canvas.set_slot(None)
        self._info_lbl.setText("—")
        self._after_change()
        self._say("Workshop cleared.")

    def _on_raw_loaded(self, slot, raw, full_scale, camera, note):
        if slot not in self._slots:
            return
        slot.raw = raw
        slot.source_raw = raw
        slot.full_scale = full_scale
        slot.camera = camera or slot.camera
        slot.raw_note = note
        if slot is self._slot():
            self._after_change()
            if raw is not None:
                self._say(f"Native values loaded from the source file — measurements "
                          f"are the camera's own pixel intensity"
                          f"{f' ({camera})' if camera else ''}.")

    # ──────────────────────────────────────────────────── Tools ───

    def _set_tool(self, tool: str):
        self._canvas.set_tool(tool)
        b = self._tool_buttons.get(tool)
        if b is not None and not b.isChecked():
            b.setChecked(True)
        self._update_status()

    def _on_style_changed(self, *_):
        """The colour and size controls set what the NEXT item looks like, and — when
        something is selected — restyle that item too, so a shape does not have to be
        deleted and drawn again just to change its colour."""
        c = self._canvas
        c.line_width = self._line_sl.value()
        c.brush_size = self._brush_sl.value()
        c.font_size = self._text_sb.value()
        c.fill_shapes = self._fill_cb.isChecked()
        c.draw_color = self._draw_color
        self._refresh_stroke_samples()
        a = c.selected()
        if a is None:
            return
        a.color = self._draw_color.name()
        a.width = c.brush_size if a.kind == A_FREE else c.line_width
        a.font_size = c.font_size
        if a.kind in (A_RECT, A_ELLIPSE, A_POLY):
            a.filled = c.fill_shapes
        c.update()

    def _refresh_stroke_samples(self):
        """Keep the two numbers and the two samples beside the sliders showing what a
        stroke drawn right now would look like."""
        line_w = self._line_sl.value()
        brush_w = self._brush_sl.value()
        self._line_val.setText(str(line_w))
        self._brush_val.setText(str(brush_w))
        self._line_prev.set_stroke(line_w, self._draw_color)
        self._brush_prev.set_stroke(brush_w, self._draw_color)
        # The big sample follows the drag, and for a size set with the arrow keys —
        # where there is no release to hide it — it shows itself for a moment.
        for sl, pop, w in ((self._line_sl, self._line_zoom, line_w),
                           (self._brush_sl, self._brush_zoom, brush_w)):
            if sl.isSliderDown():
                pop.show_for(sl, w, self._draw_color)
            elif pop.isVisible() or sl.hasFocus():
                pop.show_for(sl, w, self._draw_color, hold_ms=1200)

    def _pick_draw_color(self):
        col = QColorDialog.getColor(self._draw_color, self, "Drawing colour")
        if col.isValid():
            self._draw_color = col
            self._canvas.draw_color = col
            self._update_color_btn()
            self._on_style_changed()

    def _update_color_btn(self):
        self._color_btn.setStyleSheet(
            f"QPushButton {{ background: {self._draw_color.name()};"
            " border: 2px solid #7a7a7a; border-radius: 3px; }")

    def _on_color_picked(self, col: QColor):
        self._draw_color = col
        self._canvas.draw_color = col
        self._update_color_btn()
        self._refresh_stroke_samples()
        self._say(f"Picked colour {col.name()} — R {col.red()}  G {col.green()}  B {col.blue()}")

    # ───────────────────────────────────────────────── History ────

    def _undo(self):
        slot = self._slot()
        if slot is not None and slot.undo():
            self._canvas.refresh()
            self._after_change()
            self._say("Taken back.")

    def _redo(self):
        slot = self._slot()
        if slot is not None and slot.redo():
            self._canvas.refresh()
            self._after_change()
            self._say("Put back.")

    def _reset_to_source(self):
        slot = self._slot()
        if slot is None:
            return
        slot.reset_to_source()
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say("Back to the image as it arrived.")

    # ─────────────────────────────────────────────────── View ─────

    def _on_view_control(self, *_):
        if self._sync_guard:
            return
        slot = self._slot()
        if slot is None:
            return
        v = slot.view
        v.contrast = self._contrast_sl.value()
        v.brightness = self._bright_sl.value()
        v.gamma_slider = self._gamma_sl.value()
        v.auto_contrast = self._cb_auto_contrast.isChecked()
        v.auto_bright = self._cb_auto_bright.isChecked()
        v.auto_gamma = self._cb_auto_gamma.isChecked()
        if v.auto_contrast and v.auto_gamma:
            # Auto range and Auto gamma are two answers to the same question and fight
            # each other on the same frame; the Image Slider keeps them exclusive too.
            v.auto_gamma = False
        self._view_timer.start()

    def _on_hist_changed(self, lo: int, hi: int):
        slot = self._slot()
        if slot is None or self._sync_guard:
            return
        if slot.view.auto_contrast:
            self._sync_guard = True
            self._cb_auto_contrast.setChecked(False)
            self._sync_guard = False
            slot.view.auto_contrast = False
        slot.view.win_lo, slot.view.win_hi = lo, hi
        self._view_timer.start()

    def _apply_view_now(self):
        if self._slot() is None:
            return
        self._canvas.refresh()
        self._sync_view_controls()
        if self._compare_cb.currentIndex() > 0:
            self._update_compare()

    def _on_palette_changed(self, name: str):
        slot = self._slot()
        if slot is None or self._sync_guard:
            return
        slot.view.palette = name
        self._canvas.refresh()
        self._sync_view_controls()
        self._say(f"Palette: {name}")

    def _reset_view(self):
        slot = self._slot()
        if slot is None:
            return
        slot.view = _ViewSettings()
        self._canvas.refresh()
        self._sync_view_controls()
        self._update_compare()
        self._say("Display settings back to neutral — the picture data was never touched.")

    def _sync_view_controls(self):
        """Push the slot's view onto the controls, park the Auto values where they
        actually landed, and grey out what the current palette ignores."""
        slot = self._slot()
        self._sync_guard = True
        try:
            v = slot.view if slot is not None else _ViewSettings()
            self._contrast_sl.setValue(v.contrast)
            self._bright_sl.setValue(v.brightness)
            self._gamma_sl.setValue(v.gamma_slider)
            self._cb_auto_contrast.setChecked(v.auto_contrast)
            self._cb_auto_bright.setChecked(v.auto_bright)
            self._cb_auto_gamma.setChecked(v.auto_gamma)
            idx = self._palette_cb.findText(v.palette)
            if idx >= 0:
                self._palette_cb.setCurrentIndex(idx)

            gray = None
            lo, hi = v.win_lo, v.win_hi
            hist_vals, hist_max, hist_unit = None, 255.0, UNIT_CODE
            if slot is not None:
                # The display window is still decided on the 8-bit picture — that is
                # what render_view maps — but the bars are counted from the native data
                # when it is there, so the axis reads real camera counts.
                gray = np.clip(_to_gray(slot.base), 0, 255)
                if v.auto_contrast:
                    lo, hi = auto_window(gray)
                hist_vals, hist_unit = gray, UNIT_CODE
                if slot.measures_raw() and slot.full_scale:
                    raw = slot.raw
                    hist_vals = _to_gray(raw) if raw.ndim == 3 else raw
                    hist_max, hist_unit = float(slot.full_scale), UNIT_RAW
            self._hist.set_data(hist_vals, hist_max, hist_unit)
            self._hist.set_window(lo, hi)

            self._contrast_val.setText(f"{v.contrast:+d}" if v.contrast else "0")
            self._bright_val.setText(f"{v.brightness:+d}" if v.brightness else "0")
            gval = (_gamma_value(v, gray) if (v.auto_gamma and gray is not None)
                    else v.gamma_slider / 100.0)
            self._gamma_val.setText(f"{gval:.2f}")

            cyclic = v.palette in CYCLIC_PALETTES
            for w in (self._contrast_sl, self._bright_sl, self._gamma_sl, self._hist,
                      self._cb_auto_contrast, self._cb_auto_bright, self._cb_auto_gamma):
                w.setEnabled(not cyclic)
            if not cyclic:
                # Auto contrast owns the display window; Auto gamma is off the table
                # while it is on.
                self._hist.setEnabled(not v.auto_contrast)
                self._gamma_sl.setEnabled(not v.auto_gamma and not v.auto_contrast)
                self._cb_auto_gamma.setEnabled(not v.auto_contrast)
                self._contrast_sl.setEnabled(not v.auto_contrast)
                self._bright_sl.setEnabled(not v.auto_bright)
        finally:
            self._sync_guard = False

    # ───────────────────────────────────────────── Zoom / status ──

    def _on_zoom_slider(self, pos: int):
        if self._sync_guard:
            return
        self._canvas.set_zoom_percent(_zoom_slider_to_pct(pos))
        # With no picture on the canvas the zoom is refused, so the handle goes back
        # instead of standing at a magnification that is not in force. When it is
        # accepted the round trip is a step at most, which is left alone — putting the
        # handle back then would make it fight the mouse.
        real = _zoom_pct_to_slider(self._canvas.zoom_percent())
        if abs(real - pos) > 1:
            self._sync_guard = True
            self._zoom_sl.setValue(real)
            self._sync_guard = False
        self._zoom_val.setText(f"{self._canvas.zoom_percent():.0f} %")

    def _on_zoom_changed(self, _zoom: float):
        pct = self._canvas.zoom_percent()
        self._sync_guard = True
        # While the handle is held, only the number follows: putting the handle back
        # on the rounded position would make it stutter under the finger.
        if not self._zoom_sl.isSliderDown():
            self._zoom_sl.setValue(_zoom_pct_to_slider(pct))
        self._sync_guard = False
        self._zoom_val.setText(f"{pct:.0f} %")
        self._update_status()

    def _on_cursor_moved(self, x: float, y: float):
        slot = self._slot()
        if slot is None or x < 0 or self._canvas.comparing():
            self._cursor_text = ""
        else:
            ix, iy = int(x), int(y)
            v = slot.value_at(ix, iy)
            if v is None:
                self._cursor_text = ""
            else:
                code, val = v
                if slot.measures_raw():
                    frac = (f"  ({val / slot.full_scale * 100:.1f} % of full scale)"
                            if slot.full_scale else "")
                    self._cursor_text = (f"x {ix}  y {iy}   intensity {val:.0f}"
                                         f"{frac}   8-bit {code:.0f}")
                else:
                    self._cursor_text = f"x {ix}  y {iy}   intensity {code:.0f} (8-bit)"
        self._update_status()

    def _update_status(self, message: str = ""):
        if message:
            self._message = message
        slot = self._slot()
        if slot is None:
            hint = ("No images. Use “➤ Workshop” in Image Finder, Image Slider or "
                    "Shot Finder, or drop an image file on the picture area.")
            msg = getattr(self, "_message", "")
            self._status_lbl.setText(hint + (f"     |     {msg}" if msg else ""))
            return
        parts = []
        if getattr(self, "_cursor_text", ""):
            parts.append(self._cursor_text)
        if slot is not None:
            h, w = slot.base.shape[:2]
            parts.append(f"{w} × {h} px")
        parts.append(f"zoom {self._canvas.zoom_percent():.0f} %")
        if getattr(self, "_message", ""):
            parts.append(self._message)
        self._status_lbl.setText("     |     ".join(parts) if parts else "")

    def _say(self, message: str):
        self._message = message
        self._update_status()

    # ────────────────────────────────────────────── Measurement ───

    def _after_change(self):
        """One place that re-reads everything derived from the active slot, so no
        button is left stale or greyed out on one of the paths."""
        self._canvas.update_labels()
        self._refresh_measure()
        self._sync_view_controls()
        self._update_info()
        self._update_enabled()
        self._canvas.update()

    def _update_enabled(self):
        slot = self._slot()
        has = slot is not None
        self._btn_undo.setEnabled(has and bool(slot.undo_stack))
        self._btn_redo.setEnabled(has and bool(slot.redo_stack))
        self._btn_clear_annots.setEnabled(has and bool(slot.annots))
        self._btn_reset_src.setEnabled(has)

        # Everything added later is switched on from this one place too, so no path
        # through the panel can leave a button dead that should work.
        many = len(self._slots) >= 2
        ops = wk_ops is not None
        busy = self._save_task_busy
        self._btn_dup.setEnabled(has)
        self._btn_scalebar.setEnabled(has)
        self._btn_project.setEnabled(many and ops and not busy)
        self._btn_merge.setEnabled(has and ops and 0 <= self._ref < len(self._slots)
                                   and self._ref != self._active)
        self._btn_data_tiff.setEnabled(has and ops and not busy)
        self._btn_play.setEnabled(many)
        self._btn_anim.setEnabled(many and ops and not busy)
        for b in getattr(self, "_filter_buttons", ()):
            b.setEnabled(has and ops)
        self._btn_slider.setEnabled(
            has and slot.source_path is not None
            and getattr(self, "_slider_ref", None) is not None)
        self._btn_slider.setToolTip(
            "Open the folder this frame came from in the Image Slider"
            if getattr(self, "_slider_ref", None) is not None else
            "Only available when the Workshop runs inside Image Tools")

    def _on_image_changed(self):
        self._after_change()

    def _on_annots_changed(self):
        self._after_change()

    def _on_selection_changed(self):
        """Picking a different region has to re-read the numbers; nothing about the
        picture changed, so the full _after_change is not needed."""
        self._refresh_measure()
        self._canvas.update()

    def _refresh_measure(self):
        slot = self._slot()
        if slot is None:
            for c in self._cells.values():
                c.set_value("—")
            self._measure_src_lbl.setText("—")
            self._scale_lbl.setText("—")
            self._sync_sensor_controls(None)
            return

        arr = slot.measure_arr()
        unit = slot.unit_name()
        sel = self._canvas.selected()
        if sel is not None and sel.kind in (A_ROI_RECT, A_ROI_ELLIPSE):
            mask = _region_mask(sel, arr.shape)
            what = "selected region"
        else:
            mask = None
            what = "whole image"
        st = region_stats(arr, mask)

        src = (f"{what} — {unit}"
               + (f", full scale {slot.full_scale:.0f}" if slot.measures_raw()
                  and slot.full_scale else ""))
        if slot.raw_note:
            src += f"  ({slot.raw_note})"
        self._measure_src_lbl.setText(src)
        self._scale_lbl.setText(self._scale_text(slot))
        self._sync_sensor_controls(slot)

        if not st:
            for c in self._cells.values():
                c.set_value("—")
            return
        self._cells["min"].set_value(f"{st['min']:.0f}")
        self._cells["max"].set_value(f"{st['max']:.0f}")
        self._cells["mean"].set_value(f"{st['mean']:.2f}")
        self._cells["std"].set_value(f"{st['std']:.2f}")
        self._cells["sum"].set_value(f"{st['sum']:.4g}")
        self._cells["count"].set_value(f"{st['count']}")
        self._cells["centroid"].set_value(
            f"{st['cx']:.1f}, {st['cy']:.1f}" if "cx" in st else "—")
        if slot.px_per_mm:
            self._cells["area"].set_value(
                f"{st['count'] / (slot.px_per_mm ** 2):.4g} mm²")
        else:
            self._cells["area"].set_value(f"{st['count']} px²")

    @staticmethod
    def _scale_text(slot) -> str:
        """The Scale line: the pixel size in force, and where it came from. A ruler and
        the camera's nominal pitch are equally valid but not equally trustworthy, so the
        line always says which of the two is being used."""
        if not slot or not slot.px_per_mm:
            return "no scale — lengths in pixels only"
        um = slot.px_um or (1000.0 / slot.px_per_mm)
        where = "measured with a ruler" if slot.px_um_measured else "camera pixel size"
        return f"1 pixel = {um:.3f} µm  ({where})  —  {slot.px_per_mm:.1f} px per mm"

    def _sync_sensor_controls(self, slot):
        """Put the pixel size box back in step with the active image."""
        if not hasattr(self, "_sensor_um_sb"):
            return
        self._sensor_guard = True
        try:
            self._sensor_um_sb.setEnabled(slot is not None)
            if slot is not None and slot.px_um:
                self._sensor_um_sb.setValue(float(slot.px_um))
        finally:
            self._sensor_guard = False

    def _remember_sensor_default(self, um: float):
        """Whatever is in force here is what the next image opens with, and it outlives
        the session — an operator working behind a lens should not have to correct every
        frame they open."""
        if um and um > 0:
            self._sensor_default_um = float(um)
            self._ui_state["sensor_um"] = float(um)
            self._save_ui_state()

    def _apply_default_sensor_scale(self, slot):
        """Every new image starts with a pixel size already in force — the camera's own,
        or whatever was last put in the box."""
        if slot is None or slot.px_per_mm:
            return
        um = float(getattr(self, "_sensor_default_um", BASLER_PIXEL_UM))
        if um <= 0:
            um = BASLER_PIXEL_UM
        slot.px_um = um
        slot.px_per_mm = 1000.0 / um
        slot.px_um_measured = False

    def _set_pixel_size(self, um: float, measured: bool, message: str):
        """One way in for every change of scale, so px_um and px_per_mm can never drift
        apart and every path lands in the undo history."""
        slot = self._slot()
        if slot is None or um <= 0:
            return
        slot.push_undo()
        slot.px_um = float(um)
        slot.px_per_mm = 1000.0 / float(um)
        slot.px_um_measured = bool(measured)
        self._remember_sensor_default(um)
        self._after_change()
        self._say(message)

    def _on_sensor_um(self, um: float):
        """Typing a size re-scales straight away — no second click to apply, because a
        value shown but not in force is a trap."""
        if getattr(self, "_sensor_guard", False) or um <= 0:
            return
        self._remember_sensor_default(um)
        slot = self._slot()
        if slot is None:
            return
        self._set_pixel_size(um, False,
                             f"One pixel is {um:.3f} µm — {1000.0 / um:.1f} px per mm.")

    def _set_scale(self):
        slot = self._slot()
        if slot is None:
            return
        sel = self._canvas.selected()
        if sel is None or sel.kind != A_RULER:
            rulers = [a for a in slot.annots if a.kind == A_RULER]
            sel = rulers[-1] if rulers else None
        if sel is None:
            QMessageBox.information(
                self, "Set scale",
                "Draw a ruler across something of a known size first, then press "
                "“Set scale…” again.")
            return
        dpx = math.hypot(sel.pts[1][0] - sel.pts[0][0], sel.pts[1][1] - sel.pts[0][1])
        val, ok = QInputDialog.getDouble(
            self, "Set scale", f"The ruler is {dpx:.1f} pixels long.\n"
                               f"How long is it in millimetres?", 1.0, 0.000001, 1e6, 6)
        if not ok or val <= 0:
            return
        # A measured ruler beats the camera's nominal pitch, so it becomes the pixel
        # size — the box shows what was measured instead of contradicting it.
        um = val * 1000.0 / dpx
        self._set_pixel_size(
            um, True, f"Scale measured: one pixel is {um:.3f} µm "
                      f"({dpx / val:.1f} px per mm).")

    def _clear_scale(self):
        self._set_pixel_size(
            BASLER_PIXEL_UM, False,
            f"Back to the camera's own pixel size: {BASLER_PIXEL_UM} µm.")

    def _profile_samples(self, slot, a, width: int):
        """Samples along a profile line, in millimetres once a scale is set."""
        x, y = line_profile(slot.measure_arr(), a.pts[0], a.pts[1], width)
        if slot.px_per_mm:
            x = x / slot.px_per_mm
        return x, y

    def _show_profile(self, annot):
        slot = self._slot()
        if slot is None:
            return
        a = annot if (annot is not None and getattr(annot, "kind", "") == A_PROFILE) else None
        if a is None:
            lines = [x for x in slot.annots if x.kind == A_PROFILE]
            a = lines[-1] if lines else None
        if a is None:
            QMessageBox.information(self, "Plot profile",
                                    "Draw a profile line over the picture first.")
            return
        if self._profile_dlg is None:
            self._profile_dlg = ProfileDialog(self)
        dlg = self._profile_dlg
        x, y = self._profile_samples(slot, a, dlg.line_width())
        dlg.set_profile(x, y, slot.unit_name(), slot.label[:40],
                        "mm" if slot.px_per_mm else "px",
                        recompute=lambda w, s=slot, ann=a: self._profile_samples(s, ann, w))
        dlg.show()
        dlg.raise_()

    # ---- several regions, and the results table ------------------------------

    def _on_keep_regions(self, on: bool):
        self._canvas.keep_regions = bool(on)
        self._say("Regions pile up — read them in the results table."
                  if on else "A new region now replaces the old one.")

    def _regions(self) -> list:
        slot = self._slot()
        return [] if slot is None else [a for a in slot.annots
                                        if a.kind in _REGION_KINDS]

    def _measure_table(self):
        """(headers, rows, note) for the results table: one row per measured item,
        plus the whole picture on the first row so there is always something to
        compare against."""
        slot = self._slot()
        if slot is None:
            return [], [], ""
        arr = slot.measure_arr()
        unit = slot.unit_name()
        mm = slot.px_per_mm
        #  Pixels AND real size, side by side, in every row: the pixel count is what the
        #  picture is made of, the millimetres are what the answer is, and a table that
        #  carries only one of them always turns out to be carrying the other one.
        names = ["#", "Item", "X", "Y", "Width (px)", "Height (px)", "Width (mm)",
                 "Height (mm)", "Length (px)", "Length (mm)", "Angle (°)",
                 f"Min ({unit})", f"Max ({unit})", f"Mean ({unit})", f"Std ({unit})",
                 f"Sum ({unit})", "Pixels", "Area (mm²)", "Centre X", "Centre Y"]
        headers = list(names)
        col = {n: i for i, n in enumerate(names)}

        def fmt(v, nd=2):
            return "" if v is None else f"{v:.{nd}f}"

        def real(v_px, nd=4):
            """A pixel distance as millimetres, blank when there is no scale."""
            return "" if not mm else f"{v_px / mm:.{nd}f}"

        def new_row():
            return [""] * len(headers)

        def put(row, name, value):
            row[col[name]] = value

        def put_stats(row, st):
            if not st:
                return
            put(row, f"Min ({unit})", fmt(st["min"], 0))
            put(row, f"Max ({unit})", fmt(st["max"], 0))
            put(row, f"Mean ({unit})", fmt(st["mean"]))
            put(row, f"Std ({unit})", fmt(st["std"]))
            put(row, f"Sum ({unit})", f"{st['sum']:.6g}")
            put(row, "Pixels", str(st["count"]))
            if mm:
                put(row, "Area (mm²)", f"{st['count'] / (mm * mm):.6g}")
            put(row, "Centre X", fmt(st.get("cx"), 1))
            put(row, "Centre Y", fmt(st.get("cy"), 1))

        def put_size(row, w_px, h_px):
            put(row, "Width (px)", fmt(w_px, 1))
            put(row, "Height (px)", fmt(h_px, 1))
            put(row, "Width (mm)", real(w_px))
            put(row, "Height (mm)", real(h_px))

        rows = []
        whole = region_stats(arr, None)
        h, w = arr.shape[:2]
        row = new_row()
        put(row, "#", "—"); put(row, "Item", "Whole image")
        put(row, "X", "0"); put(row, "Y", "0")
        put_size(row, float(w), float(h))
        put_stats(row, whole)
        rows.append(row)

        for i, a in enumerate(slot.annots):
            if a.kind not in _TABLE_KINDS:
                continue
            x0, y0, x1, y1 = a.bbox()
            name = {A_ROI_RECT: "Rectangle region", A_ROI_ELLIPSE: "Ellipse region",
                    A_RULER: "Ruler", A_ANGLE: "Angle", A_CROSS: "Point",
                    A_PROFILE: "Profile line"}.get(a.kind, a.kind)
            row = new_row()
            put(row, "#", str(i + 1)); put(row, "Item", name)
            put(row, "X", fmt(x0, 1)); put(row, "Y", fmt(y0, 1))
            put_size(row, x1 - x0, y1 - y0)
            if a.kind in _REGION_KINDS:
                put_stats(row, region_stats(arr, _region_mask(a, arr.shape)))
            elif a.kind in (A_RULER, A_PROFILE) and len(a.pts) >= 2:
                dpx = math.hypot(a.pts[1][0] - a.pts[0][0], a.pts[1][1] - a.pts[0][1])
                put(row, "Length (px)", fmt(dpx, 1))
                put(row, "Length (mm)", real(dpx))
                put(row, "Angle (°)", fmt(line_angle_deg(a.pts[0], a.pts[1]), 1))
                if a.kind == A_PROFILE:
                    _, vals = line_profile(arr, a.pts[0], a.pts[1])
                    put(row, f"Min ({unit})", fmt(float(vals.min()), 0))
                    put(row, f"Max ({unit})", fmt(float(vals.max()), 0))
                    put(row, f"Mean ({unit})", fmt(float(vals.mean())))
                    put(row, f"Std ({unit})", fmt(float(vals.std())))
                    put(row, f"Sum ({unit})", f"{float(vals.sum()):.6g}")
                    put(row, "Pixels", str(int(vals.size)))
            elif a.kind == A_ANGLE and len(a.pts) >= 3:
                put(row, "X", fmt(a.pts[1][0], 1)); put(row, "Y", fmt(a.pts[1][1], 1))
                put_size(row, None, None)
                put(row, "Angle (°)", fmt(angle_between(a.pts[0], a.pts[1], a.pts[2]), 2))
            elif a.kind == A_CROSS and a.pts:
                px, py = int(round(a.pts[0][0])), int(round(a.pts[0][1]))
                v = slot.value_at(px, py)
                put(row, "X", str(px)); put(row, "Y", str(py))
                put_size(row, None, None)
                if v is not None:
                    for c in (f"Min ({unit})", f"Max ({unit})", f"Mean ({unit})"):
                        put(row, c, fmt(v[1], 0))
                    put(row, "Pixels", "1")
            rows.append(row)

        note = (f"{slot.label[:60]} — {unit}"
                + (f", 1 pixel = {slot.px_um:.3f} µm ({mm:.1f} px per mm)"
                   if mm and slot.px_um else
                   f", lengths in mm ({mm:.3f} px per mm)" if mm
                   else ", lengths in pixels (no scale set)"))
        return headers, rows, note

    def _show_results(self):
        slot = self._slot()
        if slot is None:
            QMessageBox.information(self, "Results table", "There is no image yet.")
            return
        headers, rows, note = self._measure_table()
        if self._results_dlg is None:
            self._results_dlg = _TableDialog("Results", self, extra_buttons=(
                ("Refresh", "Read the numbers again",
                 lambda: self._results_dlg.set_content(*self._measure_table())),))
        self._results_dlg.set_content(headers, rows, note)
        self._results_dlg.show()
        self._results_dlg.raise_()

    def _show_histogram_numbers(self):
        """The histogram as numbers. Same 256 buckets and same source as the picture of
        it in the Display section, so the two cannot disagree."""
        slot = self._slot()
        if slot is None:
            QMessageBox.information(self, "Histogram", "There is no image yet.")
            return
        if slot.measures_raw() and slot.full_scale:
            raw = slot.raw
            vals = _to_gray(raw) if raw.ndim == 3 else raw
            vmax, unit = float(slot.full_scale), slot.unit_name()
        else:
            vals = np.clip(_to_gray(slot.base), 0, 255)
            vmax, unit = 255.0, UNIT_CODE
        s = _stat_sample(vals)
        idx = np.clip(np.asarray(s, dtype=np.float64) * (255.0 / vmax),
                      0, 255).astype(np.int32)
        counts = np.bincount(np.ravel(idx), minlength=256).astype(np.float64)
        total = float(counts.sum()) or 1.0
        cum = np.cumsum(counts)
        step = vmax / 256.0
        rows = []
        for i in range(256):
            rows.append([str(i), f"{i * step:.4g}", f"{(i + 1) * step:.4g}",
                         f"{int(counts[i])}", f"{counts[i] / total * 100:.4f}",
                         f"{cum[i] / total * 100:.4f}"])
        headers = ["Bucket", f"From ({unit})", f"To ({unit})", "Pixels", "Share (%)",
                   "Up to here (%)"]
        note = (f"{slot.label[:60]} — 256 buckets over 0 … {vmax:.0f} {unit}, "
                f"{int(total)} pixels counted"
                + ("  (a sample of the frame, as in the histogram picture)"
                   if s.size != vals.size else ""))
        if self._hist_dlg is None:
            self._hist_dlg = _TableDialog("Histogram", self)
        self._hist_dlg.set_content(headers, rows, note)
        self._hist_dlg.show()
        self._hist_dlg.raise_()

    # ---- scale bar -----------------------------------------------------------

    def _add_scale_bar(self):
        slot = self._slot()
        if slot is None:
            return
        if not slot.px_per_mm:
            QMessageBox.information(
                self, "Scale bar",
                "Set a scale first: draw a ruler across something of a known size, "
                "then press “Set scale…”.\nWithout that the Workshop has no idea how "
                "long a millimetre is on this picture.")
            return
        h, w = slot.base.shape[:2]
        # A round length that comes out near a fifth of the frame — the length a person
        # would have picked, without having to think about it.
        want_mm = (w / 5.0) / slot.px_per_mm
        nice = _nice_length(want_mm)
        val, ok = QInputDialog.getDouble(
            self, "Scale bar",
            f"The picture is {w / slot.px_per_mm:.3f} mm wide.\n"
            f"How long should the bar be, in millimetres?",
            nice, 0.000001, 1e6, 6)
        if not ok or val <= 0:
            return
        length_px = val * slot.px_per_mm
        if length_px > w:
            QMessageBox.information(self, "Scale bar",
                                    "That bar would be wider than the picture.")
            return
        slot.push_undo()
        slot.annots = [a for a in slot.annots if a.kind != A_SCALEBAR]
        margin = max(8.0, w * 0.04)
        y = h - max(12.0, h * 0.06)
        bar = _Annot(A_SCALEBAR, [[margin, y], [margin + length_px, y]],
                     "#ffffff", max(2, self._line_sl.value()), False, "",
                     max(10, self._text_sb.value()), "", float(val))
        slot.annots.append(bar)
        self._canvas.select_index(len(slot.annots) - 1)
        self._after_change()
        self._say(f"Scale bar added: {_fmt_mm(val)} = {length_px:.1f} px. "
                  f"Drag it with Select; it is saved with the drawing.")

    def _remove_scale_bar(self):
        slot = self._slot()
        if slot is None or not any(a.kind == A_SCALEBAR for a in slot.annots):
            return
        slot.push_undo()
        slot.annots = [a for a in slot.annots if a.kind != A_SCALEBAR]
        self._after_change()
        self._say("Scale bar removed.")

    # ─────────────────────────────────────────────────── Beam ─────

    def _beam_input(self):
        """(array, mask, what) for the beam measurements: the selected region if there
        is one, otherwise the whole picture."""
        slot = self._slot()
        if slot is None:
            return None, None, ""
        arr = slot.measure_arr()
        sel = self._canvas.selected()
        if sel is not None and sel.kind in _REGION_KINDS:
            return arr, _region_mask(sel, arr.shape), "selected region"
        return arr, None, "whole image"

    def _beam_stats(self):
        if wk_beam is None:
            QMessageBox.warning(self, "Beam", f"The beam maths could not be loaded"
                                              f"{f': {_BEAM_ERROR}' if _BEAM_ERROR else ''}.")
            return None, None, None, ""
        slot = self._slot()
        arr, mask, what = self._beam_input()
        if arr is None:
            QMessageBox.information(self, "Beam", "There is no image yet.")
            return None, None, None, ""
        st = wk_beam.beam_stats(arr, mask, self._beam_base_sb.value())
        if not st or "cx" not in st:
            QMessageBox.information(
                self, "Beam",
                "Nothing to measure here: after the background was taken off there is "
                "no signal left. Try a lower background percentage, or put the region "
                "around the spot.")
            return None, None, None, ""
        return st, arr, mask, what

    def _show_beam_report(self):
        st, arr, mask, what = self._beam_stats()
        if st is None:
            return
        slot = self._slot()
        unit = slot.unit_name()
        mm = slot.px_per_mm
        rows = []

        def add(name, value, u=""):
            rows.append([name, value, u])

        def length(v):
            # Both, always — a width in millimetres alone cannot be checked against the
            # picture, and a width in pixels alone is not the answer anyone wanted.
            return f"{v:.1f} px" + (f"  =  {_fmt_mm(v / mm)}" if mm else "")

        add("Measured on", what)
        add("Background taken off", f"{st['baseline']:.1f}", unit)
        add("Peak value", f"{st['peak']:.0f}", unit)
        add("Peak at", f"{st['peak_x']:.1f}, {st['peak_y']:.1f}", "px")
        add("Centre of mass", f"{st['cx']:.2f}, {st['cy']:.2f}", "px")
        add("Total above background", f"{st['total']:.6g}", unit)
        add("D4σ width across", length(st.get("d4s_x", 0.0)))
        add("D4σ width down", length(st.get("d4s_y", 0.0)))
        add("D4σ long axis", length(st.get("d4s_major", 0.0)))
        add("D4σ short axis", length(st.get("d4s_minor", 0.0)))
        if "ellipticity" in st:
            add("Roundness (short / long)", f"{st['ellipticity']:.4f}",
                "1.0 = round")
        add("Long axis tilt", f"{st.get('angle_deg', 0.0):.2f}", "° anticlockwise")
        for key, name in (("fwhm_x", "FWHM across"), ("fwhm_y", "FWHM down"),
                          ("w1e2_x", "1/e² width across"),
                          ("w1e2_y", "1/e² width down")):
            if key in st:
                add(name, length(st[key]))
            else:
                add(name, "not reached", "")
        if mm:
            add("Scale", f"1 pixel = {slot.px_um:.3f} µm" if slot.px_um
                else f"{mm:.4f} px per mm", f"{mm:.1f} px per mm")

        note = (f"{slot.label[:60]} — {what}, {unit}. Widths from the "
                f"intensity moments (D4σ) and read off the row and column through the "
                f"centre of mass (FWHM, 1/e²). “not reached” means the trace never "
                f"comes back down to that level inside the area measured.")
        if self._beam_dlg is None:
            self._beam_dlg = _TableDialog("Beam report", self, extra_buttons=(
                ("Radial profile", "Average value against distance from the centre",
                 self._show_radial),
                ("Encircled energy", "Signal inside a circle against its radius",
                 self._show_encircled),
                ("Refresh", "Measure again", self._show_beam_report)))
        self._beam_dlg.set_content(["Quantity", "Value", "Unit"], rows, note)
        self._beam_dlg.show()
        self._beam_dlg.raise_()

    def _show_radial(self):
        st, arr, mask, what = self._beam_stats()
        if st is None:
            return
        slot = self._slot()
        r, v = wk_beam.radial_profile(arr, st["cx"], st["cy"], mask)
        if r.size < 2:
            return
        mm = slot.px_per_mm
        x = r / mm if mm else r
        if self._radial_dlg is None:
            self._radial_dlg = _CurveDialog("Radial profile", self)
        self._radial_dlg.set_curve(
            x, v,
            f"{slot.label[:50]} — {what}, around the centre of mass at "
            f"{st['cx']:.1f}, {st['cy']:.1f} px. Each point is the average of every "
            f"pixel at that distance.",
            "distance from the centre", "mm" if mm else "px", slot.unit_name(),
            columns=(f"radius_{'mm' if mm else 'px'}", f"mean_{slot.unit_key()}"))
        self._radial_dlg.show()
        self._radial_dlg.raise_()

    def _show_encircled(self):
        st, arr, mask, what = self._beam_stats()
        if st is None:
            return
        slot = self._slot()
        ee = wk_beam.encircled_energy(arr, st["cx"], st["cy"], mask,
                                      self._beam_base_sb.value())
        frac = ee.get("fraction")
        if frac is None or frac.size < 2:
            return
        mm = slot.px_per_mm
        lu = "mm" if mm else "px"
        x = ee["radii"] / mm if mm else ee["radii"]
        bits = []
        for key, name in (("r500", "half"), ("r865", "86.5 %"), ("r900", "90 %")):
            if key in ee:
                rv = ee[key] / mm if mm else ee[key]
                bits.append(f"{name} of the signal within {rv:.3f} {lu}")
        if ee.get("clipped"):
            bits.append("— the circle runs off the edge of the area measured, so the "
                        "wide end of this curve is not to be trusted")
        hl = [(0.5, "#c02020", "half"), (0.865, "#0a8f4a", "86.5 %")]
        if self._encircled_dlg is None:
            self._encircled_dlg = _CurveDialog("Encircled energy", self)
        self._encircled_dlg.set_curve(
            x, frac,
            f"{slot.label[:50]} — {what}. " + "  |  ".join(bits),
            "radius of the circle", lu, "share", hlines=hl,
            columns=(f"radius_{lu}", "fraction"))
        self._encircled_dlg.show()
        self._encircled_dlg.raise_()

    def _mark_centroid(self):
        st, arr, mask, what = self._beam_stats()
        if st is None:
            return
        self._canvas.add_annot(_Annot(A_CROSS, [[st["cx"], st["cy"]]],
                                      self._draw_color.name(), self._line_sl.value()))
        self._after_change()
        self._say(f"Centre of mass of the {what}: {st['cx']:.2f}, {st['cy']:.2f} px")

    # ──────────────────────────────────────────────── Filters ────

    def _editing_blocked(self) -> bool:
        """True when a comparison view is up — and then nothing may change pixels.

        The canvas already refuses to draw or crop while comparing, but the panel's
        buttons used to go ahead: the picture on screen was the composition, so an
        operation applied to the active image with no visible effect, and the next
        measurement was taken off a frame nobody had looked at. One guard, in front of
        every operation that touches pixels."""
        if not self._canvas.comparing():
            return False
        self._say("Comparison is a view only — set Compare to “Off” first.")
        return True

    def _need_ops(self) -> bool:
        if wk_ops is not None:
            return True
        QMessageBox.warning(self, "Filters",
                            "The pixel operations could not be loaded"
                            + (f":\n{_OPS_ERROR}" if _OPS_ERROR else "."))
        return False

    def _apply_to_both(self, fn, message: str, keep_raw: bool = True):
        """Run the same operation on the picture and on the counts behind it.

        Both layers or neither: filtering only what is on screen would leave the
        Measure panel reporting numbers from a picture that is no longer displayed —
        the one thing this tab is built not to do."""
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        h, w = slot.base.shape[:2]
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        try:
            new_base = fn(slot.base)
            new_raw = fn(slot.raw) if (aligned and keep_raw) else None
        except Exception as e:
            QMessageBox.warning(self, "Could not do that", f"{e}")
            return
        slot.push_undo()
        slot.base = new_base
        if aligned:
            if keep_raw and new_raw is not None:
                slot.raw = new_raw
            else:
                slot.raw = None
                slot.raw_note = "changed picture — values are display codes"
        changed_size = new_base.shape[:2] != (h, w)
        if changed_size:
            slot.fitted = False
            self._canvas.set_slot(slot)
        else:
            self._canvas.refresh()
        self._after_change()
        self._say(message)

    def _filter_median(self):
        if not self._need_ops():
            return
        r, ok = QInputDialog.getInt(
            self, "Median filter",
            "How far around each pixel to look, in pixels?\n"
            "1 is a 3 × 3 neighbourhood and clears single hot pixels; 3 is heavy.",
            1, 1, 15, 1)
        if not ok:
            return
        self._apply_to_both(lambda a: wk_ops.median(a, r),
                            f"Median filter, {2 * r + 1} × {2 * r + 1} pixels.")

    def _filter_blur(self):
        if not self._need_ops():
            return
        s, ok = QInputDialog.getDouble(
            self, "Blur", "How much blur (sigma, in pixels)?", 1.0, 0.1, 50.0, 2)
        if not ok:
            return
        self._apply_to_both(lambda a: wk_ops.gaussian(a, s),
                            f"Blurred, sigma {s:g} px.")

    def _filter_sharpen(self):
        if not self._need_ops():
            return
        amt, ok = QInputDialog.getDouble(
            self, "Sharpen", "How much? 1 is a normal sharpen, 3 is heavy.",
            1.0, 0.1, 10.0, 2)
        if not ok:
            return
        self._apply_to_both(lambda a: wk_ops.sharpen(a, amt),
                            f"Sharpened, amount {amt:g}.")

    def _filter_edges(self):
        if not self._need_ops():
            return
        self._apply_to_both(wk_ops.edges, "Edges — bright where the picture changes.")

    def _filter_background(self):
        if not self._need_ops():
            return
        modes = list(wk_ops.BACKGROUND_MODES)
        mode, ok = QInputDialog.getItem(self, "Remove background",
                                        "What is the background like?", modes, 0, False)
        if not ok:
            return
        if mode == modes[0]:
            param, ok2 = QInputDialog.getDouble(
                self, "Remove background",
                "Take off this percentile of the picture as the background:",
                5.0, 0.0, 99.0, 1)
        elif mode == modes[3]:
            param, ok2 = QInputDialog.getInt(
                self, "Remove background",
                "How big is the ball, in pixels?\nEverything smaller than this is "
                "kept, everything larger counts as background.", 25, 2, 500, 1)
        else:
            param, ok2 = 0.0, True
        if not ok2:
            return
        slot = self._slot()
        if slot is None:
            return
        # ONE background map, applied to both layers. Fitting a surface to each of them
        # separately would subtract two different backgrounds from the same frame.
        try:
            bg = wk_ops.background_map(slot.base, mode, param)
        except Exception as e:
            QMessageBox.warning(self, "Remove background", f"{e}")
            return
        aligned = slot.raw is not None and slot.raw.shape[:2] == slot.base.shape[:2]
        bg_raw = None
        if aligned:
            # The counts layer has its own numbers, so it needs its own map of the same
            # SHAPE — the same mode and parameter, measured on the counts.
            try:
                bg_raw = wk_ops.background_map(slot.raw, mode, param)
            except Exception:
                bg_raw = None

        def fn(a):
            if a is slot.raw and bg_raw is not None:
                return wk_ops.subtract_background(a, mode, param, bg_raw)
            return wk_ops.subtract_background(a, mode, param, bg)

        self._apply_to_both(fn, f"Background removed — {mode.lower()}.",
                            keep_raw=bg_raw is not None)

    # ────────────────────────────────────────── Picture editing ───

    def _rotate_free(self):
        if not self._need_ops():
            return
        slot = self._slot()
        if slot is None:
            return
        deg, ok = QInputDialog.getDouble(
            self, "Rotate by angle",
            "Turn by how many degrees?\nPositive turns anticlockwise. The picture "
            "grows so that no corner is cut off.", 0.0, -360.0, 360.0, 2)
        if not ok or abs(deg) < 1e-9:
            return
        self._rotate_arbitrary(deg, "Rotated by")

    def _rotate_arbitrary(self, deg: float, verb: str):
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        h, w = slot.base.shape[:2]
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        try:
            new_base = wk_ops.rotate(slot.base, deg, expand=True, nearest=False)
            new_raw = (wk_ops.rotate(slot.raw, deg, expand=True, nearest=True)
                       if aligned else None)
        except Exception as e:
            QMessageBox.warning(self, "Rotate", f"{e}")
            return
        fn = wk_ops.rotate_point_map(slot.base.shape, deg, True)
        slot.push_undo()
        slot.base = new_base
        if aligned:
            slot.raw = new_raw
        self._transform_annots(slot, fn)
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say(f"{verb} {deg:.2f}° — now {new_base.shape[1]} × {new_base.shape[0]} px. "
                  f"The corners the picture grew into are black, and they are real "
                  f"pixels: leave them out of a region.")

    def _straighten(self):
        """Turn the picture so that a line drawn along a feature becomes horizontal."""
        if not self._need_ops():
            return
        slot = self._slot()
        if slot is None:
            return
        sel = self._canvas.selected()
        kinds = (A_LINE, A_ARROW, A_RULER, A_PROFILE)
        if sel is None or sel.kind not in kinds or len(sel.pts) < 2:
            found = [a for a in slot.annots if a.kind in kinds]
            sel = found[-1] if found else None
        if sel is None:
            QMessageBox.information(
                self, "Straighten",
                "Draw a straight line, an arrow or a ruler along the edge you want "
                "level first, then press Straighten.")
            return
        ang = line_angle_deg(sel.pts[0], sel.pts[1])
        # A line drawn right-to-left describes the same edge as one drawn left-to-right;
        # folding the angle into ±90° keeps the picture the right way up either way.
        while ang > 90.0:
            ang -= 180.0
        while ang < -90.0:
            ang += 180.0
        if abs(ang) < 1e-6:
            self._say("That line is already level.")
            return
        self._rotate_arbitrary(-ang, "Straightened by")

    def _bin_dialog(self):
        if not self._need_ops():
            return
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        factor, ok = QInputDialog.getInt(
            self, "Bin pixels",
            "Join blocks of how many pixels across?\n2 makes the picture half the "
            "size, 4 a quarter.", 2, 2, 16, 1)
        if not ok:
            return
        mode, ok2 = QInputDialog.getItem(
            self, "Bin pixels", "What should a block become?",
            ["Average — keeps the scale", "Sum — adds the counts up"], 0, False)
        if not ok2:
            return
        summing = mode.startswith("Sum")
        m = "Sum" if summing else "Average"
        h, w = slot.base.shape[:2]
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        try:
            # The picture always averages: an 8-bit picture cannot hold four pixels
            # added together, it would just go white. Only the counts can be summed.
            new_base = wk_ops.bin_pixels(slot.base, factor, "Average")
            new_raw = wk_ops.bin_pixels(slot.raw, factor, m) if aligned else None
        except Exception as e:
            QMessageBox.warning(self, "Bin pixels", f"{e}")
            return
        slot.push_undo()
        slot.base = new_base
        if aligned:
            slot.raw = new_raw
            if summing and slot.full_scale:
                # The values grew, so the scale they are read against has to grow with
                # them or the histogram axis and the "% of full scale" both lie.
                slot.full_scale = float(slot.full_scale) * (factor * factor)
        sx = new_base.shape[1] / w
        sy = new_base.shape[0] / h
        self._transform_annots(slot, lambda x, y: (x * sx, y * sy))
        if slot.px_per_mm:
            slot.px_per_mm *= (sx + sy) / 2.0
            if slot.px_um:
                # Joining pixels makes each one cover more of the object.
                slot.px_um /= (sx + sy) / 2.0
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say(f"Binned {factor} × {factor} ({m.lower()}) — now "
                  f"{new_base.shape[1]} × {new_base.shape[0]} px.")

    def _transform_annots(self, slot, fn):
        for a in slot.annots:
            a.pts = [list(fn(p[0], p[1])) for p in a.pts]

    def _rotate(self, k: int):
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        h, w = slot.base.shape[:2]
        slot.push_undo()
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        slot.base = np.ascontiguousarray(np.rot90(slot.base, k))
        if aligned:
            slot.raw = np.ascontiguousarray(np.rot90(slot.raw, k))
        if k == 1:
            fn = lambda x, y: (y, w - 1 - x)                 # anticlockwise
        elif k == -1:
            fn = lambda x, y: (h - 1 - y, x)                 # clockwise
        else:
            fn = lambda x, y: (w - 1 - x, h - 1 - y)
        self._transform_annots(slot, fn)
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say("Rotated.")

    def _flip(self, axis: int):
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        h, w = slot.base.shape[:2]
        slot.push_undo()
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        slot.base = np.ascontiguousarray(np.flip(slot.base, axis=axis))
        if aligned:
            slot.raw = np.ascontiguousarray(np.flip(slot.raw, axis=axis))
        if axis == 1:
            self._transform_annots(slot, lambda x, y: (w - 1 - x, y))
        else:
            self._transform_annots(slot, lambda x, y: (x, h - 1 - y))
        self._canvas.refresh()
        self._after_change()
        self._say("Flipped.")

    def _resize_dialog(self):
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        h, w = slot.base.shape[:2]
        pct, ok = QInputDialog.getDouble(
            self, "Resize", f"Current size {w} × {h} px.\nNew size, in percent:",
            100.0, 1.0, 2000.0, 1)
        if not ok or abs(pct - 100.0) < 1e-6:
            return
        nw, nh = max(1, int(round(w * pct / 100.0))), max(1, int(round(h * pct / 100.0)))
        slot.push_undo()
        aligned = slot.raw is not None and slot.raw.shape[:2] == (h, w)
        slot.base = np.asarray(
            _arr_to_pil(slot.base).resize((nw, nh), _pil_bilinear()))
        if aligned:
            # Nearest neighbour for the measurement data: interpolating counts would
            # invent values that were never recorded.
            yi = np.clip((np.arange(nh) * h / nh).astype(int), 0, h - 1)
            xi = np.clip((np.arange(nw) * w / nw).astype(int), 0, w - 1)
            slot.raw = slot.raw[yi][:, xi]
        sx, sy = nw / w, nh / h
        self._transform_annots(slot, lambda x, y: (x * sx, y * sy))
        if slot.px_per_mm:
            slot.px_per_mm *= (sx + sy) / 2.0
            if slot.px_um:
                slot.px_um /= (sx + sy) / 2.0
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say(f"Resized to {nw} × {nh} px.")

    def _on_ref_changed(self, idx: int):
        self._ref = idx - 1
        self._update_compare()

    def _load_ref_from_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load reference image", str(self._last_save_dir or Path.home()),
            "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if not path:
            return
        before = len(self._slots)
        self.open_files([path])
        if len(self._slots) > before:
            self._ref = len(self._slots) - 1
            self._update_slot_list()
            self._ref_combo.setCurrentIndex(self._ref + 1)
            self._say(f"Reference: {self._slots[self._ref].label}")

    def _do_diff(self, absolute: bool):
        slot = self._slot()
        if slot is None or self._editing_blocked():
            return
        if not (0 <= self._ref < len(self._slots)) or self._ref == self._active:
            QMessageBox.information(self, "Reference",
                                    "Choose a different image as the reference first.")
            return
        ref = self._slots[self._ref]
        a = slot.base.astype(np.int32)
        r = ref.base.astype(np.int32)
        if a.ndim != r.ndim:
            a = _to_gray(a).astype(np.int32)
            r = _to_gray(r).astype(np.int32)
        h = min(a.shape[0], r.shape[0])
        w = min(a.shape[1], r.shape[1])
        out = (np.abs(a[:h, :w] - r[:h, :w]) if absolute
               else np.clip(a[:h, :w] - r[:h, :w], 0, 255))
        slot.push_undo()
        slot.base = np.ascontiguousarray(out.astype(np.uint8))
        # The counts no longer describe what is on screen once two frames are combined.
        slot.raw = None
        slot.raw_note = "subtracted image — values are display codes"
        slot.fitted = False
        self._canvas.set_slot(slot)
        self._after_change()
        self._say(f"{'Difference' if absolute else 'Subtraction'}: "
                  f"{slot.label[:24]} − {ref.label[:24]}")

    # ────────────────────────────────────────── Combine images ────

    def _add_derived_slot(self, base: np.ndarray, label: str,
                          raw: "np.ndarray | None" = None,
                          full_scale: "float | None" = None,
                          camera: str = "", note: str = "") -> "_WorkshopSlot":
        """Add an image the Workshop MADE, rather than one it was given.

        Deliberately not receive_image: that one starts a background read of the source
        file to find the counts, and a combined image has no single source file to
        read. Whatever counts it does have are handed in here instead."""
        base = np.ascontiguousarray(base.astype(np.uint8, copy=False))
        slot = _WorkshopSlot(label=label, base=base, source_base=base,
                             raw=raw, source_raw=raw, full_scale=full_scale,
                             camera=camera)
        slot.raw_note = note or ("" if raw is not None else
                                 "made in the Workshop — values are display codes")
        self._apply_default_sensor_scale(slot)
        self._slots.append(slot)
        self._update_slot_list()
        self._activate_slot(len(self._slots) - 1)
        return slot

    def _do_project(self):
        if not self._need_ops():
            return
        if len(self._slots) < 2:
            QMessageBox.information(
                self, "Combine images",
                "There is only one image open. Open or send over at least two.")
            return
        mode = self._project_cb.currentText()
        bases = [s.base for s in self._slots]
        try:
            out = wk_ops.project(bases, mode, np.uint8)
        except Exception as e:
            QMessageBox.warning(self, "Combine images", f"{e}")
            return

        # The counts come along only if EVERY image has them, on the same grid and the
        # same scale. Averaging counts from two different cameras' scales would produce
        # a number that means nothing.
        raw = None
        full_scale = None
        camera = ""
        note = ""
        raws = [s.raw for s in self._slots]
        scales = {float(s.full_scale) for s in self._slots if s.full_scale}
        cams = {s.camera for s in self._slots if s.camera}
        if all(s.measures_raw() for s in self._slots) and len(scales) == 1 \
                and len({r.shape[:2] for r in raws}) == 1:
            try:
                if mode == "Sum":
                    raw = wk_ops.project(raws, mode, np.uint32)
                    full_scale = scales.pop() * len(raws)
                else:
                    raw = wk_ops.project(raws, mode, raws[0].dtype)
                    full_scale = scales.pop()
                camera = cams.pop() if len(cams) == 1 else ""
            except Exception:
                raw = None
        elif any(s.measures_raw() for s in self._slots):
            note = ("not every image had its camera counts, or they were on different "
                    "scales — values here are display codes")

        h, w = out.shape[:2]
        self._add_derived_slot(out, f"{mode} of {len(bases)} images",
                               raw=raw, full_scale=full_scale, camera=camera,
                               note=note)
        self._after_change()
        self._say(f"{mode} of {len(bases)} images — {w} × {h} px, the area they all "
                  f"cover." + (f"  ({note})" if note else
                               "  Camera counts came along." if raw is not None else ""))

    def _do_merge(self):
        if not self._need_ops():
            return
        slot = self._slot()
        if slot is None:
            return
        if not (0 <= self._ref < len(self._slots)) or self._ref == self._active:
            QMessageBox.information(self, "Merge",
                                    "Choose a different image as the reference first.")
            return
        ref = self._slots[self._ref]
        try:
            out = wk_ops.merge_rg(render_view(slot.base, slot.view),
                                  render_view(ref.base, ref.view))
        except Exception as e:
            QMessageBox.warning(self, "Merge", f"{e}")
            return
        self._add_derived_slot(
            out, f"Red {slot.label[:20]} / green {ref.label[:20]}",
            note="two pictures in one — values are display codes")
        self._after_change()
        self._say("Merged: the active image is red, the reference is green. Yellow is "
                  "where both have signal.")

    # ───────────────────────────────────────────────── Compare ────

    def _update_compare(self, *_):
        mode = self._compare_cb.currentText()
        self._blend_lbl.setText(f"Mix: {self._blend_sl.value()} %")
        self._blend_sl.setEnabled(mode == "Blend")
        slot = self._slot()
        if mode == "Off" or slot is None or not (0 <= self._ref < len(self._slots)) \
                or self._ref == self._active:
            self._canvas.set_compare(None)
            if mode != "Off":
                self._say("Choose a different image as the reference to compare with.")
            return
        ref = self._slots[self._ref]
        a = render_view(slot.base, slot.view)
        b = render_view(ref.base, ref.view)
        a = np.stack([a] * 3, axis=2) if a.ndim == 2 else a
        b = np.stack([b] * 3, axis=2) if b.ndim == 2 else b

        if mode == "Side by side":
            h = max(a.shape[0], b.shape[0])
            gap = 8
            out = np.zeros((h, a.shape[1] + gap + b.shape[1], 3), np.uint8)
            out[:a.shape[0], :a.shape[1]] = a
            out[:b.shape[0], a.shape[1] + gap:] = b
        else:
            h = min(a.shape[0], b.shape[0])
            w = min(a.shape[1], b.shape[1])
            aa, bb = a[:h, :w].astype(np.int16), b[:h, :w].astype(np.int16)
            if mode == "Blend":
                t = self._blend_sl.value() / 100.0
                out = np.clip(bb + (aa - bb) * t, 0, 255).astype(np.uint8)
            else:
                out = np.abs(aa - bb).astype(np.uint8)
        self._canvas.set_compare(_np_to_qimage(out))
        self._say(f"{mode}: {slot.label[:24]}  vs  {ref.label[:24]}")

    # ──────────────────────────────────────────────────── Save ────

    def _render_for_save(self, slot: "_WorkshopSlot") -> "QImage | None":
        """Exactly what is on screen: the view settings applied, annotations painted on
        top at full resolution when the checkbox asks for them."""
        if slot is self._slot() and self._canvas.comparing():
            img = self._canvas.display_image()
            return None if img is None else img.copy()
        img = _np_to_qimage(render_view(slot.base, slot.view)).convertToFormat(
            QImage.Format.Format_RGB888)
        if self._cb_burn.isChecked() and slot.annots:
            p = QPainter(img)
            paint_annots(p, slot.annots, lambda x, y: QPointF(x, y), 1.0)
            p.end()
        return img

    def _is_source_file(self, slot, p: Path) -> bool:
        try:
            return (slot.source_path is not None and
                    p.resolve() == Path(slot.source_path).resolve())
        except Exception:
            return False

    def _save(self, fmt: str = "png"):
        slot = self._slot()
        if slot is None:
            QMessageBox.information(self, "Save", "There is no image to save.")
            return
        names = {"png": "PNG", "tiff": "TIFF", "jpg": "JPEG"}
        stem = _build_save_stem(slot)
        start = str(Path(self._last_save_dir or Path.home()) / f"{stem}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save a copy", start,
            f"{names.get(fmt, fmt.upper())} (*.{fmt}"
            + (" *.jpeg)" if fmt == "jpg" else ")"))
        if not path:
            return
        p = Path(path)
        if p.suffix.lower().lstrip(".") not in (fmt, "jpeg" if fmt == "jpg" else fmt):
            p = p.with_suffix(f".{fmt}")
        if self._is_source_file(slot, p):
            QMessageBox.warning(
                self, "Save",
                "That is the file this image came from.\n"
                "The Workshop never writes over a source file — choose another name.")
            return
        img = self._render_for_save(slot)
        if img is None:
            return
        self._last_save_dir = p.parent
        if _write_image(img, p):
            self._say(f"Saved a copy: {p}")
        else:
            QMessageBox.warning(self, "Save error", f"Could not write:\n{p}")

    def _save_data_tiff(self):
        """The measured values as a plain 16-bit TIFF.

        Separate from "Save TIFF…" on purpose, and both are needed. That one writes
        what is on SCREEN — the display stretch, the palette and the drawing baked into
        8-bit RGB, which is what a report wants. This one writes what was MEASURED, so
        the file can be read back and measured again."""
        if not self._need_ops():
            return
        slot = self._slot()
        if slot is None:
            return
        arr = slot.measure_arr()
        raw = slot.measures_raw()
        stem = _build_save_stem(slot)
        start = str(Path(self._last_save_dir or Path.home()) / f"{stem}_data.tif")
        path, _ = QFileDialog.getSaveFileName(self, "Save the values", start,
                                              "TIFF (*.tif *.tiff)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in (".tif", ".tiff"):
            p = p.with_suffix(".tif")
        if self._is_source_file(slot, p):
            QMessageBox.warning(self, "Save",
                                "That is the file this image came from — choose "
                                "another name.")
            return
        self._last_save_dir = p.parent
        desc = (f"{slot.camera or slot.label} | "
                f"{'camera pixel intensity' if raw else '8-bit intensity'}"
                f"{f' | full scale {slot.full_scale:.0f}' if slot.full_scale else ''}")
        err = wk_ops.write_data_tiff(arr, p, desc)
        if err:
            QMessageBox.warning(self, "Save error", f"Could not write:\n{p}\n\n{err}")
            return
        self._say(f"Values written to {p.name} — "
                  + ("the camera's own pixel intensity" if raw else
                     "8-bit intensity (this image has no camera values)")
                  + ", no drawing, no display settings.")

    # ---- writing in the background -------------------------------------------

    def _start_write(self, fn, busy_message: str):
        """Hand a file-writing job to a worker thread.

        Encoding a dozen frames took seconds on the GUI thread, and the window simply
        stopped responding while it happened. `fn` must already hold plain arrays and
        paths — no Qt objects cross the thread."""
        if self._save_task_busy:
            self._say("Still writing the last lot — one moment.")
            return
        self._save_task_busy = True
        self._update_enabled()
        self._say(busy_message)
        QThreadPool.globalInstance().start(_WriteTask(fn, self._write_sig))

    def _on_write_done(self, message: str, error: str):
        self._save_task_busy = False
        self._update_enabled()
        if error:
            QMessageBox.warning(self, "Save error", error)
            self._say("Writing failed.")
        else:
            self._say(message)

    def _save_all(self):
        if not self._slots:
            return
        fmt, ok = QInputDialog.getItem(self, "Save all", "Write them as:",
                                       list(_SAVE_FORMATS), 0, False)
        if not ok:
            return
        suffix = _FORMAT_SUFFIX[fmt]
        folder = QFileDialog.getExistingDirectory(
            self, "Save every image into…", str(self._last_save_dir or Path.home()))
        if not folder:
            return
        self._last_save_dir = Path(folder)
        jobs = []
        skipped = 0
        used = set()
        for slot in self._slots:
            img = self._render_for_save(slot)
            if img is None:
                skipped += 1
                continue
            stem = _build_save_stem(slot)
            target = Path(folder) / f"{stem}.{suffix}"
            n = 1
            # Two frames from the same camera in the same millisecond, or a name that
            # is already on disk, must not overwrite each other.
            while target.exists() or target in used:
                target = Path(folder) / f"{stem}_{n}.{suffix}"
                n += 1
            used.add(target)
            jobs.append((_qimage_to_np(img), target))
        if not jobs:
            self._say("Nothing to save.")
            return
        quality = 95 if fmt == "JPEG" else None

        def work():
            ok_n = fail_n = 0
            for arr, path in jobs:
                try:
                    im = _PilImg.fromarray(arr)
                    if quality is not None:
                        im.save(str(path), quality=quality)
                    else:
                        im.save(str(path))
                    ok_n += 1
                except Exception:
                    fail_n += 1
            msg = f"Saved {ok_n} image(s) as {fmt} into {folder}"
            if fail_n:
                msg += f", {fail_n} failed"
            if skipped:
                msg += f", {skipped} skipped"
            return msg, ""

        self._start_write(work, f"Writing {len(jobs)} image(s)…")

    # ---- animation -----------------------------------------------------------

    def _animation_frames(self):
        """(frames as RGB arrays, labels) for every open image, in list order."""
        frames, labels = [], []
        for slot in self._slots:
            img = self._render_for_save(slot)
            if img is None:
                continue
            frames.append(_qimage_to_np(img))
            labels.append(slot.label)
        return frames, labels

    def _play_images(self):
        if len(self._slots) < 1:
            return
        frames, labels = self._animation_frames()
        if len(frames) < 2:
            QMessageBox.information(
                self, "Play",
                "There is only one image open. Send over or open at least two.")
            return
        if self._play_dlg is None:
            self._play_dlg = _PlayDialog(self)
        self._play_dlg.set_frames([_np_to_qimage(f) for f in frames], labels)
        self._play_dlg._ms.setValue(self._anim_ms.value())
        self._play_dlg._cb_loop.setChecked(self._cb_anim_loop.isChecked())
        self._play_dlg.show()
        self._play_dlg.raise_()

    def _save_animation(self):
        if not self._need_ops():
            return
        frames, _labels = self._animation_frames()
        if len(frames) < 2:
            QMessageBox.information(
                self, "Save animation",
                "An animation needs at least two images. Open or send over some more.")
            return
        stem = _build_save_stem(self._slots[0]) if self._slots else "animation"
        start = str(Path(self._last_save_dir or Path.home()) / f"{stem}_animation.gif")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save animation", start,
            "Animated GIF (*.gif);;Animated PNG (*.png);;Animated WebP (*.webp)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() not in wk_ops.ANIM_SUFFIXES:
            p = p.with_suffix(".gif")
        self._last_save_dir = p.parent
        ms = int(self._anim_ms.value())
        loop = self._cb_anim_loop.isChecked()
        sizes = {(f.shape[1], f.shape[0]) for f in frames}
        n = len(frames)

        def work():
            err = wk_ops.write_animation(frames, p, ms, loop)
            if err:
                return "", f"Could not write the animation:\n{p}\n\n{err}"
            note = ("" if len(sizes) == 1 else
                    "  Images of different sizes were centred on black.")
            return (f"Animation saved: {p.name} — {n} frames, {ms} ms each"
                    f"{', repeating' if loop else ', played once'}.{note}"), ""

        self._start_write(work, f"Writing {n} frames…")

    # ---- session -------------------------------------------------------------

    def _save_session(self):
        """Write down the drawing, the regions, the scale and the display settings for
        every open image.

        The pictures themselves are NOT copied into the file — it stores where they came
        from. That keeps the file tiny and means it can never disagree with the archive;
        the cost is that an image the Workshop was handed without a file behind it
        cannot come back, and the count of those is reported."""
        if not self._slots:
            return
        start = str(Path(self._last_save_dir or Path.home()) / "workshop_session.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save session", start,
                                             "Workshop session (*.json)")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() != ".json":
            p = p.with_suffix(".json")
        images = []
        homeless = 0
        for slot in self._slots:
            if slot.source_path is None:
                homeless += 1
            h, w = slot.base.shape[:2]
            images.append({
                "label": slot.label,
                "source_path": None if slot.source_path is None else str(slot.source_path),
                "size": [int(w), int(h)],
                "px_per_mm": slot.px_per_mm,
                "px_um": slot.px_um,
                "px_um_measured": bool(slot.px_um_measured),
                "camera": slot.camera,
                "view": dict(vars(slot.view)),
                "annots": [a.to_dict() for a in slot.annots],
            })
        data = {"format": "ELI Image Tools Workshop session", "version": 1,
                "saved": datetime.now(tz=_TZ_PRAGUE).isoformat(timespec="seconds"),
                "images": images}
        try:
            p.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Could not write:\n{p}\n\n{e}")
            return
        self._last_save_dir = p.parent
        msg = f"Session saved: {p.name} — {len(images)} image(s)."
        if homeless:
            msg += (f" {homeless} of them has no file behind it, so only its drawing "
                    f"is written down, not the picture.")
        self._say(msg)

    def _load_session(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load session", str(self._last_save_dir or Path.home()),
            "Workshop session (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Load error", f"Could not read:\n{path}\n\n{e}")
            return
        images = data.get("images") if isinstance(data, dict) else None
        if not isinstance(images, list) or not images:
            QMessageBox.warning(self, "Load session",
                                "That file has no Workshop images in it.")
            return
        self._last_save_dir = Path(path).parent
        opened = missing = resized = 0
        for entry in images:
            if not isinstance(entry, dict):
                continue
            src = entry.get("source_path")
            if not src or not Path(src).exists():
                missing += 1
                continue
            before = len(self._slots)
            self.open_files([src])
            if len(self._slots) <= before:
                missing += 1
                continue
            slot = self._slots[-1]
            opened += 1
            if entry.get("label"):
                slot.label = str(entry["label"])
            try:
                slot.px_per_mm = (None if entry.get("px_per_mm") is None
                                  else float(entry["px_per_mm"]))
                slot.px_um = (None if entry.get("px_um") is None
                              else float(entry["px_um"]))
                slot.px_um_measured = bool(entry.get("px_um_measured", False))
            except (TypeError, ValueError):
                pass
            slot.view = _view_from_dict(entry.get("view"))
            annots = [_Annot.from_dict(d) for d in (entry.get("annots") or [])]
            slot.annots = [a for a in annots if a is not None]
            want = entry.get("size")
            h, w = slot.base.shape[:2]
            if isinstance(want, list) and len(want) == 2 and \
                    (int(want[0]), int(want[1])) != (w, h):
                # The drawing is in pixel coordinates, so a file that changed size since
                # the session was saved would put every region in the wrong place. Say
                # so rather than quietly moving things.
                resized += 1
        self._update_slot_list()
        if self._slots:
            self._activate_slot(len(self._slots) - 1)
        self._after_change()
        msg = f"Session loaded: {opened} image(s) re-opened."
        if missing:
            msg += f" {missing} could not be found."
        if resized:
            msg += (f" {resized} is no longer the size it was, so its drawing may not "
                    f"line up.")
        self._say(msg)

    def _copy_clipboard(self):
        slot = self._slot()
        if slot is None:
            return
        img = self._render_for_save(slot)
        if img is not None:
            QGuiApplication.clipboard().setImage(img)
            self._say("Copied to the clipboard.")

    # ──────────────────────────────────────────────────── Info ────

    def _update_info(self):
        slot = self._slot()
        if slot is None:
            self._info_lbl.setText("—")
            return
        h, w = slot.base.shape[:2]
        mode = "colour" if slot.base.ndim == 3 else "greyscale"
        bits = "16-bit values available" if slot.measures_raw() else "8-bit picture"
        view = "plain" if slot.view.is_neutral() else "display settings on"
        self._info_lbl.setText(
            f"<b>{slot.label[:44]}</b><br>"
            f"{w} × {h} px &nbsp;|&nbsp; {mode} &nbsp;|&nbsp; {bits}<br>"
            f"{len(slot.annots)} drawn item(s) &nbsp;|&nbsp; {len(slot.undo_stack)} step(s) "
            f"to undo &nbsp;|&nbsp; {view}")






