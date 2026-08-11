# Image Tools — STRUCTURE

> Verified against source: 2026-08-05 · `main.py` 256 L · `if_t.py` 8241 L ·
> `is_t.py` 15818 L · `sf_t.py` 3251 L · `wk_t.py` 1283 L · `cpva_client.py` 788 L

## Files

| File | Description |
|------|-------------|
| `main.py` | Entry point. Starts `QApplication`, builds the main window with a `QTabWidget` (Image Finder, Image Slider, Shot Finder, Workshop). Detects the version from the exe name. |
| `if_t.py` | **Image Finder** — camera / frame selection for a day, annotated with energy and PV data from the CPVA API + CSV. |
| `is_t.py` | **Image Slider** — time-series viewer. Scrubbing, playback, pointing, spatial contrast, multi-cam, live mode. |
| `sf_t.py` | **Shot Finder** — finds frames by PV value (energy, waveplate…) in the CPVA archive. |
| `wk_t.py` | **Workshop** — editor for images handed over from the other tabs. Brightness/contrast, palettes, crop, drawing, diff against a reference, undo/redo, PNG/TIFF export. |
| `cpva_client.py` | Shared CPVA archiver client: day cache, warming, nearest-sample and look-back lookups, tri-state results. |
| `build_config.json`, `icon.ico` | Dev Tools build settings and the app icon. |
| `image_tools_diag.log` | Diagnostic log written by the Slider (`Viewer._diag_log`), not part of the app. |

> The stray `sp_t.py` copy of the Spectra program is gone — nothing here imports it.
> A line-by-line map lives in `image_tools_structure.md`.

---

## Shared constants and conventions

- Timezone: `PRAGUE` / `TZ_PRAGUE` = `ZoneInfo("Europe/Prague")` (`wk_t` still
  hardcodes +2 h — see KNOWN ISSUES)
- CPVA archiver: `https://10.78.0.57:8443/api/1.0/cpva` — SSL without certificate
  verification; all access goes through `cpva_client`
- Filename timestamps: UTC nanoseconds (`extract_ns_from_stem` /
  `parse_unix_ns_from_name`)
- Network paths: UNC `//users-L3.tier0.lcs.local` (Lab) or `Z:\` (Office); the
  archive tree is **UTC**
- Checkboxes: `_CHECKBOX_STYLE` / `_CHECKBOX_STYLE_SM` — QSS on `::indicator` only,
  never a border on `QCheckBox {}`
- 16-bit frames are normalised as `value / 65535` (`_norm16_to8_full_scale`) — the
  camera's absolute full scale, no PNG `MaxValue` metadata involved
- matplotlib toolbars are built through `_make_mpl_toolbar` so the dark palette does
  not tint the icons away

---

## cpva_client.py — shared archiver client

| Symbol | Purpose |
|--------|---------|
| `fetch_samples` / `parse_samples` / `fetch_values` / `fetch_channels` | raw REST access (`_request_json`, one pooled HTTPS connection) |
| `best_shot_ns(start, end)` | timestamp of the strongest shot in a window |
| `date_key_for_ns` / `prev_date_key` / `next_date_key` / `day_bounds_ns` / `today_key` | day keys |
| `get_day(channel, date_key)` → `DayResult` | whole-day sample cache with status + age; today's entry is refreshed with a short TTL and merged (`_merge_tail`), concurrent callers share one in-flight fetch (`_InFlight`) |
| `warm_days` / `peek_day` / `invalidate` | pre-warm, cache-only read, drop |
| `nearest_sample` / `nearest_sample_ex` / `_match_score` | nearest sample to a timestamp, with a preference direction |
| `LookupResult` / `format_lookup` | tri-state result: real value (including a genuine 0) / `n/a` (no sample) / `ERR` (fetch failed, retryable) |
| `value_at_or_before` / `_value_before_day` / `_last_at_or_before` / `invalidate_lookback` | step PVs (e.g. the waveplate `RawPos`) are archived only on change, so they are resolved by bisecting the day of the timestamp (+ previous days). The look-back cache is keyed by "last sample before the START of a day", a key fully determined by its query. |
| `lookup_near(channel, ts_ns)` | the entry point used by every tab: energy PVs match inside a ±30 s window, `STEP_CHANNELS` fall back to `value_at_or_before` |

---

## if_t.py — Image Finder

### Constants
| Constant | Value / meaning |
|----------|-----------------|
| `_IS_LAB` | hostname-based lab detection (`_detect_is_lab`) |
| `IMAGES_ROOT_BASE` | `//users-L3.tier0.lcs.local` |
| `RAMPING_CANDIDATES` / `DEFAULT_RAMPING_SOURCE` | Lab / Office ramping CSV roots |
| `ENERGY_CSV_ROOT` / `ENERGY_CSV_NAME_FMT` | `dataof%Y%b_%d` → `dataof2026Mar_24.csv` |
| `ENERGY_COLUMNS_AVAILABLE` / `_DEFAULT` (`[]`) / `_DISPLAY` | column picker data |
| `ENERGY_MATCH_TOL_S` = 120 · `ENERGY_MATCH_TOL_API_S` = 30 | CSV vs API match tolerance |
| `CPVA_SHOT_CHANNEL` / `CPVA_SBW4_CHANNEL` / `CPVA_CHANNEL_MAP` | best-shot + per-column channels |
| `MAX_SCAN_FILES` = 2000 | cap on `stat()` calls per folder |
| `ACT_MAX_GAP_S`, `MIN_SEG_ROWS`, `MIN_SEG_DURATION_S` | ramping-segment analysis |
| `CAM_33HZ` | camera numbers running at 33 Hz |
| `ENERGY_BAR_*` | the white annotation bar (height, font, colours) |
| `EMPTY_IMG_MAX_THRESHOLD` / `EMPTY_IMG_CONTRAST_MIN` | empty-frame detection |
| `GRADIENTS` / `GRADIENT_NAMES` | LUT palettes |

### Module-level helpers
| Function | Purpose |
|----------|---------|
| `_import_cpva_client()` / `_get_slider_module()` | lazy imports (frozen-aware) |
| `_cpva_fetch_samples` / `_cpva_best_shot_ns` / `_cpva_active_windows_ns` | archiver access + merged "beam active" windows from `:TotalPower` |
| `_cam_totalpower_channel(cam)` | folder name → `:TotalPower` channel |
| `_read_img_max_value(path)` | `imgMaxValue` from a PNG tEXt chunk |
| `_image_is_nonempty(path)` | `imgMaxValue` first, pixel-contrast fallback |
| `is_valid_image_file` / `extract_display_label` / `extract_folder_number` / `extract_ns_from_stem` / `convert_timestamp` / `build_new_name` | filename helpers |
| `_energy_csv_path` / `_load_energy_csv` | daily CSV → `list[_EnergyRow]` |
| `_energy_api_for_day(dt, cols, …)` | CPVA per column (thread pool) + CSV fallback |
| `_find_energy_match` / `_find_closest_per_col_value` / `_build_per_col_from_rows` | timestamp matching |
| `_format_energy_value` / `_format_energy_diff_s` | units (sbw4 ×0.749, J → mJ) and the ± time offset |
| `_annotate_image_with_energy` / `_write_annotated_with_text` / `_write_annotated_from_pil` | the white PV bar under a frame |
| `_make_lut` / `_make_binary_lut` / `_make_stepped_lut` | palettes |
| `_style_calendar` / `_make_multiselect_calendar` / `_make_mpl_toolbar` / `_hsep` / `_group_label` / `_section_label` | UI helpers |

### Classes
| Class | Purpose |
|-------|---------|
| `_WeekendDelegate` / `_CalBorderDelegate` / `_NoScrollCalendar` / `_MultiSelectDelegate` / `_NoScrollComboBox` | house calendar style (Monday-first, grey header, red weekends), wheel blocking, multi-select painting |
| `_EnergyRow` | one CSV/API row (`ts_dt`, `values`) |
| `_ThumbView` | thumbnail + circle/square/cross overlay; emits `overlay_changing` / `overlay_edited`, carries `key` / `native_size` |
| `_EnergyLoadSignals` / `_EnergyLoadTask` / `EnergyColumnDialog` | energy column picker |
| `_LoadSignals` / `_CollectSignals` / `_CompareSignals` / `_AutoHourSignals` / `_LogSignals` / `_PreviewSignals` / `_TryAgainSignals` | thread-safe signal carriers |
| **`ImageFinderWidget`** | the tab itself |
| `_MultiDaySetupDialog` | multi-day range / camera / hour picker (weekday default Mon–Fri) |
| `_PVBrowseDialog` / `PVRegionSearchDialog` | browse archiver channels, and search a day for time regions where PVs satisfy given conditions (`_PV_REGION_COLORS` paints the regions) |
| `MultiDayPreviewWindow` | result grid with palette / overlay / save / try-again |

### ImageFinderWidget — behaviour worth knowing
- `_user_has_selected_day`, `_load_gen` (generation token that cancels stale load
  workers), `_load_sig` captured as a local `_sig` before spawning a thread (GC
  protection), `_energy_cache` per day, `_energy_selected_cols`, `_auto_hour_last_day`,
  `_DEFAULT_HOUR = 14`
- **Startup:** `QTimer.singleShot(0, _auto_select_today)` → `_apply_auto_hour_for_selected_day()`
  sets hour 14 and loads immediately, while a background thread refines the hour from
  the ramping CSV (1 s timeout) and reloads only if the hour actually changed
- **Thread-safety rule:** background threads log through `_log_safe()` (signal to the
  main thread), never `_log()`; the load worker uses `_sig.log_msg.emit()`
- Scanning: `load_folders()` scans all 24 hour folders in a pool, `_get_items_cached`
  keeps a `scandir` + sampled-`stat` cache, `_nearest_file_for_ns` probes ns offsets
- Selection: `select_images_from_folder` (size/segment based),
  `_select_by_totalpower` (energy-anchored; recomputes the correct hour folder once
  the best shot is known), `_find_image_for_day_cam`
- Energy flow: pick columns → `_run_energy_lookup_async` → `_get_energy_rows_for_dt`
  (API first, CSV fallback, cached per day) → `_find_energy_match` →
  `_annotate_image_with_energy`
- Try-again: background `_try_again_run` worker, energy-anchored candidates,
  empty-frame validation, cancel button, per-cell attempt log
- `_mirror_overlay_from` live-mirrors an overlay edit to every selected thumbnail
  with centre-preserving scaling
- A/B compare: `_compare_memory` / `_align_images` / `_show_compare_window`

---

## sf_t.py — Shot Finder

### Constants
| Constant | Value / meaning |
|----------|-----------------|
| `IMAGES_ROOT_OPTIONS` / `ENERGY_CSV_ROOT_OPTIONS` | Lab / Office roots (`_images_root_for_year` picks the year folder) |
| `EXTRA_COL_MATCH_TOL_S` = 30 | tolerance for the extra (non-search) columns |
| `IMG_MATCH_TOL_NS` = 30 s | how far a frame may sit from the matched shot |
| `CPVA_HTTP_TIMEOUT` = 15 | per-channel request timeout |
| `PV_COLUMNS` / `MJ_COLUMNS` (`Back_Ref`, `pap1`) | UI labels, mJ display |
| `SBW4_TRANSMISSION` = 0.749 · `SBW4_WARNING_THRESHOLD_J` = 0.5 | |
| `_CAM_CHANNEL_RE` | camera-channel name pattern |

### Module-level helpers
`_load_csv_for_day`, `_load_api_for_day` (per-column API with CSV fallback; returns
`(merged, per_col, col_meta)` and does **not** silently fall back to CSV on an API
*error*, which used to mix sources), `_find_closest_col_value`, `_lookup_col_value`
(tri-state via `cpva.lookup_near`), `_format_value_state` / `_format_value` /
`_format_diff`, `_find_best_match`, `_folder_hour_from_prague`, `_find_hour_folder`,
`_find_image_for_ts`, `_read_img_max_value`, `_make_lut_sf` and friends.

### ShotFinderWidget
- **Search criteria** — one row per picked PV (`_rebuild_pv_rows`): ticked = filter
  (target ± tolerance), unticked = show only. PVs are added from a searchable
  dropdown over the archiver channel list (`_fetch_channel_list`,
  `_populate_pv_dropdown`, `_rank_pv_match` / `_tokens_in_order` — typed words must
  appear in order), or removed with the per-row ✕.
- **Network source** — Images (Lab/Office) + Ramping (Lab/Office) combos
  (`_on_source_changed`, `_on_csv_source_changed`)
- **Date range** — calendar + `_TimeWindowDialog`, weekday filters
- **Cameras** — searchable list of the day's cameras (`_load_cameras` scans all 24
  hours, deduplicating)
- **Results** — one row per camera × day: values, extra columns, matched image path,
  preview (`_on_selection_changed` → `_load_and_show_preview`). A cell click reveals
  the file (`explorer /select,`), a double-click opens the dialog with every in-tolerance
  shot, and each `_DayResult` persists its search-time state (`search_cols`,
  `extra_cols`, `criteria_csv`, `cam`, `col_meta`, `img_path`) so previews, saving and
  open-in-slider read the search, not the live UI.
- **Export** — `_save_results` (annotated PNGs), `_open_in_slider`, `_send_to_workshop`

---

## is_t.py — Image Slider

### Constants (selection)
- Sizing / pacing: `SLIDER_MAX`, `SCRUB_INTERVAL_MS`, `SCRUB_MAX_SIDE`,
  `FAST_SCRUB_MAX_SIDE`, `PLAY_MAX_SIDE_SLOW/FAST`, `FULL_RES_SIDE`, `CACHE_SIZE`,
  `NATIVE_CACHE_KEEP`, `PREFETCH_RADIUS_IDLE`, `PREFETCH_AHEAD_PLAY`,
  `TICK_STEP_MINUTES`, `PLAY_TICK_MS`, `AXIS_TOLERANCE_S`,
  `PLAY_EXACT_PCT_PER_S_THRESHOLD`, `SAVE_RANGE_WARN_COUNT`
- Preview layer: `PROXY_MAX_SIDE`, `PROXY_MAX_FRAMES`, `PROXY_BATCH`,
  `PROXY_WORKERS` (16), `PROXY_DRAG_WORKERS` (2, reads allowed to run during a drag),
  `PROXY_FOCUS_GRID`, `PROXY_MOTION_TOL_MAX`, `PROXY_DRAG_MS`, `PROXY_REFINE_MS`,
  `PROXY_TOPUP_MS`
- Live mode: `ONLINE_MAX_ITEMS` (per camera), `ONLINE_ACTIVE_FOLDER_COUNT` = 2,
  `ONLINE_POLL_MIN/MAX_INTERVAL_S` (0.5–5 s, `ONLINE_POLL_BACKOFF`),
  `ONLINE_WATCHER_POLL_INTERVAL_S` = 3, `WATCHER_SUSPECT_STRIKES` = 2,
  `WATCHER_RESTART_COOLDOWN_S` = 30, `CAM_LOAD_WATCHDOG_S`, `CAM_PIPELINE_GRACE_S`,
  `CAM_DOT_FRESH_S`
- Palettes: `GRADIENTS`, `GRADIENT_ID_DEFAULT` = 0 (original colours),
  `GRADIENT_ID_GRAYSCALE` = 1
- Circle calibration: `CIRCLE_*`
- Paths: `DEFAULT_OPEN_DIR`, `DEFAULT_OPEN_ROOT`, `DEFAULT_SAVE_DIR`,
  `IMAGES_ROOT_BASE`, `container_root_for_year(year)`

### Data structures
```python
Item(path, ts_ns)                     # frozen dataclass — one frame
PixCache(max_items, native_keep)      # LRU QPixmap cache; native renders capped
_ProxyTrack()                         # preview frames of one timeline, keyed by ts_ns
_RenderBC(offset, contrast, auto)     # the render params passed through the pipeline
Pdxm1GridConfig / CamLayoutConfig     # persisted overlay grid / multi-cam layout
```

### Image pipeline
`load_image_scaled(path, max_side, brighten, gradient_id, brightness_offset,
ref_image, sub_threshold, contrast, auto_bright, sub_offset, stats_out)` is the core
decode → scale → auto-stretch → reference-diff → brightness/contrast → palette path.
The enhancement chain is shared by **every** palette including `Default`: that branch
used to `return` right after the 16-bit normalization, which silently made both Auto
checkboxes and both sliders dead controls there while the preview layer still applied
them. Around it:

- `_read_image_max_sample` / `_read_tiff_max_sample` — real maximum sample value
- `_norm16_to8_full_scale` — 16-bit → 8-bit on `value / 65535` (camera-absolute).
  It replaced the `MaxValue`-tEXt × `/4095` scaling, which was only right for 12-bit
  cameras and rendered the 6–9 bit diode cams almost black.
- `_stretch_arr_f` / `_apply_stretch` (alias `_autostretch_gray`) — percentile stretch
  (Auto contrast). This is the pass that reproduces what an auto-scaling viewer such as
  ImageJ shows; the default absolute scale is deliberately darker.
- `_apply_bc` — manual contrast, then Auto brightness **or** a manual offset, in one
  buffer round-trip. Wrapped by `_apply_contrast` / `_apply_auto_brightness` /
  `_apply_brightness_offset` for the individual call sites. Brightness is an additive
  offset, contrast a multiplicative gain — never the other way round.
  - contrast pivots on the frame's **black level** (`_BLACK_PCT`), not mid-grey. With
    the mid-grey pivot a frame sitting at code ~29 went black at contrast +20, one nudge
    of the slider.
  - Auto brightness parks the frame's **median** at `_AUTO_BRIGHT_TARGET` (capped so
    the highlights stay under `_AUTO_BRIGHT_CEIL`). It used to park p99.5 at 255, which
    on these frames meant +190 and a white rectangle.
- `_img_planes` / `_img_from_planes` — extract/insert for the above; colour images are
  handled as RGB32 with the percentile anchors taken from the luma, so `Default` keeps
  RGB sources in colour and still honours the controls.
- `_stat_sample` — percentile anchors come from a strided ~250k-pixel subsample; the
  exact percentile on a native-resolution frame costs more than the whole scrub budget.
- `_gain_to_contrast_slider`, `_auto_bc_put` / `_auto_bc_get` — the side channel that
  parks the greyed-out Auto sliders on the value actually used. **Display only for both**:
  switching an Auto checkbox off restores the user's own value (`_contrast_manual`,
  `_brightness_manual`) rather than keeping what Auto put there. Contrast could not be
  handed over anyway (the slider's gain tops out at ~3.9× while a dim frame needs 5×+, so
  the parked number pins at +127); brightness could, but baking Auto's offset into the
  manual control made the checkbox impossible to undo.
- `_apply_reference_diff` + `_diff_stats_put` / `_get` — subtraction with statistics
- `_apply_lut` — RGB LUT onto Grayscale8
- `load_proxy_gray` — small grayscale array for the preview layer, returned as
  `(u8, lo, hi, mx)`: 8-bit codes on a **two-segment ramp** plus the 16-bit levels needed
  to invert it (see the proxy section below)

### Whole-window preview (proxy) layer
With live mode off the tool preloads the **entire loaded window** at
`PROXY_MAX_SIDE` in the background, so dragging the slider repaints from memory
instead of one share read + decode per position; the frame the user stops on is then
re-rendered at native resolution.

`_proxy_kick` / `_proxy_start` / `_proxy_cancel(drop)` / `_proxy_plan` (samples the
window coarse → fine) / `_proxy_focus_jobs` / `_proxy_next_batch` / `_proxy_pump` →
`_proxy_dispatch` / `_on_proxy_batch` / `_proxy_render` / `_proxy_motion_tol` /
`_proxy_try_paint(_cam)` / `_proxy_status_text` / `_schedule_refine` →
`_refine_current_frame`. Frames are keyed by `ts_ns`, not list index, so a Refresh or
a live backfill does not invalidate what is preloaded. Painting from the preview is
skipped while zoomed in or while Subtraction is on.

#### Preview storage: 8-bit codes on a two-segment ramp
`load_proxy_gray` stores **1 byte per pixel** and carries `(lo, hi, mx)` with each frame:
codes `0..PROXY_KNEE_CODE` span p0.1..p99.9 of the real 16-bit pixels, codes above the knee
span p99.9..max. `_proxy_render` inverts both segments with a 256-entry LUT and then applies
the **same two mappings in the same order** as `load_image_scaled`'s 16-bit branch
(`_stretch_arr_f` with Auto on, `_norm16_to8_full_scale` without), so a preview paint and
the refined render of the same frame agree.

Three things had to be true at once, and each rules out a simpler scheme:

- **Auto contrast must stretch the real data.** Keeping raw uint16 (what this used to do)
  achieved that but cost 2 bytes/px, so `PROXY_RAM_BUDGET_MB` bought half as many frames —
  a 4-camera 4-hour window could only ever hold every 2nd or 3rd frame, and a drag
  therefore could not show what it was dragged across however fast the rest of the pipeline
  became. Taking the percentiles at decode time, in the worker, gives the same result at
  half the size — and takes ~1 ms per tile per tick off the GUI thread (at 12 cameras that
  was ~12 ms of a 33 ms budget spent recomputing a constant).
- **One linear scale over 0..65535 is unusable.** These frames run p0.1..p99.9 ≈ 1536..2868
  out of 65535, so the picture would be about five codes wide — the harsh posterized banding
  (7 distinct greys on a PFM frame) that the uint16 storage was introduced to avoid.
- **Saturation must stay visible.** Simply clipping at p99.9 rendered a saturated spot at
  the p99.9 *level*, i.e. dark — measured 11 instead of 253, so a saturated spot read as a
  dim one for the whole drag. On a laser diagnostic that is a wrong reading, not a quality
  trade. A single reserved "≥ p99.9" code fixes the darkness but renders every pixel above
  the knee at the frame maximum (measured 234 codes too bright), so mild and saturated
  pixels become indistinguishable. Hence a ramp for the tail as well.

Measured against the full-resolution render of the same frame: Auto on within **1 code**;
Auto off within **1 code** across 99.9 % of pixels and within 15 in the highlight tail
(16 codes covering 2868..65000); saturated pixels render 252 vs 252. `PROXY_STORE_8BIT =
False` reverts to raw uint16.

#### Budget: RAM *and* sweep time
`_proxy_budget_per_track` takes the smaller of the RAM budget and
`PROXY_SWEEP_BUDGET_S × PROXY_SWEEP_FPS_EST`. Sweep time is usually the binding limit: a
preview frame costs a whole file read (`setScaledSize` saves decode CPU, not I/O) at ~100
frames/s, so 27k frames is 4.5 minutes and 180k is half an hour at *any* RAM figure.
The step is then **snapped** (`PROXY_STEP_SNAP_SLACK`) — it is `ceil(want/per)`, so being a
handful of frames over budget jumped it from 2 to 3 and left a third of the RAM unused
(measured: 846 of 1200 MB at 1-in-3, when 1-in-2 was affordable). `_proxy_side` also drops
the decoded side for many cameras (224 / 160 / 128 px at ≤4 / ≤8 / 9+), justified by the app
itself: at 12 cameras `_cam_tile_side` renders 180 px during a fast drag anyway.

Result: 4 cameras × 6712 frames (4 h) → **step 1, every frame, ~1.2 GB, 268 s sweep**.

`_diag_log` writes one line a minute to `image_tools_diag.log` with the paint counters
`prev` / `cach` / `miss` / `load` (preview, pixmap cache, preview refused, share read),
`refuse` = miss/(miss+prev), `prox` = how much of the preview is decoded, `proxMB` /
`renderMB` = what it costs, `step` per track and `tolS` = the current accepted distance.
`rss` finally works — it returned -1 on every line of every run because
`GetCurrentProcess` had no `restype`, so the `(HANDLE)-1` pseudo-handle marshalled as
`0x00000000FFFFFFFF` and `GetProcessMemoryInfo` failed with `ERROR_INVALID_HANDLE`; see
`_rss_mb`. Reproduce headlessly with `bench_drag.py` / `bench_play.py` (real offscreen
`Viewer`, real signals) — they stop `_diag_timer` themselves, and they need
`--latency-ms` (default 145) because a local PNG read is ~1 ms and at that speed none of
the concurrency limits are visible at all.

Two rules decide whether a drag is served from RAM or falls back to one share read per
camera (the "a frame a second" case):

- **Accepted distance.** `_ProxyTrack.nearest(ts, tol)` refuses a frame further than the
  tolerance. Standing still that is `ts_gap` — the spacing of the *finished* plan. While
  the user is moving it is `_proxy_motion_tol`: the spacing actually decoded so far
  (`_ProxyTrack.current_gap`), capped by **both** `PROXY_MOTION_TOL_MAX × ts_gap` and
  `PROXY_MOTION_TOL_MAX_S` seconds. The wall-clock cap is the one that matters: the
  multiple scales with the plan, so on a coarse plan it grew without limit — measured on a
  real 4-camera session at `prox=4 %`, a drag painted frames **5.4 minutes** from the
  requested moment. Whatever tolerance is used to *accept* a frame is also the one passed
  to `_cam_note_painted` and used to *judge* it. Those were two different numbers, which is
  why every tile sat red permanently: accepted under minutes, judged against seconds.
- **What is read first.** `_proxy_focus_jobs` hands out the plan's grid points outwards
  from `_proxy_cursor_ts_ns` (recorded in `_set_info_for`, in `_per_cam_display_one` which
  does not go through it, **and seeded in `_proxy_start`** — it returns nothing while the
  cursor is 0, which is exactly the state a freshly opened window is in, so the opening
  seconds of every sweep used to get no cursor-first ordering at all). `_proxy_next_batch`
  **alternates** that with the global coarse → fine walk when idle, so a coarse layer grows
  everywhere; **while moving it gives cursor-first strict priority**, because only frames
  within a second or two of the handle can be painted in the next few hundred ms. Each call
  examines at most `PROXY_FOCUS_SCAN_MAX` grid points — the scan is on the GUI thread inside
  the tick.

`_proxy_pump` keeps `PROXY_DRAG_WORKERS` reads running through a drag rather than standing
down: from the diag log of a real session, a drag asked the share for **0.9 loads/s** on a
share that does 204/s, so throttling the sweep 15× protected half a percent of capacity —
and the sweep is the only thing that frees that capacity up, so the preview never got built,
the drag fell back to loads, and the throttle looked justified. The idle grace is now set at
the interaction **edges** (`_proxy_idle_grace`, called from the release handlers and
`stop()`); it used to be re-armed from inside the dragging branch every 60 ms, so a user who
drags in bursts never accumulated enough quiet for the full sweep to run at all, and
`_on_slider_released`'s "let it run" pump was a no-op. `_proxy_covered` needs
`PROXY_COVERED_TRACK_FRAC` of *cameras* covered and discounts `_ProxyTrack.failed`
(frames that will never decode), because requiring all of them let one slow or partly
unreadable camera hold every other tile in the not-covered state — big scrub renders and a
tight load cap — indefinitely.

The axis cursor (the big blue timestamp under the slider) is moved from
`_on_slider_changed`, i.e. from the slider itself, not from the render pipeline. Driving
it from `_apply_scrub` meant it only advanced on the 33 ms scrub tick and not at all when
that tick bailed out early (same index, or the in-flight cap reached), so it lagged the
handle and froze whenever the loader was saturated. It is set from the **snapped frame's
own** `ts_ns`, so it always reads the same moment as the label burned into the frame.
The same handler arms `_keynav_debounce`, which is what renders slider moves that never
emit `sliderPressed` / `sliderReleased` — mouse wheel, arrow keys, a click on the groove.
Those used to update `pending_slider` and nothing else.

`play_timer`, `scrub_timer` and `_nav_timer` are `PreciseTimer`s. Qt's default coarse timer
snaps to the Windows 15.625 ms scheduler tick, so their 33 ms interval really fired every
46.9 ms — 21 ticks/s instead of 30, i.e. a third of every drag's and every playback's frames
were lost before any image work started.

### Per-camera navigation: `_nav_timer`
In multi-cam the shared slider is **hidden**; the user drags the master per-camera slider.
That path had no throttle at all: `_on_per_cam_value_changed` was called straight from
`QSlider.valueChanged` — once per mouse-move, up to ~125/s — and rendered every camera
synchronously on the GUI thread, each render ending in its own `_cam_refresh_stale_marks`
pass over all tiles and its own diff-stats relayout, i.e. **O(cameras²) label work per mouse
move**.

Now a move only *records* (`_nav_request` → `_nav_pending` / `_nav_frame`) and
`_per_cam_nav_tick` renders once per `NAV_TICK_MS`, resolving the slaves once
(`_per_cam_slave_targets`) and flushing the labels and diff stats once for the whole pass
(`_per_cam_display_one(defer_labels=True)`). The master slider, the slave sync, the arrows
and per-camera playback all go through it. It self-stops, which is also what makes it cover
wheel / arrow / groove moves on a per-camera row — those emit `valueChanged` with no
`sliderPressed`, so nothing would ever stop a timer started on their behalf.

It is deliberately **not** the shared `scrub_timer` / `_apply_scrub`: that drives off
`pending_slider` (the hidden slider), its multi-cam branch calls `_display_multicam_index`
which snaps every camera to one merged moment while `_per_cam_slave_targets` deliberately
leaves a slave alone when it has no frame within `SLAVE_SYNC_MAX_NS`, independent mode has
no merged position at all, and it coalesces on `idx == self.current_idx` — which
`_per_cam_display_one` writes itself, so the second tick of any drag would return early.

`_on_per_cam_pressed` also sets **`_is_scrubbing`**, which nothing on this path did before.
`_cam_tile_side` and `_current_decode_side` read it, so the motion downscale and every other
"the user is moving" optimisation were dead on the path the user actually drags.
`_navigating()` is that predicate; `_proxy_is_moving()` is the sweep's version and includes
`_per_cam_scrubbing_cam`.

### Per-camera load concurrency
`_cam_inflight_at[cam]` is `{req_id: launch_monotonic}` and `_cam_inflight_depth()` bounds
it. This replaced a **boolean** `_cam_busy[cam]`: one read at a time per camera, and at
130-160 ms per read that is a hard ceiling of ~7 frames/s per tile however idle the machine
is — the real reason the cameras appeared to take turns. Depth 4 gives ~27/s. All tiles now
share **one** `_cam_pool` of `CAM_POOL_THREADS`, created once; it used to be one 2-thread
pool per camera rebuilt on every camera (re)load, so pools and worker threads accumulated
for the life of the process and a slow camera's threads sat idle while its neighbour's queue
grew. Fairness is the depth cap, not a partition of the threads. `_reset_cam_pipeline` and
`_cam_load_watchdog` release by **per-load age** — with one boolean per camera a healthy new
load protected a hung old one indefinitely.

`_on_cam_loaded`'s non-live paint test is `key[0] == want_key[0]` ("is this still the frame
the tile wants?"). It was `idx >= self._cam_current_idx[cam]`, and `_cam_current_idx` is
written at *request* time, so during a drag the request always ran ahead of a 145 ms load
and nearly every finished decode was paid for and thrown away; dragging **backwards** was
worse, since the correct frame has a lower index and was rejected outright. A superseded
frame is still painted when it lies *between* what is on screen and the target, which is
what makes a tile animate through a drag rather than jump — with depth > 1 several
consecutive positions decode at once and do not finish in order. The live auto-follow branch
keeps its monotonic rule.

Measured with `bench_drag.py` (4 cameras × 900 frames, `--latency-ms 145`, one pass over the
whole window, cold preview) — `shown/asked` is distinct dragged-through frames that actually
reached the screen:

| | before | after |
|---|---|---|
| paints/s per camera | 5.0 | 8.5 (cold) / every requested position (warm) |
| **frames shown of frames dragged across** | **12 %** (one camera 2 %) | **88 %** cold, **100 %** warm |
| painted-vs-requested, p95 | 18.5 s | 4.8 s cold, **0.000 s** warm |
| painted-vs-requested, worst | 259 s | 9.4 s cold, **0.000 s** warm |
| tiles marked stale (red) | ~30 of 33 paints | 0 warm |
| per-camera spread | 94 % | 98-100 % |

Playback (`bench_play.py`, 4 × 600, 0.5 / 1 / 5 %/s): 16 ms tick, spread 100 %, zero
refusals, zero stale, stride below 1 at every offered speed so nothing is skipped.

### Workers (QRunnable / Thread)
| Class | Signals | Purpose |
|-------|---------|---------|
| `LoadTask` | `LoaderSignals` | decode one frame |
| `ScanTask` | `ScanSignals` | walk folders → `Item` list |
| `RefreshScanTask` | `RefreshScanSignals` | incremental rescan |
| `SaveRangeTask` | `SaveRangeSignals` | batch save A→B with overlay + PV bar |
| `PointingAnalysisTask` | `PointingAnalysisSignals` | centroid per frame |
| `_SCTask` | `_SCSignals` | spatial contrast for one frame |
| `_ProxyTask` | `_ProxySignals` | one batch of preview frames |
| `_CamPollTask` | `_CamPollSignals` | poll one camera's folders (live) |
| `_DirWatcher` | `_DirWatchSignals` | Win32 `ReadDirectoryChangesW` watcher |

### Time / folder helpers
`parse_unix_ns_from_name`, `_dt_from_sec` / `_dt_from_ns`, `fmt_hhmm_from_ns`,
`fmt_hhmmss_ms_from_ns`, `fmt_prague_full_from_ns`, `prague_stamp_for_filename`,
`replace_unix_ns_with_prague_in_filename`, `ns_from_dt`, `floor_to_hour`,
`axis_from_hour_folder_exact` / `axis_from_any_folder`,
`folder_hour_from_prague_hour`, `_cam_folder_time_key`, `active_scan_folders`,
`poll_scan_folders`, `_is_dir_quiet` / `_dir_access_error` /
`_camera_folder_problem` / `_probe_hour_folder`, `_strip_cam_name` /
`_cam_short_label` / `_cam_aspect_hint`.

**Window model:** a pick is a list of `PickSeg`; `seg_bounds_ns` treats **To as the
EXCLUSIVE end** (12:00–13:00 is exactly one hour and touches only the 12 h folder),
`hour_end_hm` builds that end, `utc_hour_cells_for_window` / `hour_dirs_for_windows`
translate a Prague window into UTC hour folders (Prague 00:30 → the previous day's
22/23 folder), and `cameras_for_windows` returns the camera **union over every
window** plus a status (`""` / `no_data` / `error`).

### Widgets and dialogs
| Class | Purpose |
|-------|---------|
| `ImageView` | frame display, overlays (cross/circle/square, SC top-N points, PV bar, cam label + timestamp, green `Ref:` badge via `cam_ref_text`), zoom, calibration |
| `CameraView` / `MultiCameraGrid` | one camera tile / the multi-cam grid; `_FreeLayoutContainer` and `_JustifiedRowsContainer` are the two layout backends |
| `compute_justified_layout` + `CamLayoutEntry` / `CamLayoutConfig` / `LayoutConfigDialog` / `_LayoutCanvasWidget` | gallery-style packing that maximises total image area (each tile reserves the label bar, so no letterboxing), plus a drag-and-drop free layout editor |
| `remember_cam_aspect` / `_cam_aspect_hint` (`cam_aspects.json`) | per-camera frame aspect, learned from the first frame and persisted. The packing needs an aspect *before* any frame is loaded; the name hint only knows "portrait diode array vs square", so a rebuild — every time-window rescan recreates all tiles — used to pack for square tiles and the layout visibly jumped when the frames arrived. `ImageView.frame_aspect_changed` re-runs the packing when a camera's real aspect turns out different; overhead changes (label font, `Ref:` badge) go through `MultiCameraGrid.refresh_auto_layout` |
| `Pdxm1GridConfig` / `Pdxm1GridConfigDialog` / `_GridPreviewWidget` / `get_pdxm1_grid_config` / `_cam_type_key` | the PDxM1/PDxM2 diode grid overlay; line positions are stored as absolute image fractions so each line is independent, and PD cameras of the same type share one config |
| `PointingPanel` | matplotlib scatter + histogram + path, click-to-jump, live replay, region delete |
| `_SCHistogramWidget` / `_SCHistogramDialog` / `_SCExclusionEditor` / `_SCExclusionCanvas` / `_SCValueLabel` / `_SCPreviewLabel` | spatial-contrast threshold, exclusion regions, preview |
| `TickBar` | time axis under the slider — ticks, A/B marks, `dd.mm` labels at midnight crossings |
| `DatePickerDialog` + `_DayTimeDialog` + `_make_multiselect_calendar` | see below |
| `CameraPickerDialog` (+ `_CamLoaderSignals`) | camera selection + presets; the list is the union over every window of the pick |
| `LazyDirModel` / `_DirItem` / `FolderPickerDialog` | lazy network folder tree |
| `CollapsibleSection` | the sidebar's collapsible sections (accent stripe + ▾/▸), state persisted |
| `_CamSliderRow` | per-camera master radio + slider |
| `_PvOverlayPanel` | floating, draggable PV panel |
| `PopupBelowComboBox` | combo whose popup always opens below |

**`DatePickerDialog`** — one house-style multi-select calendar plus `QTimeEdit`
From/To (minute resolution) and two mutually exclusive multi-day modes: "day → day"
range (two clicks) and one time window per day ("Add day"). It opens in **Now** mode
(today, `hh:00`–`hh+1:00`); the Now button and checkbox share `_apply_now_window()`,
both leave the multi-day modes, and picking another day switches Now off. From/To
can never collide — `_on_times_changed` pushes the other field by an hour. Multi-day
selections preset every day to `_MULTIDAY_FROM`–`_MULTIDAY_TO` (07:00–21:00); the ⚙
column opens `_DayTimeDialog` for a single day, and that override lives in
`_day_overrides`, survives rebuilds and global From/To changes, and is marked `*`.

### Viewer(QWidget) — method groups
| Group | Methods |
|-------|---------|
| init / UI | `__init__`, `_build_ui`, `_load_ui_state` / `_save_ui_state`, `_on_section_toggled` / `_set_all_sections`, `_diag_log`, `resizeEvent`, `_set_busy` |
| overlays | `_on_reset_zoom`, `_toggle_draw_mode`, `_refresh_draw_btns`, `_remove_all_overlays`, `_open_overlay_settings` / `_apply_overlay_settings`, `calibrate_circle/cross/square`, `_sync_overlay_checkboxes_from_iv`, `_on_overlay_changed` |
| PV | `_open_pv_config`, `_pv_rebuild_table`, `_pv_trigger_fetch(_now)`, `_pv_current_ts`, `_pv_is_pending`, `_pv_force_refresh`, `_pv_on_result`, `_pv_update_overlay`, `_open_pv_overlay_settings`, `_pv_text` |
| multi-cam | `_is_multi_cam`, `_switch_to_multi/single_view`, `_build_per_cam_sliders`, `_on_per_cam_*`, `_per_cam_display_one`, `_start_cam_load`, `_reset_cam_pipeline`, `_cam_load_watchdog`, `_per_cam_sync_slaves`, `_per_cam_step`, `_live_advance_cam`, `_setup_multi_cam`, `_on_multicam_selected`, `_redraw_cam_in_place` |
| live mode | `_on_auto_follow_toggled`, `_ensure_dir_watcher` / `_watcher_strike` / `_stop_dir_watchers` / `_prune_dir_watchers` / `_on_dir_watch_new_file`, `_start/_stop_online_mode`, `_online_poll`, `_online_poll_single_bg` / `_multi`, `_merge_single_new_items`, `_merge_items_by_ts`, `_extend_axis_to_items`, `_restore_full_history` / `_merge_restored_history`, `_rebuild_shared_items_from_cams`, `_extend_shared_timeline_from_cams`, `_on_online_blink` |
| open / scan | `open_folder`, `_start_multi_cam_scan`, `_on_multi_scan_all_done`, `open_by_date`, `_reload_with_last_cameras`, `auto_start_online`, `open_folder_path`, `receive_external_folder`, `open_file_list`, `refresh_folder`, `_refresh_multi_cam`, `_start_scan`, `cancel_scan`, `_on_scan_*`, `_choose_axis`, `_in_ts_windows` / `_filter_to_ts_windows`, `_hard_reset_runtime`, `_reset_ui_for_new_scan` |
| brightness / subtract | `_bc`, `_refresh_auto_bc_sliders`, `_on_brightness_slider_changed`, `_on_contrast_slider_changed`, `_on_contrast_auto_changed`, `_on_bright_auto_changed`, `_apply_brightness_debounced`, `_load_raw_arr`, `_ref_arr_for` / `_cam_ref_arr_for`, `_set_reference_frame`, `_has_reference`, `_set_ref_status` / `_refresh_ref_warning`, `_on_subtract_changed`, `_update_diff_stats` / `_update_cam_diff_stats`, `_on_gradient_changed` |
| slider ↔ time | `_slider_to_time_ns`, `_slider_to_index`, `_index_to_slider_value`, `_time_to_slider_value`, `_time_to_nearest_index`, `_set_info_for` |
| display / load | `_on_slider_pressed/changed/released`, `_apply_scrub`, `_load_or_cache`, `_adaptive_index_step`, `_display_index`, `_display_exact_index`, `_display_multicam_at_time` / `_index`, `_on_cam_loaded`, `_request_display_target`, `_drain_deferred_display`, `_request_pixmap`, `_prefetch_idle` / `_playish`, `_on_loaded` |
| playback | `play`, `stop`, `_autoplay_step`, `_play_show`, `step_frame`, `keyPressEvent`, `_current_play_pct_per_s`, `_play_is_exact`, `_update_motion_speed`, `_adaptive_stride`, `_current_decode_side` |
| focus / watcher | `_toggle_focus_mode` (F11 — hides everything but the image, title bar removed via Win32 so the HWND survives), `_toggle_watcher_mode` (Ctrl+F11 — edge-to-edge wall display), `_win32_set_title_bar`, `eventFilter` |
| timestamps | `_save_current_timestamp`, `_goto_saved_timestamp`, `_clear_timestamps` |
| pointing | `run_pointing_analysis`, `_cancel_pointing`, `_on_pointing_progress/cancelled/finished`, `_on_pointing_live_toggled`, `_start/_stop_pointing_replay`, `_pointing_replay_step`, `_save_pointing_plot`, `_toggle_pointing_path`, `_toggle_pointing_select`, `_on_pointing_region_deleted`, `_restore_pointing_points`, `_on_pointing_point_clicked` |
| spatial contrast | `_sc_set_enabled`, `_on_sc_threshold_changed`, `_run_sc_auto_threshold`, `_open_sc_histogram`, `_open_sc_exclusion_editor`, `_on_sc_exclusion_set`, `_run_spatial_contrast`, `_on_sc_preview` / `_update_sc_preview`, `_on_sc_finished`, `_on_sc_topn_changed`, `_on_sc_marker_style_changed`, `_update_sc_topn_overlay` |
| marks / save | `set_mark_a/b`, `clear_marks`, `_apply_marks_to_tickbar`, `_update_range_ui`, `save_around_current`, `save_current_with_overlay`, `_get_cam_frame_for_save`, `_render_cam_frame`, `_save_multicam_current`, `_send_to_workshop`, `save_current`, `_save_multicam_range`, `save_range`, `_show_save_range_progress_dialog`, `_on_save_progress/finished`, `_pv_text_for_ts` / `_pv_prefetch_texts` / `_pv_save_append_bar` |

**Palettes:** index 0 = Default (original colours), 1 = Grayscale, 2+ = LUT.
**Saving:** Default → `shutil.copy2`; anything else → `load_image_scaled` + save;
always `_copy_metadata_into_png`.

---

## wk_t.py — Workshop

### Data structures
```python
class _WorkshopSlot:          # one received image
    source_arr / current_arr  # original + edited
    undo_stack / redo_stack   # capped by _SLOT_UNDO_LIMIT = 30
    source_path
class WorkshopCanvas(QWidget):
    TOOL_NONE / BRUSH / ERASER / LINE / RECT / TEXT / CROP / EYEDROPPER
```

### WorkshopWidget(QWidget)
Receives images from the other tabs through `receive_image(arr, label, source_path)`.

Left panel: info label, slot combo + Remove / Clear All, tool selector, draw colour
and brush/line width, Brightness / Contrast (+ Reset, Auto B/C, Apply), palettes,
reference diff (load ref, subtract, absolute), Undo / Redo / Reset to Source,
Save PNG / Save TIFF.

Key methods: `_activate_slot`, `_do_diff(absolute)`, `_apply_bright_contrast`,
`_auto_bright_contrast`, `_on_palette_changed`, `_save(fmt)`; helpers
`_np_to_qimage` / `_qimage_to_np` / `_arr_to_pil`, `_build_save_stem`, `_bresenham`.

---

## Dependencies
- `PySide6` — widgets, signals, threading
- `numpy`, `Pillow` — image processing
- `matplotlib` — PointingPanel, SC histogram (always via `_make_mpl_toolbar`)
- `scipy` — SC hole filling (optional)
- `zoneinfo` — Prague timezone
- `ssl`, `urllib` / `http.client` — CPVA archiver (no certificate verification)

---

## KNOWN ISSUES

### Functional
- **wk_t** — `receive_image` forces uint8 without scaling, so 16-bit input is
  truncated; `_redo` exists but is not wired to any button or shortcut.
- **wk_t** — `_TZ_PRAGUE` is hardcoded to +2 h, so in winter (CET = +1) the
  timestamps in saved filenames are an hour off.

### Efficiency / responsiveness
- Network IO and PNG encoding still run on the main thread in some if_t / sf_t
  save / try-again / open-in-slider paths → the UI freezes on a slow share.
- No cached directory listing: `if_t.load_folders` and `sf_t._load_cameras` rescan
  all 24 hour folders on every change of hour / source / date.
- The sf_t search and camera-load workers have no cancel/generation token, so a
  stale run can still overwrite the table.

### Duplication / dead code
- if_t: `MIN_FULL_FILES`, `_range_gen`, ignored `select_images_from_folder`
  parameters, three near-duplicate annotation font/wrap routines.
- is_t: `_LazyDirModel` stub (`pass`), overlay-draw code copied per view.
- sf_t: `_setup_calendar` duplicates the calendar factory; legacy hidden widgets.
- Frequent `except Exception: pass` hides real errors (notably `ScanTask.run`,
  which wraps the whole scan).
