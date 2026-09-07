"""
spfe_core.py  --  CPVA archiver access for the SPFE Values log.

A trimmed copy of the fetch layer of ``CSS Logger/cpva_core.py``, keeping the
function names identical so that when this program becomes a CSS Logger tab the
tab module can import them from ``cpva_core`` instead and nothing else changes.

The repository has no shared library folder on purpose (INFRASTRUCTURE.md 7):
a module has exactly one home, the folder of the program that ships it. This is
therefore a deliberate copy, not an oversight.

No GUI toolkit is imported here, so it is safe to use from headless tests.
"""
from __future__ import annotations

import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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


APP_DIR = get_app_dir()


# ---------------------------------------------------------------------------
# CPVA archiver API constants
# ---------------------------------------------------------------------------

CPVA_BASE_URL         = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT = "/samples"
CPVA_HTTP_TIMEOUT     = 10.0

# The archiver only returns reliable data when the query window <= 1 h.
CHUNK_SIZE_NS = int(3600 * 1e9)

_SESSION = requests.Session()
_SESSION.verify = False          # the archiver's certificate is self-signed
# The fetch pool runs several requests at once; requests' default adapter keeps
# only 10 pooled connections and silently reopens a full TLS handshake above
# that. Size the pool above the worker cap.
from requests.adapters import HTTPAdapter as _HTTPAdapter   # noqa: E402
_ADAPTER = _HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://", _ADAPTER)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):
    resp = _SESSION.get(url, timeout=timeout, headers={"Accept": "application/json"})
    resp.raise_for_status()
    return orjson.loads(resp.content)


def cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                       timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    """Every archived sample of one channel in [start_ns, end_ns]."""
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data


def _chunk_is_night(chunk_start_ns: int, chunk_end_ns: int) -> bool:
    """True when the whole chunk lies in 22:00-06:00 Prague -- nothing is archived then.

    A chunk reaching within a minute of now is never called night, so a late
    evening catch-up still asks for the current hour.
    """
    now_val = now_ns()
    if chunk_end_ns >= now_val - 60 * 1_000_000_000:
        return False
    dt_start = datetime.fromtimestamp(chunk_start_ns / 1e9, tz=TZ_PRAGUE)
    dt_end   = datetime.fromtimestamp(chunk_end_ns   / 1e9, tz=TZ_PRAGUE)

    def is_night(h: int) -> bool:
        return h >= 22 or h < 6

    return is_night(dt_start.hour) and is_night(dt_end.hour)


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 8) -> list[dict]:
    """Split the window into 1-hour pieces, drop the night ones, fetch in parallel.

    Reassembled in chunk order, so the result stays ascending in time.
    """
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
    results_map: dict[int, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): idx
            for idx, cs, ce in chunks
        }
        for fut in as_completed(futures):
            results_map[futures[fut]] = fut.result()

    results: list[dict] = []
    for idx in sorted(results_map):
        results.extend(results_map[idx])
    return results


# Cumulative look-back horizons (seconds) for hunting the most recent sample
# before a time. The NEW slice is scanned at each step (near -> far) and the
# first hit wins, so a PV with recent data costs one chunk and only a truly
# stale PV pays for the deep scan.
_LAST_BEFORE_STEPS_S = (3600, 6 * 3600, 24 * 3600,
                        3 * 24 * 3600, 7 * 24 * 3600, 30 * 24 * 3600)


def cpva_fetch_last_before(channel: str, before_ns: int,
                           timeout: float = CPVA_HTTP_TIMEOUT,
                           cancel_fn=None):
    """The most recent sample dict strictly before `before_ns`, or None.

    This is the function the whole program rests on: it answers "what did this
    PV read at 09:00" long after 09:00 has passed, which is why nothing has to
    be running at the time.
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
            return raw[-1]      # chunked results are ascending -> last is nearest
        hi = lo
        if lo == 0:
            break
    return None


def _is_cancelled(cancel_fn) -> bool:
    if cancel_fn is None:
        return False
    try:
        return bool(cancel_fn())
    except Exception:           # a broken predicate must not abort the fetch
        return False


def cpva_fetch_last_before_many(channels: list[str], before_ns: int,
                                timeout: float = CPVA_HTTP_TIMEOUT,
                                max_workers: int = 8,
                                progress_fn=None,
                                cancel_fn=None) -> dict[str, dict]:
    """Parallel :func:`cpva_fetch_last_before`.

    Returns ``{channel: sample_dict}`` only for channels that had a prior
    sample. A channel that raises is simply absent -- one dead PV must never
    cost the whole recording, which is the one behaviour deliberately not
    copied from Chiller Log.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, dict] = {}
    channels = [c for c in channels if c]
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
    """Archiver sample -> scalar. Number, string, one-element list, ASCII list."""
    val = sample.get("value")
    if val is None:
        return None
    if isinstance(val, (int, float, str)):
        return val
    if isinstance(val, list):
        if len(val) == 1:
            return val[0]
        if len(val) <= 512 and val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(map(chr, val))
                if decoded.strip():
                    return decoded
            except Exception:
                pass
        return val
    return val


# ---------------------------------------------------------------------------
# Time helpers -- everything on the wire is UTC nanoseconds, everything shown
# to a person is Prague.
# ---------------------------------------------------------------------------

def now_ns() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def dt_to_ns(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_PRAGUE)
    return int(dt.timestamp() * 1e9)


def ns_to_prague(ts_ns: int) -> datetime:
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone(TZ_PRAGUE)


def ns_to_local_str(ts_ns: int) -> str:
    return f"{ns_to_prague(ts_ns):%Y-%m-%d %H:%M:%S}"
