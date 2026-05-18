Launcher v1.6.2 — Information
Created by Jan Moučka, ELI Beamlines

Bugs / suggestions: jan.moucka@eli-beams.eu
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

  [🔽]              — dropdown of archived older versions with
                      timestamps; click to launch that version.


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

  The Notes button (lower right) opens the shared notes.txt file
  from the current Scratch path.


=================================================================
GENERAL NOTES
=================================================================

  - Supports two exe layouts:
      Scratch:   <Program folder>/<Program>.exe
      Programy:  dist/<Program>/vX.Y.Z/<Program>.exe
  - Archive versions are read from the archive/ subfolder.
  - Program icons are loaded from icon.ico in the exe folder.
  - Config: %APPDATA%\Launcher\config.json

-----------------------------------------------------------------
