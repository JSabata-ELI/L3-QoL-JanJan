Announcer v1.2.0 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
-----------------------------------------------------------------

Real-time screen region change monitor. Watches a selected area
of the screen and alerts (visual flash + optional sound) when
the pixel content changes beyond a configurable threshold.

Useful for monitoring camera windows, status displays, or any
on-screen indicator without keeping it in focus.


=================================================================
SETUP
=================================================================

1. SELECT MONITOR
   - Choose the target monitor from the dropdown.
   - Click Identify to overlay numbered labels on each screen
     for 3 seconds.

2. SET REFERENCE REGION
   - Click "Set reference" to enter selection mode.
   - A semi-transparent overlay covers the screen; drag to draw
     the region you want to watch, then release.
   - Press ESC to cancel.
   - The coordinates of the selected region are shown in the
     status label.

3. RE-SNAPSHOT
   - Click the refresh button (arrow icon) to re-capture the
     current screen content as the new reference without
     re-drawing the region.

4. PREVIEW REGION
   - Hover "Preview region" to see a thumbnail popup (max 640x400)
     of the currently selected area.
   - Click to pin the popup open.


=================================================================
TRACKING AND ALERTS
=================================================================

STATUS INDICATOR (circle):
  Gray    -- no reference set yet
  Orange  -- reference set, not tracking
  Green   -- actively tracking (checks every 500 ms)
  Red     -- change detected, alert active

WHEN A CHANGE IS DETECTED:
  - The window flashes with the configured colour.
  - An optional sound plays.
  - Tracking stops automatically.
  - Click anywhere on the flashing window to dismiss the alert.


=================================================================
SETTINGS (gear button)
=================================================================

DETECTION:
  - Threshold (0.5-50.0, default 2.0):
    Average pixel deviation (0-255) required to trigger an alert.
    Lower = more sensitive.
  - Flash colour: click to pick the alert overlay colour.
  - Flash duration (0-60 s, default 3.0 s).

SOUND:
  - Play sound on change: enable/disable audio alert.
  - Freq (Hz) / Duration (ms): built-in beep parameters.
  - Sound file: select a .wav file from the sounds/ folder as
    an alternative to the built-in beep.


=================================================================
GENERAL NOTES
=================================================================

  - Poll interval: 500 ms (checks for changes twice per second).
  - Flash blink interval: 300 ms.
  - Sound files: place .wav files in the sounds/ subfolder next
    to the executable.
  - Multi-monitor support via the screeninfo library.

-----------------------------------------------------------------
