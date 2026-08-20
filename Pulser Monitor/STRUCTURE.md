# Pulser Monitor — STRUCTURE

> Verified against source: 2026-08-19 · `pulser_monitor.py` 6901 L

Single-file PySide6 application: measurement, analysis and UI in one module. Run it
with `python pulser_monitor.py`.

User-facing documentation: `ReadMe_Pulser Monitor.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Pulser Monitor_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

The detailed ReadMe is the authority on *what the words mean* — dropout / fault /
trip / dead, outage / trigger off / diodes off — and this file is the map of *where
the code is*.

## Files

| File | Description |
|------|-------------|
| `pulser_monitor.py` | The whole application. |
| `C03-*-allgood.png` | Per-camera reference frame with **every pulser lit**. Every measurement is relative to this. Without it a pulser dark for the whole window cannot be flagged at all. |
| `C03-*-warmup.png` | Per-camera reference frame taken **while warming up**. Without it warm-up cannot be measured. |
| `C03-*-_-IMG_-_*.png` | Captured sample frames. |
| `test_pulser.py` | Offline analyser test: synthesises every dropout / fault / trip / death and every kind of no-data stretch out of the real reference frames. |
| `test_gui.py` | Headless test of the four tabs and every CSV / PNG export. |
| `test_real.py` | The same pipeline on genuine running and warm-up frames. |
| `test_gate.py` | Active-time gate: span algebra, an overnight window, the panel controls. `--live` adds a real archiver query. |
| `scan_share.py` | The real pipeline over any window, no GUI. Prints **every** no-data stretch with its length and how it was read, plus the full per-pulser fault log. |
| `probe_morning.py` | Per-frame array level across a morning. |
| `build_config.json` | Dev Tools build settings. Lists the eight reference PNGs so they are copied next to the exe. |
| `gui_out/`, `23062026/`, `test01072026/` | Test output and captured days. Nothing reads them. |

**Run all four test files before changing the analyser.**

No `icon.ico` yet — every other program has one, and both the Launcher card and the
built exe use it.

ROI boxes live **outside** this folder: `%APPDATA%\PulserMonitor\rois.json`.

---

## Layout of the module

| Lines (approx.) | Block |
|-----------------|-------|
| 1–130 | imports, colour and camera constants, `MIN_IMAGE_BYTES`, `CAM_COLS` |
| 130–230 | `_app_dir()`, `_bundled_ref()` — frozen-aware asset lookup |
| 228–312 | `RoiDefinition`, `SamplePoint` |
| 314–615 | **active-time gate**: span algebra, `_SpanSet`, `daytime_spans`, `_cpva_samples`, `high_power_spans`, `active_spans` |
| 615–900 | `AnalysisParams`, `DropoutEvent`, `ArrayTrip`, `PulserStats`, `CameraAnalysis` |
| 897–2070 | **the analyser** — `_pick_signal_and_ref` … `analyze_camera`, `_attribute_trips` |
| 2070–2270 | image I/O, timestamp parsing, scale normalisation, matplotlib helpers |
| 2268–2565 | ROI grid auto-detection |
| 2565–2810 | small widgets, `_TimeWindowDialog`, gate worker |
| 2804–2970 | frame measurement (`_tile_floor_contrast`, `_measure_frame`, pool init) + scan workers |
| 3067–3810 | `_ImageCanvas`, `ROIEditorDialog` |
| 3807–5110 | the four tabs (`_MapTab`, `_RunGraphTab`, `_AllDataTab`, `_StatsTab`) + CSV writers |
| 5434–6821 | `PulserMonitorWidget` — the left panel, the scan pipeline, exports |
| 6821+ | `main()` |

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
| `EXCLUDED_CLR` | indigo — gated-out time on every graph, deliberately distinct from the solid grey of a real trip, which means the opposite thing. |
| `STATE_ON/OFF/NODATA` | 1 / 0 / −1 per pulser per frame. |
| `KIND_DROPOUT/FAULT/TRIP/DEAD` | the four kinds a dark run can be. |
| `GAP_TRIP/TRIGGER/UNKNOWN/OFF` | how a no-data stretch is read. All four used to be one thing ("trip"), which is why a 2 s archive hiccup and the end-of-day switch-off appeared next to genuine outages. |
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
| `_classify_nodata_runs(...)` | no-data runs → `GAP_TRIP` / `GAP_TRIGGER` / `GAP_OFF` / `GAP_UNKNOWN`, and splits an uncleared outage into outage + diodes-off. A run shorter than `trip_min_s` gets **no kind at all** — it never becomes an `ArrayTrip`, so it breaks a run (nothing is joined across it) but blames nobody and is not listed. |
| `_dark_run_before(row, data_idx, before, lo)` | was a pulser already dark right up to this outage, and for how long |
| `_debounce_states(raw, debounce)` | off by default (1), so single-frame flickers survive |
| `_compute_pulser_stats(...)` | dark runs → `DropoutEvent`s with their forward-looking verdicts, then the `PulserStats` summary |
| `analyze_camera(...)` | the whole pipeline → `CameraAnalysis` |
| `_attribute_trips(trips, stats, state_matrix, ...)` | names the pulser that caused each outage: dark, unbroken, right up to the fall, for at least `trip_cause_frames` — **and** lit at some point since the array last started, so a long-standing fault is not blamed for every outage of the day |

### Why a verdict is written backwards

Neither of the two interesting decisions can be made when a dark run *starts*:
whether the array fell is known a few frames later (`trip_lookahead_frames`), and
whether the pulser is recoverable only after the operator has stopped trying
(`dead_restarts` / `dead_confirm_min` — several recoveries are routinely fired back
to back). So `_compute_pulser_stats` looks forward and rewrites `kind` on an event
that began earlier.

The fault/trip line is **whether the array fell within the look-ahead**, not whether
an outage overlaps the dark run somewhere. The older reading counted a pulser merely
sitting dark through somebody else's outage as having caused one, and reported 47 of
them on a day with about eight.

### Wall-clock, not frames

`trip_min_s`, `trigger_off_max_s`, `diodes_off_min`, `dead_confirm_min` are in
seconds/minutes on purpose. A frame-counted threshold changes meaning by 16× between
the 5 s archive cadence and the 3.3 Hz stream: at 0.30 s/frame a 1.2 s stumble read
as an array trip (17 of them, and 17 restarts, in one day) and the one-second
power-down ramp read as eleven pulsers dying at 17:07.

### The shared denominator

`frame_judge` — array present, at full power, not warming, not tripped, not switched
off, not gated out — is the single uptime denominator for all forty pulsers. They are
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

Reference levels are written **only** by `Capture reference...`, which asks whether
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
    ├── Pulser Map     _MapTab      grid + per-pulser timeline over its score
    ├── Statistics     _StatsTab    counts, events over time, array log, pulser log
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

`_write_alldata_csv`, `_write_events_csv`, `_write_trips_csv`, `_write_warmup_csv`,
`_write_gate_csv`. `Export all (folder)` writes, per camera: the per-frame CSV
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
