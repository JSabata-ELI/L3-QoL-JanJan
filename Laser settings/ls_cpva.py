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

TWO ARCHIVES
------------
Most channels live on the CPVA archiver (10.78.0.57). The L3 beam transport and
the experimental halls (L3BT-…, E3-…, E4-…, E5-…, ETS-…) live in a second one,
reached through the L3BT proxy on 10.78.0.64 and its twin 10.78.0.65 — the same
Cassandra rows in the same JSON, behind a different URL shape. Measured
15.10.2026: 4336 names there, 196 of them also on the CPVA archiver with
identical samples. The gate valves SGV502 to SGV505 publish ONLY there
(`L3BT-MSS:SGV50x_OPEN_IN` / `_CLOSED_IN`).

A channel is read from the proxy when the proxy lists it and the CPVA archiver
does not; everything else goes where it always went. `.64` is asked first and
`.65` only when `.64` cannot be reached.

THE BACKUP FOR THE CPVA ARCHIVER
--------------------------------
The CPVA archiver is only a front end: the L3 proxy on the same two hosts
(`/l3proxy/...`) serves the very same L3 Cassandra — measured 15.10.2026, an
identical channel list (9747 names), identical rows, and the same speed. So
when the CPVA service cannot be reached at all (connection refused or reset,
HTTP 502/503/504), the same question is asked there instead, and for the next
`CPVA_DOWN_S` seconds every CPVA question goes straight to it before the
archiver is tried again.

It is NOT asked on a timeout (the shared database is slow, the twin would be
just as slow) nor on any other HTTP error (that is an answer).
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

# ── the L3BT proxy: the second archive ───────────────────────────────────────
# Plain HTTP. Two hosts serving the same archive; the first is asked first.
PROXY_HOSTS     = ("10.78.0.64:8080", "10.78.0.65:8080")
PROXY_BASE_PATH = "/l3btproxy/archive-access/api/1.0/archive/1"
# The FCT proxy: facility channels (cooling, power, PSI, leak detection, …),
# 7636 names on 15.10.2026, 48 of them also on the CPVA archiver. Same hosts,
# same URL shape.
FCT_PROXY_BASE_PATH = "/fctproxy/archive-access/api/1.0/archive/1"
# The archives beside the CPVA archiver, in the order a name is looked up.
EXTRA_ARCHIVES = (PROXY_BASE_PATH, FCT_PROXY_BASE_PATH)
# Answers in hundredths of a second (measured 0.02–0.2 s), so a short timeout
# loses nothing and keeps a dead host from eating the pass before the twin is tried.
PROXY_TIMEOUT   = 3.0
# How long a failed channel listing is left alone before it is asked again.
_LIST_RETRY_S   = 30.0

# ── the L3 proxy: the backup for the CPVA archiver ───────────────────────────
# Same hosts, same URL shape as the L3BT proxy, but it holds the L3 archive the
# CPVA archiver itself reads. Asked only while the archiver cannot be reached.
L3_PROXY_BASE_PATH = "/l3proxy/archive-access/api/1.0/archive/1"
# How long the archiver is left alone after it could not be reached.
CPVA_DOWN_S = 60.0
# The answers that mean "the service is not there", not "your question was bad".
_UNREACHABLE_HTTP = (502, 503, 504)
# A proxy's whole channel list: the pattern is a path segment there, `*` = all.
_ALL_CHANNELS_PATH = "/channels-by-pattern/" + urllib.parse.quote("*", safe="")


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
         "dropped": 0, "pool_wait_max_s": 0.0, "passes": 0, "abandoned": 0,
         "backup": 0}
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
            f"dropped={int(s['dropped'])} backup={int(s['backup'])} "
            f"poolWaitMax={s['pool_wait_max_s']:.1f}s")


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
                    err = CpvaError(f"HTTP {resp.status}")
                    err.unreachable = resp.status in _UNREACHABLE_HTTP
                    raise err
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
                # A connection that never opened is a host that is not there; a
                # slow answer on an open one is the database, and the backup
                # reads the same database.
                never_opened = conn.sock is None
                _close_quiet(conn)
                conn = None
                _bump("timeouts")
                err = CpvaError(f"timeout after {timeout:g} s")
                err.unreachable = never_opened
                raise err from exc
            except Exception as exc:
                _close_quiet(conn)
                conn = None
                if attempt == 1:
                    err = CpvaError(f"transport error: {exc}")
                    err.unreachable = True
                    raise err from exc
        raise CpvaError("unreachable retry state")
    finally:
        _pool.put(conn)


_cpva_down_until = 0.0
_down_lock = threading.Lock()


def _cpva_is_down():
    with _down_lock:
        return time.monotonic() < _cpva_down_until


def _mark_cpva_down():
    global _cpva_down_until
    with _down_lock:
        _cpva_down_until = time.monotonic() + CPVA_DOWN_S


def _cpva_or_backup(path_qs, backup_path, timeout):
    """The CPVA archiver, or the L3 proxy when the archiver cannot be reached.

    `backup_path` is the same question in the proxy's URL shape. The backup gets
    the caller's whole timeout: it reads the same database, just as slowly.
    """
    first = None
    if not _cpva_is_down():
        try:
            return _request_json(path_qs, timeout)
        except CpvaBusyError:
            raise
        except CpvaError as exc:
            if not getattr(exc, "unreachable", False):
                raise
            _mark_cpva_down()
            first = exc
    _bump("backup")
    try:
        return _proxy_request_json(backup_path, timeout, base=L3_PROXY_BASE_PATH)
    except CpvaError as exc:
        # An HTTP error from the proxy is the archive's own answer: raised as it
        # is. Only "the proxy is not there either" names both.
        if first is None or not getattr(exc, "transport", False):
            raise
        raise CpvaError(f"archiver unreachable ({first}); "
                        f"backup L3 proxy: {exc}") from exc


# One small keep-alive pool per proxy and host. Few channels live on the L3BT
# proxy (the gate valves), so four connections each is plenty; the L3 proxy
# stands in for the whole archiver, so it gets the archiver's pool size.
_PROXY_POOL_SIZE = {PROXY_BASE_PATH: 4, FCT_PROXY_BASE_PATH: 4,
                    L3_PROXY_BASE_PATH: _POOL_SIZE}
_proxy_pools = {}
for _base, _size in _PROXY_POOL_SIZE.items():
    for _host in PROXY_HOSTS:
        _proxy_pools[(_base, _host)] = queue.LifoQueue(maxsize=_size)
        for _ in range(_size):
            _proxy_pools[(_base, _host)].put(None)
del _base, _size, _host, _


def _proxy_get_one(host, path, timeout, base=PROXY_BASE_PATH):
    """One GET on one proxy host. Raises CpvaError; `transport` is set on it when
    the host itself could not be reached, which is what sends us to the twin."""
    pool = _proxy_pools[(base, host)]
    try:
        conn = pool.get(timeout=_POOL_WAIT_S)
    except queue.Empty:
        _bump("busy")
        raise CpvaBusyError("every connection to the proxy was already in use")
    _bump("requests")
    try:
        for attempt in range(2):
            if conn is None:
                conn = http.client.HTTPConnection(host, timeout=timeout)
            else:
                conn.timeout = timeout
            try:
                conn.request("GET", base + path,
                             headers={"Accept": "application/json"})
                resp = conn.getresponse()
                body = resp.read()
                if resp.status >= 400:
                    _bump("http_errors")
                    raise CpvaError(f"HTTP {resp.status} from {host}")
                try:
                    return json.loads(body.decode("utf-8"))
                except ValueError as exc:
                    raise CpvaError(f"bad JSON from {host}: {exc}") from exc
            except CpvaError:
                raise
            except socket.timeout as exc:
                _close_quiet(conn)
                conn = None
                _bump("timeouts")
                err = CpvaError(f"timeout after {timeout:g} s on {host}")
                err.transport = True
                raise err from exc
            except Exception as exc:
                _close_quiet(conn)
                conn = None
                if attempt == 1:
                    err = CpvaError(f"transport error on {host}: {exc}")
                    err.transport = True
                    raise err from exc
        raise CpvaError("unreachable retry state")
    finally:
        pool.put(conn)


def _proxy_request_json(path, timeout=PROXY_TIMEOUT, base=PROXY_BASE_PATH):
    """GET on the proxy: the first host, and the twin only when the first one
    cannot be reached. An HTTP error is an answer, so it is not asked twice."""
    last = None
    for host in PROXY_HOSTS:
        try:
            return _proxy_get_one(host, path, timeout, base)
        except CpvaBusyError as exc:
            last = exc
        except CpvaError as exc:
            if not getattr(exc, "transport", False):
                raise
            last = exc
    raise last


# ── which archive holds a channel ────────────────────────────────────────────
# The channel lists are cheap (0.02 s each) and held for the session. A channel
# goes to a proxy only when that proxy lists it AND the CPVA archiver does not —
# the names both hold (196 on L3BT, 48 on FCT) stay on the CPVA archiver, where
# they always were.
_cpva_names = None
_proxy_names = {}           # proxy base path → its names, once listed
_lists_tried_at = 0.0
_route_lock = threading.Lock()
# Held for the whole load, so the reads of the first pass WAIT for the lists
# instead of all going to the CPVA archiver while the first one is fetching.
_load_lock = threading.Lock()


def _load_lists(timeout=6.0, force=False):
    """Fill the name lists that are not held yet. Never raises."""
    with _load_lock:
        _load_lists_locked(timeout, force)


def _load_lists_locked(timeout, force):
    global _cpva_names, _proxy_names, _lists_tried_at
    with _route_lock:
        complete = (_cpva_names is not None
                    and all(b in _proxy_names for b in EXTRA_ARCHIVES))
        if not force and complete:
            return
        if not force and _lists_tried_at \
                and time.monotonic() - _lists_tried_at < _LIST_RETRY_S:
            return
        _lists_tried_at = time.monotonic()
    if force or _cpva_names is None:
        try:
            raw = _cpva_or_backup("/channels", _ALL_CHANNELS_PATH, timeout)
            names = {str(n) for n in raw if isinstance(n, str)} \
                if isinstance(raw, list) else set()
            with _route_lock:
                _cpva_names = names
        except Exception:
            pass
    for base in EXTRA_ARCHIVES:
        if not force and base in _proxy_names:
            continue
        try:
            raw = _proxy_request_json(_ALL_CHANNELS_PATH, timeout, base=base)
            names = {str(n) for n in raw if isinstance(n, str)} \
                if isinstance(raw, list) else set()
            with _route_lock:
                _proxy_names[base] = names
        except Exception:
            pass


def archive_of(channel):
    """The proxy base path this channel is read from, or None for the CPVA
    archiver (and its backup)."""
    _load_lists()
    with _route_lock:
        lists, cpva = dict(_proxy_names), _cpva_names
    # Without the CPVA list nobody can say the name is ALSO there; the proxies
    # answer the shared names with identical samples, so it is safe.
    if cpva is not None and channel in cpva:
        return None
    for base in EXTRA_ARCHIVES:
        if channel in lists.get(base, ()):
            return base
    return None


def on_proxy(channel):
    """True when this channel is read from one of the extra proxies."""
    return archive_of(channel) is not None


# ── the one fetch ────────────────────────────────────────────────────────────

def fetch_samples(channel, start_ns, end_ns, *, timeout=DEFAULT_TIMEOUT):
    """Every archived sample of one channel in one window, as (t_ns, value).

    Sorted, oldest first. An empty list means the archiver holds nothing for
    that window — a success. Anything else raises `CpvaError`.

    `value` is None for a sample that carries no value — the archive's
    "Disconnected" rows. They are kept, not skipped: the newest sample being one
    of those means the channel is NOT publishing now, and skipping it would hold
    an old position forward as if it were current.
    """
    qs = urllib.parse.urlencode({"start": str(int(start_ns)),
                                 "end": str(int(end_ns))})
    proxy_path = f"/samples/{urllib.parse.quote(channel, safe='')}?{qs}"
    base = archive_of(channel)
    if base is not None:
        raw = _proxy_request_json(proxy_path, min(timeout, PROXY_TIMEOUT),
                                  base=base)
    else:
        cpva_qs = urllib.parse.urlencode({"channelName": channel,
                                          "start": str(int(start_ns)),
                                          "end": str(int(end_ns))})
        raw = _cpva_or_backup(f"/samples?{cpva_qs}", proxy_path, timeout)
    return parse_samples(raw if isinstance(raw, list) else [])


def parse_samples(raw):
    """Raw archiver rows as a sorted (t_ns, float or None) list.

    A `value` that arrives as a list is an array channel; its first element is
    the number. A row the archive marks as holding no value (severity
    `hasValue: false` — "Disconnected") becomes (t, None). Any other
    unparseable sample is skipped and counted, so a channel whose samples are
    systematically unreadable shows up as a number in the log rather than
    reading "no value" for ever.
    """
    out = []
    for s in raw:
        sev = s.get("severity")
        if isinstance(sev, dict) and sev.get("hasValue") is False:
            try:
                out.append((int(s.get("time")), None))
            except (TypeError, ValueError):
                _bump("dropped")
            continue
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

    Measured 2026-09-16: 9744 names in 0.02 s, so it is cheap. It is held so
    repeated questions are answered from memory, but `refresh=True` asks again:
    the list DOES change while the program is open (SPIDER GDD, TPD and FOG were
    added mid-session on 2026-10-13), so a picker refreshes on every opening.
    A refresh that fails falls back to the list already held.
    """
    global _channels
    with _channels_lock:
        if _channels is not None and not refresh:
            return _channels
    try:
        raw = _cpva_or_backup("/channels", _ALL_CHANNELS_PATH, timeout)
    except Exception:
        with _channels_lock:
            if _channels is not None:
                return _channels
        raise
    names = {str(n) for n in raw if isinstance(n, str)} \
        if isinstance(raw, list) else set()
    # The L3BT and FCT proxies' names too, so their channels can be picked. A
    # proxy that does not answer leaves the list as it is.
    _load_lists(timeout=timeout, force=True)
    with _route_lock:
        for extra in _proxy_names.values():
            names |= extra
    names = sorted(names)
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
        """(value, t_ns, recent, recent_v) for the newest sample, or
        (None, None, (), ()).

        `recent` is the timestamps of every sample in the last `RECENT_S`,
        oldest first, and `recent_v` their values. The window fetched is at
        least an hour, so they are already in hand: the shot counter counts
        EVERY sample between two passes from them, and the SBW4 watch tells a
        lone trigger from a low power stream, at no extra request. Raises
        CpvaError.
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
                # A "Disconnected" row is not a sample of the value, so the shot
                # counter must not count it.
                kept = [(t, v) for t, v in samples
                        if t >= cut and v is not None]
                return (samples[-1][1], newest, tuple(t for t, _v in kept),
                        tuple(v for _t, v in kept))
        with self._lock:
            self._hint[channel] = len(self.STEPS_S) - 1
        return None, None, (), ()

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
    __slots__ = ("value", "newest_ns", "error", "hint", "recent_ns",
                 "recent_v")

    def __init__(self, value=None, newest_ns=None, error=None, hint=None,
                 recent_ns=(), recent_v=()):
        self.value = value
        self.newest_ns = newest_ns
        self.error = error
        self.hint = hint
        # The timestamps of every sample in the last `RECENT_S`, oldest first.
        # What the shot counter counts.
        self.recent_ns = tuple(recent_ns or ())
        # Their values, same order. What `ls_core.FiringWatch` reads to tell a
        # lone trigger from a stream of low power shots.
        self.recent_v = tuple(recent_v or ())

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
        value, t_ns, recent, recent_v = reader.read(channel, end_ns,
                                                    timeout=timeout)
    except CpvaError as exc:
        if readable_error is not None:
            msg, hint = readable_error(channel, exc)
            return Reading(error=msg, hint=hint)
        return Reading(error=f"{channel} — {exc}")
    if value is None and t_ns is not None:
        # The newest sample is a "Disconnected" row: the channel is archived but
        # not publishing now. That is a failure to read, not "nothing archived".
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(t_ns / 1e9))
        return Reading(newest_ns=t_ns,
                       error=f"{channel} — disconnected since {when}")
    return Reading(value, t_ns, recent_ns=recent, recent_v=recent_v)


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


