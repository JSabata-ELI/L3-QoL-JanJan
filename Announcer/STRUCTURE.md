# Announcer — STRUCTURE

> Rewritten from tkinter to PySide6 on 2026-09-16. The single 3748-line `a.py`
> is gone; what replaced it is below. Verified against the source and against
> the real archiver on that date.

Announcer watches a list of things that have to stay true and raises the alarm
the moment one of them stops. Two kinds of thing, one mechanism:

| kind | what it watches |
|---|---|
| `value` | an archived number stays inside its limits |
| `area` | a rectangle of a screen still looks like its reference picture |

and one switch, `fires`, that says what happens when it stops holding: `show`
turns the row and its badge red, `alarm` does that and flashes a window and
plays a sound.

User-facing documentation: `ReadMe Announcer.txt` (the Launcher's **ReadMe**
button) and `ReadMe_Announcer_Full.txt` (its **Details** button). Shared
infrastructure — paths, the build chain, where settings live:
`../INFRASTRUCTURE.md`.

## What the rewrite changed, and why

Four things the operator asked for on 2026-09-16, and what they became:

1. **The Halls tab is gone**, and with it the whole beam-fate / PSS feature.
   Its main channel `L3BT-MSS:Beam_fate` is not archived at all, so that half
   could never fire; the tab was two thirds decoration. The one piece worth
   keeping — the widening look-back that finds the newest sample of a channel
   written on change only — survives as the `read: "last"` mode of an ordinary
   value.
2. **It looks like Image Tools and CSS Logger**: PySide6, Fusion, the house
   light stylesheet, the same tab bar, the same tables, the same scroll bars.
3. **Everything is edited in the window.** The four limits are typed straight
   into the table cells; a channel is found by searching the archiver's own
   list of 9744 names; a rectangle is dragged out on any screen.
4. **One list instead of four mechanisms.** The old program had three condition
   kinds, a nameless left-over rectangle, and eight hard-coded value badges that
   could never raise the alarm. All of it is one list now.

Two rules recorded from earlier work are applied here for the first time:
**reading is not alerting** (the numbers and the pictures refresh whether or not
the alarm is armed; the switches gate only what is raised), and **stale data
announces itself**.

## Files

| File | What it is |
|---|---|
| `main.py` | The entry point. Taskbar identity, the look, the header row with the circle, the tabs, the settings file, shutting down. Owns no verdicts. |
| `ann_log.py` | **Qt-free at import.** The crash log: `faulthandler`, `sys.excepthook`, `threading.excepthook` and Qt's own message handler, all into one file under `%LOCALAPPDATA%\Announcer`. Installed by `main.py` *before* Qt is imported. |
| `ann_core.py` | **Qt-free.** The item model, the pure verdicts, the config file, the migration, the readable failure text, the channel search. |
| `ann_cpva.py` | **Qt-free.** The archiver: a keep-alive connection pool, one fetch, the three reductions, the widening look-back, a whole pass with a deadline. |
| `ann_screen.py` | The screens in both coordinate spaces, the PIL grab, the picture difference, the drag-out overlay, the numbered monitor overlay. |
| `ann_watch.py` | The engine: two clocks, two single-flight guards, a wedge watchdog, per-item state, latching, the one-shot alarm. |
| `ann_alarm.py` | The flashing window and the panel that stays on top. |
| `ann_sound.py` | **Qt-free.** winmm, a named output device, the synthesised beep. |
| `ann_ui.py` | The look in one importable place: the stylesheets, the palette, the wheel guard, the drawn icons, the status circle, the red no-data exclamation mark (`AlertMark`), and the table helpers every tab uses — `ColumnFitter` (widths), `RowMenu` (the right-click menu) and `CenteredCheckDelegate` (a tick box in the middle of its column). |
| `ann_presets.py` | The sets of alarms: the drop-down's contents, the tick boxes in an alarm's editor, Assign to preset, and the Manage presets box. |
| `wa_t.py` | Tab **Watch** — one table of everything, gathered into three blocks (in range / not in range / not watched) under grey heading rows, and the message log. |
| `vl_t.py` | Tab **Values** — the numbers, their limits edited in place, the channel picker. |
| `ar_t.py` | Tab **Areas** — the rectangles, their reference pictures, a live preview, the saved rectangles. The table's columns are the operator's own order and every one is sized to its contents; the monitor number rides in the *Rectangle* cell rather than owning a column. Its list, like the Values one, holds only the chosen preset. |
| `al_t.py` | Tab **Alarm** — the flash, the picture, the sound, where it appears. |
| `presets.json` | Every setting, including the reference pictures as base64 PNG. Next to the program. |
| `images/`, `sounds/` | The alarm pictures and the selectable sounds, read from **next to the exe**. They must ship with every build. |
| `icon.ico` | The window and taskbar icon. |
| `build_config.json` | Dev Tools build settings. `extra_files` lists `images` and `sounds` as folders. |
| `testing/` | Tests, benches and render harnesses. None of them touches the real `presets.json`. |

### `testing/`

| File | What it proves |
|---|---|
| `test_core.py` | Every verdict over its limit combinations; empty-is-not-zero; that each item fires on its own; the presets (what a set holds, Unassigned, rename, delete, the counts, an old group carried across); the channel search; the config round trip; the migration of the operator's real `presets.json` (on a copy). |
| `test_cpva.py` | The three reductions; a sparse setpoint held forward; the widening look-back's request count and its memory; an empty window is not an error; one slow channel does not stretch a pass. |
| `test_screen.py` | The coordinate boundary, per screen, **including the one at 150 % scaling** — and that a grab returns exactly the rectangle asked for. |
| `test_multi_select.py` | Picking several rows: that the row menu offers Remove and "Assign to a preset" for one OR MORE and says how many, that the Remove BUTTON is there on both tabs, live for one row or several and dead with none, that Edit / Duplicate / "Take the picture again" / the rectangle buttons still need exactly one, that Remove takes every selected row — and that it takes the row picked and **not its identical twin**. |
| `test_no_data_mark.py` | Readings that stop arriving: how long they have been failing, that a quiet channel and an unfinished row are never counted, that a switched-off row is not either, that the panel beside the circle says nothing at all about a failed read while a value out of range still gets its sentence, that the log line survives, and that the circle's window shows, hides and keeps the sentences off the mark. |
| `test_selector.py` | Dragging a rectangle out: WHO OWNS THE OVERLAYS. That a shown selector survives losing the caller's reference, that a second one does not destroy the first one's windows, that `retire` holds a reference until Qt is really done, that there is always a way out, that a failing paint still closes its painter, and that several areas cost ONE photograph. |
| `test_watch.py` | Latching, that two alarms in one preset do not wait for each other, the one-shot alarm, Reset, staleness, the wedge watchdog, and that a written-off pass answering late is dropped. |
| `test_watch_table.py` | The Watch tab's three blocks: the order of them, that every verdict which is not "in range" lands in the middle one, that a latched row that reads ok again stays there, that a switched-off row is "not watched" whatever it reads, that an empty block gets no heading, that the heading spans the whole width and cannot be clicked, that a new number does not reshuffle while a new verdict does, and that the clicked row stays clicked across a reshuffle. |
| `test_hud.py` | That the strip of sentences never sits on the circle, from every corner and at every strip size. Pure rectangles, no windows. |
| `test_columns.py` | Column widths: that every column can be DRAGGED, that each one follows the widest cell in it, that a SPANNED cell (the Watch heading line, the empty-table note) is not counted as any single column's contents, that a heading always fits, and that a width the operator set himself survives a reload. |
| `test_row_menu.py` | The right-click row menu: that a right-click inside a selection of several rows keeps that selection, that a right-click outside it picks that row instead, that empty space offers nothing, and that the labels say how many rows they are about. The menu is never opened — `QMenu.exec` blocks and cannot be stubbed; `rows_at` and `labels` are called instead. |
| `test_placing.py` | That the alarm window and the circle can be dragged at all: which edge of the window a point is on (move in the middle, size on the eight grips), a pointer for each grip, that neither placement box is application-modal, that `_place` / `_place_circle` do not use `exec()` — read as SOURCE, because a box built non-modally and then `exec()`ed is modal anyway — and that letting go of the circle you are placing does not stop watching. |
| `bench_archiver.py` | Against the real archiver: fourteen values in one pass, a nonexistent channel, the sparse read. |
| `bench_whole_chain.py` | Watching → a real reading over its limit → the circle → the alarm → dismissing → back. |
| `render_window.py` | The four tabs, photographed. |
| `render_areas_columns.py` | The Areas table at the window's smallest: every column's width, where each one ends, and whether the properties — up to and including *Fires* — are all in view. *Rectangle* and *Reference* are allowed to be past the right edge; that is what the scrollbar is for. |
| `render_alarm.py` | The alarm in all eight mode/cycle combinations, the dark half of a blink, and the panel on top — each clipped by its real window shape. |
| `render_icons.py` | Every drawn icon on all three kinds of button, plus disabled. |
| `render_no_data_mark.py` | The red exclamation mark beside the green circle, at four sizes, over a light panel, the dark desktop, a red window and white paper — it has no background, so it has to carry its own outline. Nothing is shown: the widgets are `render`ed into a pixmap. |

**The two long soaks are gone — do not write another one.** On 18.9.2026 the
operator asked for them to be deleted: `bench_engine_soak.py` drove the engine
for a minute and a half and `bench_area_cycle_soak.py` built the real area
editor a hundred and fifty times over, taking his screen for minutes at a time
while he was trying to work. **He does the trying-out himself.** What they
measured is kept below, under "What the soak measured" and in the engine
section, because those numbers are the reason this code is shaped the way it is
— but nothing in this folder may now run for minutes or throw a window onto his
screen unasked, and `run_all.py` is the headless set only.

Three traps these harnesses were written around, worth knowing before a fourth
is added:

- **Pump a real event loop, not a row of `processEvents()`.** Results come back
  from worker threads through signals, and under `processEvents()` they do not
  arrive — the table sits on "not read yet" and looks like a bug in the program.
  `settle(ms)` in each harness runs a real `QEventLoop`.
- **`QWidget.grab()` ignores the window shape and comes back in DEVICE pixels.**
  A shaped window therefore photographs as a full rectangle, and on the 150 %
  screen a 300x380 window photographs as 450x570. `render_alarm.py` scales the
  shot back, **resets its device-pixel ratio to 1** (a scaled pixmap keeps the
  old ratio, which silently redraws it at two thirds size) and then clips it
  with the real mask.
- **Redirect the settings first.** Every harness points `ann_core.config_path`
  at a throw-away copy before building anything. A render script must never be
  able to cost the operator a rectangle.

---

## The item

One dict shape, two flavours, in one top-level `items` list.

**A row is identified by its `id` and by nothing else, and taking rows off the
list goes through `C.drop_items`.** `list.remove` takes the first item that is
EQUAL, and an item is a plain dict — two rows watching the same channel with
the same limits compare equal, so `remove` would take the wrong one off and
leave the selected one sitting there, which reads as "Remove did nothing".

**Which buttons work on several rows.** The tables are whole-row select in Qt's
default extended mode, so the operator has always been able to Ctrl-click or
Shift-click a handful. **Remove works on one or more**; Edit, Duplicate, "Take
the picture again" and the two saved-rectangle buttons each ask a question
about a single row and stay one-at-a-time. That is the split between
`_selected()` and `_selected_many()` on both tabs — and getting it wrong is not
a small thing: every button used to ask `_selected()`, which answers None
unless exactly one row is picked, so selecting three rows greyed out the whole
row of buttons and looked like the program had lost the selection. The question
that Remove asks **names the rows** (`U.name_list`); a count alone is not
something anyone can safely say yes to.

```json
{"id": 7, "kind": "value", "name": "Back reflection", "on": true,
 "fires": "alarm", "presets": ["Night shift"],
 "message": "Back reflection over the limit",
 "pv": "L3-PM03-023:Energy", "minus": null, "unit": "mJ",
 "lo_lo": null, "lo": null, "hi": 1.0, "hi_hi": 2.0,
 "read": "peak", "window_s": 10}

{"id": 8, "kind": "area", "name": "L3BT alignment check", "on": true,
 "fires": "alarm", "presets": [], "message": "Alignment check changed",
 "monitor": 1, "screen": "\\\\.\\DISPLAY2",
 "region": [2919, 166, 3288, 349], "threshold": 1.0,
 "reference": "<base64 PNG>"}
```

- **`id` is saved and never reused.** The old program keyed a condition's badge
  on a counter that restarted every run, and a badge for one of the eight values
  on its index in a hard-coded table. Neither survived an edit, because editing
  replaced the dict. The saved id is what lets the per-item state, the badges and
  the preset membership all point at the same row across an edit.
- **Any of the four limits may be `null` = that limit is off, and `null` is not
  zero.** A zero limit on an energy fires the moment the laser runs.
  `parse_level` is the one place a box becomes a number or None.
- **`read`** is `peak` (the worst sample of the window — one shot over the limit
  is the whole event), `mean` (the average of the newest 25 — a single chiller
  sample crosses ±0.3 °C constantly, the average does not) or `last` (the newest
  sample however old, with the widening look-back).
- **`minus`** is the second channel of a difference, held forward. A chiller's
  setpoint is written once a week, so a ten-second window of it is empty almost
  always; subtracting "no value" would make the deviation unreadable exactly
  when the chiller is behaving.
- **`presets`** are the sets of alarms this row belongs to. One set is chosen in
  the header and is then the only thing listed **and the only thing watched** —
  the engine is handed `active_items()`, not the whole list, so the alarms of
  another preset are not even read from the archiver. A row may be in several
  sets; a row in none of them appears under the automatic set **Unassigned**.
  There is deliberately no row that appears in every preset, and deliberately no
  preset **column**: a column cannot hide the other alarms, which is the whole
  point.
- **There is no `group` any more.** It was the AND — rows sharing a group name
  fired only once every one of them had stopped holding. The rule is gone and
  each row fires on its own verdict; `firing_ids` is now one set comprehension.
  An old file's group names are carried across as preset names, so the words the
  operator typed survive, and the migration says so in the log.

### Migration, once, on the first start of this version

| Old | Becomes |
|---|---|
| the eight `_PV_MONITORS` badges | fourteen ordinary `value` rows, `fires: "show"`, `read: "mean"` |
| a chiller's absolute range (the purple badge) | a **second** row on the raw `Temp`, 7–17.5 °C, or 18–22 for the Utility chiller |
| `pv_thresholds`, both its shapes | the limits of those rows — the operator's typed number wins over the table default |
| `kind: "screen"` | an `area` row, reference picture and all |
| `kind: "pv"` | a `value` row, `warn`→`hi`, `trip`→`hi_hi` |
| a `pv` condition's `gate_*` area | a separate `area` row, `"<name> · area"` |
| `kind: "hall"` | dropped, with a log line naming it |
| a row's `group` name | a **preset** of the same name, with a log line saying the AND is gone |

Splitting a gated value into two rows is exact, not a compromise: the old
`_watched_areas` put the attached picture in the same flat list that any single
failure tripped, so "both must hold" was already "either failing fires".
OR-of-failures is AND-of-holds.

What the two-row chiller loses is the purple badge's **priority** — a chiller
holding its setpoint perfectly at the wrong temperature used to be one purple
warning and is now one red row beside one green one. The names carry it instead:
"Chiller DA1 — temperature" says what is wrong on its own, and the Watch banner
names the worst thing outright.

---

## Where the settings live

`presets.json`, next to the program (`INFRASTRUCTURE.md`), keeping its name and
every key it had. The operator's saved rectangles, window positions, flash
settings and reference pictures came through the rewrite untouched.

| Key | What |
|---|---|
| `items` | **new** — the watched things |
| `conditions` | the old list, left where it is so the tkinter version would still start |
| anything else not reserved | a **saved rectangle**, by name: `[x1,y1,x2,y2]` or `{"region": [...], ...}` |
| `pv_thresholds` | the old limits table, read once by the migration |
| `flash_mode`, `flash_color`, `color_cycle`, `flash_interval`, `flash_duration`, `image_file` | the flash |
| `sound_enabled`, `sound_file`, `sound_freq`, `sound_duration`, `sound_leadin`, `sound_device` | the sound |
| `alarm_geometry` | `[x, y, w, h]` — the LAST place the flash was put, whatever the preset; only a fallback and a roll-back path now |
| `hud_position` | `[x, y]` — the same, for the circle |
| `placements` | `{preset: {"hud": [x, y] or null, "alarm": [x, y, w, h] or null}}` — where those two sit **under each preset** |
| `alarm_presets` | the preset names, in the order they were made — kept on its own so an empty preset survives |
| `active_preset` | the set being worked in; `""` is all of them, `"\nunassigned"` the automatic one |

`RESERVED_KEYS` has to be exactly right: everything at the top level that is not
in it is a rectangle's name, so a migration that forgets one key turns
`flash_mode` into a region called "flash_mode". **It was wrong**, and the two it
was missing were `alarm_geometry` and `hud_position`: the first time the
operator positioned the flash or the circle, those two appeared in the
saved-rectangles drop-down on the Areas tab. `alarm_geometry` is even a
four-number list, so **Load** accepted it and fed `[x, y, w, h]` in as
`[x1, y1, x2, y2]`, and **Delete** permanently forgot where the alarm appears.
Both are in the set now, and so are `alarm_presets`, `active_preset` and
`placements`, which would have fallen into exactly the same hole the first time
a preset was made or placed.

**Per-preset places.** `placements` is keyed by the preset the header is on,
the two that are not real presets included (`""` for All, `"\nunassigned"` for
Unassigned). A key that is **present and `null`** means "this preset was put
back to the default corner on purpose", which is not the same as a preset with
no key at all — that one borrows `hud_position` / `alarm_geometry`, so an
existing settings file does not move anything on the first start of this build.
`C.has_placement` asks whether the key is there, `C.placement` what is in it,
and `C.spread_placements` copies the current preset's two places into every
preset that has neither — the Alarm tab's "Use these places for every preset
that has none". Renaming or deleting a preset carries or drops its entry
(`rename_placements`, `drop_placements`). Naming a rectangle after a
setting is refused outright — in the old program it offered to "overwrite" the
condition list and then destroyed it.

**Writes are atomic** (temp file, then `os.replace`) because the reference
pictures live in here; a half-written file would cost every rectangle the
operator ever set up. Saves are coalesced one second after the last change, and
on close as well.

---

## Two coordinate spaces, one boundary

Measured on this PC on 2026-09-16 with PySide6 6.11.1:

| screen | Qt logical geometry | dpr | Windows says |
|---|---|---|---|
| `\\.\DISPLAY1` | 0,0 1280x720 | 1.5 | 0,0 1920x1080 |
| Q27P3C | 1920,-174 2560x1440 | 1.0 | the same |
| Q27P4U | 4480,-174 2560x1440 | 1.0 | the same |

- Qt 6 is **per-monitor DPI aware out of the box** (awareness level 2, measured),
  so its geometry is logical pixels.
- A screen's **origin is the same number in both spaces; its size is not.** The
  laptop is 1280 logical and 1920 physical wide while the next screen starts at
  1920 in both, so **Qt's logical desktop has a 640 px hole in the middle of
  it.** The union of `QScreen.geometry()` is not a coordinate space: always pick
  a screen first and work inside it.
- `PIL.ImageGrab` is unconditionally physical. `grab(all_screens=True)` comes
  back 7040x1440 for a desktop that starts at y = −174, whatever the process's
  awareness, and it works the offset out itself — **do not subtract it by
  hand**; doing that once moved every shot 174 px down.
- Windows' own `GetMonitorInfoW`, in this DPI-aware process, agrees with the
  derived physical rectangles exactly, so the ctypes block the tkinter version
  needed is gone.
- **A small rectangle is not a cheap grab.** `grab(bbox=…)` photographs the
  whole virtual desktop and crops afterwards, so one 300x200 rectangle costs
  the same as everything on every screen: measured 2026-09-17 on this PC,
  **269 ms and a 30 MB buffer per call** at 7040x1440. Asking once per watched
  area, twice a second, is more work than there is time to do it in — four
  areas did not fit in their half second — and it churned 120 MB a second. So
  `grab_regions(regions)` takes **one** photograph of the bounding box of all
  of them and crops each one out of it, and `_compare_areas` calls that once
  per pass. The Areas tab's live preview has its own 1 Hz clock for the same
  reason: it used to hang off `engine().changed`, which fires up to four times
  a second, and it grabs on the GUI thread.
- **A thumbnail needs its own edge drawn.** A watched rectangle is nearly always
  smaller than the box it is previewed in, and what is usually inside it is a
  white dialog — on a white label that is an invisible boundary, and the
  operator cannot tell what they actually dragged out. `framed_pixmap` scales
  the picture, then draws a pale ring hard against it and a black line outside
  that: the pair reads on light content and on dark alike, which one line of
  either colour does not. `scaled_pixmap` is left alone underneath it, still
  nearest-neighbour, because a watched area is often one small number or a thin
  indicator lamp and smoothing is the detail being looked for.

**The rule: a watched rectangle is always physical pixels.** Qt's logical pixels
exist only inside widgets; `to_physical` / `to_logical` are the only conversions
and they are per screen.

Every rectangle the tkinter version saved still works, because it was physical
too — by accident: `screeninfo.get_monitors()` calls `SetProcessDpiAwareness(2)`
inside itself, and the old program called it at start-up, so it had become
DPI-aware before it ever drew a selector.

**The safety net for the one case that could still be wrong** (a rectangle saved
in a session where that flip did not happen, on the scaled screen) is the size
check: a grab whose size no longer matches its reference is a **trip**, with the
two sizes named. The size is compared in `picture_diff` and again in
`judge_area`, and not left to PIL — measured with Pillow 12.2.0,
`ImageChops.difference` on two different sizes raises nothing, silently crops to
the smaller one and hands back a perfectly plausible number.

---

## The engine

Two clocks, neither sharing anything with the other:

| clock | what it does | where |
|---|---|---|
| values | one HTTP pass over every value row | a worker thread |
| pictures | grab and compare every area row | a second worker thread |

500 ms while watching, 2000 ms while not.

### The crash this cost, and the shape that fixes it

The first version made **one `QRunnable` per pass, each carrying its own
parentless `QObject`** to signal through, handed to a `QThreadPool`. It passed
every unit test, looked right in every screenshot, and then killed the program
with an access violation after an hour or two of ordinary watching: no error, no
traceback, no freeze — the window simply gone. Reproduced on 2026-09-17 both in
the real program (two runs, dead at 100 s and 170 s) and in **forty lines of
isolation** with nothing else in it.

The cause is ownership. `QThreadPool` deletes the C++ runnable when `run()`
returns, PySide6 then invalidates the Python wrapper, and the parentless signals
object hanging off it goes with it — sometimes while its own queued emission is
still sitting in the GUI thread's event queue. **Holding a Python reference to
the runnable does not fix it**: it turns the crash into `RuntimeError: Signal
source has been deleted`, which is the same bug wearing a coat.

So there is no `QRunnable`, no `QThreadPool` and no per-pass `QObject` here.
One `_PassSignals`, **parented to the engine**, of which exactly one is ever
made — which also settles the other half of the problem, that an object created
twice a second and never collected is a few kilobytes a time and half a gigabyte
a week. The passes run in a two-thread `ThreadPoolExecutor` and emit through
that one object. The test that caught it was `bench_engine_soak.py`: ~10 000
passes at twenty-five times the real rate, about ten hours of watching, in a
minute and a half. **That harness has been deleted** at the operator's request
(see the `testing/` section) — so a change to the engine's threads or signals
now has nothing standing behind it, and the shape above is the whole defence.
**One `_PassSignals`, parented, made once. Never a per-pass QObject.**

**The guard is a generation stamp, not a boolean.** A plain boolean is how a
feature dies until restart: one pass that never returns leaves the flag set and
nothing runs again. So:

- the timer **always ticks**, even when it dispatches nothing — it is also the
  heartbeat that notices a pass has gone missing and re-judges staleness;
- each pass carries a number, and a result whose number is no longer current is
  dropped;
- a pass still outstanding after `max(5 × interval, 3 × timeout + 15 s)` is
  **written off**: logged, counted, the flag cleared, a fresh pass started. The
  old one cannot be cancelled, so it is abandoned and its answer is never looked
  at.

Inside one pass the fourteen-odd reads **fan out** over a small thread pool, and
the whole pass has a deadline. That is what stops one unreachable channel from
stretching a pass to the sum of every timeout, and an abandoned read is counted
so "sometimes a value is missing" becomes a number in the log.

**Staleness** is the age of our last GOOD read, in elapsed local seconds — a
difference, never a comparison against an archiver timestamp, because this PC's
clock runs about 25 s ahead of the facility. It is **not** "the value stopped
changing": the archiver writes on change only, so a chiller holding its setpoint
publishes nothing for minutes, and that is a healthy chiller.

**Latching.** A trip is announced once and not twice a second; the latch clears
when the item returns to normal, so a second event is a second sentence.

**The alarm is one-shot.** The first `fires: "alarm"` item to trip raises it, and
whoever is listening stops the watch — so nothing re-alarms. Reset arms it
again, and if the thing is still wrong it fires straight away, which is the
truth.

---

## The second crash: a queued event delivered to a freed object

Later on 2026-09-17 the program died three more times, within a hundred seconds,
while the operator was pressing **Draw the area**. It is a different bug from
the one above and the evidence is worth keeping, because nothing in the program
recorded it: there was no log file of any kind. The only trace was Windows' own
`Application` event log and three minidumps in
`%LOCALAPPDATA%\CrashDumps` — faulting module `Qt6Core.dll`, exception
`0xc0000005`.

**What was measured, out of the dumps themselves.** All three faulted on the
**main GUI thread**, on the same spine:

```
QEventDispatcherWin32::processEvents
  QWindowsGuiEventDispatcher::sendPostedEvents
    QCoreApplicationPrivate::sendPostedEvents      <- one faulted here
      QCoreApplication::notifyInternal2            <- one faulted here
        QApplication::notify → notify_helper
          QCoreApplicationPrivate::sendThroughApplicationEventFilters
            shiboken: wrap the receiver for Python
              QObject::metaObject                  <- one faulted here
```

In one dump the faulting instruction is the atomic decrement of
`pe.receiver->d_func()->postedEvents`, with the `d_ptr` reading `0x16`. In
another the `d_ptr` is `0x5`; in the third it is `0x3ff0000000000000`, the bit
pattern of the **double 1.0**. Small integers and a float: this is memory that
was freed and handed out again, not a null and not something uninitialised.

So: **Qt was delivering a queued event to a receiver that no longer existed**,
and the application-wide Python event filter then made shiboken wrap that
dangling pointer into a Python object, which is where two of the three landed.
No paint frames on any stack — this is not the painter bug.

### What that means for how this program is written

### What the soak measured, old against fixed

`testing/bench_area_cycle_soak.py` drives the real `AreasTab`, `AreaDialog` and
`RegionSelector` through the whole cycle — press Add, press Draw, answer the
drag, press Save — hundreds of times. Over 250 cycles:

| | old | fixed |
|---|---|---|
| areas actually saved | **0** | every one |
| `AreaDialog`s still alive at the end | **312** | 0 |
| selector overlays still alive | **27** | 0 |
| working set | **1133 MB** | ~107 MB |
| commit | **1087 MB** | ~61 MB |

The old numbers grow linearly and without bound — 310 MB at 50 cycles, 1133 MB
at 250, about 4 MB and one or two live hidden windows per attempt. **Neither
version actually faulted inside 250 cycles**, so the soak does not reproduce
the crash on demand; what it does is prove the leak, and three hundred hidden
modal dialogs with timers still firing into them is the soil that crash grew
in.

It also counts photographs separately from cut-out rectangles, which is the
whole point of `grab_regions`: in the fixed run **4353 rectangles came out of
551 photographs**, and at the end one photograph served 150 areas. The old code
took one photograph per rectangle — 4353 × 269 ms is nineteen minutes of
blocking work instead of two and a half.

### The five rules that came out of it

They are cheap, and each one closes a door that was proved open:

1. **`S.retire(obj)`, never a bare `deleteLater()` followed by dropping the
   reference.** `hide()` and `deleteLater()` both POST events addressed to the
   object. `retire` keeps the Python reference and lets go only when `destroyed`
   fires, which is the first moment at which the queue is guaranteed to be
   clear. Used for the selector overlays, the numbered-monitor labels and the
   area editor.
   *Honest note:* in isolation, `deleteLater()` followed by dropping the last
   reference did **not** fault in 6.11.1 over thousands of rounds — PySide6
   does hand ownership over. `retire` is therefore belt and braces rather than
   a proven cure, and it costs nothing.
2. **A shown parentless window must be reachable from something that outlives
   the caller's variable.** `RegionSelector` is a `QObject` now and puts itself
   in `ann_screen._LIVE_SELECTORS` for as long as its overlays are up, so a
   second press of *Draw the area* cannot drop three visible windows. It also
   refuses to open twice, and the button is disabled while a drag is in
   progress.
3. **A dialog that hides itself may not be run with `exec()`.** This one is not
   a crash but it is the reason the operator was pressing that button over and
   over: the area editor hides itself while the rectangle is dragged out —
   otherwise the program photographs its own window — and **hiding a QDialog
   ends its `exec()` loop**. So `exec()` returned "Cancel" the instant *Draw
   the area* was pressed; the dialog came back, the rectangle was drawn, Save
   was pressed, and the caller had long since given up. **Nothing this dialog
   ever produced was saved** — the operator's `presets.json` has fourteen
   values and zero areas. `AreasTab._open` now uses `show()` and the
   `finished(int)` signal, which also stops each attempt leaving an orphaned,
   still-modal dialog behind with timers firing into it.

   **The same rule, the other way round, and it cost the operator two dead
   buttons.** `QDialog.exec()` sets `WA_ShowModal` itself, whatever
   `setModal(False)` said when the box was built — and an application-modal
   dialog **blocks mouse events to every other top-level window in the
   program**. The little "Where the alarm appears" and "Where the circle sits"
   boxes were both opened with `exec()`, and the alarm window and the circle
   ARE other top-level windows: the press that asks Windows to start a drag
   was never delivered to them, so neither could be moved by a pixel
   (reported 18.9.2026). `AlarmTab._place` / `_place_circle` now use `show()`
   plus `finished`, and `test_placing.py` reads those two handlers as source
   and fails if `exec` appears in either. So: **a dialog that stands beside a
   window the operator has to touch may not be run with `exec()`** — and with
   the box no longer blocking, the two placing buttons are greyed by hand
   while one is open.
4. **Every deferred step of drawing an area checks that the editor still
   exists.** Drawing is a chain of `QTimer.singleShot`s — hide, open the
   overlays, grab the reference, come back — and the operator can close the
   editor inside any of those delays. `AreaDialog._finished` is set by an
   overridden `done()`, every callback returns early on it, and `done()` also
   takes any live selector down with it and puts the main window back if it
   was the one that hid it. `bench_area_cycle_soak.py` found this: closing the
   editor within the 150 ms before the overlays appear left a selector nobody
   owned, and because one drag at a time is the rule, *Draw the area* then
   refused to work at all.
5. **Nothing Qt calls may raise.** An exception escaping a Python override does
   not stop at the boundary: PySide6 leaves it set, every later override fails
   with `SystemError`, and the process dies with an access violation in the
   next garbage collection — measured, and it is how the first version of
   `test_selector.py` took itself down. So every `paintEvent` in the program is
   a bare painter plus a `_paint_body` under `try/finally`; the application-wide
   wheel guard in `ann_ui.py` — the one piece of Python Qt calls for *every*
   event in the program — wraps its whole body; and `AlarmWindow.resizeEvent`
   and `HudWindow.moveEvent`, which do real work, are guarded too.

### And now there is a log

`ann_log.py`, installed by `main.py` **before Qt is imported**, writes
`%LOCALAPPDATA%\Announcer\announcer_crash.log`: `faulthandler` (the only thing
that can record an access violation, which is not a Python exception and has no
traceback), `sys.excepthook`, `threading.excepthook`, and Qt's own message
handler — so `QBackingStore::endPaint() called with active painter` and
`Signal source has been deleted`, which are Qt naming exactly these bugs, land
on disk instead of in a console a frozen build does not have. Verified against a
real hard fault: the file names the file and line.

**If it happens again, read that file first.** Rotated at 2 MB to
`announcer_crash.log.1`.

### What the same audit fixed while it was in there

None of these is the crash, but all of them are work the program was doing for
hours at a time, or undefined behaviour one step away from being fatal:

- **`waveOutReset` before `waveOutUnprepareHeader`, and never free a buffer the
  driver still owns.** If the wait for a sound runs out — a stalled Bluetooth
  link is exactly that — unprepare returns `WAVERR_STILLPLAYING` and does
  nothing, close refuses too, and Python then freed a buffer winmm was still
  reading from. That is a corrupted heap, not a missing beep. Reset first,
  retry the unprepare, and if it still refuses, leak the handle and the buffer
  into `ann_sound._ORPHANED` on purpose.
- **Repainting is idempotent and visibility-gated.** `WatchTab._refresh`
  returns immediately when the tab is not on screen (and repaints once on
  `showEvent`) — while watching, the whole window is hidden and it was still
  rewriting seventy cells with a tooltip and a font each, twice a second, for
  hours. `_paint_row` and `_set_banner` remember what they last applied;
  `_BadgeStrip.set_badges` does nothing when the sentences are unchanged and
  only calls `show()`/`raise_()` when it is not already up. With the lab idle
  every machine value is legitimately out of range, so "nothing changed" is the
  normal case, not the rare one.
- **A failing channel says so once.** `latched` only ever covered items that
  had fired, and a failed read never fires, so a dead channel emitted two log
  lines a second for ever. `ItemState.err_said` re-announces only when the
  words change or after five minutes.
- **One thread each, in two pools.** A shared pool of two is how both clocks
  die together: a pass cannot be cancelled, so two genuinely hung passes (a
  locked session blocks `ImageGrab`; a stalled socket outlives its timeout)
  occupied both threads for good and every later pass queued behind them for
  ever, while the log went on saying "written off". Separate single-thread
  pools are what the comment always claimed.

### Still open, deliberately

- **The alarm window is shaped, so a click in a transparent part of the picture
  falls through to whatever is behind it.** `render_alarm.py` prints
  `middle OUT (wrong)` for every image mode, and it is right to: with the viper
  the shape is 27 % of the rectangle and the geometric middle is not in it. Esc
  always works and clicking the picture itself works, so it is not a dead end —
  but "click the flash to dismiss it" is only true where the picture is opaque.
  Whether to keep the shape or make the alarm catch every click inside its
  rectangle is the operator's call, not a bug to fix quietly.
- **A watched rectangle is one pixel narrower than the one drawn.** `region` is
  stored from `QRect.right()/bottom()`, which are inclusive, and handed to PIL
  as a bbox, which is exclusive. Harmless — the reference and the live grab
  agree, so no verdict is wrong — and only the size shown in the table is off
  by one. Left alone because changing it would make every stored reference the
  wrong size and trip every area once.
- **`item["monitor"]` is a positional index** and goes stale if the monitors are
  re-arranged. Nothing depends on it: `screen_for_region` works from the
  rectangle itself. It is only a label in the table.

---

## The alarm

`ann_alarm.AlarmWindow` — borderless, always on top, translucent. Either the
whole window is filled with the colour, or one shape is, and the shape **is the
window**: `setMask` from the picture's own alpha, which the Windows plugin turns
into `SetWindowRgn`, so everything around the silhouette is neither painted nor
hit-tested and the desktop behind it takes the clicks.

Three things the tkinter version needed are gone because Qt draws real per-pixel
alpha: the near-black colour key every pixel had to be painted in, the rule that
the key must never equal the flash colour, and **`FLASH_OFF_ALPHA = 0.02`**. That
constant existed only because tk could not blank the picture without also losing
the click that dismisses the alarm; here the dark half of a blink simply paints
nothing and the click target cannot be lost. In colour mode the off half is the
fill at a quarter brightness, which is the pulse the operator knows.

**The moving rainbows keep their lookup table.** The grey map that says which
colour each pixel gets is built once per size with PIL and wrapped in a
`Format_Indexed8` QImage; a frame is then nothing but a new 256-entry colour
table, so the per-pixel cost per frame is zero. A `QLinearGradient` would be
fewer lines but a different picture — different ring spacing, and visible
banding between its handful of stops.

| cycle | what it does |
|---|---|
| `fixed` | blink in the picked colour |
| `rainbow_blink` | blink, the hue jumping 47° each time — a clearly different colour |
| `rainbow_spectrum` | blue-to-red across the width, drifting, blinking |
| `rainbow_wave` | rings out of the centre, **never dark** — its travelling colours are the alarm |

Retired values are translated on load: flash mode `alternate` → `image`, cycle
`rainbow_smooth` → `rainbow_wave`.

A duration of 0 means "until it is dismissed", which is the default: an alarm
nobody saw did not work. Esc and a click anywhere on the shape both dismiss it.

**An alarm picture with no alpha channel is one solid rectangle.** Such artwork
has to be converted first — alpha = the inverse of the greyscale, hard-thresholded
at 128, cropped to `getbbox()`. `images/viper.png` is that conversion of the
delivered line art.

### The panel that stays on screen

While watching, the tabbed window disappears and only the circle is left, which
is how this program has always worked. **Two windows, not one:** the circle is a
fixed size and carries the mask; the sentences sit in a separate plain rectangle
beside it, because a rectangle hit-tests correctly on its own and a mask around
a strip whose height changes with the number of sentences is exactly where a
mask and a layout start fighting each other — a fight already on record in
`Image Tools/is_t.py`.

Both windows are built with **no parent**: a `Qt.Tool` window with a parent is
hidden whenever its parent hides, and hiding the main window is the whole point.
`setQuitOnLastWindowClosed(False)` is set for the same reason.

**The strip must never sit on the circle.** The circle's home is the top right
corner, where there is no room to its right, so clamping the strip onto the
screen slid it straight over the circle and hid the one control that is always
meant to be visible — photographed by the operator on 2026-09-17.
`badge_spot()` tries each side in turn and accepts one only if the strip fits on
the screen there AND leaves the circle alone, falling back to underneath, which
is the one direction that cannot overlap whatever the strip's width. It is a
pure function over rectangles so `testing/test_hud.py` can walk every corner.

**The strip is capped at five badges plus a count.** With the lab idle every one
of the fourteen machine values is legitimately out of range at once, and fourteen
badges is a wall of red that says less than one line would. The badge for the
item that actually raised the alarm carries the operator's own sentence, because
that is the instruction; the others carry their reading, because when eight
things are wrong the numbers are what tell you what is going on.

**A reading that did not arrive gets a mark, never a sentence.** Asked for on
2026-09-21: the strip was filling with "not read yet", "could not be read" and
"not refreshed for 40 s", which is a column of red over the operator's real
work that he can do nothing about. `AnnouncerWindow._badges` now lets only
`STATE_WARN` and `STATE_TRIP` through. Instead, once the readings have been
failing for `C.NO_DATA_S` (five minutes), a red exclamation mark appears beside
the circle — `U.AlertMark`, drawn on nothing, no words, in the header row and,
on the desktop, as a third little window (`_NoDataMark`). Three details that
matter:

* **What counts as a failure is `ItemState.answered_at`, not `read_at`.** A
  channel written on change only answers perfectly and still has nothing in its
  window; that is a quiet channel, never "no data". Only a read that FAILED
  moves the mark. Rows switched off, values with no channel and areas with no
  rectangle are left out — they would pin the mark on for ever and it would
  stop meaning anything.
* **The mark is its own masked window** for the same reason the strip is its
  own window: the mask is the narrow strip the mark is drawn in, so the desktop
  on either side of it keeps taking clicks. Its mouse-transparent child paints
  it; the window itself is never a control.
* **The strip keeps off the mark as well as off the circle** — `_place_badges`
  hands `badge_spot()` the union of the two rectangles.

The log still says which channel and what the archiver answered, once, as it
always did: the mark replaces the sentences on the panel, not the record.

The circle's position is remembered, and it is **placeable** the same way the
alarm window is — *Place the circle* on the Alarm tab shows the real circle to
drag, with Keep / Cancel.

While an alarm is up the circle and the sentences **stay** — the badge is the only
place the operator's own words can be read, so putting the window back over them
would take the message away at the worst moment. Dismissing the alarm is what
brings the window back.

### The sound

`winsound.Beep()` drives the motherboard beeper through the kernel and never
reaches a Bluetooth speaker — that is why the lab once stayed silent while the
same script was plainly audible at the PC. So the beep is **synthesised** as real
samples and handed to a real output device through winmm.

The device is stored **by name**, never by number: the numbers shuffle every time
a Bluetooth speaker connects or drops. A named device that has gone falls back to
whatever Windows would have used — a missing speaker must not silence the alarm.
Every sound gets a **run-up of silence** (800 ms by default), because a Bluetooth
link only carries audio once it has opened and without the run-up the whole beep
lands in that gap. 8-bit wavs are padded with 128, not 0, because 8-bit PCM is
unsigned. And a sound failure never stops the flash: it falls back to the old
beep and moves on.

---

## The look

`ann_ui.install_app_look(app)` is Fusion, the stylesheet, an explicit palette and
the wheel guard, in one importable call. **Everything that puts this program's
widgets on a screen calls it** — the program, every test, every render harness —
because this PC runs Windows in dark mode and a widget that states neither a
background nor a foreground comes out black on black. A screenshot taken without
it lies about every colour in it.

- **Neither of Qt's automatic column widths can be used, so `ColumnFitter`
  computes them.** `ResizeToContents` sizes a column correctly and then refuses
  to be dragged — the operator could not widen *Say when it fires* to read a
  long sentence, and the divider did not even change the pointer. `Stretch`
  hands a column the room that is left over and ignores what is in it, which at
  the window's smallest gave that heading four pixels less than it needed and
  printed "Say when it fire". So every section is `Interactive`, the only mode
  that can be dragged, and the width is worked out per column: the heading's own
  hint against every cell, **skipping spanned cells** — Qt's own
  `resizeColumnToContents` counts them, and the Watch tab's full-width heading
  line would have made the tick-box column as wide as a sentence. A column the
  operator drags himself is remembered and left alone until `reset()`.
- **The narrow columns of the Values table are centred: *On*, *Lo Lo*, *Lo*,
  *Hi*, *Hi Hi*.** Headings and cells both, so a number sits under its own
  heading instead of looking like it belongs to the column next door. The tick
  box needed its own delegate — `setTextAlignment` moves only the TEXT of a
  cell, and measured on this Qt a tick-only cell draws its box at x 5..14 of a
  120 px column whatever alignment is asked for. `CenteredCheckDelegate` draws
  the cell without the style's left-hand indicator, paints the box in the middle
  itself, and takes the click in that same rectangle; presses and double clicks
  pass through, so the row still selects and still opens its editor.
- **Remove is a button AND a row-menu entry; "Assign to a preset" is the menu
  only.** Remove went menu-only for a while, on the argument that it is about a
  set of already-picked rows — and the operator went looking for the button and
  reported that "the Remove button does not delete" (21.9.2026). It is on both
  now, and on either it takes every picked row: `_btn_remove` is enabled from
  `_selected_many()`, not from `_selected()`, so it is live for one row or
  twenty while Edit, Duplicate and the one-row area buttons still need exactly
  one. `RowMenu` builds the menu when it opens, so its labels cannot be out of
  step with the selection, and each carries the count: "Remove these 3". Three
  rules in it, all about not lying to the operator: a right-click INSIDE a
  selection leaves that selection alone; a right-click anywhere else picks that
  row first; and **the entry's work is done after the menu has gone**, from
  `QTimer.singleShot(0, …)` on the action `exec()` returned, never from
  `triggered` while the popup is still up. Every entry there opens a window,
  and a modal "are you sure" opened underneath a menu that still holds Windows'
  mouse grab is one the operator cannot answer — which is exactly what "Remove
  does nothing" looked like. It is a `QObject` parented to the table, because a
  plain Python object with nobody holding a reference is collected the moment
  the tab has finished building and the menu then never appears again.
- **A menu states its own colours.** It is the one widget nothing else here
  styles, so left alone it comes up in the Windows dark theme inside a light
  window: `MENU_QSS`, like every other stylesheet in this file.
- **A row that is fine gets no band at all.** Fourteen healthy values painted
  green is a wall of colour in which the one thing that is wrong does not stand
  out. Only a verdict worth looking at gets a ground, and then it gets its ink
  with it — on **every** cell of the row, or the band breaks.
- **The row that fired is painted on the item**, never left to Qt's selection
  colour: the selection is where the operator last clicked, and that is a
  different question from what went wrong.
- **Three blocks, each under a spanned heading row**: watched and in range,
  watched and not, then not watched at all. Mixed together the three read as
  noise. An empty block gets no heading. The heading row is `NoItemFlags` on a
  `#dde3ec` band, and its column-0 cell carries a `QSize(1, …)` size hint —
  otherwise `ColumnFitter` sizes the *On* column to the whole sentence.
- **A row only moves when it changes block.** Inside a block the order is the
  operator's own, and the plan key holds the block plus the id, so a new number
  repaints and a new verdict rebuilds. A rebuild carries the scroll position and
  the selected row over by id, or every reshuffle jumps back to the top. The
  banner above the table still names the worst thing outright.
- **Icons are drawn**, in a 20-unit grid, at 18/27/36 px, with every
  `QIcon.Mode` spelled out — left to itself Qt invents a disabled version by
  fading the artwork until it is barely there, and keeps dark artwork on a
  checked blue button.
- **The arrows on the number boxes and the drop-downs are drawn too.** Measured
  by rendering: the moment a sub-control is given a style of its own, Qt stops
  drawing its native arrow, and both came out as a blank grey square. They are
  painted into the temp folder once and handed to the stylesheet as images — the
  same trick the sibling program uses for a list tick.
- An empty table gets **one greyed, unclickable line**, never a blank box.
- No button label ends in an ellipsis, and every non-obvious one carries a
  tooltip written as a sentence.
- A control that would change nothing is **disabled**, not left there looking
  broken — the beep's pitch when a wav is chosen, Edit with nothing selected.

---

## Dependencies

```
PySide6      the whole interface
Pillow       ImageGrab (the screen), the picture difference, the alarm stencil
ctypes       winmm, for the sound (stdlib)
urllib/http  the archiver, through a keep-alive pool (stdlib, no requests here)
winsound     the fallback beep (stdlib, Windows only)
```

`screeninfo` is **no longer used**: Qt's own screen list is enough, and the
library's hidden `SetProcessDpiAwareness` call was the thing that made the old
program's coordinate space an accident rather than a decision.

`ann_cpva.py`'s HTTP half is **copied** from `Image Tools/cpva_client.py`, which
is the master, rather than imported — the rule in this repo for anything a frozen
build would otherwise have to carry across folders. Only the part Announcer needs
is here; the whole-day cache that module is mostly made of is left behind,
because this program never asks for more than the last few seconds.
