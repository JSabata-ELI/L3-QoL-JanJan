# Git Work — STRUCTURE

> Verified against source: 2026-08-19 · `git_work.py` 1191 L

## Files

| File | Description |
|------|-------------|
| `git_work.py` | Single file. The whole tool (PySide6). |
| `icon.ico` | Window / taskbar icon (optional — dropped in by hand). |
| `%APPDATA%\GitWork\config.json` | Only the chosen repository path. |

User-facing documentation is in `ReadMe_Git Work.txt`.

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
| `list_branches(repo)` / `current_branch(repo)` | branch list / HEAD |
| `get_identity(repo)` | global `user.name` / `user.email` |
| `status_info(repo)` | clean/dirty + file counts |
| `ahead_behind(repo, branch)` | commits ahead of / behind the remote |
| `has_stash(repo)` | is there an auto-stash to restore |
| `_push_hint(err)` / `_conflict_hint()` | turn a git error into an instruction |
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
| `checkout(name, stash)` | switch branch, auto-stashing uncommitted work |
| `new_branch(name)` / `delete_branch(name)` | create / delete (merged only) |
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
| `App(QWidget)` | the window |

### `App` method groups
| Group | Methods |
|-------|---------|
| build / repo | `__init__`, `_build_ui`, `_init_repo`, `_choose_repo`, `_set_repo`, `_open_vscode`, `_set_ui_enabled` |
| identity | `_refresh_identity`, `_edit_identity`, `_ensure_identity` (asked before the first commit), `_check_autocrlf` |
| status | `_refresh`, `_update_status_badge` |
| commit message | `_toggle_msg_expand`, `_today_prefix`, `_commit_message`, `_reset_commit_message` |
| branches | `_selected_branch`, `_on_switch`, `_on_new`, `_pick_branch_to_delete`, `_on_delete` |
| actions | `_on_fetch`, `_on_pull`, `_on_commit_push`, `_on_sync`, `_on_merge`, `_on_stash_pop` |
| danger zone | `_typed_confirm`, `_on_force_push`, `_on_hard_reset`, `_on_discard_all`, `_on_force_delete` |
| jobs / log | `_start_job`, `_on_job_done`, `_append` |

---

## Dependencies
- `PySide6` — widgets, `QRunnable` worker
- `git` on PATH — every operation is a subprocess call
- No other third-party package
