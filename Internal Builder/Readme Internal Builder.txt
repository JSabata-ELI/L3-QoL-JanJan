Internal Builder — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Builds the one shared library folder that all the other programs use.

Every program here needs the same Python libraries. Instead of each
build carrying its own copy, this project is built once and its
_internal folder becomes the shared one.

To hand it out afterwards use Dev Tools → Copy Manager →
"Build internal" and "Deploy internal". (Extractor is the older,
manual way of doing the same thing.)


=================================================================
IT IS NOT A PROGRAM
=================================================================

  _internal_builder.py contains nothing but import lines. It is
  never run as an application — it exists so that PyInstaller has
  something to follow. PyInstaller packs whatever is imported, so
  an import in that file means a library in the shared folder.

  To add a library to the shared folder: import it there.


=================================================================
HOW TO BUILD IT
=================================================================

  Use the .spec file, not the plain command line.

  Both do the same collecting, but the .spec file additionally picks
  up numpy's own DLL folder (numpy.libs, and scipy.libs when it is
  there). Those DLLs are not found automatically, and a folder built
  without them looks fine — the failure only shows up later, as an
  import error inside a deployed program.

  Running _internal_builder.py directly starts a build too, but that
  one writes to a fixed path (C:\Dev\dist) and skips the DLL step.


=================================================================
GENERAL NOTES
=================================================================

  - Some libraries are optional (xlwt, tkcalendar, epics, win32com).
    If the build machine does not have them, the build still works —
    the shared folder just will not contain them.
  - build/ is PyInstaller's working folder, left over from a build.
    It can be deleted.
  - For whoever works on the code: STRUCTURE.md.

-----------------------------------------------------------------
