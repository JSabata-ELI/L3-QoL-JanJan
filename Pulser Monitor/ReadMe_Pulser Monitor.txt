Pulser Monitor — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Pulser Monitor_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Reads the pictures the diode-array cameras took over a period you
  choose, and counts every BLINK — every time a pulser went out while
  the array ran at full power — saying which pulser, when (to the
  millisecond) and whether it went out fully or only dimmed. It also
  lists the pulsers that were dead, whole-array trips and which
  pulser most likely caused each one, and how long the array spent
  warming up.

  Nobody watches forty pulsers on four cameras all day. This does the
  watching afterwards, from the pictures that were saved anyway.


THE IDEA IN FOUR STEPS

  1. Ask the archive when the array was at full power (the dump-bus
     voltage) and when its energy dipped. Every blink takes a few
     percent off the array's energy for a shot or two.
  2. Find the frames on the share. On PD1, PD3 and PD4 only the
     frames around the energy dips (and one every 5 s) are read; PD2's
     energy is not archived, so PD2 is read frame by frame.
  3. Measure each frame: how bright each pulser's own tile is
     compared with the dark gaps immediately around it.
  4. Compare each pulser with how it looked at the START of that
     stretch at full power — the way you would compare a frame with
     the picture at the start of the run. The one that moved against
     the rest is the one that blinked.

  Every threshold is per pulser. A corner pulser that is naturally
  dimmer is judged against its own level, never against a single
  number for all of them.


THE FOUR TABS

  Pulser Map     the array as a grid, coloured from green (never even
                 flickered) to light red (the worst in this array),
                 with dead ones in dark red, a hatch on any that was
                 replaced and a cross on any that took the whole
                 array down. The number in a cell is how many times
                 it blinked. Hover for its numbers; click for its
                 events and its score on a timeline.

  Statistics     blinks per pulser, blinks over time, and three logs:
                 the STRETCHES at full power (what blinked in each),
                 every PULSER event, and what the whole ARRAY did.

  All Data       one sortable row per pulser, one camera or all four,
                 with an export.

  Run Graph      every pulser's state over real time, each blink
                 marked on its row (hollow = partial), with warm-up,
                 below-full-power, diodes-off and un-analysed
                 stretches shaded.

  "Viewing camera:" above the tabs picks which camera's results
  they show. On the Pulser Map, "Heatmap:" picks what the colours
  mean (Status, Blinks, Partial blinks, Dropouts, ...). A running
  scan is stopped with "Stop" in the left panel or "Stop Scan" at
  the bottom right of the window.


A BLINK

  Any time a lit pulser goes dark at full power and the array keeps
  running is a blink — one frame or several. It is FULL when the
  pulser went below "Full blink below" (0.55 of its level), PARTIAL
  when it only dimmed below "Dark below" (0.80). Not counted:
  anything in the 30 s after full power begins and the 15 s before it
  ends (the ramps), the low-power mode, and a frame in which the whole
  array changed level at once.


THE FOUR THINGS A DARK PULSER CAN BE

  dropout   dark for a few frames and straight back. A flicker.
  fault     dark for longer and the array kept running. It either
            came back or was still dark when the window ended.
  trip      it took the array down (the culprit of a stop) and
            lit again afterwards.
  dead      it took the array down and never lit again (or only
            as a new pulser), or it stayed dark through several
            restarts ("Dead after (recoveries)"), or it never lit
            at all ("Never lit, dead after (min)").

  A pulser that ran, went dark and simply stayed dark while nobody
  restarted the array is a fault, not dead — nobody tried to bring
  it back, so nothing was proven about it.

  Dropout, fault and trip are all blinks; they only say how long the
  pulser stayed out and what the array did next. A dead pulser stays
  one death across the lunch-time switch-off, however short the
  afternoon stretch is.

  A dead pulser that gets replaced still counts as having died that
  day, and is reported with both times. The count answers "how many
  died today", not "how many are broken now".


WHAT AN ARRAY STOP WAS

  A stop lasts from full power ending to full power being back
  (warm-up included). The CULPRIT is a pulser that was lit, then dark
  right up to the stop for at least 3 s (30 s on the old 5-s archive).

  culprit never lit again after the restarts,
  or only after a pause of 30 min or more (new pulser)  dead pulser
  culprit lit again, stop up to 8 min                   pulser trip
  culprit lit again, stop longer                        bigger problem
  no culprit, back within 3 min                         manual stop
  no culprit, back within 8 min                         outage, no pulser
  no culprit, longer or never back                      switched off

  Manual stops and switched-off time are ours: listed in grey, never
  counted as outages.


THE TWO MOST IMPORTANT SETTINGS

  "Only at full power (dump bus)", on by default.

  Only the time the dump-bus voltage says the array is at full power
  is judged; warm-up is measured, never judged. If the archive cannot
  be reached, every frame is read and full power is judged from the
  pictures instead — the log and the Statistics tab say so.
  "Read only around energy dips (faster)" (on) reads PD1, PD3 and PD4
  only where their energy dipped; switch it off to read every frame.

  "Only while high power is enabled", on by default.

  The cameras start recording in the morning long before the key is
  turned. Those hours are pictures of an array that was not supposed
  to be firing, and judged as if it were they produce dropouts and
  dead pulsers that never happened. With the setting on, that time is
  removed from the analysis before anything is classified.

  Excluded time is accounted for, not deleted: it is subtracted from
  every percentage, it never produces a fault, and nothing is carried
  across it. It is drawn on the graphs as hatched shading, so you can
  always see what was not looked at.

  It needs the archive to be reachable. If it is not, the scan still
  runs with the working-hours rule only, and says so.


TWO WORDS THAT MEAN DIFFERENT THINGS

  Time window     which frames are read off the share. What you ask
                  for.
  Analysed time   which of those frames are actually judged. What the
                  laser was really doing.

-----------------------------------------------------------------
