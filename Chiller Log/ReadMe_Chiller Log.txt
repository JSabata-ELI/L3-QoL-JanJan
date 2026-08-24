Chiller Log — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Chiller Log_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Keeps a long-term log of the six chillers — flow and temperature —
  and draws it as a graph over months and years.

  It is not a live display. It is the slow record: three readings a
  day, kept for years, so you can see whether a chiller's flow has
  been quietly dropping since the spring.


THE SIX CHILLERS

  1..4   Diode Array Chiller 1 to 4
  5      Helium Chiller
  6      Utility Chiller


HOW THE LOG IS BUILT

  - Three fixed times a day are recorded: 09:00, 13:30 and 18:00
    Prague time.
  - Each entry is the average of one minute of samples around that
    time, not a single reading.
  - A reading is only kept if the chiller's pump was running both at
    that moment AND ten minutes earlier. A pump that has just been
    switched on has not settled, and a number from that period would
    be misleading rather than merely noisy.
  - So gaps in the log are normal and meaningful: they mean the
    pump was not running.

  Everything comes from the archiver over the network. The program
  never talks to a chiller directly.


THE THREE TABS

  Graph          the log drawn over time. Pick flow or temperature,
                 tick which chillers to show, and choose the period:
                 All, 1Y, 6M, 1M, or your own dates.
                 Drag a rectangle on the graph to zoom into it, and
                 press Back to come out again. "Save graph" writes
                 the picture to a file.

  Data Archive   the same data as a table, one row per recorded
                 moment. Right-click a cell to copy it, a row to
                 copy the whole row, or to delete a row. There is an
                 export to a spreadsheet file.

  Log           what the program did while fetching, in detail. This
                is the tab to read when an update looks wrong.


UPDATING THE LOG

  Press "Update archive". It works out the last moment already in
  the log and fetches everything from there up to now, then appends
  what it found. Nothing already in the log is rewritten, so
  pressing it twice is harmless.

  A long catch-up takes a while — the archiver has to be asked in
  one-hour pieces — and the Log tab shows the progress.


TWO THINGS TO KNOW

  - The log lives in a plain text file next to the program
    (chiller_archive.csv). If it is missing, it is rebuilt from the
    historical spreadsheet in the same folder, which reaches back to
    2018.
  - The chosen period is only remembered between runs if the small
    settings file next to the program already exists. Without it,
    every start opens on the same default.

-----------------------------------------------------------------
