---
name: Dev Tools structure map
description: Line-by-line map of dev_tools.py, b_t.py, cm_t.py — Builder + Copy Manager/Deploy tool. Read before editing.
---

Dev Tools — tkinter multi-tab: Builder (b_t.py) + Copy Manager/Deploy (cm_t.py)
Files: `dev_tools.py` (57 L) | `b_t.py` (~870 L) | `cm_t.py` (~1360 L)

---

## dev_tools.py

Thin launcher. Imports BuilderUI from b_t.py and DeployGUI from cm_t.py.

| Line | Name | Note |
|------|------|------|
| L10 | `_app_dir()` | frozen exe parent or `__file__` parent |
| L16 | `main()` | root window, icon, 950×780, creates DeployGUI + BuilderUI tabs, wires `on_build_done` callback |

**Build-done wiring:** Builder calls `on_build_done(built_projects, build_summary)` → switches to CM tab → `cm_tab.auto_deploy(built_projects, build_summary)`.

---

## b_t.py — Builder tab

### Module-level constants

| Line | Name | Value |
|------|------|-------|
| L16 | `APP_DIR` | script/exe directory |
| L17 | `SETTINGS_PATH` | `APP_DIR / "builder_settings.json"` |
| L18 | `USAGE_LOG` | `APP_DIR / "build_usage.json"` |
| L20 | `BUILDER_DIR` | parent of APP_DIR |
| L21 | `PROGRAMY_DIR` | parent of BUILDER_DIR (= `programy/`) |
| L22–25 | `COPY_MANAGER_SRC_DIR`, `COPY_MANAGER_DIST_DIR` | CM source + dist paths |
| L27–28 | `RE_VER`, `RE_VDIR` | regex: `X.Y.Z` and `vX.Y.Z` folder names |
| L30–32 | `ALWAYS_IGNORE` | dirs skipped during project discovery: dist, Matlab, Icons, .vscode, .venv |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L34 | `parse_version_tuple(s)` | `"1.2.3"` → `(1,2,3)` or None |
| L40 | `version_tuple_to_str(t)` | `(1,2,3)` → `"1.2.3"` |
| L44 | `bump_patch(s)` | increment patch; default `"1.0.0"` |
| L52 | `run(cmd, cwd)` | `subprocess.run` in given directory |
| L56 | `load_json(path, default)` | load JSON with fallback |
| L65 | `save_json(path, data)` | write JSON, indent=2, UTF-8 |

### Class: BuilderUI  (ttk.Frame)

#### Init & state

| Line | Method | Note |
|------|--------|------|
| L69 | `__init__` | loads settings + usage, discovers projects, builds UI |

**Key instance attrs:**
- `root_folder`, `dist_root` — source and dist dirs
- `selected_project` — currently focused project (Path or None)
- `projects_all`, `projects_sorted` — discovered projects
- `project_checks` — `{name: BooleanVar}` — checkbox state per project
- `project_rows` — `{name: Frame}` — row widget per project
- `project_next_override` — `{name: str}` — manual version overrides
- `project_next_labels` — `{name: StringVar}` — displayed next version
- `project_groups` — `{name: "Main"|"Side"|"Ignored"}` — grouping

#### Discovery & version logic

| Line | Method | Note |
|------|--------|------|
| L156 | `guess_main_py(project_dir)` | finds entry point: `<name>.py`, `main.py`, `app.py` |
| L176 | `find_projects(root)` | scans root; dirs with .py file, skips `_` prefix + ALWAYS_IGNORE |
| L192 | `last_version_from_dist(project_name)` | finds latest `vX.Y.Z` in dist; returns `(version_str, is_new)` |
| L216 | `effective_next_version_for_project(name)` | manual override or auto-bumped version |

#### UI build

| Line | Method | Note |
|------|--------|------|
| L234 | `_build_ui()` | root folder selector, scrollable project list, detail panel, action buttons, local log |
| L339 | `_render_project_buttons()` | grouped list: Main / Side / Ignored, version labels per row |

#### Root folder + reload

| Line | Method | Note |
|------|--------|------|
| L421 | `_change_root()` | folder dialog → save to settings → reload |
| L438 | `_reload_projects()` | re-discover + re-render |

#### Project selection & grouping

| Line | Method | Note |
|------|--------|------|
| L458 | `_select_project(name)` | update detail fields (name, main.py, versions, group) |
| L482 | `_on_group_changed()` | update group classification + persist |
| L499 | `_on_next_version_edited()` | track manual version overrides |

#### Build logic

| Line | Method | Note |
|------|--------|------|
| L544 | `_build_one_project(project, version, log_fn)` | **core build** (see below) |
| L690 | `_on_build_finished(results, build_summary)` | callback after thread; reload + summary + optional auto-deploy |
| L736 | `_build_selected()` | get checked projects, validate versions, launch thread |

**`_build_one_project()` steps:**
1. Find entry point `.py`
2. Create output dir: `dist/<project>/v<version>/`
3. Read optional `build_config.json` → `collect_all`, `hidden_imports`
4. PyInstaller command: `--onedir --windowed --noconfirm`, icon.ico, extra .py as `--add-data`, hidden imports
5. Rename exe: `<name>.exe` → `<name> v<version>.exe`
6. Copy main .py + extra .py files to version folder
7. Clean PyInstaller workdir (TEMP)
8. Log to `build_usage.json`

**Optional per-project config file: `build_config.json`**
```json
{ "collect_all": ["module_name"], "hidden_imports": ["module_name"] }
```

#### Other actions

| Line | Method | Note |
|------|--------|------|
| L109 | `_log(msg)` | append to local log + shared CM log |
| L148 | `_clear_local_log()` | clear local log |
| L790 | `_open_dist()` | open dist folder in Explorer |
| L794 | `_open_readme()` | find + open ReadMe (or create) |
| L824 | `_run_copy_manager()` | launch CM exe from dist or .py from source |

---

## cm_t.py — Copy Manager / Deploy tab

### Module-level constants

| Line | Name | Value |
|------|------|-------|
| L15 | `PROGRAMS_ROOT` | `C:\...\programy` (hardcoded source) |
| L18–21 | `DESTINATION_ROOTS` | `[("Scratch", Z:\Software), ("Sharepoint", C:\...\QoL)]` |
| L24 | `SOFTWARE_ROOT` | `Z:\Software` |
| L25 | `INTERNAL_BUILDER_DIST` | dist path for internal builder |
| L26 | `VERSION_RE` | regex for `vX.Y.Z` |
| L30–31 | `STATE_FILE_NAME`, `STATE_SECTION` | `"copy_manager_state.ini"`, `"[deployed]"` |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L35 | `_app_dir()` | frozen or source directory |
| L42 | `_state_path()` | path to state INI file |
| L45 | `load_state()` | load INI → `{program_name.lower(): "vX.Y.Z"}` |
| L65 | `save_state(state)` | persist state to INI |
| L78 | `_try_move(src, dst)` | move file; detect WinError 32 (locked) |
| L99 | `parse_version(s)` | `"vX.Y.Z"` → `(X,Y,Z)` |
| L104 | `version_tuple_to_str(t)` | `(X,Y,Z)` → `"vX.Y.Z"` |
| L108 | `list_versions(program_dir)` | sorted desc list of `vX.Y.Z` folders |
| L122 | `find_exe_in_folder(folder, name, ver)` | locate .exe: exact → regex → name → first |
| L151 | `unique_path(p)` | add `(2)`, `(3)`… if path exists |
| L163 | `_load_ico_as_photoimage(path)` | PIL load → tk.PhotoImage (64×64 RGBA) |
| L171 | `show_icon_compare_dialog(...)` | modal: compare old vs new icon size → bool (replace?) |
| L235 | `move_existing_exes_to_archive(folder, keep_name)` | archive old exes with timestamp |
| L260 | `find_readme_or_raise(program_dir, name)` | find ReadMe file; raises if missing |
| L286 | `copy_readme_with_overwrite_notice(src, dst, log_fn)` | copy readme, warn on overwrite |
| L294 | `is_newer_version(latest, deployed)` | compare tuples → bool |

### Class: ScrollableFrame  (ttk.Frame, L305)

Scrollable container with mousewheel support used in program list.

### Class: DeployGUI  (ttk.Frame, L339)

#### Init & state

| Line | Method | Note |
|------|--------|------|
| L339 | `__init__` | loads state, inits vars, builds UI, loads programs |

**Key instance attrs:**
- `state_deployed` — `{name.lower(): "vX.Y.Z"}` from INI
- `program_vars` — `{Path: BooleanVar}` — checkbox per program
- `program_version_vars` — `{Path: StringVar}` — selected version
- `dest_vars` — `{Path: BooleanVar}` — destination checkbox
- `program_is_new` — `{Path: bool}` — latest > deployed?
- `program_latest_version` — `{Path: str}` — latest version string
- `internal_vars` — "Deploy internal libs" checkbox per program

#### UI build & layout

| Line | Method | Note |
|------|--------|------|
| L363 | `_build_ui()` | programs root label, left panel (program list), right panel (destinations), action buttons, progress, log |
| L611 | `_load_programs()` | scan PROGRAMS_ROOT → dist → versions → render rows |

#### Selection helpers

| Line | Method | Note |
|------|--------|------|
| L706 | `_select_all_programs()` | check all |
| L714 | `_select_new_programs()` | check only programs with newer version |
| L720 | `_clear_programs()` | uncheck all |
| L797 | `_get_selected_programs()` | returns checked program Paths |
| L801 | `_get_selected_destination_roots()` | returns checked dest Paths |

#### Deploy logic

| Line | Method | Note |
|------|--------|------|
| L728 | `_collect_icon_conflicts()` | scan (program, version, dest) tuples; show compare dialog for each icon conflict |
| L824 | `_deploy_one_program(program_dir, version_name, dst_roots, icon_decisions, log_fn)` | **core deploy** (see below) |
| L1034 | `_on_copy_readme_only()` | copy only ReadMe to selected destinations |
| L1066 | `_on_fix_icons()` | scan dst folders, remove stale PNGs, copy icon.ico from source |
| L1121 | `_on_build_internal()` | build `_internal_builder.py` with PyInstaller |
| L1170 | `_on_deploy_internal()` | zip `_internal/` from dist, extract into selected program `_internal/` folders |
| L1280 | `_on_copy()` | **main deploy workflow**: collect conflicts → deploy each → update state → log stats |

**`_deploy_one_program()` steps:**
1. Find .exe in version folder
2. Find ReadMe (required, raises if missing)
3. Find all .py files in version folder
4. Find icon (.ico/.png/.gif) in version folder or program dir
5. Find extra non-.exe/.py files in version folder
6. For each destination root:
   - Create `<dst_root>/<program_name>/`
   - Archive old exes/py to `archive/<name>__<timestamp>.<ext>`
   - Copy new exe as `<program_name> <version>.exe`
   - Copy .py files
   - Copy extra files/folders (excluding `_internal`)
   - Copy ReadMe
   - Handle icon (skip stale PNG if copying ICO; use icon_decisions dict for conflicts)

#### Auto-deploy from Builder

| Line | Method | Note |
|------|--------|------|
| L540 | `auto_deploy(built_projects, build_summary)` | pre-select built projects + versions; auto-run if destinations checked |

#### Logging & busy state

| Line | Method | Note |
|------|--------|------|
| L575 | `_log(msg)` | append to log with autoscroll |
| L609 | `_clear_log()` | clear |
| L803 | `_set_busy(busy)` | enable/disable buttons + show/hide progress bar |

---

## Data structures & flows

### Project discovery
- Dir in `PROGRAMY_DIR` with at least one `.py` file
- Entry point: `<name>.py` > `main.py` > `app.py`
- Optional `build_config.json` for PyInstaller extras

### Build output layout
```
dist/<project_name>/
  v<version>/
    <project_name> v<version>.exe
    <project_name> v<version>.py   ← copy of main entry
    *.py                            ← extra py files from project root
    _internal/                      ← PyInstaller libs
    icon.ico / other extras
```

### Deploy output layout
```
<dst_root>/<program_name>/
  <program_name> <version>.exe
  *.py
  ReadMe_*.txt
  icon.ico
  archive/
    <old_name>__<YYYYMMDD_HHMMSS>.exe
```

### Configuration files
| File | Location | Purpose |
|------|----------|---------|
| `builder_settings.json` | APP_DIR | root_folder, project_groups |
| `build_config.json` | per project dir | collect_all, hidden_imports |
| `build_usage.json` | APP_DIR | build history (timestamp, project) |
| `copy_manager_state.ini` | APP_DIR | deployed versions `[deployed]` name → vX.Y.Z |
| `icon.ico` | per project dir | app icon; bundled in exe + copied to dist |

### DESTINATION_ROOTS (hardcoded in cm_t.py L18–21)
```python
DESTINATION_ROOTS = [
    ("Scratch",     Path(r"Z:\Software")),
    ("Sharepoint",  Path(r"C:\Users\jan.moucka\OneDrive - ELI Beamlines\L3-HAPLS\General\QoL")),
]
```
To add a new destination: add entry to this list.
