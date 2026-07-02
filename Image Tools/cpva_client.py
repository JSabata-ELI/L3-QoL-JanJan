"""
Shared CPVA archiver client for Image Tools (Image Slider / Image Finder / Shot Finder).

Replaces the three divergent per-file copies of the HTTP client:
  - connection POOL of persistent keep-alive HTTPS connections → genuinely
    parallel requests without a TLS handshake per call,
  - one retry policy for transport errors,
  - one shared per-(channel, day) sample cache with single-flight dedup and an
    explicit status model that distinguishes "no data" from "fetch failed".

Stdlib only. NO Qt imports — this module runs on arbitrary worker threads and is
shared between the re-exec'd module copies of the tools.
"""

from __future__ import annotations

import json
import queue
import ssl
import socket
import threading
import time
import urllib.parse
import http.client
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Iterable, NamedTuple

try:
    from zoneinfo import ZoneInfo
    TZ_PRAGUE = ZoneInfo("Europe/Prague")
except Exception:                       # pragma: no cover
    TZ_PRAGUE = timezone.utc

# ── constants ─────────────────────────────────────────────────────────────────
CPVA_BASE_URL   = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HOST       = CPVA_BASE_URL.split("://", 1)[1].split("/")[0]   # "10.78.0.57:8443"
CPVA_BASE_PATH  = "/" + CPVA_BASE_URL.split("://", 1)[1].split("/", 1)[1]
DEFAULT_TIMEOUT = 10.0

# Canonical column/PV-name → archiver channel. Keys are the names used by the
# Image Finder / Shot Finder energy columns; the Slider uses capitalized display
# names locally and maps them onto the same channels.
CHANNEL_MAP: dict[str, str] = {
    "ptm1":      "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "pcm2":      "HAPLS-ENER_IN_PCM2_LT6_DIAG2:Energy",
    "pcm4":      "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "pap1":      "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "sbw4":      "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "Back_Ref":  "L3-PM03-023:Energy",
    "waveplate": "L3-PFWP6-MTR03-1:RawPos",
}

SHOT_CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"
SBW4_CHANNEL = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"

DAY_NS = 86_400 * 1_000_000_000


class CpvaError(Exception):
    """Transport/HTTP/JSON failure talking to the archiver.

    NEVER means "no data" — an empty sample list is a valid, successful result.
    """


# ── connection pool ───────────────────────────────────────────────────────────
# LIFO pool of persistent HTTPSConnections: the most recently used (hottest
# keep-alive) socket is handed out first. Pool size is also the deliberate cap
# on concurrent requests against the archiver — callers beyond it queue here
# instead of stampeding the server.
_POOL_SIZE = 6

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_pool: "queue.LifoQueue[http.client.HTTPSConnection | None]" = queue.LifoQueue(maxsize=_POOL_SIZE)
for _ in range(_POOL_SIZE):
    _pool.put(None)          # slots start empty; connections are created lazily
del _


def _request_json(path_qs: str, timeout: float):
    """GET CPVA_BASE_PATH+path_qs on a pooled connection, return parsed JSON.

    Retries once on transport errors (idle keep-alive socket closed by the
    server is the normal case) and once on HTTP 5xx. 4xx and socket timeouts
    raise immediately.
    """
    full_path = CPVA_BASE_PATH + path_qs
    try:
        # Bounded wait: under a burst from all three tools a saturated pool
        # must degrade (CpvaError → "error"/"stale" day status), not stall the
        # caller indefinitely.
        conn = _pool.get(timeout=timeout + 5.0)
    except queue.Empty:
        raise CpvaError(f"connection pool exhausted waiting for {path_qs}")
    try:
        last_exc: Exception | None = None
        for attempt in range(2):
            if conn is None:
                conn = http.client.HTTPSConnection(CPVA_HOST, timeout=timeout, context=_ssl_ctx)
            else:
                conn.timeout = timeout
            try:
                conn.request("GET", full_path, headers={"Accept": "application/json"})
                resp = conn.getresponse()
                body = resp.read()
                if resp.status >= 500:
                    last_exc = CpvaError(f"HTTP {resp.status} for {path_qs}")
                    if attempt == 0:
                        time.sleep(0.3)
                        continue
                    raise last_exc
                if resp.status >= 400:
                    raise CpvaError(f"HTTP {resp.status} for {path_qs}")
                try:
                    return json.loads(body.decode("utf-8"))
                except ValueError as exc:
                    raise CpvaError(f"bad JSON from archiver: {exc}") from exc
            except CpvaError:
                raise
            except socket.timeout as exc:
                # A retry would double an already-long stall; the day-cache
                # layer degrades gracefully instead.
                _close_quiet(conn)
                conn = None
                raise CpvaError(f"timeout after {timeout}s for {path_qs}") from exc
            except Exception as exc:
                # RemoteDisconnected / BadStatusLine / SSLError / OSError …
                _close_quiet(conn)
                conn = None
                last_exc = exc
                if attempt == 1:
                    raise CpvaError(f"transport error for {path_qs}: {exc}") from exc
        raise CpvaError(f"unreachable retry state: {last_exc}")
    finally:
        _pool.put(conn)


def _close_quiet(conn) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


# ── low-level fetches ─────────────────────────────────────────────────────────

def fetch_samples(channel: str, start_ns: int, end_ns: int,
                  *, timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    """Raw archiver samples ({"time": ns, "value": …} dicts). Raises CpvaError."""
    qs = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(int(start_ns)),
        "end":   str(int(end_ns)),
    })
    data = _request_json(f"/samples?{qs}", timeout)
    return data if isinstance(data, list) else []


def parse_samples(raw: list[dict]) -> list[tuple[int, float]]:
    """Parse raw archiver samples into a sorted (t_ns, float) list."""
    out: list[tuple[int, float]] = []
    for s in raw:
        t = s.get("time")
        val = s.get("value")
        if isinstance(val, list):
            val = val[0] if val else None
        try:
            out.append((int(t), float(val)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_values(channel: str, start_ns: int, end_ns: int,
                 *, timeout: float = DEFAULT_TIMEOUT,
                 try_value_suffix: bool = True) -> list[tuple[int, float]]:
    """Parsed sorted (t_ns, value) samples. If the bare channel name yields no
    parseable samples, retries "<channel>.value" (some channels are only
    archived under that alias). Raises CpvaError on fetch failure."""
    result = parse_samples(fetch_samples(channel, start_ns, end_ns, timeout=timeout))
    if not result and try_value_suffix and not channel.endswith(".value"):
        result = parse_samples(fetch_samples(channel + ".value", start_ns, end_ns, timeout=timeout))
    return result


def fetch_channels(pattern: str = "**", *, timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """All archiver channel names matching pattern. Raises CpvaError."""
    qs = urllib.parse.urlencode({"pattern": pattern})
    data = _request_json(f"/channels-by-pattern?{qs}", timeout)
    out: list[str] = []
    if isinstance(data, list):
        for d in data:
            if isinstance(d, str):
                out.append(d)
            elif isinstance(d, dict):
                name = d.get("channelName") or d.get("name") or d.get("channel")
                if name:
                    out.append(str(name))
    return out


def best_shot_ns(start_ns: int, end_ns: int, *, channel: str = SHOT_CHANNEL,
                 timeout: float = 4.0) -> "int | None":
    """Timestamp (ns) of the highest-energy sample in the window, or None
    (on failure or when no sample has value > 0)."""
    try:
        samples = fetch_values(channel, start_ns, end_ns, timeout=timeout,
                               try_value_suffix=False)
    except CpvaError:
        return None
    best_t: int | None = None
    best_v = 0.0
    for t_ns, v in samples:
        if v > best_v:
            best_v = v
            best_t = t_ns
    return best_t


# ── time helpers (Prague-day keyed) ───────────────────────────────────────────

def date_key_for_ns(ts_ns: int) -> str:
    """'YYYY-MM-DD' in Prague time for a UTC-ns timestamp."""
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)
    return dt.strftime("%Y-%m-%d")


def _parse_date_key(date_key: str) -> datetime:
    y, m, d = int(date_key[:4]), int(date_key[5:7]), int(date_key[8:10])
    return datetime(y, m, d, tzinfo=TZ_PRAGUE)


def prev_date_key(date_key: str) -> str:
    return (_parse_date_key(date_key) - timedelta(days=1)).strftime("%Y-%m-%d")


def next_date_key(date_key: str) -> str:
    return (_parse_date_key(date_key) + timedelta(days=1)).strftime("%Y-%m-%d")


def day_bounds_ns(date_key: str) -> tuple[int, int]:
    """(start_ns, end_ns) of the Prague-local day."""
    day_start = _parse_date_key(date_key)
    day_end = day_start + timedelta(hours=23, minutes=59, seconds=59, microseconds=999999)
    return int(day_start.timestamp() * 1e9), int(day_end.timestamp() * 1e9)


def today_key() -> str:
    return datetime.now(TZ_PRAGUE).strftime("%Y-%m-%d")


# ── shared day cache ──────────────────────────────────────────────────────────

class DayResult(NamedTuple):
    samples: "list[tuple[int, float]]"   # sorted (t_ns, value)
    status:  str                         # "ok" | "empty" | "stale" | "error"
    age_s:   float                       # since last successful fetch (inf for "error")


class _Entry(NamedTuple):
    samples: "list[tuple[int, float]]"
    fetched_mono: float


_DAY_CACHE_MAX = 64
_day_cache: "OrderedDict[tuple[str, str], _Entry]" = OrderedDict()
_day_cache_lock = threading.Lock()


class _InFlight:
    """Single-flight record: waiters take the fetcher's result directly, so a
    FAILED fetch is not retried once per waiter (serial stampede on outage)."""
    __slots__ = ("event", "result")

    def __init__(self):
        self.event = threading.Event()
        self.result: "DayResult | None" = None


# Single-flight: (channel, date_key) → in-progress fetch record.
_inflight: "dict[tuple[str, str], _InFlight]" = {}


def get_day(channel: str, date_key: str, *,
            today_ttl: float = 3.0,
            timeout: float = DEFAULT_TIMEOUT) -> DayResult:
    """Samples for one Prague day, cached.

    Status semantics:
      "ok"    — fresh (or immutable past-day) data, possibly re-served from cache.
      "empty" — the archiver answered and there are genuinely no samples.
      "stale" — this fetch FAILED but an older successful result exists; its
                samples are returned so the UI can keep showing data, flagged.
      "error" — fetch failed and nothing is cached. NEVER cached itself: the
                next call retries.
    Past days are immutable → cached without TTL. Today honours today_ttl.
    """
    key = (channel, date_key)
    is_today = (date_key == today_key())

    while True:
        with _day_cache_lock:
            ent = _day_cache.get(key)
            if ent is not None:
                _day_cache.move_to_end(key)
                age = time.monotonic() - ent.fetched_mono
                if not is_today or age < today_ttl:
                    return DayResult(ent.samples, "ok" if ent.samples else "empty", age)
            fl = _inflight.get(key)
            if fl is None:
                fl = _InFlight()
                _inflight[key] = fl
                break              # this thread fetches
        # Another thread is fetching this key — take ITS result (even "error")
        # instead of refetching; retry happens on the NEXT get_day call.
        fl.event.wait(2 * timeout + 10.0)
        if fl.result is not None:
            return fl.result
        # Fetcher still running (wait timed out) or died without a result —
        # loop back to re-check the cache / in-flight state.

    try:
        samples = fetch_values(channel, *day_bounds_ns(date_key), timeout=timeout)
    except CpvaError:
        with _day_cache_lock:
            ent = _day_cache.get(key)
            if ent is not None:
                res = DayResult(ent.samples, "stale", time.monotonic() - ent.fetched_mono)
            else:
                res = DayResult([], "error", float("inf"))
            _finish_inflight(key, res)
        return res
    except BaseException:
        with _day_cache_lock:
            _finish_inflight(key, DayResult([], "error", float("inf")))
        raise

    with _day_cache_lock:
        _day_cache[key] = _Entry(samples, time.monotonic())
        _day_cache.move_to_end(key)
        while len(_day_cache) > _DAY_CACHE_MAX:
            _day_cache.popitem(last=False)
        res = DayResult(samples, "ok" if samples else "empty", 0.0)
        _finish_inflight(key, res)
    return res


def _finish_inflight(key, result: "DayResult") -> None:
    fl = _inflight.pop(key, None)
    if fl is not None:
        fl.result = result
        fl.event.set()


def warm_days(channels: "Iterable[str]", date_keys: "Iterable[str]",
              *, today_ttl: float = 3.0, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Pre-load the day cache for channels × date_keys in parallel (bounded by
    the connection pool). Errors are swallowed — this is best-effort warm-up;
    per-item calls will surface/retry them."""
    jobs = [(ch, dk) for ch in dict.fromkeys(channels) for dk in dict.fromkeys(date_keys)]
    if not jobs:
        return
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=min(_POOL_SIZE, len(jobs))) as ex:
            list(ex.map(lambda j: get_day(j[0], j[1], today_ttl=today_ttl,
                                          timeout=timeout), jobs))
    except Exception:
        pass


def invalidate(channel: "str | None" = None, date_key: "str | None" = None) -> None:
    """Drop cached days matching the given channel and/or date_key (None = any)."""
    with _day_cache_lock:
        for key in [k for k in _day_cache
                    if (channel is None or k[0] == channel)
                    and (date_key is None or k[1] == date_key)]:
            del _day_cache[key]


# ── generic matcher ───────────────────────────────────────────────────────────

def nearest_sample(samples: "list[tuple[int, float]]", ts_ns: int,
                   *, window_ns: int, prefer: str = "before") -> "float | None":
    """Value of the sample nearest ts_ns within ±window_ns, or None.

    prefer="before": last sample ≤ ts_ns wins; a later sample within the window
    is the fallback (energy detectors fire just before the image).
    prefer="after": mirrored (waveplate motor settles after the trigger).
    samples must be sorted by t_ns.
    """
    if not samples:
        return None
    ts_list = [s[0] for s in samples]
    idx_b = bisect_right(ts_list, ts_ns) - 1
    idx_f = bisect_left(ts_list, ts_ns)
    before = samples[idx_b] if idx_b >= 0 and (ts_ns - samples[idx_b][0]) <= window_ns else None
    after = samples[idx_f] if idx_f < len(samples) and (samples[idx_f][0] - ts_ns) <= window_ns else None
    first, second = (before, after) if prefer == "before" else (after, before)
    if first is not None:
        return first[1]
    if second is not None:
        return second[1]
    return None
