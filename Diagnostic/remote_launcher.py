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

Command (send in the room marked "Listen for commands"): /rundiagnostic

To run at Windows logon: put a shortcut to
    pythonw.exe "<this file's full path>"
in shell:startup, or add an "At log on" Task Scheduler trigger. Not done
automatically by this script.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

import notify_provision
from alerting import WebexNotifier

APP_DIR = Path(__file__).parent
CONFIG_FILE = APP_DIR / "monitor_config.json"
LOCK_FILE = APP_DIR / "diagnostic.lock"
MAIN_SCRIPT = APP_DIR / "main.py"

COMMAND = "/rundiagnostic"
POLL_FETCH_COUNT = 20


def _pid_alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def is_app_running() -> bool:
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


def launch_diagnostic() -> None:
    subprocess.Popen([sys.executable, str(MAIN_SCRIPT)], cwd=str(APP_DIR))


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
          f"every {poll_s}s — send '{COMMAND}' to launch Diagnostika.")

    while True:
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
                text = (it.get("text") or "").strip().lower()
                if text != COMMAND:
                    continue
                email = (it.get("personEmail") or "").lower()
                if allow and email not in allow:
                    webex.post_text(f"⛔ Sorry, {email} is not allowed to command me.")
                    continue
                if is_app_running():
                    webex.post_text("ℹ️ Diagnostika už běží.")
                else:
                    launch_diagnostic()
                    webex.post_text("▶️ Diagnostika se spouští…")
        except Exception as e:  # noqa: BLE001 - must keep listening no matter what
            print(f"[remote_launcher] error: {e}")

        time.sleep(poll_s)


if __name__ == "__main__":
    main()
