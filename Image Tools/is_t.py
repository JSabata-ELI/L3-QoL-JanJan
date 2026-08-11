# is_t.py — Image Finder (PySide6 port)

import math
import os
import re
import bisect
import shutil
import time
import argparse
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from collections import OrderedDict, namedtuple
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from functools import lru_cache
try:
    import matplotlib
    if hasattr(matplotlib, 'use'):
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.gridspec import GridSpec
    _MPL_OK = True
except Exception:
    plt = None
    FigureCanvas = None
    GridSpec = None
    _MPL_OK = False

from PySide6.QtCore import (
    Qt, QTimer, QRunnable, QThreadPool, QObject, Signal, QSize, QRect, QPoint, QPointF, QDate,
    QModelIndex, QTime, QLocale, QBuffer, QByteArray
)
from PySide6.QtGui import (
    QPixmap, QImageReader, QPainter, QFontMetrics, QFont, QImage, QColor, QPen, QBrush, QGuiApplication,
    QTextCharFormat, QPalette
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QDoubleSpinBox, QScrollArea,
    QLabel, QSlider, QPushButton, QFileDialog, QMessageBox, QProgressBar,
    QComboBox, QCheckBox, QDialog, QCalendarWidget, QDialogButtonBox, QFileSystemModel,
    QSpinBox, QFrame, QSizePolicy, QStyledItemDelegate, QAbstractItemView, QTreeView, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFormLayout, QColorDialog, QToolButton,
    QTimeEdit, QMenu, QStyle,
)

# ---------------- CONFIG ----------------
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
TZ_PRAGUE = ZoneInfo("Europe/Prague")
_NS_19_RE = re.compile(r'\d{19}')
SLIDER_MAX = 1_000_000
SCRUB_INTERVAL_MS = 33
NAV_TICK_MS = 33            # per-camera navigation coalescing tick (see _nav_timer). Same
                            # 33 ms as SCRUB_INTERVAL_MS, and PreciseTimer for the same
                            # reason — a coarse timer turns it into 21 Hz on Windows.
SCRUB_MAX_SIDE = 900
FAST_SCRUB_MAX_SIDE = 320
PLAY_MAX_SIDE_SLOW = 320
PLAY_MAX_SIDE_FAST = 240
FULL_RES_SIDE = 0   # max_side=0 → load_image_scaled skips downscaling (native resolution)
# Quality the frame the user SETTLED on is brought up to, outside live mode. Well above
# the scrub sizes (so stopping visibly sharpens the picture) but below native: the image
# view is never taller than ~1100 px unzoomed, so native only bought a ~22 MB
# QPixmap.fromImage on the GUI thread after every release. Live mode is untouched — it
# still decodes every arriving frame at FULL_RES_SIDE (see _current_decode_side).
REFINE_MAX_SIDE = 1600
HEAVY_RENDER_SIDE = 1200  # at or above this a render counts as "heavy": memory-capped in
                          # PixCache and treated as a settle-only render everywhere else
CACHE_SIZE = 320
# One native-resolution pixmap of a 2560×2160 camera is ~22 MB, so a plain LRU of
# CACHE_SIZE full-res renders would be worth gigabytes. Native renders are capped
# separately (see PixCache) — only the last few settled frames are worth keeping.
NATIVE_CACHE_KEEP = 4

# ---- Whole-window preview ("proxy") layer -------------------------------------
# With live mode OFF the tool preloads the ENTIRE loaded time window at this small
# size, so dragging the slider repaints from memory instead of waiting on one SMB
# read + decode per position. Stopping on a frame then re-renders it at native
# resolution (see _schedule_refine / _refine_current_frame).
PROXY_MAX_SIDE   = 224      # decoded side of a preview frame, up to 4 cameras
PROXY_SIDE_MANY  = 160      # 5-8 cameras
PROXY_SIDE_MOST  = 128      # 9+ cameras. See _proxy_side: at 12 cameras the REAL render
                            # during a fast drag is only 180 px anyway, so this is not a
                            # visible downgrade — and it is what lets the whole window fit.
PROXY_KNEE_CODE  = 239      # last 8-bit code of a stored preview frame's MAIN band. Codes
                            # above it carry the p99.9..max highlight tail — see
                            # load_proxy_gray for why the tail needs its own segment.
PROXY_STORE_8BIT = True     # store preview frames as 8-bit codes plus the (lo, hi) the
                            # p0.5/p99.5 stretch used, instead of raw uint16. Halves the RAM
                            # (so twice as many frames fit — often the whole window) and
                            # moves the percentile pass off the GUI thread. Auto contrast is
                            # unaffected: the percentiles are still taken on the 16-bit data,
                            # in the worker. See load_proxy_gray for what is and is not lost.
                            # Set False to go back to raw uint16 storage.
PROXY_RAM_BUDGET_MB = 2000  # RAM the whole-window preview may occupy, across all cameras.
                            # The frame COUNT is derived from this and the measured size of
                            # a decoded preview frame (_proxy_frame_bytes), because that is
                            # what actually varies: a 445x420 camera downscales to ~47 kB at
                            # PROXY_MAX_SIDE, a 2560x2160 one to the same, but a camera
                            # smaller than PROXY_MAX_SIDE is not downscaled at all. A fixed
                            # count was therefore either wasteful or far too sparse
                            # depending on the camera. Every camera now costs 1 byte/px here
                            # (PROXY_STORE_8BIT above), so at 224 px a 4-camera window of
                            # ~27k frames needs ~1.2 GB and fits at EVERY frame — which was
                            # not true at 2 bytes/px and 1200 MB: it sampled 1-in-3, so a
                            # drag could not show what it was dragged across no matter how
                            # fast the rest of the pipeline became.
PROXY_SWEEP_BUDGET_S = 300  # how long the sweep may take to build the whole preview, and in
                            # practice the ceiling that actually binds. A preview frame costs
                            # a WHOLE file read (setScaledSize saves decode CPU, not I/O) and
                            # the sweep runs at ~100 frames/s, so 5 minutes buys ~30k frames
                            # — enough for a 4-camera 2-hour window at every frame. Beyond
                            # that, planning frames nobody will reach for 25 minutes is not
                            # coverage; sampling the window evenly is the honest answer.
PROXY_SWEEP_FPS_EST = 100   # measured sweep rate on \\users-L3 with PROXY_WORKERS threads
                            # (the diag log shows a 26 182-frame window swept in ~4 min).
PROXY_STEP_SNAP_SLACK = 0.12  # allow the plan to overshoot the budget by this much if it
                            # buys a smaller step. `step` is ceil(want/per), so a window a
                            # few frames over budget jumped from step 2 to step 3 and left a
                            # third of the RAM unused — measured on the logged session: 846
                            # of 1200 MB, at 1-in-3 sampling, when 1-in-2 was affordable.
PROXY_MAX_FRAMES = 200000   # hard ceiling on planned preview frames regardless of the RAM
                            # budget, so a pathologically small camera cannot plan a
                            # hundred thousand reads. Per window, shared by all cameras.
                            # On the usual 16-bit cameras the RAM budget above binds first
                            # (~94 kB per frame → ~13k frames); this ceiling only matters
                            # for cameras small enough that frames are nearly free. Longer
                            # windows are sampled evenly; the exact frame is always
                            # re-rendered once the user stops on it.
                            #
                            # A preview frame costs a WHOLE file read off the share:
                            # QImageReader.setScaledSize only saves decode CPU, not
                            # I/O (a PNG must be read end-to-end). So this number IS
                            # the sweep's price — 2400 frames × ~10 MB ≈ 24 GB was
                            # what made "Preloading preview…" take forever and starve
                            # the frame under the slider. Shared across cameras, but see
                            # the per-track floor in _proxy_start.
PROXY_BATCH      = 1        # frames decoded per background task. ONE on purpose: a task
                            # handed to the pool always runs to completion, so the batch
                            # size is exactly how long the sweep keeps competing for share
                            # bandwidth after the user starts dragging. At 1, _proxy_pump
                            # re-checks that per frame (it is called from every
                            # _on_proxy_batch), which is the finest yield available
                            # without teaching _ProxyTask to abandon work mid-batch.
PROXY_QUEUE_SPARE = 2       # queued batches beyond one per worker. Kept tiny for the same
                            # reason: every queued batch is a read that will still happen
                            # after the user has already grabbed the slider.
PROXY_WORKERS    = 16       # decode threads. The sweep is background work on the SAME
                            # share as the frame the user is waiting on, so what keeps it
                            # out of the way is _proxy_pump standing down while the user
                            # is active — not a small worker count. With PROXY_BATCH=1 and
                            # the backlog below, at most WORKERS + PROXY_QUEUE_SPARE reads
                            # are still running when a drag starts (it was 32).
                            # An SMB read is latency-bound, not bandwidth-bound: 4 threads
                            # left the share mostly idle and made a full window take tens
                            # of minutes, which is why the preview was never ready when it
                            # was needed. The other pools here already run 8–16.
PROXY_DRAG_WORKERS = 10     # reads the sweep may keep running WHILE the user drags. Not
                            # zero any more: with the cursor-first ordering below, those
                            # reads are exactly the frames the drag is about to need, so
                            # standing down completely just means the drag is served one
                            # blocking share read per camera at a time — the "one image a
                            # second" case.
                            #
                            # Was 2, and that was measured to be self-defeating. From the
                            # diag log of a real 4-camera session: a drag produced 923
                            # preview paints and 55 real loads in a minute — i.e. it asked
                            # the share for 0.9 loads/s, on a share that does 204/s. So the
                            # sweep was being throttled 15x (110 -> 14 frames/s) to protect
                            # half a percent of capacity, and the thing being throttled is
                            # the only thing that frees that capacity up: the preview never
                            # got built, so the drag fell back to loads, so the throttle
                            # looked justified. 10 reads is ~69 frames/s and still leaves
                            # room for the fallback loads, which need far less than that.
                            # PROXY_BATCH = 1 keeps the yield granularity per frame.
PROXY_FOCUS_GRID = 4096     # how far (in plan grid points, each side) the cursor-first
                            # search looks for an undecoded frame before giving up and
                            # falling back to the global coarse→fine order.
PROXY_FOCUS_BATCH = 3       # frames the cursor-first pass may hand out per task WHILE the
                            # user is moving. The plan walk stays at PROXY_BATCH so its
                            # tasks remain interruptible; the neighbourhood of the handle is
                            # the only place a read can still help within the next few
                            # hundred ms, so it is worth committing a little more to it.
PROXY_FOCUS_SCAN_MAX = 256  # grid points one _proxy_focus_jobs call may examine. That scan
                            # runs on the GUI thread inside the scrub tick, and an
                            # exhausted neighbourhood used to cost the full FOCUS_GRID
                            # (8192 membership tests per camera, per pump, every drag
                            # tick). The plan walk in _proxy_next_batch supplies whatever
                            # a bounded call does not find, so the bound costs nothing but
                            # the ordering of a few frames.
PROXY_HOLD_MS    = 120      # retry delay when the sweep is held off (drag / playback /
                            # a display load in flight). Was 400: one in-flight display
                            # load stalling the sweep for 400 ms is 40 preview frames not
                            # read, and the load it is waiting for takes ~145 ms.
PROXY_DRAG_MS    = 60       # retry delay while the user is dragging. The drag needs its
                            # neighbourhood filled NOW, so the pump re-checks at roughly
                            # the scrub tick instead of PROXY_HOLD_MS.
PROXY_IDLE_GRACE_S = 0.25   # quiet time required after a drag / playback before the sweep
                            # goes back to full speed. Long enough for the frame the user
                            # landed on to win the first read, and no longer: it used to be
                            # 1.5 s AND to be re-armed from inside _proxy_pump's dragging
                            # branch every 60 ms, so a user who drags in bursts — which is
                            # how anyone uses a slider — never let the full sweep run at
                            # all. It is now set at the interaction EDGES (release / stop),
                            # which is what it was always meant to measure.
PROXY_COVERED_FRAC = 0.98   # decoded share of the planned frames at which the preview is
                            # trusted to carry a drag (see _proxy_covered_track)
PROXY_COVERED_TRACK_FRAC = 0.9  # share of CAMERAS that must be covered before the viewer as
                            # a whole counts as covered. Requiring all of them let one slow
                            # or partly-unreadable camera hold every other tile in the
                            # not-covered state — big scrub renders and a tight load cap —
                            # indefinitely.
PROXY_MOTION_TOL_MAX = 8    # ceiling, in multiples of the finished plan's spacing, on how
                            # far a preview frame may be from the requested moment while
                            # the user is moving (_proxy_motion_tol). Without it the first
                            # few decoded frames — spacing = the whole window — would be
                            # accepted for every position and a drag would paint a frame
                            # hours away from the timestamp on the label.
                            # Was 64, which was not a ceiling in any useful sense: on a 4 h
                            # window with step 3 (ts_gap 6.4 s) it permitted 6.9 MINUTES.
PROXY_MOTION_TOL_MAX_S = 2.0  # and the same ceiling in WALL-CLOCK seconds, which is the one
                            # that actually binds. The multiple above scales with the plan,
                            # so on a coarse plan it grew without limit; measured on the
                            # logged 4-camera session, a drag at prox=4 % painted frames
                            # 5.4 MINUTES from the requested moment and reported them
                            # against a 6.4 s tolerance — every tile red, and truthfully so.
                            # 2 s is ~6 frames at the usual 3.3 Hz: close enough to carry
                            # motion, far too close to show a different shot. Once the
                            # window is fully preloaded this never binds at all.
PROXY_HOLD_MAX   = 25       # consecutive holds tolerated for an in-flight display load
                            # (~10 s) before the sweep proceeds anyway, so a stranded
                            # _inflight key can never park the preview forever
PROXY_REFINE_MS  = 200      # settle time before the full-quality re-render
PROXY_TOPUP_MS   = 2000     # debounce for extending the preview after a refresh
PREFETCH_RADIUS_IDLE = 6
PREFETCH_AHEAD_PLAY = 3
TICK_STEP_MINUTES = 10
TICKBAR_DISCRETE_MAX = 200  # above this frame count the axis stays a plain time axis:
                            # per-frame ticks are redrawn on every cursor move
PLAY_TICK_MS = 33
PLAY_TICK_MS_PREVIEW = 16   # playback tick while the RAM preview is carrying the frames.
                            # The % setting is a WALL-CLOCK rate, so the number of frames a
                            # tick must cover is (rate / tick_rate) — doubling the tick rate
                            # halves the stride and therefore halves how many frames get
                            # skipped, at exactly the same playback speed. 1 %/s over 6700
                            # frames wants 67 frames/s: at 30 Hz that is a stride of 2.2
                            # (more than half the frames never shown), at 60 Hz it is 1.1.
                            # Only used when the frames come from RAM — at 33 ms a share
                            # read cannot keep up either way.
AXIS_TOLERANCE_S = 5 * 60
PLAY_EXACT_PCT_PER_S_THRESHOLD = 0.5
SAVE_RANGE_WARN_COUNT = 500
SLAVE_SYNC_MAX_NS = 3_000_000_000   # how far a slave camera's nearest frame may be from
                            # the master's moment before the slave is left alone rather
                            # than shown something unrelated (_per_cam_slave_targets).
                            # Was 0.4 s, hard-coded in the function body: cameras that run
                            # slower than ~2.5 Hz, or that have a gap, then had NO frame in
                            # the window and their tile simply never redrew — a permanently
                            # frozen picture next to three moving ones, with nothing saying
                            # why. 3 s still refuses a genuinely unrelated moment, and the
                            # tile's own "~" label now says when it is a neighbour.
ONLINE_MAX_ITEMS = 3_600    # max frames kept PER CAMERA *while live mode is ON*
                            # (~18 min at 3.3 Hz). Deliberately small: live mode is
                            # for watching the latest frames, and keeping tens of
                            # thousands of in-memory frames per camera was what made
                            # the whole app bog down after ~2 h.
                            #
                            # This cap applies ONLY with live mode ON. Trimming is
                            # memory-only (never disk), and turning live mode OFF
                            # re-scans the opened folders and puts the full history
                            # back (_restore_full_history), so browsing back hours is
                            # never blocked by the live cap.

# ---------------- PV / CPVA ----------------
import ssl
import json
import urllib.request
import urllib.parse
import threading

CPVA_BASE_URL     = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HTTP_TIMEOUT = 8.0

# All available PV channels the user can pick from
PV_CHANNEL_MAP: dict[str, str] = {
    "PTM1":      "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "PCM2":      "L3-PM03-025:Energy",
    "PCM4":      "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "PAP1":      "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "SBW4":      "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    # Derived: same archiver channel as SBW4, scaled by PV_SCALE below.
    "Compressed SBW4": "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "Back_Ref":  "L3-PM03-023:Energy",
    "Waveplate": "L3-PFWP6-MTR03-1:RawPos",
}

PV_UNITS: dict[str, str] = {
    "PTM1": "J", "PCM2": "J", "PCM4": "J", "PAP1": "J", "SBW4": "J",
    "Compressed SBW4": "J", "Back_Ref": "J", "Waveplate": "",
}

# Multiplicative factor applied to the raw archiver value before display/burn-in.
# Names not listed here use 1.0 (the raw value).
PV_SCALE: dict[str, float] = {
    "Compressed SBW4": 0.749,
}


def _import_cpva_client():
    """Load the shared CPVA client (sibling cpva_client.py). Reuses an
    already-loaded instance so every tool (and re-exec'd module copy) shares
    one connection pool and one day cache."""
    import sys as _sys
    import importlib.util as _ilu
    mod = _sys.modules.get("cpva_client")
    if mod is not None:
        return mod
    p = Path(__file__).resolve().parent / "cpva_client.py"
    spec = _ilu.spec_from_file_location("cpva_client", p)
    mod = _ilu.module_from_spec(spec)
    _sys.modules["cpva_client"] = mod   # register BEFORE exec (re-entrancy safe)
    spec.loader.exec_module(mod)
    return mod


cpva = _import_cpva_client()

# Today's cache expires after this many seconds (live mode gets fresh data
# periodically). The refresh is an incremental tail query in cpva.get_day, not a
# whole-day download, so this can stay short without flooding the archiver.
_PV_TODAY_CACHE_TTL = 1.5


def _pv_date_key(ts_ns: int) -> str:
    """Return 'YYYY-MM-DD' in Prague time for ts_ns."""
    from datetime import timezone as _tz
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=_tz.utc).astimezone(TZ_PRAGUE)
    return dt.strftime("%Y-%m-%d")


def _pv_prev_date_key(date_key: str) -> str:
    """Return 'YYYY-MM-DD' for the day before date_key (Prague time)."""
    y, m, d = int(date_key[:4]), int(date_key[5:7]), int(date_key[8:10])
    prev = datetime(y, m, d, tzinfo=TZ_PRAGUE) - timedelta(days=1)
    return prev.strftime("%Y-%m-%d")


# ±window (ns) used when searching for a "nearby" sample around the image
# timestamp. Applies to the energy detectors only — they fire together with the
# image, so nothing further away belongs to this shot. Step channels (the
# waveplate, see cpva.STEP_CHANNELS) are archived only when they CHANGE and are
# resolved by "last sample at or before the image" instead of by a window.
#
# Measured against 1000 real frames (2026-08-04, 3.3 Hz, 0.301 s between shots):
# the sample belonging to a frame sits at p50 0.025 s / p90 0.30 s from it, on
# EITHER side. The old ±30 s window therefore always found "something" — a
# neighbouring shot's value for 43 % of frames, off by up to 91 J — and never
# admitted that a frame has no archived shot. 0.3 s keeps every real pairing and
# reports n/a for the rest; beyond PV_EXACT_MATCH_NS the value is marked "~".
_PV_WINDOW_NS: int = 300_000_000          # 0.3 s
_PV_PREFER: str = "nearest"               # NOT "before" — see cpva.lookup_near


def _pv_last_known_ex(channel: str, ts_ns: int) -> "tuple[float | None, str]":
    """Return (value for ts_ns, fetch status) using the shared lookup.

    Matching rules live in cpva.lookup_near (one implementation for every tab):
    energy channels take the sample CLOSEST in time within ±_PV_WINDOW_NS (None
    when nothing is that close — never a value from another shot), step channels
    such as the waveplate take the last sample at or before ts_ns, bisected per
    frame so a held position keeps reading its real value.

    Status: "ok" → reliable, "approx" → matched, but far enough from the frame
    that it may belong to the neighbouring shot, "stale" → served from an older
    successful fetch, "error" → fetch failed and nothing cached (retry hint).
    """
    res = cpva.lookup_near(channel, int(ts_ns), window_ns=_PV_WINDOW_NS,
                           prefer=_PV_PREFER,
                           today_ttl=_PV_TODAY_CACHE_TTL,
                           timeout=CPVA_HTTP_TIMEOUT)
    if res.status in ("stale", "error"):
        return res.value, res.status
    if (res.value is not None and res.ts_ns is not None
            and channel not in cpva.STEP_CHANNELS
            and abs(res.ts_ns - int(ts_ns)) > cpva.PV_EXACT_MATCH_NS):
        return res.value, "approx"
    return res.value, "ok"


def _pv_decorate(txt: str, status: str) -> str:
    """Apply the shared display convention to an already-formatted number."""
    if status == "approx":
        return cpva.PV_TEXT_APPROX_PREFIX + txt
    if status == "stale":
        return txt + cpva.PV_TEXT_STALE_SUFFIX
    return txt


def _pv_last_known(channel: str, ts_ns: int) -> "float | None":
    """Compat shim — value only (see _pv_last_known_ex for fetch status)."""
    return _pv_last_known_ex(channel, ts_ns)[0]


def _format_pv_value(channel: str, val: float) -> str:
    """Same numeric formatting as the live PV overlay (waveplate = integer)."""
    return f"{val:.0f}" if "RawPos" in channel else f"{val:.3f}"


def pv_text_for_ts(ts_ns: "int | None", enabled_names: "list[str]") -> str:
    """Build the PV burn-in string from the archiver values at a SPECIFIC image
    timestamp — so every saved frame gets the values that were actually present at
    its own moment, not a single live snapshot. Same format/units as the overlay."""
    if ts_ns is None or not enabled_names:
        return ""
    parts: list[str] = []
    for name in enabled_names:
        channel = PV_CHANNEL_MAP.get(name)
        if not channel:
            continue
        try:
            val, status = _pv_last_known_ex(channel, ts_ns)
        except Exception:
            val, status = None, "error"
        if val is None:
            # Burn a clear token instead of silently omitting the PV: the saved
            # frame must show whether the value was missing or the fetch failed.
            token = cpva.PV_TEXT_ERROR if status == "error" else cpva.PV_TEXT_NOT_FOUND
            parts.append(f"{name}: {token}")
            continue
        val *= PV_SCALE.get(name, 1.0)
        units = PV_UNITS.get(name, "")
        txt = _pv_decorate(_format_pv_value(channel, val), status)
        parts.append(f"{name}: {txt} {units}".strip())
    return "  |  ".join(parts)


def pv_warm_days(channels: "list[str]", ts_values: "list[int]", lookback_days: int = 2) -> None:
    """Pre-load (channel, day) archiver caches for every day spanned by ts_values,
    plus a few days before the earliest (for slowly-changing PVs), all in PARALLEL.
    After this the per-image pv_text_for_ts() calls are pure in-memory bisects, so a
    save never stalls hitting one HTTP request after another. Safe to call twice —
    already-cached days return instantly."""
    if not channels or not ts_values:
        return
    date_keys = {_pv_date_key(t) for t in ts_values}
    earliest = min(date_keys)
    k = earliest
    for _ in range(max(0, lookback_days)):
        k = _pv_prev_date_key(k)
        date_keys.add(k)
    cpva.warm_days(channels, date_keys, today_ttl=_PV_TODAY_CACHE_TTL,
                   timeout=CPVA_HTTP_TIMEOUT)


def _pv_bar_font(size: int):
    """Load a real TrueType font at the given size, falling back across the common
    Windows fonts so PIL never silently drops to its tiny bitmap default."""
    from PIL import ImageFont as _PF
    for fname in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                  "C:/Windows/Fonts/calibri.ttf", "DejaVuSans.ttf"):
        try:
            return _PF.truetype(fname, size)
        except Exception:
            continue
    return _PF.load_default()


def render_pv_bar_below(pil_img, pv_text: str):
    """Return a new RGB PIL image: pil_img with a white annotation bar appended
    below it showing pv_text. The font is scaled up to fill the available width
    (clamped to a readable range) and wrapped onto multiple centred lines when one
    line will not fit — the same approach the Shot Finder uses, so 1 PV is big and
    readable and many PVs wrap instead of staying tiny."""
    from PIL import Image as _PI, ImageDraw as _PD
    pil_img = pil_img.convert("RGB")
    width = max(1, pil_img.width)
    parts = [p for p in pv_text.split("  |  ") if p] or [pv_text]
    measure = _PD.Draw(_PI.new("RGB", (1, 1)))

    # Start large (proportional to image width) so a short bar fills the space.
    start_fsize = max(22, min(64, width // 42))
    avail = width - 20
    # Keep everything on ONE line as long as it stays readable; only wrap when a
    # single line would have to shrink below this floor (i.e. real overflow).
    one_line_floor = max(18, start_fsize // 2)
    chosen_font = None
    lines = [pv_text]

    # Pass 1 — single line preferred: largest font (down to the floor) that fits.
    for fsize in range(start_fsize, one_line_floor - 1, -1):
        f = _pv_bar_font(fsize)
        try:
            if measure.textbbox((0, 0), pv_text, font=f)[2] <= avail:
                chosen_font, lines = f, [pv_text]
                break
        except Exception:
            pass

    # Pass 2 — overflow: wrap into the fewest lines, at the largest font that fits,
    # then the layout is re-scaled to that size.
    if chosen_font is None:
        for fsize in range(start_fsize, 7, -1):
            f = _pv_bar_font(fsize)
            done = False
            for n_lines in range(2, len(parts) + 1):
                chunk = max(1, -(-len(parts) // n_lines))   # ceil → balanced lines
                try_lines = ["  |  ".join(parts[i:i + chunk])
                             for i in range(0, len(parts), chunk)]
                try:
                    max_w = max(measure.textbbox((0, 0), ln, font=f)[2] for ln in try_lines)
                except Exception:
                    max_w = width
                if max_w <= avail:
                    chosen_font, lines, done = f, try_lines, True
                    break
            if done:
                break
    if chosen_font is None:
        chosen_font = _pv_bar_font(8)
        lines = [pv_text]

    try:
        bb_ag = measure.textbbox((0, 0), "Ag", font=chosen_font)
        line_h = bb_ag[3] - bb_ag[1]
    except Exception:
        line_h = 14
    pad = max(8, line_h // 2)
    bar_h = max(30, line_h * len(lines) + pad * (len(lines) + 1))

    bar = _PI.new("RGB", (width, bar_h), (255, 255, 255))
    draw = _PD.Draw(bar)
    total_h = line_h * len(lines) + pad * (len(lines) - 1)
    y = (bar_h - total_h) // 2
    for ln in lines:
        try:
            bb = draw.textbbox((0, 0), ln, font=chosen_font)
            tw = bb[2] - bb[0]
        except Exception:
            tw = 0
        x = max(8, (width - tw) // 2)
        draw.text((x, y), ln, fill=(0, 0, 0), font=chosen_font)
        y += line_h + pad

    combined = _PI.new("RGB", (width, pil_img.height + bar_h))
    combined.paste(pil_img, (0, 0))
    combined.paste(bar, (0, pil_img.height))
    return combined


# ---------------- GRADIENTS ----------------
def _copy_metadata_into_png(src: Path, dst: Path, save_txt: bool = False,
                             extra_meta: "dict | None" = None):
    """Embed original PNG/TIFF metadata as PNG tEXt chunks in dst.
    Optionally also writes a sidecar .txt when save_txt=True.
    extra_meta keys (e.g. {"Energy": "1.23 J"}) are added/overwrite source metadata.
    Safe to call from any thread."""
    try:
        from PIL import Image as _PilImg, PngImagePlugin as _PngP
        with _PilImg.open(src) as _src_img:
            info = dict(_src_img.info)
        if extra_meta:
            info.update({k: v for k, v in extra_meta.items() if v})
        if not info:
            return
        # Re-open dst (already saved PNG) and re-save with metadata embedded
        with _PilImg.open(dst) as _dst_img:
            png_info = _PngP.PngInfo()
            for k, v in info.items():
                try:
                    png_info.add_text(str(k), str(v))
                except Exception:
                    pass
            _dst_img.save(str(dst), pnginfo=png_info)
        if save_txt:
            txt_path = dst.with_suffix(".txt")
            lines = [f"# Metadata from: {src.name}", f"# Saved as: {dst.name}", ""]
            for k, v in info.items():
                lines.append(f"{k}: {v}")
            txt_path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def _copy_metadata_into_png_bg(src: Path, dst: Path, save_txt: bool = False,
                                extra_meta: "dict | None" = None):
    """Same as _copy_metadata_into_png but dispatched to a daemon thread (non-blocking)."""
    import threading
    t = threading.Thread(target=_copy_metadata_into_png,
                         args=(src, dst, save_txt, extra_meta), daemon=True)
    t.start()

# Keep old name as alias so existing call-sites in SaveRangeTask still compile
def _save_png_metadata_txt(src: Path, dst: Path):
    _copy_metadata_into_png(src, dst, save_txt=False)

def _make_lut(stops: list[tuple[float, tuple[int,int,int]]]) -> np.ndarray:
    """Interpoluje RGB LUT (256x3) ze seznamu (pozice 0–1, (r,g,b))."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]; t1, c1 = stops[j+1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                lut[i] = tuple(int(c0[k] + f*(c1[k]-c0[k])) for k in range(3))
                break
    return lut

def _make_binary_lut() -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    lut[128:] = 255
    return lut

def _make_stepped_lut(stops: list[tuple[float, tuple[int,int,int]]]) -> np.ndarray:
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        color = stops[-1][1]
        for j in range(len(stops) - 1):
            if t < stops[j+1][0]:
                color = stops[j][1]
                break
        lut[i] = color
    return lut

GRADIENTS: dict[str, np.ndarray | None] = {
    "Default":         None,  # index 0: show original image (no grayscale conversion, no LUT)
    "Grayscale":       None,  # index 1: force grayscale, no LUT
    "Gradient": _make_lut([(0,(0,0,0)),(0.15,(255,0,0)),(0.30,(255,200,0)),(0.45,(255,255,0)),(0.58,(0,255,0)),(0.68,(0,220,255)),(0.92,(255,255,255)),(1,(255,255,255))]),
    "Hot":             _make_lut([(0,(0,0,0)),(0.33,(255,0,0)),(0.66,(255,255,0)),(1,(255,255,255))]),
    "Binary":          _make_stepped_lut([(0,(0,0,0)),(0.17,(255,0,0)),(0.33,(255,165,0)),(0.5,(255,255,0)),(0.67,(0,255,0)),(0.83,(0,200,255)),(0.92,(0,0,255)),(1,(255,255,255))]),
    "Black and White": _make_binary_lut(),
    "Viridis":         _make_lut([(0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1,(253,231,37))]),
    "Plasma":          _make_lut([(0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1,(240,249,33))]),
    "Inferno":         _make_lut([(0,(0,0,4)),(0.25,(87,16,110)),(0.5,(188,55,84)),(0.75,(249,142,9)),(1,(252,255,164))]),
    "Jet":             _make_lut([(0,(0,0,128)),(0.125,(0,0,255)),(0.375,(0,255,255)),(0.625,(255,255,0)),(0.875,(255,0,0)),(1,(128,0,0))]),
    "Turbo":           _make_lut([(0,(48,18,59)),(0.2,(70,131,193)),(0.4,(48,210,142)),(0.6,(194,228,59)),(0.8,(244,117,22)),(1,(122,4,3))]),
}
GRADIENT_NAMES = list(GRADIENTS.keys())
GRADIENT_ID_DEFAULT   = 0  # show original colors, no grayscale conversion
GRADIENT_ID_GRAYSCALE = 1  # force grayscale

# Speed reference: always 1 hour, regardless of axis length
ONE_HOUR_NS = 3_600_000_000_000

# Circle calibration
CIRCLE_SEARCH_REGION = 0.05        # menší ořez — kruh je skoro celý obraz
CIRCLE_BRIGHT_PERCENTILE = 0.85    # nižší práh — měkký přechod
CIRCLE_R_MIN_FRAC = 0.30           # kruh nemůže být příliš malý
CIRCLE_R_MAX_FRAC = 0.70           # ale může být velký
CIRCLE_MIN_POINTS = 40             # méně bodů stačí pro měkký okraj
CIRCLE_MIN_DROP = 3.0              # měkký přechod = malý drop
CIRCLE_REFINE_DROP = 2.5
# Soft circle calibration (měkký přechod)
CIRCLE_SOFT_PERCENTILE = 0.20   # hledáme poloměr kde jas klesne na 20% maxima
CIRCLE_SOFT_MIN_R_FRAC = 0.15   # měkký kruh může být menší
CIRCLE_SOFT_MAX_R_FRAC = 0.80   # a větší

DEFAULT_OPEN_DIR  = r"\\users-L3.tier0.lcs.local\cpva-image-2026\2026"
DEFAULT_OPEN_ROOT = r"\\users-L3.tier0.lcs.local\cpva-image-2026"
# Where the Save dialogs open FIRST, before the user has picked anywhere.
#
# It used to be the scratch share root (\\hapls-share.lcs.local\scratch). Handing the
# native Windows save dialog a UNC path makes it enumerate that share before it can
# draw itself, and that name is unreachable from the office network: every "Save
# Image" click paid the full SMB timeout (~48 s) before a dialog appeared. A local
# folder opens instantly, and once the user saves anywhere the dialog follows them
# there (self._last_save_dir), so saving straight to the share still works — it just
# is not on the blocking path of the very first click.
def _default_save_dir() -> str:
    home = Path.home()
    for cand in (home / "Downloads", home / "Pictures", home):
        try:
            if cand.is_dir():
                return str(cand)
        except OSError:
            continue
    return str(home)


DEFAULT_SAVE_DIR = _default_save_dir()

# Each year's images live in their own network share: cpva-image-<year>
# (e.g. …\cpva-image-2025\2025\<month>\<day>\<hour>\<camera>). Always derive
# the share from the selected year — do NOT hardcode a single year's share.
IMAGES_ROOT_BASE = r"\\users-L3.tier0.lcs.local"

def container_root_for_year(year: int) -> Path:
    """Return the network share holding a given year's images (cpva-image-<year>)."""
    return Path(IMAGES_ROOT_BASE) / f"cpva-image-{year}"

# INFO-panel reference line: normal state and the "subtraction has no reference" warning.
_REF_STATUS_STYLE = "font-size: 10px; color: #666; padding: 1px 0;"
_REF_WARN_STYLE   = "font-size: 10px; font-weight: 700; color: #b36b00; padding: 1px 0;"

_CHECKBOX_STYLE = """
QCheckBox { spacing: 6px; padding: 2px 4px; font-weight: 600; color: #111; background: transparent; }
QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #4a4a4a;
    border-radius: 3px; background: #fff; }
QCheckBox::indicator:hover { border: 2px solid #2d7dff; background: #f4f8ff; }
QCheckBox::indicator:checked { border: 2px solid #2d7dff; background: #2d7dff; }
"""

# ---------------- DATA ----------------
@dataclass(frozen=True)
class Item:
    path: Path
    ts_ns: int


def parse_unix_ns_from_name(p: Path) -> int | None:
    for m in _NS_19_RE.finditer(p.stem):
        ts_ns = int(m.group())
        if 946684800_000_000_000 <= ts_ns <= 4102444800_000_000_000:
            return ts_ns
    return None


# ---------------- TIME HELPERS ----------------
@lru_cache(maxsize=512)
def _dt_from_sec(sec: int) -> datetime:
    return datetime.fromtimestamp(sec, tz=TZ_PRAGUE)

def _dt_from_ns(ts_ns: int) -> datetime:
    return _dt_from_sec(ts_ns // 1_000_000_000)

def fmt_hhmm_from_ns(ts_ns: int) -> str:
    return f"{_dt_from_ns(ts_ns):%H:%M}"

def fmt_hhmmss_ms_from_ns(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    return f"{_dt_from_sec(sec):%H:%M:%S}.{ms:03d}"

def fmt_prague_full_from_ns(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    return f"{_dt_from_sec(sec):%Y-%m-%d %H:%M:%S}.{ms:03d}"

def fmt_prague_date_from_ns(ts_ns: int) -> str:
    """Date only (no time) — used by the INFO panel's Date row."""
    return f"{_dt_from_sec(ts_ns // 1_000_000_000):%Y-%m-%d (%a)}"

def prague_stamp_for_filename(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    return f"{_dt_from_sec(sec):%Y-%m-%d_%H-%M-%S}-{ms:03d}"

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

def replace_unix_ns_with_prague_in_filename(p: Path, ts_ns: int) -> str:
    stamp = prague_stamp_for_filename(ts_ns)
    stem = p.stem
    # Split the camera token off the 19-digit ns timestamp (with its "_-_" glue)
    # so the camera part can be cleaned on its own.
    m = re.search(r"[-_]*(?<!\d)\d{19}(?!\d)", stem)
    cam, tail = (stem[:m.start()], stem[m.end():]) if m else (stem, "")
    cam = clean_cam_for_filename(cam)
    new_stem = f"{cam}_{stamp}{tail}" if cam else f"{stamp}{tail}"
    return f"{new_stem}{p.suffix}"

def _strip_cam_name(name: str) -> str:
    """Remove -_-IMG (and variants) suffix from camera folder names for display."""
    return re.sub(r"[-_]+IMG$", "", name, flags=re.IGNORECASE).rstrip("-_")

def _cam_short_label(name: str) -> str:
    """Descriptive camera token for compact display: strip the '-IMG' suffix and
    the 'C03-032-' container/number prefix. 'C03-032-PAP1DF-IMG' -> 'PAP1DF'."""
    s = _strip_cam_name(name)
    m = re.match(r"^C\d{2}-\d{2,3}-(.+)$", s, re.IGNORECASE)
    if m:
        return m.group(1)
    return s.split("-")[-1] if "-" in s else s

# Real frame aspects (width/height) observed per camera, remembered across tile
# rebuilds AND across sessions. Without it every rebuild (a time-window rescan
# recreates all CameraViews) fell back to the coarse name hint below, so the auto
# layout was computed for square tiles and visibly jumped once the frames arrived.
_CAM_ASPECT_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "cam_aspects.json"
_CAM_ASPECTS: "dict[str, float] | None" = None   # None = not loaded yet

def _cam_aspects_store() -> dict:
    global _CAM_ASPECTS
    if _CAM_ASPECTS is None:
        try:
            _CAM_ASPECTS = {k: float(v) for k, v in
                            json.loads(_CAM_ASPECT_PATH.read_text(encoding="utf-8")).items()
                            if float(v) > 0}
        except Exception:
            _CAM_ASPECTS = {}
    return _CAM_ASPECTS

def remember_cam_aspect(name: str, w: int, h: int) -> bool:
    """Record a camera's real frame aspect. Returns True when it is new or changed
    (i.e. the layout computed from the old value is now stale)."""
    if not name or w <= 0 or h <= 0:
        return False
    store = _cam_aspects_store()
    a = w / h
    old = store.get(name)
    if old is not None and abs(old - a) <= 0.01:
        return False
    store[name] = a
    try:
        _CAM_ASPECT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CAM_ASPECT_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")
    except Exception:
        pass
    return True

def _cam_aspect_hint(name: str) -> float:
    """Width/height hint for a camera by name, used to lay out tiles before a real
    frame is loaded. Prefers the aspect actually seen for that camera; otherwise
    portrait diode arrays (PD[1-4]M1xDF) are tall and the rest are treated as
    square. The real frame is still fit with KeepAspectRatio, so an imperfect hint
    only costs a little letterbox, never correctness."""
    learned = _cam_aspects_store().get(name)
    if learned:
        return learned
    return 0.45 if re.search(r"PD[1-4]M1.?DF", name, re.IGNORECASE) else 1.0

def ns_from_dt(dt: datetime) -> int:
    return int(dt.timestamp() * 1_000_000_000)

def floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)

def axis_from_hour_folder_exact(folder: Path) -> tuple[int, int] | None:
    try:
        hh = int(folder.name)
        dd = int(folder.parent.name)
        mm = int(folder.parent.parent.name)
        yy = int(folder.parent.parent.parent.name)
        if not (0 <= hh <= 23):
            return None
        import datetime as _dt
        utc_start = _dt.datetime(yy, mm, dd, hh, 0, 0, tzinfo=_dt.timezone.utc)
        start = utc_start.astimezone(TZ_PRAGUE)
        return ns_from_dt(start), ns_from_dt(start + timedelta(hours=1))
    except Exception:
        return None

def axis_from_any_folder(folder: Path) -> tuple[int, int] | None:
    p = folder
    for _ in range(4):
        ax = axis_from_hour_folder_exact(p)
        if ax is not None:
            return ax
        p = p.parent
    return None

def folder_hour_from_prague_hour(prague_hour: int, date: datetime | None = None) -> int:
    """Folders are stored in UTC — convert Prague hour to UTC hour."""
    if date is None:
        date = datetime.now(TZ_PRAGUE)
    # Zjisti UTC offset pro daný den
    offset_hours = int(date.utcoffset().total_seconds() // 3600)
    return (prague_hour - offset_hours) % 24


# In live mode only the most recently created hour folders can still receive new
# images; older hour folders are sealed. Re-enumerating every accumulated folder
# on every poll is what makes live updates slow down (and keep slowing down) over
# a long session — so the online poller scans only the newest folders per camera.
ONLINE_ACTIVE_FOLDER_COUNT = 2  # current hour + previous (grace for late writes at rollover)
# Adaptive per-camera full-poll pacing (multi-cam live). A camera with a HEALTHY
# dir-watcher gets frames pushed instantly, so its listdir poll is only a
# rollover/overflow safety net and can be slow. Without a watcher (RDCW silently
# fails on many SMB/UNC setups), polling IS the data source: start fast, back
# off while idle, snap back to fast on the first new frame. With 12 cameras this
# is the difference between hundreds of SMB roundtrips/s and a handful.
ONLINE_POLL_MIN_INTERVAL_S     = 0.5
ONLINE_POLL_MAX_INTERVAL_S     = 5.0
ONLINE_POLL_BACKOFF            = 1.5
# Safety-net poll interval while a HEALTHY watcher covers the folder. 3 s (was
# 10 s): ReadDirectoryChangesW can die silently on SMB while looking healthy —
# until the strike detector replaces it, this bounds the worst-case lag.
ONLINE_WATCHER_POLL_INTERVAL_S = 3.0
# A per-camera image load that stays in flight longer than this is treated as
# hung (usually an SMB read that never returns); the watchdog releases its
# in-flight slot and relaunches so the camera never freezes permanently.
CAM_LOAD_WATCHDOG_S            = 6.0
# ---- multi-camera tile decoding -----------------------------------------------
# ONE pool for all camera tiles, sized to this share's measured optimum (see the
# _open_reader docstring: 31 / 106 / 204 frames/s at 1 / 8 / 16 threads). It used to
# be one 2-thread pool PER camera, which is wrong in both directions — a single
# camera got 2 threads while 14 sat idle, and twelve cameras got 24, past the point
# where more concurrency helps a latency-bound SMB share. Fairness comes from the
# per-camera depth cap below, not from partitioning the threads.
CAM_POOL_THREADS               = 16
# In-flight reads allowed per camera (see _cam_inflight_depth). The old code allowed
# exactly one — a boolean gate — which capped every tile at ~7 frames/s no matter
# how idle the machine was, and is the reason the cameras appeared to take turns.
CAM_INFLIGHT_MAX               = 4
CAM_INFLIGHT_MIN               = 2
# _reset_cam_pipeline only force-releases a camera whose load has already been
# running this long. Below it the load counts as healthy and is left alone, so a
# setting change hands over through _cam_want instead of starting a second,
# competing render of the same tile.
CAM_PIPELINE_GRACE_S           = 1.5
# A watcher that "healthily" missed frames the poll found gets a strike; at
# this many strikes it is killed and recreated (after a cooldown).
WATCHER_SUSPECT_STRIKES        = 2
WATCHER_RESTART_COOLDOWN_S     = 30.0
# Per-camera refresh dot. It must track ARRIVING FRAMES, not the health of the
# poll loop: a camera whose folder stopped receiving images still completes its
# polls forever, so anything bumped on "poll finished" blinks green while the
# picture is frozen. Green = a new frame was appended within this window,
# red = live mode is on but nothing new arrived (source down / wrong folder /
# dead watcher). Keep >= ONLINE_WATCHER_POLL_INTERVAL_S so the safety-net poll
# can always deliver within one window.
CAM_DOT_FRESH_S                = 5.0
# While following live, a tile trailing the newest arrival by less than this is
# catching up normally (the decode of frame N is still running when N+1 lands),
# so it must not be marked stale. Only used for paints made in live auto-follow;
# a manually scrubbed frame is still required to match exactly.
LIVE_PAINT_TOL_NS              = 1_500_000_000   # 1.5 s
# Minutes after the UTC hour rollover during which the previous hour folder may
# still receive late writes and must stay in the scan set.
ONLINE_ROLLOVER_GRACE_MIN      = 5


def _cam_folder_time_key(folder: Path) -> "tuple[int, int, int, int] | None":
    """(year, month, day, hour) for a camera image folder  .../YYYY/MM/DD/HH/CAM,
    or None if the path does not follow that layout."""
    try:
        hh = int(folder.parent.name)
        dd = int(folder.parent.parent.name)
        mm = int(folder.parent.parent.parent.name)
        yy = int(folder.parent.parent.parent.parent.name)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hh <= 23):
        return None
    return (yy, mm, dd, hh)


def active_scan_folders(all_folders: "list[Path]", count: "int | None" = None) -> "list[Path]":
    """Return only the folders worth scanning for new images in live mode: the
    newest `count` (default ONLINE_ACTIVE_FOLDER_COUNT) hour folders. Folders
    whose path can't be parsed are always kept (defensive — never silently skip
    an unexpected layout)."""
    if count is None:
        count = ONLINE_ACTIVE_FOLDER_COUNT
    keyed: "list[tuple[tuple, Path]]" = []
    keyless: "list[Path]" = []
    for f in all_folders:
        k = _cam_folder_time_key(f)
        if k is None:
            keyless.append(f)
        else:
            keyed.append((k, f))
    keyed.sort(key=lambda x: x[0])
    active = [f for _, f in keyed[-count:]]
    return active + keyless


def poll_scan_folders(all_folders: "list[Path]") -> "list[Path]":
    """Folders to LISTDIR-poll right now: outside the rollover grace window only
    the newest hour folder can still receive frames, so scanning the previous
    one is wasted SMB traffic."""
    in_grace = time.gmtime().tm_min < ONLINE_ROLLOVER_GRACE_MIN
    return active_scan_folders(all_folders, ONLINE_ACTIVE_FOLDER_COUNT if in_grace else 1)


def _is_dir_quiet(p: "Path") -> bool:
    """One-stat directory check (is_dir implies exists) that never raises."""
    try:
        return p.is_dir()
    except OSError:
        return False


def _dir_access_error(p: "Path") -> "str | None":
    """Probe a directory and classify the outcome for user-facing messages.

    `_is_dir_quiet` collapses 'missing' and 'access/network error' into a single
    False. That is fine for hot polling loops but makes the UI lie: a path that
    is unreachable (wrong credentials, process running elevated, no authenticated
    SMB session to the server, share offline) gets reported as 'not found'. This
    recovers the difference.

    Returns:
        None                → exists and is a directory
        "does_not_exist"    → server reachable but the path is genuinely absent
        <human message>     → is_dir() raised (access/network problem)
    """
    try:
        return None if p.is_dir() else "does_not_exist"
    except OSError as e:
        we = getattr(e, "winerror", None)
        known = {
            5:    "access denied (WinError 5) — try running WITHOUT administrator, "
                  "or open the server once in Explorer first so the session authenticates",
            53:   "network path not found (WinError 53) — server unreachable / not on the lab network / VPN",
            67:   "network name not found (WinError 67) — share name wrong or server offline",
            1326: "logon failure (WinError 1326) — not authenticated to this server in this Windows session",
        }.get(we)
        if known:
            return known
        return f"{e.strerror or e}" + (f" (WinError {we})" if we else "")


def _camera_folder_problem(cam_name: str, folders: "list[Path]") -> str:
    """Build a precise message explaining why none of a camera's candidate
    folders are usable — distinguishes 'genuinely absent' (wrong day/hour, no
    data yet) from 'unreachable' (access denied, not authenticated, server down).
    Called only on the error path, so the extra stats cost nothing in normal use."""
    access_errs = []
    for f in folders:
        err = _dir_access_error(f)
        if err and err != "does_not_exist":
            access_errs.append((f, err))
    if access_errs:
        f, err = access_errs[0]
        return (f"Camera folder '{cam_name}' could not be accessed:\n{err}\n\n"
                f"Path: {f}")
    shown = folders[0] if folders else "(no path)"
    return (f"Camera folder '{cam_name}' not found — no data for the selected "
            f"day/hour, or the path does not exist.\n\nPath: {shown}")


# Negative-probe cache for hour-folder discovery. Probing candidate folders that
# don't exist yet costs one SMB stat per camera per poll — cache the misses.
_NEG_PROBE_TTL_S = 20.0
_neg_probe_cache: "dict[str, float]" = {}
_neg_probe_lock = threading.Lock()


def _probe_hour_folder(candidate: "Path") -> bool:
    """is_dir() probe for a future hour-folder candidate, with:
    - wall-clock gate: an hour-H (UTC) folder cannot exist before hour H, so
      future hours are rejected without touching the share at all;
    - negative TTL cache: a folder that was missing moments ago is not
      re-stat'ed on every poll (12 cams × every tick = a stat storm over SMB)."""
    k = _cam_folder_time_key(candidate)
    is_current_hour = False
    if k is not None:
        g = time.gmtime()
        now_key = (g.tm_year, g.tm_mon, g.tm_mday, g.tm_hour)
        if k > now_key:
            return False
        # The CURRENT hour's folder appears at any moment after rollover —
        # never negative-cache it, or discovery lags by the TTL on top of the
        # poll interval and the camera shows no frames for ~30 s each hour.
        is_current_hour = (k == now_key)
    key = str(candidate)
    now = time.monotonic()
    if not is_current_hour:
        with _neg_probe_lock:
            t = _neg_probe_cache.get(key)
            if t is not None and (now - t) < _NEG_PROBE_TTL_S:
                return False
    try:
        ok = candidate.is_dir()
    except OSError:
        ok = False
    if not ok and not is_current_hour:
        with _neg_probe_lock:
            if len(_neg_probe_cache) > 512:
                _neg_probe_cache.clear()
            _neg_probe_cache[key] = now
    return ok


# ---------------- IMAGE SCALE READER ----------------
def _read_image_max_sample(path: Path) -> int | None:
    """Read the true pixel range maximum from image metadata.

    For TIFF: reads MaxSampleValue tag (281).
    For PNG: scans tEXt chunks for a numeric value that looks like a max-sample
             (keys like 'MaxValue', 'max_value', 'MaxSampleValue', or any key
             whose value is a plain integer in the range 255–65535).
    Returns the max value (e.g. 4095, 65535) or None if not found.
    """
    ext = path.suffix.lower()
    try:
        from PIL import Image as _PilImg
        with _PilImg.open(str(path)) as pil:
            if ext in (".tif", ".tiff"):
                tag_data = pil.tag_v2 if hasattr(pil, "tag_v2") else getattr(pil, "tag", {})
                val = tag_data.get(281)  # MaxSampleValue
                if val is not None:
                    if isinstance(val, (list, tuple)):
                        val = val[0]
                    return int(val)
            elif ext == ".png":
                info = pil.info  # dict of tEXt chunks: key → str
                # Try known key names first
                for key in ("MaxValue", "max_value", "MaxSampleValue", "max_sample_value",
                            "BitDepthMax", "bit_depth_max"):
                    v = info.get(key)
                    if v is not None:
                        try:
                            return int(float(v))
                        except (ValueError, TypeError):
                            pass
                # Fallback: any chunk whose value is a plain integer in [256, 65535]
                for v in info.values():
                    if isinstance(v, str):
                        stripped = v.strip()
                        if stripped.isdigit():
                            n = int(stripped)
                            if 256 <= n <= 65535:
                                return n
    except Exception:
        pass
    return None


def _read_tiff_max_sample(path: Path) -> int | None:
    """Legacy alias kept for any remaining call-sites."""
    return _read_image_max_sample(path)


# The imgMaxValue / MaxValue tEXt reader that used to live here is gone: it was
# only ever used to scale the display and it was wrong for that (see
# _norm16_to8_full_scale below). It also cost one extra PIL open per folder on
# the SMB path, and it identified the tag by tEXt chunk INDEX, so an unusual
# chunk list silently produced a nonsense scale factor.

# ---------------- BRIGHTNESS ----------------
# The archiver writes 16-bit frames scaled so the CAMERA's full scale lands on
# 65535: the stored value is raw_counts * 65535/(2**bits - 1), i.e. ×16 for a
# 12-bit camera, ×32 for 11-bit, ×64.06 for 10-bit, ×128.25 for 9-bit, ×257 for
# 8-bit, ×516 for 7-bit, ×1040 for 6-bit (verified across the whole camera list
# and back to January archives — the same camera even switches depth between
# frames). Dividing by 65535 is therefore THE camera-independent absolute scale.
#
# The old path did `MaxValue * arr / arr.max()` and then divided by a hardcoded
# 4095. MaxValue (PNG tEXt) is the per-frame peak in raw counts, so that expression
# collapses to `arr / 65535 * (full_scale / 4095)`: exact for 12-bit cameras and
# wrong by that factor for every other depth. On the 6–9 bit diode cameras it
# squeezed the frame into the bottom 2–12 % of the range, which is why a lit diode
# array rendered nearly black until Auto contrast was switched on — and why the
# same camera looked right on frames where it happened to run 12-bit.
_FULL_SCALE_16 = 65535.0


def _norm16_to8_full_scale(arr16: np.ndarray) -> np.ndarray:
    """16-bit frame → uint8 on the camera's absolute full-scale range (see note
    above). Deterministic per pixel value, so brightness stays comparable between
    frames and between cameras without any per-frame auto-scaling."""
    return np.clip(arr16.astype(np.float32) * (255.0 / _FULL_SCALE_16), 0, 255).astype(np.uint8)


def _gain_to_contrast_slider(gain: float) -> int:
    """Inverse of the manual contrast curve in _apply_contrast: return the slider
    value whose gain matches `gain` (1.0 → 0). Used to park the disabled Contrast
    slider where Auto actually put it."""
    if not (gain > 0) or not math.isfinite(gain):
        return 0
    c = 127.0 * 259.0 * (gain - 1.0) / (259.0 + 127.0 * gain)
    return int(round(max(-127.0, min(127.0, c))))


def _stretch_arr_f(arr_f: np.ndarray, p_low: float = 0.5, p_high: float = 99.5,
                   out: dict | None = None) -> np.ndarray:
    """Percentile contrast stretch on a full-precision float array → uint8.

    Used for auto-stretch so weak images are stretched from their REAL data range
    (before any lossy 8-bit conversion). Clipping a small fraction at the top end
    means a few hot/saturated pixels can't dominate the scale and crush the rest of
    the frame to black. Falls back to min/max if the percentile window is degenerate,
    then to a flat black image only if the data is truly uniform.

    `out`, when given, receives {"contrast": slider value equivalent to the applied
    stretch} so the UI can show where Auto landed.
    """
    if arr_f.size == 0:
        return np.zeros(arr_f.shape, dtype=np.uint8)
    s = _stat_sample(arr_f)
    lo = float(np.percentile(s, p_low))
    hi = float(np.percentile(s, p_high))
    if hi <= lo:
        lo, hi = float(arr_f.min()), float(arr_f.max())
    if hi <= lo:
        return np.zeros(arr_f.shape, dtype=np.uint8)
    if out is not None:
        # Gain relative to the absolute-scale (non-auto) rendering of the same frame.
        out["contrast"] = _gain_to_contrast_slider(_FULL_SCALE_16 / (hi - lo))
    return np.clip((arr_f - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)


# ---------------- 8-BIT BRIGHTNESS / CONTRAST PRIMITIVES ----------------
# Everything below works on both Grayscale8 and colour images. Colour is handled as
# RGB32 with the statistics taken from the luma, so a gain or a shift moves all three
# channels together and the colour balance survives — needed because the "Default"
# palette keeps RGB sources in colour and must still honour the sliders.
_BLACK_PCT = 0.5      # percentile treated as the frame's black level
_HIGH_PCT  = 99.5     # percentile treated as the frame's highlight level
# Where Auto brightness parks the frame's median, and the highlight level it refuses
# to push past. See _bc_auto_offset.
_AUTO_BRIGHT_TARGET = 128.0
_AUTO_BRIGHT_CEIL   = 250.0


# Percentile anchors are read from a subsample, not from every pixel. np.percentile on
# a native-resolution frame costs ~100 ms per pass and a render makes up to three of
# them — that alone was more than the whole 33 ms scrub budget. A deterministic stride
# over ~250k pixels lands within a code or two of the exact value. The stride is forced
# odd so it cannot lock onto a single set of columns: these sensors have vertical
# banding, and an even stride divides the row width on every square frame.
_STAT_MAX_SAMPLES = 250_000


def _stat_sample(a: np.ndarray) -> np.ndarray:
    n = a.size
    if n <= _STAT_MAX_SAMPLES:
        return a
    return np.ravel(a)[::(n // _STAT_MAX_SAMPLES) | 1]


def _img_planes(img: QImage):
    """(pixel array as float32, statistics plane as float32, is_gray).

    Grayscale returns the same array for both. Colour returns the BGR channels and a
    luma plane, so percentiles are measured on perceived brightness rather than on one
    arbitrary channel."""
    if img.format() == QImage.Format.Format_Grayscale8:
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].astype(np.float32)
        return arr, arr, True
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine() // 4, 4)[:, :img.width(), :3].astype(np.float32)
    stat = 0.114 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.299 * arr[:, :, 2]  # BGR order
    return arr, stat, False


def _img_from_planes(arr: np.ndarray, is_gray: bool, w: int, h: int) -> QImage:
    a8 = np.clip(arr, 0, 255).astype(np.uint8)
    if is_gray:
        return QImage(a8.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
    bgra = np.empty((h, w, 4), dtype=np.uint8)
    bgra[:, :, :3] = a8
    bgra[:, :, 3] = 255
    return QImage(bgra.tobytes(), w, h, w * 4, QImage.Format.Format_RGB32).copy()


def _contrast_gain(contrast: int) -> float:
    """Slider value in [-127, 127] → multiplicative gain (0 → 1.0)."""
    c = float(max(-127, min(127, contrast)))
    return (259.0 * (c + 127.0)) / (127.0 * (259.0 - c))


def _bc_auto_offset(stat: np.ndarray) -> int:
    """Additive offset for Auto brightness: park the frame's MEDIAN at mid-grey.

    It used to park the 99.5th percentile at 255, which is not an exposure at all —
    on these frames the whole tonal range lives in the bottom quarter (p50 ≈ 29,
    p99.5 ≈ 65 on the absolute scale), so that rule added +190 and every pixel came
    out between 195 and 255: a white rectangle with the picture clipped away. Anchoring
    on the median is what "auto level" means, and the highlight cap keeps the bright
    content from being clipped when a frame is already well exposed."""
    s = _stat_sample(stat)
    med = float(np.percentile(s, 50.0))
    hi  = float(np.percentile(s, _HIGH_PCT))
    off = _AUTO_BRIGHT_TARGET - med
    if hi + off > _AUTO_BRIGHT_CEIL:
        off = _AUTO_BRIGHT_CEIL - hi
    return int(round(max(-255.0, min(255.0, off))))


def _apply_stretch(img: QImage, p_low: float = 0.5, p_high: float = 99.5,
                   out: dict | None = None) -> QImage:
    """Percentile contrast stretch of an 8-bit image (grayscale or colour)."""
    if img.isNull():
        return img
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    arr, stat, is_gray = _img_planes(img)
    lo, hi = (float(v) for v in np.percentile(_stat_sample(stat), [p_low, p_high]))
    if hi <= lo + 2:
        # Degenerate percentile window (dim image with sparse bright content):
        # fall back to the real min/max so we still use the full available range
        # instead of returning a black image unchanged.
        lo, hi = float(stat.min()), float(stat.max())
    if hi <= lo:
        return img
    if out is not None:
        out["contrast"] = _gain_to_contrast_slider(255.0 / (hi - lo))
    return _img_from_planes((arr - lo) * (255.0 / (hi - lo)), is_gray, w, h)


# Kept as the name the detection helpers call positionally.
def _autostretch_gray(img: QImage, p_low: float = 0.5, p_high: float = 99.5,
                      out: dict | None = None) -> QImage:
    return _apply_stretch(img, p_low, p_high, out)


def _apply_bc(img: QImage, contrast: int = 0, auto_bright: int = 0, offset: int = 0,
              out: dict | None = None) -> QImage:
    """Manual contrast, then Auto brightness OR a manual brightness offset.

    Contrast is a multiplicative gain pivoted on the frame's own BLACK LEVEL, not on
    mid-grey. Mid-grey was unusable here: an absolute-scale frame sits around code 29,
    so `gain*(29-128)+128` drove it further down and a contrast of +20 — one nudge of
    the slider — turned the picture black (measured: p50 29 → 3). Pivoting on the black
    level means contrast only spreads the signal ABOVE the background, which is what the
    control is for and what makes small moves small.

    `out`, when given, receives {"offset": the Auto offset applied} for the UI."""
    if img.isNull():
        return img
    if not contrast and not auto_bright and not offset:
        return img
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    arr, stat, is_gray = _img_planes(img)
    if contrast:
        pivot = float(np.percentile(_stat_sample(stat), _BLACK_PCT))
        gain = _contrast_gain(contrast)
        arr = (arr - pivot) * gain + pivot
        stat = arr if is_gray else (stat - pivot) * gain + pivot
    if auto_bright:
        off = _bc_auto_offset(stat)
        if out is not None:
            out["offset"] = off
    else:
        off = int(max(-255, min(255, offset)))
    if off:
        arr = arr + off
    return _img_from_planes(arr, is_gray, w, h)


def _apply_brightness_offset(img: QImage, offset: int) -> QImage:
    return _apply_bc(img, offset=offset)


def _apply_contrast(img: QImage, contrast: int) -> QImage:
    return _apply_bc(img, contrast=contrast)


def _apply_auto_brightness(img: QImage, out: dict | None = None) -> QImage:
    return _apply_bc(img, auto_bright=1, out=out)


# Brightness/contrast render params carried through the async load pipeline.
# offset: manual brightness offset (-255..255); contrast: manual contrast (-127..127);
# auto:   1 = auto-level brightness (offset ignored). Hashable → usable in cache keys.
_RenderBC = namedtuple("_RenderBC", "offset contrast auto")
_RENDER_BC_NONE = _RenderBC(0, 0, 0)


# Where the Auto passes actually landed for the last render of each frame, so the
# greyed-out Contrast / Brightness sliders can be parked on the effective value
# instead of sitting at 0 and lying about what is on screen. Written from loader
# threads, read on the UI thread; LRU-capped like the other render side-channels.
_AUTO_BC_LOCK = threading.Lock()
_AUTO_BC: OrderedDict = OrderedDict()
_AUTO_BC_MAX = 256


def _auto_bc_put(path, vals: dict):
    if not vals:
        return
    key = str(path)
    with _AUTO_BC_LOCK:
        _AUTO_BC[key] = dict(vals)
        _AUTO_BC.move_to_end(key)
        while len(_AUTO_BC) > _AUTO_BC_MAX:
            _AUTO_BC.popitem(last=False)


def _auto_bc_get(path) -> "dict | None":
    with _AUTO_BC_LOCK:
        return _AUTO_BC.get(str(path))


# ---------------- SUBTRACTION (REFERENCE DIFF) ----------------
# Difference statistics of the last rendered subtraction frames, keyed by the
# render cache key the caller passed to LoadTask. Written from loader threads,
# read on the UI thread (dict ops only, guarded by a lock); bounded so a long
# session cannot grow it without limit.
_DIFF_STATS_LOCK = threading.Lock()
_DIFF_STATS: OrderedDict = OrderedDict()
_DIFF_STATS_MAX = 512


def _diff_stats_put(key, stats: dict):
    with _DIFF_STATS_LOCK:
        _DIFF_STATS[key] = stats
        _DIFF_STATS.move_to_end(key)
        while len(_DIFF_STATS) > _DIFF_STATS_MAX:
            _DIFF_STATS.popitem(last=False)


def _diff_stats_get(key) -> "dict | None":
    with _DIFF_STATS_LOCK:
        return _DIFF_STATS.get(key)


def _apply_reference_diff(img: QImage, ref_image: np.ndarray, sub_threshold: int = 0,
                          sub_offset: int = 0, stats_out: dict | None = None) -> QImage:
    """|current − reference| as a Grayscale8 QImage.

    ±1 is always zeroed (integer round-trip noise), then `sub_threshold` cuts
    small differences. `stats_out` receives the TRUE difference statistics —
    measured before `sub_offset` is added, so the numbers stay physical.
    `sub_offset` lifts every remaining non-zero pixel by that intensity, which
    makes 1–2 count differences visible at the cost of absolute readability.
    """
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr_cur = np.frombuffer(ptr, dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine())[:, :img.width()].copy().astype(np.float32)
    if ref_image.shape != arr_cur.shape:
        from PIL import Image as PilImage
        ref_pil = PilImage.fromarray(ref_image.astype(np.uint8))
        ref_pil = ref_pil.resize(
            (arr_cur.shape[1], arr_cur.shape[0]),
            PilImage.Resampling.NEAREST)
        ref_arr = np.asarray(ref_pil, dtype=np.float32)
    else:
        ref_arr = ref_image.copy()
    diff = np.abs(arr_cur - ref_arr)
    diff[diff <= 1] = 0
    if sub_threshold > 1:
        diff[diff < sub_threshold] = 0
    nz = diff > 0
    n_nz = int(nz.sum())
    if stats_out is not None:
        vals = diff[nz]
        stats_out.update({
            "count": n_nz,
            "total": int(diff.size),
            "mean": float(vals.mean()) if n_nz else 0.0,
            "min":  float(vals.min())  if n_nz else 0.0,
            "max":  float(vals.max())  if n_nz else 0.0,
        })
    if sub_offset and n_nz:
        diff[nz] += float(sub_offset)
    diff = np.clip(diff, 0, 255).astype(np.uint8)
    out = QImage(diff.tobytes(), img.width(), img.height(),
                 img.width(), QImage.Format.Format_Grayscale8)
    return out.copy()


# ---------------- FAST IMAGE LOAD ----------------
# Largest file still read into RAM before decoding (see _open_reader). Beyond this the
# doubled peak memory outweighs the win, so those stream from the path as before.
_INMEM_READ_MAX_BYTES = 64 * 1024 * 1024


def _open_reader(path: Path):
    """QImageReader over the file's BYTES, not over its path.

    This is the single biggest win in the whole loader, and it is not about decoding at
    all. QImageReader given a UNC path performs its network I/O *without releasing the
    GIL*, so on this share every frame froze the ENTIRE Python process — GUI thread
    included — for the whole round trip. Measured on \\\\users-L3 (445x420 frames, 0.27 MB):

        QImageReader(path):  27 -> 32 frames/s from 1 to 16 threads (no scaling at all),
                             and a 5 ms GUI ticker managed 132 ticks in 3 s,
                             p99 153 ms, max 170 ms.
        bytes + decode:      31 -> 106 -> 204 frames/s at 1 / 8 / 16 threads,
                             664 ticks in 3 s, p99 6.1 ms, max 8.3 ms.

    Python's open().read() releases the GIL for the network wait, and decoding from memory
    is a couple of ms. Same handler, same pixels — only the source of the bytes changes.
    The returned buffer and byte array MUST be kept alive by the caller for as long as the
    reader is used, hence returning all three.

    Falls back to the path-based reader when the bytes cannot be read (file locked by the
    camera writer, or too large to buffer), so behaviour never gets worse than before."""
    try:
        if path.stat().st_size <= _INMEM_READ_MAX_BYTES:
            with open(path, "rb") as fh:
                data = fh.read()
            ba = QByteArray(data)
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            r = QImageReader(buf)
            r.setAutoTransform(True)
            # No format hint: QImageReader sniffs the magic bytes, which is strictly more
            # reliable than trusting the extension.
            return r, buf, ba
    except Exception:
        pass
    r = QImageReader(str(path))
    r.setAutoTransform(True)
    return r, None, None


def load_image_scaled(path: Path, max_side: int, brighten: bool, gradient_id: int = 0, brightness_offset: int = 0, ref_image: np.ndarray | None = None, sub_threshold: int = 0, contrast: int = 0, auto_bright: int = 0, sub_offset: int = 0, stats_out: dict | None = None) -> QImage:
    r, _buf, _ba = _open_reader(path)
    sz = r.size()
    if sz.isValid():
        w, h = sz.width(), sz.height()
        if w > 0 and h > 0 and max_side > 0:
            scale = max(w, h) / max_side
            if scale > 1.0:
                r.setScaledSize(QSize(max(1, int(w / scale)), max(1, int(h / scale))))
    img = r.read()
    if img.isNull():
        return QImage()

    # True once a full-precision percentile stretch has already been applied to 16-bit
    # data, so the 8-bit _apply_stretch pass below is skipped (would be redundant).
    did_autostretch = False
    # Effective Auto contrast / brightness of this render, published for the sliders.
    auto_out: dict = {}

    # "Default" = show the original colours (no grayscale conversion, no LUT). Every
    # other palette goes to Grayscale8 first, which is what the LUT maps.
    #
    # Default used to `return` right after the 16-bit normalization, which quietly made
    # Auto contrast, Auto brightness and BOTH sliders dead controls on that palette —
    # the preview layer applied them, the refined render did not, so a drag showed one
    # picture and settling showed another. The enhancement chain below is now shared.
    is_default = (gradient_id == GRADIENT_ID_DEFAULT)

    if img.format() in (QImage.Format.Format_Grayscale16, QImage.Format.Format_RGB16):
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        arr16 = np.frombuffer(ptr, dtype=np.uint16).reshape(img.height(), img.bytesPerLine() // 2)[:, :img.width()].copy()
        if brighten and ref_image is None:
            # Auto-stretch: percentile-stretch the FULL-PRECISION 16-bit data so weak
            # frames are revealed and a few hot pixels can't crush the rest to black.
            # Done here (not on the 8-bit result) because the absolute-scale path
            # below would otherwise lose the dim content before _apply_stretch
            # can see it.
            arr8 = _stretch_arr_f(arr16.astype(np.float32), out=auto_out)
            did_autostretch = True
        else:
            arr8 = _norm16_to8_full_scale(arr16)
        img = QImage(arr8.tobytes(), img.width(), img.height(), img.width(), QImage.Format.Format_Grayscale8)
    elif is_default:
        # Keep the original colours; subtraction is the one operation that needs mono.
        if ref_image is not None and img.format() != QImage.Format.Format_Grayscale8:
            img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    else:
        img = img.convertToFormat(QImage.Format.Format_Grayscale8)

    if ref_image is not None:
        img = _apply_reference_diff(img, ref_image, sub_threshold, sub_offset, stats_out)

    if brighten and ref_image is None and not did_autostretch:
        img = _apply_stretch(img, out=auto_out)

    img = _apply_bc(img, contrast=contrast, auto_bright=auto_bright,
                    offset=brightness_offset, out=auto_out)

    _auto_bc_put(path, auto_out)

    if is_default:
        return img

    # gradient_id == GRADIENT_ID_GRAYSCALE (1): no LUT, already grayscale
    # gradient_id >= 2: apply color LUT
    lut = GRADIENTS[GRADIENT_NAMES[gradient_id]] if gradient_id >= 2 else None
    if lut is not None:
        img = _apply_lut(img, lut)
    return img


def _apply_lut(img: QImage, lut: np.ndarray) -> QImage:
    """Aplikuje RGB LUT na grayscale QImage, vrátí RGB32 QImage."""
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    ptr = img.bits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, img.bytesPerLine())[:, :w].copy()
    rgb = lut[arr]  # (h, w, 3)
    # Qt RGB32 = BGRA v paměti na little-endian
    bgra = np.zeros((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = rgb[:, :, 2]  # B
    bgra[:, :, 1] = rgb[:, :, 1]  # G
    bgra[:, :, 2] = rgb[:, :, 0]  # R
    bgra[:, :, 3] = 255
    out = QImage(bgra.tobytes(), w, h, w * 4, QImage.Format.Format_RGB32)
    return out.copy()


# ---- process working set, for _diag_log ---------------------------------------
_RSS_PROBE = None   # (GetProcessMemoryInfo, _PMC, byref, sizeof) resolved once


def _rss_mb() -> float:
    """Process working set in MB, or -1.0 if it cannot be read.

    The previous inline version returned -1 on EVERY line of EVERY run — the whole
    reason this log exists (finding which resource grows before the ~2 h freeze) was
    therefore never actually measured. Cause: neither `restype` nor `argtypes` was
    declared, so ctypes defaulted GetCurrentProcess to restype=c_int. The pseudo
    handle (HANDLE)-1 came back as Python -1 and was then marshalled as a 32-bit
    value, so the callee saw 0x00000000FFFFFFFF and failed with ERROR_INVALID_HANDLE
    (6). Declaring the prototypes fixes it.

    K32GetProcessMemoryInfo (kernel32) is preferred over psapi's export so a frozen
    build does not have to carry psapi.dll. Resolved once and cached: the old code
    re-loaded the DLL every minute."""
    global _RSS_PROBE
    try:
        if _RSS_PROBE is None:
            import ctypes as _c
            from ctypes import wintypes as _w

            class _PMC(_c.Structure):
                _fields_ = [("cb", _w.DWORD), ("PageFaultCount", _w.DWORD),
                            ("PeakWorkingSetSize", _c.c_size_t), ("WorkingSetSize", _c.c_size_t),
                            ("QuotaPeakPagedPoolUsage", _c.c_size_t), ("QuotaPagedPoolUsage", _c.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", _c.c_size_t), ("QuotaNonPagedPoolUsage", _c.c_size_t),
                            ("PagefileUsage", _c.c_size_t), ("PeakPagefileUsage", _c.c_size_t)]

            k32 = _c.WinDLL("kernel32", use_last_error=True)
            fn = getattr(k32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                fn = _c.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
            k32.GetCurrentProcess.restype = _w.HANDLE
            k32.GetCurrentProcess.argtypes = []
            fn.restype = _w.BOOL
            fn.argtypes = [_w.HANDLE, _c.POINTER(_PMC), _w.DWORD]
            _RSS_PROBE = (k32, fn, _PMC, _c)
        k32, fn, _PMC, _c = _RSS_PROBE
        pmc = _PMC()
        pmc.cb = _c.sizeof(_PMC)
        if fn(k32.GetCurrentProcess(), _c.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / (1024 * 1024)
    except Exception:
        pass
    return -1.0


class PixCache:
    """LRU of rendered pixmaps keyed by (idx, max_side, …).

    `native_keep` additionally caps how many NATIVE-resolution entries (max_side
    == FULL_RES_SIDE) may live here at once: those are ~22 MB each, so letting the
    plain LRU fill up with them was worth gigabytes. Pass None for caches whose
    keys are not (idx, max_side, …) tuples."""

    def __init__(self, max_items: int, native_keep: "int | None" = NATIVE_CACHE_KEEP):
        self.max_items = max_items
        self.native_keep = native_keep
        self._d: OrderedDict = OrderedDict()

    @staticmethod
    def _is_native(key) -> bool:
        # REFINE_MAX_SIDE renders are ~9 MB each — not native, but far too big to let
        # CACHE_SIZE of them accumulate, so they are capped the same way.
        if not (isinstance(key, tuple) and len(key) > 1):
            return False
        side = key[1]
        return side == FULL_RES_SIDE or side >= HEAVY_RENDER_SIDE

    def get(self, key) -> QPixmap | None:
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def put(self, key, val: QPixmap):
        self._d[key] = val
        self._d.move_to_end(key)
        while len(self._d) > self.max_items:
            self._d.popitem(last=False)
        if self.native_keep is not None and self._is_native(key):
            # OrderedDict iterates oldest-used first, so this drops the least
            # recently shown full-res frames and keeps the newest ones.
            natives = [k for k in self._d if self._is_native(k)]
            for k in natives[:max(0, len(natives) - self.native_keep)]:
                self._d.pop(k, None)

    def clear(self):
        self._d.clear()


# ---------------- WHOLE-WINDOW PREVIEW (PROXY) LAYER ----------------
def load_proxy_gray(path: Path, max_side: int = PROXY_MAX_SIDE):
    """Decode one frame for the preview layer → (u8_array, lo, hi) or None.

    No brightness/contrast, no palette: those are re-applied to the tiny array at paint
    time (_proxy_render), so one stored frame serves every render setting.

    STORAGE IS 8-BIT, BUT THE STRETCH IS TAKEN ON THE 16-BIT DATA. That distinction is the
    whole design, and getting it wrong either way costs something real:

      - Storing the raw uint16 (what this used to do) costs 2 bytes/px, so the RAM budget
        buys half as many frames. On a 4-camera 4-hour window that meant the preview could
        only ever hold every 2nd or 3rd frame, and a drag therefore could not show what it
        was dragged across, however fast the rest of the pipeline got.
      - Flattening to uint8 on the ABSOLUTE full scale (the obvious saving) throws away
        exactly what Auto contrast needs: these frames sit in the bottom few percent of the
        range — a 445x420 PFM frame runs p0.5..p99.5 = 1400..3060 out of 65535, i.e. six
        8-bit codes — so stretching the uint8 version spread six codes over the full range
        and the preview came out in harsh posterized bands while the refined render of the
        same frame was smooth.

    So: compute p0.5/p99.5 on the 16-bit pixels HERE (on a worker thread, off the GUI
    thread where _proxy_render used to spend ~1 ms per tile per frame doing it), store the
    frame already mapped into those 256 codes, and keep (lo, hi) alongside so the absolute
    scale can be recovered exactly. Auto ON is then bit-identical to the old behaviour;
    Auto OFF is an affine LUT of the stored codes, accurate to within one output code.

    The one real loss is that pixels outside p0.5..p99.5 are clipped in storage, so with
    Auto OFF a saturated spike renders at the p99.5 level instead of pure white. It is
    bounded by the downscale (setScaledSize already averages a few hot pixels away at this
    size) and undone entirely by _refine_current_frame, which re-reads the settled frame
    from disk. Set PROXY_STORE_8BIT = False to go back to raw uint16."""
    try:
        # Bytes first, then decode — see _open_reader. The sweep is the heaviest user of
        # the share, so it benefits most from not holding the GIL through each read.
        r, _buf, _ba = _open_reader(path)
        sz = r.size()
        if sz.isValid():
            w, h = sz.width(), sz.height()
            if w > 0 and h > 0 and max_side > 0:
                scale = max(w, h) / max_side
                if scale > 1.0:
                    r.setScaledSize(QSize(max(1, int(w / scale)), max(1, int(h / scale))))
        img = r.read()
        if img.isNull():
            return None
        if img.format() in (QImage.Format.Format_Grayscale16, QImage.Format.Format_RGB16):
            ptr = img.bits()
            if hasattr(ptr, "setsize"):
                ptr.setsize(img.sizeInBytes())
            arr16 = np.frombuffer(ptr, dtype=np.uint16).reshape(
                img.height(), img.bytesPerLine() // 2)[:, :img.width()].copy()
            if not PROXY_STORE_8BIT:
                return arr16, 0.0, _FULL_SCALE_16, _FULL_SCALE_16
            # A TWO-SEGMENT ramp, because 256 codes cannot cover both a narrow data band and
            # a 20x outlier with one linear scale:
            #   codes 0..239   span p0.1..p99.9 — the picture
            #   codes 240..255 span p99.9..max  — the highlight tail
            # (lo, hi, mx) travel with the frame so _proxy_render can invert both segments.
            #
            # Why not one linear scale over 0..65535: these frames run p0.1..p99.9 ≈
            # 1536..2868 out of 65535, so the actual picture would be about five codes wide
            # — the harsh posterised banding the raw-uint16 storage existed to avoid.
            #
            # Why not simply clip at p99.9: with Auto OFF a clipped saturated spot renders
            # at the p99.9 LEVEL, i.e. dark — measured 11 instead of 253, so a saturated
            # spot read as a dim one for the whole drag. On a laser diagnostic that is a
            # wrong reading, not a quality trade.
            #
            # Why a ramp and not one reserved code: with a single "≥ p99.9" code every pixel
            # above the knee renders at the frame MAXIMUM, so a mildly bright pixel and a
            # saturated one look identical (measured 234 codes too bright). 16 codes over
            # the tail keeps them apart, and costs the main band 240 codes instead of 256 —
            # under one code of extra quantisation.
            s = _stat_sample(arr16)
            lo = float(np.percentile(s, 0.1))
            hi = float(np.percentile(s, 99.9))
            mx = float(arr16.max())
            if hi <= lo:
                lo, hi = float(arr16.min()), mx
            if hi <= lo:
                return np.zeros(arr16.shape, dtype=np.uint8), 0.0, _FULL_SCALE_16, mx
            f = arr16.astype(np.float32)
            u8 = np.clip((f - lo) / (hi - lo) * PROXY_KNEE_CODE,
                         0, PROXY_KNEE_CODE).astype(np.uint8)
            if mx > hi:
                tail = f > hi
                if tail.any():
                    u8[tail] = (PROXY_KNEE_CODE + 1 + np.clip(
                        (f[tail] - hi) / (mx - hi) * (254 - PROXY_KNEE_CODE),
                        0, 254 - PROXY_KNEE_CODE)).astype(np.uint8)
            return u8, lo, hi, max(mx, hi)
        if img.format() != QImage.Format.Format_Grayscale8:
            img = img.convertToFormat(QImage.Format.Format_Grayscale8)
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        arr8 = np.frombuffer(ptr, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].copy()
        # An 8-bit source is already on its own full scale; lo/hi say "no remapping".
        return arr8, 0.0, 255.0, 255.0
    except Exception:
        return None


def _proxy_plan(items: list, budget: int) -> "tuple[list[int], int, int]":
    """Pick which frames of `items` to preload, ordered coarse → fine.

    Returns (indices, ts_gap, step). The order doubles the sampling density on every
    pass, so the WHOLE window is covered after a small fraction of the work and
    each further pass only tightens it — scrubbing anywhere is useful immediately
    instead of only inside an already-finished prefix. `ts_gap` is the distance
    between two frames of the FINISHED plan; while the sweep is still running the
    accepted distance follows the density actually decoded so far (see
    _ProxyTrack.current_gap) — pinning acceptance to the finished spacing is what
    made the coarse passes above worthless: the preview refused every position
    until the last pass, which is half of all the reads. `step` is the plan's grid
    spacing in item indices, used to search the plan around the cursor."""
    n = len(items)
    if n <= 0 or budget <= 0:
        return [], 0, 1
    step = max(1, math.ceil(n / budget))
    base = list(range(0, n, step))
    if base and base[-1] != n - 1:
        base.append(n - 1)
    order: list[int] = []
    seen: set[int] = set()
    stride = 1
    while stride < len(base):
        stride *= 2
    while stride >= 1:
        for i in range(0, len(base), stride):
            v = base[i]
            if v not in seen:
                seen.add(v)
                order.append(v)
        stride //= 2
    span = items[-1].ts_ns - items[0].ts_ns
    # ONE sampling step, not two. The preview stands in for a frame it did not
    # actually decode, so this gap is how far the picture may be from the timestamp
    # the info panel is showing. 2× made a sampled window show a frame dozens of
    # positions away; 1× halves that and simply refuses more often, falling back to
    # a real load — which is always correct.
    ts_gap = max(1, int(span / max(1, len(base) - 1))) if span > 0 else 1 << 62
    return order, ts_gap, step


class _ProxyTrack:
    """Preloaded preview frames of one timeline (single-cam, or one camera).

    Keyed by ts_ns, not by list index: the item list grows and shifts (Refresh,
    live-cap backfill) while a preview stays valid, and timestamps come from the
    file names so they never move."""

    def __init__(self):
        self.frames: dict = {}                    # ts_ns → (u8 array, lo, hi)
        self.planned: list[int] = []              # item indices, coarse→fine order
        self.pos = 0                              # how far through `planned`
        self.ts_gap = 0                           # accepted ts distance, finished plan
        self.step = 1                             # plan grid spacing, in item indices
        self.side = 0                             # decoded side these frames were read at.
                                                  # Frames of the wrong size have to be
                                                  # dropped when _proxy_side changes (a
                                                  # camera added or removed), and the track
                                                  # is reused across _proxy_start calls
                                                  # whenever the track COUNT matches — which
                                                  # it does when only the size changed.
        self.taken: set[int] = set()              # item indices already dispatched
        self.focus_g0 = -1                        # grid point the cursor scan started at
        self.focus_d  = 0                         # how far out that scan already got
        self.focus_turn = 0                       # alternates cursor-first / coarse-plan
                                                  # batches for THIS track — see
                                                  # _proxy_next_batch
        self.failed = 0                           # planned frames that would not decode
                                                  # (file caught mid-write, truncated PNG).
                                                  # Discounted by _proxy_covered_track:
                                                  # they can never land in `frames`, so
                                                  # counting them made a camera with a few
                                                  # bad files hold the whole viewer in the
                                                  # "not covered" state forever.
        self._sorted: list[int] = []
        self._dirty = True
        self._cur_gap = 0                         # cached current_gap()

    def add(self, ts_ns: int, arr: np.ndarray):
        # Insert into the sorted view instead of invalidating it: a full re-sort of up to
        # 6000 keys ran on the GUI thread after every arriving batch (~100/s at full
        # sweep), and both current_gap() and nearest() pull on it from inside the 33 ms
        # scrub tick. _dirty / _sorted_ts stay as the correct fallback.
        if ts_ns in self.frames:
            # Re-decode of a frame already held: the sorted view is still correct, so do
            # NOT dirty it. Marking it dirty here forced a full re-sort of up to 6000 keys
            # on the GUI thread for a batch that changed nothing about the ordering.
            self.frames[ts_ns] = arr
            return
        if not self._dirty:
            bisect.insort(self._sorted, ts_ns)
            s = self._sorted
            self._cur_gap = (2 * (s[-1] - s[0]) // max(1, len(s) - 1)) if len(s) > 1 else 0
        self.frames[ts_ns] = arr

    def _sorted_ts(self) -> list:
        if self._dirty:
            self._sorted = sorted(self.frames)
            self._dirty = False
            s = self._sorted
            # Spacing of what is DECODED right now. The sweep walks the plan
            # coarse → fine, so the decoded set stays roughly evenly spread over the
            # whole window at every moment: after the first pass it is one frame every
            # span/2, then span/4, span/8 … Doubling covers the half that the pass in
            # progress has not tightened yet, so a position there is served instead of
            # refused. This is the tolerance a drag uses; standing still keeps ts_gap.
            self._cur_gap = (2 * (s[-1] - s[0]) // max(1, len(s) - 1)) if len(s) > 1 else 0
        return self._sorted

    def current_gap(self) -> int:
        """Max distance worth accepting from the frames decoded SO FAR (0 = none)."""
        self._sorted_ts()
        return self._cur_gap

    def nbytes(self) -> int:
        """RAM this track's decoded frames occupy. Logged per minute (proxMB): the
        preview's cost was previously invisible — the budget was a constant nobody
        could check against reality, and the rss field that would have caught it was
        broken (see _rss_mb)."""
        for a in self.frames.values():
            # Every frame in a track has the same shape and dtype, so one sample is
            # enough — summing .nbytes over 80k arrays runs on the GUI thread.
            if isinstance(a, tuple):
                a = a[0]
            return len(self.frames) * int(getattr(a, "nbytes", 0))
        return 0

    def nearest(self, ts_ns: int, tol_ns: "int | None" = None) -> "tuple[int, np.ndarray] | None":
        """Preloaded frame closest in time to ts_ns, or None if the gap is too big
        (that part of the window has not been preloaded yet). `tol_ns` overrides the
        finished-plan tolerance — see current_gap()."""
        if not self.frames:
            return None
        s = self._sorted_ts()
        limit = self.ts_gap if tol_ns is None else max(self.ts_gap, tol_ns)
        j = bisect.bisect_left(s, ts_ns)
        best = None
        best_d = None
        for k in (j - 1, j):
            if 0 <= k < len(s):
                d = abs(s[k] - ts_ns)
                if best_d is None or d < best_d:
                    best, best_d = s[k], d
        if best is None or best_d > limit:
            return None
        return best, self.frames[best]


class _ProxySignals(QObject):
    batch = Signal(int, int, object)   # (gen, track index, [(ts_ns, arr|None), …])


class _ProxyTask(QRunnable):
    """Decode one batch of preview frames off the UI thread."""

    def __init__(self, gen, track, jobs, signals, stop_flag, side=PROXY_MAX_SIDE):
        super().__init__()
        self.gen = gen
        self.track = track
        self.jobs = jobs
        self.signals = signals
        self.stop_flag = stop_flag
        self.side = side

    def run(self):
        out = []
        for ts_ns, path in self.jobs:
            if self.stop_flag.is_set():
                break
            out.append((ts_ns, load_proxy_gray(path, self.side)))
        try:
            self.signals.batch.emit(self.gen, self.track, out)
        except RuntimeError:
            pass


# ---------------- ASYNC LOADER ----------------
class _PvSignals(QObject):
    result = Signal(int, object)   # (gen, results_dict)

class LoaderSignals(QObject):
    # 7th arg is the _RenderBC render-params tuple (was a plain brightness_offset int).
    # 9th arg (key) is the EXACT cache key the caller computed at launch time. It is
    # echoed back so the completion handler never has to recompute ref-id / sub-threshold
    # from live UI state — recomputing there mismatched the launch key whenever the user
    # changed reference/subtraction/gradient mid-flight, which leaked _inflight entries
    # (loads that never re-fire), poisoned the cache with wrong-key pixmaps, and stranded
    # _display_load_key so the view froze after the app had been running a while.
    loaded = Signal(int, int, int, int, int, int, object, QImage, object)

class LoadTask(QRunnable):
    def __init__(self, gen, req_id, idx, path, max_side, brighten, gradient_id, signals, bc=_RENDER_BC_NONE, ref_image=None, sub_threshold=0, sub_offset=0, key=None):
        super().__init__()
        self.gen = gen; self.req_id = req_id; self.idx = idx
        self.path = path; self.max_side = max_side; self.brighten = brighten
        self.gradient_id = gradient_id; self.bc = bc
        self.ref_image = ref_image; self.sub_threshold = sub_threshold
        self.sub_offset = sub_offset
        self.key = key
        self.signals = signals

    def run(self):
        # load_image_scaled must never raise out of this thread: QThreadPool swallows
        # unhandled exceptions silently (no traceback, no signal), which used to leave
        # the caller's busy/in-flight bookkeeping stuck forever — that camera (or that
        # frame slot) would then never load again even though new frames kept arriving,
        # e.g. a fast multi-cam camera getting caught mid-write over the network share
        # more often than a slow one. Always emit, using a null QImage on failure, so
        # _on_cam_loaded / _on_loaded release their busy flags and retry the next frame.
        # Difference statistics are collected only for subtraction renders and
        # published under the SAME cache key the pixmap is stored under, so the UI
        # can label a frame whether it came from this load or from the pixmap cache.
        stats = {} if self.ref_image is not None else None
        try:
            img = load_image_scaled(self.path, self.max_side, bool(self.brighten), self.gradient_id, self.bc.offset, self.ref_image, self.sub_threshold, self.bc.contrast, self.bc.auto, self.sub_offset, stats)
        except Exception:
            img = QImage()
        if stats and self.key is not None and not img.isNull():
            _diff_stats_put(self.key, stats)
        try:
            self.signals.loaded.emit(self.gen, self.req_id, self.idx, self.max_side, self.brighten, self.gradient_id, self.bc, img, self.key)
        except RuntimeError:
            pass

# ---------------- BACKGROUND SCAN ----------------
class ScanSignals(QObject):
    status     = Signal(int, str)
    progress   = Signal(int, int, int, int, int)
    finished   = Signal(int, list)
    cancelled  = Signal(int)
    quick_item = Signal(int, object)   # (gen, Item) — newest found so far, emitted ASAP

class ScanTask(QRunnable):
    def __init__(self, gen, folders):
        super().__init__()
        self.gen = gen; self.folders = folders
        self.signals = ScanSignals()
        self._cancel = False

    def cancel(self): self._cancel = True

    def run(self):
        items = []; processed = 0; found = 0
        total_folders = len(self.folders)
        quick_emitted = False
        best_item: Item | None = None   # track newest item for quick_item signal
        try:
            for folder_i, folder in enumerate(self.folders, 1):
                if self._cancel:
                    self.signals.cancelled.emit(self.gen); return
                if not folder.exists() or not folder.is_dir():
                    continue
                self.signals.status.emit(self.gen, f"Scanning {folder_i}/{total_folders}: {folder}")
                with os.scandir(folder) as it:
                    for e in it:
                        if self._cancel:
                            self.signals.cancelled.emit(self.gen); return
                        if not e.is_file():
                            continue
                        processed += 1
                        if processed % 500 == 0:
                            self.signals.progress.emit(self.gen, folder_i, total_folders, processed, found)
                        p = Path(e.path)
                        if p.suffix.lower() not in IMG_EXT:
                            continue
                        ts_ns = parse_unix_ns_from_name(p)
                        if ts_ns is None:
                            continue
                        item = Item(p, ts_ns)
                        items.append(item)
                        found += 1
                        # Track the newest item seen so far
                        if best_item is None or ts_ns > best_item.ts_ns:
                            best_item = item
                        if found % 250 == 0:
                            self.signals.progress.emit(self.gen, folder_i, total_folders, processed, found)
                        # Emit quick_item after first 50 files found — enough for a good max
                        if not quick_emitted and found >= 50 and best_item is not None:
                            self.signals.quick_item.emit(self.gen, best_item)
                            quick_emitted = True
        except Exception:
            pass
        # Emit quick_item even if folder had <50 files
        if not quick_emitted and best_item is not None:
            self.signals.quick_item.emit(self.gen, best_item)
        self.signals.progress.emit(self.gen, total_folders, total_folders, processed, found)
        items.sort(key=lambda it: it.ts_ns)
        self.signals.finished.emit(self.gen, items)

class RefreshScanSignals(QObject):
    finished = Signal(int, list)

class RefreshScanTask(QRunnable):
    """Skenuje složky a vrátí VŠECHNY nalezené položky — merge udělá Viewer."""
    def __init__(self, gen, folders):
        super().__init__()
        self.gen = gen
        self.folders = folders
        self.signals = RefreshScanSignals()

    def run(self):
        items = []
        try:
            for folder in self.folders:
                if not folder.exists() or not folder.is_dir():
                    continue
                with os.scandir(folder) as it:
                    for e in it:
                        if not e.is_file():
                            continue
                        p = Path(e.path)
                        if p.suffix.lower() not in IMG_EXT:
                            continue
                        ts_ns = parse_unix_ns_from_name(p)
                        if ts_ns is None:
                            continue
                        items.append(Item(p, ts_ns))
        except Exception:
            pass
        items.sort(key=lambda it: it.ts_ns)
        self.signals.finished.emit(self.gen, items)

# ---------------- BACKGROUND SAVE ----------------
class SaveRangeSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int, int)

class SaveRangeTask(QRunnable):
    def __init__(self, items, outp, name_fn, gradient_id=0, brighten=False, overlay_params=None,
                 energy_map: "dict | None" = None,
                 pv_channels: "dict | None" = None,
                 pv_units: "dict | None" = None):
        super().__init__()
        self.items = items; self.outp = outp; self.name_fn = name_fn
        self.gradient_id = gradient_id; self.brighten = brighten
        self.overlay_params = overlay_params  # dict or None
        self.energy_map = energy_map or {}    # filename -> energy string
        # {display_name: cpva_channel} for per-image PV lookup from archiver at save time.
        self.pv_channels: dict = pv_channels or {}
        self.pv_units: dict = pv_units or {}
        self.signals = SaveRangeSignals()

    def _draw_overlay_on_pixmap(self, src_path):
        """Load image, draw overlay, return QPixmap. Returns None on failure."""
        p = self.overlay_params or {}
        img = load_image_scaled(src_path, 9999, self.brighten, self.gradient_id)
        if img.isNull(): return None
        pix = QPixmap.fromImage(img)
        w, h = pix.width(), pix.height()
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if p.get('show_cross') and p.get('cross_pos_norm') is not None:
            cn = p['cross_pos_norm']
            cx = int(cn.x() * w); cy = int(cn.y() * h)
            sz = p.get('cross_size', 18)
            pen = QPen(p.get('cross_color', QColor(0, 255, 0)))
            pen.setWidth(max(p.get('cross_thick', 2), w // 500))
            painter.setPen(pen)
            painter.drawLine(cx - sz, cy, cx + sz, cy)
            painter.drawLine(cx, cy - sz, cx, cy + sz)
        if p.get('show_circle') and p.get('circle_center_norm') is not None:
            cn = p['circle_center_norm']
            cx = int(cn.x() * w); cy = int(cn.y() * h)
            if p.get('circle_rx_norm') is not None:
                rx = int(p['circle_rx_norm'] * w); ry = int(p['circle_ry_norm'] * h)
            else:
                r = int(p.get('circle_r_norm', 0.1) * min(w, h)); rx = ry = r
            pen = QPen(p.get('circle_color', QColor(255, 255, 0)))
            pen.setWidth(max(p.get('circle_thick', 2), w // 500))
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
        if p.get('show_square') and p.get('square_rect_norm') is not None:
            ln, tn, rn, bn = p['square_rect_norm']
            sx = int(ln * w); sy = int(tn * h)
            sw = int((rn - ln) * w); sh = int((bn - tn) * h)
            pen = QPen(p.get('square_color', QColor(0, 200, 255)))
            pen.setWidth(max(p.get('square_thick', 2), w // 500))
            painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sx, sy, sw, sh)
        painter.end()
        return pix

    def _save_view(self, src_path, ts_ns, view_dst, pv_text, save_txt, extra_meta):
        """Render the recoloured/overlay view of src_path (+ optional PV bar) to
        view_dst. Returns True on success."""
        pix = self._draw_overlay_on_pixmap(src_path)
        if pix is None:
            return False
        if pv_text:
            try:
                from PIL import Image as _PI
                import tempfile as _tf
                with _tf.NamedTemporaryFile(suffix=".png", delete=False) as _t:
                    _tp = Path(_t.name)
                pix.save(str(_tp))
                _pil_img = _PI.open(_tp)
                render_pv_bar_below(_pil_img, pv_text).save(str(view_dst))
                _pil_img.close()
                _tp.unlink(missing_ok=True)
            except Exception:
                pix.save(str(view_dst))
        else:
            pix.save(str(view_dst))
        _copy_metadata_into_png(src_path, view_dst, save_txt=save_txt, extra_meta=extra_meta)
        return True

    def run(self):
        n = 0; errors = 0; total = len(self.items)
        save_txt = getattr(self, 'save_txt', False)
        # Warm the PV archiver caches for the whole range up front, in parallel, so
        # the per-image loop below never stalls on one HTTP request after another
        # (this is what made the first save_range click take so long).
        if self.pv_channels and self.items:
            self.signals.progress.emit(0, total, "Loading PV data…")
            pv_warm_days(list(self.pv_channels.values()),
                         [it.ts_ns for it in self.items])
        p = self.overlay_params or {}
        # A drawn shape (cross/circle/square) is the only thing that counts as a real
        # "modification" — palette, brightness and the PV bar are just views/annotations.
        has_shapes = bool(
            (p.get('show_cross')  and p.get('cross_pos_norm')   is not None) or
            (p.get('show_circle') and p.get('circle_center_norm') is not None) or
            (p.get('show_square') and p.get('square_rect_norm') is not None)
        )
        is_recoloured = (self.gradient_id != GRADIENT_ID_DEFAULT) or self.brighten
        for done, it in enumerate(self.items, 1):
            energy_str = self.energy_map.get(it.path.name, "")
            extra_meta = {"Energy": energy_str} if energy_str else None
            base = self.outp / self.name_fn(it)   # carries the original file suffix
            # Per-image PV text: archiver values at each image's OWN timestamp
            # (day-level cache → ~1 HTTP request per channel per day).
            if self.pv_channels:
                pv_text = (pv_text_for_ts(it.ts_ns, list(self.pv_channels.keys()))
                           or (self.overlay_params or {}).get('pv_text', ''))
            else:
                pv_text = (self.overlay_params or {}).get('pv_text', '')
            try:
                need_view = has_shapes or is_recoloured or bool(pv_text)
                if not need_view:
                    # Untouched image — saved exactly once as the original.
                    shutil.copy2(it.path, base)
                    _copy_metadata_into_png(it.path, base, save_txt=save_txt, extra_meta=extra_meta)
                    n += 1
                elif has_shapes:
                    # Real modification → keep BOTH the pristine original and the view.
                    shutil.copy2(it.path, base)
                    _copy_metadata_into_png(it.path, base, save_txt=save_txt, extra_meta=extra_meta)
                    view_dst = base.parent / f"{base.stem}_annotated.png"
                    if self._save_view(it.path, it.ts_ns, view_dst, pv_text, save_txt, extra_meta):
                        n += 1
                    else:
                        errors += 1
                else:
                    # Palette / brightness / PV only — not a modification → save once.
                    suffix = "_annotated" if pv_text else ""
                    view_dst = base.parent / f"{base.stem}{suffix}.png"
                    if self._save_view(it.path, it.ts_ns, view_dst, pv_text, save_txt, extra_meta):
                        n += 1
                    else:
                        errors += 1
            except Exception:
                errors += 1
            self.signals.progress.emit(done, total, it.path.name)
        self.signals.finished.emit(n, errors)

# ---------------- POINTING ANALYSIS ----------------
class PointingAnalysisSignals(QObject):
    progress = Signal(int, int)        # done, total
    finished = Signal(list)            # list of (ts_ns, cx_mm, cy_mm)
    cancelled = Signal()

class PointingAnalysisTask(QRunnable):
    def __init__(self, items, threshold, pixel_mm, signals):
        super().__init__()
        self.items = items
        self.threshold = threshold
        self.pixel_mm = pixel_mm
        self.signals = signals
        self._cancel = False

    def cancel(self): self._cancel = True

    @staticmethod
    def _process_one(item, threshold, pixel_mm):
        """Zpracuje jeden snímek — volá se z thread poolu."""
        try:
            from PIL import Image as PilImage
            pil_img = PilImage.open(str(item.path))
            w0, h0 = pil_img.size
            pil_img = pil_img.convert("I") if pil_img.mode in ("I", "I;16") else pil_img.convert("L")
            # Downscale max to 512px — enough for sub-pixel centroid accuracy,
            # fast enough for large batches. 100px was too coarse (24px grid).
            scale = max(w0, h0) / 512.0
            if scale > 1.0:
                new_w = max(1, int(w0 / scale))
                new_h = max(1, int(h0 / scale))
                pil_img = pil_img.resize((new_w, new_h), PilImage.Resampling.BILINEAR)
            arr = np.asarray(pil_img, dtype=np.float32).copy()
            w, h = arr.shape[1], arr.shape[0]
        except Exception:
            return None

        if w <= 0 or h <= 0: return None

        arr_max = arr.max()
        if arr_max <= 0: return None

        # Background: median of corner regions (5% of each dimension)
        bx = max(1, w // 20); by = max(1, h // 20)
        corners = np.concatenate([
            arr[:by, :bx].ravel(), arr[:by, -bx:].ravel(),
            arr[-by:, :bx].ravel(), arr[-by:, -bx:].ravel(),
        ])
        bg = float(np.median(corners))
        arr = np.clip(arr - bg, 0, None)
        if arr.max() <= 0: return None

        # Threshold is expressed as fraction of post-background-subtracted peak
        # (0–100 in UI = 0–100% of peak). This is scale-invariant for any bit depth.
        thr_abs = arr.max() * (threshold / 100.0)
        if thr_abs <= 0 or arr.max() < thr_abs:
            return None
        arr[arr < thr_abs] = 0

        irradiance = arr.sum()
        if irradiance < 1.0: return None

        xs = np.arange(w, dtype=np.float32)
        ys = np.arange(h, dtype=np.float32)
        cx_px = float(arr.sum(axis=0) @ xs) / irradiance
        cy_px = float(arr.sum(axis=1) @ ys) / irradiance

        # Centroid relative to image center (0,0 = image centre)
        cx_px -= w / 2.0
        cy_px -= h / 2.0

        # Scale back to original image pixels
        scale_x = w0 / w
        scale_y = h0 / h
        return (item.ts_ns, cx_px * scale_x, cy_px * scale_y, w0, h0)

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(self.items)
        step = max(1, total // 6000)

        indices = list(range(0, total, step))
        n_to_process = len(indices)
        sampled_items = [self.items[i] for i in indices]

        results_map = {}
        done_count = 0

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {
                executor.submit(
                    self._process_one, item, self.threshold, self.pixel_mm
                ): idx
                for idx, item in enumerate(sampled_items)
            }
            for future in as_completed(futures):
                if self._cancel:
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.signals.cancelled.emit()
                    return
                idx = futures[future]
                result = future.result()
                if result is not None:
                    results_map[idx] = result
                done_count += 1
                if done_count % 20 == 0 or done_count == n_to_process:
                    self.signals.progress.emit(done_count, n_to_process)

        # Seřaď podle původního pořadí
        results = [results_map[i] for i in sorted(results_map.keys())]
        self.signals.finished.emit(results)


class _SCHistogramWidget(QWidget):
    """
    Matplotlib-based histogram widget. Click or drag threshold line to set threshold.
    Emits threshold_changed(int).
    """
    threshold_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts:    "np.ndarray | None" = None
        self._bin_edges: "np.ndarray | None" = None   # len = len(counts)+1
        self._bit_depth: int   = 65535
        self._threshold: int   = 0
        self._lo: float        = 0.0
        self._hi: float        = 65535.0
        self._log_scale: bool  = True
        self._error_msg: "str | None" = None
        self._dragging:  bool  = False

        self.setMinimumSize(420, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        if _MPL_OK:
            self._fig    = plt.Figure(figsize=(5, 2.5), dpi=90, facecolor="#f3f3f3")
            self._canvas = FigureCanvas(self._fig)
            self._canvas.setParent(self)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self._canvas)
            self._canvas.mpl_connect("button_press_event",   self._on_mpl_press)
            self._canvas.mpl_connect("motion_notify_event",  self._on_mpl_motion)
            self._canvas.mpl_connect("button_release_event", self._on_mpl_release)
            self._vline = None   # threshold axvline handle
        else:
            self._canvas = None

    # ── public API ────────────────────────────────────────────────────────────
    def load_data(self, counts: "np.ndarray", bit_depth: int, threshold: int,
                  lo: float = 0.0, hi: float = -1.0,
                  bin_edges: "np.ndarray | None" = None):
        self._counts    = np.asarray(counts, dtype=np.float64).copy()
        self._bin_edges = np.asarray(bin_edges) if bin_edges is not None else None
        self._bit_depth = max(int(bit_depth), 1)
        self._threshold = max(0, min(int(threshold), self._bit_depth))
        self._lo = float(lo)
        self._hi = float(hi) if hi >= 0 else float(self._bit_depth)
        self._error_msg = None
        self._redraw()

    def set_error(self, msg: str):
        self._error_msg = msg
        self._counts = None
        self._redraw()

    def set_threshold(self, thr: int):
        self._threshold = max(0, min(int(thr), self._bit_depth))
        self._update_vline()

    def set_log_scale(self, on: bool):
        self._log_scale = on
        self._redraw()

    # ── matplotlib drawing ────────────────────────────────────────────────────
    def _redraw(self):
        if not _MPL_OK or self._canvas is None:
            return
        self._fig.clear()
        self._vline = None
        ax = self._fig.add_axes([0.10, 0.14, 0.86, 0.76])
        ax.set_facecolor("#ffffff")
        ax.tick_params(labelsize=8)

        if self._error_msg or self._counts is None:
            msg = self._error_msg or "Loading…"
            ax.text(0.5, 0.5, msg, ha="center", va="center",
                    transform=ax.transAxes, fontsize=9,
                    color="#cc0000" if self._error_msg else "#888888")
            self._canvas.draw_idle()
            return

        counts = self._counts
        n = len(counts)
        # Build bin centres for bar positions
        if self._bin_edges is not None and len(self._bin_edges) == n + 1:
            centres = (self._bin_edges[:-1] + self._bin_edges[1:]) / 2
            widths  = np.diff(self._bin_edges)
        else:
            span    = max(self._hi - self._lo, 1.0)
            bw      = span / n
            centres = self._lo + (np.arange(n) + 0.5) * bw
            widths  = np.full(n, bw)

        thr = float(self._threshold)
        colors = np.where(centres < thr, "#e09090", "#4a90d9")

        if self._log_scale:
            ax.set_yscale("log")
            # bar needs positive values — mask zeros
            mask = counts > 0
            if mask.any():
                ax.bar(centres[mask], counts[mask], width=widths[mask],
                       color=colors[mask], linewidth=0, align="center")
        else:
            ax.bar(centres, counts, width=widths, color=colors,
                   linewidth=0, align="center")

        # Use actual bin range for xlim so all bars are fully visible
        x_lo = float(centres[0]  - widths[0]  / 2)
        x_hi = float(centres[-1] + widths[-1] / 2)
        ax.set_xlim(x_lo, x_hi)
        ax.set_xlabel("Pixel intensity", fontsize=8)
        ax.set_ylabel("log count" if self._log_scale else "count", fontsize=8)

        self._vline = ax.axvline(thr, color="#cc0000", linewidth=1.5, zorder=5)
        ax.set_title(f"Threshold: {int(thr)}", fontsize=8, pad=2)
        self._canvas.draw_idle()

    def _update_vline(self):
        """Move threshold line without full redraw."""
        if not _MPL_OK or self._canvas is None or self._vline is None:
            self._redraw()
            return
        self._vline.set_xdata([float(self._threshold), float(self._threshold)])
        axes = self._fig.get_axes()
        if axes:
            axes[0].set_title(f"Threshold: {self._threshold}", fontsize=8, pad=2)
            # Re-color bars below/above threshold
            thr = float(self._threshold)
            for patch in axes[0].patches:
                cx = patch.get_x() + patch.get_width() / 2
                patch.set_facecolor("#e09090" if cx < thr else "#4a90d9")
        self._canvas.draw_idle()

    # ── mouse → threshold ─────────────────────────────────────────────────────
    def _axes_x_to_val(self, event) -> "int | None":
        if not _MPL_OK or self._canvas is None:
            return None
        axes = self._fig.get_axes()
        if not axes or event.inaxes is not axes[0]:
            return None
        val = int(round(float(event.xdata)))
        val = max(0, min(val, self._bit_depth))
        return val

    def _on_mpl_press(self, event):
        if event.button != 1:
            return
        val = self._axes_x_to_val(event)
        if val is not None:
            self._dragging = True
            self._set_thr(val)

    def _on_mpl_motion(self, event):
        if not self._dragging:
            return
        val = self._axes_x_to_val(event)
        if val is not None:
            self._set_thr(val)

    def _on_mpl_release(self, event):
        self._dragging = False

    def _set_thr(self, val: int):
        if val != self._threshold:
            self._threshold = val
            self._update_vline()
            self.threshold_changed.emit(val)


class _SCHistogramDialog(QDialog):
    """
    Popup dialog: shows a histogram for the current image,
    lets the user click/drag to set a threshold, previews the
    SC measurement in real time, and emits the accepted value.
    """
    threshold_accepted = Signal(int)
    threshold_preview  = Signal(int)   # debounced, for live SC recomputation

    # signals used to ferry results from background thread to main thread
    _sig_hist_ready = Signal(list, int, float, float, list)  # (counts, bit_depth, lo, hi, bin_edges)
    _sig_hist_error = Signal(str)

    def __init__(self, img_path: "Path", current_threshold: int,
                 bit_depth: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Threshold — Histogram")
        self.setMinimumSize(500, 300)
        self.resize(580, 340)

        # ── state ─────────────────────────────────────────────────────────────
        self._img_path  = img_path
        self._threshold = int(current_threshold)
        self._bit_depth = int(bit_depth)

        # ── layout ────────────────────────────────────────────────────────────
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # top row: status text  +  Log scale checkbox
        top = QHBoxLayout()
        self._info = QLabel("Loading histogram…")
        self._info.setStyleSheet("font-size: 10px; color: #555;")
        top.addWidget(self._info, 1)
        from PySide6.QtWidgets import QCheckBox
        self._log_cb = QCheckBox("Log scale")
        self._log_cb.setChecked(True)
        self._log_cb.setStyleSheet("font-size: 10px;")
        self._log_cb.toggled.connect(self._on_log_toggled)
        top.addWidget(self._log_cb)
        lay.addLayout(top)

        # histogram canvas
        self._hw = _SCHistogramWidget()
        # pre-configure bit_depth so the widget draws the threshold in the right
        # position even before the background worker finishes
        self._hw._bit_depth = self._bit_depth
        self._hw.set_threshold(self._threshold)
        self._hw.threshold_changed.connect(self._on_canvas_threshold)
        lay.addWidget(self._hw, 1)

        # spinbox row
        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel("Threshold:"))
        self._spin = QSpinBox()
        self._spin.setRange(0, self._bit_depth)
        self._spin.setValue(self._threshold)
        self._spin.setFixedWidth(90)
        self._spin.valueChanged.connect(self._on_spin_threshold)
        spin_row.addWidget(self._spin)
        spin_row.addStretch(1)
        lay.addLayout(spin_row)

        # buttons
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_apply.setDefault(True)
        btn_apply.setStyleSheet(
            "QPushButton{background:#2d7dff;color:#fff;font-weight:700;"
            "border-radius:3px;padding:4px 18px;}"
            "QPushButton:hover{background:#1a6aee;}")
        btn_apply.clicked.connect(self._on_apply)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_apply)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)

        # wire cross-thread signals (must be done before thread starts)
        self._sig_hist_ready.connect(self._on_hist_ready)
        self._sig_hist_error.connect(self._on_hist_error)

        # start background worker
        import threading as _threading
        _threading.Thread(target=self._bg_load, daemon=True).start()

    # ── background worker ─────────────────────────────────────────────────────
    def _bg_load(self):
        try:
            from PIL import Image as _PIL
            img = _PIL.open(str(self._img_path))
            if img.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(img.convert("I"), dtype=np.float32)
            else:
                arr = np.asarray(img.convert("L"), dtype=np.float32)
            peak = float(arr.max())
            bd   = 65535 if peak > 255 else 255
            lo   = float(arr.min())
            hi   = float(peak)
            # Use 256 bins over the actual data range for good visual resolution
            span = max(hi - lo, 1.0)
            bins = np.linspace(lo, hi + 1, 257)
            counts, bin_edges = np.histogram(arr.ravel(), bins=bins)
            self._sig_hist_ready.emit(counts.tolist(), int(bd),
                                      float(lo), float(hi), bin_edges.tolist())
        except Exception as exc:
            self._sig_hist_error.emit(str(exc))

    # ── slots (main thread) ───────────────────────────────────────────────────
    def _on_hist_ready(self, counts_list: list, bd: int, lo: float, hi: float,
                       bin_edges_list: list):
        self._bit_depth = bd
        # update spin range first so setValue doesn't clamp
        self._spin.blockSignals(True)
        self._spin.setRange(0, bd)
        self._spin.setValue(self._threshold)
        self._spin.blockSignals(False)
        counts    = np.array(counts_list,    dtype=np.float64)
        bin_edges = np.array(bin_edges_list, dtype=np.float64) if bin_edges_list else None
        n_total = int(counts.sum())
        self._info.setText(
            f"{n_total:,} pixels  ·  {bd}-bit  ·  click or drag to set threshold")
        self._hw.load_data(counts, bd, self._threshold, lo, hi, bin_edges=bin_edges)

    def _on_hist_error(self, msg: str):
        self._info.setText(f"Could not load image: {msg}")
        self._hw.set_error(f"Could not load image:\n{msg}")

    def _on_log_toggled(self, on: bool):
        self._hw.set_log_scale(on)

    def _on_canvas_threshold(self, val: int):
        """Called when user drags the threshold line on the canvas."""
        self._threshold = val
        self._spin.blockSignals(True)
        self._spin.setValue(val)
        self._spin.blockSignals(False)
        self.threshold_preview.emit(val)

    def _on_spin_threshold(self, val: int):
        """Called when user edits the spinbox."""
        self._threshold = val
        self._hw.set_threshold(val)
        self.threshold_preview.emit(val)

    def _on_apply(self):
        self.threshold_accepted.emit(self._threshold)
        self.accept()


class _SCExclusionEditor(QDialog):
    """
    Modal dialog — user paints exclusion regions on the beam preview image.
    Accepts a QPixmap (the beam-mask preview) and returns a boolean numpy mask
    (True = excluded pixel) at the original image resolution.
    """
    exclusion_confirmed = Signal(object)   # np.ndarray bool mask, original resolution

    _DEFAULT_HOT_PCT = 99.0

    def __init__(self, preview_pm: "QPixmap", orig_shape: "tuple[int,int]",
                 img_path: "Path | None" = None, existing_mask: "np.ndarray | None" = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exclusion editor — left-drag: paint   right-drag: erase")
        self.setMinimumSize(1200, 1000)
        self._orig_h, self._orig_w = orig_shape
        self._orig_pm = preview_pm
        self._img_path = img_path
        self._existing_mask = existing_mask
        self._brush_size = 20

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Single top toolbar with all controls ─────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel("Brush:"))
        self._brush_sb = QSpinBox()
        self._brush_sb.setRange(1, 400)
        self._brush_sb.setValue(self._brush_size)
        self._brush_sb.setFixedWidth(65)
        self._brush_sb.valueChanged.connect(self._on_brush_changed)
        toolbar.addWidget(self._brush_sb)

        btn_clear = QPushButton("⌫ Clear all")
        btn_clear.setToolTip("Remove all exclusion regions")
        btn_clear.clicked.connect(self._clear)
        toolbar.addWidget(btn_clear)

        toolbar.addSpacing(12)
        btn_auto = QPushButton("⚡ Auto-detect")
        btn_auto.setToolTip("Mark pixels above the threshold percentage of sensor max value")
        btn_auto.clicked.connect(self._auto_detect_hot)
        toolbar.addWidget(btn_auto)

        toolbar.addWidget(QLabel("Threshold:"))
        self._hot_pct_sb = QDoubleSpinBox()
        self._hot_pct_sb.setRange(1.0, 100.0)
        self._hot_pct_sb.setSingleStep(0.5)
        self._hot_pct_sb.setDecimals(1)
        self._hot_pct_sb.setValue(self._DEFAULT_HOT_PCT)
        self._hot_pct_sb.setSuffix(" %")
        self._hot_pct_sb.setFixedWidth(80)
        self._hot_pct_sb.setToolTip("Percentage of sensor max — pixels above this are marked as hotspots")
        toolbar.addWidget(self._hot_pct_sb)

        btn_reset_pct = QPushButton("↺")
        btn_reset_pct.setToolTip(f"Reset threshold to default ({self._DEFAULT_HOT_PCT:.0f} %)")
        btn_reset_pct.setFixedWidth(28)
        btn_reset_pct.clicked.connect(lambda: self._hot_pct_sb.setValue(self._DEFAULT_HOT_PCT))
        toolbar.addWidget(btn_reset_pct)

        toolbar.addSpacing(20)
        lbl_hint = QLabel("Left-drag: paint exclusion   ·   Right-drag: erase")
        lbl_hint.setStyleSheet("font-size: 11px; color: #555;")
        toolbar.addWidget(lbl_hint)

        toolbar.addStretch(1)

        btn_ok = QPushButton("✓ Apply")
        btn_ok.setDefault(False)
        btn_ok.setAutoDefault(False)
        btn_ok.setStyleSheet("QPushButton { font-weight: 700; padding: 4px 16px; }")
        btn_ok.clicked.connect(self._confirm)
        toolbar.addWidget(btn_ok)

        btn_cancel = QPushButton("✕ Cancel")
        btn_cancel.setDefault(False)
        btn_cancel.setAutoDefault(False)
        btn_cancel.clicked.connect(self.reject)
        toolbar.addWidget(btn_cancel)

        layout.addLayout(toolbar)

        # ── Canvas fills the rest ─────────────────────────────────────────
        self._canvas = _SCExclusionCanvas(preview_pm, parent=self)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._canvas, 1)

        self._brush_sb.valueChanged.connect(lambda v: setattr(self._canvas, '_brush_r', max(1, v // 2)))

        # Pre-load existing mask after the canvas is shown (needs display rect)
        if existing_mask is not None:
            from PySide6.QtCore import QTimer as _QT
            _QT.singleShot(50, self._preload_existing_mask)

    def _on_brush_changed(self, v: int):
        self._canvas._brush_r = max(1, v // 2)

    def _clear(self):
        self._canvas.clear_mask()

    def _preload_existing_mask(self):
        """Load the existing mask into the canvas at display resolution."""
        if self._existing_mask is None:
            return
        from PIL import Image as _PIL
        ir = self._canvas._img_rect()
        disp_w, disp_h = ir.width(), ir.height()
        if disp_w <= 0 or disp_h <= 0:
            return
        self._canvas._ensure_mask(disp_w, disp_h)
        pil_mask = _PIL.fromarray(self._existing_mask.astype(np.uint8) * 255, mode="L")
        pil_mask = pil_mask.resize((disp_w, disp_h), _PIL.NEAREST)
        self._canvas._mask_arr = np.asarray(pil_mask) > 127
        self._canvas.update()

    def _auto_detect_hot(self):
        """Mark pixels above threshold % of sensor max value as excluded."""
        if self._img_path is None:
            return
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(self._img_path))
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32)
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32)
        except Exception:
            return

        # Determine bit depth from actual max value in image
        arr_max = float(arr.max())
        bit_depth = 65535.0 if arr_max > 255 else 255.0
        pct = self._hot_pct_sb.value() / 100.0
        thr_val = bit_depth * pct
        hot_mask_orig = arr >= thr_val   # H_orig × W_orig bool

        # Resize hot mask to display resolution and merge into canvas mask
        from PIL import Image as _PIL2
        ir = self._canvas._img_rect()
        disp_w, disp_h = ir.width(), ir.height()
        self._canvas._ensure_mask(disp_w, disp_h)

        pil_hot = _PIL2.fromarray(hot_mask_orig.astype(np.uint8) * 255, "L")
        pil_hot = pil_hot.resize((disp_w, disp_h), _PIL2.NEAREST)
        hot_disp = np.asarray(pil_hot) > 127

        self._canvas._mask_arr = hot_disp.copy()
        self._canvas.update()

    def _confirm(self):
        # Scale the display-resolution mask to original image resolution
        disp_mask = self._canvas.get_mask()   # H_disp × W_disp bool
        if disp_mask is None or not disp_mask.any():
            self.exclusion_confirmed.emit(None)
        else:
            from PIL import Image as _PIL
            # Resize mask to original resolution using nearest-neighbour
            pil_mask = _PIL.fromarray(disp_mask.astype(np.uint8) * 255, mode="L")
            pil_mask = pil_mask.resize((self._orig_w, self._orig_h), _PIL.NEAREST)
            orig_mask = np.asarray(pil_mask) > 127
            self.exclusion_confirmed.emit(orig_mask)
        self.accept()


class _SCExclusionCanvas(QWidget):
    """Canvas widget inside the exclusion editor — renders preview + painted mask."""

    def __init__(self, preview_pm: "QPixmap", parent=None):
        super().__init__(parent)
        self._preview_pm = preview_pm
        self._brush_r = 10
        self._painting = False
        self._erase    = False
        self._last_pos: "QPoint | None" = None
        # Mask stored at display resolution (updated on resize)
        self._disp_w = 0
        self._disp_h = 0
        self._mask_arr: "np.ndarray | None" = None   # bool H×W at display res
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _ensure_mask(self, w: int, h: int):
        if self._mask_arr is None or self._disp_w != w or self._disp_h != h:
            # Scale existing mask if we already have one
            if self._mask_arr is not None and self._mask_arr.any():
                from PIL import Image as _PIL
                pil = _PIL.fromarray(self._mask_arr.astype(np.uint8) * 255, "L")
                pil = pil.resize((w, h), _PIL.NEAREST)
                self._mask_arr = np.asarray(pil) > 127
            else:
                self._mask_arr = np.zeros((h, w), dtype=bool)
            self._disp_w = w
            self._disp_h = h

    def clear_mask(self):
        if self._mask_arr is not None:
            self._mask_arr[:] = False
        self.update()

    def get_mask(self) -> "np.ndarray | None":
        return self._mask_arr

    def _img_rect(self) -> "QRect":
        """Return the rect where the preview image is drawn (aspect-ratio fitted)."""
        pm = self._preview_pm
        w, h = self.width(), self.height()
        scale = min(w / pm.width(), h / pm.height())
        iw = int(pm.width()  * scale)
        ih = int(pm.height() * scale)
        x0 = (w - iw) // 2
        y0 = (h - ih) // 2
        from PySide6.QtCore import QRect
        return QRect(x0, y0, iw, ih)

    def _canvas_to_mask(self, pos: "QPoint") -> "tuple[int,int]":
        """Convert widget coords to mask array (col, row). Mask lives at ir resolution."""
        ir = self._img_rect()
        col = int((pos.x() - ir.x()))
        row = int((pos.y() - ir.y()))
        col = max(0, min(col, self._disp_w - 1))
        row = max(0, min(row, self._disp_h - 1))
        return col, row

    def _paint_circle(self, pos: "QPoint", erase: bool):
        ir = self._img_rect()
        # Mask is stored at display (ir) resolution — 1 mask pixel = 1 screen pixel
        self._ensure_mask(ir.width(), ir.height())
        col, row = self._canvas_to_mask(pos)
        rr = max(1, self._brush_r)
        y0 = max(0, row - rr); y1 = min(self._disp_h, row + rr + 1)
        x0 = max(0, col - rr); x1 = min(self._disp_w, col + rr + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        circle = (yy - row)**2 + (xx - col)**2 <= rr**2
        self._mask_arr[y0:y1, x0:x1][circle] = not erase
        self.update()

    def mousePressEvent(self, event):
        self._painting = True
        self._erase = (event.button() == Qt.MouseButton.RightButton)
        self._last_pos = event.position().toPoint()
        self._paint_circle(self._last_pos, self._erase)

    def mouseMoveEvent(self, event):
        if not self._painting:
            return
        pos = event.position().toPoint()
        self._paint_circle(pos, self._erase)
        self._last_pos = pos

    def mouseReleaseEvent(self, event):
        self._painting = False

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111"))
        ir = self._img_rect()
        scaled = self._preview_pm.scaled(
            ir.width(), ir.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(ir.x(), ir.y(), scaled)

        # Draw exclusion mask as semi-transparent red overlay
        if self._mask_arr is not None and self._mask_arr.any():
            from PySide6.QtGui import QImage
            h, w = self._mask_arr.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[self._mask_arr, 0] = 220
            rgba[self._mask_arr, 3] = 80    # semi-transparent red (low alpha so image is visible)
            qi = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            pm_overlay = QPixmap.fromImage(qi).scaled(
                ir.width(), ir.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation)
            p.drawPixmap(ir.x(), ir.y(), pm_overlay)

        # Draw brush cursor
        if self._last_pos is not None:
            p.setPen(QColor(255, 255, 0, 180))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._last_pos, self._brush_r, self._brush_r)
        p.end()


class _SCValueLabel(QLabel):
    """Result value cell — double-click copies the text to clipboard."""
    def mouseDoubleClickEvent(self, event):
        txt = self.text()
        if txt and txt != "—":
            QApplication.clipboard().setText(txt)
            orig = self.styleSheet()
            self.setStyleSheet(orig.replace("background: #f5f5f5", "background: #b3e5fc")
                                   .replace("background: #e8f0fe", "background: #b3e5fc"))
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, lambda: self.setStyleSheet(orig))
        super().mouseDoubleClickEvent(event)


class _SCPreviewLabel(QLabel):
    """Compact preview in the left panel; shows full-size popup on mouse hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #111; border: 1px solid #444; border-radius: 2px;")
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self._popup: "QLabel | None" = None

    def set_full_pixmap(self, pm: "QPixmap"):
        """Store full-res pixmap and update thumbnail."""
        self._full_pm = pm
        self._refresh_thumb()

    def _refresh_thumb(self):
        if not hasattr(self, "_full_pm") or self._full_pm is None:
            return
        scaled = self._full_pm.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_thumb()

    def enterEvent(self, event):
        if not hasattr(self, "_full_pm") or self._full_pm is None:
            return
        if self._popup is not None:
            self._popup.close()
        # Find the image view area to size the popup
        app = QApplication.instance()
        screen = app.primaryScreen().availableGeometry() if app else None

        pm = self._full_pm
        max_w = (screen.width()  * 3 // 4) if screen else 1200
        max_h = (screen.height() * 3 // 4) if screen else 800
        scaled = pm.scaled(max_w, max_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)

        popup = QLabel(None)
        popup.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        popup.setPixmap(scaled)
        popup.resize(scaled.width(), scaled.height())
        popup.setStyleSheet("background: #111; border: 2px solid #555;")

        # Position: center on screen
        if screen:
            x = screen.x() + (screen.width()  - scaled.width())  // 2
            y = screen.y() + (screen.height() - scaled.height()) // 2
            popup.move(x, y)

        popup.show()
        self._popup = popup

    def leaveEvent(self, event):
        if self._popup is not None:
            self._popup.close()
            self._popup = None

    def hideEvent(self, event):
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        super().hideEvent(event)


class _SCSignals(QObject):
    finished  = Signal(object)   # dict with results, or None on error
    preview   = Signal(object)   # QPixmap preview of mask

class _SCTask(QRunnable):
    """Compute Spatial Contrast for the current frame on a background thread."""
    def __init__(self, img_path: "Path", threshold: int, signals: "_SCSignals",
                 exclusion_mask: "np.ndarray | None" = None):
        super().__init__()
        self.setAutoDelete(True)
        self._path           = img_path
        self._threshold      = threshold
        self._signals        = signals
        self._exclusion_mask = exclusion_mask  # bool H×W, True = excluded

    @staticmethod
    def _make_preview_pixmap(arr_8bit: "np.ndarray", mask: "np.ndarray") -> "QPixmap":
        """Create an RGBA preview: beam region = original gray, background = dim red tint."""
        from PySide6.QtGui import QImage, QPixmap
        h, w = arr_8bit.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        # beam pixels: white-ish
        rgba[mask,  0] = arr_8bit[mask]
        rgba[mask,  1] = arr_8bit[mask]
        rgba[mask,  2] = arr_8bit[mask]
        rgba[mask,  3] = 255
        # background: dark red tint
        rgba[~mask, 0] = np.minimum(arr_8bit[~mask].astype(np.uint16) + 80, 255).astype(np.uint8)
        rgba[~mask, 1] = (arr_8bit[~mask] * 0.3).astype(np.uint8)
        rgba[~mask, 2] = (arr_8bit[~mask] * 0.3).astype(np.uint8)
        rgba[~mask, 3] = 255
        qi = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        return QPixmap.fromImage(qi)

    def run(self):
        try:
            from PIL import Image as _PIL
        except ImportError:
            self._signals.finished.emit(None)
            return
        try:
            pil = _PIL.open(str(self._path))
            # Keep full bit depth: 16-bit → int32 array, 8-bit → uint8
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32).copy()
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32).copy()
        except Exception:
            self._signals.finished.emit(None)
            return

        if arr.size == 0:
            self._signals.finished.emit(None)
            return

        arr_max = float(arr.max())
        bit_depth = 65535.0 if arr_max > 255 else 255.0

        # Threshold is in raw pixel units (0–65535 for 16-bit, 0–255 for 8-bit)
        thr = float(self._threshold)
        mask = arr > thr

        # Fill holes so that dark regions INSIDE the beam boundary are included.
        # Strategy: closing (dilate→fill_holes→erode) ensures the beam edge is
        # connected before filling, so even beams with thin dark gaps get filled.
        try:
            from scipy.ndimage import binary_fill_holes, binary_dilation, binary_erosion
            # Closing radius — large enough to bridge typical interference gaps
            struct = np.ones((7, 7), dtype=bool)
            mask_closed = binary_dilation(mask, structure=struct)
            mask_closed = binary_fill_holes(mask_closed)
            mask_closed = binary_erosion(mask_closed, structure=struct)
            # Combine: original threshold mask OR the filled interior
            mask = mask | mask_closed
        except ImportError:
            pass  # scipy not available — threshold-only mask

        # Apply user-drawn exclusion regions
        n_excluded = 0
        if self._exclusion_mask is not None:
            excl = self._exclusion_mask
            # Resize exclusion mask to image resolution if needed
            if excl.shape != arr.shape:
                from PIL import Image as _PIL2
                pil_excl = _PIL2.fromarray(excl.astype(np.uint8) * 255, "L")
                pil_excl = pil_excl.resize((arr.shape[1], arr.shape[0]), _PIL2.NEAREST)
                excl = np.asarray(pil_excl) > 127
            n_excluded = int(excl.sum())
            mask = mask & ~excl

        # Pixels available for measurement (excluded zones are absent, not zero)
        n_valid = arr.size - n_excluded

        # For preview, normalise to 8-bit for display only
        arr_8bit = np.clip(arr / bit_depth * 255.0, 0, 255).astype(np.uint8)
        self._signals.preview.emit(self._make_preview_pixmap(arr_8bit, mask))

        beam = arr[mask]
        if beam.size == 0:
            self._signals.finished.emit({
                "error": "No pixels above threshold — lower the threshold.",
                "mean": None, "min": None, "max": None,
                "sc": None, "n_beam": 0, "n_total": n_valid,
                "threshold": self._threshold,
                "bit_depth": int(bit_depth),
            })
            return

        mean_val = float(beam.mean())
        min_val  = float(beam.min())
        max_val  = float(beam.max())
        sc       = max_val / mean_val if mean_val > 0 else float("inf")

        # Top-N pixel coordinates (y, x) sorted by descending intensity in original array
        h_full, w_full = arr.shape
        ys_all, xs_all = np.where(mask)
        if ys_all.size > 0:
            vals = arr[ys_all, xs_all]
            order = np.argsort(vals)[::-1]
            top_ys = ys_all[order].tolist()
            top_xs = xs_all[order].tolist()
        else:
            top_ys, top_xs = [], []

        self._signals.finished.emit({
            "error":      None,
            "mean":       mean_val,
            "min":        min_val,
            "max":        max_val,
            "sc":         sc,
            "n_beam":     int(mask.sum()),
            "n_total":    n_valid,
            "threshold":  self._threshold,
            "bit_depth":  int(bit_depth),
            "img_shape":  (h_full, w_full),
            "top_xs":     top_xs,
            "top_ys":     top_ys,
        })

    @staticmethod
    def otsu_threshold_raw(arr_raw: "np.ndarray", bit_depth: float) -> int:
        """Otsu's inter-class variance maximisation. Returns threshold in raw pixel units."""
        # Work on 8-bit projection for speed; rescale result back to raw units
        arr_8 = np.clip(arr_raw / bit_depth * 255.0, 0, 255).astype(np.uint8)
        hist, _ = np.histogram(arr_8.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(np.float64)
        total = hist.sum()
        if total == 0:
            return int(bit_depth * 0.04)  # ~2600 for 16-bit
        sum_b, w_b, max_var, thr_8 = 0.0, 0.0, 0.0, 0
        sum_total = float(np.dot(np.arange(256, dtype=np.float64), hist))
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            mb = sum_b / w_b
            mf = (sum_total - sum_b) / w_f
            var = w_b * w_f * (mb - mf) ** 2
            if var > max_var:
                max_var = var
                thr_8 = t
        return int(round(thr_8 / 255.0 * bit_depth))


class PointingPanel(QWidget):
    """Inline panel se scatter+hist grafy pointing stability."""
    point_clicked  = Signal(int)    # index into stored arrays
    region_deleted = Signal()       # emitted after points deleted

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(350)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if _MPL_OK:
            self._fig = plt.figure(figsize=(10, 5))
            self._canvas = FigureCanvas(self._fig)
            self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._canvas.setMouseTracking(True)
        else:
            self._fig = None
            self._canvas = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if _MPL_OK and self._canvas is not None:
            lay.addWidget(self._canvas)
        self.setVisible(False)
        self._cx = None
        self._cy = None
        self._ts = None         # float64 for path coloring
        self._ts_int = None     # int64 for timestamp lookups
        self._mask = None       # bool array; False = deleted
        self._replay_ts: int | None = None   # if set, only show points with ts <= this
        self._img_w = None
        self._img_h = None
        self._show_path = False
        self._select_mode = False
        self._rect_selector = None
        # User zoom/pan limits — kept across redraws so replay/point-deletion don't
        # snap the view back to full sensor range (None = follow the default fit).
        self._user_xlim = None
        self._user_ylim = None
        self._hover_annot = None  # kept for compat; actual tooltip is _qt_tooltip
        # Qt tooltip parented to the CANVAS (not the panel): a child of the canvas
        # always paints above the matplotlib drawing, so the timestamp can never end
        # up hidden behind the histogram axes (a sibling of the native canvas would).
        self._qt_tooltip = QLabel(self._canvas if self._canvas is not None else self)
        self._qt_tooltip.setStyleSheet(
            "background:#ffffcc; border:1px solid #888; border-radius:3px;"
            " padding:2px 6px; font-size:11px; color:#333;"
        )
        self._qt_tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._qt_tooltip.hide()
        # ── Time cursor on the beam-path colour bar ──────────────────────────
        # Draggable dot riding the Start→End gradient, with the timestamp shown
        # beside it. Both are Qt children of the canvas, NOT matplotlib artists,
        # so Save Plot writes the figure without them.
        self._cbar_ax = None        # colorbar axes of the current draw
        self._cbar_ts_min = None    # ns at the bottom of the gradient
        self._cbar_ts_max = None    # ns at the top of the gradient
        self._cbar_frac = 0.0       # cursor position: 0 = Start … 1 = End
        self._cbar_drag = False
        _host = self._canvas if self._canvas is not None else self
        self._cbar_handle = QLabel(_host)
        self._cbar_handle.setFixedSize(15, 15)
        self._cbar_handle.setStyleSheet(
            "background:#ffffff; border:2px solid #111; border-radius:7px;")
        self._cbar_handle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cbar_handle.hide()
        self._cbar_time_lbl = QLabel(_host)
        self._cbar_time_lbl.setStyleSheet(
            "background:#ffffff; border:1px solid #888; border-radius:3px;"
            " padding:1px 5px; font-size:10px; color:#111;")
        self._cbar_time_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._cbar_time_lbl.hide()
        if _MPL_OK and self._canvas is not None:
            self._canvas.installEventFilter(self)

    def plot(self, cx_urad, cy_urad, n_shots, ts_ns=None, ts_ns_int=None,
             img_w=None, img_h=None):
        if not _MPL_OK: return
        self._cx = cx_urad
        self._cy = cy_urad
        self._ts = ts_ns          # float64 for path coloring
        self._ts_int = ts_ns_int  # int64 for navigation
        self._img_w = img_w       # sensor width in pixels
        self._img_h = img_h       # sensor height in pixels
        self._mask = np.ones(len(cx_urad), dtype=bool)
        self._replay_ts = None    # reset replay on new data
        self._user_xlim = None    # new data → fresh view (drop any previous zoom)
        self._user_ylim = None
        self._select_mode = False
        self._rect_selector = None
        self._cbar_frac = 0.0     # time cursor back to Start
        self._draw()
        self.setVisible(True)

    def toggle_path(self):
        self._show_path = not self._show_path
        if self._cx is not None:
            self._draw()
        return self._show_path

    def _disarm_selector(self):
        if self._rect_selector is not None:
            try:
                self._rect_selector.set_active(False)
            except Exception:
                pass
            self._rect_selector = None

    def _arm_selector(self) -> bool:
        """(Re)create the rubber-band selector on the current scatter axes.
        Must be called again after every _draw() — fig.clear() destroys the
        axes the previous selector was bound to."""
        if not _MPL_OK:
            return False
        from matplotlib.widgets import RectangleSelector
        self._disarm_selector()
        ax_main = self._get_ax_main()
        if ax_main is None:
            return False
        # interactive=False: no leftover resize handles between drags — each
        # drag deletes immediately and the next drag starts fresh.
        self._rect_selector = RectangleSelector(
            ax_main, self._on_rect_selected,
            useblit=True, button=[1],
            minspanx=0, minspany=0,
            spancoords="data", interactive=False)
        return True

    def set_select_mode(self, on: bool) -> bool:
        """Persistent Delete mode: while on, every drag-rectangle deletes the
        points inside it immediately; the mode stays active until toggled off
        (or new data arrives). Returns the resulting mode state."""
        if not _MPL_OK or self._cx is None:
            self._select_mode = False
            self._disarm_selector()
            return False
        if on:
            self._select_mode = self._arm_selector()
        else:
            self._select_mode = False
            self._disarm_selector()
        return self._select_mode

    def toggle_select_mode(self):
        """Compat shim — flip Delete mode (see set_select_mode)."""
        return self.set_select_mode(not self._select_mode)

    def _on_rect_selected(self, eclick, erelease):
        """Delete points inside the rubber-band rectangle. Delete mode STAYS
        active — _draw() re-arms the selector on the rebuilt axes."""
        if self._cx is None or self._mask is None:
            return
        if eclick.xdata is None or erelease.xdata is None \
                or eclick.ydata is None or erelease.ydata is None:
            return
        x0, x1 = sorted([eclick.xdata, erelease.xdata])
        y0, y1 = sorted([eclick.ydata, erelease.ydata])
        cx = self._cx[self._mask]
        cy = self._cy[self._mask]
        # Build indices into original arrays for currently-visible points
        vis_indices = np.where(self._mask)[0]
        inside = (cx >= x0) & (cx <= x1) & (cy >= y0) & (cy <= y1)
        if not bool(inside.any()):
            return   # empty drag — nothing to delete, no redraw needed
        self._mask[vis_indices[inside]] = False
        self._draw()
        self.region_deleted.emit()

    def restore_all_points(self):
        """Un-delete all previously deleted points."""
        if self._mask is not None:
            self._mask[:] = True
            self._draw()
            self.region_deleted.emit()

    def set_replay_ts(self, ts_ns: "int | None"):
        """Limit visible points to those with timestamp <= ts_ns (None = show all)."""
        if not _MPL_OK or self._cx is None:
            return
        changed = self._replay_ts != ts_ns
        self._replay_ts = ts_ns
        if changed:
            self._draw()

    def _get_ax_main(self):
        """Return the scatter axes (always the second axes added, index 1)."""
        axes = self._fig.get_axes()
        return axes[1] if len(axes) > 1 else (axes[0] if axes else None)

    def _qt_pos_to_data(self, ax, qx: int, qy: int):
        """Convert Qt widget pixel coordinates to matplotlib data coordinates.
        Returns (xdata, ydata) or (None, None) if outside the axes."""
        try:
            fig_w = self._canvas.width()
            fig_h = self._canvas.height()
            if fig_w <= 0 or fig_h <= 0:
                return None, None
            # matplotlib uses bottom-left origin; Qt uses top-left
            fig_x = qx / fig_w
            fig_y = 1.0 - qy / fig_h
            bbox = ax.get_position()   # axes bbox in figure fraction [0,1]
            if not (bbox.x0 <= fig_x <= bbox.x1 and bbox.y0 <= fig_y <= bbox.y1):
                return None, None
            # Map figure fraction → data coordinates
            ax_x = (fig_x - bbox.x0) / (bbox.x1 - bbox.x0)
            ax_y = (fig_y - bbox.y0) / (bbox.y1 - bbox.y0)
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            xdata = xlim[0] + ax_x * (xlim[1] - xlim[0])
            ydata = ylim[0] + ax_y * (ylim[1] - ylim[0])
            return xdata, ydata
        except Exception:
            return None, None

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtCore import Qt as _Qt
        if obj is self._canvas and self._cx is not None:
            t = event.type()
            if t == QEvent.Type.MouseButtonPress:
                if (event.button() == _Qt.MouseButton.LeftButton
                        and self._cbar_hit(event.position().x(), event.position().y())):
                    self._cbar_drag = True
                    self._set_cbar_from_qt(event.position().y())
                    return True
                if event.button() == _Qt.MouseButton.MiddleButton:
                    self._pan_start = (event.position().x(), event.position().y())
                    self._pan_xlim = None
                    self._pan_ylim = None
                    ax = self._get_ax_main()
                    if ax:
                        self._pan_xlim = ax.get_xlim()
                        self._pan_ylim = ax.get_ylim()
                    return True
                else:
                    self._handle_qt_click(event.position().x(), event.position().y())
            elif t == QEvent.Type.MouseMove:
                if self._cbar_drag:
                    self._set_cbar_from_qt(event.position().y())
                    return True
                if hasattr(self, '_pan_start') and self._pan_start is not None:
                    self._handle_qt_pan(event.position().x(), event.position().y())
                else:
                    self._handle_qt_hover(event.position().x(), event.position().y())
            elif t == QEvent.Type.MouseButtonRelease:
                if self._cbar_drag:
                    self._cbar_drag = False
                    return True
                if event.button() == _Qt.MouseButton.MiddleButton:
                    self._pan_start = None
            elif t == QEvent.Type.Resize:
                self._update_cbar_marker()
            elif t == QEvent.Type.Wheel:
                self._handle_qt_zoom(event.position().x(), event.position().y(),
                                     event.angleDelta().y())
                return True
            elif t == QEvent.Type.MouseButtonDblClick:
                self._reset_zoom()
                return True
            elif t == QEvent.Type.Leave:
                self._clear_hover()
        return False

    def _handle_qt_pan(self, qx: float, qy: float):
        if not hasattr(self, '_pan_start') or self._pan_start is None:
            return
        ax = self._get_ax_main()
        if ax is None or self._pan_xlim is None:
            return
        dx_px = qx - self._pan_start[0]
        dy_px = qy - self._pan_start[1]
        fig_w = self._canvas.width()
        fig_h = self._canvas.height()
        bbox = ax.get_position()
        ax_w_px = bbox.width * fig_w
        ax_h_px = bbox.height * fig_h
        if ax_w_px <= 0 or ax_h_px <= 0:
            return
        x0, x1 = self._pan_xlim
        y0, y1 = self._pan_ylim
        xspan = x1 - x0
        yspan = y1 - y0
        shift_x = -dx_px / ax_w_px * xspan
        shift_y =  dy_px / ax_h_px * yspan  # dy inverted: Qt Y down, data Y up
        ax.set_xlim(x0 + shift_x, x1 + shift_x)
        ax.set_ylim(y0 + shift_y, y1 + shift_y)
        # Remember the view so replay/point-deletion redraws keep it (Task 6).
        self._user_xlim = ax.get_xlim()
        self._user_ylim = ax.get_ylim()
        self._canvas.draw_idle()

    def _handle_qt_zoom(self, qx: float, qy: float, delta: int):
        ax = self._get_ax_main()
        if ax is None:
            return
        xdata, ydata = self._qt_pos_to_data(ax, qx, qy)
        if xdata is None:
            return
        factor = 0.85 if delta > 0 else 1.0 / 0.85
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        ax.set_xlim(xdata + (x0 - xdata) * factor, xdata + (x1 - xdata) * factor)
        ax.set_ylim(ydata + (y0 - ydata) * factor, ydata + (y1 - ydata) * factor)
        # Remember the view so replay/point-deletion redraws keep it (Task 6).
        self._user_xlim = ax.get_xlim()
        self._user_ylim = ax.get_ylim()
        self._canvas.draw_idle()

    def _fit_limits(self):
        """(xlim, ylim) framed tightly around the point cloud so a small cluster
        fills the plot instead of sitting as a dot in the full sensor frame.
        Uses all non-deleted points (independent of replay) so the view is stable
        while replaying. Returns (None, None) if there are no points."""
        if self._cx is None or self._mask is None:
            return None, None
        cx = self._cx[self._mask]; cy = self._cy[self._mask]
        if len(cx) == 0:
            return None, None
        xr = float(cx.max() - cx.min())
        yr = float(cy.max() - cy.min())
        pad = max(max(xr, yr) * 0.15, 2.0)   # ~15 % margin; floor avoids absurd zoom
        xlim = (float(cx.min()) - pad, float(cx.max()) + pad)
        ylim = (float(cy.max()) + pad, float(cy.min()) - pad)   # inverted Y (image coords)
        return xlim, ylim

    def _reset_zoom(self):
        ax = self._get_ax_main()
        if ax is None:
            return
        # Back to the default fit — forget any user zoom so redraws follow it again.
        self._user_xlim = None
        self._user_ylim = None
        xlim, ylim = self._fit_limits()
        if xlim is not None:
            ax.set_xlim(xlim); ax.set_ylim(ylim)
        elif self._img_w and self._img_h:
            ax.set_xlim(-self._img_w / 2, self._img_w / 2)
            ax.set_ylim(self._img_h / 2, -self._img_h / 2)
        self._canvas.draw_idle()

    def _handle_qt_click(self, qx: float, qy: float):
        if self._cx is None or self._mask is None or self._select_mode:
            return
        ax = self._get_ax_main()
        if ax is None:
            return
        xdata, ydata = self._qt_pos_to_data(ax, qx, qy)
        if xdata is None:
            return
        cx_vis = self._cx[self._mask]
        cy_vis = self._cy[self._mask]
        vis_indices = np.where(self._mask)[0]
        if len(cx_vis) == 0:
            return
        xlim = ax.get_xlim(); ylim = ax.get_ylim()
        xrange = max(abs(xlim[1] - xlim[0]), 1e-12)
        yrange = max(abs(ylim[1] - ylim[0]), 1e-12)
        dx = (cx_vis - xdata) / xrange
        dy = (cy_vis - ydata) / yrange
        dist2 = dx * dx + dy * dy
        nearest_vis = int(np.argmin(dist2))
        if dist2[nearest_vis] > 0.03 ** 2:
            return
        self.point_clicked.emit(int(vis_indices[nearest_vis]))

    def _handle_qt_hover(self, qx: float, qy: float):
        if self._cx is None or self._mask is None or self._ts_int is None:
            return
        ax = self._get_ax_main()
        if ax is None:
            return
        xdata, ydata = self._qt_pos_to_data(ax, qx, qy)
        mask = self._mask.copy()
        if self._replay_ts is not None:
            mask &= (self._ts_int <= self._replay_ts)
        cx_vis = self._cx[mask]
        cy_vis = self._cy[mask]
        vis_indices = np.where(mask)[0]
        need_draw = False
        if xdata is None or len(cx_vis) == 0:
            need_draw = self._clear_hover()
        else:
            xlim = ax.get_xlim(); ylim = ax.get_ylim()
            xrange = max(abs(xlim[1] - xlim[0]), 1e-12)
            yrange = max(abs(ylim[1] - ylim[0]), 1e-12)
            dx = (cx_vis - xdata) / xrange
            dy = (cy_vis - ydata) / yrange
            dist2 = dx * dx + dy * dy
            nearest_vis = int(np.argmin(dist2))
            if dist2[nearest_vis] <= 0.025 ** 2:
                orig_idx = int(vis_indices[nearest_vis])
                ts_ns = int(self._ts_int[orig_idx])
                ts_str = fmt_prague_full_from_ns(ts_ns)
                px, py = float(self._cx[orig_idx]), float(self._cy[orig_idx])
                # Position Qt tooltip widget (always above all matplotlib axes)
                try:
                    disp = ax.transData.transform((px, py))
                    cx_px = int(disp[0])
                    cy_px = int(self._canvas.height() - disp[1])
                except Exception:
                    cx_px, cy_px = int(qx), int(qy)
                self._qt_tooltip.setText(ts_str)
                self._qt_tooltip.adjustSize()
                host_w = self._canvas.width()  if self._canvas is not None else self.width()
                host_h = self._canvas.height() if self._canvas is not None else self.height()
                tip_x = cx_px + 12
                tip_y = cy_px - self._qt_tooltip.height() - 4
                tip_x = max(0, min(tip_x, host_w - self._qt_tooltip.width()))
                tip_y = max(0, min(tip_y, host_h - self._qt_tooltip.height()))
                self._qt_tooltip.move(tip_x, tip_y)
                self._qt_tooltip.show()
                self._qt_tooltip.raise_()
            else:
                need_draw = self._clear_hover()
        if need_draw:
            self._canvas.draw_idle()

    def _clear_hover(self) -> bool:
        if self._qt_tooltip.isVisible():
            self._qt_tooltip.hide()
            return False  # no canvas redraw needed
        return False

    # ── Time cursor on the beam-path colour bar ──────────────────────────────
    def _cbar_rect_px(self):
        """(x_left, y_top, x_right, y_bottom) of the colour bar in canvas
        pixels, or None when no colour bar is drawn."""
        if self._cbar_ax is None or self._canvas is None:
            return None
        try:
            bbox = self._cbar_ax.get_position()
        except Exception:
            return None
        w = self._canvas.width(); h = self._canvas.height()
        if w <= 0 or h <= 0:
            return None
        return (bbox.x0 * w, (1.0 - bbox.y1) * h,
                bbox.x1 * w, (1.0 - bbox.y0) * h)

    def _cbar_hit(self, qx: float, qy: float) -> bool:
        """True if the press is on (or right next to) the colour bar — the bar
        itself is only a few pixels wide, so the grab zone is padded."""
        rect = self._cbar_rect_px()
        if rect is None or self._cbar_ts_min is None:
            return False
        x0, y_top, x1, y_bot = rect
        return (x0 - 12 <= qx <= x1 + 12) and (y_top - 10 <= qy <= y_bot + 10)

    def _set_cbar_from_qt(self, qy: float):
        rect = self._cbar_rect_px()
        if rect is None:
            return
        _, y_top, _, y_bot = rect
        span = max(y_bot - y_top, 1e-6)
        frac = (y_bot - qy) / span        # bottom = Start, top = End
        self._cbar_frac = min(1.0, max(0.0, float(frac)))
        self._update_cbar_marker()

    def _update_cbar_marker(self):
        """Place the draggable dot on the gradient and the timestamp beside it."""
        rect = self._cbar_rect_px()
        if rect is None or self._cbar_ts_min is None:
            self._cbar_handle.hide()
            self._cbar_time_lbl.hide()
            return
        x0, y_top, x1, y_bot = rect
        mid_x = (x0 + x1) / 2.0
        cy = y_bot - self._cbar_frac * (y_bot - y_top)
        hw = self._cbar_handle.width(); hh = self._cbar_handle.height()
        self._cbar_handle.move(int(mid_x - hw / 2), int(cy - hh / 2))
        self._cbar_handle.show(); self._cbar_handle.raise_()

        ts = int(round(self._cbar_ts_min
                       + self._cbar_frac * (self._cbar_ts_max - self._cbar_ts_min)))
        # HH:MM only — it has to fit the narrow strip beside the colour bar.
        self._cbar_time_lbl.setText(fmt_prague_full_from_ns(ts)[11:16])
        self._cbar_time_lbl.adjustSize()
        host_w = self._canvas.width(); host_h = self._canvas.height()
        lw = self._cbar_time_lbl.width(); lh = self._cbar_time_lbl.height()
        lx = int(x1 + 8)
        if lx + lw > host_w:              # no room on the right → flip to the left
            lx = int(x0 - 8 - lw)
        lx = max(0, min(lx, host_w - lw))
        ly = max(0, min(int(cy - lh / 2), host_h - lh))
        self._cbar_time_lbl.move(lx, ly)
        self._cbar_time_lbl.show(); self._cbar_time_lbl.raise_()

    def _draw(self):
        if not _MPL_OK: return
        self._hover_annot = None
        self._qt_tooltip.hide()
        self._rect_selector = None   # fig.clear() kills its axes — never reuse
        self._fig.clear()
        self._render_to_fig(self._fig)
        self._canvas.draw()
        self._update_cbar_marker()
        if self._select_mode:
            # Delete mode persists across redraws (deletion, replay, restore)
            self._arm_selector()

    def _render_to_fig(self, fig):
        if fig is self._fig:
            self._cbar_ax = None      # re-established below when a colour bar is drawn
        mask = self._mask.copy() if self._mask is not None else np.ones(len(self._cx), dtype=bool)
        if self._replay_ts is not None and self._ts_int is not None:
            mask &= (self._ts_int <= self._replay_ts)
        cx_urad = self._cx[mask]
        cy_urad = self._cy[mask]
        # n_shots may be 0 (e.g. replay before the first visible point) — still
        # draw the empty axes so the graph frame is present from the start.
        n_shots = len(cx_urad)
        x_std = float(np.std(cx_urad)) if n_shots else 0.0
        y_std = float(np.std(cy_urad)) if n_shots else 0.0
        ts_masked = self._ts[mask] if self._ts is not None else None

        if self._show_path and ts_masked is not None:
            fig.subplots_adjust(
                left=0.08, right=0.96, top=0.93, bottom=0.10,
                wspace=0.45, hspace=0.08)
            ax_histx = fig.add_axes([0.08, 0.72, 0.36, 0.18])
            ax_main  = fig.add_axes([0.08, 0.10, 0.36, 0.60], sharex=ax_histx)
            ax_histy = fig.add_axes([0.45, 0.10, 0.07, 0.60], sharey=ax_main)
            ax_path  = fig.add_axes([0.58, 0.10, 0.34, 0.80])
        else:
            fig.subplots_adjust(
                left=0.10, right=0.97, top=0.93, bottom=0.10,
                wspace=0.08, hspace=0.08)
            ax_histx = fig.add_axes([0.10, 0.72, 0.70, 0.18])
            ax_main  = fig.add_axes([0.10, 0.10, 0.70, 0.60], sharex=ax_histx)
            ax_histy = fig.add_axes([0.81, 0.10, 0.16, 0.60], sharey=ax_main)
            ax_path  = None

        # Count only truly deleted points, not those hidden by the replay filter.
        n_deleted = int((~self._mask).sum()) if self._mask is not None else 0
        deleted_note = f"  [{n_deleted} deleted]" if n_deleted else ""

        # ── Scatter ──────────────────────────────────────────────
        ax_main.scatter(cx_urad, cy_urad, s=6, alpha=0.4, color="#2d7dff",
                        rasterized=True)
        ax_main.axhline(0, color="#aaa", linewidth=0.8, linestyle="--")
        ax_main.axvline(0, color="#aaa", linewidth=0.8, linestyle="--")
        ax_main.set_xlabel("X (px from center)", fontsize=8)
        ax_main.set_ylabel("Y (px from center)", fontsize=8)
        ax_main.tick_params(labelsize=7)
        # Default view: frame the actual point cloud (zoom to the data) so a tight
        # cluster fills the plot instead of being a dot in the full sensor frame.
        fit_xlim, fit_ylim = self._fit_limits()
        if fit_xlim is not None:
            ax_main.set_xlim(fit_xlim); ax_main.set_ylim(fit_ylim)
        elif self._img_w and self._img_h:
            ax_main.set_xlim(-self._img_w / 2, self._img_w / 2)
            ax_main.set_ylim(self._img_h / 2, -self._img_h / 2)  # inverted: +Y downward
        else:
            ax_main.invert_yaxis()
        # Keep the user's zoom/pan across redraws (replay, point deletion) — Task 6.
        if self._user_xlim is not None and self._user_ylim is not None:
            ax_main.set_xlim(self._user_xlim)
            ax_main.set_ylim(self._user_ylim)

        # ── Histogramy ───────────────────────────────────────────
        bins = min(32, max(8, n_shots // 10))
        ax_histx.hist(cx_urad, bins=bins, color="#2d7dff", alpha=0.7)
        ax_histy.hist(cy_urad, bins=bins, color="#2d7dff", alpha=0.7,
                      orientation="horizontal")
        ax_histx.tick_params(labelbottom=False, labelsize=7)
        ax_histy.tick_params(labelleft=False, labelsize=7)
        ax_histx.set_title(
            f"N={n_shots}  σX={x_std:.1f}  σY={y_std:.1f} px{deleted_note}",
            fontsize=8, pad=3)

        # ── Path ─────────────────────────────────────────────────
        if ax_path is not None and ts_masked is not None and n_shots > 0:
            from matplotlib.collections import LineCollection
            ts = ts_masked.astype(np.float64)
            t_norm = (ts - ts.min()) / max(float(ts.max() - ts.min()), 1.0)

            points = np.array([cx_urad, cy_urad]).T.reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)
            lc = LineCollection(segments, cmap="coolwarm", alpha=0.7,
                                linewidth=1.0, rasterized=True)
            lc.set_array(t_norm[:-1])
            ax_path.add_collection(lc)

            ax_path.scatter(cx_urad[0],  cy_urad[0],  s=50, color="blue",
                            zorder=5, label="Start", marker="o")
            ax_path.scatter(cx_urad[-1], cy_urad[-1], s=50, color="red",
                            zorder=5, label="End",   marker="X")

            pad_x = max(x_std * 0.5, 0.05)
            pad_y = max(y_std * 0.5, 0.05)
            ax_path.set_xlim(cx_urad.min() - pad_x, cx_urad.max() + pad_x)
            ax_path.set_ylim(cy_urad.max() + pad_y, cy_urad.min() - pad_y)  # inverted: positive Y = down
            ax_path.set_xlabel("X (px from center)", fontsize=8)
            ax_path.set_title("Beam path", fontsize=8, pad=3)
            ax_path.legend(fontsize=7, loc="upper right",
                           handlelength=1, borderpad=0.4)
            ax_path.axhline(0, color="#aaa", linewidth=0.8, linestyle="--")
            ax_path.axvline(0, color="#aaa", linewidth=0.8, linestyle="--")
            ax_path.tick_params(labelsize=7)

            sm = plt.cm.ScalarMappable(cmap="coolwarm",
                                        norm=plt.Normalize(0, 1))
            sm.set_array([])
            cbar = fig.colorbar(sm, ax=ax_path, fraction=0.06, pad=0.02)
            cbar.set_ticks([0, 0.5, 1])
            cbar.set_ticklabels(["Start", "Mid", "End"])
            cbar.ax.tick_params(labelsize=7)
            if fig is self._fig:
                # Anchor the draggable time cursor to this colour bar.
                ts_int = (self._ts_int[mask] if self._ts_int is not None
                          else ts.astype(np.int64))
                self._cbar_ax = cbar.ax
                self._cbar_ts_min = int(ts_int.min())
                self._cbar_ts_max = int(ts_int.max())

    def save_figure(self, path: str):
        if not _MPL_OK: return
        # Save the live figure as-is — preserves current zoom/pan state
        self._fig.savefig(path, dpi=200, bbox_inches="tight")

class WeekendDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        col = index.column()
        if col < 1:
            return
        # Zkus UserRole (aktuální měsíc)
        date = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(date, QDate) and date.isValid():
            if date.dayOfWeek() in (6, 7):
                option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
                option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))
            return
        # Fallback pro dny mimo měsíc — DisplayRole je string "1"–"31"
        # Spočítáme datum ze sloupce a aktuální stránky kalendáře
        # Nelze spolehlivě bez přístupu ke kalendáři, takže červeníme jen So/Ne sloupce
        # ALE pouze pokud locale má Po jako první den (ISO)
        # Bezpečnější fallback: zkontroluj DisplayRole text a sloupec
        # Qt ISO: col 1=Po,2=Út,3=St,4=Čt,5=Pá,6=So,7=Ne
        if col in (6, 7):
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#cc0000"))
            option.palette.setColor(option.palette.ColorRole.ButtonText, QColor("#cc0000"))

# ── MULTI-SELECT CALENDAR (house style: Monday-first, gray header, red weekends,
#    white cells) — same widget the Image Finder uses ──────────────────────────
_MS_CAL_STYLE = """
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
QCalendarWidget QSpinBox {
    color: #222; background: #eeeeee; border: none; font-weight: 700;
}
QCalendarWidget QMenu { color: #111; background: #fff; }
"""


class _MultiSelectDelegate(QStyledItemDelegate):
    """Paint calendar cells: selected days = blue fill, Sat/Sun = red text, the
    focused day = blue outline. initStyleOption strips State_Selected from every
    cell that is not in the selection, so Qt's own highlight never bleeds through
    and the painted days are exactly the ones the caller selected."""

    def __init__(self, cal: QCalendarWidget):
        super().__init__(cal)
        self._cal = cal
        self._selected_keys: set = set()     # (year, month, day)
        self._focus_key = None               # (year, month, day) | None

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

    def _repaint(self):
        view = self._cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
        if view is not None:
            view.viewport().update()

    def set_selected(self, dates: "list[QDate]"):
        self._selected_keys = {(d.year(), d.month(), d.day()) for d in dates}
        self._repaint()

    def set_focus_date(self, d: "QDate | None"):
        self._focus_key = None if d is None else (d.year(), d.month(), d.day())
        self._repaint()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        d = self._date_for_index(index)
        if d is not None and (d.year(), d.month(), d.day()) not in self._selected_keys:
            option.state = option.state & ~QStyle.StateFlag.State_Selected

    def paint(self, painter, option, index):
        d = self._date_for_index(index)
        if d is None:
            super().paint(painter, option, index)
            return
        key = (d.year(), d.month(), d.day())
        is_weekend = d.dayOfWeek() in (6, 7)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if key in self._selected_keys:
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
        if key == self._focus_key:
            painter.save()
            painter.setPen(QPen(QColor("#1565C0"), 2))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


def _make_multiselect_calendar(initial: "QDate | None" = None
                               ) -> "tuple[QFrame, QCalendarWidget]":
    """Return (wrapper_frame, cal) — one calendar with a gray day-name header, a
    light nav bar (month button + year spin) and the multi-select delegate
    installed. Selection is driven by the caller via cal._wk_delegate."""
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
    cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.NoHorizontalHeader)
    if initial:
        cal.setSelectedDate(initial)
    cal.setStyleSheet(_MS_CAL_STYLE)

    nav_internal = cal.findChild(QWidget, "qt_calendar_navigationbar")
    if nav_internal:
        nav_internal.hide()

    view = cal.findChild(QAbstractItemView, "qt_calendar_calendarview")
    if view is not None:
        cal._wk_delegate = _MultiSelectDelegate(cal)
        view.setItemDelegate(cal._wk_delegate)

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

    prev_btn = QToolButton(); prev_btn.setText("◀")
    prev_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")
    month_btn = QPushButton(); month_btn.setMinimumWidth(100)
    month_btn.setStyleSheet(
        "QPushButton { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; font-weight: bold; font-size: 12px; padding: 2px 10px; }"
        "QPushButton:hover { background: #e0e0e0; }")
    year_spin = QSpinBox()
    year_spin.setRange(2000, 2100)
    year_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    year_spin.setStyleSheet(
        "QSpinBox { border: 1px solid #aaa; border-radius: 3px; background: #f5f5f5;"
        " color: #111; padding: 1px 4px; font-weight: bold; font-size: 12px; }")
    year_spin.setFixedWidth(60)
    next_btn = QToolButton(); next_btn.setText("▶")
    next_btn.setStyleSheet("QToolButton { border: none; font-weight: bold; font-size: 18px; padding: 1px 6px; }"
                           "QToolButton:hover { background: #d0d0d0; border-radius: 3px; }")

    nav_lay.addWidget(prev_btn); nav_lay.addStretch()
    nav_lay.addWidget(month_btn); nav_lay.addWidget(year_spin)
    nav_lay.addStretch(); nav_lay.addWidget(next_btn)

    def _update_nav():
        month_btn.setText(_MONTHS[cal.monthShown() - 1])
        year_spin.blockSignals(True)
        year_spin.setValue(cal.yearShown())
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

    hdr_row = QWidget()
    hdr_row.setAutoFillBackground(True)
    hdr_pal = hdr_row.palette()
    hdr_pal.setColor(QPalette.ColorRole.Window, QColor("#bdbdbd"))
    hdr_row.setPalette(hdr_pal)
    hdr_lay = QHBoxLayout(hdr_row)
    hdr_lay.setContentsMargins(0, 0, 0, 0)
    hdr_lay.setSpacing(0)
    for i, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        lbl = QLabel(name)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        colour = "#cc0000" if i >= 5 else "#111111"
        lbl.setStyleSheet(f"color: {colour}; font-weight: 700; padding: 4px 0;")
        hdr_lay.addWidget(lbl, stretch=1)

    wrapper = QFrame()
    wrapper.setStyleSheet("QFrame { border: 1px solid #b0b0b0; border-radius: 3px; }")
    w_lay = QVBoxLayout(wrapper)
    w_lay.setContentsMargins(0, 0, 0, 0)
    w_lay.setSpacing(0)
    w_lay.addWidget(nav_row)
    w_lay.addWidget(hdr_row)
    w_lay.addWidget(cal)
    return wrapper, cal


def _hsep_dialog() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;")
    return f

# ---------------- DATE PICKER DIALOG ----------------
# One selected time window on one calendar day (Prague local wall time). The
# "to" time is the EXCLUSIVE end of the window, so a whole hour reads as an
# exact hour span — 12:00–13:00, not 12:00–12:59.
PickSeg = namedtuple("PickSeg", "date h_from m_from h_to m_to")

# Preset window for every multi-day selection (both multi-day modes) — the lab
# shift. Any single day can still be given its own window with the ⚙ editor.
_MULTIDAY_FROM = QTime(7, 0)
_MULTIDAY_TO   = QTime(21, 0)


def _seg_fields(seg) -> tuple:
    """Unpack a PickSeg — or a legacy (date, hour_from, hour_to) tuple."""
    if isinstance(seg, PickSeg):
        return seg.date, seg.h_from, seg.m_from, seg.h_to, seg.m_to
    d, hf, ht = seg
    return d, int(hf), 0, int(ht) + 1, 0


def hour_end_hm(hour: int) -> "tuple[int, int]":
    """Exclusive end of `hour` as (hour, minute) — the next whole hour.
    23 → 23:59, the closest a QTimeEdit can express to midnight; seg_bounds_ns
    stretches that back out to the next day's 00:00."""
    h = max(0, min(23, int(hour)))
    return (h + 1, 0) if h < 23 else (23, 59)


def seg_bounds_ns(seg) -> "tuple[int, int]":
    """[start_ns, end_ns) of a segment.

    The "to" time is the EXCLUSIVE end: 12:00–13:00 is exactly one hour and
    touches only the 12 h archive folder (see utc_hour_cells_for_window).
    A "to" of 23:59 means "to the end of the day" — a QTimeEdit cannot show
    24:00 — and is stretched to the next midnight."""
    d, hf, mf, ht, mt = _seg_fields(seg)
    if (ht, mt) == (23, 59):
        ht, mt = 24, 0
    midnight = datetime(d.year, d.month, d.day, tzinfo=TZ_PRAGUE)
    start = midnight + timedelta(hours=hf, minutes=mf)
    end   = midnight + timedelta(hours=ht, minutes=mt)
    if end <= start:
        end = start + timedelta(minutes=1)
    return ns_from_dt(start), ns_from_dt(end)


def utc_hour_cells_for_window(start_ns: int, end_ns: int) -> "list[tuple[int, int, int, int]]":
    """UTC (year, month, day, hour) folder coordinates covering [start_ns, end_ns).
    The archive tree is year/month/day/hour in UTC (see axis_from_hour_folder_exact),
    so a Prague-time window must be converted before folders are enumerated —
    Prague 00:30 lives in the PREVIOUS day's 22 or 23 folder."""
    from datetime import timezone as _tz
    if end_ns <= start_ns:
        end_ns = start_ns + 1
    # Integer seconds — ts/1e9 loses the last digits of a ns timestamp and would
    # round an exclusive end of 10:00:00.000000000 up into the next hour folder.
    cur = datetime.fromtimestamp(start_ns // 1_000_000_000, tz=_tz.utc).replace(
        minute=0, second=0, microsecond=0)
    last = datetime.fromtimestamp((end_ns - 1) // 1_000_000_000, tz=_tz.utc)
    out = []
    while cur <= last:
        out.append((cur.year, cur.month, cur.day, cur.hour))
        cur += timedelta(hours=1)
    return out


def hour_dirs_for_windows(windows: "list[tuple[int, int]]") -> "list[Path]":
    """Archive hour folders (…/year/month/day/hour) covering the given windows,
    de-duplicated and in chronological order."""
    seen: set = set()
    out: list[Path] = []
    for start_ns, end_ns in windows:
        for y, m, d, h in utc_hour_cells_for_window(start_ns, end_ns):
            key = (y, m, d, h)
            if key in seen:
                continue
            seen.add(key)
            out.append(container_root_for_year(y) / str(y) / str(m) / str(d) / str(h))
    return out


def cameras_for_windows(windows: "list[tuple[int, int]]"
                        ) -> "tuple[list[tuple[str, str]], str]":
    """UNION of the camera folders present anywhere in the selection.

    Returns (cameras, status) where cameras is [(number, folder_name)…] sorted by
    name and status is "" (ok), "no_data" (archive readable, nothing there) or
    "error" (archive root unreachable).

    Every window is visited, so one empty day or hour can no longer make the whole
    list come back empty — that used to happen because only the FIRST window was
    scanned ("cameras are the same everywhere"), which is true only for windows
    that actually have data. Within a single window the scan does stop at the
    first hour folder that has cameras, since the set does not change there.
    """
    cameras: "list[tuple[str, str]]" = []
    seen: set = set()
    now_ns = int(time.time() * 1_000_000_000)
    reachable = False
    root_checked: set = set()
    for start_ns, end_ns in windows:
        # Online-mode windows carry an open end (1 << 62) — enumerating hour
        # folders up to that would never finish. A pick window never spans more
        # than one day, so 25 h is a safe ceiling for the folder walk.
        end_ns = min(end_ns, now_ns + ONE_HOUR_NS, start_ns + 25 * ONE_HOUR_NS)
        if end_ns <= start_ns:
            end_ns = start_ns + 1
        for hour_dir in hour_dirs_for_windows([(start_ns, end_ns)]):
            root = hour_dir.parents[3]
            if root not in root_checked:
                root_checked.add(root)
                try:
                    if root.exists():
                        reachable = True
                except OSError:
                    pass
            try:
                if not (hour_dir.exists() and hour_dir.is_dir()):
                    continue
                subs = sorted([p.name for p in hour_dir.iterdir() if p.is_dir()],
                              key=str.lower)
            except OSError:
                continue
            reachable = True
            for name in subs:
                if name in seen:
                    continue
                seen.add(name)
                m = re.match(r"^C\d{2}-(\d{2,3})-", name)
                cameras.append((m.group(1) if m else "", name))
            if subs:
                break   # this window is covered; move on to the next day/segment
    cameras.sort(key=lambda t: t[1].lower())
    if cameras:
        return cameras, ""
    return cameras, ("no_data" if reachable else "error")


class DatePickerDialog(QDialog):
    """Time-window picker: one calendar, minute-resolution From/To times and two
    mutually exclusive multi-day modes (continuous day range, or one explicit
    window per day)."""

    def __init__(self, start_folder=None, hour_from_init=None, hour_to_init=None,
                 parent=None, min_from_init=None, min_to_init=None,
                 init_date=None, init_segments=None, init_range_mode=False):
        super().__init__(parent)
        self.setWindowTitle("Time window")
        self._camera_mode = False              # set to True by open_folder
        self._segments: "list[PickSeg]" = []   # per-day windows (both multi modes)
        self._range_start: "date | None" = None
        self._range_end:   "date | None" = None
        # date -> (h_from, m_from, h_to, m_to) set through the per-day ⚙ editor.
        # Survives rebuilds of the day list and the global From/To fields.
        self._day_overrides: dict = {}

        init_dt = datetime.now(TZ_PRAGUE)
        init_hour = init_dt.hour

        if start_folder is not None:
            ax = axis_from_any_folder(start_folder)
            if ax is not None:
                try:
                    dt0 = _dt_from_ns(ax[0])
                    init_dt = dt0; init_hour = dt0.hour
                except Exception:
                    pass

        # The dialog always reopens on the previous pick — the day (and the whole
        # multi-day list, restored further down) the user chose last time. The Now
        # button is the way back to today.
        if init_date is not None:
            init_dt = datetime(init_date.year, init_date.month, init_date.day,
                               init_dt.hour, tzinfo=TZ_PRAGUE)

        # Default window = the whole current hour, expressed hour-exact
        # (12:00–13:00, never 12:00–12:59).
        _def_h_to, _def_m_to = hour_end_hm(init_hour)
        if hour_from_init is None: hour_from_init = init_hour
        if hour_to_init is None:   hour_to_init   = _def_h_to
        if min_from_init is None:  min_from_init  = 0
        if min_to_init is None:    min_to_init    = _def_m_to

        self._cal_frame, self.cal = _make_multiselect_calendar(
            QDate(init_dt.year, init_dt.month, init_dt.day))
        self.cal.setMinimumWidth(260)
        self.cal.clicked.connect(self._on_calendar_clicked)

        self.time_from = QTimeEdit(self)
        self.time_from.setDisplayFormat("HH:mm")
        self.time_from.setTime(QTime(max(0, min(23, int(hour_from_init))),
                                    max(0, min(59, int(min_from_init)))))
        self.time_from.setFixedWidth(74)
        self.time_to = QTimeEdit(self)
        self.time_to.setDisplayFormat("HH:mm")
        self.time_to.setTime(QTime(max(0, min(23, int(hour_to_init))),
                                  max(0, min(59, int(min_to_init)))))
        self.time_to.setFixedWidth(74)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept); btns.rejected.connect(self.reject)

        _cb_style = _CHECKBOX_STYLE + (
            "QCheckBox { font-size: 11px; font-weight: 700; color: #1a3a8f; }"
        )
        self.cb_now = QCheckBox("Live mode")
        self.cb_now.setToolTip(
            "Today, from the start of the current hour to the end of it. Viewer will "
            "automatically load and follow new images as they arrive.\n"
            "Off = the window is loaded once and nothing follows live.")
        self.cb_now.setStyleSheet(_cb_style)
        self.cb_now.stateChanged.connect(self._on_now_changed)

        # ── Two multi-day modes (mutually exclusive) ───────────────────────────
        self.cb_range = QCheckBox("Multiple day selection (day → day)")
        self.cb_range.setToolTip(
            f"Click the first and then the last day in the calendar. Every day in "
            f"between is loaded with the same time window, preset to "
            f"{_MULTIDAY_FROM.toString('HH:mm')}–{_MULTIDAY_TO.toString('HH:mm')}. "
            f"Change From/To to move all days at once, or press ⚙ next to a single "
            f"day in the list to give just that day its own window.")
        self.cb_range.setStyleSheet(_cb_style)

        self.cb_perday = QCheckBox("Multiple days – time window per day")
        self.cb_perday.setToolTip(
            f"Pick a day, set From/To, press 'Add day'. Repeat for as many days as "
            f"needed — each day keeps its own time window "
            f"(preset {_MULTIDAY_FROM.toString('HH:mm')}–{_MULTIDAY_TO.toString('HH:mm')}, "
            f"editable per day with ⚙).")
        self.cb_perday.setStyleSheet(_cb_style)

        self.cb_range.stateChanged.connect(self._on_range_toggled)
        self.cb_perday.stateChanged.connect(self._on_perday_toggled)

        self.btn_add_day = QPushButton("Add day")
        self.btn_add_day.setToolTip("Add the selected day with the chosen From/To time.")
        self.btn_add_day.clicked.connect(self._on_add_day)
        self.btn_add_day.setVisible(False)

        self.btn_clear_days = QPushButton("Clear")
        self.btn_clear_days.clicked.connect(self._clear_selection)
        self.btn_clear_days.setVisible(False)

        self._add_row_widget = QWidget()
        _add_row = QHBoxLayout(self._add_row_widget)
        _add_row.setContentsMargins(0, 0, 0, 0)
        _add_row.addWidget(self.btn_add_day)
        _add_row.addWidget(self.btn_clear_days)
        _add_row.addStretch(1)
        self._add_row_widget.setVisible(False)

        # Date | Time | ⚙ (edit this day's window) | ✕ (remove, per-day mode only)
        self._seg_table = QTableWidget(0, 4)
        self._seg_table.setHorizontalHeaderLabels(["Date", "Time", "", ""])
        self._seg_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._seg_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        for _c in (2, 3):
            self._seg_table.horizontalHeader().setSectionResizeMode(
                _c, QHeaderView.ResizeMode.Fixed)
            self._seg_table.setColumnWidth(_c, 28)
        self._seg_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._seg_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._seg_table.verticalHeader().setVisible(False)
        self._seg_table.setMaximumHeight(160)
        self._seg_table.setVisible(False)

        btn_now = QPushButton("Now")
        btn_now.setToolTip("Jump to today and the current hour (also switches Live mode on)")
        btn_now.setFixedWidth(48)
        btn_now.clicked.connect(self._go_to_now)

        top = QHBoxLayout()
        top.addWidget(QLabel("From:")); top.addWidget(self.time_from); top.addSpacing(10)
        top.addWidget(QLabel("To:"));   top.addWidget(self.time_to)
        top.addSpacing(10); top.addWidget(btn_now)
        top.addSpacing(10); top.addWidget(self.cb_now); top.addStretch(1)

        self.time_from.timeChanged.connect(self._on_times_changed)
        self.time_to.timeChanged.connect(self._on_times_changed)

        # Spusť scan kamer hned při otevření dialogu
        self._cam_signals = _CamLoaderSignals()
        self._cam_signals.finished.connect(self._on_cameras_preloaded)
        self._preloaded: list[tuple[str, str]] = []
        self._load_cameras_bg()

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Select date and time range"))
        lay.addWidget(self._cal_frame)
        lay.addLayout(top)
        lay.addWidget(self.cb_range)
        lay.addWidget(self.cb_perday)
        lay.addWidget(self._add_row_widget)
        lay.addWidget(self._seg_table)
        lay.addWidget(btns)

        # Reopen with the previous multi-day pick intact (before the first
        # highlight/camera scan below, so both see the restored days).
        if init_segments:
            self._restore_segments(init_segments, bool(init_range_mode))

        # Keyboard/programmatic date changes must repaint too (clicked() only
        # covers the mouse). Connected last — the handler reads the checkboxes.
        self.cal.selectionChanged.connect(self._refresh_highlight)
        self._refresh_highlight()

        # Live mode is OPT-IN: it is never pre-ticked, so nothing starts following
        # the newest frame unless the user asks for it here (or presses Now). The
        # dialog still opens on the last used window / the current hour.
        self._load_cameras_bg()

    # ── selection state ───────────────────────────────────────────────────────
    def _restore_segments(self, segments, range_mode: bool):
        """Re-enter the multi-day mode of the previous pick with its day list.
        Ticking the checkbox through setChecked would reset the times and wipe
        the list, so the mode is applied with the signals blocked."""
        segs = sorted((PickSeg(*_seg_fields(s)) for s in segments),
                      key=lambda s: (s.date, s.h_from, s.m_from))
        if not segs:
            return
        cb = self.cb_range if range_mode else self.cb_perday
        cb.blockSignals(True); cb.setChecked(True); cb.blockSignals(False)
        self._apply_mode()
        self._segments = segs
        if range_mode:
            self._range_start, self._range_end = segs[0].date, segs[-1].date
        # Days whose window differs from the global From/To were edited with ⚙ —
        # keep them marked so a From/To change does not silently overwrite them.
        glob = self.selected_times()
        self._day_overrides = {
            s.date: (s.h_from, s.m_from, s.h_to, s.m_to)
            for s in segs if (s.h_from, s.m_from, s.h_to, s.m_to) != glob}
        self._refresh_seg_table()

    def is_multiday(self) -> bool:
        return self.cb_range.isChecked() or self.cb_perday.isChecked()

    def is_range_mode(self) -> bool:
        """True = day→day range, False = per-day windows (only meaningful when
        is_multiday())."""
        return self.cb_range.isChecked()

    def is_online_mode(self) -> bool:
        return self.cb_now.isChecked()

    def selected_times(self) -> "tuple[int, int, int, int]":
        """(from_hour, from_minute, to_hour, to_minute)."""
        tf, tt = self.time_from.time(), self.time_to.time()
        return tf.hour(), tf.minute(), tt.hour(), tt.minute()

    def selected_hours(self) -> tuple[int, int]:
        """Whole-hour span (folder granularity) — kept for existing callers."""
        return self.time_from.time().hour(), self.time_to.time().hour()

    def selected_date_obj(self):
        d = self.cal.selectedDate()
        return datetime(d.year(), d.month(), d.day(), tzinfo=TZ_PRAGUE).date()

    def selected_segments(self) -> "list[PickSeg] | None":
        """Per-day windows when a multi-day mode is active, else None."""
        if self.is_multiday() and self._segments:
            return list(self._segments)
        return None

    def selected_windows(self) -> "list[tuple[int, int]]":
        """[(start_ns, end_ns)) …] for the whole selection — one entry per day."""
        segs = self.selected_segments()
        if segs is None:
            hf, mf, ht, mt = self.selected_times()
            segs = [PickSeg(self.selected_date_obj(), hf, mf, ht, mt)]
        return [seg_bounds_ns(s) for s in segs]

    def selected_axis(self) -> tuple[int, int]:
        """Slider axis = bounding box of the selection (gaps stay blank)."""
        wins = self.selected_windows()
        return min(w[0] for w in wins), max(w[1] for w in wins)

    def selected_folders(self) -> list[Path]:
        """Archive hour folders (no camera) for the current selection."""
        return hour_dirs_for_windows(self.selected_windows())

    @staticmethod
    def selected_folders_static(date, hour_from, hour_to, camera_folder: Path,
                                 extra_dates: "list | None" = None,
                                 segments: "list | None" = None,
                                 min_from: int = 0, min_to: int = 0) -> list[Path]:
        """
        Return list of archiver folder Paths for a camera over the selection.
        Precedence:
          - segments (PickSeg list, or legacy (date, hour_from, hour_to) tuples), or
          - extra_dates (list of date objects) → all those days share hour_from..hour_to, or
          - single date with hour_from..hour_to.
        hour_to/min_to are the EXCLUSIVE end of the window (see seg_bounds_ns), so
        the default 13:00 covers the 12 h folder only.
        Times are Prague lab time; folders are UTC (see utc_hour_cells_for_window).
        """
        cam_name = camera_folder.name
        if segments:
            plan = list(segments)
        elif extra_dates is not None:
            plan = [PickSeg(d, int(hour_from), int(min_from), int(hour_to), int(min_to))
                    for d in extra_dates]
        else:
            plan = [PickSeg(date, int(hour_from), int(min_from),
                            int(hour_to), int(min_to))]
        windows = [seg_bounds_ns(s) for s in plan]
        return [f / cam_name for f in hour_dirs_for_windows(windows)]

    def preloaded_cameras(self) -> list[tuple[str, str]]:
        return self._preloaded

    def _load_cameras_bg(self):
        import threading as _thr
        # Union over EVERY day/segment of the selection — a day without data must
        # not empty the list (see cameras_for_windows).
        windows = self.selected_windows()
        key = tuple(windows)
        if key == getattr(self, "_cam_scan_key", None):
            return   # same selection (every calendar click used to rescan)
        self._cam_scan_key = key
        self._cam_scan_gen = getattr(self, "_cam_scan_gen", 0) + 1
        gen = self._cam_scan_gen
        signals = self._cam_signals

        def worker():
            try:
                cameras, status = cameras_for_windows(windows)
            except Exception:
                cameras, status = [], "error"
            # A slower scan of an older selection must not overwrite a newer one.
            if gen == self._cam_scan_gen:
                signals.finished.emit(cameras, status)

        _thr.Thread(target=worker, daemon=True).start()

    def _on_cameras_preloaded(self, cameras: list, status: str = ""):
        self._preloaded = cameras

    # ── mode toggles ──────────────────────────────────────────────────────────
    def _apply_multiday_default_times(self):
        """Multi-day selections start at the lab shift (07:00–21:00)."""
        for w, t in ((self.time_from, _MULTIDAY_FROM), (self.time_to, _MULTIDAY_TO)):
            w.blockSignals(True)
            w.setTime(t)
            w.blockSignals(False)

    def _on_range_toggled(self, state: int):
        on = bool(state)
        if on and self.cb_perday.isChecked():
            self.cb_perday.blockSignals(True)
            self.cb_perday.setChecked(False)
            self.cb_perday.blockSignals(False)
        self._apply_mode()
        self._day_overrides = {}
        if on:
            self._apply_multiday_default_times()
            # Seed with the day that is already selected as a COMPLETE one-day
            # range, so the next two clicks read as "first day … last day".
            self._range_start = self._range_end = self.selected_date_obj()
            self._rebuild_range_segments()
        else:
            self._range_start = self._range_end = None
            self._segments = []
            self._refresh_seg_table()
        self._refresh_highlight()

    def _on_perday_toggled(self, state: int):
        on = bool(state)
        if on and self.cb_range.isChecked():
            self.cb_range.blockSignals(True)
            self.cb_range.setChecked(False)
            self.cb_range.blockSignals(False)
        self._apply_mode()
        if on:
            self._apply_multiday_default_times()
        self._range_start = self._range_end = None
        self._segments = []
        self._day_overrides = {}
        self._refresh_seg_table()
        self._refresh_highlight()

    def _apply_mode(self):
        multi = self.is_multiday()
        self.btn_add_day.setVisible(self.cb_perday.isChecked())
        self.btn_clear_days.setVisible(multi)
        self._add_row_widget.setVisible(multi)
        self._seg_table.setVisible(multi)
        if multi:
            # Live mode is single-day-only
            self.cb_now.setChecked(False)
            self.cb_now.setEnabled(False)
        else:
            self.cb_now.setEnabled(True)
        self.adjustSize()

    def _clear_selection(self):
        self._range_start = self._range_end = None
        self._segments = []
        self._day_overrides = {}
        self._refresh_seg_table()
        self._refresh_highlight()

    # ── calendar interaction ──────────────────────────────────────────────────
    def _on_calendar_clicked(self, qd: QDate):
        d = datetime(qd.year(), qd.month(), qd.day(), tzinfo=TZ_PRAGUE).date()
        # Online mode only makes sense on today — picking another day leaves it,
        # otherwise the viewer would poll a finished day for new frames.
        if self.cb_now.isChecked() and d != datetime.now(TZ_PRAGUE).date():
            self.cb_now.setChecked(False)
        if self.cb_range.isChecked():
            if self._range_start is None or self._range_end is not None:
                # First click of a new range
                self._range_start, self._range_end = d, None
            else:
                self._range_end = d
                if self._range_end < self._range_start:
                    self._range_start, self._range_end = self._range_end, self._range_start
            self._rebuild_range_segments()
        self._refresh_highlight()
        self._load_cameras_bg()   # another day → rescan (no-op if unchanged)

    def _range_days(self) -> "list":
        if self._range_start is None:
            return []
        end = self._range_end or self._range_start
        days, cur = [], self._range_start
        while cur <= end:
            days.append(cur)
            cur += timedelta(days=1)
        return days

    def _rebuild_range_segments(self):
        """Range mode: every day of the range gets the same From/To window (preset
        to the lab shift), except days the user edited through the ⚙ button."""
        days = self._range_days()
        hf, mf, ht, mt = self.selected_times()
        segs: list[PickSeg] = []
        for d in days:
            ovr = self._day_overrides.get(d)
            segs.append(PickSeg(d, *ovr) if ovr else PickSeg(d, hf, mf, ht, mt))
        self._segments = segs
        self._refresh_seg_table()

    def _refresh_highlight(self):
        delegate = getattr(self.cal, "_wk_delegate", None)
        if delegate is None:
            return
        if self.is_multiday():
            days = [s.date for s in self._segments]
        else:
            days = [self.selected_date_obj()]
        delegate.set_selected([QDate(d.year, d.month, d.day) for d in days])
        cur = self.cal.selectedDate()
        delegate.set_focus_date(cur if self.cb_perday.isChecked() else None)

    # ── per-day list ──────────────────────────────────────────────────────────
    def _on_add_day(self):
        hf, mf, ht, mt = self.selected_times()
        if (hf, mf) >= (ht, mt):
            QMessageBox.warning(self, "Invalid time", '"From" must be earlier than "To".')
            return
        self._upsert_segment(PickSeg(self.selected_date_obj(), hf, mf, ht, mt))
        self._refresh_seg_table()
        self._refresh_highlight()
        self._load_cameras_bg()

    def _edit_segment(self, d):
        """⚙ — give this one day its own time window, in either multi-day mode."""
        cur = next((s for s in self._segments if _seg_fields(s)[0] == d), None)
        if cur is None:
            return
        _d, hf, mf, ht, mt = _seg_fields(cur)
        dlg = _DayTimeDialog(d, QTime(hf, mf), QTime(min(23, ht), mt), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        hm = dlg.selected_hm()
        self._day_overrides[d] = hm
        self._upsert_segment(PickSeg(d, *hm))
        self._refresh_seg_table()
        self._refresh_highlight()
        self._load_cameras_bg()

    def _upsert_segment(self, seg: PickSeg):
        """Insert or replace the window for that date, keeping the list sorted."""
        self._segments = [s for s in self._segments if s.date != seg.date]
        self._segments.append(seg)
        self._segments.sort(key=lambda s: (s.date, s.h_from, s.m_from))

    def _remove_segment(self, d):
        """Per-day mode only — a day→day range is redefined by clicking its ends
        (or Clear), so removing one day out of the middle is not offered."""
        self._segments = [s for s in self._segments if s.date != d]
        self._day_overrides.pop(d, None)
        self._refresh_seg_table()
        self._refresh_highlight()

    def _refresh_seg_table(self):
        self._seg_table.setRowCount(0)
        removable = self.cb_perday.isChecked()
        for s in self._segments:
            r = self._seg_table.rowCount()
            self._seg_table.insertRow(r)
            self._seg_table.setItem(r, 0, QTableWidgetItem(s.date.strftime("%d.%m.%Y")))
            edited = s.date in self._day_overrides
            t_item = QTableWidgetItem(
                f"{s.h_from:02d}:{s.m_from:02d} – {s.h_to:02d}:{s.m_to:02d}"
                + (" *" if edited else ""))
            if edited:
                t_item.setToolTip("Time window edited for this day only")
            self._seg_table.setItem(r, 1, t_item)
            gear = QPushButton("⚙")
            gear.setFixedSize(24, 24)
            gear.setStyleSheet("font-size: 12px; padding: 0;")
            gear.setToolTip("Edit the time window of this day")
            gear.clicked.connect(lambda checked, dd=s.date: self._edit_segment(dd))
            self._seg_table.setCellWidget(r, 2, gear)
            if removable:
                btn = QPushButton("✕")
                btn.setFixedSize(24, 24)
                btn.setStyleSheet("font-size: 10px; padding: 0;")
                btn.clicked.connect(lambda checked, dd=s.date: self._remove_segment(dd))
                self._seg_table.setCellWidget(r, 3, btn)

    # ── time controls ─────────────────────────────────────────────────────────
    def _on_times_changed(self):
        # "To" is the exclusive end, so From == To is an EMPTY window: keep the
        # fields at least one whole hour apart by pushing the other one.
        if self.time_from.time() >= self.time_to.time():
            if self.sender() is self.time_to:
                t = self.time_to.time().addSecs(-3600)
                self.time_from.blockSignals(True)
                self.time_from.setTime(max(QTime(0, 0), t))
                self.time_from.blockSignals(False)
            else:
                t = self.time_from.time().addSecs(3600)
                self.time_to.blockSignals(True)
                self.time_to.setTime(t if t > self.time_from.time() else QTime(23, 59))
                self.time_to.blockSignals(False)
        if self.cb_range.isChecked():
            self._rebuild_range_segments()

    def _now_window(self) -> "tuple[QTime, QTime]":
        """Today's current hour as an hour-exact window: 9:20 → 09:00–10:00, so the
        selection is 'the last 20 minutes' and online mode keeps extending it."""
        h = datetime.now(TZ_PRAGUE).hour
        ht, mt = hour_end_hm(h)
        return QTime(h, 0), QTime(ht, mt)

    def _apply_now_window(self):
        t_from, t_to = self._now_window()
        for w, t in ((self.time_from, t_from), (self.time_to, t_to)):
            w.blockSignals(True)
            w.setTime(t)
            w.blockSignals(False)

    def _go_to_now(self):
        """Now button — today, current hour, live mode. Leaves any multi-day mode,
        which is what makes the jump land on a single, live day."""
        now_dt = datetime.now(TZ_PRAGUE)
        for cb in (self.cb_range, self.cb_perday):
            if cb.isChecked():
                cb.setChecked(False)   # handler clears the day list and refreshes
        self.cal.setSelectedDate(QDate(now_dt.year, now_dt.month, now_dt.day))
        self._apply_now_window()
        self.cb_now.setChecked(True)
        self._refresh_highlight()
        self._load_cameras_bg()

    def _on_now_changed(self, state: int):
        if state:
            # Live mode is always "today, the current hour" — jump there so the
            # checkbox and the Now button cannot disagree.
            now_dt = datetime.now(TZ_PRAGUE)
            if self.selected_date_obj() != now_dt.date():
                self.cal.setSelectedDate(QDate(now_dt.year, now_dt.month, now_dt.day))
            self._apply_now_window()
            self._refresh_highlight()

    def _on_accept(self):
        if self.is_multiday():
            if not self._segments:
                QMessageBox.warning(self, "Nothing selected",
                    "Pick the days in the calendar first "
                    "(range mode: click the first and the last day; "
                    "per-day mode: select a day and press 'Add day')."); return
            if len(self._segments) > 14:
                r = QMessageBox.question(self, "Multi-day",
                    f"{len(self._segments)} days selected. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r != QMessageBox.StandardButton.Yes:
                    return
        else:
            hf, mf, ht, mt = self.selected_times()
            if (hf, mf) >= (ht, mt):
                QMessageBox.warning(self, "Invalid time",
                                    '"From" must be earlier than "To".'); return
        self.accept()


class _DayTimeDialog(QDialog):
    """⚙ editor — the time window of ONE day of a multi-day selection."""

    def __init__(self, day, t_from: QTime, t_to: QTime, parent=None):
        super().__init__(parent)
        self.setWindowTitle(day.strftime("Time window — %d.%m.%Y"))
        self.time_from = QTimeEdit(self); self.time_from.setDisplayFormat("HH:mm")
        self.time_from.setTime(t_from); self.time_from.setFixedWidth(74)
        self.time_to = QTimeEdit(self); self.time_to.setDisplayFormat("HH:mm")
        self.time_to.setTime(t_to); self.time_to.setFixedWidth(74)

        row = QHBoxLayout()
        row.addWidget(QLabel("From:")); row.addWidget(self.time_from)
        row.addSpacing(10)
        row.addWidget(QLabel("To:"));   row.addWidget(self.time_to)
        row.addStretch(1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel, self)
        btns.accepted.connect(self._on_accept); btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(day.strftime("%A %d.%m.%Y")))
        lay.addLayout(row)
        lay.addWidget(btns)

    def selected_hm(self) -> "tuple[int, int, int, int]":
        tf, tt = self.time_from.time(), self.time_to.time()
        return tf.hour(), tf.minute(), tt.hour(), tt.minute()

    def _on_accept(self):
        hf, mf, ht, mt = self.selected_hm()
        if (hf, mf) >= (ht, mt):
            QMessageBox.warning(self, "Invalid time",
                                '"From" must be earlier than "To".'); return
        self.accept()

# ---------------- PDXM1 GRID CONFIG ----------------
from dataclasses import dataclass as _dataclass, field as _field, asdict as _asdict

_PDXM1_GRID_CONFIGS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "pdxm1_grid_configs.json"

def _cam_type_key(cam_name: str) -> str:
    """Return a config key for grid storage.
    PDXM1/PDXM2 cameras share a type key (PD1M1, PD2M2, …).
    All other cameras use their full name as key (per-camera config)."""
    m = re.search(r'PD[1-4]M[12]', cam_name, re.IGNORECASE)
    return m.group(0).upper() if m else cam_name.strip()

def _load_pdxm1_grid_configs() -> dict:
    try:
        if _PDXM1_GRID_CONFIGS_PATH.exists():
            return json.loads(_PDXM1_GRID_CONFIGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_pdxm1_grid_configs(data: dict):
    try:
        _PDXM1_GRID_CONFIGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PDXM1_GRID_CONFIGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

# Known camera types whose columns are displayed reversed
_PDXM1_REVERSED_TYPES = {"PD2M1"}

@_dataclass
class Pdxm1GridConfig:
    n_cols: int = 5
    n_rows: int = 8
    # Absolute fractions [0,1] of the full image — NOT relative to the grid borders.
    # Length = n_cols-1 and n_rows-1 respectively.  [] = auto (equal spacing).
    # Storing absolute positions makes every line truly independent: moving a
    # border does NOT shift the inner dividers, and vice versa.
    col_dividers: list = _field(default_factory=list)
    row_dividers: list = _field(default_factory=list)
    col_labels: list = _field(default_factory=list)   # [] = auto (A-E or reversed)
    row_labels: list = _field(default_factory=list)   # [] = auto (1-8)
    col_reversed: bool = False
    line_width: int = 1
    line_color: str = "#ffffff"
    line_alpha: int = 90
    font_size: int = 0                                 # 0 = auto
    font_color: str = "#ffffff"
    font_alpha: int = 210
    font_outline: int = 1                             # 0 = no outline; >0 = outline thickness in px
    show: bool = False                               # grid overlay hidden until the user turns it on
    grid_left:   float = 0.0                         # left border as fraction of image width
    grid_right:  float = 1.0                         # right border as fraction of image width
    grid_top:    float = 0.0                         # top border as fraction of image height
    grid_bottom: float = 1.0                         # bottom border as fraction of image height

    def effective_col_labels(self) -> list:
        if self.col_labels:
            return self.col_labels
        base = [chr(65 + i) for i in range(self.n_cols)]
        return list(reversed(base)) if self.col_reversed else base

    def effective_row_labels(self) -> list:
        if self.row_labels:
            return self.row_labels
        return [str(i + 1) for i in range(self.n_rows)]

    def effective_col_dividers(self) -> list:
        """Absolute image-fraction positions of inner column dividers (len = n_cols-1)."""
        n = self.n_cols
        if self.col_dividers and len(self.col_dividers) == n - 1:
            return list(self.col_dividers)
        # Default: equal spacing within current grid borders
        gl, gr = self.grid_left, self.grid_right
        return [gl + (gr - gl) * i / n for i in range(1, n)]

    def effective_row_dividers(self) -> list:
        """Absolute image-fraction positions of inner row dividers (len = n_rows-1)."""
        n = self.n_rows
        if self.row_dividers and len(self.row_dividers) == n - 1:
            return list(self.row_dividers)
        gt, gb = self.grid_top, self.grid_bottom
        return [gt + (gb - gt) * i / n for i in range(1, n)]


def get_pdxm1_grid_config(cam_name: str) -> Pdxm1GridConfig:
    """Return the Pdxm1GridConfig for a camera, loading saved data or returning defaults."""
    key = _cam_type_key(cam_name)
    saved = _load_pdxm1_grid_configs()
    cfg = Pdxm1GridConfig()
    # Grid overlay defaults to hidden for all cameras (diodes included);
    # the user turns it on per-type via the config dialog.
    cfg.show = False
    cfg.col_reversed = key in _PDXM1_REVERSED_TYPES
    if key in saved:
        d = saved[key]
        for f in ("n_cols","n_rows","col_dividers","row_dividers","col_labels","row_labels",
                  "col_reversed","line_width","line_color","line_alpha",
                  "font_size","font_color","font_alpha","font_outline","show",
                  "grid_left","grid_right","grid_top","grid_bottom"):
            if f in d:
                setattr(cfg, f, d[f])
        # Migrate from old format (col_widths/row_heights were proportional fractions
        # within the grid area; convert to absolute image fractions).
        if "col_widths" in d and not cfg.col_dividers:
            gl = d.get("grid_left", 0.0)
            gr = d.get("grid_right", 1.0)
            ws = d["col_widths"]
            acc = 0.0
            divs = []
            for w in ws[:-1]:
                acc += w
                divs.append(gl + acc * (gr - gl))
            cfg.col_dividers = divs
        if "row_heights" in d and not cfg.row_dividers:
            gt = d.get("grid_top", 0.0)
            gb = d.get("grid_bottom", 1.0)
            hs = d["row_heights"]
            acc = 0.0
            divs = []
            for h in hs[:-1]:
                acc += h
                divs.append(gt + acc * (gb - gt))
            cfg.row_dividers = divs
    return cfg


def _draw_outlined_text(p, rect, align_flags, text, font, fill_color, outline_px: int):
    """Draw text centred in rect; if outline_px > 0 draws a black stroke behind the fill."""
    if not text:
        return
    from PySide6.QtGui import QPainterPath, QFontMetrics
    p.setFont(font)
    if outline_px <= 0:
        p.setPen(fill_color)
        p.drawText(rect, align_flags, text)
        return
    fm = QFontMetrics(font)
    tw = fm.horizontalAdvance(text)
    th = fm.height()
    rx, ry, rw, rh = rect.x(), rect.y(), rect.width(), rect.height()
    if align_flags & Qt.AlignmentFlag.AlignHCenter:
        tx = rx + (rw - tw) / 2.0
    elif align_flags & Qt.AlignmentFlag.AlignRight:
        tx = float(rx + rw - tw)
    else:
        tx = float(rx)
    if align_flags & Qt.AlignmentFlag.AlignVCenter:
        ty = ry + (rh - th) / 2.0 + fm.ascent()
    elif align_flags & Qt.AlignmentFlag.AlignBottom:
        ty = float(ry + rh - fm.descent())
    else:
        ty = float(ry + fm.ascent())
    path = QPainterPath()
    path.addText(tx, ty, font, text)
    pen = QPen(QColor(0, 0, 0), outline_px * 2)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    old_brush = p.brush()
    p.strokePath(path, pen)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill_color)
    p.drawPath(path)
    p.setBrush(old_brush)


class _GridPreviewWidget(QWidget):
    """Interactive widget for editing a Pdxm1GridConfig.
    The user can drag column/row dividers to change relative widths/heights."""

    changed = Signal()  # emitted on every drag update

    def __init__(self, cfg: Pdxm1GridConfig, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 240)
        self._cfg = cfg
        self._drag_col: int = -1   # dragging inner col divider index
        self._drag_row: int = -1   # dragging inner row divider index
        self._drag_border: str = ''  # 'left','right','top','bottom'
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0
        self._drag_orig_divs_col: list = []   # absolute fractions at drag start
        self._drag_orig_divs_row: list = []
        self._drag_orig_left:   float = 0.0
        self._drag_orig_right:  float = 1.0
        self._drag_orig_top:    float = 0.0
        self._drag_orig_bottom: float = 1.0
        self.setMouseTracking(True)

    MARGIN = 10
    HANDLE_PX = 6  # pixels around a divider where hover/drag activates

    def _col_x_positions(self):
        """Pixel x positions of inner column dividers (absolute image fractions)."""
        m = self.MARGIN
        aw = self.width() - 2 * m
        return [m + int(d * aw) for d in self._cfg.effective_col_dividers()]

    def _row_y_positions(self):
        """Pixel y positions of inner row dividers (absolute image fractions)."""
        m = self.MARGIN
        ah = self.height() - 2 * m
        return [m + int(d * ah) for d in self._cfg.effective_row_dividers()]

    def _grid_rect(self):
        """Return (gx, gy, gw, gh) pixel coords of the grid area."""
        m = self.MARGIN
        aw = self.width()  - 2 * m
        ah = self.height() - 2 * m
        gx = m + int(self._cfg.grid_left  * aw)
        gy = m + int(self._cfg.grid_top   * ah)
        gw = max(4, int((self._cfg.grid_right  - self._cfg.grid_left)  * aw))
        gh = max(4, int((self._cfg.grid_bottom - self._cfg.grid_top)   * ah))
        return gx, gy, gw, gh

    def _hit_col_divider(self, pos) -> int:
        """Return col index (0-based, after col i) or -1."""
        for i, x in enumerate(self._col_x_positions()):
            if abs(pos.x() - x) <= self.HANDLE_PX:
                return i
        return -1

    def _hit_row_divider(self, pos) -> int:
        for i, y in enumerate(self._row_y_positions()):
            if abs(pos.y() - y) <= self.HANDLE_PX:
                return i
        return -1

    def _hit_border(self, pos) -> str:
        """Return 'left','right','top','bottom' or '' for outer grid border lines."""
        gx, gy, gw, gh = self._grid_rect()
        h = self.HANDLE_PX
        if abs(pos.x() - gx)       <= h and gy <= pos.y() <= gy + gh: return 'left'
        if abs(pos.x() - (gx + gw)) <= h and gy <= pos.y() <= gy + gh: return 'right'
        if abs(pos.y() - gy)       <= h and gx <= pos.x() <= gx + gw: return 'top'
        if abs(pos.y() - (gy + gh)) <= h and gx <= pos.x() <= gx + gw: return 'bottom'
        return ''

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        ci = self._hit_col_divider(pos)
        ri = self._hit_row_divider(pos)
        bd = self._hit_border(pos)
        if ci >= 0:
            self._drag_col = ci
            self._drag_start_x = pos.x()
            self._drag_orig_divs_col = list(self._cfg.effective_col_dividers())
        elif ri >= 0:
            self._drag_row = ri
            self._drag_start_y = pos.y()
            self._drag_orig_divs_row = list(self._cfg.effective_row_dividers())
        elif bd:
            self._drag_border = bd
            self._drag_start_x = pos.x()
            self._drag_start_y = pos.y()
            self._drag_orig_left   = self._cfg.grid_left
            self._drag_orig_right  = self._cfg.grid_right
            self._drag_orig_top    = self._cfg.grid_top
            self._drag_orig_bottom = self._cfg.grid_bottom
            # Freeze inner dividers at their current absolute positions so border
            # movement doesn't shift them (effective_col/row_dividers recomputes from
            # grid bounds when col/row_dividers is empty).
            if not (self._cfg.col_dividers and
                    len(self._cfg.col_dividers) == self._cfg.n_cols - 1):
                self._cfg.col_dividers = list(self._cfg.effective_col_dividers())
            if not (self._cfg.row_dividers and
                    len(self._cfg.row_dividers) == self._cfg.n_rows - 1):
                self._cfg.row_dividers = list(self._cfg.effective_row_dividers())

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_col >= 0:
            aw = self.width() - 2 * self.MARGIN
            if aw > 0:
                dx = pos.x() - self._drag_start_x
                delta_frac = dx / aw
                divs = list(self._drag_orig_divs_col)
                i = self._drag_col
                MIN_SEP = 0.01
                prev = self._cfg.grid_left if i == 0 else divs[i - 1]
                nxt  = self._cfg.grid_right if i >= len(divs) - 1 else divs[i + 1]
                divs[i] = max(prev + MIN_SEP, min(nxt - MIN_SEP,
                              self._drag_orig_divs_col[i] + delta_frac))
                self._cfg.col_dividers = divs
                self.update(); self.changed.emit(); return
        if self._drag_row >= 0:
            ah = self.height() - 2 * self.MARGIN
            if ah > 0:
                dy = pos.y() - self._drag_start_y
                delta_frac = dy / ah
                divs = list(self._drag_orig_divs_row)
                i = self._drag_row
                MIN_SEP = 0.01
                prev = self._cfg.grid_top if i == 0 else divs[i - 1]
                nxt  = self._cfg.grid_bottom if i >= len(divs) - 1 else divs[i + 1]
                divs[i] = max(prev + MIN_SEP, min(nxt - MIN_SEP,
                              self._drag_orig_divs_row[i] + delta_frac))
                self._cfg.row_dividers = divs
                self.update(); self.changed.emit(); return
        # Border drag — each line moves only itself
        if self._drag_border:
            m = self.MARGIN
            aw = self.width()  - 2 * m
            ah = self.height() - 2 * m
            if aw > 0 and ah > 0:
                dx = (pos.x() - self._drag_start_x) / aw
                dy = (pos.y() - self._drag_start_y) / ah
                MIN = 0.02
                b = self._drag_border
                if b == 'left':
                    self._cfg.grid_left  = max(0.0, min(self._drag_orig_right - MIN,
                                                         self._drag_orig_left + dx))
                elif b == 'right':
                    self._cfg.grid_right = max(self._drag_orig_left + MIN, min(1.0,
                                                         self._drag_orig_right + dx))
                elif b == 'top':
                    self._cfg.grid_top    = max(0.0, min(self._drag_orig_bottom - MIN,
                                                          self._drag_orig_top + dy))
                elif b == 'bottom':
                    self._cfg.grid_bottom = max(self._drag_orig_top + MIN, min(1.0,
                                                          self._drag_orig_bottom + dy))
                self.update(); self.changed.emit(); return
        # Hover: change cursor near dividers / borders
        ci = self._hit_col_divider(pos)
        ri = self._hit_row_divider(pos)
        bd = self._hit_border(pos)
        if ci >= 0 or bd in ('left', 'right'):
            self.setCursor(Qt.CursorShape.SplitHCursor)
        elif ri >= 0 or bd in ('top', 'bottom'):
            self.setCursor(Qt.CursorShape.SplitVCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        self._drag_col = -1
        self._drag_row = -1
        self._drag_border = ''

    def paintEvent(self, event):
        from PySide6.QtGui import QFont as _GF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.fillRect(self.rect(), QColor(0x22, 0x22, 0x22))

        m = self.MARGIN
        avail_w = self.width()  - 2 * m
        avail_h = self.height() - 2 * m
        divs_col = self._cfg.effective_col_dividers()
        divs_row = self._cfg.effective_row_dividers()
        col_labels = self._cfg.effective_col_labels()
        row_labels = self._cfg.effective_row_labels()

        # Border pixel positions
        gx = m + int(self._cfg.grid_left   * avail_w)
        gy = m + int(self._cfg.grid_top    * avail_h)
        gw = max(4, int((self._cfg.grid_right  - self._cfg.grid_left)  * avail_w))
        gh = max(4, int((self._cfg.grid_bottom - self._cfg.grid_top)   * avail_h))

        lc = QColor(self._cfg.line_color)
        lc.setAlpha(self._cfg.line_alpha)
        pen = QPen(lc); pen.setWidth(max(1, self._cfg.line_width)); p.setPen(pen)

        # Build col/row pixel positions from absolute divider fractions
        col_xs = [gx] + [m + int(d * avail_w) for d in divs_col] + [gx + gw]
        row_ys = [gy] + [m + int(d * avail_h) for d in divs_row] + [gy + gh]

        for x in col_xs:
            p.drawLine(x, gy, x, gy + gh)
        for y in row_ys:
            p.drawLine(gx, y, gx + gw, y)

        # Labels
        fc = QColor(self._cfg.font_color)
        fc.setAlpha(self._cfg.font_alpha)
        n_cols_preview = len(col_xs) - 1
        fsize = self._cfg.font_size if self._cfg.font_size > 0 else max(8, int(gw / max(1, n_cols_preview) * 0.45))
        gf = _GF(); gf.setPixelSize(max(7, fsize)); gf.setBold(True)
        for ci, lbl in enumerate(col_labels):
            cx = col_xs[ci]; cw = col_xs[ci + 1] - col_xs[ci]
            cell_h_px = row_ys[1] - row_ys[0]
            _draw_outlined_text(p, QRect(cx, gy, cw, int(cell_h_px * 0.4)),
                                Qt.AlignmentFlag.AlignCenter, lbl, gf, fc, self._cfg.font_outline)
        for ri, lbl in enumerate(row_labels):
            ry = row_ys[ri]; rh = row_ys[ri + 1] - row_ys[ri]
            cell_w_px = col_xs[1] - col_xs[0]
            _draw_outlined_text(p, QRect(gx, ry, int(cell_w_px * 0.4), rh),
                                Qt.AlignmentFlag.AlignCenter, lbl, gf, fc, self._cfg.font_outline)

        # Highlight draggable inner dividers (col/row)
        dp = QPen(QColor(255, 200, 0, 120)); dp.setWidth(3); p.setPen(dp)
        for x in col_xs[1:-1]:
            p.drawLine(x, gy, x, gy + gh)
        dp.setStyle(Qt.PenStyle.DashLine); p.setPen(dp)
        for y in row_ys[1:-1]:
            p.drawLine(gx, y, gx + gw, y)

        # Highlight outer border lines as draggable (solid orange)
        op = QPen(QColor(255, 140, 0, 180)); op.setWidth(4); p.setPen(op)
        p.drawLine(col_xs[0],  gy,      col_xs[0],  gy + gh)   # left
        p.drawLine(col_xs[-1], gy,      col_xs[-1], gy + gh)   # right
        p.drawLine(gx,         row_ys[0],  gx + gw, row_ys[0])  # top
        p.drawLine(gx,         row_ys[-1], gx + gw, row_ys[-1]) # bottom

        p.end()


class Pdxm1GridConfigDialog(QDialog):
    """Dialog to configure the PDXM1 reference grid overlay."""

    def __init__(self, cam_name: str, img_view=None, parent=None):
        super().__init__(parent)
        self._cam_name = cam_name
        self._key = _cam_type_key(cam_name)
        self._cfg = get_pdxm1_grid_config(cam_name)
        self._img_view = img_view
        self._orig_show_grid = img_view.show_pdxm1_grid if img_view is not None else True
        # Push live config to the view immediately so it shows during dialog
        if img_view is not None:
            img_view._pdxm1_cfg_override = self._cfg
            img_view.update()
        self.setWindowTitle(f"Grid Config — {self._key}")
        self.resize(520, 840)
        self.setStyleSheet("""
            QDialog, QWidget#pdxm1_cfg_root { background-color: #f0f0f0; }
            QLabel      { color: #111111; background: transparent; }
            QSpinBox    { color: #111111; background-color: #ffffff;
                          border: 1px solid #aaaaaa; padding: 1px 3px; }
            QPushButton { color: #111111; background-color: #e0e0e0;
                          border: 1px solid #aaaaaa; padding: 3px 10px; }
            QPushButton:hover   { background-color: #d0d0d0; }
            QPushButton:pressed { background-color: #c0c0c0; }
        """ + _CHECKBOX_STYLE)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        cfg = self._cfg

        # ── Grid structure ──
        struct_row = QHBoxLayout()
        struct_row.addWidget(QLabel("Columns:"))
        self._ncols_sb = QSpinBox(); self._ncols_sb.setRange(1, 20); self._ncols_sb.setValue(cfg.n_cols)
        self._ncols_sb.valueChanged.connect(self._on_struct_changed)
        struct_row.addWidget(self._ncols_sb)
        struct_row.addWidget(QLabel("  Rows:"))
        self._nrows_sb = QSpinBox(); self._nrows_sb.setRange(1, 20); self._nrows_sb.setValue(cfg.n_rows)
        self._nrows_sb.valueChanged.connect(self._on_struct_changed)
        struct_row.addWidget(self._nrows_sb)
        cb_rev = QCheckBox("Reverse columns (PD2M1)")
        cb_rev.setChecked(cfg.col_reversed)
        cb_rev.stateChanged.connect(lambda v: self._set_cfg_and_refresh(col_reversed=bool(v)))
        struct_row.addWidget(cb_rev)
        struct_row.addStretch()
        self._show_cb = QCheckBox("Show grid")
        self._show_cb.setChecked(cfg.show)
        self._show_cb.stateChanged.connect(self._on_show_toggled)
        struct_row.addWidget(self._show_cb)
        lay.addLayout(struct_row)

        # ── Draggable grid preview ──
        hint = QLabel("Drag yellow dividers to adjust column widths / row heights:")
        hint.setStyleSheet("font-size: 10px; color: #aaa;")
        lay.addWidget(hint)
        self._preview = _GridPreviewWidget(cfg, self)
        self._preview.setMinimumHeight(220)
        self._preview.changed.connect(self._live_update)
        lay.addWidget(self._preview, 1)

        btn_eq_cols = QPushButton("Equal columns")
        btn_eq_rows = QPushButton("Equal rows")
        btn_eq_cols.clicked.connect(self._reset_col_dividers)
        btn_eq_rows.clicked.connect(self._reset_row_dividers)
        eq_row = QHBoxLayout()
        eq_row.addWidget(btn_eq_cols); eq_row.addWidget(btn_eq_rows); eq_row.addStretch()
        lay.addLayout(eq_row)

        # ── Appearance ──
        app_row = QHBoxLayout()
        app_row.addWidget(QLabel("Line width:"))
        self._lw_sb = QSpinBox(); self._lw_sb.setRange(1, 10); self._lw_sb.setValue(cfg.line_width)
        self._lw_sb.valueChanged.connect(lambda v: self._set_cfg_and_refresh(line_width=v))
        app_row.addWidget(self._lw_sb)

        app_row.addWidget(QLabel("  Line alpha (0–255):"))
        self._la_sb = QSpinBox(); self._la_sb.setRange(0, 255); self._la_sb.setValue(cfg.line_alpha)
        self._la_sb.valueChanged.connect(lambda v: self._set_cfg_and_refresh(line_alpha=v))
        app_row.addWidget(self._la_sb)

        app_row.addWidget(QLabel("  Line color:"))
        self._lc_btn = QPushButton()
        self._lc_btn.setFixedSize(28, 20)
        self._lc_btn.setStyleSheet(f"background:{cfg.line_color};")
        self._lc_btn.clicked.connect(self._pick_line_color)
        app_row.addWidget(self._lc_btn)
        app_row.addStretch()
        lay.addLayout(app_row)

        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Font size (0=auto):"))
        self._fs_sb = QSpinBox(); self._fs_sb.setRange(0, 40); self._fs_sb.setValue(cfg.font_size)
        self._fs_sb.valueChanged.connect(lambda v: self._set_cfg_and_refresh(font_size=v))
        font_row.addWidget(self._fs_sb)

        font_row.addWidget(QLabel("  Font alpha:"))
        self._fa_sb = QSpinBox(); self._fa_sb.setRange(0, 255); self._fa_sb.setValue(cfg.font_alpha)
        self._fa_sb.valueChanged.connect(lambda v: self._set_cfg_and_refresh(font_alpha=v))
        font_row.addWidget(self._fa_sb)

        font_row.addWidget(QLabel("  Font color:"))
        self._fc_btn = QPushButton()
        self._fc_btn.setFixedSize(28, 20)
        self._fc_btn.setStyleSheet(f"background:{cfg.font_color};")
        self._fc_btn.clicked.connect(self._pick_font_color)
        font_row.addWidget(self._fc_btn)
        font_row.addStretch()
        lay.addLayout(font_row)

        outline_row = QHBoxLayout()
        outline_row.addWidget(QLabel("Font outline (px, 0=none):"))
        self._fo_sb = QSpinBox(); self._fo_sb.setRange(0, 10); self._fo_sb.setValue(cfg.font_outline)
        self._fo_sb.valueChanged.connect(lambda v: self._set_cfg_and_refresh(font_outline=v))
        outline_row.addWidget(self._fo_sb)
        outline_row.addStretch()
        lay.addLayout(outline_row)

        # ── OK/Cancel ──
        btn_row = QHBoxLayout()
        btn_reset = QPushButton("Reset defaults")
        btn_reset.clicked.connect(self._reset_all)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self._on_reject)
        btn_row.addWidget(btns)
        lay.addLayout(btn_row)

    # ── Live preview ─────────────────────────────────────────────────────────
    def _live_update(self):
        if self._img_view is not None:
            self._img_view._pdxm1_cfg_override = self._cfg
            self._img_view.update()

    def _on_accept(self):
        if self._img_view is not None:
            self._img_view._pdxm1_cfg_override = None
        self.accept()

    def _on_reject(self):
        if self._img_view is not None:
            self._img_view.show_pdxm1_grid = self._orig_show_grid
            self._img_view._pdxm1_cfg_override = None
            self._img_view.update()
        self.reject()

    def _on_show_toggled(self, state: int):
        checked = bool(state)
        self._cfg.show = checked
        if self._img_view is not None:
            self._img_view.show_pdxm1_grid = checked
            self._img_view.update()

    # ── Config change handlers ────────────────────────────────────────────────
    def _set_cfg_and_refresh(self, **kw):
        for k, v in kw.items():
            setattr(self._cfg, k, v)
        self._preview.update()
        self._live_update()

    def _on_struct_changed(self):
        self._cfg.n_cols = self._ncols_sb.value()
        self._cfg.n_rows = self._nrows_sb.value()
        self._cfg.col_dividers = []  # reset to equal spacing
        self._cfg.row_dividers = []
        self._preview.update()
        self._live_update()

    def _reset_col_dividers(self):
        self._cfg.col_dividers = []
        self._preview.update()
        self._live_update()

    def _reset_row_dividers(self):
        self._cfg.row_dividers = []
        self._preview.update()
        self._live_update()

    def _reset_all(self):
        rev = self._key in _PDXM1_REVERSED_TYPES
        self._cfg = Pdxm1GridConfig(col_reversed=rev)
        self._preview._cfg = self._cfg
        self._ncols_sb.setValue(self._cfg.n_cols)
        self._nrows_sb.setValue(self._cfg.n_rows)
        self._lw_sb.setValue(self._cfg.line_width)
        self._la_sb.setValue(self._cfg.line_alpha)
        self._fs_sb.setValue(self._cfg.font_size)
        self._fa_sb.setValue(self._cfg.font_alpha)
        self._lc_btn.setStyleSheet(f"background:{self._cfg.line_color};")
        self._fc_btn.setStyleSheet(f"background:{self._cfg.font_color};")
        self._fo_sb.setValue(self._cfg.font_outline)
        self._show_cb.setChecked(self._cfg.show)
        self._preview.update()
        self._live_update()

    def _pick_line_color(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self._cfg.line_color), self, "Line color")
        if c.isValid():
            self._cfg.line_color = c.name()
            self._lc_btn.setStyleSheet(f"background:{self._cfg.line_color};")
            self._preview.update()
            self._live_update()

    def _pick_font_color(self):
        from PySide6.QtWidgets import QColorDialog
        c = QColorDialog.getColor(QColor(self._cfg.font_color), self, "Font color")
        if c.isValid():
            self._cfg.font_color = c.name()
            self._fc_btn.setStyleSheet(f"background:{self._cfg.font_color};")
            self._preview.update()
            self._live_update()

    def get_config(self) -> Pdxm1GridConfig:
        return self._cfg

    def save_config(self):
        data = _load_pdxm1_grid_configs()
        data[self._key] = _asdict(self._cfg)
        _save_pdxm1_grid_configs(data)


# ---------------- CAMERA LAYOUT CONFIG ----------------

@_dataclass
class CamLayoutEntry:
    x: float = 0.0   # left edge fraction [0, 1]
    y: float = 0.0   # top edge fraction [0, 1]
    w: float = 1.0   # width fraction (0, 1]
    h: float = 1.0   # height fraction (0, 1]

@_dataclass
class CamLayoutConfig:
    entries: list = _field(default_factory=list)  # list[CamLayoutEntry]


def compute_justified_layout(aspects: list, canvas_w: float, canvas_h: float,
                             top_px: float = 0.0) -> list:
    """Pack cameras into justified rows (gallery style) that MAXIMISE total image
    area for the given canvas. Within a row every tile shares one IMAGE height and
    gets width ∝ its image aspect, so each image fills its tile's image region with
    no letterbox. Each tile also reserves top_px of fixed height for the camera's
    label bar, so when the tile is sized by this function the actual frame fills the
    window and no grey shows. Tries every row count, keeps the largest realised area.

    aspects   : per-camera image aspect ratio (w / h), in camera order.
    canvas_w  : container width in pixels.
    canvas_h  : container height in pixels.
    top_px    : per-tile non-image overhead in pixels (label bar + margins).
    Returns   : list[CamLayoutEntry] (fractions of the canvas), camera order.
    """
    n = len(aspects)
    if n == 0:
        return []
    W = max(1.0, float(canvas_w))
    H = max(1.0, float(canvas_h))
    L = max(0.0, float(top_px))
    a = [max(0.05, float(x)) for x in aspects]
    best = None
    for R in range(1, n + 1):
        if H - R * L <= 0:
            continue  # the label bars alone would not fit the canvas height
        base, extra = divmod(n, R)
        sizes = [base + 1 if i < extra else base for i in range(R)]
        rows, idx = [], 0
        for s in sizes:
            rows.append(a[idx:idx + s]); idx += s
        sum_inv = sum(1.0 / sum(r) for r in rows)   # Σ 1/Σaspect over rows
        if sum_inv <= 0:
            continue
        # image_h_r = k·W/Σaspect_r fills row width; Σ image_h + R·L ≤ H
        k = min(1.0, (H - R * L) / (W * sum_inv))
        if k <= 0:
            continue
        area = (k * W) ** 2 * sum_inv               # Σ Σaspect_r·(kW/Σaspect_r)²
        if best is None or area > best[0]:
            best = (area, rows, k)
    if best is None:
        # Degenerate (labels taller than the canvas): plain equal horizontal row.
        tw = 1.0 / n
        return [CamLayoutEntry(x=i * tw, y=0.0, w=tw, h=1.0) for i in range(n)]
    _, rows, k = best
    total_h = sum((k * W / sum(r)) + L for r in rows)
    voff = max(0.0, (H - total_h) / 2.0)            # centre the block vertically
    entries, y = [], voff
    for r in rows:
        image_h = k * W / sum(r)
        tile_h = image_h + L
        row_w = image_h * sum(r)                    # = k·W (all rows equal width)
        x = max(0.0, (W - row_w) / 2.0)             # centre each row horizontally
        for ai in r:
            tile_w = ai * image_h
            entries.append(CamLayoutEntry(x=x / W, y=y / H, w=tile_w / W, h=tile_h / H))
            x += tile_w
        y += tile_h
    return entries


class _LayoutCanvasWidget(QWidget):
    """Continuous free-form drag-resize canvas for camera layout.

    Each tile stores position and size as fractions [0, 1] of the canvas —
    no grid snapping, no discrete cells.  Tiles can overlap freely.
    """

    EDGE = 12        # pixel zone for resize handles
    MIN_FRAC = 0.04  # minimum tile size fraction
    SNAP_FRAC = 0.02         # magnetic snap threshold (fraction of canvas)
    SHAKE_WINDOW = 8         # number of recent move increments inspected for a shake
    SHAKE_FLIPS = 4          # direction reversals within the window that free the edge
    SHAKE_TRAVEL_FRAC = 0.06 # max travel (fraction) during the window to count as a shake
    TILE_COLORS = [
        QColor(0x33, 0x55, 0x88, 210), QColor(0x33, 0x77, 0x55, 210),
        QColor(0x77, 0x33, 0x55, 210), QColor(0x55, 0x33, 0x88, 210),
        QColor(0x77, 0x55, 0x33, 210), QColor(0x33, 0x66, 0x77, 210),
        QColor(0x66, 0x55, 0x22, 210), QColor(0x22, 0x55, 0x44, 210),
    ]

    def __init__(self, cam_names: list, aspects: "list | None" = None,
                 label_px: int = 28, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 320)
        self._cam_names = list(cam_names)
        # Per-tile non-image overhead (label bar + margins) in pixels — reserved at
        # the top of every tile so the previewed image area matches the live grid.
        self._label_px = max(0, int(label_px))
        # Per-camera image aspect (w/h), kept aligned with _cam_names through reorders.
        if aspects and len(aspects) == len(self._cam_names):
            self._aspects = [float(a) if a and a > 0 else 1.0 for a in aspects]
        else:
            self._aspects = [_cam_aspect_hint(n) for n in self._cam_names]
        # tiles: [x, y, w, h] all in [0.0, 1.0] as fractions of canvas
        self._tiles: list = []
        self._reset_tiles()

        self._drag_idx: int = -1
        self._drag_mode: str = ''
        self._drag_start_pos = None
        self._drag_start_tile = None
        self._selected: int = -1
        # Magnetic snapping: edges snap to neighbours unless the user "shakes" the
        # pointer (rapid back-and-forth), which frees the edge for the rest of the drag.
        self._snap_disabled = False
        self._move_hist: list = []   # recent (dx_px, dy_px) increments for shake detection
        self._last_pos = None
        self._snap_guides: list = [] # [('v', x_frac) | ('h', y_frac)] snapped this move
        self.setMouseTracking(True)

    def _reset_tiles(self):
        n = len(self._cam_names)
        self._tiles = []
        if n == 0:
            return
        # Auto-arrange = the image-area-maximising justified layout.
        W = self.width() if self.width() > 0 else 480
        H = self.height() if self.height() > 0 else 320
        entries = compute_justified_layout(self._aspects[:n], W, H, self._label_px)
        self._tiles = [[e.x, e.y, e.w, e.h] for e in entries]
        # Auto layout depends on the canvas aspect → recompute it on resize until
        # the user takes manual control (drag) or a saved/seeded layout is loaded.
        self._auto_mode = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, '_auto_mode', False):
            self._reset_tiles()
            self.update()

    def get_entries(self) -> list:
        return [CamLayoutEntry(x=t[0], y=t[1], w=t[2], h=t[3]) for t in self._tiles]

    # ── Geometry ─────────────────────────────────────────────────────────────
    def _tile_rect(self, idx: int) -> QRect:
        t = self._tiles[idx]
        W, H = self.width(), self.height()
        return QRect(int(t[0] * W), int(t[1] * H),
                     max(30, int(t[2] * W)), max(20, int(t[3] * H)))

    def _image_rect_in(self, r: QRect, aspect: float) -> QRect:
        """Sub-rectangle of tile r the camera frame actually fills: below the label
        bar (self._label_px), then KeepAspectRatio, anchored top, centred
        horizontally — mirrors the live CameraView (label header + ImageView)."""
        L = min(self._label_px, max(0, r.height() - 1))
        rx, ry = r.left(), r.top() + L
        rw, rh = r.width(), r.height() - L
        if rw <= 0 or rh <= 0 or aspect <= 0:
            return QRect(rx, ry, max(1, rw), max(1, rh))
        if rw / rh > aspect:        # region wider than image → height-limited
            ih = rh; iw = max(1, int(aspect * rh))
        else:                       # width-limited
            iw = rw; ih = max(1, int(rw / aspect))
        x0 = rx + (rw - iw) // 2
        return QRect(x0, ry, iw, ih)

    def _hit_test(self, pos) -> tuple:
        for i in range(len(self._tiles) - 1, -1, -1):
            r = self._tile_rect(i)
            if not r.contains(pos):
                continue
            rx = pos.x() - r.left()
            ry = pos.y() - r.top()
            near_left   = rx <= self.EDGE
            near_right  = (r.width()  - rx) <= self.EDGE
            near_top    = ry <= self.EDGE
            near_bottom = (r.height() - ry) <= self.EDGE
            # Corners first (higher priority than edges)
            if near_top    and near_left:  return i, 'top-left'
            if near_top    and near_right: return i, 'top-right'
            if near_bottom and near_left:  return i, 'bottom-left'
            if near_bottom and near_right: return i, 'bottom-right'
            # Edges
            if near_left:   return i, 'left'
            if near_right:  return i, 'right'
            if near_top:    return i, 'top'
            if near_bottom: return i, 'bottom'
            return i, 'move'
        return -1, ''

    # ── Snapping ───────────────────────────────────────────────────────────────
    def _is_shaking(self, W: int, H: int) -> bool:
        """True if recent motion reversed direction many times over a short travel."""
        if len(self._move_hist) < self.SHAKE_WINDOW:
            return False
        def _flips(vals):
            flips = 0; prev = 0
            for v in vals:
                s = (v > 0) - (v < 0)
                if s != 0 and prev != 0 and s != prev:
                    flips += 1
                if s != 0:
                    prev = s
            return flips
        dxs = [d[0] for d in self._move_hist]
        dys = [d[1] for d in self._move_hist]
        travel_x = sum(abs(v) for v in dxs)
        travel_y = sum(abs(v) for v in dys)
        small = max(8.0, self.SHAKE_TRAVEL_FRAC * max(W, H))
        return ((_flips(dxs) >= self.SHAKE_FLIPS and travel_x < small) or
                (_flips(dys) >= self.SHAKE_FLIPS and travel_y < small))

    def _snap_lines(self) -> tuple:
        """Candidate snap positions (x-fractions, y-fractions) from other tiles
        and the canvas borders/center, excluding the tile being dragged."""
        xs = [0.0, 0.5, 1.0]
        ys = [0.0, 0.5, 1.0]
        for j, tj in enumerate(self._tiles):
            if j == self._drag_idx:
                continue
            xs.append(tj[0]); xs.append(tj[0] + tj[2])
            ys.append(tj[1]); ys.append(tj[1] + tj[3])
        return xs, ys

    def _nearest(self, val: float, candidates: list):
        """Nearest candidate to val within SNAP_FRAC, else None."""
        best = None; best_d = self.SNAP_FRAC
        for c in candidates:
            d = abs(val - c)
            if d < best_d:
                best_d = d; best = c
        return best

    def _apply_snap(self, mode: str, t: list):
        """Snap the moved edge(s) of tile t to nearby candidate lines, recording
        guide lines in self._snap_guides for painting."""
        xs, ys = self._snap_lines()
        m = self.MIN_FRAC
        left, right = t[0], t[0] + t[2]
        top, bottom = t[1], t[1] + t[3]
        if mode == 'move':
            # Snap whichever vertical edge is closest, then translate in x.
            sl, sr = self._nearest(left, xs), self._nearest(right, xs)
            cand_x = None
            if sl is not None and (sr is None or abs(left - sl) <= abs(right - sr)):
                cand_x = (sl, sl)                 # snap left edge to sl
            elif sr is not None:
                cand_x = (sr - t[2], sr)          # snap right edge to sr
            if cand_x is not None:
                t[0] = max(0.0, min(1.0 - t[2], cand_x[0]))
                self._snap_guides.append(('v', cand_x[1]))
            st_top, sb = self._nearest(top, ys), self._nearest(bottom, ys)
            cand_y = None
            if st_top is not None and (sb is None or abs(top - st_top) <= abs(bottom - sb)):
                cand_y = (st_top, st_top)
            elif sb is not None:
                cand_y = (sb - t[3], sb)
            if cand_y is not None:
                t[1] = max(0.0, min(1.0 - t[3], cand_y[0]))
                self._snap_guides.append(('h', cand_y[1]))
            return
        # Resize modes — snap the moving edge(s), keep the opposite edge fixed.
        if 'left' in mode:
            s = self._nearest(left, xs)
            if s is not None and s <= right - m:
                t[2] = right - s; t[0] = s
                self._snap_guides.append(('v', s))
        if 'right' in mode:
            s = self._nearest(right, xs)
            if s is not None and s >= t[0] + m:
                t[2] = s - t[0]
                self._snap_guides.append(('v', s))
        if 'top' in mode:
            s = self._nearest(top, ys)
            if s is not None and s <= bottom - m:
                t[3] = bottom - s; t[1] = s
                self._snap_guides.append(('h', s))
        if 'bottom' in mode:
            s = self._nearest(bottom, ys)
            if s is not None and s >= t[1] + m:
                t[3] = s - t[1]
                self._snap_guides.append(('h', s))

    # ── Mouse ────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        idx, mode = self._hit_test(pos)
        if idx >= 0:
            self._drag_idx = idx
            self._drag_mode = mode
            self._drag_start_pos = pos
            self._drag_start_tile = list(self._tiles[idx])
            self._selected = idx
            self._auto_mode = False   # user took manual control
            # New drag — re-arm snapping and reset shake tracking.
            self._snap_disabled = False
            self._move_hist = []
            self._last_pos = pos
            self._snap_guides = []
            # bring to front
            self._tiles.append(self._tiles.pop(idx))
            self._cam_names.append(self._cam_names.pop(idx))
            # keep aspects aligned with the reordered tiles/names
            if self._aspects and idx < len(self._aspects):
                self._aspects.append(self._aspects.pop(idx))
            self._drag_idx = len(self._tiles) - 1
            self._selected = self._drag_idx
            self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._drag_idx < 0:
            _, mode = self._hit_test(pos)
            cursors = {
                'top-left':    Qt.CursorShape.SizeFDiagCursor,
                'bottom-right':Qt.CursorShape.SizeFDiagCursor,
                'corner':      Qt.CursorShape.SizeFDiagCursor,
                'top-right':   Qt.CursorShape.SizeBDiagCursor,
                'bottom-left': Qt.CursorShape.SizeBDiagCursor,
                'left':        Qt.CursorShape.SizeHorCursor,
                'right':       Qt.CursorShape.SizeHorCursor,
                'top':         Qt.CursorShape.SizeVerCursor,
                'bottom':      Qt.CursorShape.SizeVerCursor,
                'move':        Qt.CursorShape.SizeAllCursor,
            }
            self.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))
            return
        W, H = self.width(), self.height()
        if W < 1 or H < 1:
            return
        dx = (pos.x() - self._drag_start_pos.x()) / W
        dy = (pos.y() - self._drag_start_pos.y()) / H
        st = self._drag_start_tile
        t  = self._tiles[self._drag_idx]
        m  = self.MIN_FRAC
        mode = self._drag_mode
        if mode == 'move':
            t[0] = max(0.0, min(1.0 - st[2], st[0] + dx))
            t[1] = max(0.0, min(1.0 - st[3], st[1] + dy))
        elif mode == 'right':
            t[2] = max(m, min(1.0 - st[0], st[2] + dx))
        elif mode == 'bottom':
            t[3] = max(m, min(1.0 - st[1], st[3] + dy))
        elif mode in ('bottom-right', 'corner'):
            t[2] = max(m, min(1.0 - st[0], st[2] + dx))
            t[3] = max(m, min(1.0 - st[1], st[3] + dy))
        elif mode == 'left':
            new_x = max(0.0, min(st[0] + st[2] - m, st[0] + dx))
            t[2] = st[2] + (st[0] - new_x)
            t[0] = new_x
        elif mode == 'top':
            new_y = max(0.0, min(st[1] + st[3] - m, st[1] + dy))
            t[3] = st[3] + (st[1] - new_y)
            t[1] = new_y
        elif mode == 'top-left':
            new_x = max(0.0, min(st[0] + st[2] - m, st[0] + dx))
            t[2] = st[2] + (st[0] - new_x); t[0] = new_x
            new_y = max(0.0, min(st[1] + st[3] - m, st[1] + dy))
            t[3] = st[3] + (st[1] - new_y); t[1] = new_y
        elif mode == 'top-right':
            t[2] = max(m, min(1.0 - st[0], st[2] + dx))
            new_y = max(0.0, min(st[1] + st[3] - m, st[1] + dy))
            t[3] = st[3] + (st[1] - new_y); t[1] = new_y
        elif mode == 'bottom-left':
            new_x = max(0.0, min(st[0] + st[2] - m, st[0] + dx))
            t[2] = st[2] + (st[0] - new_x); t[0] = new_x
            t[3] = max(m, min(1.0 - st[1], st[3] + dy))

        # Shake-to-free: rapid back-and-forth disables snapping for the rest of the drag.
        if self._last_pos is not None:
            self._move_hist.append((pos.x() - self._last_pos.x(),
                                    pos.y() - self._last_pos.y()))
            if len(self._move_hist) > self.SHAKE_WINDOW:
                self._move_hist = self._move_hist[-self.SHAKE_WINDOW:]
            if not self._snap_disabled and self._is_shaking(W, H):
                self._snap_disabled = True
        self._last_pos = pos

        # Magnetic snapping (unless the user shook free).
        self._snap_guides = []
        if not self._snap_disabled:
            self._apply_snap(mode, t)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag_idx >= 0:
            self._drag_idx = -1
            self._drag_mode = ''
            self._snap_disabled = False
            self._move_hist = []
            self._last_pos = None
            self._snap_guides = []
            self.update()

    # ── Paint ────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(0x18, 0x18, 0x18))

        # Subtle guide lines at common fractions
        gpen = QPen(QColor(0x33, 0x33, 0x33))
        gpen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(gpen)
        for frac in (1/4, 1/3, 1/2, 2/3, 3/4):
            p.drawLine(int(frac * W), 0, int(frac * W), H)
            p.drawLine(0, int(frac * H), W, int(frac * H))

        # Canvas border
        p.setPen(QPen(QColor(0x55, 0x55, 0x55)))
        p.drawRect(0, 0, W - 1, H - 1)

        # Tiles. Each tile mirrors the live CameraView: a label header bar on top,
        # the camera frame (coloured) below it, and any leftover window space shown
        # as grey — exactly the grey letterbox the user sees in the grid. When the
        # window matches the image aspect, the frame fills it and no grey shows.
        font = p.font()
        for i, tile in enumerate(self._tiles):
            r = self._tile_rect(i)
            color = self.TILE_COLORS[i % len(self.TILE_COLORS)]
            sel = (i == self._selected)
            L = min(self._label_px, max(0, r.height() - 1))
            # Window background = grey letterbox area (matches the grid's dark bg)
            p.fillRect(r, QColor(0x22, 0x22, 0x22))
            # Label header bar with the descriptive camera name
            if L > 0:
                hdr = QRect(r.left(), r.top(), r.width(), L)
                p.fillRect(hdr, QColor(0x44, 0x44, 0x44))
                font.setPixelSize(max(9, min(L - 6, 15)))
                p.setFont(font)
                p.setPen(QColor(0xee, 0xee, 0xee))
                p.drawText(hdr.adjusted(5, 0, -5, 0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           _cam_short_label(self._cam_names[i]))
            # Image area (the actual frame): fills its region when window matches aspect
            aspect = self._aspects[i] if i < len(self._aspects) else 1.0
            img_r = self._image_rect_in(r, aspect)
            p.fillRect(img_r, color)
            ip = QPen(QColor(0xcc, 0xdd, 0xff, 220))
            ip.setStyle(Qt.PenStyle.DotLine); ip.setWidth(1)
            p.setPen(ip); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(img_r)
            # Window (tile) border
            bp = QPen(QColor(0x44, 0xaa, 0xff) if sel else QColor(0x88, 0xbb, 0xff))
            bp.setWidth(2 if sel else 1)
            p.setPen(bp)
            p.drawRect(r)
            # Resize handles — L-shapes at all 4 corners + tick on all 4 edges
            hp = QPen(QColor(0xff, 0xcc, 0x00, 200))
            hp.setWidth(3); p.setPen(hp)
            e = self.EDGE
            # Corners: top-left
            p.drawLine(r.left(), r.top(), r.left() + e, r.top())
            p.drawLine(r.left(), r.top(), r.left(), r.top() + e)
            # top-right
            p.drawLine(r.right() - e, r.top(), r.right(), r.top())
            p.drawLine(r.right(), r.top(), r.right(), r.top() + e)
            # bottom-left
            p.drawLine(r.left(), r.bottom() - e, r.left(), r.bottom())
            p.drawLine(r.left(), r.bottom(), r.left() + e, r.bottom())
            # bottom-right
            p.drawLine(r.right() - e, r.bottom(), r.right(), r.bottom())
            p.drawLine(r.right(), r.bottom() - e, r.right(), r.bottom())
            # Edge midpoints (short ticks)
            mx, my = r.center().x(), r.center().y()
            hp2 = QPen(QColor(0xff, 0xcc, 0x00, 120)); hp2.setWidth(2); p.setPen(hp2)
            p.drawLine(mx - e // 2, r.top(),    mx + e // 2, r.top())     # top
            p.drawLine(mx - e // 2, r.bottom(), mx + e // 2, r.bottom())  # bottom
            p.drawLine(r.left(),    my - e // 2, r.left(),    my + e // 2) # left
            p.drawLine(r.right(),   my - e // 2, r.right(),   my + e // 2) # right

        # Magnetic snap guide lines (drawn while a snapped edge is active)
        if self._snap_guides:
            sp = QPen(QColor(0x44, 0xff, 0x88, 220)); sp.setWidth(1)
            p.setPen(sp)
            for kind, frac in self._snap_guides:
                if kind == 'v':
                    x = int(frac * W); p.drawLine(x, 0, x, H)
                else:
                    y = int(frac * H); p.drawLine(0, y, W, y)

        p.end()


class LayoutConfigDialog(QDialog):
    """Dialog for configuring the camera grid layout (drag & resize cameras interactively)."""

    _LAYOUTS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "cam_layouts.json"

    def __init__(self, cam_names: list, parent=None, initial_entries=None,
                 cam_aspects=None, label_px: int = 28):
        super().__init__(parent)
        self.setWindowTitle("Configure Camera Layout")
        self.resize(680, 500)
        self._cam_names = list(cam_names)
        # name → aspect (w/h); used to realign aspects after a saved reorder
        self._aspect_map = {}
        if cam_aspects and len(cam_aspects) == len(self._cam_names):
            self._aspect_map = {n: float(a) for n, a in zip(self._cam_names, cam_aspects)}
        aspects = [self._aspect_map.get(n, _cam_aspect_hint(n)) for n in self._cam_names]

        lay = QVBoxLayout(self)

        # ── Canvas ──
        self._canvas = _LayoutCanvasWidget(cam_names=self._cam_names, aspects=aspects,
                                           label_px=label_px, parent=self)
        saved = self._load_saved()
        if saved and "tiles" in saved and len(saved["tiles"]) == len(cam_names):
            self._canvas._tiles = [list(t) for t in saved["tiles"]]
            self._canvas._auto_mode = False   # explicit saved layout — keep as-is
            # Restore cam_names order from saved (bring-to-front reorders them)
            if "cam_order" in saved and len(saved["cam_order"]) == len(cam_names):
                self._canvas._cam_names = list(saved["cam_order"])
                # Realign aspects to the restored camera order
                self._canvas._aspects = [
                    self._aspect_map.get(n, _cam_aspect_hint(n))
                    for n in self._canvas._cam_names]
        elif initial_entries and len(initial_entries) == len(cam_names):
            # No saved layout — seed from current on-screen camera positions
            self._canvas._tiles = [[e.x, e.y, e.w, e.h] for e in initial_entries]
            self._canvas._auto_mode = False
        lay.addWidget(self._canvas, stretch=1)

        hint = QLabel("Drag interior to move  ·  drag edge/corner to resize  ·  "
                      "solid = window, dotted = image area  ·  shake an edge to disable snapping")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        lay.addWidget(hint)

        # ── Bottom row ──
        bot = QHBoxLayout()
        btn_reset = QPushButton("Auto-arrange")
        btn_reset.setToolTip("Auto-arrange cameras to maximise each frame's image area")
        btn_reset.clicked.connect(self._reset_to_default)
        bot.addWidget(btn_reset)
        bot.addStretch()
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        bot.addWidget(btns)
        lay.addLayout(bot)

    def _reset_to_default(self):
        self._canvas._cam_names = list(self._cam_names)
        # Realign aspects to the original camera order before re-arranging.
        self._canvas._aspects = [
            self._aspect_map.get(n, _cam_aspect_hint(n)) for n in self._cam_names]
        self._canvas._reset_tiles()
        self._canvas._selected = -1
        self._canvas.update()

    def get_config(self) -> CamLayoutConfig:
        # Re-map entries back to original camera order
        name_to_entry = {
            name: CamLayoutEntry(x=t[0], y=t[1], w=t[2], h=t[3])
            for name, t in zip(self._canvas._cam_names, self._canvas._tiles)
        }
        entries = [name_to_entry.get(n, CamLayoutEntry()) for n in self._cam_names]
        return CamLayoutConfig(entries=entries)

    # ── Persistence ──────────────────────────────────────────────────────────
    def _layout_key(self) -> str:
        return ",".join(sorted(self._cam_names))

    def _load_saved(self) -> dict:
        try:
            if self._LAYOUTS_PATH.exists():
                data = json.loads(self._LAYOUTS_PATH.read_text(encoding="utf-8"))
                return data.get(self._layout_key(), {})
        except Exception:
            pass
        return {}

    @classmethod
    def load_config_for_names(cls, cam_names: list) -> "CamLayoutConfig | None":
        """Return saved CamLayoutConfig for cam_names, or None if nothing saved."""
        key = ",".join(sorted(cam_names))
        try:
            if cls._LAYOUTS_PATH.exists():
                data = json.loads(cls._LAYOUTS_PATH.read_text(encoding="utf-8"))
                saved = data.get(key, {})
                if saved and "tiles" in saved and len(saved["tiles"]) == len(cam_names):
                    cam_order = saved.get("cam_order", list(cam_names))
                    if len(cam_order) != len(cam_names):
                        cam_order = list(cam_names)
                    name_to_tile = dict(zip(cam_order, saved["tiles"]))
                    entries = []
                    for n in cam_names:
                        t = name_to_tile.get(n)
                        if t is not None:
                            entries.append(CamLayoutEntry(x=t[0], y=t[1], w=t[2], h=t[3]))
                        else:
                            entries.append(CamLayoutEntry())
                    return CamLayoutConfig(entries=entries)
        except Exception:
            pass
        return None

    def save_config(self, cfg: CamLayoutConfig):
        try:
            self._LAYOUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data: dict = {}
            if self._LAYOUTS_PATH.exists():
                try:
                    data = json.loads(self._LAYOUTS_PATH.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[self._layout_key()] = {
                "tiles":     [list(t) for t in self._canvas._tiles],
                "cam_order": list(self._canvas._cam_names),
                "entries":   [_asdict(e) for e in cfg.entries],
            }
            self._LAYOUTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# ---------------- CAMERA PICKER DIALOG ----------------
# ---------------- CAMERA PICKER DIALOG ----------------
class _CamLoaderSignals(QObject):
    finished = Signal(list, str)   # (cameras, status: "" = ok, "no_data", "error")

class CameraPickerDialog(QDialog):
    _PRESETS_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "cam_presets.json"

    def __init__(self, date_obj, hour_from: int, hour_to: int,
                 last_cam_names: list[str], parent=None,
                 preloaded_cameras: list | None = None,
                 multi_grid=None, windows: "list | None" = None):
        super().__init__(parent)
        self.setWindowTitle("Select cameras")
        self.resize(660, 640)

        self._all_cam_data: list[tuple[str, str]] = []
        self._selected_names: list[str] = list(last_cam_names) if last_cam_names else []
        self._date_obj = date_obj
        self._hour_from = hour_from
        self._hour_to = hour_to
        # Every window of the pick (one per day/segment). The camera list is the
        # UNION over all of them, so an empty day cannot hide the cameras.
        self._windows = list(windows) if windows else None
        self._presets: dict[str, list[str]] = self._load_presets()
        self._multi_grid = multi_grid

        lay = QVBoxLayout(self)

        # ── Top row: search + camera list  |  presets panel ──────────────────
        top_row = QHBoxLayout()

        # Left: search + camera list
        left = QVBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search cameras…")
        self._search_edit.textChanged.connect(self._filter_cameras)
        left.addWidget(self._search_edit)

        self._cam_list = QTableWidget(0, 2)
        self._cam_list.setHorizontalHeaderLabels(["#", "Camera"])
        self._cam_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._cam_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._cam_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cam_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._cam_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cam_list.verticalHeader().setVisible(False)
        self._cam_list.cellClicked.connect(self._on_cam_clicked)
        left.addWidget(self._cam_list, 1)

        self._status_lbl = QLabel("Loading cameras…")
        self._status_lbl.setStyleSheet("font-size: 10px; color: #555;")
        left.addWidget(self._status_lbl)

        top_row.addLayout(left, 3)

        # Right: presets panel
        right = QVBoxLayout()
        preset_lbl = QLabel("Presets")
        preset_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #333;")
        right.addWidget(preset_lbl)

        self._preset_list = QTableWidget(0, 1)
        self._preset_list.setHorizontalHeaderLabels(["Name"])
        self._preset_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._preset_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._preset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._preset_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preset_list.verticalHeader().setVisible(False)
        self._preset_list.itemSelectionChanged.connect(self._on_preset_load)
        right.addWidget(self._preset_list, 1)

        btn_save   = QPushButton("Save")
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

        # ── Selected cameras table ────────────────────────────────────────────
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

        # ── Layout config button + OK/Cancel ─────────────────────────────────
        self._layout_config: "CamLayoutConfig | None" = None
        bottom_row = QHBoxLayout()
        self._btn_layout = QPushButton("Layout…")
        self._btn_layout.setToolTip("Configure custom grid layout for the selected cameras")
        self._btn_layout.clicked.connect(self._on_layout_clicked)
        bottom_row.addWidget(self._btn_layout)
        bottom_row.addStretch()
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)
        lay.addLayout(bottom_row)

        self._refresh_preset_list()

        self._signals = _CamLoaderSignals()
        self._signals.finished.connect(self._on_cameras_loaded)
        if preloaded_cameras:
            # Non-empty preload from the background scan — use it directly.
            QTimer.singleShot(0, lambda: self._on_cameras_loaded(list(preloaded_cameras)))
        else:
            # No preload, or it came back EMPTY (slow / failed / timed-out background
            # scan). Load the camera list ourselves so the dialog is never stuck on an
            # empty list — previously an empty preload skipped this fallback entirely.
            self._load_cameras_async()

        self._refresh_sel_table()

    # ── Presets ───────────────────────────────────────────────────────────────
    def _load_presets(self) -> dict:
        try:
            if self._PRESETS_PATH.exists():
                return json.loads(self._PRESETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_presets(self):
        try:
            self._PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._PRESETS_PATH.write_text(
                json.dumps(self._presets, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _refresh_preset_list(self):
        self._preset_list.setRowCount(0)
        for name in sorted(self._presets.keys(), key=str.lower):
            r = self._preset_list.rowCount()
            self._preset_list.insertRow(r)
            self._preset_list.setItem(r, 0, QTableWidgetItem(name))

    def _selected_preset_name(self) -> str | None:
        rows = self._preset_list.selectedItems()
        return rows[0].text() if rows else None

    def _on_preset_load(self, *_):
        name = self._selected_preset_name()
        if not name or name not in self._presets:
            return
        self._selected_names = list(self._presets[name])
        self._refresh_sel_table()
        self._highlight_selected()

    def _on_preset_save(self):
        from PySide6.QtWidgets import QInputDialog
        current = self._selected_preset_name() or ""
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:", text=current)
        if not ok or not name.strip():
            return
        name = name.strip()
        self._presets[name] = list(self._selected_names)
        self._save_presets()
        self._refresh_preset_list()
        # select the just-saved preset
        for r in range(self._preset_list.rowCount()):
            if self._preset_list.item(r, 0).text() == name:
                self._preset_list.selectRow(r)
                break

    def _on_preset_rename(self):
        from PySide6.QtWidgets import QInputDialog
        name = self._selected_preset_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "Rename preset", "New name:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name:
            return
        new_name = new_name.strip()
        self._presets[new_name] = self._presets.pop(name)
        self._save_presets()
        self._refresh_preset_list()

    def _on_preset_delete(self):
        name = self._selected_preset_name()
        if not name:
            return
        reply = QMessageBox.question(self, "Delete preset",
                                     f"Delete preset '{name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._presets.pop(name, None)
        self._save_presets()
        self._refresh_preset_list()

    def _load_cameras_async(self):
        import threading as _thr
        windows = self._windows or [
            seg_bounds_ns(PickSeg(self._date_obj, self._hour_from, 0, self._hour_to, 0))]
        signals = self._signals

        def worker():
            try:
                cameras, status = cameras_for_windows(windows)
            except Exception:
                cameras, status = [], "error"
            signals.finished.emit(cameras, status)

        _thr.Thread(target=worker, daemon=True).start()

    def _on_cameras_loaded(self, cameras: list, status: str = ""):
        self._all_cam_data = cameras
        self._populate_cam_table(cameras)
        n = len(cameras)
        if n:
            self._status_lbl.setText(f"{n} cameras available.")
            self._status_lbl.setStyleSheet("font-size: 10px; color: #555;")
        elif status == "error":
            # Couldn't read the archive at all → software / access problem.
            self._status_lbl.setText("⚠ Could not read the camera archive (network / path error).")
            self._status_lbl.setStyleSheet("font-size: 10px; color: #c0392b; font-weight: 700;")
        else:
            # Archive reachable, just no camera folders anywhere in the selection
            # (every day / segment was scanned, not only the first one).
            self._status_lbl.setText(
                "No camera records in any of the selected days / times."
                if len(self._windows or []) > 1 else
                "No camera records for this date / time.")
            self._status_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._highlight_selected()

    def _populate_cam_table(self, cameras: list):
        self._cam_list.setRowCount(0)
        for num, name in cameras:
            r = self._cam_list.rowCount()
            self._cam_list.insertRow(r)
            self._cam_list.setItem(r, 0, QTableWidgetItem(num))
            self._cam_list.setItem(r, 1, QTableWidgetItem(name))
        self._highlight_selected()

    def _highlight_selected(self):
        """Zvýrazní vybrané kamery v hlavní tabulce."""
        for r in range(self._cam_list.rowCount()):
            item = self._cam_list.item(r, 1)
            if item is None:
                continue
            selected = item.text() in self._selected_names
            bg = QColor("#d0e8ff") if selected else QColor("#ffffff")
            for c in range(self._cam_list.columnCount()):
                cell = self._cam_list.item(r, c)
                if cell:
                    cell.setBackground(bg)

    def _on_cam_clicked(self, row: int, col: int):
        item = self._cam_list.item(row, 1)
        if item is None:
            return
        name = item.text()
        if name in self._selected_names:
            self._selected_names.remove(name)
        else:
            self._selected_names.append(name)
        self._highlight_selected()
        self._refresh_sel_table()
        self._cam_list.clearSelection()

    def _refresh_sel_table(self):
        self._sel_table.setRowCount(0)
        for i, name in enumerate(self._selected_names):
            r = self._sel_table.rowCount()
            self._sel_table.insertRow(r)
            item = QTableWidgetItem(name)
            self._sel_table.setItem(r, 0, item)
            btn = QPushButton("✕")
            btn.setFixedSize(24, 24)
            btn.setStyleSheet("font-size: 10px; padding: 0;")
            btn.clicked.connect(lambda checked, n=name: self._remove_selected(n))
            self._sel_table.setCellWidget(r, 1, btn)

    def _remove_selected(self, name: str):
        if name in self._selected_names:
            self._selected_names.remove(name)
        self._refresh_sel_table()
        self._highlight_selected()

    def _filter_cameras(self, text: str):
        q = text.strip().lower()
        filtered = [(num, name) for num, name in self._all_cam_data
                    if not q or q in name.lower() or q in num.lower()]
        self._populate_cam_table(filtered)

    def _on_layout_clicked(self):
        if not self._selected_names:
            QMessageBox.information(self, "No cameras selected",
                "Select at least one camera before configuring the layout.")
            return
        # Read current on-screen positions as fallback initial tiles
        initial_entries = None
        if (self._multi_grid is not None and
                len(self._selected_names) == len(getattr(self._multi_grid, '_cam_names_list', []))):
            initial_entries = self._multi_grid.get_current_layout_entries(self._selected_names)
        # Build per-camera aspect ratios: prefer the live frame's aspect, else a
        # name-based hint, so the editor can preview each camera's real image area.
        live_aspects = {}
        if self._multi_grid is not None:
            for cv in getattr(self._multi_grid, '_cam_views', []):
                pm = getattr(cv.img_view, '_pix', None)
                if pm is not None and not pm.isNull() and pm.height() > 0:
                    live_aspects[cv.cam_name] = pm.width() / pm.height()
        cam_aspects = [live_aspects.get(n, _cam_aspect_hint(n)) for n in self._selected_names]
        # Label-bar overhead, queried from a live CameraView so the editor reserves
        # the same header space the grid does (keeps preview matched to reality).
        label_px = 28
        if self._multi_grid is not None:
            for cv in getattr(self._multi_grid, '_cam_views', []):
                try:
                    label_px = cv.image_overhead_px()
                    break
                except Exception:
                    pass
        dlg = LayoutConfigDialog(self._selected_names, parent=self,
                                 initial_entries=initial_entries, cam_aspects=cam_aspects,
                                 label_px=label_px)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            dlg.save_config(cfg)
            self._layout_config = cfg

    def _on_accept(self):
        if not self._selected_names:
            QMessageBox.warning(self, "No camera", "Please select at least one camera.")
            return
        # If user didn't explicitly configure layout this session, auto-load any saved layout
        if self._layout_config is None and len(self._selected_names) > 1:
            self._layout_config = LayoutConfigDialog.load_config_for_names(self._selected_names)
        self.accept()

    @property
    def layout_config(self) -> "CamLayoutConfig | None":
        return self._layout_config

    def selected_camera_names(self) -> list[str]:
        return list(self._selected_names)

    def all_selected_camera_names(self) -> list[str]:
        return list(self._selected_names)

# ---------------- COMBOBOX ----------------
class PopupBelowComboBox(QComboBox):
    def showPopup(self):
        super().showPopup()
        view = self.view(); popup = view.window()
        if popup is None: return
        gpos = self.mapToGlobal(self.rect().bottomLeft())
        x, y = gpos.x(), gpos.y(); pw, ph = popup.width(), popup.height()
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center())) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        if x + pw > geo.right(): x = max(geo.left(), geo.right() - pw)
        if x < geo.left(): x = geo.left()
        if y + ph > geo.bottom(): y = max(geo.top(), geo.bottom() - ph)
        popup.move(x, y)

    def wheelEvent(self, event):
        event.ignore()  # Ignoruj scroll kolečkem


# ---------------- CIRCLE FIT ----------------
def _fit_circle_kasa(points):
    n = len(points)
    if n < 30: return None
    sx = sy = sxx = syy = sxy = sxz = syz = sz = 0.0
    for x, y in points:
        xx, yy = x*x, y*y; z = xx+yy
        sx+=x; sy+=y; sxx+=xx; syy+=yy; sxy+=x*y; sz+=z; sxz+=x*z; syz+=y*z
    det = sxx*(syy*n-sy*sy) - sxy*(sxy*n-sy*sx) + sx*(sxy*sy-syy*sx)
    if abs(det) < 1e-9: return None
    def d3(a,b,c,d,e,f,g,h,i): return a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)
    a = d3(sxz,sxy,sx,syz,syy,sy,sz,sy,n)/det
    b = d3(sxx,sxz,sx,sxy,syz,sy,sx,sz,n)/det
    c = d3(sxx,sxy,sxz,sxy,syy,syz,sx,sy,sz)/det
    cx, cy = a/2.0, b/2.0
    r2 = c + (a*a+b*b)/4.0
    if r2 <= 2.0: return None
    return cx, cy, r2**0.5


# ---------------- IMAGE VIEW ----------------
class ImageView(QWidget):
    # Zoom changed (set or reset). The owner re-renders the frame at the resolution the
    # new zoom needs: zooming crops the pixmap ALREADY held, so without this a settled
    # frame refined to REFINE_MAX_SIDE stayed at that size and a zoomed-in crop was an
    # upscale of it — visibly soft — until the user stepped to a different frame.
    zoom_changed = Signal()
    # First frame of a camera (or one whose resolution changed) has arrived and its
    # aspect differs from what the tile geometry was computed from. The auto layout
    # listens so tiles resize to the real frame instead of the name-based hint.
    frame_aspect_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pix: QPixmap | None = None
        self._aspect_seen: float | None = None   # aspect last reported to the layout
        self._scaled: QPixmap | None = None
        self.bg_color: QColor = QColor("#f3f3f3")  # overrideable per-instance

        self.show_cross  = False
        self.cross_size  = 18
        self.cross_thickness = 2
        self.cross_pos_norm: QPointF | None = None
        self._draw_mode: str = ""
        self.energy_text: str = ""
        self.timestamp_text: str = ""   # shown as large white overlay in top-left
        self.cam_label_text: str = ""   # camera name label below image
        self.cam_ts_text: str = ""      # timestamp label below image
        self.cam_ref_text: str = ""     # subtraction-reference badge (same as multi-cam tiles)
        self.cam_label_font_px: int = 12  # controlled by Label size spinbox

        self.show_circle = False
        self.circle_center_norm: QPointF | None = None
        self.circle_r_norm: float | None = None
        self.circle_rx_norm: float | None = None  # normalizováno přes šířku obrazu
        self.circle_ry_norm: float | None = None  # normalizováno přes výšku obrazu
        # draw state
        self._drag_start: QPointF | None = None
        self._drag_handle: str = ""   # "" | "move" | "n" | "s" | "e" | "w" | "nw" | "ne" | "sw" | "se"

        self.cross_color     = QColor(0, 255, 0, 220)
        self.circle_color    = QColor(255, 255, 0, 230)
        self.circle_thick    = 2
        self.square_color    = QColor(0, 200, 255, 230)
        self.square_thick    = 2

        self.show_square = False
        # stored as (left_norm, top_norm, right_norm, bottom_norm) — all in [0,1]
        self.square_rect_norm: tuple[float, float, float, float] | None = None

        # Top-N SC pixel markers: list of (nx, ny) normalized coords, or None
        self.sc_topn_points_norm: "list[tuple[float,float]] | None" = None

        # When True (default): labels are drawn as overlay inside the image.
        # When False: bottom space is reserved and labels drawn below the image.
        self.cam_label_use_overlay: bool = True

        # When True: draw a configurable reference grid for PDXM1 cameras.
        self.show_pdxm1_grid: bool = False
        self.pdxm1_cam_name: str = ''   # full camera name used to look up per-type grid config
        self._pdxm1_cfg_override = None  # set by Pdxm1GridConfigDialog for live preview

        # Zoom: normalized rect (ln, tn, rn, bn) inside the source image, or None = no zoom
        self._zoom_norm: "tuple[float,float,float,float] | None" = None
        # Right-click rubber-band state
        self._rb_start: "QPoint | None" = None
        self._rb_current: "QPoint | None" = None

        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_pixmap(self, pm: QPixmap):
        self._pix = pm; self._scaled = None; self.update()
        # Learn the camera's true aspect from the frame itself. Both checks are a
        # float compare, so the drag path pays nothing: the signal fires only when
        # a camera's aspect actually changes — in practice its first frame.
        if pm is not None and not pm.isNull() and pm.height() > 0:
            a = pm.width() / pm.height()
            if self._aspect_seen is None or abs(self._aspect_seen - a) > 0.01:
                self._aspect_seen = a
                remember_cam_aspect(self.pdxm1_cam_name, pm.width(), pm.height())
                self.frame_aspect_changed.emit()

    def clear(self):
        self._pix = None; self._scaled = None; self.update()

    def set_zoom(self, zoom_norm: "tuple[float,float,float,float] | None"):
        changed = self._zoom_norm != zoom_norm
        self._zoom_norm = zoom_norm
        self._scaled = None
        self.update()
        if changed:
            self.zoom_changed.emit()

    def reset_zoom(self):
        self.set_zoom(None)

    def _name_bar_h(self) -> int:
        """Height of the camera-name / timestamp strip."""
        return max(8, self.cam_label_font_px) + 10

    def _ref_bar_h(self) -> int:
        """Height of the reference badge strip (0 when no reference is set)."""
        if not self.cam_ref_text:
            return 0
        return max(8, self.cam_label_font_px - 1) + 8

    def _label_bar_h(self) -> int:
        """Total height reserved for the label strips above the image (0 in overlay mode)."""
        if self.cam_label_use_overlay:
            return 0
        return self._name_bar_h() + self._ref_bar_h()

    def set_cam_ref_text(self, text: str):
        """Set/clear the subtraction-reference badge (single-cam layout counterpart of
        CameraView.set_ref_status — the two layouts must show the same information)."""
        text = text or ""
        if text == self.cam_ref_text:
            return
        self.cam_ref_text = text
        self._scaled = None   # reserved strip height changed → re-scale the frame
        self.update()

    def _ensure_scaled(self):
        if self._pix is None or self._pix.isNull():
            self._scaled = None; return
        lbh = self._label_bar_h()
        avail_h = max(10, self.height() - lbh)
        if self.width() <= 10 or avail_h <= 10:
            self._scaled = None; return
        target = QSize(self.width(), avail_h)
        if self._scaled is not None and self._scaled.size() == target:
            return
        src = self._pix
        if self._zoom_norm is not None:
            ln, tn, rn, bn = self._zoom_norm
            pw, ph = src.width(), src.height()
            cx = int(ln * pw); cy = int(tn * ph)
            cw = max(1, int((rn - ln) * pw)); ch = max(1, int((bn - tn) * ph))
            src = src.copy(cx, cy, cw, ch)
        self._scaled = src.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)

    def resizeEvent(self, event):
        super().resizeEvent(event); self._scaled = None; self.update()

    def set_draw_mode(self, mode: str):
        self._draw_mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode else Qt.CursorShape.ArrowCursor)
        self.update()

    def _img_rect(self) -> QRect | None:
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            return None
        lbh = self._label_bar_h()
        x0 = (self.width() - self._scaled.width()) // 2
        # Image starts immediately below the label strip; any leftover space is at the bottom.
        y0 = lbh
        return QRect(x0, y0, self._scaled.width(), self._scaled.height())

    def _handle_radius(self) -> int:
        return 7

    def _circle_handles(self, ir: QRect) -> dict:
        """Vrátí handlery pro ellipsu: střed + 4 okraje."""
        if self.circle_center_norm is None: return {}
        cx = ir.left() + int(self.circle_center_norm.x() * ir.width())
        cy = ir.top()  + int(self.circle_center_norm.y() * ir.height())
        rx = int((self.circle_rx_norm or 0) * ir.width())
        ry = int((self.circle_ry_norm or 0) * ir.height())
        return {
            "move": QPointF(cx, cy),
            "n":    QPointF(cx, cy - ry),
            "s":    QPointF(cx, cy + ry),
            "e":    QPointF(cx + rx, cy),
            "w":    QPointF(cx - rx, cy),
        }

    def _square_handles(self, ir: QRect) -> dict:
        """Vrátí handlery pro obdélník: střed + 4 rohy + 4 hrany."""
        if self.square_rect_norm is None: return {}
        ln, tn, rn, bn = self.square_rect_norm
        sx = ir.left() + int(ln * ir.width())
        sy = ir.top()  + int(tn * ir.height())
        ex = ir.left() + int(rn * ir.width())
        ey = ir.top()  + int(bn * ir.height())
        mx, my = (sx + ex) // 2, (sy + ey) // 2
        return {
            "move": QPointF(mx, my),
            "nw": QPointF(sx, sy), "ne": QPointF(ex, sy),
            "sw": QPointF(sx, ey), "se": QPointF(ex, ey),
            "n":  QPointF(mx, sy), "s":  QPointF(mx, ey),
            "w":  QPointF(sx, my), "e":  QPointF(ex, my),
        }

    def _hit_handle(self, pos: QPointF, handles: dict) -> str:
        r = self._handle_radius() + 3
        for name, pt in handles.items():
            if abs(pos.x() - pt.x()) <= r and abs(pos.y() - pt.y()) <= r:
                return name
        return ""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            # Start rubber-band tracking; whether to zoom or open dialog is decided
            # in mouseReleaseEvent: drag → zoom, single click → grid config dialog.
            ir = self._img_rect()
            if ir is not None and ir.width() > 0 and ir.height() > 0:
                self._rb_start = event.position().toPoint()
                self._rb_current = self._rb_start
                self._rb_dragged = False
                self.update()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event); return
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0:
            super().mousePressEvent(event); return
        pos = event.position()

        if self._draw_mode == "cross":
            self.cross_pos_norm = QPointF(
                max(0.0, min(1.0, (pos.x() - ir.left()) / ir.width())),
                max(0.0, min(1.0, (pos.y() - ir.top())  / ir.height()))
            )
            self.update(); return

        if self._draw_mode == "circle" and self.circle_center_norm is not None \
                and self.circle_rx_norm is not None:
            hit = self._hit_handle(pos, self._circle_handles(ir))
            if hit:
                self._drag_handle = hit
                self._drag_start = pos
                return
            # klik mimo handlery = začít kreslit nový
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "circle":
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "square" and self.square_rect_norm is not None:
            hit = self._hit_handle(pos, self._square_handles(ir))
            if hit:
                self._drag_handle = hit
                self._drag_start = pos
                return
            self._drag_handle = "new"
            self._drag_start = pos
            return

        if self._draw_mode == "square":
            self._drag_handle = "new"
            self._drag_start = pos
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rb_start is not None:
            self._rb_current = event.position().toPoint()
            if not getattr(self, '_rb_dragged', False):
                dx = self._rb_current.x() - self._rb_start.x()
                dy = self._rb_current.y() - self._rb_start.y()
                if dx * dx + dy * dy > 25:  # >5 px movement = drag
                    self._rb_dragged = True
            self.update()
            return
        ir = self._img_rect()
        if ir is None or ir.width() <= 0 or ir.height() <= 0: return
        pos = event.position()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # kurzor podle handleru
        if self._draw_mode in ("circle", "square") and not self._drag_handle:
            handles = self._circle_handles(ir) if self._draw_mode == "circle" else self._square_handles(ir)
            hit = self._hit_handle(pos, handles)
            if hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif hit in ("n", "s"):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif hit in ("e", "w"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit in ("nw", "se"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ("ne", "sw"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

        if not (event.buttons() & Qt.MouseButton.LeftButton): return
        if self._drag_start is None: return

        def clamp_norm(v): return max(0.0, min(1.0, v))
        def to_norm(px, py):
            return clamp_norm((px - ir.left()) / ir.width()), clamp_norm((py - ir.top()) / ir.height())

        # ── CIRCLE ──────────────────────────────────────────────────────
        if self._draw_mode == "circle":
            if self._drag_handle == "new":
                # tažení = střed je start, poloměr = vzdálenost
                x0, y0 = self._drag_start.x(), self._drag_start.y()
                dx = (pos.x() - x0) / ir.width()
                dy = (pos.y() - y0) / ir.height()
                if shift:
                    r = max(abs(dx), abs(dy))
                    rx_n, ry_n = r, r
                else:
                    rx_n, ry_n = abs(dx), abs(dy)
                self.circle_center_norm = QPointF(*to_norm(x0, y0))
                self.circle_rx_norm = rx_n
                self.circle_ry_norm = ry_n
                self.circle_r_norm  = max(rx_n, ry_n)

            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                cx = clamp_norm(self.circle_center_norm.x() + dx)
                cy = clamp_norm(self.circle_center_norm.y() + dy)
                self.circle_center_norm = QPointF(cx, cy)

            elif self._drag_handle in ("e", "w"):
                cx_px = ir.left() + self.circle_center_norm.x() * ir.width()
                rx_n = abs(pos.x() - cx_px) / ir.width()
                if shift: self.circle_ry_norm = rx_n
                self.circle_rx_norm = rx_n
                self.circle_r_norm  = max(self.circle_rx_norm, self.circle_ry_norm)

            elif self._drag_handle in ("n", "s"):
                cy_px = ir.top() + self.circle_center_norm.y() * ir.height()
                ry_n = abs(pos.y() - cy_px) / ir.height()
                if shift: self.circle_rx_norm = ry_n
                self.circle_ry_norm = ry_n
                self.circle_r_norm  = max(self.circle_rx_norm, self.circle_ry_norm)

            self.update()

        # ── SQUARE ──────────────────────────────────────────────────────
        elif self._draw_mode == "square":
            if self._drag_handle == "new":
                # Střed je drag_start, roztahuje se symetricky na obě strany
                cx0, cy0 = to_norm(self._drag_start.x(), self._drag_start.y())
                nx1, ny1 = to_norm(pos.x(), pos.y())
                dx = abs(nx1 - cx0)
                dy = abs(ny1 - cy0)
                if shift:
                    side = max(dx, dy)
                    dx, dy = side, side
                self.square_rect_norm = (
                    max(0.0, cx0 - dx), max(0.0, cy0 - dy),
                    min(1.0, cx0 + dx), min(1.0, cy0 + dy)
                )

            elif self._drag_handle == "move":
                dx = (pos.x() - self._drag_start.x()) / ir.width()
                dy = (pos.y() - self._drag_start.y()) / ir.height()
                self._drag_start = pos
                ln, tn, rn, bn = self.square_rect_norm
                w_ = rn - ln; h_ = bn - tn
                ln = clamp_norm(ln + dx); tn = clamp_norm(tn + dy)
                self.square_rect_norm = (ln, tn,
                    clamp_norm(ln + w_), clamp_norm(tn + h_))

            else:
                ln, tn, rn, bn = self.square_rect_norm
                h = self._drag_handle
                nx, ny = to_norm(pos.x(), pos.y())
                if "w" in h: ln = min(nx, rn - 0.01)
                if "e" in h: rn = max(nx, ln + 0.01)
                if "n" in h: tn = min(ny, bn - 0.01)
                if "s" in h: bn = max(ny, tn + 0.01)
                if shift:
                    # uniform scale od protějšího rohu
                    if h in ("se",): side = max(rn - ln, bn - tn); rn = ln + side; bn = tn + side
                    elif h in ("nw",): side = max(rn - ln, bn - tn); ln = rn - side; tn = bn - side
                    elif h in ("ne",): side = max(rn - ln, bn - tn); rn = ln + side; tn = bn - side
                    elif h in ("sw",): side = max(rn - ln, bn - tn); ln = rn - side; bn = tn + side
                self.square_rect_norm = (
                    clamp_norm(ln), clamp_norm(tn),
                    clamp_norm(rn), clamp_norm(bn)
                )

            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._rb_start is not None:
            end = event.position().toPoint()
            rb_start_saved = self._rb_start
            rb_dragged = getattr(self, '_rb_dragged', False)
            self._rb_start = None
            self._rb_current = None
            self._rb_dragged = False

            if rb_dragged:
                # Drag: perform rubber-band zoom
                ir = self._img_rect()
                if ir is not None and ir.width() > 0 and ir.height() > 0:
                    rb = QRect(rb_start_saved, end).normalized()
                    rb = rb.intersected(ir)
                    if rb.width() > 4 and rb.height() > 4:
                        ln_new = (rb.left()   - ir.left()) / ir.width()
                        tn_new = (rb.top()    - ir.top())  / ir.height()
                        rn_new = (rb.right()  - ir.left()) / ir.width()
                        bn_new = (rb.bottom() - ir.top())  / ir.height()
                        if self._zoom_norm is not None:
                            zl, zt, zr, zb = self._zoom_norm
                            zw, zh = zr - zl, zb - zt
                            ln_new = zl + ln_new * zw
                            tn_new = zt + tn_new * zh
                            rn_new = zl + rn_new * zw
                            bn_new = zt + bn_new * zh
                        self.set_zoom((
                            max(0.0, min(1.0, ln_new)), max(0.0, min(1.0, tn_new)),
                            max(0.0, min(1.0, rn_new)), max(0.0, min(1.0, bn_new))
                        ))
            else:
                # Single click: open grid config dialog for any camera
                cam_name = getattr(self, 'pdxm1_cam_name', '')
                if cam_name:
                    dlg = Pdxm1GridConfigDialog(cam_name, img_view=self, parent=self)
                    if dlg.exec() == QDialog.DialogCode.Accepted:
                        dlg.save_config()

            self.update()
            return
        self._rb_start = None
        self._rb_current = None
        self._drag_handle = ""
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg_color)
        if self._pix is None or self._pix.isNull():
            p.end(); return
        self._ensure_scaled()
        if self._scaled is None or self._scaled.isNull():
            p.end(); return

        pm = self._scaled
        lbh = self._label_bar_h()
        x0 = (self.width() - pm.width()) // 2
        y0 = lbh  # image starts immediately below label bar; leftover space at bottom
        p.drawPixmap(x0, y0, pm)
        img_rect = QRect(x0, y0, pm.width(), pm.height())
        # Rámeček kolem obrázku
        border_pen = QPen(QColor(80, 80, 80, 160))
        border_pen.setWidth(1)
        p.setPen(border_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(img_rect)

        if self.show_cross:
            if self.cross_pos_norm is not None:
                cx = img_rect.left() + int(self.cross_pos_norm.x() * img_rect.width())
                cy = img_rect.top()  + int(self.cross_pos_norm.y() * img_rect.height())
            else:
                cx = img_rect.center().x(); cy = img_rect.center().y()
            pen = QPen(self.cross_color); pen.setWidth(self.cross_thickness); p.setPen(pen)
            p.drawLine(cx - self.cross_size, cy, cx + self.cross_size, cy)
            p.drawLine(cx, cy - self.cross_size, cx, cy + self.cross_size)

        if self.show_circle and self.circle_center_norm is not None:
            nx, ny = self.circle_center_norm.x(), self.circle_center_norm.y()
            cx = img_rect.left() + int(nx * img_rect.width())
            cy = img_rect.top()  + int(ny * img_rect.height())
            # použij rx/ry pokud jsou k dispozici (přesné), jinak fallback na r
            if self.circle_rx_norm is not None and self.circle_ry_norm is not None:
                rx = int(self.circle_rx_norm * img_rect.width())
                ry = int(self.circle_ry_norm * img_rect.height())
            else:
                rx = ry = int(self.circle_r_norm * min(img_rect.width(), img_rect.height()))
            pen = QPen(self.circle_color); pen.setWidth(self.circle_thick); p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if self._draw_mode == "circle":
                for pt in self._circle_handles(img_rect).values():
                    p.setPen(QPen(self.circle_color))
                    p.setBrush(QColor(self.circle_color.red(), self.circle_color.green(),
                                      self.circle_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._handle_radius(),
                                  int(pt.y()) - self._handle_radius(),
                                  self._handle_radius()*2, self._handle_radius()*2)

        if self.show_square and self.square_rect_norm is not None:
            # square_rect_norm = (left_norm, top_norm, right_norm, bottom_norm)
            ln, tn, rn, bn = self.square_rect_norm
            sx = img_rect.left() + int(ln * img_rect.width())
            sy = img_rect.top()  + int(tn * img_rect.height())
            sw = int((rn - ln) * img_rect.width())
            sh = int((bn - tn) * img_rect.height())
            pen = QPen(self.square_color); pen.setWidth(self.square_thick); p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sx, sy, sw, sh)
            if self._draw_mode == "square":
                for pt in self._square_handles(img_rect).values():
                    p.setPen(QPen(self.square_color))
                    p.setBrush(QColor(self.square_color.red(), self.square_color.green(),
                                      self.square_color.blue(), 120))
                    p.drawEllipse(int(pt.x()) - self._handle_radius(),
                                  int(pt.y()) - self._handle_radius(),
                                  self._handle_radius()*2, self._handle_radius()*2)
        if self.energy_text and not self._pix.isNull():
            from PySide6.QtGui import QFont, QFontMetrics
            available_w = img_rect.width() - 20
            font = QFont()
            display_text = self.energy_text

            # Zkus vejít na jeden řádek
            fitted = False
            for fsize in range(22, 8, -1):
                font.setPixelSize(fsize)
                fm = QFontMetrics(font)
                if fm.horizontalAdvance(self.energy_text) <= available_w:
                    fitted = True
                    break

            if not fitted:
                # Rozděl na dva řádky podle " | "
                parts_split = self.energy_text.split("  |  ")
                mid = len(parts_split) // 2
                display_text = "  |  ".join(parts_split[:mid]) + "\n" + "  |  ".join(parts_split[mid:])
                font.setPixelSize(12)
                for fsize in range(18, 8, -1):
                    font.setPixelSize(fsize)
                    fm = QFontMetrics(font)
                    max_line = max(fm.horizontalAdvance(l) for l in display_text.split("\n"))
                    if max_line <= available_w:
                        break

            fm = QFontMetrics(font)
            line_count = display_text.count("\n") + 1
            bar_h = max(28, fm.height() * line_count + 12)
            bar_top = img_rect.bottom()
            bar_h = min(bar_h, max(0, self.height() - bar_top))
            bar_rect = QRect(img_rect.left(), bar_top,
                             img_rect.width(), bar_h)
            p.fillRect(bar_rect, QColor(255, 255, 255, 220))
            p.setFont(font)
            p.setPen(QColor(0, 0, 0))
            p.drawText(bar_rect, Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter, display_text)

        # Camera name + timestamp labels — SAME look as the multi-cam tiles:
        # name left (1/3, #eee on #444), timestamp right (2/3, #ffd54f on #333,
        # font 1 px smaller).
        # overlay mode (multi-cam grid tiles): semi-transparent over the image top.
        # non-overlay mode (single-cam): drawn in the strip reserved ABOVE the
        #   image, exactly abutting the image top edge (no gap, no overlap),
        #   spanning the image width.
        if (self.cam_label_text or self.cam_ts_text or self.cam_ref_text) and not self._pix.isNull():
            from PySide6.QtGui import QFont as _QFont
            _fpx = max(8, self.cam_label_font_px)
            lbl_x = img_rect.left()
            lbl_w = img_rect.width()
            if self.cam_label_use_overlay:
                lbl_h = self._name_bar_h()
                # Semi-transparent strip at the very top of the image rect
                lbl_y = img_rect.top()
                lbl_y = min(lbl_y, img_rect.bottom() - lbl_h)
            else:
                # Drawn height == reserved height → strip ends exactly at the
                # image top edge (the old "-1" left a visible gap line).
                lbl_h = self._name_bar_h()
                lbl_y = max(0, img_rect.top() - self._label_bar_h())
            name_w = lbl_w // 3
            ts_w = lbl_w - name_w
            font = _QFont(); font.setPixelSize(_fpx)
            p.setFont(font)
            if self.cam_label_text:
                name_rect = QRect(lbl_x, lbl_y, name_w, lbl_h)
                p.fillRect(name_rect, QColor(0x44, 0x44, 0x44, 220 if self.cam_label_use_overlay else 255))
                p.setPen(QColor(0xee, 0xee, 0xee))
                p.drawText(name_rect.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           self.cam_label_text)
            if self.cam_ts_text:
                ts_rect = QRect(lbl_x + name_w, lbl_y, ts_w, lbl_h)
                p.fillRect(ts_rect, QColor(0x33, 0x33, 0x33, 220 if self.cam_label_use_overlay else 255))
                p.setPen(QColor(0xff, 0xd5, 0x4f))
                ts_font = _QFont(); ts_font.setPixelSize(max(8, _fpx - 1))
                p.setFont(ts_font)
                p.drawText(ts_rect.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           self.cam_ts_text)
            # Reference badge — own centered strip right below the name/ts strip,
            # same colours as CameraView._ref_lbl in the multi-cam tiles.
            if self.cam_ref_text:
                ref_h = self._ref_bar_h()
                ref_rect = QRect(lbl_x, lbl_y + lbl_h, lbl_w, ref_h)
                p.fillRect(ref_rect, QColor(0xc8, 0xe6, 0xc9,
                                            220 if self.cam_label_use_overlay else 255))
                p.setPen(QColor(0x22, 0x22, 0x22))
                ref_font = _QFont(); ref_font.setPixelSize(max(8, _fpx - 1))
                p.setFont(ref_font)
                p.drawText(ref_rect.adjusted(4, 0, -4, 0),
                           Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                           self.cam_ref_text)

        # Spatial contrast top-N pixel markers
        if getattr(self, 'sc_topn_points_norm', None) and not self._pix.isNull():
            r = getattr(self, 'sc_topn_marker_radius', max(3, img_rect.width() // 150))
            thick = getattr(self, 'sc_topn_marker_thick', 2)
            p.setPen(QPen(QColor(255, 80, 0, 230), thick))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for nx, ny in self.sc_topn_points_norm:
                cx = img_rect.left() + int(nx * img_rect.width())
                cy = img_rect.top()  + int(ny * img_rect.height())
                p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Camera grid overlay (configurable via right-click → Grid Config…)
        if self.show_pdxm1_grid and img_rect is not None and not self._pix.isNull():
            from PySide6.QtGui import QFont as _GFont
            _gcam = getattr(self, 'pdxm1_cam_name', '')
            if _gcam:
                _gcfg = (self._pdxm1_cfg_override if self._pdxm1_cfg_override is not None
                         else get_pdxm1_grid_config(_gcam))
            else:
                _gcfg = Pdxm1GridConfig()
            divs_col  = _gcfg.effective_col_dividers()
            divs_row  = _gcfg.effective_row_dividers()
            col_labels = _gcfg.effective_col_labels()
            row_labels = _gcfg.effective_row_labels()
            # Border pixel positions
            _gx = img_rect.left() + int(_gcfg.grid_left   * img_rect.width())
            _gy = img_rect.top()  + int(_gcfg.grid_top    * img_rect.height())
            _gw = max(10, int((_gcfg.grid_right  - _gcfg.grid_left)  * img_rect.width()))
            _gh = max(10, int((_gcfg.grid_bottom - _gcfg.grid_top)   * img_rect.height()))
            # Inner divider pixel positions (absolute image fractions)
            col_xs = ([_gx]
                      + [img_rect.left() + int(d * img_rect.width()) for d in divs_col]
                      + [_gx + _gw])
            row_ys = ([_gy]
                      + [img_rect.top() + int(d * img_rect.height()) for d in divs_row]
                      + [_gy + _gh])
            # Grid lines
            lc = QColor(_gcfg.line_color); lc.setAlpha(_gcfg.line_alpha)
            grid_pen = QPen(lc); grid_pen.setWidth(max(1, _gcfg.line_width))
            p.setPen(grid_pen)
            for x in col_xs:
                p.drawLine(x, _gy, x, _gy + _gh)
            for y in row_ys:
                p.drawLine(_gx, y, _gx + _gw, y)
            # Labels
            n_cols = len(col_xs) - 1
            fsize = _gcfg.font_size if _gcfg.font_size > 0 else max(8, min(18, int(_gw / max(1, n_cols) * 0.45)))
            gfont = _GFont(); gfont.setPixelSize(fsize); gfont.setBold(True)
            fc = QColor(_gcfg.font_color); fc.setAlpha(_gcfg.font_alpha)
            for ci, lbl in enumerate(col_labels):
                if ci + 1 >= len(col_xs): break
                cw = col_xs[ci + 1] - col_xs[ci]
                ch = row_ys[1] - row_ys[0] if len(row_ys) > 1 else _gh
                _draw_outlined_text(p, QRect(col_xs[ci], _gy, cw, int(ch * 0.4)),
                                    Qt.AlignmentFlag.AlignCenter, lbl,
                                    gfont, fc, _gcfg.font_outline)
            for ri, lbl in enumerate(row_labels):
                if ri + 1 >= len(row_ys): break
                rh = row_ys[ri + 1] - row_ys[ri]
                rw = col_xs[1] - col_xs[0] if len(col_xs) > 1 else _gw
                _draw_outlined_text(p, QRect(_gx, row_ys[ri], int(rw * 0.4), rh),
                                    Qt.AlignmentFlag.AlignCenter, lbl,
                                    gfont, fc, _gcfg.font_outline)

        # Zoom rubber-band
        if self._rb_start is not None and self._rb_current is not None:
            rb = QRect(self._rb_start, self._rb_current).normalized()
            pen = QPen(QColor(255, 200, 0, 220)); pen.setWidth(2); pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QBrush(QColor(255, 200, 0, 30)))
            p.drawRect(rb)

        p.end()

    # ------------------------------------------------------------------ circle
    def calibrate_circle_from_pixmap(self) -> bool:
        if self._calibrate_circle_hard_impl():
            return True
        return self._calibrate_circle_soft()

    def _calibrate_circle_hard_impl(self) -> bool:
        if self._pix is None or self._pix.isNull(): return False
        pm = self._pix; w0, h0 = pm.width(), pm.height()    
        if w0 <= 0 or h0 <= 0: return False

        target = 700; scale = max(w0, h0) / target
        small = pm.toImage().scaled(int(w0/scale), int(h0/scale),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ) if scale > 1.0 else pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 0.2, 99.8)
        w, h = g.width(), g.height()
        if w < 100 or h < 100: return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"): ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy()
        
        # Centroid celého obrazu (bez ořezu) — robustnější pro velké kruhy
        x1 = int(w * CIRCLE_SEARCH_REGION); x2 = int(w * (1 - CIRCLE_SEARCH_REGION))
        y1 = int(h * CIRCLE_SEARCH_REGION); y2 = int(h * (1 - CIRCLE_SEARCH_REGION))

        region = arr[y1:y2, x1:x2]
        # Použij střední jas (50..95 percentil) jako váhy — ignoruje tmavé pozadí i přesycené skvrny
        lo = np.percentile(region, 30)
        hi = np.percentile(region, CIRCLE_BRIGHT_PERCENTILE * 100)
        clipped = np.clip(region.astype(float), lo, hi) - lo
        total_w = clipped.sum()
        if total_w < 1.0:
            cx0, cy0 = w * 0.5, h * 0.5
        else:
            ys_g, xs_g = np.mgrid[y1:y2, x1:x2]
            cx0 = float((clipped * xs_g).sum() / total_w)
            cy0 = float((clipped * ys_g).sum() / total_w)

        r_min = int(min(w, h) * CIRCLE_R_MIN_FRAC)
        r_max = int(min(w, h) * CIRCLE_R_MAX_FRAC)
        if r_max <= r_min + 10: return False

        def sample(px, py):
            ix, iy = int(round(px)), int(round(py))
            if ix < 0 or iy < 0 or ix >= w or iy >= h: return 0
            return int(arr[iy, ix])

        pts = []
        for deg in range(0, 360, 2):
            ang = math.radians(deg); ca, sa = math.cos(ang), math.sin(ang)
            prof, rr_list = [], []
            for rr in range(r_min, r_max + 1):
                x = cx0 + rr*ca; y = cy0 + rr*sa
                if x < 1 or y < 1 or x >= w-1 or y >= h-1: break
                prof.append(sample(x, y)); rr_list.append(rr)
            if len(prof) < 20: continue
            sm = [sum(prof[max(0,i-2):min(len(prof),i+3)]) / len(prof[max(0,i-2):min(len(prof),i+3)]) for i in range(len(prof))]
            best_i, best_drop = -1, 0.0
            for i in range(2, len(sm) - 3):
                inside  = (sm[i-2]+sm[i-1]+sm[i])/3.0
                outside = (sm[i+1]+sm[i+2]+sm[i+3])/3.0
                drop = inside - outside
                if drop > best_drop and inside > 80: best_drop = drop; best_i = i
            if best_i < 0 or best_drop < CIRCLE_MIN_DROP: continue
            rr = rr_list[best_i]; pts.append((float(cx0+rr*ca), float(cy0+rr*sa)))

        if len(pts) < CIRCLE_MIN_POINTS: return False
        fit = _fit_circle_kasa(pts)
        if fit is None: return False
        cx, cy, r = fit

        pts2 = []; band = max(8, int(r * 0.08))
        for deg in range(0, 360, 2):
            ang = math.radians(deg); ca, sa = math.cos(ang), math.sin(ang)
            best_rr, best_drop = None, 0.0
            for rr in range(max(5, int(r - band)), int(r + band) + 1):
                x = cx+rr*ca; y = cy+rr*sa
                if x < 2 or y < 2 or x >= w-2 or y >= h-2: continue
                inside  = (sample(cx+(rr-2)*ca,cy+(rr-2)*sa)+sample(cx+(rr-1)*ca,cy+(rr-1)*sa)+sample(cx+rr*ca,cy+rr*sa))/3.0
                outside = (sample(cx+rr*ca,cy+rr*sa)+sample(cx+(rr+1)*ca,cy+(rr+1)*sa)+sample(cx+(rr+2)*ca,cy+(rr+2)*sa))/3.0
                drop = inside - outside
                if drop > best_drop and inside > 80: best_drop = drop; best_rr = rr
            if best_rr is not None and best_drop >= CIRCLE_REFINE_DROP:
                pts2.append((float(cx+best_rr*ca), float(cy+best_rr*sa)))

        if len(pts2) >= CIRCLE_MIN_POINTS:
            fit2 = _fit_circle_kasa(pts2)
            if fit2 is not None: cx, cy, r = fit2

        if r < min(w,h)*CIRCLE_R_MIN_FRAC or r > min(w,h)*CIRCLE_R_MAX_FRAC: return False
        r *= 1.04  # soft edge kompenzace — hrana je měkká, fit leží těsně uvnitř
        self.circle_center_norm = QPointF(cx / w, cy / h)
        self.circle_r_norm  = r / min(w, h)
        self.circle_rx_norm = r / w
        self.circle_ry_norm = r / h
        return True

    def _calibrate_circle_hard(self) -> bool:
        return self._calibrate_circle_hard_impl()

    def _calibrate_circle_soft(self) -> bool:   
        """
        Alternativní kalibrace kruhu pro kamery s měkkým přechodem.
        Hledá poloměr kde průměrný jas klesne na CIRCLE_SOFT_PERCENTILE * maximum.
        """
        if self._pix is None or self._pix.isNull(): return False
        pm = self._pix; w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0: return False

        target = 700; scale = max(w0, h0) / target
        small = pm.toImage().scaled(int(w0/scale), int(h0/scale),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ) if scale > 1.0 else pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 0.2, 99.8)
        w, h = g.width(), g.height()
        if w < 100 or h < 100: return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"): ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy()

        # Těžiště jasu jako odhad středu
        arr_f = arr.astype(np.float32)
        total = arr_f.sum()
        if total < 1.0: return False
        ys, xs = np.mgrid[0:h, 0:w]
        cx0 = float((arr_f * xs).sum() / total)
        cy0 = float((arr_f * ys).sum() / total)

        # Radiální profil — průměrný jas v každém poloměru
        r_max = int(min(w, h) * CIRCLE_SOFT_MAX_R_FRAC)
        r_min = int(min(w, h) * CIRCLE_SOFT_MIN_R_FRAC)
        if r_max <= r_min + 10: return False

        # Vzorkuj radiální profil (průměr přes 360 úhlů)
        n_angles = 180
        angles = np.linspace(0, 2 * np.pi, n_angles, endpoint=False)
        cos_a = np.cos(angles); sin_a = np.sin(angles)

        profile = np.zeros(r_max + 1, dtype=np.float32)
        counts  = np.zeros(r_max + 1, dtype=np.int32)
        for rr in range(r_min, r_max + 1):
            xs_r = (cx0 + rr * cos_a).astype(int)
            ys_r = (cy0 + rr * sin_a).astype(int)
            mask = (xs_r >= 0) & (xs_r < w) & (ys_r >= 0) & (ys_r < h)
            if mask.sum() < 10: continue
            profile[rr] = arr[ys_r[mask], xs_r[mask]].mean()
            counts[rr] = 1

        valid = np.where(counts[r_min:r_max+1] > 0)[0] + r_min
        if len(valid) < 10: return False

        prof_valid = profile[valid]
        peak = prof_valid.max()
        if peak < 10: return False

        threshold = peak * CIRCLE_SOFT_PERCENTILE

        # Najdi první poloměr kde jas klesne pod threshold (zvenku dovnitř)
        # Hledáme přechod zprava (velký r) doleva
        edge_r = None
        for i in range(len(valid) - 1, -1, -1):
            if prof_valid[i] >= threshold:
                edge_r = valid[i]
                break

        if edge_r is None: return False
        if edge_r < r_min or edge_r > r_max: return False

        r = float(edge_r)
        self.circle_center_norm = QPointF(cx0 / w, cy0 / h)
        self.circle_r_norm  = r / min(w, h)
        self.circle_rx_norm = r / w
        self.circle_ry_norm = r / h
        return True
    
    def calibrate_cross_from_pixmap(self) -> bool:
        if self._pix is None or self._pix.isNull():
            return False
        pm = self._pix
        w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0:
            return False

        # Downscale pro rychlost
        target = 400
        scale = max(w0, h0) / target
        if scale > 1.0:
            small = pm.toImage().scaled(
                int(w0 / scale), int(h0 / scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            small = pm.toImage()

        g = small.convertToFormat(QImage.Format.Format_Grayscale8)
        g = _autostretch_gray(g, 1.0, 99.0)
        w, h = g.width(), g.height()
        if w < 10 or h < 10:
            return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy().astype(np.float32)

        # Těžiště intenzity (weighted centroid)
        total = arr.sum()
        if total < 1.0:
            return False

        ys, xs = np.mgrid[0:h, 0:w]
        cx = float((arr * xs).sum() / total)
        cy = float((arr * ys).sum() / total)

        self.cross_pos_norm = QPointF(cx / w, cy / h)
        return True

    # ------------------------------------------------------------------ square
    def calibrate_square_from_pixmap(self) -> bool:
        """
        Detects the bright rectangular region using gradient-based edge detection
        on row and column projections. Stores result as (left_norm, top_norm, right_norm, bottom_norm).
        """
        if self._pix is None or self._pix.isNull():
            return False

        pm = self._pix
        w0, h0 = pm.width(), pm.height()
        if w0 <= 0 or h0 <= 0:
            return False

        # Downscale for speed
        target = 700
        scale = max(w0, h0) / target
        if scale > 1.0:
            small_img = pm.toImage().scaled(
                int(w0 / scale), int(h0 / scale),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
        else:
            small_img = pm.toImage()

        g = small_img.convertToFormat(QImage.Format.Format_Grayscale8)
        # Mild stretch so contrast is visible even in dark images
        g = _autostretch_gray(g, 1.0, 99.0)
        w, h = g.width(), g.height()
        if w < 50 or h < 50:
            return False

        ptr = g.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(g.sizeInBytes())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, g.bytesPerLine())[:, :w].copy().astype(np.float32)

        # ---- smooth with a simple box kernel ----
        def smooth1d(a, k=7):
            kernel = np.ones(k, dtype=np.float32) / k
            return np.convolve(a, kernel, mode='same')

        # Row projection: mean brightness per row → find top/bottom edges
        row_proj = smooth1d(arr.mean(axis=1))
        # Col projection: mean brightness per col → find left/right edges
        col_proj = smooth1d(arr.mean(axis=0))

        def find_outer_edges(proj: np.ndarray, margin_frac: float = 0.04) -> tuple[int, int] | None:
            n = len(proj)
            margin = max(2, int(n * margin_frac))
            grad = np.gradient(proj)

            search_grad = grad[margin: n - margin]
            if len(search_grad) < 10:
                return None
            abs_grad = np.abs(search_grad)
            if abs_grad.max() < 0.5:
                return None

            # Peak rising (dark→bright) = největší kladný gradient
            rising_peak = int(np.argmax(search_grad)) + margin
            # Peak falling (bright→dark) = největší záporný gradient
            falling_peak = int(np.argmin(search_grad)) + margin

            if falling_peak <= rising_peak:
                return None
            if (falling_peak - rising_peak) < int(n * 0.15):
                return None

            # Kompenzace měkkého přechodu: posun hrany ven o půl šířky gradientu
            # Šířka = vzdálenost kde gradient > 50% peak hodnoty
            def edge_halfwidth(g_section, peak_sign):
                peak_val = g_section.max() if peak_sign > 0 else g_section.min()
                mask = (g_section * peak_sign) > abs(peak_val) * 0.5
                return max(1, int(mask.sum() / 2))

            hw_rise  = edge_halfwidth(search_grad[:rising_peak  - margin + 10], +1)
            hw_fall  = edge_halfwidth(search_grad[falling_peak  - margin - 10:], -1)

            left_edge  = max(margin, rising_peak  - hw_rise)
            right_edge = min(n - 1,  falling_peak + hw_fall)

            if right_edge <= left_edge:
                return None
            if (right_edge - left_edge) < int(n * 0.15):
                return None

            return left_edge, right_edge

        col_edges = find_outer_edges(col_proj)   # left, right  in pixel-x
        row_edges = find_outer_edges(row_proj)   # top,  bottom in pixel-y

        if col_edges is None or row_edges is None:
            return False

        left,  right  = col_edges
        top,   bottom = row_edges

        # Normalise to [0, 1] relative to full image size
        left_n   = left   / w
        right_n  = right  / w
        top_n    = top    / h
        bottom_n = bottom / h

        # Sanity checks
        if (right_n - left_n) < 0.10 or (bottom_n - top_n) < 0.10:
            return False
        if (right_n - left_n) > 0.99 or (bottom_n - top_n) > 0.99:
            return False

        self.square_rect_norm = (left_n, top_n, right_n, bottom_n)
        return True

# ---------------- MULTI CAMERA GRID ----------------
class CameraView(QWidget):
    """Jeden panel v multi-camera gridu — ImageView + label + výběr."""
    clicked = Signal(int)  # camera index

    # Three states, because there are three genuinely different situations and folding
    # the middle one into either neighbour is what made this label useless:
    #   OK     amber  — showing exactly the frame the slider asked for
    #   APPROX blue   — showing the nearest PRELOADED frame, within the stated tolerance.
    #                   Normal, designed behaviour while the window is still preloading;
    #                   the text carries a leading "~" so it does not depend on colour.
    #   STALE  red    — the preview refused and no load has landed: really behind.
    # Same size and padding throughout so nothing reflows when the state flips.
    _TS_STYLE_OK = ("font-size: 11px; color: #ffd54f; background: #333; "
                    "padding: 2px 4px; border-radius: 2px;")
    _TS_STYLE_APPROX = ("font-size: 11px; color: #90caf9; background: #333; "
                        "padding: 2px 4px; border-radius: 2px;")
    _TS_STYLE_STALE = ("font-size: 11px; color: #ffffff; background: #a02020; "
                       "padding: 2px 4px; border-radius: 2px;")

    def __init__(self, cam_index: int, cam_name: str, parent=None):
        super().__init__(parent)
        self.cam_index = cam_index
        self.cam_name  = cam_name
        self._selected = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        # Top row: camera name | timestamp (above image)
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.setContentsMargins(0, 0, 0, 0)

        self._name_lbl = QLabel(_strip_cam_name(cam_name))
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._name_lbl.setStyleSheet(
            "font-size: 12px; color: #eee; background: #444; "
            "padding: 2px 4px; border-radius: 2px;")
        top_row.addWidget(self._name_lbl, 1)

        self._ts_lbl = QLabel("")
        self._ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._ts_kind = 0        # 0 exact / 1 nearest preloaded / 2 behind
        self._ts_lbl.setStyleSheet(self._TS_STYLE_OK)
        top_row.addWidget(self._ts_lbl, 2)

        self._refresh_dot = QLabel()
        self._refresh_dot.setFixedSize(12, 12)
        self._refresh_dot.setStyleSheet("background: #444; border-radius: 6px;")
        self._refresh_dot.setToolTip(
            "Camera refresh indicator — green: new frames are arriving, "
            "red: live mode on but no new frame, grey: live mode off")
        top_row.addWidget(self._refresh_dot)

        lay.addLayout(top_row)

        self._ref_lbl = QLabel("")
        self._ref_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ref_lbl.setStyleSheet(
            "font-size: 11px; color: #222; background: #c8e6c9; "
            "padding: 1px 4px; border-radius: 2px;")
        self._ref_lbl.hide()
        lay.addWidget(self._ref_lbl)

        self.img_view = ImageView(self)
        self.img_view.bg_color = QColor("#222")
        lay.addWidget(self.img_view, 1)

        self._update_border()

    def image_overhead_px(self) -> int:
        """Vertical pixels of a tile NOT used by the image (label header + layout
        margins + spacing). The justified layout reserves this so the frame fills
        the window with no grey, and the layout editor previews the same split."""
        lay = self.layout()
        m = lay.contentsMargins()
        overhead = m.top() + m.bottom() + self._name_lbl.sizeHint().height() + lay.spacing()
        if self._ref_lbl.isVisible():
            overhead += self._ref_lbl.sizeHint().height() + lay.spacing()
        return int(overhead)

    def set_timestamp(self, text: str, stale: bool = False, approx: bool = False):
        """Timestamp of the frame ACTUALLY ON SCREEN — never of the one requested.

        `stale` means this tile is behind what the slider is asking for; `approx` means it
        is showing the nearest PRELOADED frame instead of the exact one. Both colour the
        label, and `approx` also prefixes it with "~", so the distinction survives for
        anyone who cannot pick blue out of amber. Setting the label at request time was
        the original bug: the timestamps marched on across every tile while the pictures
        sat still."""
        kind = 2 if stale else (1 if approx else 0)
        want = ("~" + text) if kind == 1 else text
        if want != self._ts_lbl.text():
            self._ts_lbl.setText(want)
        if kind == getattr(self, "_ts_kind", None):
            return
        self._ts_kind = kind
        self._ts_lbl.setStyleSheet(
            (self._TS_STYLE_STALE, self._TS_STYLE_APPROX, self._TS_STYLE_OK)[2 - kind])

    def set_stale(self, stale: bool, approx: bool = False):
        """Re-colour the timestamp WITHOUT touching its text.

        This is all _cam_refresh_stale_marks ever wanted, and routing it through
        set_timestamp meant re-running fmt_hhmmss_ms_from_ns for every tile on every
        call — which, before the navigation tick existed, happened once per camera per
        mouse-move event, i.e. O(cameras^2) formats per event, all of it discarded by
        set_timestamp's own text-equality check."""
        kind = 2 if stale else (1 if approx else 0)
        if kind == getattr(self, "_ts_kind", None):
            return
        # The "~" prefix belongs to the approx state, so a state change has to fix the
        # text too — otherwise a tile that recovers keeps a tilde it no longer earns.
        txt = self._ts_lbl.text().lstrip("~")
        self._ts_lbl.setText(("~" + txt) if kind == 1 else txt)
        self._ts_kind = kind
        self._ts_lbl.setStyleSheet(
            (self._TS_STYLE_STALE, self._TS_STYLE_APPROX, self._TS_STYLE_OK)[2 - kind])

    def pulse_refresh_dot(self, blink_on: bool, is_main: bool = False,
                          fresh: bool = True, tip: str = ""):
        """Blink the dot. fresh=True → green (image is updating), fresh=False →
        red (live mode on, but this camera's image is not updating). `tip` is the
        tooltip text explaining which — built by the caller, which owns the state."""
        size = 18 if is_main else 12  # větší kruh pro hlavní kameru
        if fresh:
            if is_main:
                color = "#55ff44" if blink_on else "#22aa22"
            else:
                color = "#22dd22" if blink_on else "#0a5a0a"
        else:
            if is_main:
                color = "#ff5544" if blink_on else "#aa2222"
            else:
                color = "#dd2222" if blink_on else "#5a0a0a"
        half = size // 2
        self._refresh_dot.setFixedSize(size, size)
        self._refresh_dot.setStyleSheet(f"background: {color}; border-radius: {half}px;")
        if tip:
            self._refresh_dot.setToolTip(tip)

    def dim_refresh_dot(self):
        self._refresh_dot.setFixedSize(12, 12)
        self._refresh_dot.setStyleSheet("background: #444; border-radius: 6px;")
        self._refresh_dot.setToolTip("Live mode off")

    def set_label_font_size(self, px: int):
        self._name_lbl.setStyleSheet(
            f"font-size: {px}px; color: #eee; background: #444; "
            "padding: 2px 4px; border-radius: 2px;")
        # Rebuild ALL THREE timestamp styles at the new size and keep whichever is in
        # force, so a font-size change cannot silently turn a stale (red) or approximate
        # (blue) label back to normal.
        ts_px = max(8, px - 1)
        self._TS_STYLE_OK = (f"font-size: {ts_px}px; color: #ffd54f; background: #333; "
                             "padding: 2px 4px; border-radius: 2px;")
        self._TS_STYLE_APPROX = (f"font-size: {ts_px}px; color: #90caf9; background: #333; "
                                 "padding: 2px 4px; border-radius: 2px;")
        self._TS_STYLE_STALE = (f"font-size: {ts_px}px; color: #ffffff; background: #a02020; "
                                "padding: 2px 4px; border-radius: 2px;")
        self._ts_lbl.setStyleSheet(
            (self._TS_STYLE_STALE, self._TS_STYLE_APPROX,
             self._TS_STYLE_OK)[2 - getattr(self, "_ts_kind", 0)])
        self._ref_lbl.setStyleSheet(
            f"font-size: {max(8, px - 1)}px; color: #222; background: #c8e6c9; "
            "padding: 1px 4px; border-radius: 2px;")

    def set_ref_status(self, text: str):
        if text:
            self._ref_lbl.setText(text)
            self._ref_lbl.show()
        else:
            self._ref_lbl.hide()

    def set_selected(self, sel: bool):
        self._selected = sel
        self._update_border()

    def _update_border(self):
        if self._selected:
            self.setStyleSheet(
                "CameraView { border: 3px solid #2d7dff; border-radius: 3px; background: #1a2a3a; }")
        else:
            self.setStyleSheet(
                "CameraView { border: 2px solid #555; border-radius: 3px; background: #222; }")

    def mousePressEvent(self, event):
        # Grid config dialog is opened by ImageView.mouseReleaseEvent on single right-click.
        self.clicked.emit(self.cam_index)
        super().mousePressEvent(event)

    def _open_pdxm1_grid_config(self):
        dlg = Pdxm1GridConfigDialog(self.img_view.pdxm1_cam_name, img_view=self.img_view, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.save_config()
            self.img_view.update()


class _FreeLayoutContainer(QWidget):
    """Positions CameraView widgets using absolute geometry from CamLayoutEntry fractions."""

    def __init__(self, entries: list, views: list, parent=None):
        super().__init__(parent)
        self._entries = entries
        self._views   = views
        for v in views:
            v.setParent(self)
            v.show()
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        W, H = self.width(), self.height()
        if W < 1 or H < 1:
            return
        for e, v in zip(self._entries, self._views):
            v.setGeometry(int(e.x * W), int(e.y * H),
                          max(20, int(e.w * W)), max(20, int(e.h * H)))


class _JustifiedRowsContainer(QWidget):
    """Default auto layout: positions CameraView widgets in image-area-maximising
    justified rows, recomputed responsively on every resize so each camera's frame
    stays as large as possible regardless of window proportions (see
    compute_justified_layout). Per-camera aspect comes from the live frame when
    available, else a name-based hint."""

    def __init__(self, views: list, parent=None):
        super().__init__(parent)
        self._views = views
        for v in views:
            v.setParent(self)
            v.show()
            # A tile laid out from the name hint must be re-laid out as soon as the
            # camera's real frame shows its true aspect, else the arrangement stays
            # the suboptimal one until the window is resized or the layout editor's
            # auto-arrange is used.
            v.img_view.frame_aspect_changed.connect(self._apply)
        self._apply()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _aspects(self) -> list:
        out = []
        for v in self._views:
            pm = getattr(v.img_view, '_pix', None)
            if pm is not None and not pm.isNull() and pm.height() > 0:
                out.append(pm.width() / pm.height())
            else:
                out.append(_cam_aspect_hint(v.cam_name))
        return out

    def detach(self):
        """Drop the aspect subscriptions before this container is replaced, so a
        stale instance can never re-position tiles that now belong to a new one."""
        for v in self._views:
            try:
                v.img_view.frame_aspect_changed.disconnect(self._apply)
            except Exception:
                pass

    def _apply(self):
        W, H = self.width(), self.height()
        if W < 1 or H < 1 or not self._views:
            return
        if self._views[0].parentWidget() is not self:
            return   # tiles have moved to another container — not ours to lay out
        top_px = self._views[0].image_overhead_px()
        entries = compute_justified_layout(self._aspects(), W, H, top_px)
        if len(entries) != len(self._views):
            return
        for e, v in zip(entries, self._views):
            v.setGeometry(int(e.x * W), int(e.y * H),
                          max(20, int(e.w * W)), max(20, int(e.h * H)))


class MultiCameraGrid(QWidget):
    """
    Grid zobrazení pro 2–4 kamery.
    Layout:
      - vertikální snímky (h > w): 4×1 (vedle sebe)
      - čtvercové / horizontální:  2×2
    Layout se volí automaticky: PDxM1 kamery → pravý sloupec (portrét), ostatní → levá strana (2×N).
    """
    camera_selected = Signal(int)  # index naposledy kliknuté kamery

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cam_views: list[CameraView] = []
        self._selected_idx: int = 0          # naposledy kliknutá kamera
        self._selected_set: set[int] = set() # všechny aktuálně vybrané kamery
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._reg_container: "QWidget | None" = None  # sub-grid for regular cams in mixed layout
        self._overlay_store: dict[str, dict] = {}  # cam_name → overlay state
        self._layout_config = None  # CamLayoutConfig or None

    @staticmethod
    def _save_iv_overlay(iv: "ImageView") -> dict:
        return {
            "show_cross":          iv.show_cross,
            "cross_pos_norm":      iv.cross_pos_norm,
            "show_circle":         iv.show_circle,
            "circle_center_norm":  iv.circle_center_norm,
            "circle_r_norm":       iv.circle_r_norm,
            "circle_rx_norm":      iv.circle_rx_norm,
            "circle_ry_norm":      iv.circle_ry_norm,
            "show_square":         iv.show_square,
            "square_rect_norm":    iv.square_rect_norm,
        }

    @staticmethod
    def _restore_iv_overlay(iv: "ImageView", state: dict):
        iv.show_cross         = state.get("show_cross", False)
        iv.cross_pos_norm     = state.get("cross_pos_norm")
        iv.show_circle        = state.get("show_circle", False)
        iv.circle_center_norm = state.get("circle_center_norm")
        iv.circle_r_norm      = state.get("circle_r_norm")
        iv.circle_rx_norm     = state.get("circle_rx_norm")
        iv.circle_ry_norm     = state.get("circle_ry_norm")
        iv.show_square        = state.get("show_square", False)
        iv.square_rect_norm   = state.get("square_rect_norm")

    def setup_cameras(self, cam_names: list[str], layout_config=None):
        """Vytvoří/překreslí kamery podle seznamu jmen."""
        self._layout_config = layout_config  # CamLayoutConfig or None
        # Ulož overlay stav stávajících kamer před zničením
        for cv in self._cam_views:
            self._overlay_store[cv.cam_name] = self._save_iv_overlay(cv.img_view)

        # Odstraň staré
        for cv in self._cam_views:
            cv.setParent(None)
        self._cam_views.clear()
        if self._reg_container is not None:
            if hasattr(self._reg_container, "detach"):
                self._reg_container.detach()
            self._reg_container.setParent(None)
            self._reg_container = None
        self._cam_names_list = list(cam_names)

        for i, name in enumerate(cam_names):
            cv = CameraView(i, name, self)
            cv.clicked.connect(self._on_cam_clicked)
            self._cam_views.append(cv)
            # Obnov overlay stav pokud ho máme uložený
            if name in self._overlay_store:
                self._restore_iv_overlay(cv.img_view, self._overlay_store[name])
            # Enable grid overlay for all cameras; show flag comes from saved config
            # (defaults to hidden for all cameras, diodes included).
            _cam_cfg = get_pdxm1_grid_config(name)
            cv.img_view.show_pdxm1_grid = _cam_cfg.show
            cv.img_view.pdxm1_cam_name  = name

        self._selected_idx = 0
        self._selected_set = set()

        self._rebuild_grid()

    @staticmethod
    def _is_portrait_camera(name: str) -> bool:
        """PDX M1_DF kamery (PD1M1DF, PD2M1DF, ...) jsou portrétní (výška >> šířka).
        PDX M2 kamery (PD1M2NF, ...) jsou čtvercové — NESMÍ být označeny jako portrétní."""
        return bool(re.search(r"PD[1-4]M1.?DF", name, re.IGNORECASE))

    def _detect_orientation(self) -> str:
        """Zjisti orientaci — nejdříve podle jmen kamer, pak podle pixmapu."""
        # Pokud jakákoli kamera je PDXM1_DF → portrétní layout
        names = getattr(self, '_cam_names_list', [])
        if any(self._is_portrait_camera(n) for n in names):
            return "vertical"
        # Fallback: pixmapová detekce
        for cv in self._cam_views:
            pm = cv.img_view._pix
            if pm and not pm.isNull():
                return "vertical" if pm.height() > pm.width() else "square"
        return "square"

    @staticmethod
    def _is_pdxm1_cam(name: str) -> bool:
        """True only for M1 portrait cameras (PD[1-4]M1). M2 cameras (PD[1-4]M2) are square — excluded."""
        return bool(re.search(r'PD[1-4]M1(?!M2|\d)', name, re.IGNORECASE))

    def get_current_layout_entries(self, cam_names: list) -> list:
        """Read current on-screen camera positions as CamLayoutEntry list (ordered by cam_names).
        Falls back to stored layout config if available, otherwise reads from widget geometry."""
        if not self._cam_views:
            return []
        # If a custom layout is active and matches the cam count, use it
        if (self._layout_config is not None and
                len(self._layout_config.entries) == len(cam_names)):
            names_list = getattr(self, '_cam_names_list', [])
            name_to_entry = {n: e for n, e in zip(names_list, self._layout_config.entries)}
            return [name_to_entry.get(n, CamLayoutEntry()) for n in cam_names]
        # Auto-layout: read actual widget geometry via mapTo so nested parents work
        W = self.width()
        H = self.height()
        if W < 10 or H < 10:
            return []
        name_to_entry = {}
        for cv in self._cam_views:
            tl = cv.mapTo(self, cv.rect().topLeft())
            name_to_entry[cv.cam_name] = CamLayoutEntry(
                x=max(0.0, tl.x() / W),
                y=max(0.0, tl.y() / H),
                w=max(0.02, cv.width() / W),
                h=max(0.02, cv.height() / H),
            )
        return [name_to_entry.get(n, CamLayoutEntry()) for n in cam_names]

    def _rebuild_grid(self):
        # Remove all cam views and the reg container from the main grid
        for cv in self._cam_views:
            self._grid.removeWidget(cv)
        if self._reg_container is not None:
            if hasattr(self._reg_container, "detach"):
                self._reg_container.detach()
            self._grid.removeWidget(self._reg_container)
            self._reg_container.setParent(None)
            self._reg_container = None
        # Reset all stretches
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 0)

        n = len(self._cam_views)
        if n == 0:
            return

        # ── Custom layout (user-configured, continuous positions) ────────────
        cfg = self._layout_config
        if cfg is not None and cfg.entries and len(cfg.entries) >= n:
            self._grid.setSpacing(0)
            container = _FreeLayoutContainer(cfg.entries[:n], self._cam_views, parent=self)
            self._grid.addWidget(container, 0, 0, 1, 1)
            self._grid.setRowStretch(0, 1)
            self._grid.setColumnStretch(0, 1)
            return
        self._grid.setSpacing(0)

        # ── Auto layout = image-area-maximising justified rows ───────────────────
        # Tile widths follow each camera's aspect so portrait cameras get narrow
        # tiles and square ones get wider tiles — minimising letterbox and keeping
        # every frame as large as possible. Responsive: recomputed on resize.
        container = _JustifiedRowsContainer(self._cam_views, parent=self)
        self._reg_container = container
        self._grid.addWidget(container, 0, 0, 1, 1)
        self._grid.setRowStretch(0, 1)
        self._grid.setColumnStretch(0, 1)

    def _on_cam_clicked(self, idx: int):
        # Toggle selection: klik přidá/odebere kameru z výběru, může být 0 vybraných
        if idx in self._selected_set:
            self._selected_set.discard(idx)
        else:
            self._selected_set.add(idx)
        self._selected_idx = idx
        for cv in self._cam_views:
            cv.set_selected(cv.cam_index in self._selected_set)
        self.camera_selected.emit(idx)

    def selected_cam_index(self) -> int:
        return self._selected_idx

    def selected_cam_indices(self) -> list[int]:
        """Vrátí seznam indexů všech vybraných kamer (sorted)."""
        return sorted(self._selected_set)

    def selected_img_view(self) -> ImageView | None:
        if 0 <= self._selected_idx < len(self._cam_views):
            return self._cam_views[self._selected_idx].img_view
        return None

    def get_img_view(self, idx: int) -> ImageView | None:
        if 0 <= idx < len(self._cam_views):
            return self._cam_views[idx].img_view
        return None

    def set_cam_timestamp(self, cam_idx: int, text: str, stale: bool = False,
                          approx: bool = False):
        if 0 <= cam_idx < len(self._cam_views):
            self._cam_views[cam_idx].set_timestamp(text, stale=stale, approx=approx)

    def set_cam_stale(self, cam_idx: int, stale: bool, approx: bool = False):
        """Re-colour one tile's timestamp without rebuilding its text — see
        CameraView.set_stale."""
        if 0 <= cam_idx < len(self._cam_views):
            self._cam_views[cam_idx].set_stale(stale, approx=approx)

    def set_label_font_size(self, px: int):
        for cv in self._cam_views:
            cv.set_label_font_size(px)
        self.refresh_auto_layout()   # label bar height feeds the tile split

    def refresh_auto_layout(self):
        """Recompute the auto layout in place. Needed whenever a tile's non-image
        overhead changes (label font, reference badge appearing) — the split between
        image and header is baked into the geometry, so without this the frames end
        up letterboxed or clipped until the next resize."""
        c = self._reg_container
        if c is not None and hasattr(c, "_apply"):
            c._apply()

    def set_cam_ref_status(self, cam_idx: int, text: str):
        if 0 <= cam_idx < len(self._cam_views):
            cv = self._cam_views[cam_idx]
            was = cv._ref_lbl.isVisible()
            cv.set_ref_status(text)
            if was != bool(text):
                self.refresh_auto_layout()

    def cam_count(self) -> int:
        return len(self._cam_views)

# ---------------- TICK BAR ----------------
class TickBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.axis_min_ns = 0; self.axis_max_ns = 0
        self.step_minutes = TICK_STEP_MINUTES
        self.setMinimumHeight(56)
        self.mark_a_ns: int | None = None
        self.mark_b_ns: int | None = None
        self.cursor_ns: int | None = None
        self.left_offset: int = 0   # pixels reserved for slider label prefix (multi-cam)
        self.slider_handle_hw: int = 1  # half-width of slider handle — pro správné zarovnání cursoru
        self.discrete_ticks: list[int] | None = None
        self.discrete_tick_labels: list[str] | None = None

    def set_axis(self, a, b): self.axis_min_ns = a; self.axis_max_ns = b; self.update()
    def set_marks(self, a, b): self.mark_a_ns = a; self.mark_b_ns = b; self.update()
    def set_cursor(self, t: "int | None"): self.cursor_ns = t; self.update()
    def set_left_offset(self, px: int): self.left_offset = px; self.update()

    def _axis_w(self) -> int:
        return max(1, self.width() - self.left_offset)

    def _x_from_ns(self, t) -> int:
        if self.axis_max_ns <= self.axis_min_ns: return self.left_offset
        frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
        return self.left_offset + int(round(max(0.0, min(1.0, frac)) * (self._axis_w() - 1)))

    def _x_cursor(self, t) -> int:
        """Pixel x pro cursor — zohledňuje half-width handleru tak, aby cursor ukazoval na střed."""
        if self.axis_max_ns <= self.axis_min_ns: return self.left_offset
        hw = self.slider_handle_hw
        aw_eff = max(1, self._axis_w() - 2 * hw)
        frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
        return self.left_offset + hw + int(round(max(0.0, min(1.0, frac)) * aw_eff))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.axis_max_ns <= self.axis_min_ns: return
        w, h = self.width(), self.height()
        lo = self.left_offset          # pixel offset where the axis starts
        aw = max(1, w - lo)            # width of the axis area
        p = QPainter(self)
        fm = QFontMetrics(self.font())
        lh = fm.height()               # label height

        def _x(t) -> int:
            frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
            return lo + int(round(max(0.0, min(1.0, frac)) * (aw - 1)))

        # ── Discrete mode: single camera, show per-frame ticks ────────
        if self.discrete_ticks is not None and self.discrete_ticks:
            ticks = self.discrete_ticks
            n = len(ticks)
            min_label_w = fm.horizontalAdvance("2026-03-23 14:53:12") + 8
            max_visible = max(1, aw // min_label_w)
            step = max(1, (n + max_visible - 1) // max_visible)

            # Baseline
            p.setPen(QPen(QColor(160, 160, 160)))
            p.drawLine(lo, h // 2, w, h // 2)

            # Pens hoisted out of the loop, and minor ticks deduplicated by PIXEL: this
            # runs on every set_cursor (i.e. every playback frame and every scrub tick),
            # and allocating a QPen + drawing one line per frame meant thousands of
            # operations per repaint on the GUI thread — several hundred ms for a
            # multi-day range search, which is what jammed the slider. Only one line per
            # x is visible anyway.
            minor_pen = QPen(QColor(140, 140, 140, 160)); minor_pen.setWidth(1)
            major_pen = QPen(QColor(80, 80, 80, 200));    major_pen.setWidth(1)
            p.setPen(minor_pen)
            drawn_x: set[int] = set()
            for t in ticks:
                x = _x(t)
                if x in drawn_x:
                    continue
                drawn_x.add(x)
                p.drawLine(x, h // 2 - 3, x, h // 2 + 3)

            last_label_x = lo - 9999
            for i in range(0, n, step):
                t = ticks[i]
                x = _x(t)
                # Major tick
                p.setPen(major_pen)
                p.drawLine(x, h // 2 - 6, x, h // 2 + 6)
                if self.discrete_tick_labels and i < len(self.discrete_tick_labels):
                    label = self.discrete_tick_labels[i]
                else:
                    dt = _dt_from_ns(t)
                    label = f"{dt:%Y-%m-%d %H:%M:%S}"
                lw = fm.horizontalAdvance(label)
                lx = x - lw // 2
                if lx < last_label_x + 4:
                    continue
                lx = max(lo, min(w - lw, lx))
                lr = QRect(lx, h // 2 + 8, lw, lh)
                if lr.bottom() > h:
                    lr.moveTop(h // 2 - 8 - lh)
                p.fillRect(lr.adjusted(-2, 0, 2, 0), QColor(255, 255, 255, 200))
                p.setPen(QColor(0, 0, 0))
                p.drawText(lr, Qt.AlignmentFlag.AlignVCenter, label)
                last_label_x = lx + lw

            def _draw_mark_discrete(t, color):
                x = _x(t)
                pen = QPen(color); pen.setWidth(2); p.setPen(pen)
                p.drawLine(x, 0, x, h)
            if self.mark_a_ns is not None: _draw_mark_discrete(self.mark_a_ns, QColor(255, 0, 0, 230))
            if self.mark_b_ns is not None: _draw_mark_discrete(self.mark_b_ns, QColor(0, 0, 255, 230))

            if self.cursor_ns is not None:
                xc = _x(self.cursor_ns)
                pen = QPen(QColor(0, 120, 255, 230)); pen.setWidth(2); p.setPen(pen)
                p.drawLine(xc, 0, xc, h)
                label = fmt_hhmmss_ms_from_ns(self.cursor_ns)
                _cf = QFont(self.font()); _cf.setPixelSize(max(1, int(lh * 1.5))); p.setFont(_cf)
                _cfm = QFontMetrics(_cf); _clh = _cfm.height()
                lw = _cfm.horizontalAdvance(label) + 6
                lx = max(lo, min(w - lw, xc - lw // 2))
                p.fillRect(lx, h - _clh - 2, lw, _clh + 2, QColor(0, 80, 200, 200))
                p.setPen(QColor(255, 255, 255))
                p.drawText(lx + 3, h - 2 - _cfm.descent(), label)
                p.setFont(self.font())
            p.end()
            return

        # ── Normal time axis ──────────────────────────────────────────
        span = self.axis_max_ns - self.axis_min_ns
        span_hours = span / ONE_HOUR_NS

        # The ladder must stay bounded by SPAN, not just by zoom level: _aligned_ticks
        # walks a datetime per minor tick over the whole axis, and this paintEvent now
        # runs on every set_cursor (each scrub tick, each playback frame). With the old
        # ladder topping out at minor_min=5, a multi-day range search built thousands of
        # datetimes per repaint — 27 ms for a 30-day span, 200 ms for 180 days, i.e. the
        # whole 33 ms budget spent on the GUI thread drawing the axis.
        if span_hours >= 720:      # ~30 days+
            step_min = 10080; minor_min = 1440
        elif span_hours >= 168:    # ~7 days+
            step_min = 1440;  minor_min = 360
        elif span_hours >= 24:
            step_min = 360;   minor_min = 60
        elif span_hours >= 6:
            step_min = 60;  minor_min = 5
        elif span_hours >= 3:
            step_min = 30;  minor_min = 5
        elif span_hours >= 1.5:
            step_min = 15;  minor_min = 2
        elif span_hours >= 0.5:
            step_min = 10;  minor_min = 1
        else:
            step_min = 5;   minor_min = 1

        def _aligned_ticks(step_m: int) -> list[int]:
            start_dt = _dt_from_ns(self.axis_min_ns).replace(second=0, microsecond=0)
            # Align on minutes since midnight, not on the minute field: for every step up
            # to 30 the two agree (hour*60 is a multiple of them), but the new multi-hour
            # and multi-day steps need the hour too, or a 6 h grid would start at an
            # arbitrary hour instead of on a 6 h boundary.
            rem = (start_dt.hour * 60 + start_dt.minute) % step_m
            if rem:
                start_dt = start_dt - timedelta(minutes=rem)
            end_dt = _dt_from_ns(self.axis_max_ns).replace(second=0, microsecond=0)
            result = []; dt = start_dt
            while dt <= end_dt + timedelta(minutes=step_m):
                result.append(ns_from_dt(dt)); dt += timedelta(minutes=step_m)
            return result

        major_ticks = _aligned_ticks(step_min)
        minor_ticks = _aligned_ticks(minor_min)
        major_set = set(major_ticks)

        # Layout (top-to-bottom):
        #   [lh+2]  tick labels
        #   [8px]   major tick stubs above baseline
        #   [1px]   baseline
        #   [4px]   minor tick stubs below baseline
        #   [lh+4]  cursor label at bottom
        cursor_label_h = lh + 4
        baseline_y = h - cursor_label_h - 1
        major_tick_top = baseline_y - 8
        minor_tick_bot = baseline_y + 4

        # Baseline — thick, clearly visible
        baseline_pen = QPen(QColor(100, 100, 100)); baseline_pen.setWidth(2)
        p.setPen(baseline_pen)
        p.drawLine(lo, baseline_y, w, baseline_y)

        # Minor ticks below baseline
        pen = QPen(QColor(160, 160, 160)); pen.setWidth(1); p.setPen(pen)
        for t in minor_ticks:
            if t in major_set: continue
            x = _x(t)
            if x < lo or x > w: continue
            p.drawLine(x, baseline_y, x, minor_tick_bot)

        # Major ticks above baseline + labels above them
        last_label_x = lo - 9999
        for t in major_ticks:
            x = _x(t)
            if x < lo or x > w: continue
            pen = QPen(QColor(60, 60, 60)); pen.setWidth(1); p.setPen(pen)
            p.drawLine(x, major_tick_top, x, baseline_y)
            txt = fmt_hhmm_from_ns(t)
            tw = fm.horizontalAdvance(txt)
            tx = x - tw // 2
            tx = max(lo, min(w - tw, tx))
            if tx < last_label_x + 4:
                continue
            label_y = major_tick_top - lh - 1
            if label_y < 0: label_y = 0
            p.setPen(QColor(0, 0, 0))
            p.drawText(QRect(tx, label_y, tw, lh), Qt.AlignmentFlag.AlignVCenter, txt)
            last_label_x = tx + tw

        # Midnight date labels at bottom
        midnight_dates: list[int] = []
        _d = _dt_from_ns(self.axis_min_ns).date()
        _d_end = _dt_from_ns(self.axis_max_ns).date()
        while _d <= _d_end:
            try:
                _mn_dt = datetime(_d.year, _d.month, _d.day, 0, 0, 0, tzinfo=TZ_PRAGUE)
            except Exception:
                _mn_dt = datetime(_d.year, _d.month, _d.day, 0, 0, 0)
            _mn_ns = int(_mn_dt.timestamp() * 1_000_000_000)
            if self.axis_min_ns <= _mn_ns <= self.axis_max_ns:
                midnight_dates.append(_mn_ns)
            _d += timedelta(days=1)

        if midnight_dates or span_hours > 20:
            date_font = QFont(self.font()); date_font.setBold(True)
            p.setFont(date_font)
            dfm = QFontMetrics(date_font)
            dlh = dfm.height()
            date_y = h - dlh - 1

            def _draw_date_label(ns_val: int, align_right=False):
                dt_val = _dt_from_ns(ns_val)
                lbl = dt_val.strftime("%d.%m")
                lw = dfm.horizontalAdvance(lbl)
                x_v = _x(ns_val)
                lx = (max(lo, x_v - lw) if align_right else min(w - lw, x_v))
                frac_v = (ns_val - self.axis_min_ns) / span
                if 0.0001 < frac_v < 0.9999:
                    sep_pen = QPen(QColor(40, 80, 180, 120)); sep_pen.setWidth(1)
                    p.setPen(sep_pen); p.drawLine(x_v, baseline_y, x_v, h)
                p.fillRect(QRect(lx, date_y, lw, dlh), QColor(230, 235, 255, 220))
                p.setPen(QColor(40, 80, 180))
                p.drawText(QRect(lx, date_y, lw, dlh), Qt.AlignmentFlag.AlignVCenter, lbl)

            _draw_date_label(self.axis_min_ns, align_right=False)
            _draw_date_label(self.axis_max_ns, align_right=True)
            for mn_ns in midnight_dates:
                _draw_date_label(mn_ns, align_right=False)
            p.setFont(self.font())

        # Marks (Set From / Set To) — drawn above baseline
        def draw_mark(t, color, nudge=0):
            x = _x(t)
            pen = QPen(color); pen.setWidth(2); p.setPen(pen); p.drawLine(x, 0, x, baseline_y)
            label = fmt_hhmmss_ms_from_ns(t); lw = fm.horizontalAdvance(label)
            lx = max(lo, min(w - lw, x - lw // 2 + nudge))
            lr = QRect(lx, baseline_y - lh - 2, lw, lh)
            p.fillRect(lr.adjusted(-4, 0, 4, 0), QColor(255, 255, 255, 210))
            p.setPen(color); p.drawText(lr, Qt.AlignmentFlag.AlignVCenter, label)

        if self.mark_a_ns is not None and self.mark_b_ns is not None:
            lw = fm.horizontalAdvance(fmt_hhmmss_ms_from_ns(self.mark_a_ns))
            overlap = (lw + 8) - abs(_x(self.mark_b_ns) - _x(self.mark_a_ns))
            if overlap > 0:
                draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230), -(overlap // 2 + 2))
                draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230),   overlap // 2 + 2)
            else:
                draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230))
                draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230))
        else:
            if self.mark_a_ns is not None: draw_mark(self.mark_a_ns, QColor(255, 0, 0, 230))
            if self.mark_b_ns is not None: draw_mark(self.mark_b_ns, QColor(0, 0, 255, 230))

        # Cursor line — blue, label at bottom; _x_cursor zohledňuje half-width handleru
        if self.cursor_ns is not None:
            xc = self._x_cursor(self.cursor_ns)
            pen = QPen(QColor(0, 120, 255, 230)); pen.setWidth(2); p.setPen(pen)
            p.drawLine(xc, 0, xc, h)
            label = fmt_hhmmss_ms_from_ns(self.cursor_ns)
            _cf = QFont(self.font()); _cf.setPixelSize(max(1, int(lh * 1.5))); p.setFont(_cf)
            _cfm = QFontMetrics(_cf); _clh = _cfm.height()
            lw = _cfm.horizontalAdvance(label) + 6
            lx = max(lo, min(w - lw, xc - lw // 2))
            p.fillRect(lx, h - _clh - 2, lw, _clh + 2, QColor(0, 80, 200, 200))
            p.setPen(QColor(255, 255, 255))
            p.drawText(lx + 3, h - 2 - _cfm.descent(), label)
            p.setFont(self.font())
        p.end()

# ---------------- LAYOUT HELPERS ----------------
def _hsep() -> QFrame:
    f = QFrame(); f.setFrameShape(QFrame.Shape.HLine); f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #ccc; margin: 2px 0;"); return f

def _group_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-size: 10px; color: #777; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; padding-top: 2px;")
    return lbl

class CollapsibleSection(QWidget):
    """Collapsible panel section: clickable header (left accent stripe + bold
    UPPERCASE title + ▾/▸ arrow) over a body that hides/shows on toggle.

    API:
        sec = CollapsibleSection("Source", "source", expanded=True)
        sec.body_layout.addWidget(...) / addLayout(...)
        sec.set_expanded(bool)
        sec.toggled -> Signal(key: str, expanded: bool)
    """
    toggled = Signal(str, bool)

    _ACCENT = "#4a78c0"

    @staticmethod
    def _shade(hex_color: str, factor: float) -> str:
        """Return hex_color scaled toward black (factor<1) or white (factor>1)."""
        try:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            if factor <= 1.0:
                r, g, b = (int(c * factor) for c in (r, g, b))
            else:
                r, g, b = (int(c + (255 - c) * (factor - 1.0)) for c in (r, g, b))
            r, g, b = (max(0, min(255, c)) for c in (r, g, b))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    @classmethod
    def _header_qss(cls, accent: str) -> str:
        return (
            "QToolButton {"
            "  text-align: left; border: none; border-radius: 4px;"
            "  padding: 7px 9px; margin-top: 6px;"
            "  font-weight: 700; font-size: 11px; letter-spacing: 1px;"
            "  color: #fff; background: %(acc)s;"
            "}"
            "QToolButton:hover { background: %(hov)s; }"
        ) % {"acc": accent, "hov": cls._shade(accent, 0.85)}

    def __init__(self, title: str, key: str, expanded: bool = True, parent=None,
                 accent: str | None = None):
        super().__init__(parent)
        self._key = key
        self._title = title
        self._expanded = expanded
        accent = accent or self._ACCENT

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._header = QToolButton()
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setStyleSheet(self._header_qss(accent))
        self._header.clicked.connect(self._on_header_clicked)
        lay.addWidget(self._header)

        self.body = QWidget()
        self.body.setObjectName("secBody")
        # Accent-tinted left stripe ties the body to its colored header.
        self.body.setStyleSheet(
            f"#secBody {{ border-left: 3px solid {self._shade(accent, 1.35)}; }}")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(8, 3, 0, 7)
        self.body_layout.setSpacing(4)
        lay.addWidget(self.body)

        self.body.setVisible(expanded)
        self._update_header()

    def _update_header(self):
        arrow = "▾" if self._expanded else "▸"
        # Escape '&' so QToolButton does not treat it as a mnemonic accelerator.
        title = self._title.upper().replace("&", "&&")
        self._header.setText(f"{arrow}  {title}")

    def _on_header_clicked(self):
        self.set_expanded(self._header.isChecked())
        self.toggled.emit(self._key, self._expanded)

    def set_expanded(self, expanded: bool):
        self._expanded = bool(expanded)
        self._header.setChecked(self._expanded)
        self.body.setVisible(self._expanded)
        self._update_header()

class _DirItem:
    """Lazy node pro stromový model složek."""
    def __init__(self, path: Path, parent=None):
        self.path = path
        self.parent_item = parent
        self.children: list["_DirItem"] = []
        self.loaded = False

class _LazyDirModel(QObject):
    """Jednoduchý model pro QTreeView — načítá složky lazy."""
    from PySide6.QtCore import QAbstractItemModel, QModelIndex
    pass

from PySide6.QtCore import QAbstractItemModel, QModelIndex as _QModelIndex

class LazyDirModel(QAbstractItemModel):
    def __init__(self, root_path: Path, parent=None):
        super().__init__(parent)
        self._root = _DirItem(root_path)
        # Nenačítáme synchronně — lazy load při prvním rozbalení
        self._root.loaded = False

    def _load_children(self, item: _DirItem):
        if item.loaded: return
        item.loaded = True
        try:
            dirs = sorted(
                [p for p in item.path.iterdir() if p.is_dir()],
                key=lambda p: p.name.lower()
            )
            item.children = [_DirItem(d, item) for d in dirs]
        except Exception:
            item.children = []

    def _item_from_index(self, index: _QModelIndex) -> _DirItem:
        if not index.isValid():
            return self._root
        return index.internalPointer()

    def index(self, row, col, parent=_QModelIndex()):
        parent_item = self._item_from_index(parent)
        self._load_children(parent_item)
        if row < 0 or row >= len(parent_item.children):
            return _QModelIndex()
        child = parent_item.children[row]
        return self.createIndex(row, col, child)

    def parent(self, index=_QModelIndex()):
        if not index.isValid():
            return _QModelIndex()
        item = index.internalPointer()
        if item is None or item.parent_item is None:
            return _QModelIndex()
        p = item.parent_item
        if p.parent_item is None:
            return _QModelIndex()
        try:
            row = p.parent_item.children.index(p)
        except ValueError:
            return _QModelIndex()
        return self.createIndex(row, 0, p)

    def rowCount(self, parent=_QModelIndex()):
        item = self._item_from_index(parent)
        self._load_children(item)
        return len(item.children)

    def columnCount(self, parent=_QModelIndex()):
        return 1

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        item = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return item.path.name
        return None

    def hasChildren(self, parent=_QModelIndex()):
        item = self._item_from_index(parent)
        if not item.loaded:
            return True  # Optimisticky — ukáže šipku
        return len(item.children) > 0

    def filepath(self, index: _QModelIndex) -> str:
        if not index.isValid(): return str(self._root.path)
        return str(index.internalPointer().path)

    def index_for_path(self, path: Path) -> _QModelIndex:
        """Najde index pro danou cestu — postupně rozbalí strom."""
        try:
            rel = path.relative_to(self._root.path)
        except ValueError:
            return _QModelIndex()
        parts = rel.parts
        current_idx = _QModelIndex()
        current_item = self._root
        for part in parts:
            self._load_children(current_item)
            found = False
            for i, child in enumerate(current_item.children):
                if child.path.name.lower() == part.lower():
                    current_idx = self.createIndex(i, 0, child)
                    current_item = child
                    found = True
                    break
            if not found:
                return _QModelIndex()
        return current_idx


class FolderPickerDialog(QDialog):
    def __init__(self, start_path: str, title: str = "Select folder", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 450)
        self.selected_path: str = ""

        # Najdi rozumný root — 2 úrovně nad start_path
        try:
            sp = Path(start_path)
            root = sp.parent.parent if sp.parent.parent.exists() else sp.parent
        except Exception:
            root = Path(start_path) if start_path else Path(".")

        self._model = LazyDirModel(root)

        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(False)
        self._tree.setIndentation(20)
        self._tree.setUniformRowHeights(True)

        # Naviguj na start_path
        if start_path:
            try:
                idx = self._model.index_for_path(Path(start_path))
                if idx.isValid():
                    self._tree.setCurrentIndex(idx)
                    self._tree.scrollTo(idx)
                    self._tree.expand(idx)
            except Exception:
                pass

        self._path_edit = QLineEdit(start_path)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._tree.clicked.connect(self._on_clicked)
        self._tree.doubleClicked.connect(self._on_double_clicked)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Path:"))
        lay.addWidget(self._path_edit)
        lay.addWidget(self._tree, 1)
        lay.addWidget(btns)

    def _on_clicked(self, index):
        self._path_edit.setText(self._model.filepath(index))

    def _on_double_clicked(self, index):
        self._tree.expand(index)
        self._path_edit.setText(self._model.filepath(index))

    def _on_path_entered(self):
        path = self._path_edit.text().strip()
        try:
            idx = self._model.index_for_path(Path(path))
            if idx.isValid():
                self._tree.setCurrentIndex(idx)
                self._tree.scrollTo(idx)
        except Exception:
            pass

    def _on_accept(self):
        self.selected_path = self._path_edit.text().strip()
        self.accept()

    @staticmethod
    def get_folder(start_path: str, title: str = "Select folder", parent=None) -> str:
        dlg = FolderPickerDialog(start_path, title, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_path
        return ""
    
class _CamPollSignals(QObject):
    found = Signal(int, list, list)  # cam_i, new_items, new_folders


# ---------------------------------------------------------------------------
# Real-time directory watcher via ReadDirectoryChangesW (Windows only).
# Fires immediately when a new image file appears — no os.listdir() at all.
# Falls back gracefully on non-Windows or when the SMB server doesn't support
# change notifications.
# ---------------------------------------------------------------------------

import ctypes as _ct
import ctypes.wintypes as _wt
import threading as _threading

try:
    _k32 = _ct.windll.kernel32
    _FILE_LIST_DIR        = 0x0001
    _FILE_SHARE_ALL       = 0x07
    _OPEN_EXISTING        = 3
    _FILE_FLAG_BACKUP_SEM = 0x02000000
    _FILE_NOTIFY_FILE     = 0x00000001   # FILE_NOTIFY_CHANGE_FILE_NAME
    _FILE_ACTION_ADDED    = 1
    _FILE_ACTION_RENAMED  = 5
    _INVALID_HANDLE       = _ct.c_void_p(-1).value
    _DIRWATCH_AVAILABLE   = True
except AttributeError:
    _DIRWATCH_AVAILABLE   = False


class _DirWatchSignals(QObject):
    # cam_i, the watched folder, filename. The folder MUST travel with the event:
    # a camera has a watcher on the current AND the previous hour folder, and the
    # receiver cannot tell which one an event came from. Guessing it (first folder
    # in the camera's list that has a watcher) always named the OLDEST folder, so
    # every pushed frame got a path in the previous hour — a file that does not
    # exist. The tile then never painted, and because the bogus item had already
    # advanced the poll cutoff, the safety-net poll rejected the real file too.
    new_file = Signal(int, str, str)


class _DirWatcher(_threading.Thread):
    """Background thread watching one folder with ReadDirectoryChangesW.
    Emits new image filenames instantly — zero directory-enumeration cost."""

    def __init__(self, cam_i: int, folder: "Path",
                 signals: "_DirWatchSignals", img_ext: frozenset):
        super().__init__(daemon=True, name=f"DirWatch-cam{cam_i}")
        self._cam_i   = cam_i
        self._folder  = folder
        self._signals = signals
        self._img_ext = img_ext
        self._stop    = _threading.Event()
        self._handle  = None
        # True once ReadDirectoryChangesW is actually armed. RDCW can silently
        # fail on UNC/SMB paths — the thread then exits and is_alive() goes
        # False, so health = (ok and is_alive()). A dead watcher must NOT be
        # treated as coverage, or the poll fallback never takes over.
        self.ok = False
        # Liveness accounting for the silent-death case where the thread stays
        # alive but RDCW stops delivering (seen on some SMB shares): the poll
        # cross-checks this against frames it found itself.
        self.last_event_mono = 0.0
        self.suspect_strikes = 0

    def stop(self):
        self._stop.set()
        h = self._handle
        if h and h != _INVALID_HANDLE:
            try:
                _k32.CancelIoEx(h, None)
            except Exception:
                pass

    def run(self):
        if not _DIRWATCH_AVAILABLE:
            return
        h = _k32.CreateFileW(
            str(self._folder),
            _FILE_LIST_DIR,
            _FILE_SHARE_ALL,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEM,
            None,
        )
        if h == _INVALID_HANDLE:
            return
        self._handle = h
        self.ok = True
        self.last_event_mono = time.monotonic()   # armed counts as activity
        buf = _ct.create_string_buffer(65536)
        br  = _wt.DWORD(0)
        while not self._stop.is_set():
            ok = _k32.ReadDirectoryChangesW(
                h, buf, len(buf), False,
                _FILE_NOTIFY_FILE,
                _ct.byref(br), None, None,
            )
            if not ok or br.value == 0:
                break
            off = 0
            while True:
                nxt    = _wt.DWORD.from_buffer_copy(buf, off).value
                action = _wt.DWORD.from_buffer_copy(buf, off + 4).value
                nlen   = _wt.DWORD.from_buffer_copy(buf, off + 8).value
                name   = buf.raw[off + 12: off + 12 + nlen].decode(
                    "utf-16-le", errors="replace")
                if action in (_FILE_ACTION_ADDED, _FILE_ACTION_RENAMED):
                    if Path(name).suffix.lower() in self._img_ext:
                        self.last_event_mono = time.monotonic()
                        self.suspect_strikes = 0
                        try:
                            self._signals.new_file.emit(
                                self._cam_i, str(self._folder), name)
                        except RuntimeError:
                            return  # Qt object deleted during shutdown
                if nxt == 0:
                    break
                off += nxt
        _k32.CloseHandle(h)
        self._handle = None


class _CamPollTask(QRunnable):
    """Scan one camera's folder list for new images since cutoff ts_ns.
    Also probes next UTC hour-folders for auto-discovery."""

    def __init__(self, cam_idx: int, folders: list, cutoff: int,
                 cam_name: str, signal: "_CamPollSignals",
                 seen_map: "dict[str, set[str]] | None" = None):
        super().__init__()
        self.setAutoDelete(True)
        self._cam_i    = cam_idx
        self._folders  = folders
        self._cutoff   = cutoff
        self._cam_name = cam_name
        self._sig      = signal
        self._seen_map = seen_map   # folder_str -> set(names already parsed)

    def run(self):
        new_items: list = []
        for folder in self._folders:
            try:
                folder_path = Path(folder)
                # Names already parsed on a previous tick are skipped with a cheap
                # C-level set hit — turns O(all files) per poll into O(new files),
                # which is what stops the hour-end GIL starvation. Single writer per
                # folder (one cam owns it, guarded by _cam_poll_running), so no lock.
                seen = None if self._seen_map is None else self._seen_map.get(str(folder))
                for name in os.listdir(folder):
                    if seen is not None:
                        if name in seen:
                            continue
                        seen.add(name)
                    # String-level extension check — avoids a Path object per
                    # file in a loop that runs over thousands of entries.
                    dot = name.rfind(".")
                    if dot < 0 or name[dot:].lower() not in IMG_EXT:
                        continue
                    p = folder_path / name
                    ts_ns = parse_unix_ns_from_name(p)
                    if ts_ns is None or ts_ns <= self._cutoff:
                        continue
                    new_items.append(Item(p, ts_ns))
            except Exception:
                pass
        if new_items:
            new_items.sort(key=lambda x: x.ts_ns)

        # Auto-discover next UTC hour-folders
        new_folders: list = []
        if self._cam_name:
            known = set(self._folders)
            for folder in self._folders:
                try:
                    hour_dir = folder.parent
                    day_dir  = hour_dir.parent
                    try:
                        current_utc_hour = int(hour_dir.name)
                    except ValueError:
                        continue
                    for delta in range(1, 4):
                        next_h = (current_utc_hour + delta) % 24
                        if next_h < current_utc_hour and delta == 1:
                            try:
                                from datetime import date as _date, timedelta as _td
                                day_parts = (int(day_dir.parent.parent.name),
                                             int(day_dir.parent.name),
                                             int(day_dir.name))
                                next_day = _date(*day_parts) + _td(days=1)
                                candidate = (day_dir.parent.parent.parent
                                             / str(next_day.year)
                                             / str(next_day.month)
                                             / str(next_day.day)
                                             / str(next_h)
                                             / self._cam_name)
                            except Exception:
                                continue
                        else:
                            candidate = day_dir / str(next_h) / self._cam_name
                        if candidate in known or candidate in new_folders:
                            break
                        if _probe_hour_folder(candidate):
                            new_folders.append(candidate)
                            known.add(candidate)
                            try:
                                cand_path = Path(candidate)
                                for name in os.listdir(candidate):
                                    dot = name.rfind(".")
                                    if dot < 0 or name[dot:].lower() not in IMG_EXT:
                                        continue
                                    p = cand_path / name
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None or ts_ns <= self._cutoff:
                                        continue
                                    new_items.append(Item(p, ts_ns))
                            except Exception:
                                pass
                        else:
                            break
                except Exception:
                    pass
            if new_items:
                new_items.sort(key=lambda x: x.ts_ns)

        # Always emit, even if the signal's C++ object was already deleted (app
        # closing mid-poll) — the caller flips _cam_poll_running[cam_i] back to
        # False inside this callback, so a swallowed emit would leave that
        # camera's polling stuck "running" forever and it would never poll again.
        try:
            self._sig.found.emit(self._cam_i, new_items, new_folders)
        except RuntimeError:
            pass


# ================================================================== PER-CAM SLIDER ROW

class _CamSliderRow(QWidget):
    """One row: [● Master radio] [Camera name label] [━━━━ slider ━━━━]"""
    master_chosen     = Signal(int)   # emitted when radio is checked; arg = cam index
    master_deselected = Signal(int)   # emitted when radio is unchecked; arg = cam index
    value_changed     = Signal(int, int)  # (cam_index, slider_value)
    pressed           = Signal(int)   # cam_index
    released          = Signal(int)   # cam_index

    def __init__(self, cam_idx: int, cam_name: str, parent=None):
        super().__init__(parent)
        self.cam_idx  = cam_idx
        self._is_master = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(4)

        from PySide6.QtWidgets import QRadioButton
        self._radio = QRadioButton()
        self._radio.setAutoExclusive(False)  # allow clicking checked radio to uncheck it
        self._radio.setToolTip("Set as master camera (sync others to this). Click again to deselect.")
        self._radio.setFixedWidth(16)
        self._radio.toggled.connect(self._on_radio_toggled)
        lay.addWidget(self._radio)

        self._lbl = QLabel(_strip_cam_name(cam_name))
        self._lbl.setFixedWidth(90)
        self._lbl.setStyleSheet("font-size: 10px; color: #333;")
        lay.addWidget(self._lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(SLIDER_MAX)
        self._slider.setEnabled(False)
        self._slider.valueChanged.connect(lambda v: self.value_changed.emit(self.cam_idx, v))
        self._slider.sliderPressed.connect(lambda: self.pressed.emit(self.cam_idx))
        self._slider.sliderReleased.connect(lambda: self.released.emit(self.cam_idx))
        lay.addWidget(self._slider, 1)

    def set_master(self, yes: bool):
        self._is_master = yes
        self._radio.blockSignals(True)
        self._radio.setChecked(yes)
        self._radio.blockSignals(False)
        self._lbl.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #0055cc;" if yes
            else "font-size: 10px; color: #333;")

    def set_value(self, v: int):
        self._slider.blockSignals(True)
        self._slider.setValue(v)
        self._slider.blockSignals(False)

    def set_enabled(self, on: bool):
        self._slider.setEnabled(on)

    def value(self) -> int:
        return self._slider.value()

    def slider_x_in_parent(self) -> int:
        """X position of the slider's left edge relative to this row widget."""
        return self._slider.x()

    def _on_radio_toggled(self, checked: bool):
        if checked:
            self.master_chosen.emit(self.cam_idx)
        else:
            self.master_deselected.emit(self.cam_idx)


# ================================================================== PV OVERLAY PANEL

class _PvOverlayPanel(QWidget):
    """
    Floating, draggable, semi-transparent PV values panel.
    Parent must be the _single_wrapper (or any QWidget that covers the camera image).
    Hides itself when no PV values are set or when _hidden flag is set.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.setVisible(False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._drag_offset: "QPoint | None" = None
        self._rows: list[tuple[str, str]] = []   # (name, value+units)

        # Configurable display settings
        self.font_size_px: int = 24
        self.font_family: str = "Segoe UI"
        self.bg_opacity: int = 100      # 0–100 percent
        self.font_color: QColor = QColor("#000000")
        self.bg_color: QColor = QColor("#eae31e")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(0)

        self._content = QLabel()
        self._content.setTextFormat(Qt.TextFormat.PlainText)
        # Clicks on the text must reach the panel itself (which owns the drag),
        # not get swallowed by the child label.
        self._content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._content)
        self._apply_style()

    def _apply_style(self):
        alpha = int(self.bg_opacity / 100 * 255)
        self._bg_color = QColor(self.bg_color.red(), self.bg_color.green(), self.bg_color.blue(), alpha)
        color_hex = self.font_color.name()
        style = (
            f"QLabel {{ color: {color_hex}; font-size: {self.font_size_px}px; font-weight: 700; "
            f"font-family: '{self.font_family}', monospace; background: transparent; }}"
        )
        self._content.setStyleSheet(style)
        self.adjustSize()
        self.update()

    def apply_settings(self, font_size_px: int, font_family: str, bg_opacity: int, font_color: QColor, bg_color: "QColor | None" = None):
        self.font_size_px = font_size_px
        self.font_family = font_family
        self.bg_opacity = bg_opacity
        self.font_color = font_color
        if bg_color is not None:
            self.bg_color = bg_color
        self._apply_style()

    def update_values(self, rows: "list[tuple[str,str]]"):
        """rows = list of (name, formatted_value_with_units)"""
        self._rows = rows
        if not rows:
            self.setVisible(False)
            return
        lines = "\n".join(f"{n}: {v}" for n, v in rows)
        self._content.setText(lines)
        self.adjustSize()
        self.setVisible(True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = getattr(self, "_bg_color", QColor(30, 30, 30, 204))
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 6, 6)
        p.setPen(QPen(QColor(100, 100, 100, 100)))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = e.pos()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.MouseButton.LeftButton:
            new_pos = self.pos() + e.pos() - self._drag_offset
            parent = self.parentWidget()
            if parent:
                # Clamp inside parent
                new_pos.setX(max(0, min(new_pos.x(), parent.width() - self.width())))
                new_pos.setY(max(0, min(new_pos.y(), parent.height() - self.height())))
            self.move(new_pos)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

    def ensure_inside_parent(self):
        parent = self.parentWidget()
        if not parent:
            return
        x = max(0, min(self.x(), parent.width() - self.width()))
        y = max(0, min(self.y(), parent.height() - self.height()))
        self.move(x, y)


# ================================================================== UI
class Viewer(QWidget):
    def __init__(self):
        super().__init__()
        # Pre-warm Qt6's QPixmap/raster/GPU subsystem on the main thread.
        # Without this, the FIRST QPixmap operation in any background thread
        # blocks the main thread for ~30 s while Qt initialises the subsystem.
        try:
            QPixmap(1, 1)
        except Exception:
            pass
        # Title is set by the parent window (main.py)
        # self.setWindowTitle("Image Slider")

        self._gen = 0
        self.items: list[Item] = []
        self.ts_list: list[int] = []
        self.axis_min_ns = 0; self.axis_max_ns = 0
        self.opened_folder: Path | None = None
        self.opened_folders: list[Path] = []
        self.axis_override: tuple[int, int] | None = None
        self.last_open_dir = Path(DEFAULT_OPEN_DIR)
        self._last_save_dir: Path = Path(DEFAULT_SAVE_DIR)
        self._save_progress_dlg = None

        now_dt = datetime.now(TZ_PRAGUE)
        self.last_pick_date = now_dt.date()
        self.last_pick_hour_from: "int | None" = None   # None = first open, default to live mode
        self.last_pick_hour_to:   "int | None" = None
        self.last_pick_min_from:  int = 0               # minute part of the picked window
        self.last_pick_min_to:    int = 0               # exclusive end (see seg_bounds_ns)
        self.last_pick_axis_override: tuple[int, int] | None = None
        self._last_pick_segments: "list | None" = None  # per-day PickSeg list, or None
        self._last_pick_range_mode = False   # last multi-day pick was day→day
        # Loaded-frame filter: folders are hour-granular, so minute-precise
        # windows (and per-day windows) are enforced on the scanned items.
        # None = keep everything the folders contain.
        self._ts_windows: "list[tuple[int, int]] | None" = None
        self.last_pick_cam_names: list[str] = []   # paměť vybraných kamer

        self.pending_slider = None
        self._is_playing = False
        self._is_scrubbing = False
        self._last_motion_counter = None
        self._last_target_idx = None
        self._last_motion_ips = 0.0
        self.play_time_ns: int | None = None
        self.target_idx:   int | None = None
        self.current_idx:  int | None = None
        self._display_load_key = None
        self._deferred_display = None
        self._play_frame_acc = 0.0
        self._play_master_frame = 0
        self._discrete_mode = True   # slider skáče po indexech, ne po čase
        self._fake_ts_map = None
        self._real_ts_list: list[int] = []

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(PLAY_TICK_MS)
        # Qt's default CoarseTimer snaps to the Windows 15.625 ms scheduler tick, so a
        # 33 ms interval really fires every 46.9 ms — 21 ticks/s instead of 30. A third of
        # playback's frames were lost before any image work happened. Measured: 21.2 →
        # 30.3 ticks/s, and with the preview serving them that is 30 painted frames per
        # second per camera.
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.timeout.connect(self._autoplay_step)

        self._prefetch_debounce = QTimer(self)
        self._brightness_debounce = QTimer(self)
        self._brightness_debounce.setSingleShot(True)
        self._brightness_debounce.timeout.connect(self._apply_brightness_debounced)
        self._prefetch_debounce.setSingleShot(True)
        self._prefetch_debounce.timeout.connect(self._run_prefetch_after_idle)

        self._scrub_side = SCRUB_MAX_SIDE
        # Decode size latched for the duration of one slider drag (see _on_slider_pressed).
        self._drag_side  = SCRUB_MAX_SIDE
        self.cache = PixCache(CACHE_SIZE)

        # ── Whole-window preview layer (live mode OFF) ───────────────────────
        self._proxy_gen      = 0
        self._proxy_tracks: "list[_ProxyTrack]" = []
        self._proxy_stop     = threading.Event()
        self._proxy_inflight = 0
        self._proxy_total    = 0
        self._proxy_done     = 0
        self._proxy_rr       = -1     # round-robin cursor over the tracks
        self._proxy_cursor_ts_ns = 0  # timeline moment last displayed (_set_info_for);
                                      # the sweep fills its neighbourhood first
        self._proxy_holds    = 0      # consecutive _proxy_pump holds (see PROXY_HOLD_MAX)
        # Per-minute paint counters for the diag log: how a frame reached the screen.
        # "Is the slider smooth" is otherwise a matter of opinion; prev/load is the
        # number that decides it (a share load is ~1/s per camera, a preview paint is
        # bounded only by the 33 ms tick).
        self._diag_prev = 0   # painted from the RAM preview
        self._diag_miss = 0   # preview asked but too coarse / not there yet
        self._diag_load = 0   # painted from a real share read
        self._diag_cach = 0   # painted from the rendered-pixmap cache (revisited frame)
        # Per-paint ledger for bench_drag.py / bench_play.py, off unless the env var is
        # set. The per-minute counters above aggregate across cameras and cannot answer
        # the question the whole preview layer exists for: how far was the frame that
        # actually got painted from the one the slider asked for. Recording it here —
        # in the app, at the one funnel every paint goes through (_cam_note_painted) —
        # keeps the harness measuring the shipping code instead of a copy that drifts.
        self._bench = [] if os.environ.get("IMAGE_TOOLS_BENCH") else None
        self._proxy_grace_until = 0.0  # monotonic deadline; no dispatch before it
        self._proxy_was_enabled = None   # last _proxy_enabled() seen by _proxy_sync_enabled
        self._proxy_signals  = _ProxySignals()
        self._proxy_signals.batch.connect(self._on_proxy_batch)
        self._proxy_pool = QThreadPool(self)
        self._proxy_pool.setMaxThreadCount(PROXY_WORKERS)
        # Rendered previews (auto-stretch / contrast / palette applied to the small
        # array). Keys are not (idx, max_side, …) → no native cap here. 96 was too few
        # to survive one drag across a long window, so every pass re-ran autostretch +
        # LUT on frames it had just rendered. 384 × ~170 kB ≈ 65 MB.
        self._proxy_render_cache = PixCache(384, native_keep=None)
        self._proxy_topup = QTimer(self)
        self._proxy_topup.setSingleShot(True)
        self._proxy_topup.timeout.connect(self._proxy_start)
        # Re-dispatch after _proxy_pump held the sweep off. Deliberately NOT _proxy_topup:
        # that one restarts the whole sweep (bumps _proxy_gen, discarding every batch in
        # flight), so retrying through it would have thrown away work every 400 ms.
        self._proxy_resume = QTimer(self)
        self._proxy_resume.setSingleShot(True)
        self._proxy_resume.timeout.connect(self._proxy_pump)
        # Debounced full-quality re-render of the frame the user settled on.
        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.timeout.connect(self._refine_current_frame)

        self.scan_pool = QThreadPool(self); self.scan_pool.setMaxThreadCount(4)
        # Separate pool for online polling — one thread per camera so they run in parallel
        self._poll_pool = QThreadPool(self); self._poll_pool.setMaxThreadCount(8)
        # Per-folder cache of filenames the online poller has already parsed, so a
        # near-full hour-folder (~12k files just before UTC rollover) is NOT
        # Path+regex re-parsed on every 0.5 s tick × every camera. Re-parsing the
        # whole folder held the GIL for hundreds of ms per tick and starved the Qt
        # UI thread (cameras kept drawing, but buttons/gradient froze) — the ~2 h
        # "gets stuck" report. Bounded: only the newest 1-2 folders per camera are
        # ever scanned; stale keys are pruned at rollover (see _online_poll_multi).
        self._poll_seen: dict[str, set[str]] = {}
        # Image decode is I/O-bound over SMB (read latency dominates decode CPU)
        # — more threads keep scrubbing/prefetch responsive on a slow share.
        #
        # 8 → 16 only became worth anything once _open_reader stopped holding the GIL
        # through the network read. Before that, throughput was pinned at ~30 frames/s no
        # matter how many threads ran (measured: 27/32/29 fps at 1/8/16 threads); after it,
        # the same share gives 31/106/204 fps at 1/8/16. This is what makes scrubbing
        # smooth — the share was never the limit.
        self.load_pool = QThreadPool(self); self.load_pool.setMaxThreadCount(16)
        # Multi-camera tiles decode here, in ONE pool shared by every camera, created once
        # for the life of the Viewer. load_pool above is the single-camera timeline's; the
        # tiles used to get one 2-thread pool each, rebuilt on every camera (re)load, so
        # load_pool's 16 threads were unreachable in multi-cam while pools and threads
        # accumulated. Fairness between cameras is the per-camera depth cap
        # (_cam_inflight_depth), not a partition of the threads.
        self._cam_pool = QThreadPool(self)
        self._cam_pool.setMaxThreadCount(CAM_POOL_THREADS)
        self.analysis_pool = QThreadPool(self); self.analysis_pool.setMaxThreadCount(1)

        self.load_signals = LoaderSignals()
        self.load_signals.loaded.connect(self._on_loaded)

        self._display_req_id = 0
        self._inflight: set = set()
        # key → the display EPOCH that asked for it. The epoch is bumped only by discrete
        # navigation (_display_exact_index: release, stop, arrow step, seek, settings
        # change), never per scrub tick or per playback frame. So a load that lands late
        # within the SAME interaction still paints — which is what makes a drag or a
        # playback look alive on a slow share — while one left over from a view the user
        # has navigated away from is dropped. See _on_loaded.
        self._want_display_req: dict = {}
        self._display_epoch = 0
        self._scan_task: ScanTask | None = None
        self._save_task: SaveRangeTask | None = None
        self._refresh_task: RefreshScanTask | None = None
        self.mark_a_ns: int | None = None
        self.mark_b_ns: int | None = None
        self._pointing_task: PointingAnalysisTask | None = None
        self._brightness_offset: int = 0  # -255 .. +255
        self._ref_image: np.ndarray | None = None  # reference frame pro subtraction (full-res, jen pro status/existence)
        self._ref_path: "Path | None" = None        # cesta k reference snímku (re-decode na displej. rozlišení)
        self._ref_scaled: dict = {}                 # max_side -> np.ndarray reference zmenšená stejným pipeline jako aktuální snímek
        self._sf_energy_map: dict[str, str] = {}  # filename -> energie ze Shot Finderu
        self._saved_timestamps: list[tuple[int, str]] = []  # (ts_ns, label)

        # ── Multi-camera state ───────────────────────────────────────────────
        self._cam_names:        list[str]         = []   # jména načtených kamer
        self._cam_folders:      list[Path]        = []   # jedna (první) složka per-camera (legacy)
        self._cam_folder_lists: list[list[Path]]  = []   # všechny složky per-camera (pro online poll)
        # Per-camera items, ts_list, cache, in-flight registry
        self._cam_items:   list[list]  = []        # list of list[Item]
        self._cam_ts:      list[list]  = []        # list of list[int]
        self._cam_caches:     list        = []        # list of PixCache
        self._cam_inflight_at: list      = []        # list of {req_id: launch_monotonic}
        self._cam_req_seq:    int        = 0
        self._cam_signals:    list        = []        # list of LoaderSignals
        self._cam_ref_images: list        = []        # list of np.ndarray | None, per-camera subtraction reference (full-res, jen status)
        self._cam_ref_paths:  list        = []        # list of Path | None, cesta k reference snímku per-camera
        self._cam_ref_scaled: list        = []        # list of dict (max_side -> np.ndarray), reference zmenšená per-camera
        self._cam_diff_stats: dict        = {}        # cam_i -> last difference stats (for the info line)

        # ── Online mode state ────────────────────────────────────────────────
        self._dir_watch_sigs:  "_DirWatchSignals | None" = None
        self._dir_watchers:    "dict[str, _DirWatcher]"  = {}  # folder_str → watcher
        self._online_mode    = False
        self._online_timer   = QTimer(self)
        self._online_timer.setInterval(200)
        self._online_timer.timeout.connect(self._online_poll)
        self._auto_follow    = False   # sleduj nejnovější snímek
        # True once the live cap dropped frames from memory — turning live mode
        # off then re-scans from disk to put the full history back.
        self._live_trimmed   = False
        # Running number of frames the live cap has dropped off the FRONT of
        # self.items / self._cam_items[i]. Pixmap-cache keys add it to the index
        # so a key names a frame and not a position — see _ck().
        self._items_offset   = 0
        self._cam_offsets: list[int] = []
        self._online_blink_state = False
        self._online_last_new_ns = 0.0  # čas posledního nového snímku
        self._online_last_poll_ts = 0.0  # čas posledního spuštění polleru
        self._online_blink_timer = QTimer(self)
        self._online_blink_timer.setInterval(600)
        self._online_blink_timer.timeout.connect(self._on_online_blink)
        # Per-camera last-update timestamps for refresh dots
        self._cam_last_update_ts: list[float] = []
        # Per-camera display tracking for the same dots: ts_ns of the frame last
        # PAINTED and when that frame changed. Frames arriving is not enough —
        # a stuck display must not blink green (see _on_cam_dot_blink).
        self._cam_shown_ts_ns: list[int] = []
        self._cam_shown_mono: list[float] = []
        # ts_ns the slider / playback last ASKED each tile for, and how far the frame it
        # actually painted was allowed to be from it. Together with _cam_shown_ts_ns these
        # are the only source of truth for the tile's timestamp label and its refresh dot
        # (see _cam_note_target / _cam_note_painted / _cam_is_stale).
        self._cam_target_ts_ns: list[int] = []
        self._cam_paint_tol_ns: list[int] = []
        # Whether the frame on each tile is a preview STAND-IN for the requested one
        # (within tolerance, so not stale) rather than the exact frame. Its own label
        # state, because "close enough on purpose" is neither "exact" nor "behind".
        self._cam_paint_preview: list[bool] = []
        self._cam_dot_blink_state: bool = False
        self._cam_dot_timer = QTimer(self)
        self._cam_dot_timer.setInterval(600)
        self._cam_dot_timer.timeout.connect(self._on_cam_dot_blink)

        # ── Diagnostics: periodic health snapshot ────────────────────────────
        # Writes one line/min to image_tools_diag.log (next to the exe) so the
        # slow degradation/freeze that shows up after ~2 h online can be traced
        # to whichever metric keeps growing (RSS memory, thread/pool/child count,
        # live QPixmap/QImage count, item lists, watchers…). Cheap, always on.
        self._diag_t0 = time.monotonic()
        self._diag_timer = QTimer(self)
        self._diag_timer.setInterval(60_000)
        self._diag_timer.timeout.connect(self._diag_log)
        self._diag_timer.start()

        # ── PV state ─────────────────────────────────────────────────────────
        self._pv_enabled: list[str] = []       # ordered list of selected PV names
        self._pv_values:  dict[str, str] = {}  # name → displayed value string
        self._pv_values_ts: "int | None" = None  # frame ts the values belong to
        self._pv_fetch_ts:  "int | None" = None  # frame ts of the in-flight fetch
        self._pv_fetch_gen: int = 0            # incremented each fetch to cancel stale results
        self._pv_signals = _PvSignals()
        self._pv_signals.result.connect(self._pv_on_result)
        self._pv_overlay: "_PvOverlayPanel | None" = None   # created in _build_ui
        self._pv_overlay_multi: "_PvOverlayPanel | None" = None   # created in _build_ui

        self._build_ui()

        # Overlay appearance settings
        self._overlay_cross_color   = QColor(0, 255, 0, 220)
        self._overlay_cross_thick   = 2
        self._overlay_cross_size    = 18
        self._overlay_circle_color  = QColor(255, 255, 0, 230)
        self._overlay_circle_thick  = 2
        self._overlay_square_color  = QColor(0, 200, 255, 230)
        self._overlay_square_thick  = 2

    # ------------------------------------------------ collapsible UI state
    _UI_STATE_PATH = Path(os.environ.get("APPDATA", Path.home())) / "ELI_ImageTools" / "slider_ui_state.json"

    def _load_ui_state(self) -> dict:
        try:
            if self._UI_STATE_PATH.exists():
                return json.loads(self._UI_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_ui_state(self):
        try:
            self._UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._UI_STATE_PATH.write_text(
                json.dumps(self._ui_state, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _diag_log(self):
        """One-line/min health snapshot → image_tools_diag.log next to the exe.
        Purely diagnostic: pinpoints which resource grows before the ~2 h freeze.
        Must never raise (it runs on a timer)."""
        try:
            import gc, sys as _sys
            # Process working-set (RSS) in MB via Windows PSAPI — no psutil needed.
            rss_mb = _rss_mb()
            n_threads = threading.active_count()
            try:    n_pools = len(self.findChildren(QThreadPool))
            except Exception: n_pools = -1
            try:    n_children = len(self.children())
            except Exception: n_children = -1
            n_items   = len(self.items) if getattr(self, "items", None) else 0
            cam_items = getattr(self, "_cam_items", []) or []
            cam_total = sum(len(c) for c in cam_items)
            cam_max   = max((len(c) for c in cam_items), default=0)
            watchers  = getattr(self, "_dir_watchers", {}) or {}
            n_watch   = len(watchers)
            n_walive  = sum(1 for w in watchers.values()
                            if getattr(w, "is_alive", lambda: False)())
            # Tiles share ONE pool now; log its active thread count instead of a pool count
            # that is always 1 (the old per-camera pools are what this field was watching
            # accumulate, and they no longer exist).
            try:    n_campools = self._cam_pool.activeThreadCount()
            except Exception: n_campools = -1
            n_saved    = len(getattr(self, "_saved_timestamps", []) or [])
            # Counting QPixmap/QImage instances meant two Python-level passes over
            # gc.get_objects() — 700k+ objects once a long window is scanned — on the
            # event loop, i.e. a several-hundred-ms freeze every minute. The counts
            # never told us anything the RSS figure doesn't, so only the cheap total
            # is kept (one list build, no per-object work).
            n_gc = -1
            n_pix = n_img = -1
            try:
                n_gc = len(gc.get_objects())
            except Exception:
                pass
            try:    axis_h = (self.axis_max_ns - self.axis_min_ns) / 3.6e12
            except Exception: axis_h = -1
            up = (time.monotonic() - getattr(self, "_diag_t0", time.monotonic())) / 60.0
            # How the last minute's frames reached the screen, and how much of the
            # preview is decoded. prev ≫ load means the slider is running from RAM;
            # load-only with prox < 100 % is the "a frame a second" state.
            tracks  = getattr(self, "_proxy_tracks", []) or []
            planned = sum(len(t.planned) for t in tracks)
            decoded = sum(len(t.frames) for t in tracks)
            prox    = int(100 * decoded / planned) if planned else -1
            # Refusal RATE, not just the raw count: `miss` alone cannot be read without
            # knowing how many paints it sat next to, and this ratio is the single
            # number that says whether the preview is carrying the drag.
            _served = self._diag_prev + self._diag_miss
            refuse  = int(100 * self._diag_miss / _served) if _served else -1
            paints  = (f"prev={self._diag_prev:6d} cach={self._diag_cach:6d} "
                       f"miss={self._diag_miss:6d} load={self._diag_load:5d} "
                       f"prox={prox:4d}% refuse={refuse:4d}% ")
            self._diag_prev = self._diag_miss = self._diag_load = self._diag_cach = 0
            # What the preview actually costs, and how coarse it is. The RAM budget was
            # never verifiable before (rss was always -1) and the plan step — the thing
            # that decides whether a drag can show every frame — was never logged at
            # all, so a sampled preview looked identical to a complete one.
            prox_mb = -1.0
            try:
                prox_mb = sum(t.nbytes() for t in tracks) / (1024 * 1024)
            except Exception:
                pass
            render_mb = -1.0
            try:
                _rc = getattr(self, "_proxy_render_cache", None)
                if _rc is not None:
                    render_mb = sum(
                        (pm.width() * pm.height() * pm.depth() / 8)
                        for pm in _rc._d.values()) / (1024 * 1024)
            except Exception:
                pass
            steps   = ",".join(str(getattr(t, "step", -1)) for t in tracks) or "-"
            # The distance a preview paint may currently stand in at — the number that
            # decides whether a tile reads amber or red (see _proxy_motion_tol).
            tol_s = -1.0
            try:
                if tracks:
                    tol_s = max(self._proxy_motion_tol(i)
                                for i in range(len(tracks))) / 1e9
            except Exception:
                pass
            prox_txt = (f"proxMB={prox_mb:7.1f} renderMB={render_mb:6.1f} "
                        f"step={steps:11s} tolS={tol_s:7.2f} ")
            # The two live lags, kept apart because they have different owners.
            # srcLag = wall clock − newest frame we know of: how far behind real
            # time the ARCHIVE is, which no amount of local work can shorten.
            # shownLag = newest known frame − frame actually on the tile: the part
            # that is ours. Worst camera of each; -1 when not applicable.
            src_lag = shown_lag = -1.0
            try:
                cam_ts_lists = getattr(self, "_cam_ts", []) or []
                newest = [c[-1] for c in cam_ts_lists if c]
                if newest:
                    src_lag = max(0.0, time.time() - max(newest) / 1e9)
                    shown = getattr(self, "_cam_shown_ts_ns", []) or []
                    lags = [(c[-1] - shown[i]) / 1e9
                            for i, c in enumerate(cam_ts_lists)
                            if c and i < len(shown) and shown[i]]
                    if lags:
                        shown_lag = max(0.0, max(lags))
            except Exception:
                pass
            lags_txt = f"srcLag={src_lag:6.1f}s shownLag={shown_lag:6.1f}s "
            line = (f"{datetime.now():%Y-%m-%d %H:%M:%S} up={up:6.1f}m " + paints +
                    prox_txt + lags_txt +
                    f"rss={rss_mb:8.1f}MB thr={n_threads:3d} pools={n_pools:3d} "
                    f"campools={n_campools:2d} children={n_children:6d} "
                    f"items={n_items:7d} camTot={cam_total:8d} camMax={cam_max:7d} "
                    f"watch={n_watch}/{n_walive} saved={n_saved} gcObj={n_gc:8d} "
                    f"qpix={n_pix:6d} qimg={n_img:6d} axisH={axis_h:5.1f} "
                    f"online={int(bool(getattr(self, '_online_mode', False)))}\n")
            base = (Path(_sys.executable).resolve().parent
                    if getattr(_sys, "frozen", False)
                    else Path(__file__).resolve().parent)
            with open(base / "image_tools_diag.log", "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _on_section_toggled(self, key: str, expanded: bool):
        self._ui_state[key] = expanded
        self._save_ui_state()

    def _set_all_sections(self, expanded: bool):
        for key, sec in self._sections.items():
            sec.set_expanded(expanded)
            self._ui_state[key] = expanded
        self._save_ui_state()

    # ---------------------------------------------------------------- build UI
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        self._root_layout = root

        # ═══════════════════════ LEFT PANEL ═══════════════════════
        left_scroll = QScrollArea()
        left_scroll.setFixedWidth(275)
        left_scroll.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollBar:vertical { width: 8px; }")

        left = QWidget()
        left.setMinimumWidth(255)
        left.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        llay.setSpacing(4)

        # ── Collapsible sections scaffolding ───────────────────────
        self._ui_state = self._load_ui_state()
        self._sections: "dict[str, CollapsibleSection]" = {}

        def _add_section(key, title, default_expanded, accent=None):
            expanded = bool(self._ui_state.get(key, default_expanded))
            sec = CollapsibleSection(title, key, expanded, accent=accent)
            sec.toggled.connect(self._on_section_toggled)
            self._sections[key] = sec
            llay.addWidget(sec)
            return sec

        _exp_row = QHBoxLayout()
        _exp_row.setSpacing(4)
        _btn_expand_all = QPushButton("Expand all")
        _btn_expand_all.setToolTip("Expand all sections")
        _btn_expand_all.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_expand_all.clicked.connect(lambda: self._set_all_sections(True))
        _btn_collapse_all = QPushButton("Collapse all")
        _btn_collapse_all.setToolTip("Collapse all sections")
        _btn_collapse_all.setStyleSheet("QPushButton { font-size: 10px; padding: 2px 4px; }")
        _btn_collapse_all.clicked.connect(lambda: self._set_all_sections(False))
        _exp_row.addWidget(_btn_expand_all)
        _exp_row.addWidget(_btn_collapse_all)
        llay.addLayout(_exp_row)

        s_src  = _add_section("source",   "Source",           True,  "#2f6fd0")  # blue
        s_save = _add_section("save",     "Save",             True,  "#c0392b")  # red
        s_tl   = _add_section("timeline", "Timeline & Range", True,  "#2e9e5b")  # green
        s_disp = _add_section("display",  "Image / Display",  False, "#7a4fc0")  # purple
        s_pv   = _add_section("pv",       "PV Values",        True,  "#1a9e9e")  # teal
        s_ovl  = _add_section("overlays", "Overlays",         False, "#d08a1e")  # amber
        s_an   = _add_section("analysis", "Analysis",         False, "#b0396b")  # magenta

        # ══════════════════ Section: SOURCE ═══════════════════════
        self.btn_date = QPushButton("Time window")
        self.btn_date.setToolTip("Select a date and hour range to load images from")
        self.btn_date.clicked.connect(self.open_by_date)
        self.btn_open = QPushButton("Camera")
        self.btn_open.setToolTip("Select a camera folder to load images from")
        self.btn_open.clicked.connect(self.open_folder)
        # Refresh has no purpose in live/online mode, so it is removed from the
        # Source UI. The widget is still created (never added to a layout) so the
        # many setEnabled/setText calls elsewhere keep working without changes.
        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.setToolTip("Reload new frames from the same folders without resetting position")
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setVisible(False)
        self.btn_refresh.clicked.connect(self.refresh_folder)
        self._btn_auto_follow = QPushButton("⇢ Live mode")
        self._btn_auto_follow.setCheckable(True)
        self._btn_auto_follow.setEnabled(False)
        self._btn_auto_follow.setToolTip(
            "Live mode: new images are loaded as they arrive and the slider follows "
            "the newest one.\nTurns itself off when you move the slider.\n"
            "Green = on, red = off. Enable it in the Time window dialog to start live.")
        self._btn_auto_follow.toggled.connect(self._on_auto_follow_toggled)
        self._refresh_live_btn_style()
        row = QHBoxLayout(); row.addWidget(self.btn_date); row.addWidget(self.btn_open)
        s_src.body_layout.addLayout(row)
        row_follow = QHBoxLayout()
        row_follow.addWidget(self._btn_auto_follow)
        s_src.body_layout.addLayout(row_follow)

        # ══════════════════ Section: TIMELINE & RANGE ═════════════
        self.btn_prev = QPushButton("◀"); self.btn_prev.setToolTip("Previous image (←)"); self.btn_prev.setEnabled(False)
        self.btn_prev.setFixedWidth(32)
        self.btn_prev.clicked.connect(lambda: self.step_frame(-1))
        self.btn_next = QPushButton("▶"); self.btn_next.setToolTip("Next image (→)"); self.btn_next.setEnabled(False)
        self.btn_next.setFixedWidth(32)
        self.btn_next.clicked.connect(lambda: self.step_frame(+1))
        self.btn_play = QPushButton("Play"); self.btn_play.setEnabled(False)
        self.btn_play.setToolTip("Start playback")
        self.btn_play.clicked.connect(self.play)
        self.btn_stop = QPushButton("Stop"); self.btn_stop.setEnabled(False)
        self.btn_stop.setToolTip("Stop playback")
        self.btn_stop.clicked.connect(self.stop)
        row3a = QHBoxLayout()
        row3a.addWidget(self.btn_prev); row3a.addWidget(self.btn_next)
        row3a.addWidget(self.btn_play); row3a.addWidget(self.btn_stop)
        s_tl.body_layout.addLayout(row3a)

        self.speed_cb = PopupBelowComboBox()
        self.speed_cb.setToolTip(
            "Speed = % of the loaded images per second.")
        self.speed_cb.setMaxVisibleItems(12)
        # Nothing above 5 %/s: on a window of any size those rates need a stride far
        # larger than one frame, so they skip most of what they play — the file names
        # scroll but almost nothing is actually shown. The slow end is where the useful
        # settings are.
        for label, val in [
            ("0.10 %/s", 0.10),
            ("0.25 %/s",  0.25),
            ("0.5 %/s",  0.5),
            ("1 %/s",  1.0),
            ("2 %/s",   2.0),
            ("5 %/s",   5.0),
        ]:
            self.speed_cb.addItem(label, val)
        self.speed_cb.setCurrentIndex(3)  # default 1 %/s
        self.speed_cb.setStyleSheet(
            "QComboBox { padding: 3px 6px; background: #fff; border: 1px solid #ccc; border-radius: 4px; }"
            "QComboBox QAbstractItemView { background: #fff; }")
        row3b = QHBoxLayout(); row3b.addWidget(QLabel("Speed:")); row3b.addWidget(self.speed_cb, 1)
        s_tl.body_layout.addLayout(row3b)

        # range marks (moved here from old Settings group, next to playback)
        self.btn_set_a = QPushButton("Set From"); self.btn_set_a.setEnabled(False)
        self.btn_set_a.setToolTip("Set range start (From) to current position")
        self.btn_set_a.clicked.connect(self.set_mark_a)
        self.btn_set_b = QPushButton("Set To"); self.btn_set_b.setEnabled(False)
        self.btn_set_b.setToolTip("Set range end (To) to current position")
        self.btn_set_b.clicked.connect(self.set_mark_b)
        self.btn_clear_marks = QPushButton("Clear"); self.btn_clear_marks.setEnabled(False)
        self.btn_clear_marks.setToolTip("Clear From/To marks")
        self.btn_clear_marks.clicked.connect(self.clear_marks)
        row_marks = QHBoxLayout()
        row_marks.addWidget(self.btn_set_a); row_marks.addWidget(self.btn_set_b); row_marks.addWidget(self.btn_clear_marks)
        s_tl.body_layout.addLayout(row_marks)

        # timestamps (nested under Timeline & Range)
        s_tl.body_layout.addWidget(_group_label("Timestamps"))
        self.btn_save_ts = QPushButton("📌 Save Timestamp")
        self.btn_save_ts.setToolTip("Save current timestamp for cross-camera lookup. You can save more timestamps.")
        self.btn_save_ts.setEnabled(False)
        self.btn_save_ts.clicked.connect(self._save_current_timestamp)
        self.btn_goto_ts = QPushButton("⇢ Go to Saved")
        self.btn_goto_ts.setToolTip("Jump to nearest frame matching a saved timestamp")
        self.btn_goto_ts.setEnabled(False)
        self.btn_goto_ts.clicked.connect(self._goto_saved_timestamp)
        self.btn_clear_ts = QPushButton("✕ Clear")
        self.btn_clear_ts.setToolTip("Clear all saved timestamps")
        self.btn_clear_ts.setEnabled(False)
        self.btn_clear_ts.clicked.connect(self._clear_timestamps)
        # Row 1: Save Timestamp (full width)
        row_ts1 = QHBoxLayout()
        row_ts1.addWidget(self.btn_save_ts)
        s_tl.body_layout.addLayout(row_ts1)
        # Row 2: Go to Saved + Clear
        row_ts2 = QHBoxLayout()
        row_ts2.addWidget(self.btn_goto_ts)
        row_ts2.addWidget(self.btn_clear_ts)
        s_tl.body_layout.addLayout(row_ts2)
        self.lbl_ts_status = QLabel("No timestamps saved.")
        self.lbl_ts_status.setWordWrap(True)
        self.lbl_ts_status.setStyleSheet("font-size: 10px; color: #555;")
        s_tl.body_layout.addWidget(self.lbl_ts_status)

        # ══════════════════ Section: SAVE ═════════════════════════
        self.btn_save = QPushButton("Save Image"); self.btn_save.setEnabled(False)
        self.btn_save.setToolTip("Save current image to disk")
        self.btn_save.clicked.connect(self.save_current)
        self.btn_save_range = QPushButton("Save Range"); self.btn_save_range.setEnabled(False)
        self.btn_save_range.setToolTip("Save all images between Set From and Set To marks")
        self.btn_save_range.clicked.connect(self.save_range)
        row2a = QHBoxLayout()
        row2a.addWidget(self.btn_save)
        row2a.addWidget(self.btn_save_range)
        s_save.body_layout.addLayout(row2a)
        self.btn_send_workshop = QPushButton("➤ Workshop")
        self.btn_send_workshop.setEnabled(False)
        self.btn_send_workshop.setToolTip("Send current image to Workshop tab for editing")
        self.btn_send_workshop.clicked.connect(self._send_to_workshop)
        s_save.body_layout.addWidget(self.btn_send_workshop)
        row2c = QHBoxLayout()
        self.cb_save_overlay = QCheckBox("Save with overlay")
        self.cb_save_overlay.setToolTip("When saving, burn overlays (cross/circle/square) and PV values into the image")
        self.cb_save_overlay.setStyleSheet(_CHECKBOX_STYLE)
        row2c.addWidget(self.cb_save_overlay)
        row2c.addStretch(1)
        s_save.body_layout.addLayout(row2c)
        # Range-save (± N frames around current) — off by default, spinbox is the count.
        row2d = QHBoxLayout()
        self.cb_save_around = QCheckBox("Save ±")
        self.cb_save_around.setToolTip("Also save N frames before and after the current one")
        self.cb_save_around.setStyleSheet(_CHECKBOX_STYLE)
        row2d.addWidget(self.cb_save_around)
        self.save_around_n_sb = QSpinBox()
        self.save_around_n_sb.setRange(0, 10000)
        self.save_around_n_sb.setValue(0)
        self.save_around_n_sb.setFixedWidth(48)
        self.save_around_n_sb.setEnabled(False)
        self.save_around_n_sb.setToolTip("Number of frames before and after current to save")
        self.cb_save_around.toggled.connect(self.save_around_n_sb.setEnabled)
        row2d.addWidget(self.save_around_n_sb)
        row2d.addWidget(QLabel("frames"))
        row2d.addStretch(1)
        s_save.body_layout.addLayout(row2d)
        self.cb_save_metadata_txt = QCheckBox("Save metadata .txt")
        self.cb_save_metadata_txt.setToolTip("Also write a sidecar .txt file with the original image metadata")
        self.cb_save_metadata_txt.setStyleSheet(_CHECKBOX_STYLE)
        s_save.body_layout.addWidget(self.cb_save_metadata_txt)
        self.cb_save_original = QCheckBox("Save original (unmodified)")
        self.cb_save_original.setToolTip("When saving with default palette and no overlay, save the original unmodified file instead of skipping")
        self.cb_save_original.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_save_original.setChecked(True)
        s_save.body_layout.addWidget(self.cb_save_original)

        # ══════════════════ Section: IMAGE / DISPLAY ══════════════
        # Contrast: manual slider + "Auto" checkbox (percentile auto-stretch).
        # The Auto checkbox overrides the slider — the app-wide "checkbox is
        # superior to slider" rule for each enhancement pair.
        self.cb_bright = QCheckBox("Auto"); self.cb_bright.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_bright.setToolTip("Auto-stretch contrast (percentile) — overrides the Contrast slider")
        self.cb_bright.stateChanged.connect(self._on_contrast_auto_changed)
        row_contrast = QHBoxLayout()
        row_contrast.addWidget(QLabel("Contrast:"))
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(-127, 127)
        self.contrast_slider.setValue(0)
        self.contrast_slider.setToolTip("Manual contrast (-127 to +127)")
        self.contrast_slider.valueChanged.connect(self._on_contrast_slider_changed)
        # The manual value to come back to when Auto is switched off — the greyed-out
        # slider is overwritten while Auto is on (see _refresh_auto_bc_sliders).
        self._contrast_manual = 0
        row_contrast.addWidget(self.contrast_slider, 1)
        self.btn_contrast_reset = QPushButton("↺")
        self.btn_contrast_reset.setFixedWidth(28)
        self.btn_contrast_reset.setToolTip("Reset contrast")
        self.btn_contrast_reset.clicked.connect(self._reset_contrast_slider)
        row_contrast.addWidget(self.btn_contrast_reset)
        row_contrast.addWidget(self.cb_bright)
        s_disp.body_layout.addLayout(row_contrast)
        # Brightness: manual offset slider + "Auto" checkbox (auto-level).
        self.cb_bright_auto = QCheckBox("Auto"); self.cb_bright_auto.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_bright_auto.setToolTip("Auto-level brightness — overrides the Brightness slider")
        self.cb_bright_auto.stateChanged.connect(self._on_bright_auto_changed)
        row_bright_slider = QHBoxLayout()
        row_bright_slider.addWidget(QLabel("Brightness:"))
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(-255, 255)
        self.brightness_slider.setValue(0)
        self.brightness_slider.setToolTip("Manual brightness offset (-255 to +255)")
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        # Same as _contrast_manual: the value to come back to when Auto is switched off.
        self._brightness_manual = 0
        row_bright_slider.addWidget(self.brightness_slider, 1)
        self.btn_brightness_reset = QPushButton("↺")
        self.btn_brightness_reset.setFixedWidth(28)
        self.btn_brightness_reset.setToolTip("Reset brightness")
        self.btn_brightness_reset.clicked.connect(self._reset_brightness_slider)
        row_bright_slider.addWidget(self.btn_brightness_reset)
        row_bright_slider.addWidget(self.cb_bright_auto)
        s_disp.body_layout.addLayout(row_bright_slider)
        # Palette / gradient
        self.gradient_cb = PopupBelowComboBox()
        self.gradient_cb.setToolTip("Color gradient for image display")
        for name in GRADIENT_NAMES:
            self.gradient_cb.addItem(name)
        self.gradient_cb.setCurrentIndex(2)  # default: Gradient (0=Default, 1=Grayscale, 2+=palettes)
        self.gradient_cb.setStyleSheet(
            "QComboBox { padding: 3px 6px; background: #fff; border: 1px solid #ccc; border-radius: 4px; }"
            "QComboBox QAbstractItemView { background: #fff; }")
        self.gradient_cb.currentIndexChanged.connect(self._on_gradient_changed)
        row_grad = QHBoxLayout()
        row_grad.addWidget(QLabel("Palette:"))
        row_grad.addWidget(self.gradient_cb, 1)
        s_disp.body_layout.addLayout(row_grad)
        row_sub = QHBoxLayout()
        self.cb_subtract = QCheckBox("Subtraction")
        self.cb_subtract.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_subtract.setToolTip("Show absolute difference from reference frame")
        self.cb_subtract.stateChanged.connect(self._on_subtract_changed)
        row_sub.addWidget(self.cb_subtract)
        self.btn_set_ref = QPushButton("Set ref")
        self.btn_set_ref.setFixedWidth(65)
        self.btn_set_ref.setEnabled(False)
        self.btn_set_ref.setToolTip("Set current frame as subtraction reference")
        self.btn_set_ref.clicked.connect(self._set_reference_frame)
        row_sub.addWidget(self.btn_set_ref)
        row_sub.addStretch(1)
        s_disp.body_layout.addLayout(row_sub)
        self.cb_preload_preview = QCheckBox("Preload preview")
        self.cb_preload_preview.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_preload_preview.setToolTip(
            "Decode the whole loaded time window once in the background at low\n"
            "resolution, so dragging the slider repaints from memory instead of\n"
            "reading one image off the share per position. Progress appears in the\n"
            "INFO panel; the frame you stop on is always re-rendered at full\n"
            "resolution.\n\n"
            "Turn it off to keep all share bandwidth for the frame you are looking\n"
            "at — the slider then loads on demand and will lag on ranges it has not\n"
            "read yet.")
        self.cb_preload_preview.setChecked(bool(self._ui_state.get("preload_preview", True)))
        self.cb_preload_preview.stateChanged.connect(self._on_preload_preview_changed)
        s_disp.body_layout.addWidget(self.cb_preload_preview)
        row_sub_thr = QHBoxLayout()
        row_sub_thr.addWidget(QLabel("Diff threshold:"))
        self.sub_threshold_sb = QSpinBox()
        self.sub_threshold_sb.setRange(0, 255)
        self.sub_threshold_sb.setValue(0)
        self.sub_threshold_sb.setFixedWidth(55)
        self.sub_threshold_sb.setToolTip(
            "Pixels with |current − reference| below this value are shown as black.\n"
            "0 = show all differences (default).\n"
            "Useful for ignoring noise and tiny fluctuations.")
        # Without this, typing "100" emits valueChanged for 1, then 10, then 100 —
        # three full re-renders of every camera off the share for one edit.
        self.sub_threshold_sb.setKeyboardTracking(False)
        self.sub_threshold_sb.valueChanged.connect(self._on_subtract_changed)
        row_sub_thr.addWidget(self.sub_threshold_sb)
        row_sub_thr.addSpacing(6)
        row_sub_thr.addWidget(QLabel("Offset:"))
        self.sub_offset_sb = QSpinBox()
        self.sub_offset_sb.setRange(0, 255)
        self.sub_offset_sb.setValue(0)
        self.sub_offset_sb.setFixedWidth(55)
        self.sub_offset_sb.setToolTip(
            "Adds this intensity (0–255 display scale) to every pixel whose\n"
            "|current − reference| is non-zero. Zero-difference pixels stay black.\n"
            "Makes differences of 1–2 counts visible; absolute difference values\n"
            "can no longer be read off the image (the statistics line still shows them).")
        self.sub_offset_sb.setKeyboardTracking(False)
        self.sub_offset_sb.valueChanged.connect(self._on_subtract_changed)
        row_sub_thr.addWidget(self.sub_offset_sb)
        row_sub_thr.addStretch(1)
        s_disp.body_layout.addLayout(row_sub_thr)
        # reset zoom (moved here from old Playback group)
        self.btn_reset_zoom = QPushButton("⤢ Reset zoom")
        self.btn_reset_zoom.setToolTip("Reset zoom to full image (right-click drag to zoom in)")
        self.btn_reset_zoom.clicked.connect(self._on_reset_zoom)
        s_disp.body_layout.addWidget(self.btn_reset_zoom)
        # camera label size (moved here from old Camera Labels Settings group)
        row_cam_font = QHBoxLayout()
        row_cam_font.addWidget(QLabel("Label size:"))
        self._cam_label_size_sb = QSpinBox()
        self._cam_label_size_sb.setRange(8, 32)
        self._cam_label_size_sb.setValue(20)
        self._cam_label_size_sb.setSuffix(" px")
        self._cam_label_size_sb.valueChanged.connect(self._on_cam_label_size_changed)
        row_cam_font.addWidget(self._cam_label_size_sb)
        row_cam_font.addStretch(1)
        s_disp.body_layout.addLayout(row_cam_font)

        # ══════════════════ Section: OVERLAYS ═════════════════════
        self.cb_cross  = QCheckBox("Cross");  self.cb_cross.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_cross.setToolTip("Show cross overlay on image")
        self.cb_circle = QCheckBox("Circle"); self.cb_circle.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_circle.setToolTip("Show circle overlay on image")
        self.cb_square = QCheckBox("Square"); self.cb_square.setStyleSheet(_CHECKBOX_STYLE)
        self.cb_square.setToolTip("Show square overlay on image")

        self.cb_cross.stateChanged.connect(self._on_overlay_changed)
        self.cb_circle.stateChanged.connect(self._on_overlay_changed)
        self.cb_square.stateChanged.connect(self._on_overlay_changed)

        self.btn_draw_cross  = QPushButton("✚ Draw")
        self.btn_draw_circle = QPushButton("◯ Draw")
        self.btn_draw_square = QPushButton("◻ Draw")
        self.btn_draw_cross.setToolTip("Click on image to place cross")
        self.btn_draw_circle.setToolTip("Drag to draw ellipse, Shift = circle")
        self.btn_draw_square.setToolTip("Drag to draw rectangle, Shift = square")
        self.btn_draw_circle.clicked.connect(lambda: self._toggle_draw_mode("circle"))
        self.btn_draw_square.clicked.connect(lambda: self._toggle_draw_mode("square"))
        self.btn_draw_cross.clicked.connect(lambda: self._toggle_draw_mode("cross"))

        self.btn_cal_circle = QPushButton("◯ Cal"); self.btn_cal_circle.setEnabled(False)
        self.btn_cal_circle.setToolTip("Auto-detect circle in current frame")
        self.btn_cal_circle.clicked.connect(self.calibrate_circle)
        self.btn_cal_square = QPushButton("◻ Cal"); self.btn_cal_square.setEnabled(False)
        self.btn_cal_square.setToolTip("Auto-detect rectangle in current frame")
        self.btn_cal_square.clicked.connect(self.calibrate_square)

        # prázdný placeholder pro cross (cal neexistuje)
        _lbl_empty = QLabel("")

        self.btn_cal_cross = QPushButton("✚ Cal"); self.btn_cal_cross.setEnabled(False)
        self.btn_cal_cross.setToolTip("Auto-detect center of brightness (centroid)")
        self.btn_cal_cross.clicked.connect(self.calibrate_cross)

        grid = QGridLayout()
        grid.setSpacing(3)
        #              col 0          col 1              col 2
        grid.addWidget(self.cb_cross,        0, 0)
        grid.addWidget(self.cb_circle,       0, 1)
        grid.addWidget(self.cb_square,       0, 2)
        grid.addWidget(self.btn_draw_cross,  1, 0)
        grid.addWidget(self.btn_draw_circle, 1, 1)
        grid.addWidget(self.btn_draw_square, 1, 2)
        grid.addWidget(_lbl_empty,           2, 0)
        grid.addWidget(self.btn_cal_circle,  2, 1)
        grid.addWidget(self.btn_cal_square,  2, 2)
        grid.addWidget(self.btn_cal_cross,   2, 0)
        s_ovl.body_layout.addLayout(grid)

        overlay_settings_row = QHBoxLayout()
        overlay_settings_row.setSpacing(4)
        btn_overlay_settings = QPushButton("⚙ Overlay settings")
        btn_overlay_settings.setToolTip("Set color, thickness and size of overlays")
        btn_overlay_settings.clicked.connect(self._open_overlay_settings)
        overlay_settings_row.addWidget(btn_overlay_settings, 1)
        btn_remove_all_overlays = QPushButton("✕ Remove selected")
        btn_remove_all_overlays.setToolTip("Remove all overlays from selected camera(s)")
        btn_remove_all_overlays.clicked.connect(self._remove_all_overlays)
        overlay_settings_row.addWidget(btn_remove_all_overlays, 1)
        s_ovl.body_layout.addLayout(overlay_settings_row)

        # _online_dot and _online_lbl kept as non-visible widgets for backward compat
        self._online_dot = QLabel("●")
        self._online_dot.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot.hide()
        self._online_lbl = QLabel("Inactive")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._online_lbl.hide()

        # ══════════════════ Section: ANALYSIS ═════════════════════
        # ── Subgroup: Pointing Analysis ───────────────────────────────
        s_an.body_layout.addWidget(_group_label("Pointing Analysis"))
        row_thr_mag = QHBoxLayout()
        row_thr_mag.addWidget(QLabel("Thr:"))
        self.pointing_threshold_sb = QSpinBox()
        self.pointing_threshold_sb.setRange(0, 99)
        self.pointing_threshold_sb.setValue(70)
        self.pointing_threshold_sb.setSuffix(" %")
        self.pointing_threshold_sb.setFixedWidth(62)
        self.pointing_threshold_sb.setToolTip(
            "Threshold as % of the peak intensity (after background subtraction).\n"
            "Only pixels brighter than this fraction of the peak feed the centroid.\n"
            "70% = ignore everything below 70% of the brightest pixel (bright core only).\n"
            "Raise if noise pulls the centroid; lower if the beam gets cut off.")
        row_thr_mag.addWidget(self.pointing_threshold_sb)
        row_thr_mag.addSpacing(6)
        row_thr_mag.addWidget(QLabel("M:"))
        self.pointing_m_sb = QDoubleSpinBox()
        self.pointing_m_sb.setRange(0.001, 1000.0)
        self.pointing_m_sb.setValue(1.0)
        self.pointing_m_sb.setDecimals(3)
        self.pointing_m_sb.setFixedWidth(65)
        self.pointing_m_sb.setToolTip(
            "Multiplier applied to the measured X/Y pointing offsets.\n"
            "1 = raw sensor pixels (the plot axes are in pixels).\n"
            "Set a calibration factor to scale the values, e.g. pixels → µrad or mm.")
        row_thr_mag.addWidget(self.pointing_m_sb)
        row_thr_mag.addStretch(1)
        s_an.body_layout.addLayout(row_thr_mag)

        self.btn_pointing = QPushButton("▶ Run Analysis")
        self.btn_pointing.setToolTip("Run pointing stability analysis on current images set by timestamps (Set From and Set To)")
        self.btn_pointing.setEnabled(False)
        self.btn_pointing.clicked.connect(self.run_pointing_analysis)
        self.btn_pointing_live = QPushButton("▶ Replay")
        self.btn_pointing_live.setToolTip(
            "Replay: step through pointing analysis results frame by frame.\n"
            "Shows each image + highlights its point on the graph.")
        self.btn_pointing_live.setCheckable(True)
        self.btn_pointing_live.setEnabled(False)
        self.btn_pointing_live.toggled.connect(self._on_pointing_live_toggled)
        self.btn_pointing_cancel = QPushButton("Cancel")
        self.btn_pointing_cancel.setToolTip("Cancel running analysis")
        self.btn_pointing_cancel.setVisible(False)
        self.btn_pointing_cancel.clicked.connect(self._cancel_pointing)
        row_pa = QHBoxLayout()
        row_pa.addWidget(self.btn_pointing)
        row_pa.addWidget(self.btn_pointing_live)
        row_pa.addWidget(self.btn_pointing_cancel)
        s_an.body_layout.addLayout(row_pa)

        # Replay speed control
        row_replay_speed = QHBoxLayout()
        row_replay_speed.addWidget(QLabel("Replay speed:"))
        self._pointing_replay_fps_sb = QSpinBox()
        self._pointing_replay_fps_sb.setRange(1, 100)
        self._pointing_replay_fps_sb.setValue(10)
        self._pointing_replay_fps_sb.setSuffix(" %")
        self._pointing_replay_fps_sb.setFixedWidth(70)
        self._pointing_replay_fps_sb.setToolTip(
            "Replay speed as % of max (100% = 200 fps, 10% = 20 fps, 1% = 2 fps)")
        row_replay_speed.addWidget(self._pointing_replay_fps_sb)
        row_replay_speed.addStretch(1)
        s_an.body_layout.addLayout(row_replay_speed)

        self.btn_pointing_save = QPushButton("💾 Save Plot")
        self.btn_pointing_save.setToolTip("Save pointing plot as PNG or PDF")
        self.btn_pointing_save.setEnabled(False)
        self.btn_pointing_save.clicked.connect(self._save_pointing_plot)
        self.btn_pointing_path = QPushButton("〰 Show Path")
        self.btn_pointing_path.setToolTip("Show/hide beam path trajectory colored by time")
        self.btn_pointing_path.setEnabled(False)
        self.btn_pointing_path.clicked.connect(self._toggle_pointing_path)
        row_pa2 = QHBoxLayout()
        row_pa2.addWidget(self.btn_pointing_save)
        row_pa2.addWidget(self.btn_pointing_path)
        s_an.body_layout.addLayout(row_pa2)
        self.btn_pointing_select = QPushButton("🗑 Delete mode")
        self.btn_pointing_select.setToolTip(
            "Delete mode: every dragged rectangle deletes the points inside it "
            "immediately. Stays active until you click the button again.")
        self.btn_pointing_select.setEnabled(False)
        self.btn_pointing_select.setCheckable(True)
        self.btn_pointing_select.clicked.connect(self._toggle_pointing_select)
        self.btn_pointing_restore = QPushButton("↺ Restore All")
        self.btn_pointing_restore.setToolTip("Restore all deleted points")
        self.btn_pointing_restore.setEnabled(False)
        self.btn_pointing_restore.clicked.connect(self._restore_pointing_points)
        row_pa3 = QHBoxLayout()
        row_pa3.addWidget(self.btn_pointing_select)
        row_pa3.addWidget(self.btn_pointing_restore)
        s_an.body_layout.addLayout(row_pa3)
        self.btn_pointing_close = QPushButton("✕ Close graph")
        self.btn_pointing_close.setEnabled(False)
        self.btn_pointing_close.setToolTip("Hide the pointing analysis graph")
        self.btn_pointing_close.clicked.connect(self._close_pointing_panel)
        s_an.body_layout.addWidget(self.btn_pointing_close)

        self.lbl_pointing_status = QLabel("")
        self.lbl_pointing_status.setWordWrap(True)
        self.lbl_pointing_status.setStyleSheet("font-size: 10px; color: #555;")
        s_an.body_layout.addWidget(self.lbl_pointing_status)

        s_an.body_layout.addWidget(_hsep())

        # ── Subgroup: Spatial Contrast ────────────────────────────────
        s_an.body_layout.addWidget(_group_label("Spatial Contrast"))

        # Camera selector (visible only in multi-cam mode)
        sc_cam_row = QHBoxLayout()
        sc_cam_row.addWidget(QLabel("Camera:"))
        self._sc_cam_combo = QComboBox()
        self._sc_cam_combo.setToolTip("Select which camera to measure")
        sc_cam_row.addWidget(self._sc_cam_combo, 1)
        sc_cam_row_widget = QWidget()
        sc_cam_row_widget.setLayout(sc_cam_row)
        sc_cam_row_widget.setVisible(False)
        self._sc_cam_row_widget = sc_cam_row_widget
        s_an.body_layout.addWidget(sc_cam_row_widget)

        # Threshold row
        sc_thr_row = QHBoxLayout()
        sc_thr_row.addWidget(QLabel("Threshold:"))
        self._sc_threshold_sb = QSpinBox()
        self._sc_threshold_sb.setRange(0, 65535)
        self._sc_threshold_sb.setValue(2000)
        self._sc_threshold_sb.setSuffix("")
        self._sc_threshold_sb.setFixedWidth(80)
        self._sc_threshold_sb.setToolTip(
            "Pixels with raw intensity ≤ threshold are treated as background.\n"
            "Raise to include only the bright beam core; lower to include the full beam halo.\n"
            "16-bit images: range 0–65535. 8-bit images: range 0–255.")
        self._sc_threshold_sb.valueChanged.connect(self._on_sc_threshold_changed)
        sc_thr_row.addWidget(self._sc_threshold_sb)
        self._btn_sc_auto_thr = QPushButton("Auto")
        self._btn_sc_auto_thr.setFixedWidth(42)
        self._btn_sc_auto_thr.setToolTip(
            "Automatically set threshold using Otsu's method\n"
            "(separates background from beam)")
        self._btn_sc_auto_thr.setEnabled(False)
        self._btn_sc_auto_thr.clicked.connect(self._run_sc_auto_threshold)
        sc_thr_row.addWidget(self._btn_sc_auto_thr)
        self._btn_sc_hist = QPushButton("Manual")
        self._btn_sc_hist.setFixedWidth(58)
        self._btn_sc_hist.setToolTip(
            "Open histogram — click or drag to set threshold manually")
        self._btn_sc_hist.setEnabled(False)
        self._btn_sc_hist.clicked.connect(self._open_sc_histogram)
        sc_thr_row.addWidget(self._btn_sc_hist)
        sc_thr_row.addStretch(1)
        s_an.body_layout.addLayout(sc_thr_row)

        # Measure + Draw exclusions buttons
        sc_btn_row = QHBoxLayout()
        self._btn_sc_measure = QPushButton("▶ Measure")
        self._btn_sc_measure.setToolTip("Compute Spatial Contrast for the current frame")
        self._btn_sc_measure.clicked.connect(self._run_spatial_contrast)
        sc_btn_row.addWidget(self._btn_sc_measure, 2)
        self._btn_sc_draw = QPushButton("✏ Exclude")
        self._btn_sc_draw.setToolTip(
            "Open exclusion editor — draw regions to exclude from the measurement")
        self._btn_sc_draw.clicked.connect(self._open_sc_exclusion_editor)
        sc_btn_row.addWidget(self._btn_sc_draw, 1)
        s_an.body_layout.addLayout(sc_btn_row)

        # "Show top intensity pixels: N" — circle top-N highest-intensity pixels on the image
        sc_topn_row = QHBoxLayout()
        sc_topn_row.addWidget(QLabel("Show top intensity:"))
        self._sc_topn_sb = QSpinBox()
        self._sc_topn_sb.setRange(0, 9999)
        self._sc_topn_sb.setValue(0)
        self._sc_topn_sb.setFixedWidth(70)
        self._sc_topn_sb.setToolTip(
            "Circle the top-N highest-intensity pixels on the image after measuring.\n"
            "0 = disabled.")
        self._sc_topn_sb.valueChanged.connect(self._on_sc_topn_changed)
        sc_topn_row.addWidget(self._sc_topn_sb)
        sc_topn_row.addWidget(QLabel("px"))
        sc_topn_row.addStretch(1)
        s_an.body_layout.addLayout(sc_topn_row)

        # Marker appearance: radius + thickness
        sc_marker_row = QHBoxLayout()
        sc_marker_row.addWidget(QLabel("Marker radius:"))
        self._sc_marker_r_sb = QSpinBox()
        self._sc_marker_r_sb.setRange(1, 100)
        self._sc_marker_r_sb.setValue(5)
        self._sc_marker_r_sb.setFixedWidth(50)
        self._sc_marker_r_sb.setToolTip("Circle radius in pixels for top-intensity markers")
        self._sc_marker_r_sb.valueChanged.connect(self._on_sc_marker_style_changed)
        sc_marker_row.addWidget(self._sc_marker_r_sb)
        sc_marker_row.addWidget(QLabel("Thickness:"))
        self._sc_marker_thick_sb = QSpinBox()
        self._sc_marker_thick_sb.setRange(1, 20)
        self._sc_marker_thick_sb.setValue(2)
        self._sc_marker_thick_sb.setFixedWidth(45)
        self._sc_marker_thick_sb.setToolTip("Line thickness for top-intensity markers")
        self._sc_marker_thick_sb.valueChanged.connect(self._on_sc_marker_style_changed)
        sc_marker_row.addWidget(self._sc_marker_thick_sb)
        sc_marker_row.addStretch(1)
        s_an.body_layout.addLayout(sc_marker_row)

        self._sc_topn_points: "list[tuple[int,int]] | None" = None  # (x,y) pixel coords in full image

        self._sc_set_enabled(False)   # both buttons exist now — safe to call
        # Exclusion mask is stored per image path so it resets on image change
        self._sc_exclusion_mask: "np.ndarray | None" = None  # bool array, True = excluded
        self._sc_exclusion_path: "Path | None" = None        # path the mask belongs to

        # Results — compact 2-per-row layout
        _sc_grid = QGridLayout()
        _sc_grid.setSpacing(3)
        _sc_grid.setContentsMargins(0, 2, 0, 2)
        _sc_val_style = (
            "font-size: 11px; font-family: monospace; color: #111; "
            "background: #f5f5f5; border: 1px solid #ccc; "
            "border-radius: 2px; padding: 1px 4px;")
        _sc_lbl_style = "font-size: 11px; color: #444;"

        def _sc_cell(label: str, grid_row: int, grid_col: int) -> "_SCValueLabel":
            lbl = QLabel(label)
            lbl.setStyleSheet(_sc_lbl_style)
            val = _SCValueLabel("—")
            val.setStyleSheet(_sc_val_style)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val.setToolTip("Double-click to copy")
            _sc_grid.addWidget(lbl, grid_row, grid_col * 2)
            _sc_grid.addWidget(val, grid_row, grid_col * 2 + 1)
            _sc_grid.setColumnStretch(grid_col * 2 + 1, 1)
            return val

        # Row 0: Min  |  Max
        self._sc_val_min  = _sc_cell("Min:",  0, 0)
        self._sc_val_max  = _sc_cell("Max:",  0, 1)
        # Row 1: Mean  |  Pixel count
        self._sc_val_mean = _sc_cell("Mean:", 1, 0)
        self._sc_val_beam = _sc_cell("Pixels:", 1, 1)
        # Row 2: SC spanning full width
        sc_lbl_full = QLabel("SC (Max/Mean):")
        sc_lbl_full.setStyleSheet(_sc_lbl_style)
        self._sc_val_sc = _SCValueLabel("—")
        self._sc_val_sc.setStyleSheet(
            "font-size: 12px; font-weight: 700; font-family: monospace; color: #111; "
            "background: #e8f0fe; border: 1px solid #90a8e0; "
            "border-radius: 2px; padding: 1px 4px;")
        self._sc_val_sc.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._sc_val_sc.setToolTip("Double-click to copy")
        _sc_grid.addWidget(sc_lbl_full,     2, 0)
        _sc_grid.addWidget(self._sc_val_sc, 2, 1, 1, 3)
        s_an.body_layout.addLayout(_sc_grid)

        self._sc_cam_lbl = QLabel("")
        self._sc_cam_lbl.setStyleSheet("font-size: 10px; color: #555;")
        s_an.body_layout.addWidget(self._sc_cam_lbl)

        self._sc_status_lbl = QLabel("")
        self._sc_status_lbl.setWordWrap(True)
        self._sc_status_lbl.setStyleSheet("font-size: 10px; color: #c00;")
        s_an.body_layout.addWidget(self._sc_status_lbl)

        # Preview label (shows beam mask overlay, hidden until first measurement)
        self._sc_preview_lbl = _SCPreviewLabel()
        self._sc_preview_lbl.hide()
        s_an.body_layout.addWidget(self._sc_preview_lbl)
        self._sc_preview_pixmap: "QPixmap | None" = None
        self._sc_task_running = False
        self._sc_pending      = False
        self._sc_task_gen     = -1   # scan generation the in-flight/last task was launched for
        self._sc_topn_points: "list[tuple[int,int]]" = []
        self._sc_topn_img_shape: "tuple[int,int] | None" = None

        # ══════════════════ Section: PV VALUES ════════════════════
        pv_header_row = QHBoxLayout()
        self._btn_pv_cfg = QPushButton("⚙ Configure")
        self._btn_pv_cfg.setToolTip("Select which PV channels to display")
        self._btn_pv_cfg.clicked.connect(self._open_pv_config)
        pv_header_row.addWidget(self._btn_pv_cfg)
        self._btn_pv_refresh = QPushButton("↻ Refresh")
        self._btn_pv_refresh.setToolTip("Refresh PV values for current frame")
        self._btn_pv_refresh.clicked.connect(self._pv_force_refresh)
        pv_header_row.addWidget(self._btn_pv_refresh)
        s_pv.body_layout.addLayout(pv_header_row)
        pv_overlay_row = QHBoxLayout()
        self._btn_pv_overlay_settings = QPushButton("⚙ Overlay settings")
        self._btn_pv_overlay_settings.setToolTip("PV overlay display settings")
        self._btn_pv_overlay_settings.clicked.connect(self._open_pv_overlay_settings)
        pv_overlay_row.addWidget(self._btn_pv_overlay_settings)
        s_pv.body_layout.addLayout(pv_overlay_row)

        self._pv_table = QTableWidget(0, 2)
        self._pv_table.setHorizontalHeaderLabels(["PV", "Value"])
        self._pv_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._pv_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._pv_table.verticalHeader().setVisible(False)
        self._pv_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pv_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._pv_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pv_table.setMaximumHeight(120)
        self._pv_table.setVisible(False)
        self._pv_table.setStyleSheet("font-size: 11px;")
        s_pv.body_layout.addWidget(self._pv_table)

        self._pv_no_pv_lbl = QLabel("No PVs selected. Click ⚙ to configure.")
        self._pv_no_pv_lbl.setStyleSheet("font-size: 10px; color: #888; padding: 2px 0;")
        self._pv_no_pv_lbl.setWordWrap(True)
        s_pv.body_layout.addWidget(self._pv_no_pv_lbl)

        # ── Info labels (definice — zobrazí se v ukotvené sekci nahoře) ───
        info_style = "font-size: 11px; color: #222; padding: 1px 0;"
        self.lbl_index          = QLabel("0 / 0")
        self.lbl_date           = QLabel("Date: —")
        self.lbl_selected_range = QLabel("Range: —")
        self.lbl_filename       = QLabel("Filename: —")
        self.lbl_axis_time      = QLabel("Axis: —")
        self.lbl_prague_time    = QLabel("Prague Time: —")
        self.lbl_ref_status     = QLabel("")
        self.lbl_diff_stats     = QLabel("")
        self.lbl_scan_progress  = QLabel("")
        self.lbl_meta_status    = QLabel("")
        for lbl in [self.lbl_index, self.lbl_date, self.lbl_selected_range, self.lbl_filename,
                    self.lbl_axis_time, self.lbl_prague_time, self.lbl_ref_status,
                    self.lbl_diff_stats, self.lbl_scan_progress, self.lbl_meta_status]:
            lbl.setWordWrap(True)
            lbl.setStyleSheet(info_style)
        # The frame counter must stay on ONE line. Its "(merged)" suffix comes and
        # goes while the user switches cameras, and a wrapped second line here
        # resized the INFO panel and shifted the whole left column. Fixed width so
        # the suffix cannot re-wrap the Range label next to it either.
        _idx_font = self.lbl_index.font()
        _idx_font.setPixelSize(11)
        self.lbl_index.setFont(_idx_font)
        self.lbl_index.setWordWrap(False)
        self.lbl_index.setFixedWidth(
            QFontMetrics(_idx_font).horizontalAdvance("99999 / 99999 (merged)") + 6)
        from PySide6.QtCore import Qt as _Qt2
        self.lbl_scan_progress.setTextFormat(_Qt2.TextFormat.RichText)
        # Warning style for per-frame notes in the INFO panel (auto-hides when empty)
        self.lbl_meta_status.setStyleSheet("font-size: 10px; color: #b36b00; padding: 1px 0;")

        # Auto-hide labels when their text is empty so the INFO panel has no blank lines.
        import types as _types
        def _auto_setText(lbl_self, text):
            QLabel.setText(lbl_self, text)
            lbl_self.setVisible(bool(text.strip()))
        for _lbl in (self.lbl_filename, self.lbl_ref_status, self.lbl_diff_stats,
                     self.lbl_scan_progress, self.lbl_meta_status):
            _lbl.setText = _types.MethodType(_auto_setText, _lbl)

        self.lbl_ref_status.setStyleSheet(_REF_STATUS_STYLE)
        self.lbl_diff_stats.setStyleSheet("font-size: 10px; color: #1b5e20; padding: 1px 0;")
        self.lbl_diff_stats.setToolTip(
            "Pixels whose difference from the reference is non-zero (after the diff\n"
            "threshold), their share of the frame, and the mean / min / max of those\n"
            "differences on the 0–255 display scale. Measured on the frame as shown,\n"
            "before the visibility offset is added.")
        self.lbl_selected_range.setStyleSheet("font-size: 11px; font-weight: 700; color: #333; padding: 1px 0;")
        # Date of the frame on screen. The on-image timestamp stays time-only, so this
        # is the only place the day is readable — it matters for multi-day selections.
        # Same bold style as Range, and never wrapping: the INFO panel must keep the
        # exact same number of lines whatever date is shown (a wrapped second line
        # would shift the whole left column, see the lbl_index note above).
        self.lbl_date.setStyleSheet("font-size: 11px; font-weight: 700; color: #333; padding: 1px 0;")
        self.lbl_date.setWordWrap(False)
        self.lbl_axis_time.setVisible(False)
        self.lbl_prague_time.setVisible(False)
        self.prog = QProgressBar(); self.prog.setVisible(False)
        self.prog.setRange(0, 0); self.prog.setTextVisible(False)
        self.btn_cancel_scan = QPushButton("Cancel scan"); self.btn_cancel_scan.setVisible(False)
        self.btn_cancel_scan.clicked.connect(self.cancel_scan)

        llay.addStretch(1)

        # ═══════════════════════ RIGHT PANEL ══════════════════════
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(4)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setEnabled(False)
        self.slider.setMinimum(0); self.slider.setMaximum(SLIDER_MAX)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)

        self.tickbar  = TickBar(self)
        self.tickbar.slider_handle_hw = 1  # per-cam slider handle = 2px → hw = 1

        rlay.addWidget(self.slider)

        # Per-camera sliders (hidden until multi-cam mode is active)
        self._per_cam_container = QWidget()
        _pcl = QVBoxLayout(self._per_cam_container)
        _pcl.setContentsMargins(0, 2, 0, 0)
        _pcl.setSpacing(1)
        self._per_cam_layout = _pcl
        self._per_cam_rows: list[_CamSliderRow] = []
        self._per_cam_master_idx: int = 0      # which camera is master
        self._per_cam_scrubbing_cam: int = -1  # which cam is being dragged (-1 = none)
        # Wrap in a scrollable area so many cameras don't squeeze the image area
        self._per_cam_scroll = QScrollArea()
        self._per_cam_scroll.setWidget(self._per_cam_container)
        self._per_cam_scroll.setWidgetResizable(True)
        self._per_cam_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._per_cam_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._per_cam_scroll.setMaximumHeight(200)
        self._per_cam_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._per_cam_scroll.setVisible(False)
        rlay.addWidget(self._per_cam_scroll)

        # Tickbar below sliders — cursor line ends here, at the bottom
        rlay.addWidget(self.tickbar)

        # Outer row container — dark background, holds camera + pointing panel
        _cam_row_widget = QWidget()
        _cam_row_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _cam_row_widget.setStyleSheet("background: #1a1a1a;")
        _cam_row_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._cam_row_widget = _cam_row_widget

        self._img_pointing_row = QHBoxLayout(_cam_row_widget)
        self._img_pointing_row.setSpacing(0)
        self._img_pointing_row.setContentsMargins(0, 0, 0, 0)

        # Single-cam: dark wrapper, img_view fills it.
        # Single-cam: image + label bar BELOW (not overlapping) the image.
        _single_wrapper = QWidget()
        _single_wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        _single_wrapper.setStyleSheet("background: #1a1a1a;")
        _single_wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        _swl = QVBoxLayout(_single_wrapper)
        _swl.setContentsMargins(0, 0, 0, 0)
        _swl.setSpacing(0)

        self.img_view = ImageView(_single_wrapper)
        self.img_view.bg_color = QColor("#1a1a1a")
        self.img_view.cam_label_font_px = self._cam_label_size_sb.value()
        self.img_view.sc_topn_marker_radius = self._sc_marker_r_sb.value()
        self.img_view.sc_topn_marker_thick  = self._sc_marker_thick_sb.value()
        # Labels drawn below image (reserved strip), not overlapping the image
        self.img_view.cam_label_use_overlay = False
        # Zooming needs a native-resolution render; unzooming can go back to
        # REFINE_MAX_SIDE. Either way the settled frame must be re-rendered.
        self.img_view.zoom_changed.connect(self._schedule_refine)
        _swl.addWidget(self.img_view, 1)

        # PV overlay — floating draggable panel over single-cam image
        self._pv_overlay = _PvOverlayPanel(_single_wrapper)
        self._pv_overlay.move(8, 8)   # default top-left position

        self._single_cam_container = _single_wrapper
        self._img_pointing_row.addWidget(_single_wrapper, 1)
        self._single_wrapper = _single_wrapper

        # Multi-cam grid (skrytý dokud není >1 kamera)
        self._multi_grid = MultiCameraGrid(self)
        self._multi_grid.setVisible(False)
        self._multi_grid.camera_selected.connect(self._on_multicam_selected)
        self._img_pointing_row.addWidget(self._multi_grid, 1)

        # PV overlay for multi-cam view — parented to _cam_row_widget so it floats
        # over the camera area. raise_() puts it above _multi_grid in z-order.
        self._pv_overlay_multi = _PvOverlayPanel(_cam_row_widget)
        self._pv_overlay_multi.move(8, 8)

        self.pointing_panel = PointingPanel(self)
        self.pointing_panel.point_clicked.connect(self._on_pointing_point_clicked)
        self.pointing_panel.region_deleted.connect(self._on_pointing_region_deleted)
        self.pointing_panel.setVisible(False)
        self._img_pointing_row.addWidget(self.pointing_panel, 1)
        rlay.addWidget(_cam_row_widget, 1)

        left_scroll.setWidget(left)

        # Info panel ukotvený nad scroll area
        info_panel = QWidget()
        info_panel.setFixedWidth(275)
        info_panel.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        ilay = QVBoxLayout(info_panel)
        ilay.setContentsMargins(0, 4, 0, 4)
        ilay.setSpacing(2)
        info_title_row = QHBoxLayout()
        info_title_row.addWidget(_group_label("Info"))
        info_title_row.addStretch(1)
        self._online_dot_top = QLabel("●")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot_top.setToolTip("Online mode indicator")
        info_title_row.addWidget(self._online_dot_top)
        ilay.addLayout(info_title_row)
        ilay.addWidget(self.lbl_date)
        _idx_range_row = QHBoxLayout()
        _idx_range_row.setSpacing(6)
        _idx_range_row.addWidget(self.lbl_index)
        _idx_range_row.addWidget(self.lbl_selected_range, 1)
        ilay.addLayout(_idx_range_row)
        for lbl in [self.lbl_filename, self.lbl_meta_status, self.lbl_ref_status,
                    self.lbl_diff_stats, self.lbl_scan_progress]:
            lbl.setVisible(bool(lbl.text()))
            ilay.addWidget(lbl)
        ilay.addWidget(self.prog)
        ilay.addWidget(self.btn_cancel_scan)
        ilay.addStretch(1)   # spare height collects here, labels stay top-aligned

        # The optional INFO rows (filename, warnings, scan progress, progress bar)
        # appear and disappear during normal use. Let the panel only ever GROW:
        # once a row has been seen the space stays reserved, so the SOURCE/SAVE/…
        # sections below never shift while the user is aiming at a button.
        from PySide6.QtCore import QEvent as _QEvent

        class _HeightRatchet(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == _QEvent.Type.LayoutRequest:
                    h = obj.sizeHint().height()
                    if h > obj.minimumHeight():
                        obj.setMinimumHeight(h)
                return False

        self._info_height_ratchet = _HeightRatchet(info_panel)
        info_panel.installEventFilter(self._info_height_ratchet)
        self._info_panel = info_panel

        left_col = QWidget()
        left_col.setFixedWidth(275)
        lcol_lay = QVBoxLayout(left_col)
        lcol_lay.setContentsMargins(0, 0, 0, 0)
        lcol_lay.setSpacing(0)
        lcol_lay.addWidget(info_panel)
        lcol_lay.addWidget(left_scroll, 1)
        self._left_col = left_col
        self._right_panel = right

        root.addWidget(left_col)
        root.addWidget(right, 1)

        self._watcher_mode = False
        self._focus_mode = False

        # QShortcut s ApplicationShortcut — funguje bez ohledu na to, který widget má focus
        from PySide6.QtGui import QShortcut, QKeySequence
        _sc_f11 = QShortcut(QKeySequence("F11"), self)
        _sc_f11.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _sc_f11.activated.connect(self._toggle_focus_mode)
        _sc_ctrl_f11 = QShortcut(QKeySequence("Ctrl+F11"), self)
        _sc_ctrl_f11.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _sc_ctrl_f11.activated.connect(self._toggle_watcher_mode)

        self.scrub_timer = QTimer(self)
        self.scrub_timer.setInterval(SCRUB_INTERVAL_MS)
        # Same as play_timer: a coarse 33 ms timer fires at 21 Hz on Windows, so the drag
        # could never paint more than 21 positions a second no matter how fast the frames
        # were available.
        self.scrub_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.scrub_timer.timeout.connect(self._apply_scrub)

        # Slider moves that never emit sliderPressed/sliderReleased — mouse wheel,
        # arrow / PageUp keys, a click on the groove — have no drag to hang a render
        # off, so _on_slider_changed arms this instead.
        self._keynav_debounce = QTimer(self)
        self._keynav_debounce.setSingleShot(True)
        self._keynav_debounce.timeout.connect(self._apply_keynav)

        # Per-camera navigation gets its own coalescing tick. _on_per_cam_value_changed
        # used to render EVERY camera synchronously on each QSlider.valueChanged — i.e.
        # once per mouse-move event, up to ~125/s — including a full stale-mark pass and a
        # diff-stats relayout per camera, so a drag spent the GUI thread on O(cameras^2)
        # label work and the tiles crawled. Now a move only RECORDS what it wants.
        #
        # Deliberately NOT the shared scrub_timer / _apply_scrub:
        #   - _apply_scrub drives off pending_slider, i.e. the slider that multi-cam HIDES;
        #   - its multi-cam branch calls _display_multicam_index, which snaps every camera
        #     to one merged-timeline moment, while _per_cam_sync_slaves deliberately leaves
        #     a slave alone when it has no frame within SLAVE_SYNC_MAX_NS;
        #   - independent mode (_per_cam_master_idx < 0) has no merged position at all;
        #   - and _apply_scrub coalesces on `idx == self.current_idx`, which
        #     _per_cam_display_one writes itself — so the second tick of any drag would
        #     return early.
        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(NAV_TICK_MS)
        # Same reason as play_timer / scrub_timer: a coarse 33 ms timer fires at 21 Hz on
        # Windows, capping the drag at 21 positions/s however fast the frames arrive.
        self._nav_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._nav_timer.timeout.connect(self._per_cam_nav_tick)
        self._nav_pending: dict = {}    # cam_idx → wanted ts_ns (already snapped)
        self._nav_frame: dict = {}      # cam_idx → wanted FRAME index (see _per_cam_step)
        self._nav_cursor_ts: int = 0    # last ts written to the tickbar / clocks

    def _apply_keynav(self):
        """Render the position a non-drag slider move landed on."""
        if not self.items or self.pending_slider is None: return
        if self._is_scrubbing or self._is_playing: return
        idx = self._time_to_nearest_index(self._slider_to_time_ns(self.pending_slider))
        if not (0 <= idx < len(self.items)): return
        if self._is_multi_cam():
            self._display_multicam_index(idx, update_slider=False)
        else:
            self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=False)
        self._schedule_refine()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_sc_preview_pixmap'):
            self._update_sc_preview()

    # ================================================================ OPEN
    def _set_busy(self, busy: bool):
        for btn in [self.btn_open, self.btn_date, self.btn_refresh,
                    self.btn_set_a, self.btn_set_b, self.btn_clear_marks,
                    self.btn_save, self.btn_save_range, self.btn_play,
                    self.btn_prev, self.btn_next, self.btn_pointing,
                    self.slider, self.gradient_cb, self.speed_cb,
                    self.cb_bright, self.cb_subtract]:
            btn.setEnabled(not busy)

    def _on_reset_zoom(self):
        if self._is_multi_cam():
            selected = self._multi_grid.selected_cam_indices()
            if not selected:
                from PySide6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    self, "Reset zoom",
                    "No cameras selected — reset zoom for ALL cameras?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                targets = self._multi_grid._cam_views
            else:
                targets = [self._multi_grid._cam_views[i]
                           for i in selected
                           if i < len(self._multi_grid._cam_views)]
            for cv in targets:
                cv.img_view.reset_zoom()
        else:
            self.img_view.reset_zoom()

    def _toggle_draw_mode(self, mode: str):
        iv = self._active_img_view()
        iv.set_draw_mode("" if iv._draw_mode == mode else mode)
        if iv._draw_mode == "cross"  and not self.cb_cross.isChecked():
            self.cb_cross.setChecked(True)
        if iv._draw_mode == "circle" and not self.cb_circle.isChecked():
            self.cb_circle.setChecked(True)
        if iv._draw_mode == "square" and not self.cb_square.isChecked():
            self.cb_square.setChecked(True)
        self._refresh_draw_btns()

    def _refresh_draw_btns(self):
        iv = self._active_img_view()
        m = iv._draw_mode
        on  = "QPushButton { background: #2d7dff; color: #fff; font-weight: 700; border-radius: 4px; }"
        self.btn_draw_cross.setStyleSheet (on if m == "cross"  else "")
        self.btn_draw_circle.setStyleSheet(on if m == "circle" else "")
        self.btn_draw_square.setStyleSheet(on if m == "square" else "")

    def _remove_all_overlays(self):
        def _clear_iv(iv):
            iv.show_cross  = False; iv.cross_pos_norm  = None
            iv.show_circle = False; iv.circle_center_norm = None
            iv.circle_r_norm = None; iv.circle_rx_norm = None; iv.circle_ry_norm = None
            iv.show_square = False; iv.square_rect_norm = None
            iv.set_draw_mode("")
            iv.update()

        if self._is_multi_cam():
            for idx in self._multi_grid.selected_cam_indices():
                iv = self._multi_grid.get_img_view(idx)
                if iv is not None:
                    _clear_iv(iv)
        else:
            _clear_iv(self.img_view)

        self.cb_cross.setChecked(False)
        self.cb_circle.setChecked(False)
        self.cb_square.setChecked(False)
        self._refresh_draw_btns()

    def _open_overlay_settings(self):
        from PySide6.QtWidgets import (QDialog, QFormLayout, QSpinBox,
                                        QPushButton, QDialogButtonBox, QColorDialog)
        from PySide6.QtGui import QColor

        dlg = QDialog(self)
        dlg.setWindowTitle("Overlay Settings")
        dlg.setMinimumWidth(320)
        lay = QFormLayout(dlg)

        def make_color_btn(color: QColor) -> QPushButton:
            btn = QPushButton()
            btn.setFixedWidth(60)
            btn._color = QColor(color)
            btn.setStyleSheet(f"background: {color.name()}; border: 1px solid #888;")
            def pick():
                c = QColorDialog.getColor(btn._color, dlg, "Pick color",
                                          QColorDialog.ColorDialogOption.ShowAlphaChannel)
                if c.isValid():
                    btn._color = c
                    btn.setStyleSheet(f"background: {c.name()}; border: 1px solid #888;")
            btn.clicked.connect(pick)
            return btn

        # Cross
        cross_color_btn = make_color_btn(self._overlay_cross_color)
        cross_thick_sb = QSpinBox(); cross_thick_sb.setRange(1, 20)
        cross_thick_sb.setValue(self._overlay_cross_thick)
        cross_size_sb = QSpinBox(); cross_size_sb.setRange(4, 200)
        cross_size_sb.setValue(self._overlay_cross_size)

        # Zjisti rozměry aktuálního obrázku pro info label
        _img_w = _img_h = None
        if self.img_view._pix and not self.img_view._pix.isNull():
            _img_w = self.img_view._pix.width()
            _img_h = self.img_view._pix.height()

        lay.addRow("Cross color:", cross_color_btn)
        lay.addRow("Cross thickness:", cross_thick_sb)
        lay.addRow("Cross size (px):", cross_size_sb)
        if _img_w and _img_h:
            _cross_info = QLabel(
                f"Image: {_img_w}×{_img_h} px  |  "
                f"thickness max ~{_img_w // 100} px  |  "
                f"size max ~{min(_img_w, _img_h) // 2} px"
            )
            _cross_info.setStyleSheet("font-size: 10px; color: #666;")
            lay.addRow("", _cross_info)

        # Circle
        circle_color_btn = make_color_btn(self._overlay_circle_color)
        circle_thick_sb = QSpinBox(); circle_thick_sb.setRange(1, 20)
        circle_thick_sb.setValue(self._overlay_circle_thick)
        lay.addRow("Circle color:", circle_color_btn)
        lay.addRow("Circle thickness:", circle_thick_sb)

        # Square
        square_color_btn = make_color_btn(self._overlay_square_color)
        square_thick_sb = QSpinBox(); square_thick_sb.setRange(1, 20)
        square_thick_sb.setValue(self._overlay_square_thick)
        lay.addRow("Square color:", square_color_btn)
        lay.addRow("Square thickness:", square_thick_sb)

        from PySide6.QtWidgets import QCheckBox as _QCB
        cb_all_cams = _QCB("Apply to all cameras")
        cb_all_cams.setChecked(False)
        lay.addRow("", cb_all_cams)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._overlay_cross_color  = cross_color_btn._color
            self._overlay_cross_thick  = cross_thick_sb.value()
            self._overlay_cross_size   = cross_size_sb.value()
            self._overlay_circle_color = circle_color_btn._color
            self._overlay_circle_thick = circle_thick_sb.value()
            self._overlay_square_color = square_color_btn._color
            self._overlay_square_thick = square_thick_sb.value()
            self._apply_overlay_settings(all_cams=cb_all_cams.isChecked())

    def _apply_overlay_settings(self, all_cams: bool = False):
        def _apply_to_iv(iv):
            iv.cross_size      = self._overlay_cross_size
            iv.cross_thickness = self._overlay_cross_thick
            iv.cross_color     = self._overlay_cross_color
            iv.circle_color    = self._overlay_circle_color
            iv.circle_thick    = self._overlay_circle_thick
            iv.square_color    = self._overlay_square_color
            iv.square_thick    = self._overlay_square_thick
            iv.update()

        _apply_to_iv(self.img_view)
        if self._is_multi_cam():
            if all_cams:
                for cv in self._multi_grid._cam_views:
                    _apply_to_iv(cv.img_view)
            else:
                sel_iv = self._multi_grid.selected_img_view()
                if sel_iv is not None:
                    _apply_to_iv(sel_iv)

    # ================================================================ PV VALUES
    def _open_pv_config(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Select PV Channels")
        dlg.setMinimumWidth(260)
        vlay = QVBoxLayout(dlg)
        vlay.addWidget(QLabel("Choose which PV channels to display:"))
        checks: dict[str, QCheckBox] = {}
        for name in PV_CHANNEL_MAP:
            cb = QCheckBox(name)
            cb.setChecked(name in self._pv_enabled)
            vlay.addWidget(cb)
            checks[name] = cb
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        vlay.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._pv_enabled = [n for n in PV_CHANNEL_MAP if checks[n].isChecked()]
            self._pv_values  = {}
            self._pv_values_ts = None
            self._pv_rebuild_table()
            self._pv_trigger_fetch()
            self._pv_update_overlay()

    def _pv_rebuild_table(self):
        has = bool(self._pv_enabled)
        self._pv_table.setVisible(has)
        self._pv_no_pv_lbl.setVisible(not has)
        if not has:
            return
        self._pv_table.setRowCount(len(self._pv_enabled))
        row_h = 20
        self._pv_table.setMaximumHeight(row_h * len(self._pv_enabled) + 26)
        pending = self._pv_is_pending()
        for i, name in enumerate(self._pv_enabled):
            name_item = QTableWidgetItem(name)
            val_str = self._pv_values.get(name, "…")
            units = PV_UNITS.get(name, "")
            if val_str not in ("…", "—", cpva.PV_TEXT_ERROR, cpva.PV_TEXT_NOT_FOUND) and units:
                val_str = f"{val_str} {units}"
            val_item = QTableWidgetItem(val_str)
            if pending:
                # These numbers were fetched for another frame — say so instead of
                # letting them read as this frame's values.
                val_item.setForeground(QColor("#888888"))
                val_item.setToolTip("Refreshing — value belongs to the previous frame")
            self._pv_table.setItem(i, 0, name_item)
            self._pv_table.setItem(i, 1, val_item)
            self._pv_table.setRowHeight(i, row_h)

    def _pv_trigger_fetch(self):
        """Throttled entry: coalesce rapid frame changes (live mode ~3 Hz,
        scrubbing) into one fetch. TRUE trailing-edge debounce — each call
        restarts the 400 ms timer so the value follows the frame the user
        STOPPED on — with a 0.7 s max-wait so continuous playback (which never
        stops re-triggering) still refreshes regularly."""
        if not self._pv_enabled:
            return
        t = getattr(self, "_pv_debounce_timer", None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(400)
            t.timeout.connect(self._pv_trigger_fetch_now)
            self._pv_debounce_timer = t
        first = getattr(self, "_pv_debounce_first_ts", None)
        now = time.monotonic()
        if first is None:
            self._pv_debounce_first_ts = now
        elif now - first > 0.7:
            # Max-wait hit — fire immediately instead of postponing forever
            t.stop()
            self._pv_trigger_fetch_now()
            return
        t.start()   # restart → trailing edge

    def _pv_current_ts(self) -> "int | None":
        """Timestamp of the frame the PV panel is supposed to describe: the master
        camera in multi-cam mode, otherwise the current single-cam frame."""
        if self._is_multi_cam():
            master = self._per_cam_master_idx
            if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                # latest displayed index for master cam
                cam_items = self._cam_items[master]
                cam_idx = 0
                if hasattr(self, '_cam_current_idx') and master < len(self._cam_current_idx):
                    cam_idx = min(self._cam_current_idx[master], len(cam_items) - 1)
                if cam_items:
                    return cam_items[cam_idx].ts_ns
            elif self._cam_items:
                # No master — use first cam
                for ci, cam_items in enumerate(self._cam_items):
                    if cam_items:
                        cam_idx = 0
                        if hasattr(self, '_cam_current_idx') and ci < len(self._cam_current_idx):
                            cam_idx = min(self._cam_current_idx[ci], len(cam_items) - 1)
                        return cam_items[cam_idx].ts_ns
            return None
        if self.current_idx is not None and self.items:
            return self.items[self.current_idx].ts_ns
        return None

    def _pv_is_pending(self) -> bool:
        """True when the values on display were fetched for a DIFFERENT frame than
        the one now shown (a fetch is in flight, or was never started for it)."""
        if not self._pv_values:
            return False
        cur = self._pv_current_ts()
        return cur is not None and self._pv_values_ts != cur

    def _pv_trigger_fetch_now(self):
        """Start a background fetch for the current displayed timestamp.

        Single-flight: at most one fetch runs; triggers while one is in flight
        set a dirty flag and _pv_on_result re-triggers with the NEWEST frame's
        timestamp. Unlike the old generation-token drop, a completed fetch is
        always applied — the overlay can lag briefly but never freezes stale."""
        self._pv_debounce_first_ts = None
        if not self._pv_enabled:
            return
        if getattr(self, "_pv_fetch_inflight", False):
            self._pv_fetch_dirty = True
            return
        ts_ns = self._pv_current_ts()

        if ts_ns is None:
            return

        self._pv_fetch_ts = ts_ns
        self._pv_fetch_inflight = True
        self._pv_fetch_gen += 1
        gen = self._pv_fetch_gen
        names  = list(self._pv_enabled)
        # Mark as loading only on first fetch (no known value yet); keep last known value during refresh
        changed = False
        for name in names:
            if name not in self._pv_values:
                self._pv_values[name] = "…"
                changed = True
        if changed:
            self._pv_rebuild_table()

        def _fetch_one(name):
            channel = PV_CHANNEL_MAP.get(name)
            if not channel:
                return name, cpva.PV_TEXT_NOT_FOUND
            val, status = _pv_last_known_ex(channel, ts_ns)
            if val is None:
                # "ERR" = fetch failed (not cached → next trigger retries);
                # "n/a" = genuinely no sample near this timestamp.
                return name, (cpva.PV_TEXT_ERROR if status == "error"
                              else cpva.PV_TEXT_NOT_FOUND)
            val *= PV_SCALE.get(name, 1.0)
            return name, _pv_decorate(_format_pv_value(channel, val), status)

        def _fetch():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results: dict[str, str] = {}
            try:
                with ThreadPoolExecutor(max_workers=min(len(names), 8)) as ex:
                    futs = {ex.submit(_fetch_one, n): n for n in names}
                    for fut in as_completed(futs):
                        try:
                            name, val = fut.result()
                            results[name] = val
                        except Exception:
                            results[futs[fut]] = cpva.PV_TEXT_ERROR
            finally:
                # ALWAYS emit — _pv_on_result must clear the in-flight flag,
                # otherwise one crashed fetch would freeze the overlay forever.
                self._pv_signals.result.emit(gen, results)

        threading.Thread(target=_fetch, daemon=True).start()

    def _pv_force_refresh(self):
        """Clear PV day-cache for current frame's date and re-fetch."""
        ts_ns = self._pv_current_ts()
        if ts_ns is None and self._is_multi_cam() and self._cam_items:
            for cam_items in self._cam_items:
                if cam_items:
                    ts_ns = cam_items[0].ts_ns
                    break
        if ts_ns is not None:
            date_key = _pv_date_key(ts_ns)
            cpva.invalidate(date_key=date_key)
            cpva.invalidate(date_key=_pv_prev_date_key(date_key))
            cpva.invalidate(date_key=cpva.next_date_key(date_key))
            # Also drop the day-boundary look-back anchors, or a step PV keeps
            # reporting the value cached before the refresh.
            cpva.invalidate_lookback()
        self._pv_trigger_fetch()

    def _pv_on_result(self, gen: int, results: dict):
        # Always apply the completed fetch (it is the newest finished one —
        # single-flight guarantees no older fetch can still be running), then
        # re-trigger if frames changed while it ran.
        self._pv_fetch_inflight = False
        if results:
            # Remember WHICH frame these numbers describe, so the panel can admit
            # it when the displayed frame has moved on since.
            self._pv_values_ts = getattr(self, "_pv_fetch_ts", None)
            self._pv_values.update(results)
            self._pv_rebuild_table()
            self._pv_update_overlay()
        if getattr(self, "_pv_fetch_dirty", False):
            self._pv_fetch_dirty = False
            self._pv_trigger_fetch_now()

    def _pv_update_overlay(self):
        """Refresh the floating PV overlay panel."""
        overlay = getattr(self, "_pv_overlay", None)
        overlay_multi = getattr(self, "_pv_overlay_multi", None)
        is_multi = self._is_multi_cam()

        if not self._pv_enabled:
            if overlay is not None:
                overlay.setVisible(False)
            if overlay_multi is not None:
                overlay_multi.setVisible(False)
            return

        rows = []
        pending = self._pv_is_pending()
        for name in self._pv_enabled:
            val = self._pv_values.get(name, "…")
            units = PV_UNITS.get(name, "")
            if val not in ("…", "—", "⟳", cpva.PV_TEXT_ERROR, cpva.PV_TEXT_NOT_FOUND) and units:
                val = f"{val} {units}"
            if pending:
                # Burned-in-looking panel: never let another frame's number sit
                # there unmarked while the fetch for this frame is still running.
                val = f"⟳ {val}"
            rows.append((name, val))

        if is_multi:
            if overlay is not None:
                overlay.setVisible(False)
            if overlay_multi is not None:
                overlay_multi.update_values(rows)
                overlay_multi.ensure_inside_parent()
                overlay_multi.raise_()
                overlay_multi.setVisible(True)
        else:
            if overlay_multi is not None:
                overlay_multi.setVisible(False)
            if overlay is not None:
                overlay.update_values(rows)
                overlay.ensure_inside_parent()
                overlay.setVisible(True)

    def _open_pv_overlay_settings(self):
        overlay = getattr(self, "_pv_overlay", None)
        if overlay is None:
            return
        orig_fs = overlay.font_size_px
        orig_ff = overlay.font_family
        orig_op = overlay.bg_opacity
        orig_fc = QColor(overlay.font_color)
        orig_bc = QColor(overlay.bg_color)

        dlg = QDialog(self)
        dlg.setWindowTitle("PV Overlay Settings")
        dlg.setFixedWidth(340)
        lay = QFormLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        font_sb = QSpinBox(); font_sb.setRange(6, 72); font_sb.setValue(orig_fs)
        font_cb = QComboBox()
        font_cb.addItems(["Consolas", "Arial", "Segoe UI", "Courier New",
                          "Calibri", "Verdana", "Tahoma", "Times New Roman"])
        idx_f = font_cb.findText(orig_ff)
        if idx_f >= 0: font_cb.setCurrentIndex(idx_f)

        opacity_sl = QSlider(Qt.Orientation.Horizontal)
        opacity_sl.setRange(0, 100); opacity_sl.setValue(orig_op)
        opacity_lbl = QLabel(f"{orig_op}%")
        opacity_row = QHBoxLayout(); opacity_row.addWidget(opacity_sl); opacity_row.addWidget(opacity_lbl)

        _font_color = [QColor(orig_fc)]
        fc_btn = QPushButton(); fc_btn.setFixedWidth(60)
        fc_btn.setStyleSheet(f"background: {_font_color[0].name()};")

        _bg_color = [QColor(orig_bc)]
        bc_btn = QPushButton(); bc_btn.setFixedWidth(60)
        bc_btn.setStyleSheet(f"background: {_bg_color[0].name()};")

        from PySide6.QtWidgets import QColorDialog as _QCD
        def _pick_font_color():
            c = _QCD.getColor(_font_color[0], dlg, "Font color")
            if c.isValid():
                _font_color[0] = c
                fc_btn.setStyleSheet(f"background: {c.name()};")
                _preview()
        fc_btn.clicked.connect(_pick_font_color)

        def _pick_bg_color():
            c = _QCD.getColor(_bg_color[0], dlg, "Background color")
            if c.isValid():
                _bg_color[0] = c
                bc_btn.setStyleSheet(f"background: {c.name()};")
                _preview()
        bc_btn.clicked.connect(_pick_bg_color)

        opacity_sl.valueChanged.connect(lambda v: (opacity_lbl.setText(f"{v}%"), _preview()))
        font_sb.valueChanged.connect(lambda _: _preview())
        font_cb.currentTextChanged.connect(lambda _: _preview())

        def _preview():
            overlay2 = getattr(self, "_pv_overlay_multi", None)
            for ov in [overlay, overlay2]:
                if ov is not None:
                    ov.apply_settings(font_sb.value(), font_cb.currentText(),
                                      opacity_sl.value(), _font_color[0], _bg_color[0])

        lay.addRow("Font size (px):", font_sb)
        lay.addRow("Font:", font_cb)
        lay.addRow("Background opacity:", opacity_row)
        lay.addRow("Font color:", fc_btn)
        lay.addRow("Background color:", bc_btn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addRow(btns)

        if dlg.exec() == QDialog.DialogCode.Rejected:
            overlay2 = getattr(self, "_pv_overlay_multi", None)
            for ov in [overlay, overlay2]:
                if ov is not None:
                    ov.apply_settings(orig_fs, orig_ff, orig_op, orig_fc, orig_bc)

    def _pv_text(self) -> str:
        """Return a formatted single-line PV string for burn-in under saved images.
        The on-screen PV overlay is a view-only annotation — PV values are burned
        into saved files only when 'Save with overlay' is enabled."""
        if not self.cb_save_overlay.isChecked():
            return ""
        if not self._pv_enabled or not self._pv_values:
            return ""
        parts = []
        for name in self._pv_enabled:
            val = self._pv_values.get(name, "")
            if not val or val in ("…", "—"):
                continue
            units = PV_UNITS.get(name, "")
            parts.append(f"{name}: {val} {units}".strip())
        return "  |  ".join(parts)

    # ================================================================ MULTI-CAM HELPERS
    def _is_multi_cam(self) -> bool:
        return len(self._cam_names) > 1

    def _cam_enhance_on(self, cam_i: int) -> bool:
        """Auto-stretch / brightness / contrast apply to ALL cameras (global).

        They are driven by their own controls (checkboxes / sliders) and take
        effect when those controls change — the same enhancement on every camera.
        Camera SELECTION deliberately no longer scopes or re-applies enhancement:
        tying it to the selected camera meant a plain click silently rebuilt every
        cache and re-rendered all panels, which both surprised the user ("clicking
        a camera applies the changes") and thrashed the decode pipeline."""
        return True

    def _cam_set_shown_key(self, cam_i: int, key):
        """Record the render the tile cam_i must end up showing (see _cam_shown_key).
        Written by every display path so _on_cam_loaded can reject a render whose
        params the user has already changed."""
        if not hasattr(self, "_cam_shown_key"):
            self._cam_shown_key = []
        while len(self._cam_shown_key) <= cam_i:
            self._cam_shown_key.append(None)
        self._cam_shown_key[cam_i] = key

    def _redraw_cam_in_place(self, cam_i: int) -> bool:
        """Re-render one camera at the frame it is currently showing. Returns False
        if that camera has no frames yet."""
        cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
        if not cam_ts:
            return False
        cur = self._cam_current_idx[cam_i] if cam_i < len(self._cam_current_idx) else 0
        cur = max(0, min(cur, len(cam_ts) - 1))
        self._per_cam_display_one(cam_i, cam_ts[cur])
        return True

    def _redraw_all_cams_in_place(self):
        """Re-render every camera at the frame it is CURRENTLY showing (its own per-cam
        slider position), WITHOUT re-syncing them to the shared timeline. Used when a
        display setting changes (auto-stretch, brightness, gradient) so the shown frames
        don't jump — unlike _display_multicam_index, which snaps all cams to one time."""
        if not self._is_multi_cam() or not self._cam_items:
            return
        for cam_i in range(len(self._cam_items)):
            self._redraw_cam_in_place(cam_i)

    def _on_multicam_selected(self, idx: int):
        """Kamera v gridu byla vybrána kliknutím."""
        if self._focus_mode:
            return   # v focus modu není výběr kamery potřeba
        # Clear draw mode on ALL cameras except the newly selected one,
        # so draw mode never silently stays active on a background camera.
        for i, cv in enumerate(self._multi_grid._cam_views):
            if i != idx:
                cv.img_view.set_draw_mode("")

        # Update SC camera selector and clear stale preview from previous camera
        cam_name = self._cam_names[idx] if idx < len(self._cam_names) else ""
        if hasattr(self, '_sc_cam_lbl'):
            self._sc_cam_lbl.setText(f"Camera: {_strip_cam_name(cam_name)}" if cam_name else "")
        if hasattr(self, '_sc_preview_lbl'):
            self._sc_preview_lbl.hide()
            self._sc_preview_pixmap = None
        if hasattr(self, '_sc_exclusion_mask'):
            self._sc_exclusion_mask = None
            self._sc_exclusion_path = None

        if idx < len(self._cam_ref_images) and self._cam_ref_images[idx] is not None:
            cam_name = self._cam_names[idx] if idx < len(self._cam_names) else f"cam {idx}"
            self._set_ref_status(f"Ref set: {_strip_cam_name(cam_name)}")
        else:
            # Clearing the line must not swallow the "subtraction has no reference"
            # warning — clicking a camera would otherwise hide it again.
            self._refresh_ref_warning()
        # Update draw mode checkboxes/buttons to reflect selected camera's state
        iv = self._multi_grid.selected_img_view()
        if iv is not None:
            self.cb_cross.blockSignals(True)
            self.cb_circle.blockSignals(True)
            self.cb_square.blockSignals(True)
            self.cb_cross.setChecked(iv.show_cross)
            self.cb_circle.setChecked(iv.show_circle)
            self.cb_square.setChecked(iv.show_square)
            self.cb_cross.blockSignals(False)
            self.cb_circle.blockSignals(False)
            self.cb_square.blockSignals(False)
            self._refresh_draw_btns()

        # Enhancement (auto-stretch / brightness / contrast) is global now
        # (see _cam_enhance_on), so selecting a camera must NOT re-render or change
        # any image — it only updates draw-mode / reference status above. This is
        # what the user asked for: settings change only when a control is clicked,
        # never as a side effect of picking a camera.

        if (hasattr(self, '_sc_val_sc') and self._sc_val_sc.text() not in ("—", "")
                and hasattr(self, '_run_spatial_contrast')):
            self._run_spatial_contrast()

    def _on_cam_label_size_changed(self, px: int):
        self._multi_grid.set_label_font_size(px)
        self.img_view.cam_label_font_px = px
        self.img_view._scaled = None  # force re-scale with new label bar height
        self.img_view.update()

    def _switch_to_multi_view(self):
        self._single_wrapper.setVisible(False)
        self.pointing_panel.setVisible(False)
        self._img_pointing_row.setStretch(self._img_pointing_row.indexOf(self._multi_grid), 1)
        self._multi_grid.setVisible(True)
        QTimer.singleShot(0, self._pv_update_overlay)

    def _switch_to_single_view(self):
        self._single_wrapper.setVisible(True)
        self._multi_grid.setVisible(False)
        if hasattr(self, '_sc_cam_row_widget'):
            self._sc_cam_row_widget.setVisible(False)
        self._per_cam_scroll.setVisible(False)
        self.slider.setVisible(True)
        self.tickbar.setVisible(True)
        self.tickbar.set_cursor(None)
        self.tickbar.set_left_offset(0)

    # ── Per-camera sliders ────────────────────────────────────────────────────

    def _build_per_cam_sliders(self, cam_names: list[str]):
        """(Re)vytvoří řady per-camera sliderů. Voláno při každém setup_multi_cam."""
        # The pending navigation is keyed by CAMERA INDEX, and the rows it would index are
        # about to be destroyed — a leftover entry from a larger camera set would index
        # _per_cam_rows out of range on the next tick.
        self._nav_timer.stop()
        self._nav_pending.clear()
        self._nav_frame.clear()
        # Odstraň staré řady
        for row in self._per_cam_rows:
            row.setParent(None)
        self._per_cam_rows.clear()

        for i, name in enumerate(cam_names):
            row = _CamSliderRow(i, name, self._per_cam_container)
            row.master_chosen.connect(self._on_per_cam_master_chosen)
            row.master_deselected.connect(self._on_per_cam_master_deselected)
            row.value_changed.connect(self._on_per_cam_value_changed)
            row.pressed.connect(self._on_per_cam_pressed)
            row.released.connect(self._on_per_cam_released)
            self._per_cam_layout.addWidget(row)
            self._per_cam_rows.append(row)

        # První kamera je defaultní master
        self._per_cam_master_idx = 0
        for i, row in enumerate(self._per_cam_rows):
            row.set_master(i == 0)
            row.set_enabled(True)

        self._per_cam_scroll.setVisible(True)
        # Size the scroll area to exactly fit the current number of rows (capped),
        # so it shrinks back when the camera count decreases.
        self._resize_per_cam_scroll()

        # After layout is computed, align tickbar axis with slider track start
        def _update_tickbar_offset():
            if self._per_cam_rows:
                row0 = self._per_cam_rows[0]
                sl = row0._slider
                # Use QStyle to find where value=0 sits in pixel coords (accounts for handle size)
                from PySide6.QtWidgets import QStyleOptionSlider
                opt = QStyleOptionSlider()
                sl.initStyleOption(opt)
                track_px = sl.style().sliderPositionFromValue(
                    sl.minimum(), sl.maximum(), 0, sl.width() - 1, False)
                # Map that pixel position to tickbar coords
                pt = row0.mapToGlobal(sl.pos())
                pt.setX(pt.x() + track_px)
                offset = max(0, self.tickbar.mapFromGlobal(pt).x())
                self.tickbar.set_left_offset(offset)
        QTimer.singleShot(0, _update_tickbar_offset)
        QTimer.singleShot(0, self._resize_per_cam_scroll)

    # Show at most this many camera-slider rows at once; with more cameras
    # selected, the rest scroll into view via the scroll area's side scrollbar.
    MAX_VISIBLE_CAM_ROWS = 4

    def _resize_per_cam_scroll(self):
        """Fix the per-camera slider area height to the current row count
        (capped at MAX_VISIBLE_CAM_ROWS), so it grows AND shrinks as the
        number of cameras changes, and never shows more than the cap."""
        n = len(self._per_cam_rows)
        if n == 0:
            self._per_cam_scroll.setVisible(False)
            return
        row_h = self._per_cam_rows[0].sizeHint().height()
        spacing = self._per_cam_layout.spacing()
        m = self._per_cam_layout.contentsMargins()
        visible_rows = min(n, self.MAX_VISIBLE_CAM_ROWS)
        total = n * row_h + (n - 1) * spacing + m.top() + m.bottom()
        max_h = visible_rows * row_h + max(0, visible_rows - 1) * spacing + m.top() + m.bottom()
        h = min(max_h, max(0, total))
        self._per_cam_scroll.setMinimumHeight(h)
        self._per_cam_scroll.setMaximumHeight(h)

    def _on_per_cam_master_chosen(self, cam_idx: int):
        """Uživatel klikl na radio tlačítko — přepne master na tuto kameru."""
        self._per_cam_master_idx = cam_idx
        for i, row in enumerate(self._per_cam_rows):
            row.set_master(i == cam_idx)
        # Sync slaves from new master's current position
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if cam_ts and cam_idx < len(self._per_cam_rows):
            row = self._per_cam_rows[cam_idx]
            t_raw = self._per_cam_slider_to_ts(cam_idx, row.value())
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_raw) - 1)
            t_ns = cam_ts[frame_idx]
            self._per_cam_sync_slaves(cam_idx, t_ns)

    def _on_per_cam_master_deselected(self, cam_idx: int):
        """User unchecked the master radio — switch to independent mode (no master)."""
        self._per_cam_master_idx = -1
        for row in self._per_cam_rows:
            row.set_master(False)

    def _on_per_cam_pressed(self, cam_idx: int):
        self._per_cam_scrubbing_cam = cam_idx
        # A per-camera drag IS a scrub, and nothing said so. _is_scrubbing was written only
        # by the SHARED slider's handlers — the slider multi-cam hides — so on the path the
        # user actually drags, _cam_tile_side never applied its 0.7x / 0.45x motion
        # downscale, _current_decode_side never saw the drag, and _last_motion_ips stayed
        # 0.0 for the whole gesture. Every "the user is moving" optimisation was dead.
        self._is_scrubbing = True
        self._reset_motion_tracking()
        self._drag_side = self._scrub_side
        # Same as the shared slider: get the per-camera subtraction references off the
        # share now, so the first move of this slider doesn't block the GUI thread on one.
        self._prewarm_drag_references()
        if self._is_playing:
            self.stop()
        # Grabbing a slider means the user wants to inspect a moment manually, so
        # leave live mode: stop auto-follow/online so incoming frames don't fight
        # the manual scrub. For the master slider the existing value_changed /
        # released handlers then bind all cameras to the master's time. The user
        # re-enables Auto-follow when ready to watch live again.
        if self._online_mode:
            self._stop_online_mode()
            self._restore_full_history()
            if not self._live_trimmed:
                self._proxy_kick()
        # Render the frame the drag starts on through the same tick as the rest.
        if cam_idx < len(self._per_cam_rows):
            self._nav_request(cam_idx, self._per_cam_slider_to_ts(
                cam_idx, self._per_cam_rows[cam_idx].value()))

    def _on_per_cam_released(self, cam_idx: int):
        # Drop the drag's queued positions: they are frames already scrolled past, and
        # rendering them delays the one the user landed on.
        self._nav_timer.stop()
        self._nav_pending.clear()
        self._per_cam_scrubbing_cam = -1
        self._is_scrubbing = False
        # Back to full tile quality for the settle render (see _cam_tile_side).
        self._reset_motion_tracking()
        # The drag ran the sweep at PROXY_DRAG_WORKERS — let it back up to full speed after
        # a short grace. Before the early return below: that is the one path where the flag
        # was just cleared and nothing else would ever resume the sweep.
        self._proxy_idle_grace()
        if not self._cam_items or cam_idx >= len(self._cam_items):
            return
        row = self._per_cam_rows[cam_idx]
        t_ns = self._per_cam_slider_to_ts(cam_idx, row.value())
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if cam_ts:
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
            t_ns = cam_ts[frame_idx]
            snapped = self._per_cam_ts_to_slider(cam_idx, t_ns)
            row.set_value(snapped)
        # Release the drag's leftover coalescing state so the settle render always
        # relaunches, exactly as _on_slider_released does for the shared slider.
        self._reset_cam_pipeline()
        self._per_cam_display_one(cam_idx, t_ns)
        if self._per_cam_master_idx >= 0 and cam_idx == self._per_cam_master_idx:
            self._per_cam_sync_slaves(cam_idx, t_ns)
        if cam_idx == self._per_cam_master_idx or self._per_cam_master_idx < 0:
            self._pv_trigger_fetch()
        # …and bring every tile up to full tile resolution now that the user has stopped.
        self._schedule_refine()

    def _on_per_cam_value_changed(self, cam_idx: int, v: int):
        """Every move of a per-camera slider. Records the wanted moment and moves the axis
        cursor / clocks; the PICTURE is rendered by _per_cam_nav_tick.

        This used to render all N cameras synchronously, once per mouse-move event, on the
        GUI thread — including an O(cameras^2) stale-mark pass and a diff-stats relayout
        per camera. The cursor and the two clocks stay here on purpose: they belong to the
        slider handle, not to the render pipeline (same split as _on_slider_changed)."""
        if not self._cam_items or cam_idx >= len(self._cam_items):
            return
        t_ns = self._per_cam_slider_to_ts(cam_idx, v)
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        frame_idx = None
        if cam_ts:
            frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
            t_ns = cam_ts[frame_idx]
            snapped = self._per_cam_ts_to_slider(cam_idx, t_ns)
            if snapped != v and cam_idx < len(self._per_cam_rows):
                # set_value blocks signals, so this cannot re-enter here.
                self._per_cam_rows[cam_idx].set_value(snapped)
        if self._per_cam_is_info_cam(cam_idx):
            self._per_cam_write_clocks(t_ns)
        self._nav_request(cam_idx, t_ns, frame_idx)

    def _per_cam_is_info_cam(self, cam_idx: int) -> bool:
        """Whether this camera owns the shared info panel / axis cursor: the master, or
        in independent mode whichever camera is being dragged."""
        return (cam_idx == self._per_cam_master_idx) or (
            self._per_cam_master_idx < 0 and cam_idx == self._per_cam_scrubbing_cam)

    def _per_cam_write_clocks(self, real_ts: int):
        """Axis cursor + the two clock labels. Called at mouse rate — they belong to the
        handle — but guarded against repeats: TickBar.set_cursor calls update()
        unconditionally and each setText relayouts the info panel."""
        if real_ts == self._nav_cursor_ts:
            return
        self._nav_cursor_ts = real_ts
        self.lbl_prague_time.setText(f"Prague: {fmt_prague_full_from_ns(real_ts)}")
        self.lbl_axis_time.setText(f"Axis: {fmt_hhmmss_ms_from_ns(real_ts)}")
        self.tickbar.set_cursor(real_ts)

    def _nav_request(self, cam_idx: int, t_ns: int, frame_idx: "int | None" = None):
        """Ask for a frame on ONE camera. Every per-camera navigation — master slider,
        slave sync, the arrows, playback — comes through here, and _per_cam_nav_tick
        renders at most once per NAV_TICK_MS however fast the requests arrive."""
        self._nav_pending[cam_idx] = t_ns
        if frame_idx is None:
            frame_idx = self._per_cam_ts_to_frame(cam_idx, t_ns)
        # Kept up to date SYNCHRONOUSLY even though the render is deferred: _per_cam_step
        # steps relative to this, and _cam_current_idx is only written when the tick
        # actually runs — so two arrow presses inside one tick would otherwise both read
        # the same origin and the second keypress would be lost.
        self._nav_frame[cam_idx] = frame_idx
        if not self._nav_timer.isActive():
            self._nav_timer.start()

    def _per_cam_nav_tick(self):
        """Render whatever the per-camera sliders asked for since the last tick — once per
        camera, once per tick, with the label/stat passes hoisted out of the per-camera
        loop (they used to run once per camera per mouse-move event)."""
        if not self._is_multi_cam() or not self._per_cam_rows or not self._nav_pending:
            self._nav_timer.stop()
            self._nav_pending.clear()
            return
        pending = self._nav_pending
        self._nav_pending = {}
        n = len(self._cam_items)
        master = self._per_cam_master_idx

        if master >= 0 and master in pending:
            t_ns = pending.pop(master)
            # ONE motion sample per tick, in master frames per second. This is what
            # _cam_tile_side and _adaptive_stride read, and the per-camera path never fed
            # it before — so every drag looked stationary to them.
            self._update_motion_speed(self._nav_frame.get(master, 0))
            self._per_cam_display_one(master, t_ns, defer_labels=True)
            self._per_cam_sync_shared_widgets(master, t_ns)
            # Slaves resolved ONCE per tick instead of once per mouse-move event.
            for i, ts in self._per_cam_slave_targets(master, t_ns):
                if i in pending or not (0 <= i < n):
                    continue      # a slave the user is dragging himself wins
                self._per_cam_rows[i].set_value(self._per_cam_ts_to_slider(i, ts))
                self._nav_frame[i] = self._per_cam_ts_to_frame(i, ts)
                self._per_cam_display_one(i, ts, defer_labels=True)

        for i, t_ns in pending.items():
            if 0 <= i < n:
                self._per_cam_display_one(i, t_ns, defer_labels=True)

        # Once per TICK, not once per camera per mouse-move.
        self._flush_cam_diff_stats()
        self._cam_refresh_stale_marks()
        # Self-stopping, which is also what makes this cover wheel / arrow / groove moves
        # on a per-camera row: those emit valueChanged with no sliderPressed, so nothing
        # would ever stop a timer started on their behalf. One tick later it renders once
        # and stops — no separate keynav debounce needed.
        if not self._nav_pending and not self._is_playing \
                and self._per_cam_scrubbing_cam < 0:
            self._nav_timer.stop()

    def _per_cam_ts_to_frame(self, cam_idx: int, ts_ns: int) -> int:
        """Vrátí index nejbližšího framu (v minulosti nebo přesně) pro daný timestamp."""
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if not cam_ts:
            return 0
        return max(0, bisect.bisect_right(cam_ts, ts_ns) - 1)

    def _per_cam_frame_to_slider(self, cam_idx: int, frame_idx: int) -> int:
        """Převede index snímku na slider hodnotu na společné časové ose."""
        cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
        if not cam_ts or frame_idx >= len(cam_ts):
            return 0
        return self._per_cam_ts_to_slider(cam_idx, cam_ts[frame_idx])

    def _per_cam_slider_to_ts(self, cam_idx: int, v: int) -> int:
        """Převede hodnotu slideru (na společné časové ose) na timestamp v ns."""
        ax_min = self.axis_min_ns
        ax_max = self.axis_max_ns
        if ax_max <= ax_min:
            # Fallback: použij rozsah dané kamery
            cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
            if not cam_ts:
                return 0
            ax_min, ax_max = cam_ts[0], cam_ts[-1]
            if ax_max <= ax_min:
                return ax_min
        return int(ax_min + v / SLIDER_MAX * (ax_max - ax_min))

    def _per_cam_ts_to_slider(self, cam_idx: int, ts_ns: int) -> int:
        """Převede timestamp na hodnotu slideru (na společné časové ose)."""
        ax_min = self.axis_min_ns
        ax_max = self.axis_max_ns
        if ax_max <= ax_min:
            cam_ts = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
            if not cam_ts:
                return 0
            ax_min, ax_max = cam_ts[0], cam_ts[-1]
            if ax_max <= ax_min:
                return 0
        frac = (ts_ns - ax_min) / (ax_max - ax_min)
        return max(0, min(SLIDER_MAX, int(frac * SLIDER_MAX)))

    def _per_cam_sync_shared_widgets(self, cam_idx: int, real_ts: int):
        """The shared-timeline bookkeeping that follows the info camera: the preview
        sweep's focus point, the merged position, and the merged index label.

        Once per navigation TICK, not once per mouse-move: _time_to_nearest_index is a
        binary search over the whole merged list and the setText relayouts the info panel,
        and nothing here needs mouse resolution.

        Why it exists at all: per-camera scrubbing never reaches _set_info_for, the only
        other writer. Without it the sweep's focus point stays wherever the SHARED timeline
        was last left — during per-camera playback, frozen at the moment Play was pressed
        — and current_idx likewise, so every handler that re-renders via
        _display_multicam_index(self.current_idx) (Subtraction, Refresh) snapped all
        cameras to an unrelated time while the per-camera sliders stayed put, subtracting
        the reference from a completely different frame than the slider showed. Live
        advance still overrides this right after (_live_advance_cam), so auto-follow is
        unchanged. Only the info camera writes it — the slaves would scatter it."""
        self._proxy_cursor_ts_ns = real_ts
        if self.items and self.ts_list:
            shared_idx = max(0, min(self._time_to_nearest_index(real_ts),
                                    len(self.items) - 1))
            self.current_idx  = shared_idx
            self.target_idx   = shared_idx
            self.play_time_ns = self.items[shared_idx].ts_ns
            self.lbl_index.setText(f"{shared_idx+1} / {len(self.items)} (merged)")
        # Per-camera navigation never reaches _set_info_for (see above), so the INFO
        # date would otherwise stay frozen at whatever the shared timeline last showed.
        self.lbl_date.setText(f"Date: {fmt_prague_date_from_ns(real_ts)}")
        self._pv_trigger_fetch()

    def _per_cam_display_one(self, cam_idx: int, t_ns: int, defer_labels: bool = False):
        """Zobrazí frame pro jednu kameru na daném čase.

        `defer_labels` is set by _per_cam_nav_tick, which flushes the diff-stats label and
        re-colours the timestamps ONCE for the whole pass. Doing it per camera meant
        O(cameras^2) label work per navigation step."""
        cam_items = self._cam_items[cam_idx] if cam_idx < len(self._cam_items) else []
        cam_ts    = self._cam_ts[cam_idx]    if cam_idx < len(self._cam_ts)    else []
        if not cam_items:
            return
        pos     = bisect.bisect_right(cam_ts, t_ns) - 1
        cam_idx_f = max(0, min(len(cam_items) - 1, pos))
        # Update info panel and tickbar cursor for master camera (or active scrub cam in independent mode)
        _is_info_cam = self._per_cam_is_info_cam(cam_idx)
        if _is_info_cam:
            real_ts = cam_ts[cam_idx_f] if cam_ts else t_ns
            self._per_cam_write_clocks(real_ts)
            # The nav tick calls _per_cam_sync_shared_widgets itself, once per pass. The
            # direct callers (settle, master switch, live advance, in-place redraw) are
            # one-shots and pay for it here.
            if not defer_labels:
                self._per_cam_sync_shared_widgets(cam_idx, real_ts)
        it      = cam_items[cam_idx_f]

        if hasattr(self, '_cam_current_idx') and cam_idx < len(self._cam_current_idx):
            self._cam_current_idx[cam_idx] = cam_idx_f

        n_cams = len(self._cam_items)
        max_side = self._cam_tile_side(n_cams)
        # Auto-stretch / brightness / contrast only on the selected camera(s)
        enh         = self._cam_enhance_on(cam_idx)
        brighten    = (1 if self.cb_bright.isChecked() else 0) if enh else 0
        bc          = self._bc() if enh else _RENDER_BC_NONE
        gradient_id = self.gradient_cb.currentIndex()
        subtract    = self.cb_subtract.isChecked()
        ref = self._cam_ref_arr_for(cam_idx, max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)

        cache = self._cam_caches[cam_idx]
        ck    = self._cam_ck(cam_idx, cam_idx_f)
        key   = (ck, max_side, brighten, gradient_id, bc,
                 id(ref) if ref is not None else None, sub_thr, sub_off)
        # From here on this is the ONLY render allowed to reach tile cam_idx.
        self._cam_set_shown_key(cam_idx, key)
        # Recorded BEFORE any paint: this is what the slider is asking for, and every
        # paint path compares against it to decide whether the tile is current.
        self._cam_note_target(cam_idx, it.ts_ns)
        cached = cache.get(key)
        if cached is not None and not cached.isNull():
            iv = self._multi_grid.get_img_view(cam_idx)
            if iv:
                iv.set_pixmap(cached)
                self._diag_cach += 1
            self._cam_note_painted(cam_idx, it.ts_ns)
            # Collect + one flush per pass when the tick owns the labels, exactly as
            # _display_multicam_index already does; _update_cam_diff_stats relayouts the
            # info panel with an N-line string, so per camera per mouse-move it was one of
            # the most expensive things on the drag path.
            if defer_labels:
                self._collect_cam_diff_stats(cam_idx, key)
            else:
                self._update_cam_diff_stats(cam_idx, key)
            self._cam_want[cam_idx] = None   # we're current; drop any stale pending load
            return

        # NOTE: the timestamp label is deliberately NOT written here. It used to be, which
        # is what made every tile's clock run ahead of its picture during a drag.

        # Repaint from the preloaded preview so the tile follows the handle, and let
        # _on_per_cam_released bring it back to full tile resolution when the drag ends.
        # Not restricted to the camera being dragged any more: the slaves are synced to the
        # same moment and had to pay a share read each, which is what made "only one camera
        # refreshes" — the dragged one hit the preview, the others queued reads.
        if self._proxy_try_paint_cam(cam_idx, cam_idx_f):
            self._cam_want[cam_idx] = None
            self._schedule_refine()
            return

        # Coalesced load: remember the latest wanted frame and only kick off a load if
        # this camera isn't already loading one. Intermediate frames are skipped so the
        # display tracks real time instead of replaying a growing backlog.
        # `ck` travels with the request: recomputing it in _start_cam_load would use
        # the offset AFTER a trim that may have landed in between, and the pixmap
        # would be cached under a key naming a different frame.
        self._cam_want[cam_idx] = (cam_idx_f, it.path, max_side, brighten,
                                   gradient_id, ref, sub_thr, bc, sub_off, ck)
        self._start_cam_load(cam_idx)
        # This tile now wants a frame it has not got — colour its label accordingly
        # without waiting for the dot tick (see _display_multicam_index). The nav tick does
        # this once for the whole pass instead.
        if not defer_labels:
            self._cam_refresh_stale_marks()

    def _cam_inflight_depth(self) -> int:
        """Share reads allowed IN FLIGHT PER CAMERA.

        This was effectively 1 (a boolean gate), which put a hard ceiling of ~7 frames/s
        on every tile: one read + decode off this share costs 130-160 ms, so one at a
        time is 1/0.145 s however many cores are idle. That ceiling — not the share, not
        the GUI thread — is why the tiles updated one after another instead of together.
        Depth D gives roughly 6.9*D frames/s per tile: 4 -> ~27/s, comfortably more than a
        33 ms navigation tick can ask for.

        Derived from the pool size so N cameras cannot over-subscribe it. The share is
        latency-bound and its measured optimum is ~16 concurrent reads (see _open_reader:
        31 / 106 / 204 frames/s at 1 / 8 / 16 threads), so past 4 cameras each one gets a
        smaller slice rather than the total growing without limit."""
        n = max(1, len(self._cam_items))
        return max(CAM_INFLIGHT_MIN, min(CAM_INFLIGHT_MAX, CAM_POOL_THREADS // n))

    def _cam_inflight_count(self, cam_idx: int) -> int:
        d = (self._cam_inflight_at[cam_idx]
             if cam_idx < len(getattr(self, "_cam_inflight_at", [])) else None)
        return len(d) if d else 0

    def _start_cam_load(self, cam_idx: int):
        """Kick off the latest pending load for one camera (see _per_cam_display_one).

        The concurrency cap lives HERE rather than at the call sites: callers just ask, and
        this decides whether there is room. That is what lets several consecutive drag
        positions decode at once per tile with only a single _cam_want slot."""
        if cam_idx >= len(self._cam_want):
            return
        want = self._cam_want[cam_idx]
        if want is None:
            return
        if self._cam_inflight_count(cam_idx) >= self._cam_inflight_depth():
            return
        self._cam_want[cam_idx] = None
        self._cam_req_seq += 1
        rid = self._cam_req_seq
        self._cam_inflight_at[cam_idx][rid] = time.monotonic()
        try:
            cam_idx_f, path, max_side, brighten, gradient_id, ref, sub_thr, bc, sub_off, ck = want
            sig  = self._cam_signals[cam_idx]
            key = (ck, max_side, brighten, gradient_id, bc,
                   id(ref) if ref is not None else None,
                   sub_thr if ref is not None else 0, sub_off if ref is not None else 0)
            # req_id carries the in-flight slot id so _on_cam_loaded can release exactly
            # the load that finished — it used to be a literal 0 and unused.
            self._cam_pool.start(LoadTask(
                self._gen, rid, cam_idx_f,
                path, max_side, brighten, gradient_id,
                sig, bc, ref, sub_thr, sub_off, key=key))
        except Exception:
            # Never leave the slot occupied if the launch itself failed — _on_cam_loaded
            # would never fire to release it, and the camera would slowly run out of depth
            # and then ignore every future frame / setting change.
            self._cam_inflight_at[cam_idx].pop(rid, None)

    def _reset_cam_pipeline(self):
        """Drop the per-camera load-coalescing gate so the next redraw always
        relaunches a load. Mirrors the single-cam handlers that clear _inflight.

        A load that never completes (typically a network-share read that hangs
        and never returns) leaves its in-flight slot occupied for good. Because a new load
        only starts while the camera is under _cam_inflight_depth(), enough of those and
        that camera silently stops repainting: it ignores subtract/gradient toggles and
        stays blank even though 'Ref set' shows in the info panel. Any explicit user action
        calls this first so the pipeline can never stay wedged. Clearing _cam_want is
        safe — the redraw that follows repopulates it. A stale task that finishes later just
        re-enters _on_cam_loaded, finds its id already gone, and drains harmlessly.

        Only loads older than CAM_PIPELINE_GRACE_S are released. Releasing every
        camera unconditionally meant a setting change (Offset, Diff threshold,
        gradient) launched a SECOND render of each tile while the first was still
        decoding — two renders per camera, with different params. That doubled the work
        behind every keystroke and, before the render key was checked on completion, let
        the loser of the race repaint the tile with the old settings.

        The age is now per LOAD, not per camera. With one boolean per camera the only
        question that could be asked was "is this camera's single load young?", so a
        healthy new load protected a hung old one indefinitely."""
        n = len(getattr(self, '_cam_inflight_at', []))
        if n == 0:
            return
        now = time.monotonic()
        for i in range(n):
            d = self._cam_inflight_at[i]
            if not d:
                continue
            for rid in [r for r, t in d.items()
                        if (now - t) >= CAM_PIPELINE_GRACE_S]:
                d.pop(rid, None)
        self._cam_want = [None] * n

    def _cam_load_watchdog(self):
        """Self-heal a camera whose in-flight load has hung. Any load outstanding longer
        than CAM_LOAD_WATCHDOG_S releases its slot and the camera relaunches whatever it
        currently wants, so live mode recovers without any user action."""
        if not self._is_multi_cam() or not getattr(self, '_cam_inflight_at', None):
            return
        now = time.monotonic()
        for i in range(len(self._cam_inflight_at)):
            d = self._cam_inflight_at[i]
            hung = [r for r, t in d.items() if (now - t) >= CAM_LOAD_WATCHDOG_S]
            if not hung:
                continue
            for r in hung:
                d.pop(r, None)
            self._start_cam_load(i)

    def _per_cam_slave_targets(self, master_cam: int, master_ts_ns: int) -> list:
        """[(cam_idx, ts_ns)] for every slave that has a frame near enough to the master's
        moment. Resolution only — nothing is displayed here, so _per_cam_nav_tick can do
        this once per tick instead of once per mouse-move event.

        A slave with no frame inside the window is left ALONE rather than dragged to its
        nearest neighbour: that is the difference between this and _display_multicam_index,
        and the reason the per-camera path cannot just reuse the shared scrub machinery."""
        out = []
        for i in range(len(self._per_cam_rows)):
            if i == master_cam:
                continue
            cam_ts = self._cam_ts[i] if i < len(self._cam_ts) else []
            if not cam_ts:
                continue
            # Kandidáti: největší <= master a nejmenší > master
            pos = bisect.bisect_right(cam_ts, master_ts_ns)
            left_idx  = pos - 1
            right_idx = pos
            best_idx = None
            best_diff = SLAVE_SYNC_MAX_NS + 1
            if 0 <= left_idx < len(cam_ts):
                d = master_ts_ns - cam_ts[left_idx]
                if d < best_diff:
                    best_diff = d; best_idx = left_idx
            if right_idx < len(cam_ts):
                d = cam_ts[right_idx] - master_ts_ns
                if d < best_diff:
                    best_diff = d; best_idx = right_idx
            if best_idx is None:
                continue  # žádný snímek v limitu — nech slave beze změny
            out.append((i, cam_ts[best_idx]))
        return out

    def _per_cam_sync_slaves(self, master_cam: int, master_ts_ns: int):
        """Display every slave at the master's moment. Used by the SETTLE paths (release,
        master switch, playback end); the drag itself goes through _per_cam_nav_tick, which
        resolves the same targets once per tick."""
        for i, slave_ts in self._per_cam_slave_targets(master_cam, master_ts_ns):
            # set_value blocks signals, so this cannot re-enter _on_per_cam_value_changed.
            self._per_cam_rows[i].set_value(self._per_cam_ts_to_slider(i, slave_ts))
            self._nav_frame[i] = self._per_cam_ts_to_frame(i, slave_ts)
            self._per_cam_display_one(i, slave_ts)

    def _per_cam_step(self, delta: int):
        """Posune master kameru o delta framů, ostatní synchronizuje."""
        master = self._per_cam_master_idx
        if master < 0 or master >= len(self._cam_items) or master >= len(self._cam_ts):
            return
        cam_ts = self._cam_ts[master]
        if not cam_ts:
            return
        # Use stored per-cam frame index so navigation steps by screenshot, not by time.
        # _nav_frame first: the render is deferred to the next tick, so _cam_current_idx
        # still holds the PREVIOUS frame and two keypresses inside one tick would both step
        # from it — the second press would be silently lost.
        cur_frame = self._nav_frame.get(
            master,
            self._cam_current_idx[master] if master < len(self._cam_current_idx) else 0)
        new_frame = max(0, min(len(cam_ts) - 1, cur_frame + delta))
        new_ts = cam_ts[new_frame]
        row = self._per_cam_rows[master]
        sv = self._per_cam_ts_to_slider(master, new_ts)
        row.set_value(sv)
        self._nav_request(master, new_ts, new_frame)

    def _live_advance_cam(self, cam_idx: int, latest_ts: int, was_at_end: bool):
        """Single source of truth for 'this camera received a fresh live frame'.

        In auto-follow mode each camera shows ITS OWN latest frame independently
        (decoupled from the master) so a slow/low-rate camera never freezes the
        others and the master tick no longer fires N simultaneous decodes. The
        master additionally drives the SHARED widgets (common slider, tickbar
        cursor, current_idx); per-camera time synchronisation is reserved for
        manual scrubbing (_per_cam_sync_slaves / _per_cam_step)."""
        if not (self._auto_follow or was_at_end):
            return
        # Always: this camera shows its own latest frame and moves its own row.
        self._per_cam_display_one(cam_idx, latest_ts)
        if cam_idx < len(self._per_cam_rows):
            sv = self._per_cam_ts_to_slider(cam_idx, latest_ts)
            self._per_cam_rows[cam_idx].set_value(sv)
        # Only the master drives the shared timeline widgets. Info panel + PV
        # fetch already fire inside _per_cam_display_one (master is _is_info_cam).
        if cam_idx == self._per_cam_master_idx:
            self.tickbar.set_cursor(latest_ts)
            sv_main = self._time_to_slider_value(latest_ts)
            self.slider.blockSignals(True)
            self.slider.setValue(sv_main)
            self.slider.blockSignals(False)
            if self.items:
                self.current_idx = len(self.items) - 1

    def _setup_multi_cam(self, cam_names: list[str], cam_folders: list[Path],
                         reset_items: bool = True,
                         cam_folder_lists: "list[list[Path]] | None" = None,
                         layout_config=None):
        """Inicializuje multi-camera stav."""
        self._cam_names   = cam_names
        self._cam_folders = cam_folders
        self._cam_folder_lists = cam_folder_lists if cam_folder_lists is not None else [[f] for f in cam_folders]
        n = len(cam_names)
        self._cam_last_update_ts = [0.0] * n  # reset refresh dots on camera switch
        self._cam_shown_ts_ns    = [0] * n
        self._cam_shown_mono     = [0.0] * n
        self._cam_target_ts_ns   = [0] * n
        self._cam_paint_tol_ns   = [0] * n
        self._cam_paint_preview  = [False] * n
        # Last time the expensive full os.listdir scan ran per camera (throttled
        # when a dir-watcher is active — see _online_poll_multi).
        self._cam_last_full_poll_ts = [0.0] * n
        # Scale poll pool for many cameras (2 threads per camera, min 8)
        self._poll_pool.setMaxThreadCount(max(8, n * 2))

        # Inicializuj per-camera struktury (přeskočit pokud data už jsou z předchozího scanu)
        if reset_items:
            self._cam_items       = [[] for _ in range(n)]
            self._cam_ts          = [[] for _ in range(n)]
            self._cam_poll_max_ts = [0] * n   # highest ts_ns seen per camera (for fast incremental poll)
        self._cam_caches       = [PixCache(self._cam_cache_size(n)) for _ in range(n)]
        # Carried forward, never reset: a cache-key index must never be reused
        # (see _ck). Cameras added later start at 0.
        _prev_off = getattr(self, "_cam_offsets", [])
        self._cam_offsets      = [(_prev_off[i] if i < len(_prev_off) else 0)
                                  for i in range(n)]
        self._cam_ref_images   = [None] * n
        self._cam_ref_paths    = [None] * n
        self._cam_ref_scaled   = [dict() for _ in range(n)]
        self._cam_diff_stats   = {}
        # References belong to the previous camera set — with Subtraction still on,
        # tell the user the reference is gone instead of silently rendering plain frames.
        self._refresh_ref_warning()
        self._cam_current_idx  = [0] * n   # per-camera frame index currently displayed
        # Per-camera load coalescing. _cam_want holds the latest frame still waiting to be
        # loaded, so a backlog is never replayed; _cam_inflight_at bounds how many reads a
        # single camera may have in flight (see _cam_inflight_depth). That used to be a
        # bare boolean — one read per camera — which is what capped each tile at ~7
        # frames/s and made the cameras look like they were taking turns.
        #
        # A dict {req_id: launch_monotonic} rather than a counter: _reset_cam_pipeline and
        # _cam_load_watchdog must release SPECIFIC hung loads by age while leaving healthy
        # ones accounted for, and a load released by either of them can still complete
        # afterwards — its id is simply gone by then, so its completion cannot decrement a
        # slot it no longer owns.
        self._cam_inflight_at = [dict() for _ in range(n)]
        self._cam_req_seq = 0
        self._cam_want = [None] * n
        # Render key each tile is SUPPOSED to be showing. A LoadTask that finishes
        # may carry OLDER render params (the user changed Offset / Diff threshold /
        # gradient while it was decoding); painting it left that tile stale with
        # nothing in _cam_want to correct it. Compared in _on_cam_loaded.
        self._cam_shown_key = [None] * n
        # ONE shared decode pool for every tile, created once in __init__ and only cleared
        # here. It used to be one 2-thread QThreadPool per camera, rebuilt on every camera
        # (re)load — which is why pools and their worker threads accumulated for the life of
        # the process, and why a slow camera's threads sat idle while its neighbour's queue
        # grew. Clearing drops the previous camera set's queued tasks; any already running
        # lands in _on_cam_loaded with a req_id absent from the rebuilt _cam_inflight_at and
        # is discarded harmlessly.
        self._cam_pool.clear()
        self._cam_signals = []
        for i in range(n):
            sig = LoaderSignals()
            sig.loaded.connect(
                lambda gen, req, idx, ms, br, gid, bo, img, key, cam_i=i:
                    self._on_cam_loaded(cam_i, gen, req, idx, ms, br, gid, bo, img, key))
            self._cam_signals.append(sig)

        self._multi_grid.setup_cameras(cam_names, layout_config=layout_config)
        self._multi_grid.set_label_font_size(self._cam_label_size_sb.value())
        self._switch_to_multi_view()
        self._sc_set_enabled(True)
        # Populate the SC camera selector combo
        self._sc_cam_combo.blockSignals(True)
        self._sc_cam_combo.clear()
        for name in cam_names:
            self._sc_cam_combo.addItem(name)
        self._sc_cam_combo.setCurrentIndex(0)
        self._sc_cam_combo.blockSignals(False)
        self._sc_cam_row_widget.setVisible(True)
        # Build per-camera sliders (hidden global slider, show per-cam rows)
        self._build_per_cam_sliders(cam_names)
        self.slider.setVisible(False)
        self.tickbar.setVisible(True)
        self.tickbar.set_cursor(None)

    # ================================================================ ONLINE MODE
    def _refresh_live_btn_style(self):
        """Live mode button: faint green while following live, faint red while off,
        neutral grey while it cannot be used at all. Called explicitly because the
        state is also set with signals blocked."""
        b = self._btn_auto_follow
        if not b.isEnabled():
            base = "background: #ececec; color: #777; border: 1px solid #cfcfcf;"
        elif b.isChecked():
            base = "background: #d9f2d9; color: #14532d; border: 1px solid #7cb87c;"
        else:
            base = "background: #f9dedb; color: #7f1d1d; border: 1px solid #d9a7a1;"
        b.setStyleSheet(
            "QPushButton { %s border-radius: 4px; padding: 4px 8px; font-weight: 600; }"
            % base)

    def _on_auto_follow_toggled(self, checked: bool):
        self._auto_follow = checked
        self._refresh_live_btn_style()
        if checked and not self._online_mode:
            self._start_online_mode()
        elif not checked and self._online_mode:
            self._stop_online_mode()
            # Leaving live mode = the user wants to browse back, so undo the live
            # memory cap and load the whole time window from disk again.
            self._restore_full_history()
            # Then preload that window at preview quality so the slider is smooth.
            # When a backfill is still pending (_live_trimmed) the preload starts in
            # _merge_restored_history instead, on the complete item list.
            if not self._live_trimmed:
                self._proxy_kick()
        if checked and self.items:
            last_idx = len(self.items) - 1
            if self._is_multi_cam():
                # Jump each per-cam slider to its own latest frame immediately
                for cam_i, row in enumerate(self._per_cam_rows):
                    cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                    if not cam_ts:
                        continue
                    latest_ts = cam_ts[-1]
                    sv = self._per_cam_ts_to_slider(cam_i, latest_ts)
                    row.set_value(sv)
                    self._per_cam_display_one(cam_i, latest_ts)
                master = self._per_cam_master_idx
                if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                    self.tickbar.set_cursor(self._cam_ts[master][-1])
            else:
                self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=True)

    @staticmethod
    def _cam_cache_size(n_cams: int) -> int:
        """Per-camera pixmap cache size, scaled by camera count so total memory
        stays bounded (12 cams × 80 pixmaps ≈ 0.5 GB was too much).

        The floor was 20, which at 12 cameras meant 13 entries — a guaranteed miss on any
        revisit, so nudging the slider back and forth re-read the share every time. These
        are small tile pixmaps (400 px, ~0.5 MB), and PixCache caps the heavy full-res ones
        separately via native_keep, so a bigger allowance is cheap: 64 × 12 × 0.5 MB is
        ~0.4 GB worst case, and far less at the sizes a drag actually renders."""
        return max(64, 480 // max(1, n_cams))

    def _ensure_dir_watcher(self, cam_i: int, folder: "Path"):
        """Start a _DirWatcher for folder if not already running. Dead watcher
        threads are replaced (RDCW silently exiting used to leave a dead entry
        in the dict forever); a killed-as-suspect watcher waits out a cooldown
        before it is recreated so a chronically failing share doesn't thrash."""
        if not _DIRWATCH_AVAILABLE:
            return
        if not hasattr(self, "_watcher_restart_block"):
            self._watcher_restart_block = {}
        key = str(folder)
        w = self._dir_watchers.get(key)
        if w is not None:
            if w.is_alive():
                return
            self._dir_watchers.pop(key, None)   # dead thread — replace below
        if time.monotonic() < self._watcher_restart_block.get(key, 0.0):
            return
        if self._dir_watch_sigs is None:
            return
        w = _DirWatcher(cam_i, folder, self._dir_watch_sigs, IMG_EXT)
        w.start()
        self._dir_watchers[key] = w

    def _watcher_strike(self, folders, poll_start_mono: float):
        """The listdir poll found frames a 'healthy' watcher should have pushed
        — RDCW died silently (thread alive, no events). Strike such watchers;
        at WATCHER_SUSPECT_STRIKES kill them so _ensure_dir_watcher recreates
        them after the cooldown. While no watcher covers the folder the poll
        automatically becomes the (fast) data source again."""
        if not hasattr(self, "_watcher_restart_block"):
            self._watcher_restart_block = {}
        for f in folders:
            key = str(f)
            w = self._dir_watchers.get(key)
            if w is None or not (w.ok and w.is_alive()):
                continue
            if w.last_event_mono >= poll_start_mono:
                continue   # it did deliver recently — not suspect
            w.suspect_strikes += 1
            if w.suspect_strikes >= WATCHER_SUSPECT_STRIKES:
                try:
                    w.stop()
                except Exception:
                    pass
                self._dir_watchers.pop(key, None)
                self._watcher_restart_block[key] = (
                    time.monotonic() + WATCHER_RESTART_COOLDOWN_S)

    def _stop_dir_watchers(self):
        for w in self._dir_watchers.values():
            try:
                w.stop()
            except Exception:
                pass
        self._dir_watchers.clear()

    def _prune_dir_watchers(self, keep_keys: "set[str]"):
        """Stop and drop dir-watchers for folders no longer being actively scanned."""
        for key in [k for k in self._dir_watchers if k not in keep_keys]:
            w = self._dir_watchers.pop(key, None)
            if w is not None:
                try:
                    w.stop()
                except Exception:
                    pass

    def _on_dir_watch_new_file(self, cam_i: int, folder_str: str, filename: str):
        """Immediate handler for a new image file detected by _DirWatcher.

        `folder_str` is the folder the watcher was actually armed on — it comes
        with the event and must never be re-derived here (see _DirWatchSignals)."""
        if not self._online_mode:
            return
        if not folder_str:
            return
        if not self._is_multi_cam():
            # Single-camera: merge through the same path the poll uses (its ts
            # cutoff de-duplicates double delivery).
            p = Path(folder_str) / filename
            ts_ns = parse_unix_ns_from_name(p)
            if ts_ns is None or not self._in_ts_windows(ts_ns):
                return
            cutoff = self.ts_list[-1] if self.ts_list else 0
            if ts_ns <= cutoff:
                return
            self._merge_single_new_items([Item(p, ts_ns)])
            return
        if cam_i >= len(self._cam_items):
            return
        p     = Path(folder_str) / filename
        ts_ns = parse_unix_ns_from_name(p)
        # Both poll paths filter to the picked window; this one did not, so a frame
        # from the previous hour folder that predates the window start got in AND
        # advanced the cutoff past frames the poll would still have delivered.
        if ts_ns is None or not self._in_ts_windows(ts_ns):
            return
        cutoff = (self._cam_poll_max_ts[cam_i]
                  if hasattr(self, "_cam_poll_max_ts") and cam_i < len(self._cam_poll_max_ts)
                  else 0)
        if ts_ns <= cutoff:
            return
        # Delegate to existing on_cam_found logic via a minimal synthetic result.
        # (Re-uses the same code path as _CamPollTask so display/timeline update is identical.)
        item = Item(p, ts_ns)
        # Use the make_callback closure result by directly calling the same update path.
        _prev_merged_len  = len(self.items)
        _prev_current_idx = self.current_idx
        self._cam_items[cam_i].append(item)
        self._cam_ts[cam_i].append(ts_ns)
        if hasattr(self, "_cam_poll_max_ts") and cam_i < len(self._cam_poll_max_ts):
            self._cam_poll_max_ts[cam_i] = ts_ns
        # Trim only while live mode is ON — see ONLINE_MAX_ITEMS
        if self._online_mode and len(self._cam_items[cam_i]) > ONLINE_MAX_ITEMS:
            trim = len(self._cam_items[cam_i]) - ONLINE_MAX_ITEMS
            self._cam_items[cam_i] = self._cam_items[cam_i][trim:]
            self._cam_ts[cam_i]    = self._cam_ts[cam_i][trim:]
            self._bump_cam_offset(cam_i, trim)
            # Same index shift the poll path applies: without it the frame this
            # camera is parked on silently changes identity after a trim (visible
            # with auto-follow off, where nothing recomputes the index).
            if cam_i < len(self._cam_current_idx):
                self._cam_current_idx[cam_i] = max(0, self._cam_current_idx[cam_i] - trim)
            self._live_trimmed = True
        self._online_last_new_ns = time.time()
        # A watcher push is the other way frames arrive — record it for the dot.
        self._note_cam_frames(cam_i)
        self._extend_shared_timeline_from_cams()
        total = sum(len(c) for c in self._cam_items)
        if not self._online_mode:
            self.lbl_scan_progress.setText(f"Frames: {total}")
        cam_ts_now = self._cam_ts[cam_i]
        if cam_ts_now:
            latest_ts = cam_ts_now[-1]
            was_at_end = (
                _prev_current_idx is not None and
                _prev_merged_len > 0 and
                _prev_current_idx >= _prev_merged_len - 1
            )
            # Each camera advances independently to its own latest frame; the
            # master also drives the shared slider/tickbar (same path as polling).
            self._live_advance_cam(cam_i, latest_ts, was_at_end)

    def _start_online_mode(self):
        self._online_mode = True
        # Live mode shows the newest frame at full quality — the preloaded preview
        # is useless there and would keep competing for share bandwidth.
        self._proxy_cancel(drop=True)
        # Start real-time dir watchers for all known camera folders — single
        # camera included (it used to rely on polling alone; now both modes get
        # instant pushes with the poll as safety net).
        if _DIRWATCH_AVAILABLE:
            self._dir_watch_sigs = _DirWatchSignals()
            self._dir_watch_sigs.new_file.connect(self._on_dir_watch_new_file)
            if self._is_multi_cam():
                for cam_i, folder_list in enumerate(self._cam_folder_lists):
                    for folder in active_scan_folders(folder_list):
                        self._ensure_dir_watcher(cam_i, folder)
            elif self.opened_folders:
                for folder in active_scan_folders(self.opened_folders):
                    self._ensure_dir_watcher(0, folder)
        self._online_timer.start()
        self._btn_auto_follow.setEnabled(True)
        self._btn_auto_follow.blockSignals(True)
        self._btn_auto_follow.setChecked(True)
        self._btn_auto_follow.blockSignals(False)
        self._auto_follow = True
        self._refresh_live_btn_style()
        self._online_lbl.setText("Online: ON")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self._online_blink_state = False
        self._online_last_new_ns = 0.0
        self._online_last_poll_ts = 0.0
        self._online_blink_timer.start()
        self._cam_dot_timer.start()
        self._online_dot.setStyleSheet("font-size: 14px; color: #22cc22;")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #22cc22;")
        self.lbl_scan_progress.setText("Online mode: <span style='color:#22bb22;font-weight:700;'>ACTIVE</span>")
        self._online_poll_running = False

    def _stop_online_mode(self):
        self._online_mode = False
        self._stop_dir_watchers()
        self._dir_watch_sigs = None
        self._online_timer.stop()
        self._online_blink_timer.stop()
        self._cam_dot_timer.stop()
        for cv in self._multi_grid._cam_views:
            cv.dim_refresh_dot()
        self._btn_auto_follow.blockSignals(True)
        self._btn_auto_follow.setChecked(False)
        self._btn_auto_follow.blockSignals(False)
        self._auto_follow = False
        self._refresh_live_btn_style()
        self._online_lbl.setText("Inactive")
        self._online_lbl.setStyleSheet("font-size: 10px; color: #555;")
        self.lbl_scan_progress.setText(
            "Online mode: <span style='color:#cc2222;font-weight:700;'>INACTIVE</span>"
        )
        self._online_dot.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_dot_top.setStyleSheet("font-size: 14px; color: #aaa;")
        self._online_poll_running = False

    # ---------------- restoring what the live cap trimmed ----------------
    @staticmethod
    def _merge_items_by_ts(mem_items: list, disk_items: list) -> list:
        """Union of in-memory and freshly scanned items, unique by timestamp and
        sorted. In-memory items win on a duplicate ts — their path is the one the
        pixmap cache is already keyed on."""
        by_ts = {it.ts_ns: it for it in disk_items}
        by_ts.update({it.ts_ns: it for it in mem_items})
        return [by_ts[k] for k in sorted(by_ts)]

    def _extend_axis_to_items(self):
        """Grow the tickbar axis so it covers every timestamp in self.ts_list
        (restored frames can sit outside the axis live mode had settled on)."""
        if not self.ts_list:
            return
        changed = False
        if self.ts_list[0] < self.axis_min_ns:
            self.axis_min_ns = ns_from_dt(floor_to_hour(_dt_from_ns(self.ts_list[0])))
            changed = True
        if self.ts_list[-1] > self.axis_max_ns:
            dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
            span_h = math.ceil((self.ts_list[-1] - ns_from_dt(dt0)) / ONE_HOUR_NS)
            self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_h))
            changed = True
        if changed:
            self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

    def _restore_full_history(self):
        """Live mode keeps only the newest ONLINE_MAX_ITEMS frames per camera in
        memory. Turning it off means the user wants to scrub back, so re-scan the
        opened folders and merge the whole time window back in. Runs on the scan
        pool (SMB shares are slow); the frame on screen stays on screen."""
        if not getattr(self, "_live_trimmed", False):
            return
        if self._is_multi_cam():
            folder_lists = ([list(fl) for fl in self._cam_folder_lists]
                            if self._cam_folder_lists
                            else [[f] for f in self._cam_folders])
        else:
            folder_lists = [list(self.opened_folders)] if self.opened_folders else []
        if not any(folder_lists):
            return
        if getattr(self, "_backfill_running", False):
            return
        self._backfill_running = True
        gen = self._gen
        # Remember the moment being watched, not the index — indices shift once
        # older frames are prepended.
        keep_ts = None
        if self.current_idx is not None and 0 <= self.current_idx < len(self.ts_list):
            keep_ts = self.ts_list[self.current_idx]
        self.lbl_scan_progress.setText("Loading full history…")

        class _BackfillSignals(QObject):
            done = Signal(list)

        sig = _BackfillSignals(self)
        self._backfill_sig = sig   # keep alive until the merge runs

        def on_done(per_cam: list):
            self._backfill_running = False
            if gen != self._gen:
                return
            self._merge_restored_history(per_cam, keep_ts)

        sig.done.connect(on_done)

        class _BackfillTask(QRunnable):
            def __init__(self, folder_lists, signal):
                super().__init__()
                self._folder_lists = folder_lists
                self._sig = signal

            def run(self):
                result = []
                for folders in self._folder_lists:
                    items: list[Item] = []
                    for folder in folders:
                        try:
                            with os.scandir(folder) as it:
                                for e in it:
                                    if not e.is_file():
                                        continue
                                    name = e.name
                                    dot = name.rfind(".")
                                    if dot < 0 or name[dot:].lower() not in IMG_EXT:
                                        continue
                                    p = Path(e.path)
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None:
                                        continue
                                    items.append(Item(p, ts_ns))
                        except Exception:
                            pass
                    items.sort(key=lambda x: x.ts_ns)
                    result.append(items)
                self._sig.done.emit(result)

        self.scan_pool.start(_BackfillTask(folder_lists, sig))

    def _merge_restored_history(self, per_cam: list, keep_ts):
        """Merge the re-scanned full history into the timeline and put the user
        back on the frame they were watching."""
        if self._online_mode:
            return   # live mode came back on meanwhile — the cap rules again
        # The re-scan reads whole hour folders — re-apply the picked window so a
        # minute-precise selection is not widened by turning live mode off.
        per_cam = [self._filter_to_ts_windows(items) for items in per_cam]
        if self._is_multi_cam():
            for cam_i, disk_items in enumerate(per_cam):
                if cam_i >= len(self._cam_items):
                    break
                merged = self._merge_items_by_ts(self._cam_items[cam_i], disk_items)
                self._cam_items[cam_i] = merged
                self._cam_ts[cam_i]    = [it.ts_ns for it in merged]
                if (hasattr(self, "_cam_poll_max_ts")
                        and cam_i < len(self._cam_poll_max_ts) and merged):
                    self._cam_poll_max_ts[cam_i] = merged[-1].ts_ns
            self._rebuild_shared_items_from_cams()
        else:
            disk_items = per_cam[0] if per_cam else []
            merged = self._merge_items_by_ts(self.items, disk_items)
            self.items   = merged
            self.ts_list = [it.ts_ns for it in merged]
        # The restored frames moved in AHEAD of the live window, so every index
        # shifted and a key issued before this merge would now name a different
        # frame. Both merges are supersets of what was in memory, so the plain
        # length bump is enough here.
        self._invalidate_shared_ckeys()
        for _ci in range(len(self._cam_items)):
            self._invalidate_cam_ckeys(_ci)
        if not self.items:
            self._live_trimmed = False
            return
        self._extend_axis_to_items()
        self._apply_marks_to_tickbar()
        # Same scrub-resolution scaling the initial scan applies for big sets
        n = len(self.items)
        self._scrub_side = 500 if n >= 15000 else (600 if n >= 6000 else 900)
        idx = len(self.items) - 1
        if keep_ts is not None:
            idx = min(bisect.bisect_left(self.ts_list, keep_ts), len(self.ts_list) - 1)
        self.current_idx = idx
        self.target_idx  = idx
        if self._is_multi_cam():
            self._display_multicam_index(idx, update_slider=True)
        else:
            self._display_exact_index(idx, self.ts_list[idx], update_slider=True)
        total = (sum(len(c) for c in self._cam_items) if self._is_multi_cam()
                 else len(self.items))
        self.lbl_scan_progress.setText(f"Frames: {total}")
        self.lbl_index.setText(f"{idx + 1} / {len(self.items)}")
        self._live_trimmed = False
        # The full window is back — preload it at preview quality for smooth scrubbing.
        self._proxy_kick()

    def _on_online_blink(self):
        """Blikání kolečka online indikátoru: zelené = poll běží, červené = poll se zasekl."""
        self._online_blink_state = not self._online_blink_state
        # Červená pouze pokud poll timer neběžel déle než 5 s (zaseknutý poller)
        stale = (self._online_last_poll_ts > 0 and
                 time.time() - self._online_last_poll_ts > 5.0)
        if stale:
            color = "#cc2222" if self._online_blink_state else "#660000"
        else:
            color = "#22cc22" if self._online_blink_state else "#116611"
        self._online_dot.setStyleSheet(f"font-size: 14px; color: {color};")
        self._online_dot_top.setStyleSheet(f"font-size: 14px; color: {color};")

    def _online_poll(self):
        """Voláno každých 200ms."""
        self._online_last_poll_ts = time.time()
        if self._is_multi_cam():
            if not self._cam_folder_lists and not self._cam_folders:
                return
            # Multi-cam: per-camera tasks run independently, no global gate
            self._online_poll_multi()
        else:
            if not self.opened_folders:
                return
            if getattr(self, '_online_poll_running', False):
                return
            # Keep watchers matched to the currently active hour folders
            active = active_scan_folders(self.opened_folders)
            if _DIRWATCH_AVAILABLE and self._dir_watch_sigs is not None:
                self._prune_dir_watchers({str(f) for f in active})
                for f in active:
                    self._ensure_dir_watcher(0, f)
            # With a HEALTHY watcher frames are pushed instantly — the listdir
            # poll is only a safety net and runs slowly. Without one, polling
            # is the data source: keep the original fast 200 ms cadence.
            has_watcher = False
            for f in poll_scan_folders(self.opened_folders):
                w = self._dir_watchers.get(str(f))
                if w is not None and w.ok and w.is_alive():
                    has_watcher = True
                    break
            if has_watcher:
                now = time.monotonic()
                last = getattr(self, '_single_last_full_poll_mono', 0.0)
                if now - last < ONLINE_WATCHER_POLL_INTERVAL_S:
                    return
                self._single_last_full_poll_mono = now
            self._online_poll_single_bg()

    def _merge_single_new_items(self, new_items: list):
        """Merge freshly arrived single-camera frames into the timeline and
        advance the display. Shared by the listdir poll AND the dir-watcher
        push — both deliver through the identical code path (the ts cutoff in
        each caller de-duplicates double delivery)."""
        # A manual refresh with live mode OFF re-lists whole hour folders, so the
        # picked window still applies (online mode keeps its end open).
        new_items = self._filter_to_ts_windows(new_items)
        if not new_items:
            return
        new_items.sort(key=lambda x: x.ts_ns)

        # Capture state BEFORE mutating — needed for correct was_at_end check
        _prev_len = len(self.items)
        _prev_current_idx = self.current_idx

        # Append-only — items list is always sorted, new items are all newer
        self.items = self.items + new_items
        self.ts_list = self.ts_list + [it.ts_ns for it in new_items]

        # Cap to ONLINE_MAX_ITEMS — drop oldest frames to prevent unbounded growth.
        # Only while live mode is ON; with it OFF the full history must stay
        # browsable (turning it off restores what was trimmed).
        if self._online_mode and len(self.items) > ONLINE_MAX_ITEMS:
            trim = len(self.items) - ONLINE_MAX_ITEMS
            self.items = self.items[trim:]
            self.ts_list = self.ts_list[trim:]
            if self.current_idx is not None:
                self.current_idx = max(0, self.current_idx - trim)
            self._items_offset += trim
            self._live_trimmed = True

        # Rozšiř osu pokud nové snímky přesahují
        ts_max = self.ts_list[-1]
        if ts_max > self.axis_max_ns:
            dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
            span_hours = math.ceil(
                (ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
            self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_hours))
            self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

        self._online_last_new_ns = time.time()
        self.lbl_scan_progress.setText("Online mode: <span style='color:#22bb22;font-weight:700;'>ACTIVE</span>")
        self.lbl_index.setText(
            f"{(self.current_idx or 0) + 1} / {len(self.items)}")
        # Live mode off means this came from Refresh — extend the preview over the
        # frames it added (debounced; a no-op while live mode is on).
        self._proxy_schedule_topup()

        last_idx = len(self.items) - 1
        # Show newest frame if: auto-follow is on, OR slider was already at the end
        was_at_end = (
            _prev_current_idx is not None and
            _prev_len > 0 and
            _prev_current_idx >= _prev_len - 1
        )
        if self._auto_follow or was_at_end:
            # Live mode always wants the NEWEST frame, so anything still queued for an
            # older one is dead weight on the same share — and since _open_reader buffers
            # each file in RAM while it decodes, a backlog costs memory as well as
            # bandwidth. Measured in the 2 h soak: without this, a run of arrivals faster
            # than the share could serve piled up 770 queued loads and 700 MB of working
            # set before draining. The display epoch already stops those from painting;
            # this stops them from running at all.
            if self._online_mode:
                self.load_pool.clear()
                self._inflight.clear()
                self._want_display_req.clear()
            self._display_exact_index(
                last_idx, self.items[last_idx].ts_ns, update_slider=True)

    def _online_poll_single_bg(self):
        """Spustí background scan pro single-camera — neblokuje UI."""
        self._online_poll_running = True
        # Only the newest hour folder(s) can still receive frames — scanning the
        # whole accumulated list every tick is what made live mode lag grow over time.
        folders = poll_scan_folders(self.opened_folders)
        gen = self._gen
        poll_start_mono = time.monotonic()
        # Use ts_ns cutoff instead of path set — much faster O(n) single pass
        cutoff_ns = self.ts_list[-1] if self.ts_list else 0
        # Same O(new files) optimisation as multi-cam (see _CamPollTask): remember
        # names already parsed per folder so a near-full hour-folder isn't Path+regex
        # re-parsed on every tick. Prune sets for folders no longer active.
        active_keys = {str(f) for f in active_scan_folders(self.opened_folders)}
        for _k in list(self._poll_seen.keys()):
            if _k not in active_keys:
                self._poll_seen.pop(_k, None)
        seen_map = {str(f): self._poll_seen.setdefault(str(f), set()) for f in folders}

        def on_found(new_items):
            self._online_poll_running = False
            if gen != self._gen or not new_items:
                return
            # The poll found frames itself — if a "healthy" watcher covers these
            # folders it silently missed them (RDCW death) → strike/replace it.
            self._watcher_strike(folders, poll_start_mono)
            self._merge_single_new_items(new_items)

        class _PollSignals2(QObject):
            found = Signal(list, list)  # (new_items, new_folders)

        sig2 = _PollSignals2()

        def on_found_with_folders(new_items, new_folders):
            # Register newly discovered hour-folders so future polls scan them
            # too, and watch them (hour rollover).
            if new_folders:
                for nf in new_folders:
                    if nf not in self.opened_folders:
                        self.opened_folders.append(nf)
                if self._online_mode:
                    for f in active_scan_folders(self.opened_folders):
                        self._ensure_dir_watcher(0, f)
            on_found(new_items)

        sig2.found.connect(on_found_with_folders)

        class _PollTask(QRunnable):
            def __init__(self, folders, cutoff, signal, seen_map=None):
                super().__init__()
                self._folders = folders
                self._cutoff  = cutoff
                self._sig = signal
                self._seen_map = seen_map

            def run(self):
                new_items = []
                known = set(self._folders)
                for folder in self._folders:
                    try:
                        folder_path = Path(folder)
                        seen = None if self._seen_map is None else self._seen_map.get(str(folder))
                        for name in os.listdir(folder):
                            if seen is not None:
                                if name in seen:
                                    continue
                                seen.add(name)
                            dot = name.rfind(".")
                            if dot < 0 or name[dot:].lower() not in IMG_EXT:
                                continue
                            p = folder_path / name
                            ts_ns = parse_unix_ns_from_name(p)
                            if ts_ns is None or ts_ns <= self._cutoff:
                                continue
                            new_items.append(Item(p, ts_ns))
                    except Exception:
                        pass

                # Probe next UTC hour-folders (same logic as _CamPollTask)
                new_folders = []
                for folder in list(self._folders):
                    try:
                        cam_name = folder.name
                        hour_dir = folder.parent
                        day_dir  = hour_dir.parent
                        try:
                            current_utc_hour = int(hour_dir.name)
                        except ValueError:
                            continue
                        for delta in range(1, 4):
                            next_h = (current_utc_hour + delta) % 24
                            if next_h < current_utc_hour and delta == 1:
                                try:
                                    from datetime import date as _date, timedelta as _td
                                    day_parts = (int(day_dir.parent.parent.name),
                                                 int(day_dir.parent.name),
                                                 int(day_dir.name))
                                    next_day = _date(*day_parts) + _td(days=1)
                                    candidate = (day_dir.parent.parent.parent
                                                 / str(next_day.year)
                                                 / str(next_day.month)
                                                 / str(next_day.day)
                                                 / str(next_h)
                                                 / cam_name)
                                except Exception:
                                    continue
                            else:
                                candidate = day_dir / str(next_h) / cam_name
                            if candidate in known or candidate in new_folders:
                                break
                            if _probe_hour_folder(candidate):
                                new_folders.append(candidate)
                                known.add(candidate)
                                try:
                                    cand_path = Path(candidate)
                                    for name in os.listdir(candidate):
                                        dot = name.rfind(".")
                                        if dot < 0 or name[dot:].lower() not in IMG_EXT:
                                            continue
                                        p = cand_path / name
                                        ts_ns = parse_unix_ns_from_name(p)
                                        if ts_ns is None or ts_ns <= self._cutoff:
                                            continue
                                        new_items.append(Item(p, ts_ns))
                                except Exception:
                                    pass
                            else:
                                break
                    except Exception:
                        pass

                self._sig.found.emit(new_items, new_folders)

        self._poll_sig2 = sig2  # keep alive until next tick (no-parent QObject needs explicit ref)
        task = _PollTask(folders, cutoff_ns, sig2, seen_map)
        self._poll_pool.start(task)

    def _online_poll_multi(self):
        """Per-camera independent polling — each camera runs its own _CamPollTask in parallel."""
        gen = self._gen
        folder_lists = list(self._cam_folder_lists) if self._cam_folder_lists else [[f] for f in self._cam_folders]
        n_cams = len(folder_lists)
        if n_cams == 0:
            return

        if not hasattr(self, '_cam_poll_running') or len(self._cam_poll_running) != n_cams:
            self._cam_poll_running = [False] * n_cams
        if not hasattr(self, '_cam_poll_sigs') or len(self._cam_poll_sigs) != n_cams:
            self._cam_poll_sigs = [None] * n_cams
        poll_max_ts = getattr(self, '_cam_poll_max_ts', [0] * n_cams)
        now = time.monotonic()
        if not hasattr(self, '_cam_last_full_poll_ts') or len(self._cam_last_full_poll_ts) != n_cams:
            # Stagger the first polls so N cameras don't all hit the SMB share
            # in the same tick — spread them across one min-interval.
            step = ONLINE_POLL_MIN_INTERVAL_S / max(1, n_cams)
            self._cam_last_full_poll_ts = [
                now - ONLINE_POLL_MIN_INTERVAL_S + i * step for i in range(n_cams)]
        if not hasattr(self, '_cam_poll_interval') or len(self._cam_poll_interval) != n_cams:
            self._cam_poll_interval = [ONLINE_POLL_MIN_INTERVAL_S] * n_cams

        # Keep dir-watchers only on the folders we still actively scan, so a long
        # session does not leak one open SMB handle + thread per elapsed hour.
        # Re-ensure afterwards: dead/struck watchers get replaced (post-cooldown).
        active_keys: set[str] = set()
        active_per_cam: "list[list]" = []
        for cam_i in range(n_cams):
            act = active_scan_folders(folder_lists[cam_i])
            active_per_cam.append(act)
            for f in act:
                active_keys.add(str(f))
        self._prune_dir_watchers(active_keys)
        # Drop seen-name sets for folders no longer scanned (hour rollover) so the
        # cache stays at the newest 1-2 folders per camera instead of one full
        # ~12k-name set per elapsed hour.
        for _k in list(self._poll_seen.keys()):
            if _k not in active_keys:
                self._poll_seen.pop(_k, None)
        if _DIRWATCH_AVAILABLE and self._dir_watch_sigs is not None:
            for cam_i in range(n_cams):
                for f in active_per_cam[cam_i]:
                    self._ensure_dir_watcher(cam_i, f)

        for cam_i in range(n_cams):
            if self._cam_poll_running[cam_i]:
                continue  # this camera's previous task still running — skip tick

            # Only the newest hour folder(s) can still receive frames — scanning
            # the whole accumulated list every tick is what made live mode lag
            # worse the longer it ran.
            folders  = poll_scan_folders(folder_lists[cam_i])
            cutoff   = poll_max_ts[cam_i] if cam_i < len(poll_max_ts) else 0
            cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else ""

            # A watcher only counts as coverage while it is HEALTHY: RDCW can
            # silently fail on UNC/SMB paths (thread exits, entry stays in the
            # dict). With a healthy watcher frames stream in instantly and the
            # listdir poll is just a rollover/overflow safety net; without one,
            # polling is the data source and runs at the adaptive interval.
            has_watcher = False
            if _DIRWATCH_AVAILABLE:
                for f in folders:
                    w = self._dir_watchers.get(str(f))
                    if w is not None and w.ok and w.is_alive():
                        has_watcher = True
                        break
            interval = (ONLINE_WATCHER_POLL_INTERVAL_S if has_watcher
                        else self._cam_poll_interval[cam_i])
            if (now - self._cam_last_full_poll_ts[cam_i]) < interval:
                continue

            sig = _CamPollSignals()
            self._cam_poll_sigs[cam_i] = sig  # keep reference so it isn't GC'd

            def make_callback(ci, g, _folders=folders, _had_watcher=has_watcher,
                              _poll_start=now):
                def on_cam_found(cam_idx: int, new_items: list, new_folders: list):
                    self._cam_poll_running[cam_idx] = False
                    if g != self._gen:
                        return
                    # Polls list whole hour folders — keep the picked window
                    # (online mode leaves its end open, so live frames pass).
                    new_items = self._filter_to_ts_windows(new_items)
                    if new_items and _had_watcher:
                        # The safety-net poll beat a "healthy" watcher to these
                        # frames — RDCW died silently. Strike/replace it so the
                        # camera drops back to fast polling instead of lagging
                        # behind a dead watcher.
                        self._watcher_strike(_folders, _poll_start)
                    # Adaptive pacing: an active camera polls fast again, an
                    # idle one backs off (bounded) to spare the SMB share.
                    if hasattr(self, '_cam_poll_interval') and cam_idx < len(self._cam_poll_interval):
                        if new_items:
                            self._cam_poll_interval[cam_idx] = ONLINE_POLL_MIN_INTERVAL_S
                        else:
                            self._cam_poll_interval[cam_idx] = min(
                                ONLINE_POLL_MAX_INTERVAL_S,
                                self._cam_poll_interval[cam_idx] * ONLINE_POLL_BACKOFF)
                    # The refresh dot must NOT be bumped here unconditionally: a
                    # camera that stopped writing images keeps completing polls
                    # forever, which made the dot blink green over a frozen
                    # picture. Only real new frames count (below).
                    if new_items:
                        self._note_cam_frames(cam_idx)
                    if new_folders and cam_idx < len(self._cam_folder_lists):
                        for nf in new_folders:
                            if nf not in self._cam_folder_lists[cam_idx]:
                                self._cam_folder_lists[cam_idx].append(nf)
                                self._ensure_dir_watcher(cam_idx, nf)
                    if not new_items or cam_idx >= len(self._cam_items):
                        return
                    _prev_merged_len = len(self.items)
                    _prev_current_idx = self.current_idx
                    self._cam_items[cam_idx].extend(new_items)
                    self._cam_ts[cam_idx].extend(it.ts_ns for it in new_items)
                    # Cap per-camera list to ONLINE_MAX_ITEMS to prevent unbounded
                    # growth — live mode only (see ONLINE_MAX_ITEMS)
                    if self._online_mode and len(self._cam_items[cam_idx]) > ONLINE_MAX_ITEMS:
                        trim = len(self._cam_items[cam_idx]) - ONLINE_MAX_ITEMS
                        self._cam_items[cam_idx] = self._cam_items[cam_idx][trim:]
                        self._cam_ts[cam_idx]    = self._cam_ts[cam_idx][trim:]
                        self._bump_cam_offset(cam_idx, trim)
                        if hasattr(self, '_cam_current_idx') and cam_idx < len(self._cam_current_idx):
                            self._cam_current_idx[cam_idx] = max(0, self._cam_current_idx[cam_idx] - trim)
                        self._live_trimmed = True
                    if cam_idx < len(self._cam_poll_max_ts):
                        self._cam_poll_max_ts[cam_idx] = self._cam_ts[cam_idx][-1]
                    self._online_last_new_ns = time.time()
                    self._extend_shared_timeline_from_cams()
                    total_frames = sum(len(c) for c in self._cam_items)
                    if self._online_mode:
                        self.lbl_scan_progress.setText("Online mode: <span style='color:#22bb22;font-weight:700;'>ACTIVE</span>")
                    else:
                        self.lbl_scan_progress.setText(f"Frames: {total_frames}")
                    self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")
                    cam_ts_now = self._cam_ts[cam_idx] if cam_idx < len(self._cam_ts) else []
                    if cam_ts_now:
                        latest_ts = cam_ts_now[-1]
                        was_at_end = (
                            _prev_current_idx is not None and
                            _prev_merged_len > 0 and
                            _prev_current_idx >= _prev_merged_len - 1
                        )
                        # Each camera advances independently to its own latest frame;
                        # the master also drives the shared slider/tickbar (see helper).
                        self._live_advance_cam(cam_idx, latest_ts, was_at_end)
                return on_cam_found

            sig.found.connect(make_callback(cam_i, gen))
            self._cam_poll_running[cam_i] = True
            self._cam_last_full_poll_ts[cam_i] = now
            seen_map = {str(f): self._poll_seen.setdefault(str(f), set()) for f in folders}
            self._poll_pool.start(_CamPollTask(cam_i, folders, cutoff, cam_name, sig, seen_map))

    def _rebuild_shared_items_from_cams(self):
        """
        Builds a merged, sorted timeline from all cameras' timestamps.
        self.items / self.ts_list contain unique timestamps across all cameras —
        the slider moves through real time and each camera independently shows
        its latest frame with ts_ns <= current slider time.
        """
        if not self._cam_items:
            return
        all_items = []
        seen_ts: set[int] = set()
        for cam_items in self._cam_items:
            for it in cam_items:
                if it.ts_ns not in seen_ts:
                    seen_ts.add(it.ts_ns)
                    all_items.append(it)
        if not all_items:
            return
        all_items.sort(key=lambda it: it.ts_ns)
        _prev_len    = len(self.items)
        self.items   = all_items
        self.ts_list = [it.ts_ns for it in self.items]
        # Every shared index was just re-derived — old cache keys are meaningless.
        self._invalidate_shared_ckeys(_prev_len)

        # Rozšiř osu
        if self.ts_list:
            ts_max = self.ts_list[-1]
            if ts_max > self.axis_max_ns:
                dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
                span_h = math.ceil((ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
                self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_h))
                self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

        # Per-camera sorted timestamp arrays for fast bisect lookup
        self._cam_ts = [[it.ts_ns for it in cam_items] for cam_items in self._cam_items]

    def _extend_shared_timeline_from_cams(self):
        """
        Fast incremental update of the shared timeline during online polling.
        Only appends new timestamps — no full re-sort. Called after _cam_items
        have already been extended with strictly-newer items appended at the end.
        """
        if not self._cam_items or not self.ts_list:
            self._rebuild_shared_items_from_cams()
            return
        current_max_ts = self.ts_list[-1]
        new_items = []
        seen_ts: set[int] = set()
        for ci, cam_items in enumerate(self._cam_items):
            # Only look at the tail that is newer than current_max_ts.
            # self._cam_ts is kept in sync with _cam_items — rebuilding the
            # array here would be O(total frames) on the UI thread per event.
            ts_arr = self._cam_ts[ci] if ci < len(self._cam_ts) else [it.ts_ns for it in cam_items]
            start = bisect.bisect_right(ts_arr, current_max_ts)
            for it in cam_items[start:]:
                if it.ts_ns not in seen_ts:
                    seen_ts.add(it.ts_ns)
                    new_items.append(it)
        if not new_items:
            return
        new_items.sort(key=lambda it: it.ts_ns)
        self.items.extend(new_items)
        self.ts_list.extend(it.ts_ns for it in new_items)
        # Cap shared timeline to ONLINE_MAX_ITEMS — live mode only, so a refresh
        # with live mode off (this method is called from there too) never drops
        # history the user still wants to scrub back into.
        n_cams = len(self._cam_items)
        shared_cap = ONLINE_MAX_ITEMS * max(1, n_cams)
        if self._online_mode and len(self.items) > shared_cap:
            trim = len(self.items) - shared_cap
            self.items = self.items[trim:]
            self.ts_list = self.ts_list[trim:]
            if self.current_idx is not None:
                self.current_idx = max(0, self.current_idx - trim)
            self._items_offset += trim
            self._live_trimmed = True
        # Extend axis if needed
        ts_max = self.ts_list[-1]
        if ts_max > self.axis_max_ns:
            dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
            span_h = math.ceil((ts_max - ns_from_dt(dt0)) / ONE_HOUR_NS)
            self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_h))
            self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
            # After axis expansion, recalculate per-cam sliders: old integer values now
            # map to different timestamps on the wider axis. Jump each to its latest frame.
            if self._auto_follow and self._is_multi_cam():
                for cam_i, row in enumerate(self._per_cam_rows):
                    cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                    if not cam_ts:
                        continue
                    sv = self._per_cam_ts_to_slider(cam_i, cam_ts[-1])
                    row.set_value(sv)
        # Live mode off means this came from Refresh — extend the preview over the
        # frames it added (debounced; a no-op while live mode is on).
        self._proxy_schedule_topup()

    def open_folder(self):
        # Nejdřív zkontroluj že máme nastavené časové okno
        if self.last_pick_axis_override is None:
            QMessageBox.information(self, "Time window not set",
                "Please set a time window first using the 'Time window' button.")
            return

        # Počkej max 3s na přednahraná data
        import time as _time
        deadline = _time.time() + 3.0
        while not getattr(self, '_cameras_loaded', True) and _time.time() < deadline:
            QApplication.processEvents()
            _time.sleep(0.05)

        preloaded = getattr(self, '_preloaded_cameras', None)

        day_obj = self.last_pick_date
        dlg = CameraPickerDialog(
            day_obj,
            self.last_pick_hour_from,
            self.last_pick_hour_to,
            self.last_pick_cam_names,
            self,
            preloaded_cameras=getattr(self, '_preloaded_cameras', None),
            multi_grid=self._multi_grid,
            windows=getattr(self, '_last_pick_windows', None))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        cam_names = dlg.selected_camera_names()
        if not cam_names:
            return
        layout_cfg = dlg.layout_config

        # Ulož do paměti
        self.last_pick_cam_names = cam_names

        # Preserve online mode if already active; _pending_online_mode covers first launch
        online = self._online_mode or getattr(self, '_pending_online_mode', False)
        axis_override = self.last_pick_axis_override
        hour_from = self.last_pick_hour_from
        hour_to   = self.last_pick_hour_to
        segments  = getattr(self, '_last_pick_segments', None)

        # Sestav složky
        cam_folder_lists: list[list[Path]] = []
        for cam_name in cam_names:
            folders = DatePickerDialog.selected_folders_static(
                day_obj, hour_from, hour_to,
                Path(DEFAULT_OPEN_ROOT) / cam_name,
                segments=segments,
                min_from=self.last_pick_min_from, min_to=self.last_pick_min_to)
            cam_folders_with_cam = []
            for f in folders:
                cf = f / cam_name if not f.name == cam_name else f
                cam_folders_with_cam.append(cf)
            cam_folder_lists.append(cam_folders_with_cam)

        self.lbl_selected_range.setText(self._range_label(
            segments, hour_from, hour_to,
            self.last_pick_min_from, self.last_pick_min_to))

        if len(cam_names) == 1:
            self._stop_online_mode()
            # Preserve overlay from multi-cam grid for this camera before switching to single
            grid_state = self._multi_grid._overlay_store.get(cam_names[0])
            if grid_state is None:
                # Current single-cam might be this camera — save its overlay too
                if self._cam_names and self._cam_names[0] == cam_names[0]:
                    grid_state = MultiCameraGrid._save_iv_overlay(self.img_view)
            self._switch_to_single_view()
            self._cam_names = [cam_names[0]]
            folders = cam_folder_lists[0]
            existing = [f for f in folders if _is_dir_quiet(f)]
            if not existing:
                QMessageBox.warning(self, "Folder not found",
                    _camera_folder_problem(cam_names[0], folders))
                return
            self.last_open_dir = existing[0]
            if online:
                self._pending_online_mode = True
            # Pre-load grid_state into img_view so _start_scan saves and restores it
            if grid_state is not None:
                MultiCameraGrid._restore_iv_overlay(self.img_view, grid_state)
            # Reference grid for single-cam view — show flag comes from saved
            # config (defaults to hidden for all cameras, diodes included).
            self.img_view.show_pdxm1_grid = get_pdxm1_grid_config(cam_names[0]).show
            self.img_view.pdxm1_cam_name = cam_names[0]
            self._start_scan(existing, axis_override=axis_override,
                             folder_label=str(existing[0]))
        else:
            self._stop_online_mode()
            # Save single-cam img_view overlay into multi-grid store before switching
            if self._cam_names and len(self._cam_names) == 1:
                self._multi_grid._overlay_store[self._cam_names[0]] = \
                    MultiCameraGrid._save_iv_overlay(self.img_view)
            self._start_multi_cam_scan(
                cam_names, cam_folder_lists,
                axis_override=axis_override,
                online=online,
                layout_config=layout_cfg)

    def _start_multi_cam_scan(
        self,
        cam_names: list[str],
        cam_folder_lists: list[list[Path]],
        axis_override,
        online: bool,
        layout_config=None,
    ):
        """Spustí scan pro více kamer naráz."""
        self._setup_multi_cam(cam_names, [], layout_config=layout_config)
        self.axis_override = axis_override
        self._gen += 1
        gen = self._gen

        if self._scan_task is not None:
            self._scan_task.cancel()
            self._scan_task = None

        self._reset_ui_for_new_scan([], "Multi-camera")
        self._switch_to_multi_view()

        n_cams = len(cam_names)
        finished_count = [0]
        self._cam_items  = [[] for _ in range(n_cams)]
        self._cam_ts     = [[] for _ in range(n_cams)]
        # Uchovej všechny složky per-kamera (pro online poll)
        cam_all_folders: list[list[Path]] = [[] for _ in range(n_cams)]
        cam_first_folder: list[Path | None] = [None] * n_cams

        # Pomocné signály pro každou kameru — bezpečné přes Qt signal/slot
        class _CamScanSignals(QObject):
            done = Signal(int, list, list)  # cam_i, items, folders

        _signals = _CamScanSignals(self)

        def on_cam_scan_done(cam_i: int, items: list, folders: list):
            if gen != self._gen:
                return
            items = self._filter_to_ts_windows(items)
            _prev_len = len(self._cam_items[cam_i])
            self._cam_items[cam_i] = items
            self._cam_ts[cam_i]    = [it.ts_ns for it in items]
            self._invalidate_cam_ckeys(cam_i, _prev_len)
            if hasattr(self, '_cam_poll_max_ts') and cam_i < len(self._cam_poll_max_ts):
                self._cam_poll_max_ts[cam_i] = items[-1].ts_ns if items else 0
            cam_all_folders[cam_i] = folders          # všechny složky
            cam_first_folder[cam_i] = folders[0] if folders else None
            finished_count[0] += 1
            self.lbl_scan_progress.setText(
                f"Scanned {finished_count[0]}/{n_cams} cameras…")
            if finished_count[0] == n_cams:
                first_folders = [f for f in cam_first_folder if f is not None]
                self._on_multi_scan_all_done(
                    gen, cam_names,
                    first_folders,
                    cam_all_folders,
                    axis_override, online)

        _signals.done.connect(on_cam_scan_done)

        _pick_args = (self.last_pick_date, self.last_pick_hour_from, self.last_pick_hour_to,
                      getattr(self, '_last_pick_segments', None),
                      self.last_pick_min_from, self.last_pick_min_to)

        for cam_i, (cam_name, folder_list) in enumerate(
                zip(cam_names, cam_folder_lists)):

            # Každá kamera jako samostatný QRunnable — správně přes Qt thread pool
            class _CamScanTask(QRunnable):
                def __init__(self, ci, cname, folders, pick_args, sig):
                    super().__init__()
                    self._ci = ci
                    self._cname = cname
                    self._folders = folders
                    self._pick = pick_args
                    self._sig = sig

                def run(self):
                    # Existence filtering runs HERE, off the UI thread — one
                    # stat per folder per camera over SMB froze the open with
                    # many cameras on a slow link.
                    existing = [f for f in self._folders if _is_dir_quiet(f)]
                    if not existing:
                        try:
                            pd, hf, ht, segs, mf, mt = self._pick
                            rebuilt = DatePickerDialog.selected_folders_static(
                                pd, hf, ht, Path(DEFAULT_OPEN_ROOT) / self._cname,
                                segments=segs, min_from=mf, min_to=mt)
                            existing = [f for f in rebuilt if _is_dir_quiet(f)]
                        except Exception:
                            existing = []
                    items: list[Item] = []
                    for folder in existing:
                        try:
                            with os.scandir(folder) as it:
                                for e in it:
                                    if not e.is_file():
                                        continue
                                    name = e.name
                                    dot = name.rfind(".")
                                    if dot < 0 or name[dot:].lower() not in IMG_EXT:
                                        continue
                                    p = Path(e.path)
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None:
                                        continue
                                    items.append(Item(p, ts_ns))
                        except Exception:
                            pass
                    items.sort(key=lambda x: x.ts_ns)
                    self._sig.done.emit(self._ci, items, existing)

            task = _CamScanTask(cam_i, cam_name, list(folder_list), _pick_args, _signals)
            self.scan_pool.start(task)

    def _on_multi_scan_all_done(
        self, gen: int, cam_names: list[str],
        cam_folders: list[Path],
        cam_folder_lists: "list[list[Path]] | None",
        axis_override, online: bool
    ):
        if gen != self._gen:
            return

        self._cam_folders = cam_folders
        self._setup_multi_cam(cam_names, cam_folders, reset_items=False,
                              cam_folder_lists=cam_folder_lists,
                              layout_config=self._multi_grid._layout_config)

        # Nastav items ze první kamery pro slider
        self._rebuild_shared_items_from_cams()

        if not self.items:
            self.lbl_filename.setText("No images found.")
            self.prog.setVisible(False)
            self.btn_cancel_scan.setVisible(False)
            return

        # Nastav osu
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        if axis_override is not None:
            self.axis_min_ns, self.axis_max_ns = axis_override
        else:
            folder_axis = None
            self.axis_min_ns, self.axis_max_ns = self._choose_axis(
                folder_axis, ts_min, ts_max)

        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()

        # Povol ovládací prvky
        self.slider.setEnabled(True)
        self.btn_save.setEnabled(True)
        self.btn_save_range.setEnabled(True)
        self.btn_send_workshop.setEnabled(True)
        self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_set_a.setEnabled(True)
        self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True)
        self._btn_auto_follow.setEnabled(True)
        self._refresh_live_btn_style()
        self.btn_set_ref.setEnabled(True)
        self.btn_pointing.setEnabled(True)
        self.btn_pointing_live.setEnabled(True)
        self.btn_cal_circle.setEnabled(True)
        self.btn_cal_square.setEnabled(True)
        self.btn_cal_cross.setEnabled(True)
        self._sc_set_enabled(True)

        self.prog.setVisible(False)
        self.btn_cancel_scan.setVisible(False)
        total_frames = sum(len(c) for c in self._cam_items)
        self.lbl_scan_progress.setText(f"Frames: {total_frames}")
        self.lbl_index.setText(f"1 / {len(self.items)}")

        # Zobraz vždy poslední (nejnovější) snímek — ráno bývají kamery bez dat
        start_idx = len(self.items) - 1
        self._display_multicam_index(start_idx, update_slider=True)

        if online:
            self._pending_online_mode = False
            self.lbl_scan_progress.setText("Online mode: <span style='color:#22bb22;font-weight:700;'>ACTIVE</span>")
            self._start_online_mode()

        # Preload every camera's window at preview quality (no-op in live mode).
        self._proxy_kick()

        QTimer.singleShot(0, self._pv_update_overlay)

    @staticmethod
    def _range_label(segments, hour_from, hour_to,
                     min_from: int = 0, min_to: int = 0) -> str:
        """Build the 'Range: …' status label for single-day or per-day selections."""
        def _seg_txt(s):
            _d, hf, mf, ht, mt = _seg_fields(s)
            return f"{hf:02d}:{mf:02d}–{ht:02d}:{mt:02d}"
        if not segments:
            return f"Range: {hour_from:02d}:{min_from:02d} – {hour_to:02d}:{min_to:02d}"
        if len(segments) == 1:
            d = _seg_fields(segments[0])[0]
            return f"Range: {d.strftime('%d.%m')} {_seg_txt(segments[0])}"
        parts = [f"{_seg_fields(s)[0].strftime('%d.%m')} {_seg_txt(s)}"
                 for s in segments[:3]]
        more = "…" if len(segments) > 3 else ""
        return f"Range: {', '.join(parts)}{more} ({len(segments)} days)"

    def open_by_date(self):
        # Reopen on the last pick (day, day list and mode included). Nothing
        # picked yet in this session (hour_from is None) → the dialog keeps its
        # own default of the opened folder's day / today.
        has_pick = self.last_pick_hour_from is not None
        dlg = DatePickerDialog(
            self.last_open_dir,
            self.last_pick_hour_from,
            self.last_pick_hour_to,
            self,
            min_from_init=self.last_pick_min_from,
            min_to_init=self.last_pick_min_to,
            init_date=self.last_pick_date if has_pick else None,
            init_segments=self._last_pick_segments if has_pick else None,
            init_range_mode=getattr(self, "_last_pick_range_mode", False))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        axis_override = dlg.selected_axis()
        online = dlg.is_online_mode()
        multiday = dlg.is_multiday()
        if online:
            self._pending_online_mode = True

        # Per-day segments (None in plain single-day mode → legacy fast path)
        segments = dlg.selected_segments()
        self._last_pick_segments = segments
        self._last_pick_range_mode = dlg.is_range_mode()
        hour_from, min_from, hour_to, min_to = dlg.selected_times()
        if segments:
            first = _seg_fields(segments[0])
            self.last_pick_date = first[0]
            hour_from, min_from, hour_to, min_to = first[1], first[2], first[3], first[4]
        else:
            self.last_pick_date = dlg.selected_date_obj()
        self.last_pick_hour_from     = hour_from
        self.last_pick_hour_to       = hour_to
        self.last_pick_min_from      = min_from
        self.last_pick_min_to        = min_to
        self.last_pick_axis_override = axis_override
        # Minute-precise (and per-day) filter for the scanned frames. Online mode
        # keeps the end open so newly arriving images are never filtered out.
        windows = dlg.selected_windows()
        # Kept un-mangled for folder/camera enumeration — the online variant below
        # has an open end that no folder scan could walk.
        self._last_pick_windows = list(windows)
        if online and windows:
            windows = windows[:-1] + [(windows[-1][0], 1 << 62)]
        self._ts_windows = windows
        # Legacy field kept for backward compatibility (segments supersede it)
        self._last_pick_extra_dates: "list | None" = None

        self.lbl_selected_range.setText(
            self._range_label(segments, hour_from, hour_to, min_from, min_to))

        # Camera list for the pick. The dialog already scanned the union while it
        # was open — reuse it and only rescan when it came back empty.
        self._preloaded_cameras: list[tuple[str, str]] = list(dlg.preloaded_cameras())
        self._cameras_loaded = bool(self._preloaded_cameras)

        if not self._cameras_loaded:
            import threading as _thr
            # Union over every day/segment — an empty first day used to yield an
            # empty camera list (see cameras_for_windows).
            scan_windows = list(self._last_pick_windows)

            def worker():
                try:
                    cameras, _status = cameras_for_windows(scan_windows)
                except Exception:
                    cameras = []
                self._preloaded_cameras = cameras
                self._cameras_loaded = True

            _thr.Thread(target=worker, daemon=True).start()

        # Pokud máme zapamatované kamery, automaticky použij je
        if self.last_pick_cam_names:
            QTimer.singleShot(0, self._reload_with_last_cameras)

    def _reload_with_last_cameras(self):
        """Načte snímky s naposledy vybranými kamerami bez dotazu."""
        if not self.last_pick_cam_names:
            return
        # Počkej max 3s na přednahraná data
        import time as _time
        deadline = _time.time() + 3.0
        while not getattr(self, '_cameras_loaded', True) and _time.time() < deadline:
            QApplication.processEvents()
            _time.sleep(0.05)

        cam_names  = self.last_pick_cam_names
        day_obj    = self.last_pick_date
        hour_from  = self.last_pick_hour_from
        hour_to    = self.last_pick_hour_to
        axis_override = self.last_pick_axis_override

        segments    = getattr(self, '_last_pick_segments', None)
        extra_dates = getattr(self, '_last_pick_extra_dates', None)

        cam_folder_lists: list[list[Path]] = []
        for cam_name in cam_names:
            folders = DatePickerDialog.selected_folders_static(
                day_obj, hour_from, hour_to,
                Path(DEFAULT_OPEN_ROOT) / cam_name,
                extra_dates=extra_dates, segments=segments,
                min_from=self.last_pick_min_from, min_to=self.last_pick_min_to)
            cam_folder_lists.append([
                f / cam_name if not f.name == cam_name else f
                for f in folders])

        self.lbl_selected_range.setText(self._range_label(
            segments, hour_from, hour_to,
            self.last_pick_min_from, self.last_pick_min_to))

        if len(cam_names) == 1:
            self._stop_online_mode()
            # Preserve overlay from multi-cam grid for this camera before switching to single
            grid_state = self._multi_grid._overlay_store.get(cam_names[0])
            if grid_state is None and self._cam_names and self._cam_names[0] == cam_names[0]:
                grid_state = MultiCameraGrid._save_iv_overlay(self.img_view)
            self._switch_to_single_view()
            self._cam_names = [cam_names[0]]
            existing = [f for f in cam_folder_lists[0] if _is_dir_quiet(f)]
            if not existing:
                QMessageBox.warning(self, "Folder not found",
                    _camera_folder_problem(cam_names[0], cam_folder_lists[0]))
                return
            self.last_open_dir = existing[0]
            # Pre-load grid_state into img_view so _start_scan saves and restores it
            if grid_state is not None:
                MultiCameraGrid._restore_iv_overlay(self.img_view, grid_state)
            # Reference grid for single-cam view — show flag comes from saved
            # config (defaults to hidden for all cameras, diodes included).
            self.img_view.show_pdxm1_grid = get_pdxm1_grid_config(cam_names[0]).show
            self.img_view.pdxm1_cam_name = cam_names[0]
            self._start_scan(existing, axis_override=axis_override,
                             folder_label=str(existing[0]))
        else:
            online_flag = self._online_mode or getattr(self, '_pending_online_mode', False)
            self._stop_online_mode()
            # Save single-cam img_view overlay into multi-grid store before switching
            if self._cam_names and len(self._cam_names) == 1:
                self._multi_grid._overlay_store[self._cam_names[0]] = \
                    MultiCameraGrid._save_iv_overlay(self.img_view)
            self._start_multi_cam_scan(
                cam_names, cam_folder_lists,
                axis_override=axis_override,
                online=online_flag)

    def auto_start_online(self):
        """
        Called once on first Slider tab activation.
        Opens Time window dialog; if accepted on first-ever open, auto-opens Camera picker.
        """
        # A scan already in flight counts as "something is loaded": another tab
        # pushed a folder in (Shot Finder → Send to Image Slider switches to this
        # tab, which queues THIS call) and items/_cam_items are still empty while
        # that scan runs. Without this guard the Time-window dialog popped over
        # the pushed images and replaced them with its own scan.
        if self.items or self._cam_items or self._scan_task is not None:
            return
        is_first_open = self.last_pick_hour_from is None
        self.open_by_date()
        # Po prvním přijetí Time window → automaticky otevři Camera picker
        if is_first_open and self.last_pick_hour_from is not None:
            QTimer.singleShot(150, self.open_folder)

    def open_folder_path(self, folder: Path):
        if not folder or not folder.exists() or not folder.is_dir():
            QMessageBox.warning(self, "Folder not found", f"Folder does not exist:\n{folder}"); return
        self.last_open_dir = folder
        ax = axis_from_any_folder(folder)
        self._ts_windows = None      # explicit folder → no time-window filtering
        self.lbl_selected_range.setText("Range: single folder")
        self._start_scan([folder], axis_override=ax, folder_label=str(folder))

    def receive_external_folder(self, folder: Path, energy_map: "dict | None" = None,
                                discrete: bool = True,
                                cam_name: "str | None" = None) -> bool:
        """Load `folder` no matter what the Slider is currently doing.

        Public handoff for the other tabs ("Send to Image Slider"). open_folder_path
        alone was not enough: whatever the Slider was set to before could swallow
        or misrender the pushed images —
          • a multi-camera grid stayed visible while the pushed frame was decoded
            into the hidden single-image widget ("nothing happened"),
          • an armed/active online mode jumped the new session to live-follow and
            armed watchers on a temp folder,
          • a subtraction reference from another camera rendered every frame as
            |new − old ref| (near-black),
          • focus / watcher fullscreen kept the slider and tickbar hidden,
          • a stale Shot Finder energy map captioned the new images with old PVs.
        All of that is cleared here first. Returns False only if `folder` is gone.
        """
        folder = Path(folder)
        if not folder.exists() or not folder.is_dir():
            return False
        if getattr(self, "_focus_mode", False):
            self._toggle_focus_mode()
        if getattr(self, "_watcher_mode", False):
            self._toggle_watcher_mode()
        self._pending_online_mode = False
        try:
            self._stop_online_mode()
        except Exception:
            pass
        self.cancel_scan()
        # Back to the single-image view: _start_scan clears multi-cam DATA but
        # never un-hides _single_wrapper. cam_name (when the sender knows it)
        # labels the frames; without it the label stays empty rather than naming
        # whatever camera happened to be loaded before.
        self._cam_names = [cam_name] if cam_name else []
        self._switch_to_single_view()
        if hasattr(self, "cb_subtract") and self.cb_subtract.isChecked():
            self.cb_subtract.blockSignals(True)
            self.cb_subtract.setChecked(False)
            self.cb_subtract.blockSignals(False)
        self._ref_path = None
        self._ref_scaled = {}
        self._cam_diff_stats = {}
        if hasattr(self, "lbl_ref_status"):
            self.lbl_ref_status.setText("")
        if hasattr(self, "lbl_diff_stats"):
            self.lbl_diff_stats.setText("")
        self.img_view.set_cam_ref_text("")
        # Captions belong to THIS push only. Set before the scan: it survives
        # _reset_ui_for_new_scan and is read by _set_info_for at scan-done time,
        # so no post-load timer is needed.
        self._sf_energy_map = dict(energy_map or {})
        self._discrete_mode = bool(discrete)
        self.open_folder_path(folder)
        return True

    def open_file_list(self, files: list):
        """Load an explicit list of image Paths (e.g. from range search). No folder scan."""
        files = [Path(f) for f in files if Path(f).exists()]
        if not files:
            QMessageBox.information(self, "Range search", "No images found."); return
        items = []
        for p in files:
            ts = parse_unix_ns_from_name(p)
            if ts is None:
                continue
            items.append(Item(p, ts))
        items.sort(key=lambda it: it.ts_ns)
        if not items:
            QMessageBox.information(self, "Range search", "Could not parse timestamps from files."); return

        self._stop_online_mode()
        self._hard_reset_runtime()
        self.opened_folders = []
        self.opened_folder = None
        self._ts_windows = None      # explicit file list → no time-window filtering
        self._real_ts_list = []
        self._fake_ts_map = None
        self.tickbar.discrete_ticks = None
        self.tickbar.discrete_tick_labels = None
        self.ts_list = []; self.axis_min_ns = 0; self.axis_max_ns = 0
        self.current_idx = None
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0")
        self.lbl_date.setText("Date: —")
        self.cache = PixCache(CACHE_SIZE); self._display_req_id = 0
        self._inflight.clear(); self._want_display_req.clear()
        self.img_view.clear()
        for w in [self.slider, self.btn_save, self.btn_save_range,
                self.btn_play, self.btn_stop, self.btn_prev, self.btn_next, self.btn_set_a,
                self.btn_set_b, self.btn_clear_marks, self.btn_cal_circle, self.btn_cal_square,
                self.btn_cal_cross, self.btn_pointing, self.btn_save_ts, self.btn_goto_ts,
                self.btn_set_ref]:
            w.setEnabled(False)
        self.mark_a_ns = None; self.mark_b_ns = None; self.tickbar.set_marks(None, None)
        self.prog.setVisible(False); self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)

        self.items = items
        n = len(items)
        self.ts_list = [it.ts_ns for it in items]
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        self._scrub_side = 500 if n >= 15000 else (600 if n >= 6000 else 900)
        self.lbl_filename.setText(f"File: range search — {n} images")
        self.lbl_selected_range.setText(f"Range: {n} images (multi-day)")

        # Linear real-time axis — axis endpoints = first/last timestamp
        pad = max((ts_max - ts_min) // 40, 60_000_000_000) if ts_max > ts_min else 60_000_000_000
        self.axis_min_ns = ts_min - pad
        self.axis_max_ns = ts_max + pad
        self._real_ts_list = []
        self._fake_ts_map = None
        # Per-frame ticks only for small result sets — same cap as _on_scan_finished,
        # which this path was missing. A multi-day range search returns thousands of
        # frames, and a per-frame tick list makes every axis repaint (i.e. every
        # playback frame and every scrub tick) walk the whole list on the GUI thread.
        if n <= TICKBAR_DISCRETE_MAX:
            self.tickbar.discrete_ticks = self.ts_list[:]
            self.tickbar.discrete_tick_labels = [
                f"{_dt_from_ns(ts):%Y-%m-%d %H:%M:%S}"
                for ts in self.ts_list
            ]
        else:
            self.tickbar.discrete_ticks = None
            self.tickbar.discrete_tick_labels = None
        self.axis_override = None
        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()
        self.slider.setEnabled(True); self.btn_save.setEnabled(True); self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False); self.btn_prev.setEnabled(True); self.btn_next.setEnabled(True)
        self.btn_set_a.setEnabled(True); self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True)
        self.btn_refresh.setEnabled(False)
        self.btn_send_workshop.setEnabled(True)
        self._gen += 1
        sv = self._time_to_slider_value(ts_min)
        self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self.play_time_ns = ts_min; self.target_idx = 0
        self._display_exact_index(0, ts_min, update_slider=False)
        # Range search is a normal offline window, so it gets the preview too. This path
        # was the only loader that never kicked it, which left it with none of the
        # repaint-from-RAM benefit on precisely the multi-day result sets that need it.
        self._proxy_kick()

    def refresh_folder(self):
        """Donačte nové snímky ze stejných složek, zachová pozici a overlay."""
        if self._is_multi_cam():
            if not self._cam_folders:
                return
            self._refresh_multi_cam()
            return
        if not self.opened_folders:
            return
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⟳ …")
        self._refresh_gen = self._gen
        task = RefreshScanTask(self._gen, self.opened_folders)
        self._refresh_task = task
        task.signals.finished.connect(self._on_refresh_finished)
        self.scan_pool.start(task)

    def _refresh_multi_cam(self):
        """Donačte nové snímky pro všechny kamery v multi-cam módu."""
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⟳ …")
        gen = self._gen
        folder_lists = list(self._cam_folder_lists) if self._cam_folder_lists else [[f] for f in self._cam_folders]
        poll_max_ts = list(getattr(self, '_cam_poll_max_ts', [0] * len(folder_lists)))

        class _RefreshSignals(QObject):
            done = Signal(list)

        sig = _RefreshSignals(self)

        def on_done(new_per_cam: list):
            self.btn_refresh.setText("⟳ Refresh")
            self.btn_refresh.setEnabled(True)
            if gen != self._gen:
                return
            any_new = False
            for cam_i, new_items in enumerate(new_per_cam):
                if not new_items or cam_i >= len(self._cam_items):
                    continue
                any_new = True
                self._cam_items[cam_i].extend(new_items)
                self._cam_ts[cam_i].extend(it.ts_ns for it in new_items)
                if hasattr(self, '_cam_poll_max_ts') and cam_i < len(self._cam_poll_max_ts):
                    self._cam_poll_max_ts[cam_i] = self._cam_ts[cam_i][-1]
            if not any_new:
                return
            self._extend_shared_timeline_from_cams()
            total_frames = sum(len(c) for c in self._cam_items)
            self.lbl_scan_progress.setText(f"Frames: {total_frames}")
            self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")
            if self.current_idx is not None:
                self._display_multicam_index(self.current_idx, update_slider=True)

        sig.done.connect(on_done)

        class _RefreshTask(QRunnable):
            def __init__(self, folder_lists, poll_max_ts, signal):
                super().__init__()
                self._folder_lists = folder_lists
                self._poll_max_ts  = poll_max_ts
                self._sig = signal

            def run(self):
                result = []
                for cam_i, folders in enumerate(self._folder_lists):
                    cutoff = self._poll_max_ts[cam_i] if cam_i < len(self._poll_max_ts) else 0
                    new_items = []
                    for folder in folders:
                        try:
                            with os.scandir(folder) as it:
                                for e in it:
                                    if not e.is_file():
                                        continue
                                    p = Path(e.path)
                                    if p.suffix.lower() not in IMG_EXT:
                                        continue
                                    ts_ns = parse_unix_ns_from_name(p)
                                    if ts_ns is None or ts_ns <= cutoff:
                                        continue
                                    new_items.append(Item(p, ts_ns))
                        except Exception:
                            pass
                    new_items.sort(key=lambda x: x.ts_ns)
                    result.append(new_items)
                self._sig.done.emit(result)

        self.scan_pool.start(_RefreshTask(folder_lists, poll_max_ts, sig))

    def _on_refresh_finished(self, gen, new_items):
        self.btn_refresh.setText("⟳ Refresh")
        self.btn_refresh.setEnabled(True)
        self.btn_pointing.setEnabled(True)
        self.btn_pointing_live.setEnabled(True)
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_save_ts.setEnabled(True)
        if self._saved_timestamps:
            self.btn_goto_ts.setEnabled(True)
            self.btn_clear_ts.setEnabled(True)
        if gen != self._gen or not new_items:
            return

        # Zjisti které položky jsou skutečně nové (podle path)
        existing_paths = {it.path for it in self.items}
        added = [it for it in new_items if it.path not in existing_paths]
        if not added:
            return

        # Merge a sort
        _prev_last_ts = self.ts_list[-1] if self.ts_list else None
        merged = sorted(self.items + added, key=lambda it: it.ts_ns)
        self.items = merged
        self.ts_list = [it.ts_ns for it in self.items]
        # A Refresh can bring in frames OLDER than what is already loaded, and that
        # shifts every index after the insertion point (see _ck). Pure appends —
        # the normal case — keep their indices, so the cache survives them.
        if _prev_last_ts is not None and min(it.ts_ns for it in added) <= _prev_last_ts:
            self._invalidate_shared_ckeys()

        # Rozšiř osu pokud nové snímky přesahují
        if self.axis_override is not None:
            # osu řízenou uživatelem neměníme
            pass
        else:
            ts_max_new = self.ts_list[-1]
            if ts_max_new > self.axis_max_ns:
                dt0 = floor_to_hour(_dt_from_ns(self.ts_list[0]))
                span_hours = math.ceil(
                    (ts_max_new - ns_from_dt(dt0)) / ONE_HOUR_NS
                )
                self.axis_max_ns = ns_from_dt(dt0 + timedelta(hours=span_hours))
                self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)

        # Zachovej aktuální pozici — přepočítej slider
        if self.current_idx is not None:
            sv = self._time_to_slider_value(self.items[self.current_idx].ts_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)

        n_added = len(added)
        # Extend the preview over the frames Refresh just added. Without this the plan
        # stayed at the pre-Refresh size while `items` grew, so _proxy_covered() went on
        # reporting "covered" — the whole added tail, which is exactly where the user
        # scrubs after a Refresh, had no preview at all. The live-poll and multi-cam merges
        # already did this; the manual Refresh path was the one that did not.
        self._proxy_schedule_topup()
        if self._online_mode:
            self.lbl_scan_progress.setText(f"Online mode: +{n_added} new frame{'s' if n_added != 1 else ''}")
        else:
            self.lbl_scan_progress.setText(f"Refresh: +{n_added} new frames")
        self.lbl_index.setText(f"{(self.current_idx or 0) + 1} / {len(self.items)}")

    # ================================================================ OVERLAYS
    def _active_img_view(self) -> "ImageView":
        """Return the ImageView that overlay controls should act on."""
        if self._is_multi_cam():
            iv = self._multi_grid.selected_img_view()
            if iv is not None:
                return iv
        return self.img_view

    def _sync_overlay_checkboxes_from_iv(self, iv: "ImageView"):
        """Sync cb_cross/cb_circle/cb_square to match iv's current overlay state (no signal loops)."""
        self.cb_cross.blockSignals(True)
        self.cb_circle.blockSignals(True)
        self.cb_square.blockSignals(True)
        self.cb_cross.setChecked(iv.show_cross)
        self.cb_circle.setChecked(iv.show_circle)
        self.cb_square.setChecked(iv.show_square)
        self.cb_cross.blockSignals(False)
        self.cb_circle.blockSignals(False)
        self.cb_square.blockSignals(False)
        self._refresh_draw_btns()

    def _on_overlay_changed(self):
        iv = self._active_img_view()
        iv.show_cross  = self.cb_cross.isChecked()
        iv.show_circle = self.cb_circle.isChecked()
        iv.show_square = self.cb_square.isChecked()
        # If a checkbox was unchecked, clear draw mode for that shape
        if not self.cb_cross.isChecked()  and iv._draw_mode == "cross":
            iv.set_draw_mode("")
        if not self.cb_circle.isChecked() and iv._draw_mode == "circle":
            iv.set_draw_mode("")
        if not self.cb_square.isChecked() and iv._draw_mode == "square":
            iv.set_draw_mode("")
        self._refresh_draw_btns()
        iv.update()

    def calibrate_circle(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Circle calibration", "Wait until an image is displayed."); return
        if not self.cb_circle.isChecked(): self.cb_circle.setChecked(True)
        ok = iv.calibrate_circle_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Circle calibration",
                "Could not detect a circle.\nTip: enable Auto brightness first."); return
        iv.show_circle = True; iv.update()

    def calibrate_cross(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Cross calibration", "Wait until an image is displayed."); return
        if not self.cb_cross.isChecked():
            self.cb_cross.setChecked(True)
        ok = iv.calibrate_cross_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Cross calibration", "Could not compute centroid."); return
        iv.show_cross = True; iv.update()

    def calibrate_square(self):
        iv = self._active_img_view()
        if iv._pix is None or iv._pix.isNull():
            QMessageBox.information(self, "Square calibration", "Wait until an image is displayed."); return
        if not self.cb_square.isChecked(): self.cb_square.setChecked(True)
        ok = iv.calibrate_square_from_pixmap()
        if not ok:
            QMessageBox.warning(self, "Square calibration",
                "Could not detect a rectangle.\nTip: enable Auto brightness first."); return
        iv.show_square = True; iv.update()

    # ================================================================ SUBTRACTION PARAMS / STATS
    def _sub_params(self, ref) -> "tuple[int, int]":
        """(diff threshold, visibility offset) for one render. Both are 0 when the
        frame has no reference, so a non-subtraction render always produces the
        same cache key regardless of the spinbox values."""
        if ref is None:
            return (0, 0)
        return (int(self.sub_threshold_sb.value()), int(self.sub_offset_sb.value()))

    @staticmethod
    def _fmt_diff_stats(st: dict, compact: bool = False) -> str:
        """Difference summary line. `compact` fits one multi-cam tile per row in the
        275 px info panel; the full form is used for the single-camera view."""
        n, total = st.get("count", 0), st.get("total", 0)
        if n <= 0:
            return "0 px differ" if compact else "Diff: no pixels differ"
        pct = (100.0 * n / total) if total else 0.0
        if compact:
            return f"{n} px · avg {st['mean']:.1f} · {st['min']:.0f}–{st['max']:.0f}"
        return (f"Diff: {n} px ({pct:.2f}%) · avg {st['mean']:.1f} · "
                f"min {st['min']:.0f} · max {st['max']:.0f}")

    def _update_diff_stats(self, key):
        """Refresh the single-cam difference-statistics line for the frame rendered
        under `key`. Called from every path that shows a frame, so cache hits are
        labelled too."""
        if not self.cb_subtract.isChecked() or self._ref_path is None:
            self.lbl_diff_stats.setText("")
            return
        st = _diff_stats_get(key)
        if st is None:
            return   # stats evicted / not a diff render — keep the last numbers
        self.lbl_diff_stats.setText(self._fmt_diff_stats(st))

    def _collect_cam_diff_stats(self, cam_i: int, key):
        """Record one tile's diff statistics WITHOUT rebuilding the label.

        Formatting the label is O(cameras) and every setText forces a relayout of the
        info panel, so doing it per camera inside the display loop made one 33 ms scrub
        tick cost O(cameras²) formats and N relayouts — on the Subtraction path, which
        has no preview to fall back on either."""
        if not self.cb_subtract.isChecked():
            self._cam_diff_stats = {}
            return
        st = _diff_stats_get(key)
        if st is not None:
            self._cam_diff_stats[cam_i] = st

    def _flush_cam_diff_stats(self):
        """Write the collected statistics to the info line — once per display pass."""
        if not self.cb_subtract.isChecked():
            self.lbl_diff_stats.setText("")
            return
        lines = []
        for c in sorted(self._cam_diff_stats):
            if c >= len(self._cam_ref_paths) or self._cam_ref_paths[c] is None:
                continue
            name = _strip_cam_name(self._cam_names[c]) if c < len(self._cam_names) else f"cam {c}"
            lines.append(f"{name}: {self._fmt_diff_stats(self._cam_diff_stats[c], compact=True)}")
        self.lbl_diff_stats.setText("\n".join(lines))

    def _update_cam_diff_stats(self, cam_i: int, key):
        """Collect + show, for the callers that update a single tile."""
        self._collect_cam_diff_stats(cam_i, key)
        self._flush_cam_diff_stats()

    # ================================================================ BRIGHTNESS / CONTRAST
    def _bc(self) -> _RenderBC:
        """Brightness/contrast render params, honoring the 'Auto checkbox overrides
        slider' rule for each pair: contrast Auto (cb_bright) zeroes the manual
        contrast; brightness Auto (cb_bright_auto) zeroes the manual offset."""
        contrast = 0 if self.cb_bright.isChecked() else int(self.contrast_slider.value())
        if self.cb_bright_auto.isChecked():
            return _RenderBC(0, contrast, 1)
        return _RenderBC(int(self._brightness_offset), contrast, 0)

    def _refresh_auto_bc_sliders(self, path):
        """Park the greyed-out Contrast / Brightness sliders on the value the Auto
        pass actually applied to `path`, instead of leaving them at 0 while the
        picture on screen is clearly stretched. Signals are blocked: no reload, no
        cache invalidation.

        DISPLAY ONLY for contrast. Both now pivot on the frame's black level, so the
        operation matches — but the slider's gain tops out at ~3.9x while the
        auto-stretch of a dim frame needs 5x and more, so the parked number saturates
        at +127 and does not reproduce the picture. Unticking Auto therefore restores
        the user's own value instead (see _on_contrast_auto_changed).

        Brightness is display-only for the same reason of consistency: parking Auto's
        offset as the backing value meant that ticking Auto on and off once replaced the
        user's own brightness with Auto's (+99 on a typical frame) and there was no way
        back to it. An Auto checkbox has to be undoable."""
        vals = _auto_bc_get(path)
        if not vals:
            return
        c = vals.get("contrast")
        if c is not None and self.cb_bright.isChecked():
            self.contrast_slider.blockSignals(True)
            self.contrast_slider.setValue(int(c))
            self.contrast_slider.blockSignals(False)
        o = vals.get("offset")
        if o is not None and self.cb_bright_auto.isChecked():
            self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(int(o))
            self.brightness_slider.blockSignals(False)

    def _on_brightness_slider_changed(self, value):
        # Only user moves reach this (the Auto parking blocks signals), so this is the
        # value to return to when Auto is switched back off.
        self._brightness_offset = value
        self._brightness_manual = int(value)
        if not self.items or self.current_idx is None: return
        self._inflight.clear(); self._want_display_req.clear()
        if not self._brightness_debounce.isActive():
            self._brightness_debounce.start(60)

    def _reset_brightness_slider(self):
        self.brightness_slider.setValue(0)

    def _on_contrast_slider_changed(self, value):
        # Only user moves reach this (the Auto parking blocks signals), so this is
        # the value to return to when Auto is switched back off.
        self._contrast_manual = int(value)
        if not self.items or self.current_idx is None: return
        self._inflight.clear(); self._want_display_req.clear()
        if not self._brightness_debounce.isActive():
            self._brightness_debounce.start(60)

    def _reset_contrast_slider(self):
        self.contrast_slider.setValue(0)

    def _on_contrast_auto_changed(self):
        # Auto-stretch overrides the manual Contrast slider → grey it out while on.
        on = self.cb_bright.isChecked()
        self.contrast_slider.setEnabled(not on)
        self.btn_contrast_reset.setEnabled(not on)
        if not on:
            # Auto off → the manual slider is live again, so it must not be left on
            # the number Auto parked there (see _refresh_auto_bc_sliders): that is a
            # different operation with the same gain and it wrecks the picture.
            self.contrast_slider.blockSignals(True)
            self.contrast_slider.setValue(int(self._contrast_manual))
            self.contrast_slider.blockSignals(False)
        self._on_brightness_changed()

    def _on_bright_auto_changed(self):
        # Auto-level overrides the manual Brightness slider → grey it out while on.
        on = self.cb_bright_auto.isChecked()
        self.brightness_slider.setEnabled(not on)
        self.btn_brightness_reset.setEnabled(not on)
        if not on:
            # Auto off → put back the user's own offset, not the one Auto parked on the
            # greyed-out slider (see _refresh_auto_bc_sliders). Same rule as contrast.
            self._brightness_offset = int(self._brightness_manual)
            self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(int(self._brightness_manual))
            self.brightness_slider.blockSignals(False)
        self._on_brightness_changed()

    def _apply_brightness_debounced(self):
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._redraw_all_cams_in_place()
            return
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)

    def _load_raw_arr(self, path, max_side: int = 99999) -> "np.ndarray | None":
        """Load image as raw float32 grayscale array (no stretch/gradient).

        max_side scales exactly like the displayed frame's pipeline, so a
        reference decoded here matches the current frame pixel-for-pixel: an
        identical frame subtracts to 0 instead of leaving resample/normalization
        residue (which happened when the reference was kept at full resolution
        while the shown frame was downscaled)."""
        img = load_image_scaled(path, max_side, False, gradient_id=0, brightness_offset=0)
        if img.isNull():
            return None
        if img.format() != QImage.Format.Format_Grayscale8:
            img = img.convertToFormat(QImage.Format.Format_Grayscale8)
        ptr = img.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(img.sizeInBytes())
        return np.frombuffer(ptr, dtype=np.uint8).reshape(
            img.height(), img.bytesPerLine())[:, :img.width()].copy().astype(np.float32)

    def _ref_arr_for(self, max_side: int) -> "np.ndarray | None":
        """Single-cam subtraction reference decoded at the current display size.
        Cached per max_side so the same ndarray (stable id() for cache keys) is
        reused across frame loads until a new reference is set."""
        if self._ref_path is None:
            return None
        arr = self._ref_scaled.get(max_side)
        if arr is None:
            arr = self._load_raw_arr(self._ref_path, max_side)
            if arr is not None:
                self._ref_scaled[max_side] = arr
        return arr

    def _cam_ref_arr_for(self, cam_i: int, max_side: int) -> "np.ndarray | None":
        """Per-camera subtraction reference decoded at the current display size."""
        if cam_i >= len(self._cam_ref_paths) or self._cam_ref_paths[cam_i] is None:
            return None
        cache = self._cam_ref_scaled[cam_i]
        arr = cache.get(max_side)
        if arr is None:
            arr = self._load_raw_arr(self._cam_ref_paths[cam_i], max_side)
            if arr is not None:
                cache[max_side] = arr
        return arr

    def _set_reference_frame(self):
        # Multi-cam takes its reference from each camera's OWN frame (_cam_current_idx),
        # so it must not be gated on the shared timeline's current_idx — that gate made
        # "Set ref" a silent no-op whenever the merged index wasn't set yet.
        if self._is_multi_cam():
            if not self._cam_items or not any(self._cam_items):
                return
        elif self.current_idx is None or not self.items:
            return

        if self._is_multi_cam():
            # Per-camera reference: store for all currently selected cameras
            selected_indices = self._multi_grid.selected_cam_indices()
            if not selected_indices:
                # Cameras start UNSELECTED after every scan, so this was the normal
                # case: the click returned here silently — no badge, no reference, and
                # Subtraction then rendered every frame unchanged ("nothing happened").
                # Same fallback the Reset-zoom button uses: offer to cover all cameras.
                reply = QMessageBox.question(
                    self, "Set reference",
                    "No cameras selected — set the reference for ALL cameras?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                selected_indices = [i for i in range(len(self._cam_items))
                                    if self._cam_items[i]]
                if not selected_indices:
                    return
            set_names = []
            for cam_i in selected_indices:
                if cam_i >= len(self._cam_items) or not self._cam_items[cam_i]:
                    continue
                cam_items = self._cam_items[cam_i]
                # Reference the frame this camera is ACTUALLY showing right now. In
                # multi-cam mode the per-camera sliders drive _cam_current_idx, not the
                # shared self.current_idx (which stays pinned to the load-time last
                # frame). Reading current_idx here captured the reference at the wrong
                # time and — via the redisplay below — yanked every panel ~1 h forward
                # onto that stale timestamp (often a dark/no-beam frame, so the cameras
                # looked like they went blank).
                cam_idx = self._cam_current_idx[cam_i] if cam_i < len(self._cam_current_idx) else 0
                cam_idx = max(0, min(cam_idx, len(cam_items) - 1))
                it = cam_items[cam_idx]
                try:
                    # Record the reference by PATH only. The actual subtraction
                    # reference is decoded lazily at DISPLAY size and cached in
                    # _cam_ref_scaled (see _cam_ref_arr_for). The previous
                    # _load_raw_arr here did a blocking FULL-RES read of the frame
                    # from the network share on the UI thread, once per selected
                    # camera per click — multi-second stalls once the SMB session
                    # had been open a while — purely to fill _cam_ref_images, which
                    # is only ever tested for existence. A truthy flag is enough and
                    # also frees the ~20 MB/cam full-res array that was held for nothing.
                    self._cam_ref_images[cam_i] = True
                    self._cam_ref_paths[cam_i]  = it.path
                    self._cam_ref_scaled[cam_i] = {}   # re-decode at display size, drop stale sizes
                    ts_str = fmt_prague_full_from_ns(it.ts_ns)
                    self._multi_grid.set_cam_ref_status(cam_i, f"Ref: {ts_str}")
                    self._cam_caches[cam_i] = PixCache(self._cam_cache_size(len(self._cam_caches)))
                    cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam {cam_i}"
                    set_names.append(cam_name)
                except Exception:
                    pass
            if set_names:
                self._set_ref_status(f"Ref: {', '.join(set_names)}")
            else:
                QMessageBox.information(
                    self, "Set reference",
                    "No camera has a loaded frame yet — wait for the scan to finish.")
                return
            # Redraw each camera at ITS OWN current frame (don't snap to the shared
            # timeline) so setting a reference never moves the view. Reset the load
            # gate first: if a camera was already wedged (in-flight slots never freed), the
            # redraw below would set _cam_want but never relaunch, so the panel
            # would stay blank while the info panel shows 'Ref set' — exactly the
            # "reference is set but nothing on the cameras" symptom, which also made
            # a second Set-Reference appear to do nothing.
            self._reset_cam_pipeline()
            self._redraw_all_cams_in_place()
            return

        # Single-camera path — record the reference by PATH only; the subtraction
        # reference is decoded lazily at display size and cached in _ref_scaled
        # (see _ref_arr_for). Avoids a blocking full-res SMB read on the UI thread
        # (_ref_image was only ever written here, never read anywhere).
        it = self.items[self.current_idx]
        self._ref_path = it.path
        self._ref_scaled = {}   # re-decode at display size, drop stale sizes
        ts_str = fmt_prague_full_from_ns(it.ts_ns)
        self._set_ref_status(f"Ref: {ts_str}")
        # Badge on the image itself — the 1-camera layout is the multi-camera layout
        # with one camera, so it gets the same green "Ref: <timestamp>" strip the
        # tiles get via set_cam_ref_status (only the info-panel text was set here).
        self.img_view.set_cam_ref_text(f"Ref: {ts_str}")
        self.cache = PixCache(CACHE_SIZE)
        self._inflight.clear(); self._want_display_req.clear()
        # Drop any pending display gate so a load still in flight from the previous
        # reference/settings can't strand the pipeline (frozen slider/arrows).
        self._display_load_key = None; self._deferred_display = None
        if self.current_idx is not None:
            self._display_exact_index(
                self.current_idx, self.items[self.current_idx].ts_ns,
                update_slider=True)

    def _has_reference(self) -> bool:
        """True when at least one subtraction reference is set for the current mode."""
        if self._is_multi_cam():
            return any(p is not None for p in getattr(self, "_cam_ref_paths", []))
        return getattr(self, "_ref_path", None) is not None

    def _set_ref_status(self, text: str):
        """Reference line in the INFO panel, in its normal (grey) style."""
        self.lbl_ref_status.setStyleSheet(_REF_STATUS_STYLE)
        self.lbl_ref_status.setText(text)

    def _refresh_ref_warning(self):
        """Subtraction without a reference renders every frame unchanged and produces
        no statistics. Say so in the INFO panel instead of leaving a checkbox that
        silently does nothing (the reference is per camera, and cameras start
        unselected, so this is easy to hit)."""
        if self.cb_subtract.isChecked() and not self._has_reference():
            self.lbl_ref_status.setStyleSheet(_REF_WARN_STYLE)
            self.lbl_ref_status.setText("⚠ Subtraction on, no reference — click 'Set ref'")
            self.lbl_diff_stats.setText("")
        elif not self._has_reference():
            self._set_ref_status("")

    def _on_subtract_changed(self):
        """Subtraction on/off, Diff threshold or Offset changed.

        The pixmap caches are NOT dropped: sub_threshold, sub_offset, gradient_id
        and bc are all part of the render key, so a stale entry can never be served
        for the wrong settings. Wiping them forced a fresh decode of every camera
        off the share for every single spinbox step — the multi-second stall after
        typing an Offset — and threw away the frames needed to step back to the
        previous value. Keeping them makes a value you have already used instant.
        """
        self._refresh_ref_warning()
        # The preview layer is unusable while Subtraction is on, so _proxy_enabled now
        # depends on this checkbox: pause the sweep when it goes on, resume when off.
        # This slot is also wired to the threshold/offset spinboxes, hence the
        # changed-only variant.
        self._proxy_sync_enabled()
        if self._is_multi_cam():
            self._reset_cam_pipeline()
            # Re-render each camera at ITS OWN frame (per-cam sliders), like
            # _set_reference_frame and the brightness/gradient handlers do.
            # _display_multicam_index snapped every panel to the shared timeline's
            # current_idx instead — the load-time frame — so toggling Subtraction
            # jumped the cameras off the frames the reference had been taken from.
            self._redraw_all_cams_in_place()
            return
        self._inflight.clear(); self._want_display_req.clear()
        self._display_load_key = None; self._deferred_display = None
        if not self.items or self.current_idx is None: return
        self._display_exact_index(
            self.current_idx, self.items[self.current_idx].ts_ns,
            update_slider=True)

    def _on_preload_preview_changed(self):
        on = self.cb_preload_preview.isChecked()
        self._ui_state["preload_preview"] = on
        self._save_ui_state()
        self._proxy_was_enabled = self._proxy_enabled()
        if on:
            self._proxy_kick()
        else:
            # Turning it off is an explicit "stop spending memory on this", unlike the
            # temporary pauses in _proxy_kick — so free the decoded frames too.
            self._proxy_cancel(drop=True)

    def _on_brightness_changed(self):
        # Caches are keyed on bc/brighten, so they stay — see _on_subtract_changed.
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._reset_cam_pipeline()
            self._redraw_all_cams_in_place()
            return
        self._inflight.clear(); self._want_display_req.clear()
        self._display_load_key = None; self._deferred_display = None
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)

    def _on_gradient_changed(self):
        # Caches are keyed on gradient_id, so they stay — see _on_subtract_changed.
        if not self.items or self.current_idx is None: return
        if self._is_multi_cam():
            self._reset_cam_pipeline()
            self._redraw_all_cams_in_place()
            return
        self._inflight.clear(); self._want_display_req.clear()
        self._display_load_key = None; self._deferred_display = None
        idx = self.current_idx
        self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)
        if getattr(self, '_sc_preview_pixmap', None) is not None:
            self._run_spatial_contrast()

    # ================================================================ SPEED
    def _current_play_pct_per_s(self) -> float:
        try: return float(self.speed_cb.currentData())
        except: return 5.0

    def _play_is_exact(self) -> bool:
        return self._current_play_pct_per_s() <= PLAY_EXACT_PCT_PER_S_THRESHOLD

    def _reset_motion_tracking(self):
        self._last_motion_counter = None; self._last_target_idx = None; self._last_motion_ips = 0.0

    def _update_motion_speed(self, target_idx):
        now = time.perf_counter()
        if self._last_motion_counter is None or self._last_target_idx is None:
            self._last_motion_counter = now; self._last_target_idx = target_idx; self._last_motion_ips = 0.0; return
        dt = now - self._last_motion_counter
        if dt > 0: self._last_motion_ips = abs(target_idx - self._last_target_idx) / dt
        self._last_motion_counter = now; self._last_target_idx = target_idx

    def _adaptive_stride(self):
        ips = self._last_motion_ips
        if ips < 15:   return 1
        if ips < 40:   return 2
        if ips < 100:  return 3
        if ips < 250:  return 5
        if ips < 700:  return 10
        if ips < 1500: return 20
        return 40

    def _navigating(self) -> bool:
        """The user is moving through frames under their own control.

        Deliberately does NOT include _per_cam_scrubbing_cam: _on_per_cam_pressed now sets
        _is_scrubbing itself, and wheel / arrow / groove moves on a per-camera row have no
        sliderPressed at all — settled navigation deserves full tile quality. Use
        _proxy_is_moving where the sweep's own scheduling is concerned."""
        return bool(self._is_scrubbing or self._is_playing)

    def _cam_tile_side(self, n_cams: int) -> int:
        """Decode size for one multi-cam tile, scaled by how fast the user is moving.

        The base size is what the tile can actually show; while the user is sweeping
        through frames, a smaller render is worth far more than detail nobody can see at
        that speed, and it is N cameras' worth of saving per position. Settling brings the
        full tile size back (the refine pass re-renders every tile), so quality is only
        traded away while it cannot be perceived. Live mode never comes here — it decodes
        arriving frames at native resolution."""
        base = self._cam_tile_base_side(n_cams)
        if not self._navigating():
            return base
        # With Subtraction on there is no preview to fall back on, so every position is a
        # real read + decode + subtract — and max_side is part of both the pixmap key and
        # the reference key, so changing it mid-drag guarantees a cache miss AND a
        # re-decoded reference at the new size. Keep one size for the whole gesture.
        if self.cb_subtract.isChecked():
            return base
        ips = self._last_motion_ips
        if ips < 8:            # roughly a frame a second: full tile quality
            return base
        if ips < 40:
            return max(240, int(base * 0.7))
        return max(180, int(base * 0.45))

    def _cam_tile_base_side(self, n_cams: int) -> int:
        return 400 if n_cams >= 3 else (500 if n_cams == 2 else self._scrub_side)

    def _cam_tile_sides(self, n_cams: int) -> list:
        """Every size _cam_tile_side can return. The subtraction reference has to be
        decoded at each of them up front (_prewarm_drag_references): a size that first
        appears mid-drag would otherwise decode its reference synchronously on the GUI
        thread, inside a 33 ms scrub tick, once per camera."""
        base = self._cam_tile_base_side(n_cams)
        return sorted({base, max(240, int(base * 0.7)), max(180, int(base * 0.45))})

    def _current_decode_side(self):
        ips = self._last_motion_ips
        if self._is_playing:
            return PLAY_MAX_SIDE_SLOW if (self._play_is_exact() and ips < 40) else PLAY_MAX_SIDE_FAST
        if self._is_scrubbing:
            # Strictly the size latched at press time. A background history merge can
            # change _scrub_side mid-drag, but honouring that would ask for a size whose
            # subtraction reference _prewarm_drag_references never decoded — i.e. a
            # blocking share read inside the tick. One drag keeps its size; the new cap
            # applies from the next press.
            latched = self._drag_side
            zoomed = getattr(self.img_view, "_zoom_norm", None) is not None
            # Track 0, not the whole viewer: this branch only ever decides the SINGLE-cam
            # view's size (the tiles go through _cam_tile_side), so another camera's
            # coverage is none of its business.
            if self._proxy_usable() and self._proxy_covered_track(0) and not zoomed:
                # The preview repaints the drag from memory, so this size only decides
                # the quality of the occasional real load. Keep it LATCHED: it is part
                # of the pixmap-cache key, so switching it by drag speed made every
                # speed change a guaranteed cache miss and re-decoded the subtraction
                # reference at the new size, synchronously on the GUI thread.
                return latched
            # No preview to fall back on (Subtraction on, zoomed in, preload off) — every
            # step is a real read + decode + subtract, so keep the old speed-based
            # downscale. _on_slider_pressed pre-warms the reference at BOTH sizes, so
            # crossing the threshold still never decodes a reference mid-drag.
            return latched if ips < 40 else FAST_SCRUB_MAX_SIDE
        # Live mode, sitting on a single frame (not scrubbing / not playing): decode at
        # native resolution so the shown frame zooms and saves crisply. Live frames arrive
        # at only a few Hz, so one full-res decode per frame doesn't hurt responsiveness —
        # scrubbing and playback above keep the fast downscaled path, and _prefetch_idle
        # stays on _scrub_side.
        if self._online_mode:
            return FULL_RES_SIDE
        return self._scrub_side

    # ============================================== WHOLE-WINDOW PREVIEW (PROXY)
    # With live mode OFF the whole loaded time window is preloaded at
    # PROXY_MAX_SIDE, so moving the slider repaints from memory at screen rate
    # instead of queueing one share read + decode per position (which is what made
    # the picture run behind the slider on long windows). The frame the user stops
    # on is then re-rendered at native resolution by _refine_current_frame.
    def _proxy_enabled(self) -> bool:
        """Live mode wants the newest frame at full quality, not a preloaded
        window — and it must not compete with the preview for share bandwidth."""
        cb = getattr(self, "cb_preload_preview", None)
        if cb is not None and not cb.isChecked():
            return False
        # Live mode only. _auto_follow used to disqualify the preview as well, but
        # auto-follow with live mode OFF has nothing to follow — no frames arrive — while
        # the flag can easily still be set (it is only cleared when the user drags). That
        # combination silently disabled the entire preview layer for a browsing session.
        if self._online_mode:
            return False
        # Preview frames are small grayscale and can never stand in for a subtraction
        # render (see _proxy_usable), so sweeping the share to build them while
        # Subtraction is on is pure cost with no payoff.
        sub = getattr(self, "cb_subtract", None)
        if sub is not None and sub.isChecked():
            return False
        return True

    def _proxy_frame_bytes(self, lists: list) -> int:
        """Bytes one decoded preview frame costs, measured rather than assumed.

        Preview frames are raw grayscale at the source's bit depth (see load_proxy_gray),
        so this is width*height of the downscaled frame times 1 or 2 bytes — and a camera
        SMALLER than PROXY_MAX_SIDE is not downscaled at all, which is why guessing was
        wrong by an order of magnitude either way. Uses a frame already decoded when there
        is one; otherwise reads the image header only (no pixels) and computes the size the
        sweep will produce."""
        side = self._proxy_side()
        for tr in self._proxy_tracks:
            for arr in tr.frames.values():
                if isinstance(arr, tuple):
                    arr = arr[0]
                if arr is not None and getattr(arr, "nbytes", 0):
                    return int(arr.nbytes)
        for items in lists:
            if not items:
                continue
            try:
                r, _buf, _ba = _open_reader(items[len(items) // 2].path)
                sz = r.size()
                if sz.isValid() and sz.width() > 0 and sz.height() > 0:
                    w, h = sz.width(), sz.height()
                    scale = max(w, h) / side
                    if scale > 1.0:
                        w, h = max(1, int(w / scale)), max(1, int(h / scale))
                    # 1 byte/px unless raw uint16 storage was forced back on: a 16-bit
                    # source is stored as 8-bit codes plus its (lo, hi) — see
                    # load_proxy_gray — which is what makes a whole window fit.
                    bpp = 1
                    if not PROXY_STORE_8BIT and r.imageFormat() in (
                            QImage.Format.Format_Grayscale16, QImage.Format.Format_RGB16):
                        bpp = 2
                    return max(1, w * h * bpp)
            except Exception:
                pass
            break
        return side * side * (1 if PROXY_STORE_8BIT else 2)   # conservative fallback

    def _proxy_side(self) -> int:
        """Decoded side of a preview frame, smaller when there are many cameras.

        Justified by the app's own numbers rather than taste: at 12 cameras
        _cam_tile_side returns max(180, 400*0.45) = 180 px during a fast drag, so a 128 px
        preview is comparable to the real load it stands in for — while costing a ninth of
        the RAM of a 224 px 16-bit frame, which is the difference between sampling the
        window and holding all of it."""
        # Count cameras directly rather than going through _proxy_item_lists, which is gated
        # on _is_multi_cam() and so reads as 1 track whenever _cam_names has not been filled
        # in yet — that silently kept the largest size for every camera count.
        n = (len(self._proxy_tracks) or len(self._cam_items or [])
             or len(self._proxy_item_lists()) or 1)
        if n <= 4:
            return PROXY_MAX_SIDE
        if n <= 8:
            return PROXY_SIDE_MANY
        return PROXY_SIDE_MOST

    def _proxy_budget_per_track(self, lists: list) -> int:
        """How many preview frames each track may plan, from the RAM budget.

        The user's ask is "load the whole time window into memory". That is exactly what
        this does when the window fits; when it does not, the budget decides the sampling
        density instead of a fixed count deciding it. A 12-camera hour at 3.3 Hz is ~140k
        frames — no budget holds that at useful quality, so the honest behaviour is an even
        sample across the whole window (coarse→fine, see _proxy_plan) rather than a
        fully-loaded prefix and nothing after it.

        Two ceilings, not one. RAM is the obvious limit, but SWEEP TIME is usually the real
        one: a preview frame costs a whole file read off the share (setScaledSize saves
        decode CPU, not I/O), and the sweep runs at ~100 frames/s. 27k frames is 4.5
        minutes; 180k frames is half an hour, at any RAM figure. Planning frames that will
        not be read for 25 minutes is not coverage, so the time budget caps the plan and the
        window is sampled evenly instead — the same honest degradation, chosen deliberately.

        And the step is SNAPPED: `step` is ceil(want/per), so being a handful of frames over
        budget jumped it from 2 to 3 and threw away a third of the RAM. Overshooting the
        budget by a few percent to halve the sampling gap is always the better trade."""
        n_tracks = max(1, len(lists))
        per_frame = self._proxy_frame_bytes(lists)
        ram_frames = int(PROXY_RAM_BUDGET_MB * 1024 * 1024 / max(1, per_frame))
        time_frames = int(PROXY_SWEEP_BUDGET_S * PROXY_SWEEP_FPS_EST)
        total = max(n_tracks * 60, min(ram_frames, time_frames, PROXY_MAX_FRAMES))
        # Never plan more than the window actually holds — a small window should be
        # preloaded completely, not padded.
        want = max((len(it) for it in lists), default=0)
        per = max(60, min(total // n_tracks, want))
        if want <= per:
            return per
        k = max(1, math.ceil(want / per))
        while k > 1 and want / (k - 1) <= per * (1.0 + PROXY_STEP_SNAP_SLACK):
            k -= 1
        return max(60, math.ceil(want / k))

    def _proxy_item_lists(self) -> list:
        """One item list per preview track: per camera in multi-cam, else the
        single-camera timeline."""
        if self._is_multi_cam() and self._cam_items:
            return self._cam_items
        return [self.items] if self.items else []

    def _proxy_start(self):
        """(Re)build the preview for the frames currently loaded.

        Incremental: already-decoded frames are kept, so a Refresh or the
        live-cap backfill only pays for the frames it actually added."""
        self._proxy_topup.stop()
        self._proxy_resume.stop()
        if not self._proxy_enabled():
            self._proxy_cancel(drop=True)
            return
        lists = self._proxy_item_lists()
        if not lists:
            self._proxy_cancel(drop=True)
            return
        # Stop the running sweep but keep what it already decoded.
        self._proxy_stop.set()
        self._proxy_pool.clear()
        self._proxy_stop = threading.Event()
        self._proxy_gen += 1
        self._proxy_inflight = 0
        self._proxy_rr = -1
        self._proxy_holds = 0
        old = self._proxy_tracks if len(self._proxy_tracks) == len(lists) else []
        budget = self._proxy_budget_per_track(lists)
        # _proxy_side depends on the camera COUNT, and a track is reused whenever the count
        # matches — but the count also changes the side, so a reused track can be holding
        # frames decoded at the wrong size. They would render at the wrong scale, so drop
        # them; the sweep re-reads at the new size.
        side_now = self._proxy_side()
        self._proxy_tracks = []
        self._proxy_total = 0
        self._proxy_done  = 0
        for i, items in enumerate(lists):
            tr = old[i] if old else _ProxyTrack()
            if tr.side and tr.side != side_now:
                tr.frames.clear()
                tr._sorted = []
                tr._dirty = True
                tr._cur_gap = 0
                self._proxy_render_cache.clear()
            tr.side = side_now
            tr.planned, tr.ts_gap, tr.step = _proxy_plan(items, budget)
            tr.pos = 0
            # The plan is recomputed for the grown item list, so indices from the
            # previous one mean nothing. Frames are kept (keyed by ts_ns); only the
            # "already dispatched" bookkeeping starts over.
            tr.taken = set()
            tr.focus_g0, tr.focus_d = -1, 0
            tr.failed = 0
            self._proxy_tracks.append(tr)
            self._proxy_total += len(tr.planned)
        # Seed the focus point BEFORE the first pump. _proxy_focus_jobs returns nothing
        # while _proxy_cursor_ts_ns is 0, and that is only written by _set_info_for /
        # _per_cam_display_one — i.e. by a DISPLAY, which on a freshly opened window has
        # not happened yet. So the opening seconds of every sweep, the ones a user who
        # opens a folder and immediately drags depends on, got no cursor-first ordering
        # at all and filled the far end of the window instead.
        if not self._proxy_cursor_ts_ns:
            try:
                if self.items and self.current_idx is not None:
                    i = max(0, min(int(self.current_idx), len(self.items) - 1))
                    self._proxy_cursor_ts_ns = self.items[i].ts_ns
                elif self.items:
                    self._proxy_cursor_ts_ns = self.items[-1].ts_ns
            except Exception:
                pass
        self._proxy_pump()
        self._proxy_update_status()

    def _proxy_cancel(self, drop: bool = False):
        """Stop building. `drop` also frees the decoded frames — used when live
        mode takes over or a different folder is opened."""
        self._proxy_topup.stop()
        self._proxy_resume.stop()
        self._proxy_stop.set()
        self._proxy_pool.clear()
        self._proxy_gen += 1
        self._proxy_inflight = 0
        self._proxy_total = 0
        self._proxy_done  = 0
        if drop:
            self._proxy_tracks = []
            self._proxy_render_cache.clear()
        self._proxy_update_status()

    def _proxy_kick(self, delay_ms: int = 600):
        """Start the preload shortly after the caller's own display load, so the
        first frame the user is waiting for is not queued behind the sweep."""
        # Keep _proxy_sync_enabled's shadow accurate here rather than only in that
        # method: live mode and auto-follow call this directly, and a shadow that drifted
        # from reality turned the next _proxy_sync_enabled() into a silent no-op.
        self._proxy_was_enabled = self._proxy_enabled()
        if self._proxy_was_enabled:
            self._proxy_topup.start(delay_ms)
        else:
            # Stop building but KEEP the decoded frames. This used to drop them, so
            # ticking Subtraction (or a brief live-mode detour) threw away a sweep that
            # had cost minutes of share reads and forced a full re-sweep afterwards.
            # Only a genuinely new dataset drops them — those call sites pass drop=True
            # themselves (_start_online_mode, _hard_reset_runtime).
            self._proxy_cancel()

    def _proxy_sync_enabled(self):
        """Re-evaluate whether the preview may run, acting only when the answer actually
        changed. Callers fire per spinbox step, and an unconditional kick would bump
        _proxy_gen and discard every in-flight batch on each one."""
        if self._proxy_enabled() == self._proxy_was_enabled:
            return
        self._proxy_kick()   # updates the shadow itself

    def _proxy_schedule_topup(self):
        """New frames arrived with live mode off (Refresh) — extend the preview,
        debounced so a burst of arrivals restarts the sweep only once."""
        if self._proxy_enabled() and self._proxy_tracks:
            self._proxy_topup.start(PROXY_TOPUP_MS)

    def _proxy_focus_jobs(self, tr, items, want: int) -> list:
        """Undecoded plan frames closest to the moment the user is looking at.

        The global coarse→fine order treats the whole window as equally urgent, so the
        stretch under the slider was filled at 1/N of the sweep's rate and a drag into a
        fresh part of the window had nothing to paint from for minutes. Frames are picked
        outwards from the cursor on the plan's own grid, so this only re-orders the plan —
        the same frames are read, just the useful ones first."""
        cur_ts = self._proxy_cursor_ts_ns
        if not cur_ts or not items or not tr.planned:
            return []
        step = max(1, tr.step)
        n = len(items)
        # Cursor → item index → nearest grid point of the plan.
        lo, hi = 0, n - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if items[mid].ts_ns < cur_ts:
                lo = mid + 1
            else:
                hi = mid
        g0 = lo // step
        # Keep the OLD centre while the cursor is still inside the stretch this scan has
        # already cleared: everything in there is known to be taken, so re-centring only
        # buys a re-walk of thousands of taken grid points — once per scrub tick, which is
        # exactly when the GUI thread cannot afford it. Re-centre when the cursor leaves.
        # (focus_g0 starts at -1 with focus_d 0, so the first call always centres.)
        if abs(g0 - tr.focus_g0) > tr.focus_d:
            tr.focus_g0, tr.focus_d = g0, 0
        g0 = tr.focus_g0
        jobs = []
        d = tr.focus_d
        scanned = 0
        while d < PROXY_FOCUS_GRID:
            if scanned >= PROXY_FOCUS_SCAN_MAX:
                tr.focus_d = d      # resume here next call; the plan walk covers the rest
                return jobs
            scanned += 1
            for idx in ((g0 * step,) if d == 0
                        else ((g0 + d) * step, (g0 - d) * step)):
                if not (0 <= idx < n) or idx in tr.taken:
                    continue
                ts_ns = items[idx].ts_ns
                if ts_ns in tr.frames:
                    continue
                tr.taken.add(idx)
                jobs.append((ts_ns, items[idx].path))
                if len(jobs) >= want:
                    tr.focus_d = d
                    return jobs
            d += 1
        tr.focus_d = d
        return jobs

    def _proxy_next_batch(self):
        """Next (track index, [(ts_ns, path), …]) batch to decode, round-robin over
        the tracks so every camera's preview fills in at the same rate."""
        n_tracks = len(self._proxy_tracks)
        if n_tracks == 0:
            return None
        lists = self._proxy_item_lists()
        if len(lists) != n_tracks:
            # The camera set changed under the sweep. Just returning strands it: with
            # nothing dispatched and nothing in flight, no path re-arms _proxy_resume, so
            # the sweep never restarts and "Preloading preview… NN %" sticks forever.
            self._proxy_kick(200)
            return None
        for _ in range(n_tracks):
            self._proxy_rr = (self._proxy_rr + 1) % n_tracks
            tr    = self._proxy_tracks[self._proxy_rr]
            items = lists[self._proxy_rr]
            if tr.pos >= len(tr.planned):
                continue
            # Alternate cursor-first with the global coarse→fine walk. Cursor-first alone
            # fills the neighbourhood at FULL plan density before it ever touches the far
            # end of the window — so a drag across the WHOLE window (which is what users
            # do) still found nothing to paint out there. Alternating keeps a usable
            # coarse layer growing everywhere while the stretch under the handle gets
            # dense; whichever source is exhausted simply yields to the other.
            # The counter must live on the TRACK. A single counter for the whole sweep
            # advanced in lockstep with _proxy_rr — this loop returns on the first track
            # that yields work — so with an EVEN number of cameras the parity welded
            # itself to the camera index: half the cameras only ever got cursor-first
            # batches and never advanced tr.pos at all. Measured on 4 cameras: cam1/cam3
            # 21.2 paints/s and no refusals, cam0/cam2 6.9 paints/s with ~360 refusals and
            # 200 share reads each, because their decoded frames were one dense island
            # 988 s wide in a 7200 s window — and current_gap(), being the mean spacing of
            # the decoded set, then reported a 3.0 s tolerance while the frames actually
            # wanted were a median 4.2 s away. Per track: all four at 21.1, no refusals.
            #
            # WHILE MOVING, though, the alternation is wrong: only frames within a second
            # or two of the handle can be painted in the next few hundred ms, so half the
            # reads went somewhere that could not help. The coarse layer out in the rest of
            # the window is what the IDLE sweep is for. So: strict cursor-first during a
            # drag or playback, alternation when idle — and if the neighbourhood is already
            # exhausted, fall through to the plan walk, which preserves the property that
            # whichever source runs dry yields to the other (and with it the per-track
            # parity fix above).
            if self._proxy_is_moving():
                jobs = self._proxy_focus_jobs(tr, items, PROXY_FOCUS_BATCH)
                if jobs:
                    return self._proxy_rr, jobs
            else:
                tr.focus_turn += 1
                jobs = (self._proxy_focus_jobs(tr, items, PROXY_BATCH)
                        if tr.focus_turn & 1 else [])
            while tr.pos < len(tr.planned) and len(jobs) < PROXY_BATCH:
                idx = tr.planned[tr.pos]
                tr.pos += 1
                if idx >= len(items):
                    self._proxy_done += 1
                    continue
                if idx in tr.taken:
                    continue   # dispatched by the cursor-first pass; counted on arrival
                ts_ns = items[idx].ts_ns
                if ts_ns in tr.frames:
                    self._proxy_done += 1
                    continue
                tr.taken.add(idx)
                jobs.append((ts_ns, items[idx].path))
            if jobs:
                return self._proxy_rr, jobs
        return None

    def _proxy_is_moving(self) -> bool:
        """The user is actively moving through frames, by any of the three routes.

        One predicate so _proxy_pump, _proxy_next_batch and _proxy_motion_tol cannot
        disagree about it — they each open-coded this test, and the per-camera sliders
        were missing from some of them."""
        return bool(self._is_scrubbing or self._is_playing
                    or self._per_cam_scrubbing_cam >= 0)

    def _proxy_idle_grace(self):
        """A drag / playback just ENDED — hold the full-speed sweep for a moment so the
        frame the user landed on wins the first reads.

        Called only from the interaction edges (_on_slider_released,
        _on_per_cam_released, stop()). _proxy_pump used to set this itself on every
        60 ms tick of an ongoing drag, which meant the deadline was never reached."""
        self._proxy_grace_until = time.monotonic() + PROXY_IDLE_GRACE_S
        self._proxy_resume.start(max(50, int(PROXY_IDLE_GRACE_S * 1000)))

    def _proxy_pump(self):
        if not self._proxy_tracks or self._proxy_stop.is_set():
            return
        # Hold the sweep whenever the user is waiting on the share: dragging, playing
        # back, or simply with a display load still in flight. Its readers hit the SAME
        # share as the frame under the cursor, so letting PROXY_WORKERS of them run
        # through a drag is what made positions the preview had not reached yet take
        # seconds to appear — and letting them run through playback is why Play looked
        # frozen while the file names kept scrolling.
        #
        # Re-armed on a timer rather than only from _on_slider_released: an in-flight
        # display load has no "released" event to resume from, so without this the sweep
        # would stall for good the first time it was held.
        # Dragging / playing back: yield unconditionally and for as long as it lasts, and
        # do NOT count these holds. Counting them meant a 60 s playback left the counter
        # at ~150, so the escape hatch below was already spent the instant playback
        # stopped — and it fired while the frame the user landed on was still loading,
        # queueing six fresh preview reads in front of it. Exactly backwards.
        #
        # Standing down COMPLETELY was wrong, though: with the sweep stopped the drag has
        # nothing but one blocking share read per camera to paint from — the "one image a
        # second" case — and the preview it is waiting for never gets built, because the
        # user drags in bursts and the grace below eats what is left. Since the sweep now
        # reads the cursor's neighbourhood first (_proxy_focus_jobs), those reads ARE the
        # frames the drag is about to need, so a couple of them are allowed to run.
        if self._proxy_is_moving():
            self._proxy_holds = 0
            # The grace is deliberately NOT refreshed here. It used to be, which made it
            # unreachable: re-armed every 60 ms for the whole gesture, a user dragging in
            # bursts (i.e. any user) never accumulated PROXY_IDLE_GRACE_S of quiet, so the
            # full-speed sweep never ran and _on_slider_released's "let it run" pump found
            # 1.5 s still on the clock and simply re-armed the wait. The grace now starts
            # at the interaction EDGES — _on_slider_released, _on_per_cam_released, stop()
            # — which is the "measured from the END of the interaction" it always claimed.
            self._proxy_dispatch(PROXY_DRAG_WORKERS)
            self._proxy_resume.start(PROXY_DRAG_MS)
            return
        grace_left = self._proxy_grace_until - time.monotonic()
        if grace_left > 0:
            self._proxy_resume.start(max(50, int(grace_left * 1000)))
            return
        # An in-flight display load is only advisory: this app has a history of keys
        # stranded in _inflight (a task pulled from the pool queue never emits), and a
        # stranded key must not mean the preview never builds again. So yielding to it is
        # capped — after PROXY_HOLD_MAX consecutive holds the sweep proceeds anyway.
        if self._inflight and self._proxy_holds < PROXY_HOLD_MAX:
            self._proxy_holds += 1
            self._proxy_resume.start(PROXY_HOLD_MS)
            return
        self._proxy_holds = 0
        # One batch per worker plus a small spare, so workers do not idle from finishing a
        # batch until the GUI thread gets round to _on_proxy_batch — but no more than that:
        # a deep backlog is share bandwidth already committed to the sweep, and it is spent
        # after the user grabs the slider, when the hold above can no longer help.
        self._proxy_dispatch(PROXY_WORKERS + PROXY_QUEUE_SPARE)

    def _proxy_dispatch(self, limit: int):
        """Keep up to `limit` preview reads in flight."""
        gen = self._proxy_gen
        while self._proxy_inflight < limit:
            job = self._proxy_next_batch()
            if job is None:
                break
            track_i, jobs = job
            self._proxy_inflight += 1
            self._proxy_pool.start(_ProxyTask(gen, track_i, jobs,
                                              self._proxy_signals, self._proxy_stop,
                                              side=self._proxy_side()))

    def _on_proxy_batch(self, gen: int, track_i: int, results: list):
        if gen != self._proxy_gen:
            return
        self._proxy_inflight = max(0, self._proxy_inflight - 1)
        if 0 <= track_i < len(self._proxy_tracks):
            tr = self._proxy_tracks[track_i]
            for ts_ns, arr in results:
                self._proxy_done += 1
                if arr is not None:
                    tr.add(ts_ns, arr)
                else:
                    tr.failed += 1   # see _ProxyTrack.failed / _proxy_covered_track
        self._proxy_update_status()
        self._proxy_pump()

    def _proxy_status_text(self) -> str:
        if not self._proxy_total or self._proxy_done >= self._proxy_total:
            return ""
        pct = int(100 * self._proxy_done / self._proxy_total)
        return f"Preloading preview… {pct} %"

    def _proxy_update_status(self):
        if hasattr(self, "lbl_meta_status"):
            self.lbl_meta_status.setText(self._proxy_status_text())

    def _proxy_covered(self) -> bool:
        """True once the sweep has actually decoded (nearly) the whole window.

        _proxy_tracks is filled by _proxy_start BEFORE a single frame is read, so
        "tracks exist" said nothing about whether the preview can carry a drag. Every
        caller that used _proxy_usable as "the preview will repaint this drag from
        memory" was therefore wrong for the entire sweep: _current_decode_side kept the
        latched 900 px instead of dropping to 320 on a fast drag, and _apply_scrub
        applied its tight 3-load cap — while _proxy_try_paint still refused every
        position. Worst of both worlds, and exactly the "slider is stuck" report.

        Measured on the DECODED frames, not on the sweep's progress counters: those are
        reset by every _proxy_start (Refresh, top-up) and only advance while the pump is
        running, which it is not during a drag or playback — so a fully built preview
        would have read as "not covered" exactly when it was needed."""
        tracks = [t for t in self._proxy_tracks if t.planned]
        if not tracks:
            return False
        # A FRACTION of the tracks, not all of them. Requiring every camera meant one
        # camera with a handful of unreadable files (or simply the slowest share folder)
        # held the entire viewer in the not-covered state — which latches the big scrub
        # renders and _apply_scrub's tight load cap for every OTHER camera too. That is
        # the worst-of-both-worlds state this gate exists to avoid, reached by the gate
        # itself.
        ok = sum(1 for i, t in enumerate(self._proxy_tracks) if t.planned
                 and self._proxy_covered_track(i))
        return ok >= max(1, int(PROXY_COVERED_TRACK_FRAC * len(tracks)))

    def _proxy_covered_track(self, i: int) -> bool:
        """Whether ONE track's preview is built. Single-cam callers want this directly
        (there is only ever track 0), and _proxy_covered aggregates it."""
        if not (0 <= i < len(self._proxy_tracks)):
            return False
        tr = self._proxy_tracks[i]
        if not tr.planned:
            return True
        # The plan cursor first. tr.frames is keyed by ts_ns and never pruned, while
        # _proxy_start recomputes tr.planned for the grown item list — and because
        # _proxy_plan's step is ceil(n/budget), len(planned) SHRINKS each time n
        # crosses a multiple of the budget (n=1990 → 996 planned, n=2010 → 671). So
        # after a Refresh, frames left over from the previous plan could outnumber
        # 98 % of the new one while the new plan was barely started: covered() said
        # yes, _proxy_try_paint refused every one of the newly added frames, and
        # dragging into them — which is where users go after a Refresh — landed back
        # in the tight-cap/latched-size state this gate exists to avoid.
        # tr.pos is reset by _proxy_start and never regresses, and _proxy_next_batch
        # skips already-decoded entries without any I/O, so it catches up at once.
        if tr.pos < len(tr.planned):
            return False
        # A fraction, not an exact count: a frame that fails to decode (a file caught
        # mid-write, a truncated PNG) never lands in tr.frames. Those are DISCOUNTED
        # rather than merely tolerated — with a flat 98 % a camera whose folder holds
        # 3 % half-written files could never be covered, however long the sweep ran.
        reachable = max(1, len(tr.planned) - tr.failed)
        return len(tr.frames) >= PROXY_COVERED_FRAC * reachable

    def _proxy_usable(self) -> bool:
        """Preview frames are raw grayscale at PROXY_MAX_SIDE, so they cannot stand
        in when the render depends on full-size pixels (subtraction) or when the
        user is zoomed in and would just see a blurry crop."""
        if not self._proxy_tracks or not self._proxy_enabled():
            return False
        if self.cb_subtract.isChecked():
            return False
        return True

    def _proxy_motion_tol(self, track_i: int) -> int:
        """How far from the asked-for moment a preview frame may be RIGHT NOW.

        While the user is moving, whatever the preview already holds beats a frozen
        picture: the alternative is one blocking share read per camera, i.e. a frame a
        second. So the accepted distance follows the density decoded so far and tightens
        with every pass of the sweep — the finished-plan ts_gap only applied once the
        sweep was practically complete, which is why dragging through a window that was
        still preloading painted nothing at all.

        Standing still keeps the strict gap: there is no motion to carry, the exact
        frame is worth waiting for, and _refine_current_frame fetches it anyway."""
        if not self._proxy_is_moving():
            return 0
        if not (0 <= track_i < len(self._proxy_tracks)):
            return 0
        tr = self._proxy_tracks[track_i]
        return min(tr.current_gap(),
                   PROXY_MOTION_TOL_MAX * max(1, tr.ts_gap),
                   int(PROXY_MOTION_TOL_MAX_S * 1e9))

    def _proxy_render(self, track_i: int, ts_ns: int, arr,
                      brighten: int, bc, gradient_id: int) -> "QPixmap | None":
        """Apply the current render settings to one small preview array."""
        key = ("proxy", track_i, ts_ns, brighten, bc, gradient_id)
        pm = self._proxy_render_cache.get(key)
        if pm is not None and not pm.isNull():
            return pm
        lo, hi, mx = 0.0, _FULL_SCALE_16, _FULL_SCALE_16
        if isinstance(arr, tuple):
            arr, lo, hi, mx = arr
        if arr is None or arr.ndim != 2 or arr.size == 0:
            return None
        h, w = arr.shape
        if arr.dtype == np.uint16:
            # Legacy / PROXY_STORE_8BIT = False path. Same two mappings, in the same order,
            # as load_image_scaled's 16-bit branch — so a preview paint and the refined
            # render of the same frame cannot disagree about the tones.
            if brighten:
                arr8 = _stretch_arr_f(arr.astype(np.float32))
            else:
                arr8 = _norm16_to8_full_scale(arr)
            img = QImage(arr8.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
        elif hi > 255.0:
            # 8-bit storage of a 16-bit source. The stored codes span p0.1..p99.9 of the
            # real 16-bit pixels (load_proxy_gray took those percentiles in the worker), so
            # `orig = lo + code * (hi - lo) / 255` recovers the 16-bit value to within one
            # code. Both render modes are then a 256-entry LUT over that:
            #   Auto ON  → the p0.5..p99.5 stretch _stretch_arr_f would have applied.
            #   Auto OFF → the absolute full-scale mapping _norm16_to8_full_scale applies.
            # Same two mappings, in the same order, as load_image_scaled's 16-bit branch, so
            # a preview paint and the refined render of the same frame agree.
            #
            # Doing it as a LUT is also what took ~1 ms per tile per tick off the GUI
            # thread: the percentile pass used to run on every render-cache miss, i.e. on
            # every new position, which at 12 cameras was ~12 ms of a 33 ms budget spent
            # recomputing a constant.
            # Invert load_proxy_gray's two-segment ramp: 0..KNEE is lo..hi, KNEE+1..255 is
            # hi..mx. One straight line through all 256 would put the highlight tail on the
            # picture's scale and render every saturated pixel at the p99.9 level.
            orig = np.empty(256, dtype=np.float32)
            k = PROXY_KNEE_CODE
            orig[:k + 1] = lo + np.arange(k + 1, dtype=np.float32) * ((hi - lo) / k)
            n_tail = 255 - k
            if mx > hi and n_tail > 0:
                orig[k + 1:] = hi + (np.arange(1, n_tail + 1, dtype=np.float32)
                                     * ((mx - hi) / n_tail))
            else:
                orig[k + 1:] = hi
            if brighten:
                # p0.5/p99.5 of the STORED distribution, weighted by how many pixels sit at
                # each code — a plain percentile of 0..255 would describe the LUT, not the
                # picture.
                counts = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
                cum = np.cumsum(counts)
                total = cum[-1] if cum[-1] > 0 else 1.0
                i_lo = int(np.searchsorted(cum, 0.005 * total))
                i_hi = int(np.searchsorted(cum, 0.995 * total))
                s_lo, s_hi = orig[min(i_lo, 255)], orig[min(i_hi, 255)]
                if s_hi <= s_lo:
                    s_lo, s_hi = orig[0], orig[255]
                if s_hi <= s_lo:
                    lut = np.zeros(256, dtype=np.uint8)
                else:
                    lut = np.clip((orig - s_lo) / (s_hi - s_lo) * 255.0,
                                  0, 255).astype(np.uint8)
            else:
                lut = np.clip(orig * (255.0 / _FULL_SCALE_16), 0, 255).astype(np.uint8)
            arr8 = lut[arr]
            img = QImage(arr8.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
        else:
            img = QImage(arr.tobytes(), w, h, w, QImage.Format.Format_Grayscale8).copy()
            if brighten:
                img = _apply_stretch(img)
        img = _apply_bc(img, contrast=bc.contrast, auto_bright=bc.auto, offset=bc.offset)
        if gradient_id >= 2:
            img = _apply_lut(img, GRADIENTS[GRADIENT_NAMES[gradient_id]])
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return None
        self._proxy_render_cache.put(key, pm)
        return pm

    def _proxy_try_paint(self, idx: int) -> bool:
        """Paint the preloaded preview nearest to item `idx`. True means the view
        was updated and the caller can skip the full-size load entirely."""
        if not self._proxy_usable() or not self.items:
            return False
        # Track 0 is camera 0 in multi-cam, not the merged timeline — the tiles go
        # through _proxy_try_paint_cam instead.
        if self._is_multi_cam():
            return False
        if getattr(self.img_view, "_zoom_norm", None) is not None:
            return False
        if not (0 <= idx < len(self.items)):
            return False
        # Same single tolerance as _proxy_try_paint_cam — see the comment there.
        tr  = self._proxy_tracks[0]
        want_ts = self.items[idx].ts_ns
        tol = max(tr.ts_gap, self._proxy_motion_tol(0))
        got = tr.nearest(want_ts, tol)
        if got is None:
            self._diag_miss += 1
            return False
        ts_ns, arr = got
        pm = self._proxy_render(0, ts_ns, arr,
                                1 if self.cb_bright.isChecked() else 0,
                                self._bc(), self.gradient_cb.currentIndex())
        if pm is None:
            self._diag_miss += 1
            return False
        self.img_view.set_pixmap(pm)
        self._diag_prev += 1
        # Say which frame this actually is. _set_info_for already wrote the readouts from
        # the REQUESTED index (it runs before every call to this method), and the preview
        # is sampled, so without this single-cam quietly claimed a moment it was not
        # showing — exactly the bug _cam_note_painted was added to fix for the tiles.
        if ts_ns != want_ts:
            self._set_info_painted(ts_ns)
        return True

    def _proxy_try_paint_cam(self, cam_i: int, cam_frame_idx: int) -> bool:
        """Same for one multi-cam tile."""
        if not self._proxy_usable() or cam_i >= len(self._proxy_tracks):
            return False
        cam_items = self._cam_items[cam_i] if cam_i < len(self._cam_items) else []
        if not (0 <= cam_frame_idx < len(cam_items)):
            return False
        iv = self._multi_grid.get_img_view(cam_i)
        if iv is None or getattr(iv, "_zoom_norm", None) is not None:
            return False
        # ONE tolerance, computed once and used for BOTH the accept and the verdict.
        # These were two different numbers: nearest() accepted anything within
        # _proxy_motion_tol (minutes, on a plan that is still coarse) and the paint was
        # then judged against tr.ts_gap (seconds). Every substituted neighbour therefore
        # came out "stale", so during any drag over a partly-preloaded window every tile
        # sat red and the colour carried no information at all. `nearest` widens whatever
        # it is given to max(ts_gap, tol) — mirror that exactly.
        tr  = self._proxy_tracks[cam_i]
        tol = max(tr.ts_gap, self._proxy_motion_tol(cam_i))
        got = tr.nearest(cam_items[cam_frame_idx].ts_ns, tol)
        if got is None:
            self._diag_miss += 1
            return False
        ts_ns, arr = got
        enh = self._cam_enhance_on(cam_i)
        pm = self._proxy_render(
            cam_i, ts_ns, arr,
            (1 if self.cb_bright.isChecked() else 0) if enh else 0,
            self._bc() if enh else _RENDER_BC_NONE,
            self.gradient_cb.currentIndex())
        if pm is None:
            self._diag_miss += 1
            return False
        iv.set_pixmap(pm)
        self._diag_prev += 1
        # Label the frame that was actually painted (ts_ns), NOT the one asked for: the
        # preview is sampled, so `nearest` legitimately returns a neighbour, and labelling
        # it with the requested timestamp made the tile claim a moment it was not showing.
        self._cam_note_painted(cam_i, ts_ns, tol_ns=tol, preview=True)
        return True

    def _schedule_refine(self):
        """The view is showing a preview — queue the full-quality re-render for
        when the user settles. Restarting the timer on every move means it only
        fires for the frame actually stopped on, never for ones passed over."""
        self._refine_timer.start(PROXY_REFINE_MS)

    def _refine_current_frame(self):
        """Re-render the frame the user settled on at high quality, so it is at least as
        crisp as it would be without the preview layer.

        REFINE_MAX_SIDE for plain fit-to-window viewing; native when the pixels actually
        matter (zoomed in, or Subtraction on — see the size choice below)."""
        if self._online_mode or self._is_playing or not self.items:
            return
        if self.current_idx is None or not (0 <= self.current_idx < len(self.items)):
            return
        # Still dragging: a native-resolution decode is ~22 MB off the share and would
        # occupy a loader thread the frame under the cursor needs. The single-camera
        # path had no such guard, so the moment a drag left the preloaded range (which
        # stops _schedule_refine from being restarted) the armed timer fired mid-drag.
        # Re-arm and refine once the user has actually settled.
        if self._is_scrubbing or self._per_cam_scrubbing_cam >= 0:
            self._refine_timer.start(PROXY_REFINE_MS)
            return
        if self._is_multi_cam():
            # Tiles are small; their normal per-camera decode size IS the good
            # quality here, and 12 native-res renders would cost gigabytes.
            # Independent per-cam mode: every camera sits on its own time, so
            # re-displaying the merged index would drag them all to one moment.
            if self._per_cam_master_idx < 0 and self._per_cam_rows:
                return
            self._display_multicam_index(self.current_idx, update_slider=False)
            return
        idx = self.current_idx
        # Fit-to-window viewing does not benefit from a 2560×2160 render: the view is at
        # most ~1100 px tall on this hardware, so REFINE_MAX_SIDE already looks identical
        # while costing a quarter of the pixels — and, more to the point, it skips a
        # ~22 MB QPixmap.fromImage on the GUI thread after EVERY slider release, which was
        # a visible hitch that v2.5.5 (no refine pass at all) did not have. Zoomed in the
        # user really is looking at individual pixels, so that case stays native.
        # Subtraction stays native too: _update_diff_stats reports pixel counts, mean and
        # max difference for whatever render is on screen, and downscaling averages pixels
        # — so refining at 1600 would quietly change numbers people write down.
        zoomed = getattr(self.img_view, "_zoom_norm", None) is not None
        side = FULL_RES_SIDE if (zoomed or self.cb_subtract.isChecked()) \
               else REFINE_MAX_SIDE
        brighten    = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        bc          = self._bc()
        ref = self._ref_arr_for(side) if self.cb_subtract.isChecked() else None
        sub_thr, sub_off = self._sub_params(ref)
        key = (self._ck(idx), side, brighten, gradient_id, bc,
               id(ref) if ref is not None else None, sub_thr, sub_off)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self.img_view.set_pixmap(cached)
            self._diag_cach += 1
            self._update_diff_stats(key)
            return
        self._display_req_id += 1
        self._want_display_req[key] = self._display_epoch
        if key not in self._inflight:
            self._inflight.add(key)
            self.load_pool.start(LoadTask(
                self._gen, self._display_req_id, idx, self.items[idx].path,
                side, brighten, gradient_id, self.load_signals,
                bc, ref, sub_thr, sub_off, key=key))

    # ================================================================ SLIDER <-> TIME
    def _slider_to_time_ns(self, v):
        if self.axis_max_ns <= self.axis_min_ns: return self.axis_min_ns
        return self.axis_min_ns + int((self.axis_max_ns - self.axis_min_ns) * (v / SLIDER_MAX))
    
    def _slider_to_index(self, v) -> int:
        if not self.items: return 0
        n = len(self.items)
        return max(0, min(n - 1, int(round(v / SLIDER_MAX * (n - 1)))))

    def _index_to_slider_value(self, idx) -> int:
        n = len(self.items)
        if n <= 1: return 0
        return max(0, min(SLIDER_MAX, int(idx / (n - 1) * SLIDER_MAX)))

    def _time_to_slider_value(self, t):
        if self.axis_max_ns <= self.axis_min_ns:
            return 0
        frac = (t - self.axis_min_ns) / (self.axis_max_ns - self.axis_min_ns)
        return max(0, min(SLIDER_MAX, int(frac * SLIDER_MAX)))

    def _time_to_nearest_index(self, t):
        i = bisect.bisect_left(self.ts_list, t)
        if i <= 0: return 0
        if i >= len(self.ts_list): return len(self.ts_list) - 1
        return i - 1 if (t - self.ts_list[i-1]) <= (self.ts_list[i] - t) else i

    def _set_info_painted(self, ts_ns: int):
        """Correct the single-cam timestamp readouts to the frame ACTUALLY on screen.

        _set_info_for writes them from the REQUESTED index and runs before every paint,
        so when the preview substitutes its nearest preloaded neighbour the readouts named
        a frame that was not being shown. The "~" prefix marks it as the nearest preloaded
        frame rather than the exact one — the single-cam counterpart of the tiles' blue
        "~HH:MM:SS.mmm" label. Only called when the two actually differ; the settle
        re-render (_refine_current_frame) goes back through _set_info_for and clears it.

        The energy text is left as _set_info_for computed it: it belongs to the requested
        shot, and the "~" is what says the picture may be a neighbour of it."""
        if self._is_multi_cam():
            return
        txt = "~" + fmt_prague_full_from_ns(ts_ns)
        energy_text = getattr(self, "_info_energy_text", "") or ""
        self.lbl_prague_time.setText(
            f"Prague: {txt}\n{energy_text}" if energy_text else f"Prague: {txt}")
        self.lbl_date.setText(f"Date: {fmt_prague_date_from_ns(ts_ns)}")
        self.img_view.cam_ts_text = txt
        self.img_view.update()

    def _set_info_for(self, idx, axis_time_ns):
        it = self.items[idx]
        # Every display path comes through here, so this is where the preview sweep
        # learns which part of the window to fill first (_proxy_focus_jobs).
        self._proxy_cursor_ts_ns = it.ts_ns
        if self._is_multi_cam() and self._cam_items:
            self.lbl_filename.setText("")   # scan progress label already shows per-cam counts
            self.lbl_index.setText(f"{idx+1} / {len(self.items)} (merged)")
            self.lbl_meta_status.setText(self._proxy_status_text())
        else:
            self.lbl_filename.setText(f"File: {it.path.name}")
            self.lbl_index.setText(f"{idx+1} / {len(self.items)}")
            # No metadata warning any more: 16-bit frames are normalized on the
            # camera's absolute full scale, which needs no tEXt tag and is always
            # comparable between frames (see _norm16_to8_full_scale). The slot now
            # carries the preview-preload progress, which auto-hides when done.
            self.lbl_meta_status.setText(self._proxy_status_text())
        self.lbl_axis_time.setText(f"Axis: {fmt_hhmmss_ms_from_ns(axis_time_ns)}")
        if self._real_ts_list and idx < len(self._real_ts_list):
            real_ts = self._real_ts_list[idx]
        else:
            real_ts = it.ts_ns
        energy_text = self._sf_energy_map.get(it.path.name, "")
        if energy_text:
            self.lbl_prague_time.setText(
                f"Prague: {fmt_prague_full_from_ns(real_ts)}\n{energy_text}")
        else:
            self.lbl_prague_time.setText(f"Prague: {fmt_prague_full_from_ns(real_ts)}")
        self.lbl_date.setText(f"Date: {fmt_prague_date_from_ns(real_ts)}")
        self.img_view.energy_text = energy_text
        self.img_view.update()

        # Update single-cam label (drawn by paintEvent in reserved strip below image)
        if not self._is_multi_cam():
            cam_name = self._cam_names[0] if self._cam_names else ""
            self.img_view.cam_label_text = _strip_cam_name(cam_name)
            self.img_view.cam_ts_text = fmt_prague_full_from_ns(real_ts)
            self.img_view.update()
        self._info_energy_text = energy_text

        # Live replay: update pointing panel to show only points up to current timestamp.
        # Skip when navigating by clicking a graph point (would just redraw what's already shown).
        # Throttle during online auto-follow to avoid expensive mpl redraw on every frame.
        if (self.pointing_panel.isVisible() and self.pointing_panel._ts_int is not None
                and not getattr(self, '_pointing_nav_from_click', False)):
            if self._auto_follow:
                now_ms = int(time.time() * 1000)
                last = getattr(self, '_pointing_replay_last_ms', 0)
                if now_ms - last >= 500:
                    self._pointing_replay_last_ms = now_ms
                    self.pointing_panel.set_replay_ts(it.ts_ns)
            else:
                self.pointing_panel.set_replay_ts(it.ts_ns)

    # ================================================================ SCAN
    def _hard_reset_runtime(self):
        self._proxy_cancel(drop=True)
        self._refine_timer.stop()
        for t in [self.play_timer, self.scrub_timer, self._prefetch_debounce,
                  self._nav_timer]:
            try:
                if t.isActive(): t.stop()
            except: pass
        self._is_playing = False; self._is_scrubbing = False; self.pending_slider = None
        # Keyed by camera index, and the camera set is about to change (see
        # _build_per_cam_sliders).
        self._nav_pending.clear(); self._nav_frame.clear(); self._nav_cursor_ts = 0
        # Both scrub flags gate _proxy_pump and _refine_current_frame; leaving this one
        # set would stall the preview sweep and the full-res refine with nothing able to
        # resume either.
        self._per_cam_scrubbing_cam = -1
        self.play_time_ns = None; self.target_idx = None
        self._display_load_key = None; self._deferred_display = None
        self._reset_motion_tracking()

    def _reset_ui_for_new_scan(self, folders, folder_label):
        self._stop_online_mode()
        self._hard_reset_runtime()
        if hasattr(self, '_pointing_replay_timer') and self._pointing_replay_timer.isActive():
            self._pointing_replay_timer.stop()
        self.opened_folders = folders[:]; self.opened_folder = folders[0] if folders else None
        self.items = []; 
        self._real_ts_list = []
        self._fake_ts_map = None
        if hasattr(self, 'tickbar'):
            self.tickbar.discrete_ticks = None
            self.tickbar.discrete_tick_labels = None
        # _sf_energy_map se neresetuje zde — nastavuje ho sf.py před open_folder_path
        # self._sf_energy_map = {}
        self.ts_list = []; self.axis_min_ns = 0; self.axis_max_ns = 0
        self.current_idx = None
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0")
        self.lbl_date.setText("Date: —")
        self.cache = PixCache(CACHE_SIZE); self._display_req_id = 0
        self._inflight.clear(); self._want_display_req.clear()
        self.img_view.cam_label_text = ""
        self.img_view.cam_ts_text = ""
        # A new scan means the old reference frame belongs to a different dataset —
        # drop it exactly like setup_multi_cam drops the per-camera references, so the
        # badge and the actual subtraction reference can never disagree.
        self._ref_path = None
        self._ref_scaled = {}
        self._cam_diff_stats = {}
        self.img_view.set_cam_ref_text("")
        if hasattr(self, "lbl_ref_status"):
            # Subtraction may still be checked from the previous dataset — keep the
            # "no reference" warning visible instead of a blank line.
            self._refresh_ref_warning()
        if hasattr(self, "lbl_diff_stats"):
            self.lbl_diff_stats.setText("")
        self.img_view.clear()
        # Clear stale Spatial Contrast preview/overlay from whatever camera was
        # shown before — it belongs to that old camera, not the one being scanned in.
        if hasattr(self, '_sc_preview_lbl'):
            self._sc_preview_lbl.hide()
            self._sc_preview_pixmap = None
        if hasattr(self, '_sc_exclusion_mask'):
            self._sc_exclusion_mask = None
            self._sc_exclusion_path = None
        if hasattr(self, 'img_view'):
            self.img_view.sc_topn_points_norm = None
        for w in [self.slider, self.btn_save, self.btn_save_range,
                self.btn_play, self.btn_stop, self.btn_prev, self.btn_next, self.btn_set_a,
                self.btn_set_b, self.btn_clear_marks, self.btn_cal_circle, self.btn_cal_square,
                self.btn_cal_cross, self.btn_pointing, self.btn_save_ts, self.btn_goto_ts,
                self.btn_set_ref]:
            w.setEnabled(False)
        # Preserve marks across rescan — they are revalidated against the new axis at scan-done time
        # (do not clear mark_a_ns / mark_b_ns here)
        self.tickbar.set_marks(None, None)
        fname = folders[0].name if len(folders) == 1 else (f"{folders[0].name} → {folders[-1].name}" if folders else "Multi-camera")
        self.lbl_filename.setText(f"File: {fname}  (scanning…)")
        self.lbl_prague_time.setText("Prague: —"); self.lbl_axis_time.setText("Axis: —")
        self.lbl_index.setText("0 / 0"); self.tickbar.set_axis(0, 0)
        self.prog.setVisible(True); self.prog.setRange(0, 0)
        self.lbl_scan_progress.setText("Scanning..."); self.btn_cancel_scan.setVisible(True)

    def _start_scan(self, folders, axis_override, folder_label):
        # _start_scan is the SINGLE-camera scan path — clear stale multi-cam
        # state here, or a previous multi session's _cam_folder_lists makes
        # _start_online_mode arm watchers on dead folders and _is_multi_cam()
        # can stay wrongly True (leftover _cam_names).
        if len(self._cam_names) > 1:
            self._cam_names = self._cam_names[:1]
        self._cam_folder_lists = []
        self._cam_folders = []
        self._cam_items = []
        self._cam_ts = []
        self._cam_poll_max_ts = []
        self._gen += 1; gen = self._gen
        if self._scan_task is not None:
            self._scan_task.cancel(); self._scan_task = None
        self.axis_override = axis_override
        # Uložit overlay stav single-cam img_view před resetem UI
        _saved_overlay = MultiCameraGrid._save_iv_overlay(self.img_view)
        self._reset_ui_for_new_scan(folders, folder_label)
        # Obnovit overlay stav po resetu (clear() vymaže obrázek, ne overlay data)
        MultiCameraGrid._restore_iv_overlay(self.img_view, _saved_overlay)
        task = ScanTask(gen, folders); self._scan_task = task
        task.signals.status.connect(self._on_scan_status)
        task.signals.progress.connect(self._on_scan_progress)
        task.signals.cancelled.connect(self._on_scan_cancelled)
        task.signals.quick_item.connect(self._on_scan_quick_item)
        task.signals.finished.connect(self._on_scan_finished)
        self.scan_pool.start(task)

    def cancel_scan(self):
        if self._scan_task is not None: self._scan_task.cancel()

    def _on_scan_quick_item(self, gen, item):
        """Show newest image found so far before the full scan finishes."""
        if gen != self._gen: return
        if self.current_idx is not None: return  # already displaying something
        if not self._in_ts_windows(item.ts_ns): return   # outside the picked window
        # Minimal setup so we can decode and show this single item
        self.items = [item]
        self.ts_list = [item.ts_ns]
        self.current_idx = 0
        self._quick_item_shown = True
        self._display_req_id += 1
        req_id = self._display_req_id
        max_side = self._current_decode_side()
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        bc = self._bc()
        key = (0, max_side, brighten, gradient_id, bc, None, 0, 0)
        self._want_display_req[key] = self._display_epoch
        self._inflight.add(key)
        task = LoadTask(gen, req_id, 0, item.path, max_side, brighten, gradient_id,
                        self.load_signals, bc, key=key)
        self.load_pool.start(task)

    def _on_scan_status(self, gen, text):
        if gen != self._gen: return
        self.lbl_filename.setText(text)

    def _on_scan_progress(self, gen, folder_i, total_folders, processed, found):
        if gen != self._gen: return
        self.lbl_scan_progress.setText(f"Folder {folder_i}/{total_folders} | Files: {processed} | Imgs: {found}")

    def _on_scan_cancelled(self, gen):
        if gen != self._gen: return
        self._scan_task = None; self.prog.setVisible(False)
        self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)
        self.lbl_filename.setText("File: scan cancelled.")

    def _in_ts_windows(self, ts_ns: int) -> bool:
        """True when the frame belongs to the picked time window(s). Always True
        when no window is set (single-folder / file-list loads)."""
        wins = self._ts_windows
        if not wins:
            return True
        return any(s <= ts_ns < e for s, e in wins)

    def _filter_to_ts_windows(self, items: list) -> list:
        """Drop frames outside the picked windows. Archive folders are hourly, so
        a minute-precise From/To (and a per-day selection) can only be honoured
        here, on the scanned items."""
        if not self._ts_windows:
            return items
        return [it for it in items if self._in_ts_windows(it.ts_ns)]

    def _choose_axis(self, folder_axis, ts_min, ts_max):
        if folder_axis is not None:
            return folder_axis
        # Bez folder_axis — zkontroluj jestli je data span > 4h
        # Pokud ano (např. temp složka se snímky z různých dnů), použij
        # diskrétní osu přímo z timestampů (tight) bez zaokrouhlení na hodiny
        data_span_ns = ts_max - ts_min
        FOUR_HOURS_NS = 4 * 3_600_000_000_000
        if data_span_ns > FOUR_HOURS_NS:
            # Tight osa — malý padding kolem dat
            pad = max(data_span_ns // 20, 60_000_000_000)  # min 1 minuta
            return ts_min - pad, ts_max + pad
        # Normální případ — zaokrouhli na celé hodiny
        dt0 = floor_to_hour(_dt_from_ns(ts_min))
        dt1 = floor_to_hour(_dt_from_ns(ts_max))
        if dt1 == dt0:
            dt1 = dt0 + timedelta(hours=1)
        else:
            dt1 = dt1 + timedelta(hours=1)
        return ns_from_dt(dt0), ns_from_dt(dt1)

    def _on_scan_finished(self, gen, items):
        if gen != self._gen: return
        self._scan_task = None; self.prog.setVisible(False)
        self.lbl_scan_progress.setText(""); self.btn_cancel_scan.setVisible(False)
        self.items = self._filter_to_ts_windows(items)
        if not self.items:
            self.lbl_filename.setText("File: no images found."); self.tickbar.set_axis(0, 0); return
        self.ts_list = [it.ts_ns for it in self.items]
        ts_min, ts_max = self.ts_list[0], self.ts_list[-1]
        n = len(self.items)
        self._scrub_side = 500 if n >= 15000 else (600 if n >= 6000 else 900)
        if self.axis_override is not None:
            self.axis_min_ns, self.axis_max_ns = self.axis_override
            self.tickbar.discrete_ticks = None
            self.tickbar.discrete_tick_labels = None
        else:
            folder_axis = axis_from_any_folder(self.opened_folder) if self.opened_folder else None
            if folder_axis is not None:
                folder_span = folder_axis[1] - folder_axis[0]
                data_span   = ts_max - ts_min
                if n <= 100 and folder_span > data_span * 10:
                    folder_axis = None
            self.axis_min_ns, self.axis_max_ns = self._choose_axis(folder_axis, ts_min, ts_max)
            # Discrete mode: pokud snímků je málo a osa je příliš velká (různé dny),
            # přepni na index-based osu kde každý snímek má stejnou vzdálenost
            if self._discrete_mode and n <= TICKBAR_DISCRETE_MAX:
                self.axis_min_ns = ts_min
                self.axis_max_ns = ts_max
                self.tickbar.discrete_ticks = self.ts_list[:]
                self._fake_ts_map = None
            else:
                self.tickbar.discrete_ticks = None
                self.tickbar.discrete_tick_labels = None
                self._fake_ts_map = None
        self.tickbar.set_axis(self.axis_min_ns, self.axis_max_ns)
        self._apply_marks_to_tickbar()
        self.slider.setEnabled(True); self.btn_save.setEnabled(True); self.btn_play.setEnabled(True)
        self.btn_stop.setEnabled(False); self.btn_prev.setEnabled(True); self.btn_next.setEnabled(True)
        self.btn_set_a.setEnabled(True); self.btn_set_b.setEnabled(True)
        self.btn_clear_marks.setEnabled(True); self.btn_cal_circle.setEnabled(True)
        self.btn_cal_square.setEnabled(True); self.btn_cal_cross.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.btn_send_workshop.setEnabled(True)
        self.btn_pointing.setEnabled(True)
        self.btn_pointing_live.setEnabled(True)
        self._sc_set_enabled(True)
        self._btn_auto_follow.setEnabled(True)
        self._refresh_live_btn_style()
        self.btn_set_ref.setEnabled(True)
        self._update_range_ui(); self._sync_overlay_checkboxes_from_iv(self.img_view); self.img_view.update()
        pending_online = getattr(self, '_pending_online_mode', False)
        if pending_online and self.items:
            last_idx = len(self.items) - 1
            sv = self._time_to_slider_value(self.items[last_idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
            self.play_time_ns = self.items[last_idx].ts_ns; self.target_idx = last_idx
            self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=False)
        else:
            # If quick_item already showed the newest image, land on the newest (last) item
            # so the user sees the latest frame; otherwise start at index 0
            quick_showed = getattr(self, '_quick_item_shown', False)
            self._quick_item_shown = False
            if quick_showed:
                start_idx = len(self.items) - 1
                start_ts  = self.items[start_idx].ts_ns
                sv = self._time_to_slider_value(start_ts)
            else:
                start_idx = 0
                start_ts  = self.axis_min_ns
                sv = 0
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
            self.play_time_ns = start_ts; self.target_idx = start_idx
            self._display_exact_index(start_idx, start_ts, update_slider=False)
        self.btn_save_ts.setEnabled(True)
        self.btn_set_ref.setEnabled(True)
        if self._saved_timestamps:
            self.btn_goto_ts.setEnabled(True)
            self.btn_clear_ts.setEnabled(True)
            self.lbl_ts_status.setText(
                f"{len(self._saved_timestamps)} timestamp(s) saved.")
        if pending_online:
            self._pending_online_mode = False
            self._start_online_mode()
            self._btn_auto_follow.setChecked(True)
            if self.items:
                last_idx = len(self.items) - 1
                self._display_exact_index(last_idx, self.items[last_idx].ts_ns, update_slider=True)

        # A different camera was just scanned in — if Spatial Contrast had a
        # result showing, refresh it now so it reflects the new camera instead
        # of lingering with the previous one's numbers/preview.
        if (hasattr(self, '_sc_val_sc') and self._sc_val_sc.text() not in ("—", "")
                and hasattr(self, '_run_spatial_contrast')):
            self._run_spatial_contrast()

        # Preload the whole window at preview quality (no-op in live mode).
        self._proxy_kick()

    def _overlay_base_pixmap(self) -> "QPixmap | None":
        """Native-resolution render of the current single-cam frame, for the overlay-save
        paths.

        They used to paint onto `img_view._pix` — whatever happened to be on screen. That
        is the scrub render (900 px), the settled refine (REFINE_MAX_SIDE), or, worst
        case, a 224 px preview frame, so the exported *_overlay / *_annotated PNG silently
        inherited the viewing resolution. All overlay geometry is normalised, so it draws
        identically at any size; only the output resolution changes. Falls back to what is
        on screen if the re-read fails, so saving never breaks."""
        on_screen = self.img_view._pix
        if self.current_idx is None or not self.items:
            return on_screen.copy() if on_screen is not None and not on_screen.isNull() else None
        idx = self.current_idx
        if not (0 <= idx < len(self.items)):
            return on_screen.copy() if on_screen is not None and not on_screen.isNull() else None
        brighten = 1 if self.cb_bright.isChecked() else 0
        ref = self._ref_arr_for(FULL_RES_SIDE) if self.cb_subtract.isChecked() else None
        sub_thr, sub_off = self._sub_params(ref)
        key = (self._ck(idx), FULL_RES_SIDE, brighten, self.gradient_cb.currentIndex(), self._bc(),
               id(ref) if ref is not None else None, sub_thr, sub_off)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            return cached.copy()
        bc = self._bc()
        try:
            # Same argument order LoadTask.run uses, so the export matches the render.
            img = load_image_scaled(
                self.items[idx].path, FULL_RES_SIDE, bool(brighten),
                self.gradient_cb.currentIndex(), bc.offset, ref, sub_thr,
                bc.contrast, bc.auto, sub_off)
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img)
                if not pm.isNull():
                    return pm
        except Exception:
            pass
        return on_screen.copy() if on_screen is not None and not on_screen.isNull() else None

    def _prewarm_drag_references(self):
        """Decode the subtraction reference(s) for every size the drag can request, at
        press time, so no scrub tick ever blocks on a share read for a reference.

        Multi-cam gets the same treatment as single-cam: its tiles subtract against
        _cam_ref_arr_for at the per-camera display size, which was stalling the tick
        exactly the same way."""
        if not self.cb_subtract.isChecked():
            return
        if self._is_multi_cam():
            # Every size _cam_tile_side can pick during the drag, for every camera.
            n_cams = len(self._cam_items)
            for side in self._cam_tile_sides(n_cams):
                for cam_i in range(n_cams):
                    self._cam_ref_arr_for(cam_i, side)
            return
        # Both sizes _current_decode_side can return for a single-cam drag.
        self._ref_arr_for(self._drag_side)
        self._ref_arr_for(FAST_SCRUB_MAX_SIDE)

    # ================================================================ SLIDER HANDLERS
    def _on_slider_pressed(self):
        self._is_scrubbing = True; self._prefetch_debounce.stop(); self._reset_motion_tracking()
        # Drop what idle prefetch left queued, BEFORE stop() starts the load for the frame
        # the drag begins on. _prefetch_idle can leave up to 22 keys in _inflight, and they
        # count against _apply_scrub's cap, so the opening second of a drag could hit the
        # cap on every tick and paint nothing — while the labels and the axis cursor kept
        # advancing. _on_slider_released already clears these the same way.
        self.load_pool.clear(); self._inflight.clear(); self._want_display_req.clear()
        # Latch the decode size for the whole drag (see _current_decode_side) BEFORE
        # stop(), which redisplays the current frame and would otherwise read the
        # previous drag's latched size.
        self._drag_side = self._scrub_side
        if self._is_playing: self.stop()
        # Decode the subtraction reference(s) for every size the drag can ask for now:
        # _ref_arr_for caches per max_side and decodes off the share synchronously, so a
        # size that first appeared mid-drag stalled the 33 ms scrub tick on a network read.
        self._prewarm_drag_references()
        self.pending_slider = self.slider.value()
        if not self.scrub_timer.isActive(): self.scrub_timer.start()

    def _on_slider_changed(self, v):
        if not self.items:
            self.pending_slider = v
            return
        t = self._slider_to_time_ns(v)
        idx = self._time_to_nearest_index(t)
        ts = self.items[idx].ts_ns
        snapped = self._time_to_slider_value(ts)
        if snapped != v:
            self.slider.blockSignals(True)
            self.slider.setValue(snapped)
            self.slider.blockSignals(False)
        self.pending_slider = snapped
        # The axis cursor belongs to the SLIDER, not to the render pipeline. It used to
        # be moved only from _apply_scrub, which runs on the 33 ms scrub timer and
        # returns early on `idx == current_idx` and on the in-flight cap — so the big
        # blue timestamp lagged the handle during a drag and froze outright whenever the
        # loader was saturated. It is also the only update path for moves that never
        # emit sliderPressed (wheel, arrow keys, clicking the groove), which left the
        # bubble and the picture behind entirely. Setting it here makes it track the
        # handle, and it is set from the SNAPPED frame's own timestamp, so it always
        # reads the same moment as the label burned into the frame.
        self.tickbar.set_cursor(ts)
        if not self._is_scrubbing and not self._is_playing:
            # Wheel / keyboard / groove-click: no press, no release, so nothing else
            # would ever render this position. Debounced so holding a key does not
            # queue a decode per repeat.
            self._keynav_debounce.start(60)

    def _apply_scrub(self):
        if self.pending_slider is None or not self.items: return
        t = self._slider_to_time_ns(self.pending_slider)
        idx = self._time_to_nearest_index(t)
        if idx == self.current_idx: return

        self._update_motion_speed(idx)

        # Multi-cam: each camera independently shows latest frame <= slider time
        if self._is_multi_cam():
            self.current_idx = idx
            self.target_idx = idx
            self.play_time_ns = self.items[idx].ts_ns
            self._set_info_for(idx, self.play_time_ns)
            self.tickbar.set_cursor(self.play_time_ns)
            self._display_multicam_index(idx, update_slider=False)
            return

        prev_idx = self.current_idx
        self.current_idx = idx
        self.target_idx = idx
        self.play_time_ns = self.items[idx].ts_ns
        self._set_info_for(idx, self.play_time_ns)
        self.tickbar.set_cursor(self.play_time_ns)
        self._pv_trigger_fetch()

        max_side = self._current_decode_side()
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_arr_for(max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)
        bc = self._bc()
        key = (self._ck(idx), max_side, brighten, gradient_id, bc, id(ref) if ref is not None else None, sub_thr, sub_off)

        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self.img_view.set_pixmap(cached)
            self._diag_cach += 1
            self._update_diff_stats(key)
            return

        # Live mode off: the whole window is preloaded at PROXY_MAX_SIDE, so repaint
        # from memory and never touch the share while the slider is moving. The
        # frame stopped on is re-rendered at native resolution by the refine timer.
        if self._proxy_try_paint(idx):
            self._schedule_refine()
            return

        # No preview for this position yet — fall back to a real load, but keep only a
        # few decodes in flight: a fast drag over a long window would otherwise queue
        # thousands of LoadTasks and the picture would run far behind the slider.
        #
        # The cap must match what the drag can fall back on. With the preview covering
        # the window, 3 is right — the picture comes from memory anyway and these loads
        # are just quality. WITHOUT it every position needs a real share read, and 3 (the
        # old unconditional value) was permanently saturated on a slow share, so every
        # tick returned here and the picture froze for the whole drag. load_pool has 8
        # threads; let the drag use them, as v2.5.5 did with no cap at all.
        # Zoom belongs in this test: _proxy_try_paint refuses every zoomed position, so
        # "usable and covered" alone applied the tight cap to a drag that had no preview
        # to fall back on — the worst-of-both-worlds state this gate exists to avoid.
        # _current_decode_side already tests zoom the same way.
        zoomed = getattr(self.img_view, "_zoom_norm", None) is not None
        # Track 0: _apply_scrub returns above for multi-cam, so this is the single-cam
        # timeline's own preview and nothing else's.
        cap = 3 if (self._proxy_usable() and self._proxy_covered_track(0) and not zoomed) else 8
        if len(self._inflight) >= cap and key not in self._inflight:
            # Rewind current_idx so the NEXT 33 ms tick retries this position. Simply
            # returning here — as this used to — left current_idx already advanced, so
            # every following tick was swallowed by the `idx == self.current_idx` check
            # above and no request for this frame was ever registered: the picture froze
            # until the drag ended. That was the multi-second "stuck slider" whenever a
            # drag entered a range the preview had not reached yet.
            self.current_idx = prev_idx
            return

        self._display_req_id += 1
        self._want_display_req[key] = self._display_epoch

        if key not in self._inflight:
            self._inflight.add(key)
            self.load_pool.start(LoadTask(
                self._gen, self._display_req_id, idx,
                self.items[idx].path, max_side, brighten, gradient_id,
                self.load_signals, bc, ref, sub_thr, sub_off, key=key))

    def _on_slider_released(self):
        if self.scrub_timer.isActive(): self.scrub_timer.stop()
        self._is_scrubbing = False
        # Pokud user posune slider, vypni auto-follow
        if self._auto_follow:
            self._btn_auto_follow.setChecked(False)
        if not self.items: return
        t = self._slider_to_time_ns(self.slider.value())
        idx = self._time_to_nearest_index(t)
        self._want_display_req.clear()
        # Drop whatever the drag left queued: those decodes are for frames already
        # scrolled past, and running them first delays the frame the user landed on.
        # _inflight must be cleared with them — a runnable removed from the queue
        # never emits, so its key would block that frame from ever loading again.
        self.load_pool.clear()
        self._inflight.clear()
        self._display_load_key = None
        self._deferred_display = None
        if self._is_multi_cam():
            self._reset_cam_pipeline()
            self._display_multicam_index(idx, update_slider=False)
        else:
            self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=False)
        # …then step up to native resolution once it is clear the user has stopped.
        self._schedule_refine()
        self._schedule_prefetch_after_idle()
        self._reset_motion_tracking()
        # The drag ran the sweep at PROXY_DRAG_WORKERS — let it go back to full speed,
        # after a short grace so the frame just landed on wins the first reads. This used
        # to be a bare _proxy_pump(), which was a no-op: the dragging branch had just
        # re-armed a 1.5 s grace, so the pump found time still on the clock and only
        # re-armed the wait. The grace belongs HERE, at the end of the interaction.
        self._proxy_idle_grace()

    def _schedule_prefetch_after_idle(self):
        if not self._is_playing: self._prefetch_debounce.start(140)

    def _run_prefetch_after_idle(self):
        if not self.items or self.current_idx is None: return
        if self._is_scrubbing or self._is_playing: return
        # Multi-cam never displays from self.cache — every tile has its own cache and reads
        # from its own item list. Prefetching MERGED-timeline frames here therefore fired
        # 2*PREFETCH_RADIUS_IDLE share reads after every settle whose results could not be
        # shown by anything, competing with the tiles that were actually waiting. Measured
        # at 40+ wasted reads per drag with 4 cameras.
        if self._is_multi_cam(): return
        self._prefetch_idle(self.current_idx)

    # ================================================================ DISPLAY / LOADING
    def _ck(self, idx: int) -> int:
        """Absolute frame number of a self.items index — the cache-key identity.

        Pixmap caches are keyed by index, but the live cap trims the oldest frames
        (ONLINE_MAX_ITEMS), so the same index means a different frame after every
        trim. At the cap len(self.items) stops growing and the NEWEST frame sits at
        a fixed index forever: a plain idx key then hits the pixmap cached for the
        previous frame and the live view freezes on it. Adding the running trim
        count keeps every frame's key its own.

        The counter only ever grows — a key issued before a restructure can never
        be handed out again (see _merge_restored_history).
        """
        return idx + self._items_offset

    def _cam_ck(self, cam_i: int, idx: int) -> int:
        """_ck for a per-camera item list — each camera trims on its own clock."""
        off = self._cam_offsets[cam_i] if cam_i < len(self._cam_offsets) else 0
        return idx + off

    def _cam_ck_idx(self, cam_i: int, ck) -> int:
        """Inverse of _cam_ck — position in the CURRENT list, or negative/out of
        range if that frame has since been trimmed away."""
        if not isinstance(ck, int):
            return -1
        off = self._cam_offsets[cam_i] if cam_i < len(self._cam_offsets) else 0
        return ck - off

    def _bump_cam_offset(self, cam_i: int, n: int):
        """Record that `n` frames fell off the front of camera cam_i's list."""
        while len(self._cam_offsets) <= cam_i:
            self._cam_offsets.append(0)
        self._cam_offsets[cam_i] += n

    def _invalidate_shared_ckeys(self, prev_len: int = 0):
        """self.items was restructured (merge / rebuild), not just trimmed at the
        front, so indices no longer mean what they did. Move the key space past
        everything ever issued and drop the pixmaps those keys point at."""
        self._items_offset += max(prev_len, len(self.items)) + 1
        self.cache.clear()

    def _invalidate_cam_ckeys(self, cam_i: int, prev_len: int = 0):
        """_invalidate_shared_ckeys for one camera's item list."""
        n = len(self._cam_items[cam_i]) if cam_i < len(self._cam_items) else 0
        self._bump_cam_offset(cam_i, max(prev_len, n) + 1)
        if cam_i < len(self._cam_caches):
            self._cam_caches[cam_i].clear()

    def _load_or_cache(self, idx, max_side, brighten, req_id=0):
        gradient_id = self.gradient_cb.currentIndex()
        bc = self._bc()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_arr_for(max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)
        ref_id = id(ref) if ref is not None else None
        key = (self._ck(idx), max_side, brighten, gradient_id, bc, ref_id, sub_thr, sub_off)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull(): return cached
        if key not in self._inflight:
            self._inflight.add(key)
            self.load_pool.start(LoadTask(self._gen, req_id, idx, self.items[idx].path, max_side, brighten, gradient_id, self.load_signals, bc, ref, sub_thr, sub_off, key=key))
        return None

    def _adaptive_index_step(self, target_idx):
        if self.current_idx is None: return target_idx
        delta = target_idx - self.current_idx
        if delta == 0: return target_idx
        stride = self._adaptive_stride()
        if self._play_is_exact() and (self._is_playing or self._is_scrubbing) and self._last_motion_ips < 30:
            stride = 1
        step = min(abs(delta), stride)
        return self.current_idx + (step if delta > 0 else -step)

    def _display_index(self, idx, axis_time_ns, update_slider):
        if not (0 <= idx < len(self.items)): return
        self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(idx, axis_time_ns); self._request_display_target(idx, axis_time_ns, update_slider)

    def _display_exact_index(self, idx, axis_time_ns, update_slider):
        if not (0 <= idx < len(self.items)): return
        # Discrete navigation — the one thing that invalidates loads still in flight.
        # Everything the user does deliberately funnels through here (slider release,
        # Stop, arrow step, goto-timestamp, gradient / brightness change, scan finished),
        # while scrubbing and playback deliberately do NOT bump it.
        self._display_epoch += 1
        self.current_idx = idx; self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._display_req_id += 1; rid = self._display_req_id
        self._set_info_for(idx, axis_time_ns); self._request_pixmap(idx, rid)
        self._pv_trigger_fetch()
        self.tickbar.set_cursor(axis_time_ns)

    def _display_multicam_at_time(self, time_ns: int, update_slider: bool = False):
        """Display for all cameras the latest frame with ts_ns <= time_ns."""
        if not self._is_multi_cam() or not self._cam_items:
            return
        if not self.items:
            return

        # Snap time_ns to nearest item in merged timeline
        idx = self._time_to_nearest_index(time_ns)
        idx = max(0, min(idx, len(self.items) - 1))
        self.current_idx = idx
        self.target_idx  = idx
        self.play_time_ns = self.items[idx].ts_ns

        if update_slider:
            sv = self._time_to_slider_value(self.play_time_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)

        self._set_info_for(idx, self.play_time_ns)
        self._display_multicam_index(idx, update_slider=False)

    def _display_multicam_index(self, idx: int, update_slider: bool = False):
        """Display each camera's latest frame with ts_ns <= merged timeline ts at idx."""
        if not self._is_multi_cam() or not self._cam_items:
            return
        if not self.items:
            return

        idx = max(0, min(idx, len(self.items) - 1))
        self.current_idx = idx
        self.target_idx  = idx

        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True)
            self.slider.setValue(sv)
            self.slider.blockSignals(False)
            # Update per-cam sliders to match this timeline position
            t_ns = self.items[idx].ts_ns
            for cam_i, row in enumerate(self._per_cam_rows):
                cam_ts = self._cam_ts[cam_i] if cam_i < len(self._cam_ts) else []
                if not cam_ts:
                    continue
                frame_idx = max(0, bisect.bisect_right(cam_ts, t_ns) - 1)
                sv_cam = self._per_cam_ts_to_slider(cam_i, cam_ts[frame_idx])
                row.set_value(sv_cam)
            # Update tickbar cursor to master position (if a master is set)
            master = self._per_cam_master_idx
            if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                cam_ts_m = self._cam_ts[master]
                fidx = max(0, bisect.bisect_right(cam_ts_m, t_ns) - 1)
                self.tickbar.set_cursor(cam_ts_m[fidx])

        self.play_time_ns = self.items[idx].ts_ns
        self._set_info_for(idx, self.play_time_ns)
        self._pv_trigger_fetch()

        t_ns = self.items[idx].ts_ns
        brighten_g  = 1 if self.cb_bright.isChecked() else 0
        bc_g        = self._bc()
        gradient_id = self.gradient_cb.currentIndex()
        subtract    = self.cb_subtract.isChecked()
        n_cams      = len(self._cam_items)
        # Invariant across the loop and across this tick. Reading it once also stops two
        # tiles from being cache-keyed at different sizes if the motion estimate moves
        # while the loop is running.
        max_side    = self._cam_tile_side(n_cams)

        for cam_i in range(n_cams):
            cam_items = self._cam_items[cam_i]
            if not cam_items:
                continue

            # Auto-stretch / brightness / contrast only on the selected camera(s)
            enh      = self._cam_enhance_on(cam_i)
            brighten = brighten_g if enh else 0
            bc       = bc_g if enh else _RENDER_BC_NONE

            # Find latest frame in this camera with ts_ns <= t_ns
            cam_ts = self._cam_ts[cam_i] if (hasattr(self, '_cam_ts') and cam_i < len(self._cam_ts)) else None
            if cam_ts:
                pos = bisect.bisect_right(cam_ts, t_ns) - 1
                cam_idx = max(0, pos)
            else:
                cam_idx = min(idx, len(cam_items) - 1)
            it = cam_items[cam_idx]

            # Track which frame each camera is currently showing (for save)
            if hasattr(self, '_cam_current_idx') and cam_i < len(self._cam_current_idx):
                self._cam_current_idx[cam_i] = cam_idx

            # Per-camera reference decoded at this display size (see _ref_arr_for)
            ref = self._cam_ref_arr_for(cam_i, max_side) if subtract else None
            ref_id = id(ref) if ref is not None else None
            effective_sub_thr, effective_sub_off = self._sub_params(ref)

            cache = self._cam_caches[cam_i]
            ck = self._cam_ck(cam_i, cam_idx)
            key = (ck, max_side, brighten, gradient_id, bc, ref_id,
                   effective_sub_thr, effective_sub_off)
            self._cam_set_shown_key(cam_i, key)
            self._cam_note_target(cam_i, it.ts_ns)
            cached = cache.get(key)
            if cached is not None and not cached.isNull():
                iv = self._multi_grid.get_img_view(cam_i)
                if iv:
                    iv.set_pixmap(cached)
                    self._diag_cach += 1
                self._cam_note_painted(cam_i, it.ts_ns)
                self._collect_cam_diff_stats(cam_i, key)   # label flushed after the loop
                if cam_i < len(self._cam_want):
                    self._cam_want[cam_i] = None   # we're current; drop any stale pending load
                continue

            # Repaint this tile from the preloaded preview instead of queueing a share read
            # per camera per position, then let the refine timer bring every tile up to
            # tile resolution once the user settles.
            #
            # This used to be gated on `_is_scrubbing or _is_playing`, so arrow-stepping and
            # any single seek fell straight through to N share reads — one per camera, every
            # keypress. That is why the arrows behaved exactly like the slider used to:
            # some tiles updated, others lagged. Off-live navigation is navigation however
            # it is driven, so the preview serves all of it.
            if self._proxy_try_paint_cam(cam_i, cam_idx):
                if cam_i < len(self._cam_want):
                    self._cam_want[cam_i] = None
                self._schedule_refine()
                continue

            # Coalesced load: like _per_cam_display_one, remember only the LATEST wanted
            # frame per camera, and let _start_cam_load decide whether there is room for it
            # (see _cam_inflight_depth). Without the coalescing a fast scrub would flood the
            # pool with a growing backlog of LoadTasks and the shown frame would lag further
            # and further behind the slider; without the depth the camera could only ever
            # have one read outstanding, which is the ~7 frames/s ceiling.
            if cam_i < len(self._cam_want):
                self._cam_want[cam_i] = (cam_idx, it.path, max_side, brighten,
                                         gradient_id, ref, effective_sub_thr, bc,
                                         effective_sub_off, ck)
                self._start_cam_load(cam_i)

        # One label build for the whole pass, not one per camera (see
        # _collect_cam_diff_stats).
        self._flush_cam_diff_stats()
        # Re-colour the timestamp labels straight away. A tile that got a new target but no
        # frame produces no paint, so without this its label would keep the colour from its
        # last successful paint until the 600 ms dot tick came round.
        self._cam_refresh_stale_marks()

    def _on_cam_loaded(
        self, cam_i: int,
        gen: int, req_id: int, idx: int,
        max_side: int, brighten: int, gradient_id: int,
        bc: _RenderBC, img: QImage, key=None
    ):
        """Callback pro načtený snímek jedné kamery v multi-cam módu."""
        # Release this load's in-flight slot on EVERY path (incl. stale gen / null image) so
        # the coalescing pipeline never deadlocks, then pull the next pending frame.
        #
        # pop by req_id, never a bare decrement: this load may already have been released by
        # _reset_cam_pipeline or _cam_load_watchdog while it was still running, or dropped
        # from the queue by _cam_pool.clear() on a camera-set change. Then its id is gone
        # and this is a no-op — it must not free a slot it no longer owns.
        if cam_i < len(getattr(self, '_cam_inflight_at', [])):
            self._cam_inflight_at[cam_i].pop(req_id, None)
        if gen != self._gen or img.isNull():
            self._start_cam_load(cam_i)
            return
        # Use the key the launcher computed (echoed back by LoadTask). Recomputing it from
        # live UI state cached the pixmap under the wrong key whenever the per-camera
        # reference/subtraction changed mid-load.
        if key is None:
            subtract = self.cb_subtract.isChecked()
            ref = self._cam_ref_arr_for(cam_i, max_side) if subtract else None
            ref_id = id(ref) if ref is not None else None
            sub_thr, sub_off = self._sub_params(ref)
            key = (self._cam_ck(cam_i, idx), max_side, brighten, gradient_id, bc, ref_id, sub_thr, sub_off)
        pix = QPixmap.fromImage(img)
        if cam_i < len(self._cam_caches):
            self._cam_caches[cam_i].put(key, pix)

        # Only the render this tile is still waiting for may reach the screen.
        # A render started with older params (Offset, Diff threshold, gradient,
        # brightness) can finish AFTER the fresh one — the per-camera pool runs two
        # threads, so both are in flight at once. Painting it put the tile back to
        # the old look, and because _start_cam_load had already consumed _cam_want
        # nothing re-rendered it: the tile kept the old settings until the next
        # frame arrived. Across many cameras the winner of that race was random,
        # which is why raising Offset appeared to affect only one camera.
        want_key = (self._cam_shown_key[cam_i]
                    if cam_i < len(getattr(self, '_cam_shown_key', [])) else None)
        # key[0] identifies the FRAME; key[1:] are the render params. Only a PARAM
        # mismatch is the race this guard exists for. Comparing the whole key made
        # live mode paint nothing at all: at live frame rates a newer frame nearly
        # always arrives while the current one is decoding, so every finished decode
        # was rejected here, the relaunch was rejected in turn, and the tile stayed
        # empty except for the rare load that landed in a gap between arrivals.
        if want_key is not None and key[1:] != want_key[1:]:
            cached = (self._cam_caches[cam_i].get(want_key)
                      if cam_i < len(self._cam_caches) else None)
            if cached is not None and not cached.isNull():
                iv = self._multi_grid.get_img_view(cam_i)
                if iv:
                    iv.set_pixmap(cached)
                    self._diag_cach += 1
                # want_key[0] is the frame this render belongs to, as an absolute
                # number (_cam_ck) — convert it back to a position in the current,
                # possibly trimmed list. This path used to paint without touching the
                # label or the shown-timestamp bookkeeping, so the tile could converge
                # on the right picture while still advertising an older frame — and the
                # refresh dot counted it as "not displaying".
                _wi = self._cam_ck_idx(cam_i, want_key[0])
                if (cam_i < len(self._cam_items)
                        and isinstance(_wi, int) and 0 <= _wi < len(self._cam_items[cam_i])):
                    self._cam_note_painted(cam_i, self._cam_items[cam_i][_wi].ts_ns)
                self._update_cam_diff_stats(cam_i, want_key)
                self._start_cam_load(cam_i)
                return
            # The wanted render is neither cached nor pending (_cam_want was
            # consumed by the launch of the render that just finished) — re-derive
            # it from live UI state so the tile always converges on the settings
            # actually shown in the panel.
            self._start_cam_load(cam_i)
            if self._cam_inflight_count(cam_i) < self._cam_inflight_depth():
                self._redraw_cam_in_place(cam_i)
            return

        self._update_cam_diff_stats(cam_i, key)
        # Don't paint a frame that is already older than what the tile is showing —
        # avoids the view flashing backwards while it catches up to live.
        #
        # The reference for "older" differs by mode. Following live, the newest frame
        # keeps moving while this one decodes, so comparing against the latest REQUEST
        # (_cam_current_idx) rejected every catch-up frame and the tile never updated;
        # compare against the frame ON SCREEN instead, which is monotonic without
        # discarding anything.
        ts_new = None
        if cam_i < len(self._cam_items) and 0 <= idx < len(self._cam_items[cam_i]):
            ts_new = self._cam_items[cam_i][idx].ts_ns
        following_live = bool(self._online_mode and getattr(self, "_auto_follow", False))
        is_wanted = True
        if following_live:
            shown_ts = (self._cam_shown_ts_ns[cam_i]
                        if cam_i < len(self._cam_shown_ts_ns) else 0)
            may_paint = (ts_new is None or shown_ts == 0 or ts_new >= shown_ts)
        else:
            # "Is this still the frame the tile wants?", NOT "is its index >= the last
            # REQUEST?". The old test was `idx >= self._cam_current_idx[cam_i]`, and
            # _cam_current_idx is written at REQUEST time — so during a drag the request
            # always runs ahead of a ~145 ms load and nearly every finished decode was paid
            # for and then thrown on the floor. Dragging BACKWARDS was worse: the correct
            # frame has a lower index, so it was rejected outright until a relaunch
            # happened to catch up.
            #
            # key[1:] == want_key[1:] is already guaranteed here (the param-race guard
            # above returned otherwise), so key[0] == want_key[0] means this IS the render
            # the tile is waiting for.
            want_ck = want_key[0] if want_key is not None else None
            is_wanted = (want_ck is None or key[0] == want_ck)
            if is_wanted:
                may_paint = True
            elif self._navigating():
                # A position the user has already passed. Paint it anyway when it lies
                # BETWEEN what is on screen and where the handle now is: with a depth cap
                # above 1, several consecutive positions decode at once and they do not
                # finish in order, so this is what makes the tile animate THROUGH the drag
                # instead of jumping from start to end. The interval test — rather than a
                # plain >= — is what stops it from ever moving the tile AWAY from the
                # target, in either direction of travel.
                shown  = (self._cam_shown_ts_ns[cam_i]
                          if cam_i < len(self._cam_shown_ts_ns) else 0)
                target = (self._cam_target_ts_ns[cam_i]
                          if cam_i < len(self._cam_target_ts_ns) else 0)
                may_paint = bool(ts_new is not None and shown and target
                                 and min(shown, target) <= ts_new <= max(shown, target))
            else:
                # Parked: a decode left over from a finished drag must not overwrite the
                # frame the user landed on.
                may_paint = False
        if may_paint:
            iv = self._multi_grid.get_img_view(cam_i)
            if iv:
                iv.set_pixmap(pix)
                self._diag_load += 1
            if ts_new is not None:
                # Trailing the newest arrival by a frame or two is how a live view
                # catches up, not a fault — without a tolerance the tile would be
                # marked stale (red label + red dot) permanently while updating fine.
                self._cam_note_painted(
                    cam_i, ts_new, LIVE_PAINT_TOL_NS if following_live else 0)
                # One slider pair for many cameras → follow the selected (master) one.
                # Only for the frame actually asked for: the intermediate paints above are
                # frames flying past, and letting them drive the sliders made the
                # Brightness/Contrast controls jitter for the whole drag.
                if is_wanted and cam_i == getattr(self, "_per_cam_master_idx", 0):
                    self._refresh_auto_bc_sliders(self._cam_items[cam_i][idx].path)
        # NOTE: the refresh dot is deliberately NOT bumped here. A finished image
        # load only means a decode completed — it also fires when the user drags
        # the slider over old frames, so bumping here lit the dot green with no
        # new data at all. Arrival is recorded where frames are appended
        # (_online_poll_multi / _on_dir_watch_new_file).
        # Load the most recent frame that arrived while this one was loading.
        self._start_cam_load(cam_i)

    def _note_cam_frames(self, cam_idx: int):
        """Record that camera cam_idx just received NEW frame(s). Only real frame
        arrivals may drive the refresh dot — see CAM_DOT_FRESH_S."""
        if cam_idx < 0:
            return
        while len(self._cam_last_update_ts) <= cam_idx:
            self._cam_last_update_ts.append(0.0)
        self._cam_last_update_ts[cam_idx] = time.monotonic()

    def _note_cam_shown(self, cam_idx: int, ts_ns: int):
        """Record that camera cam_idx just PAINTED the frame with this timestamp.
        Only a changed timestamp counts as a display update — repainting the same
        frame (resize, contrast change, re-decode) must not keep the dot green."""
        if cam_idx < 0:
            return
        while len(self._cam_shown_ts_ns) <= cam_idx:
            self._cam_shown_ts_ns.append(0)
        while len(self._cam_shown_mono) <= cam_idx:
            self._cam_shown_mono.append(0.0)
        if ts_ns != self._cam_shown_ts_ns[cam_idx]:
            self._cam_shown_ts_ns[cam_idx] = ts_ns
            self._cam_shown_mono[cam_idx]  = time.monotonic()

    # ---- the ONE truth about each tile -------------------------------------------
    def _cam_note_target(self, cam_idx: int, ts_ns: int):
        """The frame the slider / playback is asking this tile for. Recorded at REQUEST
        time; nothing about the screen changes here."""
        if cam_idx < 0:
            return
        while len(self._cam_target_ts_ns) <= cam_idx:
            self._cam_target_ts_ns.append(0)
        self._cam_target_ts_ns[cam_idx] = ts_ns

    def _cam_note_painted(self, cam_idx: int, ts_ns: int, tol_ns: int = 0,
                          preview: bool = False):
        """Called from EVERY path that actually puts a pixmap on tile cam_idx, and from
        nowhere else. Updates the tile's timestamp label to the frame ON SCREEN and marks
        it stale when that is not (near enough to) the frame the slider asked for.

        Previously the label was written at request time (in _per_cam_display_one, before
        the load was even queued), so during a drag every tile's timestamp marched along
        with the handle while the pictures sat still — "the timestamp keeps going but
        nothing is shown". Now the label can only move when a frame really lands.

        `tol_ns` is how far the painted frame may legitimately be from the target, and it
        MUST be the same number the paint path used to accept the frame. It was not: the
        preview accepted a neighbour within _proxy_motion_tol (up to minutes on a coarse
        plan) and then reported tr.ts_gap (seconds), so every preview paint in between was
        judged stale and every tile sat red permanently.

        `preview` says the frame came out of the sampled RAM preview rather than being the
        exact one requested, which is the designed behaviour and gets its own (blue, "~")
        label state — distinct from both "exact" and "behind"."""
        if cam_idx < 0:
            return
        self._note_cam_shown(cam_idx, ts_ns)
        while len(self._cam_paint_tol_ns) <= cam_idx:
            self._cam_paint_tol_ns.append(0)
        self._cam_paint_tol_ns[cam_idx] = max(0, int(tol_ns))
        while len(self._cam_paint_preview) <= cam_idx:
            self._cam_paint_preview.append(False)
        stale = self._cam_is_stale(cam_idx)
        # Only call it approximate when it really differs from the target: a preview paint
        # of the exact frame (which is every paint once the window is fully preloaded) is
        # exact, and tildeing it would put the marker on permanently — the same mistake in
        # a different colour.
        target = (self._cam_target_ts_ns[cam_idx]
                  if cam_idx < len(self._cam_target_ts_ns) else 0)
        approx = bool(preview and target and ts_ns != target and not stale)
        self._cam_paint_preview[cam_idx] = approx
        if self._bench is not None:
            self._bench.append((time.perf_counter(), cam_idx, target, ts_ns,
                                2 if stale else (1 if approx else 0)))
        self._multi_grid.set_cam_timestamp(
            cam_idx, fmt_hhmmss_ms_from_ns(ts_ns), stale=stale, approx=approx)

    def _cam_is_stale(self, cam_idx: int) -> bool:
        """True when this tile is NOT showing the frame it was last asked for."""
        shown  = (self._cam_shown_ts_ns[cam_idx]
                  if cam_idx < len(self._cam_shown_ts_ns) else 0)
        target = (self._cam_target_ts_ns[cam_idx]
                  if cam_idx < len(self._cam_target_ts_ns) else 0)
        tol    = (self._cam_paint_tol_ns[cam_idx]
                  if cam_idx < len(self._cam_paint_tol_ns) else 0)
        if not target or not shown:
            return False
        return abs(shown - target) > tol

    def _cam_stale_lag_s(self, cam_idx: int) -> float:
        """How far behind the requested moment this tile is, in seconds."""
        shown  = (self._cam_shown_ts_ns[cam_idx]
                  if cam_idx < len(self._cam_shown_ts_ns) else 0)
        target = (self._cam_target_ts_ns[cam_idx]
                  if cam_idx < len(self._cam_target_ts_ns) else 0)
        if not target or not shown:
            return 0.0
        return abs(target - shown) / 1e9

    def _cam_refresh_stale_marks(self):
        """Re-colour every tile's timestamp label against the current targets, without
        changing the text. A tile that never gets its frame would otherwise keep the colour
        from its last successful paint and go on looking current."""
        if not self._is_multi_cam() or self._multi_grid is None:
            return
        for i in range(self._multi_grid.cam_count()):
            shown = self._cam_shown_ts_ns[i] if i < len(self._cam_shown_ts_ns) else 0
            if not shown:
                continue
            # set_cam_stale, not set_cam_timestamp: the text is already correct here (this
            # method exists only to re-judge the colour), and formatting it again for every
            # tile on every call was pure waste — set_timestamp discarded it anyway.
            approx = (self._cam_paint_preview[i]
                      if i < len(self._cam_paint_preview) else False)
            self._multi_grid.set_cam_stale(
                i, self._cam_is_stale(i), approx=approx)

    def _on_cam_dot_blink(self):
        """Aktualizuje blikající refresh doty u každé kamery.

        Green requires BOTH halves of "the image is updating": new frames landed
        within CAM_DOT_FRESH_S *and* the tile actually painted a new frame in that
        window. Frames-but-no-paint (hung load, coalescing stuck behind a slow SMB
        read) is exactly the case that used to blink green over a frozen picture,
        so it is red now. With auto-follow off the tile is frozen on purpose, so
        arrival alone is enough. Grey = live mode off."""
        self._cam_load_watchdog()
        self._cam_dot_blink_state = not self._cam_dot_blink_state
        now = time.monotonic()
        master_i  = getattr(self, "_per_cam_master_idx", 0)
        following = bool(getattr(self, "_auto_follow", True))
        # Keep the timestamp colours honest on the same tick: a tile that simply never
        # receives its frame produces no paint, so nothing else would ever re-evaluate it.
        self._cam_refresh_stale_marks()
        for i, cv in enumerate(self._multi_grid._cam_views):
            if not self._online_mode:
                cv.dim_refresh_dot()
                continue
            last = self._cam_last_update_ts[i] if i < len(self._cam_last_update_ts) else 0.0
            age  = (now - last) if last > 0 else None
            shown = self._cam_shown_mono[i] if i < len(self._cam_shown_mono) else 0.0
            shown_age = (now - shown) if shown > 0 else None
            arriving   = age is not None and age < CAM_DOT_FRESH_S
            displaying = shown_age is not None and shown_age < CAM_DOT_FRESH_S
            # The decisive test, and the one the user asked for: is this tile showing the
            # frame it was asked for? "Frames are arriving" and "something repainted
            # recently" can both be true while the picture sits on an older moment than
            # the slider — which is precisely when the dot used to blink green over a
            # visibly stuck tile.
            behind = self._cam_is_stale(i)
            if not arriving:
                tip = ("STALE: no new frame received" +
                       (f" for {age:.1f} s" if age is not None else " at all"))
            elif behind:
                tip = (f"STALE: showing a frame {self._cam_stale_lag_s(i):.1f} s away from "
                       f"the requested moment — this tile is behind")
            elif not displaying and following:
                tip = (f"STALE: frames arriving ({age:.1f} s ago) but the tile is "
                       f"not repainting" +
                       (f" ({shown_age:.1f} s)" if shown_age is not None else ""))
            else:
                tip = f"Receiving frames — last new frame {age:.1f} s ago"
            cv.pulse_refresh_dot(
                self._cam_dot_blink_state,
                is_main=(i == master_i),
                fresh=(arriving and not behind and (displaying or not following)),
                tip=tip,
            )

    def _request_display_target(self, idx, axis_time_ns, update_slider):
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_arr_for(max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)
        key = (self._ck(idx), max_side, brighten, gradient_id, self._bc(), id(ref) if ref is not None else None, sub_thr, sub_off)
        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self._display_req_id += 1; self.current_idx = idx
            self.img_view.set_pixmap(cached)
            self._update_diff_stats(key)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True); return
        if self._display_load_key is not None and self._display_load_key != key:
            self._deferred_display = (idx, axis_time_ns, update_slider); return
        self._display_req_id += 1; req_id = self._display_req_id
        self._want_display_req[key] = self._display_epoch; self._display_load_key = key
        pm = self._load_or_cache(idx, max_side, brighten, req_id)
        if pm is not None:
            self.current_idx = idx; self.img_view.set_pixmap(pm)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)
            self._display_load_key = None

    def _drain_deferred_display(self):
        if self._display_load_key is not None or self._deferred_display is None: return
        idx, axis_time_ns, update_slider = self._deferred_display; self._deferred_display = None
        if not (0 <= idx < len(self.items)): return
        self.target_idx = idx; self.play_time_ns = axis_time_ns
        if update_slider:
            sv = self._time_to_slider_value(self.items[idx].ts_ns)
            self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(idx, axis_time_ns); self._request_display_target(idx, axis_time_ns, update_slider)

    def _request_pixmap(self, idx, req_id):
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_arr_for(max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)
        key = (self._ck(idx), max_side, brighten, gradient_id, self._bc(), id(ref) if ref is not None else None, sub_thr, sub_off)
        self._want_display_req[key] = self._display_epoch
        pm = self._load_or_cache(idx, max_side, brighten, req_id)
        if pm is not None and self.target_idx == idx and req_id == self._display_req_id:
            self.current_idx = idx; self.img_view.set_pixmap(pm)
            self._update_diff_stats(key)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)

    def _prefetch_idle(self, idx):
        if len(self._inflight) > 10: return
        max_side = self._scrub_side; brighten = 1 if self.cb_bright.isChecked() else 0
        for j in range(idx - PREFETCH_RADIUS_IDLE, idx + PREFETCH_RADIUS_IDLE + 1):
            if j != idx and 0 <= j < len(self.items):
                self._load_or_cache(j, max_side, brighten)

    def _prefetch_playish(self, idx):
        if self._last_motion_ips >= 120 or len(self._inflight) > 4: return
        max_side = self._current_decode_side(); brighten = 1 if self.cb_bright.isChecked() else 0
        ahead = 1 if self._last_motion_ips >= 40 else PREFETCH_AHEAD_PLAY
        for j in range(idx + 1, min(len(self.items), idx + 1 + ahead)):
            self._load_or_cache(j, max_side, brighten)

    def _on_loaded(self, gen, req_id, idx, max_side, brighten, gradient_id, bc, img, key=None):
        if gen != self._gen: return
        # Use the EXACT key the launcher registered in _inflight / _want_display_req /
        # _display_load_key. Recomputing it from live UI state (as this used to) drifted
        # from the launch key on any mid-flight reference/subtract/gradient change, which
        # is what made the slider, arrows and subtraction toggle silently stop working.
        if key is None:
            subtract = self.cb_subtract.isChecked()
            ref = self._ref_arr_for(max_side) if subtract else None
            sub_thr, sub_off = self._sub_params(ref)
            key = (self._ck(idx), max_side, brighten, gradient_id, bc, id(ref) if ref is not None else None, sub_thr, sub_off)
        self._inflight.discard(key)
        if img.isNull():
            if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()
            return
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()
            return
        self.cache.put(key, pm)
        want_epoch = self._want_display_req.pop(key, None)
        # Late arrivals: which ones still deserve the screen?
        #
        # Frame identity is the WRONG test. Both a drag and playback move on every 33 ms
        # tick, so on a share slower than that EVERY load lands for a frame that is no
        # longer the target. Testing `idx != target_idx` therefore suppressed all of them
        # and the picture froze solid — 0 repaints in 5 s of playback, 0 % of scrub ticks
        # repainting on the paths with no preview to fall back on (range search, preview
        # off, zoomed in, mid-sweep). That is the very symptom this guard exists to cure,
        # just moved elsewhere; v2.5.5 painted whatever arrived, and a slightly stale
        # picture that MOVES is what makes a slider feel alive.
        #
        # The real distinction is the interaction, not the frame: a load belongs to the
        # display epoch that asked for it. Within one epoch (a whole drag, a whole
        # playback run) late frames paint. A discrete navigation bumps the epoch, so a
        # want left over from a view the user has left — e.g. frame 599 at 900 px, later
        # satisfied by an idle prefetch of the same key — is dropped instead of yanking
        # the view back to it.
        if want_epoch is not None and want_epoch != self._display_epoch:
            want_epoch = None
        if want_epoch is not None:
            # current_idx doubles as the drag / playback cursor (_autoplay_step steps from
            # it, _apply_scrub compares against it), so a late frame must not move it:
            # rewinding it made playback crawl back over frames it had already passed.
            # The deliberate display paths set current_idx themselves before requesting.
            if not (self._is_playing or self._is_scrubbing):
                self.current_idx = idx
            self.img_view.set_pixmap(pm)
            self._diag_load += 1
            self._update_diff_stats(key)
            if idx < len(self.items):
                self._refresh_auto_bc_sliders(self.items[idx].path)
            self.btn_cal_circle.setEnabled(True); self.btn_cal_square.setEnabled(True)
            # Pokud auto-follow, ujistíme se že slider je na správné pozici
            if self._auto_follow and self._online_mode and self.items and idx < len(self.items):
                sv = self._time_to_slider_value(self.items[idx].ts_ns)
                self.slider.blockSignals(True)
                self.slider.setValue(sv)
                self.slider.blockSignals(False)
        if self._display_load_key == key: self._display_load_key = None; self._drain_deferred_display()

    # ================================================================ AUTOPLAY
    def _play_tick_ms(self) -> int:
        """Playback tick. Twice as fast when the RAM preview is carrying the frames, so the
        same wall-clock speed needs half the stride and skips half as many frames (see
        PLAY_TICK_MS_PREVIEW). A share read cannot keep up with either rate, so when the
        preview is not available there is nothing to gain from the faster tick."""
        try:
            if self._proxy_usable() and self._proxy_covered():
                return PLAY_TICK_MS_PREVIEW
        except Exception:
            pass
        return PLAY_TICK_MS

    def play(self):
        in_per_cam = self._is_multi_cam() and bool(self._per_cam_rows)
        # Set BEFORE start(): the interval of a running QTimer only takes effect on the
        # next fire, and _autoplay_step reads it back to size its own stride.
        self.play_timer.setInterval(self._play_tick_ms())
        if in_per_cam:
            master = self._per_cam_master_idx
            if master < 0 or master >= len(self._cam_ts) or not self._cam_ts[master]:
                return
            cam_ts = self._cam_ts[master]
            cur = self._cam_current_idx[master] if master < len(self._cam_current_idx) else 0
            self._play_master_frame = max(0, min(len(cam_ts) - 1, cur))
            self._play_frame_acc = 0.0
            self._is_playing = True; self._is_scrubbing = False; self._reset_motion_tracking()
            self.btn_play.setEnabled(False); self.btn_stop.setEnabled(True); self.play_timer.start()
            return
        if not self.items: return
        if self.current_idx is not None:
            self.play_time_ns = self.items[self.current_idx].ts_ns
        else:
            self.play_time_ns = self._slider_to_time_ns(self.slider.value())
            self._display_exact_index(self._time_to_nearest_index(self.play_time_ns), self.play_time_ns, False)
        self._play_frame_acc = 0.0
        self._is_playing = True; self._is_scrubbing = False; self._reset_motion_tracking()
        self.btn_play.setEnabled(False); self.btn_stop.setEnabled(True); self.play_timer.start()

    def stop(self):
        self._is_playing = False
        if self.play_timer.isActive(): self.play_timer.stop()
        self.btn_play.setEnabled(bool(self.items)); self.btn_stop.setEnabled(False)
        in_per_cam = self._is_multi_cam() and bool(self._per_cam_rows)
        if not in_per_cam and self.items and self.current_idx is not None:
            self._display_exact_index(self.current_idx, self.items[self.current_idx].ts_ns, True)
        # Playback paints at PLAY_MAX_SIDE / from the preview layer — bring the frame
        # it stopped on up to native resolution.
        self._schedule_refine()
        self._schedule_prefetch_after_idle(); self._reset_motion_tracking()
        # Playback ran the sweep at PROXY_DRAG_WORKERS — back to full speed, after a short
        # grace so the frame it stopped on wins the first reads (see _proxy_idle_grace).
        self._proxy_idle_grace()

    def _autoplay_step(self):
        if not self._is_playing: return
        in_per_cam = self._is_multi_cam() and bool(self._per_cam_rows)
        if in_per_cam:
            master = self._per_cam_master_idx
            if master < 0 or master >= len(self._cam_ts) or not self._cam_ts[master]:
                self.stop(); return
            cam_ts = self._cam_ts[master]
            n = len(cam_ts)
            pct = self._current_play_pct_per_s()
            # The REAL tick, not the nominal constant: _play_tick_ms doubles the rate when
            # the preview is carrying the frames, and reading PLAY_TICK_MS regardless would
            # then advance twice as fast as the % asks for.
            self._play_frame_acc += (pct / 100.0) * n * (self.play_timer.interval() / 1000.0)
            if self._play_frame_acc < 1.0:
                return
            skip = min(int(self._play_frame_acc), 50)
            self._play_frame_acc -= skip
            cur = getattr(self, '_play_master_frame', self._cam_current_idx[master] if master < len(self._cam_current_idx) else 0)
            new_frame = cur + skip
            at_end = new_frame >= n - 1
            new_frame = min(n - 1, new_frame)
            self._play_master_frame = new_frame
            new_ts = cam_ts[new_frame]
            row = self._per_cam_rows[master]
            sv = self._per_cam_ts_to_slider(master, new_ts)
            row.set_value(sv)
            # Through the navigation tick, like every other per-camera move. Calling
            # _per_cam_display_one + _per_cam_sync_slaves directly here meant N full display
            # passes per playback tick, each with its own stale-mark and diff-stats pass —
            # the same O(cameras^2) label work that made the drag crawl. It also means a
            # tick that overruns coalesces instead of queueing.
            self._nav_request(master, new_ts, new_frame)
            if at_end:
                self.stop()
            return
        if not self.items: return
        if self.current_idx is None: self.stop(); return

        n = len(self.items)
        pct = self._current_play_pct_per_s()

        self._play_frame_acc += (pct / 100.0) * n * (self.play_timer.interval() / 1000.0)
        if self._play_frame_acc < 1.0:
            return

        # Kolik snímků máme přeskočit — max 50 (dostatečné i pro 20%/s na 3000 snímcích)
        skip = min(int(self._play_frame_acc), 50)
        self._play_frame_acc -= skip

        end_idx = n - 1
        if self.mark_b_ns is not None:
            end_idx = min(n - 1, self._time_to_nearest_index(self.mark_b_ns))
        new_idx = min(end_idx, self.current_idx + skip)
        if new_idx >= end_idx:
            self._play_show(new_idx)
            self.stop()
            return

        self._play_show(new_idx)

    def _play_show(self, new_idx: int):
        """Zobrazí snímek při přehrávání — z cache nebo spustí load."""
        self.current_idx = new_idx
        self.target_idx = new_idx
        self.play_time_ns = self.items[new_idx].ts_ns

        sv = self._time_to_slider_value(self.items[new_idx].ts_ns)
        self.slider.blockSignals(True); self.slider.setValue(sv); self.slider.blockSignals(False)
        self._set_info_for(new_idx, self.play_time_ns)
        # Move the time-axis cursor with the frame. Only _display_exact_index used to do
        # this, which is never on the playback path — so the axis cursor sat frozen at
        # wherever playback started and there was no visible progress on the timeline.
        self.tickbar.set_cursor(self.play_time_ns)

        max_side = PLAY_MAX_SIDE_FAST
        brighten = 1 if self.cb_bright.isChecked() else 0
        gradient_id = self.gradient_cb.currentIndex()
        subtract = self.cb_subtract.isChecked()
        ref = self._ref_arr_for(max_side) if subtract else None
        sub_thr, sub_off = self._sub_params(ref)
        bc = self._bc()
        # _ck(new_idx), not the raw index: every other render-key site uses the absolute,
        # trim-proof frame number, and this one did not. Identical while _items_offset is 0,
        # but after any live-mode trim playback would read and write pixmaps under keys that
        # name a different frame than the rest of the app — losing every cross-path cache
        # hit and, worse, capable of showing a cached frame that is not the one asked for.
        key = (self._ck(new_idx), max_side, brighten, gradient_id, bc,
               id(ref) if ref is not None else None, sub_thr, sub_off)

        cached = self.cache.get(key)
        if cached is not None and not cached.isNull():
            self.img_view.set_pixmap(cached)
            self._diag_cach += 1
            self._update_diff_stats(key)
            return

        # Preloaded preview → playback runs at the timer's rate instead of the
        # share's. stop() then refines the frame it ended on.
        if self._proxy_try_paint(new_idx):
            return

        self._display_req_id += 1
        self._want_display_req[key] = self._display_epoch
        if key not in self._inflight:
            if len(self._inflight) < 6:
                self._inflight.add(key)
                self.load_pool.start(LoadTask(
                    self._gen, self._display_req_id, new_idx,
                    self.items[new_idx].path, max_side, brighten, gradient_id,
                    self.load_signals, bc, ref, sub_thr, sub_off, key=key))

    # ================================================================ STEP FRAME
    def step_frame(self, delta_idx):
        if self._is_playing: self.stop()
        # Per-cam slider mode: step the master camera, sync slaves
        if self._is_multi_cam() and self._per_cam_rows:
            self._per_cam_step(delta_idx)
            return
        if not self.items: return
        j = (self.current_idx + delta_idx) if self.current_idx is not None \
            else self._time_to_nearest_index(self._slider_to_time_ns(self.slider.value()))
        j = max(0, min(len(self.items) - 1, j))
        if self._is_multi_cam():
            self._display_multicam_index(j, update_slider=True)
        else:
            self._display_exact_index(j, self.items[j].ts_ns, update_slider=True)
        self._schedule_refine()
        self._schedule_prefetch_after_idle()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:  self.step_frame(-1); return
        if event.key() == Qt.Key.Key_Right: self.step_frame(+1); return
        if event.key() == Qt.Key.Key_F11:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._toggle_watcher_mode()
            else:
                self._toggle_focus_mode()
            return
        if event.key() == Qt.Key.Key_Escape and self._focus_mode:
            self._toggle_focus_mode(); return
        if event.key() == Qt.Key.Key_Escape and self._watcher_mode:
            self._toggle_watcher_mode(); return
        super().keyPressEvent(event)

    @staticmethod
    def _win32_set_title_bar(hwnd: int, visible: bool):
        """Schová/ukáže záhlaví a resize border pomocí Win32 API.
        HWND se nepřebuduje → close button funguje normálně."""
        try:
            import ctypes
            GWL_STYLE    = -16
            WS_CAPTION   = 0x00C00000  # title bar
            WS_THICKFRAME = 0x00040000 # resize border (způsobuje viditelné okraje)
            SWP_FLAGS    = 0x0027      # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            if visible:
                style |= WS_CAPTION | WS_THICKFRAME
            else:
                style &= ~(WS_CAPTION | WS_THICKFRAME)  # bez záhlaví a bez viditelných okrajů
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
            ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
        except Exception:
            pass

    def _toggle_focus_mode(self):
        """F11: hlavní okno se přemění na plovoucí focus view (bez záhlaví).
        Záhlaví se skryje přes Win32 API — HWND se neobnoví, křížek funguje.
        F11 nebo ESC vrátí záhlaví a layout zpět."""
        self._focus_mode = not self._focus_mode
        show = not self._focus_mode
        win = self.window()
        from PySide6.QtWidgets import QTabWidget, QStatusBar

        # Skryj/ukaž vše co není kamera: levý panel, tab bar, status bar, slidery
        self._left_col.setVisible(show)
        in_multi = self._is_multi_cam() and bool(self._per_cam_rows)
        self.slider.setVisible(show and not in_multi)
        self.tickbar.setVisible(show)
        self._per_cam_scroll.setVisible(show and in_multi)
        tab_w = win.findChild(QTabWidget)
        if tab_w is not None:
            tab_w.tabBar().setVisible(show)
        sb = win.findChild(QStatusBar)
        if sb is not None:
            sb.setVisible(show)

        if self._focus_mode:
            # Save overlay relative positions before window resizes so we can
            # restore them exactly afterwards (ensure_inside_parent would shift them).
            self._focus_overlay_rel: dict = {}
            for _ov in (getattr(self, "_pv_overlay", None),
                        getattr(self, "_pv_overlay_multi", None)):
                if _ov is not None:
                    p = _ov.parentWidget()
                    if p and p.width() > 0 and p.height() > 0:
                        self._focus_overlay_rel[id(_ov)] = (
                            _ov.x() / p.width(),
                            _ov.y() / p.height(),
                        )

            self._root_layout.setContentsMargins(0, 0, 0, 0)
            self._root_layout.setSpacing(0)
            if tab_w is not None:
                tab_w.setStyleSheet("QTabWidget::pane { border: none; margin: 0; padding: 0; }")
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
            self._focus_mode_was_maximized = win.isMaximized() or win.isFullScreen()
            self._focus_mode_geom = win.saveGeometry()
            if self._focus_mode_was_maximized:
                win.showNormal()
            # Skryj záhlaví přes Win32 — bez přebudování HWND
            self._win32_set_title_bar(int(win.winId()), visible=False)
            self._focus_drag_start = None
            # Nainstaluj eventFilter pro ESC a drag-to-move
            QApplication.instance().installEventFilter(self)
            # Resize na ~65 % × 70 % obrazovky
            screen = win.screen().availableGeometry()
            fw = int(screen.width()  * 0.65)
            fh = int(screen.height() * 0.70)
            win.resize(fw, fh)
            win.move(screen.center().x() - fw // 2, screen.center().y() - fh // 2)
        else:
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self._root_layout.setSpacing(8)
            if tab_w is not None:
                tab_w.setStyleSheet("")
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
            # Vrať záhlaví přes Win32 a odinstaluj event filter
            self._win32_set_title_bar(int(win.winId()), visible=True)
            QApplication.instance().removeEventFilter(self)
            if hasattr(self, "_focus_mode_geom"):
                win.restoreGeometry(self._focus_mode_geom)
            if getattr(self, "_focus_mode_was_maximized", False):
                win.showMaximized()

        def _restore_overlay_positions():
            self._pv_update_overlay()
            rel = getattr(self, "_focus_overlay_rel", {})
            for _ov in (getattr(self, "_pv_overlay", None),
                        getattr(self, "_pv_overlay_multi", None)):
                if _ov is not None and id(_ov) in rel:
                    p = _ov.parentWidget()
                    if p and p.width() > 0 and p.height() > 0:
                        rx, ry = rel[id(_ov)]
                        nx = max(0, min(int(rx * p.width()),  p.width()  - _ov.width()))
                        ny = max(0, min(int(ry * p.height()), p.height() - _ov.height()))
                        _ov.move(nx, ny)

        QTimer.singleShot(0, _restore_overlay_positions)

    def _toggle_watcher_mode(self):
        self._watcher_mode = not self._watcher_mode
        show = not self._watcher_mode
        # Show/hide left panel, slider, tickbar
        self._left_col.setVisible(show)
        # In multi-cam mode the global slider is replaced by per-cam sliders — don't restore it
        in_multi = self._is_multi_cam() and bool(self._per_cam_rows)
        self.slider.setVisible(show and not in_multi)
        self.tickbar.setVisible(show)           # tickbar vždy viditelný (cursor line)
        self._per_cam_scroll.setVisible(show and in_multi)
        # Hide/show tab bar and status bar (they live in the main window)
        win = self.window()
        from PySide6.QtWidgets import QTabWidget, QStatusBar
        tab_w = win.findChild(QTabWidget)
        if tab_w is not None:
            tab_w.tabBar().setVisible(show)
        sb = win.findChild(QStatusBar)
        if sb is not None:
            sb.setVisible(show)
        # In watcher mode remove all borders/margins so image fills the screen edge-to-edge
        if self._watcher_mode:
            self._root_layout.setContentsMargins(0, 0, 0, 0)
            self._root_layout.setSpacing(0)
            if tab_w is not None:
                tab_w.setStyleSheet("QTabWidget::pane { border: none; margin: 0; padding: 0; }")
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
        else:
            self._root_layout.setContentsMargins(8, 8, 8, 8)
            self._root_layout.setSpacing(8)
            if tab_w is not None:
                tab_w.setStyleSheet("")   # restore global stylesheet
            win.centralWidget().setContentsMargins(0, 0, 0, 0)
            self.setContentsMargins(0, 0, 0, 0)
        # Install/remove key event filter on the window so ESC works even when
        # a child widget has focus
        if self._watcher_mode:
            win.installEventFilter(self)
            win.showFullScreen()
        else:
            win.removeEventFilter(self)
            win.showNormal()
            win.showMaximized()
        # Refresh PV overlay — it may be hidden because its parent (_left_col) was hidden
        QTimer.singleShot(0, self._pv_update_overlay)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent, QRect
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self._focus_mode:
                self._toggle_focus_mode()
                return True
            if self._watcher_mode:
                self._toggle_watcher_mode()
                return True

        if not self._focus_mode:
            return super().eventFilter(obj, event)

        # ── Focus mode: resize at edges, drag in center, consume camera clicks ──
        _RM = 8   # resize margin in pixels
        ev_type = event.type()
        win = self.window()

        if ev_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            gpos = event.globalPosition().toPoint()
            # Check if clicking on a PV overlay — handle drag entirely here using
            # global coords so child-widget event routing cannot interfere.
            for _ov in (getattr(self, "_pv_overlay", None),
                        getattr(self, "_pv_overlay_multi", None)):
                if _ov is not None and _ov.isVisible():
                    _ov_tl = _ov.mapToGlobal(QPoint(0, 0))
                    if QRect(_ov_tl, _ov.size()).contains(gpos):
                        # Let the overlay handle its OWN drag: by NOT consuming this
                        # press, Qt establishes the implicit mouse grab on the panel
                        # and its proven mouse handlers move it reliably. We only flag
                        # the gesture so the window move/resize logic stays out of it.
                        self._focus_click_on_overlay = True
                        self._focus_overlay_ref      = _ov
                        return False
            self._focus_click_on_overlay = False
            self._focus_overlay_ref      = None

            r = win.geometry()
            rx = gpos.x() - r.left();  ry = gpos.y() - r.top()
            nl = rx < _RM;  nt = ry < _RM
            nr = r.width()  - rx < _RM
            nb = r.height() - ry < _RM
            if nl or nt or nr or nb:
                self._focus_resize_edge  = (nl, nt, nr, nb)
                self._focus_resize_gpos0 = gpos
                self._focus_resize_rect0 = QRect(r)
                self._focus_drag_start   = None
            else:
                self._focus_resize_edge = None
                self._focus_drag_start  = gpos
            return True

        elif ev_type == QEvent.Type.MouseMove:
            gpos = event.globalPosition().toPoint()
            r = win.geometry()
            rx = gpos.x() - r.left();  ry = gpos.y() - r.top()
            nl = rx < _RM;  nt = ry < _RM
            nr = r.width()  - rx < _RM
            nb = r.height() - ry < _RM

            if event.buttons() & Qt.MouseButton.LeftButton:
                if getattr(self, '_focus_click_on_overlay', False):
                    # The overlay's own mouse handlers move it — never drag the window.
                    return False
                edge = getattr(self, '_focus_resize_edge', None)
                if edge is not None:
                    _nl, _nt, _nr, _nb = edge
                    r0 = self._focus_resize_rect0
                    g0 = self._focus_resize_gpos0
                    dx = gpos.x() - g0.x();  dy = gpos.y() - g0.y()
                    nr_rect = QRect(r0)
                    if _nr: nr_rect.setRight(r0.right() + dx)
                    if _nb: nr_rect.setBottom(r0.bottom() + dy)
                    if _nl: nr_rect.setLeft(r0.left() + dx)
                    if _nt: nr_rect.setTop(r0.top() + dy)
                    win.setGeometry(nr_rect)
                    return True
                elif getattr(self, '_focus_drag_start', None) is not None:
                    delta = gpos - self._focus_drag_start
                    if delta.manhattanLength() > 6:
                        self._focus_drag_start = None
                        wh = win.windowHandle()
                        if wh:
                            wh.startSystemMove()
                        return True
            else:
                # Cursor feedback: overlay → SizeAllCursor; edges → resize; center → arrow
                over_overlay = False
                for _ov in (getattr(self, "_pv_overlay", None),
                            getattr(self, "_pv_overlay_multi", None)):
                    if _ov is not None and _ov.isVisible():
                        _ov_tl = _ov.mapToGlobal(QPoint(0, 0))
                        if QRect(_ov_tl, _ov.size()).contains(gpos):
                            win.setCursor(Qt.CursorShape.SizeAllCursor)
                            over_overlay = True
                            break
                if not over_overlay:
                    if nl and nt:   win.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    elif nr and nb: win.setCursor(Qt.CursorShape.SizeFDiagCursor)
                    elif nl and nb: win.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    elif nt and nr: win.setCursor(Qt.CursorShape.SizeBDiagCursor)
                    elif nl or nr:  win.setCursor(Qt.CursorShape.SizeHorCursor)
                    elif nt or nb:  win.setCursor(Qt.CursorShape.SizeVerCursor)
                    else:           win.setCursor(Qt.CursorShape.ArrowCursor)

        elif ev_type == QEvent.Type.MouseButtonRelease:
            if getattr(self, '_focus_click_on_overlay', False):
                # Overlay drag finished — let its own release handler clear state.
                self._focus_click_on_overlay = False
                self._focus_overlay_ref      = None
                return False
            self._focus_drag_start  = None
            self._focus_resize_edge = None
            win.setCursor(Qt.CursorShape.ArrowCursor)

        return super().eventFilter(obj, event)

    # ================================================================ TIMESTAMPS
    def _save_current_timestamp(self):
        if self.current_idx is None or not self.items: return
        idx = self.current_idx
        if self._real_ts_list and idx < len(self._real_ts_list):
            ts_ns = self._real_ts_list[idx]
        elif self._is_multi_cam() and self.play_time_ns is not None:
            ts_ns = self.play_time_ns
        else:
            ts_ns = self.items[idx].ts_ns
        label = fmt_prague_full_from_ns(ts_ns)

        # Zkontroluj duplicity (±10ms)
        for existing_ts, _ in self._saved_timestamps:
            if abs(existing_ts - ts_ns) < 10_000_000:
                self.lbl_ts_status.setText(f"Already saved: {label}")
                return

        self._saved_timestamps.append((ts_ns, label))
        self.btn_goto_ts.setEnabled(True)
        self.btn_clear_ts.setEnabled(True)
        n = len(self._saved_timestamps)
        self.lbl_ts_status.setText(
            f"{n} timestamp{'s' if n > 1 else ''} saved.\nLast: {label}")

    def _goto_saved_timestamp(self):
        if not self._saved_timestamps or not self.items: return

        if len(self._saved_timestamps) == 1:
            target_ts, label = self._saved_timestamps[0]
        else:
            # Vyber ze seznamu
            from PySide6.QtWidgets import QInputDialog
            labels = [f"{i+1}: {lbl}" for i, (_, lbl) in
                      enumerate(self._saved_timestamps)]
            choice, ok = QInputDialog.getItem(
                self, "Go to Timestamp",
                "Select saved timestamp:", labels, 0, False)
            if not ok: return
            idx = labels.index(choice)
            target_ts, label = self._saved_timestamps[idx]

        # Najdi nejbližší snímek
        best_idx = self._time_to_nearest_index(target_ts)
        best_ts = self.items[best_idx].ts_ns
        diff_ms = abs(best_ts - target_ts) / 1_000_000

        self._display_exact_index(best_idx, best_ts, update_slider=True)
        self.lbl_ts_status.setText(
            f"Jumped to: {fmt_prague_full_from_ns(best_ts)}\n"
            f"Target:    {label}\n"
            f"Δt = {diff_ms:.1f} ms")

    def _clear_timestamps(self):
        self._saved_timestamps.clear()
        self.btn_goto_ts.setEnabled(False)
        self.btn_clear_ts.setEnabled(False)
        self.lbl_ts_status.setText("No timestamps saved.")

    def run_pointing_analysis(self):
        if not self.items: return

        # In multi-cam mode use only items from the selected camera
        if self._is_multi_cam() and self._cam_items:
            cam_idx = self._sc_cam_combo.currentIndex()
            if 0 <= cam_idx < len(self._cam_items):
                source_items = self._cam_items[cam_idx]
            else:
                source_items = self.items
        else:
            source_items = self.items

        # Vyber snímky — mezi marky nebo všechny
        if self.mark_a_ns is not None and self.mark_b_ns is not None:
            a, b = min(self.mark_a_ns, self.mark_b_ns), max(self.mark_a_ns, self.mark_b_ns)
            items = [it for it in source_items if a <= it.ts_ns <= b]
        else:
            items = source_items

        if not items:
            QMessageBox.information(self, "Pointing Analysis", "No images to analyse."); return

        # M is a plain multiplier applied to the measured offsets (1.0 = raw pixels);
        # it is applied once to the result arrays in _on_pointing_finished.
        self._pointing_m = self.pointing_m_sb.value()
        pixel_mm = self._pointing_m
        threshold = self.pointing_threshold_sb.value()

        # Stop replay if it's running before starting new analysis
        if self.btn_pointing_live.isChecked():
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()

        self.btn_pointing.setEnabled(False)
        self.btn_pointing_cancel.setVisible(True)
        total = len(items)
        if total > 20000:
            step = total // 5000
        elif total > 5000:
            step = total // 3000
        elif total > 1000:
            step = total // 1000
        else:
            step = 1
        n_sampled = len(range(0, total, step))
        status = f"Analysing {n_sampled} / {total} frames"
        if step > 1:
            status += f" (every {step}th frame)"
        self.lbl_pointing_status.setText(status)

        signals = PointingAnalysisSignals()
        signals.progress.connect(self._on_pointing_progress)
        signals.finished.connect(self._on_pointing_finished)
        signals.cancelled.connect(self._on_pointing_cancelled)

        task = PointingAnalysisTask(items, threshold, pixel_mm, signals)
        self._pointing_task = task
        self._set_busy(True)
        self.analysis_pool.start(task)

    def _cancel_pointing(self):
        if self._pointing_task is not None:
            self._pointing_task.cancel()

    def _on_pointing_progress(self, done, total):
        self.lbl_pointing_status.setText(f"Analysing {done} / {total}…")

    def _on_pointing_cancelled(self):
        self._pointing_task = None
        self._set_busy(False)
        self.btn_pointing.setEnabled(bool(self.items))
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_pointing_cancel.setVisible(False)
        self.lbl_pointing_status.setText("Cancelled.")

    def _on_pointing_finished(self, results):
        self._pointing_task = None
        self._set_busy(False)
        self.btn_pointing.setEnabled(bool(self.items))
        self.btn_pointing_live.setEnabled(bool(self.items))
        self._sc_set_enabled(bool(self.items) or bool(self._cam_names))
        self.btn_pointing_cancel.setVisible(False)

        if not results:
            self.lbl_pointing_status.setText("No results — try lowering the threshold."); return

        ts_arr  = np.array([r[0] for r in results], dtype=np.int64)
        # r[1], r[2] are centroid offsets from image centre in original pixels;
        # scale by the user multiplier M (1.0 = raw pixels).
        m = getattr(self, '_pointing_m', 1.0)
        cx_px   = np.array([r[1] for r in results]) * m
        cy_px   = np.array([r[2] for r in results]) * m
        # Sensor extent must be in the same (scaled) unit as the offsets above.
        img_w   = int(results[0][3] * m) if len(results[0]) > 3 else None
        img_h   = int(results[0][4] * m) if len(results[0]) > 4 else None

        n = len(results)
        sx = float(np.std(cx_px))
        sy = float(np.std(cy_px))
        self.lbl_pointing_status.setText(
            f"{n} shots  σX={sx:.1f} px  σY={sy:.1f} px")

        self.pointing_panel.setVisible(True)
        self.pointing_panel.plot(cx_px, cy_px, n,
                                  ts_ns=ts_arr.astype(np.float64),
                                  ts_ns_int=ts_arr,
                                  img_w=img_w, img_h=img_h)
        self.btn_pointing_save.setEnabled(True)
        self.btn_pointing_path.setEnabled(True)
        self.btn_pointing_path.setText("〰 Show Path")
        self.btn_pointing_close.setEnabled(True)
        self.btn_pointing_select.setEnabled(True)
        # New dataset — panel.plot() reset Delete mode; mirror it on the button
        self.btn_pointing_select.setChecked(False)
        self._style_pointing_select_btn(False)
        self.btn_pointing_restore.setEnabled(False)

    def _on_pointing_live_toggled(self, checked: bool):
        if checked:
            self.btn_pointing_live.setText("⏹ Stop")
            self.btn_pointing_live.setStyleSheet(
                "background-color: #c00; color: white; font-weight: bold;")
            self._start_pointing_replay()
        else:
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()

    def _start_pointing_replay(self):
        """Start stepping through pointing analysis results frame by frame."""
        panel = self.pointing_panel
        if panel._ts_int is None or len(panel._ts_int) == 0:
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            return
        # Start from index 0 in the pointing results
        self._pointing_replay_idx = 0
        # Show the empty graph frame immediately — points then appear one by
        # one as the replay steps through them.
        panel.set_replay_ts(int(panel._ts_int[0]) - 1)
        if not hasattr(self, '_pointing_replay_timer'):
            self._pointing_replay_timer = QTimer(self)
            self._pointing_replay_timer.timeout.connect(self._pointing_replay_step)
        fps = max(0.1, self._pointing_replay_fps_sb.value() / 100.0 * 200.0)
        self._pointing_replay_timer.start(int(1000 / fps))

    def _stop_pointing_replay(self):
        if hasattr(self, '_pointing_replay_timer'):
            self._pointing_replay_timer.stop()
        # Show all points again
        self.pointing_panel.set_replay_ts(None)

    def _pointing_replay_step(self):
        """Advance one step in the pointing replay."""
        panel = self.pointing_panel
        if panel._ts_int is None:
            self._stop_pointing_replay()
            return
        idx = getattr(self, '_pointing_replay_idx', 0)
        n = len(panel._ts_int)
        # Skip points deleted from the analysis — replay only frames that
        # actually contributed a point to the graph.
        if panel._mask is not None:
            while idx < n and not bool(panel._mask[idx]):
                idx += 1
        if idx >= n:
            # Replay finished — stop and reset button
            self._pointing_replay_timer.stop()
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            return
        ts_ns = int(panel._ts_int[idx])
        # Show image for this timestamp
        if self.items and self.ts_list is not None:
            img_idx = self._time_to_nearest_index(ts_ns)
            if 0 <= img_idx < len(self.items):
                self._pointing_nav_from_click = True
                self._display_exact_index(img_idx, self.items[img_idx].ts_ns, update_slider=True)
                self._pointing_nav_from_click = False
        # Update replay timestamp on panel to show points up to this one
        panel.set_replay_ts(ts_ns)
        self._pointing_replay_idx = idx + 1
        # Update timer interval in case fps changed
        fps = max(0.1, self._pointing_replay_fps_sb.value() / 100.0 * 200.0)
        self._pointing_replay_timer.setInterval(int(1000 / fps))

    def _save_pointing_plot(self):
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save pointing plot", "pointing_stability.png",
            "PNG Images (*.png);;PDF (*.pdf)",
            options=QFileDialog.Option(0))
        if not dst: return
        try:
            self.pointing_panel.save_figure(dst)
            QMessageBox.information(self, "Saved", f"Plot saved to:\n{dst}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _toggle_pointing_path(self):
        showing = self.pointing_panel.toggle_path()
        self.btn_pointing_path.setText(
            "〰 Hide Path" if showing else "〰 Show Path")

    def _close_pointing_panel(self):
        # Stop replay if running
        if self.btn_pointing_live.isChecked():
            self.btn_pointing_live.blockSignals(True)
            self.btn_pointing_live.setChecked(False)
            self.btn_pointing_live.blockSignals(False)
            self.btn_pointing_live.setText("▶ Replay")
            self.btn_pointing_live.setStyleSheet("")
            self._stop_pointing_replay()
        self.pointing_panel.setVisible(False)
        self.pointing_panel.set_select_mode(False)
        self.btn_pointing_close.setEnabled(False)
        self.btn_pointing_path.setEnabled(False)
        self.btn_pointing_save.setEnabled(False)
        self.btn_pointing_select.setEnabled(False)
        self.btn_pointing_select.setChecked(False)
        self._style_pointing_select_btn(False)
        self.btn_pointing_restore.setEnabled(False)
        self.btn_pointing_path.setText("〰 Show Path")

    def _toggle_pointing_select(self, checked: bool):
        active = self.pointing_panel.set_select_mode(checked)
        if checked and not active:
            self.btn_pointing_select.setChecked(False)
        self._style_pointing_select_btn(active)

    def _style_pointing_select_btn(self, on: bool):
        self.btn_pointing_select.setText("🗑 Delete mode ON" if on
                                         else "🗑 Delete mode")
        self.btn_pointing_select.setStyleSheet(
            "background-color: #c62828; color: white; font-weight: bold;"
            if on else "")

    def _on_pointing_region_deleted(self):
        # Delete mode stays active — each drag deletes immediately; the button
        # keeps its checked state until the user toggles it off.
        # update status label with new N
        panel = self.pointing_panel
        if panel._mask is not None and panel._cx is not None:
            n = int(panel._mask.sum())
            cx_v = panel._cx[panel._mask]
            cy_v = panel._cy[panel._mask]
            sx = float(np.std(cx_v)) if n else 0.0
            sy = float(np.std(cy_v)) if n else 0.0
            self.lbl_pointing_status.setText(
                f"{n} shots  σX={sx:.2f} µrad  σY={sy:.2f} µrad")
        n_deleted = int((~panel._mask).sum()) if panel._mask is not None else 0
        self.btn_pointing_restore.setEnabled(n_deleted > 0)

    def _restore_pointing_points(self):
        self.pointing_panel.restore_all_points()

    # ── Spatial Contrast ─────────────────────────────────────────────────────

    def _sc_set_enabled(self, enabled: bool):
        self._btn_sc_measure.setEnabled(enabled)
        self._btn_sc_auto_thr.setEnabled(enabled)
        self._btn_sc_hist.setEnabled(enabled)
        self._btn_sc_draw.setEnabled(enabled)

    def _on_sc_threshold_changed(self, value: int):
        """Re-run preview immediately when threshold changes (if a preview exists)."""
        if self._sc_preview_pixmap is not None:
            if self._sc_task_running:
                self._sc_pending = True   # queue a re-run for when current task finishes
            else:
                self._run_spatial_contrast()

    def _run_sc_auto_threshold(self):
        """Compute Otsu threshold from the current image and update the spinbox."""
        if self._sc_task_running:
            return
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(img_path))
            if pil.mode in ("I", "I;16", "I;16B"):
                arr = np.asarray(pil.convert("I"), dtype=np.float32).copy()
            else:
                arr = np.asarray(pil.convert("L"), dtype=np.float32).copy()
            arr_max = float(arr.max())
            bit_depth = 65535.0 if arr_max > 255 else 255.0
            thr_raw = _SCTask.otsu_threshold_raw(arr, bit_depth)
            # Block signal to avoid triggering re-measurement during set
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(thr_raw)
            self._sc_threshold_sb.blockSignals(False)
        except Exception:
            return
        # Now run measurement with the new threshold
        self._run_spatial_contrast()

    def _open_sc_histogram(self):
        """Open the manual-threshold histogram dialog for the current image."""
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        current_thr = self._sc_threshold_sb.value()
        try:
            from PIL import Image as _PIL
            pil = _PIL.open(str(img_path))
            arr_max = float(np.asarray(pil).max())
            bit_depth = 65535 if arr_max > 255 else 255
        except Exception:
            bit_depth = 65535
        dlg = _SCHistogramDialog(img_path, current_thr, bit_depth, self)

        # Debounce timer — re-runs SC 500 ms after last drag movement
        _debounce = QTimer(self)
        _debounce.setSingleShot(True)
        _debounce.setInterval(500)
        _pending_thr = [current_thr]

        def _on_preview(new_thr: int):
            _pending_thr[0] = new_thr
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(new_thr)
            self._sc_threshold_sb.blockSignals(False)
            _debounce.start()   # reset timer on every change

        def _on_debounce_fire():
            if not self._sc_task_running:
                self._run_spatial_contrast()
            else:
                self._sc_pending = True

        _debounce.timeout.connect(_on_debounce_fire)
        dlg.threshold_preview.connect(_on_preview)

        def _on_accepted(new_thr: int):
            _debounce.stop()
            self._sc_threshold_sb.blockSignals(True)
            self._sc_threshold_sb.setValue(new_thr)
            self._sc_threshold_sb.blockSignals(False)
            self._run_spatial_contrast()

        dlg.threshold_accepted.connect(_on_accepted)
        dlg.exec()
        _debounce.stop()

    def _open_sc_exclusion_editor(self):
        """Open the exclusion-region painter dialog."""
        if self._sc_preview_pixmap is None:
            # Need a preview first — run measurement to generate one
            self._run_spatial_contrast()
            self._sc_status_lbl.setText("Run Measure first to generate a preview.")
            return
        img_path = self._sc_current_image_path()
        if img_path is None:
            return
        # Determine original image size
        try:
            from PIL import Image as _PIL
            with _PIL.open(str(img_path)) as _p:
                orig_w, orig_h = _p.size
        except Exception:
            orig_w, orig_h = self._sc_preview_pixmap.width(), self._sc_preview_pixmap.height()

        dlg = _SCExclusionEditor(self._sc_preview_pixmap, (orig_h, orig_w),
                                  img_path=img_path, existing_mask=None, parent=self)
        dlg.exclusion_confirmed.connect(self._on_sc_exclusion_set)
        dlg.exec()

    def _on_sc_exclusion_set(self, mask: "np.ndarray | None"):
        self._sc_exclusion_mask = mask
        self._sc_exclusion_path = self._sc_current_image_path()  # tie mask to current image
        if mask is not None:
            n = int(mask.sum())
            self._sc_status_lbl.setText(f"Exclusion: {n:,} px masked — click Measure to recompute.")
        else:
            self._sc_status_lbl.setText("Exclusion cleared.")
        # Auto re-run measurement with new exclusion
        self._run_spatial_contrast()

    def _sc_cam_idx(self) -> int:
        """Return the camera index to use for SC measurement (from combo or grid selection)."""
        if (hasattr(self, '_sc_cam_row_widget') and self._sc_cam_row_widget.isVisible()
                and hasattr(self, '_sc_cam_combo') and self._sc_cam_combo.count() > 0):
            return self._sc_cam_combo.currentIndex()
        return self._multi_grid.selected_cam_index()

    def _sc_current_image_path(self) -> "Path | None":
        """Return the Path of the image currently shown in the selected camera."""
        if self._cam_items:
            idx = self._sc_cam_idx()
            items = self._cam_items[idx] if idx < len(self._cam_items) else []
            if not items:
                return None
            # Prefer the frame that matches current slider time for this camera
            if self.play_time_ns is not None and hasattr(self, '_cam_ts') and idx < len(self._cam_ts):
                cam_ts = self._cam_ts[idx]
                pos = bisect.bisect_right(cam_ts, self.play_time_ns) - 1
                cur_i = max(0, pos)
            elif hasattr(self, '_cam_current_idx') and idx < len(self._cam_current_idx):
                cur_i = self._cam_current_idx[idx]
            else:
                cur_i = len(items) - 1  # fall back to latest
            cur_i = max(0, min(cur_i, len(items) - 1))
            return items[cur_i].path
        else:
            if not self.items:
                return None
            if self.current_idx is not None:
                cur_i = max(0, min(self.current_idx, len(self.items) - 1))
            else:
                cur_i = 0
            return self.items[cur_i].path

    def _sc_current_cam_name(self) -> str:
        """Return the name of the camera currently used for SC measurement."""
        if self._cam_items:
            idx = self._sc_cam_idx()
            if idx < len(self._cam_names):
                return self._cam_names[idx]
        return ""

    def _run_spatial_contrast(self):
        if self._sc_task_running:
            self._sc_pending = True   # will re-run with latest image once current finishes
            return
        self._sc_pending = False
        img_path = self._sc_current_image_path()
        if img_path is None:
            self._sc_status_lbl.setText("No image loaded.")
            return
        self._sc_active_cam_name = self._sc_current_cam_name()
        if self._sc_active_cam_name:
            self._sc_cam_lbl.setText(f"Camera: {_strip_cam_name(self._sc_active_cam_name)}")

        # Reset exclusion mask when image changes
        if img_path != self._sc_exclusion_path:
            self._sc_exclusion_mask = None
            self._sc_exclusion_path = None

        threshold = self._sc_threshold_sb.value()

        self._sc_task_running = True
        self._sc_task_gen = self._gen   # tag task with current scan/camera generation
        self._sc_set_enabled(False)
        self._sc_status_lbl.setText("Measuring…")

        sig = _SCSignals()
        sig.preview.connect(self._on_sc_preview)
        sig.finished.connect(self._on_sc_finished)
        task = _SCTask(img_path, threshold, sig, exclusion_mask=self._sc_exclusion_mask)
        self.scan_pool.start(task)

    def _on_sc_preview(self, pm: "QPixmap"):
        if self._sc_task_gen != self._gen:
            return   # camera/scan changed while this task was running — discard stale preview
        self._sc_preview_pixmap = pm
        self._sc_preview_lbl.show()
        self._sc_preview_lbl.set_full_pixmap(pm)

    def _update_sc_preview(self):
        if self._sc_preview_pixmap is not None:
            self._sc_preview_lbl.set_full_pixmap(self._sc_preview_pixmap)

    def _on_sc_finished(self, result: dict):
        self._sc_task_running = False
        self._sc_set_enabled(True)
        # Camera/scan changed while this task was running (e.g. user picked a
        # different camera mid-measurement) — its result belongs to the camera
        # that's no longer showing, so discard it and re-measure the current one.
        stale = (self._sc_task_gen != self._gen)
        # If Measure was clicked (or threshold moved) while task was running, re-run now
        if getattr(self, '_sc_pending', False) or stale:
            self._sc_pending = False
            self._run_spatial_contrast()
            return

        if result is None:
            self._sc_status_lbl.setText("Error: could not open image.")
            return

        err = result.get("error")
        if err:
            self._sc_status_lbl.setText(err)
            self._sc_val_mean.setText("—")
            self._sc_val_min.setText("—")
            self._sc_val_max.setText("—")
            self._sc_val_sc.setText("—")
            self._sc_val_beam.setText("—")
            return

        bd = result.get("bit_depth", 255)
        cam_lbl = getattr(self, '_sc_active_cam_name', '')
        status = f"{cam_lbl}  ({bd}-bit)" if cam_lbl else f"({bd}-bit)"
        self._sc_status_lbl.setText(status)
        self._sc_val_mean.setText(f"{result['mean']:.1f}")
        self._sc_val_min.setText(f"{result['min']:.1f}")
        self._sc_val_max.setText(f"{result['max']:.1f}")

        sc = result["sc"]
        self._sc_val_sc.setText(f"{sc:.4f}" if sc != float("inf") else "∞")

        n_beam  = result["n_beam"]
        n_total = result["n_total"]
        pct = 100.0 * n_beam / n_total if n_total > 0 else 0.0
        self._sc_val_beam.setText(f"{n_beam:,} ({pct:.1f}%)")

        # Store top-N data and trigger overlay repaint
        self._sc_topn_points = list(zip(
            result.get("top_xs", []),
            result.get("top_ys", [])
        ))
        self._sc_topn_img_shape = result.get("img_shape", None)
        self._update_sc_topn_overlay()

    def _on_sc_topn_changed(self, _val):
        self._update_sc_topn_overlay()

    def _on_sc_marker_style_changed(self, _val=None):
        if self._is_multi_cam():
            iv = self._multi_grid.get_img_view(self._sc_cam_idx())
        else:
            iv = getattr(self, 'img_view', None)
        if iv is None:
            return
        iv.sc_topn_marker_radius = self._sc_marker_r_sb.value()
        iv.sc_topn_marker_thick  = self._sc_marker_thick_sb.value()
        iv.update()

    def _update_sc_topn_overlay(self):
        """Draw top-N intensity pixel markers on the correct ImageView (multi-cam or single)."""
        n = self._sc_topn_sb.value()
        pts = getattr(self, '_sc_topn_points', None)
        shape = getattr(self, '_sc_topn_img_shape', None)

        # Determine which ImageView to draw on
        if self._is_multi_cam():
            cam_idx = self._sc_cam_idx()
            iv = self._multi_grid.get_img_view(cam_idx)
        else:
            iv = self.img_view
        if iv is None:
            return

        # Clear markers from all other multi-cam views so stale markers don't linger
        if self._is_multi_cam():
            for cv in self._multi_grid._cam_views:
                if cv.img_view is not iv:
                    cv.img_view.sc_topn_points_norm = None
                    cv.img_view.update()

        if n == 0 or not pts or shape is None:
            iv.sc_topn_points_norm = None
        else:
            h, w = shape
            if w > 0 and h > 0:
                iv.sc_topn_points_norm = [
                    (px / w, py / h) for px, py in pts[:n]
                ]
            else:
                iv.sc_topn_points_norm = None
        iv.update()

    def _on_pointing_point_clicked(self, orig_idx: int):
        """Navigate to the image corresponding to the clicked pointing point."""
        panel = self.pointing_panel
        if panel._ts_int is None or orig_idx >= len(panel._ts_int):
            return
        ts_ns = int(panel._ts_int[orig_idx])
        if not self.items:
            return
        self._pointing_nav_from_click = True
        if self._is_multi_cam():
            self._display_multicam_at_time(ts_ns, update_slider=True)
        else:
            idx = self._time_to_nearest_index(ts_ns)
            if 0 <= idx < len(self.items):
                self._display_exact_index(idx, self.items[idx].ts_ns, update_slider=True)
        self._pointing_nav_from_click = False

    def _current_master_ts_ns(self) -> int | None:
        """Return current timestamp of the master camera (single-cam or multi-cam)."""
        if self._is_multi_cam():
            master = self._per_cam_master_idx
            if master >= 0 and master < len(self._cam_ts) and self._cam_ts[master]:
                # Use _cam_current_idx directly — exact frame timestamp, no slider rounding.
                if hasattr(self, "_cam_current_idx") and master < len(self._cam_current_idx):
                    fi = self._cam_current_idx[master]
                    ts = self._cam_ts[master]
                    if 0 <= fi < len(ts):
                        return ts[fi]
                # Fallback: derive from slider (original path)
                row = self._per_cam_rows[master] if master < len(self._per_cam_rows) else None
                if row is None:
                    return None
                v = row.value()
                t_raw = self._per_cam_slider_to_ts(master, v)
                frame_idx = max(0, bisect.bisect_right(self._cam_ts[master], t_raw) - 1)
                return self._cam_ts[master][frame_idx]
            return None
        if not self.items or self.current_idx is None:
            return None
        return self.items[self.current_idx].ts_ns

    def set_mark_a(self):
        ts = self._current_master_ts_ns()
        if ts is None: return
        self.mark_a_ns = ts
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns); self._update_range_ui()

    def set_mark_b(self):
        ts = self._current_master_ts_ns()
        if ts is None: return
        self.mark_b_ns = ts
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns); self._update_range_ui()

    def clear_marks(self):
        self.mark_a_ns = None; self.mark_b_ns = None
        self.tickbar.set_marks(None, None); self._update_range_ui()

    def _apply_marks_to_tickbar(self):
        """Validate stored marks against the current axis and update the tickbar.
        Marks outside the new axis are cleared so stale out-of-range marks don't show."""
        ax_min, ax_max = self.axis_min_ns, self.axis_max_ns
        if ax_max > ax_min:
            if self.mark_a_ns is not None and not (ax_min <= self.mark_a_ns <= ax_max):
                self.mark_a_ns = None
            if self.mark_b_ns is not None and not (ax_min <= self.mark_b_ns <= ax_max):
                self.mark_b_ns = None
        self.tickbar.set_marks(self.mark_a_ns, self.mark_b_ns)

    def _update_range_ui(self):
        ok = (self.mark_a_ns is not None) and (self.mark_b_ns is not None) and bool(self.items)
        self.btn_save_range.setEnabled(ok)

    # ================================================================ SAVE HELPERS
    def _show_copy_progress(self, total, text):
        self.prog.setVisible(True); self.prog.setRange(0, max(1, total)); self.prog.setValue(0)
        self.lbl_filename.setText(text)
        for w in [self.btn_open, self.btn_date, self.btn_save, self.btn_save_range,
                  self.btn_play, self.btn_stop, self.btn_prev, self.btn_next,
                  self.btn_set_a, self.btn_set_b, self.btn_clear_marks, self.btn_cal_cross]:
            w.setEnabled(False)

    def _hide_copy_progress(self):
        self.prog.setVisible(False); self.prog.setRange(0, 0)
        has = bool(self.items)
        self.btn_open.setEnabled(True); self.btn_date.setEnabled(True)
        self.btn_save.setEnabled(has and self.current_idx is not None)
        self.btn_send_workshop.setEnabled(has and self.current_idx is not None)
        self.btn_play.setEnabled(has); self.btn_stop.setEnabled(False)
        self.btn_prev.setEnabled(has); self.btn_next.setEnabled(has)
        self.btn_set_a.setEnabled(has); self.btn_set_b.setEnabled(has)
        self.btn_clear_marks.setEnabled(has); self._update_range_ui()
        if self.current_idx is not None and self.items:
            self.lbl_filename.setText(f"File: {self.items[self.current_idx].path.name}")
        else:
            self.lbl_filename.setText("File: —")

    def _dst_name_with_prague_time(self, it):
        return replace_unix_ns_with_prague_in_filename(it.path, it.ts_ns)

    def _pv_text_for_ts(self, ts_ns: "int | None") -> str:
        """Per-image PV burn-in text: archiver values at THIS frame's own timestamp,
        falling back to the live snapshot if a per-timestamp lookup yields nothing.

        May hit the network — call it from a worker thread, or pre-resolve the
        whole batch with _pv_prefetch_texts() before a GUI-thread render loop."""
        if not self.cb_save_overlay.isChecked():
            return ""
        text = pv_text_for_ts(ts_ns, list(self._pv_enabled))
        return text or self._pv_text()

    def _pv_prefetch_texts(self, ts_values: "list[int]") -> "dict[int, str]":
        """Resolve the PV burn-in text for every timestamp in ts_values on a worker
        thread and return {ts_ns: text}.

        The GUI-thread render loops (multi-cam save current / save range) used to
        call _pv_text_for_ts() per frame, so a cold cache meant HTTP requests on the
        main thread — the window froze. Doing it here keeps the archiver off the GUI
        thread entirely and guarantees identical text for identical timestamps."""
        if not (self._pv_enabled and self.cb_save_overlay.isChecked()):
            return {}
        names = list(self._pv_enabled)
        chans = [PV_CHANNEL_MAP[n] for n in names if n in PV_CHANNEL_MAP]
        uniq = sorted({int(t) for t in ts_values if t})
        if not (chans and uniq):
            return {}
        out: "dict[int, str]" = {}
        done = threading.Event()

        def _work():
            try:
                pv_warm_days(chans, uniq)
                for t in uniq:
                    out[t] = pv_text_for_ts(t, names)
            except Exception:
                pass
            finally:
                done.set()

        threading.Thread(target=_work, daemon=True).start()
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Loading PV values…", "", 0, 0, self)
        dlg.setWindowTitle("PV values")
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(300)      # no flash when everything is cached
        while not done.wait(0.05):
            QApplication.processEvents()
        dlg.close()
        return out

    def _pv_save_append_bar(self, pix: "QPixmap", dst: "str | Path",
                            ts_ns: "int | None" = None,
                            pv_text: "str | None" = None,
                            extra_text: str = "") -> bool:
        """Save pix to dst with a PV-values bar appended below the image (PIL).
        When ts_ns is given the bar shows the values present at that frame's own
        timestamp; pass pv_text to reuse an already-resolved string (see
        _pv_prefetch_texts) instead of looking it up again. extra_text is prepended
        as a further bar entry (e.g. the Shot Finder energy line). The font is
        scaled to the image width and wrapped so it stays readable. Returns True on
        success; falls back to a plain save on any error."""
        if pv_text is None:
            pv_text = self._pv_text_for_ts(ts_ns) if ts_ns is not None else self._pv_text()
        if extra_text:
            pv_text = "  |  ".join(t for t in (extra_text, pv_text) if t)
        dst = str(dst)
        if not pv_text:
            return bool(pix.save(dst))
        try:
            from PIL import Image as _PilImg
            import tempfile as _tf
            with _tf.NamedTemporaryFile(suffix=".png", delete=False) as _tmp:
                _tmp_path = Path(_tmp.name)
            pix.save(str(_tmp_path))
            _img = _PilImg.open(_tmp_path)
            render_pv_bar_below(_img, pv_text).save(dst)
            _img.close()
            _tmp_path.unlink(missing_ok=True)
            return True
        except Exception:
            return bool(pix.save(dst))

    def save_around_current(self):
        if self.current_idx is None or not self.items: return
        if self._is_multi_cam():
            self._save_multicam_current(); return
        n = self.save_around_n_sb.value()
        i0 = max(0, self.current_idx - n)
        i1 = min(len(self.items) - 1, self.current_idx + n)
        total = i1 - i0 + 1

        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir),
            options=QFileDialog.Option(0))
        if not out_dir: return
        self._last_save_dir = Path(out_dir)
        self._show_copy_progress(total, f"Saving {total} frames around current…")
        iv = self.img_view
        has_overlay = (self.cb_save_overlay.isChecked()
                       or iv.show_cross or iv.show_circle or iv.show_square)
        overlay_params = None
        if has_overlay:
            overlay_params = {
                'show_cross': iv.show_cross,
                'cross_pos_norm': iv.cross_pos_norm,
                'cross_size': self._overlay_cross_size,
                'cross_color': self._overlay_cross_color,
                'cross_thick': self._overlay_cross_thick,
                'show_circle': iv.show_circle,
                'circle_center_norm': iv.circle_center_norm,
                'circle_rx_norm': iv.circle_rx_norm,
                'circle_ry_norm': getattr(iv, 'circle_ry_norm', None),
                'circle_r_norm': getattr(iv, 'circle_r_norm', 0.1),
                'circle_color': self._overlay_circle_color,
                'circle_thick': self._overlay_circle_thick,
                'show_square': iv.show_square,
                'square_rect_norm': iv.square_rect_norm,
                'square_color': self._overlay_square_color,
                'square_thick': self._overlay_square_thick,
            }
        if overlay_params is not None:
            overlay_params['pv_text'] = self._pv_text()
        elif self._pv_text():
            overlay_params = {'pv_text': self._pv_text()}
        task = SaveRangeTask(
            self.items[i0:i1+1], Path(out_dir), self._dst_name_with_prague_time,
            gradient_id=self.gradient_cb.currentIndex(),
            brighten=self.cb_bright.isChecked(),
            overlay_params=overlay_params,
            energy_map=dict(self._sf_energy_map),
            # Per-image PV lookup (each frame gets the values at its own timestamp).
            pv_channels=({n: PV_CHANNEL_MAP[n] for n in self._pv_enabled if n in PV_CHANNEL_MAP}
                         if self.cb_save_overlay.isChecked() else {}),
            pv_units=dict(PV_UNITS),
        )
        task.save_txt = self.cb_save_metadata_txt.isChecked()
        self._save_task = task
        a = self.items[i0].ts_ns
        b = self.items[i1].ts_ns
        self._save_progress_dlg = self._show_save_range_progress_dialog(total)
        task.signals.progress.connect(self._on_save_progress)
        task.signals.finished.connect(lambda s, e: self._on_save_finished(s, e, a, b))
        self.scan_pool.start(task)

    def save_current_with_overlay(self):
        """Uloží aktuální snímek s nakresleným overlayem (cross/circle/square)."""
        if self.current_idx is None or not self.items: return
        if self._is_multi_cam():
            self._save_multicam_current(); return
        if self.img_view._pix is None or self.img_view._pix.isNull():
            QMessageBox.information(self, "Save with overlay",
                "No image displayed."); return

        it = self.items[self.current_idx]
        stem = self._dst_name_with_prague_time(it)
        stem_no_ext = Path(stem).stem
        suggested = str(self._last_save_dir / f"{stem_no_ext}_overlay.png")
        dst, _ = QFileDialog.getSaveFileName(
            self, "Save image with overlay", suggested, "PNG Images (*.png)",
            options=QFileDialog.Option(0))
        if not dst: return
        self._last_save_dir = Path(dst).parent

        # Overlay is drawn on a NATIVE render, not on the viewing pixmap — see
        # _overlay_base_pixmap.
        pix = self._overlay_base_pixmap()
        if pix is None or pix.isNull():
            QMessageBox.information(self, "Save with overlay",
                "No image displayed."); return
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = pix.width(), pix.height()

        if self.img_view.show_cross:
            if self.img_view.cross_pos_norm is not None:
                cx = int(self.img_view.cross_pos_norm.x() * w)
                cy = int(self.img_view.cross_pos_norm.y() * h)
            else:
                cx, cy = w // 2, h // 2
            sz = self._overlay_cross_size
            pen = QPen(self._overlay_cross_color); pen.setWidth(max(self._overlay_cross_thick, w // 500))
            painter.setPen(pen)
            sz = self._overlay_cross_size
            painter.drawLine(cx - sz, cy, cx + sz, cy)
            painter.drawLine(cx, cy - sz, cx, cy + sz)

        if self.img_view.show_circle and self.img_view.circle_center_norm is not None:
            cx = int(self.img_view.circle_center_norm.x() * w)
            cy = int(self.img_view.circle_center_norm.y() * h)
            if self.img_view.circle_rx_norm is not None:
                rx = int(self.img_view.circle_rx_norm * w)
                ry = int(self.img_view.circle_ry_norm * h)
            else:
                r = int(self.img_view.circle_r_norm * min(w, h))
                rx = ry = r
            pen = QPen(self._overlay_circle_color); pen.setWidth(max(self._overlay_circle_thick, w // 500))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)

        if self.img_view.show_square and self.img_view.square_rect_norm is not None:
            ln, tn, rn, bn = self.img_view.square_rect_norm
            sx = int(ln * w); sy = int(tn * h)
            sw = int((rn - ln) * w); sh = int((bn - tn) * h)
            pen = QPen(self._overlay_square_color); pen.setWidth(max(self._overlay_square_thick, w // 500))
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(sx, sy, sw, sh)
        painter.end()

        energy_text = self._sf_energy_map.get(it.path.name, "")
        # PV values for THIS frame's own timestamp (not the live snapshot), rendered
        # by the shared bar routine so the font/wrapping match every other save path.
        _pv_texts = self._pv_prefetch_texts([it.ts_ns])
        _pv_text = _pv_texts.get(it.ts_ns, "")
        if not self._pv_save_append_bar(pix, dst, it.ts_ns, pv_text=_pv_text,
                                        extra_text=energy_text):
            QMessageBox.critical(self, "Save failed", f"Could not save to {dst}")
            return
        _energy_str_ov = self._sf_energy_map.get(it.path.name, "")
        _copy_metadata_into_png_bg(it.path, Path(dst),
                                   save_txt=self.cb_save_metadata_txt.isChecked(),
                                   extra_meta={"Energy": _energy_str_ov} if _energy_str_ov else None)
        QMessageBox.information(self, "Saved",
            f"Saved with overlay.\nPrague Time: {fmt_prague_full_from_ns(it.ts_ns)}")

    def _get_cam_frame_for_save(self, cam_i: int):
        """Return (it, iv, cam_name) for a camera, or None if unavailable."""
        if cam_i >= len(self._cam_items):
            return None
        cam_items = self._cam_items[cam_i]
        if not cam_items:
            return None
        cam_idx = 0
        if hasattr(self, '_cam_current_idx') and cam_i < len(self._cam_current_idx):
            cam_idx = min(self._cam_current_idx[cam_i], len(cam_items) - 1)
        it = cam_items[cam_idx]
        iv = self._multi_grid.get_img_view(cam_i)
        cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam{cam_i}"
        return it, iv, cam_name

    def _render_cam_frame(self, it, iv, cam_name: str, out_dir: Path,
                          gradient_id: int, brighten: bool,
                          save_metadata_txt: bool = False,
                          pv_texts: "dict[int, str] | None" = None) -> str | None:
        """Render and save one camera frame into out_dir. Returns error string or None.

        pv_texts is the pre-resolved {ts_ns: bar text} map from _pv_prefetch_texts;
        pass it whenever this runs on the GUI thread so no archiver request can
        happen here."""
        has_shapes = iv is not None and (iv.show_cross or iv.show_circle or iv.show_square)
        is_recoloured = (gradient_id != GRADIENT_ID_DEFAULT) or brighten
        pv_text = (pv_texts.get(it.ts_ns, "") if pv_texts is not None
                   else self._pv_text_for_ts(it.ts_ns))
        has_bar = bool(pv_text)
        stem = Path(self._dst_name_with_prague_time(it)).stem
        need_view = has_shapes or is_recoloured or has_bar

        if not need_view:
            # Untouched image — saved exactly once as the original.
            dst = out_dir / f"{stem}{it.path.suffix}"
            try:
                shutil.copy2(it.path, dst)
            except Exception as e:
                return str(e)
            _copy_metadata_into_png_bg(it.path, dst, save_txt=save_metadata_txt)
            return None

        # A drawn shape (cross/circle/square) is the only real modification — when one
        # is present keep the pristine original alongside the annotated view. Palette,
        # brightness and the PV bar are not modifications, so they save a single file.
        if has_shapes:
            orig_dst = out_dir / f"{stem}{it.path.suffix}"
            try:
                shutil.copy2(it.path, orig_dst)
                _copy_metadata_into_png_bg(it.path, orig_dst, save_txt=save_metadata_txt)
            except Exception:
                pass
            dst = out_dir / f"{stem}_annotated.png"
        else:
            dst = out_dir / (f"{stem}_annotated.png" if has_bar else f"{stem}.png")

        if has_shapes and iv is not None and iv._pix is not None and not iv._pix.isNull():
            pix = iv._pix.copy()
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = pix.width(), pix.height()
            if iv.show_cross:
                cx = int(iv.cross_pos_norm.x() * w) if iv.cross_pos_norm else w // 2
                cy = int(iv.cross_pos_norm.y() * h) if iv.cross_pos_norm else h // 2
                sz = self._overlay_cross_size
                pen = QPen(self._overlay_cross_color)
                pen.setWidth(max(self._overlay_cross_thick, w // 500))
                painter.setPen(pen)
                painter.drawLine(cx - sz, cy, cx + sz, cy)
                painter.drawLine(cx, cy - sz, cx, cy + sz)
            if iv.show_circle and iv.circle_center_norm is not None:
                cx = int(iv.circle_center_norm.x() * w)
                cy = int(iv.circle_center_norm.y() * h)
                if iv.circle_rx_norm is not None:
                    rx = int(iv.circle_rx_norm * w); ry = int(iv.circle_ry_norm * h)
                else:
                    r = int(iv.circle_r_norm * min(w, h)); rx = ry = r
                pen = QPen(self._overlay_circle_color)
                pen.setWidth(max(self._overlay_circle_thick, w // 500))
                painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
            if iv.show_square and iv.square_rect_norm is not None:
                ln, tn, rn, bn = iv.square_rect_norm
                sx = int(ln * w); sy = int(tn * h)
                sw = int((rn - ln) * w); sh = int((bn - tn) * h)
                pen = QPen(self._overlay_square_color)
                pen.setWidth(max(self._overlay_square_thick, w // 500))
                painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(sx, sy, sw, sh)
            painter.end()
            if not self._pv_save_append_bar(pix, dst, it.ts_ns, pv_text=pv_text):
                return f"Could not save {dst.name}"
        else:
            img = load_image_scaled(it.path, SCRUB_MAX_SIDE, brighten, gradient_id)
            if img.isNull():
                return f"Could not load {it.path.name}"
            pix_plain = QPixmap.fromImage(img)
            if not self._pv_save_append_bar(pix_plain, dst, it.ts_ns, pv_text=pv_text):
                return f"Could not save {dst.name}"
        _copy_metadata_into_png_bg(it.path, dst, save_txt=save_metadata_txt)
        return None

    def _save_multicam_current(self):
        """Save current frame for each selected camera into a chosen folder."""
        if not self._cam_items:
            return
        selected = self._multi_grid.selected_cam_indices()
        cam_indices = selected if selected else list(range(len(self._cam_items)))
        # Collect frames first so we know what we're saving
        frames = []
        for cam_i in cam_indices:
            result = self._get_cam_frame_for_save(cam_i)
            if result:
                frames.append((cam_i, *result))
        if not frames:
            QMessageBox.information(self, "Save", "No frames to save.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir),
            options=QFileDialog.Option(0))
        if not out_dir:
            return
        out_path = Path(out_dir)
        self._last_save_dir = out_path
        gradient_id = self.gradient_cb.currentIndex()
        brighten = self.cb_bright.isChecked()
        save_txt = self.cb_save_metadata_txt.isChecked()
        pv_texts = self._pv_prefetch_texts([it.ts_ns for _, it, _, _ in frames])
        errors = []
        saved = []
        for cam_i, it, iv, cam_name in frames:
            err = self._render_cam_frame(it, iv, cam_name, out_path, gradient_id, brighten,
                                         save_metadata_txt=save_txt, pv_texts=pv_texts)
            if err:
                errors.append(f"{cam_name}: {err}")
            else:
                saved.append(cam_name)
        msg = f"Saved {len(saved)} frame(s) to:\n{out_dir}"
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors)
        QMessageBox.information(self, "Saved", msg)

    def _send_to_workshop(self):
        """Send current frame (as numpy uint8 array) to Workshop tab."""
        wk = getattr(self, "_workshop_ref", None)
        if wk is None:
            return
        try:
            if self._is_multi_cam():
                # Multi-cam: send the selected camera's current frame
                sel = self._multi_grid.selected_cam_index() if hasattr(self._multi_grid, "selected_cam_index") else -1
                if sel < 0 and self._cam_items:
                    sel = 0
                if sel < 0 or sel >= len(self._cam_items) or not self._cam_items[sel]:
                    return
                cam_items = self._cam_items[sel]
                cam_idx = 0
                if hasattr(self, "_cam_current_idx") and sel < len(self._cam_current_idx):
                    cam_idx = min(self._cam_current_idx[sel], len(cam_items) - 1)
                it = cam_items[cam_idx]
                cam_name = _strip_cam_name(self._cam_names[sel]) if sel < len(self._cam_names) else f"cam{sel}"
            else:
                if self.current_idx is None or not self.items:
                    return
                it = self.items[self.current_idx]
                cam_name = _strip_cam_name(self._cam_name) if hasattr(self, "_cam_name") and self._cam_name else Path(it.path).parent.name

            from PIL import Image as _PilImg
            import numpy as _np
            pil = _PilImg.open(str(it.path))
            if pil.mode in ("I", "I;16"):
                # Same absolute full-scale normalization as the viewer, so Workshop
                # gets exactly the intensities that are on screen.
                arr8 = _norm16_to8_full_scale(_np.array(pil))
            else:
                arr8 = _np.array(pil.convert("L"), dtype=_np.uint8)

            ts_str = fmt_prague_full_from_ns(it.ts_ns) if it.ts_ns else ""
            label = f"{cam_name}  {ts_str}".strip()
            wk.receive_image(arr8, label, source_path=it.path)
        except Exception as e:
            QMessageBox.warning(self, "Workshop", f"Could not send image:\n{e}")

    def save_current(self):
        if self._is_multi_cam():
            self._save_multicam_current(); return
        if self.current_idx is None or not self.items: return
        n_around = self.save_around_n_sb.value() if self.cb_save_around.isChecked() else 0
        if n_around > 0:
            self.save_around_current(); return
        it = self.items[self.current_idx]
        gradient_id = self.gradient_cb.currentIndex()
        save_txt = self.cb_save_metadata_txt.isChecked()
        has_overlay = (self.cb_save_overlay.isChecked() or bool(self.img_view.energy_text)
                       or self.img_view.show_cross or self.img_view.show_circle
                       or self.img_view.show_square)
        _energy_str = self._sf_energy_map.get(it.path.name, "")
        _extra_meta = {"Energy": _energy_str} if _energy_str else None

        if gradient_id == GRADIENT_ID_DEFAULT:
            if not has_overlay and not self.cb_save_original.isChecked():
                QMessageBox.information(self, "Save skipped",
                    "Saving skipped: image would be identical to the original.\n"
                    "Enable 'Save original (unmodified)' or use a palette/overlay to save.")
                return
            suggested = str(self._last_save_dir / self._dst_name_with_prague_time(it))
            dst, _ = QFileDialog.getSaveFileName(self, "Save image", suggested, f"Images (*{it.path.suffix})",
                options=QFileDialog.Option(0))
            if not dst: return
            # Ensure the saved file extension matches the source format
            _src_ext = it.path.suffix.lower()
            _dst_p = Path(dst)
            if _dst_p.suffix.lower() != _src_ext:
                dst = str(_dst_p.with_suffix(_src_ext))
            self._last_save_dir = Path(dst).parent
            try:
                shutil.copy2(it.path, Path(dst))
            except Exception as e:
                QMessageBox.critical(self, "Save failed", str(e)); return
            _copy_metadata_into_png_bg(it.path, Path(dst), save_txt=save_txt, extra_meta=_extra_meta)
        else:
            stem_no_ext = Path(self._dst_name_with_prague_time(it)).stem
            suggested = str(self._last_save_dir / f"{stem_no_ext}.png")
            dst, _ = QFileDialog.getSaveFileName(self, "Save image", suggested, "PNG Images (*.png)",
                options=QFileDialog.Option(0))
            if not dst: return
            if Path(dst).suffix.lower() not in (".png",):
                dst = str(Path(dst).with_suffix(".png"))
            self._last_save_dir = Path(dst).parent
            brighten = self.cb_bright.isChecked()
            img = load_image_scaled(it.path, SCRUB_MAX_SIDE, brighten, gradient_id)
            if img.isNull():
                QMessageBox.critical(self, "Save failed", "Could not load image."); return
            if not img.save(dst):
                QMessageBox.critical(self, "Save failed", f"Could not save to {dst}"); return
            _copy_metadata_into_png_bg(it.path, Path(dst), save_txt=save_txt, extra_meta=_extra_meta)

        # Also save annotate version alongside original if any overlay is active
        if has_overlay:
            dst_p = Path(dst)
            # Values for THIS frame's timestamp, resolved off the GUI thread — the
            # same text decides the "_annotated" suffix and gets burned in.
            _pv_ann_text = self._pv_prefetch_texts([it.ts_ns]).get(it.ts_ns, "")
            _has_bar = bool(_energy_str) or bool(_pv_ann_text)
            _ann_sfx = "_annotated" if _has_bar else ""
            ann_dst = dst_p.parent / f"{dst_p.stem}{_ann_sfx}.png"
            # Native render, not the viewing pixmap — see _overlay_base_pixmap.
            pix = self._overlay_base_pixmap()
            if pix is not None and not pix.isNull():
                painter = QPainter(pix)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                w, h = pix.width(), pix.height()
                if self.img_view.show_cross:
                    cx = int(self.img_view.cross_pos_norm.x() * w) if self.img_view.cross_pos_norm else w // 2
                    cy = int(self.img_view.cross_pos_norm.y() * h) if self.img_view.cross_pos_norm else h // 2
                    sz = self._overlay_cross_size
                    pen = QPen(self._overlay_cross_color); pen.setWidth(max(self._overlay_cross_thick, w // 500))
                    painter.setPen(pen)
                    painter.drawLine(cx - sz, cy, cx + sz, cy)
                    painter.drawLine(cx, cy - sz, cx, cy + sz)
                if self.img_view.show_circle and self.img_view.circle_center_norm is not None:
                    cx = int(self.img_view.circle_center_norm.x() * w)
                    cy = int(self.img_view.circle_center_norm.y() * h)
                    if self.img_view.circle_rx_norm is not None:
                        rx = int(self.img_view.circle_rx_norm * w); ry = int(self.img_view.circle_ry_norm * h)
                    else:
                        r = int(self.img_view.circle_r_norm * min(w, h)); rx = ry = r
                    pen = QPen(self._overlay_circle_color); pen.setWidth(max(self._overlay_circle_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(cx - rx, cy - ry, rx * 2, ry * 2)
                if self.img_view.show_square and self.img_view.square_rect_norm is not None:
                    ln, tn, rn, bn = self.img_view.square_rect_norm
                    sx = int(ln * w); sy = int(tn * h)
                    sw = int((rn - ln) * w); sh = int((bn - tn) * h)
                    pen = QPen(self._overlay_square_color); pen.setWidth(max(self._overlay_square_thick, w // 500))
                    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(sx, sy, sw, sh)
                painter.end()
                self._pv_save_append_bar(pix, ann_dst, it.ts_ns,
                                         pv_text=_pv_ann_text, extra_text=_energy_str)
                _copy_metadata_into_png_bg(it.path, ann_dst, save_txt=save_txt, extra_meta=_extra_meta)

        msg = f"Saved.\nPrague Time: {fmt_prague_full_from_ns(it.ts_ns)}"
        if has_overlay:
            msg += f"\n+ overlay: {ann_dst.name}"
        QMessageBox.information(self, "Saved", msg)

    def _save_multicam_range(self):
        """Save all frames in mark A–B range for each selected camera into a folder."""
        if self.mark_a_ns is None or self.mark_b_ns is None:
            QMessageBox.information(self, "Save range", "Set both From and To first."); return
        a, b = self.mark_a_ns, self.mark_b_ns
        if b < a: a, b = b, a
        selected = self._multi_grid.selected_cam_indices()
        cam_indices = selected if selected else list(range(len(self._cam_items)))
        def _cam_range(ts, a, b):
            """Inclusive upper bound with tolerance for cameras slightly behind master."""
            i0 = bisect.bisect_left(ts, a)
            i1 = bisect.bisect_right(ts, b)
            if i1 < len(ts):
                gap = (ts[i1 - 1] - ts[i1 - 2]) if i1 >= 2 else (ts[i1] if ts else 500_000_000)
                tolerance = max(abs(gap), 500_000_000)
                if ts[i1] - b <= tolerance:
                    i1 += 1
            return i0, i1

        # Collect every frame to save first (also gives us the total for the warning).
        jobs: "list[tuple]" = []   # (item, cam_name)
        for cam_i in cam_indices:
            if cam_i >= len(self._cam_items): continue
            cam_items = self._cam_items[cam_i]
            ts = self._cam_ts[cam_i] if (hasattr(self, '_cam_ts') and cam_i < len(self._cam_ts)) else [it.ts_ns for it in cam_items]
            cam_name = self._cam_names[cam_i] if cam_i < len(self._cam_names) else f"cam{cam_i}"
            i0, i1 = _cam_range(ts, a, b)
            for it in cam_items[i0:i1]:
                jobs.append((it, cam_name))
        total = len(jobs)
        if total == 0:
            QMessageBox.information(self, "Save range", "No frames inside From..To."); return

        # Warm the PV archiver caches in parallel, in the background, while the user
        # picks the output folder — so the per-frame resolve below is already served
        # from cache. Never blocks the UI thread.
        if self._pv_enabled and jobs and self.cb_save_overlay.isChecked():
            _pv_chs = [PV_CHANNEL_MAP[n] for n in self._pv_enabled if n in PV_CHANNEL_MAP]
            if _pv_chs:
                import threading as _thr
                _thr.Thread(target=pv_warm_days,
                            args=(_pv_chs, [it.ts_ns for it, _ in jobs]),
                            daemon=True).start()

        if total >= SAVE_RANGE_WARN_COUNT:
            reply = QMessageBox.warning(self, "Large range",
                f"{total} files selected across {len(cam_indices)} camera(s).\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir),
            options=QFileDialog.Option(0))
        if not out_dir: return
        out_path = Path(out_dir)
        self._last_save_dir = out_path
        gradient_id = self.gradient_cb.currentIndex()
        brighten = self.cb_bright.isChecked()
        save_txt = self.cb_save_metadata_txt.isChecked()

        # The render path uses QPixmap (not safe off the main thread), so the loop
        # stays here — but a modal, cancelable progress dialog keeps the UI alive.
        # Every PV value is resolved BEFORE the loop (on a worker thread), so the
        # loop itself never touches the archiver.
        pv_texts = self._pv_prefetch_texts([it.ts_ns for it, _ in jobs])
        from PySide6.QtWidgets import QProgressDialog
        prog = QProgressDialog("Saving frames…", "Cancel", 0, len(jobs), self)
        prog.setWindowTitle("Save range")
        prog.setMinimumDuration(0)
        prog.setValue(0)
        saved_total = 0
        for idx, (it, cam_name) in enumerate(jobs, 1):
            if prog.wasCanceled():
                break
            err = self._render_cam_frame(it, None, cam_name, out_path, gradient_id, brighten,
                                         save_metadata_txt=save_txt, pv_texts=pv_texts)
            if err is None:
                saved_total += 1
            prog.setValue(idx)
            if idx % 5 == 0:
                QApplication.processEvents()
        prog.close()
        QMessageBox.information(self, "Saved", f"Saved {saved_total} frame(s) to:\n{out_dir}")

    def save_range(self):
        if not self.items or self.mark_a_ns is None or self.mark_b_ns is None:
            QMessageBox.information(self, "Save range", "Set both From and To first."); return
        if self._is_multi_cam():
            self._save_multicam_range(); return
        a, b = self.mark_a_ns, self.mark_b_ns
        if b < a: a, b = b, a
        i0 = max(0, bisect.bisect_left(self.ts_list, a))
        # Use bisect_right as exclusive upper bound (consistent with _save_multicam_range).
        # The old pattern (bisect_right - 1 then slice [i0:i1+1]) can miss the last image
        # when mark_b_ns is exactly equal to the last timestamp due to clamping interactions.
        i1_excl = min(len(self.items), bisect.bisect_right(self.ts_list, b))
        if i0 >= i1_excl: QMessageBox.information(self, "Save range", "No frames inside From..To."); return
        total = i1_excl - i0

        # Pre-warm CPVA cache (in parallel) while the user picks the output folder,
        # so the background save starts without stalling on HTTP requests.
        if self._pv_enabled and self.cb_save_overlay.isChecked():
            _pv_chs = [PV_CHANNEL_MAP[n] for n in self._pv_enabled if n in PV_CHANNEL_MAP]
            _ts_vals = [it.ts_ns for it in self.items[i0:i1_excl]]
            if _pv_chs and _ts_vals:
                import threading as _thr
                _thr.Thread(target=pv_warm_days, args=(_pv_chs, _ts_vals),
                            daemon=True).start()

        if total >= SAVE_RANGE_WARN_COUNT:
            reply = QMessageBox.warning(self, "Large range",
                f"{total} files selected.\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return
        out_dir = QFileDialog.getExistingDirectory(self, "Select output folder", str(self._last_save_dir),
            options=QFileDialog.Option(0))
        if not out_dir: return
        self._last_save_dir = Path(out_dir)
        self._show_copy_progress(total, "Saving range…")
        iv = self.img_view
        has_overlay = (self.cb_save_overlay.isChecked()
                       or iv.show_cross or iv.show_circle or iv.show_square)
        overlay_params = None
        if has_overlay:
            overlay_params = {
                'show_cross': iv.show_cross,
                'cross_pos_norm': iv.cross_pos_norm,
                'cross_size': self._overlay_cross_size,
                'cross_color': self._overlay_cross_color,
                'cross_thick': self._overlay_cross_thick,
                'show_circle': iv.show_circle,
                'circle_center_norm': iv.circle_center_norm,
                'circle_rx_norm': iv.circle_rx_norm,
                'circle_ry_norm': getattr(iv, 'circle_ry_norm', None),
                'circle_r_norm': getattr(iv, 'circle_r_norm', 0.1),
                'circle_color': self._overlay_circle_color,
                'circle_thick': self._overlay_circle_thick,
                'show_square': iv.show_square,
                'square_rect_norm': iv.square_rect_norm,
                'square_color': self._overlay_square_color,
                'square_thick': self._overlay_square_thick,
            }
        if overlay_params is not None:
            overlay_params['pv_text'] = self._pv_text()
        elif self._pv_text():
            overlay_params = {'pv_text': self._pv_text()}
        task = SaveRangeTask(
            self.items[i0:i1_excl], Path(out_dir), self._dst_name_with_prague_time,
            gradient_id=self.gradient_cb.currentIndex(),
            brighten=self.cb_bright.isChecked(),
            overlay_params=overlay_params,
            energy_map=dict(self._sf_energy_map),
            pv_channels=({n: PV_CHANNEL_MAP[n] for n in self._pv_enabled if n in PV_CHANNEL_MAP}
                         if self.cb_save_overlay.isChecked() else {}),
            pv_units=dict(PV_UNITS),
        )
        task.save_txt = self.cb_save_metadata_txt.isChecked()
        self._save_task = task
        self._save_progress_dlg = self._show_save_range_progress_dialog(total)
        task.signals.progress.connect(self._on_save_progress)
        task.signals.finished.connect(lambda s, e: self._on_save_finished(s, e, a, b))
        self.scan_pool.start(task)

    def _show_save_range_progress_dialog(self, total: int) -> "QDialog":
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QProgressBar
        dlg = QDialog(self)
        dlg.setWindowTitle("Ukládání")
        dlg.setModal(False)
        dlg.setMinimumWidth(360)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        lay = QVBoxLayout(dlg)
        lbl = QLabel(f"Ukládání... 0 / {total}")
        lay.addWidget(lbl)
        pb = QProgressBar()
        pb.setRange(0, max(1, total))
        pb.setValue(0)
        lay.addWidget(pb)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("Zrušit")
        btn_row.addWidget(btn_cancel)
        lay.addLayout(btn_row)
        dlg._lbl = lbl
        dlg._pb = pb
        dlg._total = total

        def _on_cancel():
            if self._save_task is not None:
                self._save_task.setAutoDelete(False)
                try:
                    self._save_task.signals.finished.disconnect()
                except Exception:
                    pass
            dlg.close()
            self._hide_copy_progress()

        btn_cancel.clicked.connect(_on_cancel)
        dlg.show()
        return dlg

    def _on_save_progress(self, done, total, filename):
        self.prog.setValue(done); self.lbl_filename.setText(f"Saving {done}/{total}  |  {filename}")
        dlg = getattr(self, '_save_progress_dlg', None)
        if dlg is not None and dlg.isVisible():
            dlg._lbl.setText(f"Ukládání... {done} / {total}")
            dlg._pb.setValue(done)

    def _on_save_finished(self, saved, errors, a, b):
        self._save_task = None; self._hide_copy_progress()
        dlg = getattr(self, '_save_progress_dlg', None)
        if dlg is not None:
            dlg.close()
            self._save_progress_dlg = None
        QMessageBox.information(self, "Save range",
            f"Saved {saved} files.\nErrors: {errors}\n\n"
            f"From: {fmt_prague_full_from_ns(a)}\nTo: {fmt_prague_full_from_ns(b)}")


# ================================================================== MAIN
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=None)
    args = ap.parse_args()

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("""
    QWidget  { background: #f3f3f3; color: #111; }
    QLabel   { background: transparent; }
    QPushButton { padding: 5px 8px; }
    QComboBox   { padding: 3px 6px; }
    QProgressBar { background: #fff; }
    QToolTip { background: #ffffcc; color: #111; border: 1px solid #aaa; padding: 4px; }
    """)

    w = Viewer()
    w.setWindowTitle("Image Slider")
    w.resize(1200, 620)
    w.show()

    if args.folder:
        w.open_folder_path(Path(args.folder))

    app.exec()