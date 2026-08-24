"""Shared PV-list storage on the scratch network share.

The PV Monitor's config file is resolved relative to APP_DIR (see
cpva_api.get_app_dir), so every copy of the app -- the one on this PC and the
one deployed to \\\\hapls-share...\\scratch\\Software\\Diagnostic -- used to keep
its own PV list. Launching from the share therefore showed no PVs at all. This
module puts the PV list on the share instead, so any copy loads the same list.

The payload carries the PV list (with each PV's thresholds, profiles and valid
range) and a block of shared settings — poll pacing, debounce/settle, learn and
valid-range defaults, watchdog, graph window — so a limit set on one PC applies
everywhere and nobody has to re-enter it.

Two things deliberately do NOT travel over the share, and the caller is the one
that filters them out (see monitor_tab.shared_settings_subset):

* per-PC plumbing (the share path/timeout and the resolved-root cache), which
  would otherwise point every PC at whatever leg one of them used;
* the notification channels. Their secrets are DPAPI blobs bound to one Windows
  account on one machine (secrets_util.resolve_secret returns "" on a failed
  decrypt, and it does so silently), so sharing them would quietly break
  alerting everywhere else — and the plaintext has no business sitting on an
  open scratch share. Channels are provisioned by the build instead, see
  notify_provision.py.

Qt-free and stdlib-only on purpose: it stays unit-testable and remote_launcher.py
can import it without dragging in PySide6.

Two hard-won constraints shape the API:

* An unreachable UNC host takes ~48 s to fail a single os.path.isdir(). Every
  entry point here is therefore bounded by a timeout and does its blocking work
  on a DAEMON thread. Daemon specifically: concurrent.futures workers are joined
  at interpreter exit and QThreadPool waits in its destructor, so a thread stuck
  in that 48 s call would delay application *shutdown* by 48 s. A daemon thread
  can simply be abandoned.
* Writes are atomic (temp file + os.replace). monitor_tab.load_config() swallows
  a parse error and falls back to an empty PV list, so a half-written shared file
  would silently look like "no PVs configured".
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Probed CONCURRENTLY, but the winner is the first reachable entry in THIS
# order -- never "whichever answered first". A machine that can reach both legs
# must always pick the same file, or the two sites silently diverge.
#
# NOTE: these two names resolve to different IPs (10.56.50.103 vs 10.72.0.249).
# They are believed to be one NAS with a leg on each network. If that turns out
# to be wrong, drop one entry rather than letting two lists drift apart.
SHARE_CANDIDATES: tuple[str, ...] = (
    r"\\hapls-share.cs.eli-beams.eu\scratch\Software",   # office leg
    r"\\hapls-share.lcs.local\scratch\Software",         # lab leg
)

SHARED_SUBDIR = "Diagnostic"
# Deliberately NOT monitor_config.json: <Software>\Diagnostic is the share
# copy's own APP_DIR, so that name is already taken by its local config and
# would be overwritten by its own save.
SHARED_FILENAME = "monitor_pvs_shared.json"
BACKUP_FILENAME = "monitor_pvs_shared.bak.json"

DEFAULT_TIMEOUT_S = 3.0
PAYLOAD_VERSION = 1


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def candidate_roots(override: str = "") -> list[str]:
    """Roots to consider, in priority order. A user override wins outright."""
    override = (override or "").strip()
    if override:
        return [override]
    return list(SHARE_CANDIDATES)


def shared_file_for(root: str) -> Path:
    """Full path of the shared PV file under ``root``.

    Accepts either a folder or a full .json path, and tolerates a root that
    already ends in the Diagnostic subfolder so the path never doubles up.
    """
    p = Path(str(root).strip())
    if p.suffix.lower() == ".json":
        return p
    if p.name.lower() != SHARED_SUBDIR.lower():
        p = p / SHARED_SUBDIR
    return p / SHARED_FILENAME


def _first_reachable(roots: list[str], timeout_s: float) -> str:
    """Highest-priority reachable root, or "" if none answered in time.

    One daemon thread per root, so a dead host never serialises in front of a
    live one. The decision is made as soon as it *can* be: the moment the first
    entry in priority order is known reachable we return, without waiting for
    the slow legs. Waiting for every probe would cost the full timeout on any
    machine that can't see one of the hosts -- i.e. on every start.

    Only when a higher-priority root is still undecided do we wait, because it
    might yet win. Threads left running past the deadline are abandoned (see the
    module docstring on why they're daemons).
    """
    state: dict[str, bool] = {}
    cond = threading.Condition()

    def probe(root: str):
        try:
            ok = os.path.isdir(root)
        except OSError:
            ok = False
        with cond:
            state[root] = ok
            cond.notify_all()

    for root in roots:
        threading.Thread(target=probe, args=(root,), daemon=True,
                         name="pv-share-probe").start()

    deadline = time.monotonic() + max(0.1, timeout_s)
    with cond:
        while True:
            undecided = False
            for root in roots:
                if root not in state:
                    undecided = True     # a preferred root may still win
                    break
                if state[root]:
                    return root
            if not undecided:
                return ""                # every root answered, none reachable
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            cond.wait(remaining)
        # Out of time: settle for whatever did answer, still in priority order.
        for root in roots:
            if state.get(root):
                return root
    return ""


def resolve_root(override: str = "", cached_root: str = "",
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[str, str]:
    """Pick the share root to use.

    Returns (root_or_empty, source) with source in
    {"override", "cache", "probe", "none"}.

    The cached root is tried alone first, so the common case costs exactly one
    reachable-host isdir() (measured 7 ms) and never touches the dead leg.
    """
    roots = candidate_roots(override)
    cached_root = (cached_root or "").strip()
    src = "override" if (override or "").strip() else ""

    if cached_root and cached_root in roots:
        if _first_reachable([cached_root], timeout_s):
            return cached_root, (src or "cache")

    root = _first_reachable(roots, timeout_s)
    if root:
        return root, (src or "probe")
    return "", "none"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SharedResult:
    root: str                       # "" when nothing was reachable
    path: Optional[Path]            # file read, or the one we would write
    pvs: Optional[list]             # None == READ FAILED -> caller must not write
    existed: bool
    source: str                     # override | cache | probe | none | timeout
    detail: str                     # one human-readable line for the log
    mtime: Optional[float] = None   # of the file as read, for conflict detection
    # Shared settings block as found in the file ({} when the file predates it).
    # None whenever `pvs` is None: nothing was read, so nothing can be adopted.
    settings: Optional[dict] = None


def read_shared(path: Path) -> tuple[list, dict]:
    """Parse and validate a shared file, returning (pvs, settings).

    Raises ValueError on anything malformed instead of degrading to an empty
    list -- the caller has to be able to tell "deliberately empty" from
    "unreadable", which is exactly the distinction monitor_tab.load_config()
    cannot make.

    A missing or non-object ``settings`` block is not an error: files written by
    older versions have none, and a session with no shared settings simply keeps
    its own.
    """
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("top level is not a JSON object")
    pvs = payload.get("pvs")
    if not isinstance(pvs, list):
        raise ValueError("'pvs' is missing or not a list")
    for i, entry in enumerate(pvs):
        if not isinstance(entry, dict):
            raise ValueError(f"pvs[{i}] is not an object")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"pvs[{i}] has no usable 'name'")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    return pvs, settings


def load_shared(override: str = "", cached_root: str = "",
                timeout_s: float = DEFAULT_TIMEOUT_S) -> SharedResult:
    """Resolve the share and read the PV list. Never raises.

    The whole resolve+read runs on one daemon thread under a single deadline,
    not just the probe: open()/json.load() against a share that dies mid-read
    blocks for just as long as an isdir() on a dead host.
    """
    box: dict = {}
    done = threading.Event()

    def work():
        try:
            box["result"] = _load_shared_blocking(override, cached_root, timeout_s)
        except Exception as e:  # noqa: BLE001 - must never escape into startup
            box["result"] = SharedResult(
                root="", path=None, pvs=None, existed=False, source="none",
                detail=f"shared PV list failed unexpectedly: {e}")
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name="pv-share-load").start()
    # Budget: the inner probe may already consume timeout_s, so allow the read
    # a comparable slice on top rather than cutting it off at the probe's own
    # deadline.
    if not done.wait(max(0.5, timeout_s) * 2):
        return SharedResult(
            root="", path=None, pvs=None, existed=False, source="timeout",
            detail=(f"the network share did not answer within "
                    f"{timeout_s:g} s"))
    return box["result"]


def _load_shared_blocking(override: str, cached_root: str,
                          timeout_s: float) -> SharedResult:
    root, source = resolve_root(override, cached_root, timeout_s)
    if not root:
        tried = ", ".join(candidate_roots(override))
        return SharedResult(
            root="", path=None, pvs=None, existed=False, source="none",
            detail=f"no reachable network share (tried: {tried})")

    path = shared_file_for(root)
    if not path.exists():
        return SharedResult(root=root, path=path, pvs=None, existed=False,
                            source=source,
                            detail=f"shared PV list does not exist yet: {path}")
    # Stamp the mtime BEFORE reading, so a write that lands between the two is
    # seen as a conflict later rather than being silently adopted as our own.
    mtime = _mtime_or_none(path)
    try:
        pvs, settings = read_shared(path)
    except Exception as e:  # noqa: BLE001
        return SharedResult(root=root, path=path, pvs=None, existed=True,
                            source=source, mtime=mtime,
                            detail=f"shared PV list at {path} is unusable: {e}")
    return SharedResult(root=root, path=path, pvs=pvs, existed=True,
                        source=source, mtime=mtime, settings=settings,
                        detail=f"read {len(pvs)} PV(s) and "
                               f"{len(settings)} shared setting(s) from {path}")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_json_atomic(path: Path, obj) -> None:
    """Write ``obj`` as JSON so readers only ever see the old or new file.

    The temp file must sit in the destination directory: os.replace is only
    atomic within one volume.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass          # fsync is unreliable over SMB; never fatal here
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def backup_once(path: Path) -> None:
    """Keep a copy of the current shared file next to it. Best-effort."""
    path = Path(path)
    if not path.exists():
        return
    try:
        shutil.copy2(path, path.with_name(BACKUP_FILENAME))
    except Exception:  # noqa: BLE001 - a missing backup must not block the write
        pass


def _mtime_or_none(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def write_shared(path: Path, pv_dicts: list, settings: Optional[dict] = None,
                 host: str = "",
                 expect_mtime: Optional[float] = None) -> tuple[Optional[float], bool]:
    """Publish ``pv_dicts`` (and the shared ``settings``) to the shared file.

    ``settings`` must already be filtered by the caller — this function does not
    know which keys are per-PC or secret (see the module docstring).

    Returns (new_mtime, was_stale). was_stale means the file changed since the
    caller last read or wrote it, i.e. another PC published in the meantime.
    Policy is last-writer-wins, so this still writes -- but the caller logs the
    flag so a clobber is never silent, and backup_once keeps the loser.
    """
    path = Path(path)
    was_stale = False
    if expect_mtime is not None:
        current = _mtime_or_none(path)
        was_stale = current is not None and current != expect_mtime

    write_json_atomic(path, {
        "version": PAYLOAD_VERSION,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "written_by": host,
        "pvs": pv_dicts,
        "settings": dict(settings or {}),
    })
    return _mtime_or_none(path), was_stale
