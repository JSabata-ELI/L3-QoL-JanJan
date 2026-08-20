# Chiller Log — STRUCTURE

> Verified against source: 2026-08-19 · `cpt.py` 3131 L

Single-file tkinter app. Long-term (multi-year) trend log of the six chillers,
built from the CPVA archiver and kept in a plain CSV next to the program.

Not live, not an alarm: three samples per chiller per day. Live values and alerting
are Diagnostic's job; short-window raw samples are CSS Logger's.

User-facing documentation: `ReadMe_Chiller Log.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Chiller Log_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `cpt.py` | The whole program. Run with `python cpt.py`. Class is still called `CPVAExplorerApp` and the module docstring still says "Chiller Explorer" — the window title is `Chiller Log`. |
| `chiller_archive.csv` | **The log.** One row per recorded moment. The file that matters. |
| `historical_chiller_data.xlsx` | Pre-program history from 2018-11-01. Read **only** when the CSV does not exist. |
| `cpva_explorer_config.json` | Remembered display range + HTTP timeout. Not present in the repo copy. |
| `allowed_pvs.json` | 0 bytes, never read. Leftover. |
| `icon.ico` | Window / taskbar icon via `set_app_icon`. |

No `build_config.json` — this program is not set up for a Dev Tools build. The
Launcher's `SCRIPTS` set contains `"Chiller log"`, which normalises to match, so it
would appear under **Scripts** once an exe exists.

---

## Constants

| Symbol | Value / meaning |
|--------|-----------------|
| `CPVA_BASE_URL` | `https://10.78.0.57:8443/api/1.0/cpva`, `+ /samples`. |
| `CPVA_HTTP_TIMEOUT` | 10 s, overridable from the config file. |
| `CHUNK_SIZE_NS` | 1 hour. **The archiver only returns reliable data for windows ≤ 1 h**, so every request is chunked to that. |
| `_SESSION` | one `requests.Session` with `verify = False`; `_SSL_CONTEXT` is the `urllib` equivalent, left over from before the session existed. |
| `ARCHIVE_COLUMNS` | `datetime, source, ch1_flow…ch6_flow, ch1_temp…ch6_temp`. |
| `ALLOWED_PVS` | Generated: `L3-UTIL-CHL03-00n:{Flow_GPM,Temp,PumpON}` for n = 1…6. |
| `GRAPH_GROUPS` | `Flow_GPM` and `Temp`, six PVs each. |
| `CHILLER_NAMES` | 1–4 Diode Array Chiller 1–4, 5 Helium Chiller, 6 Utility Chiller. |
| `TZ_PRAGUE` | Everything user-visible is Prague time; everything on the wire is UTC nanoseconds. |
| `DEFAULT_CONFIG` | `time_from`, `time_to`, `http_timeout`. |

**`save_config` returns early when the file does not exist.** It never creates it,
so on an installation without `cpva_explorer_config.json` the display range is
silently not persisted. Deliberate or not, it is the observed behaviour; an empty
`{}` file switches persistence on.

---

## The sampling rule — `_fetch_scheduled_average_with_pump_samples`

The single most important function in the file, and the reason the log looks the
way it does.

```
DAY_TIMES        = 09:00, 13:30, 18:00   (Prague)
MINUTE_NS        = the averaging window after each of those times
CHECK_BEFORE_NS  = 10 minutes
```

For every day in the requested window, for each of the three times:

1. Skip if the moment is outside the requested window.
2. `pump_is_on_at(t - 10 min) and pump_is_on_at(t)` — **both**, or the sample is
   discarded. A pump switched on five minutes ago has not settled; that reading
   would be a meaningless spike in a multi-year trend.
3. Fetch that one minute of samples, keep the numeric ones, store the mean.

`pump_is_on_at` walks the sorted `PumpON` sample list and takes the last value at
or before the timestamp — a step function, not an interpolation.

Consequence for the docs: **gaps in the log mean the pump was off (or had just come
on)**, not that the fetch failed. The graph is drawn `steps-post` for the same
reason.

---

## Fetching

| Function | Description |
|----------|-------------|
| `_http_get_json(url, timeout)` | `requests` + `orjson`. |
| `cpva_fetch_samples(channel, start_ns, end_ns, timeout)` | One window, one channel. |
| `_chunk_is_night(start, end)` | True when the whole chunk lies in 22:00–06:00 Prague. Such chunks are **never requested** — nothing is recorded then. Guard: a chunk reaching within 60 s of now is never treated as night, so a late-evening update still catches up. |
| `cpva_fetch_samples_chunked(...)` | Split into 1 h chunks, drop the night ones, fetch up to `max_workers` (12 default, 8 for `PumpON`) in a `ThreadPoolExecutor`, reassemble **in chunk order** from `results_map`. |
| `cpva_decode_value(sample)` | Number, string, single-element list → scalar; a short all-ASCII int list is decoded as text; anything else returned as-is. |
| `cpva_fetch_channels()` | Returns `sorted(ALLOWED_PVS)` — no discovery call, the channel set is fixed. |

---

## Archive I/O

| Method | Description |
|--------|-------------|
| `_ensure_archive_exists()` | CSV missing → build it from the XLSX, `source = historical_xlsx`. Silent no-op when the XLSX is missing too (a log line only). |
| `_load_history_xlsx(path)` | openpyxl; header row lower-cased and de-spaced; `date`/`datum`; comma decimals tolerated; sorted by time. |
| `_load_archive_rows()` | Whole CSV → list of dicts, `datetime` parsed and stamped Prague, sorted. |
| `_get_last_archive_datetime()` | max of those — the resume point. |
| `_refresh_archive_view()` | Re-read the CSV, filter to `_dt_from … _dt_to`, then `_archive_rows_to_graph_data` + `_populate_archive_table`, and re-plot if the Graph tab is showing. |
| `_archive_rows_to_graph_data(rows)` | CSV columns → `_samples_by_pv` / `_table_rows` keyed by real PV names, so the plotting code is the same code that would plot live samples. |
| `_set_display_range_preset(preset)` | `all` / `1y` / `6m` / `1m`, measured **back from the newest row in the log**, not from `now()`. Saves the range to the config and refreshes. |

### `_update_archive_from_cpva` / `_update_archive_worker_impl`

Resume point → now; empty log starts at 2026-01-01. Per chiller: fetch `PumpON`
from **24 h before** the start (so the −10 min test can be answered for the first
scheduled time in the window), then flow and temp through the sampling rule.
Rows are keyed by timestamp in `rows_by_ts`, existing `datetime` strings are read
out of the CSV and skipped, and the remainder is **appended** — never rewritten, so
a second press is a no-op. Finishes by switching the display to `1Y`.

Runs on a daemon thread; all UI updates go back through `root.after`.

---

## UI

```
tk.Tk  "Chiller Log"  (zoomed, min 1200×750)
└── ttk.Notebook
    ├── Graph          _build_graph_tab
    ├── Data Archive   _build_table_tab
    └── Log            _build_log_tab
    status label at the bottom (Ready / loaded / updating / failed)
```

### Graph tab controls

`📅 Display Range` · `All` `1Y` `6M` `1M` · From/To labels · `🔄 Update archive` ·
`💾 Save graph` · `↩ Back` · Choose Variable (`Flow_GPM` / `Temp`) · `CH1…CH6`
checkboxes · `Grid` · `Legend` · `Font __ pt`.

| Method | Description |
|--------|-------------|
| `_plot_graph()` | Full draw. Colour is taken from the **chiller number**, not the loop index, so a chiller keeps its colour when others are hidden. Above `MAX_GRAPH_POINTS` (30 000) the series is decimated. Smoothed series draw the raw data thin at alpha 0.3 underneath and the moving average solid on top, both `steps-post`. |
| `_update_graph_data()` | Cheaper redraw path. |
| `_on_zoom_select` / `_zoom_back` | Rectangle zoom with a `_zoom_history` stack of `(xlim, {pv: ylim})`. |
| `_on_span_select` | Horizontal span selection. |
| `_on_graph_mouse_move` / `_process_mouse_move` | Crosshair snapped to the nearest real point, from `_graph_raw`. |
| `_apply_font_size`, `_save_graph`, `_clean_graph`, `_clear_graph` | |
| `_StatsShim` | Keeps the old `lbl_graph_stats.config(text=…, fg=…)` call working while the stats area is really a frame of per-PV coloured labels. |

### Data Archive tab

`ttk.Treeview` over `ARCHIVE_COLUMNS`. `_on_tree_motion` (hover full value),
`_on_tree_right_click` (copy cell / copy row / delete row), `_on_tree_double_click`
(open a path-looking value), `_export_csv` + `_ask_export_pvs`.
**`_delete_selected_row` is effectively dead in this view.** It matches the clicked
row by comparing `values[0]` against `ns_to_local_str(ts_ns)`, which appends
`.mmm`; `_populate_archive_table` fills that column straight from the CSV, i.e.
`%Y-%m-%d %H:%M:%S` with no milliseconds. The strings never compare equal, so
`delete_index` stays `None` and the method returns silently. It also only ever
touched `_table_rows` in memory — the CSV was never rewritten. Either fix the
comparison and add a CSV write, or drop the menu entry; as it stands it promises
something it does not do.

### Time-window dialog

`_open_time_window_dialog` builds two tabs — absolute (two hand-drawn calendars
with hour/minute/second spinners, `Set now`) and relative ("last N hours / days") —
and resolves whichever tab is active through `_resolve_time_window`.
`DatePickerDialog` is the smaller standalone picker used by `_pick_date`.

---

## Dependencies

```
tkinter               UI
requests + orjson     archiver HTTP, fast JSON
matplotlib            the graph (imported lazily by _try_import_matplotlib)
openpyxl              only for the historical XLSX import
csv, zoneinfo         the log file, Prague time
```

`_try_import_matplotlib()` is lazy on purpose: without it the tab shows a red
"matplotlib not installed" message instead of the program failing to start.
