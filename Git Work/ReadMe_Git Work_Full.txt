Git Work — Detailed information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
Verified against source: 2026-08-24  (git_work.py, 1988 lines)
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
      Two people (or the same person on two lines of work) changed
      the same lines. Git will not guess which version wins;
      somebody has to decide. This tool asks you - see section 3.9 -
      and hands the file-by-file work over to VS Code if you would
      rather do it there.

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

3.1 WHAT TO DO NOW
    The top box is the whole tool in one line: it reads the real
    state and says the single next thing to do, with one button that
    does exactly that. Used this way, none of the git words below
    have to be understood at all - keep pressing the top button
    until it says there is nothing to do.

    What it can say, in the order it decides:

      Pick the folder with your project
                     no project chosen yet.
      Tell git who you are
                     name and e-mail are missing; the button opens
                     the same dialog as Identity > Change...
      You are not on any line of work (red)
                     nothing can be saved like this. The button
                     switches to the branch shown in the box.
      You are standing on the shared 'main' (red with changed files)
                     personal work does not belong there. The button
                     makes your own line of work, and your changes
                     come with you. With nothing changed it either
                     opens the list of your own lines, or - on a
                     freshly copied project, where you have none -
                     offers to make one. It never points you at
                     somebody else's line as if it were yours.
      The server has N newer commit(s)
                     somebody (or you, from the other computer) has
                     saved something. Take it first - the button
                     does it.
      N file(s) changed and not saved yet
                     write one line about what you changed and the
                     button saves it and sends it up. Press it with
                     an empty message box and it puts the cursor in
                     the box instead of doing something half-right.
      N commit(s) are still only on this PC
                     saved but not backed up; the button sends them.
      The shared 'main' has moved on by N commit(s)
                     THE step everybody forgets. The button brings
                     the shared line into yours. Doing it regularly
                     is what stops "Merge into main" from turning
                     into forty clashes six weeks later.
      Your work is N commit(s) ahead of the shared 'main'
                     ready to be published; the button publishes it
                     (and asks first).
      Everything is saved, backed up and shared
                     nothing to do; there is no button.

    The wording is deliberately in plain words - "line of work"
    rather than branch, "send it up" rather than push. The boxes
    below keep the git names, for when you want to do something the
    panel is not offering.

3.2 REPOSITORY
    The current project folder, with two buttons: "Change..." to
    pick another one, and "Open in VS Code" to open this one there.
    Any folder that is a git repository is accepted — you can point
    it at a sub-folder and it will find the top of the project by
    itself.

3.3 IDENTITY (WHO YOUR COMMITS ARE FROM)
    Shows the name and e-mail currently in use. "Change..." edits
    them. These are global to this computer, not per project.

3.4 BRANCH AND STATUS
    The first line of this box is the one that tells the truth:

      You are on: <name>     where git really is right now
      You are on: NO branch  (red) you are on no line of work at
                             all; nothing can be saved or sent up
                             until you pick one and Switch
      You are on: <name> - the box shows '<other>'; you are not
      there until you click Switch
                             (orange) you have picked something,
                             but you have not moved yet

    The dropdown below it is a choice, not a move. It lists the
    lines of work on this computer and, after the separator, the
    ones that exist only on the server, shown as "origin/name".
    Picking such a name and clicking Switch makes your own copy of
    it here (you are asked first) - that is how you get to a
    colleague's line of work, or back to your own after a fresh
    copy of the project.

    A pick is kept. Every action ends with a re-read of the state,
    and that re-read used to reset the dropdown to the current line
    of work, so a name you had chosen quietly disappeared and
    Switch then did nothing at all. Now your choice stays in the
    box until you switch to it or pick something else.

    After a Switch the program asks git where it actually ended up
    and compares. If it is not the line of work you asked for, it
    says so instead of reporting success.

    Three buttons act on the selection:

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

3.5 COMMIT MESSAGE
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

3.6 ACTIONS
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

3.7 DANGER ZONE
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

3.8 LOG
    Every command that was run and everything it printed, colour
    coded: the command itself, its output, information, success,
    warning, error. When you need to ask somebody for help, this is
    the text to copy.

3.9 WHEN TWO VERSIONS CLASH ("which one wins?")
    Git folds two lines of work together by itself as long as they
    touched different places. Where both changed the same lines it
    stops, because choosing wrong would silently delete somebody's
    work.

    When that happens during Pull, Sync, "bring the shared line in"
    or Merge into main, a window opens and asks. It shows:

      - which files it could not fold, and what happened to each
        one ("changed on both sides", "changed on 'main', deleted on
        'Mouka'", "added on both sides, differently", ...)
      - what was committed on each of the two lines of work since
        they last agreed - dates and your own messages, so you can
        see which side is the newer one

    Three answers:

      Keep the '<other line>' version
                 the clashing files are taken from the line being
                 folded in, the fold is finished, and the work
                 carries on where it stopped (with Merge into main
                 that means: publish main, come back to your own
                 line and level it up).
      Keep the '<this line>' version
                 the same, the other way round: the files stay as
                 they are here and what the other side did to them
                 is ignored.
      Leave it - I'll do it in VS Code
                 nothing is changed. The stopped fold stays open so
                 you can go through the files by hand.

    Only the files in the list are affected; everything git folded
    on its own stays folded. And nothing is destroyed: the version
    you drop stays in the history of the line of work it came from,
    so it can be fetched back later. The merge is recorded with a
    message that says which side was kept and which files it
    covered.

    A word of warning about "keep my side": it is the right answer
    when the other side is old work that has since been redone here.
    It is the wrong answer when the other side is somebody else's
    new work - in that case answer "Leave it" and go through the
    files, or you will quietly undo their day.



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

  - Conflicts are not guessed. If a fold cannot be done cleanly the
    tool stops and asks which version wins, showing what each side
    did (section 3.9). It never picks for you, and whichever side is
    dropped stays in that line of work's history. "Leave it" hands
    the files to VS Code, which can show both versions side by side.

  - Switching with unsaved work always asks. It never silently puts
    your changes on the shelf.

  - The shared line is called out. Standing on main is shown in red
    with "(shared branch!)" next to it.

  - Delete refuses to lose work. Only the red section can force it.

  - Long operations run in the background, so the window does not
    freeze, and the buttons are disabled while one is in progress so
    two operations cannot overlap.


=================================================================
5. MORE THAN ONE PERSON
=================================================================

  This is built for a small team sharing one project, and for one
  person using two computers - which behaves exactly like two
  people. The rules are the same in both cases.

  Everybody has their own line of work.
      Yours is where you save all day. "main" is the shared one, and
      the only thing that ever goes into it is a finished piece of
      work you publish on purpose. The top panel refuses to let you
      settle down on main: standing there with changed files, it
      offers to make you your own line and bring the changes along.

  Check the "You are:" line on a shared computer.
      Your name and e-mail are stored per Windows account, and they
      are stamped on everything you save. If several people use one
      login, the second person's work will be signed with the first
      person's name until somebody presses Identity > "Change...".
      The window shows who it thinks you are at all times, on
      purpose.

  Take first, then send. Always that order.
      When the server has something you do not, the panel says so
      and offers to fetch it before anything else. Sending first is
      refused by the server anyway, and the message it gives is not
      obvious the first time.

  Bring the shared line into yours regularly.
      One button, near the top: "Bring 'main' into '<your line>'".
      Every week you skip it, the two versions drift further apart,
      and the clash list on the day you publish grows. This one
      habit is the difference between publishing in ten seconds and
      spending an afternoon on it.

  When two versions clash, look at WHO wrote the other side.
      The question (section 3.9) lists the commits of both sides
      with the author's name. "Keep my version" is the right answer
      when the other side is your own old work. It is the wrong
      answer when the name is a colleague's and the date is
      yesterday - that would quietly undo their day. Pick "Leave
      it" and go through the files instead.

  Never force-push a line somebody else uses.
      Force push is in the red zone and warns extra loudly on the
      shared line, because it deletes whatever the server had. On
      your own private line it is merely rude to your own history.

  If the server refuses to be written to directly.
      Some projects are set up so that nobody can save straight into
      the shared line. You get a plain message saying so; work on
      your own line and let whoever looks after the project publish
      it.

  Two programs, one project.
      Git Work, VS Code and a command line all reach for the same
      folder. If another one is in the middle of something you get
      "Another program is working with this project right now" -
      wait a second and press the button again. Nothing is broken.

  What is per person and what is shared.
      Per Windows account: the chosen project folder, your name and
      e-mail. Shared with everybody: the project itself and the
      server. Git Work stores nothing inside the project, so it
      cannot get in anybody's way.


=================================================================
6. THE TWO THINGS THAT WILL CONFUSE YOU ONCE
=================================================================

  "N ahead / M behind"
      You and the server have both moved on since you last agreed.
      Pull first, then push. Doing it the other way round is
      refused, which is the correct behaviour, but the error is not
      obvious if you have not seen it before.

  "detached HEAD" / "You are on: NO branch"
      You are looking at an old point in the history rather than
      standing on a line of work. Anything you save here is easy to
      lose. Pick a branch in the dropdown and press Switch; Pull,
      Commit + Push, Sync and Merge say this and stop instead of
      failing halfway through.


=================================================================
7. WHEN SOMETHING GOES WRONG
=================================================================

  Push refused
      The server has steps you do not have. Pull, then push. The log
      says so.

  A conflict after Pull or Merge
      A window asks which version wins and shows what each side did
      (section 3.9). Answer it, or choose "Leave it", press "Open in
      VS Code", go to Source Control and work through the files it
      lists. Come back here afterwards.

  The merge into main stopped on conflicts and it is a long list
      That means the two lines of work have not agreed for a long
      time - usually because something was once saved straight into
      main and never brought over. Either answer the question
      (section 3.9), or, if the files need to be gone through one by
      one, do that on your own line of work instead of on main:
      "Open in VS Code", Source Control, and merge main into your
      branch there. Once your line already contains main, "Merge
      into main" has nothing left to decide.

  "Another program is working with this project right now"
      VS Code, a second Git Work window or a command line is in the
      middle of a git command. Wait a second and press the button
      again. If nothing at all is running, delete the file
      .git\index.lock inside the project.

  Everything I send up is refused
      Read the log. "The server does not allow saving straight into
      this branch" means the project is set up so the shared line is
      written to only by its owner - work on your own line. A
      sign-in message means Windows Credential Manager or VS Code
      needs your GitHub login again.

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

  The branch I pick keeps turning into a different one
      Read the first line of the Branch box. If it says "You are
      on: NO branch", you were on no line of work, and the dropdown
      used to fill itself with a name git only invents for that
      state - so the box showed something nobody chose and Switch
      did nothing. Pick your branch, click Switch, and check that
      the first line now names it. If it does not, the log says
      what git actually did.

  My branch is not in the list
      It exists on the server but not yet on this computer. Press
      "Fetch", then look below the separator in the dropdown for
      "origin/<your branch>". Pick it and Switch - you are asked
      whether to make your own copy of it here.


=================================================================
8. NOTES
=================================================================

  - What is remembered per user: only the chosen project folder, in
    your own application data. Nothing about the project itself.
  - Git must be installed and reachable on this computer — every
    action is a real git command, shown in the log.
  - In the Launcher the program sits under the "Personal" group,
    next to Dev Tools. Build it with Dev Tools so it appears there.
  - Drop an icon.ico into the folder to give the window an icon.

-----------------------------------------------------------------
