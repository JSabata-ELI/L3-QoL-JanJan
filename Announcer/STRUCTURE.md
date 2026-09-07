# Announcer — STRUCTURE

> Verified against source: 2026-09-02 · `a.py` 3718 L

Single-file tkinter app. Three jobs in one window:

1. **Conditions** — a saved list of things that must stay true, shown in a
   three-page `ttk.Notebook` (Screen areas / Values / Halls) over **one** list.
   A `screen` condition compares its own rectangle to its own reference every
   500 ms; a `pv` condition compares an archived value to its own limits and, if
   an area is attached to it, that picture too; a `hall` condition compares the
   beam fate and the PSS state to what the operator declared. The first one to
   fail raises the alarm and says, in the user's own words, what needs doing.
2. **The ad-hoc region watch** — the original single unnamed rectangle from
   "Set reference", watched alongside the conditions.
3. **PV alerts** — poll eight fixed machine values every 500 ms and show a
   coloured badge for each one that is out of range. These never raise the alarm.

User-facing documentation: `ReadMe Announcer.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Announcer_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `a.py` | The whole program. Run with `python a.py`. |
| `presets.json` | Saved regions, window geometries, PV thresholds, flash settings and the **conditions** (including their reference pictures). |
| `images/scorpion_orig.png`, `images/viper.png` | Alarm images. Read from `images/` **next to the exe** at runtime. |
| `sounds/chime.wav`, `pluck.wav`, `pop.wav` | Selectable alert sounds. |
| `scorpion.png` | Loose copy in the folder root; the app reads `images/` only. |
| `build_config.json` | Dev Tools build settings. `extra_files` lists `images` and `sounds` as **folders**. |
| `icon.ico` | Window / taskbar icon, via `set_app_icon`. |
| `testing/` | Tests and benches, none of which touch the real `presets.json`. |

### `testing/`

| File | What it proves |
|------|----------------|
| `test_hall_logic.py` | `_hall_verdict` over every combination, the moving grace either side of its limit, and `_fetch_latest` widening against a stubbed fetch. No window, no network. |
| `test_conditions_load.py` | The old `screen` / `pv` shapes still load unchanged, the new `gate_*` and `hall` keys load, a corrupt area is dropped without taking its condition with it, and nothing private is written back. |
| `bench_hall_live.py` | Against the real archiver: a matching PSS state is quiet, the wrong one fires and names both states, and an unreadable beam fate never fires but does say so. |
| `bench_value_area.py` | A value condition with an area attached: the picture half fires on its own, an unchanged picture does not, and the value half still fires with an area present. |
| `shot_tabs.py`, `shot_editors.py` | Screenshot the three tabs and the three editors. The only way to see whether a tab label came out legible. |

Three traps the harnesses were written around, worth knowing before writing a
fourth:

- **Pump with `mainloop()`, not a loop of `update()`.** Results come back from
  the worker thread through `after()`, and under `update()` those do not
  arrive — the Halls read-out sits on "reading…" forever and looks like a bug
  in the program. `shot_tabs.py` and both benches drive themselves from
  `after()` callbacks inside a real loop.
- **`ImageGrab` takes absolute screen coordinates and works out the
  virtual-desktop offset itself.** Do not subtract it by hand: this machine's
  virtual desktop starts at y = -174 and "correcting" for that moved every shot
  174 px down. A shot that comes back as a photograph or as solid black is the
  **lock screen** — a locked session has no window to photograph.
- The saved `window_geometry` puts the window back where it was last left,
  which can be a monitor that is currently asleep; a grab of that one is black.
  Both harnesses force the position with `_apply_geometry` first.

`images/` and `sounds/` must ship with every build: a build without `images/` can
only flash a plain colour, because the alarm image is read from next to the exe.
The Builder now also picks those folder names up automatically, but the entry in
`build_config.json` documents the requirement.

**An alarm image is used as an alpha stencil, never as a picture.** `_image_mask`
takes the file's alpha channel, and `_make_flash_photo` paints the opaque pixels
in the flash colour and everything else in the chroma key — which is what makes
the alarm both see-through and click-through around the shape. A file with no
alpha channel (plain line art on white, as `announcer viper.png` arrived) is one
solid rectangle. Such artwork has to be converted first: alpha = the inverse of
the greyscale, hard-thresholded at 128, then cropped to `getbbox()` so the shape
fills the window. `images/viper.png` is that conversion of the delivered artwork.

---

## Constants

| Symbol | Value | Meaning |
|--------|-------|---------|
| `POLL_INTERVAL_MS` | 500 | Screen check interval. |
| `CHANGE_THRESHOLD` | 2 | Default average pixel deviation (0–255) that counts as a change. |
| `FLASH_DURATION_MS` | 3000 | How long the alarm blinks. |
| `FLASH_INTERVAL_MS` | 300 | Blink speed. |
| `FLASH_OFF_ALPHA` | 0.02 | Window opacity in the "off" half of a blink — not 0, because a fully transparent window stops receiving clicks. |
| `PV_AVG_COUNT` | 25 | How many recent samples each PV **badge** reading averages. |
| `PV_POLL_MS` | 500 | PV read interval. |
| `COND_PV_WINDOW_S` | 10 | How far back a `pv` **condition** looks for its worst sample. |
| `COND_MAX_REF_BYTES` | 1 000 000 | Cap on a stored reference picture — it lives inside `presets.json`. |
| `COND_DEFAULT_PV` | `L3-PM03-023:Energy` | Pre-filled in a new value condition: the back-reflection energy. |
| `HALL_POLL_MS` | 2000 | Hall read interval — the two hall values change hours apart. |
| `HALL_LOOKBACK_S` | 1 h, 1 d, 7 d, 30 d | Widening windows `_fetch_latest` tries in turn. |
| `HALL_MOVING_GRACE_S` | 60 | Default seconds the switchyard may be on the move. |
| `HALL_MOVING_VALUE` | 0 | The beam fate that means "on its way", not a destination. |
| `_RESERVED_PRESET_KEYS` | | Top-level keys in `presets.json` that are **not** presets: `pv_thresholds`, `window_geometry`, `image_geometry`, `flash_mode`, `image_file`, `color_cycle`, `flash_interval`, `conditions`. Anything else at the top level is a preset name. |

### The monitored values — `_PV_MONITORS`

Each entry is `(channel, label, lo_orange, lo_red, hi_orange, hi_red, unit)`.
`channel` is either a PV name, or a `(minuend, subtrahend)` pair whose
**difference** is what gets monitored.

| Label | Channel | Orange | Red | Unit |
|-------|---------|--------|-----|------|
| Helium volume | `L3-UTIL-HEB03-001:PressOut_PSI` | < 46 / > 57 | < 45.5 / > 60 | PSI |
| Alpha voltage | `HAPLS-VOLT_IN_CGL-SEEDER_ER3_ALPHA1:SeederPZTVoltage` | < 1.2 / > 1.9 | < 1.1 / > 2.2 | V |
| Chiller DA1–DA4, Helium Chiller, Utility chiller | `L3-UTIL-CHL03-00n:Temp` **minus** `:TempSP` | ±0.3 | ±0.6 | °C |

The six chiller rows are generated in a loop from `_CHILLER_LABELS`, so adding a
chiller means adding a label, not a table row.

### `_PV_ABS_RANGE` — the absolute check

Index-aligned with `_PV_MONITORS`. For the chillers it holds an absolute
temperature window (7–17.5 °C for DA1–DA4 and the Helium chiller, 18–22 °C for the
Utility chiller); `None` means no absolute check, which is the case for the Helium
volume and the Alpha voltage.

A breach of the absolute window paints the badge **purple** and **outranks** the
deviation thresholds: a chiller holding its setpoint perfectly at the wrong
temperature is still wrong. The purple badge shows the raw temperature, not the
deviation.

Badge colours: purple `#7a1fa0` (absolute breach), red `#cc2200`, orange
`#cc6600`, no badge when in range.

### Where the beam goes — `HALL_FATE_PV`, `HALL_PSS_PV`

| PV | Values |
|----|--------|
| `L3BT-MSS:Beam_fate` | 0 switchyard moving · 1 E2 · 2 E3 · 3 E4 · 4 E5 ELI-LUIS · 5 E5 ELI-MAIA |
| `L3-PSS:STATE_EXH_EXTERNAL_HIGH_P` | 0 internal only · 1 into the experiment |

Both are `enum` channels the archiver writes **on change only**, hours apart, so
the 60 s window every other reader uses comes back empty nearly always. That is
what `_fetch_latest` exists for.

**`L3BT-MSS:Beam_fate` is not archived** (checked 2026-09-01: the whole
`L3BT-MSS` prefix is absent, and no channel anywhere carries "fate" in its
name). Live Channel Access is not reachable from these machines either. So the
beam-fate half reads as unavailable and can never trip; the PSS half works. Do
not "fix" this in the code — it is a missing channel, not a bug. The operator
says the value will arrive from a different API.

---

## Conditions

Stored as one top-level `conditions` list in `presets.json`. Keys beginning with
an underscore are runtime-only and are stripped by `_save_conditions`.

```json
{"kind": "screen", "name": "L3BT alignment check", "enabled": true,
 "monitor": 1, "region": [2919, 166, 3288, 349], "threshold": 2.0,
 "message": "Alignment check changed — look at the HP panel",
 "reference": "<base64 PNG>"}

{"kind": "pv", "name": "Back reflection", "enabled": true,
 "pv": "L3-PM03-023:Energy", "warn": 1.0, "trip": 2.0, "unit": "mJ",
 "message": "Back reflection energy over the limit",
 "gate_monitor": 1, "gate_region": [x1, y1, x2, y2],
 "gate_threshold": 2.0, "gate_reference": "<base64 PNG>"}

{"kind": "hall", "name": "Shooting into E4", "enabled": true,
 "message": "The beam is not going where you set it",
 "hall": 3, "pss": 1, "moving_grace_s": 60}
```

- **`_ref_img`** — the decoded reference, a PIL image, made once by
  `_load_conditions` / `_encode_reference`. **`_uid`** — an in-memory number that
  ties a condition to its badge widget; not saved, because editing a condition
  replaces the dict.
- **The reference lives inside `presets.json`, base64 PNG.** One file, one config,
  and — more importantly — a reference held only in RAM would have to be re-taken
  at every start, which would silently accept a screen that is *already* in the
  bad state as normal. `COND_MAX_REF_BYTES` refuses a whole-screen reference with
  a message instead of writing a megabyte of base64.
- **`warn` / `trip` are `None` when unset**, written as `null`, shown as an empty
  box. Empty is not zero: a zero limit on an energy fires the moment the laser
  runs. `_parse_level` is the one place that turns a box into a number or `None`.
- **A `pv` condition is judged on the peak of the last `COND_PV_WINDOW_S`
  seconds**, not on an average — one shot over the limit is the whole event. The
  archiver writes only on change, so `_condition_value` falls back to the newest
  sample of the whole 60 s window: that is the value the machine is still
  holding. Nothing at all means no judgement, and `None` never trips.
- **A screen condition whose grab no longer matches the reference size fails.**
  Resolution or scaling changed under it; comparing is impossible and reporting
  "fine" would be the one wrong answer.
- **Failure is one-shot**, exactly like the ad-hoc region: `_raise_alarm` stops
  watching, so nothing re-alarms every 500 ms.
- **The `gate_*` keys are optional and mirror a screen condition** on the same
  dict — `_SCREEN_AREA_KEYS` and `_GATE_AREA_KEYS` name the two sets, which is
  what lets `_build_area_block` build both editors. A value condition carrying
  them holds while *(value under trip)* **and** *(picture matches)*; either half
  failing trips, and the message says which. The two halves are checked on
  different clocks — the picture in `_poll` on the UI thread every 500 ms, the
  value in `_poll_pvs` on a worker — and that is deliberate: no synchronisation
  is needed when each can raise the alarm by itself.
- **A `gate_region` that will not parse is dropped whole**, picture and all, so
  the next save does not carry the wreckage forward. The value half survives.
- **A `hall` condition's `hall` / `pss` are `None` when that half is off.**
  Zero is a real beam fate (switchyard moving) and a real PSS state (internal
  only), so "unset" cannot be spelled 0; `_parse_level` gives the same
  empty-is-not-zero treatment it gives warn/trip, and the dropdowns say
  `_NO_CHECK` rather than showing a number.
- **`_hall_verdict` is module level and pure.** It takes the condition and the
  two readings and returns one of `ok` / `warn` / `unknown` / `trip` with a
  sentence — no window, no network, so `testing/test_hall_logic.py` can walk
  every combination.
- **A hall value that could not be read is `unknown`, never `trip`** — and never
  silent either: the badge goes orange and names which of the two is missing.
  Reporting "fine" on the strength of a reading that was never made is the one
  wrong answer, and today the beam fate is exactly that case.

---

## Helpers

| Function | Description |
|----------|-------------|
| `set_app_icon(win, ico_path, app_id)` | Window + taskbar icon. Must be frozen-aware — in a build `__file__` does not point next to the exe. |
| `_pv_key(channel)` | One dict key for either a plain PV name or a difference pair. |
| `_parse_level(text)` | A limit box: the number in it, or `None` when empty (level off). |
| `_hall_name(v)`, `_pss_name(v)` | A beam-fate / PSS number as words; `None` becomes "not readable", an unrecognised number says so rather than passing for a hall. |
| `_value_for_label(table, label)` | The number behind a dropdown entry, `None` for `_NO_CHECK`. |
| `_hall_verdict(cond, fate, pss, moving_for_s)` | `(verdict, sentence)` — the whole hall rule, pure and testable. |
| `_HTTP_MESSAGES`, `_NETWORK_HINTS`, `_readable_pv_error(pv_name, exc)` | Turn an HTTP status or a socket error into a sentence with a hint, for the log. A raw traceback in the log window tells the operator nothing. The PVs are read with plain `urllib` — this file pulls in no `requests`. |

---

## RegionSelector(tk.Toplevel)

Full-screen overlay for dragging out the watched rectangle. Two windows on
purpose: a semi-transparent one to darken the screen, and a fully opaque red frame
for the border — a border drawn on the transparent window would be transparent
too, and hard to see against a bright display.

Reports `(x1, y1, x2, y2)` back through a callback to
`ScreenTracker._region_selected`. Esc cancels.

---

## ScreenTracker(tk.Tk)

### State

| Field | Meaning |
|-------|---------|
| `region` | The ad-hoc rectangle, `(x1, y1, x2, y2)` or `None`. |
| `reference` | Its reference frame, as a PIL image. The comparison, for it and for every screen condition, is `_picture_diff` = `ImageStat.Stat(ImageChops.difference(now, reference)).mean` — PIL only, no numpy in this file. |
| `tracking` | Whether the poll loop is running. |
| `changed` | Whether an alarm is currently up. |
| `_conditions` | The condition list, in the order shown in the panel. |

The coloured circle is the whole state machine in one glance: **grey** = nothing to
watch, **orange** = an ad-hoc reference or an enabled condition exists but not
watching, **green** = watching, **red** = something fired. `_update_circle` is the
only place that paints it.

### Two independent windows

| Window | What it is | Geometry key |
|--------|-----------|--------------|
| Control window (`ScreenTracker` itself) | The circle, presets, PV badges, log. Becomes a borderless chroma-keyed HUD while tracking. | `window_geometry` |
| Image window (`_image_win`, a `Toplevel`, built by `_ensure_image_win`) | Where the alarm image or colour flashes. | `image_geometry`, falling back to the control window's |

Both are visible at once while an alarm is up.

### Method groups

| Area | Methods |
|------|---------|
| Assets | `_get_icon_path`, `_images_dir`, `_load_image_files`, `_image_path`, `_load_sound_files`, `_make_flash_photo`, `_color_rgb` |
| UI | `_build_ui`, `_toggle_settings_popup`, `_build_settings_popup`, `_on_main_click_close_settings`, `_on_any_click` |
| Presets | `_load_presets`, `_save_presets_file`, `_preset_names`, `_refresh_preset_combo`, `_save_preset`, `_load_preset`, `_delete_preset`, `_save_pv_thresholds`, `_save_flash_settings` |
| Conditions — data | `_next_cond_uid`, `_load_conditions`, `_save_conditions`, `_decode_reference`, `_encode_reference`, `_cond_summary`, `_enabled_conditions` |
| Conditions — panel | `_build_conditions_panel`, `_build_cond_page`, `_build_quick_region_row`, `_build_hall_readout`, `_refresh_cond_tree`, `_selected_condition`, `_on_cond_click`, `_delete_condition`, `_resnap_condition`, `_build_area_block`, `_edit_condition` |
| Conditions — badges | `_rebuild_cond_badges`, `_set_cond_badge`, `_clear_cond_badges` |
| Halls | `_fetch_latest`, `_read_hall_pvs`, `_hall_readout_text`, `_read_hall_now`, `_check_hall_conditions` |
| Region | `_open_region_selector`, `_select_region`, `_on_selector_closed`, `_region_selected`, `_save_reference`, `_grab`, `_grab_rect`, `_picture_diff` |
| Watching | `_toggle_tracking`, `_start_tracking`, `_stop_tracking`, `_poll`, `_watched_areas`, `_on_change_detected`, `_on_condition_failed`, `_raise_alarm`, `_reset`, `_update_circle` |
| Alarm | `_ensure_image_win`, `_resolve_image_geometry`, `_set_flash_alpha`, `_hide_image_win`, `_start_flash`, `_do_flash`, `_play_sound` |
| HUD | `_set_ui_visible`, `_set_transparent` |
| Geometry recorders | `_open_geometry_recorder`, `_start_control_window_recording`, `_start_image_window_recording`, `_save_geometry` |
| Staying on screen | `_screen_areas`, `_fit_rect` (module level), `_frame_insets`, `_clamp_geometry`, `_apply_geometry`, `_place_popup` |
| Alignment ghost | `_start_align_ghost`, `_on_align_configure`, `_refresh_align_ghost`, `_stop_align_ghost` |
| Preview | `_update_preview`, `_show_preview_popup`, `_hide_preview_popup`, `_toggle_preview_popup`, `_check_hide_preview` |
| Log | `_log_message`, `_do_log`, `_clear_log`, `_on_log_hover`, `_hide_log_tip` |
| Monitors | `_identify_monitors` (numbered overlay on each screen) |
| PV alerts | `_cpva_context`, `_fetch_samples`, `_condition_value`, `_poll_pvs`, `_update_pv_display`, `_check_pv_conditions`, `_on_pv_frame_configure`, `_relayout_pv_alerts` |
| Shutdown | `_on_close` (cancels the PV poll job before destroying the window) |

### The notebook

`_build_conditions_panel` makes one `ttk.Notebook` with a page per entry in
`_COND_TABS`, and each page gets its own `Treeview` from `_build_cond_page`.
All three read and write the **same** `self._conditions`; a row's `iid` is its
index into that list, which is what keeps `_selected_condition`,
`_on_cond_click`, `_delete_condition` and `_resnap_condition` working whichever
page the selection is on. `_selected_condition` looks at the open page first, so
pressing Edit does what the page you are looking at implies.

Two things live on a page rather than in the window: `_build_quick_region_row`
(the unnamed rectangle and the saved regions, on Screen areas) and
`_build_hall_readout` (the live line, on Halls). `_set_ui_visible` therefore
hides the notebook alone, not each of them.

Tab labels are styled explicitly (`Cond.TNotebook.Tab`, dark ink on light).
This machine runs Windows in dark mode and the ttk default came out grey on
grey — verify by rendering, `testing/shot_tabs.py`, not by reading the code.

### Things that are the way they are for a reason

- **`FLASH_OFF_ALPHA` is 0.02, not 0.** In *Image only* mode the off half of the
  blink still has to accept the click that dismisses the alarm. A window at alpha
  0 does not.
- **`_relayout_pv_alerts` places badges by hand and wraps them.** The badges vary
  in width and only the active ones are shown, so a normal layout manager either
  clipped the right-hand one or overlapped them. `_on_pv_frame_configure` re-flows
  only when the available **width** actually changed — placing children and setting
  the frame height both fire `<Configure>` with an unchanged width, which would
  otherwise recurse.
- **`_poll_pvs` fetches on a worker thread** and hands the results back to
  `_update_pv_display`; a 500 ms UI loop cannot wait on HTTP.
- **The PV half is gated on `self.tracking`.** `_poll_pvs` returns immediately when
  tracking is off, it is kicked off by `_start_tracking`, and `_stop_tracking`
  cancels the job and hides `_pv_frame`. So the eight badges exist only while
  watching runs. Worth stating plainly in any doc, because "no setup needed" reads
  as "always on", which it is not. What *is* possible now is a watch with no
  rectangle at all: `_start_tracking` accepts an ad-hoc reference **or** any
  enabled condition, so a `pv` condition alone is enough to start.
- **A condition's message is shown as a badge, not on the alarm window.** While
  watching, the control window is a chroma-keyed overlay where only the circle and
  the badges survive (`_set_ui_visible`), so a badge is the one place a sentence
  can be read. `_rebuild_cond_badges` therefore appends its rows to
  `self._pv_alert_rows` and reuses `_relayout_pv_alerts` for the layout — but not
  to `_pv_alert_labels`, which stays index-aligned with `_PV_MONITORS`.
- **`_start_tracking` logs the conditions that cannot fire** (screen with no
  reference, value with no trip level) instead of letting a ticked condition sit
  there doing nothing.
- **The condition editor works on a copy** and writes into `self._conditions` only
  on Save; `_check_pv_conditions` drops a result whose condition is no longer in
  the list, because the fetch that produced it started before the edit.
- **`_fetch_latest` widens its window; `_condition_value` must not be used for
  an enum.** `_condition_value` returns the *highest* sample, which is the right
  answer for "did the energy spike" and a meaningless one for a hall number. The
  hall read wants the newest sample, and has to look back as far as a month to
  find one.
- **`_hall_span_hint` remembers the window that worked, per PV, and the search
  only ever widens from it.** Without the hint a channel that is not archived at
  all costs four requests on every read, every two seconds — and the beam fate is
  that channel today. The search never narrows again: a narrower window is a
  subset of a wider one, so once the hinted window comes back empty there is
  nothing a smaller one could still hold. Starting wide is never wrong, because
  the newest sample of a wide window is the same sample.
- **The hall read rides the PV worker but on its own clock.** `_poll_pvs` checks
  `self._next_hall_read` and only then calls `_read_hall_pvs`; the result travels
  back through `_update_pv_display`'s third argument. Two values that change
  twice a day do not need reading at 2 Hz, and four widening queries each would
  make the 500 ms loop overlap itself.
- **"How long has the switchyard been moving" is measured from the archiver's
  timestamp**, not from `time.time()` minus something local. This PC's clock runs
  about 25 s ahead of the facility, so a locally-timed grace period would be
  wrong by half a minute in the wrong direction.
- **The archiver, not live PVs.** `_fetch_samples` calls the CPVA samples endpoint
  over HTTPS with certificate verification disabled and returns `(time_ns, value)`
  pairs; the badges average the newest `PV_AVG_COUNT` of the last 60 s. A single
  chiller sample crosses ±0.3 constantly; the average does not. Conditions want the
  opposite (see above), which is why they take a peak instead. The archiver
  publishes about a second late, so a value condition is a second or two behind the
  machine — this raises a person, not a hardware interlock.
- **Geometry is remembered per preset or globally.** `window_geometry` /
  `image_geometry` exist both as top-level keys (global default) and inside a
  preset dict (that preset's own). `_clamp_geometry` keeps a remembered position
  on a screen that still exists.
- **A remembered position is the top-left of the window's *inside*.** That is
  what `winfo_rootx` / `winfo_rooty` report and what the recorders store.
  Windows counts a position from the *outside* of the title bar, so handing a
  remembered position straight back to `geometry()` moved the window down and
  right by the title bar — and every save-and-reload moved it again, until it
  walked off the screen. `_apply_geometry` sets the position, measures where
  the window really landed, and corrects the difference; use it instead of
  `geometry()` for anything remembered. Borderless windows (the flashing image,
  the HUD) have no title bar, so for them the two counts are the same.
- **Screen sizes for placement come from Windows, not `screeninfo`.** The
  program does not declare itself display-scaling aware, so a monitor set to
  150 % offers windows only 1280x720 of room while `screeninfo` reports its
  real 1920x1080 — positions taken from `screeninfo` can therefore be past the
  edge of a screen that windows can actually reach. `_screen_areas` asks
  Windows for the usable area of each monitor (taskbar excluded), and asks
  again every time, because taking a screenshot flips the scaling awareness of
  the whole program and with it the numbers. `screeninfo` stays in use for the
  watched region and the screenshots, which do work in real pixels.
- **Every window is pulled onto a screen before it is shown.** `_fit_rect`
  picks the screen the window already covers most of (so a window on the second
  monitor stays there), then keeps the whole frame inside it; a window larger
  than the screen is pinned to the top-left corner instead of being pushed off
  the opposite edge. `_place_popup` runs it for the settings panel, the preview,
  the log tooltips, the recorder dialogs and the monitor numbers.

---

## Dependencies

```
tkinter      UI
Pillow       ImageGrab (screen capture), ImageTk (preview and alarm image)
screeninfo   the monitor list (real pixels — region and screenshots)
ctypes       the usable area of each monitor as windows see it (stdlib)
urllib       reading the PVs from the archiver (stdlib, no requests here)
winsound     the built-in beep (stdlib, Windows only)
```
