"""Reading a long stretch of archive for a plot, fast and honestly.

The problem this solves: the archiver has no server-side averaging, so the only
way to know what a channel did over six months is to read it - and a busy
channel over six months is more readings than any machine wants to hold, let
alone draw. Asking for it an hour at a time, one channel after another, took 40
minutes for two channels.

Three ideas do the work:

* **Measure first.** What the archiver refuses is a response that carries too
  many readings, not a long time range. A channel written once every ten
  seconds answers a whole day in one request; a channel written thirty times a
  second does not manage two hours. A few short probes say which kind this is,
  and the request size is chosen to match. For a chiller over 180 days that
  turns 4320 requests into 360.

  A probe that could not read anything is NOT a slow channel, and the two were
  once the same number: see plan_span_for. Planning the biggest possible
  request on ignorance is what froze the laptop on 2026-09-02.
* **Condense while reading.** Each answer is turned into per-bin minimum,
  maximum, mean and count straight away, in the thread that received it, and
  then dropped. About 900 bins - one per pixel of the picture - so the memory
  does not depend on how long the window is. The minimum and maximum are what
  keep a two-second spike visible in a six-month plot.
* **Say what was not read.** A stretch that could not be fetched is not the
  same thing as a stretch where the archiver holds nothing, and neither is the
  same as a stretch nobody asked for. All three are blank on the picture, so
  the words have to tell them apart.
* **Stay inside the machine.** One reading costs ~1.7 KB of memory once parsed
  (see cpva_api.MAX_RESPONSE_BYTES), so the size of a single answer is a real
  limit and not a detail: a plot that ignored it emptied a 31 GB commit limit
  and froze the PC. Each answer is capped, a long stretch is worked through
  piece by piece rather than collected, and _MemoryGuard gives the whole plot
  up if it ever gets tight anyway.

No Qt and no matplotlib in here on purpose, so it can be run and tested with
plain python.
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

import cpva_api as api
import memstats

NS = 1_000_000_000

# How many readings one request should aim to bring back. The archiver refuses
# somewhere above ~110 000, the probe only samples the rate, and ten of these
# are in flight at once - so leave room. At ~1.7 KB of RAM per reading
# (measured, see cpva_api.MAX_RESPONSE_BYTES) this is ~104 MB per answer.
TARGET_SAMPLES = 60_000

# What one request is worth when the probe found readings but hit the size
# ceiling: the channel holds at least a ceiling's worth in the probed hour.
_DENSE_RATE_PER_HOUR = api.MAX_RESPONSE_BYTES / 345.0   # 345 B/reading, measured

# The whole plot gives up when memory gets this tight, whatever it has read so
# far. This is the backstop, not the fix: the request-size ceiling in cpva_api
# is what keeps the read bounded, and this catches whatever it did not.
#
# The app's own commit is the figure that matters - it is one runaway plot we
# are guarding against, and it showed as 24 GB in one process. The PC figure is
# there because this laptop shares its commit limit with everything else the
# operator has open, and 24 GB in Diagnostic was only ~75 % of the limit, so a
# PC-wide percentage alone would have let it happen.
MEM_OWN_CEILING_BYTES = 4 * 1024 * 1024 * 1024
MEM_PC_CEILING_PCT = 92.0
MEM_CHECK_INTERVAL_S = 0.5

# One bin per pixel of the 8 in x 110 dpi picture.
DEFAULT_BINS = 900

# Requests one /plot may spend before it starts sampling instead of reading
# everything.
DEFAULT_BUDGET = 600
MIN_BUCKETS_PER_PV = 60

# Readings per second the archiver actually delivers to this program, measured
# 2026-09-02 on a chiller temperature: 66 k/s with 6 requests in flight, 91 k
# with 10, 99 k with 16, 87 k with 24. It saturates, so this is a property of
# the server, not of how hard we push. Used only to tell the operator roughly
# how long a big plot will take.
READ_RATE_PER_S = 90_000.0

# Below this many readings the raw curve is kept and drawn exactly as before -
# a short window must look and behave the way it always has.
RAW_KEEP = 20_000

# A window this short is read the old way: three probe requests would cost as
# much as the fetch itself.
PROBE_MIN_WINDOW_NS = int(6 * 3600 * NS)
PROBE_SPAN_NS = int(3600 * NS)

# Request spans that read well in a log line.
_NICE_SPANS_NS = tuple(int(s * NS) for s in (
    60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 14400,
    21600, 28800, 43200, 86400))


# ---------------------------------------------------------------------------
# What the caller asks for, and what it gets back
# ---------------------------------------------------------------------------

@dataclass
class SeriesRequest:
    """One curve: which channel, and how to sanity-filter its readings."""
    pv_name: str
    display_name: str
    vmin: Optional[float] = None
    vmax: Optional[float] = None


@dataclass
class SeriesData:
    """One curve's readings, condensed to bins, plus what happened while reading."""
    request: SeriesRequest
    bin_t_ns: np.ndarray      # int64 - mean reading time in the bin
    bin_mean: np.ndarray      # float64 - NaN where the bin is empty
    bin_min: np.ndarray
    bin_max: np.ndarray
    bin_count: np.ndarray     # int64
    bin_read_ns: np.ndarray   # int64 - how much of the bin was actually fetched
    raw_t_ns: Optional[np.ndarray] = None   # kept only for a short window
    raw_v: Optional[np.ndarray] = None
    units: str = ""
    n_samples: int = 0
    n_rejected: int = 0
    newest_ns: int = 0
    read_ns: int = 0
    failed_ns: int = 0
    planned_ns: int = 0
    window_ns: int = 0
    errors: list = field(default_factory=list)
    n_chunks: int = 0
    n_chunks_failed: int = 0
    non_numeric: bool = False

    @property
    def is_reduced(self) -> bool:
        return self.raw_t_ns is None

    @property
    def has_data(self) -> bool:
        return self.n_samples > 0

    @property
    def coverage(self) -> float:
        return (self.read_ns / self.window_ns) if self.window_ns else 0.0

    @property
    def all_chunks_failed(self) -> bool:
        return self.n_chunks > 0 and self.n_chunks_failed == self.n_chunks


@dataclass
class FetchReport:
    """What the whole read cost and how complete it is."""
    mode: str = "full"                 # full | sampled | detail | short
    n_requests: int = 0
    n_done: int = 0
    n_failed: int = 0
    elapsed_s: float = 0.0
    coverage: float = 1.0              # the least well covered curve
    span_ns: dict = field(default_factory=dict)
    s_per_request: float = 0.0
    n_pvs: int = 0
    stopped_low_memory: bool = False   # abandoned to save the machine
    stopped_reason: str = ""


class PlotTooBig(Exception):
    """The question cannot be answered well enough to be worth answering."""


# ---------------------------------------------------------------------------
# Probe and plan
# ---------------------------------------------------------------------------

@dataclass
class RateProbe:
    """What the probes found out about how densely a channel is written."""
    rate_per_hour: float = 0.0      # the busiest hour seen
    s_per_request: float = 0.0
    n_answered: int = 0             # probes that came back with an answer
    n_failed: int = 0               # probes that could not be read at all
    too_big: bool = False           # a probe hour alone passed the size ceiling

    @property
    def known(self) -> bool:
        """True when the readings-per-hour figure is a measurement.

        A rate of zero from a probe that answered is a measurement (that hour
        really is empty). A rate of zero because every probe failed is not, and
        must never be planned on - see plan_span_for.
        """
        return self.n_answered > 0


def probe_rate(pv_name: str, start_ns: int, end_ns: int, *,
               timeout: float, at=(0.02, 0.25, 0.50, 0.75, 0.98),
               fetch=None) -> RateProbe:
    """Measure how densely a channel is written, at several points of the window.

    Several probes, not one, and the highest of them. A single probe at one
    point of the window is a coin flip on a facility that runs in shifts: a
    window that starts at 03:00 on a Sunday would measure a rate a hundred
    times below the Tuesday-afternoon rate, the request size would be planned
    far too large, and every weekday request would then be refused.

    Five points rather than three, and both ends included, because the case
    that brought the laptop down was every probe landing on an empty hour of a
    180-day window: "no readings anywhere I looked" was then planned as "the
    quietest channel there is, ask for whole days at a time".

    A probe that trips the size ceiling is not a failure - it is the strongest
    possible answer about density, and is recorded as such.
    """
    fetch = fetch or api.cpva_fetch_samples
    window = end_ns - start_ns
    # Measuring must stay a small fraction of the reading. Five points are
    # worth it over months; over eight hours they would read half the window
    # twice, so a short window gets fewer of them - and none of them overlap.
    n = max(1, min(len(at), int(window // (4 * PROBE_SPAN_NS))))
    if n >= len(at):
        picked = list(at)
    elif n == 1:
        picked = [at[len(at) // 2]]
    else:
        picked = [at[round(i * (len(at) - 1) / (n - 1))] for i in range(n)]

    spans = []
    for frac in picked:
        s = start_ns + int(window * frac)
        e = min(s + PROBE_SPAN_NS, end_ns)
        if e > s and (not spans or s >= spans[-1][1]):
            spans.append((s, e))
    if not spans:
        return RateProbe()

    out = RateProbe()
    counts, times = [], []
    lock = threading.Lock()

    def one(bounds):
        s, e = bounds
        t0 = time.monotonic()
        rate, failed, too_big = None, False, False
        try:
            rate = len(fetch(pv_name, s, e, timeout)) * (3600 * NS) / max(1, e - s)
        except api.ResponseTooLarge:
            too_big = True
        except Exception:                       # noqa: BLE001 - a probe may fail
            failed = True
        dt = time.monotonic() - t0
        with lock:
            times.append(dt)
            if too_big:
                out.too_big = True
                out.n_answered += 1
                counts.append(_DENSE_RATE_PER_HOUR * (3600 * NS) / max(1, e - s))
            elif failed:
                out.n_failed += 1
            else:
                out.n_answered += 1
                counts.append(rate)

    with ThreadPoolExecutor(max_workers=len(spans)) as ex:
        list(ex.map(one, spans))

    out.rate_per_hour = max(counts) if counts else 0.0
    out.s_per_request = sorted(times)[len(times) // 2] if times else 0.0
    return out


def plan_chunk_span(samples_per_hour: float, *,
                    target_samples: int = TARGET_SAMPLES,
                    min_span_ns: int = api.SPLIT_MIN_SPAN_NS,
                    max_span_ns: int = api.MAX_CHUNK_SPAN_NS) -> int:
    """How long a stretch one request should ask for, given the channel's rate."""
    if samples_per_hour <= 0:
        return max_span_ns
    hours = target_samples / samples_per_hour
    want = int(hours * 3600 * NS)
    want = max(min_span_ns, min(max_span_ns, want))
    # Round down to a span that reads well in a log line.
    nice = [s for s in _NICE_SPANS_NS if s <= want]
    return nice[-1] if nice else min_span_ns


def plan_span_for(probe: RateProbe, *, target_samples: int = TARGET_SAMPLES) -> int:
    """How long a stretch to ask for, given what the probes managed to learn.

    The distinction the crash of 2026-09-02 turned on: a measured zero and an
    unmeasurable zero are not the same number. "Every probe answered, and those
    hours were empty" is a real reading of a quiet channel, and a whole day per
    request is the right, fast plan for it - the size ceiling in cpva_api is
    what makes it safe to be wrong about. "No probe answered at all" is
    ignorance, and planning the largest possible request on ignorance is how
    ten answers of several gigabytes each got asked for at once. Ignorance gets
    the span that is safe for any channel without asking.
    """
    if not probe.known:
        return api.CHUNK_SIZE_NS
    return plan_chunk_span(probe.rate_per_hour, target_samples=target_samples)


def plan_tasks(pv_names: list[str], start_ns: int, end_ns: int,
               span_ns: dict, *, budget: int = DEFAULT_BUDGET,
               detail: bool = False) -> tuple[list, str, dict]:
    """Decide what to ask for. Returns (tasks, mode, planned_ns per channel).

    Full coverage when it fits the budget, everything when `detail` is set, and
    otherwise evenly spread sample stretches - one per bucket - so the picture
    still covers the whole window instead of only its beginning.
    """
    window = end_ns - start_ns
    if window <= 0 or not pv_names:
        return [], "full", {}

    full_cost = sum(max(1, math.ceil(window / max(1, span_ns[pv])))
                    for pv in pv_names)
    if detail or full_cost <= budget:
        mode = "detail" if detail else "full"
        tasks, planned = [], {}
        for pv in pv_names:
            span = max(1, span_ns[pv])
            bounds = []
            cs = start_ns
            while cs < end_ns:
                ce = min(cs + span, end_ns)
                bounds.append((cs, ce))
                cs = ce
            planned[pv] = window
            tasks.append((pv, bounds))
        return _interleave(tasks), mode, planned

    # Sampling: one stretch out of every bucket.
    per_pv = max(MIN_BUCKETS_PER_PV, budget // len(pv_names))
    tasks, planned = [], {}
    for pv in pv_names:
        span = max(1, min(span_ns[pv], window // per_pv or 1))
        bucket = window / per_pv
        bounds = []
        for i in range(per_pv):
            centre = start_ns + int(bucket * (i + 0.5))
            cs = max(start_ns, centre - span // 2)
            ce = min(end_ns, cs + span)
            if ce > cs:
                bounds.append((cs, ce))
        planned[pv] = sum(e - s for s, e in bounds)
        tasks.append((pv, bounds))
    coverage = min(planned[pv] / window for pv in pv_names)
    if coverage < 0.02:
        raise PlotTooBig(
            f"{len(pv_names)} PVs over {window / 86400e9:.0f} days would only "
            f"read {coverage * 100:.1f} % of the window — too little to be worth "
            f"looking at. Ask for fewer PVs, a shorter window, or add `; detail` "
            f"to read all of it however long it takes.")
    return _interleave(tasks), "sampled", planned


def _interleave(per_pv: list) -> list:
    """One task list, newest stretch of every channel first, then round-robin.

    The newest stretch goes first because the picture says whether it is current
    from the newest reading it holds; a channel that is perfectly up to date must
    not be stamped "not current" merely because its last request was scheduled
    last.
    """
    ordered = []
    for pv, bounds in per_pv:
        if not bounds:
            continue
        rest = list(bounds)
        head = [rest.pop()]                    # the stretch touching the end
        if rest:
            head.append(rest.pop(0))           # and the one touching the start
        ordered.append((pv, head + rest))

    out, i = [], 0
    while True:
        added = False
        for pv, bounds in ordered:
            if i < len(bounds):
                cs, ce = bounds[i]
                out.append(api.ChunkTask(pv, cs, ce, len(out)))
                added = True
        if not added:
            break
        i += 1
    return out


# ---------------------------------------------------------------------------
# Condensing
# ---------------------------------------------------------------------------

def _values_array(samples: list, n: int):
    """Readings as float64, or None when this channel is not a plain number.

    Booleans are refused rather than plotted as 0 and 1: the old chart path
    skipped them, and a switch drawn as a curve invites the wrong conclusion.
    """
    first = samples[0].get("value")
    if isinstance(first, bool):
        return None
    try:
        if isinstance(first, (int, float)):
            return np.fromiter((s["value"] for s in samples), np.float64, n)
        if isinstance(first, list) and len(first) == 1:
            return np.fromiter((s["value"][0] for s in samples), np.float64, n)
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    # Anything unusual goes the slow, forgiving way.
    out = np.full(n, np.nan)
    for i, s in enumerate(samples):
        v = api.cpva_decode_value(s)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[i] = float(v)
    return out


class _BinReducer:
    """One channel's bins. Fed from several threads at once."""

    def __init__(self, request: SeriesRequest, start_ns: int, end_ns: int,
                 n_bins: int, raw_keep: int = RAW_KEEP):
        self.request = request
        self.start_ns = start_ns
        self.end_ns = end_ns
        self.n_bins = max(1, n_bins)
        self.width = max(1, (end_ns - start_ns) / self.n_bins)
        self.raw_keep = raw_keep

        z = np.zeros(self.n_bins)
        self.count = np.zeros(self.n_bins, dtype=np.int64)
        self.vsum = z.copy()
        self.tsum = z.copy()
        self.vmin = np.full(self.n_bins, np.inf)
        self.vmax = np.full(self.n_bins, -np.inf)
        self.read_ns = np.zeros(self.n_bins, dtype=np.int64)

        self._raw = []
        self._raw_n = 0
        self._raw_dropped = False
        self._lock = threading.Lock()

        self.units = ""
        self.n_samples = 0
        self.n_rejected = 0
        self.newest_ns = 0
        self.read_total = 0
        self.failed_total = 0
        self.errors = []
        self.n_chunks = 0
        self.n_chunks_failed = 0
        self.non_numeric = False

    # -- fetching side ------------------------------------------------------

    def add_chunk(self, task, samples: list) -> None:
        """Fold one answer into the bins. Runs in the fetching thread."""
        cs, ce = task.start_ns, task.end_ns
        n = len(samples)
        if not n:
            with self._lock:
                self.n_chunks += 1
                self.read_total += ce - cs
                self._mark_read(cs, ce)
            return

        t = np.fromiter((s.get("time", 0) for s in samples), np.int64, n)
        v = _values_array(samples, n)
        if v is None:
            with self._lock:
                self.non_numeric = True
                self.n_chunks += 1
                self.read_total += ce - cs
                self._mark_read(cs, ce)
            return

        units = ""
        for s in (samples[0], samples[-1]):
            u = api.cpva_decode_units(s)
            if u:
                units = u

        keep = np.isfinite(v)
        r = self.request
        if r.vmin is not None:
            keep &= v >= r.vmin
        if r.vmax is not None:
            keep &= v <= r.vmax
        rejected = int(n - keep.sum())
        newest = int(t.max()) if n else 0
        t, v = t[keep], v[keep]

        if t.size:
            # int64 first: nanoseconds do not survive a float64 round trip.
            rel = (t - self.start_ns).astype(np.float64)
            idx = np.clip((rel / self.width).astype(np.int64), 0,
                          self.n_bins - 1)
            if idx.size > 1 and not np.all(np.diff(idx) >= 0):
                order = np.argsort(idx, kind="stable")
                idx, rel, v = idx[order], rel[order], v[order]
            starts = np.concatenate(([0], np.flatnonzero(np.diff(idx)) + 1))
            bins = idx[starts]
            csum = np.add.reduceat(v, starts)
            tsum = np.add.reduceat(rel, starts)
            cmin = np.minimum.reduceat(v, starts)
            cmax = np.maximum.reduceat(v, starts)
            sizes = np.diff(np.append(starts, idx.size))
        else:
            bins = None

        with self._lock:
            self.n_chunks += 1
            self.n_samples += int(t.size)
            self.n_rejected += rejected
            self.newest_ns = max(self.newest_ns, newest)
            self.read_total += ce - cs
            if units:
                self.units = units
            self._mark_read(cs, ce)
            if bins is not None:
                # `bins` holds each touched bin once, so plain indexing is both
                # correct and far quicker than the unbuffered np.*.at forms.
                self.count[bins] += sizes
                self.vsum[bins] += csum
                self.tsum[bins] += tsum
                self.vmin[bins] = np.minimum(self.vmin[bins], cmin)
                self.vmax[bins] = np.maximum(self.vmax[bins], cmax)
            if not self._raw_dropped:
                self._raw_n += int(t.size)
                if self._raw_n > self.raw_keep:
                    self._raw = []
                    self._raw_dropped = True
                elif t.size:
                    self._raw.append((t, v))

    def mark_failed(self, task, exc: BaseException) -> None:
        """Record a stretch that could not be read. Runs in the fetching thread."""
        with self._lock:
            self.n_chunks += 1
            self.n_chunks_failed += 1
            self.failed_total += task.end_ns - task.start_ns
            msg = str(exc) or exc.__class__.__name__
            if msg not in self.errors and len(self.errors) < 3:
                self.errors.append(msg)

    def _mark_read(self, cs: int, ce: int) -> None:
        """Add this stretch to the per-bin 'was fetched' tally (lock held)."""
        i0 = int(max(0, min(self.n_bins - 1, (cs - self.start_ns) // self.width)))
        i1 = int(max(0, min(self.n_bins - 1, (ce - 1 - self.start_ns) // self.width)))
        for i in range(i0, i1 + 1):
            lo = self.start_ns + int(i * self.width)
            hi = self.start_ns + int((i + 1) * self.width)
            overlap = min(ce, hi) - max(cs, lo)
            if overlap > 0:
                self.read_ns[i] += overlap

    # -- result -------------------------------------------------------------

    def finish(self) -> SeriesData:
        empty = self.count == 0
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = self.vsum / self.count
            tmean = self.tsum / self.count
        mean[empty] = np.nan
        vmin = np.where(empty, np.nan, self.vmin)
        vmax = np.where(empty, np.nan, self.vmax)
        # The mean reading time, not the middle of the bin: over six months a
        # bin is nearly five hours wide, and a channel written twice a day would
        # otherwise have its readings drawn hours away from when they happened.
        # Back to whole nanoseconds before adding the start of the window: an
        # epoch time is around 1.8e18 ns, where float64 can only step 256 ns at
        # a time, and the offset from the start of the window is small enough to
        # stay exact.
        t_ns = (np.where(empty, 0, np.nan_to_num(tmean)).astype(np.int64)
                + np.int64(self.start_ns))

        raw_t = raw_v = None
        if not self._raw_dropped and self._raw:
            raw_t = np.concatenate([a for a, _ in self._raw])
            raw_v = np.concatenate([b for _, b in self._raw])
            order = np.argsort(raw_t, kind="stable")
            raw_t, raw_v = raw_t[order], raw_v[order]
        elif not self._raw_dropped:
            raw_t = np.zeros(0, dtype=np.int64)
            raw_v = np.zeros(0)

        return SeriesData(
            request=self.request, bin_t_ns=t_ns, bin_mean=mean,
            bin_min=vmin, bin_max=vmax, bin_count=self.count.copy(),
            bin_read_ns=self.read_ns.copy(), raw_t_ns=raw_t, raw_v=raw_v,
            units=self.units, n_samples=self.n_samples,
            n_rejected=self.n_rejected, newest_ns=self.newest_ns,
            read_ns=self.read_total, failed_ns=self.failed_total,
            planned_ns=0, window_ns=self.end_ns - self.start_ns,
            errors=list(self.errors), n_chunks=self.n_chunks,
            n_chunks_failed=self.n_chunks_failed,
            non_numeric=self.non_numeric)


# ---------------------------------------------------------------------------
# The whole read
# ---------------------------------------------------------------------------

class _MemoryGuard:
    """Asked before every request whether the read may go on.

    Wraps the caller's own cancel switch, so one object answers both "the
    operator gave up on this plot" and "this plot is about to take the machine
    down". Reads the figures at most every MEM_CHECK_INTERVAL_S: the ctypes
    call is cheap, but a split request can ask hundreds of times a second.

    Once it has said stop it keeps saying stop - a read that is abandoned for
    memory must not resume because a garbage collection made room for a moment.
    """

    def __init__(self, cancel_fn=None, log_fn=None, *,
                 own_ceiling: int = MEM_OWN_CEILING_BYTES,
                 pc_ceiling_pct: float = MEM_PC_CEILING_PCT,
                 read_fn=None):
        self._cancel_fn = cancel_fn
        self._log_fn = log_fn
        self._own_ceiling = own_ceiling
        self._pc_ceiling_pct = pc_ceiling_pct
        self._read = read_fn or memstats.read
        self._next_check = 0.0
        self._lock = threading.Lock()
        self.tripped = False
        self.reason = ""

    def __call__(self) -> bool:
        if self._cancel_fn is not None and self._cancel_fn():
            return True
        with self._lock:
            if self.tripped:
                return True
            now = time.monotonic()
            if now < self._next_check:
                return False
            self._next_check = now + MEM_CHECK_INTERVAL_S
        snap = self._read()
        if snap is None:
            return False
        reason = ""
        if snap.proc_commit >= self._own_ceiling:
            reason = (f"this program had promised "
                      f"{memstats.fmt(snap.proc_commit)} of memory")
        elif snap.sys_commit_pct >= self._pc_ceiling_pct:
            reason = (f"the PC had promised {snap.sys_commit_pct:.0f} % of its "
                      f"{memstats.fmt(snap.sys_commit_limit)} memory limit")
        if not reason:
            return False
        with self._lock:
            if self.tripped:
                return True
            self.tripped = True
            self.reason = reason
        if self._log_fn:
            self._log_fn(f"  plot: STOPPED READING - {reason}. Drawing what "
                         f"was read so far.")
        return True


def fetch_series_reduced(requests: list, start_ns: int, end_ns: int, *,
                         timeout: float = api.CPVA_HTTP_TIMEOUT,
                         max_workers: int = 10,
                         n_bins: int = DEFAULT_BINS,
                         target_samples: int = TARGET_SAMPLES,
                         budget: int = DEFAULT_BUDGET,
                         detail: bool = False,
                         raw_keep: int = RAW_KEEP,
                         probe: bool = True,
                         progress_fn=None,
                         plan_fn=None,
                         cancel_fn=None,
                         log_fn=None) -> tuple[list, FetchReport]:
    """Read every channel over the window and hand back condensed curves."""
    t0 = time.monotonic()
    window = end_ns - start_ns
    report = FetchReport(n_pvs=len(requests))
    reducers = [_BinReducer(r, start_ns, end_ns, n_bins, raw_keep)
                for r in requests]
    if window <= 0 or not requests:
        return [rd.finish() for rd in reducers], report

    # 1. how densely is each channel archived?
    span_ns, per_req, rates = {}, 0.0, {}
    unknown, unknown_pvs = [], set()
    short = window <= PROBE_MIN_WINDOW_NS or not probe
    for r in requests:
        if short:
            span_ns[r.pv_name] = api.CHUNK_SIZE_NS
            rates[r.pv_name] = 0.0
        else:
            p = probe_rate(r.pv_name, start_ns, end_ns, timeout=timeout)
            span_ns[r.pv_name] = plan_span_for(p, target_samples=target_samples)
            rates[r.pv_name] = p.rate_per_hour
            per_req = max(per_req, p.s_per_request)
            if not p.known:
                unknown.append(r.display_name)
                unknown_pvs.add(r.pv_name)
    if unknown and log_fn:
        log_fn(f"  plot: could not measure {', '.join(unknown)} - reading it an "
               f"hour at a time instead of guessing.")
    report.span_ns = dict(span_ns)
    report.s_per_request = per_req

    # 2. what to ask for
    tasks, mode, planned = plan_tasks([r.pv_name for r in requests],
                                      start_ns, end_ns, span_ns,
                                      budget=budget, detail=detail)
    report.mode = "short" if short and mode == "full" else mode
    report.n_requests = len(tasks)
    report.coverage = min((planned.get(r.pv_name, window) / window
                           for r in requests), default=1.0)
    if plan_fn:
        # An estimate, not a promise. Reading time follows the number of
        # readings far more closely than the number of requests, and the
        # archiver delivers about READ_RATE_PER_S of them however they are cut
        # up (measured 2026-09-02: 66 k/s at 6 workers, 91 k at 10, 99 k at 16,
        # 87 k at 24 - it saturates, so more workers would not help).
        # A channel whose rate could not be measured would otherwise be
        # estimated at zero readings and promise an instant answer. Count it as
        # a full request per chunk instead - the plan's own target.
        n_read = 0.0
        for pv in span_ns:
            rate = rates.get(pv, 0.0)
            share = planned.get(pv, window)
            if rate > 0:
                n_read += rate * share / 3.6e12
            elif pv in unknown_pvs:
                n_read += target_samples * max(1, math.ceil(share / span_ns[pv]))
        plan_fn(report.mode, len(tasks), n_read / READ_RATE_PER_S,
                report.coverage)
    if log_fn:
        log_fn(f"  plot: {len(tasks)} requests, mode={report.mode}, "
               f"spans={{{', '.join(f'{k}={v / 3.6e12:g}h' for k, v in span_ns.items())}}}")

    by_pv = {r.pv_name: rd for r, rd in zip(requests, reducers)}

    # 3. read, condensing each answer where it lands
    guard = _MemoryGuard(cancel_fn, log_fn)
    done, failed = api.cpva_run_chunks(
        tasks, timeout=timeout, max_workers=max_workers,
        on_result=lambda t, s: by_pv[t.channel].add_chunk(t, s),
        on_error=lambda t, e: by_pv[t.channel].mark_failed(t, e),
        progress_fn=progress_fn, cancel_fn=guard, span_hint={})

    datas = []
    for rd in reducers:
        d = rd.finish()
        d.planned_ns = planned.get(rd.request.pv_name, window)
        datas.append(d)

    report.n_done, report.n_failed = done, failed
    report.stopped_low_memory = guard.tripped
    report.stopped_reason = guard.reason
    report.elapsed_s = time.monotonic() - t0
    report.coverage = min((d.coverage for d in datas), default=1.0)
    return datas, report


def make_raw_series(pv_name: str, display_name: str, t_ns, values,
                    start_ns: int, end_ns: int, units: str = "",
                    n_bins: int = DEFAULT_BINS) -> SeriesData:
    """Build a SeriesData straight from readings — for tests and canned data."""
    rd = _BinReducer(SeriesRequest(pv_name, display_name), start_ns, end_ns,
                     n_bins)
    samples = [{"time": int(t), "value": float(v),
                "metaData": {"units": units}}
               for t, v in zip(t_ns, values)]
    rd.add_chunk(api.ChunkTask(pv_name, start_ns, end_ns, 0), samples)
    d = rd.finish()
    d.planned_ns = end_ns - start_ns
    return d


# ---------------------------------------------------------------------------
# Saying what happened, in words
# ---------------------------------------------------------------------------

def _pct(x: float) -> str:
    return f"{x * 100:.0f} %" if x >= 0.01 else f"{x * 100:.1f} %"


def _dur(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def describe_fetch(datas: list, report: FetchReport,
                   window_label: str = "") -> tuple[str, str]:
    """Return (lines for the chat reply, one-line banner for the picture).

    The distinction that matters: "the archiver holds nothing here" and "I could
    not read this" look identical on the picture, so the words have to separate
    them. Never report a failure as an empty window.
    """
    lines, banner = [], []
    if not datas:
        return "", ""

    window_ns = datas[0].window_ns or 1
    with_data = [d for d in datas if d.has_data]
    all_failed = [d for d in datas if d.all_chunks_failed]
    failed_ns = max((d.failed_ns for d in datas), default=0)

    if len(all_failed) == len(datas):
        first = all_failed[0].errors[0] if all_failed[0].errors else "no reason given"
        lines.append(
            f"**⚠ I could not read the archive** ({first}). That is a fetch "
            f"failure, not an empty window — the readings may well be there.")
    elif not with_data:
        lines.append("_The archiver holds no readings for these PVs in that "
                     "window._")

    for d in datas:
        name = d.request.display_name
        if d.all_chunks_failed and len(all_failed) != len(datas):
            why = d.errors[0] if d.errors else "no reason given"
            lines.append(f"**⚠ {name} could not be read** "
                         f"({d.n_chunks_failed} of {d.n_chunks} requests "
                         f"failed: {why}).")
        elif d.non_numeric:
            lines.append(f"_{name} is not a number, so it cannot be drawn._")
        elif not d.has_data and with_data:
            lines.append(f"_{name}: read without trouble, and the archiver "
                         f"holds nothing for it in that window._")
        elif d.n_chunks_failed:
            share = d.failed_ns / window_ns
            lines.append(
                f"**⚠ {name}: {_pct(share)} of the window could not be read** "
                f"({d.errors[0] if d.errors else 'no reason given'}). Those "
                f"stretches are blank on the plot, not zero.")
        if d.n_samples and d.n_rejected > 0.05 * (d.n_samples + d.n_rejected):
            share = d.n_rejected / (d.n_samples + d.n_rejected)
            lines.append(f"_{name}: {_pct(share)} of readings were outside the "
                         f"valid range and were dropped._")

    if failed_ns and len(all_failed) != len(datas):
        banner.append(f"INCOMPLETE - {_pct(failed_ns / window_ns)} "
                      f"of the window could not be read")

    if report.stopped_low_memory:
        lines.append(
            f"**⚠ I stopped reading early to keep the computer alive** — "
            f"{report.stopped_reason}. What is drawn was read; the rest of the "
            f"window was not looked at, so a peak there would not show. Ask for "
            f"a shorter window, or fewer PVs at once.")
        banner.append("STOPPED EARLY - not enough memory to read it all")

    if report.mode == "sampled":
        lines.append(
            f"_Sampled: {_pct(report.coverage)} of the window was read, in even "
            f"stretches across it. The shape, and every peak inside a stretch, "
            f"is real; a spike between two stretches would not show. Add "
            f"`; detail` to read all of it._")
        banner.append(f"SAMPLED - {_pct(report.coverage)} of the window read")
    elif report.n_requests >= 30:
        # Only worth saying when the wait was long enough to wonder about. An
        # ordinary /plot ...; 7-18 answers in a second and needs no receipt.
        lines.append(f"_Read the whole window ({report.n_requests} requests, "
                     f"{_dur(report.elapsed_s)})._")

    if any(d.is_reduced for d in datas if d.has_data):
        lines.append(f"_Condensed to {len(datas[0].bin_mean)} points: the line "
                     f"is each point's average, the shaded band its lowest and "
                     f"highest reading._")

    return "\n".join(lines), "   ".join(banner)
