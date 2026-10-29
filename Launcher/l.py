# l.py
"""The Launcher (PySide6): one window that starts every program in the collection.

It scans a folder of programs, sorts them into groups and shows one card per
program: start, ReadMe, Details, its folder and its older versions. A newer
version on disk turns the card orange until it is started or acknowledged.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
import json

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPalette, QPen,
    QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

# ---------------- CONFIG ----------------
def _onedrive_root() -> Path:
    """This machine's OneDrive folder.

    The name is per tenant ("OneDrive - ELI Beamlines", "OneDrive - ELI ERIC", …)
    and a PC can have more than one of them, so a hard-coded name resolves to a
    real but WRONG folder instead of failing visibly. Order: the environment
    (what Windows itself says), then any OneDrive folder that actually holds the
    sources, then the historical name so nothing regresses.
    """
    env = os.environ.get("ONEDRIVE") or os.environ.get("ONEDRIVECOMMERCIAL")
    tail = Path("ELI Beamlines") / "Python" / "programy"
    candidates = []
    if env:
        candidates.append(Path(env))
    try:
        candidates.extend(sorted(d for d in Path.home().iterdir()
                                 if d.is_dir() and d.name.lower().startswith("onedrive")))
    except OSError:
        pass
    for c in candidates:
        if (c / tail).is_dir():
            return c
    return candidates[0] if candidates else Path.home() / "OneDrive - ELI Beamlines"


_ONEDRIVE = _onedrive_root()


def _devtools_dist_root() -> "Path | None":
    """Where Dev Tools puts its builds, from its own config.

    That file is the single source of truth for the build output (it is set in
    Dev Tools' "Set paths" dialog and is usually outside OneDrive, so a build does
    not churn the sync). Reading it here means a developer run of the Launcher
    lists exactly what has been built locally, instead of nothing.

    Every program of every scan asks, so the answer is kept until the config
    file changes on disk — one stat instead of reading the JSON each time.
    """
    cfg = Path(os.environ.get("APPDATA", "~")) / "DevTools" / "config.json"
    try:
        mtime = cfg.stat().st_mtime
    except OSError:
        return None
    with _DT_DIST_LOCK:
        if _DT_DIST_CACHE.get("mtime") == mtime:
            return _DT_DIST_CACHE.get("path")
    try:
        root = json.loads(cfg.read_text(encoding="utf-8")).get("dist_root")
    except Exception:
        root = None
    p = Path(root) if root else None
    p = p if p is not None and p.is_dir() else None
    with _DT_DIST_LOCK:
        _DT_DIST_CACHE.update(mtime=mtime, path=p)
    return p


_DT_DIST_CACHE: dict = {}
_DT_DIST_LOCK = threading.Lock()


def _dist_candidates(root: Path, program_name: str) -> list:
    """Every dist folder a program's builds may sit in.

    The sources sit in the repo while `dist` stays next to it, so a dist folder
    beside the scan root counts too, and so does Dev Tools' own build folder.
    The first scan and the version poll both use this list, so a build the scan
    found is never missed by the poll.
    """
    cands = [root / "dist" / program_name, root.parent / "dist" / program_name]
    dt = _devtools_dist_root()
    if dt is not None:
        cands.append(dt / program_name)
    return cands


def _programs_root() -> Path:
    """The folder whose sub-folders are programs.

    Program folders live in the git repository (`…/programy/L3-QoL-JanJan`), while
    the build output stays beside it (`…/programy/dist`). Pointing the scan at
    `programy` found Archive / Icons / Matlab and no programs, so the repo level is
    used when it is there.
    """
    base = _ONEDRIVE / "ELI Beamlines" / "Python" / "programy"
    repo = base / "L3-QoL-JanJan"
    return repo if repo.is_dir() else base
CONFIG_PATH = Path(os.environ.get("APPDATA", "~")) / "Launcher" / "config.json"

def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _save_config(data: dict):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        pass

ROOT_OPTIONS = [
    ("Lab - Scratch",      Path(r"\\hapls-share.lcs.local\scratch\Software"), False),
    ("Office - Scratch",   None,                                               True),
    ("Office - Sharepoint",None,                                               True),
    ("Office - Programs",  _programs_root(),                                   False),
]
# Třetí hodnota = True znamená "cesta je z configu, lze nastavit přes UI"

def _apply_config_to_root_options(cfg: dict) -> list:
    """Vrátí ROOT_OPTIONS s doplněnými cestami z configu."""
    key_map = {
        "Office - Scratch":    "office_scratch",
        "Office - Sharepoint": "office_sharepoint",
    }
    result = []
    for entry in ROOT_OPTIONS:
        label, path, configurable = entry
        if configurable and label in key_map:
            saved = cfg.get(key_map[label])
            path = Path(saved) if saved else None
        result.append((label, path, configurable))
    return result

IGNORE_DIR_NAMES = {"archive", "dist"}  # program\archive is ignored

# Two cards to a row, always: never one, never three. The window is kept wide
# enough for two of the widest cards instead (see _fit_width).
GROUP_COLS = 2
MIN_WINDOW_H = 430

NOTES_LAB    = Path(r"\\hapls-share.lcs.local\scratch\Software\notes.txt")
NOTES_OFFICE = Path(r"\\hapls-share.cs.eli-beams.eu\scratch\Software\notes.txt")

VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")

ARCHIVE_EXE_RE = re.compile(
    r"^.+\s+v(\d+)\.(\d+)\.(\d+)__\d{8}_\d{6}\.exe$",
    re.IGNORECASE
)

# Normalized archived exe — Dev Tools strips the "__YYYYMMDD_HHMMSS" suffix off
# files inside vX.Y.Z/ folders so a snapshot is runnable as-is. An optional
# " (2)" dedup suffix may trail the version.
#   "Image Tools v2.5.4.exe", "Image Tools v2.4.0 (2).exe"
ARCHIVE_EXE_PLAIN_RE = re.compile(
    r"^.+\s+v(\d+)\.(\d+)\.(\d+)(?:\s+\(\d+\))?\.exe$",
    re.IGNORECASE
)

TIMESTAMPED_EXE_RE = re.compile(
    r"^.+\s+v\d+\.\d+\.\d+__\d{8}_\d{6}\.exe$",
    re.IGNORECASE
)

_ARCHIVE_VER_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)__(\d{8})_(\d{6})", re.IGNORECASE)
_ARCHIVE_LABEL_RE = re.compile(r"(v\d+\.\d+\.\d+)__(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", re.IGNORECASE)
_VER_ANYWHERE_RE = re.compile(r"(v\d+\.\d+\.\d+)", re.IGNORECASE)
_VER_NUM_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)", re.IGNORECASE)

def _archive_exe_version(p: Path, folder_ver: tuple | None = None) -> tuple:
    """Sort key (maj, min, patch, date, time). Timestamped names keep full
    granularity; normalized names fall back to the version folder / filename
    with a (0, 0) timestamp so they still sort by version."""
    m = _ARCHIVE_VER_RE.search(p.stem)
    if m:
        return tuple(map(int, m.groups()))
    base = folder_ver
    if base is None:
        vm = _VER_NUM_RE.search(p.stem)
        base = tuple(map(int, vm.groups())) if vm else (0, 0, 0)
    return base + (0, 0)

def _archive_exe_label(p: Path, folder_ver: tuple | None = None) -> str:
    m = _ARCHIVE_LABEL_RE.search(p.stem)
    if m:
        ver = m.group(1)
        date = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
        time_ = f"{m.group(5)}:{m.group(6)}:{m.group(7)}"
        return f"{ver}  ({date}  {time_})"
    # Normalized name (no timestamp) — label from the version folder or filename.
    if folder_ver:
        return _ver_str(folder_ver)
    vm = _VER_NUM_RE.search(p.stem)
    if vm:
        return "v" + ".".join(vm.groups())
    return p.stem

# The main script of an archived snapshot carries the version in its name
# ("Image Tools v2.5.4.py", optionally timestamped or with a " (2)" dedup
# suffix); the helper modules beside it keep their plain names (is_t.py …).
ARCHIVE_PY_RE = re.compile(
    r"^.+\s+v(\d+)\.(\d+)\.(\d+)(?:__\d{8}_\d{6}|\s+\(\d+\))?\.py$",
    re.IGNORECASE
)

# Folders inside archive\ that are not a version snapshot.
ARCHIVE_NON_VERSION_DIRS = {"unknown", "_temp_latest"}


def _find_versioned_py(version_dir: Path, exe_path: Path | None = None) -> Path | None:
    """The main .py of an archived snapshot.

    Named after the exe when one was archived with it; otherwise the one .py in
    the folder that carries a version in its name — which is how a snapshot
    holding sources only (no exe archived, or the exe deleted by hand) is found.
    """
    if exe_path is not None:
        # e.g. "Image Tools v1.2.1__20260511_125840" or "Image Tools v2.5.4"
        candidate = version_dir / (exe_path.stem + ".py")
        if candidate.exists():
            return candidate
    cands = [f for f in version_dir.glob("*.py") if ARCHIVE_PY_RE.match(f.name)]
    if not cands:
        return None
    # Canonical name first, so a " (2)" dedup copy never wins.
    cands.sort(key=lambda p: (" (" in p.stem, "__" in p.stem, p.name.lower()))
    return cands[0]


def scan_archive_versions(program_dir: Path) -> list[dict]:
    """Vrátí seznam archivních verzí seřazených od nejnovější.
    Podporuje novou strukturu archive/vX.Y.Z/*.exe (s timestampou i normalizovanou
    bez timestampy) i starou plochou archive/*.exe."""
    archive_dir = program_dir / "archive"
    if not archive_dir.exists():
        return []

    entries = []

    # Nová struktura: archive/vX.Y.Z/ — jeden záznam na složku verze. The exe is
    # optional: a snapshot that kept only its sources is listed as well, because
    # those sources are what actually gets run (see _launch_exe).
    for version_subdir in archive_dir.iterdir():
        if not version_subdir.is_dir():
            continue
        if version_subdir.name.lower() in ARCHIVE_NON_VERSION_DIRS:
            continue
        folder_ver = parse_version(version_subdir.name)  # (maj, min, patch) | None
        # Prefer the canonical name over " (2)" dedup copies.
        exes = sorted(version_subdir.glob("*.exe"),
                      key=lambda p: (" (" in p.stem, p.name.lower()))
        exe = None
        for cand in exes:
            if ARCHIVE_EXE_RE.match(cand.name) or ARCHIVE_EXE_PLAIN_RE.match(cand.name):
                exe = cand
                break  # one exe per version folder
        main_py = _find_versioned_py(version_subdir, exe)
        if exe is None and main_py is None:
            continue
        ref = exe if exe is not None else main_py
        entries.append({
            "exe_path": exe,
            "py_path": main_py,
            # A folder that brought its own _internal needs no swap at all.
            "has_internal": (version_subdir / "_internal").is_dir(),
            "label": _archive_exe_label(ref, folder_ver),
            "_ver": _archive_exe_version(ref, folder_ver),
        })

    # Stará flat struktura: archive/*.exe (zpětná kompatibilita)
    for exe in archive_dir.glob("*.exe"):
        if ARCHIVE_EXE_RE.match(exe.name):
            entries.append({
                "exe_path": exe,
                "py_path": None,
                "has_internal": False,
                "label": _archive_exe_label(exe),
                "_ver": _archive_exe_version(exe),
            })

    entries.sort(key=lambda e: e["_ver"], reverse=True)
    for e in entries:
        e.pop("_ver", None)
    return entries

README_PREFIX = "readme_"  # case-insensitive

def _exe_version(p: Path):
    m = VERSION_RE.search(p.stem)
    return tuple(map(int, m.groups())) if m else (0, 0, 0)

def _ver_str(ver: tuple) -> str:
    return f"v{ver[0]}.{ver[1]}.{ver[2]}"

def parse_version(name: str):
    m = VERSION_RE.fullmatch(name)
    return tuple(map(int, m.groups())) if m else None


def newest_version_folder(dist_dir: Path) -> Path | None:
    if not dist_dir.exists():
        return None
    candidates = []
    for p in dist_dir.iterdir():
        if p.is_dir():
            v = parse_version(p.name)
            if v:
                candidates.append((v, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def pick_exe(exes: list[Path], program_name: str, version_folder_name: str | None = None) -> Path:
    # 1) exact program name match
    for e in exes:
        if e.stem.lower() == program_name.lower():
            return e

    # 2) if we know version folder, prefer exe containing that version (e.g. "Program v1.2.3")
    if version_folder_name:
        ver = version_folder_name.lower()
        for e in exes:
            if ver in e.stem.lower():
                return e

    # 3) fallback first by name
    return sorted(exes, key=lambda p: p.name.lower())[0]


def _norm(s: str) -> str:
    # normalize for matching: lower + remove separators
    # (underscores/spaces/hyphens/dots)
    s = s.lower()
    for ch in ("_", " ", "-", "."):
        s = s.replace(ch, "")
    return s


def find_docs(program_dir: Path, program_name: str) -> tuple:
    """(readme, readme_full) of a program, from ONE listing of its folder.

    ReadMe:  ReadMe_<ProgramName>
    Details: ReadMe_<ProgramName>_Full / _Details, or Manual_<ProgramName>
    Case, underscores/spaces/hyphens/dots and the extension are ignored. The
    folder is on a network share, so it is listed once for both.
    """
    target = _norm("readme_" + program_name)
    full_targets = {
        _norm("readme_" + program_name + "_full"),
        _norm("readme_" + program_name + "_details"),
        _norm("manual_" + program_name),
    }
    readme = full = None
    try:
        for f in program_dir.iterdir():
            if readme is not None and full is not None:
                break
            stem, name = _norm(f.stem), _norm(f.name)
            if readme is None and (stem == target or name == target):
                if f.is_file():
                    readme = f
            elif full is None and (stem in full_targets or name in full_targets):
                if f.is_file():
                    full = f
    except Exception:
        pass
    return readme, full


def find_readme_or_none(program_dir: Path, program_name: str) -> Path | None:
    return find_docs(program_dir, program_name)[0]


def find_readme_full_or_none(program_dir: Path, program_name: str) -> Path | None:
    return find_docs(program_dir, program_name)[1]


# ---------------- GROUPING CONFIG ----------------
# Global rules (same for ALL roots).
# Everything else (not listed) becomes "External".

SCRIPTS = {
    # folder names of programs that should be in "Main scripts"
    "Image Tools",
    "Screenshots",
    "Announcer",
    "CSS Logger",
    "Chiller log",
}

PARTS = {
    "Launcher",
}

# Programs that are no longer programs: their whole feature now lives inside a
# bigger one, and the folder left on the share only offers an old build of it.
# They are dropped in scan_programs(), so they appear in no group at all — a
# card here would just be a way to start last spring's version by mistake.
# Matched through _norm(), so spelling and spacing of the folder do not matter.
SUBSUMED = {
    "Image Finder":  "Image Tools",
    "Image Slider":  "Image Tools",
    "Shot finder":   "Image Tools",
    "Spectra":       "CSS Logger",
    "Builder":       "Dev Tools",
    "Copy manager":  "Dev Tools",
}

# Programs that are retired outright, with nothing replacing them. Dropped in
# scan_programs() the same way as SUBSUMED.
RETIRED = {
    "Time Converter",
}

IN_PROGRESS = {
    "Calibrations",
    # Finished, but it watches nothing until the hall shooting channels and the
    # watched channels are filled in on its Settings tab. Move it to SCRIPTS
    # once they are.
    "Laser settings",
}

NOT_WORKING_CORRECTLY = {
    "Counter of Shots"
}

PERSONAL = {
    "Copy Manager",
    "Builder",
    "Internal Builder",
    "Dev Tools",
    "Git Work",
}

GROUP_ORDER = [
    ("Main scripts", "scripts"),
    ("Side scripts", "parts"),
    ("External", "external"),
    ("In progress", "in_progress"),
    ("Not working correctly", "not_working_correctly"),
    ("Personal", "personal"),
]


def _norm_set(values: set[str]) -> set[str]:
    return {_norm(v) for v in values}


# pre-normalize once
SCRIPTS_N = _norm_set(set(SCRIPTS))
PARTS_N = _norm_set(set(PARTS))
SUBSUMED_N = {_norm(k): v for k, v in SUBSUMED.items()}
RETIRED_N = _norm_set(set(RETIRED))
PERSONAL_N = _norm_set(set(PERSONAL))
IN_PROGRESS_N = _norm_set(set(IN_PROGRESS))
NOT_WORKING_CORRECTLY_N = _norm_set(set(NOT_WORKING_CORRECTLY))

def group_for_program(program_name: str) -> str:
    key = _norm(program_name)
    if key in IN_PROGRESS_N:
        return "in_progress"
    if key in PARTS_N:
        return "parts"
    if key in NOT_WORKING_CORRECTLY_N:
        return "not_working_correctly"
    if key in SCRIPTS_N:
        return "scripts"
    if key in PERSONAL_N:
        return "personal"
    return "external"

# ---------------- SCAN LOGIC ----------------
def _build_version_list(current_exes: list[Path], program_dir: Path) -> list[dict]:
    """
    Sestaví seznam všech verzí pro dropdown:
    1) Aktuální exe soubory (seřazené od nejvyšší verze, bez timestampy)
    2) Archivní exe soubory (seřazené od nejvyšší verze, s timestampou)
    """
    result = []

    # Aktuální verze – řadit podle vX.Y.Z
    for p in sorted(current_exes, key=_exe_version, reverse=True):
        m = _VER_ANYWHERE_RE.search(p.stem)
        label = m.group(1) if m else p.stem
        # These sit in the program folder already, next to its _internal, so
        # _launch_exe starts them where they lie — nothing is moved.
        result.append({"exe_path": p, "py_path": None,
                       "has_internal": False, "label": label})

    # Archivní verze
    result.extend(scan_archive_versions(program_dir))

    return result

def _scan_one_program(program_dir: Path, root: Path) -> "tuple[str, dict] | None":
    """Scan a single program directory. Returns (name, info) or None."""
    program_name = program_dir.name
    readme, readme_full = find_docs(program_dir, program_name)

    # A) scratch layout: exe přímo ve složce programu
    exes_root = list(program_dir.glob("*.exe"))
    if exes_root:
        exes_root.sort(key=_exe_version, reverse=True)
        exe = exes_root[0]
        clean_exes = [p for p in exes_root if not TIMESTAMPED_EXE_RE.match(p.name)]
        return (program_name, {
            "exe_path": exe,
            "readme_path": readme,
            "readme_full_path": readme_full,
            "label": program_name,
            "program_dir": program_dir,
            "icon_path": find_icon_for_program(program_dir, exe),
            "archive_versions": _build_version_list(clean_exes, program_dir),
        })

    # B) programy layout: ROOT\dist\<Program>\vX.Y.Z\*.exe
    # The sources sit in the repo while `dist` stays next to it, so a dist folder
    # beside the scan root counts too — otherwise scanning the repo finds sources
    # with no exe and reports nothing.
    vf = None
    for dist_dir in _dist_candidates(root, program_name):
        vf = newest_version_folder(dist_dir)
        if vf:
            break
    if not vf:
        return None
    exes_v = list(vf.glob("*.exe"))
    if not exes_v:
        exes_v = list(vf.rglob("*.exe"))
    if not exes_v:
        return None
    exe = pick_exe(exes_v, program_name, vf.name)
    return (program_name, {
        "exe_path": exe,
        "readme_path": readme,
        "readme_full_path": readme_full,
        "label": program_name,
        "program_dir": program_dir,
        "icon_path": find_icon_for_program(program_dir, exe),
        "archive_versions": _build_version_list([], program_dir),
    })


def scan_programs(root: Path) -> dict[str, dict]:
    if not root.exists():
        raise FileNotFoundError(f"Software root not found: {root}")

    dirs = [
        p for p in root.iterdir()
        if p.is_dir() and p.name.lower() not in IGNORE_DIR_NAMES
        and _norm(p.name) not in SUBSUMED_N
        and _norm(p.name) not in RETIRED_N
    ]

    from concurrent.futures import ThreadPoolExecutor, as_completed
    programs = {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(_scan_one_program, d, root): d for d in dirs}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                name, info = result
                programs[name] = info

    return programs

def ui_label(s: str) -> str:
    return " ".join(s.replace("_", " ").split())

def find_icon_for_program(program_dir: Path, exe_path: Path) -> Path | None:
    ico = exe_path.parent / "icon.ico"
    return ico if ico.exists() else None

def clamp_label(text: str, max_len: int = 22) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."

def _find_python() -> Path | None:
    """An interpreter able to run an archived .py snapshot, or None.

    pythonw.exe is preferred over python.exe: these are windowed programs, and
    python.exe would park an empty black console behind every one of them.
    """
    if not getattr(sys, "frozen", False):
        beside = Path(sys.executable).parent / "pythonw.exe"
        return beside if beside.exists() else Path(sys.executable)
    for name in ("pythonw", "python", "py"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


OLD_RUN_LOG = CONFIG_PATH.parent / "old_version_run.log"


def _launch_no_zone_check(path: Path) -> bool:
    """Launch exe via ShellExecuteEx with SEE_MASK_NOZONECHECKS — suppresses security dialog for network paths."""
    import ctypes
    import ctypes.wintypes

    SEE_MASK_NOZONECHECKS = 0x00800000
    SEE_MASK_NOCLOSEPROCESS = 0x00000040

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize",       ctypes.wintypes.DWORD),
            ("fMask",        ctypes.wintypes.ULONG),
            ("hwnd",         ctypes.wintypes.HWND),
            ("lpVerb",       ctypes.wintypes.LPCWSTR),
            ("lpFile",       ctypes.wintypes.LPCWSTR),
            ("lpParameters", ctypes.wintypes.LPCWSTR),
            ("lpDirectory",  ctypes.wintypes.LPCWSTR),
            ("nShow",        ctypes.c_int),
            ("hInstApp",     ctypes.wintypes.HINSTANCE),
            ("lpIDList",     ctypes.c_void_p),
            ("lpClass",      ctypes.wintypes.LPCWSTR),
            ("hkeyClass",    ctypes.wintypes.HKEY),
            ("dwHotKey",     ctypes.wintypes.DWORD),
            ("hIconOrMonitor", ctypes.wintypes.HANDLE),
            ("hProcess",     ctypes.wintypes.HANDLE),
        ]

    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOZONECHECKS | SEE_MASK_NOCLOSEPROCESS
    sei.hwnd = None
    sei.lpVerb = "open"
    sei.lpFile = str(path)
    sei.lpParameters = None
    sei.lpDirectory = str(path.parent)
    sei.nShow = 1  # SW_SHOWNORMAL

    return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)))


# A stray in archive\ carries a version but no timestamp — the loose twin of a
# file that belongs inside archive\vX.Y.Z\. Timestamped names are left alone:
# those are the old flat archive, which the launcher still lists as versions.
STRAY_ARCHIVE_RE = re.compile(
    r"^.+\s+v(\d+)\.(\d+)\.(\d+)(?:\s+\(\d+\))?\.(?:exe|py)$",
    re.IGNORECASE
)


def find_misplaced_version_files(programs: dict[str, dict]) -> list[tuple[Path, Path | None]]:
    """Files that carry a version but do not sit where that version lives.

    Returns (source, destination) pairs; a destination of None means the file is
    a byte-for-byte duplicate of the one already in its version folder and is
    only taking up room, so it is offered for deletion instead of a move.

    Two kinds are found:
      * an exe with a timestamp lying in the program folder — it belongs in
        archive\\;
      * an exe or script lying loose in archive\\ itself — it belongs in its own
        archive\\vX.Y.Z\\ folder. Neither the version list nor the old cleanup
        ever looked at these, so they stayed invisible.
    """
    misplaced: list[tuple[Path, Path | None]] = []
    for name, info in programs.items():
        program_dir: Path = info.get("program_dir")
        if not program_dir:
            continue
        archive_dir = program_dir / "archive"

        for p in program_dir.glob("*.exe"):
            if TIMESTAMPED_EXE_RE.match(p.name):
                misplaced.append((p, archive_dir / p.name))

        if not archive_dir.is_dir():
            continue
        for p in sorted(archive_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in (".exe", ".py"):
                continue
            if TIMESTAMPED_EXE_RE.match(p.name) or ARCHIVE_EXE_RE.match(p.name):
                continue
            m = STRAY_ARCHIVE_RE.match(p.name)
            if not m:
                continue
            target = archive_dir / ("v" + ".".join(m.groups()))
            twin = target / p.name
            try:
                same = twin.is_file() and twin.stat().st_size == p.stat().st_size
            except OSError:
                same = False
            misplaced.append((p, None if same else twin))
    return misplaced

def apply_misplaced_cleanup(misplaced: list[tuple[Path, Path | None]]) -> list[str]:
    """Carry out the moves and deletions; returns one line per file that failed."""
    errors = []
    for src, dst in misplaced:
        try:
            if dst is None:
                src.unlink()
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Same name but a different size: two builds really do differ, so
            # neither is thrown away. The " (2)" suffix is a name the version
            # list already reads, and the plain one still wins there.
            base, n = dst, 2
            while dst.exists():
                dst = base.with_name(f"{base.stem} ({n}){base.suffix}")
                n += 1
            shutil.move(str(src), str(dst))
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    return errors



def find_programs_in_swap_state(programs: dict[str, dict]) -> list[tuple[str, Path, Path]]:
    """Return (program_name, program_dir, temp_dir) for programs stuck mid old-version swap."""
    stuck = []
    for name, info in programs.items():
        program_dir: Path = info.get("program_dir")
        if not program_dir:
            continue
        temp_dir = program_dir / "archive" / "_temp_latest"
        if temp_dir.exists() and temp_dir.is_dir():
            stuck.append((name, program_dir, temp_dir))
    return stuck


# ---------------- APP ----------------
def _icon_app_id(prefix, ico_path):
    """A taskbar identity that is new on every launch.

    Windows caches the taskbar picture per AppUserModelID and never re-reads
    it, so every *stable* id tried here eventually picked up a bad cache entry
    and then drew the blank window placeholder for good: a fixed string, a hash
    of the icon, and a hash tagged with the build's file name each broke within
    days. Setting no id at all was no better -- Windows then keys the button on
    the exe path and caches the picture there instead (Diagnostic v1.3.1,
    measured 2026-09-17: the window icon, the exe's own icon resource and the
    shell's own file icon all correct, the taskbar button blank).

    An id Windows has never seen has no cache entry, so the button falls back
    to the window icon, which every program here sets itself -- measured on a
    fresh id on 2026-09-04 and again on 2026-09-17. A random suffix per launch
    makes every run a first-time id, which is why this is the one form that
    cannot go stale. Nothing here needs a stable identity: no program registers
    a shortcut, pins itself or sends Windows toasts. The one cost is pinning a
    *running* taskbar button -- that pin would carry this run's id and would
    not start the program again, so pin the exe instead.

    Returns None when there is no icon at all; the caller then sets no id and
    the button keeps taking the exe's own picture.
    """
    import os.path
    if not ico_path or not os.path.exists(str(ico_path)):
        return None
    import uuid
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def _nudge_taskbar_icon(hwnd) -> None:
    """Make the taskbar re-read this window's icon a few times after start.

    Explorer reads a taskbar button's picture once, when it creates the
    button, and does not ask again by itself. When that one read misses, the
    button keeps the blank window placeholder for the whole run although the
    window holds the right icon -- measured on Diagnostic v1.3.4, 15.10.2026:
    started from C:, a fresh per-launch id, WM_GETICON answering with the
    magnifier, the button blank. Re-sending the same icon changed nothing;
    taking the icon away and giving it back made Explorer re-read it, and the
    same running window drew the magnifier at once. So that is done here a few
    times while the button may still be appearing (a slow start, a busy
    Explorer after logon). Runs on a daemon thread; the window's own icon
    handles are put back, so nothing is created or leaked.
    """
    if not hwnd:
        return
    import threading

    def run():
        import ctypes
        import ctypes.wintypes as wt
        import time
        user32 = ctypes.windll.user32
        send = user32.SendMessageTimeoutW
        send.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT,
                         wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = ctypes.c_size_t
        res = ctypes.c_size_t()
        for delay in (2, 8, 20, 30):          # at about 2, 10, 30 and 60 s
            time.sleep(delay)
            try:
                if not user32.IsWindow(wt.HWND(hwnd)):
                    return
                held = {}
                for which in (1, 0):          # ICON_BIG, ICON_SMALL
                    res.value = 0
                    # SMTO_ABORTIFHUNG: a frozen window just skips this round
                    if send(hwnd, 0x007F, which, 0, 0x0002, 2000,
                            ctypes.byref(res)) and res.value:
                        held[which] = res.value
                for which in held:
                    send(hwnd, 0x0080, which, 0, 0x0002, 2000, ctypes.byref(res))
                for which, h in held.items():
                    send(hwnd, 0x0080, which, h, 0x0002, 2000, ctypes.byref(res))
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name="taskbar-icon").start()


# ---------------- QT LOOK ----------------
APP_ID_PREFIX = "ELI.Launcher"
UI_FONT = "Segoe UI"
INK = "#111111"
AMBER = "#ffa500"

# Every colour stated: Windows here runs in dark mode, and anything left to the
# theme comes out black on black. Each rule that sets `color:` has its disabled
# twin, because the plain rule paints the disabled state too.
APP_STYLESHEET = f"""
QWidget {{ background: #f3f3f3; color: {INK}; font-family: "{UI_FONT}"; font-size: 9pt; }}
QLabel {{ background: transparent; }}
QLabel#hint {{ color: #404040; }}
QGroupBox {{ border: 1px solid #c4c4c4; border-radius: 4px; margin-top: 14px;
             padding-top: 6px; background: #f3f3f3; font-weight: bold; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {INK}; }}
QRadioButton {{ background: transparent; font-weight: normal; }}
QRadioButton:disabled {{ color: #9a9a9a; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border: 1px solid #555555;
                           border-radius: 7px; background: #ffffff; }}
QRadioButton::indicator:hover {{ border-color: #2f6fb5; }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5,
    radius:0.5, fx:0.5, fy:0.5, stop:0 {INK}, stop:0.5 {INK}, stop:0.62 #ffffff,
    stop:1 #ffffff); }}
QRadioButton::indicator:disabled {{ background: #eeeeee; border-color: #b0b0b0; }}
QPushButton {{ background: #e8e8e8; color: {INK}; border: 1px solid #a8a8a8;
               border-radius: 3px; padding: 4px 10px; font-weight: normal; }}
QPushButton:hover {{ background: #d9e8ff; }}
QPushButton:pressed {{ background: #c4d8f5; }}
QPushButton:disabled {{ background: #eeeeee; color: #9a9a9a; border-color: #cccccc; }}
QPushButton[alert="true"] {{ background: {AMBER}; color: {INK}; border-color: #c77d00; }}
QPushButton[alert="true"]:hover {{ background: #ffb733; }}
QPushButton[alert="true"]:pressed {{ background: #e09000; }}
QPushButton#prog {{ text-align: left; padding: 4px 8px; }}
QPushButton#prog[update="true"] {{ background: {AMBER}; color: {INK}; border-color: #c77d00; }}
QPushButton#prog[update="true"]:hover {{ background: #ffb733; }}
QPushButton#prog[update="true"]:pressed {{ background: #e09000; }}
QPushButton#doc {{ padding: 2px 3px; font-size: 8pt; }}
QToolButton#small {{ background: #e8e8e8; color: {INK}; border: 1px solid #a8a8a8;
                     border-radius: 3px; padding: 2px; }}
QToolButton#small:hover {{ background: #d9e8ff; }}
QToolButton#small:pressed {{ background: #c4d8f5; }}
QToolButton#small::menu-indicator {{ image: none; width: 0px; }}
QToolButton#groupHeader {{ background: transparent; color: {INK}; border: none;
                           border-radius: 3px; padding: 3px 6px 3px 2px;
                           font-size: 10pt; font-weight: bold; }}
QToolButton#groupHeader:hover {{ background: #e2e2e2; }}
QScrollArea {{ border: 1px solid #c4c4c4; background: #f3f3f3; }}
QWidget#body {{ background: #f3f3f3; }}
QScrollBar:vertical {{ background: #e6e6e6; width: 14px; margin: 0; border: none; }}
QScrollBar::handle:vertical {{ background: #b4b4b4; min-height: 28px;
                               border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: #9a9a9a; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QMenu {{ background: #ffffff; color: {INK}; border: 1px solid #a8a8a8; padding: 3px 0; }}
QMenu::item {{ padding: 4px 22px 4px 8px; background: transparent; }}
QMenu::item:selected {{ background: #2f6fb5; color: #ffffff; }}
QMenu::item:disabled {{ color: #555555; }}
QMenu::separator {{ height: 1px; background: #d0d0d0; margin: 3px 6px; }}
QToolTip {{ background: #ffffcc; color: {INK}; border: 1px solid #aaa; padding: 4px; }}
QMessageBox, QDialog {{ background: #f3f3f3; }}
"""


def light_palette() -> QPalette:
    """The same colours for what a stylesheet cannot reach (pop-ups, the
    native folder picker's frame, message boxes)."""
    pal = QPalette()
    for role, colour in ((QPalette.Window, "#f3f3f3"), (QPalette.WindowText, INK),
                         (QPalette.Base, "#ffffff"), (QPalette.AlternateBase, "#f7f7f7"),
                         (QPalette.Text, INK), (QPalette.Button, "#e8e8e8"),
                         (QPalette.ButtonText, INK), (QPalette.ToolTipBase, "#ffffcc"),
                         (QPalette.ToolTipText, INK), (QPalette.Highlight, "#2f6fb5"),
                         (QPalette.HighlightedText, "#ffffff"),
                         (QPalette.PlaceholderText, "#777777")):
        pal.setColor(role, QColor(colour))
    return pal


def apply_app_style(app) -> None:
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(APP_STYLESHEET)


def _ui_font(pt: float, bold: bool = False) -> QFont:
    """The font the stylesheet paints with, for measuring. A stylesheet font
    never reaches widget.font(), so measuring that would use the wrong one."""
    f = QFont(UI_FONT)
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def _repolish(w) -> None:
    """Re-read the stylesheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ---------------- DRAWN ICONS ----------------
# Drawn, never typed: an emoji resolves to a colour font that ignores the
# stylesheet, and a symbol glyph comes out as a hairline or not at all.

def _pen(ink: str, width: float = 1.6) -> QPen:
    pen = QPen(QColor(ink), width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _paint_symbol(p: QPainter, name: str, ink: str, fill: str | None) -> None:
    """One symbol on a 20 x 20 grid."""
    c = QColor(ink)
    if name == "notes":
        p.setPen(_pen(ink))
        p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        p.drawRoundedRect(QRectF(4, 2.5, 12, 15), 1.5, 1.5)
        p.setPen(_pen(ink, 1.3))
        for y in (7, 10, 13):
            p.drawLine(QPointF(7, y), QPointF(13, y))
    elif name == "gear":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for i in range(8):
            p.save()
            p.translate(10, 10)
            p.rotate(i * 45)
            p.drawRoundedRect(QRectF(-1.7, -8.8, 3.4, 4.2), 0.6, 0.6)
            p.restore()
        ring = QPainterPath()
        ring.addEllipse(QPointF(10, 10), 6.2, 6.2)
        hole = QPainterPath()
        hole.addEllipse(QPointF(10, 10), 2.6, 2.6)
        p.drawPath(ring.subtracted(hole))
    elif name == "broom":
        p.setPen(_pen(ink, 2.0))
        p.drawLine(QPointF(16.5, 2.5), QPointF(10.5, 9.5))
        p.setPen(_pen(ink, 1.2))
        p.setBrush(QColor(fill) if fill else c)
        p.drawPolygon(QPolygonF([QPointF(8.2, 7.8), QPointF(13, 11.6),
                                 QPointF(9.6, 17.8), QPointF(2.4, 13.4)]))
        p.setPen(_pen(ink, 1.0))
        for a, b in (((7.2, 11.2), (4.4, 15.0)), ((9.4, 12.6), (7.2, 16.6))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif name == "folder":
        p.setPen(_pen(ink, 1.4))
        p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(2.5, 4.5)
        path.lineTo(8, 4.5)
        path.lineTo(9.8, 6.5)
        path.lineTo(17.5, 6.5)
        path.lineTo(17.5, 15.8)
        path.lineTo(2.5, 15.8)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(2.5, 8.6), QPointF(17.5, 8.6))
    elif name == "history":
        p.setPen(_pen(ink, 1.7))
        p.setBrush(QColor(fill) if fill else Qt.NoBrush)
        p.drawEllipse(QPointF(10, 10), 7, 7)
        p.drawLine(QPointF(10, 10), QPointF(10, 5.6))
        p.drawLine(QPointF(10, 10), QPointF(13.2, 12))
    elif name == "closed":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(7, 4.5), QPointF(14.5, 10), QPointF(7, 15.5)]))
    elif name == "open":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(4.5, 7), QPointF(15.5, 7), QPointF(10, 14.5)]))
    elif name == "tick":
        p.setPen(_pen(ink, 2.3))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(4, 10.5), QPointF(8.5, 15), QPointF(16, 5.5)]))


_FILLS = {"notes": "#ffffff", "folder": "#f2c14e", "broom": "#d9a441", "history": "#ffffff"}
_ICON_CACHE: dict = {}


def drawn_icon(name: str) -> QIcon:
    """A symbol in every QIcon mode, at several sizes so it stays sharp at any
    display scaling. Needs a running QApplication."""
    icon = _ICON_CACHE.get(name)
    if icon is not None:
        return icon
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        for mode, ink, fill in ((QIcon.Normal, INK, _FILLS.get(name)),
                                (QIcon.Active, INK, _FILLS.get(name)),
                                (QIcon.Selected, "#ffffff", None),
                                (QIcon.Disabled, "#9a9a9a", None)):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.scale(size / 20, size / 20)
            _paint_symbol(p, name, ink, fill)
            p.end()
            icon.addPixmap(pm, mode, QIcon.Off)
    _ICON_CACHE[name] = icon
    return icon


# ---------------- DIALOGS ----------------
_MAIN = None   # the launcher window, parent of every message box


def _message(kind, title: str, text: str, parent=None) -> None:
    box = QMessageBox(parent if parent is not None else _MAIN)
    box.setIcon(kind)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


class messagebox:
    """The two message boxes the launch code calls, under their tkinter names,
    so that code stays exactly as it is in the Tk Launcher."""

    @staticmethod
    def showerror(title, text, parent=None):
        _message(QMessageBox.Icon.Critical, title, text, parent)

    @staticmethod
    def showinfo(title, text, parent=None):
        _message(QMessageBox.Icon.Information, title, text, parent)


def _ask(parent, title: str, text: str, yes: str, no: str) -> bool:
    """A question with two named buttons; True for the first one."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(16, 12, 16, 12)
    v.setSpacing(12)
    v.addWidget(QLabel(text, dlg))
    row = QHBoxLayout()
    row.addStretch(1)
    for label, slot in ((yes, dlg.accept), (no, dlg.reject)):
        b = QPushButton(label, dlg)
        b.setMinimumWidth(90)
        b.clicked.connect(slot)
        row.addWidget(b)
    row.addStretch(1)
    v.addLayout(row)
    return dlg.exec() == QDialog.Accepted


def prompt_move_misplaced(parent, misplaced: list[tuple[Path, Path | None]]):
    """Zobrazí souhrnnou hlášku a nabídne přesun."""
    rows = []
    for src, dst in misplaced:
        where = src.parent.name
        if dst is None:
            rows.append(f"  {where}\\{src.name}\n"
                        f"      the same file is already filed away — delete this copy")
        else:
            rows.append(f"  {where}\\{src.name}\n"
                        f"      move to  {dst.parent.name}\\")
    msg = (
        "The following versioned files are not where their version lives:\n\n"
        + "\n".join(rows)
        + "\n\nTidy them up?"
    )
    if not _ask(parent, "Misplaced files found", msg, "Tidy up", "Skip"):
        return

    errors = apply_misplaced_cleanup(misplaced)
    if errors:
        messagebox.showerror(
            "Cleanup failed",
            "Some files could not be tidied up:\n\n" + "\n".join(errors),
            parent=parent,
        )


def prompt_restore_swap_state(parent, stuck: list[tuple[str, Path, Path]]):
    """Dialog that offers to restore programs stuck in an incomplete old-version launch."""
    lines = "\n".join(f"  {name}" for name, _, _ in stuck)
    msg = (
        f"The following programs have an incomplete old-version launch.\n"
        f"Their latest files are stored in a temporary folder:\n\n"
        f"{lines}\n\n"
        f"Restore the latest versions now?"
    )
    if not _ask(parent, "Incomplete old-version launch detected", msg, "Restore", "Skip"):
        return

    errors = []
    for name, program_dir, temp_dir in stuck:
        try:
            for f in list(temp_dir.iterdir()):
                shutil.move(str(f), str(program_dir / f.name))
            try:
                temp_dir.rmdir()
            except Exception:
                pass
        except Exception as e:
            errors.append(f"{name}: {e}")
    if errors:
        messagebox.showerror(
            "Restore failed",
            "Some programs could not be restored:\n\n" + "\n".join(errors),
            parent=parent,
        )


# ---------------- APP ----------------
def _icon_file() -> Path | None:
    """icon.ico next to the exe, then the copy bundled inside it (the builder
    adds it with --add-data), then next to this file."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "icon.ico")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(Path(meipass) / "icon.ico")
    cands.append(Path(__file__).resolve().parent / "icon.ico")
    for p in cands:
        if p.exists():
            return p
    return None


def _app_version() -> str:
    """Version of the running build, e.g. "v2.0.0", or "" when run from source.
    The builder names every exe "<Program> vX.Y.Z.exe", so the exe's own file
    name is the one place that always tells the truth."""
    if not getattr(sys, "frozen", False):
        return ""
    m = re.search(r"\s(v\d+(?:\.\d+)*)$", Path(sys.executable).stem)
    return m.group(1) if m else ""


class _Invoker(QObject):
    """Hands a call to the window's thread; the worker threads use it."""
    call = Signal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _run(job):
        ms, fn, args = job
        QTimer.singleShot(ms, lambda: fn(*args))


class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        global _MAIN
        _MAIN = self
        self._invoker = _Invoker(self)
        self._scan_gen = 0
        self._config = _load_config()
        self._root_options = _apply_config_to_root_options(self._config)
        try:
            _hn = socket.gethostname().upper().strip()
        except Exception:
            _hn = ""
        self._is_lab_machine = any(kw in _hn for kw in ("OPR", "VIS"))

        ver = _app_version()
        self.setWindowTitle(f"Launcher {ver}" if ver else "Launcher (source)")
        icon = _icon_file()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(600, 510)
        self.setMinimumSize(550, MIN_WINDOW_H)

        self.programs: dict[str, dict] = {}
        self.current_root_label: str | None = None
        self.current_root_path: Path | None = None
        self._root_idx = -1                            # no source picked yet
        self._icon_cache: dict[str, QIcon | None] = {}
        self._update_available: set[str] = set()       # programs with newer version on disk
        self._pending_updates: dict[str, Path] = {}    # name → newer exe path (not yet acknowledged)
        self._prog_buttons: dict[str, QPushButton] = {}  # current button widget per program
        self._prog_cells: dict[str, QWidget] = {}        # the whole card per program
        # Acknowledged versions: {program_name: version_str} — persisted in config
        # Once a user launches or acknowledges, the version is acknowledged and highlight clears.
        self._acknowledged: dict[str, str] = self._config.get("acknowledged_versions", {})
        # Custom group overrides: {program_name: gkey} — persisted in config
        self._custom_groups: dict[str, str] = self._config.get("custom_groups", {})
        # Persistent collapse state: gkey → bool (True = expanded)
        self._group_expanded: dict[str, bool] = {}
        self._group_items: dict[str, list] = {}
        self._group_toggle_btns: dict[str, QToolButton] = {}
        self._group_grids: dict[str, QWidget] = {}
        self._group_layouts: dict[str, QGridLayout] = {}
        self._group_built: set[str] = set()            # groups whose cards exist
        self._prog_btn_w = 0                           # one width for every program button
        self._doc_btn_w: int | None = None

        self._build_ui()
        self._set_idle_state()
        self._auto_select_root()
        self._schedule_version_poll()

    # -------- the Tk `after`, safe from any thread --------

    def after(self, ms: int, fn, *args):
        """Run fn(*args) on the window's thread in `ms` milliseconds.

        Callable from the worker threads, which may never touch a widget
        themselves; the call is queued to the window's thread first.
        """
        self._invoker.call.emit((ms, fn, args))

    def _set_status(self, text: str):
        self.status.setText(text)

    # -------- window --------

    def _root_enabled(self, label: str, path) -> bool:
        return path is not None and not (self._is_lab_machine
                                         and label.lower().startswith("office"))

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # Root selector (radio buttons)
        box = QGroupBox("Data source", central)
        outer.addWidget(box)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(8, 8, 8, 8)
        bl.setSpacing(6)

        rb_row = QHBoxLayout()
        rb_row.setSpacing(12)
        bl.addLayout(rb_row)
        self._rb_group = QButtonGroup(self)
        self._rb_widgets: list[QRadioButton] = []
        for i, (label, path, _configurable) in enumerate(self._root_options):
            rb = QRadioButton(label, box)
            rb.setEnabled(self._root_enabled(label, path))
            self._rb_group.addButton(rb, i)
            rb_row.addWidget(rb)
            self._rb_widgets.append(rb)
        rb_row.addStretch(1)
        self._rb_group.idClicked.connect(self._on_root_clicked)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        bl.addLayout(btn_row)
        for text, icon, slot in (("Notes", "notes", self.open_notes),
                                 ("Set paths", "gear", self.open_set_paths)):
            b = QPushButton(text, box)
            b.setIcon(drawn_icon(icon))
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(slot)
            btn_row.addWidget(b)
        self._clean_btn = QPushButton("Clean", box)
        self._clean_btn.setIcon(drawn_icon("broom"))
        self._clean_btn.setIconSize(QSize(16, 16))
        self._clean_btn.clicked.connect(self._run_cleanup)
        btn_row.addWidget(self._clean_btn)
        btn_row.addStretch(1)

        # Program cards. Never a horizontal scroll bar: the window is kept wide
        # enough for two of the widest cards instead (_fit_width).
        self.scroll = QScrollArea(central)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._inner = QWidget()
        self._inner.setObjectName("body")
        self._inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._body = QVBoxLayout(self._inner)
        self._body.setContentsMargins(6, 4, 6, 6)
        self._body.setSpacing(2)
        self.scroll.setWidget(self._inner)
        outer.addWidget(self.scroll, 1)

        # Status. A long source path plus the tidy-up note does not fit one
        # line; it wraps instead of being cut off at the window edge.
        self.status = QLabel("", central)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._prog_buttons = {}
        self._prog_cells = {}
        self._group_toggle_btns = {}
        self._group_grids = {}
        self._group_layouts = {}
        self._group_built = set()

    def _body_note(self, text: str):
        lbl = QLabel(text, self._inner)
        lbl.setContentsMargins(8, 8, 8, 8)
        self._body.addWidget(lbl)
        self._body.addStretch(1)

    def _set_idle_state(self):
        self._clear_body()
        self._body_note("Select a data source above.")
        self.status.setText("No source selected.")

    def _set_root_idx(self, idx: int):
        self._root_idx = idx
        if idx >= 0:
            self._rb_widgets[idx].setChecked(True)
            return
        # An exclusive group refuses to have nothing checked.
        self._rb_group.setExclusive(False)
        for rb in self._rb_widgets:
            rb.setChecked(False)
        self._rb_group.setExclusive(True)

    def _on_root_clicked(self, idx: int):
        self._root_idx = idx
        self.refresh()

    def _auto_select_root(self):
        try:
            hostname = socket.gethostname().upper().strip()
        except Exception:
            hostname = ""
        # OPR / VIS stations → Lab - Scratch (index 0); everything else → Office - Scratch (index 1)
        if any(kw in hostname for kw in ("OPR", "VIS")):
            preferred = 0
        else:
            preferred = 1  # CZOW and all others
        opts = self._root_options
        idx = preferred if preferred < len(opts) and opts[preferred][1] is not None else \
              next((i for i, (_, p, _) in enumerate(opts) if p is not None), -1)
        if idx >= 0:
            self._set_root_idx(idx)
            self.refresh()

    def _selected_root(self) -> tuple[str, Path] | None:
        idx = self._root_idx
        if idx < 0:
            return None
        entry = self._root_options[idx]
        return entry[0], entry[1]

    def refresh(self):
        sel = self._selected_root()
        if not sel:
            self._set_idle_state()
            return

        root_label, selected_root = sel
        self.current_root_label = root_label
        self.current_root_path = selected_root

        self._scan_gen += 1          # every new scan gets a new number
        gen = self._scan_gen

        self.status.setText(f"Scanning: {selected_root} ...")

        def worker():
            try:
                progs = scan_programs(selected_root)
                # The tidy-up check lists every archive folder on the share —
                # done here, off the window's thread, not after the cards appear.
                try:
                    tidy = (find_misplaced_version_files(progs),
                            find_programs_in_swap_state(progs))
                except Exception:
                    tidy = ([], [])
                self.after(0, lambda: self._apply_programs(progs, root_label, selected_root, gen, tidy))
            except Exception as e:
                # Said in the status line, not in a pop-up: this also runs at
                # start-up, when an unreachable share is no reason to grab focus.
                msg = f"Scan failed: {e}"

                def show_failure():
                    self._apply_programs({}, root_label, selected_root, gen)
                    if gen == self._scan_gen:
                        self.status.setText(msg)
                self.after(0, show_failure)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_programs(self, progs: dict[str, dict], root_label: str, selected_root: Path, gen: int,
                        tidy: tuple | None = None):
        if gen != self._scan_gen:
            return
        self.programs = progs
        self.current_root_label = root_label
        self.current_root_path = selected_root
        self._update_available.clear()
        self._pending_updates.clear()
        # Startup check: flag programs whose on-disk version differs from last acknowledged
        for name, info in self.programs.items():
            ack_ver = self._acknowledged.get(name)
            if ack_ver is None:
                continue  # never acknowledged → no baseline to compare against
            if _ver_str(_exe_version(info.get("exe_path"))) != ack_ver:
                self._update_available.add(name)
        self._rebuild_buttons()
        status = f"Source: {selected_root} | Found: {len(self.programs)} programs."

        # Nothing opens by itself: a dialog that pops up after a scan takes the
        # keyboard from whatever the user is typing into. The Clean button
        # shows that there is something to tidy, and the dialogs open from it.
        misplaced, stuck = tidy if tidy is not None else ([], [])
        self._mark_clean_button(len(misplaced) + len(stuck))
        if misplaced or stuck:
            parts = []
            if misplaced:
                parts.append(f"{len(misplaced)} file(s) in the wrong archive folder")
            if stuck:
                parts.append(f"{len(stuck)} unfinished old-version launch(es)")
            status += " | To tidy (Clean): " + ", ".join(parts) + "."
        self.status.setText(status)

    def _mark_clean_button(self, count: int):
        self._clean_btn.setText(f"Clean ({count})" if count else "Clean")
        self._clean_btn.setProperty("alert", bool(count))
        _repolish(self._clean_btn)

    def _run_cleanup(self):
        if not self.programs:
            messagebox.showinfo("Clean", "No programs loaded. Select a data source first.",
                                parent=self)
            return
        misplaced = find_misplaced_version_files(self.programs)
        stuck = find_programs_in_swap_state(self.programs)
        if not misplaced and not stuck:
            self._mark_clean_button(0)
            messagebox.showinfo("Clean", "No misplaced files found.", parent=self)
            return
        if misplaced:
            prompt_move_misplaced(self, misplaced)
        if stuck:
            prompt_restore_swap_state(self, stuck)
        # Show what is left after the dialogs (the user may have pressed Skip).
        try:
            left = (len(find_misplaced_version_files(self.programs))
                    + len(find_programs_in_swap_state(self.programs)))
        except Exception:
            left = 0
        self._mark_clean_button(left)

    # -------- groups and cards --------

    def _group_of(self, name: str) -> str:
        g = self._custom_groups.get(name)
        if g not in {k for _t, k in GROUP_ORDER}:
            g = group_for_program(name)
        return g

    def _rebuild_buttons(self):
        """Only a new scan builds the window again; everything else changes
        the cards in place."""
        self._clear_body()

        items = sorted(self.programs.items(), key=lambda kv: kv[0].lower())
        grouped: dict[str, list[tuple[str, dict]]] = {k: [] for _t, k in GROUP_ORDER}
        for name, info in items:
            grouped[self._group_of(name)].append((name, info))
        self._group_items = grouped
        for _title, gkey in GROUP_ORDER:
            # Default: Main scripts open, everything else closed
            self._group_expanded.setdefault(gkey, gkey == "scripts")

        if not items:
            self._body_note("No programs to show.")
            return

        # The width is taken from every open group BEFORE the first card is
        # built, so the first group is not sized to its own names only.
        self._prog_btn_w = self._calc_prog_btn_px()

        # A header and an (empty) grid exist for EVERY group, shown or not, so
        # moving a program into a group that was empty only has to show it.
        for _title, gkey in GROUP_ORDER:
            header = QToolButton(self._inner)
            header.setObjectName("groupHeader")
            header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            header.setIconSize(QSize(14, 14))
            header.setCursor(Qt.PointingHandCursor)
            header.clicked.connect(lambda _=False, k=gkey: self._toggle_group(k))
            self._body.addWidget(header, 0, Qt.AlignLeft)
            self._group_toggle_btns[gkey] = header

            grid_w = QWidget(self._inner)
            grid = QGridLayout(grid_w)
            grid.setContentsMargins(14, 4, 4, 8)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(10)
            grid.setColumnStretch(GROUP_COLS, 1)
            self._body.addWidget(grid_w)
            self._group_grids[gkey] = grid_w
            self._group_layouts[gkey] = grid
            self._update_group_header(gkey)

            if self._group_expanded[gkey] and self._group_items[gkey]:
                self._build_group_content(gkey)
        self._body.addStretch(1)

        self._show_groups()
        self._fit_width()

    def _update_group_header(self, gkey: str):
        btn = self._group_toggle_btns.get(gkey)
        if btn is None:
            return
        title = next(t for t, k in GROUP_ORDER if k == gkey)
        btn.setIcon(drawn_icon("open" if self._group_expanded.get(gkey) else "closed"))
        btn.setText(f"{title}  ({len(self._group_items.get(gkey, []))})")

    def _show_groups(self):
        """Headers for the groups with programs, cards for the open ones."""
        for _title, gkey in GROUP_ORDER:
            has = bool(self._group_items.get(gkey))
            if gkey in self._group_toggle_btns:
                self._group_toggle_btns[gkey].setVisible(has)
                self._group_grids[gkey].setVisible(has and bool(self._group_expanded.get(gkey)))

    def _shown_group_keys(self) -> list:
        """Groups that are open right now — the only ones the sizes come from."""
        return [g for g, items in self._group_items.items()
                if items and self._group_expanded.get(g)]

    def _calc_prog_btn_px(self) -> int:
        """Width of the program buttons, in pixels.

        The longest name in the groups that are OPEN, whole — every card on
        screen gets the same button so they line up column to column, and a
        name hidden in a closed group does not stretch them.
        """
        fm = QFontMetrics(_ui_font(9))
        longest = 0
        shown = self._shown_group_keys()
        for gkey in (shown or list(self._group_items)):
            for _name, info in self._group_items.get(gkey, []):
                longest = max(longest, fm.horizontalAdvance(ui_label(info.get("label", ""))))
        # icon 24 + gap 6 + padding 2 x 8 + border 2 x 1, and a little air
        return max(120, longest + 24 + 6 + 16 + 2 + 10)

    def _doc_btn_px(self) -> int:
        """Width of one doc button ("ReadMe" / "Details"), measured once.

        Both columns of a card's small buttons get it as a minimum, so a card
        without a Details button keeps the exact geometry of one that has it.
        """
        if self._doc_btn_w is None:
            fm = QFontMetrics(_ui_font(8))
            text = max(fm.horizontalAdvance("ReadMe"), fm.horizontalAdvance("Details"))
            self._doc_btn_w = text + 2 * 3 + 2 + 10
        return self._doc_btn_w

    def _load_icon(self, icon_path) -> QIcon | None:
        """A program's icon, read once and kept for every later card."""
        if not icon_path:
            return None
        key = str(icon_path)
        if key not in self._icon_cache:
            icon = QIcon(key)
            self._icon_cache[key] = None if icon.isNull() else icon
        return self._icon_cache.get(key)

    def _fill_version_menu(self, menu: QMenu, name: str):
        """Fill the version list when it opens, from the versions known right now.

        Built with the card, the list kept whatever the scan had found and
        never showed a version the 10 s poll found later.
        """
        menu.clear()
        info = self.programs.get(name)
        if not info:
            return
        for v in info.get("archive_versions", []):
            act = menu.addAction(v["label"])
            act.triggered.connect(
                lambda _=False, p=v.get("exe_path"), d=info["program_dir"],
                py=v.get("py_path"), hi=v.get("has_internal", False):
                    self._launch_exe(p, d, py, hi))

    def _build_group_content(self, gkey: str):
        if not self._group_items.get(gkey) or gkey not in self._group_grids:
            return
        for name, info in self._group_items[gkey]:
            self._prog_cells[name] = self._build_card(gkey, name, info)
        self._group_built.add(gkey)
        self._regrid(gkey)

    def _regrid(self, gkey: str):
        """Place a group's existing cards in name order, two to a row."""
        grid_w = self._group_grids.get(gkey)
        lay = self._group_layouts.get(gkey)
        if grid_w is None or lay is None:
            return
        while lay.count():
            lay.takeAt(0)
        i = 0
        for name, _info in self._group_items.get(gkey, []):
            cell = self._prog_cells.get(name)
            if cell is None or not isValid(cell) or cell.parent() is not grid_w:
                continue
            lay.addWidget(cell, i // GROUP_COLS, i % GROUP_COLS,
                          Qt.AlignLeft | Qt.AlignTop)
            i += 1

    def _build_card(self, gkey: str, name: str, info: dict) -> QWidget:
        """One program's card (not yet placed — _regrid does that)."""
        cell = QWidget(self._group_grids[gkey])
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        btn = QPushButton(ui_label(info["label"]), cell)
        btn.setObjectName("prog")
        img = self._load_icon(info.get("icon_path"))
        if img is not None:
            btn.setIcon(img)
        btn.setIconSize(QSize(24, 24))
        btn.setProperty("update", name in self._update_available)
        btn.setFixedWidth(self._prog_btn_w)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        btn.clicked.connect(lambda _=False, n=name: self.launch(n))
        btn.setContextMenuPolicy(Qt.CustomContextMenu)
        btn.customContextMenuRequested.connect(
            lambda pos, n=name, b=btn: self._show_group_menu(b.mapToGlobal(pos), n))
        lay.addWidget(btn)
        self._prog_buttons[name] = btn

        sub = QGridLayout()
        sub.setContentsMargins(0, 0, 0, 0)
        sub.setHorizontalSpacing(2)
        sub.setVerticalSpacing(2)
        doc_w = self._doc_btn_px()
        sub.setColumnMinimumWidth(0, doc_w)
        sub.setColumnMinimumWidth(1, doc_w)
        sub.setRowMinimumHeight(0, 24)
        sub.setRowMinimumHeight(1, 24)

        has_readme = bool(info.get("readme_path"))
        has_full = bool(info.get("readme_full_path"))
        if has_readme:
            b = QPushButton("ReadMe", cell)
            b.setObjectName("doc")
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, n=name: self.open_readme(n))
            sub.addWidget(b, 0, 0, 1, 1 if has_full else 2)
        if has_full:
            b = QPushButton("Details", cell)
            b.setObjectName("doc")
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, n=name: self.open_readme_full(n))
            sub.addWidget(b, 0, 1 if has_readme else 0, 1, 1 if has_readme else 2)

        folder = QToolButton(cell)
        folder.setObjectName("small")
        folder.setIcon(drawn_icon("folder"))
        folder.setIconSize(QSize(16, 16))
        folder.setToolTip("Open the program folder")
        folder.setFixedHeight(24)
        folder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        folder.clicked.connect(lambda _=False, n=name: self.open_folder(n))
        sub.addWidget(folder, 1, 0)

        if info.get("archive_versions"):
            vb = QToolButton(cell)
            vb.setObjectName("small")
            vb.setIcon(drawn_icon("history"))
            vb.setIconSize(QSize(16, 16))
            vb.setToolTip("Start an older version")
            vb.setFixedHeight(24)
            vb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            menu = QMenu(vb)
            menu.aboutToShow.connect(lambda m=menu, n=name: self._fill_version_menu(m, n))
            vb.setMenu(menu)
            vb.setPopupMode(QToolButton.InstantPopup)
            sub.addWidget(vb, 1, 1)
        lay.addLayout(sub)
        return cell

    def _drop_card(self, name: str):
        cell = self._prog_cells.pop(name, None)
        self._prog_buttons.pop(name, None)
        if cell is not None and isValid(cell):
            for lay in self._group_layouts.values():
                lay.removeWidget(cell)
            cell.hide()
            cell.deleteLater()

    def _add_card(self, gkey: str, name: str) -> bool:
        """Build one card into a group's grid — only if that group's cards
        exist or it is open; a group never opened builds them all when opened."""
        info = self.programs.get(name)
        if gkey not in self._group_grids or info is None:
            return False
        if gkey not in self._group_built and not self._group_expanded.get(gkey):
            return False
        self._group_built.add(gkey)
        self._prog_cells[name] = self._build_card(gkey, name, info)
        return True

    def _replace_card(self, name: str):
        """Build one program's card again in its place (e.g. it gained a version list)."""
        cell = self._prog_cells.get(name)
        if cell is None or not isValid(cell):
            return
        gkey = self._group_of(name)
        self._drop_card(name)
        if self._add_card(gkey, name):
            self._regrid(gkey)
        self._fit_width()

    def _fit_width(self):
        """Keep the window wide enough for two of the widest cards side by side.

        The minimum width follows the cards, and a window that is too narrow
        is widened once. It is never made narrower: the size the user chose is
        theirs to keep.
        """
        self._body.activate()
        inner = self._inner.minimumSizeHint().width()
        bar = self.scroll.verticalScrollBar().sizeHint().width()
        m = self.centralWidget().layout().contentsMargins()
        need = inner + bar + 2 * self.scroll.frameWidth() + m.left() + m.right()
        need = max(need, 550, self.centralWidget().minimumSizeHint().width())
        self.setMinimumWidth(need)
        if self.width() < need:
            self.resize(need, max(self.height(), MIN_WINDOW_H))

    def _toggle_group(self, gkey: str):
        expanded = not self._group_expanded.get(gkey, False)
        self._group_expanded[gkey] = expanded

        # The buttons are as wide as the longest name in the OPEN groups, so a
        # group that brings a longer (or takes away the longest) name resizes
        # every card — in place, nothing is built again for it.
        new_w = self._calc_prog_btn_px()
        if new_w != self._prog_btn_w:
            self._apply_btn_width(new_w)
        if expanded and gkey not in self._group_built:
            self._build_group_content(gkey)       # lazy: first opening
        self._update_group_header(gkey)
        self._show_groups()
        self._fit_width()

    def _apply_btn_width(self, px: int):
        """Give every program button on screen the same new width, in place."""
        self._prog_btn_w = px
        for btn in self._prog_buttons.values():
            if isValid(btn):
                btn.setFixedWidth(px)

    def _set_update_style(self, name: str):
        """Amber or plain, on the one button — nothing else is redrawn."""
        btn = self._prog_buttons.get(name)
        if btn is None or not isValid(btn):
            return
        btn.setProperty("update", name in self._update_available)
        _repolish(btn)

    def _show_group_menu(self, global_pos, program_name: str):
        current_gkey = self._group_of(program_name)
        menu = QMenu(self)
        if program_name in self._update_available:
            act = menu.addAction("Acknowledge update")
            act.triggered.connect(lambda _=False: self._acknowledge_update(program_name))
            menu.addSeparator()
        head = menu.addAction("Move to group:")
        head.setEnabled(False)
        head.setFont(_ui_font(9, bold=True))
        menu.addSeparator()
        tick = drawn_icon("tick")
        for title, gkey in GROUP_ORDER:
            act = menu.addAction(title)
            if gkey == current_gkey:
                act.setIcon(tick)
            act.triggered.connect(
                lambda _=False, k=gkey: self._move_to_group(program_name, k))
        if program_name in self._custom_groups:
            menu.addSeparator()
            act = menu.addAction("Reset to default")
            act.triggered.connect(lambda _=False: self._move_to_group(program_name, None))
        menu.exec(global_pos)
        menu.deleteLater()

    def _move_to_group(self, program_name: str, gkey: str | None):
        old = self._group_of(program_name)
        if gkey is None:
            self._custom_groups.pop(program_name, None)
        else:
            self._custom_groups[program_name] = gkey
        self._config["custom_groups"] = self._custom_groups
        _save_config(self._config)
        new = self._group_of(program_name)
        info = self.programs.get(program_name)
        if new == old or info is None or not self._group_grids:
            return

        # Only the two groups involved change: the card leaves one grid and
        # joins the other. The rest of the window stays exactly as it is.
        self._group_items[old] = [(n, i) for n, i in self._group_items.get(old, [])
                                  if n != program_name]
        self._group_items[new] = sorted(self._group_items.get(new, []) + [(program_name, info)],
                                        key=lambda kv: kv[0].lower())
        self._drop_card(program_name)
        self._regrid(old)
        new_w = self._calc_prog_btn_px()
        if new_w != self._prog_btn_w:
            self._apply_btn_width(new_w)
        if self._add_card(new, program_name):
            self._regrid(new)
        self._update_group_header(old)
        self._update_group_header(new)
        self._show_groups()
        self._fit_width()

    def _rebuild_radiobuttons(self):
        for i, (label, path, _configurable) in enumerate(self._root_options):
            if i < len(self._rb_widgets):
                self._rb_widgets[i].setEnabled(self._root_enabled(label, path))

    # -------- version update polling --------

    def _schedule_version_poll(self):
        self.after(10000, self._poll_versions)

    def _poll_versions(self):
        if not self.programs or not self.current_root_path:
            self._schedule_version_poll()
            return
        root = self.current_root_path
        programs_snapshot = dict(self.programs)
        gen = self._scan_gen

        def check(name, info):
            current_ver = _exe_version(info["exe_path"])
            best_exe, archive_vers = self._scan_program_versions(root, name, info)
            return name, best_exe, archive_vers, _exe_version(best_exe) > current_ver

        def worker():
            from concurrent.futures import ThreadPoolExecutor
            updates: set[str] = set()
            # name → (best_exe_path, archive_versions)
            refreshed: dict[str, tuple] = {}
            # All programs at once, like the first scan: one after another, the
            # network round trips added up to seconds on every 10 s poll.
            with ThreadPoolExecutor(max_workers=16) as ex:
                futures = [ex.submit(check, n, i) for n, i in programs_snapshot.items()]
                for fut in futures:
                    try:
                        name, best_exe, archive_vers, newer = fut.result()
                    except Exception:
                        continue
                    if newer:
                        updates.add(name)
                    refreshed[name] = (best_exe, archive_vers)
            self.after(0, lambda: self._apply_version_updates(updates, refreshed, gen))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_program_versions(self, root: Path, name: str, info: dict) -> tuple:
        """Return (best_exe_path, archive_versions_list) for a program."""
        program_dir: Path = info.get("program_dir", root / name)

        # A) scratch layout: exes directly in program folder
        exes = [p for p in program_dir.glob("*.exe") if not TIMESTAMPED_EXE_RE.match(p.name)]
        if exes:
            exes.sort(key=_exe_version, reverse=True)
            best_exe = exes[0]
            archive_vers = _build_version_list(exes, program_dir)
            return best_exe, archive_vers

        # B) dist layout: ROOT\dist\<name>\vX.Y.Z\*.exe — the same places the
        # first scan looks in, so a build it found is not missed here.
        for dist_dir in _dist_candidates(root, name):
            vf = newest_version_folder(dist_dir)
            if not vf:
                continue
            exes_v = list(vf.glob("*.exe")) or list(vf.rglob("*.exe"))
            if exes_v:
                best_exe = pick_exe(exes_v, name, vf.name)
                archive_vers = _build_version_list([], program_dir)
                return best_exe, archive_vers
            break

        # fallback: keep existing
        return info["exe_path"], info.get("archive_versions", [])

    def _apply_version_updates(self, updates: set[str], refreshed: dict[str, tuple],
                               gen: int | None = None):
        if gen is not None and gen != self._scan_gen:
            # Another source was loaded while this poll ran — its answer is
            # about programs that are no longer on screen.
            self._schedule_version_poll()
            return
        # A card built without any older version has no version list button yet.
        gained_versions: set[str] = set()
        for name, (best_exe, archive_vers) in refreshed.items():
            if name in self.programs:
                if archive_vers and not self.programs[name].get("archive_versions"):
                    gained_versions.add(name)
                if name in updates:
                    # Newer version found — keep exe_path at current (launched) version
                    # so the next poll still sees a version difference. Store newer path
                    # separately so launch() can use it.
                    self._pending_updates[name] = best_exe
                else:
                    # No update: safe to advance exe_path to best known
                    self.programs[name]["exe_path"] = best_exe
                self.programs[name]["archive_versions"] = archive_vers
        # Filter out programs where the user has already acknowledged this version
        filtered = set()
        for name in updates:
            new_ver = _exe_version(self._pending_updates.get(name) or self.programs.get(name, {}).get("exe_path"))
            ack_ver = self._acknowledged.get(name)
            if ack_ver is None or ack_ver != _ver_str(new_ver):
                filtered.add(name)
        # Also retain programs already in _update_available (e.g. flagged at startup)
        # that are still unacknowledged — poll won't detect them since exe_path == best_exe
        for name in self._update_available:
            if name not in filtered:
                exe = self._pending_updates.get(name) or self.programs.get(name, {}).get("exe_path")
                ack_ver = self._acknowledged.get(name)
                if ack_ver is not None and _ver_str(_exe_version(exe)) != ack_ver:
                    filtered.add(name)
        flipped = filtered ^ self._update_available
        self._update_available = filtered
        # Only the buttons whose highlight changed are touched.
        for name in flipped:
            self._set_update_style(name)
        for name in gained_versions:
            self._replace_card(name)
        self._schedule_version_poll()

    def _acknowledge_update(self, program_name: str):
        """Mark the current update for program_name as seen — clears the highlight."""
        info = self.programs.get(program_name, {})
        # Advance exe_path to the newer version and forget the pending entry
        pending = self._pending_updates.pop(program_name, None)
        if pending and program_name in self.programs:
            self.programs[program_name]["exe_path"] = pending
            info = self.programs[program_name]
        ver = _exe_version(info.get("exe_path"))
        self._acknowledged[program_name] = _ver_str(ver)
        self._config["acknowledged_versions"] = self._acknowledged
        _save_config(self._config)
        if program_name in self._update_available:
            self._update_available.discard(program_name)
            self._set_update_style(program_name)

    # -------- launch --------

    def launch(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        # Use pending (newer) exe if available, otherwise current
        exe_path: Path = self._pending_updates.get(program_name, info["exe_path"])
        self._set_status(f"Starting: {program_name} ...")

        def worker():
            try:
                if not _launch_no_zone_check(exe_path):
                    os.startfile(str(exe_path))  # fallback
                self.after(0, self._set_status, f"Started: {program_name}")
                self.after(0, self._acknowledge_update, program_name)
            except Exception as e:
                self.after(0, messagebox.showerror, "Launch failed", f"{program_name}\n\n{e}")
                self.after(0, self._set_status, "Launch failed.")

        threading.Thread(target=worker, daemon=True).start()

    def _launch_exe(self, exe_path: Path | None, program_dir: Path,
                    py_path: Path | None = None, has_internal: bool = False):
        """Start an older version of a program.

        The archived .exe IS the whole old program: every build carries its own
        code inside the exe, while the _internal folder beside it holds only the
        shared Python runtime and the third-party packages, which every version
        can use. So the archived exe is what gets started. All it needs is an
        _internal beside it, which is why a version out of the archive is put
        into the program folder for the run. Its .py snapshot rides along, so
        the folder never shows an old exe with the newest sources next to it.
        """
        internal_dir = program_dir / "_internal"
        ref = exe_path if exe_path is not None else py_path
        if ref is None:
            messagebox.showerror("Cannot start", "This version has no files to start.")
            return
        label = ref.name
        snapshot_dir = ref.parent

        # Already has everything beside it: a version folder that brought its
        # own _internal, a one-file program that needs none, or an exe that is
        # in the program folder anyway. Nothing is moved for these — and the
        # last case is the important one, because moving the current exes out of
        # the way used to move away the very exe that was about to be started.
        if exe_path is not None and (has_internal
                                     or not internal_dir.exists()
                                     or exe_path.parent == program_dir):
            def worker():
                try:
                    if not _launch_no_zone_check(exe_path):
                        subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
                    self.after(0, self._set_status, f"Started: {label}")
                except Exception as e:
                    self.after(0, messagebox.showerror, "Launch failed", f"{label}\n\n{e}")
                    self.after(0, self._set_status, "Launch failed.")

            self._set_status(f"Starting: {label} ...")
            threading.Thread(target=worker, daemon=True).start()
            return

        # From here on the version comes out of the archive and has to borrow
        # the program folder's _internal, so the folder is swapped for the run.
        python_exe = None
        if exe_path is None:
            # Sources only. Running them needs Python, and they have to be put
            # in the program folder too: the programs look for their icon,
            # settings and picture folders next to their own file.
            python_exe = _find_python()
            if python_exe is None:
                messagebox.showerror(
                    "Python needed",
                    f"{label}\n\n"
                    "Only the source files of this version were kept, and there "
                    "is no Python on this computer to run them with.",
                )
                return

        temp_dir = program_dir / "archive" / "_temp_latest"
        current_files = [p for p in program_dir.glob("*.exe")
                         if not TIMESTAMPED_EXE_RE.match(p.name)]
        current_files += list(program_dir.glob("*.py"))

        # The exe, its main script, and the helper modules beside it (plain
        # names). Any other versioned script in the folder — a " (2)" duplicate,
        # a leftover from another version — is left out of the way.
        snapshot_files = [f for f in (exe_path, py_path) if f is not None]
        snapshot_files += sorted(f for f in snapshot_dir.glob("*.py")
                                 if f != py_path and not ARCHIVE_PY_RE.match(f.name))

        staged_start = program_dir / (exe_path or py_path).name
        cmd = ([str(staged_start)] if exe_path is not None
               else [str(python_exe), str(staged_start)])

        def worker():
            staged: list[Path] = []
            did_swap = False
            try:
                if temp_dir.exists():
                    self.after(0, messagebox.showerror, "Launch blocked",
                               f"A previous old-version launch of this program did not clean up.\n\n"
                               f"Use the Clean button or restart the launcher to restore it first.")
                    self.after(0, self._set_status, "Ready.")
                    return
                temp_dir.mkdir(parents=True, exist_ok=True)
                # Marked before the first move: whatever lands in the temp
                # folder has to be put back even if a later move fails.
                did_swap = True
                for f in current_files:
                    shutil.move(str(f), str(temp_dir / f.name))
                for f in snapshot_files:
                    dst = program_dir / f.name
                    shutil.copy2(str(f), str(dst))
                    staged.append(dst)
                env = dict(os.environ)
                env["PYTHONDONTWRITEBYTECODE"] = "1"
                self.after(0, self._set_status, f"Running: {label}")
                if python_exe is None:
                    proc = subprocess.Popen(cmd, cwd=str(program_dir), env=env)
                    proc.wait()
                else:
                    # pythonw shows nothing when old sources fail to start (a
                    # helper file that was never archived, an import that needs
                    # a package this computer lacks), so what they printed is
                    # kept and shown instead of silence.
                    OLD_RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
                    with OLD_RUN_LOG.open("w", encoding="utf-8", errors="replace") as errf:
                        proc = subprocess.Popen(cmd, cwd=str(program_dir), env=env,
                                                stdout=errf, stderr=errf)
                        proc.wait()
                    if proc.returncode:
                        try:
                            tail = "\n".join(
                                OLD_RUN_LOG.read_text(encoding="utf-8", errors="replace")
                                .strip().splitlines()[-12:])
                        except Exception:
                            tail = ""
                        detail = (":\n\n" + tail) if tail else "."
                        self.after(0, messagebox.showerror, "Old version stopped",
                                   f"{label}\n\nIt ended with an error" + detail)
            except Exception as e:
                self.after(0, messagebox.showerror, "Launch failed", f"{label}\n\n{e}")
                self.after(0, self._set_status, "Launch failed.")
            finally:
                for f in staged:
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass
                if did_swap and temp_dir.exists():
                    try:
                        for f in list(temp_dir.iterdir()):
                            shutil.move(str(f), str(program_dir / f.name))
                        temp_dir.rmdir()
                    except Exception:
                        pass
                self.after(0, self._set_status, "Ready.")

        self._set_status(f"Starting: {label} ...")
        threading.Thread(target=worker, daemon=True).start()

    def open_folder(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        exe_path: Path = info.get("exe_path")
        if not exe_path:
            messagebox.showerror("Not found", f"No exe path for: {program_name}")
            return

        folder = exe_path.parent
        try:
            os.startfile(str(folder))
        except Exception as e:
            messagebox.showerror("Open folder failed", f"{program_name}\n\n{e}")

    def open_notes(self):
        sel = self._selected_root()
        if not sel:
            messagebox.showerror("Notes", "Please select a data source first.")
            return
        if sel[0] == "Lab - Scratch":
            notes_path = NOTES_LAB
        else:
            notes_path = NOTES_OFFICE

        if not notes_path.exists():
            messagebox.showinfo("Notes", f"Notes file not found:\n{notes_path}")
            return
        try:
            os.startfile(str(notes_path))
        except Exception as e:
            messagebox.showerror("Notes", f"Could not open notes:\n{e}")

    def open_set_paths(self):
        key_map = {
            "Office - Scratch": (
                "office_scratch",
                "Select the path to the 'Software' folder on Scratch folder.\n"
                "Example: Z:\\Software"
            ),
            "Office - Sharepoint": (
                "office_sharepoint",
                "Select the path to the 'QoL' folder inside General folder on Sharepoint.\n"
                "Example: C:\\...\\Documents - L3-HAPLS\\General\\QoL"
            ),
        }

        # The user picks which path to set
        choices = list(key_map.keys())
        dlg = QDialog(self)
        dlg.setWindowTitle("Set paths")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        v.addWidget(QLabel("Which path do you want to set for your personal computer? "
                           "Do not set the path for PCs in the lab.", dlg))
        v.addSpacing(6)
        group = QButtonGroup(dlg)
        for i, c in enumerate(choices):
            current = self._config.get(key_map[c][0], "(not set)")
            rb = QRadioButton(f"{c}  —  current: {current}", dlg)
            rb.setChecked(i == 0)
            group.addButton(rb, i)
            v.addWidget(rb)
            # What to pick is said right here, instead of in a separate message
            # box that popped up before the folder picker.
            hint = QLabel(key_map[c][1], dlg)
            hint.setObjectName("hint")
            hint.setContentsMargins(24, 0, 0, 6)
            v.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Set selected path", dlg)
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        row.addStretch(1)
        v.addLayout(row)
        if dlg.exec() != QDialog.Accepted:
            return

        label = choices[max(0, group.checkedId())]
        cfg_key, _description = key_map[label]
        current = self._config.get(cfg_key, "")
        chosen = QFileDialog.getExistingDirectory(
            self, f"Select folder for {label}", current or str(Path.home()))
        if not chosen:
            return

        self._config[cfg_key] = chosen
        _save_config(self._config)  # the .json is created now, on the first save

        self._root_options = _apply_config_to_root_options(self._config)
        self._rebuild_radiobuttons()

        # The new path is used straight away: its option is picked and
        # loaded, instead of leaving the window empty until it is clicked.
        idx = next(i for i, (lbl, _p, _c) in enumerate(self._root_options) if lbl == label)
        if self._rb_widgets[idx].isEnabled():
            self._set_root_idx(idx)
            self.refresh()
        elif self._root_idx == idx:
            self._set_root_idx(-1)
            self._set_idle_state()

    def open_readme(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        readme_path = info.get("readme_path")
        if not readme_path:
            messagebox.showinfo("ReadMe", f"No ReadMe found for: {program_name}")
            return

        try:
            os.startfile(str(readme_path))
        except Exception as e:
            messagebox.showerror("ReadMe open failed", f"{program_name}\n\n{e}")

    def open_readme_full(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        path = info.get("readme_full_path")
        if not path:
            messagebox.showinfo("Details", f"No detailed ReadMe found for: {program_name}")
            return

        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("Details open failed", f"{program_name}\n\n{e}")


def main() -> int:
    ico = _icon_file()
    app_id = _icon_app_id(APP_ID_PREFIX, str(ico) if ico else None) if os.name == "nt" else None
    if app_id:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass

    app = QApplication(sys.argv)
    apply_app_style(app)
    if ico is not None:
        app.setWindowIcon(QIcon(str(ico)))
    win = Launcher()
    win.show()
    _nudge_taskbar_icon(int(win.winId()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
