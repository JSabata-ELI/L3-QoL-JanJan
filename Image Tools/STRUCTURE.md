# Image Tools — STRUCTURE

> Verified against source: 2026-09-07 · `main.py` 415 L · `if_t.py` 14687 L ·
> `is_t.py` 28733 L · `sf_t.py` 5218 L · `wk_t.py` 8331 L ·
> `cpva_client.py` 1672 L · `img_scale.py` 864 L
>
> One Moment was merged into the Image Finder on 2026-09-04 and `om_t.py`
> deleted; its section below says where each half went. The `sf_t.py` (Shot
> Finder) section is current as of
> 2026-08-24. The rest of this document was last verified on 2026-08-19 and the older
> modules have grown since — treat their line references as approximate.

User-facing documentation: `Readme Image Tools.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Image Tools_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `main.py` | Entry point. Starts `QApplication`, builds the main window with a `QTabWidget` — **four tabs**: Image Finder, Image Slider, Shot Finder, Workshop. Detects the version from the exe name. The Slider is loaded FIRST, because `if_t.py` and `sf_t.py` borrow its helpers through `sys.modules["image_slider"]` and would otherwise exec 26k lines of it a second time. One Moment was merged into the Image Finder on 2026-09-04 and `om_t.py` deleted, so there is no fifth tab and no fallback error tab for it any more; `finder._slider_tab_idx` is what its two Send-to-Slider buttons use. The wiring is symmetric since 2026-09-17: `viewer._finder_ref` / `shot_finder._finder_ref` plus `_finder_tab_idx` (and `viewer._tab_widget`, which it had never been given) are what the `➤ Image Finder` buttons use. |
| `if_t.py` | **Image Finder** — one camera across many days, side by side on one shared scale. Also picks the frames: for a day, for a range of days, by PV conditions, or with no target at all. |
| `is_t.py` | **Image Slider** — time-series viewer. Scrubbing, playback, pointing, spatial contrast, multi-cam, live mode. |
| `sf_t.py` | **Shot Finder** — finds frames by PV value (energy, waveplate…) in the CPVA archive. |
| ~~`om_t.py`~~ | **Gone (2026-09-04).** One Moment was merged into the Image Finder; see the section below for where each half went. Its module docstring — a design spec — is now `if_t.py`'s header. |
| `wk_t.py` | **Workshop** — look at, measure and annotate images handed over from the other tabs. Magnify to a chosen spot, non-destructive brightness/contrast/gamma/palette, histogram (as a picture and as numbers), several measuring regions with a results table and CSV export, profiles with FWHM / 1-e² / Gaussian fit, beam width and roundness, radial profile and encircled energy, vector annotations incl. angle and a calibrated scale bar, filters (median / blur / sharpen / edges / background removal), rotate-any-angle / straighten / bin / crop / resize, reference subtraction, combining several images (average / max / sum / min, red-green merge), comparison views, PNG / TIFF / JPEG export, a 16-bit data TIFF of the measured values, animated GIF / PNG / WebP, and a session file for the drawing. |
| | Inside `wk_t.py`, above the tab and Qt-free: **PIXEL OPERATIONS** — neighbourhood filters (median / Gaussian / unsharp / Sobel, scipy when present, numpy fallback always), background maps (percentile / plane / quadratic surface / morphological rolling ball), projections across frames, red-green merge, arbitrary rotation with the matching annotation point map (Pillow's own matrix, reproduced so the two cannot disagree by a pixel), binning, animation writer, 16-bit data TIFF writer — and **BEAM MEASUREMENTS** — FWHM and 1/e² widths by interpolated level crossing, Gaussian fit with r² (scipy refines, moments stand in), D4σ with principal axes / tilt / roundness, radial profile, encircled energy. |
| `cpva_client.py` | Shared CPVA archiver client: day cache, warming, nearest-sample and look-back lookups, tri-state results. |
| `daypicker.py` | **How a day and a time window are picked** — the ONE calendar. Owns the look (`CAL_STYLE`, `MultiSelectDelegate`, `NoScrollCalendar`, `make_calendar`, `weekday_gate_row`), the click rules (`compute_click`), the defaults (`default_window_for`, `last_hour_window`), the time primitives (`PickSeg`, `hour_end_hm`, `seg_bounds_ns` — no Qt, testable on their own) and the dialog itself (`DayTimePicker`). Loaded through the `_import_daypicker()` sibling-import, so every tab shares one instance. See **Picking days and times** below. |
| `img_scale.py` | **What an intensity means** — the ONE owner of the absolute scale (`to_absolute_u8` / `to_u8`), the gamma curve (`gamma_from_slider` / `auto_gamma` / `gamma_for_median`), the explicit per-frame stretch (`stretch_u8`, `percentile_window`), raw-counts recovery (`derive_factor` / `to_counts`) and the frame metadata behind the readout (`meta_from_info`, `FrameMeta.scale_note`). Pure numpy/PIL, no Qt; every tab loads it through the `_import_img_scale()` sibling-import so all five share one instance. |
| ~~`testing/test_one_moment.py`~~ | **Gone (2026-09-04)** with `om_t.py`. Its coverage lives in `testing/test_finder_moment.py` (the resolver, the folder-reading count, several picks on one wall), `test_pv_graph.py` (the axes and hold-forward), `test_pv_stats_and_formula.py` (the range statistics with the held `n = 0` row, the graph controls, a formula over time and the engine's own rules) and `test_saved_moments.py` (navigation saving nothing, the session-only list, the band on the row that is on screen, the prefetch). |
| `testing/test_settle_native.py` | Drives a real 6-camera archive grid offscreen: drag, release, then measure. Asserts the tiles reach `HQ_SIDE` (`_diag_hq > 0` and every `_cam_shown_key` at `HQ_SIDE`) and that an idle panel stops repainting. Guards the `_refine_current_frame` → `_display_multicam_index` → `_proxy_try_paint_cam` → `_schedule_refine` loop, which pinned every tile at preview resolution and burned 30 repaints/s. Synthetic local frames, no share. Not shipped (`test_` prefix). |
| `testing/test_subtraction_stats.py` | **The subtraction numbers may not depend on the display controls.** On a synthetic archive-shaped pair: the reported min/mean/max and the whole histogram come out IDENTICAL at gamma 1.00, at gamma 0.50 and with Auto gamma (while the picture on screen still visibly brightens, so gamma was moved rather than dropped); the held Auto values are all-or-nothing and give the same render parameters every frame; a smaller render never replaces a measured set and never borrows another frame's; the decode size really does reach the statistics and really does change the maximum; and a frame subtracted from itself is still exactly zero. Pure functions plus a `QGuiApplication` — no window. Not shipped (`test_` prefix). |
| `testing/test_subtraction_steady.py` | The same complaint end to end in a real viewer: five frames, all three Auto boxes ticked, stepped one at a time. Every frame must be adjusted by the same contrast/offset/gamma with no Auto sentinel left, those frozen values must be a real measurement rather than the "do nothing" the all-zero reference frame would hand over (`_diff_worth_measuring`), every frame must be measured at full resolution with no "not final" mark left behind, the numbers must still follow the pictures, and coming back to a frame must report the maximum it reported before. Then **Subtraction is clicked off and on**: the histogram box keeps its height, the checkbox does not move a pixel, the numbers stay the same difference, stepping a frame with the box OFF still moves them, and switching back on reports the same figures. Window moved off the visible desktop. Not shipped (`test_` prefix). |
| `testing/test_remove_ref.py` | **Remove ref undoes Set ref completely**, one camera and three: the reference gone, its decoded cache dropped, the green badge off the picture and off each cleared tile only, the difference line and histogram empty, the frozen Auto values and the remembered numbers released, the buttons in the right state, Subtraction left as the user set it, and the next render asking for no reference. Also the scope rule — the cameras selected at the moment of the click, the others untouched. Window moved off the visible desktop. Not shipped (`test_` prefix). |
| `testing/render_ref_buttons.py` | Draws the Subtraction controls at the real 275 px width under `main.py`'s own stylesheet and prints, per button, whether its label fits: the Set ref / Remove ref pair on a row of their own, equal width, nothing elided, nothing past the panel. Shoots both `ref_buttons.png` (live) and `ref_buttons_off.png` (Remove ref greyed), which is what showed a disabled button drawn in full black text. Not shipped (`render_` prefix). |
| `testing/test_hold_and_slave_free.py` | Two multi-camera navigation rules on the real widgets and the real signals: a **slave slider stays where it was put** (drag one, release, and neither settle tier may pull it to the master's moment — see *A redraw must not move a camera*), and a **held frame arrow ramps up** — `_hold_rate` checked as a pure function over the whole `HOLD_STEP_RAMP` table (2-3-4-5, then 8 at five seconds and 10 at eight), then one frame on the press and the rungs sampled off `_hold_timer.interval()` against the real clock, with a runaway bound on the frame count. Synthetic local frames, no share. Not shipped (`test_` prefix). |
| `testing/test_scale_invariance.py` | Checks the archive's storage rule on real frames: `stored_max / MaxValue` → a `65535/(2**bits-1)` factor, that same bracket predicted from `MaxValue` alone (`bits_from_max_value`, the rule the preview layer relies on), and unsaturated frames exist, and that no bracket is deeper than `SENSOR_BITS`. Also reports which cameras straddle a bracket (i.e. would flicker without the fixed range) and which `Camera type` models are present — a second model is the other way `SENSOR_BITS` could stop being true. Run it from the lab against a camera-hour or a day; exit 1 on a violation. Not shipped (`test_` prefix). |
| `build_config.json`, `icon.ico` | Dev Tools build settings and the app icon. |
| `image_tools_diag.log` | Diagnostic log written by the Slider (`Viewer._diag_log`), not part of the app. Also carries `dots=G../R..:<reason>` and the `cpva …` counters, so a transient red dot or a missing PV leaves evidence behind. |
| `testing/bench_live_latency.py` | **How long a live frame takes to reach the screen after it lands in the folder** — the number behind "it used to be instant". Writes shots from a background thread (its own sleeps would otherwise block the event loop and the watcher push would only be handled once the file was whole) and times `_cam_shown_ts_ns` against the moment each file became COMPLETE. `--write-ms` fills the file over that long after creating it, which is what a camera writing straight onto the share does; `--atomic` renames a finished `.tmp` into place instead; `--read-ms` simulates the share; `--trace` prints what every tile showed and when; `--before` undoes both fixes for an A/B. Found the two independent causes and pins them: a truncated read nothing re-asked for (1 camera, 1531 → 62 ms) and a slave synced at the master's request time (6 cameras, p95 1734 → 125 ms). |
| `testing/bench_pv_latency.py` | **How long after the archiver publishes a shot's sample the number appears.** The other PV harnesses call `cpva.invalidate()` when they publish, which hands the client knowledge it does not have in the field; this one does not, so the panel must find the sample through the day cache's TTL and the wait ladder — which is where the delay lived. Reports latency AND tail requests, because any change that shortens one by inflating the other is not an improvement; `--trace` prints every fetch and what it aimed at (this is what showed the sample arriving 80 ms after the 400 ms retry with the next rung 800 ms away), `--before` restores the pre-fix ladder. p50 796 → 16 ms at a 2.5 s cadence for +0.6 requests per shot. |
| `testing/bench_drag.py`, `testing/bench_play.py`, `testing/bench_live_dot.py`, `testing/bench_pv_wait.py`, `testing/bench_pv_live_multi.py`, `testing/bench_master_sync.py`, `testing/bench_live_slave_refresh.py`, `testing/bench_live_pv_load.py`, `testing/test_pv_resilience.py`, `testing/test_step_channel_detect.py`, `testing/bench_common.py` | Headless offscreen harnesses driving a real `Viewer`: the PV panel surviving a wedged archiver fetch and a dead trigger chain (`testing/test_pv_resilience.py` — the freeze that used to need a restart), drag scheduling, playback, the live refresh-dot verdict, the PV panel waiting for the archiver instead of trailing the picture by a shot (single-cam, then the same promise in **live multi-cam**, fake transport, no network), every per-camera slider handle following the master, **every camera repainting on every shot in live mode** (mismatched cadences, the toggle, and the one camera that must stay put), which PVs are read as "written only when they change" and therefore hold their value between samples (`testing/test_step_channel_detect.py` — the GDD setting that showed `no data yet`; it also checks the other half, that a per-shot energy is never held forward, and runs against the real archiver when one is reachable), and whether the PV panel starves the PICTURES under a live load (both the 145 ms share read and a 250 ms archiver round-trip simulated — with neither, nothing can starve anything). Not shipped. |
| `testing/bench_one_moment_frames.py` | **What finding a moment's frames actually costs**, against a fake archive on local disk. Builds the tree in the shape that made it slow — cameras absent from some hours (`--present`, the real case: 92 cameras, 27 of them in every hour) plus bystander cameras crowding the hour folder (`--bystanders`) — and runs the same resolve three ways: the walking resolver with no shared cache, `DayScanCache` cold, and `DayScanCache` warm. It counts FOLDER READINGS rather than trusting the seconds, because local disk is nothing like SMB; multiplying by ~150 ms is what carries over. At 20 cameras with 70 bystanders: 1816 readings walking, 25 cold, 0 warm. Fails when the warm pass reads anything at all. Since 2026-09-04 it loads `if_t.py`, which is where the resolver and the shared cache live. Not shipped (`bench_` prefix). |
| `testing/bench_live_rollover.py` | The one live fault none of the others can reach: **following the archive across a UTC hour rollover**. Every case in `testing/bench_live_dot.py` lives in one flat folder, so `poll_scan_folders` picking the newest hour and the `_probe_hour_folder` discovery of the next one are never exercised. A rollover failure is **silent** — nothing raises, nothing hangs, and `_live_health` reads "no new images" as a healthy idle source — so the dot stays green while the app has stopped following the archive. Uses the real …/YYYY/M/D/H/CAM layout with unpadded names; opens on the previous UTC hour and creates the current one, because `_probe_hour_folder` refuses a candidate whose hour is still in the future. `--cams 3` runs the multi-cam grid path. |
| `testing/test_daypicker.py` | The one day/time picker, rule by rule: a plain click leaving one day, **Ctrl+click working on a Saturday** (the rule that changed — it used to refuse and the click did nothing), a Ctrl+Shift stretch skipping the weekend, XOR-ing back out and never dropping its anchor, 07:00–21:00 for a past day and 07:00–now for today, **the table appearing at the second day and going away again** with no "Multiple days" tick anywhere, the global From/To moving every day at once while a row typed by hand stays put, OK and Cancel both present, Live mode offered ONLY when `allow_live` and leaving a chosen window alone, and — rendered against the app's dark stylesheet and sampled pixel by pixel — the dialog and the Mon–Sun row painting their own light ground with red weekends. Offscreen, no share, no network. Not shipped (`test_` prefix). |
| `testing/test_sf_load_split.py` | Shot Finder's two load buttons: **Load data** reads the archiver and not one folder on the share, its rows are green "data" rows with no picture claimed, and its day detail opens (shots + curve) without a share read either; **Load images** straight afterwards asks the archiver NOTHING and only looks for the frames, including after the camera list has been changed; a retyped band is different numbers and IS read again. Also: the rows STAY on screen (same table object, same row per day, nothing doubled) while the `Image` column goes dash → tick, a frame that is not on the share gets the cross instead, the (day × camera) hunts overlap (more than one thread, and six units cost about one), and listing a frame costs no decode while the deep test still knows a blank one. Offscreen, archiver and share both stubbed. Not shipped (`test_` prefix). |
| `testing/test_cpva_span_cache.py` | `get_day(span_ns=…)`: only the picked hours are requested, a question inside a cached slice costs nothing, a question the slice cannot answer refetches (and the whole day then answers the narrow one), `peek_day` reports a miss rather than half a day, `warm_days(span_for_day=…, max_workers=…)`, and the wave bound keeping the cache inside `_DAY_CACHE_MAX`. Counter instead of a transport — no network, no Qt. Not shipped (`test_` prefix). |
| `testing/render_sf_load_buttons.py` | Renders the two load buttons in the 275 px panel and a table of "data only" rows **with a real platform** (offscreen has no fonts and lies about text size): both labels whole, the rows green rather than red, the folder cell a grey dash, and the preview pane carrying a readable line instead of standing empty. Writes `sf_load_buttons.png` / `sf_data_rows.png`. Not shipped (`render_` prefix). |
| `testing/test_day_split.py` | Pins `cpva_client.fetch_samples_split`: an oversize window is halved until it fits and `get_day` still returns **every** sample; an error splitting cannot fix (4xx, bad JSON) is raised at once, with no split storm; a window already at the floor is not split; `CpvaBusyError` (our own pool, archiver never reached) is never split. Fake transport — no network, no share. |
| `sort_by_camera.py` | Standalone utility, not part of the app: takes the per-shot folders the camera screenshot tool writes (`<source>/DDMMYYYY_HHMMSS/`) and re-files them into `<source>/final/<CAMERA>/`, one folder per camera. |
| `HANDOFF_02072026.md` | Historical hand-over note from 2026-07-02. Kept for context; `STRUCTURE.md` and `image_tools_structure.md` are the current documents. |

> The stray `sp_t.py` copy of the Spectra program is gone — nothing here imports it.
> A line-by-line map lives in `image_tools_structure.md`.

---

## Picking days and times

**`daypicker.py` owns this. Every tab opens `DayTimePicker`; no tab builds a
calendar of its own.** It has also outgrown this program: the **Spectra tab of CSS
Logger** uses the same calendar, and `CSS Logger/daypicker.py` is a byte-identical
copy of this file. It has to be a physical copy, because the builder bundles only
the `.py` files in a program's own folder. **This file is the master** — edit it
here, then copy it over there; `CSS Logger/testing/test_daypicker_sync.py` fails
while the two differ. Before it, this program carried three different calendars —
the Slider's dialog (From/To to the minute, a "Multiple days" tick, no
Ctrl/Ctrl+Shift), the Finder's panel calendar (Ctrl/Ctrl+Shift, one Hour dropdown,
a Close-only window that applied every click at once) and a third in PV Search —
plus three near-identical copies of the delegate, the factory and the stylesheet,
and a whole multi-day dialog nothing ever opened. Picking a day meant relearning
the widget per tab. The rules, in full:

1. **One calendar.** Never a Start/End pair side by side.
2. **Clicking.** Plain click → exactly that one day. Ctrl+click → adds or removes
   **one day, any day, weekends included**. Ctrl+Shift+click → the stretch from
   the last click, XOR-ed in (the same stretch again takes it back out), and the
   day you started from is never dropped. **Saturdays and Sundays are skipped only
   by the Ctrl+Shift stretch**, and only while they are unticked in the Mon–Sun
   row (Mon–Fri ticked by default). Ctrl+click always takes any day — a click that
   silently does nothing reads as a broken widget, which is what the old
   "weekends by plain click only" rule looked like.
3. **More than one day switches the per-day time table on by itself.** There is no
   "Multiple days" tick any more: one day → no table; two or more → a row per day
   with its own From/To; back to one → the table goes away. `is_multiday()` is
   `len(days) > 1`.
4. **A day just added gets 07:00–21:00**, or 07:00–the current hour if it is today
   (`default_window_for`, never earlier than 08:00 so a day picked at 07:20 does
   not open on an empty window). 07–21 since 16.09.2026, on the operator's word:
   nothing is ever shot before seven or after nine at night, and 08–19 cut off
   both ends of the shift. A day already in the list keeps its times. Once
   the user has changed From/To by hand, a newly added day follows THAT window
   instead — typing 10:00–12:00 and then clicking another day must not throw the
   typing away.
5. **Always OK and Cancel.** Nothing takes effect until OK.
6. **The house look.** Monday first, white day cells, Sat/Sun red (spill-over days
   too), day names on a grey band. The dialog and the Mon–Sun row paint their OWN
   light ground: the app runs a dark stylesheet, and a calendar that only styles
   its cells ends up with dark-blue "Live mode" on near-black. `test_daypicker.py`
   samples the rendered pixels for exactly that.
7. **Live mode is the Image Slider's alone** (`allow_live`). Shot Finder, One
   Moment and Image Finder pass `allow_live=False` — a tick that does nothing
   reads as a broken tick. Ticking it moves the day to today and **leaves the
   times alone**; only when nothing has been chosen yet does it fall back to the
   last hour.

The global From/To is a bulk setter: it moves every day that has not been given
its own time in the table. A row typed by hand is pinned and stays put.

**The `Now` button (`_go_to_now`) makes the pick today's current hour** — one day,
`last_hour_window`, the calendar cursor on today, and `_global_touched` cleared so a
day added afterwards falls back to rule 4 rather than trailing this hour. It used to
move the calendar's cursor and nothing else, which left the pick on whatever day and
window was already there: the button said "now" and the dialog still returned
yesterday morning. Live mode is deliberately NOT touched — following new images as
they arrive is a separate choice, and its own tick sits right beside the button.

`is_t.DatePickerDialog` is a thin subclass that adds only the archive camera scan;
`sf_t.py` and `om_t.py` open it unchanged. **Image Finder has no lab-time mode and
no automatic "strongest hour"** — both were removed: the RAMPING CSV the auto-hour
leaned on stopped being written on 26.05.2026, so it fell back to a hard-coded
14:00, measurably the *thinnest* hour of 06.08.2026 (4540 frames against 11985 at
13:00). The window is the operator's choice; finding the moment inside it is what
the PV search is for.

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
  The display therefore renders `counts / (2**SENSOR_BITS − 1)` = `counts / 4095`
  (`reference_bits`, `full_scale_for_frame`, `full_scale_for_pil`). **`SENSOR_BITS` is a
  constant, not learned.** Measured 2026-08-20 over one whole day: all 88 camera folders
  report `Camera type = 'Basler acA1600-20gm'`, one 12-bit model with no exceptions, and
  no frame anywhere reports a `MaxValue` above 4095. So the denominator comes out of each
  frame's own metadata and is the same for every frame of every camera — nothing is
  remembered between sessions, and no single frame can move it.
  This replaced a per-camera "largest bracket ever seen", learned into
  `%APPDATA%\ELI_ImageTools\cam_depths.json`. That was wrong both ways: a camera seen only
  while dim was rendered against its dim bracket, and one frame with a high `MaxValue`
  darkened a camera permanently and invisibly — C03-081-PCW3NF ended up recorded at 16
  bits and painted codes 0..3 of 255 (black) on ordinary pictures. Delete the leftover
  file; nothing reads it.
  It is expressed as a denominator, not as pixel arithmetic:
  `display_full_scale(frame_bits)` is **exactly 65535** on a frame the archiver bracketed
  at the sensor's own depth, i.e. a saturated one, and larger for a dimmer frame — which
  is the point, a dim frame IS darker. It never goes below the frame's own bracket, so a
  deeper camera added later widens the range on its own.
  The factor is also what prints counts, recovered per frame as `stored_max / MaxValue`
  (`derive_factor`).
  `MaxValue` (PNG tEXt) is the **peak of that frame in raw counts**, not the sensor's
  range — it reads 4095 only when the sensor is genuinely saturated. Never divide by it.
  Verify the rule with `testing/test_scale_invariance.py` after any archiver change; it also
  reports which cameras straddle a bracket.
  Measured 2026-08-17 on `2026/8/14/12` (`testing/test_scale_invariance.py`, 0 violations):
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
  `Auto` contrast / brightness / gamma switches in the Finder / Shot Finder / Slider,
  `Auto contrast` / `Auto brightness` in the Slider, the Workshop's Auto-BC button, and
  the `Binary` (min..max) / `False Colors` (p0.5..p99.5) palettes. Everything else is
  absolute.
- **An Auto box is its own slider set automatically — never a fourth operation.**
  `Auto contrast` picks the GAIN that spreads this frame's p0.5..p99.5 window and leaves
  the black level where it is; `Auto brightness` picks the OFFSET that puts that black
  level at 0 and spreads nothing. Both are computed in `img_scale.render_u8` from the
  `AUTO_*` mask and applied through the same code as the sliders, so the number a box
  reports is a number the slider can be set to, and setting it there gives back the same
  picture. Ticking both composes into the old full percentile stretch.
  This replaced a design where BOTH boxes ran that stretch: the two Autos were
  indistinguishable from each other and neither resembled its own slider, which is what
  the operator reported on 09.09.2026.
- **The pair is applied in full precision, before the rounding to 8 bits**
  (`img_scale.absolute_f` → gain → offset → one `clip().astype(uint8)`). The dim cameras
  run p0.5..p99.5 over about six 8-bit codes, so a gain applied after the rounding came
  out in posterised bands while Auto — which worked on the 16-bit data — came out smooth.
- **The Contrast curve is a doubling curve**, `gain = 2 ** (c / CONTRAST_PER_DOUBLING)`
  with `CONTRAST_PER_DOUBLING = 64`, over ±`CONTRAST_MAX` = 384, i.e. 1/64x .. 64x. Over
  the old ±127 range it is the old rational curve to within 2 % on the positive side, so
  an earlier number still means what it meant; what it adds is the reach a dim frame
  needs (40x and more), without which Auto contrast could not be a slider value at all.
- Gamma is NOT switched off by the other two Autos any more. It used to be, because the
  stretch set both ends of the frame itself; a gain and a curve compose exactly as they
  always did with the manual slider.
- matplotlib toolbars are built through `_make_mpl_toolbar` so the dark palette does
  not tint the icons away. **The light-palette `host` it builds is then hidden and
  sized to nothing** (14.09.2026): the caller puts the TOOLBAR into a layout, which
  re-parents it away and leaves the host behind as a child no layout owns — and such a
  child sits at (0, 0) of the window at its default 100 × 30, painted in the window's
  own `#f3f3f3`. That was the "invisible rectangle" reported in the top-left of the PV
  Search panel: background-coloured, standing still however far the panel was scrolled,
  and visible only by what it blanked. The same one-liner is in CSS Logger's and Pulser
  Monitor's own copies of the helper. `testing/render_pv_search.py` prints any orphan
  child of the window, so it cannot come back unnoticed.

---

## cpva_client.py — shared archiver client

| Symbol | Purpose |
|--------|---------|
| `fetch_samples` / `parse_samples` / `fetch_values` / `fetch_values_ex` / `fetch_channels` | raw REST access (`_request_json`, one pooled HTTPS connection). `fetch_values_ex` also returns **which** name produced the samples — the bare channel or its `.value` alias — and `_Entry.src_channel` carries it, so the incremental tail query of an alias-only channel does not come back empty forever |
| `fetch_samples_split` / `_is_splittable_error` / `STATS["range_splits"]` | **the archiver cannot serve an arbitrarily large window**: past roughly 110 k samples `/samples` answers HTTP 500 (`L3-SBW4-PM311:Energy`: 2026-08-06 = 108 078 samples is served, 2026-08-05 = 116 223 and 2026-08-01 = 122 410 are not; each half of a failing day comes back in ~5 s). A response-size limit, not a timeout and not missing data. The ceiling belongs to the server and no client can know a count before the answer arrives, so nothing keys off a sample count — the 500 IS the signal, and the window is halved and re-asked (sequentially, so a pool of 8 is not multiplied) down to a 1 h floor / depth 3. Only 5xx and timeouts are split; a 4xx or bad JSON would fail identically eight times, and `CpvaBusyError` means our own pool was full, not the window. `fetch_values_ex` routes through it, so `get_day` (whole days) and `value_at_or_before` (multi-day look-back) inherit it. Before this, a Shot Finder search over one busy day returned an empty table. Pinned by `testing/test_day_split.py` |
| `CHANNEL_MAP` / `SBW4_CHANNEL` | the ONE name→channel map; the Slider derives its display names from it (`PV_DISPLAY_TO_COL`) instead of keeping a second copy that drifted (`pcm2` used to differ per tab). `sbw4` was renamed to `L3-SBW4-PM311:Energy` on 2026-08-14 — earlier dates live under the old `HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy`. `if_t.CPVA_SBW4_CHANNEL` is an alias of `SBW4_CHANNEL`, not a second literal |
| `fetch_channels_cached(pattern)` | the `"**"` listing is ~9700 names and every PV picker wants the same one — cached process-wide with single-flight; an empty/failed listing is never cached |
| `split_query` / `tokens_in_order` / `rank_pv_match` / `best_pv_match` | the ONE PV-search implementation, used by the Shot Finder rows and the Slider's PV picker. Tokens are AND-matched with implicit wildcards (`hapls sbw4` = `*hapls*sbw4*`), in-order hits rank above scrambled ones, and camera channels (`CAM_CHANNEL_RE`, ~40 % of the listing) sink to the bottom so they cannot fill the result list |
| `grid_step` / `on_grid` / `quantize` / `QUANTIZED_CHANNELS` | value-grid rule: the waveplate (`L3-PFWP6-MTR03-1:RawPos`) is only ever commanded to multiples of `WAVEPLATE_STEP` = 1000, so every value passes through `quantize`, and one snapped from off-grid (the motor caught mid-move) is reported `exact=False` → rendered `~350000`. `GRID_TOL_FRACTION` = 0.005 is the tolerance `CSS Logger/main.py` already uses on this PV |
| `CpvaBusyError` / `_POOL_WAIT_S` / `_WARM_MAX_WORKERS` | the local pool running out is not an archiver fault: it no longer arms `ERROR_BACKOFF_S`, the wait is sized against the longest another caller can hold a slot (`2 × FULL_DAY_TIMEOUT`, not the waiter's own timeout), the pool is 8 wide (the Slider's PV panel is the widest fan-out), and background warm-up may take at most half of it |
| `STATS` / `stats_line()` | request / 5xx / timeout / busy / max-pool-wait / dropped-sample / abandoned-record counters, appended to `image_tools_diag.log` by `is_t._diag_log` (which adds the PV panel's own `pvFetch` / `pvLastVal` / `pvWait` / `pvStall` fields) |
| `best_shot_ns(start, end)` | timestamp of the strongest shot in a window |
| `date_key_for_ns` / `prev_date_key` / `next_date_key` / `day_bounds_ns` / `today_key` | day keys |
| `get_day(channel, date_key)` → `DayResult` | whole-day sample cache with status + age; today's entry is refreshed with a short TTL and merged (`_merge_tail`), concurrent callers share one in-flight fetch (`_InFlight`). **The share is time-bounded** (`_INFLIGHT_MAX_WAIT_S`): a fetcher that vanishes after registering its record used to leave every later caller waiting on an event nobody would set — an unbounded wait on a worker thread, i.e. the Slider's PV panel frozen (single-flight flag never cleared) until the program was restarted. Past the bound the record is dropped (`STATS["takeovers"]`) and the waiter fetches for itself; `_finish_inflight` takes the record it is completing so a late fetcher cannot publish its stale answer into somebody else's newer one, and nothing between registering and publishing sits outside an exception handler. Pinned by `testing/test_pv_resilience.py` |
| `get_day(…, span_ns=(a, b))` / `_Entry.span` / `_covers` | **only the hours the caller will look at.** A Shot Finder search is 07:00–21:00, so the whole-day query fetched, parsed and threw away ten hours per channel per day — and the bigger response is likelier to trip the 110 k size limit and pay a split. The slice is stored with the samples, and a caller wanting MORE than an entry holds refetches instead of being served half a day as if it were all of it (the night would read as "nothing archived"). A waiter on somebody else's in-flight fetch checks its span for the same reason. No `span_ns` = the whole day, exactly as before. Pinned by `testing/test_cpva_span_cache.py` |
| `_drop_after` / `_has_inside` / `channel_aliases` / `STATS["samples_after_window"]` | **a carry-over value is not data for the window.** A window the archiver holds nothing for is answered with one sample — and that sample may be stamped AFTER the window: measured 23.09.2026, `L3-SBW4-PM311:Energy` asked for 01.09. answers with a single value stamped 22.09. 21:24, while the HAPLS-era name holds all 4 532 samples of that day. Counted as data it kept the day from ever being asked for under the other name, and the Shot Finder reported "no samples inside the chosen hours" for every day of a three-week search but today. Samples after the window are dropped everywhere they are read (`get_day`, its tail merge, `best_shot_ns`, `_value_before_day`, `if_t._cpva_fetch_samples`), and "this name holds nothing here" now means no sample INSIDE the window, which is what decides whether the alias is tried. The sample from BEFORE the window is kept — that one is the held-forward value. Pinned by `testing/test_cpva_carry_over.py` |
| `read_order` / `channel_for_day` / `channel_aliases` / `_READ_FIRST` | **SBW4 is looked for under the HAPLS-era name first, and under the L3 one only when that holds nothing for the window.** The operator's rule, 23.09.2026 — and what the archive says: the HAPLS name holds every day from 31.08. to 22.09. (4 000 – 110 000 samples each) while the L3 name answers most of them with one stray value. Reading the 24 days costs 24 requests instead of 47. The order is a COST rule only: whichever name answers, it is the same measurement, and the second name is asked whenever the first holds nothing — so a wrong guess costs one extra request, never a missing answer. The date of the rename (`SBW4_RENAME_DATE_KEY`) decides nothing any more; the names take turns with the laser configuration, not with the calendar. `CHANNEL_MAP["sbw4"]` stays the L3 name — that is the identity on labels and tooltips — and `DayResult.src_channel` reports what actually answered. Pinned by `testing/test_pv_alias_and_order.py` and `testing/test_cpva_carry_over.py` |
| `warm_days` / `peek_day` / `invalidate` | pre-warm, cache-only read, drop. `warm_days(span_for_day=…)` warms only the picked hours; `max_workers=` lifts the background cap (half the pool) for a warm-up the operator is waiting on — `FOREGROUND_WARM_WORKERS` = pool − 2, so the Slider's live panel always keeps two connections. `days_per_warm_wave(n_channels)` is how many days may be warmed at once: the cache holds `_DAY_CACHE_MAX` channel-days, so warming a month of three PVs in one go evicted its own beginning and every one of those days was fetched a SECOND time. `peek_day` honours the span too. |
| `nearest_sample` / `nearest_sample_ex` / `_match_score` | nearest sample to a timestamp, with a preference direction |
| `LookupResult` / `format_lookup` | tri-state result: real value (including a genuine 0) / `n/a` (no sample) / `ERR` (fetch failed, retryable), plus `no data yet` (`pending` — the moment asked about has not been published yet, see below). `head_ts_ns` carries the archiver's newest readable sample for that day. The display words are `PV_TEXT_*`: they name the STATE the operator is in (`no data yet`, ` (older shot)`), not what the program is doing (`wait`, ` (old)`) |
| `head_ts_ns(channel)` | the published head for today, cache-only (no network, safe on the GUI thread). Compare it with a FRAME's timestamp, never with the local clock |
| `value_at_or_before` / `_value_before_day` / `_last_at_or_before` / `invalidate_lookback` | step PVs (e.g. the waveplate `RawPos`) are archived only on change, so they are resolved by bisecting the day of the timestamp (+ previous days). The look-back cache is keyed by "last sample before the START of a day", a key fully determined by its query. |
| `lookup_near(channel, ts_ns)` | the entry point used by every tab: energy PVs match inside a ±window (the Slider passes **±0.3 s** — `_PV_WINDOW_NS`; ±30 s is only the function's default and matched a neighbouring shot for 43 % of frames), step PVs fall back to `value_at_or_before` |
| `is_step_channel` / `classify_step_channel` / `hold_fraction` / `_probe_days` | **which PVs hold their value between samples, decided from the archive instead of from a list.** `STEP_CHANNELS` was a hand-kept set of one, so every other setpoint readback read `no data yet` for ever — the GDD setting `L3-SPFE-AOD03-002:Order2_RB` had stood at 24300 since the morning and the panel showed nothing (2026-09-01). A channel that misses the window is now classified from its own samples: walk back through the days until there are `STEP_PROBE_MIN_SAMPLES` of them covering `STEP_PROBE_MIN_SPAN_S`, then ask where the TIME goes — a typical gap (p75) over `STEP_LONG_GAP_S` settles it, otherwise the gaps that are unusually long *for this channel* (≥ `STEP_LONG_GAP_RATIO` × its median) must cover `STEP_HOLD_FRACTION` of the span. Time-weighted on purpose: counting long gaps calls a setting nudged twenty times in ten minutes a detector, and a plain slow-gap rule calls a **25 s shot cadence** a setting — that cadence is real (`testing/bench_pv_wait.py` runs it) and holding an energy across it would hand out a neighbouring shot's number. A channel with nothing in the probe window is judged by the day of its LAST sample, so a detector that STOPPED weeks ago is not held forward while a setting untouched for weeks is. Verdicts expire after `STEP_VERDICT_TTL_S`, so a misreading heals itself. Pinned by `testing/test_step_channel_detect.py` |

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

### What this tab is for

**One camera, many days, side by side.** It is the *transpose* of the Image Slider: the
Slider tiles many cameras at ONE moment, this tiles many days of ONE camera. Neither of
the other tabs can do it — the Slider needs a single moment, and the Shot Finder produces
a table it previews one row at a time, so it never puts two days next to each other.

The tab used to be a fourth viewer with a search bolted on, which is what made it feel
redundant: its own single preview with no memory of what it had read (a re-read of the
share on every window resize), its own palette copy, three near-identical caption-bar
routines, and its own camera discovery beside a better one in the Slider. What only it
can do is *pick* frames — including with **no target at all** (highest-energy shot of the
hour, the `TotalPower` window with the highest mean, or purely file sizes and time gaps
when the archiver is unreachable). The Shot Finder cannot answer without a number: it
refuses to search without one (`sf_t.py`, "Tick at least one PV to search by"), because
its engine minimises a distance to a target and without a target there is nothing to
minimise.

### `_DayWall` — the comparison wall

Borrows the Slider's packer through `_get_slider_module()` rather than carrying a second
one: `compute_camera_layout` is already generic over a list of aspect ratios, so it is fed
one aspect per **day**, and `_entry_rect` keeps shared edges on the same pixel. Tiles are a
seamless partition of the canvas.

**ONE display setting drives EVERY tile — until a tile is MARKED.** With nothing marked
the whole wall renders on one setting, which is the point of the view: one colour means one
intensity in every day. Marking tiles switches to the Slider's rule — a control hits the
frames marked *when it was moved*, and nothing else (`_wall_target_paths`,
`_sync_wall_display`). That covers brightness, contrast, gamma, rotation, the drawn marks
and, since 07.09.2026, **the palette** (`apply_gradient`) — which used to be wall-wide
always on the grounds that a per-frame palette is a second legend on one picture. Picking
out the one dim day and giving it a colour scale is the same act as opening up its
brightness, so it is aimed the same way; what keeps it honest is that the tile SAYS so:
`(adjusted)` when it is off the shared brightness, `(own palette)` when only its colours
differ. Auto and the reference-day deviation stay wall-wide whatever is marked — both are
readings pooled over every frame, and a per-tile one would mean nothing.

**Why the comparison is honest, and the two ways it was not.** The archive does not store
camera counts: it stretches each frame's own power-of-two bracket up into the 16-bit
container, so a dim day and a bright one can BOTH sit near 51 000 in the file. Rendering
`stored / 65535` shows every day at almost the same brightness. Measured on three frames of
400 / 1600 / 3200 counts: stored peaks 51299 / 51224 / 51212, naive codes **199 / 199 /
199** — useless — against the wall's **24 / 99 / 199**, which is the true counts ratio.
Two bugs of exactly that shape were found and fixed here, both by converting to one common
range *before* combining:

* `_shared_auto_pair` pooled the **stored** values of every day to take one percentile
  window, mixing units; the pair that came out (contrast 127, the maximum) saturated every
  brighter day to white — 94 / 255 / 255.
* the deviation from a reference day subtracted the **stored** arrays, so |day3 − day1| was
  code 0, a black tile, while the real difference was 2800 counts (code 173).

Auto never runs per tile — `img_scale.stretch_u8` says of itself "NOT comparable between
frames". It is resolved to one shared (contrast, offset) pair over the whole wall and
applied through the ordinary absolute path, so Auto still brightens a dim camera without
measuring any day against itself. The pair is resolved *before* the tile cache key is
built, or it would invalidate the cache on the next tile and re-render on every repaint.

**A CLICK MARKS, IT DOES NOT OPEN, AND EVERY CLICK ADDS** (14.09.2026). A plain left click
adds that frame to what is marked; clicking a marked one releases that one; **Shift+click
marks the whole CAMERA** and adds it (`_select_camera` — every frame it has on this wall,
or takes the whole camera back out when all of it is in), because the operator asks for a
camera and on the Day-by-day wall a camera owns a whole row, so that would otherwise be a
click per day. Ctrl+click is kept as the same act, since it is in every tooltip and in
everyone's fingers. **Nothing clears the marking behind the operator's back any more**: it
used to `sel.clear()` before every plain click, so marking a second picture silently
dropped the first and building a set still needed Ctrl. The way back to "nothing marked =
the whole wall" is **Esc** (`ImageFinderWidget.keyPressEvent`), **right click → Unmark
every picture**, or a click on empty canvas — a wall whose tiles fill the pane edge to edge
leaves no empty canvas, which is why there are three. The panel's camera list follows the
same rule (`_on_sel_table_clicked` → `mark_camera`). The gestures are written down in the
`_sel_wall_lbl` tooltip, which is what the operator is already looking at when wondering
how to change what is marked. It used to select AND open the close-up
window in the same gesture, so every attempt to aim a control at one day threw a window
over the comparison being made, and the only way to mark a tile without that was Ctrl —
which nobody guesses. Looking closely is its own act now: **double click**, **right click →
🔍 View (close-up)**, or the **Detailed view** tab. Right click is also where the rest of
the per-frame actions live (search again, pick from folder, set/clear the reference day,
clear this frame's marks). Pinned by `testing/test_wall_click_select.py`.

**One row per camera (`set_layout_mode("rows")`, the Day-by-day tab).** **A row is a
CAMERA and a column is `(day, pick)`** — `_cell_row_key` / `_cell_col_key`. It was the
transpose until 14.09.2026 (a row a day, a column a camera), which put every camera of one
day on a single horizontal line; the operator's rule is one camera per line, the days
running across it, so five cameras over five days read as a five-by-five grid. **Four
columns fill the pane and the rest is scrolled to sideways** (`row_col_width`, the same
`_MAX_COLS` = 4 the grid mode obeys); dividing the pane by *every* column, which is what it
did, turned twelve days into twelve slivers. Three rows fit the pane (`_ROWS_IN_VIEW`), the
vertical bar steps a whole camera at a time (`_WallScroll.wheelEvent`), and the layout is
measured against the **pane** (`_avail`) and never against the widget — once the wall is
wider than its pane Qt grows the widget to its own minimum, and a pass that measured that
would grow again for ever. The grey banner names the camera and how many days its line
reaches across (`_row_head_text`); it spans the full scrolled width while its WORDS ride
with the viewport, or scrolling right leaves the row naming nothing. The tile's own caption
then names the **day**, not the camera (`_caption`, rows mode only) — the banner already
said which camera it is. Column order is `_assign_col_slots`: within one camera and one day
the tiles are numbered **by the time of the frame that was found**, so a picked moment and
a marked region cannot collide on one rectangle; it groups on `(cam, day)` outright rather
than through `_cell_row_key`, because which of the two is the row is that function's
business. Pinned by `testing/test_wall_rows_wide.py` and `test_wall_regions.py`, and
`testing/render_day_wall.py` renders five cameras × five days to be looked at.

`composite_image` writes the whole wall as one picture with the days captioned, which
neither other tab can do (both save frames singly). Its scale is **computed**, not a fixed
2×: `export_scale` asks what puts the SMALLEST tile's frame on screen at its own pixel
width, floored at 2× and capped at `_EXPORT_MAX_PX` (80 Mpx) — with two dozen tiles on a
row the old 2× threw four fifths of a 1280 px frame away and the file could not be read.
The canvas is `max(width, minimumWidth)` × `max(height, minimumHeight)`, so a wall wider or
taller than its pane, and a tab that was never shown, both come out whole. `Save view` logs
the pixel size it produced.

Pinned by `testing/test_day_wall.py`.

### The view tabs, and the Detailed view

`_build_wall_tabs` rebuilds the bar on every search: one wall per camera, then **Day by
day** (one row per camera, the days across), then **Detailed view** last. The one-wall shortcut — "one moment,
every camera" gets a single tab, because a tab holding one tile is no comparison — now also
requires that no cell carries a region, or one click plus four drags collapsed into one tab
and hid four fifths of the search.

**Detailed view** (07.09.2026) is one frame, big, with `◀ 3 / 8 ▶` under it and the ← →
keys while it is the tab on screen. It shows **the marked frames, or every frame on the
wall when nothing is marked** — the same `_wall_target_paths` rule the display controls
follow, in the wall's own order, and the frame being looked at is kept when it survives a
change of selection (`_sync_detail_tab`). The page is built by **`_make_frame_page`, called
twice**: once for this tab and once for the close-up window a right click → View opens, so
the two are one look and one renderer and share one frame list and one index
(`_preview_show` writes both, `_frame_views`). Neither is rendered while it is hidden — a
share read for a label nobody can see is 130–160 ms thrown away (`_frame_page_visible`).
The page is dark, and it says so in a **stylesheet** as well as a palette: `main.py` paints
`QWidget { background:#f3f3f3 }` over the whole application and an application stylesheet
beats a widget's palette, so the page came out pale grey with its light-on-dark counter and
scale note invisible (`QWidget#framePage`). It is deliberately **not** in `_wall_pages`, so
`self._wall`, `_wall_target_paths` and the Save-view scope keep meaning a wall; and
`_build_wall_tabs` lifts it out of the bar before deleting the wall pages and puts it back
after (`_readd_detail_tab`), or the rebuild would delete the page the operator is looking
at. Pinned by `testing/test_detail_view.py`; drawn by `testing/render_detail_view.py`.

### Moments and regions are searched TOGETHER

Both, always (07.09.2026). `_start_pv_search` used to look at the moments first and
`return`, so `cfg["regions"]` was never read again: four clicks plus four drags searched
four moments and dropped the four spans in silence, on a button that had already announced
"Search these 4 moments" and a status line that called the regions ignored.

A region is only a moment worked out from the peak of the primary PV inside it, and both
ends at the same resolver (`_resolve_moment_one`) — so there is ONE pipeline:
`_start_pick_search` builds a list of **picks** (`{ts, kind, index, region}`),
`_resolve_region_picks_async` turns each marked region into an instant with the existing
`_region_targets` (one archiver read per day and *nothing* per camera, behind a small
progress box with a Cancel), and everything goes through `_load_moments`, which asks for
**cameras × every pick**. `_pick_info` carries what each instant IS, so a tile can name its
own pick — and it names it with a PLAIN number, whichever kind it was. The caption used to
put an `r` in front of a region (`r2)`) because moment 2 and region 2 were two different
picks; one shared counter (see below) means there is only ever one 2, so `_caption`,
`_rebuild_baseline_combo` and the saved sheet's caption strip all print the bare number.
`_tile_tip` reads `pick_kind` to say "region 2 picked" rather than calling everything a
moment. `_row_head_text` counts a tile as a moment only when it carries no `region`: a
region's cell holds the same number in BOTH `pick` and `region["index"]`, so counting
`pick` over every tile announced a day of four regions as "4 moments + 4 regions".
`_assign_col_slots` orders a camera's tiles by the time of the frame, never by the pick
number, so a deleted pick cannot reorder a row. `fill_wall` keeps a camera that found
nothing, so the count still adds up. The button and the row banner name both halves, and
`get_config`'s `days` is the union of the region days and the moment days. A marked region
still needs a primary PV and is refused out loud without one — never dropped. Pinned by
`testing/test_moments_and_regions.py`.

### The picks are a list, and the list can be edited

`_build_picks` / `_pick_rows` / `_refresh_pick_table` / `_renumber_picks` (11.09.2026).
Under the graph is ONE table, thirteen columns:
`# ■ What Day Time Length | PV n Mean ±Std Min Max | ✕`. The left half holds both kinds of
pick in clock order, each row with its own ✕; the right half is what used to be a second
page called **Marked ranges** (`_build_stats` / `_refresh_stats`, both gone). A region
takes one row per plotted PV with its six description cells and its ✕ merged down the
block (`setSpan`, the ✕ inside a centred host widget or it sits at the top of a five-row
block); a moment is one row and its number columns read `—`. The tab bar went with the
second page — with one page left there was nothing to switch between — and so did the
narrow list of regions in the 275 px sidebar, which said the same thing in a quarter of
the width.

`_refresh_pick_table` calls **`clearSpans()` AND `clearContents()`** before refilling. A
merged block never writes the cells under its own span, so whatever the previous fill left
in them survived: mark a region after picking three moments and rows 2 and 3 still held
moments 2 and 3, hidden under the merge and read back by everything that asked the table
what was in it.

**One counter, and a number stays with its pick.** `_next_pick_no` takes the maximum over
`self._moment_no` (timestamp → number) AND every `r["no"]`, so a pick gets the next free
number whichever gesture made it and no two picks ever share one. Moments used to be
numbered globally while regions restarted at 1 on each day — which is the whole reason the
wall had to letter one of them `r2)`. Every reader takes the number from there: the graph
annotation (`_paint_pick_number`, shared by `_paint_moment_cursor` and
`_paint_region_spans` — the regions had no badge at all before, only a colour), the table,
the day list, and `get_config` (`regions[day][i]["index"]`, and `moment_nos` alongside
`moments_ns`, which `_start_pick_search` puts on each pick). Delete pick 3 and the rest
read 1, 2, 4 everywhere. **Renumber** is the only thing that closes the gaps, 1…n straight
down `_pick_rows`. A number that renamed itself while the list was being tidied would not
be the number being talked about.

`_picks_changed(with_stats=…)` is the single hook that refreshes the table and the Search
button; `_regions_changed` is the region-side entry point (all that is left of
`_rebuild_regions_ui`). The delete paths reached none of the old per-place refreshers, so
the button went on offering to search regions that had already been taken off. Deleting
also pushes an undo step, so Ctrl+Z puts a pick — and its number — back. There is no Undo
or Clear BUTTON any more, and no Cancel: the ✕ per row does the one-at-a-time case, Ctrl+Z
the last-gesture case, and the window's own ✕/Esc closes it.

**The statistics are cached** in `self._stat_cache`, keyed `(region id, channel)`. Reading
every archived sample of every plotted PV over every marked range is what made each extra
click on the graph slower than the last, so a change that touches no range —
`_set_moment_from_x` — passes `with_stats=False` and redraws from the cache. It is emptied
wherever the series themselves change (`_reload_series` on a new PV set, `_on_series_loaded`,
a row re-pointed at another channel). Pinned by `testing/test_pv_picks_table.py` and
`testing/test_pv_stats_and_formula.py`; drawn by `testing/render_pv_search.py`.

**No `color:` on `QTableWidget::item`**: a stylesheet rule on the item beats the colour the
item itself is given, and it was quietly repainting every ink that MEANS something — a
region's own colour, a PV's curve colour, the amber of a held value — plain dark. The
default ink comes from the widget rule instead.

**`QPushButton:disabled` is set explicitly** in `main.py`'s application stylesheet, for the
same reason `_CHECKBOX_STYLE` sets its own: `QWidget { color:#111 }` applies to a DISABLED
widget too — a stylesheet overrides the palette, disabled state included — so a greyed-out
button came out in full black text with nothing but a slightly paler border (measured on
Remove ref: 26/255 of difference, all of it in the border). It looked exactly like a live
button and clicking it simply did nothing. A button that paints itself (Stop All, Delete
mode) sets its own stylesheet and is unaffected. Drawn by `testing/render_ref_buttons.py`,
which shoots both states.

### A click is a moment, ANY drag is a range

`_PV_CLICK_SLOP_PX = 2` (11.09.2026, was 5). `_on_release` and `_on_span` both decide
through `_is_drag`, which measures the travel in SCREEN PIXELS — a few seconds is an
enormous drag on a zoomed-in axis and no movement at all across a week. At five pixels a
deliberate band on a whole-day axis (four minutes, plainly visible) was thrown away and
turned into a single moment instead. Two pixels is a tremor allowance for a click, nothing
more. The order matters and cannot be worked around: the canvas `button_release_event`
handler is connected in `_build_ui` while the `SpanSelector`s are re-installed after every
redraw, so `_on_release` runs BEFORE `_on_span` — a "the span already took it" flag is
impossible, and the shared pixel test is the only decision point.

### Handoff to the Slider

`open_in_slider` → `_push_cells_to_slider` hands over the frames actually picked, through
the Slider's own public `receive_external_folder(..., energy_map, discrete=True)` — the
same route the Shot Finder uses, which clears a multi-cam grid, an armed live mode, a
subtraction reference and focus mode first. It used to open the FIRST checked folder WHOLE
and log that it was ignoring the rest: the tab did all the work of picking a frame per day
and threw the selection away at the door. Each caption is carried next to its own file, not
looked up by index, so a day without a picture cannot shift every later caption onto the
wrong frame. The temp folder **keeps the camera folder name**, because
`img_scale.camera_from_path` reads the camera off the PARENT folder — a flat temp folder
would strip it and the Slider would fall back to the plain 65535 range, showing the same
frame ~16× darker than the wall it was compared on.

The button for it is `_btn_send_slider` in **Actions**, beside `_btn_send_workshop` on
one row (`_SEND_BTN_QSS`, the same small-button block in all three tabs). `open_in_slider`
had no caller at all before that row existed.

### Handoff IN: `open_moments` ("Send to Image Finder")

The public way in, called by `is_t.Viewer._send_to_image_finder` and
`sf_t.ShotFinderWidget._on_send_to_finder`. The mirror of `_send_moment_to_slider`: what
crosses over is a **moment**, never a file, so an instant scrubbed to between two frames
and a Shot Finder day whose picture was never loaded are both valid sends.

`open_moments(moments_ns, cam_names)`:
1. groups the moments by their Prague day and writes one `daypicker.PickSeg` per day
   covering that day's own moments ± `PUSHED_MOMENT_PAD_MIN` (15, the same pad
   `is_t.open_moment` uses), then sets `_selected_days` / `_user_has_selected_day`;
2. parks the request in `_pushed_moments` and calls `load_folders()`;
3. `_on_load_done` → `_apply_pushed_moments` ticks the sent cameras and calls
   `_load_moments`, which is where the wall is built as always.

The two-step is not avoidable: `_cams` only exists after the day scan, so a camera on a
day this tab has never looked at cannot be ticked before that. `_on_load_not_found` /
`_on_load_error` call `_drop_pushed_moments`, or a scan that found nothing would leave a
request armed to fire on top of whatever is loaded next. Cameras that are not on those
days leave the tab's own pick alone (logged, never silently emptied); with nothing ticked
at all `_load_moments` holds the request in `_pending_pv_cfg` and opens the camera picker,
which is the same "either half first" rule PV Search works by. Tested by
`testing/test_send_to_finder.py`.

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
| `_cpva_fetch_samples` / `_cpva_active_windows_ns` | archiver access + merged "beam active" windows from `:TotalPower`. The best-shot lookup is `cpva.best_shot_ns` — shared, and it splits an oversize window instead of losing the whole answer to one HTTP 500. |
| `_cam_totalpower_channel(cam)` | folder name → `:TotalPower` channel |
| `_read_img_max_value(path)` | the frame's PEAK in raw counts (`MaxValue` tEXt, looked up by NAME via `img_scale.read_max_value`). Used only by the empty-frame test — the display scale does not need it |
| `_image_is_nonempty(path)` | frame peak first, pixel-contrast fallback |
| `_render_u8(arr, auto, full_scale, gamma, contrast, offset, out)` | the ONE render step for this tab — delegates to `img_scale.render_u8`: absolute scale bent by gamma, then the Contrast / Brightness pair, in full precision. `auto` is the `AUTO_*` mask and only fills in each control's value from the frame. `full_scale` comes from the decoded image's MODE, never from `arr.max()`; `out` reports what was applied |
| `_scale_note(info, arr, auto, full_scale)` | the line under the preview: peak counts, % of full scale, bit depth, which mapping. Takes an already-open image's `.info` — never re-opens the file (130–160 ms on the share) |
| `is_valid_image_file` / `extract_display_label` / `extract_folder_number` / `extract_ns_from_stem` / `convert_timestamp` / `build_new_name` | filename helpers |
| `_energy_csv_path` / `_load_energy_csv` | daily CSV → `list[_EnergyRow]` |
| `_energy_api_for_day(dt, cols, …)` | CPVA per column (thread pool) + CSV fallback |
| `_find_energy_match` / `_find_closest_per_col_value` / `_build_per_col_from_rows` | timestamp matching |
| `_format_energy_value` / `_format_energy_diff_s` | units (J, mJ for `Back_Ref` / `PAP1`, the waveplate's count grid) and the ± time offset. Keyed by REGISTRY NAME now, and it no longer multiplies SBW4 by 0.749: this tab used to print the compressed energy under the name of the channel that reads the uncompressed one, so its SBW4 matched no other tab and no archiver query. The only factor left is the registry's own, for an entry that exists to BE a conversion (`Compressed SBW4`) |
| `_annotate_image_with_energy` / `_write_annotated_with_text` / `_write_annotated_from_pil` | the white PV bar under a frame |
| `_make_lut` / `_make_binary_lut` / `_make_stepped_lut` | palettes |
| `_style_calendar` / `_make_multiselect_calendar` / `_make_mpl_toolbar` / `_hsep` / `_group_label` / `_section_label` | UI helpers |

### Classes
| Class | Purpose |
|-------|---------|
| `_WeekendDelegate` / `_CalBorderDelegate` / `_NoScrollCalendar` / `_MultiSelectDelegate` / `_NoScrollComboBox` | house calendar style (Monday-first, grey header, red weekends), wheel blocking, multi-select painting. `_MultiSelectDelegate` is the Image Slider's version: it reads each cell's real date from the model (`UserRole`) with the MinimumDayOffset correction behind it, and paints a blue outline on the focus day — the primary day of a multi-day selection |
| `_CameraPickDialog` | the Cameras picker: token search over number + label + folder name, click to add or remove, Select all / Clear, the picked list with a ✕ per row, and a **presets** panel over the SAME `%APPDATA%/ELI_ImageTools/cam_presets.json` the Slider's `CameraPickerDialog` writes — so a saved set is offered in both tabs. Loading a preset keeps only the cameras the picked days actually recorded and says how many were missing; saving over a Slider preset preserves its stored arrangement only while the camera set is unchanged (otherwise those tiles belong to nothing and it reverts to `auto`) |
| `_EnergyRow` | one CSV/API row (`ts_dt`, `values`) |
| `_ThumbView` | thumbnail + circle/square/cross overlay; emits `overlay_changing` / `overlay_edited`, carries `key` / `native_size` |
| `_EnergyLoadSignals` / `_EnergyLoadTask` / `EnergyColumnDialog` | energy column picker |
| `_LoadSignals` / `_CollectSignals` / `_CompareSignals` / `_AutoHourSignals` / `_LogSignals` / `_PreviewSignals` / `_TryAgainSignals` | thread-safe signal carriers |
| **`ImageFinderWidget`** | the tab itself |
| `_MultiDaySetupDialog` | multi-day range / camera / hour picker (weekday default Mon–Fri) |
| `_PVBrowseDialog` / `PVRegionSearchDialog` | browse archiver channels, and search a day for time regions where PVs satisfy given conditions (`_PV_REGION_COLORS` paints the regions). **The graph draws archived values, not normalised ones**: `_redraw` groups the checked PVs by UNIT (`_unit_key_for`, off the shared PV registry) and gives each unit its own y axis — joules on one, millimetres on another, a unitless PV on its own — where it used to divide every curve by that PV's own maximum, which made a reading of 0.9 mean nothing but "near its own biggest" and left two PVs impossible to compare. Curves are drawn `steps-post` (the archive says a value holds until the next sample), and a channel the archiver writes only when it CHANGES (`cpva.classify_step_channel`, decided from the day already fetched where that settles it) is carried across the day from the value it was already sitting at (`_hold_xy` + `_seeds`, `cpva.value_at_or_before`) — a setting that last moved last week has no sample today, and drawing that as "no data" is a statement about the PV that is simply false. The edge points are synthetic, so they get no marker; only real samples do, and only while there are few enough for a dot to mean anything. Colour and line shape come from the PV's own place in the list (`_style_for`), never from the draw order, so unchecking one PV cannot recolour the rest — and past the end of the ten colours the line changes shape rather than a colour being used twice |
| `MultiDayPreviewWindow` | result grid with palette / overlay / save / try-again |

### ImageFinderWidget — behaviour worth knowing

- **PVs** — the same picker and the same table as the Image Slider, over the same
  registry: `_pick_energy_columns` opens `is_t.PvConfigDialog` and the sidebar list is
  `is_t.PvValueTable` (both borrowed through the `_get_slider_module()` loader this tab
  already used for the palettes). What this replaced was `EnergyColumnDialog`, a fixed
  list of the twelve Salvation CSV columns as tick boxes — no search, no PV of your own,
  and a second label table beside the Slider's.
  - `_energy_selected_cols` now holds **registry names** ("SBW4", "Compressed SBW4", an
    added channel's own name, a formula's name), not CSV column names. `_pv_channel_for`
    resolves one to its channel and — this is the change that makes an arbitrary PV work
    at all — falls back to the NAME ITSELF; it used to be `CPVA_CHANNEL_MAP.get(col)`
    alone, so anything outside the eight presets resolved to `None` and fell straight
    through to a CSV that had never heard of it. `_pv_csv_col` is the reverse for the
    daily CSV, and it is also what keeps the CSV-only columns (`CampOn`, `E2..E5 Open`)
    reachable: type the column name into the picker and the fallback finds it.
  - `_pv_visible_cols` (the eye) governs every PRINTED line — the info panel, the bar
    under the preview, a burned-in save — while `_pv_fetch_cols` is what is READ: every
    picked channel plus `pv_source_names` of every picked formula, so a formula built on
    a PV that is not itself shown does not read `n/a`.
  - Formulas are evaluated in `_pv_values_for_ns` through `pv_eval_derived`, the same
    evaluator the Slider uses, from the numbers just resolved for that frame.
  - **No hidden conversion**: `_format_energy_value` used to multiply SBW4 by 0.749, so
    this tab printed the compressed energy under the name of the channel that reads the
    uncompressed one. Gone; `Compressed SBW4` is a named registry entry instead.
  - The selection is persisted in `%APPDATA%/ELI_ImageTools/finder_ui_state.json`
    (`pv_selected` + `pv_hidden`) and restored at the end of `_build_ui`; the PVs
    themselves come from the shared registry. Changing the selection clears the day
    caches, or a PV added a moment ago would read `n/a` until a day happened to be
    re-fetched for some other reason.
- **Source group** (section key stays `time`, so saved fold state survives the rename)
  — the Slider's Source layout: **Time window** and **PV Search** side by
  side, **Cameras** under them (they were the other way round until 07.09.2026 — the
  two that NAME THE MOMENT belong on one line, and the camera picker is the wide button
  under them), then one line saying what is picked (day or day
  count, hour, which clock) with the scan dot beside it, and **Load data**
  (`self._btn_view` → `view_primary_files`) closing the group: the read belongs to the
  pickers above it, not to the things you do with the result. **Actions** is the next
  group down. The calendar, the hour combo,
  the Lab-time switch and the weekday gate are built into `self._time_pane`, a pane with
  no parent that `_open_time_window` drops into a modeless dialog on first use — every
  widget keeps its name (`self._cal`, `self._hour_cb`, `self._lab_time_cb`,
  `self._wd_checks`), so none of the day/hour logic had to move. The dialog has only a
  Close button because a click already takes effect; `self._status_dot` stays in the
  PANEL, since "is the camera list for these days ready" must be readable with the
  calendar shut. `_sync_time_summary` writes the line, called from
  `_apply_day_selection`, `_log_selected_datetime_preview` and the auto-hour default.
- **Cameras** — a **Cameras** button (`_CameraPickDialog`) on its own row under
  **Time window** / **PV Search** — the same "when + which cameras" pair the Slider's
  Source group
  asks — and the list of picked cameras under the Workshop button in Actions. No group,
  no banner, no count line and (since the presets landed) no count on the button either:
  `0/92` named a total nobody can act on, because which cameras a day holds is not
  knowable from the panel. `self._cams` is the single source of
  truth: one dict per camera found by the day scan (`path`, `name`, `num`, `label`,
  `hz33`, `qty`, `checked`), read by `_picked_cams` / `_snapshot_collect_jobs` /
  `_checked_cameras` / `_capture_selection_state` (which is what carries the selection
  across a re-scan). What this replaced was a 5-column table (`[ ]`, `Cam #`,
  `# of imgs`, `3.3+Hz?`, `Label`) filling a whole column of the tab for a list that
  only matters while choosing — with the sorting, searching, master-checkbox and
  row-reordering code that a table needs and a dialog does not. Two of its columns are
  gone from the UI but not from the scan: `hz33` is still computed from `CAM_33HZ`, and
  `qty` (frames per camera, always 1) is still honoured by the collect path — so either
  can come back without re-deriving it. The picked list previews a camera on click and
  unpicks it on double-click. The log box is a short 84 px box at the FOOT OF THE PANEL
  (`root`, under the scroll area, not inside it, so it cannot be scrolled away): it used
  to span the whole tab under the pictures, where 100 px of window height went to a
  running commentary nobody reads while looking at a frame. It wraps rather than
  scrolling sideways, which is what the panel width costs.
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
- **Intensity scale:** `Con:` / `Bri:` / `Gam:` rows under the Gradient combo, each with an `Auto` box (default all off =
  absolute), a `Gamma` slider + `Auto` + `↺` under it (`_gamma_arg`,
  `_sync_gamma_enabled`), and the `_preview_scale_lbl` readout under the preview (peak
  counts, % of full scale, bit depth, mapping, gamma). Every render path — preview thread,
  `_apply_gradient_to_image`, Save As, `_send_to_workshop`, `_ThumbView._load_raw` —
  goes through `_render_u8`. The worker paths take `auto` and `gamma` as arguments,
  snapshotted with `grad_name` on the main thread; `_load_raw` deliberately caches the
  ABSOLUTE render with no gamma, because that dialog applies its own `_auto_bright` later
  and the cache is shared by every setting.
- **PV Search's Search button is BLUE** (`#2d7dff` on white, `if_t.py` `_build_ui`) — the
  same primary-action blue as Shot Finder's `🔍 Load data` (`sf_t.py:1715`). It was
  `#e8ebef` grey, which in a panel of grey captions and grey lists did not read as the
  one action of the window. `_sync_search_button` only ever calls `setText`, so the
  colour survives all three captions ("Search", "Search N selections", "Search by
  condition").

---

## sf_t.py — Shot Finder

### Constants
| Constant | Value / meaning |
|----------|-----------------|
| `IMAGES_ROOT` / `ENERGY_CSV_ROOT` | one archive path, one CSV path (`_images_root_for_year` picks the year folder). The Lab / Office pair they replace named the SAME share for images (only the slash style differed), the archiver answers the same from either place and the CSV fallback is dormant, so the switch could only ever be set wrong |
| `EXTRA_COL_MATCH_TOL_S` = 30 | tolerance for the extra (non-search) columns |
| `IMG_MATCH_TOL_NS` = 30 s | how far a frame may sit from the matched shot |
| `CPVA_HTTP_TIMEOUT` = 15 | per-channel request timeout |
| `PV_COLUMNS` / `MJ_COLUMNS` (`Back_Ref`, `pap1`) | UI labels, mJ display |
| `SBW4_WARNING_THRESHOLD_J` = 0.5 | default tolerance |
| *(no conversion factor)* | SBW4 used to be multiplied by 0.749 (the L3 compressor transmission) everywhere this tab printed a value, so "SBW4" here meant a different number than the channel called SBW4 does elsewhere. A picked PV reports the archiver reading and nothing else — the only conversions left are the mJ display and the waveplate grid snap. Anything derived from a PV belongs in the Slider's picker (named entries such as "Compressed SBW4", and formulas) |
| `_CAM_CHANNEL_RE` | camera-channel name pattern |

### Module-level helpers
`_load_csv_for_day`, `_load_api_for_day` (per-column API with CSV fallback; returns
`(merged, per_col, col_meta)` and does **not** silently fall back to CSV on an API
*error*, which used to mix sources), `_find_closest_col_value`, `_lookup_col_value`
(tri-state via `cpva.lookup_near`), `_format_value_state` / `_format_value` /
`_format_diff`, `_quantize_col` / `_quantize_col_ex` (the value-grid snap — applied by
the tolerance filter, the best-match search, the ranking among in-tolerance rows AND the
displayed Δ, so all four agree; the filter alone used to skip it and a day could read
"✓ match, Δ 0" while the shot list behind it said "no shots within tolerance". A reading
snapped from OFF the grid — the waveplate motor caught mid-travel — is shown as `~350000`
so it cannot pass for a settled position), `_find_best_match`,
`_folder_hour_from_prague`, `_day_image_folder`
(day archived at all? — distinct from `_find_hour_folder` returning None, or a day with
pictures from 08:00 to 18:00 gets told it has none), `_find_hour_folder`,
`_find_image_for_ts`, `_find_image_in_day` (the ONE resolver: the shot's own hour plus
its neighbours, `hour_cache` keyed by hour), **`DayScanCache`** (with `_hour_is_closed`,
`_ts_ns_target` and `_ts_from_stem`) — the optional `scan_cache=` the resolver takes, and
the fix for the minute-long moment walls (One Moment's, before the merge): an hour folder is read ONCE into a
name→path map that answers every camera at once, a camera folder ONCE into sorted frame
timestamps that `bisect` picks the nearest from, and a closed hour's answer is kept for
good while an open one expires after 20 s. Left out, the resolver behaves exactly as it
did, which is why the Shot Finder's own calls did not have to change. `_image_problem`
(a file that exists but
cannot be looked at — 0 bytes, all-zero frame, undecodable — is "no image", the same
disappointment as no file), `_make_lut_sf` and friends, plus `_render_u8` /
`_full_scale_for_mode` — the tab's single render step (absolute, or the explicit
the `Con:` Auto box), sharing `img_scale` with the Finder and the Slider. The old
`_read_img_max_value` is gone: its only callers were the render paths, and they do not
need the metadata at all. `_ns_to_prague` turns archive nanoseconds into the lab wall
clock as a **naive** datetime, like every sample row's own `_dt` — matplotlib plots a
tz-aware datetime in ITS timezone, and the day graph came out two hours out.
`_TABLE_QSS` is the house table look, and `_get_finder_module()` borrows `if_t`'s
`_make_mpl_toolbar` the same way `_get_slider_module()` borrows the Slider's helpers.

### ShotFinderWidget
- **Search criteria** — one row per picked PV (`_rebuild_pv_rows`): ticked = filter
  (target ± tolerance), unticked = show only. PVs are added from a searchable
  dropdown over the archiver channel list (`_fetch_channel_list`,
  `_populate_pv_dropdown`; `_rank_pv_match` / `_tokens_in_order` / `_split_query` are
  aliases of the shared `cpva.*` implementation — typed words are AND-matched, in
  order first), or removed with the per-row ✕.
- **Custom PVs** — the same three things the Slider's picker offers, minus the formulas:
  - **add by name**: a single bare word gets a `➕ add "…" as an archiver channel name`
    row in the dropdown (and Enter does the same). That is the only way in when the
    channel list is down or does not carry the PV. `_register_col` /
    `_preset_for_channel` map a name that a preset already reads back onto the preset,
    so one channel can never occupy two rows under two units.
  - **rename / copy channel name / remove**: right-click a PV row
    (`_on_pv_row_menu`). A name (`_custom_labels`) is display only — the col stays the
    identity, so a rename cannot repoint a column at other data.
  - **persistence**: the list and the names are saved to
    `%APPDATA%/ELI_ImageTools/shotfinder_ui_state.json` (`_load_pv_state` in `__init__`,
    `_save_pv_state` on add / remove / rename, on search and on tab hide). Only
    `col` / `target` / `tol` / `filter` are read back, so a `scale` left in a file
    written by the short-lived scale-factor version cannot revive as a hidden factor.
    SBW4 is the FIRST-RUN default only — the end of `_build_ui` falls back to it instead
    of assigning it, which used to throw the restored list away.
- **Source group** — **Time window** and **Cameras** side by side (one row, as in the
  Image Slider), the picked days under them, then **Load data · Load images** with the
  progress bar. The Lab / Office combo that used to sit here is gone (see
  `IMAGES_ROOT`). The action lives with what it acts on: the days, the hours and the
  cameras are all right above it. Neither button carries a count — `picked/available`
  named a total nobody can act on, and it made the label longer than the button
  beside it.
- **Two load buttons** (`_btn_search` / `_btn_load_images`, both →
  `_start_search(want_images)`) — the job has two halves at very different prices.
  **Load data** (left, pale with dark ink) reads the archiver and matches the shots;
  it never touches the image share, and its rows carry `status="data"`: green, with
  "no image loaded" in the Status cell, a grey `—` for the folder, and the preview
  pane saying what to press. **Load images** (right, primary blue) does the same and
  then walks the share for the frame of every day × camera.
  - **The second one lives off the first.** `_run_key()` names what a set of numbers
    IS — the day windows, the PVs, the bands — and `_remember_day_payload` keeps one
    payload per day (the same objects the `_DayResult` holds, so no extra memory).
    Load images over a matching key reads NOTHING from the archiver. The cameras are
    deliberately not in the key: the numbers do not depend on which camera the
    picture comes from, so re-picking cameras keeps the fast path. Retyping a band
    does not — those are different numbers. Pinned by
    `testing/test_sf_load_split.py`.
  - **The rows stay put.** A Load images over a matching key passes `keep_rows=True`
    to `_rebuild_result_tabs`: the table of every camera that already has rows is
    carried over instead of deleted, and `_row_slot` lands each arriving day in the
    row it already owns (one row per day per camera, always). Emptying the table and
    letting it crawl back day by day is what made a reused run look exactly like a
    fresh one — the numbers were being reused and the operator could not tell.
    A camera picked since the data run gets a new, empty tab beside the kept ones.
  - **The `Image` column** (`RES_COL_IMG`) says what has become of that row's frame:
    a green `✓` it is on the share, a red `✕` it was looked for and is not there, a
    grey `—` nobody has looked yet. It sits **second, right after Date**, and not
    beside the Folder cell where it belongs by subject: Status is as wide as its
    longest sentence and Folder holds a whole UNC path, so anything behind them is
    off the right edge of the pane and reachable only by scrolling. The ink is the
    mark's own, never the row's status colour — a green row whose picture is missing
    still shows a red cross. Every column is named (`RES_COL_*`, `RES_HEADERS`);
    "column 6" used to stand for the Folder cell in four separate places.
  - **The frames are looked for side by side** (`FRAME_UNIT_WORKERS = 8`,
    `_frame_unit`, `_day_folders`). The archiver half has to keep its day order — a
    warm wave must be in the cache before its days are worked through — but the
    frame half has no such order: every (day × camera) unit is its own folder walk,
    spending its time waiting on the share. The day loop now submits and moves on,
    so one day's share walk overlaps the next day's archiver read and the run costs
    about its slowest few units instead of the sum of all of them. `_day_folders`
    memoises the day/hour folder per day (up to five probes) so the cameras of a day
    do not each repeat it. The pool is drained before `done` is emitted: the bar, the
    count and the export buttons all read the table.
  - **A listed frame is not decoded.** `_image_problem(path)` does the cheap tests
    only (`stat`: missing, empty, unreadable); the all-zero test is behind
    `deep=True` and the search does not pay it — it is several megabytes off the
    share for every day × camera, and it was the single most expensive thing in an
    image run. A blank frame is caught where it IS decoded anyway, in
    `_render_preview_frame` (which reports it through `bc_out["blank"]`), and
    `_on_row_blank` turns that one row's tick into a cross reading "image is blank
    (all zero)" — after which the row behaves exactly as it did when the search
    decoded it: not savable, not sendable, no preview. The opened day's Frame time
    column already made this same split for the same reason.
- **Time window** — the Image Slider's own `DatePickerDialog` (`_open_time_window`,
  `allow_live=False` because this tab has no live mode). One
  calendar, From/To to the minute, a **Multiple days** mode and a ⚙ per-day window —
  so a day looks, clicks and remembers the same in every tab, and **the days need not
  be contiguous** (Monday + Thursday is a legal search). What it replaced was
  `_TimeWindowDialog`, this tab's own Start point / End point pair of calendars with
  whole-hour spin boxes, i.e. a second time picker to keep in step with the Slider's.
  - The selection is `self._tw_windows`: one `(start_ns, end_ns)` per picked day, the
    **end EXCLUSIVE** (`is_t.seg_bounds_ns`) — the old single pair was inclusive to
    `:59:59`. `_day_windows()` is the day → window map, `_selected_days()` its keys,
    and `_start_search` clips **each day's** candidate shots to **that day's own**
    window. `_tw_times` / `_tw_day` / `_tw_segments` exist only so the dialog reopens
    on the previous pick. `per_col` is deliberately NOT clipped — it only serves value
    look-ups around a shot.
  - **The default is the shift, 07:00-20:00** (`_tw_times` in `__init__`), not the
    calendar day: nothing is shot at four in the morning, and a window starting at
    midnight made every search read hours that hold nothing. The picker's own
    `DEFAULT_FROM_HOUR` / `DEFAULT_TO_HOUR` (08-19) are untouched — this tab always
    passes its own four numbers, so the other tabs keep the house default.
  - `_cam_scan_key` keys off the whole day tuple, not a first/last pair: two different
    day sets can share their ends.
- **Cameras** — the **Cameras** button opens the Image Slider's own
  `CameraPickerDialog` (`_open_camera_picker`), fed the already-scanned list as
  `preloaded_cameras`, so the search box, the highlighting and the saved presets are
  literally the Slider's — a camera set saved in one tab is offered in the other. It is
  opened with `show_layout=False`: the whole **Layout** box — the arrangement board and
  the camera-size buttons — arranges the Slider's multi-camera grid, and the Shot Finder
  has no grid, so the dialog comes up at its old size and its OK writes neither
  `cam_layouts.json` nor a `layout_config`. The panel keeps only the picked list
  (`_set_selected_cameras` rebuilds it, `_cam_num_for` resolves the `#` column from the
  scan or from the folder name itself).
  The scanned list comes from `_load_cameras` (all 24 hours of a day, deduplicating, walking the window
  newest day first until `CAM_SCAN_DAYS_WITH_DATA` days have answered — at most
  `CAM_SCAN_MAX_DAYS`, so a 48-day range costs the share no more than a couple of days).
  It used to scan the FIRST day of the range only: a long range starting on a weekend or
  any day the archiver wrote nothing gave "No cameras found", and the list matched
  nothing for the rest of the session, so a range that searched perfectly well could
  never have a camera added to it. Opening the picker also retries a scan that came back
  empty (`_cam_loading` and `_cam_scanned_key` guard it, so one window is walked once),
  and the dialog runs its own scan when the preload is empty — it is never stuck on an
  empty list. **Every picked camera is searched**, each into its own
  results tab (`_rebuild_result_tabs`, tab label = `_clean_cam_for_filename`); the tab
  bar hides itself at one camera, so the ordinary case still looks like a plain table.
  `_active_cam` is now only which row of the list is highlighted — it used to be the
  only camera a search ever looked at, which made the list itself pointless. The PV data
  is fetched once per day and only the picture is resolved per camera.
  `_table` / `_day_results` are read-only views onto the tab in front, so preview, save,
  send-to-Slider and the shot dialog need no notion of tabs — and the row index → result
  index alignment they depend on stays inside one camera (`_tab_for` routes an arriving
  result by ITS camera, never by which tab happens to be in front). `_all_results()` is
  the across-tabs view, used only for the summary line.
- **Every searched day gets exactly one row per camera** (`_DayResult.status`): `ok` = a shot AND a
  usable picture, `no_image` = real values but nothing to look at, `no_data` = nothing to
  search (archiver outage, no samples, no numeric values, or the day threw). The last two
  are red rows carrying the reason, built by `_add_failed_row`; only `ok` rows can be
  previewed, saved or sent on, and `_on_search_done` counts them separately from the row
  total. Emitting nothing for such a day is what let a search over one busy day (the
  archiver refuses a whole day above ~110 k samples, see `fetch_samples_split`) show an
  empty table whose only explanation was one line in the Log box.
- **Results** — one row per camera × day: values, extra columns, matched image path,
  preview (`_on_selection_changed` → `_load_and_show_preview`). A cell click reveals
  the file (`explorer /select,`), a double-click opens that day's shots in a window of
  their own (see below). Each `_DayResult` persists its search-time state (`search_cols`,
  `extra_cols`, `criteria_csv`, `cam`, `col_meta`, `img_path`) so previews, saving and
  open-in-slider read the search, not the live UI.
- **The opened day** (`_build_day_panel`, `_build_day_window`, `_show_day_panel`,
  `_fill_day_table`, `_show_day_shot`) — double-clicking a day row lists that day's
  in-tolerance shots in a **window of its own**: a non-modal `QDialog` holding the shot
  list and, beside it, that day's PV curve, and nothing else. It is sized to 60/55 % of
  the main window and centred on it the first time only (`_place_day_window`); after
  that the operator's own move and resize are kept for the session. Its title bar says
  which day and which camera is listed, which is why the panel carries no header line
  and no ✕ of its own. The picked shot is drawn in the **panel preview on the right** of
  the main window, the same place a day row's picture appears, and
  each shot is resolved through `_find_image_for_shot`, which probes the hour folder of
  that shot's own time instead of `dr.hour_folder` (the hour of the day's best shot only).
  This was a modal dialog carrying a second `_PreviewWidget`, i.e. its own copy of the
  table and of the picture, on top of the results it came from. Everything the panel needs
  is captured when the day is opened (`_day_dr`, `_day_rows`, `_day_cam`,
  `_day_hour_cache`), so a later edit of the left panel or of the camera list cannot
  re-aim it — and it closes itself (`_hide_day_panel`, which the window's own Esc / ✕
  route to as well) on a new day-row selection (`_on_main_selection_changed`) and a new
  search, because a shot list outliving its own day row describes a day that is no
  longer selected. A redraw asked for by Gradient / Contrast / Brightness / Gamma goes
  through `_refresh_preview`, which re-renders the **picked shot** while the window is
  open and the day row otherwise. Every such test goes through **`_day_open()`**, which
  reads the WINDOW's visibility: a child widget of a hidden window is not `isHidden()`
  as far as Qt is concerned, so the panel's own flag would answer "open" for a window
  that is not on screen. It carries no Open / Save buttons of its own — those
  exist once, in the panel's **Save & Send** group (see **Export** below).
  - **A camera-tab change no longer drops it.** `_focus_day` / `_focus_shot_ns` /
    `_focus_panel_open` (guarded by `_restoring_focus`) are the day and the shot the
    operator is looking at, and `_on_result_tab_changed` re-establishes both in the tab
    that comes to the front: the row of the same day is selected, the shot list is
    reopened and `_select_day_shot_by_ns` picks the shot with the same timestamp
    (nearest wins). The list is PV-derived, so its timestamps are identical in every
    camera's tab. Order matters — the day row is selected FIRST (that fires
    `_on_main_selection_changed`, which closes the panel), only then is the panel
    reopened. A camera with no row for that day keeps the day selected instead of
    jumping somewhere else. A tab that has NOT been filled that far yet (a search is
    still running) keeps the wish instead of dropping it: `_maybe_restore_focus_row`,
    called from `_on_day_result` and `_add_failed_row`, selects the awaited day the
    moment its row lands. Reopening the list is wrapped, so a shot list that fails to
    build cannot also cost the selected day row. Before this, switching camera left an
    empty preview and the day had to be found and double-clicked again.
  - **Frame time column** (`_day_img_col`, `_day_set_image_cell`,
    `_day_frame_time_text`, `_on_day_row_double_clicked`) — the frame belonging to each
    shot, named by **its own timestamp** (`_ts_from_stem` → `_ns_to_prague`, printed
    `%H:%M:%S.%f`[:-3], the same shape as the Prague Time column so the two can be read
    against each other). It used to print `Path.name`, i.e. the camera name plus a
    19-digit stamp, which is why the column took half the window; the camera is in the
    title. The full path stays in the tooltip and in `UserRole`. The column Stretches,
    which also means its width is never recomputed from the cells — a
    `ResizeToContents` column would be re-measured over every row the fill writes.
    - **`UserRole` has THREE states**, not two: a path, `""` (asked, and there is no
      frame) and `None` (not asked yet). It used to store `None` for both of the last
      two, so a shot with no frame was probed again on every click and every arrow key.
    - **Double-click, not single click**, opens Explorer with the frame selected
      (`_reveal_in_explorer`), from anywhere on the row. A single click only picks the
      shot. Required, not cosmetic: Qt fires `clicked` before `doubleClicked`, so a
      single-click reveal fires on a double-click too, and browsing the list threw an
      Explorer window per row.
  - **The column fills itself** (`_start_day_prefill`, `_on_day_img_batch`,
    `_on_day_fill_done`, `_DAY_PREFILL_*`) — one daemon thread started from
    `_show_day_panel` through `QTimer.singleShot(0, …)` (so the first paint is not held
    up, and `_restore_focus_to_row` has moved the selection by then). It walks **from
    the selected row to the end and wraps to the top** — coming back to a camera tab can
    land the selection near the bottom, and filling the top first fills the part nobody
    is reading. Answers are emitted in batches of 200 (or every 250 ms) over
    `_PreviewSignals.day_img_batch`: every `setData` fires `dataChanged`, so one signal
    per row is thousands of queued slot calls and repaints. The batch list is **rebound,
    never cleared** — the emitted list is still held by the queued connection.
    - What made this possible is the tab's **shared `DayScanCache`** (`self._scan_cache`,
      built in `__init__`, `forget()`-ed in `_rebuild_result_tabs`), threaded through
      `_find_image_for_shot(…, scan_cache=…)` — which already existed on
      `_find_image_in_day` and was simply never passed. The cost is then one folder
      reading per HOUR the shots span (20-28 for a whole day, 3-4 s) instead of one per
      row (~150 ms each, i.e. ~300 s for 2 000 shots). The cached answer is identical:
      `nearest_frame` weighs both bisect neighbours against the same tolerance. The
      search's own per-camera lookup goes through it too, so twenty cameras of one shot
      cost one reading of the hour folder rather than twenty. Kept across days because a
      closed hour never changes (`_hour_is_closed`); the hour still being written to is
      re-read after `_OPEN_FOLDER_TTL_S = 20 s`, which is the one staleness window.
    - **One generation counter, `_day_fill_gen`**, bumped as the FIRST statement of
      `_hide_day_panel` (before `setRowCount(0)`, which fires a selection change of its
      own) and in `_show_day_panel` before `_fill_day_table`. Every way out of the list
      — a new search, another day row, another camera's tab, Esc, ✕ — goes through
      `_hide_day_panel`, so that one line covers all of them. The worker checks the
      generation before each row and before each emit; the slots check it before they
      look at anything else.
    - `_show_day_shot` reads `_day_cell_known(row)` on the main thread and skips the
      share when the path is already known — the file is still read and decoded on the
      worker thread, which is the slow part. That is what turns holding the down-arrow
      through a filled list from a 150 ms stall per row into nothing.
    - The fill never calls `_image_problem`: it opens and decodes the frame, and doing
      that for two thousand shots over the share is minutes. A broken frame is caught
      when it is previewed.
  - **The day's PV curve** (`_ensure_day_graph`, `_draw_day_graph`, `_move_day_marker`,
    `_on_day_graph_click`) — beside the shot list in a horizontal `QSplitter`
    (**40/60 on the first open, the curve taking the bigger half** — the list is a few
    narrow columns of numbers and a time, the curve is what the day is read from; it
    was 55/45 while the last column printed whole file names), answering "where in the
    day is this shot?". One line per searched
    PV over the WHOLE day, in the units the table prints (`_diff_ui_value`, values
    snapped with `_quantize_col`, so the curve and the cells cannot disagree), a second
    unit on its own right-hand axis, the target ± tolerance as a dashed line and a
    band, a red dot on every shot in range, the picked hours shaded, and a black
    vertical line on the shot selected. Left-click picks the nearest shot, so the graph
    and the table drive each other; the toolbar owns every other button.
    - **Drawn `steps-post`**: a PV is what the archive last wrote until something new
      is written, so the line is flat and steps at each sample — the same as CS Studio
      and as `if_t._redraw`. Sloping from one sample to the next drew values that were
      never measured, and an hour-long gap came out as a diagonal across the graph.
      Only the sample series steps; the target line, the band, the in-range dots and
      the marker are unchanged, and `_on_day_graph_click` reads `_day_plot_ns` rather
      than the drawn series, so picking is unaffected. Pinned by
      `testing/test_sf_day_window.py`.
    - The data is `dr.per_col`, captured at search time — **no day is read again**.
    - matplotlib is imported on the FIRST day opened, never at startup, and the
      toolbar comes from `if_t._make_mpl_toolbar` (via a `_get_finder_module()` loader
      that mirrors `_get_slider_module()`) — the one factory that also repaints the
      icons dark. A plain `NavigationToolbar2QT` draws them white on white here.
    - `_ns_to_prague` returns a **naive** Prague datetime, like every sample row's
      `_dt`: matplotlib plots a tz-aware datetime in ITS own timezone, which moved the
      whole curve two hours. `_on_day_graph_click` therefore *replaces* the tz
      `num2date` hands back rather than converting it.
    - Only the vertical line moves when another shot is picked — replotting 36 000
      points per click is not a thing the panel does.
- **Clear table** (`_clear_results`, in the Results header) empties the table and drops
  its camera tabs back to a single empty one, clearing the preview with the rows it
  belonged to. The Time window, the PV list and the camera selection are kept — a search
  used to be the only way back to a clean table, so the tab could be left holding rows
  that no longer described what the left panel was set to.
- **Export** — the **Save & Send** group (formerly "Search & Results"; the section KEY
  is still `search`, so the remembered open/closed state survived the rename). It holds
  an **Act on** selector, one row of send buttons — `_btn_open_finder`,
  `_btn_open_slider`, `_btn_send_workshop` (`_SEND_BTN_QSS`, the same small-button block
  as in `if_t.py` / `is_t.py`; three labels fit 80 px each, checked by
  `testing/render_send_rows.py`) — and `_save_results` (annotated PNGs) under them.
  - **Act on** (`_scope_cb`, `_export_scope`, `_sync_scope_selector`) — *Whole day(s)*
    (the selected day rows, all of them when nothing is selected) or *Selected shot*
    (the one picked in the day detail). `_on_send_to_slider` / `_on_save_images` route
    to `_day_open_in_slider` / `_day_save_image` for the shot scope — the same two
    methods the day detail's own buttons used to call. The shot scope is only enabled
    while a detail is open. `_on_send_to_finder` follows the same selector: the picked
    shots for *Selected shot* (the day's own matched shot when none is picked — the
    whole list is deliberately not sent, since every moment costs an archive look per
    camera), one moment per day row otherwise, and over twelve of them it asks first.
    Save still names what it acts on in its label; the three send buttons say it in
    their tooltips instead, because three labels on one row have no room for it.
  - All three act on the camera tab in front and are enabled by `_sync_export_buttons`
    from what THAT tab holds. File names and the Slider's `cam_name` come from the row's
    own `dr.cam` (the camera the search ran with): saving used to refuse outright once
    the camera was removed from the list, and to name files after whichever camera was
    highlighted rather than the one in the picture. The send buttons now **stay in
    place and grey out** rather than appearing and disappearing: three of them on one
    row means a hidden one moves the others under the pointer, and a disabled button
    with a tooltip saying why ("the Image Slider is not connected") tells the operator
    more than an absent one. **Send to Image Finder** works off the day rows alone —
    it hands over moments, so a day whose picture was never loaded can still be sent,
    and `_sync_export_buttons` enables it from `dr.ts_ns` rather than from `img_path`.
    All three render through `_render_u8` with the current Contrast / Brightness / Gamma
    state, so a saved or handed-over frame looks like the one that was on screen.
    `_open_in_slider` carries each file's `_DayResult` next to it
    (`list[tuple[Path, _DayResult]]`) instead of an index into a parallel list: one
    skipped day used to shift every later caption onto the wrong picture.
- **Progress** (`_ProgressTracker`, `_SearchSignals.progress/stage`, `_prog_take`,
  `_prog_release`, `_on_progress`, `_on_stage`, `_sync_prog_text`) — the bar under
  **Load data** prints how far along it is, how many matches are in and roughly how
  long is left (`time.monotonic`, not the wall clock); WHAT is being read goes on the
  wrapping `_prog_lbl` under it, because the panel is 275 px wide and a progress bar
  cannot wrap its own text.
  - **Fractions of a step, not steps.** One step is still one (day, camera) pair, but
    each is filled in as the work happens: `_ProgressTracker.PV_SHARE` (0.35) of a
    day's step is the archiver read, the rest is spread over that day's cameras, so a
    finished day adds up to exactly one step per camera and the bar cannot drift from
    the count printed on it. Counting whole pairs made the common case — one day, one
    camera — a single step that stood at 0 for the entire wait and then jumped to full.
  - **Where the fractions come from.** `cpva_client.warm_days(on_done=…)` reports each
    channel-day of the pre-warm (which is the whole PV read for a normal search) and
    `_load_api_for_day(on_col_done=…)` reports each PV of a day the warm-up did not
    cover. The tracker is fed from pool threads, hence its lock; it never emits a value
    below the last one. Every code path that ends a day closes it exactly once
    (`day_done`), including the failed-day and exception paths.
  - **How long is left** (`_EtaClock`, `_SearchSignals.eta`, `_on_eta`) — two clocks,
    because the two halves run at completely different speeds: the archiver read is
    counted in channel-days, the frame walk in day × camera steps, and what is left is
    the sum. Timing whole days instead meant a 1- or 2-day search never showed a
    number at all (however many cameras it walked) and the pre-warm — the entire job
    of a Load data — was outside the estimate. Both rates are handed back at the end
    (`rates()`) and seeded into the next run (`_eta_seed_pv` / `_eta_seed_frame`), so
    only the first search of a session has to wait before it can say anything. A phase
    with work left and no measurement yet returns -1: half an answer would promise the
    search in the time of its faster half.
  - **One bar, two jobs.** `_prog_take(owner, …)` / `_prog_release(owner)` also lend the
    bar to the camera scan (`_load_cameras`, `_CamLoadSignals.progress`,
    `_on_cam_progress`), which walks 24 hour folders per day over SMB whenever the
    picked days change and used to show nothing but the words "Loading cameras…". A
    search always wins the bar; the scan then goes quiet instead of overwriting it.
- **Tables** — `_TABLE_QSS` (the same values as `wk_t._TABLE_QSS`, spelled out because
  `wk_t` loads after this tab) on the results tables AND the day-detail table. Left
  unstyled, `setAlternatingRowColors(True)` takes the alternate colour from the app
  palette and paints every second row dark red-brown. The per-row pastel backgrounds
  (`#f8d7da` / `#fff3cd` / `#d4edda`) are deliberate status colours and stay.
- **Intensity scale** — the `Con:` / `Bri:` / `Gam:` rows (default off = absolute), the `Gamma`
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
- Held frame arrow: `HOLD_STEP_RAMP` (2, 3, 4, 5 frames/s one rung per second, then 8/s
  from 5 s and 10/s from 8 s held — see *Holding a frame arrow*; `HOLD_STEP_RATES` is
  just the rungs of it, kept for the test)
- Settle tiers, in order after a release: `PROXY_REFINE_MS` (200 ms,
  `_refine_current_frame` — single-cam to `REFINE_MAX_SIDE`, multi-cam back to full tile
  size, per camera on its own frame via `_per_cam_redraw_in_place`) then `HQ_SETTLE_MS` (500 ms, `_hq_upgrade_tiles` — every tile to `HQ_SIDE`, i.e.
  native). **The 200 ms pass must not re-arm itself.** `_schedule_refine` also restarts the
  HQ tier, and a preview repaint calls `_schedule_refine`, so a refine that repainted the
  tiles from the preview kept pushing the 500 ms deadline out 200 ms at a time: `hq=0`
  forever, tiles pinned at preview resolution, and ~`n_cams x 5` wasted repaints a second
  on an idle panel (measured in the diag log as `prev≈1750/min` with `prox=100%` on a
  6-camera archive window). `_display_multicam_index(from_refine=True)` is what breaks it;
  `testing/test_settle_native.py` fails on a regression. Live mode never had the loop — it
  re-arms from arriving frames instead.
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
CamRefRectConfig(left, top, right, bottom, color, width, alpha, show)
                                      # the camera's own permanent reference square
```

### Image pipeline
`load_image_scaled(path, max_side, brighten, gradient_id, brightness_offset,
ref_image, sub_threshold, contrast, auto_bright, sub_offset, stats_out)` is the core
decode → scale → auto-stretch → reference-diff → brightness/contrast → palette path.
The enhancement chain is shared by **every** palette including `Default`: that branch
used to `return` right after the 16-bit normalization, which silently made both Auto
checkboxes and both sliders dead controls there while the preview layer still applied
them. Around it:

- `_open_reader(path)` — a `QImageReader` over the file's **bytes**, not its path (a UNC
  path makes Qt do its network I/O without releasing the GIL and freezes the whole
  process for the round trip). The bytes come from `_read_frame_bytes`, which **waits out
  a frame that is still being written**: the camera writes straight onto the share under
  the final name, so the dir-watcher's "file created" event names a file whose pixels have
  not arrived, the read returns a null `QImage`, and nothing in the pipeline re-asks —
  only the 1.5 s stuck retry (`CAM_STUCK_RETRY_AFTER_S`) did, which is exactly the
  "it used to be instant, now it takes a second" the operator reported. Measured with
  `testing/bench_live_latency.py` at 145 ms read latency: a frame filled over 400 ms was
  on screen **1531 ms** after it was complete, against **62 ms** once the read waits.
  The wait is entered only on POSITIVE evidence of an unfinished file — `_bytes_complete`
  is false, i.e. a PNG without its closing `IEND` chunk, or a file too short to carry a
  magic number at all (the 0-byte case the watcher hits most often) — never on a read
  that FAILS (an unreachable share blocks ~45 s per attempt and must not be retried here)
  and never on a file older than `_MIDWRITE_MAX_AGE_S` = 120 s, so a frame that has been
  truncated in the archive since this morning still fails fast instead of costing the
  preview sweep a second each. `_MIDWRITE_WAITS_S` totals 1.09 s, past which the frame is
  treated as undecodable exactly as before.
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
- `img_scale.render_u8(arr16, auto_mask, full_scale, gamma, contrast, offset, out)` — the
  whole display pipeline for anything that still has 16-bit pixels: absolute scale bent by
  gamma, then the contrast gain, then the brightness offset, in float, rounded once at the
  end. `load_image_scaled`'s 16-bit branch calls it directly; `_proxy_render` builds the
  same thing as a 256-entry LUT over the stored codes.
  - contrast pivots on the frame's **black level** (`_BLACK_PCT`), not mid-grey. With
    the mid-grey pivot a frame sitting at code ~29 went black at contrast +20, one nudge
    of the slider.
  - the `AUTO_*` mask only fills in the VALUE of each control (`img_scale.auto_bc_pair`):
    Auto contrast → `255/(hi-lo)` as a gain, Auto brightness → `-lo` as an offset. Both
    together are the percentile stretch; neither one alone is.
- `_apply_bc` — the same arithmetic one step later, for the sources that have no
  full-precision data left: a subtraction difference, an 8-bit file. Takes the same
  `auto` mask, and on the subtraction path a `gamma` as well (see below). Everything else
  must go through `render_u8` — a gain applied after the rounding posterises a dim frame.
  The pivot and the Auto window are measured AFTER the gamma bend, exactly as `render_u8`
  measures its own through its curve.
- `_apply_stretch` (alias `_autostretch_gray`) — percentile stretch, kept for the
  DETECTION helpers only. It is no longer part of any display path.
- `_img_planes` / `_img_from_planes` — extract/insert for the above; colour images are
  handled as RGB32 with the percentile anchors taken from the luma, so `Default` keeps
  RGB sources in colour and still honours the controls.
- `_stat_sample` — percentile anchors come from a strided ~250k-pixel subsample; the
  exact percentile on a native-resolution frame costs more than the whole scrub budget.
- `_gain_to_contrast_slider`, `_auto_bc_put` / `_auto_bc_get` — the side channel that
  parks the greyed-out Auto sliders on the value actually used. The parked number is a
  real setting now: put the slider there by hand and the picture is identical (the slider
  reaches 64×, past anything a frame here asks for). It is still **display only** —
  switching an Auto checkbox off restores the user's own value (`_contrast_manual`,
  `_brightness_manual`), because baking Auto's number into the manual control made the
  checkbox impossible to undo.
- `_apply_reference_diff` + `_diff_stats_put` / `_get` — subtraction with statistics.
  What the statistics ARE: `bg` (the frame's own floor — `diff.min()`, so nearly always
  0), `above` (pixels above it), `pct` (that as a share of the frame), `mean` (the
  average lit difference), `min` / `max` (the faintest counted and the brightest pixel),
  `levels` (`_diff_levels_from_counts`
  — how many pixels reach each of `_DIFF_LEVELS` = 10/25/50/100) and `hist`
  (`_diff_hist_from_counts`, `_DIFF_HIST_BINS` = 64 buckets over the LIT pixels only).
  All of it comes off ONE `_diff_counts256` bincount, so the added figures cost nothing
  on top of the histogram that was being built anyway. The LEVELS are the numbers a
  bare count was missing: "everything that is not exactly the reference" is dominated
  by sensor noise and reads the same on a quiet frame as on a changed one. `min` is the
  darkest pixel that still counts as a difference: with the diff threshold at 0 it is the
  noise floor left behind, and with a threshold set it reads that threshold back, which is
  how to confirm the spinbox is doing what it says. (It was dropped once for exactly that
  reason — "just the threshold read back" — and asked for again.) `bg` is
  measured rather than assumed 0, so a frame whose floor has been lifted does not
  report its whole area as lit. Shown by `Viewer._fmt_diff_stats` / `_fmt_diff_levels`
  (`_fmt_px` groups the thousands) and drawn by `_DiffHistogram` — bars on a
  **square-root** scale (on a linear one the faintest bucket, always the biggest by
  orders of magnitude, is the only bar you can see), tick numbers across the axis
  (`_TICK_SETS`, densest set whose labels do not touch — a 265 px panel gets nine, a
  tile-sized one three), and the numbers WRITTEN INSIDE the picture: the tallest bar's own
  count top-left as `top` (never `max` any more — two different numbers, a pixel count and
  an intensity, were both labelled that), and on the right, laid out right to left,
  `max` (red), `mean` (blue) and `min` (purple). Each sits on its own white pad because
  they are printed over the bars, and each is DROPPED rather than overlapped once it would
  run into `top`, so a 275 px panel shows all three and a tile-sized histogram keeps `max`
  alone. A red line marks the brightest pixel and a purple dotted one the faintest
  counted; every colour is set explicitly because this PC runs Windows in dark
  mode and an unpainted widget comes out black. **One block per camera**
  (`_DiffCamBlock`, laid out by `Viewer._show_diff_blocks` / `_set_diff_hist` /
  `_flush_cam_diff_stats`): name and numbers, then that camera's own histogram directly
  beneath them. There used to be a list of numbers for every camera above a single
  histogram belonging to the SELECTED one, so the picture described a different camera
  than the lines above it. The blocks are pooled, not rebuilt (this re-renders on every
  displayed frame), sit in a `_DIFF_BOX_MAX_H`-capped scroll area because the INFO panel
  is anchored above the settings column and does not scroll itself, and the box height
  is MEASURED with `heightForWidth` — a `QScrollArea`'s own size hint has nothing to do
  with what is inside it — and re-applied only when it changes. Pinned by
  `testing/render_diff_stats.py`

  **Three rules keep those numbers meaning the same thing on every frame** (reported
  17.09.2026 as "the maximum jumps around and something amplifies itself"; pinned by
  `testing/test_subtraction_stats.py` and `testing/test_subtraction_steady.py`):

  1. **The difference is taken LINEAR.** `load_image_scaled`'s subtraction branch renders
     the current frame at `GAMMA_NEUTRAL` whatever the gamma control says, because the
     reference (`_load_raw_arr`) is always decoded linear — the two used to be bent by
     different curves, and with Auto gamma by a curve off each frame's own median, so the
     difference was measuring the gamma rather than the pictures. Gamma is now a display
     step on the finished difference, handed to `_apply_bc` alongside contrast and
     brightness, and therefore changes nothing in the statistics.
  2. **Auto is measured once per run and held.** `_sub_hold_on` / `_sub_hold_from_render`
     / `_capture_sub_auto_hold` (+ the per-tile `_capture_cam_sub_auto_hold`) freeze the
     contrast, offset and gamma Auto lands on, and `_apply_sub_hold` puts them into the
     render parameters in place of the Auto sentinels at READ time — `_bc_for` and
     `_cam_disp_get`, never `_bc_raw`/`_disp_snapshot`, which must keep storing the user's
     own settings. Because they land in `bc`, they are part of the pixmap cache key and a
     held render can never be served for a per-frame one. All or nothing: half a hold
     would leave the other row measuring itself on every frame. `_diff_worth_measuring`
     skips a difference with nothing in it — the first frame after Set ref is the
     REFERENCE, whose difference is identically zero, and freezing Auto's "do nothing" off
     it left every later frame unadjusted. Released by `_clear_sub_auto_hold` wherever the
     difference changes meaning (Subtraction toggled, reference set/removed, threshold or
     offset moved, an Auto box flipped, a new scan).
  3. **Only a full-resolution render may write the numbers.** `LoadTask.run` records the
     decode size in `stats["side"]`; `_stats_are_final` / `_pick_diff_stats` keep the
     measured set per FRAME (`_diff_last_final`, `_cam_diff_final`, both keyed by
     `_frame_of_key`) so a smaller render of the same frame cannot replace it and another
     frame's numbers are never borrowed. An estimate is shown MARKED — `_DIFF_TEXT_PROV_STYLE`
     and a trailing `…` on the line, grey bars with "not final" written on them in
     `_DiffHistogram` (which drops `max`/`mean`/`min` before that warning on a narrow
     histogram). `_current_decode_side` / `_cam_tile_side` / `_refine_current_frame` /
     `_prefetch_idle` render native as soon as there is a reference and the user is not
     dragging or playing, so in practice the mark only appears mid-gesture. A preview
     paint measures nothing at all, so `_proxy_try_paint` marks what is on screen.

  4. **The numbers belong to the REFERENCE, not to the Subtraction checkbox** (23.09.2026).
     With a reference set the frame is always MEASURED against it; the checkbox only
     decides whether the difference is also the picture. `load_image_scaled` takes
     `subtract`: False renders the ordinary frame and fills `stats_out` off the LINEAR
     absolute-scale rendering it builds anyway (`lin8`), so measuring costs no second read
     of the share and no display control can reach the figures. `Viewer._ref_render`
     (`_RefRender`) is the one place that decides all of it — the reference array, the
     flag, the two spinbox values, the three fields the reference contributes to the
     PIXMAP key (neutral while Subtraction is off, so the picture shares the cache entry
     it would have had with no reference) and `mkey`, the separate key the NUMBERS live
     under. Every render path goes through `_ref_render_for` / `_cam_ref_render_for`,
     `_cam_want` carries the whole `_RefRender`, and the completion handlers rebuild
     `mkey` rather than carrying it through the signal.

     Why it matters: the INFO panel is anchored ABOVE the settings column
     (`left_col` = [`info_panel`, `left_scroll`]), so hiding the histogram box moved
     everything under it — the Subtraction checkbox included — about 110 px up, under the
     cursor. Clicking on and off to compare was therefore impossible. `_update_diff_stats`
     / `_collect_cam_diff_stats` / `_flush_cam_diff_stats` are gated on `_has_reference()`
     now, and `_on_subtract_changed` only throws the numbers away when the two SPINBOXES
     changed (`_sub_params_last`) — the checkbox alone changes nothing about what the
     difference is.
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
`_rss_mb`. Reproduce headlessly with `testing/bench_drag.py` / `testing/bench_play.py` (real offscreen
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

`testing/bench_live_dot.py` asserts all of it offscreen against synthetic files in under a minute —
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

Pinned by `testing/bench_master_sync.py` — all six ways the master moves (drag, release, arrows,
master switch, playback, live arrival) plus independent mode, checking every slave
**handle**, not just its picture, against what `_per_cam_slave_targets` resolves — and by
`testing/bench_live_pv_load.py`, which grabs a slider *in live mode* and asserts the moment survives
the history merge.

`_on_per_cam_pressed` also sets **`_is_scrubbing`**, which nothing on this path did before.
`_cam_tile_side` and `_current_decode_side` read it, so the motion downscale and every other
"the user is moving" optimisation were dead on the path the user actually drags.
`_navigating()` is that predicate; `_proxy_is_moving()` is the sweep's version and includes
`_per_cam_scrubbing_cam`.

### A redraw must not move a camera: `_per_cam_redraw_in_place`
With per-camera sliders, "redraw everything" cannot go through `_display_multicam_index`:
that resolves **every** camera from **one** merged index, so a pass meant only to sharpen
the picture moves it as well. Two paths did, and both undid the user's own move:

* `_refine_current_frame` (the 200 ms settle tier) — 200 ms after releasing a **slave**
  slider the tile jumped back to the master's moment. Only independent mode
  (`_per_cam_master_idx < 0`) bailed out of that branch, so the bug lived in exactly the
  mode with a master selected, which is the one people use.
* `_refresh_multi_cam`'s `on_done` — a ⟳ Refresh discarded every per-camera position.

`_per_cam_redraw_in_place()` is the per-camera answer: each tile re-rendered on the frame
`_cam_current_idx` says it is showing (the same truth `_hq_upgrade_tiles` refines at), with
each handle re-set from that frame's timestamp because a redraw can follow a timeline
extension — the axis grows, the same moment maps to a different slider step, and a handle
left alone slides off its own picture. A slave is re-aimed by **one** thing: the master
moving (`_per_cam_sync_slaves`).

Two traps it must not fall into, both measured:

* It passes `from_refine=True` down to `_per_cam_display_one`, which then skips the
  `_schedule_refine()` a preview repaint normally calls — otherwise the settle pass re-arms
  itself every 200 ms forever, the loop `testing/test_settle_native.py` exists for.
* It does **not** write `_nav_frame`. That map is the navigation's own running position,
  written synchronously so a second step inside one tick still counts (`_nav_request`);
  pushing the last *painted* frame back into it rolled that position backwards, and a held
  frame arrow lost about one step in five to the settle pass firing between them.

### Holding a frame arrow: `HOLD_STEP_RAMP`
`btn_prev` / `btn_next` are wired to **`pressed` / `released`**, not `clicked` — `clicked`
fires on release, so a hold would have ended with one extra step on top of the ramp. The
press is always exactly one frame; `_hold_timer` then repeats at the rungs of
`HOLD_STEP_RAMP` — **2, 3, 4, 5 frames per second, one rung per whole second, then 8/s once
five seconds have been held and 10/s from eight seconds** — and the first repeat lands
500 ms in, which is what separates a click from a hold with no separate delay constant.
The ramp is a table of `(seconds held, rate)` pairs and `_hold_rate(held)` returns the last
entry whose time has passed, so adding or moving a rung is one line and needs no arithmetic
on the index. `HOLD_STEP_RATES` remains as the rungs alone, for the test.

The two fast rungs are deliberately at the END of a long hold. Every step is a share read
per camera (130-160 ms each), so at 10/s the reads are outrun and only the frames that
arrive in time get painted — which is exactly what "hold it down until I get there" wants,
and is not what the first second of a hold should do.

The rung is read from the **clock** on every tick, not counted up. Counting steps per rung
sounds equivalent and is not: a tick delayed by the GUI thread then pushes the whole ramp
back, so a busy panel — the case the ramp exists for — crawls at 2/s for four seconds.
`_hold_step_tick` also stops on `not btn.isDown()`, because a button disabled under the
cursor (folder closed, mode change) never emits `released`.

Pinned by `testing/test_hold_and_slave_free.py`, which checks `_hold_rate` as a pure
function over the whole table and then holds the real buttons through `QTest`, sampling the
timer's interval against the real clock. It asserts the rung, not a frames-per-second
count: offscreen, decode shares the one thread and GC pauses of nearly a second were
measured mid-hold, so a count over any single second is noise. The count is still checked
for the one thing it can prove — the ramp never runs away past its top rung.

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

**A master PAINT is a slave trigger** (`_live_resync_slaves_on_master_paint`, called from
`_note_cam_shown` — the same hook the PV panel hangs on, for the same reason).
`_live_advance_cam` offers the slaves the master's new moment at **request** time, while
that frame is still being read off the share, so a camera whose own file appears inside
that gap (one share read, 130–160 ms, longer while the frame is still being written) was
synced to the master's PREVIOUS moment and nothing offered it the new one again — the
master's next sync is a whole cadence away, so that tile sat one shot behind the rest of
the grid until the 1.5 s stuck retry dug it out. It is the same tile every time, which is
what made it read as "some cameras are just slower". Measured with
`testing/bench_live_latency.py --cams 3 --trace`: **1547 ms** for that one camera against
63 ms for both its neighbours, now **125 ms**. This is also what
`testing/bench_live_rollover.py --cams 3` was failing on.

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

Pinned by `testing/bench_live_slave_refresh.py`: a master at 3.3 Hz, a healthy slave arriving 54 ms
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

Measured with `testing/bench_drag.py` (4 cameras × 900 frames, `--latency-ms 145`, one pass over the
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

Playback (`testing/bench_play.py`, 4 × 600, 0.5 / 1 / 5 %/s): 16 ms tick, spread 100 %, zero
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
| `ImageView` | frame display, overlays (cross/circle/square, SC top-N points, PV bar, cam label + timestamp, green `Ref:` badge via `cam_ref_text`, the PCW3_NF reference square), zoom, calibration. `paintEvent` only opens the painter and hands it to **`_paint_body`** under `try/finally`, so the painter is closed even when the drawing raises. It was one method, and a `from PySide6.QtGui import QFont, QFontMetrics` inside the `energy_text` branch made both names local to the whole method: with no energy text to draw, the camera name strip below hit an unbound `QFontMetrics` and painting stopped there — the overlays painted last (the reference square) flashed up once and never again, and the painter left open on the widget turned the next window move into a hard crash (`endPaint() called with active painter`). The local import is gone, both names being module-level already, and the split keeps any future failure inside its own tile. Pinned by `testing/test_ref_rect_overlay.py`, which drives `_paint_body` directly with and without energy text |
| `CameraView` / `MultiCameraGrid` | one camera tile / the multi-cam grid; `_FreeLayoutContainer` and `_AutoLayoutContainer` are the two layout backends. **The header reflows instead of being cut off** (`_relayout_labels` / `_set_ts_wrapped` / `_fit_name_label`, run from `resizeEvent`, from `set_timestamp` when the text LENGTH changes, and from `set_label_font_size`): a tile too narrow for name + timestamp side by side drops the timestamp onto a second header line, and a name too long even for a whole line is middle-elided with the full name in the tooltip. `_WRAP_HYST_PX` keeps a tile dragged to the threshold width from flickering between one and two lines. The name label carries stretch **0** and the timestamp stretch 1 — the old 1:2 split was the original fault: a camera name wider than a third of the tile was cut off however much empty room sat next to the timestamp. `image_overhead_px` counts the wrapped row, so the auto layout never hands the picture room the header has already taken — the second line grows the strip ABOVE the frame, it never covers it. `testing/render_tile_label_wrap.py` renders three widths and checks all of it |
| `_TileDragHost` (+ `CameraView._tile_mouse` / `_tile_drag_mode`, `MultiCameraGrid.on_tile_layout_edited` / `reset_layout_to_auto`) | moving and resizing the tiles **on the running grid** — the arrangement board gesture, but on live pictures. Both containers mix it in and hold the arrangement in `_manual` as canvas fractions, which is what survives a resize; an auto container freezes its current on-screen geometry into `_manual` on the first drag and then only places it, since otherwise the next resize would recompute the layout and undo the drag. Mouse events are caught in an event filter on the CHILDREN (the image widget covers everything but the header and a 3 px margin, and has mouse handling of its own) and in `CameraView`'s own handlers for that margin — one code path, `_tile_mouse`. The name bar moves, the border resizes, and the top zone is narrower than the others because a 9 px one swallowed the name bar and turned every move into a resize. Edges snap to the neighbouring tile and to the canvas borders/centre (Alt disables it) so tiles butt up instead of leaving the ragged gaps a free drag always leaves; a press that never leaves the dead zone is reported back as a plain click, so the name bar still selects the camera. A finished drag is stored through `CamLayoutStore.save_manual_entries` under the same key and shape the board writes, and right-click → *Auto-arrange cameras* (`forget_saved`) is the way back — reachable where the arrangement was made, not only in the picker. The **⛶ Reset layout** button in *Display* is the softer way back (`MultiCameraGrid.reset_layout`): it restores the arrangement the set was OPENED with — `_layout_opened`, kept apart from `_layout_config` precisely because every drag overwrites the latter — which is the preset's own layout or the one remembered for these cameras, and auto when there was none; the store is rewritten to match so the next open agrees with the screen |
| `compute_camera_layout` (+ `_cam_layout_weight`, `cam_size_class` / `set_cam_size_class`, `_layout_trees` / `_layout_order_pool` / `_layout_order_cap` / `_layout_tree_pool` / `_reading_order_cost`, `_usable_extra` / `_split_extra` / `_place_with_slack`, `_justified_rows_layout`) + `CamLayoutEntry` / `CamLayoutConfig` / `CamLayoutStore` / `_LayoutCanvasWidget` / `_CamSizeRow` | camera auto-layout, the arrangement board it is previewed and dragged on, and the five per-camera size classes that steer it. A layout is a split tree (each cut puts two tiles side by side or stacks them), which is why the whole thing is searchable: a subtree behaves like one tile obeying `height = width/A + B`, so it is built bottom-up in two floats and placed back exactly. **The tree is cut two different ways, because equal sizes and set sizes are two different questions.** *All cameras the same size* (nothing set, or everything set alike): the tile sizes come from the SHAPES of the pictures — every tile hugs its own frame (`_place_with_slack`) — and candidates are picked in **two passes**: first the largest the SMALLEST frame can be, then, among the arrangements that keep it within `_LAYOUT_SMALLEST_TOL` (0.90) of that, the one showing the most picture in total, with the tidiest tile sizes as the last tie-break. Strict leximin was the earlier rule and would rather grow one frame a few percent than fill the canvas: three landscape cameras came out as one row across the top with two thirds of the canvas empty, where two columns show twice the picture for a frame 4 % shorter. The floor is what keeps the relaxation from turning into sum-maximising. *Sizes set*: hug-packing **cannot honour a size at all** — two square cameras side by side share one height and so come out identical whatever their size class, which is why setting sizes used to change almost nothing (16 : 1 came out equal, and 16 : 1 : 8 : 4 came out with Smallest the SECOND BIGGEST frame on screen and Medium one of the two smallest — the reported bug). So the tree there only says who sits where, and every split is cut in proportion to the sizes the cameras in it asked for (`_share_sum` / `_place_by_share`), which does give a Smallest camera a narrow tile with empty room around its picture — the price of "make that one big and that one small". Tile area is not the answer either, since a picture only fills a tile shaped like itself, so the cut is corrected up to `_LAYOUT_SIZE_FIT_PASSES` (14) times over: measure the picture each camera really got per unit of size asked (`_size_mismatch`), pull the over-served tiles in, let the under-served out, half a step at a time (a full step overshoots and rings). Among topologies, the closest fit wins; only fits within `_LAYOUT_SIZE_MATCH_TOL` (1.15) of the closest are then judged on picture shown, and an arrangement that puts a frame above one that asked for MORE (`_size_order_kept`) is thrown out whenever any arrangement keeps the order. **The camera ORDER the tree is built over decides which arrangements exist at all** — a cut can only put two cameras side by side if they are neighbours in that order — so the search runs over a pool of orders (`_layout_order_pool`): the sensible sortings (by shape, by size, both ways) and, from each, its reverse, its rotations and the order alternating between its two ends. It used to try the order it was handed plus three or four sortings of it, and that was the *Auto-arrange leaves half the canvas black* bug: on the reported six-camera set, 328 of the 720 possible orders fill 74 % of the canvas and 392 stop at 65 %, and every order the app tried was in the 65 % group. Random shuffles were measured as the alternative and are worse — twice the orders for the same answer, and never the best one on eleven or twelve cameras — besides making the result depend on a seed. Nothing in the pool is built from the order handed in, so the same cameras always give the same arrangement: the size buttons and *Auto-arrange* used to hand in different orders (the remembered arrangement's, and the picked list's) and so drew two different pictures of the same cameras. Which camera lands in which of two equally good tiles does still follow the order handed in, as the last tie-break (`_reading_order_cost`), so the cameras read top-to-bottom in the order they were picked. `_layout_order_cap` stops the pool at 16 orders (12 above ten cameras): measured, the answer stops improving at about twelve. Measured: 16 : 1, 16 : 1 : 8 : 4, all five classes at once, portrait among landscape, tall/wide canvases and eight mixed cameras all come out at the asked ratio to within 1 %. Sizes are shares of image AREA — 1 / 4 / 8 / 12 / 16 for Smallest..Largest, read by `_cam_layout_weight` out of `cam_sizes.json` keyed by camera NAME (so a size follows the camera into every set and every session), defaulting to Medium (8) and to Largest (16) for `PD[1-4]M1?DF` diode arrays — exactly the 2:1 the old hard-coded regex returned. Only ratios matter, a common factor cancels. That diode default is now taken LITERALLY like any other size, so a diode array really does show twice the picture — and a diode array alone with one landscape camera fills only ~35 % of the canvas (~74 % if it is set to Medium by hand), because a tall frame reaches double the area of a square one only by keeping wide empty margins. Kept on purpose; it is the frame the shift is watching. Equal-length rows (`_justified_rows_layout`, still the fallback above 12 cameras) waste half the canvas the moment one camera is portrait and take no weights at all, so above 12 cameras the sizes are ignored. Each tile reserves the label bar so the frame fills its image region; hug-packing (equal sizes) only fills the canvas in one direction, and the leftover in the other is handed down the tree by `_place_with_slack` — offered first to the tiles whose frame would actually grow (`_usable_extra`), and only what nobody can use is split by tile size. The sized cut has no leftover to hand out: it uses the whole canvas by construction, and what a picture cannot use stays as margin inside its own tile. Either way the tiles stay a seamless partition of the canvas — every tile edge shared with its neighbour, columns and rows lined up to the pixel — and what is left over shows up as the grey margin around the frame inside each window, **centred** (`ImageView._img_rect`, `_LayoutCanvasWidget._image_rect_in`): anchoring the frame under the label bar put the entire leftover height into one band below it, which is what "the cameras sit along the top edge and the bottom is stretched to the floor" was. Centring each whole TILE in its own slack instead (an older attempt) is a different thing and is what made the cameras look randomly strewn about with the columns out of line. Set `ELI_LAYOUT_DIAG=1` to have every computed arrangement logged with the aspect used per camera and how much of its tile each frame fills — by eye a grey band and a wrongly learned aspect look identical. Tile colours on the board are keyed by camera name, not by list position: dragging a tile brings it to the front of the list, and an index-keyed palette recoloured every camera on a single click. Results are cached per canvas size (the key includes the weights), since every resize recomputes them — rounded to about a percent of the canvas, so a window drag pays for far fewer of them. The arrangements THEMSELVES are cached separately (`_layout_tree_pool`, `_LAYOUT_TREE_CACHE`), keyed on the cameras alone: which arrangements exist depends on the shapes and the sizes, not on how big the window is, so a resize pays only for scoring them. That is what buys the wide order search — building the trees is the slow half (0.4 ms per order at six cameras, 45 ms at twelve) and it used to run again for every size the window passed through. Screening a wide pool also drops to two correction rounds with twice as long a shortlist (`_LAYOUT_SIZE_WIDE_POOL`), which measures out at the same arrangement in half the time. Measured before → after on the same canvas: six cameras 65 → 74 % of the canvas carrying picture, seven 78 → 80 %, nine 71 → 75 %, eleven 63 → 67 %, twelve 65 → 66 %, with the size ratios unchanged; one resize step costs about 13 ms at six cameras and 52 ms at twelve, where twelve used to cost 224 ms. `testing/test_auto_arrange.py` pins all of it
| `_AspectBox` | keeps the board at the camera area's EXACT ratio, centred, on every resize — so `_board()` never has to letterbox itself and no hatched strips appear beside it. Letterboxing inside a wrongly shaped widget was the first attempt and the strips read as canvas the cameras had failed to use; correcting the window height once on show could not fix it either, because the height added is shared with the widgets above by layout stretch (so it always undershot) and any later resize brought the strips back. What is left over is now plain dialog background. `_board()` keeps a 2 px tolerance: integer widget sizes cannot hit an arbitrary ratio exactly and a one-pixel miss drew a one-pixel hatched sliver |
| `_LayoutCanvasWidget` (`canvas_px` / `_scale` / `_header_h` / `_header_band` / `_tile_margins` / `_draw_tile_caption` / `set_cameras` / `set_canvas` / `tile_selected`) | the arrangement board: a **scale model** of the live camera area, not a sketch of it. It is given the area's SIZE in real pixels plus the live label overhead and header font size; `_reset_tiles` runs `compute_camera_layout` on those LIVE numbers, so the fractions it holds are bit-for-bit the ones `_AutoLayoutContainer._apply` will use, and everything measured inside a tile (name bar, the 3/6/3/3 margins, the label font) is drawn through `_scale()` = board height / live height. Passing only the ASPECT and then reserving the live grid's ABSOLUTE label height on a board a third of the size was the old behaviour, and it was wrong twice: the name bar came out 3-8x too fat, and the search — optimising against a label-to-canvas ratio 2-3x off — could win a genuinely different arrangement than the grid produced, which is why the result had to be thrown away as a "frozen preview". The header bar is drawn at its REAL proportion, which makes it a few pixels tall and useless for text, so `_draw_tile_caption` writes the camera name ACROSS THE FRAME instead, with the size class under it. It shrinks the font to fit, then falls back to `_cam_short_label` (PAM10NF, not C03-039-PAM10NF) and only elides as a last resort — eliding first threw away the middle, the only part that names the camera, and kept the prefix every camera shares. The size line is added only when a second line genuinely fits, and nothing is drawn below the readable floor. The header is otherwise drawn like the real one (the `#444` name block on the left third, the `#333` timestamp block, the refresh dot) and `_header_h` deliberately excludes the tile's bottom margin, which `_tile_margins` owns — counting it at both ends pushed the frame up inside its window. A plain click only selects a tile and emits `tile_selected` (the size buttons listen); `_auto_mode` / `_user_edited` flip on the first movement past `DEAD_PX`, so picking a camera to resize it no longer freezes the arrangement as hand-made. `testing/test_cam_size_layout.py` asserts board == grid to the pixel at three board sizes and against real `CameraView`s in a real `_AutoLayoutContainer` |
| `CamLayoutStore` | persistence only — `cam_layouts.json`, one record per camera SET (`key_for` = sorted names): `{"auto", "tiles", "cam_order", "entries"}`. `auto: true`, and a MISSING `auto` for legacy records, means the fractions are only a frozen preview and are ignored so the grid arranges itself; only `auto: false` overrides. `save_board` is what the picker's OK writes, `save_manual_entries` what a live tile drag writes (same key, same shape, so each finds the other's), `forget_saved` is the way back. Was `LayoutConfigDialog`, whose own window is gone now the board lives in the picker |
| `remember_cam_aspect` / `_cam_aspect_hint` (`cam_aspects.json`) | per-camera frame aspect, learned from the first frame and persisted. The packing needs an aspect *before* any frame is loaded; the name hint only knows "portrait diode array vs square", so a rebuild — every time-window rescan recreates all tiles — used to pack for square tiles and the layout visibly jumped when the frames arrived. `ImageView.frame_aspect_changed` re-runs the packing when a camera's real aspect turns out different; overhead changes (label font, `Ref:` badge) go through `MultiCameraGrid.refresh_auto_layout` |
| `cam_size_class` / `set_cam_size_class` (`cam_sizes.json`, `_CAM_SIZE_CLASSES`) | per-camera size class, keyed by camera NAME and written the moment a size button is pressed — the same lazy-dict-plus-JSON shape as `cam_aspects.json`. `{camera: "smallest"|"small"|"medium"|"large"|"largest"}`, area shares 1 / 4 / 8 / 12 / 16, read only by `_cam_layout_weight`. Deliberately keyed by name, not by camera set: a size follows the camera into every set and every session. Absent means Medium, except `PD[1-4]M1?DF` diode arrays which mean Largest — chosen so the default weights are exactly the old hard-coded 2:1 and no existing arrangement moves. A preset's `sizes` are applied by WRITING them here, so this stays the single place any size is read from |
| `Pdxm1GridConfig` / `Pdxm1GridConfigDialog` / `_GridPreviewWidget` / `get_pdxm1_grid_config` / `_cam_type_key` / `_is_diode_cam` | the PDxM1/PDxM2 diode grid overlay; line positions are stored as absolute image fractions so each line is independent, and PD cameras of the same type share one config. `_is_diode_cam` is the gate: the grid is drawn, wired up and offered ONLY on diodes. It used to be enabled for every camera with the `show` flag alone deciding, so a flag saved on some other camera could resurrect a grid that means nothing there |
| `CamRefRectConfig` / `CamRefRectDialog` / `get_ref_rect_config` / `save_ref_rect_config` / `_ref_rect_key` (`cam_ref_rects.json`) | the permanent reference square some cameras carry. The camera software draws it on the live screen but never writes it into the archived frame, so the Slider re-draws it from a saved position — edges as fractions of the FULL frame, remapped through `_zoom_norm` in `paintEvent` so it stays on the same sensor pixels when the tile is zoomed. `_ref_rect_key` is both the gate and the store key (`_REF_RECT_CAMS`: `PCW3.*NF` → `PCW3_NF`), matching the display name `C03-081_PCW3_NF` and the archive folder `C03-081-PCW3NF-_-IMG` alike; it returns `""` for every other camera, which is what makes the feature invisible elsewhere. Unlike the diode grid it defaults to **shown** — the reference belongs on the picture the first time anyone opens the camera. Defaults measured off the camera screenshots: 0.336/0.162 → 0.848/0.668, i.e. 344,166 → 868,684 on the 1024×1024 frames the camera writes. Screen only: `SaveRangeTask` is untouched, so a saved frame stays the plain archived picture. `testing/test_ref_rect_overlay.py` renders the real `ImageView` and checks all four edges to 2 px, zoomed and not |
| `PointingPanel` | matplotlib scatter + histogram + path, click-to-jump, live replay, click/region delete with per-operation undo. The Beam-path colour bar carries a draggable time cursor: it snaps to the nearest shot, shows `HH:MM` beside the bar, moves a lime dot along the path and drives the image viewer (throttled through `_cursor_nav_timer`, one share read is ~130-160 ms). The bar's own axes are explicit (`cax`), so a multi-day analysis can draw a line at every local midnight and write the date beside each day's segment — `HH:MM` alone would be ambiguous. Cursor, readout and dot are Qt children of the canvas, so `Save Plot` writes the figure without them, while the day markers (matplotlib artists) are included. Above the canvas sits the **camera strip** (`_build_cam_header` / `set_cam_header` / `hide_cam_header`, signal `cam_step`) — `◀ «name» n/N ▶`, hidden while there is only one graph, no wrap-around at the ends, its own light band and dark text set explicitly because the strip would otherwise inherit the theme. `export_state()` + `plot(state=…)` are what let one panel hold several cameras' graphs in turn. The camera name is added as a `suptitle` **inside `save_figure` only** and removed again straight after, so the saved PNG says which camera it is without doubling the strip on screen |
| `_SCHistogramWidget` / `_SCHistogramDialog` / `_SCExclusionEditor` / `_SCExclusionCanvas` / `_SCValueLabel` / `_SCPreviewLabel` | spatial-contrast threshold, exclusion regions, preview |
| `TickBar` | time axis under the slider — ticks, A/B marks, `dd.mm` labels at midnight crossings. A multi-day pick glues its windows together and **alternates the label rows** window by window (hours above the baseline / date at the bottom, then swapped, …), so the two hours meeting at a seam never fight for the same pixels |
| `DatePickerDialog` + `_DayTimeDialog` + `_make_multiselect_calendar` | see below |
| `CameraPickerDialog` (+ `_CamLoaderSignals`, `_capture_current_layout` / `_preset_cameras` / `_preset_layout` / `_preset_sizes`, `_layout_box` / `_refresh_board` / `_seed_board_from_saved` / `_board_is_auto` / `_board_entries`) | camera selection, presets, AND the arrangement — the `Layout` box with the size buttons and `_LayoutCanvasWidget` is in the window from the moment it opens, under the picked list. It replaced a `Layout` button that opened a second modal window, which meant nobody picking cameras ever saw how they would be arranged. The board on screen IS the answer: `_on_accept` reads it directly (auto → `"auto": true` and `layout_config = None`, so the grid recomputes it from the current sizes; dragged → the fractions, stored as `auto: false`), which is why there is no longer a second result that can disagree with what is visible. Changing the picked set clears `_layout_config` / `_layout_chosen` and rebuilds the board via `set_cameras` — those rectangles belonged to another set. `showEvent` grows the window once so the board fills its widget, since the board keeps the camera area's aspect and anything left over is hatched. The camera area's SIZE and the header font come in from `Viewer._cam_area_px()` (the multi-grid while it is visible, else `_cam_row_widget`, which is valid in single-camera mode too) and the *Label size* spinbox. **`show_layout=False`** hides the whole box and restores the old 660x640 window, and `_on_accept` then touches neither the store nor `layout_config`: the Shot Finder (`sf_t._open_camera_picker`) and Pulser Monitor (`om_t._pick_cameras`) reuse this dialog and have no grid, so an arrangement there would be a promise they cannot keep — and must not overwrite the Slider's. A preset in `cam_presets.json` carries the ARRANGEMENT and the SIZES as well as the camera list — `{"cameras": [...], "auto": false, "cam_order": [...], "tiles": [[x,y,w,h], …], "sizes": {cam: class}}`, the tiles in the same shape `cam_layouts.json` uses and read through the shared `_entries_from_tiles`. **Save** captures the board plus every picked camera's size; a board still arranging itself is stored as `"auto": true` rather than frozen into fixed fractions, because those fractions belong to the area they were computed for and a preset gets opened in another one — the sizes are saved either way, being what the automatic arrangement is steered by. Loading a preset writes its sizes through `set_cam_size_class` (so the ONE place sizes are read from stays the only place) and decides the arrangement too, setting `_layout_chosen` so `_on_accept` does not fall back to the layout remembered for that camera set. Presets written before this are a bare list of names, or carry no `sizes`, and read as auto / unchanged. Right-click → *Auto-arrange cameras* clears only this window's layout and the `cam_layouts.json` entry, never the preset's own copy — re-loading the preset brings the arrangement back, which is the point of naming it |
| `_CamSizeRow` | the five size buttons — Smallest / Small / Medium / Large / Largest — acting on whichever tile was last clicked on the board, and disabled until one is. A press calls `set_cam_size_class` (written to disk at once) and re-runs the board's auto-arrange **packing in the picked list's order, exactly as the Auto-arrange button does** — left as it was, it packed in the order the board opened on, so the two buttons drew two different pictures of the same cameras at the same sizes; a HAND-MADE arrangement is deliberately left alone, since sizes only steer the automatic one and silently undoing somebody's drag would be worse than waiting for *Auto-arrange*. Styled explicitly — dark ink on light, the one in force filled blue — because the app palette leaves a themed button unreadable, and the camera-name label is elided from its own `resizeEvent` or a long name pushes the five buttons out of the dialog |
| `LazyDirModel` / `_DirItem` / `FolderPickerDialog` | lazy network folder tree |
| `CollapsibleSection` | the sidebar's collapsible sections (accent stripe + ▾/▸), state persisted. The body carries the accent's left stripe (`_shade(accent, 1.35)`) **and** a near-white wash of it (`_shade(accent, 1.93)`) so each group reads as one coloured block; the tint stops there because black control text has to stay comfortably legible on it |
| `_CamSliderRow` | per-camera master radio + slider |
| `_PvOverlayPanel` | floating, draggable PV panel. Its rows are **rich text**, one `<span>` per row (everything escaped — a PV name is archiver text, not markup), because a value past its limit has to go red while the others do not and a `QLabel` has exactly one QSS colour. `update_values(..., alarm=<row names as printed>)` states which rows are over their limits; `set_alarm_phase(0/1/2)` is the flash — 0 normal, 1 the alarm rows red, 2 the whole panel red with **every** row white (the default value colour is black, and black on red is the one combination this feature must never produce; the corner badge follows the same `_text_color_hex`, and the red fill is fully opaque because a see-through red over a bright picture is not a warning). The phase changes **text colour only**: it never touches the stylesheet (`_apply_style` resets the width high-water mark and re-sizes the panel — doing that twice a second is exactly the hopping the mark exists to prevent) and never the row set, weight or count, so a flash cannot move the panel a pixel under the cursor. Driven by `Viewer._pv_alarm_timer` / `_on_alarm_blink`. **Its own size** is `panel_w` / `panel_h` (`_apply_panel_size`, set in Overlay settings): 0 = fit the text, which is what it has always done, and a number holds that side at exactly that many pixels — the width high-water mark is skipped while `panel_w` is set, or a long value would push the panel past the size the operator laid out and `adjustSize()` would keep it there |
| `_Trip` | one thing that went wrong, and the shot it went wrong on: `kind` (`pv` / `image`), `key`, `ts_ns`, `text`, `detail`, `count`, `open`, `acked`. Two kinds in ONE list on purpose — the operator's question is the same for both ("something happened, where?") and so is the answer. `key` is what makes a repeat the SAME trip (the PV name, or `cam<i>`): live mode runs at a few shots a second, so a back reflection high for ten seconds is one entry with a count, never thirty. `ts_ns` keeps naming the FIRST offending shot, which is the one worth looking at. `head()` / `tail()` exist so the row can hold the clock time and the count **out** of the elide |
| `_TripLabel` | one trip line that elides itself and never asks the layout for more width than it is given. Both halves matter in a 275 px panel: a plain non-wrapping `QLabel` reports its full text as its minimum width, which pushed the row to 650 px and carried the **See** button clean off the side of a box that does not scroll sideways (`QSizePolicy.Ignored` horizontally fixes that), and the elide has to be redone on every `resizeEvent` or it is computed once against a width the label did not have yet. Time and count are outside the elide — eliding the whole line from the right threw the count away first, and "it has happened 39 times" is the part worth reading |
| `_TripBox` | the trip list, at the very top of the anchored Info panel (above `_range_table`), invisible until something trips. Header = `⚠ N trips · M not seen` + **Clear trips**; then one row per trip, newest first, in a scroll area. `refresh(trips)` **reuses** the rows whenever the same trips are still in the same order and only rebuilds when the list itself changes — not an optimisation: a repeating trip is refreshed on every shot, so rebuilding would destroy the **See** button under the cursor twice a second, and deleting a widget from inside its own click is how this crashes (the click is also emitted through `QTimer.singleShot(0, …)` for the case where the list *does* change). Rows are a **fixed** `_ROW_H` = 20 (the same as `PvValueTable.ROW_H`): the scroll area resizes its body to the viewport, and with rows free to shrink twenty trips were squeezed into 3 px each instead of the list scrolling. The area is `setFixedHeight(min(_MAX_H, …))`, not merely capped — a `QScrollArea`'s own size hint has nothing to do with its contents, so with only a maximum it settled at one row and hid the rest behind a scrollbar for no reason |
| `PvConfigDialog` | "Select PV channels" — the ONE PV picker, opened from the Slider (`_open_pv_config`) and from the Image Finder (`if_t._pick_energy_columns`). **One meaning per control**: a PV *being in the list* is what makes it READ (✕ is the only way out), and the **Show** column is the *eye* — the value is printed over the frame and burned into a saved image. The two tables **are** that split: *On the picture* and *Read, not on the picture*, `_rebuild_picked_tables` putting each row in one or the other. What this replaced was four states over the same PVs — a grid of preset tick boxes, per-row tick boxes for added PVs, a tick per formula, and the sidebar's eye — where "picked" had two owners that could disagree. Columns: **Show · Letter · PV · Displayed name · Unit · Min · Max · What it is · ✕**, both grids sharing `_W_*` widths so they line up as one table; only the PV column stretches. Each table is **really drawn as a table** (`_make_table` / `_cell` / `_TABLE_QSS`): a `QFrame#pvTable` for the outer border, every control in its own `QFrame#pvCell` carrying a right and bottom rule, zero grid spacing so those rules meet instead of doubling, and the column names **once at the top** — `_grid_header` is now called only for a table that has rows, because a header was previously built for the empty second table as well and, with its title hidden, that left a bare row of column names under the first table, reading as a footer of the list above it. The cells set **no background**: they take the dialog's own, so the text keeps the palette's colour — a painted white cell printed light text on white the moment the dialog came up under a dark palette and the whole PV column went invisible. Every rule is scoped by object name, or an unqualified `border` on the frame would cascade into every box and button inside it (Qt style sheets apply to children too) (the dialog is 830 wide rather than 700 because the two limit columns otherwise come straight out of that one). **Everything is painted here** (`_CB_QSS`, `_EDIT_QSS`, `_HEAD_QSS`, `_BOX_QSS`): the tick boxes — the one control that carries this dialog's whole meaning — came out of the platform as a hairline outline the colour of the panel behind them, and the limit columns' numbers in an amber that is barely there on white while the rest of the table was dark. The presets sit in a light `QFrame#pvBox` under a section caption, and the search-result list is **hidden while it is empty** and only as tall as its results otherwise (`_sync_results_visible`, `_RESULTS_MAX_H`) — empty, it was a ~130 px hole and the largest thing in the window. Rendered by `testing/render_pv_config.py`, behaviour pinned by `testing/test_pv_dialog_formula_in_table.py`. **PV** is the archiver channel because that is what the row actually reads — and on a formula row it is an editable box holding the EXPRESSION, prefixed with `=`, because that is what a formula reads. **A formula is an ordinary row of these tables**: expression in PV, name in Displayed name, unit in Unit, limits in Min/Max, eye in Show. The separate *Own formulas* block under the tables is gone (with its paragraph of syntax, which nobody was looking at while typing — it is now the tooltip `_FORMULA_TIP`, on the PV column header, on the `+ Add formula` button and on every expression box). That block was a SECOND place to edit the same row, and the row it belonged to could not edit its own name. The record behind a formula (`_derived_rows`) is therefore **plain text, not widgets** (`name` / `expr` / `unit` / `shown` / `canon`): the row is destroyed and rebuilt on every change, and a record made of its boxes would be a formula that dies with its own table. `_picked_entries` keeps a NAMELESS formula in the list (the accessors filter it out instead) or `+ Add formula` would produce no row to type in at all; typing goes through `_on_expr_typed` (record + ⚠ only — a rebuild would delete the box being typed in) and leaving a box through `_on_row_edit_finished` (`_sync_row_state` + `_refresh_letters`, still no rebuild). A rename carries the PV's alarm limits with it, since those are keyed by name. `_refresh_derived_row_state` marks a formula that cannot be computed in the box itself (pink) as well as with ⚠; **Displayed name** writes `PV_LABELS` and is display only — the PV name stays the key for selection, eye state, letters, formula bindings and the saved state, so naming a PV cannot repoint a formula or hide which channel is read; a formula has no box (its name is typed in the formula row). **Unit** is only ever filled when it can be defended: `PV_UNITS` for a preset, `cpva.CHANNEL_UNITS` when the archiver's own listing carried one (usually it does not), `pv_unit_guess` for a fixed-meaning channel suffix, or what the operator types for an added PV (`PV_CUSTOM_UNITS`) or for a formula (kept on the formula's own record) — unknown stays blank rather than guessed. The box is OPEN for exactly the two kinds whose unit nothing else can know (added PV, formula); a preset's unit is a code constant and is only shown. Presets are a strip of **tick boxes** (`_preset_boxes`, `_on_preset_toggled`): ticked *is* in the list, unticking is the same as that row's ✕, and `_sync_preset_boxes` reads the list back onto them with signals blocked (it runs from the rebuild the tick itself asked for, so an unblocked box would toggle the preset straight back off) — one state read two ways rather than the button-that-greys-out it replaced, which could only ever add. `PV_PRESET_RECIPES` makes a preset that is **not a channel** but a recipe: ticking `Compressed SBW4` runs `_add_recipe` → add the source preset (`SBW4`), add the formula `SBW4 × 0.749` under the recipe's name and unit, and take the source off the picture (`hide_source`), so only the converted number is printed while the raw channel is still read — which the formula needs. `_remove_recipe` deletes the formula and gives the source its eye back, or unticking the only thing being printed would leave a list with nothing on the picture at all. This retired the last `PV_SCALE` entry: `Compressed SBW4` used to be a `PV_DISPLAY_TO_COL` name pointing at the SBW4 channel with the 0.749 applied out of sight, so the picker showed a PV whose printed number matched no archiver reading and no calculation the operator could see; the factor is now an ordinary formula row that can be read, edited and unticked. `pv_migrate_preset_recipes` converts an older state file's selection on load (source + formula + hidden source) and is idempotent, or an existing installation would come up with that PV silently gone. `_set_shown` is what moves a source's eye: the live row's tick box has to move with the state, because every rebuild reads the boxes back first (`_sync_row_state`) and would otherwise overwrite the change with the stale tick. Anything else comes from the search box over the whole archiver listing (`cpva.fetch_channels_cached` on a worker thread → `_PvChannelListSignals`, ranked by `cpva.rank_pv_match`), or is typed in full and taken literally by Enter. A channel that a preset already reads adds **the preset**, never a second row reading the same PV under no unit. `_sync_row_state` reads every live row back before each rebuild — one method, because forgetting one of the three it used to be was how a preset toggle wiped a name typed a second earlier — and the Show box and ✕ defer their rebuild through `_later`, since a rebuild deletes the very widget whose signal is being delivered. Every row carries its **channel letter**, and the formula rows at the bottom define **derived PVs** (`PV_DERIVED`) as Python expressions in those letters. Letters are numbered over `_pending_names()` (every preset, picked or not), NOT over the two tables, so clicking an eye cannot move them; `_refresh_letters` re-renders each formula from its stored `bindings` (letter → PV name) after every add / remove / rename, and a binding whose PV was deleted is parked on a letter past the end of the list rather than left where another PV has moved in, the row warning instead of silently repointing. `_on_accept` refuses to close on a nameless, duplicate or unparsable formula. **Min / Max** are the alarm limits (`PV_LIMITS`), one `QLineEdit` each, **empty = not watched** — never a limit of zero. Formulas get them too: a ratio or a difference is exactly the sort of number somebody wants watched. A comma is accepted as the decimal mark as well as a dot (this keyboard types a comma) and `_normalise_limit_box` rewrites the box to `f"{v:g}"` on `editingFinished` so the stored value is the visible one, marking the box pink when what is in it is not a number at all — a threshold the operator believes is set but is not is the one failure this must not have. The pair is read back as a pair in `_sync_row_state`, or a half-typed Max would drop a Min that is already set. Results out: `selected_names` (canonical order = letter order), `hidden_names` (the eye), `custom_channels`, `labels`, `custom_units`, `derived_defs`, `limits`. `limits()` is deliberately **not** filtered to the picked list (the same as `labels()`): unticking a preset takes it off this tab, it does not delete it, and a limit that vanished because the *other* tab had that PV switched off would be a threshold the operator set and then silently lost — ✕ (`_remove_pv` / `_remove_channel` / `_remove_derived_row`) is what drops a limit |
| `PvValueTable` | The PV panel's list — eye · PV · value — as one widget for **both** tabs that report PV values, so two tabs over one registry cannot end up with two different tables. `refresh(names, hidden, value_of, alarm=None)`: the host supplies only the numbers, since how a value is fetched and how "this shot" is told from "an older shot" is a per-tab matter while what a row looks like is not. `alarm` (optional, so the Image Finder needs no change at all) names the PVs outside their limits on the shot on screen; they print red and bold, and red **wins over** the held-value grey — which shot a number came from is a qualifier, being past the threshold is the message. The table does not blink: a row flashing in the corner of the eye is noise, and the overlay over the picture is what has to be seen. `eye_clicked` carries the PV **by name**, not by row index. Column 0 has no title on purpose: at 24 px a word would elide to "…", i.e. the one control in the table would be its unreadable part. Its own `eventFilter` shows the value tooltips through `_show_long_tip`. `_apply_height(rows)` pins the table to `header.sizeHint() + rows*ROW_H + 2*frameWidth` on **both** sides, not just the maximum: a `QTableWidget` is Expanding with a `minimumSizeHint` of about two rows, so it is the one widget in the 275 px panel that a layout short of room can steal height from — collapsing **Timeline & Range** made the panel's content fit its viewport, the scrollbar went away, and the shortfall came out of this table (106 px → 82 px, four picked PVs down to two readable rows, 16.09.2026). A fixed height cannot be squeezed, so the room comes from the panel's own scrollbar instead. Pinned by `testing/test_pv_table_height.py`, measured by `testing/probe_pv_table_squeeze.py` |
| `PV_REGISTRY_PATH` · `pv_registry_load/save/to_dict/from_dict` | The registry — added PVs, formulas, names, units, **alarm limits** (`pv_limits`, written as `{"min": …, "max": …}` so a hand-edited file can set one side without spelling a null; a missing key leaves the limits alone, so an older registry keeps loading, and `_coerce_limit` refuses anything that is not a finite number rather than reading it as zero) — lives in `%APPDATA%/ELI_ImageTools/pv_registry.json`, NOT in either tab's own state file: the picker is opened from two tabs and whichever saved last would otherwise decide whether a PV the other one added still exists tomorrow. What stays per tab is only the SELECTION (`pv_enabled` + `pv_hidden` in `slider_ui_state.json`, `pv_selected` + `pv_hidden` in `finder_ui_state.json`). `pv_registry_load(fallback=…)` reads the Slider's old `pv_custom` / `pv_derived` / `pv_labels` keys when there is no registry file yet, so an existing installation keeps its PVs on the first run of this version. `pv_registry_from_dict` is where every entry is validated — a formula whose name clashes with a read PV, a label for a PV nothing on screen can edit, a unit left behind by a removed channel: each of those reads as "the programme shows the wrong number", never as a bad config file |
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
| overlays | `_on_reset_zoom`, `_toggle_draw_mode`, `_refresh_draw_btns`, `_remove_all_overlays`, `_open_overlay_settings` / `_apply_overlay_settings`, `calibrate_circle/cross/square` (→ `_calibrate_shape`), `_sync_overlay_checkboxes_from_iv`, `_on_overlay_changed`, `_overlay_target_indices` / `_overlay_targets`, `_on_overlay_edited` / `_copy_overlay_shape`, `_draw_mode_of_targets`. **Overlays act on the SELECTED cameras — all of them when none is selected** — the same rule the display controls follow (`_disp_targets`), and they no longer follow the last-clicked tile. Arming a Draw button arms that mode on every targeted tile, so one click marks them all: the tile the user drew on emits `ImageView.overlay_edited`, `MultiCameraGrid` re-emits it with the camera index, and `_on_overlay_edited` copies **only the shape that moved** onto the other targets — dragging the cross must not disturb their circles. Geometry is normalized to the image rect, so the middle of one tile is the middle of every frame whatever each camera's resolution is. A **Cal** button runs the detection once per targeted camera, each on ITS OWN frame (one click, one result per camera), and the ones that failed are named together in a single dialog instead of one dialog per camera. `_draw_mode_of_targets` reads the armed mode off the targets rather than `selected_img_view()`, because unselecting a tile leaves it "last clicked" while the controls point elsewhere; `_on_multicam_selected` re-aims the armed mode at the new selection (through `_sync_overlay_checkboxes_from_iv`, which it used to duplicate inline). **A camera carries SEVERAL marks of each kind** — see **Overlay marks** — so `_copy_overlay_shape` mirrors the WHOLE list of one kind and `_on_overlay_edited` therefore carries adding, moving and deleting alike, through the one signal there is. `_remove_all_overlays` goes through `_overlay_targets()`: it used to iterate `selected_cam_indices()` alone, so with nothing selected the only bulk-clear there is did nothing. **Cal ADDS** its result rather than replacing what is there, so a measured mark can sit beside marks placed by hand; the detectors append nothing when they fail, so a failed Cal leaves no empty mark. Pinned by `testing/test_multicam_overlays.py` and `testing/test_overlay_marks_multi.py` |
| PV | `_open_pv_config` (→ `PvConfigDialog`), `_pv_rebuild_table` (→ `PvValueTable.refresh`, the host supplying `_pv_row_value`), `_pv_visible_names`, `_pv_toggle_eye`, `_pv_trigger_fetch(_now)`, `_pv_current_ts`, `_pv_is_pending`, `_pv_force_refresh`, `_pv_on_result`, `_pv_update_overlay`, `_open_pv_overlay_settings` (font, font size, **panel size w × h**, opacity, colours and the out-of-limits style; every change previews on both overlays at once and Cancel puts the lot back), `_pv_save_overlay_style` / `_pv_load_overlay_style` (the whole look is persisted under `pv_overlay_style` — only the alarm style used to be, so a size and a font the operator had set were gone at the next start), `_pv_text`. The table lists **every** selected PV and its first column is an **eye** (`_pv_hidden`): it removes that PV from the on-image overlay and from the burn-in, never from the reading — a hidden PV keeps its number in the table and may still be a formula's source. `_pv_trigger_fetch_now` asks the archiver for `pv_source_names(...)`, i.e. the ticked PVs **plus** every formula's inputs, and evaluates `pv_eval_derived` on the worker thread so the derived values reach `_pv_values` as ordinary names (held-value / stale handling then applies to them unchanged). The **selection** is persisted in `slider_ui_state.json` (`pv_enabled` + `pv_hidden`) and the **registry** in the shared `pv_registry.json` (see `pv_registry_save`) — the selection used to live only in memory, so every restart came up reading no PV at all until the user re-opened the dialog, and the registry used to live in this tab's file, where the Image Finder could not add to it. `_pv_current_ts` returns `None` rather than falling back to another camera's moment, and `_reset_ui_for_new_scan` clears `_pv_values` (greying the previous dataset's numbers still read as this one's). Fan-out is capped at `PV_FETCH_MAX_WORKERS` = 4 so the panel cannot exhaust the shared cpva pool. **The panel waits for the archiver** (`_pv_awaiting`, `_pv_arm_wait_retry`, `_pv_held_age_s`, `_pv_head_behind_s`): a value the archiver has not published yet is `pending`, not `n/a`, and the panel re-asks until it lands — every 400 ms for the first `PV_ARCHIVER_FINE_WAIT_S` = 2.5 s, then doubling to 2 s, giving up after `PV_ARCHIVER_MAX_WAIT_S` = 20 s of **monotonic** waiting. The ladder used to double from the first retry, straight through the archiver's ~1 s publication delay: measured with `testing/bench_pv_latency.py` (which, unlike the other PV harnesses, does NOT invalidate the day cache when it publishes, so the panel has to find the sample by itself the way it does in the field), the sample became readable 80 ms after the 400 ms retry and the next rung was 800 ms further on, so the number appeared **~730 ms after the archiver had it, on every shot**. Holding the short interval while the answer is genuinely expected takes that to **16 ms** (p50, 2.5 s cadence) for +0.6 requests per shot. Two further details make it work: the retry goes through `_pv_retry_fetch_now`, **not** the paced `_pv_trigger_fetch` (a re-ask for a frame already being waited on is not a new refresh, and the pacing gate pushed the 400 ms retry out to the next 500 ms slot), and that fetch passes `fresh=True` down to `_pv_last_known_ex`, which drops today's cache TTL — this fetch exists BECAUSE a sample is missing, so a cached "not there yet" answers nothing. The short interval is deliberately NOT used for the `behind` case, where `_pv_pick_fetch_ts` has stepped back to an already-published frame: those numbers are real and labelled, there is nothing to catch, and above ~1 shot/s that state is permanent — asking every 400 ms through a whole run would raise the panel's own request rate for no visible gain — so the numbers for the frame on screen arrive on their own. Before this, a fetch was only ever triggered by a frame change, and since it fires on the *leading* edge (a few ms after the image appears, ~1 s before its sample is readable) the panel kept the previous shot's numbers and nothing ever asked again: with one frame per shot it stayed **one shot behind the picture**. A held number now also says how far back it comes from (`12.95 J (-28 s)`, not a quiet `(old)`), and the overlay badge distinguishes `no data yet` (the archiver has not published this frame) from `older shot` (this number was read for an earlier frame) and `⟳` (our own refresh is in flight). The markers share **one line along the bottom edge**, joined by `·` and elided (never wrapped) when the panel is narrow, and hovering the panel explains every lit marker (`_BADGE_HELP` / `_badge_tip`). They used to be stacked in a strip down the right-hand side, reserved at the width of `no data yet` whether or not anything was lit, which left a third of the overlay empty beside every number; height is what this panel has to spare, not width. The reservation itself stays constant (`_badge_strip_h`, plus `_badge_min_w` so one marker always fits), because that is what stops a lit marker resizing the panel. Those explanations — and the value column's tooltips — go through `_show_long_tip`, because Qt hides its own tooltip after ~10 s and then refuses to show it again until the pointer has left the widget and come back, i.e. it disappears mid-sentence on exactly the text that needs reading. **Above ~1 shot/s** the frame on screen is *always* younger than the publication delay, so `_pv_pick_fetch_ts` deliberately aims the fetch at the newest frame the archiver has published — the frame at or before `cpva.head_ts_ns` (never newer: that would be matched against a neighbour's sample) and at most `PV_RETARGET_MAX_BACK_S` = 3 s back, so a value from a quiet stretch is never dragged in. The numbers then skip a shot and say so (`(-0.6 s)`, tooltip naming that frame's time) while the images still show every frame; the retry keeps running and snaps onto the displayed frame the moment its own sample lands. `_pv_is_held` therefore treats "read for a different frame" as its primary test, and `_pv_is_pending` (⟳) is measured against the fetch TARGET, not the screen, so a deliberate offset does not light it. Nothing changes away from the live edge — scrubbing, archive browsing and the burn-in still resolve every shot exactly. Pinned by `testing/bench_pv_wait.py`. **A paint is a trigger of its own** (`_note_cam_shown`): in multi-cam `_pv_current_ts` answers from `_cam_shown_ts_ns`, the frame actually *painted* on the master tile, while every other trigger fires at *request* time — a live frame triggers its fetch ~150 ms before the share read that paints it finishes, so that fetch still described the **previous** frame and nothing asked again (the retry compares against the same request-time frame and sees nothing wrong). The panel therefore sat one shot behind in live multi-cam even with everything above in place; `testing/bench_pv_live_multi.py` measured it (shot 2 reading shot 1's energy) and now pins the fix. `_pv_cam_index` is the one place that decides which camera the panel describes, so the fetch and the paint that re-triggers it can never disagree. **The panel heals itself** (`_pv_health_tick`, every `PV_HEALTH_TICK_MS`): single-flight means one fetch that never returns freezes the values for the rest of the session (reported as "the PV overlay stopped loading, a restart fixed it"), so a fetch still in flight after `PV_FETCH_WATCHDOG_S` = 120 s is written off — its generation goes into `_pv_abandoned_gen` so a late result can never come back and put an old frame's numbers under the current picture — and a fresh one starts; and because every normal refresh rides on a frame change or a paint, `PV_KEEPALIVE_S` = 60 s re-asks on its own when nothing has triggered one. `_diag_log` records both (`pvFetch` / `pvLastVal` / `pvStall`). Pinned by `testing/test_pv_resilience.py`. **ALARM LIMITS AND TRIPS** (`PV_LIMITS`, `_pv_check_limits`, `_Trip` / `_TripBox`, `_goto_trip`): a value past the Min/Max set for it in the picker flashes over the picture and leaves a line in the trip list naming the shot it happened on. **LIVE MODE ONLY** — `_pv_check_limits` and `_note_cam_fault_trip` both return early unless `_online_mode`, and `_stop_online_mode` calls `_end_live_trips` (stop flashing, close what was open, keep the lines). This is the scope of the feature, not a limitation to be lifted: it watches the shots as they arrive, and browsing is not shooting. Scrubbing a day would otherwise raise a trip for every frame the slider landed on, dated by when the operator dragged past it — and it *still* would not answer "was BR ever over 0.3 today", because only the frames actually looked at are ever read. Answering that is a different feature: a sweep of the day's archived samples, not a check of the frame on screen. Camera faults have a second reason of their own — outside live mode the poll timer is stopped, so `_live_health`'s stall test would call every camera faulted the moment browsing started. Closing the open trips on the way out is what stops a new live run counting its shots onto a trip dated in the previous one. The fetch already had the number and threw it away — the panel is handed finished strings (units, held-value flags, quantised steps) and re-reading a threshold out of one of those would compare a number to a label — so `_pv_trigger_fetch_now` now emits the pair `(text, numbers)` through the one signal it has (one producer, one consumer; a second signal for the numbers could arrive out of step with the text it belongs to), and only `ok`/`approx` readings go into `numbers`. `_pv_check_limits` runs **before** `_pv_rebuild_table` / `_pv_update_overlay`, or every trip would show one refresh late. Only a real reading for THIS shot can trip: `v is None or _pv_is_held(name)` skips, and skipping is *not* recovery either — a missing reading is no evidence in either direction, so an open trip is left exactly as it is. Comparison is the raw threshold, no deadband; **coalescing lives in `_trip_add`, not in the comparison** — `_pv_over` holds the currently-offending PVs so a trip opens on the OK→over CROSSING only, counts further shots, and closes on the way back; at 3 Hz anything else would be a trip per shot. Two independent flash lifetimes (`_alarm_blink_steps`): `_pv_alarm_names` is refilled by every fetch, so the VALUE stops flashing by itself when the next shot lands (what the operator asked for), while the PANEL step is offered only under the `text+bg` style and lasts while `_trips_unseen()`, i.e. past the shot, until every trip has been looked at or **Clear trips**. `_alarm_sync_timer` runs `_pv_alarm_timer` (`TRIP_BLINK_MS` = 450, `PreciseTimer` — a default Qt timer is coarse on Windows and a drifting flash reads as a stutter) only while there is something to blink. Image faults come off the SAME detector as the red refresh dot — `_note_cam_fault_trip` on the not-fault→fault transition inside `_on_cam_dot_blink` (and inside `_on_online_blink` for single-cam, which has no tile dots) — so `_live_health`'s 8 s latch is what keeps a frame caught mid-write out of the list; the row carries a short phrase per reason (`_CAM_FAULT_SHORT`) because the tooltip's own first line elided to "a new image arrived but…" in 275 px, and only a `read` fault has a real count (failed frames) since counting 600 ms ticks would print `×340` for one event lasting three minutes. **Only a NUMBER trips.** A PV the archiver refuses ("ERR") used to open a trip of its own after 15 s (`_pv_check_read_errors` / `PV_ERROR_TRIP_S`); both are gone. A trip is the record of the beam going out of limits, and a failed read is no statement about the beam — the value row and the overlay's corner badge are already where a read failure is reported, so the trip list stayed full of channels that had merely stopped publishing while nothing had actually tripped. `_goto_trip` switches live mode off FIRST (the next arriving frame would drag the slider straight back off the shot), drops the cached tile and the coalescing gate for an image trip (`_invalidate_cam_ckeys` + `_reset_cam_pipeline`, so the frame is read from the share again rather than re-served), reuses `_jump_to_saved_ts`, and re-reads the values for that moment with `_pv_force_refresh`. **No new retry loop was added** — `_on_cam_loaded` and `_cam_load_watchdog` already relaunch failed and stuck loads, and the ask was for the program to keep going rather than fight a bad frame. `_open_pv_config` clears the alarm state as well as the values, or an "over" flag from the old threshold would either keep flashing at a limit nobody set or swallow the first crossing of the new one. `_diag_log` adds `trips=<listed>/<open> pvOver=<n>`, and each trip writes its own `diag_note` line when it opens. **A CHANNEL ARCHIVED MORE SLOWLY THAN THE FRAMES** (`_pv_wide_window_ns`, status `"far"`, `_pv_sample_ts`, `pv_offset_label`, `_pv_sample_offset_s`, `PV_NEAR_MAX_GAP_S` = 60 s): measured 16.09.2026 15:26–15:33 (`testing/probe_pv_match_window.py`, `probe_pv_sample_gaps.py`), the camera stored 1200 frames at 3.3 Hz while PTM1/SBW4/Back_Ref were written once every 5.01 s — 93 % of frames had nothing inside `_PV_WINDOW_NS` and read `n/a` with the only candidate 2 s away, and PTM1's own rate over that day ran from 1.15/s overnight to 0.01/s at midday. When the strict lookup comes back `not_found`, `_pv_last_known_ex` therefore re-asks with **half the channel's own median sample gap** around that frame (measured over `_PV_CADENCE_SPAN` samples each side, from `peek_day` — cache only, no second network round trip) capped at `PV_NEAR_MAX_GAP_S`, and returns the hit as `"far"` together with **the sample's own timestamp**, which is the third element `_pv_last_known_ex` now returns. That timestamp is what the label is computed from (`pv_offset_label`, one formatter for the table, the overlay badge and `pv_text_for_ts`'s burn-in, signed because the nearest reading is as often after the frame as before it), and it is archiver-clock against archiver-clock, so the workstation's 25 s skew cannot reach it. The widening **switches itself off** when the channel catches up: at 3.3 Hz half the median gap is 0.15 s, below the strict window, so nothing is widened and a frame in a hole in the archive still reads `n/a` rather than a neighbouring shot's number — that is the case the fixed ±0.3 s exists for, and it is what the user asked to stay ready for ("někdy budou přibývat i energie rychlostí 3,3hz"). A **setting** reports no sample timestamp at all (`is_step` in `_pv_last_known_ex`): its value between two changes is what the machine was set to, not an old reading, and labelling the waveplate `(-40 min)` would say the opposite. `"far"` values are excluded from `nums`, so a reading from another moment can never raise a trip. `n/a` itself finally explains itself (`_pv_no_sample_reason`, `_pv_nearest_archived`): the tooltip names how far the nearest reading is, which is what tells "nothing was archived here" from "the program is not reading". Pinned by `testing/test_pv_na_reason.py`. **WHICH FRAME A READING BELONGS TO** (`_pv_own_window_ns`, passed into `_pv_last_known_ex(own_window_ns=…)` and into `pv_text_for_ts(frame_ts_list=…)`, and reused for the label by `_pv_claim_window_ns`): the ±0.3 s window assumed the camera stamps its frame at the instant of the reading. It does not, and each camera is off by its own constant — measured 16.09.2026 15:00-16:00, all storing one frame every 5.00 s against PTM1 (`testing/probe_cam_frame_offset.py`): PCM4NF -0.164 s (spread 0.12) paired 92 %, PCM2NF -0.366 s (0.11) paired **5 %**, PCW3NF -0.512 s (0.18) paired **0 %**, PTM9NF -0.393 s (0.32) paired **20 %**. Same shots, same channel, same rate. A reading is now claimed by the frame it is NEAREST to — half the gap to that camera's neighbouring frame, floored at `_PV_WINDOW_NS` and capped at `PV_NEAR_MAX_GAP_S`, which cannot hand one reading to two frames and needs no per-camera calibration; `testing/probe_cam_rates.py` prints the before/after per camera (every 5 s camera now 92-93 % on PTM1, 99 % on SBW4). It tightens by itself: at 3.3 Hz half the gap is 0.15 s, below the floor, so a neighbour's reading can never be claimed. A lone frame falls back to the floor rather than the cap — one frame is no evidence about a camera's rate. Pinned by `testing/test_pv_frame_pairing.py`. **SPEED** (17.09.2026): the archiver's publication delay was re-measured at ≈0 (`testing/probe_publish_delay.py`, p50 -0.10 s on PTM1, -0.15 s on the chiller; the 2026-08-14 figure of ~0.9 s is gone), so `PV_REFRESH_MIN_INTERVAL_S` 0.5 → **0.25** and `PV_ARCHIVER_RETRY_MS_MIN` 400 → **120**: picture → number p50 **351 ms → 234 ms** at the same 2.7 requests per shot, measured by `testing/bench_pv_latency.py`, which now reports `picture -> number` alongside `publish -> shown` because the operator's question is whether the number is there when the picture is. The cost is a 3.3 Hz live run going from ~4 to ~8 fetches/s (`bench_pv_live_multi.py` prints it). The live edge itself was left alone by the user's decision: the picture is never held back waiting for its numbers |
| live health | `_live_health`, `_live_health_summary`, `_reset_live_health`, `_note_cam_frames`, `_note_cam_read_fail` / `_ok`, `_note_cam_folder_status`, `_set_lag_since`, `_poll_hung_age`, `_paint_single` |
| multi-cam | `_is_multi_cam`, `_switch_to_multi/single_view`, `_build_per_cam_sliders`, `_on_per_cam_*`, `_per_cam_display_one`, `_per_cam_row_frame`, `_start_cam_load`, `_reset_cam_pipeline`, `_cam_load_watchdog`, `_per_cam_sync_slaves`, `_per_cam_redraw_in_place`, `_live_slave_target`, `_live_sync_one_slave`, `_cam_off_master`, `_cam_toggle_ts`, `_per_cam_step`, `_live_advance_cam`, `_setup_multi_cam`, `_on_multicam_selected`, `_redraw_cam_in_place` |
| live mode | `_on_auto_follow_toggled`, `_ensure_dir_watcher` / `_watcher_strike` / `_stop_dir_watchers` / `_prune_dir_watchers` / `_on_dir_watch_new_file`, `_start/_stop_online_mode`, `_online_poll`, `_online_poll_single_bg` / `_multi`, `_merge_single_new_items`, `_merge_items_by_ts`, `_extend_axis_to_items`, `_restore_full_history` / `_merge_restored_history`, `_rebuild_shared_items_from_cams`, `_extend_shared_timeline_from_cams`, `_on_online_blink` |
| open / scan | `open_folder`, `_start_multi_cam_scan`, `_on_multi_scan_all_done`, `open_by_date`, `_reload_with_last_cameras`, `auto_start_online`, `open_moment` (the Image Finder's `Send moment` / `Send + cameras` hand-over, One Moment's before the merge: a window of `PUSHED_MOMENT_PAD_MIN` around a pushed timestamp, landing ON it via `_pending_restore_ts_ns`), `open_folder_path`, `receive_external_folder`, `open_file_list`, `refresh_folder`, `_refresh_multi_cam`, `_start_scan`, `cancel_scan`, `_on_scan_*`, `_choose_axis`, `_in_ts_windows` / `_filter_to_ts_windows`, `_hard_reset_runtime`, `_reset_ui_for_new_scan` |
| brightness / subtract | `_bc`, `_refresh_auto_bc_sliders`, `_sync_bc_value_labels` / `_set_gamma_label` (the "Con:/Bri:/Gam:" rows' numeric readouts — driven off the sliders, and called from the Auto parking too, which blocks signals), `_bc_value_label` / `_bc_value_set_auto` (those readouts' two states: black = a setting the user made, grey italic = a value Auto measured, and in multi-cam the MASTER camera's alone — switched from `_sync_bc_controls_enabled`), `_on_brightness_slider_changed`, `_on_contrast_slider_changed`, `_on_contrast_auto_changed`, `_on_bright_auto_changed`, `_apply_brightness_debounced`, `_load_raw_arr`, `_ref_arr_for` / `_cam_ref_arr_for`, `_set_reference_frame` / `_remove_reference_frame` (the Set ref / Remove ref pair — both act on the cameras selected at the moment of the click, with the same "none selected → all of them?" question), `_has_reference`, `_update_ref_buttons` (Remove ref is live only while there is a reference, and never wider than Set ref, whose own enabling belongs to the bulk grey-out lists), `_set_ref_status` / `_refresh_ref_warning`, `_on_subtract_changed`, `_sub_hold_on` / `_apply_sub_hold` / `_capture_sub_auto_hold` / `_clear_sub_auto_hold` (Auto held for a subtraction run — see the render section), `_update_diff_stats` / `_update_cam_diff_stats`, `_on_gradient_changed` |
| per-tile display | `_disp_ui_snapshot` / `_disp_snapshot`, `_cam_disp_reset` / `_cam_disp_get`, `_disp_targets`, `_apply_disp_to_targets`, `_disp_target_records`, `_disp_diff_flags`, `_refresh_disp_diff_marks`, `_load_disp_from_targets`, `_bc_value_set_mixed`.<br>**Palette, the three Con/Bri/Gam rows and all three Auto boxes are stored PER TILE** in `_cam_disp`. Three rules: a control acts on the SELECTED tiles (all of them when none is selected); it acts at the moment it is MOVED and nowhere else; and the panel READS BACK the other way — click a camera and the rows show that camera's own settings, click back and the earlier ones are there again. Selecting never writes to a tile. A selection whose tiles disagree marks each differing row's readout with ≠ and puts up `lbl_disp_mixed` naming them, because one tile's number is not the others'; moving the control then sets them all the same. The manual slider values live in the record's `ui` blob rather than being read back out of `bc` — while an Auto box is on, `bc` holds a zero for that row and the slider is parked on Auto's measurement. Pinned by `testing/test_bc_autos_and_per_tile.py` |
| slider ↔ time | `_slider_to_time_ns`, `_slider_to_index`, `_index_to_slider_value`, `_time_to_slider_value`, `_time_to_nearest_index`, `_set_info_for` |
| display / load | `_on_slider_pressed/changed/released`, `_apply_scrub`, `_load_or_cache`, `_adaptive_index_step`, `_display_index`, `_display_exact_index`, `_display_multicam_at_time` / `_index`, `_on_cam_loaded`, `_request_display_target`, `_drain_deferred_display`, `_request_pixmap`, `_prefetch_idle` / `_playish`, `_on_loaded` |
| playback | `play`, `stop`, `_autoplay_step`, `_play_show`, `step_frame`, `_hold_step_begin`, `_hold_step_tick`, `_hold_step_end`, `keyPressEvent`, `_current_play_pct_per_s`, `_play_is_exact`, `_update_motion_speed`, `_adaptive_stride`, `_current_decode_side` |
| focus / watcher | `_toggle_focus_mode` (F11 — hides everything but the image, title bar removed via Win32 so the HWND survives), `_toggle_watcher_mode` (Ctrl+F11 — edge-to-edge wall display), `_win32_set_title_bar`, `eventFilter` |
| focus-mode window shape | `_focus_mask_rects` / `_focus_apply_mask` / `_iv_content_rect` / `_focus_edge_rect`. Focus mode must show the cameras and *nothing* else, but the tiles never fill their area exactly, so the window used to be a large grey rectangle with the frames somewhere inside it — covering the program underneath. `setMask` of the union of the camera cards (header band + the frame actually drawn, per `ImageView._img_rect`) plus any floating PV overlay solves both halves at once: on Windows the masked-away pixels are neither painted nor hit-tested, so the gaps are see-through *and* clicks land on the window behind. Re-applied on a 200 ms timer because the frame rect follows the layout, the label bar and the image aspect and there is no one signal for all three; identical regions are skipped so nothing flickers. Since the window's real edges can be masked off (a click there never arrives), the resize hot zone is taken from the mask's bounding rect — `_focus_edge_rect` |
| timestamps | `_save_current_timestamp`, `_goto_saved_timestamp`, `_clear_timestamps` |
| pointing | `_pointing_target_cams`, `run_pointing_analysis`, `_start_next_pointing_cam`, `_pointing_cam_prefix`, `_cancel_pointing`, `_on_pointing_progress/cancelled/finished`, `_finish_pointing_run`, `_step_pointing_set`, `_show_pointing_set`, `_update_pointing_status`, `_on_pointing_live_toggled`, `_start/_stop_pointing_replay`, `_pointing_replay_step`, `_save_pointing_plot`, `_toggle_pointing_path`, `_toggle_pointing_select`, `_on_pointing_region_deleted`, `_restore_pointing_points`, `_on_pointing_point_clicked`.<br>**One run, several cameras.** The tiles selected in the layout decide the scope, or all of them when nothing is selected (`_disp_targets`' rule). `_pointing_queue` holds the cameras still to do and they run strictly one at a time — the task already fans out over 16 threads, four at once would only fight over the share. Each finished camera becomes an entry in `_pointing_sets` (`cx/cy/ts/ts_int/img_w/img_h/n` + a `state` blob); `_show_pointing_set` swaps them into the single `PointingPanel`, saving the outgoing camera's `export_state()` first so deletions, zoom and Show Path survive the round trip. Cancelling keeps the cameras already finished. A camera with no frames in the A/B range is dropped and **named** in the status line, never silently |
| spatial contrast | `_sc_set_enabled`, `_on_sc_threshold_changed`, `_run_sc_auto_threshold`, `_open_sc_histogram`, `_open_sc_exclusion_editor`, `_on_sc_exclusion_set`, `_run_spatial_contrast`, `_on_sc_preview` / `_update_sc_preview`, `_on_sc_finished`, `_on_sc_topn_changed`, `_on_sc_marker_style_changed`, `_update_sc_topn_overlay` |
| marks / save | `set_mark_a/b`, `clear_marks`, `_apply_marks_to_tickbar`, `_update_range_ui`, `save_around_current`, `save_current_with_overlay`, `_get_cam_frame_for_save`, `_render_cam_frame`, `_save_multicam_current`, `_send_to_workshop`, `_send_to_image_finder` (the `➤ Image Finder` button beside it in Source: hands `_current_view_ts_ns()` and `_cam_names` to `if_t.ImageFinderWidget.open_moments` — a MOMENT, so a position scrubbed between two frames is a valid send; enabled alongside `btn_send_workshop` in all four loader paths), `save_current`, `_save_multicam_range`, `save_range`, `_show_save_range_progress_dialog`, `_on_save_progress/finished`, `_pv_text_for_ts` / `_pv_prefetch_texts` / `_pv_save_append_bar` |

**Palettes:** index 0 = Default (original colours), 1 = Grayscale, 2+ = LUT.
**Saving:** Default → `shutil.copy2`; anything else → `load_image_scaled` + save;
always `_copy_metadata_into_png`.
---

## om_t.py — One Moment (MERGED AND DELETED, 2026-09-04)

`om_t.py` no longer exists. Everything One Moment did lives in the **Image
Finder** (`if_t.py`); its module docstring — the design spec, not a comment — was
folded into `if_t.py`'s own header, which is now the place to read WHY these parts
behave as they do.

Where each half went:

| One Moment | Now |
|---|---|
| the PV graph, one axis per unit, nothing normalised | `PVRegionSearchDialog._redraw` / `_unit_key_for` / `_pv_meta_for`, opened from the Finder's `PV Search` button |
| hold-forward drawing of a setting written on change | `PVRegionSearchDialog._hold_xy` + `_seeds`, seeded from `cpva.value_at_or_before` in the loader's second pass |
| click a moment, snapped onto a real sample | `_set_moment_from_x` → `_snap_ns`; every click ADDS one (`self._moments`), Ctrl+Z takes the last back. A click is a press that did NOT move — `_is_drag` / `_PV_CLICK_SLOP_PX = 2`; any real drag is a range |
| the formula-over-time engine | module level in `if_t.py`: `derived_plan`, `_merge_base_ts`, `_hold_index`, `_hold_limit_ns`, `_align_source`, `_break_gaps`, `_eval_vector`, `_eval_loop`, `_spot_parity`, `build_derived_series`; a formula sits on the PV list under a `derived:` key |
| range statistics with the held `n = 0` row | `_fill_stat_row` / `_stat_cells` / `_held_cells` / `_stats_tip` / `_last_before` — **every** marked range at once (2026-09-07), and since 11.09.2026 in the RIGHT-HAND COLUMNS OF THE PICKS TABLE rather than a page of their own (`_build_stats` / `_refresh_stats` / `_stat_table` are gone, and so is the tab bar that switched between them). One row per plotted PV, the pick's own six cells merged down the block via `setSpan`. There is no picker either: the drop-down (`_stats_cb`, `_refresh_stats_combo`) went in 2026-09-07, because one range at a time is exactly what stops two being compared. The table shares the right-hand pane with the graph through a `QSplitter` |
| the four right-click menus | NOT ported (operator's decision, 2026-09-04). The right button is the zoom; everything the menus offered is on the control strip under the graph — grid, legend, log Y, `Y range`, `Time range`, `Ticks`, `Whole day`, `Copy`, `Save` — and the per-PV settings (`Own axis`, `Edit`) sit under the PV list |
| `Own axis`, per PV | `_own_axis` (a set of channels) → `_unit_key_for` → the group ordering in `_redraw`. **The shared groups keep the main axis and the own-axis PVs take the twins** (`_group_rank`, 2026-09-07): the groups used to be drawn in PV-list order, so `Own axis` on the FIRST PV handed the left-hand axis to that one PV and moved every other curve onto a new one — which is what made a per-PV setting look like a switch for the whole graph. The rows that carry it wear an amber band (`_refresh_own_axis_marks`) and say so in their tooltip, since the state of one button is not a record when the selection moves |
| `Edit`, per PV | `_edit_selected_pv` — WHICH CHANNEL the row reads (`Search` opens `_PVBrowseDialog`, the shared archiver-wide ranker), plus the name on screen and the unit. A row is no longer welded to the channel it was seeded with (2026-09-07). Name and unit go to `is_t.PV_LABELS` / `is_t.PV_CUSTOM_UNITS` when the channel is on the shared list; otherwise the unit lands in `_local_unit` (read by `_pv_meta_for`, so it still decides the axis) and the dialog says it is kept for this window only. Re-pointing a row moves its own-axis flag and drops its colour, unit cache and alias note |
| prev / next shot stepping | the Finder's OWN panel (`_step_shot`, `_sync_shot_steps`), walking `_shot_stamps` — the primary PV's samples, kept when the window closed, so following a stretch of the day never reopens it |
| the saved-moments list, session-only | the Finder's panel (`_refresh_moment_list`, `_save_current_moment`, `_forget_saved_moment`, `_clear_saved_moments`), cap `_SAVED_MOMENTS_MAX`, never written to any state file |
| the background prefetch | `_start_moment_prefetch`, the newest `_MOMENT_PREFETCH_KEEP` moments resolved quietly, and only after what was asked for is on the wall |
| the two caches | `_res_cache_get` / `_res_cache_put` per (camera, moment) with the ten-minute negative-cache guard, over one shared `sf_t.DayScanCache` |
| `Send moment` / `Send + cameras` | `_send_moment_to_slider(with_cameras)`, still through `is_t.Viewer.open_moment` |
| the way back in | `open_moments` (see "Handoff IN"), the route the Slider's and Shot Finder's `➤ Image Finder` buttons take |
| the tiles | the Finder's wall (`_DayWall`), which is what the operator wanted kept |

`main.py` no longer loads it, there is no "One Moment" tab, and
`testing/test_one_moment.py` is gone — its coverage lives in
`testing/test_finder_moment.py`, `test_pv_graph.py`, `test_pv_stats_and_formula.py`,
`test_pv_own_axis_and_edit.py` and `test_saved_moments.py`.
`testing/bench_one_moment_frames.py` still exists and now benches the resolver in
`if_t.py`. `testing/render_pv_search.py` draws the whole window (five PVs, three
marked ranges, one PV on its own axis) and `testing/render_pv_edit.py` drives the
`Edit PV` dialog through a channel change — both need a real platform
(`QT_QPA_PLATFORM=windows`), because offscreen has no fonts.

## wk_t.py — Workshop

**The one rule of this tab: display settings never change pixels.** Brightness,
contrast, gamma, the display window and the palette live in `_ViewSettings` and are
re-applied by `render_view` on every render, so any of them can be undone by moving the
control back. Only the Filters and Edit sections (crop, rotate, flip, resize, arbitrary
rotation, straighten, bin, median / blur / sharpen / edges / background removal,
reference subtraction) touch `base`, and every one of those is undoable. Saving writes a
NEW file and refuses a target equal to `source_path`.

**The second rule: a filter changes the counts the same way it changes the picture.**
`_apply_to_both(fn, message)` runs one operation over `base` AND `raw`, or drops `raw`
with a note. Filtering only what is on screen would leave the Measure panel reporting
numbers from a picture nobody is looking at.

### Three parts in one file
| Block | Holds | Uses Qt |
|---|---|---|
| PIXEL OPERATIONS | filters, background maps, projections, red/green merge, arbitrary rotation (+ its point map), binning, animation and 16-bit data TIFF writers | no |
| BEAM MEASUREMENTS | FWHM / 1/e² widths, Gaussian fit, D4σ and principal axes, radial profile, encircled energy | no |
| the tab | canvas, panel, dialogs, undo, hand-off | yes |

The first two blocks are **deliberately Qt-free**, which is what makes them checkable
against a synthetic Gaussian: `beam_stats` on a known σ returns D4σ within 0.3 % and the
tilt within 0.1°, and `rotate_point_map` puts an annotation within 0.1 px of where
Pillow actually moved the pixel.

The panel calls them through the names `wk_ops` and `wk_beam`, which are both bound to
this module (`sys.modules[__name__]`). That is not decoration: it says at every call site
which group a function belongs to, and it means the two blocks can be lifted back out
into sibling files — the shape they were written in — without touching a single caller.
The `_OPS_ERROR` / `_BEAM_ERROR` strings and the `wk_ops is not None` guards are the
remains of that split and are what `_update_enabled` greys buttons out on, so they stay.

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
    px_per_mm   # always set: 1000 / px_um. What every length divides by
    px_um       # how much one pixel covers, in µm — the number the operator sets
                # (starts at BASLER_PIXEL_UM = 4.4, remembered in the UI state file)
    px_um_measured             # True when a ruler produced px_um, for the wording
    zoom / offset / fitted     # per-slot view position
    undo_stack / redo_stack    # capped by _SLOT_UNDO_LIMIT = 30
```
**The scale is never off.** Every slot is born with a pixel size (`_apply_default_sensor_scale`,
called from `receive_image` and `_add_derived_slot`), and every length is reported in
pixels AND in real size side by side — ruler caption, results table, beam report. That is
what removed the old on/off tick: with both numbers always present, a scale can only add
information, while a switch adds a way to read the wrong number. `_set_pixel_size` is the
one way in, so `px_um` and `px_per_mm` cannot drift apart and every change lands in the
undo history. `_set_scale` (ruler of known length) computes `px_um = mm * 1000 / px` and
sets `px_um_measured`, so the Scale line can say "measured with a ruler" rather than
"camera pixel size" — 4.4 µm is true at the SENSOR, and any lens in front changes what a
pixel covers on the object. Binning and resizing scale `px_per_mm` by `(sx+sy)/2` and
`px_um` by its inverse: joining pixels makes each one cover more.

`BASLER_PIXEL_UM` is 4.4, not the 4.5 the older MATLAB analysis scripts use. The frames
carry the number themselves: NI Vision writes `Unit length` and a `Measurements` JSON
block (`Sensor Calibration` → `Pixel Width (um)`) into every archived PNG, and both say
4.4 — the acA1600-20gm pitch. Two things the default still does NOT know, both readable
from that same metadata if it is ever wired up: **camera binning** (`Picture Size` → `X`/
`Y Binning`; OM1NF acquires 2×2, so one stored pixel covers 8.8 µm and a frame measured
at 4.4 comes out half size), and the **optics in front of the sensor**, which is what
would turn a size on the chip into the beam size in the experiment. NI's own `ROI Area`
gets the binning wrong too, so it is no help as a cross-check.

`_state()` shares array references instead of copying: `base` and `raw` are never
modified in place, every edit builds a new array, so 30 history steps cost nothing.
The state is `(base, raw, annots, px_per_mm, full_scale, px_um, px_um_measured)` — `full_scale` is in there
because binning with "Sum" multiplies it, and an undo that put the pixels back but left
the scale four times too big made every "% of full scale" and the histogram axis wrong
with nothing on screen to show why.

`_RawLoadTask` (a `QRunnable`) re-opens `source_path` in the background and reads the
native array plus `img_scale.full_scale_for_pil` / `camera_from_path`. This is why the
hand-off contract `receive_image(arr, label, source_path)` did **not** have to change
for is_t / if_t / sf_t: the senders keep handing over 8-bit, and the Workshop fetches
the counts itself. `receive_image` also accepts `raw=`, `full_scale=`, `camera=` for a
sender that wants to pass them directly. Statistics say which of the two they came from
(`unit_name()` → `UNIT_RAW` "pixel intensity" / `UNIT_CODE` "8-bit intensity") — never
silently. `unit_key()` is the same distinction without spaces, for CSV column names.

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
CROP / EYEDROP / ERASER / RULER / ANGLE / ROI_RECT / ROI_ELLIPSE / PROFILE / CROSS`

**Angle** is clicked out in three presses (arm end → corner → arm end) with the shape
following the cursor in between, the same in-progress-object pattern as the polygon;
right-click or Escape throws a half-finished one away, and `set_tool` does too rather
than inventing an angle from two points. `angle_between` always reports the inner
angle — a protractor reading of 250° is the same corner measured the long way round.

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
`180°` and `Resize` keep plain text — a third turning arrow beside Undo and Original
would be one too many to tell apart.

The recipes are the whole program's icon vocabulary, not the Workshop's: `if_t`'s
`_set_action_icon` borrows them through `_get_workshop_module()`. Two families draw the
same three shapes on purpose. `mark_circle` / `mark_square` / `mark_cross` put the shape
inside a **picture frame** — those buttons lay a reference mark on the frames, and the
frame is half of what they mean. `shape_circle` / `shape_square` / `shape_cross` are the
**bare shape**, matching the ✚ ◯ ◻ the Image Slider's own Draw buttons show, and they are
what the Image Finder's wall uses (07.09.2026 — asked for, so the two tabs read alike).

### Overlay marks (is_t)
`MARK_KINDS = ("cross", "circle", "square")` and `ImageView.marks: dict[kind, list]` — a
camera carries **as many marks of each kind as the operator draws**, not one. Geometry is a
plain tuple in fractions of the image rect: cross `(x, y)`, circle `(cx, cy, rx, ry)`, square
`(l, t, r, b)`. **Never a QPointF and never a mutable record**: marks are copied between
cameras, into `_overlay_store` and into `SaveRangeTask` on a worker thread, and one shared
mutable element would let a dragged handle move marks on seven other tiles. `circle_r_norm` is
gone — it was always `max(rx, ry)` and every reader of it was a dead fallback branch.

`ImageView._sel = (kind, index)` is the mark the operator picked; it is **local UI state that
never travels**, because the index does not name the same mark on two cameras whose lists have
diverged. It is cleared by `set_draw_mode`, `_restore_iv_overlay`, `set_marks`, `clear_marks`,
a successful delete and `focusOutEvent` — that last one because Delete only reaches the view
holding the keyboard, so a highlight left lit on a tile the operator walked away from promises
something the key will not do. Arming a mode selects the LAST mark of that kind, so a camera
carrying one shape behaves exactly as it always did: its handles are there to grab with no
click to select first.

**Mouse.** Ctrl means "make a new one here anyway"; without it, landing on a mark picks it up.
Hit-testing (`mark_hit`) is proximity to the OUTLINE, never "inside the shape" — a click
anywhere inside a big circle counting as a hit would make a second mark inside the first
impossible to draw. `_hit_mark` searches from the top down, so the mark drawn last is the one
the operator sees up there. Handles are drawn, and answer, **only on the selected mark**: with
eight circles armed, handles on all of them would be forty blobs over the picture. A press with
no movement in circle/square mode still leaves a mark, of `_NEW_MARK_FRAC` size — the rule
"a click leaves a mark" is the same for all three shapes, and a click alone cannot say how big.

**Delete** lives in `ImageView.keyPressEvent`, gated on `_draw_mode == _sel[0]`, and emits
`overlay_edited` exactly like a drag: the grid re-emits, `_on_overlay_edited` runs, and the
other targeted cameras are handed the whole shorter list. Every other key goes to
`super().keyPressEvent`, or the arrow keys would stop stepping frames the moment a picture was
clicked (the tile is `StrongFocus` and `Viewer.keyPressEvent` owns the arrows, F11 and Escape).

**One drawing routine.** `draw_marks(painter, rect, marks, style, pen_min_w=…, draw_mode=…,
selected=…, handle_radius=…)` serves the screen (`rect` = `_img_rect()`) and every saved
picture (`rect` = `QRect(0, 0, w, h)`, `pen_min_w = w // 500`), so a saved copy is what was on
screen; `selected` is screen-only and every save passes None. It replaced four hand-written
copies — `_paint_body`, `SaveRangeTask._draw_overlay_on_pixmap`, `save_current_with_overlay`,
`_render_cam_frame` and `save_current` — each of which had to be edited in step or one export
quietly disagreed with the screen. `overlay_params_from_view` replaced the two hand-built
parameter dicts and carries **copies** of the lists, because that dict is handed to a worker
thread while the operator can go on drawing.

**A ticked box with nothing drawn is not a mark.** `has_any_mark` counts marks, and is the one
test used everywhere. The four save paths used to ask only whether a tick box was on, which was
survivable while a ticked Cross box drew a cross in the middle of the picture by itself. That
phantom is gone (the operator asked for it: only placed marks are drawn), and with it the loose
test would have written an `_annotated.png` identical to the original plus a second copy of the
original beside it.

**Selection is shown with its own contrast**, never a colour: a black casing under the mark's
own colour, white dashes over it, and white-filled black-bordered grab points. No single colour
survives both a blown-out beam core and a black field. The cross is the exception to the dashes
— its arms are a few pixels long and a dash line down the middle leaves nothing of the colour
that says which kind of mark it is — so it gets a dashed box around it instead, which is also
its hit-test area made visible. Judged by rendering (`testing/render_overlay_marks.py` →
`testing/overlay_marks.png`, dark frame and bright frame, one of each kind selected), not by
reasoning.

Nothing is persisted: `_overlay_store` is in-memory only and `slider_ui_state.json` has no
overlay key. Marks normalize to the **displayed** rect, so one placed while zoomed in is stored
against the crop — pre-existing, unchanged, and the reason only the fixed PCW3_NF rectangle
remaps for zoom. Three neighbours with similar names are untouched: `CamRefRectConfig`
(PCW3_NF's permanent square), `Pdxm1GridConfig` (the diode measuring grid) and the Image
Finder's own `_ThumbView` marks, which still hold one of each kind.

### Annotations
`_Annot(kind, pts, colour, width, filled, text, font_size, label, value)` — geometry in image
coordinates, painted at draw time by the shared `paint_annots(painter, annots, to_widget,
scale)`. Save calls the same function with an identity mapping, so a saved copy is what
was on screen. Being geometry is what makes them selectable, movable and resizable after
the fact (`_BOX_KINDS` get eight handles, `_SEG_KINDS` two endpoints), keeps them crisp
at 6400 %, and keeps the drawing colour out of the palette LUT. The Eraser deletes an
item rather than restoring pixels. Geometry edits (rotate / flip / resize / crop / bin /
arbitrary rotation) transform the annotation coordinates too, so nothing detaches from
the feature it marks. `value` is the one numeric payload a shape may carry — only the
scale bar uses it so far — and it is a field rather than a subclass so that undo, the
session file and `copy()` need no special case. `to_dict` / `from_dict` are the session
form.

**Shift while dragging** squares a box (`_BOX_KINDS`, in `set_handle`) and straightens a
segment (`_SEG_KINDS`, via `snap_direction` at `_SNAP_STEP_DEG = 45°`). While drawing it
snaps against the fixed first point; while dragging an end point of a finished line it
snaps against the OTHER end, so an existing ruler can be squared up without redrawing.
The directions are the picture's own — the thing actually wanted is a line parallel to
the BEAM, which needs the beam's edge found first (the second-moment long axis is already
there in `wk_beam.beam_stats`, and the edges of a top-hat could be fitted); those angles
would be offered from the same helper.

**Captions carry everything.** `_region_caption` writes three lines — values (min / max /
mean / sd), the region's size in pixels AND real units, then pixel count, area, sum and
centre — and `_draw_caption` grows its plate per line. A tooltip promising "min, max, mean
and more" over a caption showing two of them is what this replaced. Same rule for the
ruler: `"511.0 px  =  2.3 mm"`, never one or the other.

**Scale bar** (`A_SCALEBAR`) is an annotation, not a display setting, which is what
gets it burn-in, undo and dragging for free. Its `value` is the length in **millimetres**
and `update_labels` recomputes its pixel length from `px_per_mm` on every refresh, so
changing the scale moves the bar instead of leaving a bar that lies. `set_handle` ignores
it — a calibrated bar must not be resizable by mouse. It is drawn on a dark plate like
the measurement captions: a bar that vanishes into a saturated spot is worse than none,
because the reader still believes the picture is calibrated.

### Measurement
`region_stats` (min / max / mean / std / sum / count + moment centroid),
`_region_mask` (rectangle or ellipse), `line_profile(arr, p0, p1, width)`. All measured
on `measure_arr()`, i.e. **before** the view transform. `_HistogramWidget` and
`_PlotWidget` are painted by hand on purpose — a matplotlib toolbar would have to go
through the Finder's icon-tinting workaround, and neither of these needs one.

`line_profile`'s `width` averages that many pixels ACROSS the line, as ImageJ's line
width does: on a noisy frame a one-pixel profile is mostly noise, and averaging 9 rows
cuts that by three without moving the peak, so the FWHM read off it stops jumping.

**Several regions.** `WorkshopCanvas.keep_regions` (the "Keep several regions" box)
decides whether `add_annot` purges the previous measuring region. Off — the default —
because the panel's cells show exactly one region and two on screen under one set of
numbers is a trap. On, the cells follow the selection and `_measure_table` is where they
are all read.

**Tables.** One `_TableDialog` (headers + rows + Copy + Save CSV, all colours stated
explicitly so the header row cannot come out grey-on-grey) serves three things:
`_measure_table` (one row per region / ruler / angle / point / profile, plus the whole
image on the first row so there is always something to compare against),
`_show_histogram_numbers` (the same 256 buckets and the same source as the histogram
picture, so the two cannot disagree) and `_show_beam_report`. `_write_csv` is the single
CSV writer — through the `csv` module, because a camera name contains commas.

### Beam measurements
`beam_stats(arr, mask, baseline_pct)` returns centre of mass, D4σ across / down, D4σ on
the principal axes with the tilt and the roundness, and FWHM / 1/e² widths read off the
row and column through the centre of mass. `radial_profile` and `encircled_energy` plot
into `_CurveDialog`; `profile_metrics` and `gaussian_fit` feed the line-profile window,
which draws the half-maximum and 1/e² levels, marks both crossings, and overlays the fit
with its r² so "is this spot Gaussian" is answered rather than assumed.

**A baseline is subtracted before any moment is taken, and the value used is reported
with the result.** Every second-moment width weights a pixel by its value times its
distance squared, so a background of a few counts spread over a whole frame outweighs the
spot and D4σ comes out as roughly the frame size. The panel's "Background" spinbox is
that percentile (5 % by default; 0 % measures the values as they are). A width without
its baseline is not a measurement.

### Keyboard shortcuts
`_shortcut_table()` is the single source: group, key, what it does, what to call, and the
button whose tooltip should name the key. `_install_shortcuts` builds the `QShortcut`s
from it, `_annotate_shortcut_tooltips` writes the key into that button's tooltip, and
`_shortcut_groups` / `_show_shortcuts` (F1, or the `Shortcuts` button) list them in a
window. Nothing is spelled out twice, so a new key cannot appear in one place only.

Two decisions worth keeping:
- **Window scope, switched off while hidden.** The keys are `WindowShortcut` and
  `showEvent` / `hideEvent` enable and disable them, so they work right after clicking
  the tab — when the focus still sits on the tab bar, outside this widget — yet never
  fire from another tab, and never *swallow* a key there either (a disabled shortcut
  passes the key on; a `WindowShortcut` guarded only inside its callback would eat
  Ctrl+Z from every text field in the window). The two `setShortcut` calls on the Undo
  and Redo buttons were exactly that bug and are gone.
- **Tool letters live in `WorkshopCanvas.keyPressEvent`, not in a shortcut.** Shortcuts
  are matched before `keyPressEvent`, so `H E L P` as window shortcuts would steal the
  letters from a text annotation being typed. Inside `keyPressEvent` the `_text_active`
  branch returns first, and `_TOOL_KEYS` is only consulted afterwards; the canvas then
  emits `tool_requested` so the strip's button follows.

### WorkshopWidget layout
Tool strip — three rows: the tool buttons, then colour + Line/Brush sliders with their
numbers and `_StrokePreview` samples + text size with its `_TextPreview` sample + Fill,
then the zoom slider with its
percentage + ＋ − Fit 1:1 + undo/redo/clear/original/shortcuts. Line and Brush are
`_line_sl` / `_brush_sl`; `_refresh_stroke_samples` is the one place that repaints the
numbers and all three samples, and the eyedropper (`_on_color_picked`) has to call it too
because it changes the colour without going through `_on_style_changed`. Above the
`_StrokePreview` range the little sample has to squeeze its stroke, so each slider also
owns a `_StrokeZoomPopup` (`_line_zoom` / `_brush_zoom`) — a `Qt.ToolTip` window big
enough to draw the stroke at true thickness, shown on `sliderPressed` and hidden on
`sliderReleased`. `_refresh_stroke_samples` keeps a visible popup in step with the drag
(`isSliderDown`) and gives an arrow-key change a 1.2 s self-hiding one (`hasFocus`),
and `hideEvent` drops all three — a separate window would otherwise outlive the tab.

**The text sample, and why it is measured differently.** Text had a number and nothing
else for a long time; 16 and 40 read the same on a spin box. `_TextPreview` (`_text_prev`)
draws `_TEXT_SAMPLE` in `_TEXT_FONT` — the same font `paint_annots` uses for a text item
— and `_TextZoomPopup` (`_text_zoom`) is its true-size window. Three traps, each one a
bug that was shipped in the first attempt:
* **Measure the ink, not the line height.** `_text_ink` uses
  `QFontMetrics.tightBoundingRect`, not `height()`: the font's line box is a third
  taller than the letters, and using it threw away most of the true-size range of a
  28 px box. `tightBoundingRect` is measured FROM THE BASELINE, so the baseline has to
  be put back (`-r.x()`, `-r.y()`) to centre the letters.
* **Clamping to the box is not squeezing.** `min(1, room/height)` makes everything from
  16 up draw at exactly the box height, so 20, 28 and 60 came out identical. `fit()`
  keeps sizes up to `_TRUE_UP_TO` (20 px of ink) at true size and log-squeezes the rest
  into the pixels left over, the same way `_StrokePreview` does — and the squeezed range
  starts a whole pixel ABOVE the cap, or the first size past it rounds back onto the cap.
* **A spin box has no press or release.** There is no drag to follow, so the big sample
  is shown for 1.2 s on every change of the number — and only when the number itself
  changed (`_text_last`), or picking a colour would pop it up as well.
All three samples are one box (`_SAMPLE_BOX`) so the row reads as one set of controls.

The zoom
slider is spaced by ratio (`_zoom_pct_to_slider` / `_zoom_slider_to_pct`, 1000 steps
over 2 %-6400 %), so a linear handle gives an even feel across the whole range;
`_on_zoom_changed` leaves the handle alone while it is held down, and
`_on_zoom_slider` puts it back only when the canvas refused the zoom (no picture
loaded). Over a
`QSplitter` of a collapsible panel and the canvas, with a status line underneath showing
the cursor position, its value in counts and % of full scale, the image size and the
zoom. Sections and accents: Images `#2f6fd0`, Display `#7a4fc0`, Measure `#b0396b`,
Beam `#0f7f8f`, Filters `#4d7a2a`, Edit `#2e9e5b`, Combine `#5a5f8f`,
Compare `#d08a1e`, Save `#c0392b`; expanded state persists in
`%APPDATA%/ELI_ImageTools/workshop_ui_state.json`.

Key methods: `receive_image`, `open_files` (also drag-and-drop), `_add_derived_slot`
(for an image the Workshop MADE — no source file to read counts back from, so
deliberately not `receive_image`), `_duplicate_slot`, `_paste_clipboard`,
`_activate_slot`, `_on_view_control` / `_apply_view_now` / `_sync_view_controls` (one
guarded place that parks every Auto value on its control), `_refresh_measure`,
`_measure_table`, `_set_scale` / `_add_scale_bar`, `_show_profile`, `_beam_stats` and the
three beam windows, `_apply_to_both` + the `_filter_*` methods, `_rotate` / `_flip` /
`_resize_dialog` / `_rotate_arbitrary` / `_straighten` / `_bin_dialog` / `_do_diff`,
`_do_project` / `_do_merge`, `_update_compare`, `_render_for_save` / `_save` /
`_save_data_tiff` / `_save_all` / `_save_animation` / `_copy_clipboard`,
`_save_session` / `_load_session`, `_canvas_menu`, `_shortcut_table` /
`_install_shortcuts` / `_show_shortcuts`, `_after_change` (the
single place that re-reads everything derived from the active slot, so no button is left
stale) and `_update_enabled` (the single place that decides what is greyed out).

**Two TIFF buttons, and both are needed.** "Save TIFF" writes what is on SCREEN — the
display stretch, the palette and the drawing baked into 8-bit RGB, which is what a report
wants. "Save the values as TIFF" writes what was MEASURED, as a plain single-channel
16-bit file with no palette and no annotations, which is what ImageJ or a script wants.
Before this existed, TIFF was the screen version only, so anyone saving a TIFF to keep
the data silently kept 8 bits of it.

**"Save with overlay" is the one gate for every picture that leaves.** `_cb_burn` is read
in exactly one place, `_render_for_save`, which every export goes through — `_save`,
`_save_all`, `_copy_clipboard`, `_animation_frames`. Ticked, `paint_annots` is run over
the rendered view at scale 1.0, so the file carries the drawing AND the captions the
measuring tools generated (`_Annot.label`); unticked, the file is `render_view(base,
view)` and nothing else. Three things the switch needs beyond the flag itself, all
because a silent switch is a switch nobody finds: it is mirrored as a checkable entry in
`_canvas_menu` (the Save section is collapsible, so the panel can hide it completely),
`_overlay_note` puts which way it went into every status line, and `_on_burn_toggled`
persists it as `save_overlay` in the UI state. The `_save_data_tiff` path never had a
choice to make — measured values carry no drawing by definition — and neither does a
comparison view, which is a blend of two images with no annotations of its own.

**Writing runs off the GUI thread.** `_WriteTask` / `_start_write(fn, busy_message)` take
a callable holding plain arrays and paths — nothing Qt owns crosses the thread — and
`_on_write_done` reports back. Save all and the animation export both go through it; they
used to encode on the GUI thread and the window simply stopped responding.

**Right-click menu.** `WorkshopCanvas.contextMenuEvent` calls back into
`_canvas_menu(global_pos, image_point)`, which builds the menu fresh each time so what is
greyed out is right for that moment, and includes the value under the cursor. It stands
down under Magnify and while a polygon or an angle is being clicked out, because
right-click already means "zoom out" and "finish the shape" there.

**Session file** (`_save_session` / `_load_session`) stores the drawing, the regions, the
scale, the display settings and where each image came from — not the pixels. That keeps it
tiny and means it can never disagree with the archive; the cost is that an image handed
over without a file behind it cannot come back, and both the count of those and any file
that has changed size since are reported rather than quietly mis-placed.

**Back to the Slider.** `_show_in_slider` opens the source frame's FOLDER through
`is_t.receive_external_folder` (the same public handoff the Finder and Shot Finder use, so
the Slider clears its online mode, its reference frame and its multi-camera grid first).
The Workshop cannot push edited pixels back — the Slider browses files on the share.
`main.py` wires `workshop._slider_ref` / `_slider_tab_idx` / `_tab_widget`.

---

## Dependencies
- `PySide6` — widgets, signals, threading
- `numpy`, `Pillow` — image processing
- `matplotlib` — PointingPanel, SC histogram (always via `_make_mpl_toolbar`)
- `scipy` — SC hole filling; Workshop filters and morphology (`wk_ops`) and the Gaussian
  fit (`wk_beam`). Optional everywhere: each of those has a numpy fallback, so a build
  without scipy loses accuracy and speed, not features.
- `zoneinfo` — Prague timezone
- `ssl`, `urllib` / `http.client` — CPVA archiver (no certificate verification)

---

## KNOWN ISSUES

### Functional
- **wk_t** — a Compare view is read-only: the picture on screen is a composition of two
  slots, so drawing and measuring are refused while one is shown. Every pixel operation
  in the panel now refuses too (`_editing_blocked`); they used to go ahead invisibly,
  leaving the next measurement to be taken off a frame nobody had looked at.
- **wk_t** — the "Rolling ball" background is a morphological opening plus a smooth, not
  ImageJ's shrink-and-roll, so its numbers will not match ImageJ's to the count.
- **wk_t** — an arbitrary rotation fills the corners it grows into with black, and those
  are real pixels: leave them out of a region or they drag the mean down. The status line
  says so after every such rotation.

### Efficiency / responsiveness
- Network IO and PNG encoding still run on the main thread in some if_t / sf_t
  save / try-again / open-in-slider paths → the UI freezes on a slow share.
- No cached directory listing: `if_t.load_folders` and `sf_t._load_cameras` rescan
  all 24 hour folders. (`if_t` no longer rescans on a change of HOUR — `_on_hour_change`
  does not reload, a search starts only from View — and `_on_gradient_changed` no longer
  throws the listing cache away, which used to cost a share re-read for a palette change.)
- `if_t.load_folders` / `_scan_hour` still duplicate `is_t.cameras_for_windows`, which
  does the same walk better (union over every hour folder of every window, plus search
  and presets in `CameraPickerDialog`). Replacing it is the next cleanup here.
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
- is_t: `_LazyDirModel` stub (`pass`). The overlay-draw code that used to be copied per
  view is gone — the screen and all four save paths go through `draw_marks` (see
  **Overlay marks**).
- sf_t: `_setup_calendar` duplicates the calendar factory; legacy hidden widgets.
- Frequent `except Exception: pass` hides real errors (notably `ScanTask.run`,
  which wraps the whole scan).
