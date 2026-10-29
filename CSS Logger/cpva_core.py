"""
cpva_core.py  —  Non-UI helpers for the CPVA Suite (CSS Logger + Spectra).

Pure logic: config/preset I/O, CPVA archiver HTTP access, time/PV-name helpers,
image-path helpers. No GUI toolkit imported here, so it is safe to use from the
PySide6 app (main.py) and from headless tests.

Extracted from the former tkinter `cssl.py` (now removed).
"""
import itertools
import json
import math
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
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
# A single big request needs far longer than a one-hour one. The per-request
# timeout is derived from its span (see _timeout_for_span), between these two.
CPVA_HTTP_TIMEOUT_LONG = 120.0

# The backup. The CPVA archiver is only a front end: the L3 proxy on .64 (twin
# .65) serves the very same L3 Cassandra — measured 15.10.2026, an identical
# channel list, identical rows, the same `count=` decimation, the same speed. So
# when the CPVA service cannot be reached at all (connection refused or reset,
# HTTP 502/503/504) the same question goes there instead, and for the next
# CPVA_DOWN_S seconds every question goes straight to it. Never on a read timeout
# (same slow database behind both) and never on HTTP 500 (the archiver's "window
# too big", which the fetchers answer by splitting).
L3_PROXY_BASE_URLS = (
    "http://10.78.0.64:8080/l3proxy/archive-access/api/1.0/archive/1",
    "http://10.78.0.65:8080/l3proxy/archive-access/api/1.0/archive/1",
)
CPVA_DOWN_S = 60.0
_UNREACHABLE_HTTP = (502, 503, 504)

# The archives the CPVA archiver does not have. Same hosts, same URL shape.
# L3BT: beam transport and the experimental halls' safety channels (4336 names
# on 15.10.2026, 196 also on CPVA). FCT: facility channels — cooling, power,
# PSI, leak detection (7636 names, 48 also on CPVA). A channel is read from one
# of these only when it lists it and the CPVA archiver does not; the shared
# names stay where they always were. Looked up in this order.
EXTRA_ARCHIVES = {
    "l3bt": ("http://10.78.0.64:8080/l3btproxy/archive-access/api/1.0/archive/1",
             "http://10.78.0.65:8080/l3btproxy/archive-access/api/1.0/archive/1"),
    "fct":  ("http://10.78.0.64:8080/fctproxy/archive-access/api/1.0/archive/1",
             "http://10.78.0.65:8080/fctproxy/archive-access/api/1.0/archive/1"),
}

# One hour: the span the archiver serves for any channel, however fast it is.
# It is no longer the fixed request size — cpva_fetch_many_adaptive asks for as
# much as the server will actually give and halves only what it refuses — but it
# stays the reference unit for timeouts, request estimates and the live tick.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds

# Adaptive fetch bounds.
MIN_CHUNK_NS       = int(60 * 1e9)          # never split below one minute
MAX_CHUNK_NS       = int(30 * 86400 * 1e9)  # largest span ever tried in one request
RAW_START_CHUNK_NS = 4 * CHUNK_SIZE_NS      # raw mode's optimistic first try
MIN_CHUNK_COUNT    = 16                     # smallest useful decimation target
FILL_STEPS         = 12                     # wide requests per period, for a
                                            # graph that visibly fills in
FETCH_MAX_REQUESTS = 20_000                 # hard budget for one fetch call

# Checking a server-thinned series against the archive itself (see
# _decimated_is_suspect). The archiver's decimation levels are not complete for
# every channel: a rarely-written one can come back as a long run of identical
# interpolated points, or simply stop days before the period does.
VERIFY_DENSE_PER_HOUR = 500     # samples in the probe hour above which the
                                # channel is dense and its thinned series kept
VERIFY_EDGE_FRACTION  = 0.05    # a thinned series must reach this close to both
                                # ends of the period, or it is treated as short
VERIFY_MAX_SAMPLES    = 400_000 # a re-read bigger than this is dropped again:
                                # the thinned series is the lesser evil
VERIFY_SPARSE_TOTAL   = 100_000 # probe-hour rate × hours in the period above
                                # which a channel is thinned, however slow it is

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
    "master_step": "1000",
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


# Requests in flight at once, for the whole program. Every setting tab runs its
# own loads and Live polls, each with a pool of up to 16; without a shared cap
# a few tabs loading together would multiply the archiver's load (and the
# connection pool above is 32). A request waiting for a slot waits here, not in
# the archiver.
MAX_REQUESTS_IN_FLIGHT = 24
_IN_FLIGHT = threading.BoundedSemaphore(MAX_REQUESTS_IN_FLIGHT)


def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):
    with _IN_FLIGHT:
        resp = _SESSION.get(
            url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        body = resp.content
    return orjson.loads(body)


def _cannot_reach(exc) -> bool:
    """The service is not there at all — as opposed to slow, or answering."""
    if isinstance(exc, requests.ConnectionError):   # refused, reset, SSL, connect timeout
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return code in _UNREACHABLE_HTTP


_cpva_down_until = 0.0
_DOWN_LOCK = threading.Lock()
BACKUP_STATS = {"backup": 0}


def _cpva_is_down() -> bool:
    with _DOWN_LOCK:
        return time.monotonic() < _cpva_down_until


def _mark_cpva_down() -> None:
    global _cpva_down_until
    with _DOWN_LOCK:
        _cpva_down_until = time.monotonic() + CPVA_DOWN_S


def _get_json_or_backup(cpva_url: str, proxy_path: str,
                        timeout: float = CPVA_HTTP_TIMEOUT):
    """The CPVA archiver, or the L3 proxy when the archiver cannot be reached.

    `proxy_path` is the same question in the proxy's URL shape. The backup gets
    the caller's whole timeout: it reads the same database, just as slowly. An
    error from the proxy is raised as it is (its HTTP 500 still means "split");
    only when neither can be reached is it one ConnectionError naming both —
    deliberately not a Timeout, so nobody fans that out into smaller windows.
    """
    first = None
    if not _cpva_is_down():
        try:
            return _http_get_json(cpva_url, timeout=timeout)
        except Exception as exc:
            if not _cannot_reach(exc):
                raise
            _mark_cpva_down()
            first = exc
    with _DOWN_LOCK:
        BACKUP_STATS["backup"] += 1
    try:
        return _proxy_get_json(L3_PROXY_BASE_URLS, proxy_path, timeout)
    except requests.ConnectionError as last:
        if first is None:
            raise
        raise requests.ConnectionError(
            f"archiver unreachable ({first}); backup L3 proxy: {last}") from last


def _proxy_get_json(base_urls, path: str, timeout: float):
    """GET on a proxy: the first host, and the twin only when the first one
    cannot be reached. Any other error is an answer and is raised as it is."""
    last = None
    for base in base_urls:
        try:
            return _http_get_json(base + path, timeout=timeout)
        except Exception as exc:
            if not isinstance(exc, requests.ConnectionError):
                raise                   # an answer, or a slow one: not the twin's job
            last = exc
    raise last


# ---------------------------------------------------------------------------
# Which archive holds a channel
# ---------------------------------------------------------------------------
# The channel lists are cheap (0.02 s each) and held for the session; every
# cpva_fetch_channels (a picker opening) refreshes them.
_ALL_CHANNELS_PATH = "/channels-by-pattern/" + urllib.parse.quote("*", safe="")
_cpva_names: "set[str] | None" = None
_proxy_names: dict = {}                 # extra archive key → its names
_lists_tried_at = 0.0
_ROUTE_LOCK = threading.Lock()
# Held for the whole load, so the first reads WAIT for the lists instead of all
# going to the CPVA archiver while the first one is fetching.
_LOAD_LOCK = threading.Lock()
_LIST_RETRY_S = 30.0


def _names_of(raw) -> "set[str]":
    return {str(x) for x in raw if isinstance(x, str)} \
        if isinstance(raw, list) else set()


def _load_lists(timeout: float = 6.0, force: bool = False) -> None:
    """Fill the name lists that are not held yet. Never raises."""
    global _cpva_names, _lists_tried_at
    with _LOAD_LOCK:
        with _ROUTE_LOCK:
            complete = (_cpva_names is not None
                        and all(k in _proxy_names for k in EXTRA_ARCHIVES))
            if not force and complete:
                return
            if not force and _lists_tried_at \
                    and time.monotonic() - _lists_tried_at < _LIST_RETRY_S:
                return
            _lists_tried_at = time.monotonic()
        if force or _cpva_names is None:
            try:
                names = _names_of(_get_json_or_backup(
                    f"{CPVA_BASE_URL}{CPVA_CHANNELS_ENDPOINT}",
                    _ALL_CHANNELS_PATH, timeout=timeout))
                with _ROUTE_LOCK:
                    _cpva_names = names
            except Exception:
                pass
        for key, urls in EXTRA_ARCHIVES.items():
            if not force and key in _proxy_names:
                continue
            try:
                names = _names_of(_proxy_get_json(urls, _ALL_CHANNELS_PATH, timeout))
                with _ROUTE_LOCK:
                    _proxy_names[key] = names
            except Exception:
                pass


def archive_of(channel: str):
    """The extra archive ("l3bt" / "fct") this channel is read from, or None
    for the CPVA archiver (and its backup)."""
    _load_lists()
    with _ROUTE_LOCK:
        lists, cpva = dict(_proxy_names), _cpva_names
    # Without the CPVA list nobody can say the name is ALSO there; the proxies
    # answer the shared names with identical samples, so it is safe.
    if cpva is not None and channel in cpva:
        return None
    for key in EXTRA_ARCHIVES:
        if channel in lists.get(key, ()):
            return key
    return None


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
    # The proxy names the channel in the path; the rest of the query is the same.
    proxy_params = urllib.parse.urlencode(
        {k: v for k, v in query.items() if k != "channelName"})
    proxy_path = (f"/samples/{urllib.parse.quote(channel, safe='')}"
                  f"?{proxy_params}")
    extra = archive_of(channel)
    if extra is not None:
        data = _proxy_get_json(EXTRA_ARCHIVES[extra], proxy_path, timeout)
    else:
        data = _get_json_or_backup(url, proxy_path, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def _timeout_for_span(span_ns: int, base: float = CPVA_HTTP_TIMEOUT,
                      cap: float = CPVA_HTTP_TIMEOUT_LONG) -> float:
    """Seconds to allow one request covering `span_ns`.

    An hour is answered well inside the 10 s default; a month can take a
    minute. Scaling with the span stops a small request from waiting two
    minutes on a sick archiver while still letting a big one finish.
    """
    if span_ns <= CHUNK_SIZE_NS:
        return base
    return min(cap, max(base, base * span_ns / CHUNK_SIZE_NS))


# Statuses the archiver uses when one response would be too large or too slow.
_SPLIT_STATUS = (413, 414, 500, 502, 503, 504)


def _is_splittable_error(exc) -> bool:
    """True only for failures a SMALLER time window could plausibly fix.

    The archiver answers HTTP 500 when a single response would carry too many
    samples, and a huge response can simply run out of time; halving the window
    cures both. A refused connection or a bad host is not cured by halving —
    fanning those out would turn one unreachable archiver into tens of
    thousands of pointless requests.
    """
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code in _SPLIT_STATUS:
        return True
    if "Timeout" in type(exc).__name__:        # requests / urllib3 read timeout
        return True
    return isinstance(exc, ValueError)         # truncated / unexpected shape


def _looks_decimated(samples: list, requested: int):
    """Did the server honour `count=`?  True / False / None when unclear."""
    if not requested or not samples:
        return None
    if any(s.get("quality") == "Interpolated" for s in samples[:20]):
        return True
    if len(samples) > 5 * requested:
        return False
    return None


def _decimated_is_suspect(samples: list, start_ns: int, end_ns: int):
    """Does this server-thinned series describe the level rather than the data?

    Returns ``(True, reason)`` when the series must not be trusted. Measured on
    the real archiver 2026-09-21 over 10.-12.9.2026, both silent:

      * ``L3-PCM3Y-MTR03-73:RawPos`` answered ``count=2000`` with 630 identical
        points that stop 61.5 h before the period does, hiding the thirteen
        positions the motor really went through at 10:30. Asked raw, the same
        request returns those eighteen samples.
      * ``HAPLS-SPEC_CENT_PD1M1_LT7_DIAG1:SpectralCentroid`` answered with 4321
        identical points over a period holding not one archived sample, which
        the Logger then drew as a solid three-day line.

    Both give themselves away in the same two ways: nothing in the series ever
    changes, or the series does not reach the ends of the period. A decimated
    point carries ``minimum``/``maximum`` for its bin, so a bin that really saw
    a change says so even when its mean happens to repeat.
    """
    span = end_ns - start_ns
    if span <= 0 or not samples:
        return False, ""
    times = [int(s["time"]) for s in samples if s.get("time") is not None]
    if not times:
        return False, ""
    edge = max(int(span * VERIFY_EDGE_FRACTION), int(MIN_CHUNK_NS))
    if times[-1] < end_ns - edge:
        return True, (f"thinned series stops {(end_ns - times[-1])/3.6e12:.1f} h "
                      f"before the period ends")
    if times[0] > start_ns + edge:
        return True, (f"thinned series starts {(times[0] - start_ns)/3.6e12:.1f} h "
                      f"after the period begins")
    seen = set()
    for s in samples:
        lo, hi = s.get("minimum"), s.get("maximum")
        if lo is not None and hi is not None and lo != hi:
            return False, ""          # a bin really saw a change — trust it
        v = s.get("value")
        if isinstance(v, (int, float)):
            seen.add(float(v))
            if len(seen) > 1:
                return False, ""
    return True, "thinned series never changes"


def _coalesce_ranges(ranges: list) -> list:
    """Sort and merge touching/overlapping (start, end) pairs."""
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


class _SpanOracle:
    """How much time one channel's archiver will serve in a single request.

    `good` is the widest span known to have worked, `bad` the narrowest known
    to have failed. There is one oracle PER CHANNEL on purpose: how many
    samples an hour holds is a property of the signal, so a single shared
    oracle would drag a slowly-changing signal (a valve state, an hourly
    setpoint) down to the chunk size of the fastest one — turning a dozen
    requests into thousands.
    """

    __slots__ = ("good", "bad", "_lock")

    def __init__(self):
        self.good = 0
        self.bad  = 0                 # 0 = nothing has failed yet
        self._lock = threading.Lock()

    def ok(self, span_ns: int) -> None:
        with self._lock:
            self.good = max(self.good, int(span_ns))

    def failed(self, span_ns: int) -> None:
        with self._lock:
            self.bad = int(span_ns) if not self.bad else min(self.bad, int(span_ns))

    def plan_span(self, span_ns: int, hint: "_SpanOracle" = None) -> int:
        """Span to try for a range of `span_ns` that has just been refused.

        Always at most half of what failed, so a split always makes progress.
        `hint` is the shared cross-channel oracle: until this channel has had a
        success of its own, the widest span ALREADY known to work for some
        other signal is a far better guess than blind halving.
        """
        with self._lock:
            good, bad = self.good, self.bad
        if not good and hint is not None:
            good = hint.good
        target = good or (bad // 2 if bad else span_ns // 2)
        half = max(MIN_CHUNK_NS, span_ns // 2)
        return max(MIN_CHUNK_NS, min(int(target) or MIN_CHUNK_NS, half))


class FetchReport(NamedTuple):
    """What one adaptive fetch actually managed to do.

    `gaps` is the honest part: every time range that could not be read, per
    channel. A short sample list plus an empty `gaps` means the archive really
    holds nothing there; a short list with gaps means we failed to read it.
    """
    gaps:        dict          # channel -> [(start_ns, end_ns), ...]
    boundaries:  dict          # channel -> [start_ns of each request served]
    requests:    int
    splits:      int
    cancelled:   bool
    over_budget: bool
    decimated:   object        # True / False / None (unknown)
    span_ns:     int
    elapsed_s:   float
    rechecked:   tuple = ()    # channels whose thinned series was thrown away
                               # and read again as archived (see
                               # _decimated_is_suspect)
    cached:      int = 0       # channel-days served from the day cache


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 12) -> list[dict]:
    """One channel over [start_ns, end_ns), as much of it as can be read.

    NOTE: this no longer raises when part of the range cannot be read. It used
    to re-raise the first failing hour, which threw away the whole channel over
    a single refused request; now an unreadable range is simply absent from the
    result (see :func:`cpva_fetch_many_adaptive` if you need to know which).
    """
    res, _errors, _report = cpva_fetch_many_adaptive(
        [channel], start_ns, end_ns, count=None, timeout=timeout,
        max_workers=max_workers, log_fn=log_fn)
    return res.get(channel, [])


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


def _empty_report(gaps, boundaries, span_ns) -> FetchReport:
    return FetchReport(gaps, boundaries, 0, 0, False, False, None, span_ns, 0.0)


# How densely a channel is archived barely changes from one load to the next,
# and asking costs a request per channel before anything else can start (on a
# busy archiver, seconds). A probe of the newest hour is remembered this long.
DENSITY_MEMO_S = 1800.0
# How long the pre-flight probe is waited for once the day cache has already
# covered ≥ 90 % of the period (see _preflight_failed).
PREFLIGHT_CACHED_WAIT_S = 3.0
_DENSITY_MEMO: dict = {}          # (names, hour asked) -> (samples, monotonic)
_DENSITY_LOCK = threading.Lock()


def _probe_density_names(names, start_ns: int, end_ns: int, timeout: float,
                         cancel_fn=None) -> int:
    """:func:`_probe_density` under each archived name in turn, until one
    holds samples (SBW4's names take turns, so the quiet one says nothing).

    Remembered for DENSITY_MEMO_S per channel and probe position (the newest
    hour of the period, or its middle hour), so reloading, switching tabs or
    stepping the window does not ask again."""
    newest = end_ns >= time.time() * 1e9 - 2 * CHUNK_SIZE_NS
    key = (tuple(names), "now" if newest else int(end_ns // CHUNK_SIZE_NS))
    now = time.monotonic()
    with _DENSITY_LOCK:
        hit = _DENSITY_MEMO.get(key)
    if hit is not None and now - hit[1] < DENSITY_MEMO_S:
        return hit[0]
    n = -1
    for name in names:
        n = _probe_density(name, start_ns, end_ns, timeout, cancel_fn)
        if n > 0:
            break
    if n >= 0:
        with _DENSITY_LOCK:
            _DENSITY_MEMO[key] = (n, now)
    return n


def _probe_density(channel: str, start_ns: int, end_ns: int, timeout: float,
                   cancel_fn=None) -> int:
    """Raw samples the channel has in the newest hour of the period.

    One request. It decides whether a suspect thinned series is worth replacing:
    a densely archived channel HAS a working decimation level (measured: the
    energies and the chiller temperatures all track their raw data), and
    re-reading one of those raw is exactly the cost the thinning exists to
    avoid. A channel writing a handful of samples a day is free to read raw.
    """
    if _is_cancelled(cancel_fn):
        return -1
    a = max(start_ns, end_ns - CHUNK_SIZE_NS)
    try:
        return len(cpva_fetch_samples(channel, a, end_ns, timeout, None))
    except Exception:
        return -1                      # unknown — let the caller re-read


def _channels_to_read_raw(channels, start_ns, end_ns, timeout,
                          cancel_fn=None, max_workers: int = 8,
                          names_of=None) -> dict:
    """Which of these channels are too sparse to be worth thinning.

    Returns ``{channel: samples in the probe hour}`` for the sparse ones, plus
    ``"_requests"`` — how many requests this cost. A channel is judged on the
    newest hour of the period; if that hour is empty the middle hour decides,
    so a meter that simply happened to be off at the end is not mistaken for a
    setting. Two requests per channel at the very worst, run in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor

    spent = [0]

    def _judge(ch):
        names = (names_of or {}).get(ch) or (ch,)
        n = _probe_density_names(names, start_ns, end_ns, timeout, cancel_fn)
        spent[0] += len(names)
        if n == 0 and end_ns - start_ns > 3 * CHUNK_SIZE_NS:
            mid = start_ns + (end_ns - start_ns) // 2
            n = _probe_density_names(names, mid, min(end_ns, mid + CHUNK_SIZE_NS),
                                     timeout, cancel_fn)
            spent[0] += len(names)
        return n

    if _is_cancelled(cancel_fn) or not channels:
        return {"_requests": 0}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(channels))) as ex:
        counts = dict(zip(channels, ex.map(_judge, channels)))
    # Judged on the WHOLE period, not the hour: an energy meter writing ~320
    # samples an hour is "sparse" by the hourly rule, and over two months that
    # was ~460 000 raw samples per signal — measured 2026-09-30, eight of them
    # were read raw and the PC ran out of memory.
    hours = max(1.0, (end_ns - start_ns) / CHUNK_SIZE_NS)
    out = {ch: n for ch, n in counts.items()
           if 0 <= n < VERIFY_DENSE_PER_HOUR
           and n * hours <= VERIFY_SPARSE_TOTAL}
    out["_requests"] = spent[0]
    return out


def _recheck_decimated(suspects, out, gaps, boundaries, start_ns, end_ns,
                       timeout, cancel_fn=None, log_fn=None, max_workers=8):
    """Replace every untrustworthy thinned series with the real samples.

    ``suspects`` is ``[(channel, reason), …]`` from :func:`_decimated_is_suspect`.
    Sparse channels are re-read raw over the whole period; dense ones keep what
    the server gave. Mutates ``out``, ``gaps`` and ``boundaries`` in place and
    returns ``(rechecked, extra_requests)``.
    """
    from concurrent.futures import ThreadPoolExecutor

    def _note(text):
        if log_fn:
            try:
                log_fn(text)
            except Exception:
                pass

    extra = 0
    names = [ch for ch, _why in suspects]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(names))) as ex:
        density = dict(zip(names, ex.map(
            lambda ch: _probe_density(ch, start_ns, end_ns, timeout, cancel_fn),
            names)))
    extra += len(names)

    redo = [ch for ch in names if density.get(ch, -1) < VERIFY_DENSE_PER_HOUR]
    for ch in names:
        if ch not in redo:
            _note(f"{shorten_pv_name(ch)}: the archiver's thinned series looks "
                  f"flat, but the channel is written {density[ch]}× an hour — "
                  f"kept as it is")
    if not redo or _is_cancelled(cancel_fn):
        return [], extra

    raw_out, raw_err, raw_rep = _fetch_many_adaptive(
        redo, start_ns, end_ns, count=None, timeout=timeout,
        cancel_fn=cancel_fn, preflight=False, verify=False)
    extra += raw_rep.requests

    why_of = dict(suspects)
    done = []
    for ch in redo:
        got = raw_out.get(ch, [])
        if raw_err.get(ch) and not got:
            continue                   # the raw read failed — keep what we had
        if len(got) > VERIFY_MAX_SAMPLES:
            _note(f"{shorten_pv_name(ch)}: {why_of[ch]}, but reading it in full "
                  f"is {len(got)} samples — the thinned series was kept")
            continue
        out[ch] = got
        gaps[ch] = list(raw_rep.gaps.get(ch) or [])
        boundaries[ch] = list(raw_rep.boundaries.get(ch) or [])
        done.append(ch)
        _note(f"{shorten_pv_name(ch)}: {why_of[ch]} — re-read as archived, "
              f"{len(got)} sample(s)")
    return done, extra


# How many steps a long load fills the graph in. Up to this many days a step is
# one day; beyond it neighbouring days are read together (three months: 15
# steps of six days). Measured 12.10.2026, three months of the Operation preset
# read one day per request cost 767 requests and twice the time of the old
# 112 — the archiver's cost is per request, not per sample.
MAX_TILES = 16

# A day counts as closed this long after its last midnight (the day cache's
# own rule); a tile ending later belongs to the open day.
CLOSED_DAY_NS = int(3600e9)
# The open day has no thinned level, so it comes back exactly as archived (by
# mid-afternoon 78 000 samples for one energy). One request for all of it took
# 4.7–14 s, the same in four-hour pieces read side by side 3.9–4.5 s
# (measured 12.10.2026, four energies, back to back).
OPEN_DAY_PIECE_NS = 4 * CHUNK_SIZE_NS
# How far back the day cache keeps parts of days (cpva_cache.RECENT_DAYS).
RECENT_DAYS_NS = 3 * 24 * CHUNK_SIZE_NS


def _load_tiles(start_ns: int, end_ns: int) -> list:
    """The pieces a load is read in, NEWEST FIRST.

    Whole Prague days (one, or several together for a long period), so the
    graph fills in from the present backwards and every closed day is a unit
    the day cache can keep. Grouped from the newest end, so the first step is
    a full one."""
    days = _prague_days(start_ns, end_ns)[::-1]
    if len(days) > MAX_TILES:
        per = -(-len(days) // MAX_TILES)
        days = [(days[min(i + per, len(days)) - 1][0], days[i][1])
                for i in range(0, len(days), per)]
    return days


def _is_midnight(ns: int) -> bool:
    d = datetime.fromtimestamp(ns / 1e9, TZ_PRAGUE)
    return d.hour == 0 and d.minute == 0 and d.second == 0 and d.microsecond == 0


def _whole_days(lo: int, hi: int) -> list:
    """The whole Prague days inside [lo, hi): what the day cache can hold."""
    return [(a, b) for a, b in _prague_days(lo, hi)
            if _is_midnight(a) and _is_midnight(b)]


def _fetch_many_adaptive(channels: list[str], start_ns: int, end_ns: int,
                         count: int | None = None,
                         timeout: float | None = None,
                         max_workers: int = 16,
                         progress_fn=None,
                         cancel_fn=None,
                         log_fn=None,
                         chunk_fn=None,
                         coverage_fn=None,
                         phase_fn=None,
                         start_chunk_ns: int | None = None,
                         min_chunk_ns: int = MIN_CHUNK_NS,
                         max_requests: int = FETCH_MAX_REQUESTS,
                         preflight: bool = True,
                         verify: bool = True,
                         tile_fn=None,
                         cache=None,
                         aliases: bool = True,
                         cached_fn=None):
    """Read several channels over [start_ns, end_ns), whatever its length.

    The period is cut into Prague days (:func:`_load_tiles`) and read NEWEST DAY
    FIRST, every channel of a day before the next day, so a long load fills the
    graph in from the present backwards, a whole day at a time. Within a day a
    channel is asked for as much as the archiver will serve, and only what it
    refuses is halved. The archiver answers HTTP 500 when a single response
    would carry too many samples.

    ``count`` > 0 asks for server-side decimation and is shared out across the
    requests in proportion to their span, so the total returned stays near the
    target instead of being multiplied by the number of requests.

    A channel archived under two names (SBW4) is read under the names of
    :func:`read_order` in turn: a request whose answer holds nothing for its
    range (:func:`_holds_nothing`) is asked again under the next name, and
    whatever answers is filed under the name asked for. ``aliases=False`` reads
    exactly the name given.

    Returns ``(results, errors, report)``:
      * ``results``  channel -> time-ordered, de-duplicated sample list
      * ``errors``   channel -> one human sentence (same shape as before)
      * ``report``   :class:`FetchReport`, including the ranges NOT read

    ``progress_fn(done, total)`` is called from worker threads. ``total`` is a
    live estimate that only ever GROWS: it starts at days × channels, and each
    split, pre-split piece or second-name request adds to it. ``done`` never
    falls, ``total`` never falls, ``done <= total`` always, and the last call
    has ``done == total``.

    ``chunk_fn(channel, samples)`` is called as each request lands (and for
    each day served from the cache), for callers that paint partial results.
    ``tile_fn(edge_ns)`` is called whenever the read reaches further back:
    everything from ``edge_ns`` to the end of the period is complete.
    ``log_fn(text)`` gets one-line notes.

    ``coverage_fn(fraction)`` gets the share of (channels × period) finished,
    0..1, never falling. ``phase_fn(text)`` names the steps that read without
    moving it (the density check before, the re-reads after).

    ``cache`` (a :class:`cpva_cache.DayCache`) serves closed days read before
    and keeps the closed days read now. ``cached_fn(fraction)`` gets the share
    of (channels × period) served from it so far, each time it grows — a
    caller can show that at once instead of waiting for the last requests.

    ``verify`` (only meaningful with ``count``) checks each thinned series
    against the archive and reads the channel again, as archived, when the
    server's decimation level turns out to be flat or to stop early — see
    :func:`_decimated_is_suspect`. Turn it off only inside that re-read.
    """
    from concurrent.futures import ThreadPoolExecutor

    start_ns, end_ns = int(start_ns), int(end_ns)
    span_total = max(0, end_ns - start_ns)
    base_timeout = CPVA_HTTP_TIMEOUT if timeout is None else float(timeout)

    gaps: dict[str, list] = {ch: [] for ch in channels}
    boundaries: dict[str, list] = {ch: [] for ch in channels}
    errors: dict[str, str] = {}
    parts: dict[str, list] = {ch: [] for ch in channels}
    first_exc: dict[str, str] = {}

    if not channels or span_total <= 0:
        if progress_fn:
            progress_fn(0, 0)
        return ({ch: [] for ch in channels}, errors,
                _empty_report(gaps, boundaries, span_total))

    # The names each channel is read under. Both SBW4 names picked as separate
    # channels are each read exactly as named — the user asked for both.
    names_of = {}
    for ch in channels:
        order = read_order(ch) if aliases else (ch,)
        if len(order) > 1 and any(a in channels for a in channel_aliases(ch)):
            order = (ch,)
        names_of[ch] = order

    tiles = _load_tiles(start_ns, end_ns)
    oracles = {ch: _SpanOracle() for ch in channels}
    hint = _SpanOracle()          # shared, consulted only for a FIRST attempt
    whole = span_total * len(channels)
    t0 = time.monotonic()
    lock = threading.Lock()
    wake = threading.Event()      # a request finished: the feeder may go on
    state = {"pending": 0, "done": 0, "total": 0, "covered": 0,
             "requests": 0, "splits": 0, "cached": 0, "from_cache": 0,
             "cancelled": False, "over_budget": False, "decimated": None,
             "noted_span": {}}
    ex: object = None
    sparse_raw: set = set()
    overflow: set = set()         # sparse_raw channels that turned out dense
    held: dict = {}               # samples received so far, per channel
    emit_lock = threading.Lock()
    emitted = [0, 0]
    # Per (channel, tile): what was read, whether a piece of it failed, which
    # archived name answered, and the decimation target of the whole tile —
    # what the day cache needs to keep the day.
    tile_got: dict = {}
    tile_bad: set = set()
    tile_src: dict = {}
    tile_cnt: dict = {}
    tile_pending = [0] * len(tiles)
    tile_fed = [False] * len(tiles)
    edge = [end_ns]               # everything newer than this is complete
    alias_days: dict = {}         # channel -> {name: tiles answered}
    # (channel, tile) of a two-name channel -> the name being asked, requests
    # still out under it, what they read and which pieces failed.
    by_name: dict = {}
    cache_hit_days: set = set()   # (channel, day start) served from the cache
    recent_hits: set = set()      # (channel, start) served from today's block
    tile_todo: dict = {}          # (channel, tile) -> the ranges asked for

    def _note(text: str) -> None:
        if log_fn:
            try:
                log_fn(text)
            except Exception:
                pass

    def _phase(text: str) -> None:
        if phase_fn:
            try:
                phase_fn(text)
            except Exception:
                pass

    def _share(cnt, piece_ns, whole_ns):
        if not cnt:
            return None
        if whole_ns <= 0:
            return int(cnt)
        return max(MIN_CHUNK_COUNT, int(round(cnt * piece_ns / whole_ns)))

    def _emit_progress():
        """Report progress, in order, from whichever worker thread got here.

        The snapshot is taken INSIDE emit_lock: taking it outside let two
        threads deliver their counts in the wrong order, so a caller watching a
        progress bar saw it jump backwards.
        """
        if progress_fn is None and coverage_fn is None:
            return
        with emit_lock:
            with lock:
                d, t, c = state["done"], state["total"], state["covered"]
            if d < emitted[0] or t < emitted[1]:
                return                    # a fresher snapshot already went out
            emitted[0], emitted[1] = d, t
            try:
                if progress_fn:
                    progress_fn(d, t)
                if coverage_fn:
                    coverage_fn(min(1.0, c / whole) if whole else 1.0)
            except Exception:
                pass

    def _advance_edge():
        """Report how far back the read is complete, newest tile first."""
        if tile_fn is None:
            return
        with lock:
            e = end_ns
            for i, (lo, _hi) in enumerate(tiles):
                if not tile_fed[i] or tile_pending[i]:
                    break
                e = lo
            moved = e < edge[0]
            if moved:
                edge[0] = e
        if moved:
            try:
                tile_fn(e)
            except Exception:
                pass

    def _submit(ch, a, b, cnt, tile, ni=0, planned=False, unit=None) -> bool:
        """Queue one request. False (and a recorded gap) when the budget is spent.

        A `planned` request is already counted in "total" (the whole plan is
        counted up front, so the progress total never has to shrink); anything
        else is new work and grows the total. ``unit`` is the Prague day a
        two-name channel's request belongs to (its name is decided per day).
        """
        multi = len(names_of[ch]) > 1
        key = (ch, tile, unit)
        with lock:
            if state["requests"] >= max_requests:
                state["over_budget"] = True
                state["covered"] += b - a
                gaps[ch].append((a, b))
                tile_bad.add((ch, tile))
                if planned:
                    state["done"] += 1
                else:
                    state["total"] += 1
                    state["done"] += 1
                return False
            state["requests"] += 1
            state["pending"] += 1
            tile_pending[tile] += 1
            if not planned:
                state["total"] += 1
            if multi:
                ent = by_name.setdefault(key, {"ni": ni, "pending": 0,
                                               "got": [], "failed": []})
                ent["pending"] += 1
        try:
            ex.submit(_task, ch, a, b, cnt, tile, ni, unit)
        except RuntimeError:
            # The pool was shut down under us (a cancel while a split was being
            # queued). Retire the slot as done — never shrink "total", callers
            # are promised it only grows — and record the range as unread.
            with lock:
                state["pending"] -= 1
                tile_pending[tile] -= 1
                state["done"] += 1
                state["covered"] += b - a
                state["cancelled"] = True
                gaps[ch].append((a, b))
                tile_bad.add((ch, tile))
                if multi:
                    by_name[key]["pending"] -= 1
            wake.set()
            return False
        return True

    def _split(ch, a, b, cnt, span, tile, ni, unit=None):
        oracles[ch].failed(span)
        hint.failed(span)
        piece = oracles[ch].plan_span(span, hint)
        k = max(2, int(math.ceil(span / piece)))
        with lock:
            state["splits"] += 1
            told = state["noted_span"].get(ch)
            if told != piece:
                state["noted_span"][ch] = piece
        if told != piece:
            _note(f"{shorten_pv_name(ch)}: {span/3.6e12:.1f} h per request refused, "
                  f"trying {piece/3.6e12:.2f} h ({k} pieces)")
        # Newest piece first, for the same reason the whole plan is newest first.
        for i in range(k - 1, -1, -1):
            ca = a + (span * i) // k
            cb = b if i == k - 1 else a + (span * (i + 1)) // k
            if cb > ca:
                _submit(ch, ca, cb, _share(cnt, cb - ca, span), tile, ni,
                        unit=unit)

    def _commit(ch, tile, a, samples, name, cnt) -> None:
        """File what one request (or one whole tile of a two-name channel) read."""
        raw_warn = False
        with lock:
            if ch in overflow:
                return
            held[ch] = held.get(ch, 0) + len(samples)
            if ch in sparse_raw and held[ch] > VERIFY_MAX_SAMPLES:
                # A channel judged sparse that is not: stop NOW, before it
                # holds a million samples, and free what it has.
                overflow.add(ch)
                parts[ch] = []
                boundaries[ch] = []
                return
            boundaries[ch].append(a)
            parts[ch].append(samples)
            tile_got.setdefault((ch, tile), []).append(samples)
            if samples and len(names_of[ch]) > 1:
                tile_src.setdefault((ch, tile), set()).add(name)
                alias_days.setdefault(ch, {}).setdefault(name, set()).add(a)
            if state["decimated"] is None and cnt:
                state["decimated"] = _looks_decimated(samples, cnt)
                raw_warn = state["decimated"] is False
        if raw_warn:
            _note(f"the archiver returned {len(samples)} raw samples for "
                  f"count={cnt} — it does not decimate, so a long period "
                  f"will be slow and heavy")
        if chunk_fn and samples:
            try:
                chunk_fn(ch, samples)
            except Exception:
                pass

    def _unit_requests(ch, tile, lo, hi, ni, first_planned=False):
        """Queue one day of a two-name channel under name number ``ni``."""
        tlo, thi = tiles[tile]
        cnt = _share(tile_cnt.get((ch, tile)), hi - lo, thi - tlo)
        piece = _piece_for(ch, hi - lo)
        if start_chunk_ns is None and thi > time.time() * 1e9 - CLOSED_DAY_NS:
            piece = min(piece, OPEN_DAY_PIECE_NS)     # see OPEN_DAY_PIECE_NS
        k = max(1, int(math.ceil((hi - lo) / piece)))
        for i in range(k - 1, -1, -1):
            a = lo + ((hi - lo) * i) // k
            b = hi if i == k - 1 else lo + ((hi - lo) * (i + 1)) // k
            if b > a:
                _submit(ch, a, b, _share(cnt, b - a, hi - lo), tile, ni,
                        planned=(first_planned and i == k - 1), unit=(lo, hi))

    def _settle_tile(ch, tile, unit):
        """Every request of one day under one name of a two-name channel has
        returned: keep the answer, or ask the next name for the whole day.

        Decided per Prague DAY, never per request: the names take turns with
        the laser configuration, so a day belongs to one of them, and judging
        each piece on its own mixed the two names inside one day (the L3 name's
        stray value on a HAPLS day came through)."""
        lo, hi = unit
        with lock:
            ent = by_name[(ch, tile, unit)]
            got = list(itertools.chain.from_iterable(ent["got"]))
            failed, ni = list(ent["failed"]), ent["ni"]
        names = names_of[ch]
        nothing = _holds_nothing(got, lo, hi)
        if nothing and ni + 1 < len(names) and not _is_cancelled(cancel_fn):
            with lock:
                ent.update(ni=ni + 1, got=[], failed=[])
            _unit_requests(ch, tile, lo, hi, ni + 1)
            return
        if nothing:
            # No name holds this day. What the first name said is either the
            # bracket sample or a grid the archiver invented — neither is data.
            got = []
        elif ni > 0:
            # Only the other name's own day: its bracket samples belong to days
            # the first name answered for.
            got = [s for s in got if lo <= (s.get("time") or 0) < hi]
        with lock:
            state["covered"] += hi - lo
            for fa, fb in failed:
                gaps[ch].append((fa, fb))
                tile_bad.add((ch, tile))
        _commit(ch, tile, lo, got, names[ni], tile_cnt.get((ch, tile)))

    def _task(ch, a, b, cnt, tile, ni=0, unit=None):
        names = names_of[ch]
        multi = len(names) > 1
        covers = not multi             # a two-name tile is counted when settled
        try:
            if _is_cancelled(cancel_fn):
                with lock:
                    state["cancelled"] = True
                    gaps[ch].append((a, b))
                    tile_bad.add((ch, tile))
                return
            if ch in overflow:
                return                 # given up raw; it is read thinned later
            name = names[ni]
            span = b - a
            tmo = _timeout_for_span(span, base_timeout)
            samples, exc = None, None
            try:
                samples = cpva_fetch_samples(name, a, b, tmo, cnt)
            except Exception as e:
                exc = e
            if exc is not None and not _is_splittable_error(exc):
                # A dropped keep-alive is routine on a long load and the adapter
                # retries nothing of its own. Try once more, but never fan out.
                try:
                    samples, exc = cpva_fetch_samples(name, a, b, tmo, cnt), None
                except Exception as e:
                    exc = e
            if exc is not None:
                with lock:
                    first_exc.setdefault(ch, f"{type(exc).__name__}: {exc}")
                if _is_splittable_error(exc) and span > min_chunk_ns:
                    covers = False
                    _split(ch, a, b, cnt, span, tile, ni, unit)
                elif multi:
                    # Refused outright (measured: HTTP 400 on one of SBW4's
                    # names). Settled with the day: the other name may still
                    # hold it.
                    with lock:
                        by_name[(ch, tile, unit)]["failed"].append((a, b))
                else:
                    with lock:
                        gaps[ch].append((a, b))
                        tile_bad.add((ch, tile))
                return
            oracles[ch].ok(span)
            hint.ok(span)
            if multi:
                with lock:
                    by_name[(ch, tile, unit)]["got"].append(_drop_after(samples, b))
                return
            _commit(ch, tile, a, samples, name, cnt)
        finally:
            if multi:
                # Settle BEFORE this request stops counting as pending: asking
                # the next name queues new work first, so neither the tile nor
                # the whole load can look finished in between.
                with lock:
                    ent = by_name.get((ch, tile, unit))
                    if ent is not None:
                        ent["pending"] -= 1
                    last = ent is not None and ent["pending"] == 0
                if last:
                    try:
                        _settle_tile(ch, tile, unit)
                    except Exception as exc:
                        _note(f"{shorten_pv_name(ch)}: {exc}")
            with lock:
                state["pending"] -= 1
                tile_pending[tile] -= 1
                state["done"] += 1
                if covers:
                    state["covered"] += b - a
            _emit_progress()
            _advance_edge()
            wake.set()

    # ── pre-flight: is the archiver there at all? ──────────────────────────
    # A big first request is allowed up to 120 s, so without this a dead
    # archiver would look like a two-minute hang. One minute of the newest end
    # at the short timeout answers that in 10 s. Skipped for short windows,
    # which are cheap enough to fail on their own. It runs BEFORE the density
    # pass below, so a dead archiver is still answered in one timeout instead
    # of one per channel.
    # It runs BESIDE the density check below (which starts at once): on a busy
    # archiver each of the two cost seconds, one after the other.
    dead_box = [None]

    def _preflight():
        probe_a = max(start_ns, end_ns - MIN_CHUNK_NS)
        for _attempt in (1, 2):
            try:
                cpva_fetch_samples(names_of[channels[0]][0], probe_a, end_ns,
                                   base_timeout, None)
                dead_box[0] = None
                return
            except Exception as e:
                # Any HTTP reply at all — even a refusal or a 404 for one bad
                # channel name — means the archiver is alive; only a failure
                # that never got a reply counts as unreachable.
                if getattr(e, "response", None) is not None:
                    dead_box[0] = None
                    return
                dead_box[0] = e

    pre_thread = None
    if preflight and span_total > 4 * CHUNK_SIZE_NS:
        pre_thread = threading.Thread(target=_preflight, daemon=True,
                                      name="cpva-preflight")
        pre_thread.start()

    def _preflight_failed():
        """Wait for the pre-flight; a report when the archiver is unreachable."""
        if pre_thread is None:
            return None
        # When the cache already gave most of the period, what is left is a
        # few requests that time out on their own if the archiver is gone —
        # waiting up to two full timeouts for the probe first held the bar at
        # 94 % (12.10.2026). A probe still out after a few seconds counts as
        # an answer to come.
        with lock:
            mostly_cached = whole and state["from_cache"] >= 0.9 * whole
        pre_thread.join(PREFLIGHT_CACHED_WAIT_S if mostly_cached else None)
        if pre_thread.is_alive():
            return None
        dead = dead_box[0]
        if dead is None:
            return None
        msg = f"{type(dead).__name__}: {dead}"
        _note(f"the archiver did not answer ({msg}) — nothing was read")
        for ch in channels:
            gaps[ch] = [(start_ns, end_ns)]
            errors[ch] = msg
        if progress_fn:
            progress_fn(1, 1)
        return ({ch: [] for ch in channels}, errors,
                FetchReport(gaps, boundaries, 1, 0, False, False, None,
                            span_total, time.monotonic() - t0))

    # ── who gets thinned, and who is read exactly as archived ──────────────
    # Thinning is for a channel the archiver writes thousands of times an hour;
    # for a motor position or a setting it is not a saving, it is damage. The
    # server hands back one point per minute whatever the target, so a stepping
    # channel arrives as minute-MEANS: the waveplate's 1049 real positions over
    # three days came back as 90 averages that the motor never stood on. One
    # cheap request per channel tells the two apart before anything else is
    # asked for, and a channel found to be sparse is read raw throughout —
    # which costs almost nothing, because sparse is exactly what it is.
    per_ch_count = {ch: count for ch in channels}
    if count and verify and span_total > 4 * CHUNK_SIZE_NS:
        _phase("Checking how densely each signal is archived")
        sparse = _channels_to_read_raw(channels, start_ns, end_ns,
                                       base_timeout, cancel_fn, max_workers,
                                       names_of=names_of)
        with lock:
            state["requests"] += sparse.pop("_requests", 0)
        for ch in sparse:
            per_ch_count[ch] = None
            sparse_raw.add(ch)
        if sparse:
            _note(f"read as archived, not thinned (too few samples to thin): "
                  f"{', '.join(shorten_pv_name(c) for c in sparse)}")

    # ── the day cache: closed days read before are not asked for again ─────
    cache_on = cache is not None and verify and not _is_cancelled(cancel_fn)
    if cache_on:
        whole_days = [d for lo, hi in tiles for d in _whole_days(lo, hi)]
        # The recent days too: the share may hold the part of today (or of
        # the window's first day) another load already read.
        recent_from = time.time() * 1e9 - RECENT_DAYS_NS
        part_days = [d for lo, hi in tiles for d in _prague_days(lo, hi)
                     if d[1] > recent_from and d not in whole_days]
        if whole_days or part_days:
            try:
                cache.prefetch(channels, whole_days + part_days, TZ_PRAGUE)
            except Exception as exc:
                _note(f"day cache unreadable ({exc}) — reading the archiver")
                cache_on = False

    def _from_cache(ch, lo, hi, tile_count):
        """The days of one tile the cache holds: ``(hits, missing)``.

        ``hits`` is ``[(day_lo, day_hi, samples), …]``; ``missing`` is the rest
        of the tile as coalesced ``(lo, hi)`` ranges, still to be read. A tile
        that holds today serves its closed days from the cache and reads only
        today (measured: the newest six-day step was read in full every time,
        because it was not ALL closed)."""
        hits = []
        for a, b in _whole_days(lo, hi):
            cnt = (None if tile_count is None
                   else max(MIN_CHUNK_COUNT, int(round(tile_count * (b - a) / (hi - lo)))))
            s = cache.get(ch, a, b, cnt, TZ_PRAGUE)
            if s is not None:
                hits.append((a, b, s))
        missing, cur = [], lo
        for a, b, _s in hits:
            if a > cur:
                missing.append((cur, a))
            cur = b
        if cur < hi:
            missing.append((cur, hi))
        # Parts of recent days an earlier load already read (in memory): the
        # open day, and the part day a "last N hours" window starts in.
        if missing and hasattr(cache, "recent_get"):
            density = None if tile_count is None else tile_count / (hi - lo)
            rest = []
            for mlo, mhi in missing:
                for dlo, dhi in _prague_days(mlo, mhi):
                    got = cache.recent_get(ch, _day_start(dlo), dlo, dhi,
                                           names_of[ch][0], density)
                    if got is None:
                        rest.append((dlo, dhi))
                        continue
                    a, b, smp = got
                    hits.append((a, b, smp))
                    recent_hits.add((ch, a))
                    if a > dlo:
                        rest.append((dlo, a))
                    if b < dhi:
                        rest.append((b, dhi))
            missing = _coalesce_ranges(rest)
        return hits, missing

    def _piece_for(ch, span):
        """The first request size for one tile of one channel."""
        if start_chunk_ns is not None:
            return max(min_chunk_ns, int(start_chunk_ns))
        o = oracles[ch]
        if o.bad and o.bad <= span:
            return o.plan_span(span, hint)          # it is already known too big
        if per_ch_count[ch] or ch in sparse_raw:
            return span
        # Exactly as archived and dense: start from what is known to work.
        return max(min_chunk_ns, min(span, max(o.good, RAW_START_CHUNK_NS)))

    filed: set = set()            # tiles whose cached part is handed over

    def _file_cached(ti):
        """Hand over what the cache and memory hold of one tile, and note what
        is left to ask the archiver for (``tile_todo``). Needs no archiver, so
        it runs for every tile before anything waits on one."""
        if ti in filed:
            return
        filed.add(ti)
        lo, hi = tiles[ti]
        tspan = hi - lo
        for ch in channels:
            tcnt = _share(per_ch_count[ch], tspan, span_total)
            tile_cnt[(ch, ti)] = tcnt
            tile_todo[(ch, ti)] = [(lo, hi)]
            if cache_on:
                hits, todo = _from_cache(ch, lo, hi, tcnt)
                tile_todo[(ch, ti)] = list(todo)
                if hits:
                    got = [s for _a, _b, smp in hits for s in smp]
                    with lock:
                        if not todo:
                            state["done"] += 1     # the planned slot, all cached
                        state["covered"] += sum(b - a for a, b, _s in hits)
                        state["from_cache"] += sum(b - a for a, b, _s in hits)
                        from_cache = state["from_cache"]
                        state["cached"] += sum(1 for a, _b, _s in hits
                                               if (ch, a) not in recent_hits)
                        boundaries[ch].append(hits[0][0])
                        parts[ch].append(got)
                        for a, _b, _s in hits:
                            cache_hit_days.add((ch, a))
                    # Say how much is read BEFORE handing the samples over:
                    # the caller's snapshots skip a nearly complete load, and
                    # with the old order one was drawn of what came from the
                    # cache, 2.3 s on the window's thread just before the end.
                    _emit_progress()
                    if chunk_fn and got:
                        try:
                            chunk_fn(ch, got)
                        except Exception:
                            pass
                    # After the samples are handed over, so a picture made on
                    # this news already holds them.
                    if cached_fn and whole:
                        try:
                            cached_fn(min(1.0, from_cache / whole))
                        except Exception:
                            pass

    def _feed_tile(ti):
        _file_cached(ti)
        lo, hi = tiles[ti]
        tspan = hi - lo
        open_day = start_chunk_ns is None and hi > time.time() * 1e9 - CLOSED_DAY_NS
        for ch in channels:
            tcnt = tile_cnt[(ch, ti)]
            todo = tile_todo[(ch, ti)]
            planned = True                         # the first request takes the slot
            for rlo, rhi in reversed(todo):        # newest first
                if len(names_of[ch]) > 1:
                    # A two-name channel is read one Prague day at a time:
                    # which name holds the data is decided per day.
                    for dlo, dhi in _prague_days(rlo, rhi)[::-1]:
                        _unit_requests(ch, ti, dlo, dhi, 0, first_planned=planned)
                        planned = False
                    continue
                span = rhi - rlo
                piece = _piece_for(ch, span)
                if open_day:
                    piece = min(piece, OPEN_DAY_PIECE_NS)
                k = max(1, int(math.ceil(span / piece)))
                for i in range(k - 1, -1, -1):
                    a = rlo + (span * i) // k
                    b = rhi if i == k - 1 else rlo + (span * (i + 1)) // k
                    if b > a:
                        _submit(ch, a, b, _share(tcnt, b - a, tspan), ti,
                                planned=planned)
                        planned = False
        with lock:
            tile_fed[ti] = True
        _emit_progress()
        _advance_edge()

    # Count the whole plan up front, so the reported total only ever grows when
    # a request genuinely splits into more work.
    with lock:
        state["total"] = len(tiles) * len(channels)
    if progress_fn:
        emitted[1] = state["total"]
        progress_fn(0, state["total"])

    # Everything the cache and memory hold goes out first, newest step first —
    # it needs no archiver, so it must not wait for the pre-flight request
    # below (measured: on a reload the picture waited 2–4 s for that one
    # minute of data, the archiver's answer time at that moment).
    if cache_on:
        for ti in range(len(tiles)):
            if _is_cancelled(cancel_fn):
                break
            _file_cached(ti)

    failed = _preflight_failed()
    next_tile = 0
    if failed is not None:
        if not state["from_cache"]:
            return failed
        # The archiver is not there, but the cache gave part of the period:
        # keep that, and only the rest is unread.
        for ch in channels:
            gaps[ch] = [r for ti in range(len(tiles))
                        for r in tile_todo.get((ch, ti), [tiles[ti]])]
            if gaps[ch]:
                tile_bad.update((ch, ti) for ti in range(len(tiles)))
        next_tile = len(tiles)

    workers = max(1, min(max_workers, len(tiles) * len(channels)))
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        while True:
            if _is_cancelled(cancel_fn):
                with lock:
                    state["cancelled"] = True
                break
            # Keep the pool busy, but feed whole days in order: a day is
            # queued only when the pool has room, so the newest days always go
            # out first and a cancel leaves little queued behind it.
            while next_tile < len(tiles):
                with lock:
                    busy = state["pending"]
                if busy >= workers:
                    break
                _feed_tile(next_tile)
                next_tile += 1
            with lock:
                busy = state["pending"]
            if next_tile >= len(tiles) and busy == 0:
                break
            wake.wait(0.25)
            wake.clear()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    # Everything never fed (a cancel) is unread, and said so.
    for ti in range(next_tile, len(tiles)):
        lo, hi = tiles[ti]
        for ch in channels:
            gaps[ch].append((lo, hi))
            tile_bad.add((ch, ti))

    # ── merge: by sample time, not by request order ────────────────────────
    # Every request also returns the sample just before its own start, so
    # neighbours overlap. With adaptive splitting a child's anchor can predate
    # its parent's start, so request order is not time order — and
    # cpva_fetch_last_before relies on the last element being the newest.
    with lock:
        snap_parts = {ch: list(parts[ch]) for ch in channels}
    out: dict[str, list] = {}
    for ch in channels:
        merged = sorted(itertools.chain.from_iterable(snap_parts[ch]),
                        key=lambda s: (s.get("time") or 0))
        seen, clean = set(), []
        for s in merged:
            t = s.get("time")
            if t in seen:
                continue
            seen.add(t)
            clean.append(s)
        out[ch] = clean

    # ── a channel judged sparse that turned out not to be ──────────────────
    # Two quiet probe hours can still belong to a fast meter that ran for one
    # day of the period. Holding (and drawing) a million samples is worse than
    # the thinned series, so take that after all — one request, and only in
    # this rare case.
    over = [ch for ch in sparse_raw
            if ch in overflow
            or (len(out[ch]) > VERIFY_MAX_SAMPLES and not gaps[ch])]
    if over and not _is_cancelled(cancel_fn):
        _phase(f"Thinning {len(over)} signal{'' if len(over) == 1 else 's'} "
               f"too big to read as archived")
        thin_out, _thin_err, thin_rep = _fetch_many_adaptive(
            over, start_ns, end_ns, count=count, timeout=base_timeout,
            cancel_fn=cancel_fn, preflight=False, verify=False, aliases=aliases)
        with lock:
            state["requests"] += thin_rep.requests
        for ch in over:
            if not thin_out.get(ch):
                continue
            _note(f"{shorten_pv_name(ch)}: reading it as archived is over "
                  f"{max(len(out[ch]), held.get(ch, 0))} samples — thinned after all")
            out[ch] = thin_out[ch]
            sparse_raw.discard(ch)
            boundaries[ch] = list(thin_rep.boundaries.get(ch) or [])

    # ── the thinned series is checked against the archive itself ───────────
    # The server's decimation levels are not built for every channel, and a
    # missing one is answered with a flat run of interpolated points rather
    # than with nothing — which the Logger then drew as a solid line across
    # days. Only channels that look wrong are checked, and only the sparse ones
    # are read again, so a normal load costs nothing extra.
    rechecked: list = []
    if count and verify and state["decimated"] and not state["cancelled"]:
        suspects = []
        for ch in channels:
            if gaps[ch] or not out[ch] or not per_ch_count[ch]:
                continue          # incomplete, empty, or already read raw
            if len(names_of[ch]) > 1:
                # Already judged day by day (an invented grid sends the day to
                # the other name, or out); the days no name holds are empty on
                # purpose and would only make the series look short.
                continue
            bad, why = _decimated_is_suspect(out[ch], start_ns, end_ns)
            if bad:
                suspects.append((ch, why))
        if suspects and not _is_cancelled(cancel_fn):
            _phase(f"Checking {len(suspects)} thinned "
                   f"signal{'' if len(suspects) == 1 else 's'} against the archive")
            rechecked, extra_req = _recheck_decimated(
                suspects, out, gaps, boundaries, start_ns, end_ns,
                base_timeout, cancel_fn, log_fn, max_workers)
            with lock:
                state["requests"] += extra_req

    # ── keep the closed days for the next load ─────────────────────────────
    if cache_on and not state["cancelled"]:
        now = int(time.time() * 1e9)
        replaced = set(over) | set(rechecked)
        for (ch, ti), got in tile_got.items():
            if ch in replaced or (ch, ti) in tile_bad:
                continue
            lo, hi = tiles[ti]
            src = ",".join(sorted(tile_src.get((ch, ti), ()))) or names_of[ch][0]
            flat = list(itertools.chain.from_iterable(got))
            tcnt = tile_cnt.get((ch, ti))
            for a, b in _whole_days(lo, hi):
                if (ch, a) in cache_hit_days:
                    continue                  # came from the cache in the first place
                cnt = (None if tcnt is None
                       else max(MIN_CHUNK_COUNT, int(round(tcnt * (b - a) / (hi - lo)))))
                try:
                    cache.put(ch, a, b, flat, cnt, src, TZ_PRAGUE, now)
                except Exception:
                    pass
            # Recent days: what was asked for now joins the blocks in memory.
            if hasattr(cache, "recent_put"):
                if tile_src.get((ch, ti), set()) - {names_of[ch][0]}:
                    continue              # another name answered: not kept
                density = None if tcnt is None else tcnt / (hi - lo)
                for rlo, rhi in tile_todo.get((ch, ti), ()):
                    for dlo, dhi in _prague_days(rlo, rhi):
                        try:
                            cache.recent_put(ch, _day_start(dlo), dlo, dhi, flat,
                                             names_of[ch][0], density, now,
                                             tz=TZ_PRAGUE)
                        except Exception:
                            pass
        cache.flush_async()

    for ch, answered in alias_days.items():
        others = {n: len(t) for n, t in answered.items() if n != names_of[ch][0]}
        for n, k in others.items():
            _note(f"{shorten_pv_name(ch)}: {k} day{'' if k == 1 else 's'} "
                  f"read under {n}")

    for ch in channels:
        g = _coalesce_ranges(gaps[ch])
        gaps[ch] = g
        boundaries[ch].sort()
        if g:
            unread = sum(b - a for a, b in g)
            errors[ch] = (
                f"{len(g)} range(s) unread ({unread/3.6e12:.2f} h of "
                f"{span_total/3.6e12:.2f} h) — first "
                f"{ns_to_local_str(g[0][0])[:16]} → {ns_to_local_str(g[0][1])[:16]}"
                + (f": {first_exc[ch]}" if ch in first_exc else ""))
        elif ch in first_exc and not out[ch]:
            errors[ch] = first_exc[ch]

    report = FetchReport(gaps, boundaries, state["requests"], state["splits"],
                         state["cancelled"], state["over_budget"],
                         state["decimated"], span_total, time.monotonic() - t0,
                         tuple(rechecked), state["cached"])
    if state["over_budget"]:
        _note(f"stopped after {max_requests} requests — the period is too long "
              f"to read in full at this level of detail")
    return out, errors, report


# ── Channels archived under two names ─────────────────────────────────────────
# SBW4's energy is archived under the HAPLS-era name and the L3 name, and the two
# take turns with the laser configuration, not with the calendar. The operator's
# rule (23.09.2026): read the HAPLS name first, and the L3 one only where the
# HAPLS name holds nothing. Image Tools/cpva_client.py (channel_aliases,
# read_order) is the master copy of this rule; keep the two in step.
# Never add a pair without evidence: an alias says two names are the SAME
# physical measurement.
SBW4_HAPLS = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"
SBW4_L3    = "L3-SBW4-PM311:Energy"

_CHANNEL_ALIASES: dict[str, tuple[str, ...]] = {
    SBW4_HAPLS: (SBW4_L3,),
    SBW4_L3:    (SBW4_HAPLS,),
}

# Which name is asked FIRST, whichever of them the user picked. The operator's
# rule: SBW4 is looked for under the HAPLS name, and under the L3 name only where
# that holds nothing.
_READ_FIRST: dict[str, str] = {
    SBW4_HAPLS: SBW4_HAPLS,
    SBW4_L3:    SBW4_HAPLS,
}


def channel_aliases(channel: str) -> tuple[str, ...]:
    """The other names this channel is archived under (usually none)."""
    return _CHANNEL_ALIASES.get(channel, ())


def read_order(channel: str) -> tuple[str, ...]:
    """Every name this measurement may be archived under, in the order to ask.

    One name for everything except SBW4. The order is a cost rule only: the
    second name is asked whenever the first holds nothing for the range, so a
    wrong guess costs one extra request, never a missing answer."""
    alts = channel_aliases(channel)
    if not alts:
        return (channel,)
    first = _READ_FIRST.get(channel, channel)
    rest = [n for n in (channel,) + alts if n != first]
    return (first, *rest)


def _drop_after(samples: list, end_ns: int) -> list:
    """The reply without the samples stamped AFTER the range asked for.

    The archiver brackets a range on both sides, and the trailing sample can be
    weeks later: measured 23.09.2026, `L3-SBW4-PM311:Energy` asked for 01.09.
    answered with one sample stamped 22.09. Kept, it makes a day the name holds
    nothing for look like a day with data. The sample from BEFORE the range
    stays: that one is the real held-forward value."""
    if not samples or all((s.get("time") or 0) <= end_ns for s in samples[-3:]):
        return samples
    return [s for s in samples if (s.get("time") or 0) <= end_ns]


def _holds_nothing(samples: list, start_ns: int, end_ns: int) -> bool:
    """Does this reply say the name holds nothing for [start, end)?

    True when no sample lies inside the range, and also when a THINNED reply is
    a grid the archiver invented: measured 12.10.2026, a day that holds no
    sample under a name is answered to `count=` with 144 interpolated points,
    all one value, every bin's minimum equal to its maximum. That grid hid the
    L3 name's real data for 12.-29.08.2026, because a reply that is not empty
    was never asked for under SBW4's other name."""
    inside = [s for s in samples if start_ns <= (s.get("time") or 0) < end_ns]
    if not inside:
        return True
    if any(s.get("quality") != "Interpolated" for s in inside):
        return False
    first = inside[0].get("value")
    for s in inside:
        lo, hi = s.get("minimum"), s.get("maximum")
        if lo is not None and hi is not None and lo != hi:
            return False
        if s.get("value") != first:
            return False
    return True


def _prague_days(start_ns: int, end_ns: int) -> list:
    """[start, end) cut at Prague midnights: ``[(lo, hi), …]``."""
    out = []
    lo = int(start_ns)
    while lo < end_ns:
        d = datetime.fromtimestamp(lo / 1e9, TZ_PRAGUE).date()
        # The next midnight, found by stepping a day on the calendar so a DST
        # change (a 23 or 25 hour day) lands on the real midnight.
        nd = datetime.fromordinal(d.toordinal() + 1).replace(tzinfo=TZ_PRAGUE)
        hi = min(int(end_ns), int(nd.timestamp() * 1e9))
        if hi <= lo:            # cannot happen, but never loop forever
            hi = int(end_ns)
        out.append((lo, hi))
        lo = hi
    return out


def _day_start(ns: int) -> int:
    """The Prague midnight that starts the day ``ns`` falls in."""
    d = datetime.fromtimestamp(ns / 1e9, TZ_PRAGUE).date()
    return int(datetime(d.year, d.month, d.day, tzinfo=TZ_PRAGUE).timestamp() * 1e9)


def _empty_day_ranges(samples: list, start_ns: int, end_ns: int) -> list:
    """The Prague days of [start, end) holding no sample of ``samples``, with
    neighbouring empty days joined into one range. The archiver's bracket
    samples (just before the start, just after the end) do not count."""
    ts = np.sort(np.array([s.get("time") or 0 for s in samples], dtype=np.int64))
    ranges = []
    for lo, hi in _prague_days(start_ns, end_ns):
        i = int(np.searchsorted(ts, lo, side="left"))
        if i < ts.size and ts[i] < hi:
            continue                      # this day has data under the name
        if ranges and ranges[-1][1] == lo:
            ranges[-1] = (ranges[-1][0], hi)
        else:
            ranges.append((lo, hi))
    return ranges


def cpva_fetch_many_adaptive(channels: list[str], start_ns: int, end_ns: int,
                             count: int | None = None,
                             timeout: float | None = None,
                             max_workers: int = 16,
                             progress_fn=None,
                             cancel_fn=None,
                             log_fn=None,
                             chunk_fn=None,
                             coverage_fn=None,
                             phase_fn=None,
                             start_chunk_ns: int | None = None,
                             min_chunk_ns: int = MIN_CHUNK_NS,
                             max_requests: int = FETCH_MAX_REQUESTS,
                             preflight: bool = True,
                             verify: bool = True,
                             tile_fn=None,
                             cache=None,
                             cached_fn=None):
    """The one fetch: :func:`_fetch_many_adaptive`, day by day, newest first.

    A channel archived under two names (SBW4) is read under the HAPLS name
    first and under the L3 name for every piece the HAPLS name holds nothing
    for — inside the same pool, while the load runs, so those days are on the
    graph as early as any other. Whatever answers is filed under the name
    asked for, so the SBW4 column and every custom PV built on it covers the
    days either name was the one being written.
    """
    return _fetch_many_adaptive(
        channels, start_ns, end_ns, count=count, timeout=timeout,
        max_workers=max_workers, progress_fn=progress_fn, cancel_fn=cancel_fn,
        log_fn=log_fn, chunk_fn=chunk_fn, coverage_fn=coverage_fn,
        phase_fn=phase_fn, start_chunk_ns=start_chunk_ns,
        min_chunk_ns=min_chunk_ns, max_requests=max_requests,
        preflight=preflight, verify=verify, tile_fn=tile_fn, cache=cache,
        cached_fn=cached_fn)


def cpva_fetch_many_chunked(channels: list[str], start_ns: int, end_ns: int,
                            timeout: float = CPVA_HTTP_TIMEOUT,
                            max_workers: int = 16,
                            progress_fn=None,
                            cancel_fn=None):
    """Raw (undecimated) read of several channels — see cpva_fetch_many_adaptive.

    Kept as the two-value form for callers that do not need the report.
    """
    res, errors, _report = cpva_fetch_many_adaptive(
        channels, start_ns, end_ns, count=None, timeout=timeout,
        max_workers=max_workers, progress_fn=progress_fn, cancel_fn=cancel_fn)
    return res, errors


def cpva_fetch_many_optimized(channels: list[str], start_ns: int, end_ns: int,
                              count: int,
                              timeout: float = CPVA_HTTP_TIMEOUT,
                              max_workers: int = 16,
                              progress_fn=None,
                              cancel_fn=None):
    """Decimated read of several channels — see cpva_fetch_many_adaptive.

    Kept as the two-value form for callers that do not need the report.
    """
    res, errors, _report = cpva_fetch_many_adaptive(
        channels, start_ns, end_ns, count=count, timeout=timeout,
        max_workers=max_workers, progress_fn=progress_fn, cancel_fn=cancel_fn)
    return res, errors


# Cumulative look-back horizons (seconds) for hunting the most recent sample
# before a time. We scan the NEW slice at each step (near → far) and stop at the
# first hit, so PVs with recent data cost one request and only truly-stale PVs
# pay for the deeper scan.
_LAST_BEFORE_STEPS_S = (3600, 6 * 3600, 24 * 3600,
                        3 * 24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600)

# Enough points to be sure the newest one in the range is among them, few
# enough that the archiver will serve a wide range in one go.
_LAST_BEFORE_COUNT = 100

# The hunt is BOUNDED. It used to halve a refused range all the way down to one
# minute, newest half first, at up to 120 s per request — a three-month load
# sat on "92 %" in this step until the program was killed (reported 09.10.2026).
# A range is now halved only while it is longer than six hours, each name gets
# a fixed number of requests, and the whole hunt has a deadline. A signal whose
# value before the window is not found in time simply starts where its first
# sample is — which the graph already shows honestly.
_LAST_BEFORE_MIN_SPLIT_NS = 6 * CHUNK_SIZE_NS
LAST_BEFORE_MAX_REQUESTS = 12          # per archived name
LAST_BEFORE_DEADLINE_S = 20.0          # for one call, all names together


class _HuntBudget:
    """Requests left, a deadline and the caller's cancel, for one look-back."""

    __slots__ = ("left", "deadline", "cancel_fn")

    def __init__(self, requests: int, deadline: float, cancel_fn=None):
        self.left = int(requests)
        self.deadline = float(deadline)
        self.cancel_fn = cancel_fn

    def take(self) -> bool:
        if self.left <= 0 or time.monotonic() >= self.deadline:
            return False
        if _is_cancelled(self.cancel_fn):
            return False
        self.left -= 1
        return True

    def seconds_left(self) -> float:
        return max(0.5, self.deadline - time.monotonic())


def _last_before_in_range(channel: str, lo: int, hi: int,
                          timeout: float = CPVA_HTTP_TIMEOUT,
                          cancel_fn=None, budget: _HuntBudget = None,
                          strict: bool = False):
    """Newest sample in [lo, hi), hunted NEWEST HALF FIRST, or None.

    One request when the archiver will serve the whole range, and a few when it
    will not — never below six hours, and never past the ``budget``.

    ``strict`` (a channel with two archived names) refuses an answer the
    archiver invented: a thinned reply for a range the name holds nothing for
    is a flat grid of interpolated points (see :func:`_holds_nothing`), and
    taking its value would hold a number nobody measured across the window.
    """
    if hi <= lo or _is_cancelled(cancel_fn):
        return None
    if budget is None:
        budget = _HuntBudget(LAST_BEFORE_MAX_REQUESTS,
                             time.monotonic() + LAST_BEFORE_DEADLINE_S, cancel_fn)
    if not budget.take():
        return None
    try:
        raw = cpva_fetch_samples(channel, lo, hi,
                                 min(_timeout_for_span(hi - lo, timeout),
                                     budget.seconds_left()),
                                 _LAST_BEFORE_COUNT)
    except Exception as exc:
        if not (_is_splittable_error(exc) and (hi - lo) > _LAST_BEFORE_MIN_SPLIT_NS):
            return None
        mid = lo + (hi - lo) // 2
        return (_last_before_in_range(channel, mid, hi, timeout, cancel_fn,
                                      budget, strict)
                or _last_before_in_range(channel, lo, mid, timeout, cancel_fn,
                                         budget, strict))
    if not raw:
        return None
    # The archiver brackets the range on BOTH sides: the reply carries the
    # sample just before `lo` and the one just after `hi`. Taking the newest of
    # the whole reply therefore hands back a value from the future — measured
    # 2026-09-21 on HAPLS-SPEC_CENT_PD1M1_LT7_DIAG1:SpectralCentroid, where the
    # value "before" 10.9. came back stamped 19.9. and was then held flat across
    # the whole window. Only samples genuinely older than `hi` may answer.
    # A sample older than `lo` is kept: it still is the newest one before `hi`,
    # and accepting it saves the caller the deeper look-back rings.
    older = [s for s in raw if (s.get("time") or 0) < hi]
    if strict and _holds_nothing(raw, lo, hi):
        older = [s for s in older if s.get("quality") != "Interpolated"]
    if not older:
        return None
    return max(older, key=lambda s: (s.get("time") or 0))


def cpva_fetch_last_before(channel: str, before_ns: int,
                           timeout: float = CPVA_HTTP_TIMEOUT,
                           cancel_fn=None, deadline: float | None = None):
    """Return the most recent sample dict strictly before `before_ns`, or None.

    Scans expanding rings back to ~30 days and stops at the first ring that
    holds data, so a PV whose last update predates the requested window can
    still be carried forward instead of leaving a gap in the plot. Bounded:
    see LAST_BEFORE_MAX_REQUESTS / LAST_BEFORE_DEADLINE_S. ``deadline`` is a
    ``time.monotonic()`` value shared by a caller hunting many channels.

    A channel archived under two names (SBW4) follows the same day rule as the
    load: the HAPLS name is hunted first, and its answer stands when it comes
    from the same Prague day (that day is written under HAPLS). Otherwise the
    other name is hunted too and the NEWER sample wins — the names take turns,
    so the newer one was the one being written when the window starts.
    """
    if deadline is None:
        deadline = time.monotonic() + LAST_BEFORE_DEADLINE_S
    names = read_order(channel)
    strict = len(names) > 1

    def _hunt(name):
        return _last_before_one(name, before_ns, timeout, cancel_fn,
                                _HuntBudget(LAST_BEFORE_MAX_REQUESTS, deadline,
                                            cancel_fn), strict)

    first = _hunt(names[0])
    if len(names) == 1:
        return first
    day = datetime.fromtimestamp(before_ns / 1e9, TZ_PRAGUE).date()
    if first and datetime.fromtimestamp((first.get("time") or 0) / 1e9,
                                        TZ_PRAGUE).date() == day:
        return first
    found = [s for s in [first] + [_hunt(n) for n in names[1:]] if s]
    return max(found, key=lambda s: s.get("time") or 0) if found else None


def _last_before_one(channel: str, before_ns: int,
                     timeout: float = CPVA_HTTP_TIMEOUT,
                     cancel_fn=None, budget: _HuntBudget = None,
                     strict: bool = False):
    """:func:`cpva_fetch_last_before` for exactly the name given."""
    if budget is None:
        budget = _HuntBudget(LAST_BEFORE_MAX_REQUESTS,
                             time.monotonic() + LAST_BEFORE_DEADLINE_S, cancel_fn)
    hi = before_ns
    for step_s in _LAST_BEFORE_STEPS_S:
        if _is_cancelled(cancel_fn) or budget.left <= 0:
            return None
        lo = max(0, before_ns - int(step_s * 1e9))
        if lo >= hi:
            break
        found = _last_before_in_range(channel, lo, hi, timeout, cancel_fn,
                                      budget, strict)
        if found:
            return found
        hi = lo
        if lo == 0:
            break
    return None


def cpva_fetch_last_before_many(channels: list[str], before_ns: int,
                                timeout: float = CPVA_HTTP_TIMEOUT,
                                max_workers: int = 8,
                                progress_fn=None,
                                cancel_fn=None,
                                deadline_s: float = LAST_BEFORE_DEADLINE_S):
    """Parallel :func:`cpva_fetch_last_before` for several channels at once.

    Returns ``{channel: sample_dict}`` only for channels that actually had a
    prior sample. Never takes much longer than ``deadline_s``: whatever has not
    answered by then is left out, and a cancel returns at once — the pool is
    abandoned, not waited for (the old ``with`` block waited for every running
    hunt even after a cancel).
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    out: dict[str, dict] = {}
    if not channels:
        return out
    deadline = time.monotonic() + max(0.5, float(deadline_s))
    workers = min(max_workers, len(channels))
    done = 0
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            ex.submit(cpva_fetch_last_before, ch, before_ns, timeout, cancel_fn,
                      deadline): ch
            for ch in channels
        }
        pending = set(futures)
        # Polled, so a cancel or the deadline ends the wait at once instead of
        # when the next running request happens to come back.
        while pending and not _is_cancelled(cancel_fn) \
                and time.monotonic() < deadline + 1.0:
            finished, pending = wait(pending, timeout=0.25,
                                     return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    res = fut.result()
                except Exception:
                    res = None
                if res:
                    out[futures[fut]] = res
                done += 1
                if progress_fn:
                    progress_fn(done, len(channels))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
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
    data = _get_json_or_backup(url, "/channels-by-pattern/" +
                               urllib.parse.quote("*", safe=""), timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response from /channels: {type(data).__name__}")
    global _cpva_names
    out = [str(x) for x in data]
    with _ROUTE_LOCK:
        _cpva_names = set(out)
    return out + extra_channel_names(set(out), timeout=timeout)


def extra_channel_names(known=(), timeout: float = CPVA_HTTP_TIMEOUT) -> list[str]:
    """The L3BT and FCT proxies' names that are not in `known`, sorted, so a
    picker can offer their channels too. The same lists route the reads, so a
    channel added since the first read becomes readable. A proxy that does not
    answer adds nothing."""
    seen = set(known)
    out: list[str] = []
    for key, urls in EXTRA_ARCHIVES.items():
        try:
            extra = _names_of(_proxy_get_json(urls, _ALL_CHANNELS_PATH, timeout))
        except Exception:
            continue
        with _ROUTE_LOCK:
            _proxy_names[key] = set(extra)
        out.extend(sorted(extra - seen))
        seen |= extra
    return out


# ---------------------------------------------------------------------------
# Shots: the moments the laser fired, found from the channels' own writes
# ---------------------------------------------------------------------------
# A shot is a moment when MOST of the channels running at the shot rate wrote
# a value. One channel alone never decides: any channel can skip a shot (it
# writes only when its value changes, or the archive drops a sample), and a
# channel running on its own clock (a housekeeping reading every minute, a
# camera running free at 10 Hz) is not on the shot rate at all and has no vote.

SHOT_RATE_TOL      = 1.25   # own gaps within ×1.25 of each other are one rate
SHOT_JOIN_FRACTION = 0.4    # writes closer than 0.4 × the shot gap are one moment
SHOT_MIN_SAMPLES   = 4      # fewer samples than this cannot tell a channel's rate


def _median_gap(ts) -> "float | None":
    ts = np.asarray(ts)
    if ts.size < SHOT_MIN_SAMPLES:
        return None
    d = np.diff(ts)
    d = d[d > 0]
    return float(np.median(d)) if d.size else None


def shot_rate(ts_by_pv: dict) -> "tuple[float | None, list]":
    """The shot gap (ns) and the channels that run on it.

    ``ts_by_pv`` maps each real channel to its sorted sample times (ns). The
    shot gap is the gap most channels repeat at (a tie goes to the slower one:
    a channel running free is never slower than the shots). Returns
    ``(None, [])`` when no channel has enough samples to tell."""
    gaps = {}
    for pv, ts in ts_by_pv.items():
        g = _median_gap(ts)
        if g:
            gaps[pv] = g
    if not gaps:
        return None, []
    groups: "list[list[float]]" = []
    for g in sorted(gaps.values()):
        if groups and g <= groups[-1][0] * SHOT_RATE_TOL:
            groups[-1].append(g)
        else:
            groups.append([g])
    best = max(groups, key=lambda grp: (len(grp), grp[0]))
    gap = best[len(best) // 2]
    members = [pv for pv, g in gaps.items() if best[0] <= g <= best[-1]]
    return gap, members


def shot_moments(ts_by_pv: dict, gap_ns: float, members: list):
    """(starts, ends) int64 arrays of the shots: the first and the last write
    of every moment that at least half of ``members`` wrote in.

    Writes closer together than 0.4 × the shot gap belong to one moment, so a
    channel archived a little after the others still lands on its shot."""
    empty = np.empty(0, dtype=np.int64)
    if not gap_ns or not members:
        return empty, empty
    parts, who = [], []
    for i, pv in enumerate(members):
        ts = np.asarray(ts_by_pv.get(pv, ()), dtype=np.float64)
        if ts.size:
            parts.append(ts.astype(np.int64))
            who.append(np.full(ts.size, i, dtype=np.int64))
    if not parts:
        return empty, empty
    t = np.concatenate(parts)
    w = np.concatenate(who)
    order = np.argsort(t, kind="stable")
    t, w = t[order], w[order]
    tol = SHOT_JOIN_FRACTION * float(gap_ns)
    new = np.empty(t.size, dtype=bool)
    new[0] = True
    new[1:] = np.diff(t) > tol
    moment = np.cumsum(new) - 1
    n_mom = int(moment[-1]) + 1
    k = len(members)
    # A channel that wrote twice in one moment still has one vote.
    voted = np.unique(moment * k + w) // k
    votes = np.bincount(voted, minlength=n_mom)
    ok = votes >= max(1, math.ceil(k / 2))
    starts = t[new][ok]
    last = np.empty(t.size, dtype=bool)
    last[:-1] = new[1:]
    last[-1] = True
    ends = t[last][ok]
    return starts, ends


def find_shots(ts_by_pv: dict, rate=None):
    """Every shot in ``ts_by_pv``: ``(starts, ends, gap_ns, members)``.

    ``rate`` is a ``(gap_ns, members)`` found elsewhere — used when these
    samples are too few to tell the rate themselves (a short selection)."""
    gap, members = shot_rate(ts_by_pv)
    if (gap is None or not members) and rate is not None:
        gap, members = rate
        members = [pv for pv in members if pv in ts_by_pv]
    starts, ends = shot_moments(ts_by_pv, gap, members)
    return starts, ends, gap, members


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


def _fmt_percent_of(value: float, reference: float) -> str:
    """`value` as a percentage of `reference`, to hundredths.

    Used for the selection statistics: a scatter of 0.07 on a channel that reads
    1.0 means 7 %, and that is the number an operator judges a shot by — the
    absolute figure says nothing without the average beside it.

    A reference of zero (or one so small that the ratio explodes) has no honest
    percentage, so it says so instead of printing a huge number.
    """
    try:
        v = float(value)
        r = abs(float(reference))
    except (TypeError, ValueError):
        return "—"
    if v != v or r != r:                  # NaN on either side
        return "—"
    if r == 0 or r < abs(v) * 1e-9:
        return "—"
    return f"{100.0 * v / r:.2f} %"


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
# Each name is only dropped when it is a WHOLE dash/underscore segment. Without
# the boundary guards the "IN" alternative also matched inside a word, so
# L3-TIMING-TIMING:SysRate was labelled "L3-TIM_G-TIM_G - SysRate" — on the
# graph, in the table header and in the exported file.
_STRIP_PATTERNS = re.compile(
    r"(?<![A-Za-z0-9])(?:HAPLS|ENER|IN|LT\d+|DIAG\d*)(?![A-Za-z0-9])[-_]?|"
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
    # Collapse any run of separators, not just underscores: a dropped segment
    # leaves its own "_" next to the dash that separated it, and "PFM8-_" would
    # otherwise come out as "PFM8-".
    device = re.sub(r"[-_]{2,}", "_", device).strip("-_")

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

    Every whitespace-separated token must occur somewhere in the name, in ANY
    order — "023 l3" and "l3 023" find the same channels. Explicit "*" / "?"
    inside a token still work (and "*" is what lets a token match across a gap
    in the middle of a word).

    Compiled once per query, then called for every channel — the archiver lists
    ~10 000 of them and this runs on each keystroke.
    """
    tokens = (query or "").strip().lower().split()
    if not tokens:
        return lambda _text: True
    rxs = [re.compile("".join(
               ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
               for ch in tok))
           for tok in tokens]

    def match(text):
        low = text.lower()
        return all(rx.search(low) is not None for rx in rxs)
    return match


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
