# Announcer — STRUCTURE

> Verified against source: 2026-08-19 · `a.py` 1798 L

Single-file tkinter app. Two independent jobs in one window:

1. **Screen region watch** — grab a rectangle of the screen every 500 ms, compare
   it to a reference, and raise a visual + audible alarm when it changes.
2. **PV alerts** — poll eight machine values every 500 ms and show a coloured
   badge for each one that is out of range.

The user-facing description is in `ReadMe Announcer.txt`.

## Files

| File | Description |
|------|-------------|
| `a.py` | The whole program. Run with `python a.py`. |
| `presets.json` | Saved regions, window geometries, PV thresholds and flash settings. |
| `images/scorpion_orig.png` | The alarm image. Read from `images/` **next to the exe** at runtime. |
| `sounds/chime.wav`, `pluck.wav`, `pop.wav` | Selectable alert sounds. |
| `scorpion.png` | Loose copy in the folder root; the app reads `images/` only. |
| `build_config.json` | Dev Tools build settings. `extra_files` lists `images` and `sounds` as **folders**. |
| `icon.ico` | Window / taskbar icon, via `set_app_icon`. |

`images/` and `sounds/` must ship with every build: a build without `images/` can
only flash a plain colour, because the alarm image is read from next to the exe.
The Builder now also picks those folder names up automatically, but the entry in
`build_config.json` documents the requirement.

---

## Constants

| Symbol | Value | Meaning |
|--------|-------|---------|
| `POLL_INTERVAL_MS` | 500 | Screen check interval. |
| `CHANGE_THRESHOLD` | 2 | Default average pixel deviation (0–255) that counts as a change. |
| `FLASH_DURATION_MS` | 3000 | How long the alarm blinks. |
| `FLASH_INTERVAL_MS` | 300 | Blink speed. |
| `FLASH_OFF_ALPHA` | 0.02 | Window opacity in the "off" half of a blink — not 0, because a fully transparent window stops receiving clicks. |
| `PV_AVG_COUNT` | 25 | How many recent samples each PV reading averages. |
| `PV_POLL_MS` | 500 | PV read interval. |
| `_RESERVED_PRESET_KEYS` | | Top-level keys in `presets.json` that are **not** presets: `pv_thresholds`, `window_geometry`, `image_geometry`, `flash_mode`, `image_file`. Anything else at the top level is a preset name. |

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

---

## Helpers

| Function | Description |
|----------|-------------|
| `set_app_icon(win, ico_path, app_id)` | Window + taskbar icon. Must be frozen-aware — in a build `__file__` does not point next to the exe. |
| `_pv_key(channel)` | One dict key for either a plain PV name or a difference pair. |
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
| `region` | `(x1, y1, x2, y2)` or `None`. |
| `reference` | The reference frame, as a PIL image. The comparison is `ImageStat.Stat(ImageChops.difference(now, reference)).mean` — PIL only, no numpy in this file. |
| `tracking` | Whether the poll loop is running. |
| `changed` | Whether an alarm is currently up. |

The coloured circle is the whole state machine in one glance: **grey** = no
reference, **orange** = reference set but not watching, **green** = watching, **red**
= change detected. `_update_circle` is the only place that paints it.

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
| Region | `_select_region`, `_on_selector_closed`, `_region_selected`, `_save_reference`, `_grab` |
| Watching | `_toggle_tracking`, `_start_tracking`, `_stop_tracking`, `_poll`, `_on_change_detected`, `_reset`, `_update_circle` |
| Alarm | `_ensure_image_win`, `_resolve_image_geometry`, `_set_flash_alpha`, `_hide_image_win`, `_start_flash`, `_do_flash`, `_play_sound` |
| HUD | `_set_ui_visible`, `_set_transparent` |
| Geometry recorders | `_open_geometry_recorder`, `_start_control_window_recording`, `_start_image_window_recording`, `_clamp_geometry`, `_save_geometry` |
| Alignment ghost | `_start_align_ghost`, `_on_align_configure`, `_refresh_align_ghost`, `_stop_align_ghost` |
| Preview | `_update_preview`, `_show_preview_popup`, `_hide_preview_popup`, `_toggle_preview_popup`, `_check_hide_preview` |
| Log | `_log_message`, `_do_log`, `_clear_log`, `_on_log_hover`, `_hide_log_tip` |
| Monitors | `_identify_monitors` (numbered overlay on each screen) |
| PV alerts | `_poll_pvs`, `_update_pv_display`, `_on_pv_frame_configure`, `_relayout_pv_alerts` |
| Shutdown | `_on_close` (cancels the PV poll job before destroying the window) |

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
- **Geometry is remembered per preset or globally.** `window_geometry` /
  `image_geometry` exist both as top-level keys (global default) and inside a
  preset dict (that preset's own). `_clamp_geometry` keeps a remembered position
  on a screen that still exists.

---

## Dependencies

```
tkinter      UI
Pillow       ImageGrab (screen capture), ImageTk (preview and alarm image)
screeninfo   the monitor list
urllib       reading the PVs from the archiver (stdlib, no requests here)
winsound     the built-in beep (stdlib, Windows only)
```
