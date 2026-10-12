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
  * Only whole Prague days that ended more than an hour ago are stored; today is
    always read live.
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
        self._root: Path | None = None
        self._resolved = threading.Event()
        self.hits = 0
        self.stored = 0
        env = os.environ.get(_ENV_OVERRIDE, "")
        if root is None and env:
            root = env
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
        months = sorted({self.day_key(lo, tz)[:7] for lo, _hi in days})
        todo = [(ch, m) for ch in channels for m in months
                if (ch, m) not in self._loaded]
        if not todo:
            return 0

        def _one(key):
            ch, m = key
            return key, self._read_month_file(self._month_path(ch, m))

        found = 0
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(todo)))) as ex:
            for key, days_map in ex.map(_one, todo):
                with self._lock:
                    self._months[key] = days_map
                    self._loaded.add(key)
                found += bool(days_map)
        return found

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
                        if day in mine:
                            merged[day] = mine[day]
                    self._months[(ch, month)] = {**merged, **mine}
                _write_month_file(path, merged)
                written += len(days)
            except Exception as exc:
                self._note(f"day cache: could not save {ch} {month} ({exc})")
        self.stored += written
        return written

    def flush_async(self):
        """:meth:`flush` in a daemon thread, so a load never waits for the share."""
        if self._root is None or not self._dirty_set:
            return
        threading.Thread(target=self.flush, daemon=True, name="cpva-cache-save").start()


def _to_samples(ent: dict) -> list:
    t, v = ent["t"], ent["v"]
    lo, hi = ent.get("lo"), ent.get("hi")
    units = ent.get("units", "")
    meta = {"units": units}
    if lo is None:
        return [{"time": int(a), "value": float(b), "quality": "Original",
                 "metaData": meta} for a, b in zip(t.tolist(), v.tolist())]
    return [{"time": int(a), "value": float(b), "minimum": float(c), "maximum": float(d),
             "quality": "Interpolated", "metaData": meta}
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
    for s in samples:
        ts = s.get("time")
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
                             ("count", "src", "units", "lo_ns", "hi_ns", "saved")}
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.array(json.dumps(meta)), **arrays)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)
