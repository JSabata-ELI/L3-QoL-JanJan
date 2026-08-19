Announcer — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

The program does two things at once.

1. It watches a chosen rectangle of the screen and raises an alarm
   (a flashing image or colour, plus a sound) as soon as what is
   drawn there changes. Useful for camera windows, status displays
   or any on-screen indicator you cannot keep looking at.

2. It reads eight machine values every half second and shows a
   coloured badge for each one that is out of range: the helium
   volume, the Alpha voltage, and how far each of the six chillers
   is from its setpoint. No setup needed — the badges appear on
   their own and disappear when the value comes back.


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
  - The image window flashes: either its whole background in the
    configured colour, or the selected image alone (Flash mode).
  - An optional sound plays.
  - Tracking stops automatically.
  - Click the flashing area to dismiss the alert and go back to
    the control window. In "Image only" mode the clickable area is
    the image itself; it stays clickable while it blinks off.
    Esc dismisses the alert as well.


=================================================================
PV ALERTS
=================================================================

Eight values are read every half second, each one averaged over its
last 25 samples. Nothing is shown while a value is in range; a badge
appears the moment it is not.

  Helium volume     the helium pressure, in PSI
  Alpha voltage     the seeder voltage, in V
  Chiller DA1..DA4  how far the chiller is from its setpoint, in °C
  Helium Chiller    the same
  Utility chiller   the same

The badge colour says how bad it is:

  Orange   past the first limit  (Lo / Hi)
  Red      past the second one   (Lolo / HiHi)
  Purple   the chiller's actual temperature is outside the range it
           is allowed to run in at all — 7 to 17.5 °C for DA1..DA4
           and the Helium chiller, 18 to 22 °C for the Utility
           chiller

Purple wins over orange and red, and it shows the real temperature
instead of the deviation. A chiller can sit exactly on its setpoint
and still be at the wrong temperature; that is what purple is for.

The arrow at the end of the badge says which way the value went. The
badges lay themselves out side by side and wrap onto another line, so
none of them is ever cut off.

Limits are edited in Settings under "PV Limits" (Lolo / Lo / Hi /
HiHi per value) and are saved automatically. The purple range is
fixed in the program.

If a value cannot be read, the reason is written into the Message
log in plain words — hover a line for the hint.


=================================================================
REGION PRESETS
=================================================================

A preset stores the watched rectangle so you do not have to draw it
again.

  Load          use the selected preset
  Save region   store the current rectangle under a name
  Delete        remove it

Window positions are remembered too, either for everything or per
preset: see "Set control window" and "Set image window" in Settings.
Both are recorded by dragging the window where you want it and
confirming, so you never type coordinates.


=================================================================
SETTINGS (gear button)
=================================================================

DETECTION:
  - Threshold (0.5-50.0, default 2.0):
    Average pixel deviation (0-255) required to trigger an alert.
    Lower = more sensitive.
  - Flash colour: click to pick the alert overlay colour.
  - Flash duration (0-60 s, default 3.0 s).
  - Flash mode:
      Background color -- the whole image window blinks.
      Image only       -- only the selected image blinks, filled
                          with the flash colour; everything around
                          it stays see-through, and the off half of
                          the blink shows nothing at all.
  - Image: which file from the images/ folder is used.

SOUND:
  - Play sound on change: enable/disable audio alert.
  - Freq (Hz) / Duration (ms): built-in beep parameters.
  - Sound file: select a .wav file from the sounds/ folder as
    an alternative to the built-in beep.

WINDOW POSITION & SIZE:
  - Set control window: drag the control panel where you want it,
    then confirm. The position is remembered.
  - Set image window: the same for the window the alarm flashes in.
    This is how the alarm image is lined up with whatever is behind
    it; there is nothing to type.

PV LIMITS:
  - One row per monitored value, four numbers each:
      Lolo / Lo   the low limits  (red / orange)
      Hi / HiHi   the high limits (orange / red)
  - Saved as soon as they are changed.


=================================================================
GENERAL NOTES
=================================================================

  - Poll interval: 500 ms (checks for changes twice per second).
    The PVs are read on the same rhythm.
  - Flash blink interval: 300 ms.
  - Sound files: place .wav files in the sounds/ subfolder next
    to the executable.
  - Alarm images: the images/ subfolder next to the executable.
    Both folders must be there — without images/ the alarm can only
    flash a plain colour. A deploy that loses them has to be redone;
    they are listed in build_config.json so every build carries them.
  - Everything is stored in presets.json next to the program: the
    saved regions, the window positions, the PV limits and the flash
    settings.
  - Multi-monitor support via the screeninfo library. "Identify"
    puts a number on each screen for three seconds.
  - While watching, the control panel turns into a small see-through
    overlay — only the circle and the badges stay visible, and the
    rest of it lets clicks through to whatever is underneath.
  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
