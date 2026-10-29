# CSS Logger — STRUCTURE

> Verified against source: 2026-08-21 · `main.py` 7820 L · `sp_t.py` 5696 L ·
> `cpva_core.py` 749 L · `testing/test_smoke.py` 548 L · `testing/test_live_pacing.py` 251 L ·
> `testing/test_count_param.py` 52 L

User-facing documentation: `Readme CSS logger.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_CSS Logger_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `main.py` | The application — PySide6 "CPVA Suite" (CSS Logger + Spectra in one window). Run with `python main.py`. |
| `cpva_core.py` | Non-UI helpers: config/preset I/O, CPVA archiver HTTP, time / PV-name / image helpers. No GUI toolkit — shared by `main.py` and the tests. |
| `cpva_cache.py` | `DayCache` — closed Prague days of archived signals, one `.npz` per signal per month on the scratch share (`<scratch>\Software\CPVA cache\<signal>\<YYYY-MM>.npz`). See "The day cache" under cpva_core. |
| `logger_tabs.py` | `LoggerTabs` — the setting tabs: each one a `CSSLoggerWidget`, the bar placed in the main tab row, suspend/resume, `%APPDATA%\CSS Logger\logger_tabs.json`. See "Setting tabs" below. |
| `testing/test_day_cache_and_tiles.py` | Offline: newest-step-first reading and the "complete back to" edge, at most `MAX_TILES` steps, the day cache (second load asks nothing for closed days, coarser days not served for finer requests, today and words never stored, two writers of one month), the open day in ≤4 h pieces and a reload asking only for the last minutes (memory blocks: levels, the value before the window, off for a fresh cache), the bounded look-back (a refusing archiver, a cancel). |
| `testing/test_logger_tabs.py` | Offline: first start = one tab named after its preset, "+" copies the tab on screen, only the tab on screen is awake (and none while Spectra is), a hidden Live tab polls at `HIDDEN_LIVE_POLL_MS`, showing it ticks at once, rename / close / restore / background build. |
| `testing/bench_long_load.py` | Bench against the REAL archiver: one long load of a preset, a timeline of every partial picture and the end-of-load cost, a PNG of each picture into `testing/_out/`. Cache in a temp folder unless `CPVA_CACHE_DIR` is set. |
| `testing/probe_logger_tabs_look.py` | Renders the main tab row with three setting tabs (and with Spectra on screen) into `testing/_out/logger_tabs_row*.png`. |
| `sp_t.py` | The Spectra widget, embedded as the second tab of the suite. This folder is its only home — see the warning at the top of `main.py`. Its own docs: `STRUCTURE_Spectra_tab.md`, `ReadMe_Spectra tab.txt`, `ReadMe_Spectra tab_Full.txt`. |
| `daypicker.py` | The shared day/time picker — **a verbatim copy; the master is `Image Tools/daypicker.py`**. It is here because the builder only bundles `.py` files from the program's own folder, and it is loaded by path (`_import_daypicker` in `sp_t.py`) rather than by `import daypicker`, which the builder's module-home check would refuse. It is also listed in `build_config.json` → `hidden_imports`, which compiles it into the exe without any import statement for that check to trip over — **`_internal` never survives the trip to the share, so the loose copy the builder puts there cannot be relied on** (see below). Change the master, then re-copy; `testing/test_daypicker_sync.py` fails while the two differ. |
| `testing/test_smoke.py` | Offline smoke test — headless (`QT_QPA_PLATFORM=offscreen`), network + dialogs mocked, clicks through every button/dialog; also covers custom-PV bindings (incl. the dialog's bindings table) and the Conditions filter. |
| `testing/test_live_pacing.py` | Offline test of live-mode pacing: the period is used as given (no clamp), bounded tick look-back, cursor advance on an empty tick, Stop-Live cancellation, table item reuse. |
| `testing/test_long_window_fetch.py` | Offline test of loading a long period: full coverage of 12 h / 48 h / a year against an archiver that refuses anything wider than six hours, night hours read, no signal dragged down by another, unique ascending samples, unread ranges reported, cancellation, monotone progress, newest-first order, and the widget-level three-day load. |
| `testing/probe_x_axis_long.py` | Renders the time axis for six periods (1 h … 1 year) with the real Windows backend, measures the pixel gap between stamps, and saves a PNG of each into `testing/_out/`. |
| `testing/test_export_table.py` | Offline test of the table export and the carry-forward: the shot grid ignores the channels written at their own pace, a held value crosses a slice, a word value survives into the file, the table's columns follow the graph, and a whole day exports without a duplicate row at a slice seam. |
| `testing/test_shot_count_selection.py` | Offline test of the shot counter: `find_shots` (5 s rate, a free runner and a housekeeping channel never vote, a skipping channel never loses a shot, a late channel is its shot's end), `_fmt_span_full`, the heading "200 shots" with A's own N 187 and the custom PV's 200, the thinned load counted from a raw read of the selection ("…" first), and a late live sample put in place. |
| `testing/test_live_selection.py` | Offline test of the live selection (dragged past the right edge → its end sits on and follows the newest sample, a selection inside the graph stays put, Clear ends it), the left-button-only statistics selector, and the Axis limits dialog (date/time fields, one entry per PV, Y lands in the PV's own Y min/max, the time range survives the replot). |
| `testing/test_live_rebuild_clock.py` | Offline test of Live: a due tick with nothing new still merges what earlier ticks brought (and the custom PV follows), and a slower PV's late sample is kept without duplicates. |
| `testing/test_custom_pv_vectorised.py` | Custom PVs over whole columns give exactly what the old per-row eval gave (missing source, word value, custom of a custom, `A if … else B`, 3-argument min/max, round, math, division by zero), are faster, and the live tail matches the next rebuild. |
| `testing/test_last_good_shot.py` | Offline test of the last-good-shot hold: `_last_passing_moment` walks back over hours of laser-off, never answers with a sample after the period, gives up at the depth or on a dead archiver; a laser-off hour with the Conditions on draws flat lines at the last good shot's values with the yellow note naming it. |
| `testing/probe_time_window_look.py` | Renders the Start/End dialog on both tabs (real fonts, nothing on screen) and checks the window size never changes. |
| `testing/test_time_window_dialog.py` | Offline test of the Start/End picker: paging the calendar moves the selected day (a months-long period must not come back as the last hour), the status line follows, a short month clamps, and the multi-day picker keeps its own list. |
| `testing/test_xy_same_shot.py` | Offline test of XY pairing: a point only where both channels wrote in one shot (a late energy still pairs), rows from other channels add nothing, Conditions drop shots, a thinned window warns about averages. |
| `testing/test_xy_live.py` | Offline test of the XY tab: a live tick grows the cloud without rebuilding the figure, a hidden tab is only marked and caught up on the way in, a condition thins it out (and the Table tab with it), and a right-drag zoom leaves Back and Forward usable. |
| `testing/test_graph_focus_move_resize.py` | Offline test of the Graph tab's F11 window: frame only when windowed, a left press on the graph (also on a widget added later) moves it, the frame and corners resize, fullscreen does neither, docked back the graph's presses are left alone. |
| `testing/test_styles_menu_and_drop_marker.py` | Offline test of the Styles menu and the column drop marker: Delete asks only after the menu has closed and removes the look, Load/Delete are greyed out with a reason when nothing is saved, the entry under the mouse is light blue, and dragging a heading shows the blue line on the landing edge. |
| `testing/test_style_current_and_version.py` | Real-backend test (no focus taken): `_app_version()` reads the version from the exe name ("" from source), the "Loaded style:" line under Styles follows load / delete, escapes the name and is not kept between runs; saves `testing/_out/sidebar_style_current.png`. |
| `testing/test_instant_changes_and_wheel.py` | Offline: PV-table look edits change the graph in place (same canvas, nothing queued, nothing read), sidebar Move / Remove without a read, zoom kept through a rebuild, the wheel over an unclicked drop-down / a tab bar / a calendar changes nothing. |
| `testing/test_wheel_guard.py` | Offline: the wheel guard installs once, fields become StrongFocus, a click selects, the pointer leaving / hiding keeps it, focus to another control ends it (another window / its own list does not), a tab bar is still swallowed; `_nudge_taskbar_icon` defined after `_icon_app_id` and called on the shown window. |
| `testing/test_sidebar_names_conditions.py` | Offline: the wheel guard (unclicked field ignores the wheel, a click arms it, the pointer leaving does not disarm it), the fixed INFO line, Live beside Time window, PV list names / rename / move, every condition kind and the old forms, the Conditions window applying at once, PV Time asking for its own redraw, the bindings table keeping its scroll. |
| `testing/probe_y_axis_columns.py` | Measures the gap between each Y-axis title and the column to its left (4 / 12 PVs, 9 / 11 / 14 pt) and saves crops into `testing/_out/`. |
| `testing/test_conditions_no_data_and_cache_seed.py` | Offline (fixes of 12.10.2026): the INFO custom-PV box names only custom PVs ticked Show; a condition channel with no sample in the window is skipped at the end of a load as in the pictures during it; the range rule for thinned points; "Keep steps every" stops zoom detail only when it acts; a cached day comes back with the sample before it. |
| `testing/probe_conditions_thinned.py` | Real archiver, no window: per day, how many thinned condition-channel points pass by their mean vs by their min..max range. Showed the 2025 case: PTM1/PCM4/PAP1 have no samples at all there. |
| `testing/probe_sidebar_conditions_look.py` | Renders (real Windows backend, no focus taken) the sidebar with a short and a very long INFO message, the Conditions window and the PV Time Plot from synthetic days. |
| `testing/probe_graph_layout.py` | Renders the Graph tab with the real Windows backend: the opening graph/PV-list split and how many rows it shows, the Std / P-P percentages, and the cursor value boxes with the mouse at each edge. PNGs into `testing/_out/`. |
| `testing/probe_export_dialog.py` | Renders the Export table dialog with the real Windows backend into `testing/_out/export_dialog.png` and measures it for pale text, empty labels and clipped names. |
| `testing/probe_export_live.py` | End-to-end against the REAL archiver: loads a short window, prints how many rows each channel actually has a value in (so a held-only channel is visible as such), exports, and reads the file back. |
| `testing/test_count_param.py` | Focused test of the archiver `count` parameter handling (hits the real archiver). |
| `cpva_explorer_config.json` | Saved settings (time range, shown channels, conditions, master PV, `graph_opts`, …). |
| `cpva_presets.json` | Named PV-list presets. |
| `cpva_conditions_presets.json` | Condition (min/max filter) presets. |
| `custom_pvs.json` | Derived PVs — `{"name", "expr", "bindings"}` per entry, where `bindings` maps every channel letter in `expr` to the full PV name it stands for. |
| `derived_pvs.json` | A second store of derived-PV definitions. |
| `ramping_setups.json` | Named ramping setups. |
| `ramping_archive.json` | Index of the parquet files in `RampingRepository/`. |
| `RampingRepository/` | Local parquet data (ramping events). Historical: nothing has been written since 2026-06-19 and no tab reads it — the PV Time Plot now reads the archiver. `RAMPING_PV_MAP` in `cpva_core.py` is the short-name → PV table those files were built from, i.e. which archiver channel each old column came from. |
| `build_config.json`, `icon.ico` | Build settings for Dev Tools, and the app icon. |
| `importtime.txt` | Captured `python -X importtime` output from a startup-cost measurement. Not read by the app. |

> The old tkinter `cssl.py` is gone; its non-UI helpers live in `cpva_core.py`,
> the UI was fully replaced by `main.py` (PySide6).

---

## main.py

### Purpose
Explorer and logger for CPVA (Control System Studio) archive data. Shows PV
values over time, walks the archive, exports a shot table to CSV, draws graphs
(Graph / XY / PV Time) and opens the camera images belonging to a row.

`CPVASuiteWindow(QMainWindow)` hosts two tabs — **CSS Logger**
(`logger_tabs.LoggerTabs`, a row of `CSSLoggerWidget` setting tabs) and
**Spectra** (`sp_t.SpectraWidget`) — restores its geometry and asks both
children to stop their background work on close. `_css_logger` is now a
property: the first setting tab (it owns `cpva_explorer_config.json`). Spectra's
custom-PV host calls go to `_logger_tabs.active()`.

### Setting tabs (`logger_tabs.py`)
Asked for 12.10.2026 ("like Image Tools": one tab per setting). The pattern is
Image Tools' `slider_tabs.py`: `_TabArea` (bar + "+" right after the last tab,
over a stack), `_TabPage` (holds `logger` once built, or the saved `state`),
`LoggerTabs` (controller). The bar is moved into a corner strip of the main
`QTabWidget` (`move_chrome_into`), inside a frameless see-through holder
(`QFrame#sessionTray`, bottom-aligned so the setting tabs stand on the main
tabs' bottom line), shown only while the CSS Logger tab is current
(`_sync_tab_row` in `CPVASuiteWindow._build_ui`, which also calls
`set_outer_visible`). Since 15.10.2026 the setting tabs have the main tabs'
shape — rounded top, closed bottom (`_TAB_QSS`) — but quieter: smaller, 1 px
grey rim, pale fill, bold 13 px text in a fixed 26 px height; the current one
is light slate, and
every close button carries the dark "×" (`_sync_close_icons`). The main tabs
get a 2 px rim all round (bottom too) on their own bar only, so the panels'
inner tabs keep their style.

- **State.** `CSSLoggerWidget(tab_state=…)`; `export_tab_state()` = preset,
  real PVs, master PV/multiple, conditions + on/off, `live` + `live_span_s`,
  `time_from`/`time_to`. `_apply_tab_state` runs after `_populate_ui` (which
  still loads the Operation preset first). `_maybe_autostart_live` follows a
  saved `live: false` with a plain reload instead of starting Live. Per-user file
  `%APPDATA%\CSS Logger\logger_tabs.json` (`CSS_LOGGER_TABS_FILE` overrides it —
  every test that builds `CPVASuiteWindow` must set it), autosave 15 s + quit,
  atomic. The config file still holds the shared things (graph options, styles,
  display names) and is written by the first tab only.
- **Activity.** `_apply_activity`: the current tab `resume()`s while the CSS
  Logger is on screen, every other one `suspend()`s. `suspend` sets
  `_suspended`: `_live_poll_ms` becomes ≥ `HIDDEN_LIVE_POLL_MS` (10 s, well
  inside `_LIVE_TICK_LOOKBACK_NS`), `_on_incremental_finished` only merges
  (graph and table clocks forced off, no axis scroll), the countdown stops,
  `_on_load_partial` draws nothing. `resume` forces the next table + graph
  rebuild and runs a live tick at once (if the timer is armed — a tick in
  flight finishes into the forced rebuild), or redraws a non-live tab, and
  re-reads `custom_pvs.json` when another tab changed it (mtime).
- **Building.** The current tab is built at start; the others one by one
  (`BACKGROUND_FIRST_MS`, then every `BACKGROUND_NEXT_MS`), suspended before
  their 600 ms autostart, so they load in the background. "+" builds a copy of
  the tab on screen. Close stops Live and bumps the load / live / export epochs.
- **Archive load.** Every tab fetches on its own; `cpva_core._IN_FLIGHT` caps
  the whole program at `MAX_REQUESTS_IN_FLIGHT` (24) concurrent requests.

### Tabs
- **Graph** — one banded plot: every PV gets its own vertical band of a shared
  axes (CS-Studio style), its own Y axis column on the left, crosshair cursor
  with per-PV value boxes, span statistics, rubber-band zoom with history,
  reference lines, conditions, custom PVs, and the axis settings panel.
- **XY Plot** — scatter of one loaded PV against another, rectangle zoom + history.
- **PV Time Plot** — per-day violin (or every shot on a time axis) of one channel
  over days picked on the calendar, read from the archiver by the tab itself and
  cached per (day, PV) for the session. Independent of the Graph tab's window.
- **Table** — `QTableWidget` of the merged rows, context menu (Copy row / Open
  image), **Export table** (see below). Its columns are `_table_pvs()`, not
  `_pv_order`: the channels that are on the graph (`show` ticked) and have
  something in the window (a sample, or a held value from before it). A word
  channel keeps its column — it has no line but belongs in a table of shots.
- **Log** — run log.

### Sidebar
The scroll area is 310 px wide with its vertical scrollbar **always on**, so
the inner width never changes with the content height. Top row: **📅 Time
window** and **Live** side by side, equal width, `_TW_ROW_BTN_H` high; under
them `_lbl_tw_from` / `_lbl_tw_to` / `_lbl_tw_live` — `_refresh_time_labels`, which in Live
prints `now − _fmt_span(_live_window_span)`. `_do_auto_reload` recomputes the
span and must refresh the labels with it, or they keep the span Live started
with. Presets (load/save/save-as/delete): `_presets[0]` is the built-in
`_BLANK_PRESET` (`"builtin": True`, no PVs), dropped from the file by
`_store_presets`, refused by Save (→ Save as new) and Delete, never the startup
choice in `_populate_ui` (`testing/test_blank_preset.py`). The preset file is
shared by every station: `_read_presets_file` collapses duplicate names (later
content, earlier place), Load re-reads it, and `_store_presets(name, preset)`
re-reads, changes that one name and writes — never the in-memory list, which
would undo other stations' saves. Save keeps keys it does not edit and writes
master PV / multiple; Save as new on an existing name asks to overwrite
(`testing/test_preset_save_load.py`). PV list
(**+ Add PV** / **Custom PV** / Remove / bin — Custom PV is the only entry to
the custom-PV dialog; Remove moves the selection to the next PV), PV count,
Master PV / master-multiple filter, then:

**PV list rows show the NAME, not the PV.** A real row's text is `_col_label(pv)`
(Display Name → `_pv_display_names` → `shorten_pv_name`); the PV rides in
`_PV_ROLE` and is the tooltip. **Always read a row through `_item_pv()` /
`_real_pv_names()`, never `.text()`** — a row added as plain text (tests, old
code) has no role and its text is the PV, which `_item_pv` also handles.
Double-click / F2 renames (`_set_pv_display_name`); the right-click menu has
Rename, Change PV (the old double-click: points the row at another archiver
PV), Copy PV name(s), Move up / down (Ctrl+Up/Down — the order is the
bands' order), Remove (Del). Move and Remove go through `_reshape_from_memory`
(table rows, customs, graph rebuilt from the loaded samples, no archive read);
it declines — and the caller reloads — while Live is on, during a load, or when
the list holds a PV that was never loaded. A Display Name typed in the PV table goes through
`_remember_display_names` too, so list, graph, table heading, XY / PV Time
drop-downs and export all say the same. Names that differ from the automatic
one persist in `config["pv_display_names"]`.

`_lbl_status` is a `_SidebarText(max_lines=4)`: Ignored horizontal size policy
(a 40-character PV name used to widen the whole sidebar), zero-width break
points after `: _ - / .` (display only — `text()` returns what was set), a
fixed height, "…" plus the full text as tooltip when longer. `_InfoLabel` is
the same without the line limit, hidden while empty.

| Section | Widgets |
|---------|---------|
| **INFO** | `_lbl_status` (the status line — it used to be a grey label under the whole window; same attribute, so every `setText` caller is unchanged), `_lbl_graph_info` (graph notes such as "Zoom/pan on…", formerly the grey text at the top right of the graph; an `_InfoLabel`, hidden while empty), `_lbl_cpv_info` (orange: every custom PV ticked Show in the PV table that cannot be computed, from `_cpv_short`, set in `_emit_custom_pv_diag`, re-judged when the PV table is applied; the Log gets every one, ticked or not), progress bar, **Stop loading** |
| **DATA FILTER** | Conditions, Limit crossings |
| **GRAPH** | Reference lines, Graph settings, `_btn_graph_view` "View ▾", Clean graph, `_font_size_spin`, `_avg_target_spin` |
| **SELECTION** | `_btn_clear_sel` |

INFO lives in `_sidebar_info`, a holder UNDER the sidebar's scroll area (both
in a fixed-width 310 px column built in `_build_ui`), so it is always on screen
at the window's bottom edge; inside the scroll area it fell below the fold
whenever the sidebar was taller than the window. New sections go into the
scrolling part, before its final stretch.

RAMP / WAVEPLATE (was MASTER PV / FILTER until 12.10.2026): "Waveplate PV"
(`_master_pv_edit`; `editingFinished` → `_on_master_pv_edited` reloads, since
master-only rows are dropped when rows are built), "Keep steps every"
(`_master_multiple_edit`, default `MASTER_STEP_DEFAULT` = "1000", empty = every
row; `editingFinished` → `_on_step_edited` re-filters at once through
`_set_conditions`) and the dead-shot rule: `_chk_dead_shot`, `_dead_energy_edit`,
`_dead_energy_spin` (see the statistics notes below). Presets, tab state and
config store the group through `_ramp_settings` / `_apply_ramp_settings`; the
step key is `master_step` — `_saved_step` reads the old `master_multiple` only
when it is non-empty (it used to be saved empty by default).

PV list order: drag (InternalMove; `rowsMoved` → `_on_pv_list_dropped`, which
puts the ƒ rows back at the end) or Ctrl+Up/Down (`_move_selected_pvs`) both end
in `_apply_pv_list_order`, which REPLACES the real-PV part of `_plot_order`
when one exists — the PV-table drag order otherwise kept winning and moving a
PV in the list did nothing to the graph. Ctrl+C `_copy_selected_pv_names`,
Ctrl+V `_paste_pv_names` (split on whitespace / `,` / `;`, duplicates skipped,
one reload).

PV table refresh (`_refresh_axis_settings_tv`) updates cells in place: an item
is re-created only when the cell changes kind (tagged with `UserRole + 41`:
band / band_pad / blank / check / color / text); otherwise only differing
text, flags, check state, colour or tooltip is written, with
`setUpdatesEnabled(False)` and the scroll restored before painting. While a
cell editor is open the refresh is postponed (`_retry_axis_settings_tv`, 200
ms) so typed text is not lost.

Range selection uses `_EdgeSpanSelector` (both the left-drag selection and the
right-drag zoom): matplotlib's selector replaces an event outside the axes by
the last one inside, so a drag past the edge stopped short of it. The subclass
reads the position from the pixels, clips it to the plot and marks the event
as in the axes, so the band lands exactly on the edge.

Custom PV dialog: the Channel column of "Available channels" is editable for
real PVs (`_on_channel_renamed` → `result_display_names`);
`_open_custom_pv_dialog` applies each through `_set_pv_display_name`.

PV Time Plot: `_pv_time_progress` (under the plot) is driven by
`coverage_fn` of `cpva_fetch_many_adaptive` per day, forward only, hidden in
`_pv_time_finish`.

These were the row `_graph_ctrl_bar` above the graph, which is gone; the Graph
tab starts with the grouped icon strip `_graph_strip` (the matplotlib toolbar
behind it is hidden). The sidebar's PV TABLE section holds "Reset columns" and
"Styles", which used to sit in an "Axis settings" row above the table. Built in `_build_sidebar`, which runs
before `_build_graph_tab`, so anything the graph code reads from these widgets
exists by then. `_GRAPH_STALE_NOTES` are the graph notes cleared once a graph
is drawn (the cursor used to overwrite them).

The full date and time under the cursor that the old top-right label showed is
now in the time box under the axis whenever the view spans more than a day.

### Loading a long period
A period is read newest step first (see "Adaptive fetch") and **drawn as it
arrives**. The fetch threads' `chunk_fn` (`_on_chunk`) only decodes and APPENDS
to `acc` under a short lock; a separate `_snapshotter` thread merges and sorts
and emits `_LoadSig.partial` — never while the window is still drawing the last
one (`gui_ready`), never sooner than `max(2 s, 3 × _partial_cost)` (what the
last `_draw_load_partial` took), and not once coverage is ≥ 90 %. Measured
12.10.2026: building the snapshot inside the fetch threads, under the lock,
held up the whole pool (a 20 s load took 146 s), and back-to-back redraws
starved the fetch of the GIL. `_on_load_partial` repaints the graph and the
table without ending the load (it must never touch `_load_in_flight`, the
progress bar or the summary). The grey "loading" span runs to
`_loading_from_ns` = the fetch's `tile_fn` edge (complete for EVERY signal
from there to the end), no longer an average over signals. A partial picture
has no pre-window values yet (`_pre_window_vals = {}`, never the previous
load's), so with Conditions on rows next to the grey part can be missing until
the end. The bar is 0-95 % coverage, 96 decode, 100 done. The value before the
window is hunted AFTER the read, only for signals whose first sample is after
the start, with `LAST_BEFORE_DEADLINE_S` — it used to be unbounded and was the
"stuck at 92 %" of 09.10.2026; hunting every signal beside the read instead
slowed the archiver for the read itself. `_maybe_hunt_last_good` starts its
thread on a `QTimer.singleShot(0)`, i.e. after the end-of-load draw (running
alongside, it made that draw 11.5 s instead of 1.6 s). `_LoadSig.note` puts the
fetch's own notes into the Log while it runs. There is no "this will be big,
are you sure?" dialog — a year is a legitimate request, and the answer to "this
is taking too long" is **Stop loading**, which bumps `_load_epoch` (the
worker's `cancel_fn` watches it), keeps everything already painted and reports
the rest as unread. `_set_load_pct` holds the bar's high-water mark, because the
request total grows whenever the archiver refuses a range.

`_load_truth_suffix` is what stops the status line lying: `⚠ N signals
incomplete (X h unread)`, `stopped`, `raw data — the archiver does not thin it
out`, and the table/graph truncation. When nothing at all was read it says
**"Nothing could be read — see the Log tab"**, never "Loaded 0 pts" — that one
line is how a period over half a day used to present itself.

### PV Time Plot — its own fetch, cached per (day, PV)
This tab does **not** use the main load. It has its own worker
(`_pv_time_fetch`, own `_LoadSig`, own `_pv_time_epoch` cancel token) and reads
whole local days, `dt_to_ns(day) … dt_to_ns(day+1)`, of only the channels it
needs: the Y channel, every condition channel (switched on or not, so ticking a
box never triggers a fetch), and the sources of any derived channel among them.

`_pv_time_cache[(date, pv)] = {"ts", "val", "seed"}` — plain arrays, keyed **per
channel**, not per set of channels. Keying it per set was the first attempt and
it re-read every day the moment a condition named a ninth channel. `seed` is the
carry-forward value from before the day, fetched with
`cpva_fetch_last_before_many` only for channels whose first sample of the day is
missing or late; without it a slow channel (waveplate, valve) is blank all
morning and a condition on it drops the morning's shots.

`_pv_time_day_columns` turns the cached arrays into shots: the grid is the
**basis** channels' own timestamps (`req["basis"]`, the real PVs behind Y),
merged with the `SAMPLE_HOLD_MIN_GAP_MS` rule via
`np.diff(all_ts) > gap`; every other channel is read at each shot's last sample
with `searchsorted(..., "right") - 1`, falling back to `seed`. The grid must
come from Y alone: taking the union of every channel in play made a held-forward
value count as another shot, so the same number entered the distribution
repeatedly and the shot count changed with the condition list. Derived channels
are `eval`'d over whole columns with `abs/min/max/round` bound to their **numpy**
versions (`np.minimum`, `np.round`, …) — the builtins fail on an array.

Each finished day is handed over by `sig.partial` as it completes, so pressing
Plot mid-read keeps every day already fetched and only asks for the rest.

**The tab redraws on its own.** Y, mode, the days and every condition edit go
to `_pv_time_request_plot` → `_pv_time_auto_timer` (400 ms collapse) →
`_plot_pv_time`. A hidden tab only sets `_pv_time_stale`; `_on_main_tab_changed`
catches up on the way in (and draws the first time the tab is opened —
`_pv_time_auto_ready` stays False until then, so startup reads nothing). The
button is now **Refresh**. X stamps are upright: daily = `date\nweekday`, every
k-th written when they would collide; raw = `HH:MM\nMM-DD`, date only at midnight.

### Conditions — one editor, two places
`_ConditionEditor` is used by the Conditions window AND the PV Time Plot. A row:
on/off · channel (shown by name, stored by PV) · kind (`_COND_KINDS`: Between,
Target ± %, Target ± value, At least, At most, Outside) · one or two numbers ·
"keeps N of M" (`count_fn`) · ✕. Edits emit `changed` after 350 ms; a bad number
turns its box red and counts as empty.

`_cond_normalize` turns any stored form into `{pv, enabled, kind, a, b, min,
max, outside}`; **`min`/`max` are the effective bounds every filter reads**,
and `outside` (kind "outside") inverts them. Every evaluator goes through
`_cond_value_passes` (one number: `_row_matches_conditions` /
`_condition_value_ok`, `_export_row_ok`, `_condition_count`) or
`_cond_mask_passes` (arrays, NaN never passes: `_condition_mask` — its tables
carry `outside` as a 6th field — and PV Time). Old `{pv, min, max}` loads as
Between, the PV Time Plot's old `{target, tol}` as Target ± %.

PV Time reads condition channels (any channel outside the shot basis) up to
one merge gap AFTER the shot, the same look-ahead as `_condition_mask`;
reading exactly at the shot took the previous shot's SBW4. Measured with
`testing/probe_sbw4_offset.py`: SBW4 lands within 137 ms of PTM1 on ~99 % of
shots. PV Time has its own "Apply conditions" (`_pv_time_cond_apply`, config
`pv_time_conditions_on`) and "Copy from Graph conditions". Since 12.10.2026 all
of it sits in its own non-modal window `_pv_time_cond_dialog`, built once with
the tab and only shown/hidden (`_open_pv_time_conditions` /
`_close_pv_time_conditions`); the tab's "Conditions" button
(`_btn_pv_time_cond`) shows the count — `_refresh_pv_time_cond_button`.

Main view: `_conditions_all` (every row, saved to `config["conditions"]`) and
`_conditions` (the switched-on ones — what filters; tests may still set it
directly, `_conditions_view` then shows that). The window has only Close: each
change goes straight to `_set_conditions` (table, graph, XY), counts from
`_condition_count`. PV Time keeps its own rows (`_pv_time_cond_editor`, saved
to `config["pv_time_conditions"]`); its counts come from the tally
`_pv_time_day_values` builds while drawing. A row that is off AND has no
numbers (a new row, or a saved empty one) switches itself on when a number is
typed; a row with limits that was switched off by hand stays off. (A saved
empty SBW4 row used to stay off forever, so its 0.15 J never filtered.)

### `_make_calendar(initial, follow_page=)` — the day must follow the month
Paging the calendar (◀ ▶ / the month menu / the year box) does **not** move
Qt's selected date. In a picker whose value *is* the one selected day that is a
silent wrong answer: the user turns to February, presses OK, and gets the day
the dialog opened on — with the untouched clock boxes that is exactly the
window it was opened with, which is how "February → now" came back as **the
last hour**. `follow_page=True` moves the selection onto the month on screen
(same day of the month, clamped to its length) and repaints the delegate;
`TimeWindowDialog` uses it and also connects `cal.selectionChanged` to
`_refresh_status`, so the blue status line never disagrees with the highlight.
`DatePickerDialog` must NOT use it — its value is an explicit list of days.

### Table export — `_ExportTableDialog` + `_export_from_archive`
Two lists, because they answer two different questions: **columns** (default
`_table_pvs()`) and **rows**. Only channels written once per shot can give the
table its moments; `_export_shot_channels()` picks the numeric channels that are
not the master, not custom and not named like a housekeeping channel
(`_NON_SHOT_PV_HINTS`). A rate/timing/fate/state channel writes at its own pace,
so letting it mark shots turns a shot table into "something was archived here" —
measured on a real 8-minute window: 16 shots vs 2 221 rate samples. Those
channels are still exported, held at their last value.

The re-read (`source: "archive"`) is the point of the feature: the graph is
decimated to `avg_target_points`, so exporting what is loaded exports the
picture. It fetches raw (`count=None`) and streams:

| Piece | Role |
|-------|------|
| `_EXPORT_SLICE_NS` = 2 h | the window is read one slice at a time and appended, so a year never has to fit in RAM. Raw samples arrive as a dict *per sample* (with its own `metaData` dict), so a day of a dozen 3 Hz channels is most of a gigabyte. Small costs nothing: the request count is set by the span the archiver serves, not by the slice, and a dozen channels already fill the fetch pool. Keep it ≤ 4 h — above that `cpva_fetch_many_adaptive` adds a reachability probe per slice |
| `_export_slices` | contiguous `[lo, hi)` slices; the last one is `+1 ns` so a sample exactly on the window end is not dropped |
| `_export_slice_rows` | the shot grid = the basis channels' own timestamps merged by `np.diff > SAMPLE_HOLD_MIN_GAP_MS` (the same rule as `_build_table_rows`), then every channel read at `searchsorted(..., "right") - 1`, falling back to the value it is holding. Shots outside `[lo, hi)` are dropped — every request also returns the sample before its own start, so they belong to the neighbouring slice |
| `held` | carried across slices (seeded by `cpva_fetch_last_before_many` at the window start), which is what pairs a slow channel onto a shot |
| `last_ts` | a shot within one gap of the previous written row is skipped: the seam guard, because each slice groups its own samples independently |
| `_export_eval` / `_export_row_ok` / `_export_fmt` | custom PVs per row (from `_cpv_plan()`, with the sources of an exported formula added to the fetch even when they are not columns), the Conditions filter, and one cell — `%.10g` for a float (never `str(0.1+0.2)`), a word value as it is, quoted by `csv.writer` |
| `_export_epoch` | the cancel token. Bumped by the progress dialog's Cancel and by `CPVASuiteWindow.closeEvent`; a cancelled export keeps the file written so far |

The progress dialog is **non-modal** — an export can run for an hour and the
rest of the program must stay usable. Only one runs at a time.

### Dialogs
`TimeWindowDialog` (CS-Studio layout: two panes, Start | End, each a
`QTabWidget` `sd["tabs"]` — tab 0 "Absolute" = calendar + `QTimeEdit`
`sd["time"]` + `_ABS_QUICK` (Now/00:00/07:00/13:00/17:00), tab 1 "Relative" = years…secs spin boxes in a
3×2 grid, the `_PRESETS` quick spans (on Start: Start relative + End now; on
End: End's own span), "Before now", Now. A relative pane keeps its hidden
calendar on the moment its span gives. The window is `setFixedSize` from its
size hint, so neither a tab switch nor a long status text resizes it — the old
design folded the calendar away and the window jumped. `ends_at_now()` tells the caller whether the period follows the clock —
the End side on Relative with every box at zero. Without it, OK handed back two frozen
timestamps and "end = now, keep up" was indistinguishable from "end = now, stand
still", so picking yesterday 08:00-12:00 while Live was on showed the last four
hours instead. Same idea as `daypicker.DayTimePicker.is_online_mode()`.
The "Live" button (Absolute End only) → `_on_live()`: End Relative all-zero,
Start Relative 1 h, sets `_live_requested`; `wants_live()` = requested AND still
`ends_at_now()`, and `_open_time_window_dialog` then calls `_toggle_live_mode()`
instead of a plain reload),
`_GraphAxisLimitsDialog` (View ▾ → Axis limits: time From/To as
`QDateTimeEdit` in Prague time, no styling of its own — a styled spin box loses
its arrows; Y per PV picked from a combo with a colour swatch, kept per PV in
`_edits` so several can be set before OK, empty box = follow the data. OK calls
`_apply_axis_limits_dialog`, which writes `ymin`/`ymax` into `_pv_settings`
(the PV table's own cells, so a replot keeps them — Spectra's
`_AxisLimitsDialog` set one unnamed axis that the next `_band_ylim` overwrote),
runs the full replot FIRST when Y changed, and only then puts the time range
back on top with both views pushed onto the toolbar history; an untouched range
comes back up to a second off from the whole-second boxes, hence the 1.5 s
tolerance),
`DatePickerDialog` + `_make_calendar` + `_WeekendDelegate` (house calendar style,
Monday-first, red weekends), `PVBrowserDialog` (loads the channel list once, then
filters locally), `_ExportTableDialog` (see **Table export**), `_ConditionsDialog` (see **Conditions** below; custom channels are
offered too), `_RefLinesDialog` (per line: name, which signal it belongs to, Y,
colour, style, width, reorder, delete, and a button that hands control back to
the two-click placement in the graph — signalled by `pick_request`, which makes
`_open_ref_lines_dialog` re-open the window afterwards), `_CustomPVDialog`,
`_GraphSettingsDialog`.

Rebuilt tables keep their scroll position (`_CustomPVDialog._restore_scroll_later`,
which only undoes a jump to an end, never the user's own scroll): the bindings
table, the formula list on ✕, the PV table and the PV list.

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
| Time axis | `x_ticks_max` (12, was 8 — one rung finer on the round-step ladder: 1 h every 5 min, 1 day every 2 h; a stored 8 is moved to 12 once, marked by `config["x_ticks_default_12"]`), `x_tick_seconds` (0 = automatic), `x_time_format` (`auto`/`hms`/`hm`) |
| Plot rect (figure fractions) | `margin_right`, `margin_top`, `margin_bottom`, `band_pad_frac` |
| Cursor / performance | `cursor_value_boxes`, `cursor_boxes_max`, `line_markers` |

`y_minor_ticks` is **off** by default: matplotlib builds a full Tick object
(2 lines + 2 texts) per minor tick, which with a dozen stacked axes is ~400
objects and the single largest cost of a redraw.

Column spacing defaults are 5 / 0 / 4 px (gap / label pad / outer), measured
and looked at with `testing/probe_y_axis_columns.py`: the title's capitals reach
the edge of its text box, so a gap of 0 puts the neighbour's spine on the
letters. The user asked for 2 px more air towards the neighbour and 1 px less
towards the own numbers than the earlier 3 / 1. The old 6 / 5 / 8 written back
into a config are moved once (`config["y_axis_tight_v1"]`), then 3 / 1 once
more (`config["y_axis_spacing_v2"]`); hand-set values are kept.

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

Horizontally the boxes have to be steered too. They are `clip_on=False`, so
nothing stops them being drawn past the axes and past the window — with the
mouse at the far right the numbers were simply off-screen. `_place_cursor_boxes`
now estimates each box's width (`_cursor_box_width`: text length × font size ×
dpi, the same cheap arithmetic the height estimate uses — asking matplotlib for a
real extent would need a renderer on every mouse move) and flips the WHOLE set to
`ha="right"` when the widest one would cross the right spine, so they stay
together instead of flipping one at a time. `_edge_align` does the same for the
time box under the axis (`ha` centred → left/right near an end), and the
left-hand value box is clamped into the plot rectangle vertically while still
printing the value under the real mouse position, not the clamped one.

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

**A letter is resolved through its binding only** — in `_cpv_plan`,
`cpv_defs_for_spectra` and `_pv_time_resolve` alike. There is no fallback to the
letter's place in today's list: that would read a different PV after every preset
switch. An unbound letter leaves the custom PV empty with "X is not bound to a
PV". A custom built on another custom is bound to that one's *name*, so
`_CustomPVDialog._accept` carries a rename (`rec["orig_name"]` → new name) into
every binding that named it. Pinned by `testing/test_custom_pv_stable_bindings.py`,
which also checks every saved `custom_pvs.json` entry under every saved preset.

### SBW4 is archived under two names
`HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy` and `L3-SBW4-PM311:Energy` take turns with
the laser configuration. The operator's rule (23.09.2026) is `read_order`:
**HAPLS first**, whichever name is in the list; the other only where HAPLS holds
nothing. It is applied INSIDE `_fetch_many_adaptive`, per Prague DAY: a two-name
channel's tile is queued one day at a time (`_unit_requests`, keyed
`(channel, tile, day)` in `by_name`); when every request of a day under one
name is back, `_settle_tile` keeps it, or queues the whole day under the next
name. Never per request — judging pieces separately mixed the names inside a
day. "Holds nothing" is `_holds_nothing`: no sample inside the day, OR a
thinned reply that is a grid the archiver invented (all `Interpolated`, one
value, min == max) — measured 12.10.2026, that grid (144 points a day) hid
the L3 name's real data for 12.-29.08.2026, and it was why the old after-pass
(`_empty_day_ranges` on the result) never asked the L3 name. Every reply of a
two-name channel goes through `_drop_after` (the trailing bracket sample can
be weeks later); the other name's day keeps only its own samples; a day no name
holds is filed empty. A refused name (HTTP 400) also moves the day to the next
name. Both names in the list as separate channels: each is read as named. The
whole-period `_decimated_is_suspect` check skips two-name channels (their empty
days are empty on purpose). `cpva_fetch_last_before` hunts HAPLS first and keeps
it when it is from the same day, else the newer of the two (`strict` refuses an
invented answer). Spectra's `sp_t._cpva_fetch_chunked` reads `read_order` the
same way. The rule's master copy is `Image Tools/cpva_client.py`
(`channel_aliases`, `read_order`).

The dialog shows edge gaps as percentages of the figure (what a user thinks in)
while storing subplot fractions, live-applies on Apply, and can restore the
defaults or whatever was in effect when it opened.

### Performance constants and helpers

| Name | Why it exists |
|------|---------------|
| `_LIVE_MAX_SPAN_S` = 366 d | a window outside 60 s … this falls back to 1 h |
| `_LIVE_SLOW_SPAN_S` = 24 h | **not** a ceiling. There used to be a 12 h clamp (`_LIVE_MAX_INIT_SPAN_S`), which meant the graph quietly disagreed with the From/To it was captioned with. Live now uses the period as asked; above this span `_live_span_from_window()` says in the Log that every refresh re-reads and redraws the whole of it. What made the clamp unnecessary: Live only ever *starts* for a period that ends at "now" (see `TimeWindowDialog.ends_at_now()`) |
| `_LIVE_TICK_LOOKBACK_NS` = 2 min | the hard bound on one tick's query range. `_live_last_ts` only advances when samples arrive, so on a quiet archiver `[last sample → now]` grew without limit and each 300 ms tick fanned out into (hours × PVs) 1-hour chunk requests. Kept ≤ `CHUNK_SIZE_NS`, so a tick is exactly one request per PV |
| `_LIVE_TICK_OVERLAP_NS` = 30 s | an empty tick still moves `_live_last_ts` to `now −` this, so the next query stays small while the overlap covers archiver ingestion lag |
| `_LIVE_LATE_ARRIVAL_NS` = 5 s | every tick queries from `_live_last_ts −` this, and the worker keeps, per PV, only samples newer than **that PV's own** last sample (`own_last`, captured on the GUI thread before the worker starts). The worker used to filter every PV by `start_ns` = the newest timestamp of ANY PV, so a slower channel's sample archived a moment after a faster channel's newer one was dropped for good — and a custom PV built on it held a stale value |
| `_live_rows_stale` / the table clock | `_live_last_rebuild_ns` is restarted only by a rebuild that ran. It used to restart on every due tick, also one that brought nothing — that tick skipped the rebuild AND postponed the next, so with sparse channels customs (only recomputed by the rebuild) lagged many seconds. The rebuild now runs on a due tick whenever `_live_rows_stale` (data arrived since the last one), whatever the tick itself brought. Pinned by `testing/test_live_rebuild_clock.py` |
| `_extend_custom_series_live` | on a graph tick without a table rebuild, appends each custom trace's newest stretch computed straight from its sources (their timestamps after the trace's last point, gap-merged like the rows, held values via `_held_column`, formula via `_cpv_eval_columns`). The next rebuild replaces the series with the row-based one. Live trimming is a `bisect` + `del` on the sorted list instead of a list comprehension per PV per tick |
| `gc.set_threshold(50_000, 20, 10)` | at import. The default 700-allocation gen-0 threshold made the collector rescan the millions of sample tuples during every row build — measured 300 ms → 65 ms on a 60 000-row custom-PV compute next to a million samples. Gens 1/2 keep default ratios so matplotlib figure cycles are still collected |
| `_cpv_eval_columns` / `_CPV_NP_ENV` | a formula evaluated once over whole columns (`abs/min/max/round/math` bound to numpy; `min`/`max` reduce so three arguments work). A formula numpy cannot take whole (`A if A > 0 else B`) falls back to per-element eval with `_CPV_SAFE_ENV`, same answers. `_compute_custom_pvs_in_rows` reads each source out of the rows once (that read, not the eval, is the cost) and keeps `_cpv_cols` for `_rebuild_custom_pvs`. Pinned against the old per-row loop by `testing/test_custom_pv_vectorised.py` |
| partial-load handshake | `_on_load_partial` gets a `threading.Event` with each snapshot and sets it when drawn; the worker makes no new snapshot while it is clear. The 1.5 s wall-clock throttle alone let snapshots queue up behind a slow repaint |
| `_MAX_TABLE_ROWS` = 5000 | only the newest rows are rendered; export/graph/XY always use the full `_table_rows` |
| `_NON_SHOT_PV_HINTS` | name fragments of channels that are written at their own pace and must not mark shots in the export (`_export_shot_channels`) |
| `_EXPORT_SLICE_NS` = 2 h | see **Table export** |
| `_CPV_SAFE_ENV` | the only names a custom-PV formula can reach (`abs/min/max/round/math`, no builtins). Was a local in `_compute_custom_pvs_in_rows` until the pre-window seed and the export needed the same environment |
| `_TABLE_SEVERITY_FG` | severity → colour **string**. A `QColor` in a module global is destroyed after the `QApplication` and takes the interpreter down with it (0xC0000005 on exit) |
| `graph_opts["live_poll_ms"]` = 300 | how often the archive is asked. Was a literal duplicated in `_schedule_live_tick` **and** `_live_countdown_tick`, where the countdown label would silently disagree if only one were edited |
| `graph_opts["live_graph_min_ms"]` = 300 / `["live_table_ms"]` = 1500 | **two separate refresh clocks.** The graph fast path is cheap, the table rebuild re-merges the whole accumulated history (~1 s with a dozen PVs). They shared one throttle of `max(1 s, 3 × measured cost)`, so one slow table rebuild set the pace for both and the window looked ~5 s behind — the reported "refresh rate is 5 s". Each now keeps `max(floor, 2 × its own measured cost)`; costs are measured separately into `_live_graph_cost_ns` / `_live_table_cost_ns` |
| `_GRID_STYLES` | (name, matplotlib linestyle) handed out in order to the PVs that have Grid ticked, so several grids on one plot are distinguishable |
| `_XY_CMAP` | hand-built navy → purple → magenta → red ramp for the XY scatter. The stock ranges (`plasma` et al.) end in a pale yellow that is invisible on white, hiding the newest points |
| `_LIVE_BTN_OFF_STYLE` / `_LIVE_BTN_ON_STYLE` | the sidebar's one mode switch, green ⇄ orange, same padding either way so it does not resize when pressed |
| `_NS_PER_DAY` / `_mpl_epoch_num()` / `_ns_to_num()` | ns → matplotlib date numbers as one numpy division, instead of a `datetime` per sample |
| `_pairs_to_ns_arrays` / `_samples_to_ns_arrays` | samples → `(ts_ns, values)` numpy arrays; memo per list, and a Live list that only grew at the end / was trimmed at the start is converted for its new end only |
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
floating graph window; windowed it has an 8 px frame that is the resize grip, and
an APPLICATION-wide event filter turns any left press inside it into
`startSystemMove` — per-child filters would miss the canvas rebuilt on each
replot; removed again by `stop_watching()` on dock-back), `_WheelGuard` + `_install_wheel_guard()` (application
wide filter: the mouse wheel only changes a spin box / drop-down / slider that
has been **clicked (selected)**, and only while the pointer is over it; moving
the pointer away does not deselect it, selecting another control does;
otherwise the scroll is passed to the
panel underneath. `hasFocus()` alone never worked: Qt's default `WheelFocus`
gives these widgets focus from the wheel event itself, before any filter. So
the guard switches them to `StrongFocus` on `Polish` and keeps a "wheelReady"
property set by a mouse press (also on a spin box's inner line edit) or a
Tab/click FocusIn, and cleared by FocusOut (except when another window was
activated or the drop-down's own list opened). Scroll bars are left out.
The whole filter is wrapped in try/except. `_NEVER` = QTabBar and every part of a
QCalendarWidget: the wheel there always goes to the panel, never to the widget).

**In-place PV-table edits.** `_on_axis_item_changed` sends Width / Style /
Points / Size / Alpha / Y min / Y max / Autoscale / Smooth / Display Name to
`_axis_cell_in_place` (`_restyle_pv_in_place`, `_rescale_pv_in_place`,
`_resmooth_pv_in_place`, `_rename_on_graph_in_place`): artist setters +
`draw_idle`, same canvas. Only Show, and Smooth switched on/off (adds or drops
the faint raw line), fall back to `_apply_axis_settings` + full rebuild. A full
rebuild keeps a user zoom: `_plot_graph` stores the xlim in `_keep_xlim`, the
impl puts it back (with Home/Back history pushed) before the xlim callback is
connected.

### CSSLoggerWidget — state (`_init_state`)
Data: `_samples_by_pv`, `_table_rows` (+ `_table_rows_unfiltered`), `_pv_order` /
`_base_pv_order`, `_col_full_names`, `_numeric_pvs`, `_pre_window_vals`
(carry-forward value before the window start), `_pairs_cache`.

`_pre_window_vals` is not the graph's alone — it seeds `_build_table_rows`, so a
channel that did not change inside the window carries its old value in **every
row** instead of leaving an empty column, and the customs computed from those
rows stop being blank for a quiet archived window. Two rules go with that:
* the seed accepts **any** value, not only a number. It used to reuse an
  already-fetched pre-window sample only `if isinstance(v, (int, float))`, which
  silently dropped a word channel (a beam fate, a state) — invisible on the
  graph, an empty first stretch in the table.
* `_seed_custom_pv_pre_window()` (from `_rebuild_custom_pvs`, before the row
  compute) evaluates each formula on its sources' pre-window values and stores
  the result under the custom channel's own name, so a derived trace is held
  across the window's left edge like a real one. It runs in definition order, so
  a formula reading another custom picks up the value seeded just above it.

A condition on a channel with **no sample inside the window** is skipped
(`_has_window_data`; a custom PV counts when one of its sources has samples),
even though the rows carry its held pre-window value. Judging that held value
emptied a whole month of 2025 at the end of the load (PTM1/PCM4/PAP1 have no
samples there; the pictures during the load, which have no held values yet,
had shown the data). `_cond_skipped` lists them; the plot note says
"Not applied (no data in this period): …", and `_seed_rejected` ignores them,
so no look-back starts. A channel WITH samples in the window that sit outside
the range (laser off) still empties the table — `_log_conditions_diag` says so.

The Log was for a long time the ONLY place that said it. On screen the table
read "No rows." and the XY tab read "neither channel has values in this
window", which is simply false — the channels had thousands of samples, the
conditions threw the rows away. `_apply_conditions_to_rows` now records
`_cond_drop_counts = (rows_in, rows_out)`, and `_no_rows_message()` turns that
into the sentence both places show, in dark ink (`_set_xy_info(warn=True)` and
the same colour on `_lbl_table_info`) because with an empty grid that line is
the whole answer. `_xy_empty_message(n_rows)` keeps the old wording for the one
case it was ever right about: rows on the table, but the two chosen channels
silent.
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
`_sel_live` — the selection was dragged out past the right-hand edge (within
`max(8 px, 1 %)` of the visible xmax, because the `SpanSelector` freezes at the
last position the mouse had inside the axes) while the view shows the newest
data. `_follow_live_selection()`, called at the top of `_recompute_stats`, then
puts the end ON the newest real sample (`_newest_sample_num()` — from
`_samples_by_pv`, not `_graph_raw_np`, whose arrays are padded out to "now") and
moves the selector band; the stats title says "end follows the newest point".
With no sample after the start yet, the end follows the clock instead.
`_sel_samples` / `_sel_pre` — a live selection's own untrimmed copy of the
samples since its start (and each channel's value just before it, for the
conditions). `_sel_sync()` copies new samples into it in the live tick BEFORE
the window trim and on every `_recompute_stats`, so a live selection keeps
counting after its start is older than `_live_window_span`. Any selection made
while Live is on (fixed ones too) uses this copy; out of Live a fixed selection
reads `_samples_by_pv` + `_pre_window_vals` directly. **Never `_graph_raw_np`**:
that is the screen-thinned trace (bins whose edges move every live tick, plus
held values from other channels' rows) and made N wander 709 → 710 → 709.
Each card = the PV's own samples in the region, filtered by
`_stats_keep_mask(ts, tables, sbp, pre)` = conditions (`_condition_mask`) +
"Keep steps every" (`_master_multiple_mask`, master read by `_held_at`) +
the dead-shot rule (`_dead_shot_mask`). Min/max take `_minmax_by_pv` of the
kept samples. Every graph PV always gets a card — N 0 and dashes when
the region has no shot.

Shot count (16.10.2026): the heading is `_set_stats_title` — start → end,
`_fmt_span_full` (d / h / min / s), and `_sel_shot_count`. Shots come from
`cpva_core.find_shots(ts_by_pv)` = `shot_rate` (the median own gap most real
channels share, groups within ×1.25, tie → slower; members = channels on that
rate) + `shot_moments` (member writes within 0.4 × gap are one moment; a shot
when ≥ ceil(members / 2) wrote; returns first and last write). Customs never
vote (`_shot_ts_by_pv` takes `_base_pv_order` only). The count is the shot
starts inside the region, filtered by `_stats_keep_mask` at the shot ENDS
(where the shot's energy is archived). `_window_shots()` memoises the loaded
window's shots by each list's identity/length/ends; a live selection runs
`find_shots` on `_sel_samples` with the window's rate as fallback.
Custom PVs: `_rebuild_custom_pvs` still computes them into the table rows
(`_compute_custom_pvs_in_rows`, stores `_cpv_last_plan`), but the TRACE
(`_samples_by_pv[name]`) is `_custom_series_on_shots` — one value per shot,
each source `_held_column` at the shot end. No shot rate → the row grid as
before. Live between rebuilds: `_extend_custom_on_new_shots` (the rate from
`_window_shots_memo`).
Exact numbers from a thinned load: `_load_thinned` (Detail > 0 and window
> 1 h, set in `__on_load_finished_inner` from `_load_avg_target`) makes
`_recompute_stats` ask `_sel_raw_covering`; missing → `_request_sel_raw`
(300 ms debounce) → `_start_sel_raw_read`: raw `cpva_fetch_many_adaptive`
per `_export_slices` 2 h slice, kept as `_SampleArrays` (two float64 arrays,
read by `_samples_to_ns_arrays` / `_held_column`), cap `_SEL_RAW_MAX_SAMPLES`.
Cards show "…" (`_show_stats_reading`) until `_on_sel_raw_loaded` stores
`_sel_raw` {a, b, epoch, pvs, samples, pre, shots}; customs are computed on
its shots in `_sel_raw_covering`. `_condition_tables(minmax={})` for raw.
A failed read → `_sel_raw_failed`, thinned numbers with "⚠ …" in the heading.
Live late arrivals: every `_LIVE_BACKFILL_EVERY_NS` a tick reaches
`_LIVE_BACKFILL_NS` back; samples older than a PV's own last that it does not
hold go through `_merge_late_live_samples` (insort into `_samples_by_pv` and
`_sel_samples`, rows marked stale).

Dead shots: sidebar "Leave dead shots out of statistics" (config
`dead_shot_skip`, default on; `dead_shot_energy_pv`, default SBW4 HAPLS name;
`dead_shot_energy_max`, default 0.05). A sample is dead when the energy reading
of its shot (newest energy sample ≤ t + merge gap and ≥ t − merge gap) is below
the limit AND the master PV (waveplate) held at that energy sample equals its
value at the previous energy sample. Statistics only — the graph still draws it.

Pause restart (sidebar "Restart after pause >", config `sel_pause_restart` /
`sel_pause_s`): shots = `_shot_times()` (condition-passing samples of the
condition PVs, or without conditions the `_export_shot_channels` graph PVs, all
through `_stats_keep_mask`); `_last_pause()` finds the newest gap longer than
the set seconds. Live: `_sel_restart_after_pause()` moves `_sel_range`'s start.
Fixed: `_fixed_restart_after_pause()` only moves the counting start and writes
`_sel_fixed_restart_note` into the title — the band stays.

Selection edges: `_on_span_select` → `_snap_sel_edges` puts an edge on the
first shot after / last shot before a pause when let go within 8 px and no
other shot lies between (pause = ≥ 2 s, ≥ 3 px and ≥ 3 × median shot spacing;
evenly spaced data never snaps). Shots for this are `_all_shot_times_ns()` =
(first, last) sample of each merge-gap group. Start / End boxes in the stats
heading (`_sel_edge_edits`, filled by `_sync_sel_edge_edits`): typed time →
`_on_sel_edge_typed`, -1 / +1 → `_step_sel_edge`, both via
`_set_selection_edges`.

Conditions: `_conditions` is the list that actually filters, already empty
when the sidebar "Apply conditions" box (`_conditions_on`, config
`conditions_enabled`) is off; `_conditions_all` keeps every row. Paths that read
samples rather than `_table_rows` filter with `_condition_tables()` +
`_condition_mask(ts, tables)` — each condition channel read at
`t + SAMPLE_HOLD_MIN_GAP_MS`, like the row merge: `_update_graph_data` (live),
`_pv_stats`, live selection stats. `_hold_to_window` replaces the pre-window
value with the last good shot's (`_last_good_value`) when `_pre_window_ok()`
fails.

Thinned points are judged by their **range**, not their mean
(`_cond_range_passes`; rows via `_rows_condition_mask`, samples via the
`c_lo`/`c_hi` columns of `_condition_tables`, both from `_minmax_by_pv`): a
"between" passes when the bin's min..max reaches the range, "outside" when it
leaves it; a raw point (lo = hi) is judged exactly as before. A 10-min bin
holding a few good shots among laser-off ones has a mean outside the range, and
the one day with the event vanished from a long window (12.10.2026). The band
(`_thin_trace`) and `_pv_stats` min/max use the archiver's min/max of the kept
bins, conditions on or off. Zoom in for shot-by-shot judgement: the zoom detail
reads the condition channels over the same range and runs every trace through
`_condition_mask` (≤ 1.1 h is raw, so exact). The zoom detail is stopped only by
"Keep steps every" when it really acts (`_step_filter_applies`: a number AND
the master PV has a value — its default "1000" used to switch the detail off
for everyone) and by a condition on a custom PV.

Last good shot: after every load, live start and Conditions change,
`_maybe_hunt_last_good()` checks `_seed_rejected()` and, if the Conditions
reject the window's seed, runs `_last_passing_moment` (module level; condition
channels only, raw, `_LAST_PASS_STEP_NS` = 6 h steps back to
`_LAST_PASS_DEPTH_NS` = 14 days) in a thread, then reads every real PV at that
moment with `cpva_fetch_last_before_many`. `_last_good` = {conds, before, ts,
vals (custom PVs via `_customs_from`), hunting, failed}; `_lg_epoch` cancels a
stale hunt. `_last_good_note()` puts the yellow note on the plot while the
window has no rows of its own.

Grid: `_apply_grids(pvs, axes)` draws the per-PV grids; the Grid column and
"Hide all grids" call `_regrid_in_place()` — no `_plot_graph`, view kept. The
Grid tick then rewrites only that column's tooltips (`_refresh_grid_tooltips`):
a full `_refresh_axis_settings_tv` recomputed every PV's stats and re-sized the
PV table, which resized and redrew the graph.
Reset by a new drag and by `_clear_selection`. The statistics selector is
`button=1`: without it matplotlib takes every button, so the right-drag zoom
also started a selection.
There is **no** `_zoom_history` / `_xy_zoom_history` any more: the view history
belongs to the toolbars (Home / Back / Forward) and a second hand-kept one would
drift out of step. `_on_zoom_select` pushes onto the toolbar's stack via
`push_current()`, so a right-drag zoom is undone by the same Back. `_user_zoomed`
only records "the user is looking somewhere of their own choosing", which stops
the live window scrolling the view out from under them.
XY: `_xy_figure`, `_xy_rows`, `_xy_scatter`, `_xy_rect_selector`, `_xy_toolbar`,
`_xy_choice_map`.
PV Time: `_pv_time_canvas` / `_pv_time_figure` / `_pv_time_toolbar`,
`_pv_time_days` (picked dates), `_pv_time_cache` (see above),
`_pv_time_choice_map`, `_pv_time_condition_rows`, `_pv_time_sig`,
`_pv_time_epoch`, `_pv_time_busy`.
Automatic loading: `_load_in_flight`, `_reload_pending`,
`_pending_reload_reason`, `_autoload_timer` (400 ms debounce).
Live: `_live_mode`, `_live_last_ts`, `_live_window_span`, `_live_last_rebuild_ns`
(table) + `_live_last_graph_ns` (graph), `_live_table_cost_ns` +
`_live_graph_cost_ns`, `_live_stopped_by_zoom` (a zoom ended Live — right-drag
via `_on_zoom_select`, toolbar Zoom/Pan drag via `_on_graph_tb_press` /
`_on_graph_tb_release`; `_live_off_by_zoom` fixes From/To to the shown window;
Home restarts Live), `_live_timer` + `_countdown_timer`,
`_last_render_s` (every graph render is timed by a wrapper put on
`canvas.draw` in `_plot_graph_impl`; the graph clock is 3 × (refresh + render),
and with no new data `_live_pixel_ns`; no separate time-axis slide between
refreshes; graph skipped while its tab is hidden, table cells skipped while
the table is hidden — `_populate_table_if_seen` / `_table_unwritten` /
`_on_table_shown` via `_ShowWatch`),
`_fetched_pvs` (what the window really holds: `_fetch_pv_names` = Show ticked
+ Conditions channels + sources of shown custom PVs + master PV; used by the
load worker, `_live_initial_load`, `_live_tick`, the selection raw read and the
last-good hunt; `_sync_fetched_to_show` drops a hidden PV's samples,
`_catch_up_shown_pvs` / `_after_catch_up` read a PV shown again),
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
| load | `_on_load_clicked(silent=)`, `_on_load_error`, `_on_load_finished` / `__on_load_finished_inner`, `_build_table_rows(samples, order, pre_vals=)` |
| automatic loading | `_request_reload(delay_ms, reason)`, `_do_auto_reload`, `_finish_load`. **There is no LOAD DATA button.** Everything that changes what should be on screen calls `_request_reload`: PV added / removed / cleared / renamed, a new time window, a new preset. A burst collapses into one fetch via `_autoload_timer`; a request made while `_load_in_flight` sets `_reload_pending` and runs from `_finish_load`. Every load end path **must** call `_finish_load()` — including the "live was switched off meanwhile" early returns in `_on_live_init_error` / `_after_live_initial_load`, or the lock stays held and no automatic reload can ever start again |
| graph | `_plot_graph` (+ `_schedule_replot`), `_plot_graph_impl`, `_update_graph_data`, `_band_ylim`, `_apply_x_ticks`, `_retick_from_current_xlim`, `_compute_x_ticks`, `_bottom_margin_floor`, `_keep_x_label_visible`, `_grid_style_for` / `_grid_style_name` / `_grid_style_index`, `_apply_font_size`, `_clear_graph`, `_clean_graph`, `_save_graph_image` / `_write_graph_image` / `_add_stats_table_to_figure` (stats drawn as figure text BELOW y=0, taken in by `bbox_inches="tight"`, removed again in `finally`), `_graph_popout` / `_restore_graph_from_popup`, `_install_graph_shortcuts` |
| graph toolbar | `_build_graph_strip` (permanent grouped icon bar `_graph_strip`, buttons in `_gstrip_btns`, icons from module `_graph_icon` / `_GICON_RECIPES`), `_graph_tb_action` / `_graph_tb_trigger` (strip buttons fire the HIDDEN matplotlib toolbar's actions in `_graph_tb_holder`), `_sync_graph_strip`, `_on_graph_strip_mode`, `_install_graph_toolbar` (re-presses Pan/Zoom from `_graph_tb_mode_wanted` after a rebuild), `_install_xy_toolbar`, `_sync_graph_interaction_mode`, `_on_graph_view_home`, `_push_graph_view`, `_on_xy_rect_zoom_push`, `_open_graph_view_menu` |
| loupe | `_set_loupe_on`, `_set_loupe_mag`, `_update_loupe` (called from `_process_mouse_move_impl`; hidden on `figure_leave_event`); class `_GraphLoupe` — child of `_graph_container`, mouse-transparent, own small Figure, shows ONE trace (`nearest_trace`: smallest vertical pixel distance at the mouse time), box = loupe size / magnification in screen pixels around the mouse mapped through that trace's own axis; rebuilt when the main figure or the chosen line changes |
| margins | `_open_margins_dialog` → non-modal `_MarginsDialog` (label_pad_px, axis_gap_px, outer_margin_px, margins, band_pad_frac → `_apply_graph_opts` on every change). Replaces matplotlib's Subplots dialog |
| PV table order | `_plot_order` (config `plot_order`), `_ordered_for_plot(pvs)` used by `_refresh_axis_settings_tv` and `_plot_graph_impl`; `_RowDragger` (hand-made row drag on `_axis_tv`'s viewport) → `_on_axis_rows_dropped`. The left PV list keeps its own order |
| cursor | `_on_canvas_draw`, `_on_graph_mouse_move`, `_process_mouse_move(_impl)`, `_flush_cursor_table` |
| stats / selection | `_on_span_select` (stores `_sel_range`, then delegates), `_recompute_stats`, `_restore_selection_band`, `_clear_selection`, `_drop_selection_if_outside`, `_make_stat_card`, `_copy_stats_text`, `_clear_stats`, `_on_zoom_select`, `_fit_stats_strip` (strip height = `_FlowLayout.heightForWidth` / `first_row_height`, capped at 45 % of the graph pane — but two rows of cards are always shown whole, up to 70 % (`rows_height(w, 2)`); re-run by `_ResizeHook` on the pane, and once more on a 0 ms timer whenever the strip was hidden, because cards measured before their first show lack their small fonts and measure too tall — the old empty band under them), `_set_stats_mode` (all / row / hide, config `stats_strip_mode`); `_stats_rows` = what the cards show, read by the image save. `_FlowLayout` wraps when `x + w > inner.x() + inner.width()` (a card ending exactly on the edge fits). Save: `_save_graph_image` proposes `CSS Logger <view start> to <end> (saved <now>).png`; `_write_graph_image` always adds `_add_header_to_figure` (period, save time, PV names in their colours, ABOVE y=1) and with a selection `_add_stats_table_to_figure` |
| graph options | `_open_graph_settings_dialog`, `_apply_graph_opts`, `_avg_target_points`, `_on_avg_target_changed` |
| live | `_toggle_live_mode`, `_live_span_from_window`, `_live_initial_load`, `_on_live_init_error`, `_after_live_initial_load`, `_live_tick`, `_on_incremental_finished`, `_schedule_live_tick`, `_live_countdown_tick`, `_live_poll_ms`, `_scroll_live_time_axis`, `_stop_live`, `_maybe_autostart_live` |
| filtering | `_apply_conditions_to_rows`, `_log_conditions_diag`, `_row_matches_conditions`, `_condition_value_ok`, `_get_master_pv`, `_get_master_multiple`, `_remove_master_only_rows`, `_remove_fake_hour_boundary_rows`, `_filter_master_multiple_rows`, `_no_rows_message` |
| custom PVs | `_col_letter`, `_channel_letters`, `_cpv_dialog_channels`, `_migrate_custom_pv_bindings`, `_cpv_plan`, `_compute_custom_pvs_in_rows`, `_seed_custom_pv_pre_window`, `_emit_custom_pv_diag`, `_rebuild_custom_pvs`, `_custom_pv_tooltip`, `_open_custom_pv_dialog` |
| table | `_populate_table`, `_set_table_cell`, `_format_value`, `_on_table_scroll`, `_on_table_context_menu`, `_on_table_double_click`, `_try_open_image_at_row` |
| axis settings | `_refresh_axis_settings_tv`, `_autosize_axis_pane` (the PV list is exactly as tall as the PVs it holds, capped so the graph keeps `_AXIS_PANE_MIN_GRAPH` px and the buttons stay on screen), `_on_axis_tv_double_click`, `_on_axis_tv_clicked`, `_on_axis_color_changed`, `_on_axis_item_changed`, `_apply_axis_settings`, `_get_pv_default_settings`, `_pv_style_kwargs`, `_pv_stats`, `_flush_axis_measured`, `_resync_pv_colors`, `_safe_float` |
| columns / looks | `_apply_default_axis_columns`, `_reset_axis_columns`, `_open_axis_column_menu`, `_build_styles_menu`, `_fill_styles_menu`, `_current_style_payload`, `_apply_style_payload`, `_save_style_preset`, `_load_style_preset`, `_delete_style_preset`, `_export_style_preset`, `_import_style_preset` |
| reference lines | `_open_ref_lines_dialog`, `_graph_ref_pv_choices`, `_ref_axis_for`, `_draw_ref_lines`, `_run_ref_pick`, `_set_ref_pick_step`, `_clear_ref_pick_dim`, `_end_ref_pick`, `_cancel_ref_pick`, `_on_ref_pick_click`, `_on_ref_pick_key`, `_pv_at_click` |
| XY | `_refresh_xy_choices`, `_on_xy_axis_changed`, `_plot_xy(_impl)`, `_xy_pairs`, `_on_xy_rect_select`, `_clean_xy`, `_clear_xy_plot`, `_set_xy_info`, `_xy_empty_message` |
| PV Time | `_plot_pv_time(_impl)`, `_recent_workdays`, `_pick_pv_time_days`, `_refresh_pv_time_days_label`, `_refresh_pv_time_choices`, `_pv_time_add_condition_row`, `_pv_time_remove_condition_row`, `_pv_time_conditions`, `_pv_time_resolve`, `_pv_time_fetch`, `_pv_time_pv_arrays`, `_pv_time_trim_cache`, `_pv_time_finish`, `_on_pv_time_error`, `_pv_time_day_columns`, `_pv_time_day_values`, `_pv_time_draw`, `_draw_daily_distribution`, `_draw_pv_time_raw`, `_install_pv_time_toolbar`, `_clear_pv_time_plot` |
| PV list / presets | `_open_pv_browser`, `_remove_selected_pvs`, `_clear_pv_list`, `_on_pv_double_click`, `_real_pv_names`, `_sync_pv_list_customs`, `_update_pv_count`, `_refresh_preset_combo`, `_load_preset`, `_save_preset`, `_save_preset_as`, `_delete_preset` |
| export | `_open_export_dialog`, `_export_shot_channels`, `_export_rows_on_screen`, `_export_from_archive` (+ the module helpers `_export_slices`, `_export_slice_rows`, `_export_eval`, `_export_row_ok`, `_export_fmt`) |
| misc | `_open_time_window_dialog`, `_refresh_time_labels`, `_open_conditions_dialog`, `_open_ref_lines_dialog`, `_col_label`, `_table_pvs`, `_save_runtime_state`, `_log`, `_clear_log`, `_update_status` |

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
follows its logical column wherever it has been dragged. While a heading is
being dragged, `_HeaderDropMarker` (an event filter on the header viewport that
never consumes an event) draws a 3 px blue line on the edge where the column
will land, using Qt's own target rule from `QHeaderView::mouseMoveEvent`.

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

`_fill_styles_menu` traps: the Load / Delete sub-menus are built as
`QMenu(title, menu)` and kept on `menu._style_submenus` — `menu.addMenu("Load")`
returns a PySide wrapper that destroys the sub-menu when the function returns,
which made Delete open nothing. Every entry that opens a dialog runs via
`QTimer.singleShot(0, …)`, after the popup has released the mouse.

### Graph traps — read before touching the plot

**The figure is laid out for the canvas's REAL size.** The px gaps of the Y
columns (Margins and spacing) become fractions of the figure width, and
matplotlib keeps fractions when the canvas stretches. The figure used to be
built at `max(container − 8, 960)` px, so on any graph narrower than ~968 px
every gap came out smaller than set ("spacing does not work"). Now it is the
container size (960×480 only while the container is not laid out yet), kept
in `_graph_layout_w`; `_keep_x_label_visible` (canvas `resize_event`)
schedules one replot when the canvas width moves > 3 % away from it.
"Gap inside band" also pads Autoscale'd PVs (`_band_ylim`, same screen size as
a band's gap). The Graph settings dialog's fallbacks are
`_GRAPH_OPTS_DEFAULTS`, not their own numbers.

**Live with no shot yet must not rebuild every tick.** The live graph tick used
to call `_plot_graph()` whenever `_graph_lines` was empty — i.e. on every tick
before the first shot — which destroyed the selector under the mouse, so a
selection could not be dragged until shots arrived. With nothing numeric yet
it now only slides the time axis (`_scroll_live_time_axis`) and refreshes the
stats; `_plot_graph_impl` also draws empty axes in Live when `_samples_by_pv`
is still empty.

**The time-axis stamps are a FIXED LIST, so they must be rebuilt after every
change of the visible range.** `_apply_x_ticks` is the only place that installs
them, and `_retick_from_current_xlim` re-runs it for whatever is on screen. It is
called from the full replot, `_update_graph_data`, `_on_zoom_select` and the
toolbar's Home / Back / Forward. Miss one and the axis keeps the *old* window's
positions: a narrow zoom then shows **zero** timestamps (measured — that was the
bug). The live path already had its own copy of this fix; the zoom path did not.

**Both window edges are always stamped, so an interior stamp too close to one
is the same instant written twice.** `_compute_x_ticks` demanded only a quarter
of a step of clearance (`_CLEAR`, now **0.6**). At a six-hour step over two days
a quarter step is 3 % of the width, so the pair at the right-hand edge always
collided — that was the "the stamp on the right is written over the last value"
report. Two more parts of the same fix:
- the outer two labels are pinned inside the plot (`set_horizontalalignment`
  "left" / "right"); a stamp centred on the right spine hung half of itself off
  the figure, and `margin_right` is only 1.5 %;
- `_STEPS` runs out to a year. It used to stop at two days, so a week asked for
  ~85 stamps and a year for ~180, all on top of each other.

**The stamps are two lines, date above time, and the labels are computed once.**
`_apply_x_ticks` builds a `{tick position: text}` map in tick order and the
formatter only looks it up — matplotlib calls the formatter repeatedly and not
necessarily left to right, so deciding "is this a new day?" inside it made the
dates come and go between redraws. The date is printed on the first stamp of
each day; above 120 days it carries the year (a year-long period otherwise opens
and closes on the same "09-02"); when the step is a whole day or more the stamps
are dates and only the two window edges also say the time, or an edge and the
midnight beside it read as one stamp twice. `x_time_format = "auto"` is now
genuinely automatic — it drops the seconds at whole-minute steps and the clock
at whole-day steps. It used to be a synonym for `hms`, which is why a two-day
view printed eight-glyph `06:00:00` stamps. `hms` / `hm` still override it.
Verify by **rendering**: `testing/probe_x_axis_long.py` measures the gap between
stamps in pixels for six periods and saves a PNG of each.

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
with the axis pane in the F11 pop-out. (There is no `_graph_ctrl_bar` any
more — its controls are in the sidebar, see **Sidebar**.)

**Toolbar icons: matplotlib tints them ONCE, AT CONSTRUCTION, and only when it
thinks the palette is dark** — on a dark Windows theme they come out near-white
and read as blank buttons, and fixing the palette afterwards does nothing. Build
every matplotlib toolbar through `_make_mpl_toolbar`, which parents it to a
light-palette host first. (Pulser Monitor and Image Tools carry the same helper
for the same reason.) `_CustomToolbar`, `_TB_STYLE` and `_TB_HINTS`
are imported from `sp_t` in this folder, which `main.py`
already imports `SpectraWidget` from, so this adds no new coupling; the imports
are wrapped so a change there degrades to the stock toolbar instead of breaking
startup.

**Right-drag on the canvas is the zoom, so the canvas must not get a context
menu** — it would swallow the drag. Those entries live on the "View ▾" button
(`_open_graph_view_menu`).

**A hand-made zoom must push BOTH views onto the toolbar's history.** matplotlib
enables Back and Forward purely from where the toolbar sits in `_nav_stack`
(`backend_qt.py: set_history_buttons`), and its own `release_zoom` pushes after
applying the new limits as well as before. `_on_zoom_select` (graph) and
`_on_xy_rect_select` (XY) used to push only the view being LEFT, so the stack
held one entry, `_pos == 0`, and both buttons stayed greyed out — reported as
"the back button does nothing after I zoomed with the magnifier". Both now call
their push helper (`_push_graph_view` / `_xy_push_view`) on each side of the
`set_xlim` / `set_ylim`, and `_plot_xy_impl` records the opening view right
after the first `canvas.draw()` so Home and Back have a destination from the
very first gesture.

### XY tab — it draws from `_table_rows`, so it has to be told when they change
The scatter is built once by `_plot_xy_impl`; `_xy_update_points` then refreshes
that same `PathCollection` in place. Rebuilding the figure per tick would throw
away the toolbar history and the user's view several times a second.

| Piece | Role |
|-------|------|
| `_xy_scatter`, `_xy_plotted` | the collection on screen and the `(x_label, y_label, x_pv, y_pv)` it was drawn from — a live refresh whose combos have moved on falls back to the full `_plot_xy` |
| `_xy_dirty` | the visible tab is redrawn, a hidden one is only marked; `_on_main_tab_changed` catches it up on the way in |
| `_xy_user_zoomed` | set by `_on_xy_rect_select` and by the toolbar's Zoom/Pan, cleared by Home. While set, a refresh adds points but leaves the axes alone |
| `_xy_fit_axes` | **`ax.relim()` is no use here** — it ignores collections, and a scatter is a collection. The limits are taken from the points, with 5 % air |
| `_xy_dot_size(n)` | the count → dot-area ladder, shared by the first plot and every refresh, so a cloud that grows past a threshold thins out the same way a fresh one would |
| `_xy_pairs` | **same shot only, nothing held.** Reads `_samples_by_pv` (not the rows) on the `_window_shots()` grid (fallback `_xy_own_moments`: X∪Y writes merged within `SAMPLE_HOLD_MIN_GAP_MS`). `_xy_value_per_shot` takes a channel's last sample from one gap before a shot's first write to one gap after its last. Filtered by `_stats_keep_mask` (Conditions, master multiples, dead shots). The old row-based hold-forward paired values from different shots (22.10.2026: LP-level SBW4 against full-power PTM1) |
| `_xy_info` | caption; when `_sel_needs_raw()` (window loaded thinned) it warns in dark ink that the points are 1-minute archive averages — by user decision the XY tab does not re-read raw |

Callers that must refresh it, all of them places that rebuild `_table_rows`:
`_on_incremental_finished` (the live table branch), `_open_conditions_dialog`
and the custom-PV dialog. The Conditions button is on the Graph, XY and Table
tabs and is one handler — before, pressing it *from the XY tab* repainted
everything except the plot being looked at.

### `_internal` never reaches the share, so a `.py` loaded by path is not safe there
`Dev Tools/dt_core.py::build_one_project` ships every sibling `.py` as `--add-data`, which lands in the
build's own `_internal`. That folder never gets to the share. Two separate steps
see to it: `Dev Tools/dt_core.py::deploy_one_program` skips `_internal` entirely when it copies a
program, and its "Deploy Libraries" button fills the destination's `_internal`
from ONE shared runtime library built from `Internal Builder`, **deleting every
file that library does not have** ("Remove files that no longer exist in the new
_internal"). So neither pressing that button nor not pressing it can put
`daypicker.py` there. The local build ran; the network copy could not find the
file. Measured: `dist/CSS Logger/v2.1.8/_internal/daypicker.py` exists,
`dist/_internal_builder/_internal/daypicker.py` does not.
Two things fix it and both are needed: `daypicker` in `build_config.json` →
`hidden_imports` (compiled into the exe, where nothing can replace it), and
`_import_daypicker` searching the exe's own modules, `__file__`'s folder,
`sys._MEIPASS` and the folder beside `sys.executable` (the deploy does copy the
loose `.py` there) before it gives up — and naming every path it tried when it
does. Same loader, same reason, in `Image Tools/is_t.py`, `Image Tools/if_t.py`
and `SPFE Values/spfe_t.py`.

---

## cpva_core.py — non-UI helpers

### Constants
- `CPVA_BASE_URL` — `https://10.78.0.57:8443/api/1.0/cpva` (+ `/samples`,
  `/channels` endpoints), `CPVA_HTTP_TIMEOUT` = 10 s, SSL verification off
- `L3_PROXY_BASE_URLS`, `CPVA_DOWN_S` — the backup. `_get_json_or_backup` asks
  the L3 proxy (.64, then .65; same Cassandra, same rows and `count=`
  decimation) only when CPVA cannot be reached (`requests.ConnectionError`, HTTP
  502/503/504), then skips CPVA for 60 s. Never on a read timeout or HTTP 500, so
  splitting is unchanged; the proxy's own 500 still splits; both gone is one
  `ConnectionError` naming both (not split). Spectra's `_cpva_fetch` and channel
  listing go through it too. `BACKUP_STATS`; test `testing/test_cpva_backup.py`
- `EXTRA_ARCHIVES` — L3BT and FCT proxies. `archive_of` sends a channel there
  when that proxy lists it and CPVA does not (`_load_lists`, held per session);
  `cpva_fetch_channels` / `extra_channel_names` append their names to the picker
  list and refresh the routing lists. Both proxies honour `count=`
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
| `cpva_fetch_many_adaptive(channels, start_ns, end_ns, count=…)` | **the one fetch.** Asks for as much time per request as the archiver will actually serve and halves only what it refuses, so any period loads — an hour, a year, a night. Returns `(results, errors, FetchReport)`. See "Adaptive fetch" below |
| `cpva_fetch_samples_chunked(...)` | one channel through the adaptive fetch. No longer raises on a partial failure: it used to re-raise the first failing hour and throw the whole channel away |
| `cpva_fetch_many_chunked(channels, …)` / `cpva_fetch_many_optimized(...)` | two-value shims over `cpva_fetch_many_adaptive` (raw / decimated), for callers that do not need the report. Both take `cancel_fn` — stopping a QTimer cannot stop a running pool, so "Stop Live" used to leave the whole queue hammering the archiver and holding the GIL; a cancelled pool drops everything it has not started (`_is_cancelled`) |
| `cpva_fetch_last_before(channel, before_ns, deadline=)` | carry-forward value before the window; scans expanding rings (`_LAST_BEFORE_STEPS_S`, up to ~30 days), each ring hunted newest-half-first by `_last_before_in_range`. **Bounded** (`_HuntBudget`): a ring is halved only while longer than `_LAST_BEFORE_MIN_SPLIT_NS` (6 h), at most `LAST_BEFORE_MAX_REQUESTS` (12) per name, all within `LAST_BEFORE_DEADLINE_S` (20 s). It used to halve to one minute at up to 120 s a request — the "stuck at 92 %" of 09.10.2026 |
| `cpva_fetch_last_before_many(channels, before_ns, …, deadline_s=)` | the same for many channels in one pool; returns only channels that had a prior sample. Waits with `wait(FIRST_COMPLETED, 0.25 s)` so a cancel or the deadline ends it at once, and abandons the pool (`shutdown(wait=False)`) — the old `with` block waited for every running hunt even after a cancel |
| `read_order(ch)` / `channel_aliases(ch)` / `_holds_nothing` / `_drop_after` | the two-name rule (see "SBW4 is archived under two names") |
| `_http_get_json` | every request passes `_IN_FLIGHT`, a program-wide semaphore of `MAX_REQUESTS_IN_FLIGHT` (24): several setting tabs loading at once share it |
| `cpva_decode_value(sample)` | decodes a sample value (numeric / string / enum) |
| `cpva_fetch_channels()` | full list of archiver channels |
| `now_ns()` / `dt_to_ns()` / `ns_to_local_str()` / `_fmt_cursor_value()` | time + value formatting |
| `parse_user_datetime(s)` | user date/time input (ISO + European format) |
| `shorten_pv_name(full_name)` | shortens a PV name for the UI (`_STRIP_PATTERNS`). A noise segment is only dropped when it is a WHOLE dash/underscore segment: without the boundary guards the `IN` alternative also matched inside a word, so `L3-TIMING-TIMING:SysRate` was labelled `L3-TIM_G-TIM_G - SysRate` — on the graph, in the table header and in the exported file. Any run of separators is then collapsed, not only underscores, or a dropped segment leaves its own `_` beside the dash that separated it (`PFM8-`) |
| `make_pv_query_matcher(q)` | compiles a query into a predicate: every whitespace token must appear somewhere, **in any order** (`023 l3` == `l3 023`), explicit `*` / `?` inside a token still work. Compiled once per query, then run over ~10 000 channels per keystroke |
| `_matches_wildcard()` | thin wrapper over `make_pv_query_matcher()` (backwards compatibility) |
| `_open_path()` / `_looks_like_image_path()` / `_image_file_size()` / `_resolve_image_path()` | image helpers |
| `safe_divide(a, b)` | element-wise division with NaN for a zero/infinite denominator |

### Adaptive fetch (`cpva_fetch_many_adaptive`)

The archiver answers **HTTP 500** when one response would carry too many
samples. Two shapes were wrong before:

* one request per signal over the whole period (`avg_target_points > 0`, the
  default) — a single 500 lost the entire signal, which is why nothing much
  beyond half a day would load at all, and it looked like an empty archive;
* a rigid one-hour grid — a year needs ~70 000 requests.

Plus `_chunk_is_night` **silently dropped** every hour lying wholly inside
22:00-06:00 Prague, so any period over a night came back holed, with no error
and no log line. It is gone.

| Piece | What it does |
|-------|--------------|
| `_SpanOracle` | per channel, the widest span known to work and the narrowest known to fail; `plan_span()` picks the next size. **One oracle per channel**: how many samples an hour holds is a property of the signal, so a shared one would drag a slow signal (a valve state) down to the fastest signal's size and turn a dozen requests into thousands. A shared oracle is also kept, consulted only until a channel has a success of its own |
| steps (`_load_tiles`) | the period is cut into whole Prague days, **newest first**; above `MAX_TILES` (16) days neighbouring days are grouped (from the newest end), so three months is 15 steps of 6 days. Measured 12.10.2026: one day per request cost 767 requests for three months of the Operation preset against 112 — the archiver's cost is per request. Steps are FED lazily (`_feed_tile`, whenever fewer than `max_workers` requests are out), every channel of a step before the next one, so a long load fills in a step at a time from the right and a cancel leaves little queued. `tile_fn(edge)` reports the newest-first prefix of complete steps (the grey area's edge). Within a step a channel's first request is the whole step (`_piece_for`: thinned or sparse), or what the oracle already knows works (raw dense: from `RAW_START_CHUNK_NS`) |
| split, not recurse | a refused range is re-queued as *k* smaller ranges in the **same** pool. Recursing inside the worker (the `sp_t.py` pattern) would serialise a 30-day → 720-piece split in one thread while fifteen idle |
| `_is_splittable_error` | only 413/414/5xx, a read timeout or a bad shape justify halving. A refused connection gets one retry and is then recorded — otherwise one unreachable archiver becomes half a million requests |
| `count` sharing | the decimation target is split across the requests in proportion to their span (floor `MIN_CHUNK_COUNT`), so the total stays near the target instead of being multiplied by the request count |
| merge | by sample **`time`**, not request order: an adaptively split child's anchor sample can predate its parent's start, and `cpva_fetch_last_before` needs the last element to be the newest. Duplicates (every request also returns the sample before its own start) are dropped |
| `FetchReport` | `gaps` (per channel, the ranges that could **not** be read), `boundaries` (the request starts actually used — `_remove_fake_hour_boundary_rows` needs them), `requests`, `splits`, `cancelled`, `over_budget`, `decimated`, `elapsed_s`, `rechecked`, `cached` (channel-days served from the day cache) |
| `progress_fn(done, total)` | `total` only ever **grows**: it starts at steps × channels and grows with every split, pre-split piece, extra day of a two-name channel or second-name request. `done` never falls, `done <= total`, and the last call is level. Emission is serialised (`emit_lock`) — snapshotting outside it let two threads report out of order and the bar jumped backwards |
| `_timeout_for_span` | 1 h → 10 s, ≥ 12 h → 120 s (`CPVA_HTTP_TIMEOUT` … `CPVA_HTTP_TIMEOUT_LONG`). The `http_timeout` config key finally does something — `CSSLoggerWidget._http_timeout()` reads it as the **base** and the per-request value is derived from the span |
| pre-flight | for periods over 4 h, one 1-minute request at the base timeout, so an unreachable archiver is known in 10 s rather than after a 120 s hang. It runs in its own thread BESIDE the density check and is joined before the first step is fed (on a busy archiver the two cost seconds each, one after the other). Only a failure that got **no HTTP reply at all** counts as dead — a 500 or a 404 means it is answering |
| `FETCH_MAX_REQUESTS` | 20 000 per call, the backstop. Reported as `over_budget` |

### The day cache (`cpva_cache.DayCache`)

Asked for 12.10.2026 ("save the data somewhere the first time and read it from
there"), location the user's choice: the scratch share, Software folder, own
subfolder outside every program folder (a redeploy replaces the program folder;
Image Tools can read the same files). `<scratch>\Software\CPVA cache\<signal>\
<YYYY-MM>.npz`: per day `t` (int64), `v`, and for a thinned day `lo`/`hi`, plus a
JSON block per day — `count` (points asked for that day, `None` = exactly as
archived), `src` (the name(s) that answered), units, the day's bounds.

| Rule | Why |
|------|-----|
| only whole Prague days ended > 1 h ago (`CLOSED_AFTER_NS`) | a closed day does not change; today only in memory (below) |
| only days read without a gap, never a channel that was re-checked or thinned after an overflow | a stored day is believed without asking |
| served only when stored at least as fine (`count` ≥ asked / `COUNT_SLACK`, or exact) | a three-month load stores ~22 points a day; a week then asks for ~285 and must go to the archiver (the finer day replaces it) |
| numbers only | a word or waveform channel is never stored |
| per day within a step (`_from_cache` → hits + missing ranges) | the newest step holds today, so it is never wholly closed — measured: it was re-read in full every time |
| `prefetch` reads the month files of the window in parallel before planning | the per-day look-ups never touch the share |
| `flush_async` after the load, atomic tmp + `os.replace`, re-reading the month just before replacing it | a load never waits for the share; two stations saving one month keep each other's days |
| root: frozen app → probe the share in a daemon thread (`SHARE_ROOTS`, 3 s), source run → OFF unless `CPVA_CACHE_DIR` | a test's fake archive must never land in the shared cache; the lab leg can take ~48 s to fail from the office |

Measured 12.10.2026, three months of the Operation preset: 2.8 MB for 90 days ×
8 signals; the second load asked the archiver 27 requests (today and the
period's partial first day) instead of 242, 711 signal-days from the cache.
**Recent days in memory** (`DayCache.recent_get` / `recent_put`, 12.10.2026).
The share holds whole closed days only, so a "last 24 h" window (the open day +
yesterday afternoon) was read in full on every reload — and the open day has no
thinned level at all: measured, PAP1 78 000 raw samples by 15:00, one request
4–21 s. Now one block per (signal, Prague day) for the last `RECENT_DAYS` (3)
days is kept in the process: `_from_cache` serves the part already read (plus
the newest sample before it, so a quiet signal still has a value to hold), the
store step adds what each load asked for (`tile_todo`), up to
`RECENT_SETTLE_NS` (2 min) before now. A block keeps its level of detail
(`density` = points per ns, `None` = exact) and is served only to a request no
finer than it (`COUNT_SLACK`); blocks of one day join when they touch and match
in level and archived name (`_join_blocks`), else the one covering more stays.
Off with the rest of the
cache (root `""`, i.e. every source run/test). Measured: 24 h reload 49 → ~10
requests. The open day is also asked for in pieces of at most
`OPEN_DAY_PIECE_NS` (4 h): one request per signal 4.7–14 s, 4 h pieces side by
side 3.9–4.5 s. What is left is the archiver's own answer time, which varies for
all requests at once (tiny requests 0.2–4.9 s depending on the moment) — sending
a slow request twice does not help.

**Parts on the share.** `recent_put(..., tz=)` also files a block as
`"<YYYY-MM-DD>+part"` in the month file once it grew by `SHARE_PART_STEP_NS`
(30 min) since it was last saved (`_shared_hi`); `flush` joins it with another
station's part (or keeps the larger) and `_drop_stale_parts` removes parts older
than `RECENT_DAYS` or whose whole day is stored. `prefetch` takes the months of
the window's recent days too, re-reads them after 30 min, and `_adopt_parts`
moves the parts into the memory blocks. Meta gained `density` and `day_lo`.
Measured: 24 h after a restart 24.6 s → 8.6 s, first picture at ~0.6 s.

**Cache before the archiver.** `_file_cached(ti)` hands over every tile's
cached part (`chunk_fn`, then `cached_fn(share served from cache)`) before
`_preflight_failed()` waits for the pre-flight request — it used to wait 2–4 s
for that minute of data. A dead archiver no longer discards what the cache gave:
only the rest becomes gaps. In `main.py` the load's snapshotter draws one
picture at once when `cached_fn` reports ≥ 0.9 (skipping its 2 s gap and the
"none after 90 %" rule). With ≥ 90 % from the cache the pre-flight is waited
for at most `PREFLIGHT_CACHED_WAIT_S` (3 s) — the full wait (up to two 10 s
timeouts) held the bar at 94 %.

**The sample before each day.** `_from_samples` keeps the newest numeric sample
before the day as `pre` (meta: `[t, value, min, max]`) and `_to_samples` puts it
first, as an archiver answer has it. A load starting on a cached day then has
its pre-window value without `cpva_fetch_last_before_many` — on a cached load
that look-back ran for nearly every signal (up to the 20 s deadline, the bar on
96 %). Days stored before 13.10.2026 lack it until re-stored.

**Timeline in the Log.** "timeline: …" lines: archive read finished after N s,
value before the window took N s, a picture during loading over 1 s (the bar
cannot move meanwhile), drawing the loaded data over 1 s.

`_channels_to_read_raw` also remembers each channel's probe for
`DENSITY_MEMO_S` (30 min, `_DENSITY_MEMO`), so a reload or a tab switch does not
probe again (tests clear it in their fake's `install`).

Constants: `CHUNK_SIZE_NS` (1 h — still the unit for timeouts and the live
tick), `MIN_CHUNK_NS` (1 min floor), `MAX_CHUNK_NS` (30 d, the widest single
request), `RAW_START_CHUNK_NS` (4 h, raw mode's first try), `MIN_CHUNK_COUNT`.

### The server's thinned series is not taken on trust

`count=` ("Detail" above zero; called "Avg to" until 12.10.2026) asks the archiver for its own decimated
samples. Measured against the live archiver on 2026-09-21 over 10.-12.9.2026,
that answer is wrong three different ways, and every one of them is silent —
this is what a user sees as "a signal holding one value across the whole
period":

* **a level with nothing behind it.** `HAPLS-SPEC_CENT_PD1M1_LT7_DIAG1:SpectralCentroid`
  holds **no** archived sample in that period (its neighbours are 11.8. and
  19.9.), and the archiver answered with **4321 identical interpolated
  points**, drawn as a solid three-day line;
* **a level that stops early.** `L3-PCM3Y-MTR03-73:RawPos` answered with 630
  identical points ending **61.5 h before the period did**, hiding the thirteen
  positions the motor went through at 10:30. The same request asked raw returns
  them;
* **minute-means instead of steps.** `L3-PFWP6-MTR03-1:RawPos` — the waveplate,
  and the master signal of the ramping work — has 1049 distinct archived
  positions there and came back as 90 averages, topping out at 999 096 instead
  of 1 000 000.

| Piece | What it does |
|-------|--------------|
| `_probe_density` / `_channels_to_read_raw` | **before anything else is asked for**, one raw request per channel over the newest hour (the middle hour decides if that one is empty). Under `VERIFY_DENSE_PER_HOUR` the channel is read exactly as archived for the whole period — thinning a motor position is not a saving, it is damage, and a sparse channel is cheap to read in full. Skipped for periods under 4 h |
| the sparse channel's chunk size | the whole step, not `RAW_START_CHUNK_NS`: it is sparse, so ask for the lot. Raw mode's cautious 4 h would be 2190 requests over a year for a channel holding a dozen samples |
| `_decimated_is_suspect` | after the merge, a thinned series is rejected when it **never changes** (no bin reports `minimum != maximum`, no two values differ) or when it does not reach within `VERIFY_EDGE_FRACTION` of either end |
| `_recheck_decimated` | probes those channels' density and re-reads the sparse ones raw. A dense channel whose series really is flat is a stuck meter — left alone, and said in the Log |
| `VERIFY_MAX_SAMPLES` | both directions have the same cap: a re-read bigger than this is dropped, and a channel judged sparse from two quiet probe hours that turns out to hold more than this is thinned after all (one extra request). Holding a million sample dicts is worse than a coarse trace |
| `FetchReport.rechecked` | the channels whose thinned series was thrown away |
| `verify=False` | switches the whole thing off; used only inside the re-reads, so they cannot recurse |

A separate bug in the same family, fixed with it: the archiver brackets a range
on **both** sides, so `_last_before_in_range` picking `max(time)` of the reply
returned the sample from **after** the window — the 10.9. window was seeded
with a reading stamped 19.9. and held flat across the three days. Only samples
older than the range end may answer now.

Pinned by `testing/test_thinned_series_is_checked.py`.

Measured against a fake archiver (`testing/test_long_window_fetch.py`): a year
over 2 signals costs ~250 requests when the archiver refuses anything above 7
days, and 4 when it serves the lot.

### Dependencies
- `requests`, `urllib3`, `orjson`, `ssl` — HTTPS to CPVA (no certificate verification)
- `numpy` — `safe_divide`
- `threading`, `itertools`, `math`, `time` — the adaptive fetch
- no GUI toolkit (safe for headless tests)
