Announcer — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-24  (a.py, 2902 lines)
-----------------------------------------------------------------
Short version: ReadMe Announcer.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT THE PROGRAM DOES
=================================================================

1.1 CONDITIONS

    A condition is one thing that has to stay true while the program
    is watching. You keep a list of them; all of them are checked
    twice a second, and the first one that stops being true raises
    the alarm and says what needs doing.

    Two kinds:

      Screen area   a rectangle of a screen still looks the way it
                    did when its reference picture was taken
      Value         a machine value stays under its limit

    Each one has a name and a sentence of your own — that sentence is
    what appears when it fires, so it can say "check the L3BT
    alignment in HP" rather than "change detected".

    The screen check does not understand what it is looking at, and
    that is the whole point: it works on a camera window, a
    control-system screen, a warning lamp, a number, a graph, a web
    page — anything that is drawn on a screen.

1.2 THE QUICK RECTANGLE

    "Set reference" at the top draws one rectangle without giving it
    a name, exactly as the program has always worked. It is watched
    alongside the conditions and it is not remembered as a condition.
    Use it for a one-off, and a condition for anything you want to
    keep.

1.3 THE MACHINE VALUE BADGES

    Eight values from the machine, read twice a second, each shown
    as a coloured badge only when it is outside its limits. Nothing
    to configure and nothing to draw.

    IMPORTANT: this half only runs while the program is watching.
    Pressing Start turns it on; stopping turns it off and the badges
    disappear with it.


=================================================================
2. SETTING UP
=================================================================

2.1 A SCREEN AREA CONDITION

  1. "Add screen area" in the Conditions panel.
  2. NAME, and the sentence to show when it fires.
  3. MONITOR, then "Draw area". The chosen screen dims under a
     see-through overlay; drag out the rectangle and let go. Esc
     cancels without changing anything.

     The overlay is deliberately built from two windows: a dim one
     over the screen, and a solid red frame for the border. A border
     drawn on the see-through window would be see-through too, and
     invisible against a bright display.

  4. The reference picture is taken as soon as you let go, and shown
     in the dialog. "Take reference" takes it again — use it if the
     screen was mid-change at that moment.
  5. FIRES ON A CHANGE OVER — the sensitivity, section 5.
  6. Save, and tick the condition in the On column.

  "Identify" at the top flashes a big number on each screen for three
  seconds, so you do not have to guess which one Windows calls number
  2.

2.2 A VALUE CONDITION

  1. "Add value". The back-reflection energy (L3-PM03-023:Energy) is
     filled in for you; any other PV name can be typed instead.
  2. "Read now" reads that value for the last minute and shows what
     it is doing now and its highest and lowest — pick the limits
     from that, rather than guessing.
  3. WARN OVER   past this, an orange badge appears and nothing else
                 happens.
     TRIP OVER   past this, the alarm goes off.
     Leaving a box empty switches that level off. Empty is not zero:
     a limit of zero on an energy would fire the moment the laser
     runs.
  4. UNIT is only used in the text of the badge.

  A value condition needs no rectangle: with only value conditions in
  the list, Start works and nothing has to be drawn.

2.3 THE LIST ITSELF

  On            click the box to switch a condition on or off; it is
                saved at once
  Add / Edit    Edit, or a double-click, opens the same dialog again
  Delete        removes it, after asking
  arrow (↺)     re-takes the reference picture of the selected screen
                condition — for when what you watch has changed for a
                good reason and the new state should count as normal

  The list, the reference pictures and the limits all live in the
  settings file next to the program, so they come back next time.

  The rectangle is remembered as a position on the desktop, so a
  condition belongs to the computer it was made on and to that
  computer's monitor arrangement. Move the monitors around and the
  area has to be drawn again.

  PREVIEW: hover "Preview region" for a thumbnail of what is inside
  the quick rectangle right now, up to 640 by 400. Click to pin it
  open.


=================================================================
3. THE CIRCLE
=================================================================

The coloured circle is the entire state of the program in one
glance:

  grey     nothing to watch yet
  orange   ready, not watching
  green    watching
  red      something fired and the alarm is up

Nothing else needs to be read to know where you are.


=================================================================
4. WHEN SOMETHING FIRES
=================================================================

  - The alarm window flashes, in one of two modes (section 6).
  - A sound plays, if sound is switched on.
  - The sentence belonging to that condition appears as a red badge
    on the overlay and as a line in the message log, so it can still
    be read after the flashing is gone.
  - Watching stops by itself. That is deliberate: whatever changed
    has now changed, and re-alarming every half second would be
    useless.
  - Click the flashing area, or press Esc, to dismiss the alarm.
    The control panel comes back and the circle goes orange, ready
    to start again.

Two things never raise the alarm:

  - A value that could not be read at all. The reason goes into the
    message log in plain words instead; a missing reading is not a
    good reading, and it is not a bad one either.
  - A condition that cannot fire: a screen area with no reference
    picture, or a value with no trip limit. Pressing Start writes a
    line in the log for each of those, so a condition cannot sit
    there ticked and silently do nothing.

One case is worth knowing about: if the screen resolution or the
display scaling changes while watching, the new picture no longer has
the same size as the reference and the two cannot be compared. That
counts as the condition failing, and the badge says both sizes. The
alternative — quietly reporting that everything is fine — is the one
wrong answer.

In "Image only" mode the clickable area is the image itself, and it
stays clickable through the dark half of the blink. (This is why the
window is never made fully transparent between blinks — a window at
zero opacity stops receiving clicks, so the alarm would become
impossible to dismiss at the wrong moment.)


=================================================================
5. THE THRESHOLD, AND HOW TO CHOOSE IT
=================================================================

The comparison is the average difference per pixel, on a scale where
0 means identical and 255 means black against white. The threshold
is that average, and it defaults to 2. Each screen condition has its
own — a noisy camera picture and a line of text need different
numbers — and the one in Settings belongs to the quick rectangle.

What that means in practice:

  - A large rectangle with one small thing changing in it produces a
    small average. Watch a tight rectangle around the thing you care
    about, not the whole window.
  - A live camera image with noise in it produces a constant small
    average even when nothing happened. Raise the threshold until it
    stops triggering, or watch a quieter part of the screen.
  - Anti-aliased text and a moving mouse cursor both count as
    changes. Keep the cursor out of the rectangle.

Range 0.5 to 50. Lower is more sensitive.


=================================================================
6. THE ALARM APPEARANCE
=================================================================

There are two windows: the control panel and the alarm window. They
are separate on purpose, so the alarm can sit exactly on top of
whatever you need to see it against while the control panel lives
somewhere out of the way.

FLASH MODE
  Background color   the whole alarm window blinks in the chosen
                     colour
  Image only         only the chosen image blinks, filled with the
                     chosen colour; everything around it stays
                     see-through, and the dark half of the blink
                     shows nothing at all

FLASH COLOUR       click to pick it
FLASH DURATION     0 to 60 seconds, 3 by default
BLINK SPEED        fixed, about three blinks a second

IMAGE              which file from the images folder is used

SOUND
  Play sound on change   on or off
  Freq / Duration        the built-in beep, in hertz and
                         milliseconds
  Sound file             a .wav from the sounds folder instead of
                         the beep

The images and sounds folders sit next to the program. Without the
images folder the alarm can only flash a plain colour, because the
picture is read from disk at the moment it is needed. Both folders
are listed in the build settings so that every build carries them —
a deployment that loses them has to be redone.


=================================================================
7. REGION PRESETS AND WINDOW POSITIONS
=================================================================

A preset stores the QUICK rectangle under a name, so a routine you do
every week does not have to be drawn again. Conditions keep their own
rectangles and need no preset.

  Load          use the selected preset
  Save region   store the current rectangle under a name
  Delete        remove it

Window positions are remembered too, and they are recorded by
dragging rather than typed:

  Set control window   drag the control panel where you want it,
                       then confirm
  Set image window     the same for the alarm window. This is how
                       you line the alarm image up with whatever is
                       behind it.

A position can be global (used for everything) or stored inside a
preset (used only for that one). A window returns to exactly the spot
it was recorded at, so the alarm image stays lined up with what is
behind it, however many times the position is saved and loaded again.

No window can end up where you cannot see it. Before any window is
shown — the panel, the alarm, the settings, a preview, a tooltip — its
position is checked against the screens that are actually connected
and, if part of the window would fall outside one, it is moved just
enough to be completely visible. It stays on the screen it was meant
for; only a remembered monitor that is gone sends it to another one.

Everything is kept in one settings file next to the program: the
saved rectangles, the window positions, the limits and the flash
settings.


=================================================================
8. THE MACHINE VALUES IN DETAIL
=================================================================

8.1 WHAT IS READ

    Helium volume       the helium pressure, in PSI
    Alpha voltage       the seeder voltage, in V
    Chiller DA1..DA4    how far the chiller is from its setpoint, °C
    Helium Chiller      the same
    Utility chiller     the same

    The six chiller rows are the difference between the measured
    temperature and the setpoint, not the temperature itself. A
    chiller that is asked for 12 and delivers 12.5 shows 0.5.

8.2 HOW THEY ARE READ

    Not live from the machine, but from the archiver, over the
    network. Each reading asks for that value's samples over the
    last minute and averages the most recent twenty-five of them.

    Averaging is what makes the badges usable. A single sample of a
    chiller deviation crosses a 0.3 limit constantly; the average
    over twenty-five samples only crosses it when something is
    genuinely off. The cost is that the badge reacts over seconds,
    not instantly — which is the right trade for these values.

    The reading happens on a background thread, so a slow or
    unreachable archiver cannot freeze the window. A value that
    cannot be read shows no badge, and the reason is written into
    the message log in plain words rather than as a technical error
    — hover a line in the log for the explanation.

8.3 THE COLOURS

    orange   past the first limit  (the Lo or Hi value)
    red      past the second limit (the Lolo or HiHi value)
    purple   the chiller's ACTUAL temperature is outside the range it
             is allowed to run in at all

    Purple outranks both of the others, and it shows the real
    temperature instead of the deviation. The reason it exists: a
    chiller can hold its setpoint perfectly and still be at
    completely the wrong temperature, because somebody set the
    setpoint wrong. The deviation check cannot see that; the
    absolute check can.

    The allowed ranges are 7 to 17.5 °C for DA1 to DA4 and the
    Helium chiller, and 18 to 22 °C for the Utility chiller. They
    are fixed in the program, not editable.

    The arrow at the end of the badge says which way the value went.

8.4 THE LIMITS

    Settings -> PV Limits, one row per value and four numbers each:

      Lolo / Lo   the low limits  (red / orange)
      Hi / HiHi   the high limits (orange / red)

    They save themselves the moment you change them. The defaults
    are:

      Helium volume    orange below 46 or above 57
                       red below 45.5 or above 60
      Alpha voltage    orange below 1.2 or above 1.9
                       red below 1.1 or above 2.2
      every chiller    orange beyond ±0.3, red beyond ±0.6

8.5 THE LAYOUT

    Only the badges that are actually alerting are shown, and they
    vary in width, so they are laid out by hand and wrapped onto
    another line when they run out of room. The result is that none
    of them is ever cut off, however many are up at once. The badges
    belonging to conditions are laid out in the same row.

8.6 HOW A VALUE CONDITION IS JUDGED

    Not on an average, unlike the eight badges above: on the HIGHEST
    sample of the last ten seconds. One shot over the limit is the
    whole event — an average over a minute of shots would hide
    exactly the thing you asked to be told about.

    The archiver only writes a sample when the value changes, so a
    value that jumped over the limit and then sat perfectly still
    produces nothing recent at all. In that case the newest sample of
    the last minute is used, because that is the value the machine is
    still holding. Only when there is nothing at all is no judgement
    made.

    Two consequences worth knowing:

      - The reading comes from the archiver, which publishes about a
        second late, so the alarm is a second or two behind the
        machine. This is not a program to protect hardware with; it
        is a program that tells a person to go and look.
      - A spike that has already passed can still fire, if it is
        inside the last ten seconds. That is the intended behaviour:
        you want to be told that it happened.


=================================================================
9. THE OVERLAY MODE
=================================================================

While watching, the control panel becomes a small borderless
see-through overlay. Only the circle and the badges stay visible;
the rest of it lets clicks straight through to whatever is
underneath.

This is what makes the program usable in practice — it can sit on
top of the screen you are actually working with, without covering it
and without being in the way of the mouse.


=================================================================
10. WHEN SOMETHING GOES WRONG
=================================================================

  It alarms constantly
      The rectangle contains something that is always moving: a live
      camera, a clock, a cursor, a blinking caret. Move the
      rectangle or raise the threshold.

  It never alarms
      Either the change is too small relative to the rectangle
      (shrink the rectangle around the thing that matters), or the
      threshold is too high, or watching was stopped by a previous
      alarm and never restarted — check the circle.

  A condition is ticked but nothing ever happens
      Press Start and read the message log. A screen area with no
      reference picture and a value with no trip limit both say so
      there, and both are silent by design.

  Start does nothing at all
      There is nothing to watch: no quick rectangle and no condition
      switched on. The status line says so.

  A screen condition fires the moment watching starts
      The screens have been rearranged or the display scaling
      changed, so the area is not where the reference was taken. The
      badge says both sizes when that is the cause. Draw the area
      again.

  A value condition will not stop firing
      The value is still over the limit, or it went over inside the
      last ten seconds. Check with "Read now" in the condition,
      which shows what it has actually been doing.

  The reference picture is refused as too big
      The picture is kept inside the settings file, so it has to stay
      small. Draw the box that changes, not a whole screen.

  The alarm flashes but I hear nothing
      Sound is switched off, or the chosen .wav is missing from the
      sounds folder. The built-in beep always works.

  The alarm window shows no picture
      The images folder is missing next to the program, or the
      chosen file is not in it. The alarm still works, as a plain
      colour.

  I cannot dismiss the alarm
      Click the image itself in "Image only" mode, not the empty
      space around it. Esc always works.

  No badges at all
      Either every value is in range, which is the normal case, or
      watching is not running — the values are only read while it
      is. If the message log fills with read failures instead, the
      archiver is not reachable from this computer.

  A badge is purple but the deviation is tiny
      That is exactly the case purple is for: the chiller is holding
      a setpoint that is itself wrong. Check the setpoint, not the
      chiller.

  A remembered window position puts the window somewhere odd
      The monitor it was recorded on is disconnected or was
      rearranged, so the window was moved to a screen that exists.
      Re-record the position with "Set control window" / "Set image
      window".

  A window sits a little away from where it was recorded
      It should not any more. If the panel or the alarm window is
      short of where you left it, or drifts a bit further every time
      the position is saved and loaded, the version in use is older
      than August 2026 — take the current one.

-----------------------------------------------------------------
