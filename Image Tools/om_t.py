"""
om_t.py — One Moment

The transpose of every other tab in this program. The Slider and Image Finder answer
"one thing over time"; this tab answers "everything at one time".

Workflow, in the order the controls sit on screen (all of them in the LEFT panel, the
same place every other tab keeps its settings):

  1. TIME WINDOW — the Slider's own day + From/To picker, so a window picked there can
     be picked here the same way,
  2. CAMERA — the Slider's own camera picker, presets included,
  3. PVs — the Slider's own PV picker (search, added channels, formulas, names, units),
  4. LOAD — every picked PV is fetched for the window and drawn in ONE graph,
  5. drag on the graph to mark a time range → statistics per PV, in the left panel,
  6. CLICK the graph → that moment is resolved into one frame per picked camera, shown
     as tiles on the right (or in a pop-out window).

WHY A NEW MODULE AND NOT A MODE OF THE SLIDER
The Slider's state is a scan of folders for a camera set over a time window; this tab's
state is a set of PV series plus ONE timestamp. Bolting the second onto the first would
have meant a second meaning for every one of the Slider's ~200 members.

WHAT IS BORROWED, AND FROM WHERE  (nothing here re-implements a resolver or a renderer)
  * `image_slider` (is_t.py) — the PV registry and its picker (`PvConfigDialog`), the
    PV list widget (`PvValueTable`), the time-window picker (`DatePickerDialog`), the
    camera picker (`CameraPickerDialog`), the panel section (`CollapsibleSection`),
    the image renderer (`load_image_scaled` — palette, contrast, brightness, gamma),
    `container_root_for_year`, `parse_unix_ns_from_name`, `_cam_short_label`.
  * `shot_finder` (sf_t.py) — `_find_image_in_day`, the ONE frame-for-a-timestamp
    resolver in this program (hour-neighbour probing + the 30 s match window).
  * `cpva_client` — `get_day` (day cache), `day_bounds_ns`, `date_key_for_ns`.

ONE GRAPH, NOT ONE PER PV
Every picked PV is drawn in a single graph. PVs are grouped by UNIT and each unit group
gets its own y axis (the second on the right, further ones on outward-offset spines), so
a joule and a motor count are never plotted against the same scale and no value is
normalised — every number on screen is the number that was archived. "Stacked" is still
offered for the case of many unrelated PVs, where one graph becomes a tangle.

A FORMULA IS A CURVE LIKE ANY OTHER
A picked formula (derived PV) is fetched, computed and drawn: its sources are read even
when they are not themselves picked, evaluated on the union of their own timestamps with
each source held forward, and the result is a series the graph, the moment, the value
list and the range statistics all treat like a read channel. WHAT a formula means is
still is_t's answer — `pv_eval_derived`, the same evaluator the Slider and the Image
Finder use; what this module adds is the time base to evaluate it on. A formula with no
unit gets a y axis of its own, and its line BREAKS wherever one of its sources has no
value instead of being drawn straight across the gap.

WHAT IS REMEMBERED
The window, the cameras, the PV pick and eye state, the graph mode, the snap PV, the
display sliders and which sections are open — in `one_moment_ui_state.json` beside every
other tab's state file. Restoring reads no share and no archiver: Load stays a
deliberate press.
"""

import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta
from io import BytesIO
from pathlib import Path

import numpy as np

try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo("Europe/Prague")
except ImportError:                                   # pragma: no cover
    PRAGUE = None

from PySide6.QtCore import (Qt, QObject, QRunnable, QSize, QThreadPool, QTimer,
                            Signal)
from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


# ── sibling modules ───────────────────────────────────────────────────────────
# Same idiom as sf_t / if_t: register in sys.modules BEFORE exec so a re-entrant
# import cannot run the file twice, and reuse whatever main.py already loaded so a
# 25k-line module is never executed a second time.

def _load_sibling(mod_name: str, filename: str):
    mod = sys.modules.get(mod_name)
    if mod is not None:
        return mod
    import importlib.util as _ilu
    here = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    spec = _ilu.spec_from_file_location(mod_name, here / filename)
    mod = _ilu.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


cpva = _load_sibling("cpva_client", "cpva_client.py")
img_scale = _load_sibling("img_scale", "img_scale.py")

_SL = None
_SF = None
_WK = None


def _sl():
    """The Image Slider module — owner of the PV registry, the pickers and the
    image renderer."""
    global _SL
    if _SL is None:
        _SL = _load_sibling("image_slider", "is_t.py")
    return _SL


def _sf():
    """The Shot Finder module — owner of the frame-for-a-timestamp resolver."""
    global _SF
    if _SF is None:
        _SF = _load_sibling("shot_finder", "sf_t.py")
    return _SF


def _wk():
    """The Workshop module — owner of the painted button icons.

    Wrapped in its own try: a missing Workshop must cost this tab its icons, not its
    buttons (and `python om_t.py` on its own has to keep working)."""
    global _WK
    if _WK is None:
        try:
            _WK = _load_sibling("workshop", "wk_t.py")
        except Exception:
            _WK = False
    return _WK or None


def _icon(name: str, ink: str = "#1e2530"):
    """One painted button icon, or None when the Workshop is not there.

    Painted, not a text glyph: a glyph is whatever font the machine has, and it goes
    invisible the moment the button is disabled or checked. Never called at import
    time — QPixmap needs the application object."""
    wk = _wk()
    if wk is None:
        return None
    try:
        return wk.action_icon(name, ink)
    except Exception:
        return None


def _icon_btn(btn: QPushButton, name: str, text: str = "", ink: str = "#1e2530"):
    """Give `btn` a painted icon, falling back to `text` (which may carry a glyph)
    when the Workshop is unavailable."""
    ic = _icon(name, ink)
    if ic is not None:
        btn.setIcon(ic)
        btn.setIconSize(QSize(16, 16))
    elif text:
        btn.setText(text)
    return btn


def _mpl():
    """matplotlib pieces, imported on first use.

    Importing matplotlib costs ~0.9 s cold, and this tab is not the one the app opens
    on, so it is not paid until the graph is actually built."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
    from matplotlib.widgets import SpanSelector
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    return (Figure, FigureCanvasQTAgg, NavigationToolbar2QT, SpanSelector,
            FuncFormatter, MultipleLocator)


# ── constants ─────────────────────────────────────────────────────────────────
NS_PER_S = 1_000_000_000

# One frame per camera per moment; the share read is ~150 ms each, so they go in
# parallel. Eight is the Slider's own working number for the same share.
_FRAME_WORKERS = 8

# Longest side a tile is decoded to. A stored frame is ~1600 px wide and a wall of
# twenty of them at native size is ~100 MB of pixmaps for no visible gain.
_TILE_SIDE_MIN, _TILE_SIDE_MAX, _TILE_SIDE_DEFAULT = 120, 700, 280

# Narrowest a tile may be: the camera name and the time have to fit side by side even
# when the picture itself is smaller than they are.
_TILE_MIN_W = 132

# A click is a click, not a one-pixel drag: below this many pixels of travel the
# release sets the MOMENT instead of marking a range.
_CLICK_SLOP_PX = 5

# A slider is dragged, not clicked: re-rendering every camera at every tick would put
# twenty share reads on every pixel of travel. One render, once the hand stops.
_RENDER_SETTLE_MS = 220

_PLOT_COLORS = ["#1565C0", "#c62828", "#2e7d32", "#ef6c00", "#6a1b9a",
                "#00838f", "#9e9d24", "#4e342e", "#ad1457", "#283593"]

_HINT_STYLE = "color: #666; font-size: 11px;"
_HEAD_STYLE = "font-weight: 700; color: #111;"
_SMALL_BTN = "QPushButton { font-size: 11px; padding: 3px 6px; }"
# A picker button carries its own setting as its text, so the text is left-aligned
# next to the icon instead of centred under it.
_PICK_BTN = ("QPushButton { text-align: left; font-size: 11px; padding: 4px 6px; }")
_LOAD_BTN = (
    "QPushButton { background: #1565C0; color: #fff; font-weight: 700; "
    "padding: 5px 10px; border-radius: 3px; }"
    "QPushButton:hover { background: #0d47a1; }"
    # Muted, not pale: the disabled icon is painted grey, and on a pale blue it
    # would be the one control on the panel nobody can see.
    "QPushButton:disabled { background: #6f8bb0; color: #e8eef6; }")

# Left panel width. The Slider uses 275 px; this tab's statistics table needs the
# extra room for a number per PV.
_LEFT_W = 300

_NAME_W = 30            # width of the "Con:/Bri:/Gam:" labels, so the rows line up

def _state_path() -> Path:
    """This tab's own settings file. One file per tab, as everywhere else in this
    program: the PV REGISTRY is shared (pv_registry.json — which PVs exist), the
    SELECTION is not (which of them this tab shows).

    Read from the environment on every call rather than frozen at import, so a test
    can point APPDATA somewhere harmless."""
    return (Path(os.environ.get("APPDATA", Path.home()))
            / "ELI_ImageTools" / "one_moment_ui_state.json")


def _ns_to_prague(ts_ns: int) -> datetime:
    """UTC nanoseconds → Prague wall clock, timezone-aware."""
    from datetime import timezone as _tz
    dt = datetime.fromtimestamp(ts_ns / NS_PER_S, tz=_tz.utc)
    return dt.astimezone(PRAGUE) if PRAGUE is not None else dt


def _fmt_moment(ts_ns: int) -> str:
    d = _ns_to_prague(ts_ns)
    return d.strftime("%d.%m.%Y %H:%M:%S") + f".{(ts_ns % NS_PER_S) // 1_000_000:03d}"


def _fmt_hms(seconds: float) -> str:
    s = int(round(seconds)) % 86400
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _fmt_delta(dt_ns: int) -> str:
    """Signed offset of a frame from the moment asked for, in the units that read
    best. A frame is stored roughly every 35 s, so a few seconds off is normal and
    hiding it would be a lie about which shot is on screen."""
    s = dt_ns / NS_PER_S
    sign = "+" if s >= 0 else "−"
    s = abs(s)
    if s < 1.0:
        return f"{sign}{s * 1000:.0f} ms"
    return f"{sign}{s:.1f} s"


def _fmt_span(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


# Tick spacings a clock actually has. Left to itself matplotlib divides the span into
# round NUMBERS of seconds and labels a time axis 00:00:00, 02:46:40, 05:33:20 — ten
# thousand seconds apart, which nobody can read as a time.
_TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800,
                 3600, 7200, 10800, 21600, 43200, 86400)


def _tick_step_s(span_s: float, want: int = 10) -> float:
    """Tick spacing in seconds for a window `span_s` long: the smallest clock-shaped
    step that keeps the axis under `want` labels. Beyond a day it goes in whole days."""
    span_s = max(float(span_s), 1.0)
    for step in _TICK_STEPS_S:
        if span_s / step <= want:
            return float(step)
    days = int(span_s // 86400) + 1
    return float(86400 * max(1, -(-days // want)))


def _hsep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #ccc;")
    return f


def _date_keys_for_windows(windows: "list[tuple[int, int]]") -> "list[str]":
    """Every Prague day any of the picked windows touches, in order.

    A window is allowed to cross midnight (22:00 → 02:00 is a legal pick), and the
    archiver is queried a day at a time, so the day list is what the fetch iterates."""
    keys: "list[str]" = []
    for a, b in windows:
        d = _ns_to_prague(int(a)).date()
        last = _ns_to_prague(max(int(a), int(b) - 1)).date()
        while d <= last:
            k = d.strftime("%Y-%m-%d")
            if k not in keys:
                keys.append(k)
            d = d + timedelta(days=1)
    return keys


def _mask_to_windows(ts: np.ndarray, windows: "list[tuple[int, int]]") -> np.ndarray:
    """Boolean mask of the samples inside any picked window. Fetching is per day, so
    without this a 10:00–11:00 pick would still draw the whole day."""
    if not windows or ts.size == 0:
        return np.ones(ts.size, dtype=bool)
    m = np.zeros(ts.size, dtype=bool)
    for a, b in windows:
        m |= (ts >= int(a)) & (ts < int(b))
    return m


# ── a formula over time ───────────────────────────────────────────────────────
# The registry's own evaluator (is_t.pv_eval_derived) answers "what is this formula
# worth AT ONE MOMENT" and is what the Slider, the Image Finder and the burn-in all
# use. A graph needs the same answer at every moment of the window, which needs two
# things the scalar evaluator does not do: a time base to evaluate ON, and a way to
# do it 20 000 times without freezing anything. Both live here; what a formula MEANS
# still lives in is_t.

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
# median spacing, clamped. This facility mixes ~1 Hz shot channels with sensors read
# once an hour, so a single constant either blanks the slow one or carries a dead
# fast one across an hour the laser was off.
_HOLD_SLACK = 20.0
_HOLD_MIN_NS = 30 * NS_PER_S
_HOLD_MAX_NS = 3600 * NS_PER_S

_LOOP_STOP_EVERY = 512          # how often the per-point path looks at the stop flag
_LOOP_PROG_EVERY = 2048         # … and how often it reports progress

# Tokens whose MEANING changes between a float and an array: min()/max()/round()
# either reduce or raise, math.* takes scalars only, a conditional expression picks
# one whole branch by element 0, and an index or an attribute reaches into the array
# itself. Every one of these was measured against numpy, not guessed.
_NO_VECTOR_RE = re.compile(r"\b(?:min|max|round)\s*\(|\bif\b|\[|\.\s*[A-Za-z_]")

_EMPTY_TS = np.zeros(0, dtype=np.int64)
_EMPTY_VAL = np.zeros(0, dtype=np.float64)

_STATUS_RANK = {"ok": 0, "approx": 1, "stale": 2, "pending": 3, "error": 4}


def _worst_status(statuses) -> str:
    """The worst of a formula's source statuses — a number derived from a stale
    reading is itself stale, which is `pv_eval_derived`'s own rule."""
    worst = "ok"
    for s in statuses:
        if _STATUS_RANK.get(s, 0) > _STATUS_RANK.get(worst, 0):
            worst = s
    return worst


def derived_plan(names: "list[str]") -> "list[dict]":
    """The formulas among `names`, in registry definition order.

    A SNAPSHOT, taken on the GUI thread and handed to the worker: PV_DERIVED is
    written only by the picker, and a picker accepted mid-load must not change what
    is already being computed. `sources` is the recursive, cycle-guarded LEAF list —
    a formula chained onto another formula lists the second one's channels, because
    that is what the evaluator recomputes the chain from."""
    sl = _sl()
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


def _merge_base_ts(parts: "list[np.ndarray]",
                   gap_ns: int = _DERIVED_MERGE_GAP_NS) -> np.ndarray:
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


def _hold_index(src_ts: np.ndarray, base_ts: np.ndarray) -> np.ndarray:
    """Index of the source sample AT OR BEFORE every base timestamp; -1 where the
    source has nothing yet.

    One searchsorted, because none of cpva_client's look-back helpers takes an array
    (`value_at_or_before` is one query per timestamp, and it is the right tool for
    exactly one job — the seed before the window)."""
    base_ts = np.asarray(base_ts, dtype=np.int64)
    if src_ts is None or len(src_ts) == 0:
        return np.full(base_ts.size, -1, dtype=np.int64)
    return np.searchsorted(np.asarray(src_ts, dtype=np.int64),
                           base_ts, side="right").astype(np.int64) - 1


def _hold_limit_ns(src_ts: np.ndarray, channel: str) -> int:
    """How long one sample of `channel` may stand in for the value.

    A STEP channel's archiver record IS a step function — a sample only when the
    value changes — so at any instant the last sample is the true value however old
    it is. Everything else is limited, see _HOLD_SLACK."""
    if channel and channel in getattr(cpva, "STEP_CHANNELS", frozenset()):
        return int(np.iinfo(np.int64).max)
    if src_ts is None or len(src_ts) < 3:
        return _HOLD_MAX_NS
    step = float(np.median(np.diff(np.asarray(src_ts, dtype=np.int64))))
    return int(min(_HOLD_MAX_NS, max(_HOLD_MIN_NS, _HOLD_SLACK * step)))


def _align_source(name: str, d: dict, base_ts: np.ndarray,
                  seed: "tuple[int, float] | None" = None) -> np.ndarray:
    """One source's values on `base_ts`, held forward. NaN where it has no value.

    PV_SCALE is applied here: `pv_eval_derived` is documented to want the registry's
    own factor already in, so a formula cannot mean one thing here and another in
    the Slider."""
    scale = float(_sl().PV_SCALE.get(name, 1.0))
    base_ts = np.asarray(base_ts, dtype=np.int64)
    out = np.full(base_ts.size, np.nan, dtype=np.float64)
    if base_ts.size == 0:
        return out
    src_ts = np.asarray(d.get("ts") if d else _EMPTY_TS, dtype=np.int64)
    src_val = np.asarray(d.get("val") if d else _EMPTY_VAL, dtype=np.float64)
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

    # The seed is the last sample BEFORE the window: without it a source read once an
    # hour blanks the formula until its first in-window sample.
    if seed is not None:
        s_ts, s_val = int(seed[0]), float(seed[1])
        before = ~have
        if before.any():
            age = base_ts[before] - s_ts
            out[before] = np.where(age <= limit, s_val * scale, np.nan)
    return out


def _break_gaps(ts: np.ndarray, val: np.ndarray) -> "tuple[np.ndarray, np.ndarray]":
    """Drop the points the formula has no value for, but leave ONE NaN standing in
    each run of them. That NaN is what breaks the line instead of drawing it straight
    across an hour with no data, while keeping the arrays short enough for the
    statistics and the left-panel value to read directly."""
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
    # ±inf, and one inf in the array pushes matplotlib's y limits out to infinity and
    # flattens every real curve on that axis onto the edge.
    out[~np.isfinite(out)] = np.nan
    return ts[keep], out


def _can_vectorise(expr: str) -> bool:
    return bool(expr) and _NO_VECTOR_RE.search(expr) is None


def _compiled(expr: str):
    """The registry's OWN expression cache, so a formula is compiled once for the
    whole program and an unparsable one is remembered as False, exactly as
    pv_eval_derived remembers it."""
    cache = _sl()._PV_EXPR_CACHE
    code = cache.get(expr)
    if code is None:
        try:
            code = compile(expr, "<pv-derived>", "eval")
        except Exception:
            code = False
        cache[expr] = code
    return code


def _eval_vector(expr: str, ns: dict, n: int) -> "np.ndarray | None":
    """The formula on whole arrays, or None when the answer cannot be trusted.

    Evaluated in the registry's own sandbox (is_t._PV_EVAL_ENV), never in a
    numpy-flavoured copy of it. None comes back when the expression is vetoed, when
    eval raised, or when the result is not a float array of exactly `n` points — and
    that last test, not the veto, is what catches a reduction like min(A) collapsing
    into a perfectly straight, perfectly wrong line."""
    if not _can_vectorise(expr):
        return None
    code = _compiled(expr)
    if not code:
        return None
    try:
        with np.errstate(all="ignore"):          # A/0 → inf here, filtered later
            r = eval(code, _sl()._PV_EVAL_ENV, dict(ns))
    except Exception:
        return None
    if not isinstance(r, np.ndarray) or r.shape != (n,) or r.dtype.kind not in "fiu":
        return None
    return r.astype(np.float64, copy=False)


def _eval_loop(names: "list[str]", base_ts: np.ndarray, cols: dict, statuses: dict,
               stop=None, progress=None) -> "dict | None":
    """Point by point through is_t.pv_eval_derived — the SAME evaluator the Slider,
    the Image Finder and the burn-in use, so an expression the vectoriser refuses is
    still computed, and computed identically. One pass also covers a formula chained
    onto another one: the evaluator walks the registry in definition order and feeds
    each result forward.

    ~20 µs a point, so a 20 000-point day costs well under a second — which is why
    this runs in the load worker, looks at `stop` every 512 points and reports
    progress every 2048. Returns None when it was stopped."""
    sl = _sl()
    n = int(np.asarray(base_ts).size)
    out = {nm: np.full(n, np.nan, dtype=np.float64) for nm in names}
    keys = list(cols.keys())
    for i in range(n):
        if stop is not None and (i % _LOOP_STOP_EVERY) == 0 and stop.is_set():
            return None
        if progress is not None and i and (i % _LOOP_PROG_EVERY) == 0:
            progress(i, n)
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


def _spot_parity(name: str, base_ts: np.ndarray, cols: dict, statuses: dict,
                 vec: np.ndarray) -> bool:
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


def build_derived_series(plan: "list[dict]", series: dict,
                         windows: "list[tuple[int, int]]", seeds: "dict | None" = None,
                         stop=None, progress=None) -> dict:
    """{formula name → series entry}, built from the sources already fetched.

    ONE TIME BASE PER FORMULA — the union of its own leaf sources' timestamps, merged
    and held forward — and one base PER WINDOW, so a held value never crosses a
    stretch the user deliberately cut out of the pick (a 22:00→02:00 window, or two
    separate days).

    A formula with an unbound letter, no live source, or a base over
    _DERIVED_MAX_POINTS gets an EMPTY series carrying `reason`, never a silent
    absence: the left panel prints the reason where the value would be."""
    sl = _sl()
    seeds = seeds or {}
    out: dict = {}
    for spec in plan:
        nm = spec["name"]
        expr = spec["expr"]
        entry = {"channel": f"= {expr}" if expr else "= (empty)",
                 "role": "derived", "ts": _EMPTY_TS, "val": _EMPTY_VAL,
                 "status": "missing", "reason": ""}
        out[nm] = entry
        if not expr:
            entry["reason"] = "This formula has no expression yet."
            continue
        if spec["unbound"]:
            entry["reason"] = ("letter(s) " + ", ".join(spec["unbound"])
                               + " stand for no PV — re-open Select PVs and bind "
                                 "them again.")
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
                got = _eval_loop([nm], base, cols, statuses, stop=stop,
                                 progress=progress)
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
            entry["good"] = np.flatnonzero(np.isfinite(val_all))
            if ts_all.size == 0 and not entry["reason"]:
                entry["reason"] = ("every point of this window is missing at least "
                                   "one of the formula's sources.")
        elif not entry["reason"]:
            entry["reason"] = "no source sample inside the picked window."
    return out


# ── PV loading ────────────────────────────────────────────────────────────────
class _PvLoadSignals(QObject):
    """One signals object per WIDGET, not per task — a fresh QObject per worker is
    the ~3 KB-per-run leak this program has been bitten by before."""
    series = Signal(object, int)     # ({name: {...}}, generation)
    progress = Signal(str, int, int)  # (message, done, total)
    done = Signal(int, int)          # (generation, n_failed)


class _PvLoadTask(QRunnable):
    """Fetch every picked channel over the picked window(s), then compute the picked
    formulas over the same window(s).

    `cpva.get_day` is already cached and pooled, so this task's only job is to keep the
    waiting off the GUI thread, to stitch the days a window spans, and to report
    per-channel progress. The formulas are computed HERE and not on the GUI thread:
    the per-point fallback path can cost most of a second, the seeds need the network,
    and `_on_series` already delivers the finished dict under the generation guard."""

    def __init__(self, gen: int, date_keys: "list[str]",
                 windows: "list[tuple[int, int]]",
                 wanted: "list[tuple[str, str, str]]", plan: "list[dict]",
                 signals: _PvLoadSignals, stop_flag: "threading.Event"):
        super().__init__()
        self._gen = gen
        self._keys = list(date_keys)
        self._windows = list(windows)
        self._wanted = list(wanted)          # [(pv name, channel, role)]
        self._plan = list(plan)              # derived_plan() snapshot
        self._sig = signals
        self._stop = stop_flag

    def _seeds(self, series: dict) -> dict:
        """{(source name, window start) → (ts, value)} — the last sample BEFORE each
        window, so a source read once an hour does not blank the formula until its
        first in-window sample. One query per source per window, and the client
        caches it per day boundary."""
        need: "set[str]" = set()
        for spec in self._plan:
            need.update(s for s in spec["sources"] if s in series)
        seeds: dict = {}
        for name in sorted(need):
            channel = series[name].get("channel") or name
            for a, _b in self._windows:
                if self._stop.is_set():
                    return seeds
                try:
                    res = cpva.value_at_or_before(channel, int(a) - 1,
                                                  timeout=cpva.FULL_DAY_TIMEOUT)
                except Exception:
                    continue
                if getattr(res, "value", None) is not None and res.ts_ns is not None:
                    seeds[(name, int(a))] = (int(res.ts_ns), float(res.value))
        return seeds

    def run(self):
        out: dict = {}
        failed = 0
        total = len(self._wanted) + len(self._plan)
        done = 0
        # Serial on purpose: cpva_client holds its own connection pool and the day
        # cache means a repeat pick costs nothing. Firing eight day-queries at once
        # only moves the queue inside the client.
        for name, channel, role in self._wanted:
            if self._stop.is_set():
                return
            self._sig.progress.emit(f"Reading {name}…", done, total)
            ts_parts, val_parts = [], []
            status = "ok"
            for key in self._keys:
                if self._stop.is_set():
                    return
                try:
                    res = cpva.get_day(channel, key, timeout=cpva.FULL_DAY_TIMEOUT)
                    samples = res.samples or []
                    if res.status == "error":
                        status = "error"
                    elif res.status == "stale" and status == "ok":
                        status = "stale"
                except Exception as exc:
                    status = "error"
                    samples = []
                    self._sig.progress.emit(f"{name}: {type(exc).__name__}",
                                            done, total)
                if samples:
                    ts_parts.append(np.asarray([s[0] for s in samples],
                                               dtype=np.int64))
                    val_parts.append(np.asarray([s[1] for s in samples],
                                                dtype=np.float64))
            if status == "error":
                failed += 1
            ts = (np.concatenate(ts_parts) if ts_parts
                  else np.zeros(0, dtype=np.int64))
            val = (np.concatenate(val_parts) if val_parts
                   else np.zeros(0, dtype=np.float64))
            if ts.size:
                order = np.argsort(ts, kind="stable")
                ts, val = ts[order], val[order]
                keep = _mask_to_windows(ts, self._windows)
                ts, val = ts[keep], val[keep]
            out[name] = {"channel": channel, "ts": ts, "val": val,
                         "status": status, "role": role}
            done += 1
            self._sig.progress.emit(f"{name}: {ts.size} samples", done, total)

        if self._plan and not self._stop.is_set():
            self._sig.progress.emit("Computing formulas…", done, total)
            seeds = self._seeds(out)
            if self._stop.is_set():
                return
            got = build_derived_series(
                self._plan, out, self._windows, seeds=seeds, stop=self._stop,
                progress=lambda i, n: self._sig.progress.emit(
                    f"Computing formulas… {i}/{n}", done, total))
            out.update(got)
            done += len(self._plan)

        if self._stop.is_set():
            return
        self._sig.series.emit(out, self._gen)
        self._sig.done.emit(self._gen, failed)


# ── frame resolving and rendering ─────────────────────────────────────────────
# Two steps, not one. FINDING the frame for a moment means probing hour folders over
# the share; RENDERING it is what every display control changes. Splitting them means
# moving the Contrast slider re-renders what is already found instead of walking the
# share again for each of twenty cameras.

class _ResolveSignals(QObject):
    item = Signal(object, int)       # (item dict, generation)
    done = Signal(int)


class _ResolveTask(QRunnable):
    """One moment, many cameras → the file that holds each camera's frame.

    Resolution is `shot_finder._find_image_in_day` — the same resolver the Shot Finder
    uses, so a frame found here is the frame found there."""

    def __init__(self, gen: int, ts_ns: int, cams: "list[str]",
                 signals: _ResolveSignals, stop_flag: "threading.Event"):
        super().__init__()
        self._gen = gen
        self._ts_ns = int(ts_ns)
        self._cams = list(cams)
        self._sig = signals
        self._stop = stop_flag

    def _one(self, cam: str) -> dict:
        sl, sf = _sl(), _sf()
        res = {"cam": cam, "path": None, "ts_ns": None, "asked_ns": self._ts_ns,
               "note": ""}
        try:
            dt_p = _ns_to_prague(self._ts_ns)
            day = date(dt_p.year, dt_p.month, dt_p.day)
            day_dir = (sl.container_root_for_year(day.year)
                       / str(day.year) / str(day.month) / str(day.day))
            # Prague-naive: the resolver reads .hour and converts it to the UTC
            # folder hour itself.
            dt_naive = dt_p.replace(tzinfo=None)
            path, _cam_folder = sf._find_image_in_day(
                day, cam, dt_naive, self._ts_ns, {}, day_dir=day_dir)
            if path is None:
                res["note"] = "no frame near this moment"
                return res
            res["path"] = path
            res["ts_ns"] = sl.parse_unix_ns_from_name(path)
        except Exception as exc:
            res["note"] = f"{type(exc).__name__}"
        return res

    def run(self):
        if not self._cams:
            self._sig.done.emit(self._gen)
            return
        workers = min(_FRAME_WORKERS, len(self._cams))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for res in ex.map(self._one, self._cams):
                    if self._stop.is_set():
                        return
                    self._sig.item.emit(res, self._gen)
        finally:
            if not self._stop.is_set():
                self._sig.done.emit(self._gen)


class _RenderSignals(QObject):
    tile = Signal(object, int)       # (item dict with "img", generation)
    done = Signal(int)


class _RenderTask(QRunnable):
    """Already-found files → one picture per camera.

    The renderer is the Slider's `load_image_scaled`: the same absolute scale, the same
    palettes, the same Contrast / Brightness / Gamma meaning. Nothing about the picture
    is computed here, so a frame in this tab and the same frame in the Slider cannot
    drift apart."""

    def __init__(self, gen: int, items: "list[dict]", opts: dict,
                 signals: _RenderSignals, stop_flag: "threading.Event"):
        super().__init__()
        self._gen = gen
        self._items = [dict(it) for it in items]
        self._opts = dict(opts)
        self._sig = signals
        self._stop = stop_flag

    def _one(self, item: dict) -> dict:
        res = dict(item)
        res["img"] = None
        path = item.get("path")
        if path is None:
            return res
        o = self._opts
        try:
            img = _sl().load_image_scaled(
                Path(path), int(o["max_side"]), bool(o["auto_contrast"]),
                int(o["gradient_id"]), int(o["brightness"]),
                None, 0, int(o["contrast"]), int(o["auto_bright"]), 0, None,
                int(o["gamma"]))
            # The renderer hands back a QImage over a buffer it does not own; copy it
            # before it crosses the thread boundary.
            res["img"] = img.copy() if not img.isNull() else None
            if res["img"] is None:
                res["note"] = "could not be read"
        except Exception as exc:
            res["note"] = f"{type(exc).__name__}"
        return res

    def run(self):
        if not self._items:
            self._sig.done.emit(self._gen)
            return
        workers = min(_FRAME_WORKERS, len(self._items))
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for res in ex.map(self._one, self._items):
                    if self._stop.is_set():
                        return
                    self._sig.tile.emit(res, self._gen)
        finally:
            if not self._stop.is_set():
                self._sig.done.emit(self._gen)


# ── one tile ──────────────────────────────────────────────────────────────────
class _Tile(QFrame):
    """One camera's frame at the chosen moment.

    Two labels over the picture and nothing else — the camera and the time, the same
    pair the Slider puts over each of its camera panels. The picture area is sized to
    the picture, so a portrait frame does not sit in a black letterbox the width of the
    widest tile in the row (which is what the first version did)."""

    clicked = Signal(object)         # the tile's item dict

    _NAME_QSS = ("font-size: 11px; color: #eeeeee; background: #444444; "
                 "padding: 1px 4px; border-radius: 2px; border: none;")
    _TS_QSS = ("font-size: 11px; color: #ffd54f; background: #333333; "
               "padding: 1px 4px; border-radius: 2px; border: none;")
    _TS_QSS_MISS = ("font-size: 11px; color: #ffffff; background: #a02020; "
                    "padding: 1px 4px; border-radius: 2px; border: none;")

    def __init__(self, res: dict, parent=None):
        super().__init__(parent)
        self._res = dict(res)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QFrame { background: #222222; border-radius: 3px; }")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        img = res.get("img")
        pic_w = img.width() if img is not None and not img.isNull() else 0
        pic_h = img.height() if img is not None and not img.isNull() else 0
        head_w = max(_TILE_MIN_W, pic_w)

        sl = _sl()
        cam_label = sl._cam_short_label(res.get("cam", "")) or res.get("cam", "")
        name = QLabel(cam_label)
        name.setStyleSheet(self._NAME_QSS)
        name.setToolTip(res.get("cam", ""))
        name.setMinimumWidth(10)

        if res.get("ts_ns"):
            ts_txt = _ns_to_prague(res["ts_ns"]).strftime("%H:%M:%S")
            ts_qss = self._TS_QSS
        else:
            ts_txt = "no frame"
            ts_qss = self._TS_QSS_MISS
        ts = QLabel(ts_txt)
        ts.setStyleSheet(ts_qss)
        ts.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Name and time side by side while both fit; one over the other when they do
        # not. A caption that does not fit is a caption that gets clipped mid-letter,
        # and the two things this tile says are the only two things it says.
        # The time is never shortened; on a tile too narrow even for that, the tile
        # grows to it and the name is elided (the full one stays in the tooltip).
        name_w, ts_w = name.sizeHint().width(), ts.sizeHint().width()
        if name_w + ts_w + 3 <= head_w:
            top = QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(3)
            top.addWidget(name, 1)
            top.addWidget(ts, 0)
            content_w = max(pic_w, name_w + ts_w + 3)
        else:
            # Trimmed against the label's OWN measured width, one character at a time.
            # A font metric taken before the stylesheet is applied measures a different
            # font, and being eight pixels out is a clipped last letter.
            limit = max(_TILE_MIN_W, ts_w)
            txt = cam_label
            while len(txt) > 2 and name.sizeHint().width() > limit:
                txt = txt[:-1]
                name.setText(txt + "…")
            top = QVBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(1)
            top.addWidget(name)
            ts.setAlignment(Qt.AlignmentFlag.AlignLeft
                            | Qt.AlignmentFlag.AlignVCenter)
            top.addWidget(ts)
            content_w = max(pic_w, name.sizeHint().width(), ts_w)
        lay.addLayout(top)

        pic = QLabel()
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic.setStyleSheet("background: #1a1a1a; border: none;")
        if pic_w and pic_h:
            pic.setFixedSize(QSize(pic_w, pic_h))
            pic.setPixmap(QPixmap.fromImage(img))
        else:
            pic.setFixedSize(QSize(content_w, max(60, content_w // 2)))
            pic.setText(res.get("note") or "no frame")
            pic.setStyleSheet("background: #1a1a1a; color: #b0b0b0; border: none;")
            pic.setWordWrap(True)
        lay.addWidget(pic, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setFixedWidth(content_w + 6)

        # Everything the caption deliberately leaves out lives in the tooltip: how far
        # the frame is from the moment asked for, and where the file is.
        tip = [res.get("cam", "")]
        if res.get("ts_ns") and res.get("asked_ns"):
            tip.append(f"{_fmt_moment(res['ts_ns'])}   "
                       f"({_fmt_delta(res['ts_ns'] - res['asked_ns'])} "
                       f"from the moment picked)")
        if res.get("path") is not None:
            tip.append(str(res["path"]))
            tip.append("Click → send to Workshop")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("\n".join(t for t in tip if t))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._res.get("path"):
            self.clicked.emit(self._res)
        super().mouseReleaseEvent(event)


def _cols_for(width_px: int, tile_side: int, n: int) -> int:
    """How many tiles fit across `width_px`. One rule for the wall beside the graph
    and for the pop-out, so a pop-out looks like the wall it came from."""
    tile_w = max(_TILE_MIN_W, int(tile_side)) + 12
    avail = max(int(width_px) - 12, tile_w)
    return max(1, min(max(1, int(n)), avail // tile_w))


def _place_in_grid(grid: QGridLayout, tiles: "list[QWidget]", cols: int,
                   prev_cols: int = 0) -> int:
    """Lay `tiles` out in `cols` columns and return `cols`.

    The tiles are RE-PLACED, never rebuilt: moving a widget inside a QGridLayout is a
    pointer move, while rebuilding twenty of them is twenty QPixmap loads for a window
    that only got wider.

    The slack goes into a trailing row and column, never into the gaps between tiles:
    a wall of frames has to read as a wall. A grid never gives a column back, so the
    stretch of the column the LAST layout used has to be taken away by hand or the
    tiles keep drifting right."""
    while grid.count():
        grid.takeAt(0)
    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
    for i, t in enumerate(tiles):
        grid.addWidget(t, i // cols, i % cols, alignment=align)
    for c in range(max(int(prev_cols), cols) + 1):
        grid.setColumnStretch(c, 0)
    grid.setRowStretch(grid.rowCount(), 1)
    grid.setColumnStretch(cols, 1)
    return cols


class _MomentPopout(QDialog):
    """The same tiles in a window of their own, for when the panel beside the graph is
    too small to see twenty cameras in.

    It FOLLOWS the tab: `set_results` is called again on a new moment and after a
    re-render, because a window still showing the previous window's frames is worse
    than no window at all."""

    def __init__(self, title: str, results: "list[dict]", on_tile_clicked,
                 tile_side: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1150, 820)
        self._on_click = on_tile_clicked
        self._tile_side = int(tile_side)
        self._cols = 0
        lay = QVBoxLayout(self)
        self._head = QLabel(title)
        self._head.setStyleSheet(_HEAD_STYLE)
        lay.addWidget(self._head)
        area = QScrollArea()
        area.setWidgetResizable(True)
        self._area = area
        self._holder = QWidget()
        self._grid = QGridLayout(self._holder)
        self._grid.setSpacing(8)
        area.setWidget(self._holder)
        lay.addWidget(area, 1)
        self.set_results(title, results, self._tile_side)

    def set_results(self, title: str, results: "list[dict]", tile_side: int):
        self.setWindowTitle(title)
        self._head.setText(title)
        self._tile_side = int(tile_side)
        for t in self._holder.findChildren(_Tile):
            t.setParent(None)
            t.deleteLater()
        tiles = []
        for res in results:
            t = _Tile(res, self._holder)
            t.clicked.connect(self._on_click)
            tiles.append(t)
        self._place(tiles)

    def _place(self, tiles=None):
        if tiles is None:
            tiles = self._holder.findChildren(_Tile)
        n = len(tiles)
        self._cols = _place_in_grid(
            self._grid, tiles,
            _cols_for(self._area.viewport().width(), self._tile_side, n),
            self._cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._place)


# ── the tab ───────────────────────────────────────────────────────────────────
class OneMomentWidget(QWidget):
    """See the module docstring. Public members main.py wires up:
    `_workshop_ref`, `_workshop_tab_idx`, `_tab_widget`, and `cancel_scan()` for the
    window's Stop All button."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- state ----------------------------------------------------------
        today = date.today()
        key = today.strftime("%Y-%m-%d")
        # The default window is the whole of today: this tab is read as "the day", and
        # an hour-wide default would look like an empty archive on every first open.
        self._windows: "list[tuple[int, int]]" = [cpva.day_bounds_ns(key)]
        self._day: date = today                  # first day of the window
        self._pick_hours = (0, 0, 23, 59)        # h_from, m_from, h_to, m_to
        self._pick_segments = None
        self._pv_selected: "list[str]" = []      # picked PV names, registry order
        self._pv_hidden: "set[str]" = set()      # eye state — off the graph
        self._cams: "list[str]" = []             # picked camera folder names
        self._series: dict = {}                  # pv name → {"ts","val","status",…}
        self._plot_order: "list[str]" = []        # pv names actually drawn
        self._moment_ns: "int | None" = None
        self._span: "tuple[int, int] | None" = None    # (from_ns, to_ns)
        # x = 0 on the graph is Prague midnight of the window's first day; every
        # conversion between the axis and a timestamp goes through it.
        self._axis_t0_ns: int = self._windows[0][0]
        self._tile_items: "list[dict]" = []      # resolved files, before rendering
        self._tile_results: "list[dict]" = []    # rendered tiles
        self._axes: list = []                    # every axes, twins included
        self._axes_paint: list = []              # the axes cursor/span are drawn on
        self._ax_x = None                        # the axes whose x ticks are visible
        self._grid_cols = 0                      # columns the tile grid last used
        self._cursor_lines: list = []
        self._span_patches: list = []
        self._selectors: list = []               # SpanSelectors, one per axes
        self._press_px: "tuple[float, float] | None" = None
        self._popout_dlg: "QDialog | None" = None
        self._tiles: "dict[str, _Tile]" = {}     # camera → its tile widget
        # No control may write the settings file until the panel is finished, and the
        # remembered snap PV can only be applied once a graph exists to snap on.
        self._state_ready = False
        self._snap_wanted = ""

        self._pv_gen = 0
        self._res_gen = 0
        self._rnd_gen = 0
        self._pv_stop = threading.Event()
        self._res_stop = threading.Event()
        self._rnd_stop = threading.Event()
        self._pool = QThreadPool.globalInstance()

        # ONE signals object per widget. A fresh QObject per worker is a leak this
        # program has paid for before (~3 KB per run, never collected).
        self._pv_sig = _PvLoadSignals()
        self._pv_sig.series.connect(self._on_series)
        self._pv_sig.progress.connect(self._on_pv_progress)
        self._pv_sig.done.connect(self._on_pv_done)
        self._res_sig = _ResolveSignals()
        self._res_sig.item.connect(self._on_resolved)
        self._res_sig.done.connect(self._on_resolve_done)
        self._rnd_sig = _RenderSignals()
        self._rnd_sig.tile.connect(self._on_tile)
        self._rnd_sig.done.connect(self._on_tiles_done)

        # Display controls are dragged; the render waits for the hand to stop.
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_RENDER_SETTLE_MS)
        self._render_timer.timeout.connect(self._render_frames)

        # Light-palette host for every matplotlib toolbar this tab ever builds — see
        # _make_toolbar. Never shown; it exists only to own the palette at the moment
        # the toolbar is constructed, and to keep it alive afterwards.
        self._tb_host = QWidget(self)
        self._tb_host.setVisible(False)
        _hp = self._tb_host.palette()
        _hp.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
        _hp.setColor(QPalette.ColorRole.Button, QColor("#f0f0f0"))
        _hp.setColor(QPalette.ColorRole.WindowText, QColor("#202020"))
        _hp.setColor(QPalette.ColorRole.ButtonText, QColor("#202020"))
        self._tb_host.setPalette(_hp)

        # The PV registry — added channels, formulas, names, units — is shared with the
        # Slider and the Finder and lives in its own file. Whichever tab is built first
        # has to load it, or a PV added last week is missing from the picker here.
        try:
            _sl().pv_registry_load()
        except Exception:
            pass

        self._build_ui()
        # After the panel exists and before anything is shown: the remembered window,
        # cameras, PVs, sliders and open sections. Reading them costs no network and
        # no share — Load stays a deliberate press.
        self._restore_state()
        self._refresh_pick_labels()
        self._refresh_pv_table()
        self._refresh_stats()

    # ══════════════════════════ UI ════════════════════════════════════════════
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self._build_left_panel())

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(6)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._build_graph_pane())
        split.addWidget(self._build_moment_pane())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        # The graph is built LATER (the first Load), so when the splitter first lays
        # itself out the top pane holds nothing but a one-line placeholder — and the
        # handle stays where that put it. Without an explicit split the graph opens
        # about 90 px tall, which is too short for its own axis labels, let alone a
        # curve. Neither pane may be collapsed away entirely either.
        split.setChildrenCollapsible(False)
        split.setSizes([460, 440])
        self._split = split
        rlay.addWidget(split, 1)
        root.addWidget(right, 1)

    # ── left panel ────────────────────────────────────────────────────────────
    def _build_left_panel(self) -> QWidget:
        """Every setting of this tab, in the place the rest of the program keeps its
        settings: a scrollable column of collapsible sections on the left."""
        sl = _sl()
        Section = sl.CollapsibleSection

        scroll = QScrollArea()
        scroll.setFixedWidth(_LEFT_W)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }"
                             "QScrollBar:vertical { width: 8px; }")
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._sections: "dict[str, object]" = {}

        def _sec(key, title, expanded, accent):
            s = Section(title, key, expanded, accent=accent)
            s.toggled.connect(lambda *_: self._save_state())
            self._sections[key] = s
            lay.addWidget(s)
            return s

        s_src = _sec("source", "Source", True, "#2f6fd0")       # blue
        s_mom = _sec("moment", "The moment", True, "#d08a1e")   # amber
        s_pv = _sec("pv", "PV channels", True, "#1a9e9e")       # teal
        s_stat = _sec("stats", "Range statistics", True, "#2e9e5b")  # green
        s_disp = _sec("display", "Image / Display", True, "#7a4fc0")  # purple

        # ══════════════ SOURCE ══════════════════════════════════════════════
        # Each picker says on its own button what it is set to — a 300 px panel has
        # no room for a caption under every button, and a button that reads
        # "21.08. 07:00–19:00" needs no caption.
        row = QHBoxLayout()
        row.setSpacing(4)
        self._btn_window = QPushButton()
        _icon_btn(self._btn_window, "calendar")
        self._btn_window.setStyleSheet(_PICK_BTN)
        self._btn_window.clicked.connect(self._pick_window)
        row.addWidget(self._btn_window, 3)
        self._btn_cams = QPushButton()
        _icon_btn(self._btn_cams, "camera")
        self._btn_cams.setStyleSheet(_PICK_BTN)
        self._btn_cams.clicked.connect(self._pick_cameras)
        row.addWidget(self._btn_cams, 2)
        s_src.body_layout.addLayout(row)

        self._btn_load = QPushButton("Load")
        _icon_btn(self._btn_load, "play", "▶ Load", ink="#ffffff")
        self._btn_load.setToolTip("Read the picked PVs over the picked window and "
                                  "draw them")
        self._btn_load.setStyleSheet(_LOAD_BTN)
        self._btn_load.clicked.connect(self._load_day)
        s_src.body_layout.addWidget(self._btn_load)

        # ══════════════ THE MOMENT ══════════════════════════════════════════
        self._lbl_moment = QLabel("—")
        self._lbl_moment.setWordWrap(True)
        self._lbl_moment.setStyleSheet("font-weight: 700; color: #111;")
        s_mom.body_layout.addWidget(self._lbl_moment)

        row_nav = QHBoxLayout()
        self._btn_prev = QPushButton("prev")
        _icon_btn(self._btn_prev, "step_prev", "◀ prev")
        self._btn_prev.setToolTip("The shot before this one (previous sample of the "
                                 "snap PV)")
        self._btn_prev.setStyleSheet(_SMALL_BTN)
        self._btn_prev.clicked.connect(lambda: self._step_moment(-1))
        self._btn_next = QPushButton("next")
        _icon_btn(self._btn_next, "step_next", "next ▶")
        self._btn_next.setToolTip("The shot after this one")
        self._btn_next.setStyleSheet(_SMALL_BTN)
        self._btn_next.clicked.connect(lambda: self._step_moment(+1))
        for b in (self._btn_prev, self._btn_next):
            b.setEnabled(False)
            row_nav.addWidget(b)
        s_mom.body_layout.addLayout(row_nav)

        self._btn_popout = QPushButton("Pop out the frames")
        _icon_btn(self._btn_popout, "popout", "⧉ Pop out the frames")
        self._btn_popout.setToolTip("Show these frames in a window of their own")
        self._btn_popout.setStyleSheet(_SMALL_BTN)
        self._btn_popout.setEnabled(False)
        self._btn_popout.clicked.connect(self._popout)
        s_mom.body_layout.addWidget(self._btn_popout)

        # ══════════════ PV CHANNELS ═════════════════════════════════════════
        self._btn_pvs = QPushButton("Search / select PVs…")
        self._btn_pvs.setToolTip(
            "Search the archiver and pick the PVs to plot — the Image Slider's own "
            "picker, one shared PV list for the whole program. Added channels, "
            "formulas, names and units belong to every tab at once.")
        self._btn_pvs.clicked.connect(self._pick_pvs)
        s_pv.body_layout.addWidget(self._btn_pvs)

        row_mode = QHBoxLayout()
        row_mode.addWidget(QLabel("Graph:"))
        self._cmb_mode = QComboBox()
        self._cmb_mode.addItems(["One graph", "Stacked"])
        self._cmb_mode.setToolTip(
            "One graph: every PV in a single plot. PVs are grouped by unit and each "
            "unit gets its own y axis, so nothing is normalised — the numbers on the "
            "axes are the numbers that were archived.\n"
            "Stacked: one plot per PV, for when many unrelated PVs make a single graph "
            "unreadable.")
        self._cmb_mode.currentIndexChanged.connect(lambda *_: self._on_mode_changed())
        row_mode.addWidget(self._cmb_mode, 1)
        s_pv.body_layout.addLayout(row_mode)

        row_snap = QHBoxLayout()
        row_snap.addWidget(QLabel("Snap to:"))
        self._cmb_snap = QComboBox()
        self._cmb_snap.setToolTip(
            "Which PV's samples a click snaps onto. A click lands wherever the mouse "
            "was; the moment used is the nearest real sample of this PV, so the frames "
            "belong to a shot that exists.")
        self._cmb_snap.currentIndexChanged.connect(lambda *_: self._save_state())
        row_snap.addWidget(self._cmb_snap, 1)
        s_pv.body_layout.addLayout(row_snap)

        self._pv_table = sl.PvValueTable()
        self._pv_table.setToolTip("The picked PVs and their value at the moment. The "
                                  "eye takes a PV off the graph without unpicking it.")
        self._pv_table.eye_clicked.connect(self._on_pv_eye)
        s_pv.body_layout.addWidget(self._pv_table)
        self._lbl_no_pv = QLabel("No PV picked yet.")
        self._lbl_no_pv.setStyleSheet(_HINT_STYLE)
        s_pv.body_layout.addWidget(self._lbl_no_pv)

        # ══════════════ RANGE STATISTICS ════════════════════════════════════
        self._lbl_range = QLabel("Drag on the graph to mark a range.")
        self._lbl_range.setWordWrap(True)
        self._lbl_range.setStyleSheet(_HINT_STYLE)
        s_stat.body_layout.addWidget(self._lbl_range)

        self._stat_table = QTableWidget(0, 4)
        self._stat_table.setHorizontalHeaderLabels(["PV", "Mean", "± Std", "n"])
        hh = self._stat_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self._stat_table.verticalHeader().setVisible(False)
        self._stat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._stat_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._stat_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stat_table.setStyleSheet("font-size: 11px;")
        self._stat_table.setToolTip("Mean, spread and sample count over the marked "
                                    "range. Hover a row for the smallest, the largest "
                                    "and the peak-to-peak value.")
        s_stat.body_layout.addWidget(self._stat_table)

        self._btn_clear_range = QPushButton("Clear the range")
        self._btn_clear_range.setStyleSheet(_SMALL_BTN)
        self._btn_clear_range.setEnabled(False)
        self._btn_clear_range.clicked.connect(self._clear_span)
        s_stat.body_layout.addWidget(self._btn_clear_range)

        # ══════════════ IMAGE / DISPLAY ═════════════════════════════════════
        # The same four controls, with the same meanings, as the Image Slider: the
        # frames are rendered by the Slider's own renderer, so anything else would be
        # a second answer to a question that already has one.
        self._cb_contrast_auto = QCheckBox("Auto")
        self._cb_contrast_auto.setToolTip(
            "Auto-stretch contrast (percentile) — overrides the Contrast slider. "
            "Per-frame, so brightness stops being comparable between cameras.")
        self._cb_contrast_auto.stateChanged.connect(self._on_display_changed)
        self._sld_contrast = self._slider_row(
            s_disp, "Con:", -127, 127, 0,
            "Contrast — a multiplicative gain on the picture. 0 is the plain "
            "absolute scale.", self._cb_contrast_auto, "0")

        self._cb_bright_auto = QCheckBox("Auto")
        self._cb_bright_auto.setToolTip(
            "Auto-level brightness — overrides the Brightness slider.")
        self._cb_bright_auto.stateChanged.connect(self._on_display_changed)
        self._sld_bright = self._slider_row(
            s_disp, "Bri:", -255, 255, 0,
            "Brightness — an offset added to every pixel. 0 is the plain absolute "
            "scale.", self._cb_bright_auto, "0")

        self._cb_gamma_auto = QCheckBox("Auto")
        self._cb_gamma_auto.setToolTip(
            "Auto gamma — picks the curve that lands THIS frame's median at "
            f"{int(img_scale.AUTO_GAMMA_TARGET * 100)} % of the range. Per-frame, so "
            "it overrides the Gamma slider.")
        self._cb_gamma_auto.stateChanged.connect(self._on_display_changed)
        self._sld_gamma = self._slider_row(
            s_disp, "Gam:", img_scale.GAMMA_SLIDER_MIN, img_scale.GAMMA_SLIDER_MAX,
            img_scale.GAMMA_SLIDER_NEUTRAL,
            "Gamma of the display curve. 1.00 is the plain absolute scale; below 1 "
            "lifts the dark end (0.50 is the working point on these cameras).\n"
            "One pixel value still always gives one brightness, so frames stay "
            "comparable — what changes is that equal differences stop looking equal.",
            self._cb_gamma_auto, "1.00")

        row_pal = QHBoxLayout()
        row_pal.addWidget(QLabel("Palette:"))
        self._cmb_palette = QComboBox()
        for nm in sl.GRADIENT_NAMES:
            self._cmb_palette.addItem(nm)
        self._cmb_palette.setCurrentIndex(sl.GRADIENT_ID_GRAYSCALE)
        self._cmb_palette.setToolTip(
            "Colour palette of the frames.\n"
            "Default = the untouched file from the folder: no palette and no "
            "brightness/contrast, so those controls are greyed out while it is "
            "selected.")
        self._cmb_palette.currentIndexChanged.connect(self._on_palette_changed)
        row_pal.addWidget(self._cmb_palette, 1)
        s_disp.body_layout.addLayout(row_pal)

        row_size = QHBoxLayout()
        lbl_size = QLabel("Size:")
        lbl_size.setMinimumWidth(_NAME_W)
        row_size.addWidget(lbl_size)
        self._sld_size = QSlider(Qt.Orientation.Horizontal)
        self._sld_size.setRange(_TILE_SIDE_MIN, _TILE_SIDE_MAX)
        self._sld_size.setValue(_TILE_SIDE_DEFAULT)
        self._sld_size.setToolTip("Longest side of a frame on screen, in pixels")
        self._sld_size.valueChanged.connect(self._on_display_changed)
        row_size.addWidget(self._sld_size, 1)
        self._lbl_size = QLabel(str(_TILE_SIDE_DEFAULT))
        self._lbl_size.setFixedWidth(34)
        self._lbl_size.setStyleSheet("font-weight: 700;")
        row_size.addWidget(self._lbl_size)
        s_disp.body_layout.addLayout(row_size)

        self._sync_display_enabled()

        lay.addStretch(1)

        # Progress and status sit BELOW the sections and outside all of them: they
        # report on whatever is running, and a collapsed section must not be able to
        # hide the one line that says why nothing is happening.
        self._prog = QProgressBar()
        self._prog.setVisible(False)
        self._prog.setMaximumHeight(14)
        lay.addWidget(self._prog)
        self._status = QLabel("Pick a window and at least one PV, then press Load.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_HINT_STYLE)
        lay.addWidget(self._status)

        scroll.setWidget(host)
        return scroll

    def _slider_row(self, sec, name: str, lo: int, hi: int, val: int, tip: str,
                    auto_cb: QCheckBox, val_text: str) -> QSlider:
        """One "name · slider · number · reset · Auto" row, the Slider's own shape for
        an image adjustment."""
        row = QHBoxLayout()
        lbl = QLabel(name)
        lbl.setMinimumWidth(_NAME_W)
        lbl.setToolTip(tip)
        row.addWidget(lbl)
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(lo, hi)
        sld.setValue(val)
        sld.setToolTip(tip)
        sld.valueChanged.connect(self._on_display_changed)
        row.addWidget(sld, 1)
        out = QLabel(val_text)
        out.setFixedWidth(34)
        out.setToolTip(tip)
        out.setStyleSheet("font-weight: 700;")
        row.addWidget(out)
        btn = QPushButton()
        _icon_btn(btn, "reset", "↺")
        btn.setFixedSize(QSize(26, 22))
        btn.setStyleSheet("QPushButton { padding: 0; }")
        btn.setToolTip("Back to the neutral value")
        btn.clicked.connect(lambda *_: sld.setValue(val))
        row.addWidget(btn)
        row.addWidget(auto_cb)
        sec.body_layout.addLayout(row)
        sld._value_label = out          # read back by _on_display_changed
        return sld

    # ── right side ────────────────────────────────────────────────────────────
    def _build_graph_pane(self) -> QWidget:
        w = QWidget()
        # An axis label, a tick row and a curve need this much or the graph is a
        # smear: three y axes at 8 pt cannot be drawn in 90 px.
        w.setMinimumHeight(230)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        head = QHBoxLayout()
        t = QLabel("The window")
        t.setStyleSheet(_HEAD_STYLE)
        head.addWidget(t)
        hint = QLabel("·   click = pick that moment   ·   drag = mark a range")
        hint.setStyleSheet(_HINT_STYLE)
        head.addWidget(hint)
        head.addStretch(1)
        lay.addLayout(head)

        self._graph_holder = QWidget()
        self._graph_lay = QVBoxLayout(self._graph_holder)
        self._graph_lay.setContentsMargins(0, 0, 0, 0)
        self._graph_lay.setSpacing(0)
        self._canvas = None
        self._toolbar = None
        self._fig = None
        self._graph_lay.addWidget(self._empty_label("No data loaded yet."))
        lay.addWidget(self._graph_holder, 1)
        return w

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #999;")
        return lbl

    def _build_moment_pane(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        head = QHBoxLayout()
        self._lbl_frames = QLabel("The frames")
        self._lbl_frames.setStyleSheet(_HEAD_STYLE)
        head.addWidget(self._lbl_frames)
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(_hsep())

        self._tiles_area = QScrollArea()
        self._tiles_area.setWidgetResizable(True)
        self._tiles_host = QWidget()
        self._tiles_grid = QGridLayout(self._tiles_host)
        self._tiles_grid.setSpacing(6)
        self._tiles_grid.setContentsMargins(0, 0, 0, 0)
        self._tiles_area.setWidget(self._tiles_host)
        lay.addWidget(self._tiles_area, 1)

        self._tiles_hint = QLabel(
            "Click a moment in the graph to see every picked camera at that moment. "
            "Click a frame to send it to the Workshop.")
        self._tiles_hint.setStyleSheet(_HINT_STYLE)
        self._tiles_hint.setWordWrap(True)
        lay.addWidget(self._tiles_hint)
        return w

    # ══════════════════════════ remembered settings ═══════════════════════════
    # Written on every change rather than on close: a tab inside the main window is
    # never told the program is quitting, so anything saved "on the way out" is
    # saved never. Merged rather than replaced, so two writers cannot delete each
    # other's half of the document.

    @staticmethod
    def _read_state_file() -> dict:
        try:
            p = _state_path()
            if p.exists():
                got = json.loads(p.read_text(encoding="utf-8"))
                return got if isinstance(got, dict) else {}
        except Exception:
            pass
        return {}

    def _write_state_file(self, updates: dict):
        try:
            data = self._read_state_file()
            data.update(updates)
            p = _state_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                         encoding="utf-8")
        except Exception:
            pass

    def _state_snapshot(self) -> dict:
        sl = _sl()
        segs = None
        if self._pick_segments:
            segs = []
            for s in self._pick_segments:
                d, hf, mf, ht, mt = sl._seg_fields(s)
                segs.append({"date": d.isoformat(), "h_from": int(hf),
                             "m_from": int(mf), "h_to": int(ht), "m_to": int(mt)})
        out = {
            "day": self._day.isoformat(),
            "pick_hours": list(self._pick_hours),
            "segments": segs,
            "cams": list(self._cams),
            "pv_selected": list(self._pv_selected),
            "pv_hidden": sorted(self._pv_hidden),
            "graph_mode": self._cmb_mode.currentIndex(),
            "snap_pv": self._cmb_snap.currentText(),
            "tile_size": self._sld_size.value(),
            "palette": self._cmb_palette.currentIndex(),
            "contrast": self._sld_contrast.value(),
            "bright": self._sld_bright.value(),
            "gamma": self._sld_gamma.value(),
            "auto_contrast": self._cb_contrast_auto.isChecked(),
            "auto_bright": self._cb_bright_auto.isChecked(),
            "auto_gamma": self._cb_gamma_auto.isChecked(),
        }
        for key, sec in self._sections.items():
            out[f"sec_{key}"] = bool(getattr(sec, "_expanded", True))
        return out

    def _save_state(self):
        """Called from every control. Silent until the panel is fully built — a
        handler firing halfway through _build_ui would save a half-empty document
        over the real one."""
        if not getattr(self, "_state_ready", False):
            return
        self._write_state_file(self._state_snapshot())

    def _restore_window(self, st: dict):
        """The remembered time window. Restoring reads NOTHING — no share, no
        archiver: Load stays a deliberate press."""
        sl = _sl()
        try:
            self._pick_hours = tuple(int(v) for v in st["pick_hours"])[:4]
        except Exception:
            pass
        try:
            self._day = date.fromisoformat(str(st["day"]))
        except Exception:
            pass
        segs_in = st.get("segments")
        try:
            if segs_in:
                segs = [sl.PickSeg(date.fromisoformat(str(s["date"])),
                                   int(s["h_from"]), int(s["m_from"]),
                                   int(s["h_to"]), int(s["m_to"]))
                        for s in segs_in]
                windows = [sl.seg_bounds_ns(s) for s in segs]
                windows = [w for w in windows if w[1] > w[0]]
                if windows:
                    self._pick_segments = segs
                    self._windows = windows
            else:
                hf, mf, ht, mt = self._pick_hours
                seg = sl.PickSeg(self._day, hf, mf, ht, mt)
                a, b = sl.seg_bounds_ns(seg)
                if b > a:
                    self._pick_segments = None
                    self._windows = [(a, b)]
        except Exception:
            pass                    # a state file from another version: keep today
        self._axis_t0_ns = self._day_start_of(self._windows[0][0])

    def _restore_state(self):
        """Put the panel back the way it was left. Called at the END of _build_ui,
        with every control's signals blocked: a restored value must not be read back
        as a user action and re-saved, and must not kick off a render."""
        st = self._read_state_file()
        if not st:
            self._state_ready = True
            return
        self._restore_window(st)
        cams = st.get("cams")
        if isinstance(cams, list):
            self._cams = [str(c) for c in cams]
        pvs = st.get("pv_selected")
        if isinstance(pvs, list):
            known = set(_sl().pv_all_names())
            # A PV taken out of the shared registry since last time is dropped here,
            # not carried as a name nothing can read.
            self._pv_selected = [str(n) for n in pvs if str(n) in known]
        hid = st.get("pv_hidden")
        if isinstance(hid, list):
            self._pv_hidden = {str(n) for n in hid} & set(self._pv_selected)

        pairs = ((self._cmb_mode, "graph_mode"), (self._cmb_palette, "palette"))
        for cmb, key in pairs:
            try:
                i = int(st[key])
            except Exception:
                continue
            if 0 <= i < cmb.count():
                cmb.blockSignals(True)
                cmb.setCurrentIndex(i)
                cmb.blockSignals(False)
        for sld, key in ((self._sld_size, "tile_size"),
                         (self._sld_contrast, "contrast"),
                         (self._sld_bright, "bright"),
                         (self._sld_gamma, "gamma")):
            try:
                v = int(st[key])
            except Exception:
                continue
            sld.blockSignals(True)
            sld.setValue(max(sld.minimum(), min(sld.maximum(), v)))
            sld.blockSignals(False)
        for cb, key in ((self._cb_contrast_auto, "auto_contrast"),
                        (self._cb_bright_auto, "auto_bright"),
                        (self._cb_gamma_auto, "auto_gamma")):
            if key in st:
                cb.blockSignals(True)
                cb.setChecked(bool(st[key]))
                cb.blockSignals(False)
        for key, sec in self._sections.items():
            want = st.get(f"sec_{key}")
            if want is not None and hasattr(sec, "set_expanded"):
                sec.set_expanded(bool(want))

        self._snap_wanted = str(st.get("snap_pv") or "")
        # One pass to bring the labels and the greying-out in line with what was
        # just restored — and only then may saving start.
        self._sync_display_enabled()
        self._on_display_changed()
        self._state_ready = True

    # ══════════════════════════ pickers ═══════════════════════════════════════
    def _refresh_pick_labels(self):
        """The two pickers say what they are set to ON the button, so a 300 px panel
        does not spend two more lines on captions. A formula counts as loadable
        through its SOURCES: a formula-only pick is legal, one whose letters point at
        deleted PVs is not."""
        n_pv = len(_sl().pv_source_names(self._pv_selected))
        self._btn_window.setText(self._window_text())
        self._btn_window.setToolTip(self._window_tip())
        self._btn_cams.setText(f"{len(self._cams)} camera(s)" if self._cams
                               else "Cameras…")
        self._btn_cams.setToolTip(
            ("Pick the cameras whose frame you want at the moment you click in the "
             "graph.\nPicked now: " + ", ".join(_sl()._cam_short_label(c) or c
                                                for c in self._cams))
            if self._cams else
            "Pick the cameras whose frame you want at the moment you click in the "
            "graph")
        self._btn_load.setEnabled(n_pv > 0)

    def _window_text(self) -> str:
        """What goes ON the window button — short enough to fit beside the camera
        button. The full sentence is the button's tooltip."""
        if not self._windows:
            return "Time window…"
        a = _ns_to_prague(self._windows[0][0])
        b = _ns_to_prague(self._windows[-1][1])
        if len(self._windows) > 1:
            return (f"{len(self._windows)} days  "
                    f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}")
        return (f"{a.strftime('%d.%m.')}  {a.strftime('%H:%M')}–"
                f"{b.strftime('%H:%M')}")

    def _window_tip(self) -> str:
        base = ("Pick the day and the From/To times — the Image Slider's own picker, "
                "so a window picked there is picked here the same way.\nOnly the "
                "samples inside the window are read and drawn.")
        if not self._windows:
            return base
        a = _ns_to_prague(self._windows[0][0])
        b = _ns_to_prague(self._windows[-1][1])
        total = sum((w[1] - w[0]) for w in self._windows) / NS_PER_S
        if len(self._windows) > 1:
            now = (f"{len(self._windows)} days, {a.strftime('%d.%m.')}–"
                   f"{b.strftime('%d.%m.%Y')}   ·   {_fmt_span(total)}")
        else:
            now = (f"{a.strftime('%d.%m.%Y')}   {a.strftime('%H:%M')} → "
                   f"{b.strftime('%H:%M')}   ·   {_fmt_span(total)}")
        return f"Picked now: {now}\n\n{base}"

    def _pick_window(self):
        """The Slider's time-window picker — the same calendar, on a button.

        Only the window is taken from it: this tab has no live mode and no folder
        scan, so the Live tick is hidden rather than shown doing nothing, and the
        dialog's camera preload is ignored on purpose."""
        sl = _sl()
        hf, mf, ht, mt = self._pick_hours
        try:
            dlg = sl.DatePickerDialog(None, hf, ht, self,
                                      min_from_init=mf, min_to_init=mt,
                                      init_date=self._day,
                                      init_segments=self._pick_segments,
                                      allow_live=False)
        except TypeError:               # an older Slider without the flag
            dlg = sl.DatePickerDialog(None, hf, ht, self,
                                      min_from_init=mf, min_to_init=mt,
                                      init_date=self._day,
                                      init_segments=self._pick_segments)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        windows = [w for w in dlg.selected_windows() if w[1] > w[0]]
        if not windows:
            return
        self._windows = windows
        self._pick_segments = dlg.selected_segments()
        self._pick_hours = dlg.selected_times()
        segs = self._pick_segments
        self._day = (_ns_to_prague(windows[0][0]).date() if segs
                     else dlg.selected_date_obj())
        self._axis_t0_ns = self._day_start_of(windows[0][0])
        # The loaded series belong to the PREVIOUS window; keeping them would draw
        # yesterday's samples under today's axis. A load still in flight belongs to
        # it too, so it is abandoned rather than left to deliver into the new state.
        self._abandon_load()
        self._series = {}
        self._plot_order = []
        self._span = None
        self._clear_graph("Window changed — press Load.")
        self._invalidate_moment()
        self._refresh_pick_labels()
        self._refresh_pv_table()
        self._refresh_stats()
        self._save_state()
        self._status.setText("Window changed — press Load.")

    def _abandon_load(self):
        """Give up on a PV load that is still running. Without this the task passes
        its generation guard and repopulates the series with the PREVIOUS pick — a PV
        (or a formula) taken out a moment ago arrives and is drawn."""
        self._pv_stop.set()
        self._pv_stop = threading.Event()
        self._pv_gen += 1
        self._prog.setVisible(False)
        self._btn_load.setEnabled(True)

    def _invalidate_moment(self):
        """There is no picked moment any more — say so everywhere at once.

        Clearing the state alone used to leave the old timestamp on the panel, the old
        "The frames at …" title over an empty wall, prev/next enabled over nothing, and
        a pop-out window showing the previous window's frames."""
        self._moment_ns = None
        self._res_stop.set()
        self._res_stop = threading.Event()
        self._res_gen += 1
        self._rnd_stop.set()
        self._rnd_stop = threading.Event()
        self._rnd_gen += 1
        self._render_timer.stop()
        self._tile_items = []
        self._tile_results = []
        self._clear_tiles()
        self._close_popout()
        self._lbl_moment.setText("—")
        self._btn_prev.setEnabled(False)
        self._btn_next.setEnabled(False)
        self._btn_popout.setEnabled(False)
        self._lbl_frames.setText("The frames")
        self._tiles_hint.setText(
            "Click a moment in the graph to see every picked camera at that moment. "
            "Click a frame to send it to the Workshop.")

    @staticmethod
    def _day_start_of(ts_ns: int) -> int:
        """Prague midnight of the day `ts_ns` falls in — the x = 0 of the graph, so a
        tick reads as a wall clock time."""
        return cpva.day_bounds_ns(_ns_to_prague(int(ts_ns)).strftime("%Y-%m-%d"))[0]

    def _pick_cameras(self):
        """The Slider's camera picker, presets included — so a camera set saved there
        is offered here."""
        sl = _sl()
        hf, _mf, ht, mt = self._pick_hours
        # The scan itself follows `windows`, which carries the minutes; the hours are
        # what the dialog PRINTS, and a 10:30→11:15 pick must not read as 10→11.
        if mt:
            ht = min(23, ht + 1)
        dlg = sl.CameraPickerDialog(self._day, hf, ht, self._cams, self,
                                    windows=list(self._windows))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._cams = dlg.selected_camera_names()
        self._refresh_pick_labels()
        self._save_state()
        if self._moment_ns is not None:
            self._resolve_frames()

    def _pick_pvs(self):
        """The Slider's picker over the SHARED registry — the same dialog, the same
        search, the same added PVs and formulas, in every tab. Only the SELECTION
        belongs to this tab."""
        sl = _sl()
        dlg = sl.PvConfigDialog(self._pv_selected, sl.PV_CUSTOM_CHANNELS,
                                sl.PV_DERIVED, sl.PV_LABELS,
                                hidden=self._pv_hidden,
                                units=sl.PV_CUSTOM_UNITS, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sl.PV_CUSTOM_CHANNELS.clear()
        sl.PV_CUSTOM_CHANNELS.update(dlg.custom_channels())
        sl.PV_DERIVED[:] = dlg.derived_defs()
        sl.PV_LABELS.clear()
        sl.PV_LABELS.update(dlg.labels())
        sl.PV_CUSTOM_UNITS.clear()
        sl.PV_CUSTOM_UNITS.update(dlg.custom_units())
        sl.pv_registry_save()
        self._pv_selected = dlg.selected_names()
        self._pv_hidden = set(dlg.hidden_names()) & set(self._pv_selected)
        # The loaded series belong to the PREVIOUS list; a PV added just now has no
        # samples, so the graph would silently keep the old set. A load in flight
        # belongs to the old list too — see _abandon_load.
        self._abandon_load()
        self._series = {}
        self._plot_order = []
        self._clear_graph("PV list changed — press Load.")
        self._invalidate_moment()
        self._refresh_pick_labels()
        self._refresh_pv_table()
        self._refresh_stats()
        self._save_state()
        self._status.setText("PV list changed — press Load.")

    def _on_mode_changed(self):
        self._rebuild_graph()
        self._save_state()

    def _on_pv_eye(self, name: str):
        """The eye takes a PV off the GRAPH without unpicking it: its samples stay
        loaded and its value stays listed, so hiding one of eight PVs to read the other
        seven costs nothing and no re-read."""
        if name in self._pv_hidden:
            self._pv_hidden.discard(name)
        else:
            self._pv_hidden.add(name)
        self._refresh_pv_table()
        self._rebuild_graph()
        self._save_state()

    # ══════════════════════════ loading ═══════════════════════════════════════
    def _load_day(self):
        sl = _sl()
        picked = list(self._pv_selected)
        read = [n for n in picked if not sl.pv_is_derived(n)]
        # A formula's sources must be FETCHED even when the user did not tick them,
        # or the formula silently reads n/a — the same rule the Image Finder follows.
        helpers = [s for s in sl.pv_source_names(picked) if s not in read]
        plan = derived_plan(picked)
        wanted = ([(n, sl.pv_channel_for(n) or n, "read") for n in read]
                  + [(n, sl.pv_channel_for(n) or n, "helper") for n in helpers])
        if not wanted:
            QMessageBox.information(
                self, "One Moment",
                "Nothing to read: every letter of the picked formula(s) stands for a "
                "PV that no longer exists. Re-open Select PVs and bind them again.")
            return
        if not self._windows:
            return

        self._pv_stop.set()                     # abandon whatever is still running
        self._pv_stop = threading.Event()
        self._pv_gen += 1
        self._invalidate_moment()
        keys = _date_keys_for_windows(self._windows)
        self._prog.setVisible(True)
        self._prog.setRange(0, len(wanted) + len(plan))
        self._prog.setValue(0)
        self._btn_load.setEnabled(False)
        bits = f"Reading {len(read) + len(helpers)} PV(s)"
        if plan:
            bits += f" and computing {len(plan)} formula(s)"
        self._status.setText(f"{bits} over {len(keys)} day(s)…")
        self._pool.start(_PvLoadTask(self._pv_gen, keys, self._windows, wanted,
                                     plan, self._pv_sig, self._pv_stop))

    def _on_pv_progress(self, msg: str, done: int, total: int):
        self._prog.setRange(0, max(total, 1))
        self._prog.setValue(done)
        self._status.setText(msg)

    def _on_series(self, series: dict, gen: int):
        if gen != self._pv_gen:
            return
        self._series = series
        self._span = None
        self._invalidate_moment()
        self._rebuild_graph()
        self._refresh_pv_table()
        self._refresh_stats()

    def _on_pv_done(self, gen: int, failed: int):
        if gen != self._pv_gen:
            return
        self._prog.setVisible(False)
        self._btn_load.setEnabled(True)
        # A helper is a formula's source the user never asked to see: reporting it as
        # "no samples" would name a PV that is not on the panel at all.
        empty = [n for n, d in self._series.items()
                 if len(d["ts"]) == 0 and d.get("role") != "helper"]
        bits = [f"{len(self._plot_order)} PV(s) drawn"]
        if empty:
            bits.append("no samples in this window: " + ", ".join(empty))
        if failed:
            bits.append(f"{failed} archiver error(s)")
        self._status.setText("   ·   ".join(bits) or "Nothing to draw.")

    def cancel_scan(self):
        """Stop All, from the window's corner button."""
        self._pv_stop.set()
        self._res_stop.set()
        self._rnd_stop.set()
        self._render_timer.stop()
        self._prog.setVisible(False)
        self._btn_load.setEnabled(True)
        self._status.setText("Stopped.")

    # ══════════════════════════ the graph ═════════════════════════════════════
    def _clear_graph(self, msg: str):
        self._selectors = []
        self._axes = []
        self._axes_paint = []
        self._ax_x = None
        self._cursor_lines = []
        self._span_patches = []
        while self._graph_lay.count():
            item = self._graph_lay.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
        self._canvas = None
        self._toolbar = None
        self._fig = None
        self._graph_lay.addWidget(self._empty_label(msg))

    def _unit_of(self, name: str) -> str:
        return _sl().pv_units_for(name) or ""

    def _unit_key_of(self, name: str) -> str:
        """Which y axis a PV belongs on. The unit, except that a formula WITHOUT a
        unit gets an axis of its own: a ratio around 1.5 and a motor count around
        20 000 are both "no unit", and sharing one axis flattens the ratio onto the
        x axis. The printed label still uses the plain unit."""
        unit = self._unit_of(name)
        if unit:
            return unit
        if _sl().pv_is_derived(name):
            return f"ƒ{name}"
        return ""

    def _values_of(self, name: str) -> np.ndarray:
        sl = _sl()
        return self._series[name]["val"] * float(sl.PV_SCALE.get(name, 1.0))

    def _colour_of(self, name: str) -> str:
        """A PV's colour comes from the PICKED LIST, not from its position among the
        ones being drawn — hiding one PV must not repaint the others."""
        try:
            i = self._pv_selected.index(name)
        except ValueError:
            i = 0
        return _PLOT_COLORS[i % len(_PLOT_COLORS)]

    def _rebuild_graph(self):
        """Draw the loaded series. One graph by default (see the module docstring);
        Stacked when the combo asks for it."""
        (Figure, FigureCanvas, NavToolbar, SpanSelector, FuncFormatter,
         MultipleLocator) = _mpl()

        order = [n for n in self._pv_selected
                 if n in self._series and len(self._series[n]["ts"]) > 0
                 and n not in self._pv_hidden]

        # Tear the old canvas down completely — a stale SpanSelector holding a dead
        # axes keeps firing and would set moments on a graph nobody can see.
        keep_moment, keep_span = self._moment_ns, self._span
        self._clear_graph("No samples for the picked PVs in this window.")
        self._plot_order = order

        self._cmb_snap.blockSignals(True)
        cur = self._cmb_snap.currentText() or self._snap_wanted
        self._cmb_snap.clear()
        self._cmb_snap.addItems(order)
        if cur in order:
            self._cmb_snap.setCurrentText(cur)
        self._cmb_snap.blockSignals(False)

        if not order:
            return

        while self._graph_lay.count():          # drop the placeholder label
            item = self._graph_lay.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)

        self._axis_t0_ns = self._day_start_of(self._windows[0][0])
        self._fig = Figure(figsize=(8, 3.6), constrained_layout=True)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setMinimumHeight(170)

        if self._cmb_mode.currentIndex() == 0:
            self._draw_one_graph(order)
        else:
            self._draw_stacked(order)

        # Tick labels: clock time, and the day too once the window spans more than one.
        # They go on _ax_x, the one axes whose x ticks are actually painted — a twin
        # created by twinx has its x axis hidden, so formatting the LAST axes (right in
        # stacked mode) would silently format nothing in one-graph mode.
        multiday = len(_date_keys_for_windows(self._windows)) > 1
        t0 = self._axis_t0_ns

        def _fmt_tick(v, _p):
            if not multiday:
                return _fmt_hms(v)
            ts = int(t0 + v * NS_PER_S)
            return _ns_to_prague(ts).strftime("%d.%m.\n%H:%M")

        self._ax_x.xaxis.set_major_formatter(FuncFormatter(_fmt_tick))
        self._ax_x.set_xlabel("Prague time", fontsize=8)
        lo = (self._windows[0][0] - t0) / NS_PER_S
        hi = (self._windows[-1][1] - t0) / NS_PER_S
        if hi > lo:
            self._axes[0].set_xlim(lo, hi)
            # x = 0 is Prague midnight, so whole multiples of a clock-shaped step land
            # on the half hour, the hour, noon — never at 02:46:40.
            self._ax_x.xaxis.set_major_locator(
                MultipleLocator(_tick_step_s(hi - lo, 8 if multiday else 10)))

        self._toolbar = self._make_toolbar(NavToolbar, self._canvas)
        self._graph_lay.addWidget(self._toolbar)
        self._graph_lay.addWidget(self._canvas, 1)

        # A selector per axes so a drag works wherever the mouse happens to be — with
        # twin axes the top-most one gets the events, and which one that is is not
        # something to depend on. They all report to one handler.
        for ax in self._axes:
            self._selectors.append(self._make_span_selector(SpanSelector, ax))

        self._canvas.mpl_connect("button_press_event", self._on_press)
        self._canvas.mpl_connect("button_release_event", self._on_release)

        # A rebuild (a hidden PV, a mode change) must not lose where the user was.
        if keep_span is not None:
            self._span = keep_span
            self._paint_span((keep_span[0] - t0) / NS_PER_S,
                             (keep_span[1] - t0) / NS_PER_S)
        if keep_moment is not None:
            self._moment_ns = keep_moment
            self._move_cursor(keep_moment)
        self._canvas.draw_idle()

    def _draw_one_graph(self, order: "list[str]"):
        """Every PV in one plot, one y axis per UNIT.

        Grouping by unit is what makes a single graph honest: two energies share an
        axis and read against each other, while an energy and a motor position get an
        axis each and neither is rescaled to fit the other."""
        ax0 = self._fig.add_subplot(111)
        groups: "dict[str, list[str]]" = {}
        for n in order:
            groups.setdefault(self._unit_key_of(n), []).append(n)

        handles, labels = [], []
        sl = _sl()
        self._axes = [ax0]
        for k, (_key, names) in enumerate(groups.items()):
            unit = self._unit_of(names[0])
            ax = ax0 if k == 0 else ax0.twinx()
            if k >= 2:
                # Each further unit gets its spine pushed further out, so three or four
                # units still each have a readable scale of their own.
                ax.spines["right"].set_position(("outward", 46 * (k - 1)))
            if ax is not ax0:
                self._axes.append(ax)
            for n in names:
                d = self._series[n]
                x = (d["ts"] - self._axis_t0_ns) / NS_PER_S
                y = self._values_of(n)
                colour = self._colour_of(n)
                ln, = ax.plot(x, y, lw=1.0, color=colour,
                              marker="." if len(x) < 600 else None, ms=2.5,
                              label=sl.pv_label_for(n))
                handles.append(ln)
                labels.append(sl.pv_label_for(n) + (f" [{unit}]" if unit else ""))
            # One PV on this axis → the axis is that PV, so it wears its colour. Two or
            # more → the colour would name only one of them, and the legend names all.
            one = names[0] if len(names) == 1 else None
            ax.set_ylabel(f"{sl.pv_label_for(one)}\n[{unit}]" if one and unit
                          else (sl.pv_label_for(one) if one
                                else (f"[{unit}]" if unit else "value")),
                          fontsize=8,
                          color=self._colour_of(one) if one else "#333333")
            ax.tick_params(axis="y", labelsize=8,
                           colors=self._colour_of(one) if one else "#333333")
        ax0.tick_params(axis="x", labelsize=8)
        ax0.grid(True, alpha=0.25)
        self._ax_x = ax0
        if len(handles) > 1:
            ax0.legend(handles, labels, fontsize=7, loc="upper right",
                       framealpha=0.85, ncol=1 if len(handles) < 5 else 2)
        # Cursor and marked range are painted on the base axes only: the twins share
        # its x and draw no background of their own, so one line shows through all.
        self._axes_paint = [ax0]
        self._cursor_lines = [ax0.axvline(0, color="#111111", lw=1.0, ls="--",
                                          visible=False)]
        self._span_patches = [None]

    def _draw_stacked(self, order: "list[str]"):
        """One plot per PV, all sharing the time axis — for many unrelated PVs, where a
        single graph is a tangle whatever the axes do."""
        sl = _sl()
        axes = self._fig.subplots(len(order), 1, sharex=True, squeeze=False)[:, 0]
        self._axes = list(axes)
        self._axes_paint = list(axes)
        self._ax_x = axes[-1]
        self._cursor_lines = []
        self._span_patches = []
        for i, name in enumerate(order):
            ax = axes[i]
            d = self._series[name]
            x = (d["ts"] - self._axis_t0_ns) / NS_PER_S
            y = self._values_of(name)
            colour = self._colour_of(name)
            ax.plot(x, y, lw=1.0, color=colour,
                    marker="." if len(x) < 600 else None, ms=2.5)
            unit = self._unit_of(name)
            ax.set_ylabel(f"{sl.pv_label_for(name)}\n[{unit}]" if unit
                          else sl.pv_label_for(name), fontsize=8, color=colour)
            ax.tick_params(labelsize=8)
            ax.grid(True, alpha=0.25)
            if d["status"] == "stale":
                ax.set_facecolor("#fff8e1")
            self._cursor_lines.append(
                ax.axvline(0, color="#111111", lw=1.0, ls="--", visible=False))
            self._span_patches.append(None)

    def _make_toolbar(self, nav_cls, canvas):
        """matplotlib tints the toolbar icons at construction time, and only when the
        host palette is dark — under a dark palette the icons come out invisible. A
        light-palette host parent skips the tinting, so the black icons survive.

        The host is built ONCE in __init__ and kept: a local one is collected as soon
        as this returns, and it takes the toolbar (its C++ child) down with it."""
        tb = nav_cls(canvas, self._tb_host)
        tb.setStyleSheet(
            "QToolBar { background: #f0f0f0; border: none; spacing: 1px; }"
            "QToolButton { background: transparent; padding: 3px; }"
            "QToolButton:hover { background: #d6d6d6; border-radius: 3px; }"
            "QLabel { color: #202020; }")
        return tb

    def _make_span_selector(self, span_cls, ax):
        kw = dict(direction="horizontal", useblit=True, minspan=0)
        props = dict(alpha=0.20, facecolor="#1565C0")
        try:
            return span_cls(ax, self._on_span, props=props, **kw)
        except TypeError:
            # matplotlib < 3.5 spelled it rectprops.
            return span_cls(ax, self._on_span, rectprops=props, **kw)

    # ── graph interaction ─────────────────────────────────────────────────────
    def _toolbar_busy(self) -> bool:
        return bool(getattr(self._toolbar, "mode", "")) if self._toolbar else False

    def _on_press(self, event):
        self._press_px = (event.x, event.y) if event.x is not None else None

    def _on_release(self, event):
        """A click — not a drag — is what picks the moment. The drag belongs to the
        span selector, so the two are told apart by how far the mouse travelled."""
        if event.button != 1 or self._toolbar_busy():
            return
        start = self._press_px
        self._press_px = None
        if start is None or event.x is None or event.inaxes is None:
            return
        if abs(event.x - start[0]) > _CLICK_SLOP_PX or \
                abs(event.y - start[1]) > _CLICK_SLOP_PX:
            return                                  # a drag; _on_span handles it
        if event.xdata is None:
            return
        self._set_moment_from_x(float(event.xdata))

    def _on_span(self, x_from: float, x_to: float):
        """A marked time range → per-PV statistics. Sub-click-slop spans are the
        release of a plain click and are left to _on_release."""
        if self._toolbar_busy() or self._canvas is None or not self._axes:
            return
        try:
            ax = self._axes[0]
            x0_px, x1_px = ax.transData.transform([(x_from, 0), (x_to, 0)])[:, 0]
            if abs(x1_px - x0_px) <= _CLICK_SLOP_PX:
                return
        except Exception:
            if abs(x_to - x_from) <= 0:
                return
        lo, hi = sorted((x_from, x_to))
        self._span = (int(self._axis_t0_ns + lo * NS_PER_S),
                      int(self._axis_t0_ns + hi * NS_PER_S))
        self._paint_span(lo, hi)
        if self._canvas is not None:
            self._canvas.draw_idle()
        self._refresh_stats()

    def _clear_span(self):
        self._span = None
        for i, ax in enumerate(self._axes_paint):
            old = self._span_patches[i] if i < len(self._span_patches) else None
            if old is not None:
                try:
                    old.remove()
                except Exception:
                    pass
            if i < len(self._span_patches):
                self._span_patches[i] = None
        if self._canvas is not None:
            self._canvas.draw_idle()
        self._refresh_stats()

    def _paint_span(self, lo: float, hi: float):
        for i, ax in enumerate(self._axes_paint):
            old = self._span_patches[i]
            if old is not None:
                try:
                    old.remove()
                except Exception:
                    pass
            self._span_patches[i] = ax.axvspan(lo, hi, color="#1565C0", alpha=0.10,
                                               zorder=0)

    def _set_moment_from_x(self, x_seconds: float):
        """Snap the clicked x to a real sample of the snap PV. A moment between two
        samples has no shot behind it, and the frames would be an arbitrary pick."""
        ts_ns = int(self._axis_t0_ns + x_seconds * NS_PER_S)
        snap = self._cmb_snap.currentText() or (self._plot_order[0]
                                                if self._plot_order else "")
        j = self._nearest_index(snap, ts_ns)
        if j is not None:
            ts_ns = int(self._series[snap]["ts"][j])
        self._apply_moment(ts_ns)

    def _nearest_index(self, name: str, ts_ns: int) -> "int | None":
        """Index of the sample of `name` closest to `ts_ns`, or None when that PV has
        no samples. Both neighbours of the insertion point are compared — bisect alone
        names the sample AFTER the timestamp, which is the wrong one half the time."""
        d = self._series.get(name)
        if d is None or not len(d["ts"]):
            return None
        ts = d["ts"]
        # A formula's series carries a NaN marker per gap. Snapping onto one would
        # name a moment the formula has no value at, so only the finite points are
        # candidates (`good` is the index list of those, built with the series).
        idx = d.get("good")
        if idx is not None and len(idx) == 0:
            return None
        if idx is not None and len(idx) < len(ts):
            ts = ts[idx]
        else:
            idx = None
        i = int(np.searchsorted(ts, ts_ns))
        cands = [j for j in (i - 1, i) if 0 <= j < len(ts)]
        if not cands:
            return None
        k = min(cands, key=lambda j: abs(int(ts[j]) - ts_ns))
        return int(idx[k]) if idx is not None else k

    def _move_cursor(self, ts_ns: int):
        x = (ts_ns - self._axis_t0_ns) / NS_PER_S
        for line in self._cursor_lines:
            line.set_xdata([x, x])
            line.set_visible(True)

    def _apply_moment(self, ts_ns: int):
        self._moment_ns = int(ts_ns)
        self._move_cursor(self._moment_ns)
        if self._canvas is not None:
            self._canvas.draw_idle()
        self._lbl_moment.setText(_fmt_moment(self._moment_ns))
        self._btn_prev.setEnabled(True)
        self._btn_next.setEnabled(True)
        self._refresh_pv_table()
        self._resolve_frames()

    def _step_moment(self, direction: int):
        """Previous / next sample of the snap PV — i.e. the shot before or after."""
        if self._moment_ns is None:
            return
        snap = self._cmb_snap.currentText()
        here = self._nearest_index(snap, self._moment_ns)
        if here is None:
            return
        d = self._series[snap]
        ts = d["ts"]
        idx = d.get("good")
        if idx is not None and len(idx) and len(idx) < len(ts):
            # Step over a formula's gap markers, not onto them.
            pos = int(np.searchsorted(idx, here))
            pos = max(0, min(len(idx) - 1, pos + (1 if direction > 0 else -1)))
            j = int(idx[pos])
        else:
            j = max(0, min(len(ts) - 1, here + (1 if direction > 0 else -1)))
        if j == here:
            return                      # already at the first / last shot
        self._apply_moment(int(ts[j]))

    # ══════════════════════════ the numbers ═══════════════════════════════════
    def _value_at(self, name: str, ts_ns: int) -> "tuple[float | None, int | None]":
        """(value, offset in ns) of the sample nearest `ts_ns`, or (None, None)."""
        j = self._nearest_index(name, ts_ns)
        if j is None:
            return None, None
        d = self._series[name]
        sl = _sl()
        return (float(d["val"][j]) * float(sl.PV_SCALE.get(name, 1.0)),
                int(d["ts"][j]) - int(ts_ns))

    def _refresh_pv_table(self):
        """The picked PVs and their value AT THE MOMENT, in the left panel — the same
        eye · PV · value list the Slider and the Finder use."""
        sl = _sl()
        names = list(self._pv_selected)
        self._pv_table.setVisible(bool(names))
        self._lbl_no_pv.setVisible(not names)
        if not names:
            return

        def _value_of(name: str):
            derived = sl.pv_is_derived(name)
            if name not in self._series:
                tip = "Not read yet — press Load."
                if derived:
                    d = sl.pv_derived_def(name) or {}
                    tip = f"= {d.get('expr', '')}\n{tip}"
                return "—", True, tip
            d = self._series[name]
            if len(d["ts"]) == 0 and d.get("reason"):
                # A formula that could not be computed says WHY, where the number
                # would be — an empty cell reads as a bug in the tab.
                return "n/a", True, f"{d.get('channel', name)}\n{d['reason']}"
            if self._moment_ns is None:
                n = len(d["ts"])
                return (f"{n} pts", True,
                        "Samples in the window. Click the graph to read the value at "
                        "one moment.")
            val, off = self._value_at(name, self._moment_ns)
            if val is None:
                return "n/a", True, "No sample of this PV in the window."
            unit = self._unit_of(name)
            txt = f"{val:.4g}"
            tip = (f"{txt} {unit}".strip() + "\n"
                   f"nearest sample is {_fmt_delta(off)} from the moment picked")
            return txt, abs(off) > 5 * NS_PER_S, tip

        self._pv_table.refresh(names, self._pv_hidden, _value_of)

    def _refresh_stats(self):
        """Statistics of the marked range, per PV.

        Four columns and the rest in the tooltip, because this lives in a 300 px panel:
        a ten-column table there is a table nobody can read. Mean and spread are what
        a marked range is marked for; the extremes are one hover away."""
        sl = _sl()
        names = [n for n in self._pv_selected
                 if n in self._series and len(self._series[n]["ts"]) > 0]
        has = self._span is not None
        self._btn_clear_range.setEnabled(has)
        if not has:
            self._lbl_range.setText("Drag on the graph to mark a range."
                                    if names else
                                    "Nothing loaded yet.")
            self._stat_table.setRowCount(0)
            return

        lo, hi = self._span
        self._lbl_range.setText(
            f"{_ns_to_prague(lo).strftime('%d.%m. %H:%M:%S')} → "
            f"{_ns_to_prague(hi).strftime('%H:%M:%S')}   ·   "
            f"{_fmt_span((hi - lo) / NS_PER_S)}")

        self._stat_table.setRowCount(len(names))
        for r, name in enumerate(names):
            d = self._series[name]
            unit = self._unit_of(name)
            m = (d["ts"] >= lo) & (d["ts"] <= hi)
            y = self._values_of(name)[m]
            # A formula's series carries one NaN per gap (that NaN is what breaks the
            # line); an archiver NaN would poison mean/std the same way.
            y = y[np.isfinite(y)]
            label = QTableWidgetItem(sl.pv_label_for(name))
            label.setForeground(QColor(self._colour_of(name)))
            label.setToolTip(d.get("channel", name))
            self._stat_table.setItem(r, 0, label)
            if y.size == 0:
                for c, txt in ((1, "—"), (2, "—"), (3, "0")):
                    it = QTableWidgetItem(txt)
                    it.setForeground(QColor("#888888"))
                    it.setToolTip("No sample of this PV inside the marked range.")
                    self._stat_table.setItem(r, c, it)
                self._stat_table.setRowHeight(r, 20)
                continue
            std = float(y.std(ddof=1)) if y.size > 1 else float("nan")
            tip = (f"{sl.pv_label_for(name)}  [{unit}]\n" if unit
                   else f"{sl.pv_label_for(name)}\n")
            tip += (f"n = {y.size}\n"
                    f"mean = {y.mean():.6g}\n"
                    f"std = {std:.6g}\n" if y.size > 1 else
                    f"n = {y.size}\nmean = {y.mean():.6g}\n")
            tip += (f"min = {y.min():.6g}\nmax = {y.max():.6g}\n"
                    f"peak-to-peak = {y.max() - y.min():.6g}")
            for c, txt in ((1, f"{y.mean():.4g}"),
                           (2, "—" if y.size < 2 else f"{std:.3g}"),
                           (3, str(y.size))):
                it = QTableWidgetItem(txt)
                it.setToolTip(tip)
                self._stat_table.setItem(r, c, it)
            self._stat_table.setRowHeight(r, 20)
        # Tall enough for what it holds, and no taller — the section below it must not
        # be pushed off the panel by an empty table.
        self._stat_table.setMaximumHeight(20 * max(len(names), 1) + 26)

    # ══════════════════════════ the frames ════════════════════════════════════
    def _display_opts(self) -> dict:
        return {
            "max_side": self._sld_size.value(),
            "gradient_id": self._cmb_palette.currentIndex(),
            "contrast": self._sld_contrast.value(),
            "brightness": self._sld_bright.value(),
            "gamma": (img_scale.GAMMA_SLIDER_AUTO
                      if self._cb_gamma_auto.isChecked()
                      else self._sld_gamma.value()),
            "auto_contrast": 1 if self._cb_contrast_auto.isChecked() else 0,
            "auto_bright": 1 if self._cb_bright_auto.isChecked() else 0,
        }

    def _on_palette_changed(self, *_):
        self._sync_display_enabled()
        self._on_display_changed()

    def _sync_display_enabled(self):
        """"Default" is the untouched file and a cyclic palette is pinned to absolute
        value bands: on both, Contrast and Brightness do nothing, so they are greyed
        out rather than left looking live. Same rule as the Slider's."""
        sl = _sl()
        gid = self._cmb_palette.currentIndex()
        raw = (gid == sl.GRADIENT_ID_DEFAULT) or sl.palette_is_cyclic(gid)
        self._cb_contrast_auto.setEnabled(not raw)
        self._cb_bright_auto.setEnabled(not raw)
        self._sld_contrast.setEnabled(
            not raw and not self._cb_contrast_auto.isChecked())
        self._sld_bright.setEnabled(
            not raw and not self._cb_bright_auto.isChecked())

    def _on_display_changed(self, *_):
        """Any display control → re-render what is already found, after the settle."""
        self._sync_display_enabled()
        self._lbl_size.setText(str(self._sld_size.value()))
        self._sld_contrast._value_label.setText(str(self._sld_contrast.value()))
        self._sld_bright._value_label.setText(str(self._sld_bright.value()))
        # An Auto tick outranks its slider everywhere in this program, so the slider it
        # outranks is greyed out rather than left looking live.
        auto_g = self._cb_gamma_auto.isChecked()
        self._sld_gamma.setEnabled(not auto_g)
        self._sld_gamma._value_label.setText(
            "auto" if auto_g
            else f"{img_scale.gamma_from_slider(self._sld_gamma.value()):.2f}")
        if self._tile_items:
            self._render_timer.start()
        self._save_state()

    def _resolve_frames(self):
        """Find each picked camera's frame for the moment, then render them."""
        if self._moment_ns is None:
            return
        if not self._cams:
            self._tile_items = []
            self._tile_results = []
            self._clear_tiles()
            self._btn_popout.setEnabled(False)
            self._tiles_hint.setText(
                "No camera picked — press Camera to choose which frames this moment "
                "should show.")
            return
        self._res_stop.set()
        self._res_stop = threading.Event()
        self._res_gen += 1
        self._tile_items = []
        self._tile_results = []
        self._clear_tiles()
        self._btn_popout.setEnabled(False)
        self._tiles_hint.setText(f"Looking for {len(self._cams)} camera(s) at "
                                 f"{_ns_to_prague(self._moment_ns):%H:%M:%S}…")
        self._pool.start(_ResolveTask(self._res_gen, self._moment_ns, self._cams,
                                      self._res_sig, self._res_stop))

    def _on_resolved(self, item: dict, gen: int):
        if gen != self._res_gen:
            return
        self._tile_items.append(item)

    def _on_resolve_done(self, gen: int):
        if gen != self._res_gen:
            return
        # Camera order as picked, not as the share happened to answer.
        rank = {c: i for i, c in enumerate(self._cams)}
        self._tile_items.sort(key=lambda it: rank.get(it.get("cam"), 9999))
        found = sum(1 for it in self._tile_items if it.get("path") is not None)
        if not found:
            self._tiles_hint.setText("No camera had a frame near this moment.")
        self._render_frames()

    def _render_frames(self):
        """Turn the found files into pictures with the current display settings. No
        share walk here — that is _resolve_frames, and it already happened."""
        self._render_timer.stop()
        if not self._tile_items:
            return
        self._rnd_stop.set()
        self._rnd_stop = threading.Event()
        self._rnd_gen += 1
        self._tile_results = []
        self._pool.start(_RenderTask(self._rnd_gen, self._tile_items,
                                     self._display_opts(), self._rnd_sig,
                                     self._rnd_stop))

    def _clear_tiles(self):
        while self._tiles_grid.count():
            item = self._tiles_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._tiles.clear()
        self._grid_cols = 0

    def _on_tile(self, res: dict, gen: int):
        """One camera's picture arrived. ONE tile is built — the wall is not rebuilt.

        Rebuilding every tile per arrival is what made twenty cameras cost 210 tile
        widgets and a visible flicker down the wall."""
        if gen != self._rnd_gen:
            return
        self._tile_results.append(res)
        cam = res.get("cam", "")
        old = self._tiles.pop(cam, None)
        if old is not None:                 # a re-render replaces that camera's tile
            old.setParent(None)
            old.deleteLater()
        t = _Tile(res, self._tiles_host)
        t.clicked.connect(self._send_to_workshop)
        self._tiles[cam] = t
        self._place_tiles()

    def _place_tiles(self):
        """Re-flow the tiles already built, in the order the cameras were picked, in
        the widest grid the panel can hold — so the wall grows with the window instead
        of leaving a gutter down the side."""
        if not self._tiles:
            return
        cams = [c for c in self._cams if c in self._tiles]
        cams += [c for c in self._tiles if c not in cams]
        tiles = [self._tiles[c] for c in cams]
        self._grid_cols = _place_in_grid(
            self._tiles_grid, tiles,
            _cols_for(self._tiles_area.viewport().width(),
                      self._sld_size.value(), len(tiles)),
            self._grid_cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-flow rather than re-read: the pictures are already in memory, and now so
        # are the tiles that hold them.
        if self._tiles:
            QTimer.singleShot(0, self._place_tiles)

    def _on_tiles_done(self, gen: int):
        if gen != self._rnd_gen:
            return
        found = sum(1 for r in self._tile_results if r.get("img") is not None)
        missing = len(self._tile_results) - found
        txt = f"{found} frame(s)"
        if missing:
            txt += f"   ·   {missing} camera(s) had nothing near this moment"
        txt += "   ·   click a frame to send it to the Workshop"
        self._tiles_hint.setText(txt)
        self._btn_popout.setEnabled(bool(self._tile_results))
        if self._moment_ns is not None:
            self._lbl_frames.setText(
                f"The frames at {_ns_to_prague(self._moment_ns):%H:%M:%S}")
        self._refresh_popout()

    def _popout_title(self) -> str:
        return (f"One Moment — {_fmt_moment(self._moment_ns)}"
                if self._moment_ns else "One Moment")

    def _popout(self):
        """Open the pop-out, or bring the open one to the front — a second window over
        the first is two answers to the same question."""
        if not self._tile_results:
            return
        if self._popout_dlg is not None:
            self._refresh_popout()
            self._popout_dlg.raise_()
            self._popout_dlg.activateWindow()
            return
        self._popout_dlg = _MomentPopout(self._popout_title(), self._tile_results,
                                     self._send_to_workshop,
                                     self._sld_size.value(), self)
        self._popout_dlg.finished.connect(
            lambda *_: setattr(self, "_popout_dlg", None))
        self._popout_dlg.show()     # non-modal: the graph stays usable behind it

    def _refresh_popout(self):
        """Keep an open pop-out on the CURRENT frames."""
        if self._popout_dlg is None or not self._tile_results:
            return
        try:
            self._popout_dlg.set_results(self._popout_title(), self._tile_results,
                                     self._sld_size.value())
        except RuntimeError:            # the window was closed under us
            self._popout_dlg = None

    def _close_popout(self):
        dlg, self._popout_dlg = self._popout_dlg, None
        if dlg is None:
            return
        try:
            dlg.close()
            dlg.deleteLater()
        except RuntimeError:
            pass

    # ══════════════════════════ hand-over ═════════════════════════════════════
    def _send_to_workshop(self, res: dict):
        """Hand a frame to the Workshop for measuring.

        The Workshop measures the picture it is given, so it gets the plain absolute
        mapping — never this tab's display settings, which are a display curve and
        would be measured as if they were data."""
        wk = getattr(self, "_workshop_ref", None)
        path = res.get("path")
        if wk is None or path is None:
            return
        try:
            from PIL import Image as _PilImage
            data = Path(path).read_bytes()
            with _PilImage.open(BytesIO(data)) as im:
                im.load()
                mode, info = im.mode, dict(im.info)
                if mode in ("I", "I;16"):
                    arr = np.asarray(im, dtype=np.float32)
                else:
                    arr = np.asarray(im.convert("L"), dtype=np.float32)
            arr8 = img_scale.render_u8(arr, False,
                                       img_scale.full_scale_for_pil(path, info, mode),
                                       None)
            cam = re.sub(r"[-_]+IMG$", "", Path(path).parent.name,
                         flags=re.IGNORECASE).rstrip("-_")
            wk.receive_image(arr8, f"{cam}  |  {Path(path).name}", source_path=path)
            tabs = getattr(self, "_tab_widget", None)
            idx = getattr(self, "_workshop_tab_idx", None)
            if tabs is not None and idx is not None:
                tabs.setCurrentIndex(idx)
        except Exception as exc:
            QMessageBox.warning(self, "Workshop", f"Could not send image:\n{exc}")


# ── standalone ────────────────────────────────────────────────────────────────
def main():
    from PySide6.QtWidgets import QApplication, QMainWindow
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    win = QMainWindow()
    win.setWindowTitle("One Moment")
    win.setCentralWidget(OneMomentWidget())
    win.resize(1450, 950)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
