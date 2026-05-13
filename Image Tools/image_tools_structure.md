---
name: Image Tools structure map
description: Line-by-line class/function map of all 4 files in Image Tools — read this before editing to avoid re-reading ~14 000 lines.
---

Image Tools — PySide6 multi-tab image viewer
Files: `main.py` (243 L) | `if_t.py` (5406 L) | `sf_t.py` (2008 L) | `is_t.py` (6194 L)

---

## main.py

| Line | Name | What it does |
|------|------|-------------|
| L28 | `_VER_RE` | regex to extract version from exe name |
| L30 | `_detect_version()` | extracts semantic version string |
| L41 | `APP_VERSION`, `APP_TITLE` | version + title constants |
| L46 | `build_main_window(folder_arg)` | creates main window, 3 tabs, wires inter-tab integration |
| L198 | `_open_folder_in_slider(viewer, tabs, folder)` | switches to slider tab and loads folder |
| L206 | `main()` | entry point: parse args, build window, event loop |

**Inter-tab wiring:** ImageFinder's "Open in Slider" calls `_open_folder_in_slider` which calls `Viewer.open_folders()`.

---

## if_t.py — Image Finder tab (5406 L)

### Key constants

| Line | Name | Note |
|------|------|------|
| L38 | `IMAGES_ROOT` | network path to image archive |
| L40–43 | `RAMPING_CANDIDATES` | list of `[name, path]` ramping CSV sources |
| L47–56 | scan/sample config | scanning tolerances, sample steps |
| L58–60 | `IMAGE_EXTS`, `CAM_33HZ` | file extensions, high-freq camera IDs |
| L62–63 | `FINAL_RE`, `SOURCE_RE` | filename parsing regexes |
| L70–108 | energy CSV config | paths, column names, display names, tolerances |
| L110–116 | CPVA archiver config | `CPVA_BASE_URL`, `CPVA_HTTP_TIMEOUT`, `CPVA_SHOT_CHANNEL`, `CPVA_SBW4_CHANNEL` |
| L111–129 | `INFO_TEXT` | help text shown in info dialog |
| L161–181 | `GRADIENTS`, `GRADIENT_NAMES` | color LUT definitions |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L118 | `_cpva_ssl_ctx()` | creates SSL context (cert verification off) |
| L123 | `_cpva_fetch_samples(channel, start_ns, end_ns)` | fetch archiver JSON samples for one channel |
| L137 | `_cpva_best_shot_ns(start_ns, end_ns)` | returns ns timestamp of highest-energy shot |
| L173 | `_cam_totalpower_channel(cam_name)` | derives TotalPower channel from camera folder name |
| L186 | `_cpva_active_windows_ns(channel, start_ns, end_ns, ...)` | returns active time windows (10× baseline threshold); supports `active_from_ns` filter |
| L275 | `_hsep()` | horizontal separator widget |
| L282 | `_group_label(text)` | styled group header label |
| L291 | `is_valid_image_file(name)` | checks extension |
| L298 | `extract_display_label(folder_name)` | camera label from folder name |
| L308 | `extract_folder_number(folder_name)` | camera number from folder name |
| L319 | `extract_ns_from_stem(stem)` | Unix ns timestamp from filename |
| L325 | `convert_timestamp(ns, use_prague_time)` | ns → formatted string |
| L330 | `build_new_name(stem, use_prague_time)` | new filename with Prague timestamp |
| L342 | `_energy_csv_path(dt)` | builds path to daily energy CSV |
| L361 | `_load_energy_csv(csv_path)` | loads + parses daily CSV |
| L411 | `_find_energy_match(rows, img_ts_ns, tol_s)` | best CSV row for image timestamp |
| L442 | `_format_energy_diff_s(diff_s)` | formats time difference |
| L449 | `_format_energy_value(col, raw_val)` | formats CSV value with units |
| L484 | `_annotate_image_with_energy(src, dst, ...)` | adds annotation bar to image |
| L860 | `_write_annotated_from_pil(img, dst, text)` | adds white annotation bar below a PIL image and saves it; used by `MultiDayPreviewWindow._save_items` |

### Small helper classes

| Line | Class | Purpose |
|------|-------|---------|
| L202 | `_WeekendDelegate` | colors weekend dates red in calendar |
| L237 | `_NoScrollCalendar` | QCalendarWidget that ignores mousewheel |
| L269 | `_NoScrollComboBox` | QComboBox that ignores mousewheel |
| L352 | `_EnergyRow` | data container: timestamp + column values dict |
| L637 | `_EnergyLoadSignals` | Qt signals for CSV loading |
| L640 | `_EnergyLoadTask` | QRunnable: loads energy CSV in background |
| L654 | `EnergyColumnDialog` | dialog to pick which energy columns to display |
| L698 | `_LoadSignals` | signals for folder loading |
| L703 | `_CollectSignals` | signals for file collection |
| L706 | `_CompareSignals` | signals for memory comparison |
| L710 | `_AutoHourSignals` | signals for auto-hour detection |

### Class: _ThumbView  (L860 — ~340 lines)

Custom QWidget used as thumbnail in `MultiDayPreviewWindow`. Copied interaction model from `ImageView` in is_t.py.

| Feature | Detail |
|---------|--------|
| Overlays | Circle, square, cross — all independent, any combination active simultaneously |
| Coordinates | Normalised 0–1 relative to `_img_rect()` (letterboxed displayed image) |
| Drawing | QPainter in `paintEvent` — NOT baked into pixmap |
| Drag-to-create | Hold+drag sets shape size; Shift = proportional |
| Drag handles | Circle: move + N/S/E/W; Square: move + 8 corners/edges |
| Colors | Circle: yellow (255,255,0,230); Square: cyan (0,200,255,230); Cross: green (0,255,0,220) |
| Signals | `clicked`, `dbl_clicked`, `hovered_in`, `hovered_out` |
| Selection border | `set_selected(on)` → blue border |
| Key fields | `show_circle`, `circle_center_norm` (QPointF), `circle_rx_norm`, `circle_ry_norm` |
|            | `show_square`, `square_rect_norm` (tuple x0,y0,x1,y1) |
|            | `show_cross`, `cross_pos_norm` (QPointF) |
|            | `circle_color`, `square_color`, `cross_color` (QColor) |

### Class: ImageFinderWidget  (L717–2533+)

Main widget for the Image Finder tab.

| Line | Method | Note |
|------|--------|------|
| L726 | `__init__` | init + `_build_ui()` |
| L777 | `_set_busy` | enable/disable controls during ops |
| L784 | `_log` | append to log box |
| L792 | `_schedule_autoload` | delayed folder load |
| L801 | `_build_ui` | full layout: calendars, tables, buttons, controls |
| L1086 | `_get_original` | original folder name for row |
| L1090 | `_get_qty` | quantity value for row |
| L1095 | `_set_check_visual` | update checkbox appearance |
| L1103 | `_on_cell_clicked` | toggle selection |
| L1108 | `_on_cell_double_clicked` | edit quantity |
| L1114 | `_on_cell_changed` | handle quantity edit |
| L1118 | `_on_header_clicked` | toggle all rows |
| L1123 | `_toggle_row` | toggle single checkbox |
| L1129 | `_toggle_all` | toggle all checkboxes |
| L1143 | `_refresh_master_checkbox` | update master state |
| L1150 | `_capture_selection_state` | save selection for restore |
| L1158 | `_refresh_selected_table` | rebuild selected cameras table |
| L1176 | `_on_sel_table_double_clicked` | deselect camera |
| L1185 | `_apply_search` | filter camera list |
| L1193 | `sort_subfolders_by_label` | sort by camera label |
| L1202 | `sort_subfolders_by_camnum` | sort by camera number |
| L1219 | `_reorder_table_rows` | reorder + restore selection |
| L1241 | `_on_calendar_selected` | date selected → load cameras |
| L1248 | `_on_hour_change` | hour slider changed |
| L1253 | `_on_labtime_toggle` | Prague vs lab time toggle |
| L1261 | `_on_ramping_source_change` | switch CSV source |
| L1268 | `_on_gradient_changed` | update color gradient |
| L1274 | `_pick_energy_columns` | open column-selector dialog |
| L1284 | `_get_energy_rows_for_dt` | load CSV rows for datetime |
| L1301 | `_lookup_energy_for_files` | match CSV rows to image files |
| L1338 | `_refresh_energy_info` | update energy display |
| L1346 | `_on_nav_mode_changed` | navigation mode toggle |
| L1353 | `_energy_nav_prev` | navigate to previous energy row |
| L1368 | `_energy_nav_next` | navigate to next energy row |
| L1383 | `_refresh_energy_info_single` | update energy for single row |
| L1420 | `_build_ui` | full layout (inner rebuild) |
| L1491 | `_ensure_ramping_root` | validate ramping CSV source |
| L1499 | `_parse_timestamp` | parse timestamp string from CSV |
| L1511 | `_read_ramping_csv_rows` | read all rows from ramping CSV |
| L1543 | `_get_ramping_for_day_cached` | load + cache ramping data |
| L1609 | `_pick_best_block_real_hour` | find best hour by activity |
| L1688 | `_apply_auto_hour_for_selected_day` | auto-set hour from ramping data |
| L1729 | `_apply_auto_hour_ui` | update UI with auto-determined hour |
| L1762 | `_build_datetime` | construct target datetime from UI |
| L1776 | `_build_target_path` | build image folder path for datetime |
| L1779 | `_log_selected_datetime_preview` | debug log |
| L1797 | `load_folders` | scan camera folders for selected datetime |
| L1872 | `_on_load_not_found` | handle missing folder |
| L1877 | `_on_load_error` | handle loading error |
| L1881 | `_on_load_done` | update tables after scan |
| L1932 | `open_in_slider` | load selected cameras into Slider tab |
| L1948 | `_open_first_in_slider` | load first camera into Slider tab |
| L1960 | `open_folder_in_explorer` | open folder in Windows Explorer |
| L1976 | `_snapshot_collect_jobs` | create list of folders to collect |
| L1987 | `_get_items_cached` | cached file list for folder |
| L2026 | `select_images_from_folder` | subset selection by criteria |
| L2097 | `_cleanup_view_temp` | cleanup temp view folder |
| L2109 | `_apply_gradient_to_image` | apply color gradient to PIL image |
| L2121 | `_make_view_copy_with_readable_name` | copy image with readable timestamp name |
| L2146 | `_collect_primary_files_now` | synchronous file collection |
| L2177 | `_collect_primary_files_async` | async file collection |
| L2211 | `view_primary_files` | collect + display images |
| L2297 | `_run_energy_lookup_async` | async energy lookup for files |
| L2323 | `save_primary_files_as` | save images to user folder |
| L2417 | `show_info` | display info/help dialog |
| L2426 | `_save_to_memory` | save image set to memory slot |
| L2440 | `_do_save_to_memory_slot` | save folder to named slot |
| L2460 | `_clear_slot` | clear memory slot |
| L2468 | `_clear_memory` | clear all slots |
| L2477 | `_align_images` | align two numpy arrays for comparison |
| L2495 | `_compare_memory` | compare two memory slots |
| L2533 | `_show_compare_window` | show comparison in new window |

### Class: MultiDayPreviewWindow  (L4141+)

Multi-day multi-camera thumbnail grid with overlays, undo, annotated save.

| Line | Method | Note |
|------|--------|------|
| L4182 | `_build_ui` | two toolbar rows + thumbnail grid |
| L4597 | `_display_state` | snapshot all display state for undo (per-ThumbView overlay coords + global colors) |
| L4617 | `_undo_last` | pop last undo snapshot and restore |
| L4680 | `_reset_display` | clear all overlays + display effects (with confirmation) |
| L5096 | `_open_save_dialog` | 2×2 save grid: Selected/All × Original/Annotated |
| L5161 | `_bake_overlay_to_pil` | convert normalised `_ThumbView` overlay coords to PIL draw calls for saving |
| L5222 | `_save_items` | save images: applies `_apply_display_effects` + `_bake_overlay_to_pil`, adds `_annotated` suffix |

**State fields (set in `_build_ui`):**
- `_draw_circle`, `_draw_square`, `_draw_cross` — independent booleans
- `_rotation` — current rotation (0/90/180/270)
- `_auto_bright` — auto brightness flag
- `_circle_qcolor`, `_square_qcolor`, `_cross_qcolor` — current overlay colors
- `_thumb_views: dict[tuple, _ThumbView]` — `(cam_name, date)` → widget
- `_thumb_camnames: dict[tuple, str]` — key → camera name
- `_undo_stack: list[dict]` — max 50 snapshots

---

## sf_t.py — Shot Finder tab (2008 L)

Data source: **CPVA archiver HTTP API** (no CSV files).
Channels queried per PV column: see `CPVA_CHANNEL_MAP`.

### Key constants

| Line | Name | Note |
|------|------|------|
| L88 | `IMAGES_ROOT` | network path to image archive |
| L91 | `CPVA_BASE_URL` | archiver API base URL |
| L92 | `CPVA_HTTP_TIMEOUT` | HTTP timeout (15 s) |
| L95–101 | `CPVA_CHANNEL_MAP` | maps PV column name → CPVA channel (ptm1, pcm2, pcm4, pap1, sbw4) |
| L103–118 | `PV_COLUMNS`, `MJ_COLUMNS` | display names, mJ columns |
| L119–120 | SBW4 config | `SBW4_TRANSMISSION`, `SBW4_WARNING_THRESHOLD_J` |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L124 | `_cpva_ssl_ctx()` | SSL context (no cert verification) |
| L126 | `_cpva_fetch_samples(channel, start_ns, end_ns)` | fetch archiver JSON for one channel |
| L133 | `_load_api_for_day(day, cols)` | query all requested PV columns for a full day; returns unified row list with `_dt` (Prague-naive), `_ns` (exact UTC ns), and column values as strings |
| L194 | `_find_best_match(rows, col, target)` | row closest to target value |
| L211 | `_folder_hour_from_prague(prague_hour, ref_date)` | Prague hour → UTC folder hour |
| L220 | `_find_hour_folder(day, hour_utc)` | fuzzy-match ±2h to find hour folder |
| L230 | `_find_image_for_ts(cam_folder, ts_dt, ts_ns_override)` | find image ≤5 s from timestamp; `ts_ns_override` uses exact UTC ns directly |
| L284 | `_format_value(col, raw)` | format PV value with units |
| L368 | `_hsep()` | horizontal separator |
| L376 | `_group_label(text)` | group header label |

### Small helper classes

| Line | Class | Purpose |
|------|-------|---------|
| L298 | `_SearchSignals` | Qt signals: result, done, log_msg, progress |
| L305 | `_CamLoadSignals` | signals for camera loading |
| L309 | `_PreviewSignals` | signals for preview |
| L314 | `_WeekendDelegate` | colors weekends red |
| L331 | `_NoScrollCalendar` | calendar ignoring mousewheel |
| L358 | `_NoScrollComboBox` | combobox ignoring mousewheel |
| L384 | `_DayResult` | result container: day, best_row, col, actual, diff, target_csv, hour_folder, rows_in_tol, ts_ns |
| L403 | `_PreviewWidget` | centered image preview widget |

### Class: ShotFinderWidget  (L459+)

| Line | Method | Note |
|------|--------|------|
| L461 | `__init__` | init + `_build_ui()` |
| L463 | `_cleanup_temp` | cleanup temp files |
| L470 | `resizeEvent` | handle resize |
| L474 | `_show_preview` | display image preview |
| L585 | `_set_busy` | enable/disable controls |
| L590 | `_log` | log message |
| L599 | `_build_ui` | full UI: calendars, tables, buttons |
| L862 | `_setup_calendar` | configure calendar styling |
| L900 | `_on_date_changed` | date selection |
| L904 | `_on_pv_changed_rb` | update units for PV |
| L923 | `_update_date_info` | update date range label |
| L933 | `_on_selection_changed` | result table row selection → preview |
| L1009 | `_load_and_show_preview` | background preview load |
| L1013 | `_rescale_preview` | rescale to fit widget |
| L1018 | `_on_gradient_changed` | gradient selection |
| L1025 | `_qdate_to_date` | QDate → date |
| L1028 | `_selected_days` | list of selected days |
| L1042 | `_load_cameras` | background camera list load |
| L1075 | `_on_cameras_loaded` | populate camera list |
| L1084 | `_on_cam_search_changed` | filter camera dropdown |
| L1114 | `_on_cam_dropdown_clicked` | select from dropdown |
| L1134 | `_on_cam_selected_clicked` | select from selected list |
| L1140 | `_on_cam_remove` | remove camera |
| L1160 | `_start_search` | start background search (queries API per day) |
| L1303 | `_on_day_result` | update table with result |
| L1410 | `_on_table_double_clicked` | show detail dialog (uses cached rows_in_tol) |
| L1616 | `_open_in_slider` | open results in Slider tab |
| L1616 | `_on_search_done` | finish search + update UI |
| L1743 | `_save_results` | save images to user folder |

---

## is_t.py — Image Slider tab (6194 L)

### Key constants

| Line | Name | Note |
|------|------|------|
| L45 | `IMG_EXT` | supported image extensions |
| L46 | `TZ_PRAGUE` | Prague timezone |
| L47–60 | UI/cache config | timing, scaling, cache size, `AXIS_TOLERANCE_S` |
| L124–139 | `GRADIENTS`, `GRADIENT_NAMES`, `ONE_HOUR_NS` | color LUTs, 1h reference |
| L142–155 | calibration params | circle calibration defaults, default open dirs |

### Module-level helpers

| Line | Name | What it does |
|------|------|-------------|
| L63 | `_copy_metadata_into_png` | embed metadata into PNG |
| L91 | `_save_png_metadata_txt` | alias for metadata copy |
| L94 | `_make_lut(stops)` | RGB gradient lookup table |
| L107 | `_make_binary_lut()` | binary threshold gradient |
| L112 | `_make_stepped_lut(stops)` | stepped color gradient |
| L172 | `parse_unix_ns_from_name(p)` | ns timestamp from filename |
| L184 | `_dt_from_ns(ts_ns)` | ns → datetime in Prague TZ |
| L187 | `fmt_hhmm_from_ns` | ns → `HH:MM` |
| L190 | `fmt_hhmmss_ms_from_ns` | ns → `HH:MM:SS.MS` |
| L196 | `fmt_prague_full_from_ns` | ns → full date+time string |
| L202 | `prague_stamp_for_filename` | ns → filename-safe timestamp |
| L208 | `replace_unix_ns_with_prague_in_filename` | rename with Prague timestamp |
| L215 | `ns_from_dt(dt)` | datetime → ns |
| L218 | `floor_to_hour(dt)` | floor to hour start |
| L221 | `axis_from_hour_folder_exact(folder)` | time axis from hour folder path |
| L236 | `axis_from_any_folder(folder)` | time axis from any parent folder |
| L245 | `folder_hour_from_prague_hour` | Prague hour → UTC folder hour |
| L255 | `_read_tiff_max_sample` | read MaxSampleValue from TIFF |
| L277 | `_autostretch_gray` | auto-stretch grayscale contrast |
| L296 | `_apply_brightness_offset` | constant brightness offset |
| L312 | `load_image_scaled` | load + process image (scale, brighten, gradient) |
| L379 | `_apply_lut` | apply RGB gradient to image |
| L1288 | `_fit_circle_kasa` | fit circle to point cloud (Kasa method) |

### Dataclass + cache

| Line | Name | Purpose |
|------|------|---------|
| L167 | `Item` | immutable: `path: Path`, `ts_ns: int` |
| L398 | `PixCache` | LRU cache for QPixmap objects |

### Background task classes

| Line | Class | Purpose |
|------|-------|---------|
| L417 | `LoaderSignals` / `LoadTask` | load single image |
| L437 | `ScanSignals` / `ScanTask` | scan folder for images |
| L487 | `RefreshScanSignals` / `RefreshScanTask` | re-scan folder |
| L521 | `SaveRangeSignals` / `SaveRangeTask` | save image range |
| L559 | `PointingAnalysisSignals` / `PointingAnalysisTask` | pointing analysis per image |

### UI widget classes

| Line | Class | Purpose |
|------|-------|---------|
| L656 | `PointingPanel` | matplotlib panel: pointing scatter plot, toggle path, save figure |
| L799 | `WeekendDelegate` | colors weekends red in calendar |
| L830 | `DatePickerDialog` | select date range + hour range; `selected_folders` property |
| L1072 | `CameraPickerDialog` | select cameras from list; multi-select + filter |
| L1269 | `PopupBelowComboBox` | combobox that opens popup below, ignores mousewheel |
| L1308 | `ImageView` | display image + pointing calibration overlays (circle/cross/square handles, normalised coords) |
| L2041 | `CameraView` | single camera: image + timestamp label + selection highlight |
| L2125 | `MultiCameraGrid` | N cameras in grid; `selected_cam_index`, `selected_img_view` |
| L2294 | `TickBar` | timeline tick marks widget |
| L2437 | `_DirItem` / `LazyDirModel` | lazy-loading file tree model for folder picker |
| L2547 | `FolderPickerDialog` | browse + select folder dialog |
| L2625 | `_CamPollSignals` / `_CamPollTask` | background camera polling |

### Class: Viewer  (L2724+)

Main image slider widget. Handles:
- Folder loading + scanning
- Timeline scrubbing (slider + keyboard navigation)
- Multi-camera grid display
- Brightness / gradient controls
- Image comparison modes
- Pointing calibration (circle/cross/square)
- Save range of images
- Online ("now") refresh mode

Key public methods (call from main.py):
- `open_folders(folders)` — load image folders into slider
- `save_image(path)` — save current view

---

## Architecture overview

```
main.py
  └── build_main_window()
        ├── tab 0: ImageFinderWidget   (if_t.py)
        ├── tab 1: Viewer              (is_t.py)
        └── tab 2: ShotFinderWidget    (sf_t.py)

ImageFinder → _open_folder_in_slider() → Viewer.open_folders()
ShotFinder  → _open_in_slider()        → Viewer.open_folders()
```

All modules: PySide6 + threading for background work.
Image timestamps: Unix nanoseconds in filename, displayed in Prague TZ.
Folder layout: `IMAGES_ROOT / YYYY / MM / DD / HH_utc / CAM_NAME / *.png`

**Overlay system (ThumbView + ImageView):**
Overlays (circle/square/cross) are drawn by QPainter in `paintEvent` using normalised 0–1 coordinates relative to the letterboxed image rect (`_img_rect()`). They are never baked into the pixmap until save, at which point `_bake_overlay_to_pil()` translates normalised coords to full-resolution pixel coordinates.

**CPVA Archiver API:**
Both if_t.py (for TotalPower active-window detection) and sf_t.py (for PV value search) query the archiver at `CPVA_BASE_URL` via `_cpva_fetch_samples(channel, start_ns, end_ns)`. The channel names follow the pattern `HAPLS-ENER_IN_<SENSOR>_LT7_DIAG2:Energy`.
