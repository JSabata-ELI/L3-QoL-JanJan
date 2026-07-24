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
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# The archiver only returns reliable data when the query window <= 1 h.
CHUNK_SIZE_NS = int(3600 * 1e9)   # 1 hour in nanoseconds

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

def _http_get_json(url: str, timeout: float = CPVA_HTTP_TIMEOUT):
    resp = _SESSION.get(
        url,
        timeout=timeout,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return json.loads(resp.content)


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


def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 12,
                               progress_fn=None) -> list[dict]:
    """Fetch a channel's samples in parallel time-chunks.

    progress_fn, if given, is called as progress_fn(done, total) after each
    chunk completes so callers can drive a progress bar.
    """
    chunks = []
    cs = start_ns
    i = 0

    while cs < end_ns:
        ce = min(cs + CHUNK_SIZE_NS, end_ns)
        chunks.append((i, cs, ce))
        i += 1
        cs = ce

    if not chunks:
        if progress_fn:
            progress_fn(0, 0)
        return []

    if len(chunks) == 1:
        out = cpva_fetch_samples(channel, chunks[0][1], chunks[0][2], timeout)
        if progress_fn:
            progress_fn(1, 1)
        return out

    if log_fn:
        log_fn(f"      {channel}: {len(chunks)} chunks")

    total = len(chunks)
    workers = min(max_workers, total)
    results_map = {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): idx
            for idx, cs, ce in chunks
        }
        done = 0
        for fut in as_completed(futures):
            idx = futures[fut]
            results_map[idx] = fut.result()
            done += 1
            if progress_fn:
                progress_fn(done, total)

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
