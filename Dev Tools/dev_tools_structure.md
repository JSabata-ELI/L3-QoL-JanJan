---
name: Dev Tools structure map
description: Function map of dev_tools.py, dt_core.py, dt_common.py, dt_builder.py, dt_copy.py — Builder + Copy Manager. Read before editing.
---

Dev Tools — PySide6, two tabs: Builder (`dt_builder.py`) + Copy Manager (`dt_copy.py`),
all logic in `dt_core.py`, shared look/log/dialogs in `dt_common.py`.
Verified 2026-10-16. Why things are the way they are: `STRUCTURE.md`.

No line numbers here — they drift. Grep the name.

---

## dev_tools.py — window

| Name | Note |
|------|------|
| `_icon_file()` | → `dt_core.icon_file()` (exe folder → `sys._MEIPASS` → source folder) |
| `_icon_app_id()` | fresh taskbar id per launch (`ELI.DevTools.<random>`) |
| `_nudge_taskbar_icon(hwnd)` | takes the window icon away and back at ~2/10/30/60 s so Explorer re-reads it |
| `DevToolsWindow` | title, icon, `LogHub`, the blue program tabs, `BuilderTab` + `CopyTab`, cross refs `builder.copy_tab` / `copy.builder` |
| `main()` | app id → QApplication → `apply_app_style` → `init_ui_thread` → window shown at 1280 × 860 (not maximized) → nudge |

---

## dt_core.py — logic, no UI

### Paths, config, state
| Name | Note |
|------|------|
| `PRACTICE`, `APP_KEY`, `PRACTICE_ROOT`, `CONFIG_PATH` | practice mode switch (off) and the config file `%APPDATA%\DevTools\config.json` |
| `app_dir()`, `icon_file()`, `state_dir()` | frozen-aware folders |
| `settings_path()`, `usage_log_path()`, `cm_state_path()` | `builder_settings.json`, `build_usage.json`, `copy_manager_state.ini` |
| `load_json`, `save_json`, `load_config`, `save_config` | |
| `ensure_practice_setup(log)` | practice mode only: folders + one-time seed from the real setup |
| `programs_root()`, `dist_root()`, `scratch_root()`, `destination_roots()` | |
| `internal_builder_dist()`, `internal_builder_distpath()` | where the Internal Builder's `_internal` is read from / built to |

### Versions
| Name | Note |
|------|------|
| `read_versions_txt(force)`, `write_version_to_txt`, `invalidate_versions_cache` | `<scratch>/Versions.txt`, 15 s cache |
| `RE_VER`, `RE_VDIR`, `VERSION_RE`, `TIMESTAMPED_EXE_RE` | |
| `parse_version_tuple`, `bump_patch`, `parse_version`, `is_newer_version` | |
| `list_versions(dist_dir)` | `vX.Y.Z` folders, newest first |
| `last_version_in_dist`, `last_built_version(name, dist)` | Versions.txt first, then dist |
| `fmt_elapsed` | "4.5s" / "2m 38s" |

### Discovery (Builder)
| Name | Note |
|------|------|
| `ALWAYS_IGNORE`, `NO_WINDOW` | |
| `guess_main_py(dir)` | `<name>.py` → slug → `main.py` → `app.py` → first `.py` |
| `find_projects(root)` | folders with a `.py`, not `_`-prefixed, sorted |
| `find_helpers(projects, cache)` | `extra_exes` per project, cached on the config's mtime |
| `module_homes(root)`, `check_module_homes(p, py_files, root, log)` | build stops on a module outside the folder or with a second copy |

### Build
| Name | Note |
|------|------|
| `ASSET_DIR_NAMES`, `SHARED_DATA_DIR_NAMES` | asset folders (shipped by build, protected by deploy); shared data (never touched) |
| `_TEST_SUBMODULES` | `.tests` excluded for a `collect_all` package (collect-all only from `build_config.json`) |
| `new_workdir(name)` | private PyInstaller work folder, day-old ones swept |
| `as_out_dir(msg)`, `exe_icon_count(exe)`, `_rmtree_readonly`, `_run_logged(…, on_line)`, `_rm_norm` | |
| `PhaseClock` | build time split: analysis / package / collect / finish, from PyInstaller's lines |
| `parallel_builds(n)`, `_free_ram_gb()` | builds at once: ≤ 3, one per 4 cores and per 2 GB free |
| `_SHARED_FILE_LOCK` | Versions.txt, `build_usage.json`, `build_times.log` written one at a time |
| `record_build_usage(name)` | `build_usage.json`, last 500 |
| `record_build_times(name, ver, total, phases, copy_s)`, `build_times_path()` | `build_times.log`, one tab-separated line per build |
| `build_one_project(p, ver, dist, root, log, clock)` | → `(ok, version folder or error)` |
| `build_one_helper(h, ver, dist, log, clock)` | `--onefile`, own dist entry |
| `build_internal_builder(root, log, clock)` | `.spec` if present |
| `build_summary(...)` | the BUILD DONE report |

### Copy Manager list, state
| Name | Note |
|------|------|
| `CM_IGNORE`, `list_program_dirs(root)` | |
| `find_helper_programs(dirs)` | helper names per program |
| `helper_parents(refresh)`, `dest_dir_for(dst, name)`, `helper_dir_names(name)` | a helper goes inside its program's folder |
| `load_state`, `save_state`, `last_deployed(name, versions_map, state)` | |

### Files, archive
| Name | Note |
|------|------|
| `merge_dir(src, dst)` | copy over, never delete first |
| `_move_legacy_toplevel_folder` | old top-level helper folder → inside the program |
| `exe_version`, `find_exe_in_folder`, `unique_path`, `_try_move` | `_try_move` reports a locked file instead of failing |
| `find_readme_or_raise`, `find_readme_full_or_none`, `_find_docs` | the two user documents; `_full` mirrors the Launcher |
| `fix_archive_dir`, `reunite_unknown_helpers`, `canonical_stem`, `normalize_version_folder_names` | archive maintenance |
| `_archive_file`, `move_existing_exes_to_archive` | |
| `fix_destinations(roots, log)` | the **Fix** button |

### Deploy
| Name | Note |
|------|------|
| `_find_icon`, `scan_icon_conflicts(jobs, roots)` | file part of the icon check (thread) |
| `icon_decisions_for(name, exists, replace)` | |
| `ERROR_KEYS` | what counts as an error in the reports |
| `deploy_one_program(program_dir, version, roots, helper_parent_dir, log, icon_decisions)` | → `(stats, file_log)` |
| `copy_readme_only(names, roots, helper_parent_dir, log)` | |
| `latest_internal_folder()`, `deploy_libraries(names, internal, scratch, log, progress)` | **Deploy Libraries** |
| `copy_summary(...)`, `overall_summary(...)` | COPY DONE / ALL DONE reports |

---

## dt_common.py — look, log, threads, dialogs

| Name | Note |
|------|------|
| `ACCENT`, `SELECT_BG`, `SEC_*` | Image Tools colours and section accents |
| `_IT_APP_QSS`, `BTN_QSS`, `COMBO_QSS`, `CHECKBOX_QSS`, `TREE_QSS`, `OTHER_QSS`, `MAIN_TABS_QSS` | stylesheets copied from Image Tools |
| `_tick_image_url()` | white tick picture in `%TEMP%` |
| `app_stylesheet()`, `apply_app_style(app)`, `light_palette()` | |
| `_shade(hex, f)`, `Section(title, accent)` | coloured panel; content goes into `.body_layout` |
| `_Invoker`, `init_ui_thread()`, `on_ui(fn, …)`, `ask_on_ui(fn, …)` | thread → UI thread hand-over |
| `LogHub` | `new_view()`, `write(text, force_scroll)`, `clear()`; flushes every 40 ms |
| `log_box(hub)` | the Log section with **Clear** |
| `WheelGuard`, `guard_wheel(combo)` | no wheel edits on an unfocused drop-down |
| `wrap_path`, `path_row` | long paths wrap at separators |
| `message`, `ask_yes_no`, `ask_dist_missing` | |
| `icon_compare_dialog(...)` | **Icon conflict** — True = replace |
| `set_paths_dialog(parent)` | **Set paths** — True when saved |

---

## dt_builder.py — `BuilderTab`

| Group | Methods |
|-------|---------|
| UI | `_build_ui`, `_log`, `_save_settings`, `_save_replace_icons` |
| Versions | `last_version`, `default_next`, `effective_next`, `invalidate_version_cache`, `helpers_of` |
| List | `reload_projects`, `_make_item`, `_render` (all rows, once), `_group_of`, `_layout_rows` (re-place only, signals blocked), `_paint_order`, `_refresh_version_labels` |
| Ticks | `_on_item_changed` (push to helpers), `_set_tick`, `_visible_names`, `_select_all`, `_clear_all`, `build_queue` |
| Panel | `_on_current_changed` (ignored while `_updating`), `_focused_name`, `_show_focus`, `_on_next_edited`, `_on_group_changed`, `_open_focused_readme`, `_open_readme` |
| Paths | `_change_root`, `_open_set_paths`, `apply_paths`, `_open_dist` |
| Build | `_ensure_dist_root`, `_build_selected` (worker), `_on_build_finished` |

State: `ticked`, `tick_order`, `items` (name → row), `section_items`, `expanded`,
`next_override`, `focused` (`("project", Path)` / `("helper", dict)`), `project_groups`.

---

## dt_copy.py — `CopyTab`

| Group | Methods |
|-------|---------|
| UI | `_build_ui`, `_log`, `_build_dest_rows`, `selected_destination_roots`, `set_busy`, `_progress`, `_progress_hide` |
| Paths | `_change_root`, `_open_set_paths`, `apply_paths`, `refresh` |
| List | `load_programs`, `_fit_name_column`, `_make_row`, `_attach_combo`, `_fill_row`, `update_rows_in_place` |
| Ticks | `_on_item_changed` (push to helpers), `_set_checked`, `_select_all`, `_select_new`, `_clear`, `selected_programs` |
| Icons | `resolve_icon_conflicts` (UI thread) |
| Copy | `_record_deployed`, `copy_one_after_build` (called from the Builder's worker), `copy_after_build_report`, `_write_reports`, `_on_copy` |
| Other | `_on_copy_readme_only`, `_on_fix`, `_on_deploy_libraries` |

State: `rows` (name → item, combo, key, is_new, latest), `helper_names_by_parent`,
`helper_parent_dir`, `expanded`, `dest_checks`, `state_deployed`.

`_status_of(has_versions, is_new, deployed)` — NEW green / UTD red / grey.

---

## Configuration files
| File | Where | Content |
|------|-------|---------|
| `config.json` | `%APPDATA%\DevTools\` | `root_folder`, `dist_root`, `scratch`, `sharepoint` (also read by the Launcher) |
| `builder_settings.json` | beside the app | `root_folder` fallback, `project_groups`, `replace_icons_without_asking` |
| `build_usage.json` | beside the app | build history |
| `copy_manager_state.ini` | beside the app | `[deployed] name = vX.Y.Z` |
| `Versions.txt` | `<scratch>` | `Name = vX.Y.Z`, shared with every PC |
