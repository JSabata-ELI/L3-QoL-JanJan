# Spectra — STRUCTURE

> Verified against source: 2026-08-05 · `sp_t.py` 4536 L

## Files

| File | Description |
|------|-------------|
| `sp_t.py` | Single file. Spectrometer analysis in PySide6. |
| `_verify_layout.py`, `*.png` | Dev-only layout screenshots / helper, not part of the app. |

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
| `_METHODS` | dropdown label → stat key: Mean, Median, Trimmed mean 10%, Sigma-clipped mean |

### Config files (`%APPDATA%\ELI_Spectra\`)

| Path (function) | Content |
|-----------------|---------|
| `_search_pv_config_path()` → `search_pvs.json` | Current user search-PV list |
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
| `_load_search_presets()` / `_save_search_presets(p)` | `search_presets.json` I/O |
| `_ns_to_dt` / `_fmt_hms` / `_fmt_date` / `_fmt_dur` | ns → Prague datetime / `HH:MM:SS` / `YYYY-MM-DD` / `Xm YYs` |
| `_day_range_ns(qdate)` | `QDate` → whole day `(start_ns, end_ns)` in Prague tz |
| `_bg(fn)` | Runs `fn` in a daemon `threading.Thread` |

### Shared QSS / hint constants
`_CAL_STYLE` (calendar), `_CHK_STYLE` (`::indicator` only — never a border on the
root `QCheckBox`), `_GROUP_STYLE`, `_BTN_PRIMARY` / `_BTN_SUCCESS` / `_BTN_DANGER`,
`_TB_STYLE` and `_TB_HINTS` (matplotlib toolbar), `_APP_STYLESHEET`.

### `_CustomToolbar(NavigationToolbar2QT)`
Removes the **"Export values"** button from the Subplots dialog for good and
emits `subplot_params_changed` when that dialog closes (→ layout is saved).

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
| `_WeekendDelegate(QStyledItemDelegate)` | Calendar cell painting: selected = blue fill, Sat/Sun = red text (detected by `index.column()`, 5 = Sat, 6 = Sun, so spill-over days work too). `initStyleOption` strips Qt's own highlight from unselected cells. |
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
| `_colorbar_bot` / `_colorbar_info` | colorbar object + `{cmap, vmin, vmax, label}` for GDD/TOD colouring |
| `_last_saved_layout` | last written layout (dedupes writes) |

#### UI layout

```
QHBoxLayout
├── Sidebar (fixed width SIDEBAR_W = 340 px)
│   ├── Status block (blinking live indicator + lbl_day + lbl_status)
│   ├── "📅 Load day…" button
│   ├── GroupBox "Search data by"
│   │     ├── Active card: "Searching by:" + "Spectrum:" + "Change…"
│   │     │      and the resolved "→ X: … / Y: …" pair
│   │     ├── Preset combo + inline + / ✎ / 🗑 (add / rename / delete)
│   │     ├── Inline channel search (fast add)
│   │     ├── QTableWidget [Label | Channel] (click = plot; double-click = rename)
│   │     └── "+ Add PV…" (PvSearchDialog) / "✕ Remove"
│   ├── GroupBox "Mode" (Archive | Live, checkable buttons)
│   ├── GroupBox "Live" (Average last N + Start/Stop) — hidden in archive mode
│   ├── GroupBox "Display"
│   │     Average (_METHODS) · Colour by (Selection order / GDD / TOD) ·
│   │     Normalize (None / Peak / Area) · Variation band (±1σ / 10–90 pct) ·
│   │     Smooth + window · Show search graph
│   ├── GroupBox "Spectrum range [nm]" (From / To + "Auto-fit range to data on Analyze")
│   ├── GroupBox "Compare regions" (Show comparison curve, A, B, A−B / A÷B)
│   ├── Region panel (Expand all + collapsible region list + Clear/Analyze + progress)
│   └── "💾 Export results"
└── QSplitter (Vertical)
    ├── Top canvas (_CustomToolbar + "Select" mode + search signal + SpanSelector)
    └── Bottom canvas (averaged / live spectra + optional GDD/TOD colorbar)
```

Both canvases are built by `_make_canvas_panel(suffix)`; `_update_top_visibility()`
hides the search graph on request.

#### Key methods

| Method | Purpose |
|--------|---------|
| `_pick_day()` | `DatePickerDialog` → Archive mode → `_load_day_energy()` |
| `_load_day_energy()` / `_on_energy_loaded()` / `_on_energy_error()` | background fetch of the active search PV for the selected days, then `_draw_energy()` + `_install_span()` |
| `_load_search_pvs` / `_save_search_pvs` / `_refresh_pv_table` | search-PV list persistence + table |
| `_open_add_pv_dialog` / `_remove_selected_pv` / `_on_pv_label_edited` | add / remove / rename a PV |
| `_ensure_channels_loaded` / `_on_channels_loaded` / `_on_inline_search` / `_on_inline_result_clicked` | inline channel search |
| `_update_preset_combo` / `_on_preset_combo_changed` / `_preset_add` / `_preset_rename` / `_preset_delete` / `_open_edit_presets_dialog` / `_apply_preset` | presets |
| `_change_spec_pv()` | pick the spectrum Y channel. A `…_X`/`…_Y` channel is treated as one half of a pair (X = base + `_X`); any other channel is the Y waveform itself and `XAxisSourceDialog` asks how to build the wavelength axis |
| `_load_x_axis_cfg` / `_save_spec_base` / `_x_axis_summary` / `_resolve_x_data` | wavelength-axis config, its human-readable summary, and the actual fetch/build |
| `_save_layout` / `_load_layout` | splitter + subplot margins (`layout.json`) |
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
| `cancel_scan()` | public API for the parent "Stop All" → `_stop_live()` |

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

#### Live mode performance
`_live_tick()` polls every `LIVE_INTERVAL_S`, but most ticks bring no new shot —
the redraw (`ax.clear` + up to `MAX_INDIVIDUAL_LINES` traces + a full
`draw_idle`) only runs when new spectra actually arrived. The status line is
still updated on every tick.

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
