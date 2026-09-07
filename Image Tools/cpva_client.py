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
import re
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
    # Corrected: this map used to say HAPLS-ENER_IN_PCM2_LT6_DIAG2:Energy while the
    # Image Slider and the CSS Logger both read L3-PM03-025:Energy, so the Slider and
    # the other two tabs reported different numbers under the same label. This is the
    # right channel. Operator-visible: PCM2 values change in Image Finder / Shot Finder.
    "pcm2":      "L3-PM03-025:Energy",
    "pcm4":      "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "pap1":      "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    # Renamed 2026-08-14: SBW4 is archived under L3-SBW4-PM311:Energy. The old
    # HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy still holds the historical samples, so
    # dates before the rename read "n/a" under this name.
    "sbw4":      "L3-SBW4-PM311:Energy",
    "Back_Ref":  "L3-PM03-023:Energy",
    "waveplate": "L3-PFWP6-MTR03-1:RawPos",
}

SHOT_CHANNEL = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"
SBW4_CHANNEL = CHANNEL_MAP["sbw4"]

DAY_NS = 86_400 * 1_000_000_000


class CpvaError(Exception):
    """Transport/HTTP/JSON failure talking to the archiver.

    NEVER means "no data" — an empty sample list is a valid, successful result.
    """


class CpvaBusyError(CpvaError):
    """The LOCAL connection pool was saturated — we never reached the archiver.

    Distinct from CpvaError because it says nothing about the channel or the day
    being asked for: get_day must not arm its ERROR_BACKOFF_S blackout on a healthy
    PV just because another tab was mid-way through a 25 s whole-day query.
    """


# ── connection pool ───────────────────────────────────────────────────────────
# LIFO pool of persistent HTTPSConnections: the most recently used (hottest
# keep-alive) socket is handed out first. Pool size is also the deliberate cap
# on concurrent requests against the archiver — callers beyond it queue here
# instead of stampeding the server.
# 8, not 6: the Slider's PV panel is the widest single fan-out in the app (8
# selectable names), so at 6 one tab could exhaust the pool on its own and then
# report its own queueing as an archiver error. The CSS Logger already drives 16
# concurrent requests at this same archiver, so 8 is well inside what it serves.
_POOL_SIZE = 8

# The genuine worst case for how long ONE caller can hold a slot: two attempts at
# the whole-day timeout floor plus the 0.3 s retry sleep (see _request_json).
_MAX_REQUEST_HOLD_S = 2 * FULL_DAY_TIMEOUT + 1.0
# How long a waiter may queue for a slot. It MUST be sized against the longest
# another caller can hold one — never against the waiter's own query timeout. That
# mismatch was the bug: the Slider passes timeout=8.0, so it waited 13 s while a
# legitimate holder sat on the slot for up to 25 s, raised CpvaError, and get_day
# turned a saturated pool into a 10 s "this channel/day is broken" blackout.
_POOL_WAIT_S = _MAX_REQUEST_HOLD_S + 5.0

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_pool: "queue.LifoQueue[http.client.HTTPSConnection | None]" = queue.LifoQueue(maxsize=_POOL_SIZE)
for _ in range(_POOL_SIZE):
    _pool.put(None)          # slots start empty; connections are created lazily
del _


# ── diagnostics ───────────────────────────────────────────────────────────────
# Process-wide counters. The user's report is "SOMETIMES the PV is not read", and
# nothing in this module used to leave a trace of a failed or queued request — the
# tri-state status reached the UI and the reason was gone. These are appended to
# image_tools_diag.log once a minute by the Slider, which turns "sometimes" into a
# number. Never raise, never lock for long: a bump is a dict update under a lock.
STATS: "dict[str, float]" = {
    "requests": 0,          # HTTP GETs attempted
    "http_errors": 0,       # 4xx/5xx that ended as CpvaError
    "timeouts": 0,          # socket timeouts (never retried, by design)
    "busy": 0,              # CpvaBusyError — our own pool was full
    "pool_wait_max_s": 0.0, # worst observed queueing time for a slot
    "samples_dropped": 0,   # unparseable samples skipped by parse_samples
    "warm_failures": 0,     # per-job failures inside warm_days
    "takeovers": 0,         # abandoned single-flight records (see get_day)
    "range_splits": 0,      # oversize ranges re-fetched in halves (fetch_samples_split)
    "alias_hits": 0,        # empty days answered by the channel's other name (SBW4)
}
_stats_lock = threading.Lock()


def _stat_bump(key: str, n: float = 1) -> None:
    with _stats_lock:
        STATS[key] = STATS.get(key, 0) + n


def _stat_max(key: str, v: float) -> None:
    with _stats_lock:
        if v > STATS.get(key, 0.0):
            STATS[key] = v


def stats_line() -> str:
    """One-line snapshot for a log. Cheap; safe to call from a timer."""
    with _stats_lock:
        s = dict(STATS)
    with _day_cache_lock:
        n_days = len(_day_cache)
    return (f"cpva req={int(s['requests'])} httpErr={int(s['http_errors'])} "
            f"to={int(s['timeouts'])} busy={int(s['busy'])} "
            f"poolWaitMax={s['pool_wait_max_s']:.1f}s "
            f"drop={int(s['samples_dropped'])} warmFail={int(s['warm_failures'])} "
            f"take={int(s['takeovers'])} split={int(s.get('range_splits', 0))} "
            f"days={n_days}")


def _request_json(path_qs: str, timeout: float):
    """GET CPVA_BASE_PATH+path_qs on a pooled connection, return parsed JSON.

    Retries once on transport errors (idle keep-alive socket closed by the
    server is the normal case) and once on HTTP 5xx. 4xx and socket timeouts
    raise immediately.
    """
    full_path = CPVA_BASE_PATH + path_qs
    _t_wait = time.monotonic()
    try:
        # Bounded wait: under a burst from all three tools a saturated pool
        # must degrade (CpvaBusyError → "error"/"stale" day status), not stall the
        # caller indefinitely. See _POOL_WAIT_S for why it is not `timeout + 5`.
        conn = _pool.get(timeout=_POOL_WAIT_S)
    except queue.Empty:
        _stat_bump("busy")
        _stat_max("pool_wait_max_s", time.monotonic() - _t_wait)
        raise CpvaBusyError(f"connection pool exhausted waiting for {path_qs}")
    _stat_max("pool_wait_max_s", time.monotonic() - _t_wait)
    _stat_bump("requests")
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
                    _stat_bump("http_errors")
                    raise last_exc
                if resp.status >= 400:
                    _stat_bump("http_errors")
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
                _stat_bump("timeouts")
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


# The archiver cannot serve an arbitrarily large window in one response: past roughly
# 110k samples /samples answers HTTP 500 (measured on L3-SBW4-PM311:Energy — a day with
# 108 078 samples is fine, 116 223 and 122 410 are not, and each half of a failing day
# comes back without complaint in ~5 s). It is a response-size limit, not a timeout and
# not missing data.
#
# The ceiling is empirical and belongs to the server, so nothing here may key off a
# sample COUNT — we cannot know one before the request. The only reliable signal is the
# 500 itself, so an oversize window is discovered by asking and recovered by asking for
# less. Whole days of a busy shift are exactly the queries that hit it, which is how a
# Shot Finder search over such a day used to come back completely empty.
_SPLIT_MIN_SPAN_NS = 3_600_000_000_000     # 1 h — below this a failure is a real failure
_SPLIT_MAX_DEPTH   = 3                     # ≤ 8 chunks ≈ 880k samples for one day


def _is_splittable_error(exc: Exception) -> bool:
    """True when re-asking for a SMALLER window could plausibly succeed.

    Only the server saying 5xx and the request running out of time qualify. A 4xx, a
    malformed answer or a bad channel name would fail identically on every sub-range,
    so splitting those just multiplies one error into eight."""
    msg = str(exc)
    return msg.startswith("HTTP 5") or msg.startswith("timeout after")


def fetch_samples_split(channel: str, start_ns: int, end_ns: int,
                        *, timeout: float = DEFAULT_TIMEOUT,
                        min_span_ns: int = _SPLIT_MIN_SPAN_NS,
                        max_depth: int = _SPLIT_MAX_DEPTH) -> list[dict]:
    """`fetch_samples`, but a window the archiver refuses as too large is halved and
    fetched in pieces instead of failing.

    The halves run SEQUENTIALLY: this fires on the rare oversize day, and issuing them
    in parallel would take extra slots from a pool of 8 that whole-day warm-ups are
    already sharing. Two 5 s halves in place of one failure is the right trade.

    Raises CpvaError exactly as `fetch_samples` does whenever splitting cannot help:
    a non-size error, a window already at the floor, or the depth bound reached."""
    try:
        return fetch_samples(channel, start_ns, end_ns, timeout=timeout)
    except CpvaBusyError:
        # Our own pool was full — we never reached the archiver, so nothing suggests the
        # window is too big. Splitting would ask that same full pool for twice as much.
        raise
    except CpvaError as exc:
        if (max_depth <= 0 or (end_ns - start_ns) <= min_span_ns
                or not _is_splittable_error(exc)):
            raise

    _stat_bump("range_splits")
    mid = (start_ns + end_ns) // 2
    out: list[dict] = []
    # mid+1 for the second half: the archiver's range is inclusive at both ends, so a
    # sample landing exactly on the split point would otherwise be returned twice.
    for lo, hi in ((start_ns, mid), (mid + 1, end_ns)):
        out.extend(fetch_samples_split(channel, lo, hi, timeout=timeout,
                                       min_span_ns=min_span_ns,
                                       max_depth=max_depth - 1))
    return out


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
            # Still skipped — a malformed sample has no usable value — but counted,
            # so a channel whose samples are systematically unparseable shows up in
            # the diag log instead of just reading "n/a" forever.
            _stat_bump("samples_dropped")
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_values_ex(channel: str, start_ns: int, end_ns: int,
                    *, timeout: float = DEFAULT_TIMEOUT,
                    try_value_suffix: bool = True
                    ) -> "tuple[list[tuple[int, float]], str]":
    """(parsed sorted samples, the channel name that actually produced them).

    Returning the name matters for incremental refreshes: an alias-only channel is
    found through "<channel>.value", and a later tail query against the BARE name
    returns nothing, so the cached day never grows and every sample archived after
    the first read looks like "no data" for the rest of the session.

    Goes through `fetch_samples_split`, so a window too large for one archiver response
    is read in pieces rather than lost. Every caller inherits that: get_day (whole days)
    and value_at_or_before (multi-day look-back windows) are the two that reach the
    ceiling in practice."""
    result = parse_samples(fetch_samples_split(channel, start_ns, end_ns, timeout=timeout))
    if result or not try_value_suffix or channel.endswith(".value"):
        return result, channel
    alias = channel + ".value"
    try:
        return parse_samples(fetch_samples_split(alias, start_ns, end_ns,
                                                 timeout=timeout)), alias
    except CpvaError as exc:
        # The suffix is a GUESS — most channels are not archived under it, and the
        # archiver answers a name it does not know with HTTP 400. Letting that
        # bubble up turned "the real name answered, and the day is empty" into "the
        # archiver did not answer", which is a statement about the machine.
        # Measured 04.09.2026 on L3-SBW4-PM311:Energy: the bare name returns an
        # empty day, the .value probe returns 400, and PV Search drew no curve at
        # all instead of falling back to SBW4's other archived name.
        if not str(exc).startswith("HTTP 4"):
            raise                      # 5xx / timeout: a real failure, still raised
        return result, channel


def fetch_values(channel: str, start_ns: int, end_ns: int,
                 *, timeout: float = DEFAULT_TIMEOUT,
                 try_value_suffix: bool = True) -> list[tuple[int, float]]:
    """Parsed sorted (t_ns, value) samples. If the bare channel name yields no
    parseable samples, retries "<channel>.value" (some channels are only
    archived under that alias). Raises CpvaError on fetch failure."""
    return fetch_values_ex(channel, start_ns, end_ns, timeout=timeout,
                           try_value_suffix=try_value_suffix)[0]


# channel → engineering unit, but ONLY when the archiver itself said so. The listing
# usually answers with plain strings, in which case this stays empty and a UI that wants
# a unit has to get it from somewhere it can defend (a preset table, or the operator).
# Never guessed here: a wrong unit printed next to a number is worse than none.
CHANNEL_UNITS: dict[str, str] = {}

# Keys an archiver listing might carry the engineering unit under. EPICS calls it EGU.
_UNIT_KEYS = ("unit", "units", "egu", "EGU", "displayUnit", "engineeringUnits")


def fetch_channels(pattern: str = "**", *, timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """All archiver channel names matching pattern. Raises CpvaError.

    Entries may come back as bare strings or as dicts; when a dict carries an
    engineering unit it is kept in CHANNEL_UNITS as a side effect, since this is the
    only listing the app ever asks for."""
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
                    for k in _UNIT_KEYS:
                        u = d.get(k)
                        if isinstance(u, str) and u.strip():
                            CHANNEL_UNITS[str(name)] = u.strip()
                            break
    return out


_channels_cache: dict[str, list[str]] = {}
_channels_lock = threading.Lock()


def fetch_channels_cached(pattern: str = "**", *,
                          timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """fetch_channels() with a process-wide cache and single-flight.

    The "**" listing is ~9700 names (~MBs of JSON) and every PV picker in the
    suite wants exactly that same list, so downloading it per tab is pure waste.
    An empty/failed listing is never cached — the next caller retries.
    """
    hit = _channels_cache.get(pattern)
    if hit is not None:
        return list(hit)
    with _channels_lock:
        hit = _channels_cache.get(pattern)
        if hit is not None:
            return list(hit)
        out = fetch_channels(pattern, timeout=timeout)
        if out:
            _channels_cache[pattern] = list(out)
        return list(out)


# ── PV-name search (shared by every picker: Shot Finder, Image Slider, …) ──────
# Camera channels (C03-013-PFM1NF:Exposure, …) are ~40 % of the "**" listing.
# They are matched last so a query like "pcm" cannot be filled up entirely by
# camera channels before a single energy PV shows up.
CAM_CHANNEL_RE = re.compile(r"^C\d{2}-\d{2,3}-")


def split_query(text: str) -> list[str]:
    """Query text → lowercase tokens. Spaces, commas and '*' all separate, so
    "hapls sbw4", "hapls,sbw4" and "*hapls**sbw4*" are the same query: every
    token must appear somewhere in the name (implicit wildcards between them)."""
    return [t for t in re.split(r"[\s,;*]+", (text or "").strip().lower()) if t]


def tokens_in_order(hay: str, tokens: "list[str]") -> bool:
    """True when every token occurs in `hay` in the order typed."""
    pos = 0
    for t in tokens:
        i = hay.find(t, pos)
        if i < 0:
            return False
        pos = i + len(t)
    return True


def rank_pv_match(disp: str, key: str, q) -> "int | None":
    """Sort weight of one suggestion against query `q` (lower = better), or None
    when it doesn't match at all. `q` may be raw text or a token list.

    Multi-token queries are AND-matched: "hapls sbw4" keeps only names holding
    both "hapls" and "sbw4" anywhere, i.e. what "*hapls*sbw4*" would mean —
    without having to type the stars. Tokens found in the typed order rank above
    the same tokens scrambled, so nothing is hidden by guessing the order wrong.

    Ranking exists because taking the first N raw substring hits out of ~9700
    channels returned nothing but cameras for queries like "pcm" — the PV the
    user was after was hit #150. Field-name and prefix hits now win.
    """
    tokens = q if isinstance(q, (list, tuple)) else split_query(q)
    if not tokens:
        return None
    d, k = disp.lower(), key.lower()
    field = k.rsplit(":", 1)[-1]
    worst = 0
    total = 0
    for t in tokens:
        if t in (d, k):
            s = 0
        elif field == t:
            s = 1
        elif field.startswith(t) or k.startswith(t) or d.startswith(t):
            s = 2
        elif t in field:
            s = 3
        elif t in d or t in k:
            s = 4
        else:
            return None          # AND semantics: one missing token = no hit
        worst = max(worst, s)
        total += s
    # Weakest token decides the tier; the sum only breaks ties, so a name that
    # matches every token well beats one that barely matches any.
    score = worst * 10 + min(total, 9)
    if len(tokens) > 1 and not (tokens_in_order(k, tokens)
                                or tokens_in_order(d, tokens)):
        score += 5
    if CAM_CHANNEL_RE.match(key):
        # Relative order is preserved when EVERY hit is a camera, so an
        # explicitly camera-targeted query is unaffected.
        score += 100
    return score


def best_pv_match(suggestions, q) -> "str | None":
    """Key of the top-ranked (display, key) suggestion for `q`, or None."""
    tokens = q if isinstance(q, (list, tuple)) else split_query(q)
    best = None
    for i, (disp, key) in enumerate(suggestions):
        s = rank_pv_match(disp, key, tokens)
        if s is not None and (best is None or (s, i) < best[0]):
            best = ((s, i), key)
    return best[1] if best else None


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


# SBW4 was re-archived under a new name on this Prague day. Every sample from
# before it is still under the old name and under NOTHING else, so a query that
# spans the rename has to change name half way — otherwise the older days come
# back empty and read as "the laser never fired", which is not what happened.
SBW4_RENAME_DATE_KEY = "2026-08-14"
SBW4_CHANNEL_LEGACY = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"


def channel_for_day(channel: str, date_key: str) -> str:
    """The name `channel` was archived under on the given Prague day.

    Only SBW4 has ever moved; every other channel is returned unchanged. Date keys
    are 'YYYY-MM-DD', so a plain string comparison orders them correctly.

    This is a GUESS, not a fact: the two SBW4 names take turns depending on which
    laser configuration is running, so the date rule alone reads a day back as
    empty whenever the other name is the one being written. `channel_aliases`
    below is what makes that recoverable."""
    if channel == SBW4_CHANNEL and date_key < SBW4_RENAME_DATE_KEY:
        return SBW4_CHANNEL_LEGACY
    return channel


# Names that mean the SAME measurement. SBW4 is the only one: the HAPLS-era name
# and the L3 name are both live, and which one carries a given day depends on the
# configuration that ran, not on the date. A day that comes back empty under one
# of them is asked for under the other before it is believed.
_CHANNEL_ALIASES: "dict[str, tuple[str, ...]]" = {
    SBW4_CHANNEL:        (SBW4_CHANNEL_LEGACY,),
    SBW4_CHANNEL_LEGACY: (SBW4_CHANNEL,),
}


def channel_aliases(channel: str) -> "tuple[str, ...]":
    """The other names this channel has been archived under (may be empty).

    Kept deliberately small: an alias list is a promise that the two names are the
    same physical measurement, and only SBW4 has ever been renamed."""
    return _CHANNEL_ALIASES.get(channel, ())


# ── shared day cache ──────────────────────────────────────────────────────────

class DayResult(NamedTuple):
    samples: "list[tuple[int, float]]"   # sorted (t_ns, value)
    status:  str                         # "ok" | "empty" | "stale" | "error"
    age_s:   float                       # since last successful fetch (inf for "error")
    # Timestamps of `samples`, precomputed once per fetch. Bisecting a day used to
    # rebuild this list on EVERY lookup (8 ms per call on a 140k-sample span).
    # Optional/last so older positional constructions keep working.
    ts_list: "list[int]" = ()
    # The name that actually produced these samples. Differs from the name asked
    # for only when the channel's other name answered (SBW4), and the caller says
    # so on screen — a graph drawn from a name nobody asked for has to admit it.
    src_channel: str = ""


class _Entry(NamedTuple):
    samples: "list[tuple[int, float]]"
    ts_list: "list[int]"
    fetched_mono: float
    # True when the fetch happened while the day was still running, so the day may
    # have grown since. Without this a day first read at 15:00 stayed cached as an
    # immutable "past day" once midnight passed, and every shot archived after that
    # first read read back as "no data" for the rest of the session.
    partial: bool = False
    # The channel name that actually produced these samples — the bare name, or
    # "<name>.value" for a channel archived only under that alias. The incremental
    # tail query must use it; querying the bare name for an alias-only channel
    # returns nothing, so the day would never grow. Defaulted/last for compat.
    src_channel: str = ""


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

# How long a waiter keeps believing in somebody else's in-flight fetch before it
# throws the record away and fetches for itself. Sized on the genuine worst case
# for one fetch — the full queueing time for a pool slot plus the longest a holder
# may keep it — so a merely slow fetcher is never abandoned.
#
# It exists because the wait used to be unbounded: a fetcher that vanished between
# registering itself and finishing left its record behind, and every later caller
# then waited on an event nobody would ever set. In the Image Slider that is a PV
# worker thread that never returns, so the panel's single-flight flag stays raised
# and the values stop refreshing until the program is restarted — which is exactly
# the freeze operators reported after a few hours online.
_INFLIGHT_MAX_WAIT_S = _POOL_WAIT_S + _MAX_REQUEST_HOLD_S + 5.0

# (channel, date_key) → monotonic time until which a failed fetch is not retried.
_error_until: "dict[tuple[str, str], float]" = {}


def _entry_result(ent: "_Entry", status: str, age: float) -> DayResult:
    return DayResult(ent.samples, status, age, ent.ts_list, ent.src_channel)


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
    waited_s = 0.0

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
        round_s = min(2 * timeout + 10.0,
                      max(0.1, _INFLIGHT_MAX_WAIT_S - waited_s))
        fl.event.wait(round_s)
        if fl.result is not None:
            return fl.result
        # Fetcher still running (wait timed out) or died without a result — loop
        # back to re-check the cache / in-flight state. Past the patience bound the
        # record is treated as dead and dropped, so THIS thread becomes the fetcher
        # instead of waiting forever on an event nobody will set.
        waited_s += round_s
        if waited_s >= _INFLIGHT_MAX_WAIT_S:
            with _day_cache_lock:
                if _inflight.get(key) is fl:
                    del _inflight[key]
                    _stat_bump("takeovers")
            waited_s = 0.0

    try:
        day_start, day_end = day_bounds_ns(date_key)
        if ent is not None and ent.samples:
            # Incremental refresh of today: ask only for what we cannot have yet,
            # and ask the name that produced the samples we already hold (see
            # _Entry.src_channel). try_value_suffix stays off — an empty tail is the
            # NORMAL answer here and must not cost a second request every time.
            src = ent.src_channel or channel
            tail = fetch_values(src,
                                max(day_start, ent.ts_list[-1] - TAIL_OVERLAP_NS),
                                day_end, timeout=timeout, try_value_suffix=False)
            samples = _merge_tail(ent.samples, ent.ts_list, tail)
        else:
            first_failure: "CpvaError | None" = None
            try:
                samples, src = fetch_values_ex(channel, day_start, day_end,
                                               timeout=max(timeout, FULL_DAY_TIMEOUT))
            except CpvaBusyError:
                raise                  # our own pool — says nothing about the name
            except CpvaError as exc:
                # The name asked for FAILED. That is not the end of the story when
                # the measurement has another archived name: a name the archiver
                # does not know for this day answers 400, and treating that as an
                # outage is what made SBW4 report "the archiver did not answer" on
                # days its other name holds every sample. The failure is KEPT and
                # re-raised below unless the other name actually delivers — a day
                # cached as "empty" after a failed query would be a lie.
                if not channel_aliases(channel):
                    raise
                first_failure = exc
                samples, src = [], channel
            # Nothing under the name asked for → try the other name the SAME
            # measurement is archived under (SBW4's HAPLS-era and L3 names take
            # turns). Only ever a SECOND request, and only on a day that came back
            # empty, so a channel with data pays nothing for this. The name that
            # answered is remembered as `src_channel`, so today's incremental tail
            # keeps asking the name that works.
            if not samples:
                for alt in channel_aliases(channel):
                    try:
                        alt_s, alt_src = fetch_values_ex(
                            alt, day_start, day_end,
                            timeout=max(timeout, FULL_DAY_TIMEOUT))
                    except CpvaError:
                        continue
                    if alt_s:
                        samples, src = alt_s, alt_src
                        _stat_bump("alias_hits")
                        first_failure = None
                        break
                if first_failure is not None:
                    raise first_failure
    except CpvaBusyError:
        # Our own pool was full — nothing was learned about this channel or day, so
        # do NOT arm _error_until. Blacking the key out for ERROR_BACKOFF_S on a
        # client-side queue is what turned one busy moment into ~10 s of guaranteed
        # "ERR" on a perfectly healthy PV. The next trigger simply retries.
        with _day_cache_lock:
            ent = _day_cache.get(key)
            if ent is not None:
                res = _entry_result(ent, "stale", time.monotonic() - ent.fetched_mono)
            else:
                res = DayResult([], "error", float("inf"), ())
            _finish_inflight(key, fl, res)
        return res
    except CpvaError:
        with _day_cache_lock:
            _error_until[key] = time.monotonic() + ERROR_BACKOFF_S
            ent = _day_cache.get(key)
            if ent is not None:
                res = _entry_result(ent, "stale", time.monotonic() - ent.fetched_mono)
            else:
                res = DayResult([], "error", float("inf"), ())
            _finish_inflight(key, fl, res)
        return res
    except BaseException:
        with _day_cache_lock:
            _finish_inflight(key, fl, DayResult([], "error", float("inf"), ()))
        raise

    # Everything from here to _finish_inflight stays inside the try as well: a
    # thread that dies AFTER registering its in-flight record and BEFORE publishing
    # a result is precisely what leaves the record behind for every later caller to
    # wait on, so no line between the two may sit outside a handler.
    try:
        ts_list = [s[0] for s in samples]
        partial = int(time.time() * 1e9) <= day_end     # the day had not ended yet
        with _day_cache_lock:
            _error_until.pop(key, None)
            _day_cache[key] = ent = _Entry(samples, ts_list, time.monotonic(),
                                           partial, src)
            _day_cache.move_to_end(key)
            while len(_day_cache) > _DAY_CACHE_MAX:
                _day_cache.popitem(last=False)
            res = _entry_result(ent, "ok" if samples else "empty", 0.0)
            _finish_inflight(key, fl, res)
        return res
    except BaseException:
        with _day_cache_lock:
            _finish_inflight(key, fl, DayResult([], "error", float("inf"), ()))
        raise


def _finish_inflight(key, fl: "_InFlight", result: "DayResult") -> None:
    """Publish `result` to the waiters of THIS record and unregister it.

    The record is passed in rather than looked up: now that a waiter may abandon a
    record (see _INFLIGHT_MAX_WAIT_S), a late fetcher popping "whatever is under
    the key" would silently complete somebody else's newer fetch with its own
    stale answer. Callers hold _day_cache_lock."""
    if _inflight.get(key) is fl:
        del _inflight[key]
    fl.result = result
    fl.event.set()


# Background warm-up must never be able to take the WHOLE pool away from the
# interactive PV fetch: a save-range warm-up of whole days holds each slot for
# seconds, and the panel's own lookups then queued behind it and reported the wait
# as an archiver error. Half the pool is the cap.
_WARM_MAX_WORKERS = max(1, _POOL_SIZE // 2)


def warm_days(channels: "Iterable[str]", date_keys: "Iterable[str]",
              *, today_ttl: float = 3.0, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Pre-load the day cache for channels × date_keys in parallel (bounded by
    _WARM_MAX_WORKERS). Fetch failures are counted and swallowed — this is
    best-effort warm-up and per-item calls will surface/retry them — but only
    CpvaError is: a programming error in here must not stay invisible."""
    jobs = [(ch, dk) for ch in dict.fromkeys(channels) for dk in dict.fromkeys(date_keys)]
    if not jobs:
        return

    def _one(j):
        try:
            get_day(j[0], j[1], today_ttl=today_ttl, timeout=timeout)
        except CpvaError:
            _stat_bump("warm_failures")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(_WARM_MAX_WORKERS, len(jobs))) as ex:
        list(ex.map(_one, jobs))


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


def head_ts_ns(channel: str) -> "int | None":
    """Timestamp of the newest sample READABLE for today, from cache only (no network,
    safe on the GUI thread). None = today is not cached for this channel, or holds no
    samples yet.

    This is what explains "the panel shows the previous shot": nothing past this instant
    has been published, so no frame newer than it can have values of its own. Compare it
    against the FRAME's timestamp, never against the local clock — a workstation whose
    clock is off (one was measured 25 s ahead of this server) would invent a lag that
    does not exist, or hide one that does."""
    res = peek_day(channel, today_key())
    if res is None or not res.ts_list:
        return None
    return res.ts_list[-1]


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
#   status "pending"            → the archiver has not published this moment YET
#                                 (see lookup_near(pending_if_uncovered=True)) →
#                                 retry later; NOT the same as "no sample".
#   status "error"              → fetch failed (retryable, never cached) → "ERR".
#   status "stale"              → value from an older successful fetch → value +
#                                 " (older shot)".
#   matched sample further from the image than PV_EXACT_MATCH_NS → prefix "~":
#                                 the pairing to this exact shot is not certain.

# The words the operator reads. They say what the state IS, not what the program is
# doing about it: "wait" and "(old)" were read as "the program is busy" and "this
# number is a bit stale", when they mean "the archiver has nothing for this frame
# yet" and "this number belongs to an earlier shot".
PV_TEXT_ERROR = "ERR"
PV_TEXT_NOT_FOUND = "n/a"
PV_TEXT_PENDING = "no data yet"
PV_TEXT_STALE_SUFFIX = " (older shot)"
PV_TEXT_APPROX_PREFIX = "~"

# How far a matched sample may sit from the image before the pairing is reported
# as uncertain ("~").
#
# This used to be 150_000_000 (half the 0.3 s shot spacing), which contradicted the
# measurement it cited: offsets are p50 0.025 s / p90 0.30 s, so a tenth of every
# perfectly correct pairing sat beyond the threshold and the panel marked ordinary
# live traffic "~". A marker that fires on normal operation carries no information
# and trains the operator to ignore it.
#
# 0.30 s = the measured p90 AND the caller's own ±window (see is_t._PV_WINDOW_NS),
# which makes the window the single decision boundary: inside it the nearest sample
# IS this shot's, outside it there is no value at all ("n/a"). For per-shot energy
# channels "~" therefore no longer fires on distance — it is left to the quantized
# channels (a waveplate caught mid-move, LookupResult.exact=False), which is a real
# and rare condition. Raise this back toward 0.15 s if the marker is wanted again.
PV_EXACT_MATCH_NS = 300_000_000


class LookupResult(NamedTuple):
    value: "float | None"
    ts_ns: "int | None"      # timestamp of the sample actually used
    status: str              # "ok" | "not_found" | "pending" | "stale" | "error"
    # False when the raw archiver sample had to be SNAPPED onto the channel's value
    # grid (see QUANTIZED_CHANNELS) — i.e. the motor was caught mid-move, so the
    # returned position is the nearest legal one rather than one it reported holding.
    # Defaulted and last, so every existing positional construction keeps working.
    exact: bool = True
    # Newest sample readable for the day that contains ts_ns — the archiver's VISIBLE
    # HEAD for this channel. None when that day holds no samples at all. It is what
    # separates "there is no sample for this moment" from "this moment has not been
    # published yet": the read API lags the machine by about a second (measured
    # 2026-08-14 against the server's own clock: p50 0.9 s, up to ~2.5 s), so anything
    # newer than the head cannot be answered yet, however healthy the channel is.
    # Defaulted/last for positional compatibility.
    head_ts_ns: "int | None" = None


def format_lookup(res: "LookupResult", num_fmt) -> str:
    """Render a LookupResult with the tri-state convention. num_fmt is a
    callable float → str (units/precision differ per tab)."""
    if res.status == "error":
        return PV_TEXT_ERROR
    if res.value is None:
        return PV_TEXT_PENDING if res.status == "pending" else PV_TEXT_NOT_FOUND
    txt = num_fmt(res.value)
    if res.status == "stale":
        txt += PV_TEXT_STALE_SUFFIX
    if not res.exact:
        # Snapped onto the value grid from a mid-move sample — same "~" the energy
        # channels use for a match that may belong to the neighbouring shot.
        txt = PV_TEXT_APPROX_PREFIX + txt
    return txt


# Channels whose archiver record is a STEP function: a sample is written only
# when the value CHANGES (motor position setpoints/readbacks). At any instant
# the true value is therefore the last sample at or before that instant, however
# old it is — matching them against a ±window is wrong, because between two
# moves there is no nearby sample at all.
STEP_CHANNELS: frozenset = frozenset({"L3-PFWP6-MTR03-1:RawPos"})
# Backwards-compatible alias (older call sites read FORWARD_CHANNELS).
FORWARD_CHANNELS: frozenset = STEP_CHANNELS


# ── the same rule, worked out from the archive instead of from a list ─────────
# The set above cannot be kept complete by hand: every setpoint readback in the
# facility behaves the same way, and the one that is missing from it reads "no
# data yet" for ever. The GDD setting L3-SPFE-AOD03-002:Order2_RB did exactly
# that (2026-09-01): it had been holding 24300 since 10:35 that morning, wrote
# 16 samples all day, and no ±0.3 s window around an image ever touched one.
#
# So a channel that misses the window is now CLASSIFIED from its own record:
# collect the samples it wrote at or before the moment asked about — walking back
# days until there are enough of them — and look at how they are spread.
#
# A slow gap alone is not enough to call something a setting, because the laser is
# not always fast: a run at one shot every 25 s is a real cadence (it is the one the
# panel's own "trailing by a shot" bug was reported at), and holding a detector's
# reading forward across it would hand out a neighbouring shot's energy — exactly
# what the ±window exists to prevent. Nor is counting long gaps enough: a setting
# nudged twenty times in ten minutes has mostly one-second gaps, and it is still a
# setting for the rest of the day.
#
# What separates them is where the TIME goes. A channel written only on change
# spends practically all of its time between samples — the samples are the events,
# the silence is the value standing. A detector spends its time firing: its gaps are
# its cadence. So the verdict is time-weighted:
#   * a typical gap (p75) longer than STEP_LONG_GAP_S is slower than any shot cadence
#     the machine has, and settles it on its own; otherwise
#   * add up the gaps that are unusually long for THIS channel (at least
#     STEP_LONG_GAP_RATIO × its median gap, and never less than
#     STEP_MIN_TYPICAL_GAP_S) and compare with the time the samples cover. At
#     STEP_HOLD_FRACTION or more, the channel is holding rather than measuring.
# The relative cut is what keeps a 25 s cadence out of it: every gap is the cadence,
# so none of them is "unusually long" and the fraction is zero. Measured on this
# archive: PTM1 energy every 5 s all day → 0, the GDD orders → ~1.
STEP_MIN_TYPICAL_GAP_S = 20.0
STEP_LONG_GAP_S = 600.0
STEP_LONG_GAP_RATIO = 10.0
STEP_HOLD_FRACTION = 0.9
STEP_TYPICAL_GAP_Q = 0.75
# What the verdict has to see before it is taken: this many samples AND this much
# time covered by them, walking back through at most this many days. The SPAN is
# what stops a setting somebody is working on right now from reading as a live
# measurement — ten minutes of nudges look exactly like one if that is all you
# look at, and the walk back is what puts the quiet hours around them back in view.
STEP_PROBE_MIN_SAMPLES = 8
STEP_PROBE_MIN_SPAN_S = 2 * 3600.0
STEP_PROBE_MAX_DAYS = 8
# channel → (verdict, when it was reached). A fetch failure produces NO verdict
# rather than a wrong one.
_step_verdict: "dict[str, tuple[bool, float]]" = {}
_step_lock = threading.Lock()
# Verdicts expire. A channel's nature does not change, but the evidence available
# for it does — the day it is asked about may have barely started, and a setting is
# indistinguishable from a measurement for as long as somebody is turning the knob.
# Re-deciding costs one already-cached day read, and it means any misreading heals
# itself within minutes instead of lasting the whole session.
STEP_VERDICT_TTL_S = 600.0


def _cached_step_verdict(channel: str) -> "bool | None":
    with _step_lock:
        ent = _step_verdict.get(channel)
        if ent is None:
            return None
        verdict, stored = ent
        if time.monotonic() - stored < STEP_VERDICT_TTL_S:
            return verdict
        del _step_verdict[channel]
        return None


def _gap_quantile_s(ts_sorted: "list[int]", q: float) -> "float | None":
    """The q-quantile of the gaps between consecutive timestamps, in seconds.
    None when there are not two samples to compare."""
    if len(ts_sorted) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(ts_sorted, ts_sorted[1:]))
    return gaps[min(len(gaps) - 1, int(round(q * (len(gaps) - 1))))] / 1e9


def _typical_gap_s(ts_sorted: "list[int]") -> "float | None":
    """How long this channel usually goes between samples (p75, see above)."""
    return _gap_quantile_s(ts_sorted, STEP_TYPICAL_GAP_Q)


def hold_fraction(ts_sorted: "list[int]") -> "float | None":
    """How much of the time these samples cover is spent in a gap that is unusually
    long for this channel — i.e. holding rather than measuring. None when there are
    fewer than two samples."""
    if len(ts_sorted) < 2:
        return None
    gaps = [b - a for a, b in zip(ts_sorted, ts_sorted[1:])]
    span = ts_sorted[-1] - ts_sorted[0]
    if span <= 0:
        return None
    median = sorted(gaps)[len(gaps) // 2]
    cut = max(median * STEP_LONG_GAP_RATIO, STEP_MIN_TYPICAL_GAP_S * 1e9)
    return sum(g for g in gaps if g >= cut) / span


def _holds_between_samples(ts_sorted: "list[int]") -> "bool | None":
    """Does this run of samples read as a value written only when it CHANGES?

    None when there is nothing to judge by (fewer than two samples)."""
    slow = _typical_gap_s(ts_sorted)
    if slow is None:
        return None
    if slow >= STEP_LONG_GAP_S:
        return True                         # slower than any shot cadence there is
    held = hold_fraction(ts_sorted)
    return held is not None and held >= STEP_HOLD_FRACTION


def _probe_days(channel: str, upto_ns: int, start_dk: str, *,
                primary: "list[tuple[int, float]] | None",
                today_ttl: float, timeout: float, network_ok: bool
                ) -> "tuple[list[int], bool] | None":
    """Timestamps of the samples at or before upto_ns, walking back from the Prague
    day start_dk until enough of them cover enough time (STEP_PROBE_MIN_SAMPLES /
    STEP_PROBE_MIN_SPAN_S) or the walk runs out of days.

    Returns (sorted timestamps, run_is_representative). None when a day could not be
    read at all: an unreadable day must produce no verdict rather than a wrong one."""
    seen: "set[int]" = set()
    dk = start_dk
    samples = primary
    for i in range(STEP_PROBE_MAX_DAYS):
        if i or samples is None:
            if not network_ok:
                res = peek_day(channel, dk)
                if res is None:
                    return None             # cache-only and this day is not in it
            else:
                res = get_day(channel, dk, today_ttl=today_ttl, timeout=timeout)
            if res.status == "error":
                return None
            samples = res.samples
        seen.update(t for t, _v in samples if t <= upto_ns)
        ts_sorted = sorted(seen)
        if (len(ts_sorted) >= STEP_PROBE_MIN_SAMPLES
                and ts_sorted[-1] - ts_sorted[0] >= STEP_PROBE_MIN_SPAN_S * 1e9):
            return ts_sorted, True
        dk = prev_date_key(dk)
        samples = None
    return sorted(seen), False


def classify_step_channel(channel: str, ts_ns: int, *,
                          primary: "list[tuple[int, float]] | None" = None,
                          today_ttl: float = 3.0,
                          timeout: float = DEFAULT_TIMEOUT,
                          network_ok: bool = True) -> bool:
    """Decide (and remember) whether `channel` holds its value between samples.

    `primary` is the sample list of the day containing ts_ns when the caller has
    already fetched it — the common case, and it usually settles the question on
    its own without a single extra request."""
    if channel in STEP_CHANNELS:
        return True
    cached = _cached_step_verdict(channel)
    if cached is not None:
        return cached
    probe = _probe_days(channel, ts_ns, date_key_for_ns(ts_ns), primary=primary,
                        today_ttl=today_ttl, timeout=timeout, network_ok=network_ok)
    if probe is None:
        return False                        # a day could not be read — no verdict
    ts_sorted, complete = probe
    if len(ts_sorted) < 2 and network_ok:
        # Nothing written in the last STEP_PROBE_MAX_DAYS days. That is either a
        # setting nobody has touched for weeks or a channel that has STOPPED — and
        # the difference matters: holding a dead energy detector's last reading
        # forward would present a three-week-old shot as this frame's. So find the
        # last sample there is, wherever it is, and judge the channel by the company
        # it kept THEN: a detector's own day is dense, a setting's is not.
        back = value_at_or_before(channel, ts_ns, today_ttl=today_ttl,
                                  timeout=timeout)
        if back.ts_ns is not None:
            probe = _probe_days(channel, back.ts_ns, date_key_for_ns(back.ts_ns),
                                primary=None, today_ttl=today_ttl, timeout=timeout,
                                network_ok=network_ok)
            if probe is not None:
                ts_sorted, complete = probe
    verdict = _holds_between_samples(ts_sorted)
    if verdict is None:
        # A single sample in the whole archive, or none at all: nothing to judge by,
        # and nothing worth remembering either — the next lookup may know better.
        return False
    if complete or verdict:
        # A verdict off a run that is too short to be representative is not worth
        # keeping unless it is "this holds its value" — which few samples over many
        # days IS the evidence for. Everything kept expires, see _cached_step_verdict.
        with _step_lock:
            _step_verdict[channel] = (verdict, time.monotonic())
    return verdict


def is_step_channel(channel: str, ts_ns: "int | None" = None, *,
                    today_ttl: float = 3.0,
                    timeout: float = DEFAULT_TIMEOUT,
                    network_ok: bool = True) -> bool:
    """True when `channel` is archived only when it CHANGES, so its value at any
    moment is the last sample at or before it.

    Without ts_ns this answers from what is already known (the explicit list plus
    verdicts reached earlier) and never fetches — that is what the display paths
    want. With ts_ns it classifies the channel if it has to."""
    if channel in STEP_CHANNELS:
        return True
    cached = _cached_step_verdict(channel)
    if cached is not None:
        return cached
    if ts_ns is None:
        return False
    return classify_step_channel(channel, int(ts_ns), today_ttl=today_ttl,
                                 timeout=timeout, network_ok=network_ok)


def invalidate_step_verdicts(channel: "str | None" = None) -> None:
    """Forget the auto-detected step/live verdicts (None = all)."""
    with _step_lock:
        if channel is None:
            _step_verdict.clear()
        else:
            _step_verdict.pop(channel, None)


# ── value grid (quantized channels) ───────────────────────────────────────────
# Channels whose real value can only ever be a multiple of a fixed step. The
# waveplate is commanded in whole 1000-count positions, so ANY other reading is
# the raw motor readback caught while it was still travelling between two of
# them — the archiver records a sample on every change, mid-move included.
#
# Reading such a sample back verbatim reports a position the waveplate was never
# set to, which is a wrong number on a laser diagnostic, not a rounding detail.
# So every value of a quantized channel is snapped onto its grid, and a value
# that had to be snapped from off-grid is reported with exact=False so the UI can
# mark it "~" (the pairing to a real setting is uncertain).
#
# CSS Logger/main.py (_filter_master_multiple_rows) already applies this rule to
# this very PV; the Image Tools were the only place that ignored it.
WAVEPLATE_STEP = 1000.0
QUANTIZED_CHANNELS: "dict[str, float]" = {
    "L3-PFWP6-MTR03-1:RawPos": WAVEPLATE_STEP,
}
# How far off the grid a sample may sit and still count as a settled reading.
# 0.5 % of the step = 5 counts on 1000, the same tolerance the CSS Logger uses on
# this channel. A fixed near-zero epsilon would only ever match a bit-exact value
# and would therefore flag every real encoder readback as mid-move.
GRID_TOL_FRACTION = 0.005


def grid_step(channel: str) -> "float | None":
    """Value-grid step for channel, or None when it is not a quantized channel."""
    return QUANTIZED_CHANNELS.get(channel)


def on_grid(value: float, step: float) -> bool:
    """True when value is a settled reading, i.e. within tolerance of a multiple
    of step."""
    tol = max(1e-6, abs(step) * GRID_TOL_FRACTION)
    return abs(value - round(value / step) * step) <= tol


def quantize(channel: str, value: "float | None") -> "tuple[float | None, bool]":
    """(value snapped onto the channel's grid, was_already_on_grid).

    The ONE place the multiple-of-N rule lives — every display and every lookup
    path goes through it, so a quantized channel can never surface an off-grid
    number. Non-quantized channels and None pass straight through as exact."""
    if value is None:
        return None, True
    step = QUANTIZED_CHANNELS.get(channel)
    if not step:
        return value, True
    return round(value / step) * step, on_grid(value, step)

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
# Entries are (payload, stored_monotonic); payload None = "genuinely nothing before
# this day". A day boundary in the past is immutable, so a POSITIVE hit never
# expires. A NEGATIVE one does — see _BEFORE_NEG_TTL_S.
_before_cache: "OrderedDict[tuple[str, str], tuple[tuple[float, int] | None, float]]" = OrderedDict()
_before_lock = threading.Lock()
_BEFORE_CACHE_MAX = 256
# A cached "nothing before this day" used to last for the whole process. So one
# transient empty answer — or a channel temporarily archived only under its .value
# alias — pinned the waveplate at "n/a" until the user hit Refresh, for the rest of
# the shift. 10 minutes is long enough that one save range of thousands of frames
# still pays exactly ONE wide look-back, short enough that nothing stays pinned.
_BEFORE_NEG_TTL_S = 600.0
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

    Cached per (channel, date_key); fetch failures are NEVER cached, and a NEGATIVE
    result only for _BEFORE_NEG_TTL_S (a past day boundary that really has data is
    immutable, so a positive hit is cached for good)."""
    ck = (channel, date_key)
    now = time.monotonic()
    with _before_lock:
        entry = _before_cache.get(ck)
        if entry is not None:
            hit, stored = entry
            if hit is not None:
                _before_cache.move_to_end(ck)
                return LookupResult(hit[0], hit[1], "ok")
            if now - stored < _BEFORE_NEG_TTL_S:
                _before_cache.move_to_end(ck)
                return LookupResult(None, None, "not_found")
            del _before_cache[ck]           # negative entry expired — re-query below
        if _before_error_until.get(ck, 0.0) > now:
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
        if found is None and not channel.endswith(".value"):
            # Every window came back empty. Before recording an absence, try the
            # ".value" alias ONCE over the widest window — some channels are archived
            # only under it, and this path deliberately runs with try_value_suffix off
            # (an empty look-back is normal and must not cost a second request each
            # time). One extra request, only in the already-failing case.
            widest = max(lookback_days) if lookback_days else 0
            if widest:
                alias = parse_samples(fetch_samples(
                    channel + ".value", end_ns - widest * DAY_NS, end_ns,
                    timeout=timeout))
                if alias:
                    t_ns, val = alias[-1]
                    found = (val, t_ns)
    except CpvaError:
        with _before_lock:
            _before_error_until[ck] = time.monotonic() + ERROR_BACKOFF_S
        return LookupResult(None, None, "error")
    with _before_lock:
        _before_error_until.pop(ck, None)
        _before_cache[ck] = (found, time.monotonic())
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

    A failed day no longer aborts the lookup. It is treated as empty and degrades
    the status to "stale", so the walk (and then the wide look-back) can still
    answer: for a step PV the day OF the image usually holds no samples at all
    (the motor did not move that day), so nearly every lookup reaches day-1/day-2
    and one hiccup there used to produce "ERR" for a PV that was perfectly
    resolvable. An unverifiable value is now shown flagged " (old)" rather than
    withheld; only when nothing at all can be found is "error" returned.

    network_ok=False serves cache hits only (for UI-thread callers); days that
    are not cached are reported as "stale" rather than dropped, so a value is
    still shown — flagged as possibly out of date."""
    ts_ns = int(ts_ns)
    dk = date_key_for_ns(ts_ns)
    status = "ok"
    saw_error = False
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
            saw_error = True
            status = "stale"
            res = DayResult([], "empty", float("inf"))
        elif res.status == "stale":
            status = "stale"
        hit = _last_at_or_before(res.samples, ts_ns)
        if hit is not None:
            val, exact = quantize(channel, hit[1])
            return LookupResult(val, hit[0], status, exact)
        if _i < _STEP_DAY_WALK - 1:
            dk = prev_date_key(dk)
    # dk is now the EARLIEST day consulted — anchor the wide query at its start
    # so no day is skipped between the walk and the look-back.
    back = _value_before_day(channel, dk, lookback_days=lookback_days,
                             timeout=timeout, network_ok=network_ok)
    if back.value is None:
        # Nothing anywhere. Report the fetch failure we saw rather than "no data",
        # so the caller retries instead of caching an absence.
        if saw_error or back.status == "error":
            return LookupResult(None, None, "error")
        return back
    val, exact = quantize(channel, back.value)
    st = "stale" if (status == "stale" or back.status == "stale") else back.status
    return LookupResult(val, back.ts_ns, st, exact)


def lookup_near(channel: str, ts_ns: int, *,
                window_ns: int = 30 * 1_000_000_000,
                prefer: "str | None" = None,
                fallback_before: bool = False,
                pending_if_uncovered: bool = False,
                today_ttl: float = 3.0,
                timeout: float = DEFAULT_TIMEOUT) -> LookupResult:
    """Sample nearest ts_ns within ±window_ns.

    Step channels bypass the window entirely: their value is only archived on
    change, so the correct reading at ts_ns is the last sample at or before it
    (value_at_or_before), and a "nearest within 30 s" match would either miss
    (long hold between moves) or jump early to the NEXT position. Which channels
    those are is not a fixed list — a channel that misses the window is classified
    from its own record (see classify_step_channel) and answered by look-back the
    moment it turns out to be one, so a setting the list never heard of reads its
    real value instead of "no data yet".

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

    pending_if_uncovered=True returns status "pending" instead of "not_found" when
    nothing matched AND today's data does not yet reach past ts_ns + window_ns, i.e.
    the archiver's visible head is still BEFORE the moment being asked about. A sample
    is published about a second after it is taken while an image is on the share in
    ~0.02 s, so for a fresh frame "no sample" is normally "not published yet" — a caller
    that cannot tell the two apart either shows a neighbouring shot's number or gives up
    on a value that is about to arrive. Only today can pend: a finished day cannot grow.

    Two statuses are tracked, not one. `hit_status` belongs to the day the winning
    sample came from, so a NEIGHBOURING day failing can no longer label a perfectly
    good value "error". `primary_status` belongs to the day that actually contains
    ts_ns, and it alone decides the no-hit answer: a primary day that answered
    cleanly and simply had nothing in the window is "not_found" ("n/a"), not "error"
    ("ERR"). Collapsing both into one variable is why healthy PVs read ERR.
    """
    if is_step_channel(channel):
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
    hit_status = "ok"
    primary_status = "ok"
    primary_head: "int | None" = None
    primary_samples: "list[tuple[int, float]] | None" = None
    for dk in date_keys:
        res = get_day(channel, dk, today_ttl=today_ttl, timeout=timeout)
        day_status = "error" if res.status == "error" else (
            "stale" if res.status == "stale" else "ok")
        if dk == date_key:
            primary_status = day_status
            primary_head = res.ts_list[-1] if res.ts_list else None
            primary_samples = res.samples
        cand = nearest_sample_ex(res.samples, ts_ns, window_ns=window_ns,
                                 prefer=prefer, ts_list=res.ts_list)
        if cand is None:
            continue
        score = _match_score(cand[0] - ts_ns, prefer)
        if hit_score is None or score < hit_score:
            hit, hit_score, hit_status = cand, score, day_status
    if hit is not None:
        val, exact = quantize(channel, hit[1])
        return LookupResult(val, hit[0], hit_status, exact, primary_head)
    if fallback_before:
        back = value_at_or_before(channel, ts_ns, today_ttl=today_ttl,
                                  timeout=timeout)
        if back.status == "error" and primary_status == "error":
            return back
        if back.value is not None:
            return back._replace(head_ts_ns=primary_head)
    # Nothing in the window, and the caller did not ask for a look-back. Before
    # answering "nothing", ask what KIND of channel this is: one that is written
    # only when it changes has no sample near any frame, and its value at this
    # moment is simply the last one written (see classify_step_channel). That is
    # not a stale reading — it is the setting the machine is running with.
    if primary_status != "error" and classify_step_channel(
            channel, ts_ns, primary=primary_samples,
            today_ttl=today_ttl, timeout=timeout):
        back = value_at_or_before(channel, ts_ns, today_ttl=today_ttl,
                                  timeout=timeout)
        if back.value is not None:
            return back._replace(head_ts_ns=primary_head)
    if primary_status == "error":
        return LookupResult(None, None, "error", True, primary_head)
    # Nothing matched. Is that "no such sample" or "not published yet"? Only the
    # head can tell, and only for today — a finished day will never grow again.
    if (pending_if_uncovered and date_key == today_key()
            and (primary_head is None or primary_head < ts_ns + window_ns)):
        return LookupResult(None, None, "pending", True, primary_head)
    return LookupResult(None, None, "not_found", True, primary_head)
