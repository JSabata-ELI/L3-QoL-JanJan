"""
cpva_core.py  —  Non-UI helpers for the CPVA Suite (CSS Logger + Spectra).

Pure logic: config/preset I/O, CPVA archiver HTTP access, time/PV-name helpers,
image-path helpers. No GUI toolkit imported here, so it is safe to use from the
PySide6 app (main.py) and from headless tests.

Extracted from the former tkinter `cssl.py` (now removed).
"""
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import orjson
import requests
import urllib3

TZ_PRAGUE = ZoneInfo("Europe/Prague")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


APP_DIR      = get_app_dir()
CONFIG_FILE  = APP_DIR / "cpva_explorer_config.json"
PRESETS_FILE = APP_DIR / "cpva_presets.json"
CONDITIONS_PRESETS_FILE = APP_DIR / "cpva_conditions_presets.json"
CUSTOM_PVS_FILE = APP_DIR / "custom_pvs.json"
RAMPING_REPOSITORY_DIR = APP_DIR.parent / "Diagnostic" / "RampingRepository"
DATA_REPOSITORY_DIR = APP_DIR.parent / "Diagnostic" / "DataRepository"
IMAGE_ROOT = r"\\users-L3.tier0.lcs.local\cpva-image-2026"

# ---------------------------------------------------------------------------
# CPVA archiver API constants
# ---------------------------------------------------------------------------

CPVA_BASE_URL          = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT  = "/samples"
CPVA_CHANNELS_ENDPOINT = "/channels"
CPVA_HTTP_TIMEOUT      = 10.0

# The archiver only returns reliable data when the query window <= 1 h.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds

# Rows within this many milliseconds of each other are merged into one.
SAMPLE_HOLD_MIN_GAP_MS = 137
MASTER_RAMP_PV = "L3-PFWP6-MTR03-1:RawPos"

RAMPING_PV_MAP = {
    "waveplate": "L3-PFWP6-MTR03-1:RawPos",
    "ptm1":      "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "pcm2":      "L3-PM03-025:Energy",
    "pcm4":      "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "pap1":      "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "sbw4":      "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "Back_ref":  "L3-PM03-023:Energy",
    "ln36":      "L3-VCS-LN36:OPEN",
}

PV_TO_RAMPING = {pv: short for short, pv in RAMPING_PV_MAP.items()}

_SESSION = requests.Session()
_SESSION.verify = False
# The fetch pools run up to 16 requests at once. requests' default adapter only
# keeps 10 pooled connections, so anything above that reopens a fresh HTTPS
# connection (full TLS handshake) per request and logs "connection pool is full,
# discarding connection" — a big hidden cost on the first (many-chunk) load.
# Size the pool above the worker cap so every concurrent request reuses a
# keep-alive connection instead.
from requests.adapters import HTTPAdapter as _HTTPAdapter
_ADAPTER = _HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)


def safe_divide(a, b):
    """Element-wise a / b, returning NaN where the denominator is 0 or non-finite."""
    return np.where(
        (b != 0) & np.isfinite(b),
        a / b,
        np.nan,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "pv_list": [],
    "time_from": "",
    "time_to": "",
    "http_timeout": 10.0,
    "conditions": [],
    "master_pv": MASTER_RAMP_PV,
    "master_multiple": "",
    "avg_target_points": 2000,
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, val in DEFAULT_CONFIG.items():
                data.setdefault(key, val)
            return data
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    if not CONFIG_FILE.exists():
        return
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Presets  (external file — survives re-deploy, shared across stations)
# ---------------------------------------------------------------------------

def load_presets() -> list[dict]:
    """
    Load presets from PRESETS_FILE.
    Each preset: {"name": str, "pvs": [str, ...], "time_window_hours": float|None}
    Returns [] if file missing or invalid.
    """
    if PRESETS_FILE.exists():
        try:
            with open(PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("presets"), list):
                return data["presets"]
        except Exception:
            pass
    return []


def save_presets(presets: list[dict]) -> None:
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, indent=2, ensure_ascii=False)


def load_condition_presets() -> list[dict]:
    if CONDITIONS_PRESETS_FILE.exists():
        try:
            with open(CONDITIONS_PRESETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("presets"), list):
                return data["presets"]
        except Exception:
            pass
    return []


def save_condition_presets(presets: list[dict]) -> None:
    CONDITIONS_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONDITIONS_PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump({"presets": presets}, f, indent=2, ensure_ascii=False)


def load_custom_pvs() -> list[dict]:
    if CUSTOM_PVS_FILE.exists():
        try:
            with open(CUSTOM_PVS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get("custom_pvs", [])
        except Exception:
            pass
    return []


def save_custom_pvs(custom_pvs: list[dict]) -> None:
    CUSTOM_PVS_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = []
    for cpv in custom_pvs:
        d = dict(cpv)
        d.pop("_compiled", None)
        d.pop("compiled", None)
        clean.append(d)
    with open(CUSTOM_PVS_FILE, "w", encoding="utf-8") as f:
        json.dump({"custom_pvs": clean}, f, indent=2, ensure_ascii=False)


def load_ramping_repository() -> list[dict]:
    index_file = RAMPING_REPOSITORY_DIR / "index.json"
    if not index_file.exists():
        return []
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("rampings", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CPVA API - HTTP
# ---------------------------------------------------------------------------

_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):
    resp = _SESSION.get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return orjson.loads(resp.content)


def cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                       timeout: float = CPVA_HTTP_TIMEOUT,
                       count: int | None = None) -> list[dict]:
    """Fetch samples for a channel in [start_ns, end_ns].

    When ``count`` is given, it is passed to the archiver as the desired number
    of samples. The Cassandra PV Archiver then returns server-side *decimated*
    samples from the decimation level whose density is closest to ``count``
    (see the JSON archive-access protocol, Appendix B.3), instead of every raw
    sample — the same mechanism CS Studio uses as "Optimized Archived Data".
    Decimated points arrive as ``type: "minMaxDouble"`` with a mean ``value``
    plus ``minimum``/``maximum``; ``quality`` is ``"Interpolated"`` for them and
    ``"Original"`` for raw ones. If no decimation level is configured on the
    server, raw samples are returned regardless of ``count``.
    """
    query = {
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    }
    if count is not None and count > 0:
        query["count"] = str(int(count))
    params = urllib.parse.urlencode(query)
    url  = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def _chunk_is_night(chunk_start_ns: int, chunk_end_ns: int) -> bool:
    """Return True if the entire chunk is within 22:00-06:00 Prague time (no data expected)."""
    TZ = ZoneInfo("Europe/Prague")
    now_ns_val = int(datetime.now(timezone.utc).timestamp() * 1e9)
    # Never skip chunks that extend to current time or future
    if chunk_end_ns >= now_ns_val - 60 * 1_000_000_000:  # within 1 min of now
        return False
    dt_start = datetime.fromtimestamp(chunk_start_ns / 1e9, tz=TZ)
    dt_end   = datetime.fromtimestamp(chunk_end_ns   / 1e9, tz=TZ)
    def is_night(h): return h >= 22 or h < 6
    return is_night(dt_start.hour) and is_night(dt_end.hour)


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 12) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chunks = []
    cs = start_ns
    i = 0

    while cs < end_ns:
        ce = min(cs + CHUNK_SIZE_NS, end_ns)
        if not _chunk_is_night(cs, ce):
            chunks.append((i, cs, ce))
        i += 1
        cs = ce

    if not chunks:
        return []

    if len(chunks) == 1:
        return cpva_fetch_samples(channel, chunks[0][1], chunks[0][2], timeout)

    if log_fn:
        log_fn(f"      {channel}: {len(chunks)} chunks")

    workers = min(max_workers, len(chunks))
    results_map = {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): idx
            for idx, cs, ce in chunks
        }

        for fut in as_completed(futures):
            idx = futures[fut]
            results_map[idx] = fut.result()

    results = []
    for idx in sorted(results_map):
        results.extend(results_map[idx])

    return results


def _is_cancelled(cancel_fn) -> bool:
    """True when the caller has stopped caring about this fetch.

    A pool of (channel × 1-hour-chunk) requests used to run to completion even
    after the GUI had moved on: pressing "Stop Live" left the whole queue
    hammering the archiver and holding the GIL, so the app kept stuttering long
    after it looked idle. Passing a ``cancel_fn`` lets the pool drop every
    request it has not started yet (in-flight ones still finish).
    """
    if cancel_fn is None:
        return False
    try:
        return bool(cancel_fn())
    except Exception:        # a broken predicate must not abort the fetch
        return False


def cpva_fetch_many_chunked(channels: list[str], start_ns: int, end_ns: int,
                            timeout: float = CPVA_HTTP_TIMEOUT,
                            max_workers: int = 16,
                            progress_fn=None,
                            cancel_fn=None):
    """Fetch several channels over [start_ns, end_ns) using ONE shared thread pool.

    All (channel, 1-hour-chunk) requests compete for the same pool, so the load
    is limited by a single ``max_workers`` cap instead of running channels
    sequentially. Returns ``(results, errors)`` where ``results`` maps each
    channel to its time-ordered sample list and ``errors`` maps a channel to the
    first error string encountered (that channel's data may be partial/empty).

    ``progress_fn(done, total)`` is called from worker threads as chunks finish.
    ``cancel_fn()`` is polled between chunks — see :func:`_is_cancelled`.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    tasks = []                       # (channel, chunk_idx, cs, ce)
    for ch in channels:
        cs = start_ns
        i = 0
        while cs < end_ns:
            ce = min(cs + CHUNK_SIZE_NS, end_ns)
            if not _chunk_is_night(cs, ce):
                tasks.append((ch, i, cs, ce))
            i += 1
            cs = ce

    results_map = {ch: {} for ch in channels}
    errors: dict[str, str] = {}
    total = len(tasks)
    if progress_fn:
        progress_fn(0, total)
    if total == 0:
        return {ch: [] for ch in channels}, errors

    done = 0
    workers = min(max_workers, total)

    def _run(ch, cs, ce):
        if _is_cancelled(cancel_fn):
            return None                         # queued but no longer wanted
        return cpva_fetch_samples(ch, cs, ce, timeout)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(_run, ch, cs, ce): (ch, idx)
            for ch, idx, cs, ce in tasks
        }
        for fut in as_completed(futures):
            ch, idx = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:            # keep other channels/chunks alive
                errors.setdefault(ch, str(exc))
            else:
                if res is not None:
                    results_map[ch][idx] = res
            done += 1
            if progress_fn:
                progress_fn(done, total)
            if _is_cancelled(cancel_fn):
                ex.shutdown(wait=False, cancel_futures=True)
                break

    out = {}
    for ch in channels:
        merged = []
        for idx in sorted(results_map[ch]):
            merged.extend(results_map[ch][idx])
        out[ch] = merged
    return out, errors


def cpva_fetch_many_optimized(channels: list[str], start_ns: int, end_ns: int,
                              count: int,
                              timeout: float = CPVA_HTTP_TIMEOUT,
                              max_workers: int = 16,
                              progress_fn=None,
                              cancel_fn=None):
    """Fetch several channels using server-side decimation (one request each).

    Each channel is fetched with a single request over the whole window, passing
    ``count`` so the archiver returns decimated samples instead of every raw
    sample (see cpva_fetch_samples). This needs no 1-hour chunking — the archiver
    serves the full range at once from a decimation level. Returns
    ``(results, errors)`` like cpva_fetch_many_chunked. ``progress_fn(done,
    total)`` is called as channels finish (total = number of channels).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {ch: [] for ch in channels}
    errors: dict[str, str] = {}
    total = len(channels)
    if progress_fn:
        progress_fn(0, total)
    if total == 0:
        return results, errors

    done = 0
    workers = min(max_workers, total)

    def _run(ch):
        if _is_cancelled(cancel_fn):
            return None                         # queued but no longer wanted
        return cpva_fetch_samples(ch, start_ns, end_ns, timeout, count)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_run, ch): ch for ch in channels}
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                errors.setdefault(ch, str(exc))
            else:
                if res is not None:
                    results[ch] = res
            done += 1
            if progress_fn:
                progress_fn(done, total)
            if _is_cancelled(cancel_fn):
                ex.shutdown(wait=False, cancel_futures=True)
                break
    return results, errors


# Cumulative look-back horizons (seconds) for hunting the most recent sample
# before a time. We scan the NEW slice at each step (near → far) and stop at the
# first hit, so PVs with recent data cost one chunk and only truly-stale PVs pay
# for the deeper scan. The archiver is unreliable for windows > 1 h, so every
# slice is fetched via cpva_fetch_samples_chunked (1-hour chunks) — a single
# multi-hour request would silently return nothing.
_LAST_BEFORE_STEPS_S = (3600, 6 * 3600, 24 * 3600,
                        3 * 24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600)


def cpva_fetch_last_before(channel: str, before_ns: int,
                           timeout: float = CPVA_HTTP_TIMEOUT,
                           cancel_fn=None):
    """Return the most recent sample dict strictly before `before_ns`, or None.

    Scans expanding 1-hour-chunked rings back to ~30 days and stops at the first
    ring that holds data, so a PV whose last update predates the requested
    window can still be carried forward instead of leaving a gap in the plot.
    ``cancel_fn`` is polled before every ring, since the deepest scan is the
    single longest thing the initial load does.
    """
    hi = before_ns
    for step_s in _LAST_BEFORE_STEPS_S:
        if _is_cancelled(cancel_fn):
            return None
        lo = max(0, before_ns - int(step_s * 1e9))
        if lo >= hi:
            break
        try:
            raw = cpva_fetch_samples_chunked(channel, lo, hi, timeout)
        except Exception:
            raw = None
        if raw:
            # Chunked results are time-ordered ascending → last is nearest `hi`.
            return raw[-1]
        hi = lo
        if lo == 0:
            break
    return None


def cpva_fetch_last_before_many(channels: list[str], before_ns: int,
                                timeout: float = CPVA_HTTP_TIMEOUT,
                                max_workers: int = 8,
                                progress_fn=None,
                                cancel_fn=None):
    """Parallel :func:`cpva_fetch_last_before` for several channels at once.

    The initial load previously hunted each PV's carry-forward value one after
    another; a PV with no recent data scans expanding rings back to ~30 days, so
    a handful of stale PVs alone could dominate the whole load time. Running them
    in a shared pool collapses that into a single wave. Returns ``{channel:
    sample_dict}`` only for channels that actually had a prior sample.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, dict] = {}
    if not channels:
        return out
    workers = min(max_workers, len(channels))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_last_before, ch, before_ns, timeout, cancel_fn): ch
            for ch in channels
        }
        for fut in as_completed(futures):
            ch = futures[fut]
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                out[ch] = res
            done += 1
            if progress_fn:
                progress_fn(done, len(channels))
            if _is_cancelled(cancel_fn):
                ex.shutdown(wait=False, cancel_futures=True)
                break
    return out


def cpva_decode_value(sample: dict):
    val = sample.get("value")

    if val is None:
        return None

    if isinstance(val, (int, float, str)):
        return val

    if isinstance(val, list):
        if len(val) == 1:
            return val[0]

        # ASCII decode zkoušej jen pro kratší listy
        if len(val) <= 512 and val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(map(chr, val))
                if decoded.strip():
                    return decoded
            except Exception:
                pass

        return val

    return val


def cpva_fetch_channels(timeout: float = CPVA_HTTP_TIMEOUT) -> list[str]:
    url  = f"{CPVA_BASE_URL}{CPVA_CHANNELS_ENDPOINT}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response from /channels: {type(data).__name__}")
    return [str(x) for x in data]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def dt_to_ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int(dt.timestamp() * 1e9)


def ns_to_local_str(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{ms:03d}"


def _fmt_cursor_value(v: float) -> str:
    """Format a cursor/annotation value without scientific e+N notation where avoidable."""
    if v != v:           # NaN
        return "nan"
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e9:  return f"{v:.4e}"       # too large for comfortable fixed-point
    if a >= 10000: return f"{v:,.0f}"     # e.g. 300000 → "300,000"
    if a >= 1000:  return f"{v:.1f}"
    if a >= 100:   return f"{v:.2f}"
    if a >= 10:    return f"{v:.3f}"
    if a >= 1:     return f"{v:.4f}"
    if a >= 0.01:  return f"{v:.5f}"
    if a >= 1e-4:  return f"{v:.6f}"
    return f"{v:.4e}"                     # truly tiny — e-notation unavoidable


def parse_user_datetime(s: str) -> datetime | None:
    s = s.strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# PV name shortening
# ---------------------------------------------------------------------------

# Segments stripped from PV names for display (case-insensitive).
_STRIP_PATTERNS = re.compile(
    r"HAPLS[-_]?|"
    r"ENER[-_]?|"
    r"[-_]?IN[-_]?|"
    r"[-_]?LT\d[-_]?|"
    r"[-_]?DIAG\d?[-_]?|"
    r"[-_]{2,}",
    re.IGNORECASE,
)


def shorten_pv_name(full_name: str) -> str:
    """
    Return a compact display label, e.g.:
      HAPLS-ENER-IN-PFM8-LT1-DIAG2:Energy.value  ->  PFM8 - Energy
    """
    # Split on colon: device part and field part
    if ":" in full_name:
        device_part, field_part = full_name.split(":", 1)
    else:
        device_part, field_part = full_name, ""

    # Strip noise from device part
    device = _STRIP_PATTERNS.sub("_", device_part)
    device = re.sub(r"_+", "_", device).strip("_")

    # From the field, take only up to the first dot segment (drop .RBV, .value, etc.)
    field = field_part.split(".")[0] if field_part else ""

    if device and field:
        return f"{device} - {field}"
    return device or field or full_name


# ---------------------------------------------------------------------------
# Wildcard filter
# ---------------------------------------------------------------------------

def make_pv_query_matcher(query: str):
    """Compile `query` into a predicate that tests one PV name.

    Every whitespace-separated token must occur in the name, in the order given
    — "023 l3" behaves exactly like "*023*l3*", without having to type the
    asterisks. Explicit "*" / "?" inside a token still work (and "*" is what
    lets a token match across a gap in the middle of a word).

    Compiled once per query, then called for every channel — the archiver lists
    ~10 000 of them and this runs on each keystroke.
    """
    tokens = (query or "").strip().lower().split()
    if not tokens:
        return lambda _text: True
    parts = []
    for tok in tokens:
        parts.append("".join(
            ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
            for ch in tok))
    rx = re.compile(".*".join(parts))
    return lambda text: rx.search(text.lower()) is not None


def _matches_wildcard(text: str, pattern: str) -> bool:
    return make_pv_query_matcher(pattern)(text)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _open_path(path_str: str):
    """Open a file/path with the OS default handler.

    Raises on failure (no GUI here) — callers in the UI layer surface the error.
    """
    if sys.platform == "win32":
        os.startfile(path_str)           # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path_str])
    else:
        subprocess.Popen(["xdg-open", path_str])


def _looks_like_image_path(value: str) -> bool:
    return any(value.lower().endswith(ext)
               for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"))


def _image_file_size(value: str) -> str:
    """Return a human-readable file size for an image path, or '' if unavailable."""
    try:
        path = Path(_resolve_image_path(value))
        sz = path.stat().st_size
        if sz < 1024:
            return f"{sz} B"
        elif sz < 1024 * 1024:
            return f"{sz/1024:.1f} kB"
        else:
            return f"{sz/1024/1024:.2f} MB"
    except Exception:
        return ""


def _resolve_image_path(value: str) -> str:
    """Prepend UNC root; folder structure already uses UTC hours."""
    clean = value.replace("/", "\\").lstrip("\\")
    return IMAGE_ROOT + "\\" + clean
