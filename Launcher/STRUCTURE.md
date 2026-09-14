# Launcher — STRUCTURE

> Verified against source: 2026-08-19 · `l.py` 1586 L

User-facing documentation: `Readme Launcher.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Launcher_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `l.py` | Single file. The whole launcher (tkinter/ttk). |
| `icon.ico` | Window / taskbar icon (see `set_app_icon`). |
| `%APPDATA%\Launcher\config.json` | Persisted settings — not in the repo. |
| `%APPDATA%\Launcher\old_version_run.log` | Output of the last archived-version run (`OLD_RUN_LOG`) — not in the repo. |
| `testing/test_archive_versions.py` | What the version menu offers for each shape of `archive/` folder. |
| `testing/test_old_version_launch.py` | Each route: an in-folder exe is started without moving anything, an archived exe is swapped in **with its sources**, a sources-only folder runs its own code, a one-file exe runs in place. |

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
| `PARTS` | Launcher |
| `IN_PROGRESS` | Calibrations |
| `NOT_WORKING_CORRECTLY` | Counter of Shots |
| `PERSONAL` | Copy Manager, Builder, Internal Builder, Dev Tools, Git Work |
| *(everything else)* | External |
| `SUBSUMED` | **not shown at all** — Image Finder, Image Slider, Shot finder (Image Tools), Spectra (CSS Logger), Builder, Copy manager (Dev Tools) |

`SUBSUMED` is not a group: `scan_programs()` skips those folders before a card is
ever built. Each of them became a tab of a bigger program, so the folder left on
the share only offers a build from last spring — a card for it is a way to start
the wrong version by mistake. The names are matched through `_norm()`, so folder
spelling and spacing do not matter. Note `PERSONAL` still names Copy Manager and
Builder: harmless, because the folders never reach the grouping step.

Display order (`GROUP_ORDER`): Main scripts → Side scripts → External →
In progress → Not working correctly → Personal. The labels are display text
only; the keys behind them stay `scripts` and `parts`, which is what the saved
expanded/collapsed state and `custom_groups` in the config are keyed on.

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
| `find_readme_or_none(program_dir, name)` | Case/separator-insensitive ReadMe lookup (`README_PREFIX`) — the **short** doc |
| `find_readme_full_or_none(program_dir, name)` | The **detailed** companion doc. Accepts `readme_<name>_full`, `readme_<name>_details` or `manual_<name>`, same normalisation. |
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
- `ARCHIVE_PY_RE` — a snapshot's **main script**: `Name v1.2.3.py`, optionally
  `__20250101_120000` or a ` (2)` dedup suffix. The helper modules beside it keep
  their plain names, so this is what tells the two apart.
- `_find_versioned_py(version_dir, exe=None)` — the snapshot's main `.py`: named
  after the exe when one was archived with it, otherwise the one versioned `.py`
  in the folder. `exe=None` is how a **source-only** snapshot is found.
- `ARCHIVE_NON_VERSION_DIRS` — `unknown`, `_temp_latest`: folders inside `archive/`
  that are not a version.

**archive/ layout handling (`scan_archive_versions`):**
1. `archive/vX.Y.Z/` — **one entry per version folder**; the canonical name is
   preferred over ` (2)` dedup copies (sort key puts `" ("` last, then `break`).
   **The exe is optional**: a folder holding only sources is listed too and run
   with Python (route 3). `exe_path` is then `None`. Each entry also carries
   `has_internal` — whether the folder brought its own `_internal/`.
2. `archive/*.exe` — legacy flat layout, still supported.
3. Everything is sorted by the precomputed `_ver` key (removed again before return).
   Folders in `ARCHIVE_NON_VERSION_DIRS` are skipped — `archive/unknown/` holds
   versioned `.py` files and would otherwise become a bogus entry.

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
| `_find_python()` | `pythonw` (no console behind a windowed app) → `python` → `py`; `None` if there is none |
| `_launch_exe(exe, program_dir, py_path, has_internal)` | An older version — see the routes below |
| `open_folder(name)` / `open_notes()` | Explorer / shared notes.txt |

**Running an older version (`_launch_exe`).** The archived exe **is** the whole
old program: for `--onedir`, PyInstaller embeds the PYZ (the app's own modules) in
the exe, and `_internal/` holds only the interpreter, the extension modules and
the third-party packages — interchangeable between versions. Measured on the
published copy (`Software/Image Tools`): the three exes in the folder are ~34 MB
each and differ in size per version; `_internal/base_library.zip` holds 155
stdlib bootstrap `.pyc` and none of the app's modules; and the app's own
`debug_syspath.txt` dump shows `sys.path` is `_internal` plus its sub-folders and
nothing else — so **the loose `.py` in the program folder are never imported by a
running exe**. Do not "fix" old versions by running their sources instead; the
exe is correct.

Routes, in order:

1. **Start where it lies** — `exe_path.parent == program_dir` (the last few builds
   are kept side by side in the published folder), or `has_internal` (the version
   folder brought its own `_internal/`), or `program_dir/_internal` does not exist
   at all (a `--onefile` helper needs none). Nothing is moved.
   Route 1 exists because of the bug it replaced: the swap moved every
   non-timestamped `*.exe` of `program_dir` into `_temp_latest` *including the exe
   about to be started*, then `copy2(exe_path, program_dir/exe_path.name)` read a
   path it had just emptied → `FileNotFoundError`, "Launch failed". Every version
   sitting in the program folder was unlaunchable from the menu.
2. **Swap into `program_dir`** — an archived version needs to borrow the folder's
   `_internal`. The current non-timestamped `*.exe` + all `*.py` are **moved** to
   `archive/_temp_latest/`; the archived exe **and its whole `.py` snapshot** are
   copied in (previously only the main `.py` was, leaving the folder with no
   helper sources); the exe is started with `cwd=program_dir` and waited on; the
   originals are moved back in `finally`. `did_swap` is set *before* the first
   move so a failure part-way still restores. Helper selection: the main script
   plus every `.py` **not** matching `ARCHIVE_PY_RE`, so a ` (2)` duplicate or
   another version's script stays out.
3. **Sources only** (`exe_path is None`) — staged the same way and run via
   `_find_python()` with `PYTHONDONTWRITEBYTECODE=1`. Staged rather than run in the
   archive folder because the programs resolve `icon.ico`, settings and asset
   folders against `Path(__file__).parent`. A non-zero exit shows the tail of
   `OLD_RUN_LOG` (`%APPDATA%\Launcher\old_version_run.log`) — `pythonw` has no
   console, so an import failure would otherwise be completely silent.

If `_temp_latest` already exists routes 2 and 3 are refused (a previous swap did
not finish); the broom button and every scan offer to restore it.

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
  ReadMe, Details, ✓ acknowledge, 📂 folder, 🔽 archived-version dropdown,
  right-click group menu
- Office sources are disabled on lab machines (`_rebuild_radiobuttons`)
- `set_app_icon(win, ico_path, app_id)` — icon + AppUserModelID so the taskbar
  shows the program icon instead of the Python feather


---

## The two-document scheme (added 2026-08-19)

Every program carries two user-facing documents, and the card shows a button for
each one that exists:

| Button | File the matcher looks for | Content |
|--------|---------------------------|---------|
| **ReadMe** | `readme_<folder>` | Short: what it does, the principles, the main controls. |
| **Details** | `readme_<folder>_full`, `readme_<folder>_details`, or `manual_<folder>` | Long: every control, every setting, file formats, troubleshooting. |

Both go through `_norm()` (lower-case, strip `_ - . ' '`) and compare against the
file's stem *and* full name, so the extension is irrelevant. `.txt` is used
throughout so `os.startfile` always has a handler; `STRUCTURE.md` stays `.md`
because it is for developers reading the repo, not for the Launcher.

Row 0 of the card's sub-frame holds the doc buttons: **ReadMe** spans both columns
when there is no Details file, otherwise the two share the row. A program with a
Details file but no ReadMe (should not happen) shows Details spanning both.
`open_readme()` / `open_readme_full()` both `os.startfile` and report a missing
file in a message box rather than failing silently.

### Making two buttons fit — measured, not guessed

Adding Details as a second text button took a card from **250 px to 336 px**, and
`_group_min_cell_px = 260` with `max(2, …)` then laid three of them into ~860 px of
canvas: the third card was clipped at the window edge and a horizontal scrollbar
appeared. Measured on this machine (vista theme, Segoe UI):

| | |
|---|---|
| program button at `width=18` | 160 px, while `"Internal Builder"` needs only 132 px — **28 px of slack** |
| a doc button with no explicit `width` | 69–80 px, and that is the **theme minimum**: `"ReadMe"` is 42 px of text, `"Details"` 35 px |

So the width was never spent on the labels. Three changes, and the card ends at
**268 px** — 18 px more than the original single-button card:

| Change | Why |
|--------|-----|
| `_prog_btn_chars()` sizes the program button to the **longest label actually shown** (clamped 10…22, matching `clamp_label`), one width for every group so the cards still line up | reclaims the slack; verified no label clips — the widest needs 80 px in 108 px of inner space |
| the doc buttons get `width=7` and their own `Doc.TButton` style with padding `(3, 2)` | an explicit width is the **only** way under the theme minimum. 56 px each, holding 42 px and 35 px of text. The font stays at 8 — legibility first |
| `_calc_group_cols()` uses `_measured_cell_px` (the first card's real `winfo_reqwidth() + 12`) and allows **one** column | a hard-coded estimate goes stale the moment a card gains a button, and forcing two columns drew a card into space that did not exist |

`_build_group_content` measures the first cell it builds, and re-flows once (guarded
by `_cell_remeasure_done`, cleared in `_rebuild_buttons`) if the column count that
measurement implies differs from the one already used — so a card that changes shape
re-flows the grid by itself instead of silently overflowing.

Verified by driving the real window: 2 columns at 700 px, 3 at 918 px, 4 at 1200 px,
content within the canvas every time.
