Announcer — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Announcer_Full.txt  ("Details" button)
-----------------------------------------------------------------

THE PROGRAM DOES TWO SEPARATE JOBS AT ONCE

  1. It watches a rectangle of the screen you draw yourself, and
     raises an alarm — a flashing image or colour, plus a sound — as
     soon as what is drawn there changes. Good for a camera window,
     a status display, or any indicator you cannot sit and stare at.

  2. While it is watching, it also reads eight machine values twice
     a second and shows a coloured badge for each one that is out of
     range: the helium pressure, the Alpha voltage, and how far each
     of the six chillers is from its setpoint. There is nothing to
     set up for this half — but note that it only runs while the
     screen watch is running. Press Start and the badges start
     working; stop watching and they go away.


HOW THE SCREEN WATCH WORKS

  It takes a picture of your rectangle, keeps it as the reference,
  and then takes another one twice a second. If the average
  difference between the new picture and the reference is bigger than
  the threshold, that counts as a change and the alarm goes off.

  So it does not understand what it is looking at. It only knows that
  the picture is no longer the same one. That is what makes it work
  on anything.


SETTING IT UP

  1. Pick the monitor from the dropdown. "Identify" puts a number on
     each screen for three seconds so you can tell which is which.
  2. Press "Set reference". The screen dims; drag out the rectangle
     you want watched and let go. Esc cancels.
  3. Press Start. The circle turns green.

  The arrow button re-takes the reference picture without asking you
  to draw the rectangle again — use it when the thing you are
  watching has legitimately changed and you want the new state to
  count as normal.

  "Preview region" shows you what is inside the rectangle right now.


THE CIRCLE IS THE WHOLE STATE

  Grey     no rectangle set yet
  Orange   rectangle set, not watching
  Green    watching
  Red      a change was detected, the alarm is up


WHEN THE ALARM GOES OFF

  The alarm window flashes, either its whole background in the
  chosen colour or just the chosen image, and a sound plays.
  Watching stops by itself, so it does not keep re-alarming.

  Click the flashing area, or press Esc, to dismiss it and come back
  to the control window.


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

  - Region presets: save the rectangle under a name and load it
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
