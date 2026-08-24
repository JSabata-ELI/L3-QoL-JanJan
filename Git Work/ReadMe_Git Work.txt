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

  Repository        Which project folder you are working on. It is
                    remembered for next time. "Open in VS Code"
                    opens the same folder there.

  Identity          The name and e-mail your saved work is signed
                    with. Asked once, before your first save.

  Branch & status   Which line of work you are on, and a sentence
                    saying what to do about it — for example
                    "3 behind - Pull". "Fetch" asks the server what
                    is new without changing any of your files;
                    "Refresh" only re-reads what is already known.

  Commit message    A short note about what you changed. It comes
                    prefilled with today's date, which is a valid
                    note on its own. "More" opens a bigger box. An
                    empty box means "save nothing, just send up what
                    was already saved" — with changed files waiting
                    you are asked before that happens.

  Actions           Pull, Commit + Push, Merge into main, Sync,
                    Restore stash.

  Danger zone       Four buttons that can destroy work. Marked in
                    red and confirmed first; three of them make you
                    type a word before they run.

  Log               Exactly what was run and what came back.


THE FIVE NORMAL ACTIONS

  Pull             bring your line of work up to date from the
                   server.
  Commit + Push    save your changes as one step (needs a note) and
                   send them up.
  Merge into main  publish your work into the shared line everyone
                   uses. Asks first.
  Sync             one click for the usual sequence: ask the server,
                   save, take, send.
  Restore stash    bring back changes that were put aside when you
                   switched to another line of work.


WHAT KEEPS YOU SAFE

  - It never stops on a hidden password question.
  - It warns you when you are about to save straight onto the shared
    line of work.
  - A real clash between your changes and somebody else's is handed
    over to VS Code instead of being guessed at.
  - Deleting a line of work refuses if it still holds work that was
    never published. The forced version is in the red section.

-----------------------------------------------------------------
