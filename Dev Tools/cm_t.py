# cm_t.py
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import configparser
import sys
import time
import os

# ---------------- CONFIG ----------------
# cm_t.py žije v L3-QoL-JanJan/Dev Tools/ po přesunu do gitu.
# _app_dir() vrátí L3-QoL-JanJan/Dev Tools/  (frozen i source)
# Zdrojáky ikon:  L3-QoL-JanJan/           = _app_dir().parent
# Dist / exe:     programy/dist/            = _app_dir().parent.parent / "dist"
# ---------------- USER CONFIG (shared with b_t.py) ----------------
_CONFIG_PATH = Path(os.environ.get("APPDATA", "~")) / "DevTools" / "config.json"

def _load_devtools_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_devtools_config(data: dict):
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass

def _get_destination_roots() -> list[tuple[str, Path]]:
    cfg = _load_devtools_config()
    roots = []
    scratch = cfg.get("scratch")
    sharepoint = cfg.get("sharepoint")
    if scratch:
        roots.append(("Scratch", Path(scratch)))
    if sharepoint:
        roots.append(("Sharepoint", Path(sharepoint)))
    return roots

def _src_root() -> Path:
    """Kořen git repozitáře s py zdrojáky (L3-QoL-JanJan/)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        cfg = _load_devtools_config()
        src = cfg.get("src_root")
        if src:
            return Path(src)
    return Path(__file__).resolve().parent.parent

def _dist_root() -> Path:
    cfg = _load_devtools_config()
    override = cfg.get("dist_root")
    if override:
        return Path(override)
    return _programs_root().parent / "dist"

def _internal_builder_dist() -> Path:
    r"""C:\Dev\dist\_internal_builder — lokální (mimo OneDrive), nebo fallback na programy/dist."""
    local = Path(r"C:\Dev\dist") / "_internal_builder"
    if local.exists():
        return local
    return _dist_root() / "Internal Builder"

def _programs_root() -> Path:
    """Root folder containing all program source subfolders.
    Priority: 1) %APPDATA%\\DevTools\\config.json  2) builder_settings.json  3) source fallback.
    """
    # 1. Per-user config (set via Set paths or synced from Builder)
    cfg = _load_devtools_config()
    root = cfg.get("root_folder")
    if root and Path(root).exists():
        return Path(root)
    # 2. builder_settings.json next to exe / source file
    try:
        _exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        settings_path = _exe_dir / "builder_settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            root = data.get("root_folder")
            if root and Path(root).exists():
                return Path(root)
    except Exception:
        pass
    # 3. Fallback: L3-QoL-JanJan/ (parent of Dev Tools/)
    return Path(__file__).resolve().parent.parent


def find_helper_programs(program_dirs: list[Path]) -> dict[str, list[str]]:
    """Helper exes per program folder, read from each folder's build_config.json.

    Same source as the builder's expander (`extra_exes`), so nothing has to be
    registered here: a program has helpers exactly when its build_config.json
    lists them.

    A helper is built into its OWN dist folder under its own name, which makes it
    a program like any other as far as the copy is concerned — it only lacks a
    source folder, and that is precisely why it never appeared on this list and
    was reported as "NOT copied — not selected in Copy Manager".
    """
    out: dict[str, list[str]] = {}
    for p in program_dirs:
        cfg_path = p / "build_config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        names: list[str] = []
        for spec in (cfg.get("extra_exes") or []):
            if isinstance(spec, str):
                spec = {"script": spec}
            script = p / spec.get("script", "")
            if not script.exists():
                continue
            name = (spec.get("name") or script.stem).strip()
            if name and name not in names:
                names.append(name)
        if names:
            out[p.name] = names
    return out


def _get_scratch_root() -> Path | None:
    cfg = _load_devtools_config()
    s = cfg.get("scratch")
    return Path(s) if s else None

def _versions_txt_path() -> Path | None:
    r = _get_scratch_root()
    return (r / "Versions.txt") if r else None

def read_versions_txt() -> dict[str, str]:
    p = _versions_txt_path()
    if not p or not p.exists():
        return {}
    result = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                name, _, ver = line.partition("=")
                result[name.strip()] = ver.strip()
    except Exception:
        pass
    return result

def write_version_to_txt(program_name: str, version: str):
    p = _versions_txt_path()
    if not p:
        return
    versions = read_versions_txt()
    versions[program_name] = version if version.startswith("v") else f"v{version}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k} = {v}" for k, v in sorted(versions.items())]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not write Versions.txt: {e}")

INTERNAL_BUILDER_DIST = _internal_builder_dist()
VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_VERSION_LOOSE_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
# Archived-style exe name, e.g. "Image Tools v2.5.2__20260526_090829.exe"
TIMESTAMPED_EXE_RE = re.compile(r"^.+__\d{8}_\d{6}\.exe$", re.IGNORECASE)

# Folders an app reads at runtime from next to its exe (Announcer/images,
# Announcer/sounds). Kept in sync with the auto-included list in b_t.py.
# They are never deleted on a destination, and when a build does not carry them
# the deploy takes them straight from the source tree instead.
ASSET_DIR_NAMES = ("images", "sounds", "assets", "icons", "img", "audio",
                   "fonts", "templates")


def _fmt_elapsed(seconds: float) -> str:
    """Seconds as "4.5s" or "2m 38s" — the form used in every end-of-run report."""
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{seconds:.1f}s"


def _dir_has_files(d: Path) -> bool:
    """True if the folder holds at least one file (at any depth)."""
    try:
        return any(f.is_file() for f in d.rglob("*"))
    except OSError:
        return False


def _merge_dir(src: Path, dst: Path) -> tuple[int, int, list[str]]:
    """Copy src over dst file by file, WITHOUT deleting anything first.

    The previous version deleted the destination folder and re-created it from
    the build (rmtree + copytree). Two ways that lost data for good: a build
    whose images/ was empty replaced a good folder with an empty one, and a copy
    that failed half way left the folder wiped with nothing put back. Merging
    can only add or overwrite, so the worst case is a stale leftover file.

    Returns (copied, failed, error messages).
    """
    copied = failed = 0
    errors: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part in ("__pycache__",) for part in rel.parts):
            continue
        target = dst / rel
        try:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if item.name.lower() == "thumbs.db":
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
        except Exception as e:
            failed += 1
            errors.append(f"{rel}: {e}")
    return copied, failed, errors


def _exe_version(p: Path) -> tuple[int, int, int]:
    """Version tuple parsed from an exe filename, for sorting; (0,0,0) if none."""
    m = VERSION_RE.search(p.stem)
    return tuple(map(int, m.groups())) if m else (0, 0, 0)
README_PREFIX = "ReadMe_"
README_NAME = "ReadMe.txt"

STATE_FILE_NAME = "copy_manager_state.ini"
STATE_SECTION = "deployed"  # keys: <program_name> = vX.Y.Z


# ---------------- STATE ----------------
def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _icon_app_id(prefix, ico_path):
    """Taskbar identity for `prefix`, tagged with the icon file's own content.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so a fixed id that was once seen without an icon keeps drawing the
    generic placeholder for good (measured on Diagnostic, 2026-08-24: same
    program, same icon, only the id changed -> old id generic, fresh id
    correct). Hashing the icon into the id makes every PC derive the same id
    from the same picture, and retires the old id by itself the day the icon
    is redrawn -- no hand-bumped ".2" suffixes, no per-machine icon-cache
    clearing. Returns None when the icon cannot be read; the caller then sets
    no id at all rather than burning a content id on a run that has no picture
    to give it. The same helper sits in every program here.
    """
    if not ico_path:
        return None
    try:
        import hashlib
        with open(ico_path, "rb") as fh:
            return f"{prefix}.{hashlib.sha1(fh.read()).hexdigest()[:12]}"
    except OSError:
        return None


def set_app_icon(win, ico_path: str, app_id: str | None = None) -> None:
    """Apply icon.ico to the title bar AND the Windows taskbar button.

    tkinter's iconbitmap only sets the title bar icon; the Windows 11 taskbar
    reads the small icon slots + window-class icon, which Tk leaves as its
    default feather. We force every slot from icon.ico via Win32.
    """
    import ctypes
    _aumid = _icon_app_id(app_id, ico_path) if app_id else None
    if _aumid:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_aumid)
        except Exception:
            pass
    try:
        win.iconbitmap(default=ico_path)
    except Exception:
        pass
    try:
        u = ctypes.windll.user32
        hwnd = u.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        big = u.LoadImageW(None, ico_path, 1, 0, 0, 0x10 | 0x40)
        sm  = u.LoadImageW(None, ico_path, 1, 16, 16, 0x10)
        for which, h in ((1, big), (0, sm), (2, sm)):
            if h:
                u.SendMessageW(hwnd, 0x0080, which, h)
        set_cls = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
        if big:
            set_cls(hwnd, -14, big)
        if sm:
            set_cls(hwnd, -34, sm)
    except Exception:
        pass


def _state_path() -> Path:
    return _app_dir() / STATE_FILE_NAME


def load_state() -> dict[str, str]:
    p = _state_path()
    if not p.exists():
        return {}
    cfg = configparser.ConfigParser()
    try:
        cfg.read(p, encoding="utf-8")
    except Exception:
        try:
            cfg.read(p)
        except Exception:
            return {}
    if not cfg.has_section(STATE_SECTION):
        return {}
    out = {}
    for k, v in cfg.items(STATE_SECTION):
        out[k] = (v or "").strip()
    return out


def save_state(state: dict[str, str]) -> None:
    cfg = configparser.ConfigParser()
    cfg[STATE_SECTION] = {}
    for k, v in state.items():
        cfg[STATE_SECTION][k.lower()] = v
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            cfg.write(f)
    except Exception:
        pass


# ---------------- LOCK / ARCHIVE HELPERS ----------------
def _is_locked_winerror32(e: BaseException) -> bool:
    return getattr(e, "winerror", None) == 32


def _try_move(src: Path, dst: Path) -> tuple[bool, str | None]:
    try:
        src.rename(dst)
    except OSError:
        try:
            shutil.move(str(src), str(dst))
        except (PermissionError, OSError) as e:
            if _is_locked_winerror32(e):
                return False, "locked"
            raise
    except (PermissionError, OSError) as e:
        if _is_locked_winerror32(e):
            return False, "locked"
        raise
    return True, None

# ---------------- LOGIC ----------------
def parse_version(s: str):
    m = _VERSION_LOOSE_RE.fullmatch((s or "").strip())
    return tuple(map(int, m.groups())) if m else None


def version_tuple_to_str(vt: tuple[int, int, int]) -> str:
    return f"v{vt[0]}.{vt[1]}.{vt[2]}"


def list_versions(dist_dir: Path) -> list[Path]:
    """Return version folders sorted by version DESC."""
    if not dist_dir.exists():
        return []
    items = []
    for p in dist_dir.iterdir():
        if p.is_dir():
            v = parse_version(p.name)
            if v:
                items.append((v, p))
    items.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in items]


def find_exe_in_folder(version_folder: Path, program_name: str, version_name: str) -> Path:
    # --onedir: exe je ve podsložce version_folder/{name} v{ver}/
    exes = sorted(version_folder.glob("*.exe"))
    if not exes:
        # zkus podsložky (--onedir layout)
        exes = sorted(version_folder.rglob("*.exe"))
    if not exes:
        raise FileNotFoundError(f"No .exe found in: {version_folder}")

    ver = version_name.lstrip("v")  # "v1.2.3" -> "1.2.3"

    exact = version_folder / f"{program_name} v{ver}.exe"
    if exact.exists():
        return exact

    for e in exes:
        stem_lower = e.stem.lower()
        ver_token = f"v{ver}".lower()
        # musí být celé slovo/token, ne prefix jiné verze
        if re.search(r'(?<!\d)' + re.escape(ver_token) + r'(?!\d)', stem_lower):
            return e

    for e in exes:
        if e.stem.lower() == program_name.lower():
            return e

    return exes[0]


def unique_path(p: Path) -> Path:
    """If path exists, create 'name (2).ext', 'name (3).ext', ..."""
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    i = 2
    while True:
        cand = p.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1

def _load_ico_as_photoimage(path: Path, size: int = 64):
    """Načte .ico soubor a vrátí tk.PhotoImage. Vyžaduje Pillow."""
    from PIL import Image, ImageTk
    img = Image.open(path)
    img = img.convert("RGBA")
    img = img.resize((size, size), Image.LANCZOS)
    return ImageTk.PhotoImage(img)

def show_icon_compare_dialog(parent, program_name: str, existing: Path, incoming: Path) -> bool:
    """
    Zobrazí porovnávací okno dvou ikon.
    Vrací True = nahradit, False = přeskočit.
    """
    result = {"replace": False}

    win = tk.Toplevel(parent)
    win.title(f"Icon conflict – {program_name}")
    win.resizable(False, False)
    win.grab_set()

    try:
        img_existing = _load_ico_as_photoimage(existing, size=128)
        img_incoming = _load_ico_as_photoimage(incoming, size=128)
        load_ok = True
    except Exception as e:
        load_ok = False
        err_msg = str(e)

    ttk.Label(win, text=f"Program:  {program_name}", font=("Segoe UI", 10, "bold")).pack(pady=(12, 2))
    ttk.Label(win, text="Icon already exists with a different file size. Replace it?").pack(pady=(0, 10))

    img_frame = ttk.Frame(win)
    img_frame.pack(padx=20, pady=(0, 10))

    left_box = ttk.LabelFrame(img_frame, text=f"Existing  ({existing.stat().st_size} B)")
    left_box.grid(row=0, column=0, padx=(0, 20))

    right_box = ttk.LabelFrame(img_frame, text=f"Incoming  ({incoming.stat().st_size} B)")
    right_box.grid(row=0, column=1)

    if load_ok:
        lbl_l = ttk.Label(left_box, image=img_existing)
        lbl_l.image = img_existing          # prevent GC
        lbl_l.pack(padx=8, pady=8)

        lbl_r = ttk.Label(right_box, image=img_incoming)
        lbl_r.image = img_incoming
        lbl_r.pack(padx=8, pady=8)
    else:
        ttk.Label(left_box,  text="(preview unavailable)", width=18).pack(padx=8, pady=8)
        ttk.Label(right_box, text="(preview unavailable)", width=18).pack(padx=8, pady=8)
        ttk.Label(win, text=f"Pillow error: {err_msg}", foreground="red").pack()

    ttk.Label(win, text=f"Existing:  {existing}",  foreground="gray").pack(anchor="w", padx=20)
    ttk.Label(win, text=f"Incoming:  {incoming}", foreground="gray").pack(anchor="w", padx=20, pady=(0, 10))

    btn_frame = ttk.Frame(win)
    btn_frame.pack(pady=(0, 14))

    def on_replace():
        result["replace"] = True
        win.destroy()

    def on_skip():
        win.destroy()

    ttk.Button(btn_frame, text="Keep existing", width=14, command=on_skip).pack(side="left", padx=6)
    ttk.Button(btn_frame, text="Replace", width=14, command=on_replace).pack(side="left", padx=6)

    win.wait_window()
    return result["replace"]

def _fix_archive_dir(archive_dir: Path, log_fn=None):
    """Move any flat files in archive/ into vX.Y.Z/ subfolders.
    Handles: versioned+timestamp ('Name vX.Y.Z__TS.exe'), version-only ('Name vX.Y.Z.exe'),
    and timestamp-only py files matched to a versioned exe.
    Per version with timestamps: keeps only the LATEST build, deletes older ones.
    """
    import re as _re
    from collections import defaultdict as _dd

    _VERSIONED_TS_RE = _re.compile(r"^.+\s+(v\d+\.\d+\.\d+)__(\d{8}_\d{6})\.", _re.IGNORECASE)
    _TS_ONLY_RE      = _re.compile(r"^.+__(\d{8}_\d{6})\.")

    flat_files = [f for f in archive_dir.iterdir()
                  if f.is_file() and f.suffix.lower() not in (".txt", ".log")]
    if not flat_files:
        return

    # Two passes so ts_to_version is populated before processing ts-only files
    ts_builds: dict = _dd(lambda: _dd(list))  # ver -> ts -> [files]
    ver_only:  dict = _dd(list)               # ver -> [files]
    ts_to_ver: dict = {}
    unmatched = []

    for f in flat_files:
        m = _VERSIONED_TS_RE.match(f.name)
        if m:
            ver, ts = m.group(1).lower(), m.group(2)
            ts_builds[ver][ts].append(f)
            ts_to_ver[ts] = ver
            continue
        vm = VERSION_RE.search(f.stem)
        if vm:
            ver_only[vm.group(0).lower()].append(f)
            continue
        unmatched.append(f)

    # Match ts-only files (e.g. if_t__20260407_081206.py) to a version via timestamp
    still_unmatched = []
    for f in unmatched:
        m = _TS_ONLY_RE.match(f.name)
        if m and m.group(1) in ts_to_ver:
            ts_builds[ts_to_ver[m.group(1)]][m.group(1)].append(f)
        else:
            still_unmatched.append(f)

    if not ts_builds and not ver_only:
        return

    changed = False
    program_name = archive_dir.parent.name

    if log_fn:
        log_fn(f"[{program_name}] Fixing archive...")

    # Versioned+timestamp: keep latest per version, delete older builds
    for ver, ts_dict in sorted(ts_builds.items()):
        sorted_ts = sorted(ts_dict.keys(), reverse=True)
        latest_ts, older_ts = sorted_ts[0], sorted_ts[1:]
        ver_dir = archive_dir / ver
        ver_dir.mkdir(exist_ok=True)
        for f in ts_dict[latest_ts]:
            dst = unique_path(ver_dir / f.name)
            try:
                shutil.move(str(f), str(dst))
                if log_fn: log_fn(f"[{program_name}]   {f.name} -> {ver}/")
                changed = True
            except Exception as e:
                if log_fn: log_fn(f"[{program_name}]   WARN: {f.name}: {e}")
        for ts in older_ts:
            for f in ts_dict[ts]:
                try:
                    f.unlink()
                    if log_fn: log_fn(f"[{program_name}]   deleted older build: {f.name}")
                    changed = True
                except Exception as e:
                    if log_fn: log_fn(f"[{program_name}]   WARN delete: {f.name}: {e}")

    # Version-only files: just move to subfolder
    for ver, files in sorted(ver_only.items()):
        ver_dir = archive_dir / ver
        ver_dir.mkdir(exist_ok=True)
        for f in files:
            dst = unique_path(ver_dir / f.name)
            try:
                shutil.move(str(f), str(dst))
                if log_fn: log_fn(f"[{program_name}]   {f.name} -> {ver}/")
                changed = True
            except Exception as e:
                if log_fn: log_fn(f"[{program_name}]   WARN: {f.name}: {e}")

    # Unversioned/unmatched: move to unknown/
    if still_unmatched:
        unk_dir = archive_dir / "unknown"
        unk_dir.mkdir(exist_ok=True)
        for f in still_unmatched:
            dst = unique_path(unk_dir / f.name)
            try:
                shutil.move(str(f), str(dst))
                if log_fn: log_fn(f"[{program_name}]   {f.name} -> unknown/")
                changed = True
            except Exception as e:
                if log_fn: log_fn(f"[{program_name}]   WARN: {f.name}: {e}")

    if changed and log_fn:
        log_fn(f"[{program_name}]   archive OK")


def _reunite_unknown_helpers(archive_dir: Path, log_fn=None):
    """Move helper .py files stranded in archive/unknown/ back into their version folders.

    Helper modules (if_t.py, is_t.py, …) carry no version in their filename, so the
    deploy step used to drop every old copy into unknown/ (as 'if_t.py', 'if_t (2).py', …).
    Each copy was logged in unknown/archive_log.txt with the timestamp of the deploy that
    archived it; the matching version folder carries that same timestamp in its own
    archive_log.txt. We rebuild that link and move each copy back — keeping the ORIGINAL
    importable name so the version folder becomes a self-contained, runnable snapshot.

    The Nth physical copy of a name (unique_path order: base, ' (2)', ' (3)', …) matches
    the Nth timestamp for that name in chronological order.
    """
    from collections import defaultdict as _dd
    unk = archive_dir / "unknown"
    unk_log = unk / "archive_log.txt"
    if not unk.is_dir() or not unk_log.exists():
        return
    program_name = archive_dir.parent.name

    def _log(m):
        if log_fn:
            log_fn(f"[{program_name}] {m}")

    _TS_RE = re.compile(r"\d{8}_\d{6}")

    # timestamp -> version folder (from every vX.Y.Z/archive_log.txt); drop ambiguous ones
    ts_to_ver: dict[str, Path] = {}
    ambiguous: set[str] = set()
    for vdir in archive_dir.iterdir():
        if not vdir.is_dir() or vdir.name == "unknown":
            continue
        vlog = vdir / "archive_log.txt"
        if not vlog.exists():
            continue
        try:
            for line in vlog.read_text(encoding="utf-8").splitlines():
                ts = line.split("|", 1)[0].strip()
                if not _TS_RE.fullmatch(ts):
                    continue
                if ts in ts_to_ver and ts_to_ver[ts] != vdir:
                    ambiguous.add(ts)
                else:
                    ts_to_ver[ts] = vdir
        except Exception:
            continue
    if not ts_to_ver:
        return

    # unknown/archive_log.txt -> per-filename chronological timestamp list
    per_name_ts: dict[str, list[str]] = _dd(list)
    try:
        for line in unk_log.read_text(encoding="utf-8").splitlines():
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            ts, fname = parts[0].strip(), parts[1].strip()
            if _TS_RE.fullmatch(ts) and fname:
                per_name_ts[fname].append(ts)
    except Exception:
        return

    moved = skipped = 0
    for fname, ts_list in per_name_ts.items():
        ts_list = sorted(ts_list)  # chronological == unique_path assignment order
        stem, suf = Path(fname).stem, Path(fname).suffix
        phys = [unk / (fname if i == 1 else f"{stem} ({i}){suf}")
                for i in range(1, len(ts_list) + 1)]
        existing = [p for p in phys if p.exists()]
        if len(existing) != len(ts_list):
            _log(f"unknown: '{fname}' — {len(existing)} file(s) vs {len(ts_list)} log entries, skipping (ambiguous)")
            skipped += len(existing)
            continue
        for src, ts in zip(phys, ts_list):
            if ts in ambiguous or ts not in ts_to_ver:
                skipped += 1
                continue
            dst_dir = ts_to_ver[ts]
            dst = dst_dir / fname
            if dst.exists():
                _log(f"unknown: {src.name} -> {dst_dir.name}/ SKIP (exists)")
                skipped += 1
                continue
            ok, why = _try_move(src, dst)
            if ok:
                moved += 1
            else:
                _log(f"unknown: {src.name} -> {dst_dir.name}/ SKIP ({why})")
                skipped += 1

    if moved:
        _log(f"unknown: reunited {moved} helper file(s) with their version folders"
             + (f" ({skipped} skipped)" if skipped else ""))

    # tidy up: if no .py copies remain, drop the (now stale) unknown folder + its log
    try:
        if not any(p.suffix.lower() == ".py" for p in unk.iterdir()):
            unk_log.unlink(missing_ok=True)
            unk.rmdir()
            _log("unknown: emptied and removed")
    except Exception:
        pass


_DUP_SUFFIX_RE = re.compile(r"^(.*?) \((\d+)\)$")
_TS_SUFFIX_RE  = re.compile(r"^(.*?)__\d{8}_\d{6}$")


def _canonical_stem(stem: str) -> str:
    """Strip a '__YYYYMMDD_HHMMSS' timestamp and/or a ' (N)' dedup suffix off a
    filename stem, yielding the name the file should live under:
        'if_t__20260401_134341'      -> 'if_t'
        'Image Tools v2.5.4 (2)'     -> 'Image Tools v2.5.4'
        'Image Tools v2.5.4__2026..' -> 'Image Tools v2.5.4'
    """
    m = _TS_SUFFIX_RE.match(stem)
    if m:
        stem = m.group(1)
    m = _DUP_SUFFIX_RE.match(stem)
    if m:
        stem = m.group(1)
    return stem


def _dup_index(stem: str) -> int:
    m = _DUP_SUFFIX_RE.match(stem)
    return int(m.group(2)) if m else 0


def _normalize_version_folder_names(archive_dir: Path, log_fn=None):
    """Make every vX.Y.Z/ folder a clean, runnable snapshot.

    Timestamps are gone by design (Dev Tools no longer keeps them in the archive);
    the version lives in the filename ('Image Tools v2.5.4.exe') and in the folder
    name, which is what the Launcher now reads.

    Per version folder, files are grouped by their canonical name (timestamp and
    ' (N)' dedup suffix stripped). For each group the best copy is kept and renamed
    to the canonical name; redundant copies ('… (2).exe', stray timestamped twins)
    are deleted. Helper modules (if_t.py, …) keep their importable names so
    'import if_t' still resolves inside the folder.
    """
    from collections import defaultdict as _dd
    program_name = archive_dir.parent.name
    renamed = removed = 0

    for vdir in archive_dir.iterdir():
        if not vdir.is_dir() or vdir.name == "unknown":
            continue

        groups: dict[str, list[Path]] = _dd(list)
        for f in vdir.iterdir():
            if not f.is_file() or f.suffix.lower() in (".txt", ".log"):
                continue
            groups[_canonical_stem(f.stem) + f.suffix].append(f)

        for canon, files in groups.items():
            canon_path = vdir / canon
            # Keep the base copy: no ' (N)' suffix first, then the canonical
            # (non-timestamped) name, then lowest index — deterministic, size-agnostic.
            keeper = min(files, key=lambda p: (_dup_index(p.stem),
                                               0 if p.name == canon else 1,
                                               p.name.lower()))
            for f in files:
                if f == keeper:
                    continue
                try:
                    f.unlink()
                    removed += 1
                    if log_fn:
                        log_fn(f"[{program_name}]   {vdir.name}/ removed duplicate: {f.name}")
                except Exception as e:
                    if log_fn:
                        log_fn(f"[{program_name}]   WARN remove {f.name}: {e}")

            if keeper.name != canon and not canon_path.exists():
                ok, _why = _try_move(keeper, canon_path)
                if ok:
                    renamed += 1

    if log_fn and (renamed or removed):
        log_fn(f"[{program_name}] normalized {renamed} name(s), removed {removed} duplicate(s) in version folders")


def move_existing_exes_to_archive(target_dir: Path, keep_name: str, logs: list[str], program_name: str):
    """
    Move ALL *.exe except keep_name into archive/vX.Y.Z/ subfolder.
    If an exe is locked (WinError 32) -> SKIP and continue (archiving can happen later).
    """
    archive_dir = target_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for exe in target_dir.glob("*.exe"):
        if exe.name.lower() == keep_name.lower():
            continue

        src = exe
        _vm = VERSION_RE.search(src.stem)
        old_ver_label = _vm.group(0) if _vm else "unknown"
        ver_archive_dir = archive_dir / old_ver_label
        ver_archive_dir.mkdir(parents=True, exist_ok=True)
        dst = unique_path(ver_archive_dir / src.name)

        moved, why = _try_move(src, dst)
        if moved:
            _arch_log = ver_archive_dir / "archive_log.txt"
            try:
                with _arch_log.open("a", encoding="utf-8") as _f:
                    _f.write(f"{timestamp} | {src.name}\n")
            except Exception:
                pass
            logs.append(f"[{program_name}] Archived -> {dst}")
        else:
            logs.append(f"[{program_name}] SKIP archive (locked) -> {src}")


def _normalize_name(s: str) -> str:
    return s.lower().replace("_", "").replace(" ", "")


def find_readme_or_raise(program_dir: Path, program_name: str) -> Path:
    target = _normalize_name(f"{README_PREFIX}{program_name}")

    for f in program_dir.iterdir():
        if f.is_file() and _normalize_name(f.stem) == target:
            return f

    for cand in (README_NAME, "README.md", "README.txt", "ReadMe.md"):
        p = program_dir / cand
        if p.exists() and p.is_file():
            return p

    raise FileNotFoundError(
        f"[{program_name}] Missing ReadMe.\n"
        f"Accepted:\n"
        f"  - {README_PREFIX}{program_name}.*\n"
        f"  - {README_NAME}\n"
        f"  - README.md / README.txt\n"
        f"In folder:\n{program_dir}"
    )


def find_readme_full_or_none(program_dir: Path, program_name: str) -> "Path | None":
    """The detailed companion document, or None.

    The Launcher shows this one behind its own **Details** button
    (`l.py::find_readme_full_or_none`), so the deploy has to carry it as well as
    the short ReadMe. Missing is not an error — a program may have only the short
    one, and then the Launcher simply shows no Details button.
    """
    targets = {
        _normalize_name(f"{README_PREFIX}{program_name}_Full"),
        _normalize_name(f"{README_PREFIX}{program_name}_Details"),
        _normalize_name(f"Manual_{program_name}"),
    }
    try:
        for f in program_dir.iterdir():
            if f.is_file() and _normalize_name(f.stem) in targets:
                return f
    except OSError:
        return None
    return None


def copy_readme_with_overwrite_notice(src_readme: Path, dst_dir: Path, logs: list[str], program_name: str):
    dst_readme = dst_dir / src_readme.name
    if dst_readme.exists():
        logs.append(f"[{program_name}] WARNING: {src_readme.name} exists, overwriting -> {dst_readme}")
    shutil.copy2(src_readme, dst_readme)
    logs.append(f"[{program_name}] ReadMe copied -> {dst_readme}")


def is_newer_version(latest: str, last_deployed: str) -> bool:
    vt_latest = parse_version(latest or "")
    vt_deployed = parse_version(last_deployed or "")
    if not vt_latest:
        return False
    if not vt_deployed:
        return True
    return vt_latest > vt_deployed


# ---------------- UI ----------------
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_summary(jobs, selected_roots) -> str:
        from collections import defaultdict
        stats = defaultdict(int)
        # stats se plní v _deploy_one_program — ale tam nemáme přístup
        # Proto přidáme tracking přes return value
        return "All done."



class DeployGUI(ttk.Frame):
    def __init__(self, parent=None, log_widget=None):
        super().__init__(parent)
        self._external_log = log_widget

        self.state_deployed = load_state()

        self.program_vars: dict[Path, tk.BooleanVar] = {}
        self.program_version_vars: dict[Path, tk.StringVar] = {}
        self.dest_vars: dict[str, tk.BooleanVar] = {}  # keyed by destination name

        self.program_is_new: dict[Path, bool] = {}
        self.program_latest_version: dict[Path, str] = {}

        # Helper exes (a program's build_config.json -> extra_exes): they have a
        # dist folder of their own but no source folder, so they are shown
        # indented under their parent, the same way the builder shows them.
        self.helper_keys_by_parent: dict[str, list[Path]] = {}
        self.helper_parent_dir: dict[str, Path] = {}   # helper name -> parent source folder
        self.helper_rows: dict[str, list[ttk.Frame]] = {}
        self.expander_buttons: dict[str, ttk.Button] = {}
        # Folded/unfolded state survives a Refresh — the rows are rebuilt, the
        # list the user opened should not close under them.
        self.expanded_programs: set[str] = set()

        self.dest_path_labels: list[tuple[ttk.Label, str]] = []
        self._log_autoscroll = True

        self.arrow_col_px = 24
        self.program_col_px = 200
        self.version_col_px = 140
        self.new_col_px = 70

        self._build_ui()
        self._load_programs()

    def _update_programs_root_wraplength(self, event=None):
        try:
            w = self.programs_root_lbl.winfo_width()
            if w > 50:
                self.programs_root_lbl.configure(wraplength=w)
        except Exception:
            pass

    def _split_path_to_lines(self, path_str: str, max_px: int, font: tkfont.Font) -> str:
        parts = path_str.split("\\")
        if not parts:
            return path_str

        lines: list[str] = []
        current = parts[0]

        for part in parts[1:]:
            candidate = current + "\\" + part
            if font.measure(candidate) <= max_px:
                current = candidate
            else:
                lines.append(current)
                current = "\\" + part
        lines.append(current)

        return "\n".join(lines)

    def _reflow_dest_paths(self):
        f = tkfont.nametofont("TkDefaultFont")

        for lbl, raw in self.dest_path_labels:
            w = lbl.winfo_width()
            if w <= 30:
                continue

            max_px = max(80, w - 8)
            new_text = self._split_path_to_lines(raw, max_px, f)

            if lbl.cget("text") != new_text:
                lbl.configure(text=new_text)

    def _on_dest_configure(self, event=None):
        self.after_idle(self._reflow_dest_paths)

    def _build_dest_rows(self):
        for child in self.dest_frame.winfo_children():
            child.destroy()
        self.dest_vars.clear()
        self.dest_path_labels.clear()

        destinations = _get_destination_roots()
        if not destinations:
            ttk.Label(self.dest_frame, text="No paths configured.\nClick ⚙ Set paths.",
                      foreground="gray").pack(anchor="w", padx=4, pady=4)
        else:
            for name, path in destinations:
                var = tk.BooleanVar(value=True)
                self.dest_vars[name] = var

                row = ttk.Frame(self.dest_frame)
                row.pack(fill="x", pady=4)
                row.grid_columnconfigure(0, weight=0)
                row.grid_columnconfigure(1, weight=0)
                row.grid_columnconfigure(2, weight=1)

                ttk.Checkbutton(row, variable=var).grid(row=0, column=0, sticky="w")
                ttk.Label(row, text=name, width=12).grid(row=0, column=1, sticky="w", padx=(6, 8))

                raw = str(path)
                path_lbl = ttk.Label(row, text=raw, justify="left", anchor="w")
                path_lbl.grid(row=0, column=2, sticky="ew")
                self.dest_path_labels.append((path_lbl, raw))
                path_lbl.bind("<Configure>", self._on_dest_configure)

        self.dest_frame.bind("<Configure>", self._on_dest_configure)
        self.after_idle(self._reflow_dest_paths)

    def _open_set_paths(self):
        cfg = _load_devtools_config()

        win = tk.Toplevel(self)
        win.title("Set paths")
        win.resizable(False, False)
        win.grab_set()

        ttk.Label(win, text="Configure shared paths for Dev Tools (Builder + Copy Manager).",
                  padding=(12, 10)).pack()

        fields = [
            ("root_folder", "Root folder",               "e.g. C:\\...\\Jan_a_Jan"),
            ("scratch",     "Scratch (Software) folder", "e.g. Z:\\Software"),
            ("sharepoint",  "Sharepoint (QoL) folder",   "e.g. C:\\...\\L3-HAPLS\\General\\QoL"),
        ]

        vars_ = {}
        for key, label, hint in fields:
            row = ttk.Frame(win)
            row.pack(fill="x", padx=16, pady=4)
            ttk.Label(row, text=label + ":", width=28, anchor="w").pack(side="left")
            var = tk.StringVar(value=cfg.get(key, ""))
            vars_[key] = var
            ttk.Entry(row, textvariable=var, width=40, state="readonly").pack(side="left", padx=(4, 4))
            ttk.Button(row, text="Browse…",
                       command=lambda k=key, v=var: _browse(k, v)).pack(side="left")

        def _browse(key, var):
            cur = var.get() or str(Path.home())
            chosen = filedialog.askdirectory(title="Select folder", initialdir=cur)
            if chosen:
                var.set(chosen)

        def on_save():
            for key, var in vars_.items():
                val = var.get().strip()
                if val:
                    cfg[key] = val
                else:
                    cfg.pop(key, None)
            _save_devtools_config(cfg)
            win.destroy()
            self.programs_root_lbl.configure(text=str(_programs_root()))
            self._build_dest_rows()
            self._load_programs()
            messagebox.showinfo("Paths saved", "Paths saved to %APPDATA%\\DevTools\\config.json")

        ttk.Button(win, text="Save", command=on_save, padding=(16, 6)).pack(pady=(8, 12))

    def _change_root(self):
        cur = str(_programs_root())
        chosen = filedialog.askdirectory(title="Select root folder", initialdir=cur)
        if not chosen:
            return
        new_root = Path(chosen)
        if not new_root.exists():
            messagebox.showerror("Invalid folder", f"Folder does not exist:\n{new_root}")
            return
        cfg = _load_devtools_config()
        cfg["root_folder"] = str(new_root)
        _save_devtools_config(cfg)
        self.programs_root_lbl.configure(text=str(new_root))
        self._load_programs()
        builder = getattr(self, "_builder_ref", None)
        if builder is not None:
            builder.root_folder = new_root
            builder.dist_root = new_root.parent / "dist"
            builder.root_var.set(str(new_root))
            builder._reload_projects(select_first=True)

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.LabelFrame(root, text="Root folder")
        top.pack(fill="x")

        top_row = ttk.Frame(top)
        top_row.pack(fill="x", padx=10, pady=8)
        top_row.grid_columnconfigure(0, weight=1)

        self.programs_root_lbl = ttk.Label(top_row, text=str(_programs_root()), justify="left", anchor="w")
        self.programs_root_lbl.grid(row=0, column=0, sticky="ew")

        ttk.Button(top_row, text="Change…", command=self._change_root).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(top_row, text="⚙ Set paths", command=self._open_set_paths).grid(row=0, column=2, padx=(4, 0))

        top_row.bind("<Configure>", self._update_programs_root_wraplength)
        self.after(0, self._update_programs_root_wraplength)

        body = ttk.Frame(root)
        body.pack(fill="x", pady=(10, 6))
        body.configure(height=320)
        body.pack_propagate(False)

        left = ttk.LabelFrame(body, text="Select programs + version")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.header = ttk.Frame(left)
        self.header.pack(fill="x", padx=8, pady=(8, 2))

        # Column 0 is the slot for the fold arrow of a program that carries
        # helpers, so every tick box stays on the same left edge and a row with
        # an arrow does not read as indented.
        self._configure_row_columns(self.header)
        ttk.Label(self.header, text="Program").grid(row=0, column=2, sticky="w")
        ttk.Label(self.header, text="Version").grid(row=0, column=3, sticky="w")
        ttk.Label(self.header, text="Status").grid(row=0, column=4, sticky="w")

        self.programs_sf = ScrollableFrame(left)
        self.programs_sf.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        right = ttk.LabelFrame(body, text="Destinations")
        right.pack(side="left", fill="y", expand=False)

        right.configure(width=340)
        right.pack_propagate(False)

        self.dest_frame = ttk.Frame(right)
        self.dest_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self._build_dest_rows()

        self.after_idle(self._reflow_dest_paths)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(6, 0))

        # Group 3 — copy (packed right first so expand can fill the middle)
        self.copy_btn = ttk.Button(actions, text="Copy", command=self._on_copy)
        self.copy_btn.pack(side="right")
        self.readme_btn = ttk.Button(actions, text="Copy ReadMe Only", command=self._on_copy_readme_only)
        self.readme_btn.pack(side="right", padx=(0, 4))
        ttk.Frame(actions, width=16).pack(side="right")

        # Group 1 — selection
        ttk.Button(actions, text="Select All", command=self._select_all_programs).pack(side="left")
        ttk.Button(actions, text="Select New", command=self._select_new_programs).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Clear", command=self._clear_programs).pack(side="left", padx=(4, 0))
        ttk.Button(actions, text="Refresh", command=self._refresh).pack(side="left", padx=(4, 0))

        # Group 2 — fix + deploy (centered between group 1 and group 3)
        _center = ttk.Frame(actions)
        _center.pack(side="left", expand=True, fill="x")
        _fix_row = ttk.Frame(_center)
        ttk.Button(_fix_row, text="Fix", command=self._on_fix).pack(side="left")
        self.internal_btn = ttk.Button(_fix_row, text="Deploy Libraries", command=self._on_deploy_internal)
        self.internal_btn.pack(side="left", padx=(4, 0))
        _fix_row.pack(expand=True)

        prog_frame = ttk.Frame(root)
        prog_frame.pack(fill="x", pady=(4, 0))
        self._prog_var = tk.IntVar(value=0)
        self._prog_label_var = tk.StringVar(value="")
        self._prog_bar = ttk.Progressbar(prog_frame, variable=self._prog_var, maximum=100)
        self._prog_label = ttk.Label(prog_frame, textvariable=self._prog_label_var, anchor="w", font=("Segoe UI", 8))
        self._prog_bar.pack_forget()
        self._prog_label.pack_forget()

        log_box = ttk.LabelFrame(root, text="Log")
        log_box.pack(fill="both", expand=True)

        log_inner = ttk.Frame(log_box)
        log_inner.pack(fill="both", expand=True, padx=8, pady=8)

        if self._external_log is not None:
            self.log = self._external_log
            self.log.configure(wrap="none", font=("Consolas", 8))
        else:
            self.log = tk.Text(log_inner, height=12, wrap="none", font=("Consolas", 8))

        log_vsb = ttk.Scrollbar(log_inner, orient="vertical", command=self.log.yview)
        log_hsb = ttk.Scrollbar(log_inner, orient="horizontal", command=self.log.xview)
        self.log.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        log_vsb.pack(side="right", fill="y")
        log_hsb.pack(side="bottom", fill="x")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.configure(state="disabled")
        self.log.bind("<MouseWheel>", self._on_log_scroll)
        log_vsb.bind("<ButtonRelease-1>", self._on_log_scrollbar_release)

    def _refresh(self):
        self.state_deployed = load_state()
        try:
            self.programs_root_lbl.configure(text=str(_programs_root()))
        except Exception:
            pass
        self._load_programs()

    def auto_deploy(self, built_projects: list, build_summary: str = None,
                    build_info: dict = None):
        """
        Voláno z BuilderUI po úspěšném buildu (copy_after_build).
        built_projects = [(program_dir: Path, version_name: str), ...]
        Předvybere programy + verze a spustí deploy automaticky.
        """
        # Odškrtni vše
        for var in self.program_vars.values():
            var.set(False)

        # Zaškrtni a nastav verzi pro každý buildnutý projekt
        not_found = []
        for program_dir, version_name in built_projects:
            matched = None
            for p in self.program_vars:
                if p.name.lower() == program_dir.name.lower():
                    matched = p
                    break
            if matched is None:
                not_found.append(program_dir.name)
                continue
            self.program_vars[matched].set(True)
            self.program_version_vars[matched].set(version_name)

        if not_found:
            self._log(f"[auto_deploy] WARNING: tyto projekty nebyly nalezeny v CM listu: {not_found}")

        selected_roots = self._get_selected_destination_roots()
        if not selected_roots:
            # No destination = no copy, so the copy report never runs. Print the
            # build report here, otherwise the whole run ends without any report.
            if build_summary:
                self._log(build_summary, force_scroll=True)
            self._log("[auto_deploy] ERROR: žádná destinace není vybrána — "
                      "nic se nekopírovalo.", force_scroll=True)
            return

        self._log(f"[auto_deploy] Spouštím deploy pro: {[p.name for p, _ in built_projects]}\n")
        self._on_copy(build_summary=build_summary, build_info=build_info)

    def _log(self, text: str, force_scroll: bool = False):
        target = self._external_log if self._external_log is not None else getattr(self, "log", None)
        if target is None:
            return
        if force_scroll:
            # Final reports must always land in view. Nudging the log during a long
            # build/copy turns autoscroll off and only scrolling back to the very
            # bottom turns it on again — which is how the end-of-run report ended
            # up written below the visible area and looked like it was missing.
            self._log_autoscroll = True
            builder = getattr(self, "_builder_ref", None)
            if builder is not None:
                try:
                    builder._local_log_autoscroll = True
                except Exception:
                    pass
        def _append():
            try:
                target.configure(state="normal")
                target.insert("end", text + "\n")
                if self._log_autoscroll:
                    target.see("end")
                target.configure(state="disabled")
            except Exception:
                pass
        try:
            target.after(0, _append)
        except Exception:
            pass

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _on_log_scroll(self, event=None):
        self.after(50, self._check_log_position)

    def _on_log_scrollbar_release(self, event=None):
        self.after(50, self._check_log_position)

    def _check_log_position(self):
        try:
            bottom = self.log.yview()[1]
            self._log_autoscroll = (bottom >= 0.95)
        except Exception:
            pass

    def _compute_program_col_px(self, names: list[str]) -> int:
        f = tkfont.nametofont("TkDefaultFont")
        max_px = 0
        for n in names:
            max_px = max(max_px, f.measure(n))
        return max_px + 40

    def _configure_row_columns(self, frame):
        """The column layout shared by the header and every row: fold arrow, tick
        box, program, version, status, last deployed."""
        frame.grid_columnconfigure(0, minsize=self.arrow_col_px)
        frame.grid_columnconfigure(1, minsize=26)
        frame.grid_columnconfigure(2, minsize=self.program_col_px)
        frame.grid_columnconfigure(3, minsize=self.version_col_px)
        frame.grid_columnconfigure(4, minsize=self.new_col_px)
        frame.grid_columnconfigure(5, weight=1)

    def _load_programs(self):
        for child in self.programs_sf.inner.winfo_children():
            child.destroy()
        self.program_vars.clear()
        self.program_version_vars.clear()
        self.program_is_new.clear()
        self.program_latest_version.clear()
        self.helper_keys_by_parent.clear()
        self.helper_parent_dir.clear()
        self.helper_rows.clear()
        self.expander_buttons.clear()

        programs_root = _programs_root()
        dist_root = _dist_root()

        if not programs_root.exists():
            self._log(f"ERROR: programs root does not exist: {programs_root}")
            return

        IGNORE = {"dist", "archive", "internal builder", "l3-qol-janjan", ".venv", ".vscode", ".git",
                  "matlab", "icons", "extractor", "shift planner"}
        program_dirs = []
        for p in sorted(programs_root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir():
                continue
            if p.name.startswith("_"):
                continue
            if p.name.lower() in IGNORE:
                continue
            program_dirs.append(p)

        if not program_dirs:
            self._log(f"No program folders found in: {programs_root}")
            return

        # A helper whose name is already a program folder is that program — list
        # it once, as itself.
        taken = {p.name.lower() for p in program_dirs}
        helper_names_by_parent = {
            parent: [n for n in names if n.lower() not in taken]
            for parent, names in find_helper_programs(program_dirs).items()
        }

        col_names = [p.name for p in program_dirs]
        for names in helper_names_by_parent.values():
            col_names += [f"↳ {n}" for n in names]
        self.program_col_px = self._compute_program_col_px(col_names)
        self._configure_row_columns(self.header)

        # Read once for the whole list: Versions.txt lives on the scratch share
        # and re-reading it per program made opening the list wait on the network
        # as many times as there are programs.
        versions_map = read_versions_txt()

        self.programs_sf.inner.grid_columnconfigure(0, weight=1)
        row_idx = 0
        for p in program_dirs:
            helper_names = helper_names_by_parent.get(p.name) or []
            row = self._build_program_row(p, row_idx, dist_root, versions_map)
            row_idx += 1

            if helper_names:
                # Folded by default: most programs have no helpers, and an
                # always-open tree would push the programs themselves out of the
                # visible part of the list.
                open_now = p.name in self.expanded_programs
                btn = ttk.Button(row, text="▾" if open_now else "▸", width=2,
                                 command=lambda pn=p.name: self._toggle_expanded(pn))
                btn.grid(row=0, column=0, sticky="w")
                self.expander_buttons[p.name] = btn

                keys, rows = [], []
                for n in helper_names:
                    # No source folder of its own — the name is what the copy
                    # keys on (dist/<name>), so a key under the programs root is
                    # enough to make it look like any other program here.
                    key = programs_root / n
                    self.helper_parent_dir[n] = p
                    keys.append(key)
                    rows.append(self._build_program_row(key, row_idx, dist_root,
                                                       versions_map, is_helper=True))
                    row_idx += 1
                self.helper_keys_by_parent[p.name] = keys
                self.helper_rows[p.name] = rows
                if not open_now:
                    for hr in rows:
                        hr.grid_remove()

    def _build_program_row(self, key: Path, row_idx: int, dist_root: Path,
                           versions_map: dict, is_helper: bool = False) -> ttk.Frame:
        """One row of the program list — a program folder or, indented, a helper
        exe built from one. Both are copied the same way, so both get a tick box,
        a version to pick and a status."""
        name = key.name
        version_folders = list_versions(dist_root / name)   # may not exist yet
        version_names = [vf.name for vf in version_folders]

        latest = version_names[0] if version_names else ""
        self.program_latest_version[key] = latest

        # Deployed version from Versions.txt (primary) or local state (fallback)
        last_deployed = (versions_map.get(name, "")
                         or self.state_deployed.get(name.lower(), "")).strip()
        is_new = bool(latest) and is_newer_version(latest, last_deployed)
        self.program_is_new[key] = is_new

        var_checked = tk.BooleanVar(value=False)
        self.program_vars[key] = var_checked
        var_version = tk.StringVar(value=latest)
        self.program_version_vars[key] = var_version

        row = ttk.Frame(self.programs_sf.inner)
        row.grid(row=row_idx, column=0, sticky="ew", pady=(1 if is_helper else 3))
        self._configure_row_columns(row)

        tick = ttk.Checkbutton(row, variable=var_checked)
        if not is_helper:
            tick.configure(command=lambda kk=key: self._on_program_check_clicked(kk))
        tick.grid(row=0, column=1, sticky="w")
        ttk.Label(row, text=f"↳ {name}" if is_helper else name).grid(
            row=0, column=2, sticky="w", padx=((20 if is_helper else 6), 8))

        cb = ttk.Combobox(
            row,
            textvariable=var_version,
            values=version_names,
            state="readonly" if version_names else "disabled",
        )
        cb.grid(row=0, column=3, sticky="w")

        if not version_names:
            ttk.Label(row, text="NEW", foreground="gray").grid(row=0, column=4, sticky="w")
        elif is_new:
            ttk.Label(row, text="NEW", foreground="green").grid(row=0, column=4, sticky="w")
        elif last_deployed:
            ttk.Label(row, text="UTD", foreground="#cc0000").grid(row=0, column=4, sticky="w")
        else:
            ttk.Label(row, text="").grid(row=0, column=4, sticky="w")

        ttk.Label(row, foreground="gray",
                  text=f"last deployed: {last_deployed}" if last_deployed
                       else "last deployed: (none)").grid(row=0, column=5, sticky="w")
        return row

    def _on_program_check_clicked(self, key: Path):
        """Ticking a program takes its helpers with it — the usual case is a
        released version where the app and its helper match. It is a one-way
        push, not a lock: untick a helper afterwards and it stays unticked.
        Ticking also opens the list, so what went along is not hidden away."""
        var = self.program_vars.get(key)
        helper_keys = self.helper_keys_by_parent.get(key.name) or []
        if var is None or not helper_keys:
            return
        for hk in helper_keys:
            hv = self.program_vars.get(hk)
            if hv is not None:
                hv.set(var.get())
        if var.get():
            self._toggle_expanded(key.name, open_it=True)

    def _toggle_expanded(self, program_name: str, open_it: bool | None = None):
        was_open = program_name in self.expanded_programs
        want_open = (not was_open) if open_it is None else bool(open_it)
        if want_open == was_open:
            return
        if want_open:
            self.expanded_programs.add(program_name)
        else:
            self.expanded_programs.discard(program_name)

        for hr in self.helper_rows.get(program_name, []):
            try:
                hr.grid() if want_open else hr.grid_remove()
            except Exception:
                pass
        btn = self.expander_buttons.get(program_name)
        if btn is not None:
            try:
                btn.configure(text="▾" if want_open else "▸")
            except Exception:
                pass

    def _select_all_programs(self):
        for var in self.program_vars.values():
            var.set(True)

    def _select_new_programs(self):
        any_new = False
        for p, var in self.program_vars.items():
            if self.program_is_new.get(p, False):
                var.set(True)
                latest = self.program_latest_version.get(p, "")
                if latest:
                    self.program_version_vars[p].set(latest)
                any_new = True
            else:
                var.set(False)
        if not any_new:
            messagebox.showinfo("NEW", "No NEW versions found.")

    def _clear_programs(self):
        for var in self.program_vars.values():
            var.set(False)

    def _on_fix(self):
        """Fix archives (sort flat files into vX.Y.Z/ subfolders) + fix icons for all programs in all destinations."""
        selected_roots = self._get_selected_destination_roots()
        if not selected_roots:
            from tkinter import messagebox
            messagebox.showwarning("No destination", "Select at least one destination root.")
            return

        self._clear_log()
        self._log("Fixing archives and icons...\n")
        self._set_busy(True)

        def worker():
            try:
                for dst_root in selected_roots:
                    if not dst_root.exists():
                        self.after(0, self._log, f"Skipping (not accessible): {dst_root}")
                        continue

                    for program_dir in sorted(dst_root.iterdir(), key=lambda p: p.name.lower()):
                        if not program_dir.is_dir():
                            continue
                        if program_dir.name.lower() in ("archive", "dist"):
                            continue

                        program_name = program_dir.name
                        try:
                            # ── Archive old exes from main folder — keep only newest ─
                            _all_exes = sorted(
                                [p for p in program_dir.glob("*.exe") if not TIMESTAMPED_EXE_RE.match(p.name)],
                                key=_exe_version, reverse=True,
                            )
                            if len(_all_exes) > 1:
                                _keep = _all_exes[0]
                                _logs: list[str] = []
                                move_existing_exes_to_archive(program_dir, _keep.name, _logs, program_name)
                                for _msg in _logs:
                                    self.after(0, self._log, _msg)

                            # ── Fix archive ──────────────────────────────────────
                            archive_dir = program_dir / "archive"
                            if archive_dir.exists():
                                _logfn = lambda msg: self.after(0, self._log, msg)
                                _fix_archive_dir(archive_dir, _logfn)
                                # Reunite helper .py files stranded in unknown/ with their
                                # version folders, then make every snapshot runnable by
                                # stripping timestamp suffixes off in-folder source names.
                                _reunite_unknown_helpers(archive_dir, _logfn)
                                _normalize_version_folder_names(archive_dir, _logfn)

                            # ── Fix icons ────────────────────────────────────────
                            dist_prog_dir = _dist_root() / program_name
                            src_ico = None
                            for vf in list_versions(dist_prog_dir)[:1]:
                                cand = vf / "icon.ico"
                                if cand.exists():
                                    src_ico = cand
                                    break
                            if src_ico is None:
                                cand = dist_prog_dir / "icon.ico"
                                if cand.exists():
                                    src_ico = cand

                            for png_name in ("icon.png", "Icon.png", "ICON.png"):
                                png = program_dir / png_name
                                if png.exists():
                                    try:
                                        png.unlink()
                                        self.after(0, self._log, f"[{program_name}] Removed: {png_name}")
                                    except Exception as e:
                                        self.after(0, self._log, f"[{program_name}] Could not remove {png_name}: {e}")

                            if src_ico is not None and src_ico.exists():
                                dst_ico = program_dir / "icon.ico"
                                if not dst_ico.exists() or dst_ico.stat().st_size != src_ico.stat().st_size:
                                    try:
                                        shutil.copy2(src_ico, dst_ico)
                                        self.after(0, self._log, f"[{program_name}] icon.ico updated")
                                    except Exception as e:
                                        self.after(0, self._log, f"[{program_name}] FAILED icon.ico: {e}")
                        except Exception as e:
                            self.after(0, self._log, f"[{program_name}] ERROR (skipped): {e}")

                self.after(0, lambda: self._log("\nDone.", force_scroll=True))
            except Exception as e:
                self.after(0, lambda m=str(e): self._log(f"\nFix aborted: {m}", force_scroll=True))
            finally:
                self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _collect_icon_conflicts(
        self,
        jobs: list[tuple[Path, str]],
        destination_roots: list[Path],
    ) -> dict[str, bool]:
        """
        Projde všechny jobs a destination_roots, najde ikony kde:
        - cílová ikona existuje A má jinou velikost než zdrojová
        Zobrazí porovnávací dialog pro každý konflikt.
        Vrací dict: klíč = "{program_name}|{dst_root}" -> True = nahradit, False = přeskočit.
        """
        decisions: dict[str, bool] = {}

        for program_dir, version_name in jobs:
            if not version_name:
                continue

            version_folder = program_dir / version_name  # program_dir is dist_root/program_name

            # Najdi zdrojovou ikonu (stejná logika jako v _deploy_one_program)
            icon_src = None
            for name in ("icon.ico", "Icon.ico", "icon.png", "Icon.png", "icon.gif", "Icon.gif"):
                cand = version_folder / name
                if cand.exists():
                    icon_src = cand
                    break
            if icon_src is None:
                for name in ("icon.ico", "Icon.ico", "icon.png", "Icon.png", "icon.gif", "Icon.gif"):
                    cand = program_dir / name
                    if cand.exists():
                        icon_src = cand
                        break
            print(f"DEBUG [{program_dir.name}] icon_src={icon_src}", flush=True)              
            if icon_src is None:
                continue  # žádná ikona ke kopírování

            # Zjisti jestli existuje konflikt v jakékoliv destinaci
            conflict_existing = None
            for dst_root in destination_roots:
                target_dir = dst_root / program_dir.name
                dst_icon = target_dir / icon_src.name
                if dst_icon.exists() and dst_icon.stat().st_size != icon_src.stat().st_size:
                    conflict_existing = dst_icon
                    break
            
            for dst_root in destination_roots:
                target_dir = dst_root / program_dir.name
                dst_icon = target_dir / icon_src.name
            if conflict_existing is not None:
                replace = show_icon_compare_dialog(self, program_dir.name, conflict_existing, icon_src)
            else:
                replace = None  # žádný konflikt, rozhodne se per-destinace níže

            for dst_root in destination_roots:
                target_dir = dst_root / program_dir.name
                dst_icon = target_dir / icon_src.name
                if replace is not None:
                    decisions[f"{program_dir.name}|{dst_root}"] = replace
                elif not dst_icon.exists():
                    decisions[f"{program_dir.name}|{dst_root}"] = True
                else:
                    # stejná velikost -> přeskočit
                    decisions[f"{program_dir.name}|{dst_root}"] = False

        return decisions

    def _get_selected_programs(self) -> list[Path]:
        return [p for p, v in self.program_vars.items() if v.get()]

    def _get_selected_destination_roots(self) -> list[Path]:
        all_destinations = {name: path for name, path in _get_destination_roots()}
        return [all_destinations[name] for name, var in self.dest_vars.items()
                if var.get() and name in all_destinations]

    def _set_busy(self, busy: bool):
        self.copy_btn.configure(state=("disabled" if busy else "normal"))
        self.readme_btn.configure(state=("disabled" if busy else "normal"))

    def _progress_show(self, total: int):
        self._prog_bar.configure(maximum=max(1, total))
        self._prog_var.set(0)
        self._prog_label_var.set("")
        self._prog_bar.pack(fill="x", pady=(4, 0))
        self._prog_label.pack(fill="x")

    def _progress_update(self, done: int, total: int, label: str = ""):
        self._prog_var.set(done)
        txt = f"{label}  ({done}/{total})" if label else f"{done}/{total}"
        self._prog_label_var.set(txt)

    def _progress_hide(self):
        self._prog_bar.pack_forget()
        self._prog_label.pack_forget()
        self._prog_label_var.set("")

    def _deploy_one_program(self, program_dir: Path, version_name: str, destination_roots: list[Path], live_log=None, icon_decisions: dict | None = None) -> tuple[list[str], dict]:
        logs: list[str] = []
        file_log: list[str] = []  # structured per-file record for end summary

        def log(msg: str):
            logs.append(msg)
            if live_log:
                live_log(msg)

        def flog(action: str, src: str, dst: str = ""):
            # action: "copied", "archived", "moved", "skipped", "removed", "failed"
            if dst:
                file_log.append(f"  [{action:8}]  {src}  →  {dst}")
            else:
                file_log.append(f"  [{action:8}]  {src}")

        # One counter per kind of file, so the end report can name what it counted.
        # Extras (icons, images/, sounds/) used to be added to exe_copied, which made
        # the report claim more exe files than there were programs.
        stats = {
            "exe_copied": 0, "exe_failed": 0,
            "py_copied": 0, "py_failed": 0,
            "readme_copied": 0, "readme_failed": 0,
            "extra_copied": 0, "extra_failed": 0,
            "icon_copied": 0, "icon_failed": 0, "icon_skipped": 0,
            "archived_exe": 0, "archived_py": 0, "archive_skipped": 0,
            "dest_ok": 0, "dest_failed": 0, "dest_unreachable": 0,
        }
        # Failure counters watched to decide whether a single destination came out
        # clean — the report says "copied to 2/2 destinations", not just a file count.
        _FAIL_KEYS = ("exe_failed", "py_failed", "readme_failed", "extra_failed", "icon_failed")
        program_name = program_dir.name

        if not version_name:
            raise FileNotFoundError(f"[{program_name}] NEW program: build it first (no versions in dist).")

        # program_dir is now dist_root/program_name — version folder is a child of it
        version_folder = program_dir / version_name
        if not version_folder.exists():
            raise FileNotFoundError(f"Selected version folder missing: {version_folder}")

        src_exe = find_exe_in_folder(version_folder, program_name, version_name)

        # ReadMe lookup order:
        # 1. version_folder  (builder copies it there for primary dev)
        # 2. source folder   (the project folder in the repo — the live ReadMe.
        #    Builds made before the builder started copying the ReadMe have none
        #    in version_folder, and without this step the deploy silently shipped
        #    no ReadMe at all, so the Launcher showed no ReadMe button.)
        # 3. program_dir     (dist/program_name — may have it from previous deploy)
        # 4. scratch/program_name  (already deployed there by a previous version)
        # 5. None — skip with warning
        src_readme = None
        _readme_search_dirs = [version_folder, _programs_root() / program_name, program_dir]
        # A helper exe has no source folder of its own: its documentation lives in
        # the folder of the program it is built from, named after the helper
        # (ReadMe_<helper name>.txt). Without this step a helper built before the
        # builder started copying its ReadMe along would ship none at all.
        _helper_parent = self.helper_parent_dir.get(program_name)
        if _helper_parent is not None:
            _readme_search_dirs.append(_helper_parent)
        for dst_root in destination_roots:
            _readme_search_dirs.append(dst_root / program_name)
        for _d in _readme_search_dirs:
            try:
                src_readme = find_readme_or_raise(_d, program_name)
                break
            except FileNotFoundError:
                pass
        if src_readme is None:
            log(f"[{program_name}] WARNING: ReadMe not found — skipping ReadMe copy")

        # The detailed companion doc, searched in the same places and in the same
        # order. Absent is fine; the Launcher then shows no Details button.
        src_readme_full = None
        for _d in _readme_search_dirs:
            src_readme_full = find_readme_full_or_none(_d, program_name)
            if src_readme_full is not None:
                break

        # Najdi všechny .py soubory ve version_folder
        src_py_files = list(version_folder.glob("*.py"))

        # Ikona: version_folder nebo program_dir (dist/program_name)
        icon_src = None
        for name in ("icon.ico", "Icon.ico", "icon.png", "Icon.png", "icon.gif", "Icon.gif"):
            cand = version_folder / name
            if cand.exists():
                icon_src = cand
                break
        if icon_src is None:
            for name in ("icon.ico", "Icon.ico", "icon.png", "Icon.png", "icon.gif", "Icon.gif"):
                cand = program_dir / name
                if cand.exists():
                    icon_src = cand
                    break

        # Najdi všechny složky a ostatní soubory ve version_folder (kromě _internal).
        # Folders count: an app's runtime assets live in folders next to the exe
        # (Announcer/images, Announcer/sounds) and are useless if only the exe is
        # deployed.
        src_extras = []
        for item in version_folder.iterdir():
            if item.name == "_internal":
                continue
            if item in (src_exe, src_readme, src_readme_full):
                continue
            if item.is_file() and item.suffix.lower() in (".py", ".exe"):
                continue
            src_extras.append(item)

        # Runtime assets, second source. Builds made before the builder started
        # bundling asset folders ship an EMPTY images/ and sounds/, and deploying
        # such a version used to leave the destination empty too. So whenever the
        # version folder has nothing to offer for an asset folder, take it from the
        # source tree (L3-QoL-JanJan/<program>/images) — that is where the files
        # the program actually uses live.
        # _programs_root(), not _src_root(): it is the one that resolves the
        # configured Root folder when running as the built exe.
        src_prog_dir = _programs_root() / program_name
        for _asset in ASSET_DIR_NAMES:
            if _dir_has_files(version_folder / _asset):
                continue  # the build carries it — already in src_extras
            _repo_asset = src_prog_dir / _asset
            if not _dir_has_files(_repo_asset):
                continue
            src_extras = [i for i in src_extras if i.name.lower() != _asset]
            src_extras.append(_repo_asset)
            log(f"[{program_name}] {_asset}/ not in the build — taken from {_repo_asset}")

        # Folder names the new version brings along — they must survive the
        # stale-folder cleanup below, which would otherwise delete them and (since
        # nothing re-created them) leave the deployed app without its assets.
        incoming_dirs = {i.name for i in src_extras if i.is_dir()}

        log(f"[{program_name}] Version: {version_name}")
        log(f"[{program_name}] EXE: {src_exe.name}")
        log(f"[{program_name}] ReadMe: {src_readme.name if src_readme else '(none — skipped)'}")
        log(f"[{program_name}] Details: {src_readme_full.name if src_readme_full else '(none)'}")
        log(f"[{program_name}] Extra files: {[x.name for x in src_extras]}")
        log(f"[{program_name}] PY files: {[x.name for x in src_py_files]}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ver = version_name

        for dst_root in destination_roots:
            target_dir = dst_root / program_name
            # Create the program folder if it doesn't exist yet (mkdir -p). Only a
            # genuinely unreachable destination (drive not mounted / network down)
            # fails here — then skip THIS destination and continue with the others,
            # instead of stalling or aborting the whole copy.
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                archive_dir = target_dir / "archive"
                archive_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log(f"[{program_name}] SKIP — destination not accessible: {dst_root} ({e})")
                flog("skipped", str(dst_root), "not accessible")
                stats["dest_failed"] += 1
                stats["dest_unreachable"] += 1
                continue
            _fix_archive_dir(archive_dir, log)

            log(f"[{program_name}] → {target_dir}")

            dest_label = dst_root.name
            flog("dest", f"→ {target_dir}")
            _fails_before = sum(stats[k] for k in _FAIL_KEYS)

            # ── Smaž zbytkové složky z předchozích chybných deployů ─
            # Asset folders are never deleted here, not even when neither the build
            # nor the source tree has them: the deployed app needs them, and a
            # deletion is the one thing nothing later puts back.
            for item in target_dir.iterdir():
                if (item.is_dir()
                        and item.name not in ("_internal", "archive")
                        and item.name.lower() not in ASSET_DIR_NAMES
                        and item.name not in incoming_dirs):
                    try:
                        shutil.rmtree(item)
                        log(f"[{program_name}] Removed stale folder: {item.name}")
                        flog("removed", item.name, str(target_dir))
                    except Exception as e:
                        log(f"[{program_name}] Could not remove folder {item.name}: {e}")

            # Version currently deployed (being replaced) — used to group unversioned
            # helper .py files (if_t.py, …) into the SAME archive/vX.Y.Z/ folder as the
            # main script, instead of dumping them into archive/unknown/. Computed before
            # the .exe is archived away, so the old .exe can serve as a fallback source.
            _live_ver = None
            for _cand in list(target_dir.glob(f"{program_name}*.py")) + \
                         list(target_dir.glob(f"{program_name}*.exe")):
                _vm = VERSION_RE.search(_cand.stem)
                if _vm:
                    _live_ver = _vm.group(0)
                    break

            # ── Archivuj staré .exe ──────────────────────────────────
            for exe in target_dir.glob(f"{program_name}*.exe"):
                # Extract old version from stem: "Program Name vX.Y.Z" -> "vX.Y.Z"
                _vm = VERSION_RE.search(exe.stem)
                old_ver_label = _vm.group(0) if _vm else "unknown"
                ver_archive_dir = archive_dir / old_ver_label
                ver_archive_dir.mkdir(parents=True, exist_ok=True)
                dst_arch = unique_path(ver_archive_dir / exe.name)
                moved, why = _try_move(exe, dst_arch)
                if moved:
                    _arch_log = ver_archive_dir / "archive_log.txt"
                    try:
                        with _arch_log.open("a", encoding="utf-8") as _f:
                            _f.write(f"{timestamp} | {exe.name}\n")
                    except Exception:
                        pass
                    log(f"[{program_name}] Archived EXE: {exe.name} -> archive/{old_ver_label}/")
                    flog("archived", exe.name, str(dst_arch.relative_to(dst_root)))
                    stats["archived_exe"] += 1
                else:
                    log(f"[{program_name}] SKIP archive (locked): {exe.name}")
                    flog("skipped", exe.name, "locked")
                    stats["archive_skipped"] += 1

            # ── Archivuj staré .py ───────────────────────────────────
            for pyf in target_dir.glob("*.py"):
                _vm = VERSION_RE.search(pyf.stem)
                # Versioned main .py -> its own version; unversioned helpers -> the
                # live version's folder (keeps the snapshot together and runnable).
                old_ver_label = _vm.group(0) if _vm else (_live_ver or "unknown")
                ver_archive_dir = archive_dir / old_ver_label
                ver_archive_dir.mkdir(parents=True, exist_ok=True)
                dst_arch = unique_path(ver_archive_dir / pyf.name)
                moved, why = _try_move(pyf, dst_arch)
                if moved:
                    _arch_log = ver_archive_dir / "archive_log.txt"
                    try:
                        with _arch_log.open("a", encoding="utf-8") as _f:
                            _f.write(f"{timestamp} | {pyf.name}\n")
                    except Exception:
                        pass
                    log(f"[{program_name}] Archived PY: {pyf.name} -> archive/{old_ver_label}/")
                    flog("archived", pyf.name, str(dst_arch.relative_to(dst_root)))
                    stats["archived_py"] += 1
                else:
                    log(f"[{program_name}] SKIP archive PY (locked): {pyf.name}")
                    flog("skipped", pyf.name, "locked")
                    stats["archive_skipped"] += 1

            # ── Kopíruj nový .exe ────────────────────────────────────
            dst_exe = target_dir / f"{program_name} {ver}.exe"
            try:
                # Logged BEFORE the copy so a stall (slow/flaky scratch) is visible:
                # if the log stops on this line, the copy itself is hanging.
                log(f"[{program_name}] Copying EXE ({src_exe.stat().st_size // 1024} KB) → {dst_exe.name} …")
                shutil.copy2(src_exe, dst_exe)
                log(f"[{program_name}] Copied EXE -> {dst_exe.name}")
                flog("copied", src_exe.name, str(dst_exe.relative_to(dst_root)))
                stats["exe_copied"] += 1
            except Exception as e:
                log(f"[{program_name}] FAILED EXE: {e}")
                flog("failed", src_exe.name, str(e))
                stats["exe_failed"] += 1

            # ── Kopíruj .py soubory ──────────────────────────────────
            for pyf in src_py_files:
                dst_py = target_dir / pyf.name
                try:
                    shutil.copy2(pyf, dst_py)
                    log(f"[{program_name}] Copied PY: {pyf.name}")
                    flog("copied", pyf.name, str(dst_py.relative_to(dst_root)))
                    stats["py_copied"] += 1
                except Exception as e:
                    log(f"[{program_name}] FAILED PY: {e}")
                    flog("failed", pyf.name, str(e))
                    stats["py_failed"] += 1

            # ── Kopíruj ostatní extra soubory/složky ─────────────────
            for item in src_extras:
                dst_item = target_dir / item.name
                try:
                    if item.is_dir():
                        copied, failed_n, errs = _merge_dir(item, dst_item)
                        log(f"[{program_name}] Copied extra: {item.name}/ "
                            f"({copied} files{f', {failed_n} FAILED' if failed_n else ''})")
                        for _e in errs[:5]:
                            log(f"[{program_name}]   FAILED in {item.name}/: {_e}")
                        if failed_n:
                            flog("failed", f"{item.name}/", f"{failed_n} of {copied + failed_n} files")
                            stats["extra_failed"] += 1
                            continue
                    else:
                        shutil.copy2(item, dst_item)
                        log(f"[{program_name}] Copied extra: {item.name}")
                    flog("copied", item.name, str(dst_item.relative_to(dst_root)))
                    stats["extra_copied"] += 1
                except Exception as e:
                    log(f"[{program_name}] FAILED extra {item.name}: {e}")
                    flog("failed", item.name, str(e))
                    stats["extra_failed"] += 1

            # ── ReadMe + the detailed companion ──────────────────────
            # Both, because the Launcher has a button for each. Publishing only
            # the short one leaves Details with nothing to open.
            for _doc, _label in ((src_readme, "ReadMe"), (src_readme_full, "Details")):
                if _doc is None:
                    continue
                try:
                    dst_readme = target_dir / _doc.name
                    if dst_readme.exists():
                        log(f"[{program_name}] WARNING: {_doc.name} exists, overwriting")
                    shutil.copy2(_doc, dst_readme)
                    log(f"[{program_name}] {_label} copied -> {dst_readme.name}")
                    flog("copied", _doc.name, str(dst_readme.relative_to(dst_root)))
                    stats["readme_copied"] += 1
                except Exception as e:
                    log(f"[{program_name}] FAILED {_label}: {e}")
                    flog("failed", _doc.name, str(e))
                    stats["readme_failed"] += 1

            # ── Kopíruj ikonu ────────────────────────────────────────
            if icon_src is not None:
                key = f"{program_name}|{dst_root}"
                should_copy = (icon_decisions or {}).get(key, True)

                # Smaž případné PNG ikony pokud kopírujeme ICO
                if icon_src.suffix.lower() == ".ico":
                    for stale_name in ("icon.png", "Icon.png", "ICON.png"):
                        stale = target_dir / stale_name
                        if stale.exists():
                            try:
                                stale.unlink()
                                log(f"[{program_name}] Removed stale PNG icon: {stale.name}")
                                flog("removed", stale_name, str(target_dir.relative_to(dst_root)))
                            except Exception as e:
                                log(f"[{program_name}] Could not remove PNG icon: {e}")

                if should_copy:
                    try:
                        dst_icon = target_dir / icon_src.name
                        shutil.copy2(icon_src, dst_icon)
                        log(f"[{program_name}] Icon copied -> {dst_icon.name}")
                        flog("copied", icon_src.name, str(dst_icon.relative_to(dst_root)))
                        stats["icon_copied"] += 1
                    except Exception as e:
                        log(f"[{program_name}] FAILED icon: {e}")
                        flog("failed", icon_src.name, str(e))
                        stats["icon_failed"] += 1
                else:
                    log(f"[{program_name}] Icon skipped (kept existing) -> {dst_root}")
                    flog("skipped", icon_src.name, f"kept existing in {dest_label}")
                    stats["icon_skipped"] += 1

            if sum(stats[k] for k in _FAIL_KEYS) > _fails_before:
                stats["dest_failed"] += 1
            else:
                stats["dest_ok"] += 1

        log(f"[{program_name}] DONE\n")
        return logs, stats, file_log

    def _on_copy_readme_only(self):
        selected_programs = self._get_selected_programs()
        selected_roots = self._get_selected_destination_roots()

        if not selected_programs:
            messagebox.showwarning("Nothing selected", "Select at least one program.")
            return
        if not selected_roots:
            messagebox.showwarning("No destination", "Select at least one destination root.")
            return

        self._clear_log()
        self._log("Copying ReadMe files only...\n")
        self._set_busy(True)

        dist_root = _dist_root()
        def worker():
            try:
                for src_dir in selected_programs:
                    program_dir = dist_root / src_dir.name
                    try:
                        # Search: version_folder → source folder → program_dir → scratch/program_name
                        src_readme = None
                        version_folders = list_versions(program_dir)
                        search_dirs = (version_folders[:1] if version_folders else []) + [src_dir, program_dir]
                        # A helper exe keeps its ReadMe in the folder of the
                        # program it is built from — it has none of its own.
                        _hp = self.helper_parent_dir.get(src_dir.name)
                        if _hp is not None:
                            search_dirs.append(_hp)
                        for dst_root in selected_roots:
                            search_dirs.append(dst_root / program_dir.name)
                        for _d in search_dirs:
                            try:
                                src_readme = find_readme_or_raise(_d, program_dir.name)
                                break
                            except FileNotFoundError:
                                pass
                        # The detailed companion, same places, same order.
                        src_readme_full = None
                        for _d in search_dirs:
                            src_readme_full = find_readme_full_or_none(_d, program_dir.name)
                            if src_readme_full is not None:
                                break
                        if src_readme is None and src_readme_full is None:
                            self.after(0, self._log, f"[{program_dir.name}] WARNING: no ReadMe found — skipped")
                            continue
                        if src_readme is None:
                            self.after(0, self._log, f"[{program_dir.name}] WARNING: short ReadMe not found")
                        if src_readme_full is None:
                            self.after(0, self._log, f"[{program_dir.name}] note: no Details doc")
                        for dst_root in selected_roots:
                            try:
                                if not dst_root.exists():
                                    self.after(0, self._log, f"[{program_dir.name}] SKIP — not accessible: {dst_root}")
                                    continue
                            except OSError:
                                self.after(0, self._log, f"[{program_dir.name}] SKIP — not accessible: {dst_root}")
                                continue
                            target_dir = dst_root / program_dir.name
                            target_dir.mkdir(parents=True, exist_ok=True)
                            for _doc, _label in ((src_readme, "ReadMe"),
                                                 (src_readme_full, "Details")):
                                if _doc is None:
                                    continue
                                dst_readme = target_dir / _doc.name
                                shutil.copy2(_doc, dst_readme)
                                self.after(0, self._log,
                                           f"[{program_dir.name}] {_label} copied -> {dst_readme}")
                    except Exception as e:
                        self.after(0, self._log, f"[{program_dir.name}] ERROR: {e}")
                self.after(0, lambda: self._log("\nDone.", force_scroll=True))
            finally:
                # Always re-enable the buttons, even if a copy hung-then-failed.
                self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_build_internal(self):
        builder_script = _programs_root() / "Internal Builder" / "_internal_builder.py"
        if not builder_script.exists():
            messagebox.showerror("Error", f"Builder script not found:\n{builder_script}")
            return

        self._clear_log()
        self._log(f"Building _internal_builder...\n{builder_script}\n")
        self._set_busy(True)

        def worker():
            import subprocess
            spec_file = builder_script.parent / "_internal_builder.spec"
            if spec_file.exists():
                # Use .spec — contains all collect-all libraries
                cmd = [
                    sys.executable, "-m", "PyInstaller",
                    "--noconfirm",
                    "--distpath", r"C:\Dev\dist",
                    str(spec_file),
                ]
            else:
                # Fallback: run __main__ block in the script itself
                cmd = [sys.executable, str(builder_script)]
            self.after(0, self._log, f"CMD: {' '.join(cmd)}\n")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(builder_script.parent),
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        self.after(0, self._log, line)
                proc.wait()
                if proc.returncode == 0:
                    self.after(0, lambda: self._log("\n✓ Build DONE.", force_scroll=True))
                else:
                    self.after(0, lambda rc=proc.returncode: self._log(
                        f"\n✗ Build FAILED (returncode={rc})", force_scroll=True))
            except Exception as e:
                self.after(0, lambda m=str(e): self._log(f"ERROR: {m}", force_scroll=True))
            finally:
                self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_deploy_internal(self):
        selected = [p for p, v in self.program_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("Nothing selected", "Check at least one _int checkbox.")
            return

        # Najdi nejnovější verzi _internal_builder (dist\Internal Builder\vX.Y.Z\_internal)
        if not INTERNAL_BUILDER_DIST.exists():
            messagebox.showerror("Error", f"_internal_builder dist not found:\n{INTERNAL_BUILDER_DIST}")
            return

        # Find latest version subfolder
        _ver_folders = sorted(
            [d for d in INTERNAL_BUILDER_DIST.iterdir() if d.is_dir() and VERSION_RE.match(d.name)],
            key=lambda d: [int(x) for x in VERSION_RE.match(d.name).groups()],
            reverse=True,
        )
        if _ver_folders:
            _latest_ver_folder = _ver_folders[0]
        else:
            _latest_ver_folder = INTERNAL_BUILDER_DIST  # fallback: no versioned subfolders

        internal_src = _latest_ver_folder / "_internal"
        if not internal_src.exists():
            messagebox.showerror("Error", f"_internal folder not found in:\n{_latest_ver_folder}")
            return

        scratch = _get_scratch_root()
        if scratch is None:
            messagebox.showerror("Error", "Scratch path not configured.\nClick ⚙ Set paths first.")
            return
        zip_path = scratch / "_internal_builder.zip"

        self._clear_log()
        self._log(f"Packing _internal from: {_latest_ver_folder}\n")
        self._set_busy(True)
        self._progress_show(1)  # zobraz progress bar hned; maximum se upřesní ve workeru

        def worker():
            ok = 0
            fail = 0

            # Hydrate OneDrive placeholders in one shot before reading any files.
            # Without this, each file.read() triggers a separate cloud download.
            try:
                import subprocess as _sp
                _sp.run(
                    ["attrib", "-U", "/s", "/d", str(internal_src)],
                    capture_output=True, timeout=120
                )
                self.after(0, self._log, "OneDrive: hydration requested for _internal")
            except Exception as _he:
                self.after(0, self._log, f"OneDrive hydration skipped: {_he}")

            # Spočítej soubory předem
            all_files = [f for f in internal_src.rglob("*") if f.is_file()]
            n_files = len(all_files)
            self.after(0, self._log, f"Files found in _internal: {n_files}")
            total_steps = n_files + n_files * len(selected)
            done_steps = 0
            self.after(0, lambda: self._prog_bar.configure(maximum=max(1, total_steps)))

            # Zabal _internal do ZIP — temp soubor v %TEMP%
            try:
                import zipfile as _zf
                import tempfile as _tmpmod
                _tmp_fd, _tmp_str = _tmpmod.mkstemp(suffix=".zip", prefix="_internal_builder_")
                tmp_zip = Path(_tmp_str)
                os.close(_tmp_fd)
                self.after(0, self._log, f"Creating ZIP: {zip_path.name}")
                with _zf.ZipFile(tmp_zip, "w", compression=_zf.ZIP_DEFLATED) as zf:
                    for f in all_files:
                        arcname = "_internal_builder/_internal/" + f.relative_to(internal_src).as_posix()
                        zf.write(f, arcname)
                        done_steps += 1
                        self.after(0, lambda d=done_steps, t=total_steps: self._progress_update(d, t, "ZIP"))
                shutil.copy2(str(tmp_zip), str(zip_path))
                tmp_zip.unlink(missing_ok=True)
                import zipfile as _zf2
                with _zf2.ZipFile(zip_path, "r") as _zcheck:
                    _zcount = sum(1 for m in _zcheck.infolist() if not m.filename.endswith("/"))
                self.after(0, self._log, f"ZIP created: {zip_path.name} — {_zcount} files\n")
                # Recalculate total_steps from the actual ZIP file count so the
                # progress bar maximum is always accurate (n_files from rglob can
                # differ from the final ZIP entry count when extra binaries were
                # added to _internal at build time).
                total_steps = _zcount + _zcount * len(selected)
                done_steps = _zcount  # ZIP phase is complete
                self.after(0, lambda: self._prog_bar.configure(maximum=max(1, total_steps)))
            except Exception as e:
                self.after(0, self._log, f"ERROR creating ZIP: {e}")
                self.after(0, self._set_busy, False)
                self.after(0, self._progress_hide)
                return

            # Rozbal do každé vybrané složky
            import zipfile
            for program_dir in selected:
                dst_dir = scratch / program_dir.name
                if not dst_dir.exists() or not dst_dir.is_dir():
                    self.after(0, self._log, f"[{program_dir.name}] SKIP — folder not found: {dst_dir}")
                    fail += 1
                    done_steps += n_files
                    self.after(0, lambda d=done_steps, t=total_steps: self._progress_update(d, t, "skipped"))
                    continue

                self.after(0, self._log, f"[{program_dir.name}] → {dst_dir}")
                try:
                    old_internal = dst_dir / "_internal"
                    old_internal.mkdir(parents=True, exist_ok=True)

                    with zipfile.ZipFile(zip_path, "r") as zf:
                        members = [m for m in zf.infolist() if not m.filename.endswith("/")]
                        prefix = ""
                        for m in members:
                            idx = m.filename.find("_internal/")
                            if idx >= 0:
                                prefix = m.filename[:idx + len("_internal/")]
                                break

                        # Build set of relative paths that should exist after deploy
                        expected_rel = set()
                        members_to_write = []
                        for member in members:
                            if not member.filename.startswith(prefix):
                                continue
                            rel = member.filename[len(prefix):]
                            if not rel:
                                continue
                            expected_rel.add(rel)
                            dst_file = old_internal / rel.replace("/", os.sep)
                            # Skip if file exists and size matches (avoid OneDrive sync storm).
                            # Never skip .pyd files — same size doesn't mean same binary
                            # (e.g. numpy 2.4.2 vs 2.4.6 produce identical-sized but
                            # differently-compiled extensions that link different DLL hashes).
                            _is_pyd = dst_file.suffix.lower() == ".pyd"
                            if not _is_pyd and dst_file.exists() and dst_file.stat().st_size == member.file_size:
                                done_steps += 1
                                self.after(0, lambda d=done_steps, t=total_steps, n=program_dir.name: self._progress_update(d, t, n))
                                continue
                            members_to_write.append((member, rel))

                        # Remove files that no longer exist in the new _internal
                        removed = 0
                        for existing in old_internal.rglob("*"):
                            if existing.is_file():
                                rel = existing.relative_to(old_internal).as_posix()
                                if rel not in expected_rel:
                                    existing.unlink(missing_ok=True)
                                    removed += 1

                        extracted = 0
                        for member, rel in members_to_write:
                            member.filename = rel
                            zf.extract(member, old_internal)
                            extracted += 1
                            done_steps += 1
                            self.after(0, lambda d=done_steps, t=total_steps, n=program_dir.name: self._progress_update(d, t, n))

                    skipped = len(expected_rel) - extracted
                    self.after(0, self._log,
                        f"[{program_dir.name}] OK — {extracted} updated, {skipped} unchanged, {removed} removed")
                    ok += 1
                except Exception as e:
                    self.after(0, self._log, f"[{program_dir.name}] ERROR: {e}")
                    fail += 1
                    done_steps += n_files
                    self.after(0, lambda d=done_steps, t=total_steps: self._progress_update(d, t, "error"))

            summary = f"\n{'='*40}\nDeploy libraries DONE  ✓ {ok} OK  |  ✗ {fail} failed\n{'='*40}"
            self.after(0, self._progress_hide)
            self.after(0, lambda: self._log(summary, force_scroll=True))
            self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _copy_summary(self, total_stats, per_program: list, n_jobs: int, roots: list,
                      elapsed: float, n_errors: int, detail_lines: list) -> str:
        """The end-of-copy report: what went where, counted per kind of file.
        Counts are totals over ALL destinations — the old report divided by the
        number of destinations and labelled the result "per dest", which came out
        wrong whenever one destination failed."""
        n_dest = len(roots)
        dest_list = "\n".join(f"    {r}" for r in roots)
        unreachable = total_stats["dest_unreachable"]
        unreachable_note = (f"    Destinations unreachable: {unreachable}"
                            f"  (drive not mounted / network down)\n" if unreachable else "")
        return (
            f"\n{'='*40}\n"
            f"COPY DONE  {'⚠ ' + str(n_errors) + ' error(s) — see log above' if n_errors else '✓ No errors'}\n"
            f"  Time elapsed:      {_fmt_elapsed(elapsed)}\n"
            f"  Programs copied:   {sum(1 for p in per_program if p['error'] is None)} of {n_jobs}\n"
            f"  Destinations:      {n_dest}\n{dest_list}\n"
            f"\n  Totals across all destinations:\n"
            f"    EXE copied:        {total_stats['exe_copied']}  |  failed: {total_stats['exe_failed']}\n"
            f"    PY copied:         {total_stats['py_copied']}  |  failed: {total_stats['py_failed']}\n"
            f"    ReadMe copied:     {total_stats['readme_copied']}  |  failed: {total_stats['readme_failed']}\n"
            f"    Extras copied:     {total_stats['extra_copied']}  |  failed: {total_stats['extra_failed']}\n"
            f"       (icon.ico, images/, sounds/ and anything else next to the exe)\n"
            f"    Shared icon:       {total_stats['icon_copied']} copied  |  "
            f"{total_stats['icon_skipped']} kept existing  |  failed: {total_stats['icon_failed']}\n"
            f"    Old files archived:  {total_stats['archived_exe']} exe + {total_stats['archived_py']} py"
            f"  |  skipped (locked): {total_stats['archive_skipped']}\n"
            + unreachable_note +
            f"\nDetail:\n" + "\n".join(detail_lines) + f"\n{'='*40}"
        )

    def _overall_summary(self, per_program: list, roots: list, copy_elapsed: float,
                         n_errors: int, build_info: dict = None) -> str:
        """The wrap-up for the whole job the user started — build and copy together.
        The copy report alone never said what was built, and the build report alone
        never said where it ended up, so neither answered "what did I just do?"."""
        b_items = {i["name"].lower(): i for i in (build_info or {}).get("items", [])}
        b_failed = (build_info or {}).get("failed", [])
        b_elapsed = (build_info or {}).get("elapsed", 0.0) or 0.0
        did_build = build_info is not None

        total_errors = n_errors + len(b_failed)
        n_dest = len(roots)

        lines = ["", "=" * 40]
        lines.append(f"ALL DONE  {'⚠ ' + str(total_errors) + ' error(s)' if total_errors else '✓ Everything went through'}")
        lines.append(f"  Task:            {'build + copy' if did_build else 'copy only'}")
        if did_build:
            lines.append(f"  Total time:      {_fmt_elapsed(b_elapsed + copy_elapsed)}"
                         f"   (build {_fmt_elapsed(b_elapsed)}  +  copy {_fmt_elapsed(copy_elapsed)})")
        else:
            lines.append(f"  Total time:      {_fmt_elapsed(copy_elapsed)}")

        lines.append("")
        lines.append("  Programs:")
        for p in per_program:
            mark = "✗" if p["error"] else ("✓" if p["dest_ok"] == n_dest else "⚠")
            lines.append(f"    {mark}  {p['name']}  {p['version']}")
            b = b_items.get(p["name"].lower())
            if b:
                where = f"  →  {b['out_dir']}" if b.get("out_dir") else ""
                lines.append(f"         built in {b['seconds']:.1f}s{where}")
            if p["error"]:
                lines.append(f"         NOT copied: {p['error']}")
            else:
                lines.append(f"         copied to {p['dest_ok']} of {n_dest} destinations")

        # Built but not copied — a program can drop out of the copy (not on the
        # Copy manager list), and then only this report would notice.
        copied_names = {p["name"].lower() for p in per_program}
        for key, b in b_items.items():
            if key not in copied_names:
                lines.append(f"    ⚠  {b['name']}  {b['version']}")
                lines.append(f"         built in {b['seconds']:.1f}s"
                             f"{'  →  ' + b['out_dir'] if b.get('out_dir') else ''}")
                lines.append("         NOT copied — not selected in Copy Manager")

        for f in b_failed[:10]:
            lines.append(f"    ✗  build failed: {f}")

        lines.append("")
        lines.append("  Destinations:")
        for r in roots:
            lines.append(f"    {r}")
        lines.append("=" * 40)
        return "\n".join(lines)

    def _on_copy(self, build_summary: str = None, build_info: dict = None):
        selected_programs = self._get_selected_programs()
        selected_roots = self._get_selected_destination_roots()

        if not selected_programs or not selected_roots:
            # Bailing out must not swallow the build report handed to us.
            if build_summary:
                self._log(build_summary, force_scroll=True)
            if not selected_programs:
                messagebox.showwarning("Nothing selected", "Select at least one program.")
            else:
                messagebox.showwarning("No destination", "Select at least one destination root.")
            return

        dist_root = _dist_root()
        jobs = [(dist_root / p.name, self.program_version_vars[p].get().strip()) for p in selected_programs]

        # Keep the log when the copy is the second half of a build+copy run — wiping
        # it would throw away the build output the user just watched, and the run is
        # one single job as far as they are concerned.
        if build_summary is None and build_info is None:
            self._clear_log()
        self._log("Checking icon conflicts...\n")

        icon_decisions = self._collect_icon_conflicts(jobs, selected_roots)

        self._log("Starting deploy...\n")
        self._set_busy(True)
        start_time = time.perf_counter()

        def worker():
            nonlocal start_time
            changed = False
            from collections import defaultdict
            total_stats = defaultdict(int)
            all_file_logs: list[tuple[str, str, list[str]]] = []  # (program, version, file_log)
            per_program: list[dict] = []                          # for the overall report
            try:
                for program_dir, version_name in jobs:
                    try:
                        lines, prog_stats, file_log = self._deploy_one_program(
                            program_dir, version_name, selected_roots,
                            live_log=lambda msg: self.after(0, self._log, msg),
                            icon_decisions=icon_decisions,
                        )
                        for k, v in prog_stats.items():
                            total_stats[k] += v
                        all_file_logs.append((program_dir.name, version_name, file_log))
                        per_program.append({
                            "name": program_dir.name, "version": version_name,
                            "dest_ok": prog_stats.get("dest_ok", 0),
                            "error": None,
                        })

                        self.state_deployed[program_dir.name.lower()] = version_name
                        write_version_to_txt(program_dir.name, version_name)
                        changed = True

                    except Exception as e:
                        err_msg = str(e)
                        self.after(0, self._log, f"[{program_dir.name}] ERROR: {err_msg}\n")
                        total_stats["program_failed"] += 1
                        all_file_logs.append((program_dir.name, version_name, [f"  [failed  ]  {err_msg}"]))
                        per_program.append({
                            "name": program_dir.name, "version": version_name,
                            "dest_ok": 0, "error": err_msg,
                        })

                if changed:
                    save_state(self.state_deployed)

                # Every kind of failure counts, not just the exe one — an unreachable
                # destination or a failed ReadMe used to be reported as "No errors".
                # dest_failed is deliberately left out: it only marks WHICH destination
                # a file failure happened in and would count the same failure twice.
                n_errors = sum(total_stats[k] for k in (
                    "exe_failed", "py_failed", "readme_failed", "extra_failed",
                    "icon_failed", "dest_unreachable", "program_failed"))
                elapsed = time.perf_counter() - start_time
                n_dest = len(selected_roots)

                detail_lines = []
                for prog_name, ver_name, flog in all_file_logs:
                    detail_lines.append(f"\n  {prog_name}  {ver_name}")
                    detail_lines.extend(flog)

                # ── Report 1: what the copy did ─────────────────────────────
                copy_summary = self._copy_summary(
                    total_stats, per_program, len(jobs), selected_roots,
                    elapsed, n_errors, detail_lines)

                if build_summary:
                    self.after(0, self._log, build_summary)
                self.after(0, lambda: self._log(copy_summary, force_scroll=True))

                # ── Report 2: the whole task, build and copy together ───────
                overall = self._overall_summary(
                    per_program, selected_roots, elapsed, n_errors, build_info)
                self.after(0, lambda: self._log(overall, force_scroll=True))
            finally:
                self.after(0, self._set_busy, False)
                self.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Copy Manager")
    set_app_icon(root, str(_app_dir() / "icon.ico"), "ELI.CopyManager")
    root.geometry("980x650")
    root.minsize(900, 560)
    app = DeployGUI(root)
    app.pack(fill="both", expand=True)
    root.mainloop()