# dt_core.py
"""Dev Tools logic without any UI: paths, versions, build, deploy, archive.

Ported on 16.10.2026 from the Tk version (b_t.py + cm_t.py, backed up in
C:\\Dev\\backup_DevTools_tk_20261016). The code paths are kept as they were
there — they are debugged — only the Tk calls are gone: everything that used
to talk to a widget now takes a `log` callable (safe to call from any thread)
and returns its result.

PRACTICE mode (off in normal use)
---------------------------------
For trying out a change to Dev Tools itself without touching anything real,
set PRACTICE = True. Everything then goes to its own places:

- config:       %APPDATA%\\DevToolsPractice\\config.json
- state files:  C:\\Dev\\DevTools_practice\\state\\
- destinations: C:\\Dev\\DevTools_practice\\Scratch / SharePoint
- build output: C:\\Dev\\DevTools_practice\\dist

The first start seeds those from the real ones (read only), so the versions and
project groups look like the real thing.
"""
from __future__ import annotations

import ast
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Callable

PRACTICE = False
APP_KEY = "DevToolsPractice" if PRACTICE else "DevTools"
PRACTICE_ROOT = Path(r"C:\Dev\DevTools_practice")

_APPDATA = Path(os.environ.get("APPDATA", "~")).expanduser()
CONFIG_PATH = _APPDATA / APP_KEY / "config.json"
_REAL_CONFIG_PATH = _APPDATA / "DevTools" / "config.json"

LogFn = Callable[[str], None]


def _no_log(_msg: str) -> None:
    pass


# ----------------- APP FOLDERS -----------------
def app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def icon_file() -> Path | None:
    """icon.ico: next to the exe, then inside the bundle, then the source folder."""
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


def state_dir() -> Path:
    return PRACTICE_ROOT / "state" if PRACTICE else app_dir()


def settings_path() -> Path:
    return state_dir() / "builder_settings.json"


def usage_log_path() -> Path:
    return state_dir() / "build_usage.json"


def build_times_path() -> Path:
    return state_dir() / "build_times.log"


def cm_state_path() -> Path:
    return state_dir() / "copy_manager_state.ini"


# ----------------- JSON / CONFIG -----------------
def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_config() -> dict:
    return load_json(CONFIG_PATH, {})


def save_config(data: dict):
    try:
        save_json(CONFIG_PATH, data)
    except Exception:
        pass


def ensure_practice_setup(log: LogFn = _no_log) -> None:
    """Create the practice folders and seed them once from the real setup.

    Only reads the real config, state and Versions.txt; writes nothing outside
    PRACTICE_ROOT and %APPDATA%\\DevToolsPractice.
    """
    if not PRACTICE:
        return
    for sub in ("Scratch", "SharePoint", "dist", "state"):
        (PRACTICE_ROOT / sub).mkdir(parents=True, exist_ok=True)

    real_cfg = load_json(_REAL_CONFIG_PATH, {})
    src_root = Path(__file__).resolve().parent.parent
    if not CONFIG_PATH.exists():
        save_config({
            "root_folder": real_cfg.get("root_folder") or str(src_root),
            "dist_root": str(PRACTICE_ROOT / "dist"),
            "scratch": str(PRACTICE_ROOT / "Scratch"),
            "sharepoint": str(PRACTICE_ROOT / "SharePoint"),
        })
        log(f"Practice mode: config created -> {CONFIG_PATH}")

    real_dev_tools = src_root / "Dev Tools"
    for name in ("builder_settings.json", "copy_manager_state.ini"):
        dst = state_dir() / name
        src = real_dev_tools / name
        if not dst.exists() and src.exists():
            try:
                shutil.copy2(src, dst)
                log(f"Practice mode: seeded {name} from Dev Tools")
            except OSError as e:
                log(f"Practice mode: could not seed {name}: {e}")

    vt = PRACTICE_ROOT / "Scratch" / "Versions.txt"
    real_scratch = real_cfg.get("scratch")
    if not vt.exists() and real_scratch:
        try:
            real_vt = Path(real_scratch) / "Versions.txt"
            if real_vt.exists():
                vt.write_text(real_vt.read_text(encoding="utf-8"), encoding="utf-8")
                log("Practice mode: seeded Versions.txt from the real scratch")
        except OSError as e:
            log(f"Practice mode: could not seed Versions.txt: {e}")


# ----------------- PATHS -----------------
def programs_root() -> Path:
    """Root folder containing all program source subfolders.
    Priority: 1) config  2) builder settings  3) the repo this file sits in."""
    cfg = load_config()
    root = cfg.get("root_folder")
    if root and Path(root).exists():
        return Path(root)
    root = load_json(settings_path(), {}).get("root_folder")
    if root and Path(root).exists():
        return Path(root)
    return Path(__file__).resolve().parent.parent


def dist_root() -> Path:
    override = load_config().get("dist_root")
    if override:
        return Path(override)
    return programs_root().parent / "dist"


def scratch_root() -> Path | None:
    s = load_config().get("scratch")
    return Path(s) if s else None


def destination_roots() -> list[tuple[str, Path]]:
    cfg = load_config()
    roots = []
    if cfg.get("scratch"):
        roots.append(("Scratch", Path(cfg["scratch"])))
    if cfg.get("sharepoint"):
        roots.append(("Sharepoint", Path(cfg["sharepoint"])))
    return roots


def internal_builder_dist() -> Path:
    r"""C:\Dev\dist\_internal_builder — local (outside OneDrive), or programy/dist."""
    local = Path(r"C:\Dev\dist") / "_internal_builder"
    if local.exists():
        return local
    return dist_root() / "Internal Builder"


def internal_builder_distpath() -> Path:
    """Where building the Internal Builder writes. Practice keeps off C:\\Dev\\dist."""
    return PRACTICE_ROOT / "dist" if PRACTICE else Path(r"C:\Dev\dist")


# ----------------- VERSIONS.TXT -----------------
# Versions.txt lives on the scratch share, where a single read costs tens of
# milliseconds. The project list asks for it once per row, so the answer is kept
# for a few seconds instead of being fetched dozens of times per redraw.
_VERSIONS_CACHE: dict = {"stamp": 0.0, "data": {}}
_VERSIONS_TTL = 15.0


def versions_txt_path() -> Path | None:
    r = scratch_root()
    return (r / "Versions.txt") if r else None


def invalidate_versions_cache():
    _VERSIONS_CACHE["stamp"] = 0.0


def read_versions_txt(force: bool = False) -> dict[str, str]:
    now = time.monotonic()
    if not force and _VERSIONS_CACHE["stamp"] and (now - _VERSIONS_CACHE["stamp"]) < _VERSIONS_TTL:
        return dict(_VERSIONS_CACHE["data"])
    p = versions_txt_path()
    result: dict[str, str] = {}
    if p and p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    name, _, ver = line.partition("=")
                    result[name.strip()] = ver.strip()
        except Exception:
            pass
    _VERSIONS_CACHE["data"] = result
    _VERSIONS_CACHE["stamp"] = now
    return dict(result)


# Builds run side by side: every read-modify-write of a shared file holds this.
_SHARED_FILE_LOCK = threading.RLock()


def write_version_to_txt(program_name: str, version: str):
    with _SHARED_FILE_LOCK:
        _write_version_to_txt(program_name, version)


def _write_version_to_txt(program_name: str, version: str):
    p = versions_txt_path()
    if not p:
        return
    versions = read_versions_txt(force=True)
    versions[program_name] = version if version.startswith("v") else f"v{version}"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k} = {v}" for k, v in sorted(versions.items())]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not write Versions.txt: {e}")
    invalidate_versions_cache()


# ----------------- VERSION HELPERS -----------------
RE_VER = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$")
RE_VDIR = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)
VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_VERSION_LOOSE_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
# Archived-style exe name, e.g. "Image Tools v2.5.2__20260526_090829.exe"
TIMESTAMPED_EXE_RE = re.compile(r"^.+__\d{8}_\d{6}\.exe$", re.IGNORECASE)


def parse_version_tuple(ver: str):
    m = RE_VER.match(ver or "")
    return tuple(map(int, m.groups())) if m else None


def bump_patch(ver: str) -> str:
    t = parse_version_tuple(ver)
    if not t:
        return "1.0.0"
    return f"{t[0]}.{t[1]}.{t[2] + 1}"


def parse_version(s: str):
    m = _VERSION_LOOSE_RE.fullmatch((s or "").strip())
    return tuple(map(int, m.groups())) if m else None


def is_newer_version(latest: str, last_deployed: str) -> bool:
    vt_latest = parse_version(latest or "")
    vt_deployed = parse_version(last_deployed or "")
    if not vt_latest:
        return False
    if not vt_deployed:
        return True
    return vt_latest > vt_deployed


def list_versions(dist_dir: Path) -> list[Path]:
    """Version folders sorted by version, newest first."""
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


def last_version_in_dist(dist: Path, name: str) -> str | None:
    base = dist / name
    best = None
    try:
        for p in base.iterdir():
            if not p.is_dir():
                continue
            m = RE_VDIR.match(p.name)
            if m:
                t = tuple(map(int, m.groups()))
                if best is None or t > best:
                    best = t
    except Exception:
        pass
    return f"{best[0]}.{best[1]}.{best[2]}" if best else None


def last_built_version(name: str, dist: Path) -> tuple[str | None, bool]:
    """(last version, is_new): Versions.txt first, then the dist folder."""
    versions = read_versions_txt()
    if name in versions:
        ver = versions[name].lstrip("v")
        if parse_version_tuple(ver):
            return ver, False
    ver = last_version_in_dist(dist, name)
    return (ver, False) if ver else (None, True)


def fmt_elapsed(seconds: float) -> str:
    """Seconds as "4.5s" or "2m 38s" — the form used in every end-of-run report."""
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    return f"{seconds:.1f}s"


# ----------------- PROJECT DISCOVERY (Builder) -----------------
ALWAYS_IGNORE = {"dist", "matlab", "icons", ".vscode", ".venv", ".git"}

# Dev Tools runs windowed, so every console program it starts (py, PyInstaller
# and PyInstaller's own helper processes) would otherwise open a console window
# that flashes up and takes the keyboard focus.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def guess_main_py(project_dir: Path) -> Path | None:
    pys = [p for p in project_dir.glob("*.py") if p.name != "__init__.py"]
    if not pys:
        return None
    slug = project_dir.name.replace(" ", "_").lower()
    for stem in (project_dir.name, slug, project_dir.name.replace(" ", "").lower()):
        cand = project_dir / f"{stem}.py"
        if cand.exists():
            return cand
    for p in pys:
        if p.stem.replace(" ", "_").lower() == slug:
            return p
    for name in ("main.py", "app.py"):
        if (project_dir / name).exists():
            return project_dir / name
    pys.sort(key=lambda x: x.name.lower())
    return pys[0]


def find_projects(root: Path) -> list[Path]:
    out = []
    if not root.exists():
        return out
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("_") or p.name.lower() in ALWAYS_IGNORE:
            continue
        if guess_main_py(p) is not None:
            out.append(p)
    return sorted(out, key=lambda p: p.name.lower())


def find_helpers(projects: list[Path], cache: dict | None = None) -> dict[str, list[dict]]:
    """Helper exes per project, read from each project's build_config.json.

    A project has helpers exactly when its build_config.json lists them under
    `extra_exes`. Cached against the config's own timestamp, so an edited
    config shows up on the next refresh.
    """
    cache = cache if cache is not None else {}
    out: dict[str, list[dict]] = {}
    for p in projects:
        cfg_path = p / "build_config.json"
        try:
            stamp = cfg_path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        cached = cache.get(p.name)
        if cached is not None and cached[0] == stamp:
            if cached[1]:
                out[p.name] = cached[1]
            continue
        cfg = load_json(cfg_path, {})
        items = []
        for spec in (cfg.get("extra_exes") or []):
            if isinstance(spec, str):
                spec = {"script": spec}
            script = p / spec.get("script", "")
            if not script.exists():
                continue
            items.append({
                "name": spec.get("name") or script.stem,
                "parent": p,
                "script": script,
                "windowed": bool(spec.get("windowed")),
                "hidden_imports": (list(cfg.get("hidden_imports", []))
                                   + list(spec.get("hidden_imports", []))),
                # Inherited by default: a helper that ships beside the app
                # generally reads the same data files (Diagnostic's
                # notify_provision.dat), and in its own folder needs its own copy.
                "extra_files": list(spec.get("extra_files", cfg.get("extra_files", []))),
                "exclude_modules": list(cfg.get("exclude_modules", [])),
            })
        cache[p.name] = (stamp, items)
        if items:
            out[p.name] = items
    return out


# ----------------- MODULE HOME CHECK -----------------
_MODULE_SCAN_SKIP_DIRS = {"_internal", "__pycache__", ".git", "dist", "build",
                          "archive", "testing", "tests", "DataRepository"}


def module_homes(root: Path) -> dict[str, list[Path]]:
    """Module name -> every program folder that has a .py with that name.
    Only the top level of each folder: that is all PyInstaller sees."""
    homes: dict[str, list[Path]] = {}
    try:
        folders = [d for d in root.iterdir()
                   if d.is_dir() and d.name not in _MODULE_SCAN_SKIP_DIRS
                   and not d.name.startswith(".")]
    except Exception:
        return homes
    for d in folders:
        for f in d.glob("*.py"):
            if f.name.startswith(("test_", "_verify")) or f.stem == "main":
                continue
            homes.setdefault(f.stem, []).append(f)
    return homes


def check_module_homes(p: Path, py_files: list[Path], root: Path,
                       log: LogFn = _no_log) -> tuple[bool, str]:
    """Refuse to build when a module the program imports is not in its folder,
    or has a second copy in another program folder (INFRASTRUCTURE.md §7)."""
    homes = module_homes(root)
    if not homes:
        return True, ""
    imported: set[str] = set()
    for f in py_files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    own = {f.stem for f in py_files}
    problems: list[str] = []
    for mod in sorted(imported):
        where = homes.get(mod)
        if not where:
            continue
        elsewhere = [f for f in where if f.parent != p]
        if mod not in own:
            problems.append(
                f"  '{mod}' is imported but there is no {mod}.py in this folder.\n"
                f"    It lives in: " + ", ".join(f.parent.name for f in elsewhere) +
                f"\n    Move {mod}.py into '{p.name}' - the build cannot reach it anywhere else.")
        elif elsewhere:
            lines = [f"  '{mod}.py' exists in more than one folder - the build "
                     f"uses the one in '{p.name}':"]
            for f in [p / f"{mod}.py"] + elsewhere:
                try:
                    st = f.stat()
                    stamp = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                    size = st.st_size
                except Exception:
                    stamp, size = "?", 0
                lines.append(f"    {f}  ({size} B, {stamp})")
            lines.append("    Keep one copy only, or the build will drift from what you edit.")
            problems.append("\n".join(lines))
    if not problems:
        return True, ""
    msg = f"Module home check failed for '{p.name}':\n" + "\n".join(problems)
    for line in msg.splitlines():
        log(line)
    return False, msg


# ----------------- BUILD -----------------
ASSET_DIR_NAMES = ("images", "sounds", "assets", "icons", "img", "audio",
                   "fonts", "templates")

# Folders a running program writes its shared data into on the share
# (Laser settings\shared). No build carries them and no deploy may touch them.
SHARED_DATA_DIR_NAMES = ("shared",)

# No --collect-all is added on its own any more: it pulled every submodule and
# data file of numpy/scipy/matplotlib/pandas into every build and made the
# analysis several times longer. PyInstaller's own hooks bundle what these need;
# a program that still misses something lists it in build_config.json.
# --collect-all also drags in the packages' own test suites. Only ".tests" —
# never ".testing"/"._testing", which some libraries use at runtime.
_TEST_SUBMODULES = {
    "numpy": ["numpy.tests", "numpy.f2py.tests", "numpy.random.tests",
              "numpy.linalg.tests", "numpy.fft.tests", "numpy.ma.tests",
              "numpy.lib.tests", "numpy.core.tests", "numpy.typing.tests"],
    "scipy": ["scipy.tests"],
    "sklearn": ["sklearn.tests"],
    "matplotlib": ["matplotlib.tests"],
    "pandas": ["pandas.tests"],
}


def new_workdir(name: str) -> Path:
    """A private PyInstaller work folder for this one build.

    A shared work folder was deleted by one build while another was still
    running in it. Folders older than a day are swept away."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "build"
    root = Path(tempfile.gettempdir()) / "universal_builder_pyinstaller"
    try:
        cutoff = time.time() - 24 * 3600
        for old in root.iterdir():
            if old.stat().st_mtime < cutoff:
                shutil.rmtree(str(old), ignore_errors=True)
    except Exception:
        pass
    return root / f"{safe}_{os.getpid()}_{int(time.time() * 1000) % 1_000_000}"


def as_out_dir(msg: str) -> str:
    """Keep only what is really a path (the Internal Builder returns a sentence)."""
    if not msg or "\n" in msg:
        return ""
    return msg if ("\\" in msg or "/" in msg) else ""


def exe_icon_count(exe: Path) -> int:
    """Icon groups the built .exe carries; 0 = none, -1 = could not count."""
    try:
        import ctypes
        import ctypes.wintypes as _wt
        _ex = ctypes.windll.shell32.ExtractIconExW
        _ex.argtypes = [_wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(_wt.HICON),
                        ctypes.POINTER(_wt.HICON), ctypes.c_uint]
        _ex.restype = ctypes.c_uint
        return int(_ex(str(exe), -1, None, None, 0))
    except Exception:
        return -1


def _rmtree_readonly(path: Path):
    """rmtree that clears the read-only flag OneDrive leaves on synced files."""
    def _on_rm_error(func, p, _exc):
        import stat as _stat
        try:
            os.chmod(p, _stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(str(path), onerror=_on_rm_error)


def _run_logged(args: list[str], cwd: Path, log: LogFn,
                on_line: LogFn | None = None) -> int:
    # Below normal priority: a build takes minutes of CPU and the PC must stay
    # usable meanwhile (a PC was seen freezing during heavy builds).
    flags = NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(cwd), creationflags=flags)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            if on_line is not None:
                on_line(line)
            log(line)
    proc.wait()
    return proc.returncode


class PhaseClock:
    """Wall-clock seconds of one build, split by phase.

    analysis = start until PyInstaller starts packing (finding every module);
    package  = PYZ / PKG / EXE (packing the found modules into the exe);
    collect  = COLLECT (copying the libraries into _internal; one-file has none);
    finish   = after PyInstaller (moving files, assets, ReadMes)."""
    PHASES = ("analysis", "package", "collect", "finish")
    _MARKS = (("Building PYZ", "package"), ("checking PYZ", "package"),
              ("Building COLLECT", "collect"), ("checking COLLECT", "collect"))

    def __init__(self):
        self.seconds = dict.fromkeys(self.PHASES, 0.0)
        self.current = "analysis"
        self._since = time.perf_counter()

    def enter(self, phase: str):
        """Move on to a later phase; an earlier one is ignored."""
        if self.PHASES.index(phase) <= self.PHASES.index(self.current):
            return
        now = time.perf_counter()
        self.seconds[self.current] += now - self._since
        self.current, self._since = phase, now

    def on_line(self, line: str):
        for key, phase in self._MARKS:
            if key in line:
                self.enter(phase)
                return

    def stop(self) -> dict[str, float]:
        now = time.perf_counter()
        self.seconds[self.current] += now - self._since
        self._since = now
        return dict(self.seconds)

    def short(self) -> str:
        return ", ".join(f"{k} {v:.0f}s" for k, v in self.seconds.items() if v >= 0.5)


def _free_ram_gb() -> float:
    """Free physical memory in GB (a large number when it cannot be read)."""
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = _MemStatus()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullAvailPhys / 1024 ** 3
    except Exception:
        pass
    return 64.0


def parallel_builds(n_queued: int) -> int:
    """How many builds run at once: PyInstaller keeps about one core busy and
    takes up to ~2 GB with scipy, so at most 3 and never more than the machine
    has room for. The rest of the PC stays usable."""
    by_cpu = (os.cpu_count() or 4) // 4
    by_ram = int(_free_ram_gb() // 2)
    return max(1, min(3, by_cpu, by_ram, n_queued))


def record_build_times(name: str, version: str, total: float,
                       phases: dict[str, float] | None, copy_s: float | None = None):
    """One tab-separated line per build in build_times.log (never trimmed)."""
    phases = phases or {}
    cols = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, version, f"{total:.1f}"]
    cols += [f"{phases.get(k, 0.0):.1f}" for k in PhaseClock.PHASES]
    cols.append("" if copy_s is None else f"{copy_s:.1f}")
    path = build_times_path()
    with _SHARED_FILE_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with open(path, "a", encoding="utf-8") as f:
                if new:
                    f.write("\t".join(["date", "program", "version", "total"]
                                      + list(PhaseClock.PHASES) + ["copy"]) + "\n")
                f.write("\t".join(cols) + "\n")
        except OSError:
            pass


def _rm_norm(s: str) -> str:
    return s.lower().replace("_", "").replace(" ", "").replace("-", "").replace(".", "")


def record_build_usage(project_name: str):
    path = usage_log_path()
    with _SHARED_FILE_LOCK:
        usage = load_json(path, {"events": []})
        usage.setdefault("events", []).append({"ts": time.time(), "project": project_name})
        usage["events"] = usage["events"][-500:]
        try:
            save_json(path, usage)
        except Exception:
            pass


def build_one_project(p: Path, ver: str, dist: Path, root: Path,
                      log: LogFn = _no_log,
                      clock: PhaseClock | None = None) -> tuple[bool, str]:
    """Build a project with PyInstaller into <dist>/<project>/v<ver>/."""
    clock = clock or PhaseClock()
    name = p.name
    main_path = guess_main_py(p)
    if not main_path:
        return False, f"No .py entry found in:\n{p}"
    if not dist.exists():
        return False, f"Build aborted — dist folder not found:\n{dist}"

    verdir = dist / name / f"v{ver}"
    # PyInstaller --noconfirm fails on the read-only flags OneDrive leaves behind.
    if verdir.exists():
        _rmtree_readonly(verdir)
    verdir.mkdir(parents=True, exist_ok=True)

    workdir = new_workdir(name)
    specdir = workdir / "spec"
    builddir = workdir / "build"
    specdir.mkdir(parents=True, exist_ok=True)

    icon_path = p.resolve() / "icon.ico"
    icon_args = []
    if icon_path.exists():
        # Inside the bundle as well: icon_file() falls back to sys._MEIPASS.
        icon_args = ["--icon", str(icon_path), "--add-data", f"{icon_path};."]

    # Test scripts are dev-only — nothing imports them at runtime.
    extra_py_files = [x.resolve() for x in p.glob("*.py")
                      if x.name != main_path.name and not x.name.startswith("test_")]

    ok, why = check_module_homes(p, [main_path.resolve()] + extra_py_files, root, log)
    if not ok:
        return False, why

    build_cfg = load_json(p / "build_config.json", {})
    collect_all = list(build_cfg.get("collect_all", []))
    collect_bins = build_cfg.get("collect_binaries", [])
    hidden_imports = build_cfg.get("hidden_imports", [])
    copy_metadata = build_cfg.get("copy_metadata", [])
    exclude_modules = list(build_cfg.get("exclude_modules", []))
    extra_data_files = list(build_cfg.get("extra_files", []))

    # Runtime asset folders ship with every build (same list as the deploy's).
    for asset in ASSET_DIR_NAMES:
        if (p / asset).is_dir() and asset not in extra_data_files:
            extra_data_files.append(asset)

    for pkg in collect_all:
        for mod in _TEST_SUBMODULES.get(pkg, []):
            if mod not in exclude_modules:
                exclude_modules.append(mod)

    args = ["py", "-m", "PyInstaller", "--onedir", "--windowed", "--noconfirm",
            "--name", name] + icon_args + [
        "--distpath", str(verdir), "--workpath", str(builddir),
        "--specpath", str(specdir), "--paths", str(p),
        "--hidden-import", "concurrent.futures", "--hidden-import", "concurrent",
        "--hidden-import", "zoneinfo", "--hidden-import", "zoneinfo._tzdata",
    ]
    for pkg in collect_all:
        args += ["--collect-all", pkg]
    for pkg in collect_bins:
        args += ["--collect-binaries", pkg]
    # Every source of the program is analysed, not only what the entry file
    # imports: Image Tools loads its tabs from their .py files at run time
    # (importlib), so their imports (scipy.ndimage, matplotlib.widgets, ...)
    # were invisible and only --collect-all of whole packages covered them.
    for extra in extra_py_files:
        if extra.stem.isidentifier() and extra.stem not in hidden_imports:
            args += ["--hidden-import", extra.stem]
    for imp in hidden_imports:
        args += ["--hidden-import", imp]
    for pkg in copy_metadata:
        args += ["--copy-metadata", pkg]
    for mod in exclude_modules:
        args += ["--exclude-module", mod]
    for extra in extra_py_files:
        args += ["--add-data", f"{extra};."]
    args.append(main_path.name)

    log(f"BUILD cwd: {p}")
    log(f"CMD: {' '.join(args)}\n")
    rc = _run_logged(args, p, log, clock.on_line)
    if rc != 0:
        return False, f"PyInstaller failed with code {rc}\n\n{' '.join(args)}"
    clock.enter("finish")

    # Move the app one level up into the version folder.
    inner_dir = verdir / name
    if inner_dir.exists():
        for item in inner_dir.iterdir():
            target = verdir / item.name
            try:
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink()
                shutil.move(str(item), str(verdir))
            except Exception as e:
                log(f"Warning: could not move {item}: {e}")
        try:
            inner_dir.rmdir()
        except Exception:
            pass

    exe_src = verdir / f"{name}.exe"
    exe_dst = verdir / f"{name} v{ver}.exe"
    if exe_src.exists():
        try:
            exe_src.rename(exe_dst)
        except Exception as e:
            log(f"Warning: could not rename exe: {e}")

    if icon_path.exists():
        try:
            shutil.copy2(str(icon_path), str(verdir / "icon.ico"))
        except Exception:
            pass

    if exe_dst.exists():
        n = exe_icon_count(exe_dst)
        if n > 0:
            log(f"  icon: the exe carries its own ({n} group(s))")
        elif n == 0:
            log("Warning: the built exe carries NO icon of its own. Check that "
                "icon.ico sits in the program's folder.")
        else:
            log("Warning: could not check whether the exe carries an icon")

    try:
        (verdir / f"{name} v{ver}.py").write_bytes(main_path.read_bytes())
        for extra in extra_py_files:
            (verdir / extra.name).write_bytes(extra.read_bytes())
    except Exception as e:
        log(f"Warning: could not copy source files: {e}")

    for fname in extra_data_files:
        src = p / fname
        dst = verdir / src.name
        if src.is_dir():
            try:
                if dst.exists():
                    shutil.rmtree(str(dst), ignore_errors=True)
                shutil.copytree(str(src), str(dst),
                                ignore=shutil.ignore_patterns("__pycache__", "Thumbs.db"))
                n = sum(1 for f in dst.rglob("*") if f.is_file())
                log(f"  extra folder: {src.name}/  ({n} files)")
            except Exception as e:
                log(f"Warning: could not copy extra folder {fname}: {e}")
        elif src.exists():
            try:
                shutil.copy2(str(src), str(dst))
                log(f"  extra file: {src.name}")
            except Exception as e:
                log(f"Warning: could not copy extra file {fname}: {e}")
        else:
            log(f"Warning: extra_file not found: {src}")

    # Both user documents: the Launcher has a button for each.
    short = {_rm_norm(f"ReadMe_{name}")}
    full = {_rm_norm(f"ReadMe_{name}_Full"), _rm_norm(f"ReadMe_{name}_Details"),
            _rm_norm(f"Manual_{name}")}
    readme = next((f for f in p.iterdir() if f.is_file() and _rm_norm(f.stem) in short), None)
    if readme is None:
        readme = next((p / c for c in ("ReadMe.txt", "README.md", "README.txt", "ReadMe.md")
                       if (p / c).exists()), None)
    readme_full = next((f for f in p.iterdir() if f.is_file() and _rm_norm(f.stem) in full), None)
    if readme is None:
        log(f"Warning: no ReadMe found in {p} — deploy will keep the published one")
    if readme_full is None:
        log(f"Note: no ReadMe_{name}_Full — the Launcher will show no Details button")
    for doc, what in ((readme, "readme"), (readme_full, "readme (details)")):
        if doc is None:
            continue
        try:
            shutil.copy2(str(doc), str(verdir / doc.name))
            log(f"  {what}: {doc.name}")
        except Exception as e:
            log(f"Warning: could not copy {doc.name}: {e}")

    shutil.rmtree(workdir, ignore_errors=True)
    write_version_to_txt(name, ver)
    record_build_usage(name)

    detail = [f"  Output: {verdir}"]
    try:
        for item in sorted(verdir.iterdir(), key=lambda x: (x.is_dir(), x.name.lower())):
            if item.is_dir():
                n = sum(1 for f in item.rglob("*") if f.is_file())
                detail.append(f"    [dir]   {item.name}/  ({n} files)")
            else:
                detail.append(f"    [file]  {item.name}  ({item.stat().st_size / 1024:.0f} KB)")
    except Exception:
        pass
    log("\n".join(detail))
    return True, str(verdir)


def build_one_helper(h: dict, ver: str, dist: Path, log: LogFn = _no_log,
                     clock: PhaseClock | None = None) -> tuple[bool, str]:
    """Build one helper exe into its own <dist>/<name>/v<ver>/ folder.

    --onefile (cannot collide with the parent's _internal, survives being copied
    by hand) and --console by default (the window is the sign it is alive)."""
    clock = clock or PhaseClock()
    name, p, src = h["name"], h["parent"], h["script"]
    if not src.exists():
        return False, f"Helper script not found:\n{src}"

    verdir = dist / name / f"v{ver}"
    if verdir.exists():
        _rmtree_readonly(verdir)
    verdir.mkdir(parents=True, exist_ok=True)

    workdir = new_workdir(name)
    specdir = workdir / "spec"
    builddir = workdir / "build"
    specdir.mkdir(parents=True, exist_ok=True)

    icon_path = p.resolve() / "icon.ico"
    icon_args = (["--icon", str(icon_path), "--add-data", f"{icon_path};."]
                 if icon_path.exists() else [])
    args = ["py", "-m", "PyInstaller", "--onefile",
            "--windowed" if h.get("windowed") else "--console",
            "--noconfirm", "--name", name] + icon_args + [
        "--distpath", str(verdir), "--workpath", str(builddir),
        "--specpath", str(specdir), "--paths", str(p)]
    for imp in h.get("hidden_imports", []):
        args += ["--hidden-import", imp]
    for mod in h.get("exclude_modules", []):
        args += ["--exclude-module", mod]
    args.append(src.name)

    log(f"BUILD cwd: {p}")
    log(f"CMD: {' '.join(args)}\n")
    rc = _run_logged(args, p, log, clock.on_line)
    if rc != 0:
        return False, f"PyInstaller failed with code {rc}\n\n{' '.join(args)}"
    clock.enter("finish")

    exe_src = verdir / f"{name}.exe"
    exe_dst = verdir / f"{name} v{ver}.exe"
    if exe_src.exists():
        try:
            if exe_dst.exists():
                exe_dst.unlink()
            exe_src.rename(exe_dst)
        except OSError as e:
            log(f"Warning: could not rename exe: {e}")

    try:
        (verdir / f"{name} v{ver}.py").write_bytes(src.read_bytes())
    except OSError as e:
        log(f"Warning: could not copy source: {e}")

    for fname in h.get("extra_files", []):
        fsrc = p / fname
        fdst = verdir / Path(fname).name
        try:
            if fsrc.is_dir():
                shutil.copytree(str(fsrc), str(fdst),
                                ignore=shutil.ignore_patterns("__pycache__", "Thumbs.db"))
                log(f"  extra folder: {fsrc.name}/")
            elif fsrc.exists():
                shutil.copy2(str(fsrc), str(fdst))
                log(f"  extra file: {fsrc.name}")
            else:
                log(f"Warning: extra_file not found: {fsrc}")
        except OSError as e:
            log(f"Warning: could not copy extra {fname}: {e}")

    if icon_path.exists():
        try:
            shutil.copy2(str(icon_path), str(verdir / "icon.ico"))
        except OSError:
            pass

    if exe_dst.exists():
        n = exe_icon_count(exe_dst)
        if n > 0:
            log(f"  icon: the exe carries its own ({n} group(s))")
        elif n == 0:
            log("Warning: the built exe carries NO icon of its own")
        else:
            log("Warning: could not check whether the exe carries an icon")

    # The helper's OWN documents, named after the helper, in the parent's folder.
    short = {_rm_norm(f"ReadMe_{name}")}
    full = {_rm_norm(f"ReadMe_{name}_Full"), _rm_norm(f"ReadMe_{name}_Details"),
            _rm_norm(f"Manual_{name}")}
    docs: dict[str, Path] = {}
    for f in p.iterdir():
        if not f.is_file():
            continue
        key = _rm_norm(f.stem)
        if key in full:
            docs.setdefault("readme (details)", f)
        elif key in short:
            docs.setdefault("readme", f)
    if "readme" not in docs:
        log(f"Warning: no ReadMe_{name} in {p} — deploy will keep the published one")
    if "readme (details)" not in docs:
        log(f"Note: no ReadMe_{name}_Full — the Launcher will show no Details button")
    for what, doc in docs.items():
        try:
            shutil.copy2(str(doc), str(verdir / doc.name))
            log(f"  {what}: {doc.name}")
        except OSError as e:
            log(f"Warning: could not copy {doc.name}: {e}")

    shutil.rmtree(workdir, ignore_errors=True)
    try:
        write_version_to_txt(name, ver)
    except Exception as e:
        log(f"Warning: could not write Versions.txt: {e}")
    return True, str(verdir)


def build_internal_builder(root: Path, log: LogFn = _no_log,
                           clock: PhaseClock | None = None) -> tuple[bool, str]:
    """Build _internal_builder — from its .spec if there is one, else its __main__."""
    script = root / "Internal Builder" / "_internal_builder.py"
    if not script.exists():
        return False, f"Builder script not found:\n{script}"
    spec = script.parent / "_internal_builder.spec"
    # Frozen, sys.executable is Dev Tools itself, not Python.
    py = "py" if getattr(sys, "frozen", False) else sys.executable
    if spec.exists():
        cmd = [py, "-m", "PyInstaller", "--noconfirm",
               "--distpath", str(internal_builder_distpath()), str(spec)]
    else:
        cmd = [py, str(script)]
    log(f"CMD: {' '.join(cmd)}\n")
    try:
        rc = _run_logged(cmd, script.parent, log,
                         clock.on_line if clock is not None else None)
    except Exception as e:
        return False, f"ERROR: {e}"
    if rc == 0:
        return True, "Internal Builder build succeeded."
    return False, f"Internal Builder build failed (rc={rc})"


def build_summary(ok_count: int, fail_count: int, failed: list[str],
                  built_paths: list[tuple], elapsed: float) -> str:
    lines = ["=" * 40,
             f"BUILD DONE  {'✓ No errors' if fail_count == 0 else '⚠ ' + str(fail_count) + ' failed'}",
             f"  Time elapsed:   {fmt_elapsed(elapsed)}",
             f"  Built:          {ok_count}  |  Failed: {fail_count}"]
    if built_paths:
        lines.append("")
        for name, ver, t, out_dir, *rest in built_paths:
            split = f": {rest[0]}" if rest and rest[0] else ""
            lines.append(f"  ✓  {name} {ver}  ({t:.1f}s{split})")
            if out_dir:
                lines.append(f"       → {out_dir}")
    if failed:
        lines.append("")
        for f in failed[:10]:
            lines.append(f"  ✗  {f.splitlines()[0]}")
    lines.append("=" * 40)
    return "\n".join(lines)


# ----------------- COPY MANAGER: PROGRAM LIST -----------------
CM_IGNORE = {"dist", "archive", "internal builder", "l3-qol-janjan", ".venv", ".vscode",
             ".git", "matlab", "icons", "extractor", "shift planner", "time converter"}


def list_program_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in sorted(root.iterdir(), key=lambda x: x.name.lower())
            if p.is_dir() and not p.name.startswith(("_", "."))
            and p.name.lower() not in CM_IGNORE]


def find_helper_programs(program_dirs: list[Path]) -> dict[str, list[str]]:
    """Helper exe names per program folder (build_config.json -> extra_exes)."""
    out: dict[str, list[str]] = {}
    for p in program_dirs:
        try:
            cfg = json.loads((p / "build_config.json").read_text(encoding="utf-8"))
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


_HELPER_PARENTS: dict[str, str] | None = None   # helper name (lower) -> program folder name


def helper_parents(refresh: bool = False) -> dict[str, str]:
    global _HELPER_PARENTS
    if _HELPER_PARENTS is None or refresh:
        mapping: dict[str, str] = {}
        try:
            dirs = [p for p in programs_root().iterdir()
                    if p.is_dir() and not p.name.startswith("_")]
            taken = {p.name.lower() for p in dirs}
            for parent, names in find_helper_programs(dirs).items():
                for n in names:
                    if n.lower() not in taken:
                        mapping[n.lower()] = parent
        except Exception:
            pass
        _HELPER_PARENTS = mapping
    return _HELPER_PARENTS


def dest_dir_for(dst_root: Path, program_name: str) -> Path:
    """A helper exe goes INSIDE its program's folder; everything else on top."""
    parent = helper_parents().get(program_name.lower())
    if parent and parent.lower() != program_name.lower():
        return dst_root / parent / program_name
    return dst_root / program_name


# Programs that run on one PC only and are never published to the Scratch
# (Software) folder — SharePoint only. A helper exe follows its parent.
NOT_ON_SCRATCH = {"diagnostic"}


def kept_off_scratch(program_name: str) -> bool:
    name = program_name.lower()
    parent = (helper_parents().get(name) or program_name).lower()
    return name in NOT_ON_SCRATCH or parent in NOT_ON_SCRATCH


def roots_for(program_name: str, roots: list[Path]) -> list[Path]:
    """The destinations this program is copied to: all of them, minus the
    Scratch for the programs kept off it."""
    if not kept_off_scratch(program_name):
        return list(roots)
    scratch = scratch_root()
    if scratch is None:
        return list(roots)
    return [r for r in roots if Path(r) != scratch]


def helper_dir_names(program_name: str) -> set[str]:
    return {n for n, parent in helper_parents().items()
            if parent.lower() == program_name.lower()}


# ----------------- COPY MANAGER: STATE -----------------
STATE_SECTION = "deployed"


def load_state() -> dict[str, str]:
    p = cm_state_path()
    if not p.exists():
        return {}
    cfg = configparser.ConfigParser()
    try:
        cfg.read(p, encoding="utf-8")
    except Exception:
        return {}
    if not cfg.has_section(STATE_SECTION):
        return {}
    return {k: (v or "").strip() for k, v in cfg.items(STATE_SECTION)}


def save_state(state: dict[str, str]) -> None:
    cfg = configparser.ConfigParser()
    cfg[STATE_SECTION] = {k.lower(): v for k, v in state.items()}
    try:
        cm_state_path().parent.mkdir(parents=True, exist_ok=True)
        with open(cm_state_path(), "w", encoding="utf-8") as f:
            cfg.write(f)
    except Exception:
        pass


def last_deployed(name: str, versions_map: dict, state: dict) -> str:
    return (versions_map.get(name, "") or state.get(name.lower(), "")).strip()


# ----------------- COPY MANAGER: FILE HELPERS -----------------
def _dir_has_files(d: Path) -> bool:
    try:
        return any(f.is_file() for f in d.rglob("*"))
    except OSError:
        return False


def merge_dir(src: Path, dst: Path) -> tuple[int, int, list[str]]:
    """Copy src over dst file by file, WITHOUT deleting anything first.

    Deleting first lost data twice: an empty images/ in a build replaced a good
    one, and a half-failed copy left the folder wiped."""
    copied = failed = 0
    errors: list[str] = []
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if "__pycache__" in rel.parts:
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


def _move_legacy_toplevel_folder(old_dir: Path, new_dir: Path, log, flog):
    """Carry a helper's old top-level folder into its program's folder."""
    try:
        if not old_dir.exists() or old_dir.resolve() == new_dir.resolve():
            return
    except OSError:
        return
    try:
        if not new_dir.exists():
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_dir), str(new_dir))
            log(f"[{new_dir.name}] Moved into {new_dir.parent.name}\\ (was in the top folder)")
            flog("moved", str(old_dir), str(new_dir))
            return
        copied, failed, errors = merge_dir(old_dir, new_dir)
        log(f"[{new_dir.name}] Merged the old top-level folder in: {copied} file(s), {failed} failed")
        for e in errors[:5]:
            log(f"[{new_dir.name}]   {e}")
        if failed == 0:
            shutil.rmtree(old_dir, ignore_errors=True)
            if old_dir.exists():
                log(f"[{new_dir.name}] Old folder still in use — remove it by hand: {old_dir}")
            else:
                flog("removed", str(old_dir), "merged into " + str(new_dir))
        else:
            log(f"[{new_dir.name}] Old folder kept (some files could not be copied): {old_dir}")
    except Exception as e:
        log(f"[{new_dir.name}] Could not move the old folder {old_dir}: {e}")


def exe_version(p: Path) -> tuple[int, int, int]:
    m = VERSION_RE.search(p.stem)
    return tuple(map(int, m.groups())) if m else (0, 0, 0)


def find_exe_in_folder(version_folder: Path, program_name: str, version_name: str) -> Path:
    exes = sorted(version_folder.glob("*.exe")) or sorted(version_folder.rglob("*.exe"))
    if not exes:
        raise FileNotFoundError(f"No .exe found in: {version_folder}")
    ver = version_name.lstrip("v")
    exact = version_folder / f"{program_name} v{ver}.exe"
    if exact.exists():
        return exact
    token = f"v{ver}".lower()
    for e in exes:
        # a whole token, not the prefix of another version
        if re.search(r'(?<!\d)' + re.escape(token) + r'(?!\d)', e.stem.lower()):
            return e
    for e in exes:
        if e.stem.lower() == program_name.lower():
            return e
    return exes[0]


def unique_path(p: Path) -> Path:
    """'name (2).ext', 'name (3).ext', … when p exists."""
    if not p.exists():
        return p
    i = 2
    while True:
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _try_move(src: Path, dst: Path) -> tuple[bool, str | None]:
    def _locked(e):
        return getattr(e, "winerror", None) == 32
    try:
        src.rename(dst)
    except OSError:
        try:
            shutil.move(str(src), str(dst))
        except OSError as e:
            if _locked(e):
                return False, "locked"
            raise
    return True, None


def _normalize_name(s: str) -> str:
    return s.lower().replace("_", "").replace(" ", "")


def find_readme_or_raise(program_dir: Path, program_name: str) -> Path:
    target = _normalize_name(f"ReadMe_{program_name}")
    for f in program_dir.iterdir():
        if f.is_file() and _normalize_name(f.stem) == target:
            return f
    for cand in ("ReadMe.txt", "README.md", "README.txt", "ReadMe.md"):
        p = program_dir / cand
        if p.is_file():
            return p
    raise FileNotFoundError(f"[{program_name}] Missing ReadMe in {program_dir}")


def find_readme_full_or_none(program_dir: Path, program_name: str) -> Path | None:
    """The detailed document behind the Launcher's Details button.
    Mirrors Launcher/l.py::find_readme_full_or_none exactly."""
    targets = {_normalize_name(f"ReadMe_{program_name}_Full"),
               _normalize_name(f"ReadMe_{program_name}_Details"),
               _normalize_name(f"Manual_{program_name}")}
    try:
        for f in program_dir.iterdir():
            if f.is_file() and _normalize_name(f.stem) in targets:
                return f
    except OSError:
        return None
    return None


def _find_docs(search_dirs: list[Path], program_name: str) -> tuple[Path | None, Path | None]:
    readme = None
    for d in search_dirs:
        try:
            readme = find_readme_or_raise(d, program_name)
            break
        except (FileNotFoundError, OSError):
            pass
    full = None
    for d in search_dirs:
        full = find_readme_full_or_none(d, program_name)
        if full is not None:
            break
    return readme, full


# ----------------- ARCHIVE MAINTENANCE -----------------
def fix_archive_dir(archive_dir: Path, log: LogFn | None = None):
    """Move flat files in archive/ into vX.Y.Z/ subfolders; per version with
    timestamps keep only the latest build."""
    versioned_ts_re = re.compile(r"^.+\s+(v\d+\.\d+\.\d+)__(\d{8}_\d{6})\.", re.IGNORECASE)
    ts_only_re = re.compile(r"^.+__(\d{8}_\d{6})\.")

    flat = [f for f in archive_dir.iterdir()
            if f.is_file() and f.suffix.lower() not in (".txt", ".log")]
    if not flat:
        return
    ts_builds: dict = defaultdict(lambda: defaultdict(list))
    ver_only: dict = defaultdict(list)
    ts_to_ver: dict = {}
    unmatched = []
    for f in flat:
        m = versioned_ts_re.match(f.name)
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
    still_unmatched = []
    for f in unmatched:
        m = ts_only_re.match(f.name)
        if m and m.group(1) in ts_to_ver:
            ts_builds[ts_to_ver[m.group(1)]][m.group(1)].append(f)
        else:
            still_unmatched.append(f)
    if not ts_builds and not ver_only:
        return

    program = archive_dir.parent.name
    say = (lambda m: log(f"[{program}] {m}")) if log else (lambda m: None)
    say("Fixing archive...")
    changed = False

    def _move_into(files, folder):
        nonlocal changed
        folder.mkdir(exist_ok=True)
        for f in files:
            try:
                shutil.move(str(f), str(unique_path(folder / f.name)))
                say(f"  {f.name} -> {folder.name}/")
                changed = True
            except Exception as e:
                say(f"  WARN: {f.name}: {e}")

    for ver, ts_dict in sorted(ts_builds.items()):
        sorted_ts = sorted(ts_dict, reverse=True)
        _move_into(ts_dict[sorted_ts[0]], archive_dir / ver)
        for ts in sorted_ts[1:]:
            for f in ts_dict[ts]:
                try:
                    f.unlink()
                    say(f"  deleted older build: {f.name}")
                    changed = True
                except Exception as e:
                    say(f"  WARN delete: {f.name}: {e}")
    for ver, files in sorted(ver_only.items()):
        _move_into(files, archive_dir / ver)
    if still_unmatched:
        _move_into(still_unmatched, archive_dir / "unknown")
    if changed:
        say("  archive OK")


def reunite_unknown_helpers(archive_dir: Path, log: LogFn | None = None):
    """Move helper .py files stranded in archive/unknown/ back into their version
    folders, matched through the deploy timestamps in the archive logs."""
    unk = archive_dir / "unknown"
    unk_log = unk / "archive_log.txt"
    if not unk.is_dir() or not unk_log.exists():
        return
    program = archive_dir.parent.name
    say = (lambda m: log(f"[{program}] {m}")) if log else (lambda m: None)
    ts_re = re.compile(r"\d{8}_\d{6}")

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
                if not ts_re.fullmatch(ts):
                    continue
                if ts in ts_to_ver and ts_to_ver[ts] != vdir:
                    ambiguous.add(ts)
                else:
                    ts_to_ver[ts] = vdir
        except Exception:
            continue
    if not ts_to_ver:
        return

    per_name_ts: dict[str, list[str]] = defaultdict(list)
    try:
        for line in unk_log.read_text(encoding="utf-8").splitlines():
            parts = line.split("|", 1)
            if len(parts) == 2:
                ts, fname = parts[0].strip(), parts[1].strip()
                if ts_re.fullmatch(ts) and fname:
                    per_name_ts[fname].append(ts)
    except Exception:
        return

    moved = skipped = 0
    for fname, ts_list in per_name_ts.items():
        ts_list = sorted(ts_list)
        stem, suf = Path(fname).stem, Path(fname).suffix
        phys = [unk / (fname if i == 1 else f"{stem} ({i}){suf}")
                for i in range(1, len(ts_list) + 1)]
        existing = [p for p in phys if p.exists()]
        if len(existing) != len(ts_list):
            say(f"unknown: '{fname}' — {len(existing)} file(s) vs {len(ts_list)} log entries, skipping (ambiguous)")
            skipped += len(existing)
            continue
        for src, ts in zip(phys, ts_list):
            if ts in ambiguous or ts not in ts_to_ver:
                skipped += 1
                continue
            dst = ts_to_ver[ts] / fname
            if dst.exists():
                say(f"unknown: {src.name} -> {dst.parent.name}/ SKIP (exists)")
                skipped += 1
                continue
            ok, why = _try_move(src, dst)
            if ok:
                moved += 1
            else:
                say(f"unknown: {src.name} -> {dst.parent.name}/ SKIP ({why})")
                skipped += 1
    if moved:
        say(f"unknown: reunited {moved} helper file(s) with their version folders"
            + (f" ({skipped} skipped)" if skipped else ""))
    try:
        if not any(p.suffix.lower() == ".py" for p in unk.iterdir()):
            unk_log.unlink(missing_ok=True)
            unk.rmdir()
            say("unknown: emptied and removed")
    except Exception:
        pass


_DUP_SUFFIX_RE = re.compile(r"^(.*?) \((\d+)\)$")
_TS_SUFFIX_RE = re.compile(r"^(.*?)__\d{8}_\d{6}$")


def canonical_stem(stem: str) -> str:
    m = _TS_SUFFIX_RE.match(stem)
    if m:
        stem = m.group(1)
    m = _DUP_SUFFIX_RE.match(stem)
    return m.group(1) if m else stem


def _dup_index(stem: str) -> int:
    m = _DUP_SUFFIX_RE.match(stem)
    return int(m.group(2)) if m else 0


def normalize_version_folder_names(archive_dir: Path, log: LogFn | None = None):
    """Per version folder keep the best copy of each file under its canonical
    (importable) name and delete the redundant twins."""
    program = archive_dir.parent.name
    renamed = removed = 0
    for vdir in archive_dir.iterdir():
        if not vdir.is_dir() or vdir.name == "unknown":
            continue
        groups: dict[str, list[Path]] = defaultdict(list)
        for f in vdir.iterdir():
            if f.is_file() and f.suffix.lower() not in (".txt", ".log"):
                groups[canonical_stem(f.stem) + f.suffix].append(f)
        for canon, files in groups.items():
            keeper = min(files, key=lambda p: (_dup_index(p.stem),
                                               0 if p.name == canon else 1,
                                               p.name.lower()))
            for f in files:
                if f == keeper:
                    continue
                try:
                    f.unlink()
                    removed += 1
                    if log:
                        log(f"[{program}]   {vdir.name}/ removed duplicate: {f.name}")
                except Exception as e:
                    if log:
                        log(f"[{program}]   WARN remove {f.name}: {e}")
            if keeper.name != canon and not (vdir / canon).exists():
                ok, _ = _try_move(keeper, vdir / canon)
                if ok:
                    renamed += 1
    if log and (renamed or removed):
        log(f"[{program}] normalized {renamed} name(s), removed {removed} duplicate(s) in version folders")


def _archive_file(f: Path, ver_label: str, archive_dir: Path, timestamp: str) -> tuple[bool, Path]:
    ver_dir = archive_dir / ver_label
    ver_dir.mkdir(parents=True, exist_ok=True)
    dst = unique_path(ver_dir / f.name)
    moved, _ = _try_move(f, dst)
    if moved:
        try:
            with (ver_dir / "archive_log.txt").open("a", encoding="utf-8") as fh:
                fh.write(f"{timestamp} | {f.name}\n")
        except Exception:
            pass
    return moved, dst


def move_existing_exes_to_archive(target_dir: Path, keep_name: str, log: LogFn, program: str):
    """Move every *.exe except keep_name into archive/vX.Y.Z/; a locked one is skipped."""
    archive_dir = target_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for exe in target_dir.glob("*.exe"):
        if exe.name.lower() == keep_name.lower():
            continue
        vm = VERSION_RE.search(exe.stem)
        moved, dst = _archive_file(exe, vm.group(0) if vm else "unknown", archive_dir, timestamp)
        log(f"[{program}] Archived -> {dst}" if moved else f"[{program}] SKIP archive (locked) -> {exe}")


def fix_destinations(roots: list[Path], log: LogFn):
    """The Fix button: archives and icons of every program on every destination."""
    for dst_root in roots:
        if not dst_root.exists():
            log(f"Skipping (not accessible): {dst_root}")
            continue
        dirs = []
        for d in sorted(dst_root.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.lower() in ("archive", "dist"):
                continue
            dirs.append(d)
            # Helper folders inside a program folder need the same fix.
            for h in sorted(helper_dir_names(d.name)):
                for sub in d.iterdir():
                    if sub.is_dir() and sub.name.lower() == h:
                        dirs.append(sub)
        for program_dir in dirs:
            program = program_dir.name
            try:
                exes = sorted([p for p in program_dir.glob("*.exe")
                               if not TIMESTAMPED_EXE_RE.match(p.name)],
                              key=exe_version, reverse=True)
                if len(exes) > 1:
                    move_existing_exes_to_archive(program_dir, exes[0].name, log, program)
                archive_dir = program_dir / "archive"
                if archive_dir.exists():
                    fix_archive_dir(archive_dir, log)
                    reunite_unknown_helpers(archive_dir, log)
                    normalize_version_folder_names(archive_dir, log)

                dist_prog = dist_root() / program
                src_ico = None
                for vf in list_versions(dist_prog)[:1]:
                    if (vf / "icon.ico").exists():
                        src_ico = vf / "icon.ico"
                if src_ico is None and (dist_prog / "icon.ico").exists():
                    src_ico = dist_prog / "icon.ico"
                for png_name in ("icon.png", "Icon.png", "ICON.png"):
                    png = program_dir / png_name
                    if png.exists():
                        try:
                            png.unlink()
                            log(f"[{program}] Removed: {png_name}")
                        except Exception as e:
                            log(f"[{program}] Could not remove {png_name}: {e}")
                if src_ico is not None:
                    dst_ico = program_dir / "icon.ico"
                    if not dst_ico.exists() or dst_ico.stat().st_size != src_ico.stat().st_size:
                        try:
                            shutil.copy2(src_ico, dst_ico)
                            log(f"[{program}] icon.ico updated")
                        except Exception as e:
                            log(f"[{program}] FAILED icon.ico: {e}")
            except Exception as e:
                log(f"[{program}] ERROR (skipped): {e}")


# ----------------- DEPLOY -----------------
_ICON_NAMES = ("icon.ico", "Icon.ico", "icon.png", "Icon.png", "icon.gif", "Icon.gif")


def _find_icon(*folders: Path) -> Path | None:
    for folder in folders:
        for name in _ICON_NAMES:
            if (folder / name).exists():
                return folder / name
    return None


def scan_icon_conflicts(jobs: list[tuple[Path, str]], roots: list[Path]) -> list[tuple]:
    """The file part of the icon check — no UI, run it in a thread (share stats).

    Returns (program, source icon, conflicting destination icon or None,
    {dst_root: destination icon exists}) per program with an icon."""
    out = []
    for program_dir, version_name in jobs:
        if not version_name:
            continue
        icon_src = _find_icon(program_dir / version_name, program_dir)
        if icon_src is None:
            continue
        try:
            src_size = icon_src.stat().st_size
        except OSError:
            continue
        conflict = None
        dst_exists = {}
        for dst_root in roots_for(program_dir.name, roots):
            dst_icon = dest_dir_for(dst_root, program_dir.name) / icon_src.name
            try:
                size = dst_icon.stat().st_size
                dst_exists[dst_root] = True
            except OSError:
                dst_exists[dst_root] = False
                continue
            if conflict is None and size != src_size:
                conflict = dst_icon
        out.append((program_dir.name, icon_src, conflict, dst_exists))
    return out


def icon_decisions_for(name: str, dst_exists: dict, replace: bool | None) -> dict[str, bool]:
    """replace None = no conflict: copy where missing, skip where the same."""
    return {f"{name}|{root}": (replace if replace is not None else not exists)
            for root, exists in dst_exists.items()}


_FAIL_KEYS = ("exe_failed", "py_failed", "readme_failed", "extra_failed", "icon_failed")
ERROR_KEYS = _FAIL_KEYS + ("dest_unreachable", "program_failed")


def deploy_one_program(program_dir: Path, version_name: str, roots: list[Path],
                       helper_parent_dir: dict[str, Path] | None = None,
                       log: LogFn = _no_log,
                       icon_decisions: dict | None = None) -> tuple[dict, list[str]]:
    """Copy one built version to every destination. Returns (stats, file log)."""
    file_log: list[str] = []

    def flog(action: str, src: str, dst: str = ""):
        file_log.append(f"  [{action:8}]  {src}  →  {dst}" if dst else f"  [{action:8}]  {src}")

    stats = {k: 0 for k in (
        "exe_copied", "exe_failed", "py_copied", "py_failed", "readme_copied",
        "readme_failed", "extra_copied", "extra_failed", "icon_copied", "icon_failed",
        "icon_skipped", "archived_exe", "archived_py", "archive_skipped",
        "dest_ok", "dest_failed", "dest_unreachable")}
    program = program_dir.name
    if not version_name:
        raise FileNotFoundError(f"[{program}] NEW program: build it first (no versions in dist).")
    kept = roots_for(program, roots)
    if len(kept) != len(roots):
        log(f"[{program}] Scratch skipped — this program goes to SharePoint only")
    roots = kept
    version_folder = program_dir / version_name
    if not version_folder.exists():
        raise FileNotFoundError(f"Selected version folder missing: {version_folder}")
    src_exe = find_exe_in_folder(version_folder, program, version_name)

    # ReadMe lookup order: version folder, the repo source folder (the live one),
    # dist/<program>, a helper's parent folder, the copy already published.
    search = [version_folder, programs_root() / program, program_dir]
    parent = (helper_parent_dir or {}).get(program)
    if parent is not None:
        search.append(parent)
    search += [dest_dir_for(r, program) for r in roots]
    src_readme, src_readme_full = _find_docs(search, program)
    if src_readme is None:
        log(f"[{program}] WARNING: ReadMe not found — skipping ReadMe copy")

    src_py_files = list(version_folder.glob("*.py"))
    icon_src = _find_icon(version_folder, program_dir)

    src_extras = [i for i in version_folder.iterdir()
                  if i.name != "_internal"
                  and i not in (src_exe, src_readme, src_readme_full)
                  and not (i.is_file() and i.suffix.lower() in (".py", ".exe"))]

    # Asset folders a build did not carry come from the source tree.
    src_prog_dir = programs_root() / program
    for asset in ASSET_DIR_NAMES:
        if _dir_has_files(version_folder / asset) or not _dir_has_files(src_prog_dir / asset):
            continue
        src_extras = [i for i in src_extras if i.name.lower() != asset]
        src_extras.append(src_prog_dir / asset)
        log(f"[{program}] {asset}/ not in the build — taken from {src_prog_dir / asset}")

    # Folders the new version brings must survive the stale-folder cleanup.
    incoming_dirs = {i.name for i in src_extras if i.is_dir()}

    log(f"[{program}] Version: {version_name}")
    log(f"[{program}] EXE: {src_exe.name}")
    log(f"[{program}] ReadMe: {src_readme.name if src_readme else '(none — skipped)'}")
    log(f"[{program}] Details: {src_readme_full.name if src_readme_full else '(none)'}")
    log(f"[{program}] Extra files: {[x.name for x in src_extras]}")
    log(f"[{program}] PY files: {[x.name for x in src_py_files]}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for dst_root in roots:
        target_dir = dest_dir_for(dst_root, program)
        if target_dir.parent != dst_root:
            _move_legacy_toplevel_folder(dst_root / program, target_dir, log, flog)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            archive_dir = target_dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log(f"[{program}] SKIP — destination not accessible: {dst_root} ({e})")
            flog("skipped", str(dst_root), "not accessible")
            stats["dest_failed"] += 1
            stats["dest_unreachable"] += 1
            continue
        fix_archive_dir(archive_dir, log)
        log(f"[{program}] → {target_dir}")
        flog("dest", f"→ {target_dir}")
        fails_before = sum(stats[k] for k in _FAIL_KEYS)

        # Leftover folders from earlier deploys — never assets, shared data,
        # helper folders or what this version brings.
        keep_helpers = helper_dir_names(program)
        for item in target_dir.iterdir():
            if (item.is_dir() and item.name not in ("_internal", "archive")
                    and item.name.lower() not in ASSET_DIR_NAMES
                    and item.name.lower() not in SHARED_DATA_DIR_NAMES
                    and item.name.lower() not in keep_helpers
                    and item.name not in incoming_dirs):
                try:
                    shutil.rmtree(item)
                    log(f"[{program}] Removed stale folder: {item.name}")
                    flog("removed", item.name, str(target_dir))
                except Exception as e:
                    log(f"[{program}] Could not remove folder {item.name}: {e}")

        # The live version groups unversioned helper .py files into its folder.
        live_ver = None
        for cand in list(target_dir.glob(f"{program}*.py")) + list(target_dir.glob(f"{program}*.exe")):
            vm = VERSION_RE.search(cand.stem)
            if vm:
                live_ver = vm.group(0)
                break

        for exe in target_dir.glob(f"{program}*.exe"):
            vm = VERSION_RE.search(exe.stem)
            label = vm.group(0) if vm else "unknown"
            moved, dst = _archive_file(exe, label, archive_dir, timestamp)
            if moved:
                log(f"[{program}] Archived EXE: {exe.name} -> archive/{label}/")
                flog("archived", exe.name, str(dst.relative_to(dst_root)))
                stats["archived_exe"] += 1
            else:
                log(f"[{program}] SKIP archive (locked): {exe.name}")
                flog("skipped", exe.name, "locked")
                stats["archive_skipped"] += 1

        for pyf in target_dir.glob("*.py"):
            vm = VERSION_RE.search(pyf.stem)
            label = vm.group(0) if vm else (live_ver or "unknown")
            moved, dst = _archive_file(pyf, label, archive_dir, timestamp)
            if moved:
                log(f"[{program}] Archived PY: {pyf.name} -> archive/{label}/")
                flog("archived", pyf.name, str(dst.relative_to(dst_root)))
                stats["archived_py"] += 1
            else:
                log(f"[{program}] SKIP archive PY (locked): {pyf.name}")
                flog("skipped", pyf.name, "locked")
                stats["archive_skipped"] += 1

        dst_exe = target_dir / f"{program} {version_name}.exe"
        try:
            # Logged before the copy: if the log stops here, the copy hangs.
            log(f"[{program}] Copying EXE ({src_exe.stat().st_size // 1024} KB) → {dst_exe.name} …")
            shutil.copy2(src_exe, dst_exe)
            log(f"[{program}] Copied EXE -> {dst_exe.name}")
            flog("copied", src_exe.name, str(dst_exe.relative_to(dst_root)))
            stats["exe_copied"] += 1
        except Exception as e:
            log(f"[{program}] FAILED EXE: {e}")
            flog("failed", src_exe.name, str(e))
            stats["exe_failed"] += 1

        for pyf in src_py_files:
            dst_py = target_dir / pyf.name
            try:
                shutil.copy2(pyf, dst_py)
                log(f"[{program}] Copied PY: {pyf.name}")
                flog("copied", pyf.name, str(dst_py.relative_to(dst_root)))
                stats["py_copied"] += 1
            except Exception as e:
                log(f"[{program}] FAILED PY: {e}")
                flog("failed", pyf.name, str(e))
                stats["py_failed"] += 1

        for item in src_extras:
            dst_item = target_dir / item.name
            try:
                if item.is_dir():
                    copied, failed_n, errs = merge_dir(item, dst_item)
                    log(f"[{program}] Copied extra: {item.name}/ "
                        f"({copied} files{f', {failed_n} FAILED' if failed_n else ''})")
                    for e in errs[:5]:
                        log(f"[{program}]   FAILED in {item.name}/: {e}")
                    if failed_n:
                        flog("failed", f"{item.name}/", f"{failed_n} of {copied + failed_n} files")
                        stats["extra_failed"] += 1
                        continue
                else:
                    shutil.copy2(item, dst_item)
                    log(f"[{program}] Copied extra: {item.name}")
                flog("copied", item.name, str(dst_item.relative_to(dst_root)))
                stats["extra_copied"] += 1
            except Exception as e:
                log(f"[{program}] FAILED extra {item.name}: {e}")
                flog("failed", item.name, str(e))
                stats["extra_failed"] += 1

        for doc, label in ((src_readme, "ReadMe"), (src_readme_full, "Details")):
            if doc is None:
                continue
            try:
                dst_doc = target_dir / doc.name
                if dst_doc.exists():
                    log(f"[{program}] WARNING: {doc.name} exists, overwriting")
                shutil.copy2(doc, dst_doc)
                log(f"[{program}] {label} copied -> {dst_doc.name}")
                flog("copied", doc.name, str(dst_doc.relative_to(dst_root)))
                stats["readme_copied"] += 1
            except Exception as e:
                log(f"[{program}] FAILED {label}: {e}")
                flog("failed", doc.name, str(e))
                stats["readme_failed"] += 1

        if icon_src is not None:
            should_copy = (icon_decisions or {}).get(f"{program}|{dst_root}", True)
            if icon_src.suffix.lower() == ".ico":
                for stale_name in ("icon.png", "Icon.png", "ICON.png"):
                    stale = target_dir / stale_name
                    if stale.exists():
                        try:
                            stale.unlink()
                            log(f"[{program}] Removed stale PNG icon: {stale.name}")
                            flog("removed", stale_name, str(target_dir.relative_to(dst_root)))
                        except Exception as e:
                            log(f"[{program}] Could not remove PNG icon: {e}")
            if should_copy:
                try:
                    dst_icon = target_dir / icon_src.name
                    shutil.copy2(icon_src, dst_icon)
                    log(f"[{program}] Icon copied -> {dst_icon.name}")
                    flog("copied", icon_src.name, str(dst_icon.relative_to(dst_root)))
                    stats["icon_copied"] += 1
                except Exception as e:
                    log(f"[{program}] FAILED icon: {e}")
                    flog("failed", icon_src.name, str(e))
                    stats["icon_failed"] += 1
            else:
                log(f"[{program}] Icon skipped (kept existing) -> {dst_root}")
                flog("skipped", icon_src.name, f"kept existing in {dst_root.name}")
                stats["icon_skipped"] += 1

        if sum(stats[k] for k in _FAIL_KEYS) > fails_before:
            stats["dest_failed"] += 1
        else:
            stats["dest_ok"] += 1

    log(f"[{program}] DONE\n")
    return stats, file_log


def copy_readme_only(names: list[str], roots: list[Path],
                     helper_parent_dir: dict[str, Path], log: LogFn):
    dist = dist_root()
    src_root = programs_root()
    for name in names:
        program_dir = dist / name
        try:
            versions = list_versions(program_dir)
            search = versions[:1] + [src_root / name, program_dir]
            if name in helper_parent_dir:
                search.append(helper_parent_dir[name])
            search += [dest_dir_for(r, name) for r in roots]
            readme, full = _find_docs(search, name)
            if readme is None and full is None:
                log(f"[{name}] WARNING: no ReadMe found — skipped")
                continue
            if readme is None:
                log(f"[{name}] WARNING: short ReadMe not found")
            if full is None:
                log(f"[{name}] note: no Details doc")
            for dst_root in roots_for(name, roots):
                try:
                    reachable = dst_root.exists()
                except OSError:
                    reachable = False
                if not reachable:
                    log(f"[{name}] SKIP — not accessible: {dst_root}")
                    continue
                target_dir = dest_dir_for(dst_root, name)
                target_dir.mkdir(parents=True, exist_ok=True)
                for doc, label in ((readme, "ReadMe"), (full, "Details")):
                    if doc is not None:
                        shutil.copy2(doc, target_dir / doc.name)
                        log(f"[{name}] {label} copied -> {target_dir / doc.name}")
        except Exception as e:
            log(f"[{name}] ERROR: {e}")


# ----------------- DEPLOY LIBRARIES (_internal) -----------------
def latest_internal_folder() -> tuple[Path | None, str]:
    """(the newest _internal folder of the Internal Builder, error text)."""
    base = internal_builder_dist()
    if not base.exists():
        return None, f"_internal_builder dist not found:\n{base}"
    vers = sorted([d for d in base.iterdir() if d.is_dir() and VERSION_RE.match(d.name)],
                  key=lambda d: [int(x) for x in VERSION_RE.match(d.name).groups()],
                  reverse=True)
    latest = vers[0] if vers else base
    internal = latest / "_internal"
    if not internal.exists():
        return None, f"_internal folder not found in:\n{latest}"
    return internal, ""


def deploy_libraries(names: list[str], internal_src: Path, scratch: Path, log: LogFn,
                     progress: Callable[[int, int, str], None]) -> tuple[int, int]:
    """Zip _internal onto the scratch and unpack it into every selected program
    folder there. Unchanged files (same size, not .pyd) are left alone."""
    import zipfile
    zip_path = scratch / "_internal_builder.zip"
    ok = fail = 0

    # Hydrate OneDrive placeholders in one go before reading any file.
    try:
        subprocess.run(["attrib", "-U", "/s", "/d", str(internal_src)],
                       capture_output=True, timeout=120, creationflags=NO_WINDOW)
        log("OneDrive: hydration requested for _internal")
    except Exception as e:
        log(f"OneDrive hydration skipped: {e}")

    all_files = [f for f in internal_src.rglob("*") if f.is_file()]
    n_files = len(all_files)
    log(f"Files found in _internal: {n_files}")
    total = n_files + n_files * len(names)
    done = 0

    fd, tmp_str = tempfile.mkstemp(suffix=".zip", prefix="_internal_builder_")
    os.close(fd)
    tmp_zip = Path(tmp_str)
    log(f"Creating ZIP: {zip_path.name}")
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in all_files:
            zf.write(f, "_internal_builder/_internal/" + f.relative_to(internal_src).as_posix())
            done += 1
            progress(done, total, "ZIP")
    shutil.copy2(str(tmp_zip), str(zip_path))
    tmp_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zc:
        zcount = sum(1 for m in zc.infolist() if not m.filename.endswith("/"))
    log(f"ZIP created: {zip_path.name} — {zcount} files\n")
    total = zcount + zcount * len(names)
    done = zcount

    for name in names:
        dst_dir = scratch / name
        if kept_off_scratch(name):
            log(f"[{name}] SKIP — not published on the Scratch")
            done += n_files
            progress(done, total, "skipped")
            continue
        if not dst_dir.is_dir():
            log(f"[{name}] SKIP — folder not found: {dst_dir}")
            fail += 1
            done += n_files
            progress(done, total, "skipped")
            continue
        log(f"[{name}] → {dst_dir}")
        try:
            internal = dst_dir / "_internal"
            internal.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                members = [m for m in zf.infolist() if not m.filename.endswith("/")]
                prefix = ""
                for m in members:
                    idx = m.filename.find("_internal/")
                    if idx >= 0:
                        prefix = m.filename[:idx + len("_internal/")]
                        break
                expected: set[str] = set()
                to_write = []
                for m in members:
                    if not m.filename.startswith(prefix):
                        continue
                    rel = m.filename[len(prefix):]
                    if not rel:
                        continue
                    expected.add(rel)
                    dst_file = internal / rel.replace("/", os.sep)
                    # Never skip .pyd: same size does not mean the same binary.
                    if (dst_file.suffix.lower() != ".pyd" and dst_file.exists()
                            and dst_file.stat().st_size == m.file_size):
                        done += 1
                        progress(done, total, name)
                        continue
                    to_write.append((m, rel))
                removed = 0
                for existing in internal.rglob("*"):
                    if existing.is_file() and existing.relative_to(internal).as_posix() not in expected:
                        existing.unlink(missing_ok=True)
                        removed += 1
                for m, rel in to_write:
                    m.filename = rel
                    zf.extract(m, internal)
                    done += 1
                    progress(done, total, name)
            log(f"[{name}] OK — {len(to_write)} updated, "
                f"{len(expected) - len(to_write)} unchanged, {removed} removed")
            ok += 1
        except Exception as e:
            log(f"[{name}] ERROR: {e}")
            fail += 1
            done += n_files
            progress(done, total, "error")
    return ok, fail


# ----------------- REPORTS -----------------
def copy_summary(total_stats, per_program: list, n_jobs: int, roots: list,
                 elapsed: float, n_errors: int, detail_lines: list) -> str:
    """What went where, totals over ALL destinations."""
    dest_list = "\n".join(f"    {r}" for r in roots)
    unreachable = total_stats["dest_unreachable"]
    unreachable_note = (f"    Destinations unreachable: {unreachable}"
                        f"  (drive not mounted / network down)\n" if unreachable else "")
    return (
        f"\n{'=' * 40}\n"
        f"COPY DONE  {'⚠ ' + str(n_errors) + ' error(s) — see log above' if n_errors else '✓ No errors'}\n"
        f"  Time elapsed:      {fmt_elapsed(elapsed)}\n"
        f"  Programs copied:   {sum(1 for p in per_program if p['error'] is None)} of {n_jobs}\n"
        f"  Destinations:      {len(roots)}\n{dest_list}\n"
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
        "\nDetail:\n" + "\n".join(detail_lines) + f"\n{'=' * 40}")


def overall_summary(per_program: list, roots: list, copy_elapsed: float,
                    n_errors: int, build_info: dict | None = None) -> str:
    """The wrap-up for the whole job — build and copy together."""
    b_items = {i["name"].lower(): i for i in (build_info or {}).get("items", [])}
    b_failed = (build_info or {}).get("failed", [])
    b_elapsed = (build_info or {}).get("elapsed", 0.0) or 0.0
    did_build = build_info is not None
    total_errors = n_errors + len(b_failed)
    lines = ["", "=" * 40,
             f"ALL DONE  {'⚠ ' + str(total_errors) + ' error(s)' if total_errors else '✓ Everything went through'}",
             f"  Task:            {'build + copy' if did_build else 'copy only'}"]
    if did_build:
        lines.append(f"  Total time:      {fmt_elapsed(b_elapsed + copy_elapsed)}"
                     f"   (build {fmt_elapsed(b_elapsed)}  +  copy {fmt_elapsed(copy_elapsed)})")
    else:
        lines.append(f"  Total time:      {fmt_elapsed(copy_elapsed)}")
    lines += ["", "  Programs:"]
    for p in per_program:
        n_dest = len(roots_for(p["name"], roots))
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
    copied = {p["name"].lower() for p in per_program}
    for key, b in b_items.items():
        if key not in copied:
            lines.append(f"    ⚠  {b['name']}  {b['version']}")
            lines.append(f"         built in {b['seconds']:.1f}s"
                         f"{'  →  ' + b['out_dir'] if b.get('out_dir') else ''}")
            lines.append("         NOT copied — not selected in Copy Manager")
    for f in b_failed[:10]:
        lines.append(f"    ✗  build failed: {f}")
    lines += ["", "  Destinations:"] + [f"    {r}" for r in roots] + ["=" * 40]
    return "\n".join(lines)
