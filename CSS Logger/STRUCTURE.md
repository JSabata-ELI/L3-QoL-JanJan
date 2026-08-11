# CSS Logger — STRUCTURE

> Verified against source: 2026-08-11 · `main.py` 5087 L · `cpva_core.py` 614 L

## Files

| File | Description |
|------|-------------|
| `main.py` | The application — PySide6 "CPVA Suite" (CSS Logger + Spectra in one window). Run with `python main.py`. |
| `cpva_core.py` | Non-UI helpers: config/preset I/O, CPVA archiver HTTP, time / PV-name / image helpers. No GUI toolkit — shared by `main.py` and the tests. |
| `sp_t.py` | The Spectra widget, embedded as the second tab of the suite. |
| `test_smoke.py` | Offline smoke test — headless (`QT_QPA_PLATFORM=offscreen`), network + dialogs mocked, clicks through every button/dialog. |
| `test_live_pacing.py` | Offline test of live-mode pacing: window clamping, bounded tick look-back, cursor advance on an empty tick, Stop-Live cancellation, table item reuse. |
| `test_count_param.py` | Focused test of the archiver `count` parameter handling (hits the real archiver). |
| `cpva_explorer_config.json` | Saved settings (time range, shown channels, conditions, master PV, `graph_opts`, …). |
| `cpva_presets.json` | Named PV-list presets. |
| `cpva_conditions_presets.json` | Condition (min/max filter) presets. |
| `custom_pvs.json` | Derived PVs — `{"name", "expr", "bindings"}` per entry, where `bindings` maps every channel letter in `expr` to the full PV name it stands for. |
| `RampingRepository/` | Local parquet data (ramping events) — works offline. |
| `build_config.json` | Build settings for Dev Tools. |

> The old tkinter `cssl.py` is gone; its non-UI helpers live in `cpva_core.py`,
> the UI was fully replaced by `main.py` (PySide6).

---

## main.py

### Purpose
Explorer and logger for CPVA (Control System Studio) archive data. Shows PV
values over time, walks the archive, exports CSV, draws graphs (Graph / XY /
PV Time) and opens the camera images belonging to a row.

`CPVASuiteWindow(QMainWindow)` hosts two tabs — **CSS Logger**
(`CSSLoggerWidget`) and **Spectra** (`sp_t.SpectraWidget`) — restores its
geometry and asks both children to stop their background work on close.

### Tabs
- **Graph** — one banded plot: every PV gets its own vertical band of a shared
  axes (CS-Studio style), its own Y axis column on the left, crosshair cursor
  with per-PV value boxes, span statistics, rubber-band zoom with history,
  reference lines, conditions, custom PVs, and the axis settings panel.
- **XY Plot** — scatter of one loaded PV against another, rectangle zoom + history.
- **PV Time Plot** — daily distribution / raw trace from the RampingRepository.
- **Table** — `QTableWidget` of the merged rows, context menu (Copy row / Open
  image), CSV export.
- **Log** — run log.

### Sidebar
Time window dialog, presets (load/save/save-as/delete), PV list
(Browse/Remove/Clear), **LOAD DATA**, **Live** toggle with countdown, Master PV /
master-multiple filter, PV count, progress bar.

### Dialogs
`TimeWindowDialog` (absolute + relative quick picks per side),
`DatePickerDialog` + `_make_calendar` + `_WeekendDelegate` (house calendar style,
Monday-first, red weekends), `PVBrowserDialog` (loads the channel list once, then
filters locally), `_ConditionsDialog`, `_RefLinesDialog`, `_CustomPVDialog`
(expressions over channel letters A, B, C…; its top table maps every letter to a
full PV name and marks the ones not currently loaded), `_GraphSettingsDialog`.

### Appearance constants
`_APP_STYLESHEET`, `_BTN_PRIMARY` / `_BTN_SUCCESS` / `_BTN_DANGER`, `_CHK_STYLE`
(`::indicator` only), `_CAL_STYLE`, `_GRAPH_COLORS` (15 series colours).

### `_GRAPH_OPTS_DEFAULTS` — the Graph settings dialog
Everything the user can tune from **Graph settings**; stored under
`config["graph_opts"]`, and every key here is also its default, so an older
config simply picks up the defaults for keys it lacks.

| Group | Keys |
|-------|------|
| Fonts (pt) | `font_size`, `tick_font_delta` (−1), `cursor_font_delta` |
| Y axis columns (px) | `axis_gap_px`, `label_pad_px`, `outer_margin_px`, `show_axis_titles`, `y_ticks_max`, `y_minor_ticks` |
| Plot rect (figure fractions) | `margin_right`, `margin_top`, `margin_bottom`, `band_pad_frac` |
| Cursor / performance | `cursor_value_boxes`, `cursor_boxes_max`, `line_markers` |

`y_minor_ticks` is **off** by default: matplotlib builds a full Tick object
(2 lines + 2 texts) per minor tick, which with a dozen stacked axes is ~400
objects and the single largest cost of a redraw.

### Custom-PV expressions — letters are positional, bindings are not
A custom PV is a Python expression over channel letters (`Sum of Green = F+H`),
and those letters are just positions in `_pv_order`, so they change whenever the
PV list does (preset load, remove, reorder). Each entry therefore also stores
`bindings` — the PV name behind every letter — and that is what evaluation uses:

| Piece | Role |
|-------|------|
| `_CPV_VAR_RE` | matches channel-letter variables only; skips the `E` of `1E5` and the lowercase safe names (`math`, `abs`, `min`, `max`, `round`) |
| `_cpv_vars(expr)` | ordered unique letters in an expression |
| `_cpv_rewrite(expr, letter_map)` | renames letters in one pass (no cascading through an already-rewritten letter) |
| `_cpv_to_display(entry, letter_by_pv)` | stored letters → the letters valid right now, for the dialog |
| `_cpv_from_display(expr, pv_by_letter)` | back to `(expr, bindings)` when the dialog is accepted |
| `_CPV_LEGACY_LETTERS` | the PV order that pre-`bindings` entries were written against; `_migrate_custom_pv_bindings` uses it once, from `_populate_ui`, then saves |

`_cpv_dialog_channels()` letters every loaded channel and then every bound PV
that is *not* loaded, so an expression stays readable in any preset and the
dialog can name the PVs it cannot currently compute. `_compute_custom_pvs_in_rows`
collects unresolved bindings, syntax errors and per-formula evaluation-failure
counts into `_cpv_diag`; `_emit_custom_pv_diag` logs them and suppresses repeats,
because the rebuild runs on every live refresh.

The dialog shows edge gaps as percentages of the figure (what a user thinks in)
while storing subplot fractions, live-applies on Apply, and can restore the
defaults or whatever was in effect when it opened.

### Performance constants and helpers

| Name | Why it exists |
|------|---------------|
| `_LIVE_MAX_SPAN_S` = 366 d | a window outside 60 s … this falls back to 1 h |
| `_LIVE_MAX_INIT_SPAN_S` = 12 h | the *live* ceiling. The config remembers the last From/To, so a window that grew to days silently became the live window on the next start — and a live window is re-merged, re-filtered and re-drawn for the whole session. `_live_span_from_window()` clamps it and logs the clamp; LOAD DATA still loads any span |
| `_LIVE_TICK_LOOKBACK_NS` = 2 min | the hard bound on one tick's query range. `_live_last_ts` only advances when samples arrive, so on a quiet archiver `[last sample → now]` grew without limit and each 300 ms tick fanned out into (hours × PVs) 1-hour chunk requests. Kept ≤ `CHUNK_SIZE_NS`, so a tick is exactly one request per PV |
| `_LIVE_TICK_OVERLAP_NS` = 30 s | an empty tick still moves `_live_last_ts` to `now −` this, so the next query stays small while the overlap covers archiver ingestion lag |
| `_MAX_TABLE_ROWS` = 5000 | only the newest rows are rendered; export/graph/XY always use the full `_table_rows` |
| `_TABLE_SEVERITY_FG` | severity → colour **string**. A `QColor` in a module global is destroyed after the `QApplication` and takes the interpreter down with it (0xC0000005 on exit) |
| `_LIVE_REBUILD_MIN_INTERVAL_NS` = 1 s | the live tick polls every 300 ms, but the full merge + filter + replot is throttled to this cadence (otherwise a long session spends all its time rebuilding) |
| `_NS_PER_DAY` / `_mpl_epoch_num()` / `_ns_to_num()` | ns → matplotlib date numbers as one numpy division, instead of a `datetime` per sample |
| `_pairs_to_ns_arrays` / `_samples_to_ns_arrays` | samples → `(ts_ns, values)` numpy arrays |
| `_downsample_arrays_mean(ts, vals, target)` | mean-bucket downsampling to the target point count |
| `_moving_avg_np(vals, w)` | smoothing for the per-PV Smooth column |

### Signal + support classes
`_LoadSig` (`done`/`error`/`progress`/`pct`), `_IncSig` (live increments),
`_ChanSig` (channel list), `_ColorSwatchDelegate` (colour cell in the axis
table), `_FlowLayout` (wrapping button rows), `_GraphPopupWindow` (F11 / Ctrl+F11
floating graph window).

### CSSLoggerWidget — state (`_init_state`)
Data: `_samples_by_pv`, `_table_rows` (+ `_table_rows_unfiltered`), `_pv_order` /
`_base_pv_order`, `_col_full_names`, `_numeric_pvs`, `_pre_window_vals`
(carry-forward value before the window start), `_pairs_cache`.
Graph: `_mpl_figure` / `_mpl_canvas`, `_graph_axes`, `_graph_lines`, `_graph_pvs`,
`_graph_raw` / `_graph_raw_np`, `_graph_spine_xpos`, `_span_selector`,
`_zoom_selector` + `_zoom_history`, crosshair artists (`_crosshair_*`,
`_x_cursor_ann`, `_y_cursor_ann`), `_blit_bg`, `_cursor_frame_ms` (adaptive frame
budget), `_graph_popup`.
XY: `_xy_figure`, `_xy_rows`, `_xy_scatter`, `_xy_rect_selector`,
`_xy_zoom_history`, `_xy_choice_map`.
PV Time: `_pv_time_figure`, `_pv_time_df`, `_pv_time_columns`,
`_pv_time_condition_rows`.
Live: `_live_mode`, `_live_last_ts`, `_live_window_span`, `_live_last_rebuild_ns`,
`_live_rebuild_cost_ns`, `_live_autoscroll`, `_live_timer` + `_countdown_timer`,
`_live_epoch` (cancel token: every fetch worker captures it and its pool aborts
once it moves — bumped by `_stop_live` and by each `_live_initial_load`).
Coalescing timers: `_cursor_tbl_timer` (cursor table rewritten only when the
mouse settles), `_replot_timer` (`_schedule_replot`, collapses rapid axis-table
edits into one redraw).
Config: `_graph_opts`, `_presets`, `_condition_presets`, `_custom_pvs`,
`_conditions`, `_ref_lines`, `_pv_settings`, `_master_pv`, `_master_multiple`,
`_axis_tv_cols`, `_axis_row_pv`.

### Key method groups

| Group | Methods |
|-------|---------|
| build | `_build_ui`, `_build_sidebar`, `_build_graph_tab`, `_build_axis_settings_panel`, `_build_xy_tab`, `_build_pv_time_tab`, `_build_table_tab`, `_build_log_tab`, `_populate_ui` |
| load | `_on_load_clicked`, `_on_load_error`, `_on_load_finished` / `__on_load_finished_inner`, `_build_table_rows` |
| graph | `_plot_graph` (+ `_schedule_replot`), `_plot_graph_impl`, `_update_graph_data`, `_band_ylim`, `_compute_x_ticks`, `_apply_font_size`, `_clear_graph`, `_clean_graph`, `_save_graph`, `_graph_popout` / `_restore_graph_from_popup`, `_install_graph_shortcuts` |
| cursor | `_on_canvas_draw`, `_on_graph_mouse_move`, `_process_mouse_move(_impl)`, `_flush_cursor_table` |
| stats / selection | `_on_span_select`, `_make_stat_card`, `_copy_stats_text`, `_clear_stats`, `_on_zoom_select`, `_zoom_back` |
| graph options | `_open_graph_settings_dialog`, `_apply_graph_opts`, `_avg_target_points`, `_on_avg_target_changed` |
| live | `_toggle_live_mode`, `_live_span_from_window`, `_live_initial_load`, `_on_live_init_error`, `_after_live_initial_load`, `_live_tick`, `_on_incremental_finished`, `_schedule_live_tick`, `_live_countdown_tick`, `_stop_live`, `_maybe_autostart_live` |
| filtering | `_apply_conditions_to_rows`, `_row_matches_conditions`, `_condition_value_ok`, `_get_master_pv`, `_get_master_multiple`, `_remove_master_only_rows`, `_remove_fake_hour_boundary_rows`, `_filter_master_multiple_rows` |
| custom PVs | `_col_letter`, `_channel_letters`, `_cpv_dialog_channels`, `_migrate_custom_pv_bindings`, `_compute_custom_pvs_in_rows`, `_emit_custom_pv_diag`, `_rebuild_custom_pvs`, `_custom_pv_tooltip`, `_open_custom_pv_dialog` |
| table | `_populate_table`, `_set_table_cell`, `_format_value`, `_on_table_scroll`, `_on_table_context_menu`, `_on_table_double_click`, `_try_open_image_at_row` |
| axis settings | `_refresh_axis_settings_tv`, `_on_axis_tv_double_click`, `_on_axis_tv_clicked`, `_on_axis_color_changed`, `_on_axis_item_changed`, `_apply_axis_settings`, `_get_pv_default_settings`, `_resync_pv_colors`, `_safe_float` |
| XY | `_refresh_xy_choices`, `_on_xy_axis_changed`, `_plot_xy(_impl)`, `_on_xy_rect_select`, `_clean_xy`, `_clear_xy_plot`, `_xy_zoom_back` |
| PV Time | `_plot_pv_time(_impl)`, `_draw_daily_distribution`, `_pv_time_add_condition_row`, `_pv_time_add_features`, `_clear_pv_time_plot`, `_load_data_repository` |
| PV list / presets | `_open_pv_browser`, `_remove_selected_pvs`, `_clear_pv_list`, `_on_pv_double_click`, `_real_pv_names`, `_sync_pv_list_customs`, `_update_pv_count`, `_refresh_preset_combo`, `_load_preset`, `_save_preset`, `_save_preset_as`, `_delete_preset` |
| misc | `_open_time_window_dialog`, `_refresh_time_labels`, `_open_conditions_dialog`, `_open_ref_lines_dialog`, `_export_csv`, `_save_runtime_state`, `_log`, `_clear_log`, `_update_status` |

The axis table columns are `_axis_tv_cols` = show · pv · display_name · color ·
cursor_val · ymin · ymax · auto_scale · width · smooth · grid, and every edit
goes through `_on_axis_item_changed` → `_schedule_replot`, so a burst of clicks
costs one redraw. `_resync_pv_colors` keeps the stored colours aligned with the
PV order, `_sync_pv_list_customs` keeps derived PVs in the sidebar list.

---

## cpva_core.py — non-UI helpers

### Constants
- `CPVA_BASE_URL` — `https://10.78.0.57:8443/api/1.0/cpva` (+ `/samples`,
  `/channels` endpoints), `CPVA_HTTP_TIMEOUT` = 10 s, SSL verification off
- `IMAGE_ROOT` — `\\users-L3.tier0.lcs.local\cpva-image-2026`
- `CHUNK_SIZE_NS` — 1 hour; the largest window that is safe for one API request
- `SAMPLE_HOLD_MIN_GAP_MS`, `MASTER_RAMP_PV`
- `CONFIG_FILE`, `PRESETS_FILE`, `CONDITIONS_PRESETS_FILE`, `CUSTOM_PVS_FILE` —
  next to the exe/script (`get_app_dir()`, `APP_DIR`)
- `RAMPING_PV_MAP` / `PV_TO_RAMPING`, `RAMPING_REPOSITORY_DIR`,
  `DATA_REPOSITORY_DIR` (both under `../Diagnostic/`)
- `TZ_PRAGUE`, `DEFAULT_CONFIG`
- `_SESSION` — one `requests.Session` mounted with an `HTTPAdapter(pool_connections=32,
  pool_maxsize=32)`. The fetch pools run up to 16 requests at once, and the default
  pool of 10 made every extra request pay a full TLS handshake ("connection pool
  is full, discarding connection") on the first many-chunk load.

### Helper functions

| Function | Description |
|----------|-------------|
| `get_app_dir()` | exe folder (frozen) or `__file__` |
| `load_config()` / `save_config()` | JSON config with default fallbacks |
| `load_presets()` / `save_presets()` | PV-list presets |
| `load_condition_presets()` / `save_condition_presets()` | condition presets |
| `load_custom_pvs()` / `save_custom_pvs()` | derived PVs |
| `load_ramping_repository()` | reads `RampingRepository/index.json` (offline) |
| `_http_get_json(url, timeout)` | shared GET → JSON |
| `cpva_fetch_samples(channel, start_ns, end_ns)` | one HTTP GET → list of dicts |
| `cpva_fetch_samples_chunked(...)` | splits the range into hourly chunks, skips night hours (`_chunk_is_night`) |
| `cpva_fetch_many_chunked(channels, …)` / `cpva_fetch_many_optimized(...)` | pooled multi-channel fetch. Both take `cancel_fn` — stopping a QTimer cannot stop a running pool, so "Stop Live" used to leave the whole queue hammering the archiver and holding the GIL; a cancelled pool drops everything it has not started (`_is_cancelled`) |
| `cpva_fetch_last_before(channel, before_ns)` | carry-forward value before the window; scans expanding rings (`_LAST_BEFORE_STEPS_S`, up to ~30 days) |
| `cpva_fetch_last_before_many(channels, before_ns, …)` | the same for many channels in one shared pool — a few stale PVs used to dominate the whole initial load; returns only channels that had a prior sample. Also `cancel_fn`-aware (the deepest ring scan is the longest single thing a load does) |
| `cpva_decode_value(sample)` | decodes a sample value (numeric / string / enum) |
| `cpva_fetch_channels()` | full list of archiver channels |
| `now_ns()` / `dt_to_ns()` / `ns_to_local_str()` / `_fmt_cursor_value()` | time + value formatting |
| `parse_user_datetime(s)` | user date/time input (ISO + European format) |
| `shorten_pv_name(full_name)` | shortens a PV name for the UI (`_STRIP_PATTERNS`) |
| `make_pv_query_matcher(q)` | compiles a query into a predicate: whitespace tokens must appear **in order** (`023 l3` == `*023*l3*`), explicit `*` / `?` still work. Compiled once per query, then run over ~10 000 channels per keystroke |
| `_matches_wildcard()` | thin wrapper over `make_pv_query_matcher()` (backwards compatibility) |
| `_open_path()` / `_looks_like_image_path()` / `_image_file_size()` / `_resolve_image_path()` | image helpers |
| `safe_divide(a, b)` | element-wise division with NaN for a zero/infinite denominator |

### Dependencies
- `requests`, `urllib3`, `orjson`, `ssl` — HTTPS to CPVA (no certificate verification)
- `numpy` — `safe_divide`
- no GUI toolkit (safe for headless tests)
