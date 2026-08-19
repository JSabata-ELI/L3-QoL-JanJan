---
name: Dev Tools structure map
description: Line-by-line map of dev_tools.py, b_t.py, cm_t.py — Builder + Copy Manager/Deploy tool. Read before editing.
---

Dev Tools — tkinter two-tab app: Builder (`b_t.py`) + Copy Manager / Deploy (`cm_t.py`)
Files (verified 2026-08-19): `dev_tools.py` (86 L) | `b_t.py` (1215 L) | `cm_t.py` (2128 L)

Line numbers are anchors — they drift. Re-grep the symbol if an offset looks wrong.

---

## dev_tools.py

Thin launcher. Imports `BuilderUI` from `b_t.py` and `DeployGUI` from `cm_t.py`.

| Line | Name | Note |
|------|------|------|
| L10 | `_app_dir()` | frozen exe parent or `__file__` parent |
| L17 | `set_app_icon(win, ico, app_id)` | title bar + **taskbar** icon: `iconbitmap` only fixes WM_BIG, so ICON_SMALL/SMALL2 and the window-class icons are forced via Win32 (otherwise Windows shows the Tk feather) |
| L51 | `main()` | root window, icon, 950×780, `ttk.Notebook`, `DeployGUI` first (it owns the shared log), then `BuilderUI(log_widget=cm_tab.log)`, cross refs `_cm_ref` / `_builder_ref` |

**Build-done wiring:** Builder calls `on_build_done(built_projects, build_summary)`
→ selects the CM tab → `cm_tab.auto_deploy(built_projects, build_summary)`.

---

## b_t.py — Builder tab

### Module-level constants

| Line | Name | Value |
|------|------|-------|
| L16-18 | `APP_DIR`, `SETTINGS_PATH`, `USAGE_LOG` | `builder_settings.json`, `build_usage.json` |
| L21 | `_CONFIG_PATH` | `%APPDATA%\DevTools\config.json` |
| L73-80 | `BUILDER_DIR`, `PROGRAMY_DIR`, `PROGRAMY_DIST_DIR`, `COPY_MANAGER_SRC_DIR`, `COPY_MANAGER_DIST_DIR` | source + dist roots |
| L82-83 | `RE_VER`, `RE_VDIR` | `X.Y.Z` / `vX.Y.Z` |
| L85 | `ALWAYS_IGNORE` | dist, Matlab, Icons, .vscode, .venv, .git |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L23/31 | `_load_devtools_config` / `_save_devtools_config` | `%APPDATA%\DevTools\config.json` |
| L38/45/59 | `_versions_txt_path` / `read_versions_txt` / `write_version_to_txt` | `<scratch>/Versions.txt`, flat `Name = vX.Y.Z` |
| L89/96/100 | `parse_version_tuple` / `version_tuple_to_str` / `bump_patch` | version math |
| L107 | `run(cmd, cwd)` | `subprocess.run` |
| L111/120 | `load_json` / `save_json` | JSON with fallback |

### Class: BuilderUI (ttk.Frame, L123)

| Line | Method | Note |
|------|--------|------|
| L124 | `__init__` | loads settings + usage, discovers projects, builds UI |
| L172 | `_log(msg)` | local log + shared CM log |
| L198-211 | `_on_local_log_scroll` / `_on_local_log_scrollbar_release` / `_check_local_log_position` / `_clear_local_log` | autoscroll only while pinned to the bottom |

**Key instance attrs:** `root_folder`, `dist_root`, `selected_project`,
`projects_all` / `projects_sorted`, `project_checks` `{name: BooleanVar}`,
`project_rows`, `project_next_override`, `project_next_labels`, `project_groups`
(`Main`/`Side`/`Ignored project`).

#### Discovery & version logic

| Line | Method | Note |
|------|--------|------|
| L219 | `guess_main_py(project_dir)` | `<name>.py` → `main.py` → `app.py` → single `.py` |
| L246 | `find_projects(root)` | dirs with a `.py`, skipping `_`-prefixed + `ALWAYS_IGNORE` |
| L262 | `last_version_from_dist(name)` | newest `vX.Y.Z` in dist |
| L294/299/302 | `default_next_version_for_project` / `effective_next_version_for_project` / `reset_next_version_overrides` | auto-bump vs manual override |
| L308 | `sort_projects` | display order |

#### UI

| Line | Method | Note |
|------|--------|------|
| L312 | `_build_ui()` | root selector, scrollable project list, detail panel, action buttons, local log |
| L418 | `_render_project_buttons()` | grouped list Main / Side / Ignored + per-row version labels |
| L481/494 | `_update_focus_styles` / `_refresh_project_list_version_labels` | |
| L500/527/602 | `_change_root` / `_open_set_paths` / `_reload_projects` | roots (config-backed when frozen) + rescan |
| L622/646/660/663/672 | `_select_project` / `_on_group_changed` / `_on_project_check_clicked` / `_bind_next_version_trace` / `_on_next_version_edited` | selection, grouping, version override tracking |
| L690/694/698 | `_select_all_projects` / `_clear_all_projects` / `_get_checked_projects` | |

#### Build

| Line | Method | Note |
|------|--------|------|
| L707 | `_build_internal_builder_sync(live_log)` | builds `_internal_builder` when it is missing |
| **L735** | `_build_one_project(p, ver, live_log)` | **core build** (steps below) |
| L990 | `_set_build_buttons_enabled(enabled)` | busy state |
| L994 | `_on_build_finished(...)` | reload + summary + optional auto-deploy |
| L1040 | `_build_selected()` | validate versions, launch the build thread |
| L1097/1101/1131 | `_open_dist` / `_open_readme` / `_run_copy_manager` | |

**`_build_one_project()` steps**
1. entry `.py`; output `dist/<project>/v<version>/`
2. delete an existing version folder first (PyInstaller `--noconfirm` trips over the
   read-only flags OneDrive leaves behind — `_on_rm_error` clears them)
3. extra `.py` files from the project root, **excluding `test_*.py`** (dev-only)
4. `build_config.json` → `collect_all`, `collect_binaries`, `hidden_imports`,
   `copy_metadata`, `exclude_modules`, `extra_files`
5. **auto `--collect-all`** (L806 `_AUTO_COLLECT`) when the sources import numpy /
   scipy / sklearn / cv2 / matplotlib / pandas — their native libs otherwise go missing
6. **auto `--exclude-module`** (L826 `_TEST_SUBMODULES`) for those packages' own
   `.tests` submodules that `--collect-all` drags in. Only `.tests`, never
   `.testing` / `._testing` (public runtime helpers)
7. `py -m PyInstaller --onedir --windowed --noconfirm` + icon + `--add-data` per extra `.py`
8. move the built app one level up into the version folder (`_internal` stays)
9. rename exe → `<Name> v<version>.exe`, copy `icon.ico` next to it
10. copy the main `.py`, the extra sources, and `extra_files` (folders too —
    recursively, minus `__pycache__` / `Thumbs.db`)
11. **auto asset folders**: `images` / `sounds` / `assets` / `icons` are copied
    whenever the project has them, config or not. They are read from next to the
    exe at runtime, so a build without them breaks only once it is deployed
    (Announcer's alarm image is `images/scorpion_orig.png`)
12. copy the project's ReadMe into the version folder — `cm_t.py` looks for it
    there first and otherwise keeps the copy already on the destination, so
    skipping this leaves the published ReadMe frozen forever
13. remove the TEMP work dir only
14. `write_version_to_txt(name, ver)` + append to `build_usage.json`, then log the
    resulting folder listing

---

## cm_t.py — Copy Manager / Deploy tab

### Module-level constants & path resolution

| Line | Name | Value |
|------|------|-------|
| L22 | `_CONFIG_PATH` | `%APPDATA%\DevTools\config.json` |
| L24/32 | `_load_devtools_config` / `_save_devtools_config` | |
| L39 | `_get_destination_roots()` | `[("Scratch", …), ("Sharepoint", …)]` **from the config** — no hardcoded paths |
| L50/59/66/73 | `_src_root` / `_dist_root` / `_internal_builder_dist` / `_programs_root` | config override → fallback; internal builder prefers local `C:\Dev\dist\_internal_builder` |
| L97/102/106/120 | `_get_scratch_root` / `_versions_txt_path` / `read_versions_txt` / `write_version_to_txt` | `Versions.txt` |
| L134-140 | `VERSION_RE`, `_VERSION_LOOSE_RE`, `TIMESTAMPED_EXE_RE`, `_exe_version` | |
| L144-148 | `README_PREFIX`, `README_NAME`, `STATE_FILE_NAME`, `STATE_SECTION` | |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L152/158 | `_app_dir` / `set_app_icon` | frozen-aware dir, taskbar icon |
| L192/196/216 | `_state_path` / `load_state` / `save_state` | `copy_manager_state.ini` `[deployed]` |
| L229/233 | `_is_locked_winerror32` / `_try_move` | locked-file aware move |
| L250/255/259 | `parse_version` / `version_tuple_to_str` / `list_versions` | `vX.Y.Z` folders, desc |
| L273 | `find_exe_in_folder` | exact → regex → name → first |
| L302 | `unique_path` | ` (2)`, ` (3)`, … |
| L314/322 | `_load_ico_as_photoimage` / `show_icon_compare_dialog` | modal old-vs-new icon compare → replace? |
| **L386** | `_fix_archive_dir` | flat files in `archive/` → `vX.Y.Z/` folders; per version keeps only the latest timestamped build |
| **L493** | `_reunite_unknown_helpers` | moves helper `.py` copies out of `archive/unknown/` back into their version folder under the ORIGINAL importable name, matching Nth copy ↔ Nth timestamp via the two `archive_log.txt` files |
| L597-617 | `_DUP_SUFFIX_RE`, `_TS_SUFFIX_RE`, `_canonical_stem`, `_dup_index` | strip `__YYYYMMDD_HHMMSS` and ` (N)` |
| **L622** | `_normalize_version_folder_names` | per version folder: group by canonical name, keep the base copy, rename to the canonical name, delete duplicates — so every folder is a clean runnable snapshot the Launcher can read |
| L677 | `move_existing_exes_to_archive` | on deploy, archive every other exe; a locked exe is skipped, not fatal |
| L711/715/737 | `_normalize_name` / `find_readme_or_raise` / `copy_readme_with_overwrite_notice` | ReadMe handling |
| L745 | `is_newer_version(latest, deployed)` | drives the "new" highlight |
| L756 | `ScrollableFrame` | mousewheel-aware program list container |
| L780 | `_build_summary(jobs, roots)` | confirmation text |

### Class: DeployGUI (ttk.Frame, L789)

| Line | Method | Note |
|------|--------|------|
| L790 | `__init__` | loads state, builds UI, loads programs |
| L813-857 | `_update_programs_root_wraplength`, `_split_path_to_lines`, `_reflow_dest_paths`, `_on_dest_configure`, `_build_dest_rows` | long UNC paths wrap instead of stretching the window |
| L890/940 | `_open_set_paths` / `_change_root` | config-backed roots |
| L961 | `_build_ui()` | left program list, right destinations, action buttons, progress, log |
| L1073/1081 | `_refresh` / `auto_deploy(built_projects, build_summary)` | Builder hand-off |
| L1116-1152 | `_log`, `_clear_log`, `_on_log_scroll`, `_on_log_scrollbar_release`, `_check_log_position`, `_compute_program_col_px` | log + layout |
| L1159 | `_load_programs()` | dist scan → versions → rows |
| L1252/1256/1270 | `_select_all_programs` / `_select_new_programs` / `_clear_programs` | |
| **L1274** | `_on_fix()` | runs `_fix_archive_dir` + `_reunite_unknown_helpers` + `_normalize_version_folder_names` over the selected programs' archives |
| L1365 | `_collect_icon_conflicts(...)` | one compare dialog per (program, version, dest) icon conflict |
| L1431/1434/1439 | `_get_selected_programs` / `_get_selected_destination_roots` / `_set_busy` | |
| L1443-1455 | `_progress_show` / `_progress_update` / `_progress_hide` | |
| **L1460** | `_deploy_one_program(program_dir, version_name, destination_roots, live_log, icon_decisions)` | **core deploy** |
| L1738/1796/1845 | `_on_copy_readme_only` / `_on_build_internal` / `_on_deploy_internal` | ReadMe-only copy; build `_internal_builder`; zip + extract `_internal/` into the selected programs |
| **L2021** | `_on_copy(build_summary)` | main workflow: collect conflicts → deploy each → update state + `Versions.txt` → log stats |

**`_deploy_one_program()` steps**
1. exe in the version folder, ReadMe, `.py` files, icon, extras — **files and
   folders**, `_internal` excluded
2. per destination root: create `<dst>/<program>/`, archive the old exe/py into
   `archive/vX.Y.Z/`, copy the new exe as `<program> <version>.exe`, copy the `.py`
   files, the extras, the ReadMe, and the icon (a stale PNG is dropped when an ICO
   is copied; conflicts follow `icon_decisions`)
3. remove leftover folders on the destination — except `_internal`, `archive`, and
   the names in `incoming_dirs`

**ReadMe lookup order** (L1493) — version folder → **source folder in the repo**
→ `dist/<program>/` → `<destination>/<program>/` → warn and skip. Step 2 was added
because builds predating the Builder's ReadMe copy have none in the version folder,
and the deploy then shipped no ReadMe at all. `_on_copy_readme_only` (L1767) uses
the same order.

**`incoming_dirs`** (L1530-1548) — the folder names the new version brings. The
stale-folder cleanup at L1581 must skip them, or an app's runtime assets
(`Announcer/images`, `Announcer/sounds`) are deleted and nothing re-creates them.

---

## Data structures & flows

### Project discovery
- A dir under `PROGRAMY_DIR` with at least one `.py`
- Entry point `<name>.py` > `main.py` > `app.py`
- Optional `build_config.json` for PyInstaller extras

### Build output layout
```
dist/<project_name>/
  v<version>/
    <project_name> v<version>.exe
    <project_name> v<version>.py   ← copy of the entry point
    *.py                            ← extra sources (no test_*.py)
    _internal/                      ← PyInstaller libs
    icon.ico / extra_files
    images/ sounds/ assets/ icons/  ← auto-detected asset folders
    ReadMe_*.txt                    ← copied so the deploy can publish it
```

### Deploy output layout
```
<dst_root>/<program_name>/
  <program_name> <version>.exe
  *.py
  ReadMe_*.txt
  icon.ico
  images/ sounds/ assets/ icons/   ← whatever the build brought
  archive/
    vX.Y.Z/
      <program_name> vX.Y.Z.exe
      if_t.py, is_t.py …            ← importable names → runnable snapshot
      archive_log.txt
```

### Configuration files
| File | Location | Purpose |
|------|----------|---------|
| `builder_settings.json` | `APP_DIR` | `root_folder`, `project_groups` |
| `build_config.json` | per project dir | collect_all, collect_binaries, hidden_imports, copy_metadata, exclude_modules, extra_files |
| `build_usage.json` | `APP_DIR` | build history |
| `copy_manager_state.ini` | `APP_DIR` | deployed versions `[deployed] name = vX.Y.Z` |
| `config.json` | `%APPDATA%\DevTools\` | `src_root`, `dist_root`, `scratch`, `sharepoint` |
| `Versions.txt` | `<scratch>` | `Name = vX.Y.Z` per program, written after each build/deploy |
| `icon.ico` | per project dir | app icon; bundled into the exe + copied to dist |

To add a deployment destination, set it in **Set paths** (it lands in
`%APPDATA%\DevTools\config.json`) — `_get_destination_roots()` reads it from there.
