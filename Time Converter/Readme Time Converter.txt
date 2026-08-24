Time Converter — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Time Converter_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Camera files come out of the archive with a machine timestamp at
  the end of the name, for example

      image_1746262710366145024.png

  which nobody can read. This tool copies the files somewhere you
  choose and renames the copies so the time is readable:

      image_2026_05_03--08_18_30__366145.png

  The originals are never touched. Only copies are written.


HOW IT WORKS

  - It reads the long number at the end of the name and turns it
    into a date and a time.
  - By default the time is shown in Prague local time. You can
    switch that off in the first window and keep UTC instead.
  - A file that already has a readable name is recognised and
    skipped, so you can safely run the tool twice over the same
    folder.
  - A file with no number at the end, or with a number that is not
    a plausible time, is skipped rather than given a nonsense name.
  - Nothing is copied until you have seen the full list of what
    will happen and confirmed it.


THE FOUR STEPS

  1. Options       Prague time on/off, detailed report on/off.
  2. Pick files    the picker opens in the camera archive on the
                   network. Select one file, or several, or press
                   Ctrl+A to take everything in the folder. There is
                   no folder picker for the source.
  3. Pick target   the folder the copies go into. Opens in your
                   Documents folder.
  4. Preview       one line per file, with the new name and what
                   will happen to it, plus counts at the top.
                   Press Proceed to start, Cancel to go back.

  A progress bar shows how far it is and how long is left, and can
  be cancelled at any point. At the end you get a summary, and the
  tool returns to step 1 so you can do another batch.


THE COLOURS IN THE LIST

  Green   will be copied
  Blue    will replace a file that is already in the target folder
  Grey    skipped
  Orange  something unusual, worth a look
  Red     error

-----------------------------------------------------------------
