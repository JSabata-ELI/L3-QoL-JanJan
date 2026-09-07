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
FETCH_MAX_REQUESTS = 20_000                 # hard budget for one fetch call

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


def cpva_fetch_many_adaptive(channels: list[str], start_ns: int, end_ns: int,
                             count: int | None = None,
                             timeout: float | None = None,
                             max_workers: int = 16,
                             progress_fn=None,
                             cancel_fn=None,
                             log_fn=None,
                             chunk_fn=None,
                             start_chunk_ns: int | None = None,
                             min_chunk_ns: int = MIN_CHUNK_NS,
                             max_requests: int = FETCH_MAX_REQUESTS,
                             preflight: bool = True):
    """Read several channels over [start_ns, end_ns), whatever its length.

    Asks for as much time per request as the archiver will actually serve, and
    halves only what it refuses. The archiver answers HTTP 500 when a single
    response would carry too many samples, so the old fixed shapes both failed:
    one request over the whole window lost the entire channel on the first 500,
    and a rigid one-hour grid needed 70 000 requests for a year.

    ``count`` > 0 asks for server-side decimation and is shared out across the
    requests in proportion to their span, so the total returned stays near the
    target instead of being multiplied by the number of requests.

    Work is queued NEWEST FIRST, so a long load fills the graph in from the
    right-hand (present) edge while it runs.

    Returns ``(results, errors, report)``:
      * ``results``  channel -> time-ordered, de-duplicated sample list
      * ``errors``   channel -> one human sentence (same shape as before)
      * ``report``   :class:`FetchReport`, including the ranges NOT read

    ``progress_fn(done, total)`` is called from worker threads. ``total`` is a
    live estimate that only ever GROWS: it starts at the number of planned
    requests, and each time one splits into *k* pieces the parent counts as
    done and ``total`` grows by *k*. ``done`` never falls, ``total`` never falls,
    ``done <= total`` always, and the last call has ``done == total``.

    ``chunk_fn(channel, samples)`` is called as each request lands, for callers
    that want to paint partial results. ``log_fn(text)`` gets one-line notes.
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

    oracles = {ch: _SpanOracle() for ch in channels}
    hint = _SpanOracle()          # shared, consulted only for a FIRST attempt
    t0 = time.monotonic()
    lock = threading.Lock()
    finished = threading.Event()
    state = {"pending": 1, "done": 0, "total": 0, "requests": 0, "splits": 0,
             "cancelled": False, "over_budget": False, "decimated": None,
             "noted_span": {}}
    ex: object = None
    emit_lock = threading.Lock()
    emitted = [0, 0]

    def _note(text: str) -> None:
        if log_fn:
            try:
                log_fn(text)
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
        if progress_fn is None:
            return
        with emit_lock:
            with lock:
                d, t = state["done"], state["total"]
            if d < emitted[0] or t < emitted[1]:
                return                    # a fresher snapshot already went out
            emitted[0], emitted[1] = d, t
            try:
                progress_fn(d, t)
            except Exception:
                pass

    def _submit(ch, a, b, cnt, planned: bool = False, probe: bool = False) -> bool:
        """Queue one request. False (and a recorded gap) when the budget is spent.

        A `planned` request is already counted in "total" (the whole plan is
        counted up front, so the progress total never has to shrink); a split
        child is new work and grows the total.
        """
        with lock:
            if state["requests"] >= max_requests:
                state["over_budget"] = True
                gaps[ch].append((a, b))
                if planned:
                    state["done"] += 1
                return False
            state["requests"] += 1
            state["pending"] += 1
            if not planned:
                state["total"] += 1
        try:
            ex.submit(_task, ch, a, b, cnt, probe)
        except RuntimeError:
            # The pool was shut down under us (a cancel while a split was being
            # queued). Retire the slot as done — never shrink "total", callers
            # are promised it only grows — and record the range as unread.
            with lock:
                state["pending"] -= 1
                state["done"]    += 1
                state["cancelled"] = True
                gaps[ch].append((a, b))
                pend = state["pending"]
            if pend == 0:
                finished.set()
            return False
        return True

    def _split(ch, a, b, cnt, span):
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
                _submit(ch, ca, cb, _share(cnt, cb - ca, span))

    def _task(ch, a, b, cnt, probe: bool = False):
        try:
            if _is_cancelled(cancel_fn):
                with lock:
                    state["cancelled"] = True
                    gaps[ch].append((a, b))
                return
            span = b - a
            tmo = _timeout_for_span(span, base_timeout)
            samples, exc = None, None
            try:
                samples = cpva_fetch_samples(ch, a, b, tmo, cnt)
            except Exception as e:
                exc = e
            if exc is not None and not _is_splittable_error(exc):
                # A dropped keep-alive is routine on a long load and the adapter
                # retries nothing of its own. Try once more, but never fan out.
                try:
                    samples, exc = cpva_fetch_samples(ch, a, b, tmo, cnt), None
                except Exception as e:
                    exc = e
            if exc is not None:
                with lock:
                    first_exc.setdefault(ch, f"{type(exc).__name__}: {exc}")
                if _is_splittable_error(exc) and span > min_chunk_ns:
                    _split(ch, a, b, cnt, span)
                else:
                    with lock:
                        gaps[ch].append((a, b))
                return
            if not probe:
                # A probe is deliberately tiny, so letting it set "the widest
                # span known to work" would make every later split fall back to
                # one hour and cost thousands of requests over a long period.
                oracles[ch].ok(span)
                hint.ok(span)
            raw_warn = False
            with lock:
                boundaries[ch].append(a)
                parts[ch].append(samples)
                if state["decimated"] is None and cnt:
                    state["decimated"] = _looks_decimated(samples, cnt)
                    raw_warn = state["decimated"] is False
            if raw_warn:
                _note(f"the archiver returned {len(samples)} raw samples for "
                      f"count={cnt} — it does not decimate, so a long period "
                      f"will be slow and heavy")
            if chunk_fn:
                try:
                    chunk_fn(ch, samples)
                except Exception:
                    pass
        finally:
            with lock:
                state["pending"] -= 1
                state["done"]   += 1
                pend = state["pending"]
            _emit_progress()
            if pend == 0:
                finished.set()

    # ── the plan: newest first, channels interleaved ───────────────────────
    if start_chunk_ns is None:
        want = min(span_total, MAX_CHUNK_NS if count else RAW_START_CHUNK_NS)
    else:
        want = int(start_chunk_ns)
    want = max(min_chunk_ns, int(want))

    # Each channel opens with ONE small request at the newest end. It is what
    # puts something on screen straight away: the first big request over a long
    # period is often refused, and the caller would otherwise watch an empty
    # graph through the whole search for a span the archiver will serve.
    probe_span = min(span_total, max(min_chunk_ns, CHUNK_SIZE_NS))
    use_probe = span_total > 2 * probe_span

    per_ch = {}
    for ch in channels:
        lst, b = [], end_ns
        if use_probe:
            a = b - probe_span
            lst.append((ch, a, b, True))
            b = a
        while b > start_ns:
            a = max(start_ns, b - want)
            lst.append((ch, a, b, False))
            b = a
        per_ch[ch] = lst
    plan = [t for grp in itertools.zip_longest(*per_ch.values())
            for t in grp if t is not None]

    # Count the whole plan up front, so the reported total only ever grows when
    # a request genuinely splits into more work.
    with lock:
        state["total"] = len(plan)
    if progress_fn:
        emitted[1] = len(plan)
        progress_fn(0, len(plan))

    # ── pre-flight: is the archiver there at all? ──────────────────────────
    # A big first request is allowed up to 120 s, so without this a dead
    # archiver would look like a two-minute hang. One minute of the newest end
    # at the short timeout answers that in 10 s. Skipped for short windows,
    # which are cheap enough to fail on their own.
    if preflight and span_total > 4 * CHUNK_SIZE_NS:
        probe_a = max(start_ns, end_ns - MIN_CHUNK_NS)
        dead = None
        for _attempt in (1, 2):
            try:
                cpva_fetch_samples(channels[0], probe_a, end_ns, base_timeout, None)
                dead = None
                break
            except Exception as e:
                # Any HTTP reply at all — even a refusal or a 404 for one bad
                # channel name — means the archiver is alive; only a failure
                # that never got a reply counts as unreachable.
                if getattr(e, "response", None) is not None:
                    dead = None
                    break
                dead = e
        if dead is not None:
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

    workers = max(1, min(max_workers, len(plan)))
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        for ch, a, b, is_probe in plan:
            if _is_cancelled(cancel_fn):
                # Do not even queue the rest: the caller has moved on.
                with lock:
                    state["cancelled"] = True
                    state["done"] += 1
                    gaps[ch].append((a, b))
                continue
            _submit(ch, a, b, _share(count, b - a, span_total),
                    planned=True, probe=is_probe)
        # Retire the planner only after everything is queued: without this seed
        # the first task can finish before the second is submitted, "pending"
        # touches zero and the fetch returns with one request's worth of data.
        with lock:
            state["pending"] -= 1
            pend = state["pending"]
        if pend == 0:
            finished.set()
        while not finished.wait(0.25):
            if _is_cancelled(cancel_fn):
                with lock:
                    state["cancelled"] = True
                break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    # ── merge: by sample time, not by request order ────────────────────────
    # Every request also returns the sample just before its own start, so
    # neighbours overlap. With adaptive splitting a child's anchor can predate
    # its parent's start, so request order is not time order — and
    # cpva_fetch_last_before relies on the last element being the newest.
    out: dict[str, list] = {}
    for ch in channels:
        merged = sorted(itertools.chain.from_iterable(parts[ch]),
                        key=lambda s: (s.get("time") or 0))
        seen, clean = set(), []
        for s in merged:
            t = s.get("time")
            if t in seen:
                continue
            seen.add(t)
            clean.append(s)
        out[ch] = clean

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
                         state["decimated"], span_total, time.monotonic() - t0)
    if state["over_budget"]:
        _note(f"stopped after {max_requests} requests — the period is too long "
              f"to read in full at this level of detail")
    return out, errors, report


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


def _last_before_in_range(channel: str, lo: int, hi: int,
                          timeout: float = CPVA_HTTP_TIMEOUT,
                          cancel_fn=None):
    """Newest sample in [lo, hi), hunted NEWEST HALF FIRST, or None.

    One request when the archiver will serve the whole range, and O(log) when
    it will not — instead of one request per hour. That matters because this
    runs for every signal whose data starts after the window: at a fixed hour
    grid the deepest 30-day ring alone was ~720 requests per signal.
    """
    if hi <= lo or _is_cancelled(cancel_fn):
        return None
    try:
        raw = cpva_fetch_samples(channel, lo, hi,
                                 _timeout_for_span(hi - lo, timeout),
                                 _LAST_BEFORE_COUNT)
    except Exception as exc:
        if not (_is_splittable_error(exc) and (hi - lo) > MIN_CHUNK_NS):
            return None
        mid = lo + (hi - lo) // 2
        return (_last_before_in_range(channel, mid, hi, timeout, cancel_fn)
                or _last_before_in_range(channel, lo, mid, timeout, cancel_fn))
    if not raw:
        return None
    return max(raw, key=lambda s: (s.get("time") or 0))


def cpva_fetch_last_before(channel: str, before_ns: int,
                           timeout: float = CPVA_HTTP_TIMEOUT,
                           cancel_fn=None):
    """Return the most recent sample dict strictly before `before_ns`, or None.

    Scans expanding rings back to ~30 days and stops at the first ring that
    holds data, so a PV whose last update predates the requested window can
    still be carried forward instead of leaving a gap in the plot.
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
        found = _last_before_in_range(channel, lo, hi, timeout, cancel_fn)
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
