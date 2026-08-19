Dev Tools — Information
Created by Jan Moučka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------

Personal tool for building and deploying the python projects in
this repository. One window, two tabs sharing one log:

  Builder        — turns a project folder into an exe
  Copy Manager   — copies the built exe to Scratch / SharePoint


=================================================================
SET PATHS (both tabs)
=================================================================

  Sources root   — the repository with the .py projects
  Dist root      — where builds are written (…/dist)
  Scratch        — deployment target 1 (also holds Versions.txt)
  SharePoint     — deployment target 2

  Stored in %APPDATA%\DevTools\config.json. This matters when Dev
  Tools itself runs as an exe — it can no longer guess the repo
  location from its own path.


=================================================================
BUILDER TAB
=================================================================

  Projects are discovered automatically: every folder under the
  sources root that contains a .py file. Entry point is picked as
  <folder name>.py, then main.py, then app.py.

  Each project is grouped as Main / Side / Ignored (the grouping
  is remembered) and shows its last built version plus the next
  one, which is the patch bump by default and can be typed over.

  Tick the projects you want, then Build. Output goes to

      dist/<project>/v<version>/<project> v<version>.exe

  together with a copy of the sources, icon.ico and _internal/.

  After a build the tool switches to the Copy Manager tab with
  the built projects already selected.

  The build is PyInstaller --onedir --windowed. On top of that:
    - test_*.py is never bundled (dev-only).
    - numpy / scipy / sklearn / cv2 / matplotlib / pandas get
      --collect-all automatically when the sources import them,
      so their native libraries are not missing at runtime.
    - the test suites those packages drag in are excluded again.
    - the folders images\, sounds\, assets\ and icons\ are copied
      next to the exe whenever the program has them. Programs read
      those at runtime (Announcer takes its alarm image from
      images\), so they have to travel with every build.
    - the program's ReadMe is copied next to the exe as well; the
      Copy Manager takes it from there, so the published ReadMe
      follows the sources.
    - the new version is written to <Scratch>\Versions.txt.

  Per-project extras go into build_config.json next to the
  sources. extra_files may name folders as well as files:

      {
        "collect_all":      ["module"],
        "collect_binaries": ["module"],
        "hidden_imports":   ["module"],
        "copy_metadata":    ["module"],
        "exclude_modules":  ["module"],
        "extra_files":      ["data.json", "images"]
      }


=================================================================
COPY MANAGER TAB
=================================================================

  Lists every program in the dist root with its available
  versions and the version last deployed from this machine.
  "Select new" ticks only the programs that have something newer.

  Copy deploys to the ticked destinations:

      <destination>\<program>\<program> vX.Y.Z.exe
                              *.py, ReadMe, icon.ico
                              images\, sounds\, …
                              archive\vX.Y.Z\…

  Folders that came with the build (images\, sounds\) are copied
  along with the files. Folders on the destination that the new
  version does not bring are deleted as leftovers — except
  _internal\ and archive\.

  The previous exe (and its sources) is moved into the archive
  folder first. A locked exe is skipped, not treated as an error.
  If the icon changed, a dialog shows the old and the new one
  side by side before anything is overwritten.

  Other buttons:
    ReadMe only     — refresh just the ReadMe files
    Fix             — repair the archive folders (see below)
    Build internal  — build the internal-libs builder
    Deploy internal — push _internal/ into the selected programs


=================================================================
FIX — ARCHIVE LAYOUT
=================================================================

  The archive keeps one folder per version, and each folder is a
  self-contained, runnable snapshot:

      archive\v2.5.4\Image Tools v2.5.4.exe
                     if_t.py, is_t.py, …
                     archive_log.txt

  No timestamps in file names — the version is in the file name
  and in the folder name, which is what the Launcher reads.

  Fix repairs older archives:
    - loose files in archive\ are moved into their version folder
    - helper modules stranded in archive\unknown\ are matched
      back to the version they were archived from (via the deploy
      timestamps in the two archive_log.txt files) and restored
      under their original importable name, so "import if_t"
      still works inside the folder
    - duplicate copies ("… (2).exe", timestamped twins) are
      removed and the remaining file is renamed to the clean name


=================================================================
GENERAL NOTES
=================================================================

  - builder_settings.json  root folder + project grouping
  - build_usage.json       build history
  - copy_manager_state.ini last deployed version per program
  - Versions.txt           on Scratch, "Name = vX.Y.Z" per program

  A program's ReadMe must be named ReadMe_<folder name> (any
  extension). The deploy is forgiving and also accepts a plain
  README.txt, but the Launcher is not: a ReadMe it cannot match is
  invisible, so its ReadMe button opens nothing. The deploy can
  therefore succeed while the Launcher still shows no ReadMe.

  For the developer view of the code see STRUCTURE.md, and
  dev_tools_structure.md for the line-by-line map.

-----------------------------------------------------------------
