Announcer — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Announcer_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT WATCHES

  You give it a list of conditions — things that have to stay true.
  While it is watching, it checks all of them twice a second, and the
  moment one of them stops being true it raises the alarm: a flashing
  image or colour, a sound, and the sentence you wrote for that
  condition.

  A condition is one of two things:

    Screen area   a rectangle of a screen still looks the way it did
                  when you took its reference picture. Good for a
                  status box, a warning lamp, a number, a camera
                  window — anything drawn on a screen.
    Value         a machine value stays under its limit. Two limits:
                  the first only shows a warning badge, the second
                  raises the alarm.

  It also shows badges for eight machine values that are out of
  range — the helium pressure, the Alpha voltage, and how far each of
  the six chillers is from its setpoint. Nothing to set up for those,
  but they are only read while it is watching.


HOW THE SCREEN AREA CHECK WORKS

  It keeps the reference picture and takes a new one twice a second.
  If the average difference between them is bigger than the number
  you set, that counts as a change.

  So it does not understand what it is looking at. It only knows the
  picture is no longer the same one. That is what makes it work on
  anything.


SETTING UP A CONDITION

  Screen area:
  1. Press "Add screen area" and give it a name and the sentence you
     want to see when it fires.
  2. Pick the monitor and press "Draw area". The screen dims; drag
     out the rectangle and let go. Esc cancels. The reference picture
     is taken straight away and shown underneath.
  3. Save. Tick it in the On column.

  Value:
  1. Press "Add value". The back-reflection energy is filled in for
     you; any other PV name can be typed instead.
  2. "Read now" shows what that value has been doing over the last
     minute, so you can pick the two limits from a real number
     instead of guessing.
  3. Fill in "Warn over" and "Trip over" and save. Leave a box empty
     to switch that level off.

  The arrow button under the list re-takes the reference picture of
  the selected screen condition — use it when what you are watching
  has changed for a good reason and the new state should count as
  normal.

  A value condition needs no rectangle at all: with only value
  conditions in the list, Start still works.


THE OLD SINGLE-RECTANGLE WATCH

  "Set reference" at the top still draws one quick rectangle without
  giving it a name, and "Preview region" shows what is inside it. It
  is watched alongside the conditions. Use it for a one-off; use a
  condition for anything you want to keep.


THE CIRCLE IS THE WHOLE STATE

  Grey     nothing to watch yet
  Orange   ready, not watching
  Green    watching
  Red      something fired, the alarm is up


WHEN THE ALARM GOES OFF

  The alarm window flashes, either its whole background in the
  chosen colour or just the chosen image, and a sound plays.
  Watching stops by itself, so it does not keep re-alarming.

  The sentence you wrote for that condition appears as a red badge on
  the small overlay and in the message log, so it is still readable
  after the flashing has been dismissed.

  Click the flashing area, or press Esc, to dismiss it and come back
  to the control window.

  A value that cannot be read never fires the alarm — the reason goes
  into the message log instead. A condition that cannot fire at all
  (a screen area with no reference, a value with no trip limit) says
  so in the log when you press Start.


THE MACHINE VALUE BADGES

  They are read only while the screen watch is running, and only
  then. Nothing is shown while a value is in range. When one goes
  out, a badge appears with the value and an arrow for the direction.

  Orange   past the first limit
  Red      past the second limit
  Purple   a chiller's actual temperature is outside the range it is
           allowed to run in at all, whatever its setpoint says.
           Purple beats orange and red and shows the real
           temperature, because a chiller can sit exactly on its
           setpoint and still be at the wrong temperature.

  The limits are edited in Settings, under PV Limits, and save
  themselves. The purple range is fixed in the program.


THINGS THAT SAVE YOU WORK

  - The conditions, their reference pictures and their limits are all
    kept in the settings file next to the program, so they are there
    again next time.
  - Region presets: save the quick rectangle under a name and load it
    again instead of drawing it.
  - Window positions are remembered, either globally or per preset,
    and they are recorded by dragging the window where you want it —
    there are no coordinates to type. A window comes back exactly
    where it was left, and every window the program opens is kept
    fully on a screen: if a remembered spot belongs to a monitor that
    is not connected any more, or would leave part of the window over
    an edge, it is moved just enough to be completely visible.
  - While watching, the control panel turns into a small
    see-through overlay: only the circle and the badges stay
    visible, and clicks pass through the rest of it to whatever is
    underneath.

-----------------------------------------------------------------
