SPFE VALUES
===========

The daily table of SPFE numbers, filled in by itself.

Every working day a fixed set of values - Pumplaser DAC, humidity, oscillator
power and bandwidth, the amplifier and XPW loop positions, the Dazzler settings
- is written down once in the morning and once at the end of the day. This
program reads those numbers from the archiver and keeps the table for everybody
in one workbook in the shared folder.


WHAT IS ON SCREEN
-----------------

One page: the buttons down the left, the day on the right. There are no tabs -
this table is going to become one page of the Chiller Log program, and a set of
tabs inside another program's tabs is what nobody wants to click through.

The buttons on the left
  "Record now" at the top, then a coloured section for each job - Scheduled
  columns, Day, Earlier days, What is acceptable, Shared folder - the same
  sections the Image Tools programs use. Click a section's coloured bar to fold
  it away; "Expand all" and "Collapse all" do the lot. Which ones you leave open
  is remembered for next time. "Log" sits on its own at the bottom, away from
  the buttons that do something.

The grey strip with the date
  Which day is on screen, and beside it the campaign. Type what the day is
  working on into the Campaign box and it stays there - and on every day after
  it - until somebody types something else. A name carried over from an earlier
  day is shown in grey; the day's own name is in black. It also stands beside
  the date in the workbook, so whoever only opens the Excel file sees it too.

The table
  One day, exactly as it is written into the workbook. The left two columns are
  the group and the Detail - the name of the quantity. Then the recorded
  moments: Morning (09:00), At the end (18:00), and any extra moment somebody
  recorded, headed with its time.

  One line per quantity, and the numbers of a quantity stand SIDE BY SIDE, the
  way the paper form has always had them. The heading is two levels: the name
  of the moment across the top, and under it X, Y and SUM. A quantity that is
  one number stands across all three.

    ┌───────────┬──────────────────────────┬──────────────────────┐
    │           │                          │    Morning (09:00)   │
    │           │ Detail                   ├──────┬──────┬────────┤
    │           │                          │  X   │  Y   │  SUM   │
    ├───────────┼──────────────────────────┼──────┴──────┴────────┤
    │ SPFE      │ Pumplaser DAC            │        24500         │
    ├───────────┼──────────────────────────┼──────┬──────┬────────┤
    │ XPW       │ Input - BA2Loop2 (X,Y,SUM)│ 150 │ -250 │  2500  │
    └───────────┴──────────────────────────┴──────┴──────┴────────┘

  Which number is which is also written in the name of the quantity, in
  brackets: "Input - BA2Loop2 (X,Y,SUM)".

  The hour in the heading is the hour the values are read at: for each quantity
  it is the last reading the archiver holds before that time.

  The unit stands in the Detail column, in brackets. A value cell holds the
  number and nothing else, so that is all there is to type.

  The columns are only as wide as what is in them, and the empty column on the
  right is there so the table still reaches its own frame. Make the window
  narrower and the table follows.


COLOURS IN THE TABLE
--------------------

  Red, with the number in white
    The value is outside the range somebody set for it in References. Hold the
    mouse over it and it says which edge it went past.

  Yellow, with the number in black
    The value is still inside its range but close to an edge - within 5 % of
    the width of the range, unless that number was given its own percentage.
    This is the one that gives you time to do something about it.

  Pale amber, with the number in red
    The value does not fit the previous days. This one needs no setting up: it
    compares the number with the same hour on the days before it. It cannot
    catch a quantity that has been drifting for a fortnight, which is exactly
    what a reference is for - so a reference always wins the colour, and this
    remark joins its tooltip underneath.

  Pale cream
    "This one is yours to fill in." The cells the paper form expects somebody
    to type into: the Spider settings, the best GDD, the energy and the free
    text at the bottom. The numbers that hardly ever change are already there.
    A moment nobody has recorded yet is plain white, cream or not - there is
    nothing to type over until the day has been read.

  The number is always kept. A colour is a warning, never a deletion.

  Every colour belongs to ONE number. If the SUM of an XPW loop is out of
  range, only the SUM turns red; X and Y beside it stay white. The workbook
  does the same.


TYPING INTO THE TABLE
---------------------

  Every cell can be typed into, including the ones the archiver filled in - if
  a number came out wrong, correct it. Click into it, type, then press Enter or
  click anywhere else: it is saved by itself, for that day only. There is no
  Save button.

  A cell is one number, so correcting the Y of an XPW loop leaves X and SUM
  exactly as they were. Emptying a cell clears that number and nothing else.

  The numbers are shown rounded, because that is how they are read: X and Y to
  the nearest ten, SUM to the nearest hundred, the Dazzlers to whole percent.
  The log file always keeps the exact measured value.

  Typing into a column that says nothing was recorded - every column of a day
  the archiver cannot answer for - writes that day down by hand.

  Right-click an extra column and you can delete it. Morning and At the end
  cannot be deleted; to read one again, press Morning or At the end.

  "Energy" is written in blue and underlined: clicking it opens the shared
  energy comparison sheet in the browser.


THE BUTTONS
-----------

Record now
  Reads every PV at this moment and adds a column headed with the time. Use it
  at any time of day - that is what it is for.

< Previous / Next > / Today
  Which day the table shows.

View day
  A window listing every day that has been recorded, with the table beside it.
  "Show this day in the table" brings the picked day back to the main page.

Morning / At the end
  Fills the shown day's scheduled column by hand. The values are the ones the
  archiver holds for 09:00 and for 18:00.

Fill missing days
  Fills in every working-day morning and evening that is missing, back to the
  last recorded day. This is why nothing has to be running at 09:00: the
  archiver is asked afterwards what the value was then. A weekend is skipped,
  and a moment that has not happened yet is not invented.

Fill this day
  The same thing for the one day on screen, however long ago it was. Use it
  with < Previous to walk back into days the log does not have yet - "Fill
  missing days" cannot reach them, because it starts at the newest day that is
  already recorded.

Pick days
  A calendar: click a day, Ctrl+click to add one, Ctrl+Shift+click for a
  stretch of days, then OK - and all of them are filled in. A day picked by
  hand is filled in even if it is a Saturday.

References
  The range each number is allowed to be in. One line per number, with a
  Lowest and a Highest box; leave both empty and that number is never coloured.
  A value outside its range is red, a value close to an edge is yellow.

  "Yellow when within 5 %" at the top of the window says how close counts. It
  is 5 % of the width of the range - so 5 % of a range of 100 to 200 is five
  either side. A number that wants a different one gets it in the last column
  of its own line. With only one edge set, the percentage is taken off that
  edge instead.

  The ranges are shared: they are written beside the log in the shared folder,
  so everybody's window colours the same values. The day on screen is recoloured
  the moment you press Save - a range typed today is applied to the days that
  are already recorded, it is not only for what comes next.

Sync now
  Sends anything still waiting on this PC to the shared folder: values, changed
  numbers, campaign names, deleted moments, references, and any day the workbook
  is missing a block for.

Open folder
  Opens the shared folder in Explorer.

Log
  What happened: which PV did not answer, which day was filled in, why a write
  had to wait. The first place to look when something is missing.


WHERE THE VALUES GO
-------------------

  <shared folder>\SPFE Values\SPFE values.xlsx   the table people read
  <shared folder>\SPFE Values\spfe_log.csv       the same numbers, one row per
                                                 moment, for the program

The workbook has three sheets:

  SPFE values   the days, one block each, exactly the table that is on screen -
                X, Y and SUM in three cells side by side, coloured per number.
  Trends        the same numbers laid out for charting: one line per DAY, one
                column per number and per moment, so the morning and the end of
                the day are two columns of the same quantity.
  Charts        a chart per group - 1st amplifier, XPW, the Dazzlers, the best
                GDD and so on - showing how each number ran day by day, with the
                morning and the end of the day as two curves.

The Trends and the Charts sheets belong to the program: they are thrown away and
drawn again on every save, so they can never show yesterday's picture. Do not
type on them, and do not build a chart of your own there - it would be gone
after the next recording. The day blocks are a different matter: those are only
ever added to, and a note typed into one straight from Excel stays.
  <shared folder>\SPFE Values\spfe_campaigns.json    which day is which campaign
  <shared folder>\SPFE Values\spfe_tombstones.json   the moments somebody deleted
  <shared folder>\SPFE Values\spfe_references.json   the range each number is
                                                     allowed to be in

A copy of every one of them is always kept on this PC first, so a shared folder
that is down, or a workbook somebody has open in Excel, only delays the values -
it can never lose them. The status line at the bottom says when something is
waiting, and Sync now sends it.

The workbook is only ever added to. A new day is appended below the last one and
a new moment fills its own column, so anything typed straight into Excel stays
where it was put. A deleted moment has its column emptied, and nothing else in
the file moves.


BEFORE IT CAN SHOW EVERYTHING
-----------------------------

Five quantities still have no PV: Pumplaser DAC, humidity, oscillator power,
oscillator bandwidth and DAZZ2. The archiver has no channel with pump, dac,
humid, osc or dazz in its name, so somebody has to point at the right ones.
Open spfe_fields.json next to the program and put the PV name into the "pvs"
list of the quantity. Until then those cells show a dash; everything else works.

Two more things in the same file are worth a look:
  - DAZZ1 reads L3-SPFE-AOD03-002 Power_RB. That is the only dazzler the
    archiver knows. A dazzler has one percentage, not two: the second number
    the table used to show, labelled "intensity", was a guess and is gone.
  - The Energy row has no unit yet. Put it in its "unit" and it appears in the
    Detail column.

Also in that file:
  - "round" is the step each number is shown to - 10, 100, 1. Change it there,
    not in the program.
  - "default_from" on the Spider settings and the best GDD is the first day
    those numbers count for. Days before it are left empty, because nobody
    wrote those settings down then; they can still be typed in by hand. Take
    the line out and the numbers apply to every day again.

The two recording times, the working-days-only rule and how strict the
out-of-range check is are all in spfe_config.json.


Detailed description: ReadMe_SPFE Values_Full.txt
