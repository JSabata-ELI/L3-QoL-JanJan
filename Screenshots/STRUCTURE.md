# Screenshots — STRUCTURE

> Verified against source: 2026-08-05 · `s.py` 4569 L

## Files

| File | Description |
|------|-------------|
| `s.py` | Single file. The whole tool (tkinter/ttk). |
| `custom_presets.json` | User presets, written next to the exe/script (`_custom_presets_path`). |
| `icon.ico` | Window / taskbar icon (`set_app_icon`). |

---

## Purpose

Grabs camera frames and monitor screenshots for a shift/experiment log and drops
them into one destination folder (usually the scratch share). Two sources:

- **Archiver (`cpva`)** — copies the newest frame of each selected camera out of
  the CPVA image store on the network.
- **Screenshot window** — captures a monitor (or all screens) from the local
  session, cropping the Win11 DWM shadow away.

Both can be fired once (**Copy**), on a fixed interval (**Auto**) or continuously
(**Live**), and the result can be reviewed in a preview grid before it is kept.

---

## Constants and tables

| Constant | Meaning |
|----------|---------|
| `NETWORK_ROOT` | `\\users-L3.tier0.lcs.local\cpva-image-2026` — CPVA image store |
| `DEFAULT_DEST` | `\\hapls-share.lcs.local\scratch` |
| `DEFAULT_DEST_CZOW` | local fallback destination for off-lab use |
| `RUN_FOLDER_FMT` / `TS_FMT` | `%Y-%m-%d__%H-%M-%S` |
| `CAM_INFO` | `{UI cam name: CPVA id}` — ~80 cameras |
| `CAM_CATEGORIES` | UI grid grouping: LT1, LT2, LT4 + PAD, LT5, LT6, LT7, Compressor, L3BT |
| `CAM_ALIASES` | UI name → folder spelling (`PTM1_1w_NF` → `PTM11w_NF`); L3BT aliases are added from `L3BT_FOLDERS` by `add_l3bt_aliases_from_foldernames` |
| `PRESETS` | Built-in one-click camera sets (All cameras, per-section, …) |
| `MONITOR_LAYOUTS` | Physical monitor arrangement per station (`VIS-01`, `VIS-02`, `OPR-01…03`) |
| `STATION_CAMERAS` | Which cameras exist at which hostname; others are greyed out |
| `IDENTIFY_MS` | 2000 ms — how long the monitor number overlay stays up |
| `TS_IN_NAME_RE` | `_<unix-ns>.<ext>` timestamp in archive filenames |
| `_TIMESTAMP_IN_FILENAME_RE` | `YYYY-MM-DD__HH-MM-SS` anywhere in a saved filename |

### Station detection
`_get_station_id()` = uppercase hostname. `_cam_available_here(cam, station)` /
`_cam_available_on_stations(cam)` decide whether a camera checkbox is enabled;
a camera not listed for any station is allowed everywhere. Aliases are resolved
before the lookup.

---

## CSS title parsing

| Function | Purpose |
|----------|---------|
| `parse_cpva_header(header)` | `(cam_name, cam_id)` out of a CSS window title / folder name; normalizes `C03-`, `L3-`, `L3BT-` spellings |
| `_norm_suffix_mode(raw)` | inserts the `_` before an `NF`/`FF`/`DF` suffix |
| `cpva_label(cam_ui)` / `is_known_camera(cam_ui)` | UI name ↔ `CAM_INFO` |
| `display_timestamp_only(name)` | UI-only: reduces a filename to its timestamp for the preview caption (the camera is already obvious from the selection); falls back to the plain name |

---

## Windows API layer

### DPI
`_set_dpi_awareness()` — Per-Monitor-Aware v2 → `shcore` → legacy fallback.
Without it window captures come out cropped on HiDPI screens.

### Monitors
| Function | Purpose |
|----------|---------|
| `list_monitors_rects()` | `EnumDisplayMonitors` → list of rects |
| `_cluster_1d(values, tol)` | groups nearly equal coordinates |
| `geometric_monitor_grid(rects)` | rects → `{(row, col): monitor_index}` |
| `monitor_index_for_hwnd(hwnd)` | which monitor a window sits on |
| `monitors_for_cameras(cams)` | monitors that show the given cameras (via `MONITOR_LAYOUTS`) |
| `take_screenshot_monitor_png(dst, monitor_index)` | one monitor, or `None` = all screens |

### Window capture
`take_screenshot_window_png(dst, hwnd)`:
1. `GetWindowRect` — bitmap including the shadow
2. `PrintWindow` with `PW_RENDERFULLCONTENT`
3. `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)` — real visible bounds
4. PIL crop to those bounds → PNG

Helpers: `_get_window_text`, `_norm_win_title`, `_get_window_class`,
`find_window_by_title_substring`, `_get_window_bounds`,
`_build_window_needles_static(cam)` / `App.build_window_needles(cam)` — the title
substrings a camera's CSS window may use.

---

## Archive lookup (CPVA)

| Function | Purpose |
|----------|---------|
| `today_day_root()` / `target_cpva_hour_dir()` | `NETWORK_ROOT/Y/M/D/H` (UTC tree) |
| `_find_latest_existing_hour_dir(log)` | newest hour folder that actually exists |
| `find_camera_folders_bulk(day_root, cams, log)` | one scan → `{cam: folder}`; uses `norm_folder` / `tokenize_user` / `contains_token` for fuzzy matching |
| `find_image_near_click_fast(cam_dir, t_click_ns, …)` | newest frame at/near the click time, from the cached directory listing |
| `safe_mtime(p)` | `stat()` that never raises |

`App` keeps `_cam_dir_cache` / `_cam_dir_cache_time` (+ lock) and
`_start_cache_refresh_worker()` re-scans every cached camera folder every 3 s in
the background, so a Copy does not pay for a cold network listing.

---

## UI helper classes

| Class | Purpose |
|-------|---------|
| `CollapsibleSection(ttk.Frame)` | expandable camera category with a right-hand slot for its "All" checkbox |
| `ToolTip` | delayed tooltip with a text callback (used for greyed-out cameras and preset contents) |
| `PreviewWindow(tk.Toplevel)` | grid of the copied images — see below |
| `App(tk.Tk)` | main window |
| `set_app_icon(win, ico, app_id)` | icon + AppUserModelID (taskbar icon) |

---

## PreviewWindow — review grid

Opened after a copy (when **Preview** is ticked) with the freshly written files.

- Action bar: **✔ Save** (`_do_keep` → `_save_all_annotated`, writes
  `<name>_annotated.png` for every image that has drawings/crop, then closes),
  **🗑 Delete** (`_do_delete`), **🔄 Delete and Try Again** (`_do_try_again` →
  re-runs the same copy).
- Image bar: Contrast slider + Auto, Brightness slider + Auto, Zoom, Palette
  (`_PALETTES`, LUTs built lazily by `_build_lut`).
- Grid: `_compute_layout` / `_relayout_if_needed` / `_redraw` fit the thumbnails
  to the window (`_THUMB`, `_MIN_THUMB`, `_CELL_PAD`, `_FILL_SLACK`);
  `_fit_caption` shortens the caption, `_process` builds one thumbnail,
  `_fit_to_box` scales the whole frame (never crops, also enlarges).
- Hover shows a bigger popup (`_show_popup` / `_hide_popup`); click opens
  `_open_zoom_window` — zoom, contrast/brightness (+auto), palette, crop with
  preview + Clear crop, freehand/line/rect drawing with colour and width, Undo,
  Clear drawing, **✔ Apply** / **↺ Revert**. State per image lives in
  `_img_states` (`overlay`, `crop_rect`) and is what Save bakes in.

### Brightness / contrast rules
`CONTRAST` = multiplicative gain, `BRIGHTNESS` = additive offset — never mixed.
- `_manual_contrast(arr, contrast)` — gain curve around mid-gray, `[-127, 127]`
- `_autostretch(arr)` — 0.1/99.9 percentile stretch → `(arr, equivalent slider value)`
- `_auto_brightness(arr, target=255, p_high=99.5)` — offset so the top percentile hits 255
- `_contrast_from_gain(gain)` — inverse of the gain curve, used to park the slider
- `_apply_bc(...)` — the single pipeline used by every preview path; returns the
  slider-equivalent values actually applied
- Auto overrides the matching manual slider, greys it out, and
  `_sync_auto_sliders()` parks the disabled slider on the computed value
  (`_syncing_sliders` guards the re-entrant redraw)

---

## App — main window

### Layout (`_build_ui`)
- **Settings** box: destination (`...` picker, 📂 open), run/file name, **Copy**,
  **Labels**, **Detail**, **Preview** checkbox
- **Auto row**: `Auto every N s`, `max cycles`, ▶ Start auto, ⏺ Start live,
  Config presets, Clear all
- **Source** box: Archiver / Screenshot window radio buttons
- **Monitors** box + All screens + **Identify**, plus a station layout picker
  (`_render_monitor_layout`, `_render_layout_placeholder`)
- **Presets** box: built-in + custom preset buttons (toggle, active ones styled)
- **Selected cameras** table (`_refresh_selected_cams_table`) and **Diagnostics**
  log (read-only Text, copy/select shortcuts still work)
- **Cameras** panel: search box + `CollapsibleSection` per category with an "All"
  checkbox; column count adapts (`_update_cam_cols`, `_cam_cell_px`)

### Selection / presets
| Method | Purpose |
|--------|---------|
| `_selected_cameras()` / `_selected_monitors()` | current selection |
| `_toggle_category_all(cat)` / `_update_category_check(cat)` | category checkbox |
| `_on_cam_var_changed(cam)` → `_recompute_monitors_from_cameras()` | picking cameras preselects the monitors they live on |
| `toggle_preset(name)` / `_refresh_preset_button_styles()` | preset on/off |
| `_preset_add/_preset_remove/_open_preset_manager/_save_custom_presets` | preset editor; custom presets override built-ins with the same name and persist in `custom_presets.json` |
| `_clear_all_selections()` | resets cameras, monitors and presets |
| `_expand_sections_with_selected_cams()` / `_sync_sections_to_selected_cams()` | opens the categories that hold the selection |
| `on_identify_monitors()` | numbered overlay on every screen for `IDENTIFY_MS` |

### Output naming
- `_planned_output_count()` — monitors + cameras; one output means the name field
  is a **file** name, more than one means a **folder** name (`_update_name_label`)
- `_ensure_output_path(dest, run_ts, total)` — creates `dest/<name>` for multi-output
  runs, writes straight into `dest` for a single file
- `sanitize_folder_name(name)`, `_is_unc_path(s)`, `pick_dest()`, `open_dest()`
- Camera file name: `<cam>__<YYYY-MM-DD__HH-MM-SS>.<ext>`, where the timestamp is
  the frame's own ns timestamp converted to Prague time (`safe_mtime` fallback).
  Monitor shots: `<run_ts>_monitor<N>.png` / `<run_ts>_all.png`. A single output
  with a user name uses that name verbatim.
- **Labels** (`on_labels`) — per camera: on/off, `prefix`/`suffix`, free text and an
  optional auto-incrementing `idx` counter (per-camera `index`, ↺ resets it).
  Stored in `App._label_settings` (session state); the token is joined to the name
  with `__` and the counter advances after each successful copy.
- **Detail** (`on_detail`) — sidecar `.txt` note next to the run output, with
  Save / Delete file.

### Copy / auto / live
| Method | Purpose |
|--------|---------|
| `on_copy()` → `_run_copy_worker(monitors, all_screens, cams, source_mode, …)` | the one worker used by every path; screenshots first, then cameras; per-step progress, problem list, log |
| `_progress_show/_progress_update/_progress_hide` | progress bar (auto runs grow the maximum per cycle) |
| `_set_busy` / `_set_state_recursive` | disables the UI while a run is in flight |
| `_toggle_auto_copy` / `_start_auto_copy` / `_run_auto_cycle` / `_schedule_next_auto_cycle` / `_stop_auto_copy` | interval capture; `max cycles` 0 = unlimited; a stop request aborts the running cycle |
| `_toggle_live` / `_start_live` / `_stop_live` | continuous capture with a live-updating preview |
| `_open_preview_async(out_dir)` | preview of everything in the output folder |
| `_open_preview_with_callbacks(...)` | preview of the files this run wrote, with the Try-again callback wired back to `_run_copy_worker` |
| `_open_auto_preview(entry)` | single reusable window with a ◀ / ▶ cycle browser over `_auto_cycle_history` |

### Misc
`timed(log, label)` context manager for timing log lines, `get_app_dir()`,
`open_in_explorer(path)`, `norm_query`, `clamp`/`_ctrl_backspace` entry helpers,
`_is_descendant`.

---

## Dependencies
- `PIL` (Pillow) — decode, LUTs, crop, PNG encode, thumbnails (`_PIL_OK` guard)
- `numpy` — brightness/contrast/LUT math (imported lazily inside the methods)
- `ctypes` / `wintypes` — DWM, PrintWindow, monitor enumeration, DPI
- `tkinter` / `ttk` — UI
