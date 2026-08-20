Extractor — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (e.py, 137 lines)
-----------------------------------------------------------------
Short version: ReadMe_Extractor.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. WHAT PROBLEM THIS SOLVES
=================================================================

Every program on the share is a Windows program built out of Python.
A built program is two parts:

  - one small starter file (the .exe you double-click), and
  - one folder called "_internal" next to it, holding the Python
    runtime and all the support libraries the program needs.

Almost all of the programs need the same libraries, and that folder
is large. So the folder is built once, zipped, and the same zip is
unpacked into every program folder. The programs then share one
identical support folder instead of sixteen near-identical copies.

Extractor is the tool that does that unpacking. Internal Builder is
the tool that produces the zip in the first place.


=================================================================
2. WHERE IT WORKS
=================================================================

  Share root      Z:\Software
  The zip         any single *.zip file directly in Z:\Software
  What it writes  Z:\Software\<program>\_internal

The share root is written into the script as a fixed path. If the
share is mapped to a different drive letter on the machine you use,
edit that one line at the top of e.py.

The machine must have the share mapped and reachable. If it is not,
the script prints an error and exits without touching anything.


=================================================================
3. THE RUN, STEP BY STEP
=================================================================

  1. It prints the share root and checks that it exists.

  2. It searches the share root for zip files.
       - none found  -> error, exit
       - exactly one -> that is the one it uses
       - more than one -> it prints a warning, lists them all, and
         uses the first one in directory order. This is the one place
         where it can silently do the wrong thing, so keep only one
         zip on the share.

  3. It walks its built-in list of program folder names and splits
     them into "found" and "missing". Missing ones are listed and
     then skipped.

  4. It prints how many folders will be written and asks:
         Proceed? [y/N]
     Only a literal "y" continues. Anything else cancels.

  5. For each found folder:
       a) if a "_internal" folder already exists there, it is
          deleted outright. If the delete fails (a file is open, or
          the share is read-only) that folder is counted as failed
          and the script moves on to the next one.
       b) the zip is unpacked into a fresh "_internal".
       c) progress is printed every 200 files and again at the end.

  6. It prints a final line with the number of folders that
     succeeded and the number that failed, and waits for Enter.


=================================================================
4. THE PATH-PREFIX TRICK
=================================================================

The zip does not contain the support folder at its top level. It
contains something like

    _internal_builder/_internal/python312.dll
    _internal_builder/_internal/base_library.zip
    ...

Extractor reads the first real file entry in the zip, finds the part
of the path that ends with "_internal/", and treats everything up to
and including that as a prefix to cut away. Every entry is then
written relative to the target "_internal" folder.

It prints the prefix it decided on ("Stripping prefix: ...") before
it starts. If that line looks wrong, stop the run — the zip is not
laid out the way the script expects, and the result will be a nested
folder instead of a usable one.

Entries that do not start with the detected prefix are skipped.


=================================================================
5. THE OUT-OF-DATE FOLDER LIST
=================================================================

The list inside the script is:

    Calibrations, Copy manager, Counter of shots, Image Finder,
    Image Slider, Image Tools, Launcher, Screenshots,
    Time converter

Of those, "Copy manager", "Counter of shots", "Image Finder" and
"Image Slider" no longer exist as separate programs — their work
moved into Dev Tools and into Image Tools. And these current
programs are missing from the list entirely:

    Announcer, Chiller Log, CSS Logger, Dev Tools, Diagnostic,
    Extractor, Git Work, Internal Builder, Pulser Monitor,
    Screenshots, Shift planner, Spectra, Time Converter

("Time converter" in the list does not match the real folder name
"Time Converter" on a case-sensitive comparison, but Windows folder
lookup is case-insensitive, so that one still resolves.)

Consequence: a run of this script updates only the handful of
folders that happen to still match. It never breaks anything, it
just quietly does less than you might assume.

Two ways to deal with it:

  - Preferred: use Dev Tools -> Copy Manager -> "Build internal"
    and "Deploy internal". That path discovers the program folders
    instead of hard-coding them.
  - If you really want to use this script, edit the list at the top
    of e.py first, then run it.


=================================================================
6. WHY IT IS NOT AN EXE
=================================================================

The confirmation prompt and all the progress output go to a console.
Built as a windowed program there would be nowhere for them to
appear, and the confirmation could not be answered. So it stays a
plain script that you run from a console on purpose.


=================================================================
7. WHEN SOMETHING GOES WRONG
=================================================================

  "SOFTWARE_ROOT does not exist or is not accessible"
      The share is not mapped, or the machine cannot reach it.
      Open Z:\Software in Explorer first and try again.

  "No .zip file found"
      The zip was not copied to the share, or it sits in a
      sub-folder. It has to be directly in Z:\Software.

  "Found N zip files"
      Remove the ones you do not want. Do not rely on which one it
      picks.

  "Could not remove old _internal"
      Some program is running from that folder, or Explorer has a
      file open there. Close it and re-run; only that one folder was
      skipped.

  A program will not start after a run
      The support folder it got does not match the version of the
      program. Rebuild the program, or deploy the matching support
      folder from Dev Tools.

  The run finished but a program was not updated
      Its folder is not in the built-in list — see section 5.


=================================================================
8. RELATED TOOLS
=================================================================

  Internal Builder   builds the support folder and the zip
  Dev Tools          Copy Manager tab: build + deploy without the
                     hard-coded list; this is the maintained path
  Launcher           starts the deployed programs from the share

-----------------------------------------------------------------
