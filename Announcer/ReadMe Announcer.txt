Announcer — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Announcer_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  You give it a list of things that have to stay true. It checks all of
  them twice a second, and the moment one of them stops being true it
  raises the alarm: a flashing picture or colour, a sound, and the
  sentence you wrote for that one thing.

  There are two kinds of thing to watch:

    A value        an archived number stays inside its limits — a
                   pressure, a voltage, how far a chiller is from its
                   setpoint, an energy.
    A screen area  a rectangle of a screen still looks the way it did
                   when you took its reference picture. Good for a
                   status box, a warning lamp, a number, a camera
                   window — anything drawn on a screen.

  Each one carries a switch that says what happens when it goes wrong:

    shows only         the row goes red and a badge appears. Nothing
                       flashes, nothing beeps.
    raises the alarm   that, and the flashing window and the sound.


PRESETS — SETS OF ALARMS

  A preset is a named set of alarms. Pick one in the drop-down at the
  top of the window and it is the only set you see and the only set
  that is watched, so a night shift and a commissioning run can each
  have their own list and you switch between them with one click.
  "All alarms" shows everything; "Unassigned" is everything you have
  not put in a set.

  Make and rename sets with "Manage presets". Put alarms in a set by
  ticking Presets in the alarm's own editor, or by picking the rows
  and RIGHT-CLICKING them — the menu says how many it is about, for
  instance "Assign these 3 to a preset". Deleting a set never deletes
  its alarms — they simply become Unassigned.

  Each preset also keeps its OWN place for the circle and for the
  alarm picture, on the Alarm tab under "Where it appears": one set
  can have them over the left screen and another over the right. A
  preset you have not placed yet follows the last place you used. The
  button "Use these places for every preset that has none" hands the
  current places to all the sets still unplaced, and leaves the ones
  you placed by hand alone.


THE FOUR TABS

  Watch    Everything being watched, one row each, with what it is
           reading right now and whether it is holding — in three
           blocks: what is in range, what is not, and what is not
           being watched at all. The green or red bar at the top names
           the worst thing outright. The message log is underneath.

  Values   The numbers. Click a limit and type — that is all there is
           to changing a limit. Add value lets you search the
           archiver's own list of channels by a few words from the
           name, so you do not have to know it by heart.

           Remove takes every row that is picked off the list. Pick as
           many as you like — Ctrl-click or Shift-click — and it names
           them all before it removes anything.

           RIGHT-CLICK A ROW for the same things plus "Assign to a
           preset". The menu says how many rows it is about, for
           instance "Remove these 3".

  Areas    The rectangles. Add area dims the screens; drag out the
           rectangle and let go. The picture on the right shows the
           area as it is now next to its reference, each with a black
           edge round it so you can see where the rectangle ends, and
           the live difference number underneath.

           "Take the picture again" photographs the same rectangle
           once more and keeps that as normal. It says the time it
           did it, because a new picture of an unchanged screen looks
           exactly like the old one.

           Right-click a row here too, for the same menu.

  Alarm    What the alarm looks like, what it sounds like, which
           speaker it comes out of, and where on the screen it
           appears. The sound starts switched OFF — tick it on here.
           "Place the alarm window" lets you park the flash exactly
           over something that is already on a monitor, so that thing
           is what appears to blink. The Test buttons are there to be
           used — an alarm nobody has ever seen or heard is an alarm
           nobody knows is broken.


THE FOURTEEN VALUES IT STARTS WITH

  Helium volume, the Alpha seeder voltage, and each of the six
  chillers twice: once for how far it is from its setpoint, once for
  whether the temperature itself is right. They are set to "shows
  only", which is what they have always done.

  They are ordinary rows. Change them, switch them off, delete them.
  "Restore the standard values" puts back any that are missing.


THE CIRCLE, AND WHAT IT MEANS

  grey     nothing is switched on
  orange   ready, but not watching
  green    watching
  red      something fired

  While it is watching, the window disappears and only the circle is
  left, floating on top of everything. You can click straight through
  the space around it, so it does not get in the way. Click the circle
  to stop watching and bring the window back; drag it to move it.

  The readings keep coming whether it is watching or not. Watching
  only decides whether something wrong actually raises the alarm.

  A RED EXCLAMATION MARK next to the circle means the readings have
  not been arriving for over five minutes — the archiver is not
  answering, or the channel is dead. Nothing else is written next to
  the circle about it; the message log on the Watch tab says which
  channel it is and what the archiver said.


WHEN SOMETHING FIRES

  The picture or colour flashes where you put it, the sound plays, and
  the sentence you wrote appears next to the circle. Watching STOPS, so
  it cannot go off again every half second.

  Click the flash, or press Esc, to put it away. Then press Reset and
  Start watching again. If the thing is still wrong it fires straight
  away — that is the honest answer, not a bug.


TWO THINGS WORTH KNOWING

  A screen area does not understand what it is looking at. It only
  knows the picture is no longer the same one. So draw the rectangle
  TIGHT around the thing that matters: a small change inside a big
  rectangle averages away to almost nothing.

  A value that could not be read is never shown as fine. It goes grey
  and says why — "nothing archived in the window" is a different thing
  from "the archiver could not be reached", and the log says which.


IF IT DISAPPEARS

  It now writes down what happened to it, every time:

    C:\Users\<you>\AppData\Local\Announcer\announcer_crash.log

  Send that file with the report. Before, a crash left nothing behind
  at all, so there was nothing to look at afterwards.

  One thing that looks like a crash but is not: while you are drawing
  an area the screens go dark and the window is out of the way. If the
  keyboard has gone somewhere else, Esc may not reach it — a single
  click anywhere cancels, and after two minutes it gives up on its
  own and brings the window back.
