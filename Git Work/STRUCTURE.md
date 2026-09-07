# Git Work — STRUCTURE

> Verified against source: 2026-08-24 · `git_work.py` 1988 L

## Files

| File | Description |
|------|-------------|
| `git_work.py` | Single file. The whole tool (PySide6). |
| `icon.ico` | Window / taskbar icon (optional — dropped in by hand). |
| `%APPDATA%\GitWork\config.json` | Only the chosen repository path. |

User-facing documentation: `ReadMe_Git Work.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Git Work_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

---

## Purpose
A GUI wrapper for everyday git so a team does not need the command line. Every
action is one git invocation chain; when a step fails the tool stops and explains
in plain words what to do next (usually in VS Code → Source Control).

---

## Constants
| Constant | Meaning |
|----------|---------|
| `CONFIG_PATH` | `%APPDATA%\GitWork\config.json` |
| `TARGET` | `main` — the shared branch that Merge targets |
| `PROTECTED` | `{main, master}` — extra warnings apply here |
| `ICON` | `icon.ico` next to the script |
| `C_TEXT` / `C_CMD` / `C_INFO` / `C_OK` / `C_WARN` / `C_ERR`, `TAG_COLOR` | log colours per tag (`cmd`, `out`, `info`, `ok`, `warn`, `err`) |
| `APP_QSS` | application stylesheet |

---

## Git layer (no Qt)

| Function | Purpose |
|----------|---------|
| `load_config()` / `save_config(cfg)` | the remembered repository path |
| `GitError(message, hint)` | a failed step plus the human-readable next step |
| `_no_window_startupinfo()` | keeps a console window from flashing up |
| `_git_env()` | `GIT_TERMINAL_PROMPT=0` — never block on a hidden password prompt |
| `git_capture(repo, *args)` | run git, capture stdout/stderr, raise `GitError` |
| `repo_toplevel(path)` | resolve the repository root |
| `list_branches(repo)` / `current_branch(repo)` | local branches (via `for-each-ref refs/heads`, never `git branch` — in detached HEAD that prints a `(HEAD detached at …)` pseudo-entry that hijacks the branch box) / HEAD |
| `list_remote_only_branches(repo)` | branches that exist only on the server, as `origin/name`, so the box can offer them |
| `get_identity(repo)` | global `user.name` / `user.email` |
| `status_info(repo)` | clean/dirty + file counts |
| `ahead_behind(repo, branch)` | commits ahead of / behind the remote |
| `has_stash(repo)` | is there an auto-stash to restore |
| `_push_hint(err)` / `_conflict_hint()` | turn a git error into an instruction |
| `_UNMERGED` / `unmerged_text(code, ours, theirs)` | git's `UU`/`UD`/`DU`/`AA`/… codes in plain words, plus what to do to each file to keep one side (`pick` / `add` / `remove`). Deletion is the half that inverts: on `UD` it is *theirs* that deleted the file |
| `unmerged_paths(repo)` | `[(path, code)]` for every file a stopped merge left behind |
| `commits_since(repo, base, tip)` | what each side committed since they parted ways — the facts the conflict question shows |
| `ref_exists(repo, ref)` / `counts(repo, base, tip)` | does a ref exist; `(commits only on tip, commits only on base)` |
| `next_step(repo, info, has_identity, has_message, target)` | the whole "What to do now" decision, as data: `{text, button, action, tone}`. Priority order, first match wins; pure enough to test without Qt |
| `_icon_file()` | frozen-aware icon lookup: next to the exe → `sys._MEIPASS` → the source folder |
| `_set_app_id()` / `main()` | AppUserModelID for the taskbar button, then build the window |

### `GitJobs` — the operations
Constructed with `(repo, target, emit)`; every method runs on a worker thread and
logs through `emit`. `_run` / `_out` / `_log` / `_current` / `_commit_if_needed` /
`_report_state` are the shared internals.

| Method | What it does |
|--------|--------------|
| `fetch()` | refresh knowledge of the server (changes nothing) |
| `pull()` | bring the current branch up to date |
| `commit_push(msg)` | commit (message required) + push the branch |
| `sync(msg)` | fetch → commit (if a message) → pull → push |
| `merge_to_main(msg)` | push the branch and merge it into `main`; fast-forward when possible, otherwise `_merge_fallback(source)` does a local merge |
| `checkout(name, stash, from_remote=False)` | switch branch, auto-stashing uncommitted work; `from_remote` tracks `origin/name` into a new local branch; afterwards it re-reads `branch --show-current` and fails loudly if HEAD is not where it was sent |
| `new_branch(name)` / `delete_branch(name)` | create / delete (merged only) |
| `get_shared()` | merge `origin/<target>` into the current branch — the step that keeps the two lines from drifting apart; offered by the "What to do now" panel |
| `_record_conflict(ours, theirs, finish)` | a stopped merge is turned into facts on `self.conflict` (files + what each side committed since the merge base); changes nothing on disk |
| `take_side(report, side)` | finish a stopped merge by keeping one side of every clashing file, commit with a message naming the choice, then `_finish_to_main(source)` if that is what was interrupted |
| `stash_pop()` | restore what the auto-stash kept |
| `force_push()` | `--force-with-lease`, extra warning on `main` |
| `hard_reset_to_server()` | drop local commits/changes, match the remote |
| `discard_all()` | delete every uncommitted change and untracked file |
| `force_delete_branch(name)` | delete even with unmerged commits |

The last four are the guarded "danger zone"; three of them need the user to type
`RESET` / `DISCARD` / `DELETE` (`App._typed_confirm`).

---

## Qt layer

| Class | Purpose |
|-------|---------|
| `WorkerSignals` / `GitWorker(QRunnable)` | run one `GitJobs` method off the UI thread and stream its log back |
| `IdentityDialog(QDialog)` | ask for git name + email once, store them in the global config so VS Code and the CLI agree |
| `ConflictDialog(QDialog)` | the "which version wins?" question: the clashing files and what happened to each, what each side committed since they parted ways, and three answers (keep theirs / keep ours / leave it). Sets `.choice` |
| `App(QWidget)` | the window |

### `App` method groups
| Group | Methods |
|-------|---------|
| build / repo | `__init__`, `_build_ui`, `_init_repo`, `_choose_repo`, `_set_repo`, `_open_vscode`, `_set_ui_enabled` |
| identity | `_refresh_identity`, `_edit_identity`, `_ensure_identity` (asked before the first commit), `_check_autocrlf`, `_check_remote` (every action assumes a remote called `origin`; without one, say so at load instead of on the first failed push) |
| status | `_refresh` (rebuilds the branch box but keeps `_pending_pick`, the branch the user chose by hand), `_update_here` (the "You are on: …" line — where git really is vs. what the box shows), `_update_status_badge` |
| commit message | `_toggle_msg_expand`, `_today_prefix`, `_commit_message`, `_reset_commit_message`, `_roll_date_prefill`, `_confirm_push_only` |
| branches | `_selected_branch`, `_on_branch_picked` (user picked something — remembered across refreshes), `_on_switch` (no silent no-ops: empty pick and "already there" both say so), `_ensure_on_branch` (blocks Pull/Commit + Push/Sync/Merge while detached), `_on_new`, `_pick_branch_to_delete`, `_on_delete` |
| actions | `_on_fetch`, `_on_pull`, `_on_commit_push`, `_on_sync`, `_on_merge`, `_on_stash_pop` |
| stopped merge | `_on_job_done` reads `jobs.conflict` → `_ask_about_conflict` shows `ConflictDialog`, confirms, then runs `take_side` as a follow-up job |
| what to do now | `_update_step` (fills the top panel from `next_step`, re-run on every refresh and 500 ms after typing in the message box), `_on_step` (runs the one offered action; with changed files and an empty message it focuses the box instead of half-doing the save) |
| danger zone | `_typed_confirm`, `_on_force_push`, `_on_hard_reset`, `_on_discard_all`, `_on_force_delete` |
| jobs / log | `_start_job`, `_on_job_done`, `_append` |

---

## Dependencies
- `PySide6` — widgets, `QRunnable` worker
- `git` on PATH — every operation is a subprocess call
- No other third-party package
