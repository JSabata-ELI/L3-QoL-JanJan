Launcher — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Launcher_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  One window from which every program in this collection can be
  started. It looks at a folder full of programs, sorts them into
  groups, and shows one card per program with its version, its
  documentation and its older versions.

  It also tells you when a program has a newer version than the one
  you last used.


HOW IT WORKS

  Nothing is installed and nothing is registered. The Launcher
  simply looks at a folder, and whatever it finds there is what it
  offers. Point it at a different folder and it shows a different
  set of programs.

  That is why it has several sources to choose from at the top: the
  same programs exist in more than one place — the copy the lab runs
  from, the copy on the office share, and the working copy on this
  computer.


THE SOURCE BUTTONS AT THE TOP

  Lab - Scratch          the copy the lab runs from
  Office - Scratch       your own office copy
  Office - SharePoint    the copy on SharePoint
  Office - Programs      the working copy on this computer

  Pick one and it is scanned. The two office paths are yours to set
  with the Set paths button; they are remembered. On a lab machine
  the office sources are switched off.


A PROGRAM CARD

  Big button    starts the program. It shows the program's icon and
                name, and turns orange when a newer version is
                sitting on disk than the one you last used.
  ReadMe        the short description of the program.
  Details       the long one, when the program has it.
  Folder        opens the program's folder.
  Clock         a list of older versions; pick one to run it.

  Right-click the big button for two more things:
    Acknowledge update   "I know about the new version" — clears the
                         orange without starting anything.
    Move to group        puts the program into another group. Your
                         choice is remembered; Reset to default undoes it.


THE GROUPS

  The cards are sorted into groups (Main scripts, Side scripts,
  External, ...). Click a group's name to open or close it. Only Main
  scripts is open when the Launcher starts.


THE ORANGE HIGHLIGHT

  Every ten seconds the Launcher looks for newer program files. When
  it finds one, that card goes orange, and it stays orange across
  restarts until you either start the program or right-click it and
  choose Acknowledge update.


RUNNING AN OLD VERSION

  An archived program file is the whole old program, but it cannot
  run from the archive folder: it needs the support folder — the big
  one with the Python runtime in it — and there is only one of those,
  next to the current program. It suits every version, so it is not
  copied per version.

  So the Launcher swaps: it briefly puts the current program file and
  its source files aside, drops the old ones in, runs the old
  program, and puts the current ones back when you close it. The
  whole old set goes in together, never an old program file with the
  newest sources beside it.

  Versions that are already in the program folder — the last few are
  usually kept there side by side — are simply started where they
  are. Nothing is moved for those.

  A version that kept only its source files is run with Python, if
  there is Python on the computer.

  If a program is killed rather than closed, that swap can be left
  half done. The Launcher then refuses to start it again; the Clean
  button shows a count and puts it right when clicked.


THE OTHER BUTTONS

  Notes       the shared notes file on the current share.
  Set paths   set your two office paths.
  Clean       tidy up: version files left in the wrong folder, and
              programs stuck in a half-finished swap. Every scan checks
              for these; when it finds any, the button turns orange
              and shows a count, e.g. "Clean (3)". Nothing opens until
              you click it.


ONE RULE FOR DOCUMENTATION

  A program's short description must be named ReadMe_<folder name>,
  with any extension, and the long one ReadMe_<folder name>_Full.
  The Launcher builds the name it looks for out of the folder name,
  so a plain README.txt is invisible and the button opens nothing.

-----------------------------------------------------------------
