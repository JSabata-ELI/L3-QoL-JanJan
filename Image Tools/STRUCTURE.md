# Image Tools — STRUCTURE

> Verified against source: 2026-08-19 · `main.py` 275 L · `if_t.py` 8627 L ·
> `is_t.py` 23916 L · `sf_t.py` 3637 L · `wk_t.py` 3781 L · `cpva_client.py` 1316 L ·
> `img_scale.py` 640 L

## Files

| File | Description |
|------|-------------|
| `main.py` | Entry point. Starts `QApplication`, builds the main window with a `QTabWidget` (Image Finder, Image Slider, Shot Finder, Workshop). Detects the version from the exe name. |
| `if_t.py` | **Image Finder** — camera / frame selection for a day, annotated with energy and PV data from the CPVA API + CSV. |
| `is_t.py` | **Image Slider** — time-series viewer. Scrubbing, playback, pointing, spatial contrast, multi-cam, live mode. |
| `sf_t.py` | **Shot Finder** — finds frames by PV value (energy, waveplate…) in the CPVA archive. |
| `wk_t.py` | **Workshop** — look at, measure and annotate images handed over from the other tabs. Magnify to a chosen spot, non-destructive brightness/contrast/gamma/palette, histogram, region statistics and profiles in real counts, vector annotations, rotate/flip/resize/crop, reference subtraction, comparison views, PNG/TIFF export of a copy. |
| `cpva_client.py` | Shared CPVA archiver client: day cache, warming, nearest-sample and look-back lookups, tri-state results. |
| `img_scale.py` | **What an intensity means** — the ONE owner of the absolute scale (`to_absolute_u8` / `to_u8`), the gamma curve (`gamma_from_slider` / `auto_gamma` / `gamma_for_median`), the explicit per-frame stretch (`stretch_u8`, `percentile_window`), raw-counts recovery (`derive_factor` / `to_counts`) and the frame metadata behind the readout (`meta_from_info`, `FrameMeta.scale_note`). Pure numpy/PIL, no Qt; every tab loads it through the `_import_img_scale()` sibling-import so all four share one instance. |
| `test_scale_invariance.py` | Checks the archive's storage rule on real frames: `stored_max / MaxValue` → a `65535/(2**bits-1)` factor, that same bracket predicted from `MaxValue` alone (`bits_from_max_value`, the rule the preview layer relies on), and unsaturated frames exist. Also reports which cameras straddle a bracket — i.e. would flicker without the reference range. Run it from the lab against a camera-hour or a day; exit 1 on a violation. Not shipped (`test_` prefix). |
| `build_config.json`, `icon.ico` | Dev Tools build settings and the app icon. |
| `image_tools_diag.log` | Diagnostic log written by the Slider (`Viewer._diag_log`), not part of the app. Also carries `dots=G../R..:<reason>` and the `cpva …` counters, so a transient red dot or a missing PV leaves evidence behind. |
| `bench_drag.py`, `bench_play.py`, `bench_live_dot.py`, `bench_pv_wait.py`, `bench_pv_live_multi.py`, `bench_master_sync.py`, `bench_live_slave_refresh.py`, `bench_live_pv_load.py`, `test_pv_resilience.py`, `bench_common.py` | Headless offscreen harnesses driving a real `Viewer`: the PV panel surviving a wedged archiver fetch and a dead trigger chain (`test_pv_resilience.py` — the freeze that used to need a restart), drag scheduling, playback, the live refresh-dot verdict, the PV panel waiting for the archiver instead of trailing the picture by a shot (single-cam, then the same promise in **live multi-cam**, fake transport, no network), every per-camera slider handle following the master, **every camera repainting on every shot in live mode** (mismatched cadences, the toggle, and the one camera that must stay put), and whether the PV panel starves the PICTURES under a live load (both the 145 ms share read and a 250 ms archiver round-trip simulated — with neither, nothing can starve anything). Not shipped. |
| `bench_live_rollover.py` | The one live fault none of the others can reach: **following the archive across a UTC hour rollover**. Every case in `bench_live_dot.py` lives in one flat folder, so `poll_scan_folders` picking the newest hour and the `_probe_hour_folder` discovery of the next one are never exercised. A rollover failure is **silent** — nothing raises, nothing hangs, and `_live_health` reads "no new images" as a healthy idle source — so the dot stays green while the app has stopped following the archive. Uses the real …/YYYY/M/D/H/CAM layout with unpadded names; opens on the previous UTC hour and creates the current one, because `_probe_hour_folder` refuses a candidate whose hour is still in the future. `--cams 3` runs the multi-cam grid path. |
| `test_day_split.py` | Pins `cpva_client.fetch_samples_split`: an oversize window is halved until it fits and `get_day` still returns **every** sample; an error splitting cannot fix (4xx, bad JSON) is raised at once, with no split storm; a window already at the floor is not split; `CpvaBusyError` (our own pool, archiver never reached) is never split. Fake transport — no network, no share. |
| `sort_by_camera.py` | Standalone utility, not part of the app: takes the per-shot folders the camera screenshot tool writes (`<source>/DDMMYYYY_HHMMSS/`) and re-files them into `<source>/final/<CAMERA>/`, one folder per camera. |
| `HANDOFF_02072026.md` | Historical hand-over note from 2026-07-02. Kept for context; `STRUCTURE.md` and `image_tools_structure.md` are the current documents. |

> The stray `sp_t.py` copy of the Spectra program is gone — nothing here imports it.
> A line-by-line map lives in `image_tools_structure.md`.

---

## Shared constants and conventions

- Timezone: `PRAGUE` / `TZ_PRAGUE` = `ZoneInfo("Europe/Prague")` in every module
- CPVA archiver: `https://10.78.0.57:8443/api/1.0/cpva` — SSL without certificate
  verification; all access goes through `cpva_client`
- Filename timestamps: UTC nanoseconds (`extract_ns_from_stem` /
  `parse_unix_ns_from_name`)
- Network paths: UNC `//users-L3.tier0.lcs.local` (Lab) or `Z:\` (Office); the
  archive tree is **UTC**
- Checkboxes: `_CHECKBOX_STYLE` / `_CHECKBOX_STYLE_SM` — QSS on `::indicator` only,
  never a border on `QCheckBox {}`
- **Intensity scale (all four tabs, `img_scale.py`).** The archiver stores
  `stored = raw_counts × 65535/(2**bits − 1)` — a factor that is fixed *within* a frame,
  not a per-frame normalisation onto the full range. Measured on the PD reference frames:
  a warmup frame with `MaxValue` 3378 stores a maximum of 54061 (= 3378 × 16.0037), not
  65535, and dividing back returns exactly 3378.
  **`bits` is NOT the camera's — it is the power-of-two bracket of that frame's own peak**,
  `ceil(log2(MaxValue+1))` (`bits_from_max_value`). Measured 2026-08-18 on 150 frames from
  six cameras: the bracket predicted from `MaxValue` alone matches the factor recovered
  from the pixels on every frame, 0 exceptions. So `value / 65535` is **not** a stable
  scale: C03-081-PCW3NF peaks on 1023/1024 counts and flipped ×64.06 / ×32.02 every few
  seconds, which doubled and halved its picture (dark orange ↔ green) at unchanged shot
  energy while the frames were identical in counts (median 68, p99.9 ≈ 685).
  The display therefore renders `counts / (2**ref_bits − 1)`, where `ref_bits` is the
  largest bracket ever seen for that camera — learned, monotone, and remembered in
  `%APPDATA%\ELI_ImageTools\cam_depths.json` (`reference_bits`,
  `full_scale_for_frame`, `full_scale_for_pil`). It is expressed as a denominator, not as
  pixel arithmetic: `display_full_scale(frame_bits, ref_bits)` is **exactly 65535** when
  the two agree, so any camera that does not straddle a bracket renders bit-identically to
  before. A growth bumps `reference_generation()`, which every pixmap cache must honour —
  `is_t._check_scale_generation` is called from the three load handlers.
  The factor is also what prints counts, recovered per frame as `stored_max / MaxValue`
  (`derive_factor`).
  `MaxValue` (PNG tEXt) is the **peak of that frame in raw counts**, not the sensor's
  range — it reads 4095 only when the sensor is genuinely saturated. Never divide by it.
  Verify the rule with `test_scale_invariance.py` after any archiver change; it also
  reports which cameras straddle a bracket.
  Measured 2026-08-17 on `2026/8/14/12` (`test_scale_invariance.py`, 0 violations):
  **PFM13NF = 8-bit, PAM10NF = 11-bit, PASF1NF = 10-bit** — three cameras, three depths,
  none of them 12-bit, all on the same fixed-factor rule. That is why the old
  `MaxValue`-based path was not a cosmetic bug: it rendered PFM13 at mean code **3 of
  255** (pure black) where the absolute scale gives 55, PASF1 at 5 instead of 21, PAM10
  at 29 instead of 58. Do not assume 12-bit anywhere.
- **We do NOT match the lab (IMAQ/LabVIEW) display, on purpose** — settled 2026-08-17 by
  comparing the three cameras above against the lab screens. The lab divides by each
  camera's *configured* depth, and the configuration has drifted: it agrees with our
  absolute scale on PASF1 (correctly configured), renders PAM10 ~2× too dark, and PFM13
  **~16× too dark** (configured 12-bit, sensor running 8-bit — a frame peaking at 83 % of
  its own full scale arrives on the lab screen as mean code 3 of 255).
  No static per-camera depth setting can be right, because the depth changes between
  frames of the same camera; we derive it per frame instead. So where we differ from the
  lab, the lab is the one throwing the picture away — matching it would mean reproducing
  a stale config and giving up "one palette colour = one intensity". The readout under
  the preview names the depth, so a discrepancy against a lab screen is explainable
  rather than mysterious.
- **Gamma** (`img_scale.to_absolute_u8(..., gamma=)`) is the answer to "the absolute scale
  is right but the frame is dark" that does NOT cost comparability: `code = 255 ×
  (value/65535) ** gamma` depends only on the pixel value, so one colour is still one
  intensity — the mapping stays a monotone bijection of the absolute value, just not a
  straight line. What it costs is that equal count differences stop *looking* equally big,
  which is why anything other than 1.00 is named in the readout. Slider units are integer
  percent (100 = linear) so the value is exact in a cache key; `0` is the Auto sentinel.
  Present in all three tabs (`Gamma` slider + `Auto` + `↺`), default 1.00. Working point
  on these cameras is 0.50 (PFM13/PAM10/PASF1 mean code 55/58/21 → 116/120/73).
  `Auto gamma` lands the frame's MEDIAN at `AUTO_GAMMA_TARGET` (45 %) — per-frame, so it
  gives up comparability like Auto contrast does, and the value it picked is parked on the
  greyed-out slider.
- Per-frame mapping happens in exactly five places, all deliberate and all visible: the
  `Auto stretch` and `Auto` gamma switches in the Finder / Shot Finder / Slider,
  `Auto contrast` / `Auto brightness` in the Slider, the Workshop's Auto-BC button, and
  the `Binary` (min..max) / `False Colors` (p0.5..p99.5) palettes. Everything else is
  absolute.
- Auto contrast and gamma are mutually exclusive by design: the percentile stretch already
  sets both ends of the frame, so gamma would be a second correction fighting over the
  same pixels. `_bc()` drops gamma and `_sync_bc_controls_enabled` greys the whole row.
- matplotlib toolbars are built through `_make_mpl_toolbar` so the dark palette does
  not tint the icons away

---

## cpva_client.py — shared archiver client

| Symbol | Purpose |
|--------|---------|
| `fetch_samples` / `parse_samples` / `fetch_values` / `fetch_values_ex` / `fetch_channels` | raw REST access (`_request_json`, one pooled HTTPS connection). `fetch_values_ex` also returns **which** name produced the samples — the bare channel or its `.value` alias — and `_Entry.src_channel` carries it, so the incremental tail query of an alias-only channel does not come back empty forever |
| `fetch_samples_split` / `_is_splittable_error` / `STATS["range_splits"]` | **the archiver cannot serve an arbitrarily large window**: past roughly 110 k samples `/samples` answers HTTP 500 (`L3-SBW4-PM311:Energy`: 2026-08-06 = 108 078 samples is served, 2026-08-05 = 116 223 and 2026-08-01 = 122 410 are not; each half of a failing day comes back in ~5 s). A response-size limit, not a timeout and not missing data. The ceiling belongs to the server and no client can know a count before the answer arrives, so nothing keys off a sample count — the 500 IS the signal, and the window is halved and re-asked (sequentially, so a pool of 8 is not multiplied) down to a 1 h floor / depth 3. Only 5xx and timeouts are split; a 4xx or bad JSON would fail identically eight times, and `CpvaBusyError` means our own pool was full, not the window. `fetch_values_ex` routes through it, so `get_day` (whole days) and `value_at_or_before` (multi-day look-back) inherit it. Before this, a Shot Finder search over one busy day returned an empty table. Pinned by `test_day_split.py` |
| `CHANNEL_MAP` / `SBW4_CHANNEL` | the ONE name→channel map; the Slider derives its display names from it (`PV_DISPLAY_TO_COL`) instead of keeping a second copy that drifted (`pcm2` used to differ per tab). `sbw4` was renamed to `L3-SBW4-PM311:Energy` on 2026-08-14 — earlier dates live under the old `HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy`. `if_t.CPVA_SBW4_CHANNEL` is an alias of `SBW4_CHANNEL`, not a second literal |
| `fetch_channels_cached(pattern)` | the `"**"` listing is ~9700 names and every PV picker wants the same one — cached process-wide with single-flight; an empty/failed listing is never cached |
| `split_query` / `tokens_in_order` / `rank_pv_match` / `best_pv_match` | the ONE PV-search implementation, used by the Shot Finder rows and the Slider's PV picker. Tokens are AND-matched with implicit wildcards (`hapls sbw4` = `*hapls*sbw4*`), in-order hits rank above scrambled ones, and camera channels (`CAM_CHANNEL_RE`, ~40 % of the listing) sink to the bottom so they cannot fill the result list |
| `grid_step` / `on_grid` / `quantize` / `QUANTIZED_CHANNELS` | value-grid rule: the waveplate (`L3-PFWP6-MTR03-1:RawPos`) is only ever commanded to multiples of `WAVEPLATE_STEP` = 1000, so every value passes through `quantize`, and one snapped from off-grid (the motor caught mid-move) is reported `exact=False` → rendered `~350000`. `GRID_TOL_FRACTION` = 0.005 is the tolerance `CSS Logger/main.py` already uses on this PV |
| `CpvaBusyError` / `_POOL_WAIT_S` / `_WARM_MAX_WORKERS` | the local pool running out is not an archiver fault: it no longer arms `ERROR_BACKOFF_S`, the wait is sized against the longest another caller can hold a slot (`2 × FULL_DAY_TIMEOUT`, not the waiter's own timeout), the pool is 8 wide (the Slider's PV panel is the widest fan-out), and background warm-up may take at most half of it |
| `STATS` / `stats_line()` | request / 5xx / timeout / busy / max-pool-wait / dropped-sample / abandoned-record counters, appended to `image_tools_diag.log` by `is_t._diag_log` (which adds the PV panel's own `pvFetch` / `pvLastVal` / `pvWait` / `pvStall` fields) |
| `best_shot_ns(start, end)` | timestamp of the strongest shot in a window |
| `date_key_for_ns` / `prev_date_key` / `next_date_key` / `day_bounds_ns` / `today_key` | day keys |
| `get_day(channel, date_key)` → `DayResult` | whole-day sample cache with status + age; today's entry is refreshed with a short TTL and merged (`_merge_tail`), concurrent callers share one in-flight fetch (`_InFlight`). **The share is time-bounded** (`_INFLIGHT_MAX_WAIT_S`): a fetcher that vanishes after registering its record used to leave every later caller waiting on an event nobody would set — an unbounded wait on a worker thread, i.e. the Slider's PV panel frozen (single-flight flag never cleared) until the program was restarted. Past the bound the record is dropped (`STATS["takeovers"]`) and the waiter fetches for itself; `_finish_inflight` takes the record it is completing so a late fetcher cannot publish its stale answer into somebody else's newer one, and nothing between registering and publishing sits outside an exception handler. Pinned by `test_pv_resilience.py` |
| `warm_days` / `peek_day` / `invalidate` | pre-warm, cache-only read, drop |
| `nearest_sample` / `nearest_sample_ex` / `_match_score` | nearest sample to a timestamp, with a preference direction |
| `LookupResult` / `format_lookup` | tri-state result: real value (including a genuine 0) / `n/a` (no sample) / `ERR` (fetch failed, retryable), plus `no data yet` (`pending` — the moment asked about has not been published yet, see below). `head_ts_ns` carries the archiver's newest readable sample for that day. The display words are `PV_TEXT_*`: they name the STATE the operator is in (`no data yet`, ` (older shot)`), not what the program is doing (`wait`, ` (old)`) |
| `head_ts_ns(channel)` | the published head for today, cache-only (no network, safe on the GUI thread). Compare it with a FRAME's timestamp, never with the local clock |
| `value_at_or_before` / `_value_before_day` / `_last_at_or_before` / `invalidate_lookback` | step PVs (e.g. the waveplate `RawPos`) are archived only on change, so they are resolved by bisecting the day of the timestamp (+ previous days). The look-back cache is keyed by "last sample before the START of a day", a key fully determined by its query. |
| `lookup_near(channel, ts_ns)` | the entry point used by every tab: energy PVs match inside a ±window (the Slider passes **±0.3 s** — `_PV_WINDOW_NS`; ±30 s is only the function's default and matched a neighbouring shot for 43 % of frames), `STEP_CHANNELS` fall back to `value_at_or_before` |

**Error status is per-day, not collapsed.** `lookup_near` returns a hit with the status of
the day it came from, so a *neighbouring* day failing can no longer label a good value
`ERR`; and a no-hit answer is `error` only when the day that actually contains `ts_ns`
failed — a clean but empty day is `n/a`. `value_at_or_before` no longer aborts on the first
errored day either: it treats it as empty, degrades to `stale` and keeps walking, which
matters because a step PV's own day usually holds no samples at all. Negative look-back
results now expire (`_BEFORE_NEG_TTL_S` = 600 s) instead of pinning `n/a` for the session,
and one `.value` alias probe runs before an absence is recorded.

**The archiver publishes about a second late, and that is not "no data".**
`lookup_near(pending_if_uncovered=True)` answers `pending` instead of `not_found` when
nothing matched *and* today's data does not reach past `ts_ns + window_ns` — i.e. the
channel's published head is still before the moment being asked about, so the sample may
yet arrive. Measured 2026-08-14 on `PTM1` (95 samples, polled 4×/s, every observation
stamped with the **server's** clock from the HTTP `Date` header): a sample becomes
readable ≈ 0.2–2.5 s after its own timestamp, p50 0.9 s. An image, by contrast, is on the
share ~0.02 s after its timestamp. Only *today* can pend — a finished day cannot grow.

⚠ **Never measure that lag against the local clock.** The workstation this was measured on
runs ~25 s **ahead** of both the archiver server and the image file server (`Date` header
and `net time` agree; the PC itself is correctly synced to `czow-lsrdc01.eli-laser.eu`, so
it is the facility side that differs). Any "how old is this frame?" test taken from
`time.time()` therefore reads ~25 s on a frame that was just taken. Frame-vs-sample
matching is unaffected — both timestamps come from the facility side — but every freshness
decision has to be either archiver-timestamp vs archiver-timestamp, or `time.monotonic()`
elapsed.

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
| `CPVA_SHOT_CHANNEL` / `CPVA_SBW4_CHANNEL` (= `cpva.SBW4_CHANNEL`) / `CPVA_CHANNEL_MAP` | best-shot + per-column channels |
| `MAX_SCAN_FILES` = 2000 | cap on `stat()` calls per folder |
| `ACT_MAX_GAP_S`, `MIN_SEG_ROWS`, `MIN_SEG_DURATION_S` | ramping-segment analysis |
| `CAM_33HZ` | camera numbers running at 33 Hz |
| `ENERGY_BAR_*` | the white annotation bar (height, font, colours) |
| `EMPTY_IMG_MAX_THRESHOLD` / `EMPTY_IMG_CONTRAST_MIN` | empty-frame detection |
| `GRADIENTS` / `GRADIENT_NAMES` | LUT palettes |

### Module-level helpers
| Function | Purpose |
|----------|---------|
| `_import_cpva_client()` / `_import_img_scale()` / `_get_slider_module()` | lazy imports (frozen-aware) |
| `_cpva_fetch_samples` / `_cpva_best_shot_ns` / `_cpva_active_windows_ns` | archiver access + merged "beam active" windows from `:TotalPower` |
| `_cam_totalpower_channel(cam)` | folder name → `:TotalPower` channel |
| `_read_img_max_value(path)` | the frame's PEAK in raw counts (`MaxValue` tEXt, looked up by NAME via `img_scale.read_max_value`). Used only by the empty-frame test — the display scale does not need it |
| `_image_is_nonempty(path)` | frame peak first, pixel-contrast fallback |
| `_render_u8(arr, auto, full_scale)` | the ONE render step for this tab: absolute, or the explicit `Auto stretch`. `full_scale` comes from the decoded image's MODE, never from `arr.max()` |
| `_scale_note(info, arr, auto, full_scale)` | the line under the preview: peak counts, % of full scale, bit depth, which mapping. Takes an already-open image's `.info` — never re-opens the file (130–160 ms on the share) |
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
- **Intensity scale:** `Auto stretch` checkbox next to the Gradient combo (default off =
  absolute), a `Gamma` slider + `Auto` + `↺` under it (`_gamma_arg`,
  `_sync_gamma_enabled`), and the `_preview_scale_lbl` readout under the preview (peak
  counts, % of full scale, bit depth, mapping, gamma). Every render path — preview thread,
  `_apply_gradient_to_image`, Save As, `_send_to_workshop`, `_ThumbView._load_raw` —
  goes through `_render_u8`. The worker paths take `auto` and `gamma` as arguments,
  snapshotted with `grad_name` on the main thread; `_load_raw` deliberately caches the
  ABSOLUTE render with no gamma, because that dialog applies its own `_auto_bright` later
  and the cache is shared by every setting.

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
`_format_diff`, `_find_best_match`, `_folder_hour_from_prague`, `_day_image_folder`
(day archived at all? — distinct from `_find_hour_folder` returning None, or a day with
pictures from 08:00 to 18:00 gets told it has none), `_find_hour_folder`,
`_find_image_for_ts`, `_find_image_in_day` (the ONE resolver: the shot's own hour plus
its neighbours, `hour_cache` keyed by hour), `_image_problem` (a file that exists but
cannot be looked at — 0 bytes, all-zero frame, undecodable — is "no image", the same
disappointment as no file), `_make_lut_sf` and friends, plus `_render_u8` /
`_full_scale_for_mode` — the tab's single render step (absolute, or the explicit
`Auto stretch`), sharing `img_scale` with the Finder and the Slider. The old
`_read_img_max_value` is gone: its only callers were the render paths, and they do not
need the metadata at all.

### ShotFinderWidget
- **Search criteria** — one row per picked PV (`_rebuild_pv_rows`): ticked = filter
  (target ± tolerance), unticked = show only. PVs are added from a searchable
  dropdown over the archiver channel list (`_fetch_channel_list`,
  `_populate_pv_dropdown`; `_rank_pv_match` / `_tokens_in_order` / `_split_query` are
  aliases of the shared `cpva.*` implementation — typed words are AND-matched, in
  order first), or removed with the per-row ✕.
- **Network source** — Images (Lab/Office) + Ramping (Lab/Office) combos
  (`_on_source_changed`, `_on_csv_source_changed`)
- **Date range** — calendar + `_TimeWindowDialog`, weekday filters. The dialog's HOURS
  are part of the search: `_start_search` turns `_tw_start` / `_tw_end` into
  `tw_start_ns` / `tw_end_ns` and clips each day's candidate shots to them (end inclusive
  to the whole second the dialog shows). `per_col` is deliberately NOT clipped — it only
  serves value look-ups around a shot. They used to be collected, displayed and dropped,
  so "today 14:00 → 16:00" searched from midnight.
- **Cameras** — searchable list of the day's cameras (`_load_cameras` scans all 24
  hours, deduplicating)
- **Every searched day gets exactly one row** (`_DayResult.status`): `ok` = a shot AND a
  usable picture, `no_image` = real values but nothing to look at, `no_data` = nothing to
  search (archiver outage, no samples, no numeric values, or the day threw). The last two
  are red rows carrying the reason, built by `_add_failed_row`; only `ok` rows can be
  previewed, saved or sent on, and `_on_search_done` counts them separately from the row
  total. Emitting nothing for such a day is what let a search over one busy day (the
  archiver refuses a whole day above ~110 k samples, see `fetch_samples_split`) show an
  empty table whose only explanation was one line in the Log box.
- **Results** — one row per camera × day: values, extra columns, matched image path,
  preview (`_on_selection_changed` → `_load_and_show_preview`). A cell click reveals
  the file (`explorer /select,`), a double-click opens the dialog with every in-tolerance
  shot — that dialog owns a `_PreviewWidget` of its own (it is modal, so the panel preview
  behind it is not visible) and resolves each shot through `_find_image_for_shot`, which
  probes the hour folder of that shot's own time instead of `dr.hour_folder` (the hour of
  the day's best shot only). Each `_DayResult` persists its search-time state (`search_cols`,
  `extra_cols`, `criteria_csv`, `cam`, `col_meta`, `img_path`) so previews, saving and
  open-in-slider read the search, not the live UI.
- **Export** — `_save_results` (annotated PNGs), `_open_in_slider`, `_send_to_workshop`.
  All three render through `_render_u8` with the current `Auto stretch` state, so a saved
  or handed-over frame looks like the one that was on screen. `_open_in_slider` carries
  each file's `_DayResult` next to it (`list[tuple[Path, _DayResult]]`) instead of an
  index into a parallel list: one skipped day used to shift every later caption onto the
  wrong picture.
- **Intensity scale** — `Auto stretch` checkbox (default off = absolute), the `Gamma`
  slider + `Auto` + `↺` (`_gamma_arg`, `_sync_gamma_enabled`), and the `_scale_note_lbl`
  readout: peak in raw counts, % of full scale, bit depth, mapping, gamma.
  `auto` and `gamma` are passed INTO `_load_and_show_preview`, never read from the widgets
  there — that method runs on a worker thread.

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
  `WATCHER_RESTART_COOLDOWN_S` = 30, `CAM_LOAD_WATCHDOG_S`, `CAM_PIPELINE_GRACE_S`
- Refresh dot: `CAM_DOT_FRESH_S` = 5 (green *wording* only), `CAM_UNDISPLAYED_RED_S` = 8,
  `CAM_FOLDER_ERR_RED_S` = 6, `CAM_POLL_HUNG_S` = 15, `ONLINE_STALL_RED_S` = 5,
  `LIVE_START_GRACE_S` = 10, `CAM_READ_FAIL_RED_N` = 1 — see the refresh-dot section below
- Palettes: `GRADIENTS`, `GRADIENT_ID_DEFAULT` = 0 (original colours),
  `GRADIENT_ID_GRAYSCALE` = 1. `gradient_id` is a positional index into
  `GRADIENT_NAMES`, so a reorder re-maps every selection made from an index —
  the order is Default, Grayscale, Gradient, Binary, False Colors, Rainbow, then
  the rest, and it is mirrored in `if_t.py`, `sf_t.py`, `wk_t.py` (the last two have
  no *Default*). *Binary* and *Rainbow* are the NI Vision palettes of those names:
  - *Rainbow*: blue → red with a prominent green middle, 0 black and 255 white. Jet
    and Turbo also run blue→red but have neither the black start, the white top, nor
    the wide greens.
  - *Binary*: **measured**, not guessed (`_NI_BINARY_CYCLE`, `NI_BINARY_BAND`,
    `_ni_binary_rgb`, `_make_ni_binary_lut`). The rule, in the stored 16-bit units the
    archive uses (after the frame is put on its camera's reference range — see the
    intensity-scale note, or the bands move when the archiver's bracket changes):
    `band = stored // 1024`; black if `band == 0`, else `_NI_BINARY_CYCLE[(band-1) % 15]`.
    **15** colours, not 16, and black is NOT part of the cycle — it is the single bottom
    band. The colours are exactly 0/127/255 per channel: red, green, blue, yellow,
    magenta, cyan, orange, rose, chartreuse, violet, azure, spring green, light red,
    light green, light blue. No white and no grey anywhere.
  - How it was measured, since the same method is the only way to settle the remaining
    palettes: the NI viewer here shows the live camera only and cannot open a ramp probe,
    so the palette was read out of a screenshot instead. A screenshot of an NI window and
    the archive PNG of the frame it shows are the same picture rendered twice, so pairing
    them pixel by pixel gives value → colour. Done on C03-041_PASF1_NF and
    C03-042_PASF2_NF (2026-08-14); the two cameras have very different value ranges and
    produced the same rule, which is the part that cannot happen by accident. The app's
    own render now matches the NI window on **98.3 % / 98.2 %** of pixels (±2 per channel
    for the screenshot's rounding); the rest is the archive frame being 5 s off the one
    on screen, because the archiver stores only about one frame per 35 s. The tooling is
    in the session scratchpad (`ni_lut_extract.py`, `binary_scan.py`, `binary_fine.py`,
    `verify_binary_end_to_end.py`).
  - Rules that fell out of the measurement and are easy to reintroduce by accident:
    the bands are **absolute**, so Binary is view-only (`_is_view_only_palette` covers it
    alongside Default) and takes an exact 16-bit path in `load_image_scaled` that skips
    the 8-bit step entirely — 8 bits quantise to 257 stored units against a 1024-unit
    band, so a pixel near an edge would come out the wrong colour. The LUT in `GRADIENTS`
    is the same rule at 8-bit resolution and serves only the subtraction path, which has
    no 16-bit data left. The old min→max stretch (`FULL_RANGE_PALETTES`) is gone: it made
    the colours mean something different in every frame.
  - *False Colors* is the dark-blue → violet → purple → magenta → pink → white ramp
    with the stops crowded at the bottom (~4× the colour change per count at low
    intensities than mid-scale). It stays inside one colour family on purpose: a full
    spectrum was tried here and only duplicated Gradient / Jet / Turbo. It is the one
    palette in `ADAPTIVE_PALETTES`: spread over the frame's own p0.5..p99.5
    (`_palette_normalize`) instead of the absolute scale, because a beam strong enough
    to see by eye still sits in the bottom tenth of that scale on these cameras. With
    Auto on, the window is already 0..255 and it is a no-op. `_proxy_render` applies
    the same rules so preview and refined render agree.
  - A banded/posterizing "Binary (bands)" variant existed briefly and was removed on
    request; the NI palette is the only Binary.
  - `CYCLIC_PALETTES` — the palettes whose colour repeats with the value (*Binary*).
    Nothing may resample their output with interpolation, because the average of two
    cycle colours is a third, unrelated one: fitting a 2560×2160 frame into a tile is
    about a 6× shrink, which box-filtered the saturated confetti into pastel grey mush
    and was the visible difference against the NI viewer. `palette_is_cyclic(gid)` +
    `set_pixel_exact_scaling(bool)` flip every `ImageView` to `FastTransformation`;
    `_on_gradient_changed` → `_apply_pixel_exact_scaling` drives it and drops the
    scaled copies the views hold (`_ensure_scaled` only re-scales on a size change).
    The flag is module state, not per-view: one palette combo drives the single-cam
    view and every tile, and `CamGrid.setup_cameras` creates and destroys tiles at
    runtime, so a per-view flag would need re-pushing at each site.
    `_proxy_usable` refuses the preview layer for these for the same reason (the
    proxy's downscale and its 8-bit quantisation both change the band index).
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

- `_read_image_max_sample` / `_read_tiff_max_sample` — declared maximum sample value.
  No call sites left; the display scale does not consult metadata for the RANGE — see
  `_frame_scale` for the one thing it does read.
- `_norm16_to8_full_scale` — 16-bit → 8-bit on `value / full_scale`, delegating to
  `img_scale.to_absolute_u8` so the Finder and Shot Finder render through the same
  function. It replaced the `MaxValue`-tEXt × `/4095` scaling, which was only right for
  12-bit cameras and rendered the 6–9 bit diode cams almost black.
- `_frame_scale(path, reader)` — the frame's `(full_scale, camera, frame_bits)`, read off
  the `QImageReader`'s tEXt. **Must be called BEFORE `reader.read()`**: Qt serves tEXt out
  of the header it parses on the way in, and after the read `text()` returns empty and
  libpng logs a read error. Called first it costs ~1.4 ms and the pixels are bit-identical.
  `load_proxy_gray` stores `(camera, frame_bits)` with the frame instead of the resolved
  range, and `_proxy_render` resolves it at PAINT time — freezing it at decode would leave
  every frame of a preview sweep on whatever reference was known before the camera first
  showed its bigger bracket.
- `_ni_binary_rgb(arr16, full_scale)` — the cyclic NI Binary palette bands absolute stored
  values, so it takes the same range: a frame bracketed a step lower would otherwise cross
  twice as many band edges and come out a different set of colours.
- Gamma rides in `_RenderBC.gamma` (slider percent, `0` = Auto), so it lands in every
  render cache key for free. It is applied at the THREE places the absolute scale is
  applied — `load_image_scaled`'s 16-bit branch and both `_proxy_render` paths — because a
  preview paint and the refined render of the same frame must not disagree about the tones.
  `_proxy_gamma` resolves Auto for the proxy: the stored 8-bit codes are a monotone remap
  of the real values, so the median CODE maps through the ramp LUT to the median value
  (order statistics survive a monotone LUT), which is why the two agree to ~0.001 instead
  of merely looking similar.
- `_stretch_arr_f` / `_apply_stretch` (alias `_autostretch_gray`) — percentile stretch
  (Auto contrast). This is the pass that reproduces what an auto-scaling viewer such as
  ImageJ shows; the default absolute scale is deliberately darker.
- `_apply_bc` — manual contrast, then Auto brightness **or** a manual offset, in one
  buffer round-trip. Wrapped by `_apply_contrast` / `_apply_auto_brightness` /
  `_apply_brightness_offset` for the individual call sites. The MANUAL controls keep the
  usual split: brightness is an additive offset, contrast a multiplicative gain — never
  the other way round.
  - contrast pivots on the frame's **black level** (`_BLACK_PCT`), not mid-grey. With
    the mid-grey pivot a frame sitting at code ~29 went black at contrast +20, one nudge
    of the slider.
  - Auto brightness is an auto **level** (`_bc_auto_level`), not a shift: p0.5..p99.5
    onto 0..255, i.e. what ImageJ's auto display range does. Both additive rules tried
    before failed on real frames — parking p99.5 at 255 added +190 and clipped the frame
    to a white rectangle; parking the median at mid-grey lifted the whole picture into a
    flat light field (measured on C03-051-WRT2DPNF: p0.5..p99.5 of 2..53 → 126..177) with
    its contrast untouched. A narrow range can only be spread by a gain.
  - Auto contrast and Auto brightness therefore share ONE auto-level pass:
    `load_image_scaled` and `_proxy_render` run it when either is ticked and then call
    `_apply_bc` with `auto_bright=0`, so a frame is never levelled twice. `_apply_bc`'s
    own `auto_bright` branch is the 8-bit fallback for subtraction frames.
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

### The live refresh dot: health of OUR pipeline, not liveness of the source

`_live_health(cam_i, now) -> (state, tooltip, reason)` is the single verdict behind both
indicators — the per-tile dots (`_on_cam_dot_blink` → `CameraView.pulse_refresh_dot`) and the
Info-panel dot (`_on_online_blink` → `_live_health_summary`, the worst camera). Three states:
blinking bright green = **active** (images arriving and being shown), steady dim green =
**idle** (source quiet, nothing wrong), blinking red = **fault**. Grey = live mode off, which
is also what auto-follow off and any multi-cam scrub produce, because both call
`_stop_online_mode`.

**Green is the default and needs no evidence.** It used to be conditional on an arrival
within `CAM_DOT_FRESH_S`, so five seconds after a run ended every camera went red while
nothing was wrong — an unchanged picture is the correct display of a source that is not
shooting. `CAM_DOT_FRESH_S` now only picks active-vs-idle. Red requires a **named** fault:

| reason | test |
|---|---|
| `stall` | `_online_last_poll_mono` (monotonic, so an NTP step cannot fake it) older than `ONLINE_STALL_RED_S` — the GUI thread was blocked, so nothing was displayed whatever the folders say. Global: it reddens every dot |
| `folder` | `_cam_folder_err[i]` set for `CAM_FOLDER_ERR_RED_S`. The message is built **in the poll worker** by `_oserror_message` from the `OSError` its own `os.listdir` raised — no extra `stat()` on a share that is already failing. One folder listing successfully clears it, which is what keeps hour rollover from reading as a failure |
| `poll` | the listing has been outstanding for `CAM_POLL_HUNG_S` (`_poll_hung_age`). An unplugged share **blocks** rather than raising, so nothing else detects the commonest case |
| `read` / `lag` | the same test, different tooltip: a frame we know about is not on screen |

The lag test is a **latch**, not an age: `_cam_lag_since[i]` records when the tile *first*
fell behind and is cleared the moment it catches up. An arrival clock never fires on a wedged
tile under a live stream (arrivals keep resetting it) and a paint clock fires falsely on the
first arrival after an idle gap. "Behind" is `_cam_arrived_ts_ns[i] - _cam_shown_ts_ns[i] >
LIVE_PAINT_TOL_NS` — **the same 1.5 s the paint path accepts**, which is what keeps the dot
and the timestamp label from contradicting each other (a red label implies the latch is set;
an idle source has `arrived == shown` and therefore neither). Repeated read failures
(`CAM_READ_FAIL_RED_N`) bypass the tolerance, because a failed decode is evidence rather than
a timing estimate: at 3.3 Hz one undecodable frame sits only 0.3 s ahead of the one on screen,
so without this the most literal form of the fault — "a new file appeared and cannot be read"
— never turned the dot red. The threshold is **1**, and the hysteresis is the 8 s latch: a
higher count is unreachable anyway, since `_on_cam_loaded` relaunches through a `_cam_want`
the finished load has already consumed, so a single bad frame fails exactly twice.
`_cam_read_fail_ts` records *which* frame failed, so a successful decode of an older one
(single-cam idle prefetch does exactly that) cannot clear it.

Faults are observable at all only because three things stopped being swallowed:
`_CamPollTask.run` / `_PollTask.run` now classify their `OSError` instead of
`except Exception: pass` (carried on `_CamPollSignals.found`'s fourth argument); the load
handlers stopped conflating a stale generation with `img.isNull()`; and the single-camera view
gained a paint choke point, `_paint_single`, so something finally knows which frame is on its
screen rather than only which was requested. `LIVE_START_GRACE_S` suppresses the verdict while
live mode's opening burst (12 cameras repainting at native resolution at once) settles.

**Known cost of the new rule:** a camera pointed at a readable but empty folder, or whose
hour-folder discovery fails, is now green. Without knowing each camera's expected rate that is
unavoidable once "no new files must be green"; the green tooltip reports the silence age, so
hovering distinguishes "idle 2 s" from "idle 47 min".

`bench_live_dot.py` asserts all of it offscreen against synthetic files in under a minute —
run it with `--cams 2` and `--cams 1`. Case 1 (an idle source stays green) is the reported
bug and must never be allowed to regress.

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

**Where a row sits is resolved to the NEAREST frame** (`_per_cam_row_frame`).
`_per_cam_ts_to_slider` *truncates* a timestamp to a slider step, so mapping the step back
lands a few microseconds **before** the frame the handle was placed on, and a "newest frame
at or before" resolve then names the *previous* one. Three paths did exactly that and each
stepped one frame back: pressing the handle without moving it, releasing a drag, and
`_on_per_cam_master_chosen`, which synced every slave to a moment the new master itself was
not showing. They now resolve the handle to the **nearest** frame, which is stable under
that truncation.

The answer must stay a **timestamp**. Answering from a remembered frame *index*
(`_nav_frame` / `_cam_current_idx`) is worse than the bug it cures: `_on_per_cam_pressed`
kicks `_restore_full_history()`, which merges the whole window back **on the scan pool** and
therefore lands mid-drag, so by the release the same index names a far older moment —
measured at **18 s backwards**, 20 live frames restored to 56. `_restore_full_history` states
the rule in its own comment: *remember the moment being watched, not the index*. Nothing
re-places `_nav_frame` after that merge (`_display_multicam_index` moves the handles, not the
index map), which is exactly why only the handle can be trusted. `_setup_multi_cam` still
clears `_nav_frame` / `_nav_pending`: both are keyed by camera SLOT and `_per_cam_step` steps
relative to them, so a leftover entry belongs to whoever held that slot before.

Pinned by `bench_master_sync.py` — all six ways the master moves (drag, release, arrows,
master switch, playback, live arrival) plus independent mode, checking every slave
**handle**, not just its picture, against what `_per_cam_slave_targets` resolves — and by
`bench_live_pv_load.py`, which grabs a slider *in live mode* and asserts the moment survives
the history merge.

`_on_per_cam_pressed` also sets **`_is_scrubbing`**, which nothing on this path did before.
`_cam_tile_side` and `_current_decode_side` read it, so the motion downscale and every other
"the user is moving" optimisation were dead on the path the user actually drags.
`_navigating()` is that predicate; `_proxy_is_moving()` is the sweep's version and includes
`_per_cam_scrubbing_cam`.

### Live mode: every camera refreshes on every shot
The master stays the clock — its arrivals decide *which moment* the grid is on — but that
rule used to be implemented as "only the master's arrivals paint anything", and two whole
classes of tile then stopped updating:

* **A slave's own arrival painted nothing.** `_live_advance_cam` returned immediately for
  any camera that was not the master. One shot writes N files and they do not land in the
  same instant, so the slave's frame — merged into the timeline, counted for the refresh
  dot — stayed invisible until the master's *next* arrival.
* **A slave the master had run away from froze for good.** `_per_cam_slave_targets` skips a
  camera with no frame within `SLAVE_SYNC_MAX_NS` (3 s), which is right for scrubbing (do
  not drag a slave to a distant neighbour the user never asked for) and wrong at the live
  edge: a camera whose cadence never lined up with the master's inside 3 s never repainted
  again, however much it was shooting. Measured minutes wide in the field — a grid at
  08:31:32 next to five tiles frozen at 08:08:02.

`_live_slave_target(cam, master_ts)` is the live-edge resolver, and it answers in three
cases: the nearest frame within `SLAVE_SYNC_MAX_NS` (unchanged, so a camera keeping up is
unaffected); else the newest frame **at or before the master's moment** if that is newer
than the tile's current picture (never past the master — the grid must not show the future
relative to its own clock); else `None`, which is the one case a frozen tile is the truth:
the camera really received nothing new.

`_per_cam_sync_slaves(..., live_edge=True)` resolves through it; `_per_cam_slave_targets`
itself is unchanged, so drag / release / arrows / master switch / playback keep the old
rule. `_live_sync_one_slave` is the single-tile version, called from `_live_advance_cam`
when a slave arrives — one tile per arrival, because re-syncing the whole grid on every
slave frame would multiply the share reads by the camera count.

A tile that is advanced away from the master's moment reads its own time, so it must not
look current: `_cam_off_master` is checked alongside `_cam_is_stale` both on the paint path
(`_cam_note_painted`) and on the 600 ms dot tick (`_cam_refresh_stale_marks`).
`_cam_is_stale` alone cannot see it — that test only compares a tile against its own last
request, and an advanced tile got exactly the frame it was asked for.

**The live toggle reloads the whole grid.** `_on_auto_follow_toggled` used to re-display
nothing at all when live mode went *off*, so the tiles kept the live decode size and
whatever moments they were left on. Both directions now resolve a moment per camera
(`_cam_toggle_ts` — the frame *on screen*, since `_cam_current_idx` is written at request
time) and re-render **every** tile, including the ones whose moment did not change: the two
modes decode at different sizes (`_cam_live_side`), and `_hq_cancel()` + `_schedule_hq()`
re-arm the native tier, which otherwise waits for an arrival that never comes with the
laser off.

Pinned by `bench_live_slave_refresh.py`: a master at 3.3 Hz, a healthy slave arriving 54 ms
later, a slave writing once every 40 s, and a camera given nothing at all — asserting that
each tile *and its handle* land on every frame that camera received, that the quiet one
never moves, and that toggling live off and on repaints all four.

The per-camera line in `image_tools_diag.log` (`cams=0:1234+7/b0.4,…`) is what separates
the two ways a tile can sit on an old picture: frames **known** per camera and how many
arrived in the last 60 s, against `b` = arrived − shown in seconds. Discovery stopping
(`+0` while the laser runs) is a poll/watcher problem — a different fix in different code
from anything above.

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
hour folder of every window** (listed in parallel — a camera that ran only 18:00–19:00
appears next to a different set recorded at 17:00) plus a status (`""` / `no_data` /
`error`).

### Widgets and dialogs
| Class | Purpose |
|-------|---------|
| `ImageView` | frame display, overlays (cross/circle/square, SC top-N points, PV bar, cam label + timestamp, green `Ref:` badge via `cam_ref_text`), zoom, calibration |
| `CameraView` / `MultiCameraGrid` | one camera tile / the multi-cam grid; `_FreeLayoutContainer` and `_AutoLayoutContainer` are the two layout backends |
| `compute_camera_layout` (+ `_cam_layout_weight`, `_layout_trees`, `_place_layout_tree`, `_justified_rows_layout`) + `CamLayoutEntry` / `CamLayoutConfig` / `LayoutConfigDialog` / `_LayoutCanvasWidget` | camera auto-layout plus a drag-and-drop free layout editor. A layout is a split tree (each cut puts two tiles side by side or stacks them), which is why the whole thing is searchable: a subtree behaves like one tile obeying `height = width/A + B`, so it is built bottom-up in two floats and placed back exactly. Candidates are scored leximin — the smallest frame is made as large as it can be, then the next smallest — because maximising the *sum* hands one lucky frame most of the canvas, and equal-length rows (`_justified_rows_layout`, still the fallback above 12 cameras) waste half the canvas the moment one camera is portrait. Diode arrays ask for twice the area (`_cam_layout_weight`). Each tile reserves the label bar so the frame fills its image region; the packing only fills the canvas in one direction, and rather than centring the block and leaving a dead grey margin, the slack is stretched into the tiles — frames keep their size and aspect, the empty pixels end up inside the tiles where the focus-mode mask can cut them away. Results are cached per canvas size, since every resize event recomputes them. The editor draws on a board with the LIVE camera area's aspect (`_board`), so its preview is the arrangement the grid will actually produce, and an untouched/auto-arranged editor returns `is_auto()` — the picker then keeps the grid on auto (saved as `"auto": true` in `cam_layouts.json`) instead of freezing editor-sized fractions into a free layout. Only a hand-dragged layout is stored as fixed; entries without the flag are legacy frozen previews and are ignored |
| `remember_cam_aspect` / `_cam_aspect_hint` (`cam_aspects.json`) | per-camera frame aspect, learned from the first frame and persisted. The packing needs an aspect *before* any frame is loaded; the name hint only knows "portrait diode array vs square", so a rebuild — every time-window rescan recreates all tiles — used to pack for square tiles and the layout visibly jumped when the frames arrived. `ImageView.frame_aspect_changed` re-runs the packing when a camera's real aspect turns out different; overhead changes (label font, `Ref:` badge) go through `MultiCameraGrid.refresh_auto_layout` |
| `Pdxm1GridConfig` / `Pdxm1GridConfigDialog` / `_GridPreviewWidget` / `get_pdxm1_grid_config` / `_cam_type_key` | the PDxM1/PDxM2 diode grid overlay; line positions are stored as absolute image fractions so each line is independent, and PD cameras of the same type share one config |
| `PointingPanel` | matplotlib scatter + histogram + path, click-to-jump, live replay, click/region delete with per-operation undo. The Beam-path colour bar carries a draggable time cursor: it snaps to the nearest shot, shows `HH:MM` beside the bar, moves a lime dot along the path and drives the image viewer (throttled through `_cursor_nav_timer`, one share read is ~130-160 ms). The bar's own axes are explicit (`cax`), so a multi-day analysis can draw a line at every local midnight and write the date beside each day's segment — `HH:MM` alone would be ambiguous. Cursor, readout and dot are Qt children of the canvas, so `Save Plot` writes the figure without them, while the day markers (matplotlib artists) are included |
| `_SCHistogramWidget` / `_SCHistogramDialog` / `_SCExclusionEditor` / `_SCExclusionCanvas` / `_SCValueLabel` / `_SCPreviewLabel` | spatial-contrast threshold, exclusion regions, preview |
| `TickBar` | time axis under the slider — ticks, A/B marks, `dd.mm` labels at midnight crossings. A multi-day pick glues its windows together and **alternates the label rows** window by window (hours above the baseline / date at the bottom, then swapped, …), so the two hours meeting at a seam never fight for the same pixels |
| `DatePickerDialog` + `_DayTimeDialog` + `_make_multiselect_calendar` | see below |
| `CameraPickerDialog` (+ `_CamLoaderSignals`) | camera selection + presets; the list is the union over every window of the pick |
| `LazyDirModel` / `_DirItem` / `FolderPickerDialog` | lazy network folder tree |
| `CollapsibleSection` | the sidebar's collapsible sections (accent stripe + ▾/▸), state persisted. The body carries the accent's left stripe (`_shade(accent, 1.35)`) **and** a near-white wash of it (`_shade(accent, 1.93)`) so each group reads as one coloured block; the tint stops there because black control text has to stay comfortably legible on it |
| `_CamSliderRow` | per-camera master radio + slider |
| `_PvOverlayPanel` | floating, draggable PV panel |
| `PvConfigDialog` | "Select PV Channels": preset checkboxes **plus** a search box over the whole archiver listing (`cpva.fetch_channels_cached` on a worker thread → `_PvChannelListSignals`, ranked by `cpva.rank_pv_match`). A picked channel is added under its own name to `PV_CUSTOM_CHANNELS` (module-level, because `pv_text_for_ts` resolves names through `pv_channel_for` on the save worker thread) and persisted as `pv_custom`. Picking a channel that is already a preset ticks the preset instead of adding a duplicate row that would read the same PV twice; unticking an added PV keeps it in the list. Every row in **Selected PVs** carries a **"show as" box** whose text goes to `PV_LABELS` (persisted as `pv_labels`, read through `pv_label_for` by the overlay, the PV table and `pv_text_for_ts`): a searched-for PV is named after its channel, which is unreadable over a picture. A label is **display only** — the PV name stays the key for selection, eye state, channel letters, formula bindings and the saved state, so naming a PV cannot repoint a formula or hide which channel is read (the channel is still in the row's and the table's tooltip). `_sync_labels` reads the boxes back before every rebuild, exactly like `_sync_custom_checks`, or a preset toggle would wipe a name just typed; `labels()` drops a name equal to the PV's own. Formulas get no box — their name is typed by the user already. The presets sit in a **3-column grid** (a single column pushed everything below them off the dialog). Every tick box carries its **channel letter**, and the formula rows at the bottom define **derived PVs** (`PV_DERIVED`, persisted as `pv_derived`) as Python expressions in those letters. Letters are positional, so `_refresh_letters` re-renders each formula from its stored `bindings` (letter → PV name) after every add / remove / rename; a binding whose PV was deleted is parked on a letter past the end of the list rather than left where another PV has moved in, and the row warns instead of silently repointing. `_on_accept` refuses to close on a nameless, duplicate or unparsable formula |
| `PopupBelowComboBox` | combo whose popup always opens below |

**`DatePickerDialog`** — one house-style multi-select calendar plus `QTimeEdit`
From/To (minute resolution) and ONE multi-day mode: tick "Multiple days" and every
calendar click adds a day (a click on a picked day removes it again). The single
From/To window applies to every picked day — `_on_times_changed` → `_rebuild_segments`
moves them all at once. It opens in **Now** mode (today, `hh:00`–`hh+1:00`);
`_apply_now_window()` is the Live-mode checkbox's, while the **Now button only moves
the calendar to today** — it must never change a mode, a window or the day list.
From/To can never collide — `_on_times_changed` pushes the other field by an hour.
The ⚙ column opens `_DayTimeDialog` for one day; that override lives in
`_day_overrides`, survives rebuilds and global From/To changes, and is marked `*`.

### Viewer(QWidget) — method groups
| Group | Methods |
|-------|---------|
| init / UI | `__init__`, `_build_ui`, `_load_ui_state` / `_save_ui_state`, `_on_section_toggled` / `_set_all_sections`, `_diag_log`, `resizeEvent`, `_set_busy` |
| overlays | `_on_reset_zoom`, `_toggle_draw_mode`, `_refresh_draw_btns`, `_remove_all_overlays`, `_open_overlay_settings` / `_apply_overlay_settings`, `calibrate_circle/cross/square`, `_sync_overlay_checkboxes_from_iv`, `_on_overlay_changed` |
| PV | `_open_pv_config` (→ `PvConfigDialog`), `_pv_rebuild_table`, `_pv_visible_names`, `_pv_on_table_clicked`, `_pv_trigger_fetch(_now)`, `_pv_current_ts`, `_pv_is_pending`, `_pv_force_refresh`, `_pv_on_result`, `_pv_update_overlay`, `_open_pv_overlay_settings`, `_pv_text`. The table lists **every** selected PV and its first column is an **eye** (`_pv_hidden`): it removes that PV from the on-image overlay and from the burn-in, never from the reading — a hidden PV keeps its number in the table and may still be a formula's source. `_pv_trigger_fetch_now` asks the archiver for `pv_source_names(...)`, i.e. the ticked PVs **plus** every formula's inputs, and evaluates `pv_eval_derived` on the worker thread so the derived values reach `_pv_values` as ordinary names (held-value / stale handling then applies to them unchanged). The channel selection is **persisted** in `slider_ui_state.json` (`pv_enabled` + `pv_custom` + `pv_derived` + `pv_labels` + `pv_hidden`) — it used to live only in memory, so every restart came up reading no PV at all until the user re-opened the dialog. `_pv_current_ts` returns `None` rather than falling back to another camera's moment, and `_reset_ui_for_new_scan` clears `_pv_values` (greying the previous dataset's numbers still read as this one's). Fan-out is capped at `PV_FETCH_MAX_WORKERS` = 4 so the panel cannot exhaust the shared cpva pool. **The panel waits for the archiver** (`_pv_awaiting`, `_pv_arm_wait_retry`, `_pv_held_age_s`, `_pv_head_behind_s`): a value the archiver has not published yet is `pending`, not `n/a`, and the panel re-asks (400 ms, doubling to 2 s, giving up after `PV_ARCHIVER_MAX_WAIT_S` = 20 s of **monotonic** waiting) until it lands — so the numbers for the frame on screen arrive on their own. Before this, a fetch was only ever triggered by a frame change, and since it fires on the *leading* edge (a few ms after the image appears, ~1 s before its sample is readable) the panel kept the previous shot's numbers and nothing ever asked again: with one frame per shot it stayed **one shot behind the picture**. A held number now also says how far back it comes from (`12.95 J (-28 s)`, not a quiet `(old)`), and the overlay badge distinguishes `no data yet` (the archiver has not published this frame) from `older shot` (this number was read for an earlier frame) and `⟳` (our own refresh is in flight). The markers share **one line along the bottom edge**, joined by `·` and elided (never wrapped) when the panel is narrow, and hovering the panel explains every lit marker (`_BADGE_HELP` / `_badge_tip`). They used to be stacked in a strip down the right-hand side, reserved at the width of `no data yet` whether or not anything was lit, which left a third of the overlay empty beside every number; height is what this panel has to spare, not width. The reservation itself stays constant (`_badge_strip_h`, plus `_badge_min_w` so one marker always fits), because that is what stops a lit marker resizing the panel. Those explanations — and the value column's tooltips — go through `_show_long_tip`, because Qt hides its own tooltip after ~10 s and then refuses to show it again until the pointer has left the widget and come back, i.e. it disappears mid-sentence on exactly the text that needs reading. **Above ~1 shot/s** the frame on screen is *always* younger than the publication delay, so `_pv_pick_fetch_ts` deliberately aims the fetch at the newest frame the archiver has published — the frame at or before `cpva.head_ts_ns` (never newer: that would be matched against a neighbour's sample) and at most `PV_RETARGET_MAX_BACK_S` = 3 s back, so a value from a quiet stretch is never dragged in. The numbers then skip a shot and say so (`(-0.6 s)`, tooltip naming that frame's time) while the images still show every frame; the retry keeps running and snaps onto the displayed frame the moment its own sample lands. `_pv_is_held` therefore treats "read for a different frame" as its primary test, and `_pv_is_pending` (⟳) is measured against the fetch TARGET, not the screen, so a deliberate offset does not light it. Nothing changes away from the live edge — scrubbing, archive browsing and the burn-in still resolve every shot exactly. Pinned by `bench_pv_wait.py`. **A paint is a trigger of its own** (`_note_cam_shown`): in multi-cam `_pv_current_ts` answers from `_cam_shown_ts_ns`, the frame actually *painted* on the master tile, while every other trigger fires at *request* time — a live frame triggers its fetch ~150 ms before the share read that paints it finishes, so that fetch still described the **previous** frame and nothing asked again (the retry compares against the same request-time frame and sees nothing wrong). The panel therefore sat one shot behind in live multi-cam even with everything above in place; `bench_pv_live_multi.py` measured it (shot 2 reading shot 1's energy) and now pins the fix. `_pv_cam_index` is the one place that decides which camera the panel describes, so the fetch and the paint that re-triggers it can never disagree. **The panel heals itself** (`_pv_health_tick`, every `PV_HEALTH_TICK_MS`): single-flight means one fetch that never returns freezes the values for the rest of the session (reported as "the PV overlay stopped loading, a restart fixed it"), so a fetch still in flight after `PV_FETCH_WATCHDOG_S` = 120 s is written off — its generation goes into `_pv_abandoned_gen` so a late result can never come back and put an old frame's numbers under the current picture — and a fresh one starts; and because every normal refresh rides on a frame change or a paint, `PV_KEEPALIVE_S` = 60 s re-asks on its own when nothing has triggered one. `_diag_log` records both (`pvFetch` / `pvLastVal` / `pvStall`). Pinned by `test_pv_resilience.py` |
| live health | `_live_health`, `_live_health_summary`, `_reset_live_health`, `_note_cam_frames`, `_note_cam_read_fail` / `_ok`, `_note_cam_folder_status`, `_set_lag_since`, `_poll_hung_age`, `_paint_single` |
| multi-cam | `_is_multi_cam`, `_switch_to_multi/single_view`, `_build_per_cam_sliders`, `_on_per_cam_*`, `_per_cam_display_one`, `_per_cam_row_frame`, `_start_cam_load`, `_reset_cam_pipeline`, `_cam_load_watchdog`, `_per_cam_sync_slaves`, `_live_slave_target`, `_live_sync_one_slave`, `_cam_off_master`, `_cam_toggle_ts`, `_per_cam_step`, `_live_advance_cam`, `_setup_multi_cam`, `_on_multicam_selected`, `_redraw_cam_in_place` |
| live mode | `_on_auto_follow_toggled`, `_ensure_dir_watcher` / `_watcher_strike` / `_stop_dir_watchers` / `_prune_dir_watchers` / `_on_dir_watch_new_file`, `_start/_stop_online_mode`, `_online_poll`, `_online_poll_single_bg` / `_multi`, `_merge_single_new_items`, `_merge_items_by_ts`, `_extend_axis_to_items`, `_restore_full_history` / `_merge_restored_history`, `_rebuild_shared_items_from_cams`, `_extend_shared_timeline_from_cams`, `_on_online_blink` |
| open / scan | `open_folder`, `_start_multi_cam_scan`, `_on_multi_scan_all_done`, `open_by_date`, `_reload_with_last_cameras`, `auto_start_online`, `open_folder_path`, `receive_external_folder`, `open_file_list`, `refresh_folder`, `_refresh_multi_cam`, `_start_scan`, `cancel_scan`, `_on_scan_*`, `_choose_axis`, `_in_ts_windows` / `_filter_to_ts_windows`, `_hard_reset_runtime`, `_reset_ui_for_new_scan` |
| brightness / subtract | `_bc`, `_refresh_auto_bc_sliders`, `_sync_bc_value_labels` / `_set_gamma_label` (the "Con:/Bri:/Gam:" rows' numeric readouts — driven off the sliders, and called from the Auto parking too, which blocks signals), `_on_brightness_slider_changed`, `_on_contrast_slider_changed`, `_on_contrast_auto_changed`, `_on_bright_auto_changed`, `_apply_brightness_debounced`, `_load_raw_arr`, `_ref_arr_for` / `_cam_ref_arr_for`, `_set_reference_frame`, `_has_reference`, `_set_ref_status` / `_refresh_ref_warning`, `_on_subtract_changed`, `_update_diff_stats` / `_update_cam_diff_stats`, `_on_gradient_changed` |
| slider ↔ time | `_slider_to_time_ns`, `_slider_to_index`, `_index_to_slider_value`, `_time_to_slider_value`, `_time_to_nearest_index`, `_set_info_for` |
| display / load | `_on_slider_pressed/changed/released`, `_apply_scrub`, `_load_or_cache`, `_adaptive_index_step`, `_display_index`, `_display_exact_index`, `_display_multicam_at_time` / `_index`, `_on_cam_loaded`, `_request_display_target`, `_drain_deferred_display`, `_request_pixmap`, `_prefetch_idle` / `_playish`, `_on_loaded` |
| playback | `play`, `stop`, `_autoplay_step`, `_play_show`, `step_frame`, `keyPressEvent`, `_current_play_pct_per_s`, `_play_is_exact`, `_update_motion_speed`, `_adaptive_stride`, `_current_decode_side` |
| focus / watcher | `_toggle_focus_mode` (F11 — hides everything but the image, title bar removed via Win32 so the HWND survives), `_toggle_watcher_mode` (Ctrl+F11 — edge-to-edge wall display), `_win32_set_title_bar`, `eventFilter` |
| focus-mode window shape | `_focus_mask_rects` / `_focus_apply_mask` / `_iv_content_rect` / `_focus_edge_rect`. Focus mode must show the cameras and *nothing* else, but the tiles never fill their area exactly, so the window used to be a large grey rectangle with the frames somewhere inside it — covering the program underneath. `setMask` of the union of the camera cards (header band + the frame actually drawn, per `ImageView._img_rect`) plus any floating PV overlay solves both halves at once: on Windows the masked-away pixels are neither painted nor hit-tested, so the gaps are see-through *and* clicks land on the window behind. Re-applied on a 200 ms timer because the frame rect follows the layout, the label bar and the image aspect and there is no one signal for all three; identical regions are skipped so nothing flickers. Since the window's real edges can be masked off (a click there never arrives), the resize hot zone is taken from the mask's bounding rect — `_focus_edge_rect` |
| timestamps | `_save_current_timestamp`, `_goto_saved_timestamp`, `_clear_timestamps` |
| pointing | `run_pointing_analysis`, `_cancel_pointing`, `_on_pointing_progress/cancelled/finished`, `_on_pointing_live_toggled`, `_start/_stop_pointing_replay`, `_pointing_replay_step`, `_save_pointing_plot`, `_toggle_pointing_path`, `_toggle_pointing_select`, `_on_pointing_region_deleted`, `_restore_pointing_points`, `_on_pointing_point_clicked` |
| spatial contrast | `_sc_set_enabled`, `_on_sc_threshold_changed`, `_run_sc_auto_threshold`, `_open_sc_histogram`, `_open_sc_exclusion_editor`, `_on_sc_exclusion_set`, `_run_spatial_contrast`, `_on_sc_preview` / `_update_sc_preview`, `_on_sc_finished`, `_on_sc_topn_changed`, `_on_sc_marker_style_changed`, `_update_sc_topn_overlay` |
| marks / save | `set_mark_a/b`, `clear_marks`, `_apply_marks_to_tickbar`, `_update_range_ui`, `save_around_current`, `save_current_with_overlay`, `_get_cam_frame_for_save`, `_render_cam_frame`, `_save_multicam_current`, `_send_to_workshop`, `save_current`, `_save_multicam_range`, `save_range`, `_show_save_range_progress_dialog`, `_on_save_progress/finished`, `_pv_text_for_ts` / `_pv_prefetch_texts` / `_pv_save_append_bar` |

**Palettes:** index 0 = Default (original colours), 1 = Grayscale, 2+ = LUT.
**Saving:** Default → `shutil.copy2`; anything else → `load_image_scaled` + save;
always `_copy_metadata_into_png`.

---

## wk_t.py — Workshop

**The one rule of this tab: display settings never change pixels.** Brightness,
contrast, gamma, the display window and the palette live in `_ViewSettings` and are
re-applied by `render_view` on every render, so any of them can be undone by moving the
control back. Only the Edit section (crop, rotate, flip, resize, reference subtraction)
touches `base`, and every one of those is undoable. Saving writes a NEW file and
refuses a target equal to `source_path`.

### Three layers per slot
```python
@dataclass _WorkshopSlot:
    base        # uint8 picture being shown and edited (what the sender rendered)
    source_base # base as it arrived, for "↺ Original"
    raw / source_raw / full_scale / camera / raw_note
                # native counts re-read from source_path — MEASUREMENT ONLY, kept
                # geometrically in step with base; measure_arr() falls back to base
    view        # _ViewSettings — display only
    annots      # list[_Annot] in image coordinates
    px_per_mm   # set from a ruler of known length
    zoom / offset / fitted     # per-slot view position
    undo_stack / redo_stack    # capped by _SLOT_UNDO_LIMIT = 30
```
`_state()` shares array references instead of copying: `base` and `raw` are never
modified in place, every edit builds a new array, so 30 history steps cost nothing.

`_RawLoadTask` (a `QRunnable`) re-opens `source_path` in the background and reads the
native array plus `img_scale.full_scale_for_pil` / `camera_from_path`. This is why the
hand-off contract `receive_image(arr, label, source_path)` did **not** have to change
for is_t / if_t / sf_t: the senders keep handing over 8-bit, and the Workshop fetches
the counts itself. `receive_image` also accepts `raw=`, `full_scale=`, `camera=` for a
sender that wants to pass them directly. Statistics say which of the two they came from
(`unit_name()` → `counts` / `code`) — never silently.

### Rendering
`render_view(base, view)` — display window → manual contrast → gamma → brightness →
palette. Contrast is a gain pivoted on the frame's own BLACK LEVEL, matching
`is_t._apply_bc`; `_contrast_gain` is the same curve so a number means the same thing in
both tabs. Cyclic palettes (NI Binary) bypass the whole chain, and the panel greys the
controls they ignore. `WorkshopCanvas._ensure_image` caches the result per
(base, shape, view key).

### Borrowed, not copied
`_import_img_scale()` and `_get_slider_module()` (both lifted from `if_t.py`) bring in
`img_scale` and the Image Slider's `GRADIENTS` / `ADAPTIVE_PALETTES` /
`CYCLIC_PALETTES` / `CollapsibleSection`. `_load_palettes()` keeps a five-entry local
fallback so the file still opens standalone; `_FallbackSection` does the same for the
panel. Palettes are looked up **by name**, because is_t's list starts with `Default`
and persists selections by index.

### Tools (`WorkshopCanvas`)
`TOOL_PAN / ZOOM / SELECT / BRUSH / LINE / ARROW / RECT / ELLIPSE / POLY / TEXT /
CROP / EYEDROP / ERASER / RULER / ROI_RECT / ROI_ELLIPSE / PROFILE / CROSS`

**Magnify** is a mode, not a pair of buttons: left click zooms in *at the click point*,
right click zooms out, and a drag past `_DRAG_DEAD_PX` = 6 rubber-bands a rectangle that
`zoom_to_rect` blows up to fill the canvas. The wheel magnifies under the cursor in every
tool. `_zoom_at` keeps the point under the cursor fixed; `resizeEvent` keeps the image
point at the canvas centre where it was (it used to call `fit_to_view()` unconditionally,
so every window resize threw the zoom away). Above `_PIXEL_VALUE_ZOOM` = 24× the value is
written inside each pixel, ImageJ-style.

### Button icons
Every symbol on a Workshop button is **drawn**, never typed. One `_ico_*` recipe per
symbol paints into a 20×20 grid; `_render_icon_pixmap(name, px, ink)` scales the painter
to any size, and `tool_icon(tool)` / `action_icon(name, ink)` cache the finished `QIcon`.
`_ipen` is the single pen helper, so the whole set shares one weight vocabulary
(2.0 px primary, 1.7–1.8 px detail, round caps except on rectilinear forms).

This replaced Unicode glyphs, which put three typefaces in one row: `✋🔍📏💧` came from
Segoe UI Emoji as colour bitmaps that **ignore the stylesheet's `color:`** — so they
stayed colourful on the blue checked background while their neighbours turned white —
`▭◯⬠〰⧉` came from Segoe UI Symbol as hairline outlines, and `⬠`/`⧉` are not guaranteed
to exist at all. At the 13 px font size the strip used, that left about 9 px of ink.

Three rules the factory depends on:
- **Every `QIcon` mode is spelled out.** Left to itself Qt fades its own disabled variant
  to near-invisible and keeps dark artwork on the checked blue. Both are the
  "can't see it" failure this replaced.
- **Each icon is added at 20, 30 and 40 px** so `QIcon` can pick per monitor scaling.
  Do not pre-multiply by one screen's `devicePixelRatio`.
- **Nothing is built at import time** — `QPixmap` needs a live `QGuiApplication`.

Checked blue is `#2f6fb5`, not `#3a7ebf`: white on the old blue was 4.3 : 1 and read as
washed out; the new one is 5.2 : 1. Buttons are 32 × 30 px with `padding: 0`.
`180°` and `Resize…` keep plain text — a third turning arrow beside Undo and Original
would be one too many to tell apart.

### Annotations
`_Annot(kind, pts, colour, width, filled, text, font_size, label)` — geometry in image
coordinates, painted at draw time by the shared `paint_annots(painter, annots, to_widget,
scale)`. Save calls the same function with an identity mapping, so a saved copy is what
was on screen. Being geometry is what makes them selectable, movable and resizable after
the fact (`_BOX_KINDS` get eight handles, `_SEG_KINDS` two endpoints), keeps them crisp
at 6400 %, and keeps the drawing colour out of the palette LUT. The Eraser deletes an
item rather than restoring pixels. Geometry edits (rotate / flip / resize / crop)
transform the annotation coordinates too, so nothing detaches from the feature it marks.

### Measurement
`region_stats` (min / max / mean / std / sum / count + moment centroid),
`_region_mask` (rectangle or ellipse), `line_profile`. All measured on `measure_arr()`,
i.e. **before** the view transform. `_HistogramWidget` and `_PlotWidget` are painted by
hand on purpose — a matplotlib toolbar would have to go through the Finder's icon-tinting
workaround, and neither of these needs one.

### WorkshopWidget layout
Tool strip (tools + colour/width/fill + zoom box + undo/redo/clear/original) over a
`QSplitter` of a collapsible panel and the canvas, with a status line underneath showing
the cursor position, its value in counts and % of full scale, the image size and the
zoom. Sections and accents: Images `#2f6fd0`, Display `#7a4fc0`, Measure `#b0396b`,
Edit `#2e9e5b`, Compare `#d08a1e`, Save `#c0392b`; expanded state persists in
`%APPDATA%/ELI_ImageTools/workshop_ui_state.json`.

Key methods: `receive_image`, `open_files` (also drag-and-drop), `_activate_slot`,
`_on_view_control` / `_apply_view_now` / `_sync_view_controls` (one guarded place that
parks every Auto value on its control), `_refresh_measure`, `_set_scale`, `_show_profile`,
`_rotate` / `_flip` / `_resize_dialog` / `_do_diff`, `_update_compare`,
`_render_for_save` / `_save` / `_save_all` / `_copy_clipboard`, `_after_change` (the
single place that re-reads everything derived from the active slot, so no button is left
stale).

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
- **wk_t** — a Compare view is read-only: the picture on screen is a composition of two
  slots, so drawing and measuring are refused while one is shown.

### Efficiency / responsiveness
- Network IO and PNG encoding still run on the main thread in some if_t / sf_t
  save / try-again / open-in-slider paths → the UI freezes on a slow share.
- No cached directory listing: `if_t.load_folders` and `sf_t._load_cameras` rescan
  all 24 hour folders on every change of hour / source / date.
- The sf_t search and camera-load workers have no cancel/generation token, so a
  stale run can still overwrite the table.

### Duplication / dead code
- The palette LUT tables and `_lut_pixels` are still defined three times
  (`if_t.GRADIENTS`, `sf_t.SF_GRADIENTS`, `is_t.GRADIENTS`) with the same stops copied
  by hand. The intensity SCALE now has one owner (`img_scale.py`); the palettes are the
  remaining copy-paste, and the two per-frame ones (`Binary`, `False Colors`) have to
  stay in step across all three. `wk_t` no longer has a copy — it borrows `is_t`'s
  through `_get_slider_module()`, which is the pattern the other two should follow.
- if_t: `MIN_FULL_FILES`, `_range_gen`, ignored `select_images_from_folder`
  parameters, three near-duplicate annotation font/wrap routines.
- is_t: `_LazyDirModel` stub (`pass`), overlay-draw code copied per view.
- sf_t: `_setup_calendar` duplicates the calendar factory; legacy hidden widgets.
- Frequent `except Exception: pass` hides real errors (notably `ScanTask.run`,
  which wraps the whole scan).
