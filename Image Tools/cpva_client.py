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

# A whole-day query for a busy channel returns 30k–100k samples and measured
# 0.3–7.4 s server-side, so the per-call timeout (tuned for small queries) is far
# too tight for it — it used to turn healthy days into "error". Full-day fetches
# therefore get their own floor; incremental tail fetches keep the caller's value.
FULL_DAY_TIMEOUT = 25.0

# After a failed (channel, day) fetch, don't hammer the archiver: an "error" is
# never cached as data, so without this every lookup re-ran a whole-day query and
# paid the full timeout again.
ERROR_BACKOFF_S = 10.0

# Today's tail fetch re-reads this much already-cached time so a sample archived
# late (with a timestamp before the newest one we hold) is still picked up.
TAIL_OVERLAP_NS = 5 * 1_000_000_000

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
    # Timestamps of `samples`, precomputed once per fetch. Bisecting a day used to
    # rebuild this list on EVERY lookup (8 ms per call on a 140k-sample span).
    # Optional/last so older positional constructions keep working.
    ts_list: "list[int]" = ()


class _Entry(NamedTuple):
    samples: "list[tuple[int, float]]"
    ts_list: "list[int]"
    fetched_mono: float
    # True when the fetch happened while the day was still running, so the day may
    # have grown since. Without this a day first read at 15:00 stayed cached as an
    # immutable "past day" once midnight passed, and every shot archived after that
    # first read read back as "no data" for the rest of the session.
    partial: bool = False


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

# (channel, date_key) → monotonic time until which a failed fetch is not retried.
_error_until: "dict[tuple[str, str], float]" = {}


def _entry_result(ent: "_Entry", status: str, age: float) -> DayResult:
    return DayResult(ent.samples, status, age, ent.ts_list)


def _merge_tail(old: "list[tuple[int, float]]", old_ts: "list[int]",
                tail: "list[tuple[int, float]]") -> "list[tuple[int, float]]":
    """Append newly archived samples from an overlapping tail query, skipping the
    ones already held. Sorted output; only re-sorts when a late write lands before
    the newest cached sample."""
    if not tail:
        return old
    if not old:
        return sorted(tail, key=lambda x: x[0])
    start = bisect_left(old_ts, tail[0][0])
    existing = set(old_ts[start:])
    add = [s for s in tail if s[0] not in existing]
    if not add:
        return old
    if add[0][0] < old_ts[-1]:
        return sorted(old + add, key=lambda x: x[0])
    return old + add


def get_day(channel: str, date_key: str, *,
            today_ttl: float = 3.0,
            timeout: float = DEFAULT_TIMEOUT) -> DayResult:
    """Samples for one Prague day, cached.

    Status semantics:
      "ok"    — fresh (or immutable past-day) data, possibly re-served from cache.
      "empty" — the archiver answered and there are genuinely no samples.
      "stale" — this fetch FAILED (or is being backed off) but an older successful
                result exists; its samples are returned so the UI can keep showing
                data, flagged.
      "error" — fetch failed and nothing is cached. Not cached as data, but the
                same (channel, day) is not retried for ERROR_BACKOFF_S.
    Past days are immutable → cached without TTL. Today honours today_ttl and is
    refreshed with an INCREMENTAL tail query (only the time after the newest
    cached sample), not by re-downloading the whole day.
    """
    key = (channel, date_key)
    is_today = (date_key == today_key())

    while True:
        with _day_cache_lock:
            ent = _day_cache.get(key)
            if ent is not None:
                _day_cache.move_to_end(key)
                age = time.monotonic() - ent.fetched_mono
                # Today: honour the TTL. A finished day: serve forever, unless it
                # was read while it was still running (then top it up once).
                if (age < today_ttl) if is_today else (not ent.partial):
                    return _entry_result(ent, "ok" if ent.samples else "empty", age)
            blocked_until = _error_until.get(key, 0.0)
            if blocked_until > time.monotonic():
                # A recent fetch of this day failed — don't pay the timeout again.
                if ent is not None:
                    return _entry_result(ent, "stale",
                                         time.monotonic() - ent.fetched_mono)
                return DayResult([], "error", float("inf"), ())
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

    day_start, day_end = day_bounds_ns(date_key)
    try:
        if ent is not None and ent.samples:
            # Incremental refresh of today: ask only for what we cannot have yet.
            # try_value_suffix stays off — an empty tail is the NORMAL answer here
            # and must not cost a second request every time.
            tail = fetch_values(channel,
                                max(day_start, ent.ts_list[-1] - TAIL_OVERLAP_NS),
                                day_end, timeout=timeout, try_value_suffix=False)
            samples = _merge_tail(ent.samples, ent.ts_list, tail)
        else:
            samples = fetch_values(channel, day_start, day_end,
                                   timeout=max(timeout, FULL_DAY_TIMEOUT))
    except CpvaError:
        with _day_cache_lock:
            _error_until[key] = time.monotonic() + ERROR_BACKOFF_S
            ent = _day_cache.get(key)
            if ent is not None:
                res = _entry_result(ent, "stale", time.monotonic() - ent.fetched_mono)
            else:
                res = DayResult([], "error", float("inf"), ())
            _finish_inflight(key, res)
        return res
    except BaseException:
        with _day_cache_lock:
            _finish_inflight(key, DayResult([], "error", float("inf"), ()))
        raise

    ts_list = [s[0] for s in samples]
    partial = int(time.time() * 1e9) <= day_end     # the day had not ended yet
    with _day_cache_lock:
        _error_until.pop(key, None)
        _day_cache[key] = ent = _Entry(samples, ts_list, time.monotonic(), partial)
        _day_cache.move_to_end(key)
        while len(_day_cache) > _DAY_CACHE_MAX:
            _day_cache.popitem(last=False)
        res = _entry_result(ent, "ok" if samples else "empty", 0.0)
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


def peek_day(channel: str, date_key: str) -> "DayResult | None":
    """Cached samples for one day WITHOUT touching the network. None = cache miss.
    Today's TTL is ignored (the point is to never block a UI thread)."""
    key = (channel, date_key)
    with _day_cache_lock:
        ent = _day_cache.get(key)
        if ent is None:
            return None
        _day_cache.move_to_end(key)
        return _entry_result(ent, "ok" if ent.samples else "empty",
                             time.monotonic() - ent.fetched_mono)


def invalidate(channel: "str | None" = None, date_key: "str | None" = None) -> None:
    """Drop cached days matching the given channel and/or date_key (None = any).
    Also clears the error backoff, so an explicit refresh always retries now."""
    with _day_cache_lock:
        for key in [k for k in _day_cache
                    if (channel is None or k[0] == channel)
                    and (date_key is None or k[1] == date_key)]:
            del _day_cache[key]
        for key in [k for k in _error_until
                    if (channel is None or k[0] == channel)
                    and (date_key is None or k[1] == date_key)]:
            del _error_until[key]


# ── generic matcher ───────────────────────────────────────────────────────────

def nearest_sample(samples: "list[tuple[int, float]]", ts_ns: int,
                   *, window_ns: int, prefer: str = "before") -> "float | None":
    """Value of the sample nearest ts_ns within ±window_ns, or None.

    prefer="nearest": the sample closest in time wins, whichever side it is on.
    prefer="before": last sample ≤ ts_ns wins; a later sample within the window
    is the fallback.
    prefer="after": mirrored.
    samples must be sorted by t_ns.
    """
    s = nearest_sample_ex(samples, ts_ns, window_ns=window_ns, prefer=prefer)
    return None if s is None else s[1]


def _match_score(dt_ns: int, prefer: str) -> "tuple[int, int]":
    """Ranking key for a candidate sample offset (sample_ts - ts_ns); lower wins.
    Used both inside one day and to pick between days."""
    if prefer == "before":
        return (0 if dt_ns <= 0 else 1, abs(dt_ns))
    if prefer == "after":
        return (0 if dt_ns >= 0 else 1, abs(dt_ns))
    return (0, abs(dt_ns))          # "nearest"


def nearest_sample_ex(samples: "list[tuple[int, float]]", ts_ns: int,
                      *, window_ns: int, prefer: str = "before",
                      ts_list: "list[int] | None" = None
                      ) -> "tuple[int, float] | None":
    """Like nearest_sample but returns the full (t_ns, value) sample.

    ts_list is the precomputed timestamp column of `samples` (see DayResult.ts_list).
    Pass it whenever it is available — building it here costs O(n) per call and
    dwarfs the bisect itself on a day-sized list."""
    if not samples:
        return None
    if not ts_list or len(ts_list) != len(samples):
        ts_list = [s[0] for s in samples]
    idx_b = bisect_right(ts_list, ts_ns) - 1
    idx_f = bisect_left(ts_list, ts_ns)
    best = None
    best_score = None
    for idx in (idx_b, idx_f):
        if not (0 <= idx < len(samples)):
            continue
        dt = samples[idx][0] - ts_ns
        if abs(dt) > window_ns:
            continue
        score = _match_score(dt, prefer)
        if best_score is None or score < best_score:
            best, best_score = samples[idx], score
    return best


# ── high-level value lookup (shared tri-state contract) ───────────────────────
# Display convention for every PV value in every tab:
#   status "ok" with value 0.0  → a REAL archiver zero — format it as a number.
#   status "not_found"          → lookup succeeded, no sample matches → "n/a".
#   status "error"              → fetch failed (retryable, never cached) → "ERR".
#   status "stale"              → value from an older successful fetch → value + " (old)".
#   matched sample further from the image than PV_EXACT_MATCH_NS → prefix "~":
#                                 the pairing to this exact shot is not certain.

PV_TEXT_ERROR = "ERR"
PV_TEXT_NOT_FOUND = "n/a"
PV_TEXT_STALE_SUFFIX = " (old)"
PV_TEXT_APPROX_PREFIX = "~"

# At 3.3 Hz (0.3 s between shots) a sample further than half that from the image
# can no longer be attributed to this shot with certainty — measured offsets are
# p50 0.025 s / p90 0.30 s, so the tail genuinely overlaps the neighbouring shot.
PV_EXACT_MATCH_NS = 150_000_000


class LookupResult(NamedTuple):
    value: "float | None"
    ts_ns: "int | None"      # timestamp of the sample actually used
    status: str              # "ok" | "not_found" | "stale" | "error"


def format_lookup(res: "LookupResult", num_fmt) -> str:
    """Render a LookupResult with the tri-state convention. num_fmt is a
    callable float → str (units/precision differ per tab)."""
    if res.status == "error":
        return PV_TEXT_ERROR
    if res.value is None:
        return PV_TEXT_NOT_FOUND
    txt = num_fmt(res.value)
    if res.status == "stale":
        txt += PV_TEXT_STALE_SUFFIX
    return txt


# Channels whose archiver record is a STEP function: a sample is written only
# when the value CHANGES (motor position setpoints/readbacks). At any instant
# the true value is therefore the last sample at or before that instant, however
# old it is — matching them against a ±window is wrong, because between two
# moves there is no nearby sample at all.
STEP_CHANNELS: frozenset = frozenset({"L3-PFWP6-MTR03-1:RawPos"})
# Backwards-compatible alias (older call sites read FORWARD_CHANNELS).
FORWARD_CHANNELS: frozenset = STEP_CHANNELS

# Progressively widening look-back windows (days). Stop at the first that has
# data, so a slow PV that last changed a month (or more) ago is still resolved
# in 1–4 queries.
LOOKBACK_WINDOWS_DAYS = (2, 8, 32, 120, 400)

# Prague days consulted through the shared day cache (day-of-ts included) before
# falling back to one wide, day-aligned look-back query.
_STEP_DAY_WALK = 3

# Cache of "last sample strictly BEFORE the start of this Prague day" per
# (channel, date_key). The key fully determines the query, so a whole save range
# reuses one wide look-back instead of N — WITHOUT one image's moment leaking
# onto every other image of the same day. (The old cache was keyed by the day of
# an arbitrary ts and stored the value at THAT ts, so the first lookup of a day
# pinned its value for every later frame — a waveplate held at 350k would report
# an unrelated earlier position.)
_before_cache: "OrderedDict[tuple[str, str], tuple[float, int] | None]" = OrderedDict()
_before_lock = threading.Lock()
_BEFORE_CACHE_MAX = 256
# Same purpose as _error_until, for the look-back queries (a failure here costs up
# to len(LOOKBACK_WINDOWS_DAYS) timeouts, so retrying it per frame is expensive).
_before_error_until: "dict[tuple[str, str], float]" = {}


def _last_at_or_before(samples: "list[tuple[int, float]]", ts_ns: int
                       ) -> "tuple[int, float] | None":
    """Last (t_ns, value) with t_ns <= ts_ns, or None. samples must be sorted."""
    idx = bisect_right(samples, (ts_ns, float("inf"))) - 1
    return samples[idx] if idx >= 0 else None


def invalidate_lookback(channel: "str | None" = None,
                        date_key: "str | None" = None) -> None:
    """Drop cached day-boundary look-back anchors (None = any)."""
    with _before_lock:
        for key in [k for k in _before_cache
                    if (channel is None or k[0] == channel)
                    and (date_key is None or k[1] == date_key)]:
            del _before_cache[key]
        for key in [k for k in _before_error_until
                    if (channel is None or k[0] == channel)
                    and (date_key is None or k[1] == date_key)]:
            del _before_error_until[key]


def _value_before_day(channel: str, date_key: str, *,
                      lookback_days: "tuple[int, ...]",
                      timeout: float,
                      network_ok: bool) -> LookupResult:
    """Last sample strictly before the start of the Prague day date_key.
    Cached per (channel, date_key); fetch failures are NEVER cached."""
    ck = (channel, date_key)
    with _before_lock:
        if ck in _before_cache:
            hit = _before_cache[ck]
            _before_cache.move_to_end(ck)
            if hit is None:
                return LookupResult(None, None, "not_found")
            return LookupResult(hit[0], hit[1], "ok")
        if _before_error_until.get(ck, 0.0) > time.monotonic():
            return LookupResult(None, None, "error")
    if not network_ok:
        return LookupResult(None, None, "not_found")
    end_ns = day_bounds_ns(date_key)[0] - 1
    found: "tuple[float, int] | None" = None
    try:
        for d in lookback_days:
            samples = fetch_values(channel, end_ns - d * DAY_NS, end_ns,
                                   timeout=timeout, try_value_suffix=False)
            if samples:
                t_ns, val = samples[-1]   # query end is end_ns → all samples ≤ end_ns
                found = (val, t_ns)
                break
    except CpvaError:
        with _before_lock:
            _before_error_until[ck] = time.monotonic() + ERROR_BACKOFF_S
        return LookupResult(None, None, "error")
    with _before_lock:
        _before_error_until.pop(ck, None)
        _before_cache[ck] = found
        _before_cache.move_to_end(ck)
        while len(_before_cache) > _BEFORE_CACHE_MAX:
            _before_cache.popitem(last=False)
    if found is None:
        return LookupResult(None, None, "not_found")
    return LookupResult(found[0], found[1], "ok")


def value_at_or_before(channel: str, ts_ns: int, *,
                       lookback_days: "tuple[int, ...]" = LOOKBACK_WINDOWS_DAYS,
                       timeout: float = DEFAULT_TIMEOUT,
                       today_ttl: float = 3.0,
                       network_ok: bool = True) -> LookupResult:
    """Last sample at or before ts_ns — the true value of a step PV at ts_ns.

    The day of ts_ns and the two days before it are read from the shared day
    cache and bisected EXACTLY at ts_ns, so every frame of a day gets its own
    value. Only when those days hold nothing does one wide, day-aligned
    look-back query run (cached per day boundary, see _value_before_day) — that
    covers PVs whose last change was weeks earlier.

    A failed fetch returns status "error" instead of a value from further back:
    an unverifiable day must not silently show an older position.
    network_ok=False serves cache hits only (for UI-thread callers); days that
    are not cached are reported as "stale" rather than dropped, so a value is
    still shown — flagged as possibly out of date."""
    ts_ns = int(ts_ns)
    dk = date_key_for_ns(ts_ns)
    status = "ok"
    for _i in range(_STEP_DAY_WALK):
        if network_ok:
            res = get_day(channel, dk, today_ttl=today_ttl, timeout=timeout)
        else:
            res = peek_day(channel, dk)
            if res is None:
                # Cache-only mode and this day was never loaded — anything found
                # further back may already be superseded.
                status = "stale"
                res = DayResult([], "empty", float("inf"))
        if res.status == "error":
            return LookupResult(None, None, "error")
        if res.status == "stale":
            status = "stale"
        hit = _last_at_or_before(res.samples, ts_ns)
        if hit is not None:
            return LookupResult(hit[1], hit[0], status)
        if _i < _STEP_DAY_WALK - 1:
            dk = prev_date_key(dk)
    # dk is now the EARLIEST day consulted — anchor the wide query at its start
    # so no day is skipped between the walk and the look-back.
    back = _value_before_day(channel, dk, lookback_days=lookback_days,
                             timeout=timeout, network_ok=network_ok)
    if back.value is not None and status == "stale":
        return LookupResult(back.value, back.ts_ns, "stale")
    return back


def lookup_near(channel: str, ts_ns: int, *,
                window_ns: int = 30 * 1_000_000_000,
                prefer: "str | None" = None,
                fallback_before: bool = False,
                today_ttl: float = 3.0,
                timeout: float = DEFAULT_TIMEOUT) -> LookupResult:
    """Sample nearest ts_ns within ±window_ns.

    STEP_CHANNELS bypass the window entirely: their value is only archived on
    change, so the correct reading at ts_ns is the last sample at or before it
    (value_at_or_before), and a "nearest within 30 s" match would either miss
    (long hold between moves) or jump early to the NEXT position.

    prefer defaults to "nearest" — the sample closest in time to the image. For a
    per-shot detector "before" is WRONG as a tie-break: the archiver writes a
    shot's sample up to ~0.3 s AFTER the image timestamp, so "last sample ≤ ts"
    systematically returns the PREVIOUS shot (measured: 43 % of frames at 3.3 Hz).
    Each day is bisected through its own cached index; the neighbouring days are
    consulted only when ts_ns is within window_ns of midnight, so a sub-second
    window costs one day fetch instead of three.

    fallback_before=True chains to value_at_or_before() when nothing is inside
    the window. Fast channels should leave it False so a value from a different
    session hours away is never shown.
    """
    if channel in STEP_CHANNELS:
        return value_at_or_before(channel, ts_ns, today_ttl=today_ttl,
                                  timeout=timeout)
    if prefer is None:
        prefer = "nearest"
    date_key = date_key_for_ns(ts_ns)
    day_start, day_end = day_bounds_ns(date_key)
    date_keys = [date_key]
    if ts_ns - day_start < window_ns:
        date_keys.insert(0, prev_date_key(date_key))
    if day_end - ts_ns < window_ns:
        date_keys.append(next_date_key(date_key))
    hit = None
    hit_score = None
    status = "ok"
    for dk in date_keys:
        res = get_day(channel, dk, today_ttl=today_ttl, timeout=timeout)
        if res.status == "error":
            status = "error"
        elif res.status == "stale" and status != "error":
            status = "stale"
        cand = nearest_sample_ex(res.samples, ts_ns, window_ns=window_ns,
                                 prefer=prefer, ts_list=res.ts_list)
        if cand is None:
            continue
        score = _match_score(cand[0] - ts_ns, prefer)
        if hit_score is None or score < hit_score:
            hit, hit_score = cand, score
    if hit is not None:
        return LookupResult(hit[1], hit[0], status if status != "ok" else "ok")
    if fallback_before:
        back = value_at_or_before(channel, ts_ns, today_ttl=today_ttl,
                                  timeout=timeout)
        if back.status == "error" and status == "error":
            return back
        if back.value is not None:
            return back
    if status == "error":
        return LookupResult(None, None, "error")
    return LookupResult(None, None, "not_found")
