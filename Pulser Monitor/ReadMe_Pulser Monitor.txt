# Pulser Monitor

PySide6 GUI that scans the diode-array cameras over a chosen time window and reports what each pulser did: brief flickers, sustained dropouts, diodes that died, whole-array trips (and which pulser most likely caused them), how many times the diodes had to be switched back on, and how long the array spent warming up.

---

## Quick Overview

| Tab | What it does |
|-----|--------------|
| **Pulser Map** | Grid of the array. In **Status** it runs green (never even flickered) through to light red (the worst pulser in this array), with dead pulsers in full dark red, a hatch on any that was replaced, and an x on any that took the whole array down. Hover for a per-pulser readout; click to list that pulser's events on a timeline, over its own decision score |
| **Statistics** | Per-pulser event counts, events over time, the spread of time between them, and two logs: what the **array** did (outage / trigger off / diodes off) and what each **pulser** did (every fault, trip and death with its times and lengths) |
| **All Data** | One sortable row per pulser, for this camera or for all four at once; CSV export |
| **Run Graph** | State of every pulser over real time — ON / OFF / no data, with warm-up, diodes-off and un-analysed stretches shaded across the full height. Outage starts are marked, the array's first and last full-power frame are labelled, each pulser's events are flagged on its own row, and hovering reads out the Prague time under the cursor (click to pin it) |

Left panel: pick cameras, set the time window, edit the ROI boxes per camera, choose which time counts, tune detection, scan, export.

Note the difference between the **time window** (which frames are read off the share) and **Analysed time** (which of them are judged). The window is what you ask for; the gate is what the laser was actually doing.

---

## Files

| File | What it is |
|------|-----------|
| `pulser_monitor.py` | The whole application — measurement, analysis and UI in one file. Run it directly: `python pulser_monitor.py` |
| `C03-*-allgood.png` | Per-camera reference frame with **every pulser lit**. Every measurement is relative to this; without it a pulser that stayed dark for the whole window cannot be flagged at all |
| `C03-*-warmup.png` | Per-camera reference frame taken while the array was **warming up**. Without it warm-up time cannot be measured |
| `test_pulser.py` | Offline test of the analyser: synthesises every dropout / fault / trip / death and every kind of stretch without data out of the real reference frames |
| `test_gui.py` | Headless test of the four tabs and every CSV / PNG export |
| `test_real.py` | Same pipeline on genuine running and warm-up frames |
| `test_gate.py` | Test of the active-time gate: span algebra, an overnight window, the panel controls. `--live` adds a real archiver query |
| `scan_share.py` | Runs the real pipeline over any window on the share without the GUI, for checking a day quickly. Prints **every** stretch without data with its length, what it was read as and why, plus the full per-pulser fault log — the two tables to look at when a count looks wrong |
| `probe_morning.py` | Prints the per-frame array level across a morning — handy when a period needs explaining |
| `build_config.json` | Build settings for Dev Tools. It lists the eight reference PNGs so they are copied next to the exe — a build without them cannot flag a pulser that stayed dark, and cannot measure warm-up |
| `STRUCTURE.md` | Developer map of the file: where each block, class and function lives |
| `gui_out\`, `23062026\`, `test01072026\` | Test output and captured sample days. Nothing reads them |

Run all four test files before changing the analyser.

ROI boxes live outside this folder, in `%APPDATA%\PulserMonitor\rois.json`.

There is no `icon.ico` in this folder yet. Every other program has one, and both
the Launcher card and the built exe use it.

---

## Detailed Description

### What a scan does

0. **Work out which time is worth judging** (see *Analysed time* below) — once per scan, before any camera is touched.
1. **Find the frames.** `<share>/<year>/<month>/<day>/<hour>/<camera>/` — folder names are UTC and unpadded, the filename carries a 19-digit Unix-ns timestamp. Hour folders are listed on 12 threads, which is almost entirely network wait (~17x faster than one at a time).
2. **Measure each frame** in a worker process: for every ROI box, `contrast = p75(box) - p10(window reaching 35 % beyond the box)`, plus the plain box mean and a whole-frame mean.
3. **Analyse** the series: throw out frames that do not show the array, find warm-up, learn each pulser's own lit level, score every frame against it, turn the ON/OFF runs into classified events.

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

#### How this relates to the *diodes off* stretches

Two different mechanisms answer the same question — "were the diodes even meant to be on?" — and they are deliberately not merged:

| | Where it gets its answer | What it does with it |
|---|---|---|
| **Only while high power is enabled** | the archiver, from the operator's key | removes the time from the analysis entirely |
| **diodes off** (a kind of stretch without data) | the images themselves, from their lack of structure | keeps the stretch, listed in the array log as *diodes off*, never counted as an outage |

**The key wins where it is available**, because it is a direct reading rather than an inference: when the archiver answers, that time is gone before anything is classified, so no *diodes off* stretch is produced for it at all. When the archiver cannot be reached the key rule switches itself off, every frame in 07:00-21:00 is read, and the image-based test carries the day on its own — which is why it must stay. Measured on 10 Aug: ungated, the evening reads as one *diodes off* stretch; gated, it is not in the result at all.

So the two are a primary source and a fallback, not a duplicate. Neither one may be removed on the grounds that the other covers it.

### Why the measurement looks the way it does

The pulsers are **not optically independent** — each one lights its neighbours' tiles as well as its own, so a pulser's apparent brightness depends on how many others are lit. Subtracting a *local* floor cancels that: when one dies its neighbourhood dims too, and the shared dimming drops out of the difference.

The floor is taken from a window reaching well beyond the box rather than from a thin ring hugging it, so it finds the dark separator wherever it actually is. This matters because the ROI boxes are hand-drawn and some of them straddle a separator or are narrower than their tile. With a thin ring those boxes measured a healthy pulser as dead; with the window they do not. Measured over 320 pulser-days: dead pulsers score 0.25-0.43 of their reference, healthy ones 0.87-1.10, with nothing in between — hence the ON/OFF thresholds at 0.70 / 0.55.

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

Judged **only while the array is running at full power**: never during warm-up, never while the diodes are off, never inside an outage, never outside the analysed time. Severity increases down the table.

| Term | Rule |
|------|------|
| **dropout** | Dark for no more than `Dropout up to` frames, then straight back. A flicker. Counted, never discarded — debouncing is off by default so single-frame flickers survive |
| **fault** | Dark for longer, **the array kept running**, and it came back. "Not working as it should, but it does come back" |
| **trip** | Dark for longer and **the array went down within `Trip look-ahead` frames** of it going dark — this pulser took the array with it |
| **dead** | It could not be recovered: after going dark it failed to light again across `Dead after (recoveries)` array recoveries, or through `Dead after (min)` minutes of time the array spent running. Whichever comes first |

The line between a fault and a trip is **whether the array fell**, decided by looking forward a bounded number of frames — not by whether an outage happens to overlap the dark run somewhere. That older reading counted a pulser which merely sat dark through someone else's outage as if it had caused one, and reported 47 of them on a day with about eight.

**A verdict is reached by looking forward, and is written back onto an event that began earlier.** Neither decision can be made when a dark run starts: whether the array fell is only known a few frames later, and whether the pulser is recoverable only after the operator has finished trying — and several recoveries are routinely fired back to back, so a pulser still dark after the first one is not yet dead.

**Death can end.** A dead pulser gets replaced, which takes an hour or two, and then it lights again. Such a run keeps its `dead` kind and gains a recovery time; the pulser stays among the day's dead, reported as `C5 † 09:11 → 15:40 (replaced)`. The count answers "how many died today", not "how many are broken right now".

There is no *dead-from-start*. A dead pulser is a dead pulser; when it started being dead is a timestamp.

### What the array did — four different things, all of which used to be "a trip"

| Term | Rule |
|------|------|
| **data gap** | No data for less than `Outage from`. A hiccup in the archive. It still breaks a run, so nothing is joined across it, but it is not an outage and it blames nobody. Not listed |
| **array trip** | No data for longer, **with a pulser going dark right before it**. Normally cleared within 8 minutes, sometimes 10-15 |
| **trigger off** | Nothing was dark beforehand and the data is back within `Trigger off up to` — we stopped it ourselves |
| **diodes off** | Nothing was dark beforehand *and* it lasts `Diodes off from` or more. Usually the end of the day, but it happens mid-day too |
| **unexplained** | Long enough to matter, nothing dark beforehand, and too long to be a trigger-off. Surfaced and flagged rather than guessed at |
| **caused the trip** | A pulser dark, unbroken, right up to the moment the array went down, for at least `Trip cause` frames — *and* lit at some point since the array last started, so a long-standing fault is not blamed for every outage of the day |
| **restart** | An **outage** after which data resumed, i.e. the diodes had to be switched on again. Switching them on after a deliberate stop is not a recovery from anything and is not counted |
| **warm-up** | The whole array sitting at its second, much lower level (0.27-0.35 of normal, repeatable to better than a percent). Its own category, reported as minutes |

Only **trip** and **unexplained** count as the array going down. Time we stopped it ourselves is not downtime — nobody was trying to run the array — so it is never an outage, never a restart, and never a reason to call a pulser dead. It is still listed, in grey, because the operator needs to see it.

An outage nobody cleared is **split**: the first `Diodes off from` is the outage, and the rest is the diodes being left off. Both halves are honest, and the outage keeps its cause.

Two limits are in **seconds and minutes rather than frames**, on purpose. Every frame-counted threshold changes meaning by 16x between the 5 s archive cadence and the 3.3 Hz stream: at 0.30 s per frame a 1.2 s stumble in the archive read as an array trip (17 of them in one day, and 17 restarts), and the one-second dimming ramp as the array was switched off for the evening read as eleven pulsers dying at 17:07.

Nothing about a pulser is judged **inside** a warm-up stretch. Warm-up must be found before anything is classified, because while warming the array runs below the OFF threshold and all forty pulsers would read as dropped out at once. Correcting each pulser's score by its own warm ratio instead looks reasonable and is quietly disastrous: the ratio is about 0.28, so the correction multiplies the score by ~3.6 and a genuinely dead pulser comes out looking alive — which is how a day's two real deaths came to be dated to a mid-morning trip instead of to the first frame of the day. Warm-up contributes minutes, and nothing else.

### Uptime

`Uptime %` is a pulser's lit time over the **array's own full-power time**, so all forty share one denominator. The array is switched on as a whole, so they must: the old per-pulser frame ratio handed 100 % both to a pulser that ran 7.5 hours and to one that ran 5.75, because each got its own denominator. Warm-up, outages, deliberate stops and un-analysed time are all outside it.

`Dark time` is the absolute time a pulser was dark while the array was running. It replaces `Avail %`, which measured "(ON+OFF) / all frames" and meant nothing to anyone.

### The ROI editor

Opens on the bundled all-alive reference. Draw, move and resize boxes; `Load latest from share` fetches a current frame to compare against.

Reference levels are only ever written by **`Capture reference...`**, which asks whether the loaded image is the all-alive or the warm-up reference. This is deliberate: the editor used to re-measure them from whatever image was on screen when OK was pressed, which quietly replaced the all-alive reference with a live frame, dead pulsers included. Boxes that were moved during the session are re-measured on OK, but only against a genuine reference.

`Auto-create grid` discards every box and detects a new grid; it asks first, because the stored boxes are hand-tuned per camera and cannot be reproduced automatically.

### Detection settings

| Setting | Default | Meaning |
|---------|---------|---------|
| Only 07:00-21:00 (Prague) | on | Judge only these hours. See *Analysed time* — on the current share this only makes the scan faster |
| Only while high power is enabled | on | Judge only while `L3-SIS-KEY:HighPowerEnable` = 1. Needs the archiver; this is the rule that changes results |
| Alive score | 0.70 | Score at or above which a pulser reads ON |
| Off score | 0.55 | Score at or below which it reads OFF; in between, the previous state holds |
| Use reference baselines | on | Blend the all-alive level into each pulser's learnt level. Needed to flag a pulser that was dark for the entire window |
| Gap factor | 4.0 | A jump in time longer than this many times the median cadence has no data |
| No-data level | 0.12 | Frame dimmer than this fraction of a normal frame carries no data. Must stay well below the warm-up level (~0.31) |
| Dropout up to (frames) | 5 | Dark runs no longer than this are flickers |
| Trip look-ahead (frames) | 10 | How soon after a pulser goes dark the array must fall for it to be a trip rather than a fault |
| Trip cause (frames) | 3 | How long a pulser must already be dark, right up to an outage, to be named as its cause — and what separates the array falling over from the array being switched off |
| Dead after (recoveries) | 3 | Failed recoveries before a dark pulser is called dead |
| Dead after (min) | 20 | Or minutes of array-up time dark. Whichever comes first. A replacement takes an hour or two, so this band separates "cannot be recovered" from "was swapped out" |
| Outage from (s) | 60 | Below this, no data is a hiccup in the archive rather than the array being down. **The most important setting on the 3.3 Hz stream** |
| Trigger off up to (s) | 300 | Nothing dark first and back within this: we stopped it ourselves |
| Diodes off from (min) | 30 | Beyond this, an unexplained or un-cleared stretch is the diodes being left off |
| Warm-up level | auto | Derived from the warm-up reference; set it by hand to override |
| Debounce | 1 | Off by default. Above 1, short flickers are erased before they can be counted |

### Exports

`Export all (folder)` writes, per camera: the full per-frame CSV (brightness, score and state per pulser, plus the frame's own state — `excluded` for a frame the gate left out), the event log, the trip log with its suspected cause, the warm-up windows, and PNGs of the map, run graph and statistics — plus one master CSV across all cameras and `pulser_analysed_time.csv`.

That last file is what keeps the rest of the bundle readable months later: every other export is *per observed time*, and a trip log with two entries looks identical whether the window was watched throughout or two thirds of it was excluded. It records the window, the observed and excluded totals, which rules were applied, and every excluded span.

---

## Notes

- ROI boxes and their reference levels are stored in `%APPDATA%\PulserMonitor\rois.json`. The file carries a schema version; an older one is backed up next to it and its reference levels are re-measured against the bundled reference images. **Box positions are never touched.**
- Cameras are scanned one after another; the Stop button uses a generation counter, so a cancelled scan is dropped rather than killed mid-read.
- Each year lives in its own share (`cpva-image-<year>`), and the year is swapped inside the share *name* — on a UNC path the share is part of the anchor.
