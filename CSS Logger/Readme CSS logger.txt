CSS Logger — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Explorer and logger for the CPVA (Control System Studio) archive.
Pick PVs, pick a time window, load — then read the values in a
table, plot them, filter them, derive new ones and export.

The program window has two tabs: CSS Logger and Spectra.


=================================================================
SIDEBAR — LOADING DATA
=================================================================

TIME WINDOW
  📅 Set time window — absolute date/time on both ends, or
  relative quick picks (last hour, last 24 h, "now"…).

PRESETS
  Named PV lists. Load / Save / Save as new… / ✕ delete.

PV LIST
  Browse… opens the channel browser. It downloads the archiver
  channel list once and then filters it as you type:
  the words you type must appear in order, so "023 l3" finds
  the same thing as "*023*l3*". "*" and "?" also work.
  ✕ Remove drops the selected PVs, 🗑 clears the list.

LOAD DATA
  Fetches every PV in the list for the time window. The range is
  split into hourly chunks and fetched in parallel; night hours
  with no data are skipped. For each PV the last value BEFORE the
  window is also fetched so a flat signal is not missing.

⏵ Live
  Polls for new values every 300 ms and extends the window. The
  countdown next to the button shows the next poll. On long
  sessions the full rebuild of the table and graph is throttled
  to once a second — new samples still arrive every tick.
  While Live is on, From/To read "now − <span> … now", because the
  window rolls with the clock.
  The live window is capped at 12 hours: it is kept merged and
  redrawn continuously, and the saved time range can otherwise
  grow to several days and make every refresh crawl. LOAD DATA
  still loads any window you ask for; the Log says when the live
  window was shortened.
  Stop Live also cancels the requests that are still running, so
  the program goes quiet immediately instead of finishing a queue
  of archiver calls in the background.

MASTER PV / FILTER
  Master PV — the ramp master used to deduplicate rows.
  Keep multiples of — keep only rows where the master PV is a
  multiple of this value (e.g. 5).


=================================================================
GRAPH TAB
=================================================================

One plot, one band per PV (CS-Studio style): each PV keeps its
own vertical band and its own Y axis column on the left. A PV
with Autoscale on uses the full height.

  Clean graph / Save graph / Back (undo the last zoom)
  Reference lines   — horizontal lines at fixed values
  Conditions        — min/max filters. A row is kept only if every
                      condition PV is present and inside its
                      [min, max]; everything else is discarded from
                      the table, the graph and the CSV export. If a
                      whole window is out of range the table stays
                      empty on purpose and the Log says how many rows
                      were dropped. A condition on a PV that has no
                      data in the loaded window is skipped (also
                      logged), so it cannot reject every row on its
                      own. Custom PVs can be used as condition PVs.
  Add custom PV     — a new signal from an expression over the
                      loaded ones (A, B, C … = channel letters).

                      Each formula remembers which PV every letter
                      stands for, so loading another preset or
                      removing a PV re-assigns the letters to follow
                      the PVs instead of changing what the formula
                      means. The letter you see can therefore change
                      between sessions while the formula keeps its
                      meaning: "compressed beta = SBW4 * 0.749" shows
                      as A*0.749 when SBW4 is first in the list and
                      as H*0.749 when it is eighth — same PV, same
                      result. Only the stored PV names are permanent.

                      Two tables spell that out:
                        Available channels — the automatic letter
                          assignment for the list as loaded now
                          (read-only; it follows the list).
                        What each letter means — one row per letter
                          per formula with the PV behind it. Pick a
                          different channel there to re-point that
                          letter; the expression updates to the new
                          letter so it stays honest about what it
                          reads.

                      A PV a formula needs but that is not loaded is
                      listed in both tables, marked "not loaded" (its
                      column then stays empty and the Log says so).
                      Its binding is kept, not silently re-pointed at
                      whatever now holds that letter.
  Graph settings    — fonts, axis-column spacing, plot margins,
                      cursor readouts, performance switches
  Font / Avg to     — font size, and the point count the traces
                      are averaged down to

MOUSE
  - Move the mouse for a crosshair with the time and the value of
    every visible PV (value boxes can be turned off, and are
    dropped automatically above ~20 visible PVs).
  - Drag with the span tool to get statistics for that interval
    — one card per PV with N, Avg, Std, Min, Max and peak-to-peak
    (the numbers can be selected and copied).
  - Rubber-band drag zooms; Back walks the zoom history.

  F11        — show the graph in its own resizable window
  Ctrl+F11   — show the graph fullscreen
  (the same shortcut docks it back)

AXIS SETTINGS PANEL
  One row per PV: Show, display name, colour, cursor value,
  Y min / Y max, Autoscale, line width, Smooth and Grid.
  Edits are collected and repainted once you stop clicking.


=================================================================
XY PLOT TAB
=================================================================

  Scatter of one loaded PV against another. Drag a rectangle to
  zoom, Back to step out again.


=================================================================
PV TIME PLOT TAB
=================================================================

  Daily distribution / raw trace built from the local
  RampingRepository (works without the archiver). Conditions can
  be added per row.


=================================================================
TABLE TAB
=================================================================

  All merged rows. Only the newest 5000 rows are rendered to keep
  the UI fast — export, graph and XY plot always use everything.

  - Right-click a row: Copy row, Open image.
  - Double-click a cell holding an image path to open the frame.


=================================================================
EXPORT
=================================================================

  CSV export writes every loaded row — not just the 5000 the
  table renders. One Timestamp column plus one column per PV.
  Decimal separator: period. Column separator: semicolon,
  announced by a "sep=;" first line so Excel opens it directly.


=================================================================
GENERAL NOTES
=================================================================

  - Archiver: https://10.78.0.57:8443/api/1.0/cpva
    (self-signed certificate, verification disabled)
  - Images: \\users-L3.tier0.lcs.local\cpva-image-2026
  - Settings live next to the program:
      cpva_explorer_config.json    window state, time range,
                                   conditions, graph settings
      cpva_presets.json            PV-list presets
      cpva_conditions_presets.json condition presets
      custom_pvs.json              derived PVs
  - RampingRepository/ holds local parquet data for the PV Time
    tab and works offline; ramping_archive.json is its index and
    ramping_setups.json keeps the named setups.
  - The archiver only answers reliably for windows of about an hour,
    so every request is split into one-hour pieces and fetched in
    parallel. Night hours (22:00-06:00) are skipped, since nothing
    is recorded then.
  - For a long window the program can also ask the archiver for a
    thinned-out version of the data (the "count" option) instead of
    every single sample, which is what keeps a month-long load
    usable.

  For whoever works on the code:
      STRUCTURE.md        what each part does and why
      test_smoke.py       clicks through every button, headless,
                          no network
      test_live_pacing.py live mode pacing
      test_count_param.py the archiver's count parameter
                          (needs the real archiver)

-----------------------------------------------------------------
