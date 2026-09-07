# b_t.py
import ast
import json
import sys as _sys
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tempfile
import shutil
import threading

# ----------------- BUILDER STORAGE (independent of root folder) -----------------
APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "builder_settings.json"
USAGE_LOG = APP_DIR / "build_usage.json"

# ----------------- USER CONFIG (shared with cm_t.py) -----------------
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

def _versions_txt_path() -> Path | None:
    cfg = _load_devtools_config()
    scratch = cfg.get("scratch")
    if scratch:
        return Path(scratch) / "Versions.txt"
    return None

# Versions.txt lives on the scratch share, where a single read costs tens of
# milliseconds. The project list asks for it once per row, so the answer is kept
# for a few seconds instead of being fetched dozens of times per redraw.
_VERSIONS_CACHE: dict = {"stamp": 0.0, "data": {}}
_VERSIONS_TTL = 15.0


def invalidate_versions_cache():
    _VERSIONS_CACHE["stamp"] = 0.0


def _as_out_dir(msg: str) -> str:
    """The build helpers return the output folder on success — except the Internal
    Builder, which returns a plain sentence. Keep only what is really a path, so
    the reports never print "Internal Builder build succeeded." as a location."""
    if not msg or "\n" in msg:
        return ""
    return msg if ("\\" in msg or "/" in msg) else ""


def read_versions_txt(force: bool = False) -> dict[str, str]:
    now = time.monotonic()
    if not force and _VERSIONS_CACHE["stamp"] and (now - _VERSIONS_CACHE["stamp"]) < _VERSIONS_TTL:
        return _VERSIONS_CACHE["data"]

    p = _versions_txt_path()
    if not p or not p.exists():
        _VERSIONS_CACHE["data"] = {}
        _VERSIONS_CACHE["stamp"] = now
        return {}
    result = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                name, _, ver = line.partition("=")
                result[name.strip()] = ver.strip()
    except Exception:
        pass
    _VERSIONS_CACHE["data"] = result
    _VERSIONS_CACHE["stamp"] = now
    return result

def write_version_to_txt(program_name: str, version: str):
    p = _versions_txt_path()
    if not p:
        return
    versions = read_versions_txt(force=True)
    versions[program_name] = f"v{version}" if not version.startswith("v") else version
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k} = {v}" for k, v in sorted(versions.items())]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"Warning: could not write Versions.txt: {e}")
    invalidate_versions_cache()

# ----------------- RELATED TOOLS -----------------
BUILDER_DIR = APP_DIR
# APP_DIR = L3-QoL-JanJan/Dev Tools/  →  parent = L3-QoL-JanJan/ (git source root)
#                                         parent.parent = programy/  (dist, archive, exe)
PROGRAMY_DIR = BUILDER_DIR.parent          # L3-QoL-JanJan/ — zdrojáky
PROGRAMY_DIST_DIR = BUILDER_DIR.parent.parent / "dist"  # programy/dist/ — exe výstupy

COPY_MANAGER_SRC_DIR = BUILDER_DIR.parent.parent / "Copy manager"
COPY_MANAGER_DIST_DIR = PROGRAMY_DIST_DIR / "Copy manager"

RE_VER = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$")
RE_VDIR = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$", re.IGNORECASE)

ALWAYS_IGNORE = {
    "dist", "Matlab", "Icons", ".vscode", ".venv", ".git",
}

def parse_version_tuple(ver: str):
    m = RE_VER.match(ver or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))


def version_tuple_to_str(t):
    return f"{t[0]}.{t[1]}.{t[2]}"


def bump_patch(ver: str) -> str:
    t = parse_version_tuple(ver)
    if not t:
        return "1.0.0"
    return f"{t[0]}.{t[1]}.{t[2] + 1}"


def exe_icon_count(exe: Path) -> int:
    """How many icon groups the built .exe carries in itself.

    This is what the Windows taskbar draws: a frozen build sets no
    AppUserModelID (see _icon_app_id() in every program), so Windows keys the
    taskbar button on the exe file and paints it from the exe's own icon
    resource. A build that came out without one shows the blank window
    placeholder instead, and nothing the running program does can fix it -
    which is exactly the bug that kept coming back. So every build checks it
    and says so in the log. Returns 0 when the file has no icon, and -1 when
    the count could not be taken at all.
    """
    try:
        import ctypes
        import ctypes.wintypes as _wt
        _ex = ctypes.windll.shell32.ExtractIconExW
        _ex.argtypes = [_wt.LPCWSTR, ctypes.c_int,
                        ctypes.POINTER(_wt.HICON), ctypes.POINTER(_wt.HICON),
                        ctypes.c_uint]
        _ex.restype = ctypes.c_uint
        # index -1 only counts the icon groups, it loads nothing
        return int(_ex(str(exe), -1, None, None, 0))
    except Exception:
        return -1


def run(cmd, cwd: Path):
    return subprocess.call(cmd, cwd=str(cwd), shell=True)


def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

class BuilderUI(ttk.Frame):
    def __init__(self, parent=None, on_build_done=None, log_widget=None):
        super().__init__(parent)
        self._on_build_done = on_build_done
        self._log_widget = log_widget

        self.usage = load_json(USAGE_LOG, {"events": []})

        default_root = APP_DIR.parent
        settings = load_json(SETTINGS_PATH, {})
        cfg = _load_devtools_config()
        root_str = cfg.get("root_folder") or settings.get("root_folder") or str(default_root)
        self.root_folder = Path(root_str)

        # Sync root_folder to %APPDATA% so CM reads the same value
        if not cfg.get("root_folder"):
            cfg["root_folder"] = str(self.root_folder)
            _save_devtools_config(cfg)

        # dist: explicit override in config, or derived as sibling of root_folder
        dist_override = cfg.get("dist_root")
        self.dist_root = Path(dist_override) if dist_override else self.root_folder.parent / "dist"

        self.selected_project: Path | None = None
        # A helper can be the focused program as well: it has its own version to
        # set and its own documentation, so the panel on the right has to be able
        # to show it. The parent stays in selected_project.
        self.selected_helper: dict | None = None
        self.projects_all: list[Path] = []
        self.projects_sorted: list[Path] = []

        self.project_checks: dict[str, tk.BooleanVar] = {}
        self.project_rows: dict[str, ttk.Frame] = {}
        self.project_next_override: dict[str, str] = {}
        self.project_next_labels: dict[str, tk.StringVar] = {}

        # Helper exes (build_config.json -> extra_exes): programs that live in a
        # project's folder but are started on their own, so they are ticked and
        # built on their own too.
        #
        # Their tick boxes are kept HERE and not rebuilt with the rows, unlike the
        # projects': a tick that vanished when the list was rebuilt would be a
        # tick the build silently ignores.
        self.helpers_by_parent: dict[str, list[dict]] = {}
        self.helper_checks: dict[str, tk.BooleanVar] = {}
        self.helper_next_labels: dict[str, tk.StringVar] = {}
        self.expanded_projects: set[str] = set()
        # Helper rows are built with their parent and only hidden while folded,
        # so opening a program is a show/hide and not a rebuild of the list.
        self.helper_rows: dict[str, list[ttk.Frame]] = {}
        self.expander_buttons: dict[str, ttk.Button] = {}
        # program name -> (row frame, name tick box, version label), for both
        # projects and helpers: the focus highlight works the same for both.
        self.row_widgets: dict[str, tuple] = {}

        # Both answers cost a folder scan or a share read, and every redraw asks
        # for them once per row; they only change when a build writes a version.
        self._last_version_cache: dict[str, tuple] = {}
        self._helpers_cache: dict[str, tuple] = {}

        self.root_var = tk.StringVar(value=str(self.root_folder))
        
        self.project_groups: dict[str, str] = settings.get("project_groups", {})
        self.show_ignored = tk.BooleanVar(value=False)
        self.group_var = tk.StringVar(value="main")
        self.name_var = tk.StringVar()
        self.main_var = tk.StringVar()
        self.last_ver_var = tk.StringVar()
        self.next_ver_var = tk.StringVar()
        self._next_var_trace_id = None
        self._local_log_autoscroll = True

        self._build_ui()
        self._bind_next_version_trace()
        self._reload_projects(select_first=True)

    # ----------------- DISCOVERY -----------------
    def _log(self, msg: str, force_scroll: bool = False):
        print(msg)
        if force_scroll:
            # A final report must end up in view wherever the user left the log.
            # Scrolling up during a build turns autoscroll off (and nothing turns
            # it back on until you scroll to the very bottom), which is exactly
            # how the end-of-run report used to disappear below the fold.
            self._local_log_autoscroll = True
            cm = getattr(self, "_cm_ref", None)
            if cm is not None:
                cm._log_autoscroll = True
        for w in [self._log_widget, getattr(self, "_local_log", None)]:
            if w is None:
                continue
            is_local = (w is getattr(self, "_local_log", None))
            def _append(w=w, is_local=is_local):
                try:
                    w.configure(state="normal")
                    w.insert("end", msg + "\n")
                    if is_local:
                        should_scroll = self._local_log_autoscroll
                    else:
                        # Sdílený CM log — použij jeho vlastní autoscroll flag pokud existuje
                        cm = getattr(self, "_cm_ref", None)
                        should_scroll = cm._log_autoscroll if cm is not None else (w.yview()[1] >= 0.95)
                    if should_scroll:
                        w.see("end")
                    w.configure(state="disabled")
                except Exception:
                    pass
            try:
                w.after(0, _append)
            except Exception:
                pass

    def _on_local_log_scroll(self, event=None):
        self.after(50, self._check_local_log_position)

    def _on_local_log_scrollbar_release(self, event=None):
        self.after(50, self._check_local_log_position)

    def _check_local_log_position(self):
        try:
            bottom = self._local_log.yview()[1]
            self._local_log_autoscroll = (bottom >= 0.95)
        except Exception:
            pass

    def _clear_local_log(self):
        try:
            self._local_log.configure(state="normal")
            self._local_log.delete("1.0", "end")
            self._local_log.configure(state="disabled")
        except Exception:
            pass

    def guess_main_py(self, project_dir: Path) -> Path | None:
        pys = [p for p in project_dir.glob("*.py") if p.name != "__init__.py"]
        if not pys:
            return None

        # Try exact folder-name match first, then slug variants (spaces→underscores, lowercase)
        slug = project_dir.name.replace(" ", "_").lower()
        for stem in (project_dir.name, slug, project_dir.name.replace(" ", "").lower()):
            cand = project_dir / f"{stem}.py"
            if cand.exists():
                return cand
        # Case-insensitive match against all .py stems
        for p in pys:
            if p.stem.replace(" ", "_").lower() == slug:
                return p

        for name in ("main.py", "app.py"):
            cand2 = project_dir / name
            if cand2.exists():
                return cand2

        if len(pys) == 1:
            return pys[0]

        pys.sort(key=lambda x: x.name.lower())
        return pys[0]

    def find_projects(self, root: Path) -> list[Path]:
        out = []
        if not root.exists():
            return out

        for p in root.iterdir():
            if not p.is_dir():
                continue
            if p.name.startswith("_"):
                continue
            if p.name.lower() in {n.lower() for n in ALWAYS_IGNORE}:
                continue
            if self.guess_main_py(p) is not None:
                out.append(p)
        return out

    def find_helpers(self, projects: list[Path]) -> dict[str, list[dict]]:
        """Helper exes per project, read from each project's build_config.json.

        Nothing has to be registered anywhere: a project has helpers exactly when
        its build_config.json lists them under `extra_exes`, so the expander in
        the project list appears by itself and cannot go stale.

        A helper builds into its OWN dist folder with its own version, because it
        can be built without its parent — and a version folder of the parent that
        held only the helper would be a version of the app that is not the app.
        """
        out: dict[str, list[dict]] = {}
        for p in projects:
            # Cached against the config's own timestamp: re-reading 16 configs on
            # every redraw is what made the list feel sticky, and an edited
            # config still shows up because its mtime moved.
            cfg_path = p / "build_config.json"
            try:
                stamp = cfg_path.stat().st_mtime_ns
            except OSError:
                stamp = 0
            cached = self._helpers_cache.get(p.name)
            if cached is not None and cached[0] == stamp:
                if cached[1]:
                    out[p.name] = cached[1]
                continue

            cfg = load_json(cfg_path, {})
            specs = cfg.get("extra_exes") or []
            items = []
            for spec in specs:
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
                    # generally reads the same data files it does (Diagnostic's
                    # notify_provision.dat), and in its own folder it has to
                    # bring its own copy.
                    "extra_files": list(spec.get("extra_files",
                                                 cfg.get("extra_files", []))),
                    "exclude_modules": list(cfg.get("exclude_modules", [])),
                })
            self._helpers_cache[p.name] = (stamp, items)
            if items:
                out[p.name] = items
        return out

    def invalidate_version_cache(self):
        """Drop the cached versions after anything that can change them."""
        self._last_version_cache.clear()
        invalidate_versions_cache()

    def helpers_of(self, project_name: str) -> list[dict]:
        return self.helpers_by_parent.get(project_name, [])

    def last_version_from_dist(self, project_name: str):
        cached = self._last_version_cache.get(project_name)
        if cached is not None:
            return cached

        result = self._read_last_version(project_name)
        self._last_version_cache[project_name] = result
        return result

    def _read_last_version(self, project_name: str):
        # Primary: read from Versions.txt on scratch
        versions = read_versions_txt()
        if project_name in versions:
            ver = versions[project_name].lstrip("v")
            if parse_version_tuple(ver):
                return ver, False

        # Fallback: scan dist folder
        base = self.dist_root / project_name
        if not base.exists():
            return None, True

        best = None
        try:
            for p in base.iterdir():
                if not p.is_dir():
                    continue
                m = RE_VDIR.match(p.name)
                if not m:
                    continue
                t = tuple(map(int, m.groups()))
                if best is None or t > best:
                    best = t
        except Exception:
            pass

        if best is None:
            return None, True

        return version_tuple_to_str(best), False

    def default_next_version_for_project(self, project_name: str) -> tuple[str, str | None, bool]:
        last_ver, is_new = self.last_version_from_dist(project_name)
        default_ver = "1.0.0" if is_new else bump_patch(last_ver)
        return default_ver, last_ver, is_new

    def effective_next_version_for_project(self, project_name: str) -> str:
        return self.project_next_override.get(project_name) or self.default_next_version_for_project(project_name)[0]

    def reset_next_version_overrides(self):
        self.project_next_override.clear()
        self._refresh_project_list_version_labels()
        if self.selected_project is not None:
            self._select_project(self.selected_project)

    def sort_projects(self, projects: list[Path]) -> list[Path]:
        return sorted(projects, key=lambda p: p.name.lower())

    # ----------------- UI -----------------
    def _init_row_styles(self):
        """The focused program has to be visible at a glance."""
        try:
            st = ttk.Style(self)
            base = st.lookup("TFrame", "background") or "#f0f0f0"
            st.configure("Focused.TFrame", background="#cfe0f5")
            st.configure("FocusedRow.TCheckbutton", background="#cfe0f5",
                         font=("Segoe UI", 9, "bold"))
            st.configure("Row.TCheckbutton", background=base,
                         font=("Segoe UI", 9))
            st.configure("Focused.TLabel", background="#cfe0f5")
            st.configure("Row.TLabel", background=base)
        except Exception:
            pass

    def _build_ui(self):
        self._init_row_styles()
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.LabelFrame(outer, text="Root folder")
        top.pack(fill="x")

        top_row = ttk.Frame(top)
        top_row.pack(fill="x", padx=10, pady=8)

        entry = ttk.Entry(top_row, textvariable=self.root_var, state="readonly")
        entry.pack(side="left", fill="x", expand=True)

        ttk.Button(top_row, text="Change…", command=self._change_root).pack(side="left", padx=(8, 0))
        ttk.Button(top_row, text="⚙ Set paths", command=self._open_set_paths).pack(side="left", padx=(8, 0))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.LabelFrame(body, text="Projects")
        left.pack(side="left", fill="both", expand=False)

        left_top = ttk.Frame(left)
        left_top.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Button(left_top, text="Select all", command=self._select_all_projects).pack(side="left")
        ttk.Button(left_top, text="Clear", command=self._clear_all_projects).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(left_top, text="Show ignored", variable=self.show_ignored,
                        command=self._render_project_buttons).pack(side="left", padx=(10, 0))

        canvas_wrap = ttk.Frame(left)
        canvas_wrap.pack(fill="both", expand=True, padx=8, pady=8)

        canvas = tk.Canvas(canvas_wrap, highlightthickness=0, width=440)
        vsb = ttk.Scrollbar(canvas_wrap, orient="vertical", command=canvas.yview)
        self.btn_frame = ttk.Frame(canvas)

        self.btn_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.btn_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        # Bound while the pointer is over the list, not on each widget: the rows
        # are full of buttons and labels, and a wheel turn over one of those used
        # to do nothing at all, which read as a frozen list.
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"

        def _wheel_on(event=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _wheel_off(event=None):
            canvas.unbind_all("<MouseWheel>")

        canvas_wrap.bind("<Enter>", _wheel_on)
        canvas_wrap.bind("<Leave>", _wheel_off)
        canvas.bind("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True, padx=(10, 0))

        cfg = ttk.LabelFrame(right, text="Selected / focused project")
        cfg.pack(fill="x")

        g = ttk.Frame(cfg)
        g.pack(fill="x", padx=10, pady=10)

        ttk.Label(g, text="Name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(g, textvariable=self.name_var, state="readonly", width=38).grid(row=0, column=1, sticky="w", padx=(8, 0))

        ttk.Label(g, text="MAINPY:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(g, textvariable=self.main_var, state="readonly", width=38).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(g, text="Last version:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(g, textvariable=self.last_ver_var, state="readonly", width=14).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(g, text="Next version:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(g, textvariable=self.next_ver_var, width=14).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(g, text="Group:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.group_combo = ttk.Combobox(g, textvariable=self.group_var,
                                        values=["Main project", "Side project", "Ignored project"],
                                        state="readonly", width=12)
        self.group_combo.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        self.group_combo.bind("<<ComboboxSelected>>", self._on_group_changed)

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(10, 0))

        self.btn_build = ttk.Button(actions, text="BUILD selected", command=self._build_selected)
        self.btn_build.pack(side="left")
        ttk.Button(actions, text="Open folder", command=self._open_dist).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Run Copy manager", command=self._run_copy_manager).pack(side="left", padx=(8, 0))
        self.copy_after_build_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(actions, text="Copy after build",
                        variable=self.copy_after_build_var).pack(side="left", padx=(16, 0))

        log_frame = ttk.LabelFrame(right, text="Log")
        log_frame.pack(fill="both", expand=True, pady=(0, 4))
        log_inner = ttk.Frame(log_frame)
        log_inner.pack(fill="both", expand=True, padx=6, pady=6)
        self._local_log = tk.Text(log_inner, height=8, wrap="none",
                                  font=("Consolas", 8), state="disabled")
        _lvsb = ttk.Scrollbar(log_inner, orient="vertical", command=self._local_log.yview)
        _lhsb = ttk.Scrollbar(log_inner, orient="horizontal", command=self._local_log.xview)
        self._local_log.configure(yscrollcommand=_lvsb.set, xscrollcommand=_lhsb.set)
        _lvsb.pack(side="right", fill="y")
        _lhsb.pack(side="bottom", fill="x")
        self._local_log.pack(side="left", fill="both", expand=True)
        self._local_log.bind("<MouseWheel>", self._on_local_log_scroll)
        _lvsb.bind("<ButtonRelease-1>", self._on_local_log_scrollbar_release)
        btn_row = ttk.Frame(log_frame)
        btn_row.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(btn_row, text="Clear", command=self._clear_local_log).pack(side="left")

    def _render_project_buttons(self):
        for child in self.btn_frame.winfo_children():
            child.destroy()
        # NOT cleared: a tick is a selection the user made, and rebuilding the
        # list (group change, Show ignored, refresh) must not throw it away.
        # Only ticks of projects that no longer exist are dropped.
        live_names = {p.name for p in self.projects_sorted}
        for name in [n for n in self.project_checks if n not in live_names]:
            self.project_checks.pop(name, None)
        self.project_rows.clear()
        self.project_next_labels.clear()
        self.helper_rows.clear()
        self.expander_buttons.clear()
        self.row_widgets.clear()

        _migrate = {
            "main": "Main project", "side": "Side project", "ignored": "Ignored project",
            "Main projects": "Main project", "Side projects": "Side project",
            "Ignored": "Ignored project",
        }
        for name, grp in list(self.project_groups.items()):
            if grp in _migrate:
                self.project_groups[name] = _migrate[grp]

        # Re-read here rather than at startup: editing a build_config.json must
        # show up on the next refresh, not on the next run of Dev Tools.
        self.helpers_by_parent = self.find_helpers(self.projects_sorted)

        groups = {"Main project": [], "Side project": [], "Ignored project": []}
        for p in self.projects_sorted:
            g = self.project_groups.get(p.name, "Main project")
            groups.get(g, groups["Main project"]).append(p)

        row_idx = 0
        show_ign = self.show_ignored.get()
        sections = [("Main projects", groups["Main project"]),
                    ("Side projects", groups["Side project"])]
        if show_ign:
            sections.append(("Ignored projects", groups["Ignored project"]))
        for section_title, projects in sections:
            if not projects:
                continue
            hdr = ttk.Label(self.btn_frame, text=section_title,
                            font=("Segoe UI", 9, "bold"))
            hdr.grid(row=row_idx, column=0, sticky="w", padx=4, pady=(8, 2))
            row_idx += 1

            for p in projects:
                row = ttk.Frame(self.btn_frame)
                row.grid(row=row_idx, column=0, sticky="ew", padx=4, pady=2)

                var = self.project_checks.get(p.name)
                if var is None:
                    var = tk.BooleanVar(value=False)
                    self.project_checks[p.name] = var
                self.project_rows[p.name] = row

                helpers = self.helpers_of(p.name)
                if helpers:
                    # Folded by default: most projects have no helpers and an
                    # always-open tree would push the projects themselves off the
                    # visible part of the list.
                    open_now = p.name in self.expanded_projects
                    btn = ttk.Button(row, text="▾" if open_now else "▸", width=2,
                                     command=lambda pn=p.name: self._toggle_expanded(pn))
                    btn.pack(side="left", padx=(0, 2))
                    self.expander_buttons[p.name] = btn
                else:
                    # Keeps every project's tick box on the same left edge, so a
                    # row with an expander does not read as indented.
                    ttk.Label(row, text=" ", width=3).pack(side="left")

                # Everything from the tick box rightwards lives in its own frame,
                # and that frame is what the focus highlight paints. The slot for
                # the arrow stays outside it: a highlight drawn across an empty
                # slot looks like a hole punched in it.
                band = ttk.Frame(row)
                band.pack(side="left", fill="both", expand=True)

                cb = ttk.Checkbutton(
                    band, text=p.name, variable=var, style="Row.TCheckbutton",
                    command=lambda pp=p: self._on_project_check_clicked(pp)
                )
                cb.pack(side="left", anchor="w")

                next_var = tk.StringVar(value=f"Next: {self.effective_next_version_for_project(p.name)}")
                self.project_next_labels[p.name] = next_var

                ver_lbl = ttk.Label(band, textvariable=next_var, width=18,
                                    style="Row.TLabel")
                ver_lbl.pack(side="right", padx=(8, 0))
                ttk.Button(band, text="ReadMe", width=8,
                           command=lambda pp=p: self._open_readme(pp)).pack(side="right", padx=(4, 0))
                ttk.Button(band, text="Details", width=7,
                           command=lambda pp=p: self._on_project_clicked(pp)).pack(side="right")

                # Clicking the row itself is the same as pressing Details, and on
                # a program that carries helpers it opens them: the helpers are
                # what the click is usually about.
                self.row_widgets[p.name] = (band, cb, ver_lbl)

                for w in (row, band, ver_lbl):
                    w.bind("<Button-1>", lambda e, pp=p: self._on_project_clicked(pp))

                row_idx += 1

                if helpers:
                    rows = []
                    for h in helpers:
                        rows.append(self._build_helper_row(h, row_idx))
                        row_idx += 1
                    self.helper_rows[p.name] = rows
                    if p.name not in self.expanded_projects:
                        for hrow in rows:
                            hrow.grid_remove()

        # A tick on a program that is no longer on the list (moved to Ignored
        # with Show ignored off) is dropped: what builds has to be what you see.
        for name, var in self.project_checks.items():
            if name not in self.project_rows and var.get():
                var.set(False)
                for h in self.helpers_of(name):
                    hv = self.helper_checks.get(h["name"])
                    if hv is not None:
                        hv.set(False)

        self.btn_frame.columnconfigure(0, weight=1)
        self._focused_row_name = None
        self._update_focus_styles()

    def _focused_name(self) -> str | None:
        """The program the panel on the right is showing — project or helper."""
        if self.selected_helper is not None:
            return self.selected_helper["name"]
        return self.selected_project.name if self.selected_project else None

    def _update_focus_styles(self):
        """Repaint only the row that lost focus and the one that gained it."""
        new_name = self._focused_name()
        old_name = getattr(self, "_focused_row_name", None)
        if old_name == new_name and old_name in self.row_widgets:
            return

        for name, focused in ((old_name, False), (new_name, True)):
            trio = self.row_widgets.get(name)
            if trio is None:
                continue
            row, cb, lbl = trio
            for w, styles in ((row, ("Focused.TFrame", "TFrame")),
                              (cb, ("FocusedRow.TCheckbutton", "Row.TCheckbutton")),
                              (lbl, ("Focused.TLabel", "Row.TLabel"))):
                try:
                    w.configure(style=styles[0] if focused else styles[1])
                except Exception:
                    pass

        self._focused_row_name = new_name

    def _build_helper_row(self, h: dict, row_idx: int) -> ttk.Frame:
        """One indented row for a helper exe, under its parent."""
        name = h["name"]
        row = ttk.Frame(self.btn_frame)
        row.grid(row=row_idx, column=0, sticky="ew", padx=(26, 4), pady=1)

        var = self.helper_checks.get(name)
        if var is None:
            var = tk.BooleanVar(value=False)
            self.helper_checks[name] = var

        cb = ttk.Checkbutton(row, text=f"↳ {name}", variable=var,
                             style="Row.TCheckbutton")
        cb.pack(side="left", anchor="w")

        next_var = tk.StringVar(value=f"Next: {self.effective_next_version_for_project(name)}")
        self.helper_next_labels[name] = next_var
        ver_lbl = ttk.Label(row, textvariable=next_var, width=18, style="Row.TLabel")
        ver_lbl.pack(side="right", padx=(8, 0))

        # The same two buttons a project has. A helper is built and deployed as a
        # program of its own, so it has its own documentation and its own version
        # to look at — the script name it used to show instead is in Details, as
        # MAINPY. Without these the sub-program looked like a program you are not
        # allowed to read anything about.
        ttk.Button(row, text="ReadMe", width=8,
                   command=lambda hh=h: self._open_helper_readme(hh)
                   ).pack(side="right", padx=(4, 0))
        ttk.Button(row, text="Details", width=7,
                   command=lambda hh=h: self._select_helper(hh)).pack(side="right")

        self.row_widgets[name] = (row, cb, ver_lbl)
        for w in (row, ver_lbl):
            w.bind("<Button-1>", lambda e, hh=h: self._select_helper(hh))
        return row

    def _toggle_expanded(self, project_name: str, open_it: bool | None = None):
        was_open = project_name in self.expanded_projects
        want_open = (not was_open) if open_it is None else bool(open_it)
        if want_open == was_open:
            return
        if want_open:
            self.expanded_projects.add(project_name)
        else:
            self.expanded_projects.discard(project_name)
        self._apply_expanded(project_name)

    def _apply_expanded(self, project_name: str):
        open_now = project_name in self.expanded_projects
        for hrow in self.helper_rows.get(project_name, []):
            try:
                hrow.grid() if open_now else hrow.grid_remove()
            except Exception:
                pass
        btn = self.expander_buttons.get(project_name)
        if btn is not None:
            try:
                btn.configure(text="▾" if open_now else "▸")
            except Exception:
                pass

    def _on_project_clicked(self, project_dir: Path):
        """Row / Details click: focus the program and open its helpers."""
        self._select_project(project_dir)
        if self.helpers_of(project_dir.name):
            self._toggle_expanded(project_dir.name, open_it=True)

    def _refresh_project_list_version_labels(self):
        for p in self.projects_sorted:
            var = self.project_next_labels.get(p.name)
            if var is not None:
                var.set(f"Next: {self.effective_next_version_for_project(p.name)}")
        for name, var in self.helper_next_labels.items():
            var.set(f"Next: {self.effective_next_version_for_project(name)}")
    # ----------------- ROOT CHANGE -----------------
    def _change_root(self):
        folder = filedialog.askdirectory(initialdir=str(self.root_folder))
        if not folder:
            return

        new_root = Path(folder)
        if not new_root.exists():
            messagebox.showerror("Invalid folder", f"Folder does not exist:\n{new_root}")
            return

        self.root_folder = new_root
        cfg = _load_devtools_config()
        dist_override = cfg.get("dist_root")
        self.dist_root = Path(dist_override) if dist_override else self.root_folder.parent / "dist"
        self.root_var.set(str(self.root_folder))

        # Save per-user to %APPDATA% (shared with CM); also keep builder_settings.json as fallback
        cfg = _load_devtools_config()
        cfg["root_folder"] = str(self.root_folder)
        _save_devtools_config(cfg)
        save_json(SETTINGS_PATH, {"root_folder": str(self.root_folder)})
        self._reload_projects(select_first=True)
        cm = getattr(self, "_cm_ref", None)
        if cm is not None:
            cm.programs_root_lbl.configure(text=str(self.root_folder))
            cm._load_programs()

    def _open_set_paths(self):
        from tkinter import filedialog, messagebox
        cfg = _load_devtools_config()

        win = tk.Toplevel(self)
        win.title("Set paths")
        win.resizable(False, False)
        win.grab_set()

        ttk.Label(win, text="Configure shared paths for Dev Tools (Builder + Copy Manager).",
                  padding=(12, 10)).pack()

        fields = [
            ("root_folder", "Programs folder",              "e.g. C:\\...\\Jan_a_Jan"),
            ("dist_root",   "Dist output folder",           "e.g. C:\\Dev\\dist  (leave empty = auto)"),
            ("scratch",     "Scratch (Software) folder",    "e.g. Z:\\Software"),
            ("sharepoint",  "Sharepoint (QoL) folder",      "e.g. C:\\...\\L3-HAPLS\\General\\QoL"),
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
            chosen = filedialog.askdirectory(title=f"Select folder", initialdir=cur)
            if chosen:
                var.set(chosen)

        def on_save():
            warnings = []
            for key, var in vars_.items():
                val = var.get().strip()
                if val and key != "dist_root" and Path(val).name.lower() == "dist":
                    warnings.append(f"  '{key}' path ends with 'dist' folder:\n  {val}\n  Should it be the parent folder?")
            if warnings:
                if not messagebox.askyesno(
                    "Suspicious path",
                    "Warning — these paths look wrong:\n\n" + "\n\n".join(warnings) +
                    "\n\nSave anyway?",
                    parent=win,
                ):
                    return
            for key, var in vars_.items():
                val = var.get().strip()
                if val:
                    cfg[key] = val
                else:
                    cfg.pop(key, None)
            _save_devtools_config(cfg)
            # Apply root_folder / dist_root changes immediately
            new_root_str = cfg.get("root_folder", "").strip()
            if new_root_str and Path(new_root_str).exists():
                self.root_folder = Path(new_root_str)
                self.root_var.set(str(self.root_folder))
                self._reload_projects(select_first=True)
            dist_override = cfg.get("dist_root", "").strip()
            self.dist_root = Path(dist_override) if dist_override else self.root_folder.parent / "dist"
            # Sync CM tab so destinations panel reflects the new paths immediately
            cm = getattr(self, "_cm_ref", None)
            if cm is not None:
                cm._build_dest_rows()
                cm._load_programs()
            win.destroy()
            messagebox.showinfo("Paths saved", "Paths saved to %APPDATA%\\DevTools\\config.json")

        ttk.Button(win, text="Save", command=on_save, padding=(16, 6)).pack(pady=(8, 12))

    def _reload_projects(self, select_first: bool):
        self.invalidate_version_cache()
        self.projects_all = self.find_projects(self.root_folder)
        if not self.projects_all:
            self.projects_sorted = []
            self._render_project_buttons()
            self.selected_project = None
            self.name_var.set("")
            self.main_var.set("")
            self.last_ver_var.set("")
            self.next_ver_var.set("")
            messagebox.showinfo("No projects found", f"No project folders with a usable .py found in:\n{self.root_folder}")
            return

        self.projects_sorted = self.sort_projects(self.projects_all)
        self._render_project_buttons()

        if select_first:
            self._select_project(self.projects_sorted[0])

    # ----------------- SELECTION -----------------
    def _select_project(self, project_dir: Path):
        self.selected_project = project_dir
        self.selected_helper = None
        try:
            self.group_combo.configure(state="readonly")
        except Exception:
            pass

        name = project_dir.name
        main_path = self.guess_main_py(project_dir)
        main = main_path.name if main_path else ""

        default_next, last, is_new = self.default_next_version_for_project(project_dir.name)
        effective_next = self.project_next_override.get(project_dir.name, default_next)

        self.name_var.set(name)
        self.main_var.set(main)

        if is_new:
            self.last_ver_var.set("— NEW —")
        else:
            self.last_ver_var.set(last)

        self.next_ver_var.set(effective_next)

        self.group_var.set(self.project_groups.get(project_dir.name, "Main project"))
        self._update_focus_styles()
        self._refresh_project_list_version_labels()

    def _select_helper(self, h: dict):
        """Show a helper in the panel on the right, like a project.

        Its version is its own (`effective_next_version_for_project` is keyed by
        program name, and a helper's name is its own dist folder), so the Next
        version field edits the helper's version and nothing else. Group is left
        empty and disabled: a helper is not sorted into the project sections, it
        follows its parent.
        """
        self.selected_helper = h
        self.selected_project = h["parent"]

        default_next, last, is_new = self.default_next_version_for_project(h["name"])
        effective_next = self.project_next_override.get(h["name"], default_next)

        self.name_var.set(h["name"])
        self.main_var.set(h["script"].name)
        self.last_ver_var.set("— NEW —" if is_new else last)
        self.next_ver_var.set(effective_next)
        self.group_var.set("")
        try:
            self.group_combo.configure(state="disabled")
        except Exception:
            pass

        self._update_focus_styles()
        self._refresh_project_list_version_labels()

    def _open_helper_readme(self, h: dict):
        """A helper's own documentation, which lives in its parent's folder.

        Named after the helper (`ReadMe_<helper name>.txt`) and not after the
        folder it sits in: that is the name the Launcher looks for, because the
        helper is deployed as a program of its own.
        """
        parent = h["parent"]
        name = h["name"]
        norm = lambda s: s.lower().replace("_", "").replace(" ", "")
        target = norm(f"ReadMe_{name}")

        readme = next((f for f in parent.iterdir()
                       if f.is_file() and norm(f.stem) == target), None)
        if readme is None:
            if not messagebox.askyesno(
                "ReadMe not found",
                f"{name} has no ReadMe of its own in:\n{parent}\n\n"
                f"Create ReadMe_{name}.txt?"
            ):
                return
            readme = parent / f"ReadMe_{name}.txt"
            readme.write_text("", encoding="utf-8-sig")

        os.startfile(str(readme))

    def _on_group_changed(self, event=None):
        # A helper has no group of its own; the combo is disabled while one is
        # focused, and this guard keeps a stray event from moving its parent.
        if self.selected_helper is not None:
            return
        if self.selected_project is None:
            return
        name = self.selected_project.name
        new_group = self.group_var.get()
        if new_group == "Main project":
            self.project_groups.pop(name, None)
        else:
            self.project_groups[name] = new_group
        settings = load_json(SETTINGS_PATH, {})
        settings["project_groups"] = self.project_groups
        save_json(SETTINGS_PATH, settings)
        self._render_project_buttons()

    def _on_project_check_clicked(self, project_dir: Path):
        # Ticking a program takes its helpers with it — that is the usual case,
        # a released version where the app and its helper match. It is a one-way
        # push, not a lock: untick a helper afterwards and it stays unticked,
        # which is how you rebuild only the app.
        var = self.project_checks.get(project_dir.name)
        helpers = self.helpers_of(project_dir.name)
        if var is not None:
            for h in helpers:
                hv = self.helper_checks.get(h["name"])
                if hv is not None:
                    hv.set(var.get())
            # Ticking shows what it did to the helpers instead of leaving it
            # folded away out of sight. Unticking does NOT fold again — closing
            # the list is the arrow's job, and a row that disappears under your
            # cursor looks like the tick did something it did not.
            if helpers and var.get():
                self._toggle_expanded(project_dir.name, open_it=True)
        self._select_project(project_dir)

    def _bind_next_version_trace(self):
        try:
            if self._next_var_trace_id is not None:
                self.next_ver_var.trace_remove("write", self._next_var_trace_id)
        except Exception:
            pass

        self._next_var_trace_id = self.next_ver_var.trace_add("write", self._on_next_version_edited)

    def _on_next_version_edited(self, *args):
        name = self._focused_name()
        if name is None:
            return

        val = self.next_ver_var.get()          # bez .strip() — nemazat mezery při psaní

        default_next = self.default_next_version_for_project(name)[0]

        if not val.strip():
            self.project_next_override.pop(name, None)
        elif val.strip() == default_next:
            self.project_next_override.pop(name, None)
        else:
            self.project_next_override[name] = val.strip()

        self._refresh_project_list_version_labels()

    def _select_all_projects(self):
        for var in self.project_checks.values():
            var.set(True)
        for var in self.helper_checks.values():
            var.set(True)

    def _clear_all_projects(self):
        for var in self.project_checks.values():
            var.set(False)
        for var in self.helper_checks.values():
            var.set(False)

    def _get_checked_projects(self) -> list[Path]:
        out = []
        by_name = {p.name: p for p in self.projects_sorted}
        for name, var in self.project_checks.items():
            if var.get() and name in by_name:
                out.append(by_name[name])
        return out

    def _get_checked_helpers(self) -> list[dict]:
        """Ticked helper exes, in project order. Read from helper_checks rather
        than from the rows, so a helper ticked and then folded away still counts."""
        out = []
        for p in self.projects_sorted:
            for h in self.helpers_of(p.name):
                var = self.helper_checks.get(h["name"])
                if var is not None and var.get():
                    out.append(h)
        return out

    # ----------------- BUILD CORE -----------------
    def _build_internal_builder_sync(self, live_log=None) -> tuple[bool, str]:
        """Synchronous build of _internal_builder — uses .spec if available, else __main__ block."""
        import subprocess as _sp
        builder_script = self.root_folder / "Internal Builder" / "_internal_builder.py"
        if not builder_script.exists():
            return False, f"Builder script not found:\n{builder_script}"
        spec_file = builder_script.parent / "_internal_builder.spec"
        if spec_file.exists():
            cmd = [_sys.executable, "-m", "PyInstaller", "--noconfirm",
                   "--distpath", r"C:\Dev\dist", str(spec_file)]
        else:
            cmd = [_sys.executable, str(builder_script)]
        if live_log:
            live_log(f"CMD: {' '.join(cmd)}\n")
        try:
            proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                             cwd=str(builder_script.parent))
            for line in proc.stdout:
                line = line.rstrip()
                if line and live_log:
                    live_log(line)
            proc.wait()
            if proc.returncode == 0:
                return True, "Internal Builder build succeeded."
            return False, f"Internal Builder build failed (rc={proc.returncode})"
        except Exception as e:
            return False, f"ERROR: {e}"

    # Folders and files that are never a program module: build output, caches,
    # dev-only helpers.
    _MODULE_SCAN_SKIP_DIRS = {"_internal", "__pycache__", ".git", "dist", "build",
                              "archive", "testing", "tests", "DataRepository"}

    def _module_homes(self) -> dict[str, list[Path]]:
        """Map module name -> every program folder that has a .py with that name.

        Only the top level of each program folder is scanned, because that is
        exactly what PyInstaller sees: the builder passes the program folder as
        the only --paths entry."""
        homes: dict[str, list[Path]] = {}
        try:
            folders = [d for d in self.root_folder.iterdir()
                       if d.is_dir() and d.name not in self._MODULE_SCAN_SKIP_DIRS
                       and not d.name.startswith(".")]
        except Exception:
            return homes
        for d in folders:
            for f in d.glob("*.py"):
                if f.name.startswith(("test_", "_verify")):
                    continue
                homes.setdefault(f.stem, []).append(f)
        return homes

    def _check_module_homes(self, p: Path, py_files: list[Path],
                            live_log=None) -> tuple[bool, str]:
        """Refuse to build when a module the program imports is not in its folder.

        The build only ever sees the program's own folder, so a module reached
        at runtime through a sys.path detour (or a second copy kept in another
        program folder) produces a build that silently runs *different* code
        than the source tree. That happened to the Spectra tab: sp_t.py was
        developed in Spectra/ while a five-week-old copy in CSS Logger/ was the
        one bundled, with no error anywhere. See INFRASTRUCTURE.md §7."""
        homes = self._module_homes()
        if not homes:
            return True, ""
        # Real imports only — parsed, not grepped. A prose line inside a
        # docstring ("from a build under C:\\Dev\\dist") matches the regex the
        # --collect-all detection uses and would fail the build for nothing.
        imported: set[str] = set()
        for f in py_files:
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])
        own = {f.stem for f in py_files}
        problems: list[str] = []
        for mod in sorted(imported):
            where = homes.get(mod)
            if not where:
                continue                      # third-party or stdlib
            elsewhere = [f for f in where if f.parent != p]
            if mod not in own:
                problems.append(
                    f"  '{mod}' is imported but there is no {mod}.py in this folder.\n"
                    f"    It lives in: " + ", ".join(str(f.parent.name) for f in elsewhere) +
                    f"\n    Move {mod}.py into '{p.name}' - the build cannot reach it "
                    "anywhere else."
                )
            elif elsewhere:
                mine = p / f"{mod}.py"
                lines = [f"  '{mod}.py' exists in more than one folder - the build "
                         f"uses the one in '{p.name}':"]
                for f in [mine] + elsewhere:
                    try:
                        stamp = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                        size = f.stat().st_size
                    except Exception:
                        stamp, size = "?", 0
                    lines.append(f"    {f}  ({size} B, {stamp})")
                lines.append("    Keep one copy only, or the build will drift from "
                             "what you edit.")
                problems.append("\n".join(lines))
        if not problems:
            return True, ""
        msg = ("Module home check failed for '" + p.name + "':\n"
               + "\n".join(problems))
        if live_log:
            for line in msg.splitlines():
                live_log(line)
        return False, msg

    def _build_one_project(self, p: Path, ver: str, live_log=None) -> tuple[bool, str]:
        """Postaví projekt pomocí PyInstalleru do dist/<projekt>/vX.X.X/."""
        name = p.name

        main_path = self.guess_main_py(p)
        if not main_path:
            return False, f"No .py entry found in:\n{p}"

        # Výstupní složka pro tuto verzi
        verdir = self.dist_root / p.name / f"v{ver}"
        if not self.dist_root.exists():
            import tkinter.messagebox as _mb
            ans = _mb.askyesnocancel(
                "dist folder missing",
                f"Output folder does not exist:\n{self.dist_root}\n\n"
                "Create it now?\n\n"
                "Yes = create and continue\n"
                "No = choose a different folder\n"
                "Cancel = abort build",
                parent=self,
            )
            if ans is None:
                return False, f"Build aborted — dist folder not found:\n{self.dist_root}"
            elif not ans:
                chosen = filedialog.askdirectory(
                    title="Select output (dist) folder",
                    initialdir=str(self.dist_root.parent),
                )
                if not chosen:
                    return False, "Build aborted — no output folder selected."
                self.dist_root = Path(chosen)
                verdir = self.dist_root / p.name / f"v{ver}"
        # Smaž existující verdir před buildem — PyInstaller --noconfirm selže na read-only
        # souborech (OneDrive je po sync označí jako read-only). Odstraníme je sami.
        if verdir.exists():
            def _on_rm_error(func, path, exc_info):
                import stat as _stat
                try:
                    os.chmod(path, _stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(str(verdir), onerror=_on_rm_error)
        verdir.mkdir(parents=True, exist_ok=True)

        # Dočasné složky pro PyInstaller
        workdir = Path(tempfile.gettempdir()) / "universal_builder_pyinstaller"
        specdir = workdir / "spec"
        builddir = workdir / "build"
        specdir.mkdir(parents=True, exist_ok=True)

        icon_path = p.resolve() / "icon.ico"
        icon_args = ["--icon", str(icon_path)] if icon_path.exists() else []

        # Test scripts are dev-only — nothing imports them at runtime, so keep them
        # out of the bundle.
        extra_py_files = [
            x.resolve() for x in p.glob("*.py")
            if x.name != main_path.name and not x.name.startswith("test_")
        ]

        # Every module must live in this folder — a copy elsewhere, or a module
        # reached through sys.path, builds code that is not what you edited.
        ok, why = self._check_module_homes(p, [main_path.resolve()] + extra_py_files,
                                          live_log)
        if not ok:
            return False, why

        build_cfg = load_json(p / "build_config.json", {})
        extra_collect_all: list[str]     = list(build_cfg.get("collect_all", []))
        extra_collect_bins: list[str]    = build_cfg.get("collect_binaries", [])
        extra_hidden_imports: list[str]  = build_cfg.get("hidden_imports", [])
        extra_copy_metadata: list[str]   = build_cfg.get("copy_metadata", [])
        extra_exclude_modules: list[str] = list(build_cfg.get("exclude_modules", []))
        # Extra data files/folders to copy into the version folder after build
        extra_data_files: list[str] = list(build_cfg.get("extra_files", []))

        # Runtime asset folders: the app reads them from next to the exe, so they
        # must ship with every build. Auto-included even when the project has no
        # build_config.json — a missing asset folder is invisible until the app is
        # run from the deployed copy (Announcer/images/scorpion_orig.png).
        # Keep this list in sync with ASSET_DIR_NAMES in cm_t.py — the deploy uses
        # the same names to protect the folders on the destination and to fall back
        # to the source tree when a build did not bring them.
        for _asset in ("images", "sounds", "assets", "icons", "img", "audio",
                       "fonts", "templates"):
            if (p / _asset).is_dir() and _asset not in extra_data_files:
                extra_data_files.append(_asset)

        # Auto-detect packages with known DLL bundling issues and add --collect-all
        # so PyInstaller always includes all native libraries (e.g. numpy _umath_linalg).
        _AUTO_COLLECT = {
            "numpy":      "numpy",
            "scipy":      "scipy",
            "sklearn":    "sklearn",
            "cv2":        "cv2",
            "matplotlib": "matplotlib",
            "pandas":     "pandas",
        }
        _import_re = re.compile(r'^\s*(?:import|from)\s+([\w]+)', re.MULTILINE)
        _detected: set[str] = set()
        for _f in [main_path] + extra_py_files:
            try:
                _detected.update(_import_re.findall(_f.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                pass
        for _imp, _pkg in _AUTO_COLLECT.items():
            if _imp in _detected and _pkg not in extra_collect_all:
                extra_collect_all.append(_pkg)

        # --collect-all also drags in the packages' own test suites. Those cannot be
        # imported without pytest / test data, so PyInstaller only prints warnings and
        # skips them — but when they *are* importable they bloat the dist. Exclude them
        # explicitly. Note: only the ".tests" packages, never ".testing"/"._testing",
        # which are public helpers some libraries use at runtime.
        _TEST_SUBMODULES = {
            "numpy":      ["numpy.tests", "numpy.f2py.tests", "numpy.random.tests",
                           "numpy.linalg.tests", "numpy.fft.tests", "numpy.ma.tests",
                           "numpy.lib.tests", "numpy.core.tests", "numpy.typing.tests"],
            "scipy":      ["scipy.tests"],
            "sklearn":    ["sklearn.tests"],
            "cv2":        [],
            "matplotlib": ["matplotlib.tests"],
            "pandas":     ["pandas.tests"],
        }
        for _pkg in extra_collect_all:
            for _mod in _TEST_SUBMODULES.get(_pkg, []):
                if _mod not in extra_exclude_modules:
                    extra_exclude_modules.append(_mod)

        args = [
            "py", "-m", "PyInstaller",
            "--onedir", "--windowed", "--noconfirm",
            "--name", name,
        ] + icon_args + [
            "--distpath", str(verdir),
            "--workpath", str(builddir),
            "--specpath", str(specdir),
            "--paths", str(p),
            "--hidden-import", "concurrent.futures",
            "--hidden-import", "concurrent",
            "--hidden-import", "zoneinfo",
            "--hidden-import", "zoneinfo._tzdata",
        ]
        for pkg in extra_collect_all:
            args += ["--collect-all", pkg]
        for pkg in extra_collect_bins:
            args += ["--collect-binaries", pkg]
        for imp in extra_hidden_imports:
            args += ["--hidden-import", imp]
        for pkg in extra_copy_metadata:
            args += ["--copy-metadata", pkg]
        for mod in extra_exclude_modules:
            args += ["--exclude-module", mod]
        for extra in extra_py_files:
            args += ["--add-data", f"{extra};."]

        args.append(main_path.name)

        def _log(msg: str):
            print(msg)
            if live_log:
                live_log(msg)

        _log(f"BUILD cwd: {p}")
        _log(f"CMD: {' '.join(args)}\n")

        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(p),
        )
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        rc = proc.returncode

        if rc != 0:
            return False, f"PyInstaller failed with code {rc}\n\n{' '.join(args)}"

        # Po buildu: přesun pouze obsahu vnitřní složky (MyApp/) o úroveň výš
        # _internal zůstane na místě — je sdílená
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
                    print(f"Warning: could not move {item}: {e}")
            try:
                inner_dir.rmdir()
            except Exception:
                pass
            # nesnaž se složku mazat, jen ji nech prázdnou — OneDrive si poradí

        # Přijmenování .exe na styl "Název vX.Y.Z.exe"
        exe_src = verdir / f"{name}.exe"
        exe_dst = verdir / f"{name} v{ver}.exe"
        if exe_src.exists():
            try:
                exe_src.rename(exe_dst)
            except Exception as e:
                print(f"Warning: could not rename exe: {e}")

        # Kopíruj icon.ico vedle exe (tkinter iconbitmap ho potřebuje jako fyzický soubor)
        _ico_src = p / "icon.ico"
        if _ico_src.exists():
            try:
                shutil.copy2(str(_ico_src), str(verdir / "icon.ico"))
            except Exception:
                pass

        # The taskbar picture comes from the exe itself - see exe_icon_count().
        if exe_dst.exists():
            _n = exe_icon_count(exe_dst)
            if _n > 0:
                print(f"Icon: the exe carries its own icon ({_n} group(s)) — "
                      f"its taskbar button will show it")
            elif _n == 0:
                print("Warning: the built exe carries NO icon of its own, so "
                      "its taskbar button will be a blank window. Check that "
                      "icon.ico sits in the program's folder and that "
                      "PyInstaller got --icon.")
            else:
                print("Warning: could not check whether the exe carries an icon")

        # Kopíruj main .py a ostatní zdrojáky
        try:
            py_dst = verdir / f"{name} v{ver}.py"
            py_dst.write_bytes(main_path.read_bytes())
            for extra in extra_py_files:
                edst = verdir / extra.name
                edst.write_bytes(extra.read_bytes())
        except Exception as e:
            print(f"Warning: could not copy source files: {e}")

        # Kopíruj extra datové soubory a složky (build_config.json → extra_files
        # plus the auto-detected asset folders)
        for fname in extra_data_files:
            src = p / fname
            dst = verdir / src.name
            if src.is_dir():
                try:
                    if dst.exists():
                        shutil.rmtree(str(dst), ignore_errors=True)
                    shutil.copytree(str(src), str(dst),
                                    ignore=shutil.ignore_patterns("__pycache__", "Thumbs.db"))
                    _n = sum(1 for _f in dst.rglob("*") if _f.is_file())
                    _log(f"  extra folder: {src.name}/  ({_n} files)")
                except Exception as e:
                    _log(f"Warning: could not copy extra folder {fname}: {e}")
            elif src.exists():
                try:
                    shutil.copy2(str(src), str(dst))
                    _log(f"  extra file: {src.name}")
                except Exception as e:
                    _log(f"Warning: could not copy extra file {fname}: {e}")
            else:
                _log(f"Warning: extra_file not found: {src}")

        # Kopíruj ReadMe do version folder. Deploy (cm_t.py) hledá ReadMe nejdřív
        # tady a jinak sáhne po té, co už leží na cíli — bez tohohle kroku by se
        # zveřejněná ReadMe nikdy neaktualizovala.
        #
        # Programy mají DVĚ uživatelské dokumentace a Launcher na každou má vlastní
        # tlačítko: ReadMe_<jméno> (krátká, "ReadMe") a ReadMe_<jméno>_Full
        # (podrobná, "Details"). Musí se kopírovat obě — kdyby se kopírovala jen
        # krátká, tlačítko Details by na sdíleném disku nemělo co otevřít.
        _rm_norm = lambda s: s.lower().replace("_", "").replace(" ", "").replace("-", "").replace(".", "")
        _rm_short = {_rm_norm(f"ReadMe_{p.name}")}
        _rm_full = {_rm_norm(f"ReadMe_{p.name}_Full"),
                    _rm_norm(f"ReadMe_{p.name}_Details"),
                    _rm_norm(f"Manual_{p.name}")}

        def _find_doc(targets):
            return next((f for f in p.iterdir()
                         if f.is_file() and _rm_norm(f.stem) in targets), None)

        _readme = _find_doc(_rm_short)
        if _readme is None:
            _readme = next((p / c for c in ("ReadMe.txt", "README.md", "README.txt", "ReadMe.md")
                            if (p / c).exists()), None)
        _readme_full = _find_doc(_rm_full)

        if _readme is None:
            _log(f"Warning: no ReadMe found in {p} — deploy will keep the published one")
        if _readme_full is None:
            _log(f"Note: no ReadMe_{p.name}_Full — the Launcher will show no Details button")

        for _doc, _what in ((_readme, "readme"), (_readme_full, "readme (details)")):
            if _doc is None:
                continue
            try:
                shutil.copy2(str(_doc), str(verdir / _doc.name))
                _log(f"  {_what}: {_doc.name}")
            except Exception as e:
                _log(f"Warning: could not copy {_doc.name}: {e}")

        # Vyčisti pouze pracovní TEMP dir
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

        # Zapiš novou verzi do Versions.txt
        write_version_to_txt(name, ver)

        # Logování
        self.usage.setdefault("events", []).append({"ts": time.time(), "project": p.name})
        self.usage["events"] = self.usage["events"][-500:]
        save_json(USAGE_LOG, self.usage)

        # Detailní výpis výstupní složky
        detail_lines = [f"  Output: {verdir}"]
        try:
            for item in sorted(verdir.iterdir(), key=lambda x: (x.is_dir(), x.name.lower())):
                if item.is_dir():
                    n_files = sum(1 for _ in item.rglob("*") if _.is_file())
                    detail_lines.append(f"    [dir]   {item.name}/  ({n_files} files)")
                else:
                    size_kb = item.stat().st_size / 1024
                    detail_lines.append(f"    [file]  {item.name}  ({size_kb:.0f} KB)")
        except Exception:
            pass
        _log("\n".join(detail_lines))

        return True, str(verdir)

    def _build_one_helper(self, h: dict, ver: str, live_log=None) -> tuple[bool, str]:
        """Build one helper exe into its own dist/<name>/vX.Y.Z/ folder.

        --onefile, unlike the projects: a helper is a single program someone
        starts by hand or at logon, and one file is the form that survives being
        copied somewhere by itself. It also means a helper can never collide with
        its parent's `_internal`.
        """
        name = h["name"]
        p = h["parent"]
        src = h["script"]

        def _log(msg: str):
            print(msg)
            if live_log:
                live_log(msg)

        if not src.exists():
            return False, f"Helper script not found:\n{src}"

        verdir = self.dist_root / name / f"v{ver}"
        if verdir.exists():
            # Same reason as the projects: OneDrive marks synced files read-only
            # and --noconfirm would fail on them.
            def _on_rm_error(func, path, exc_info):
                import stat as _stat
                try:
                    os.chmod(path, _stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            shutil.rmtree(str(verdir), onerror=_on_rm_error)
        verdir.mkdir(parents=True, exist_ok=True)

        workdir = Path(tempfile.gettempdir()) / "universal_builder_pyinstaller"
        specdir = workdir / "spec"
        builddir = workdir / "build"
        specdir.mkdir(parents=True, exist_ok=True)

        icon_path = p.resolve() / "icon.ico"
        icon_args = ["--icon", str(icon_path)] if icon_path.exists() else []

        args = [
            "py", "-m", "PyInstaller",
            "--onefile",
            # Console by default: a helper runs in the background and the window
            # is the only sign it is alive — and closing it is how you stop it.
            "--windowed" if h.get("windowed") else "--console",
            "--noconfirm", "--name", name,
        ] + icon_args + [
            "--distpath", str(verdir),
            "--workpath", str(builddir),
            "--specpath", str(specdir),
            "--paths", str(p),
        ]
        for imp in h.get("hidden_imports", []):
            args += ["--hidden-import", imp]
        for mod in h.get("exclude_modules", []):
            args += ["--exclude-module", mod]
        args.append(src.name)

        _log(f"BUILD cwd: {p}")
        _log(f"CMD: {' '.join(args)}\n")

        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=str(p))
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _log(line)
        proc.wait()
        if proc.returncode != 0:
            return False, f"PyInstaller failed with code {proc.returncode}\n\n{' '.join(args)}"

        exe_src = verdir / f"{name}.exe"
        exe_dst = verdir / f"{name} v{ver}.exe"
        if exe_src.exists():
            try:
                if exe_dst.exists():
                    exe_dst.unlink()
                exe_src.rename(exe_dst)
            except OSError as e:
                _log(f"Warning: could not rename exe: {e}")

        # The source, so a version folder still says what it was built from.
        try:
            (verdir / f"{name} v{ver}.py").write_bytes(src.read_bytes())
        except OSError as e:
            _log(f"Warning: could not copy source: {e}")

        # Data files the helper reads at runtime. In its own folder it cannot
        # borrow the app's copy, so it needs its own (Diagnostic's listener and
        # notify_provision.dat).
        for fname in h.get("extra_files", []):
            fsrc = p / fname
            fdst = verdir / Path(fname).name
            if fsrc.is_dir():
                try:
                    shutil.copytree(str(fsrc), str(fdst),
                                    ignore=shutil.ignore_patterns("__pycache__", "Thumbs.db"))
                    _log(f"  extra folder: {fsrc.name}/")
                except OSError as e:
                    _log(f"Warning: could not copy extra folder {fname}: {e}")
            elif fsrc.exists():
                try:
                    shutil.copy2(str(fsrc), str(fdst))
                    _log(f"  extra file: {fsrc.name}")
                except OSError as e:
                    _log(f"Warning: could not copy extra file {fname}: {e}")
            else:
                _log(f"Warning: extra_file not found: {fsrc}")

        if icon_path.exists():
            try:
                shutil.copy2(str(icon_path), str(verdir / "icon.ico"))
            except OSError:
                pass

        # The taskbar picture comes from the exe itself - see exe_icon_count().
        if exe_dst.exists():
            _n = exe_icon_count(exe_dst)
            if _n > 0:
                _log(f"  icon: the exe carries its own ({_n} group(s))")
            elif _n == 0:
                _log("Warning: the built exe carries NO icon of its own, so "
                     "its taskbar button will be a blank window")
            else:
                _log("Warning: could not check whether the exe carries an icon")

        # The helper's OWN documentation, named after the helper and living in
        # the parent's source folder. It is deployed as a program of its own, so
        # the Launcher looks for ReadMe_<helper name> — the parent's ReadMe would
        # never be found under that name, and the card would show no buttons.
        _rm_norm = lambda t: t.lower().replace("_", "").replace(" ", "").replace("-", "").replace(".", "")
        _short = {_rm_norm(f"ReadMe_{name}")}
        _full = {_rm_norm(f"ReadMe_{name}_Full"),
                 _rm_norm(f"ReadMe_{name}_Details"),
                 _rm_norm(f"Manual_{name}")}
        _docs = {}
        for f in p.iterdir():
            if not f.is_file():
                continue
            key = _rm_norm(f.stem)
            if key in _full:
                _docs.setdefault("readme (details)", f)
            elif key in _short:
                _docs.setdefault("readme", f)
        if "readme" not in _docs:
            _log(f"Warning: no ReadMe_{name} in {p} — deploy will keep the published one")
        if "readme (details)" not in _docs:
            _log(f"Note: no ReadMe_{name}_Full — the Launcher will show no Details button")
        for what, doc in _docs.items():
            try:
                shutil.copy2(str(doc), str(verdir / doc.name))
                _log(f"  {what}: {doc.name}")
            except OSError as e:
                _log(f"Warning: could not copy {doc.name}: {e}")

        try:
            write_version_to_txt(name, ver)
        except Exception as e:
            _log(f"Warning: could not write Versions.txt: {e}")

        return True, str(verdir)

    def _set_build_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.btn_build.config(state=state)

    def _on_build_finished(self, ok_count: int, fail_count: int, failed: list, built_paths: list, built_projects: list = None, elapsed: float = 0.0):
        self._set_build_buttons_enabled(True)
        self.invalidate_version_cache()
        self.projects_all = self.find_projects(self.root_folder)
        self.reset_next_version_overrides()
        self.projects_sorted = self.sort_projects(self.projects_all)
        self._render_project_buttons()

        prev_helper = self.selected_helper
        if self.selected_project is not None:
            by_name = {p.name: p for p in self.projects_sorted}
            if self.selected_project.name in by_name:
                self._select_project(by_name[self.selected_project.name])
            else:
                self._select_project(self.projects_sorted[0])
        else:
            self._select_project(self.projects_sorted[0])

        # The panel was showing a helper before the build — keep showing it,
        # with its new version, instead of jumping to its parent.
        if prev_helper is not None:
            same = next((h for h in self.helpers_of(prev_helper["parent"].name)
                         if h["name"] == prev_helper["name"]), None)
            if same is not None:
                self._select_helper(same)

        elapsed_str = f"{elapsed:.1f}s"
        if elapsed > 60:
            elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

        lines = ["=" * 40]
        lines.append(f"BUILD DONE  {'✓ No errors' if fail_count == 0 else '⚠ ' + str(fail_count) + ' failed'}")
        lines.append(f"  Time elapsed:   {elapsed_str}")
        lines.append(f"  Built:          {ok_count}  |  Failed: {fail_count}")
        if built_paths:
            lines.append("")
            for name, ver, t, out_dir in built_paths:
                lines.append(f"  ✓  {name} {ver}  ({t:.1f}s)")
                if out_dir:
                    lines.append(f"       → {out_dir}")
        if failed:
            lines.append("")
            for f in failed[:10]:
                first_line = f.split("\n")[0]
                lines.append(f"  ✗  {first_line}")
        lines.append("=" * 40)
        summary = "\n".join(lines)

        # Handed to the Copy manager so it can write the overall wrap-up report
        # covering build + copy together, instead of only the copy half.
        build_info = {
            "ok": ok_count,
            "fail": fail_count,
            "elapsed": elapsed,
            "items": [{"name": n, "version": v, "seconds": t, "out_dir": o}
                      for n, v, t, o in built_paths],
            "failed": [f.split("\n")[0] for f in failed],
        }

        if self.copy_after_build_var.get() and built_projects and self._on_build_done:
            self._on_build_done(built_projects, build_summary=summary, build_info=build_info)
        else:
            self._log(summary, force_scroll=True)
            if fail_count > 0:
                messagebox.showwarning("Build finished with errors", "\n".join(
                    [f"Failed: {fail_count}"] + [f.split('\n')[0] for f in failed[:10]]
                ))

    # ----------------- BUILD ACTIONS -----------------
    def _build_selected(self):
        projects = self._get_checked_projects()
        helpers = self._get_checked_helpers()
        if not projects and not helpers:
            messagebox.showinfo("No selection", "Select at least one project.")
            return

        versions_by_name = {}
        invalid = []

        # Helpers version independently of their parent — they are their own
        # program in dist, and a helper rebuilt on its own must not pretend to
        # be a new version of the app it came from.
        for name in [p.name for p in projects] + [h["name"] for h in helpers]:
            ver = self.effective_next_version_for_project(name).strip()
            if not ver:
                invalid.append(f"{name}: (empty)")
            else:
                versions_by_name[name] = ver

        if invalid:
            messagebox.showerror(
                "Invalid version",
                "These projects have invalid next version values:\n\n" + "\n".join(invalid)
            )
            return

        self._set_build_buttons_enabled(False)

        def worker():
            import time as _time
            ok_count = 0
            fail_count = 0
            failed = []
            built_paths = []
            built_projects = []
            start_time = _time.perf_counter()

            for p in projects:
                ver = versions_by_name[p.name]
                self._log(f"Building: {p.name}  ->  v{ver}")
                t0 = _time.perf_counter()
                if p.name.lower() == "internal builder":
                    ok, msg = self._build_internal_builder_sync(live_log=self._log)
                else:
                    ok, msg = self._build_one_project(p, ver, live_log=self._log)
                elapsed_one = _time.perf_counter() - t0
                if ok:
                    ok_count += 1
                    # msg is the version folder the build landed in — carried into
                    # the reports so they say WHERE the exe was built. The Internal
                    # Builder branch returns a sentence instead, so only keep msg
                    # when it actually looks like a path.
                    built_paths.append((p.name, f"v{ver}", elapsed_one, _as_out_dir(msg)))
                    built_projects.append((p, f"v{ver}"))
                else:
                    fail_count += 1
                    failed.append(f"{p.name}:\n{msg}")

            for h in helpers:
                name = h["name"]
                ver = versions_by_name[name]
                self._log(f"Building helper: {name}  ->  v{ver}")
                t0 = _time.perf_counter()
                ok, msg = self._build_one_helper(h, ver, live_log=self._log)
                elapsed_one = _time.perf_counter() - t0
                if ok:
                    ok_count += 1
                    built_paths.append((name, f"v{ver}", elapsed_one, _as_out_dir(msg)))
                    # Deploy keys on the dist folder's name, and a helper's dist
                    # folder is named after the helper — so "Copy after build"
                    # picks it up like any other program.
                    built_projects.append((self.dist_root / name, f"v{ver}"))
                else:
                    fail_count += 1
                    failed.append(f"{name}:\n{msg}")

            elapsed_total = _time.perf_counter() - start_time
            self.after(0, lambda: self._on_build_finished(
                ok_count, fail_count, failed, built_paths, built_projects, elapsed_total))

        threading.Thread(target=worker, daemon=True).start()

    def _open_dist(self):
        self.dist_root.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.dist_root))

    def _open_readme(self, project_dir: Path):
        name = project_dir.name
        norm = lambda s: s.lower().replace("_", "").replace(" ", "")
        target = norm(f"ReadMe_{name}")

        readme = None
        for f in project_dir.iterdir():
            if f.is_file() and norm(f.stem) == target:
                readme = f
                break

        if readme is None:
            for cand in ("ReadMe.txt", "README.md", "README.txt", "ReadMe.md"):
                p = project_dir / cand
                if p.exists():
                    readme = p
                    break

        if readme is None:
            if messagebox.askyesno(
                "ReadMe not found",
                f"No ReadMe found in:\n{project_dir}\n\nCreate ReadMe_{name}.txt?"
            ):
                readme = project_dir / f"ReadMe_{name}.txt"
                readme.write_text("", encoding="utf-8")
            else:
                return

        os.startfile(str(readme))

    def _run_copy_manager(self):
        exe = None
        if COPY_MANAGER_DIST_DIR.exists():
            exes = sorted(COPY_MANAGER_DIST_DIR.glob("*.exe"))
            if exes:
                exe = exes[0]

        if exe and exe.exists():
            try:
                subprocess.Popen([str(exe)], cwd=str(COPY_MANAGER_DIST_DIR))
                return
            except Exception as e:
                messagebox.showwarning("Run warning", f"Could not start Copy manager EXE:\n{e}")

        if COPY_MANAGER_SRC_DIR.exists():
            py_candidates = []
            for p in COPY_MANAGER_SRC_DIR.glob("*.py"):
                if p.name == "__init__.py":
                    continue
                py_candidates.append(p)

            prefer = ["copy_manager.py", "main.py", "app.py"]
            py_entry = None
            for nm in prefer:
                cand = COPY_MANAGER_SRC_DIR / nm
                if cand.exists():
                    py_entry = cand
                    break
            if py_entry is None and py_candidates:
                py_candidates.sort(key=lambda x: x.name.lower())
                py_entry = py_candidates[0]

            if py_entry and py_entry.exists():
                try:
                    subprocess.Popen(["py", str(py_entry)], cwd=str(COPY_MANAGER_SRC_DIR))
                    return
                except Exception as e:
                    messagebox.showerror("Run failed", f"Could not start Copy manager script:\n{e}")
                    return

        messagebox.showerror(
            "Copy manager not found",
            "Could not find Copy manager in either location:\n"
            f"- DIST: {COPY_MANAGER_DIST_DIR}\n"
            f"- SRC:  {COPY_MANAGER_SRC_DIR}"
        )
