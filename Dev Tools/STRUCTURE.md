# Dev Tools — STRUCTURE

> Verified against source: 2026-10-16 (PySide6 rewrite)

User-facing documentation: `ReadMe Dev Tools.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Dev Tools_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

Dev Tools was a tkinter program until 16.10.2026 (`b_t.py` + `cm_t.py`). It was
ported to PySide6 in the Image Tools look; the build and deploy code paths were
carried over unchanged (they are debugged), only the UI is new. The Tk version
is backed up in `C:\Dev\backup_DevTools_tk_20261016\`.

## Files

| File | Description |
|------|-------------|
| `dev_tools.py` | Entry point. One window, two tabs (Builder, Copy Manager), one shared log. Taskbar identity (`_icon_app_id`, `_nudge_taskbar_icon`). |
| `dt_core.py` | **All logic, no UI**: paths and config, Versions.txt, project discovery, module home check, build, deploy, archive maintenance, Deploy Libraries, end-of-run reports. Every function that used to write to a widget takes a `log` callable instead. |
| `dt_builder.py` | **Builder** tab (`BuilderTab`). |
| `dt_copy.py` | **Copy Manager** tab (`CopyTab`). |
| `dt_common.py` | Shared Qt pieces: the Image Tools look (stylesheets, `Section` panels, tick picture), `LogHub` (the shared log), `on_ui` / `ask_on_ui` (thread hand-over), `WheelGuard`, dialogs (Set paths, Icon conflict, message boxes). |
| `build_config.json` | Hidden imports of the four `dt_*` modules, Qt exclusions. |
| `builder_settings.json` | Builder settings (root folder fallback, per-project group, *Replace icons without asking*). |
| `build_usage.json` | Build history (gitignored). |
| `copy_manager_state.ini` | Last deployed version per program (gitignored). |
| `icon.ico` | Window / taskbar / exe icon. |

A function map per module is in `dev_tools_structure.md`.

---

## Paths

```
programy/
├── L3-QoL-JanJan/          ← git repo (sources) = root_folder
│   ├── Dev Tools/
│   ├── Image Tools/
│   └── ...
└── (dist_root, usually C:\Dev\dist)   ← build output
    ├── Image Tools/
    │   └── v1.2.3/
    └── ...
```

All paths come from `%APPDATA%\DevTools\config.json` (`root_folder`, `dist_root`,
`scratch`, `sharepoint`), edited in the **Set paths** dialog of either tab. The
Launcher reads the same file (`Launcher/l.py::_devtools_dist_root`), so its name
and keys must not change.

- `programs_root()` — config `root_folder` → `builder_settings.json` → the repo
  this file sits in.
- `dist_root()` — config `dist_root` → `<programs root>/../dist`.
- `destination_roots()` — `Scratch` and `Sharepoint` from the config; nothing is
  hardcoded.
- `internal_builder_dist()` — `C:\Dev\dist\_internal_builder` if it exists, else
  `<dist>/Internal Builder`.

### Practice mode
`dt_core.PRACTICE = True` sends everything to separate places — config
`%APPDATA%\DevToolsPractice`, state files, Scratch, SharePoint and dist under
`C:\Dev\DevTools_practice\` — seeded once (read only) from the real ones. For
trying out a change to Dev Tools itself without touching any share. Off in
normal use; the window title says "practice folders" when it is on.

### Versions.txt
`<scratch>/Versions.txt` is a flat `Name = vX.Y.Z` list, written after every
successful build and every copy (`write_version_to_txt`) and read back by
`read_versions_txt()`. **Cached for 15 s** (`_VERSIONS_CACHE`): the file sits on
the scratch share, where one read costs tens of milliseconds, and the project list
asks for it once per row. The cache is dropped on every write;
`read_versions_txt(force=True)` is used for read-modify-write and by the Copy
Manager when it (re)builds its list.

---

## Responsiveness and safety rules (both tabs)

| Rule | Where |
|------|-------|
| **No widget is touched from a worker thread.** Workers log through `LogHub.write` (thread-safe) and hand everything else to the UI thread with `on_ui(fn, …)`. A worker that needs an answer (the icon conflict dialog during *Copy after build*) uses `ask_on_ui(fn, …)`, which waits on an Event. `on_ui` is a queued signal on `_Invoker`, created by `init_ui_thread()` right after the QApplication. | `dt_common.py` |
| **No share I/O on the UI thread.** The icon check is split: `scan_icon_conflicts` (stat on the shares, in a thread) → `CopyTab.resolve_icon_conflicts` (dialogs, UI thread) → deploy worker. The missing-dist question is asked before the build thread starts (`_ensure_dist_root`). | `dt_core.py`, `dt_copy.py`, `dt_builder.py` |
| **Batched log.** `LogHub` queues lines from any thread and writes them every 40 ms in one append to every log pane (each tab has one; both show the same text). A pane follows the end only while it is scrolled to the bottom; a final report passes `force_scroll=True` and always lands in view. **Clear** empties both. | `dt_common.py` |
| **Wheel never edits an unfocused drop-down.** `WheelGuard` (installed by `guard_wheel`) ignores the wheel on a combo box that has not been clicked into, so scrolling the Copy Manager list over a Version box cannot change which version will be copied, and a turn over Group cannot re-group a project. | `dt_common.py` |
| **No list rebuild on a parameter change.** Builder: `_render` builds every row (Ignored ones too) once; a group change and *Show ignored* only re-place them (`_layout_rows`). After a build only the version column is repainted unless the set of projects/helpers changed. Copy Manager: `update_rows_in_place()` re-lists versions and repaints status after a copy and after a build; only **Refresh**, **Change** and **Set paths** call `load_programs()`. Ticks, chosen versions and scroll stay. | `dt_builder.py`, `dt_copy.py` |
| **A re-layout is not a click.** `_layout_rows` takes all rows out of the tree and puts them back; while they are out Qt hands the current row to a neighbour. With the tree's signals on, that counted as a click and unfolded the neighbour — choosing a Group unfolded Diagnostic and showed its listener. Signals are blocked during the re-layout, the current row is restored before they come back, and `_on_current_changed` ignores calls while `_updating`. Test: `test_ui.py::test_group_change_unfolds_nothing`. | `dt_builder.py` |
| **No console windows.** Every `py` / PyInstaller `Popen` passes `creationflags=NO_WINDOW`; frozen, `sys.executable` is Dev Tools itself, so the Internal Builder command uses `py`. | `dt_core.py` |

---

## The look (`dt_common.py`)

Copied from Image Tools, not imported (Image Tools copies these constants between
its own modules the same way):

| Piece | Source in Image Tools |
|-------|-----------------------|
| App sheet, scroll bars | `main.py` application stylesheet |
| Program tabs (Builder / Copy Manager) — `MAIN_TABS_QSS` | `main.py`, the big blue tabs |
| Tick boxes — `CHECKBOX_QSS` + white tick | `is_t.py::_CHECKBOX_STYLE`, tick from `if_t.py::_tick_image_url` |
| Drop-downs — `COMBO_QSS` | `sf_t.py` combo + popup colours |
| Buttons — `BTN_QSS`, accent `#primary` | `wk_t.py::_BTN_QSS`, Image Slider's Apply |
| Lists — `TREE_QSS` | `sf_t.py::_TABLE_QSS` |
| Panels — `Section(title, accent)` | `is_t.py::CollapsibleSection` (without folding), accents of its sections |

A styled tick box draws no tick of its own, so the white tick is a picture drawn
once into `%TEMP%\dt_tick_w.png` (`_tick_image_url`). `light_palette()` is kept as
well: the PC runs Windows in dark mode, and the palette covers popups a sheet does
not reach.

---

## dt_builder.py — Builder

### List
- `QTreeWidget` with columns **#** (build order), **Program** (tick box; the fold
  arrow sits here, `setTreePosition(COL_NAME)`), **Next version**.
- Rows: section rows (Main / Side / Ignored projects, small grey capitals, not
  selectable), project rows, helper rows as children of their project.
- `project_groups` (persisted in `builder_settings.json`); Ignored rows are hidden
  unless **Show ignored** is ticked. A program that disappears from view loses its
  tick: what builds is what is on screen.
- **Build order = click order**: `tick_order` records every tick; ticking a
  project pushes the tick onto its helpers right behind it (one-way push, not a
  lock — unticking a helper afterwards sticks). **Select all** appends what was not
  ticked in list order; **Clear** empties. `build_queue()` gives
  `[("project", Path) | ("helper", dict)]` in that order.
- `ticked` / `tick_order` are kept outside the rows, so a rebuild of the list never
  throws a selection away.
- Clicking a row shows it in the panel; clicking a project that has helpers also
  unfolds it (the helpers are usually what the click is about). The arrow folds.

### Selected program panel
Name, Main script (`guess_main_py`, or the helper's script), Last version, **Next
version** (editable; an override per program name in `next_override`, dropped when
it equals the default or is emptied), **Group** (disabled for a helper — a helper
follows its parent), **Open ReadMe** (a helper's ReadMe is `ReadMe_<helper name>`
in its parent's folder, the name the Launcher looks for; offers to create a
missing one).

### Versions
`last_version(name)` — Versions.txt first, then the highest `vX.Y.Z` folder in
dist (cached per name, dropped after a build). Next = patch + 1, or `1.0.0` for a
program never built.

### Build (`_build_selected`)
On the UI thread: the queue, a version check, the dist folder question, the copy
destinations (read once). Then a worker thread runs the builds in a thread pool,
**several at once** (`dt_core.parallel_builds`: at most 3, one per 4 CPU cores and
per 2 GB of free memory, never more than are ticked): `dt_core.build_one_project`
/ `build_one_helper` / `build_internal_builder`. Each build has its own PyInstaller
work and dist folder; the shared files (Versions.txt, `build_usage.json`,
`build_times.log`) are written under one lock (`_SHARED_FILE_LOCK`). With more than
one build at once every PyInstaller line is prefixed with `[<program>]`. With
**Copy after build**, `CopyTab.copy_one_after_build` runs right after each
program's build, **one copy at a time** (it may ask about an icon), while the
other builds go on. The report lists the programs in build order whatever order
they finished in. Each build's time is split into phases by `PhaseClock` (see
below) and appended to `build_times.log`. Finally `_on_build_finished` (UI thread): versions refreshed, the
Copy Manager rows updated in place, the build report, and with *Copy after build*
the copy report and the overall report (`CopyTab.copy_after_build_report`).

---

## dt_core.py — build

### `build_one_project(p, ver, dist, root, log)` steps
1. Resolve the entry `.py`; output dir `<dist>/<project>/v<version>/`.
2. Delete an existing version folder first (`_rmtree_readonly`) — PyInstaller
   `--noconfirm` fails on the read-only flags OneDrive leaves behind.
3. Collect the extra `.py` files from the project root — **`test_*.py` excluded**.
3a. `check_module_homes()` — **the build stops** when an imported module has no
   `.py` in the project folder, or has a second copy in another project folder.
   PyInstaller is given the project folder as its only `--paths`, so a module
   reached at runtime through `sys.path` is invisible to it and the copy in the
   project folder is bundled instead — that is how the built Spectra tab stayed
   five weeks behind `Spectra/sp_t.py` with no error. Imports are read with `ast`,
   not a regex (prose in a docstring would fail the build). `module_homes()` scans
   only the top level of each program folder and skips `_internal`, `archive`,
   `testing`, caches, `test_*.py`, `main.py`.
4. Read the optional `build_config.json`.
5. Runtime asset folders (`ASSET_DIR_NAMES`: images, sounds, assets, icons, img,
   audio, fonts, templates) ship with every build automatically — one list shared
   with the deploy.
6. `--collect-all` **only** for what `build_config.json` lists (with the
   packages' `.tests` submodules excluded, never `.testing`). Until 16.10.2026 it
   was added on its own for numpy, scipy, sklearn, cv2, matplotlib and pandas;
   that pulled every submodule and data file into every build and made the
   analysis several times longer. PyInstaller's own hooks bundle what they need.
   A built program that misses a module gets it in its own `build_config.json`
   (`hidden_imports` first, `collect_all` only if that is not enough).
7. `py -m PyInstaller --onedir --windowed --noconfirm` in a private work folder
   (`new_workdir`; a shared one was deleted under a running build), with the icon
   both as `--icon` and inside the bundle (`--add-data`).
8. Move the built app one level up into the version folder.
9. Rename the exe to `<Name> v<version>.exe`, copy `icon.ico` next to it, log
   whether the exe carries its own icon (`exe_icon_count`).
10. Copy the main `.py` (as `<Name> v<version>.py`) and the extra sources.
11. Copy `extra_files` and asset folders.
12. Copy **both** user documents (`ReadMe_<name>`, `ReadMe_<name>_Full`) into the
    version folder — the deploy takes them from there first.
13. Delete the work folder, write Versions.txt, append to `build_usage.json`, log
    the output folder listing.

### Build times — `PhaseClock`, `build_times.log`
`_run_logged` passes every PyInstaller line to `PhaseClock.on_line`, which moves
on when PyInstaller starts a step: **analysis** (start until `Building PYZ`),
**package** (PYZ / PKG / EXE), **collect** (`Building COLLECT`; a one-file helper
has none) and **finish** (everything after PyInstaller: moving files, assets,
ReadMes). The split is shown in the `Built:` log line and in the report, and one
tab-separated line per build goes to `build_times.log` beside `build_usage.json`:
date, program, version, total, the four phases and the copy-after-build time.

### Per-project `build_config.json`
```json
{
  "collect_all":      ["module"],
  "collect_binaries": ["module"],
  "hidden_imports":   ["module"],
  "copy_metadata":    ["module"],
  "exclude_modules":  ["module"],
  "extra_files":      ["data.json", "images"],
  "extra_exes":       [{"script": "helper.py",
                        "name":   "My Helper",
                        "windowed": false,
                        "hidden_imports": ["module"],
                        "extra_files": ["data.dat"]}]
}
```

### Helper exes — `extra_exes`
A helper lives in a project's folder but is **started on its own** (Diagnostic's
Webex listener). `find_helpers(projects)` reads them on every refresh from each
`build_config.json` (cached against the file's mtime), so nothing is registered and
the list cannot go stale. A `str` entry counts as `{"script": …}`; a missing script
is dropped.

`build_one_helper(h, ver, dist, log)` builds into **`<dist>/<helper name>/vX.Y.Z/`**
— its own dist entry and version: a helper can be built without its parent, and a
parent version folder holding only the helper would be "a version of the app that
is not the app". **`--onefile`** (cannot collide with the app's `_internal`,
survives being copied by hand) and **`--console`** by default (its window is the
sign it is alive). `extra_files` are inherited from the project config by default;
`hidden_imports` are the project's plus the helper's. A helper that fails only logs
a warning.

---

## dt_core.py / dt_copy.py — Copy Manager

### State
`copy_manager_state.ini`, section `[deployed]`, `name = vX.Y.Z`
(`load_state` / `save_state`). "Last deployed" = Versions.txt, else this file.
`is_newer_version(latest, deployed)` → status **NEW** (green), **UTD** (red, up to
date), grey **NEW** for a program never built.

### List (`CopyTab.load_programs`)
Program folders under the root (`list_program_dirs`, skipping `_` and `.` folders
and `CM_IGNORE`), one row each, plus, indented, one per helper exe
(`find_helper_programs`). A helper whose name is also a program folder is that
program. Columns: Program (tick box), Version (drop-down of the dist version
folders, newest first), Status, Last deployed. The Program column is measured so a
name is never cut (`_fit_name_column`). Ticking a program pushes the tick onto its
helpers and unfolds them.

### Where things go
`dest_dir_for(dst_root, name)` — a helper resolves to `<dst>/<parent>/<helper>`,
everything else to `<dst>/<name>`. `helper_dir_names(name)` keeps the parent's
deploy from deleting the helper folder. `_move_legacy_toplevel_folder` carries a
helper's old top-level folder into its program's folder once (merge if the new one
exists; a locked file leaves the old folder standing and says so).

### `deploy_one_program(program_dir, version, roots, helper_parent_dir, log, icon_decisions)`
1. Find the exe, the two documents, the `.py` files, the icon and the extras
   (files **and folders**, `_internal` excluded).
2. Asset folders the build did not carry are taken from the source tree.
3. Per destination: create the folder (an unreachable one is skipped, the others go
   on), fix its archive, delete leftover folders — **except** `_internal`,
   `archive`, asset folders, shared data folders (`SHARED_DATA_DIR_NAMES`:
   `shared`), the program's helper folders and anything the new version brings
   (`incoming_dirs`); archive the old exe and `.py` files into
   `archive/vX.Y.Z/` (unversioned helper modules go with the live version; a locked
   file is skipped); copy the exe as `<program> <version>.exe`, the `.py` files, the
   extras (folders merged with `merge_dir`, never deleted first), both documents and
   the icon (a stale PNG icon is dropped; conflicts follow the icon decisions).
4. Returns per-kind counters and a file log for the reports.

### ReadMe lookup order
1. the version folder (the Builder copies both documents there)
2. **the source folder in the repo** — the live ReadMe; without this, builds from
   before the Builder copied the ReadMe shipped none
3. `<dist>/<program>/`
4. for a helper: its parent's source folder
5. the copy already published on the destination

`_normalize_name` ignores case, underscores and spaces. `find_readme_full_or_none`
mirrors `Launcher/l.py::find_readme_full_or_none` exactly — if the two diverge, a
publish lands a file the Launcher will not open.

### Icon conflicts
`scan_icon_conflicts` (thread): a destination `icon.ico` with a different size is a
conflict. `CopyTab.resolve_icon_conflicts` (UI thread): the side-by-side **Icon
conflict** dialog, or straight replace when *Replace icons without asking* is
ticked on the Builder tab. No conflict → copy where missing, skip where the same.

### Other buttons
- **Select new** — ticks every NEW row and sets it to its newest version.
- **Copy ReadMe only** — `copy_readme_only`, both documents, same lookup order.
- **Fix** — `fix_destinations`: per program folder (and helper folder) on the
  ticked destinations: keep only the newest exe on top, `fix_archive_dir` (flat
  files into `vX.Y.Z/`, newest timestamped build per version),
  `reunite_unknown_helpers` (helper `.py` files back from `archive/unknown/` by the
  timestamps in the archive logs), `normalize_version_folder_names` (one copy per
  file under its importable name), refresh `icon.ico`, drop stale PNG icons.
- **Deploy Libraries** — `deploy_libraries`: zips the newest Internal Builder
  `_internal` onto the scratch and unpacks it into every ticked program folder
  there; unchanged files (same size, never `.pyd`) are left alone, files no longer
  in the bundle are deleted. Progress bar under the buttons.

### Reports
`copy_summary` (what went where, totals over all destinations), `overall_summary`
(the whole job, build and copy together; a program built but not copied is named),
`build_summary`. Every kind of failure counts as an error (`ERROR_KEYS`).

### Deploy output layout
```
<dst_root>/<program_name>/
  <program_name> <version>.exe
  *.py
  ReadMe_*.txt
  icon.ico
  images/ sounds/ assets/ icons/   ← whatever the build brought along
  shared/                          ← written by the running program, never touched
  archive/
    vX.Y.Z/
      <program_name> vX.Y.Z.exe
      if_t.py …                    ← importable names, runnable snapshot
      archive_log.txt
```

---

## Tests (`testing/`)
| File | What |
|------|------|
| `test_core.py` | Logic on throw-away folders: discovery, Versions.txt, module home check, deploy + redeploy + archive, helper inside parent, icon scan, archive fix, reports. |
| `test_ui.py` | Both tabs offscreen: ticks and build order, groups, the group-change unfold bug, next-version override, a full copy. |
| `test_helper_nested_dest.py` | Helper destinations, legacy folder move/merge. |
| `test_icon_app_id.py` | Every program's taskbar identity and icon nudge (repo-wide). |
| `render_tabs.py` | Screenshots of both tabs, window shown without taking focus. |
| `audit_*`, `_dbg_*` | Taskbar / exe identity tools (also used by Screenshots/testing/audit_icon_launch.py). |

## Dependencies
- `PySide6` — UI (QtCore, QtGui, QtWidgets only)
- `PyInstaller` — invoked via subprocess (`py -m PyInstaller`)
- `configparser` — Copy Manager state file
- `ctypes` — taskbar identity, exe icon count
