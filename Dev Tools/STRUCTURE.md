# Dev Tools — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `b_t.py` | **Builder** — PyInstaller build UI. Sestavuje exe z py projektů. |
| `cm_t.py` | **Copy Manager** — deployment UI. Kopíruje hotové exe na Scratch / Sharepoint. |
| `dev_tools.py` | Entry point. Spustí kombinované okno s oběma panely (Builder + Copy Manager). |

---

## Cesty (layout po přesunu do gitu)

```
programy/
├── L3-QoL-JanJan/          ← git repozitář (zdrojáky)
│   ├── Dev Tools/           ← APP_DIR / BUILDER_DIR
│   │   ├── b_t.py
│   │   ├── cm_t.py
│   │   └── dev_tools.py
│   ├── Image Tools/
│   ├── Launcher/
│   └── ...
└── dist/                    ← PROGRAMY_DIST_DIR = APP_DIR.parent.parent / "dist"
    ├── Image Tools/
    │   └── v1.2.3/
    └── ...
```

- `PROGRAMY_DIR = BUILDER_DIR.parent` → `L3-QoL-JanJan/` (zdrojáky)
- `PROGRAMY_DIST_DIR = BUILDER_DIR.parent.parent / "dist"` → `programy/dist/` (exe výstupy)
- `COPY_MANAGER_SRC_DIR = BUILDER_DIR.parent.parent / "Copy manager"` (pokud existuje mimo git)

---

## b_t.py — Builder

### Konstanty
- `SETTINGS_PATH` — `Dev Tools/builder_settings.json` — nastavení Builderu (root folder, projekt skupiny)
- `USAGE_LOG` — `Dev Tools/build_usage.json` — log buildů (v .gitignore)
- `ALWAYS_IGNORE` — složky vždy ignorované při skenování projektů: `dist`, `Matlab`, `Icons`, `.vscode`, `.venv`, `.git`

### Funkce
| Funkce | Popis |
|--------|-------|
| `parse_version_tuple(ver)` | `"1.2.3"` → `(1, 2, 3)` |
| `bump_patch(ver)` | `"1.2.3"` → `"1.2.4"` |
| `run(cmd, cwd)` | Spustí shell příkaz |
| `load_json` / `save_json` | JSON helpers |

### BuilderUI(ttk.Frame)
- `root_folder` — kořen pro skenování projektů (výchozí: `L3-QoL-JanJan/`)
- `dist_root` — výstupní složka pro exe (`programy/dist/`)
- `find_projects(root)` — vrátí seznam složek s `.py` souborem
- `guess_main_py(project_dir)` — najde hlavní `.py` (hledá: `<name>.py`, `main.py`, `app.py`, jediný `.py`)
- `last_version_from_dist(name)` — přečte nejvyšší `vX.Y.Z` složku z `dist/`
- `default_next_version_for_project(name)` → `(next_ver, last_ver, is_new)`
- `effective_next_version_for_project(name)` — vrátí override nebo vypočítanou verzi
- Build proces: `PyInstaller --onefile` (nebo `--onedir`), výstup do `dist/<Name>/vX.Y.Z/`
- Log widget sdílený s Copy Managerem

---

## cm_t.py — Copy Manager

### Cesty (dynamické)
```python
def _src_root() -> Path:   # L3-QoL-JanJan/  (frozen: exe.parent.parent)
def _dist_root() -> Path:  # programy/dist/
PROGRAMS_ROOT = _src_root()
INTERNAL_BUILDER_DIST = _dist_root() / "Internal Builder"
```

### Konstanty
- `DESTINATION_ROOTS` — cílové kořeny pro deployment:
  - `Scratch` → `Z:\Software`
  - `Sharepoint` → `C:\Users\...\OneDrive - ELI Beamlines\L3-HAPLS\General\QoL`
- `VERSION_RE` — `v(\d+)\.(\d+)\.(\d+)`
- `STATE_FILE_NAME` — `copy_manager_state.ini` (v .gitignore) — sleduje naposledy nasazenou verzi

### State (INI soubor)
```ini
[deployed]
Image Tools = v1.2.3
Launcher = v0.9.1
```
- `load_state()` / `save_state(state)` — čte/zapisuje stav

### Logika verzí
| Funkce | Popis |
|--------|-------|
| `list_versions(dist_dir)` | Vrátí seznam `vX.Y.Z` složek seřazených DESC |
| `find_exe_in_folder(ver_folder, name, ver)` | Najde exe v dané verzi (onefile i onedir layout) |
| `unique_path(p)` | Pokud cesta existuje, přidá `(2)`, `(3)`, ... |

### CopyManagerUI (hlavní třída)
- Zobrazuje seznam programů z `dist/`
- Pro každý program: aktuální nasazená verze, dostupné verze k nasazení
- Deployment: zkopíruje exe + ikonu + README do `DESTINATION_ROOTS`
- Pokud se změní ikona → `show_icon_compare_dialog` (porovnání vizuálů)
- Sdílí log widget s Builderem

### dev_tools.py — Entry point
- Vytvoří jedno okno s `QTabWidget` nebo rozděleným `ttk.Frame`
- Vlevo: BuilderUI, vpravo: CopyManagerUI
- Sdílený log widget pro oba nástroje

---

## Závislosti
- `tkinter` — UI
- `PyInstaller` — volán přes subprocess
- `PIL` (volitelné) — pro náhled ikon v dialogu
- `configparser` — state soubor Copy Manageru
