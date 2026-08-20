Dev Tools — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Dev Tools_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Turns the source folders in this repository into finished Windows
  programs, and puts those programs onto the shares where everybody
  runs them from.

  One window, two tabs sharing one log:

    Builder        source folder  ->  a program
    Copy Manager   a program      ->  the shares


THE FOUR PATHS

  Sources root   the repository with the source folders
  Dist root      where finished builds are written
  Scratch        the first place programs are published to
  SharePoint     the second

  Set them once with "Set paths"; they are remembered per user. This
  matters because Dev Tools is itself a built program and cannot work
  out where the repository is from its own location.


BUILDER

  Nothing has to be registered. Every folder under the sources root
  that contains a source file is a project, and the starting file is
  found automatically.

  Each project shows the version it was last built as and the next
  one, which is the last number stepped up by one and can be typed
  over. Projects are grouped as Main, Side or Ignored, and the
  grouping is remembered.

  Tick what you want and press Build. The result goes into a folder
  named after the version, together with a copy of the sources, the
  icon, BOTH documentation files (the short one and the detailed one),
  and any images or sounds folder the program needs at run time.

  When a build finishes it switches to the Copy Manager with those
  projects already ticked.

  A project can also declare helper programs — small programs that
  live in the same folder but are started on their own. Those appear
  as an extra line under the project, and each one builds into its
  own folder with its own version, and with its own ReadMe and
  Details buttons like any other program. Click the project —
  anywhere on its line, or its tick box, or Details — and its helpers
  open; the little arrow in front of the name closes them again.
  Opening and closing never changes what is ticked.


COPY MANAGER

  Lists every finished program with its available versions and the
  version last published from this computer. "Select new" ticks only
  the ones that have something newer.

  Copy publishes to whichever destinations you ticked. Before it
  overwrites anything, the previous version is moved into the
  archive, so an old version can always be run again from the
  Launcher. A program file that is locked because somebody is running
  it is skipped, not treated as a failure. If the icon changed, you
  are shown the old and the new one side by side first.

  The other buttons:

    ReadMe only       refresh just the documentation files — both the
                      short one and the detailed one
    Fix               repair old archive folders
    Build internal    build the shared support folder
    Deploy internal   push that folder to the selected programs


THE ARCHIVE LAYOUT

  One folder per version, and each folder is a complete, runnable
  snapshot: the program, its sources under their real names, and a
  log. No timestamps in the file names — the version is in the file
  name and in the folder name, which is what the Launcher reads.


THE ONE NAMING RULE

  A program's documentation must be named ReadMe_<folder name> for
  the short version and ReadMe_<folder name>_Full for the long one.
  Both are built into the version folder and both are published.

  The publishing step here is forgiving about the short one and will
  accept a plain README.txt; the Launcher is not, and shows nothing
  for it. So a publish can succeed while the Launcher still has no
  documentation to open.

  And remember that the documentation only reaches a share when you
  publish it. Writing a new ReadMe in the repository changes nothing
  on Z: until Copy or "ReadMe only" has run.

-----------------------------------------------------------------
