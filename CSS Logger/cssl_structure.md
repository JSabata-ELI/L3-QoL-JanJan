---
name: cssl.py structure map
description: Line-by-line class/function map of cssl.py — read this before editing to avoid re-reading 1700+ lines.
---

File: `CSS logger/cssl.py`  ~1750 lines  |  Pure tkinter, Python 3.12

## Module-level constants

| Line | Name | Value / note |
|------|------|-------------|
| L22 | `TZ_PRAGUE` | `ZoneInfo("Europe/Prague")` — imported but NOT used for image paths |
| L35 | `APP_DIR` | frozen exe parent or `__file__` parent |
| L36 | `CONFIG_FILE` | `APP_DIR / "cpva_explorer_config.json"` |
| L43 | `CPVA_BASE_URL` | `https://10.78.0.57:8443/api/1.0/cpva` |
| L47 | `IMAGE_ROOT` | `\\users-L3.tier0.lcs.local\cpva-image-2026` |
| L50 | `CHUNK_SIZE_NS` | 1 h in ns — archiver reliable window |
| L53 | `MERGE_WINDOW_MS` | `80` — row-merge tolerance |
| L323–329 | fonts/colors | `FONT_NORMAL`, `FONT_MONO`, `FONT_HEADER`, `COLOR_GREEN/RED/BLUE/GRAY` |

## Module-level helper functions

| Line | Name | What it does |
|------|------|-------------|
| L29 | `get_app_dir()` | frozen or source directory |
| L68 | `load_config()` | JSON config with defaults fallback |
| L81 | `save_config()` | writes JSON config |
| L90 | `_make_ssl_context()` | unverified SSL (self-signed cert on archiver) |
| L97 | `_http_get_json()` | HTTPS GET → parsed JSON |
| L105 | `cpva_fetch_samples()` | single API chunk call |
| L119 | `cpva_fetch_samples_chunked()` | splits range into ≤1h chunks, concatenates |
| L144 | `cpva_decode_value()` | extracts int/float/str from CPVA sample dict |
| L166 | `cpva_fetch_channels()` | fetches all channel names from `/channels` |
| L178 | `now_ns()` | UTC now in nanoseconds |
| L182 | `dt_to_ns()` | datetime → ns |
| L188 | `ns_to_local_str()` | ns → `"YYYY-MM-DD HH:MM:SS.mmm"` local |
| L195 | `parse_user_datetime()` | multi-format parser (ISO + European) |
| L213 | `_STRIP_PATTERNS` | regex: strips HAPLS, IN, LT#, DIAG# from PV names |
| L223 | `shorten_pv_name()` | `HAPLS-ENER-IN-PFM8-LT1-DIAG2:Energy.value` → `PFM8 - Energy` |
| L250 | `_matches_wildcard()` | case-insensitive `*` wildcard + space-AND matching |
| L265 | `_open_path()` | `os.startfile` / `xdg-open` |
| L277 | `_looks_like_image_path()` | checks `.png/.jpg/.tif/…` extension |
| L282 | `_image_file_size()` | UNC stat → `"1.2 MB"` / `""` on error |
| L297 | `_resolve_image_path()` | prepends `IMAGE_ROOT`, converts slashes (no TZ math) |
| L303 | `_popup_geometry()` | `+x+y` string below widget, clamped to screen |
| L336 | `_btn()` | factory: raised button, `cursor="hand2"` |
| L642 | `_try_import_matplotlib()` | safe import with TkAgg backend |

## Class: DatePickerDialog  (L355–521)

Modal calendar + time picker. Callback `on_ok_callback(dt)` on OK.
Position params: `click_x, click_y` → placed just below trigger widget.
**Key fix:** hour/min/sec are `tk.StringVar` (not `IntVar`) — avoids Tcl octal-parse error with `"09"`.

| Line | Method | Note |
|------|--------|------|
| L364 | `__init__` | builds UI, positions via `geometry(f"{w}x{h}+{x}+{y}")` |
| L396 | `_build_ui` | nav buttons, 6×7 day grid, time spinboxes, OK/Cancel |
| L448 | `_draw_calendar` | redraws grid, highlights selected day blue |
| L467 | `_get_time_ints` | parses StringVar safely (handles leading zeros) |
| L476 | `_update_label` | updates "selected" label |
| L484 | `_on_day_click` | row/col → day number, redraws |
| L493 | `_shift_month` | wraps Dec→Jan, clamps day |
| L504 | `_shift_year` | clamps day |
| L511 | `_on_ok` | clamps H/M/S, calls callback, destroys |

## Class: PVBrowserDialog  (L527–636)

Modal browser for all archiver channel names. Async load, wildcard filter.
Position params: `x, y` → places 700×550 window at that position.

| Line | Method | Note |
|------|--------|------|
| L528 | `__init__` | async load; positions via `geometry(f"{w}x{h}+{x}+{y}")` |
| L557 | `_build_ui` | search entry + live filter, listbox, count label, Add/Close |
| L592 | `_load_async` | background thread → `cpva_fetch_channels()` |
| L601 | `_on_loaded` | stores + filters channels |
| L605 | `_on_err` | shows error messagebox |
| L609 | `_apply_filter` | wildcard filter |
| L614 | `_refresh_listbox` | cap 2000 rows, update count label |
| L623 | `_add_selected` | calls `on_add_callback` with selection |
| L628 | `_add_and_close` | add + destroy |
| L632 | `_on_destroy` | fires `on_close_callback(search_text)` for search memory |

## Class: CPVAExplorerApp  (L657–1739)

Single-window app: sidebar + Notebook (Graph / Table / Log tabs).

### Init & UI build

| Line | Method | Note |
|------|--------|------|
| L658 | `__init__` | config, matplotlib import, time state, `_build_ui()` |
| L696 | `_build_ui` | sidebar + notebook + status bar |
| L731 | `_build_sidebar` | presets, From/To pickers, PV listbox, Browse/Remove/Clear, Load, progress bar |
| L812 | `_build_graph_tab` | canvas container, grid toggle, Y-limit entries, info/stats labels |
| L1104 | `_build_table_tab` | Treeview + scrollbars, context menu, tooltip system, Export CSV button |
| L1155 | `_build_log_tab` | dark `ScrolledText`, Clear button |

**Stored button refs** (for popup positioning):
`self.btn_from`, `self.btn_to`, `self.btn_browse`, `self.btn_clear_pvs`, `self.btn_export_csv`

### Time window

| Line | Method | Note |
|------|--------|------|
| L1172 | `_populate_ui` | load PV list from config |
| L1178 | `_refresh_time_labels` | update From/To button text |
| L1186 | `_apply_preset` | set range = now − N hours |
| L1191 | `_pick_date` | opens `DatePickerDialog` with button coords |
| L1206 | `_resolve_time_window` | validates + returns `(start_ns, end_ns)` |

### PV list

| Line | Method | Note |
|------|--------|------|
| L1218 | `_get_pv_list` | listbox → list |
| L1221 | `_update_pv_count` | updates count label |
| L1224 | `_open_pv_browser` | opens `PVBrowserDialog` below `self.btn_browse` |
| L1233 | `_add_pvs_from_browser` | deduped insert + save config |
| L1242 | `_remove_selected_pvs` | delete selected + save |
| L1248 | `_clear_pv_list` | custom confirm Toplevel (positioned below Trash btn) |
| L1288 | `_save_pv_list_to_config` | persist to JSON |

### Data loading

| Line | Method | Note |
|------|--------|------|
| L1296 | `_on_load_clicked` | validates, disables button, starts worker thread |
| L1356 | `_on_load_finished` | stores samples, merges rows, populates table, updates progress |
| L1381 | `_merge_samples_into_rows` | image PVs → individual rows; others → 80ms merge window |

### Graph tab

| Line | Method | Note |
|------|--------|------|
| L858 | `_on_tab_changed` | auto-plots when Graph tab selected |
| L863 | `_toggle_graph_grid` | grid on/off all axes |
| L869 | `_apply_graph_ylim` | manual Y limits |
| L881 | `_on_span_select` | SpanSelector → min/max/mean/n stats |
| L901 | `_plot_graph` | multi-axis figure; crosshair setup; SpanSelector |
| L1013 | `_clear_graph` | destroy canvas/figure, disconnect events |
| L1038 | `_on_graph_mouse_move` | crosshair vline+hline per axis; datetime+y in info label |

**Crosshair state attrs:** `_graph_axes`, `_crosshair_vlines`, `_crosshair_hlines`, `_crosshair_cid`

### Table tab

| Line | Method | Note |
|------|--------|------|
| L1441 | `_populate_table` | build columns, auto-size widths, insert rows |
| L1498 | `_text_px` | char-count → pixel estimate |
| L1502 | `_format_value` | float (smart decimals), list, image path (+file size), str |
| L1524 | `_on_tree_motion` | column-header tooltip with full PV name |
| L1547 | `_on_tree_right_click` | context menu; "Open image" enabled only for image paths |
| L1558 | `_on_tree_double_click` | opens image via `_open_path(_resolve_image_path(...))` |
| L1567 | `_get_cell_value` | Treeview row+col → string |
| L1577 | `_copy_cell` | clipboard |
| L1585 | `_copy_row` | tab-separated row to clipboard |
| L1593 | `_open_image_from_selection` | opens image or shows "not an image" |

### CSV Export

| Line | Method | Note |
|------|--------|------|
| L1608 | `_export_csv` | file dialog, writes CSV |
| L1663 | `_ask_export_pvs` | checkbox dialog positioned below `self.btn_export_csv` |

### Log

| Line | Method | Note |
|------|--------|------|
| L1727 | `_log` | async append + scroll |
| L1735 | `_clear_log` | clear text area |
