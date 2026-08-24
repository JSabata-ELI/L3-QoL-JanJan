Git Work — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-19  (git_work.py, 1218 lines)
-----------------------------------------------------------------
Short version: ReadMe_Git Work.txt  ("ReadMe" button)
Code map:      STRUCTURE.md
-----------------------------------------------------------------


=================================================================
1. THE VOCABULARY, ONCE
=================================================================

The tool underneath is called git. It has its own words, and they
appear on the buttons, so they are worth ten lines of explanation.

  repository ("repo")
      One project folder, with its whole history kept inside it.
      There is a copy on your computer and a copy on the server.

  commit
      One saved step: a snapshot of the project plus a short note
      saying what changed and who saved it. History is a chain of
      commits. A commit is local until you push it.

  push / pull
      Push sends your commits up to the server. Pull brings the
      server's commits down to you.

  fetch
      Ask the server what it has, without changing any of your
      files. Purely informational and always safe.

  branch
      A parallel line of work. Everybody has their own, so two
      people can work on the same project at the same time without
      standing on each other. "main" is the shared one that the
      finished work ends up in.

  merge
      Fold one line of work into another.

  conflict
      Two people changed the same lines. Git will not guess which
      version wins; somebody has to decide. This tool hands that
      decision to VS Code.

  stash
      A temporary shelf. Changes you have not saved yet can be put
      on it, so you can switch to another line of work, and taken
      back off afterwards.


=================================================================
2. FIRST RUN
=================================================================

  1. Repository. The tool guesses the project folder from where it
     is installed and remembers whatever you pick after that. Use
     "Change..." if you work on more than one project. Your choice
     is stored per user, so it does not affect anybody else.

  2. Identity. Before your first save it asks for your name and
     e-mail. These are written into git's own settings on this
     computer, which means VS Code and any command line you open
     later will use exactly the same identity — no divergence
     between tools.

  3. Line endings. Windows and everybody else disagree about how a
     line of text ends. If git on this machine has not been told
     what to do about it, the log shows a one-off tip asking you to
     run this once, in any console:

         git config --global core.autocrlf true

     Without it, changing one word in a file can show up as the
     whole file having been rewritten, which makes reviewing
     anybody's work impossible. Worth doing straight away.


=================================================================
3. THE WINDOW
=================================================================

3.1 REPOSITORY
    The current project folder, with two buttons: "Change..." to
    pick another one, and "Open in VS Code" to open this one there.
    Any folder that is a git repository is accepted — you can point
    it at a sub-folder and it will find the top of the project by
    itself.

3.2 IDENTITY (WHO YOUR COMMITS ARE FROM)
    Shows the name and e-mail currently in use. "Change..." edits
    them. These are global to this computer, not per project.

3.3 BRANCH & STATUS
    The dropdown lists the local lines of work, with the current one
    selected. Three buttons act on the selection:

      Switch     move to the selected line of work. If you have
                 unsaved changes it offers to put them on the shelf
                 first (Yes) or to cancel so you can save them
                 (No). Nothing is thrown away either way.
      New...     create a line of work starting from where you are
                 now, and switch to it.
      Delete...  delete a line of work, but only if everything in
                 it has already been folded into another. A line
                 still holding unpublished work is refused. Use the
                 red section if you really mean it.

    The status line is written to be acted on, not decoded. What it
    can say:

      clean                          nothing changed here
      N file(s) changed              N files differ from your last
                                     save
      up to date                     you and the server agree
      committed work is up to date - these files are not committed
                                     yet
                                     your saved work matches the
                                     server, but you have changes
                                     that are not saved. (Plain
                                     "up to date" next to a pile of
                                     changed files reads as a
                                     contradiction, so it is spelled
                                     out.)
      N behind - Pull                the server has N steps you do
                                     not have
      N ahead - Push                 you have N steps the server
                                     does not have
      N ahead / M behind - Pull then Push
                                     both, and the order matters
      not on server yet              this line of work exists only
                                     on your computer
      detached HEAD - switch to a branch
                                     you are looking at a point in
                                     history rather than standing on
                                     a line of work. Nothing is
                                     broken; just switch to a
                                     branch.
      (shared branch!)               you are standing on main or
                                     master, where everybody's work
                                     lands. Think before saving.

    Colour: green agrees, orange needs an action, red needs
    attention.

      Fetch    asks the server what is new and updates this line.
               Changes none of your files.
      Refresh  only re-reads what is already known locally. Does not
               contact the server.

3.4 COMMIT MESSAGE
    The note attached to your next save. It arrives prefilled with
    today's date in the form DDMMYYYY, which is the convention used
    in this project, and the date on its own is a perfectly valid
    note — leaving the prefill untouched saves under it.

    "More" swaps the one-line box for a bigger one so you can write
    several lines; "Less" folds it back and shows the first line.
    Folding does not shorten the note — the full text is still what
    gets saved.

    Clearing the box completely means something specific: "do not
    save anything, just send up what was already saved". If you do
    that while you have changed files, you are asked first, because
    it is almost never what you meant — answer No and type a note.
    Should you go ahead anyway, Commit + Push tells you the changed
    files were left behind, rather than reporting nothing to do.

    The box is only refilled with a fresh date after a run that
    really saved something. After a run that saved nothing it keeps
    exactly what it had, so an empty box stays visibly empty instead
    of looking filled in afterwards.

3.5 ACTIONS
    Pull             bring your line of work up to date from the
                     server.
    Commit + Push    save your changes as one step, then send them
                     up. Needs a note.
    Merge into main  send your line of work up and fold it into the
                     shared line. It is done as a clean append when
                     that is possible, and otherwise as a proper
                     fold on your machine. Asks for confirmation
                     first.
    Sync             the whole usual sequence in one click: ask the
                     server, save if you wrote a note, take what is
                     new, send yours up.
    Restore stash    take your changes back off the shelf. The
                     button is only active when there is something
                     on it.

3.6 DANGER ZONE
    Four buttons, in a red box, each with a warning of its own:

      Force push
          Overwrite the server's copy of your line of work with
          yours. Anything on the server that you do not have is
          lost. It is done in the careful variant, which refuses if
          the server moved since you last looked — but on the shared
          line it warns you extra loudly, and there you should not
          do it at all.

      Hard reset to server
          Throw away every local step and every change on this line
          and make it identical to the server. Type RESET to
          confirm.

      Discard ALL changes
          Delete every unsaved change and every new file that was
          never added. Type DISCARD to confirm.

      Force delete branch
          Delete a line of work even though it still holds steps
          that were never folded in anywhere. Those steps are lost.
          Type DELETE to confirm.

3.7 LOG
    Every command that was run and everything it printed, colour
    coded: the command itself, its output, information, success,
    warning, error. When you need to ask somebody for help, this is
    the text to copy.


=================================================================
4. WHAT KEEPS YOU SAFE
=================================================================

  - No hidden password prompt. Git is run with its own prompting
    switched off, so the tool can never sit there frozen waiting for
    an answer to a question you cannot see. If credentials are the
    problem you get an error and an explanation instead.

  - Every failure comes with a next step. A failed push says to pull
    first; a conflict says to open VS Code's Source Control. The
    tool never simply says "error".

  - Conflicts are not guessed. If a fold cannot be done cleanly, the
    tool stops and hands it over. Resolving conflicts wants to be
    done in an editor that can show both versions side by side.

  - Switching with unsaved work always asks. It never silently puts
    your changes on the shelf.

  - The shared line is called out. Standing on main is shown in red
    with "(shared branch!)" next to it.

  - Delete refuses to lose work. Only the red section can force it.

  - Long operations run in the background, so the window does not
    freeze, and the buttons are disabled while one is in progress so
    two operations cannot overlap.


=================================================================
5. THE TWO THINGS THAT WILL CONFUSE YOU ONCE
=================================================================

  "N ahead / M behind"
      You and the server have both moved on since you last agreed.
      Pull first, then push. Doing it the other way round is
      refused, which is the correct behaviour, but the error is not
      obvious if you have not seen it before.

  "detached HEAD"
      You are looking at an old point in the history rather than
      standing on a line of work. Anything you save here is easy to
      lose. Pick a branch in the dropdown and press Switch.


=================================================================
6. WHEN SOMETHING GOES WRONG
=================================================================

  Push refused
      The server has steps you do not have. Pull, then push. The log
      says so.

  A conflict after Pull or Merge
      Press "Open in VS Code", go to Source Control, and work
      through the files it lists. Come back here afterwards.

  "Set your identity" keeps appearing
      The name or the e-mail did not get written. Open "Change..."
      in the Identity box and fill in both fields.

  Every file looks changed after somebody else's commit
      The line-ending setting from section 2. Run the one command
      and the noise disappears.

  Restore stash is greyed out
      There is nothing on the shelf. Either it was already taken
      back, or the changes were saved normally instead.

  I switched branches and my changes are gone
      They are on the shelf. Press "Restore stash".


=================================================================
7. NOTES
=================================================================

  - What is remembered per user: only the chosen project folder, in
    your own application data. Nothing about the project itself.
  - Git must be installed and reachable on this computer — every
    action is a real git command, shown in the log.
  - In the Launcher the program sits under the "Personal" group,
    next to Dev Tools. Build it with Dev Tools so it appears there.
  - Drop an icon.ico into the folder to give the window an icon.

-----------------------------------------------------------------
