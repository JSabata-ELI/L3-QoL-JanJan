Extractor — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Extractor_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT IS FOR

  All the programs on the Z:\Software share need the same set of
  Python support files. Instead of every program carrying its own
  copy, one zip with those files is built once, and this script
  unpacks that zip into every program folder.


HOW IT WORKS

  - It looks into Z:\Software and expects to find exactly one zip
    file there.
  - It goes through a list of program folders written inside the
    script, and for each one it throws away the old support folder
    and unpacks the zip in its place.
  - Everything happens in a console window: it prints what it found,
    asks once for confirmation, then reports how many folders
    succeeded and how many failed.


HOW TO RUN IT

  1. Put the zip into Z:\Software.
  2. Open a console and run:  python e.py
  3. Read the list it prints, type  y  and press Enter.
  4. Wait for the final count.


THINGS TO KNOW BEFORE YOU USE IT

  - The old support folder is deleted, not backed up. The message on
    screen says it is kept with a timestamp; that sentence is wrong
    and left over from an older version.
  - The list of program folders inside the script is out of date. It
    still names folders that no longer exist and it does not know
    about the newer programs at all, so those simply do not get
    updated. Nothing breaks — missing folders are skipped.
  - Dev Tools does the same job better: its Copy Manager tab has
    "Build internal" and "Deploy internal", which always work from
    the current list of programs. Use those unless you have a
    reason not to. This script is the manual fallback.
  - The computer you run it from must be able to reach Z:.

-----------------------------------------------------------------
