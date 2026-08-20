# Calibrations — STRUCTURE

> Verified against source: 2026-08-19 · `cal.py` 1055 L

User-facing documentation: `Readme_calibrations.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Calibrations_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `cal.py` | Single file. The whole tool (PySide6). Run with `python cal.py`. |
| `icon.ico` | Window / taskbar icon. |

There is no `build_config.json`. Nothing is read from disk at startup and nothing
is written except an export the user asks for.

---

## Purpose

A Python replica of the logic in `Calibrations2.xlsx`. It computes a calibration
factor per measured point (`QE95 / Device`), averages it over a chosen waveplate
range, and derives new calibration constants (multiplicator, offset,
int_multiplicator) from a straight-line fit.

**It does not open the spreadsheet.** The workbook was the source of the *formulas*,
not an input file — everything is typed into the table or generated from the
device profile. Export is CSV, or `.xls` if `xlwt` is installed.

---

## Constants

| Symbol | Value |
|--------|-------|
| Devices | `PAP1`, `PTM1`, `PCM2`, `PCM4` |
| `DEFAULT_PROFILES` | Per device: the waveplate rows to generate (`start`, `end`, `step`) and the fit range (`from_`, `to`). PAP1 0…1 400 000 step 50 000, fit 200 000…1 000 000 · PTM1 0…60 000 step 5 000, fit 0…50 000 · PCM2 and PCM4 200 000…800 000 step 50 000, fit the whole range |
| `TIMINGS` | `Off-A SS`, `Off-A`, `Off-B SS`, `Off-B` — the list that used to live on the Settings sheet |
| `DEFAULT_INFO_TEXT` | The text the ⓘ dialog shows. |

Highlight brushes are deliberately faint (alpha 22–35) so a highlighted row is
still readable.

---

## Module-level helpers

| Function | Description |
|----------|-------------|
| `to_float(s)` | String → float; blank or unparsable → `None`. |
| `linreg_slope_intercept(x, y)` | Least squares → `(slope, intercept)`, or `(None, None)` when there are too few points. |
| `micro_to_base(v_micro)` | `v × 1e-6`. `int_multiplicator` is entered and displayed in micro units (×10⁶) but computed in base units. |
| `_normalize_seps(s)` | Unify the separators in a typed date. |
| `parse_date_any(s)` | Accepts `dd.mm.yyyy`, `yyyy-mm-dd`, `dd-mm-yy` and more. |
| `format_date(d)` | The canonical output form, `dd.mm.yyyy`. |

## Dialogs

- `CalendarDialog(QDialog)` — date picker over `QCalendarWidget`; `selected_date()`.
- `InfoDialog(QDialog)` — read-only help text.

---

## CalibrationTable(QMainWindow)

### The table

Five columns: `Waveplate | QE95 [mJ/J] | <Device> [mJ/J] | Cal Factor (read-only) | Note`.
`_set_headers_for_device` renames the third column to whichever device is
selected.

### Top controls

| Control | Meaning |
|---------|---------|
| `device_combo` | The device. Changing it regenerates the default waveplate rows, and asks first if the table has unsaved edits (`on_device_change_requested` → `mark_dirty`). |
| `convert_combo` / `useint_combo` | The YES/NO switches, a replica of the Excel Settings logic. |
| `date_combo` + `btn_date_cal` | Calibration date — type it or pick it from the calendar. `_fill_last_7_days` prefills the recent days. |
| `timing_combo` | One of `TIMINGS`. |
| `from_combo` / `to_combo` | The waveplate range used for the fit and the average factor. Filled from the table by `populate_from_to_choices_from_table`. |
| `avg_cal_factor` | Read-only: the mean `Cal Factor` inside from…to. |

### Calibration panel

| Field | Meaning |
|-------|---------|
| `mult1`, `off1`, `intmult1_micro` | The **old** constants — typed in by the user. |
| `mult2`, `off2`, `intmult2_micro` | The **new** constants — read-only, computed. |

### Key methods

| Method | Description |
|--------|-------------|
| `cal_factor_formula(qe95, device_val)` | `QE95 / Device`. |
| `recompute_cal_factor_for_row(row)` / `recompute_all_cal_factors()` | Fill the read-only column. |
| `_collect_points_in_range()` | The `(Device, QE95)` pairs inside from…to — the input to the fit. |
| `update_from_to_rows` / `update_average_cal_factor` / `find_row_by_waveplate` | Keep the range and its average in step with the table. |
| `recompute_new_calibration()` | The new constants. See below. |
| `apply_highlight()` / `_clear_backgrounds()` | Shade the from…to rows plus the active row and column. |
| `add_row()` / `remove_row()` / `_refresh_row_numbers()` | Edit the row set. |
| `on_item_changed(item)` | One edit → recompute that row, the average and the new constants. |
| `_collect_table_data()`, `save_to_file()`, `_save_csv(...)`, `_save_xls(...)` | Export. CSV always works; `.xls` needs `xlwt` and falls back to CSV with a message when it is missing. |
| `_set_cell_text` / `_get_cell_text` | The only two places that touch cell text, so the read-only flag cannot be lost. |

### How the new calibration is computed

A straight line is fitted over the selected range:

```
QE = slope × Device + intercept
```

The exponent depends on the device: **p = 1 for PAP1, p = 2 for the others.**

Two candidate parameter sets are formed:

```
M_mult = slope × mult_old            J_mult = 1
M_off  = slope × off_old + intercept J_off  = off_old + intercept / (int_old × slope**p)
M_int  = 1                           J_int  = slope × int_old
```

and the Convert? / Use Int? pair picks between them:

| Convert? | Use Int? | Result |
|----------|----------|--------|
| no | no | `M_mult`, `M_off`, `M_int` |
| no | yes | `J_mult`, `J_off`, `J_int` |
| yes | no | `1`, `M_off / M_mult`, `M_mult` — blank when `M_mult` is ~0, rather than dividing by it |
| yes | yes | `J_mult × J_int`, `J_off × J_int`, `1` |

If the fit fails, or any old constant is missing, all three outputs are cleared
rather than filled with a partial answer.

---

## Dependencies

```
PySide6   UI
csv       CSV export
xlwt      optional — .xls export; without it, CSV with a notice
```
