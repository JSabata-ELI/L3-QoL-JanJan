Dev Tools — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-10-16 (PySide6 version)
  (dev_tools.py 167 lines, dt_core.py 2033, dt_common.py 582,
   dt_builder.py 698, dt_copy.py 602; 4082 lines in all)
  Program folder: 304 kB in 14 files (without testing/, build output and caches)
-----------------------------------------------------------------
Short version: ReadMe Dev Tools.txt  ("ReadMe" button)
Code map:      STRUCTURE.md  (and dev_tools_structure.md, a function map)
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

The window opens at its normal size (1280 x 860, not maximised),
which fits the project lists and the log below them. It looks like Image Tools: each part
of a tab is a section with a coloured title bar — Root folder (blue),
the program list (green), Selected program (purple), Destinations
(red), Log (teal).


=================================================================
2. THE FOUR PATHS
=================================================================

  Sources root   the repository holding the source folders
  Dist root      where finished builds are written
  Scratch        publishing destination one; also holds the shared
                 version list
  SharePoint     publishing destination two

Diagnostic and Diagnostic Webex listener run on one PC only and are
never copied to Scratch — copy, ReadMe copy and Deploy Libraries all
skip Scratch for them, even when it is ticked. They go to SharePoint
only.

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

    Each row shows the next version, which is the last number stepped
    up by one. Click a row and the "Selected program" panel on the
    right shows the version it was last built as (from the shared
    version list, else from the build output folder) and the next one,
    which you can type over when a change deserves a bigger step. The
    same panel sets the Group and has "Open ReadMe", which opens the
    program's short document (and offers to create it when there is
    none).

    Changing a group, or switching "Show ignored", moves the rows in
    place — the list is not redrawn and does not flicker.

    THE MOUSE WHEEL never changes a drop-down list you have not clicked
    into. Over Group here, or over a Version in the Copy Manager list,
    the wheel scrolls the list underneath; only the box you clicked
    into (it has the keyboard focus) steps its value with the wheel.
    Scrolling the Copy Manager list used to change the version that
    would be copied without anybody noticing.

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
      path, not from inside a bundle. It is ALSO put inside the bundle
      and baked into the program file itself, so the title bar, the
      taskbar button and the .exe in Explorer all have it even when
      the file beside the program cannot be read.

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

      - Big libraries (numpy, scipy, matplotlib, pandas and the like)
        are NOT collected whole any more. Until October 2026 they
        were, whenever a program imported them; that made the build
        tool examine every part of every one of them and made each
        build several times longer. The build tool's own knowledge of
        these libraries brings in what they need. If a built program
        stops with a missing-module error, name that module in the
        program's settings file (see 3.5) — only as a last resort the
        whole package.

      - A package collected whole (because its settings file asks for
        it) also drags in its own test suite, which is large and
        useless. Those are excluded again — and only the test suites,
        never the similarly named parts that some libraries genuinely
        use at run time.

      - Several programs ticked at once are built side by side — up
        to three at a time, fewer on a machine with few processor
        cores or little free memory. While they run together, each
        line in the log starts with the program's name in brackets.
        With "Copy after build", each program is copied as soon as
        its own build is done, but only one copy runs at a time. The
        final report lists the programs in the order they were
        ticked, whichever finished first.

      - Every build's time is split into four parts and shown in the
        log ("Built: ... (95s: analysis 70s, package 15s, collect 8s,
        finish 2s)"): analysis is the build tool finding every module
        the program uses, package is packing them into the program
        file, collect is copying the libraries into the support
        folder, finish is the work after that (sources, pictures,
        ReadMes). Each build also adds one line to build_times.log
        next to Dev Tools, with the date, the four parts and the copy
        time, so it can be seen later what made a build slow.

      - The version folder is deleted before the build. OneDrive
        marks synced files read-only and the build tool refuses to
        overwrite them.

      - The build runs without a console window: its output goes to
        the log only, so nothing flashes up or takes the keyboard
        while a build runs.

      - Afterwards the built program is moved up one level into the
        version folder, renamed to include its version, and the new
        version is written into the shared version list on Scratch.

      - Finally the built program file is checked for an icon of its
        own. The log says "Icon: the exe carries its own icon" when it
        does, and a Warning when it does not — then its taskbar button
        would be a blank window. The usual cause is that icon.ico is
        missing from the project folder. The same check runs for
        helper programs.

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
    project's line has: a tick box and its own next version. Click it
    and the panel on the right shows the helper — its name, the script
    it is built from, its last and its next version — so the version
    you build it with is set there, separately from its parent's, and
    "Open ReadMe" opens the helper's own document. Group stays empty
    and locked: a helper is not sorted into the sections, it follows
    its parent.

    A helper's documentation is named after the HELPER, not after the
    folder it lives in — ReadMe_<helper name>.txt and
    ReadMe_<helper name>_Full.txt, both in the parent's folder. That
    is the name the Launcher looks for, because the helper is
    deployed as a program of its own; the parent's ReadMe would never
    be found under it. The build copies both into the helper's version
    folder, and says so in the log when one of them is missing.

    The helpers open on any click on the project: on its line or on
    its tick box. Only the arrow in front of the name closes them
    again. Nothing else opens them — changing a group, for instance,
    leaves every project folded or unfolded as it was. The point is that the helpers are usually the
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

      Published INSIDE the application's folder, though: a helper
      lands in Software\<Application>\<Helper>, not beside the
      application in the top Software folder, because it is a part of
      the application and not a program of its own. A helper still
      standing in the top folder from an earlier publish is carried
      into its new place the first time it is published again, with
      its archive and its ReadMe. The Launcher lists the top folder
      only, so a helper no longer gets a card there.

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

    Programs build in the order you ticked them. Each ticked line
    shows its place in the queue as a number left of the tick box,
    e.g. "2."; the numbers stand in one column and have their own
    space, so nothing in the line moves when one appears; unticking
    one moves the ones behind it up, and ticking it again puts it at
    the end. Ticking a project queues its helpers right behind it.
    Select all keeps whatever you had ticked first and adds the rest
    in list order; Clear empties the queue.

    With "Copy after build" ticked, each program is copied to the
    destinations ticked in the Copy Manager as soon as its own build
    has finished — and only then does the next program start
    building. So the first program you clicked is out on the shares
    while the others are still building. A program whose build fails
    is not copied; the queue carries on with the next one. The
    destinations are read when you press BUILD selected, so change
    them before, not during, the run. The build report, the copy
    report and the overall report come at the end, as before. If no
    destination is ticked, nothing is copied and the log says so.

    With "Copy after build" off, the programs are only built.

    "Replace icons without asking" (next to it, remembered between
    runs): when a destination already has a different icon.ico, it
    is overwritten straight away instead of showing the Icon
    conflict window. This also applies to Copy in the Copy Manager.


=================================================================
4. THE COPY MANAGER
=================================================================

4.1 THE LIST

    Every program in the build output, with its available versions
    and the version last published. The Status column says NEW in
    green when the newest build is newer than what was published, UTD
    in red when the published version is up to date, and a grey NEW
    for a program that has never been built. "Select new" ticks
    exactly the green ones and sets them to their newest version
    (when there are none, the log says so).

    After a copy, and after a build, the rows update in place: a new
    version appears in its Version list and the status and "last
    deployed" follow, while the ticks, the chosen versions and the
    scroll position stay. Only Refresh rebuilds the list — use it when
    a new program folder has appeared.

    "Last published" is remembered per computer, not per share. It
    answers "have I published this" rather than "is this the newest
    on the share".

    HELPER PROGRAMS ARE ON THIS LIST TOO, exactly as in the Builder:
    the program that carries them gets a small arrow, and behind it
    each helper is an indented line of its own with its own tick box,
    its own version to pick and its own status. They are found the
    same way the Builder finds them — from the build settings of the
    program they belong to — so nothing has to be registered here.

    Ticking the program ticks its helpers too, and opens the list so
    that what went along is visible. It is a push, not a lock: untick
    a helper afterwards and it stays unticked, which is how you
    publish only the application. A tick survives folding the list
    away.

    Until this was added, a helper had no line here at all, because it
    has no source folder of its own — so "Copy after build" reported
    it as "NOT copied — not selected in Copy Manager" and it had to be
    copied by hand.

4.2 WHAT PUBLISHING WRITES

      <destination>/<program>/
          the program file, with its version in the name
          the source files
          the documentation
          the icon
          the runtime folders the build brought along
          archive/
              vX.Y.Z/  the previous version, complete
          <helper>/    a helper program of this one (section 3.6),
                       laid out the same way, with its own archive

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
    program came up without its alarm picture. A helper program's
    sub-folder (Diagnostic\Diagnostic Webex listener) is kept for the
    same kind of reason: the helper is published on its own, so it is
    never part of the application's new version, and without this
    exception every publish of the application wiped its own helper.

4.4 WHERE THE DOCUMENTATION IS TAKEN FROM

    In this order:

      1. the version folder — the Builder puts it there
      2. THE SOURCE FOLDER IN THE REPOSITORY — the live one
      3. the build output folder for that program
      4. for a helper: the folder of the program it belongs to, where
         its own ReadMe_<helper name> documents live
      5. the copy already on the destination
      6. nothing found: skip, with a warning

    Step 2 is the important one. Builds made before the Builder
    started copying documentation have none in their version folder,
    and without that step such a publish shipped no documentation at
    all — so the Launcher card had no button.

4.5 THE OTHER BUTTONS

    Copy ReadMe only
        Publishes just the documentation files — both the short one
        and the detailed one. For fixing a typo without a rebuild and
        without touching the program itself. This is also the button
        to press after writing documentation for a program you do not
        want to rebuild.

    Fix
        Repairs old archive folders — section 5.

    Deploy Libraries
        Pushes the shared support folder — built by the Internal
        Builder project in the Builder tab — into the ticked programs
        on Scratch. Files that did not change are left alone; files
        that are no longer part of it are removed. A progress bar runs
        under the buttons. This is a large upload and every program
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

    All of this is done for every program folder on the destination
    AND for the helper folders inside them (Diagnostic\Diagnostic
    Webex listener): a helper has its own program file and its own
    archive, so it needs the same repair.


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

  A build ends with "No such file or directory" somewhere in Temp
      The scratch folder the build was using disappeared while it was
      still working. Every build now gets its own, named after the
      program, so two Builder windows can no longer delete each
      other's; leftovers older than a day are swept on the next
      build. If it still happens, something outside the Builder is
      cleaning Temp while a build runs.

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
      have been published (Copy, or "Copy ReadMe only" — writing it in
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
