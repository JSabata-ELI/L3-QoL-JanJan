# Launcher — STRUCTURE

> Verified against source: 2026-08-05 · `l.py` 1523 L

## Files

| File | Description |
|------|-------------|
| `l.py` | Single file. The whole launcher (tkinter/ttk). |
| `icon.ico` | Window / taskbar icon (see `set_app_icon`). |
| `%APPDATA%\Launcher\config.json` | Persisted settings — not in the repo. |

---

## l.py

### Constants and configuration
- `CONFIG_PATH` — `%APPDATA%\Launcher\config.json`
- `_load_config()` / `_save_config()` — tolerant JSON read/write (never raises)
- `ROOT_OPTIONS` — `(label, path, configurable)` scan roots:
  - `Lab - Scratch` → `\\hapls-share.lcs.local\scratch\Software`
  - `Office - Scratch` → from config (`office_scratch`)
  - `Office - Sharepoint` → from config (`office_sharepoint`)
  - `Office - Programs` → `~/OneDrive - ELI Beamlines/ELI Beamlines/Python/programy`
- `_apply_config_to_root_options(cfg)` — fills the configurable paths in
- `NOTES_LAB` / `NOTES_OFFICE` — shared `notes.txt` on the two shares
- `IGNORE_DIR_NAMES` — `{archive, dist}`, skipped when scanning a root

### config.json keys
```json
{
  "office_scratch":        "path...",
  "office_sharepoint":     "path...",
  "acknowledged_versions": {"Image Tools": "v2.5.4"},
  "custom_groups":         {"Program name": "parts"}
}
```

### Program groups
| Constant | Programs |
|----------|----------|
| `SCRIPTS` | Image Tools, Screenshots, Time Converter, Announcer, CSS Logger, Chiller log |
| `PARTS` | Image Finder, Image Slider, Shot finder, Launcher |
| `IN_PROGRESS` | Calibrations |
| `NOT_WORKING_CORRECTLY` | Counter of Shots |
| `PERSONAL` | Copy Manager, Builder, Internal Builder, Dev Tools, Git Work |
| *(everything else)* | External |

Display order (`GROUP_ORDER`): Scripts → Parts → External → In progress →
Not working correctly → Personal.

`group_for_program(name)` resolves a group, but `custom_groups` from the config
wins — the card context menu (`_show_group_menu` → `_move_to_group`) lets the
user move a program to another group (or reset it) and the choice is persisted.

### Scanning
| Function | Description |
|----------|-------------|
| `scan_programs(root)` | Scans the root with a 16-thread pool → `{program_name: info}` |
| `_scan_one_program(dir, root)` | One program dir. Layout A: `*.exe` directly in the folder (Scratch). Layout B: `root/dist/<Program>/vX.Y.Z/*.exe` (Programy) |
| `newest_version_folder(dist_dir)` | Highest `vX.Y.Z` folder |
| `pick_exe(exes, program_name, ver_folder)` | Picks the right exe out of a folder |
| `_build_version_list(exes, program_dir)` | Dropdown content: current exes (newest first) + `scan_archive_versions()` |
| `scan_archive_versions(program_dir)` | Archived versions from `archive/` |
| `find_readme_or_none(program_dir, name)` | Case/separator-insensitive ReadMe lookup (`README_PREFIX`) |
| `find_icon_for_program(program_dir, exe)` | `icon.ico` next to the exe |

### Versions and naming
- `VERSION_RE` — `v(\d+)\.(\d+)\.(\d+)$` for folder names; `parse_version()` / `_ver_str()`
- `ARCHIVE_EXE_RE` — timestamped archive exe: `Name v1.2.3__20250101_120000.exe`
- `ARCHIVE_EXE_PLAIN_RE` — **normalized** archive exe: `Name v1.2.3.exe`, optional
  ` (2)` dedup suffix. Dev Tools strips the timestamp inside `archive/vX.Y.Z/`
  folders so a snapshot is runnable as-is, so both spellings must be accepted.
- `TIMESTAMPED_EXE_RE` — used to keep timestamped files out of the "current" list
- `_exe_version(p)` / `_archive_exe_version(p, folder_ver)` — sort keys.
  `_archive_exe_version` returns `(maj, min, patch, date, time)`; a normalized name
  has no timestamp, so it falls back to the version folder (or the filename) with a
  `(0, 0)` timestamp and still sorts by version.
- `_archive_exe_label(p, folder_ver)` — `"v1.2.3  (2025-01-01  12:00:00)"` for
  timestamped names, plain `"v1.2.3"` for normalized ones.
- `_find_versioned_py(version_dir, exe)` — the matching `.py` snapshot next to the
  archived exe (both name spellings).

**archive/ layout handling (`scan_archive_versions`):**
1. `archive/vX.Y.Z/` — **one entry per version folder**; the canonical name is
   preferred over ` (2)` dedup copies (sort key puts `" ("` last, then `break`).
2. `archive/*.exe` — legacy flat layout, still supported.
3. Everything is sorted by the precomputed `_ver` key (removed again before return).

### Update indicator
- `_schedule_version_poll()` → `self.after(10000, ...)` — 10 s poll
- `_poll_versions()` / `_scan_program_versions()` — background rescan per program
- `_apply_version_updates(updates, refreshed)` — a program is highlighted only if
  the on-disk version differs from `acknowledged_versions[name]`
- `_acknowledge_update(name)` — ✓ button or a successful launch; writes the config
- On startup `_apply_programs()` re-flags anything whose version no longer matches
  the acknowledged one

### Launching
| Function / method | Description |
|-------------------|-------------|
| `_launch_no_zone_check(path)` | `ShellExecuteEx` + `SEE_MASK_NOZONECHECKS` — suppresses the "unblock" dialog for exes on network shares |
| `launch(name)` | Current version, background thread, auto-acknowledges |
| `_launch_exe(exe, program_dir, py_path)` | Archived version — see swap below |
| `open_folder(name)` / `open_notes()` | Explorer / shared notes.txt |

**Old-version swap (`_launch_exe`, when `program_dir/_internal` exists):**
the archived exe needs the program folder's `_internal/`, so the current exe/py
files are *moved* to `archive/_temp_latest/`, the archived pair is copied in, the
process is started and waited on, and the originals are moved back in `finally`.
If `_temp_latest` already exists the launch is refused (a previous swap did not
finish). Fallbacks: run the `.py` via `pythonw`, or start the exe in place.

### Maintenance helpers
| Function | Description |
|----------|-------------|
| `find_misplaced_timestamped_exes(programs)` | Timestamped exes left in a program folder → `(src, archive dst)` |
| `prompt_move_misplaced(parent, list)` | Summary dialog, Move / Skip |
| `find_programs_in_swap_state(programs)` | Programs with a leftover `archive/_temp_latest/` |
| `prompt_restore_swap_state(parent, stuck)` | Offers to move the latest files back |
| `_run_cleanup()` | 🧹 Clean button — runs both checks on demand |

Both checks also run automatically after a scan (`_apply_programs`, delayed 200 /
400 ms).

### UI (tkinter)
- `Launcher(tk.Tk)` — main window; source radio buttons, 📋 Notes, ⚙ Set paths,
  🧹 Clean, scrollable body, status bar
- `ScrollableFrame(ttk.Frame)` — canvas-based scroll area used for the card grid
- Collapsible group sections (`_rebuild_buttons`, `_build_group_content`,
  `_toggle_group`); column count adapts to width (`_calc_group_cols`)
- Card = program button (name + version, amber + ↑ when an update is pending),
  ReadMe, ✓ acknowledge, 📂 folder, 🔽 archived-version dropdown, right-click group menu
- Office sources are disabled on lab machines (`_rebuild_radiobuttons`)
- `set_app_icon(win, ico_path, app_id)` — icon + AppUserModelID so the taskbar
  shows the program icon instead of the Python feather
