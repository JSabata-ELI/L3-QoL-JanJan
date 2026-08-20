Internal Builder — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19
  (_internal_builder.py 169 lines, _internal_builder.spec)
-----------------------------------------------------------------
Short version: Readme Internal Builder.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. THE PROBLEM
=================================================================

A built program in this collection is two things sitting next to
each other:

    Announcer.exe          the small starter
    _internal\             the Python runtime plus every library
                           the program needs

That second folder is by far the bigger one — hundreds of megabytes
once numpy, scipy, matplotlib, Pillow and Qt are in it. There are
sixteen programs, and they all need almost exactly the same set.

Building each program with its own support folder would mean sixteen
near-identical copies to build, to upload over the network and to
keep in step. So instead:

  - one dummy project (this one) is built with everything in it,
  - its support folder is taken as the shared one,
  - the same shared folder is copied next to every program.

Each program is then built as a starter only, which is small and
quick, and the heavy folder moves once.


=================================================================
2. WHY THE SOURCE FILE IS NOTHING BUT IMPORTS
=================================================================

The build tool (PyInstaller) works out what to pack by following the
imports in the code it is given. It has no other way of knowing what
a program will need.

So the source file here is a list of imports and nothing else. It has
no window, no main function worth running, no behaviour. Building it
produces an .exe that nobody ever launches — the .exe is a by-product
and the support folder is the actual output.

That gives a very simple rule:

    a library in the shared folder  <=>  an import in this file

To add a library, add the import. To find out whether a library is in
the shared folder, look for its import here.

The imports are grouped:

  standard library      the things Python ships with — file paths,
                        JSON, CSV, threads, sockets, timezones, ...
  tkinter               the older window toolkit, used by most of
                        the programs
  PySide6 (Qt)          the newer window toolkit, used by Image
                        Tools, Diagnostic and Pulser Monitor
  numpy, pandas, scipy  numbers, tables, image maths
  matplotlib            the graphs, in both the file-writing and the
                        Qt-window flavour
  Pillow (PIL)          reading and writing images, screen grabs
  screeninfo            which monitors are attached and how big
  orjson                fast JSON, used where large caches are read
  requests              HTTP, used for the archive and for alerts
  dateutil              flexible date parsing

  optional, each in a try/except:
      xlwt              old-style Excel writing
      tkcalendar        the date picker widget
      epics             live control-system values
      win32com          talking to Outlook and other Windows apps

The optional four are wrapped so that a machine without one of them
still produces a working bundle. The cost is silent: the bundle
simply lacks that library, and the program that needed it fails
later. If a deployed program complains about a missing library,
check whether it is one of these four and whether the build machine
had it installed.


=================================================================
3. THE TWO WAYS TO BUILD, AND WHY ONLY ONE IS RIGHT
=================================================================

3.1 The .spec file — use this one

    The .spec file collects every library group, and then does two
    extra things that a command line cannot:

      - numpy on Windows keeps its heavy maths libraries (BLAS) as
        loose Windows DLLs in a separate folder beside the package,
        not inside it. The automatic collection does not look there.
        The spec searches that folder and adds every DLL explicitly.
      - the same for scipy, when scipy keeps its libraries the same
        way.

    Why this matters: the compiled parts of numpy and the DLLs they
    load have to come from the same installation. Miss the DLLs and
    the build still succeeds, the folder still looks complete, and
    the failure appears days later as a program on the share
    refusing to start with an import error. That is an expensive way
    to find out.

3.2 Running the .py file directly

    The file has a block at the bottom that starts a build of itself
    with a long list of collection options. It works, but:

      - it writes to a fixed folder on C: (C:\Dev\dist), which is
        probably not where you want the output, and
      - it does not do the numpy/scipy DLL step from 3.1.

    Treat it as a historical convenience, not the supported path.

3.3 Through Dev Tools

    Dev Tools -> Copy Manager -> "Build internal" drives the build
    for you. The small settings file in this folder tells it to
    collect Pillow fully and to force Pillow's sub-parts as required
    imports, because Pillow is the one library the automatic
    detection regularly gets wrong.


=================================================================
4. GETTING THE FOLDER TO THE OTHER PROGRAMS
=================================================================

  Preferred:  Dev Tools -> Copy Manager
                "Build internal"   builds it
                "Deploy internal"  copies it next to every program
                                   on the share

  Manual fallback:  Extractor. It unpacks a zip of the folder into
  program folders named in a hard-coded list, and that list is out
  of date. See the Extractor documentation before using it.

The shared folder is deliberately NOT part of a normal program
build or upload — a program build produces only the starter. The
support folder moves separately, and only when it actually changed.


=================================================================
5. WHEN TO REBUILD IT
=================================================================

Rebuild the shared folder when, and only when:

  - a program starts needing a library that is not in it yet,
  - a library has to be updated for a bug fix or a new feature,
  - Python itself is updated on the build machine.

Do not rebuild it as part of routine work on a program. It is a
large upload, and every program on the share depends on it, so a bad
rebuild breaks all of them at once rather than one.

After a rebuild, start at least one tkinter program (for example
Time Converter or Launcher) and one Qt program (for example Image
Tools) from the share before you consider the job done. Those two
exercise almost everything in the folder between them.


=================================================================
6. WHEN SOMETHING GOES WRONG
=================================================================

  A deployed program will not start, "No module named ..."
      That library is not in the shared folder. Either the import is
      missing from this file, or the build machine did not have the
      library installed (check the optional four in section 2).

  A deployed program fails inside numpy or scipy
      Almost certainly the missing-DLL case from section 3.1. The
      bundle was built from the command line instead of the spec.
      Rebuild with the spec.

  The build fails on one library
      Install it on the build machine, or move its import into a
      try/except if the programs can live without it — but then note
      that the programs which need it will fail at run time instead.

  The build succeeds but the folder is much smaller than usual
      Something was skipped. Compare the size against the copy on
      the share before deploying; the shared folder is a known,
      fairly stable size.

  The "build" folder is huge
      That is the build tool's scratch area, left behind on purpose
      so a repeat build is faster. Safe to delete.

-----------------------------------------------------------------
