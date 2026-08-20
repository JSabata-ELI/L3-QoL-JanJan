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
  with the gear button; they are remembered. On a lab machine the
  office sources are switched off.


A PROGRAM CARD

  Big button    starts the program. It shows the version, and turns
                orange with an arrow when a newer version is sitting
                on disk than the one you last used.
  ReadMe        the short description of the program.
  Details       the long one, when the program has it.
  Tick          "I know about the new version" — clears the orange
                without starting anything.
  Folder        opens the program's folder.
  Arrow down    a list of older versions; pick one to run it.

  Right-click a card to move that program into another group. Your
  choice is remembered.


THE ORANGE HIGHLIGHT

  Every ten seconds the Launcher looks for newer program files. When
  it finds one, that card goes orange with an arrow, and it stays
  orange across restarts until you either start the program or press
  the tick.


RUNNING AN OLD VERSION

  An old program file cannot run on its own — it needs the support
  folder that sits next to the current one. So the Launcher briefly
  puts the current files aside, drops the old ones in, runs them,
  and puts the current ones back when you close the program.

  If a program is killed rather than closed, that swap can be left
  half done. The Launcher then refuses to start it again and offers
  to put it right, either at the next scan or straight away with the
  broom button.


THE OTHER BUTTONS

  Notes    the shared notes file on the current share.
  Gear     set your two office paths.
  Broom    tidy up: version files left in the wrong folder, and
           programs stuck in a half-finished swap. Both checks also
           run by themselves after every scan.


ONE RULE FOR DOCUMENTATION

  A program's short description must be named ReadMe_<folder name>,
  with any extension, and the long one ReadMe_<folder name>_Full.
  The Launcher builds the name it looks for out of the folder name,
  so a plain README.txt is invisible and the button opens nothing.

-----------------------------------------------------------------
