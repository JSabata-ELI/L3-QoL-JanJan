# CSS Logger — STRUCTURE

> Verified against source: 2026-08-21 · `main.py` 7820 L · `sp_t.py` 5696 L ·
> `cpva_core.py` 749 L · `test_smoke.py` 548 L · `test_live_pacing.py` 251 L ·
> `test_count_param.py` 52 L

User-facing documentation: `Readme CSS logger.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_CSS Logger_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `main.py` | The application — PySide6 "CPVA Suite" (CSS Logger + Spectra in one window). Run with `python main.py`. |
| `cpva_core.py` | Non-UI helpers: config/preset I/O, CPVA archiver HTTP, time / PV-name / image helpers. No GUI toolkit — shared by `main.py` and the tests. |
| `sp_t.py` | The Spectra widget, embedded as the second tab of the suite. This folder is its only home — see the warning at the top of `main.py`. Its own docs: `STRUCTURE_Spectra_tab.md`, `ReadMe_Spectra tab.txt`, `ReadMe_Spectra tab_Full.txt`. |
| `test_smoke.py` | Offline smoke test — headless (`QT_QPA_PLATFORM=offscreen`), network + dialogs mocked, clicks through every button/dialog; also covers custom-PV bindings (incl. the dialog's bindings table) and the Conditions filter. |
| `test_live_pacing.py` | Offline test of live-mode pacing: window clamping, bounded tick look-back, cursor advance on an empty tick, Stop-Live cancellation, table item reuse. |
| `test_count_param.py` | Focused test of the archiver `count` parameter handling (hits the real archiver). |
| `cpva_explorer_config.json` | Saved settings (time range, shown channels, conditions, master PV, `graph_opts`, …). |
| `cpva_presets.json` | Named PV-list presets. |
| `cpva_conditions_presets.json` | Condition (min/max filter) presets. |
| `custom_pvs.json` | Derived PVs — `{"name", "expr", "bindings"}` per entry, where `bindings` maps every channel letter in `expr` to the full PV name it stands for. |
| `derived_pvs.json` | A second store of derived-PV definitions. |
| `ramping_setups.json` | Named ramping setups. |
| `ramping_archive.json` | Index of the parquet files in `RampingRepository/`. |
| `RampingRepository/` | Local parquet data (ramping events) — works offline. |
| `build_config.json`, `icon.ico` | Build settings for Dev Tools, and the app icon. |
| `importtime.txt` | Captured `python -X importtime` output from a startup-cost measurement. Not read by the app. |

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
filters locally), `_ConditionsDialog` (min/max per PV; custom channels are
offered too), `_RefLinesDialog` (per line: name, which signal it belongs to, Y,
colour, style, width, reorder, delete, and a button that hands control back to
the two-click placement in the graph — signalled by `pick_request`, which makes
`_open_ref_lines_dialog` re-open the window afterwards), `_CustomPVDialog`,
`_GraphSettingsDialog`.

`_CustomPVDialog` holds expressions over channel letters A, B, C… plus two
mapping tables. The top one is the automatic letter assignment for the loaded
list (read-only). The bottom one — `_refresh_bindings_table` /
`_on_binding_picked`, rebuilt through `_queue_bindings_refresh` on a dialog-owned
single-shot `QTimer` — lists one row per letter per formula with an editable
channel combo; picking another channel rewrites that letter inside the
expression, which is what re-binds it (bindings are derived from the letters, so
there is no separate override state to keep in sync). Letters with no channel, or
whose PV is bound but not loaded, are marked instead of guessed.

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
| Time axis | `x_ticks_max`, `x_tick_seconds` (0 = automatic), `x_time_format` (`auto`/`hms`/`hm`) |
| Plot rect (figure fractions) | `margin_right`, `margin_top`, `margin_bottom`, `band_pad_frac` |
| Cursor / performance | `cursor_value_boxes`, `cursor_boxes_max`, `line_markers` |

`y_minor_ticks` is **off** by default: matplotlib builds a full Tick object
(2 lines + 2 texts) per minor tick, which with a dozen stacked axes is ~400
objects and the single largest cost of a redraw.

`label_pad_px` may be **negative**: the rotated tick numbers keep a couple of
pixels of empty margin around their digits, so a small negative value pulls the
axis title closer without any ink touching.

### Cursor value boxes — `_place_cursor_boxes`
Every box is anchored where the cursor line crosses its own trace. Boxes used to
be pushed onto two alternating rows, which moved half of them away from their
curve even with nothing in the way. Now the boxes are decluttered in pixel space:
only boxes that would really overlap are merged into a stack, and the stack is
spread out around the average height its members asked for. A box therefore moves
only when it has to, and only as far as it has to.

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
| `graph_opts["live_poll_ms"]` = 300 | how often the archive is asked. Was a literal duplicated in `_schedule_live_tick` **and** `_live_countdown_tick`, where the countdown label would silently disagree if only one were edited |
| `graph_opts["live_graph_min_ms"]` = 300 / `["live_table_ms"]` = 1500 | **two separate refresh clocks.** The graph fast path is cheap, the table rebuild re-merges the whole accumulated history (~1 s with a dozen PVs). They shared one throttle of `max(1 s, 3 × measured cost)`, so one slow table rebuild set the pace for both and the window looked ~5 s behind — the reported "refresh rate is 5 s". Each now keeps `max(floor, 2 × its own measured cost)`; costs are measured separately into `_live_graph_cost_ns` / `_live_table_cost_ns` |
| `_GRID_STYLES` | (name, matplotlib linestyle) handed out in order to the PVs that have Grid ticked, so several grids on one plot are distinguishable |
| `_XY_CMAP` | hand-built navy → purple → magenta → red ramp for the XY scatter. The stock ranges (`plasma` et al.) end in a pale yellow that is invisible on white, hiding the newest points |
| `_LIVE_BTN_OFF_STYLE` / `_LIVE_BTN_ON_STYLE` | the sidebar's one mode switch, green ⇄ orange, same padding either way so it does not resize when pressed |
| `_NS_PER_DAY` / `_mpl_epoch_num()` / `_ns_to_num()` | ns → matplotlib date numbers as one numpy division, instead of a `datetime` per sample |
| `_pairs_to_ns_arrays` / `_samples_to_ns_arrays` | samples → `(ts_ns, values)` numpy arrays |
| `_downsample_arrays_mean(ts, vals, target)` | mean-bucket downsampling to the target point count |
| `_moving_avg_np(vals, w)` | smoothing for the per-PV Smooth column |

### Signal + support classes
`_LoadSig` (`done`/`error`/`progress`/`pct`), `_IncSig` (live increments),
`_ChanSig` (channel list), `_ColorSwatchDelegate` (colour cell in the axis
table), `_CenteredCheckDelegate` (Show / Autoscale / Grid tick boxes painted in
the middle of their column — Qt and the app stylesheet both push them to the
left edge, so the box is drawn by hand and the click area matches it),
`_ComboBoxDelegate` (Style and Points cells; the list drops open on the first
click so picking a style is one gesture),
`_FlowLayout` (wrapping button rows), `_GraphPopupWindow` (F11 / Ctrl+F11
floating graph window), `_WheelGuard` + `_install_wheel_guard()` (application
wide filter: the mouse wheel only changes a spin box / drop-down / slider that
has been clicked into; otherwise the scroll is passed to the panel underneath).

### CSSLoggerWidget — state (`_init_state`)
Data: `_samples_by_pv`, `_table_rows` (+ `_table_rows_unfiltered`), `_pv_order` /
`_base_pv_order`, `_col_full_names`, `_numeric_pvs`, `_pre_window_vals`
(carry-forward value before the window start), `_pairs_cache`.
Graph: `_mpl_figure` / `_mpl_canvas`, `_graph_axes`, `_graph_lines`, `_graph_pvs`,
`_graph_raw` / `_graph_raw_np`, `_graph_spine_xpos`, `_span_selector`,
`_zoom_selector`, `_graph_toolbar`, `_user_zoomed`, `_reticking`, crosshair
artists (`_crosshair_*`, `_x_cursor_ann`, `_y_cursor_ann`), `_blit_bg`,
`_cursor_frame_ms` (adaptive frame budget), `_graph_popup`.
`_sel_range` — the statistics selection as `(xmin, xmax)` matplotlib date numbers,
i.e. **absolute time**. It has to live in state and not only inside the
`SpanSelector`: every full replot destroys the figure (and with it the selector
and the stat cards), which is why the selected region and its numbers used to
vanish on a reload, a font change or any axis-table edit. `_recompute_stats()`
derives the numbers from it, so they also follow newly arrived live data.
There is **no** `_zoom_history` / `_xy_zoom_history` any more: the view history
belongs to the toolbars (Home / Back / Forward) and a second hand-kept one would
drift out of step. `_on_zoom_select` pushes onto the toolbar's stack via
`push_current()`, so a right-drag zoom is undone by the same Back. `_user_zoomed`
only records "the user is looking somewhere of their own choosing", which stops
the live window scrolling the view out from under them.
XY: `_xy_figure`, `_xy_rows`, `_xy_scatter`, `_xy_rect_selector`, `_xy_toolbar`,
`_xy_choice_map`.
Automatic loading: `_load_in_flight`, `_reload_pending`,
`_pending_reload_reason`, `_autoload_timer` (400 ms debounce).
PV Time: `_pv_time_figure`, `_pv_time_df`, `_pv_time_columns`,
`_pv_time_condition_rows`.
Live: `_live_mode`, `_live_last_ts`, `_live_window_span`, `_live_last_rebuild_ns`
(table) + `_live_last_graph_ns` (graph), `_live_table_cost_ns` +
`_live_graph_cost_ns`, `_live_autoscroll`, `_live_timer` + `_countdown_timer`,
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
| load | `_on_load_clicked(silent=)`, `_on_load_error`, `_on_load_finished` / `__on_load_finished_inner`, `_build_table_rows` |
| automatic loading | `_request_reload(delay_ms, reason)`, `_do_auto_reload`, `_finish_load`. **There is no LOAD DATA button.** Everything that changes what should be on screen calls `_request_reload`: PV added / removed / cleared / renamed, a new time window, a new preset. A burst collapses into one fetch via `_autoload_timer`; a request made while `_load_in_flight` sets `_reload_pending` and runs from `_finish_load`. Every load end path **must** call `_finish_load()` — including the "live was switched off meanwhile" early returns in `_on_live_init_error` / `_after_live_initial_load`, or the lock stays held and no automatic reload can ever start again |
| graph | `_plot_graph` (+ `_schedule_replot`), `_plot_graph_impl`, `_update_graph_data`, `_band_ylim`, `_apply_x_ticks`, `_retick_from_current_xlim`, `_compute_x_ticks`, `_bottom_margin_floor`, `_keep_x_label_visible`, `_grid_style_for` / `_grid_style_name` / `_grid_style_index`, `_apply_font_size`, `_clear_graph`, `_clean_graph`, `_save_graph`, `_graph_popout` / `_restore_graph_from_popup`, `_install_graph_shortcuts` |
| graph toolbar | `_install_graph_toolbar`, `_install_xy_toolbar`, `_sync_graph_interaction_mode`, `_adopt_toolbar_margins`, `_on_graph_view_home`, `_push_graph_view`, `_on_xy_rect_zoom_push`, `_open_graph_view_menu` |
| cursor | `_on_canvas_draw`, `_on_graph_mouse_move`, `_process_mouse_move(_impl)`, `_flush_cursor_table` |
| stats / selection | `_on_span_select` (stores `_sel_range`, then delegates), `_recompute_stats`, `_restore_selection_band`, `_clear_selection`, `_drop_selection_if_outside`, `_make_stat_card`, `_copy_stats_text`, `_clear_stats`, `_on_zoom_select` |
| graph options | `_open_graph_settings_dialog`, `_apply_graph_opts`, `_avg_target_points`, `_on_avg_target_changed` |
| live | `_toggle_live_mode`, `_live_span_from_window`, `_live_initial_load`, `_on_live_init_error`, `_after_live_initial_load`, `_live_tick`, `_on_incremental_finished`, `_schedule_live_tick`, `_live_countdown_tick`, `_live_poll_ms`, `_scroll_live_time_axis`, `_stop_live`, `_maybe_autostart_live` |
| filtering | `_apply_conditions_to_rows`, `_log_conditions_diag`, `_row_matches_conditions`, `_condition_value_ok`, `_get_master_pv`, `_get_master_multiple`, `_remove_master_only_rows`, `_remove_fake_hour_boundary_rows`, `_filter_master_multiple_rows` |
| custom PVs | `_col_letter`, `_channel_letters`, `_cpv_dialog_channels`, `_migrate_custom_pv_bindings`, `_compute_custom_pvs_in_rows`, `_emit_custom_pv_diag`, `_rebuild_custom_pvs`, `_custom_pv_tooltip`, `_open_custom_pv_dialog` |
| table | `_populate_table`, `_set_table_cell`, `_format_value`, `_on_table_scroll`, `_on_table_context_menu`, `_on_table_double_click`, `_try_open_image_at_row` |
| axis settings | `_refresh_axis_settings_tv`, `_autosize_axis_pane` (the PV list is exactly as tall as the PVs it holds, capped so the graph keeps `_AXIS_PANE_MIN_GRAPH` px and the buttons stay on screen), `_on_axis_tv_double_click`, `_on_axis_tv_clicked`, `_on_axis_color_changed`, `_on_axis_item_changed`, `_apply_axis_settings`, `_get_pv_default_settings`, `_pv_style_kwargs`, `_pv_stats`, `_flush_axis_measured`, `_resync_pv_colors`, `_safe_float` |
| columns / looks | `_apply_default_axis_columns`, `_reset_axis_columns`, `_open_axis_column_menu`, `_build_styles_menu`, `_fill_styles_menu`, `_current_style_payload`, `_apply_style_payload`, `_save_style_preset`, `_load_style_preset`, `_delete_style_preset`, `_export_style_preset`, `_import_style_preset` |
| reference lines | `_open_ref_lines_dialog`, `_graph_ref_pv_choices`, `_ref_axis_for`, `_draw_ref_lines`, `_run_ref_pick`, `_set_ref_pick_step`, `_clear_ref_pick_dim`, `_end_ref_pick`, `_cancel_ref_pick`, `_on_ref_pick_click`, `_on_ref_pick_key`, `_pv_at_click` |
| XY | `_refresh_xy_choices`, `_on_xy_axis_changed`, `_plot_xy(_impl)`, `_xy_pairs`, `_on_xy_rect_select`, `_clean_xy`, `_clear_xy_plot` |
| PV Time | `_plot_pv_time(_impl)`, `_draw_daily_distribution`, `_pv_time_add_condition_row`, `_pv_time_add_features`, `_clear_pv_time_plot`, `_load_data_repository` |
| PV list / presets | `_open_pv_browser`, `_remove_selected_pvs`, `_clear_pv_list`, `_on_pv_double_click`, `_real_pv_names`, `_sync_pv_list_customs`, `_update_pv_count`, `_refresh_preset_combo`, `_load_preset`, `_save_preset`, `_save_preset_as`, `_delete_preset` |
| misc | `_open_time_window_dialog`, `_refresh_time_labels`, `_open_conditions_dialog`, `_open_ref_lines_dialog`, `_export_csv`, `_save_runtime_state`, `_log`, `_clear_log`, `_update_status` |

The axis table columns are `_axis_tv_cols` = show · pv · display_name · color ·
cursor_val · ymin · ymax · auto_scale · width · style · marker · marker_size ·
alpha · smooth · grid · unit · last · min · max · mean · count · blank. Every
edit goes through `_on_axis_item_changed` → `_schedule_replot`, so a burst of
clicks costs one redraw. `_resync_pv_colors` keeps the stored colours aligned
with the PV order, `_sync_pv_list_customs` keeps derived PVs in the sidebar list.

Columns are never addressed by a literal number — always
`list(self._axis_tv_cols).index(name)` — because the header is movable
(`setSectionsMovable`) and the user can drag any column anywhere. Row → PV comes
from `_axis_row_pv`, never from a column position. `_AXIS_READONLY_COLS` are the
cells the user cannot type into (the PV name, the live cursor readout and the
measured values); `_AXIS_HIDDEN_BY_DEFAULT` start switched off in the header's
right-click menu; `_AXIS_ALWAYS_SHOWN` (pv, blank) can never be hidden. Divider
rows paint every cell blue, not just the spanned first one, because a span
follows its logical column wherever it has been dragged.

`unit`/`last`/`min`/`max`/`mean`/`count` are measured, not stored: `_pv_stats`
computes them from `_samples_by_pv` and caches on the sample count
(`_pv_stats_cache`, cleared outright on every load — a new range can hold the
same number of samples). `_flush_axis_measured` refreshes only the ones on
screen and runs on every Live table refresh.

`style` / `marker` / `marker_size` / `alpha` are translated to matplotlib by
`_pv_style_kwargs` via the module maps `_LINE_STYLES` and `_MARKER_STYLES`
(`_MARKER_HOLLOW` marks the ones drawn with a white face). `marker: "auto"` is
the pre-existing behaviour — a dot only while `graph_opts["line_markers"]` is on
and the trace is under 200 points. Line *none* plus points *none* falls back to
solid: a signal must never become invisible.

`_build_styles_menu` / `_current_style_payload` / `_apply_style_payload` save and
restore a whole look (per-PV styling, reference lines, column order/widths/
visibility) as a named entry in `config["style_presets"]`, plus export/import to
a JSON file the user picks. Nothing is stored automatically — `_pv_settings` and
`_ref_lines` are still session-only by design.

### Graph traps — read before touching the plot

**The time-axis stamps are a FIXED LIST, so they must be rebuilt after every
change of the visible range.** `_apply_x_ticks` is the only place that installs
them, and `_retick_from_current_xlim` re-runs it for whatever is on screen. It is
called from the full replot, `_update_graph_data`, `_on_zoom_select` and the
toolbar's Home / Back / Forward. Miss one and the axis keeps the *old* window's
positions: a narrow zoom then shows **zero** timestamps (measured — that was the
bug). The live path already had its own copy of this fix; the zoom path did not.

**Grids are per PV, one per ticked channel.** Each ticked PV draws
`ax.yaxis.grid(...)` on **its own** twinx axis with its own colour and its own
`_GRID_STYLES` entry; the shared vertical time lines are drawn once on
`axes[0].xaxis`. Do not collapse them back into one boolean — that is exactly
what made the 2nd..Nth tick box appear dead. Two constraints:
- `ax.set_axisbelow(True)` on **every** axis. twinx axes draw in order, so a
  later PV's grid otherwise crosses an earlier PV's trace.
- matplotlib turns a grid **ON regardless of the first argument** as soon as any
  line property is passed with it. `grid(False, linestyle=...)` draws a grid. Pass
  the style kwargs only in the enabling branch.

**Margins are FRACTIONS of the figure; the axis text is a fixed size in points.**
So a shorter canvas silently starves the time axis. Opening the statistics strip
shortens the graph by well over a third (measured 418 px → 256 px) and the
"Time (Prague)" title went to y0 = −14, i.e. off the figure. `_bottom_margin_floor`
gives the smallest workable fraction and `_keep_x_label_visible`, wired to the
canvas `resize_event`, re-applies it in both directions — growing when the graph
shrinks, returning to the user's own setting when the room comes back. Any new
widget that changes the canvas height inherits this for free; anything that sets
`subplots_adjust(bottom=...)` by hand must respect the floor.

**The toolbar is bound to one canvas, and the canvas is rebuilt by every full
replot.** `_install_graph_toolbar` therefore tears the old one down and builds a
new one into the permanent `_graph_tb_holder`. `_clear_graph` drops it with the
canvas, or its buttons act on a destroyed figure. The holder is hidden and shown
alongside `_graph_ctrl_bar` in the F11 pop-out.

**Toolbar icons: matplotlib tints them ONCE, AT CONSTRUCTION, and only when it
thinks the palette is dark** — on a dark Windows theme they come out near-white
and read as blank buttons, and fixing the palette afterwards does nothing. Build
every matplotlib toolbar through `_make_mpl_toolbar`, which parents it to a
light-palette host first. (Pulser Monitor and Image Tools carry the same helper
for the same reason.) `_CustomToolbar`, `_TB_STYLE`, `_TB_HINTS` and
`_AxisLimitsDialog` are imported from `sp_t` in this folder, which `main.py`
already imports `SpectraWidget` from, so this adds no new coupling; the imports
are wrapped so a change there degrades to the stock toolbar instead of breaking
startup.

**Right-drag on the canvas is the zoom, so the canvas must not get a context
menu** — it would swallow the drag. Those entries live on the "View ▾" button
(`_open_graph_view_menu`).

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
