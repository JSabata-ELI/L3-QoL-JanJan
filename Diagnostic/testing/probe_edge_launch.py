"""Watch ONLY the first seconds of the Edge sign-in window: does it survive?

WHY
    "Sign in with Edge" can fail with two very different sentences, and one of
    them - "the sign-in was not completed", with no "in time" and no page named
    - means something narrow: the process that was launched had already ENDED
    the first time it was looked at, before any portal cookie existed. From the
    outside that is indistinguishable from "the button did nothing", because it
    comes back within a second or two.

    The usual suspect is the hand-off: an Edge already running on the same
    profile folder takes the address and the newly started one exits at once.
    This prints, every half second, whether the launched process is still alive,
    whether the debug port answers and where the tab is - so the hand-off names
    itself instead of being guessed at.

    python probe_edge_launch.py [seconds]

It closes the window itself. Nothing here prints a cookie value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import edge_cdp  # noqa: E402


def port_answers(port: int) -> str:
    """The browser's own answer on the debug port, or why there is none."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0) as r:
            info = json.loads(r.read().decode("utf-8", "replace"))
        return str(info.get("Browser") or "answered")
    except Exception as exc:  # noqa: BLE001 - not up yet is the normal case
        return f"no ({type(exc).__name__})"


def tab_url(port: int) -> str:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json",
                                    timeout=1.0) as r:
            tabs = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return ""
    for tab in tabs:
        if tab.get("type") == "page":
            return str(tab.get("url") or "")[:90]
    return ""


def main(argv: list[str]) -> int:
    seconds = float(argv[1]) if len(argv) > 1 else 15.0
    exe, err = edge_cdp.find_edge()
    print(f"Edge    : {exe or 'NOT FOUND: ' + err}")
    if not exe:
        return 1
    folder = edge_cdp.profile_dir()
    port = edge_cdp._free_port()
    print(f"profile : {folder}")
    print(f"port    : {port}")
    print(f"page    : {edge_cdp.MENU_PAGE}")
    print("args    : " + " ".join(
        edge_cdp.launch_args(exe, folder, port, edge_cdp.MENU_PAGE)[1:-1]))

    already = running_on(folder)
    print(f"already on this profile before launch: {already or 'none'}")

    t0 = time.monotonic()
    proc, err = edge_cdp.launch(edge_cdp.MENU_PAGE, port)
    if proc is None:
        print(f"could not start Edge: {err}")
        return 1
    print(f"launched pid {proc.pid}")

    try:
        while time.monotonic() - t0 < seconds:
            alive = "alive" if proc.poll() is None else f"ENDED rc={proc.poll()}"
            print(f"[{time.monotonic() - t0:5.1f}s] pid {proc.pid}: {alive:<14} "
                  f"port: {port_answers(port):<18} tab: {tab_url(port)}")
            time.sleep(0.5)
    finally:
        print(f"still on this profile: {running_on(folder) or 'none'}")
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    return 0


def running_on(folder) -> str:
    """The pids of any msedge already using this profile folder."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{folder.name}*' }} | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception as exc:  # noqa: BLE001
        return f"(could not ask: {exc})"
    return " ".join(out.split())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
