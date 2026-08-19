"""Intensity scale of archived camera frames — the ONE implementation.

    HOW THE ARCHIVE STORES INTENSITY

The archiver writes 16-bit frames with a power-of-two range stretched onto 65535:

    stored = raw_counts * 65535 / (2**bits - 1)

i.e. ×16.0037 at 12 bits, ×32.03 at 11, ×64.06 at 10, ×128.25 at 9, ×257 at 8, ×516 at
7, ×1040 at 6. The factor is fixed WITHIN a frame — it is not a per-frame normalisation
onto the full range: measured on the PD1–PD4 reference frames, a warmup frame with a
peak of 3378 counts stores a maximum of 54061 (= 3378 × 16.0037) and NOT 65535, and
dividing back gives exactly 3378.0. A per-frame stretch would pin every frame's maximum
to 65535 — three of the sixteen sampled frames sit below it.

    ...BUT `bits` IS NOT A PROPERTY OF THE CAMERA

It is the power-of-two BRACKET of that frame's own peak:

    bits = ceil(log2(MaxValue + 1))

Measured 18.08.2026 on 150 frames from six cameras (PCW3NF, PCM2NF, PTM11wNF, PTM11wFF,
PASF1NF, PFM13NF): the bracket derived from `MaxValue` alone matches the factor recovered
from the pixels on every single frame, 0 exceptions (`test_scale_invariance.py` asserts
this). So a camera whose peak drifts across a power of two switches factor from frame to
frame. C03-081-PCW3NF sits exactly on 1023/1024 and alternated ×64.06 / ×32.02 every few
seconds — in counts the frames were identical (median 68, p99.9 ≈ 685) while the stored
values differed by exactly 2.

Consequence: `stored / 65535` is NOT a stable scale. Rendering it directly made that
camera's picture double and halve in brightness shot to shot with nothing physical behind
it. The invariant quantity is COUNTS, so the display maps

    counts / (2**ref_bits - 1)      ref_bits = the largest bracket seen for this camera

which is the same thing as `stored / display_full_scale(frame_bits, ref_bits)` — no pixel
arithmetic, just a different denominator, and exactly 65535 whenever the frame's own
bracket is already the reference (see `display_full_scale`). `ref_bits` is remembered per
camera and only ever grows, so a picture can settle darker but can never oscillate; see
`reference_bits`.

    WHY NO OTHER COEFFICIENT IS NEEDED TO DISPLAY A FRAME

`stored/full_scale` and `counts/(2**ref_bits-1)` are the same linear map. The factor is
needed on top of that only to print a NUMBER in counts (readout, colorbar, thresholds),
and it is recoverable per frame as `stored_max / MaxValue` (see `derive_factor`).

`MaxValue` (PNG tEXt) is the **peak of that frame in raw counts** — not the sensor's
range. It reads 4095 on saturated 12-bit frames, which is what made it look like a
range. Scaling a frame by it (`MaxValue * arr / arr.max()`, then `/4095`) collapses to
`arr/65535 * (full_scale/4095)`: correct for 12-bit cameras only, and on the 6–11 bit
diode cameras it squeezes the frame into the bottom few percent of the range so a lit
diode array renders nearly black. With the tEXt chunk missing it is worse — a 0..65535
array divided by 4095 blows the whole frame out to white.

    WHAT STILL MAPS PER FRAME, ON PURPOSE

The `Binary` (min..max) and `False Colors` (p0.5..p99.5) palettes, and any explicit
Auto-stretch control. Both are deliberate and must stay: see `is_t.ADAPTIVE_PALETTES`
and `stretch_u8` below.

Pure numpy/PIL — no Qt, so every tab (`if_t`, `is_t`, `sf_t`, `wk_t`) can import it.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from pathlib import Path

import numpy as np

FULL_SCALE_16 = 65535.0

# Range of power-of-two brackets the archiver can plausibly have used.
MIN_BITS, MAX_BITS = 6, 16
# stored/raw factor for every one of them: 65535/(2**bits - 1).
SCALE_FACTORS: dict[int, float] = {
    bits: FULL_SCALE_16 / (2 ** bits - 1) for bits in range(MIN_BITS, MAX_BITS + 1)
}
# How far a measured ratio may sit from a tabulated factor and still count as that
# depth. 0.5 % separates neighbouring depths by a wide margin (they differ 2×) while
# absorbing the rounding in a single-pixel peak.
FACTOR_TOL = 0.005

# tEXt keys that carry the frame peak, most specific first. Looked up BY NAME: the
# reader this replaced took tEXt chunk number 12 (the Matlab `imgMeta.OtherText{12,2}`
# idiom), which is `MaxValue` in the files we happen to have and an arbitrary other
# number in anything written by a different IMAQ version.
_MAX_VALUE_KEYS = ("MaxValue", "max_value", "imgMaxValue",
                   "MaxSampleValue", "max_sample_value")

_STAT_MAX_SAMPLES = 250_000

# Where the learned per-camera reference brackets live. Same folder as the app's other
# per-camera state (cam_aspects.json, cam_layouts.json, ...). Deleting the file makes
# every camera re-learn from the frames it sees next.
CAM_DEPTHS_PATH = (Path(os.environ.get("APPDATA", Path.home()))
                   / "ELI_ImageTools" / "cam_depths.json")


# ── the archiver's per-frame bracket ──────────────────────────────────────────
def bits_from_max_value(max_value: "float | None") -> "int | None":
    """The power-of-two bracket the archiver stretched this frame by, from `MaxValue`.

    `ceil(log2(MaxValue + 1))`: a peak of 1023 counts is bracketed at 10 bits, 1024 at 11.
    Derived from metadata ALONE on purpose — the downscaled preview proxy has no
    full-resolution maximum to feed `derive_factor`, and preview and refined render must
    not be allowed to pick different brackets for the same frame. `derive_factor` stays
    the cross-check that this rule is still true (see test_scale_invariance.py).

    None when there is no usable `MaxValue`; the caller then renders on the plain 16-bit
    full scale, exactly as before this existed."""
    if max_value is None:
        return None
    try:
        mv = float(max_value)
    except (TypeError, ValueError):
        return None
    if not (mv > 0) or not math.isfinite(mv):
        return None
    return max(MIN_BITS, min(MAX_BITS, int(math.ceil(math.log2(mv + 1.0)))))


def display_full_scale(frame_bits: "int | None",
                       ref_bits: "int | None") -> float:
    """The denominator that renders a frame as `counts / (2**ref_bits - 1)`.

        stored / [(2**ref_bits - 1) * SCALE_FACTORS[frame_bits]]
      = (stored / SCALE_FACTORS[frame_bits]) / (2**ref_bits - 1)
      = counts / (2**ref_bits - 1)

    With `frame_bits == ref_bits` this is (2**b - 1) * 65535/(2**b - 1) = 65535 exactly, so
    a camera that never straddles a bracket renders bit-identically to the old code. Only
    the frames the archiver bracketed lower come out scaled — darker, never brighter,
    because `ref_bits` is the largest bracket seen."""
    if not frame_bits or not ref_bits:
        return FULL_SCALE_16
    return (2 ** int(ref_bits) - 1) * SCALE_FACTORS[int(frame_bits)]


# ── learned per-camera reference bracket ──────────────────────────────────────
_ref_lock = threading.Lock()
_ref_bits: "dict[str, int] | None" = None
# Bumped every time a camera's reference grows. Anything holding a RENDERED frame
# (a pixmap cache) has to notice, because those pixmaps were drawn against the old
# range — see reference_generation.
_ref_gen = 0


def _load_ref_bits() -> dict:
    try:
        data = json.loads(CAM_DEPTHS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for cam, b in (data or {}).items():
        try:
            bi = int(b)
        except (TypeError, ValueError):
            continue
        if MIN_BITS <= bi <= MAX_BITS:
            out[str(cam)] = bi
    return out


def _save_ref_bits(snapshot: dict) -> None:
    try:
        CAM_DEPTHS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CAM_DEPTHS_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True),
                                   encoding="utf-8")
    except Exception:
        pass    # a display preference is never worth failing a render over


def reference_bits(camera: "str | None", frame_bits: "int | None") -> "int | None":
    """The bracket this camera's frames are rendered against — the largest one seen.

    Monotone by design. Growing it darkens the camera's picture once and then holds it
    there; letting it shrink would put the flicker back, one frame at a time. So the
    worst case is a single step down in brightness when a frame finally exceeds the
    bracket — never an oscillation, and never an over-bright frame.

    Called from the loader worker threads, hence the lock. The file is written only on the
    handful of occasions the value actually grows."""
    if not frame_bits:
        return None
    if not camera:
        return int(frame_bits)
    global _ref_bits, _ref_gen
    with _ref_lock:
        if _ref_bits is None:
            _ref_bits = _load_ref_bits()
        cur = _ref_bits.get(camera)
        if cur is not None and cur >= int(frame_bits):
            return cur
        _ref_bits[camera] = int(frame_bits)
        # Only a GROWTH invalidates rendered frames. Recording a camera for the first time
        # cannot: under this lock there is exactly one first writer, so nothing has been
        # drawn against a different range yet — and bumping there would make every camera
        # clear the caches once on a fresh install, for nothing.
        if cur is not None:
            _ref_gen += 1
        snapshot = dict(_ref_bits)
    _save_ref_bits(snapshot)
    return int(frame_bits)


def current_reference_bits(camera: "str | None") -> "int | None":
    """The reference already learned for `camera`, without learning anything new.

    For render-time lookups: a stored preview frame keeps its OWN bracket, and the range
    it is drawn against has to be whatever the camera's reference is *now* — resolving it
    once at decode time would freeze half a window at the old value the moment a bigger
    frame turned up."""
    if not camera:
        return None
    global _ref_bits
    with _ref_lock:
        if _ref_bits is None:
            _ref_bits = _load_ref_bits()
        return _ref_bits.get(camera)


def reference_generation() -> int:
    """Counter that ticks whenever any camera's reference grows.

    A rendered pixmap is only valid for the reference it was drawn against, so every
    pixmap cache has to be dropped when this changes. It happens a handful of times in the
    life of an install — the first time each camera shows its bigger bracket — and never
    again once cam_depths.json knows the answer."""
    return _ref_gen


def full_scale_for_frame(camera: "str | None",
                         max_value: "float | None") -> "tuple[float, int | None, int | None]":
    """(full_scale, frame_bits, ref_bits) for one frame — the whole chain in one call.

    Every render path goes through this so they cannot disagree: metadata → bracket →
    learned reference → denominator."""
    fb = bits_from_max_value(max_value)
    rb = reference_bits(camera, fb)
    return display_full_scale(fb, rb), fb, rb


_CAM_IMG_MARK_RE = re.compile(r"[-_]+IMG(?=$|[-_])", re.IGNORECASE)


def camera_from_path(path) -> "str | None":
    """The camera a frame belongs to — its folder, when that folder is an archive camera
    folder ('C03-081-PCW3NF-_-IMG' → 'C03-081-PCW3NF').

    None for anything else, and that is the point: a picture the user opened from a
    Downloads folder or a Workshop export must not have a bracket learned against it, and
    with no camera the reference collapses to the frame's own — i.e. the plain 65535
    behaviour."""
    try:
        name = Path(path).parent.name
    except Exception:
        return None
    if not _CAM_IMG_MARK_RE.search(name):
        return None
    return _CAM_IMG_MARK_RE.sub("", name).strip("-_") or None


def full_scale_for_pil(path, info: dict, mode: str) -> float:
    """Absolute range for a frame opened with PIL — the one call the Finder / Shot Finder
    render paths need.

    The MODE decides 8-bit vs 16-bit and must never be replaced by a look at arr.max(): a
    genuinely dark 16-bit frame can hold nothing above 255 and would be brightened 257× by
    a value-based guess. A 16-bit frame then goes on the camera's reference range, so the
    Finder and the Slider agree about a camera that straddles a bracket."""
    if mode not in ("I", "I;16"):
        return 255.0
    return full_scale_for_frame(camera_from_path(path),
                                max_value_from_info(info or {}))[0]


# ── gamma ─────────────────────────────────────────────────────────────────────
# Gamma is the answer to "the absolute scale is correct but the picture is dark" that
# does NOT cost comparability:
#
#     code = 255 * (value / full_scale) ** gamma
#
# The curve depends only on the pixel value, never on the frame's content, so one colour
# still means one intensity in every frame and on every camera — the mapping is still a
# bijection of the absolute value, it is simply no longer a straight line. A colour
# boundary lands on a different count than at gamma 1, but it lands on the SAME count
# everywhere, and that is the property that makes a palette readable as a measurement.
#
# What it costs: equal count differences stop looking equally big (the dark end is
# expanded, the bright end compressed), so judging a RATIO by eye gets harder. Anything
# other than 1.0 therefore has to be named in the readout — a silent gamma is a lie about
# the picture.
#
# Range: below ~0.5 the frame goes milky and the top end stops separating; above 1.0
# darkens. Measured on real frames (PFM13 8-bit / PAM10 11-bit / PASF1 10-bit, mean code
# at gamma 1.0 = 55 / 58 / 21): gamma 0.5 gives 116 / 120 / 73, which is the usable
# working point.
GAMMA_MIN = 0.30
GAMMA_MAX = 1.50
GAMMA_NEUTRAL = 1.00
# Slider units: integer percent of gamma, so the value is exact in a cache key and a
# slider position maps to a gamma without rounding drift. 0 is the AUTO sentinel — see
# auto_gamma; it has to travel in the same field so one cache key covers the mode.
GAMMA_SLIDER_MIN = int(round(GAMMA_MIN * 100))
GAMMA_SLIDER_MAX = int(round(GAMMA_MAX * 100))
GAMMA_SLIDER_NEUTRAL = int(round(GAMMA_NEUTRAL * 100))
GAMMA_SLIDER_AUTO = 0
# Where Auto gamma puts the frame's median, as a fraction of the output range. 0.45 is
# just below mid-grey: high enough to read a dim frame, low enough that a normal frame is
# not pushed into the washed-out zone.
AUTO_GAMMA_TARGET = 0.45


def gamma_from_slider(v: "int | float | None") -> float:
    """Slider units → gamma. The AUTO sentinel and None both mean 'no fixed gamma'
    (1.0); resolving Auto needs the pixels, so callers use auto_gamma for that."""
    if v is None or int(v) == GAMMA_SLIDER_AUTO:
        return GAMMA_NEUTRAL
    return max(GAMMA_MIN, min(GAMMA_MAX, int(v) / 100.0))


def slider_from_gamma(g: float) -> int:
    """Gamma → slider units, for parking the slider where Auto landed."""
    if not (g > 0) or not math.isfinite(g):
        return GAMMA_SLIDER_NEUTRAL
    return int(round(max(GAMMA_MIN, min(GAMMA_MAX, float(g))) * 100))


def is_auto_gamma(v: "int | float | None") -> bool:
    return v is not None and int(v) == GAMMA_SLIDER_AUTO


def gamma_for_median(median_value: float, full_scale: float = FULL_SCALE_16,
                     target: float = AUTO_GAMMA_TARGET) -> float:
    """Gamma that lands `median_value` (a 16-bit value) at `target` of the output range.

    Split out from auto_gamma because the preview proxy does not hold the 16-bit frame —
    it holds 8-bit codes plus the LUT back to 16-bit. Order statistics survive a monotone
    LUT, so the proxy maps its median CODE through that LUT and calls this, which is what
    makes the preview paint agree with the refined render instead of merely resembling
    it."""
    med = float(median_value) / float(full_scale)
    if not (0.0 < med < 1.0):
        return GAMMA_NEUTRAL
    g = math.log(target) / math.log(med)
    if not math.isfinite(g):
        return GAMMA_NEUTRAL
    return max(GAMMA_MIN, min(GAMMA_MAX, g))


def auto_gamma(arr: np.ndarray, full_scale: float = FULL_SCALE_16,
               target: float = AUTO_GAMMA_TARGET) -> float:
    """Gamma that lands this frame's MEDIAN at `target` of the output range.

    Median, not mean: a dark field with one bright spot has a mean pulled up by the spot
    and would come out under-corrected, while the median describes what most of the frame
    is doing. Per-frame by definition — so, like Auto contrast, it trades comparability
    for legibility, and the value it picked is reported so the slider can show it."""
    if arr.size == 0:
        return GAMMA_NEUTRAL
    return gamma_for_median(float(np.median(stat_sample(arr))), full_scale, target)


# ── absolute scale ────────────────────────────────────────────────────────────
def to_absolute_u8(arr16: np.ndarray, full_scale: float = FULL_SCALE_16,
                   gamma: float = GAMMA_NEUTRAL) -> np.ndarray:
    """16-bit frame → uint8 on the camera's absolute full-scale range.

    Deterministic per pixel value, so brightness stays comparable between frames and
    between cameras without any per-frame auto-scaling. This is the mapping a palette
    needs to mean an intensity.

    `gamma` != 1 bends the curve without breaking that (see the gamma note above). It is
    applied here, at the ONE step that turns an absolute value into a code, so every
    caller — refined render, preview proxy, Finder, Shot Finder — bends it identically.
    Works on any float array of 16-bit values, including a 256-entry LUT."""
    frac = np.clip(arr16.astype(np.float32) * (1.0 / float(full_scale)), 0.0, 1.0)
    if gamma != GAMMA_NEUTRAL:
        frac = np.power(frac, float(gamma), dtype=np.float32)
    return np.clip(frac * 255.0, 0, 255).astype(np.uint8)


def to_u8(arr: np.ndarray, auto: bool = False,
          full_scale: float = FULL_SCALE_16,
          gamma: "int | float | None" = None) -> np.ndarray:
    """Frame → uint8 for display. THE decision point for what a palette colour means.

    Absolute (`auto=False`, the default): `value / full_scale`, the camera's own range.
    One colour is then one intensity, comparable between frames and between cameras —
    which is the whole reason a palette can be read as a measurement.

    `auto=True` is the explicit per-frame percentile stretch: it makes a dim frame
    readable at the price of comparability, so it belongs behind a visible switch and
    never in a default.

    `gamma` is in SLIDER units (percent, or the AUTO sentinel), because that is what the
    UI and the cache keys carry. It bends the absolute curve without costing
    comparability; Auto resolves per frame and does cost it. Ignored when `auto` is on —
    the percentile stretch already sets both ends, so stacking gamma on top would be two
    corrections fighting over the same frame.

    `full_scale` is 65535 for the archive's 16-bit frames (see the module docstring) and
    255 for an 8-bit source, which is already on its own full scale. Decide it from the
    decoded image's MODE, not from `arr.max()`: a genuinely dark 16-bit frame can hold
    nothing above 255 and would then be brightened 257×."""
    if auto:
        return stretch_u8(arr)
    g = (auto_gamma(arr, full_scale) if is_auto_gamma(gamma)
         else gamma_from_slider(gamma))
    return to_absolute_u8(arr, full_scale, g)


def stat_sample(a: np.ndarray) -> np.ndarray:
    """Deterministic subsample for percentile work.

    np.percentile over a native-resolution frame costs ~100 ms; a stride over ~250k
    pixels lands within a code or two. The stride is forced ODD so it cannot lock onto
    one set of columns — these sensors have vertical banding and an even stride divides
    the row width on every square frame."""
    n = a.size
    if n <= _STAT_MAX_SAMPLES:
        return a
    return np.ravel(a)[::(n // _STAT_MAX_SAMPLES) | 1]


def percentile_window(arr: np.ndarray, p_low: float = 0.5,
                      p_high: float = 99.5) -> "tuple[float, float] | None":
    """(lo, hi) percentile window of `arr`, or None when the data is uniform.

    Falls back to min/max if the percentile window is degenerate. The ONE place this
    window is computed — `stretch_u8` here and `is_t._stretch_arr_f` (which also
    reports the equivalent slider positions) both go through it."""
    if arr.size == 0:
        return None
    s = stat_sample(arr)
    lo = float(np.percentile(s, p_low))
    hi = float(np.percentile(s, p_high))
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        return None
    return lo, hi


def stretch_u8(arr: np.ndarray, p_low: float = 0.5, p_high: float = 99.5) -> np.ndarray:
    """Percentile contrast stretch → uint8. NOT comparable between frames.

    Take the stretch on the 16-BIT data, never on an 8-bit rendering of it: frames from
    the dim cameras occupy a handful of 8-bit codes (a PFM frame runs p0.5..p99.5 ≈
    1400..3060 of 65535 — six codes), and stretching six codes over the full range comes
    out in harsh posterised bands.

    Clipping a small fraction at the top means a few hot pixels cannot dominate the
    scale and crush the rest of the frame to black."""
    win = percentile_window(arr, p_low, p_high)
    if win is None:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo, hi = win
    return np.clip((arr.astype(np.float32) - lo) / (hi - lo) * 255.0,
                   0, 255).astype(np.uint8)


# ── counts / bit depth ────────────────────────────────────────────────────────
def derive_factor(stored_max: float, max_value: float) -> "tuple[float, int] | None":
    """(factor, bits) for a frame, from its stored maximum and its `MaxValue` peak.

    Returns None when the ratio matches no plausible depth — an unknown camera then
    shows no counts readout, and NOTHING about the displayed image changes, because the
    display does not use the factor at all."""
    if not (stored_max > 0) or not (max_value > 0):
        return None
    ratio = stored_max / max_value
    bits, factor = min(SCALE_FACTORS.items(), key=lambda kv: abs(kv[1] - ratio))
    if abs(factor - ratio) / factor > FACTOR_TOL:
        return None
    return factor, bits


def to_counts(arr: np.ndarray, factor: float) -> np.ndarray:
    """Stored 16-bit values → raw sensor counts (float)."""
    return arr.astype(np.float32) / float(factor)


def counts_from_stored(stored: float, factor: float) -> float:
    """One stored value → raw sensor counts."""
    return float(stored) / float(factor)


# ── metadata ──────────────────────────────────────────────────────────────────
class FrameMeta:
    """What a frame's PNG/TIFF metadata says about its intensity scale.

    `max_value` is the frame's peak in raw counts; `factor` / `bit_depth` are filled in
    only when a decoded array was available (`arr=`) — deriving them needs the stored
    maximum, and re-decoding a frame just to label it is not worth a share read."""

    __slots__ = ("max_value", "stored_max", "factor", "bit_depth",
                 "camera_type", "timestamp", "unit_length")

    def __init__(self, max_value=None, stored_max=None, factor=None, bit_depth=None,
                 camera_type=None, timestamp=None, unit_length=None):
        self.max_value = max_value
        self.stored_max = stored_max
        self.factor = factor
        self.bit_depth = bit_depth
        self.camera_type = camera_type
        self.timestamp = timestamp
        self.unit_length = unit_length

    @property
    def full_scale_counts(self) -> "int | None":
        return (2 ** self.bit_depth - 1) if self.bit_depth else None

    @property
    def peak_fraction(self) -> "float | None":
        """Frame peak as a fraction of the camera's full scale (0..1)."""
        if self.stored_max is None:
            return None
        return min(1.0, self.stored_max / FULL_SCALE_16)

    def scale_note(self, auto_stretch: bool = False,
                   gamma: "int | float | None" = None,
                   gamma_applied: "float | None" = None,
                   ref_bits: "int | None" = None) -> str:
        """One-line description of what the rendered intensities mean.

        The point of showing it is that a wrong scale becomes visible instead of
        silent — a black frame is then readable as "3 % of full scale", not as a
        broken viewer. A gamma other than 1 is named for the same reason: it changes
        which count a colour sits on, so it must never be invisible.

        `gamma_applied` is the resolved value, needed when `gamma` is the Auto sentinel —
        Auto picks per frame, so only the render knows what it used.

        `ref_bits` is named whenever the frame was rendered against a bracket other than
        the one the archiver stretched it by — that halves or doubles the picture, so a
        silent rescale would be exactly the lie this line exists to prevent."""
        parts = []
        if self.max_value is not None:
            parts.append(f"peak {self.max_value:.0f} counts")
        frac = self.peak_fraction
        if frac is not None:
            parts.append(f"{frac * 100:.0f} % FS")
        if self.bit_depth:
            parts.append(f"{self.bit_depth}-bit")
        if ref_bits and self.bit_depth and int(ref_bits) != int(self.bit_depth):
            parts.append(f"shown on {int(ref_bits)}-bit range")
        if auto_stretch:
            parts.append("auto stretch")
        else:
            parts.append("absolute scale")
            g = (gamma_applied if gamma_applied is not None
                 else gamma_from_slider(gamma))
            if g is not None and abs(g - GAMMA_NEUTRAL) > 0.005:
                parts.append(f"gamma {g:.2f}"
                             + (" (auto)" if is_auto_gamma(gamma) else ""))
        return "  ·  ".join(parts)


def _text_chunks(path: Path) -> dict:
    """PNG tEXt chunks as {key: str}. Empty for anything else.

    Deliberately PNG-only. A TIFF's `MaxSampleValue` (tag 281) is the DECLARED range of
    the format, not the peak of this frame, so feeding it in here would make every TIFF
    look like a saturated frame — and the empty-frame test that reads the peak would
    then never reject a dark one."""
    if path.suffix.lower() != ".png":
        return {}
    from PIL import Image as _PilImg
    with _PilImg.open(str(path)) as pil:
        return dict(pil.info)


def max_value_from_info(info: dict) -> "float | None":
    """The frame's peak in raw counts, from tEXt chunks looked up BY NAME.

    There is no numeric-scan fallback on purpose: the reader this replaced accepted any
    numeric chunk (by position) as the scale, and a frame written by a different IMAQ
    version was then rescaled by a nonsense factor with nothing to show it."""
    for key in _MAX_VALUE_KEYS:
        v = (info or {}).get(key)
        if v is None:
            continue
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError):
            continue
        if 0 < f <= FULL_SCALE_16:
            return f
    return None


def read_max_value(path: Path) -> "float | None":
    """The frame's peak in raw counts, from metadata alone (no pixel decode)."""
    try:
        return max_value_from_info(_text_chunks(Path(path)))
    except Exception:
        return None


def read_frame_meta(path: Path, arr: "np.ndarray | None" = None) -> FrameMeta:
    """Metadata of one frame, read from disk.

    Prefer `meta_from_info(img.info, arr)` when the frame is already open: on the SMB
    share a second open of the same file costs another 130–160 ms, and the display
    paths have the decoded array in hand anyway."""
    try:
        info = _text_chunks(Path(path))
    except Exception:
        info = {}
    return meta_from_info(info, arr)


def meta_from_info(info: dict, arr: "np.ndarray | None" = None) -> FrameMeta:
    """Metadata from an already-open image's `.info` dict. Pass `arr` (the decoded
    frame) when you have it — that is what makes `factor` / `bit_depth` available for
    free, since deriving them needs the stored maximum."""
    info = info or {}
    meta = FrameMeta(
        max_value=max_value_from_info(info),
        camera_type=info.get("Camera type"),
        timestamp=info.get("TimestampData"),
    )
    try:
        meta.unit_length = float(info["Unit length"])
    except (KeyError, TypeError, ValueError):
        pass
    if arr is not None and arr.size:
        meta.stored_max = float(arr.max())
        if meta.max_value is not None:
            got = derive_factor(meta.stored_max, meta.max_value)
            if got is not None:
                meta.factor, meta.bit_depth = got
    return meta
