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

import os
import re
import sys
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
    QFontMetrics, QIcon, QPixmap, QPainterPath,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QFileDialog,
    QMessageBox, QSizePolicy, QScrollArea, QFrame, QToolButton, QButtonGroup,
    QColorDialog, QSplitter, QDialog, QInputDialog,
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

def _import_img_scale():
    """Load the shared intensity-scale helper (sibling img_scale.py): one instance per
    process, registered before exec, the same way if_t/sf_t/is_t do it."""
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


try:
    img_scale = _import_img_scale()
except Exception:                                    # pragma: no cover - standalone run
    img_scale = None


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
        "Hot":       _fallback_lut([(0, (0, 0, 0)), (0.33, (255, 0, 0)),
                                    (0.66, (255, 255, 0)), (1, (255, 255, 255))]),
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

#  Shapes whose geometry is a bounding box (two corner points) and which therefore get
#  the full eight resize handles.
_BOX_KINDS = {A_RECT, A_ELLIPSE, A_ROI_RECT, A_ROI_ELLIPSE}
#  Shapes defined by two free endpoints.
_SEG_KINDS = {A_LINE, A_ARROW, A_RULER, A_PROFILE}
#  Shapes that measure something and report it in the Measure section.
MEASURE_KINDS = {A_RULER, A_ROI_RECT, A_ROI_ELLIPSE, A_PROFILE, A_CROSS}


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

    def copy(self) -> "_Annot":
        return _Annot(self.kind, [list(p) for p in self.pts], self.color, self.width,
                      self.filled, self.text, self.font_size, self.label)

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
        return {"move": (cx, cy)}

    def set_handle(self, name: str, x: float, y: float, square: bool = False):
        if self.kind in _SEG_KINDS:
            if name == "p0":
                self.pts[0] = [x, y]
            elif name == "p1":
                self.pts[1] = [x, y]
            return
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
        if self.kind == A_FREE or self.kind == A_POLY:
            for a, b in zip(self.pts, self.pts[1:]):
                if _dist_to_segment(x, y, a, b) <= tol:
                    return True
            return False
        if self.kind == A_CROSS:
            return abs(x - x0) <= tol * 3 and abs(y - y0) <= tol * 3
        return (x0 - tol) <= x <= (x1 + tol) and (y0 - tol) <= y <= (y1 + tol)


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


def line_profile(arr: np.ndarray, p0, p1) -> "tuple[np.ndarray, np.ndarray]":
    """Values sampled along a line, one sample per pixel of length.
    Returns (distance in pixels, value)."""
    data = _to_gray(arr) if arr.ndim == 3 else arr
    h, w = data.shape[:2]
    n = max(2, int(round(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))) + 1)
    xs = np.clip(np.linspace(p0[0], p1[0], n), 0, w - 1)
    ys = np.clip(np.linspace(p0[1], p1[1], n), 0, h - 1)
    vals = data[np.rint(ys).astype(int), np.rint(xs).astype(int)].astype(np.float64)
    dist = np.hypot(xs - p0[0], ys - p0[1])
    return dist, vals


# ─────────────────────────────────────────────────────────────────
#  Slot — one image in the Workshop, with its edit history
# ─────────────────────────────────────────────────────────────────

_SLOT_UNDO_LIMIT = 30


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

    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)

    # Per-slot view position, so switching images and switching back keeps the zoom.
    zoom: float = 1.0
    offset: "tuple[float, float]" = (0.0, 0.0)
    fitted: bool = False

    def _state(self):
        # base / raw are never modified in place — every edit builds a new array — so
        # the history can share references instead of copying megabytes per step.
        return (self.base, self.raw, [a.copy() for a in self.annots], self.px_per_mm)

    def _restore(self, st):
        self.base, self.raw, self.annots, self.px_per_mm = st[0], st[1], st[2], st[3]

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
        return "counts" if self.measures_raw() else "code"

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
            if img_scale is not None:
                try:
                    camera = img_scale.camera_from_path(str(p)) or ""
                    full_scale = float(img_scale.full_scale_for_pil(str(p), info, mode))
                except Exception:
                    full_scale = None
            self._sig.done.emit(slot, arr, full_scale, camera, "")
        except Exception as e:
            self._sig.done.emit(slot, None, None, "", f"could not read source file ({e})")


# ─────────────────────────────────────────────────────────────────
#  Canvas
# ─────────────────────────────────────────────────────────────────

TOOL_PAN, TOOL_ZOOM, TOOL_SELECT = "pan", "zoom", "select"
TOOL_BRUSH, TOOL_LINE, TOOL_ARROW = "brush", "line", "arrow"
TOOL_RECT, TOOL_ELLIPSE, TOOL_POLY, TOOL_TEXT = "rect", "ellipse", "poly", "text"
TOOL_CROP, TOOL_EYEDROP, TOOL_ERASER = "crop", "eyedrop", "eraser"
TOOL_RULER, TOOL_ROI_RECT, TOOL_ROI_ELLIPSE = "ruler", "roi_rect", "roi_ellipse"
TOOL_PROFILE, TOOL_CROSS = "profile", "cross"

#  tool → the annotation it creates
_TOOL_ANNOT = {
    TOOL_BRUSH: A_FREE, TOOL_LINE: A_LINE, TOOL_ARROW: A_ARROW, TOOL_RECT: A_RECT,
    TOOL_ELLIPSE: A_ELLIPSE, TOOL_POLY: A_POLY, TOOL_TEXT: A_TEXT,
    TOOL_RULER: A_RULER, TOOL_ROI_RECT: A_ROI_RECT,
    TOOL_ROI_ELLIPSE: A_ROI_ELLIPSE, TOOL_PROFILE: A_PROFILE, TOOL_CROSS: A_CROSS,
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
    zoom_changed   = Signal(float)
    status         = Signal(str)
    profile_requested = Signal(object)      # _Annot of kind A_PROFILE

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

        self._new: "_Annot | None" = None
        self._press_pos: "QPointF | None" = None
        self._dragged = False
        self._rb_start: "QPointF | None" = None    # widget coords (zoom band / crop)
        self._rb_end: "QPointF | None" = None
        self._sel = -1
        self._drag_handle = ""
        self._drag_last: "QPointF | None" = None
        self._poly: "_Annot | None" = None
        self._text_active = False
        self._hover_img: "QPointF | None" = None

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
        self._sel = idx if 0 <= idx < len(self._annots()) else -1
        self.update()

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
                    dpx = math.hypot(a.pts[1][0] - a.pts[0][0], a.pts[1][1] - a.pts[0][1])
                    a.label = (f"{dpx:.1f} px" if not slot.px_per_mm
                               else f"{dpx / slot.px_per_mm:.3f} mm  ({dpx:.1f} px)")
                elif a.kind == A_PROFILE:
                    a.label = "profile — double-click to plot"
                elif a.kind == A_CROSS and a.pts:
                    x, y = int(round(a.pts[0][0])), int(round(a.pts[0][1]))
                    v = slot.value_at(x, y)
                    a.label = f"({x}, {y})  {v[1]:.0f} {unit}" if v else ""
                else:
                    st = region_stats(arr, _region_mask(a, arr.shape))
                    a.label = ("" if not st else
                               f"mean {st['mean']:.1f}  max {st['max']:.0f} {unit}")
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
                self._sel = idx
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
            self._sel = idx
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
                if self._drag_handle == "move":
                    a.translate(p.x() - self._drag_last.x(), p.y() - self._drag_last.y())
                    self._drag_last = p
                else:
                    a.set_handle(self._drag_handle, p.x(), p.y(),
                                 square=bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                self.update_labels()
                self.update()
            return

        if self._new is not None and left:
            p = self._widget_to_img(pos)
            if self._new.kind == A_FREE:
                self._new.pts.append([p.x(), p.y()])
            else:
                if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and \
                        self._new.kind in _BOX_KINDS:
                    x0, y0 = self._new.pts[0]
                    side = max(abs(p.x() - x0), abs(p.y() - y0))
                    p = QPointF(x0 + math.copysign(side, p.x() - x0),
                                y0 + math.copysign(side, p.y() - y0))
                self._new.pts[1] = [p.x(), p.y()]
            self._dragged = True
            self.update()
            return

        if self._poly is not None:
            p = self._widget_to_img(pos)
            self._poly.pts[-1] = [p.x(), p.y()]
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
            self._sel = idx
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
            self._new = None
            self._sel = -1
            self.update(); return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in(); return
        if k == Qt.Key.Key_Minus:
            self.zoom_out(); return
        if k == Qt.Key.Key_0:
            self.fit_to_view(); return
        if k == Qt.Key.Key_1:
            self.zoom_reset(); return

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
        self.tool = tool
        if tool != TOOL_SELECT:
            self._sel = -1
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

        if a.kind in (A_FREE, A_POLY) and len(a.pts) >= 2:
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


def _draw_caption(p: QPainter, a: "_Annot", to_widget, scale: float, col: QColor):
    """Measurement caption, drawn on a dark plate so it stays readable over both a
    black background and a saturated spot."""
    x0, y0, _, _ = a.bbox()
    anchor = to_widget(x0, y0)
    f = QFont("Segoe UI", max(7, int(round(9 * min(max(scale, 0.6), 2.0)))))
    p.setFont(f)
    fm = QFontMetrics(f)
    w = fm.horizontalAdvance(a.label) + 8
    h = fm.height() + 4
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
    """Histogram of the displayed 8-bit codes with draggable black and white points.

    This is the ImageJ way of setting a display range: you see where the data actually
    is, instead of guessing with two sliders."""

    changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(84)
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self._counts = np.zeros(256, dtype=np.float64)
        self._lo, self._hi = 0, 255
        self._drag = ""

    def set_data(self, gray: "np.ndarray | None"):
        if gray is None or gray.size == 0:
            self._counts = np.zeros(256, dtype=np.float64)
        else:
            s = _stat_sample(np.clip(gray, 0, 255).astype(np.uint8))
            self._counts = np.bincount(np.ravel(s), minlength=256).astype(np.float64)
        self.update()

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
        h = self.height() - 12
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
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setPen(QColor("#555"))
        p.setFont(QFont("Segoe UI", 7))
        p.drawText(2, 10, f"{self._lo}")
        p.drawText(self.width() - 26, 10, f"{self._hi}")

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
        self._hover = None

    def set_data(self, x, y, unit: str):
        self._x, self._y, self._unit = np.asarray(x), np.asarray(y), unit
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(52, 12, max(10, self.width() - 68), max(10, self.height() - 46))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        r = self._plot_rect()
        p.setPen(QPen(QColor("#888")))
        p.drawRect(r)
        if self._x.size < 2:
            return
        ymin, ymax = float(self._y.min()), float(self._y.max())
        if ymax <= ymin:
            ymax = ymin + 1.0
        xmax = float(self._x.max()) or 1.0

        p.setPen(QPen(QColor("#e6e6e6")))
        p.setFont(QFont("Segoe UI", 7))
        for i in range(5):
            yy = r.bottom() - r.height() * i / 4.0
            p.setPen(QPen(QColor("#ececec")))
            p.drawLine(QPointF(r.left(), yy), QPointF(r.right(), yy))
            p.setPen(QColor("#555"))
            p.drawText(QRectF(2, yy - 8, 46, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{ymin + (ymax - ymin) * i / 4.0:.0f}")
        p.setPen(QColor("#555"))
        p.drawText(QRectF(r.left(), r.bottom() + 4, r.width(), 16),
                   Qt.AlignmentFlag.AlignHCenter, f"distance along the line (px), 0 … {xmax:.0f}")

        poly = QPolygonF([
            QPointF(r.left() + r.width() * float(xv) / xmax,
                    r.bottom() - r.height() * (float(yv) - ymin) / (ymax - ymin))
            for xv, yv in zip(self._x, self._y)])
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#2f6fd0"), 1.6))
        p.drawPolyline(poly)

        if self._hover is not None and r.contains(self._hover):
            frac = (self._hover.x() - r.left()) / r.width()
            idx = int(max(0, min(self._x.size - 1, round(frac * (self._x.size - 1)))))
            hx = r.left() + r.width() * float(self._x[idx]) / xmax
            hy = r.bottom() - r.height() * (float(self._y[idx]) - ymin) / (ymax - ymin)
            p.setPen(QPen(QColor("#c02020"), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(hx, r.top()), QPointF(hx, r.bottom()))
            p.setBrush(QBrush(QColor("#c02020")))
            p.drawEllipse(QPointF(hx, hy), 3, 3)
            txt = f"{self._x[idx]:.1f} px   {self._y[idx]:.1f} {self._unit}"
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(0, 0, 0, 180)))
            p.drawRect(QRectF(r.left() + 4, r.top() + 4, 190, 18))
            p.setPen(QColor("#fff"))
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRectF(r.left() + 8, r.top() + 4, 186, 18),
                       Qt.AlignmentFlag.AlignVCenter, txt)

    def mouseMoveEvent(self, e):
        self._hover = QPointF(e.position())
        self.update()

    def leaveEvent(self, _e):
        self._hover = None
        self.update()


class ProfileDialog(QDialog):
    """Values along a line, with the numbers one click away."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Line profile")
        self.resize(640, 380)
        self._x = np.zeros(0)
        self._y = np.zeros(0)
        self._unit = ""
        lay = QVBoxLayout(self)
        self._info = QLabel("—")
        self._info.setStyleSheet("color: #222; font-size: 11px;")
        self._plot = _PlotWidget()
        lay.addWidget(self._info)
        lay.addWidget(self._plot, 1)
        row = QHBoxLayout()
        b_copy = _btn("Copy values", "Copy the samples to the clipboard as text")
        b_copy.clicked.connect(self._copy)
        b_csv = _btn("Save CSV…", "Write the samples to a CSV file")
        b_csv.clicked.connect(self._save_csv)
        row.addWidget(b_copy); row.addWidget(b_csv); row.addStretch(1)
        lay.addLayout(row)

    def set_profile(self, x, y, unit: str, title: str):
        self._x, self._y, self._unit = np.asarray(x), np.asarray(y), unit
        self._plot.set_data(x, y, unit)
        if y.size:
            self._info.setText(
                f"{title} — {y.size} samples, min {y.min():.0f}, max {y.max():.0f}, "
                f"mean {y.mean():.1f} {unit}")

    def _text(self) -> str:
        return "distance_px\tvalue\n" + "\n".join(
            f"{a:.3f}\t{b:.4f}" for a, b in zip(self._x, self._y))

    def _copy(self):
        QGuiApplication.clipboard().setText(self._text())

    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", "profile.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("distance_px,value\n")
                for a, b in zip(self._x, self._y):
                    f.write(f"{a:.3f},{b:.4f}\n")
        except Exception as e:
            QMessageBox.warning(self, "Save error", f"Could not save:\n{e}")


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
    TOOL_RULER: _ico_ruler,         TOOL_ROI_RECT: _ico_roi_rect,
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


# "180°" keeps its plain text: a third circular arrow next to Undo and Original
# would be one turning arrow too many to tell apart.
_ICON_RECIPES_ACTION = {
    "plus": _ico_plus, "minus": _ico_minus, "undo": _ico_undo, "redo": _ico_redo,
    "reset": _ico_reset, "rotate_left": _ico_rotate_left,
    "rotate_right": _ico_rotate_right,
    "flip_h": _ico_flip_h, "flip_v": _ico_flip_v,
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
    ("Ruler", "Ruler — measures length; set a scale to read it in millimetres", TOOL_RULER),
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
    "edit": "#2e9e5b", "compare": "#d08a1e", "save": "#c0392b",
}

COMPARE_MODES = ["Off", "Side by side", "Blend", "Difference"]


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
        self._profile_dlg: "ProfileDialog | None" = None

        self._raw_sig = _RawSignals()
        self._raw_sig.done.connect(self._on_raw_loaded)

        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(40)
        self._view_timer.timeout.connect(self._apply_view_now)

        self._ui_state = self._load_ui_state()
        self._build_ui()
        self._update_slot_list()
        self._set_tool(TOOL_PAN)
        self._update_enabled()

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
        self._build_edit_section()
        self._build_compare_section()
        self._build_save_section()
        self._panel_layout.addStretch(1)
        panel_scroll.setWidget(panel)

        self._canvas = WorkshopCanvas()
        self._canvas.image_changed.connect(self._on_image_changed)
        self._canvas.annots_changed.connect(self._on_annots_changed)
        self._canvas.color_picked.connect(self._on_color_picked)
        self._canvas.cursor_moved.connect(self._on_cursor_moved)
        self._canvas.zoom_changed.connect(self._on_zoom_changed)
        self._canvas.status.connect(self._say)
        self._canvas.profile_requested.connect(self._show_profile)
        self._canvas.files_dropped.connect(self.open_files)

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
        self._line_sb = QSpinBox(); self._line_sb.setRange(1, 60); self._line_sb.setValue(2)
        self._line_sb.setFixedWidth(48)
        self._line_sb.setToolTip("Thickness of lines and outlines. With something "
                                 "selected it changes that item.")
        self._line_sb.valueChanged.connect(self._on_style_changed)
        row2.addWidget(self._line_sb)

        row2.addWidget(_small_label("Brush"))
        self._brush_sb = QSpinBox(); self._brush_sb.setRange(1, 200); self._brush_sb.setValue(6)
        self._brush_sb.setFixedWidth(52)
        self._brush_sb.setToolTip("Thickness of the freehand line")
        self._brush_sb.valueChanged.connect(self._on_style_changed)
        row2.addWidget(self._brush_sb)

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

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #c3c9d2;")
        row2.addWidget(sep2)

        row2.addWidget(_small_label("Zoom"))
        self._zoom_sb = QDoubleSpinBox()
        self._zoom_sb.setRange(2.0, 6400.0)
        self._zoom_sb.setDecimals(0)
        self._zoom_sb.setSuffix(" %")
        self._zoom_sb.setValue(100.0)
        self._zoom_sb.setFixedWidth(84)
        self._zoom_sb.setKeyboardTracking(False)
        self._zoom_sb.setToolTip("Magnification — type a value or use the buttons")
        self._zoom_sb.valueChanged.connect(self._on_zoom_box)
        row2.addWidget(self._zoom_sb)
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
            row2.addWidget(b)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #c3c9d2;")
        row2.addWidget(sep3)

        self._btn_undo = _btn("Undo", "Take back the last change (Ctrl+Z)",
                              icon="undo")
        self._btn_undo.setShortcut("Ctrl+Z")
        self._btn_undo.clicked.connect(self._undo)
        self._btn_redo = _btn("Redo", "Put back what was taken back (Ctrl+Y)",
                              icon="redo")
        self._btn_redo.setShortcut("Ctrl+Y")
        self._btn_redo.clicked.connect(self._redo)
        self._btn_clear_annots = _btn("Clear drawing",
                                      "Remove every drawn and measured item")
        self._btn_clear_annots.clicked.connect(lambda: self._canvas.clear_annots())
        self._btn_reset_src = _btn("Original", "Go back to the image as it arrived",
                                   danger=True, icon="reset")
        self._btn_reset_src.clicked.connect(self._reset_to_source)
        for b in (self._btn_undo, self._btn_redo, self._btn_clear_annots, self._btn_reset_src):
            row2.addWidget(b)
        row2.addStretch(1)
        outer.addLayout(row2)
        return box

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
        b_open = _btn("Open file…", "Open an image from disk")
        b_open.clicked.connect(self._open_dialog)
        b_rm = _btn("Remove", "Take this image out of the Workshop", danger=True)
        b_rm.clicked.connect(self._remove_slot)
        b_clear = _btn("Clear all", "Take every image out of the Workshop", danger=True)
        b_clear.clicked.connect(self._clear_all)
        for b in (b_open, b_rm, b_clear):
            row.addWidget(b)
        lay.addLayout(row)

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
        b_whole.clicked.connect(lambda: (self._canvas.select_index(-1),
                                         self._refresh_measure()))
        b_profile = _btn("Plot profile", "Plot the values along the selected profile line")
        b_profile.clicked.connect(lambda: self._show_profile(self._canvas.selected()))
        row.addWidget(b_whole); row.addWidget(b_profile)
        lay.addLayout(row)

        lay.addWidget(_small_label("Scale"))
        srow = QHBoxLayout()
        self._scale_lbl = _small_label("not set — lengths in pixels")
        self._scale_lbl.setWordWrap(True)
        srow.addWidget(self._scale_lbl, 1)
        lay.addLayout(srow)
        srow2 = QHBoxLayout()
        b_setscale = _btn("Set scale…",
                          "Draw a ruler over something of a known size, select it, "
                          "then enter that size here")
        b_setscale.clicked.connect(self._set_scale)
        b_clrscale = _btn("Clear", "Go back to measuring in pixels")
        b_clrscale.clicked.connect(self._clear_scale)
        srow2.addWidget(b_setscale); srow2.addWidget(b_clrscale)
        lay.addLayout(srow2)

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

        lay.addWidget(_small_label("Against the reference image"))
        srow = QHBoxLayout()
        b_sub = _btn("Subtract", "Active minus reference, negatives cut to zero")
        b_sub.clicked.connect(lambda: self._do_diff(False))
        b_abs = _btn("Difference", "How far apart the two are, in either direction")
        b_abs.clicked.connect(lambda: self._do_diff(True))
        srow.addWidget(b_sub); srow.addWidget(b_abs)
        lay.addLayout(srow)

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
        row.addWidget(b_png); row.addWidget(b_tif)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        b_all = _btn("Save all…", "Write every image in the Workshop into one folder")
        b_all.clicked.connect(self._save_all)
        b_clip = _btn("Copy", "Put what you see on the clipboard")
        b_clip.clicked.connect(self._copy_clipboard)
        row2.addWidget(b_all); row2.addWidget(b_clip)
        lay.addLayout(row2)


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
        paths, _ = QFileDialog.getOpenFileName(
            self, "Open image", str(self._last_save_dir or Path.home()),
            "Images (*.png *.tif *.tiff *.jpg *.jpeg *.bmp)")
        if paths:
            self.open_files([paths])

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
                self._say(f"Native values loaded from the source file — "
                          f"measurements are in counts{f' ({camera})' if camera else ''}.")

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
        c.line_width = self._line_sb.value()
        c.brush_size = self._brush_sb.value()
        c.font_size = self._text_sb.value()
        c.fill_shapes = self._fill_cb.isChecked()
        c.draw_color = self._draw_color
        a = c.selected()
        if a is None:
            return
        a.color = self._draw_color.name()
        a.width = c.brush_size if a.kind == A_FREE else c.line_width
        a.font_size = c.font_size
        if a.kind in (A_RECT, A_ELLIPSE, A_POLY):
            a.filled = c.fill_shapes
        c.update()

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
            if slot is not None:
                gray = np.clip(_to_gray(slot.base), 0, 255)
                if v.auto_contrast:
                    lo, hi = auto_window(gray)
            self._hist.set_data(gray)
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

    def _on_zoom_box(self, pct: float):
        if self._sync_guard:
            return
        self._canvas.set_zoom_percent(pct)

    def _on_zoom_changed(self, _zoom: float):
        self._sync_guard = True
        self._zoom_sb.setValue(round(self._canvas.zoom_percent()))
        self._sync_guard = False
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
                    self._cursor_text = (f"x {ix}  y {iy}   {val:.0f} counts"
                                         f"{frac}   display code {code:.0f}")
                else:
                    self._cursor_text = f"x {ix}  y {iy}   value {code:.0f}"
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

    def _on_image_changed(self):
        self._after_change()

    def _on_annots_changed(self):
        self._after_change()

    def _refresh_measure(self):
        slot = self._slot()
        if slot is None:
            for c in self._cells.values():
                c.set_value("—")
            self._measure_src_lbl.setText("—")
            self._scale_lbl.setText("not set — lengths in pixels")
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

        src = (f"{what} — values in {unit}"
               + (f", full scale {slot.full_scale:.0f}" if slot.measures_raw()
                  and slot.full_scale else ""))
        if slot.raw_note:
            src += f"  ({slot.raw_note})"
        self._measure_src_lbl.setText(src)

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
        self._scale_lbl.setText(
            f"{slot.px_per_mm:.3f} px per mm — lengths in mm" if slot.px_per_mm
            else "not set — lengths in pixels")

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
        slot.push_undo()
        slot.px_per_mm = dpx / val
        self._after_change()
        self._say(f"Scale set: {slot.px_per_mm:.3f} pixels per millimetre.")

    def _clear_scale(self):
        slot = self._slot()
        if slot is None or slot.px_per_mm is None:
            return
        slot.push_undo()
        slot.px_per_mm = None
        self._after_change()
        self._say("Scale cleared — lengths are in pixels again.")

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
        x, y = line_profile(slot.measure_arr(), a.pts[0], a.pts[1])
        if self._profile_dlg is None:
            self._profile_dlg = ProfileDialog(self)
        self._profile_dlg.set_profile(x, y, slot.unit_name(), slot.label[:40])
        self._profile_dlg.show()
        self._profile_dlg.raise_()

    # ────────────────────────────────────────── Picture editing ───

    def _transform_annots(self, slot, fn):
        for a in slot.annots:
            a.pts = [list(fn(p[0], p[1])) for p in a.pts]

    def _rotate(self, k: int):
        slot = self._slot()
        if slot is None:
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
        if slot is None:
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
        if slot is None:
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
        if slot is None:
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

    def _save(self, fmt: str = "png"):
        slot = self._slot()
        if slot is None:
            QMessageBox.information(self, "Save", "There is no image to save.")
            return
        stem = _build_save_stem(slot)
        start = str(Path(self._last_save_dir or Path.home()) / f"{stem}.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save a copy", start, f"{'PNG' if fmt == 'png' else 'TIFF'} (*.{fmt})")
        if not path:
            return
        p = Path(path)
        if p.suffix.lower() != f".{fmt}":
            p = p.with_suffix(f".{fmt}")
        try:
            if slot.source_path is not None and \
                    p.resolve() == Path(slot.source_path).resolve():
                QMessageBox.warning(
                    self, "Save",
                    "That is the file this image came from.\n"
                    "The Workshop never writes over a source file — choose another name.")
                return
        except Exception:
            pass
        img = self._render_for_save(slot)
        if img is None:
            return
        self._last_save_dir = p.parent
        if _write_image(img, p):
            self._say(f"Saved a copy: {p}")
        else:
            QMessageBox.warning(self, "Save error", f"Could not write:\n{p}")

    def _save_all(self):
        if not self._slots:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Save every image into…", str(self._last_save_dir or Path.home()))
        if not folder:
            return
        self._last_save_dir = Path(folder)
        ok = fail = 0
        for slot in self._slots:
            img = self._render_for_save(slot)
            target = Path(folder) / f"{_build_save_stem(slot)}.png"
            n = 1
            while target.exists():
                target = Path(folder) / f"{_build_save_stem(slot)}_{n}.png"
                n += 1
            if img is not None and _write_image(img, target):
                ok += 1
            else:
                fail += 1
        self._say(f"Saved {ok} image(s) into {folder}" + (f", {fail} failed" if fail else ""))

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






