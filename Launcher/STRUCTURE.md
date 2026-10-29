# Launcher — STRUCTURE

> Verified against source: 2026-10-16 · `l.py` 2310 L (PySide6; the Tk version
> was replaced the same day — backup in `C:\Dev\backup\launcher_tk_2026-10-16\`)

User-facing documentation: `Readme Launcher.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Launcher_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `l.py` | Single file. The whole launcher (PySide6). |
| `icon.ico` | Window / taskbar icon (`_icon_file`, `main`). |
| `%APPDATA%\Launcher\config.json` | Persisted settings — not in the repo. |
| `%APPDATA%\Launcher\old_version_run.log` | Output of the last archived-version run (`OLD_RUN_LOG`) — not in the repo. |
| `testing/test_archive_versions.py` | What the version menu offers for each shape of `archive/` folder. |
| `testing/test_old_version_launch.py` | Each route: an in-folder exe is started without moving anything, an archived exe is swapped in **with its sources**, a sources-only folder runs its own code, a one-file exe runs in place. |
| `testing/test_inplace_updates.py` | The window changes in place (update flag, acknowledge, move to group, open/close a group), the version list is read on opening, no dialog after a scan, names fit their buttons. Runs on `minimal:enable_fonts` — nothing on screen. |
| `testing/render_window.py <out> [source]` | Renders the window, all groups open and the group menu to PNGs, without putting anything on screen. |
| `testing/test_cleanup_strays.py` | The Clean button's file moves and deletions. |

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
| `SCRIPTS` | Image Tools, Screenshots, Announcer, CSS Logger, Chiller log |
| `PARTS` | Launcher |
| `IN_PROGRESS` | Calibrations |
| `NOT_WORKING_CORRECTLY` | Counter of Shots |
| `PERSONAL` | Copy Manager, Builder, Internal Builder, Dev Tools, Git Work |
| *(everything else)* | External |
| `SUBSUMED` | **not shown at all** — Image Finder, Image Slider, Shot finder (Image Tools), Spectra (CSS Logger), Builder, Copy manager (Dev Tools) |
| `RETIRED` | **not shown at all** — Time Converter (retired, nothing replaces it) |

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
| `find_docs(program_dir, name)` | `(readme, readme_full)` from **one** folder listing (the folder is on a share). ReadMe = `readme_<name>`; Details = `readme_<name>_full`, `readme_<name>_details` or `manual_<name>`; case/separators/extension ignored |
| `find_readme_or_none` / `find_readme_full_or_none` | Thin wrappers over `find_docs` |
| `_dist_candidates(root, name)` | Every dist folder a program's builds may sit in (`root/dist`, `root.parent/dist`, Dev Tools' `dist_root`) — shared by the scan and the version poll |
| `_devtools_dist_root()` | Dev Tools' build folder from its config; cached until the config file's mtime changes |
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
- `_schedule_version_poll()` → `self.after(10000, ...)` — 10 s poll (`after` is
  the window's own thread-safe stand-in for Tk's, see UI below)
- `_poll_versions()` / `_scan_program_versions()` — background rescan, all programs
  in parallel (`ThreadPoolExecutor`, 16), same dist folders as the first scan
  (`_dist_candidates`)
- `_apply_version_updates(updates, refreshed, gen)` — a program is highlighted only if
  the on-disk version differs from `acknowledged_versions[name]`. A result from a
  poll that started before another source was loaded (`gen != _scan_gen`) is
  dropped. Only buttons whose highlight flipped are restyled
  (`_set_update_style`); a card that gained its first older version is rebuilt
  alone (`_replace_card`) so it gets its clock button.
- `_acknowledge_update(name)` — right-click *Acknowledge update* or a successful launch; writes the config;
  restyles that one button
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
not finish); the Clean button offers to restore it (every scan marks the button).

### Maintenance helpers
| Function | Description |
|----------|-------------|
| `find_misplaced_version_files(programs)` | Versioned files not where their version lives → `(src, dst)`; `dst=None` = identical twin, delete |
| `apply_misplaced_cleanup(list)` | Does the moves / deletions; a different build of the same name is kept as ` (2)` |
| `prompt_move_misplaced(parent, list)` | Summary dialog, Tidy up / Skip (`_ask`) |
| `find_programs_in_swap_state(programs)` | Programs with a leftover `archive/_temp_latest/` |
| `prompt_restore_swap_state(parent, stuck)` | Offers to move the latest files back |
| `_run_cleanup()` | Clean button — runs both checks, opens the dialogs, then re-marks the button with what is left |
| `_mark_clean_button(count)` | `Clean (n)` with the `alert` property (orange, dark text), or plain `Clean` |

Both checks run in the scan's worker thread (`refresh` → `_apply_programs(..., tidy)`)
and **never open a dialog by themselves** — a modal after every scan took the
keyboard from whatever the user was typing into. The result only marks the
Clean button and is named in the status line (which wraps instead of being
cut). A failed scan is reported in the status line, not a message box.

### UI (PySide6)
- `Launcher(QMainWindow)` — source radio buttons in a *Data source* group box,
  Notes / Set paths / Clean, a `QScrollArea` body (`_inner`, `_body`), a
  wrapping status label. `main()` sets the app id, `apply_app_style(app)`
  (Fusion + `light_palette()` + `APP_STYLESHEET`, every colour stated — this PC
  runs Windows in dark mode), the window icon on the app and the window, and
  `_nudge_taskbar_icon`.
- **Threads.** Workers never touch a widget. `after(ms, fn, *args)` emits to
  `_Invoker`, a queued signal that runs `QTimer.singleShot(ms, fn)` on the
  window's thread — the copied launch / scan / poll code calls it exactly as it
  called Tk's `after`. `_set_status(text)` replaces `status.configure(text=…)`.
- `messagebox` — a two-method shim (`showerror`, `showinfo`) over `QMessageBox`,
  kept under the tkinter name so the launch code and its test stub still match.
- **Only a new scan rebuilds the window** (`_rebuild_buttons` → `_clear_body`).
  Everything else changes the widgets in place:
  - a header (`QToolButton#groupHeader`) + a grid widget exist for every group
    in `GROUP_ORDER`; `_show_groups()` shows the non-empty ones and the open
    grids, `_update_group_header()` sets the drawn triangle + count
  - cards: `_build_card()` makes one, `_regrid(gkey)` places a group's cards in
    name order, two to a row; `_prog_cells` / `_prog_buttons` map name → card /
    button; `_group_built` says which groups have their cards
  - move to group: `_drop_card` + `_regrid(old)`, `_add_card` + `_regrid(new)`
    (a never-opened group builds its cards lazily when opened)
  - update flag: the `update` dynamic property + `_repolish()`
  - the version menu is filled on `aboutToShow` (`_fill_version_menu`)
- **Widths.** `_calc_prog_btn_px()` = the longest label in the OPEN groups,
  measured with `_ui_font(9)` (a stylesheet font never reaches `font()`), plus
  icon and padding — names are never cut. `_apply_btn_width()` resizes in place.
  `_doc_btn_px()` is the minimum of both small-button columns, so a card without
  Details or without older versions keeps the same size. `_fit_width()` sets the
  window's minimum width from the body's `minimumSizeHint` + scroll bar + margins
  and widens the window once if needed; never narrows it. No horizontal scroll
  bar.
- **Drawn icons.** `drawn_icon(name)` paints notes, gear, broom, folder, history
  (clock), open/closed triangles and tick with QPainter on a 20 × 20 grid, at
  six sizes and in every QIcon mode — no emoji or symbol glyphs.
- Right-click on a program button: `_show_group_menu` — *Acknowledge update*
  (only while orange), *Move to group* (current one ticked), *Reset to default*.
- Set paths: the hint for each path is in the dialog itself; a chosen folder is
  selected and loaded straight away (`QFileDialog.getExistingDirectory`).
- Office sources are disabled on lab machines (`_root_enabled`,
  `_rebuild_radiobuttons`). No value widgets, so no wheel guard is needed.


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

Row 0 of the card's small-button grid holds the doc buttons: **ReadMe** spans both
columns when there is no Details file, otherwise the two share the row. A program
with a Details file but no ReadMe (should not happen) shows Details spanning both.
Row 1 holds the folder button and, when there are older versions, the clock.
Both columns have `_doc_btn_px()` as their minimum and both rows 24 px, so every
card has the same size whatever buttons it has.
`open_readme()` / `open_readme_full()` both `os.startfile` and report a missing
file in a message box rather than failing silently.

The Tk version's card-width history (theme minimums, a measured cell width that
bounced between two values and rebuilt the window forever) does not apply any
more: Qt's layouts give each card its real minimum size, the column count is a
constant, and `_fit_width()` only sets the window's minimum width.
