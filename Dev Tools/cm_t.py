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
def _src_root() -> Path:
    """Kořen git repozitáře s py zdrojáky (L3-QoL-JanJan/)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent

def _dist_root() -> Path:
    """programy/dist/ — kam jdou exe soubory."""
    return _src_root().parent / "dist"

PROGRAMS_ROOT = _src_root()

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

INTERNAL_BUILDER_DIST = _dist_root() / "Internal Builder"
VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
README_PREFIX = "ReadMe_"
README_NAME = "ReadMe.txt"

STATE_FILE_NAME = "copy_manager_state.ini"
STATE_SECTION = "deployed"  # keys: <program_name> = vX.Y.Z


# ---------------- STATE ----------------
def _app_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


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
def parse_version(folder_name: str):
    m = VERSION_RE.fullmatch(folder_name)
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

def move_existing_exes_to_archive(target_dir: Path, keep_name: str, logs: list[str], program_name: str):
    """
    Move ALL *.exe except keep_name into archive.
    If an exe is locked (WinError 32) -> SKIP and continue (archiving can happen later).
    """
    archive_dir = target_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for exe in target_dir.glob("*.exe"):
        if exe.name.lower() == keep_name.lower():
            continue

        src = exe
        dst_base = archive_dir / f"{src.stem}__{timestamp}{src.suffix}"
        dst = unique_path(dst_base)

        moved, why = _try_move(src, dst)
        if moved:
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
        
        self.internal_vars: dict[Path, tk.BooleanVar] = {}
        self.dest_path_labels: list[tuple[ttk.Label, str]] = []
        self._log_autoscroll = True

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
            ("scratch",    "Scratch (Software) folder",  "e.g. Z:\\Software"),
            ("sharepoint", "Sharepoint (QoL) folder",    "e.g. C:\\...\\L3-HAPLS\\General\\QoL"),
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
            self._build_dest_rows()
            messagebox.showinfo("Paths saved", "Paths saved to %APPDATA%\\DevTools\\config.json")

        ttk.Button(win, text="Save", command=on_save, padding=(16, 6)).pack(pady=(8, 12))

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root)
        top.pack(fill="x")

        top.grid_columnconfigure(0, weight=0)
        top.grid_columnconfigure(1, weight=1)

        ttk.Label(top, text="Programs folder:").grid(row=0, column=0, sticky="w")

        self.programs_root_lbl = ttk.Label(top, text=str(PROGRAMS_ROOT), justify="left", anchor="w")
        self.programs_root_lbl.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        top.bind("<Configure>", self._update_programs_root_wraplength)
        self.after(0, self._update_programs_root_wraplength)

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(10, 10))

        left = ttk.LabelFrame(body, text="Select programs + version (default = latest)")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.header = ttk.Frame(left)
        self.header.pack(fill="x", padx=8, pady=(8, 2))

        self.header.grid_columnconfigure(0, minsize=26)
        self.header.grid_columnconfigure(1, minsize=self.program_col_px)
        self.header.grid_columnconfigure(2, minsize=self.version_col_px)
        self.header.grid_columnconfigure(3, minsize=self.new_col_px)
        self.header.grid_columnconfigure(4, weight=1)
        self.header.grid_columnconfigure(5, minsize=40)

        ttk.Label(self.header, text="Program").grid(row=0, column=1, sticky="w")
        ttk.Label(self.header, text="Version").grid(row=0, column=2, sticky="w")
        ttk.Label(self.header, text="Status").grid(row=0, column=3, sticky="w")
        ttk.Label(self.header, text="Libraries").grid(row=0, column=5, sticky="w")

        self.programs_sf = ScrollableFrame(left)
        self.programs_sf.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        right = ttk.LabelFrame(body, text="Destinations")
        right.pack(side="left", fill="y", expand=False)

        right.configure(width=330)
        right.pack_propagate(False)

        self.dest_frame = ttk.Frame(right)
        self.dest_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_dest_rows()

        self.after_idle(self._reflow_dest_paths)

        actions = ttk.Frame(root)
        actions.pack(fill="x")

        ttk.Button(actions, text="Select ALL", command=self._select_all_programs).pack(side="left")
        ttk.Button(actions, text="Select NEW", command=self._select_new_programs).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Clear", command=self._clear_programs).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Refresh", command=self._refresh).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="⚙ Set paths", command=self._open_set_paths).pack(side="left", padx=(8, 0))

        self.copy_btn = ttk.Button(actions, text="Copy", command=self._on_copy)
        self.copy_btn.pack(side="right")
        self.readme_btn = ttk.Button(actions, text="Copy ReadMe only", command=self._on_copy_readme_only)
        self.readme_btn.pack(side="right", padx=(0, 8))
        self.fix_icons_btn = ttk.Button(actions, text="Fix icons", command=self._on_fix_icons)
        self.fix_icons_btn.pack(side="right", padx=(0, 8))
        self.internal_btn = ttk.Button(actions, text="Deploy libraries", command=self._on_deploy_internal)
        self.internal_btn.pack(side="right", padx=(0, 8))
        self.build_internal_btn = ttk.Button(actions, text="Build libraries", command=self._on_build_internal)
        self.build_internal_btn.pack(side="right", padx=(0, 8))

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
        self._load_programs()

    def auto_deploy(self, built_projects: list, build_summary: str = None):
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
            self._log("[auto_deploy] ERROR: žádná destinace není vybrána.")
            return

        self._log(f"[auto_deploy] Spouštím deploy pro: {[p.name for p, _ in built_projects]}\n")
        self._on_copy(build_summary=build_summary)

    def _log(self, text: str):
        target = self._external_log if self._external_log is not None else getattr(self, "log", None)
        if target is None:
            return
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

    def _load_programs(self):
        for child in self.programs_sf.inner.winfo_children():
            child.destroy()
        self.program_vars.clear()
        self.program_version_vars.clear()
        self.program_is_new.clear()
        self.program_latest_version.clear()

        if not PROGRAMS_ROOT.exists():
            self._log(f"ERROR: programs root does not exist: {PROGRAMS_ROOT}")
            return

        program_dirs = []
        IGNORE = {"dist", "matlab", "icons", "internal builder", ".venv", ".vscode", "extractor"}
        for p in sorted(PROGRAMS_ROOT.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir():
                continue
            if p.name.startswith("_"):
                continue
            if p.name.lower() in IGNORE:
                continue
            program_dirs.append(p)

        if not program_dirs:
            self._log(f"No program folders found in: {PROGRAMS_ROOT}")
            return

        self.program_col_px = self._compute_program_col_px([p.name for p in program_dirs])
        self.header.grid_columnconfigure(1, minsize=self.program_col_px)

        for p in program_dirs:
            dist_dir = _dist_root() / p.name
            version_folders = list_versions(dist_dir)
            version_names = [vf.name for vf in version_folders]

            latest = version_names[0] if version_names else ""
            self.program_latest_version[p] = latest

            # Read deployed version from Versions.txt (primary) or local state (fallback)
            versions_map = read_versions_txt()
            last_deployed = versions_map.get(p.name, "") or self.state_deployed.get(p.name.lower(), "")
            last_deployed = last_deployed.strip()
            is_new = bool(latest) and is_newer_version(latest, last_deployed)
            self.program_is_new[p] = is_new

            var_checked = tk.BooleanVar(value=False)
            self.program_vars[p] = var_checked

            var_version = tk.StringVar(value=latest)
            self.program_version_vars[p] = var_version

            row = ttk.Frame(self.programs_sf.inner)
            row.pack(fill="x", pady=3)

            row.grid_columnconfigure(0, minsize=26)
            row.grid_columnconfigure(1, minsize=self.program_col_px)
            row.grid_columnconfigure(2, minsize=self.version_col_px)
            row.grid_columnconfigure(3, minsize=self.new_col_px)
            row.grid_columnconfigure(4, weight=1)

            ttk.Checkbutton(row, variable=var_checked).grid(row=0, column=0, sticky="w")
            ttk.Label(row, text=p.name).grid(row=0, column=1, sticky="w", padx=(6, 8))

            cb = ttk.Combobox(
                row,
                textvariable=var_version,
                values=version_names,
                state="readonly" if version_names else "disabled",
            )
            cb.grid(row=0, column=2, sticky="w")

            if not version_names:
                ttk.Label(row, text="NEW", foreground="gray").grid(row=0, column=3, sticky="w")
            else:
                if is_new:
                    ttk.Label(row, text="NEW", foreground="green").grid(row=0, column=3, sticky="w")
                else:
                    ttk.Label(row, text="").grid(row=0, column=3, sticky="w")

            if last_deployed:
                ttk.Label(row, text=f"last deployed: {last_deployed}", foreground="gray").grid(
                    row=0, column=4, sticky="w"
                )
            else:
                ttk.Label(row, text="last deployed: (none)", foreground="gray").grid(
                    row=0, column=4, sticky="w"
                )
            var_internal = tk.BooleanVar(value=False)
            self.internal_vars[p] = var_internal
            ttk.Checkbutton(row, variable=var_internal).grid(row=0, column=5, sticky="w")

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
        print(f"DEBUG _collect_icon_conflicts: jobs={[(p.name, v) for p,v in jobs]}", flush=True)
        print(f"DEBUG destination_roots={destination_roots}", flush=True)

        for program_dir, version_name in jobs:
            if not version_name:
                continue

            version_folder = _dist_root() / program_dir.name / version_name

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
                print(f"DEBUG [{program_dir.name}] dst_root={dst_root.name} dst_icon_exists={dst_icon.exists()} src_size={icon_src.stat().st_size} dst_size={dst_icon.stat().st_size if dst_icon.exists() else 'N/A'}", flush=True)
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

        stats = {
            "exe_copied": 0, "exe_failed": 0,
            "py_copied": 0, "py_failed": 0,
            "readme_copied": 0, "readme_failed": 0,
            "archived": 0, "archive_skipped": 0,
        }
        program_name = program_dir.name

        if not version_name:
            raise FileNotFoundError(f"[{program_name}] NEW program: build it first (no versions in dist).")

        version_folder = _dist_root() / program_name / version_name
        if not version_folder.exists():
            raise FileNotFoundError(f"Selected version folder missing: {version_folder}")

        src_exe = find_exe_in_folder(version_folder, program_name, version_name)
        src_readme = find_readme_or_raise(program_dir, program_name)

        # Najdi všechny .py soubory ve version_folder
        src_py_files = list(version_folder.glob("*.py"))

        # Najdi icon.png vedle exe nebo v program_dir
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

        # Najdi všechny složky a ostatní soubory ve version_folder (kromě _internal)
        src_extras = []
        for item in version_folder.iterdir():
            if item.name == "_internal":
                continue
            if item == src_exe:
                continue
            if item.suffix.lower() in (".py", ".exe"):
                continue
            if item.is_dir():
                continue
            src_extras.append(item)

        log(f"[{program_name}] Version: {version_name}")
        log(f"[{program_name}] EXE: {src_exe.name}")
        log(f"[{program_name}] ReadMe: {src_readme.name}")
        log(f"[{program_name}] Extra files: {[x.name for x in src_extras]}")
        log(f"[{program_name}] PY files: {[x.name for x in src_py_files]}")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ver = version_name

        for dst_root in destination_roots:
            target_dir = dst_root / program_name
            target_dir.mkdir(parents=True, exist_ok=True)
            archive_dir = target_dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)

            log(f"[{program_name}] → {target_dir}")

            dest_label = dst_root.name
            flog("dest", f"→ {target_dir}")

            # ── Smaž zbytkové složky z předchozích chybných deployů ─
            for item in target_dir.iterdir():
                if item.is_dir() and item.name not in ("_internal", "archive"):
                    try:
                        shutil.rmtree(item)
                        log(f"[{program_name}] Removed stale folder: {item.name}")
                        flog("removed", item.name, str(target_dir))
                    except Exception as e:
                        log(f"[{program_name}] Could not remove folder {item.name}: {e}")

            # ── Archivuj staré .exe ──────────────────────────────────
            for exe in target_dir.glob(f"{program_name}*.exe"):
                dst_arch = archive_dir / f"{exe.stem}__{timestamp}{exe.suffix}"
                moved, why = _try_move(exe, dst_arch)
                if moved:
                    log(f"[{program_name}] Archived EXE: {exe.name}")
                    flog("archived", exe.name, str(dst_arch.relative_to(dst_root)))
                    stats["archived"] += 1
                else:
                    log(f"[{program_name}] SKIP archive (locked): {exe.name}")
                    flog("skipped", exe.name, "locked")
                    stats["archive_skipped"] += 1

            # ── Archivuj staré .py ───────────────────────────────────
            for pyf in target_dir.glob("*.py"):
                dst_arch = archive_dir / f"{pyf.stem}__{timestamp}{pyf.suffix}"
                moved, why = _try_move(pyf, dst_arch)
                if moved:
                    log(f"[{program_name}] Archived PY: {pyf.name}")
                    flog("archived", pyf.name, str(dst_arch.relative_to(dst_root)))
                    stats["archived"] += 1
                else:
                    log(f"[{program_name}] SKIP archive PY (locked): {pyf.name}")
                    flog("skipped", pyf.name, "locked")
                    stats["archive_skipped"] += 1

            # ── Kopíruj nový .exe ────────────────────────────────────
            dst_exe = target_dir / f"{program_name} {ver}.exe"
            try:
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
                        if dst_item.exists():
                            shutil.rmtree(dst_item)
                        shutil.copytree(item, dst_item)
                    else:
                        shutil.copy2(item, dst_item)
                    log(f"[{program_name}] Copied extra: {item.name}")
                    flog("copied", item.name, str(dst_item.relative_to(dst_root)))
                    stats["exe_copied"] += 1
                except Exception as e:
                    log(f"[{program_name}] FAILED extra {item.name}: {e}")
                    flog("failed", item.name, str(e))
                    stats["exe_failed"] += 1

            # ── ReadMe ───────────────────────────────────────────────
            try:
                dst_readme = target_dir / src_readme.name
                if dst_readme.exists():
                    log(f"[{program_name}] WARNING: {src_readme.name} exists, overwriting")
                shutil.copy2(src_readme, dst_readme)
                log(f"[{program_name}] ReadMe copied -> {dst_readme.name}")
                flog("copied", src_readme.name, str(dst_readme.relative_to(dst_root)))
                stats["readme_copied"] += 1
            except Exception as e:
                log(f"[{program_name}] FAILED ReadMe: {e}")
                flog("failed", src_readme.name, str(e))
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
                    except Exception as e:
                        log(f"[{program_name}] FAILED icon: {e}")
                        flog("failed", icon_src.name, str(e))
                else:
                    log(f"[{program_name}] Icon skipped (kept existing) -> {dst_root}")
                    flog("skipped", icon_src.name, f"kept existing in {dest_label}")

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

        def worker():
            for program_dir in selected_programs:
                try:
                    src_readme = find_readme_or_raise(program_dir, program_dir.name)
                    for dst_root in selected_roots:
                        target_dir = dst_root / program_dir.name
                        target_dir.mkdir(parents=True, exist_ok=True)
                        dst_readme = target_dir / src_readme.name
                        shutil.copy2(src_readme, dst_readme)
                        self.after(0, self._log, f"[{program_dir.name}] ReadMe copied -> {dst_readme}")
                except Exception as e:
                    self.after(0, self._log, f"[{program_dir.name}] ERROR: {e}")
            self.after(0, self._log, "\nDone.")
            self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fix_icons(self):
        selected_roots = self._get_selected_destination_roots()
        if not selected_roots:
            messagebox.showwarning("No destination", "Select at least one destination root.")
            return

        self._clear_log()
        self._log("Fixing icons...\n")
        self._set_busy(True)

        def worker():
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
                    src_program_dir = PROGRAMS_ROOT / program_name
                    src_ico = src_program_dir / "icon.ico"

                    # Smaž PNG ikony
                    for png_name in ("icon.png", "Icon.png", "ICON.png"):
                        png = program_dir / png_name
                        if png.exists():
                            try:
                                png.unlink()
                                self.after(0, self._log, f"[{program_name}] Removed: {png_name}")
                            except Exception as e:
                                self.after(0, self._log, f"[{program_name}] Could not remove {png_name}: {e}")

                    # Kopíruj icon.ico pokud existuje ve zdroji
                    if src_ico.exists():
                        dst_ico = program_dir / "icon.ico"
                        if not dst_ico.exists() or dst_ico.stat().st_size != src_ico.stat().st_size:
                            try:
                                shutil.copy2(src_ico, dst_ico)
                                self.after(0, self._log, f"[{program_name}] Copied icon.ico")
                            except Exception as e:
                                self.after(0, self._log, f"[{program_name}] FAILED icon.ico: {e}")
                        else:
                            self.after(0, self._log, f"[{program_name}] icon.ico OK (same size, skipped)")
                    else:
                        self.after(0, self._log, f"[{program_name}] No icon.ico in source, skipping")

            self.after(0, self._log, "\nDone.")
            self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_build_internal(self):
        builder_script = PROGRAMS_ROOT / "Internal Builder" / "_internal_builder.py"
        if not builder_script.exists():
            messagebox.showerror("Error", f"Builder script not found:\n{builder_script}")
            return

        self._clear_log()
        self._log(f"Building _internal_builder...\n{builder_script}\n")
        self._set_busy(True)
        self.build_internal_btn.configure(state="disabled")

        def worker():
            import subprocess
            cmd = [
                sys.executable, "-m", "PyInstaller",
                "--onedir", "--windowed",
                "--name", "_internal_builder",
                "--collect-all", "PIL",
                "--collect-all", "matplotlib",
                "--collect-all", "pyparsing",
                "--noconfirm",
                str(builder_script),
            ]
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
                    self.after(0, self._log, "\n✓ Build DONE.")
                else:
                    self.after(0, self._log, f"\n✗ Build FAILED (returncode={proc.returncode})")
            except Exception as e:
                self.after(0, self._log, f"ERROR: {e}")
            finally:
                self.after(0, self._set_busy, False)
                self.after(0, lambda: self.build_internal_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_deploy_internal(self):
        selected = [p for p, v in self.internal_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("Nothing selected", "Check at least one _int checkbox.")
            return

        # Najdi nejnovější verzi _internal_builder
        if not INTERNAL_BUILDER_DIST.exists():
            messagebox.showerror("Error", f"_internal_builder dist not found:\n{INTERNAL_BUILDER_DIST}")
            return

        internal_src = INTERNAL_BUILDER_DIST / "_internal"
        if not internal_src.exists():
            messagebox.showerror("Error", f"_internal folder not found in:\n{INTERNAL_BUILDER_DIST}")
            return

        scratch = _get_scratch_root()
        if scratch is None:
            messagebox.showerror("Error", "Scratch path not configured.\nClick ⚙ Set paths first.")
            return
        zip_path = scratch / "_internal_builder.zip"

        self._clear_log()
        self._log(f"Packing _internal from: {INTERNAL_BUILDER_DIST}\n")
        self._set_busy(True)

        def worker():
            ok = 0
            fail = 0

            # Spočítej soubory předem
            all_files = [f for f in internal_src.rglob("*") if f.is_file()]
            n_files = len(all_files)
            self.after(0, self._log, f"Files found in _internal: {n_files}")
            total_steps = n_files + n_files * len(selected)
            done_steps = 0
            self.after(0, lambda: self._progress_show(total_steps))

            # Zabal _internal do ZIP
            try:
                import zipfile as _zf
                tmp_zip = zip_path.with_suffix(".tmp.zip")
                self.after(0, self._log, f"Creating ZIP: {zip_path.name}")
                with _zf.ZipFile(tmp_zip, "w", compression=_zf.ZIP_DEFLATED) as zf:
                    for f in all_files:
                        arcname = "_internal_builder/_internal/" + f.relative_to(internal_src).as_posix()
                        zf.write(f, arcname)
                        done_steps += 1
                        self.after(0, lambda d=done_steps, t=total_steps: self._progress_update(d, t, "ZIP"))
                tmp_zip.replace(zip_path)
                import zipfile as _zf2
                with _zf2.ZipFile(zip_path, "r") as _zcheck:
                    _zcount = sum(1 for m in _zcheck.infolist() if not m.filename.endswith("/"))
                self.after(0, self._log, f"ZIP created: {zip_path.name} — {_zcount} files\n")
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
                    if old_internal.exists():
                        shutil.rmtree(old_internal)
                        self.after(0, self._log, f"[{program_dir.name}] Removed old _internal")

                    with zipfile.ZipFile(zip_path, "r") as zf:
                        members = [m for m in zf.infolist() if not m.filename.endswith("/")]
                        prefix = ""
                        for m in members:
                            idx = m.filename.find("_internal/")
                            if idx >= 0:
                                prefix = m.filename[:idx + len("_internal/")]
                                break

                        extracted = 0
                        for member in members:
                            if not member.filename.startswith(prefix):
                                continue
                            member.filename = member.filename[len(prefix):]
                            if not member.filename:
                                continue
                            zf.extract(member, old_internal)
                            extracted += 1
                            done_steps += 1
                            self.after(0, lambda d=done_steps, t=total_steps, n=program_dir.name: self._progress_update(d, t, n))

                    self.after(0, self._log, f"[{program_dir.name}] OK — {extracted} files")
                    ok += 1
                except Exception as e:
                    self.after(0, self._log, f"[{program_dir.name}] ERROR: {e}")
                    fail += 1
                    done_steps += n_files
                    self.after(0, lambda d=done_steps, t=total_steps: self._progress_update(d, t, "error"))

            summary = f"\n{'='*40}\nDeploy libraries DONE  ✓ {ok} OK  |  ✗ {fail} failed\n{'='*40}"
            self.after(0, self._progress_hide)
            self.after(0, self._log, summary)
            self.after(0, self._set_busy, False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_copy(self, build_summary: str = None):
        selected_programs = self._get_selected_programs()
        selected_roots = self._get_selected_destination_roots()

        if not selected_programs:
            messagebox.showwarning("Nothing selected", "Select at least one program.")
            return
        if not selected_roots:
            messagebox.showwarning("No destination", "Select at least one destination root.")
            return

        jobs = [(p, self.program_version_vars[p].get().strip()) for p in selected_programs]

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

                        self.state_deployed[program_dir.name.lower()] = version_name
                        write_version_to_txt(program_dir.name, version_name)
                        changed = True

                    except Exception as e:
                        err_msg = str(e)
                        self.after(0, self._log, f"[{program_dir.name}] ERROR: {err_msg}\n")
                        total_stats["exe_failed"] += 1
                        all_file_logs.append((program_dir.name, version_name, [f"  [failed  ]  {err_msg}"]))

                if changed:
                    save_state(self.state_deployed)

                n_errors = total_stats['exe_failed']
                elapsed = time.perf_counter() - start_time
                elapsed_str = f"{elapsed:.1f}s"
                if elapsed > 60:
                    minutes = int(elapsed // 60)
                    seconds = int(elapsed % 60)
                    elapsed_str = f"{minutes}m {seconds}s"
                n_dest = len(selected_roots)

                detail_lines = []
                for prog_name, ver_name, flog in all_file_logs:
                    detail_lines.append(f"\n  {prog_name}  {ver_name}")
                    detail_lines.extend(flog)

                summary = (
                    f"\n{'='*40}\n"
                    f"ALL DONE  {'⚠ ' + str(n_errors) + ' error(s) — see log above' if n_errors else '✓ No errors'}\n"
                    f"  Time elapsed:     {elapsed_str}\n"
                    f"  Destinations:      {n_dest}\n"
                    f"  EXE copied:        {total_stats['exe_copied'] // max(n_dest,1)} per dest  |  failed: {total_stats['exe_failed']}\n"
                    f"  PY copied:         {total_stats['py_copied'] // max(n_dest,1)} per dest  |  failed: {total_stats['py_failed']}\n"
                    f"  ReadMe copied:     {total_stats['readme_copied'] // max(n_dest,1)} per dest  |  failed: {total_stats['readme_failed']}\n"
                    f"  Old EXE archived:  {total_stats['archived']}  |  skipped (locked): {total_stats['archive_skipped']}\n"
                    f"\nDetail:\n" + "\n".join(detail_lines) + f"\n{'='*40}"
                )
                if build_summary:
                    self.after(0, self._log, build_summary)
                self.after(0, self._log, summary)
            finally:
                self.after(0, self._set_busy, False)
                self.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Copy Manager")
    try:
        root.iconbitmap(str(_app_dir() / "icon.ico"))
    except Exception:
        pass
    root.geometry("980x650")
    root.minsize(900, 560)
    app = DeployGUI(root)
    app.pack(fill="both", expand=True)
    root.mainloop()