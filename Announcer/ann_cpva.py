"""Announcer — reading numbers out of the archiver.

Stdlib only, and no Qt: this runs on worker threads and is exercised from
headless tests.

The HTTP half is taken from `Image Tools/cpva_client.py` — the pool, the one
retry policy, and the `CpvaError` / `CpvaBusyError` split. It is COPIED rather
than imported, which is the rule in this repo for anything a frozen build would
otherwise have to carry across folders (`daypicker.py` lives in two programs the
same way). `Image Tools/cpva_client.py` is the master; only the small part
Announcer needs is here, and the whole-day cache it is mostly made of is left
behind — this program never asks for more than the last few seconds.

What it adds, which no sibling has, is Announcer's own three ways of turning a
window of samples into one number:

    peak   the worst sample in the window. One shot over the limit is the whole
           event, and an average of a minute of shots would hide it.
    mean   the average of the newest few samples. A single chiller sample
           crosses ±0.3 °C constantly; the average does not.
    last   the newest sample however old, found with a widening look-back. For a
           channel written on change only — a setpoint, a state, a valve.

TWO THINGS AN EMPTY ANSWER DOES NOT MEAN
----------------------------------------
An empty sample list is a valid, successful answer: the archiver holds nothing
for that window. A failure raises. The old Announcer returned `[]` for both,
which is how a dead archiver came to look exactly like eight values comfortably
in range. Every reading therefore carries its own `error`, and a reading with an
error is never judged.

And a value that has not changed is not a dead channel. The archiver writes on
change only, so a chiller holding its setpoint perfectly publishes nothing for
minutes. That is why `peak` falls back to the newest sample of the wider window,
and why staleness is about the age of our last successful READ, not about the
age of the value.
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

# Announcer only ever asks for seconds of one channel, which the archiver
# answers in well under a second. The timeout is what bounds a whole pass, so it
# is generous enough not to fail a busy moment and short enough that a pass
# cannot outlive the operator's patience.
DEFAULT_TIMEOUT = 4.0

# The archiver publishes about a second late, so the newest sample of "now" is
# usually a second old. Windows are widened at the back rather than the front.
PUBLISH_LAG_NS = 2_000_000_000

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


# ── the three reductions ─────────────────────────────────────────────────────

def reduce_peak(samples, since_ns):
    """The worst sample since `since_ns`, or the newest one we have.

    The fallback is not a shortcut. The archiver writes on change only, so a
    value that went high and then sat still produces nothing at all in the last
    ten seconds — and the newest sample of the wider window is the value the
    machine is STILL holding. Nothing at all means no judgement.
    """
    recent = [v for t, v in samples if t >= since_ns]
    if recent:
        return max(recent)
    return samples[-1][1] if samples else None


def reduce_mean(samples, count):
    """The average of the newest `count` samples."""
    if not samples:
        return None
    tail = samples[-int(count):] if count else samples
    return sum(v for _t, v in tail) / len(tail)


def reduce_last(samples):
    """The newest sample, whenever it was written."""
    return samples[-1][1] if samples else None


def hold_forward(samples, at_ns):
    """The value a channel was holding at `at_ns`.

    A window with no samples in it is never "no data" for a channel written on
    change only: the last value written before the window is what the machine
    was holding all the way through it.
    """
    value = None
    for t, v in samples:
        if t <= at_ns:
            value = v
        else:
            break
    if value is None and samples:
        # Every sample we have is newer than the moment asked about — the
        # oldest of them is the closest thing to an answer.
        value = samples[0][1]
    return value


def difference_series(a_samples, b_samples):
    """a − b as a series, with b held forward.

    A chiller's setpoint changes once a week, so a ten-second window of it is
    empty almost always. Subtracting "no value" would make the deviation
    unreadable exactly when the chiller is behaving; holding the setpoint
    forward gives the number the operator means.
    """
    out = []
    for t, v in a_samples:
        b = hold_forward(b_samples, t)
        if b is not None:
            out.append((t, v - b))
    return out


# ── the newest value of a sparse channel ─────────────────────────────────────

class LastReader:
    """Finds the newest sample of a channel that is written on change only.

    Windows widen until one of them answers, and the window that worked is
    remembered per channel so the next read starts there. The memory only ever
    widens: a narrower window is a subset of a wider one, so once the remembered
    one comes back empty there is nothing a smaller one could still hold. And
    starting wide is never wrong — the newest sample of a wide window is the
    same sample.

    Without the memory a channel that is not archived at all costs a request for
    every window, on every pass, for ever.
    """

    STEPS_S = (3600, 6 * 3600, 86400, 7 * 86400, 30 * 86400)

    def __init__(self):
        self._hint = {}
        self._lock = threading.Lock()

    def read(self, channel, end_ns=None, *, timeout=DEFAULT_TIMEOUT):
        """(value, t_ns) for the newest sample, or (None, None). Raises CpvaError."""
        end_ns = end_ns if end_ns is not None else now_ns()
        with self._lock:
            start_at = self._hint.get(channel, 0)
        for i in range(start_at, len(self.STEPS_S)):
            span = self.STEPS_S[i] * 1_000_000_000
            samples = fetch_samples(channel, end_ns - span, end_ns, timeout=timeout)
            if samples:
                with self._lock:
                    self._hint[channel] = i
                return samples[-1][1], samples[-1][0]
        with self._lock:
            self._hint[channel] = len(self.STEPS_S) - 1
        return None, None

    def forget(self, channel=None):
        """Start over. Used when the operator edits a channel name."""
        with self._lock:
            if channel is None:
                self._hint.clear()
            else:
                self._hint.pop(channel, None)


# ── one reading ──────────────────────────────────────────────────────────────

class Reading:
    """What one pass learned about one watched value.

    `error` is set ONLY when the read failed. An empty window leaves `error`
    None and `value` None, which is a different sentence on screen and a
    different thing for the operator to do about it.
    """
    __slots__ = ("value", "newest_ns", "samples", "error", "hint")

    def __init__(self, value=None, newest_ns=None, samples=0, error=None, hint=None):
        self.value = value
        self.newest_ns = newest_ns
        self.samples = samples
        self.error = error
        self.hint = hint

    @property
    def ok(self):
        return self.error is None

    def __repr__(self):
        return (f"Reading(value={self.value!r}, samples={self.samples}, "
                f"error={self.error!r})")


def read_value(item, *, last_reader=None, end_ns=None, timeout=DEFAULT_TIMEOUT,
               readable_error=None):
    """Read one value item and reduce it to one number.

    `item` is an `ann_core` value item. `readable_error` is `ann_core`'s
    translator, passed in so this module keeps no dependency on it.
    """
    end_ns = end_ns if end_ns is not None else now_ns()
    end_ns += PUBLISH_LAG_NS          # the archiver's clock is behind ours
    pv = (item.get("pv") or "").strip()
    if not pv:
        return Reading(error="no channel set")
    minus = (item.get("minus") or "").strip() or None
    mode = item.get("read") or "peak"
    window_s = float(item.get("window_s") or 10.0)

    # The fetch window is wider than the judged window on purpose: a channel
    # written on change only can be silent for a minute and still be holding a
    # value we must see.
    fetch_span_s = max(window_s, 60.0)
    start_ns = end_ns - int(fetch_span_s * 1e9)
    since_ns = end_ns - int(window_s * 1e9)

    try:
        if mode == "last" and not minus:
            reader = last_reader or LastReader()
            value, t_ns = reader.read(pv, end_ns, timeout=timeout)
            return Reading(value, t_ns, 1 if value is not None else 0)

        a = fetch_samples(pv, start_ns, end_ns, timeout=timeout)
        series = a
        if minus:
            b = fetch_samples(minus, start_ns, end_ns, timeout=timeout)
            if not b:
                # The setpoint has not been written in the last minute, which is
                # the normal state of a setpoint. Go and find it.
                reader = last_reader or LastReader()
                bv, bt = reader.read(minus, end_ns, timeout=timeout)
                b = [(bt, bv)] if bv is not None else []
            if not b:
                return Reading(error=f"{minus} — nothing archived to subtract")
            series = difference_series(a, b)
    except CpvaError as exc:
        if readable_error is not None:
            msg, hint = readable_error(pv, exc)
            return Reading(error=msg, hint=hint)
        return Reading(error=f"{pv} — {exc}")

    if not series:
        return Reading(samples=0)
    if mode == "mean":
        value = reduce_mean(series, item.get("mean_count") or 25)
    elif mode == "last":
        value = reduce_last(series)
    else:
        value = reduce_peak(series, since_ns)
    return Reading(value, series[-1][0], len(series))


class PassReader:
    """Reads a whole list of value items at once, pass after pass.

    One long-lived thread pool, not one per pass: a pass happens twice a second
    for as long as the program runs, and building six threads each time would be
    most of the work. It also holds the one `LastReader`, so the memory of which
    look-back window answered survives across passes — which is the whole point
    of having it.

    The reads run side by side, because fourteen of them one after another would
    take longer than the interval they are read on.
    """

    def __init__(self, workers=6, timeout=DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.last_reader = LastReader()
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(workers)),
                                        thread_name_prefix="cpva")

    def read_many(self, items, *, end_ns=None, deadline_s=None,
                  readable_error=None, cancelled=None):
        """{item id: Reading} for every item in the list.

        `deadline_s` bounds the WHOLE pass. Anything still outstanding when it
        expires is reported as not read and written off — an HTTP read in a
        thread cannot be cancelled, so it is abandoned rather than waited for,
        and its answer is simply never looked at. That bound is what stops one
        unreachable channel from stretching a pass to the sum of every timeout,
        and an abandoned read is counted so "sometimes a value is missing"
        becomes a number in the log.
        """
        if not items:
            return {}
        _bump("passes")
        end_ns = end_ns if end_ns is not None else now_ns()
        out = {}
        started = time.monotonic()
        futures = {}
        for it in items:
            if cancelled is not None and cancelled():
                break
            fut = self._pool.submit(read_value, it,
                                    last_reader=self.last_reader,
                                    end_ns=end_ns, timeout=self.timeout,
                                    readable_error=readable_error)
            futures[fut] = int(it["id"])
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
                iid = futures[fut]
                try:
                    out[iid] = fut.result()
                except Exception as exc:  # never let one read kill a pass
                    out[iid] = Reading(error=f"read failed: {exc}")
        for fut in pending:
            out[futures[fut]] = Reading(
                error="not read in time — the archiver did not answer this pass")
            _bump("abandoned")
            fut.cancel()
        return out

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
