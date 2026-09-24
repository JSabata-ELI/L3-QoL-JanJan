# Spectra tab — STRUCTURE

> Verified against source: 2026-09-23 · `sp_t.py`, `daypicker.py`

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
| `daypicker.py` | The shared day/time picker. **Verbatim copy — the master is `Image Tools/daypicker.py`.** Loaded by path, see `_import_daypicker`. |
| `testing/test_daypicker_sync.py` | The drift guard: byte-compares the two copies of `daypicker.py` and checks `sp_t.py` still loads it by path rather than by a plain import. No Qt, no network. |
| `testing/test_spectra_windows.py` | The compressed-axis arithmetic (`_TimeMap`): only the picked hours are on the axis, one day is the identity, the join is marked, the round trip is exact, no line is drawn across a join, a drag across a join splits per day, an old region still shades correctly. No Qt widgets. |
| `testing/test_spectra_multiday.py` | The same end to end, with `_fetch_scalars` replaced: every window is fetched, the progress bar counts them all, the archiver's "sample before the window" freebie is dropped, a silent PV still reports when it last recorded, the graph really is drawn compressed with one divider per join, **Axis limits** refuses X on the search graph but allows it below, a click is not a spectrum. |
| `testing/test_every_spectrum.py` | The **Every spectrum** display on synthetic regions (no archiver): counts the curves actually drawn, checks the thinning is named in the title and the legend, checks the CSV writes one time-named column per shot, and renders `testing/_out/*.png`. `SPEC_TEST_NX=2048` times it on a realistic waveform length. |
| `testing/test_shot_bar.py` | The shot bar's **two rules**, on a really shown window. Rule 1: for every shot, the handle's centre pixel and the shot's pixel in the search graph are compared on screen (`_slider_metrics` vs `ax.transData`) — worst 0.5 px, i.e. Qt's own half pixel — repeated at three window widths, after a splitter drag, and while zoomed in; stepping past the visible edge pans the graph and keeps the zoom width. Rule 2: sweeping the whole travel, every resting place is a shot that exists and none is in the hour where nothing was measured, and each end of that hole is reachable from its own side. Plus: put on a shot's own place the bar names THAT shot (60 shots at 3.3 a second, the truncation trap, checked for the tell-tale one-shot-early direction), a real `QTest` click lands on the shot drawn at that pixel, a real drag walks steadily through them, and a whole drag redraws **neither** graph. Renders `_out/shot_bar_*.png` |
| `testing/probe_shot_bar_look.py` | How the bar LOOKS, read back off a render: the handle is dark ink (luma 105) on a light groove (230), the ◀ arrow is real ink, the groove's travel covers exactly the plot box, and the marker's tag stays clear of the graph's title. Writes `_out/shot_bar_look.png` |
| `testing/test_single_browser.py` | The **Every spectrum — pick one** bar and export completeness, on synthetic regions. The bar: it only exists in that display, it lists every drawn shot of every *visible* spectrum ordered by measurement time (two regions built out of time order, on two different days, so list order cannot pass by accident), the bold curve carries the right shot's values, the tag border follows the spectrum, ◀ ▶ step exactly one and clamp, and a redraw / a hidden spectrum keeps you on the same shot rather than the same number. Export: the CSV holds ALL shots while the graph is thinned (`sp_t.MAX_SINGLE_LINES` monkey-patched to 20), a region with no resolved axis exports as `sample_number` with sample-named λ headers and a note, and a shorter region is resampled onto the export grid instead of blanked. Two traps this test itself fell into and now documents: `isVisible()` is False for every child of a widget that was never shown (use `isHidden()`), and every redraw builds NEW highlight artists, so a reference taken earlier is detached and its flags never change. |
| `testing/test_bundle_axis.py` | **One axis for everything on the graph**, on the real SPIDER numbers (2048 archived of 4096). The bundle of individual spectra, the bold picked curve and the auto-fitted From/To all land on the same femtoseconds, and the bundle's own peak is at t ≈ 0 and not at "sample 2048"; the intensity axis reaches 1.0 instead of stopping at 0.05; the empty placeholder graph's own scale is not mistaken for the user's zoom, while a real Pan/Zoom gesture IS kept across a new From/To and dropped by Reset view; and a range the spectra do not reach says so on the graph in both display modes. Renders `_out/bundle_axis.png` |
| `testing/test_shot_filter.py` | The **shot filter**: hold-forward with no sample inside the window (the normal case, not a corner), the archived TOD float dust that a bare `==` would throw away, stack/stack_ts/n alignment after a filter, the average really being the kept rows' average, the bit-for-bit restore when it goes off, the shot bar keeping the same shot while the rows renumber, the "nothing matched" wording in all five places it appears, a channel that never recorded, the background top-up for a channel added after Analyze, the live path (**filter first, then N**), the colour-by collapse, persistence (including the default-on master switch) and what `_reanalyze_all` must drop. No archiver — `_fetch_scalars` and `_shot_filter_config_path` are monkey-patched. |
| `testing/test_live_filter_order.py` | That in live mode the filter runs **before** "average last N". The reported case replayed on a made-up buffer: 300 shots of which only the oldest 50 were taken at the value asked for, N = 200 — the 50 must come back, not an empty list. Plus N trimming the matching ones and not the raw ones, the three counters keeping their separate meanings, "newer filtered out" still counting RAW shots, a value nobody was ever at, N past the buffer, an unarmed channel, and no-condition behaving exactly as before. |
| `testing/test_live_window.py` | `_live_span_ns` — the stretch Live preloads. Qt-free, with an injected clock: a past day's From kept and only the date moved, today's own From winning when today is in the pick, the last day picked winning otherwise, nothing picked and a future From both falling back to the last whole hour, a bare window read for its From, 00:04 landing on 00:00 **today**, a legacy `(date, h_from, h_to)` tuple, and the spans the >2 h question is measured on. |
| `testing/probe_live_gdd_window.py` | The reported case against the REAL archive: takes the span a 07:00 pick gives, fills the live buffer and the GDD series from the archiver exactly as the poll does, and prints how many shots each GDD setting in the window matches and which of them N draws. **Measured** 2026-09-23: 07:00 → 15:10 is 15 167 shots, GDD = 24700 matches 3256 of them. Reads hours of a waveform PV, so run it deliberately, not in a loop. |
| `testing/probe_filter_cost.py` | Why the filter computes averages lazily. Times `_compute_stats` against stack size: **9.6 s** for all seven keys on 9007 × 2048 (24 s at 4096), of which median + trimmed + the two percentiles are 7.3 s, against 0.26 s for mean + std. Run it before changing `_stat_keys_needed`. |
| `testing/probe_filter_real_archive.py` | The filter against the REAL archive, no GUI: reads a short window of the spectrum channel and of `Order2_RB`, holds the GDD forward onto every shot and prints how many shots fall at each setting. The one thing a synthetic test cannot check. Deliberately short — a raw read of a fast waveform PV over hours is what once drove this PC into swap. |
| `testing/probe_filter_look.py` | How the filter block LOOKS, read back off a render: the header text in each state, the per-condition notes, the measured widths of the numeric boxes against the text they must hold, and PNGs of the open / red / folded block — also under `main.py`'s own stylesheet, which is how the operator really sees it. |
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
from the CPVA archive. Two modes, but no mode switch: the calendar decides.
**Archive** is simply "Live off" — pick time regions in the search graph and
average the spectra inside them. **Live** is the one button (and the calendar's
own Live tick): it reads the picked window — its From time, moved onto today —
keeps polling the newest shots, and averages the last N of the ones that pass
the shot filter.

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
| `LIVE_BUF_MAX` | `20000` — rolling buffer size (`deque`). Live preloads the whole picked window, so this has to hold a lab day: **measured** 2026-09-23, the SPIDER writes ~460 shots/h, i.e. ~6400 for 07:00–21:00. The old `2000` silently dropped the morning, which is what made a filter set on a morning value report "nothing matches" |
| `DEFAULT_LIVE_N` | `100` — default "average last N" |
| `_LIVE_PRELOAD_WARN_H` | `2` h — above this, `_confirm_live_preload` asks before Live reads the window. Measured: 07:00 → 15:10 is 15 167 spectra and 55 s |
| `MAX_INDIVIDUAL_LINES` | `400` — cap when overlaying a region's individual spectra |
| `SINGLE_METHOD` / `MAX_SINGLE_LINES` | `"single"` — the `_METHODS` value of **Every spectrum**, the display that draws each measured shot instead of an average — and `3000`, the per-region cap for it. Above the cap the rows are picked with `linspace`, not `stack[::step]`: a `ceil()` step of 4 on 9007 shots drew only 2252 of the 3000 allowed. **A DRAWING cap only** — `_export_rows()` ignores it, see `_export_csv` |
| `_REGION_COLORS` | 8 default region colours |
| `_TRACE_COLORS` | 8 colours for the search-graph PV traces — a separate palette so a curve is never drawn in a selected spectrum's colour. Assigned by `_trace_colour()` from the PV's place in the PV list and looked up on every draw, so a reload, a day with no data for one PV or any redraw can never shuffle the curve colours |
| `_METHODS` | dropdown label → stat key: Mean, Median, Trimmed mean 10%, Sigma-clipped mean, **Every spectrum** (`single`) |
| `_METHOD_SHORT` | stat key → short legend text. Measured on synthetic data, the four methods differ by 0.1–0.2 % of peak on clean spectra (up to 30 % with bad shots in the region), so switching them looks like a no-op. The active method is therefore named in the graph title, every legend entry and each region's metric block — the label is the feedback |
| `_CBAR_BOX` / `_CBAR_RECT` / `_FULL_RECT` | the colour bar's fixed axes rectangle, the figure fraction left to the plot while it shows, and the whole figure when hidden. **Absolute values, never derived from the current axes size** |
| `_STEP_COLORS` | (accent, fill, border) per numbered sidebar card. 1 grey = day, 2 green = the search signal **and** the PV list it comes from (one card: the list is how you choose the signal), 3 blue = measured spectrum |
| `STAT_KEYS` | the seven averaging / spread keys `_stats_from_stack` can produce. One list, so the shot filter, `_RESULT_KEYS` and the restore path cannot disagree about what "the averages" are |
| `_FILTER_REL_EPS` | `1e-6` — slack added to the shot filter's `±`, as a fraction of the value asked for. **Measured, not chosen:** GDD is archived exactly (`24700.0`) but TOD holds `-97999.99999999999` where −98000 was set, so a bare `==` silently rejects every shot at that setting — the worst failure a filter can have, because it looks like nothing was measured |
| `_FILTER_ACCENT` / `_FILTER_ALARM` / `_FILTER_EDIT_OK` / `_FILTER_EDIT_BAD` | the filter block's teal, the red its header wears while it keeps nothing, and the two line-edit looks (a `QDoubleValidator` still passes a lone `-`, so a box can hold text that is not a number and has to say so) |
| `_PV_COL_SHOW` / `_PV_COL_LABEL` / `_PV_COL_CHAN` / `_PV_SHOW_W` | column indices of the PV table (tick box, Label, Channel) and the tick-box column width. Named because the tick box was inserted **in front of** Label — every leftover literal `0`/`1` renamed the wrong cell. 30 px so the style's check indicator is not clipped at 150 % display scaling |

### Config files (`%APPDATA%\ELI_Spectra\`)

| Path (function) | Content |
|-----------------|---------|
| `_search_pv_config_path()` → `search_pvs.json` | Current user search-PV list — `{label, channel, shown}` per PV; `shown: false` means it stays listed but is left off the search graph |
| `_preset_config_path()` → `search_presets.json` | Named PV-list presets |
| `_spec_pvs_config_path()` → `spec_pvs.json` | Spectrum Y channel, its base name, and the wavelength-axis config |
| `_layout_config_path()` → `layout.json` | Splitter sizes + manually adjusted subplot margins |
| `_shot_filter_config_path()` → `shot_filter.json` | The shot filter: `{enabled, conditions: [{label, channel, value, tol, on}]}`. `value: null` is a row waiting to be filled in and **must never read as "match 0.0"** — which is also why the boxes are line edits and not spin boxes. A missing file — or one with no `enabled` key — gives the three `ORDER_PVS` rows with the **master switch on and nothing ticked**: an armed filter with no condition keeps every shot, so typing a value is the only step left. An explicit `"enabled": false` is still honoured, because the user's own switch always wins |

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
| `_modal_length(arrs)` | The waveform length most of a set has — the one definition of "the length this set of shots is about". Anyone keeping a per-shot list beside the stack (timestamps, a filter PV's values) has to drop exactly the same rows |
| `_stats_from_stack(stack, keys=None)` | Every method of one already-stacked set; `keys` narrows what is really computed and the rest come back `None`. Exists for the shot filter, which recomputes on every keystroke: **measured** at 9.6 s for all seven on 9007 × 2048 against 0.26 s for mean + std (`testing/probe_filter_cost.py`) |
| `_compute_stats(arrs, keys=None)` | Keeps only the most common waveform length, then delegates → `mean/median/trimmed/sigma/std/p10/p90/stack/n`, so switching the method needs no refetch |
| `_hold_forward(series, ts_arr)` | A slowly-changing scalar's value **at** each shot time: the last sample at or before it, `NaN` before the first one. `side="right"`, so a sample written at exactly the shot's instant already counts. Not an interpolation — these are set points. The dispersion PVs record ~35 samples in three days (FOD once), so a short region usually holds **no** sample of its own and every value comes from the archiver's pre-window freebie, which `_fetch_scalars` keeps |
| `_match_value(vals, target, tol)` | The comparison: `|v − target| ≤ tol + _FILTER_REL_EPS·max(1,|target|)`, and **`NaN` never matches**. The `max(1,…)` floor is what makes target 0 work — a purely relative epsilon would give zero slack and reject an archived `1e-17` |
| `_parse_num(text)` | A typed number, `None` for blank *and* for text that is not yet a number. Accepts a decimal comma as well as a point (Czech keyboard) |
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
| `_fmt_window(w)` | one picked window → `2026-08-25 08:00-12:00` |
| `_import_daypicker()` | Loads the sibling `daypicker.py` **by path**, one instance per process, registered in `sys.modules` before exec. Re-exports `PickSeg`, `seg_bounds_ns`, `DayTimePicker`. Not a plain `import daypicker` on purpose: the builder's module-home check would see the same module name in two program folders and refuse to build (`Dev Tools/b_t.py`) |
| `_bg(fn)` | Runs `fn` in a daemon `threading.Thread` |
| `_fit_button(btn, extra=22)` | Minimum width measured from the button's own label. **Never `setFixedWidth` on a button holding text** — `_APP_STYLESHEET` spends ~16 px on padding + border, so a hand-picked number clipped "Change…" to "Chang…", and clipped harder at 125/150 % display scaling |
| `_step_card(number, title)` | One numbered sidebar card → `(frame, body_layout)`. Number badge + coloured left stripe; the fill and border are scoped to the frame's object name so they cannot cascade onto the table or spin boxes inside |
| `_shade(hex, factor)` / `_SET_ACCENT` / `_sub_label(text)` / `_SettingsGroup` | The **Display settings** block at the bottom of the panel: a purple (`#7a4fc0`, the Image Slider's "Image / Display" accent) `QToolButton` header with white text over a body washed in the same accent at factor 1.93 — near-white on purpose, anything stronger and the black control text stops being comfortably legible. The body's rule is scoped to `#setBody` so the controls inside keep their own white background, plus a `#setBody QLabel` rule because a label would otherwise take its colour from the inherited dark theme. Clicking the header folds the body away. `_sub_label()` is the small uppercase caption of one block inside it. Also used, in teal, for the **Shot filter** block, which is why the class has `set_title()` and `set_accent()`: the header is all that is left when a block is folded, so the filter writes its result into it. And a third time, in blue grey (`_LOAD_ACCENT` `#455A64`), for the **Loading data** roof over the three numbered step cards |

### `_TimeMap(windows)`
The compressed time axis of the search graph, and the **only** place that
converts between real time and the top graph's x. The picked windows are laid end
to end and the time between them is removed from the axis. x is **plain seconds
from the start of the first window — never a matplotlib date number**, or half of
matplotlib's date machinery would read a compressed x as a real instant.

With one window it is the identity, so the single-day graph has no separate code
path. Overlapping or touching windows are merged in `__init__`, otherwise
`from_x` would be ambiguous. Window ends are **exclusive**, the same convention
`daypicker.seg_bounds_ns` uses.

| Member | What it does |
|---|---|
| `to_x(t_ns)` | seconds on the axis, or `None` when `t` is in removed time |
| `to_x_clamped(t_ns)` | as above, but a time in a gap is pulled to the nearest window edge |
| `clip(t0, t1)` | one x-range per window the interval overlaps — region shading |
| `split_ns(t0, t1)` | the same split in absolute ns — `_on_span` |
| `contains(t_ns)` | is this instant on the axis at all |
| `window_len_ns(t_ns)` | length of the window this instant is in, 0 in removed time — lets `_keep_drag_parts` ask "how much of THAT day did the drag cover?" |
| `window_at_x(x)` | the `(start, end)` of the window under a point on the axis, `None` in the padding — the whole day a double-click marks. Next window wins on a join, as `from_x` does |
| `from_x(x)` | absolute ns. At a join, the **next** window wins |
| `xlim()` / `boundaries()` / `window_centres()` | axis limits, join positions, label anchors |
| `is_window_start(x)` | so the formatter can print the date only at a join |
| `trace(t_ns, vals)` | `(plot_x, plot_y, cur_x, cur_y)`. `plot_*` carry a **NaN break at every join** so `steps-post` cannot draw a line from Monday evening to Wednesday morning, and hold each window's last value out to its own end. `cur_*` are the same points **without** NaNs and strictly ascending, because the crosshair `searchsorted`s on them |
| `ticks(max_ticks)` | fixed tick positions. Every window gets its own start tick; past `max_ticks // 2` windows the inner clock ticks are dropped, or they print straight through the dates (`08-2412:00`) |

### Shared QSS / hint constants
`_CHK_STYLE` (`::indicator` only — never a border on the
root `QCheckBox`), `_GROUP_STYLE`, `_BTN_PRIMARY` / `_BTN_SUCCESS` / `_BTN_DANGER`,
`_TB_STYLE` and `_TB_HINTS` (matplotlib toolbar), `_APP_STYLESHEET`,
`_TIMEBAR_STYLE` (the "pick one" shot bar — `_APP_STYLESHEET` styles only the
*vertical* scroll bars and an unstyled `QSlider` inherits the platform/dark
palette, so every colour is set: `#e6e6e6` groove with a `#b4b4b4` border, a dark
`#5b6b80` handle, `#1565C0` on hover). `_SLIDER_HANDLE_W` is the handle width the
sheet asks for and the fallback for `_slider_metrics()`; `_SHOT_BAR_MAX` is the
bar's value range, deliberately far finer than its pixel width.
The calendar's own styling lives in `daypicker.py`, not here.

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

**Not gated on live mode.** F11 works while reading the archive too.

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
| `daypicker.DayTimePicker` | **Not defined here.** The day/time picker lives in `daypicker.py`, whose master copy is `Image Tools/daypicker.py`; this folder keeps a verbatim copy because the builder only bundles `.py` files from the program's own folder. It owns the calendar look and every picking rule, so Spectra cannot drift away from the Image Slider's calendar again. Opened with `allow_live=False` (Spectra has its own Live button). Output used here: `selected_windows()` → `[(start_ns, end_ns)]`, one per day, ends **exclusive**; `all_segments()` → `[PickSeg]` to reopen the same pick. `testing/test_daypicker_sync.py` fails the moment the two copies differ — copy the master over the copy, never patch one side. Spectra's own former calendar (`_CAL_STYLE`, `_WeekendDelegate`, `_make_calendar`, `DatePickerDialog`) was deleted when this landed; `main.py` still carries its own separate copies for the CSS Logger tab. |
| `PvSearchDialog` | Loads every CPVA channel once, then filters locally while typing. Multi-select → `added_pvs()`. |
| `XAxisSourceDialog` | How to build the wavelength axis for a Y channel that has no `_X` twin: copy from another PV, copy + linear transform (`x' = a·x + b`), load from a CSV / text file, or use the plain sample index. |
| `PresetEditDialog` | Named presets: preset list (Load) on the left, channel search + green selected PVs on the right; Save/Delete, `preset_loaded` signal. |
| `ExportDialog` | Export options: CSV data + graph image (png/pdf/svg). |
| `_AxisLabelsDialog` / `_AxisLimitsDialog` | Per-axis title / limit editing straight on a canvas (Auto restores autoscale). Opened from the canvas right-click menu built in `_make_canvas_panel` (Axis limits…, Axis labels…, major/minor grid, Y scale linear/log, Reset view — the hit test is DPI-proof, 125/150 % scaling used to swallow the menu). |

### SpectraWidget(QWidget)

#### State

| Attribute | Type / meaning |
|-----------|----------------|
| `_segments` | `list[daypicker.PickSeg]` — the pick as the calendar returned it, fed back in as `init_segments` so reopening shows it again |
| `_windows` | `list[(start_ns, end_ns)]` — one time window per picked day, ends exclusive. **This is what gets fetched**, and the only definition of "loaded" |
| `_tmap` | `_TimeMap` built from `_windows` — the compressed x axis. Rebuilt in `_load_day_energy` |
| `_search_pvs` | `list[(label, channel)]` — user-editable search-PV list |
| `_spec_y_pv` / `_spec_base_pv` / `_spec_x_pv` / `_x_axis_cfg` | spectrum channel + how its X axis is built (`native`/`pv`/`linear`/`csv`/`index`) |
| `_color_mode` | `"order"` \| `"gdd"` \| `"tod"` — how spectra are coloured |
| `_energy_data` | `list[(ts_ns, float)]` — search-signal series, already restricted to `_windows` by the worker |
| `_x_data` | `np.ndarray \| None` — wavelength axis, cached from the first resolve |
| `_regions` | `list[dict]` — single source of truth; each region carries everything (id, t_start, t_end, color, visible, expanded, show_individual, analyzed, mean/median/trimmed/sigma/std/p10/p90/stack/stack_ts, orders, energy_avg, energy_n, n, `_metrics`). `stack_ts[i]` is the timestamp of `stack[i]` — the same "keep only the most common length" filter `_compute_stats` applies, repeated on the times, so the CSV can name each spectrum by when it was taken. **Those keys are what the shot filter rewrites**, with the analysis's own results parked beside them in `stack_all` / `stack_ts_all` / `n_all` / `stats_all` / `orders_all` / `energy_avg_all` / `energy_n_all`, plus `scalar_series` (`{channel: [(ts, val)]}` as fetched), `shot_vals` (`{channel: ndarray(n_all)}`, held forward, aligned to the **unfiltered** times), `filter_mask` (`ndarray(bool)` or `None` = keeps everything) and `stats_keys` (which averages are really computed right now) |
| `_filter_on` / `_filter_conds` | the shot filter: master switch + `[{label, channel, value, tol, on}]`, loaded from `shot_filter.json` in `__init__` **before** `_build_ui` |
| `_filter_rows` | per-row widget refs (`chk`, `pv`, `val`, `tol`, `note`) — the same pattern as `_row_widgets`, so a readout can be rewritten without rebuilding the rows |
| `_filter_timer` / `_filter_busy` / `_filter_pending` / `_filter_fetch_err` | the debounce, the top-up-fetch lock and its queue, and `(rid, channel) → message` for a read that failed (so it is not retried on every apply) |
| `_live_scalars` | `{channel: [(ts, val)]}` polled on each live tick; live shots are filtered against it, held forward exactly as archive ones are |
| `_live_kept_n` / `_live_seen_n` / `_live_newer_dropped` / `_live_arming` | what the last `_live_shots()` saw: kept of seen, how many **newer** shots were rejected (the red curve's label), and which channels have not reported yet |
| `_color_pinned` | `(order_label, value)` when the filter has pinned the colour-by PV to one value. Recorded, not announced — `_compute_region_colors` runs inside a draw |
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
| `_single_items_cache` / `_single_pos` | the shot bar's list of individual spectra (`{rid, k, ts, label}`, oldest first) and the position in it. Rebuilt by `_rebuild_single_browser()` after every draw; the position is carried over by (rid, k), not by index |
| `_single_ts_arr` / `_single_ts_pos` | the timestamps of those shots that HAVE one, and their positions in the list. The sorted array `_nearest_shot_pos()` bisects; a shot with no `stack_ts` is left out instead of piling up at time 0 |
| `_single_hl` | `{halo, line, tag}` — the bold "this one" artists. `animated=True`, recreated by `_install_bot_cursor_artists()` after each `ax.clear()`, painted only by `_blit_bot()` |
| `_top_marker` | `{line, tag}` — the shot bar's marker on the search graph. Same contract as `_single_hl`: `animated=True`, recreated by `_install_top_cursor_artists()` (and by `_draw_top_empty()`) after each `ax.clear()`, painted only by `_blit_top()` |
| `_shot_bar_margins` / `_shot_bar_pin_pending` | the margins `_pin_shot_bar()` last set (re-setting the same ones would restart the layout for nothing) and the one-deferral guard |
| `_bot_blit_fn` / `_top_blit_fn` | each canvas's one blit closure, published by `_install_cursor` / `_install_top_cursor` so the shot bar can repaint the overlay without a full draw |
| `_region_colors_cache` | the colour map from the last `_compute_region_colors()`. The bold curve's tag border is painted long after `_redraw_spectra_now` returned and must use the same colours |

#### UI layout

```
QHBoxLayout
├── Sidebar (fixed width SIDEBAR_W = 340 px, inside one QScrollArea)
│   ├── Row: "⇢ Live mode" (checkable; green #d9f2d9 while live, red #f9dedb
│   │     while not — the same paint job as Image Slider's _refresh_live_btn_style)
│   │     + "⏹ Stop".  No Idle/Working/LIVE pill any more: it blinked, it could
│   │     not be clicked, and lbl_status + the progress bar say the same thing
│   ├── Row: "✓ Analyze" | "💾 Export" | "Average last N:" + spin box.
│   │     The N box (_g_live) is hidden unless live runs, and it used to be a
│   │     GroupBox "Live" far down the panel, between the shot filter and the
│   │     drawing settings. It is neither a filter nor a drawing setting — it
│   │     says how much of the live stream is on the graph — so it belongs with
│   │     the buttons that act on the data. MEASURED on this PC (Segoe UI 9 pt,
│   │     display at 150 %): 86 + 83 + 80 + 59 px + 3 gaps = 320 of the 336 px
│   │     inside the sidebar, so the full caption fits and Analyze keeps stretch
│   ├── the progress bar
│   ├── lbl_status
│   ├── _SettingsGroup "LOADING DATA" (blue grey #455A64, expanded by default)
│   │     — one roof over the three numbered steps, because picking a day,
│   │     picking what to search on and picking the measured channel are one
│   │     job. Loose, they took most of the panel even after the data was in;
│   │     folded, the whole job is one bar. Blue grey so it is telling apart
│   │     from the purple settings, the teal filter and the grey / green / blue
│   │     of the cards inside it
│   │   ├── Step card 1 "DAY & TIME"  — lbl_day (+ window tooltip)
│   │   │                              + "📅 Load day and time…"  (no "&" in a
│   │   │                                button label — Qt eats it as a mnemonic)
│   │   ├── Step card 2 "SEARCH BY — what I search on"
│   │   │     ├── the SELECTED PV's label + its channel + "(+N more plotted)  (N off)"
│   │   │     ├── Preset combo + inline + / ✎ / 🗑 (add / rename / delete)
│   │   │     ├── Inline channel search (fast add)
│   │   │     ├── QTableWidget [✓ | Label | Channel] (tick = drawn in the search graph;
│   │   │     │      click a row = search by it; dbl-click a label = rename)
│   │   │     │      Channel stretched to the table edge by _fit_pv_columns()
│   │   │     └── "+ Add PV…" (PvSearchDialog) / "✕ Remove"
│   │   └── Step card 3 "SPECTRUM — what I measure"
│   │         ├── the channel, large; the resolved "X: … / Y: …" pair, small
│   │         ├── "Change…"  (_fit_button, never a fixed width)
│   │         └── "Unit:" (_edit_x_unit) — fills itself in from the channel name
│   ├── Region panel — _make_region_panel(): the heading carries "⤢ Expand all"
│   │     and "✕ Clear all" (both act on the whole list), then the list itself.
│   │     The box is QSizePolicy.Maximum and the panel is NOT the stretching
│   │     widget any more — an empty list used to claim every spare pixel
│   ├── _SettingsGroup "SHOT FILTER" (teal #00695C, folded by default) —
│   │     _make_filter_panel(). NOT in Display settings: it changes what n IS.
│   │     Master tick + "N of M shots match" + one row per condition
│   │       [✓] [ PV ] [ value ] ± [ tol ] [✕]   /  its own readout line
│   │     + "+ Add condition…".  The header text IS the readout, because it is
│   │     the only part left visible when the block is folded away
│   └── _SettingsGroup "DISPLAY SETTINGS" — the panel's last block: a purple
│         header (the accent the Image Slider gives "Image / Display") over a
│         near-white tinted body, folded away by clicking the header. Holds
│         everything that only changes how the result is DRAWN:
│           · caption "GRAPH": ONE QGridLayout, labels aligned in column 0 —
│             Show (_METHODS, incl. Every spectrum) · Colour (Selection order /
│             GDD / TOD) · Normalize (None / Peak / Area) · Variation band
│             (±1σ / 10–90 pct) · Smooth + window, then "Show search graph"
│           · caption "WAVELENGTH RANGE [NM]" / "TIME RANGE [FS]" (_lbl_xrange,
│             written by _sync_x_unit_labels): From / To, width-capped +
│             trailing stretch so "To:" sits beside the first value, + auto-fit
│           · caption "COMPARE REGIONS": Show comparison curve, A, B, A−B / A÷B
└── QSplitter (Vertical)
    ├── Top panel
    │     ├── _CustomToolbar (+ the "Select" mode action)
    │     ├── Top canvas (search signal + SpanSelector)
    │     └── Shot bar (_make_shot_bar) — hidden unless the display is
    │           Every spectrum:
    │             row 1: the _ShotBar alone, its row's left/right margins
    │                    pinned to the plot box by _pin_shot_bar()
    │             row 2: [◀] [▶] [☑ Highlight] "431 of 892 · date time · Spectrum 2"
    └── Bottom canvas (averaged / live spectra + optional GDD/TOD colorbar)
```

Both canvases are built by `_make_canvas_panel(suffix)`; `_update_top_visibility()`
hides the search graph on request and restores `_split_ratio`. **The shot bar is
inside the top panel**, so minimising the search graph or switching to Live takes
the bar with it — deliberate: a bar whose whole job is to point at a place on
that graph is meaningless without it.

The bottom figure additionally gets `_cax_bot` (`fig.add_axes(_CBAR_BOX)`,
`set_in_layout(False)`, hidden). The plot's own room is steered by the tight-layout
engine's `rect` — see **The graph must not resize itself** below.

#### Key methods

| Method | Purpose |
|--------|---------|
| `_pick_day()` | `daypicker.DayTimePicker` → leaves live (`_stop_live`, the live hour is dropped) → `_windows` / `_segments` → `_load_day_energy()` |
| `_day_summary()` / `_window_tooltip()` | the Step-1 card's text and its hover list of windows |
| `_confirm_request_volume(windows)` | hours × PVs one-hour requests; above `_FETCH_WARN_REQUESTS` (400) it **asks**, and never refuses — a capped load that looked complete would be worse than a slow one |
| `_load_day_energy()` / `_on_energy_loaded()` / `_on_energy_error()` | background fetch, **per (PV, window)**, concatenated per PV; the progress bar is ranged over `len(pvs) * len(windows)`. The worker drops the "last sample before the start" that every request returns, **in the worker** — doing it in the slot meant scanning every window for every one of half a million samples with the panel frozen. `raw_newest` is carried out so a PV empty in every window can still say when it last recorded. Then `_draw_energy()` + `_install_span()` |
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
| `_refresh_pill` / `_update_stop_button` | repaint the Live switch (green while live, red while not) and enable Stop. It kept the old name: a dozen call sites, on every load and every analysis, ask for exactly this refresh |
| `_reanalyze_all(why)` / `_start_pending_reanalysis` / `_finish_analysis_ui` | keep the selections, drop stale results, re-run |
| `_install_span()` / `_on_span(xmin, xmax)` | SpanSelector → new region(s) in `_regions`, via `_tmap.from_x`. A drag crossing a join is **split into one region per window** (`_tmap.split_ns`), so two days are never averaged into one spectrum. The minimum drag is `_MIN_SPAN_S` (0.25 s of archive time) — the old `1e-9` guard was written for matplotlib date numbers and, on an axis in seconds, let every click through as a region |
| `_add_region(t_start, t_end)` | the **only** place a region dict is built and painted, so `_on_span` and `_on_top_dblclick` cannot drift apart. Does not touch the list UI — the caller does |
| `_keep_drag_parts(parts)` | drops the days a drag only clipped, returns `(kept, dropped)`. A piece survives if it holds ≥ `_EDGE_KEEP_FRAC` of the drag's **longest** piece, or covers ≥ `_FULL_DAY_FRAC` of its own day's loaded window. Two traps this shape exists for: measuring the share against the drag **total** would give each of 20 deliberately dragged days 5 % and throw them all away; and without the whole-day test, a day loaded with a 30-minute window beside a neighbour's 11 h would read as an accidental clip even when selected in full. The longest piece is always kept, so a drag never marks nothing |
| `_on_top_dblclick(event)` | double-click inside a day → that day's whole window, via `_tmap.window_at_x`. Gated on the same "Select" toggle as the SpanSelector. **Do not test `event.inaxes is self._ax_top`**: a second search PV puts `twinx` axes over the graph and the click is reported on one of those, so `_top_extra_axes` counts too and x is read back through `_ax_top`'s own transform. Connected **once** in `_make_canvas_panel` — `_install_span` runs on every redraw and would stack a handler each time. Adding nothing when that exact day is already marked is deliberate; the double-click's own press/release also reaches the SpanSelector, where `_MIN_SPAN_S` discards it |
| `_draw_top_empty` / `_draw_energy` / `_paint_region_spans` | top graph. `_paint_region_spans` shades each region through `_tmap.clip`, so removed time is never shaded and a region outside the selection paints nothing |
| `_style_time_axis(ax)` | `FixedLocator` + `FuncFormatter` put real dates and times back on the compressed axis, and tag it `ax._sp_time_axis = True`. The formatter reads the tick **position**, never its index, so labels survive a zoom. Accepted trade-off: fixed positions mean a deep zoom can leave one or two ticks — do not "fix" it with matplotlib's date locators, they would read the axis as real time |
| `_paint_window_joins(ax)` | dashed divider at each join plus the date above each block, drawn **inside** the axes (above is the title) on a white plate, and thinned past 8 windows |
| `_rebuild_regions_ui` / `_make_region_row` / `_build_region_details` / `_apply_visibility_style` | region panel |
| `_toggle_region_expanded` / `_toggle_all_expanded` / `_update_expand_all_btn` / `_toggle_region_visible` / `_toggle_region_individual` / `_delete_region` / `_clear_regions` / `_update_action_buttons` | region actions |
| `_run_analysis()` | background: per unanalysed region fetch `PV_SPEC_Y` + `PV_ENERGY` + `ORDER_PVS` → `_compute_stats()`; runs straight away, no confirmation dialog |
| `_on_analysis_progress` / `_on_analysis_done` / `_on_analysis_error` | `r.update(res)`, `analyzed=True`, rebuild UI, `_redraw_spectra()` |
| `_redraw_spectra()` / `_redraw_spectra_now()` | bottom graph: visible regions, method, colour mode, normalize, variation band, smoothing, comparison curve, live overlay, colorbar. The outer one is only the wait cursor for a heavy **Every spectrum** draw (`_single_draw_estimate() > 500`) |
| `_is_single` / `_curve_method` / `_method_label` / `_on_method_changed` | **Every spectrum** support. `_curve_method()` is the key everything that still needs ONE curve reads — metrics, `_plot_comparison`, `_auto_fit_range`, the CSV details block — and it is `mean` while the display is `single`; `r.get("single")` would be `None` and the region would silently vanish from the graph. `_on_method_changed` greys out the variation band, which describes an average that is not on screen |
| `_single_rows(stack)` / `_plot_all_spectra(...)` | which rows are **drawn** `(indices, total)`, and the drawing itself. One `LineCollection` per region, not one `ax.plot` per shot — 3000 curves × 2048 points is ~1.5 s as a collection and minutes as separate lines. Alpha and line width scale with the count; the legend gets a proxy line, because a collection at alpha 0.06 is invisible there |
| `_export_rows(stack)` | which rows the **CSV** writes: all of them. Deliberately not `_single_rows()` — the cap exists because curves take seconds to paint, and a text column costs nothing. When the two differ, the details block says `every spectrum (9007), graph drew 3000` |
| `_axis_for(x, n)` / `_curve_x(r, n)` | **the X axis n points are actually drawn against** — `_fit_x_axis(x, n)` when that yields an axis, else `np.arange(n)`. `_curve_x` is the same thing for a region. THE one place that decides it, and it took two bugs to get there. First `_export_csv` read `self._x_data` instead: a region analysed before its axis resolved had `r["x"] = None` (graph → sample numbers) while `_x_data` still held a real axis from an earlier resolve (file → nm), so the same peak sat at 2045 on screen and at −5.5 in the file. Then `_plot_all_spectra` was found still doing its own `len(x) != stack.shape[1] → np.arange`, a **bare length test** where the others call `_fit_x_axis` — so on SPIDER (2048 archived of 4096) the whole **Every spectrum** bundle was drawn on array positions while the averaged curve, the bold picked one, the metrics and the auto-fitted From/To were on femtoseconds: the bundle piled up around "2000", the picked spectrum sat at 0 fs, and the fs range then masked the bundle to a slice of its baseline (intensity axis stopping at 0.05 with peaks at 1.0). Every plotter, `_signal_span`, the live traces and the CSV now go through this helper |
| `_drawn_x_span()` / `_range_misses_data_msg()` | the graph's answer when **From/To keeps no point at all**: it names the range asked for and the range the spectra cover, instead of a blank white graph with matplotlib's invented ±0.05 axis — which reads as "the archive has nothing" and is how the axis bug above stayed invisible. Checked in `_redraw_spectra_now` with `_measured_y_range(ax) is None` (curves handed over, nothing inside the window) and on the *nothing drawn* path, where a bundle drops out one step earlier |
| `_fit_x_axis(x, n)` *(module)* | **the one rule for "does this axis fit these points".** Equal length → the axis as stored. Shorter or longer but with a **constant step** → rebuilt from its own first value and step, because the archiver stores only part of some axes: `L3-SBDP-SPIDER:TimeDomain_Int_X` holds **2048** points while its `_Y` holds **4096**, and every path used to compare lengths, find them unequal and quietly count array positions — four analysed days came out in "samples", a 33 fs pulse reported as `FWHM 18.2` and its peak as `2045`. Not uniform (a grating spectrometer's λ axis), a single point, a flat axis, a NaN in it → `None`, i.e. sample numbers: extrapolating a curved axis would invent numbers. The rebuild assumes the stored part is the **beginning** of the axis; for SPIDER that is certain, `TimeDomain_FL_Y`'s transform-limited pulse peaks exactly on point 2048, so point 2048 is t = 0 |
| `_x_is_samples(r=None)` | True while the plot is on sample numbers. Feeds the axis title (`Sample number (no measured axis resolved)`), the crosshair's unit, the CSV's `sample_number` column, the sample-named metric headers and the note at the top of the file |
| `_x_unit` / `_x_unit_source` / `_x_unit_is_manual` / `_x_names` / `_x_title` / `_fmt_x_value` / `_on_x_unit_edited` / `_x_unit_note` / `_sync_x_unit_labels` | **the unit of the X axis.** The tab was written for a grating spectrometer and hard-coded `Wavelength [nm]` / `Peak λ` / `wavelength_nm`, but the same panel is used on the SPIDER **time domain**, whose axis is femtoseconds — a pulse duration announced in nanometres is worse than no axis at all. `_guess_x_unit()` reads the channel name (`TimeDomain…` → `fs`, `SpecDomain` / `Wavelength` / `Spectrum` / `Fund` / `SHG` and anything unknown → `nm`, `THz` → `THz`), `_x_unit_kind()` turns the unit into a quantity and a symbol (`fs` → Time / t, `nm` → Wavelength / λ, unknown → X / x). **The name is all there is:** MEASURED 2026-09-24, the archiver's `metaData.units` is the empty string on every channel — the SPIDER waveforms and a plain `…:Energy` scalar alike — so the EGU route `main.py` uses for scalar units returns nothing here. A typed unit **belongs to one channel**: it is stored as `x_axis["unit"]` *together with* `x_axis["unit_for"]`, and a unit whose channel does not match is ignored, which includes every file written before 2026-09-24. That is the fix for the bug where `spec_pvs.json` held `{"base": "…TimeDomain_Int", "unit": "nm"}` and the graph called a femtosecond axis `Wavelength [nm]`. Typing the unit the channel already guesses, or emptying the box, **drops** the override rather than writing it — `editingFinished` also fires on focus-out, so storing it would let a mere click through the box freeze the displayed unit in for good. One source for the axis title, the readout, the details box, the From/To caption in the Display settings group (`_lbl_xrange`) and the CSV headers |
| `_x_unit_impossible(x, unit)` / `_x_unit_note()` | **a unit the numbers contradict.** One rule, and it is physics rather than a hunch: a wavelength cannot be zero or negative, so `nm` / `µm` on an axis running through zero is impossible. Nothing is guessed from the size or the span of the numbers — a merely plausible rule could contradict a *correct* unit, which is worse than the mislabelling it is meant to catch. Only a hand-typed unit is ever checked (the guessed one comes from the name and is right for every channel that exists), and the panel **says so without relabelling the graph**: the sentence goes into the status line beside `_x_fit_note()` and into the CSV as a `# NOTE:` row, because rewriting the label behind the operator's back would hide the very mistake he has to correct |
| `_x_fit_note()` | the sentence the status line, the channel tooltip and the CSV carry when an axis was rebuilt: *"the archive stored only 2048 of the 4096 axis points — the rest was continued at the same spacing"*. A rebuilt axis must never pass for a fully archived one |
| `_prep_curve` / `_norm_scale` / `_norm_mode` / `_band_kind` / `_smooth_win` / `_method` / `_intensity_label` | display-option helpers |
| `_plot_spectrum` / `_plot_individual` / `_plot_live_spectra` / `_plot_comparison` | the actual curves |
| `_compute_region_colors()` | `"order"` → fixed palette; `"gdd"`/`"tod"` → rainbow by Order 2/3 value + sets `_colorbar_info` |
| `_metrics_html` / `_update_metric_labels` | peak / centroid / FWHM / RMS-BW / area readout |
| `_refresh_compare_combos` | keeps the A/B region combos in sync |
| `_signal_span` / `_apply_fit_span` / `_auto_fit_range` / `_auto_fit_live_range` / `_on_x_range_edited` | wavelength-range auto-fit |
| `_on_live_clicked` / `_enter_live` / `_confirm_live_preload` / `_leave_live` / `_start_live` / `_stop_live` / `_live_tick` / `_on_live_y` | live mode. `_enter_live` asks `_live_span_ns` for the picked window's From on today, asks the user above 2 h, rewrites `_windows` / `_segments` to that open-ended stretch and sets `_archive_reload_pending`; `_stop_live` is the single way out and always runs `_leave_live`, which reads that stretch unless Stop or another job is in flight |
| `_export()` / `_export_csv()` / `_export_comparison_curve()` | CSV + graph image export. The picture goes through `_savefig_bot()`, not `fig.savefig()` |
| `_single_items()` / `_rebuild_single_browser()` / `_update_single_label()` / `_single_when()` / `_single_current()` / `_single_curve()` | the list behind the **Every spectrum — pick one** bar. `_single_items()` is one flat list over all *visible* analysed regions, sorted by `stack_ts` — so it runs across regions and across days; the entry is `{rid, k, ts, label}`. `_rebuild_single_browser()` is called at the end of `_redraw_spectra_now` (every path that changes what is on the graph goes through it) and restores the position by **(region id, stack row)**, never by its number: hiding a spectrum renumbers the list and a plain clamp would move the user onto a different shot. `_single_curve()` masks / smooths / normalises exactly like `_plot_all_spectra`, or the bold curve would sit beside the shot it names |
| `_install_single_hl_artists()` / `_single_hl_list()` / `_update_single_highlight(blit)` / `_blit_bot()` / `_savefig_bot()` | the bold curve itself — white halo + **black** line + a tag whose *border* carries the spectrum's colour. Three things worth keeping: (1) `animated=True`, so a full draw skips them and the blit background stays clean — a baked-in artist leaves a ghost on every move; (2) therefore `_savefig_bot()` turns animation off around `savefig`, or the exported picture loses the curve; (3) black, not the region's colour — measured by rendering, 3000 curves of one colour make a solid band and a bold line of that same colour inside a white halo reads as a white gap |
| `_make_shot_bar()` / `_ShotBar` *(module)* / `_slider_metrics()` *(module)* | **the bar itself.** Two rules define it: (1) *the bar and the graph share one X* — the handle's centre pixel and the shot's pixel in the graph are the SAME pixel; (2) *the bar can only stand on a real measurement* — where nothing was measured the handle cannot go. `_ShotBar` changes three things about a `QSlider`: a click jumps straight there instead of paging (the bar is a time axis, paging would need a dozen clicks to cross a day), the wheel and the arrow keys move one *shot* rather than one slider unit, and its layout row must stay empty of anything else because that row's margins are the pin. `_slider_metrics()` asks the style for `(groove_x, travel, handle_width)` — the three numbers Qt itself places a handle with, so a value and a pixel convert the same way in both directions |
| `_shot_bar_x_span()` / `_shot_bar_geom()` / `_shot_bar_value_from_ts()` / `_shot_bar_ts_from_value()` / `_nearest_shot_pos()` | **time ↔ bar.** The bar covers whatever stretch the graph is showing (`ax.get_xlim()`), so a zoom or pan keeps the alignment; the conversion goes through the **pixel** `ax.transData` draws that instant on, not through a proportion of the axis. That matters: the row's margins have to be whole pixels, so the travel can never exactly equal the plot box, and the proportional form inherited that error and grew it towards the ends (measured 1.8 px, now 0.5 px = Qt's own half pixel). `_nearest_shot_pos()` bisects `_single_ts_arr` and takes the **nearer** of the two neighbours — an at-or-before lookup lands on the shot BEFORE the one the handle was put on, every time, in the same direction |
| `_on_shot_bar_moved()` / `_go_to_shot()` / `_sync_shot_bar()` / `_step_single(±1)` / `_page_single(±1)` / `_keep_shot_in_view()` / `_on_single_hl_toggled()` | **the slots.** `_on_shot_bar_moved` never keeps the raw position: it resolves to the nearest shot and writes the handle back onto that shot's own place (`blockSignals`), which is what makes an unmeasured stretch unreachable. A move costs **one blit per graph**: measured 20 ms with 3000 × 2048 curves behind it, against 4.4 s for the full draw, and 0 full draws over a whole mouse drag (`testing/test_shot_bar.py`). `_keep_shot_in_view()` only ever does anything when the user has zoomed in: stepping onto an off-screen shot slides the graph over, same width, rather than leaving the handle stuck against the edge |
| `_schedule_shot_bar_pin()` / `_pin_shot_bar()` | **rule 1.** The bar's row gets left/right margins that put value 0 on the plot box's left edge and the top value on its right, both pulled in by the handle's own inset (`groove_x + handle_width/2`) — value 0 puts the handle's *centre* half a handle in from the groove's end, and without that the travel is short by a handle width and the alignment drifts across the bar. Driven from the top canvas's `draw_event`, which is the one hook that catches every way the plot box can move: resize, splitter drag, redraw, a per-PV Y axis appearing on the right, longer tick labels on the left. Deferred through `QTimer.singleShot(0, …)` with a one-deferral guard, because `draw_event` fires repeatedly while a window is dragged, and skipped when the margins have not changed so it cannot restart the layout for nothing |
| `_install_top_marker_artists()` / `_top_marker_list()` / `_update_top_marker(blit)` / `_blit_top()` | **the marker on the search graph** — a vertical line plus a tag naming the time, both in the selection's own colour from `_region_colors_cache` (identity-keyed, never from a place in a list). Same contract as the bold curve below: `animated=True` and painted only by the blit. The tag hangs *inside* the plot box from the top edge, not above it — the graph carries its title up there |
| `cancel_scan()` | stop everything — live, a day load, an analysis. Wired to the sidebar **Stop** button and to `CPVASuiteWindow.closeEvent`. Was dead code inside the suite: only the standalone `__main__` block called it |
| `_load_shot_filter` / `_save_shot_filter` | `shot_filter.json` I/O. A blank `value` stays `None` |
| `_active_conditions` / `_filter_channels` / `_cond_text` / `_filter_summary` | which conditions really filter (master on **and** row ticked **and** a value typed), the channels they need, and their wording for a message or a CSV note |
| `_region_filter_mask(r)` | the mask, or **`None` for "keeps everything"** — the fast path, see below |
| `_apply_shot_filter()` / `_restore_region_unfiltered(r)` | THE one place the region keys are rewritten, and the way back |
| `_stat_keys_needed()` / `_ensure_stats_for_display()` | which averages the graph is showing, and filling in one the filter pass skipped (called first in `_redraw_spectra_now`) |
| `_shot_stat(r, ch, idx)` | `(mean, count)` of one scalar over the matching shots — what `orders` / `energy_avg` become while the filter bites |
| `_filter_counts` / `_filter_bites` / `_n_of_text(r)` / `_filter_title_suffix` / `_filter_keeps_nothing_msg` / `_filter_status_line` | every number and sentence the filter puts on screen |
| `_live_shots(n_raw=None)` | the buffered live shots the filter keeps. **`n_raw` slices the raw buffer FIRST** — see below |
| `_ensure_filter_values` / `_on_filter_values` / `_start_pending_filter` | the background top-up for a channel added after Analyze |
| `_make_filter_panel` / `_make_filter_row` / `_make_filter_edit` / `_rebuild_filter_rows` / `_filter_num_width` | the block |
| `_on_filter_master_toggled` / `_on_filter_row_toggled` / `_on_filter_num_edited` / `_filter_pick_pv` / `_filter_add_condition` / `_filter_remove_condition` / `_schedule_filter` / `_apply_filter_and_redraw` | its slots |
| `_update_filter_readout` / `_update_filter_row_notes` / `_filter_row_note` / `_cond_kept_count` / `_cond_value_at_picked_shot` | the readouts, including `now 24700` — the filter PV's value at the shot the bar is standing on, which is the cheapest check that the channel, the unit and the hold-forward are all the ones the user thinks |
| `_region_count_html(r)` / `_update_region_counts()` | the details block's `# of spectra` line and the in-place rewrite of just that label |

### The archiver hands over more than the window — twice measured

Every request comes back with **one sample before its start and the first one at
or after its end**, whatever window was asked for. `_cpva_fetch_chunked` dedupes
by timestamp but never clips, and `_fetch_waveforms` / `_fetch_scalars` do not
either. Two bugs lived in that, both found while verifying the shot filter
against the real archive (2026-09-23) and both fixed:

- **A region was averaged with a shot from either side of it.** Measured: a
  five-minute window holding 60 shots came back with 62, and a window holding
  none came back with two shots from five hours away. So `n` was wrong by two,
  the average included shots the user never marked, and the shot bar could stand
  on a shot outside the region it names. `_run_analysis` now clips `wfs` to
  `[t_start, t_end)` — the exclusive end `daypicker.seg_bounds_ns` uses. The
  window averages of the energy and the orders are clipped too; the **raw
  `scalar_series` is deliberately not**, because its freebie is the value held
  forward onto the region's first shots.
- **Live buffered every shot two or three times.** Consecutive three-second polls
  overlap, and `_on_live_y` appended whatever arrived. Measured by replaying six
  real ticks: **16 buffer entries for 6 shots**. "Average last 100" was therefore
  averaging some 37 distinct shots with unequal weights, and the red "newest"
  curve was often not the newest. `_on_live_y` now skips a timestamp already in
  the buffer, and the redraw gate counts what was really added instead of what
  arrived.

`testing/test_shot_filter.py` pins both.

### The shot filter

Conditions on a scalar PV's value **at each shot's own time**: several of them,
AND-ed, each with its own tick box so it can stay configured while switched off.
Archive and Live. The user's decisions, in his words: any PV, several conditions
with tick boxes, the average recomputed from the matching shots, and live's
"Average last N" counting raw shots with the filter applied afterwards.

**It rewrites the existing keys rather than adding an accessor.** The filtered
`stack` / `stack_ts` / `n` / `mean` / … go into the very keys the tab already
reads, with the originals in `*_all`. The twenty-odd places that read them —
`_redraw_spectra_now`, `_single_items`, `_single_curve`, `_export_csv`,
`_plot_individual`, `_compute_region_colors`, `_update_metric_labels`,
`_single_draw_estimate`, `_build_region_details` — are untouched, so none of the
bugs recorded against them (`_fit_x_axis` vs a bare length test, `_curve_x` vs
`self._x_data`) can come back through a new call site, and the graph, the
metrics, the legend, the shot bar and the CSV follow the filter *by
construction*. It also keeps the blit path (`_single_curve`) reading a plain dict
value instead of the filter UI mid-paint.

**`None` mask is the fast path.** `_region_filter_mask` returns `None` when the
filter is off or keeps everything; `_apply_shot_filter` then re-points at `*_all`
and `r.update(stats_all)`. No maths, and switching the filter off gives back the
analysis's own numbers **bit for bit** — which is what makes this safe to leave
wired into a tab that worked without it. `orders` / `energy_avg` are restored
verbatim on that path too, so today's semantics are unchanged while the filter
sleeps.

**Lazy averages are not an optimisation.** `_apply_shot_filter` computes only
`_stat_keys_needed()`. Measured (`testing/probe_filter_cost.py`) on the worst
real region, 9007 shots × 2048 points: all seven keys **9.6 s** (24 s at 4096
points), of which the median, the trimmed mean and the two percentiles are 7.3 s,
against 0.26 s for mean + std. Every keystroke in the value box would otherwise
freeze the window. `_ensure_stats_for_display()` fills in whatever a new Show or
band setting asks for, and `_schedule_filter(300)` debounces typing.

**The shot bar's identity had to change.** `_rebuild_single_browser` restored the
position by `(rid, k)`, and `k` is a row index into a stack the filter rewrites —
the same physical shot comes back as a different row, so the user was moved onto
its neighbour. It now matches on `(rid, ts)` and falls back to `(rid, k)` only
for a shot with no timestamp, which cannot be identified any other way.

**Live polls the filter channels on the same tick** (`_live_tick` → a 3-tuple
payload → `_absorb_live_scalars`). Every read brings the archiver's last sample
before its window, so a set point that is never written again still reports on
every tick with nothing to track; and `_start_live` therefore needs no lookback.
`_absorb_live_scalars` keeps one sample before the oldest buffered shot when it
trims — that is the value held forward onto it. A channel not yet in
`_live_scalars` **arms** (lets everything through) instead of rejecting, or every
switching live on would flash "nothing matched"; a new scalar sample also forces a
redraw, because a late write can flip the verdict on shots already buffered.
`_x_is_samples` and `_drawn_x_span` deliberately stay **unfiltered**: one asks
about the axis length, the other reports the span the spectra really cover.

**Where "nothing matched" is said** — and it is always checked *before* the
wavelength range, because blaming the range for a filter kill names the wrong
culprit: the graph placeholder (`_filter_keeps_nothing_msg`, which also names a
channel that has no archived value at or before a selection, with the date it
first recorded), the region details line in red, the graph title, every legend
count, the status line, the block's own folded header (red accent), the CSV note
and `_export`'s "nothing to export" box.

**Two traps it opened, both closed.** `_compute_region_colors` over a zero-wide
range paints every region `cmap(0.0)`, so a filter that pins the colour-by PV
made several spectra read as one — it now falls back to the selection-order
palette and records `_color_pinned` for the status line. And
`_update_single_label` appends `· filtered`, so a bar reading `37 of 37` is not
mistaken for the whole day.

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
- **Each canvas has exactly ONE blit path**, the `_blit()` closure inside
  `_install_cursor` / `_install_top_cursor`, published as `self._bot_blit_fn` /
  `_top_blit_fn` and reached from outside via `_blit_bot()` / `_blit_top()`. It
  restores the background and then draws *both* the crosshair artists and the shot
  bar's overlay — `_single_hl_list()` below, `_top_marker_list()` above. Before
  that, the cursor, the leave handler and the bar each restored the background and
  blitted on their own, so whichever ran last wiped the other's artists off the
  graph — moving the mouse erased the bold curve, and `axes_leave_event` erased it
  again.
- `_on_draw` captures the background and then puts the bar's overlay back
  (`_update_single_highlight(blit=False)` / `_update_top_marker(blit=False)`) plus
  `_blit()`: those artists are animated, so the full draw that just finished left
  them out, and without this they would only reappear on the next mouse move.
- `_hide_all()` in both cursors hides **only the crosshair**. The bar's overlay is
  not the mouse's: leaving the graph must not take the marker or the bold curve
  with it.
- The top `_on_draw` is also where `_schedule_shot_bar_pin()` is called from —
  that is the moment `tight_layout` has settled and the plot box's pixels are
  final.

#### Pan/zoom + "Select" mode (top toolbar)
`_track_zoom_on(ax, …)` stores the user's xlim/ylim in `_*_user_*lim` so a
redraw does not reset the view; "Home" and "Reset view" clear them
(`_forget_axis_limits`), typed limits file themselves
(`_remember_axis_limits`, because typing is not a gesture — see below). The
custom **"Select"** action (top toolbar only) arms the span selector; turning on
Pan/Zoom switches Select off and suspends the span.

Two traps, both of which made this dead or worse than dead:

- **`ax.clear()` replaces `ax.callbacks` with a fresh registry.** Connecting once
  in `__init__` meant the memory was dead from the first redraw on, so a zoom was
  forgotten as soon as anything redrew. It is re-wired from
  `_install_bot_cursor_artists` / `_install_top_cursor_artists`, which already run
  after every `clear()` for the same reason; `_connect_zoom_tracking()` only does
  the top graph's first wiring (its artists do not exist until the first
  `_draw_energy`), so nothing is connected twice.
- **Matplotlib autoscales an axes while it is being DRAWN**, i.e. after
  `_*_redrawing` has been dropped, so a limit change with no user behind it would
  be filed away as "the user's zoom" and re-applied for the rest of the session.
  A change therefore only counts while Pan or Zoom is the toolbar's active tool
  (`_tb_*.mode`), and `_draw_bot_empty()` pins the placeholder's own 0…1 limits
  instead of leaving them to be invented.

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
`_chk_show_energy.toggled` and the live switch — so every live click
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

#### Live loads the picked time window (`_live_span_ns`)
Live used to preload a flat `LIVE_HISTORY_S = 600` s. It now reads the window set
in **Load day and time**, the way the Image Slider does — daypicker rule 7: the
**From** time is kept and only the DATE moves to today; **To is thrown away**,
because live has not happened yet and the window stays open. Nothing picked, or
a From still in the future, falls back to `last_hour_window()`.

`_live_span_ns(segments, windows, now=None)` is a **module function** with an
injectable clock, so the rule is testable with no widget
(`testing/test_live_window.py`). `_enter_live` calls it, rewrites `_windows` to
`[(start, now)]` and `_segments` to that one day — not `[]`, so the calendar
reopens on the window live is really streaming and the two cannot disagree.

The calendar now passes `allow_live=True`, so its own Live tick works here too:
ticking it hands the window to `_enter_live()` instead of to `_load_day_energy()`.

Above `_LIVE_PRELOAD_WARN_H = 2` hours, `_confirm_live_preload` asks first —
same shape as `_confirm_request_volume`, it only asks and never refuses.
**Measured** 2026-09-23: 07:00 → 15:10 is 15 167 spectra read in 55 s.

#### Filter first, N second (`_live_shots`)
**The order was reversed on 2026-09-23.** It used to be "last N off the raw
buffer, filter afterwards", which was harmless while live only held ten minutes
but is the whole bug once live preloads a window: with GDD stepped to 25100 at
noon, "the last 200 shots" were all of the new setting, so `GDD = 24700`
reported nothing matching although the morning was sitting in the buffer.

The filter now sweeps the **whole** buffer and `n_last` takes the last N of what
matched. Three counters come out of it, and they mean three different things:

| counter | meaning |
|---|---|
| `_live_seen_n` | raw shots in the window |
| `_live_matched_n` | how many of them match the conditions |
| `_live_kept_n` | how many the graph uses, after N |
| `_live_newer_dropped` | RAW shots after the last match — the red curve's "n newer filtered out" |

`_filter_counts` adds `_live_matched_n` / `_live_seen_n`, so "X of Y shots match"
is about the window; N is reported separately in the status line
(`averaging last 200 of 3256 matching (N=200), 15167 shots in window`).

`N` is also not just the black curve: `_redraw_spectra_now` hands the same shots
to `_plot_live_spectra` for the faint blue traces. Two further silent losses,
both reported:

- `_compute_stats` keeps only the **most common waveform length**, so the average
  can rest on fewer shots than it was handed — shown as
  "skipped (different length)" (`_live_used_n` vs `_live_slice_n`).
- Every kept shot is averaged, but only up to `MAX_INDIVIDUAL_LINES = 400` are
  *drawn* as faint traces; above that `_plot_live_spectra` decimates.

#### Re-analysis when the spectrum source changes
A region's curves belong to one channel and one wavelength axis. `_change_spec_pv`
used to rewrite `_spec_y_pv` / `_x_axis_cfg` and clear `_x_data` while leaving
`r["analyzed"] = True`, so the old channel's curves stayed on screen, the graph
silently mixed two channels, and Analyze stayed greyed out — the only way out was
Clear all plus re-marking every span.

`_reanalyze_all(why)` now keeps the selections (id, times, colour, label,
visibility, expanded) and pops `_RESULT_KEYS`, then calls `_run_analysis()`, which
already processes exactly the not-analysed regions.

`_RESULT_KEYS` therefore has to include the shot filter's caches
(`stack_all`, `stack_ts_all`, `n_all`, `stats_all`, `stats_keys`, `orders_all`,
`energy_*_all`, `scalar_series`, `shot_vals`, `filter_mask`) and `fetch_error`,
which it had been missing all along. `shot_vals` is keyed to the OLD channel's
shot times and a new channel's shots fall at different instants, so filtering the
new curves against the old times would silently keep the wrong ones.

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
3. `# Curve data` — the X column, then `<region> (<method>)` + `<region> std`
   per region, one column per live shot, and the comparison column when enabled
   (7 regions + 100 live shots = 107 curves).

In **Every spectrum** the region pair is replaced by **one column per measured
shot**, `<region> HH:MM:SS` from `stack_ts` — `_export_rows()`, i.e. **all** of
them, not `_single_rows()`. The 3000-curve cap is a drawing cap; a text column
costs nothing. When the file is fuller than the picture the details block says
`every spectrum (9007), graph drew 3000`.

Three holes closed at the same time, all of them silent before:

- **The X column is `_curve_x(region)`, not `self._x_data`.** Those two can
  disagree — a region analysed before its axis resolved has `r["x"] = None`, so
  the graph is on sample numbers while `_x_data` still holds a real axis from an
  earlier resolve. Same peak, two places (2045 vs −5.5), and the details block's
  `Peak λ 2045.00 nm` was really *sample* 2045.
- **The header names what it is.** `wavelength_nm` / `time_fs` (from the unit —
  `_x_names()`), or `sample_number` plus sample-named metric columns and a
  `# NOTE:` line, decided by `_x_is_samples()`. When the axis was **rebuilt** from
  a partially archived one, a `# NOTE:` line says so as well (`_x_fit_note()`).
- **A region whose waveform length differs from the export grid is
  `np.interp`-ed onto it**, instead of being written straight and having its tail
  padded with empty cells at `j >= len(a)`.
- Live shots that do not fit the grid are still dropped (one X column cannot
  carry two lengths), but the count is now on the live summary row:
  `individual, 3 skipped (different length)`.

---

## Dependencies
- `PySide6` — Qt widgets, signals, threading
- `numpy` — spectral math, averaging, metrics
- `matplotlib` — figures, `SpanSelector`, `FigureCanvasQTAgg`, `NavigationToolbar2QT`, colorbar
- `ssl`, `urllib` — CPVA archiver API (no certificate verification)
- `zoneinfo` — Prague timezone
- `json`, `csv` — config persistence + export
