"""
cpva_cache.py  —  Closed days of archived PVs, kept on the scratch share.

A day that has ended does not change in the archive any more, so once one load
has read it there is no reason for the next load to ask the archiver again. The
CSS Logger reads every closed Prague day it is about to request from here first,
and saves what it had to read from the archiver after the load.

Layout (one file per signal per month, so a three-month load opens a handful of
files instead of thousands)::

    <scratch>\\Software\\CPVA cache\\<signal>\\<YYYY-MM>.npz

Each month file holds, per day, the sample times and values as numbers and, for
a thinned day, the per-point minimum and maximum. A JSON block in the same file
says, per day, how fine it was read (``count`` = points asked for that day, or
``None`` when it was read exactly as archived), which archived name answered
(SBW4 has two) and the units.

Rules:
  * Only whole Prague days that ended more than an hour ago are stored on the
    share. What was read of the open day is kept in memory only (one block per
    signal, :meth:`DayCache.recent_get`), so a reload asks the archiver just
    for what came since.
  * A day is stored only when it was read without a gap; a failed piece leaves
    the day out, so the next load asks the archiver again.
  * A day stored at a coarser level than the one asked for is NOT served — the
    archiver is asked, and the finer day replaces it.
  * Numbers only. A signal carrying words or waveforms is never stored.
  * Writes are atomic (temporary file, then replace). Two stations saving the
    same month at once both re-read the file just before replacing it, so each
    one's days survive; the worst case is one day read twice.
  * Nothing here may stall a load: the share is looked for once, in a daemon
    thread with a deadline, and an unreachable share simply means no cache.

The folder is outside every program folder on purpose: a redeploy of the
CSS Logger replaces its own folder, and Image Tools can read the same files.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

# The two names of the scratch share, office leg first (the same order as
# Diagnostic/shared_pvs.py). The lab leg can take ~48 s to fail from the
# office, so it is only ever probed in a daemon thread with a deadline.
SHARE_ROOTS = (
    r"\\hapls-share.cs.eli-beams.eu\scratch\Software",
    r"\\hapls-share.lcs.local\scratch\Software",
)
CACHE_SUBDIR = "CPVA cache"
PROBE_TIMEOUT_S = 3.0
# A day is "closed" an hour after its last midnight: late writes to the
# archiver land within seconds, an hour is a wide margin.
CLOSED_AFTER_NS = int(3600e9)
# A stored day serves a request that asks for at most this much more detail
# (the share decides the level; 22 vs 24 points a day is the same level).
COUNT_SLACK = 1.25
FORMAT_VERSION = 1

_ENV_OVERRIDE = "CPVA_CACHE_DIR"


def _safe_name(channel: str) -> str:
    """A folder name for a signal: the PV name with path characters replaced."""
    return re.sub(r'[\\/:*?"<>|]', "_", channel).strip(" .") or "_"


def _first_reachable(roots, timeout_s: float) -> str:
    """The first root (in order) that exists, decided within ``timeout_s``.

    One daemon thread per root, so a dead host never blocks a live one, and the
    answer is given as soon as the preferred root is known."""
    state: dict = {}
    cond = threading.Condition()

    def probe(root):
        try:
            ok = os.path.isdir(root)
        except OSError:
            ok = False
        with cond:
            state[root] = ok
            cond.notify_all()

    for r in roots:
        threading.Thread(target=probe, args=(r,), daemon=True,
                         name="cpva-cache-probe").start()
    deadline = time.monotonic() + max(0.1, timeout_s)
    with cond:
        while True:
            for r in roots:
                if r not in state:
                    break
                if state[r]:
                    return r
            else:
                return ""
            left = deadline - time.monotonic()
            if left <= 0:
                break
            cond.wait(left)
    for r in roots:
        if state.get(r):
            return r
    return ""


class DayCache:
    """Closed days of archived signals, one ``.npz`` per signal per month.

    ``root`` is the cache folder itself. ``None`` means "find the scratch share"
    (in the background — :meth:`ready` waits for that with a deadline); an empty
    string means "no cache" (every call is then a cheap no-op).
    """

    def __init__(self, root: str | None = None, log_fn=None):
        self._log_fn = log_fn
        self._lock = threading.Lock()
        self._months: dict = {}            # (channel, "YYYY-MM") -> {day: entry}
        self._loaded: set = set()          # (channel, "YYYY-MM") already read
        self._dirty_set: set = set()       # (channel, "YYYY-MM", day) to write
        self._recent: dict = {}            # (channel, day start) -> block, see recent_get
        self._shared_hi: dict = {}         # (channel, day start) -> end saved on the share
        self._loaded_at: dict = {}         # (channel, "YYYY-MM") -> when read
        self._root: Path | None = None
        self._resolved = threading.Event()
        self.hits = 0
        self.stored = 0
        env = os.environ.get(_ENV_OVERRIDE, "")
        if root is None and env:
            root = env
        # "" switches the whole cache off, the days in memory too: a run from
        # source (every test) must not serve one fake archive to the next.
        self._recent_on = root != ""
        if root is None:
            threading.Thread(target=self._find_share, daemon=True,
                             name="cpva-cache-root").start()
        else:
            self._root = Path(root) if root else None
            self._resolved.set()

    # ── where it lives ───────────────────────────────────────────────────────

    def _find_share(self):
        try:
            base = _first_reachable(SHARE_ROOTS, PROBE_TIMEOUT_S)
            if base:
                self._root = Path(base) / CACHE_SUBDIR
        finally:
            self._resolved.set()

    def ready(self, timeout_s: float = PROBE_TIMEOUT_S + 0.5) -> bool:
        """True when a cache folder is known (waits for the share probe)."""
        self._resolved.wait(timeout_s)
        return self._root is not None

    @property
    def root(self) -> Path | None:
        return self._root if self._resolved.is_set() else None

    def _note(self, text: str):
        if self._log_fn:
            try:
                self._log_fn(text)
            except Exception:
                pass

    # ── days and months ──────────────────────────────────────────────────────

    @staticmethod
    def day_key(lo_ns: int, tz) -> str:
        return datetime.fromtimestamp(lo_ns / 1e9, tz).strftime("%Y-%m-%d")

    def _month_path(self, channel: str, month: str) -> Path:
        return self._root / _safe_name(channel) / f"{month}.npz"

    def _read_month_file(self, path: Path) -> dict:
        """{day: entry} from one month file; {} when missing or unreadable."""
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError:
            return {}
        try:
            z = np.load(io.BytesIO(blob), allow_pickle=False)
            meta = json.loads(str(z["meta"]))
            if meta.get("version") != FORMAT_VERSION:
                return {}
            out = {}
            for day, info in (meta.get("days") or {}).items():
                ent = dict(info)
                ent["t"] = z[f"{day}_t"]
                ent["v"] = z[f"{day}_v"]
                if f"{day}_lo" in z.files:
                    ent["lo"] = z[f"{day}_lo"]
                    ent["hi"] = z[f"{day}_hi"]
                out[day] = ent
            return out
        except Exception:
            return {}       # a half-written or foreign file is simply not used

    def prefetch(self, channels, days, tz, max_workers: int = 8) -> int:
        """Read every month file the given days need, in parallel.

        ``days`` is ``[(lo_ns, hi_ns), …]`` of whole Prague days. Returns how
        many month files were found. Meant to run once at the start of a load,
        so the per-day look-ups after it never touch the share."""
        if not self.ready():
            return 0
        now = time.time()
        recent_from = now * 1e9 - RECENT_DAYS * 86_400 * 10**9
        months = sorted({self.day_key(lo, tz)[:7] for lo, _hi in days})
        # A month holding one of the recent days is read again after a while:
        # another station may have saved more of today since.
        fresh = {self.day_key(lo, tz)[:7] for lo, hi in days if hi > recent_from}
        todo = [(ch, m) for ch in channels for m in months
                if (ch, m) not in self._loaded
                or (m in fresh and now - self._loaded_at.get((ch, m), 0)
                    > SHARE_PART_STEP_NS / 1e9)]
        if not todo:
            return 0

        def _one(key):
            ch, m = key
            return key, self._read_month_file(self._month_path(ch, m))

        found = 0
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(todo)))) as ex:
            for key, days_map in ex.map(_one, todo):
                with self._lock:
                    # Days saved here and not written yet stay.
                    mine = self._months.get(key, {})
                    pending = {d for c, m, d in self._dirty_set if (c, m) == key}
                    self._months[key] = {**days_map,
                                         **{d: mine[d] for d in pending if d in mine}}
                    self._loaded.add(key)
                    self._loaded_at[key] = now
                self._adopt_parts(key[0], days_map)
                found += bool(days_map)
        return found

    def _adopt_parts(self, channel: str, days_map: dict) -> None:
        """Take the part days a month file holds into the blocks in memory."""
        if not self._recent_on:
            return
        now_ns = int(time.time() * 1e9)
        for day, ent in days_map.items():
            if not day.endswith(PART_SUFFIX) or ent.get("lo_ns") is None:
                continue
            day_lo = ent.get("day_lo")
            if day_lo is None or day_lo < now_ns - RECENT_DAYS * 86_400 * 10**9:
                continue
            blk = {k: ent[k] for k in ("t", "v", "lo", "hi") if k in ent}
            blk.update(units=ent.get("units", ""), lo_ns=int(ent["lo_ns"]),
                       hi_ns=int(ent["hi_ns"]), src=ent.get("src"),
                       density=ent.get("density"))
            key = (channel, int(day_lo))
            with self._lock:
                old = self._recent.get(key)
                joined = _join_blocks(old, blk) if old is not None else blk
                if joined is None:      # not joinable: keep the one covering more
                    joined = max((old, blk), key=lambda b: b["hi_ns"] - b["lo_ns"])
                self._recent[key] = joined
                self._shared_hi[key] = max(self._shared_hi.get(key, 0),
                                           int(ent["hi_ns"]))

    def get(self, channel: str, lo_ns: int, hi_ns: int, count, tz):
        """The stored day as archiver-shaped sample dicts, or None.

        ``count`` is the number of points the load would ask the archiver for
        this day (``None`` = exactly as archived). A day stored coarser than
        that is not served."""
        if self._root is None:
            return None
        day = self.day_key(lo_ns, tz)
        with self._lock:
            ent = self._months.get((channel, day[:7]), {}).get(day)
        if ent is None or ent.get("lo_ns") != int(lo_ns) or ent.get("hi_ns") != int(hi_ns):
            return None
        have = ent.get("count")
        if have is not None and (count is None or count > have * COUNT_SLACK):
            return None
        self.hits += 1
        return _to_samples(ent)

    def put(self, channel: str, lo_ns: int, hi_ns: int, samples, count,
            src: str, tz, now_ns: int) -> bool:
        """Remember one closed day (kept in memory until :meth:`flush`).

        Returns False when the day is not storable: still open, or carrying
        anything but plain numbers."""
        if self._root is None or hi_ns > now_ns - CLOSED_AFTER_NS:
            return False
        ent = _from_samples(samples, lo_ns, hi_ns)
        if ent is None:
            return False
        ent.update(count=(None if count is None else int(count)), src=src,
                   lo_ns=int(lo_ns), hi_ns=int(hi_ns), saved=int(time.time()))
        day = self.day_key(lo_ns, tz)
        with self._lock:
            old = self._months.setdefault((channel, day[:7]), {}).get(day)
            if old is not None and old.get("count") is None and ent["count"] is not None:
                return False      # never replace an exact day with a thinned one
            self._months[(channel, day[:7])][day] = ent
            self._dirty_set.add((channel, day[:7], day))
        return True

    def flush(self) -> int:
        """Write every month that gained a day. Returns the days written."""
        if self._root is None:
            return 0
        with self._lock:
            dirty = sorted(self._dirty_set)
            self._dirty_set = set()
        by_month: dict = {}
        for ch, month, day in dirty:
            by_month.setdefault((ch, month), set()).add(day)
        written = 0
        for (ch, month), days in by_month.items():
            path = self._month_path(ch, month)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                # Re-read just before replacing: another station may have saved
                # other days of this month since this one read it.
                merged = self._read_month_file(path)
                with self._lock:
                    mine = self._months.get((ch, month), {})
                    for day in days:
                        if day not in mine:
                            continue
                        theirs = merged.get(day)
                        if day.endswith(PART_SUFFIX) and theirs is not None:
                            # Another station saved part of the same day: join
                            # the two, or keep the one covering more.
                            merged[day] = (_join_blocks(theirs, mine[day])
                                           or max((theirs, mine[day]),
                                                  key=lambda b: b["hi_ns"] - b["lo_ns"]))
                        else:
                            merged[day] = mine[day]
                    _drop_stale_parts(merged)
                    self._months[(ch, month)] = {**merged, **{
                        d: e for d, e in mine.items() if d not in merged
                        and not d.endswith(PART_SUFFIX)}}
                _write_month_file(path, merged)
                written += sum(1 for d in days if not d.endswith(PART_SUFFIX))
            except Exception as exc:
                self._note(f"day cache: could not save {ch} {month} ({exc})")
        self.stored += written
        return written

    def flush_async(self):
        """:meth:`flush` in a daemon thread, so a load never waits for the share."""
        if self._root is None or not self._dirty_set:
            return
        threading.Thread(target=self.flush, daemon=True, name="cpva-cache-save").start()

    # ── parts of recent days: what is already read, in memory only ──────────
    # The share keeps whole closed days. A "last 24 hours" window holds none:
    # it is the open day plus the afternoon of yesterday, so every reload read
    # both again. The open day is the expensive one — the archiver has no
    # thinned level for it, so it comes back exactly as archived (measured
    # 12.10.2026: 78 000 samples of PAP1 by 15:00, one request 4–21 s, while a
    # closed day was under a second). Neither part changes once read, so a
    # reload asks only for what came since. Kept in this process only, one
    # block per signal per Prague day, for the last RECENT_DAYS days.

    def recent_get(self, channel: str, day_lo: int, lo_ns: int, hi_ns: int,
                   src: str, density):
        """``(a, b, samples)`` — the part of [lo, hi) already read, or None.

        [lo, hi) lies inside the Prague day starting at ``day_lo``. ``samples``
        are those inside [a, b) plus the one just before ``a`` when the block
        holds it (a request brings that one too: it is the value the signal had
        at ``a``). ``src`` is the archived name the caller would ask first; a
        block another name answered is not served. ``density`` is the points
        per nanosecond the caller would ask for (None = exactly as archived);
        a block read coarser than that is not served."""
        if not self._recent_on:
            return None
        with self._lock:
            ent = self._recent.get((channel, int(day_lo)))
        if ent is None or ent["src"] != src:
            return None
        have = ent["density"]
        if have is not None and (density is None or density > have * COUNT_SLACK):
            return None
        a, b = max(int(lo_ns), ent["lo_ns"]), min(int(hi_ns), ent["hi_ns"])
        if b <= a:
            return None
        t = ent["t"]
        i0 = max(0, int(np.searchsorted(t, a, side="left")) - 1)
        i1 = int(np.searchsorted(t, b, side="left"))
        part = {k: ent[k][i0:i1] for k in ("t", "v", "lo", "hi") if k in ent}
        part["units"] = ent["units"]
        return a, b, _to_samples(part)

    def recent_put(self, channel: str, day_lo: int, lo_ns: int, hi_ns: int,
                   samples, src: str, density, now_ns: int, tz=None) -> bool:
        """Remember what one load read over [lo, hi), inside the Prague day
        starting at ``day_lo``.

        Only the part older than RECENT_SETTLE_NS is kept, and only plain
        numbers. It joins the block already held when it touches it and was
        read at the same level of detail; otherwise it replaces that block.
        With ``tz`` given (and a share), the block is also saved there as the
        day's part (``<day>+part`` in the month file) — see :meth:`flush`."""
        if not self._recent_on:
            return False
        now_ns = int(now_ns)
        hi_ns = min(int(hi_ns), now_ns - RECENT_SETTLE_NS)
        lo_ns, day_lo = int(lo_ns), int(day_lo)
        if hi_ns <= lo_ns or day_lo < now_ns - RECENT_DAYS * 86_400 * 10**9:
            return False
        thinned = any((s.get("quality") or "Original") != "Original"
                      for s in samples if lo_ns <= (s.get("time") or 0) < hi_ns)
        if thinned and density is None:
            return False                 # asked exact, got something else
        # The newest sample before the block is kept with it: it is the value
        # the signal had at the block's start. Without it a quiet signal (a
        # motor position, a valve) served from here had nothing to hold from,
        # and the load went looking for it in the archive afterwards.
        before = [s.get("time") for s in samples
                  if s.get("time") is not None and s.get("time") < lo_ns]
        ent = _from_samples(samples, max(before) if before else lo_ns, hi_ns)
        if ent is None:
            return False                 # words or waveforms
        if thinned and "lo" not in ent:
            ent["lo"], ent["hi"] = ent["v"].copy(), ent["v"].copy()
        elif not thinned:
            ent.pop("lo", None)
            ent.pop("hi", None)
            density = None               # exact serves any level of detail
        ent.update(lo_ns=lo_ns, hi_ns=hi_ns, src=src, density=density)
        key = (channel, day_lo)
        with self._lock:
            old = self._recent.get(key)
            if old is not None:
                # Not joinable (another level, another name, a hole between):
                # the one covering more stays — a block taken over from the
                # share must not give way to a few minutes read elsewhere.
                ent = _join_blocks(old, ent) or max(
                    (old, ent), key=lambda b: b["hi_ns"] - b["lo_ns"])
            for k in [k for k in self._recent
                      if k[1] < now_ns - RECENT_DAYS * 86_400 * 10**9]:
                del self._recent[k]
                self._shared_hi.pop(k, None)
            if len(ent["t"]) > RECENT_MAX_SAMPLES:
                self._recent.pop(key, None)
                return False
            self._recent[key] = ent
            # On the share too, once it has grown by SHARE_PART_STEP_NS since
            # it was last saved there (a restart, or another station, then
            # reads only what came after it).
            if (tz is not None and self._root is not None
                    and ent["hi_ns"] - self._shared_hi.get(key, ent["lo_ns"])
                    >= SHARE_PART_STEP_NS):
                day = self.day_key(day_lo, tz) + PART_SUFFIX
                part = dict(ent, day_lo=day_lo, saved=int(time.time()), count=None)
                self._months.setdefault((channel, day[:7]), {})[day] = part
                self._dirty_set.add((channel, day[:7], day))
                self._shared_hi[key] = ent["hi_ns"]
        return True


# What was read is kept only up to this long before "now": a sample stamped a
# moment ago may still be on its way to the archive.
RECENT_SETTLE_NS = int(120e9)
# Days kept in memory, counted back from now (older ones are on the share).
RECENT_DAYS = 3
# One block is dropped beyond this (16 bytes a sample: 32 MB).
RECENT_MAX_SAMPLES = 2_000_000
# A block is saved on the share again once it has grown by this much: at most
# one write per signal per half hour per station (today's raw energies are
# ~1.3 MB a signal by the evening).
SHARE_PART_STEP_NS = int(1800e9)
# The month-file key of a day's part: "<YYYY-MM-DD>+part". Separate from the
# whole day's own key, which the closed day takes later.
PART_SUFFIX = "+part"


def _same_level(a, b) -> bool:
    """Two blocks read at the same level of detail (both exact, or thinned to
    densities within COUNT_SLACK of each other)."""
    da, db = a.get("density"), b.get("density")
    if da is None or db is None:
        return da is None and db is None
    return max(da, db) <= min(da, db) * COUNT_SLACK


def _join_blocks(old: dict, new: dict):
    """One block of the two when they touch or overlap and match in level and
    archived name — else None. Fields other than the arrays come from ``new``."""
    if (old.get("src") != new.get("src") or not _same_level(old, new)
            or old["lo_ns"] > new["hi_ns"] or new["lo_ns"] > old["hi_ns"]):
        return None
    if ("lo" in old) != ("lo" in new):
        return None
    out = dict(new)
    t, idx = np.unique(np.concatenate([old["t"], new["t"]]), return_index=True)
    for k in ("v", "lo", "hi"):
        if k in new:
            out[k] = np.concatenate([old[k], new[k]])[idx]
    out.update(t=t, units=new.get("units") or old.get("units", ""),
               lo_ns=min(old["lo_ns"], new["lo_ns"]),
               hi_ns=max(old["hi_ns"], new["hi_ns"]))
    return out


def _drop_stale_parts(days: dict) -> None:
    """Remove the part days a month file no longer needs: older than
    RECENT_DAYS, or whose whole day is stored by now."""
    oldest = time.time() * 1e9 - RECENT_DAYS * 86_400 * 10**9
    for d in [d for d in days if d.endswith(PART_SUFFIX)]:
        ent = days[d]
        if (ent.get("day_lo") or 0) < oldest or d[:-len(PART_SUFFIX)] in days:
            del days[d]


def _to_samples(ent: dict) -> list:
    t, v = ent["t"], ent["v"]
    lo, hi = ent.get("lo"), ent.get("hi")
    units = ent.get("units", "")
    meta = {"units": units}
    # The sample before the day comes first, as an archiver answer has it.
    head = []
    pre = ent.get("pre")
    if pre and pre[1] is not None:
        s = {"time": int(pre[0]), "value": float(pre[1]), "metaData": meta,
             "quality": "Original"}
        if pre[2] is not None and pre[3] is not None:
            s.update(minimum=float(pre[2]), maximum=float(pre[3]),
                     quality="Interpolated")
        head.append(s)
    if lo is None:
        return head + [{"time": int(a), "value": float(b), "quality": "Original",
                        "metaData": meta} for a, b in zip(t.tolist(), v.tolist())]
    return head + [{"time": int(a), "value": float(b), "minimum": float(c),
                    "maximum": float(d), "quality": "Interpolated", "metaData": meta}
                   for a, b, c, d in zip(t.tolist(), v.tolist(), lo.tolist(), hi.tolist())]


def _num(x):
    """A plain number from an archiver value (a one-element list is unwrapped)."""
    if isinstance(x, list):
        if len(x) != 1:
            return None
        x = x[0]
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        return float(x)
    return None


def _from_samples(samples, lo_ns: int, hi_ns: int):
    """Arrays for the samples inside [lo, hi), or None when one is not a number."""
    t, v, lo, hi = [], [], [], []
    has_mm = False
    units = ""
    pre = None                       # the newest numeric sample before the day
    for s in samples:
        ts = s.get("time")
        if ts is not None and ts < lo_ns and (pre is None or ts > pre["time"]):
            if _num(s.get("value")) is not None:
                pre = s
        if ts is None or not (lo_ns <= ts < hi_ns):
            continue
        val = _num(s.get("value"))
        if val is None:
            return None
        mn, mx = _num(s.get("minimum")), _num(s.get("maximum"))
        if mn is not None and mx is not None:
            has_mm = True
        t.append(int(ts))
        v.append(val)
        lo.append(val if mn is None else mn)
        hi.append(val if mx is None else mx)
        if not units:
            units = (s.get("metaData") or {}).get("units", "") or ""
    ent = {"t": np.asarray(t, dtype=np.int64), "v": np.asarray(v, dtype=np.float64),
           "units": units}
    if has_mm:
        ent["lo"] = np.asarray(lo, dtype=np.float64)
        ent["hi"] = np.asarray(hi, dtype=np.float64)
    # Kept with the day so a load starting on it has the value before its
    # start without asking the archiver (that look-back held the bar at 96 %
    # for up to 20 s on a cached load).
    if pre is not None:
        ent["pre"] = [int(pre["time"]), _num(pre.get("value")),
                      _num(pre.get("minimum")), _num(pre.get("maximum"))]
    return ent


def _write_month_file(path: Path, days: dict) -> None:
    arrays = {}
    meta = {"version": FORMAT_VERSION, "days": {}}
    for day, ent in sorted(days.items()):
        arrays[f"{day}_t"] = ent["t"]
        arrays[f"{day}_v"] = ent["v"]
        if ent.get("lo") is not None:
            arrays[f"{day}_lo"] = ent["lo"]
            arrays[f"{day}_hi"] = ent["hi"]
        meta["days"][day] = {k: ent.get(k) for k in
                             ("count", "src", "units", "lo_ns", "hi_ns", "saved",
                              "density", "day_lo", "pre")}
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.array(json.dumps(meta)), **arrays)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)
