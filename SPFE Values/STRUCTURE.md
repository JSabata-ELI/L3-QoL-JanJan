# SPFE Values — STRUCTURE

> Verified against source: 2026-09-03

PySide6. Records a fixed set of SPFE quantities twice every working day, from
the CPVA archiver, into one workbook on the scratch share laid out like the
paper table.

Modelled on `Chiller Log/cpt.py` — same idea, three deliberate departures
(§ *What was not copied*). Written as **one QWidget with no tab bar of its own**
so it can become a single page of the joined program.

**The look follows Chiller Log** — Segoe UI 9 / Consolas 9, its
`#1976D2 / #E53935 / #4CAF50 / #666666` semantic quartet, a `#E0E0E0` header
band on white cells, plain Windows-grey buttons and one blue primary. Nothing
was copied: `cpt.py` is tkinter with an unstyled `ttk.Treeview` and contains no
QSS at all, so the design language is translated, not shared. The neutrals match
the house Qt table style in `CSS Logger/daypicker.py:240`. The **toolbar**
follows Image Tools instead: collapsible colour-coded sections
(`spfe_sections.py`), so a person moving between the suite's programs sees the
same panel.

User-facing documentation: `ReadMe_SPFE Values.txt` (short) and
`ReadMe_SPFE Values_Full.txt` (detailed). Shared infrastructure:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `main.py` | Standalone window. Title, icon, taskbar id, `closeEvent → widget.shutdown()`. **Not** copied when the widget moves into CSS Logger. |
| `spfe_t.py` | `SPFEValuesWidget(QWidget)` — the whole UI, one page. The future tab. |
| `spfe_record.py` | Fetch a moment, judge it. The layer between archiver, files and window. No GUI. |
| `spfe_store.py` | Fields, config, share resolution, the CSV, the workbook. No GUI, no `requests`. |
| `spfe_stats.py` | The out-of-range rule — "is this unlike the other days?". No GUI, no imports at all beyond the stdlib. |
| `spfe_limits.py` | The **reference** rule — "is this inside the range somebody set?". Three levels: `""`, `"warn"`, `"bad"`. No GUI, no files, stdlib only. |
| `spfe_core.py` | Archiver access. A trimmed copy of `CSS Logger/cpva_core.py`'s fetch layer, names kept identical. |
| `spfe_fields.json` | **The quantities.** Where the PV names, the units, the rounding steps, the prefilled numbers and the Energy link go. Five rows still have no PV. |
| `spfe_config.json` | Times, share, statistics thresholds, `warn_pct`. |
| `spfe_references.json` | Not shipped — written beside the log, local and shared. `{"limits": {csv column: {min, max, warn_pct, set_at, by}}}`. |
| `spfe_sections.py` | The collapsible coloured panel section, a copy of `CollapsibleSection` from `Image Tools/is_t.py`. Self-contained: four widget classes and two Qt names. Copied rather than imported because `is_t.py` is 23 000 lines for this one widget. Not byte-checked — keep it in step by hand. |
| `daypicker.py` | **A byte-identical copy of `Image Tools/daypicker.py`**, the suite's one calendar. Loaded by path (`_import_daypicker`), never by a plain import: the builder passes only the program's own folder to PyInstaller, and one module name in two program folders makes its module-home check refuse. Edit the master, then copy it here; `testing/test_daypicker_sync.py` fails while the two differ. |
| `testing/` | Eight test files, `conftest.py` (the `tmp` fixture — without it the eleven workbook tests errored out and never ran), `render_window.py`, and two run-by-hand maintenance jobs: `rebuild_workbook.py` and `migrate_dazz.py`. |

No `icon.ico` and no `build_config.json` — the program is not built yet. Both
are needed before Dev Tools ▸ Builder can produce an exe; `openpyxl` will have
to be visible to PyInstaller.

**Visibility.** The Launcher lists a folder only when it holds an `.exe`
(`Launcher/l.py:487`), so this folder is invisible to it. Dev Tools ▸ Builder
lists any folder with a `.py` (`Dev Tools/b_t.py:314`), so it *does* appear in
the Builder's project list; it is simply never built. Renaming the folder
`_SPFE Values` would hide it there too.

---

## The data model

```
spfe_fields.json → Fields → [Row]
Row.columns      → one CSV column per number in the cell
Record           → one moment: when, slot, source, {column: value}
```

A `Row` holds a **list** of PVs, because the paper table does: `BA1Loop4 (X,Y)`
is two numbers, `BA2Loop2 (X,Y,SUM)` is three. They become `ba1loop4_1`,
`ba1loop4_2`, … in the CSV and are judged separately, so "X drifted" is caught
while Y is fine.

**One table line per QUANTITY, with its numbers SIDE BY SIDE.** Every recorded
moment is `parts` grid columns wide, `parts` being the widest quantity's number
count (`SPFEValuesWidget._parts`, `store.parts_per_moment` — derived, never
written down). `_grid_col(moment, part)` is the only place that arithmetic
lives; `store.moment_col(fields, i)` is its twin for the workbook.

`_table_lines` / `_lines_for` still return one `_Line` per NUMBER — that is what
carries the CSV column into `_paint_cell`, `_cell_text` and `_limit_level` — but
a `_Line` is no longer a table row. A single-number quantity is `setSpan(r,
first, 1, parts)`; a two-number one leaves the third box white and empty.

Three shapes in three months, and the reasons matter:

| Until | Shape | Why it changed |
|---|---|---|
| 2026-09-03 | one cell, `150;-250;2500` | unreadable; correcting Y meant retyping all three |
| 2026-09-03 | one LINE per number | readable, but 13 quantities became 22 lines and stopped looking like the paper form |
| now | one line, numbers side by side | the shape of the form people copy from |

The heading therefore has two levels, and the upper one is **not** a
`QHeaderView`: `QHeaderView.paintSection` is clipped to one section, so a name
spanning three of them is simply cut off. `spfe_t._HeaderBand` is a painted
strip above the table instead. It takes its widths from the table after
`_fit_columns` has set them, offsets by `horizontalScrollBar().value()`, and the
table therefore must be `ScrollPerPixel` — Qt's default counts that scrollbar in
whole COLUMNS, and the strip read the number as pixels and drifted. Each table
carries its own band as `table._spfe_band`, so the day page and the View-day
window go through the same `_fill_table`.

`Row.component_names` reads `X,Y,SUM` out of the label, `Row.base_label` is the
label without them, and `Row.part_label(column)` names one number. All three
live on `Row`, so the window heading (`_part_headings`), the workbook heading
(`store.part_headings`) and the Trends legend cannot disagree — and adding a PV
to a quantity cannot leave its name behind. A heading name is only used when
every multi-number quantity agrees on it at that position; where they disagree
the heading is blank and the Detail column's full label — `Input - BA2Loop2
(X,Y,SUM)` — is what says which is which. That is why the table shows the full
label now and not `base_label`.

A row with no `pvs`, or with `manual` set, is typed by the operator — but every
cell of a recorded column can be typed into, PV rows included, so `is_manual`
now decides only which cells are **tinted**, not which can be edited.

`format_value(row, column, values)` prints **one** number, and that is what both
the table and the workbook write into a cell. `format_cell` — every number of a
row joined with `join_text(row)`, `150 ; -250 ; 2500` — survives for the two
places that still want one string: a single-number or free-text cell, and
`rebuild_workbook.py --diff` reading a block in the old layout.

The table no longer needs `parse_cell`: a cell is one number, so
`_on_cell_edited` writes `{column: text}` directly. `parse_cell` stays for the
workbook side and keeps its three rules — a `-` read back is empty (never the
literal string, which would go into the CSV and be silently skipped by
`history_for`), only the numbers actually typed are returned, and the
separators clear the rest on purpose.

The fields for a row:

- `unit` is shown in the **Detail** column (`Row.display_label`), never in the
  value cell — `format_cell(..., with_unit=False)`. Only numbers are ever
  typed, and `_CellDelegate` refuses anything else (C locale: a full stop, not
  a comma). The workbook keeps its unit inside the cell, because a spreadsheet
  column has no label beside it.
- `round` → `Row.round_to`, one step per number of the row, padded to
  `len(columns)` by `_rounding_steps` so a short list cannot read past its end
  at format time. Applied inside `format_cell`, so **screen and workbook cannot
  disagree**, and never to the CSV — the statistics read raw values. `_rounded`
  sends an exact half away from zero on purpose: `round()` sends it to the even
  side and `floor(v/step + 0.5)` sends it upwards, so −365 and 365 would round
  in opposite directions, and half of these quantities are negative.
- `default` is the value a row shows until somebody types over it, and
  `store.apply_defaults` writes it into a record when a moment is recorded —
  otherwise the screen would show numbers the files do not have.
  `Reading.measured` counts the values that came from a PV **before** the
  defaults go in, so "no PV answered, record nothing" still works.
- `default_from` (`Row.default_applies(day)`) is the day a default starts
  counting. Before it the cell is empty and nothing is written: the Spider
  settings and the best GDD were only true from the day they were written down,
  and filling in the past must not invent them. Both `apply_defaults` and
  `_cell_text` go through the same predicate.
- `url` makes the label a link (blue, underlined, `cellClicked` on column 1 →
  `QDesktopServices.openUrl`). Only `energy` has one: the shared energy
  comparison sheet on SharePoint.

`_cell_text` decides what a cell shows: the value; or the default, if it applies
to this day; or **empty** for a hand-typed row and a dash for a fetched one. On
a hand-typed row a dash reads as a value, while an empty box reads as a box
waiting to be filled in — which is what it is.

Slots: `morning`, `evening`, `now`. A `now` column has no history of its own and
borrows the nearer scheduled slot (`slot_for_comparison`).

---

## Recording — there is no scheduler

Nothing runs in the background. `cpva_fetch_last_before_many(pvs, ts)` answers
"what did this PV read at 09:00" long after 09:00, so the program reconstructs a
day whenever it is next opened.

| Path | Where |
|---|---|
| Catch-up | `_catch_up` → `_fill_days(catch_up_start(...), now)`. `catch_up_start` bounds a first run to `catch_up_max_days` back. |
| One chosen day | `_on_fill_this_day` → `_fill_days(day, end of day, one_day=True, asked_for=True)`. |
| Chosen days | `_on_pick_days` → `_ask_for_days` (the house calendar) → `_fill_days(first, end, only=set(days), asked_for=True)`. |
| Live | `_check_slot_due`, a one-minute `QTimer`. Only a convenience; whatever it misses, the catch-up fills. |
| Record now | `recorder.record_now` — this minute, its own column. |

**`_fill_days` is the one backfill body**, and the reason it exists as its own
method: `catch_up_start` returns `max(newest recorded day, floor)`, so the
catch-up can never look at a day older than the log. Nothing in the program
could reach the past — raising `catch_up_max_days` changes nothing once a single
record exists. The two Earlier-days buttons point the same body anywhere.

`asked_for=True` runs it with `weekdays_only` off: the automatic catch-up is
filling in a working week nobody was here for, but refusing a Saturday somebody
deliberately clicked on reads as a broken button. A future moment is still never
produced.

`_ask_for_days` builds a days-only dialog on `daypicker.make_calendar` +
`compute_click` + `MultiSelectDelegate.set_selected`, with the weekday gate set
to Mon–Fri (rule 2: a Ctrl+click still takes any single day). Not
`DayTimePicker`: its per-day From/To would be a control with nothing to control,
because the two moments of a day are fixed here.

---

## The files, and why there are that many

| Path | Read by |
|---|---|
| `<share>\Software\SPFE Values\SPFE values.xlsx` | people — three sheets, see *The three sheets* |
| `<share>\Software\SPFE Values\spfe_log.csv` | the program |
| `<share>\Software\SPFE Values\spfe_campaigns.json` | which day is which campaign |
| `<share>\Software\SPFE Values\spfe_tombstones.json` | the moments somebody deleted |
| `%APPDATA%\SPFE_Values\…` | the mirror that cannot fail |
| `%APPDATA%\SPFE_Values\pending_edits.json` | **local only**: changes not yet on the share |

The workbook is never parsed back — people edit it by hand and a round trip
would be unreliable — so the CSV carries the machine-readable truth. Same split
as `Chiller Log/chiller_archive.csv`, flat and appended.

### Order of writes — `Store.save`

1. local CSV (cannot fail),
2. shared CSV,
3. the workbook, **one `try` per record**.

Anything that fails after step 1 becomes `SaveResult.problem`, a plain sentence
for the status line, and the rows stay queued for `Store.sync()`. `sync()` runs
on every start and on **Sync now**.

Step 3 used to share one `try` for the whole list, so the first locked or
unwritable moment abandoned every day after it and nothing ever went back for
them — which is how a log holding 22 days ended up as 3 blocks in the workbook.
Each record is now caught on its own, the misses are counted into one sentence,
and `sync()`'s `_repair_workbook_blocks` writes a block for every recorded day
the sheet is missing.

Every write holds `Store._io_lock`. A rewrite is read-modify-`os.replace`, so an
autosave's rewrite straddling a catch-up's `append_csv` would drop the appended
record; `save`, `update_values`, `sync`, `set_campaign` and `delete_record` all
hold it.

### The side files — merged key by key

`read_json_map` / `write_json_map` / `merge_day_map` carry both. The merge is
per key and **never removes** an entry: the greater `set_at` (or `at`) wins,
and the stamps are `%Y-%m-%d %H:%M:%S`, so comparing the strings is comparing
the times. Deliberately unlike `all_rows`, which takes whichever CSV is longer —
two people on two PCs each own the days they touched.

- **Campaigns.** Stored only for the day somebody typed on; the carry-forward is
  **computed** by `campaign_for` (newest entry on or before the day), never
  stored, so correcting one day corrects every day after it. An emptied name is
  `""`, which is how a campaign is ended, and is why the entry is a dict and is
  never deleted. It cannot be a CSV column: `append_csv` writes the header only
  into an empty file, so a fifth column would be silently dropped on every read
  of the existing log.
- **Tombstones.** See *Deleting* below.
- **`pending_edits.json`** is local by design. `sync()` only ever pushes rows
  the share is **missing**, so a corrected value would never reach it; the queue
  is written at commit time and replayed by `_replay_pending`. It is also the
  safety net for a program closed mid-save.

### Deleting — `Store.delete_record`

Order alone cannot make this safe. Delete locally and the longer shared CSV wins
in `all_rows`; delete on the share and `sync()` pushes the local row back. So
the **tombstone is written first**, and from that instant the moment is
invisible to every reader:

1. `spfe_tombstones.json`, local then share;
2. `all_rows` drops any tombstoned `datetime` — one line, one place;
3. `append_csv` refuses a tombstoned stamp, and `save` reports it in a sentence
   rather than dropping the record silently (only Record now in the same minute
   can hit this);
4. `sync()` excludes them from `missing` and sweeps both CSVs;
5. then `remove_from_csv` on the share and the mirror, and the workbook column.

Every step is re-runnable, and only `slot == now` can be deleted at all.

### The three sheets

| Sheet | Constant | Owner | Written |
|---|---|---|---|
| `SPFE values` | `SHEET_VALUES` | people | append-only, one moment at a time |
| `Trends` | `SHEET_TRENDS` | the program | wiped and rewritten every save |
| `Charts` | `SHEET_CHARTS` | the program | wiped and rewritten every save |

`_values_sheet(wb)` — **never `wb.active`**. Excel remembers whichever sheet was
last shown, so a day's values written into `wb.active` could land on top of a
chart's data.

### Workbook layout — the day sheet

```
A            B                          C      D      E       F      G      H
2026-09-01 | 77 Borghesi / E5 Spadova | Morning (C:E, merged) | At the end (F:H)
           |                          |  X  |  Y  |  SUM  |  X  |  Y  |  SUM
SPFE       | Pumplaser DAC            |     24500 (C:E)     |    24500 (F:H)
XPW        | Input - BA2Loop2 (X,Y,SUM)| 150 | -250| 2500  | 160 | -260| 2500
…
Notes      | Notes                    | free text (C:E)     |
```

- **A block is two heading lines** (`BLOCK_HEAD_LINES`): the moment's name
  merged across the columns it owns, and under it `part_headings(fields)`. Every
  row index that used to be `header_row + 1 + i` is `header_row +
  BLOCK_HEAD_LINES + i`.
- **A moment is `parts_per_moment(fields)` columns**, `moment_col(fields, i)`
  giving the first of them: Morning C:E, At the end F:H, extras from I.
  `MAX_EXTRA_COLS` counts moments, not columns.
- `_style_value_row` merges a single-number quantity across the whole moment, so
  there is no empty box beside it — the same rule as on screen.
- Every merge this module makes is undone by `_unmerge_run` before it is made
  again. Everything but the top-left cell of a merged range is a `MergedCell`
  whose `.value` is **read-only**, and a block is written into more than once (a
  second moment, a correction, a repair).
- `_block_is_old_shape` refuses a block written before the split — one heading
  line, `150;-250;2500` in one cell. Values go in by position, so writing into
  one would put every number a line too high; the Log says so and names
  `testing/rebuild_workbook.py`, and the CSV has the values either way.
- Group label merged down its rows in column A.
- **The campaign is column B of the header row** — the one cell of the day
  header with nothing else to say. An existing block's B is only overwritten by
  a non-empty name, so a name typed straight into Excel cannot be blanked by a
  program that happens not to know it. `write_workbook_campaigns` refreshes
  every block on the sheet from the merged map (carried names included) and is
  idempotent, so it can run on every sync.
- A day with **no records gets no block**, campaign or not: blocks are appended
  at the bottom, so one created out of turn would put a later day above an
  earlier one and destroy the one thing the workbook is for. Such a day keeps
  its name in the side file until its first value is recorded.
- Morning is **always** the first moment, At the end **always** the second, so
  the days line up.
- `_target_column` answers three questions in order: the two fixed moments; then
  **a moment already written keeps its own place**, found by its heading (it
  used to look for "the first free column", so every correction of a recorded
  extra moment landed in a new column and left the stale value where it was);
  then a new moment goes **after the last one used**, so a deleted moment stays
  a gap and 16:05 cannot end up to the left of 14:32.
- `clear_workbook_column` empties a deleted moment's columns: values,
  **comments** (clearing a value does not remove the out-of-range comment, and a
  red marker explaining a number that is gone is worse than nothing), fills,
  borders, fonts, alignments — and the **merges**, or a merged range left behind
  keeps drawing a box round columns that are supposed to look untouched. The
  header cell's value is load-bearing: a moment only counts as unused again once
  it is `None`. Never `delete_cols`.
- **Nothing is inserted, ever.** `ws.insert_cols` would shift every other block
  on the sheet; instead other days simply leave their extra moments empty. That,
  plus writing only the target day's block, is why a note typed straight into
  Excel survives.
- Only the two scheduled moments get borders when a block is created; an extra
  one is styled when it is first used (`_style_value_row`), so a day with no
  extra moment does not trail empty boxes to the right.
- **Colour per NUMBER.** `write_workbook_record` calls `reference_verdicts` and
  paints each cell for itself, so an XPW loop whose SUM is out of range turns
  the SUM red and leaves X and Y white. `row_reference_level`, which rolled the
  three verdicts into the worst one because the cell could only have one colour,
  is gone — and so is `spfe_record.judge_record`, which did the same for the
  statistical remark. `flags` reaching `write_workbook_record` is now
  `{CSV column: sentence}`, exactly what `judge_columns` returns and what the
  window already used.
- `_save_atomic` writes `*.saving.xlsx` beside the target and `os.replace`s it,
  and turns a `PermissionError` from either step into `WorkbookLocked` — which
  the caller reports as "open in Excel, the values are saved".

- `_ensure_block_shape` runs before a value is written into a block that already
  exists. Values go in **by position**, so a block written when the quantity list
  was shorter would take everything from the new row down one line too low — the
  last value onto the blank separator. It compares the labels in column B, then
  inserts or deletes lines at the bottom of the block and calls `_relabel_block`.
  Two traps it exists around: an empty label comes back out of the file as
  `None`, not `""` (humidity), and `openpyxl` does not move merged ranges when
  rows are inserted, so the group column is unmerged first — the unmerge range
  now has to **start below the heading lines**, or it would tear the moment
  names apart.

### The Trends and Charts sheets — `write_workbook_trends`

The day blocks cannot be charted. Each day is its own block with a blank line
between, so one quantity across thirty days is thirty separate cells, and an
`openpyxl` chart can only point at a `Reference` range. So the same numbers are
laid out a second time.

- `trend_columns(fields)` is the list of curves: `(group, heading, CSV column,
  slot)`, **one per number PER SLOT**, so morning and end-of-day are two columns
  of the same quantity and each chart draws them as two curves. Free-text rows
  are skipped; everything numeric is in, hand-typed settings included ("when did
  the GDD change?" is what the sheet answers).
- The Trends sheet is **one line per DAY**, column A the date. `_as_number`
  turns a stored string into a real number — a text cell charts as a zero.
- `Charts` gets one `LineChart` per group, `set_categories` on column A.
  `chart.x_axis.delete = False` and the same for `y_axis`: on a chart built from
  scratch the flag defaults to True and Excel draws neither axis.
- Both sheets are **deleted and recreated** on every call, so they cannot go
  stale and cannot accumulate a second set of charts. The price, stated in both
  ReadMes: a chart somebody adds by hand there is lost at the next save.
- Called from `Store._refresh_trends`, at the end of `Store.save` and at the end
  of `Store.sync` (a day recorded on another PC has to reach the charts even
  when this PC pushed nothing). It swallows everything: the values are already
  in the CSV and in the day blocks by then, and a picture that could not be
  redrawn is not worth losing them over.

`Store.update_values` is the one path that rewrites rather than appends: the
operator correcting a value. It patches named columns of named rows in both
CSVs and re-writes those workbook columns. Three things it has to do that it
did not:

- `_seed_local_rows` first copies in any row only the share has — `all_rows`
  prefers the longer file, so the screen may well be showing somebody else's
  records, and the patch would otherwise find nothing to patch;
- `rewrite_csv` **returns the number of rows it patched**, and a zero is
  reported instead of "saved" (it used to return early on an empty file and on
  a stamp it did not hold, while the caller reported success either way);
- the workbook record is rebuilt from the **local** CSV, which has just been
  patched and cannot fail. Reading the share instead silently skipped any
  moment the share did not have.

---

## Typed values save themselves

There is no Save button. `_on_cell_edited` writes the cell as soon as the
editor closes — Enter, Tab or a click elsewhere. Each of the following is
load-bearing:

- **Roles, not index arithmetic.** Every value item carries `_ROLE_KEY` (the
  quantity), `_ROLE_COL` (**which number of it** — the CSV column, which is
  what the edit is written to), `_ROLE_STAMP`, `_ROLE_TEXT` (the text it was
  filled with), `_ROLE_HELD` and `_ROLE_LEVEL` (the reference verdict the
  delegate paints), set before `setItem` so they cost no extra signal.
  `_ROLE_TEXT` is the
  **no-op guard**: a commit that changed nothing writes nothing, which is the
  one thing standing between a rounded cell and the measured value it was
  rounded from. Index arithmetic would also break on an empty day, where the
  table has one placeholder column and no records at all.
- **`_fill_depth` is a counter**, not a flag. Filling writes every cell, and a
  nested fill clearing a flag in its `finally` would un-guard the outer one.
  Every write outside a fill goes through the same counter (`_repaint_column`).
- **The save must not rebuild the table.** Qt destroys an open editor without
  committing it, so `_queue_edits` patches the record, `self._rows` and the
  cells in place instead of calling `_reload_rows`; `_refresh_today` returns
  early while the table is in `EditingState`, sets `_refresh_wanted`, and the
  delegate's `destroyEditor` calls `_editor_closed` to run it. Without this,
  Record now, the catch-up and the start-up job each eat whatever is being
  typed.
- **Not through `_run`.** Its "something is already running" refusal would drop
  an edit for the seconds-to-minutes the start-up job holds `_working`. Instead:
  `_pending`, a 400 ms single-shot `_save_timer` (one share rewrite for a row
  of cells tabbed through, not one per cell), and one long-lived writer that
  re-arms the timer while `_pending` is not empty. Entries are removed only
  after a successful write.
- **A failure keeps the value.** The local mirror is written first and cannot
  fail; the status line says what did. Reverting the text under the operator is
  the one way to actually lose it.
- **The first change to a day carries the day's held defaults with it**, which
  the Save button did as a side effect and `test_typed_values` pins. `_ROLE_HELD`
  is how a held default is told from a stored value, so a default the operator
  deliberately blanked is not resurrected.
- `shutdown()` commits the open editor, flushes `_pending` into
  `pending_edits.json` (local, instant, replayed by the next `sync`) and only
  then waits up to two seconds for the writer. The writer is a daemon thread and
  dies with the interpreter, so the queue file is what makes closing safe.

A `PENDING` column has no `_ROLE_STAMP`; typing into one goes to
`_create_manual_column`, which builds a `source="manual"` record for that slot
and saves it. That is how a day the archiver cannot answer for is written down
by hand — and every column of a day older than the log is such a column.

---

## Reaching the share

`SHARE_CANDIDATES` is office-leg-first, then lab. `_first_reachable` is copied
from `Diagnostic/shared_pvs.py:103` and the reasoning is unchanged: one **daemon**
thread per root, return as soon as the highest-priority candidate is known good,
never wait for all probes. A single `os.path.isdir()` on the unreachable leg
blocks ~48 s (INFRASTRUCTURE §2), and a `ThreadPoolExecutor` would move that
stall to shutdown because its workers are joined at exit.

The winning root is cached in `cfg["_last_share_root"]`, so the next start costs
one check of a host known to answer.

---

## The out-of-range rule — `spfe_stats.judge`

Statistical only; nothing is configured per quantity.

```
history = the last `history_days` values of the SAME slot, excluding this day
if len(history) < min_history        → no verdict at all
spread  = MAD(history) × 1.4826
if spread == 0   (a setpoint)        → flag when |v − median| / |median| > flat_tolerance_pct
else                                 → flag when |v − median| / spread > mad_factor
```

Three decisions worth keeping:

- **Median/MAD, not mean/σ.** One bad day inflates σ enough to hide the next
  one. `test_one_bad_day_does_not_hide_the_next` pins this.
- **The flat case.** *Pumplaser DAC 24500* is identical every day, so MAD is 0
  and a plain σ rule makes a one-count change infinitely many σ out. This is the
  single most likely source of false alarms in the whole program.
- **Mornings only against mornings.** The two slots are different operating
  points; pooling them widens the spread until nothing ever looks wrong.

Nothing is ever deleted. A flagged value is written, the cell goes amber **with
red bold ink** (Chiller Log marks a bad row in `#E53935`), and the reason lands
on the cell and in the Log window.

Verdicts are **recomputed on the way to the screen** (`_flags_for`), not stored:
the history grows every day, so a value can stop being unusual, and a frozen
verdict would keep an old alarm on screen forever. `_refresh_all` clears the
cache.

**One shape only**: `judge_columns` → `{csv column: reason}`. There used to be a
`judge_record` beside it, collapsing the three verdicts of a quantity into one
sentence, because the workbook had one cell for all three numbers. It has three
now, so both consumers take the per-number form. Marking all three numbers
because one is unusual is how an alarm stops meaning anything.

---

## The reference rule — `spfe_limits.judge`

The other half, and the one `spfe_stats` structurally cannot do: a quantity
drifting out of spec over a fortnight is never "unlike the other days", because
by then the drift **is** the history. A reference is a person saying what the
number is allowed to be.

```
entry = {"min": lo|None, "max": hi|None, "warn_pct": pct|None}   per CSV column
v < lo or v > hi                     → "bad"
within band of an edge               → "warn"
band = pct% of (hi − lo), or of the one edge when only one is set
```

Decisions worth keeping:

- **Per number, not per quantity.** Keyed by the CSV column (`ba2loop2_3`), so
  the SUM of counts and the X in micrometres get their own ranges.
- **Half a reference is a reference.** Either edge may be missing; both missing
  means the number is never coloured, which is where every number starts.
- **The band is a percentage of the RANGE**, not of the value: 5 % of
  100-to-200 is five at both ends. With one edge there is no width, so it is
  5 % of that edge — and of the value itself when the edge is 0.
- **A reversed range is swapped, not refused** (`normalise`). It is what
  happens when the two boxes are filled in the order they come to mind.
- **Nothing unusable is ever an alarm** (`as_number`): an empty cell, a dash, a
  word, a NaN, an infinity all return no verdict. A typo in one range must not
  take the colouring off everything else.

**Precedence, and why.** A reference is somebody's decision about the machine;
the statistical remark is only "this differs from the other days". So the
reference colours the cell and the remark joins the same tooltip underneath —
`_paint_cell`: `bad` → `warn` → statistical amber → the "yours to fill in"
tint → paper.

**The colours, and the outline.** `warn` is `#FFEB3B` with black bold text — a
light fill keeps black ink. `bad` is `#C62828`, which is dark, so the number
turns white *and* gets a black outline stroked round it by
`_CellDelegate._paint_out_of_range`. A `QTableWidgetItem` can only be given one
ink, and plain white on that red is the pair that goes grey on a projector and
on a printout. The stroke goes down **first** and the white fill over it:
`drawPath` with both a pen and a brush centres the stroke on the glyph edge, so
half of it lands inside the letter — at 10 pt that is the whole letter, and the
number came out a black smudge.

**Storage.** `spfe_references.json`, local copy first and then the share, merged
by `merge_day_map` on `set_at` exactly like the campaign names — number by
number, so two people setting two different quantities cannot wipe each other.
`Store.set_references` writes the **whole** table, because it comes from a
window where every line is on screen at once; merging a half table would revive
a range somebody cleared on purpose. `sync()` merges both ways
(`_sync_references`).

`Store.reference_args()` is read **once before a loop** over records:
`references()` opens two files, and filling in a day writes sixteen records.

**In the workbook**: the same `reference_verdicts`, per number — red fill with
white bold text, yellow with black, and the reason in the cell's note. The
`row_reference_level` that used to roll them into the worst one is gone with the
cell that forced it.

---

## What was not copied from Chiller Log

| Chiller Log | Here |
|---|---|
| One dead PV aborts the whole update; every fetched value is discarded (`cpt.py:2587`). | Each PV is caught on its own; the cell is empty and the rest of the column is recorded. |
| `open(ARCHIVE_FILE, "a")` outside any try — a workbook open in Excel loses a multi-minute run (`cpt.py:2760`). | Local mirror first, then the share; a locked file is a delay with a sentence, never a loss. |
| `save_config` returns early when the file does not exist, so a fresh install silently never persists (`cpt.py:184`). | `save_config` creates it. |

The pump gate (`cpt.py:2633`) has **no equivalent here** and none was invented.
There is no "has it settled" signal for these quantities, and a guessed one
would silently drop good data.

---

## UI notes

- **Windows here runs in dark mode**, so anything left to the theme is painted
  black. Three things follow, and all three were black on the first attempt:
  a plain `QWidget` **ignores a stylesheet background** unless it carries
  `WA_StyledBackground` (`_styled_ground` — the page, both splitter halves and
  both dialogs), scroll bars and tooltips need their own rules
  (`_GROUND_STYLE`), and everything a stylesheet cannot reach needs the palette
  (`_light_palette`, inherited by every child, which is what keeps a message box
  light). `main.py` paints the `QMainWindow` too.
- **No tab bar.** One `QSplitter`: the toolbar on the left, the day heading and
  the table on the right, the status line underneath. What used to be the
  History and the Log tab are windows opened from the toolbar — `_open_view_day`
  and `_open_log`. A tab bar here would end up nested inside the host program's
  tab bar.
- `self.txt_log` is built in `__init__`, **before** `_build_ui`, and its window
  is created on first use and only ever hidden. `_append_log` has to work from
  the first second, whether or not anybody has opened the Log window.
- The View day window keeps its own read-only `hist_table` and reuses
  `_fill_table(..., editable=False)`. `_refresh_day_list` returns immediately
  when the window has never been opened, so `_refresh_all` costs nothing extra.
- One `_Bridge(QObject)` for the whole widget, created once. A per-worker signal
  object parented to the widget is never destroyed and leaks a few kB per job —
  hundreds of MB of commit over a week in a program left open.
- `_start_up` paints the local log **before** touching the network. The share
  probe plus a catch-up can take seconds, and an empty table for that long reads
  as "the log is gone".
- Every colour is set explicitly, including the foreground of any cell that gets
  a background. `_paint_cell` is one method for exactly that reason — a cell is
  painted twice, when the table is built and again after a value is saved, and
  the two must not drift. Its precedence is fixed: outside a reference (red,
  white outlined text) beats near a reference (yellow, black bold) beats the
  amber statistical flag (red bold ink) beats the `TYPED_BG` tint — an alarm
  always wins, and the tint only says "this one is yours to fill in".
- The tint marks `is_manual` rows of recorded columns. It is not "editable" —
  every cell of a recorded column is editable — and it is skipped in a `PENDING`
  column, or a tinted cell would sit in every un-recorded Morning.
- Missing scheduled columns are shown as empty `PENDING` columns so the table is
  the same shape as the paper form; typing into one creates it, and they are
  never handed to `update_values`.
- **Nothing stretches. Every column is measured** (`_fit_columns` →
  `_text_width`, with each item's own font) and then held between a floor and a
  ceiling of its own. What is left of the window goes into an **empty filler
  column at the far right**, which is the whole trick: the table still fills
  its frame, so there is no bare strip beside it, and no real column has to be
  inflated to make it do so. Before this, the slack went to **Detail**, and a
  1 cm label sat in a 10 cm box. Values are `VALUE_PT` (10 pt) against the
  page's 9 pt — 11 pt in a 165 px column was half a metre of white with a
  four-digit number in it. `_CELL_PAD` is 24, measured.
  **All sub-columns of one moment come out the same width.** They have to: the
  moment's name is painted across them by the header band, and a name centred
  over three boxes of different widths is not centred over anything. So the
  measuring loop is per moment, it skips cells that are merged across the whole
  moment (a single-number quantity asks each sub-column for its share, not for
  all of it) and free-text rows (a note wraps; it never sets a width), and it
  finally makes room for the moment's own heading. The leftover goes to
  **Detail** first, then to whole moments in equal steps, then to the filler.
- **Row heights are measured too** (`_measure_rows`, run **after** the widths,
  because a note wraps inside its own cell). The free-text row is measured at
  the **merged** width of each moment, not at one sub-column. The label columns
  are measured as well, so a long label wraps instead of being cut; nothing is
  merged downwards any more, so a label asks for all of what it needs.
- `_fit_rows` shares the leftover height out but never past `_ROW_GROW_MAX`
  (1.5×): sixteen short lines in a tall window used to become sixteen tall
  empty boxes. What the cap leaves over is given back by
  `_cap_table_height` — the table ends below its last row and the page's grey
  carries on, which reads as "the table stops here" rather than as a missing
  row. That method sets `setMaximumHeight` **only when the value changes**: a
  widget given the maximum it already has asks its parent to lay out again, and
  a resize is what called it, which is a relayout loop that pins a core at
  100 %.
  The page layout has to cooperate with that cap: the table is added with
  stretch **1000** and a plain `addStretch(1)` follows it. With nothing able to
  expand, `QVBoxLayout` shares the slack out **between** the widgets — the date
  band, the heading strip and the table each floated in a band of bare grey; and
  with the table and the spacer both at stretch 1 they split the height evenly
  and the last two rows fell off the bottom. 1000-to-1 serves the table first
  and gives the spacer only what the cap refuses.
- **The delete lives on the column** — right-click the table, its header, or the
  moment name in the heading strip (`_on_header_menu` / `_on_table_menu` /
  `_HeaderBand.menu_wanted` → `_column_menu`). `_column_at` **divides** by
  `_parts()` now: a moment is three grid columns, not one.
- The **References window** (`_open_references`) is built once and hidden, like
  the other two. `_fit_ref_columns` runs from the resize filter as well as from
  `_fill_references`, because on the first fill the dialog has not been laid
  out and its viewport is not the width it will end up being — the filler came
  out as nothing. Its Save writes on its own thread (`spfe-refs`), not through
  `_run`, whose "something is already running" refusal would drop it while the
  start-up job holds the program.
- Deleting a moment is offered only for a recorded `SLOT_NOW` column, and always
  behind a `QMessageBox` naming the day and the time, because nothing in this
  program can undo it.
- Long share paths are elided **in the middle** — the machine name at the front
  and the folder at the back are the two halves that identify the share.
- **The group rule is drawn, not left to the grid.** `_CellDelegate` paints a
  2 px `LINE_DARK` line above every row that starts a group (`_group_starts`,
  filled in by `_fill_table` and shared by both delegate instances). The grid
  alone draws every row the same, so Dazzlers ran straight into XPW. A row
  delegate replaces the table's own, which is why the number editor and the line
  live in **one** class — a `setItemDelegateForRow(r, None)` would lose the line
  on that row.
- **The table fills its frame.** `_fit_rows` spreads the leftover height over
  the rows in proportion to their natural heights (30 px, 76 px for the free-text
  row), recomputed from `table._spfe_row_heights` on every resize — never from
  what is on screen, or each resize would add to the last one. An event filter on
  each viewport calls it. Without this the table stopped halfway down and left a
  bare white field that reads as something missing.
- **The date sits in a band** (`_DAY_BAND`, no gap below it), so it is the
  table's top edge rather than a line dropped on its corner. The band is
  `DAY_BAND` `#C9C9C9`, a step darker than the table's own `#E0E0E0` header and
  matching the workbook's day fill, so the line that starts a day is the
  heavier one.
- The band is a **container `QWidget`**, not the old bare `QLabel`, because the
  campaign box shares it. Two consequences, both of them the dark-mode rule
  again: `_DAY_BAND`'s selector had to become `QWidget` and the container needs
  `_styled_ground`, or the theme paints it black; and the `QLineEdit` gets its
  own explicit QSS (`_CAMPAIGN_EDIT`).
- **The campaign box commits on `editingFinished`** (Enter *and* focus-out) and
  compares against `_campaign_shown` first, because that signal can fire twice
  for one name. Never `textChanged`: that is one share write per keystroke.
  `_show_campaign` returns immediately while the box has focus — a background
  `_refresh_all` would otherwise throw away what is being typed — and a carried
  name is shown as `placeholderText` so it reads differently from the day's own.
- **Two short buttons share a line** (Previous/Next, Today/View day, Morning/At
  the end). One full-width button per line wastes the height and makes every
  button look equally important. The button text is 10 pt with tight padding: a
  9 pt label inside a tall bar reads as an empty button.
- **The campaign box takes the width of the band.** `QSizePolicy.Expanding`,
  min 360, max 720, and the `addStretch(1)` that used to sit beside it is gone —
  a real name is about sixty characters ("77 Borghesi / E5 ELI70157 Spadova /
  electrons and plasma") and only a third of it fitted in the old 220–340 box.
  The band itself is `QSizePolicy.Fixed` vertically, or the page's leftover
  height goes into it and it becomes a hand's breadth of bare grey.
- **The toolbar is collapsible coloured sections**, the Image Tools idiom:
  `spfe_sections.CollapsibleSection`, a copy of the class in `Image Tools/is_t.py`
  (96 self-contained lines — this program must not import a 23 000-line module
  for one widget; same reasoning as `daypicker.py`, without the byte-for-byte
  rule). "Record now" stays above them, Expand all / Collapse all under it, Log
  alone at the foot. Which sections are open lives in
  `%APPDATA%\SPFE_Values\spfe_ui_state.json` (`_load_ui_state` /
  `_save_ui_state`, both of which swallow everything — a panel that will not
  open because a settings file went bad is worse than one that opens with
  everything showing). The sections sit in a transparent `QScrollArea`: folded
  open they are taller than a short window, and a panel that cannot scroll just
  squashes them. The five `QGroupBox`es and `_group_style` are gone.

---

## Tests

All headless, no network, no share. The scratch share is never touched:
`render_window.py` redirects `APPDATA` **and** overrides `SHARE_CANDIDATES` to a
temporary folder.

```
python testing/test_spfe_stats.py      the rule, incl. the flat/MAD==0 case
python testing/test_spfe_limits.py     the references: in / near / out, half a
                                       range, a reversed range, nothing usable
python testing/test_spfe_store.py      fields, rounding, parse_cell, default_from,
                                       the component names, CSV, workbook blocks,
                                       one cell per number, the old-layout guard,
                                       the Trends sheet and the charts, locked
                                       file, delete + tombstone, campaigns,
                                       references, repair
python testing/test_spfe_record.py     judging a whole column, one verdict per
                                       number, component naming
python testing/test_cold_start.py      first run: no log, no share, missing PVs
python testing/test_typed_values.py    typing: autosave, the no-op commit, one
                                       number of three as its own cell, the
                                       reference colours, a day written by hand
python testing/test_pick_days.py       the calendar dialog and the day spans
python testing/test_daypicker_sync.py  daypicker.py is still the master, verbatim
python testing/render_window.py        the page, View day, References and Log
                                       into _window*.png
```

`testing/rebuild_workbook.py` is not a test but a **run-by-hand maintenance
job**: it writes the whole sheet again from the log, with the days in date
order. The workbook is append-only on purpose, so a day that was missed lands
at the bottom — which is what happened when the interrupted first run left 3 of
23 days in the file and `sync()` then added the other 20 underneath them. Run
`--diff` first: it lists every cell the log cannot reproduce, and anything there
that is not just the new rounding is somebody's hand typing and would be
destroyed. It backs the workbook up into `%APPDATA%\SPFE_Values` and replaces
the shared file with a move, so a failure leaves the old one untouched. It reads
a sheet in **either** layout, so it can still diff a file written before the
numbers were split.

Run twice on 2026-09-03: once to put twenty August days back in date order, and
once when the numbers were split into cells of their own — the two layouts
cannot be mixed, so the whole sheet had to be written again. The 192 differences
before the second run were of exactly two harmless kinds (the joined separator
had already become `" ; "`, and DAZZ1 had lost a number it never had); after it,
`--diff` reported none. **Run `--diff` before AND after.**

`testing/migrate_dazz.py` is the other run-by-hand job of that day: dropping a
PV from a row renames its log column (`dazz1_1` → `dazz1`), which nothing in the
program can guess. It backs up every file it rewrites and is safe to run twice.
Any future change of that kind needs its own script.

`test_spfe_store`'s formatting checks build their own `Row` objects instead of
reading the shipped field file: the rounding steps and the units there are the
operator's to change, and a test that pins them stops them from being changed.

`render_window.py` insists on `QT_QPA_PLATFORM=windows` — offscreen Qt has no
fonts and lies about text size, so it cannot be used to check how anything looks.

---

## Becoming a page of another program

The intended host is **Chiller Log**, the sibling slow-log program, which is to
be renamed once the two are joined. That merge is blocked on one thing and it is
not small: `Chiller Log/cpt.py` is **tkinter** (3156 lines, `ttk.Notebook`,
`ttk.Treeview`, two hand-drawn calendars) and this widget is Qt. One of the two
has to change toolkit. Until somebody decides which, this program takes on
Chiller Log's *look* and keeps its own window.

The Qt route below is still written down, because dropping the widget into a Qt
host (CSS Logger) is a ten-minute job and would work today:

1. Copy `spfe_t.py`, `spfe_record.py`, `spfe_store.py`, `spfe_stats.py`,
   `spfe_limits.py`, `spfe_sections.py`, `spfe_fields.json`, `spfe_config.json`
   into `CSS Logger/`. Leave `main.py` and `spfe_core.py` behind — and
   `daypicker.py`, which is already there and byte-identical, so the by-path
   loader finds the host's copy unchanged.
2. `spfe_t.py` already prefers `cpva_core` over `spfe_core`; `spfe_record.py`
   needs the same two-line adapter.
3. `CSS Logger/main.py:7766` — one `addTab` inside a `try/except` that
   substitutes a red error label, exactly as Spectra does.
4. `CPVASuiteWindow.closeEvent` (`main.py:7807`) — add
   `self._spfe.shutdown()` beside `self._spectra.cancel_scan()`, or the
   one-minute timer keeps the suite awake.
5. Docs follow the tab convention: `ReadMe_SPFE tab.txt`,
   `ReadMe_SPFE tab_Full.txt`, `STRUCTURE_SPFE_tab.md`.

`spfe_core.py` is a deliberate copy of `cpva_core.py`'s fetch layer
(INFRASTRUCTURE §7: one module, one home — no shared library folder). It exists
only so the program runs standalone, and disappears at step 1.
