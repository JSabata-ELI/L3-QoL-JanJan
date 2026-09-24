# Screenshots — STRUCTURE

> Verified against source: 2026-08-05, re-checked 2026-08-19 (unchanged) · `s.py` 4569 L

User-facing documentation: `ReadMe Screenshots.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Screenshots_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `s.py` | Single file. The whole tool (tkinter/ttk). |
| `custom_presets.json` | The whole preset list, written next to the exe/script (`_presets_path`). v2 format `{"version": 2, "presets": {name: {...}}}`; the key order is the list order. A v1 file (flat name→data of custom presets only) is merged over the built-ins on load. |
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
An auto run keeps one preview window open and fills it as the pictures arrive.

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
| `PRESETS` | Factory seed for the preset list (All cameras, per-section, …); copied into `App._presets` when there is no saved list, and by "Restore defaults" |
| `MONITOR_LAYOUTS` | Physical monitor arrangement per station (`VIS-01`, `VIS-02`, `OPR-01…03`) |
| `STATION_CAMERAS` | Which cameras exist at which hostname; others are greyed out |
| `IDENTIFY_MS` | 2000 ms — how long the monitor number overlay stays up |
| `AUTO_BACK_WINDOW_NS` | 10 s — how far back from a cycle's own moment a frame may be taken, and how late a cycle may still be copied |
| `AUTO_MIN_INTERVAL_S` | 5 s — the shortest auto interval |
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
| `cpva_hour_dir_for_ns(ts_ns)` | the hour folder that holds a given moment's frames |
| `_find_latest_existing_hour_dir(log)` | newest hour folder that actually exists |
| `find_camera_folders_bulk(day_root, cams, log, hour_dir=None)` | one scan → `{cam: folder}`; uses `norm_folder` / `tokenize_user` / `contains_token` for fuzzy matching |
| `scan_camera_dir(cam_dir)` | one camera folder as sorted `(ts_ns, filename)` |
| `find_frame_at_or_before(cam_dir, t_target_ns, …)` | **the newest frame at or before the requested moment**, plus how far behind it is |
| `safe_mtime(p)` | `stat()` that never raises |

`find_frame_at_or_before` never answers with a frame from *after* the requested
moment — a cycle asks for the picture that existed at its own time. (The
previous nearest-in-either-direction rule was how one cycle ended up with frames
spread over ten seconds: for some cameras the nearest frame was a later one.) A
cached listing whose newest entry is already older than the requested moment
cannot answer it and is re-read even while its TTL holds.

`App` keeps `_cam_dir_cache` / `_cam_dir_cache_time` (+ lock, taken for reads as
well as writes) and `_start_cache_refresh_worker()` re-scans every cached camera
folder every 3 s in the background, so a Copy does not pay for a cold network
listing. Folders only enter that cache once they have been read, so
`_prime_camera_cache` reads them all when an auto run starts.

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
An auto run uses one single `cycle_mode=True` window for the whole run.

- Action bar: **✔ Save** (`_do_keep` → `_save_all_annotated`, writes
  `<name>_annotated.png` for every image that has drawings/crop, then closes),
  **🗑 Delete** (`_do_delete`), **🔄 Delete and Try Again** (`_do_try_again` →
  re-runs the same copy).
- `cycle_mode=True` adds a cycle bar at the top — `Cycle:` drop-down, ◀ ▶,
  `Follow latest`, and the running cycle's status — and changes the action bar:
  Save no longer closes the window, Delete removes only the cycle on screen
  (`on_cycle_change` tells `App` to forget it), and Try Again is not offered.
  `set_cycle_history` / `show_cycle` / `append_path` / `set_caption_extra` /
  `set_cycle_status` are the live interface: `show_cycle` swaps the grid in
  place, `append_path` grids ONE new tile (`_make_cell`) instead of decoding
  every earlier image again, and falls back to a full `_redraw` only when the
  column count changes. Following stops as soon as the user steps with ◀ ▶ or
  picks a cycle.
- Image bar, row 1: Contrast + ↺ + Auto, Brightness + ↺ + Auto, Gamma + ↺.
  Row 2: Zoom, Palette (`_PALETTES`, false-colour LUTs built lazily by
  `_build_lut`). Two rows because one would run off the window edge.
- `↺` also switches the matching Auto off (`_reset_contrast` /
  `_reset_brightness`): with Auto left on, the next redraw parks the slider back
  where Auto wants it and the button looks dead.
- Grid: `_compute_layout` / `_relayout_if_needed` / `_redraw` fit the thumbnails
  to the window (`_THUMB`, `_MIN_THUMB`, `_CELL_PAD`, `_FILL_SLACK`);
  `_fit_caption` shortens the caption, `_process` builds one thumbnail,
  `_fit_to_box` scales the whole frame (never crops, also enlarges).
- Hover shows a bigger popup (`_show_popup` / `_hide_popup`); click opens
  `_open_zoom_window` — zoom, contrast/brightness (+auto), palette, crop with
  preview + Clear crop, freehand/line/rect drawing with colour and width, Undo,
  Clear drawing, **✔ Apply** / **↺ Revert**. State per image lives in
  `_img_states` (`overlay`, `crop_rect`) and is what Save bakes in.

### Brightness / contrast / gamma rules
`CONTRAST` = multiplicative gain, `BRIGHTNESS` = additive offset, `GAMMA` =
midtone curve with the ends pinned — never mixed up.

- `_tone_lut(stats_arr, auto_contrast, contrast, auto_bright, brightness, gamma)`
  — builds ONE 256-entry curve in the order contrast → gamma → brightness, and
  returns it with the slider-equivalent values actually applied. Auto contrast is
  the 0.1/99.9 percentile stretch; auto brightness is the offset that puts the
  99.5 percentile (measured through the curve so far) at 255.
- `_gamma_curve(lut, gamma)` — above 1 lifts the midtones, below 1 deepens them.
- `_stats_gray(img)` — the 8-bit grey the auto modes measure, for any source mode
  (a 16-bit frame is normalised by its own min/max, as the archive writes it).
- `_contrast_from_gain(gain)` — inverse of the gain curve, used to park the slider.
- `_render_tuned(img, palette, …)` — **the one rendering path** for the grid, the
  hover popup, the zoom window and Save. A curve rather than a pixel transform is
  what lets the SAME settings apply to a grayscale image and to each channel of a
  colour one: with the `Original` palette a colour camera keeps its colours while
  its contrast/brightness/gamma move. (Previously colour survived only while
  every control sat at its default, so ticking Auto contrast looked like it was
  also changing the palette.)
- `_settings_for(state)` — an image's own settings if it was edited in the zoom
  window, otherwise the toolbar's; `gamma` defaults to 1.0 so states saved before
  gamma existed stay valid.
- `_apply_bc(...)` — grayscale-only shorthand over `_tone_lut`, kept for tests.
- Auto overrides the matching manual slider, greys it out, and
  `_sync_auto_sliders()` parks the disabled slider on the computed value
  (`_syncing_sliders` guards the re-entrant redraw).

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
- **Presets** box: one button per preset in the saved list order (toggle, active ones styled)
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
| `_preset_add/_preset_remove` | switch one preset's cameras and monitors on/off |
| `_load_presets/_save_presets/_factory_presets/_presets_path` | the one ordered list `self._presets` (name → {cams, mons}), loaded from and written to `custom_presets.json` |
| `_open_preset_manager(offscreen=False)` | preset editor: New / Remove / rename on Save / drag a row to reorder / camera search / Restore defaults. Order changes rewrite `self._presets` and call `_rebuild_preset_buttons()`. `offscreen=True` is for tests — built far outside the desktop, no input grab. |
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
| `on_copy()` → `_run_copy_worker(monitors, all_screens, cams, source_mode)` | the manual **Copy** button: one set of pictures for the moment of the click |
| `_copy_cameras(cams, source, target_ns, out_dir, …, on_file, cycle)` | the shared camera step, used by Copy and by every auto cycle; CPVA cameras run in a thread pool and are merged back in camera order, window captures stay sequential; `on_file` fires per copied file |
| `_copy_one_camera(...)` | one camera: pick the frame, apply the skip rule, build the name, copy |
| `_resolve_cam_folders(target_ns, cams)` | camera → archive folder, cached per hour folder (the mapping only changes when the archive rolls over) |
| `_progress_show/_progress_update/_progress_hide` | progress bar (manual Copy only; auto shows its progress as tiles appearing in the preview) |
| `_set_busy` / `_set_state_recursive` | disables **Copy** while a manual run is in flight |
| `_toggle_live` / `_start_live` / `_stop_live` | continuous capture |
| `_open_preview_with_callbacks(...)` | preview of the files a manual run wrote, with the Try-again callback wired back to `_run_copy_worker` |

### Auto copy — a fixed time grid

Cycles sit on a grid measured from the start of the run, and each one records
the moment it belongs to. The copying may lag; the pictures may not — a cycle
processed late still fetches the frames from its own recorded moment.

| Method | Purpose |
|--------|---------|
| `_toggle_auto_copy` / `_start_auto_copy` / `_stop_auto_copy` | start/stop; `max cycles` 0 = unlimited; start primes the camera listings and opens the live preview |
| `auto_tick_target_ns(t0_ns, n, interval_s)` | the moment cycle n belongs to — always exactly `interval_s` apart |
| `auto_tick_delay_ms(t0_mono, n, interval_s, now)` | when tick n is due, measured from the start of the run (never "one interval after the last cycle finished") |
| `_schedule_auto_tick(n)` / `_on_auto_tick(n)` | arm the next tick; a tick records its moment, grabs the screens **at once** in its own thread, and queues the camera work |
| `_grab_tick_screenshots(tick)` | the screen can only be captured now, so it is captured at the tick itself |
| `_auto_drain_loop` / `_process_tick(tick)` | one worker copying the queued cycles in order; logs `copied N/M`, the `spread` of the frames and the `lag` behind the grid |
| `_prime_camera_cache(cams)` | one listing per camera before cycle 1 — otherwise the first cycle pays a cold share read per camera |
| `auto_should_skip(age_ns, src, prev_frame)` | a frame older than `AUTO_BACK_WINDOW_NS` (10 s) is skipped unless it is the very frame the previous cycle used |
| `_auto_offset_ns` | how far the archive's clock runs behind this PC, measured by cycle 1 |
| `_preview_begin_cycle` / `_preview_add_file` / `_preview_end_cycle` / `_forget_cycle` | the live preview: a cycle is registered, then each file appears as it lands |
| `_auto_preview_window` / `_close_auto_preview` | the one `cycle_mode` `PreviewWindow` for a whole run |

A cycle more than 10 s behind its own moment is dropped with a log line (the
archive can no longer answer for that moment); its screenshots, already taken at
the tick, are kept.

**The archive clock.** This PC's clock runs seconds ahead of the facility and
CPVA publishes about a second late, so "now" by the local clock is a moment the
archive has nothing for — asked straight, every camera looks half a minute too
old and the whole run gets skipped. So **cycle 1 takes each camera's newest
frame** (`newest=True`, no skip rule) and the smallest age it sees becomes
`_auto_offset_ns`; every later cycle asks about `target_ns - offset`. If a whole
cycle ever falls outside the window while still delivering *new* frames, the
offset must have moved and is re-measured from that cycle, out loud in the log.
The manual **Copy** uses `newest=True` too — it means "what the cameras have
now" — and reports the spread between the frames rather than their absolute age.

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
