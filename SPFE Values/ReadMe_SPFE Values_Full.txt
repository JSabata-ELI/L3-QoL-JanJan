SPFE VALUES - DETAILED DESCRIPTION
=================================

The short version is in ReadMe_SPFE Values.txt. This file explains why the
program behaves the way it does, and what to do when it does not.


1. WHAT IT REPLACES
-------------------

A table filled in by hand every working day: rows are the quantities, columns
are Morning and At the end, and a Notes block at the bottom holds the Spider
settings, the GDD numbers and whatever happened that day.

The program fills in the numbers that come from PVs. It does not fill in the
notes - nobody can read those off an archiver - but it gives you somewhere to
type them so the whole day lands in one place.


2. NOTHING RUNS IN THE BACKGROUND
---------------------------------

There is no service, no scheduled task, nothing that has to be left running at
09:00. The archiver keeps every value, so the program asks it afterwards what a
PV read at 09:00 - the same way Chiller Log works.

Two consequences:

  - A day is never lost because nobody had the program open. Press
    "Fill missing days" whenever you like and the gap fills in.
  - If the program does happen to be open when a recording time passes, it
    records straight away, so you can type the notes while the numbers are
    still in front of you.

The catch-up goes back at most 30 days (catch_up_max_days). That is a guard, not
a limit on the log: it stops a first run on an empty file from trying to
reconstruct a year.

"Fill missing days" only ever looks forward from the newest day already
recorded, which is why it can never bring back a day older than the log. That
is what the two buttons under "Earlier days" are for:

  Fill this day   both columns of the day on screen, however far back it is.
                  Walk back with < Previous and press it.
  Pick days       a calendar. Click a day, Ctrl+click to add another,
                  Ctrl+Shift+click for a stretch, OK - and all of them are
                  asked for, oldest first.

They ask the archiver exactly the same question as the catch-up, at the same two
times of day, and skip anything that is already in the log. How far back they
can reach is up to the archiver: a PV that was not being archived yet answers
with nothing, and the Log window says so.

Weekends are skipped by the catch-up, because the value of "Saturday morning" on
a machine nobody was running is noise. Setting weekdays_only to false in the
config brings them back everywhere - and a day picked by hand is always filled
in, weekend or not, because somebody deliberately clicked on it.

The recording times themselves are on screen, in the column headings: "Morning
(09:00)" and "At the end (18:00)". The value under such a heading is the last
reading the archiver holds BEFORE that time, per quantity - not an average, and
not necessarily a sample taken at 09:00:00.


3. WHEN A PV DOES NOT ANSWER
----------------------------

The quantity's number stays empty, the Log window says which PV it was, and
everything else in that column is still recorded.

This is deliberately unlike Chiller Log, where a single dead PV aborts the whole
update and every value already fetched is thrown away. Here one bad PV costs one
cell.

If a whole column comes back empty, nothing is recorded at all and the status
line says so - an all-empty row in the log would look like a real measurement of
nothing.

Note that a frozen sensor cannot be caught this way. The archiver keeps handing
out fresh timestamps for a PV that stopped changing months ago; only the
repeating value gives it away, which is what section 4 is for.


4. "THESE VALUES ARE OFF"
-------------------------

Every number is compared against the same slot on the previous working days -
mornings with mornings, evenings with evenings. The two are genuinely different
operating points, and mixing them would widen the spread until nothing ever
looked wrong.

The rule:

  - the last 20 records of that slot                (history_days)
  - at least 8 of them, or no verdict at all        (min_history)
  - the middle value (median) and how far the days
    normally sit from it (MAD)
  - flagged when the value is more than 5 times
    that normal distance from the middle            (mad_factor)

The median is used rather than an average because one bad day would drag an
average - and inflate the spread enough to hide the next bad day.

The one case that needs its own rule is a setpoint. Pumplaser DAC reads exactly
24500 every day, so the normal distance is zero and any change at all is
infinitely far out. When the history is flat like that, the value is instead
flagged only if it is more than 20 % away (flat_tolerance_pct). A one-count
change is not an alarm; a halving is.

For the first weeks there is not enough history and nothing is flagged. That is
on purpose - a program that cries wolf on day three gets ignored by day ten.

A flagged value is never deleted or refused. It is written, the cell goes amber
with the reason on it, and the reason also goes into the Log window. If you decide
it was real, nothing needs doing: as the following days record the same value
the history moves with it and the flag stops appearing.

An extra "record now" column has no history of its own, so it is judged against
whichever scheduled slot it is nearer to.

There is a second, quite different judge beside this one - see section 5.


5. REFERENCES: WHAT THE NUMBER IS ACTUALLY ALLOWED TO BE
--------------------------------------------------------

Section 4 asks "is this unlike the other days?". It needs no setting up, and it
has one blind spot that matters: a quantity drifting slowly out of spec over a
fortnight is never flagged, because by then the drift IS the history.

A reference is the other half. Somebody says what a number is allowed to be,
and every value is measured against that - today's and every day already
recorded. The References button opens a window with one line per number:

  Lowest / Highest    the range. Either may be left empty: "at least 600" and
                      "at most 42" are perfectly ordinary things to want. Both
                      empty means this number is never coloured, which is where
                      every number starts.
  Yellow within %     how close to an edge still counts as close, for this one
                      number. Empty means the percentage at the top of the
                      window, which is 5 % and is stored in spfe_config.json as
                      warn_pct.

The three verdicts, and what they look like:

  outside the range   red cell, the number in WHITE with a black outline round
                      it. The outline is drawn by hand rather than left to the
                      item's own colour: plain white on that red is exactly the
                      pair that turns into a grey smudge on a projector, on a
                      printout, and on a screen somebody has turned down.
  near an edge        yellow cell, the number in black and bold.
  inside              no colour at all.

"Near an edge" is a percentage of the WIDTH of the range: 5 % of 100-to-200 is
five, at both ends. With only one edge set there is no width, so it is 5 % of
that edge itself - 5 % below a minimum of 24 500 is 1 225. A minimum typed
above the maximum is swapped rather than refused; it is what happens when the
two boxes are filled in the order they come to mind.

A reference is somebody's decision about the machine, so it wins the colour of
the cell over the statistical remark of section 4 - that remark is only "this
differs from the other days", and it joins the same tooltip underneath.

The ranges live beside the log, local copy first and then the share, and are
merged number by number exactly as the campaign names are: a range set on the
lab PC and one set here are two lines of the same table, and the newer wins only
where they collide. Nobody can wipe somebody else's line by saving their own.

The workbook echoes the verdict: an out-of-range cell is filled red with white
bold text, a near-edge cell yellow with black, and the reason is in the cell's
note. Every number has a cell of its own there too, so the colour belongs to one
number: an XPW loop whose SUM is out of range turns the SUM red and leaves X and
Y white beside it. Until the numbers were split the workbook had one cell for
all three and had to take the colour of the worst of them, which made two good
numbers look bad.


6. TYPING, CORRECTING AND DELETING
----------------------------------

Every cell of a recorded column can be typed into - the ones the archiver
filled in as well. If a number came out wrong, correct it. The cells with the
pale cream background are the ones the paper form expects somebody to fill in
anyway: the Spider settings, the best GDD, the energy and the notes.

There is no Save button. A cell is written the moment its editor closes - Enter,
Tab, or a click anywhere else - and the status line says so. What that costs is
worth knowing:

  - A cell you open and close without changing anything writes nothing. This is
    what protects a measured value from the rounded number the cell shows.
  - Several cells typed one after another are written together, about half a
    second after the last one, so a whole day of corrections is one write to
    the share instead of twenty.
  - If the write fails, the value STAYS on screen and in the queue: it is
    already in the copy on this PC, and "Sync now" finishes the job. Putting
    the old text back would be the one way to actually lose it.
  - Closing the program mid-typing is safe. The open cell is committed, the
    queue is written to disk, and the next start sends it.

A quantity made of several numbers - BA1Loop4 is X and Y, the XPW loops are X,
Y and SUM - is ONE LINE with the numbers SIDE BY SIDE, each in a cell of its
own. That is the shape of the paper form the table replaces. Typing into the Y
cell changes Y and nothing else, and emptying it clears Y and nothing else.

It was one cell holding "150;-250;2500" until 2026-09-03, and that had two
problems worth remembering: nobody could read it at a glance, and correcting Y
meant retyping all three with the separators in the right places. For a while
after that each number had a LINE of its own, which fixed both - but it turned
thirteen quantities into twenty-two lines and no longer looked like the form
anybody was copying from. Side by side is the third and final shape.

Every recorded moment is therefore as many columns as the widest quantity has
numbers - three, today - and the heading has two levels: the name of the moment
across its own columns, and under it X, Y and SUM. A quantity that is one
number lies across all three, so there is no empty box beside it.

The names X, Y and SUM come from two places at once, on purpose:
  - the second heading line, which can only speak when every multi-number
    quantity agrees on the name at that position (they do: X, Y, SUM);
  - the quantity's own label in spfe_fields.json - "Input - BA2Loop2
    (X,Y,SUM)" - which is shown in full in the Detail column and is what says
    which is which if ever they stop agreeing.

The workbook has exactly the same shape, cell for cell.

The editor takes one number and nothing else: a comma is refused, because the
log has to stay something that can be added up.

Typing into a column of a day that was never recorded - an empty Morning or At
the end, whose heading says as much when the mouse rests on it - writes that day
down by hand. This is how a day the archiver cannot answer for still gets into
the table.

An extra "record now" column can be deleted: right-click it and confirm. It
goes from both logs and its column in the workbook is emptied. Morning and At
the end are the shape of the day and cannot be deleted - to read one again,
press Morning or At the end, which overwrites it. Nothing in this program can
undo a delete.

The campaign box in the grey strip works the same way: type a name, leave the
box, and it is saved for that day and carried on to the days after it.


7. THE FILES
------------

  <share>\SPFE Values\SPFE values.xlsx        the table, one block per day
  <share>\SPFE Values\spfe_log.csv            one flat row per moment
  <share>\SPFE Values\spfe_campaigns.json     which day is which campaign
  <share>\SPFE Values\spfe_tombstones.json    the moments somebody deleted
  <share>\SPFE Values\spfe_references.json    the range each number is allowed
                                              to be in
  %APPDATA%\SPFE_Values\...                   a copy of all of them on this PC
  %APPDATA%\SPFE_Values\pending_edits.json    changes not yet on the share

The workbook is what people open. The CSV is what the program reads - the
statistics need the numbers, and reading them back out of a workbook that people
edit by hand would be unreliable.

Why a local copy exists: every measurement is written there first, where nothing
can fail. Only then is it pushed to the share. A shared folder that is down, or
a workbook open in Excel, therefore delays the values instead of losing them.
The status line goes red and "Sync now" sends them once the way is clear; so
does the next start.

A value you type is a change to a row that already exists, not a new row, so it
needs its own queue: pending_edits.json holds it until it reaches the share, and
"Sync now" replays it. Without that a correction typed while the share was down
would have stayed on this PC for ever, because the sync only ever looks for rows
the share is MISSING.

The two side files are merged day by day, not "whichever file is longer": two
people on two PCs each own the days they typed on, and neither can wipe the
other's. A campaign name is stored only for the day somebody typed it on and
carried forward from there when the table is drawn, so correcting one day
corrects every day that follows it. An emptied name is stored as an empty name -
that is how a campaign is ended - and an entry is never deleted, or the other
copy would put it straight back.

Deleting a moment writes a note into spfe_tombstones.json first, before
anything is removed. That order is the whole trick: the two logs are compared by
length, so a row deleted from one of them and not the other would simply come
back. With the note written, the moment is invisible everywhere at once and the
rest - both logs and the workbook column - can be finished by the next sync.

The workbook has three sheets, and only the first of them belongs to people:

  "SPFE values"  the days, one block each. This is the sheet that is only ever
                 added to and that a note typed by hand survives in.
  "Trends"       the same numbers laid out so a chart can point at them: one
                 line per DAY, one column per number and per moment.
  "Charts"       one line chart per group, drawn from the Trends sheet.

The Trends and Charts sheets are thrown away and written again on every save.
They have to be: the day blocks cannot be charted at all, because each day is
its own block with a blank line between, so one quantity across thirty days is
thirty separate cells and not a range - and a chart can only point at a range.
The price is that a chart somebody builds there by hand is gone after the next
recording. That is stated in the operator ReadMe as well.

The morning and the end of the day are two COLUMNS of the same quantity on the
Trends sheet, so each chart draws them as two curves and the drift through a day
is visible. Free-text rows are left out; everything numeric is in, the settings
typed by hand included - "when did the GDD change?" is exactly what that sheet
answers.

The day sheet itself is only ever added to:

  - a new day is appended as a block below the last one,
  - a block is TWO heading lines: the moment's name across the columns it owns,
    and under it the name of each number (X, Y, SUM),
  - every moment is as many columns as the widest quantity has numbers, so
    Morning is C:E, At the end is F:H and the extra moments follow,
  - Morning and At the end always sit in the same place, so the days line up
    when you scroll,
  - a "record now" moment takes the room to the right of the last one used; no
    column is ever inserted, because inserting one would shift every other day
    on the sheet,
  - a deleted moment has its own columns emptied, and the gap stays a gap so the
    times inside a day stay in order,
  - the campaign stands beside the date, in the day's header row.

Anything typed straight into Excel therefore stays where it was put. The one
exception is a cell you edit through the program - that is you correcting your
own value, and only the cells you changed are rewritten.

A block written before the numbers were split - one heading line, and
"150;-250;2500" in a single cell - is REFUSED rather than written into. Values
go into a block by position, so writing into an old block would put every number
one line too high and under the wrong heading. The Log says so and names
testing/rebuild_workbook.py, and the values are in the CSV either way.

If the workbook is open in Excel when a whole run of days is written, each day
is now attempted on its own: one refused day no longer abandons the rest, and
"Sync now" writes a block for any recorded day the workbook does not have.

A day that is added late lands at the bottom, because that is what append-only
means. On 3.9.2026 twenty days of August had to be added at once, so the file
was written again from the log with the days back in date order - by hand, with
testing/rebuild_workbook.py, after checking that every value in it could be
reproduced from the log. The old file is kept in
%APPDATA%\SPFE_Values\SPFE values.before-rebuild-*.xlsx. If it ever has to be
done again, run that script with --diff first.

The same script rebuilt the file a second time the same day, when the numbers
were split into cells of their own: the two layouts cannot be mixed, so the
whole sheet had to be written again. The 192 differences --diff reported before
that run were all of two harmless kinds - the separator inside a joined cell had
already changed from ";" to " ; ", and DAZZ1 had lost the second number it never
had - and after the rebuild --diff reported none. That is the check to repeat:
run --diff before AND after.

The DAZZ1 change also moved a column in the log, from dazz1_1 to dazz1. That was
done by testing/migrate_dazz.py, once, on both copies of spfe_log.csv, with a
backup of each beside it.


8. THE SHARED FOLDER, AND THE 48-SECOND TRAP
--------------------------------------------

The scratch share answers to two names, and they are two different machines:

  \\hapls-share.cs.eli-beams.eu\scratch\Software    reachable from the office
  \\hapls-share.lcs.local\scratch\Software          reachable from the lab

From an office PC the lab name is not merely absent - a single check on it
blocks for about 48 seconds. The program therefore asks both at once, on
throwaway threads, and takes the first name in a fixed priority order as soon as
it is known to answer. The name that worked is remembered, so the next start
costs one check of a machine that is known to be there.

If neither answers, the program says so in the "Shared folder" box and keeps
working on this PC alone.

To force a particular folder - a test folder, or a different share - put its
path into "share_root" in spfe_config.json.


9. SETTINGS - spfe_config.json
------------------------------

  slot_morning         "09:00"   when the morning column is taken
  slot_evening         "18:00"   when the end-of-day column is taken
  weekdays_only        true      skip Saturday and Sunday
  share_root           ""        force a folder; empty = probe the two names
  share_subdir         "SPFE Values"
  share_probe_timeout_s 3.0
  history_days         20        how many past days the check looks at
  min_history          8         fewer than this and nothing is flagged
  mad_factor           5.0       how many normal distances is "off"
  flat_tolerance_pct   20.0      the same, for a value that never changes
  warn_pct             5.0       how close to the edge of a reference is
                                 "nearly out" - set from the References window
  http_timeout         10.0      one archiver request
  catch_up_on_start    true      fill in missing days when the program opens
  catch_up_max_days    30        how far back a catch-up will go


10. THE QUANTITIES - spfe_fields.json
------------------------------------

This is where a quantity is added, renamed or given its PV. No code changes.

Each row is:

  key       never changes - it is the column name in the log file
  label     what stands in the second column of the table
  pvs       one PV name per number in the cell. An empty string means "not
            filled in yet" and the cell shows a dash.
  join      what separates the numbers of a multi-number row, ";" by default.
            Only the log uses it now: on screen and in the workbook every
            number has a cell of its own.
  unit      shown in the Detail column after the label, in brackets. It is
            never in the value cell on screen, so the only thing that has to be
            typed into a cell is the number. The workbook still keeps the unit
            in the cell, because a spreadsheet column has no label beside it.
  decimals  how many decimal places
  round     the step the number is shown to: 10 to the nearest ten, 100 to the
            nearest hundred, 1 whole numbers. One entry per PV - the XPW rows
            read [10, 10, 100], so X and Y are shown to tens and SUM to
            hundreds - or a single number for the whole row. An exact half goes
            away from zero, so 365 shows as 370 and -365 as -370.
  manual    "text" or "number" - typed by the operator, not read from a PV
  default   what a manual row shows until somebody types over it. It also goes
            into the record when a moment is recorded, so the files say what the
            screen says. Typing over it changes that one day, nothing else.
  default_from  the first day that default counts for. Earlier days show an
            empty cell and nothing is written into the files for them.
  url       makes the label a link; clicking it opens the address in a browser
  check     false switches the out-of-range check off for that row

Rounding is what is SHOWN, on screen and in the workbook. The log file always
keeps the measured value, so the out-of-range check still sees the real spread -
and correcting a cell by hand cannot quietly replace a measurement with its
rounded self, because a cell you open and close without changing anything is
not written at all.

The prefilled numbers are the ones that hardly ever change and that everybody
kept writing out by hand: the two Spider settings and the three best-GDD values.
They are in this file, so correcting them for good is a one-line edit rather
than a habit.

They also carry "default_from": "2026-09-01", the day they were written down.
Fill in an older day and those five rows stay empty rather than claiming
settings that nobody had agreed on yet; they can still be typed in by hand for
that day. Take the line out and the numbers apply to every day again.

A row can hold several PVs, because the paper table does: BA1Loop4 is X and Y,
BA2Loop2 is X, Y and SUM. They are stored, shown, coloured and checked
separately, so "X drifted" is caught even when Y is fine and only X is marked.
The names come out of the label - write it as "BA2Loop2 (X,Y,SUM)" - and they
also become the second line of the table's heading.

Two rows must never share a key. The program refuses to start rather than let
one quietly overwrite the other in the log.

Taking a number AWAY from a row moves a column in the log. A row of two PVs is
stored as key_1 and key_2; the same row with one PV is stored as key. So when
DAZZ1 lost its second number on 3.9.2026, everything already recorded under
dazz1_1 would have been orphaned - the log would still hold it, the table would
no longer look for it. testing/migrate_dazz.py renamed the column in both copies
of the log, with a backup of each. Any future change of this kind needs the same
kind of one-off script; the program cannot guess which old column became which
new one.


11. WHEN SOMETHING IS WRONG
---------------------------

Some value cells show a dash
  Those quantities have no PV yet: Pumplaser DAC, humidity, oscillator power,
  oscillator bandwidth and DAZZ2. See section 10.

"nothing recorded" and an empty table
  Nothing has been recorded for that day. Press "Fill this day"; for a run of
  days, "Pick days". "Fill missing days" only reaches days newer than the last
  one in the log.

An older day fills in but the Spider settings and the GDD stay empty
  That is deliberate: those five numbers only count from the day they were
  written down ("default_from" in spfe_fields.json, section 9). Type them in if
  you know what they were.

The Spider settings and the GDD are empty on days they should apply to
  Their "default_from" is later than that day. Change or remove the line.

"... was deleted earlier - it is not recorded again"
  Record now was pressed in the same minute as a moment that has been deleted.
  The note that deletes a moment is that exact minute, so wait a minute and
  press it again.

The campaign box shows a grey name
  Grey means the name was carried over from an earlier day. Type over it and
  this day gets a name of its own; empty it and the campaign ends here.

The status line is red about Excel
  The workbook is open. The values are already saved - close Excel and press
  "Sync now", or just wait for the next recording.

The status line says neither share name answered
  Working on this PC only. The values are safe; press "Sync now" once the share
  is back.

A value looks wrong but is not amber
  Either there are fewer than 8 previous days of that slot, or it is within 5
  normal distances of them. Lower mad_factor if the check should be stricter.

Too many amber cells
  Raise mad_factor, or raise flat_tolerance_pct if the complaints are about
  setpoints that changed on purpose.


12. WHERE IT IS GOING
---------------------

The whole window is one widget and one page - no tab bar of its own - so it is
meant to become an "SPFE values" page inside Chiller Log, the sibling program
that keeps the same kind of slow daily log for the chillers. That program will
be renamed when the two are joined. Until then this one is not built and not
published, so it does not appear in the Launcher.

That is also why it looks the way it does: Segoe UI 9, a grey table header on
white cells, plain Windows buttons, and Chiller Log's four colours - blue for
"working on it", green for done, red for wrong, grey for neutral. The one thing
that has to be translated rather than copied is the toolkit: Chiller Log is
written in tkinter and this is Qt, so the two share a look, not any code.

What is left for the merge: this widget is Qt, and Chiller Log's window is not.
Joining them means one of the two changes toolkit, which is a job of its own.
