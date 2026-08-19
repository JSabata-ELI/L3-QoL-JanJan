Extractor — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Distributes the shared library folder to every program on the
Z:\Software share.

All the programs use the same set of Python libraries. Instead of
each one carrying its own copy, one zip of them is built once (by
Internal Builder) and this script unpacks it as the _internal folder
of every program.


=================================================================
HOW TO USE IT
=================================================================

  1. Put the zip into Z:\Software. There must be exactly one zip
     there — with several, the script warns and takes the first.
  2. Run it in a console: python e.py
  3. It lists which program folders it found and which are missing,
     then asks. Type y to go ahead.
  4. It works through them one by one and finishes with a count of
     how many worked and how many failed.


=================================================================
IMPORTANT
=================================================================

  - The old _internal folder is DELETED before the new one is
    unpacked. There is no backup, even though the confirmation
    message claims the old one is kept with a timestamp — that text
    is left over from an older version and is wrong.

  - The list of program folders inside the script is out of date. It
    still names programs that no longer exist (Image Finder, Image
    Slider, Copy manager, Counter of shots), and it does not know
    about the newer ones at all (Announcer, CSS Logger, Diagnostic,
    Dev Tools, Git Work, Pulser Monitor, Shift planner, Spectra,
    Chiller Log). Missing folders are skipped, so nothing breaks —
    but the programs not in the list simply do not get the update.

  - Dev Tools does this better now. Its Copy Manager tab has
    "Build internal" and "Deploy internal", which work from the
    current list of programs and need no hard-coded paths. Use those
    unless you have a reason not to. This script is the manual
    fallback.

  - It must run from a machine that can reach Z:.


=================================================================
GENERAL NOTES
=================================================================

  - It needs a console: the confirmation and the progress output go
    there, so it is not built into an exe.
  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
