---
name: Image Tools structure map
description: Line-by-line class/function map of all files in Image Tools — read this before editing to avoid re-reading ~23 000 lines.
---

Image Tools — PySide6 multi-tab image viewer
Files (verified 2026-06-19): `main.py` (255 L) | `if_t.py` (6624 L) | `is_t.py` (11238 L) | `sf_t.py` (2682 L) | `wk_t.py` (1283 L)
Orphan: `sp_t.py` (1072 L) — NOT imported by main.py; stray copy of the Spectra program, not part of this app.

Line numbers below are approximate anchors — they drift as the files change. Re-grep the symbol name if an offset looks wrong.

---

## main.py — entry point & window assembly

| Line | Name | What it does |
|------|------|-------------|
| L6  | frozen `sys.path` fixup | strips user site-packages, prepends `_internal`, writes debug_syspath/debug_pil txt |
| L28 | `_VER_RE` | regex `v#.#.#` extracted from exe name |
| L30 | `_detect_version()` / `APP_VERSION` / `APP_TITLE` | version string from exe/file name |
| L46 | `build_main_window(folder_arg)` | loads if/is/sf/wk via importlib (`if`/`is` are keywords), builds `QTabWidget` (4 tabs), wires inter-tab refs, Stop-All button in status bar |
| L55 | `_load_module(name, filename)` | importlib loader, frozen-aware base dir |
| L187 | `_open_folder_in_slider(viewer, tabs, folder)` | switch to Slider tab + load folder |
| L195 | `main()` | argparse, AppUserModelID, Fusion style + global QSS, `showMaximized` |

**Inter-tab wiring (L123-138):** finder/shot_finder get `_slider_ref`, `_tab_widget`; all tabs get `_workshop_ref` + `_workshop_tab_idx`. Slider auto-starts online mode on first activation of tab index 1 (L177).

---

## if_t.py — Image Finder tab (6624 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L50 | `_IS_LAB` | hostname OPR1/2/3, VIS01/02 → lab |
| L53 | `IMAGES_ROOT_BASE` | `//users-L3.tier0.lcs.local` |
| L55 | `RAMPING_CANDIDATES` | Lab `//hapls-share…/2026_alldata`, Office `Z:\…` |
| L66 | `MAX_SCAN_FILES` = 2000 | cap on stat() per folder (silent truncation, see review) |
| L68 | `MIN_FULL_FILES` = 1 | **unused** |
| L73 | `IMAGE_EXTS` | png/jpg/jpeg/tif/tiff/bmp |
| L74 | `CAM_33HZ` | set of 33 Hz camera numbers |
| L82 | `ENERGY_CSV_ROOT` / L86 `ENERGY_CSV_NAME_FMT` | `dataof%Y%b_%d` |
| L90/96/102 | `ENERGY_COLUMNS_AVAILABLE` / `_DEFAULT` (`[]`) / `_DISPLAY` | column picker data |
| L118 | `ENERGY_MATCH_TOL_S` = 2.0 | image↔PV match tolerance |
| L121 | `CPVA_BASE_URL` / L122 `CPVA_HTTP_TIMEOUT`=10 | archiver REST |
| L125 | `CPVA_SHOT_CHANNEL` / L126 `CPVA_SBW4_CHANNEL` | best-shot channels |
| L129 | `CPVA_CHANNEL_MAP` | col → channel |
| L149-151 | `_cpva_conn_lock` / `_cpva_conn` / `_CPVA_HOST` | **single** serialized persistent HTTPS conn |
| L404/417 | `GRADIENTS` / `GRADIENT_NAMES` | LUT palettes |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L140 | `_cpva_ssl_ctx` | SSL ctx, verification off |
| L154 | `_cpva_fetch_samples` | archiver GET, reuse conn, 1 retry |
| L188 | `_cpva_best_shot_ns` | highest-energy sample ns in window |
| L221 | `_cam_totalpower_channel` | folder → `:TotalPower` channel |
| L234 | `_cpva_active_windows_ns` | merged active windows from TotalPower |
| L445 | `_read_img_max_value` | imgMaxValue from PNG tEXt (12th-chunk heuristic — fragile) |
| L593-642 | `is_valid_image_file` / `extract_display_label` / `extract_folder_number` / `extract_ns_from_stem` / `convert_timestamp` | filename helpers |
| L644 | `_energy_csv_path` / L663 `_load_energy_csv` | daily CSV → `[_EnergyRow]` |
| L713 | `_energy_api_for_day` | CPVA per-col (ThreadPool) + CSV fallback |
| L843 | `_find_energy_match` | bisect nearest CSV row in tol |
| L874 | `_find_closest_per_col_value` | per-col nearest within tol |
| L918 | `_format_energy_value` | unit format (sbw4 ×0.749, J→mJ) |
| L953 | `_annotate_image_with_energy` / L1104 `_write_annotated_with_text` / L1508 `_write_annotated_from_pil` | white-bar annotation (3 near-dup font/wrap routines) |
| L5155 | `_section_label` | small-caps label |
| **L5160** | `def _NoScrollCalendar()` | ⚠️ **shadows** the class at L539 — see KNOWN ISSUES |
| L6605 | `main` | standalone entry |

### Classes
| Line | Class | Base | Purpose |
|------|-------|------|---------|
| L475 | `_WeekendDelegate` | QStyledItemDelegate | red weekends |
| L510 | `_CalBorderDelegate` | _WeekendDelegate | + From/To border |
| **L539** | `_NoScrollCalendar` (class) | QCalendarWidget | wheel-block — **DEAD, shadowed by func L5160** |
| L571 | `_NoScrollComboBox` | QComboBox | ignore wheel |
| L654 | `_EnergyRow` | (slots) | one CSV row (ts_dt, values) |
| L1161 | `_ThumbView` | QWidget | thumbnail + circle/square/cross overlay |
| L1615-1697 | `_EnergyLoadSignals`/`_EnergyLoadTask`/`EnergyColumnDialog`/`_LoadSignals`/`_CollectSignals`/`_CompareSignals`/`_AutoHourSignals`/`_LogSignals`/`_PreviewSignals` | signals + column dialog |
| **L1702** | `ImageFinderWidget` | QWidget | main widget |
| L4830 | `_MultiDaySetupDialog` | QDialog | range/camera/hour picker |
| L5168 | `MultiDayPreviewWindow` | QWidget | results grid, palette/overlay/save/try-again |

### `ImageFinderWidget` key methods
| Line | Method | Purpose |
|------|--------|---------|
| L1711 | `__init__` | `_load_gen=0`, energy caches, pools |
| L1782/1790/1798 | `_set_busy` / `_log` / `_log_safe` | `_log_safe` = thread-safe via signal |
| L1806 | `_schedule_autoload` | debounced load_folders |
| L1815 | `_build_ui` | full UI |
| L2239-2317 | `_preview_*` | inline row preview (gen-checked at L2307) |
| L2490/2497 | `_auto_select_today` / `_on_calendar_selected` | day selection |
| L2525 | `_pick_energy_columns` | column dialog |
| L2535 | `_get_energy_rows_for_dt` | cached API→CSV |
| L2563 | `_lookup_energy_for_files` | per-file match (7-tuple) |
| L2815 | `_get_ramping_for_day_cached` | ramping CSV, 1 s thread timeout |
| L2883 | `_pick_best_block_real_hour` | segment-based auto-hour |
| L2974 | `_apply_auto_hour_for_selected_day` | default hour + bg refine |
| L3043 | `_build_datetime` / L3057 `_build_target_path` | UI → UTC folder path (`max(year,2025)`) |
| L3082 | `load_folders` | scan all 24 hours (12 threads), fill table |
| L3177 | `_on_load_done` | gen-checked table fill |
| L3358 | `_nearest_file_for_ns` | probe ns offsets via exists(), scandir fallback |
| L3447 | `_find_image_for_day_cam` | TotalPower→file, blind-scan fallback |
| L3703 | `_on_multiday_search` | multi-day search |
| L3841 | `_get_csv_best_hour_for_day` | count-based best hour (**likely dead**) |
| L3924 | `_get_items_cached` | scandir + sampled stat, `_namecache` |
| L4002 | `select_images_from_folder` | size/segment selection (params window/tol_kb/sample_* **ignored**) |
| L4130/4343 | `_collect_primary_files_now` / `_async` | parallel collect (24 threads) |
| L4189 | `_select_by_totalpower` | energy-anchored pick |
| L4383/4447 | `view_primary_files` / `save_primary_files_as` | View / Save |
| L4673/4655/4714 | `_compare_memory` / `_align_images` / `_show_compare_window` | A/B diff |

---

## is_t.py — Image Slider tab (11238 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L46-63 | `IMG_EXT`, `SLIDER_MAX`=1e6, `SCRUB_*`/`PLAY_*` sides, `CACHE_SIZE`=320, `PREFETCH_*`, `ONLINE_MAX_ITEMS`=50000 | operational tuning |
| L72-89 | `CPVA_BASE_URL`/`CPVA_HTTP_TIMEOUT`=8, `PV_CHANNEL_MAP`, `PV_UNITS` | PV archiver |
| L101-214 | `_pv_day_cache`(+lock,TTL) / `_pv_before_cache`(+lock) | day & look-back PV caches |
| L477-492 | `GRADIENTS`/`GRADIENT_NAMES`, `GRADIENT_ID_DEFAULT`=0/`_GRAYSCALE`=1 | palettes |
| L498-512 | `CIRCLE_*` calib, `DEFAULT_OPEN_DIR/ROOT`, `DEFAULT_SAVE_DIR` | calib + paths |
| L624 | `ONLINE_ACTIVE_FOLDER_COUNT`=2 | live-poll hour folders |
| L1124-1136 | `_k32`, `_FILE_*`, `_DIRWATCH_AVAILABLE` | Win32 ReadDirectoryChangesW |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L92-254 | `_pv_ssl_ctx`/`_pv_date_key`/`_pv_load_day`/`_pv_query_range`/`_pv_value_at_or_before`/`_pv_last_known`/`_format_pv_value` | PV fetch + cache |
| L259 | `pv_text_for_ts` / L281 `pv_warm_days` | PV burn-in string + parallel pre-warm |
| L319 | `render_pv_bar_below` | white PV text bar under PIL image |
| L402/435 | `_copy_metadata_into_png` (+`_bg`) | embed source metadata |
| L447-465 | `_make_lut`/`_make_binary_lut`/`_make_stepped_lut` | LUTs |
| L529-563 | `parse_unix_ns_from_name`, `_dt_from_sec`(lru), `_dt_from_ns`, `fmt_*`, `prague_stamp_for_filename` | ts parse/format |
| L741/760 | `_autostretch_gray` / `_apply_brightness_offset` | contrast/brightness |
| L776 | `load_image_scaled` | **core** decode/scale/gradient/subtract pipeline |
| L889 | `_apply_lut` | RGB LUT onto Grayscale8 |
| L3389 | `_fit_circle_kasa` | Kåsa circle fit |

### QRunnable workers + Signals
| Signals (L) | Worker (L) | run() purpose |
|------|--------|---------------|
| `_PvSignals` 927 | (Viewer fetch) | `result(gen, dict)` |
| `LoaderSignals` 930 | `LoadTask` 933 | decode one image (run 942) |
| `ScanSignals` 950 | `ScanTask` 957 | scan folders → items (run 966; whole body wrapped in `except: pass` L1005) |
| `RefreshScanSignals` 1014 | `RefreshScanTask` 1017 | incremental rescan (run 1025) |
| `SaveRangeSignals` 1048 | `SaveRangeTask` 1052 | batch save A→B w/ overlay+PV (run 1131) |
| `PointingAnalysisSignals` 1191 | `PointingAnalysisTask` 1196 | centroid per frame (run 1266) |
| `_SCSignals` 2006 | `_SCTask` 2010 | spatial contrast (run 2040, otsu 2145) |
| `_CamPollSignals` 5109 | `_CamPollTask` 5208 | poll one cam's folders (run 5222) |
| `_DirWatchSignals` 5139 | `_DirWatcher`(Thread) 5143 | ReadDirectoryChangesW (run 5166) |

### Widget / dialog classes
| Line | Class | Purpose |
|------|-------|---------|
| L1304/1473/1625/1795 | `_SCHistogramWidget`/`Dialog`, `_SCExclusionEditor`/`Canvas` | SC threshold + exclusion regions |
| L2174 | `PointingPanel` | mpl scatter/hist/path + Qt interaction |
| L2654/2685 | `WeekendDelegate`, `DatePickerDialog` | calendar + date/hour/multiday |
| L3035 | `CameraPickerDialog` | camera selection + presets |
| L3409 | `ImageView` | image display + overlays + zoom + calibration (paintEvent 3778) |
| L4289/4398 | `CameraView` / `MultiCameraGrid` | 2–4 cam grid |
| L4645 | `TickBar` | time axis / cursor / marks (paintEvent 4680) |
| L4921/4936 | `_DirItem` / `LazyDirModel`, L5031 `FolderPickerDialog` | lazy folder tree |
| L4929 | `_LazyDirModel` | **dead stub (`pass`)** |
| L5300 | `_CamSliderRow` | per-cam master radio + slider |
| L5372 | `_PvOverlayPanel` | floating draggable PV panel |

### `Viewer` (L5477, QWidget) — main tab, method groups
| Group | Methods (anchor L) |
|-------|--------------------|
| init/UI | `__init__` 5478, `_build_ui` 5616, `resizeEvent` 6433 |
| overlays | `_on_reset_zoom` 6448, `_toggle_draw_mode` 6471, `_remove_all_overlays` 6490, `_apply_overlay_settings` 6596, calibrate circle/cross/square 8524/8535/8546 |
| PV | `_open_pv_config` 6618, `_pv_trigger_fetch` 6663, `_pv_on_result` 6754, `_pv_update_overlay` 6761 |
| multi-cam | `_is_multi_cam` 6894, `_switch_to_multi/single_view` 6947/6954, `_build_per_cam_sliders` 6967, `_setup_multi_cam` 7228 |
| online | `_on_auto_follow_toggled` 7283, dir-watchers 7307/7320/7328/7338, `_start/_stop_online_mode` 7410/7437, `_online_poll`/`_single_bg`/`_multi` 7472/7487/7642, timeline 7756/7790 |
| open/scan | `open_folder` 7841, `_start_multi_cam_scan` 7931, `open_by_date` 8113, `auto_start_online` 8253, `open_folder_path` 8266, `open_file_list` 8274, `refresh_folder` 8347, `_start_scan` 8848, `_choose_axis` 8904 |
| brightness/subtract | `_on_brightness_slider_changed` 8558, `_load_raw_arr` 8578, `_set_reference_frame` 8591, `_on_subtract_changed` 8654, `_on_gradient_changed` 8678 |
| slider↔time | `_slider_to_time_ns` 8729, `_time_to_nearest_index` 8749, `_set_info_for` 8755 |
| display/load | `_load_or_cache` 9104, `_display_exact_index` 9137, `_display_multicam_at_time/_index` 9148/9171, `_on_cam_loaded` 9262, `_request_pixmap` 9345, prefetch 9358/9365, `_on_loaded` 9372 |
| playback | `play` 9401, `stop` 9424, `_autoplay_step` 9433, `step_frame` 9521, `keyPressEvent` 9537 |
| focus/watcher | `_toggle_focus_mode` 9572, `_toggle_watcher_mode` 9659, `eventFilter` 9705 |
| timestamps | `_save_current_timestamp` 9823, `_goto_saved_timestamp` 9847 |
| pointing | `run_pointing_analysis` 9881, `_on_pointing_finished` 9961, `_save_pointing_plot` 10067 |
| spatial contrast | `_run_sc_auto_threshold` 10144, `_open_sc_histogram` 10170, `_open_sc_exclusion_editor` 10218, `_run_spatial_contrast` 10294, `_update_sc_topn_overlay` 10394 |
| marks/save | `set_mark_a/b` 10469/10475, `save_around_current` 10557, `save_current_with_overlay` 10617, `_render_cam_frame` 10734, `_send_to_workshop` 10853, `save_current` 10900, `save_range` 11080 |

---

## sf_t.py — Shot Finder tab (2682 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L81-92 | `SF_GRADIENTS` | LUT dict |
| L105-117 | `IMAGES_ROOT_OPTIONS`, `ENERGY_CSV_ROOT_OPTIONS`, `ENERGY_CSV_NAME_FMT` | Lab/Office roots |
| L119 | `EXTRA_COL_MATCH_TOL_S` = 5.0 | closest-value tol for extra cols |
| L122-134 | `CPVA_BASE_URL`/`CPVA_HTTP_TIMEOUT`=15, `CPVA_CHANNEL_MAP` | archiver |
| L136 | `PV_COLUMNS` | col → `"… [J]"` label |
| L146 | `MJ_COLUMNS` = {Back_Ref, pap1} | shown ×1000 mJ |
| L148-149 | `SBW4_TRANSMISSION`=0.749, `SBW4_WARNING_THRESHOLD_J`=0.5 | |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L153 | `_read_img_max_value` | imgMaxValue (12th-chunk heuristic) |
| L182/189 | `_cpva_ssl_ctx` / `_cpva_fetch_samples` | archiver GET → (list, url) |
| L203 | `_load_csv_for_day` | daily CSV → merged + per-col |
| L261 | `_load_api_for_day` | CPVA per-col (ThreadPool) + CSV fallback, merge by `_ns` |
| L366 | `_find_closest_col_value` | bisect closest within tol |
| L391 | `_find_best_match` | min \|val−target\|; ⚠️ **re-divides sbw4 by 0.749** (double-scale, see KNOWN ISSUES) |
| L408 | `_folder_hour_from_prague` | Prague→UTC folder hour (hardcoded −1 when no zoneinfo → DST bug) |
| L417 | `_find_hour_folder` | probe `root/Y/M/D/h` offsets [0,−1,1,−2,2] |
| L432 | `_find_image_for_ts` | scan folder, closest ns (docstring says 5 s, code uses 10 s) |
| L483 | `_format_value` | per-col format |

### Classes & key methods
| Line | Class / method | Purpose |
|------|---------------|---------|
| L497-509 | `_SearchSignals`/`_CamLoadSignals`/`_PreviewSignals` | signals |
| L513/530/557 | `_WeekendDelegate`/`_NoScrollCalendar`/`_NoScrollComboBox` | calendar/combo |
| L583 | `_DayResult` | result container, computes `ts_ns` |
| L609 | `_PreviewWidget` | centered painter |
| L646 | `_TimeWindowDialog` | start/end date+hour |
| **L786** | `ShotFinderWidget` | main tab |
| L921 | `_build_ui` | full UI |
| L1203 | `_setup_calendar` | **dup of `_make_cal`, likely unused** |
| L1268 | `_rebuild_criteria_rows` | per-PV target/tol rows |
| L1370 | `_on_selection_changed` | row → find image + energy + preview thread |
| L1436 | `_load_and_show_preview` | bg load/normalize/LUT → QImage |
| L1517 | `_load_cameras` | bg, ThreadPool scans 24 hours (no cancel token) |
| L1649 | `_start_search` | build criteria, worker per-day API/CSV + match (no cancel token) |
| L1843 | `_on_day_result` | populate row (does network IO on main thread) |
| L1958 | `_on_table_double_clicked` | all in-tol shots dialog |
| L2222 | `_open_in_slider` | copy 1 img/day to temp → slider |
| L2360 | `_save_results` | save annotated PNGs (main-thread encode) |
| L2614 | `_send_to_workshop` | array → Workshop |

---

## wk_t.py — Workshop tab (1283 L)

### Constants & helpers
| Line | Name | Purpose |
|------|------|---------|
| L35-62 | `_wk_make_lut`/`_make_binary_lut`/`_make_stepped_lut` | LUT builders |
| L64 | `WK_GRADIENTS` | name → LUT (Grayscale=None …) |
| L81/92/101 | `_np_to_qimage` / `_qimage_to_np` / `_arr_to_pil` | conversions |
| L107 | `_TZ_PRAGUE` | ⚠️ **hardcoded +2h** (wrong in winter, CET=+1) |
| L111 | `_build_save_stem` | `{cam}_{YYYY-MM-DD_HH-MM-SS-mmm}` |
| L162 | `_SLOT_UNDO_LIMIT` = 30 | |
| L202 | `_bresenham` | integer line points |

### Classes
| Line | Class | Members |
|------|-------|---------|
| L164 | `_WorkshopSlot` (dataclass) | `source_arr`/`current_arr`/`undo_stack`/`redo_stack`/`source_path`; `push_undo` 173, `undo` 179, `redo` 186, `reset_to_source` 193 |
| L224 | `WorkshopCanvas(QWidget)` | TOOL_* 228-235; `set_slot` 269, `_rebuild_qimage` 274, `fit_to_view` 315, `paintEvent` 348, mouse 404/440/464, `_paint_brush` 525, `_paint_eraser` 538, `_paint_line` 563, `_paint_rect` 576, `_commit_text` 598, `_do_crop` 631 |
| L664 | `WorkshopWidget(QWidget)` | `_build_ui` 678, `receive_image` 928 (⚠️ forces uint8), `_activate_slot` 969, `_undo` 1059, `_redo` 1067 (⚠️ **not wired to UI**), `_on_bc_changed` 1086, `_apply_bright_contrast` 1121, `_auto_bright_contrast` 1139, `_on_palette_changed` 1158, `_do_diff` 1204, `_save` 1230 |

---

## Shared conventions
- Timezone: `ZoneInfo("Europe/Prague")` (is_t/if_t/sf_t); wk_t hardcodes +2h (bug).
- CPVA archiver `https://10.78.0.57:8443/api/1.0/cpva`, SSL verification disabled.
- Filename timestamps: UTC nanoseconds (19-digit), parsed by `extract_ns_from_stem`/`parse_unix_ns_from_name`.
- Network roots: UNC `//users-L3.tier0.lcs.local` (Lab) or `Z:\` (Office).
- Checkboxes: `_CHECKBOX_STYLE` — QSS on `::indicator` only.
- Background threads must log via `_log_safe`/signal, never touch widgets directly.

## KNOWN ISSUES (review 2026-06-19, verified in source)
- ✅ FIXED — **if_t**: removed redundant `def _NoScrollCalendar()` that shadowed the wheel-block class L539.
- ✅ FIXED — **if_t L4710**: `_compare_memory` except branch now emits `_compare_sig.error`.
- ✅ FIXED — **sf_t L391 `_find_best_match`**: removed the second `/0.749` (caller already passes target_csv).
- **wk_t L936**: 16-bit input truncated to uint8 (no scaling); `_redo` (L1067) unreachable — no UI/shortcut.
- Main-thread network IO / PNG encoding in if_t & sf_t save / try-again / open-in-slider paths → UI freeze on slow shares.
- Duplicated logic: overlay-draw (is_t, 4 copies), camera-scan worker (3 copies), annotation font/wrap (if_t, 3 copies).
