Launcher — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Central hub for launching all QoL tools. Scans the selected
software directory, organises programs into categories, shows
available versions, and launches executables.


=================================================================
SOURCES (radio buttons at the top)
=================================================================

  - Lab — Scratch:        \\hapls-share.lcs.local\scratch\Software
  - Office — Scratch:     configurable (see Set Path)
  - Office — SharePoint:  configurable (see Set Path)
  - Office — Programs:    OneDrive/ELI Beamlines/Python/programy

  Select a source to scan it. The active source is highlighted.

SET PATH (upper-right button)
  - Configure your personal Office Scratch and SharePoint paths.
  - Settings are saved to %APPDATA%\Launcher\config.json and
    restored on next launch.


=================================================================
PROGRAM CARDS
=================================================================

Each program is shown as a card with:

  [Program button]  — click to launch the current version.
                      The button shows an arrow (↑) and turns
                      orange when a newer version is available
                      on disk.

  [ReadMe]          — opens the program's readme file (if present).

  [✓]               — acknowledge a new version without launching.
                      Clears the orange highlight and remembers
                      the acknowledgement across restarts.
                      (Only shown when an update is available.)

  [📂]              — opens the program's folder in Explorer.

  [🔽]              — dropdown of archived older versions;
                      click to launch that version. Older builds
                      are labelled "v1.2.3 (date time)", newer
                      archive snapshots just "v1.2.3".

  Right-click a card to move the program to another category
  (or to reset it back to its default one). The choice is saved.


=================================================================
LAUNCHING AN OLD VERSION
=================================================================

  An archived build needs the program folder's _internal folder,
  so the Launcher temporarily swaps files:

    1. the current exe/py are moved to archive\_temp_latest\
    2. the archived exe/py are copied into the program folder
    3. the old version runs
    4. on exit the current files are moved back

  If the program is closed abnormally the swap may be left
  unfinished. The Launcher then refuses to start that program
  again and offers to restore it — on the next scan, or right
  away via the 🧹 Clean button.


=================================================================
UPDATE INDICATOR
=================================================================

  - Every 10 seconds the Launcher checks for newer .exe files.
  - If a newer version is found, the button turns orange and
    gains a ↑ arrow.
  - The highlight persists across restarts until you either:
      * Launch the program (auto-acknowledges), or
      * Click the ✓ button on the card.


=================================================================
CATEGORIES
=================================================================

  Programs are grouped into collapsible sections:
  Scripts, Parts, External, In Progress,
  Not Working Correctly, Personal.

  Click a section header to expand or collapse it.


=================================================================
NOTES
=================================================================

  The Notes button opens the shared notes.txt file from the
  current Scratch path.


=================================================================
CLEAN (🧹 button)
=================================================================

  Runs two housekeeping checks on the loaded programs:

  - Versioned exe files (…__20260101_120000.exe) sitting in a
    program folder instead of its archive folder — offers to
    move them.
  - Programs left in an unfinished old-version swap
    (archive\_temp_latest\ still present) — offers to restore
    the latest files.

  Both checks also run automatically after every scan.


=================================================================
GENERAL NOTES
=================================================================

  - Supports two exe layouts:
      Scratch:   <Program folder>/<Program>.exe
      Programy:  dist/<Program>/vX.Y.Z/<Program>.exe
  - Archive versions are read from the archive/ subfolder:
      archive/vX.Y.Z/<Program> vX.Y.Z.exe          (new, one
        entry per version folder; " (2)" copies are ignored)
      archive/<Program> vX.Y.Z__<date>_<time>.exe  (legacy flat)
  - Program icons are loaded from icon.ico in the exe folder.
  - Office sources are disabled on lab machines.
  - Config: %APPDATA%\Launcher\config.json
    (paths, acknowledged versions, custom categories)

-----------------------------------------------------------------
