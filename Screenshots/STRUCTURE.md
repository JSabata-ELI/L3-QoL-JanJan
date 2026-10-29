# Screenshots — STRUCTURE

> Verified against source: 2026-10-16 · PySide6 (replaced the Tk `s.py` that day;
> the Tk code is kept in `C:\Dev\backup\Screenshots_Tk_20261016`).

User-facing documentation: `ReadMe Screenshots.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Screenshots_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `main.py` | Entry point and the main window (`ScreenshotsWindow`): selection, monitors, presets, Copy / Auto / Live run control. Taskbar identity (`_icon_app_id`, `_nudge_taskbar_icon`), version from the exe name (`_detect_version`). |
| `ssq_core.py` | Everything that is not a window: camera tables, station rules, Windows capture layer, archive lookup, auto time grid, the preview's tone/palette rendering (`Tone`), presets file, output naming, `ArchiveCopier`, `LiveCapture`. No Qt import. |
| `ssq_preview.py` | `PreviewWindow` (grid of copied pictures, cycle bar for auto runs, hover popup) and `ZoomWindow` (one picture: tones, crop, drawing). |
| `ssq_dialogs.py` | `PresetManager`, `LabelsDialog`, `DetailDialog`, `identify_monitors`. All non-modal. |
| `ssq_common.py` | App look (stylesheet + light palette, drawn tick/radio/arrow PNGs), `on_ui` thread hand-over, `LogHub` (Diagnostics), app-wide wheel guard, drawn button icons (`icon`, `icon_button`), `CollapsibleSection`, PIL → Qt picture bridge. |
| `custom_presets.json` | The preset list, written beside the exe/script on first change (v2 `{"version": 2, "presets": {...}}`, key order = list order; a v1 flat file is merged over the built-ins). Same file and format as the Tk version. |
| `icon.ico` | Window / taskbar / exe icon. |
| `build_config.json` | Builder: hidden imports (`ssq_*`, numpy, PIL.ImageGrab/ImageDraw, zoneinfo, tzdata), excluded Qt modules and tkinter. |

---

## ssq_core.py

Most of it is s.py's own code, moved unchanged (constants, `CAM_INFO`,
`CAM_WINDOW_TITLES`, `CAM_CATEGORIES`, `PRESETS`, `MONITOR_LAYOUTS`,
`STATION_CAMERAS`, `parse_cpva_header`, the monitor / window capture layer,
`find_camera_folders_bulk`, `scan_camera_dir`, `find_frame_at_or_before`,
`auto_tick_target_ns`, `auto_tick_delay_ms`, `auto_should_skip`, `fmt_span_ms`).
The rules behind them — the fixed time grid, frames taken at-or-before the
cycle's moment, the 10 s skip rule (old AND different from the previous
cycle's frame), the archive clock offset measured by cycle 1 — are unchanged;
see the Full ReadMe, sections 4.1–4.3.

| Name | Purpose |
|------|---------|
| `Tone` | the preview's rendering without a window: `_PALETTES` (Image Tools' list, "Original" for "Default"), `_NI_PALETTES` (Binary, False Colors: raw counts, tones off), `_tone_lut` (contrast → gamma → brightness, one 256-entry curve), `_render_tuned` (the ONE path for grid, popup, zoom and Save), `_fit_to_box`, `_resample_for` (Binary shrinks by whole pixels). |
| `list_monitor_devices()` | `\\.\DISPLAYn` per monitor, in `list_monitors_rects` order — matches a monitor number to its Qt screen (Identify). |
| `cam_available` / `cam_tooltip` | window-mode greying (L3BT never; the office box `CZOW_STATION` follows the Layout box via `LAYOUT_TO_STATION`) and the greyed camera's "Available on: …". |
| `planned_output_count`, `ensure_output_path`, `monitor_targets`, `camera_file_name`, `prague_file_ts`, `sanitize_folder_name` | naming: one output = FILE name, more = FOLDER name; `<cam>__<Prague time of the frame>.<ext>`, label token joined with `__` in front or behind. |
| `presets_path`, `factory_presets`, `load_presets`, `save_presets` | the preset list. |
| `icon_file()` | icon.ico beside the exe → `sys._MEIPASS` → beside the script. |
| `ArchiveCopier` | listing cache of every camera folder in use (re-read every 3 s by `start_cache_refresh_worker`, dropped after an hour unused), camera→folder map per hour (`resolve_cam_folders`), `prime` before an auto run, `label_settings` + `bump_label_index`, `last_frame` for the skip rule, `copy_cameras` (CPVA in a pool of 8, merged back in camera order; window mode sequential with one desktop snapshot). |
| `LiveCapture` | Start live: one thread polls the folders every 80 ms, one copies the queue into `<dest>/Live`. |

## main.py — ScreenshotsWindow

- Camera state is one bool per camera (`_cam_sel`) and **one checkbox per
  (section, camera)**: a camera in two sections (SCG1SDB_FF) has two boxes that
  follow each other (`_set_cam`). A click recomputes the monitors from the
  cameras' CSS windows (`_recompute_monitors_from_cameras`); presets and the
  section "All" set cameras without that.
- Monitors: `monitor_vars` + `_apply_monitor_effective` (manual vs. preset
  reference counts, All screens). The grid comes from live geometry
  (`geometric_monitor_grid`); on the office box with one monitor the
  `MONITOR_LAYOUTS` placeholder is shown instead.
- Presets: buttons in a 6-column grid, `presetOn` property for the active
  style; `toggle_preset` / `_preset_add` / `_preset_remove` keep the monitor
  reference counts. `save_presets` / `rebuild_preset_buttons` are what the
  Preset Manager calls. `Clear all` (objectName `danger`, light red) sits at
  the bottom right of the Presets box.
- Selected cameras: everything at once, about five rows per column, the box
  as tall as its content (`_refresh_selected_cams_table`); a horizontal bar
  only when the columns run out of width.
- Copy (`_run_copy_worker`): one desktop grab for every monitor, then
  `copier.copy_cameras(newest=True)`, progress bar, preview of the written files.
- Auto: `_start_auto_copy` fixes interval / max cycles / out_dir; ticks on
  the grid via one single-shot `QTimer` (`_schedule_auto_tick`); each tick
  grabs the screens at once in a thread and queues the camera work;
  `_auto_drain_loop` copies in order (`_process_tick`: drop when > 10 s
  behind, cycle 1 measures `_auto_offset_ns`). One persistent preview
  (`_auto_preview_window`), closed-by-user stays closed until the next Start
  or re-ticking Preview.
- Live: `LiveCapture`; the preview opens with the collected files on Stop.
- Worker threads touch widgets only through `on_ui`.

## ssq_preview.py

- `PreviewWindow`: action bar (Save / Delete / Delete and Try Again; in cycle
  mode Delete this cycle), tone row (Contrast, Brightness — each ↺ + Auto —,
  Gamma ↺), second row (Zoom, Palette). Thumbnails are rendered at the
  screen's pixel density; a tone change re-colours in place (`_retone`), a
  zoom/width change re-grids (`_relayout_if_needed`). Cycle mode:
  `set_cycle_history`, `show_cycle`, `append_path` (one new tile, no full
  redraw), Follow latest. Save writes `<name>_annotated.png` for every picture
  edited in the zoom window.
- `ZoomWindow`: overlay kept as a PIL RGBA picture (what Save bakes in);
  free-hand strokes are previewed as vectors and drawn into the overlay on
  release; right-drag = crop; Zoom tool only magnifies.

## Tests (`testing/`)

Run from the program folder: `python testing/<name>.py`. Qt tests start with
`import qt_harness as H` (`minimal:enable_fonts` — real fonts, nothing on screen).

| File | What it pins |
|------|--------------|
| `test_core.py` | naming, label counter, presets file, station rules |
| `test_tone.py`, `test_palettes_match_image_tools.py` | the tone curve; palettes identical to Image Tools' `is_t.GRADIENTS` (reads is_t.py) |
| `test_frame_pick.py`, `test_auto_grid.py` | at-or-before frame pick, skip rule, the time grid |
| `test_main_window.py` | the window, side windows, preview/zoom/annotate, and a real Copy + 2-cycle Auto run on a fake archive in %TEMP% |
| `render_windows.py` | every window to `render_*.png` for looking |
| `audit_icon_launch.py` | launches the program once WITHOUT focus (`SSQ_NO_ACTIVATE`), checks window icon and taskbar button |

Deck pictures: `C:\Dev\screenshots_capture\capture.py` + `redeck.py`.

## Dependencies
PySide6, Pillow, numpy, tzdata (zoneinfo on Windows), ctypes.
