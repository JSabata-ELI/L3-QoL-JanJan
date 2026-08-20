Internal Builder — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Internal Builder_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT IS FOR

  All the programs here need the same set of Python support files.
  Instead of every program carrying its own copy, this one project is
  built once and its support folder becomes the shared one that all
  the programs get.


THE IDEA IN ONE PARAGRAPH

  The build tool packs whatever a program imports. So this project
  is a file that does nothing but import things. It is never started
  and has no window — it exists purely to give the build tool a list
  to follow. Every import in it means one more library in the shared
  folder.

  To add a library to the shared folder: add an import for it.


HOW TO BUILD IT

  Use the .spec file, not the plain command line.

  Both collect the same libraries, but the .spec file also picks up
  the extra Windows files that numpy (and scipy) keep in a separate
  place. Those are not found automatically, and a folder built
  without them looks perfectly fine — the failure only appears
  later, as a program on the share refusing to start.

  Running the .py file directly also starts a build, but it writes to
  a fixed folder on C: and skips that extra step. Avoid it.


HOW IT REACHES THE OTHER PROGRAMS

  Dev Tools -> Copy Manager -> "Build internal", then
  "Deploy internal".

  Extractor is the older manual way of doing the same thing.


NOTES

  - A few libraries are optional. If the machine you build on does
    not have them, the build still succeeds and the shared folder
    simply does without them.
  - The "build" folder is scratch left over from a build and can be
    deleted at any time.

-----------------------------------------------------------------
