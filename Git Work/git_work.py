"""
Git Work - a PySide6 GUI helper that lets a team do everyday git safely from
one window, without knowing the git command line.

It shows the current branch and whether you are ahead/behind the server, and for
every action it says in plain words what happened (already up to date, N new
commits pulled, your version is newer, diverged, etc.). When a step fails it
stops and explains what to do next, usually in VS Code's Source Control.

Dangerous operations (force push, hard reset, discard-all, force branch delete)
are available but live in a red "Danger zone" behind explicit confirmations.

This folder ("Git Work") shows up under the "Personal" tab in the Launcher.
"""

import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QFileDialog,
    QMessageBox, QInputDialog, QDialog, QDialogButtonBox,
)


# ---------------------------------------------------------------- config ----

CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home()))) / "GitWork" / "config.json"
TARGET = "main"          # shared branch we merge into
PROTECTED = {"main", "master"}
def _icon_file() -> Path:
    """icon.ico sits next to the exe. In a frozen build __file__ points into the
    bundle (_internal), not the exe folder, so resolving from __file__ silently
    yields a path that does not exist and the app ends up with no icon at all."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent / "icon.ico"
        if exe_dir.exists():
            return exe_dir
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "icon.ico"
        return exe_dir
    return Path(__file__).resolve().parent / "icon.ico"


ICON = _icon_file()

# Colours (readable on the light #f0f0f0 / white surfaces).
C_TEXT = "#111111"
C_CMD = "#1565C0"
C_INFO = "#666666"
C_OK = "#137a2b"
C_WARN = "#a15c00"
C_ERR = "#c0392b"

TAG_COLOR = {"cmd": C_CMD, "out": C_TEXT, "info": C_INFO,
             "ok": C_OK, "err": C_ERR, "hint": C_WARN}

APP_QSS = """
QWidget { background-color: #f0f0f0; color: #111111; font-size: 12px; }
QGroupBox { border: 1px solid #b0b0b0; border-radius: 4px; margin-top: 9px;
            font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #333333; }
QLabel { background: transparent; color: #111111; }
QPushButton { color: #111111; background-color: #e0e0e0; border: 1px solid #aaaaaa;
              border-radius: 3px; padding: 4px 12px; }
QPushButton:hover { background-color: #d0d0d0; }
QPushButton:pressed { background-color: #c0c0c0; }
QPushButton:disabled { color: #999999; background-color: #ececec; }
QPushButton#primary { background-color: #1565C0; color: #ffffff; border: 1px solid #0d47a1; }
QPushButton#primary:hover { background-color: #1976d2; }
QPushButton#primary:disabled { background-color: #9bbfe0; border-color: #9bbfe0; color: #eef; }
QGroupBox#dangerZone { border: 1px solid #c0392b; }
QGroupBox#dangerZone::title { color: #c0392b; }
QLineEdit, QComboBox { background: #ffffff; color: #111111; border: 1px solid #aaaaaa;
                       border-radius: 3px; padding: 3px 6px; }
QComboBox QAbstractItemView { background: #ffffff; color: #111111;
                              selection-background-color: #1565C0; selection-color: #ffffff; }
QTextEdit { background: #ffffff; color: #111111; border: 1px solid #b0b0b0; }
"""


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------- git plumbing ----

class GitError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


def _no_window_startupinfo():
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si


def _git_env():
    # Never let git block on an invisible terminal prompt.
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git_capture(repo, *args):
    """Run a git command in `repo`; return (rc, stdout, stderr). Never raises.

    git talks UTF-8; decode explicitly (Windows would otherwise use cp1250 and
    mangle Czech diacritics). core.quotepath=false keeps Czech filenames
    readable instead of octal escapes.
    """
    try:
        r = subprocess.run(
            ["git", "-c", "core.quotepath=false", *args], cwd=str(repo),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            startupinfo=_no_window_startupinfo(), env=_git_env(),
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git-not-found"


def repo_toplevel(path):
    if not path:
        return None
    rc, out, _ = git_capture(path, "rev-parse", "--show-toplevel")
    return out if rc == 0 and out else None


def list_branches(repo) -> list:
    rc, out, _ = git_capture(repo, "branch", "--format=%(refname:short)")
    return [b.strip() for b in out.splitlines() if b.strip()] if rc == 0 else []


def current_branch(repo) -> str:
    rc, out, _ = git_capture(repo, "branch", "--show-current")
    return out if rc == 0 else ""


def get_identity(repo):
    _, name, _ = git_capture(repo, "config", "user.name")
    _, email, _ = git_capture(repo, "config", "user.email")
    return name, email


def status_info(repo) -> dict:
    info = {"branch": "", "upstream": None, "ahead": 0, "behind": 0,
            "changes": 0, "detached": False}
    rc, out, _ = git_capture(repo, "status", "--porcelain=v2", "--branch")
    if rc != 0:
        return info
    for line in out.splitlines():
        if line.startswith("# branch.head "):
            head = line[len("# branch.head "):].strip()
            info["branch"] = head
            info["detached"] = head == "(detached)"
        elif line.startswith("# branch.upstream "):
            info["upstream"] = line[len("# branch.upstream "):].strip()
        elif line.startswith("# branch.ab "):
            for p in line.split()[2:]:
                if p.startswith("+"):
                    info["ahead"] = int(p[1:])
                elif p.startswith("-"):
                    info["behind"] = int(p[1:])
        elif not line.startswith("#"):
            info["changes"] += 1
    return info


def ahead_behind(repo, branch):
    """Return (ahead, behind) vs origin/<branch>, or None if no upstream."""
    rc, out, _ = git_capture(
        repo, "rev-list", "--left-right", "--count", f"origin/{branch}...{branch}")
    if rc != 0 or not out:
        return None
    try:
        behind, ahead = (int(x) for x in out.split())
        return ahead, behind
    except Exception:
        return None


def has_stash(repo) -> bool:
    rc, out, _ = git_capture(repo, "stash", "list")
    return rc == 0 and bool(out.strip())


def _push_hint(err: str) -> str:
    low = (err or "").lower()
    if any(s in low for s in ("non-fast-forward", "rejected", "fetch first", "stale info")):
        return ("Someone pushed to this branch before you. Click Pull first, "
                "then Push again. (Never force-push a shared branch.)")
    if any(s in low for s in ("authentication", "could not read", "denied",
                              "terminal prompt", "403", "401")):
        return ("Sign-in failed. VS Code should prompt you to sign in to GitHub, "
                "or fix your login in Windows Credential Manager, then try again.")
    if "no upstream" in low or "has no upstream" in low:
        return "This branch isn't on the server yet - it will be published now."
    return ("Check the message above. Usually a Pull is needed, or it's a "
            "network / sign-in problem.")


def _conflict_hint() -> str:
    return ("Git couldn't merge automatically. Open VS Code > Source Control, "
            "resolve the files marked with '!', then Stage all and Commit. "
            "Come back and press Refresh.")


# ------------------------------------------------------------- git jobs -----

class GitJobs:
    """The git workflows. `emit(text, tag)` streams lines back to the UI."""

    def __init__(self, repo, target, emit):
        self.repo = repo
        self.target = target
        self.emit = emit

    # -- low level ---------------------------------------------------------

    def _run(self, *args, check=True):
        cmd = ["git", *args]
        self._log(">>> " + " ".join(cmd), "cmd")
        rc, out, err = git_capture(self.repo, *args)
        if err == "git-not-found":
            raise GitError("git was not found on PATH.",
                           "Install Git for Windows, then reopen Git Work.")
        if out:
            self._log(out, "out")
        if err:
            self._log(err, "out")
        if check and rc != 0:
            raise GitError(f"Command failed: {' '.join(cmd)}")
        return rc, out, err

    def _out(self, *args):
        return self._run(*args)[1]

    def _log(self, text, tag="out"):
        self.emit(text, tag)

    def _current(self) -> str:
        b = self._out("branch", "--show-current")
        if not b:
            raise GitError(
                "You are not on a branch (detached HEAD).",
                "Pick a branch in the Branch box and click Switch, then retry.")
        return b

    def _commit_if_needed(self, msg):
        """Commit everything if a message was given.

        Returns (committed, left_uncommitted) so callers can tell the two
        no-op cases apart: a genuinely clean tree, and changed files that
        stayed behind because the message box was empty.
        """
        status = self._out("status", "--porcelain")
        if not status:
            self._log("Working tree clean, nothing to commit.", "info")
            return False, False
        self._log("Changes to commit:", "info")
        self._log(status, "out")
        if not msg:
            self._log("No commit message -> changes stay uncommitted and will "
                      "NOT be pushed.", "hint")
            return False, True
        self._run("add", "-A")
        self._run("commit", "-m", msg)
        return True, False

    def _report_state(self, branch):
        """Log a plain-language summary of where we stand vs the server."""
        ab = ahead_behind(self.repo, branch)
        if ab is None:
            self._log(f"'{branch}' is not on the server yet - use Push to publish it.",
                      "info")
            return
        ahead, behind = ab
        if ahead == 0 and behind == 0:
            self._log(f"'{branch}' is up to date with the server - you have the "
                      "latest version.", "ok")
        elif behind and ahead:
            self._log(f"'{branch}' has diverged: {behind} commit(s) to pull and "
                      f"{ahead} to push. Pull first, then Push.", "hint")
        elif behind:
            self._log(f"The server has {behind} newer commit(s) - use Pull to get them.",
                      "hint")
        else:
            self._log(f"Your branch is {ahead} commit(s) ahead of the server - "
                      "use Push to share your work.", "info")

    # -- everyday operations ----------------------------------------------

    def fetch(self):
        self._run("fetch", "--all", "--prune", check=False)
        cur = self._out("branch", "--show-current")
        if cur:
            self._report_state(cur)
        self._log("Fetch complete.", "ok")

    def pull(self):
        cur = self._current()
        self._run("fetch", "origin", cur, check=False)
        ab = ahead_behind(self.repo, cur)
        if ab is not None:
            ahead, behind = ab
            if behind == 0:
                if ahead == 0:
                    self._log("Already up to date - you have the latest version. "
                              "Nothing to pull.", "ok")
                else:
                    self._log(f"Nothing to pull. Your branch is {ahead} commit(s) "
                              "ahead - use Push to share it.", "ok")
                return
            self._log(f"Server has {behind} new commit(s) - pulling...", "info")
        rc, _, err = self._run("pull", "--no-edit", "origin", cur, check=False)
        if rc != 0:
            low = err.lower()
            if "would be overwritten" in low or "local changes" in low:
                raise GitError(
                    "Your uncommitted changes would collide with incoming ones.",
                    "Commit your work first (type a message and Commit + Push), "
                    "then Pull.")
            raise GitError(f"Pull of '{cur}' failed.", _conflict_hint())
        n = ab[1] if ab else "the new"
        self._log(f"Pulled {n} commit(s) from the server - '{cur}' is now current.",
                  "ok")

    def commit_push(self, msg):
        cur = self._current()
        committed, left_uncommitted = self._commit_if_needed(msg)
        self._run("fetch", "origin", cur, check=False)
        ab = ahead_behind(self.repo, cur)
        if ab is not None:
            ahead, behind = ab
            if behind > 0:
                raise GitError(
                    f"The server has {behind} commit(s) you don't have.",
                    "Click Pull first, then Push (this keeps everyone's work).")
            if ahead == 0 and not committed:
                if left_uncommitted:
                    raise GitError(
                        "Nothing was sent - your changed files are still "
                        "uncommitted.",
                        "Type a commit message in the box above, then click "
                        "Commit + Push again.")
                self._log("Nothing to push - already up to date with the server.",
                          "ok")
                return
        rc, _, err = self._run("push", "-u", "origin", cur, check=False)
        if rc != 0:
            raise GitError(f"Push of '{cur}' failed.", _push_hint(err))
        n = ab[0] if ab else "your"
        self._log(f"Pushed {n} commit(s) - the server copy of '{cur}' is updated.",
                  "ok")

    def sync(self, msg):
        cur = self._current()
        self._run("fetch", "--all", "--prune", check=False)
        _, left_uncommitted = self._commit_if_needed(msg)
        ab = ahead_behind(self.repo, cur)
        if ab and ab[1] > 0:
            self._log(f"Server has {ab[1]} new commit(s) - pulling first...", "info")
            rc, _, _ = self._run("pull", "--no-edit", "origin", cur, check=False)
            if rc != 0:
                raise GitError(f"Pull during Sync failed for '{cur}'.", _conflict_hint())
        rc, _, err = self._run("push", "-u", "origin", cur, check=False)
        if rc != 0:
            raise GitError(f"Push during Sync failed for '{cur}'.", _push_hint(err))
        if left_uncommitted:
            self._log(f"Sync done for what was committed, but your changed "
                      f"files are still on this PC only - type a commit "
                      f"message and run Sync again.", "hint")
            return
        self._log(f"Sync done - '{cur}' matches the server.", "ok")

    def merge_to_main(self, msg):
        cur = self._current()
        if cur == self.target:
            raise GitError(
                f"You are on '{self.target}'. Merge mode publishes another "
                f"branch into '{self.target}'.",
                "Switch to your working branch first.")
        self._run("fetch", "--all", "--prune", check=False)
        _, left_uncommitted = self._commit_if_needed(msg)
        if left_uncommitted:
            raise GitError(
                "Your changed files are still uncommitted, so the merge would "
                "publish an older state.",
                "Type a commit message in the box above, then merge again.")

        rc, _, err = self._run("push", "-u", "origin", cur, check=False)
        if rc != 0:
            raise GitError(f"Push of '{cur}' failed.", _push_hint(err))
        self._log(f"Pushed '{cur}'.", "ok")

        rc, _, _ = self._run("push", "origin", f"{cur}:{self.target}", check=False)
        if rc == 0:
            self._log(f"Fast-forwarded '{self.target}' from '{cur}'. "
                      f"'{self.target}' now contains your work.", "ok")
            return
        self._log("Fast-forward not possible - doing a local merge.", "info")
        self._merge_fallback(cur)

    def _merge_fallback(self, source):
        self._run("checkout", self.target)
        rc, _, _ = self._run("pull", "--no-edit", "origin", self.target, check=False)
        if rc != 0:
            raise GitError(
                f"Pull of '{self.target}' failed.",
                f"Resolve it in VS Code, then retry. You are now on '{self.target}'.")
        rc, _, _ = self._run("merge", source, "--no-edit", check=False)
        if rc != 0:
            raise GitError(
                f"Merge conflict merging '{source}' into '{self.target}'.",
                "You are now on 'main'. Resolve conflicts in VS Code > Source "
                f"Control, Stage all, Commit, then run 'git push origin "
                f"{self.target}', and finally switch back to '{source}'.")
        self._run("push", "origin", self.target)
        self._log(f"Pushed '{self.target}'.", "ok")
        self._run("checkout", source)
        self._run("merge", self.target, "--no-edit")
        self._log(f"'{source}' is now level with '{self.target}'.", "ok")

    def checkout(self, name, stash):
        if stash:
            self._run("stash", "push", "-u", "-m", "GitWork auto-stash")
            self._log("Your changes were stashed. Use 'Restore stash' to get "
                      "them back on this branch later.", "hint")
        self._run("switch", name)
        self._log(f"Switched to '{name}'.", "ok")
        self._report_state(name)

    def new_branch(self, name):
        self._run("switch", "-c", name)
        self._log(f"Created and switched to '{name}'.", "ok")

    def delete_branch(self, name):
        rc, _, _ = self._run("branch", "-d", name, check=False)
        if rc != 0:
            raise GitError(
                f"Branch '{name}' was not deleted.",
                "It has commits that aren't merged yet. Merge it first, or use "
                "the Danger zone's Force delete if you're sure.")
        self._log(f"Deleted branch '{name}'.", "ok")

    def stash_pop(self):
        rc, _, _ = self._run("stash", "pop", check=False)
        if rc != 0:
            raise GitError(
                "Restoring the stash hit a conflict.",
                "Resolve it in VS Code > Source Control, Stage all, Commit.")
        self._log("Stash restored.", "ok")

    # -- dangerous operations (guarded in the UI) --------------------------

    def force_push(self):
        cur = self._current()
        rc, _, err = self._run("push", "--force-with-lease", "origin", cur, check=False)
        if rc != 0:
            hint = _push_hint(err)
            if "stale info" in err.lower() or "rejected" in err.lower():
                hint = ("The server changed since your last fetch - someone else "
                        "may have pushed. Fetch and review before forcing again.")
            raise GitError("Force push failed.", hint)
        self._log(f"Force-pushed '{cur}' - the server branch was overwritten.", "ok")

    def hard_reset_to_server(self):
        cur = self._current()
        self._run("fetch", "origin", cur, check=False)
        rc, _, _ = self._run("reset", "--hard", f"origin/{cur}", check=False)
        if rc != 0:
            raise GitError(
                f"Reset failed - '{cur}' may not be on the server yet.",
                "Push it first, or check the branch name.")
        self._log(f"'{cur}' was reset to match the server. Local commits and "
                  "changes are gone.", "ok")

    def discard_all(self):
        self._run("reset", "--hard", "HEAD")
        self._run("clean", "-fd")
        self._log("All uncommitted changes and untracked files were discarded.", "ok")

    def force_delete_branch(self, name):
        self._run("branch", "-D", name)
        self._log(f"Force-deleted branch '{name}' - any unmerged commits are gone.", "ok")


# --------------------------------------------------------- worker thread ----

class WorkerSignals(QObject):
    log = Signal(str, str)
    done = Signal(bool)


class GitWorker(QRunnable):
    def __init__(self, repo, target, fn):
        super().__init__()
        self.signals = WorkerSignals()
        self.jobs = GitJobs(repo, target, self.signals.log.emit)
        self.fn = fn

    def run(self):
        try:
            self.fn(self.jobs)
            self.signals.done.emit(True)
        except GitError as e:
            self.jobs._log("", "out")
            self.jobs._log("!!! " + str(e), "err")
            if e.hint:
                self.jobs._log("--> " + e.hint, "hint")
            self.signals.done.emit(False)
        except Exception as e:
            self.jobs._log("!!! Unexpected error: " + repr(e), "err")
            self.signals.done.emit(False)


# --------------------------------------------------------------- the UI -----

class IdentityDialog(QDialog):
    def __init__(self, parent, name="", email=""):
        super().__init__(parent)
        self.setWindowTitle("Git identity")
        self.setMinimumWidth(420)
        self.result_value = None
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        grid.addWidget(QLabel("Your name:"), 0, 0)
        self.name_e = QLineEdit(name)
        self.name_e.setMinimumWidth(280)
        grid.addWidget(self.name_e, 0, 1)
        grid.addWidget(QLabel("Your email:"), 1, 0)
        self.email_e = QLineEdit(email)
        self.email_e.setMinimumWidth(280)
        grid.addWidget(self.email_e, 1, 1)
        lay.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.name_e.setFocus()

    def _save(self):
        n = self.name_e.text().strip()
        e = self.email_e.text().strip()
        if not n or "@" not in e:
            QMessageBox.critical(self, "Invalid", "Enter a name and a valid email.")
            return
        self.result_value = (n, e)
        self.accept()


class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Git Work")
        self.resize(780, 720)
        self.setStyleSheet(APP_QSS)
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))

        self.cfg = load_config()
        self.repo = None
        self.busy = False
        self.pool = QThreadPool.globalInstance()
        self._worker = None
        self._action_buttons = []

        self._build_ui()
        self._init_repo()

    # -- layout ------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Repository
        rg = QGroupBox("Repository")
        rl = QHBoxLayout(rg)
        self.repo_lbl = QLabel("(none selected)")
        self.repo_lbl.setStyleSheet(f"color: {C_INFO};")
        rl.addWidget(self.repo_lbl, 1)
        b_change = QPushButton("Change...")
        b_change.setToolTip("Pick a different project folder (git repository).\n"
                            "Your choice is remembered for next time.")
        b_change.clicked.connect(self._choose_repo)
        rl.addWidget(b_change)
        b_code = QPushButton("Open in VS Code")
        b_code.setToolTip("Open this repository in VS Code (useful for resolving\n"
                          "conflicts in Source Control).")
        b_code.clicked.connect(self._open_vscode)
        rl.addWidget(b_code)
        root.addWidget(rg)

        # Identity
        ig = QGroupBox("Identity (who your commits are from)")
        il = QHBoxLayout(ig)
        self.id_lbl = QLabel("...")
        il.addWidget(self.id_lbl, 1)
        b_id = QPushButton("Change...")
        b_id.setToolTip("Set the name and email attached to your commits\n"
                        "(stored in git's global settings on this computer).")
        b_id.clicked.connect(self._edit_identity)
        il.addWidget(b_id)
        root.addWidget(ig)

        # Branch & status
        bg = QGroupBox("Branch & status")
        bl = QVBoxLayout(bg)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Branch:"))
        self.branch_cb = QComboBox()
        self.branch_cb.setMinimumWidth(220)
        r1.addWidget(self.branch_cb)
        for text, cb, tip in (
            ("Switch", self._on_switch,
             "Move to the branch selected in the box on the left.\n"
             "If you have uncommitted changes you'll be offered a stash."),
            ("New...", self._on_new,
             "Create a new branch starting from the current one\n"
             "and switch to it."),
            ("Delete...", self._on_delete,
             "Delete a fully merged local branch (you pick which one).\n"
             "Branches with unmerged work are refused - nothing is lost."),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(cb)
            r1.addWidget(btn)
            self._action_buttons.append(btn)
        r1.addStretch()
        bl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Status:"))
        self.status_lbl = QLabel("")
        r2.addWidget(self.status_lbl, 1)
        b_fetch = QPushButton("Fetch")
        b_fetch.setToolTip("Ask the server what's new. Changes nothing in your\n"
                           "files - only updates the status line. Always safe.")
        b_fetch.clicked.connect(self._on_fetch)
        r2.addWidget(b_fetch)
        self._action_buttons.append(b_fetch)
        b_refresh = QPushButton("Refresh")
        b_refresh.setToolTip("Re-read the branch list and status shown in this\n"
                             "window (does not contact the server).")
        b_refresh.clicked.connect(self._refresh)
        r2.addWidget(b_refresh)
        bl.addLayout(r2)
        root.addWidget(bg)

        # Commit message (single line by default, expandable to multi-line)
        mg = QGroupBox("Commit message (leave empty to just push what is already committed)")
        ml = QVBoxLayout(mg)
        mrow = QHBoxLayout()
        self.msg_edit = QLineEdit()
        self.msg_edit.setPlaceholderText("What did you change?")
        # Start every day's message with the date, the team's convention
        # (e.g. "15072026 konec dne"). The date on its own is a valid
        # message - only an empty box means "just push".
        self.msg_edit.setText(self._today_prefix() + " ")
        mrow.addWidget(self.msg_edit, 1)
        self.msg_expand_btn = QPushButton("▼ More")
        self.msg_expand_btn.setFixedWidth(70)
        self.msg_expand_btn.setToolTip("Expand to a bigger box for a longer,\n"
                                       "multi-line commit message.")
        self.msg_expand_btn.clicked.connect(self._toggle_msg_expand)
        mrow.addWidget(self.msg_expand_btn)
        ml.addLayout(mrow)
        self.msg_multi = QTextEdit()
        self.msg_multi.setPlaceholderText("What did you change?\n\n- bullet points welcome\n- second line...")
        self.msg_multi.setFixedHeight(100)
        self.msg_multi.hide()
        ml.addWidget(self.msg_multi)
        root.addWidget(mg)

        # Actions
        ag = QGroupBox("Actions")
        al = QHBoxLayout(ag)
        b_pull = QPushButton("Pull")
        b_pull.setToolTip("Download new commits from the server into your\n"
                          "current branch (brings you up to date).")
        b_pull.clicked.connect(self._on_pull)
        al.addWidget(b_pull)
        self._action_buttons.append(b_pull)
        b_cp = QPushButton("Commit + Push")
        b_cp.setObjectName("primary")
        b_cp.setToolTip("Save your changes as a commit (needs a message above)\n"
                        "and upload your branch to the server.")
        b_cp.clicked.connect(self._on_commit_push)
        al.addWidget(b_cp)
        self._action_buttons.append(b_cp)
        b_merge = QPushButton(f"Merge into {TARGET}")
        b_merge.setToolTip(f"Publish your branch's work into the shared '{TARGET}'\n"
                           "branch (commit + push + merge, asks first).")
        b_merge.clicked.connect(self._on_merge)
        al.addWidget(b_merge)
        self._action_buttons.append(b_merge)
        b_sync = QPushButton("Sync")
        b_sync.setObjectName("primary")
        b_sync.setToolTip("One click: fetch + commit (if a message is given)\n"
                          "+ pull + push. Makes you and the server match.")
        b_sync.clicked.connect(self._on_sync)
        al.addWidget(b_sync)
        self._action_buttons.append(b_sync)
        self.stash_btn = QPushButton("Restore stash")
        self.stash_btn.setToolTip("Bring back the changes that were put aside\n"
                                  "(stashed) when you switched branches.")
        self.stash_btn.clicked.connect(self._on_stash_pop)
        al.addWidget(self.stash_btn)
        self._action_buttons.append(self.stash_btn)
        al.addStretch()
        root.addWidget(ag)

        # Danger zone
        dg = QGroupBox("Danger zone - can destroy work, use with care")
        dg.setObjectName("dangerZone")
        dl = QVBoxLayout(dg)
        warn = QLabel("These overwrite or delete history/changes. Read each warning "
                      "before confirming.")
        warn.setStyleSheet(f"color: {C_ERR};")
        dl.addWidget(warn)
        dr = QHBoxLayout()
        for text, cb, tip in (
            ("Force push", self._on_force_push,
             "Overwrite the server copy of your branch with your local version.\n"
             "Anything on the server that you don't have will be LOST."),
            ("Hard reset to server", self._on_hard_reset,
             "Throw away ALL local commits and changes on this branch and make\n"
             "it identical to the server. Cannot be undone."),
            ("Discard ALL changes", self._on_discard_all,
             "Permanently delete every uncommitted change AND every untracked\n"
             "file in the repository. Cannot be undone."),
            ("Force delete branch", self._on_force_delete,
             "Delete a branch even if it has commits that were never merged.\n"
             "Those commits will be LOST."),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(cb)
            dr.addWidget(btn)
            self._action_buttons.append(btn)
        dr.addStretch()
        dl.addLayout(dr)
        root.addWidget(dg)

        # Log
        lg = QGroupBox("Log")
        ll = QVBoxLayout(lg)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setDefaultStyleSheet("")
        f = self.log.font()
        f.setFamily("Consolas")
        f.setPointSize(9)
        self.log.setFont(f)
        ll.addWidget(self.log)
        root.addWidget(lg, 1)

    # -- repo handling -----------------------------------------------------

    def _init_repo(self):
        candidate = self.cfg.get("repo") or str(Path(__file__).resolve().parent)
        top = repo_toplevel(candidate)
        if top:
            self._set_repo(top, remember=False)
        else:
            self._append("No git repository selected yet. Click 'Change...' to "
                         "pick your project folder.", "hint")
            self._set_ui_enabled(False)

    def _choose_repo(self):
        start = str(self.repo or self.cfg.get("repo") or Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select the git repository folder", start)
        if not chosen:
            return
        top = repo_toplevel(chosen)
        if not top:
            QMessageBox.critical(
                self, "Not a git repository",
                f"'{chosen}' is not inside a git repository.\n\nPick the folder "
                "that contains your project (the one with the hidden .git).")
            return
        self._set_repo(top, remember=True)

    def _set_repo(self, top, remember):
        self.repo = Path(top)
        self.repo_lbl.setText(str(self.repo))
        if remember:
            self.cfg["repo"] = str(self.repo)
            save_config(self.cfg)
        self._set_ui_enabled(True)
        self._append(f"Repository: {self.repo}", "info")
        self._refresh_identity()
        self._check_autocrlf()
        self._refresh()

    def _open_vscode(self):
        if not self.repo:
            return
        try:
            subprocess.Popen("code .", cwd=str(self.repo), shell=True)
        except Exception as e:
            QMessageBox.warning(self, "VS Code", f"Could not open VS Code:\n{e}")

    def _set_ui_enabled(self, on):
        for b in self._action_buttons:
            b.setEnabled(on)
        self.branch_cb.setEnabled(on)

    # -- identity ----------------------------------------------------------

    def _refresh_identity(self):
        if not self.repo:
            return
        name, email = get_identity(self.repo)
        if name and email:
            self.id_lbl.setText(f"You are: {name} <{email}>")
            self.id_lbl.setStyleSheet(f"color: {C_TEXT};")
        else:
            self.id_lbl.setText("Not set - click Change... before committing.")
            self.id_lbl.setStyleSheet(f"color: {C_ERR}; font-weight: 600;")

    def _edit_identity(self):
        name, email = get_identity(self.repo) if self.repo else ("", "")
        dlg = IdentityDialog(self, name, email)
        if dlg.exec() and dlg.result_value:
            n, e = dlg.result_value
            git_capture(self.repo, "config", "--global", "user.name", n)
            git_capture(self.repo, "config", "--global", "user.email", e)
            self._append(f"Identity set to {n} <{e}> (global).", "ok")
            self._refresh_identity()

    def _ensure_identity(self) -> bool:
        name, email = get_identity(self.repo)
        if name and email:
            return True
        QMessageBox.information(
            self, "Set your identity",
            "Before your first commit, tell git who you are.\n\n"
            "Enter your name and email in the next dialog.")
        self._edit_identity()
        name, email = get_identity(self.repo)
        return bool(name and email)

    def _check_autocrlf(self):
        rc, out, _ = git_capture(self.repo, "config", "core.autocrlf")
        if rc != 0 or not out.strip():
            self._append(
                "Tip: line-ending setting is not configured. On Windows, run "
                "once:  git config --global core.autocrlf true  (prevents noisy "
                "'whole file changed' diffs across the team).", "hint")

    # -- branch / status ---------------------------------------------------

    def _refresh(self):
        if not self.repo:
            return
        branches = list_branches(self.repo)
        info = status_info(self.repo)
        self.branch_cb.blockSignals(True)
        self.branch_cb.clear()
        self.branch_cb.addItems(branches)
        cur = info["branch"] or current_branch(self.repo)
        idx = self.branch_cb.findText(cur)
        if idx >= 0:
            self.branch_cb.setCurrentIndex(idx)
        elif cur:
            self.branch_cb.addItem(cur)
            self.branch_cb.setCurrentText(cur)
        self.branch_cb.blockSignals(False)
        self._update_status_badge(info)
        try:
            self.stash_btn.setEnabled(not self.busy and has_stash(self.repo))
        except Exception:
            pass

    def _update_status_badge(self, info):
        if info["detached"]:
            self.status_lbl.setText("detached HEAD - switch to a branch")
            self.status_lbl.setStyleSheet(f"color: {C_ERR}; font-weight: 600;")
            return
        parts = []
        color = C_OK
        parts.append(f"{info['changes']} file(s) changed" if info["changes"] else "clean")
        if info["changes"]:
            color = C_WARN
        if info["upstream"] is None:
            parts.append("not on server yet")
            color = C_WARN
        else:
            a, b = info["ahead"], info["behind"]
            if a and b:
                parts.append(f"{a} ahead / {b} behind - Pull then Push")
                color = C_ERR
            elif b:
                parts.append(f"{b} behind - Pull")
                color = C_WARN
            elif a:
                parts.append(f"{a} ahead - Push")
                color = C_WARN
            elif info["changes"]:
                # "up to date" next to a pile of changed files reads as a
                # contradiction - it only ever meant "no commits waiting".
                parts.append("committed work is up to date - these files are not "
                             "committed yet")
            else:
                parts.append("up to date")
        if info["branch"] in PROTECTED:
            parts.append("(shared branch!)")
            color = C_ERR
        self.status_lbl.setText("   •   ".join(parts))
        self.status_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _selected_branch(self) -> str:
        b = self.branch_cb.currentText().strip()
        return "" if b.startswith("(") else b

    # -- commit message ----------------------------------------------------

    def _toggle_msg_expand(self):
        if self.msg_multi.isHidden():
            # Expand. Seed the big box from the one-liner, but don't clobber a
            # longer message that's just collapsed (unchanged first line).
            line = self.msg_edit.text()
            existing = self.msg_multi.toPlainText()
            first = existing.splitlines()[0] if existing.strip() else ""
            if line.strip() and line != first:
                self.msg_multi.setPlainText(line)
            self.msg_edit.hide()
            self.msg_multi.show()
            self.msg_expand_btn.setText("▲ Less")
            self.msg_multi.setFocus()
        else:
            # Collapse: show the first line; the full text stays in the big box
            # and is still what gets committed (see _commit_message).
            text = self.msg_multi.toPlainText()
            self.msg_edit.setText(text.splitlines()[0] if text.strip() else "")
            self.msg_multi.hide()
            self.msg_edit.show()
            self.msg_expand_btn.setText("▼ More")

    @staticmethod
    def _today_prefix() -> str:
        return datetime.now().strftime("%d%m%Y")

    def _commit_message(self) -> str:
        # The big box wins while it has a multi-line message whose first line
        # still matches the collapsed one-liner (user just collapsed the view).
        multi = self.msg_multi.toPlainText().strip()
        line = self.msg_edit.text().strip()
        if not self.msg_multi.isHidden():
            msg = multi
        elif multi and "\n" in multi and multi.splitlines()[0].strip() == line:
            msg = multi
        else:
            msg = line
        # A bare date counts as a real message - it is what the box is
        # prefilled with, and committing under it beats silently skipping the
        # commit. Only a truly empty box means "just push what is already
        # committed".
        return msg

    def _on_switch(self):
        target = self._selected_branch()
        if not target or target == current_branch(self.repo):
            return
        stash = False
        if status_info(self.repo)["changes"]:
            ans = QMessageBox.question(
                self, "Uncommitted changes",
                "You have uncommitted changes on this branch.\n\n"
                "Yes = stash them and switch (restore later with 'Restore stash')\n"
                "No  = cancel and let me commit first",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
            stash = True
        self._start_job(lambda j: j.checkout(target, stash))

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "New branch", "Name of the new branch:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if " " in name or name.startswith("-"):
            QMessageBox.critical(self, "Invalid name",
                                 "Branch names can't contain spaces or start with '-'.")
            return
        self._start_job(lambda j: j.new_branch(name))

    def _pick_branch_to_delete(self, title) -> str:
        """Let the user pick a deletable branch (not current, not protected)."""
        cur = current_branch(self.repo)
        deletable = [b for b in list_branches(self.repo)
                     if b != cur and b not in PROTECTED]
        if not deletable:
            QMessageBox.information(
                self, title,
                "There is no branch that can be deleted.\n\n(The branch you are "
                f"standing on and the shared '{TARGET}' branch are protected.)")
            return ""
        name, ok = QInputDialog.getItem(
            self, title, "Which branch do you want to delete?",
            deletable, 0, False)
        return name if ok else ""

    def _on_delete(self):
        target = self._pick_branch_to_delete("Delete branch")
        if not target:
            return
        if QMessageBox.question(
                self, "Delete branch",
                f"Delete local branch '{target}'?\n\nUnmerged work will be kept "
                "(git refuses to delete it); this only removes a fully merged branch.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._start_job(lambda j: j.delete_branch(target))

    # -- everyday action handlers -----------------------------------------

    def _on_fetch(self):
        self._start_job(lambda j: j.fetch())

    def _on_pull(self):
        self._start_job(lambda j: j.pull())

    def _on_commit_push(self):
        msg = self._commit_message()
        if status_info(self.repo)["changes"] and not self._ensure_identity():
            return
        if current_branch(self.repo) in PROTECTED and QMessageBox.question(
                self, "Shared branch",
                "You are on the shared 'main' branch. Personal work usually goes "
                "on your own branch.\n\nCommit + Push here anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._start_job(lambda j: j.commit_push(msg))

    def _on_sync(self):
        msg = self._commit_message()
        if status_info(self.repo)["changes"] and not self._ensure_identity():
            return
        self._start_job(lambda j: j.sync(msg))

    def _on_merge(self):
        if current_branch(self.repo) == TARGET:
            QMessageBox.warning(self, "On main",
                                f"Switch to your working branch before merging into '{TARGET}'.")
            return
        msg = self._commit_message()
        if status_info(self.repo)["changes"] and not self._ensure_identity():
            return
        if QMessageBox.question(
                self, "Confirm merge",
                f"This will push your branch and merge it into '{TARGET}'.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._start_job(lambda j: j.merge_to_main(msg))

    def _on_stash_pop(self):
        if QMessageBox.question(
                self, "Restore stash",
                "Restore your most recently stashed changes onto this branch?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._start_job(lambda j: j.stash_pop())

    # -- dangerous handlers ------------------------------------------------

    def _typed_confirm(self, title, message, word) -> bool:
        text, ok = QInputDialog.getText(
            self, title, message + f"\n\nType {word} to confirm:")
        return ok and text.strip() == word

    def _on_force_push(self):
        cur = current_branch(self.repo)
        if cur in PROTECTED and QMessageBox.warning(
                self, "Force push to SHARED branch",
                f"'{cur}' is the shared branch. Force-pushing it can erase other "
                "people's work.\n\nAre you absolutely sure?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if QMessageBox.warning(
                self, "Force push",
                f"Overwrite the server copy of '{cur}' with your local version?\n\n"
                "Anything on the server that you don't have will be lost.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._start_job(lambda j: j.force_push())

    def _on_hard_reset(self):
        cur = current_branch(self.repo)
        if not self._typed_confirm(
                "Hard reset to server",
                f"This throws away ALL your local commits and changes on '{cur}' "
                "and makes it identical to the server. This cannot be undone here.",
                "RESET"):
            return
        self._start_job(lambda j: j.hard_reset_to_server())

    def _on_discard_all(self):
        if not self._typed_confirm(
                "Discard ALL changes",
                "This permanently deletes every uncommitted change AND every "
                "untracked file (including data not saved anywhere else).",
                "DISCARD"):
            return
        self._start_job(lambda j: j.discard_all())

    def _on_force_delete(self):
        target = self._pick_branch_to_delete("Force delete branch")
        if not target:
            return
        if not self._typed_confirm(
                "Force delete branch",
                f"Delete branch '{target}' even if it has commits that were never "
                "merged. Those commits will be lost.",
                "DELETE"):
            return
        self._start_job(lambda j: j.force_delete_branch(target))

    # -- job runner --------------------------------------------------------

    def _start_job(self, fn):
        if not self.repo or self.busy:
            return
        self.busy = True
        self._set_ui_enabled(False)
        self._append("=" * 64, "info")
        worker = GitWorker(self.repo, TARGET, fn)
        worker.signals.log.connect(self._append)
        worker.signals.done.connect(self._on_job_done)
        self._worker = worker
        self.pool.start(worker)

    def _reset_commit_message(self):
        """Fresh date prefill after a successful action (also rolls the date
        over midnight). A failed job keeps the message for the retry."""
        self.msg_multi.clear()
        self.msg_edit.setText(self._today_prefix() + " ")
        if not self.msg_multi.isHidden():
            self.msg_multi.setPlainText(self._today_prefix() + " ")

    def _on_job_done(self, ok):
        self._append("--- done ---" if ok else "--- stopped ---", "ok" if ok else "err")
        if ok:
            self._reset_commit_message()
        self.busy = False
        self._set_ui_enabled(True)
        self._worker = None
        self._refresh()

    # -- log ---------------------------------------------------------------

    def _append(self, text, tag="out"):
        color = TAG_COLOR.get(tag, C_TEXT)
        weight = "font-weight:bold;" if tag in ("ok", "err") else ""
        safe = html.escape(text).replace("\n", "<br>")
        self.log.append(f'<span style="color:{color};{weight}">{safe}</span>')
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())


def _set_app_id():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ELI.GitWork")
        except Exception:
            pass


# Note: a WM_SETICON / SetClassLongPtr "force taskbar icon" helper used to live
# here. Measured on Win11: with the window icon and the window-class icon set to
# two deliberately different images, the taskbar draws the *window* icon, so for
# Qt apps setWindowIcon is already sufficient and forcing the class icon does
# nothing. (The Tk apps still need their set_app_icon helper, because there
# iconbitmap leaves the small slots on Tk's default feather.)


def main():
    _set_app_id()
    app = QApplication(sys.argv)
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    w = App()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
