Git Work
========

A GUI helper that lets a team do everyday git safely from one window - no git
command line needed. When a step fails it stops and explains, in plain words,
what to do next (usually in VS Code's Source Control).

Sections in the window
----------------------
- Repository : pick + remember your project folder (per user). "Open in VS Code".
- Identity   : your git name/email. Asked once before your first commit; stored
               in git's global config so VS Code and the CLI agree.
- Branch     : switch / create / delete branches (delete only removes a fully
               merged branch; unmerged work is never lost here).
- Status     : shows if you are clean/changed and ahead/behind the server.
- Actions    : Fetch, Pull, Commit + Push, Merge into main, Sync, Restore stash.

The commit message
------------------
The box is prefilled with today's date (DDMMYYYY). "More" opens a bigger box for
a multi-line message and "Less" folds it back to its first line - the full text
is still what gets committed. The date on its own IS a valid message - leaving
the prefill untouched commits under it. Only clearing the box completely means
"just push what is already committed"; in that case Commit + Push tells you the
changed files stayed behind instead of reporting nothing to push.

What the actions do
-------------------
- Fetch          : refresh knowledge of the server (safe, changes nothing).
- Pull           : bring your branch up to date from the server.
- Commit + Push  : commit your changes (needs a message) and push your branch.
- Merge into main: push your branch and merge it into 'main' (fast-forward if
                   possible, otherwise a local merge; confirmed first).
- Sync           : fetch + commit (if message) + pull + push, in one click.
- Restore stash  : bring back changes that were auto-stashed when you switched
                   branch with uncommitted work.

Danger zone (guarded)
---------------------
Available but clearly marked in red and confirmed before running:
- Force push          : overwrite the server copy of your branch
                        (uses --force-with-lease; extra warning on 'main').
- Hard reset to server: throw away local commits/changes, match the server.
- Discard ALL changes : delete every uncommitted change and untracked file.
- Force delete branch : delete a branch even with unmerged commits.
The last three require typing RESET / DISCARD / DELETE to confirm.

Safety
------
- Never blocks on a hidden password prompt (GIT_TERMINAL_PROMPT=0).
- Warns when you commit on the shared 'main' branch.
- Real merge conflicts are handed off to VS Code > Source Control.
- On start it checks git's line-ending setting and, if it is not configured,
  suggests running once:  git config --global core.autocrlf true
  Without it Windows and everyone else disagree about every line of every file,
  and a one-word change shows up as the whole file being rewritten.
- The Status line spells out what to do rather than just showing numbers:
  "3 behind - Pull", "2 ahead / 1 behind - Pull then Push", "not on server yet",
  "detached HEAD - switch to a branch", and "(shared branch!)" on main.

Notes
-----
- The repo root is detected automatically (parent of this folder) but can be
  changed to any git repository.
- Config is per user: %APPDATA%\GitWork\config.json (only the chosen repo path).
- Drop an "icon.ico" into this folder to give the window a Git icon.
- Developer notes are in STRUCTURE.md.

Grouping: appears under the "Personal" tab in the Launcher, next to the
Builder / Copy Manager. Build it with Dev Tools so it shows up there.
