# Spectra tab — STRUCTURE

> Verified against source: 2026-08-21 · `sp_t.py`

Spectra is **not a program of its own** — it is the second tab of CSS Logger and
lives in this folder as `sp_t.py`. It had its own `Spectra/` folder until
2026-08-21; that folder is gone, because the copy of `sp_t.py` left behind in
`CSS Logger/` was the one that ended up in every build.

Documentation of the tab: `ReadMe_Spectra tab.txt` (short) and
`ReadMe_Spectra tab_Full.txt` (detailed). Neither is shown by the Launcher — the
Launcher's **ReadMe** / **Details** buttons open the CSS Logger documents, which
are the ones users see. The CSS Logger structure document is `STRUCTURE.md`.
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `sp_t.py` | Single file. Spectrometer analysis in PySide6. |
| `_verify_layout.py` | Dev-only: renders the widget headless and saves the screenshots below. Run with `python _verify_layout.py`. |
| `ui_full.png`, `ui_sidebar.png`, `sidebar_small.png`, `top_multi.png`, `top_steps.png`, `_verify_large.png`, `_verify_small.png` | Layout screenshots from that script. Reference images only — the app never reads them. |

There is no `icon.ico` and no `build_config.json` here, and that is deliberate:
**Spectra is not built as a standalone program.** `SpectraWidget` is imported by
`CSS Logger/main.py` (which puts `Spectra/` on `sys.path` at line 13) and shown as
the second tab of that app, so it ships inside the CSS Logger build. `sp_t.py`
still runs on its own for development.


---

## sp_t.py

### Purpose
Analysis of spectrometer data (SPIDER by default, any waveform PV in practice)
from the CPVA archive. Two modes: **Archive** — pick time regions in the search
graph and average the spectra inside them — and **Live** — poll the newest shots
and average the last N.

### Constants

| Constant | Value / meaning |
|----------|-----------------|
| `PV_ENERGY` | `HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy` — SBW4 energy (default search signal + per-region scalar) |
| `PV_ALPHA_ENERGY` | `HAPLS-ENER_IN_GPL_LT2_DIAG2:Energy` — Alpha output energy |
| `PV_SPEC_X` / `PV_SPEC_Y` | `L3-SBDP-SPIDER:SpecDomain_Int_X` / `_Y` — wavelength / intensity waveform |
| `CPVA_URL` | `https://10.78.0.57:8443/api/1.0/cpva` |
| `ORDER_PVS` | `(label, channel)` for Order 2/3/4 = **GDD, TOD, FOD** — averaged per region and shown in the details |
| `DEFAULT_SEARCH_PVS` | Default search-PV list (SBW4, Alpha, GDD, TOD) — user-editable |
| `SIDEBAR_W` | `340` px — fixed sidebar width |
| `LIVE_INTERVAL_S` | `3` s — live poll period |
| `LIVE_BUF_MAX` | `2000` — rolling buffer size (`deque`) |
| `DEFAULT_LIVE_N` | `100` — default "average last N" |
| `LIVE_HISTORY_S` | `600` s — preload when live mode starts |
| `MAX_INDIVIDUAL_LINES` | `400` — cap when overlaying a region's individual spectra |
| `_REGION_COLORS` | 8 default region colours |
| `_TRACE_COLORS` | 8 colours for the search-graph PV traces — a separate palette so a curve is never drawn in a selected spectrum's colour. Assigned by `_trace_colour()` from the PV's place in the PV list and looked up on every draw, so a reload, a day with no data for one PV or any redraw can never shuffle the curve colours |
| `_METHODS` | dropdown label → stat key: Mean, Median, Trimmed mean 10%, Sigma-clipped mean |
| `_METHOD_SHORT` | stat key → short legend text. Measured on synthetic data, the four methods differ by 0.1–0.2 % of peak on clean spectra (up to 30 % with bad shots in the region), so switching them looks like a no-op. The active method is therefore named in the graph title, every legend entry and each region's metric block — the label is the feedback |
| `_CBAR_BOX` / `_CBAR_RECT` / `_FULL_RECT` | the colour bar's fixed axes rectangle, the figure fraction left to the plot while it shows, and the whole figure when hidden. **Absolute values, never derived from the current axes size** |
| `_STEP_COLORS` | (accent, fill, border) per numbered sidebar card. 1 grey = day, 2 green = the search signal **and** the PV list it comes from (one card: the list is how you choose the signal), 3 blue = measured spectrum |
| `_PV_COL_SHOW` / `_PV_COL_LABEL` / `_PV_COL_CHAN` / `_PV_SHOW_W` | column indices of the PV table (tick box, Label, Channel) and the tick-box column width. Named because the tick box was inserted **in front of** Label — every leftover literal `0`/`1` renamed the wrong cell. 30 px so the style's check indicator is not clipped at 150 % display scaling |

### Config files (`%APPDATA%\ELI_Spectra\`)

| Path (function) | Content |
|-----------------|---------|
| `_search_pv_config_path()` → `search_pvs.json` | Current user search-PV list — `{label, channel, shown}` per PV; `shown: false` means it stays listed but is left off the search graph |
| `_preset_config_path()` → `search_presets.json` | Named PV-list presets |
| `_spec_pvs_config_path()` → `spec_pvs.json` | Spectrum Y channel, its base name, and the wavelength-axis config |
| `_layout_config_path()` → `layout.json` | Splitter sizes + manually adjusted subplot margins |

### Module-level helpers

| Function | Purpose |
|----------|---------|
| `_ssl_ctx()` | SSL context without certificate verification (self-signed) |
| `_cpva_fetch(channel, start_ns, end_ns)` | One HTTP GET on `/samples` → list of dicts |
| `_cpva_load_all_channels()` | GET `/channels` → sorted channel names (`[]` on error); cached in `_cpva_channel_cache` |
| `_fetch_waveforms(...)` | Waveform data → `list[(ts_ns, np.ndarray)]` |
| `_fetch_scalars(...)` | Scalar data → `list[(ts_ns, float)]` |
| `_strip_xy_suffix(ch)` | `…_X` / `…_Y` → base channel name |
| `_reconstruct_wavelength_axis(vals)` | Builds an axis from a partly broken `_X` waveform |
| `_load_x_csv(path)` | Wavelength axis from a CSV / text file |
| `_parse_number_list(text)` | Tolerant number-list parser (CSV, spaces, `;`) |
| `_trimmed_mean(stack, frac=0.1)` / `_sigma_clipped_mean(stack, sigma=3)` | Robust averages |
| `_compute_stats(arrs)` | Keeps only the most common waveform length, then computes **every** method at once → `mean/median/trimmed/sigma/std/p10/p90/stack/n`, so switching the method needs no refetch |
| `_trapz(y, x)` | Trapezoid integral, NumPy 1.x/2.x compatible |
| `_fwhm(x, y)` | FWHM above baseline with linear edge interpolation |
| `_spectral_metrics(x, y)` | `peak_wl`, `peak_int`, `centroid`, `fwhm`, `rms_bw`, `area` |
| `_smooth(y, win)` | Moving-average smoothing |
| `_split_query(text)` | PV search: text → lowercase tokens. Spaces, commas, semicolons and `*` all separate, so `l3 sbw4`, `l3,sbw4` and `*l3**sbw4*` are one query. |
| `_tokens_in_order(hay, tokens)` | True when every token appears in the order typed. |
| `_rank_pv_match(name, tokens)` | Sort weight (lower = better) or `None`. **AND semantics** — a missing token means no hit. Tiers: exact channel 0, exact field 1, prefix 2, in field 3, anywhere 4; the *weakest* token sets the tier and the sum only breaks ties. `+5` when the tokens are out of order, `+100` for a camera channel (`^C\d{2}-\d{2,3}-`), because the camera channels are a large slice of the archiver list and would otherwise bury every real hit. |
| `_pv_search(channels, text, exclude=None)` | The ranked result list. **Empty query returns nothing** — not everything. |

The four above are the same PV-search behaviour as the Image Slider picker; that
one is the reference implementation.
| `_load_search_presets()` / `_save_search_presets(p)` | `search_presets.json` I/O |
| `_ns_to_dt` / `_fmt_hms` / `_fmt_date` / `_fmt_dur` | ns → Prague datetime / `HH:MM:SS` / `YYYY-MM-DD` / `Xm YYs` |
| `_day_range_ns(qdate)` | `QDate` → whole day `(start_ns, end_ns)` in Prague tz |
| `_bg(fn)` | Runs `fn` in a daemon `threading.Thread` |
| `_fit_button(btn, extra=22)` | Minimum width measured from the button's own label. **Never `setFixedWidth` on a button holding text** — `_APP_STYLESHEET` spends ~16 px on padding + border, so a hand-picked number clipped "Change…" to "Chang…", and clipped harder at 125/150 % display scaling |
| `_step_card(number, title)` | One numbered sidebar card → `(frame, body_layout)`. Number badge + coloured left stripe; the fill and border are scoped to the frame's object name so they cannot cascade onto the table or spin boxes inside |

### Shared QSS / hint constants
`_CAL_STYLE` (calendar), `_CHK_STYLE` (`::indicator` only — never a border on the
root `QCheckBox`), `_GROUP_STYLE`, `_BTN_PRIMARY` / `_BTN_SUCCESS` / `_BTN_DANGER`,
`_TB_STYLE` and `_TB_HINTS` (matplotlib toolbar), `_APP_STYLESHEET`.

### `_CustomToolbar(NavigationToolbar2QT)`
Removes the **"Export values"** button from the Subplots dialog for good and
emits `subplot_params_changed` when that dialog closes (→ layout is saved).

### `_SpectraFocusWindow(QWidget)` — focus mode (F11)

Borderless top-level window holding nothing but the spectra graph. Flags
`Qt.Tool | FramelessWindowHint | WindowStaysOnTopHint`, parent `None` (so it can
sit on any monitor); `Tool` keeps it off the taskbar. Same shape as the Image
Slider's `_FocusWindow` in `../Image Tools/is_t.py`, which is the reference
implementation.

- `_bot_container` — the canvas panel built by `_make_canvas_panel("bot")` — is
  **re-parented, never copied**, which is why live polling keeps painting into it
  with no changes to the timer path at all.
- No title bar, so move/resize are by hand: `eventFilter` swallows a left-press on
  the graph and calls `startSystemMove()`; the `_BORDER = 8` layout margin is the
  resize grip (`_edges_at` / `_cursor_for` / `mousePressEvent`, falling back to
  arithmetic when the window manager declines `startSystemResize`). Safe to take a
  plain drag because `_tb_bot` is hidden while in focus mode.
- `_leaving` guards `closeEvent`. Two traps, both hit during development:
  `close()` is also how `_focus_leave` tears the window down, and the re-dock hook
  in `closeEvent` then asked the owner to toggle again — which re-entered focus
  mode immediately. `_focus_toggling` on the widget is the second belt.

Owner side: `toggle_focus_mode()` (public — F11 calls it), `_focus_enter`,
`_focus_leave`, `_load_focus_rect` / `_save_focus_rect` (`focus_window` key in
`layout.json`, discarded when it lands on a disconnected monitor).

`eventFilter` on the main window leaves focus mode when it is restored from the
taskbar, and needs **two** steps — `isMinimized()` seen first, then not. Watching
only for "not minimized" fired on the `showMinimized()` call inside `_focus_enter`,
so focus mode ended the instant it started.

**Not gated on live mode.** F11 works in Archive too.

### F11 ownership

`CSS Logger/main.py` registers the only F11 (`_install_graph_shortcuts`,
`ApplicationShortcut`) and `_graph_popout()` forwards to
`window()._spectra.toggle_focus_mode()` when the CSS Logger tab is not the visible
one. Registering a second `ApplicationShortcut` for F11 here instead would make Qt
report an ambiguous shortcut and fire neither. The standalone `__main__` block has
its own F11 because there is no suite to forward it.

### Signal class
```python
class _Sig(QObject):
    done       = Signal(object)
    error      = Signal(str)
    progress   = Signal(str)
    progress_n = Signal(int, int)   # (done, total)
```
Usage: `sig = _Sig(self)` → connect slots → run in the background via `_bg(fn)`.
CPVA calls **never** happen on the main thread.

### Dialogs

| Dialog | Purpose |
|--------|---------|
| `_WeekendDelegate(QStyledItemDelegate)` | Calendar cell painting: selected = blue fill, Sat/Sun = red text. The weekend test uses **the cell's real date**, never its column index. `_date_for_index` prefers the model's own `UserRole` date; when that is absent (spill-over cells) it reconstructs one, and `_first_cell()` accounts for Qt dropping the header row / week-number column. It also compensates for Qt shifting the whole grid back a week when the 1st falls in the first column — without that the painted days sat a week off and clicking one day highlighted another. `initStyleOption` strips Qt's own highlight from unselected cells. |
| `_make_calendar(initial)` | Factory: `QCalendarWidget` (Monday-first, English locale) + custom grey nav row (◀ month▾ year ▶) + custom grey day-name header. Returns `(wrapper_frame, cal)`. |
| `DatePickerDialog` | Day selection. Plain click = toggle a day, Ctrl+click = add the range from the last click. `selected_dates()` → `list[QDate]`. |
| `PvSearchDialog` | Loads every CPVA channel once, then filters locally while typing. Multi-select → `added_pvs()`. |
| `XAxisSourceDialog` | How to build the wavelength axis for a Y channel that has no `_X` twin: copy from another PV, copy + linear transform (`x' = a·x + b`), load from a CSV / text file, or use the plain sample index. |
| `PresetEditDialog` | Named presets: preset list (Load) on the left, channel search + green selected PVs on the right; Save/Delete, `preset_loaded` signal. |
| `ExportDialog` | Export options: CSV data + graph image (png/pdf/svg). |
| `_AxisLabelsDialog` / `_AxisLimitsDialog` | Per-axis title / limit editing straight on a canvas (Auto restores autoscale). Opened from the canvas right-click menu built in `_make_canvas_panel` (Axis limits…, Axis labels…, major/minor grid, Y scale linear/log, Reset view — the hit test is DPI-proof, 125/150 % scaling used to swallow the menu). |

### SpectraWidget(QWidget)

#### State

| Attribute | Type / meaning |
|-----------|----------------|
| `_selected_day` / `_selected_days` | `QDate` / `list[QDate]` |
| `_day_start_ns` / `_day_end_ns` | bounds of the loaded range |
| `_search_pvs` | `list[(label, channel)]` — user-editable search-PV list |
| `_spec_y_pv` / `_spec_base_pv` / `_spec_x_pv` / `_x_axis_cfg` | spectrum channel + how its X axis is built (`native`/`pv`/`linear`/`csv`/`index`) |
| `_color_mode` | `"order"` \| `"gdd"` \| `"tod"` — how spectra are coloured |
| `_energy_data` | `list[(ts_ns, float)]` — search-signal series for the loaded day(s) |
| `_x_data` | `np.ndarray \| None` — wavelength axis, cached from the first resolve |
| `_regions` | `list[dict]` — single source of truth; each region carries everything (id, t_start, t_end, color, visible, expanded, show_individual, analyzed, mean/median/trimmed/sigma/std/p10/p90/stack, orders, energy_avg, energy_n, n, `_metrics`) |
| `_region_seq` | monotonic id source |
| `_row_widgets` | `dict[id → {name, eye, details}]` — widget refs for in-place updates |
| `_span` | `SpanSelector` on the top graph |
| `_top_user_xlim/ylim`, `_bot_user_xlim/ylim` | saved pan/zoom state (survives redraw) |
| `_top_cursor_artists` / `_bot_cursor_artists` | crosshair + floating label artists per canvas |
| `_live`, `_live_buf`, `_live_start_ns`, `_live_last_ns`, `_live_autofit_done` | live-mode state + rolling `deque(maxlen=LIVE_BUF_MAX)` |
| `_busy` | analysis in progress (lock) |
| `_cax_bot` | the colour bar's **permanent** axes. Created once in `_make_canvas_panel("bot")`, then only shown/hidden |
| `_colorbar_bot` / `_colorbar_info` | colorbar object + `{cmap, vmin, vmax, label}` for GDD/TOD colouring |
| `_bot_container` | the canvas panel focus mode borrows |
| `_focus_win`, `_focus_was_maximized`, `_focus_saw_minimized`, `_focus_toggling` | focus-mode state |
| `_split_ratio` | the divider ratio the user last **dragged**. `_update_top_visibility()` restores this instead of a hard-coded pair |
| `_bot_right_base` | right margin the user set by hand in the Subplots dialog; only meaningful while the layout engine is off |
| `_loading` | a day load is in flight (feeds the state pill and the Stop button) |
| `_analysis_gen` / `_reanalyze_pending` | generation stamp for in-flight analyses + a re-run queued behind an obsolete one |
| `_live_used_n` / `_live_slice_n` | shots the live average actually used vs. handed to it — the difference is reported as "skipped (different length)" |
| `_last_saved_layout` | last written layout (dedupes writes) |

#### UI layout

```
QHBoxLayout
├── Sidebar (fixed width SIDEBAR_W = 340 px, inside one QScrollArea)
│   ├── State row: pill (○ Idle / ⟳ Working… / ● LIVE) + "⏹ Stop"
│   ├── lbl_status
│   ├── Step card 1 "DAY"  — lbl_day + "📅 Load day…"
│   ├── Step card 2 "SEARCH BY — what I search on"
│   │     ├── the SELECTED PV's label + its channel + "(+N more plotted)  (N off)"
│   │     ├── Preset combo + inline + / ✎ / 🗑 (add / rename / delete)
│   │     ├── Inline channel search (fast add)
│   │     ├── QTableWidget [✓ | Label | Channel] (tick = drawn in the search graph;
│   │     │      click a row = search by it; dbl-click a label = rename)
│   │     │      Channel stretched to the table edge by _fit_pv_columns()
│   │     └── "+ Add PV…" (PvSearchDialog) / "✕ Remove"
│   ├── Step card 3 "SPECTRUM — what I measure"
│   │     ├── the channel, large; the resolved "X: … / Y: …" pair, small
│   │     └── "Change…"  (_fit_button, never a fixed width)
│   ├── GroupBox "Mode" (Archive | Live, checkable buttons)
│   ├── GroupBox "Live" (Average last N + Start/Stop) — hidden in archive mode
│   ├── GroupBox "Display" — ONE QGridLayout, labels aligned in column 0
│   │     Average (_METHODS) · Colour (Selection order / GDD / TOD) ·
│   │     Normalize (None / Peak / Area) · Variation band (±1σ / 10–90 pct) ·
│   │     Smooth + window   … then "Show search graph" below the grid
│   ├── GroupBox "Spectrum range [nm]" (From / To, width-capped + trailing
│   │     stretch so "To:" sits beside the first value, + auto-fit checkbox)
│   ├── GroupBox "Compare regions" (Show comparison curve, A, B, A−B / A÷B)
│   ├── Region panel (Expand all + collapsible region list + Clear/Analyze + progress)
│   └── "💾 Export results"
└── QSplitter (Vertical)
    ├── Top canvas (_CustomToolbar + "Select" mode + search signal + SpanSelector)
    └── Bottom canvas (averaged / live spectra + optional GDD/TOD colorbar)
```

Both canvases are built by `_make_canvas_panel(suffix)`; `_update_top_visibility()`
hides the search graph on request and restores `_split_ratio`.

The bottom figure additionally gets `_cax_bot` (`fig.add_axes(_CBAR_BOX)`,
`set_in_layout(False)`, hidden). The plot's own room is steered by the tight-layout
engine's `rect` — see **The graph must not resize itself** below.

#### Key methods

| Method | Purpose |
|--------|---------|
| `_pick_day()` | `DatePickerDialog` → Archive mode → `_load_day_energy()` |
| `_load_day_energy()` / `_on_energy_loaded()` / `_on_energy_error()` | background fetch of the active search PV for the selected days, then `_draw_energy()` + `_install_span()` |
| `_load_search_pvs` / `_save_search_pvs` / `_refresh_pv_table` / `_fill_pv_row` | search-PV list persistence (including each PV's `shown` flag) + table. A missing `shown` key reads as shown, so an older config file comes back with every PV on the graph; a file with *everything* off is treated as everything on, because an empty graph looks broken |
| `_pv_is_shown` / `_shown_pvs` / `_first_shown_row` / `_pv_hidden` | which PVs are drawn. `_pv_hidden` holds channels, not rows, so it survives a reorder; `_refresh_pv_table` prunes it to the current list so re-adding a channel does not bring it back switched off |
| `_on_pv_item_changed` → `_on_pv_show_toggled` / `_on_pv_label_edited` | one `itemChanged` handler dispatched by column: the tick box switches the curve, the Label cell renames. Toggling repaints only that row (grey when off), so the selection and scroll position survive. Two invariants: the last ticked PV cannot be unticked, and selecting an unticked row re-ticks it — the search must never run on a curve nobody can see |
| `_open_add_pv_dialog` / `_remove_selected_pv` | add / remove a PV |
| `_ensure_channels_loaded` / `_on_channels_loaded` / `_on_inline_search` / `_on_inline_result_clicked` | inline channel search |
| `_update_preset_combo` / `_on_preset_combo_changed` / `_preset_add` / `_preset_rename` / `_preset_delete` / `_open_edit_presets_dialog` / `_apply_preset` | presets |
| `_change_spec_pv()` | pick the spectrum Y channel. A `…_X`/`…_Y` channel is treated as one half of a pair (X = base + `_X`); any other channel is the Y waveform itself and `XAxisSourceDialog` asks how to build the wavelength axis |
| `_load_x_axis_cfg` / `_save_spec_base` / `_x_axis_summary` / `_resolve_x_data` | wavelength-axis config, its human-readable summary, and the actual fetch/build |
| `_save_layout` / `_load_layout` / `_on_splitter_moved` | splitter + subplot margins + focus geometry (`layout.json`). `_save_layout` writes `_split_ratio`, **not** the live sizes: while the search graph is hidden the live sizes are `[0, everything]`, and saving that collapsed the top graph on the next start |
| `_set_cbar_space(on)` | reserve/release the colour bar's width. Idempotent by construction — both branches SET an absolute value, so running it on every redraw cannot accumulate |
| `_fit_pv_columns` / `resizeEvent` | hold the tick box at `_PV_SHOW_W`, size Label to its content (capped) and stretch Channel to the table edge. `resizeColumnToContents` first, then read the width back: on an Interactive column `sectionSizeHint()` reports the HEADER's hint, not the widest cell |
| `_set_pill` / `_refresh_pill` / `_update_stop_button` | the Idle / Working… / LIVE state box and the Stop button |
| `_reanalyze_all(why)` / `_start_pending_reanalysis` / `_finish_analysis_ui` | keep the selections, drop stale results, re-run |
| `_install_span()` / `_on_span(xmin, xmax)` | SpanSelector → new region in `_regions` |
| `_draw_top_empty` / `_draw_energy` / `_paint_region_spans` | top graph |
| `_rebuild_regions_ui` / `_make_region_row` / `_build_region_details` / `_apply_visibility_style` | region panel |
| `_toggle_region_expanded` / `_toggle_all_expanded` / `_update_expand_all_btn` / `_toggle_region_visible` / `_toggle_region_individual` / `_delete_region` / `_clear_regions` / `_update_action_buttons` | region actions |
| `_run_analysis()` | background: per unanalysed region fetch `PV_SPEC_Y` + `PV_ENERGY` + `ORDER_PVS` → `_compute_stats()`; runs straight away, no confirmation dialog |
| `_on_analysis_progress` / `_on_analysis_done` / `_on_analysis_error` | `r.update(res)`, `analyzed=True`, rebuild UI, `_redraw_spectra()` |
| `_redraw_spectra()` | bottom graph: visible regions, method, colour mode, normalize, variation band, smoothing, comparison curve, live overlay, colorbar |
| `_prep_curve` / `_norm_scale` / `_norm_mode` / `_band_kind` / `_smooth_win` / `_method` / `_intensity_label` | display-option helpers |
| `_plot_spectrum` / `_plot_individual` / `_plot_live_spectra` / `_plot_comparison` | the actual curves |
| `_compute_region_colors()` | `"order"` → fixed palette; `"gdd"`/`"tod"` → rainbow by Order 2/3 value + sets `_colorbar_info` |
| `_metrics_html` / `_update_metric_labels` | peak / centroid / FWHM / RMS-BW / area readout |
| `_refresh_compare_combos` | keeps the A/B region combos in sync |
| `_signal_span` / `_apply_fit_span` / `_auto_fit_range` / `_auto_fit_live_range` / `_on_x_range_edited` | wavelength-range auto-fit |
| `_set_live_mode` / `_toggle_live` / `_start_live` / `_stop_live` / `_live_tick` / `_on_live_y` / `_blink_tick` | live mode |
| `_export()` / `_export_csv()` / `_export_comparison_curve()` | CSV + graph image export |
| `cancel_scan()` | stop everything — live, a day load, an analysis. Wired to the sidebar **Stop** button and to `CPVASuiteWindow.closeEvent`. Was dead code inside the suite: only the standalone `__main__` block called it |

#### Crosshair cursors
`_install_cursor` (bottom) and `_install_top_cursor` (top) draw a blitted
crosshair with floating X/Y value labels inside the graph.

- The artists live in `self._bot_cursor_artists` / `_top_cursor_artists` and are
  **recreated after every `ax.clear()`** by `_install_bot_cursor_artists()` /
  `_install_top_cursor_artists()` — called from `_redraw_spectra`, `_draw_bot_empty`
  and the top-graph equivalents.
- The mouse callbacks always read the artists fresh from `self`, so a queued
  redraw can never draw a detached `Text` (that raises
  `'NoneType' object has no attribute 'dpi'`).
- Motion is coalesced through a 16 ms `QTimer.singleShot`, with a background
  region captured on draw and restored per move.

#### Pan/zoom + "Select" mode (top toolbar)
`_connect_zoom_tracking()` stores the user's xlim/ylim in `_*_user_*lim` so a
redraw does not reset the view; "Home" clears them. The custom **"Select"**
action (top toolbar only) arms the span selector; turning on Pan/Zoom switches
Select off and suspends the span.

#### The graph must not resize itself

Two independent causes, both measured before and after with a throwaway harness
that toggles the controls 30x and prints `_ax_bot.get_position()` each round.

**1. The colour bar used to eat the plot.** `fig.colorbar(sm, ax=ax, fraction=0.04,
pad=0.01)` takes its space out of the parent axes' *current* rectangle, and
`Colorbar.remove()` does not reliably hand it back — especially once `_load_layout`
has killed the layout engine for good (`fig.set_layout_engine(None)`, which happens
the moment the user has ever opened the toolbar's Subplots dialog). Because every
display control redraws through `_redraw_spectra`, the remove-then-re-add cycle ran
on each click of Average / Normalize / Smooth / Variation band as well.

Measured on the old code, plot width as a fraction of the figure:

| clicks on "Colour by" | 0 | 5 | 15 | 25 | 29 |
|---|---|---|---|---|---|
| width | 0.929 | 0.509 | 0.182 | 0.062 | **0.040** |

and toggling Normalize/Smooth/Band with the bar up drove it to **0.000** — the
graph disappeared entirely and the right-hand side was blank. That is exactly the
"it keeps shrinking and leaves empty space" report.

Now: one permanent `_cax_bot`, shown or hidden, drawn into with `cax=` (never
`ax=`), and `_set_cbar_space(on)` sets the layout engine's `rect` to an absolute
`_CBAR_RECT` / `_FULL_RECT`. `Colorbar.remove()` is never called. Result: exactly
two possible rectangles, 0.929 without the bar and 0.809 with it, stable to five
decimals over 30 rounds.

`_cax_bot` is positioned by hand so it has no gridspec cell, which makes
tight_layout emit "This figure includes Axes that are not compatible with
tight_layout" on **every** draw. `set_in_layout(False)` does *not* suppress it —
`TightLayoutEngine.execute` passes all of `fig.axes` to `get_subplotspec_list`
regardless. The message is filtered once at module import; the main axes is still
laid out correctly inside the rect, which is what the measurements show.

**2. The splitter ratio was thrown away.** `_update_top_visibility()` did
`setSizes([440, 320])` unconditionally, and it runs from both
`_chk_show_energy.toggled` and `_set_live_mode()` — so every Archive/Live click
and every "Show search graph" tick discarded whatever the user had dragged. It now
restores `_split_ratio`, recorded by `_on_splitter_moved` (a real drag; note that a
programmatic `setSizes()` emits no `splitterMoved`, which is worth knowing when
testing this).

There are deliberately **no** graph-size presets and no "fit to window" button. The
default size was fine; the bug was that it did not stay.

#### Live mode performance
`_live_tick()` polls every `LIVE_INTERVAL_S`, but most ticks bring no new shot —
the redraw (`ax.clear` + up to `MAX_INDIVIDUAL_LINES` traces + a full
`draw_idle`) only runs when new spectra actually arrived. The status line is
still updated on every tick.

#### What "average last N" really averages
`N` is a ceiling: `buf = list(self._live_buf)[-n_avg:]` with
`n_avg = min(N, len(buf))`, then `_compute_stats(buf)`. Two silent losses, both now
reported in the status line (`averaging last 37 of 37 buffered (N=100)`):

- `_compute_stats` keeps only the **most common waveform length**, so the average
  can rest on fewer shots than it was handed — the difference is shown as
  "skipped (different length)" (`_live_used_n` vs `_live_slice_n`).
- Every buffered shot is averaged, but only up to `MAX_INDIVIDUAL_LINES = 400` are
  *drawn* as faint traces; above that `_plot_live_spectra` decimates.

The preload is only `LIVE_HISTORY_S = 600` s, so shortly after Start Live there are
usually far fewer than 100 shots buffered — which is why the average looked like it
was ignoring N.

#### Re-analysis when the spectrum source changes
A region's curves belong to one channel and one wavelength axis. `_change_spec_pv`
used to rewrite `_spec_y_pv` / `_x_axis_cfg` and clear `_x_data` while leaving
`r["analyzed"] = True`, so the old channel's curves stayed on screen, the graph
silently mixed two channels, and Analyze stayed greyed out — the only way out was
Clear all plus re-marking every span.

`_reanalyze_all(why)` now keeps the selections (id, times, colour, label,
visibility, expanded) and pops `_RESULT_KEYS`, then calls `_run_analysis()`, which
already processes exactly the not-analysed regions.

An analysis already in flight is the tricky part. `_analysis_gen` stamps each run
and travels in the `sig.done` payload; `_on_analysis_done` discards a payload whose
stamp is stale, otherwise old-channel results would be written into the regions that
were just cleared. When the change lands mid-fetch, `_reanalyze_pending` queues the
re-run and `_start_pending_reanalysis` fires it once the obsolete run unwinds
(`_finish_analysis_ui` is the shared tail of both the done and error handlers).

#### Export CSV format
One CSV, `utf-8-sig`, `sep=;` on the first line and a decimal point:

1. `# Spectrum details` — one row per analysed region: label, date, start, end,
   # of spectra, method, SBW4 output energy, GDD/TOD/FOD, then the metric columns
   (peak λ, peak intensity, centroid, FWHM, RMS bandwidth, area). Live shots get
   one summary row.
2. blank separator line
3. `# Curve data` — `wavelength_nm`, then `<region> (<method>)` + `<region> std`
   per region, one column per live shot, and the comparison column when enabled
   (7 regions + 100 live shots = 107 curves).

---

## Dependencies
- `PySide6` — Qt widgets, signals, threading
- `numpy` — spectral math, averaging, metrics
- `matplotlib` — figures, `SpanSelector`, `FigureCanvasQTAgg`, `NavigationToolbar2QT`, colorbar
- `ssl`, `urllib` — CPVA archiver API (no certificate verification)
- `zoneinfo` — Prague timezone
- `json`, `csv` — config persistence + export
