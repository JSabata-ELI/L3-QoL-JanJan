Time Converter — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (tc.py, 592 lines)
-----------------------------------------------------------------
Short version: Readme Time Converter.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS FOR
=================================================================

The camera archive stores every image with a machine timestamp at
the end of the filename — the number of nanoseconds since the first
of January 1970. A stored file looks like this:

    C03-015-PD1M1DF-_-IMG_-_1746262710366145024.png

That is exact and completely unreadable. This tool copies files out
to a folder of your choice and renames the copies so the time is
plain:

    C03-015-PD1M1DF_IMG_2026_05_03--08_18_30__366145.png

The tool only ever writes copies. The archive is opened read-only
and nothing in it is renamed, moved or deleted.


=================================================================
2. THE NAME FORMAT
=================================================================

The new part of the name is always exactly this shape:

    YYYY_MM_DD--HH_MM_SS__ffffff

  YYYY_MM_DD    year, month, day, separated by underscores
  --            two hyphens between the date and the time
  HH_MM_SS      hours, minutes, seconds, 24-hour clock
  __            two underscores
  ffffff        six digits of fractional seconds (microseconds)

Underscores and hyphens are used instead of colons and spaces
because a Windows filename cannot contain a colon, and because a
name without spaces survives being pasted into a console, a script
or a report without needing quotes. The shape also sorts correctly
in Explorer: alphabetical order is chronological order.

The three nanosecond digits below a microsecond are dropped. In
practice that does not matter — no camera in the archive resolves
anything close to a microsecond.

Everything in front of the number is kept as the prefix, so the
camera name and the rest of the label stay in the new name.

One piece of tidying happens first: the archive tends to produce
runs like "-_-" and "_-_" between name parts. Those are collapsed to
a single underscore before the name is rebuilt.


=================================================================
3. UTC OR PRAGUE TIME
=================================================================

The stored number is an absolute instant, with no timezone in it.
It has to be rendered against some clock, and the tool offers two:

  Prague local time (the default)
      What the clock on the wall said when the shot was taken.
      This is what you want when you are matching an image to a
      shift log, an e-mail or somebody's memory of the day.

  UTC (switch the checkbox off)
      What the archive itself uses internally. This is what you
      want when you are matching an image against the archive's
      own folder structure, whose hour folders are UTC.

The choice affects only the new name. The original number is
unchanged, and the preview table shows the UTC rendering of the
original in its own column no matter which mode you picked, so you
can always see both.

Prague time means real Prague time, with summer time handled
properly — the conversion uses the Windows timezone database, not a
fixed offset.


=================================================================
4. WHAT GETS SKIPPED, AND WHY
=================================================================

There are exactly three reasons a file is skipped, and the preview
table names the reason for every one:

  already converted
      The name already ends in the readable format. This is what
      makes the tool safe to run a second time over the same
      folder — a converted file is recognised and left alone
      instead of having a second date appended.

  no trailing number
      There is no number at the end of the name at all, so there is
      nothing to convert.

  invalid timestamp
      There is a number, but read as nanoseconds it lands outside
      the first of January 2000 to the first of January 2100. That
      is almost always a serial number, a shot counter or a
      resolution that happens to sit at the end of the name. The
      tool refuses to invent a date from it — a skipped file is
      better than a file dated 1970 or 33658.

A skipped file is not an error. Nothing is written for it, and the
run continues.


=================================================================
5. THE FOUR WINDOWS
=================================================================

5.1 OPTIONS
    Two checkboxes, both on by default:
      - Convert to local time (Europe - Prague)
      - Show detailed report after copying
    Above them is a short reminder of the four steps. OK continues,
    Cancel closes the tool.

    Turning the report off replaces both the preview table and the
    result table with a single message box. Use that when you are
    copying a few hundred files and do not want to read a list.

5.2 FILE PICKER
    Opens directly in the camera archive on the network. Select one
    file, several with Ctrl or Shift, or everything in the folder
    with Ctrl+A. There is no folder picker for the source — the
    selection is always a list of files.

5.3 TARGET FOLDER
    Opens in your Documents folder. The copies land directly in
    whatever folder you pick; no sub-folders are created.

5.4 PREVIEW
    A table with one row per file and five columns:

      Prefix              everything in front of the timestamp
      Original timestamp  the raw number as it stands in the name
      Orig. UTC           that number rendered in UTC
      New name            the name the copy will get
      Status              what will happen

    Above the table, a row of coloured boxes counts what is about
    to happen: how many will be copied, how many will overwrite
    something, how many will be skipped.

    The row colours are:
      green   copy — nothing of that name in the target folder
      blue    overwrite — a file of that name is already there and
              will be replaced without a further question
      grey    skip, with the reason in the status column
      orange  unusual, worth a look
      red     error

    Proceed starts the copying. Cancel goes back to the options
    window with nothing written.


=================================================================
6. THE COPY RUN
=================================================================

Four files are copied at the same time. A network share answers
several requests in parallel much better than one at a time, and
four is the point where more workers stop helping.

The progress window shows the count, the percentage, the time spent
so far and, from the third file onwards, an estimate of the time
left. For runs of twenty files or more the estimate is smoothed, so
it settles down quickly and reacts if the share slows instead of
jumping about.

Cancel stops the run. Files already copied stay where they are, work
that had not started yet is dropped, and the report lists what was
done before the stop and marks the run as cancelled. Up to four
files may already have been in flight when you pressed it, so the
count can end one or two higher than what you saw on the bar.

Timestamps on the copies: the modification time is carried over from
the original, so sorting by date in Explorer still works even for a
file whose name you have changed. (The creation time is the moment
of the copy — Windows does not carry that one over.)


=================================================================
7. THE REPORT
=================================================================

The same table again, now describing what actually happened, plus
the skipped files, plus a note if the run was cancelled. An error on
a single file (target folder full, permission refused, a name the
filesystem rejects) shows as a red row with the message from
Windows; the rest of the run is unaffected.

After the report the tool returns to the options window, so several
batches can be done in one session without restarting.


=================================================================
8. WHEN SOMETHING GOES WRONG
=================================================================

  The file picker opens empty or hangs
      The archive share is not reachable from this machine. Open the
      archive path in Explorer first; the picker will then open
      instantly.

  Everything is grey / "no trailing number"
      You are looking at files that were already converted, or at
      files that never carried a timestamp. Check the Original
      timestamp column — it will be empty.

  Everything is grey / "invalid timestamp"
      The number at the end of those names is not a nanosecond
      time. Nothing to do; those files cannot be converted by this
      tool.

  The new times are an hour off what I expected
      Check the Prague checkbox. Summer time is the usual cause of
      a one-hour disagreement between a memory of the day and the
      archive's own UTC folders.

  A file was overwritten that I wanted to keep
      Blue rows in the preview are the warning, and they are
      counted in the dashboard before anything is written. Copy into
      an empty folder if you are unsure.


=================================================================
9. NOTES
=================================================================

  - No settings file. Every run asks for its options, so nothing is
    carried over from the last time and nothing can be left in a
    surprising state.
  - The window sizes itself to the content and stays centred.
  - Related tools: Image Tools finds and views archive images
    without copying them out; Screenshots handles the other
    direction, putting images together into a report.

-----------------------------------------------------------------
