# Dev Tools — STRUCTURE

> Verified against source: 2026-08-05 · `dev_tools.py` 86 L · `b_t.py` 1176 L · `cm_t.py` 2117 L

## Files

| File | Description |
|------|-------------|
| `dev_tools.py` | Entry point. One window, `ttk.Notebook` with both tabs (Builder + Copy Manager) and a shared log. |
| `b_t.py` | **Builder** — PyInstaller build UI. Builds exes out of the py projects. |
| `cm_t.py` | **Copy Manager** — deployment UI. Copies finished exes to Scratch / SharePoint. |
| `builder_settings.json` | Builder settings (root folder, per-project group). |
| `build_usage.json` | Build history (gitignored). |
| `copy_manager_state.ini` | Last deployed version per program (gitignored). |
| `icon.ico` | Window / taskbar icon (`set_app_icon`). |

A line-by-line map is in `dev_tools_structure.md`.

---

## Paths

```
programy/
├── L3-QoL-JanJan/          ← git repo (sources)
│   ├── Dev Tools/           ← APP_DIR / BUILDER_DIR
│   ├── Image Tools/
│   └── ...
└── dist/                    ← PROGRAMY_DIST_DIR
    ├── Image Tools/
    │   └── v1.2.3/
    └── ...
```

- `PROGRAMY_DIR = BUILDER_DIR.parent` → `L3-QoL-JanJan/` (sources)
- `PROGRAMY_DIST_DIR = BUILDER_DIR.parent.parent / "dist"` → `programy/dist/` (exe output)
- `COPY_MANAGER_SRC_DIR` / `COPY_MANAGER_DIST_DIR` — used by "Run Copy Manager"

When Dev Tools runs **frozen**, `__file__` no longer points into the repo, so the
roots come from `%APPDATA%\DevTools\config.json` (`src_root`, `dist_root`,
`scratch`, `sharepoint`), editable from the **Set paths** dialog in either tab
(`_open_set_paths`). `_src_root()` / `_dist_root()` / `_programs_root()` /
`_get_scratch_root()` resolve config → fallback in that order.

### Versions.txt
`<scratch>/Versions.txt` is a flat `Name = vX.Y.Z` list, written by the Builder
after every successful build (`write_version_to_txt`) and read back by
`read_versions_txt()`. Both tabs carry their own copy of these two helpers.

---

## b_t.py — Builder

### Constants
- `APP_DIR`, `SETTINGS_PATH` (`builder_settings.json`), `USAGE_LOG` (`build_usage.json`)
- `_CONFIG_PATH` — `%APPDATA%\DevTools\config.json`
- `RE_VER` (`X.Y.Z`) / `RE_VDIR` (`vX.Y.Z` folder)
- `ALWAYS_IGNORE` — folders skipped while discovering projects (`dist`, `Matlab`,
  `Icons`, `.vscode`, `.venv`, `.git`)

### Functions
| Function | Description |
|----------|-------------|
| `parse_version_tuple(ver)` / `version_tuple_to_str(t)` | `"1.2.3"` ↔ `(1, 2, 3)` |
| `bump_patch(ver)` | `"1.2.3"` → `"1.2.4"` (default `"1.0.0"`) |
| `run(cmd, cwd)` | `subprocess.run` in a directory |
| `load_json` / `save_json` | JSON helpers |
| `_load_devtools_config` / `_save_devtools_config` | `%APPDATA%\DevTools\config.json` |
| `_versions_txt_path` / `read_versions_txt` / `write_version_to_txt` | `Versions.txt` on the scratch share |

### BuilderUI(ttk.Frame)
- `root_folder` — scan root (default `L3-QoL-JanJan/`), `dist_root` — `programy/dist/`
- `find_projects(root)` — folders containing a `.py`, skipping `_`-prefixed names
  and `ALWAYS_IGNORE`
- `guess_main_py(project_dir)` — entry point: `<name>.py` → `main.py` → `app.py` →
  the only `.py`
- `last_version_from_dist(name)` / `default_next_version_for_project(name)` /
  `effective_next_version_for_project(name)` — auto-bumped version or the manual override
- `sort_projects`, `_render_project_buttons` — list grouped Main / Side / Ignored
  (group per project persisted in `builder_settings.json`)
- Build: `_build_selected()` → thread → `_build_one_project(p, ver)` per project →
  `_on_build_finished(...)` → `on_build_done` callback → Copy Manager `auto_deploy`
- `_build_internal_builder_sync()` — builds the internal-libs builder when needed
- `_open_dist()`, `_open_readme(project_dir)` (creates one if missing), `_run_copy_manager()`
- The log is shared with the Copy Manager (`log_widget`), plus a local log box

### `_build_one_project()` steps
1. Resolve the entry `.py`; output dir `dist/<project>/v<version>/`
2. Delete an existing version folder first — PyInstaller `--noconfirm` fails on the
   read-only flags OneDrive leaves behind
3. Collect the extra `.py` files from the project root — **`test_*.py` is excluded**
   (dev-only, nothing imports it at runtime)
4. Read the optional `build_config.json`
5. Auto-detect imports of `numpy`, `scipy`, `sklearn`, `cv2`, `matplotlib`, `pandas`
   in the sources and add `--collect-all` for them (their native libs, e.g.
   `numpy._umath_linalg`, otherwise go missing)
6. `--collect-all` also drags in each package's own test suite, so the matching
   `<pkg>.tests` submodules are added to `--exclude-module`. Only `.tests` — never
   `.testing` / `._testing`, which some libraries use at runtime.
7. Run `py -m PyInstaller --onedir --windowed --noconfirm` with the icon, the extra
   `.py` files as `--add-data`, and every `collect_all` / `collect_binaries` /
   `hidden_imports` / `copy_metadata` / `exclude_modules` entry
8. Move the built app one level up into the version folder (`_internal` stays put)
9. Rename the exe to `<Name> v<version>.exe`, copy `icon.ico` next to it (tkinter's
   `iconbitmap` needs a real file)
10. Copy the main `.py` + the extra sources + `build_config.json → extra_files`
11. Clean the PyInstaller work dir in TEMP
12. `write_version_to_txt(name, ver)` and append to `build_usage.json`

### Per-project `build_config.json`
```json
{
  "collect_all":      ["module"],
  "collect_binaries": ["module"],
  "hidden_imports":   ["module"],
  "copy_metadata":    ["module"],
  "exclude_modules":  ["module"],
  "extra_files":      ["data.json"]
}
```

---

## cm_t.py — Copy Manager

### Paths and constants
- `_src_root()` / `_dist_root()` / `_programs_root()` / `_internal_builder_dist()`
  (prefers a local `C:\Dev\dist\_internal_builder` outside OneDrive)
- `_get_destination_roots()` — `Scratch` and `Sharepoint` from
  `%APPDATA%\DevTools\config.json`; nothing is hardcoded any more, and the
  **Set paths** dialog (`_open_set_paths`) writes them
- `VERSION_RE` / `_VERSION_LOOSE_RE` / `TIMESTAMPED_EXE_RE` / `_exe_version`
- `README_PREFIX` = `ReadMe_`, `README_NAME` = `ReadMe.txt`
- `STATE_FILE_NAME` = `copy_manager_state.ini`, `STATE_SECTION` = `deployed`

### State (INI)
```ini
[deployed]
Image Tools = v1.2.3
Launcher = v0.9.1
```
`load_state()` / `save_state(state)`; `is_newer_version(latest, deployed)` drives
the "new" highlight and **Select new**.

### Version helpers
| Function | Description |
|----------|-------------|
| `list_versions(dist_dir)` | `vX.Y.Z` folders, newest first |
| `find_exe_in_folder(ver_folder, name, ver)` | exact name → regex → name → first exe |
| `unique_path(p)` | appends `(2)`, `(3)`, … if the path exists |
| `_try_move(src, dst)` | move, reporting the WinError 32 "file locked" case |
| `_normalize_name(s)`, `parse_version`, `version_tuple_to_str` | |

### Archive maintenance (the **Fix** button, `_on_fix`)
The archive layout is "one folder per version, each a self-contained runnable
snapshot": `archive/vX.Y.Z/<Name> vX.Y.Z.exe` + its helper modules under their
**importable** names, no timestamps.

| Function | Description |
|----------|-------------|
| `_fix_archive_dir(archive_dir)` | moves flat files in `archive/` into `vX.Y.Z/` folders (versioned+timestamp, version-only, and timestamp-only `.py` matched to a versioned exe); per version keeps only the latest timestamped build |
| `_reunite_unknown_helpers(archive_dir)` | helper modules (`if_t.py`, `is_t.py`, …) carry no version, so old copies used to pile up in `archive/unknown/` as `if_t.py`, `if_t (2).py`, … Each copy's deploy timestamp is in `unknown/archive_log.txt` and the same timestamp is in the version folder's own `archive_log.txt`; that link is rebuilt (Nth copy ↔ Nth timestamp) and the file moves back under its original importable name |
| `_canonical_stem(stem)` / `_dup_index(stem)` | strip a `__YYYYMMDD_HHMMSS` timestamp and/or a ` (N)` dedup suffix |
| `_normalize_version_folder_names(archive_dir)` | per version folder, group files by canonical name, keep the best copy, rename it to the canonical name and delete the redundant twins |
| `move_existing_exes_to_archive(target_dir, keep_name, …)` | on deploy, moves every other exe into its `archive/vX.Y.Z/` folder; a locked exe is skipped, not fatal |

This is also what the Launcher relies on: it reads the version from the folder and
from the plain `Name vX.Y.Z.exe` filename.

### DeployGUI(ttk.Frame)
- State: `state_deployed`, `program_vars` / `program_version_vars` / `dest_vars`,
  `program_is_new`, `program_latest_version`, `internal_vars`
- `_load_programs()` — scan dist → versions → rows; the program column width is
  computed from the names (`_compute_program_col_px`)
- Selection: `_select_all_programs`, `_select_new_programs`, `_clear_programs`,
  `_get_selected_programs`, `_get_selected_destination_roots`
- Destinations: `_build_dest_rows`, `_reflow_dest_paths` / `_split_path_to_lines`
  (long UNC paths wrap instead of stretching the window)
- Deploy: `_collect_icon_conflicts()` → `show_icon_compare_dialog` per conflict →
  `_deploy_one_program(...)` per program → state + `Versions.txt` update
  (`_on_copy`), with `_progress_show/_update/_hide` and `_set_busy`
- `auto_deploy(built_projects, build_summary)` — called from the Builder: preselects
  the built projects and versions and can run straight away
- Extras: `_on_copy_readme_only()`, `_on_fix()`, `_on_build_internal()`,
  `_on_deploy_internal()` (zips `_internal/` from dist into the selected programs),
  `_change_root()`, `_refresh()`
- `ScrollableFrame` — the scrollable program list; `_build_summary(jobs, roots)` —
  the confirmation text

### `_deploy_one_program()` steps
1. Find the exe in the version folder, the ReadMe (required), the `.py` files, the
   icon and any extra files
2. Per destination root: create `<dst>/<program>/`, archive the old exe/py into
   `archive/vX.Y.Z/`, copy the new exe as `<program> <version>.exe`, copy the `.py`
   files, extras (except `_internal`), the ReadMe and the icon (a stale PNG is
   dropped when an ICO is copied; conflicts use the `icon_decisions` answers)

### Deploy output layout
```
<dst_root>/<program_name>/
  <program_name> <version>.exe
  *.py
  ReadMe_*.txt
  icon.ico
  archive/
    vX.Y.Z/
      <program_name> vX.Y.Z.exe
      if_t.py …            ← importable names, runnable snapshot
      archive_log.txt
```

---

## Dependencies
- `tkinter` / `ttk` — UI
- `PyInstaller` — invoked via subprocess (`py -m PyInstaller`)
- `PIL` (optional) — icon preview in the compare dialog
- `configparser` — Copy Manager state file
- `ctypes` — `set_app_icon` (taskbar icon)
