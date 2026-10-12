"""Laser settings — reading numbers out of the archiver.

Stdlib only, and no Qt: this runs on worker threads and is exercised from
headless tests.

Copied from `Announcer/ann_cpva.py`, whose HTTP half in turn comes from
`Image Tools/cpva_client.py` — the connection pool, the one retry policy, and
the `CpvaError` / `CpvaBusyError` split. The repo keeps no shared library, so a
module has exactly one home and this is a deliberate copy. Announcer's three
reductions are left behind: this program asks one question only, and it is
always the same one.

THE ONE QUESTION
----------------
"What is this channel holding right now?" Every channel here is a setting, a
state or a switch — a hall's shooting flag, a delay, a voltage — and the
archiver writes on change only. A channel holding its value perfectly publishes
nothing for hours, so the newest sample is found with a widening look-back
(`LastReader`) and then held forward. A quiet channel is a healthy channel.

TWO THINGS AN EMPTY ANSWER DOES NOT MEAN
----------------------------------------
An empty sample list is a valid, successful answer: the archiver holds nothing
for that channel at all. A failure raises. Both are different from a number, and
neither may ever be judged as one — a dead archiver must not look like a row of
green ticks. Every reading therefore carries its own `error`, and a reading with
an error is never judged.

Staleness here is about the age of our last successful READ, never about the age
of the value.
"""

from __future__ import annotations

import http.client
import json
import queue
import socket
import ssl
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# ── the archiver ─────────────────────────────────────────────────────────────
CPVA_BASE_URL  = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_HOST      = CPVA_BASE_URL.split("://", 1)[1].split("/")[0]
CPVA_BASE_PATH = "/" + CPVA_BASE_URL.split("://", 1)[1].split("/", 1)[1]

# This program only ever asks for the newest sample of one channel, which the
# archiver answers in well under a second. The timeout is what bounds a whole
# pass, so it is generous enough not to fail a busy moment and short enough that
# a pass cannot outlive the operator's patience.
DEFAULT_TIMEOUT = 4.0

# The archiver publishes about a second late, so the newest sample of "now" is
# usually a second old. Windows are widened at the back rather than the front.
PUBLISH_LAG_NS = 2_000_000_000

# How far back from a channel's newest sample `Reading.recent_ns` reaches. The
# shot counter only needs the samples since the previous pass; two minutes
# covers a pass that was late or written off by the watchdog.
RECENT_S = 120

_POOL_SIZE = 8
# The longest one caller can hold a slot: two attempts plus the retry sleep.
_MAX_HOLD_S  = 2 * DEFAULT_TIMEOUT + 1.0
# A waiter's patience must be sized against how long another caller can hold a
# slot, never against its own query timeout. Getting that backwards is what made
# a saturated pool look like a broken channel in the sibling program.
_POOL_WAIT_S = _MAX_HOLD_S + 5.0

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_pool: "queue.LifoQueue" = queue.LifoQueue(maxsize=_POOL_SIZE)
for _ in range(_POOL_SIZE):
    _pool.put(None)          # slots start empty; connections are made lazily
del _


class CpvaError(Exception):
    """A failure talking to the archiver. NEVER means "no data"."""


class CpvaBusyError(CpvaError):
    """Our OWN pool was full, so the request never left this PC.

    Kept apart because it says nothing at all about the channel: telling the
    operator a chiller is broken because another read was still in flight would
    be a lie.
    """


# ── counters, so "sometimes it is not read" becomes a number ─────────────────
STATS = {"requests": 0, "http_errors": 0, "timeouts": 0, "busy": 0,
         "dropped": 0, "pool_wait_max_s": 0.0, "passes": 0, "abandoned": 0}
_stats_lock = threading.Lock()


def _bump(key, n=1):
    with _stats_lock:
        STATS[key] = STATS.get(key, 0) + n


def _peak_stat(key, v):
    with _stats_lock:
        if v > STATS.get(key, 0.0):
            STATS[key] = v


def stats_line():
    """One line for the log. Cheap, safe from a timer."""
    with _stats_lock:
        s = dict(STATS)
    return (f"archiver passes={int(s['passes'])} req={int(s['requests'])} "
            f"httpErr={int(s['http_errors'])} timeout={int(s['timeouts'])} "
            f"busy={int(s['busy'])} abandoned={int(s['abandoned'])} "
            f"dropped={int(s['dropped'])} poolWaitMax={s['pool_wait_max_s']:.1f}s")


def _close_quiet(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _request_json(path_qs, timeout):
    """GET on a pooled keep-alive connection, parsed.

    Retries once on a transport error (an idle keep-alive socket closed by the
    server is the ordinary case) and once on a 5xx. A 4xx and a timeout raise
    straight away — a retry would only double an already long stall.
    """
    full_path = CPVA_BASE_PATH + path_qs
    waited = time.monotonic()
    try:
        conn = _pool.get(timeout=_POOL_WAIT_S)
    except queue.Empty:
        _bump("busy")
        _peak_stat("pool_wait_max_s", time.monotonic() - waited)
        raise CpvaBusyError("every connection to the archiver was already in use")
    _peak_stat("pool_wait_max_s", time.monotonic() - waited)
    _bump("requests")
    try:
        for attempt in range(2):
            if conn is None:
                conn = http.client.HTTPSConnection(CPVA_HOST, timeout=timeout,
                                                   context=_ssl_ctx)
            else:
                conn.timeout = timeout
            try:
                conn.request("GET", full_path, headers={"Accept": "application/json"})
                resp = conn.getresponse()
                body = resp.read()
                if resp.status >= 500:
                    if attempt == 0:
                        time.sleep(0.3)
                        continue
                    _bump("http_errors")
                    raise CpvaError(f"HTTP {resp.status}")
                if resp.status >= 400:
                    _bump("http_errors")
                    raise CpvaError(f"HTTP {resp.status}")
                try:
                    return json.loads(body.decode("utf-8"))
                except ValueError as exc:
                    raise CpvaError(f"bad JSON from archiver: {exc}") from exc
            except CpvaError:
                raise
            except socket.timeout as exc:
                _close_quiet(conn)
                conn = None
                _bump("timeouts")
                raise CpvaError(f"timeout after {timeout:g} s") from exc
            except Exception as exc:
                _close_quiet(conn)
                conn = None
                if attempt == 1:
                    raise CpvaError(f"transport error: {exc}") from exc
        raise CpvaError("unreachable retry state")
    finally:
        _pool.put(conn)


# ── the one fetch ────────────────────────────────────────────────────────────

def fetch_samples(channel, start_ns, end_ns, *, timeout=DEFAULT_TIMEOUT):
    """Every archived sample of one channel in one window, as (t_ns, value).

    Sorted, oldest first. An empty list means the archiver holds nothing for
    that window — a success. Anything else raises `CpvaError`.
    """
    qs = urllib.parse.urlencode({"channelName": channel,
                                 "start": str(int(start_ns)),
                                 "end": str(int(end_ns))})
    raw = _request_json(f"/samples?{qs}", timeout)
    return parse_samples(raw if isinstance(raw, list) else [])


def parse_samples(raw):
    """Raw archiver rows as a sorted (t_ns, float) list.

    A `value` that arrives as a list is an array channel; its first element is
    the number. An unparseable sample is skipped and counted, so a channel whose
    samples are systematically unreadable shows up as a number in the log rather
    than reading "no value" for ever.
    """
    out = []
    for s in raw:
        val = s.get("value")
        if isinstance(val, list):
            val = val[0] if val else None
        try:
            out.append((int(s.get("time")), float(val)))
        except (TypeError, ValueError):
            _bump("dropped")
            continue
    out.sort(key=lambda x: x[0])
    return out


def now_ns():
    return int(time.time() * 1e9)


# ── every channel the archiver knows ─────────────────────────────────────────
_channels = None
_channels_lock = threading.Lock()


def fetch_channels(*, timeout=10.0, refresh=False):
    """Every channel name the archiver holds. Raises `CpvaError`.

    Measured 2026-09-16: 9744 names in 0.02 s, so it is cheap — but it is held
    for the session anyway, because a name picker asks for it on every keystroke
    and the list does not change while the program is open.
    """
    global _channels
    with _channels_lock:
        if _channels is not None and not refresh:
            return _channels
    raw = _request_json("/channels", timeout)
    names = sorted(str(n) for n in raw if isinstance(n, str)) \
        if isinstance(raw, list) else []
    with _channels_lock:
        _channels = names
    return names


# ── the newest value of a channel written on change only ─────────────────────

class LastReader:
    """Finds the newest sample of a channel that is written on change only.

    Windows widen until one of them answers, and the window that worked is
    remembered per channel so the next read starts there. The memory only ever
    widens: a narrower window is a subset of a wider one, so once the remembered
    one comes back empty there is nothing a smaller one could still hold. And
    starting wide is never wrong — the newest sample of a wide window is the
    same sample.

    Without the memory a channel that is not archived at all costs a request for
    every window, on every pass, for ever. Every channel in this program is a
    setting or a state, so this is the only way anything is read here.

    THE WINDOWS GO BACK A YEAR, and that is not generosity. The halls' gate
    valves are the worst case in the program: a safety gate valve that has not
    been touched since the summer publishes nothing at all in the last month,
    and a month was the old limit — the square would have sat grey for ever
    while the valve was perfectly, readably closed. The cost of the extra steps
    is paid once per channel, because the window that answered is remembered.
    """

    STEPS_S = (3600, 6 * 3600, 86400, 7 * 86400, 30 * 86400,
               90 * 86400, 365 * 86400)

    def __init__(self):
        self._hint = {}
        self._lock = threading.Lock()

    def read(self, channel, end_ns=None, *, timeout=DEFAULT_TIMEOUT):
        """(value, t_ns, recent) for the newest sample, or (None, None, ()).

        `recent` is the timestamps of every sample in the last `RECENT_S`,
        oldest first. The window fetched is at least an hour, so they are
        already in hand: the shot counter counts EVERY sample between two
        passes from them, at no extra request. Raises CpvaError.
        """
        end_ns = end_ns if end_ns is not None else now_ns()
        with self._lock:
            start_at = self._hint.get(channel, 0)
        for i in range(start_at, len(self.STEPS_S)):
            span = self.STEPS_S[i] * 1_000_000_000
            samples = fetch_samples(channel, end_ns - span, end_ns, timeout=timeout)
            if samples:
                with self._lock:
                    self._hint[channel] = i
                newest = samples[-1][0]
                cut = newest - int(RECENT_S * 1_000_000_000)
                recent = tuple(t for t, _v in samples if t >= cut)
                return samples[-1][1], newest, recent
        with self._lock:
            self._hint[channel] = len(self.STEPS_S) - 1
        return None, None, ()

    def forget(self, channel=None):
        """Start over. Used when the operator edits a channel name."""
        with self._lock:
            if channel is None:
                self._hint.clear()
            else:
                self._hint.pop(channel, None)


# ── one reading ──────────────────────────────────────────────────────────────

class Reading:
    """What one pass learned about one channel.

    `error` is set ONLY when the read failed. Nothing archived at all leaves
    `error` None and `value` None, which is a different sentence on screen and a
    different thing for the operator to do about it.
    """
    __slots__ = ("value", "newest_ns", "error", "hint", "recent_ns")

    def __init__(self, value=None, newest_ns=None, error=None, hint=None,
                 recent_ns=()):
        self.value = value
        self.newest_ns = newest_ns
        self.error = error
        self.hint = hint
        # The timestamps of every sample in the last `RECENT_S`, oldest first.
        # What the shot counter counts; nothing else looks at it.
        self.recent_ns = tuple(recent_ns or ())

    @property
    def ok(self):
        return self.error is None

    @property
    def has_value(self):
        return self.error is None and self.value is not None

    def __repr__(self):
        return (f"Reading(value={self.value!r}, newest_ns={self.newest_ns!r}, "
                f"error={self.error!r})")


def read_channel(channel, *, last_reader=None, end_ns=None,
                 timeout=DEFAULT_TIMEOUT, readable_error=None):
    """The value one channel is holding now, as a `Reading`. Never raises."""
    channel = (channel or "").strip()
    if not channel:
        return Reading(error="no channel set")
    end_ns = end_ns if end_ns is not None else now_ns()
    end_ns += PUBLISH_LAG_NS          # the archiver's clock is behind ours
    reader = last_reader or LastReader()
    try:
        value, t_ns, recent = reader.read(channel, end_ns, timeout=timeout)
    except CpvaError as exc:
        if readable_error is not None:
            msg, hint = readable_error(channel, exc)
            return Reading(error=msg, hint=hint)
        return Reading(error=f"{channel} — {exc}")
    return Reading(value, t_ns, recent_ns=recent)


class PassReader:
    """Reads a whole list of channels at once, pass after pass.

    One long-lived thread pool, not one per pass: a pass happens every second
    for as long as the program runs, and building six threads each time would be
    most of the work. It also holds the one `LastReader`, so the memory of which
    look-back window answered survives across passes — which is the whole point
    of having it.

    The reads run side by side, because twenty of them one after another would
    take longer than the interval they are read on.
    """

    def __init__(self, workers=6, timeout=DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.last_reader = LastReader()
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(workers)),
                                        thread_name_prefix="cpva")

    def read_many(self, channels, *, end_ns=None, deadline_s=None,
                  readable_error=None, cancelled=None):
        """{channel: Reading} for every distinct channel name given.

        Distinct, because the same channel may be watched twice — two halls can
        share a switch — and reading it twice a pass would be a request for
        nothing.

        `deadline_s` bounds the WHOLE pass. Anything still outstanding when it
        expires is reported as not read and written off: an HTTP read in a
        thread cannot be cancelled, so it is abandoned rather than waited for.
        That bound is what stops one unreachable channel from stretching a pass
        to the sum of every timeout, and an abandoned read is counted so
        "sometimes a value is missing" becomes a number in the log.
        """
        wanted = []
        for name in channels:
            name = (name or "").strip()
            if name and name not in wanted:
                wanted.append(name)
        if not wanted:
            return {}
        _bump("passes")
        end_ns = end_ns if end_ns is not None else now_ns()
        out = {}
        started = time.monotonic()
        futures = {}
        for name in wanted:
            if cancelled is not None and cancelled():
                break
            fut = self._pool.submit(read_channel, name,
                                    last_reader=self.last_reader,
                                    end_ns=end_ns, timeout=self.timeout,
                                    readable_error=readable_error)
            futures[fut] = name
        pending = set(futures)
        while pending:
            left = None
            if deadline_s is not None:
                left = deadline_s - (time.monotonic() - started)
                if left <= 0:
                    break
            done, pending = wait(pending, timeout=left,
                                 return_when=FIRST_COMPLETED)
            if not done:
                break                     # the deadline ran out
            for fut in done:
                name = futures[fut]
                try:
                    out[name] = fut.result()
                except Exception as exc:  # never let one read kill a pass
                    out[name] = Reading(error=f"read failed: {exc}")
        for fut in pending:
            out[futures[fut]] = Reading(
                error="not read in time — the archiver did not answer this pass")
            _bump("abandoned")
            fut.cancel()
        return out

    def forget(self, channel=None):
        self.last_reader.forget(channel)

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


