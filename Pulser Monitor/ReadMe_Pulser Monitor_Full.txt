Pulser Monitor — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-10-27
  (main.py 8369 lines, scan_share.py 135)
-----------------------------------------------------------------
Short version: ReadMe_Pulser Monitor.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------

PySide6 GUI that scans the diode-array cameras over a chosen time window and counts every **blink** — every time a pulser went out while the array ran at full power — with the pulser, the time to the millisecond, and whether it went out fully or only dimmed. It also reports the diodes that died, whole-array trips (and which pulser most likely caused them), how many times the diodes had to be switched back on, and how long the array spent warming up.

---

## Quick Overview

| Tab | What it does |
|-----|--------------|
| **Pulser Map** | Grid of the array. In **Status** it runs green (never even flickered) through to light red (the worst pulser in this array), with dead pulsers in full dark red, a hatch on any that was replaced, and an x on any that took the whole array down; the number in a cell is how many times it blinked. Hover for a per-pulser readout; click to list that pulser's events (time to the millisecond, how deep it went) on a timeline, over its own score with the two lines it is judged by |
| **Statistics** | Blinks per pulser (full and partial stacked), blinks over time, the spread of time between a pulser's blinks, and three logs: the **stretches at full power** (what blinked in each), every **pulser event** (blinks included), and the **array stops** (pulser trip / dead pulser / bigger problem / manual stop / outage with no pulser / switched off, with the culprit and what became of it) |
| **All Data** | One sortable row per pulser — blinks, partial blinks and the rest — for this camera or for all four at once; CSV export |
| **Run Graph** | State of every pulser over real time — ON / OFF / no data, with warm-up, below-full-power, diodes-off and un-analysed stretches shaded across the full height. Every blink is marked on its pulser's row (hollow for a partial one), outage starts are marked, the array's first and last judged frame are labelled, and hovering reads out the Prague time and that pulser's score under the cursor (click to pin it) |

Left panel: pick cameras, set the time window, edit the ROI boxes per camera, choose which time counts, tune detection, scan, export.

- **Viewing camera:** — the drop-down above the tabs. After a scan it lists the scanned cameras and picks whose results the four tabs show.
- **Heatmap:** — the drop-down under the Pulser Map. Picks what the cell colour means: `Status` (the default), `Blinks`, `Partial blinks`, `Dropouts`, `Faults`, `Trips`, `Caused trips`, `Total outages` or `Uptime %`.
- **Set time window** opens the *Time window* dialog: a calendar and an `Hour:` box under **Start point** and under **End point** (`Now` sets the end to the current day and hour). The end hour is taken to its last second, so 7 to 21 means 07:00:00-21:59:59.
- **Scan** / **Stop** start and stop a scan. When the program runs on its own, a red **Stop Scan** button at the bottom right of the window does the same as **Stop**.

Note the difference between the **time window** (which frames are read off the share) and **Analysed time** (which of them are judged). The window is what you ask for; the gate is what the laser was actually doing.

---

## Files

| File | What it is |
|------|-----------|
| `main.py` | The whole application — measurement, analysis and UI in one file. Run it directly: `python main.py` |
| `C03-*-allgood.png` | Per-camera reference frame with **every pulser lit**. Every measurement is relative to this; without it a pulser that stayed dark for the whole window cannot be flagged at all |
| `C03-*-warmup.png` | Per-camera reference frame taken while the array was **warming up**. Without it warm-up time cannot be measured |
| `testing\test_pulser.py` | Offline test of the analyser: synthesises every dropout / fault / trip / death and every kind of stretch without data out of the real reference frames |
| `testing\test_gui.py` | Headless test of the four tabs and every CSV / PNG export |
| `testing\test_real.py` | Same pipeline on genuine running and warm-up frames |
| `testing\test_gate.py` | Test of the active-time gate: span algebra, an overnight window, the panel controls. `--live` adds a real archiver query |
| `testing\test_blinks.py` | Test of blink counting: the full-power stretches from a dump-bus series, the energy dips and the low-power mode, which frames are read, and a scripted day — one-frame and three-frame blinks, a partial blink, a neighbour dimming through the optical coupling, a whole-array dip, the low-power mode, the ramps, a dead pulser across the lunch-time switch-off, an energy dip with no pulser in it |
| `testing\bench_day.py` | One real day through the program's own pipeline without the window: times each step, prints the stretches table and writes the exports. Reads the share for minutes — a heavy job |
| `testing\render_tabs.py` | Draws every tab to PNG with the real fonts and nothing on screen, from a scripted day or from a `bench_day.py` result, so the views can be looked at |
| `scan_share.py` | Runs the real pipeline over any window on the share without the GUI, for checking a day quickly. Prints **every** stretch without data with its length, what it was read as and why, plus the full per-pulser fault log — the two tables to look at when a count looks wrong |
| `testing\probe_morning.py` | Prints the per-frame array level across a morning to the console (it opens no window) — handy when a period needs explaining |
| `build_config.json` | Build settings for Dev Tools. It lists the eight reference PNGs so they are copied next to the exe — a build without them cannot flag a pulser that stayed dark, and cannot measure warm-up |
| `STRUCTURE.md` | Developer map of the file: where each block, class and function lives |
| `gui_out\`, `23062026\`, `test01072026\` | Test output and captured sample days. Nothing reads them |

Run all the test files (`testing\test_pulser.py`, `test_gui.py`, `test_real.py`, `test_gate.py` and `test_blinks.py`) before changing the analyser.

ROI boxes live outside this folder, in `%APPDATA%\PulserMonitor\rois.json`.

`icon.ico` is the program's icon (drawn by `testing\make_icon.py`); the title bar, the taskbar button and the built exe all use it.

---

## Detailed Description

### What a scan does

0. **Work out which time is worth judging** (see *Analysed time* below) — once per scan, before any camera is touched.
1. **Read full power and the energy** of all four arrays from the archiver (see *Full power* below) — about a hundred chunked queries for a day, under a minute.
2. **Find the frames.** `<share>/<year>/<month>/<day>/<hour>/<camera>/` — folder names are UTC and unpadded, the filename carries a 19-digit Unix-ns timestamp. Hour folders are listed on 12 threads, which is almost entirely network wait (~17x faster than one at a time). Then pick the frames to read: nothing while the dump bus says the diodes are off; at full power every frame on PD2M1, and on PD1M1/PD3M1/PD4M1 the frames within 1.5 s of an energy dip plus one every 5 s; one every 5 s while warming.
3. **Measure each frame** in a worker process: for every ROI box, `contrast = p75(box) - p10(window reaching 35 % beyond the box)`, plus the plain box mean and a whole-frame mean.
4. **Analyse** the series: throw out frames that do not show the array, find warm-up, split the day into stretches at full power, score every pulser against its own level at the start of its stretch, turn the dark runs into blinks and the other events.

### Analysed time

Two rules decide which parts of the window are judged at all. Both are **exclusions, never detections**: a period failing either one is left out rather than reported as a fault.

| Rule | Default | What it does |
|------|---------|--------------|
| **Only 07:00-21:00 (Prague)** | on | Judge only the working day. Hours are adjustable |
| **Only while high power is enabled** | on | Judge only while `L3-SIS-KEY:HighPowerEnable` reads 1, read from the archiver |

**The high-power rule is the one that changes results.** Measured on 1-17 Aug 2026, the archiver writes **nothing at all** outside roughly 07:00-21:00, and nothing on weekends — so the hour rule removes no real frames and only makes the scan faster by skipping the enumeration of empty hour folders. The key, on the other hand, is switched on well after the cameras start recording: on 10 Aug the frames begin at 07:00 and the key only went on at 12:28, so **five and a half hours of that day are frames of an array that was not supposed to be firing**. Judged, they are dropouts and dead pulsers that never happened.

`L3-SIS-KEY:HighPowerEnable` is an *enum* and only its **transitions** are archived, so the state a window opens in has to be found by looking back before it — the longest gap measured between two samples is 11.6 days. If the archiver cannot be reached the scan still runs, with the hour rule only, and says so in the log and on the Statistics tab; it never gates on a guess.

Excluded time is **accounted for, not deleted**:

- it never produces a dropout, an array trip or downtime;
- it is subtracted from every span, uptime denominator and downtime figure, so the numbers answer "while we were looking", not "of the calendar";
- it acts as a **hard break** — nothing is carried across it. A pulser dark at 20:59 and lit at 07:05 is not credited with a ten-hour outage nobody watched, and one lit at both ends is not credited with a ten-hour clean run it was never observed to complete. An outage still open when the window closes is recorded as ending there, and is never called *dead*: a pulser dark at 21:00 has been shown to be dark, not to be broken;
- a trip still running at the boundary is reported as ending at the boundary, so if it is still down next morning that reads as two honest observations rather than one claim about the night;
- it is drawn on the Run Graph and the map timeline — hatched indigo, distinct from the solid grey of a real trip, which means the opposite thing.

If the gate removes the whole window the scan stops and says so, rather than reporting forty pulsers at 0 % uptime.

#### How this relates to the *switched off* stops

Two different mechanisms answer the same question — "were the diodes even meant to be on?" — and they are deliberately not merged:

| | Where it gets its answer | What it does with it |
|---|---|---|
| **Only while high power is enabled** | the archiver, from the operator's key | removes the time from the analysis entirely |
| **switched off** (a kind of array stop) | the dump bus, or the images themselves from their lack of structure | keeps the stop, listed in the array log as *switched off*, never counted as an outage |

**The key wins where it is available**, because it is a direct reading rather than an inference: when the archiver answers, that time is gone before anything is classified, so no *switched off* stop is produced for it at all. When the archiver cannot be reached the key rule switches itself off, every frame in 07:00-21:00 is read, and the image-based test carries the day on its own — which is why it must stay. Measured on 10 Aug: ungated, the evening reads as one *switched off* stop; gated, it is not in the result at all.

So the two are a primary source and a fallback, not a duplicate. Neither one may be removed on the grounds that the other covers it.

### Full power

A blink only counts while the array runs its 520 A script at full intensity. **Only at full power (dump bus)** (on) reads that from two archiver channels per array, measured on 19-23 Oct 2026:

| Channel | What it says |
|---------|--------------|
| `L3-PC<k>-PC<k>:VoltageInternalDumpBus` | 92-106 V at full power (it ripples with the shots), 76-83 V while warming up, near zero when off. At or above 88 V is full power. All four converters switch together |
| `HAPLS-ENER_IN_PD<k>M1_LT7_DIAG2:Energy` | the array's output energy, ~3.3 Hz. A pulser going out takes 3-5 % off it for a shot or two, a partial blink ~0.4 %, against noise of ~0.44 %. Below 80 % of its stretch's usual level is the **low-power mode**, which the voltage does not see (20 Oct 16:44-17:04: all four arrays at ~25 %, voltage still above 88 V) |

- A **stretch** is one unbroken run at full power by the voltage. Its first 30 s and last 15 s are the ramps and are not judged.
- The low-power stretches of **any** array are left out of **every** array, padded by 5 s — the arrays change level together, and this is what covers PD2M1, whose own energy is not archived (two samples per ten minutes; its energy meter is in fault).
- While the voltage says the diodes are off, no frame is read at all; the gap is what the array log reads as the array being down.
- **Read only around energy dips (faster)** (on): on the arrays whose energy is archived, only the frames within 1.5 s of an energy dip (below -0.35 % of its running median) plus one every 5 s are read. Every blink shows up as such a dip, so the blinks are all still read, at a fraction of the time. Off: every frame at full power is read on every array — the way to check a day against the fast reading.
- If the archiver cannot be reached, every frame is read and full power is judged from the pictures (no warm-up, no outage), and the log and the Statistics tab say so.

### How a blink is found

The operator spots a blink by comparing a frame with how the array looked when the run began: the difference shows which pulser went out. The program does the same with numbers, per stretch:

- **reference** — each pulser's median contrast over the first minute of the stretch;
- **score** — the frame's contrast over that reference, divided by the frame's **array level** (the median of that ratio over the lit pulsers), so the whole array brightening or dimming together cancels and only one pulser moving against the rest remains.

Measured that way a healthy pulser stays within a few percent of 1.00 for hours, which is what makes a partial blink visible at all. A pulser is **dark** in a frame when its score is below `Dark below` (0.80); a dark run is a **full** blink when its lowest score went below `Full blink below` (0.55), a **partial** one otherwise. Measured: full blinks 0.25-0.55, partial ones (PD1 A8, PD4 E4/E7) 0.51-0.75.

Not judged, and drawn light cyan as *below full power*:

- a frame whose array level fell more than 15 % below the level around it, or in which four or more pulsers were dark at once — the array changing, not a pulser blinking;
- the ramps and the low-power mode (above);
- a stretch with fewer than three frames to take a reference from (listed as "not judged").

A pulser already **dark when the stretch began** has no lit level of its own there — its reference would be its dark level and every frame would read 1.00. So a pulser whose reference sits below 0.55 of what the February all-alive reference predicts (relative to the rest of the array) keeps the February score, which is what lets it be called dead. The Stretches log lists these as *Dark from start*.

An **energy dip deeper than 0.7 % with no pulser seen dark** in it is listed as *unexplained*. The same dip on several arrays at the same instant (22 Oct 11:23:15 and 15:43:55 on PD1, PD3 and PD4) is the laser as a whole, not a pulser.

### Why the measurement looks the way it does

The pulsers are **not optically independent** — each one lights its neighbours' tiles as well as its own, so a pulser's apparent brightness depends on how many others are lit. Subtracting a *local* floor cancels that: when one dies its neighbourhood dims too, and the shared dimming drops out of the difference.

The floor is taken from a window reaching well beyond the box rather than from a thin ring hugging it, so it finds the dark separator wherever it actually is. This matters because the ROI boxes are hand-drawn and some of them straddle a separator or are narrower than their tile. With a thin ring those boxes measured a healthy pulser as dead; with the window they do not. Measured over 320 pulser-days against the February reference: dead pulsers score 0.25-0.43, healthy ones 0.87-1.10, with nothing in between. Against the start of their own stretch, healthy pulsers sit far tighter still — which is why blinks are judged that way.

Every threshold is derived **per pulser** from the reference, never set as one global number, for the same reason.

### The three states of a frame

| | whole-frame mean | array level | pattern correlation |
|---|---|---|---|
| running | 0.42 | 0.95 | 0.90 |
| warming up | 0.135 | 0.28 | 0.89 |
| diodes off | **0.36** | 0.22 | **-0.1** |

A frame with the diodes off is **not dark** — the camera's gain lifts sensor noise to a mid-grey brighter than three quarters of a running frame — so no brightness test can find it, and its array level is almost the same as warm-up's. What it lacks is *structure*. Each frame's per-ROI contrasts are correlated against the reference's pattern; that is scale-free, so a dim-but-real frame and a bright-but-empty one separate cleanly.

Frames that fail the correlation test are **removed from the series**, not kept as dark samples. The archiver writes them on a separate, much faster schedule (a 0.30 s noise stream alongside the real 5 s acquisition), so keeping them made the array look like it was tripping and recovering between consecutive frames. The gap they leave behind is what marks the outage.

For the same reason, frames are never filtered by **file size**: a PNG of a dim frame compresses better, so size tracks brightness. The old per-camera size gate silently discarded 37.9 % of every day — the whole warm-up stretch and every blank frame written during a trip.

### What a pulser did — every dark run is exactly one of four

Judged **only while the array is running at full power**: never during warm-up, never while the diodes are off, never inside an outage, never outside the analysed time. Severity increases down the table. **Dropout, fault and trip are all blinks** — they say only how long the pulser stayed out and what the array did next; any of them can be *partial*.

| Term | Rule |
|------|------|
| **dropout** | Dark for no more than `Dropout up to` frames, then straight back. A flicker. Counted, never discarded — debouncing is off by default so single-frame flickers survive |
| **fault** | Dark for longer and **the array kept running**. Either it came back ("not working as it should, but it does come back"), or it was still dark when the window or the analysed time ended — a pulser nobody tried to restart stays a fault however long it is dark |
| **trip** | It was the **culprit** of an array stop (see below) and lit again afterwards — this pulser took the array with it |
| **dead** | It was the culprit of a stop and never lit again after the restarts, or only after a long pause (replaced). Or, for a pulser that did not take the array down: it stayed dark through `Dead after (recoveries)` restarts, or it **never lit at all** — dark from the first judged frame and still dark after `Never lit, dead after` minutes of array-up time. A pulser that ran, went dark and then simply stayed dark while nobody ever restarted the array is a **fault**, however long it lasts: it was never asked to come back, so nothing was demonstrated about it |

The line between a fault and a trip is **whether this pulser took the array down** — whether it was dark right up to the stop — not whether an outage happens to overlap the dark run somewhere. That older reading counted a pulser which merely sat dark through someone else's outage as if it had caused one, and reported 47 of them on a day with about eight.

**A verdict is reached by looking forward, and is written back onto an event that began earlier.** Neither decision can be made when a dark run starts: whether the array fell is only known a few frames later, and whether the pulser is recoverable only after the operator has finished trying — and several recoveries are routinely fired back to back, so a pulser still dark after the first one is not yet dead.

**Death can end.** A dead pulser gets replaced, which takes an hour or two, and then it lights again. Such a run keeps its `dead` kind and gains a recovery time; the pulser stays among the day's dead, reported as `C5 † 09:11 → 15:40 (replaced)`. The count answers "how many died today", not "how many are broken right now".

There is no *dead-from-start*. A dead pulser is a dead pulser; when it started being dead is a timestamp.

**A death carries across a break.** A dead pulser is still the same death after the lunch-time switch-off: a dark run that opens while its last death is unanswered (it has not lit since) extends that event instead of starting a new one. Judged on their own, the short afternoon stretches made the same dead pulser a fault in each of them.

### What the array did — every stop is one of six

A **stop** runs from the end of one stretch at full power to the start of the next: with the dump bus that is the voltage leaving full power and coming back to it, **warm-up included** — "back" means back at full intensity. The operator's rules (26-27 Oct 2026) decide it first by whether a pulser took the array down, then by how long it lasted.

The **culprit** is a pulser that was lit earlier in the stretch, then went dark and stayed dark right up to the stop for at least `Culprit dark` — 3 s on the 3.3 Hz stream, 30 s on the old 0.2 Hz archive (the whole array dims for about a second as it goes off, which must not make a culprit). A pulser dark since the stretch began is never the culprit: one dead since the morning did not take down the afternoon. The frames right before the voltage falls are read and scored for this even though they are not judged for blinks.

What became of the culprit is read **forward**: the first time it is seen properly lit again (longer than a flicker). After a pause shorter than `Replaced if back after` (30 min) it **revived** — it sometimes takes the second or third restart. Lit again only after a longer pause (a new pulser takes 40-90 min, or it happened overnight) it was **replaced**, and it died that day. Never lit again although the array was restarted: **dead**. Nothing switched on again in the window: **not restarted**, and nothing was shown about it.

| Kind | Rule |
|------|------|
| **dead pulser** | A culprit that died (dead or replaced). Takes precedence over the length |
| **pulser trip** | A culprit that revived (or was not restarted), and full power back within `Outage up to` (8 min) — however short the stop |
| **bigger problem** | A culprit that revived, but the stop lasted longer than `Outage up to` |
| **manual stop** | No culprit, back within `Manual stop up to` (3 min) — somebody's own hands |
| **outage, no pulser** | No culprit, back within `Outage up to` |
| **switched off** | No culprit and longer, or never back in the window (the end of the day) |
| **data gap** | Inside a stretch, no data for less than `Outage from`. A hiccup in the archive: it breaks a run but is not a stop and is not listed |
| **restart** | An **outage** after which full power came back, i.e. the diodes had to be switched on again. Switching them on after a stop of ours is not a recovery and is not counted |
| **warm-up** | The whole array at its second, much lower level (0.27-0.35 of normal). Part of a stop; its own minutes |

Only **pulser trip**, **dead pulser**, **bigger problem** and **outage, no pulser** count as the array going down. A manual stop and switched-off time are ours, not downtime — never an outage, never a restart, never a reason to call a pulser dead. They are still listed, in grey, because the operator needs to see them.

A pulser that went dark **without** taking the array down keeps the older rule: it is dead once it has stayed dark through `Dead after (recoveries)` restarts, or never lit at all (`Never lit, dead after`).

Time the active-time gate left out is nobody's stop: a stop that runs into gated-out time (the night, the key turned off) is reported only for the part that was watched, and not at all if that part was shorter than `Outage from` — unless a pulser took the array down, which is always reported, judged by the next day if the window holds one.

Two limits are in **seconds and minutes rather than frames**, on purpose. Every frame-counted threshold changes meaning by 16x between the 5 s archive cadence and the 3.3 Hz stream: at 0.30 s per frame a 1.2 s stumble in the archive read as an array trip (17 of them in one day, and 17 restarts), and the one-second dimming ramp as the array was switched off for the evening read as eleven pulsers dying at 17:07.

Nothing about a pulser is judged **inside** a warm-up stretch. Warm-up must be found before anything is classified, because while warming the array runs below the OFF threshold and all forty pulsers would read as dropped out at once. Correcting each pulser's score by its own warm ratio instead looks reasonable and is quietly disastrous: the ratio is about 0.28, so the correction multiplies the score by ~3.6 and a genuinely dead pulser comes out looking alive — which is how a day's two real deaths came to be dated to a mid-morning trip instead of to the first frame of the day. Warm-up contributes minutes, and nothing else.

### Uptime

`Uptime %` is a pulser's lit time over the **array's own full-power time**, so all forty share one denominator. The array is switched on as a whole, so they must: the old per-pulser frame ratio handed 100 % both to a pulser that ran 7.5 hours and to one that ran 5.75, because each got its own denominator. Warm-up, outages, deliberate stops and un-analysed time are all outside it.

`Dark time` is the absolute time a pulser was dark while the array was running. It replaces `Avail %`, which measured "(ON+OFF) / all frames" and meant nothing to anyone.

### The ROI editor

Opened with **Edit <camera>** in the left panel. Opens on the bundled all-alive reference. Draw, move and resize boxes; `Load latest from share` fetches a current frame to compare against, and `Load image` opens any picture file to check the boxes against. Viewing an image never changes the stored reference levels.

| Button | What it does |
|--------|--------------|
| `Name:` + `Rename` | Type a new name for the selected box and press Rename |
| `Delete` | Removes the selected box (greyed until a box is selected) |
| `Save configuration` / `Load configuration` | Write this camera's boxes and reference levels to a JSON file, or read them back |

`Load configuration` in the left panel, under the per-camera edit buttons, loads a saved file for all cameras at once and replaces the current boxes.

Reference levels are only ever written by **`Capture reference`**, which asks whether the loaded image is the all-alive or the warm-up reference. This is deliberate: the editor used to re-measure them from whatever image was on screen when OK was pressed, which quietly replaced the all-alive reference with a live frame, dead pulsers included. Boxes that were moved during the session are re-measured on OK, but only against a genuine reference.

`Auto-create grid` discards every box and detects a new grid; it asks first, because the stored boxes are hand-tuned per camera and cannot be reproduced automatically.

### Detection settings

| Setting | Default | Meaning |
|---------|---------|---------|
| Only 07:00-21:00 (Prague) | on | Judge only these hours. See *Analysed time* — on the current share this only makes the scan faster |
| Only while high power is enabled | on | Judge only while `L3-SIS-KEY:HighPowerEnable` = 1. Needs the archiver |
| Only at full power (dump bus) | on | Judge only while the dump bus says full power, less the ramps and the low-power mode. See *Full power* |
| Read only around energy dips (faster) | on | On PD1/PD3/PD4, read only the frames around the energy dips plus one every 5 s. Off = every frame |
| Dark below | 0.80 | Score below which a pulser is dark in a frame. One dark run = one blink |
| Full blink below | 0.55 | A blink whose lowest score went below this is full; otherwise partial. Changes only the split, not the count |
| Use reference baselines | on | Blend the all-alive level into each pulser's learnt level. Needed to flag a pulser that was dark for the entire window |
| Gap factor | 4.0 | A jump in time longer than this many times the median cadence has no data |
| No-data level | 0.12 | Frame dimmer than this fraction of a normal frame carries no data. Must stay well below the warm-up level (~0.31) |
| Dropout up to (frames) | 5 | Dark runs no longer than this are flickers |
| Culprit dark at 3.3 Hz (s) | 3 | How long a pulser must be dark right up to a stop to be its culprit, on the 3.3 Hz stream |
| Culprit dark at 0.2 Hz (s) | 30 | The same on the old one-frame-per-5-s archive |
| Dead after (recoveries) | 3 | For a pulser that went dark without taking the array down: failed recoveries before it is called dead |
| Never lit, dead after (min) | 20 | Applies **only** to a pulser dark from the first judged frame that has never lit since. Minutes of array-up time we must watch before calling that dead rather than "not seen yet". It never applies to a pulser that ran earlier in the window |
| Replaced if back after (min) | 30 | A culprit that lights again only after a pause at least this long was replaced (and died); after a shorter one it revived |
| Outage from (s) | 60 | Inside a stretch, a pause in the data shorter than this is a hiccup in the archive, not a stop. Without the dump bus it is also what splits the day into stretches |
| Manual stop up to (min) | 3 | No culprit and back within this: somebody's own hands |
| Outage up to (min) | 8 | An ordinary outage is over within this. Longer: a bigger problem (with a culprit) or switched off (without) |
| Warm-up level | auto | Derived from the warm-up reference; set it by hand to override |
| Debounce | 1 | Off by default. Above 1, short flickers are erased before they can be counted |

### Exports

`Export all (folder)` writes, per camera: the full per-frame CSV (brightness, score and state per pulser, plus the frame's own state — `excluded` for a frame the gate left out, `below-full-power` for one not judged), the event log (every blink with its time to the millisecond, whether it was partial, and its lowest score), the stretches table (`pulser_stretches_<camera>.csv`, with the unexplained energy dips under it), the trip log with its suspected cause, the warm-up windows, and PNGs of the map, run graph and statistics — plus one master CSV across all cameras and `pulser_analysed_time.csv`.

That last file is what keeps the rest of the bundle readable months later: every other export is *per observed time*, and a trip log with two entries looks identical whether the window was watched throughout or two thirds of it was excluded. It records the window, the observed and excluded totals, which rules were applied, and every excluded span.

Single exports, each for the camera picked in **Viewing camera:**. The buttons stay greyed, or answer "No data to export.", until a scan has run.

| Button | Where | What it writes |
|--------|-------|----------------|
| `Export full CSV` | left panel, Export | the per-frame CSV for this camera |
| `Export map (PNG)` | Pulser Map, under the map | a picture of the map as shown |
| `Export events (CSV)` | Pulser Map, right side | the event list of the pulser you clicked ("Click a pulser first." otherwise) |
| `Export stats (CSV)` | Statistics | asks for a folder; writes the event log, the stretches table, the trip log and a PNG of the statistics |
| `Export this camera CSV` | All Data | the table for this camera |
| `Export master CSV (all cameras)` | All Data | the table for every scanned camera in one file |
| `Export graph (PNG)` | Run Graph | a picture of the run graph |

---

## Notes

- ROI boxes and their reference levels are stored in `%APPDATA%\PulserMonitor\rois.json`. The file carries a schema version; an older one is backed up next to it and its reference levels are re-measured against the bundled reference images. **Box positions are never touched.**
- Cameras are scanned one after another; the Stop button uses a generation counter, so a cancelled scan is dropped rather than killed mid-read.
- Each year lives in its own share (`cpva-image-<year>`), and the year is swapped inside the share *name* — on a UNC path the share is part of the anchor.
