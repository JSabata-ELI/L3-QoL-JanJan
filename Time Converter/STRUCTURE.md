# Time Converter — STRUCTURE

> Verified against source: 2026-08-19 · `tc.py` 592 L

## Files

| File | Description |
|------|-------------|
| `tc.py` | Single file. The whole tool (tkinter/ttk). Run with `python tc.py`. |
| `icon.ico` | Window / taskbar icon (`set_app_icon`). |

No config file — every run asks for its options.

---

## Purpose

Copies camera files out of the archive and renames them on the way: the UNIX
nanosecond timestamp at the end of the filename is replaced by a readable
`YYYY_MM_DD--HH_MM_SS__ffffff`, in UTC or Prague local time.

The originals are never touched — only copies are written.

---

## Constants

| Symbol | Meaning |
|--------|---------|
| `FINAL_RE` | `\d{4}_\d{2}_\d{2}--\d{2}_\d{2}_\d{2}__\d{6}$` — a name already in the target form. Matching files are skipped, which is what makes the tool safe to run twice over the same folder. |
| `SOURCE_RE` | `(\d+)$` — the trailing number, taken as the nanosecond timestamp. |
| `PRAGUE` | `ZoneInfo("Europe/Prague")`. |
| `TS_MIN_NS` / `TS_MAX_NS` | 2000-01-01 to 2100-01-01 in nanoseconds. A trailing number outside that window is *not* a timestamp — better to skip the file than to name it something absurd. |

---

## Naming logic

| Function | Description |
|----------|-------------|
| `split_stem(stem)` | `(prefix, raw_ts_str)`. First it collapses `-_-` and `_-_` to a single `_`, which is the artefact the camera archive produces; then the trailing number is the timestamp and everything before it (minus trailing underscores) is the prefix. |
| `convert_timestamp(ns, use_prague_time)` | ns → `%Y_%m_%d--%H_%M_%S__%f`, in Prague or UTC. |
| `orig_ts_to_utc(orig_ts_str)` | The same rendering in UTC, for the "Orig. UTC" column. Returns `—` on anything unparsable rather than raising. |
| `build_new_name(stem, use_prague_time)` | `(new_stem, None)` or `(None, reason)`, where reason is `already_converted`, `no_trailing_number` or `invalid_timestamp`. Every skip in the tables comes from one of those three. |
| `_fmt_hms(seconds)` | Duration for the progress window. |

---

## UI pieces

| Function | Description |
|----------|-------------|
| `show_intro_and_get_options(parent)` | The opening dialog: *Convert to local time* and *Show detailed report*, both on by default. Returns a dict, or `None` if cancelled. |
| `_make_file_table(parent, rows, height)` | A `ttk.Treeview` of five columns with colour tags: copy / overwrite / skip / warn / error. |
| `_make_dashboard(parent, counts)` | The row of coloured count boxes. |
| `_plan_row`, `_skip_row`, `_done_row` | Build one table row for each of the three phases, so the columns cannot drift between the preview and the report. |
| `show_preview(parent, plan_rows, skip_rows)` | Everything that will happen, before anything happens → Proceed / Cancel. |
| `show_report(parent, done_rows, skip_rows, cancelled, total)` | The result. |
| `create_progress_window(parent, total)` | Progress bar, elapsed time, an ETA smoothed over the recent rate, and Cancel. |
| `_center_window(win, w, h)` | |
| `set_app_icon(win, ico_path, app_id)` | Window + taskbar icon; frozen-aware. |

---

## Flow (`main`)

1. Intro dialog — timezone and report options.
2. `filedialog.askopenfilenames`, starting in the camera archive on the network.
3. Choose the destination folder.
4. Plan: every file through `build_new_name` → a copy, overwrite or skip row.
5. Preview window (when the report option is on).
6. Copy on a `ThreadPoolExecutor(max_workers=4)` — `copy_one(args)` per file —
   while the main thread polls for progress. Cancel stops it between files.
7. Report window, or a plain message box. Then back to step 1, so several batches
   can be done in one session.

---

## Dependencies

```
tkinter              UI
pathlib, shutil      the copying
concurrent.futures   four workers
zoneinfo             Prague time
```
