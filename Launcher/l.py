# l.py
import os
import re
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import json

# ---------------- CONFIG ----------------
_ONEDRIVE = Path.home() / "OneDrive - ELI Beamlines"
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
    ("Office - Programs",  _ONEDRIVE / "ELI Beamlines" / "Python" / "programy", False),
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

NOTES_LAB    = Path(r"\\hapls-share.lcs.local\scratch\Software\notes.txt")
NOTES_OFFICE = Path(r"\\hapls-share.cs.eli-beams.eu\scratch\Software\notes.txt")

VERSION_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")

ARCHIVE_EXE_RE = re.compile(
    r"^.+\s+v(\d+)\.(\d+)\.(\d+)__\d{8}_\d{6}\.exe$",
    re.IGNORECASE
)

TIMESTAMPED_EXE_RE = re.compile(
    r"^.+\s+v\d+\.\d+\.\d+__\d{8}_\d{6}\.exe$",
    re.IGNORECASE
)

def _archive_exe_version(p: Path) -> tuple:
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)__(\d{8})_(\d{6})", p.stem, re.IGNORECASE)
    if m:
        return tuple(map(int, m.groups()))
    return (0, 0, 0, 0, 0)

def _archive_exe_label(p: Path) -> str:
    m = re.search(r"(v\d+\.\d+\.\d+)__(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", p.stem, re.IGNORECASE)
    if m:
        ver = m.group(1)
        date = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
        time_ = f"{m.group(5)}:{m.group(6)}:{m.group(7)}"
        return f"{ver}  ({date}  {time_})"
    return p.stem

def scan_archive_versions(program_dir: Path) -> list[dict]:
    """Vrátí seznam archivních verzí seřazených od nejnovější."""
    archive_dir = program_dir / "archive"
    if not archive_dir.exists():
        return []
    exes = [p for p in archive_dir.glob("*.exe") if ARCHIVE_EXE_RE.match(p.name)]
    exes.sort(key=_archive_exe_version, reverse=True)
    return [
        {"exe_path": p, "label": _archive_exe_label(p)}
        for p in exes
    ]

README_PREFIX = "readme_"  # case-insensitive

def _exe_version(p: Path):
    m = VERSION_RE.search(p.stem)
    return tuple(map(int, m.groups())) if m else (0, 0, 0)

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


# ---------------- GROUPING CONFIG ----------------
# Global rules (same for ALL roots).
# Everything else (not listed) becomes "External".

SCRIPTS = {
    # folder names of programs that should be in "Scripts"
    "Image Tools",
    "Screenshots",
    "Time Converter",
    "Dev Tools",
    "Announcer",
}

PARTS = {
    "Image Finder",
    "Image Slider",
    "Shot finder",
    "Launcher",
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
        m = re.search(r"(v\d+\.\d+\.\d+)", p.stem, re.IGNORECASE)
        label = m.group(1) if m else p.stem
        result.append({"exe_path": p, "label": label})

    # Archivní verze
    result.extend(scan_archive_versions(program_dir))

    return result

def scan_programs(root: Path) -> dict[str, dict]:
    if not root.exists():
        raise FileNotFoundError(f"Software root not found: {root}")

    programs = {}
    for program_dir in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not program_dir.is_dir():
            continue
        if program_dir.name.lower() in IGNORE_DIR_NAMES:
            continue

        program_name = program_dir.name
        readme = find_readme_or_none(program_dir, program_name)

        # A) scratch layout: exe přímo ve složce programu
        exes_root = list(program_dir.glob("*.exe"))
        if exes_root:
            exes_root.sort(key=_exe_version, reverse=True)
            exe = exes_root[0]
            clean_exes = [p for p in exes_root if not TIMESTAMPED_EXE_RE.match(p.name)]
            programs[program_name] = {
                "exe_path": exe,
                "readme_path": readme,
                "label": program_name,
                "program_dir": program_dir,
                "icon_path": find_icon_for_program(program_dir, exe),
                "archive_versions": _build_version_list(clean_exes, program_dir),
            }
            continue

        # B) programy layout: ROOT\dist\<Program>\vX.Y.Z\*.exe
        dist_dir = root / "dist" / program_name
        vf = newest_version_folder(dist_dir)
        if not vf:
            continue
        exes_v = list(vf.glob("*.exe"))
        if not exes_v:
            # --onedir layout: exe je ve podsložce
            exes_v = list(vf.rglob("*.exe"))
        if not exes_v:
            continue
        exe = pick_exe(exes_v, program_name, vf.name)
        programs[program_name] = {
            "exe_path": exe,
            "readme_path": readme,
            "label": program_name,
            "program_dir": program_dir,
            "icon_path": find_icon_for_program(program_dir, exe),
            "archive_versions": _build_version_list([], program_dir),
        }

    return programs

def ui_label(s: str) -> str:
    return " ".join(s.replace("_", " ").split())

def find_icon_for_program(program_dir: Path, exe_path: Path) -> Path | None:
    """
    Preferuj:
      1) icon.png vedle exe
      2) icon.png v program root složce
      3) icon.gif (fallback pro Tk)
    """
    for name in ("icon.png", "Icon.png", "ICON.png", "icon.gif", "Icon.gif", "icon.ico", "Icon.ico"):
        p = exe_path.parent / name
        if p.exists():
            return p
    for name in ("icon.png", "Icon.png", "ICON.png", "icon.gif", "Icon.gif", "icon.ico", "Icon.ico"):
        p = program_dir / name
        if p.exists():
            return p
    return None

def clamp_label(text: str, max_len: int = 22) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


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

# ---------------- APP ----------------
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self._scan_gen = 0
        self._config = _load_config()
        self._root_options = _apply_config_to_root_options(self._config)
        self.style = ttk.Style(self)

        # Modern Windows theme (nejlíp vypadá na Win10/11)
        for t in ("vista", "xpnative"):
            if t in self.style.theme_names():
                self.style.theme_use(t)
                break

        # Jednotná typografie
        self.option_add("*Font", "SegoeUI 10")
        # --- fixed cell sizing + dynamic columns ---
        self._group_cols = 2
        self._group_min_cell_px = 260  # min šířka jedné "dlaždice" (uprav si)

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
            font=("Segoe UI", 9, "bold"),
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
        self.style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabel", font=("Segoe UI", 10))
        import sys
        if getattr(sys, "frozen", False):
            self.title(Path(sys.executable).stem)
        else:
            self.title("Software Launcher")
        try:
            import sys
            if getattr(sys, "frozen", False):
                _base = Path(sys.executable).resolve().parent
            else:
                _base = Path(__file__).resolve().parent
            self.iconbitmap(str(_base / "icon.ico"))
        except Exception:
            pass
        self.geometry("600x510")
        self.minsize(550, 430)

        self.programs: dict[str, dict] = {}
        self.current_root_label: str | None = None
        self.current_root_path: Path | None = None
        self._icon_cache: dict[str, tk.PhotoImage] = {}
        self._known_versions: dict[str, tuple] = {}   # program_name → (major, minor, patch)
        self._update_available: set[str] = set()       # programs with newer version on disk

        # no default selection
        self.root_choice = tk.IntVar(value=-1)  # bude obsahovat label z ROOT_OPTIONS

        self._build_ui()
        self._set_idle_state()
        self._schedule_version_poll()

    def _calc_group_cols(self) -> int:
        # kolik sloupců se vejde do aktuální šířky okna
        w = self.sf.inner.winfo_width()
        if w <= 50:
            return self._group_cols

        cols = max(1, w // self._group_min_cell_px)
        # nechceme extrém; 4 bohatě stačí
        return max(2, min(int(cols), 4))

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
                state="normal" if path is not None else "disabled",
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
        self._rebuild_buttons()
        self.status.configure(text=f"Source: {selected_root} | Found: {len(self.programs)} programs.")

        misplaced = find_misplaced_timestamped_exes(self.programs)
        if misplaced:
            self.after(200, lambda: prompt_move_misplaced(self, misplaced))

    def _run_cleanup(self):
        if not self.programs:
            messagebox.showinfo("Clean", "No programs loaded. Select a data source first.")
            return
        misplaced = find_misplaced_timestamped_exes(self.programs)
        if not misplaced:
            messagebox.showinfo("Clean", "No misplaced files found.")
            return
        prompt_move_misplaced(self, misplaced)

    def _rebuild_buttons(self):
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
            g = group_for_program(name)
            grouped[g].append((name, info))

        # layout: each group is its own LabelFrame, inside is a grid
        cols = self._calc_group_cols()

        any_group_shown = False
        for title, gkey in GROUP_ORDER:
            group_items = grouped.get(gkey, [])
            if not group_items:
                continue

            any_group_shown = True
            section = ttk.LabelFrame(self.sf.inner, text=f"{title}")
            section.pack(fill="x", padx=6, pady=(6, 0))

            grid = ttk.Frame(section)
            grid.pack(fill="x", padx=8, pady=6)

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
                            if key.lower().endswith(".ico"):
                                from PIL import Image, ImageTk
                                img = Image.open(key)
                                img = img.resize((24, 24), Image.LANCZOS)
                                self._icon_cache[key] = ImageTk.PhotoImage(img)
                            else:
                                self._icon_cache[key] = tk.PhotoImage(file=key)
                        except Exception:
                            self._icon_cache[key] = None
                    img = self._icon_cache.get(key)

                btn_style = "Update.Prog.TButton" if name in self._update_available else "Prog.TButton"
                btn_text = ui_label(info["label"]) + (" ↑" if name in self._update_available else "")
                btn = ttk.Button(
                    cell,
                    text=btn_text,
                    image=img,
                    compound="left",
                    style=btn_style,
                    width=18,
                    command=lambda n=name: self.launch(n),
                )
                btn.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 6))

                # --- sub_grid: 2×2 vpravo od hlavního tlačítka ---
                sub = ttk.Frame(cell)
                sub.grid(row=0, column=1, rowspan=2, sticky="nsew")
                sub.grid_columnconfigure(0, weight=1)
                sub.grid_columnconfigure(1, weight=1)

                # row 0: ReadMe přes celou šířku (nebo prázdný placeholder)
                if info.get("readme_path"):
                    info_btn = ttk.Button(
                        sub,
                        text="ReadMe",
                        style="Info.TButton",
                        command=lambda n=name: self.open_readme(n),
                    )
                    info_btn.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
                else:
                    ttk.Frame(sub, height=1).grid(row=0, column=0, columnspan=2)

                # row 1 vlevo: 📂 složka
                folder_btn = ttk.Button(
                    sub,
                    text="📂",
                    style="Info.TButton",
                    width=3,
                    command=lambda n=name: self.open_folder(n),
                )
                folder_btn.grid(row=1, column=0, sticky="ew", padx=(0, 2))

                # row 1 vpravo: ▾ verze (jen pokud existují)
                versions = info.get("archive_versions", [])
                if versions:
                    mb = ttk.Menubutton(
                        sub,
                        text="  🔽  ",
                        style="Info.TButton",
                        width=4,
                    )
                    menu = tk.Menu(mb, tearoff=0)
                    for v in versions:
                        menu.add_command(
                            label=v["label"],
                            command=lambda p=v["exe_path"], d=info["program_dir"]: self._launch_exe(p, d),
                        )
                    mb["menu"] = menu
                    mb.grid(row=1, column=1, sticky="ew")
                else:
                    ttk.Frame(sub, width=1).grid(row=1, column=1)

            for c in range(cols):
                grid.grid_columnconfigure(c, weight=0, uniform="grpcols")

        if not any_group_shown:
            ttk.Label(self.sf.inner, text="No programs to show.").pack(anchor="w", padx=8, pady=8)

    def _rebuild_radiobuttons(self):
        for i, (label, path, configurable) in enumerate(self._root_options):
            if i < len(self._rb_widgets):
                state = "normal" if path is not None else "disabled"
                self._rb_widgets[i].configure(state=state)

    # -------- version update polling --------

    def _schedule_version_poll(self):
        self.after(5000, self._poll_versions)

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
                self.programs[name]["exe_path"] = best_exe
                self.programs[name]["archive_versions"] = archive_vers
        changed = updates != self._update_available
        self._update_available = updates
        if changed:
            self._rebuild_buttons()
        self._schedule_version_poll()

    # -------- launch --------

    def launch(self, program_name: str):
        info = self.programs.get(program_name)
        if not info:
            messagebox.showerror("Not found", f"Program not found: {program_name}")
            return

        exe_path: Path = info["exe_path"]
        self.status.configure(text=f"Starting: {program_name} ...")

        def worker():
            try:
                os.startfile(str(exe_path))
                self.after(0, self.status.configure, {"text": f"Started: {program_name}"})
            except Exception as e:
                self.after(0, messagebox.showerror, "Launch failed", f"{program_name}\n\n{e}")
                self.after(0, self.status.configure, {"text": "Launch failed."})

        threading.Thread(target=worker, daemon=True).start()

    def _launch_exe(self, exe_path: Path, program_dir: Path):
        """Spustí konkrétní exe. Pokud je v archive/, přesune ho do hlavní složky."""
        target = exe_path
        if exe_path.parent.name.lower() == "archive":
            target = program_dir / exe_path.name
            if not target.exists():
                try:
                    exe_path.rename(target)
                except Exception as e:
                    messagebox.showerror("Move failed", f"Could not move file:\n{e}")
                    return

        self.status.configure(text=f"Starting: {target.name} ...")
        def worker():
            try:
                os.startfile(str(target))
                self.after(0, self.status.configure, {"text": f"Started: {target.name}"})
            except Exception as e:
                self.after(0, messagebox.showerror, "Launch failed", f"{target.name}\n\n{e}")
                self.after(0, self.status.configure, {"text": "Launch failed."})
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


if __name__ == "__main__":
    Launcher().mainloop()