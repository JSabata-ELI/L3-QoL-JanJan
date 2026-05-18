Time Converter v1.3.6 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
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

  Input:  any file whose name ends with a 19-digit UNIX
          nanosecond timestamp (e.g. "image_1746262710366145024.png")

  Output: "image_2026-05-03--08-18-30__366.png"
          (Prague local time by default)

  Files that have already been converted (name matches
  YYYY-MM-DD--HH-MM-SS__mmm pattern) are skipped automatically.

  Valid timestamp range: 2000-01-01 to 2100-01-01.


=================================================================
GENERAL NOTES
=================================================================

  - Original files are never modified — only copies are made.
  - File operations run concurrently for speed.
  - The window auto-sizes to fit the content width.

-----------------------------------------------------------------
