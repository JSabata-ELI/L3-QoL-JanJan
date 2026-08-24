"""
Standalone Webex listener that launches the Diagnostic app (main.py) on
command.

Meant to run continuously in the background — separate from main.py so a
command can be answered even while the app itself is closed (main.py's own
Webex listener, in monitor_tab.py, only runs once the app is already open).
Reuses the bot token / room / allowlist configured in monitor_config.json
(PV Monitor -> Settings -> Notifications -> Webex) so there is only one bot
identity to manage.

Deliberately Qt-free and importing only alerting.WebexNotifier (no PySide6,
no matplotlib) so it stays a lightweight, always-on process.

Command (send in the room marked "Listen for commands"): /run
("/rundiagnostic" is still accepted — it is what this listener used to answer to.)

In a room with other people in it the command must TAG THE BOT first —
"@Diagnostics /run". Webex shows a bot only the messages that mention
it (the Messages API answers 403 for the rest), so an untagged command never
arrives here at all, which looks exactly like this listener not running. The
leading mention is stripped below before the command is matched.

What /run does, in order:
  1. Picks the NEWEST BUILT version under C:\\Dev\\dist\\Diagnostic (v1.0.7,
     v1.0.8, …) and starts its .exe. That build carries its own libraries in
     `_internal`, so nothing has to be installed for it to run. Only when there
     is no build at all does it fall back to the source main.py next to this
     file, run with pythonw.
  2. Asks for monitoring to arm itself on that launch (DIAGNOSTIC_START_MONITORING),
     so "/run" gives a program that is actually tracking, without any setting
     having to be turned on for good.
  3. WAITS until the app reports it is tracking and only then replies "done".
     The waiting happens on its own thread, so the bot keeps answering while a
     cold start takes its minute.

To run at Windows logon:
    pythonw.exe "<this file's full path>" --install-startup
drops a shortcut in the current user's Startup folder (no admin rights needed);
--uninstall-startup removes it again. Nothing is installed unless asked.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import memstats
import notify_provision
from alerting import WebexNotifier, read_run_status
from cpva_api import get_app_dir

# Where this listener's own files live. get_app_dir is the app's one rule for
# it (the exe's folder when frozen, this file's folder otherwise) and is used
# rather than a second copy of the same three lines. It matters here: built as
# a single-file exe, __file__ points into the temporary folder Windows unpacks
# the bundle into, so monitor_config.json would be looked for in a directory
# that is empty and thrown away — the listener would report "not configured"
# on a PC where it is configured perfectly well.
APP_DIR = get_app_dir()
CONFIG_FILE = APP_DIR / "monitor_config.json"
LOCK_FILE = APP_DIR / "diagnostic.lock"
MAIN_SCRIPT = APP_DIR / "main.py"

# Where the built versions live. Each vX.Y.Z folder is a complete, runnable copy
# (exe + its `_internal` libraries), so starting one needs nothing installed on
# the PC — which is the point of preferring a build over the source.
BUILD_ROOT = Path(r"C:\Dev\dist\Diagnostic")
VERSION_RE = re.compile(r"^v(\d+)(?:\.(\d+))*$")
# How the builder names the copy of the entry script it puts in a version
# folder: "Diagnostic v1.0.7.py". Nothing else there is named that way.
ENTRY_COPY_RE = re.compile(r" v\d+(?:\.\d+)*\.py$", re.IGNORECASE)

COMMANDS = ("/run", "/rundiagnostic")
POLL_FETCH_COUNT = 20

# How long to wait for a launched app to report that it is tracking. A cold
# start pays for importing PySide6, matplotlib and pandas off a cold file cache
# and can take the better part of a minute; the app answers as soon as it is up,
# so this ceiling only decides when to stop hoping.
READY_TIMEOUT_S = 240
READY_POLL_S = 2

# This listener is the process that really stays up for weeks, so its console
# gets a memory line once an hour: its own committed memory (does it grow?) and
# how full the PC's commit limit is (RAM + page file promised to everything —
# the limit that, once reached, stops any program from starting).
MEM_LOG_INTERVAL_S = 3600.0


def _is_command(raw: str | None) -> bool:
    """True when this message asks for the app to start.

    The mention has to come off first. In a group space Webex only delivers
    messages that @mention the bot, and it puts that mention INTO the text — the
    room shows "@Diagnostics /run" and the API hands over "Diagnostics /run".
    Comparing the whole string to the command therefore never matched in the one
    kind of room where the tag is compulsory, and the command looked ignored.
    monitor_tab does the same strip for its own commands; this one had been left
    behind.

    Only the first word has to be the command, so a trailing "please" or a full
    stop does not lose it.
    """
    text = (raw or "").strip()
    if "/" in text and not text.startswith("/"):
        text = text[text.index("/"):]
    parts = text.split()
    return bool(parts) and parts[0].lower().rstrip(".,!:;") in COMMANDS


def _pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def is_app_running() -> bool:
    """Is a copy of the app up right now?

    The shared status file is asked FIRST because it is the only answer that
    holds whichever way the app was started — from the source folder here or
    from a build under C:\\Dev\\dist. The lock file beside this script is only
    a fallback for a copy started by an older version that did not write the
    status file yet.
    """
    pid = read_run_status().get("pid")
    if isinstance(pid, int) and _pid_alive(pid):
        return True
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    if _pid_alive(pid):
        return True
    LOCK_FILE.unlink(missing_ok=True)   # stale lock from a crashed run
    return False


def is_tracking() -> bool:
    """Is the running app actually monitoring (not just open)?"""
    st = read_run_status()
    pid = st.get("pid")
    return bool(st.get("monitoring")) and isinstance(pid, int) and _pid_alive(pid)


def _version_key(name: str) -> "tuple[int, ...] | None":
    """(1, 0, 7) for "v1.0.7", None for a folder that is not a version.
    Compared as numbers, so v1.0.10 correctly beats v1.0.9 — sorting the names
    as text would not."""
    if not VERSION_RE.match(name):
        return None
    try:
        return tuple(int(p) for p in name[1:].split("."))
    except ValueError:
        return None


def newest_build() -> "Path | None":
    """The .exe of the highest version folder under BUILD_ROOT, or None.

    A version folder only counts when it really holds an exe — a half-finished
    or emptied build directory must not shadow the working version below it.
    """
    if not BUILD_ROOT.is_dir():
        return None
    candidates = []
    try:
        for d in BUILD_ROOT.iterdir():
            if not d.is_dir():
                continue
            key = _version_key(d.name)
            if key is None:
                continue
            exe = next((e for e in sorted(d.glob("*.exe"))), None)
            if exe is not None:
                candidates.append((key, exe))
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


def _source_script() -> "Path | None":
    """The app's own .py beside this listener, when there is one.

    Two spellings, because this listener ships in two places: `main.py` in the
    source folder, and `Diagnostic v1.0.7.py` in a built version folder, where
    the builder renames the entry script after the version. Without the second
    spelling the exe would have no fallback at all on a PC that has no
    C:\\Dev\\dist — which is every PC but this one.
    """
    if MAIN_SCRIPT.exists():
        return MAIN_SCRIPT
    # In a build folder every module of the app sits beside the entry copy, so
    # "some .py that is not me" would just as happily pick shared_pvs.py. Only
    # the entry copy carries the version in its name — that is the whole match.
    cands = sorted(q for q in APP_DIR.glob("*.py") if ENTRY_COPY_RE.search(q.name))
    return cands[-1] if cands else None


def _python_runner() -> "Path | None":
    """An interpreter that can run the source, or None when there is none.

    Frozen, `sys.executable` is THIS listener — starting the source with it
    would relaunch the listener with a file name as an argument instead of
    opening the app. So when frozen the answer is a real python from PATH, or
    nothing at all.
    """
    if not getattr(sys, "frozen", False):
        py = Path(sys.executable)
        pyw = py.with_name("pythonw.exe")   # no console window flashing up
        return pyw if pyw.exists() else py
    from shutil import which
    found = which("pythonw") or which("python")
    return Path(found) if found else None


def launch_diagnostic() -> "tuple[subprocess.Popen | None, str]":
    """Start the app. Returns (process, what was started) for the reply text.

    The environment variable asks that launch — and only that launch — to arm
    monitoring, so "/run" delivers a program that is tracking without the
    Settings checkbox having to be left on for every manual start too.
    """
    env = dict(os.environ)
    env["DIAGNOSTIC_START_MONITORING"] = "1"

    exe = newest_build()
    if exe is not None:
        try:
            p = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
            return p, f"{exe.parent.name} ({exe.name})"
        except OSError as e:
            print(f"[remote_launcher] cannot start {exe}: {e}")

    # No build (or it would not start) — run the source next to this listener.
    script = _source_script()
    runner = _python_runner()
    if script is None:
        why = f"no built version under {BUILD_ROOT} and no source script beside me"
    elif runner is None:
        why = f"no built version under {BUILD_ROOT} and no Python to run the source with"
    else:
        why = ""
    if why:
        print(f"[remote_launcher] {why}")
        return None, why
    try:
        p = subprocess.Popen([str(runner), str(script)],
                             cwd=str(APP_DIR), env=env)
        return p, f"source {script.name}"
    except OSError as e:
        print(f"[remote_launcher] cannot start {script}: {e}")
        return None, str(e)


def _wait_and_report(webex: WebexNotifier, proc: "subprocess.Popen", what: str) -> None:
    """Wait for the launched app to say it is tracking, then post the result.

    Runs on its own thread: a cold start can take a minute and the poll loop
    must keep answering meanwhile — a bot that goes deaf while it works looks
    exactly like a bot that has died.

    Four distinct endings, because each needs a different reaction — and the
    process handle is what tells the last two apart. A build made BEFORE the
    run-status file existed starts perfectly well and simply never reports, and
    without the handle that is indistinguishable from a program that died on
    startup; the operator would go looking for a crash that never happened.
    """
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if is_tracking():
            webex.post_text(f"✅ Done — Diagnostic is running and tracking. [{what}]")
            return
        if proc.poll() is not None and not is_app_running():
            webex.post_text(
                "❌ Diagnostic quit right after starting "
                f"(exit code {proc.returncode}). [{what}]")
            return
        time.sleep(READY_POLL_S)

    if is_app_running():
        webex.post_text(
            "⚠️ Diagnostic is running, but tracking did not arm itself — "
            "send `@Diagnostics /start` to arm it (or use the Start "
            f"monitoring button). [{what}]")
    elif proc.poll() is None:
        webex.post_text(
            "⚠️ Diagnostic is running but does not report its state — this "
            "build is older than that feature. Rebuild Diagnostic. "
            f"[{what}]")
    else:
        webex.post_text(
            f"❌ Diagnostic did not come up within {READY_TIMEOUT_S} s. [{what}]")


def load_webex_settings() -> dict:
    settings = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f).get("settings") or {}
        except Exception:
            settings = {}
    # Same precedence as the app: channels baked into the build win, so this
    # watcher works on a PC that has never opened Settings.
    settings.update(notify_provision.load())
    return settings


def build_webex(settings: dict) -> WebexNotifier:
    rooms = settings.get("webex_rooms", [])
    room_ids = [r["room_id"] for r in rooms if r.get("enabled") and r.get("room_id")]
    listen_room_id = next(
        (r["room_id"] for r in rooms if r.get("listen") and r.get("room_id")), "")
    return WebexNotifier(
        mode=settings.get("webex_mode", "webhook"),
        bot_token=settings.get("webex_bot_token", ""),
        room_ids=room_ids, listen_room_id=listen_room_id,
        timeout=float(settings.get("http_timeout_s", 10.0)))


def main() -> None:
    settings = load_webex_settings()
    webex = build_webex(settings)
    if not webex.can_listen():
        print("[remote_launcher] Webex bot not configured for listening "
              "(need bot mode + a room with 'Listen for commands' checked "
              "in PV Monitor Settings). Exiting.")
        return

    allow = [e.lower() for e in settings.get("webex_command_allowlist", [])]
    poll_s = max(1, int(settings.get("webex_command_poll_s", 5)))

    bot_id = webex.get_me_id()
    last_id = None
    primed = False
    print(f"[remote_launcher] listening in room {webex.listen_room_id} "
          f"every {poll_s}s — send '{COMMANDS[0]}' to launch Diagnostic.")

    mem_start = memstats.read()
    started = time.monotonic()
    next_mem = started
    print(f"[remote_launcher] {memstats.long_line(mem_start)}")

    while True:
        now = time.monotonic()
        if now >= next_mem:
            next_mem = now + MEM_LOG_INTERVAL_S
            # First pass falls due immediately; skip it, the baseline was just
            # printed above.
            if now > started:
                snap = memstats.read()
                since = mem_start.proc_commit if mem_start else None
                up = int((now - started) / 3600)
                print(f"[remote_launcher] {memstats.long_line(snap, since)} "
                      f"Running for {up} h.")
        try:
            if not bot_id:
                bot_id = webex.get_me_id()

            items = webex.fetch_messages(POLL_FETCH_COUNT)   # newest first
            if webex.retry_after_s:
                wait = webex.retry_after_s
                webex.retry_after_s = 0.0
                time.sleep(wait)
                continue

            newest_id = items[0]["id"] if items else None

            if not primed:
                # Baseline only on a successful poll, so a transient failure
                # doesn't leave last_id unset and cause the whole backlog to
                # be treated (and re-answered) as new on the next poll.
                if not webex.last_error:
                    last_id = newest_id
                    primed = True
                time.sleep(poll_s)
                continue

            new_items = []
            for it in items:
                if it.get("id") == last_id:
                    break
                new_items.append(it)
            new_items.reverse()   # oldest-first
            if newest_id:
                last_id = newest_id

            for it in new_items:
                if bot_id and it.get("personId") == bot_id:
                    continue
                if webex.is_own_message(it.get("id")):
                    continue
                if not _is_command(it.get("text")):
                    continue
                email = (it.get("personEmail") or "").lower()
                if allow and email not in allow:
                    webex.post_text(f"⛔ Sorry, {email} is not allowed to command me.")
                    continue
                if is_app_running():
                    if is_tracking():
                        webex.post_text("ℹ️ Diagnostic is already running and tracking.")
                    else:
                        webex.post_text(
                            "ℹ️ Diagnostic is already running, but not "
                            "tracking — send `@Diagnostics /start` to "
                            "arm it (or use the Start monitoring button).")
                    continue
                proc, what = launch_diagnostic()
                if proc is None:
                    webex.post_text(f"❌ Could not start Diagnostic: {what}")
                    continue
                webex.post_text(f"▶️ Starting Diagnostic — {what}. "
                                "I will report back when it is up.")
                # Off the poll loop: the "done" message must wait for a cold
                # start, and this loop must not.
                threading.Thread(target=_wait_and_report,
                                 args=(webex, proc, what), daemon=True).start()
        except Exception as e:  # noqa: BLE001 - must keep listening no matter what
            print(f"[remote_launcher] error: {e}")

        time.sleep(poll_s)


def _startup_shortcut() -> Path:
    """The current user's Startup folder entry. Per-user, so putting a file
    there needs no administrator rights — which is the whole reason this is a
    shortcut in Startup and not a Task Scheduler job or a service."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "Diagnostic Webex listener.lnk")


def install_startup() -> None:
    lnk = _startup_shortcut()
    if getattr(sys, "frozen", False):
        # Built as an exe: it IS the thing to start, and it takes no argument.
        target, arg = Path(sys.executable), ""
    else:
        py = Path(sys.executable)
        pyw = py.with_name("pythonw.exe")   # pythonw: no console window at logon
        target = pyw if pyw.exists() else py
        arg = '"%s"' % Path(__file__).resolve()
    # Built with the Windows shell itself through PowerShell, so no extra
    # package has to be installed for this to work. WindowStyle 7 = minimised,
    # so the listener starts out of the way instead of covering the desktop.
    ps = (
        "$s=(New-Object -COM WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='%s';"
        "$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Save()"
    ) % (lnk, target, arg, APP_DIR)
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        print(f"[remote_launcher] autostart installed: {lnk}")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"[remote_launcher] could not install autostart: {e}")


def uninstall_startup() -> None:
    lnk = _startup_shortcut()
    try:
        lnk.unlink(missing_ok=True)
        print(f"[remote_launcher] autostart removed: {lnk}")
    except OSError as e:
        print(f"[remote_launcher] could not remove autostart: {e}")


if __name__ == "__main__":
    if "--install-startup" in sys.argv:
        install_startup()
    elif "--uninstall-startup" in sys.argv:
        uninstall_startup()
    else:
        main()
