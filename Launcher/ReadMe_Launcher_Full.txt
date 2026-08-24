Launcher — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (l.py, 1586 lines)
-----------------------------------------------------------------
Short version: Readme Launcher.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. THE IDEA
=================================================================

There is no installer anywhere in this collection, and no registry
entry, no start-menu shortcut, no update service. A program is a
folder: a starter file, a support folder, an icon, its documentation,
and an archive of its older versions.

The Launcher is the consequence of that. It looks at a folder full of
such folders and shows you what is there. Nothing is registered with
it, nothing has to be added to a list. Copy a new program folder onto
the share and the Launcher shows it at the next scan; delete it and
it is gone.

The advantages are that a deployment is a file copy, a rollback is
running an older file, and nothing can be half-installed. The price
is that the Launcher has to be told which folder to look at, and has
to be tolerant of everything it finds there.


=================================================================
2. THE FOUR SOURCES
=================================================================

The same programs exist in several places, and the top row picks
which one you are looking at:

  Lab - Scratch
      The copy the lab actually runs from, on the scratch share.
      This is the authoritative one for anybody using the machines.

  Office - Scratch
      Your own path to the same kind of share from the office
      network. Set it yourself; it is remembered.

  Office - SharePoint
      The copy that lives on SharePoint. Set it yourself.

  Office - Programs
      The working copy on this computer, laid out differently — as
      build output rather than as a deployed folder (see section 4).

Selecting a source scans it and rebuilds the whole window. The active
source is highlighted.

The two configurable paths are set with the gear button and stored
per user, together with your acknowledged versions and any groups you
have moved programs into.

On a lab machine the office sources are disabled outright, because
they are not reachable from there and a click would only produce a
long wait for a timeout.

A WARNING ABOUT UNREACHABLE SHARES: a share whose host is switched
off does not refuse quickly — Windows waits, and roughly three
quarters of a minute can go by before the scan gives up. If the
Launcher seems frozen straight after you picked a source, that is
almost always what is happening. Wait it out; it recovers.


=================================================================
3. WHAT A CARD SHOWS
=================================================================

  THE BIG BUTTON
      The program's name and version. Click it to start the current
      version. When a newer version is on disk than the one you last
      used, the button turns amber and gains an up arrow.

  ReadMe
      The short description — what the program is for and how it
      behaves, in a couple of screens.

  Details
      The long description: every control, every setting, the file
      formats, and what to do when it goes wrong. The button only
      appears for programs that have one, and when it is missing the
      ReadMe button simply takes the whole width.

      If no program shows this button, the documentation has not been
      published to the folder you are looking at — see the note at
      the end of section 10.

  The tick
      "I have seen that there is a new version." Clears the amber
      without starting anything, and the acknowledgement survives a
      restart. Only shown while an update is pending.

  The folder
      Opens the program's folder in Explorer.

  The arrow down
      A list of older versions, newest first. Click one to run it —
      see section 5.

  RIGHT-CLICK
      Move this program into a different group, or reset it to its
      default one. Your choice is stored per user, so you can
      rearrange the window without affecting anybody else.


=================================================================
4. THE TWO FOLDER LAYOUTS
=================================================================

The Launcher understands two ways a program can be laid out, because
the deployed copies and the build output are not arranged the same
way.

  DEPLOYED (the shares)
      <Program folder>\<Program>.exe
      <Program folder>\_internal\
      <Program folder>\archive\...

  BUILD OUTPUT (this computer)
      dist\<Program>\vX.Y.Z\<Program>.exe

In the second case the highest version folder wins. When a folder
holds several starter files, the one whose name matches the program
is preferred, then one containing the version number, then the first
one alphabetically.

Two folder names are always skipped when scanning: the archive folder
and the build-output folder, so neither is mistaken for a program.


=================================================================
5. THE OLDER-VERSION SWAP
=================================================================

This is the least obvious thing the Launcher does, and worth
understanding before you use it.

An old starter file cannot simply be run from the archive folder. It
needs the support folder — the large one with the Python runtime in
it — and there is only one of those, sitting next to the current
starter. Copying it per archived version would multiply hundreds of
megabytes by every version ever kept.

So the Launcher swaps instead:

  1. The current starter (and its source snapshot, if there is one)
     are MOVED into a temporary folder inside the archive.
  2. The chosen archived pair is copied into the program folder.
  3. The old version is started, and the Launcher waits for it to
     finish.
  4. When it exits, the current files are moved back — this happens
     even if the run failed.

Two things follow from that:

  - While an old version is running, the program folder holds the old
    starter. Anybody else launching that program from the same share
    at that moment gets the old version. Keep old-version runs short.

  - If the program is killed rather than closed — the machine is
    switched off, the process is ended in Task Manager — step 4 never
    happens and the temporary folder is left behind. The Launcher
    detects that: it refuses to launch that program again, and offers
    to put the current files back. That check runs after every scan,
    and on demand with the broom button.

If the swap cannot be done at all, the Launcher falls back to running
the source snapshot directly, or to starting the archived file where
it lies, which may or may not work depending on the program.


=================================================================
6. VERSIONS AND THE ARCHIVE
=================================================================

Versions are read out of file and folder names. Two spellings of an
archived version exist, and both are accepted:

  archive\vX.Y.Z\<Program> vX.Y.Z.exe
      The current form: one folder per version, holding a runnable
      snapshot. Shown in the list as just the version.

  archive\<Program> vX.Y.Z__20260101_120000.exe
      The older flat form, with a timestamp in the name. Shown as the
      version followed by the date and time.

Inside a version folder there is one entry in the list, not one per
file — a "(2)" duplicate copy is ignored in favour of the canonical
name.

The list is always sorted by version, newest first, and the current
versions come before the archived ones.


=================================================================
7. THE UPDATE HIGHLIGHT
=================================================================

Every ten seconds the Launcher re-checks the versions on disk, in the
background. A program is highlighted when the version on disk differs
from the version you last acknowledged.

Two things clear it:

  - starting the program, which acknowledges it automatically, or
  - pressing the tick, which acknowledges it without starting.

The acknowledgement is stored per user, so the highlight is about
you, not about the share. Somebody else on the same share still sees
their own.

At startup everything is re-checked, so a version that changed while
the Launcher was closed is highlighted the next time you open it.


=================================================================
8. THE GROUPS
=================================================================

  Scripts                  the everyday tools
  Parts                    pieces of larger programs
  External                 everything not otherwise classified
  In progress              not finished
  Not working correctly    known broken
  Personal                 build and deployment tooling

Click a header to expand or collapse the section. The number of cards
per row follows the window width.

A program that is not in any of the lists inside the Launcher lands in
"External". That is not a statement about the program — it just means
nobody has classified it yet. Right-click to move it, or add it to
the list in the source.


=================================================================
9. THE THREE SMALL BUTTONS
=================================================================

  Notes (clipboard)
      Opens the shared notes file on the current share, the one
      everybody writes into.

  Set paths (gear)
      Your two office paths. Stored per user.

  Clean (broom)
      Two housekeeping checks, on demand:
        - version files with a timestamp left sitting in a program
          folder instead of its archive folder — it offers to move
          them;
        - programs left in a half-finished version swap — it offers
          to restore them.
      Both checks also run by themselves shortly after every scan, so
      the button is only needed when you want them immediately.


=================================================================
10. THE DOCUMENTATION NAMING RULE
=================================================================

The Launcher builds the filename it looks for out of the folder name.
Case, spaces, underscores, hyphens, dots and the extension are all
ignored, but the words have to match:

  ReadMe_<folder name>          -> the "ReadMe" button
  ReadMe_<folder name>_Full     -> the "Details" button
  (also accepted for Details: ReadMe_<folder>_Details,
   Manual_<folder>)

So for a folder called "Image Tools", both "ReadMe_Image Tools.txt"
and "Readme Image Tools.txt" work, and "README.txt" does not — the
button on that card simply opens nothing, with no error anywhere.
This has caught people out before; if a ReadMe button does nothing,
check the filename first.

AND REMEMBER WHICH FOLDER YOU ARE LOOKING AT. The Launcher shows the
documents that exist in the source you picked at the top. A document
written in the repository is not on a share until it has been
published there (Dev Tools, Copy Manager, "Copy" or "ReadMe only").
So the same program can show a Details button under one source and
not under another — that is the two folders differing, not the
Launcher.


=================================================================
11. WHEN SOMETHING GOES WRONG
=================================================================

  The window is empty after picking a source
      The path does not exist, or holds no program folders. Check the
      path with the gear button.

  It seems frozen for the better part of a minute
      An unreachable share. See the warning in section 2.

  A program refuses to launch, saying a swap is unfinished
      An old version was killed instead of closed. Press the broom
      button and accept the restore.

  A card is amber and I do not want it to be
      Press the tick.

  A ReadMe or Details button does nothing
      The file is named something the Launcher does not look for.
      Section 10.

  A program is in the wrong group
      Right-click the card and move it. If it should be in that group
      for everybody, it needs to be added to the list in the source
      and the Launcher rebuilt.

  Windows asks whether I really want to run this file
      Files on a network share are treated as untrusted. The
      Launcher already suppresses that dialog when it starts a
      program; you will still see it if you double-click the file in
      Explorer yourself.

  An old version behaves oddly
      It is running against the current support folder, not the one
      it was built with. That is the trade-off described in section
      5. If it will not run at all, the two are too far apart.

-----------------------------------------------------------------
