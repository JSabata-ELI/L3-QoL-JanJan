# l.py
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json
from PIL import Image, ImageTk

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
    """
    cfg = Path(os.environ.get("APPDATA", "~")) / "DevTools" / "config.json"
    try:
        root = json.loads(cfg.read_text(encoding="utf-8")).get("dist_root")
    except Exception:
        return None
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


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
# enough for two of the widest cards instead (see _fit_two_columns).
GROUP_COLS = 2
MIN_WINDOW_H = 430
# The cards get less than the canvas is wide: the grid is packed with padx=8
# inside a frame packed with padx=6, on both sides. Measuring this live reads
# the previous size while a resize is still on its way and then the second
# column lands past the right edge, so it is taken from the two pack() calls in
# _rebuild_buttons_inner — change it there and change it here.
GRID_PAD_PX = 2 * (8 + 6)

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

def _find_versioned_py(version_dir: Path, exe_path: Path) -> Path | None:
    """Najde hlavní .py soubor pro danou verzi (stejný název jako exe, nebo první nalezený s verzí)."""
    py_stem = exe_path.stem  # e.g. "Image Tools v1.2.1__20260511_125840" or "Image Tools v2.5.4"
    candidate = version_dir / (py_stem + ".py")
    if candidate.exists():
        return candidate
    # Fallback: first .py file in the folder with version in name (timestamped or normalized)
    for f in version_dir.glob("*.py"):
        exe_like = f.name[:-3] + ".exe"
        if ARCHIVE_EXE_RE.match(exe_like) or ARCHIVE_EXE_PLAIN_RE.match(exe_like):
            return f
    return None


def scan_archive_versions(program_dir: Path) -> list[dict]:
    """Vrátí seznam archivních verzí seřazených od nejnovější.
    Podporuje novou strukturu archive/vX.Y.Z/*.exe (s timestampou i normalizovanou
    bez timestampy) i starou plochou archive/*.exe."""
    archive_dir = program_dir / "archive"
    if not archive_dir.exists():
        return []

    entries = []

    # Nová struktura: archive/vX.Y.Z/*.exe — jeden záznam na složku verze.
    for version_subdir in archive_dir.iterdir():
        if not version_subdir.is_dir():
            continue
        folder_ver = parse_version(version_subdir.name)  # (maj, min, patch) | None
        # Prefer the canonical name over " (2)" dedup copies.
        exes = sorted(version_subdir.glob("*.exe"),
                      key=lambda p: (" (" in p.stem, p.name.lower()))
        for exe in exes:
            if not (ARCHIVE_EXE_RE.match(exe.name) or ARCHIVE_EXE_PLAIN_RE.match(exe.name)):
                continue
            entries.append({
                "exe_path": exe,
                "py_path": _find_versioned_py(version_subdir, exe),
                "label": _archive_exe_label(exe, folder_ver),
                "_ver": _archive_exe_version(exe, folder_ver),
            })
            break  # one exe per version folder

    # Stará flat struktura: archive/*.exe (zpětná kompatibilita)
    for exe in archive_dir.glob("*.exe"):
        if ARCHIVE_EXE_RE.match(exe.name):
            entries.append({
                "exe_path": exe,
                "py_path": None,
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


def find_readme_or_none(program_dir: Path, program_name: str) -> Path | None:
    """
    Robust ReadMe match in program root folder.
    Accepts:
      ReadMe_<ProgramName>
    but ignores:
      - case
      - underscores/spaces/hyphens/dots
      - file extension (.txt etc.) by matching stem
    """
    target = _norm("readme_" + program_name)

    try:
        for f in program_dir.iterdir():
            if not f.is_file():
                continue
            if _norm(f.stem) == target or _norm(f.name) == target:
                return f
    except Exception:
        return None

    return None


def find_readme_full_or_none(program_dir: Path, program_name: str) -> Path | None:
    """
    Long/detailed companion document, shown as the extra "Details" button.
    Accepts (case / separators / extension ignored):
      ReadMe_<ProgramName>_Full
      ReadMe_<ProgramName>_Details
      Manual_<ProgramName>
    """
    targets = {
        _norm("readme_" + program_name + "_full"),
        _norm("readme_" + program_name + "_details"),
        _norm("manual_" + program_name),
    }

    try:
        for f in program_dir.iterdir():
            if not f.is_file():
                continue
            if _norm(f.stem) in targets or _norm(f.name) in targets:
                return f
    except Exception:
        return None

    return None


# ---------------- GROUPING CONFIG ----------------
# Global rules (same for ALL roots).
# Everything else (not listed) becomes "External".

SCRIPTS = {
    # folder names of programs that should be in "Scripts"
    "Image Tools",
    "Screenshots",
    "Time Converter",
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

IN_PROGRESS = {
    "Calibrations"
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
    ("Scripts", "scripts"),
    ("Parts", "parts"),
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
        result.append({"exe_path": p, "label": label})

    # Archivní verze
    result.extend(scan_archive_versions(program_dir))

    return result

def _scan_one_program(program_dir: Path, root: Path) -> "tuple[str, dict] | None":
    """Scan a single program directory. Returns (name, info) or None."""
    program_name = program_dir.name
    readme = find_readme_or_none(program_dir, program_name)
    readme_full = find_readme_full_or_none(program_dir, program_name)

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
    _dist_candidates = [root / "dist" / program_name,
                        root.parent / "dist" / program_name]
    _dt_dist = _devtools_dist_root()
    if _dt_dist is not None:
        _dist_candidates.append(_dt_dist / program_name)
    for dist_dir in _dist_candidates:
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


# ---------------- UI HELPERS ----------------
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # mouse wheel only when needed (we also guard in handler)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scroll_enabled()

    def _on_canvas_configure(self, event):
        # Šířku inner okna nastavujeme jen pokud je obsah užší než canvas
        bbox = self.canvas.bbox("all")
        content_w = (bbox[2] - bbox[0]) if bbox else 0
        if content_w < event.width:
            self.canvas.itemconfigure(self.window, width=event.width)
        self._update_scroll_enabled()

    def _content_overflows(self) -> bool:
        bbox = self.canvas.bbox("all")
        if not bbox:
            return False
        content_h = bbox[3] - bbox[1]
        canvas_h = self.canvas.winfo_height()
        return content_h > canvas_h + 2

    def _update_scroll_enabled(self):
        overflow = self._content_overflows()

        # hide/show scrollbar
        if overflow:
            if not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y")
        else:
            if self.vsb.winfo_ismapped():
                self.vsb.pack_forget()

        # if no overflow, force top
        if not overflow:
            self.canvas.yview_moveto(0)

    def _on_mousewheel(self, event):
        if not self._content_overflows():
            return  # LOCK scroll when everything fits
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

def find_misplaced_timestamped_exes(programs: dict[str, dict]) -> list[tuple[Path, Path]]:
    """
    Projde hlavní složky všech programů a vrátí seznam
    (exe_path, cílová_archive_cesta) pro každý exe s timestampou.
    """
    misplaced = []
    for name, info in programs.items():
        program_dir: Path = info.get("program_dir")
        if not program_dir:
            continue
        for p in program_dir.glob("*.exe"):
            if TIMESTAMPED_EXE_RE.match(p.name):
                archive_dir = program_dir / "archive"
                misplaced.append((p, archive_dir / p.name))
    return misplaced

def prompt_move_misplaced(parent: tk.Tk, misplaced: list[tuple[Path, Path]]):
    """Zobrazí souhrnnou hlášku a nabídne přesun."""
    lines = "\n".join(
        f"  {src.parent.name}\\{src.name}"
        for src, _ in misplaced
    )
    msg = (
        f"The following versioned files were found outside the archive folder:\n\n"
        f"{lines}\n\n"
        f"Move them to their archive folders?"
    )

    confirmed = tk.BooleanVar(value=False)

    dlg = tk.Toplevel(parent)
    dlg.title("Misplaced files found")
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.focus_set()

    ttk.Label(dlg, text=msg, justify="left", padding=(16, 12)).pack()

    btn_row = ttk.Frame(dlg)
    btn_row.pack(pady=(0, 12))

    def on_yes():
        confirmed.set(True)
        dlg.destroy()

    def on_no():
        dlg.destroy()

    ttk.Button(btn_row, text="Move", width=10, command=on_yes).pack(side="left", padx=8)
    ttk.Button(btn_row, text="Skip", width=10, command=on_no).pack(side="left", padx=8)

    parent.wait_window(dlg)

    if not confirmed.get():
        return

    errors = []
    for src, dst in misplaced:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        except Exception as e:
            errors.append(f"{src.name}: {e}")
    if errors:
        messagebox.showerror(
            "Move failed",
            "Some files could not be moved:\n\n" + "\n".join(errors),
            parent=parent,
        )


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


def prompt_restore_swap_state(parent: tk.Tk, stuck: list[tuple[str, Path, Path]]):
    """Dialog that offers to restore programs stuck in an incomplete old-version launch."""
    lines = "\n".join(f"  {name}" for name, _, _ in stuck)
    msg = (
        f"The following programs have an incomplete old-version launch.\n"
        f"Their latest files are stored in a temporary folder:\n\n"
        f"{lines}\n\n"
        f"Restore the latest versions now?"
    )

    confirmed = tk.BooleanVar(value=False)

    dlg = tk.Toplevel(parent)
    dlg.title("Incomplete old-version launch detected")
    dlg.resizable(False, False)
    dlg.grab_set()
    dlg.focus_set()

    ttk.Label(dlg, text=msg, justify="left", padding=(16, 12)).pack()

    btn_row = ttk.Frame(dlg)
    btn_row.pack(pady=(0, 12))

    def on_yes():
        confirmed.set(True)
        dlg.destroy()

    def on_no():
        dlg.destroy()

    ttk.Button(btn_row, text="Restore", width=10, command=on_yes).pack(side="left", padx=8)
    ttk.Button(btn_row, text="Skip", width=10, command=on_no).pack(side="left", padx=8)

    parent.wait_window(dlg)

    if not confirmed.get():
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


def set_app_icon(win, ico_path, app_id=None):
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


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self._scan_gen = 0
        self._config = _load_config()
        self._root_options = _apply_config_to_root_options(self._config)
        try:
            _hn = socket.gethostname().upper().strip()
        except Exception:
            _hn = ""
        self._is_lab_machine = any(kw in _hn for kw in ("OPR", "VIS"))
        self.style = ttk.Style(self)

        # Modern Windows theme (nejlíp vypadá na Win10/11)
        for t in ("vista", "xpnative"):
            if t in self.style.theme_names():
                self.style.theme_use(t)
                break

        # Jednotná typografie
        self.option_add("*Font", "SegoeUI 10")
        # --- two columns, window sized to the cards ---
        self._group_cols = GROUP_COLS
        # How wide the widest card on screen really is. Nothing is guessed: a
        # guess goes stale the moment a card gains a button, and a stale guess is
        # what let two columns be drawn into the space for one.
        self._measured_cell_px = None
        # A card is not the same width in every group (the ReadMe+Details pair
        # makes it narrower than the archive dropdown does), so one measured
        # width re-taken per group used to bounce between two values and each
        # bounce booked another rebuild — the window flickered between one and
        # two columns for ever. Now the width is taken from the widest card on
        # screen and may only GROW inside one relayout chain, and the chain is
        # capped, so it always settles.
        self._relayouts = 0
        self._btn_chars_used: int | None = None
        self._chrome_px: int | None = None   # window width that is not card space
        self._doc_btn_px = None

        # Jemnější buttony
        # Program button style (hover effect)
        self.style.configure(
            "Prog.TButton",
            padding=(8, 6),
            font=("Segoe UI", 9),
            anchor="w"
        )

        # Hover + pressed states
        self.style.map(
            "Prog.TButton",
            background=[
                ("active", "#e6f2ff"),      # hover
                ("pressed", "#cce4ff")
            ]
        )

        # Update-available variant: amber background
        self.style.configure(
            "Update.Prog.TButton",
            padding=(8, 6),
            font=("Segoe UI", 9),
            anchor="w",
            background="#ffa500",
            foreground="#000000",
        )
        self.style.map(
            "Update.Prog.TButton",
            background=[
                ("active", "#ffb733"),
                ("pressed", "#e09000"),
            ]
        )

        self.style.configure("Info.TButton", padding=(5, 2), font=("Segoe UI", 8))
        # The two doc buttons sit side by side, so they get tighter padding than
        # the other small buttons. The font stays at 8 — legibility first; the
        # width is bought back from the program button instead.
        self.style.configure("Doc.TButton", padding=(3, 2), font=("Segoe UI", 8))
        self.style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabel", font=("Segoe UI", 10))
        self.title("Launcher")
        if getattr(sys, "frozen", False):
            _base = Path(sys.executable).resolve().parent
        else:
            _base = Path(__file__).resolve().parent
        set_app_icon(self, str(_base / "icon.ico"), "ELI.Launcher")
        self.geometry("600x510")
        self.minsize(550, 430)

        self.programs: dict[str, dict] = {}
        self.current_root_label: str | None = None
        self.current_root_path: Path | None = None
        self._icon_cache: dict[str, tk.PhotoImage] = {}
        self._known_versions: dict[str, tuple] = {}   # program_name → (major, minor, patch)
        self._update_available: set[str] = set()       # programs with newer version on disk
        self._pending_updates: dict[str, Path] = {}    # name → newer exe path (not yet acknowledged)
        self._prog_buttons: dict[str, ttk.Button] = {} # current button widget per program
        # Acknowledged versions: {program_name: version_str} — persisted in config
        # Once a user launches or clicks ✓, the version is acknowledged and highlight clears.
        self._acknowledged: dict[str, str] = self._config.get("acknowledged_versions", {})
        # Custom group overrides: {program_name: gkey} — persisted in config
        self._custom_groups: dict[str, str] = self._config.get("custom_groups", {})

        # no default selection
        self.root_choice = tk.IntVar(value=-1)  # bude obsahovat label z ROOT_OPTIONS

        self._build_ui()
        self._set_idle_state()
        self._auto_select_root()
        self._schedule_version_poll()

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
            self.root_choice.set(idx)
            self.refresh()

    def _calc_group_cols(self) -> int:
        """Always two columns of program buttons.

        The count used to follow the window width, which is what let the layout
        flip between one and two columns while the cards were being measured.
        The window is now sized to the cards instead (`_fit_two_columns`), so
        this is a constant and a resize never re-flows the grid.
        """
        return GROUP_COLS

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # Root selector (radiobuttons)
        box = ttk.LabelFrame(root, text="Data source")
        box.pack(fill="x")

        # Řádek 1: radiobuttons
        rb_row = ttk.Frame(box)
        rb_row.pack(fill="x", padx=4, pady=(6, 2))

        self._rb_widgets = []
        for i, (label, path, configurable) in enumerate(self._root_options):
            rb = ttk.Radiobutton(
                rb_row,
                text=label,
                variable=self.root_choice,
                value=i,
                command=self.refresh,
                state="normal" if (path is not None and not (self._is_lab_machine and label.lower().startswith("office"))) else "disabled",
            )
            rb.pack(side="left", padx=6)
            self._rb_widgets.append(rb)

        # Řádek 2: tlačítka
        btn_row = ttk.Frame(box)
        btn_row.pack(fill="x", padx=4, pady=(0, 6))

        ttk.Button(btn_row, text="📋 Notes", command=self.open_notes).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="⚙ Set paths", command=self.open_set_paths).pack(side="left")
        ttk.Button(btn_row, text="🧹 Clean", command=self._run_cleanup).pack(side="left", padx=(6, 0))
        
        # Buttons grid
        self.sf = ScrollableFrame(root)
        self.sf.pack(fill="both", expand=True, pady=(10, 0))
        self.hsb = ttk.Scrollbar(root, orient="horizontal", command=self.sf.canvas.xview)
        self.hsb.pack(fill="x")
        self.sf.canvas.configure(xscrollcommand=self.hsb.set)
        self.bind("<Configure>", lambda _e: self.after_idle(self._refresh_cols_and_rebuild))

        # Status
        self.status = ttk.Label(root, text="", anchor="w")
        self.status.pack(fill="x", pady=(8, 0))

    def _refresh_cols_and_rebuild(self):
        if getattr(self, "_in_rebuild", False):
            return
        new_cols = self._calc_group_cols()
        if new_cols != getattr(self, "_group_cols", 2):
            self._group_cols = new_cols
            self._rebuild_buttons()

    def _set_idle_state(self):
        for w in self.sf.inner.winfo_children():
            w.destroy()
        ttk.Label(self.sf.inner, text="Select a data source above.").pack(anchor="w", padx=8, pady=8)
        self.status.configure(text="No source selected.")

    def _selected_root(self) -> tuple[str, Path] | None:
        idx = self.root_choice.get()
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

        self._scan_gen += 1          # ← každý nový scan dostane nové číslo
        gen = self._scan_gen

        self.status.configure(text=f"Scanning: {selected_root} ...")

        def worker():
            try:
                progs = scan_programs(selected_root)
                # ← aplikuj pouze pokud jsme stále ve stejném scanu
                self.after(0, lambda: self._apply_programs(progs, root_label, selected_root, gen))
            except Exception as e:
                self.after(0, lambda: self._apply_programs({}, root_label, selected_root, gen))
                self.after(0, messagebox.showerror, "Scan failed", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_programs(self, progs: dict[str, dict], root_label: str, selected_root: Path, gen: int):
        if gen != self._scan_gen:
            return
        self.programs = progs
        self.current_root_label = root_label
        self.current_root_path = selected_root
        self._update_available.clear()
        self._pending_updates.clear()
        # A new scan can bring different names and different cards, so the card
        # width is measured again from scratch instead of keeping the widest one
        # some earlier source happened to have.
        self._measured_cell_px = None
        self._btn_chars_used = None
        self._relayouts = 0
        # Startup check: flag programs whose on-disk version differs from last acknowledged
        for name, info in self.programs.items():
            ack_ver = self._acknowledged.get(name)
            if ack_ver is None:
                continue  # never acknowledged → no baseline to compare against
            if _ver_str(_exe_version(info.get("exe_path"))) != ack_ver:
                self._update_available.add(name)
        self._rebuild_buttons()
        self.status.configure(text=f"Source: {selected_root} | Found: {len(self.programs)} programs.")

        misplaced = find_misplaced_timestamped_exes(self.programs)
        if misplaced:
            self.after(200, lambda: prompt_move_misplaced(self, misplaced))

        stuck = find_programs_in_swap_state(self.programs)
        if stuck:
            self.after(400, lambda: prompt_restore_swap_state(self, stuck))

    def _run_cleanup(self):
        if not self.programs:
            messagebox.showinfo("Clean", "No programs loaded. Select a data source first.")
            return
        misplaced = find_misplaced_timestamped_exes(self.programs)
        stuck = find_programs_in_swap_state(self.programs)
        if not misplaced and not stuck:
            messagebox.showinfo("Clean", "No misplaced files found.")
            return
        if misplaced:
            prompt_move_misplaced(self, misplaced)
        if stuck:
            prompt_restore_swap_state(self, stuck)

    def _rebuild_buttons(self):
        # Measuring a card runs Tk's pending idle work, and one of those idle
        # jobs is the column check — which would call this again from inside
        # itself, on widgets that are being replaced. One rebuild at a time.
        if getattr(self, "_in_rebuild", False):
            return
        self._in_rebuild = True
        try:
            self._rebuild_buttons_inner()
        finally:
            self._in_rebuild = False

    def _rebuild_buttons_inner(self):
        # One column count for the whole rebuild: asking again per group is what
        # let two groups be drawn to two different layouts in the same pass.
        self._group_cols = self._calc_group_cols()
        for w in self.sf.inner.winfo_children():
            w.destroy()

        items = sorted(self.programs.items(), key=lambda kv: kv[0].lower())

        grouped: dict[str, list[tuple[str, dict]]] = {
            "scripts": [],
            "parts": [],
            "external": [],
            "in_progress": [],
            "not_working_correctly": [],
            "personal": [],
        }
        for name, info in items:
            g = self._custom_groups.get(name) or group_for_program(name)
            grouped[g].append((name, info))

        # Pre-load all icons into cache before building any widgets.
        # This avoids the need to build hidden widgets just to initialise PhotoImage.
        for _, _info in items:
            _ip = _info.get("icon_path")
            if _ip and str(_ip) not in self._icon_cache:
                _key = str(_ip)
                try:
                    _pil = Image.open(_key)
                    if getattr(_pil, "format", "") == "ICO":
                        _sizes = _pil.info.get("sizes", [])
                        if _sizes:
                            _cands = [s for s in _sizes if s[0] <= 32]
                            _target = max(_cands) if _cands else min(_sizes, key=lambda s: s[0])
                            _pil = Image.open(_key).resize(_target, Image.LANCZOS)
                    _pil = _pil.convert("RGBA").resize((24, 24), Image.LANCZOS)
                    self._icon_cache[_key] = ImageTk.PhotoImage(_pil)
                except Exception:
                    self._icon_cache[_key] = None

        # Persistent collapse state: gkey → bool (True = expanded)
        if not hasattr(self, "_group_expanded"):
            self._group_expanded: dict[str, bool] = {}

        # Store toggle button refs so _toggle_group can update arrows without rebuild
        self._group_toggle_btns: dict[str, ttk.Button] = {}
        self._group_frames: dict[str, ttk.Frame] = {}
        self._group_grids: dict[str, ttk.Frame] = {}    # inner grid widget per group
        self._group_headers: dict[str, ttk.Frame] = {}  # header widget per group, for pack(after=)
        self._group_items: dict[str, list] = {}         # items per group for lazy build

        # Fill the item lists for EVERY group first. The button width is taken
        # from them, and the first group used to be built while the later groups
        # were still missing — so it got sized to its own longest name only.
        for _title, _gkey in GROUP_ORDER:
            if grouped.get(_gkey):
                self._group_items[_gkey] = grouped[_gkey]
                # Default: Scripts expanded, everything else collapsed
                if _gkey not in self._group_expanded:
                    self._group_expanded[_gkey] = (_gkey == "scripts")

        any_group_shown = False
        for title, gkey in GROUP_ORDER:
            group_items = grouped.get(gkey, [])
            if not group_items:
                continue

            any_group_shown = True

            expanded = self._group_expanded[gkey]
            arrow = "▼" if expanded else "▶"

            header = ttk.Frame(self.sf.inner)
            header.pack(fill="x", padx=6, pady=(6, 0))
            self._group_headers[gkey] = header

            grid_frame = ttk.Frame(self.sf.inner)
            grid = ttk.Frame(grid_frame)
            grid.pack(fill="x", padx=8, pady=6)
            self._group_frames[gkey] = grid_frame
            self._group_grids[gkey] = grid
            self._group_items[gkey] = group_items

            toggle_btn = ttk.Button(
                header,
                text=f"{arrow}  {title}  ({len(group_items)})",
                style="Info.TButton",
                command=lambda k=gkey: self._toggle_group(k),
            )
            toggle_btn.pack(side="left")
            self._group_toggle_btns[gkey] = toggle_btn

            if expanded:
                grid_frame.pack(fill="x", padx=6, pady=(2, 0))
                self._build_group_content(gkey)

        if not any_group_shown:
            ttk.Label(self.sf.inner, text="No programs to show.").pack(anchor="w", padx=8, pady=8)
            return

        self._sync_cell_width()

    def _shown_group_keys(self) -> list:
        """Groups that are open right now — the only ones the sizes come from."""
        return [g for g, items in self._group_items.items()
                if items and self._group_expanded.get(g)]

    def _prog_btn_chars(self) -> int:
        """Width of the program buttons, in characters.

        Sized to the longest name in the groups that are OPEN, so every card on
        screen gets the same button and they line up column to column. A name
        hidden in a collapsed group does not stretch them: at a fixed 18 the
        widest label ("Internal Builder") left 28 px of empty button, and that
        slack is exactly what the second doc button needs.
        """
        longest = 0
        shown = self._shown_group_keys()
        for gkey in (shown or list(self._group_items)):
            for _name, info in self._group_items.get(gkey, []):
                longest = max(longest, len(ui_label(info.get("label", ""))))
        return max(10, min(longest, 22))

    def _sync_cell_width(self, allow_shrink: bool = False):
        """Re-flow the grid if the widest card on screen does not fit the layout.

        The width may only grow while a relayout chain is running, so it cannot
        bounce between the two card sizes; `allow_shrink` is for the one case
        where a card really did get narrower — a group was just closed — and it
        is only ever asked for by a click, never from inside a chain.
        """
        widest = 0
        for gkey in self._shown_group_keys():
            grid = self._group_grids.get(gkey)
            if grid is None:
                continue
            try:
                grid.update_idletasks()
                for cell in grid.winfo_children():
                    widest = max(widest, cell.winfo_reqwidth() + 12)
            except Exception:
                return          # widgets went away under us — nothing to size
        if widest <= 50:
            return

        if allow_shrink:
            changed = widest != self._measured_cell_px
        else:
            changed = self._measured_cell_px is None or widest > self._measured_cell_px
        if changed:
            self._measured_cell_px = widest

        self._fit_two_columns()

        if self._calc_group_cols() != self._group_cols and self._relayouts < 3:
            self._relayouts += 1
            self.after(0, self._rebuild_buttons)
        else:
            self._relayouts = 0

    def _fit_two_columns(self):
        """Keep the window wide enough for two of the widest cards side by side.

        Opening a group can bring wider cards, and two of those no longer fit in
        the window the user left the last group in — the second column would be
        drawn past the right edge. So the minimum width follows the cards, and a
        window that is already too narrow is widened once. It is never made
        narrower again: the size the user chose is theirs to keep.
        """
        cell = self._measured_cell_px
        if not cell:
            return          # no real card measured yet — nothing to size to
        try:
            win_w, win_h = self.winfo_width(), self.winfo_height()
            canvas_w = self.sf.canvas.winfo_width()
            # The scrollbar takes its share of the width as soon as the list is
            # long enough, so count it even while it is hidden. A scrollbar that
            # was never mapped asks for far more than it takes (69 px measured),
            # hence the cap — otherwise the window jumps wider than it needs.
            vsb_w = 0 if self.sf.vsb.winfo_ismapped() else min(self.sf.vsb.winfo_reqwidth(), 24)
        except Exception:
            return
        if win_w <= 1 or canvas_w <= 1:
            return

        # Everything the window spends on itself: padding, border, scrollbar.
        # Measured right after a resize the canvas is still the old size, which
        # makes this look bigger than it is — and it is a property of the window,
        # not of the moment, so keep the smallest reading rather than the latest.
        chrome = max(0, win_w - canvas_w) + max(0, vsb_w)
        if self._chrome_px is None or chrome < self._chrome_px:
            self._chrome_px = chrome
        needed = GROUP_COLS * cell + self._chrome_px + GRID_PAD_PX
        self.minsize(needed, MIN_WINDOW_H)
        if win_w < needed:
            self.geometry(f"{needed}x{max(win_h, MIN_WINDOW_H)}")

    def _doc_btn_width_px(self) -> int:
        """
        Width of one doc button ("ReadMe" / "Details"), measured once.

        Both halves of a card's button area get this as a minimum, so a card
        without a "Details" button keeps the exact geometry of one that has it.
        Without the minimum those two halves shrink to the small icon buttons
        underneath, the lone "ReadMe" shrinks with them, and the whole card is
        pulled narrower than its neighbours.
        """
        if self._doc_btn_px is None:
            probe = ttk.Button(self, text="Details", style="Doc.TButton", width=7)
            try:
                probe.update_idletasks()
                self._doc_btn_px = max(40, probe.winfo_reqwidth())
            except Exception:
                self._doc_btn_px = 56
            finally:
                probe.destroy()
        return self._doc_btn_px

    def _build_group_content(self, gkey: str):
        grid = self._group_grids.get(gkey)
        group_items = self._group_items.get(gkey, [])
        if grid is None or not group_items:
            return
        cols = self._group_cols
        prog_chars = self._prog_btn_chars()
        self._btn_chars_used = prog_chars
        for i, (name, info) in enumerate(group_items):
            r = i // cols
            c = i % cols

            cell = ttk.Frame(grid)
            cell.grid(row=r, column=c, padx=6, pady=6, sticky="w")
            cell.grid_columnconfigure(0, weight=0)
            cell.grid_columnconfigure(1, weight=0)

            img = None
            ip = info.get("icon_path")
            if ip:
                key = str(ip)
                if key not in self._icon_cache:
                    try:
                        pil_img = Image.open(key)
                        if getattr(pil_img, "format", "") == "ICO":
                            sizes = pil_img.info.get("sizes", [])
                            if sizes:
                                candidates = [s for s in sizes if s[0] <= 32]
                                target = max(candidates) if candidates else min(sizes, key=lambda s: s[0])
                                pil_img = Image.open(key).resize(target, Image.LANCZOS)
                        pil_img = pil_img.convert("RGBA").resize((24, 24), Image.LANCZOS)
                        self._icon_cache[key] = ImageTk.PhotoImage(pil_img)
                    except Exception:
                        self._icon_cache[key] = None
                img = self._icon_cache.get(key)

            btn_style = "Update.Prog.TButton" if name in self._update_available else "Prog.TButton"
            # The button is as wide as the longest name in the open groups, but
            # not wider than the cap — a name over the cap ends in "..." instead
            # of being cut off mid-word at the edge of the button.
            btn_text = clamp_label(ui_label(info["label"]), prog_chars)
            btn = ttk.Button(
                cell,
                text=btn_text,
                image=img,
                compound="left",
                style=btn_style,
                width=prog_chars,
                command=lambda n=name: self.launch(n),
            )
            btn.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 6))
            btn.bind("<Button-3>", lambda e, n=name: self._show_group_menu(e, n))
            self._prog_buttons[name] = btn

            sub = ttk.Frame(cell)
            sub.grid(row=0, column=1, rowspan=2, sticky="nsew")
            doc_px = self._doc_btn_width_px()
            # +2 on the left half is the gap the two doc buttons sit apart with.
            sub.grid_columnconfigure(0, weight=1, minsize=doc_px + 2)
            sub.grid_columnconfigure(1, weight=1, minsize=doc_px)
            sub.grid_rowconfigure(0, weight=0)
            sub.grid_rowconfigure(1, weight=0)

            has_readme = bool(info.get("readme_path"))
            has_full = bool(info.get("readme_full_path"))
            # width=7 is not the label length — a ttk button with no explicit
            # width takes the theme's minimum (69-80 px here) however short its
            # text is, and two of those is what made the card 86 px too wide.
            # At width=7 with padding (3,2) the button is 56 px and holds
            # "Details" (35 px) and "ReadMe" (42 px) with room to spare.
            if has_readme or has_full:
                if has_readme:
                    info_btn = ttk.Button(
                        sub, text="ReadMe", style="Doc.TButton", width=7,
                        command=lambda n=name: self.open_readme(n),
                    )
                    info_btn.grid(
                        row=0, column=0,
                        columnspan=1 if has_full else 2,
                        sticky="ew", pady=(0, 2), padx=(0, 2) if has_full else 0,
                    )
                if has_full:
                    full_btn = ttk.Button(
                        sub, text="Details", style="Doc.TButton", width=7,
                        command=lambda n=name: self.open_readme_full(n),
                    )
                    full_btn.grid(
                        row=0, column=1 if has_readme else 0,
                        columnspan=1 if has_readme else 2,
                        sticky="ew", pady=(0, 2),
                    )
            else:
                ttk.Frame(sub, height=1).grid(row=0, column=0, columnspan=2)

            folder_btn = ttk.Button(
                sub, text="📂", style="Info.TButton", width=3,
                command=lambda n=name: self.open_folder(n),
            )
            folder_btn.grid(row=1, column=0, sticky="ew", padx=(0, 2))

            versions = info.get("archive_versions", [])
            if versions:
                mb = ttk.Menubutton(sub, text="  🔽  ", style="Info.TButton", width=4)
                menu = tk.Menu(mb, tearoff=0)
                for v in versions:
                    menu.add_command(
                        label=v["label"],
                        command=lambda p=v["exe_path"], d=info["program_dir"], py=v.get("py_path"): self._launch_exe(p, d, py),
                    )
                mb["menu"] = menu
                mb.grid(row=1, column=1, sticky="ew")
            else:
                ttk.Frame(sub, width=1).grid(row=1, column=1)

        for c in range(cols):
            grid.grid_columnconfigure(c, weight=0, uniform="grpcols")

    def _toggle_group(self, gkey: str):
        expanded = not self._group_expanded.get(gkey, False)
        self._group_expanded[gkey] = expanded
        self._relayouts = 0          # a click starts a fresh relayout chain

        # The buttons are as wide as the longest name in the OPEN groups, so a
        # group that brings a longer (or takes away the longest) name resizes
        # every card — that needs the whole grid built again, not just this one.
        if self._prog_btn_chars() != self._btn_chars_used:
            self._rebuild_buttons()
            # The rebuild itself may only widen the cards; this click may also
            # narrow them, so the columns come back after closing a group.
            self._sync_cell_width(allow_shrink=True)
            return

        frame = self._group_frames.get(gkey)
        btn = self._group_toggle_btns.get(gkey)
        if frame:
            if expanded:
                # Lazy build: populate grid if it has no children yet
                grid = self._group_grids.get(gkey)
                if grid and not grid.winfo_children():
                    self._build_group_content(gkey)
                header = self._group_headers.get(gkey)
                if header:
                    frame.pack(fill="x", padx=6, pady=(2, 0), after=header)
                else:
                    frame.pack(fill="x", padx=6, pady=(2, 0))
            else:
                frame.pack_forget()
        if btn:
            current = btn.cget("text")
            arrow = "▼" if expanded else "▶"
            btn.configure(text=arrow + current[1:])

        # Opening a group can bring a wider card; closing one can take the
        # widest away, and then the cards may fit in more columns again. A click
        # is allowed to make the cards narrower again; a rebuild it starts is
        # not, so the two card sizes can never take turns.
        self._sync_cell_width(allow_shrink=True)

    def _show_group_menu(self, event, program_name: str):
        current_gkey = self._custom_groups.get(
            program_name, group_for_program(program_name))
        menu = tk.Menu(self, tearoff=0)
        if program_name in self._update_available:
            menu.add_command(
                label="✓  Acknowledge update",
                command=lambda: self._acknowledge_update(program_name),
            )
            menu.add_separator()
        menu.add_command(
            label="Move to group:",
            state="disabled",
            font=("Segoe UI", 9, "bold"),
        )
        menu.add_separator()
        for title, gkey in GROUP_ORDER:
            label = f"✓  {title}" if gkey == current_gkey else f"     {title}"
            menu.add_command(
                label=label,
                command=lambda k=gkey: self._move_to_group(program_name, k),
            )
        if program_name in self._custom_groups:
            menu.add_separator()
            menu.add_command(
                label="↺  Reset to default",
                command=lambda: self._move_to_group(program_name, None),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _move_to_group(self, program_name: str, gkey: str | None):
        if gkey is None:
            self._custom_groups.pop(program_name, None)
        else:
            self._custom_groups[program_name] = gkey
        self._config["custom_groups"] = self._custom_groups
        _save_config(self._config)
        scroll_pos = self.sf.canvas.yview()[0]
        self._rebuild_buttons()
        self.after_idle(lambda: self.sf.canvas.yview_moveto(scroll_pos))

    def _rebuild_radiobuttons(self):
        for i, (label, path, configurable) in enumerate(self._root_options):
            if i < len(self._rb_widgets):
                state = "normal" if (path is not None and not (self._is_lab_machine and label.lower().startswith("office"))) else "disabled"
                self._rb_widgets[i].configure(state=state)

    # -------- version update polling --------

    def _schedule_version_poll(self):
        self.after(10000, self._poll_versions)

    def _poll_versions(self):
        if not self.programs or not self.current_root_path:
            self._schedule_version_poll()
            return
        root = self.current_root_path
        programs_snapshot = dict(self.programs)

        def worker():
            updates: set[str] = set()
            # name → (best_exe_path, archive_versions)
            refreshed: dict[str, tuple] = {}
            for name, info in programs_snapshot.items():
                try:
                    current_ver = _exe_version(info["exe_path"])
                    best_exe, archive_vers = self._scan_program_versions(root, name, info)
                    if _exe_version(best_exe) > current_ver:
                        updates.add(name)
                    refreshed[name] = (best_exe, archive_vers)
                except Exception:
                    pass
            self.after(0, lambda: self._apply_version_updates(updates, refreshed))

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

        # B) dist layout: ROOT\dist\<name>\vX.Y.Z\*.exe
        dist_dir = root / "dist" / name
        vf = newest_version_folder(dist_dir)
        if vf:
            exes_v = list(vf.glob("*.exe")) or list(vf.rglob("*.exe"))
            if exes_v:
                best_exe = pick_exe(exes_v, name, vf.name)
                archive_vers = _build_version_list([], program_dir)
                return best_exe, archive_vers

        # fallback: keep existing
        return info["exe_path"], info.get("archive_versions", [])

    def _apply_version_updates(self, updates: set[str], refreshed: dict[str, tuple]):
        for name, (best_exe, archive_vers) in refreshed.items():
            if name in self.programs:
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
        changed = filtered != self._update_available
        self._update_available = filtered
        if changed:
            self._rebuild_buttons()
        self._schedule_version_poll()

    def _acknowledge_update(self, program_name: str):
        """Mark the current update for program_name as seen — clears highlight and blink."""
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
            scroll_pos = self.sf.canvas.yview()[0]
            self._rebuild_buttons()
            self.after_idle(lambda: self.sf.canvas.yview_moveto(scroll_pos))

    # -------- launch --------

    def launch(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        # Use pending (newer) exe if available, otherwise current
        exe_path: Path = self._pending_updates.get(program_name, info["exe_path"])
        self.status.configure(text=f"Starting: {program_name} ...")

        def worker():
            try:
                if not _launch_no_zone_check(exe_path):
                    os.startfile(str(exe_path))  # fallback
                self.after(0, self.status.configure, {"text": f"Started: {program_name}"})
                self.after(0, self._acknowledge_update, program_name)
            except Exception as e:
                self.after(0, messagebox.showerror, "Launch failed", f"{program_name}\n\n{e}")
                self.after(0, self.status.configure, {"text": "Launch failed."})

        threading.Thread(target=worker, daemon=True).start()

    def _launch_exe(self, exe_path: Path, program_dir: Path, py_path: Path | None = None):
        """Spusti archivni verzi programu.
        Zkopiruje exe docasne do program_dir (kde je _internal/), pocka na dokonceni, pak temp kopii smaze."""
        internal_dir = program_dir / "_internal"
        label = exe_path.name

        if internal_dir.exists():
            # Full swap: hide current version, place archive version in program_dir, restore after close.
            temp_dir = program_dir / "archive" / "_temp_latest"
            current_exes = [p for p in program_dir.glob("*.exe") if not TIMESTAMPED_EXE_RE.match(p.name)]
            current_pys = list(program_dir.glob("*.py"))
            staged_exe = program_dir / exe_path.name
            staged_py = (program_dir / py_path.name) if py_path else None

            def worker():
                swapped_files: list[Path] = []
                did_swap = False
                try:
                    if temp_dir.exists():
                        self.after(0, messagebox.showerror, "Launch blocked",
                                   f"A previous old-version launch of this program did not clean up.\n\n"
                                   f"Use the Clean button or restart the launcher to restore it first.")
                        self.after(0, self.status.configure, {"text": "Ready."})
                        return
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    for f in current_exes + current_pys:
                        shutil.move(str(f), str(temp_dir / f.name))
                    did_swap = True
                    shutil.copy2(str(exe_path), str(staged_exe))
                    swapped_files.append(staged_exe)
                    if py_path and py_path.exists():
                        shutil.copy2(str(py_path), str(staged_py))
                        swapped_files.append(staged_py)
                    self.after(0, self.status.configure, {"text": f"Running: {label}"})
                    proc = subprocess.Popen([str(staged_exe)], cwd=str(program_dir))
                    proc.wait()
                except Exception as e:
                    self.after(0, messagebox.showerror, "Launch failed", f"{label}\n\n{e}")
                    self.after(0, self.status.configure, {"text": "Launch failed."})
                finally:
                    for f in swapped_files:
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
                    self.after(0, self.status.configure, {"text": "Ready."})

        elif py_path is not None and py_path.exists():
            # Fallback: run .py via Python (needs Python in PATH)
            launch_py = py_path

            def worker():
                try:
                    if getattr(sys, "frozen", False):
                        _py = (shutil.which("pythonw") or shutil.which("python")
                               or shutil.which("py"))
                        if not _py:
                            raise RuntimeError("Python interpreter not found in PATH.")
                        pythonw = Path(_py)
                    else:
                        pythonw = Path(sys.executable).parent / "pythonw.exe"
                        if not pythonw.exists():
                            pythonw = Path(sys.executable)
                    subprocess.Popen([str(pythonw), str(launch_py)], cwd=str(launch_py.parent))
                    self.after(0, self.status.configure, {"text": f"Started: {label}"})
                except Exception as e:
                    self.after(0, messagebox.showerror, "Launch failed", f"{label}\n\n{e}")
                    self.after(0, self.status.configure, {"text": "Launch failed."})

        else:
            # Last resort: run .exe directly without _internal
            def worker():
                try:
                    subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
                    self.after(0, self.status.configure, {"text": f"Started: {label}"})
                except Exception as e:
                    self.after(0, messagebox.showerror, "Launch failed", f"{label}\n\n{e}")
                    self.after(0, self.status.configure, {"text": "Launch failed."})

        self.status.configure(text=f"Starting: {label} ...")
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

        # Uživatel si vybere, kterou cestu chce nastavit
        choices = list(key_map.keys())
        choice_win = tk.Toplevel(self)
        choice_win.title("Set paths")
        choice_win.resizable(False, False)
        choice_win.grab_set()

        ttk.Label(choice_win, text="Which path do you want to set for your personal computer? Do not set the path for PCs in the lab.", padding=(12, 10)).pack()

        chosen_label = tk.StringVar(value=choices[0])
        for c in choices:
            current = self._config.get(key_map[c][0], "(not set)")
            ttk.Radiobutton(
                choice_win,
                text=f"{c}  —  current: {current}",
                variable=chosen_label,
                value=c,
            ).pack(anchor="w", padx=16, pady=2)

        def on_confirm():
            label = chosen_label.get()
            cfg_key, description = key_map[label]
            choice_win.destroy()

            current = self._config.get(cfg_key, "")
            messagebox.showinfo("Set path", description)

            chosen = filedialog.askdirectory(
                title=f"Select folder for {label}",
                initialdir=current or str(Path.home()),
            )
            if not chosen:
                return

            self._config[cfg_key] = chosen
            _save_config(self._config)  # .json se vytvoří teď, poprvé při uložení

            self._root_options = _apply_config_to_root_options(self._config)

            # Restartuj výběr — zruš aktuální výběr a přebuildi radiobuttons
            self.root_choice.set(-1)
            self._rebuild_radiobuttons()
            self._set_idle_state()

        ttk.Button(choice_win, text="Set selected path", command=on_confirm).pack(pady=(8, 12))

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


if __name__ == "__main__":
    Launcher().mainloop()