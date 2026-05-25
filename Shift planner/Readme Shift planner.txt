Shift_plan v1.0.0 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
-----------------------------------------------------------------

One-click launcher for the current L3-HAPLS shift plan
spreadsheet. Always opens the latest version regardless of how
the filename changes over time.


=================================================================
PURPOSE
=================================================================

  - The shift plan file in the L3-HAPLS\General folder is
    replaced every few weeks with a new version.
  - Each new file has a different name (e.g. Shift_plan_26_KW22_26,
    Shift_plan_W35_39, Shift_plan_27_KW17_20, etc.).
  - Static shortcuts and browser bookmarks break with every
    rename.
  - This launcher resolves the current file at click-time and
    opens it directly in Excel.


=================================================================
HOW IT WORKS
=================================================================

  - Double-click Shift_plan.exe.
  - The launcher scans the target folder for any file matching
    Shift_plan*.xlsx (case-insensitive).
  - If multiple matches are found, the most recently modified
    file is selected.
  - The file is opened in its default application (Excel).
  - If no matching file is found, the target folder is opened
    in Windows Explorer instead.


=================================================================
TARGET LOCATION
=================================================================

  - Folder:
    C:\Users\<user>\OneDrive - ELI Beamlines\L3-HAPLS\General
  - Filename pattern: Shift_plan*.xlsx
  - The folder is expected to be synchronised locally via
    OneDrive. Files On-Demand is supported - the file will be
    downloaded automatically on first access if not cached.


=================================================================
DEPLOYMENT
=================================================================

  - The launcher is a standalone single-file .exe built with
    PyInstaller (--onefile --windowed).
  - No installation, no background process, no scheduled task.
  - Place Shift_plan.exe anywhere convenient: desktop, Start
    menu, pinned folder, etc.
  - The hard-coded folder path assumes the standard ELI OneDrive
    layout. If the user profile path differs, the source must be
    rebuilt with the corrected FOLDER constant.


=================================================================
GENERAL NOTES
=================================================================

  - No configuration file - the target folder and filename
    pattern are compiled into the binary.
  - No logging - the launcher exits silently after opening the
    file.
  - Exit code 1 indicates no matching file was found (folder
    was opened as a fallback).

-----------------------------------------------------------------