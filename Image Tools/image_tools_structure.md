---
name: Image Tools structure map
description: Line-by-line class/function map of all files in Image Tools — read this before editing to avoid re-reading ~29 000 lines.
---

Image Tools — PySide6 multi-tab image viewer
Files (verified 2026-08-05): `main.py` (256 L) | `if_t.py` (8241 L) | `is_t.py` (15818 L) |
`sf_t.py` (3251 L) | `wk_t.py` (1283 L) | `cpva_client.py` (788 L)

Line numbers are approximate anchors — they drift as the files change. Re-grep the
symbol name if an offset looks wrong.

---

## main.py — entry point & window assembly

| Line | Name | What it does |
|------|------|-------------|
| L6 | frozen `sys.path` fixup | strips user site-packages, prepends `_internal` |
| L28 | `_VER_RE` | `v#.#.#` extracted from the exe name |
| L30 | `_detect_version()` / `APP_VERSION` / `APP_TITLE` | version string |
| L46 | `build_main_window(folder_arg)` | loads if/is/sf/wk via importlib (`if`/`is` are keywords), builds the 4-tab `QTabWidget`, wires inter-tab refs, Stop-All button in the status bar |
| L188 | `_open_folder_in_slider(viewer, tabs, folder)` | switch to the Slider tab + load a folder |
| L196 | `main()` | argparse, AppUserModelID, Fusion style + global QSS, `showMaximized` |

**Inter-tab wiring:** finder / shot_finder get `_slider_ref` and `_tab_widget`; every
tab gets `_workshop_ref` + `_workshop_tab_idx`. The Slider auto-starts live mode on
the first activation of tab index 1.

---

## cpva_client.py — shared archiver client (788 L)

| Line | Name | Purpose |
|------|------|---------|
| L76 | `CpvaError` | |
| L100/158 | `_request_json` / `_close_quiet` | one pooled HTTPS connection, SSL verification off |
| L168/180/196/208 | `fetch_samples` / `parse_samples` / `fetch_values` / `fetch_channels` | raw REST |
| L224 | `best_shot_ns` | strongest shot in a window (`SHOT_CHANNEL`) |
| L244-270 | `date_key_for_ns`, `_parse_date_key`, `prev_date_key`, `next_date_key`, `day_bounds_ns`, `today_key` | day keys |
| L276/286/302 | `DayResult`, `_Entry`, `_InFlight` | cache types; concurrent callers share one fetch |
| L319/323 | `_entry_result` / `_merge_tail` | today's entry is refreshed with a TTL and merged |
| **L342** | `get_day(channel, date_key)` | the day cache |
| L433/440/457/470 | `_finish_inflight`, `warm_days`, `peek_day`, `invalidate` | |
| L486/500/510 | `nearest_sample`, `_match_score`, `nearest_sample_ex` | nearest sample, preference direction |
| L559/565 | `LookupResult`, `format_lookup` | tri-state: value (incl. genuine 0) / `n/a` / `ERR` |
| L611/618/632 | `_last_at_or_before`, `invalidate_lookback`, `_value_before_day` | look-back cache keyed by "last sample before the START of a day" — a key fully determined by its query |
| L675 | `value_at_or_before` | step PVs (waveplate `RawPos`) are archived only on change |
| **L724** | `lookup_near(channel, ts_ns)` | the entry point every tab uses: energy = ±30 s window, step PVs = `value_at_or_before` |

---

## if_t.py — Image Finder tab (8241 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L47/51 | `_detect_is_lab` / `_IS_LAB` | hostname → lab |
| L54 | `IMAGES_ROOT_BASE` | `//users-L3.tier0.lcs.local` |
| L56/60 | `RAMPING_CANDIDATES` / `DEFAULT_RAMPING_SOURCE` | Lab / Office ramping CSV |
| L63-67 | `DEFAULT_WINDOW`, `DEFAULT_SAMPLE_STEP`, `DEFAULT_SAMPLE_NEAR`, `DEFAULT_TOL_KB`, `MAX_SCAN_FILES` = 2000 | selection + scan caps |
| L69 | `MIN_FULL_FILES` | **unused** |
| L70-72 | `ACT_MAX_GAP_S`, `MIN_SEG_ROWS`, `MIN_SEG_DURATION_S` | ramping segments |
| L74/75 | `IMAGE_EXTS`, `CAM_33HZ` | |
| L83/87 | `ENERGY_CSV_ROOT`, `ENERGY_CSV_NAME_FMT` | `dataof%Y%b_%d` |
| L91/97/(display) | `ENERGY_COLUMNS_AVAILABLE` / `_DEFAULT` (`[]`) / `_DISPLAY` | column picker |
| L123/127 | `ENERGY_MATCH_TOL_S` = 120 / `ENERGY_MATCH_TOL_API_S` = 30 | CSV vs API tolerance |
| L130-135 | `CPVA_BASE_URL`, `CPVA_HTTP_TIMEOUT` = 10, `CPVA_SHOT_CHANNEL`, `CPVA_SBW4_CHANNEL` | |
| L347-350 | `ENERGY_BAR_HEIGHT_PX`, `_FONT_SIZE_PT`, `_BG_COLOR`, `_TEXT_COLOR` | annotation bar |
| L402/415 | `GRADIENTS` / `GRADIENT_NAMES` | LUT palettes |
| L474/477 | `EMPTY_IMG_MAX_THRESHOLD`, `EMPTY_IMG_CONTRAST_MIN` | empty-frame detection |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L137/161 | `_import_cpva_client` / `_get_slider_module` | lazy, frozen-aware imports |
| L179/186 | `_cpva_fetch_samples` / `_cpva_best_shot_ns` | archiver |
| L219/232 | `_cam_totalpower_channel` / `_cpva_active_windows_ns` | merged beam-active windows |
| L373-390 | `_make_lut` / `_make_binary_lut` / `_make_stepped_lut` | LUTs |
| L426/431 | `_app_dir` / `load_readme_text` | |
| L443/480 | `_read_img_max_value` / `_image_is_nonempty` | `imgMaxValue`, contrast fallback |
| L641/666 | `_style_calendar` / `_make_mpl_toolbar` | house calendar QSS; toolbar built so the dark palette does not tint the icons away |
| L789 | `_make_multiselect_calendar` | multi-select calendar factory |
| L902/909 | `_hsep` / `_group_label` | |
| L918-955 | `is_valid_image_file`, `extract_display_label`, `extract_folder_number`, `extract_ns_from_stem`, `convert_timestamp`, `build_new_name` | filename helpers |
| L969/988 | `_energy_csv_path` / `_load_energy_csv` | daily CSV → `[_EnergyRow]` |
| L1038 | `_energy_api_for_day` | CPVA per column (thread pool) + CSV fallback |
| L1160/1191/1234 | `_find_energy_match` / `_find_closest_per_col_value` / `_build_per_col_from_rows` | matching |
| L1265/1272 | `_format_energy_diff_s` / `_format_energy_value` | ± offset, units (sbw4 ×0.749, J→mJ) |
| L1307/1447/1864 | `_annotate_image_with_energy` / `_write_annotated_with_text` / `_write_annotated_from_pil` | white-bar annotation (3 near-duplicate font/wrap routines) |
| L6093 | `_section_label` | small-caps label |
| L8222 | `main` | standalone entry |

### Classes
| Line | Class | Base | Purpose |
|------|-------|------|---------|
| L514/549/578 | `_WeekendDelegate` / `_CalBorderDelegate` / `_NoScrollCalendar` | QStyledItemDelegate / QCalendarWidget | red weekends, From/To border, wheel block |
| L713 | `_MultiSelectDelegate` | | multi-select painting |
| L896 | `_NoScrollComboBox` | QComboBox | ignore wheel |
| L979 | `_EnergyRow` | (slots) | one row (`ts_dt`, `values`) |
| L1504 | `_ThumbView` | QWidget | thumbnail + circle/square/cross overlay; `overlay_changing` / `overlay_edited`, `key`, `native_size` |
| L1971-2057 | `_EnergyLoadSignals` / `_EnergyLoadTask` / `EnergyColumnDialog` / `_LoadSignals` / `_CollectSignals` / `_CompareSignals` / `_AutoHourSignals` / `_LogSignals` / `_PreviewSignals` / `_TryAgainSignals` | signals + column dialog |
| **L2064** | `ImageFinderWidget` | QWidget | the tab |
| L5768 | `_MultiDaySetupDialog` | QDialog | range / camera / hour picker (weekday default Mon–Fri) |
| L6108/6190 | `_PVBrowseDialog` / `PVRegionSearchDialog` | QDialog | browse channels; find time regions where the chosen PVs satisfy conditions (`_PV_REGION_COLORS` at L6104) |
| **L6592** | `MultiDayPreviewWindow` | QWidget | result grid: palette / overlay / save / search-again |

### `ImageFinderWidget` key methods
| Line | Method | Purpose |
|------|--------|---------|
| L2073 | `__init__` | `_load_gen = 0`, energy caches, pools |
| L2144/2152/2160 | `_set_busy` / `_log` / `_log_safe` | `_log_safe` = thread-safe via signal |
| L2168 | `_schedule_autoload` | debounced `load_folders` |
| L2177 | `_build_ui` | full UI |
| L2546-2607 | `_get_original`, `_get_qty`, `_set_check_visual`, `_on_cell_*`, `_toggle_row/_all`, `_refresh_master_checkbox` | camera table |
| L2615-2739 | `_preview_load_from_row`, `_preview_set_files`, `_preview_prev/next`, `_on_preview_ready`, `_paint_pv_bar`, `_preview_show` | inline row preview |
| L2818-2922 | `_capture_selection_state`, `_refresh_selected_table`, `_fit_table_width`, `_apply_search`, `sort_subfolders_by_label/camnum`, `_reorder_table_rows` | selection + sorting |
| L2931-3012 | `_apply_day_selection`, `_effective_days`, `_on_calendar_clicked`, `_auto_select_today` | day selection (multi-day aware) |
| L3020-3041 | `_on_hour_change`, `_on_labtime_toggle`, `_on_gradient_changed`, `_pick_energy_columns` | |
| L3051 | `_get_energy_rows_for_dt` | cached API → CSV |
| L3107/3156/3203 | `_lookup_energy_for_files` / `_pv_values_for_ns` / `_format_pv_state` | per-file match, per-column lookup, tri-state format |
| L3211-3289 | `_energy_parts_for_path`, `_energy_entry_for_path`, `_on_pv_preview_toggle`, `_refresh_energy_info(_single)`, `_on_nav_mode_changed`, `_energy_nav_prev/next` | energy info panel + shot navigation |
| L3363-3483 | `_ensure_ramping_root`, `_parse_timestamp`, `_read_ramping_csv_rows`, `_get_ramping_for_day_cached` (1 s thread timeout), `_pick_best_block_real_hour` | ramping-based auto hour |
| L3574/3609 | `_apply_auto_hour_for_selected_day` / `_apply_auto_hour_ui` | hour 14 immediately + background refine, reload only if it changed |
| L3642/3656 | `_build_datetime` / `_build_target_path` | UI → UTC folder path |
| **L3680** | `load_folders` | scan all 24 hours in a pool, fill the table |
| L3766-3787 | `_on_load_not_found` / `_on_load_error` / `_on_load_done` | gen-checked table fill |
| L3854-3882 | `open_in_slider`, `_open_first_in_slider`, `open_folder_in_explorer` | |
| L3944-4057 | `_blocking_call`, `_nearest_file_for_ns`, `_frame_nearest_ns` | probe ns offsets via `exists()`, scandir fallback |
| L4117/4213 | `_find_image_for_regions` / `_find_image_for_day_cam` | PV-region hit → frame; TotalPower → frame with blind-scan fallback |
| L4522/4536 | `_energy_rows_for_day_cached` / `_open_pv_region_search` | |
| L4559 | `_run_multiday_search` | multi-day search |
| L4717 | `_get_csv_best_hour_for_day` | count-based best hour (**likely dead**) |
| L4801/4863 | `_get_items_cached` (scandir + sampled stat, `_namecache`) / `_any_image_from_folder` | |
| L4879 | `select_images_from_folder` | size / segment selection (several params **ignored**) |
| L4954-4984 | `_cleanup_view_temp`, `_apply_gradient_to_image`, `_make_view_copy_with_readable_name` | |
| L5009/5222 | `_collect_primary_files_now` / `_async` | parallel collect |
| L5068 | `_select_by_totalpower` | energy-anchored pick; recomputes the correct hour folder once `best_shot_ns` is known |
| L5276/5361 | `view_primary_files` / `save_primary_files_as` | View / Save |
| L5334 | `_run_energy_lookup_async` | background energy lookup (`_sig` captured locally) |
| L5494-5593 | `show_info`, `_save_to_memory`, `_send_to_workshop`, `_do_save_to_memory_slot`, `_clear_slot`, `_clear_memory`, `_align_images` | |
| L5611/5652 | `_compare_memory` / `_show_compare_window` | A/B diff |

### `MultiDayPreviewWindow` key methods
| Line | Method | Purpose |
|------|--------|---------|
| L6631/6816 | `_build_ui` / `_make_scroll_tab` | grid + Day/Camera tabs |
| L6862-6924 | `_load_raw`, `_apply_display_effects`, `_render_thumb`, `_render_popup` | render chain |
| L6937-6994 | `_on_palette_changed`, `_on_brightness_changed`, `_on_auto_bright_toggled`, `_on_preview_toggled`, `_rotate_all`, `_on_circle/square/cross_toggled` | display + overlay switches |
| L7017-7093 | `_pick_shape_color`, `_apply_colors_to_views`, `_clear_all_overlays`, **`_mirror_overlay_from`** (mirrors an overlay edit to every selected thumb, centre-preserving), `_tv_overlay_state` | |
| L7106-7190 | `_display_state`, `_push_undo`, `_undo_last`, `_apply_display_state`, `_reset_display` | undo of display state |
| L7244-7294 | `_active_draw_mode`, `_update_all_draw_modes`, `_native_size_for`, `_refresh_all_thumbs` | |
| L7294/7444 | `_make_thumb_cell` / `_search_by_time` | one cell; re-search by time |
| L7516-7580 | `_show_popup` / `_hide_popup` / `hideEvent` / `closeEvent` / `_pv_text_for_path` | |
| L7618/7646 | `_populate_day_tab` / `_populate_cam_tab` | |
| L7658-7894 | `_open_save_dialog`, `_bake_overlay_to_pil`, `_save_selected`, `_save_all`, `_items_for_keys`, `_paths_for_keys`, `_save_items`, `_save_paths` | saving |
| **L7900-8004** | `_try_again_selected` / `_try_again_single` / `_try_again_candidates` / `_try_again_run` | background re-search: energy-anchored candidates (`_select_by_totalpower` + size fallback), empty-frame validation, cancel, per-cell attempt log |
| L8115-8183 | `_pick_image_single`, `_clear_grid`, `_rebuild_grids`, `_update_thumb_result` | |

---

## is_t.py — Image Slider tab (15818 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L49-90 | `IMG_EXT`, `TZ_PRAGUE`, `SLIDER_MAX`, `SCRUB_*`, `PLAY_MAX_SIDE_*`, `FULL_RES_SIDE`, `CACHE_SIZE` = 320, `NATIVE_CACHE_KEEP` = 4, `PROXY_*`, `PREFETCH_*`, `TICK_STEP_MINUTES`, `PLAY_TICK_MS`, `AXIS_TOLERANCE_S`, `SAVE_RANGE_WARN_COUNT`, `ONLINE_MAX_ITEMS` = 3600 | operational tuning |
| L109/110 | `CPVA_BASE_URL`, `CPVA_HTTP_TIMEOUT` = 8 | archiver |
| L159 | `_PV_TODAY_CACHE_TTL` = 1.5 | today's PV cache |
| L467-469 | `GRADIENT_NAMES`, `GRADIENT_ID_DEFAULT` = 0, `GRADIENT_ID_GRAYSCALE` = 1 | palettes |
| L472-485 | `ONE_HOUR_NS`, `CIRCLE_*` | circle calibration |
| L487-494 | `DEFAULT_OPEN_DIR/ROOT`, `DEFAULT_SAVE_DIR`, `IMAGES_ROOT_BASE` | paths |
| L501/504 | `_REF_STATUS_STYLE`, `_REF_WARN_STYLE`, `_CHECKBOX_STYLE` | QSS |
| L630-664 | `ONLINE_ACTIVE_FOLDER_COUNT` = 2, `ONLINE_POLL_MIN/MAX_INTERVAL_S` (0.5/5), `ONLINE_POLL_BACKOFF`, `ONLINE_WATCHER_POLL_INTERVAL_S` = 3, `CAM_LOAD_WATCHDOG_S`, `CAM_PIPELINE_GRACE_S`, `WATCHER_SUSPECT_STRIKES` = 2, `WATCHER_RESTART_COOLDOWN_S` = 30, `CAM_DOT_FRESH_S` | live mode |
| L1023 | `_RenderBC` / `_RENDER_BC_NONE` | render params tuple (offset, contrast, auto) |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L137 | `_import_cpva_client` | lazy import |
| L162-237 | `_pv_date_key`, `_pv_prev_date_key`, `_pv_last_known_ex` (delegates to `cpva.lookup_near`), `_pv_decorate`, `_pv_last_known`, `_format_pv_value`, `pv_text_for_ts` | PV value at a frame's timestamp |
| L265/283/296 | `pv_warm_days` / `_pv_bar_font` / `render_pv_bar_below` | pre-warm; white PV bar under a PIL image |
| L379-421 | `_copy_metadata_into_png(_bg)` / `_save_png_metadata_txt` | metadata passthrough |
| L424-442 | `_make_lut` / `_make_binary_lut` / `_make_stepped_lut` | LUTs |
| L496 | `container_root_for_year` | archive root per year |
| L514/519 | `Item` (frozen dataclass) / `parse_unix_ns_from_name` | |
| L529-567 | `_dt_from_sec` (lru), `_dt_from_ns`, `fmt_hhmm_from_ns`, `fmt_hhmmss_ms_from_ns`, `fmt_prague_full_from_ns`, `prague_stamp_for_filename`, `replace_unix_ns_with_prague_in_filename` | ts parse/format |
| L567-587 | `_strip_cam_name`, `_cam_short_label`, `_cam_aspect_hint` | camera names |
| L590-617 | `floor_to_hour`, `axis_from_hour_folder_exact`, `axis_from_any_folder`, `folder_hour_from_prague_hour` | axis from a folder |
| L670-777 | `_cam_folder_time_key`, `active_scan_folders`, `poll_scan_folders`, `_is_dir_quiet`, `_dir_access_error`, `_camera_folder_problem`, `_probe_hour_folder` | folder probing + diagnosis |
| L814/858 | `_read_image_max_sample` / `_read_tiff_max_sample` | real max sample |
| **L887** | `_norm16_to8_full_scale` | 16-bit → 8-bit on `value/65535` (camera-absolute); replaced the `MaxValue`-tEXt × `/4095` scaling that rendered 6–9 bit diode cams near-black |
| L894 | `_gain_to_contrast_slider` | auto gain → Contrast slider value |
| L904-993 | `_stretch_arr_f`, `_autostretch_gray`, `_apply_brightness_offset`, `_apply_contrast`, `_apply_auto_brightness` | brightness = offset, contrast = gain; each reports what it applied |
| L1036/1047 | `_auto_bc_put` / `_auto_bc_get` | side channel that parks the greyed-out Auto sliders |
| L1062-1075 | `_diff_stats_put` / `_get`, `_apply_reference_diff` | subtraction + stats |
| **L1123** | `load_image_scaled` | core decode / scale / brightness-contrast / subtract / palette pipeline |
| L1206/1225 | `_apply_lut` / `PixCache` | |
| L1265-1335 | `load_proxy_gray`, `_proxy_plan` (coarse → fine sampling), `_ProxyTrack` | preview layer |
| L3328 | `_make_multiselect_calendar` | house-style calendar (the Qt grid-shift paint bug is fixed in `_date_for_index`) |
| L3453-3522 | `_seg_fields`, `hour_end_hm`, **`seg_bounds_ns`** (To = EXCLUSIVE end), `utc_hour_cells_for_window`, `hour_dirs_for_windows`, **`cameras_for_windows`** (union over every window + status) | window model |
| L4144-4266 | `_cam_type_key`, `_load/_save_pdxm1_grid_configs`, `_PDXM1_REVERSED_TYPES`, `get_pdxm1_grid_config`, `_draw_outlined_text` | diode grid overlay config |
| L4784 | `compute_justified_layout` | gallery packing that maximises total image area; each tile reserves `top_px` for its label bar so no letterbox shows |
| L5780 | `_fit_circle_kasa` | Kåsa circle fit |
| L7486/7490 | `_hsep` / `_group_label` | |

### QRunnable workers + Signals
| Signals (L) | Worker (L) | run() purpose |
|------|--------|---------------|
| `_ProxySignals` 1376 | `_ProxyTask` 1380 | one batch of preview frames |
| `_PvSignals` 1404 | (Viewer fetch) | PV result |
| `LoaderSignals` 1407 | `LoadTask` 1417 | decode one image |
| `ScanSignals` 1452 | `ScanTask` 1459 | scan folders → items (whole body wrapped in `except: pass`) |
| `RefreshScanSignals` 1516 | `RefreshScanTask` 1519 | incremental rescan |
| `SaveRangeSignals` 1550 | `SaveRangeTask` 1554 | batch save A→B with overlay + PV bar |
| `PointingAnalysisSignals` 1693 | `PointingAnalysisTask` 1698 | centroid per frame |
| `_SCSignals` 2508 | `_SCTask` 2512 | spatial contrast |
| `_CamPollSignals` 7774 | `_CamPollTask` 7890 | poll one camera's folders |
| `_DirWatchSignals` 7804 | `_DirWatcher` (Thread) 7808 | `ReadDirectoryChangesW` |
| `_CamLoaderSignals` 5396 | (CameraPickerDialog) | camera list |

### Widget / dialog classes
| Line | Class | Purpose |
|------|-------|---------|
| L1806/1975 | `_SCHistogramWidget` / `_SCHistogramDialog` | SC histogram + threshold |
| L2127/2297 | `_SCExclusionEditor` / `_SCExclusionCanvas` | SC exclusion regions |
| L2418/2432 | `_SCValueLabel` / `_SCPreviewLabel` | SC readout + preview |
| L2676 | `PointingPanel` | mpl scatter / hist / path + Qt interaction |
| L3185/3230 | `WeekendDelegate` / `_MultiSelectDelegate` | calendar painting |
| **L3579** | `DatePickerDialog` | one house-style calendar + minute-resolution From/To + two multi-day modes; opens in **Now** mode (today, `hh:00`–`hh+1:00`), `_apply_now_window` shared by button and checkbox, `_on_times_changed` prevents an empty window, multi-day presets `_MULTIDAY_FROM`–`_MULTIDAY_TO` (07:00–21:00) |
| L4102 | `_DayTimeDialog` | per-day window override (⚙ column, kept in `_day_overrides`, marked `*`) |
| L4170/4305/4545 | `Pdxm1GridConfig` / `_GridPreviewWidget` / `Pdxm1GridConfigDialog` | diode grid overlay; line positions are absolute image fractions, so each line is independent; PD cameras of one type share a config |
| L4773/4780/4846/5262 | `CamLayoutEntry` / `CamLayoutConfig` / `_LayoutCanvasWidget` / `LayoutConfigDialog` | multi-cam layout editor (free layout + justified rows) |
| **L5399** | `CameraPickerDialog` | camera selection + presets; list = union over every window of the pick |
| L5761 | `PopupBelowComboBox` | popup always opens below |
| **L5800** | `ImageView` | image display, overlays, zoom, calibration, `cam_ref_text` green `Ref:` badge |
| L6795/6936/6961/7003 | `CameraView` / `_FreeLayoutContainer` / `_JustifiedRowsContainer` / `MultiCameraGrid` | camera tile + the two layout backends + grid |
| L7219 | `TickBar` | time axis, cursor, A/B marks, date labels |
| L7495 | `CollapsibleSection` | sidebar sections (accent stripe + ▾/▸), state persisted via `_load/_save_ui_state` |
| L7586-7696 | `_DirItem` / `_LazyDirModel` (**dead stub**, `pass`) / `LazyDirModel` / `FolderPickerDialog` | lazy folder tree |
| L8004/8076 | `_CamSliderRow` / `_PvOverlayPanel` | per-cam slider row; floating PV panel |

### `Viewer` (L8181, QWidget) — method groups
| Group | Methods (anchor L) |
|-------|--------------------|
| init / UI | `__init__` 8182, `_load_ui_state` 8392, `_save_ui_state` 8400, `_diag_log` 8408, `_on_section_toggled` 8475, `_set_all_sections` 8479, `_build_ui` 8486, `resizeEvent` 9447, `_set_busy` 9453 |
| overlays | `_on_reset_zoom` 9462, `_toggle_draw_mode` 9485, `_remove_all_overlays` 9504, `_open_overlay_settings` 9526, `_apply_overlay_settings` 9610, calibrate circle/cross/square 12318/12329/12340, `_sync_overlay_checkboxes_from_iv` 12290, `_on_overlay_changed` 12303 |
| PV | `_open_pv_config` 9632, `_pv_rebuild_table` 9658, `_pv_trigger_fetch` 9684, `_pv_trigger_fetch_now` 9744, `_pv_force_refresh` 9808, `_pv_on_result` 9826, `_pv_update_overlay` 9842, `_open_pv_overlay_settings` 9884, `_pv_text` 9966 |
| multi-cam | `_is_multi_cam` 9984, `_on_multicam_selected` 10029, `_switch_to_multi/single_view` 10087/10094, `_build_per_cam_sliders` 10107, per-cam slider handlers 10176-10286, `_start_cam_load` 10372, `_reset_cam_pipeline` 10400, `_cam_load_watchdog` 10437, `_per_cam_sync_slaves` 10455, `_per_cam_step` 10487, `_live_advance_cam` 10506, `_setup_multi_cam` 10533 |
| live mode | `_on_auto_follow_toggled` 10637, `_ensure_dir_watcher` 10676, `_watcher_strike` 10699, `_stop/_prune_dir_watchers` 10724/10732, `_on_dir_watch_new_file` 10742, `_start/_stop_online_mode` 10829/10866, `_restore_full_history` 10916, `_merge_restored_history` 10989, `_online_poll` 11051, `_merge_single_new_items` 11087, `_online_poll_single_bg` 11146, `_online_poll_multi` 11283, `_rebuild_shared_items_from_cams` 11438, `_extend_shared_timeline_from_cams` 11472 |
| open / scan | `open_folder` 11531, `_start_multi_cam_scan` 11633, `_on_multi_scan_all_done` 11746, `open_by_date` 11841, `_reload_with_last_cameras` 11913, `auto_start_online` 11983, `open_folder_path` 12001, `receive_external_folder` 12010, `open_file_list` 12067, `refresh_folder` 12141, `_refresh_multi_cam` 12158, `_start_scan` 13220, `cancel_scan` 13249, `_choose_axis` 13305, `_in_ts_windows` / `_filter_to_ts_windows` 13289/13297, `_on_scan_finished` 13326 |
| brightness / subtract | `_bc` 12405, `_refresh_auto_bc_sliders` 12414, brightness/contrast handlers 12435-12468, `_load_raw_arr` 12476, `_ref_arr_for` 12495, `_cam_ref_arr_for` 12508, `_set_reference_frame` 12520, `_set_ref_status` 12632, `_refresh_ref_warning` 12637, `_on_subtract_changed` 12649, `_update_diff_stats` 12373, `_on_gradient_changed` 12700 |
| preview layer | `_proxy_enabled` 12769, `_proxy_start` 12792, `_proxy_cancel` 12826, `_proxy_kick` 12841, `_proxy_next_batch` 12855, `_proxy_pump` 12886, `_on_proxy_batch` 12905, `_proxy_update_status` 12924, `_proxy_render` 12938, `_proxy_try_paint(_cam)` 12965/12990, `_schedule_refine` 13017, `_refine_current_frame` 13023 |
| slider ↔ time | `_slider_to_time_ns` 13070, `_slider_to_index` 13074, `_index_to_slider_value` 13079, `_time_to_slider_value` 13084, `_time_to_nearest_index` 13090, `_set_info_for` 13096 |
| display / load | `_on_slider_pressed/changed/released` 13425/13439/13523, `_apply_scrub` 13452, `_load_or_cache` 13562, `_display_index` 13587, `_display_exact_index` 13595, `_display_multicam_at_time/_index` 13606/13629, `_on_cam_loaded` 13745, `_request_display_target` 13889, `_request_pixmap` 13922, prefetch 13936/13943, `_on_loaded` 13950 |
| playback | `play` 13993, `stop` 14016, `_autoplay_step` 14028, `_play_show` 14082, `step_frame` 14123, `keyPressEvent` 14140, `_adaptive_stride` 12733, `_current_decode_side` 12743 |
| focus / watcher | `_win32_set_title_bar` 14156, `_toggle_focus_mode` 14175 (F11), `_toggle_watcher_mode` 14262 (Ctrl+F11, edge-to-edge), `eventFilter` 14308 |
| timestamps | `_save_current_timestamp` 14426, `_goto_saved_timestamp` 14450, `_clear_timestamps` 14478 |
| pointing | `run_pointing_analysis` 14484, `_on_pointing_finished` 14564, live replay 14606-14681, `_save_pointing_plot` 14681, `_toggle_pointing_select` 14718, `_on_pointing_region_deleted` 14731, `_on_pointing_point_clicked` 15057 |
| spatial contrast | `_run_sc_auto_threshold` 14766, `_open_sc_histogram` 14792, `_open_sc_exclusion_editor` 14840, `_run_spatial_contrast` 14916, `_on_sc_finished` 14958, `_update_sc_topn_overlay` 15023 |
| marks / save | `set_mark_a/b` 15098/15104, `_apply_marks_to_tickbar` 15114, `_pv_text_for_ts` 15156, `_pv_prefetch_texts` 15167, `_pv_save_append_bar` 15206, `save_around_current` 15238, `save_current_with_overlay` 15299, `_render_cam_frame` 15392, `_save_multicam_current` 15479, `_send_to_workshop` 15518, `save_current` 15559, `_save_multicam_range` 15663, `save_range` 15746, save progress 15826-15870 |

---

## sf_t.py — Shot Finder tab (3251 L)

### Key constants
| Line | Name | Note |
|------|------|------|
| L95-104 | `_CHECKBOX_STYLE`, `_PV_NAME_FONT_PX`, `_CHECKBOX_STYLE_SM` | QSS |
| L107-130 | `IMAGES_ROOT_OPTIONS`, `_images_root_for_year`, `ENERGY_CSV_ROOT_OPTIONS`, `ENERGY_CSV_NAME_FMT` | Lab / Office roots |
| L134/138 | `EXTRA_COL_MATCH_TOL_S` = 30, `IMG_MATCH_TOL_NS` = 30 s | tolerances |
| L159/160 | `CPVA_BASE_URL`, `CPVA_HTTP_TIMEOUT` = 15 | archiver |
| L195/200 | `MJ_COLUMNS` = {Back_Ref, pap1}, `_CAM_CHANNEL_RE` | |
| L202/203 | `SBW4_TRANSMISSION` = 0.749, `SBW4_WARNING_THRESHOLD_J` = 0.5 | |

### Module-level helpers
| Line | Function | Purpose |
|------|----------|---------|
| L53-70 | `_make_lut_sf` / `_make_binary_lut_sf` / `_make_stepped_lut_sf` | LUTs |
| L141/168 | `_import_cpva_client` / `_get_slider_module` | lazy imports |
| L207 | `_read_img_max_value` | `imgMaxValue` |
| L236/244 | `_cpva_fetch_samples` / `_cpva_fetch_channels` | archiver |
| L250 | `_load_csv_for_day` | daily CSV → merged + per-col |
| **L308** | `_load_api_for_day` | per-column API + CSV fallback → `(merged, per_col, col_meta)`; does **not** fall back to CSV on an API *error* (source mixing caused the waveplate 500k/0 alternation) |
| L412/421 | `_find_closest_col_value` / `_lookup_col_value` | closest within tolerance; tri-state via `cpva.lookup_near` |
| L479-501 | `_format_value_state` / `_format_diff` / `_find_best_match` | |
| L520/529/544 | `_folder_hour_from_prague` / `_find_hour_folder` / `_find_image_for_ts` | Prague → UTC folder hour, probe offsets, closest frame |
| L595 | `_format_value` | per-column format |

### Classes & key methods
| Line | Class / method | Purpose |
|------|---------------|---------|
| L613-628 | `_SearchSignals` / `_CamLoadSignals` / `_PreviewSignals` / `_ChannelSignals` | signals |
| L633/650/677 | `_WeekendDelegate` / `_NoScrollCalendar` / `_NoScrollComboBox` | calendar / combo |
| L703 | `_DayResult` | result container; persists `search_cols`, `extra_cols`, `criteria_csv`, `cam`, `col_meta`, `img_path`, computes `ts_ns` |
| L744/781 | `_PreviewWidget` / `_TimeWindowDialog` | centred painter; start/end date+hour |
| **L921** | `ShotFinderWidget` | the tab |
| L1063 | `_build_ui` | full UI |
| L1342 | `_setup_calendar` | **duplicate of the calendar factory** |
| L1402-1541 | `_rebuild_pv_suggestions`, `_fetch_channel_list`, `_on_channels_loaded`, `_make_pv_dropdown`, `_split_query`, `_tokens_in_order`, `_rank_pv_match`, `_populate_pv_dropdown` | searchable channel dropdown (typed words must appear in order) |
| L1600-1669 | `_on_pv_search_changed`, `_on_pv_dropdown_clicked`, `_best_pv_match`, `_on_pv_search_return`, `_register_col`, `_add_pv_col`, `_remove_pv_col`, `_make_remove_btn` | add / remove a PV column |
| L1680/1695 | `_sync_pv_cfg_from_rows` / `_rebuild_pv_rows` | one row per picked PV: ticked = filter (target/tol), unticked = show only |
| L1787-1816 | `_filter_cols`, `_show_cols`, `_get_criteria`, `_update_date_info`, `_build_energy_text` | |
| L1833/1889 | `_on_selection_changed` / `_load_and_show_preview` | row → image + PV values + preview thread |
| L1953-1973 | `_open_time_window`, `_selected_days`, `_load_cameras` | camera scan over all 24 hours (no cancel token) |
| L2033-2109 | `_on_cam_search_changed`, `_on_cam_dropdown_clicked`, `_on_cam_selected_clicked`, `_on_cam_remove` | camera picker |
| **L2109** | `_start_search` | per-day worker: API/CSV + match; resolves the matched image path |
| L2360 | `_on_day_result` | populate a row (still does network IO on the main thread) |
| L2504/2529 | `_on_table_cell_clicked` (Folder cell → `explorer /select,<image>`) / `_on_table_double_clicked` (all in-tolerance shots) | |
| L2795-2948 | `_on_search_done`, `_open_in_slider`, `_save_results` | copy to temp → slider; annotated PNGs (main-thread encode) |
| L3183 | `_send_to_workshop` | array → Workshop |

---

## wk_t.py — Workshop tab (1283 L)

### Constants & helpers
| Line | Name | Purpose |
|------|------|---------|
| L35-52 | `_wk_make_lut` / `_wk_make_binary_lut` / `_wk_make_stepped_lut` | LUT builders |
| (below) | `WK_GRADIENTS` | name → LUT (Grayscale = None …) |
| L81/92/101 | `_np_to_qimage` / `_qimage_to_np` / `_arr_to_pil` | conversions |
| L107 | `_TZ_PRAGUE` | ⚠️ **hardcoded +2 h** (wrong in winter, CET = +1) |
| L111 | `_build_save_stem` | `{cam}_{YYYY-MM-DD_HH-MM-SS-mmm}` |
| L162 | `_SLOT_UNDO_LIMIT` = 30 | |
| L202 | `_bresenham` | integer line points |
| L646-657 | `_TOOL_BTN_STYLE`, `_BTN_STYLE`, `_DANGER_STYLE` | QSS |
| L1273/1279 | `_group_label` / `_separator` | |

### Classes
| Line | Class | Members |
|------|-------|---------|
| L165 | `_WorkshopSlot` (dataclass) | `source_arr` / `current_arr` / `undo_stack` / `redo_stack` / `source_path`; `push_undo`, `undo`, `redo`, `reset_to_source` |
| L224 | `WorkshopCanvas(QWidget)` | `TOOL_*`; `set_slot`, `_rebuild_qimage`, `fit_to_view`, `paintEvent`, mouse handlers, `_paint_brush/_eraser/_line/_rect`, `_commit_text`, `_do_crop` |
| L664 | `WorkshopWidget(QWidget)` | `_build_ui`, `receive_image` (⚠️ forces uint8 → 16-bit input truncated), `_activate_slot`, `_undo`, `_redo` (⚠️ **not wired to any button/shortcut**), `_on_bc_changed`, `_apply_bright_contrast`, `_auto_bright_contrast`, `_on_palette_changed`, `_do_diff`, `_save` |

---

## Shared conventions
- Timezone `ZoneInfo("Europe/Prague")` in is_t / if_t / sf_t; wk_t hardcodes +2 h (bug).
- All archiver access goes through `cpva_client` (SSL verification disabled).
- Filename timestamps: UTC nanoseconds (19 digits).
- Network roots: UNC `//users-L3.tier0.lcs.local` (Lab) or `Z:\` (Office); the archive
  tree itself is UTC.
- Checkboxes: `_CHECKBOX_STYLE` — QSS on `::indicator` only.
- Background threads log via `_log_safe` / a signal, never touch widgets directly.
- matplotlib toolbars are created with `_make_mpl_toolbar` so the dark palette does not
  tint the icons into invisibility.
- Brightness = additive offset, contrast = multiplicative gain — never swapped, and an
  Auto checkbox always parks its (greyed-out) slider on the computed value.

## CHANGES 2026-07-15 (13-item fix batch)
- **cpva_client.py**: new shared helpers — `LookupResult` (tri-state), `value_at_or_before`,
  `lookup_near`, `nearest_sample_ex`, `format_lookup`.
- **Tri-state PV display everywhere**: real value (incl. genuine 0) / `n/a` / `ERR`.
- **sf_t**: `_load_api_for_day` returns `col_meta` and no longer falls back to CSV on an
  API error; `_DayResult` persists its search-time state; combined value column;
  tolerances unified to `EXTRA_COL_MATCH_TOL_S`; Folder cell click = `explorer /select`.
- **if_t**: multi-day weekday default Mon–Fri; per-column PV lookup for preview/save;
  annotation bar shows PV values only; `_image_is_nonempty`; Try-again rewritten as a
  background worker; `_ThumbView` overlay signals + `_mirror_overlay_from`.
- **is_t**: PV overlay uses a trailing debounce (400 ms, 0.7 s max wait) + single-flight;
  pointing "Delete mode"; live mode gained DirWatchers in single-cam, a 3 s watcher poll,
  silent-death detection (`_watcher_strike`) and a stale-state reset in `_start_scan`.

## CHANGES 2026-08-04 (PV values + time window)
- **cpva_client**: step-PV lookup fixed. `STEP_CHANNELS` (waveplate `RawPos`) are archived
  only on change, so `lookup_near` returns `value_at_or_before`, which bisects the day of
  the timestamp (+ 2 previous days) EXACTLY at ts. The old look-back cache was keyed by
  (channel, day-of-ts) while storing the value at one arbitrary ts, so the first frame of
  a day pinned its value for every other frame. New: `peek_day`, `invalidate_lookback`.
- **is_t**: `_pv_last_known_ex` delegates to `cpva.lookup_near`; the private duplicates are gone.
- **is_t `DatePickerDialog`** rebuilt: minute resolution, one house-style calendar
  (`_make_multiselect_calendar`, Qt grid-shift paint bug fixed in `_date_for_index`), two
  mutually exclusive multi-day modes.
- **is_t folder enumeration** goes through `utc_hour_cells_for_window` /
  `hour_dirs_for_windows`: the archive tree is UTC, so Prague 00:30 resolves to the
  PREVIOUS day's 22/23 folder.
- **is_t frame filter**: `Viewer._ts_windows` + `_filter_to_ts_windows` enforce
  minute-precise, per-day windows; live mode keeps the last window's end open.

## CHANGES 2026-08-05 (time window + camera union + ref badge)
- **`seg_bounds_ns`**: To is the **EXCLUSIVE** end — 12:00–13:00 is exactly one hour and
  enumerates only the 12 h folder. `hour_end_hm()` builds that end; a To of 23:59 still
  means "to midnight" (a `QTimeEdit` cannot show 24:00).
- **`DatePickerDialog`** opens in **Now** mode (today, `hh:00`–`hh+1:00`); From/To can no
  longer collide; both multi-day modes preset every day to 07:00–21:00 with per-day ⚙
  overrides in `_day_overrides`.
- **`cameras_for_windows(windows)`** returns the union over every day/segment plus a
  status; the three copies of the camera-scan worker all call it, so one empty day or
  hour can no longer produce an empty camera list.
- **Single-cam reference badge**: `ImageView.cam_ref_text` / `set_cam_ref_text()` draw the
  green `Ref: <timestamp>` strip — the 1-camera counterpart of `CameraView.set_ref_status()`.

## CHANGES since 2026-08-05 (current working tree)
- **is_t multi-cam layout**: `compute_justified_layout` + `LayoutConfigDialog` /
  `_LayoutCanvasWidget` / `CamLayoutConfig`. Two containers back the grid —
  `_JustifiedRowsContainer` (gallery rows sized to maximise image area, each tile
  reserving its label bar so no grey letterbox shows) and `_FreeLayoutContainer`
  (user-placed tiles as fractions of the canvas).
- **is_t diode grid overlay**: `Pdxm1GridConfig` + `Pdxm1GridConfigDialog` +
  `_GridPreviewWidget`. Line positions are absolute image fractions (so each line moves
  independently), configs are keyed by camera *type* (`_cam_type_key`: PD1M1, PD2M2 …)
  and persisted; `_PDXM1_REVERSED_TYPES` flips column order where needed.
- **is_t render params** are one `_RenderBC(offset, contrast, auto)` tuple through the
  whole pipeline; `_apply_contrast` / `_apply_auto_brightness` joined
  `_apply_brightness_offset`, and `_refresh_auto_bc_sliders` parks the greyed-out Auto
  sliders on the applied values.
- **is_t reference diff** reports statistics (`_apply_reference_diff` + `_diff_stats_*`,
  `_update_diff_stats` / `_update_cam_diff_stats`) and warns when the reference no longer
  matches the loaded set (`_refresh_ref_warning`).
- **is_t sidebar** is built from `CollapsibleSection`s whose expanded state is persisted
  (`_load_ui_state` / `_save_ui_state`); `_diag_log` writes `image_tools_diag.log`.
- **is_t**: `receive_external_folder` accepts a folder plus an energy map from the other
  tabs; `_probe_hour_folder` / `_camera_folder_problem` / `_dir_access_error` give real
  reasons instead of an empty camera list.
- **if_t**: `PVRegionSearchDialog` + `_PVBrowseDialog` + `_find_image_for_regions` — pick
  PVs and conditions, get the time regions of a day that satisfy them, then the frame
  nearest each region. `_make_mpl_toolbar` keeps the toolbar icons visible under the dark
  palette.
- **sf_t**: PVs are added from a searchable archiver channel dropdown
  (`_fetch_channel_list`, `_rank_pv_match`, `_tokens_in_order`); `IMG_MATCH_TOL_NS` bounds
  how far a frame may sit from the matched shot; `EXTRA_COL_MATCH_TOL_S` is 30 s.

## KNOWN ISSUES (verified in source 2026-08-05)
- **wk_t**: `receive_image` forces uint8 without scaling → 16-bit input is truncated;
  `_redo` (L1067) is unreachable (no button, no shortcut); `_TZ_PRAGUE` hardcoded +2 h.
- Main-thread network IO / PNG encoding in if_t & sf_t save / try-again / open-in-slider
  paths → UI freeze on a slow share.
- No directory-listing cache: `if_t.load_folders` and `sf_t._load_cameras` rescan all 24
  hour folders on every change of hour / source / date.
- No cancel/generation token on the sf_t search and camera-load workers → a stale run can
  overwrite the table.
- Dead code: if_t `MIN_FULL_FILES`, `_range_gen`, `_get_csv_best_hour_for_day`, ignored
  `select_images_from_folder` params, 3 near-duplicate annotation routines; is_t
  `_LazyDirModel` stub; sf_t `_setup_calendar`.
- Frequent `except Exception: pass` hides real errors (notably `ScanTask.run`).
