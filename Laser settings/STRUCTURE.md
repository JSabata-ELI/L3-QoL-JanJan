> Verified against source: 2026-10-05 · main.py 581 L · ls_core.py 2238 L ·
> ls_cpva.py 456 L · ls_engine.py 248 L · ls_ui.py 1537 L · ls_log.py 174 L ·
> ov_t.py 1258 L · st_t.py 880 L · in_t.py 159 L · ft_t.py 1155 L

# Laser settings — developer notes

Five halls, one shared list of watched channels, and per-hall limits. The
program reads the archiver once a second and paints a verdict. It writes
nothing to the machine.

## The files

| File | What lives in it |
|---|---|
| `main.py` | Taskbar identity, the look, the tabs, the settings file, `log()`, shutting down. |
| `ls_core.py` | **Every rule.** The halls, the inclusive range test, what a missing reading means, the formulas and `row_values`, the settings document and its repair (the look, the band, the arrangement), the channel search, the archiver's failures in plain language. No Qt. |
| `ls_cpva.py` | The archiver. Copied from `Announcer/ann_cpva.py` (itself from `Image Tools/cpva_client.py`): pooled keep-alive HTTPS, one retry, `CpvaError` vs `CpvaBusyError`, `LastReader`, `PassReader`. No Qt. |
| `ls_engine.py` | The clock. One pass a second, single-flight with a generation number, a watchdog on a pass that never returns. Also looks at the L3BT MSS window on its own thread while `C.screen_wanted` says so. |
| `ls_screen.py` | The stopgap for SGV502–505: finds the window titled exactly "L3BT MSS", photographs it with `PrintWindow` (plain ctypes, adapted from `Screenshots/s.py`), and reads the four O/C boxes. Pure-Python pixel reading — no PIL, no numpy, no Qt. |
| `ls_dm.py` | The deformable mirror: finds the Remote Desktop window to the mirror's PC (title contains `192.168.1.107`, `TscShellContainerClass` preferred), photographs it with `ls_screen.grab_window`, finds Mirror Control Window's right-hand **Set-up** 6 × 6 grid anywhere in it and reads the 36 cell colours. Matches them against the saved set-ups; writes / reads `dm_state.json` and `dm_setups.json` on the scratch share (`exchange`). Pure Python, no Qt. See "The deformable mirror" below. |
| `dm_t.py` | **DM settings** tab: the set-up the mirror is on (big banner), the grid as read beside a saved one with the differing electrodes outlined (`DmGrid`), the shared list of saved set-ups with Save / Rename / Delete. |
| `dw_t.py` | **DM writer** tab: the per-PC switch that makes this PC (OPR1) the one that reads the window, the title text, what this PC saw last and how long the look took. |
| `ls_ui.py` | The look. Copied from `Announcer/ann_ui.py`, plus this program's two painted widgets: `HallSquare` (which also writes the SHOOTING / idle word inside itself) and `VerdictMark`. |
| `ls_log.py` | The crash log. Copied from `Announcer/ann_log.py`. |
| `ov_t.py` | Overview tab: `_HallBand` (the strip the five squares are placed in, by hand), and the readings as a LIST of row widgets — the name, the verdict square, the number, and nothing else. No table, no heading row; see "the list is not a table" below. |
| `st_t.py` | **Limits** tab: the hall switcher, the gate valve channel, the limits table with its letters and formulas, the channel picker. |
| `in_t.py` | The Info / Log page — built, kept working, and NOT on the tab bar. `ls_core.status_line` and `read_clock_text`, the settings and log paths, and every line `main.log()` has said. |
| `ft_t.py` | **Settings** tab: `_LayoutBoard` (the free-form board the five squares are arranged on) and the band's own settings, plus the typeface, the size, bold and the square size of the Overview's list with a live example built from `ov_t._Row` itself. |
| `build_config.json` | Read by `Dev Tools/b_t.py`. All four tab modules are hidden imports — `main.py` loads them by name, `in_t` included. |
| `testing/` | `test_core.py` (the rules), `test_screen_hall.py` (the MSS window's valve boxes, from `mss_window.png`), `test_window_builds.py` (the window and every tab, offline and offscreen), `test_hall_layout_drag.py` (the board driven through its own mouse handlers), `test_fit_to_window.py` (the fit, at four window sizes, against a real window that is never shown), `test_edit_in_place.py` (an edit repaints in place, the wheel guard, the Maximise debounce), `test_row_order_drag.py` (drag order, the Scanning box, rename on the Overview), `render_tabs.py` (`overview_idle.png` = the orange squares) (pictures, nothing shown on screen — including `overview_watching_int*.png`, the fallback, which is the only place the white "watch" square can be seen, plus `overview_big.png` / `overview_tiny.png` for the fit at both ends, `limits_tiny.png` for the squeezed Limits tab, `overview_arranged*.png`, `settings_tab_arranged.png`, `info.png` and `font.png`), `make_icon.py`. |

## The tabs, and the one that is not there

Five tabs on the window, in this order, and the module names do **not** all
match the captions:

| Caption | Module | Class |
|---|---|---|
| Overview | `ov_t` | `OverviewTab` |
| Limits | `st_t` | `SettingsTab` |
| DM settings | `dm_t` | `DmTab` |
| DM writer | `dw_t` | `DmWriterTab` |
| Settings | `ft_t` | `FontTab` |
| *(not shown)* | `in_t` | `InfoTab` |

Renamed on 1.10.2026 — "klidně font přejmenuj na settings a dej to tam,
settings přejmenuj na limits". The FILES kept their names deliberately: a
module has exactly one home in this repo, and renaming two of them would cost
`main.py`, `build_config.json`, both ReadMes, `render_tabs.py` and two test
files for a change nobody can see. Each module's docstring says which tab it
is, and so does this table.

`in_t` is built FIRST and then hidden: `main.log()` writes into it and every
other tab's failure is reported through `log()`, so it has to exist before any
of them is built. `_add_tab(..., shown=False)` does the hiding, and it hides it
EXPLICITLY — a QWidget parented to the main window but in no layout paints
itself over the top left corner of the window otherwise. To put the tab back,
hand `_add_tab` the three arguments the way the others are handed them.

Three of the modules are deliberate copies of Announcer's. The repo keeps no
shared library — a module has exactly one home, the folder of the program that
ships it — so keep them in step by hand when the master changes.

## The data

One document, `%APPDATA%\LaserSettings\laser_settings.json`:

```json
{
  "version": 1,
  "shooting": {
    "Int": {"open": "L3-PSS:SGV501_IN_OPEN_POSITION"},
    "E2":  {"open": "L3-PSS:SGV502_IN_OPEN_POSITION"}
  },
  "rows": [
    {"id": 1, "pv": "L3-FE-PULSER:Delay", "name": "Front end delay",
     "unit": "ns", "show": true,
     "limits": {"E2": {"min": 10.0, "max": 20.0}, ...}},
    {"id": 7, "pv": "", "name": "Compressed energy", "unit": "J",
     "show": true, "expr": "A * 0.749", "bind": {"A": 1},
     "limits": {"...": "..."}}
  ],
  "look": {"family": "Segoe UI", "size_px": 19, "bold": true,
           "mark_px": 30},
  "halls": {"auto": false, "band_px": 150, "show_word": true,
            "word_px": 11, "fit": true, "band_frac": 0.3,
            "manual": {"size_px": 19, "mark_px": 30,
                       "band_px": 150, "word_px": 11},
            "layout": {"Int": [0.02, 0.05, 0.22, 0.90], "E2": [...]}},
  "shots": {"reset_s": 10},
  "scanning": false,
  "active_hall": "E2",
  "window_geometry": [x, y, w, h]
}
```

`look` is the Overview list's own look, owned by the Settings tab.
`LOOK_DEFAULTS` holds the four defaults, `LOOK_LIMITS` the bounds (10–40 px of
text, 12–60 px of square) and `look_of(cfg)` hands out the live dictionary after
repairing it — `_clean_look` pulls a size of 500 back to 40 and an empty
typeface back to Segoe UI, so a hand-edited file cannot leave the first tab
unreadable with no way back except editing the file again.

`halls` is the BAND the five squares live in, and `halls_of(cfg)` /
`_clean_halls` are its `look_of` / `_clean_look`: same shape, same live
dictionary, same repair on the way in. `HALLS_DEFAULTS` and `HALLS_LIMITS`
(60–420 px of band, 7–28 px of word) are the defaults and the bounds.

Three things about it are easy to get backwards:

* **`auto` is the same flag `Image Tools/cam_layouts.json` carries and means the
  same thing.** True and `layout` is IGNORED and `auto_hall_tiles(w, h)`
  arranges the squares; false and the stored rectangles are used verbatim. The
  first drag sets it false, "Arrange them again" sets it true and empties
  `layout`. Without the flag there is no way back to the even row except editing
  the file.
* **`auto_hall_tiles` takes the band's REAL PIXEL SIZE and is not a constant.**
  A square has to be square, and the fractions that make one are different on
  every window width — which is why the automatic arrangement is computed at
  paint time, in `_HallBand._place` and `_LayoutBoard.reload`, and never stored.
* **An arrangement that does not place all five halls is not used.**
  `_clean_halls` forces `auto` back on when `layout` is short of a hall, because
  the missing square would have nowhere to go and would simply vanish off the
  first tab. A rectangle that is not four numbers is dropped, which is one way
  to end up short.

Each rectangle is `[x, y, w, h]` in fractions of the band, clamped by
`_clean_tile` to inside it and to `TILE_MIN_FRAC` — a tile dragged to nothing
could not be grabbed again to make it bigger.

## The one-square mode

`halls["single"]` (off by default) swaps the five squares for ONE
`HallSquare` (`OverviewTab._single`) on the left with the list beside it,
placed by `OverviewTab._place_single`. Its name is `ls_core.single_hall` —
the first hall in force, or `FALLBACK_HALL` (Int) when there is none, since the
beam never goes into two halls at once and goes into Int when it goes nowhere
else. In this mode `band_px` is the square's SIDE, so the Settings box and the
fit/manual machinery are shared; the fit is `ls_core.fit_single`, and the
list's area is sized from `_Row.block_size` (not `sizeHint`, which is stale
until the next event-loop turn). The Settings tab hides the board and
"Arrange them again" in this mode.

## The list rows

`name: -value  [square]` — the name with a colon (`ov_t.name_shown`) is flush
RIGHT, so names end on one line. After the colon comes one space
(`ov_t.name_gap`) and a slot for a minus, then the digits flush LEFT, so every
number starts on one line (2.10.2026). The slot is the value label's `indent`
when the number has no minus; `ov_t.value_advance` measures a number that way,
and both `_Row.block_size` and the fit's `row_w` add up the same pieces, so the
fit can never pick a size the row then needs more room for. The UNIT is a
column of its own (`_Row._unit`, `ov_t.split_value` cuts `C.value_text`'s
"-12000 fs²" in two, `unit_advance` measures it with its leading space and
`VALUE_PAD`), so the units stand under each other; each column is as wide as
its widest entry, so the squares stand in one column right after the longest
unit. `set_value` / `texts()` still take and give the whole string.

**The shot counter** (2.10.2026, Image Slider's rules — `Image Tools/is_t.py`
`ShotCounter`). A row with `"kind": "shots"` (`new_shot_row`, `is_shot_row`;
`normalise` keeps one at most, forces `pv`/`unit` empty, and since 5.10.2026
APPENDS one when the list has none — `load_config` runs the defaults through
`normalise` too, so a first run has it; the Limits tab has no Add button and
`_remove` skips it). `ls_core.ShotCounter`
lives on the main window (`main.shots()`), is fed in `main._on_pass` before the
tabs repaint, and reaches the row through `row_values(..., shots=)` — never
stale, so a formula may use its letter. What it counts:
`ls_cpva.Reading.recent_ns`, the timestamps of the last `RECENT_S` (120 s),
sliced out of the window `LastReader.read` already fetched — every sample
between two passes is counted at no extra request. Channels are
`shot_channels(rows)`: the SHOWN plain rows, plus the sources of shown formulas
("jen zobrazené řádky"). The first look at a channel is history. Each channel
is counted on its own (`_per_channel`) and `count` is the MAX of them, so one
shot staggered over several channels and passes is one (the 0.2 s
`SAME_SHOT_S` merge it replaced on 5.10.2026 counted it 2-3 times). Idle is
`reset_s` on `time.monotonic()` (`cfg["shots"]`, `shots_of`, Settings tab box;
`main.shot_reset_changed` hands it to the counter): `count` and `_per_channel`
zero, `shown` stays, the next shot shows 1. `_last_shot_ns` survives the reset:
with no run going, a stamp within `LATE_TAIL_S` (2 s, at most `reset_s/2`) of it
is a late channel's share of the last shot and opens nothing.

**L3 and "waiting for hall"** (2.10.2026). `HALL_SHOWN` / `hall_shown` put "L3"
on Int's squares (band, one square, the Settings board) and nowhere else — the
file, the Limits tab and the limit sentences keep "Int", and `_hall_tip` keys
`faults` by the REAL hall. `l3_waiting`: Int's `hall_state` IDLE (SGV501 open)
and no E-hall SHOOTING (idle OR unknown/unset) → `ov_t.refresh` turns a plain
"idle"/"watch" square into `STATE_WAITING` ("waiting for hall", white like
idle). A "fault" is never overridden. Judging is unchanged — the Int fallback
of `halls_in_force` still decides the limits.

`paint_hall_label`'s word line is the word FONT's height, not `word_px + 2`:
"waiting for hall" was the first word with a descender and lost the bottom of
its g.

**The Limits table has two orders.** `_rows_on_screen` is the list (letters and
formulas go by it); `_table_rows` is the table: shown rows,
`None` (the spanned "Not shown" line, `_fill_separator`), unticked rows. Every
"table row i" lookup goes through `_table_rows`; `_fill_row(t, row, pos)` takes
the list position for the letter.

**Rows are dragged into order** (12.10.2026). `st_t._RowTable` is the table:
`InternalMove`, and its `dropEvent` never lets Qt move items — it answers
`IgnoreAction` (a MoveAction makes `startDrag` clear the source cells) and
calls `SettingsTab.move_rows(table rows, drop row)`. That rewrites
`win.rows()` IN PLACE, filling the slots the table's rows held with shown +
hidden in their new order, so the shot counter (own table) keeps its slot. A
drop on the other side of the "Not shown" line sets `show` to match. Then
`_rows_changed` one event-loop turn later. Every cell gets `ItemIsDragEnabled`
at the end of `_fill_row`. `testing/test_row_order_drag.py`.

**A name is retyped on the Overview by double-click** (12.10.2026):
`ov_t._Row.start_rename` lays a `_NameEdit` over the name label; `done(True)`
on Enter / focus-out, `done(False)` on Esc, once. `OverviewTab._renamed` writes
`row["name"]` and calls `main.rows_changed` one turn later (the edit box is
inside its own signal), so the Limits table's Name cell follows.

**SHOOTING lasts `FIRING_HOLD_S` (15 s) after the last shot** (2.10.2026).
`ls_core.FiringWatch`, owned by the Overview, notices a new SBW4 sample by its
archiver timestamp changing and times it on `time.monotonic()` — never the
sample's stamp against `time.time()`, because this PC runs ~25 s ahead of the
archiver. The sample already archived at start does not count; the square waits
for the next one.

## Fitting the window

`fit` is the switch, `ls_core.fit_sizes` is the rule, and
`ov_t.OverviewTab._fit_to_window` is the part that measures. Asked for on
1.10.2026: "user si nastaví velikost okna, pak klikne na fit everything to the
window a script naškáluje 'tabulku' s textem a haly tak, aby se to tam vlezlo
bez nějakých větších mezer".

**It writes what it worked out into the document.** That is the whole point and
the whole difference from the first attempt, which kept a multiplicative
`scale` in the drawing code: the Settings tab then said "Height of the band
400 px" over a band 253 px tall, and the complaint was exactly that — "zatím to
absolutně neodpovídá". A number on screen that is not the number being used is
worse than no number. `main.fit_parked()` → `ft_t.FontTab.park()` puts the four
in the boxes, with their signals blocked.

The split, in order:

* `FIT_MARK_RATIO = 1.5` — the verdict square against the text beside it.
  Fixed, not a setting ("velikost čtverečků bude třeba 1,5x větší než text s
  údaji"). `FIT_WORD_FRAC = 0.10` does the same for the word inside a square,
  against the square's own side.
* the band takes as much as it may, and the rows take the rest. In the even row
  "as much as it may" is `band_side_cap`, the side of a square that fits the
  width — because a band taller than its squares IS the strip of nothing. A
  hand-made arrangement has no square to aim at and keeps `band_frac`, its
  share of the height, caught by `ft_t.FontTab._remember_band_share` the moment
  a square is dragged.
* `_largest` bisects for the biggest whole text size that fits BOTH the height
  and the width, then walks down until it really fits and up while the next one
  still does. Ten measurements whatever the window, and no fudge factor — the
  first attempt shaved three per cent off an algebraic answer to cover a font's
  height not growing in step with its pixel size, which is a guess about the
  error rather than a measurement of it.
* room nothing can use is left **at the bottom of the list**. Measured
  1.10.2026: three rows on a 1920 px window leave 380 px there, because a row
  is already as wide as the window and a square has to stay square. That is
  the honest consequence of his own choice and not a bug — which is why
  `testing/test_fit_to_window.py` asserts "either the slack is under a row, or
  everything that could have taken it is at a limit" rather than a number.

**With the fit OFF the band is still never taller than its squares.**
`OverviewTab._place_children` takes `min(band_px, room, C.band_needed(...))`;
`band_needed` is `band_side_cap` rounded the way `fit_sizes` rounds, or the
band's own height for a hand-made arrangement. Before this, "Height of the
band 500" on a 520 px window gave a 400 px band round 298 px squares and a
40 px list (1.10.2026). The Settings board (`_LayoutBoard`) is height-for-width
off the band's real shape, so it is a ~1:1 model of it.

**Maximise to a set size and place** (`window_max: {on, w, h, x, y}`, `C.window_max_of`):
`main.LaserSettingsWindow.nativeEvent` answers WM_GETMINMAXINFO — `ptMaxSize`
becomes the set size × the screen scale + the invisible border on each side
(read off Windows' default `ptMaxPosition`, which is the top left of the work
area); `ptMaxPosition` is that default + `x`, `y` × the scale
(`GetDpiForWindow`). `C.max_rect` cuts the size to the monitor's work area and
slides the position back in. `x`, `y` are relative to the work area of
whatever screen it is on, never desktop coordinates. Anything
that raises there is caught and logged; normal maximise happens instead.
`_save_now` saves `normalGeometry()` while maximised.

`manual` is what he last typed, so switching the fit off puts his own sizes
back rather than leaving the fit's. In the DOCUMENT, not in memory: the fit is
on by default, so an in-memory snapshot would die with the process. Written by
`_size_typed` and by `_fit_toggled(True)`; `C.manual_sizes` and
`C.apply_manual` are the two halves.

**Typing in one of the four boxes switches the fit off.** `_loading` is held
around every programmatic write — `reload()` and `park()` — and every handler
returns on it, so a signal that gets through is always a human. Copied from
`Image Tools/is_t.py`'s Auto sliders.

`show` is on the ROW, not in `limits`. It was per hall until 30.9.2026 and the
operator asked for one switch — "show funguje jako globální nastavení, pouze min
a max funguje jako specifické". `limits_for` STRIPS a per-hall `show` out of any
entry it is handed, the way `gate_for` strips `closed`; `normalise` migrates an
old file by `any()` of the five, so nothing that was being watched disappears on
the first run. `is_shown(row)` is the only reader.

`alarm` is on the row too (6.10.2026, for a GDD scan that leaves the limits
on purpose). `is_alarmed(row)` is the only reader; default True, so an old
file alarms. Unticked: still shown, `judge_row` answers `STATE_UNKNOWN`
"alarm off — <the verdict it would have given>", and `hall_faults` skips it,
so no hall blinks. The Limits table's "Alarm" column, next to Show.

`scan_min`, `scan_max` are on the row as well (8.10.2026): ONE pair of scan
limits for every hall, on every row but the built-in ones (`can_scan`).
Whether they are in force is ONE switch, `cfg["scanning"]` (12.10.2026), the
Overview's Scanning box (`OverviewTab._scan_box`, `main.scanning()` /
`set_scanning()`). `is_scanning(row, scanning)` = the switch on and
`has_scan_limits(row)`; `limits_in_force(row, hall, scanning)` hands
`_worst_verdict`, `hall_faults` and `row_mark` the scan pair then, and the
hall's Min / Max otherwise — a row with both scan boxes empty keeps Min / Max
while scanning (the user's choice). The verdict sentence starts "scan: ".
Alarm still wins. `scanning_rows(rows, scanning)` = the shown rows scanning;
while it is not empty, `ov_t` appends `_SCAN_SUFFIX` (" + SCANNING") to the
word of a square that is "shooting" or "fault". `ls_ui.paint_hall_label`
shrinks a word wider than its square rather than letting it run off the edge.
Every caller passes `scanning` explicitly; the default False is for old
callers only. The per-row `scan` tick and the GDD / TOD / FOD name rule of
8.10.2026 are gone: `normalise` turns an old tick (on a row with a scan limit)
into `scanning: true` and drops the key. The box is placed by hand on the line
above the list, with the shot counter (`_line_h`, `_place_line`); that line
is always there now, and `_overhead` / the single-mode fit count it. Ticked,
its caption is bold — the amber ground it had first was taken off the same
day ("divný žlutý obrys").

`id` is saved and never reused: it is what lets a reading, a table row, a
square and a **formula** point at the same channel across an edit.

One key per gate. `closed`, `on` and `shoot_when` were settings once; they are
not, and `gate_for` STRIPS them out of any entry it is handed rather than
leaving them to rot in the file, where the next person to read it would believe
them.

`C.normalise` repairs anything: a bad limit is emptied, a repeated id is made
unique, a number where a channel name should be becomes "", a binding that is
not an id is dropped, an unknown hall falls back. One bad field never costs the
other forty. Two rules inside it are easy to get backwards:

* a gate entry that is **a string** is the shape from before the valve had two
  channels — it becomes the OPEN switch, which is what it was;
* a saved **empty** channel name stays empty (the operator cleared it), but a
  gate entry that is not readable at all goes back to the default (that is
  damage, not a decision).

## The gate valves

`ls_core.hall_state` is the whole rule and it is where to start. **One channel
is read, the open switch**, and it answers on its own: not open means shut. The
closed switch is the fallback and nothing else — `gate_channels` adds it only
while the open one is unreadable (error, nothing archived, or older than
`STALE_AFTER_S`) and drops it again the moment the open one is back, which is
why `main.channels()` hands it the last pass's readings.

**What a 1 on that channel means comes from its NAME**, never from the number:
`switch_sense(pv)` says `OPEN` for an `_IN_OPEN_POSITION` channel, `CLOSED` for
an `_IN_CLOSED_POSITION` one, and `None` for a name that says neither (taken as
an open switch for the judging, and shown as the bare number on screen).
Without it, pointing the box at a closed-position channel made every verdict in
the program the exact opposite of the truth. The Limits tab's readout —
`value=1 : open`, `value=0 : closed` — is the same call, which is why the label
and the squares cannot disagree.

`closed_channel(hall)` comes from `DEFAULT_GATES`, never from the file: the
name is deliberately not editable, because a wrong one there would be found
once a year, on the one day the open switch was down.

`halls_in_force` sits on top of `hall_state` and is what everything on screen
actually asks. It answers `(halls, fallback)`: normally the halls being shot
into, and — because SGV502 to SGV505 publish nothing — `(["Int"], True)` while
not one of those four can answer, whatever Int's own valve says. Without it
today's machine judges NOTHING: Int shoots while its valve is shut, the valve
reads open, so no hall is ever "shooting" and the first tab is a wall of grey
dashes. That was the bug report of 30.9.2026.

Two things it is easy to get wrong when touching it:

* "cannot answer" is `hall_state` coming back UNKNOWN **or UNSET**, not the raw
  readings. A hall answering IDLE has answered, so the fallback drops out — that
  is how the rule switches itself off the day the first valve publishes;
* the fallback hall's square is `"watch"`, painted WHITE. Green would claim the
  beam is in there. `ov_t` maps the state, `HallSquare` paints it, and a fault
  still becomes `"fault"` so the blink needs nothing new.

`shoot_when(hall)` is a plain lookup in `DEFAULT_SHOOT_WHEN` — Int on `CLOSED`,
because Int is internal shooting. It is a fact about the machine, not a
setting, so nothing on screen can change it.

All five valves are read. SGV502 to SGV505 do not publish yet; there is nothing
to switch on when they come up.

### The L3BT MSS window (`ls_screen`)

Until those four publish, `hall_state(..., screen)` takes the valve's box off the
L3BT MSS window as a THIRD source, after the open and the closed channel. Its
sentence then says "Read off the L3BT MSS window". `halls_in_force`,
`single_hall` and `status_line` pass `screen` through, so a readable window turns
the Int fallback off by itself.

* `ls_screen.read_window()` → `ScreenRead(positions, problem, at)`; a hall
  missing from `positions` is not known. `_screen_position` ignores a look older
  than `STALE_AFTER_S`.
* `VALVE_BOXES` holds each box's centre measured in a 1190 × 629 frame
  (DWM frame bounds) on 1.10.2026; `read_picture` scales by the real frame
  size and `find_box` searches ±30 px for the black outline (a run as wide as a
  box, one under it, two ink columns). Right half solid → OPEN, left → CLOSED,
  neither (crossed) → CLOSED, both → no answer.
* Ink is "every channel under 170", so the red arrows never count and a line
  smeared grey by scaling still does.
* **Exact title only.** A loose match picked up the Photos app showing
  "L3bt mss2.png" on the dev PC — a picture of the window read as the window.
* The engine only looks while `C.screen_wanted` (any of E2–E5 UNKNOWN/UNSET
  from its channels), on its own one-thread pool with its own busy flag, so a
  hung MSS never holds up the archiver pass. `engine.screen()` is None
  otherwise.
* Fixture: `testing/mss_window.png` (the window, cut to its frame), driven by
  `testing/test_screen_hall.py` at 1×, 1.25×, 1.5×, shifted and flipped.

`LastReader.STEPS_S` reaches a **year** because of these valves: a month was
the old limit and a valve untouched since the summer would have left its square
grey for ever.

## The deformable mirror (`ls_dm`, `dm_t`, `dw_t`)

Added 8.10.2026. Mirror Control Window runs on a PC with no share and no
internet; OPR1 keeps a Remote Desktop window to it (192.168.1.107). On OPR1,
with **DM writer** switched on, the engine's `dm` thread photographs that
window every `PERIOD_S` (5 s) and writes `dm_state.json`; every PC (OPR1
included) reads it back and the **DM settings** tab says which saved set-up the
grid is. Asked for: no voltages, only which set-up — "nemusí to psát hodnotu,
ta je k ničemu, ale musí to rozpoznat, jaký nastavení tam je použitý".

* **What is compared is colour.** The numbers 0–35 in the cells are electrode
  numbers, the same in every set-up. Two set-ups match when no channel of any
  cell differs by more than `MATCH_TOL` (10). The two fixtures differ by up to
  49 in 21 electrodes; one set-up read twice differs by 0.
* **Finding the grid** (`read_picture`): red pixels via channel slices +
  `bytes.translate` + a big-int AND + one regex (`_red_mask`) — a byte regex on
  the BGRA data matched black pixels shifted by one byte (opaque alpha passes
  for red) and was 570 000 false starts. A red → white → blue column is a scale
  (`_bar_at`; SID4's rainbow has no white middle). Set-up's scale is on its
  RIGHT, so `grid_left_of` walks left; Applied voltages has only black and the
  "150 / 0 / -150" labels there and is never taken. Cell pixels are "red or
  blue at ≥ 235, the other two equal" (`_is_cell`); the frame and the digits
  never are. `_walk` steps over ≤ 2 px of blended edge in a scaled window.
* **The cells are not width / 6.** The graph cuts the last row and column: 34 px
  apart, the last one 28 px (`_SPAN_CELLS = 5 + 28/34`). The digits sit in the
  middle of the FULL cell. `_pitch` refines the guess from where the colours
  change. Each cell is the per-channel median of eight spots in a ring around
  the digit (`_RING`), spots past the cut edge left out.
* Where the scale was is the next look's hint (`engine._dm_hint`); a full
  search of 1920 × 1080 is ~50 ms, from the hint ~2 ms.
* **Minimised = unknown, no colours.** Windows does not draw a minimised
  window. `find_rdp_window` DOES return minimised windows (unlike
  `ls_screen.find_window`) so the file can say why. Asked for: "Prázdné
  hodnoty" — no old colours carried on.
* **Stale** after `STALE_AFTER_S` (30 s) without a write: the tab says
  "No fresh reading" and draws no colours.
* **The share**: same two names and probing as `Diagnostic/shared_pvs.py`
  (daemon probe threads, 3 s deadline, re-probe after 60 s). `exchange` runs
  on the `dm` thread only. The set-ups file is read BEFORE an edit and written
  only if it was read — an unreadable file is never replaced by a one-entry
  list. Edits (`engine.dm_edit`) are queued callables `setups → (setups|None,
  sentence)`; an exception in one is that edit's sentence, not the share's.
* Delete takes two clicks (`_ARM_MS`), no modal dialog — `exec()` freezes the
  other windows.
* **Built-in Low / High power** (`REFERENCE_SETUPS`, colours of the two
  fixtures as literals: 7:27 = High, 7:31 = Low — swapped 9.10.2026, the
  8.10 guess was backwards). `with_references` puts them
  first in every `DmView.setups` and overrules a file entry of the same name;
  edits see them (so their colours cannot be saved twice) but their colours
  never go into `dm_setups.json`; `rename_setup` / `delete_setups` leave them
  alone.
* **Two ways in** (9.10.2026, OPR1 maximised RDP window gave no grid via
  PrintWindow): `_read_both_ways` tries `ls_screen.grab_window` then
  `ls_screen.grab_screen_of` (BitBlt of the window's DWM frame off the screen
  DC — only that rectangle, never the whole desktop). `DmRead.how` says which;
  a failed look's problem carries `describe_picture` for each. The pictures
  ride in `DmRead.picture` / `screen_picture`, are stripped in `exchange`, and
  written as PNG (`png_bytes`, no Qt/PIL) when `save_picture` — the engine asks
  on the first NO_GRID of a streak and on the DM writer tab's button.
  `testing/test_dm_live_grab.py "<title>"` runs both on a real window.
* **High / Low power ticks** (asked 9.10.2026, any number of each): every entry
  carries `"power"` = `TAG_HIGH` / `TAG_LOW` / `TAG_NONE`; `tag_of(name,
  setups)` falls back to `REFERENCE_TAGS` for the built-in two. `set_tag` is
  the edit; `for_file` is what `exchange` writes — own set-ups whole, a
  built-in one only as `{"power": tag}` when its tick differs from its name;
  `clean_setups` keeps such a power-only entry and `with_references` lays its
  tick over the built-in colours. In `dm_t` the two columns use
  `U.CenteredCheckDelegate`; a click shows at once via `_pending` until a view
  carrying it (or any edit note) comes back. Ticking one column unticks the
  other on that row.
* **Renaming a built-in set-up** (12.10.2026). `rename_setup` on Low / High
  power writes an own entry under the new name holding `REFERENCE_SETUPS[old]`,
  its tick and `"replaces": old`; `replaced_references` → `with_references`
  then leaves the built-in out, everywhere, so every consumer still sees only
  entries with colours. `clean_setups` keeps `replaces`. Deleting the entry
  brings the built-in back; a built-in name can never be given to another
  set-up (`add_setup`, `rename_setup`). Delete stays refused on the built-ins.
* **The Overview line** (asked 8.10.2026): a built-in row `kind: "dm"`
  (`new_dm_row`, `is_dm_row`, `is_builtin_row` = shot counter or DM), added by
  `normalise` with Show UNTICKED. `ls_dm.mirror_now(view)` gives the text and
  the tag (None = mirror not known, `TAG_NONE` also for an unsaved grid);
  `ls_core.dm_judge(tag, FiringWatch.fresh_energy(mono), title)` the verdict —
  a fresh SBW4 shot (< `FIRING_HOLD_S`) over `DM_SPLIT_J` (0.05 J) on a set-up
  not tagged high, or at/under it on one not tagged low, is BAD; tagged
  neither with no shot is UNKNOWN. Low power is ALSO the SAST1 shutter open
  (`DM_SHUTTER_CHANNEL`, State 1 = open, measured against SBW4 bursts of
  6.10.2026) with no fresh shot over the split — `shutter_open`, passed in by
  `_dm_line`; `main.channels` reads the shutter only while the DM row is shown. `OverviewTab._dm_line`
  adds that fault to the halls in force AFTER the not-firing gate (a High power
  fault is a sub-threshold shot). The value is written with a non-breaking
  space so `split_value` keeps "High power" whole. `testing/test_dm_overview.py`.
* Fixtures: `testing/dm_0727.png`, `testing/dm_0731.png` (the remote screen at
  7:27 and 7:31 on 8.10.2026, two set-ups). `test_dm_grid.py` (scaled 0.5–1.25×
  sharp and soft, framed, covered, moved, one electrode changed, speed, the
  verdict, the set-up edits); `test_dm_tabs.py` (the round trip through a temp
  folder standing in for the share, plus `dm_tab_*.png` / `dm_writer_*.png`).

## Formulas

Copied down from `Image Tools/is_t.py` (`PV_DERIVED`, `pv_eval_derived`,
`PvConfigDialog._refresh_letters`) and cut to size. The repo keeps no shared
library, so keep the two in step by hand.

A row with a non-empty `expr` is a formula: no `pv`, nothing read for it, a
number worked out from the rows above it. `bind` maps each letter to a **row
id**, captured while typing; the letters on screen are the rows' POSITIONS, so
`expr_to_display` rewrites the box from `bind` every time it is drawn and
`expr_from_display` captures it again on every edit. That is the whole reason a
formula survives renaming, repointing and reordering.

**`is_formula` and `is_formula_row` are different questions and the wrong one
costs real data.** `is_formula` = "has something to work out" (non-empty
`expr`), which is what `row_values` wants. `is_formula_row` = "IS a formula
row" (the `expr` key is present, empty or not), which is what the Limits tab
and `main.channels()` want. Measured 30.9.2026: a freshly added formula row has
an empty `expr`, so `is_formula` said False, so the first thing typed into the
row's own box went into `pv` — a real settings file ended up with
`"pv": "C*0.749"` and the archiver was being asked for a channel of that name
once a second, with an empty "Reading now" and nothing on screen to say why.
Hence: `normalise` KEEPS an empty `expr`, and `_repair_misplaced_formulas` puts
the shape right (a formula row whose `pv` has no colon in it cannot be a channel
name — every archived name here carries one).

**Decimal comma.** `fix_decimal_commas` swaps `,` for `.` at bracket depth 0
only, via `_split_on_brackets`. `A * 0,749` is VALID Python — the tuple
`(A*0, 749)` — so it compiled, nothing went red, and it failed once a second as
"did not work out to a number". A blanket `replace` would wreck `min(A, B)`, and
a plain `(?<=\d),(?=\d)` regex turns `min(1, 2)` into `min(1.2)`.

A decimal comma inside brackets is left exactly as typed, and **deliberately not
reported**: `min(1, 2)` and `min(A, 0,749)` are the same shape and the language
accepts both, so nothing here can tell a decimal point from an argument
separator. Marking a correct `min(1, 2)` red would cost more than the rare case
it would catch. Said in `_FORMULA_TIP` and both ReadMes instead. Do not "fix"
this by adding the check back.

`row_values(rows, readings, last_ok, now_s)` is **the** place a row becomes a
number — plain rows first, then the formulas in the order they need each other
(12.10.2026, since rows can be dragged anywhere a formula may use a row below
it). A ring of formulas is `_in_formula_loop` → a `formula_problems` sentence;
the "further down the list" rule is gone. The dict comes back in list order.
`judge_row` and `hall_faults` take its `RowValue`, so "a missing reading is
never a tick" is written once. A formula is as stale as its oldest source, and
one source without a number leaves it without one.

`compile` + `eval` in `_EXPR_ENV`, which has **no builtins** — a settings file
edited by hand must not be able to run anything. An expression that will not
compile is cached as `False` so a broken formula is not re-parsed once a
second.

## The rules worth knowing before changing anything

* **`min <= value <= max`, inclusive.** Asked for explicitly.
* **How a reading is WRITTEN is `C.value_text`, and only there.**
  `UNIT_DECIMALS` fixes the decimal places per unit — `J` and `mJ` to two,
  "energii zarovnej na setiny" — and everything else keeps the shortest form
  that loses nothing. `unit_text` lifts `^2` to `²`. Both at display time;
  see the trap about that below.
* **An empty limit box is not zero.** It switches that side off. Both empty is
  not a pass — it is `STATE_UNKNOWN`.
* **A missing reading is never a tick.** Failed, nothing archived, or not read
  for `STALE_AFTER_S` (30 s) all come out as a grey dash. The age that matters
  is the age of the last successful READ, never of the value: the archiver
  writes on change only.
* **The list's square is `row_mark`, not `judge_row`** (12.10.2026). Grey
  (`unknown`) means only "cannot be read". Read fine but not firing
  (`FiringWatch`) or no hall in force: ORANGE `idle_ok` / `idle_bad`, judged
  against `single_hall`'s limits so the operator sees where it stands. Read
  fine with no limits or Alarm off: WHITE `none`. Green / red only on a shot.
  `hall_faults` is unchanged, so an orange cross never blinks a hall. The
  colours are `ls_ui.VerdictMark._GROUND`; the meaning of each is the
  `ov_t._MARK_LEGEND` at the foot of every row's tooltip (asked for the same
  day). `judge_row` is still what the DM line and the tests' older checks use.
* **No hall shooting means no hall blinks.** The rows are orange, see above.
* **Two empty limit boxes are not a tick.** `judge` answers "no limits set", so
  a fresh list of channels with no Min or Max is a column of grey dashes. That
  is the first thing to check before hunting a drawing bug.
* **The first tab's row list is stable.** It is every row whose `show` is on,
  and `show` does not depend on the hall, so it cannot reshuffle.
* **`show` is one switch, and unticking it must REBUILD.** `settings_changed()`
  alone only refills the tables, so the row stayed on the Overview until the
  next restart. `st_t` calls `main.rows_changed()` — the Overview and the Limits
  table only, each in one paint, the Limits page keeping its scroll position and
  current cell — deferred by `QTimer.singleShot(0)` because it is called from
  that table's own `itemChanged` and a rebuild deletes the item Qt is still
  holding.
* **A change of LOOK never rebuilds** (6.10.2026, "při změně parametrů nic
  nerefreshovat, ale hned upravit"). Three ways in, each narrower than
  `settings_changed(rebuild=True)`, which rebuilt all four tabs once per wheel
  notch and once per mouse move of a board drag (40 ms each, measured):
  * `main.look_changed()` → `OverviewTab.restyle()`: every box on the Settings
    tab, the fit switch, the defaults, the counter line's size / Bold. Same row
    widgets, `_fit_to_window(force=True)` hands them the look. `_Row.apply_look`
    skips an unchanged look (`_look_key`), since a stylesheet is a re-polish.
  * `main.halls_arranged()` → `OverviewTab.rearrange()`: the board's drag —
    squares moved, the fit coalesced through `_fit_soon`.
  * the Limits tab's hall buttons rewrite the Min / Max cells in place
    (`_pick_hall`, `_fill_gate`), no refill.
  `testing/test_edit_in_place.py` pins all of it by object identity.
* **The once-a-second pass touches only what changed.** Tooltips through
  `ov_t._set_tip`, `_gate_now`'s stylesheet only on a new colour, `_fit_columns`
  only when `_columns_key()` (names, number LENGTHS, units) moves, and the Limits
  and Info tabs skip the pass while they are not visible and catch up in
  `showEvent`.
* **The Maximise boxes re-maximise once**, 400 ms after the last edit
  (`main._remax_timer`), never per step — each one restores and maximises the
  whole window.
* **The channel list is fetched on a thread** (`ChannelPicker._load`), and the
  dialog polls a plain dict every 100 ms; no Qt object crosses threads. On the
  GUI thread a dead archiver held the window for six seconds.
* **A faulted hall blinks RED and WHITE**, on one shared 450 ms precise beat
  owned by the tab, never by the squares.

## Traps already paid for

* **No `QRunnable`, no `QThreadPool`, no per-pass `QObject`.** One
  `_PassSignals` parented to the engine. The other shape crashes with an access
  violation within a couple of thousand passes — see the comment at the top of
  `ls_engine.py`.
* **A `paintEvent` uses `try/finally`.** An exception that escapes one leaves
  the painter active and kills the program later, somewhere unreadable.
* **The taskbar id is random per launch.** Windows caches the taskbar picture
  per AppUserModelID and never re-reads it; every stable id tried in this family
  eventually went blank.
* **`icon.ico` is drawn per size, not resampled.** Below 48 px it is a
  simplified drawing — five halls at 16 px merge into a grey band. Redraw with
  `python testing/make_icon.py`.
* **`ColumnFitter` takes a `stretch_col` and a `fixed_cols`.** Both are this
  copy's additions. `stretch_col`: the spare width can go to several columns,
  shared in proportion to their contents — handing it all to the last column
  puts a grey strip under a heading. `fixed_cols`: a column whose contents are
  DRAWN measures as empty, so `fit()` squeezed the Limits tab's columns
  wrongly. Only the Limits tab has a table now.
* **The Overview's list is not a table.** It was a three-column
  `QTableWidget` and a table hands its spare width to a column: `Name` ran the
  whole width of the window and the verdict square ended up on the far right,
  yards from the name it belongs to. Reported in those words on 30.9.2026. It
  is `ov_t._Row` widgets in a `QVBoxLayout` in a `QScrollArea` now — name,
  square, number, packed left, the spare width behind the number. Four things
  are easy to get wrong here:
  * the name column is **measured** (`QFontMetrics` over every name, floored and
    capped in multiples of the text size — `NAME_MIN_EM`, `NAME_MAX_EM`,
    `NAME_HARD_MAX` — longer names elided) so the squares line up down the list;
  * the font is written **twice**, and both are needed: as a `QFont` on the two
    labels, because the name is measured with `QFontMetrics(label.font())` and a
    stylesheet never reaches `font()`; and in the row's stylesheet, because
    `APP_QSS` sets `font-family` on every QWidget and **a stylesheet beats
    `setFont`** — measured on 30.9.2026, with the font alone, picking Consolas
    changed the settings file and nothing on screen. `apply_look` writes the
    same three values into both, so they cannot drift;
  * a row's stylesheet is scoped by object name (`#listRow`). `QLabel` is a
    `QFrame`, so a plain `QFrame { background: … }` on the row would paint the
    two labels as well;
  * **the block is CENTRED and every row is exactly as wide as it.** Both
    columns get a fixed width from `_fit_columns` and a row has no trailing
    stretch, so its `sizeHint` is the block; the rows go in with
    `AlignHCenter`. A stretch would make every row full width, and a centred
    row that is already full width is not centred at all;
  * **every name and every number is shown in full.** `_name_width_for`
    honours the longest name and the TEXT SIZE gives way — the fit shrinks it
    until the row fits. There was a cap (a flat `NAME_HARD_MAX = 520`, then a
    share of the width) and the cap is what produced "SBW4 Compres…". The
    ellipsis is the last resort only, for a name no readable size could fit;
  * `_fit_columns` runs again when a VALUE's text changes, not just a name's,
    or a number that gained a digit is clipped by a column measured for the
    shorter one. The fit's own width budget uses `VALUE_FLOOR_TEXT` so that
    never costs a re-fit;
  * **nothing is painted behind the list** — no sheet, no zebra and no red band
    on the row that is out of range. That row is marked by the red cross in its
    square, which is what the square is for ("od toho tam je ten čtvereček",
    30.9.2026). `U.PAPER` is still stated on the scroll area, its viewport and
    the sheet rather than left to Qt: this PC is in Windows dark mode.
* **The wheel guard has to take WheelFocus away.** Spin boxes and drop-downs
  are born `WheelFocus`, and Qt gives such a widget the focus — as a MOUSE
  focus — on a wheel notch before the app filter sees the notch, so the guard
  let the first notch through. `ls_ui.install_wheel_guard` turns them to
  `StrongFocus` on `Polish`, as Image Tools' guard does.
* **A table tick box has to be drawn.** `PE_IndicatorItemViewItemCheck` on this
  Windows style paints the TICK and nothing else, so an unticked cell came out
  completely empty and "not watched" looked like "there is no switch here".
  `ls_ui.paint_check_box` draws the frame as well.
* **A styled tick box loses its tick.** Giving `QCheckBox::indicator:checked` a
  background stops Qt drawing the mark, and a ticked box came out as a plain
  blue square. This copy draws the tick and feeds it in as an image — use
  `U.chk_qss()`, never the raw `CHK_QSS`, which still holds its token.
* **Nothing may be rendered onto the user's screen.** `testing/render_tabs.py`
  uses the `windows` plugin (the `offscreen` one on this PC has no font and
  draws every letter as a box) together with `WA_DontShowOnScreen`, and builds
  the tabs against a stub window with canned readings — no archiver traffic and
  no real program started.
* **The band has NO layout, and must not get one.** `_HallBand` places its five
  `HallSquare` children with `setGeometry` from `ls_core.hall_tiles` on every
  resize. A `QHBoxLayout` would simply overrule the whole arrangement. The
  `AlertMark` in the corner is `move`d and never `setGeometry`d — it fixes its
  own size from the height it was built with, so a setGeometry there is ignored
  for the size and only misleads the next reader.
* **NEITHER DOES THE OVERVIEW TAB ITSELF.** Its three pieces — the band, the
  line, the list — are placed by `_place_children`, and that is not tidiness.
  Measured 1.10.2026 on a 480 px tab with the band set to 150: a `QVBoxLayout`
  handed it **113** and gave the rest to the scroll area, because a box layout
  short of room takes the shortfall in proportion to what each item COULD give
  up. Three size policies were tried; `Fixed` and `setFixedHeight` both fix
  that and both then make the band a floor under the whole window, which is the
  other thing that had to go ("úplně bez minima"). Placed by hand, the band
  gets exactly what it is set to whenever that fits, the list gets the rest, and
  `OverviewTab.minimumSizeHint` is `(0, 0)`.
* **`setGeometry` on a widget that has never been SHOWN delivers no resize
  event.** Qt holds it until the widget is shown, so on a window that is built
  and measured without being shown — every test and every picture in
  `testing/` — `_HallBand` stayed laid out for the band's previous height.
  Measured 1.10.2026: a band moved from 150 to 220 kept 150 px squares. Hence
  `_HallBand.relayout()`, called by `_place_children` straight after the move
  instead of waiting for the event.
* **Stop the fit's timer before deleting a tab.** `OverviewTab` owns two timers
  — the blink and the coalesced fit — and a fit that fires while its tab is on
  the deferred-delete queue runs over a half-dismantled widget. That is an
  access violation, not a Python exception: the script dies with no traceback
  after the last picture has been written, which is what `render_tabs.py` did
  until it stopped both timers first.
* **The window's minimum is 127 x 109 and that is the tab bar.** `main.py` asks
  for nothing (`setMinimumSize(0, 0)`), the Overview asks for nothing, and the
  Limits and Settings tabs are each inside a `QScrollArea` whose own
  `minimumSizeHint` is 80 x 80. Anything that claims a minimum width on one of
  those tabs — `_gate_edit` claimed 340 — is claiming it for the whole window.
* **A scale model is built from the REAL size, never from its own.**
  `ft_t._LayoutBoard` takes `band_px`, the band's real pixel size, and works
  the arrangement out for THAT (`reload` → `C.hall_tiles(halls, *self._band_px)`).
  It used to take only the aspect and arrange at the board's own size, so a
  478 px model of a 970 px band showed 88 px squares where the real ones are
  186 — a different arrangement, reported 1.10.2026 as "neodpovídá". Anything
  measured in ABSOLUTE pixels is then drawn through `_scale()`: the word
  inside a square came out twice too big and ran off the sides without it.
  `ls_ui.paint_hall_label` is the one function `HallSquare` and the board both
  draw with, which is what stops them drifting again. Same trap and same fix
  as `Image Tools/is_t.py`'s `_LayoutCanvasWidget._canvas_px`.
* **The preview rows are the REAL size, and go through `C.value_text`.** They
  were capped at 28 px so a parked 140 px could not turn the box into five
  hundred pixels of scrolling; that was the wrong trade and he said so. A
  preview that is not the real size is not a preview. The sample numbers go
  through the same call the readings do, or the preview shows a format the
  program does not use (`20.0552 J` and a raw `fs^2`).
* **`C.unit_text` is a DISPLAY rule and must stay one.** `fs^2` → `fs²`
  happens in `value_text`, not in the document: the Limits tab's Unit cell
  shows what was typed and the settings file keeps it. Lifting it on the way in
  would rewrite his text behind his back and make the cell untrustworthy.
* **The board must not re-read the file mid-drag.** A fit during a drag parks
  its numbers and calls `set_band_px`, which reloads an automatic board.
  `_LayoutBoard.reload` returns at once while `_drag_idx >= 0`,
  or the square would snap back to where it was when the press landed on every
  mouse move. `test_hall_layout_drag.py` pins it.
* **`_LayoutBoard` is letterboxed to the band's REAL aspect**
  (`FontTab._band_aspect`, the window's width over `band_px`). Without it the
  model is stretched and a square dragged square there comes out a rectangle on
  the first tab. The width is read off the live window, so it follows a resize;
  there is a fallback for the stub window in `render_tabs.py`, which has no
  real width.
* **`HallSquare` fills its widget** rather than drawing a centred square of the
  smaller side. The operator sets the width AND the height by hand now, and a
  drawing that ignored one of them would make half of every drag do nothing.
  It also writes the SHOOTING / idle word itself, inside the square, and draws
  NOTHING at all when the square is too small for the lettering — half a word
  off the edge is worse than the colour on its own.
* **The small icon frames needed a GREEN edge, not a black one.** Measured at
  16 px: `testing/make_icon.py` drew the lit hall with a black rim 0.8 of a
  pixel wide, and LANCZOS painted that as a grey bar across the top and the
  bottom and nothing down the sides — the square read as a grey-capped
  rectangle. The pen minimums in that file are in `OVER` units, because every
  frame is composed at `OVER` times its own size; a bare `max(1.6, …)` there is
  a fifth of a pixel on the finished icon.

## Running it

```
python main.py                           the program
python testing/test_core.py              the rules, headless
python testing/test_window_builds.py     the window and every tab, offline
python testing/test_hall_layout_drag.py  the board, driven by its own handlers
python testing/test_fit_to_window.py     the fit, at four window sizes
python testing/test_edit_in_place.py     edits repaint in place; the wheel guard
python testing/render_tabs.py            the pictures
python testing/test_dm_grid.py           the DM grid off the screenshots
python testing/test_dm_tabs.py           DM settings / writer round trip + pictures
python testing/test_dm_overview.py       DM settings line: Low/High vs SBW4, blink
python testing/test_row_order_drag.py    drag order, Scanning box, rename in place
python testing/make_icon.py --preview    icon.ico, and a contact sheet of it
```
