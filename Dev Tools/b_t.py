# b_t.py
import json
import sys as _sys
import os
import re
import subprocess
import time
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
        root_str = settings.get("root_folder", str(default_root))
        self.root_folder = Path(root_str)

        self.dist_root = PROGRAMY_DIST_DIR

        self.selected_project: Path | None = None
        self.projects_all: list[Path] = []
        self.projects_sorted: list[Path] = []

        self.project_checks: dict[str, tk.BooleanVar] = {}
        self.project_rows: dict[str, ttk.Frame] = {}
        self.project_next_override: dict[str, str] = {}
        self.project_next_labels: dict[str, tk.StringVar] = {}

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
    def _log(self, msg: str):
        print(msg)
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

        cand = project_dir / f"{project_dir.name}.py"
        if cand.exists():
            return cand

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

    def last_version_from_dist(self, project_name: str):
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
    def _build_ui(self):
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.LabelFrame(outer, text="Root folder")
        top.pack(fill="x")

        top_row = ttk.Frame(top)
        top_row.pack(fill="x", padx=10, pady=8)

        entry = ttk.Entry(top_row, textvariable=self.root_var, state="readonly")
        entry.pack(side="left", fill="x", expand=True)

        ttk.Button(top_row, text="Change…", command=self._change_root).pack(side="left", padx=(8, 0))

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

        canvas = tk.Canvas(canvas_wrap, highlightthickness=0, width=320)
        vsb = ttk.Scrollbar(canvas_wrap, orient="vertical", command=canvas.yview)
        self.btn_frame = ttk.Frame(canvas)

        self.btn_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.btn_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        self.btn_frame.bind("<MouseWheel>", _on_mousewheel)

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
        self.project_checks.clear()
        self.project_rows.clear()
        self.project_next_labels.clear()

        _migrate = {
            "main": "Main project", "side": "Side project", "ignored": "Ignored project",
            "Main projects": "Main project", "Side projects": "Side project",
            "Ignored": "Ignored project",
        }
        for name, grp in list(self.project_groups.items()):
            if grp in _migrate:
                self.project_groups[name] = _migrate[grp]

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

                var = tk.BooleanVar(value=False)
                self.project_checks[p.name] = var
                self.project_rows[p.name] = row

                cb = ttk.Checkbutton(
                    row, text=p.name, variable=var,
                    command=lambda pp=p: self._on_project_check_clicked(pp)
                )
                cb.pack(side="left", anchor="w")

                next_var = tk.StringVar(value=f"Next: {self.effective_next_version_for_project(p.name)}")
                self.project_next_labels[p.name] = next_var

                ttk.Label(row, textvariable=next_var, width=18).pack(side="right", padx=(8, 0))
                ttk.Button(row, text="ReadMe", width=8,
                           command=lambda pp=p: self._open_readme(pp)).pack(side="right", padx=(4, 0))
                ttk.Button(row, text="Details", width=7,
                           command=lambda pp=p: self._select_project(pp)).pack(side="right")

                row_idx += 1

        self.btn_frame.columnconfigure(0, weight=1)
        self._update_focus_styles()

    def _update_focus_styles(self):
        for name, row in self.project_rows.items():
            if self.selected_project and name == self.selected_project.name:
                try:
                    row.configure(style="Focused.TFrame")
                except Exception:
                    pass
            else:
                try:
                    row.configure(style="TFrame")
                except Exception:
                    pass

    def _refresh_project_list_version_labels(self):
        for p in self.projects_sorted:
            var = self.project_next_labels.get(p.name)
            if var is not None:
                var.set(f"Next: {self.effective_next_version_for_project(p.name)}")
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
        self.dist_root = PROGRAMY_DIST_DIR
        self.root_var.set(str(self.root_folder))

        save_json(SETTINGS_PATH, {"root_folder": str(self.root_folder)})
        self._reload_projects(select_first=True)

    def _reload_projects(self, select_first: bool):
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

    def _on_group_changed(self, event=None):
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
        self._select_project(project_dir)

    def _bind_next_version_trace(self):
        try:
            if self._next_var_trace_id is not None:
                self.next_ver_var.trace_remove("write", self._next_var_trace_id)
        except Exception:
            pass

        self._next_var_trace_id = self.next_ver_var.trace_add("write", self._on_next_version_edited)

    def _on_next_version_edited(self, *args):
        if self.selected_project is None:
            return

        name = self.selected_project.name
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

    def _clear_all_projects(self):
        for var in self.project_checks.values():
            var.set(False)

    def _get_checked_projects(self) -> list[Path]:
        out = []
        by_name = {p.name: p for p in self.projects_sorted}
        for name, var in self.project_checks.items():
            if var.get() and name in by_name:
                if self.project_groups.get(name, "Main project") != "Ignored project":
                    out.append(by_name[name])
        return out

    # ----------------- BUILD CORE -----------------
    def _build_one_project(self, p: Path, ver: str, live_log=None) -> tuple[bool, str]:
        """Postaví projekt pomocí PyInstalleru do dist/<projekt>/vX.X.X/."""
        name = p.name

        main_path = self.guess_main_py(p)
        if not main_path:
            return False, f"No .py entry found in:\n{p}"

        # Výstupní složka pro tuto verzi
        verdir = self.dist_root / p.name / f"v{ver}"
        verdir.mkdir(parents=True, exist_ok=True)

        # Dočasné složky pro PyInstaller
        workdir = Path(tempfile.gettempdir()) / "universal_builder_pyinstaller"
        specdir = workdir / "spec"
        builddir = workdir / "build"
        specdir.mkdir(parents=True, exist_ok=True)

        icon_path = p / "icon.ico"
        icon_args = ["--icon", str(icon_path)] if icon_path.exists() else []

        extra_py_files = [x for x in p.glob("*.py") if x.name != main_path.name]

        build_cfg = load_json(p / "build_config.json", {})
        extra_collect_all: list[str] = build_cfg.get("collect_all", [])
        extra_hidden_imports: list[str] = build_cfg.get("hidden_imports", [])
        # Extra data files to copy into the version folder after build
        # e.g. "extra_files": ["cpva_presets.json"] in build_config.json
        extra_data_files: list[str] = build_cfg.get("extra_files", [])

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
        for imp in extra_hidden_imports:
            args += ["--hidden-import", imp]
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

        # Kopíruj main .py a ostatní zdrojáky
        try:
            py_dst = verdir / f"{name} v{ver}.py"
            py_dst.write_bytes(main_path.read_bytes())
            for extra in extra_py_files:
                edst = verdir / extra.name
                edst.write_bytes(extra.read_bytes())
        except Exception as e:
            print(f"Warning: could not copy source files: {e}")

        # Kopíruj extra datové soubory definované v build_config.json → extra_files
        for fname in extra_data_files:
            src = p / fname
            if src.exists():
                try:
                    shutil.copy2(str(src), str(verdir / src.name))
                    _log(f"  extra file: {src.name}")
                except Exception as e:
                    _log(f"Warning: could not copy extra file {fname}: {e}")
            else:
                _log(f"Warning: extra_file not found: {src}")

        # Vyčisti pouze pracovní TEMP dir
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

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

    def _set_build_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        self.btn_build.config(state=state)

    def _on_build_finished(self, ok_count: int, fail_count: int, failed: list, built_paths: list, built_projects: list = None, elapsed: float = 0.0):
        self._set_build_buttons_enabled(True)
        self.projects_all = self.find_projects(self.root_folder)
        self.reset_next_version_overrides()
        self.projects_sorted = self.sort_projects(self.projects_all)
        self._render_project_buttons()

        if self.selected_project is not None:
            by_name = {p.name: p for p in self.projects_sorted}
            if self.selected_project.name in by_name:
                self._select_project(by_name[self.selected_project.name])
            else:
                self._select_project(self.projects_sorted[0])
        else:
            self._select_project(self.projects_sorted[0])

        elapsed_str = f"{elapsed:.1f}s"
        if elapsed > 60:
            elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"

        lines = ["=" * 40]
        lines.append(f"BUILD DONE  {'✓ No errors' if fail_count == 0 else '⚠ ' + str(fail_count) + ' failed'}")
        lines.append(f"  Time elapsed:   {elapsed_str}")
        lines.append(f"  Built:          {ok_count}  |  Failed: {fail_count}")
        if built_paths:
            lines.append("")
            for name, ver, t in built_paths:
                lines.append(f"  ✓  {name} {ver}  ({t:.1f}s)")
        if failed:
            lines.append("")
            for f in failed[:10]:
                first_line = f.split("\n")[0]
                lines.append(f"  ✗  {first_line}")
        lines.append("=" * 40)
        summary = "\n".join(lines)

        if self.copy_after_build_var.get() and built_projects and self._on_build_done:
            self._on_build_done(built_projects, build_summary=summary)
        else:
            self._log(summary)
            if fail_count > 0:
                messagebox.showwarning("Build finished with errors", "\n".join(
                    [f"Failed: {fail_count}"] + [f.split('\n')[0] for f in failed[:10]]
                ))

    # ----------------- BUILD ACTIONS -----------------
    def _build_selected(self):
        projects = self._get_checked_projects()
        if not projects:
            messagebox.showinfo("No selection", "Select at least one project.")
            return

        versions_by_name = {}
        invalid = []

        for p in projects:
            ver = self.effective_next_version_for_project(p.name).strip()
            if not ver:
                invalid.append(f"{p.name}: (empty)")
            else:
                versions_by_name[p.name] = ver

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
                ok, msg = self._build_one_project(p, ver, live_log=self._log)
                elapsed_one = _time.perf_counter() - t0
                if ok:
                    ok_count += 1
                    built_paths.append((p.name, f"v{ver}", elapsed_one))
                    built_projects.append((p, f"v{ver}"))
                else:
                    fail_count += 1
                    failed.append(f"{p.name}:\n{msg}")

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
