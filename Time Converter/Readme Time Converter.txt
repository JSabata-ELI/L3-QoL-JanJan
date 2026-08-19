Time Converter — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Batch file renaming utility that converts UNIX nanosecond
timestamps embedded in filenames to human-readable datetime
format, then copies the renamed files to a chosen destination.


=================================================================
WORKFLOW
=================================================================

1. START
   - On launch, two options are shown:
     * Convert to local time (Europe/Prague) — ON by default.
       Turn off to keep UTC.
     * Show detailed report after copying — ON by default.

2. SELECT FILES
   - Choose one or more files via the file picker, or select an
     entire folder (all files in the folder are included).

3. SELECT DESTINATION
   - Pick the target folder where renamed copies will be saved.

4. PREVIEW
   - A preview table shows the planned rename for every file:
     Prefix | Original timestamp | Orig. UTC | New name | Status
   - Dashboard shows counts: Will copy / Will overwrite / Will skip.
   - Statuses are colour-coded:
       Green  — will copy (new file)
       Blue   — will overwrite (file already exists at destination)
       Grey   — skip (already converted, no timestamp, or out of range)
       Orange — warning (unusual situation)
       Red    — error
   - Click Proceed to start, or Cancel to go back.

5. PROGRESS
   - A progress bar shows X / Total (percentage) and elapsed time.
   - Click Cancel at any time to abort mid-way.

6. REPORT
   - After completion a summary shows total copied, skipped, and
     any errors encountered.


=================================================================
FILENAME FORMAT
=================================================================

  Input:  any file whose name ends with a UNIX nanosecond
          timestamp, e.g. "image_1746262710366145024.png"

  Output: "image_2026_05_03--08_18_30__366145.png"
          (Prague local time by default)

  So: underscores inside the date and inside the time, two hyphens
  between them, and six digits of fractional seconds
  (microseconds) after the double underscore.

  Files that have already been converted are skipped
  automatically, recognised by that exact pattern:
  YYYY_MM_DD--HH_MM_SS__ffffff

  A "-_-" or "_-_" run in the original name is tidied up to a
  single underscore first, which is what the camera archive tends
  to produce.

  Valid timestamp range: 2000-01-01 to 2100-01-01. A trailing
  number outside it is treated as "not a timestamp" and the file
  is skipped rather than given a nonsense name.


=================================================================
GENERAL NOTES
=================================================================

  - Original files are never modified — only copies are made.
  - Copying runs on four threads at once, and the progress window
    estimates the remaining time from the rate so far.
  - The window auto-sizes to fit the content width.
  - After a run it goes back to the start, so several batches can
    be done without restarting.
  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
