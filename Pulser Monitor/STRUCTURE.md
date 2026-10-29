# Pulser Monitor — STRUCTURE

> Verified against source: 2026-10-27 · `main.py` 8369 L (named `pulser_monitor.py` until 2026-10-29)

Single-file PySide6 application: measurement, analysis and UI in one module. Run it
with `python main.py`.

User-facing documentation: `ReadMe_Pulser Monitor.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Pulser Monitor_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

The detailed ReadMe is the authority on *what the words mean* — dropout / fault /
trip / dead, pulser trip / dead pulser / bigger problem / manual stop / outage with no
pulser / switched off, blink / partial — and this file is the map of *where
the code is*.

## Files

| File | Description |
|------|-------------|
| `main.py` | The whole application. |
| `C03-*-allgood.png` | Per-camera reference frame with **every pulser lit**. Every measurement is relative to this. Without it a pulser dark for the whole window cannot be flagged at all. |
| `C03-*-warmup.png` | Per-camera reference frame taken **while warming up**. Without it warm-up cannot be measured. |
| `C03-*-_-IMG_-_*.png` | Captured sample frames. |
| `testing/test_pulser.py` | Offline analyser test: synthesises every dropout / fault / trip / death and every kind of no-data stretch out of the real reference frames. |
| `testing/test_gui.py` | Headless test of the four tabs and every CSV / PNG export (writes to `testing/gui_out/`). |
| `testing/test_real.py` | The same pipeline on genuine running and warm-up frames. |
| `testing/test_gate.py` | Active-time gate: span algebra, an overnight window, the panel controls. `--live` adds a real archiver query. |
| `testing/test_blinks.py` | Blink counting: dump-bus stretches, energy dips and low power, the reading plan, and a scripted day (1- and 3-frame blinks, a partial, optical coupling, a whole-array dip, low power, ramps, a death carried across a switch-off, an unexplained dip). |
| `testing/bench_day.py` | One real day through the program's own pipeline, no window; times each step, prints the stretches, writes exports and pickles. Heavy — ask first. |
| `testing/render_tabs.py` | Every tab to PNG under `minimal:enable_fonts` (real fonts, nothing on screen), from the scripted day or a bench pickle. |
| `testing/test_wheel_guard.py`, `testing/make_icon.py` | Wheel guard test; draws `icon.ico`. |
| `scan_share.py` | The real pipeline over any window, no GUI. Prints **every** no-data stretch with its length and how it was read, plus the full per-pulser fault log. |
| `testing/probe_morning.py` | Per-frame array level across a morning, printed to the console (no window). |
| `build_config.json` | Dev Tools build settings. Lists the eight reference PNGs so they are copied next to the exe. |
| `icon.ico` | The program's icon. |
| `gui_out/`, `23062026/`, `test01072026/` | Test output and captured days. Nothing reads them. |

**Run all the test files (`testing/test_pulser.py`, `test_gui.py`, `test_real.py`, `test_gate.py` and `test_blinks.py`) before changing the analyser.**

ROI boxes live **outside** this folder: `%APPDATA%\PulserMonitor\rois.json`.

---

## Layout of the module

| Lines (approx.) | Block |
|-----------------|-------|
| 1–240 | imports, colour and camera constants, `MIN_IMAGE_BYTES`, `CAM_COLS`, `_TABLE_CSS`, `_app_dir()`, `_bundled_ref()` |
| 240–325 | `RoiDefinition`, `SamplePoint` |
| 288–595 | **active-time gate**: span algebra, `_SpanSet`, `daytime_spans`, `_cpva_samples`, `high_power_spans`, `active_spans` |
| 596–865 | **full-power gate**: chunked archiver queries, `CamPower`, `PowerInfo`, `full_power_info`, `plan_reading` |
| 866–1290 | `AnalysisParams`, `DropoutEvent`, `ArrayTrip`, `PulserStats`, `StretchSummary`, `CameraAnalysis` |
| 1290–2800 | **the analyser** — `_pick_signal_and_ref` … `_stretch_ids`, `_stretch_scores`, `_summarise_stretches`, `analyze_camera`, `_attribute_trips` |
| 2800–3000 | image I/O, timestamp parsing, scale normalisation, matplotlib helpers |
| 2984–3305 | ROI grid auto-detection |
| 3305–3565 | small widgets, `_TimeWindowDialog`, gate and power workers |
| 3564–3840 | frame measurement (`_tile_floor_contrast`, `_measure_frame`, pool init) + scan workers |
| 3840–4578 | `_ImageCanvas`, `ROIEditorDialog` |
| 4578–6500 | the four tabs (`_MapTab`, `_RunGraphTab`, `_AllDataTab`, `_StatsTab`) + CSV writers |
| 6504–7990 | `PulserMonitorWidget` — the left panel, the scan pipeline, exports |
| 7990+ | `main()`, `apply_app_style()` (also used by `testing/render_tabs.py`) |

---

## Constants worth knowing

| Symbol | Value / meaning |
|--------|-----------------|
| `IMAGES_ROOT_OPTIONS` | `Lab` / `Office` spellings of `//users-L3.tier0.lcs.local/cpva-image-2026`. |
| `CAMERAS` | display name → CPVA folder: `PD1M1…PD4M1` → `C03-0NN-PDnM1DF-_-IMG`. |
| `CAM_COLS` | column labels per camera. **PD2M1's columns are mirrored.** |
| `MIN_IMAGE_BYTES` | 64 kB — a "was the file written at all" check only. Content is judged from pixels, **never** from file size: a PNG of a dim frame compresses better, so the old per-camera size gate discarded 37.9 % of every day — the entire warm-up stretch and every blank frame written during a trip. |
| `_FULL_SCALE_16` | 65535. The archiver stretches each camera's full scale onto the 16-bit range, so this is the camera-independent absolute scale. |
| `CONFIG_PATH` / `CONFIG_VERSION` | `%APPDATA%\PulserMonitor\rois.json`, schema v3. v1 used the wrong intensity scale, v2 measured contrast against a thin ring; loading either **re-measures the reference levels** from the bundled images. Box positions are never touched. |
| `GATE_HOUR_START` / `GATE_HOUR_END` | 7 / 21 (end exclusive), Prague. |
| `HPE_CHANNEL` | `L3-SIS-KEY:HighPowerEnable`. |
| `DUMPBUS_CHANNEL` / `ENERGY_CHANNEL` | `L3-PC<k>-PC<k>:VoltageInternalDumpBus` / `HAPLS-ENER_IN_PD<k>M1_LT7_DIAG2:Energy`. PC<k> drives PD<k>M1 (`_pc_of`). |
| `FULL_POWER_V` / `LIT_POWER_V` | 88 V (full power; actual 92–106) / 70 V (the diodes fire at all; warm-up 76–83). |
| `_ENERGY_MIN_RATE` | 1 sample/s of full power: below it an array's energy counts as not archived (PD2M1 has ~0.003). |
| `BELOW_FULL_CLR` | light cyan — lit but not at full power: ramps, low power, a whole-array dip. Not judged, not a stop. |
| `EXCLUDED_CLR` | indigo — gated-out time on every graph, deliberately distinct from the solid grey of a real trip, which means the opposite thing. |
| `STATE_ON/OFF/NODATA` | 1 / 0 / −1 per pulser per frame. |
| `KIND_DROPOUT/FAULT/TRIP/DEAD` | the four kinds a dark run can be. |
| `GAP_TRIP/DEAD/BIGGER/UNKNOWN/MANUAL/OFF` | what an array stop was: pulser trip / dead pulser / bigger problem / outage with no pulser / manual stop / switched off (`_GAP_LABEL` has the words). `GAP_TRIGGER` is kept as another name for `GAP_MANUAL`. |
| `_HPE_LOOKBACK_DAYS` | `(2, 14, 45)` — staged look-back for the key's state at the window start. Most scans find a transition within 2 days; the longer stages cover a window opening during a shutdown. |

---

## The active-time gate

Half-open `[start, end)` integer-ns spans throughout.

| Function | Role |
|----------|------|
| `_merge_spans` / `_clip_spans` / `_invert_spans` / `_intersect_spans` | span algebra |
| `_SpanSet` | a merged span list plus `total_ns`, `overlap_ns(a, b)`, containment tests |
| `daytime_spans(start, end, h0, h1)` | the working-day spans in Prague local time, DST included |
| `_cpva_samples(channel, start, end)` | one archiver query, `http.client` + `ssl` with verification off |
| `high_power_spans(start, end)` | the spans where the key reads 1. Handles three measured facts: the value comes back as a one-element list; **only transitions are archived**, so the opening state is found by looking back up to `_HPE_LOOKBACK_DAYS` (sized against an observed 11.6-day gap); and the `.value` alias other channels need returns HTTP 400 here, so it is never tried |
| `active_spans(start, end, gate_hours=True, gate_high_power=True)` | the intersection, plus the notes describing which rules applied and whether the archiver answered |

Both rules are **exclusions, never detections** — see the full ReadMe for why that
distinction is the whole point. `_GateWorker` runs `active_spans` off the UI thread
once per scan, before any camera is touched; a failure downgrades to the hour rule
and sets `gate_hpe_failed`, which the Statistics tab surfaces.

---

## The full-power gate

| Function | Role |
|----------|------|
| `_cpva_samples_chunked(channel, spans, chunk_s)` | the archiver refuses a day in one query (HTTP 500), so: 1 h chunks for the dump bus, 30 min for the energy, 8 threads; a refused chunk is split in two and retried (`_cpva_chunk`, two levels) |
| `_spans_where(samples, test, merge_ns, min_ns)` | runs of samples passing `test`, merged across gaps ≤ 3 s so one low ripple sample does not split a stretch |
| `_running_dev(v)` | each energy sample against the median of the 31 around it — a blink is a one- or two-sample dip |
| `_energy_low_spans` / `_energy_dips` | the low-power mode (below `low_power_frac` × the stretch median) / dips below `dip_dev` |
| `_cam_power(cam, windows, params)` → `CamPower` | one array: `full_spans`, `lit_spans`, `judged_spans` (full less the ramps), `has_energy`, `low_spans`, `dips`. Never raises; `failed` says why |
| `full_power_info(start, end, params, active)` → `PowerInfo` | all four arrays in parallel (PD2M1 needs the others' low power), `low_all` merged across them. Runs in `_PowerWorker` after the active-time gate |
| `plan_reading(files, cp, params)` | which listed frames are decoded: none outside `lit_spans`; at full power every frame without energy (PD2M1) or the frames within `dip_window_s` of a dip plus one per `sparse_step_s`; every frame in the last `stop_dense_s` before full power ends (the culprit); one per `sparse_step_s` while warming |

The widget runs: gate → `_PowerWorker` → listing → `plan_reading` per camera → measuring → `analyze_camera(..., power=cp, low_power=info.low_all)`. `self._scan_params` freezes the settings at Scan so the plan and the analysis agree. No dump bus → `power=None`, `gate_power_failed=True`, every frame read, full power from the images.

---

## Measurement

| Function | Role |
|----------|------|
| `_read_tiff_max_sample(path)` / `_to_unit_scale(arr, max_val)` | put every frame on one absolute 0…1 scale (`_FULL_SCALE_16`), so two frames are comparable |
| `_load_as_float32_gray(path)` | decode to greyscale float |
| `_tile_floor_contrast(arr, x, y, w, h)` | **the core measurement.** `p75(box) − p10(window reaching 35 % beyond the box)`. The local floor cancels the pulsers' optical crosstalk — each one lights its neighbours' tiles, so apparent brightness depends on how many others are lit, and a shared dimming drops out of a difference. The window reaches well beyond the box rather than hugging it because the ROI boxes are hand-drawn and some straddle a separator; with a thin ring those measured a healthy pulser as dead. |
| `_measure_frame(task)` / `_pool_init(roi_rects)` | one frame in a worker **process** (`ProcessPoolExecutor`) — decoding is CPU-bound |
| `_FileScanWorker` | enumerates `<share>/<year>/<month>/<day>/<hour>/<camera>/` on 12 threads. Folder names are UTC and unpadded; the filename carries a 19-digit Unix-ns timestamp (`_NS_19_RE`, `_parse_ts_from_path`). Almost pure network wait, so threads win ~17× |
| `_ScanWorker` | drives measurement + `analyze_camera` per camera |
| `_images_root_for_year(root, year)` | each year is its own share (`cpva-image-<year>`); the year is swapped inside the **share name**, which on a UNC path is part of the anchor |

---

## The analyser

Order matters here, and every step exists because doing it later produced a wrong
answer.

| Function | Role |
|----------|------|
| `_pattern_correlation(contrast_matrix, ref_contrast)` | per-frame correlation of the ROI contrast pattern against the reference's. **Scale-free**, which is the point: a diodes-off frame is not dark (camera gain lifts sensor noise to a mid-grey brighter than three quarters of a running frame) and its array level is almost warm-up's — what it lacks is *structure*. Measured: noise −0.22…−0.08, warm-up 0.891, running 0.897. |
| — | Frames below `pattern_min` are **removed from the series**, not kept as dark samples: the archiver writes them on a separate, much faster schedule (a 0.30 s noise stream alongside the real 5 s acquisition), and kept as samples they produced 1896 "trips" in one morning. The gap they leave is what marks the outage. |
| `_augment_timeline(...)` | inserts synthetic markers for time gaps and for the stretches dropped at either **end** of the window, and returns the per-frame excluded flags and the median cadence |
| `_detect_nodata(frame_means, synth, nodata_frac, frame_excluded)` | whole-frame darkness → no-data |
| `_pick_signal_and_ref(...)` | choose contrast (preferred) or plain mean per ROI, with the matching reference value |
| `_learn_baselines(...)` | each pulser's own alive level, optionally blended with the reference level (`use_reference` — needed to flag a pulser dark for the **entire** window, which has no learnt level of its own) |
| `_detect_warmup(...)` | the whole array at its second, much lower level (0.27–0.35 of normal, repeatable to better than a percent). **Must run before classification**: while warming, every pulser is below the OFF threshold and all forty would read as dropped out. Correcting each score by the warm ratio (~0.28, i.e. ×3.6) makes a genuinely dead pulser look alive — which is how a day's two real deaths were once dated to a mid-morning trip. Warm-up contributes minutes and nothing else. |
| `_debounce_states(raw, debounce)` | off by default (1), so single-frame flickers survive |
| `_stretch_ids(...)` | stretch index per frame: the dump bus's `judged_spans` when there is power info, else runs of judged frames broken by warm-up, gated-out time or a gap longer than `trip_min_s` |
| `_stretch_scores(...)` | **the score the states are decided on.** Per stretch: reference = each pulser's median over the first `ref_len_s`; score = contrast / reference / the frame's array level. A pulser whose reference is below `dead_rel` of the February prediction (*dark from start*) keeps the February score (`feb_score`). Frames whose level falls `array_level_min` below the rolling level around it, or with `array_down_n` pulsers dark, are `frame_change` — not judged |
| `_native_cadence_ns(...)` | the camera's own spacing (10th percentile of the gaps) — 0.3 s on the stream even when most frames read are 5 s apart; decides the culprit time |
| `_find_culprits(...)` | the pulsers that took the array down at a stop: lit earlier in the stretch, dark right up to it for `culprit_fast_s` / `culprit_slow_s`, never one dark since the stretch began. Looks at every scored frame, the unjudged ramp before the voltage falls included |
| `_array_stops(...)` | **every array stop** → `ArrayTrip` with its GAP_* kind, culprits and their fates (revived / dead / replaced / not restarted, read forward from the states), plus `frame_trip` / `frame_off` / `frame_restart`, `hints` for the culprit's dark run and `forced` states for its ramp frames. Stops come from the stretch bounds; a stop into gated-out time keeps only its watched part |
| `_compute_pulser_stats(...)` | dark runs → `DropoutEvent`s with their forward-looking verdicts (plus `min_score` and `partial`), then the `PulserStats` summary. A culprit's run takes its kind from `hints` (trip / dead). A death carries across a break (`dead_open` / `ep_carry`): a dark run opened before the pulser lit again extends the open DEAD event |
| `_summarise_stretches(...)` | `StretchSummary` per stretch (blinks full/partial, per pulser, dark from start, low power inside, unexplained energy dips) |
| `analyze_camera(...)` | the whole pipeline → `CameraAnalysis`. With `power`: ramps (`frame_ramp`), low power (`frame_low`) and voltage-overrules-warm-up; the ramps are scored (`score_sid`) but not judged. States: dark when score < `blink_below`, no hysteresis |
| `_attribute_trips(trips, stats, state_matrix, ...)` | per outage, which pulsers lit before it came back after it (`recovered`) and which did not (`died`), per up-period |

### Why a verdict is written backwards

Neither of the two interesting decisions can be made when a dark run *starts*:
whether it took the array down is known only when the stop comes, and whether the
pulser is recoverable only after the operator has stopped trying (several recoveries
are routinely fired back to back). So `_array_stops` and `_compute_pulser_stats` look
forward and write `kind` onto an event that began earlier.

For a pulser that did NOT take the array down, `KIND_DEAD` has two routes, and
`dead_confirm_min` is only half of one of them. `restarts >= dead_restarts`: it was
restarted and did not come back. The other is `not ep_lit_before and up_dark >= dead_confirm_min`, i.e. the
pulser never lit in this window at all and we have now watched the array run long
enough to say so — `ever_lit` records a sustained ON stretch (longer than
`dropout_frames`, the same bar a genuine recovery has to clear), and its value is
captured into `ep_lit_before` when the dark run opens. The minutes deliberately do
NOT stand alone: a pulser that ran, went dark and sat there while nobody restarted
the array is a `KIND_FAULT` for as long as that lasts, because the array merely being
up demonstrates nothing about a pulser nobody asked to come back. The operator's own
definition, 2026-09-14; `test_pulser.py` Test 6b pins all three outcomes.

The fault/trip line is **whether the pulser was the culprit of a stop**, not whether
an outage overlaps the dark run somewhere. The older reading counted a pulser merely
sitting dark through somebody else's outage as having caused one, and reported 47 of
them on a day with about eight.

### Wall-clock, not frames

`trip_min_s`, `culprit_fast_s` / `culprit_slow_s`, `manual_max_min`, `outage_max_min`,
`replaced_min`, `dead_confirm_min` are in seconds/minutes on purpose. A frame-counted threshold changes meaning by 16× between
the 5 s archive cadence and the 3.3 Hz stream: at 0.30 s/frame a 1.2 s stumble read
as an array trip (17 of them, and 17 restarts, in one day) and the one-second
power-down ramp read as eleven pulsers dying at 17:07.

### The shared denominator

`frame_judge` — array present, at full power (by the dump bus when there is one), not
warming, not below full power (`frame_below_full`: ramps, low power, a whole-array dip),
not tripped, not switched off, not gated out — is the single uptime denominator for all
forty pulsers. They are
switched on as a whole, so they must share it: the old per-pulser frame ratio gave
100 % both to a pulser that ran 7.5 h and to one that ran 5.75 h, because each got
its own denominator. `observed_total_ns` subtracts only the excluded time **inside**
the frame span, or a night beyond the last frame would zero out a result that plainly
described two hours of running.

---

## ROI editing and grid detection

`ROIEditorDialog` + `_ImageCanvas`: draw, move, resize boxes over the bundled
all-alive reference; `Load latest from share` fetches a current frame
(`_RefImageWorker`, `_find_latest_cam_folder`, `_find_ref_image`).

Reference levels are written **only** by `Capture reference`, which asks whether
the loaded image is the all-alive or the warm-up reference. The editor used to
re-measure them from whatever was on screen when OK was pressed, which quietly
replaced the all-alive reference with a live frame, dead pulsers included. Boxes
moved during the session are re-measured on OK, but only against a genuine reference.

`_detect_grid_rois(arr, cam_key)` (with `_smooth1d`, `_detrend`, `_axis_edges`,
`_box_blur2d`, `_cell_centers`, `_neighbor_clamps`, `_cell_bbox`, `_consensus_spans`,
`_sane_span`) is behind **Auto-create grid**, which discards every box and asks first
— the stored boxes are hand-tuned per camera and cannot be reproduced automatically.
See the `project-pulser-monitor-detection` note: the hand-tuned ROI maps must never
be regenerated.

---

## UI

```
PulserMonitorWidget
├── left panel   cameras · time window · ROI editor · active-time gate ·
│                detection settings · Scan / Stop · exports · log
└── QTabWidget
    ├── Pulser Map     _MapTab      grid (Status / Blinks / …) + per-pulser events + score
    ├── Statistics     _StatsTab    blinks per pulser / over time / between, and three logs:
    │                               Stretches at full power, Pulser events, Array down-time
    ├── All Data       _AllDataTab  one sortable row per pulser, per camera or all
    └── Run Graph      _RunGraphTab every pulser's state over real time
```

Helpers: `_WeekendDelegate` / `_NoScrollCalendar` / `_NoScrollComboBox` /
`_TimeWindowDialog` (house calendar style, wheel does not change a control the mouse
merely hovers), `_shade_excluded` (the indigo hatch, shared by every plot),
`_decimate_states`, `_grid_layout_for`, `_NumericTableItem` (numeric sort in the
table), `_make_mpl_toolbar` (builds the matplotlib toolbar so its icons are **not**
tinted — under the dark palette a tinted toolbar goes invisible; see
`project-mpl-toolbar-icons`).

Cameras are scanned one after another; **Stop** uses a generation counter, so a
cancelled scan is dropped rather than killed mid-read.

### Exports

`_write_alldata_csv`, `_write_events_csv` (every event, ms times, `blink`, `partial`,
`lowest_score`), `_write_stretches_csv`, `_write_trips_csv`, `_write_warmup_csv`,
`_write_gate_csv`. Times in tables and CSVs go through `_fmt_ms` / `_fmt_ms_in` (no date
when the result lies in one day) and durations under a minute through `_fmt_dur_short`. `Export all (folder)` writes, per camera: the per-frame CSV
(brightness, score and state per pulser, plus the frame's own state — `excluded` for
a gated frame), the event log, the trip log with its suspected cause, the warm-up
windows, and PNGs of the map, run graph and statistics — plus one master CSV across
all cameras and `pulser_analysed_time.csv`.

That last file is what keeps the bundle readable months later: every other export is
*per observed time*, and a trip log with two entries looks identical whether the
window was watched throughout or two thirds of it was excluded. It records the
window, the observed and excluded totals, which rules applied, and every excluded
span.

---

## Dependencies

```
PySide6                     UI
numpy                       all the measurement and analysis maths
Pillow                      frame decoding
matplotlib (QtAgg)          the four tabs' plots
http.client, ssl, urllib    the archiver query for the high-power key
zoneinfo                    Prague time
concurrent.futures          ProcessPool for measuring, ThreadPool for listing
```
