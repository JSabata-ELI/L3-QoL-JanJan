"""
Low-level CPVA archiver client for the PV Monitor tab.

These functions are copied verbatim from the standalone PV Monitor app so the
whole toolchain shares one proven implementation.

CPVA host uses a self-signed cert -> TLS verification is disabled for the
archiver session only. (The notifier clients in alerting.py keep TLS ON.)
"""

from __future__ import annotations

import fnmatch
import json
import re
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import urllib3
from requests.adapters import HTTPAdapter

TZ_PRAGUE = ZoneInfo("Europe/Prague")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


APP_DIR = get_app_dir()

# ---------------------------------------------------------------------------
# CPVA archiver API constants
# ---------------------------------------------------------------------------

CPVA_BASE_URL          = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT  = "/samples"
CPVA_CHANNELS_ENDPOINT = "/channels"
CPVA_HTTP_TIMEOUT      = 10.0

# The default query window. It is NOT a hard limit of the archiver: what the
# archiver refuses is a response that carries too many samples (measured: fine
# up to roughly 110 000, HTTP 500 above that), so a slowly archived channel
# answers a whole day in one request while a fast one struggles with two hours.
# One hour is simply the span that is safe for every channel without asking
# first. chart_history.py measures the channel's rate and picks a bigger span.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds

# The ceiling on ONE answer, and the reason this module has one at all.
#
# A reading is not a number and a timestamp: the archiver sends severity,
# status, quality and a metaData block with every one of them. Measured
# 2026-09-02 on two utility channels: 345 bytes per reading on the wire, and
# about 1.7 KB per reading once json.loads has built the dictionaries - a
# five-fold expansion. So an answer covering a whole day of a channel written
# thirty times a second is ~900 MB on the wire and ~4.5 GB in RAM, and ten of
# those in flight is more memory than the PC has.
#
# That is not theory: on 2026-09-02 a 180-day plot took the laptop down that
# way (Diagnostic at 24 GB of a 31 GB commit limit, desktop frozen, hard power
# off). Nothing refused the oversized answer, because the archiver had happily
# agreed to send it.
#
# So the size is refused HERE, while the body is still arriving: the download
# is abandoned the moment it passes the ceiling, and the caller is told to ask
# for a shorter range instead - which the splitter below already knows how to
# do.
#
# 32 MB is about 93 000 readings, or ~160 MB of RAM once parsed. That leaves
# room above the 60 000 readings chart_history aims for per request (~21 MB),
# so an ordinary plot never trips the ceiling, while ten requests in flight
# cannot cost more than roughly 1.6 GB however wrong the plan was.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

# The channel list is one big answer by nature (thousands of names) and carries
# no per-reading overhead, so it gets its own, looser ceiling.
MAX_CHANNELS_RESPONSE_BYTES = 64 * 1024 * 1024


class ResponseTooLarge(Exception):
    """The answer passed MAX_RESPONSE_BYTES and was abandoned mid-download.

    Treated exactly like the archiver's own size refusal: ask for less time.
    """

    def __init__(self, n_bytes: int, limit: int):
        self.n_bytes = n_bytes
        self.limit = limit
        super().__init__(f"answer exceeded {limit // (1024 * 1024)} MB "
                         f"and was abandoned - asking for a shorter range")

# Halving a refused request is the only thing that helps - retrying the same
# range never does. Below the floor a failure is a real failure, not a size
# problem.
SPLIT_MIN_SPAN_NS = int(60 * 1e9)          # 1 minute
# Deep enough to get from a whole-day request down to a few minutes. Four
# halvings only reach 1.5 h, which a busy channel still answers with far too
# much - the chunk was then given up on and left a hole in the plot.
SPLIT_MAX_DEPTH   = 8                      # <= 256 pieces per chunk
MAX_CHUNK_SPAN_NS = int(24 * 3600 * 1e9)   # never ask for more than a day at once

_SESSION = requests.Session()
_SESSION.verify = False
# The PV Monitor polls many channels concurrently. urllib3's default pool
# (10 connections) throttles that and forces connection churn -- each evicted
# connection means a fresh TLS handshake on the next request, which is slower
# than the fetch itself. Size the pool to the largest poll concurrency the UI
# allows (poll_max_workers, capped at 64) so the pool is never the bottleneck.
_adapter = HTTPAdapter(pool_connections=64, pool_maxsize=64)
_SESSION.mount("https://", _adapter)
_SESSION.mount("http://", _adapter)


# ---------------------------------------------------------------------------
# CPVA API - HTTP
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT,
                   max_bytes: int = MAX_RESPONSE_BYTES):
    """GET one JSON answer, refusing to hold more than `max_bytes` of it.

    Streamed rather than taken in one piece so an oversized answer is dropped
    while it is still arriving: `resp.content` would have the whole thing in
    memory before anyone could object, which is exactly how a single plot once
    reached 24 GB. Content-Length is honoured when the server sends one, so an
    answer that announces itself as too big costs no download at all.
    """
    resp = _SESSION.get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json"},
        stream=True,
    )
    try:
        resp.raise_for_status()
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLarge(int(declared), max_bytes)
        buf = bytearray()
        for part in resp.iter_content(chunk_size=256 * 1024):
            buf += part
            if len(buf) > max_bytes:
                raise ResponseTooLarge(len(buf), max_bytes)
    finally:
        # Closing a streamed response mid-body is what actually abandons the
        # download; without it the connection stays busy delivering an answer
        # nobody will read.
        resp.close()
    return json.loads(buf)


def cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                       timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url  = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def cpva_is_splittable_error(exc: BaseException) -> bool:
    """True when asking for a shorter time range is likely to help.

    The archiver answers HTTP 500 when one response would carry too many
    samples, and a very large response can also die as a read timeout or a
    dropped connection. Everything else - a bad channel name (4xx), a malformed
    answer - would fail again just as hard on half the range.

    Our own size refusal counts too, and is the common case now: the archiver
    is willing to send far more than this program is willing to hold.
    """
    if isinstance(exc, ResponseTooLarge):
        return True
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", 0) or 0
        return 500 <= code < 600
    return False


def cpva_fetch_samples_split(channel: str, start_ns: int, end_ns: int,
                             timeout: float = CPVA_HTTP_TIMEOUT,
                             min_span_ns: int = SPLIT_MIN_SPAN_NS,
                             max_depth: int = SPLIT_MAX_DEPTH,
                             on_split=None) -> list[dict]:
    """Fetch one range, halving it when the archiver refuses the size, and
    return the readings of the whole range as one list.

    ONLY FOR A RANGE THAT IS MEANT TO FIT IN MEMORY. Splitting makes each
    *answer* smaller; this function then joins them back together, so the
    *result* is as big as the range asked for however finely it was cut. That
    is what filled 24 GB on 2026-09-02: whole-day requests for a busy channel
    were refused, split into pieces the archiver would answer, and every piece
    kept. For a long window use cpva_fetch_samples_piecewise, which hands each
    piece over and forgets it.

    `on_split(channel, span_ns)`, if given, is told the span that failed, so the
    caller can shrink the rest of its plan instead of collecting the same
    refusal a few thousand more times.
    """
    out: list[dict] = []
    cpva_fetch_samples_piecewise(
        channel, start_ns, end_ns, timeout,
        on_piece=lambda s, e, samples: out.extend(samples),
        min_span_ns=min_span_ns, max_depth=max_depth, on_split=on_split)
    return out


@dataclass(frozen=True)
class ChunkTask:
    """One planned request: a channel and the time range to ask it for."""
    channel: str
    start_ns: int
    end_ns: int
    index: int = 0        # the caller's own ordering, echoed back untouched


def cpva_fetch_samples_piecewise(channel: str, start_ns: int, end_ns: int,
                                 timeout: float = CPVA_HTTP_TIMEOUT, *,
                                 on_piece,
                                 on_piece_error=None,
                                 min_span_ns: int = SPLIT_MIN_SPAN_NS,
                                 max_depth: int = SPLIT_MAX_DEPTH,
                                 on_split=None,
                                 cancel_fn=None) -> None:
    """Fetch one range, halving what is refused, handing over each piece as it
    arrives instead of collecting them.

    This is the difference between a bounded read and an unbounded one.
    `cpva_fetch_samples_split` returns `left + right`, so cutting a request up
    makes each *answer* smaller but the *result* just as large - a day of a
    busy channel is millions of readings however it was fetched. Here every
    piece goes to `on_piece(start_ns, end_ns, samples)` and is then dropped, so
    a range costs what its largest piece costs and nothing more.

    A piece that cannot be read is reported to `on_piece_error(start, end,
    exc)` and the rest are still fetched; without that callback the failure is
    raised, as the old function did. `cancel_fn` is asked before every request,
    so a plot given up on stops halfway through a split too.
    """
    def go(cs: int, ce: int, depth: int) -> None:
        if cancel_fn is not None and cancel_fn():
            return
        try:
            samples = cpva_fetch_samples(channel, cs, ce, timeout)
        except Exception as exc:                  # noqa: BLE001 - reported below
            span = ce - cs
            if (depth <= 0 or span <= min_span_ns
                    or not cpva_is_splittable_error(exc)):
                if on_piece_error is None:
                    raise
                on_piece_error(cs, ce, exc)
                return
            if on_split:
                try:
                    on_split(channel, span)
                except Exception:                 # noqa: BLE001 - advisory only
                    pass
            mid = cs + span // 2
            go(cs, mid, depth - 1)
            # Both ends of the archiver's range are inclusive, so the second
            # half starts one nanosecond later or the boundary sample arrives
            # twice.
            go(mid + 1, ce, depth - 1)
            return
        on_piece(cs, ce, samples)

    go(start_ns, end_ns, max_depth)


def _fetch_task(task: "ChunkTask", timeout: float, span_hint, on_split, *,
                on_piece, on_piece_error=None, cancel_fn=None) -> None:
    """Fetch one task's range, honouring a span the channel is known to choke on."""
    hint = span_hint.get(task.channel) if span_hint is not None else None
    span = task.end_ns - task.start_ns
    kw = dict(on_piece=on_piece, on_piece_error=on_piece_error,
              on_split=on_split, cancel_fn=cancel_fn)
    if not hint or hint >= span:
        cpva_fetch_samples_piecewise(task.channel, task.start_ns, task.end_ns,
                                     timeout, **kw)
        return
    cs = task.start_ns
    while cs < task.end_ns:
        ce = min(cs + hint, task.end_ns)
        cpva_fetch_samples_piecewise(task.channel, cs, ce, timeout, **kw)
        cs = ce + 1


def cpva_run_chunks(tasks: list["ChunkTask"], *,
                    timeout: float = CPVA_HTTP_TIMEOUT,
                    max_workers: int = 12,
                    on_result=None,
                    on_error=None,
                    progress_fn=None,
                    cancel_fn=None,
                    span_hint: dict | None = None) -> tuple[int, int]:
    """Run every task on one shared pool. Returns (done, failed).

    One pool for all (channel x chunk) pairs, not a pool per channel, so several
    PVs are read at the same time instead of one after the other.

    `on_result(task, samples)` and `on_error(task, exc)` are called **in the
    worker thread**. That is deliberate: a full response can be tens of
    megabytes of dictionaries, so it has to be reduced and dropped where it was
    received. Collecting them all first is what makes a long window run the
    machine out of memory.

    A failing task never stops the others - that is the whole point. The caller
    decides what a gap means.
    """
    total = len(tasks)
    if not total:
        if progress_fn:
            progress_fn(0, 0)
        return 0, 0

    lock = threading.Lock()

    def _note_split(channel: str, span_ns: int) -> None:
        if span_hint is None:
            return
        with lock:
            prev = span_hint.get(channel)
            new = max(SPLIT_MIN_SPAN_NS, span_ns // 2)
            if prev is None or new < prev:
                span_hint[channel] = new

    counts = {"done": 0, "failed": 0}

    def _run(task: "ChunkTask") -> None:
        if cancel_fn is not None and cancel_fn():
            return
        bad = []

        def piece(cs: int, ce: int, samples: list) -> None:
            # Handed over here, in this thread, while the response is still
            # local: the caller reduces it and lets it go. Passing it back to
            # the main loop would keep every answer alive at once. The task is
            # re-cut to the piece's own bounds, so a caller that tracks which
            # stretches were read stays right even when a request was split.
            if on_result:
                on_result(ChunkTask(task.channel, cs, ce, task.index), samples)

        def piece_failed(cs: int, ce: int, exc: BaseException) -> None:
            bad.append(exc)
            if on_error:
                on_error(ChunkTask(task.channel, cs, ce, task.index), exc)

        try:
            _fetch_task(task, timeout, span_hint, _note_split,
                        on_piece=piece, on_piece_error=piece_failed,
                        cancel_fn=cancel_fn)
        except Exception as exc:                  # noqa: BLE001 - reported, not raised
            piece_failed(task.start_ns, task.end_ns, exc)
        with lock:
            if bad:
                counts["failed"] += 1
            else:
                counts["done"] += 1
            seen = counts["done"] + counts["failed"]
        if progress_fn:
            progress_fn(seen, total)

    workers = max(1, min(max_workers, total))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_run, t) for t in tasks]
        try:
            for fut in as_completed(futures):
                fut.result()          # re-raise a bug in on_result/on_error
        finally:
            if cancel_fn is not None and cancel_fn():
                # Drop everything still queued instead of paying for requests
                # whose answer nobody wants.
                ex.shutdown(wait=False, cancel_futures=True)
    return counts["done"], counts["failed"]


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 12,
                               progress_fn=None,
                               chunk_ns: int = CHUNK_SIZE_NS,
                               errors: dict | None = None,
                               cancel_fn=None) -> list[dict]:
    """Fetch a channel's samples in parallel time-chunks.

    progress_fn, if given, is called as progress_fn(done, total) after each
    chunk completes so callers can drive a progress bar.

    `cancel_fn`, if given, is asked before every chunk whether the answer is
    still wanted; once it says yes, the chunks still queued are skipped instead
    of being fetched. A year-long window is hundreds of requests, and without
    this there is no way to take back a query that was asked by mistake.

    `errors` decides what a failed chunk means. Left out, the first failure is
    raised and nothing is returned - the historical behaviour. Pass a dict and
    the failure is recorded there instead (`{channel: message}`) while the
    remaining chunks are still fetched, so one unreadable hour costs an hour and
    not the whole query.
    """
    chunks = []
    cs = start_ns
    i = 0

    while cs < end_ns:
        ce = min(cs + chunk_ns, end_ns)
        chunks.append(ChunkTask(channel, cs, ce, i))
        i += 1
        cs = ce

    if not chunks:
        if progress_fn:
            progress_fn(0, 0)
        return []

    if log_fn and len(chunks) > 1:
        log_fn(f"      {channel}: {len(chunks)} chunks")

    results_map = {}
    lock = threading.Lock()
    first_error: list[BaseException] = []

    def _keep(task, samples):
        # Keyed by (chunk, where the piece starts), not by chunk alone: a chunk
        # the archiver refused arrives as several pieces sharing one index, and
        # keying by index would keep only the last of them.
        with lock:
            results_map[(task.index, task.start_ns)] = samples

    def _note(task, exc):
        with lock:
            if not first_error:
                first_error.append(exc)
            if errors is not None:
                errors.setdefault(channel, str(exc))

    cpva_run_chunks(chunks, timeout=timeout, max_workers=max_workers,
                    on_result=_keep, on_error=_note, progress_fn=progress_fn,
                    cancel_fn=cancel_fn)

    if cancel_fn is not None and cancel_fn():
        return []

    if first_error and errors is None:
        raise first_error[0]

    results = []
    for idx in sorted(results_map):
        results.extend(results_map[idx])

    return results


def cpva_decode_value(sample: dict):
    val = sample.get("value")

    if val is None:
        return None

    if isinstance(val, (int, float, str)):
        return val

    if isinstance(val, list):
        if len(val) == 1:
            return val[0]

        # Try ASCII decode only for shorter lists
        if len(val) <= 512 and val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(map(chr, val))
                if decoded.strip():
                    return decoded
            except Exception:
                pass

        return val

    return val


def cpva_decode_units(sample: dict) -> str:
    """Extract the engineering units string from a sample."""
    return (sample.get("metaData") or {}).get("units", "") or ""


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


def ns_to_prague(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)


def ns_to_local_str(ts_ns: int) -> str:
    sec = ts_ns // 1_000_000_000
    ms  = (ts_ns % 1_000_000_000) // 1_000_000
    dt  = datetime.fromtimestamp(sec)
    return f"{dt:%Y-%m-%d %H:%M:%S}.{ms:03d}"


def ns_to_prague_str(ts_ns: int) -> str:
    return ns_to_prague(ts_ns).strftime("%Y-%m-%d %H:%M:%S")


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
    if ":" in full_name:
        device_part, field_part = full_name.split(":", 1)
    else:
        device_part, field_part = full_name, ""

    device = _STRIP_PATTERNS.sub("_", device_part)
    device = re.sub(r"_+", "_", device).strip("_")

    field = field_part.split(".")[0] if field_part else ""

    if device and field:
        return f"{device} - {field}"
    return device or field or full_name


# ---------------------------------------------------------------------------
# Wildcard filter
# ---------------------------------------------------------------------------

def _matches_wildcard(text: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if not pattern:
        return True
    tl = text.lower()
    pl = pattern.lower()
    if "*" in pl:
        return fnmatch.fnmatch(tl, pl)
    return all(t in tl for t in pl.split())
