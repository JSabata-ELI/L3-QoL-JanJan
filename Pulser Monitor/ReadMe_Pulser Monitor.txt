Pulser Monitor — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Pulser Monitor_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Reads the pictures the diode-array cameras took over a period you
  choose, and says what each pulser did in that time: brief flickers,
  longer outages, pulsers that died, whole-array trips and which
  pulser most likely caused each one, how many times the diodes had
  to be switched back on, and how long the array spent warming up.

  Nobody watches forty pulsers on four cameras all day. This does the
  watching afterwards, from the pictures that were saved anyway.


THE IDEA IN THREE STEPS

  1. Find the frames on the share for the period you asked for.
  2. Measure each frame: how bright each pulser's own tile is
     compared with the dark gaps immediately around it.
  3. Compare that against a reference picture with every pulser lit,
     per pulser, and turn the resulting on/off pattern over time into
     named events.

  Every threshold is worked out for each pulser individually from
  that reference. A corner pulser that is naturally dimmer is judged
  against its own normal, never against a single number for all of
  them.


THE FOUR TABS

  Pulser Map     the array as a grid, coloured from green (never even
                 flickered) to light red (the worst in this array),
                 with dead ones in dark red, a hatch on any that was
                 replaced and a cross on any that took the whole
                 array down. Hover for its numbers; click for its
                 events on a timeline.

  Statistics     counts per pulser, events over time, and two logs:
                 what the whole ARRAY did, and what each PULSER did.

  All Data       one sortable row per pulser, one camera or all four,
                 with an export.

  Run Graph      every pulser's state over real time, with warm-up,
                 diodes-off and un-analysed stretches shaded.


THE FOUR THINGS A DARK PULSER CAN BE

  dropout   dark for a few frames and straight back. A flicker.
  fault     dark for longer, the array kept running, and it came
            back. Not working properly, but it does recover.
  trip      dark for longer and the array went down right after —
            this pulser took the array with it.
  dead      it could not be brought back, judged over several
            recovery attempts or a period of array running time.

  A dead pulser that gets replaced still counts as having died that
  day, and is reported with both times. The count answers "how many
  died today", not "how many are broken now".


THE MOST IMPORTANT SETTING

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
