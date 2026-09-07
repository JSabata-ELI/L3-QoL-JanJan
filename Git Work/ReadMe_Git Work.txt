Git Work — Short information
Created by Jan Moucka, ELI Laser

Bugs / suggestions: jan.moucka@eli-laser.eu
-----------------------------------------------------------------
Detailed version: ReadMe_Git Work_Full.txt  ("Details" button)
-----------------------------------------------------------------

WHAT IT DOES

  Everyday work with the shared code archive from one window, with
  no command line. When a step fails it stops and says in plain
  words what to do next — usually "open it in VS Code".


THE IDEA IN ONE PARAGRAPH

  The code lives in two places: a copy on your computer and a copy
  on the server. Working means keeping those two in step: take what
  is new on the server, save your own work as a numbered step, send
  it up. Every button here is one of those three moves, or a
  combination of them. Nothing happens to the server until you press
  a button that says it will.


THE WINDOW, TOP TO BOTTOM

  What to do now    The top box is the short way to use the whole
                    program: it reads the real state and says, in
                    one sentence, the single next thing to do — with
                    one button that does exactly that. Keep pressing
                    that button until it says "Everything is saved,
                    backed up and shared. Nothing to do." None of
                    the git words below have to be understood for
                    this to work. It also names the step everybody
                    forgets: "the shared main has moved on — bring
                    it into your line first", which is what keeps a
                    later publish from turning into a pile of
                    clashes.

  Repository        Which project folder you are working on. It is
                    remembered for next time. "Open in VS Code"
                    opens the same folder there.

  Identity          The name and e-mail your saved work is signed
                    with. Asked once, before your first save.

  Branch and status The top line always says where you really are
                    ("You are on: Mouka"). The box below it is what
                    you have picked — picking is not moving, you get
                    there by clicking "Switch", and until you do,
                    the top line says so in orange. Branches shown
                    as "origin/name" exist only on the server; pick
                    one and Switch makes your own copy of it here.
                    If the top line says "You are on: NO branch" in
                    red, nothing can be saved or sent up until you
                    pick a branch and Switch.
                    The status line adds a sentence about what to do
                    — for example "3 behind - Pull". "Fetch" asks
                    the server what is new without changing any of
                    your files; "Refresh" only re-reads what is
                    already known.

  Commit message    A short note about what you changed. It comes
                    prefilled with today's date, which is a valid
                    note on its own. "More" opens a bigger box. An
                    empty box means "save nothing, just send up what
                    was already saved" — with changed files waiting
                    you are asked before that happens.

  Actions           Pull, Commit + Push, Merge into main, Sync,
                    Restore stash.

  If two versions clash
                    When both lines of work changed the same lines,
                    git stops - it will not guess. A window then
                    asks which version wins. It lists the files it
                    could not join, says what happened to each, and
                    shows what was saved on each side since they
                    last agreed, so you can see which is the newer
                    work. You answer "keep this side", "keep the
                    other side", or "leave it, I'll do it in VS
                    Code". Only the listed files are affected, and
                    the version you drop stays in its own line's
                    history - nothing is lost.

  Danger zone       Four buttons that can destroy work. Marked in
                    red and confirmed first; three of them make you
                    type a word before they run.

  Log               Exactly what was run and what came back.


THE NORMAL ACTIONS

  (The "What to do now" button picks the right one of these for you.
  The buttons stay there for when you want a particular one.)

  Pull             bring your line of work up to date from the
                   server.
  Bring main in    take what has appeared on the shared line into
                   yours. Offered by the top button whenever the
                   shared line has moved on; do it often and
                   publishing stays painless.
  Commit + Push    save your changes as one step (needs a note) and
                   send them up.
  Merge into main  publish your work into the shared line everyone
                   uses. Asks first.
  Sync             one click for the usual sequence: ask the server,
                   save, take, send.
  Restore stash    bring back changes that were put aside when you
                   switched to another line of work.


WORKING WITH SOMEBODY ELSE (or on two computers)

  - Your own line of work is where you save all day; the shared
    "main" only ever gets finished work you publish on purpose. The
    top panel will not let you settle down on the shared line.
  - Take first, then send. The panel always says which comes now.
  - Press "Bring main into <your line>" whenever the panel offers
    it. Skipping it for weeks is what turns publishing into an
    afternoon of clashes.
  - When two versions clash, the question shows WHO wrote each side.
    "Keep my version" is right for your own old work, wrong for a
    colleague's yesterday — then pick "Leave it" instead.
  - On a shared computer, check the "You are:" line before saving:
    the name and e-mail belong to the Windows account, not to you.
  - Two programs at once (Git Work + VS Code) is fine; if one is
    busy you get a plain "another program is working with this
    project" and just press the button again.


WHAT KEEPS YOU SAFE

  - It never stops on a hidden password question.
  - It warns you when you are about to save straight onto the shared
    line of work.
  - A real clash between your changes and somebody else's is handed
    over to VS Code instead of being guessed at.
  - Deleting a line of work refuses if it still holds work that was
    never published. The forced version is in the red section.

-----------------------------------------------------------------
