Dev Tools — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19
  (dev_tools.py 87 lines, b_t.py 1495, cm_t.py 2129)
-----------------------------------------------------------------
Short version: ReadMe Dev Tools.txt  ("ReadMe" button)
Code map:      STRUCTURE.md  (and dev_tools_structure.md, older)
-----------------------------------------------------------------


=================================================================
1. WHAT IT IS FOR
=================================================================

Everything in this collection is written in Python but has to arrive
at a lab machine as a program you double-click, with no Python
installed anywhere near it. Dev Tools is the machine that does that
conversion and the delivery afterwards.

Two tabs, one shared log:

  Builder        a source folder becomes a Windows program
  Copy Manager   that program is published to the shares

They are separate on purpose: building is slow and local, publishing
is fast and touches what everybody else uses. You will often build
several times and publish once.


=================================================================
2. THE FOUR PATHS
=================================================================

  Sources root   the repository holding the source folders
  Dist root      where finished builds are written
  Scratch        publishing destination one; also holds the shared
                 version list
  SharePoint     publishing destination two

Set them with "Set paths" in either tab. They are stored per user.

WHY THEY ARE SETTINGS AND NOT WORKED OUT AUTOMATICALLY: Dev Tools is
itself a built program. Once built, it no longer sits inside the
repository, so it cannot deduce where the repository is from its own
location. When run from source it can guess, and it does — the
settings are the fallback that makes the built copy work.


=================================================================
3. THE BUILDER
=================================================================

3.1 HOW PROJECTS ARE FOUND

    Every folder under the sources root containing a source file is a
    project. Nothing is registered anywhere. Folders starting with an
    underscore are skipped, as are the build output, the Matlab
    folder, the icon folder and the usual hidden tool folders.

    The starting file is guessed in this order: a file named after
    the folder, then main.py, then app.py, then — if there is exactly
    one source file — that one.

3.2 GROUPS AND VERSIONS

    Each project sits in Main, Side or Ignored, and that choice is
    remembered. The point is that the list is long and most of it is
    not what you are working on today.

    Each row shows the version the project was last built as, taken
    from the build output folder, and the next version, which is the
    last number stepped up by one. Type over it when a change
    deserves a bigger step.

3.3 WHAT A BUILD PRODUCES

    A folder named after the version, containing:

      the program file, named with its version in it
      the shared support folder
      a copy of every source file from the project root
      the icon
      the settings file the build read
      the runtime folders the program needs — images, sounds,
        assets, icons — if it has them
      BOTH of the project's documentation files: the short ReadMe and
        the detailed one

    A note on each of those last few, because they are all there for
    a reason somebody learned the hard way:

      THE SOURCES are copied so a version folder can say what it was
      built from, and so that an archived version is a complete
      snapshot rather than an opaque program file.

      THE ICON is copied as a real file next to the program because
      one of the window toolkits in use can only load an icon from a
      path, not from inside a bundle.

      THE RUNTIME FOLDERS are found and copied automatically, with no
      configuration. A program reads them from next to itself at the
      moment it needs them, so a build shipped without them does not
      fail at build time or at start-up — it fails much later, when
      somebody triggers the alarm that needed the picture.

      THE DOCUMENTATION is copied so that the publishing step can
      take it from there. Without that, publishing would keep
      shipping whatever documentation was already on the share, and
      an updated ReadMe would never reach anybody.

      Both files are copied, because the Launcher has a button for
      each: the short one behind "ReadMe" and the detailed one behind
      "Details". Carrying only the short one leaves the Details
      button with nothing to open — which looks exactly like the
      feature not existing.

3.4 WHAT THE BUILD DOES BEHIND THE SCENES

    The underlying tool is PyInstaller, in its folder-based, windowed
    mode. On top of the plain invocation:

      - Test files are never bundled. They are development-only and
        nothing imports them at run time.

      - If the sources import numpy, scipy, scikit-learn, OpenCV,
        matplotlib or pandas, the whole of that package is collected
        automatically. Those packages carry compiled parts that the
        automatic detection misses, and a bundle without them fails
        at run time with a missing-module error that names something
        you have never heard of.

      - Collecting a whole package also drags in that package's own
        test suite, which is large and useless. Those are excluded
        again — and only the test suites, never the similarly named
        parts that some libraries genuinely use at run time.

      - The version folder is deleted before the build. OneDrive
        marks synced files read-only and the build tool refuses to
        overwrite them.

      - Afterwards the built program is moved up one level into the
        version folder, renamed to include its version, and the new
        version is written into the shared version list on Scratch.

3.5 PER-PROJECT EXTRAS

    A settings file next to the sources can add:

      collect_all        pull in a whole package
      collect_binaries   pull in a package's compiled parts only
      hidden_imports     force a module in that nothing visibly
                         imports
      copy_metadata      include a package's registration data
      exclude_modules    keep something out
      extra_files        extra files or whole folders to copy next
                         to the program
      extra_exes         helper programs — see below

3.6 HELPER PROGRAMS

    A helper is a program that lives in a project's folder but is
    started on its own. There is one in practice: Diagnostic's Webex
    listener, which runs all day so that a chat command can open the
    application while the application is closed.

    In the project list, a project with helpers gets a small arrow.
    Open it and each helper is a line of its own, with everything a
    project's line has: a tick box, its own next version, and its own
    ReadMe and Details buttons. Details shows the helper in the panel
    on the right — its name, the script it is built from, its last and
    its next version — so the version you build it with is set there,
    separately from its parent's. Group stays empty and locked: a
    helper is not sorted into the sections, it follows its parent.

    A helper's documentation is named after the HELPER, not after the
    folder it lives in — ReadMe_<helper name>.txt and
    ReadMe_<helper name>_Full.txt, both in the parent's folder. That
    is the name the Launcher looks for, because the helper is
    deployed as a program of its own; the parent's ReadMe would never
    be found under it. The build copies both into the helper's version
    folder, and says so in the log when one of them is missing.

    The helpers open on any click on the project: on its line, on its
    tick box, or on Details. Only the arrow in front of the name
    closes them again. The point is that the helpers are usually the
    reason for the click — they do not have to be hunted for behind a
    two-character arrow.

    OPENING AND CLOSING NEVER TOUCHES WHAT IS TICKED. Neither does
    changing a group, switching "Show ignored" on and off, or any
    other redraw of the list: what you ticked stays ticked until you
    untick it or build. The one exception is a program that leaves
    the list — moved to Ignored while ignored programs are hidden —
    whose tick goes with it, because what builds has to be what you
    can see.

    Ticking the project ticks its helpers too — the usual case is a
    release where the two match. It is a push, not a lock: untick a
    helper afterwards and it stays unticked, which is how you rebuild
    only the application. A tick survives folding the project away.

    HELPERS BUILD DIFFERENTLY, in two ways that matter:

      Into their own folder, with their own version, not into the
      parent's version folder. A helper can be built without its
      parent, and a version folder of the application containing only
      the helper would be a version of the application that is not
      the application. As a result a helper also appears as its own
      row in the Copy Manager and publishes like any other program.

      As a single self-contained file, rather than a folder. Two
      folder-based builds side by side would each want their own
      support folder in the same place and would overwrite each
      other. One file cannot collide with anything, and it is also
      the form that survives being copied somewhere by hand, which is
      how these actually get installed.

    A helper shows a console window by default, because a background
    program's window is the only sign that it is alive and closing it
    is how you stop it. That can be switched off per helper.

    A helper inherits the project's extra files by default, since a
    program shipping beside an application usually reads the same
    data — and in its own folder it cannot borrow the application's
    copy.

    A HELPER THAT FAILS TO BUILD ONLY LOGS A WARNING. The application
    is already built and usable at that point, and failing the whole
    build would throw that away.

3.7 AFTER A BUILD

    The window switches to the Copy Manager with the projects you
    built already ticked, at the versions you built. The intended
    flow is build, glance at the log, publish.


=================================================================
4. THE COPY MANAGER
=================================================================

4.1 THE LIST

    Every program in the build output, with its available versions
    and the version last published from this computer. Programs with
    something newer than what was published are highlighted, and
    "Select new" ticks exactly those.

    "Last published" is remembered per computer, not per share. It
    answers "have I published this" rather than "is this the newest
    on the share".

4.2 WHAT PUBLISHING WRITES

      <destination>/<program>/
          the program file, with its version in the name
          the source files
          the documentation
          the icon
          the runtime folders the build brought along
          archive/
              vX.Y.Z/  the previous version, complete

4.3 THE ORDER OF OPERATIONS, AND THE THREE SAFETY RULES

    1. The previous program file and its sources are MOVED into the
       archive first. Nothing is overwritten until the old version is
       safely somewhere else — that is what makes the Launcher's
       "run an older version" possible at all.

    2. A program file that is locked, because somebody has it
       running, is SKIPPED and reported. It is not an error: the
       other destinations and the other programs still publish, and
       you can repeat the publish later. Treating it as a failure
       would mean one colleague with the program open blocks a whole
       release.

    3. If the icon changed, a dialog shows the old and the new one
       side by side, per program, BEFORE anything is overwritten. An
       icon change is usually intentional and occasionally a
       mistake, and it is very visible to everybody afterwards.

    Then: folders on the destination that the new version does not
    bring are deleted as leftovers — except the shared support folder
    and the archive, which are never touched, and except the folders
    the new version does bring. That last exception exists because
    the runtime folders (images, sounds) are exactly such folders,
    and without the exception a publish deleted them and the deployed
    program came up without its alarm picture.

4.4 WHERE THE DOCUMENTATION IS TAKEN FROM

    Five places, in this order:

      1. the version folder — the Builder puts it there
      2. THE SOURCE FOLDER IN THE REPOSITORY — the live one
      3. the build output folder for that program
      4. the copy already on the destination
      5. nothing found: skip, with a warning

    Step 2 is the important one. Builds made before the Builder
    started copying documentation have none in their version folder,
    and without that step such a publish shipped no documentation at
    all — so the Launcher card had no button.

4.5 THE OTHER BUTTONS

    ReadMe only
        Publishes just the documentation files — both the short one
        and the detailed one. For fixing a typo without a rebuild and
        without touching the program itself. This is also the button
        to press after writing documentation for a program you do not
        want to rebuild.

    Fix
        Repairs old archive folders — section 5.

    Build internal
        Builds the shared support folder (the Internal Builder
        project).

    Deploy internal
        Pushes that folder into the selected programs on the
        destinations. This is a large upload and every program
        depends on it, so it is a separate, deliberate action rather
        than part of a normal publish.


=================================================================
5. THE ARCHIVE, AND WHAT "FIX" REPAIRS
=================================================================

5.1 THE INTENDED LAYOUT

      archive/v2.5.4/
          Image Tools v2.5.4.exe
          if_t.py, is_t.py, ...        (their real, importable names)
          archive_log.txt

    One folder per version, and each folder is a self-contained,
    runnable snapshot. No timestamps in the file names: the version
    is in the file name and in the folder name, which is what the
    Launcher reads.

5.2 WHAT FIX DOES

      - Loose files sitting directly in the archive are moved into
        the version folder they belong to.

      - Helper source files stranded in an "unknown" folder are
        matched back to the version they were archived from and
        restored under their original importable name. Those files
        carry no version in their name, so old copies used to pile up
        as "if_t.py", "if_t (2).py" and so on. The link is rebuilt
        from the publish timestamps recorded in the two log files —
        the Nth copy corresponds to the Nth timestamp. Restoring the
        original name matters because the snapshot has to be
        runnable, and a program that imports a module by name cannot
        find "if_t (2).py".

      - Duplicate copies and timestamped twins are removed, and the
        remaining file is renamed to the clean name.


=================================================================
6. THE FILES DEV TOOLS KEEPS
=================================================================

  the builder settings     the scan root and each project's group
  the build history        what was built and when
  the publish state        the last version published per program,
                           per computer
  the shared version list  on Scratch: one line per program, "Name =
                           vX.Y.Z". This is what tells everybody
                           which version is current.


=================================================================
7. THE DOCUMENTATION NAMING RULE
=================================================================

  ReadMe_<folder name>          the short version, the Launcher's
                                "ReadMe" button
  ReadMe_<folder name>_Full     the detailed version, the Launcher's
                                "Details" button
                                (_Details and Manual_<folder> are
                                also accepted)

Case, spaces, underscores, hyphens, dots and the extension are all
ignored, so several spellings work.

Both files are carried by the build and by the publish. A program
with only the short one is fine — the Launcher then simply shows no
Details button.

THE TRAP: the publishing step here is forgiving and will also accept
a plain README.txt. The Launcher is not — it builds the filename it
looks for out of the folder name and shows nothing for anything else.
So a publish can succeed, report the documentation as copied, and the
Launcher card still opens nothing. Name it after the folder.


=================================================================
8. WHEN SOMETHING GOES WRONG
=================================================================

  A build fails with a missing module at run time
      Something the automatic collection did not see. Add it to
      hidden_imports, or the whole package to collect_all, in the
      project's settings file.

  A build fails saying it cannot overwrite the version folder
      OneDrive has marked the files read-only. The Builder deletes
      the folder first to avoid exactly this; if it still happens,
      close whatever has a file in there open.

  A published program starts but cannot find its picture or sound
      The runtime folder did not travel. Check that the folder is
      next to the sources, and that the publish did not report
      deleting it as a leftover.

  A publish skipped a program
      Somebody is running it. Repeat the publish later; nothing was
      half-written.

  The Launcher shows no documentation for a program I just published
      The naming rule in section 7.

  The Launcher shows no "Details" button at all
      Three things have to be true, and all three are easy to miss:
      the detailed file has to exist in the repository, it has to
      have been published (Copy, or "ReadMe only" — writing it in
      the repository does nothing to the share on its own), and the
      LAUNCHER ITSELF has to be a build new enough to know about the
      button. An old Launcher exe on the share has no Details button
      in it, however many files you publish.

  The Launcher shows an old version as current
      The shared version list was not updated, or you published to
      one destination and are looking at the other.

  A helper did not build
      It only logs a warning, by design. Look in the log for the
      reason; the application itself is built and fine.

  An old version will not run from the Launcher
      Its archive folder is not a complete snapshot. Press Fix, then
      look inside the folder — it should contain the program and its
      source files under their real names.

  I cannot find the projects at all
      The sources root is wrong. "Set paths".

-----------------------------------------------------------------
