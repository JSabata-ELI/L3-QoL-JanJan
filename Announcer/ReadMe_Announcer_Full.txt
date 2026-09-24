Announcer — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Short version: ReadMe Announcer.txt  ("ReadMe" button)
-----------------------------------------------------------------

=================================================================
1. WHAT THE PROGRAM DOES
=================================================================

  You keep a list of things that have to stay true. The program checks
  the whole list twice a second and the moment one of them stops being
  true it raises the alarm, saying in your own words what needs doing.

  There is one list and two kinds of thing on it.


1.1 A VALUE

  An archived number that has to stay inside its limits. Four limits,
  and any of them can be left empty:

    Lo Lo   below this it FIRES
    Lo      below this it only warns
    Hi      above this it only warns
    Hi Hi   above this it FIRES

  An empty box means that limit is switched off. It does NOT mean
  zero. A zero limit on an energy would fire the moment the laser
  runs, so empty has to mean "don't check".

  A value can also watch the DIFFERENCE between two channels — a
  chiller's temperature minus its setpoint, for instance. The second
  channel is held forward, which matters: a setpoint is written once a
  week, so a ten-second window of it is empty almost always, and
  without holding it forward the deviation would be unreadable exactly
  when the chiller was behaving.


1.2 A SCREEN AREA

  A rectangle of a screen that has to keep looking like its reference
  picture. The program keeps the reference, takes a new picture twice a
  second, and compares them. If the average difference is bigger than
  the sensitivity number you set, that counts as a change.

  It does not understand what it is looking at. It only knows the
  picture is no longer the same one. That is exactly what makes it work
  on anything at all.


1.3 SHOWS ONLY, OR RAISES THE ALARM

  Every row carries that switch.

    shows only         the row goes red in the table, and a badge with
                       your sentence appears next to the circle.
                       Nothing flashes and nothing beeps.
    raises the alarm    all of that, plus the flashing window and the
                       sound.

  The fourteen machine values the program starts with are set to
  "shows only", because that is what they have always done. Anything
  you add yourself defaults to raising the alarm.


1.4 PRESETS — SETS OF ALARMS TO WORK IN

  A preset is a named set of alarms. Pick one in the drop-down at the
  top of the window and it is the only set you see: the Watch, Values
  and Areas lists hold nothing else, and nothing else is watched
  either. So you can set up a night shift without the commissioning
  alarms in the way, and switch between them with one click.

  All alarms       everything there is — the normal view
  Unassigned       everything that is in no preset. The program makes
                   this one itself; you cannot rename or delete it.

  An alarm may be in several presets at once. Nothing is ever lost by
  making your first preset: what you do not put in one stays under
  Unassigned, and there it is still listed and still watched whenever
  Unassigned or All alarms is chosen.

  How to put an alarm in a preset:

    · press "Manage presets" at the top to make, rename or forget a
      set. Forgetting a set keeps its alarms — they become Unassigned.
    · in an alarm's own editor, tick the sets it belongs to under
      Presets.
    · or pick the rows in the Values or Areas list, RIGHT-CLICK them
      and choose "Assign to a preset" — every row you picked is
      changed, and the menu says how many it is about.

  Add an alarm while a preset is chosen and it joins that preset, or
  it would vanish the moment you saved it.

  Each alarm fires on its own. Being in the same preset as another
  alarm does not make one wait for the other. (The old "Group" column
  did exactly that; it is gone, and any group names you had are now
  presets with the same names.)


=================================================================
2. THE FOUR TABS
=================================================================

2.1 WATCH

  One table, one row per watched thing:

    On               tick it to watch it
    Name             what you called it
    What it watches  the channel, or the monitor and the size of the
                     rectangle
    Reading now      the number, or how far the picture has drifted
    State            in range / warning / FIRED / not refreshed /
                     no reading / off

  The table is in three blocks, each under its own grey heading line
  that says how many are in it:

    Watched · in range        ticked, and holding
    Watched · not in range    ticked, and something is wrong with it
    Not watched               not ticked at all

  A block with nothing in it is left out. Inside a block the order is
  the one you arranged on the Values and Areas tabs, and a row only
  ever moves when it changes block — a table that shuffles twice a
  second moves the row out from under your pointer just as you click
  it. When a row does move, your place in the list and the row you had
  clicked are kept.

  The bar above the table still names the worst thing outright.

  A row that is fine has no colour at all. Only a verdict worth
  looking at is coloured, so the one thing that is wrong stands out
  instead of being lost in a wall of green.

  Double-click any row to open it for editing on its own tab.

  The message log is underneath, on a splitter you can drag. Every
  failure arrives there as a sentence with an explanation, never as a
  raw error. "Reading report" writes out how many reads have been
  made, how many failed and how many had to be written off.


2.2 VALUES

  The whole list of numbers, in this order:

    On · Name · Say when it fires · Lo Lo · Lo · Hi · Hi Hi · Unit ·
    Channel · Fires · How read

  CLICK A LIMIT AND TYPE. That is the whole of changing a limit — the
  yellow-tinted cells are the ones you can type into. The name, the
  unit and the sentence can be typed in place as well.

  Which presets a row belongs to is NOT a column — the drop-down at
  the top decides which rows are on the list at all. See 1.4.

  Add value opens the editor:

    Name and the sentence you want to read when it fires
    Channel        type it, or press Find
    Find           searches the archiver's own list of every channel
                   it holds — nearly ten thousand names. Type a few
                   words from the name in any order; each word has to
                   appear somewhere, nothing has to be at the start
                   and nothing has to touch. "chl temp" finds
                   L3-UTIL-CHL03-001:Temp. It tells you how many it
                   is showing out of how many matched.
    Read now       reads it from the archiver and says what it holds,
                   so you can check the name is right before you save
    the difference between two channels — the chiller case
    the four limits and the unit
    How it is read:
      worst in the window          one shot over the limit is the
                                   whole event, so nothing is
                                   averaged away. This is the default
                                   for anything you add.
      average of the newest few    steadier. A single chiller sample
                                   crosses its limit constantly; the
                                   average does not. This is what the
                                   fourteen machine values use.
      newest value, however old    for a channel written only when it
                                   changes — a setpoint, a state, a
                                   valve. It looks back an hour, then
                                   six, then a day, then a week, then
                                   a month, and stops at the first
                                   window that has anything in it.
    When it stops holding — shows only, or raises the alarm
    Presets        the sets this alarm belongs to. Tick none and it
                   belongs to Unassigned. "New preset" makes one
                   without leaving the editor.

  Duplicate is the quick way to add a second, similar channel.

  REMOVE is a button above the table and an entry on the rows' own
  right-click menu; either way it takes every row you have picked off
  the list for good. Pick as many as you like with Ctrl-click or
  Shift-click — the button stays live for one row or twenty, and the
  menu says how many it is about ("Remove these 3"). It names them
  all before it does anything.

  RIGHT-CLICK A ROW for the rest: Edit, Duplicate and "Assign to a
  preset". Assigning is only on the menu, because it is about the rows
  you have already picked rather than about the tab.

  "Restore the standard values" puts back any of the fourteen machine
  values that are missing — matched on the channel, so a row you have
  renamed is not added twice.

  EVERY COLUMN is as wide as the widest thing in it, heading included,
  and every one of them can still be dragged wider or narrower by its
  divider. A width you set yourself is then left alone.


2.3 AREAS

  The rectangles, in this order: On, Name, Sensitivity, Say when it
  fires, Fires, Rectangle, Reference. What the area IS comes first
  and where it is comes last, and no column is wider than what is in
  it — so if the last two do not fit the window, the scrollbar
  underneath reaches them. The monitor number is written into the
  Rectangle cell, with the size and the corner.

  Sensitivity can be typed straight into the table. Presets work here
  exactly as they do on the Values tab — see 1.4.

  RIGHT-CLICK A ROW for Edit, "Take the picture again", "Assign to a
  preset" and Remove. The last two are no longer buttons above the
  table; they are about the rows you have picked, so they are on the
  rows, and with several picked the menu says how many.

  Add area:
    1. Give it a name and the sentence you want to see.
    2. Press "Draw the area". Both windows get out of the way, then
       every screen dims. Drag out the rectangle and let go — the
       size in real pixels is shown above it as you drag. Esc
       cancels.
       You do not have to pick a monitor first: drag on whichever
       screen the thing is on.
    3. The reference picture is taken straight away, with the
       program's own windows still hidden, and shown underneath.
    4. Save, and tick it On.

  "Take the picture again" photographs the SAME rectangle once more
  and keeps that as the reference. Use it when the screen has
  legitimately changed and you want the new look to count as normal.
  It does not ask you to draw anything again.

  A picture of a screen that has not changed is identical to the one
  it replaced, so there would be nothing at all to see. That is why
  the line under the preview says "reference picture taken at" and
  the time — that line, and the difference dropping to nearly zero,
  are the proof it happened. The same line is in the editor, under
  its own picture.

  THE PREVIEW on the right shows the selected area as it is now, its
  reference below it, and the live difference next to the sensitivity.
  Both pictures have a black edge drawn round them: the rectangle is
  usually smaller than the box it is shown in, and without the edge
  there is no telling a white screen apart from the white box behind
  it.

  SAVED RECTANGLES are the presets from earlier versions, and they
  still hold the same rectangles. Save keeps the selected area's
  rectangle under a name; Load puts a saved rectangle into the
  selected area and takes a fresh reference picture; Delete forgets
  the saved rectangle and leaves the watched areas alone.


2.4 ALARM

  The flash:
    What flashes    fill the window with the colour, or one shape
                    painted in the colour
    Colour          the colour picker
    Colours         the picked colour · a different colour every
                    blink · blue to red across the width, drifting ·
                    rings running out of the centre, never dark
    Blink           how long each half of a blink lasts. Zero means
                    do not blink at all.
    For             how long it flashes. Zero means until somebody
                    clicks it or presses Esc, which is the usual
                    answer: an alarm nobody saw did not work.
    Shape           which picture, from the images folder next to the
                    program. The preview shows it as it will really
                    flash — the shape, filled with the colour.

  The sound:
    the on/off switch, which sound, the beep's pitch and length, the
    Bluetooth run-up, and which speaker it comes out of.

    THE SOUND STARTS SWITCHED OFF. A new installation flashes and
    says nothing until you tick it on here.

    THE SPEAKER MATTERS. Windows keeps a separate output per program,
    so a program parked on the built-in speaker stays there even after
    a Bluetooth one has been made the default — which is exactly how a
    lab can hear every system sound and nothing from this one. Naming
    the speaker here settles it. The list is re-read every time you
    open it, because a Bluetooth speaker only appears once it has
    connected.

    THE BLUETOOTH RUN-UP is silence played in front of the sound. A
    Bluetooth link only carries audio once it has opened, which takes
    a moment when nothing has been played for a while. Without the
    run-up the whole beep lands in that gap and nobody hears it. 800
    milliseconds is the default.

    "Test the sound" plays it now and says what happened. If it could
    not be played it says so, rather than leaving you to wonder.

  Where it appears:
    "Place the alarm window" shows the alarm as a framed rectangle
    with grips on its corners and edges, which you can drag and size.
    It is only faintly tinted and the picture shows through it, so
    whatever is already on the monitor stays visible underneath and
    you can line the alarm up on it exactly — park it over a picture
    on a screen and that picture is what appears to blink.

    Drag the MIDDLE of the rectangle to move it; drag one of the
    eight grips on its edge to size it, and the pointer changes over
    a grip to say so. It cannot be made smaller than a grip or two,
    because a rectangle with no edge left is a rectangle you cannot
    get hold of again. "Fit to the picture" makes the window exactly
    the picture's own size so it is not stretched. "Keep this place"
    saves it; Cancel, or Esc on the rectangle itself, leaves it where
    it was.

    The little box that asks is deliberately NOT one of those windows
    that blocks everything else. It cannot be: while it blocked, the
    rectangle it is asking about could not be dragged at all, which
    is the bug that was reported on 18.9.2026.

    "Place the circle" shows the circle now, wherever it last sat,
    so you can drag it to where you want it while the program is
    watching. Keep this place saves it. You can also just drag it
    while it is watching — either way it is remembered. While it is
    being placed, letting go of it does NOT stop watching.

    "Put the circle back in the corner" forgets where it was dragged
    to, and it goes back to the top right of the main screen. It is
    about the preset you are on and no other.

    BOTH places belong to the preset. The line at the top of "Where
    it appears" says which preset you are setting and where its two
    things sit, and switching preset in the header switches the
    places with it — one set of alarms can flash on the left screen
    and another on the right. A preset you have never placed simply
    follows the last place you used anywhere, so nothing moves the
    first time you start this version.

    "Use these places for every preset that has none" hands the
    current circle and alarm places to every preset still unplaced,
    "All alarms" and "Unassigned" included. A preset you placed
    yourself — or put back in the corner on purpose — is left as it
    is. The button greys out when every preset has its own places,
    and its tooltip names the ones still waiting.

    The sentences appear beside the circle and never on top of it,
    nor on top of the exclamation mark: to its right if there is
    room, otherwise to its left, otherwise underneath. Only the worst
    five get a badge of their own; if more than that are out of range
    the last badge says how many more.


=================================================================
3. THE CIRCLE
=================================================================

  grey     nothing is switched on — there is nothing to watch
  orange   something is switched on, but not watching
  green    watching
  red      something fired

  Click it to start or stop. It is the same as the button beside it.

  While it is watching, the window disappears and ONLY the circle is
  on screen, floating above everything. The space around it is not
  part of the window at all, so clicks go straight through to whatever
  is behind — it cannot get in the way of real work. Drag it to move
  it; where you leave it is remembered.

  Any sentence worth reading appears as a badge beside it: red for
  something that fired, amber for a warning. ONLY readings that are
  out of range get a sentence. A reading that did not arrive at all
  never does — see the exclamation mark below.

  A RED EXCLAMATION MARK, next to the circle, no background and no
  words: the readings have not been arriving for over five minutes.
  The archiver is down, the network is gone, or the channel itself is
  dead. It is the only thing said about that on screen; which channel
  and what the archiver answered is in the message log on the Watch
  tab. It goes away by itself the moment a reading comes back.

  Five minutes, not five seconds, on purpose: a missed read or a bad
  minute at the archiver is normal and interrupts nobody. Five minutes
  of it means the alarm cannot see what it is watching.


=================================================================
4. WHEN SOMETHING FIRES
=================================================================

  1. The flash appears where you placed it and the sound plays.
  2. Your sentence appears as a red badge beside the circle, and the
     circle goes red.
  3. WATCHING STOPS. Nothing can re-alarm every half second.
  4. The circle and the badge STAY while the flash is up — that badge
     is the only place your own words can be read at that moment, so
     the window does not come back over the top of it.
  5. Click the flash, or press Esc, or click the circle. The flash
     goes away and the window comes back.
  6. Press Reset, then Start watching.

  If the thing is still wrong when you start again, it fires straight
  away. That is the truth, not a fault.

  In "one shape" mode only the shape itself takes clicks — everything
  around it is see-through and click-through. Esc always works, so
  there is no way to end up with a flash you cannot dismiss.


=================================================================
5. THE SENSITIVITY, AND HOW TO CHOOSE IT
=================================================================

  It is the average difference between the two pictures, on a scale of
  0 to 255, averaged over every pixel and every colour channel. Lower
  is more sensitive. The default is 2.

  What counts as difference: camera noise, anti-aliased text moving by
  a pixel, the mouse pointer crossing the rectangle, a clock digit
  changing. All of it.

  The important consequence is about SIZE, not about the number: a
  small change inside a big rectangle averages away to almost nothing.
  A number changing inside a whole camera window might move the
  average by 0.3. The same number in a rectangle drawn tightly around
  it moves it by 30. So the answer is almost always a tighter
  rectangle rather than a lower number.

  Use the preview on the Areas tab: it shows the live difference next
  to the sensitivity, so you can watch what normal looks like before
  you decide.

  If the area fires the moment you start watching, the usual cause is
  that the screen resolution or its scaling changed after the
  reference was taken. The program says so outright, with both sizes:
  "area is now 246x122 px, reference 369x183". Take the picture again.


=================================================================
6. HOW THE VALUES ARE READ
=================================================================

  From the archiver, over the network — not from the machine directly.
  The archiver publishes about a second late, so a value is a second
  or two behind the machine. This program raises a person, not a
  hardware interlock.

  The readings are taken WHETHER OR NOT it is watching: twice a second
  while watching, every two seconds while not. That is why the tables
  are never blank, and why you can set a limit by watching the value
  move.

  All the channels of one pass are read side by side, and the pass has
  a time limit of its own. One channel that will not answer therefore
  costs that channel and not the whole pass — it is reported as "not
  read in time" and the others come back as normal.

  An empty answer and a failed read are DIFFERENT THINGS and the
  program never confuses them:

    "nothing archived in the window"   the archiver was reached and
                                       answered; it holds nothing for
                                       that window. Often it simply
                                       means the channel name is not
                                       one the archiver writes.
    a failure                          it could not be asked, or it
                                       refused. The log says which,
                                       in words, with what to try.

  Either way the row goes GREY and never green. Reporting that
  everything is fine on the strength of a reading that was never taken
  is the one answer that must never happen.

  "Not refreshed" means something else again: the last GOOD reading is
  now more than fifteen seconds old, so the number on screen is stale.
  It does not mean the value stopped changing — the archiver only
  writes when a value changes, so a chiller holding its setpoint
  perfectly publishes nothing for minutes, and that is a healthy
  chiller.

  If a whole pass never comes back at all, the program says so, writes
  it off, and starts a new one. It never sits showing the last good
  numbers as though they were current.


=================================================================
7. WHERE THE SETTINGS LIVE
=================================================================

  presets.json, in the program's own folder. Everything is in there:
  the watched things, the limits, the saved rectangles, the flash and
  sound settings, where the alarm and the circle sit — and the
  reference pictures, as text.

  The reference pictures are stored ON PURPOSE rather than re-taken at
  every start. A picture taken fresh at start-up would quietly accept a
  screen that is ALREADY in the bad state as normal, which is the one
  way this program could fail silently. A reference bigger than about a
  megabyte is refused with a message instead — watch a smaller area.

  It is written a second after your last change, safely: to a
  temporary file that is then renamed, so a crash mid-write cannot
  cost you every rectangle you ever set up.

  Everything from earlier versions came through this rewrite: the
  saved rectangles, the window positions, the flash and sound
  settings, and any limits you had typed into the old PV Limits table
  — those became the limits of the fourteen machine values, and your
  typed number wins over the built-in default.


=================================================================
8. WHAT CHANGED IN THIS VERSION
=================================================================

  The program was rewritten. It looks and behaves like Image Tools and
  CSS Logger now, and it is one list instead of four different
  mechanisms.

  GONE:
    - The Halls tab and the whole "where the beam goes" check. Its
      main channel, L3BT-MSS:Beam_fate, is not archived at all, so
      that half could never fire.
    - The nameless "quick rectangle". Every rectangle is a named row
      now.
    - The screen area hidden inside a value condition. It is a row of
      its own, called "<the value's name> · area". Nothing changed
      about how it behaves: either half going wrong fired the alarm
      before, and either row does now.
    - The purple chiller badge. Each chiller is two rows instead: one
      for holding its setpoint, one for the temperature being right.
      One row, one thing measured.

  NEW:
    - Four limits per value instead of two, and both ends of the
      range.
    - The channel search, so a PV can be found by a few words rather
      than typed from memory.
    - Presets: named sets of alarms, so only the set you are working
      in is listed and watched. They took the place of the old
      "Group" column, which made two alarms wait for each other.
    - Limits typed straight into the table.
    - The live preview beside the areas.
    - A red exclamation mark beside the circle when the readings have
      not arrived for over five minutes — and, in exchange, NOT ONE
      WORD beside the circle about a read that failed. The panel over
      your work used to fill up with "not read yet" and "could not be
      read", which is noise. Asked for on 21.9.2026.
    - Remove is a button on the Values and Areas tabs again, as well
      as on the right-click menu. It was menu-only and looked as
      though it did nothing. While a menu was still open, the "are
      you sure" box underneath it could not be answered — the work
      is now done after the menu has gone.
    - Readings that keep coming when it is not watching.
    - "Not refreshed", so old numbers cannot pass for current ones.

  FIXED, after it crashed three times in two minutes on 17.9.2026
  while an area was being drawn:

    - Drawing an area now actually saves it. It never did. The
      editor has to get out of the way while you drag the rectangle
      out, or the program photographs its own window — and getting
      out of the way was quietly cancelling the whole editor. You
      drew the rectangle, you pressed Save, and it was thrown away.
      That is why there were fourteen values saved and not one area.
    - Pressing "Draw the area" twice in a row can no longer take the
      program down. The button switches itself off while you are
      drawing.
    - Each abandoned attempt used to leave a hidden copy of the
      editor running, with its timers still going. Measured over 250
      attempts: 312 of them still alive and a gigabyte of memory
      gone. Now nothing is left behind.
    - Closing the editor while the screens are dark now works. It
      used to leave the picker running with nobody to report back
      to, and after that "Draw the area" did nothing at all.
    - There is always a way out of the dimmed screens: Esc, a single
      click anywhere, or two minutes of waiting.
    - Watching an area is much cheaper. Photographing one small
      rectangle costs the same as photographing every screen — 269 ms
      and 30 MB a time, measured — and it used to do that once per
      area, twice a second, plus a third time for the preview in the
      window. Now it is one photograph per round, and the preview
      has its own slower clock.
    - The program writes down what happened to it when it dies. See
      section 9.
    - Closing the window really ends the program. It used to leave
      an invisible copy running, holding the settings file, so the
      next start made a second one.
    - "alarm_geometry" and "hud_position" no longer show up in the
      saved-rectangles list on the Areas tab. Loading one fed the
      alarm's position in as a rectangle, and deleting one forgot
      where the alarm appears.
    - "Playing…" under "Test the sound" now clears itself. The
      message from the sound was never being delivered.
    - Much less work while it is just sitting there watching. The
      table was being rewritten twice a second even with the window
      hidden, the badges next to the circle were being restyled
      twice a second to say the same thing, and a channel that
      cannot be read was writing two lines a second into the log
      for ever. All three now do nothing when nothing has changed.
    - A sound on a speaker that stops answering can no longer
      corrupt the program's memory.
    - A stuck reading can no longer take the other half with it.
      The numbers and the pictures have a thread each now, so one
      of them hanging leaves the other working.
    - Remove takes everything you picked. Ctrl-click or Shift-click
      as many rows as you like, right-click them and choose Remove
      once; it names them before it does anything. Before, picking
      more than one row greyed out every button, which looked like
      the program had lost the selection.

  KNOWN, and left as it is on purpose:

    - Clicking the alarm dismisses it only where the picture is
      actually drawn. The window has the shape of the picture, so a
      click in a see-through part goes to whatever is behind it.
      Esc always works. Say if you would rather the whole rectangle
      took the click.


=================================================================
9. IF SOMETHING LOOKS WRONG
=================================================================

  Nothing is being read at all
    Look at the log. "Archiver unreachable" means this PC cannot get
    to it — the network or the VPN. Every failure in the log carries an
    explanation of what it means and what to try.

  One value says "nothing archived in the window"
    The channel name is probably not one the archiver writes. Open the
    row, press Find, and search for it by a few words.

  A channel name is refused outright
    The archiver answers a name it does not know with a refusal, not
    with an empty answer, and the log says "Channel name refused".
    Check the spelling.

  An area fires as soon as watching starts
    Either the screen really did change, or its resolution or scaling
    changed after the reference was taken. The message says which,
    with both sizes. Take the picture again.

  An area never fires
    Check it has a reference picture — the Reference column says NO
    PICTURE in red when it has not. Without one there is nothing to
    compare against, and the program says so when watching starts.

  No sound
    Press "Test the sound" on the Alarm tab. It says what happened.
    If the lab is silent but the PC is not, the speaker is the thing
    to change: name it under "Play it on". If a Bluetooth speaker
    swallows the start of the sound, raise the Bluetooth run-up.

  The alarm flashes somewhere useless
    "Place the alarm window" on the Alarm tab.

  The circle is in the way
    Drag it. Or "Put the circle back in the corner". The space around
    it is click-through, so it should not be able to get in the way of
    a click.

  It flashed and I cannot make it stop
    Esc. Always.

  The program disappeared
    It writes a record of its own death now:

      C:\Users\<you>\AppData\Local\Announcer\announcer_crash.log

    Send that file with the report. It holds the start of every run,
    Qt's own complaints, and — if the program was killed outright
    rather than closing — the exact place in the code it died. Before
    this, a crash left nothing behind anywhere except in Windows'
    event log, which says only "access violation" and nothing about
    where.

    The file is kept to about 2 MB; the previous one is beside it as
    announcer_crash.log.1.

  The screens went dark and there is no window anywhere
    That is the area picker, not a crash. Press Esc, or click once
    anywhere, and everything comes back. If you do nothing it gives
    up by itself after two minutes.
